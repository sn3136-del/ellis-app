"""Baseline pair-policy import + official-research overrides.

Two strictly-separated provenance tiers:

  1. import_reference_baseline() — the passport-index reference dataset
     (data/reference/passport_index_baseline.csv, provenance in
     baseline_provenance.json). NON-OFFICIAL aggregated data: every imported
     row is source='reference_dataset', verification_status='provisional',
     release_status='defined_provisional'. Provisional rows give every pair a
     DEFINED outcome but never count as verified or released.

  2. apply_research_overrides() — official-source research already grounded in
     this repo (research_status='verified' research files + verified VisaRoute
     records). These rows become source='official_research',
     verification_status='verified' and always take precedence.

ensure_full_pair_coverage() then materializes an explicit UNRESOLVED row for
every remaining (nationality, destination) pair so no combination is silently
absent. Nothing here ever guesses: no data -> UNRESOLVED, never a default.
"""
from __future__ import annotations

import csv
import json
import uuid

from sqlalchemy import func, insert, select, update

from ..visa_snapshot import SNAPSHOT_DATE
from ..visa_snapshot.matrix import matrix_key
from ..visa_snapshot.models import RouteMatrixEntry, VisaRoute
from ..visa_snapshot.registry import REFERENCE_DIR, load_registry
from .models import RoutePairPolicy, pair_key

ORDINARY = "ordinary_passport"

BASELINE_CSV = REFERENCE_DIR / "passport_index_baseline.csv"
BASELINE_PROVENANCE = REFERENCE_DIR / "baseline_provenance.json"

# dataset value -> (disposition, route_outcome, primary_category)
_VALUE_MAP = {
    "visa free": ("VISA_FREE", "VISA_EXEMPT", "visa_free_entry"),
    "visa on arrival": ("VISA_ON_ARRIVAL", "VISA_ON_ARRIVAL", "visa_on_arrival_tourist"),
    "e-visa": ("EVISA_REQUIRED", "EVISA", "evisa_tourist"),
    "eta": ("ETA_REQUIRED", "ELECTRONIC_AUTHORIZATION", "eta"),
    "visa required": ("EMBASSY_VISA_REQUIRED", "EMBASSY_OR_CONSULATE_APPLICATION", "tourist_visa"),
    "no admission": ("NO_TOURIST_ROUTE", "NO_AVAILABLE_TOURIST_ROUTE", ""),
}

# research-file / route-record disposition -> route outcome
DISPOSITION_TO_OUTCOME = {
    "VISA_FREE": "VISA_EXEMPT",
    "ETA_REQUIRED": "ELECTRONIC_AUTHORIZATION",
    "EVISA_REQUIRED": "EVISA",
    "VISA_ON_ARRIVAL": "VISA_ON_ARRIVAL",
    "EMBASSY_VISA_REQUIRED": "EMBASSY_OR_CONSULATE_APPLICATION",
    "AUTHORIZED_VISA_CENTER_REQUIRED": "AUTHORIZED_VISA_CENTER",
    "NO_TOURIST_ROUTE": "NO_AVAILABLE_TOURIST_ROUTE",
}

_DISPOSITION_TO_CATEGORY = {
    "VISA_FREE": "visa_free_entry",
    "ETA_REQUIRED": "eta",
    "EVISA_REQUIRED": "evisa_tourist",
    "VISA_ON_ARRIVAL": "visa_on_arrival_tourist",
    "EMBASSY_VISA_REQUIRED": "tourist_visa",
    "AUTHORIZED_VISA_CENTER_REQUIRED": "tourist_visa",
}


def _nationality_codes() -> set[str]:
    return {n["code"] for n in load_registry("nationalities")["entries"]}


def _destination_codes() -> set[str]:
    return {c["alpha_3"] for c in load_registry("countries")["entries"]}


def _existing_pairs(db) -> dict[str, tuple[str, str]]:
    """pair_key -> (source, verification_status) for precedence checks."""
    rows = db.execute(select(RoutePairPolicy.pair_key, RoutePairPolicy.source,
                             RoutePairPolicy.verification_status)).all()
    return {k: (s, v) for k, s, v in rows}


