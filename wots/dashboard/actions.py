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


# ---------------------------------------------------------------- outreach (Phase 4)


def approve_pitch(board: Board, desk, ctx: OrgContext, item_id: str, edits: dict | None = None,
                  notes: str | None = None):
    """Approve the pitch and its follow-up together, optionally with the CEO's edits. Echo then sends
    it in the recipient's window."""
    _gate_check(board, ctx, item_id, {"PITCH_DRAFTED"})
    for kind, change in (edits or {}).items():
        if kind not in {"initial", "followup"} or not str(change.get("body", "x")).strip():
            raise ActionError("A pitch can't be empty")
        body = change.get("body")
        if body is not None and "unsubscribe" not in body.lower():
            raise ActionError("Keep the footer: every email must say how to opt out")
    desk.approve(item_id, edits)
    item = board.transition(ctx, item_id, "PITCH_APPROVED", "user", ctx.user_id,
                            notes or ("Pitch approved with edits" if edits else "Pitch approved"))
    _record(board, ctx, item_id, "pitch", "approved", notes)
    return item


def reject_pitch(board: Board, ctx: OrgContext, item_id: str, notes: str):
    _gate_check(board, ctx, item_id, {"PITCH_DRAFTED"})
    if not notes.strip():
        raise ActionError("Say what to change, so Echo knows how to redraft it")
    _record(board, ctx, item_id, "pitch", "rejected", notes.strip())
    return board.transition(ctx, item_id, "PREVIEW_DEPLOYED", "user", ctx.user_id, f"Pitch sent back: {notes.strip()}")


def mark_replied(board: Board, ctx: OrgContext, item_id: str, notes: str | None = None):
    """For replies that arrive outside the connected mailbox (a phone call, another inbox)."""
    item = board.get(ctx, item_id)
    if item.status != "PITCHED":
        raise ActionError("Only a pitched lead can be marked as replied")
    if not ctx.can_edit:
        raise Forbidden("Viewers can't change leads")
    return board.transition(ctx, item_id, "REPLIED", "user", ctx.user_id, notes or "Replied (recorded by hand)")


def close_deal(board: Board, ctx: OrgContext, item_id: str, won: bool, notes: str | None = None):
    _gate_check(board, ctx, item_id, {"REPLIED"})
    item = board.transition(ctx, item_id, "WON" if won else "LOST", "user", ctx.user_id,
                            notes or ("Won" if won else "Lost"))
    _record(board, ctx, item_id, "reply", "won" if won else "lost", notes)
    return item


def suppress_lead(board: Board, desk, ctx: OrgContext, item_id: str, reason: str = "asked not to be contacted"):
    """Never email this lead's address again. A pitched lead is closed as LOST."""
    if not ctx.can_edit:
        raise Forbidden("Viewers can't change the suppression list")
    item = board.get(ctx, item_id)
    if not item.email:
        raise ActionError("This lead has no email address")
    desk.suppress(item.email, reason)
    if item.status == "PITCHED":
        board.transition(ctx, item_id, "LOST", "user", ctx.user_id, f"Suppressed: {reason}")
    else:
        board.log(ctx, item_id, "user", ctx.user_id, f"{item.email} suppressed: {reason}")
    return board.get(ctx, item_id)
