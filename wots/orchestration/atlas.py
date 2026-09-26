"""Atlas, the office manager (spec v2 §7). Deterministic Python driven by workflow YAML: no LLM,
no pipeline logic in code, so every decision is testable.

Each tick visits every active office in turn (starting one further along each tick, with a
per-office job cap, so a busy office can't starve the others). For each office:
  1. budget/credits guard: past its daily LLM budget, its LLM employees pause and the CEO is told once
  2. fix loop: items whose fix_count reached the workflow's max are escalated
  3. skip: states owned by a type the office hasn't hired, marked skip_if_missing, are skipped
  4. assign: items in `assign` states go to an employee of that type with WIP capacity
  5. dispatch: items in employee-owned states are claimed (lease) and queued as jobs; failed jobs
     back off exponentially and escalate after the retry limit
  6. timers
"""
from __future__ import annotations

import logging
import threading
import traceback
from collections import Counter
from dataclasses import dataclass, field
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ..core.board import Board, InvalidTransition
from ..core.catalogue import Catalogue
from ..core.clock import Clock, utcnow
from ..core.config import Config
from ..core.context import OrgContext
from ..core.files import FileStore
from ..core.jobs import InlineQueue, JobQueue
from ..core.metering import BudgetExceeded, LLMConfigError, LLMError, Meter, start_of_local_day
from ..core.models import Artifact, Employee, Event, Job, Organization, OrgWorkflow, QAReport
from ..core.repo import scoped
from ..employees.base import EmployeeContext, EmployeeInfo, EmployeeResult, NotYet
from ..integrations.errors import IntegrationConfigError
from . import assignment
from .timers import run_timers

log = logging.getLogger(__name__)
PAUSE_NOTE = "Budget guard"
LLM_DOWN_NOTE = "LLM unavailable"
INTEGRATION_DOWN_NOTE = "Integration unavailable"


@dataclass
class TickReport:
    assigned: Counter = field(default_factory=Counter)  # employee name -> items
    ran: Counter = field(default_factory=Counter)  # employee name -> jobs completed
    failures: int = 0
    escalated: int = 0
    skipped: int = 0
    paused: dict[str, str] = field(default_factory=dict)  # org slug -> reason
    llm_unavailable: dict[str, str] = field(default_factory=dict)
    integrations_unavailable: dict[str, str] = field(default_factory=dict)
    offices: int = 0

    def summary(self) -> str:
        ran = ", ".join(f"{k} {v}" for k, v in sorted(self.ran.items())) or "none"
        assigned = ", ".join(f"{k} {v}" for k, v in sorted(self.assigned.items())) or "none"
        text = (f"offices: {self.offices} | assigned: {assigned} | jobs: {ran} | failures: {self.failures} | "
                f"escalated: {self.escalated}" + (f" | skipped: {self.skipped}" if self.skipped else ""))
        for slug, reason in {**self.paused, **self.llm_unavailable}.items():
            text += f" | {slug} LLM PAUSED: {reason}"
        for slug, reason in self.integrations_unavailable.items():
            text += f" | {slug} WAITING FOR SETUP: {reason}"
        return text


@dataclass
class Staff:
    """An office's hired, enabled employees, with their type and validated config."""
    row: Employee
    info: EmployeeInfo
    impl: object | None  # None = not built yet (a later phase)


def system_ctx(org: Organization) -> OrgContext:
    return OrgContext(org_id=org.id, slug=org.slug, name=org.name, is_internal=org.is_internal,
                      settings=dict(org.settings or {}))


