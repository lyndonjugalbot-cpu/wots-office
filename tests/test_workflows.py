"""Workflows as data (spec v2 §6): every transition in the YAML works, everything else is blocked,
and broken definitions or under-staffed offices are refused with a clear reason."""
import pytest
import yaml

from wots.core.board import InvalidTransition
from wots.core.catalogue import Catalogue
from wots.orchestration.workflow_loader import WORKFLOWS_DIR, parse_workflow
from wots.orchestration.workflow_validator import activation_problems, validate_workflow

from .conftest import force_status, internal, new_item

WORKFLOWS = ["website", "ad_refresh"]


@pytest.fixture
def rt(make_runtime):
    return make_runtime()


def test_the_catalogue_is_valid():
    catalogue = Catalogue.load()
    catalogue.validate()  # raises WorkflowError on any problem
    assert set(catalogue.workflows) == set(WORKFLOWS)
    assert set(catalogue.templates) == {"web_agency", "ad_agency"}
    assert catalogue.types["cold_email"].risk_level == "high"
    assert not catalogue.types["content_creator"].hireable  # planned, not yet available


@pytest.mark.parametrize("key", WORKFLOWS)
def test_every_allowed_transition_succeeds_and_is_logged(rt, key):
    ctx = internal(rt)
    wf = rt.catalogue.workflows[key]
    for a, b in sorted(wf.transitions):
        item = new_item(rt, workflow=key)
        force_status(rt, ctx, item, a)
        assert rt.board.transition(ctx, item, b, "system", "test").status == b
        last = rt.board.history(ctx, item)[-1]
        assert (last.from_status, last.to_status, last.actor_kind) == (a, b, "system")


@pytest.mark.parametrize("key", WORKFLOWS)
def test_every_other_transition_is_blocked(rt, key):
    ctx = internal(rt)
    wf = rt.catalogue.workflows[key]
    item = new_item(rt, workflow=key)
    blocked = 0
    for a in wf.states:
        for b in wf.states:
            if wf.is_allowed(a, b) or a == wf.escalate_to:
                continue  # escalations have their own resume rule, tested below
            force_status(rt, ctx, item, a)
            with pytest.raises(InvalidTransition):
                rt.board.transition(ctx, item, b, "system", "test")
            blocked += 1
    assert blocked > len(wf.transitions)


def test_states_from_another_workflow_are_rejected(rt):
    ctx = internal(rt)
    item = new_item(rt, workflow="ad_refresh")
    with pytest.raises(InvalidTransition):
        rt.board.transition(ctx, item, "ENRICHED", "system", "test")  # a website state


def test_employee_states_can_always_escalate(rt):
    ctx = internal(rt)
    wf = rt.catalogue.workflows["website"]
    for state in [s for s in wf.states.values() if s.employee_owned]:
        item = new_item(rt)
        force_status(rt, ctx, item, state.name)
        assert rt.board.transition(ctx, item, "ESCALATED", "system", "atlas").status == "ESCALATED"


def test_an_error_escalation_resumes_only_where_it_stopped(rt):
    ctx = internal(rt)
    item = new_item(rt)
    force_status(rt, ctx, item, "ENRICHED")
    rt.board.transition(ctx, item, "ESCALATED", "system", "atlas", "Quill failed 4 times")
    with pytest.raises(InvalidTransition):
        rt.board.transition(ctx, item, "IN_QA", "user", "u")  # somewhere it never was
    assert rt.board.transition(ctx, item, "ENRICHED", "user", "u").status == "ENRICHED"


def test_a_fix_loop_escalation_goes_back_to_building_not_needs_fix(rt):
    ctx = internal(rt)
    item = new_item(rt)
    force_status(rt, ctx, item, "NEEDS_FIX", fix_count=3)
    rt.board.transition(ctx, item, "ESCALATED", "system", "atlas")
    with pytest.raises(InvalidTransition):
        rt.board.transition(ctx, item, "NEEDS_FIX", "user", "u")
    back = rt.board.transition(ctx, item, "BUILDING", "user", "u", "Try a simpler layout")
    assert back.status == "BUILDING" and back.fix_count == 0  # a fresh fix budget


def test_entering_needs_fix_counts_a_fix(rt):
    ctx = internal(rt)
    item = new_item(rt)
    force_status(rt, ctx, item, "IN_QA")
    assert rt.board.transition(ctx, item, "NEEDS_FIX", "employee", "hawk").fix_count == 1


def test_transitions_refuse_unknown_fields(rt):
    ctx = internal(rt)
    item = new_item(rt)
    with pytest.raises(ValueError):
        rt.board.transition(ctx, item, "VERIFY", "system", "test", updates={"status": "APPROVED"})


def _website_raw():
    return yaml.safe_load((WORKFLOWS_DIR / "website.yaml").read_text())


@pytest.mark.parametrize("breakage,expected", [
    (lambda r: r["transitions"].remove(["IN_QA", "READY_FOR_APPROVAL"]), "can't be reached"),
    (lambda r: r["states"].__setitem__("VERIFY", {"owner": {"type": "time_traveller"}}), "unknown employee type"),
    (lambda r: r["transitions"].append(["ASSETS_READY", "DISQUALIFIED"]), "exactly one next state"),
    (lambda r: r["transitions"].append(["APPROVED_X", "NEW"]), "undefined state"),
    (lambda r: r["states"]["COPY_READY"].__setitem__("skip_to", "APPROVED"), "skip_if_missing"),
    (lambda r: r["rules"]["fix_loop"].__setitem__("escalate_to", "NOWHERE"), "fix_loop.escalate_to"),
])
def test_broken_workflows_are_refused(breakage, expected):
    raw = _website_raw()
    breakage(raw)
    errors = validate_workflow(parse_workflow(raw), set(Catalogue.load().types))
    assert any(expected in e for e in errors), errors


def test_activation_names_the_missing_employee():
    catalogue = Catalogue.load()
    wf = catalogue.workflows["website"]
    names = catalogue.type_names()
    everyone = set(wf.required_types())
    assert activation_problems(wf, everyone, names) == []
    problems = activation_problems(wf, everyone - {"qa_tester"}, names)
    assert problems == ["The website workflow needs a QA Tester (for IN_QA). Hire one or pick a different template."]
    # graphic_designer is skip_if_missing, so it's never required
    assert "graphic_designer" not in everyone
    assert activation_problems(wf, everyone - {"cold_email"}, names, allow_missing={"cold_email"}) == []
