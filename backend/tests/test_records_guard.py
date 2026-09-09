"""Held records reveal status and route identity, never indirect claims."""
import json
import pytest

from app.visa_snapshot.records_guard import held_envelope


@pytest.mark.parametrize("bad", [
    None, "checked", {"consistent": True},
    {"consistent": True, "outcome": "checked", "evidence_contract": 1,
     "verified_fields": ["disposition"]},
    {"consistent": True, "outcome": "fetch_failed", "evidence_contract": 2,
     "verified_fields": ["disposition"]},
    {"consistent": True, "outcome": "checked", "evidence_contract": 2,
     "verified_fields": ["government_fee"]},
    {"consistent": True, "outcome": "checked", "evidence_contract": 2,
     "verified_fields": "disposition"},
    {"consistent": "false", "outcome": "checked", "evidence_contract": 2,
     "verified_fields": ["disposition"]},
])
def test_reader_rejects_legacy_malformed_or_ancillary_grounding_markers(bad):
    from app.visa_snapshot.records_guard import grounded_verdict_supported
    assert not grounded_verdict_supported(bad)


def test_reader_accepts_current_effective_verified_verdict_contract():
    from app.visa_snapshot.records_guard import grounded_verdict_supported
    from app.visa_snapshot.freshness import EVIDENCE_CONTRACT
    assert grounded_verdict_supported({"consistent": True, "outcome": "checked",
        "evidence_contract": EVIDENCE_CONTRACT, "verified_fields": ["disposition"]})


def test_visa_on_arrival_continues_to_preparation_and_held_voa_stays_blocked():
    from app.visa_snapshot.intake_flow import continuation_meta
    from app.checklist_intake import NEXT_STAGE_BY_KIND
    answer = {"status": "KIMI_PRIMARY", "held": False,
              "guidance": {"disposition": "VISA_ON_ARRIVAL"}}
    meta = continuation_meta(answer)
    assert not meta["blocked"]
    assert meta["kind"] == "visa_on_arrival_preparation"
    assert NEXT_STAGE_BY_KIND[meta["kind"]] == "entry_preparation"
    assert continuation_meta(dict(answer, held=True))["blocked"]


def test_held_envelope_drops_provenance_explanations_and_future_claim_fields():
    withdrawn = "Withdrawn claim: visa-free for 90 days and 999 USD"
    raw = {
        "status": "primary", "cached": True, "stale": True, "held": True,
        "review_required": True, "cache_key": "ISL|ISL|FSM|tourism|default|unknown|v6",
        "route": {"passport_nationality": "ISL", "destination_country": "FSM"},
        "operator_released": False, "detail_pending": False,
        "transit_countries": [], "model": "engine", "elapsed_seconds": 0.1,
        "approximate": False, "approximate_reason": "",
        "guidance": {"exceptions": [withdrawn]},
        "source_verified": {"note": withdrawn, "field_provenance": {
            "disposition": {"note": withdrawn}}},
        "grounded_check": {"note": withdrawn},
        "workflow_plan": [withdrawn], "advisories": [withdrawn],
        "missing_fields": [withdrawn], "contradictions": [withdrawn],
        "apply_steps": [withdrawn], "reply": withdrawn,
        "future_claim_container": {"description": withdrawn},
    }
    safe = held_envelope(raw)
    assert withdrawn not in json.dumps(safe)
    assert safe["guidance"] is None
    assert safe["held"] and safe["review_required"]
    assert safe["route"] == raw["route"]
    assert safe["cache_key"] == raw["cache_key"]
    assert safe["elapsed_seconds"] == 0.1
    # Reading must never erase the cached record's evidence.
    assert raw["guidance"]["exceptions"] == [withdrawn]
    assert raw["source_verified"]["note"] == withdrawn