def _provenance() -> dict:
    try:
        prov = json.loads(BASELINE_PROVENANCE.read_text())
    except Exception:  # noqa: BLE001 — provenance file is required for honesty
        raise RuntimeError("baseline_provenance.json missing — refusing to import "
                           "reference data without recorded provenance")
    import hashlib
    actual = hashlib.sha256(BASELINE_CSV.read_bytes()).hexdigest()
    if actual != prov.get("sha256"):
        raise RuntimeError(
            "passport_index_baseline.csv does not match its recorded sha256 — "
            "refusing to import tampered/replaced reference data")
    return prov


def import_reference_baseline(db, *, batch_size: int = 4000) -> dict:
    """Import the reference dataset as PROVISIONAL pair rows. Never overwrites
    an official_research row. Idempotent (existing pair rows are preserved)."""
    prov = _provenance()
    nats = _nationality_codes()
    dests = _destination_codes()
    existing = _existing_pairs(db)
    imported = skipped_existing = unknown_code = 0
    chunk: list[dict] = []

    def _flush():
        nonlocal imported, chunk
        if chunk:
            db.execute(insert(RoutePairPolicy), chunk)
            db.commit()
            imported += len(chunk)
            chunk = []

    with open(BASELINE_CSV, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            nat, dest, val = row["Passport"].strip(), row["Destination"].strip(), row["Requirement"].strip()
            if nat not in nats or dest not in dests:
                unknown_code += 1
                continue
            if val == "-1" or nat == dest:
                continue  # self pair; the matrix carries explicit NOT_APPLICABLE
            key = pair_key(nat, ORDINARY, dest)
            if key in existing:
                skipped_existing += 1
                continue
            days = None
            if val.isdigit():
                days = int(val)
                disp, outcome, cat = _VALUE_MAP["visa free"]
            elif val.lower() in _VALUE_MAP:
                disp, outcome, cat = _VALUE_MAP[val.lower()]
            else:
                unknown_code += 1
                continue
            chunk.append(dict(
                id=uuid.uuid4().hex, snapshot_date=SNAPSHOT_DATE, pair_key=key,
                passport_nationality=nat, travel_document_type=ORDINARY,
                destination_country=dest, disposition=disp, route_outcome=outcome,
                primary_category=cat, max_stay_days=days,
                source="reference_dataset", verification_status="provisional",
                source_ref=prov["source_url"],
                official_source_urls=[],
                retrieved_at=prov["retrieved_at"], dataset_date=prov["dataset_last_updated"],
                # Provisional data NEVER yields a covered release status —
                # even 'no admission' needs official confirmation first.
                release_status="defined_provisional",
                release_reason=("reference dataset reports no lawful tourist "
                                "admission; official confirmation required"
                                if outcome == "NO_AVAILABLE_TOURIST_ROUTE" else
                                "outcome defined from non-official reference dataset; "
                                "official verification pending"),
                notes="", ))
            existing[key] = ("reference_dataset", "provisional")
            if len(chunk) >= batch_size:
                _flush()
    _flush()
    return {"imported": imported, "skipped_existing": skipped_existing,
            "unknown_or_unmapped": unknown_code}


def _upsert_verified_pair(db, existing, *, nat: str, dest: str, disposition: str,
                          source_ref: str, urls: list[str], max_stay_days=None,
                          retrieved_at: str = "") -> str:
    """Write/upgrade one pair to verified official_research. Returns action."""
    outcome = DISPOSITION_TO_OUTCOME.get(disposition)
    if not outcome:
        return "skipped_meta_disposition"
    key = pair_key(nat, ORDINARY, dest)
    values = dict(
        disposition=disposition, route_outcome=outcome,
        primary_category=_DISPOSITION_TO_CATEGORY.get(disposition, ""),
        source="official_research", verification_status="verified",
        source_ref=source_ref[:400], official_source_urls=urls[:12],
        retrieved_at=retrieved_at,
        # A verified row must not retain provisional numbers or a stale
        # electronic-portal binding: max_stay_days comes only from the
        # research itself, dataset provenance is cleared, and the family
        # binding is recomputed by assign_families_to_pairs afterwards.
        max_stay_days=max_stay_days,
        dataset_date="",
        release_status=("legally_unavailable" if outcome == "NO_AVAILABLE_TOURIST_ROUTE"
                        else "defined_verified"),
        release_reason=("verified against official sources"
                        if outcome != "NO_AVAILABLE_TOURIST_ROUTE"
                        else "verified: no lawful tourist route"),
    )
    from . import ELECTRONIC_OUTCOMES
    if outcome not in ELECTRONIC_OUTCOMES and outcome != "ENTRY_PREPARATION":
        values["portal_family_id"] = ""
    if key in existing:
        db.execute(update(RoutePairPolicy)
                   .where(RoutePairPolicy.pair_key == key).values(**values))
        action = "upgraded"
    else:
        db.execute(insert(RoutePairPolicy), [dict(
            id=uuid.uuid4().hex, snapshot_date=SNAPSHOT_DATE, pair_key=key,
            passport_nationality=nat, travel_document_type=ORDINARY,
            destination_country=dest, notes="", **values)])
        action = "created"
    existing[key] = ("official_research", "verified")
    return action


def _parse_stay_days(maximum_stay) -> int | None:
    """First number only ('90 days per 180 days' -> 90, never 9018), bounded
    to a plausible stay; anything else is honestly None."""
    import re
    if isinstance(maximum_stay, int):
        return maximum_stay if 1 <= maximum_stay <= 366 else None
    if isinstance(maximum_stay, str) and "day" in maximum_stay.lower():
        m = re.search(r"\d{1,3}", maximum_stay)
        if m:
            days = int(m.group(0))
            return days if 1 <= days <= 366 else None
    return None


def apply_research_overrides(db, *, research_dir=None) -> dict:
    """Apply verified official research on top of the provisional baseline.

    Trusts ONLY (a) research files whose research_status == 'verified' and
    (b) VisaRoute records with research_status == 'verified'. Disposition
    groups use nationalities lists with ALL / ALL_OTHERS sentinels, applied in
    file order (ALL_OTHERS = nationalities not matched by an earlier group).
    """
    from ..visa_snapshot.pipeline import RESEARCH_DIR
    rdir = research_dir or RESEARCH_DIR
    nats = sorted(_nationality_codes())
    existing = _existing_pairs(db)
    stats = {"files_seen": 0, "files_verified": 0, "pairs_upgraded": 0,
             "pairs_created": 0, "routes_applied": 0, "groups_skipped": 0}

    if rdir.exists():
        for path in sorted(rdir.glob("*/research.json")):
            stats["files_seen"] += 1
            try:
                data = json.loads(path.read_text())
            except Exception:  # noqa: BLE001
                continue
            if data.get("research_status") != "verified":
                continue
            dest = (data.get("destination") or path.parent.name).upper()
            if dest not in _destination_codes():
                stats["groups_skipped"] += 1
                continue    # a mistyped destination must not mint verified rows
            stats["files_verified"] += 1
            assigned: set[str] = set()
            for group in data.get("dispositions") or []:
                disp = group.get("disposition")
                if disp not in DISPOSITION_TO_OUTCOME:
                    stats["groups_skipped"] += 1
                    continue
                listed = group.get("nationalities") or []
                if "ALL" in listed:
                    members = [n for n in nats if n != dest]
                elif "ALL_OTHERS" in listed:
                    members = [n for n in nats if n != dest and n not in assigned]
                else:
                    members = [n for n in listed if n in nats and n != dest]
                urls = [u for u in (group.get("source_urls") or []) if isinstance(u, str)]
                days = _parse_stay_days(group.get("maximum_stay"))
                for nat in members:
                    if nat in assigned:
                        continue
                    action = _upsert_verified_pair(
                        db, existing, nat=nat, dest=dest, disposition=disp,
                        source_ref=f"research:{dest}", urls=urls,
                        max_stay_days=days, retrieved_at=str(data.get("retrieved_at") or ""))
                    if action == "upgraded":
                        stats["pairs_upgraded"] += 1
                    elif action == "created":
                        stats["pairs_created"] += 1
                    assigned.add(nat)
            db.commit()

    # Verified on-demand route records are per-exact-route official research.
    for route in db.execute(select(VisaRoute).where(
            VisaRoute.research_status == "verified")).scalars().all():
        if route.disposition not in DISPOSITION_TO_OUTCOME:
            continue
        action = _upsert_verified_pair(
            db, existing, nat=route.passport_nationality,
            dest=route.destination_country, disposition=route.disposition,
            source_ref=f"route:{route.route_key[:380]}", urls=[])
        stats["routes_applied"] += 1
        if action == "upgraded":
            stats["pairs_upgraded"] += 1
        elif action == "created":
            stats["pairs_created"] += 1
    db.commit()
    return stats


def ensure_full_pair_coverage(db, *, batch_size: int = 4000) -> dict:
    """Materialize an explicit UNRESOLVED row for every remaining pair so no
    combination is silently absent. Self pairs get NOT_APPLICABLE."""
    nats = sorted(_nationality_codes())
    dests = sorted(_destination_codes())
    have = {k for (k,) in db.execute(select(RoutePairPolicy.pair_key)).all()}
    created = 0
    chunk: list[dict] = []

    def _flush():
        nonlocal created, chunk
        if chunk:
            db.execute(insert(RoutePairPolicy), chunk)
            db.commit()
            created += len(chunk)
            chunk = []

    nat_entries = {n["code"]: n for n in load_registry("nationalities")["entries"]}
    for nat in nats:
        own = nat if nat_entries[nat].get("kind") in ("state_nationality", "territory_document_class") else None
        for dest in dests:
            key = pair_key(nat, ORDINARY, dest)
            if key in have:
                continue
            is_self = own is not None and dest == own
            chunk.append(dict(
                id=uuid.uuid4().hex, snapshot_date=SNAPSHOT_DATE, pair_key=key,
                passport_nationality=nat, travel_document_type=ORDINARY,
                destination_country=dest,
                disposition="NOT_APPLICABLE" if is_self else "RESEARCH_INCOMPLETE",
                route_outcome="NO_AVAILABLE_TOURIST_ROUTE" if is_self else "UNRESOLVED",
                primary_category="", max_stay_days=None, source="none",
                verification_status="verified" if is_self else "unresolved",
                source_ref="own-state destination" if is_self else "",
                official_source_urls=[], retrieved_at="", dataset_date="",
                release_status="legally_unavailable" if is_self else "unresolved",
                release_reason=("destination is the holder's own state — no tourist "
                                "visa route exists" if is_self else
                                "no reference or official data for this pair yet — "
                                "official research required"),
                notes="", ))
            have.add(key)
            if len(chunk) >= batch_size:
                _flush()
    _flush()
    total = db.execute(select(func.count(RoutePairPolicy.id))).scalar_one()
    return {"created_unresolved_or_self": created, "total_pairs": total,
            "expected_pairs": len(nats) * len(dests)}


def sync_matrix_dispositions(db, *, batch_size: int = 800) -> dict:
    """Reflect VERIFIED pair policies into the structural matrix rows for the
    matching category (bulk, keyed by deterministic matrix_key).

    HONESTY-CRITICAL: only verification_status == 'verified' pairs may touch
    the matrix. RouteMatrixEntry carries no provenance column and every
    pre-existing consumer (resolution.py, coverage.py, ondemand.py) treats a
    researched disposition there as verified official research — so writing a
    provisional reference-dataset disposition into the matrix would launder
    non-official data into 'verified' answers. Provisional outcomes live ONLY
    in the pair-policy layer, which carries provenance. Rows still at
    RESEARCH_INCOMPLETE are the only update targets — a disposition set by
    the verified research pipeline is never overwritten either."""
    cats = [c["code"] for c in load_registry("tourist_visa_categories")["entries"]]
    keys_by_disposition: dict[str, list[str]] = {}
    pairs = db.execute(select(
        RoutePairPolicy.passport_nationality, RoutePairPolicy.destination_country,
        RoutePairPolicy.disposition, RoutePairPolicy.primary_category).where(
        RoutePairPolicy.travel_document_type == ORDINARY,
        RoutePairPolicy.verification_status == "verified",
        RoutePairPolicy.source == "official_research",
        RoutePairPolicy.disposition.notin_(("RESEARCH_INCOMPLETE", "NOT_APPLICABLE")))).all()
    for nat, dest, disp, cat in pairs:
        if disp == "NO_TOURIST_ROUTE":
            for c in cats:
                keys_by_disposition.setdefault(disp, []).append(matrix_key(nat, ORDINARY, dest, c))
            continue
        cat = cat or _DISPOSITION_TO_CATEGORY.get(disp, "")
        if not cat:
            continue
        keys_by_disposition.setdefault(disp, []).append(matrix_key(nat, ORDINARY, dest, cat))
    updated = 0
    for disp, keys in keys_by_disposition.items():
        for i in range(0, len(keys), batch_size):
            res = db.execute(update(RouteMatrixEntry).where(
                RouteMatrixEntry.matrix_key.in_(keys[i:i + batch_size]),
                RouteMatrixEntry.disposition == "RESEARCH_INCOMPLETE",
            ).values(disposition=disp))
            updated += res.rowcount or 0
        db.commit()
    return {"matrix_rows_updated": updated}
