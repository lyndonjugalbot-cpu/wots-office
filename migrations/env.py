"""Alembic environment: uses the app's engine factory so relative SQLite paths resolve the same way."""
import os

from alembic import context
from dotenv import load_dotenv

from wots.core.config import ROOT
from wots.core.db import make_engine
from wots.core.models import Base

load_dotenv(ROOT / ".env", override=False)  # data migrations read WOTS_OWNER_EMAIL / WOTS_OWNER_NAME
config = context.config
target_metadata = Base.metadata


def run_migrations() -> None:
    # wots.core.db.upgrade() passes the runtime's URL (which already honours DATABASE_URL); the plain
    # `alembic` command uses DATABASE_URL, then alembic.ini
    url = config.attributes.get("url") or os.environ.get("DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    engine = make_engine(url)
    with engine.connect() as connection:
        # render_as_batch lets SQLite handle ALTER TABLE in future migrations
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata,
                      literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    run_migrations()
