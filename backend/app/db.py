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
    connect_args = {}
    if url.startswith("sqlite"):
        # SQLite allows a single writer. A long orchestrator build and a
        # concurrent research job would otherwise fail immediately with
        # "database is locked"; wait for the lock instead of dropping work.
        connect_args = {"check_same_thread": False, "timeout": 60}
    eng = create_engine(url, future=True, connect_args=connect_args)
    if url.startswith("sqlite") and ":memory:" not in url:
        # Write-ahead logging: readers never block the writer and the writer
        # never blocks readers. Without it a long portal build and the API
        # serving the applicant contend for one lock, and a build that takes
        # a lock mid-flush can strand the other in PendingRollbackError
        # (observed 2026-08-02: a global sweep killed by a concurrent
        # single-family build). Also the single biggest cheap speedup for a
        # run that writes a checkpoint at every step.
        from sqlalchemy import event

        @event.listens_for(eng, "connect")
        def _sqlite_pragmas(dbapi_conn, _rec):  # pragma: no cover - driver hook
            cur = dbapi_conn.cursor()
            try:
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.execute("PRAGMA busy_timeout=60000")
            finally:
                cur.close()
    return eng


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def create_all(bind=None):
    # Import models so metadata is populated before create_all.
    from . import models  # noqa: F401
    from .visa_snapshot import models as snapshot_models  # noqa: F401
    from .adapter_factory import models as factory_models  # noqa: F401
    from .global_routes import models as global_models  # noqa: F401
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
