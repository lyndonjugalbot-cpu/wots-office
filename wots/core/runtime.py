"""Wires config, database, catalogue, board, metering, files, jobs and Atlas together."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..integrations.notify import Notifier
from ..integrations.toolbox import Integrations
from ..orchestration.atlas import Atlas
from .board import Board
from .catalogue import Catalogue
from .clock import Clock, utcnow
from .config import Config, get_config
from .db import make_engine, make_sessionmaker, upgrade
from .files import FileStore
from .jobs import InlineQueue, JobQueue
from .metering import Meter
from .offices import Offices
from .repo import install_query_guard


@dataclass
class Runtime:
    config: Config
    engine: Engine
    sessions: sessionmaker[Session]
    catalogue: Catalogue
    board: Board
    meter: Meter
    files: FileStore
    notifier: Notifier
    offices: Offices
    atlas: Atlas
    integrations: Integrations


def build_runtime(config: Config | None = None, *, clock: Clock = utcnow, migrate: bool = True, llm_client=None,
                  queue: JobQueue | None = None, http_transport=None, preview_uploader=None) -> Runtime:
    config = config or get_config()
    settings = config.settings
    install_query_guard()  # every tenant-table query must filter by org_id
    if migrate:
        upgrade(settings.database_url, settings.data_path)
    engine = make_engine(settings.database_url)
    sessions = make_sessionmaker(engine)
    catalogue = Catalogue.load()
    catalogue.sync(sessions)
    files = FileStore(settings.data_path)
    notifier = Notifier(settings.notify.webhook, settings.dry_run, files, clock)
    board = Board(sessions, config, catalogue, notifier, clock)
    meter = Meter(sessions, config, clock, client=llm_client)
    offices = Offices(sessions, catalogue, clock)
    atlas = Atlas(sessions, config, catalogue, board, meter, files, notifier, queue or InlineQueue(), clock)
    integrations = Integrations(sessions, config, transport=http_transport, uploader=preview_uploader, clock=clock)
    atlas.integrations = integrations
    return Runtime(config, engine, sessions, catalogue, board, meter, files, notifier, offices, atlas, integrations)
