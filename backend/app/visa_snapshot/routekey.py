"""Deterministic route-key construction (brief section 10).

The key is a readable canonical composite over the normalized components.
Equivalent normalized inputs -> identical key; materially different routes ->
different keys. The canonical content hash for versioning is separate
(see importer.canonical_hash).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from . import SNAPSHOT_DATE
from .registry import (
    RegistryError,
    normalize_category,
    normalize_country,
    normalize_document_type,
    normalize_nationality,
)

KEY_VERSION = "rk1"


@dataclass(frozen=True)
class RouteInput:
    passport_nationality: str
    passport_issuing_country: str
    travel_document_type: str
    lawful_country_of_residence: str
    destination_country: str
    visa_category: str
    visa_subtype: str | None = None
    travel_purpose: str = "tourism"
    policy_period: str | None = None      # applicable travel-date policy period
    consular_jurisdiction: str | None = None


def normalize_route(inp: RouteInput) -> dict:
    nat = normalize_nationality(inp.passport_nationality)
    # Issuers can be territories (alpha-2 in the issuer registry) or states.
    iss = normalize_country(inp.passport_issuing_country, field="passport_issuing_country")
    doc = normalize_document_type(inp.travel_document_type)
    res = normalize_country(inp.lawful_country_of_residence, field="lawful_country_of_residence")
    dest = normalize_country(inp.destination_country, field="destination_country")
    cat, sub = normalize_category(inp.visa_category, inp.visa_subtype)
    purpose = (inp.travel_purpose or "tourism").strip().lower()
    if purpose != "tourism":
        raise RegistryError(f"travel_purpose: only 'tourism' supported in this snapshot, got {inp.travel_purpose!r}")
    period = (inp.policy_period or SNAPSHOT_DATE).strip()
    jur = (inp.consular_jurisdiction or "default").strip().lower().replace(" ", "_")
    return {
        "passport_nationality": nat,
        "passport_issuing_country": iss,
        "travel_document_type": doc,
        "lawful_country_of_residence": res,
        "destination_country": dest,
        "visa_category": cat,
        "visa_subtype": sub,
        "travel_purpose": purpose,
        "policy_period": period,
        "consular_jurisdiction": jur,
    }


def route_key(inp: RouteInput) -> str:
    n = normalize_route(inp)
    return "|".join([
        KEY_VERSION,
        f"nat={n['passport_nationality']}",
        f"iss={n['passport_issuing_country']}",
        f"doc={n['travel_document_type']}",
        f"res={n['lawful_country_of_residence']}",
        f"dest={n['destination_country']}",
        f"cat={n['visa_category']}",
        f"sub={n['visa_subtype']}",
        f"pur={n['travel_purpose']}",
        f"per={n['policy_period']}",
        f"jur={n['consular_jurisdiction']}",
    ])


def canonical_hash(record: dict) -> str:
    """Canonical content hash over a record, excluding volatile bookkeeping."""
    volatile = {"content_hash", "record_id", "version", "retrieved_at"}
    body = {k: v for k, v in sorted(record.items()) if k not in volatile}
    blob = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
