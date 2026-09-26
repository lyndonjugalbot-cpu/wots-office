"""Shared fixtures: a real SQLite database per test (migrated with Alembic, so it starts with the
internal Wots Office and its team), a controllable clock, and fake employees, so Atlas's rules are
tested without any LLM or network."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

import pytest
from sqlalchemy import update

from wots.core.config import Config, load_config
from wots.core.context import OrgContext
from wots.core.models import WorkItem
from wots.core.runtime import Runtime, build_runtime
from wots.employees.base import EmployeeContext, EmployeeResult

from .fakeweb import FakeWeb

INTERNAL = "wots-office"


class FakeClock:
    def __init__(self, start: datetime = datetime(2026, 9, 1, 0, 0)):  # a Tuesday, 12pm in Auckland
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


class FakeEmployee:
    """Runs `behaviour(item, ctx)`; by default leaves the item where it is."""

    def __init__(self, name: str, behaviour: Callable | None = None):
        self.name = name
        self.behaviour = behaviour or (lambda item, ctx: EmployeeResult(None))
        self.calls: list[tuple[str, str, str | None]] = []  # (item id, status seen, feedback)

    def run(self, item, task: str, ctx: EmployeeContext) -> EmployeeResult:
        self.calls.append((item.id, item.status, ctx.feedback))
        return self.behaviour(item, ctx)


@pytest.fixture(autouse=True)
def no_real_claude(monkeypatch):
    """Tests must never reach the real API (the developer's .env key would otherwise be used)."""

    class Refuse:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("Tests must not call the real Claude API; inject a fake client")

    monkeypatch.setattr("wots.core.metering.anthropic.Anthropic", Refuse)


@pytest.fixture(autouse=True)
def web(monkeypatch) -> FakeWeb:
    """Every runtime's integrations go to this fake internet, with test keys for the internal office."""
    for key in ("GOOGLE_PLACES_KEY", "COMPANIES_HOUSE_KEY", "ABN_GUID"):
        monkeypatch.setenv(key, f"test-{key.lower()}")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test-cloudflare-token")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test-account")
    return FakeWeb()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def config_for(tmp_path, **settings) -> Config:
    base = load_config()
    data = base.settings.model_copy(update={
        "data_dir": str(tmp_path / "data"),
        "database_url": f"sqlite:///{tmp_path / 'wots.db'}",
        "dry_run": settings.pop("dry_run", True),
    })
    for key, value in settings.items():  # e.g. atlas={"max_retries": 1}
        section = getattr(data, key)
        data = data.model_copy(update={key: section.model_copy(update=value) if hasattr(section, "model_copy") else value})
    return base.model_copy(update={"settings": data})


@pytest.fixture
def make_runtime(tmp_path, clock, web) -> Callable[..., Runtime]:
    """A runtime on a fresh database. With fakes=True (the default) every hired employee is a no-op
    FakeEmployee, reachable as rt.fakes[name]; tests give the ones they need a behaviour."""

    def _make(fakes: bool = True, llm_client=None, **settings) -> Runtime:
        rt = build_runtime(config_for(tmp_path, **settings), clock=clock, llm_client=llm_client,
                           http_transport=web.transport, preview_uploader=web.upload)
        rt.fakes = {}
        if fakes:
            for org in rt.offices.orgs():
                fake_team(rt, rt.offices.system_ctx(org.id))
        return rt

    return _make


def fake_team(rt: Runtime, ctx: OrgContext) -> dict[str, FakeEmployee]:
    for emp in rt.offices.employees(ctx):
        fake = FakeEmployee(emp.name)
        rt.atlas.impl_overrides[emp.id] = fake
        if ctx.slug == INTERNAL:
            rt.fakes[emp.name] = fake
    return rt.fakes


def internal(rt: Runtime) -> OrgContext:
    return rt.offices.system_ctx(INTERNAL)


def employee_id(rt: Runtime, ctx: OrgContext, name: str) -> str:
    return next(e.id for e in rt.offices.employees(ctx) if e.name == name)


def behave(rt: Runtime, name: str, behaviour: Callable) -> FakeEmployee:
    rt.fakes[name].behaviour = behaviour
    return rt.fakes[name]


def new_item(rt: Runtime, ctx: OrgContext | None = None, workflow: str = "website", name: str = "Joe's Plumbing",
             **fields) -> str:
    ctx = ctx or internal(rt)
    return rt.board.create_item(ctx, workflow_key=workflow,
                                profile={"business_name": name, "country": "US", **fields})


def force_status(rt: Runtime, ctx: OrgContext, item_id: str, status: str, **fields) -> None:
    """Put an item into a status directly, bypassing the board (test setup only)."""
    with rt.sessions.begin() as s:
        s.execute(update(WorkItem).where(WorkItem.org_id == ctx.org_id, WorkItem.id == item_id)
                  .values(status=status, **fields))


def as_user(rt: Runtime, ctx: OrgContext, role: str = "ceo") -> OrgContext:
    """The office as its CEO (or owner) sees it: a user context that can pass the CEO gates."""
    from wots.core.models import Membership
    from wots.core.repo import scoped

    with rt.sessions() as s:
        uid = s.scalars(scoped(Membership, ctx).where(Membership.role == role).with_only_columns(Membership.user_id)).first()
    return rt.offices.user_ctx(uid, ctx.slug)
