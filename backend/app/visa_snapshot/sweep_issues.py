"""Every sweep finding reaches the five-stage correction loop.

guard-20260912 T9. The consistency sweep only reports; this module files
what it reports as DatabaseIssueReport rows so a violation is worked through
open, acknowledged, corrected, reviewed and published like any reader flag,
with traceable history:

  * one issue per finding, reported_by "sweep", status open, the canonical
    cache key, field truncated to the 64 character column and the full
    names in proposal["fields"] (the gate reads them there), plus
    proposal["sweep"]: run_id, code, severity, fingerprint, surfaces,
    observed, expected, evidence, first and last seen and occurrences
  * dedupe is one read on the indexed fingerprint column: a live issue is
    updated in place; a fingerprint matching an issue already published or
    dismissed files a NEW issue carrying supersedes, counted as
    dismissed_but_reproduced (or published_but_reproduced), so a closure
    that did not fix the fact is overturned visibly
  * recheck(db, issue) reruns the single check behind the code for that
    route and returns (passes, why); main.py requires it to pass before a
    sweep issue is dismissed or counted as corrected

This module writes issue reports and nothing else: no override append, no
grounded recheck, no change-log record (test_sweep_issue_loop pins the
import graph).
"""
from __future__ import annotations

from datetime import datetime, timezone

REPORTER = "sweep"
LIVE_STATUSES = ("open", "acknowledged", "corrected", "reviewed")
CLOSED_STATUSES = ("published", "dismissed")
# The closed vocabulary a dismissal may cite instead of a competent page.
DISMISS_REASONS = (
    "duplicate_of_open_issue",             # the same fact is tracked by another open issue
    "not_reproducible",                    # the check no longer fires, verified by rerunning it
    "outside_route_scope",                 # the finding concerns a route the database does not serve
    "superseded_by_published_correction",  # a published correction already fixed the fact
    "reporter_error",                      # a flag that names no field and no fact
    "check_false_positive",                # sourced operations adjudication of a report-level checker
)
EVIDENCE_CHARS = 4000


def _as_dict(finding) -> dict:
    return finding.as_dict() if hasattr(finding, "as_dict") else dict(finding)


def sweep_meta(issue) -> dict | None:
    proposal = issue.proposal if isinstance(getattr(issue, "proposal", None), dict) else {}
    meta = proposal.get("sweep")
    return meta if isinstance(meta, dict) and meta.get("code") else None


def is_sweep_issue(issue) -> bool:
    return getattr(issue, "reported_by", "") == REPORTER and sweep_meta(issue) is not None


def sweep_fields(issue) -> list[str]:
    meta = sweep_meta(issue) or {}
    return [str(f) for f in (meta.get("fields") or []) if str(f).strip()]


def _route_for(db, cache_key: str) -> dict:
    """The stored route of the canonical row, or the route the key spells."""
    from sqlalchemy import select
    from .models import KimiRouteGuidanceCache
    row = db.execute(select(KimiRouteGuidanceCache).where(
        KimiRouteGuidanceCache.cache_key == cache_key)).scalars().first()
    if row is not None and isinstance(row.route, dict) and row.route:
        return {k: row.route.get(k) for k in ("passport_nationality", "lawful_country_of_residence",
                                              "destination_country", "travel_purpose",
                                              "travel_document_type") if row.route.get(k)}
    parts = str(cache_key or "").split("|")
    route = {"passport_nationality": parts[0] if parts else "",
             "destination_country": parts[2] if len(parts) > 2 else "",
             "travel_purpose": parts[3] if len(parts) > 3 else "tourism"}
    doc = next((p[4:] for p in parts if p.startswith("doc:")), "")
    if doc:
        route["travel_document_type"] = doc
    return route


def _bounded(value):
    import json
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= EVIDENCE_CHARS:
        return json.loads(text)
    return {"truncated": text[:EVIDENCE_CHARS]}


def evidence_digest(evidence) -> str:
    """Hash the full canonical evidence before its display copy is bounded."""
    import hashlib
    import json
    return hashlib.sha256(json.dumps(evidence or {}, sort_keys=True, ensure_ascii=False,
                                    default=str).encode("utf-8")).hexdigest()


def finding_signature(finding: dict) -> str:
    """Bind an adjudication to the observed problem AND its supporting context.
    A changed observation, expected value, or source evidence needs fresh review.
    Run ids, timestamps and recurrence counters are not policy evidence.
    """
    import hashlib
    import json
    payload = {k: finding.get(k) for k in ("fingerprint", "code", "severity", "observed", "expected", "surfaces")}
    payload["evidence_sha256"] = finding.get("evidence_sha256") or evidence_digest(finding.get("evidence"))
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                    default=str).encode("utf-8")).hexdigest()


