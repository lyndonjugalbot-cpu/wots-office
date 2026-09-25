"""Atlas's rules (spec v2 §7), driven by the workflow YAML, with fake employees."""
import pytest
from sqlalchemy import func, select

from wots.core.metering import LLMConfigError
from wots.core.models import Artifact, Event, Job, WorkItem
from wots.employees.base import ArtifactOut, EmployeeResult

from .conftest import behave, employee_id, fake_team, force_status, internal, new_item

ACTIVE = ["ASSIGNED", "BUILDING", "IN_QA", "NEEDS_FIX", "READY_FOR_APPROVAL", "ESCALATED"]


def to(status, **kw):
    return lambda item, ctx: EmployeeResult(status, **kw)


def ready_for_developers(rt, n):
    ctx = internal(rt)
    items = [new_item(rt, name=f"Biz {i}") for i in range(n)]
    for item in items:
        force_status(rt, ctx, item, "ASSETS_READY")
    return items


def active_for(rt, name):
    ctx = internal(rt)
    with rt.sessions() as s:
        return s.scalar(select(func.count()).select_from(WorkItem).where(
            WorkItem.org_id == ctx.org_id, WorkItem.assigned_employee_id == employee_id(rt, ctx, name),
            WorkItem.status.in_(ACTIVE)))


def statuses(rt):
    return sorted(i.status for i in rt.board.items(internal(rt)))


def test_items_flow_through_the_workflow(make_runtime):
    rt = make_runtime()
    behave(rt, "Scout", to("VERIFY"))
    behave(rt, "Ledger", to("ENRICHED"))
    behave(rt, "Quill", to("COPY_READY"))
    behave(rt, "Iris", to("ASSETS_READY"))
    behave(rt, "Juno", to("ASSETS_READY"))
    behave(rt, "Pixel", to("IN_QA"))
    behave(rt, "Nova", to("IN_QA"))
    behave(rt, "Hawk", to("READY_FOR_APPROVAL"))
    item = new_item(rt)
    rt.atlas.tick()  # NEW -> ... -> ASSETS_READY
    rt.atlas.tick()  # assigned, built, tested
    ctx = internal(rt)
    path = [e.to_status for e in rt.board.history(ctx, item)]
    assert path == ["NEW", "VERIFY", "ENRICHED", "COPY_READY", "ASSETS_READY", "ASSIGNED", "BUILDING", "IN_QA",
                    "READY_FOR_APPROVAL"]
    developer = rt.fakes["Nova"].calls or rt.fakes["Pixel"].calls
    assert developer[0][1] == "BUILDING"  # the start hop happens before the developer runs
    with rt.sessions() as s:
        assert set(s.scalars(select(Job.status).where(Job.org_id == ctx.org_id))) == {"done"}


def test_batch_mode_waits_until_every_item_is_done(make_runtime):
    rt = make_runtime()
    ctx = internal(rt)
    items = ready_for_developers(rt, 5)
    rt.atlas.tick()
    assert active_for(rt, "Pixel") == 2 and active_for(rt, "Nova") == 2
    waiting = rt.board.items(ctx, statuses=["ASSETS_READY"])
    assert len(waiting) == 1

    pixels = [i.id for i in rt.board.items(ctx, assigned_to=employee_id(rt, ctx, "Pixel"))]
    force_status(rt, ctx, pixels[0], "APPROVED")
    rt.atlas.tick()
    assert rt.board.get(ctx, waiting[0].id).status == "ASSETS_READY"  # batch: one still in progress
    force_status(rt, ctx, pixels[1], "DISQUALIFIED")
    rt.atlas.tick()
    assert rt.board.get(ctx, waiting[0].id).assigned_employee_id == employee_id(rt, ctx, "Pixel")
    assert len(items) == 5


def test_rolling_mode_refills_as_soon_as_a_slot_frees(make_runtime):
    rt = make_runtime()
    ctx = internal(rt)
    for name in ("Pixel", "Nova"):
        rt.offices.update_employee(ctx, employee_id(rt, ctx, name), {"wip_mode": "rolling"})
    ready_for_developers(rt, 5)
    rt.atlas.tick()
    pixels = [i.id for i in rt.board.items(ctx, assigned_to=employee_id(rt, ctx, "Pixel"))]
    force_status(rt, ctx, pixels[0], "APPROVED")
    rt.atlas.tick()
    assert rt.board.items(ctx, statuses=["ASSETS_READY"]) == []
    assert active_for(rt, "Pixel") == 2


