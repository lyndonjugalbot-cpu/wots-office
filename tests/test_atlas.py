"""Atlas rules: WIP (batch vs rolling), even split, fix routing and escalation, leases, retries (Phase 0 acceptance)."""
from sqlalchemy import func, select

from wots.agents.base import AgentResult
from wots.core.models import Event, Lead, QAReport
from wots.core.states import ACTIVE_STATUSES

from .conftest import FakeAgent, force_status, new_lead, web_designer


def assigned(rt, designer):
    with rt.sessions() as s:
        return sorted(s.scalars(select(Lead.id).where(Lead.assigned_to == designer)))


def ready_leads(rt, n):
    ids = [new_lead(rt, name=f"Biz {i}") for i in range(n)]
    for i in ids:
        force_status(rt, i, "ASSETS_READY")
    return ids


# ---------------------------------------------------------------- WIP


def test_batch_mode_waits_until_every_lead_is_approved(make_runtime):
    rt = make_runtime([web_designer("pixel")], wip={"wip_mode": "batch"})
    ids = ready_leads(rt, 3)
    rt.atlas.tick()
    first_two = assigned(rt, "pixel")
    assert first_two == ids[:2]  # a free designer gets a full batch of 2

    force_status(rt, ids[0], "APPROVED")
    rt.atlas.tick()
    assert assigned(rt, "pixel") == first_two  # one still active: batch mode gives nothing new

    force_status(rt, ids[1], "PREVIEW_DEPLOYED")  # past APPROVED counts as done
    rt.atlas.tick()
    assert assigned(rt, "pixel") == ids


def test_rolling_mode_refills_as_soon_as_a_slot_frees(make_runtime):
    rt = make_runtime([web_designer("pixel")], wip={"wip_mode": "rolling"})
    ids = ready_leads(rt, 3)
    rt.atlas.tick()
    assert assigned(rt, "pixel") == ids[:2]
    force_status(rt, ids[0], "APPROVED")
    rt.atlas.tick()
    assert assigned(rt, "pixel") == ids


def test_even_split_between_free_designers(make_runtime):
    rt = make_runtime([web_designer("pixel"), web_designer("nova")])
    ids = ready_leads(rt, 4)
    report = rt.atlas.tick()
    assert report.assigned == {"pixel": 2, "nova": 2}
    assert assigned(rt, "nova") == [ids[0], ids[2]] or assigned(rt, "pixel") == [ids[0], ids[2]]


def test_fewer_leads_this_week_wins_when_both_are_free(make_runtime, clock):
    rt = make_runtime([web_designer("pixel"), web_designer("nova")])
    # Pixel already did 3 leads this week (all approved, so Pixel is free again)
    for i in range(3):
        lead_id = new_lead(rt, name=f"Old {i}")
        force_status(rt, lead_id, "ASSETS_READY")
        rt.board.transition(lead_id, "ASSIGNED", "atlas", updates={"assigned_to": "pixel"})
        force_status(rt, lead_id, "APPROVED")
    new_id = ready_leads(rt, 1)[0]
    rt.atlas.tick()
    assert rt.board.get(new_id).assigned_to == "nova"

    clock.advance(days=8)  # past the fairness window, last week's leads no longer count
    force_status(rt, new_id, "APPROVED")
    newer = ready_leads(rt, 2)
    rt.atlas.tick()
    assert {rt.board.get(i).assigned_to for i in newer} == {"pixel", "nova"}  # back to an even split


