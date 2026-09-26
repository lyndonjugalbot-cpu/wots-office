"""Outreach Specialist (Echo in the internal office), spec v2 §8, §11, Phase 4.

draft_pitch (at PREVIEW_DEPLOYED): writes the pitch and its one follow-up for the CEO's pitch queue.
  Claude writes only the subject line and a one- or two-sentence opener from the lead's record;
  the body comes from templates/email/, and the footer (who we are, postal address, where we found
  them, how to opt out) is added here, per country, so the legal parts never vary.
send_pitch (at PITCH_APPROVED): sends the approved pitch from the office's own mailbox, only in
  the recipient's local send window and within the office's daily cap.

Suppression is checked at both steps. Nothing is ever sent without the CEO's approval
(`auto_send` is off), and in dry-run mode "sent" means saved to data/outbox/.
"""
from __future__ import annotations

import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from ...core.config import ROOT
from ...core.metering import LLMError
from ...core.outreach import next_window
from ...integrations.errors import IntegrationConfigError
from ..base import ArtifactOut, BaseEmployee, EmployeeContext, EmployeeResult, NotYet
from .data_verifier import region_timezone

TEMPLATES = ROOT / "templates" / "email"
_env = Environment(loader=FileSystemLoader(str(TEMPLATES)), undefined=StrictUndefined, keep_trailing_newline=True,
                   trim_blocks=True)
PLACEHOLDER = "[preview link: published once DRY_RUN is off]"
DRY_RUN_FROM = "Wots Office (dry run) <outreach@example.invalid>"
SCREENSHOT = "qa/screens/375.png"
MAX_ATTACHMENT = 900_000  # bytes; bigger attachments hurt deliverability
PUBLIC_SOURCES = {"osm": "OpenStreetMap", "places": "Google Maps", "companies_house": "the Companies House register"}

SYSTEM = """You are {name}, who writes short, friendly cold emails for a small web design studio.
You will get one business's public details. Write:
- subject: under 60 characters, plain and honest, e.g. "A website idea for Joe's Plumbing". No "Re:" or "Fwd:",
  no clickbait, no exclamation marks, no emoji.
- opener: one or two short sentences that greet them warmly and mention something true from their details
  (their trade and town). This goes before the main pitch, which is written separately.
Rules: use only the facts given; never invent reviews, awards, customers or opinions about their business.
Don't mention prices or the website itself (the pitch does that). Use {spelling} spelling.
Reply with JSON only."""
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["subject", "opener"],
          "properties": {"subject": {"type": "string"}, "opener": {"type": "string"}}}