def test_work_is_split_evenly_and_the_less_loaded_developer_goes_first(make_runtime, clock):
    rt = make_runtime()
    ctx = internal(rt)
    ready_for_developers(rt, 2)
    rt.atlas.tick()
    assert active_for(rt, "Pixel") == 1 and active_for(rt, "Nova") == 1

    for item in rt.board.items(ctx, statuses=ACTIVE):
        force_status(rt, ctx, item.id, "APPROVED")
    # Pixel had more this week, so the next single item goes to Nova even though Pixel sorts first
    extra = ready_for_developers(rt, 1)[0]
    force_status(rt, ctx, extra, "ASSETS_READY")
    with rt.sessions.begin() as s:
        pix = employee_id(rt, ctx, "Pixel")
        for item in rt.board.items(ctx, assigned_to=pix):
            rt.board._event(s, ctx, item.id, "ASSETS_READY", "ASSIGNED", "system", "atlas", "earlier")
    clock.advance(seconds=1)
    rt.atlas.tick()
    assert rt.board.get(ctx, extra).assigned_employee_id == employee_id(rt, ctx, "Nova")


def test_developers_never_exceed_wip_over_many_ticks(make_runtime, clock):
    rt = make_runtime()
    ctx = internal(rt)
    behave(rt, "Pixel", to("IN_QA"))
    behave(rt, "Nova", to("IN_QA"))
    turn = {"n": 0}

    def hawk(item, _ctx):
        turn["n"] += 1
        return EmployeeResult("READY_FOR_APPROVAL" if turn["n"] % 3 else "NEEDS_FIX",
                              qa_report={"passed": bool(turn["n"] % 3), "issues": []})

    behave(rt, "Hawk", hawk)
    ready_for_developers(rt, 9)
    for _ in range(15):
        rt.atlas.tick()
        assert active_for(rt, "Pixel") <= 2 and active_for(rt, "Nova") <= 2
        for item in rt.board.items(ctx, statuses=["READY_FOR_APPROVAL"]):
            rt.board.transition(ctx, item.id, "APPROVED", "user", "ceo")
        clock.advance(seconds=30)
    assert statuses(rt) == ["APPROVED"] * 9


def test_a_failed_qa_goes_back_to_the_same_developer_with_feedback_then_escalates(make_runtime):
    rt = make_runtime()
    ctx = internal(rt)
    behave(rt, "Pixel", to("IN_QA"))
    behave(rt, "Nova", to("IN_QA"))
    behave(rt, "Hawk", to("NEEDS_FIX", qa_report={"passed": False, "issues": [
        {"severity": "high", "description": "Phone number doesn't match"}]}))
    item = ready_for_developers(rt, 1)[0]
    for _ in range(6):
        rt.atlas.tick()
    final = rt.board.get(ctx, item)
    assert final.status == "ESCALATED" and final.fix_count == 3
    builder = "Pixel" if rt.fakes["Pixel"].calls else "Nova"
    other = "Nova" if builder == "Pixel" else "Pixel"
    assert rt.fakes[other].calls == []
    calls = rt.fakes[builder].calls
    assert len(calls) == 3 and calls[0][2] is None  # the build and two fixes; the third failure escalates
    assert all("Phone number doesn't match" in c[2] for c in calls[1:])
    note = rt.board.history(ctx, item)[-1].note
    assert "Fix loop reached 3" in note and "Phone number" in note
    assert any("ESCALATED" in text for _, text in rt.notifier.sent)


def test_ceo_notes_beat_older_qa_reports(make_runtime, clock):
    from wots.core.models import Approval, QAReport

    rt = make_runtime()
    ctx = internal(rt)
    item = new_item(rt)
    with rt.sessions.begin() as s:
        s.add(QAReport(org_id=ctx.org_id, work_item_id=item, artifact_version=1, passed=False,
                       issues=[{"severity": "low", "description": "typo"}], created_at=clock()))
    assert rt.board.latest_feedback(ctx, item) == "QA report: [low] typo"
    clock.advance(minutes=1)
    with rt.sessions.begin() as s:
        s.add(Approval(org_id=ctx.org_id, work_item_id=item, kind="build", decision="rejected",
                       notes="Mention Portland", decided_at=clock()))
    assert rt.board.latest_feedback(ctx, item) == "CEO notes: Mention Portland"


