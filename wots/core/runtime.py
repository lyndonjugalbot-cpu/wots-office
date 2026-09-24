"""Wires config, database, board, LLM, notifier, agents and Atlas together."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..agents.base import Agent, load_agents
from ..integrations.notify import Notifier
from .atlas import Atlas
from .board import Board
from .clock import Clock, utcnow
from .config import Config, get_config
from .db import make_engine, make_sessionmaker, upgrade
from .llm import LLM


@dataclass
class Runtime:
    config: Config
    engine: Engine
    sessions: sessionmaker[Session]
    board: Board
    llm: LLM
    notifier: Notifier
    atlas: Atlas


def build_runtime(config: Config | None = None, *, agents: dict[str, Agent] | None = None, clock: Clock = utcnow,
                  migrate: bool = True, llm_client=None) -> Runtime:
    config = config or get_config()
    settings = config.settings
    if migrate:
        upgrade(settings.database_url)
    engine = make_engine(settings.database_url)
    sessions = make_sessionmaker(engine)
    notifier = Notifier(settings.notify.webhook, settings.dry_run, settings.data_path / "outbox", clock)
    board = Board(sessions, config, notifier, clock)
    llm = LLM(sessions, config, clock, client=llm_client)
    atlas = Atlas(sessions, config, board, load_agents(config) if agents is None else agents, llm, notifier, clock)
    return Runtime(config, engine, sessions, board, llm, notifier, atlas)
