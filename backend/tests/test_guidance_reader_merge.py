"""All readers merge original cache facts before applying shared cleanup."""
import copy
import json
from datetime import date

import pytest

from app.visa_snapshot import kimi_primary as kp, verified_overrides as vo
from app.visa_snapshot.records_guard import apply_records_hold

ROUTE = {"passport_nationality": "ISL", "destination_country": "NRU",
         "travel_purpose": "tourism", "travel_document_type": "ordinary_passport"}
URL = "https://www.mofa.go.jp/visa/"
RAW = {"disposition": "VISA_EXEMPT", "requirement_detail": "unconditional_visa_free",
       "government_fee": {"amount": 0, "currency": None}, "application_channel": "not_required",
       "processing_time": "Not applicable (no visa)", "route_workflow_type": "visa_exempt_preparation",
       "visa_category": "No visa needed", "visa_products": [], "source_url": URL,
       "permitted_stay": "30 days", "required_documents": ["Passport"], "confidence": "high"}
CORRECTION = {"disposition": "VISA_REQUIRED", "requirement_detail": "paper_visa"}


def hit(fields):
    return {"route": {"nationality": "ISL", "destination": "NRU", "purpose": "tourism"},
            "fields": fields, "source_url": URL, "verified_at": date.today().isoformat(),
            "verified_by": "Fixture reader", "verifier": "ai",
            "note": "Fixture official text explicitly establishes the ordinary passport tourism verdict and listed fields."}


@pytest.fixture
def isolated(tmp_path, monkeypatch, db):
    path = tmp_path / "overrides.json"
    path.write_text("[]")
    monkeypatch.setattr(vo, "OVERRIDES", path)
    monkeypatch.setenv("ELLIS_OPERATOR_OVERRIDES", str(tmp_path / "operator.json"))
    monkeypatch.setattr(kp, "_official_portals", lambda: {})
    monkeypatch.setattr(kp, "is_available", lambda: True)
    vo.reload()
    yield path
    from app.visa_snapshot.models import KimiRouteGuidanceCache
    db.query(KimiRouteGuidanceCache).filter_by(model="reader-merge-fixture").delete()
    db.commit()
    vo.reload()


@pytest.mark.parametrize("fields", [None, CORRECTION,
    {"disposition": "VISA_EXEMPT", "government_fee": {"amount": 0, "currency": None}},
    {"disposition": "VISA_EXEMPT", "visa_products": [
        {"type": "Tourist visa", "fee": {"amount": 25, "currency": "USD"}}]}])
def test_console_and_customer_use_identical_merge_and_hold(monkeypatch, fields):
    monkeypatch.setattr(vo, "find", lambda route: hit(fields) if fields is not None else None)
    original = copy.deepcopy(RAW)
    merged, provenance = vo.apply(original, ROUTE)
    customer = kp.apply_verified_overrides(kp._result(
        kp.STATUS_PRIMARY, original, cached=True, stale=False), ROUTE)
    assert customer["guidance"] == merged
    console = apply_records_hold(ROUTE, {"guidance": merged, "source_verified": provenance})
    customer = apply_records_hold(ROUTE, customer)
    assert bool(console.get("held")) == bool(customer.get("held"))
    assert original == RAW
    if fields == CORRECTION:
        for key in ("government_fee", "application_channel", "processing_time", "route_workflow_type",
                    "visa_category", "appointment_required", "interview_required"):
            assert key not in merged
    if fields and fields.get("visa_products"):
        assert customer["held"] and merged["visa_products"][0]["fee"]["amount"] == 25


def test_raw_priced_exemption_is_not_cleaned_into_a_safe_answer(monkeypatch):
    monkeypatch.setattr(vo, "find", lambda route: None)
    raw = dict(RAW, government_fee={"amount": 25, "currency": "USD"},
               visa_products=[{"type": "Tourist e-Visa", "fee": {"amount": 25, "currency": "USD"}}])
    merged, _ = vo.apply(raw, ROUTE)
    result = apply_records_hold(ROUTE, kp.apply_verified_overrides(kp._result(
        kp.STATUS_PRIMARY, raw, cached=True, stale=False), ROUTE))
    assert result["guidance"] == merged and result["held"]
    assert merged["government_fee"]["amount"] == 25
    assert merged["visa_products"][0]["fee"]["amount"] == 25


