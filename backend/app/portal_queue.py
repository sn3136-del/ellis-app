"""Durable background execution for live portal work.

The applicant's click records a PortalRun row and the HTTP response returns
immediately; an executor (an in-process thread pool here, plus the worker
process) claims the run atomically and drives the same durable workflow the
synchronous path uses — service.signal — outside any HTTP request.

Guarantees:
- at most ONE queued/running run per case (repeated clicks reuse it, so
  duplicate portal runs are structurally impossible);
- claims are atomic (UPDATE ... WHERE status='queued'), so the API executor
  and the worker process can never both drive one run;
- leases renew on every real checkpoint; an expired lease marks the run
  STALLED (visible to the applicant with a safe Retry) instead of silently
  spinning;
- a run may enter SUBMITTING only when it was enqueued with allow_submit —
  set exclusively for the applicant's explicit post-final-review confirmation;
- OTP/verification tokens never persist in the queue: they travel as a vault
  reference revealed once and destroyed immediately.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from . import models, progress, vault
from .db import SessionLocal

LEASE_SECONDS = 180          # renewed on every checkpoint
STALL_AFTER_SECONDS = 120    # no checkpoint for this long -> shown as stalled

_WAKE = threading.Event()
_STOP = threading.Event()
_THREADS: list[threading.Thread] = []


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def active_run(db, application_id: str) -> models.PortalRun | None:
    return db.execute(select(models.PortalRun).where(
        models.PortalRun.application_id == application_id,
        models.PortalRun.status.in_(("queued", "running"))).order_by(
        models.PortalRun.created_at.desc())).scalars().first()


def latest_run(db, application_id: str) -> models.PortalRun | None:
    return db.execute(select(models.PortalRun).where(
        models.PortalRun.application_id == application_id).order_by(
        models.PortalRun.created_at.desc())).scalars().first()


def enqueue(db, *, app_row, signal_name: str, kwargs: dict | None = None,
            allow_submit: bool = False) -> tuple[models.PortalRun, bool]:
    """Record the applicant's signal exactly once. A second click while a run
    is queued/running returns the existing run — the idempotency safeguard."""
    existing = active_run(db, app_row.id)
    if existing is not None:
        return existing, False
    kwargs = dict(kwargs or {})
    # Verification tokens must not persist in plaintext: vault them and pass
    # only the reference; the executor reveals once and destroys.
    if kwargs.get("token"):
        stored = vault.store(str(kwargs.pop("token")), {"kind": "one_time_token"})
        kwargs["token_ref"] = stored["ref"]
    run = models.PortalRun(org_id=app_row.org_id, application_id=app_row.id,
                           status="queued", signal_name=signal_name,
                           signal_kwargs=kwargs, allow_submit=bool(allow_submit),
                           current_step_key="queued")
    db.add(run)
    db.add(models.CaseProgressEvent(application_id=app_row.id,
                                    step_key="queued", status="active"))
    db.commit()
    _WAKE.set()
    return run, True


def record_event(db, application_id: str, step_key: str, status: str = "active"):
    last = db.execute(select(models.CaseProgressEvent).where(
        models.CaseProgressEvent.application_id == application_id).order_by(
        models.CaseProgressEvent.at.desc(),
        models.CaseProgressEvent.id.desc())).scalars().first()
    if last is not None and last.step_key == step_key and last.status == status:
        return  # no duplicate spam for repeated identical checkpoints
    db.add(models.CaseProgressEvent(application_id=application_id,
                                    step_key=step_key, status=status))
    db.commit()


def _make_progress_sink(application_id: str, run_id: str):
    """Applicant-safe progress recorder: step key + status only. Also renews
    the run's lease so genuine progress can never be misread as a stall."""
    def sink(step_key: str, status: str = "active"):
        db = SessionLocal()
        try:
            run = db.get(models.PortalRun, run_id)
            if run is not None:
                run.current_step_key = step_key
                run.last_checkpoint_at = _now()
                run.lease_expires_at = _now() + timedelta(seconds=LEASE_SECONDS)
            record_event(db, application_id, step_key, status)
        except Exception:  # noqa: BLE001 — progress must never break the run
            db.rollback()
        finally:
            db.close()
    return sink


def _claim(db, run_id: str, worker_id: str) -> bool:
    res = db.execute(update(models.PortalRun).where(
        models.PortalRun.id == run_id,
        models.PortalRun.status == "queued").values(
        status="running", claimed_by=worker_id, started_at=_now(),
        lease_expires_at=_now() + timedelta(seconds=LEASE_SECONDS),
        last_checkpoint_at=_now(),
        attempts=models.PortalRun.attempts + 1))
    db.commit()
    return bool(getattr(res, "rowcount", 0) == 1)


def claim_next(db, worker_id: str) -> str | None:
    rows = db.execute(select(models.PortalRun).where(
        models.PortalRun.status == "queued").order_by(
        models.PortalRun.created_at.asc())).scalars().all()
    for run in rows:
        if _claim(db, run.id, worker_id):
            return run.id
    return None


