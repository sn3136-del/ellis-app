"""Saved cases cannot revive old claims or act on a newly held route."""
import copy
import json
from datetime import date
from types import SimpleNamespace

import pytest

from app import models, service
from app.visa_snapshot import api, intake_flow, kimi_primary as kp, verified_overrides as vo
from app.visa_snapshot.models import CaseRouteGuidance, RouteIntake, RouteResolution

ORG = "case-evidence"
HEADERS = {"Authorization": "Bearer dev-token", "X-Org-Id": ORG, "X-User-Id": "reader"}
URL = "https://www.ica.gov.sg/enter-transit-depart/entering-singapore/visa_requirements"
ANSWERS = {"passport_nationality": "USA", "passport_issuing_country": "USA",
           "destination_country": "SGP", "lawful_country_of_residence": "USA",
           "travel_document_type": "ordinary_passport", "travel_purpose": "tourism",
           "visa_category": "tourist_visa", "address_line1": "1 Main St",
           "address_city": "New York", "address_country": "USA", "age": 30,
           "arrival_date": "2027-01-10", "departure_date": "2027-01-20",
           "prior_refusals": "no", "email": "fixture@example.com"}
OLD = {"disposition": "VISA_REQUIRED", "requirement_detail": "paper_visa",
       "visa_category": "Legacy consular visa", "source_url": URL, "confidence": "high",
       "permitted_stay": "30 days", "required_documents": ["Obsolete bank statement"],
       "application_channel": "embassy", "government_fee": {"amount": 999, "currency": "USD"},
       "visa_products": []}


@pytest.fixture
def isolated(tmp_path, monkeypatch, db):
    path = tmp_path / "overrides.json"
    path.write_text("[]")
    monkeypatch.setattr(vo, "OVERRIDES", path)
    monkeypatch.setenv("ELLIS_OPERATOR_OVERRIDES", str(tmp_path / "operators.json"))
    monkeypatch.setattr(kp, "is_available", lambda: True)
    # Every case below supplies its own saved evidence; unrelated cases in
    # the session database cannot become a replacement canonical answer.
    monkeypatch.setattr(kp, "_cached", lambda *a: None)
    vo.reload()
    yield path
    from app.visa_snapshot.models import KimiRouteGuidanceCache
    db.query(KimiRouteGuidanceCache).filter_by(model="case-evidence-fixture").delete()
    db.commit()
    vo.reload()


def install_correction(path, *, disposition="VISA_EXEMPT", documents=None):
    fields = {"disposition": disposition,
              "requirement_detail": "unconditional_visa_free" if disposition == "VISA_EXEMPT" else "paper_visa_on_arrival",
              "visa_category": "Visa-free entry" if disposition == "VISA_EXEMPT" else "Visa on arrival",
              "government_fee": {"amount": 0, "currency": "USD"}, "visa_products": [],
              "required_documents": documents or [], "permitted_stay": "30 days",
              "permitted_stay_days": 30, "processing_time": None,
              "application_channel": "not_required" if disposition == "VISA_EXEMPT" else "on_arrival"}
    path.write_text(json.dumps([{"route": {"nationality": "USA", "destination": "SGP",
        "purpose": "tourism", "travel_document_type": "ordinary_passport"},
        "source_url": URL, "verified_at": date.today().isoformat(), "verifier": "ai",
        "verified_by": "Scoped test source reader", "note": "Fixture source explicitly establishes this ordinary US passport tourism rule and its listed requirements.",
        "fields": fields}]))
    vo.reload()


