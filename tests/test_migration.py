"""The v1 -> v2 migration (spec v2 §15): a v1 database with a little of everything is upgraded,
and every row lands in the internal office with the right mapping."""
from datetime import datetime

import pytest
from sqlalchemy import create_engine, select, text

from wots.core.db import upgrade
from wots.core.models import (Approval, Artifact, Employee, Event, LeadProfile, Membership, Organization, QAReport,
                              Suppression, UsageEvent, User, WorkItem)
from wots.core.repo import install_query_guard

T = datetime(2026, 9, 20, 3, 0)


@pytest.fixture
def v1_db(tmp_path, monkeypatch):
    monkeypatch.setenv("WOTS_OWNER_EMAIL", "owner@wots.test")
    monkeypatch.setenv("WOTS_OWNER_NAME", "Owner")
    url = f"sqlite:///{tmp_path / 'wots.db'}"
    data = tmp_path / "data"
    upgrade(url, data, revision="0001")
    engine = create_engine(url)
    with engine.begin() as c:
        lead = ("INSERT INTO leads (id, scope, status, business_name, country, social_links, fix_count, assigned_to, "
                "claimed_by, phone, source, created_at, updated_at) VALUES (:id, :scope, :status, :name, 'US', '[]', "
                ":fix, :assigned, :claimed, '(503) 555-0147', 'csv:samples', :t, :t)")
        c.execute(text(lead), dict(id=1, scope="website", status="IN_QA", name="Harbour Line", fix=1, assigned="pixel",
                                   claimed=None, t=T))
        c.execute(text(lead), dict(id=2, scope="website", status="NEW", name="Claimed Mid-run", fix=0, assigned=None,
                                   claimed="ledger", t=T))
        c.execute(text(lead), dict(id=3, scope="ad_refresh", status="NEW", name="Ad Lead", fix=0, assigned=None,
                                   claimed=None, t=T))
        c.execute(text("INSERT INTO artifacts (id, lead_id, kind, path, version, created_by, created_at) "
                       "VALUES (1, 1, 'site', 'site/index.html', 2, 'pixel', :t)"), dict(t=T))
        c.execute(text("INSERT INTO qa_reports (id, lead_id, artifact_version, passed, issues, created_at) "
                       "VALUES (1, 1, 1, 0, '[{\"severity\": \"high\", \"description\": \"todo\"}]', :t)"), dict(t=T))
        c.execute(text("INSERT INTO approvals (id, lead_id, kind, decision, notes, decided_at) "
                       "VALUES (1, 1, 'build', 'rejected', 'Mention Portland', :t)"), dict(t=T))
        for i, (actor, frm, to) in enumerate([("import", None, "NEW"), ("ledger", "NEW", "ENRICHED"),
                                              ("ceo", "READY_FOR_APPROVAL", "NEEDS_FIX"), ("atlas", "ASSIGNED", "BUILDING")], 1):
            c.execute(text("INSERT INTO events (id, lead_id, from_status, to_status, actor, note, ts) "
                           "VALUES (:i, 1, :f, :to, :a, 'n', :t)"), dict(i=i, f=frm, to=to, a=actor, t=T))
        c.execute(text("INSERT INTO llm_usage (id, agent, model, input_tokens, output_tokens, cost_usd, lead_id, ts) "
                       "VALUES (1, 'quill', 'claude-opus-5', 1000, 2000, 0.055, 1, :t)"), dict(t=T))
        c.execute(text("INSERT INTO suppression (id, email, reason, added_at) VALUES (1, 'no@x.test', 'unsub', :t)"),
                  dict(t=T))
    engine.dispose()
    site = data / "leads" / "1" / "site"
    site.mkdir(parents=True)
    (site / "index.html").write_text("<h1>Harbour</h1>")
    upgrade(url, data)
    install_query_guard()
    return create_engine(url), data


def test_v1_data_moves_into_the_internal_office(v1_db):
    from sqlalchemy.orm import Session

    engine, data = v1_db
    with Session(engine) as s:
        cross = {"cross_org": True}
        org = s.scalars(select(Organization)).one()
        assert (org.slug, org.is_internal, org.plan_key) == ("wots-office", True, "internal")
        owner = s.scalars(select(User)).one()
        assert owner.email == "owner@wots.test"
        roles = {m.role for m in s.scalars(select(Membership).execution_options(**cross))}
        assert roles == {"owner", "ceo"}
        staff = {e.name: e for e in s.scalars(select(Employee).execution_options(**cross))}
        assert len(staff) == 11 and "Echo" not in staff

        items = {p.business_name: (w, p) for w, p in s.execute(
            select(WorkItem, LeadProfile).join(LeadProfile, LeadProfile.work_item_id == WorkItem.id)
            .execution_options(**cross))}
        harbour, profile = items["Harbour Line"]
        assert (harbour.workflow_key, harbour.status, harbour.fix_count) == ("website", "IN_QA", 1)
        assert harbour.assigned_employee_id == staff["Pixel"].id and harbour.org_id == org.id
        assert profile.phone == "(503) 555-0147" and profile.source == "csv:samples"
        claimed, _ = items["Claimed Mid-run"]
        assert claimed.status == "VERIFY" and claimed.claimed_by is None  # a v1 claim at NEW = verification started
        assert items["Ad Lead"][0].workflow_key == "ad_refresh"

        art = s.scalars(select(Artifact).execution_options(**cross)).one()
        assert (art.work_item_id, art.version, art.created_by_employee_id) == (harbour.id, 2, staff["Pixel"].id)
        assert s.scalars(select(QAReport).execution_options(**cross)).one().issues[0]["description"] == "todo"
        approval = s.scalars(select(Approval).execution_options(**cross)).one()
        assert approval.decided_by_user_id == owner.id and approval.notes == "Mention Portland"

        events = list(s.scalars(select(Event).where(Event.work_item_id == harbour.id).order_by(Event.seq)
                                .execution_options(**cross)))
        assert [(e.actor_kind, e.actor_id) for e in events] == [
            ("system", "import"), ("employee", staff["Ledger"].id), ("user", owner.id), ("system", "atlas")]
        assert [e.seq for e in events] == sorted(e.seq for e in events)

        usage = s.scalars(select(UsageEvent).execution_options(**cross)).one()
        assert (usage.employee_id, usage.work_item_id) == (staff["Quill"].id, harbour.id)
        assert usage.credits == pytest.approx(5.5)
        assert s.scalars(select(Suppression).execution_options(**cross)).one().org_id == org.id
        leftovers = [r[0] for r in s.execute(text("SELECT name FROM sqlite_master WHERE name LIKE 'v1_%'"))]
        assert leftovers == []

    moved = data / "orgs" / org.id / "items" / harbour.id / "site" / "index.html"
    assert moved.read_text() == "<h1>Harbour</h1>"
    assert not (data / "leads" / "1").exists()


def test_the_migrated_office_runs(v1_db, tmp_path, clock):
    """After migrating, the normal runtime opens the same database and carries on."""
    from wots.core.runtime import build_runtime

    from .conftest import config_for

    engine, data = v1_db
    engine.dispose()
    rt = build_runtime(config_for(tmp_path), clock=clock)
    ctx = rt.offices.system_ctx("wots-office")
    assert {i.business_name for i in rt.board.items(ctx)} == {"Harbour Line", "Claimed Mid-run", "Ad Lead"}
    harbour = next(i for i in rt.board.items(ctx) if i.business_name == "Harbour Line")
    assert rt.files.resolve(ctx.org_id, harbour.id, "site/index.html").is_file()
    assert rt.board.latest_feedback(ctx, harbour.id) == "CEO notes: Mention Portland"
