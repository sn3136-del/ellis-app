"""The read-only consistency sweep: six projections of one route, compared.

Trip.com's fourth finding (Hong Kong to Vietnam read visa-free on one screen
and e-visa on another, and refreshing the QC backend made the two diverge)
is a class, not a route: every surface built its own projection of the
cached answer, and nothing ever compared them. This module builds every
projection Ellis serves from ONE canonical route dict under ONE clock and
files every undeclared difference:

  reader plain       what /database/lookup and /database/ask serve
  reader dated       the same lookup with an arrival date 45 days out
  reader residence   the same lookup for a passport holder living elsewhere
  QC record          what /database/records serves (main._record_payload)
  export row         the workbook cells (tstation.export_values)
  AI facts           what the composer is allowed to speak from

Exactly five differences are declared and never filed:
  held_envelope                a held answer's reader surface carries no
                               claims, the QC record still shows them
  export_label                 the workbook prints a documented blank as its
                               label and a stay in words in the stay cell
  ai_subset                    the composer sees a subset of the fields
  arrival_date_policy_interval a dated lookup selects a policy interval the
                               route carries with its own official bound
  residence_conditional_rule   a residence changes only the consular
                               district cell

Everything else is surface_divergence. key_fork is a route whose stored
route no longer reproduces its key, or a variant row that could answer in
its place. stale_projection is an in-process records copy that disagrees
with a fresh build.

READ ONLY. This module imports no fact writer, opens no session of its own
and never commits; test_consistency_sweep pins that its source references
none of verified_overrides.append_operator_entry, freshness.recheck_route,
change_log.record, or a session add, delete or commit.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta, timezone

DECLARED_DIFFERENCES = ("held_envelope", "export_label", "ai_subset",
                        "arrival_date_policy_interval", "residence_conditional_rule")
BLOCKING_CODES = ("surface_divergence", "key_fork", "stale_projection")
# Beside the 25 cells, the state every surface must agree on.
COMPARED_STATE = ("disposition", "requirement_detail", "held", "review_required",
                  "publication_state", "confidence_level", "source_url",
                  "official_portal_url", "data_source")
DATED_OFFSET_DAYS = 45
RESIDENCE_PROBE = "ARE"


@dataclass
class Finding:
    code: str
    severity: str          # blocking | report
    cache_key: str         # the canonical key of the route
    field: str
    surfaces: list = field(default_factory=list)
    observed: object = None
    expected: object = None
    evidence: dict = field(default_factory=dict)
    fingerprint: str = ""
    checked_at: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _norm(value):
    try:
        return json.loads(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return str(value)


def fingerprint(code: str, canonical_key: str, field_name: str, observed) -> str:
    """Stable across runs and distinct per field: the same wrong value on the
    same route and field always hashes the same, so a re-run updates one
    issue instead of filing a second."""
    payload = json.dumps([code, canonical_key, field_name, _norm(observed)],
                         sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def make_finding(code: str, canonical_key: str, field_name: str, *, observed=None,
                 expected=None, surfaces=(), evidence=None, checked_at: str = "",
                 severity: str | None = None) -> Finding:
    return Finding(code=code, severity=severity or ("blocking" if code in BLOCKING_CODES else "report"),
                   cache_key=canonical_key, field=field_name, surfaces=list(surfaces),
                   observed=_norm(observed), expected=_norm(expected), evidence=dict(evidence or {}),
                   fingerprint=fingerprint(code, canonical_key, field_name, observed),
                   checked_at=checked_at)


# ---------------------------------------------------------------------------
# One canonical route dict per cache row
# ---------------------------------------------------------------------------

def route_dict(r) -> dict:
    """The request-shaped route a reader sends for this row: the stored route
    with the document type normalised, the visa category derived from the
    purpose and no consular district (the reader never types one)."""
    from . import kimi_primary
    from .row_projection import canonical_route
    route = canonical_route(r)
    route.pop("consular_jurisdiction", None)
    route.pop("arrival_date", None)
    route.pop("transit_countries", None)
    route["visa_category"] = kimi_primary.category_for_purpose(route.get("travel_purpose") or "tourism")
    route["lawful_country_of_residence"] = route.get("passport_nationality")
    return route


def _row_context(db, r):
    """The grounded check and the disputed fields every surface reads."""
    from . import freshness, kimi_primary
    gc = freshness.effective_check(r.verification)
    disputed = list(gc.get("disputed_fields") or [])
    disputed.extend(freshness.active_disputed_fields(db, r.cache_key))
    return gc, disputed


def _reader_projection(db, r, route: dict, gc: dict, *, arrival_date: str | None = None,
                       residence: str | None = None) -> dict:
    """The lookup surface for one row, as main.travel_database_lookup builds
    it: the shared reader composition, the grounded check stamp, the records
    hold, and the held envelope when the answer is withheld."""
    from . import kimi_primary
    from .records_guard import apply_records_hold, held_envelope
    rt = dict(route)
    if arrival_date:
        rt["arrival_date"] = arrival_date
    if residence:
        rt["lawful_country_of_residence"] = residence
    released = bool((r.verification or {}).get("operator_released"))
    out = kimi_primary.reader_projection(
        rt, r.status, kimi_primary.served_guidance(r), cached=True,
        stale=kimi_primary._is_stale(r), released=released,
        missing=r.missing_fields, contradictions=r.contradictions, model=r.model,
        advisories=kimi_primary.deterministic_advisories(rt, r.guidance or {}))
    out["detail_pending"] = bool((r.verification or {}).get("detail_pending"))
    out["operator_released"] = released
    if isinstance(gc, dict) and gc.get("outcome") == "checked":
        out["grounded_check"] = {k: gc.get(k) for k in
                                 ("at", "outcome", "source_url", "consistent", "changed_fields",
                                  "disputed_fields", "evidence_contract", "verified_fields",
                                  "unverified_fields", "source_checks", "field_sources",
                                  "unchecked_sources", "renewed")}
    out = apply_records_hold(rt, out, db)
    out["cache_key"] = kimi_primary.canonical_key(kimi_primary.cache_key(rt))
    out["_route"] = rt
    if out.get("held"):
        env = held_envelope(out)
        env["_route"] = rt
        return env
    return out


def _reader_rows(db, r, route: dict, out: dict, gc: dict, disputed: list) -> list[dict]:
    """The reader answer as 25-field records, through the same projector the
    records path uses, so cell by cell comparison is possible."""
    from . import kimi_primary, tstation
    from .records_guard import grounded_verdict_supported
    g = out.get("guidance")
    if not isinstance(g, dict):
        return []
    problems = kimi_primary.serve_time_invariants(g)
    collected = (r.generated_at.isoformat() if r.generated_at else "")
    until = (r.fresh_until.isoformat() if r.fresh_until else "")
    return tstation.records_for_route(out.get("_route") or route, g, out.get("source_verified"),
                                      collected, until, grounded_ok=grounded_verdict_supported(gc),
                                      disputed_fields=list(disputed) + problems,
                                      grounded_fields=gc.get("verified_fields"))


def _qc_projection(db, r, route: dict) -> list[dict]:
    """The records surface: the exact rows /database/records serves, with the
    browser payload computed from each."""
    from .row_projection import records_projection
    return records_projection(db, r, route)


def _qc_payloads(rows: list[dict]) -> list[dict]:
    from .. import main as served
    return [served._record_payload(rec) for rec in rows]


def _export_rows(rows: list[dict]) -> list[list]:
    from . import tstation
    return [tstation.export_values(rec) for rec in rows]


def _ai_facts(out: dict) -> dict:
    from . import assistant
    return assistant.fact_payload(out)


def _cells(rec: dict) -> dict:
    from . import tstation
    return {f: _norm(rec.get(f)) for f in tstation.FIELD_ORDER}


def _pair_rows(reader_rows: list[dict], qc_rows: list[dict]) -> list[tuple]:
    """Match reader rows to the QC rows the reader is meant to show: the
    published ones, by product name, falling back to product order."""
    published = [rec for rec in qc_rows if not rec.get("_held")]
    pairs, used = [], set()
    for i, a in enumerate(reader_rows):
        hit = next((j for j, b in enumerate(published)
                    if j not in used and b.get("visa_type_name") == a.get("visa_type_name")), None)
        if hit is None and i < len(published) and i not in used:
            hit = i
        if hit is None:
            pairs.append((a, None))
            continue
        used.add(hit)
        pairs.append((a, published[hit]))
    for j, b in enumerate(published):
        if j not in used:
            pairs.append((None, b))
    return pairs


def _policy_bounded(r, out: dict) -> bool:
    """Whether the route carries a policy interval of its own, which is the
    only lawful reason a dated lookup differs from a plain one."""
    from . import policy_intervals
    g = out.get("guidance") if isinstance(out.get("guidance"), dict) else {}
    prov = out.get("source_verified") or {}
    marker = getattr(policy_intervals, "MARKER", "_policy_interval")
    if g.get(marker) or g.get("policy_valid_until") or g.get("policy_interval_conflict"):
        return True
    proofs = prov.get("field_provenance") if isinstance(prov, dict) else {}
    if any(isinstance(p, dict) and (p.get("effective_from") or p.get("effective_to"))
           for p in (proofs or {}).values()):
        return True
    return bool(isinstance(prov, dict) and (prov.get("effective_from") or prov.get("effective_to")))


def compare_projections(canonical_key: str, plain_rows: list[dict], plain_out: dict,
                        dated_rows: list[dict], dated_out: dict, residence_rows: list[dict],
                        residence_out: dict, qc_rows: list[dict], qc_payloads: list[dict],
                        export_rows: list[list], ai_facts: dict, *, policy_bounded: bool,
                        checked_at: str) -> tuple[list[Finding], dict]:
    """Compare the six projections of one route. Returns the findings and the
    count of declared differences seen, so the evidence can show that a
    quiet run still looked."""
    from . import tstation
    findings: list[Finding] = []
    declared = {name: 0 for name in DECLARED_DIFFERENCES}
    held = bool(plain_out.get("held"))

    def file(field_name, observed, expected, surfaces, **evidence):
        findings.append(make_finding("surface_divergence", canonical_key, field_name,
                                     observed=observed, expected=expected, surfaces=surfaces,
                                     evidence=evidence, checked_at=checked_at))

    # 1. reader plain against the QC record, cell by cell
    if held:
        declared["held_envelope"] += 1
        # The reader carries no claims; the QC row must still say the route is held.
        if qc_rows and not any(rec.get("_route_held") or rec.get("_held") for rec in qc_rows):
            file("held", False, True, ["reader", "qc"], reason="reader held, records not")
    else:
        if qc_rows and all(rec.get("_route_held") for rec in qc_rows):
            file("held", False, True, ["reader", "qc"], reason="records held, reader not")
        for i, (a, b) in enumerate(_pair_rows(plain_rows, qc_rows)):
            if a is None:
                file("visa_type_name", None, b.get("visa_type_name"), ["reader", "qc"],
                     reason="published record the reader does not show")
                continue
            if b is None:
                file("visa_type_name", a.get("visa_type_name"), None, ["reader", "qc"],
                     reason="reader product with no published record")
                continue
            ca, cb = _cells(a), _cells(b)
            for f in tstation.FIELD_ORDER:
                if ca[f] != cb[f]:
                    file(f, ca[f], cb[f], ["reader", "qc"], product_index=i)
        for i, payload in enumerate(qc_payloads):
            st = payload.get("field_status") or {}
            rec = qc_rows[i]
            wording = {"max_stay_duration": payload.get("max_stay_text"),
                       "validity_duration": payload.get("validity_text")}
            for f in tstation.FIELD_ORDER:
                if st.get(f) == "filled" and _norm(rec.get(f)) in (None, "", []) and not wording.get(f):
                    file(f, rec.get(f), "filled", ["qc"], reason="checklist says filled, cell empty")
    # 2. reader dated against reader plain
    if bool(dated_out.get("held")) != held or len(dated_rows) != len(plain_rows):
        if policy_bounded:
            declared["arrival_date_policy_interval"] += 1
        else:
            file("held", bool(dated_out.get("held")), held, ["reader_dated", "reader"],
                 reason="arrival date changed the hold")
    else:
        for i, (a, b) in enumerate(zip(dated_rows, plain_rows)):
            ca, cb = _cells(a), _cells(b)
            diff = [f for f in tstation.FIELD_ORDER if ca[f] != cb[f]]
            if diff and policy_bounded:
                declared["arrival_date_policy_interval"] += 1
            for f in diff if not policy_bounded else []:
                file(f, ca[f], cb[f], ["reader_dated", "reader"], product_index=i,
                     reason="arrival date changed a cell on a route with no policy interval")
    # 3. reader residence against reader plain
    if bool(residence_out.get("held")) != held or len(residence_rows) != len(plain_rows):
        file("held", bool(residence_out.get("held")), held, ["reader_residence", "reader"],
             reason="residence changed the hold")
    else:
        for i, (a, b) in enumerate(zip(residence_rows, plain_rows)):
            ca, cb = _cells(a), _cells(b)
            for f in tstation.FIELD_ORDER:
                if ca[f] != cb[f]:
                    if f == "consulate_district":
                        declared["residence_conditional_rule"] += 1
                    else:
                        file(f, ca[f], cb[f], ["reader_residence", "reader"], product_index=i)
    # 4. export against the QC record
    for i, (rec, payload, cells) in enumerate(zip(qc_rows, qc_payloads, export_rows)):
        for f, shown in zip(tstation.FIELD_ORDER, cells):
            value = _norm(rec.get(f))
            if _norm(shown) == value:
                continue
            labels = (tstation.NOT_PUBLICLY_AVAILABLE, tstation.NOT_APPLICABLE)
            wording = (payload.get("max_stay_text"), payload.get("validity_text"))
            if (value in (None, "", []) and shown in labels) or (shown in wording and shown):
                declared["export_label"] += 1
                continue
            file(f, shown, value, ["export", "qc"], product_index=i)
    # 5. AI facts against the reader
    if held:
        if ai_facts.get("held") is None or any(k not in ("route", "held") for k in ai_facts):
            file("facts", sorted(ai_facts), ["route", "held"], ["ai", "reader"],
                 reason="held answer leaked facts to the composer")
    else:
        g = plain_out.get("guidance") if isinstance(plain_out.get("guidance"), dict) else {}
        for k, v in ai_facts.items():
            if k in ("route", "comparison", "special_policies"):
                continue
            if _norm(v) != _norm(g.get(k)):
                file(k, v, g.get(k), ["ai", "reader"])
        declared["ai_subset"] += 1
    return findings, declared


def check_row(db, r, *, now: datetime, in_process_cache: dict | None = None) -> tuple[list[Finding], dict, dict]:
    """All findings for one cached row, the declared differences seen, and
    the projections themselves (so callers can extend the checks)."""
    from . import kimi_primary
    canonical = kimi_primary.canonical_key(r.cache_key or "")
    checked_at = now.isoformat()
    route = route_dict(r)
    gc, disputed = _row_context(db, r)
    plain_out = _reader_projection(db, r, route, gc)
    plain_rows = _reader_rows(db, r, route, plain_out, gc, disputed)
    dated = (now + timedelta(days=DATED_OFFSET_DAYS)).date().isoformat()
    dated_out = _reader_projection(db, r, route, gc, arrival_date=dated)
    dated_rows = _reader_rows(db, r, route, dated_out, gc, disputed)
    probe = RESIDENCE_PROBE if route.get("passport_nationality") != RESIDENCE_PROBE else "SGP"
    residence_out = _reader_projection(db, r, route, gc, residence=probe)
    residence_rows = _reader_rows(db, r, route, residence_out, gc, disputed)
    qc_rows = _qc_projection(db, r, route)
    qc_payloads = _qc_payloads(qc_rows)
    export_rows = _export_rows(qc_rows)
    ai_facts = _ai_facts(plain_out)
    findings, declared = compare_projections(
        canonical, plain_rows, plain_out, dated_rows, dated_out, residence_rows, residence_out,
        qc_rows, qc_payloads, export_rows, ai_facts,
        policy_bounded=_policy_bounded(r, plain_out), checked_at=checked_at)
    # key_fork: the stored route must reproduce its own key.
    if kimi_primary.cache_key(route) != r.cache_key:
        findings.append(make_finding("key_fork", canonical, "cache_key",
                                     observed=kimi_primary.cache_key(route), expected=r.cache_key,
                                     surfaces=["cache"], evidence={"route": route}, checked_at=checked_at))
    # stale_projection: a warm in-process records copy that disagrees with a fresh build.
    if in_process_cache:
        cached = in_process_cache.get(r.cache_key)
        if cached is not None:
            fresh = [_cells(rec) for rec in qc_rows]
            if [_cells(rec) for rec in cached] != fresh:
                findings.append(make_finding("stale_projection", canonical, "records_cache",
                                             observed=[c.get("visa_requirement") for c in cached],
                                             expected=[c.get("visa_requirement") for c in qc_rows],
                                             surfaces=["records_cache", "qc"], checked_at=checked_at))
    projections = {"route": route, "gc": gc, "disputed": disputed, "reader": plain_out,
                   "reader_rows": plain_rows, "dated": dated_out, "residence": residence_out,
                   "qc_rows": qc_rows, "qc_payloads": qc_payloads, "export": export_rows,
                   "ai": ai_facts}
    return findings, declared, projections


def _in_process_records_cache() -> dict | None:
    """The records copy main holds in this process, grouped by cache key, or
    None when the cache is cold."""
    try:
        from .. import main as served
    except Exception:  # noqa: BLE001 - the sweep must not depend on the API app
        return None
    rows = (getattr(served, "_RECORDS_CACHE", None) or {}).get("rows")
    if not rows:
        return None
    grouped: dict = {}
    for rec in rows:
        grouped.setdefault(rec.get("_cache_key"), []).append(rec)
    return grouped


def canonical_rows(db) -> list:
    """Every canonical cache row, the way the records builder selects them."""
    from sqlalchemy import select
    from . import kimi_primary
    from .models import KimiRouteGuidanceCache
    return [r for r in db.execute(select(KimiRouteGuidanceCache)).scalars()
            if kimi_primary.is_canonical_key(r.cache_key or "")]


def variant_forks(db) -> list[Finding]:
    """A dated or residence variant row still stored beside a canonical row
    is a second answer waiting to be served: key_fork."""
    from sqlalchemy import select
    from . import kimi_primary
    from .models import KimiRouteGuidanceCache
    out = []
    canon = set()
    variants = []
    for r in db.execute(select(KimiRouteGuidanceCache)).scalars():
        key = r.cache_key or ""
        if kimi_primary.is_canonical_key(key):
            canon.add(key)
        elif not any(p.startswith("via:") for p in key.split("|")[7:]):
            variants.append(key)
    for key in variants:
        base = kimi_primary.canonical_key(key)
        out.append(make_finding("key_fork", base, "variant_row", observed=key, expected=base,
                                surfaces=["cache"], evidence={"canonical_exists": base in canon}))
    return out


def run(db, now: datetime | None = None, trigger: str = "manual", *, limit: int | None = None,
        keys: list[str] | None = None, proof_checks: bool = True, absence_checks: bool = True,
        coverage: bool = True) -> dict:
    """Sweep the inventory and return the evidence dict. Never writes."""
    from . import kimi_primary
    now = now or datetime.now(timezone.utc)
    run_id = f"{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    rows = canonical_rows(db)
    if keys:
        wanted = set(keys)
        rows = [r for r in rows if r.cache_key in wanted]
    if limit:
        rows = rows[:limit]
    in_process = _in_process_records_cache()
    findings: list[Finding] = []
    declared_totals = {name: 0 for name in DECLARED_DIFFERENCES}
    served_rows = 0
    published = withheld = 0
    grades = {"High": 0, "Medium": 0, "Low": 0}
    proof_rows: list[tuple] = []
    for r in rows:
        row_findings, declared, proj = check_row(db, r, now=now, in_process_cache=in_process)
        findings.extend(row_findings)
        for k, v in declared.items():
            declared_totals[k] += v
        for rec in proj["qc_rows"]:
            served_rows += 1
            if rec.get("_held"):
                withheld += 1
            else:
                published += 1
            grades[str(rec.get("confidence_level") or "Low")] = grades.get(str(rec.get("confidence_level") or "Low"), 0) + 1
        proof_rows.append((r, proj))
    findings.extend(variant_forks(db))
    evidence = {
        "run_id": run_id, "trigger": trigger, "generated_at": datetime.now(timezone.utc).isoformat(),
        "evaluated_at": now.isoformat(), "clock": {"dated_lookup_offset_days": DATED_OFFSET_DAYS,
                                                    "residence_probe": RESIDENCE_PROBE},
        "inventory": {"cache_rows": _cache_row_count(db), "canonical_rows": len(rows),
                      "served_rows": served_rows, "published": published, "withheld": withheld,
                      "grades": grades},
        "declared_differences": declared_totals,
    }
    for name, enabled in (("proof", proof_checks), ("absence", absence_checks), ("coverage", coverage)):
        if enabled:
            extra = _extension(name)
            if extra is not None:
                result = extra(proof_rows, now=now)
                findings.extend(result.get("findings") or [])
                if result.get("summary") is not None:
                    evidence[name] = result["summary"]
    evidence["findings"] = [f.as_dict() for f in findings]
    evidence["counts"] = summarise(findings)
    return evidence


def _extension(name: str):
    """The report-only checks later tasks add beside the surface comparison
    (T4 proof checks, T8 absence accounting, T13 coverage). Each is a module
    exposing check_rows(rows, now=...) -> {findings, summary}; a check that is
    not shipped yet is simply absent from the evidence."""
    import importlib
    module = {"proof": ".sweep_proof_checks", "absence": ".sweep_absence",
              "coverage": ".inventory_coverage"}[name]
    try:
        return importlib.import_module(module, __package__).check_rows
    except ImportError:
        return None


def _cache_row_count(db) -> int:
    from sqlalchemy import func, select
    from .models import KimiRouteGuidanceCache
    return int(db.execute(select(func.count()).select_from(KimiRouteGuidanceCache)).scalar() or 0)


def summarise(findings: list[Finding]) -> dict:
    counts: dict = {}
    for f in findings:
        entry = counts.setdefault(f.code, {"total": 0, "severity": f.severity, "keys": []})
        entry["total"] += 1
        if f.cache_key not in entry["keys"]:
            entry["keys"].append(f.cache_key)
    return counts
