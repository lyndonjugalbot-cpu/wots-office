"""Dashboard API: approval queue actions, escalations, file serving and intake (spec §10)."""
import pytest
from fastapi.testclient import TestClient

from wots.dashboard.app import create_app

from .conftest import force_status, new_lead


@pytest.fixture
def client_rt(make_runtime):
    rt = make_runtime()
    return TestClient(create_app(rt)), rt


def test_overview_counts_and_pending(client_rt):
    client, rt = client_rt
    a, b = new_lead(rt), new_lead(rt, name="B")
    force_status(rt, a, "READY_FOR_APPROVAL")
    force_status(rt, b, "ESCALATED")
    data = client.get("/api/overview").json()
    assert data["pending"] == {"approvals": 1, "escalations": 1}
    assert data["counts"]["website"] == {"READY_FOR_APPROVAL": 1, "ESCALATED": 1}
    assert data["dry_run"] is True and data["spend_cap"] == 1.0
    ceo = next(p for p in client.get("/api/office").json()["agents"] if p["id"] == "ceo")
    assert ceo["status"] == "waiting" and "2 items" in ceo["bubble"]


def test_approve_reject_and_disqualify(client_rt):
    client, rt = client_rt
    ids = [new_lead(rt, name=f"L{i}") for i in range(3)]
    for i in ids:
        force_status(rt, i, "READY_FOR_APPROVAL", assigned_to="pixel")

    assert client.post(f"/api/leads/{ids[0]}/approve", json={}).json()["status"] == "APPROVED"
    assert client.post(f"/api/leads/{ids[1]}/reject", json={"notes": ""}).status_code == 409  # notes required
    assert client.post(f"/api/leads/{ids[1]}/reject", json={"notes": "Use their green"}).json()["status"] == "NEEDS_FIX"
    assert rt.board.latest_feedback(ids[1]) == "CEO notes: Use their green"
    out = client.post(f"/api/leads/{ids[2]}/disqualify", json={"reason": "has a website after all"}).json()
    assert out["status"] == "DISQUALIFIED" and out["disqualify_reason"] == "has a website after all"
    # Approving twice isn't allowed
    assert client.post(f"/api/leads/{ids[0]}/approve", json={}).status_code == 409
    detail = client.get(f"/api/leads/{ids[1]}").json()
    assert detail["approvals"][0]["decision"] == "rejected" and detail["events"][-1]["to"] == "NEEDS_FIX"


def test_escalation_offers_resume_and_send_back(client_rt):
    client, rt = client_rt
    errored = new_lead(rt)
    rt.board.transition(errored, "ESCALATED", "atlas", "quill failed 4 times")
    assert client.get(f"/api/leads/{errored}").json()["resume_status"] == "NEW"
    assert client.post(f"/api/leads/{errored}/resolve", json={"to_status": "NEW"}).json()["status"] == "NEW"

    fixes = new_lead(rt, name="Fix loop")
    force_status(rt, fixes, "NEEDS_FIX", assigned_to="nova", fix_count=3)
    rt.board.transition(fixes, "ESCALATED", "atlas")
    assert client.get(f"/api/leads/{fixes}").json()["resume_status"] is None
    out = client.post(f"/api/leads/{fixes}/resolve", json={"to_status": "BUILDING", "notes": "Simpler hero"}).json()
    assert out["status"] == "BUILDING" and out["fix_count"] == 0


def test_files_are_served_sandboxed_and_cannot_escape(client_rt):
    client, rt = client_rt
    lead_id = new_lead(rt)
    site = rt.config.settings.data_path / "leads" / str(lead_id) / "site"
    site.mkdir(parents=True)
    (site / "index.html").write_text("<h1>Hi</h1>")
    r = client.get(f"/api/leads/{lead_id}/files/site/index.html")
    assert r.status_code == 200 and "sandbox" in r.headers["content-security-policy"]
    for sneaky in ("../../wots.db", "%2e%2e/%2e%2e/wots.db", "site/../../../wots.db"):
        r = client.get(f"/api/leads/{lead_id}/files/{sneaky}")
        assert r.status_code in (404, 422) and b"SQLite" not in r.content  # never serves the database


def test_csv_upload_and_samples(client_rt):
    client, rt = client_rt
    csv = b"business_name,country\nA Plumber,US\nKiwi Ltd,NZ\n"
    out = client.post("/api/import", files={"file": ("x.csv", csv, "text/csv")}, data={"scope": "website"}).json()
    assert out["created"] == 1 and "NZ" in out["skipped"][0]
    assert client.post("/api/import-samples").json()["created"] == 4
    assert len(client.get("/api/leads?status=NEW").json()) == 5
