"""Integration errors. A config error (missing key, rejected key) breaks every call the same way, so
Atlas pauses that kind of work and tells the CEO once instead of burning each item's retries."""
from __future__ import annotations


class IntegrationError(RuntimeError):
    """A call failed (timeout, 5xx, bad reply). Retried like any employee failure."""


class IntegrationConfigError(IntegrationError):
    """The integration isn't set up for this office (no key, key rejected, not allowed)."""
