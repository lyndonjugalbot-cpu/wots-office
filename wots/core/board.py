"""The board: work items and their status, org-scoped (spec v2 §2.1, §2.3).

Every status change goes through Board.transition(), which checks the office's workflow YAML and
writes an events row. Claims are leases, so a crashed employee's item is picked up again once its
lease runs out.
"""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from .catalogue import Catalogue
from .clock import Clock, utcnow
from .config import Config
from .context import OrgContext
from .models import Approval, Event, LeadProfile, QAReport, WorkItem
from .repo import NotFound, one_or_404, scoped

WORK_FIELDS = {"assigned_employee_id", "fix_count", "priority", "disqualify_reason"}
PROFILE_FIELDS = {c.name for c in LeadProfile.__table__.columns} - {"work_item_id", "org_id", "created_at", "updated_at"}
ERROR_PREFIX = "error:"


class InvalidTransition(Exception):
    pass


def item_view(item: WorkItem, profile: LeadProfile | None) -> SimpleNamespace:
    """A work item and its lead profile as one object (what employees and the dashboard read)."""
    data = {c.name: getattr(item, c.name) for c in WorkItem.__table__.columns}
    if profile is not None:
        data.update({k: getattr(profile, k) for k in PROFILE_FIELDS})
    return SimpleNamespace(**data)