def test_designers_never_exceed_wip_over_many_ticks(make_runtime):
    finished = []

    def work(lead, ctx):  # designers finish instantly; a fake Hawk alternates pass and fail
        return AgentResult("IN_QA")

    def qa(lead, ctx):
        finished.append(lead.id)
        return AgentResult("READY_FOR_APPROVAL" if len(finished) % 2 else "NEEDS_FIX", "QA")

    hawk = FakeAgent("hawk", scope=["website"], owns={"website": "IN_QA"}, uses_llm=True, behaviour=qa)
    rt = make_runtime([web_designer("pixel", work), web_designer("nova", work), hawk])
    ids = ready_leads(rt, 12)
    for step in range(30):
        rt.atlas.tick()
        with rt.sessions() as s:
            for d in ("pixel", "nova"):
                active = s.scalar(select(func.count()).select_from(Lead).where(
                    Lead.assigned_to == d, Lead.status.in_([st.value for st in ACTIVE_STATUSES])))
                assert active <= 2, f"{d} has {active} active leads at tick {step}"
        # The CEO approves whatever is waiting, which frees the designers
        with rt.sessions() as s:
            waiting = list(s.scalars(select(Lead.id).where(Lead.status == "READY_FOR_APPROVAL")))
        for lead_id in waiting:
            rt.board.transition(lead_id, "APPROVED", "ceo")
    with rt.sessions() as s:
        approved = s.scalar(select(func.count()).select_from(Lead).where(Lead.status == "APPROVED"))
    assert approved >= 10 and len(ids) == 12


# ---------------------------------------------------------------- fix routing


def test_needs_fix_goes_back_to_same_designer_with_feedback_then_escalates(make_runtime):
    pixel = web_designer("pixel", lambda lead, ctx: AgentResult("IN_QA"))
    nova = web_designer("nova")
    rt = make_runtime([pixel, nova])
    lead_id = ready_leads(rt, 1)[0]
    rt.atlas.tick()  # assigned to one of them; make it Pixel's for a clear story
    force_status(rt, lead_id, "IN_QA", assigned_to="pixel")
    nova_calls = len(nova.calls)

    for attempt in (1, 2):
        with rt.sessions.begin() as s:
            s.add(QAReport(lead_id=lead_id, artifact_version=attempt, passed=False,
                           issues=[{"severity": "high", "description": f"Phone number mismatch #{attempt}"}]))
        rt.board.transition(lead_id, "NEEDS_FIX", "hawk", "QA failed")
        rt.atlas.tick()  # routes the fix and Pixel works it straight away
        lead = rt.board.get(lead_id)
        assert (lead.status, lead.assigned_to, lead.fix_count) == ("IN_QA", "pixel", attempt)
        assert f"Phone number mismatch #{attempt}" in pixel.calls[-1][2]  # feedback reached the designer

    rt.board.transition(lead_id, "NEEDS_FIX", "hawk", "QA failed again")
    report = rt.atlas.tick()
    lead = rt.board.get(lead_id)
    assert (lead.status, lead.fix_count, report.escalated) == ("ESCALATED", 3, 1)
    assert any("ESCALATED" in m for m in rt.notifier.sent)
    assert len(nova.calls) == nova_calls  # fixes never go to the other designer


def test_ceo_notes_beat_older_qa_reports(make_runtime, clock):
    from wots.core.models import Approval

    rt = make_runtime()
    lead_id = new_lead(rt)
    with rt.sessions.begin() as s:
        s.add(QAReport(lead_id=lead_id, artifact_version=1, passed=False, issues=[{"severity": "low", "description": "old"}],
                       created_at=clock()))
    clock.advance(minutes=5)
    with rt.sessions.begin() as s:
        s.add(Approval(lead_id=lead_id, kind="build", decision="reject", notes="Use their brand green", decided_at=clock()))
    assert rt.board.latest_feedback(lead_id) == "CEO notes: Use their brand green"


# ---------------------------------------------------------------- leases


def test_lease_blocks_others_until_it_expires(make_runtime, clock):
    rt = make_runtime()
    lead_id = new_lead(rt)
    assert rt.board.claim(lead_id, "ledger-1", seconds=600)
    assert not rt.board.claim(lead_id, "ledger-2")
    clock.advance(seconds=599)
    assert not rt.board.claim(lead_id, "ledger-2")
    clock.advance(seconds=1)
    assert rt.board.claim(lead_id, "ledger-2")  # the first worker crashed; its lease ran out
    rt.board.release(lead_id, "ledger-1")  # a stale holder can't release someone else's claim
    assert rt.board.get(lead_id).claimed_by == "ledger-2"


