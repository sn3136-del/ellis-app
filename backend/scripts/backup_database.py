"""Publish an integrity-checked SQLite backup atomically, including live WAL data.

Never fall back to copying a running database file. On failure the previous
backup remains intact and the caller receives a nonzero exit status.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time


def backup_database(database: Path, destination: Path, *, timeout: float = 60) -> dict:
    database, destination = database.resolve(), destination.resolve()
    if database == destination or not database.is_file():
        raise ValueError("Backup requires an existing database and a distinct destination")
    destination.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    fd, temporary = tempfile.mkstemp(prefix=".ellis-backup-", suffix=".db", dir=destination.parent)
    os.close(fd)
    pending = Path(temporary)
    try:
        def progress(_status, _remaining, _total):
            if time.monotonic() - started > timeout:
                raise TimeoutError("SQLite backup exceeded its time budget")

        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)) as source:
            with closing(sqlite3.connect(pending)) as target:
                source.backup(target, pages=128, progress=progress, sleep=0.1)
                result = target.execute("PRAGMA integrity_check").fetchall()
                if result != [("ok",)]:
                    raise RuntimeError("SQLite backup failed integrity validation")
                tables = target.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
                if not tables:
                    raise RuntimeError("Refusing an empty database backup")
        with pending.open("rb") as copied:
            os.fsync(copied.fileno())
        digest = hashlib.sha256(pending.read_bytes()).hexdigest()
        size = pending.stat().st_size
        os.replace(pending, destination)
        directory = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return {"state": "verified", "database": str(database), "backup": str(destination),
                "created_at": datetime.now(timezone.utc).isoformat(), "sha256": digest,
                "bytes": size, "table_count": tables, "integrity_check": "ok",
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "scope": "SQLite database including committed WAL content; not a full service recovery drill"}
    finally:
        pending.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    print(json.dumps(backup_database(args.database, args.destination, timeout=args.timeout)))
