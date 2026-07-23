from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine

# Full application metadata (legacy + snapshot models).
from app.db import Base
from app import models  # noqa: F401
from app.visa_snapshot import models as snapshot_models  # noqa: F401

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    x = context.get_x_argument(as_dictionary=True)
    return (x.get("db_url")
            or os.getenv("DATABASE_URL")
            or "sqlite:///./ellis.db")


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata,
                      literal_binds=True, compare_type=True,
                      dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), future=True)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