@pytest.mark.parametrize("shadow", [False, True])
def test_lookup_and_records_share_sourced_correction_without_inventing_fee_or_method(
        client, db, isolated, shadow):
    from app.visa_snapshot.models import KimiRouteGuidanceCache
    isolated.write_text(json.dumps([hit(CORRECTION)]))
    vo.reload()
    key = kp.cache_key(ROUTE)
    db.query(KimiRouteGuidanceCache).filter_by(cache_key=key).delete()
    planted = dict(RAW, disposition="VISA_REQUIRED", government_fee={"amount": 999, "currency": "USD"},
                   application_channel="online_portal")
    row = KimiRouteGuidanceCache(cache_key=key, route=ROUTE, guidance=planted if shadow else RAW,
        status=kp.STATUS_PRIMARY, model="reader-merge-fixture",
        verification={"drill_shadow": RAW} if shadow else {})
    db.add(row)
    db.commit()
    headers = {"Authorization": "Bearer dev-token", "X-Org-Id": "merge-test", "X-User-Id": "reader"}
    response = client.post("/database/lookup", headers=headers,
                           json={"nationality": "ISL", "destination": "NRU"})
    assert response.status_code == 200, response.text
    customer = response.json()
    records = client.get("/database/records", headers=dict(headers, Authorization="Bearer admin-token"),
                         params={"nationality": "ISL", "destination": "NRU",
                                 "purpose": "tourism", "document": "ordinary_passport"}).json()["records"]
    assert records and all(record["held"] == customer["held"] for record in records)
    assert not customer["held"]
    assert customer["guidance"]["disposition"] == "VISA_REQUIRED"
    assert "government_fee" not in customer["guidance"]
    assert "application_channel" not in customer["guidance"]
    assert all(record["visa_fee_amount"] is None and record["application_method"] is None for record in records)
    db.refresh(row)
    assert row.guidance == (planted if shadow else RAW)


def test_shared_pure_merge_retains_unreplaced_products_and_checked_fields():
    product = {"type": "Alternative long stay visa", "fee": {"amount": 40, "currency": "USD"}}
    fields = dict(CORRECTION, government_fee={"amount": 0, "currency": "USD"},
                  application_channel="embassy")
    merged, effective = vo.merge_verified_fields(dict(RAW, visa_products=[product]), fields)
    assert merged["visa_products"] == [product]
    assert merged["government_fee"] == fields["government_fee"]
    assert merged["application_channel"] == "embassy"
    assert effective == fields


@pytest.mark.parametrize("disputed", [False, True])
def test_lossless_legacy_lists_preserve_real_disagreements_and_reader_hold_parity(
        client, db, isolated, disputed):
    from app.visa_snapshot.models import KimiRouteGuidanceCache
    from app.visa_snapshot.freshness import EVIDENCE_CONTRACT
    raw = dict(RAW, required_documents="Passport")
    disagreement = "The stored source readings disagree on nationality eligibility"
    marker = {"outcome": "checked", "consistent": True, "evidence_contract": EVIDENCE_CONTRACT,
              "verified_fields": ["disposition"], "source_url": URL, "at": date.today().isoformat()}
    key = kp.cache_key(ROUTE)
    db.query(KimiRouteGuidanceCache).filter_by(cache_key=key).delete()
    row = KimiRouteGuidanceCache(cache_key=key, route=ROUTE, guidance=raw,
        contradictions=[disagreement] if disputed else [], status=kp.STATUS_PRIMARY,
        model="reader-merge-fixture", verification={"grounded_check": marker})
    db.add(row)
    db.commit()
    headers = {"Authorization": "Bearer dev-token", "X-Org-Id": "merge-test", "X-User-Id": "reader"}
    lookup = client.post("/database/lookup", headers=headers,
                         json={"nationality": "ISL", "destination": "NRU"})
    assert lookup.status_code == 200, lookup.text
    records = client.get("/database/records", headers=dict(headers, Authorization="Bearer admin-token"),
                         params={"nationality": "ISL", "destination": "NRU",
                                 "purpose": "tourism", "document": "ordinary_passport"}).json()["records"]
    assert lookup.json()["held"] == disputed
    assert records and all(record["held"] == disputed for record in records)
    raw_result = kp.apply_verified_overrides(kp._result(kp.STATUS_PRIMARY, raw,
        cached=True, stale=False, contradictions=row.contradictions), ROUTE)
    assert raw_result["guidance"]["required_documents"] == ["Passport"]
    assert (disagreement in raw_result["contradictions"]) == disputed
