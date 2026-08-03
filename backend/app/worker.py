"""Background worker: durable portal-run execution + stall detection.

Runs OUT of the API process. It holds no in-memory workflow state — every run
it claims is rehydrated from the DB, so it (and the API) can be restarted at
any time without losing or corrupting a case.

The worker NEVER advances a case on its own initiative: it only executes
PortalRun rows the applicant's actions enqueued (claims are atomic, so the
API-process executor and this worker can never both drive one run), and it
marks runs whose lease lapsed as STALLED so the applicant sees an honest
message with a safe Retry instead of an endless spinner. Cases waiting on a
human are untouched; submission stays gated by allow_submit + the signed
final review. Nothing here can create a duplicate portal run.
"""
from __future__ import annotations

import random
import time

from .db import SessionLocal
from . import portal_queue


def _jitter(base_ms: int) -> float:
    return (base_ms / 1000.0) * (0.8 + 0.4 * random.random())


def release_due_entry_filings(db) -> int:
    """Enqueue arrival-card filings whose destination window has now opened.

    This does not breach the no-initiative rule above. The applicant already
    pressed Continue; their instruction and the moment they gave it are
    recorded on the case (entry_preparation.schedule). All that was missing
    was a clock: Thailand will not accept a TDAC until three days before
    arrival, so the press cannot be honoured the instant it is made. Only
    cases the applicant scheduled, still untouched, and with nothing already
    in flight are released.
    """
    from . import entry_preparation
    released = 0
    for app_row in entry_preparation.due_cases(db):
        try:
            _, created = portal_queue.enqueue(db, app_row=app_row, signal_name="start")
        except Exception:  # noqa: BLE001 — one bad case must not stall the sweep
            db.rollback()
            continue
        released += int(bool(created))
    return released


def tick_once(db) -> int:
    """One worker tick: expire lapsed leases, release any arrival-card filing
    whose window just opened, then claim and execute every queued portal run.
    Returns the number of runs executed. Cases without a queued run —
    including any case waiting on a human — are never touched."""
    portal_queue.expire_stale_leases(db)
    try:
        release_due_entry_filings(db)
    except Exception:  # noqa: BLE001 — never let the sweep kill the run loop
        db.rollback()
    return portal_queue.run_pending_once("worker")


def run_forever(interval_seconds: float = 5.0):  # pragma: no cover - long-running
    while True:
        db = SessionLocal()
        try:
            tick_once(db)
        except Exception:  # noqa: BLE001 — a transient DB error must not kill the worker
            pass
        finally:
            db.close()
        time.sleep(_jitter(int(interval_seconds * 1000)))


if __name__ == "__main__":  # pragma: no cover
    run_forever()
