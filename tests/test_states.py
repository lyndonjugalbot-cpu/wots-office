"""Every allowed transition succeeds and writes an event; every other one is rejected (Phase 0 acceptance)."""
import pytest
from sqlalchemy import select

from wots.core.board import InvalidTransition
from wots.core.models import Event
from wots.core.states import TRANSITIONS, WORKING_STATUSES, Scope, Status, statuses_for

from .conftest import force_status, new_lead

ALLOWED = [(scope, f, t) for scope, table in TRANSITIONS.items() for (f, t) in table]
BLOCKED = [(scope, f, t) for scope in Scope for f in Status for t in Status if (f, t) not in TRANSITIONS[scope]]


@pytest.fixture
def rt(make_runtime):
    return make_runtime()


@pytest.mark.parametrize("scope", list(Scope))
def test_every_allowed_transition_succeeds_and_is_logged(rt, scope):
    lead_id = new_lead(rt, scope=scope.value)
    for (from_status, to_status), _ in TRANSITIONS[scope].items():
        force_status(rt, lead_id, from_status.value)
        lead = rt.board.transition(lead_id, to_status, "tester", f"{from_status}->{to_status}")
        assert lead.status == to_status.value
        with rt.sessions() as s:
            event = s.scalars(select(Event).where(Event.lead_id == lead_id).order_by(Event.id.desc())).first()
        assert (event.from_status, event.to_status, event.actor) == (from_status.value, to_status.value, "tester")
    assert len(ALLOWED) > 40


@pytest.mark.parametrize("scope", list(Scope))
def test_every_other_transition_is_blocked(rt, scope):
    lead_id = new_lead(rt, scope=scope.value)
    blocked = [(f, t) for s, f, t in BLOCKED if s == scope]
    for from_status, to_status in blocked:
        force_status(rt, lead_id, from_status.value)
        with pytest.raises(InvalidTransition):
            rt.board.transition(lead_id, to_status, "tester")
        assert rt.board.get(lead_id).status == from_status.value  # unchanged
    # e.g. nobody can skip QA or approve their own work
    assert (Status.BUILDING, Status.READY_FOR_APPROVAL) in blocked or scope == Scope.AD_REFRESH
    assert (Status.IN_QA, Status.APPROVED) in blocked


def test_scope_specific_statuses_are_rejected_in_the_other_scope(rt):
    web = new_lead(rt, scope="website")
    force_status(rt, web, "ASSIGNED")
    with pytest.raises(InvalidTransition):
        rt.board.transition(web, "DESIGNING", "tester")  # ad_refresh's work status
    ad = new_lead(rt, scope="ad_refresh")
    force_status(rt, ad, "ENRICHED")
    with pytest.raises(InvalidTransition):
        rt.board.transition(ad, "COPY_READY", "tester")  # website only
    assert Status.BRIEFED not in statuses_for(Scope.WEBSITE)


def test_error_escalation_can_resume_only_where_it_left_off(rt):
    lead_id = new_lead(rt)
    rt.board.transition(lead_id, "ESCALATED", "atlas", "Ledger failed 4 times")
    with pytest.raises(InvalidTransition):
        rt.board.transition(lead_id, "ENRICHED", "ceo")  # can't skip Ledger's work
    assert rt.board.transition(lead_id, "NEW", "ceo", "Retry after fixing the API key").status == "NEW"


def test_escalation_from_fix_loop_goes_back_to_building_not_needs_fix(rt):
    lead_id = new_lead(rt)
    force_status(rt, lead_id, "NEEDS_FIX", assigned_to="pixel", fix_count=3)
    rt.board.transition(lead_id, "ESCALATED", "atlas")
    with pytest.raises(InvalidTransition):
        rt.board.transition(lead_id, "NEEDS_FIX", "ceo")
    lead = rt.board.transition(lead_id, "BUILDING", "ceo", "Try a simpler layout")
    assert lead.fix_count == 0  # the CEO's send-back gives a fresh fix budget


def test_working_statuses_can_escalate(rt):
    for scope in Scope:
        for status in WORKING_STATUSES[scope]:
            assert (status, Status.ESCALATED) in TRANSITIONS[scope]


def test_transition_rejects_workflow_fields_in_updates(rt):
    lead_id = new_lead(rt)
    with pytest.raises(ValueError):
        rt.board.transition(lead_id, "ENRICHED", "ledger", updates={"status": "APPROVED"})
