"""A stay-only schedule cannot silently rewrite independent entry evidence."""
from copy import deepcopy
from datetime import date
import json

import pytest

from app.visa_snapshot import scheduled_policies as sp, kimi_primary as kp

ROUTE = {"passport_nationality": "SGP", "destination_country": "THA", "travel_purpose": "tourism",
         "travel_document_type": "ordinary_passport", "arrival_date": "2026-09-15"}
BASE = {"disposition": "VISA_EXEMPT", "requirement_detail": "unconditional_visa_free",
        "permitted_stay_days": 60, "permitted_stay": "60 days", "visa_products": [],
        "application_channel": "not_required", "government_fee": {"amount": 0, "currency": None}}
ORIGINAL_SOURCE = {"source_url": "https://www.thaiembassy.sg/visa-matters-forms",
                   "verified_at": "2026-09-08", "verifier": "ai", "note": "Existing entry-condition source."}


@pytest.fixture(autouse=True)
def fixed_policy(monkeypatch):
    # Use a real source-validated shipped policy, isolated from manifest count changes.
    rows = sp._parse_rows(json.loads(sp.POLICIES.read_text()))
    policy = next(row for row in rows if row["route"]["nationality"] == "SGP")
    monkeypatch.setattr(sp, "_load", lambda: [deepcopy(policy)])
    monkeypatch.setattr(sp, "_today", lambda: date(2026, 9, 9))


def apply(field, value, *, arrival="2026-09-15"):
    guidance = {**BASE, field: value}
    proof = {"fields": [field], "field_provenance": {field: deepcopy(ORIGINAL_SOURCE)}}
    original = deepcopy((guidance, proof))
    g, prov = sp.apply(guidance, proof, {**ROUTE, "arrival_date": arrival})
    assert (guidance, proof) == original
    return g, prov


@pytest.mark.parametrize("field,value", [
    ("onward_travel_evidence", "Return or onward ticket dated within the 60-day visa-exempt period."),
    ("onward_travel_evidence", "Onward ticket within sixty (60) calendar days of arrival."),
    ("entry_requirements", "Proof of an onward ticket departing within 60 days is required."),
    ("entry_requirements", ["Passport", "Visitors may stay for 60 days without a visa."]),
    ("entry_requirements", "Visitors may stay not exceeding 60 days."),
    ("entry_requirements", "Visitors may stay no longer than 60 days."),
    ("entry_requirements", "Before 15 September 2026 the stay was 60 days, but onward tickets must still depart within 60 days."),
    ("entry_requirements", "Previously the limit was 45 days and onward tickets must now depart within 60 days."),
    ("entry_requirements", "Onward tickets must depart within 60 days; fees were previously waived."),
    ("required_documents", ["Passport", "Onward ticket within the 60–day visa-exempt period"]),
    ("required_documents", [{"name": "Return ticket", "note": "Return ticket before 60 days after entry."}]),
    ("accommodation_evidence", "Booking covering the sixty-day permitted stay."),
    ("financial_evidence", "Funds sufficient for a 60-day stay."),
    ("exceptions", ["Apply for an extension before the 60-day limit."]),
])
def test_obsolete_stay_dependency_is_preserved_and_held_with_its_original_field_proof(field, value):
    g, prov = apply(field, value)
    assert g["permitted_stay_days"] == 30
    if field == "exceptions":
        for item in value:
            assert item in g[field]
    else:
        assert g[field] == value
    conflict = g["scheduled_policy_conflict"]
    assert conflict["fields"] == [field]
    assert conflict["dependent_stay_claims"][0]["superseded_stay_days"] == 60
    assert conflict["dependent_stay_claims"][0]["path"].startswith(field)
    field_proof = prov["field_provenance"][field]
    if field == "exceptions":
        assert field_proof["prior_evidence"] == ORIGINAL_SOURCE
        assert field_proof["scope"] == "scheduled_exemption_additions_only"
    else:
        assert field_proof == ORIGINAL_SOURCE
    assert any("scheduled_policy" in problem for problem in kp.serve_time_invariants(g))


@pytest.mark.parametrize("field,value", [
    ("passport_validity", "At least six months remaining at entry."),
    ("required_documents", ["Passport valid for 60 days after departure.", "Visa-exempt entry permitted."]),
    ("entry_requirements", "Passport valid for 60 days after departure. Return ticket required."),
    ("entry_requirements", "Pay a fee of THB 60. Onward ticket required."),
    ("entry_requirements", "No more than 180 days in any calendar year; passport valid six months."),
    ("required_documents", ["Hotel reservation", "Travel insurance valid for at least 60 days."]),
    ("onward_travel_evidence", "Refundable return ticket whose fare credit remains valid for 60 days."),
    ("onward_travel_evidence", "Return ticket within the permitted stay."),
    ("entry_requirements", "Submit application at least 60 days before travel."),
    ("exceptions", ["Extensions of 60 days may be available under a separate permission."]),
    ("exceptions", ["An independent health certificate is valid for 60 days."]),
    ("entry_requirements", "Before 15 September 2026, the permitted stay was 60 days."),
    ("entry_requirements", "The 60-day visa-exempt period no longer applies."),
    ("entry_requirements", "Previously the permitted stay was 60 days."),
    ("entry_requirements", "The 60-day visa-exempt period has been superseded."),
])
def test_independent_numbers_and_explicit_historical_stays_do_not_trigger_a_blanket_hold(field, value):
    g, prov = apply(field, value)
    assert "scheduled_policy_conflict" not in g
    if field != "exceptions":
        assert g[field] == value
        assert prov["field_provenance"][field] == ORIGINAL_SOURCE
    else:
        for item in value:
            assert item in g[field]
    assert g["permitted_stay_days"] == 30


