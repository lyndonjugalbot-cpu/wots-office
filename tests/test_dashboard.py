"""The dashboard API (spec v2 §12): sign-in, office scoping, roles at the gates, files and intake."""
import pytest
from fastapi.testclient import TestClient

from wots.core import auth
from wots.dashboard.app import create_app
from wots.employees.base import EmployeeResult

from .conftest import employee_id, fake_team, force_status, internal, new_item


def signed_in(rt, email=None):
    client = TestClient(create_app(rt))
    url = auth.login_link(rt, email, "http://testserver")
    response = client.get(url.replace("http://testserver", ""), follow_redirects=False)
    assert response.status_code == 303 and auth.COOKIE in response.cookies
    return client


@pytest.fixture
def rt(make_runtime):
    return make_runtime()


def test_everything_needs_sign_in(rt):
    client = TestClient(create_app(rt))
    for path in ("/api/me", "/api/overview", "/api/items", "/api/office", "/api/team"):
        assert client.get(path).status_code == 401
    assert client.get("/auth/login?token=forged.abc", follow_redirects=False).status_code == 401
    session = auth.session_cookie(rt, auth.default_user_id(rt))
    assert client.get("/auth/login", params={"token": session}, follow_redirects=False).status_code == 401  # wrong purpose


def test_me_overview_and_office(rt):
    client = signed_in(rt)
    me = client.get("/api/me").json()
    assert [o["slug"] for o in me["offices"]] == ["wots-office"] and set(me["offices"][0]["roles"]) == {"owner", "ceo"}
    new_item(rt)
    overview = client.get("/api/overview").json()
    assert overview["counts"] == {"website": {"NEW": 1}} and overview["credits"] is None
    assert {w["key"]: w["waiting_for"] for w in overview["workflows"]}["website"] == ["cold_email"]
    people = client.get("/api/office").json()["agents"]
    kinds = {p["name"]: p["kind"] for p in people}
    assert kinds["Atlas"] == "atlas" and kinds["Hawk"] == "qa" and kinds["Pixel"] == "worker"
    assert next(p for p in people if p["kind"] == "ceo")["name"]


def test_approve_reject_and_disqualify(rt):
    client = signed_in(rt)
    ctx = internal(rt)
    a, b, c = (new_item(rt, name=n) for n in ("A", "B", "C"))
    for item in (a, b, c):
        force_status(rt, ctx, item, "READY_FOR_APPROVAL", assigned_employee_id=employee_id(rt, ctx, "Pixel"))
    assert client.post(f"/api/items/{a}/approve", json={}).json()["status"] == "APPROVED"
    assert client.post(f"/api/items/{a}/approve", json={}).status_code == 409  # already decided
    assert client.post(f"/api/items/{b}/reject", json={"notes": ""}).status_code == 409
    rejected = client.post(f"/api/items/{b}/reject", json={"notes": "Mention Portland"}).json()
    assert rejected["status"] == "NEEDS_FIX" and rejected["fix_count"] == 1 and rejected["assigned_to"] == "Pixel"
    assert rt.board.latest_feedback(ctx, b) == "CEO notes: Mention Portland"
    assert client.post(f"/api/items/{c}/disqualify", json={"reason": "closed down"}).json()["status"] == "DISQUALIFIED"
    detail = client.get(f"/api/items/{a}").json()
    assert detail["approvals"][0]["decision"] == "approved" and detail["events"][-1]["actor"] == "You"


def test_escalations_offer_resume_and_send_back(rt):
    client = signed_in(rt)
    ctx = internal(rt)
    item = new_item(rt)
    force_status(rt, ctx, item, "ENRICHED")
    rt.board.transition(ctx, item, "ESCALATED", "system", "atlas", "Quill failed 4 times")
    detail = client.get(f"/api/items/{item}").json()
    assert detail["resume_status"] == "ENRICHED" and detail["can_decide"]
    assert client.post(f"/api/items/{item}/resolve", json={"to_status": "IN_QA"}).status_code == 409
    assert client.post(f"/api/items/{item}/resolve", json={"to_status": "ENRICHED"}).json()["status"] == "ENRICHED"


def test_viewers_cant_decide_and_other_offices_cant_see(rt):
    ctx = internal(rt)
    viewer = rt.offices.ensure_user("viewer@wots.test")
    rt.offices.add_member(ctx, viewer.id, "viewer")
    item = new_item(rt)
    force_status(rt, ctx, item, "READY_FOR_APPROVAL")
    client = signed_in(rt, "viewer@wots.test")
    assert client.get(f"/api/items/{item}").json()["can_decide"] is False
    assert client.post(f"/api/items/{item}/approve", json={}).status_code == 403
    assert client.post("/api/import-samples").status_code == 403
    assert client.post("/api/team/hire", json={"type": "web_developer", "name": "Ada"}).status_code == 403

    rival = rt.offices.create_office("Rival", templates=["web_agency"], ceo_email="ceo@rival.test")
    fake_team(rt, rival)
    theirs = signed_in(rt, "ceo@rival.test")
    assert theirs.get(f"/api/items/{item}").status_code == 404
    assert theirs.post(f"/api/items/{item}/approve", json={}).status_code == 404
    assert theirs.get("/api/items").json() == []
    assert theirs.get("/api/overview", headers={"X-Org": "wots-office"}).status_code == 403
    assert theirs.get(f"/api/items/{item}/files/copy.json").status_code == 404


