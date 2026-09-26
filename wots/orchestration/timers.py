"""Atlas timers (spec v2 §7.6): follow-ups, replies, preview TTL cleanup, retention purge.

Atlas calls run_timers() for each office every tick:
  - follow-ups (Phase 4): one follow-up `followup_after_days` after a pitch with no reply (the CEO
    approved it together with the pitch), sent in the recipient's window; LOST the same time later
  - replies (Phase 4): the office mailbox is read every few minutes; opt-outs and bounces are
    suppressed at once and the lead is LOST; real replies go to the CEO (REPLIED)
  - preview teardown (Phase 3)
The retention purge arrives in Phase 6.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from ..integrations.errors import IntegrationConfigError, IntegrationError

log = logging.getLogger(__name__)
TEARDOWN_STATES = ["LOST"]  # spec v2 §8: previews are taken down after preview_ttl_days if LOST


def run_timers(atlas, ctx) -> None:
    teardown_previews(atlas, ctx)
    if atlas.integrations is not None:
        read_replies(atlas, ctx)
        follow_up(atlas, ctx)


def _echo(atlas, ctx):
    return next((st for st in atlas.staff(ctx) if st.info.type.key == "cold_email"), None)


def follow_up(atlas, ctx) -> None:
    """Send the approved follow-up once `followup_after_days` pass without a reply; then close as LOST."""
    from ..employees.impl.cold_email import ColdEmail

    after = timedelta(days=atlas.config.settings.outreach.followup_after_days)
    now = atlas.clock()
    pitched = atlas.board.items(ctx, statuses=["PITCHED"], unclaimed=True)
    if not pitched:
        return
    echo = _echo(atlas, ctx)
    actor = echo.info.id if echo else "atlas"
    tools = atlas.integrations.for_office(ctx)
    desk = tools.outreach()
    for item in pitched:
        messages = desk.messages(item.id)
        initial, followup = messages.get("initial"), messages.get("followup")
        if initial is None or initial.sent_at is None:
            continue
        if followup is not None and followup.sent_at is None and followup.status == "approved":
            if now < initial.sent_at + after:
                continue
            reason = desk.suppressed(item.email)
            if reason:
                atlas.board.transition(ctx, item.id, "LOST", "employee", actor, f"No follow-up: {item.email} opted out ({reason})")
                continue
            job = _JobView(atlas, ctx, now)
            wait = ColdEmail.when_allowed(job, desk, item)
            if wait:
                if desk.schedule(followup.id, wait[0]):
                    atlas.board.log(ctx, item.id, "employee", actor, "Follow-up " + wait[1][0].lower() + wait[1][1:])
                continue
            try:
                message_id = ColdEmail.deliver(tools, atlas.files.item_dir(ctx.org_id, item.id), item, followup,
                                               dry_run=atlas.config.settings.dry_run,
                                               in_reply_to=initial.provider_message_id)
            except IntegrationError as e:
                log.warning("Follow-up to %s failed: %s", item.email, e)
                continue
            desk.mark_sent(followup.id, message_id, atlas.config.settings.dry_run)
            atlas.board.log(ctx, item.id, "employee", actor, f"Follow-up sent to {item.email}")
            continue
        last = (followup.sent_at if followup is not None and followup.sent_at else initial.sent_at)
        if now >= last + after:
            atlas.board.transition(ctx, item.id, "LOST", "employee", actor, "No reply after the follow-up")


class _JobView:
    """The bits of an EmployeeContext that ColdEmail.when_allowed reads."""

    def __init__(self, atlas, ctx, now):
        self.config, self.now = atlas.config, now


def read_replies(atlas, ctx) -> None:
    """Every few minutes: read the office mailbox and act on replies from people we pitched."""
    last = atlas.timer_state.get(("replies", ctx.org_id))
    every = timedelta(minutes=atlas.config.settings.outreach.reply_check_minutes)
    now = atlas.clock()
    if last and now - last < every:
        return
    atlas.timer_state[("replies", ctx.org_id)] = now
    tools = atlas.integrations.for_office(ctx)
    try:
        reader = tools.imap_reader()
        if reader is None:
            return
        desk = tools.outreach()
        recipients = desk.recipients()
        if not recipients:
            return
        messages = reader.since((now - timedelta(days=21)).date())
    except IntegrationConfigError as e:
        atlas._tell_ceo_once(ctx, f"Integration unavailable (mailbox): {e}. Replies aren't being read.")
        return
    except IntegrationError as e:
        log.warning("Couldn't read %s's mailbox: %s", ctx.slug, e)
        return
    echo = _echo(atlas, ctx)
    actor = echo.info.id if echo else "atlas"
    for mail in messages:
        kind = mail.kind
        if kind == "auto":
            continue
        if kind == "bounce":
            hits = [addr for addr in recipients if addr in mail.text.lower()]
        else:
            hits = [mail.from_addr] if mail.from_addr in recipients else []
        for addr in hits:
            item = atlas.board.get(ctx, recipients[addr])
            if item.status != "PITCHED":
                continue  # already handled
            if kind in {"opt_out", "bounce"}:
                desk.suppress(addr, "opted out by reply" if kind == "opt_out" else "bounced")
                note = "Asked not to be contacted; suppressed" if kind == "opt_out" else "The email bounced; suppressed"
                atlas.board.transition(ctx, item.id, "LOST", "employee", actor, note)
            else:
                first = next((line.strip() for line in mail.text.splitlines() if line.strip() and not line.startswith(">")), "")
                atlas.board.transition(ctx, item.id, "REPLIED", "employee", actor, f"Replied: {first[:200]}")


def teardown_previews(atlas, ctx) -> int:
    """Take down previews of LOST leads once they're older than previews.preview_ttl_days."""
    if atlas.integrations is None:
        return 0
    ttl = timedelta(days=atlas.config.settings.previews.preview_ttl_days)
    due = []
    for item in atlas.board.items(ctx, statuses=TEARDOWN_STATES):
        preview = (item.checks or {}).get("preview") or {}
        if preview.get("alias") and not preview.get("removed_at") and preview.get("deployed_at"):
            if atlas.clock() - datetime.fromisoformat(preview["deployed_at"]) >= ttl:
                due.append((item, preview))
    if not due:
        return 0
    deployer = next((st for st in atlas.staff(ctx) if st.info.type.key == "deployment"), None)
    actor = deployer.info.id if deployer else "atlas"
    host = atlas.integrations.for_office(ctx).preview_host()
    removed = 0
    for item, preview in due:
        try:
            host.delete(preview["alias"])
        except IntegrationError as e:  # try again next tick; a broken token is reported by deploys
            log.warning("Couldn't take down the preview for %s: %s", item.id, e)
            continue
        checks = {**item.checks, "preview": {**preview, "removed_at": atlas.clock().isoformat(timespec="seconds")}}
        atlas.board.update_profile(ctx, item.id, {"preview_url": None, "checks": checks}, "employee", actor,
                                   f"Preview taken down ({ttl.days} days after deploying; the lead was lost)")
        removed += 1
    return removed