def test_before_effective_date_preserves_current_60_day_onward_condition_without_hold():
    text = "Return or onward ticket within the 60-day visa-exempt period."
    g, prov = apply("onward_travel_evidence", text, arrival="2026-09-14")
    assert g["permitted_stay_days"] == 60
    assert g["onward_travel_evidence"] == text
    assert "scheduled_policy_conflict" not in g
    assert "scheduled_policy" not in g
    assert g["upcoming_policy"]["effective_from"] == "2026-09-15"
    assert prov["field_provenance"]["onward_travel_evidence"] == ORIGINAL_SOURCE


def test_exception_cleanup_preserves_obligations_and_independent_60_day_facts():
    exceptions = ["Singapore passport holders may stay 60 days. Proof of funds is required.",
                  "Apply for an extension before the 60-day limit, entry remains subject to inspection.",
                  "An independent health certificate is valid for 60 days.",
                  "Extension of 60 days is a separate application."]
    g, prov = apply("exceptions", exceptions)
    assert "Singapore passport holders may stay 60 days." not in g["exceptions"]
    assert "Proof of funds is required." in g["exceptions"]
    assert "Apply for an extension before the 60-day limit" in g["exceptions"]
    assert "entry remains subject to inspection." in g["exceptions"]
    assert exceptions[2] in g["exceptions"] and exceptions[3] in g["exceptions"]
    assert g["scheduled_policy_conflict"]["fields"] == ["exceptions"]
    # The schedule's combined exception list must retain the earlier evidence
    # for the independent clauses, rather than crediting the stay notice.
    assert prov["field_provenance"]["exceptions"]["prior_evidence"] == ORIGINAL_SOURCE


def test_repeated_schedule_application_preserves_the_same_conflict_and_inputs():
    g, p = apply("onward_travel_evidence", "Onward ticket within 60 days of arrival.")
    original = deepcopy((g, p))
    again, again_p = sp.apply(g, p, ROUTE)
    assert (g, p) == original
    assert again["scheduled_policy_conflict"] == g["scheduled_policy_conflict"]
    assert again["onward_travel_evidence"] == g["onward_travel_evidence"]
    assert again_p["field_provenance"]["onward_travel_evidence"] == ORIGINAL_SOURCE


@pytest.mark.parametrize("spelling", ["Macao", "Macau"])
def test_reviewed_macao_identity_can_join_the_existing_seventeen_schedules_without_weakening_scope(spelling):
    existing = [r for r in json.loads(sp.POLICIES.read_text()) if r["route"]["nationality"] != "MAC"]
    maca = deepcopy(next(row for row in existing if row["route"]["nationality"] == "HKG"))
    maca["id"] = "THA-2026-09-15-MAC-ordinary-tourism"
    maca["route"]["nationality"] = "MAC"
    maca["evidence"]["text"] = maca["evidence"]["text"].replace("Hong Kong", spelling)
    maca["evidence"]["quotes"]["nationality"] = spelling
    parsed = sp._parse_rows(existing + [maca])
    assert len(parsed) == 18
    assert {r["route"]["nationality"] for r in parsed} == {r["route"]["nationality"] for r in existing} | {"MAC"}
    maca["route"]["nationality"] = "HKG"
    with pytest.raises(ValueError, match="nationality quote"):
        sp._parse_rows([maca])


def test_exception_composite_provenance_does_not_grow_or_recredit_on_repeated_reads():
    first, first_prov = apply("exceptions", ["Apply for an extension before the 60-day limit."])
    second, second_prov = sp.apply(first, first_prov, ROUTE)
    assert second == first
    assert second_prov == first_prov
    proof = second_prov["field_provenance"]["exceptions"]
    assert proof["prior_evidence"] == ORIGINAL_SOURCE
    assert proof["scope"] == "scheduled_exemption_additions_only"


def test_scheduled_dependency_enters_the_shared_public_hold_gate_even_after_operator_release():
    from app.visa_snapshot.records_guard import apply_records_hold, held_envelope
    text = "Onward ticket within the 60-day visa-exempt period."
    for arrival, must_hold in [("2026-09-14", False), ("2026-09-15", True)]:
        guidance, proof = apply("onward_travel_evidence", text, arrival=arrival)
        out = apply_records_hold({**ROUTE, "arrival_date": arrival}, {
            "guidance": guidance, "source_verified": proof,
            "operator_released": True, "held": False})
        assert out["held"] is must_hold
        if must_hold:
            public = held_envelope(out)
            assert public["guidance"] is None
            assert text not in json.dumps(public)
