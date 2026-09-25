"""Notifications when work reaches a human gate or escalates (spec v2 §7.8).

In dry-run mode, or with no webhook, messages go to data/outbox/{org_id}/notifications.log.
The platform webhook (NOTIFY_WEBHOOK) is only used for internal offices; customer offices will
connect their own webhook integration (Phase 6+).
"""
from __future__ import annotations

import logging

import httpx

from ..core.clock import Clock, utcnow
from ..core.context import OrgContext
from ..core.files import FileStore

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, webhook: str | None, dry_run: bool, files: FileStore, clock: Clock = utcnow):
        self.webhook = webhook
        self.dry_run = dry_run
        self.files = files
        self.clock = clock
        self.sent: list[tuple[str, str]] = []  # (org_id, text): handy in tests

    def send(self, ctx: OrgContext, text: str) -> None:
        self.sent.append((ctx.org_id, text))
        webhook = self.webhook if ctx.is_internal else None
        if self.dry_run or not webhook:
            with (self.files.outbox(ctx.org_id) / "notifications.log").open("a") as f:
                f.write(f"{self.clock().isoformat(timespec='seconds')}Z  {text}\n")
            return
        try:
            # "content" is Discord's field and "text" is Telegram's; each ignores the other
            httpx.post(webhook, json={"content": text, "text": text}, timeout=10).raise_for_status()
        except httpx.HTTPError as e:  # a notification failing must never stop the pipeline
            log.warning("Notification failed for %s: %s", ctx.slug, e)
