"""Metering and credits, notifications, migrations and the CLI (spec v2 §4, §10)."""
from types import SimpleNamespace

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import select

from wots.core.metering import BudgetExceeded, LLMConfigError, LLMError, start_of_local_day
from wots.core.models import Base, CreditLedger, UsageEvent

from .conftest import employee_id, force_status, internal, new_item


class FakeClient:
    def __init__(self, stop_reason="end_turn"):
        self.calls = []
        response = SimpleNamespace(usage=SimpleNamespace(input_tokens=1000, output_tokens=2000), stop_reason=stop_reason)
        create = lambda **kw: self.calls.append(kw) or response  # noqa: E731
        self.messages = SimpleNamespace(create=create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=create))


def test_llm_calls_are_costed_attributed_and_refused_over_budget(make_runtime):
    client = FakeClient()
    rt = make_runtime(llm_client=client)
    ctx = internal(rt)
    quill, item = employee_id(rt, ctx, "Quill"), new_item(rt)
    llm = rt.meter.for_employee(ctx, quill, item)
    llm.complete(model="claude-opus-5", system="s", messages=[{"role": "user", "content": "hi"}])
    assert rt.meter.spend_today(ctx) == pytest.approx((1000 * 5 + 2000 * 25) / 1e6)
    assert client.calls[0]["fallbacks"] == "default" and client.calls[0]["thinking"] == {"type": "adaptive"}
    with rt.sessions() as s:
        usage = s.scalars(select(UsageEvent).where(UsageEvent.org_id == ctx.org_id)).one()
    assert (usage.employee_id, usage.work_item_id, usage.credits) == (quill, item, pytest.approx(5.5))

    llm.complete(model="claude-haiku-4-5", system="s", messages=[{"role": "user", "content": "hi"}])
    assert "thinking" not in client.calls[1]  # Haiku 4.5 doesn't take adaptive thinking
    with pytest.raises(LLMError):
        llm.complete(model="claude-haiku-4-5", system="s", messages=[], effort="low")  # no effort on Haiku

    rt.meter.record(ctx, kind="llm", model="claude-opus-5", cost_usd=5)
    with pytest.raises(BudgetExceeded):
        llm.complete(model="claude-opus-5", system="s", messages=[])
    with pytest.raises(LLMError):
        rt.meter.cost("some-unpriced-model", 1, 1)


def test_effort_reaches_the_api_alongside_structured_output(make_runtime):
    client = FakeClient()
    rt = make_runtime(llm_client=client)
    ctx = internal(rt)
    fmt = {"format": {"type": "json_schema", "schema": {"type": "object"}}}
    rt.meter.complete(ctx, model="claude-sonnet-5", system="s", messages=[], effort="low", output_config=fmt)
    assert client.calls[0]["output_config"] == {**fmt, "effort": "low"}
    rt.meter.complete(ctx, model="claude-opus-5", system="s", messages=[])
    assert "output_config" not in client.calls[1]  # unset = the API default


def test_refusals_are_errors_but_still_costed(make_runtime):
    rt = make_runtime(llm_client=FakeClient(stop_reason="refusal"))
    ctx = internal(rt)
    with pytest.raises(LLMError):
        rt.meter.complete(ctx, model="claude-opus-5", system="s", messages=[])
    assert rt.meter.spend_today(ctx) > 0


@pytest.mark.parametrize("message", [
    "Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing.",
    "This API key is not scoped to a workspace, so this request must include the anthropic-workspace-id header.",
])
def test_account_wide_api_errors_become_config_errors(make_runtime, message):
    import anthropic
    import httpx

    class NoCredit:
        def __init__(self):
            request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
            error = anthropic.BadRequestError(message, response=httpx.Response(400, request=request), body=None)

            def create(**kw):
                raise error

            self.messages = SimpleNamespace(create=create)
            self.beta = SimpleNamespace(messages=SimpleNamespace(create=create))

    rt = make_runtime(llm_client=NoCredit())
    with pytest.raises(LLMConfigError):
        rt.meter.complete(internal(rt), model="claude-opus-5", system="s", messages=[])


def test_dry_run_caps_spend_lower_than_the_budget(make_runtime):
    rt = make_runtime(dry_run=True)
    ctx = internal(rt)
    assert rt.meter.daily_cap(ctx) == 1.00
    rt.meter.record(ctx, kind="llm", model="claude-opus-5", cost_usd=0.99)
    assert rt.meter.paused_reason(ctx) is None
    rt.meter.record(ctx, kind="llm", model="claude-opus-5", cost_usd=0.02)
    assert "dry run" in rt.meter.paused_reason(ctx)


