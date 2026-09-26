"""Offices, members, hiring and workflow activation (spec v2 §3, §5, §6)."""
import pytest
from sqlalchemy import select

from wots.core.models import AuditLog
from wots.core.offices import OfficeError
from wots.core.repo import scoped

from .conftest import employee_id, internal


def test_the_migration_seeds_the_internal_office(make_runtime):
    rt = make_runtime()
    ctx = internal(rt)
    assert ctx.is_internal and ctx.name == "Wots Office"
    team = {e.name: e.type_key for e in rt.offices.employees(ctx)}
    assert team == {"Scout": "lead_researcher", "Ledger": "data_verifier", "Quill": "copywriter",
                    "Iris": "graphic_designer", "Pixel": "web_developer", "Nova": "web_developer", "Hawk": "qa_tester",
                    "Dock": "deployment", "Scout-Ads": "ad_researcher", "Lens": "creative_strategist",
                    "Juno": "graphic_designer"}
    flows = {w.workflow_key: w for w in rt.offices.workflows(ctx)}
    assert set(flows) == {"website", "ad_refresh"} and all(w.active for w in flows.values())
    assert flows["website"].settings["allow_missing"] == ["cold_email"]  # Echo waits for the outreach terms


def test_create_an_office_from_templates(make_runtime):
    rt = make_runtime()
    ctx = rt.offices.create_office("Kiwi Web Co", templates=["web_agency", "ad_agency"], ceo_email="Boss@Kiwi.test",
                                   owner_email="owner@kiwi.test")
    assert ctx.slug == "kiwi-web-co" and not ctx.is_internal
    names = sorted(e.name for e in rt.offices.employees(ctx))
    assert "Echo" not in names  # high risk: not until the owner accepts the outreach terms
    assert names.count("Hawk") == 1  # shared types are hired once across templates
    website = next(w for w in rt.offices.workflows(ctx) if w.workflow_key == "website")
    assert website.settings["allow_missing"] == ["cold_email"] and "outreach terms" in website.settings["missing_reason"]
    ceo = rt.offices.user("boss@kiwi.test")
    assert [(o.slug, roles) for o, roles in rt.offices.memberships(ceo.id)] == [("kiwi-web-co", {"ceo"})]
    with pytest.raises(OfficeError):
        rt.offices.create_office("Kiwi Web Co", templates=["web_agency"], ceo_email="x@y.test")


def test_high_risk_types_need_the_outreach_terms(make_runtime):
    rt = make_runtime()
    ctx = internal(rt)
    with pytest.raises(OfficeError, match="outreach terms"):
        rt.offices.hire(ctx, "cold_email", "Echo")
    with pytest.raises(OfficeError):
        rt.offices.accept_outreach_terms(ctx, "  ")
    rt.offices.accept_outreach_terms(ctx, "1 Queen St, Auckland")
    echo = rt.offices.hire(ctx, "cold_email", "Echo")
    assert echo.type_key == "cold_email"
    website = next(w for w in rt.offices.workflows(ctx) if w.workflow_key == "website")
    assert website.settings == {"allow_missing": []}  # hiring Echo cleared the note


def test_hiring_validates_config_and_names(make_runtime):
    rt = make_runtime()
    ctx = internal(rt)
    with pytest.raises(OfficeError):
        rt.offices.hire(ctx, "web_developer", "Pixel")  # name taken
    with pytest.raises(OfficeError):
        rt.offices.hire(ctx, "web_developer", "Ada", {"style_profile": "vaporwave"})
    with pytest.raises(OfficeError):
        rt.offices.hire(ctx, "web_developer", "Ada", {"max_wip": 9})
    with pytest.raises(OfficeError):
        rt.offices.hire(ctx, "web_developer", "Ada", {"effort": "extreme"})
    with pytest.raises(OfficeError):
        rt.offices.hire(ctx, "content_creator", "Reel")  # planned, not yet available
    with pytest.raises(OfficeError):
        rt.offices.hire(ctx, "time_traveller", "Doc")
    ada = rt.offices.hire(ctx, "web_developer", "Ada", {"style_profile": "minimal", "wip_mode": "rolling"})
    assert ada.config == {"style_profile": "minimal", "max_wip": 2, "wip_mode": "rolling"}


def test_update_and_fire_are_audited(make_runtime):
    rt = make_runtime()
    ctx = internal(rt)
    pixel = employee_id(rt, ctx, "Pixel")
    assert rt.offices.update_employee(ctx, pixel, {"effort": "low"}).config["effort"] == "low"
    assert "effort" not in rt.offices.update_employee(ctx, pixel, {"effort": None}).config
    rt.offices.fire(ctx, pixel)
    assert "Pixel" not in {e.name for e in rt.offices.employees(ctx)}
    with rt.sessions() as s:
        actions = [a.action for a in s.scalars(scoped(AuditLog, ctx).order_by(AuditLog.ts))]
    assert actions[-3:] == ["employee.update", "employee.update", "employee.fire"]


def test_activation_fails_when_a_needed_type_is_missing(make_runtime):
    rt = make_runtime()
    ctx = internal(rt)
    rt.offices.fire(ctx, employee_id(rt, ctx, "Hawk"))
    with pytest.raises(OfficeError, match="needs a QA Tester"):
        rt.offices.activate_workflow(ctx, "website", allow_missing={"cold_email"})


def test_roles(make_runtime):
    from .conftest import as_user

    rt = make_runtime()
    ctx = internal(rt)
    ceo = as_user(rt, ctx, "ceo")
    assert ceo.can_pass_gate("ceo") and ceo.can_manage_team
    viewer = rt.offices.ensure_user("viewer@wots.test")
    manager = rt.offices.ensure_user("manager@wots.test")
    rt.offices.add_member(ctx, viewer.id, "viewer")
    rt.offices.add_member(ctx, manager.id, "manager", delegated=True)
    v = rt.offices.user_ctx(viewer.id, ctx.slug)
    m = rt.offices.user_ctx(manager.id, ctx.slug)
    assert not v.can_pass_gate("ceo") and not v.can_edit and not v.can_manage_team
    assert m.can_pass_gate("ceo") and m.can_edit and not m.can_manage_team
    assert not ctx.can_pass_gate("ceo")  # Atlas never passes a human gate
    with rt.sessions() as s:
        assert s.scalars(select(AuditLog.action).where(AuditLog.org_id == ctx.org_id, AuditLog.action == "member.add")).all()