def test_a_lease_blocks_others_until_it_expires(make_runtime, clock):
    rt = make_runtime()
    ctx = internal(rt)
    ledger = behave(rt, "Ledger", to("ENRICHED"))
    item = new_item(rt)
    force_status(rt, ctx, item, "VERIFY")
    assert rt.board.claim(ctx, item, "crashed-worker")  # a worker that died mid-job
    rt.atlas.tick()
    assert ledger.calls == [] and rt.board.get(ctx, item).status == "VERIFY"
    clock.advance(seconds=rt.config.settings.atlas.lease_seconds + 1)
    rt.atlas.tick()
    assert rt.board.get(ctx, item).status == "ENRICHED"


def test_failures_back_off_exponentially_then_escalate(make_runtime, clock):
    rt = make_runtime()
    ctx = internal(rt)

    def broken(item, _ctx):
        raise RuntimeError("scraper timed out")

    ledger = behave(rt, "Ledger", broken)
    item = new_item(rt)
    force_status(rt, ctx, item, "VERIFY")
    backoff = rt.config.settings.atlas.retry_backoff_seconds
    rt.atlas.tick()
    assert len(ledger.calls) == 1
    rt.atlas.tick()
    assert len(ledger.calls) == 1  # held for the backoff
    for delay in (backoff, backoff * 2, backoff * 4):
        clock.advance(seconds=delay + 1)
        rt.atlas.tick()
    assert len(ledger.calls) == 4
    final = rt.board.get(ctx, item)
    assert final.status == "ESCALATED" and final.claimed_by is None
    assert "failed 4 times" in rt.board.history(ctx, item)[-1].note
    with rt.sessions() as s:
        assert s.scalar(select(func.count()).select_from(Job).where(Job.org_id == ctx.org_id, Job.status == "failed")) == 4


def test_an_illegal_result_counts_as_a_failure(make_runtime):
    rt = make_runtime()
    ctx = internal(rt)
    behave(rt, "Ledger", to("APPROVED"))  # skipping the whole pipeline isn't allowed
    item = new_item(rt)
    force_status(rt, ctx, item, "VERIFY")
    report = rt.atlas.tick()
    assert report.failures == 1 and rt.board.get(ctx, item).status == "VERIFY"
    assert "not allowed" in rt.board.history(ctx, item)[-1].note


def test_success_resets_the_failure_count(make_runtime, clock):
    rt = make_runtime()
    ctx = internal(rt)
    attempts = {"n": 0}

    def flaky(item, _ctx):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("blip")
        return EmployeeResult("ENRICHED")

    behave(rt, "Ledger", flaky)
    item = new_item(rt)
    force_status(rt, ctx, item, "VERIFY")
    rt.atlas.tick()
    clock.advance(minutes=5)
    rt.atlas.tick()
    assert rt.board.get(ctx, item).status == "ENRICHED"
    assert rt.board.record_failure(ctx, item, "x", "new problem") == 1  # counted from the last status change


def test_results_record_artifacts_with_versions(make_runtime):
    rt = make_runtime()
    ctx = internal(rt)
    behave(rt, "Quill", lambda item, _ctx: EmployeeResult(None, "drafted", artifacts=[ArtifactOut("copy", "copy.json")]))
    item = new_item(rt)
    force_status(rt, ctx, item, "ENRICHED")
    rt.atlas.tick()
    rt.atlas.tick()
    with rt.sessions() as s:
        versions = list(s.scalars(select(Artifact.version).where(Artifact.org_id == ctx.org_id).order_by(Artifact.version)))
    assert versions == [1, 2]


def test_a_state_owned_by_a_type_nobody_holds_is_skipped(make_runtime):
    rt = make_runtime()
    ctx = internal(rt)
    for name in ("Iris", "Juno"):
        rt.offices.fire(ctx, employee_id(rt, ctx, name))
    item = new_item(rt)
    force_status(rt, ctx, item, "COPY_READY")
    report = rt.atlas.tick()
    assert report.skipped == 1
    events = rt.board.history(ctx, item)
    assert any(e.to_status == "ASSETS_READY" and "no Graphic Designer" in e.note for e in events)


