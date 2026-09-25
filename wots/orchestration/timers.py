"""Atlas timers (spec v2 §7.6): follow-ups, preview TTL cleanup, retention purge.

Follow-ups arrive with the Outreach Specialist (Phase 4), preview teardown with Deployment
(Phase 3), and the retention purge with hardening (Phase 6). Atlas calls run_timers() every
tick so they slot in here without touching the orchestrator.
"""
from __future__ import annotations


def run_timers(atlas, ctx) -> None:  # noqa: ARG001 - filled in by later phases
    return None