class Atlas:
    def __init__(self, sessions: sessionmaker[Session], config: Config, catalogue: Catalogue, board: Board,
                 meter: Meter, files: FileStore, notifier=None, queue: JobQueue | None = None, clock: Clock = utcnow):
        self.sessions = sessions
        self.config = config
        self.catalogue = catalogue
        self.board = board
        self.meter = meter
        self.files = files
        self.notifier = notifier
        self.queue = queue or InlineQueue()
        self.clock = clock
        self.impl_overrides: dict[str, object] = {}  # employee id -> implementation (tests, trials)
        self.integrations = None  # wots.integrations.toolbox.Integrations, set by the runtime
        self.timer_state: dict = {}  # e.g. when each office's mailbox was last read
        self._tick_lock = threading.Lock()
        self._report_lock = threading.Lock()
        self._rotation = 0

    @property
    def settings(self):
        return self.config.settings

    # ------------------------------------------------------------------ tick

    def tick(self, org_slug: str | None = None) -> TickReport | None:
        """One pass over every active office (or just one). None if a tick is already running."""
        if not self._tick_lock.acquire(blocking=False):
            return None
        try:
            report = TickReport()
            with self.sessions() as s:
                query = select(Organization).where(Organization.status == "active").order_by(Organization.created_at)
                if org_slug:
                    query = query.where(Organization.slug == org_slug)
                orgs = list(s.scalars(query))
            if orgs:
                start = self._rotation % len(orgs)
                self._rotation += 1
                for org in orgs[start:] + orgs[:start]:
                    report.offices += 1
                    self._tick_office(system_ctx(org), report)
            self.queue.drain()
            return report
        finally:
            self._tick_lock.release()

    def _tick_office(self, ctx: OrgContext, report: TickReport) -> None:
        staff = self.staff(ctx)
        workflows = self.active_workflows(ctx)
        paused = self._budget_guard(ctx)
        if paused:
            report.paused[ctx.slug] = paused
        budget = [self.settings.atlas.max_jobs_per_org_per_tick]
        for wf in workflows:
            self._fix_loop(ctx, wf, report)
            self._skip_missing(ctx, wf, staff, report)
            self._assign(ctx, wf, staff, report)
        for wf in workflows:
            self._dispatch(ctx, wf, staff, report, llm_paused=bool(paused), budget=budget)
        run_timers(self, ctx)

    # ------------------------------------------------------------------ office setup

    def staff(self, ctx: OrgContext) -> list[Staff]:
        with self.sessions() as s:
            rows = list(s.scalars(scoped(Employee, ctx).where(Employee.enabled.is_(True), Employee.fired_at.is_(None))
                                  .order_by(Employee.name)))
        out = []
        for row in rows:
            type_def = self.catalogue.types.get(row.type_key)
            if not type_def:
                continue
            info = EmployeeInfo(id=row.id, name=row.name, type=type_def, config=type_def.validate_config(row.config))
            impl = self.impl_overrides.get(row.id)
            if impl is None:
                cls = type_def.load_impl()
                impl = cls(info) if cls else None
            out.append(Staff(row, info, impl))
        return out

    def active_workflows(self, ctx: OrgContext):
        with self.sessions() as s:
            keys = list(s.scalars(select(OrgWorkflow.workflow_key).where(
                OrgWorkflow.org_id == ctx.org_id, OrgWorkflow.active.is_(True)).order_by(OrgWorkflow.workflow_key)))
        return [self.catalogue.workflows[k] for k in keys if k in self.catalogue.workflows]

    # ------------------------------------------------------------------ 1. budget guard

    def _budget_guard(self, ctx: OrgContext) -> str | None:
        reason = self.meter.paused_reason(ctx)
        if reason:
            self._tell_ceo_once(ctx, f"{PAUSE_NOTE}: {reason}. LLM employees are paused.")
        return reason

    def _tell_ceo_once(self, ctx: OrgContext, note: str) -> None:
        """Log and notify, at most once per local day for the same kind of problem."""
        since = start_of_local_day(self.clock(), ctx.settings.get("timezone", self.settings.timezone))
        prefix = note.split(":")[0]
        with self.sessions() as s:
            if s.scalar(select(func.count()).select_from(Event).where(
                    Event.org_id == ctx.org_id, Event.work_item_id.is_(None), Event.note.startswith(prefix),
                    Event.ts >= since)):
                return
        self.board.log(ctx, None, "system", "atlas", note)
        if self.notifier:
            self.notifier.send(ctx, f"[{ctx.name}] {note}")

    # ------------------------------------------------------------------ 2-4. fix loop, skips, assignment

    def _fix_loop(self, ctx, wf, report) -> None:
        rule = wf.fix_loop
        if not rule:
            return
        for item in self.board.items(ctx, workflow=wf.key, statuses=[rule["counter_on"]], unclaimed=True):
            self._escalate_if_exhausted(ctx, wf, item, report)

    def _escalate_if_exhausted(self, ctx, wf, item, report) -> bool:
        """An item entering the fix loop for the max-th time goes to the CEO instead of back to work."""
        rule = wf.fix_loop
        if not rule or item.status != rule["counter_on"] or item.fix_count < rule["max"]:
            return False
        feedback = self.board.latest_feedback(ctx, item.id) or "no feedback recorded"
        self.board.transition(ctx, item.id, rule["escalate_to"], "system", "atlas",
                              f"Fix loop reached {item.fix_count}. Last feedback: {feedback}")
        with self._report_lock:
            report.escalated += 1
        return True

    def _skip_missing(self, ctx, wf, staff, report) -> None:
        hired = {st.info.type.key for st in staff}
        for state in wf.states.values():
            if state.skip_if_missing and state.value not in hired:
                name = self.catalogue.types[state.value].display_name
                for item in self.board.items(ctx, workflow=wf.key, statuses=[state.name], unclaimed=True):
                    self.board.transition(ctx, item.id, state.skip_to, "system", "atlas", f"Skipped: no {name} hired")
                    report.skipped += 1

    def _assign(self, ctx, wf, staff, report) -> None:
        for state in wf.states.values():
            if state.owner != "assign":
                continue
            pool = [st for st in staff if st.info.type.key == state.value and st.impl is not None]
            ready = self.board.items(ctx, workflow=wf.key, statuses=[state.name], unclaimed=True)
            if not pool or not ready:
                continue
            next_state = wf.outgoing(state.name)[0]
            active_states = wf.wip.get(state.value, [])
            since = assignment.fairness_since(self.clock(), self.settings.wip.fairness_window_days)
            with self.sessions() as s:
                slots = {st.info.id: assignment.capacity(s, ctx, st.info.id, st.info.config, active_states) for st in pool}
                load = {st.info.id: assignment.recent_load(s, ctx, st.info.id, since, next_state) for st in pool}
            names = {st.info.id: st.info.name for st in pool}
            for item in sorted(ready, key=lambda i: (i.created_at, i.id)):
                free = [eid for eid, n in slots.items() if n > 0]
                if not free:
                    break
                chosen = min(free, key=lambda eid: (load[eid], names[eid]))
                self.board.transition(ctx, item.id, next_state, "system", "atlas", f"Assigned to {names[chosen]}",
                                      updates={"assigned_employee_id": chosen})
                slots[chosen] -= 1
                load[chosen] += 1
                report.assigned[names[chosen]] += 1

    # ------------------------------------------------------------------ 5. dispatch

    def _dispatch(self, ctx, wf, staff, report, llm_paused: bool, budget: list[int]) -> None:
        by_type: dict[str, list[Staff]] = {}
        by_id: dict[str, Staff] = {}
        for st in staff:
            if st.impl is not None:
                by_type.setdefault(st.info.type.key, []).append(st)
                by_id[st.info.id] = st
        # Finish work before starting more: the latest steps get the job budget first (so a big batch of
        # new leads can't starve approved sites), then a forward pass lets items move several steps a tick
        owned = [s for s in wf.states.values() if s.employee_owned]
        dispatched: dict[str, str] = {}  # item id -> the status it was dispatched at, this tick
        for state in [*reversed(owned), *owned]:
            items = self.board.items(ctx, workflow=wf.key, statuses=[state.name], unclaimed=True)
            for item in items:
                if dispatched.get(item.id) == item.status:
                    continue  # already worked on at this step this tick (it didn't move)
                if budget[0] <= 0 or ctx.slug in report.llm_unavailable:
                    return
                if self._escalate_if_exhausted(ctx, wf, item, report):  # reached the max during this tick
                    continue
                if state.owner == "assigned":
                    who = by_id.get(item.assigned_employee_id or "")
                else:
                    candidates = by_type.get(state.value, [])
                    who = min(candidates, key=lambda st: (self._busy(ctx, st.info.id), st.info.name)) if candidates else None
                if who is None or (llm_paused and who.info.type.uses_llm):
                    continue
                if not self.board.claim(ctx, item.id, who.info.id):
                    continue
                dispatched[item.id] = item.status
                budget[0] -= 1
                task = state.task or who.info.type.task_kinds[0]
                with self.sessions.begin() as s:
                    job = Job(org_id=ctx.org_id, work_item_id=item.id, employee_id=who.info.id, task=task,
                              status="queued", ts=self.clock())
                    s.add(job)
                    s.flush()
                    job_id = job.id
                self.queue.submit(lambda item=item, who=who, task=task, job_id=job_id, wf=wf:
                                  self._run_job(ctx, wf, who, item.id, task, job_id, report))

    def _busy(self, ctx: OrgContext, employee_id: str) -> int:
        from ..core.models import WorkItem

        with self.sessions() as s:
            return s.scalar(select(func.count()).select_from(WorkItem).where(
                WorkItem.org_id == ctx.org_id, WorkItem.claimed_by == employee_id,
                WorkItem.claim_expires_at > self.clock())) or 0

    def _run_job(self, ctx: OrgContext, wf, who: Staff, item_id: str, task: str, job_id: str, report: TickReport):
        emp = who.info
        self._job_status(ctx, job_id, "running")
        try:
            item = self.board.get(ctx, item_id)
            hop = wf.start_hop(item.status)
            if hop:  # e.g. ASSIGNED -> BUILDING, NEEDS_FIX -> BUILDING when the designer starts
                item = self.board.transition(ctx, item_id, hop, "employee", emp.id, "Started")
            model = self.config.models.resolve(emp.config.get("model") or emp.type.default_model)
            job_ctx = EmployeeContext(
                org=ctx, employee=emp, config=self.config, item_dir=self.files.item_dir(ctx.org_id, item_id),
                task=task, dry_run=self.settings.dry_run,
                llm=self.meter.for_employee(ctx, emp.id, item_id) if emp.type.uses_llm else None,
                model=model, effort=emp.config.get("effort"), feedback=self.board.latest_feedback(ctx, item_id),
                tools=self.integrations.for_office(ctx) if self.integrations else None, now=self.clock())
            result = who.impl.run(item, task, job_ctx)
            self._apply(ctx, emp, item, result)
            self._job_status(ctx, job_id, "done")
            with self._report_lock:
                report.ran[emp.name] += 1
            self.board.release(ctx, item_id, emp.id)
        except NotYet as e:
            # Scheduled, not failed: e.g. waiting for the recipient's send window or tomorrow's send cap
            self._job_status(ctx, job_id, "scheduled", e.note)
            self.board.release(ctx, item_id, emp.id)
            seconds = max(60.0, (e.until - self.clock()).total_seconds())
            self.board.hold(ctx, item_id, f"{emp.id}:scheduled", seconds)
            if e.announce:
                self.board.log(ctx, item_id, "employee", emp.id, e.note)
        except BudgetExceeded as e:
            self._job_status(ctx, job_id, "paused", str(e))
            self.board.release(ctx, item_id, emp.id)  # not the item's fault: try again later
        except LLMConfigError as e:
            # A bad key / no credit balance breaks every call: don't burn each item's retries on it
            self._job_status(ctx, job_id, "paused", str(e))
            self.board.release(ctx, item_id, emp.id)
            with self._report_lock:
                report.llm_unavailable[ctx.slug] = str(e)
            self._tell_ceo_once(ctx, f"{LLM_DOWN_NOTE}: {e}. LLM employees are paused until it's fixed.")
        except IntegrationConfigError as e:
            # A missing or rejected key: every such item would fail the same way, so wait for the fix
            self._job_status(ctx, job_id, "paused", str(e))
            self.board.release(ctx, item_id, emp.id)
            with self._report_lock:
                report.integrations_unavailable[ctx.slug] = str(e)
            # One message per day for each kind of problem (the prefix before the first colon)
            self._tell_ceo_once(ctx, f"{INTEGRATION_DOWN_NOTE} ({str(e).split(':')[0]}): {e}. {emp.name} is waiting.")
        except Exception as e:  # noqa: BLE001 - any employee failure is retried, then escalated
            self._job_status(ctx, job_id, "failed", f"{type(e).__name__}: {e}")
            with self._report_lock:
                report.failures += 1
            self._handle_failure(ctx, wf, emp, item_id, e, report)

    def _apply(self, ctx: OrgContext, emp: EmployeeInfo, item: SimpleNamespace, result: EmployeeResult) -> None:
        if result.artifacts or result.qa_report is not None:
            with self.sessions.begin() as s:
                for art in result.artifacts:
                    version = (s.scalar(select(func.max(Artifact.version)).where(
                        Artifact.org_id == ctx.org_id, Artifact.work_item_id == item.id,
                        Artifact.kind == art.kind)) or 0) + 1
                    s.add(Artifact(org_id=ctx.org_id, work_item_id=item.id, kind=art.kind, path=art.path,
                                   version=version, created_by_employee_id=emp.id))
                if result.qa_report is not None:
                    site_version = s.scalar(select(func.max(Artifact.version)).where(
                        Artifact.org_id == ctx.org_id, Artifact.work_item_id == item.id, Artifact.kind == "site")) or 0
                    s.add(QAReport(org_id=ctx.org_id, work_item_id=item.id, artifact_version=site_version,
                                   passed=bool(result.qa_report["passed"]), issues=result.qa_report.get("issues", []),
                                   created_at=self.clock()))
        if result.next_status is not None:
            self.board.transition(ctx, item.id, result.next_status, "employee", emp.id, result.note,
                                  updates=result.updates)
        elif result.note:
            self.board.log(ctx, item.id, "employee", emp.id, result.note)

    def _handle_failure(self, ctx, wf, emp: EmployeeInfo, item_id: str, error: Exception, report) -> None:
        detail = f"{type(error).__name__}: {error}"
        if isinstance(error, (InvalidTransition, LLMError)):  # expected failure modes: the message is enough
            log.warning("%s failed on %s/%s: %s", emp.name, ctx.slug, item_id, detail)
        else:
            log.warning("%s failed on %s/%s\n%s", emp.name, ctx.slug, item_id, traceback.format_exc())
        failures = self.board.record_failure(ctx, item_id, emp.id, detail)
        atlas = self.settings.atlas
        if failures > atlas.max_retries and wf.escalate_to:
            self.board.release(ctx, item_id, emp.id)
            try:
                self.board.transition(ctx, item_id, wf.escalate_to, "system", "atlas",
                                      f"{emp.name} failed {failures} times; last error: {detail}")
                with self._report_lock:
                    report.escalated += 1
            except InvalidTransition:  # e.g. the CEO moved the item meanwhile
                log.warning("Couldn't escalate %s after repeated failures", item_id)
        else:
            # Exponential backoff: hold the item so nobody picks it up until the delay passes
            self.board.hold(ctx, item_id, f"{emp.id}:retry", atlas.retry_backoff_seconds * 2 ** (failures - 1))

    def _job_status(self, ctx: OrgContext, job_id: str, status: str, error: str | None = None) -> None:
        from sqlalchemy import update

        with self.sessions.begin() as s:
            s.execute(update(Job).where(Job.org_id == ctx.org_id, Job.id == job_id).values(status=status, error=error))
