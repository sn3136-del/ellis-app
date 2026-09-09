from copy import deepcopy

import pytest

from app.visa_snapshot import kimi_primary as kp, permission_eligibility as pe, verified_overrides as vo


def route(nationality="THA", destination="AUS", document="ordinary_passport"):
    return {"passport_nationality": nationality, "destination_country": destination,
            "travel_purpose": "tourism", "travel_document_type": document}


def eta():
    return {"disposition": "ELECTRONIC_AUTHORIZATION_REQUIRED",
            "requirement_detail": "eta_electronic_authorization",
            "visa_category": "Electronic Travel Authority (601)", "confidence": "high",
            "source_url": pe.PROGRAMS[0].source_url, "application_channel": "online_portal",
            "government_fee": {"amount": 20, "currency": "AUD"}}


@pytest.mark.parametrize("nationality", ["THA", "IDN", "VNM", "IND", "PHL"])
def test_internally_consistent_eta_still_held_for_ineligible_passport(monkeypatch, nationality):
    monkeypatch.setattr(vo, "find", lambda _: None)
    g = eta()
    assert kp.serve_time_invariants(g) == []  # A fee/label consistency check cannot catch eligibility.
    original = deepcopy(g)
    out = kp._result(kp.STATUS_PRIMARY, g, cached=True, stale=False, released=True)
    out = kp.apply_verified_overrides(out, route(nationality))
    assert out["held"] and out["review_required"]
    assert any("product eligibility:" in issue for issue in out["contradictions"])
    assert out["apply_steps"] == out["workflow_plan"] == []
    assert g == original


def test_invalid_alternate_is_checked_even_with_eligible_primary():
    g = {"disposition": "VISA_REQUIRED", "visa_category": "Visitor 600",
         "visa_products": [{"type": "Visitor (600)"}, {"type": "ETA (601)"}]}
    assert pe.issues(g, route())
    g["visa_products"].pop()
    # A citation explaining the exclusion is not a recommendation to apply.
    g["source_url"] = pe.PROGRAMS[0].source_url
    assert pe.issues(g, route()) == []


@pytest.mark.parametrize("nationality", ["JPN", "HKG", "GBR", "MYS", "USA"])
def test_list_membership_does_not_add_verification_or_grant_a_visa(nationality):
    g = eta()
    annotated = pe.annotate(g, route(nationality))
    assert annotated == g
    assert "source_verified" not in annotated
    assert pe.issues(g, route(nationality, destination="CAN")) == []


@pytest.mark.parametrize("document", ["official_passport", "diplomatic_passport", "service_passport"])
def test_taiwan_official_document_exclusion(document):
    assert pe.issues(eta(), route("TWN", document=document))
    assert pe.issues(eta(), route("TWN")) == []


def test_recomputation_removes_old_hold_after_product_correction():
    rejected = pe.annotate(eta(), route())
    rejected.update(disposition="VISA_REQUIRED", requirement_detail="evisa", visa_category="Visitor 600")
    assert "_permission_eligibility_issues" not in pe.annotate(rejected, route())


def test_operator_cannot_save_invalid_product_or_change_file(monkeypatch, tmp_path):
    path = tmp_path / "operator.json"
    path.write_text("[]\n")
    monkeypatch.setenv("ELLIS_OPERATOR_OVERRIDES", str(path))
    monkeypatch.setenv("ELLIS_DATA_DIR", str(tmp_path))
    vo.reload()
    entry = {"route": {"nationality": "THA", "destination": "AUS", "travel_purpose": "tourism"},
             "verified_at": "2026-09-09", "verified_by": "AI regression",
             "verifier": "ai", "source_url": pe.PROGRAMS[0].source_url,
             "note": "Source review must not bypass the published eligibility list.", "fields": eta()}
    entry["fields"].pop("confidence")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="product eligibility"):
        vo.append_operator_entry(entry)
    assert path.read_bytes() == before
    vo.reload()


def test_malaysian_russia_correction_exposes_only_supported_products(monkeypatch, tmp_path):
    from pathlib import Path
    from app.visa_snapshot.records_guard import apply_records_hold
    monkeypatch.setattr(vo, "OVERRIDES", Path(__file__).resolve().parents[2] / "data/database_seed/verified_overrides.json")
    monkeypatch.setenv("ELLIS_OPERATOR_OVERRIDES", str(tmp_path / "operator.json"))
    monkeypatch.setenv("ELLIS_DATABASE_HOLD_LOW_CONFIDENCE", "1")
    vo.reload()
    rt = route("MYS", "RUS")
    raw = {"disposition": "VISA_EXEMPT", "requirement_detail": "unconditional_visa_free",
           "visa_category": "No visa needed", "permitted_stay": "30 days", "permitted_stay_days": 30,
           "government_fee": {"amount": 0, "currency": None}, "visa_products": [], "confidence": "high"}
    out = kp.apply_verified_overrides(kp._result(kp.STATUS_PRIMARY, raw, cached=True, stale=False), rt)
    out = apply_records_hold(rt, out)
    assert not out["held"]
    g = out["guidance"]
    assert g["disposition"] == "VISA_REQUIRED" and g["requirement_detail"] == "evisa"
    assert g["permitted_stay_days"] == 30
    assert len(g["visa_products"]) == 1
    product = g["visa_products"][0]
    assert product["entry"] == "single" and "120 days" in product["validity"]
    assert product["source_url"] == "https://evisa.kdmid.ru/"
    assert g["government_fee"] is None  # Unverified amount is never a free visa.
    vo.reload()
