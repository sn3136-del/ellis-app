"""Attempt the canonical dataset each six-hour cycle with four bounded workers.

Only the coordinator owns run accounting and its atomic status file. Each
worker opens a separate database session and reads its own row. The process
lock stays held until every worker has stopped; cancelled, partial and failed
checks never become invented verification or hidden backlog.
"""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
import fcntl
import json
import logging
import os
from pathlib import Path
import signal
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("freshness-sweep")
MAX_ROWS = 5000
MAX_SECONDS = 5 * 60 * 60
WORKERS = 4
ROUTE_SECONDS = 75.0
SPACING_SECONDS = 2.0  # global dispatch spacing; never four new requests at once
HEARTBEAT_SECONDS = 30.0
DUE_AFTER_HOURS = 0.25  # exclude only very recent on-demand duplicate reads
_COUNTS = ("attempted", "read", "verified", "renewed", "partial", "corrected", "disputed",
           "unreadable", "skipped_pending", "deferred", "errors", "insufficient_evidence",
           "provider_failed", "no_official_source", "source_reads", "source_fetch_failures")


def _utc():
    return datetime.now(timezone.utc).isoformat()


def _write_status(path: Path, status: dict) -> None:
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", delete=False) as f:
            temporary = f.name
            json.dump(status, f, ensure_ascii=False)
            f.write("\n"); f.flush(); os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def _load_row(db, key):
    from sqlalchemy import select
    from app.visa_snapshot.models import KimiRouteGuidanceCache
    return db.execute(select(KimiRouteGuidanceCache).where(
        KimiRouteGuidanceCache.cache_key == key)).scalars().first()


def _check_route(key: str, deadline: float, stop: threading.Event) -> dict:
    """No ORM row/session or shared stats cross the worker boundary."""
    from app.db import SessionLocal
    from app.visa_snapshot import freshness, kimi_primary, verified_overrides
    from app.visa_snapshot.records_guard import grounded_verdict_supported
    delta = {k: 0 for k in _COUNTS}
    db = None
    report = None
    try:
        if stop.is_set() or time.monotonic() >= deadline:
            delta["deferred"] = 1
            return delta
        db = SessionLocal()
        row = _load_row(db, key)
        if row is None:
            delta["deferred"] = 1
            return delta
        delta["attempted"] = 1
        report = freshness.recheck_row(db, row, budget_seconds=min(ROUTE_SECONDS,
            max(0.0, deadline - time.monotonic())), should_stop=stop.is_set) or {}
        outcome = report.get("outcome")
        # Reading an official page and proving a route are distinct events.
        # Even an interrupted comparison can have completed real source reads.
        for counter in ("source_reads", "source_fetch_failures"):
            value = report.get(counter)
            delta[counter] = value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
        delta["read"] = int(delta["source_reads"] > 0 or outcome == "checked")
        if outcome in ("detail_pending", "noncanonical"):
            delta["attempted"] = 0
            delta["skipped_pending"] = 1
        elif outcome in ("cancelled", "budget_exhausted", "concurrent_change"):
            delta["deferred"] = 1
        elif outcome == "checked":
            check = freshness.effective_check(row.verification)
            guidance, _ = verified_overrides.apply(dict(row.guidance or {}), dict(row.route or {}))
            if (grounded_verdict_supported(check) and not kimi_primary.serve_time_invariants(guidance)
                    and not freshness.active_disputed_fields(db, key)):
                delta["verified"] = 1
            delta["renewed"] = int(check.get("renewed") is True)
            delta["partial"] = int(bool(check.get("unverified_fields") or check.get("unchecked_sources")))
        elif outcome == "fetch_failed" and not delta["read"]:
            delta["unreadable"] = 1
            freshness.note_unreadable(db, row, report)
        elif outcome == "page_not_relevant":
            delta["insufficient_evidence"] = 1
        elif outcome == "provider_error":
            delta["provider_failed"] = 1
        elif outcome == "no_official_source":
            delta["no_official_source"] = 1
        delta["corrected"] = int(bool(report.get("changed")))
        delta["disputed"] = int(bool(report.get("disputed") or report.get("generic_skipped")))
    except Exception as exc:
        if db is not None:
            db.rollback()
        delta["errors"] = 1
        delta["last_error"] = {"route_key": key, "message": str(exc)[:300], "at": _utc()}
        log.warning("recheck failed for %s: %s", key, exc)
    finally:
        if db is not None:
            db.close()
    return delta