def test_metered_offices_spend_credits_and_pause_at_zero(make_runtime, clock):
    rt = make_runtime()
    mine = internal(rt)
    shop = rt.offices.create_office("Paying Customer", templates=["web_agency"], ceo_email="ceo@customer.test")
    assert rt.meter.credit_balance(mine) is None  # the internal office is unlimited
    assert rt.meter.credit_balance(shop) == 0 and "no credits" in rt.meter.paused_reason(shop)
    with rt.sessions.begin() as s:
        s.add(CreditLedger(org_id=shop.org_id, delta=100, reason="topup", balance_after=100, ts=clock()))
    assert rt.meter.paused_reason(shop) is None
    rt.meter.record(shop, kind="llm", model="claude-opus-5", cost_usd=0.40)
    assert rt.meter.credit_balance(shop) == pytest.approx(60)
    with rt.sessions() as s:
        assert s.scalars(select(CreditLedger).where(CreditLedger.org_id == mine.org_id)).all() == []


def test_today_is_the_ceos_day_in_auckland(clock):
    # 2026-09-01 00:00 UTC is 12:00 on 1 Sep in Auckland (NZST, UTC+12)
    assert start_of_local_day(clock(), "Pacific/Auckland").isoformat() == "2026-08-31T12:00:00"


def test_notifications_go_to_each_offices_outbox(make_runtime):
    rt = make_runtime(dry_run=True)
    ctx = internal(rt)
    item = new_item(rt)
    force_status(rt, ctx, item, "IN_QA")
    rt.board.transition(ctx, item, "READY_FOR_APPROVAL", "employee", "hawk")
    rt.board.transition(ctx, item, "APPROVED", "user", "ceo")
    log = (rt.config.settings.data_path / "outbox" / ctx.org_id / "notifications.log").read_text()
    assert "READY_FOR_APPROVAL" in log and "APPROVED" not in log.replace("READY_FOR_APPROVAL", "")


def test_migrations_match_the_models(make_runtime):
    rt = make_runtime()
    with rt.engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_catalogue_is_synced_to_the_database(make_runtime):
    from wots.core.models import EmployeeType, WorkflowDef

    rt = make_runtime()
    with rt.sessions() as s:
        assert len(s.scalars(select(EmployeeType)).all()) == len(rt.catalogue.types)
        assert {w.key for w in s.scalars(select(WorkflowDef))} == {"website", "ad_refresh"}
    rt.catalogue.sync(rt.sessions)  # idempotent
    with rt.sessions() as s:
        assert len(s.scalars(select(EmployeeType)).all()) == len(rt.catalogue.types)


def test_cli_import_status_and_offices(tmp_path, monkeypatch, capsys):
    from wots.core import cli
    from wots.core.config import get_config

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    monkeypatch.setenv("WOTS_DATA_DIR", str(tmp_path / "data"))
    get_config.cache_clear()
    csv = tmp_path / "leads.csv"
    csv.write_text("business_name,country,phone\nJoe's Plumbing,US,555-0100\nCorner Cafe,GB,020 7946 0000\n"
                   "Kiwi Co,NZ,\n,US,\n")
    try:
        assert cli.main(["import-leads", str(csv), "--org", "wots-office", "--workflow", "website"]) == 0
        out = capsys.readouterr().out
        assert "Imported 2 lead(s)" in out and "'NZ' is not a target" in out and "no business_name" in out
        assert cli.main(["status"]) == 0
        out = capsys.readouterr().out
        assert "Wots Office" in out and "NEW" in out and "DRY_RUN=on" in out and "waiting for: cold_email" in out
        assert cli.main(["orgs", "create", "Test Agency", "--template", "web_agency", "--ceo", "a@b.test"]) == 0
        assert cli.main(["orgs", "list"]) == 0
        assert "test-agency" in capsys.readouterr().out
        assert cli.main(["employees", "hire", "--org", "test-agency", "--type", "web_developer", "--name", "Ada",
                         "--config", "style_profile=minimal"]) == 0
        assert cli.main(["employees", "list", "--org", "test-agency"]) == 0
        assert "Ada" in capsys.readouterr().out
        assert cli.main(["workflows", "validate"]) == 0
        assert cli.main(["login-link"]) == 0
        assert "/auth/login?token=" in capsys.readouterr().out
    finally:
        get_config.cache_clear()
