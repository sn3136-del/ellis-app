"""Destination inventory (brief Part 1): every recognized destination
jurisdiction with its identified official application channels, deduplicated
by portal family. Persisted as destination_inventory.jsonl in the snapshot.

A channel appears ONLY when backed by data: a registered portal family, a
verified snapshot portal record, or pair policies that resolve to that
channel kind. A destination with no lawful tourist channel for anyone is
reported exactly so — never invented.
"""
from __future__ import annotations

import json

from sqlalchemy import func, select

from ..visa_snapshot import SNAPSHOT_DATE
from ..visa_snapshot.models import OfficialPortalRecord
from ..visa_snapshot.registry import SNAPSHOT_DIR, load_registry
from .models import PortalFamily, RoutePairPolicy

_OUTCOME_TO_CHANNEL = {
    "VISA_EXEMPT": "visa_exempt_preparation",
    "ENTRY_PREPARATION": "electronic_arrival_form",
    "ELECTRONIC_AUTHORIZATION": "electronic_travel_authorization",
    "EVISA": "official_evisa_portal",
    "VISA_ON_ARRIVAL": "visa_on_arrival_process",
    "EMBASSY_OR_CONSULATE_APPLICATION": "embassy_or_consular_application",
    "AUTHORIZED_VISA_CENTER": "authorized_visa_center",
    "MAIL_APPLICATION": "mail_application",
    "APPOINTMENT_REQUIRED": "appointment_based_application",
    "NO_AVAILABLE_TOURIST_ROUTE": "no_lawful_tourist_entry_route",
    "REQUIRES_MANUAL_JURISDICTION_SELECTION": "embassy_or_consular_application",
}


def build_inventory(db) -> list[dict]:
    countries = load_registry("countries")["entries"]
    fam_by_dest: dict[str, list[PortalFamily]] = {}
    for fam in db.execute(select(PortalFamily).where(
            PortalFamily.active.is_(True))).scalars():
        for d in fam.destinations or []:
            fam_by_dest.setdefault(d, []).append(fam)
    portal_by_dest: dict[str, int] = {d: c for d, c in db.execute(
        select(OfficialPortalRecord.destination_country, func.count())
        .where(OfficialPortalRecord.verification_status.in_(
            ("verified_official_domain", "verified_via_official_link")))
        .group_by(OfficialPortalRecord.destination_country)).all()}
    outcome_rows = db.execute(
        select(RoutePairPolicy.destination_country, RoutePairPolicy.route_outcome,
               RoutePairPolicy.verification_status, func.count())
        .group_by(RoutePairPolicy.destination_country,
                  RoutePairPolicy.route_outcome,
                  RoutePairPolicy.verification_status)).all()
    outcomes_by_dest: dict[str, dict] = {}
    for dest, outcome, verification, count in outcome_rows:
        d = outcomes_by_dest.setdefault(dest, {})
        o = d.setdefault(outcome, {"total": 0, "verified": 0, "provisional": 0})
        o["total"] += count
        if verification in ("verified", "provisional"):
            o[verification] += count

    inventory = []
    for c in countries:
        dest = c["alpha_3"]
        fams = fam_by_dest.get(dest, [])
        dest_outcomes = outcomes_by_dest.get(dest, {})
        channels = sorted({_OUTCOME_TO_CHANNEL[o] for o in dest_outcomes
                           if o in _OUTCOME_TO_CHANNEL})
        inventory.append({
            "snapshot_date": SNAPSHOT_DATE,
            "destination_country": dest,
            "name": c["name"],
            "is_territory": bool(c.get("is_territory")),
            "channels": channels,
            "portal_families": [
                {"family_id": f.family_id, "kind": f.kind,
                 "operator_kind": f.operator_kind,
                 "verification_status": f.verification_status,
                 "url": f.base_url} for f in sorted(fams, key=lambda f: f.family_id)],
            "verified_snapshot_portals": portal_by_dest.get(dest, 0),
            "inbound_outcomes": dest_outcomes,
            "has_defined_outcome_for_every_nationality": bool(dest_outcomes) and
                "UNRESOLVED" not in {o for o, v in dest_outcomes.items()
                                     if v["total"] > 0 and o == "UNRESOLVED"},
        })
    return inventory


def export_inventory(db) -> dict:
    """Write destination_inventory.jsonl + global_route_pairs.jsonl into the
    frozen snapshot directory."""
    outdir = SNAPSHOT_DIR / SNAPSHOT_DATE
    outdir.mkdir(parents=True, exist_ok=True)
    inv = build_inventory(db)
    with open(outdir / "destination_inventory.jsonl", "w", encoding="utf-8") as fh:
        for row in inv:
            fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
    pair_count = 0
    with open(outdir / "global_route_pairs.jsonl", "w", encoding="utf-8") as fh:
        for p in db.execute(select(RoutePairPolicy)
                            .order_by(RoutePairPolicy.pair_key)).scalars():
            fh.write(json.dumps({
                "snapshot_date": p.snapshot_date, "pair_key": p.pair_key,
                "passport_nationality": p.passport_nationality,
                "travel_document_type": p.travel_document_type,
                "destination_country": p.destination_country,
                "disposition": p.disposition, "route_outcome": p.route_outcome,
                "primary_category": p.primary_category,
                "max_stay_days": p.max_stay_days, "source": p.source,
                "verification_status": p.verification_status,
                "source_ref": p.source_ref,
                "official_source_urls": p.official_source_urls or [],
                "retrieved_at": p.retrieved_at, "dataset_date": p.dataset_date,
                "portal_family_id": p.portal_family_id,
                "release_status": p.release_status,
                "release_reason": p.release_reason,
            }, sort_keys=True, ensure_ascii=False) + "\n")
            pair_count += 1
    fam_count = 0
    with open(outdir / "global_portal_families.jsonl", "w", encoding="utf-8") as fh:
        for f in db.execute(select(PortalFamily)
                            .order_by(PortalFamily.family_id)).scalars():
            fh.write(json.dumps({
                "family_id": f.family_id, "name": f.name, "kind": f.kind,
                "operator": f.operator, "operator_kind": f.operator_kind,
                "base_url": f.base_url, "hostnames": f.hostnames or [],
                "destinations": f.destinations or [],
                "nationality_scope": f.nationality_scope or [],
                "supported_outcomes": f.supported_outcomes or [],
                "account_required": bool(f.account_required),
                "verification_status": f.verification_status,
                "last_verified_at": str(f.last_verified_at) if f.last_verified_at else None,
                "stale": bool(f.stale), "active": bool(f.active),
            }, sort_keys=True, ensure_ascii=False) + "\n")
            fam_count += 1
    return {"destinations": len(inv), "pairs_exported": pair_count,
            "families_exported": fam_count, "dir": str(outdir)}
