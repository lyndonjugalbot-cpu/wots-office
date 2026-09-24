"""Atlas, the orchestrator (spec §6). Plain Python, no LLM, so every decision is testable.

Each tick:
  1. Budget guard: past today's LLM cap, LLM agents are paused and the CEO is told (once a day).
  2. Fix routing: NEEDS_FIX goes back to the same designer with the latest feedback, or escalates.
  3. Assignment: ready leads go to designers who have capacity under the WIP rules.
  4. Dispatch: each enabled agent claims up to its batch of leads, runs, and Atlas applies the result.
     Failures are retried with exponential backoff, then escalated.
"""
from __future__ import annotations

import logging
import threading
import traceback
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ..agents.base import Agent, AgentContext, AgentResult
from .board import Board, InvalidTransition
from .clock import Clock, utcnow
from .config import AgentConfig, Config
from .llm import LLM, BudgetExceeded, LLMConfigError, start_of_local_day
from .models import Artifact, Event, Lead, QAReport
from .states import ACTIVE_STATUSES, READY_TO_ASSIGN, WORK_STATUS, Scope, Status

log = logging.getLogger(__name__)
ATLAS = "atlas"
BUDGET_NOTE = "Budget guard"
LLM_DOWN_NOTE = "LLM unavailable"


@dataclass
class TickReport:
    fixes_routed: int = 0
    escalated: int = 0
    assigned: dict[str, int] = field(default_factory=dict)
    ran: Counter = field(default_factory=Counter)
    failures: int = 0
    budget_paused: bool = False
    llm_unavailable: str | None = None

    def summary(self) -> str:
        ran = ", ".join(f"{k} {v}" for k, v in sorted(self.ran.items())) or "none"
        assigned = ", ".join(f"{k} {v}" for k, v in sorted(self.assigned.items())) or "none"
        return (f"assigned: {assigned} | fixes routed: {self.fixes_routed} | escalated: {self.escalated} | "
                f"agent runs: {ran} | failures: {self.failures}"
                + (" | LLM agents PAUSED (budget)" if self.budget_paused else "")
                + (f" | LLM agents PAUSED: {self.llm_unavailable}" if self.llm_unavailable else ""))


