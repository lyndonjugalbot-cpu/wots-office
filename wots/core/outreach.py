"""Outreach records, suppression and send windows for one office (spec v2 §8, §11, Phase 4).

Suppression is checked when a pitch is drafted AND again right before anything is sent. An
address is suppressed by email, or by domain for company-wide opt-outs (never for webmail
domains like gmail.com, which would block everyone on them).
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select, update

from .context import OrgContext
from .metering import start_of_local_day
from .models import Outreach, Suppression

WEBMAIL = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com", "icloud.com",
           "me.com", "aol.com", "proton.me", "protonmail.com", "gmx.com", "hotmail.co.uk", "yahoo.co.uk",
           "bigpond.com", "optusnet.com.au"}
DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


def normalise_email(address: str | None) -> str:
    return (address or "").strip().lower()


def next_window(now_utc: datetime, tz_name: str, window) -> datetime | None:
    """None if `now` is inside the recipient's send window; otherwise when the next one opens (UTC, naive)."""
    tz = ZoneInfo(tz_name)
    local = now_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    start, end = (time.fromisoformat(window.start), time.fromisoformat(window.end))
    days = {DAYS.index(d) for d in window.days}
    if local.weekday() in days and start <= local.time() < end:
        return None
    for ahead in range(0, 8):
        day = (local + timedelta(days=ahead)).date()
        if day.weekday() not in days:
            continue
        opens = datetime.combine(day, start, tzinfo=tz)
        if opens > local:
            return opens.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    raise ValueError("The send window has no days")


class OutreachDesk:
    def __init__(self, sessions, ctx: OrgContext, clock, timezone: str):
        self.sessions, self.ctx, self.clock, self.timezone = sessions, ctx, clock, timezone

    # ---------------------------------------------------------------- suppression

    def suppressed(self, address: str | None) -> str | None:
        """Why this address may not be emailed, or None."""
        addr = normalise_email(address)
        if not addr:
            return None
        domain = addr.split("@")[-1]
        with self.sessions() as s:
            row = s.scalars(select(Suppression).where(
                Suppression.org_id == self.ctx.org_id,
                (Suppression.email == addr) | (Suppression.domain == domain))).first()
        return row.reason if row else None

    def suppress(self, address: str, reason: str, whole_domain: bool = False) -> None:
        addr = normalise_email(address)
        domain = addr.split("@")[-1]
        if whole_domain and domain in WEBMAIL:
            raise ValueError(f"{domain} is a webmail domain; suppress the address instead")
        with self.sessions.begin() as s:
            exists = s.scalars(select(Suppression).where(
                Suppression.org_id == self.ctx.org_id,
                Suppression.domain == domain if whole_domain else Suppression.email == addr)).first()
            if not exists:
                s.add(Suppression(org_id=self.ctx.org_id, email=None if whole_domain else addr,
                                  domain=domain if whole_domain else None, reason=reason, added_at=self.clock()))

    def unsuppress(self, suppression_id: str) -> None:
        with self.sessions.begin() as s:
            s.execute(delete(Suppression).where(Suppression.org_id == self.ctx.org_id, Suppression.id == suppression_id))

    def suppression_list(self) -> list[Suppression]:
        with self.sessions() as s:
            return list(s.scalars(select(Suppression).where(Suppression.org_id == self.ctx.org_id)
                                  .order_by(Suppression.added_at.desc())))

    # ---------------------------------------------------------------- drafts and sends

    def messages(self, item_id: str) -> dict[str, Outreach]:
        """The item's current pitch messages by kind (initial, followup)."""
        with self.sessions() as s:
            rows = s.scalars(select(Outreach).where(Outreach.org_id == self.ctx.org_id, Outreach.work_item_id == item_id,
                                                    Outreach.status != "discarded").order_by(Outreach.created_at))
            return {r.kind: r for r in rows}

    def replace_drafts(self, item_id: str, drafts: dict[str, tuple[str, str]]) -> None:
        """A new draft (or redraft) replaces any unsent messages for the item."""
        with self.sessions.begin() as s:
            s.execute(update(Outreach).where(Outreach.org_id == self.ctx.org_id, Outreach.work_item_id == item_id,
                                             Outreach.sent_at.is_(None)).values(status="discarded"))
            for kind, (subject, body) in drafts.items():
                s.add(Outreach(org_id=self.ctx.org_id, work_item_id=item_id, kind=kind, subject=subject, body=body,
                               status="draft"))

    def approve(self, item_id: str, edits: dict[str, dict] | None = None) -> None:
        """The CEO approves the pitch and its follow-up together (optionally edited)."""
        edits = edits or {}
        with self.sessions.begin() as s:
            for row in s.scalars(select(Outreach).where(Outreach.org_id == self.ctx.org_id,
                                                        Outreach.work_item_id == item_id, Outreach.status == "draft")):
                change = edits.get(row.kind) or {}
                row.subject = (change.get("subject") or row.subject).strip()
                row.body = change.get("body") or row.body
                row.status = "approved"

    def schedule(self, outreach_id: str, when: datetime | None) -> bool:
        """Record when a message will go out. True if that changed (worth a note on the timeline)."""
        with self.sessions.begin() as s:
            row = s.scalars(select(Outreach).where(Outreach.org_id == self.ctx.org_id, Outreach.id == outreach_id)).one()
            changed = row.scheduled_for != when
            row.scheduled_for = when
            return changed

    def mark_sent(self, outreach_id: str, message_id: str, dry_run: bool) -> None:
        with self.sessions.begin() as s:
            row = s.scalars(select(Outreach).where(Outreach.org_id == self.ctx.org_id, Outreach.id == outreach_id)).one()
            row.sent_at, row.status, row.provider_message_id = self.clock(), "saved_to_outbox" if dry_run else "sent", message_id

    def sent_today(self) -> int:
        since = start_of_local_day(self.clock(), self.timezone)
        with self.sessions() as s:
            return s.scalar(select(func.count()).select_from(Outreach).where(
                Outreach.org_id == self.ctx.org_id, Outreach.sent_at >= since)) or 0

    def recipients(self) -> dict[str, str]:
        """Email address -> work item id, for everyone this office has emailed (to match replies)."""
        from .models import LeadProfile

        with self.sessions() as s:
            rows = s.execute(select(LeadProfile.email, LeadProfile.work_item_id).join(
                Outreach, Outreach.work_item_id == LeadProfile.work_item_id).where(
                Outreach.org_id == self.ctx.org_id, LeadProfile.org_id == self.ctx.org_id,
                Outreach.sent_at.is_not(None))).all()
        return {normalise_email(e): i for e, i in rows if e}