def test_the_office_switcher(rt):
    ctx = internal(rt)
    owner = auth.default_user_id(rt)
    second = rt.offices.create_office("Second", templates=["ad_agency"], ceo_email="lead@second.test")
    rt.offices.add_member(second, owner, "manager")
    new_item(rt, ctx=second, workflow="ad_refresh")
    client = signed_in(rt)
    assert {o["slug"] for o in client.get("/api/me").json()["offices"]} == {"wots-office", "second"}
    assert client.get("/api/items", headers={"X-Org": "second"}).json()[0]["workflow_key"] == "ad_refresh"
    assert client.get("/api/items").json() == []  # the default office is the first membership
    assert ctx.slug == "wots-office"


def test_files_are_served_sandboxed_and_cannot_escape(rt):
    client = signed_in(rt)
    ctx = internal(rt)
    item = new_item(rt)
    site = rt.files.item_dir(ctx.org_id, item) / "site"
    site.mkdir()
    (site / "index.html").write_text("<h1>hi</h1>")
    response = client.get(f"/api/items/{item}/files/site/index.html")
    assert response.status_code == 200 and "sandbox" in response.headers["content-security-policy"]
    assert client.get(f"/api/items/{item}/files/../../../../wots.db").status_code == 404
    assert client.get(f"/api/items/{item}/files/%2e%2e/%2e%2e/secret").status_code == 404
    base = client.get(f"/api/items/{item}").json()["files_base"]
    anonymous = TestClient(create_app(rt))  # a sandboxed preview sends no cookie
    assert anonymous.get(base + "site/index.html").status_code == 200
    assert anonymous.get(base + "../../wots.db").status_code == 404
    assert anonymous.get(base.replace("/api/files/", "/api/files/x") + "site/index.html").status_code == 404
    forged = auth.sign(rt, {"purpose": "session", "uid": "x", "exp": 9999999999})
    assert anonymous.get(f"/api/files/{forged}/site/index.html").status_code == 404


def test_csv_upload_samples_and_team(rt):
    client = signed_in(rt)
    upload = client.post("/api/import", files={"file": ("x.csv", b"business_name,country\nJoe,US\n,US\n")},
                         data={"workflow": "website"})
    assert upload.json() == {"created": 1, "skipped": ["line 3: no business_name"]}
    assert client.post("/api/import-samples").json()["created"] == 4
    assert client.post("/api/import", files={"file": ("x.csv", b"name\nJoe\n")}).status_code == 400

    team = client.get("/api/team").json()
    assert team["can_manage"] and "Pixel" in {m["name"] for m in team["members"]}
    assert client.post("/api/team/hire", json={"type": "web_developer", "name": "Ada",
                                               "config": {"style_profile": "playful"}}).status_code == 200
    assert client.post("/api/team/hire", json={"type": "cold_email", "name": "Echo"}).status_code == 400
    ada = next(m for m in client.get("/api/team").json()["members"] if m["name"] == "Ada")
    assert client.post(f"/api/team/{ada['id']}/fire").json() == {"ok": True}


def test_tick_runs_only_the_current_office(rt):
    client = signed_in(rt)
    rival = rt.offices.create_office("Rival", templates=["web_agency"], ceo_email="ceo@rival.test")
    fake_team(rt, rival)
    scout = rt.atlas.impl_overrides[employee_id(rt, rival, "Scout")]
    scout.behaviour = lambda item, ctx: EmployeeResult("VERIFY")
    theirs = new_item(rt, ctx=rival)
    assert client.post("/api/tick").json()["ran"]
    assert rt.board.get(rival, theirs).status == "NEW"  # the signed-in office was ticked, not the rival
    rt.atlas.tick("rival")
    assert rt.board.get(rival, theirs).status == "VERIFY"


def test_research_from_the_dashboard(rt, web):
    from .fakeweb import osm_element

    web.osm["portland"] = [osm_element(i, f"Portland Pipes {i}") for i in range(5)]
    client = signed_in(rt)
    trades = client.get("/api/trades").json()
    assert {"key": "plumber", "label": "Plumbers", "uk_registry": True} in trades["trades"]
    body = {"country": "US", "trade": "plumber", "regions": ["Portland"], "limit": 3}
    result = client.post("/api/research", json=body).json()
    assert result["created"] == 3 and result["requests"] == 2
    assert client.get("/api/overview").json()["research"]["osm"]["requests_today"] == 2
    assert client.post("/api/research", json={**body, "country": "NZ"}).status_code == 400
    assert client.post("/api/research", json={**body, "source": "places"}).status_code == 400  # switched off
