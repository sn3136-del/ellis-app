"""Snapshot core: registries, normalization, route keys, schema validation, frozen-date policy."""
import json
from pathlib import Path

import pytest

from app.visa_snapshot import (
    AUTOMATIC_RULE_REFRESH_ENABLED,
    SNAPSHOT_DATE,
    SNAPSHOT_LABEL,
    UPDATE_MODE,
)
from app.visa_snapshot.registry import (
    REFERENCE_DIR,
    REGISTRY_FILES,
    RegistryError,
    load_registry,
    normalize_category,
    normalize_country,
    normalize_currency,
    normalize_document_type,
    normalize_nationality,
)
from app.visa_snapshot.routekey import RouteInput, canonical_hash, route_key
from app.visa_snapshot.validation import validate_record


def test_snapshot_is_frozen_manual_only():
    assert SNAPSHOT_DATE == "2026-07-23"
    assert "July 23, 2026" in SNAPSHOT_LABEL
    assert UPDATE_MODE == "manual"
    assert AUTOMATIC_RULE_REFRESH_ENABLED is False


def test_all_registries_exist_and_load():
    for name in REGISTRY_FILES:
        reg = load_registry(name)
        assert reg["registry"] == name
        assert reg["version"] >= 1
        assert reg["snapshot_date"] == SNAPSHOT_DATE
        assert isinstance(reg["entries"], list)
        assert reg["entry_count"] == len(reg["entries"])


def test_registry_counts_are_iso_complete():
    assert load_registry("countries")["entry_count"] == 249  # ISO 3166-1
    assert load_registry("currencies")["entry_count"] >= 170
    assert load_registry("languages")["entry_count"] >= 180
    # 9 since 2026-08-28: the PRC Travel Document (旅行证) joined the P0 set.
    assert load_registry("travel_document_types")["entry_count"] == 12
    # special nationality classes present
    codes = {e["code"] for e in load_registry("nationalities")["entries"]}
    assert {"XXA", "XXB", "GBN", "GBD"} <= codes


def test_country_normalization_equivalence():
    assert normalize_country("US") == "USA"
    assert normalize_country("usa") == "USA"
    assert normalize_country("United States") == "USA"
    assert normalize_country("cn") == "CHN"
    assert normalize_country("HK") == "HKG"
    with pytest.raises(RegistryError):
        normalize_country("Narnia")


def test_nationality_document_category_normalization():
    assert normalize_nationality("CHN") == "CHN"
    assert normalize_nationality("cn") == "CHN"
    assert normalize_nationality("XXA") == "XXA"
    assert normalize_document_type("Ordinary Passport") == "ordinary_passport"
    assert normalize_category("evisa_tourist", "single_entry") == ("evisa_tourist", "single_entry")
    assert normalize_category("tourist_visa", None) == ("tourist_visa", "any")
    with pytest.raises(RegistryError):
        normalize_category("tourist_visa", "airside_transit")
    assert normalize_currency("usd") == "USD"


def _inp(**kw):
    base = dict(
        passport_nationality="CHN", passport_issuing_country="CN",
        travel_document_type="ordinary_passport", lawful_country_of_residence="US",
        destination_country="JP", visa_category="tourist_visa", visa_subtype="single_entry",
    )
    base.update(kw)
    return RouteInput(**base)


def test_route_key_deterministic_and_normalizing():
    k1 = route_key(_inp())
    k2 = route_key(_inp(passport_issuing_country="chn", lawful_country_of_residence="United States",
                        destination_country="Japan"))
    assert k1 == k2
    assert k1.startswith("rk1|nat=CHN|iss=CHN|doc=ordinary_passport|res=USA|dest=JPN|")


def test_route_key_material_difference():
    assert route_key(_inp()) != route_key(_inp(destination_country="KR"))
    assert route_key(_inp()) != route_key(_inp(travel_document_type="refugee_travel_document"))
    assert route_key(_inp()) != route_key(_inp(visa_subtype="multiple_entry"))
    assert route_key(_inp()) != route_key(_inp(consular_jurisdiction="new-york"))


def test_route_key_rejects_non_tourism():
    with pytest.raises(RegistryError):
        route_key(_inp(travel_purpose="business"))


def test_canonical_hash_stable_and_excludes_bookkeeping():
    rec = {"a": 1, "b": [1, 2], "record_id": "x", "version": 3, "content_hash": "y", "retrieved_at": "now"}
    h1 = canonical_hash(rec)
    h2 = canonical_hash({"b": [1, 2], "a": 1, "record_id": "z", "version": 9, "content_hash": "q", "retrieved_at": "later"})
    assert h1 == h2
    assert h1 != canonical_hash({"a": 2, "b": [1, 2]})


def test_route_schema_validates_minimal_record():
    from app.visa_snapshot.routekey import canonical_hash as ch
    rec = {
        "snapshot_date": "2026-07-23", "record_id": "r-00000001", "version": 1,
        "route_key": route_key(_inp()),
        "passport_nationality": "CHN", "passport_issuing_country": "CHN",
        "travel_document_type": "ordinary_passport", "lawful_country_of_residence": "USA",
        "destination_country": "JPN", "visa_category": "tourist_visa", "visa_subtype": "single_entry",
        "travel_purpose": "tourism",
        "research_status": "not_researched", "human_review_status": "not_reviewed",
        "fee_status": "unknown", "portal_verification_status": "unknown",
        "adapter_status": "none", "conflict_status": "none",
    }
    rec["content_hash"] = ch(rec)
    assert validate_record("route", rec) == []
    bad = dict(rec, research_status="totally_fine")
    assert validate_record("route", bad) != []


def test_schema_rejects_wrong_snapshot_date():
    rec = {"snapshot_date": "2026-07-24"}
    errs = validate_record("route", rec)
    assert any("2026-07-23" in e or "const" in e for e in errs)


def test_no_recurring_refresh_anywhere():
    """No cron/interval/schedule-based rule refresh may exist in the snapshot
    package OR the Temporal app (the one-time research workflow is one-shot)."""
    app_dir = Path(__file__).resolve().parents[1] / "app"
    # API-usage tokens (not prose): actually creating a schedule/cron trigger.
    banned = ("schedule.every", "CronTrigger", "start_interval", "create_schedule",
              "ScheduleSpec", "ScheduleActionStartWorkflow", "cron_schedule")
    targets = list((app_dir / "visa_snapshot").rglob("*.py")) + [app_dir / "temporal_app.py"]
    for py in targets:
        text = py.read_text(encoding="utf-8")
        for token in banned:
            assert token not in text, f"{py.name} contains banned recurring-refresh token {token!r}"