@pytest.fixture
def saved_case(db, isolated):
    applicant = models.Applicant(org_id=ORG, user_id="reader", full_name="Fixture Applicant",
                                 email="fixture@example.com")
    db.add(applicant)
    db.flush()
    case = models.VisaApplication(org_id=ORG, user_id="reader", applicant_id=applicant.id,
                                  destination_country="Singapore", answers=dict(ANSWERS))
    db.add(case)
    db.flush()
    intake = RouteIntake(org_id=ORG, user_id="reader", answers=dict(ANSWERS),
                         case_id=case.id, status="converted")
    db.add(intake)
    db.flush()
    cg = CaseRouteGuidance(org_id=ORG, case_id=case.id, intake_id=intake.id,
        disposition="VISA_REQUIRED", continuation_kind="visa_application",
        guidance={"status": kp.STATUS_PRIMARY, "held": False, "guidance": copy.deepcopy(OLD)},
        checklist=intake_flow.derive_document_checklist(OLD, answers=ANSWERS))
    db.add(cg)
    db.commit()
    return case, intake, cg


def test_held_case_readers_hide_saved_verdict_and_obsolete_checklist(client, saved_case):
    case, intake, _ = saved_case
    for response in (client.post(f"/intake/{intake.id}/continue", headers=HEADERS),
                     client.get(f"/cases/{case.id}/checklist", headers=HEADERS)):
        assert response.status_code == 200
        body = response.json()
        assert body["guidance"]["held"] and body["guidance"]["guidance"] is None
        assert body["disposition"] is None and body["continuation_kind"] is None
        assert body["checklist"] == []
        assert "Obsolete bank statement" not in response.text and "999" not in response.text


