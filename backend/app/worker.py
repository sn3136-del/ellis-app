"""Background worker: appointment monitoring + resumable-case advancement.

Runs OUT of the API process. It holds no in-memory workflow state — every tick
it loads durable state from the DB, so it (and the API) can be restarted at any
time without losing or corrupting a case. This is what makes the "restart the
worker and the workflow stays consistent" property real here.

Production: replace the poll loop with a Temporal worker (TEMPORAL_HOST) whose
activities are the same step functions. Appointment polling uses adapter-safe
intervals with jitter/backoff.
"""
from __future__ import annotations

import random
import time

from sqlalchemy import select

from .db import SessionLocal
from . import models, service


def _jitter(base_ms: int) -> float:
    return (base_ms / 1000.0) * (0.8 + 0.4 * random.random())


def tick_once(db) -> int:
    """Advance every non-terminal, non-paused case one step; return count moved."""
    rows = db.execute(select(models.VisaApplication).where(
        models.VisaApplication.state.notin_(["COMPLETED", "CANCELLED", "MANUAL_REVIEW_REQUIRED"]))).scalars().all()
    moved = 0
    for app_row in rows:
        exec_row = db.execute(select(models.WorkflowExecution).where(
            models.WorkflowExecution.application_id == app_row.id)).scalar_one_or_none()
        # Only auto-advance cases NOT waiting on a human.
        if exec_row and exec_row.pending:
            continue
        try:
            _, wf = service.signal(db, app_row.id, "start")
            moved += 1
        except Exception:
            continue
    return moved


def run_forever(interval_seconds: float = 30.0):  # pragma: no cover - long-running
    while True:
        db = SessionLocal()
        try:
            tick_once(db)
        finally:
            db.close()
        time.sleep(_jitter(int(interval_seconds * 1000)))


if __name__ == "__main__":  # pragma: no cover
    run_forever()
