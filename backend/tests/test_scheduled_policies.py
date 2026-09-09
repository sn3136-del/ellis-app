from copy import deepcopy
from datetime import date
import json

import pytest

from app.visa_snapshot import scheduled_policies as sp, kimi_primary as kp

BASE = {"disposition": "VISA_EXEMPT", "requirement_detail": "unconditional_visa_free",
        "permitted_stay_days": 60, "permitted_stay": "60 days",
        "government_fee": {"amount": 0, "currency": None},
        "visa_products": [{"type": "Outdated tourist visa", "fee": {"amount": 25}}],
        "arrival_card": {"required": True, "name": "Thailand Digital Arrival Card"},
        "required_documents": ["Valid passport", "Proof of onward travel"],
        "passport_validity": "At least six months", "health_requirements": [{"name": "Keep separate evidence"}],
        "exceptions": ["Hong Kong passport holders may stay 60 days. Proof of sufficient funds may be requested.",
                       "Extension before the 60-day limit, entry remains subject to immigration inspection."]}
ROUTE = {"passport_nationality": "HKG", "destination_country": "THA", "travel_purpose": "tourism",
         "travel_document_type": "ordinary_passport"}


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(sp, "_today", lambda: date(2026, 9, 9))


def rows():
    return json.loads(sp.POLICIES.read_text())


@pytest.mark.parametrize("arrival,stay", [("2026-09-14", 60), ("2026-09-15", 30)])
def test_effective_day_switches_answer_without_forking_canonical_key(arrival, stay):
    original = deepcopy(BASE)
    g, prov = sp.apply(BASE, None, {**ROUTE, "arrival_date": arrival})
    assert g["permitted_stay_days"] == stay
    assert BASE == original
    assert kp.cache_key({**ROUTE, "arrival_date": arrival}) == kp.cache_key(ROUTE)
    if stay == 60:
        assert g["upcoming_policy"]["effective_from"] == "2026-09-15" and prov is None
    else:
        assert prov["verifier"] == "ai"
        assert all(p["verifier"] == "ai" for p in prov["field_provenance"].values())
        assert g["scheduled_policy"]["date_used"] == arrival


def test_all_and_only_seventeen_manifest_nationalities_are_scheduled():
    loaded = sp._parse_rows(rows())
    assert len(loaded) == 17
    assert {r["route"]["nationality"] for r in loaded} == {"KOR", "HKG", "RUS", "VNM", "TWN", "JPN", "USA", "SGP", "MYS", "GBR", "AUS", "IDN", "PHL", "FRA", "ESP", "IND", "CAN"}
    for row in loaded:
        nat = row["route"]["nationality"]
        g, _ = sp.apply(BASE, None, {**ROUTE, "passport_nationality": nat, "arrival_date": "2026-09-15"})
        assert g["permitted_stay_days"] == (90 if nat == "KOR" else 30)
    assert sp.apply(BASE, None, {**ROUTE, "passport_nationality": "CHN", "arrival_date": "2026-09-15"}) == (BASE, None)


@pytest.mark.parametrize("change", [{"travel_document_type": "diplomatic_passport"}, {"travel_purpose": "business"},
                                    {"destination_country": "JPN"}, {"travel_document_type": "prc_travel_document"}])
def test_document_purpose_destination_isolation(change):
    assert sp.apply(BASE, None, {**ROUTE, "arrival_date": "2026-09-15", **change}) == (BASE, None)


def test_independent_arrival_and_admission_requirements_survive():
    g, _ = sp.apply(BASE, None, {**ROUTE, "arrival_date": "2026-09-15"})
    for field in ("arrival_card", "required_documents", "passport_validity", "health_requirements"):
        assert g[field] == BASE[field]
    assert g["visa_products"] == []
    assert "Hong Kong passport holders may stay 60 days." not in g["exceptions"]
    assert "Extension before the 60-day limit" in g["exceptions"]
    assert g["scheduled_policy_conflict"]["fields"] == ["exceptions"]
    assert "Proof of sufficient funds may be requested." in g["exceptions"]
    assert "entry remains subject to immigration inspection." in g["exceptions"]


@pytest.mark.parametrize("arrival", [None, "invalid", "2026-02-30", "20260915"])
def test_invalid_or_absent_date_uses_current_policy(arrival, monkeypatch):
    g, _ = sp.apply(BASE, None, {**ROUTE, "arrival_date": arrival})
    assert g["permitted_stay_days"] == 60
    monkeypatch.setattr(sp, "_today", lambda: date(2026, 9, 15))
    g, _ = sp.apply(BASE, None, {**ROUTE, "arrival_date": arrival})
    assert g["permitted_stay_days"] == 30


@pytest.mark.parametrize("verifier,when", [("human", "2026-09-08"), ("ai", "2026-09-20")])
def test_human_or_newer_verified_conflict_is_not_silently_overridden(verifier, when):
    prov = {"fields": ["permitted_stay_days"], "verifier": verifier, "verified_at": when,
            "source_url": "https://consular.mfa.go.th/new-rule"}
    g, actual = sp.apply(BASE, prov, {**ROUTE, "arrival_date": "2026-09-21"})
    assert actual == prov
    assert g["permitted_stay_days"] == 60
    assert g["scheduled_policy_conflict"]["fields"] == ["permitted_stay_days"]
    assert "scheduled_policy" not in g


def test_unrelated_human_verified_arrival_card_does_not_block_schedule():
    source = {"verifier": "human", "verified_at": "2026-09-09", "source_url": "https://tdac.immigration.go.th/"}
    prov = {**source, "fields": ["arrival_card"], "field_provenance": {"arrival_card": source}}
    g, p = sp.apply(BASE, prov, {**ROUTE, "arrival_date": "2026-09-15"})
    assert g["permitted_stay_days"] == 30
    assert p["field_provenance"]["arrival_card"] == source
    assert p["field_provenance"]["disposition"]["verifier"] == "ai"


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(source_url="https://travel-blog.example/policy"),
    lambda r: r.update(verifier="human"),
    lambda r: r["route"].update(destination="JPN"),
    lambda r: r.update(effective_to="2026-10-01"),
    lambda r: r.update(effective_from="2026-09-14"),
    lambda r: r.update(effective_to="invalid"),
    lambda r: r["evidence"]["quotes"].update(nationality="Unquoted nationality"),
    lambda r: r["fields"].update(permitted_stay_days=60),
    lambda r: r["fields"].update(arrival_card=None),
    lambda r: r["route"].update(travel_document_type="diplomatic_passport"),
])
def test_malformed_or_unsubstantiated_schedule_is_rejected(mutation):
    row = deepcopy(rows()[0]); mutation(row)
    with pytest.raises(ValueError):
        sp._parse_rows([row])


def test_overlapping_routes_are_rejected_instead_of_selecting_one():
    first = rows()[0]
    second = deepcopy(first); second["id"] += "-conflict"
    with pytest.raises(ValueError, match="overlapping"):
        sp._parse_rows([first, second])


def test_invalid_store_cannot_keep_applying_previously_loaded_policy(tmp_path, monkeypatch):
    path = tmp_path / "scheduled.json"
    path.write_text(json.dumps(rows()))
    monkeypatch.setattr(sp, "POLICIES", path)
    assert len(sp._load()) == 17
    path.write_text("not json")
    assert sp._load() == []