class Atlas:
    def __init__(self, sessions: sessionmaker[Session], config: Config, board: Board, agents: dict[str, Agent],
                 llm: LLM | None = None, notifier=None, clock: Clock = utcnow):
        self.sessions = sessions
        self.config = config
        self.board = board
        self.agents = agents
        self.llm = llm
        self.notifier = notifier
        self.clock = clock
        self._tick_lock = threading.Lock()

    @property
    def settings(self):
        return self.config.settings

    # ------------------------------------------------------------------ tick

    def tick(self) -> TickReport | None:
        """Run one tick. Returns None if another tick is already running (scheduler + dashboard button)."""
        if not self._tick_lock.acquire(blocking=False):
            return None
        try:
            report = TickReport()
            report.budget_paused = self._budget_guard()
            self._route_fixes(report)
            self._assign(report)
            self._dispatch(report, llm_paused=report.budget_paused)
            return report
        finally:
            self._tick_lock.release()

    # ------------------------------------------------------------------ 1. budget guard

    def _budget_guard(self) -> bool:
        if not self.llm or not self.llm.over_budget():
            return False
        since = start_of_local_day(self.clock(), self.settings.timezone)
        with self.sessions() as s:
            already_told = s.scalar(select(func.count()).select_from(Event).where(
                Event.lead_id.is_(None), Event.note.startswith(BUDGET_NOTE), Event.ts >= since))
        if not already_told:
            spend = self.llm.spend_today()
            note = (f"{BUDGET_NOTE}: today's LLM spend ${spend:.2f} reached the ${self.settings.llm_cap_usd:.2f} cap"
                    f"{' (dry run)' if self.settings.dry_run else ''}. LLM agents are paused until tomorrow.")
            self.board.log(None, ATLAS, note)
            if self.notifier:
                self.notifier.send(note)
        return True

    def _tell_ceo_once(self, note: str) -> None:
        """Log and notify, at most once per local day for the same kind of problem."""
        since = start_of_local_day(self.clock(), self.settings.timezone)
        prefix = note.split(":")[0]
        with self.sessions() as s:
            if s.scalar(select(func.count()).select_from(Event).where(
                    Event.lead_id.is_(None), Event.note.startswith(prefix), Event.ts >= since)):
                return
        self.board.log(None, ATLAS, note)
        if self.notifier:
            self.notifier.send(note)

    # ------------------------------------------------------------------ 2. fix routing

    def _route_fixes(self, report: TickReport) -> None:
        now = self.clock()
        with self.sessions() as s:
            leads = list(s.scalars(select(Lead).where(Lead.status == Status.NEEDS_FIX.value, Board.unclaimed(now))
                                   .order_by(Lead.updated_at, Lead.id)))
        max_fix = self.settings.atlas.max_fix_count
        for lead in leads:
            fix_count = lead.fix_count + 1
            feedback = self.board.latest_feedback(lead.id) or "No feedback recorded"
            if fix_count >= max_fix or not lead.assigned_to:
                why = f"fix_count reached {fix_count}" if lead.assigned_to else "no designer on record"
                self.board.transition(lead.id, Status.ESCALATED, ATLAS, f"{why}. Last feedback: {feedback}",
                                      updates={"fix_count": fix_count})
                report.escalated += 1
            else:
                work = WORK_STATUS[Scope(lead.scope)]
                self.board.transition(lead.id, work, ATLAS, f"Fix #{fix_count} for {lead.assigned_to}. {feedback}",
                                      updates={"fix_count": fix_count})
                report.fixes_routed += 1

    # ------------------------------------------------------------------ 3. assignment with WIP limits

    def designer_pool(self, scope: Scope) -> list[AgentConfig]:
        return sorted((a.config for a in self.agents.values() if scope.value in a.config.pool), key=lambda c: c.name)

    def active_count(self, s: Session, designer: str) -> int:
        return s.scalar(select(func.count()).select_from(Lead).where(
            Lead.assigned_to == designer, Lead.status.in_([st.value for st in ACTIVE_STATUSES]))) or 0

    def capacity(self, s: Session, designer: str) -> int:
        wip = self.settings.wip
        active = self.active_count(s, designer)
        if wip.wip_mode == "batch":
            # New work only once every current lead is APPROVED or has left the pipeline
            return wip.max_wip if active == 0 else 0
        return max(0, wip.max_wip - active)

    def weekly_load(self, s: Session, designer: str) -> int:
        since = self.clock() - timedelta(days=self.settings.wip.fairness_window_days)
        return s.scalar(select(func.count(func.distinct(Event.lead_id))).join(Lead, Lead.id == Event.lead_id).where(
            Event.to_status == Status.ASSIGNED.value, Event.ts >= since, Lead.assigned_to == designer)) or 0

    def _assign(self, report: TickReport) -> None:
        now = self.clock()
        for scope in Scope:
            pool = self.designer_pool(scope)
            if not pool:
                continue
            with self.sessions() as s:
                ready = list(s.scalars(select(Lead).where(
                    Lead.scope == scope.value, Lead.status == READY_TO_ASSIGN[scope].value, Board.unclaimed(now))
                    .order_by(Lead.created_at, Lead.id)))
                slots = {d.name: self.capacity(s, d.name) for d in pool}
                load = {d.name: self.weekly_load(s, d.name) for d in pool}
            for lead in ready:
                free = [name for name, n in slots.items() if n > 0]
                if not free:
                    break
                # Keep the split even: the free designer with fewer leads this week gets it
                designer = min(free, key=lambda name: (load[name], name))
                self.board.transition(lead.id, Status.ASSIGNED, ATLAS, f"Assigned to {designer}",
                                      updates={"assigned_to": designer})
                slots[designer] -= 1
                load[designer] += 1
                report.assigned[designer] = report.assigned.get(designer, 0) + 1

    # ------------------------------------------------------------------ 4. dispatch

    def _candidates(self, agent: Agent, scope: str, limit: int) -> list[Lead]:
        cfg = agent.config
        now = self.clock()
        query = select(Lead).where(Lead.scope == scope, Board.unclaimed(now))
        if scope in cfg.assigned_only:
            # A designer picks up new assignments and leads sent back to it for a fix
            query = query.where(Lead.assigned_to == agent.name, Lead.status.in_(
                [cfg.owns[scope], WORK_STATUS[Scope(scope)].value]))
        else:
            query = query.where(Lead.status == cfg.owns[scope])
        with self.sessions() as s:
            return list(s.scalars(query.order_by(Lead.updated_at, Lead.id).limit(limit)))

    def _dispatch(self, report: TickReport, llm_paused: bool) -> None:
        for agent in self.agents.values():
            cfg = agent.config
            if cfg.uses_llm and (llm_paused or report.llm_unavailable):
                continue
            batch = self.config.agents.batch_size(cfg)
            for scope in cfg.scope:
                if scope not in cfg.owns:
                    continue
                for lead in self._candidates(agent, scope, batch):
                    if report.llm_unavailable and cfg.uses_llm:
                        break
                    if self.board.claim(lead.id, agent.name):
                        self._run_one(agent, lead, report)

    def _run_one(self, agent: Agent, lead: Lead, report: TickReport) -> None:
        cfg = agent.config
        try:
            if lead.scope in cfg.assigned_only and lead.status == Status.ASSIGNED.value:
                lead = self.board.transition(lead.id, WORK_STATUS[Scope(lead.scope)], agent.name, "Started")
            ctx = AgentContext(
                config=self.config,
                lead_dir=self.lead_dir(lead.id),
                dry_run=self.settings.dry_run,
                llm=self.llm,
                model=self.config.agents.model_for(cfg),
                feedback=self.board.latest_feedback(lead.id),
            )
            result = agent.run(lead, ctx)
            self._apply(agent, lead, result)
            report.ran[agent.name] += 1
            self.board.release(lead.id, agent.name)
        except BudgetExceeded:
            self.board.release(lead.id, agent.name)  # not the lead's fault: try again tomorrow
        except LLMConfigError as e:
            # A bad key or missing workspace breaks every LLM call; don't burn each lead's retries on it
            self.board.release(lead.id, agent.name)
            report.llm_unavailable = str(e)
            self._tell_ceo_once(f"{LLM_DOWN_NOTE}: {e}. LLM agents are paused until it's fixed.")
        except Exception as e:  # noqa: BLE001 - any agent failure is retried, then escalated
            report.failures += 1
            self._handle_failure(agent, lead, e, report)

    def _apply(self, agent: Agent, lead: Lead, result: AgentResult) -> None:
        if result.qa_report is not None:
            with self.sessions.begin() as s:
                version = s.scalar(select(func.max(Artifact.version)).where(
                    Artifact.lead_id == lead.id, Artifact.kind == "site")) or 0
                s.add(QAReport(lead_id=lead.id, artifact_version=version, passed=bool(result.qa_report["passed"]),
                               issues=result.qa_report.get("issues", []), created_at=self.clock()))
        if result.next_status is not None:
            self.board.transition(lead.id, result.next_status, agent.name, result.note, updates=result.updates)
        elif result.note:
            self.board.log(lead.id, agent.name, result.note)
        if result.artifacts:
            with self.sessions.begin() as s:
                for art in result.artifacts:
                    version = (s.scalar(select(func.max(Artifact.version)).where(
                        Artifact.lead_id == lead.id, Artifact.kind == art.kind)) or 0) + 1
                    s.add(Artifact(lead_id=lead.id, kind=art.kind, path=art.path, version=version,
                                   created_by=agent.name, created_at=self.clock()))

    def _handle_failure(self, agent: Agent, lead: Lead, error: Exception, report: TickReport) -> None:
        detail = f"{type(error).__name__}: {error}"
        if not isinstance(error, InvalidTransition):
            log.warning("Agent %s failed on lead %s\n%s", agent.name, lead.id, traceback.format_exc())
        failures = self.board.record_failure(lead.id, agent.name, detail)
        atlas = self.settings.atlas
        if failures > atlas.max_retries:
            self.board.release(lead.id, agent.name)
            try:
                self.board.transition(lead.id, Status.ESCALATED, ATLAS,
                                      f"{agent.name} failed {failures} times; last error: {detail}")
                report.escalated += 1
            except InvalidTransition:  # e.g. the CEO moved the lead meanwhile; leave it where it is
                log.warning("Could not escalate lead %s after repeated failures", lead.id)
        else:
            # Exponential backoff: hold the lead so nobody picks it up until the delay passes
            self.board.hold(lead.id, f"{agent.name}:retry", atlas.retry_backoff_seconds * 2 ** (failures - 1))

    def lead_dir(self, lead_id: int) -> Path:
        path = self.settings.data_path / "leads" / str(lead_id)
        path.mkdir(parents=True, exist_ok=True)
        return path
