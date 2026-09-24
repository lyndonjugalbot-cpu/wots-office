"""What the CEO can do from the dashboard (spec §10). Every decision is an approvals row plus a
board transition, so it shows up in the lead's timeline."""
from __future__ import annotations

from ..core.board import Board
from ..core.models import Approval
from ..core.states import Status

CEO = "ceo"


class ActionError(Exception):
    pass


def _record(board: Board, lead_id: int, kind: str, decision: str, notes: str | None) -> None:
    with board.sessions.begin() as s:
        s.add(Approval(lead_id=lead_id, kind=kind, decision=decision, notes=notes, decided_at=board.clock()))


def approve_build(board: Board, lead_id: int, notes: str | None = None):
    lead = board.transition(lead_id, Status.APPROVED, CEO, notes or "Approved")
    _record(board, lead_id, "build", "approved", notes)
    return lead


def reject_build(board: Board, lead_id: int, notes: str):
    if not notes.strip():
        raise ActionError("Say what needs fixing, so the designer knows what to change")
    # The notes are recorded first, so Atlas hands them to the designer with the fix
    _record(board, lead_id, "build", "rejected", notes.strip())
    return board.transition(lead_id, Status.NEEDS_FIX, CEO, f"CEO rejected: {notes.strip()}")


def disqualify(board: Board, lead_id: int, reason: str):
    reason = reason.strip() or "ceo_decision"
    lead = board.transition(lead_id, Status.DISQUALIFIED, CEO, f"Disqualified: {reason}",
                            updates={"disqualify_reason": reason})
    _record(board, lead_id, "build", "disqualified", reason)
    return lead


def resolve_escalation(board: Board, lead_id: int, to_status: str, notes: str | None = None):
    """Send an escalated lead back (to the designer, or to where an error stopped it) or drop it."""
    decision = "dropped" if to_status == Status.DISQUALIFIED.value else "sent_back"
    if notes:
        _record(board, lead_id, "escalation", decision, notes)
    updates = {"disqualify_reason": notes or "dropped_after_escalation"} if decision == "dropped" else None
    lead = board.transition(lead_id, to_status, CEO, notes or f"Escalation {decision.replace('_', ' ')}", updates=updates)
    if not notes:
        _record(board, lead_id, "escalation", decision, None)
    return lead
