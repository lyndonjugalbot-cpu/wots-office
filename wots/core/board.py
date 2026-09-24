"""The task board: the one source of truth for leads (spec §2.1).

Every status change goes through Board.transition(), which checks the scope's transition
table and writes an events row (spec §2.4). Claims are leases, so a crashed agent's lead is
picked up again once its lease runs out (spec §6.1).
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from .clock import Clock, utcnow
from .config import Config
from .models import Approval, Event, Lead, QAReport
from .states import TERMINAL_STATUSES, WORKING_STATUSES, Scope, Status, is_allowed

# Lead columns an agent result or a transition may set. Workflow fields (status, claims) are not included.
UPDATABLE_FIELDS = {
    "business_name", "category", "description", "country", "region", "timezone", "address", "phone",
    "email", "contact_name", "website_found", "social_links", "entity_type", "registry_id", "source",
    "source_ref", "assigned_to", "fix_count", "preview_url", "disqualify_reason",
}
ERROR_PREFIX = "error:"


class InvalidTransition(Exception):
    pass


class LeadNotFound(Exception):
    pass


class Board:
    def __init__(self, sessions: sessionmaker[Session], config: Config, notifier=None, clock: Clock = utcnow):
        self.sessions = sessions
        self.config = config
        self.notifier = notifier
        self.clock = clock

    # ------------------------------------------------------------------ leads

    def create_lead(self, *, scope: str, business_name: str, country: str, actor: str,
                    note: str | None = None, **fields: Any) -> Lead:
        scope = Scope(scope)
        unknown = set(fields) - UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"Unknown lead fields: {', '.join(sorted(unknown))}")
        with self.sessions.begin() as s:
            now = self.clock()
            lead = Lead(scope=scope.value, status=Status.NEW.value, business_name=business_name,
                        country=country, created_at=now, updated_at=now, **fields)
            s.add(lead)
            s.flush()
            s.add(Event(lead_id=lead.id, from_status=None, to_status=Status.NEW.value, actor=actor,
                        note=note, ts=now))
        return lead

    def get(self, lead_id: int) -> Lead:
        with self.sessions() as s:
            lead = s.get(Lead, lead_id)
            if not lead:
                raise LeadNotFound(lead_id)
            return lead

    def transition(self, lead_id: int, to_status: str, actor: str, note: str | None = None,
                   updates: dict[str, Any] | None = None) -> Lead:
        """Move a lead to a new status, if the scope's table allows it, and log the event."""
        to_status = Status(to_status)
        updates = dict(updates or {})
        bad = set(updates) - UPDATABLE_FIELDS
        if bad:
            raise ValueError(f"These fields can't be set by a transition: {', '.join(sorted(bad))}")

        with self.sessions.begin() as s:
            lead = s.get(Lead, lead_id)
            if not lead:
                raise LeadNotFound(lead_id)
            from_status = Status(lead.status)
            allowed = is_allowed(lead.scope, from_status, to_status)
            if not allowed and from_status == Status.ESCALATED and to_status in WORKING_STATUSES[Scope(lead.scope)]:
                # The CEO may resume an error escalation at the working status it came from
                allowed = to_status == self._escalated_from(s, lead.id)
            if not allowed:
                raise InvalidTransition(f"{lead.scope}: {from_status} -> {to_status} is not allowed (lead {lead.id})")

            if from_status == Status.ESCALATED and to_status not in TERMINAL_STATUSES:
                updates.setdefault("fix_count", 0)  # the CEO's send-back starts a fresh fix budget
            now = self.clock()
            for key, value in updates.items():
                setattr(lead, key, value)
            lead.status = to_status.value
            lead.updated_at = now
            s.add(Event(lead_id=lead.id, from_status=from_status.value, to_status=to_status.value,
                        actor=actor, note=note, ts=now))

        if self.notifier and to_status.value in self.config.settings.notify.on_statuses:
            self.notifier.send(f"Lead {lead.id} ({lead.business_name}, {lead.scope}) is now {to_status}"
                               + (f": {note}" if note else ""), lead_id=lead.id)
        return lead

    def log(self, lead_id: int | None, actor: str, note: str) -> None:
        """Record something that isn't a status change (errors, budget pauses)."""
        with self.sessions.begin() as s:
            status = s.get(Lead, lead_id).status if lead_id else None
            s.add(Event(lead_id=lead_id, from_status=status, to_status=status, actor=actor, note=note,
                        ts=self.clock()))

    # ------------------------------------------------------------------ claims (leases)

    def claim(self, lead_id: int, agent: str, seconds: int | None = None) -> bool:
        """Claim a lead if nobody holds an unexpired lease on it. Atomic, so two workers can't both win."""
        now = self.clock()
        seconds = seconds or self.config.settings.atlas.lease_seconds
        with self.sessions.begin() as s:
            result = s.execute(
                update(Lead)
                .where(Lead.id == lead_id, or_(Lead.claimed_by.is_(None), Lead.claim_expires_at <= now))
                .values(claimed_by=agent, claim_expires_at=now + timedelta(seconds=seconds))
            )
            return result.rowcount == 1

    def release(self, lead_id: int, agent: str) -> None:
        with self.sessions.begin() as s:
            s.execute(update(Lead).where(Lead.id == lead_id, Lead.claimed_by == agent)
                      .values(claimed_by=None, claim_expires_at=None))

    def hold(self, lead_id: int, holder: str, seconds: float) -> None:
        """Keep a lead untouchable for a while (used for retry backoff)."""
        with self.sessions.begin() as s:
            s.execute(update(Lead).where(Lead.id == lead_id)
                      .values(claimed_by=holder, claim_expires_at=self.clock() + timedelta(seconds=seconds)))

    @staticmethod
    def unclaimed(now):
        return or_(Lead.claimed_by.is_(None), Lead.claim_expires_at <= now)

    # ------------------------------------------------------------------ history helpers

    def record_failure(self, lead_id: int, agent: str, error: str) -> int:
        """Log an agent error and return how many times it has failed since the last status change."""
        self.log(lead_id, agent, f"{ERROR_PREFIX} {error}")
        with self.sessions() as s:
            # Event ids, not timestamps, give the order: several events can share a timestamp
            last_change = s.scalar(
                select(func.max(Event.id)).where(Event.lead_id == lead_id, Event.from_status.is_distinct_from(Event.to_status))
            ) or 0
            return s.scalar(
                select(func.count()).select_from(Event).where(
                    Event.lead_id == lead_id, Event.note.startswith(ERROR_PREFIX), Event.id > last_change,
                )
            ) or 0

    def latest_feedback(self, lead_id: int) -> str | None:
        """The newest QA report issues or CEO rejection notes, whichever came last (spec §6.3)."""
        with self.sessions() as s:
            qa = s.scalars(select(QAReport).where(QAReport.lead_id == lead_id, QAReport.passed.is_(False))
                           .order_by(QAReport.created_at.desc(), QAReport.id.desc())).first()
            ceo = s.scalars(select(Approval).where(Approval.lead_id == lead_id, Approval.notes.is_not(None))
                            .order_by(Approval.decided_at.desc(), Approval.id.desc())).first()
        if ceo and (not qa or ceo.decided_at >= qa.created_at):
            return f"CEO notes: {ceo.notes}"
        if qa:
            issues = "; ".join(f"[{i.get('severity', '?')}] {i.get('description', '')}" for i in qa.issues)
            return f"QA report: {issues or 'failed'}"
        return None

    def history(self, lead_id: int) -> list[Event]:
        with self.sessions() as s:
            return list(s.scalars(select(Event).where(Event.lead_id == lead_id).order_by(Event.ts, Event.id)))

    @staticmethod
    def _escalated_from(s: Session, lead_id: int) -> Status | None:
        prev = s.scalars(select(Event.from_status).where(Event.lead_id == lead_id,
                                                         Event.to_status == Status.ESCALATED.value,
                                                         Event.from_status != Status.ESCALATED.value)
                         .order_by(Event.id.desc())).first()
        return Status(prev) if prev else None
