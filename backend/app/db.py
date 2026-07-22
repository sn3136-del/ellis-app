"""Database engine + session. SQLite for dev/test, Postgres/Neon in production
via DATABASE_URL. Schema is created from the ORM metadata; Alembic migrations
are used when installed (see migrations/)."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


def _make_engine(url: str | None = None):
    url = url or settings().database_url
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, future=True, connect_args=connect_args)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def create_all(bind=None):
    # Import models so metadata is populated before create_all.
    from . import models  # noqa: F401
    Base.metadata.create_all(bind or engine)


def get_session():
    """FastAPI dependency — yields a session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
