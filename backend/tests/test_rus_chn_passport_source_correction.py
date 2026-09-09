"""One reviewed route correction does not authorize global enum guessing."""
import json
from pathlib import Path

from app.passport_validity import normalize_passport_validity_rule, passport_validity_rule_errors
from app.visa_snapshot import kimi_primary as kp, verified_overrides as vo


def test_reviewed_russian_ordinary_tourism_rule_is_scoped_and_ancillary(tmp_path, monkeypatch):
    seed = Path(__file__).resolve().parents[2] / "data/database_seed/verified_overrides.json"
    entries = [e for e in json.loads(seed.read_text()) if e.get("route", {}).get("nationality") == "RUS"
               and e["route"].get("destination") == "CHN" and e["route"].get("travel_purpose") == "tourism"]
    assert len(entries) == 1
    isolated = tmp_path / "seed.json"
    isolated.write_text(json.dumps(entries))
    monkeypatch.setattr(vo, "OVERRIDES", isolated)
    monkeypatch.setenv("ELLIS_OPERATOR_OVERRIDES", str(tmp_path / "operator.json"))
    vo.reload()
    try:
        rule = {"kind": "duration_of_intended_stay", "months": None}
        assert normalize_passport_validity_rule(rule) == rule
        assert passport_validity_rule_errors(rule) == ["passport_validity_requirement has an unknown kind"]
        route = {"passport_nationality": "RUS", "destination_country": "CHN",
                 "travel_purpose": "tourism", "travel_document_type": "ordinary_passport"}
        raw = {"disposition": "VISA_EXEMPT", "requirement_detail": "conditional_visa_free",
               "passport_validity_requirement": rule, "passport_validity": "Old unchecked prose"}
        merged, provenance = vo.apply(raw, route)
        assert merged["passport_validity_requirement"] == {"kind": "valid_through_departure", "months": 0}
        assert "throughout the intended stay" in merged["passport_validity"]
        assert not any("passport_validity_requirement" in p for p in kp.serve_time_invariants(merged))
        for field in ("passport_validity", "passport_validity_requirement"):
            proof = provenance["field_provenance"][field]
            assert proof["verifier"] == "ai" and proof["verified_at"] == "2026-09-09"
            assert proof["source_url"] == "https://cs.mfa.gov.cn/gyls/lsgz/fwxx/202511/t20251110_11749824.shtml"
            assert proof["quote"] == "For foreign nationals, an ordinary passport valid for at least the duration of intended stay in China is needed."
        # The side-field review did not refresh or take authorship of the verdict.
        assert provenance["field_provenance"]["disposition"]["verified_at"] == "2026-08-22"
        assert provenance["field_provenance"]["disposition"]["verifier"] == "ai"
        assert raw["passport_validity_requirement"] == rule
        for other in (dict(route, passport_nationality="HKG"), dict(route, travel_purpose="business"),
                      dict(route, travel_document_type="diplomatic_passport")):
            result, proof = vo.apply(raw, other)
            assert result["passport_validity_requirement"] == rule
            assert proof is None
            assert any("passport_validity_requirement" in p for p in kp.serve_time_invariants(result))
    finally:
        vo.reload()
