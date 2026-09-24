"""Shared fixtures: a real SQLite database per test (migrated with Alembic), a controllable clock,
and fake agents, so Atlas's rules are tested without any LLM or network."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

import pytest
from sqlalchemy import update

from wots.agents.base import AgentContext, AgentResult
from wots.core.config import AgentConfig, Config, load_config
from wots.core.models import Lead
from wots.core.runtime import Runtime, build_runtime


class FakeClock:
    def __init__(self, start: datetime = datetime(2026, 9, 1, 0, 0)):  # a Tuesday, 12pm in Auckland
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


class FakeAgent:
    """Runs `behaviour(lead, ctx)`; by default leaves the lead where it is."""

    def __init__(self, name: str, *, scope: list[str], owns: dict[str, str], assigned_only=(), pool=(),
                 uses_llm: bool = False, behaviour: Callable | None = None):
        self.name = name
        self.config = AgentConfig.model_validate({
            "class": "tests:FakeAgent", "enabled": True, "scope": scope, "owns": owns,
            "assigned_only": list(assigned_only), "pool": list(pool), "uses_llm": uses_llm,
        })
        self.config.name = name
        self.behaviour = behaviour or (lambda lead, ctx: AgentResult(None))
        self.calls: list[tuple[int, str, str | None]] = []  # (lead id, status seen, feedback)

    def run(self, lead: Lead, ctx: AgentContext) -> AgentResult:
        self.calls.append((lead.id, lead.status, ctx.feedback))
        return self.behaviour(lead, ctx)


def web_designer(name: str, behaviour: Callable | None = None) -> FakeAgent:
    return FakeAgent(name, scope=["website"], owns={"website": "ASSIGNED"}, assigned_only=["website"],
                     pool=["website"], uses_llm=True, behaviour=behaviour)


@pytest.fixture(autouse=True)
def no_real_claude(monkeypatch):
    """Tests must never reach the real API (the developer's .env key would otherwise be used)."""

    class Refuse:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("Tests must not call the real Claude API; inject a fake client")

    monkeypatch.setattr("wots.core.llm.anthropic.Anthropic", Refuse)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def make_runtime(tmp_path, clock) -> Callable[..., Runtime]:
    def _make(agents: list[FakeAgent] | None = None, **settings) -> Runtime:
        base = load_config()
        data = base.settings.model_copy(update={
            "data_dir": str(tmp_path / "data"),
            "database_url": f"sqlite:///{tmp_path / 'wots.db'}",
            "dry_run": settings.pop("dry_run", True),
        })
        for key, value in settings.items():  # e.g. wip={"wip_mode": "rolling"}
            section = getattr(data, key)
            data = data.model_copy(update={key: section.model_copy(update=value) if hasattr(section, "model_copy") else value})
        config = Config(settings=data, agents=base.agents, countries=base.countries)
        return build_runtime(config, agents={a.name: a for a in (agents or [])}, clock=clock)

    return _make


def force_status(rt: Runtime, lead_id: int, status: str, **fields) -> None:
    """Put a lead into a status directly, bypassing the board (test setup only)."""
    with rt.sessions.begin() as s:
        s.execute(update(Lead).where(Lead.id == lead_id).values(status=status, **fields))


def new_lead(rt: Runtime, scope: str = "website", name: str = "Joe's Plumbing", **fields) -> int:
    return rt.board.create_lead(scope=scope, business_name=name, country="US", actor="test", **fields).id