def expire_stale_leases(db) -> int:
    """Running runs whose lease lapsed (dead executor, hung call past every
    timeout): mark STALLED so the applicant sees the honest message + Retry
    instead of an endless spinner. Never auto-retried."""
    n = 0
    rows = db.execute(select(models.PortalRun).where(
        models.PortalRun.status == "running")).scalars().all()
    for run in rows:
        exp = _aware(run.lease_expires_at)
        if exp is not None and exp < _now():
            run.status = "stalled"
            run.finished_at = _now()
            run.error = "no response from the official portal"
            record_event(db, run.application_id, "stalled", "failed")
            n += 1
    if n:
        db.commit()
    return n


_SAFE_GATE_ERRORS = {
    "RealOnlyStop": "This route is not yet available for live execution.",
    "MockAsProductionError": "This route is not yet available for live execution.",
    "PreparationOnlyMode": "Required information or readiness checks are still missing.",
    "PassportBlocked": "The passport on this case blocks portal execution.",
}


def execute_run(run_id: str, worker_id: str) -> None:
    """Drive one claimed run to its next pause/terminal state. All workflow
    mutation happens here — never in the HTTP request."""
    from . import service

    db = SessionLocal()
    try:
        run = db.get(models.PortalRun, run_id)
        if run is None or run.status != "running":
            return
        application_id = run.application_id
        kwargs = dict(run.signal_kwargs or {})
        token_ref = kwargs.pop("token_ref", None)
        if token_ref:
            try:
                kwargs["token"] = vault.reveal(token_ref)
            except KeyError:
                pass
            finally:
                vault.destroy(token_ref)   # one-time use, gone immediately
        sink = _make_progress_sink(application_id, run_id)
        sink("connecting", "active")
        try:
            status, wf = service.signal(
                db, application_id, run.signal_name,
                progress_sink=sink, block_submit=not run.allow_submit, **kwargs)
        except Exception as e:  # noqa: BLE001 — typed gate errors, sanitized
            db.rollback()
            run = db.get(models.PortalRun, run_id)
            run.status = "failed"
            run.finished_at = _now()
            run.error = _SAFE_GATE_ERRORS.get(type(e).__name__,
                                              "portal execution could not continue")
            record_event(db, application_id, "recoverable_failure", "failed")
            db.commit()
            return
        run = db.get(models.PortalRun, run_id)
        state = status.get("state", "")
        if status.get("pending"):
            run.status = "waiting_applicant"
            step = progress.step_for_state(state, status.get("pending"))
            run.current_step_key = step
            record_event(db, application_id, step, "handoff")
        elif state == "RECOVERABLE_FAILURE":
            run.status = "failed"
            run.error = "the official portal did not respond as expected"
            record_event(db, application_id, "recoverable_failure", "failed")
        else:
            run.status = "completed"
            step = progress.step_for_state(state, None)
            run.current_step_key = step
            record_event(db, application_id, step,
                         "done" if state != "MANUAL_REVIEW_REQUIRED" else "failed")
        run.finished_at = _now()
        db.commit()
    finally:
        db.close()


def run_pending_once(worker_id: str = "worker") -> int:
    """Claim-and-execute every queued run (used by the worker process tick and
    by tests). Returns the number of runs executed."""
    executed = 0
    while True:
        db = SessionLocal()
        try:
            expire_stale_leases(db)
            run_id = claim_next(db, worker_id)
        finally:
            db.close()
        if run_id is None:
            return executed
        execute_run(run_id, worker_id)
        executed += 1


def _executor_loop(worker_id: str):  # pragma: no cover - thread loop
    while not _STOP.is_set():
        _WAKE.wait(timeout=3.0)
        _WAKE.clear()
        try:
            run_pending_once(worker_id)
        except Exception:  # noqa: BLE001 — the loop must survive anything
            pass


def start_executor(threads: int = 2) -> None:
    """In-process executor threads (API process). Test runtime never starts
    them — tests drive runs explicitly via run_pending_once."""
    from .config import settings
    if settings().runtime_mode == "test" or _THREADS:
        return
    for i in range(threads):
        t = threading.Thread(target=_executor_loop, args=(f"api-exec-{i}",),
                             daemon=True, name=f"ellis-portal-exec-{i}")
        t.start()
        _THREADS.append(t)


def retry_available(db, app_row, exec_row) -> bool:
    """A safe applicant-triggered retry exists when the last run stalled or
    failed, or the case sits in RECOVERABLE_FAILURE — and no run is active.
    Irreversible steps stay guarded downstream by reconcile-before-act,
    allow_submit, and the lost-session irreversibility check."""
    if active_run(db, app_row.id) is not None:
        return False
    state = exec_row.state if exec_row is not None else app_row.state
    if state in ("COMPLETED", "CANCELLED", "MANUAL_REVIEW_REQUIRED"):
        return False
    run = latest_run(db, app_row.id)
    if run is not None and run.status in ("stalled", "failed"):
        return True
    return state == "RECOVERABLE_FAILURE"
