"""Budget guard, LLM costing, notifications, migrations and the CLI."""
from types import SimpleNamespace

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from wots.agents.base import AgentResult
from wots.core.llm import BudgetExceeded, LLMError, start_of_local_day
from wots.core.models import Base

from .conftest import FakeAgent, new_lead


def spend(rt, usd, agent="quill"):
    # claude-opus-5 output is $25 per million tokens
    rt.llm.record(agent, "claude-opus-5", 0, int(usd / 25 * 1_000_000), None)


def test_budget_guard_pauses_only_llm_agents_and_notifies_once(make_runtime, clock):
    ledger = FakeAgent("ledger", scope=["website"], owns={"website": "NEW"}, uses_llm=True,
                       behaviour=lambda lead, ctx: AgentResult("ENRICHED"))
    counter = FakeAgent("counter", scope=["website"], owns={"website": "ENRICHED"}, uses_llm=False)
    rt = make_runtime([ledger, counter], dry_run=False)  # real budget: $5
    first, second = new_lead(rt), new_lead(rt, name="Second")

    spend(rt, 5.00)
    report = rt.atlas.tick()
    assert report.budget_paused and ledger.calls == []
    rt.board.transition(second, "ENRICHED", "test")
    rt.atlas.tick()
    assert [c[0] for c in counter.calls] == [second]  # non-LLM agents keep working while paused
    budget_msgs = [m for m in rt.notifier.sent if "Budget guard" in m]
    assert len(budget_msgs) == 1 and "$5.00" in budget_msgs[0]

    clock.advance(days=1)  # a new day in Auckland
    rt.atlas.tick()
    assert rt.board.get(first).status == "ENRICHED"


def test_dry_run_caps_spend_lower_than_the_budget(make_runtime):
    rt = make_runtime(dry_run=True)
    assert rt.config.settings.llm_cap_usd == 1.00
    spend(rt, 0.99)
    assert not rt.llm.over_budget()
    spend(rt, 0.02)
    assert rt.llm.over_budget()


def test_today_is_the_ceos_day_in_auckland(clock):
    # 2026-09-01 00:00 UTC is 12:00 on 1 Sep in Auckland (NZST, UTC+12)
    assert start_of_local_day(clock(), "Pacific/Auckland").isoformat() == "2026-08-31T12:00:00"


class FakeClient:
    def __init__(self, stop_reason="end_turn"):
        self.calls = []
        response = SimpleNamespace(usage=SimpleNamespace(input_tokens=1000, output_tokens=2000), stop_reason=stop_reason)
        create = lambda **kw: self.calls.append(kw) or response  # noqa: E731
        self.messages = SimpleNamespace(create=create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=create))


def test_llm_calls_are_costed_and_refused_over_budget(make_runtime):
    client = FakeClient()
    rt = make_runtime()
    rt.llm._client = client
    rt.llm.complete(agent="quill", model="claude-opus-5", system="s", messages=[{"role": "user", "content": "hi"}])
    assert rt.llm.spend_today() == pytest.approx((1000 * 5 + 2000 * 25) / 1e6)
    assert client.calls[0]["fallbacks"] == "default" and client.calls[0]["thinking"] == {"type": "adaptive"}

    rt.llm.complete(agent="ledger", model="claude-haiku-4-5", system="s", messages=[{"role": "user", "content": "hi"}])
    assert "thinking" not in client.calls[1]  # Haiku 4.5 doesn't take adaptive thinking

    spend(rt, 5)
    with pytest.raises(BudgetExceeded):
        rt.llm.complete(agent="quill", model="claude-opus-5", system="s", messages=[])
    with pytest.raises(LLMError):
        rt.llm.cost("some-unpriced-model", 1, 1)


def test_refusals_are_errors_but_still_costed(make_runtime):
    rt = make_runtime()
    rt.llm._client = FakeClient(stop_reason="refusal")
    with pytest.raises(LLMError):
        rt.llm.complete(agent="quill", model="claude-opus-5", system="s", messages=[])
    assert rt.llm.spend_today() > 0


def test_dry_run_notifications_go_to_the_outbox_log(make_runtime):
    rt = make_runtime(dry_run=True)
    lead_id = new_lead(rt)
    for status in ("ENRICHED", "COPY_READY", "ASSETS_READY", "ASSIGNED", "BUILDING", "IN_QA", "READY_FOR_APPROVAL"):
        rt.board.transition(lead_id, status, "test")
    log = (rt.config.settings.data_path / "outbox" / "notifications.log").read_text()
    assert "READY_FOR_APPROVAL" in log and "ENRICHED" not in log  # only the statuses that need the CEO


def test_migrations_match_the_models(make_runtime):
    rt = make_runtime()
    with rt.engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_cli_import_and_status(tmp_path, monkeypatch, capsys):
    from wots.core import cli
    from wots.core.config import get_config

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    monkeypatch.setenv("WOTS_DATA_DIR", str(tmp_path / "data"))
    get_config.cache_clear()
    csv = tmp_path / "leads.csv"
    csv.write_text("business_name,country,phone\nJoe's Plumbing,US,555-0100\nCorner Cafe,GB,020 7946 0000\n"
                   "Kiwi Co,NZ,\n,US,\n")
    try:
        assert cli.main(["import-leads", str(csv), "--scope", "website"]) == 0
        out = capsys.readouterr().out
        assert "Imported 2 lead(s)" in out and "'NZ' is not a v1 target" in out and "no business_name" in out
        assert cli.main(["tick"]) == 0
        assert cli.main(["status"]) == 0
        out = capsys.readouterr().out
        # Ledger enriches both leads on the tick; Quill can't reach Claude in tests, so they wait at ENRICHED
        assert "website (2 leads)" in out and "ENRICHED" in out and "DRY_RUN=on" in out
        assert "LLM spend today: $0.00" in out
    finally:
        get_config.cache_clear()


def test_a_broken_api_key_pauses_llm_agents_without_escalating_leads(make_runtime):
    from wots.core.llm import LLMConfigError

    def needs_claude(lead, ctx):
        raise LLMConfigError("Claude API setup problem: key is not scoped to a workspace")

    quill = FakeAgent("quill", scope=["website"], owns={"website": "NEW"}, uses_llm=True, behaviour=needs_claude)
    rt = make_runtime([quill])
    leads = [new_lead(rt, name=f"Biz {i}") for i in range(3)]
    for _ in range(6):
        report = rt.atlas.tick()
    assert report.llm_unavailable and len(quill.calls) == 6  # one probe per tick, not one per lead
    assert {rt.board.get(i).status for i in leads} == {"NEW"}
    assert sum("LLM unavailable" in m for m in rt.notifier.sent) == 1
