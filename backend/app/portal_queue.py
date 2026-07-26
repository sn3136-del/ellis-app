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

# The lease must comfortably exceed the WORST single flow node (CDP connect
# 60s + fresh session + entry-gate waits ~180s) — it renews on every
# checkpoint, so only a genuinely wedged node can let it lapse.
LEASE_SECONDS = 600
STALL_AFTER_SECONDS = 150    # no checkpoint for this long -> shown as stalled
MAX_QUEUED_PER_CASE = 3      # backlog cap: beyond this the case is "busy"

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


class CaseBusy(Exception):
    """Too many queued runs for one case — surfaced as an honest 409."""


def enqueue(db, *, app_row, signal_name: str, kwargs: dict | None = None,
            allow_submit: bool = False) -> tuple[models.PortalRun, bool]:
    """Record the applicant's signal exactly once.

    - A repeat of the SAME signal while a run for it is queued/running reuses
      that run (double-click safety).
    - A DIFFERENT signal appends a new queued run — never silently dropped;
      queued runs execute strictly one-at-a-time per case (claim_next refuses
      a case with a running sibling), so portal work never overlaps.
    """
    active = db.execute(select(models.PortalRun).where(
        models.PortalRun.application_id == app_row.id,
        models.PortalRun.status.in_(("queued", "running"))).order_by(
        models.PortalRun.created_at.asc())).scalars().all()
    for run in active:
        if run.signal_name == signal_name:
            return run, False
    if sum(1 for r in active if r.status == "queued") >= MAX_QUEUED_PER_CASE:
        raise CaseBusy("Ellis is still working on your previous action.")
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
    # TOCTOU self-heal: no DB constraint enforces the single-queue invariant,
    # so two concurrent enqueues of the same signal can both insert. The
    # LOSER (newer row) cancels itself; the survivor is returned.
    twins = db.execute(select(models.PortalRun).where(
        models.PortalRun.application_id == app_row.id,
        models.PortalRun.signal_name == signal_name,
        models.PortalRun.status.in_(("queued", "running"))).order_by(
        models.PortalRun.created_at.asc(),
        models.PortalRun.id.asc())).scalars().all()
    if len(twins) > 1 and twins[0].id != run.id:
        _destroy_token_ref(run)
        run.status = "cancelled"
        run.finished_at = _now()
        db.commit()
        return twins[0], False
    _WAKE.set()
    return run, True


def _destroy_token_ref(run) -> None:
    """One-time tokens must never outlive their run, whatever ends it."""
    ref = (run.signal_kwargs or {}).get("token_ref")
    if ref:
        try:
            vault.destroy(ref)
        except Exception:  # noqa: BLE001
            pass


def cancel_queued(db, application_id: str) -> int:
    """Cancel every queued (unclaimed) run for a case, destroying any vaulted
    one-time tokens they carry."""
    n = 0
    for run in db.execute(select(models.PortalRun).where(
            models.PortalRun.application_id == application_id,
            models.PortalRun.status == "queued")).scalars().all():
        _destroy_token_ref(run)
        run.status = "cancelled"
        run.finished_at = _now()
        n += 1
    if n:
        db.commit()
    return n


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
                if run.status != "running":
                    # This run was fenced out (stalled by lease expiry, or a
                    # cancel landed): a zombie must stop at the next node
                    # boundary instead of renewing a lease it no longer holds.
                    sink.fenced = True
                    return
                run.current_step_key = step_key
                run.last_checkpoint_at = _now()
                run.lease_expires_at = _now() + timedelta(seconds=LEASE_SECONDS)
                # Commit the lease renewal UNCONDITIONALLY — record_event may
                # dedupe (and skip its commit), and a renewal lost to the
                # session close would let a healthy run read as stalled.
                db.commit()
            record_event(db, application_id, step_key, status)
        except Exception:  # noqa: BLE001 — progress must never break the run
            db.rollback()
        finally:
            db.close()

    sink.fenced = False
    sink.should_abort = lambda: bool(getattr(sink, "fenced", False))
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
        # Strict per-case serialization: never start a run while another run
        # for the same case is (or may still be) executing.
        sibling = db.execute(select(models.PortalRun).where(
            models.PortalRun.application_id == run.application_id,
            models.PortalRun.status == "running",
            models.PortalRun.id != run.id)).scalars().first()
        if sibling is not None:
            continue
        if not _claim(db, run.id, worker_id):
            continue
        # Post-claim convergence: two executors can pass the sibling check
        # concurrently and each claim a DIFFERENT queued run of one case.
        # Deterministic tie-break — the earliest-created run wins; the loser
        # releases its claim back to the queue and moves on.
        db.expire_all()
        running = db.execute(select(models.PortalRun).where(
            models.PortalRun.application_id == run.application_id,
            models.PortalRun.status == "running").order_by(
            models.PortalRun.created_at.asc(),
            models.PortalRun.id.asc())).scalars().all()
        if len(running) > 1 and running[0].id != run.id:
            mine = db.get(models.PortalRun, run.id)
            mine.status = "queued"
            mine.claimed_by = ""
            mine.lease_expires_at = None
            db.commit()
            continue
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
            _destroy_token_ref(run)
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
        db.expire_all()
        run = db.get(models.PortalRun, run_id)
        # Fencing: while this segment ran, the run may have been marked
        # stalled (lease lapse) or a cancel may have landed. Never resurrect
        # a superseded run's status when a successor exists — the successor
        # owns the case now; our workflow results are already persisted and
        # remain valid (reconcile-before-act protects irreversibles).
        superseded = run.status not in ("running",) and active_run(db, application_id) is not None
        if superseded:
            run.finished_at = run.finished_at or _now()
            db.commit()
            return
        # A cancel that landed mid-run wins: persist_workflow already refused
        # to overwrite the CANCELLED case; the run ends as cancelled too.
        app_row = db.get(models.VisaApplication, application_id)
        if app_row is not None and app_row.state == "CANCELLED" and run.signal_name != "cancel":
            run.status = "cancelled"
            run.finished_at = _now()
            db.commit()
            return
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
        # Terminal bookkeeping the synchronous path does in the API layer:
        # execution-class record + the Trip.com case-status webhook.
        if state == "COMPLETED":
            try:
                from . import service as service_mod
                service_mod.record_terminal_execution(db, run.org_id, application_id)
            except Exception:  # noqa: BLE001 — bookkeeping never breaks a run
                pass
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
