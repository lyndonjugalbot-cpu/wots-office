"""Engine and sessions. Migrations live in migrations/ (Alembic)."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .config import ROOT


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite:///") and not url.startswith("sqlite:////") and url != "sqlite:///:memory:":
        # Relative SQLite paths are relative to the repo root, not the current directory
        path = ROOT / url.removeprefix("sqlite:///")
        path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{path}"
    engine = create_engine(url)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _pragmas(conn, _record):
            cur = conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")  # the dashboard can read while Atlas writes
            cur.close()

    return engine


def make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def upgrade(url: str, data_dir: Path | None = None, revision: str = "head") -> None:
    """Apply migrations (alembic upgrade head). `data_dir` lets data migrations move files."""
    from alembic import command
    from alembic.config import Config as AlembicConfig

    cfg = AlembicConfig(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(ROOT / "migrations")))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["url"] = url
    if data_dir is not None:
        cfg.attributes["data_dir"] = str(data_dir)
    command.upgrade(cfg, revision)
