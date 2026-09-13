"""Durable, generation-bound ownership for staged details and source retries.

Only job metadata lives here. An expired worker cannot clear a later job or
write its answer; unsuccessful retries retain the publication pending flag.
Recovery uses the existing official-source check and its bounded budget.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import DateTime, cast, func, select, update
from sqlalchemy.orm.attributes import set_committed_value

from .models import KimiRouteGuidanceCache as Model

JOB = "detail_job"
LEASE_SECONDS = 180
MAX_ATTEMPTS = 3
WINDOW_SECONDS = 6 * 60 * 60
RETRY_SECONDS = 60
_ACTIVE = ContextVar("ellis_detail_source_lease", default=None)


def _now():
    return datetime.now(timezone.utc)


def _date(value):
    try:
        d = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def generation(value):
    d = _date(value)
    return d.isoformat() if d else None


def reserve(generated_at):
    now = _now()
    return {"version": 1, "generation": generation(generated_at), "token": uuid4().hex,
            "state": "queued", "attempts": 0, "lease_until": (now + timedelta(seconds=LEASE_SECONDS)).isoformat(),
            "created_at": now.isoformat(), "window_started_at": now.isoformat()}


def _job(row):
    ver = row.verification if isinstance(row.verification, dict) else {}
    job = ver.get(JOB)
    return job if isinstance(job, dict) and job.get("generation") == generation(getattr(row, "generated_at", None)) else {}


def _window_start(job):
    return _date(job.get("window_started_at") or job.get("created_at") or job.get("started_at"))


def _window_elapsed(job, now):
    start = _window_start(job)
    return bool(start and start + timedelta(seconds=WINDOW_SECONDS) <= now)


def retry_eligible(row, *, now=None):
    """Pure read: live owners and cooldowns block; each six-hour window is bounded."""
    now = now or _now()
    ver = row.verification if isinstance(row.verification, dict) else {}
    if not ver.get("detail_pending"):
        return False
    created = _date(getattr(row, "generated_at", None))
    if created is None:
        return False
    job = _job(row)
    if not job:
        # Legacy daemon threads have no durable metadata. Give a recently
        # generated row its original bounded worker window before takeover.
        return created + timedelta(seconds=LEASE_SECONDS) <= now
    attempts = job.get("attempts", 0)
    if type(attempts) is not int or attempts < 0:
        return False
    if job.get("state") == "complete":
        return False
    if attempts >= MAX_ATTEMPTS and not _window_elapsed(job, now):
        return False
    lease_end = _date(job.get("lease_until"))
    retry_at = _date(job.get("retry_at"))
    return not ((lease_end and lease_end > now) or (retry_at and retry_at > now))


def _latest(db, row_id):
    with db.no_autoflush:
        r = db.execute(select(Model.id, Model.cache_key, Model.generated_at, Model.verification,
                              Model.guidance, Model.route, Model.fresh_until).where(Model.id == row_id)).first()
    return SimpleNamespace(**r._mapping) if r else None


def live_lease_clause(db):
    """Check expiry at the UPDATE, not only at an earlier Python read."""
    until = Model.verification[JOB]["lease_until"].as_string()
    if db.get_bind().dialect.name == "sqlite":
        return func.julianday(until) > func.julianday("now")
    return cast(until, DateTime(timezone=True)) > func.clock_timestamp()


def _write(db, row, latest, verification, *, values=None, note=None, require_lease=False):
    from .freshness import json_unchanged
    payload = {"verification": verification, **(values or {})}
    stmt = update(Model).where(Model.id == latest.id, Model.generated_at == latest.generated_at,
        json_unchanged(db, Model.verification, latest.verification),
        json_unchanged(db, Model.guidance, latest.guidance),
        json_unchanged(db, Model.route, latest.route), Model.fresh_until == latest.fresh_until).values(**payload).execution_options(synchronize_session=False)
    if require_lease:
        stmt = stmt.where(live_lease_clause(db))
    with db.no_autoflush:
        changed = db.execute(stmt)
    if changed.rowcount != 1:
        db.rollback()
        db.refresh(row)
        return False
    for key, value in payload.items():
        set_committed_value(row, key, value)
    if note and "guidance" in payload:
        from . import change_log
        change_log.record(db, latest.cache_key, latest.route, latest.guidance, payload["guidance"],
                          origin="engine", note=note)
    db.commit()
    db.refresh(row)
    return True


def claim(db, row, *, recovery=False, expected_generation=None, expected_token=None):
    latest = _latest(db, row.id)
    if latest is None or not (latest.verification or {}).get("detail_pending"):
        return None
    gen = generation(latest.generated_at)
    if gen is None or expected_generation is not None and gen != expected_generation:
        return None
    job = _job(latest)
    if recovery:
        if not retry_eligible(latest):
            return None
    elif expected_token is not None:
        until = _date(job.get("lease_until"))
        if (job.get("token") != expected_token or job.get("state") != "queued"
                or until is None or until <= _now()):
            return None
    elif job:
        # A direct call must not steal a live worker's job. New staged calls
        # carry their reserved token; legacy direct callers may claim only
        # a genuinely unowned pending row.
        return None
    now = _now()
    reset_window = not _window_start(job) or _window_elapsed(job, now)
    attempts = 0 if reset_window else job.get("attempts", 0)
    if type(attempts) is not int or not 0 <= attempts < MAX_ATTEMPTS:
        return None
    new = dict(job, version=1, generation=gen, token=uuid4().hex, state="running",
               attempts=attempts + 1, started_at=now.isoformat(),
               lease_until=(now + timedelta(seconds=LEASE_SECONDS)).isoformat(),
               mode="official_source_recovery" if recovery else "initial_detail",
               window_started_at=(now if reset_window else _window_start(job)).isoformat())
    new.pop("retry_at", None)
    verification = deepcopy(latest.verification or {})
    verification[JOB] = new
    if not _write(db, row, latest, verification):
        return None
    return {"row_id": latest.id, "cache_key": latest.cache_key, "generation": gen, "token": new["token"]}


def owns(lease, row, *, now=None):
    if not isinstance(lease, dict) or row is None:
        return False
    ver = row.verification if isinstance(row.verification, dict) else {}
    job = _job(row)
    until = _date(job.get("lease_until"))
    return bool(ver.get("detail_pending") and job.get("state") == "running" and until and until > (now or _now())
                and row.id == lease.get("row_id") and row.cache_key == lease.get("cache_key")
                and generation(row.generated_at) == lease.get("generation") and job.get("token") == lease.get("token"))


def active_lease():
    return _ACTIVE.get()


@contextmanager
def source_owner(lease):
    marker = _ACTIVE.set(lease)
    try:
        yield
    finally:
        _ACTIVE.reset(marker)


def finish(db, row, lease, *, success, reason, values=None):
    # The caller validated this exact state. Reading newer state for the CAS
    # must not silently authorize writing an answer computed from an older one.
    expected = (deepcopy(row.guidance), deepcopy(row.route), deepcopy(row.verification), row.fresh_until)
    latest = _latest(db, row.id)
    if not owns(lease, latest) or success and expected != (latest.guidance, latest.route, latest.verification, latest.fresh_until):
        db.rollback()
        if latest is not None:
            db.refresh(row)
        return False
    verification = deepcopy(latest.verification or {})
    job = dict(verification[JOB])
    job.update(state="complete" if success else "cooldown" if job["attempts"] >= MAX_ATTEMPTS else "retry_wait",
               finished_at=_now().isoformat(), last_result=str(reason)[:120], lease_until=None)
    if success:
        verification.pop("detail_pending", None)
        job.pop("retry_at", None)
    else:
        verification["detail_pending"] = True
        retry_at = (_window_start(job) + timedelta(seconds=WINDOW_SECONDS)
                    if job["attempts"] >= MAX_ATTEMPTS else
                    _now() + timedelta(seconds=RETRY_SECONDS * 2 ** (job["attempts"] - 1)))
        job["retry_at"] = retry_at.isoformat()
    verification[JOB] = job
    if success and lease.get("_checked_snapshot"):
        # Provisional evidence is useful immediately; only this final CAS
        # may promise that the entire record is complete and fresh.
        for key in ("grounded_check", "last_good_check"):
            check = dict(verification.get(key) or {})
            check["renewed"] = True
            check.pop("detail_completion_pending", None)
            verification[key] = check
        values = dict(values or {}, fresh_until=lease["_checked_snapshot"]["renew_until"])
    def commit():
        return _write(db, row, latest, verification, values=values if success else None,
                      note="detail stage completed for the owning generation" if success else None, require_lease=True)
    if success and lease.get("_checked_snapshot"):
        from . import verified_overrides
        with verified_overrides.operator_write_lock(), verified_overrides.current_store_table():
            if not _matches_checked_snapshot(row, lease):
                return False
            return commit()
    return commit()


def _matches_checked_snapshot(row, lease):
    from . import verified_overrides
    checked = lease.get("_checked_snapshot")
    if not isinstance(checked, dict) or any(checked.get(k) != getattr(row, k)
            for k in ("guidance", "route", "verification", "fresh_until")):
        return False
    guidance, provenance = verified_overrides.apply(dict(row.guidance or {}), dict(row.route or {}))
    return checked.get("projection") == {"guidance": guidance, "provenance": provenance}


def _source_complete(db, row, report, lease):
    from . import freshness, kimi_primary, verified_overrides
    from .records_guard import grounded_verdict_supported
    gc = (row.verification or {}).get("grounded_check") or {}
    if not _matches_checked_snapshot(row, lease):
        return False
    if (report.get("outcome") != "checked" or report.get("renewed") is not True
            or not grounded_verdict_supported(gc) or report.get("unverified_fields")
            or report.get("unchecked_source_count") or report.get("source_fetch_failures")
            or report.get("disputed") or report.get("unquoted_fields") or report.get("validation_errors")):
        return False
    guidance, _ = verified_overrides.apply(dict(row.guidance or {}), dict(row.route or {}))
    # Serving intentionally removes inapplicable application labels from an
    # exemption. Keep the stored core solely for shape/completeness validation;
    # the current authoritative projection still wins every populated value.
    _, missing, contradictions = kimi_primary.validate_answer(
        dict(row.guidance or {}, **guidance), detail_known=True)
    return not (missing or contradictions or kimi_primary.serve_time_invariants(guidance)
                or freshness.active_disputed_fields(db, row.cache_key))


def recover_row(db, row, *, today=None, budget_seconds=None, should_stop=None):
    """One bounded source attempt, never a replay of the unverified model."""
    from . import freshness, kimi_primary
    budget = freshness.ROUTE_BUDGET_SECONDS if budget_seconds is None else min(freshness.ROUTE_BUDGET_SECONDS, budget_seconds)
    if budget <= 0 or should_stop and should_stop() or kimi_primary.provider_suspension():
        return {"outcome": "budget_exhausted", "route_key": row.cache_key, "detail_pending": True}
    lease = claim(db, row, recovery=True)
    if lease is None:
        return {"outcome": "detail_pending", "route_key": row.cache_key}
    report = {}
    try:
        with source_owner(lease):
            report = freshness.recheck_row(db, row, today=today, budget_seconds=budget, should_stop=should_stop)
        db.refresh(row)
        success = owns(lease, row) and _source_complete(db, row, report, lease)
        finished = finish(db, row, lease, success=success, reason=report.get("outcome", "incomplete_source_check"),
                          values={"missing_fields": [], "contradictions": [], "status": kimi_primary.STATUS_PRIMARY} if success else None)
        return dict(report, renewed=bool(report.get("renewed") and success and finished),
                    detail_pending=not (success and finished), detail_recovered=bool(success and finished),
                    detail_job_state=(row.verification or {}).get(JOB, {}).get("state"))
    except Exception as exc:
        db.rollback()
        try:
            finish(db, row, lease, success=False, reason=type(exc).__name__)
        except Exception:
            db.rollback()  # the durable pending lease remains recoverable
        return dict(report, outcome="detail_retry_failed", route_key=row.cache_key, detail_pending=True,
                    renewed=False, detail_recovered=False,
                    detail_job_state=(row.verification or {}).get(JOB, {}).get("state"))
