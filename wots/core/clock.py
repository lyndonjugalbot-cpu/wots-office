"""Time in one place, so tests can control it. All stored times are naive UTC."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Callable

Clock = Callable[[], datetime]


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
