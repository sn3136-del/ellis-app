"""Read-only consistency reporting inside the existing freshness cycle.

Only the private report file and the caller's operational status are written.
Findings are diagnostic candidates, never issue filings, policy verification,
confidence changes or publication decisions.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time

CHECK_TYPES = frozenset({
    "surface_divergence", "key_fork", "stale_projection", "verdict_detail_missing",
    "verdict_page_scheme_conflict", "proof_missing_quote", "proof_nationality_unnamed",
    "proof_off_jurisdiction", "absence_undocumented",
})
STATES = frozenset({"running", "complete", "failed", "interrupted", "budget_exhausted"})


def _stamp():
    return datetime.now(timezone.utc).isoformat()


def _count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def public_summary(value):
    """Double allowlist at the public API boundary: no keys, quotes or paths."""
    if not isinstance(value, dict) or value.get("state") not in STATES:
        return None
    result = {"state": value["state"]}
    for key in ("started_at", "checked_at", "last_success_at"):
        stamp = value.get(key)
        try:
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                result[key] = parsed.isoformat()
        except (AttributeError, TypeError, ValueError, OverflowError):
            pass
    for key in ("routes_checked", "records_checked", "findings_total"):
        if _count(value.get(key)):
            result[key] = value[key]
    for key, allowed in (("by_type", CHECK_TYPES), ("by_severity", {"blocking", "report"})):
        counts = value.get(key)
        if isinstance(counts, dict):
            result[key] = {name: counts[name] for name in sorted(allowed)
                           if _count(counts.get(name))}
    if value.get("failure") in {"scan_failed", "report_write_failed"}:
        result["failure"] = value["failure"]
    return result


def _write_private(path, report):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".consistency-", delete=False) as handle:
            temporary = handle.name
            os.fchmod(handle.fileno(), 0o600)
            json.dump(report, handle, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def run_report(db, *, status_path, deadline, should_stop, previous=None, on_progress=None):
    from . import consistency_sweep, freshness, verified_overrides
    prior = public_summary(previous) or {}
    status = {"state": "running", "started_at": _stamp(), "routes_checked": 0}
    last_success = prior.get("checked_at") if prior.get("state") == "complete" else prior.get("last_success_at")
    if last_success:
        status["last_success_at"] = last_success
    last_heartbeat = 0.0

    def checkpoint(completed):
        nonlocal last_heartbeat
        status["routes_checked"] = completed
        if should_stop():
            status["state"] = "interrupted"
            raise InterruptedError("consistency scan interrupted")
        if time.monotonic() >= deadline:
            status["state"] = "budget_exhausted"
            raise TimeoutError("consistency scan reached the existing cycle deadline")
        if on_progress and time.monotonic() - last_heartbeat >= 30:
            last_heartbeat = time.monotonic()
            on_progress(public_summary(status))

    try:
        checkpoint(0)
        # A fresh coordinator session has no pending changes; this additionally
        # prevents any accidental ORM autoflush while projecting stored rows.
        with db.no_autoflush, verified_overrides.pinned_table(), freshness.disputed_fields_snapshot(db):
            report = consistency_sweep.run(db, trigger="freshness_timer_report_only",
                                           checkpoint=checkpoint)
        checkpoint(report["inventory"]["canonical_rows"])
    except Exception:
        if status["state"] == "running":
            status.update(state="failed", failure="scan_failed")
        return public_summary(status)
    try:
        _write_private(Path(status_path).parent / "consistency-reports" / "latest.json", report)
    except (OSError, TypeError, ValueError):
        status.update(state="failed", failure="report_write_failed")
        return public_summary(status)
    findings = report["findings"]
    status.update(state="complete", checked_at=report["generated_at"],
                  last_success_at=report["generated_at"],
                  records_checked=report["inventory"]["served_rows"],
                  findings_total=len(findings),
                  by_type={name: sum(f.get("code") == name for f in findings) for name in CHECK_TYPES},
                  by_severity={name: sum(f.get("severity") == name for f in findings)
                               for name in ("blocking", "report")})
    return public_summary(status)