def test_crashed_agents_lead_is_picked_up_after_lease_expiry(make_runtime, clock):
    ledger = FakeAgent("ledger", scope=["website"], owns={"website": "NEW"},
                       behaviour=lambda lead, ctx: AgentResult("ENRICHED", "verified"))
    rt = make_runtime([ledger])
    lead_id = new_lead(rt)
    rt.board.claim(lead_id, "ledger")  # simulate a run that died without releasing
    rt.atlas.tick()
    assert rt.board.get(lead_id).status == "NEW" and ledger.calls == []
    clock.advance(seconds=rt.config.settings.atlas.lease_seconds)
    rt.atlas.tick()
    lead = rt.board.get(lead_id)
    assert lead.status == "ENRICHED" and lead.claimed_by is None


# ---------------------------------------------------------------- retries


def test_failures_back_off_exponentially_then_escalate(make_runtime, clock):
    def broken(lead, ctx):
        raise RuntimeError("Places API timeout")

    ledger = FakeAgent("ledger", scope=["website"], owns={"website": "NEW"}, behaviour=broken)
    rt = make_runtime([ledger])
    lead_id = new_lead(rt)
    backoff = rt.config.settings.atlas.retry_backoff_seconds

    rt.atlas.tick()  # failure 1, held for 30s
    assert len(ledger.calls) == 1
    rt.atlas.tick()
    assert len(ledger.calls) == 1  # still backing off
    for failure, wait in ((2, backoff), (3, backoff * 2), (4, backoff * 4)):
        clock.advance(seconds=wait - 1)
        rt.atlas.tick()
        assert len(ledger.calls) == failure - 1  # not yet
        clock.advance(seconds=1)
        rt.atlas.tick()
        assert len(ledger.calls) == failure

    lead = rt.board.get(lead_id)
    assert lead.status == "ESCALATED" and lead.claimed_by is None
    with rt.sessions() as s:
        note = s.scalars(select(Event.note).where(Event.lead_id == lead_id, Event.to_status == "ESCALATED",
                                                  Event.from_status == "NEW")).one()
    assert "Places API timeout" in note and "failed 4 times" in note


def test_an_illegal_result_counts_as_a_failure(make_runtime):
    cheat = FakeAgent("ledger", scope=["website"], owns={"website": "NEW"},
                      behaviour=lambda lead, ctx: AgentResult("APPROVED"))
    rt = make_runtime([cheat])
    lead_id = new_lead(rt)
    report = rt.atlas.tick()
    assert report.failures == 1 and rt.board.get(lead_id).status == "NEW"


def test_success_resets_the_failure_count(make_runtime, clock):
    attempts = []

    def flaky(lead, ctx):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("blip")
        return AgentResult("ENRICHED")

    rt = make_runtime([FakeAgent("ledger", scope=["website"], owns={"website": "NEW"}, behaviour=flaky)])
    lead_id = new_lead(rt)
    rt.atlas.tick()
    clock.advance(seconds=30)
    rt.atlas.tick()
    assert rt.board.get(lead_id).status == "ENRICHED"


def test_agent_results_record_artifacts_with_versions(make_runtime):
    from wots.agents.base import ArtifactOut
    from wots.core.models import Artifact

    quill = FakeAgent("quill", scope=["website"], owns={"website": "ENRICHED"},
                      behaviour=lambda lead, ctx: AgentResult("COPY_READY", artifacts=[ArtifactOut("copy", "copy.json")]))
    rt = make_runtime([quill])
    lead_id = new_lead(rt)
    force_status(rt, lead_id, "ENRICHED")
    rt.atlas.tick()
    force_status(rt, lead_id, "ENRICHED")
    rt.atlas.tick()
    with rt.sessions() as s:
        versions = list(s.scalars(select(Artifact.version).where(Artifact.lead_id == lead_id).order_by(Artifact.version)))
    assert versions == [1, 2]