def test_the_per_office_job_cap(make_runtime):
    rt = make_runtime(atlas={"max_jobs_per_org_per_tick": 3})
    ctx = internal(rt)
    ledger = behave(rt, "Ledger", to("ENRICHED"))
    for i in range(5):
        force_status(rt, ctx, new_item(rt, name=f"B{i}"), "VERIFY")
    rt.atlas.tick()
    assert len(ledger.calls) == 3
    rt.atlas.tick()
    assert len(ledger.calls) == 5


def test_every_office_gets_its_turn(make_runtime):
    rt = make_runtime(atlas={"max_jobs_per_org_per_tick": 1})
    other = rt.offices.create_office("Second Office", templates=["web_agency"], ceo_email="ceo@second.test")
    fake_team(rt, other)
    new_item(rt, ctx=other)
    new_item(rt)
    report = rt.atlas.tick()
    assert report.offices == 2
    assert all(len(rt.board.history(c, i.id)) >= 1 for c in (other, internal(rt)) for i in rt.board.items(c))


def spend(rt, ctx, usd):
    rt.meter.record(ctx, kind="llm", model="claude-opus-5", output_tokens=int(usd / 25 * 1_000_000), cost_usd=usd)


def test_the_budget_guard_pauses_only_llm_employees_and_tells_the_ceo_once(make_runtime, clock):
    rt = make_runtime(dry_run=False)  # the real daily budget: $5
    ctx = internal(rt)
    quill = behave(rt, "Quill", to("COPY_READY"))  # a copywriter uses Claude
    ledger = behave(rt, "Ledger", to("ENRICHED"))  # a data verifier doesn't
    first, second = new_item(rt), new_item(rt, name="Second")
    force_status(rt, ctx, first, "ENRICHED")
    force_status(rt, ctx, second, "VERIFY")
    spend(rt, ctx, 5.00)
    report = rt.atlas.tick()
    assert report.paused and quill.calls == []
    assert [c[0] for c in ledger.calls] == [second]  # non-LLM employees keep working
    rt.atlas.tick()
    assert sum("Budget guard" in text for _, text in rt.notifier.sent) == 1

    clock.advance(days=1)  # a new day in Auckland
    rt.atlas.tick()
    assert rt.board.get(ctx, first).status == "COPY_READY"


def test_a_broken_api_key_pauses_llm_employees_without_escalating(make_runtime):
    rt = make_runtime()
    ctx = internal(rt)

    def needs_claude(item, _ctx):
        raise LLMConfigError("Claude API setup problem: key is not scoped to a workspace")

    quill = behave(rt, "Quill", needs_claude)
    items = [new_item(rt, name=f"Biz {i}") for i in range(3)]
    for item in items:
        force_status(rt, ctx, item, "ENRICHED")
    for _ in range(6):
        report = rt.atlas.tick()
    assert report.llm_unavailable and len(quill.calls) == 6  # one probe per tick, not one per item
    assert {rt.board.get(ctx, i).status for i in items} == {"ENRICHED"}
    assert sum("LLM unavailable" in text for _, text in rt.notifier.sent) == 1


def test_a_type_without_an_implementation_waits(make_runtime):
    """Employees whose type is built in a later phase are on the team but never dispatched."""
    rt = make_runtime(fakes=False)
    ctx = internal(rt)
    staff = {st.info.name: st for st in rt.atlas.staff(ctx)}
    assert staff["Ledger"].impl is not None
    assert staff["Dock"].impl is None and staff["Lens"].impl is None
    item = new_item(rt)
    force_status(rt, ctx, item, "APPROVED")
    rt.atlas.tick()
    assert rt.board.get(ctx, item).status == "APPROVED"
    with rt.sessions() as s:
        assert not s.scalar(select(func.count()).select_from(Event).where(
            Event.org_id == ctx.org_id, Event.note.startswith("error:")))


@pytest.mark.parametrize("mode", ["inline", "threads"])
def test_thread_workers_give_the_same_result(make_runtime, mode):
    from wots.core.jobs import ThreadQueue

    rt = make_runtime()
    if mode == "threads":
        rt.atlas.queue = ThreadQueue(4)
    behave(rt, "Ledger", to("ENRICHED"))
    ctx = internal(rt)
    for i in range(6):
        force_status(rt, ctx, new_item(rt, name=f"B{i}"), "VERIFY")
    rt.atlas.tick()
    assert statuses(rt) == ["ENRICHED"] * 6
