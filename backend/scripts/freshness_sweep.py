"""Attempt the canonical dataset each six-hour cycle with four bounded workers.

Only the coordinator owns run accounting and its atomic status file. Each
worker opens a separate database session and reads its own row. The process
lock stays held until every worker has stopped; cancelled, partial and failed
checks never become invented verification or hidden backlog.
"""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
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
           "provider_failed", "no_official_source", "source_reads", "source_fetch_failures",
           "model_comparisons", "model_comparisons_reused", "provider_suspended",
           "provider_rate_limited")


def _utc():
    return datetime.now(timezone.utc).isoformat()


def _timestamp(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _positive_backlog(previous) -> bool:
    backlog = previous.get('backlog_remaining')
    return isinstance(backlog, int) and not isinstance(backlog, bool) and backlog > 0


def _continuation(previous, now):
    """Resume only a stopped cycle whose original five-hour budget remains.

    The process lock is already held, so a persisted 'running' state means
    its writer is gone. Counters remain per invocation; no old evidence is
    relabelled as a new check. An expired cycle starts normally.

    A cycle that finished dispatch but left a positive backlog is resumed
    too, inside the same original budget: on 9 September 2026 an invocation
    ended "complete" with 61 rows whose checks had been discarded by the
    JSON comparison defect, and the only way to retry them was a fresh
    cycle that would have bought hundreds of recently completed checks
    again. The continuation selects exactly the rows with no recorded
    attempt in the cycle, so a completed cycle with nothing left, or one
    whose deadline has passed, still starts normally.
    """
    if not isinstance(previous, dict):
        return None
    state = previous.get('state')
    finished_with_backlog = (state in {'complete', 'complete_with_errors'}
                             and _positive_backlog(previous))
    if state not in {'running', 'interrupted', 'failed', 'budget_exhausted', 'provider_suspended',
                     'rate_limited'} \
            and not finished_with_backlog:
        return None
    if previous.get('schema_version') not in (2, 3):
        return None
    if finished_with_backlog and previous.get('schema_version') != 3:
        return None          # only a checkpointed cycle knows its own start
    start = _timestamp(previous.get('cycle_started_at') or previous.get('started_at'))
    budget = previous.get('cycle_time_budget_seconds', previous.get('time_budget_seconds'))
    if isinstance(budget, bool) or not isinstance(budget, (int, float)) or not 0 < budget <= MAX_SECONDS:
        return None
    if start is None or start > now or now >= start + timedelta(seconds=budget):
        return None
    # Scheduling position is bookkeeping only: it never extends the original
    # deadline. Legacy checkpoints have no count and begin a fair block at one.
    scheduled = previous.get('cycle_scheduled', 0)
    if isinstance(scheduled, bool) or not isinstance(scheduled, int) or scheduled < 0:
        scheduled = 0
    plan = {'prior_cycle_scheduled': scheduled, 'cycle_started_at': start.isoformat(), 'cycle_time_budget_seconds': budget,
            'remaining_seconds': (start + timedelta(seconds=budget) - now).total_seconds(),
            'resumed_from_started_at': previous.get('started_at'),
            'prior_attempt_results': previous.get('attempted', 0)}
    if finished_with_backlog:
        plan['continuation_reason'] = 'completed_with_backlog'
        plan['prior_backlog_remaining'] = previous['backlog_remaining']
    return plan


def _remaining_cycle_rows(rows, cycle_started_at, now):
    """Skip recorded attempts already made in this cycle, not unverified facts.

    Use the original due cutoff, also supporting a pre-checkpoint worker.
    A cancelled in-flight check may be retried. Other attempted outcomes,
    including unreadable or insufficient evidence, wait for the next cycle.
    Newly created/changed rows without a recorded attempt remain eligible.
    """
    start = _timestamp(cycle_started_at)
    cutoff = start - timedelta(hours=DUE_AFTER_HOURS)
    remaining = []
    for row in rows:
        verification = getattr(row, 'verification', None)
        check = verification.get('grounded_check') if isinstance(verification, dict) else None
        check = check if isinstance(check, dict) else {}
        at = _timestamp(check.get('at'))
        if (at is None or at > now or at < cutoff or
                (check.get('outcome') == 'cancelled' and at >= start)):
            remaining.append(row)
    return remaining


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
        for counter in ("source_reads", "source_fetch_failures", "model_comparisons", "model_comparisons_reused"):
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
            pending = bool((row.verification or {}).get("detail_pending"))
            delta["renewed"] = int(check.get("renewed") is True and not pending)
            delta["partial"] = int(bool(pending or check.get("unverified_fields") or check.get("unchecked_sources")))
        elif outcome == "fetch_failed" and not delta["read"]:
            delta["unreadable"] = 1
            freshness.note_unreadable(db, row, report)
        elif outcome == "page_not_relevant":
            delta["insufficient_evidence"] = 1
        elif outcome == "provider_error":
            delta["provider_failed"] = 1
        elif outcome == "no_official_source":
            delta["no_official_source"] = 1
        # Provider state belongs to the cycle, independently of this route's
        # final outcome. One supported page followed by a provider failure
        # can return checked (partial); a failure using the remaining route
        # budget can return budget_exhausted. Neither should hide an active
        # account suspension or keep dispatching source reads after it.
        suspended = kimi_primary.provider_suspension()
        if suspended:
            delta["provider_suspended"] = 1
            delta["provider_notice"] = suspended.get("reason") or "provider account suspended"
        elif kimi_primary.rate_gate_saturated():
            delta["provider_rate_limited"] = 1
            delta["provider_notice"] = ("AI provider rate limit persisted at the "
                                        f"{int(kimi_primary.RATE_GATE_MAX_SECONDS)} second cap")
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
                if (delta.get("provider_suspended") or delta.get("provider_rate_limited")) and not stop.is_set():
                    # Stop dispatching: the provider account is suspended, or
                    # its rate limit is not clearing. Routes not attempted
                    # stay due and are picked up by the next cycle.
                    status["state"] = "provider_suspended" if delta.get("provider_suspended") else "rate_limited"
                    status["provider_notice"] = str(delta.get("provider_notice") or
                                                    "provider account suspended")[:240]
                    status["provider_suspended_at"] = _utc()
                    log.warning("sweep stopped: %s", status["provider_notice"])
                    stop.set()
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
    from app.visa_snapshot import freshness, freshness_priority, consistency_runtime
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
        # Preserve the actual previous invocation before replacing its status.
        # A corrupt status is not trusted as a continuation instruction.
        previous = None
        try:
            if path.is_file():
                if path.stat().st_size > 128 * 1024:
                    raise ValueError('oversized sweep status')
                previous = json.loads(path.read_text(encoding='utf-8'))
                if isinstance(previous, dict):
                    _write_status(path.with_suffix('.previous.json'), previous)
        except (OSError, ValueError, TypeError):
            log.exception('cannot preserve or validate prior sweep status')
            return 1
        continuation = _continuation(previous, datetime.now(timezone.utc))
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGTERM, signal.SIGINT):
                previous_handlers[sig] = signal.signal(sig, lambda _sig, _frame: stop.set())
        started = time.monotonic()
        budget = continuation['remaining_seconds'] if continuation else MAX_SECONDS
        deadline = started + budget
        status = {"schema_version": 3, "running": True, "state": "running", "process_id": os.getpid(),
            "started_at": _utc(), "finished_at": None, "updated_at": _utc(), "last_progress_at": None,
            "elapsed_seconds": 0, "target_cycle_hours": 6, "due_after_hours": DUE_AFTER_HOURS,
            "time_budget_seconds": budget, "route_budget_seconds": ROUTE_SECONDS,
            "row_limit": MAX_ROWS, "workers": WORKERS, "due_before": None, "selected": 0,
            "scheduled": 0, "completed": 0, "in_flight": 0, "cycle_unattempted": 0,
            "integrity_violations": 0, "integrity_resolved": 0, "backlog_remaining": None,
            "last_error": None, **{key: 0 for key in _COUNTS},
            "queue_priority_policy": freshness_priority.POLICY_ID,
            "queue_priority_basis": "User and Trip.com market priorities; not measured traffic",
            "oldest_dispatch_every": freshness_priority.OLDEST_EVERY,
            "prior_cycle_scheduled": 0, "cycle_scheduled": 0}
        status.update({k:v for k,v in continuation.items() if k != 'remaining_seconds'} if continuation else {
            'cycle_started_at': status['started_at'], 'cycle_time_budget_seconds': MAX_SECONDS})
        result = 0
        attempted_keys = set()
        checkpoint_failed = False
        def save():
            nonlocal checkpoint_failed
            status["updated_at"] = _utc()
            status["cycle_scheduled"] = status["prior_cycle_scheduled"] + status["scheduled"]
            status["elapsed_seconds"] = round(time.monotonic() - started, 3)
            try:
                _write_status(path, status)
                return True
            except OSError:
                checkpoint_failed = True
                stop.set()
                status['state'] = 'failed'
                status['last_error'] = {'message': 'Unable to persist sweep checkpoint; dispatch stopped.', 'at': _utc()}
                log.exception("cannot write freshness sweep status")
                return False
        if not save():
            return 1  # no integrity audit or paid work without a durable budget
        db = None
        try:
            db = SessionLocal()
            # Deterministic diagnostics run before any paid source comparison,
            # inside the same deadline. They never file issues or change facts.
            def report_progress(summary):
                status["consistency"] = summary
                save()
            status["consistency"] = consistency_runtime.run_report(
                db, status_path=path, deadline=deadline, should_stop=stop.is_set,
                previous=(previous or {}).get("consistency"), on_progress=report_progress)
            if status["consistency"]["state"] == "failed":
                # A read error must not leave the coordinator transaction
                # unusable for its existing source/integrity work.
                db.rollback()
            if not save():
                raise OSError("Unable to persist consistency status; dispatch stopped")
            integrity = freshness.audit_integrity(db)
            status["integrity_violations"] = integrity["violated"]
            status["integrity_resolved"] = integrity.get("resolved", 0)
            due = freshness.due_rows(db, older_than_hours=0 if continuation else DUE_AFTER_HOURS, limit=10**9)
            if continuation:
                due = _remaining_cycle_rows(due, status['cycle_started_at'], datetime.now(timezone.utc))
            status["due_before"] = len(due)
            ordered = freshness_priority.prioritize_due_rows(
                due, now=datetime.now(timezone.utc),
                dispatch_offset=status['prior_cycle_scheduled'])
            keys = [row.cache_key for row in ordered[:MAX_ROWS]]
            db.close(); db = None  # no coordinator transaction survives worker dispatch
            status["selected"] = status["cycle_unattempted"] = len(keys)
            save()
            log.info("sweep: %d due, %d selected, %d workers", status["due_before"], len(keys), WORKERS)
            if continuation:
                log.info('continuing cycle from %s with %.1f seconds of original budget remaining (%s)',
                         status['cycle_started_at'], budget,
                         continuation.get('continuation_reason') or 'stopped cycle')
            attempted_keys = _run_workers(keys, deadline, stop, status, save)
            if status["state"] == "running":
                status["state"] = ("interrupted" if stop.is_set() else
                    "complete_with_errors" if status["errors"] else "complete")
            if status["errors"] or status["state"] in {"failed", "interrupted", "provider_suspended", "rate_limited"}:
                # A suspended provider exits non-zero on purpose: systemd
                # records the failure, which is the alert an operator sees.
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
                eligible = freshness.due_rows(final_db, older_than_hours=0 if continuation else DUE_AFTER_HOURS, limit=10**9)
                if continuation:
                    eligible = _remaining_cycle_rows(eligible, status['cycle_started_at'], datetime.now(timezone.utc))
                status["eligible_now"] = len(eligible)
                status["backlog_remaining"] = (len(eligible) if continuation else
                    sum(row.cache_key not in attempted_keys for row in eligible))
            except Exception as exc:
                status["last_error"] = {"message": "backlog count failed: " + str(exc)[:250], "at": _utc()}
            finally:
                if final_db is not None:
                    final_db.close()
            status["cycle_unattempted"] = status["selected"] - status["attempted"]
            status["running"] = False
            status["finished_at"] = _utc()
            save()
        if checkpoint_failed:
            result = 1
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