def file_findings(db, findings, *, run_id: str, now: datetime | None = None,
                  dry_run: bool = False) -> dict:
    """File (or refresh) one issue per finding. With dry_run nothing is
    written and the counts are the prediction a smoke run must match."""
    from sqlalchemy import select
    from .models import DatabaseIssueReport
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat()
    counts = {"findings": 0, "created": 0, "updated": 0,
              "dismissed_but_reproduced": 0, "published_but_reproduced": 0,
              "adjudicated_false_positive": 0}
    created: list[str] = []
    seen: set = set()
    for finding in findings:
        d = _as_dict(finding)
        fingerprint = str(d.get("fingerprint") or "")
        if not fingerprint or fingerprint in seen:
            continue
        seen.add(fingerprint)
        counts["findings"] += 1
        same = db.execute(select(DatabaseIssueReport).where(
            DatabaseIssueReport.fingerprint == fingerprint)
            .execution_options(populate_existing=True)).scalars().all()
        live = next((i for i in same if i.status in LIVE_STATUSES), None)
        if live is not None:
            counts["updated"] += 1
            if not dry_run:
                proposal = dict(live.proposal or {})
                meta = dict(proposal.get("sweep") or {})
                meta.update(last_seen=stamp, last_run_id=run_id,
                            occurrences=int(meta.get("occurrences") or 0) + 1,
                            code=d.get("code"), severity=d.get("severity"),
                            fingerprint=fingerprint, surfaces=d.get("surfaces") or [],
                            observed=d.get("observed"), expected=d.get("expected"),
                            evidence=_bounded(d.get("evidence") or {}),
                            evidence_sha256=evidence_digest(d.get("evidence")))
                field = str(d.get("field") or "")
                meta["fields"] = [field]
                fields = dict(proposal.get("fields") or {})
                fields[field] = {"observed": d.get("observed"), "expected": d.get("expected")}
                proposal["fields"] = fields
                proposal["sweep"] = meta
                live.proposal = proposal
            continue
        closed = [i for i in same if i.status in CLOSED_STATUSES]
        prior = max(closed, key=lambda i: (i.created_at or now).isoformat(), default=None)
        if prior is not None:
            adjudication = (sweep_meta(prior) or {}).get("false_positive_adjudication") or {}
            if (prior.status == "dismissed" and d.get("severity") == "report"
                    and adjudication.get("finding_signature") == finding_signature(d)):
                # The same operator-adjudicated checker error is counted and
                # remains traceable, rather than reopening forever. This does
                # not bypass source verification or any traveler publication gate.
                counts["adjudicated_false_positive"] += 1
                if not dry_run:
                    proposal = dict(prior.proposal or {})
                    meta = dict(proposal.get("sweep") or {})
                    meta.update(last_seen=stamp, last_run_id=run_id,
                                occurrences=int(meta.get("occurrences") or 0) + 1)
                    proposal["sweep"] = meta
                    prior.proposal = proposal
                continue
            counts[f"{prior.status}_but_reproduced"] += 1
        counts["created"] += 1
        if dry_run:
            continue
        field = str(d.get("field") or "")
        meta = {"run_id": run_id, "first_run_id": run_id, "last_run_id": run_id,
                "code": d.get("code"), "severity": d.get("severity"), "fingerprint": fingerprint,
                "surfaces": d.get("surfaces") or [], "observed": d.get("observed"),
                "expected": d.get("expected"), "evidence": _bounded(d.get("evidence") or {}),
                "evidence_sha256": evidence_digest(d.get("evidence")),
                "fields": [field], "first_seen": stamp, "last_seen": stamp, "occurrences": 1,
                "supersedes": prior.id if prior is not None else None,
                "supersedes_status": prior.status if prior is not None else None}
        note = (f"Consistency sweep {d.get('code')} on {field} ({d.get('severity')})"
                + (f", reproduced after issue {prior.id} was {prior.status}" if prior is not None else ""))
        issue = DatabaseIssueReport(
            org_id="platform", cache_key=str(d.get("cache_key") or ""),
            route=_route_for(db, str(d.get("cache_key") or "")),
            field=field[:64], note=note[:1000], reported_by=REPORTER, status="open",
            fingerprint=fingerprint,
            proposal={"fields": {field: {"observed": d.get("observed"), "expected": d.get("expected")}},
                      "sweep": meta})
        db.add(issue)
        db.flush()
        created.append(issue.id)
    if not dry_run:
        db.commit()
    counts["created_ids"] = created
    return counts


def _route_findings(db, row, now: datetime) -> list:
    """Every finding the sweep would file for one canonical row today."""
    from . import consistency_sweep, kimi_primary
    findings, _declared, proj = consistency_sweep.check_row(db, row, now=now)
    for name in ("proof", "absence"):
        extension = consistency_sweep._extension(name)
        if extension is not None:
            findings.extend(extension([(row, proj)], now=now).get("findings") or [])
    canonical = kimi_primary.canonical_key(row.cache_key or "")
    findings.extend(f for f in consistency_sweep.variant_forks(db) if f.cache_key == canonical)
    return findings


def recheck(db, issue) -> tuple[bool, str]:
    """Rerun the single check behind a sweep issue's code for its route.
    Returns (passes, why). A route with no canonical row cannot be rechecked
    and never passes: a deleted answer is a coverage gap, not a fix."""
    from sqlalchemy import select
    from . import kimi_primary
    from .models import KimiRouteGuidanceCache
    meta = sweep_meta(issue)
    if meta is None:
        return False, "not a sweep issue: there is no check to rerun"
    code = str(meta.get("code"))
    fields = set(meta.get("fields") or [issue.field])
    now = datetime.now(timezone.utc)
    recheckers = _RECHECKERS.get(code)
    if recheckers is not None:
        return recheckers(db, issue, meta, now)
    canonical = kimi_primary.canonical_key(issue.cache_key or "")
    row = db.execute(select(KimiRouteGuidanceCache).where(
        KimiRouteGuidanceCache.cache_key == canonical)).scalars().first()
    if row is None:
        return False, f"no canonical row {canonical} to recheck"
    still = [f for f in _route_findings(db, row, now) if f.code == code and f.field in fields]
    if still:
        first = still[0]
        return False, (f"{code} still fires on {first.field}: observed "
                       f"{str(first.observed)[:160]}, expected {str(first.expected)[:160]}")
    return True, f"{code} no longer fires on {', '.join(sorted(fields))} (rechecked {now.isoformat()})"


# Codes whose check is not a per-route rerun register a rechecker here
# (T13 adds coverage_gap).
_RECHECKERS: dict = {}
