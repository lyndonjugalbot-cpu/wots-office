"""CEO notifications (spec §6.5): a Telegram or Discord webhook, or a local log file.

In DRY_RUN mode, or with no webhook configured, messages go to data/outbox/notifications.log.
"""
from __future__ import annotations

import logging
from pathlib import Path

import httpx

from ..core.clock import Clock, utcnow

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, webhook: str | None, dry_run: bool, outbox: Path, clock: Clock = utcnow):
        self.webhook = webhook
        self.dry_run = dry_run
        self.log_path = outbox / "notifications.log"
        self.clock = clock
        self.sent: list[str] = []  # handy in tests and the dashboard

    def send(self, text: str, lead_id: int | None = None) -> None:
        self.sent.append(text)
        if self.dry_run or not self.webhook:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a") as f:
                f.write(f"{self.clock().isoformat(timespec='seconds')}Z  {text}\n")
            return
        try:
            # "content" is Discord's field and "text" is Telegram's; each ignores the other
            httpx.post(self.webhook, json={"content": text, "text": text}, timeout=10).raise_for_status()
        except httpx.HTTPError as e:  # a notification failing must never stop the pipeline
            log.warning("Notification failed for lead %s: %s", lead_id, e)
