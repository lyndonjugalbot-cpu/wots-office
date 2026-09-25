"""What people do at the human gates (spec v2 §12). Every decision is an approvals row plus a board
transition, so it shows up in the item's timeline, and each checks the person's role."""
from __future__ import annotations

from ..core.board import Board
from ..core.context import OrgContext
from ..core.models import Approval


class ActionError(Exception):
    pass


class Forbidden(ActionError):
    """The person's role can't make this decision."""


def _gate_check(board: Board, ctx: OrgContext, item_id: str, expected: set[str] | None = None) -> None:
    item = board.get(ctx, item_id)
    wf = board.workflow(item.workflow_key)
    state = wf.states[item.status]
    if state.owner != "gate":
        raise ActionError(f"{item.status} isn't waiting for a person")
    if expected and item.status not in expected:
        raise ActionError(f"This item is at {item.status}")
    if not ctx.can_pass_gate(state.value):
        raise Forbidden(f"Only the {state.value.upper()} (or a manager they delegated to) can decide this")


def _record(board: Board, ctx: OrgContext, item_id: str, kind: str, decision: str, notes: str | None) -> None:
    with board.sessions.begin() as s:
        s.add(Approval(org_id=ctx.org_id, work_item_id=item_id, kind=kind, decision=decision, notes=notes,
                       decided_by_user_id=ctx.user_id, decided_at=board.clock()))


def approve_build(board: Board, ctx: OrgContext, item_id: str, notes: str | None = None):
    _gate_check(board, ctx, item_id, {"READY_FOR_APPROVAL"})
    item = board.transition(ctx, item_id, "APPROVED", "user", ctx.user_id, notes or "Approved")
    _record(board, ctx, item_id, "build", "approved", notes)
    return item


def reject_build(board: Board, ctx: OrgContext, item_id: str, notes: str):
    _gate_check(board, ctx, item_id, {"READY_FOR_APPROVAL"})
    if not notes.strip():
        raise ActionError("Say what needs fixing, so the designer knows what to change")
    # The notes are recorded first, so Atlas hands them to the designer with the fix
    _record(board, ctx, item_id, "build", "rejected", notes.strip())
    return board.transition(ctx, item_id, "NEEDS_FIX", "user", ctx.user_id, f"CEO rejected: {notes.strip()}")


def disqualify(board: Board, ctx: OrgContext, item_id: str, reason: str):
    _gate_check(board, ctx, item_id)
    reason = reason.strip() or "ceo_decision"
    item = board.transition(ctx, item_id, "DISQUALIFIED", "user", ctx.user_id, f"Disqualified: {reason}",
                            updates={"disqualify_reason": reason})
    _record(board, ctx, item_id, "build", "disqualified", reason)
    return item


def resolve_escalation(board: Board, ctx: OrgContext, item_id: str, to_status: str, notes: str | None = None):
    """Send an escalated item back (to its designer, or where an error stopped it) or drop it."""
    item = board.get(ctx, item_id)
    wf = board.workflow(item.workflow_key)
    if item.status != wf.escalate_to:
        raise ActionError("This item isn't escalated")
    _gate_check(board, ctx, item_id)
    decision = "dropped" if to_status == "DISQUALIFIED" else "sent_back"
    _record(board, ctx, item_id, "escalation", decision, notes)
    updates = {"disqualify_reason": notes or "dropped_after_escalation"} if decision == "dropped" else None
    return board.transition(ctx, item_id, to_status, "user", ctx.user_id,
                            notes or f"Escalation {decision.replace('_', ' ')}", updates=updates)