class Board:
    def __init__(self, sessions: sessionmaker[Session], config: Config, catalogue: Catalogue, notifier=None,
                 clock: Clock = utcnow):
        self.sessions = sessions
        self.config = config
        self.catalogue = catalogue
        self.notifier = notifier
        self.clock = clock

    # ------------------------------------------------------------------ reading

    def get(self, ctx: OrgContext, item_id: str) -> SimpleNamespace:
        with self.sessions() as s:
            item = one_or_404(s, WorkItem, ctx, id=item_id)
            profile = s.scalars(scoped(LeadProfile, ctx).where(LeadProfile.work_item_id == item_id)).first()
            return item_view(item, profile)

    def items(self, ctx: OrgContext, *, workflow: str | None = None, statuses: list[str] | None = None,
              assigned_to: str | None = None, unclaimed: bool = False, limit: int | None = None,
              newest_first: bool = False) -> list[SimpleNamespace]:
        query = (select(WorkItem, LeadProfile).outerjoin(LeadProfile, LeadProfile.work_item_id == WorkItem.id)
                 .where(WorkItem.org_id == ctx.org_id))
        if workflow:
            query = query.where(WorkItem.workflow_key == workflow)
        if statuses:
            query = query.where(WorkItem.status.in_(statuses))
        if assigned_to:
            query = query.where(WorkItem.assigned_employee_id == assigned_to)
        if unclaimed:
            query = query.where(self.unclaimed(self.clock()))
        order = WorkItem.updated_at.desc() if newest_first else WorkItem.updated_at
        query = query.order_by(order, WorkItem.id)
        if limit:
            query = query.limit(limit)
        with self.sessions() as s:
            return [item_view(i, p) for i, p in s.execute(query).all()]

    @staticmethod
    def unclaimed(now):
        return or_(WorkItem.claimed_by.is_(None), WorkItem.claim_expires_at <= now)

    def workflow(self, key: str):
        return self.catalogue.workflows[key]

    # ------------------------------------------------------------------ writing

    def create_item(self, ctx: OrgContext, *, workflow_key: str, profile: dict[str, Any], actor_kind: str = "system",
                    actor_id: str | None = "import", note: str | None = None) -> str:
        wf = self.workflow(workflow_key)
        unknown = set(profile) - PROFILE_FIELDS
        if unknown:
            raise ValueError(f"Unknown lead fields: {', '.join(sorted(unknown))}")
        now = self.clock()
        with self.sessions.begin() as s:
            item = WorkItem(org_id=ctx.org_id, workflow_key=workflow_key, kind=wf.work_item_kind, status=wf.initial,
                            created_at=now, updated_at=now)
            s.add(item)
            s.flush()
            s.add(LeadProfile(work_item_id=item.id, org_id=ctx.org_id, created_at=now, updated_at=now, **profile))
            self._event(s, ctx, item.id, None, wf.initial, actor_kind, actor_id, note)
            return item.id

    def transition(self, ctx: OrgContext, item_id: str, to_status: str, actor_kind: str, actor_id: str | None,
                   note: str | None = None, updates: dict[str, Any] | None = None) -> SimpleNamespace:
        """Move an item to a new status if its workflow allows it, and log the event."""
        updates = dict(updates or {})
        bad = set(updates) - WORK_FIELDS - PROFILE_FIELDS
        if bad:
            raise ValueError(f"These fields can't be set by a transition: {', '.join(sorted(bad))}")
        with self.sessions.begin() as s:
            item = one_or_404(s, WorkItem, ctx, id=item_id)
            wf = self.workflow(item.workflow_key)
            if to_status not in wf.states:
                raise InvalidTransition(f"{wf.key} has no state {to_status}")
            from_status = item.status
            allowed = wf.is_allowed(from_status, to_status)
            if not allowed and from_status == wf.escalate_to:
                # The CEO may resume an error escalation where it stopped (not into the fix loop itself)
                came_from = self._escalated_from(s, ctx, item.id)
                target = wf.states[to_status]
                allowed = (to_status == came_from and target.employee_owned
                           and to_status != (wf.fix_loop or {}).get("counter_on"))
            if not allowed:
                raise InvalidTransition(f"{wf.key}: {from_status} -> {to_status} is not allowed")

            if wf.fix_loop and to_status == wf.fix_loop["counter_on"] and "fix_count" not in updates:
                updates["fix_count"] = item.fix_count + 1  # entering the fix loop counts a fix (spec v2 §7.4)
            if from_status == wf.escalate_to and wf.states[to_status].owner != "terminal":
                updates.setdefault("fix_count", 0)  # a CEO send-back starts a fresh fix budget
            now = self.clock()
            profile_updates = {k: v for k, v in updates.items() if k in PROFILE_FIELDS}
            for key, value in updates.items():
                if key in WORK_FIELDS:
                    setattr(item, key, value)
            item.status = to_status
            item.updated_at = now
            if profile_updates:
                profile = one_or_404(s, LeadProfile, ctx, work_item_id=item.id)
                for key, value in profile_updates.items():
                    setattr(profile, key, value)
                profile.updated_at = now
            self._event(s, ctx, item.id, from_status, to_status, actor_kind, actor_id, note)
            profile = s.scalars(scoped(LeadProfile, ctx).where(LeadProfile.work_item_id == item.id)).first()
            view = item_view(item, profile)

        state = wf.states[to_status]
        if self.notifier and (state.owner == "gate" or to_status == wf.escalate_to):
            name = getattr(view, "business_name", None) or view.id[:8]
            self.notifier.send(ctx, f"[{ctx.name}] {name} ({wf.key}) is now {to_status}" + (f": {note}" if note else ""))
        return view

    def log(self, ctx: OrgContext, item_id: str | None, actor_kind: str, actor_id: str | None, note: str) -> None:
        """Record something that isn't a status change (errors, budget pauses)."""
        with self.sessions.begin() as s:
            status = one_or_404(s, WorkItem, ctx, id=item_id).status if item_id else None
            self._event(s, ctx, item_id, status, status, actor_kind, actor_id, note)

    def _event(self, s: Session, ctx: OrgContext, item_id, from_status, to_status, actor_kind, actor_id, note):
        seq = (s.scalar(select(func.max(Event.seq)).where(Event.org_id == ctx.org_id)) or 0) + 1
        s.add(Event(seq=seq, org_id=ctx.org_id, work_item_id=item_id, from_status=from_status, to_status=to_status,
                    actor_kind=actor_kind, actor_id=actor_id, note=note, ts=self.clock()))

    # ------------------------------------------------------------------ claims (leases)

    def claim(self, ctx: OrgContext, item_id: str, holder: str, seconds: int | None = None) -> bool:
        """Claim an item if nobody holds an unexpired lease on it. Atomic, so two workers can't both win."""
        now = self.clock()
        seconds = seconds or self.config.settings.atlas.lease_seconds
        with self.sessions.begin() as s:
            result = s.execute(update(WorkItem).where(
                WorkItem.org_id == ctx.org_id, WorkItem.id == item_id, self.unclaimed(now),
            ).values(claimed_by=holder, claim_expires_at=now + timedelta(seconds=seconds)))
            return result.rowcount == 1

    def release(self, ctx: OrgContext, item_id: str, holder: str) -> None:
        with self.sessions.begin() as s:
            s.execute(update(WorkItem).where(WorkItem.org_id == ctx.org_id, WorkItem.id == item_id,
                                             WorkItem.claimed_by == holder).values(claimed_by=None, claim_expires_at=None))

    def hold(self, ctx: OrgContext, item_id: str, holder: str, seconds: float) -> None:
        """Keep an item untouchable for a while (retry backoff)."""
        with self.sessions.begin() as s:
            s.execute(update(WorkItem).where(WorkItem.org_id == ctx.org_id, WorkItem.id == item_id).values(
                claimed_by=holder, claim_expires_at=self.clock() + timedelta(seconds=seconds)))

    # ------------------------------------------------------------------ history helpers

    def record_failure(self, ctx: OrgContext, item_id: str, employee_id: str, error: str) -> int:
        """Log an employee error; return how many times it has failed since the item last changed status."""
        self.log(ctx, item_id, "employee", employee_id, f"{ERROR_PREFIX} {error}")
        with self.sessions() as s:
            last_change = s.scalar(select(func.max(Event.seq)).where(
                Event.org_id == ctx.org_id, Event.work_item_id == item_id,
                Event.from_status.is_distinct_from(Event.to_status))) or 0
            return s.scalar(select(func.count()).select_from(Event).where(
                Event.org_id == ctx.org_id, Event.work_item_id == item_id, Event.note.startswith(ERROR_PREFIX),
                Event.seq > last_change)) or 0

    def latest_feedback(self, ctx: OrgContext, item_id: str) -> str | None:
        """The newest failed QA report or CEO notes, whichever came last (spec v2 §7.4)."""
        with self.sessions() as s:
            qa = s.scalars(scoped(QAReport, ctx).where(QAReport.work_item_id == item_id, QAReport.passed.is_(False))
                           .order_by(QAReport.created_at.desc())).first()
            ceo = s.scalars(scoped(Approval, ctx).where(Approval.work_item_id == item_id, Approval.notes.is_not(None))
                            .order_by(Approval.decided_at.desc())).first()
        if ceo and (not qa or ceo.decided_at >= qa.created_at):
            return f"CEO notes: {ceo.notes}"
        if qa:
            issues = "; ".join(f"[{i.get('severity', '?')}] {i.get('description', '')}" for i in qa.issues)
            return f"QA report: {issues or 'failed'}"
        return None

    def history(self, ctx: OrgContext, item_id: str) -> list[Event]:
        with self.sessions() as s:
            return list(s.scalars(scoped(Event, ctx).where(Event.work_item_id == item_id).order_by(Event.seq)))

    @staticmethod
    def _escalated_from(s: Session, ctx: OrgContext, item_id: str) -> str | None:
        return s.scalars(select(Event.from_status).where(
            Event.org_id == ctx.org_id, Event.work_item_id == item_id, Event.to_status.is_not(None),
            Event.from_status.is_not(None), Event.from_status != Event.to_status,
        ).order_by(Event.seq.desc())).first()


__all__ = ["Board", "InvalidTransition", "NotFound", "item_view"]