def _run_workers(keys: list[str], deadline: float, stop: threading.Event, status: dict, save) -> set[str]:
    """Bound both concurrency and submitted work; coordinator alone writes stats."""
    pending = {}
    cursor = 0
    attempted_keys = set()
    next_dispatch = 0.0
    # shutdown(wait=True) is intentional: keep the run lock until every
    # worker has committed/rolled back and closed its private session.
    with ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="freshness") as executor:
        while pending or cursor < len(keys):
            now = time.monotonic()
            if now >= deadline and not stop.is_set():
                status["state"] = "budget_exhausted"
                stop.set()
            if stop.is_set() and status["state"] == "running":
                status["state"] = "interrupted"
            accepting = not stop.is_set()
            if accepting and cursor < len(keys) and len(pending) < WORKERS and now >= next_dispatch:
                key = keys[cursor]
                pending[executor.submit(_check_route, key, deadline, stop)] = key
                cursor += 1
                status["scheduled"] = cursor
                status["in_flight"] = len(pending)
                next_dispatch = now + SPACING_SECONDS
                save()
                continue
            if not pending:
                if not accepting or cursor >= len(keys):
                    break
                stop.wait(min(HEARTBEAT_SECONDS, max(0.001, next_dispatch - now)))
                continue
            timeout = HEARTBEAT_SECONDS
            if accepting:
                timeout = min(timeout, max(0.001, deadline - now))
                if cursor < len(keys) and len(pending) < WORKERS:
                    timeout = min(timeout, max(0.001, next_dispatch - now))
            done, _ = wait(pending, timeout=timeout, return_when=FIRST_COMPLETED)
            for future in done:
                key = pending.pop(future)
                try:
                    delta = future.result()
                except BaseException as exc:
                    stop.set()
                    status["state"] = "failed"
                    delta = {"errors": 1, "last_error": {"route_key": key,
                        "message": type(exc).__name__ + ": " + str(exc)[:250], "at": _utc()}}
                for counter in _COUNTS:
                    status[counter] += delta.get(counter, 0)
                if delta.get("attempted"):
                    attempted_keys.add(key)
                if delta.get("last_error"):
                    status["last_error"] = delta["last_error"]
                status["completed"] += 1
                status["last_progress_at"] = _utc()
            status["in_flight"] = len(pending)
            status["cycle_unattempted"] = status["selected"] - status["attempted"]
            save()  # completion or honest coordinator heartbeat while workers run
    return attempted_keys


def main() -> int:
    from app.db import SessionLocal
    from app.visa_snapshot import freshness
    path = freshness.sweep_status_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = path.with_suffix(".lock").open("a+")
    except OSError:
        log.exception("cannot create freshness sweep lock/status directory")
        return 1
    previous_handlers = {}
    stop = threading.Event()
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.info("another sweep is running; this invocation is skipped")
            return 0
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGTERM, signal.SIGINT):
                previous_handlers[sig] = signal.signal(sig, lambda _sig, _frame: stop.set())
        started = time.monotonic()
        deadline = started + MAX_SECONDS
        status = {"schema_version": 2, "running": True, "state": "running", "process_id": os.getpid(),
            "started_at": _utc(), "finished_at": None, "updated_at": _utc(), "last_progress_at": None,
            "elapsed_seconds": 0, "target_cycle_hours": 6, "due_after_hours": DUE_AFTER_HOURS,
            "time_budget_seconds": MAX_SECONDS, "route_budget_seconds": ROUTE_SECONDS,
            "row_limit": MAX_ROWS, "workers": WORKERS, "due_before": None, "selected": 0,
            "scheduled": 0, "completed": 0, "in_flight": 0, "cycle_unattempted": 0,
            "integrity_violations": 0, "integrity_resolved": 0, "backlog_remaining": None,
            "last_error": None, **{key: 0 for key in _COUNTS}}
        result = 0
        attempted_keys = set()
        def save():
            status["updated_at"] = _utc()
            status["elapsed_seconds"] = round(time.monotonic() - started, 3)
            try:
                _write_status(path, status)
            except OSError:
                log.exception("cannot write freshness sweep status")
        save()
        db = None
        try:
            db = SessionLocal()
            integrity = freshness.audit_integrity(db)
            status["integrity_violations"] = integrity["violated"]
            status["integrity_resolved"] = integrity.get("resolved", 0)
            due = freshness.due_rows(db, older_than_hours=DUE_AFTER_HOURS, limit=10**9)
            status["due_before"] = len(due)
            keys = [row.cache_key for row in due[:MAX_ROWS]]
            db.close(); db = None  # no coordinator transaction survives worker dispatch
            status["selected"] = status["cycle_unattempted"] = len(keys)
            save()
            log.info("sweep: %d due, %d selected, %d workers", status["due_before"], len(keys), WORKERS)
            attempted_keys = _run_workers(keys, deadline, stop, status, save)
            if status["state"] == "running":
                status["state"] = "complete_with_errors" if status["errors"] else "complete"
            if status["errors"] or status["state"] in {"failed", "interrupted"}:
                result = 1
        except BaseException as exc:
            stop.set()
            result = 1
            status["state"] = "failed"
            status["last_error"] = {"message": type(exc).__name__ + ": " + str(exc)[:250], "at": _utc()}
            if db is not None:
                db.rollback()
            log.exception("freshness sweep failed")
            if not isinstance(exc, Exception):
                raise
        finally:
            if db is not None:
                db.close()
            # A new session sees worker commits and newly created/deleted rows.
            final_db = None
            try:
                final_db = SessionLocal()
                eligible = freshness.due_rows(final_db, older_than_hours=DUE_AFTER_HOURS, limit=10**9)
                status["eligible_now"] = len(eligible)
                status["backlog_remaining"] = sum(row.cache_key not in attempted_keys for row in eligible)
            except Exception as exc:
                status["last_error"] = {"message": "backlog count failed: " + str(exc)[:250], "at": _utc()}
            finally:
                if final_db is not None:
                    final_db.close()
            status["cycle_unattempted"] = status["selected"] - status["attempted"]
            status["running"] = False
            status["finished_at"] = _utc()
            save()
        log.info("sweep %s: %d attempts, %d reads, %d verified, %d cycle routes unattempted, %s currently due",
            status["state"], status["attempted"], status["read"], status["verified"],
            status["cycle_unattempted"], status["backlog_remaining"])
        return result
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