def test_new_verified_verdict_refreshes_saved_case_and_preserves_uploaded_documents(
        client, db, saved_case, isolated):
    case, intake, cg = saved_case
    doc = models.StoredDocument(org_id=ORG, application_id=case.id, name="existing-bank.pdf",
        mime="application/pdf", size_bytes=20, sha256="a" * 64, storage_ref="fixture://document")
    db.add(doc)
    db.commit()
    doc_id = doc.id
    install_correction(isolated, documents=["Passport"])
    response = client.post(f"/intake/{intake.id}/continue", headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["disposition"] == "VISA_EXEMPT"
    assert body["continuation_kind"] == "entry_preparation"
    assert "Obsolete bank statement" not in response.text
    db.refresh(cg)
    assert cg.disposition == "VISA_EXEMPT" and cg.continuation_kind == "entry_preparation"
    assert all("obsolete" not in item["label"].lower() for item in cg.checklist)
    assert db.get(models.StoredDocument, doc_id) is not None


@pytest.mark.parametrize("held", [False, True])
def test_resolve_and_reopen_cannot_restore_legacy_pair_disposition(
        client, db, isolated, monkeypatch, held):
    intake = RouteIntake(org_id=ORG, user_id="reader", answers=dict(ANSWERS))
    resolution = RouteResolution(org_id=ORG, raw_input={}, normalized_input={}, route_key="fixture",
        readiness_status="APPLICANT_HANDOFF_READY", snapshot_date="2026-07-23",
        checks={"snapshot_resolution": {"disposition": "EMBASSY_VISA_REQUIRED"},
                "normalization": {"normalized": dict(ANSWERS)}})
    db.add_all([intake, resolution])
    db.commit()
    monkeypatch.setattr(api.resolution_mod, "resolve", lambda *a, **k: {
        "resolution_id": resolution.id, "disposition": "EMBASSY_VISA_REQUIRED",
        "readiness_status": resolution.readiness_status, "checks": resolution.checks})
    monkeypatch.setattr(kp, "_cached", lambda *a: object())
    install_correction(isolated)
    monkeypatch.setattr(kp, "get_route_guidance", lambda *a, **k: {
        "status": kp.STATUS_PRIMARY, "held": held, "guidance": copy.deepcopy(OLD),
        "detail_pending": held})
    response = client.post(f"/intake/{intake.id}/resolve", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["disposition"] == (None if held else "VISA_EXEMPT")
    assert "EMBASSY_VISA_REQUIRED" not in response.text
    reopened = client.get(f"/intake/{intake.id}", headers=HEADERS)
    assert reopened.status_code == 200
    assert "EMBASSY_VISA_REQUIRED" not in reopened.text


def test_held_case_cannot_complete_start_or_generate_an_appointment_packet(client, saved_case):
    case, _, _ = saved_case
    responses = [client.post(f"/cases/{case.id}/checklist/complete", headers=HEADERS),
                 client.post(f"/cases/{case.id}/start", headers=HEADERS),
                 client.get(f"/cases/{case.id}/appointment-packet", headers=HEADERS)]
    for response in responses:
        assert response.status_code == 409, response.text
        assert "Obsolete bank statement" not in response.text and "999" not in response.text


def test_service_transition_cannot_bypass_a_new_hold(db, saved_case):
    from app.visa_snapshot.case_evidence import CaseEvidenceBlocked
    case, _, _ = saved_case
    with pytest.raises(CaseEvidenceBlocked, match="being checked"):
        service.enforce_safety(db, case.id, SimpleNamespace(adapter=None))


def test_corrected_voa_can_complete_preparation_but_cannot_start_filing(
        client, db, saved_case, isolated):
    case, _, cg = saved_case
    install_correction(isolated, disposition="VISA_ON_ARRIVAL")
    from app import checklist_intake
    passport = models.StoredDocument(org_id=ORG, application_id=case.id, name="confirmed-passport.pdf",
        mime="application/pdf", size_bytes=20, sha256="b" * 64, storage_ref="fixture://passport",
        doc_type="passport", page_classification={"accepted_as_passport_identity": True, "classifier": "mrz"})
    db.add(passport)
    db.flush()
    checklist_intake.seed_intake_confirmed_passport(db, org_id=ORG, application_id=case.id, document=passport)
    db.commit()
    complete = client.post(f"/cases/{case.id}/checklist/complete", headers=HEADERS)
    assert complete.status_code == 200, complete.text
    db.refresh(cg)
    assert cg.continuation_kind == "visa_on_arrival_preparation"
    start = client.post(f"/cases/{case.id}/start", headers=HEADERS)
    assert start.status_code == 409
    assert start.json()["detail"]["reason"] == "visa_on_arrival_preparation"
    packet = client.get(f"/cases/{case.id}/appointment-packet", headers=HEADERS)
    assert packet.status_code == 409


def test_gate_does_not_reclassify_cases_without_saved_route_guidance(db, isolated):
    from app.visa_snapshot.case_evidence import ensure_current_case_guidance
    assert ensure_current_case_guidance(db, SimpleNamespace(id="no-route-case"), for_filing=True) is None


@pytest.mark.parametrize("verified_fields,expected_grade", [
    (["government_fee"], "Low"), (["disposition"], "Medium"),
])
def test_lookup_records_and_freshness_share_the_verdict_evidence_contract(
        client, db, isolated, monkeypatch, verified_fields, expected_grade):
    from app.visa_snapshot.models import KimiRouteGuidanceCache
    from app.visa_snapshot.freshness import EVIDENCE_CONTRACT
    route = dict(ANSWERS, passport_nationality="ISL", passport_issuing_country="ISL",
                 lawful_country_of_residence="ISL", destination_country="NRU")
    key = kp.cache_key(route)
    db.query(KimiRouteGuidanceCache).filter_by(cache_key=key).delete()
    guidance = {"disposition": "VISA_EXEMPT", "requirement_detail": "unconditional_visa_free",
                "permitted_stay": "30 days", "source_url": URL, "confidence": "high",
                "visa_products": [], "required_documents": ["Passport"]}
    marker = {"outcome": "checked", "consistent": True,
              "evidence_contract": EVIDENCE_CONTRACT, "verified_fields": verified_fields,
              "source_url": URL, "at": date.today().isoformat()}
    db.add(KimiRouteGuidanceCache(cache_key=key, route=route, guidance=guidance,
        status=kp.STATUS_PRIMARY, model="case-evidence-fixture", verification={"grounded_check": marker}))
    db.commit()
    monkeypatch.setattr(kp, "get_route_guidance", lambda *a, **kw: {
        "status": kp.STATUS_PRIMARY, "held": False, "guidance": guidance,
        "grounded_check": marker})
    lookup = client.post("/database/lookup", headers=HEADERS,
                         json={"nationality": "ISL", "destination": "NRU"}).json()
    admin = dict(HEADERS, Authorization="Bearer admin-token")
    records = client.get("/database/records", headers=admin,
                         params={"nationality": "ISL", "destination": "NRU"}).json()["records"]
    freshness = client.get("/database/freshness", headers=admin).json()
    record = next(r for r in freshness["answers"] if r["cache_key"] == key)
    assert records and {r["confidence_level"] for r in records} == {expected_grade}
    assert bool(lookup["held"]) == (expected_grade == "Low")
    assert record["grounded"] == (expected_grade == "Medium")
    assert {r["source_check"] for r in records} == {
        "reference" if expected_grade == "Low" else "grounded-consistent"}


@pytest.mark.parametrize("verification,held", [
    ({}, True),
    ({"operator_released": {"by": "operator"}}, False),
    ({"operator_released": {"by": "operator"}, "detail_pending": True}, True),
])
def test_each_product_exposes_the_whole_routes_held_status(client, db, isolated, monkeypatch, verification, held):
    from app.visa_snapshot.models import KimiRouteGuidanceCache
    route = dict(ANSWERS)
    guidance = {"disposition": "ELECTRONIC_AUTHORIZATION_REQUIRED",
        "requirement_detail": "eta_electronic_authorization", "source_url": URL,
        "confidence": "high", "permitted_stay": "30 days", "required_documents": ["Passport"],
        "application_channel": "online_portal", "government_fee": {"amount": 7, "currency": "USD"},
        "visa_products": [
            {"type": "Electronic travel authorization", "requirement_detail": "eta_electronic_authorization",
             "fee": {"amount": 7, "currency": "USD"}, "validity": "2 years", "max_stay_days": 30},
            {"type": "Standard Visitor visa", "requirement_detail": "paper_visa",
             "fee": {"amount": 120, "currency": "USD"}, "validity": "6 months", "max_stay_days": 30}]}
    isolated.write_text(json.dumps([{"route": {"nationality": "USA", "destination": "SGP", "purpose": "tourism"},
        "fields": {"disposition": guidance["disposition"], "requirement_detail": guidance["requirement_detail"]},
        "source_url": URL, "verified_at": date.today().isoformat(), "verifier": "ai",
        "note": "Fixture: this ordinary passport tourism route requires electronic authorization."}]))
    vo.reload()
    key = kp.cache_key(route)
    db.query(KimiRouteGuidanceCache).filter_by(cache_key=key).delete()
    db.add(KimiRouteGuidanceCache(cache_key=key, route=route, guidance=guidance,
        status=kp.STATUS_PRIMARY, model="case-evidence-fixture", verification=verification))
    db.commit()
    monkeypatch.setattr(kp, "get_route_guidance", lambda *a, **kw: kp.apply_verified_overrides(
        {"status": kp.STATUS_PRIMARY, "held": False, "guidance": guidance,
         "operator_released": bool(verification.get("operator_released")),
         "detail_pending": bool(verification.get("detail_pending"))}, route))
    admin = dict(HEADERS, Authorization="Bearer admin-token")
    records = client.get("/database/records", headers=admin,
        params={"nationality": "USA", "destination": "SGP"}).json()["records"]
    lookup = client.post("/database/lookup", headers=HEADERS,
                         json={"nationality": "USA", "destination": "SGP"}).json()
    assert {r["confidence_level"] for r in records} == {"Medium", "Low"}
    assert lookup["held"] == held
    assert (lookup["guidance"] is None) == held
    assert all(r["held"] == held and r["review_required"] == held for r in records)