class ColdEmail(BaseEmployee):
    type_key = "cold_email"

    def run(self, lead, task: str, ctx: EmployeeContext) -> EmployeeResult:
        if lead.workflow_key != "website":
            raise NotImplementedError("Ad refresh pitches arrive in Phase 5")
        if task == "draft_pitch":
            return self._draft(lead, ctx)
        if task == "send_pitch":
            return self._send(lead, ctx)
        raise NotImplementedError(task)

    # ---------------------------------------------------------------- drafting

    def _draft(self, lead, ctx: EmployeeContext) -> EmployeeResult:
        postal = ctx.org.settings.get("postal_address")
        if not postal:
            raise IntegrationConfigError("Outreach needs the office's postal address: accept the outreach terms "
                                         "(`wots orgs accept-outreach-terms`)")
        desk = ctx.tools.outreach()
        if not lead.email:
            return EmployeeResult("ESCALATED", "No email address to pitch to. Add one (and where it's published) "
                                               "on the lead, then send it back to PREVIEW_DEPLOYED.")
        reason = desk.suppressed(lead.email)
        if reason:
            return EmployeeResult("DISQUALIFIED", f"{lead.email} is on the suppression list ({reason})",
                                  updates={"disqualify_reason": "suppressed"})
        rules = ctx.config.countries[lead.country]
        if rules.require_conspicuous_publication and not self._published(lead):
            return EmployeeResult("ESCALATED", f"{lead.country} rules: we may only email an address the business "
                                               "publishes itself. Add where it's published on the lead, then send it back.")
        if rules.require_source_disclosure and not self._source(lead):
            return EmployeeResult("ESCALATED", f"{lead.country} rules: the email must say where we found their details. "
                                               "Add where you found them on the lead, then send it back.")
        live = bool(lead.preview_url)
        if not live and not ctx.dry_run:
            return EmployeeResult("ESCALATED", "There's no live preview to link to; Dock needs to deploy it first.")

        subject, opener = self._personal_touch(lead, ctx, rules.spelling)
        office = ctx.org.name
        sender = ctx.config.settings.outreach.sender_name
        screenshot = (ctx.item_dir / SCREENSHOT).exists() and (ctx.item_dir / SCREENSHOT).stat().st_size <= MAX_ATTACHMENT
        common = {"greeting": self._greeting(lead), "business_name": lead.business_name, "sender_name": sender,
                  "office_name": office, "preview_url": lead.preview_url if live else PLACEHOLDER}
        body = _env.get_template("website.md").render(
            **common, opener=opener, screenshot=screenshot,
            pricing=ctx.config.settings.outreach.pricing.get(lead.country)).strip()
        followup = _env.get_template("website_followup.md").render(**common).strip()
        footer = self._footer(lead, rules, office, postal)
        drafts = {"initial": (subject, f"{body}\n\n{footer}"), "followup": (f"Re: {subject}", f"{followup}\n\n{footer}")}
        desk.replace_drafts(lead.id, drafts)
        folder = ctx.item_dir / "pitch"
        folder.mkdir(exist_ok=True)
        for kind, (subj, text) in drafts.items():
            (folder / f"{kind}.txt").write_text(f"To: {lead.email}\nSubject: {subj}\n\n{text}\n")
        return EmployeeResult("PITCH_DRAFTED", f"Pitch drafted for {lead.email}: “{subject}”",
                              artifacts=[ArtifactOut("pitch", "pitch/initial.txt")])

    @staticmethod
    def _published(lead) -> bool:
        """AU inferred consent: the address came from a public listing, or someone recorded where it's published."""
        found_at = ((lead.checks or {}).get("email") or {}).get("found_at")
        manual = ((lead.checks or {}).get("email") or {}).get("added_by")
        return bool(found_at) or (lead.source in PUBLIC_SOURCES and not manual)

    @staticmethod
    def _source(lead) -> str | None:
        """Where we found the business, in words we can put in the email (None if we can't say)."""
        return PUBLIC_SOURCES.get(lead.source or "") or ((lead.checks or {}).get("email") or {}).get("found_at")

    @staticmethod
    def _greeting(lead) -> str:
        if lead.contact_name:
            return f"Hi {lead.contact_name.split()[0]},"
        return "Hi there,"

    def _personal_touch(self, lead, ctx: EmployeeContext, spelling: str) -> tuple[str, str]:
        if not ctx.llm or not ctx.model:
            raise LLMError(f"{self.name} needs an LLM and a model")
        record = {k: v for k, v in {"business_name": lead.business_name, "trade": lead.category,
                                    "town": lead.region if lead.country == "UK" else None, "country": lead.country,
                                    "contact_name": lead.contact_name}.items() if v}
        prompt = "Business details:\n" + json.dumps(record, indent=2)
        if ctx.feedback:
            prompt += f"\n\nThe CEO sent the last draft back. Fix this:\n{ctx.feedback}"
        response = ctx.llm.complete(
            model=ctx.model, effort=ctx.effort, max_tokens=2000,
            system=SYSTEM.format(name=self.name, spelling=spelling), messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}})
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError(f"{self.name}'s reply wasn't valid JSON") from e
        subject, opener = data.get("subject", "").strip(), data.get("opener", "").strip()
        if not subject or not opener:
            raise LLMError(f"{self.name} left the subject or opener empty")
        if len(subject) > 80 or subject.lower().startswith(("re:", "fwd:", "fw:")) or "http" in opener + subject:
            raise LLMError(f"{self.name}'s subject or opener broke the rules: {subject!r}")
        return subject, opener[:400]

    @staticmethod
    def _footer(lead, rules, office: str, postal: str) -> str:
        lines = ["--", f"{office}, {postal}"]
        if rules.require_source_disclosure:
            lines.append(f"We found {lead.business_name}'s details on {ColdEmail._source(lead)}.")
        lines.append("This is a one-off business proposal. If you'd rather not hear from us, reply \"unsubscribe\" "
                     "and we won't contact you again.")
        return "\n".join(lines)

    # ---------------------------------------------------------------- sending

    def _send(self, lead, ctx: EmployeeContext) -> EmployeeResult:
        desk = ctx.tools.outreach()
        pitch = desk.messages(lead.id).get("initial")
        if pitch is None or pitch.status != "approved":
            raise RuntimeError("There's no approved pitch to send")
        reason = desk.suppressed(lead.email)
        if reason:
            return EmployeeResult("DISQUALIFIED", f"Not sent: {lead.email} was suppressed after approval ({reason})",
                                  updates={"disqualify_reason": "suppressed"})
        if PLACEHOLDER in pitch.body and not ctx.dry_run:
            return EmployeeResult("ESCALATED", "This pitch was drafted in dry-run mode without a real preview link. "
                                               "Send it back to PREVIEW_DEPLOYED to redraft it.")
        wait = self.when_allowed(ctx, desk, lead)
        if wait:
            until, note = wait
            raise NotYet(until, note, announce=desk.schedule(pitch.id, until))
        message_id = self.deliver(ctx.tools, ctx.item_dir, lead, pitch, dry_run=ctx.dry_run)
        desk.mark_sent(pitch.id, message_id, ctx.dry_run)
        where = "saved to the outbox (dry run)" if ctx.dry_run else "sent"
        return EmployeeResult("PITCHED", f"Pitch {where} to {lead.email}")

    @staticmethod
    def when_allowed(ctx, desk, lead):
        """(until, note) if sending must wait for the recipient's window or tomorrow's cap; None to send now."""
        rules = ctx.config.countries[lead.country]
        tz = lead.timezone or region_timezone(lead.country, lead.region)
        opens = next_window(ctx.now, tz, rules.send_window_local)
        if opens:
            return opens, f"Scheduled for the recipient's send window ({opens:%a %d %b %H:%M} UTC; {tz})"
        cap = ctx.config.settings.outreach.daily_send_cap
        if desk.sent_today() >= cap:
            from datetime import timedelta

            later = next_window(ctx.now + timedelta(days=1), tz, rules.send_window_local) or ctx.now + timedelta(days=1)
            return later, f"Today's send cap ({cap}) is reached; scheduled for the next window"
        return None

    @staticmethod
    def deliver(tools, item_dir: Path, lead, message, dry_run: bool, in_reply_to: str | None = None) -> str:
        from ...integrations.email import OutgoingEmail

        attachments = []
        shot = item_dir / SCREENSHOT
        if message.kind == "initial" and shot.exists() and shot.stat().st_size <= MAX_ATTACHMENT:
            attachments.append((f"{lead.business_name} on a phone.png", shot.read_bytes(), "image/png"))
        sender = tools.from_address() or (DRY_RUN_FROM if dry_run else None)
        if not sender:
            raise IntegrationConfigError("The office mailbox has no From address: add OUTREACH_FROM to .env")
        mail = OutgoingEmail(to=lead.email, subject=message.subject, body=message.body, from_addr=sender,
                             attachments=attachments, in_reply_to=in_reply_to)
        return tools.email_sender().send(mail)
