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
    from .visa_snapshot import models as snapshot_models  # noqa: F401
    eng = bind or engine
    Base.metadata.create_all(eng)
    _ensure_columns(eng)


# SQLAlchemy create_all() creates missing TABLES but never ALTERs an existing
# table to add new columns — so a deployment upgrading an older DB would miss
# columns added over time (e.g. EmailNotification.status, StoredDocument.
# execution_class). This lightweight, idempotent additive migration ADDs any
# model column absent from the live table. It never drops/alters existing
# columns; real schema changes still belong in Alembic (migrations/).
def _ensure_columns(eng):
    from sqlalchemy import inspect, text
    insp = inspect(eng)
    existing_tables = set(insp.get_table_names())
    dialect = eng.dialect.name
    with eng.begin() as conn:
        for table_name, table in Base.metadata.tables.items():
            if table_name not in existing_tables:
                continue
            have = {c["name"] for c in insp.get_columns(table_name)}
            for col in table.columns:
                if col.name in have:
                    continue
                coltype = col.type.compile(dialect=eng.dialect)
                ddl = f'ALTER TABLE {table_name} ADD COLUMN {col.name} {coltype}'
                default = _sql_default(col, dialect)
                if default is not None:
                    ddl += f" DEFAULT {default}"
                try:
                    conn.execute(text(ddl))
                except Exception:
                    # Never fail startup on a best-effort additive migration;
                    # Alembic remains the authoritative path for hard changes.
                    pass


def _sql_default(col, dialect):
    d = getattr(col, "default", None)
    if d is None or getattr(d, "is_callable", False) or not getattr(d, "is_scalar", True):
        return None
    val = d.arg
    if isinstance(val, bool):
        return "1" if (val and dialect == "sqlite") else ("true" if val else ("false" if dialect != "sqlite" else "0"))
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, str):
        return "'" + val.replace("'", "''") + "'"
    return None


def get_session():
    """FastAPI dependency — yields a session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
