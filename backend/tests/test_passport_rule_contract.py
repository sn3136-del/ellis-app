"""Source corrections must use passport rules that the consumer can enforce."""
from copy import deepcopy
from datetime import date
import json

import pytest

from app.passport_validity import (passport_validity_rule_errors, required_valid_until,
                                  normalize_passport_validity_rule)
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
    "valid_for_duration_of_stay", [], 6, True,
    {"kind": None, "months": 0},
    {"kind": None, "months": 6},
    {"kind": "", "months": None},
    {"kind": None, "months": None, "source": None},
    {"kind": "duration_of_intended_stay", "months": None},
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


@pytest.mark.parametrize("value", [{}, {"kind": None}, {"months": None}, {"kind": None, "months": None}])
def test_empty_legacy_contract_is_unknown_at_every_boundary(value, monkeypatch):
    from app.visa_snapshot.evidence_validator import field_value_supported
    monkeypatch.setattr(vo, "find", lambda route: None)
    original = deepcopy(value)
    raw = {"passport_validity_requirement": value, "passport_validity": "unknown"}
    assert normalize_passport_validity_rule(value) is None
    assert passport_validity_rule_errors(value) == []
    assert vo._field_errors(raw) == []
    clean, _, problems = kimi_primary.validate_answer(raw)
    assert clean["passport_validity_requirement"] is None
    assert not any("passport_validity_requirement" in p for p in problems)
    merged, proof = vo.apply(raw, {})
    customer = kimi_primary.apply_verified_overrides(kimi_primary._result(
        kimi_primary.STATUS_PRIMARY, raw, cached=True, stale=False), {})
    assert merged["passport_validity_requirement"] is None
    assert customer["guidance"] == merged and proof is None
    assert required_valid_until(value, date(2026, 10, 1), date(2026, 10, 15)) == (None, "")
    # An unknown value cannot be credited as a supported passport constraint.
    assert not field_value_supported("passport_validity_requirement", value,
                                     "Your passport must be valid throughout your stay.")
    assert value == original and raw["passport_validity_requirement"] == original


def test_unknown_synonym_is_not_normalized_from_plausible_prose(monkeypatch):
    monkeypatch.setattr(vo, "find", lambda route: None)
    rule = {"kind": "duration_of_intended_stay", "months": None}
    raw = {"disposition": "VISA_REQUIRED", "requirement_detail": "paper_visa",
           "passport_validity_requirement": rule,
           "passport_validity": "Valid for at least the duration of intended stay in China"}
    merged, _ = vo.apply(raw, {})
    assert merged["passport_validity_requirement"] == rule
    assert kimi_primary.serve_time_invariants(merged) == [
        "passport_validity_requirement has an unknown kind"]


@pytest.mark.parametrize("rule", [{"kind": None, "months": None},
                                  {"kind": "duration_of_intended_stay", "months": None}])
def test_saved_case_unknown_rule_never_becomes_a_satisfied_constraint(rule, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from app import passport_validity as pv, rules
    monkeypatch.setattr(rules, "latest_rule", lambda *a, **kw: None)
    db = Mock()
    db.query.return_value.filter_by.return_value.first.return_value = SimpleNamespace(
        guidance={"guidance": {"passport_validity_requirement": rule,
                              "passport_validity": "Typically 6 months beyond intended stay"}})
    app_row = SimpleNamespace(id="contract-case", destination_country="ERI", visa_type="tourism",
        answers={"expiry_date": "2026-11-01", "intended_arrival": "2026-10-01",
                 "intended_departure": "2026-10-15", "issuing_country": "USA"})
    assert pv._guidance_rule(db, app_row) is None
    result = pv.check_case_passport(db, app_row, today=date(2026, 9, 9))
    assert result["status"] == "ok_rule_unverified"
    assert "rule" not in result and "required_valid_until" not in result
    assert "has not been verified" in result["explanation"]


def test_normalization_never_clears_persisted_disagreements_or_claims(monkeypatch):
    monkeypatch.setattr(vo, "find", lambda route: None)
    raw = {"disposition": "VISA_REQUIRED", "requirement_detail": "paper_visa",
           "passport_validity_requirement": {"kind": None, "months": None},
           "passport_validity": "Typically 6 months beyond intended stay; confirm exact rule with embassy"}
    issues = ["passport_validity_requirement has an unknown kind", "Source eligibility dispute"]
    out = kimi_primary.apply_verified_overrides(kimi_primary._result(
        kimi_primary.STATUS_PRIMARY, raw, cached=True, stale=False, contradictions=issues), {})
    assert out["guidance"]["passport_validity_requirement"] is None
    assert out["guidance"]["passport_validity"] == raw["passport_validity"]
    assert out["contradictions"] == issues
    from app.visa_snapshot.records_guard import apply_records_hold
    assert apply_records_hold({}, out)["held"] is True


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


def test_operator_unknown_contract_is_persisted_as_null(override_files):
    _, path = override_files
    entry = reviewed_entry({"kind": None, "months": None})
    vo.append_operator_entry(entry)
    saved = json.loads(path.read_text())
    assert saved[-1]["fields"]["passport_validity_requirement"] is None
    found = vo.find({"passport_nationality": "IDN", "destination_country": "KOR",
                     "travel_purpose": "tourism"})
    assert found["fields"]["passport_validity_requirement"] is None
    assert entry["fields"]["passport_validity_requirement"] == {"kind": None, "months": None}


def test_freshness_legacy_unknown_keeps_the_quote_gate():
    from app.visa_snapshot.freshness import _quoted_proposals
    field = "passport_validity_requirement"
    quote = "No specific passport rule is published here."
    proposal = {"corrected_fields": {field: {"kind": None, "months": None}}, "evidence": {field: quote}}
    quoted, _, rejected = _quoted_proposals(proposal, quote)
    assert quoted == {field: None} and rejected == []
    quoted, _, rejected = _quoted_proposals(proposal, "An unrelated document.")
    assert quoted == {} and rejected == [field]


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
