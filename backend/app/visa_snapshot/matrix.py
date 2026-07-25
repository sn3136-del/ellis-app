"""Exhaustive normalized route matrix (brief section 8).

Normalization (documented; these ARE the matrix dimensions):
  - passport_nationality: every registry nationality code (state nationalities,
    HKG/MAC/TWN document classes, special classes XXA/XXB/XXC/XXX/GB*).
  - travel_document_type = ordinary_passport rows are enumerated PER
    NATIONALITY. The 7 non-ordinary document types are destination-policy
    driven, so they are enumerated per (document_type × destination × category)
    with passport_nationality = 'ANY' — an explicit grouped entry, never a
    silent absence. Per-nationality refinements are added when research shows a
    destination differentiates by nationality for that document type.
  - passport_issuing_country = 'DERIVED' (issuer follows the nationality's
    state / document class issuer). Routes where issuer differs materially are
    represented at the route-record level, keyed separately by routekey.py.
  - lawful_country_of_residence = 'ANY' at matrix level. Residence changes the
    application channel/jurisdiction (route records), rarely the visa
    disposition; where research shows a residence-dependent disposition, a
    per-residence override entry is added.
  - visa_subtype = 'any' at matrix level (subtypes live on route records).

Exclusions (documented, and still materialized as explicit rows):
  - destination == the nationality's own state -> disposition NOT_APPLICABLE
    (a state does not issue itself tourist visas). Row exists, not hidden.

Everything else defaults to RESEARCH_INCOMPLETE until the one-time research
pipeline links a verified policy. MATRIX_COMPLETENESS = created/expected and is
NEVER presented as requirements coverage (separate metrics in coverage.py).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, insert, select

from . import SNAPSHOT_DATE
from .models import RouteMatrixEntry
from .registry import load_registry

DISPOSITIONS = (
    "VISA_FREE", "ETA_REQUIRED", "EVISA_REQUIRED", "VISA_ON_ARRIVAL",
    "EMBASSY_VISA_REQUIRED", "AUTHORIZED_VISA_CENTER_REQUIRED",
    "TRANSIT_AUTHORIZATION_REQUIRED", "NO_TOURIST_ROUTE", "NOT_APPLICABLE",
    "RESEARCH_INCOMPLETE", "CONFLICTED", "HUMAN_REVIEW_REQUIRED",
)

ORDINARY = "ordinary_passport"


def _nationalities() -> list[dict]:
    return load_registry("nationalities")["entries"]


def _destinations() -> list[str]:
    return [c["alpha_3"] for c in load_registry("countries")["entries"]]


def _categories() -> list[str]:
    return [c["code"] for c in load_registry("tourist_visa_categories")["entries"]]


def _other_document_types() -> list[str]:
    return [d["code"] for d in load_registry("travel_document_types")["entries"]
            if d["code"] != ORDINARY]


def matrix_key(nat: str, doc: str, dest: str, cat: str) -> str:
    return f"mk1|nat={nat}|iss=DERIVED|doc={doc}|res=ANY|dest={dest}|cat={cat}|sub=any"


def expected_count() -> int:
    n_nat = len(_nationalities())
    n_dest = len(_destinations())
    n_cat = len(_categories())
    n_odoc = len(_other_document_types())
    return n_nat * n_dest * n_cat + n_odoc * n_dest * n_cat


def iter_entries():
    """Deterministic generator of every matrix entry dict (no DB access)."""
    dests = _destinations()
    cats = _categories()
    for nat in _nationalities():
        nat_code = nat["code"]
        nat_state = nat.get("alpha_2")  # None for special classes
        # Map the nationality's own state to alpha_3 for the NOT_APPLICABLE rule.
        own_alpha3 = nat_code if nat.get("kind") in ("state_nationality", "territory_document_class") else None
        for dest in dests:
            not_applicable = own_alpha3 is not None and dest == own_alpha3
            for cat in cats:
                yield {
                    "matrix_key": matrix_key(nat_code, ORDINARY, dest, cat),
                    "passport_nationality": nat_code,
                    "passport_issuing_country": "DERIVED",
                    "travel_document_type": ORDINARY,
                    "lawful_country_of_residence": "ANY",
                    "destination_country": dest,
                    "visa_category": cat,
                    "visa_subtype": "any",
                    "disposition": "NOT_APPLICABLE" if not_applicable else "RESEARCH_INCOMPLETE",
                    "excluded": False,
                    "exclusion_reason": ("destination is the holder's own state" if not_applicable else ""),
                }
    for doc in _other_document_types():
        for dest in dests:
            for cat in cats:
                yield {
                    "matrix_key": matrix_key("ANY", doc, dest, cat),
                    "passport_nationality": "ANY",
                    "passport_issuing_country": "DERIVED",
                    "travel_document_type": doc,
                    "lawful_country_of_residence": "ANY",
                    "destination_country": dest,
                    "visa_category": cat,
                    "visa_subtype": "any",
                    "disposition": "RESEARCH_INCOMPLETE",
                    "excluded": False,
                    "exclusion_reason": "",
                }


def populate(db, *, batch_size: int = 5000, progress=None) -> dict:
    """Idempotent bulk population. Existing matrix_keys are preserved (their
    researched dispositions are never reset); only missing rows are inserted."""
    existing = set(k for (k,) in db.execute(select(RouteMatrixEntry.matrix_key)).all())
    inserted = 0
    chunk: list[dict] = []

    def _flush():
        nonlocal inserted, chunk
        if chunk:
            db.execute(insert(RouteMatrixEntry), chunk)
            db.commit()
            inserted += len(chunk)
            if progress:
                progress(inserted)
            chunk = []

    for e in iter_entries():
        if e["matrix_key"] in existing:
            continue
        e = dict(e, id=uuid.uuid4().hex, snapshot_date=SNAPSHOT_DATE, route_id=None)
        chunk.append(e)
        if len(chunk) >= batch_size:
            _flush()
    _flush()
    total = db.execute(select(func.count(RouteMatrixEntry.id))).scalar_one()
    return {"expected": expected_count(), "existing_before": len(existing),
            "inserted": inserted, "total_now": total}


def completeness(db) -> dict:
    expected = expected_count()
    created = db.execute(select(func.count(RouteMatrixEntry.id)).where(
        RouteMatrixEntry.snapshot_date == SNAPSHOT_DATE)).scalar_one()
    by_disposition = {
        d: c for d, c in db.execute(
            select(RouteMatrixEntry.disposition, func.count(RouteMatrixEntry.id))
            .where(RouteMatrixEntry.snapshot_date == SNAPSHOT_DATE)
            .group_by(RouteMatrixEntry.disposition)).all()
    }
    return {
        "expected_entries": expected,
        "created_entries": created,
        "matrix_completeness": (created / expected) if expected else 0.0,
        "dispositions": by_disposition,
        "note": "matrix completeness is NOT requirements coverage",
    }
