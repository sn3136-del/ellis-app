"""Source corrections must use passport rules that the consumer can enforce."""
from copy import deepcopy
from datetime import date
import json

import pytest

from app.passport_validity import passport_validity_rule_errors, required_valid_until
from app.visa_snapshot import kimi_primary, tstation, verified_overrides as vo


@pytest.mark.parametrize("value", [
    None,
    {"kind": "valid_on_arrival"},
    {"kind": "valid_on_arrival", "months": None},
    {"kind": "valid_on_arrival", "months": 0},
    {"kind": "valid_through_departure", "months": None},
    {"kind": "valid_through_departure", "months": 0},
    {"kind": "months_after_arrival", "months": 6},
    {"kind": "months_after_departure", "months": 3},
])
def test_supported_contract_is_not_changed(value):
    before = deepcopy(value)
    assert passport_validity_rule_errors(value) == []
    assert value == before
    assert vo._field_errors({"passport_validity_requirement": value}) == []


@pytest.mark.parametrize("value", [
    "valid_for_duration_of_stay", [], 6, True, {},
    {"kind": None, "months": None},
    {"kind": ["valid_on_arrival"], "months": 0},
    {"kind": "valid_for_duration_of_stay", "months": 0},
    {"kind": "minimum_remaining_months", "months": 6},
    {"kind": "months_after_application", "months": 6},
    {"kind": "valid_on_arrival", "months": 6},
    {"kind": "valid_through_departure", "months": 3},
    {"kind": "valid_through_departure", "months": False},
    {"kind": "valid_through_departure", "months": "0"},
    {"kind": "months_after_arrival"},
    {"kind": "months_after_arrival", "months": None},
    {"kind": "months_after_arrival", "months": 0},
    {"kind": "months_after_arrival", "months": -6},
    {"kind": "months_after_arrival", "months": True},
    {"kind": "months_after_arrival", "months": "6"},
    {"kind": "months_after_arrival", "months": 6.9},
    {"kind": "months_after_arrival", "months": float("inf")},
    {"kind": "months_after_arrival", "months": float("nan")},
    {"kind": "valid_through_departure", "months": 0, "application_months": 6},
])
def test_unsupported_rule_is_rejected_at_override_boundary(value):
    errors = passport_validity_rule_errors(value)
    assert errors and all(e.startswith("passport_validity_requirement") for e in errors)
    assert vo._field_errors({"passport_validity_requirement": value}) == errors


def test_duration_of_stay_uses_departure_without_six_month_addition():
    rule = {"kind": "valid_through_departure", "months": 0}
    departure = date(2026, 10, 15)
    assert passport_validity_rule_errors(rule) == []
    assert required_valid_until(rule, date(2026, 10, 1), departure) == (
        departure, "valid through your departure date")


def test_malformed_raw_cached_rule_is_held_even_with_verdict_evidence():
    guidance = {
        "disposition": "VISA_EXEMPT", "requirement_detail": "unconditional_visa_free",
        "permitted_stay": "30 days", "permitted_stay_days": 30,
        "source_url": "https://www.mofa.go.jp/visa/",
        "passport_validity_requirement": {"kind": "valid_for_duration_of_stay", "months": 0},
    }
    provenance = {
        "fields": ["disposition"], "verifier": "human", "verified_at": date.today().isoformat(),
        "source_url": guidance["source_url"],
        "note": "Fixture: ordinary passport holders visiting for tourism need no visa.",
    }
    assert any("passport_validity_requirement" in error
               for error in kimi_primary.serve_time_invariants(guidance))
    records = tstation.records_for_route(
        {"passport_nationality": "ZZZ", "destination_country": "JPN", "travel_purpose": "tourism"},
        guidance, provenance, grounded_ok=True)
    assert records and all(record["confidence_level"] == "Low" for record in records)


@pytest.fixture
def override_files(tmp_path, monkeypatch):
    seed = tmp_path / "seed.json"
    operator = tmp_path / "operator.json"
    seed.write_text("[]")
    operator.write_text("[]\n")
    monkeypatch.setattr(vo, "OVERRIDES", seed)
    monkeypatch.setenv("ELLIS_OPERATOR_OVERRIDES", str(operator))
    vo.reload()
    yield seed, operator
    vo.reload()


def reviewed_entry(rule):
    return {
        "route": {"nationality": "IDN", "destination": "KOR", "travel_purpose": "tourism"},
        "source_url": "https://www.mofa.go.kr/my-en/brd/m_21504/view.do?page=1&seq=46",
        "verified_at": date.today().isoformat(), "verified_by": "Contract test",
        "verifier": "ai", "note": "Fixture source review for the passport field.",
        "fields": {"passport_validity_requirement": rule},
    }


def test_operator_cannot_persist_a_noncanonical_source_proposal(override_files):
    _, path = override_files
    before = path.read_bytes()
    with pytest.raises(ValueError, match="passport_validity_requirement"):
        vo.append_operator_entry(reviewed_entry(
            {"kind": "valid_for_duration_of_stay", "months": 0}))
    assert path.read_bytes() == before


def test_canonical_operator_rule_survives_serialization(override_files):
    _, path = override_files
    rule = {"kind": "valid_through_departure", "months": 0}
    vo.append_operator_entry(reviewed_entry(rule))
    saved = json.loads(path.read_text())
    assert saved[-1]["fields"]["passport_validity_requirement"] == rule


def test_historical_malformed_rule_does_not_discard_other_verified_facts(override_files):
    seed, _ = override_files
    record = reviewed_entry({"kind": "minimum_remaining_months", "months": 6})
    record["fields"]["permitted_stay"] = "30 days"
    seed.write_text(json.dumps([record]))
    vo.reload()
    result = vo.find({"passport_nationality": "IDN", "destination_country": "KOR",
                      "travel_purpose": "tourism"})
    assert result["fields"]["permitted_stay"] == "30 days"
    assert "passport_validity_requirement" not in result["fields"]
