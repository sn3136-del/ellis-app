"""The complete applicant journey: passport upload at intake, deterministic
profile extraction (MRZ authoritative, Kimi can never invent identity data),
Kimi-guidance continuation into a case per disposition, route checklist,
duplicate-safety, refresh-resume, and the non-blocking official-source audit.
No administrator approval exists anywhere on this path."""
import base64

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal, create_all
from app.main import app as fastapi_app
from app.providers.ocr import mrz_check_digit
from app.visa_snapshot import intake_flow, kimi_primary
from app.visa_snapshot.models import (CaseRouteGuidance, KimiRouteGuidanceCache,
                                      OnDemandRouteResearchJob,
                                      RouteIntakeDocument)
from app import models as core_models

H = {"Authorization": "Bearer dev-token", "X-Org-Id": "org-journey", "X-User-Id": "uj"}


# ---- deterministic TD3 specimen (valid check digits, future expiry) ----------
def _mrz_lines(*, passport_no="X1234567", nat="USA", issuing="USA",
               birth="900115", sex="M", expiry="330101",
               surname="DOE", given="JOHN"):
    pn = passport_no.ljust(9, "<")
    name = f"{surname}<<{given}".replace(" ", "<")
    l1 = f"P<{issuing}{name}".ljust(44, "<")[:44]
    l2 = (pn + mrz_check_digit(pn) + nat + birth + mrz_check_digit(birth)
          + sex + expiry + mrz_check_digit(expiry) + "<" * 14
          + mrz_check_digit("<" * 14) + "0")
    return l1, l2


def _passport_text(**kw):
    l1, l2 = _mrz_lines(**kw)
    return f"PASSPORT\nUnited States of America\n{l1}\n{l2}\n"


ANSWERS_SGP = {
    "passport_nationality": "USA", "passport_issuing_country": "USA",
    "travel_document_type": "ordinary_passport",
    "lawful_country_of_residence": "USA", "destination_country": "SGP",
    "visa_category": "tourist_visa", "travel_purpose": "tourism",
    "arrival_date": "2026-07-26", "departure_date": "2026-08-26",
    "age": 36, "preferred_language": "en", "email": "us@example.com",
}

EXEMPT_ANSWER = {
    "disposition": "VISA_EXEMPT", "visa_category": "Visa-free entry (tourism)",
    "permitted_stay": "90 days", "passport_validity": "6 months beyond entry",
    "required_documents": ["passport", "onward or return ticket",
                           "proof of accommodation"],
    "forms": ["SG Arrival Card"], "application_channel": "not_required",
    "official_portal_url": None,
    "government_fee": {"amount": None, "currency": None},
    "processing_time": "none (exempt)", "biometrics_required": False,
    "interview_required": False, "appointment_required": False,
    "account_registration_steps": [], "payment_process": [],
    "submission_process": [], "exceptions": [], "uncertainty": [],
    "confidence": "high",
}

REQUIRED_ANSWER = dict(
    EXEMPT_ANSWER, disposition="VISA_REQUIRED", visa_category="Tourist L visa",
    application_channel="embassy",
    government_fee={"amount": 140, "currency": "USD"},
    official_portal_url="https://cova.mfa.gov.cn/",
    forms=["Visa application form V.2013"], appointment_required=True,
    required_documents=["passport", "passport photo", "flight itinerary",
                        "hotel booking", "bank statement"],
    processing_time="4 business days")

ETA_ANSWER = dict(
    EXEMPT_ANSWER, disposition="ELECTRONIC_AUTHORIZATION_REQUIRED",
    visa_category="Electronic travel authorization",
    application_channel="online_portal",
    government_fee={"amount": 10, "currency": "GBP"},
    official_portal_url=None, forms=[], processing_time="3 business days")


@pytest.fixture()
def db():
    create_all()
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture()
def client():
    return TestClient(fastapi_app)


@pytest.fixture(autouse=True)
def _reset(db):
    for row in db.execute(select(KimiRouteGuidanceCache)).scalars().all():
        db.delete(row)
    db.commit()
    yield
    kimi_primary.set_provider(None)


def _new_intake(client, answers=ANSWERS_SGP):
    r = client.post("/intake", json={"answers": dict(answers),
                                     "email": answers.get("email", "")}, headers=H)
    assert r.status_code == 200
    return r.json()["id"]


# =========================================================================
# Part 1 — passport upload at intake
# =========================================================================
def test_passport_upload_prefills_all_passport_fields(client):
    iid = _new_intake(client)
    r = client.post(f"/intake/{iid}/passport",
                    json={"name": "passport.pdf", "text": _passport_text()}, headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] is True and body["rejected"] is False
    assert body["mrz_valid"] is True
    pre = body["prefill"]
    assert pre["passport_nationality"] == "USA"
    assert pre["passport_issuing_country"] == "USA"
    assert pre["passport_number"] == "X1234567"
    assert pre["birth_date"] == "1990-01-15"
    assert pre["passport_expiry_date"] == "2033-01-01"
    assert pre["surname"] == "DOE" and pre["given_names"] == "JOHN"
    assert pre["full_name"] == "JOHN DOE"
    # travel_document_type is deliberately NOT prefilled: the applicant may
    # have picked a non-ordinary document and the preview doesn't show it.
    assert "travel_document_type" not in pre
    # Every field carries provenance + confidence; MRZ-valid identity fields
    # do not demand re-confirmation.
    fields = body["profile"]["fields"]
    for key in ("surname", "passport_number", "birth_date", "expiry_date"):
        assert fields[key]["source"] == "mrz"
        assert fields[key]["needs_confirmation"] is False
        assert 0 < fields[key]["confidence"] <= 1


def test_age_is_derived_from_birth_date_not_guessed(client):
    from datetime import date
    iid = _new_intake(client)
    r = client.post(f"/intake/{iid}/passport",
                    json={"name": "p.pdf", "text": _passport_text()}, headers=H)
    pre = r.json()["prefill"]
    assert pre["age"] == intake_flow.derive_age("1990-01-15", date.today())
    # Deterministic derivation incl. the not-yet-birthday case + leap years.
    assert intake_flow.derive_age("1990-01-15", date(2026, 7, 24)) == 36
    assert intake_flow.derive_age("1990-08-15", date(2026, 7, 24)) == 35
    assert intake_flow.derive_age("2000-02-29", date(2026, 2, 28)) == 25
    assert intake_flow.derive_age("2000-02-29", date(2026, 3, 1)) == 26
    assert intake_flow.derive_age("garbage", date(2026, 1, 1)) is None


def test_mrz_wins_over_conflicting_visual_zone_and_requires_confirmation(client):
    iid = _new_intake(client)
    text = _passport_text() + "\nSurname: DOE\nGiven names: JOHNNY\n"
    r = client.post(f"/intake/{iid}/passport",
                    json={"name": "p.pdf", "text": text}, headers=H)
    body = r.json()
    fields = body["profile"]["fields"]
    # The MRZ value is kept (authoritative) but the disagreement is flagged for
    # the applicant — never silently resolved.
    assert fields["given_names"]["value"] == "JOHN"
    assert fields["given_names"]["needs_confirmation"] is True
    assert any(c["field"] == "given_names" for c in body["conflicts"])


def test_invalid_mrz_checksum_requires_confirmation_everywhere(client):
    iid = _new_intake(client)
    l1, l2 = _mrz_lines()
    broken = l2[:9] + ("9" if l2[9] != "9" else "8") + l2[10:]  # wrong check digit
    r = client.post(f"/intake/{iid}/passport",
                    json={"name": "p.pdf", "text": f"{l1}\n{broken}"}, headers=H)
    body = r.json()
    # An unverifiable MRZ page is never accepted as the identity source.
    assert body["rejected"] is True


def test_visa_sticker_page_is_rejected_with_guidance(client):
    iid = _new_intake(client)
    l1, l2 = _mrz_lines()
    visa_text = "VISA\n" + "V<USA" + l1[5:] + "\n" + l2
    r = client.post(f"/intake/{iid}/passport",
                    json={"name": "visa.jpg", "mime": "image/jpeg",
                          "text": visa_text}, headers=H)
    body = r.json()
    assert body["rejected"] is True and body["accepted"] is False
    assert body["message"]


def test_profile_builder_cannot_invent_fields():
    """No MRZ + junk OCR keys -> missing identity fields STAY missing; only
    whitelisted keys with provenance can exist. (Kimi has no code path into
    the profile builder at all.)"""
    p = intake_flow.build_passport_profile(
        ocr_fields={"favorite_color": {"value": "blue", "confidence": 0.99},
                    "surname": {"value": "DOE", "confidence": 0.5}},
        mrz=None, recognized_text="", mrz_valid=False)
    assert set(p["fields"]).issubset(set(intake_flow.PROFILE_FIELDS) | {"_age"})
    assert "favorite_color" not in p["fields"]
    assert p["fields"]["surname"]["needs_confirmation"] is True
    for key in ("passport_number", "nationality", "birth_date", "expiry_date"):
        assert key in p["missing"]
    assert "age" not in p["prefill"]           # no birth date -> no derived age


def test_duplicate_intake_upload_returns_same_record(client, db):
    iid = _new_intake(client)
    payload = {"name": "p.pdf", "text": _passport_text()}
    r1 = client.post(f"/intake/{iid}/passport", json=payload, headers=H)
    r2 = client.post(f"/intake/{iid}/passport", json=payload, headers=H)
    assert r1.json()["document_id"] == r2.json()["document_id"]
    assert r2.json()["duplicate"] is True
    rows = db.execute(select(RouteIntakeDocument).where(
        RouteIntakeDocument.intake_id == iid)).scalars().all()
    assert len(rows) == 1


def test_profile_survives_refresh(client):
    iid = _new_intake(client)
    client.post(f"/intake/{iid}/passport",
                json={"name": "p.pdf", "text": _passport_text()}, headers=H)
    r = client.get(f"/intake/{iid}/passport", headers=H)
    assert r.json()["prefill"]["passport_number"] == "X1234567"


def test_no_passport_pii_in_audit_log(client, db):
    iid = _new_intake(client)
    client.post(f"/intake/{iid}/passport",
                json={"name": "p.pdf", "text": _passport_text()}, headers=H)
    events = db.execute(select(core_models.AuditEvent).where(
        core_models.AuditEvent.action == "intake_passport_ocr")).scalars().all()
    assert events
    for e in events:
        blob = str(e.detail)
        for pii in ("X1234567", "DOE", "JOHN", "1990", "900115"):
            assert pii not in blob


# =========================================================================
# Part 3 — continuation from guidance into the case workflow
# =========================================================================
def _two_pass(answer):
    """Both Kimi passes: the analysis answer for pass 1, ACCEPT for pass 2."""
    def provider(system, user):
        if "verifier" in system:
            return {"verdict": "ACCEPT", "issues": [], "corrected": None}
        return dict(answer)
    return provider


def _resolve_with_guidance(client, answer, answers=ANSWERS_SGP):
    kimi_primary.set_provider(_two_pass(answer))
    iid = _new_intake(client, answers)
    client.post(f"/intake/{iid}/passport",
                json={"name": "p.pdf", "text": _passport_text()}, headers=H)
    g = client.post(f"/intake/{iid}/guidance", headers=H)
    assert g.status_code == 200
    return iid, g.json()


def test_visa_exempt_guidance_continues_to_entry_preparation(client, db):
    iid, g = _resolve_with_guidance(client, EXEMPT_ANSWER)
    assert g["guidance"]["disposition"] == "VISA_EXEMPT"
    r = client.post(f"/intake/{iid}/continue", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["continuation_kind"] == "entry_preparation"
    assert body["case_id"] and body["case_state"] == "DRAFT"
    assert body["already_converted"] is False
    # A visa-exempt route is NEVER presented as a consular visa application.
    import json as _json
    assert "consular" not in _json.dumps(body).lower()
    # Entry preparation still has real work: checklist with the passport,
    # onward travel, accommodation, and the arrival-card form.
    ids = {i["id"] for i in body["checklist"]}
    assert "passport" in ids and "flight_itinerary" in ids
    assert "hotel_booking" in ids
    assert any(i.startswith("form:") for i in ids)
    # The intake is converted and linked — not restarted.
    ri = client.get(f"/intake/{iid}", headers=H).json()
    assert ri["status"] == "converted" and ri["case_id"] == body["case_id"]


def test_visa_required_guidance_continues_to_visa_document_intake(client):
    iid, g = _resolve_with_guidance(
        client, REQUIRED_ANSWER, dict(ANSWERS_SGP, destination_country="CHN"))
    r = client.post(f"/intake/{iid}/continue", headers=H)
    body = r.json()
    assert body["continuation_kind"] == "visa_application"
    ids = {i["id"] for i in body["checklist"]}
    for expected in ("passport", "photo", "flight_itinerary", "hotel_booking",
                     "bank_statement"):
        assert expected in ids
    # Nothing on this path started a portal action: the case is a DRAFT.
    assert body["case_state"] == "DRAFT"


def test_electronic_authorization_guidance_continues_correctly(client):
    iid, g = _resolve_with_guidance(
        client, ETA_ANSWER, dict(ANSWERS_SGP, destination_country="GBR"))
    r = client.post(f"/intake/{iid}/continue", headers=H)
    assert r.json()["continuation_kind"] == "authorization_application"


def test_uncertain_guidance_without_disposition_shows_precise_blocker(client):
    bad = {k: v for k, v in EXEMPT_ANSWER.items() if k != "disposition"}
    iid, g = _resolve_with_guidance(
        client, bad, dict(ANSWERS_SGP, destination_country="JPN"))
    assert g["status"] == "KIMI_UNCERTAIN"
    r = client.post(f"/intake/{iid}/continue", headers=H)
    assert r.status_code == 409
    assert "disposition" in r.json()["detail"]["blockers"]


def test_uncertain_guidance_with_disposition_continues_with_available(client):
    partial = dict(EXEMPT_ANSWER)
    partial.pop("processing_time")           # a gap that does NOT block safe prep
    iid, g = _resolve_with_guidance(
        client, partial, dict(ANSWERS_SGP, destination_country="KOR"))
    assert g["status"] == "KIMI_UNCERTAIN"
    r = client.post(f"/intake/{iid}/continue", headers=H)
    assert r.status_code == 200
    assert r.json()["continuation_kind"] == "entry_preparation"


def test_continue_is_idempotent_and_never_creates_a_second_case(client, db):
    iid, _ = _resolve_with_guidance(
        client, EXEMPT_ANSWER, dict(ANSWERS_SGP, destination_country="MYS"))
    r1 = client.post(f"/intake/{iid}/continue", headers=H)
    r2 = client.post(f"/intake/{iid}/continue", headers=H)
    assert r1.json()["case_id"] == r2.json()["case_id"]
    assert r2.json()["already_converted"] is True
    cases = db.execute(select(core_models.VisaApplication).where(
        core_models.VisaApplication.org_id == "org-journey")).scalars().all()
    assert len([c for c in cases if c.id == r1.json()["case_id"]]) == 1


def test_passport_data_survives_guidance_and_continuation(client, db):
    iid, _ = _resolve_with_guidance(
        client, EXEMPT_ANSWER, dict(ANSWERS_SGP, destination_country="THA"))
    # Apply the extracted profile to the intake answers (the applicant's
    # confirmation step), then continue.
    profile = client.get(f"/intake/{iid}/passport", headers=H).json()
    client.put(f"/intake/{iid}", json={"answers": profile["prefill"]}, headers=H)
    r = client.post(f"/intake/{iid}/continue", headers=H)
    case_id = r.json()["case_id"]
    case = client.get(f"/cases/{case_id}", headers=H).json()
    ans = case["answers"]
    assert ans["passport_number"] == "X1234567"
    assert ans["birth_date"] == "1990-01-15"
    assert ans["nationality"] == "USA"          # canonical alias for the pipeline
    assert ans["expiry_date"] == "2033-01-01"
    # The uploaded passport document was carried into the case — no re-upload.
    review = client.get(f"/cases/{case_id}/review", headers=H).json()
    passports = [d for d in review["documents"] if d["doc_type"] == "passport"]
    assert len(passports) == 1 and passports[0]["approved"] is True
    assert passports[0]["extracted_fields"]["passport_number"]["value"] == "X1234567"
    # The intake-side blob was moved to the case (single erasable home).
    idoc = db.execute(select(RouteIntakeDocument).where(
        RouteIntakeDocument.intake_id == iid)).scalars().first()
    assert idoc is not None and idoc.content is None


def test_continuation_never_starts_or_links_official_source_research(client, db):
    """The applicant flow performs ZERO official-source research: resolve,
    guidance, and continuation neither create nor link a research job — even
    when an (admin-created) job exists for the same intake. The Kimi two-pass
    verification is reported instead of any audit."""
    from app.visa_snapshot import ondemand
    kimi_primary.set_provider(_two_pass(EXEMPT_ANSWER))
    iid = _new_intake(client, dict(ANSWERS_SGP, destination_country="IDN"))
    before = db.query(OnDemandRouteResearchJob).count()
    rr = client.post(f"/intake/{iid}/resolve", headers=H)
    assert rr.status_code == 200 and "research_job" not in rr.json()
    client.post(f"/intake/{iid}/guidance", headers=H)
    assert db.query(OnDemandRouteResearchJob).count() == before   # none created
    # Even an admin-created job for this intake is NOT adopted by continuation.
    job = ondemand.create_job(
        db, org_id="org-journey", user_id="uj", intake_id=iid, case_id=None,
        answers=dict(ANSWERS_SGP, destination_country="IDN"), normalized={},
        key="rk1|nat=USA|iss=USA|doc=ordinary_passport|res=USA|dest=IDN|cat=tourist_visa|sub=None|pur=tourism|per=2026-07-26|jur=default",
        language="en")
    r = client.post(f"/intake/{iid}/continue", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert "audit" not in body
    assert body["verification"]["verdict"] == "ACCEPT"    # two-pass, not audit
    db.expire_all()
    job2 = db.get(OnDemandRouteResearchJob, job.id)
    assert job2.case_id is None                  # research stays a dev-only tool


def test_no_admin_approval_anywhere_on_the_continuation_path(client, db):
    """Continuation succeeds with zero HumanReviewTask resolution and no admin
    endpoint involvement — the journey introduces no approval gate."""
    from app.visa_snapshot.models import HumanReviewTask
    open_before = {t.id for t in db.execute(select(HumanReviewTask).where(
        HumanReviewTask.status == "open")).scalars().all()}
    iid, _ = _resolve_with_guidance(
        client, EXEMPT_ANSWER, dict(ANSWERS_SGP, destination_country="VNM"))
    r = client.post(f"/intake/{iid}/continue", headers=H)
    assert r.status_code == 200
    # Whatever review tasks exist are audit artifacts — none was resolved and
    # continuation did not wait on them.
    still_open = {t.id for t in db.execute(select(HumanReviewTask).where(
        HumanReviewTask.status == "open")).scalars().all()}
    assert open_before.issubset(still_open)


def test_guidance_saved_to_case_survives_cache_expiry(client, db):
    iid, _ = _resolve_with_guidance(
        client, EXEMPT_ANSWER, dict(ANSWERS_SGP, destination_country="PHL"))
    case_id = client.post(f"/intake/{iid}/continue", headers=H).json()["case_id"]
    # Wipe the guidance cache entirely — the case must still know its route.
    for row in db.execute(select(KimiRouteGuidanceCache)).scalars().all():
        db.delete(row)
    db.commit()
    r = client.get(f"/cases/{case_id}/checklist", headers=H)
    body = r.json()
    assert body["disposition"] == "VISA_EXEMPT"
    assert body["continuation_kind"] == "entry_preparation"
    assert body["guidance"]["guidance"]["permitted_stay"] == "90 days"


# =========================================================================
# Part 4 — document intake against the checklist
# =========================================================================
def test_uploaded_documents_are_classified_and_satisfy_checklist(client):
    """Upload alone NEVER fulfils a requirement; binding + the applicant's
    explicit Submit does."""
    iid, _ = _resolve_with_guidance(
        client, REQUIRED_ANSWER, dict(ANSWERS_SGP, destination_country="IND"))
    case_id = client.post(f"/intake/{iid}/continue", headers=H).json()["case_id"]
    before = client.get(f"/cases/{case_id}/checklist", headers=H).json()
    flight = next(i for i in before["checklist"] if i["id"] == "flight_itinerary")
    assert flight["status"] == "pending"
    # An unbound upload classifies correctly but changes NO checklist status.
    r = client.post(f"/cases/{case_id}/documents",
                    json={"name": "loose.pdf",
                          "text": "Flight itinerary\nAirline: Loose Air\n"
                                  "Departure: SIN 27 JUL\nPNR: ZZZ999"},
                    headers=H)
    assert r.status_code == 200 and r.json()["doc_type"] == "flight_itinerary"
    mid = client.get(f"/cases/{case_id}/checklist", headers=H).json()
    flight = next(i for i in mid["checklist"] if i["id"] == "flight_itinerary")
    assert flight["status"] == "pending"
    assert mid["checklist_counts"]["required_missing"] == \
        before["checklist_counts"]["required_missing"]
    # A checklist-row upload binds to that exact requirement → ready to submit,
    # still NOT fulfilled.
    r = client.post(f"/cases/{case_id}/documents",
                    json={"name": "trip.pdf",
                          "text": "Flight itinerary\nAirline: Example Air\n"
                                  "Departure: SIN 26 JUL\nPNR: ABC123",
                          "checklist_item_id": "flight_itinerary"},
                    headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["doc_type"] == "flight_itinerary"
    assert body["binding"]["match_verdict"] == "match"
    mid = client.get(f"/cases/{case_id}/checklist", headers=H).json()
    flight = next(i for i in mid["checklist"] if i["id"] == "flight_itinerary")
    assert flight["status"] == "ready_to_submit"
    assert mid["checklist_counts"]["required_missing"] == \
        before["checklist_counts"]["required_missing"]
    # The applicant's explicit Submit fulfils the requirement.
    s = client.post(f"/cases/{case_id}/checklist/flight_itinerary/submit",
                    json={"document_id": body["id"]}, headers=H)
    assert s.status_code == 200 and s.json()["submitted"] is True
    after = client.get(f"/cases/{case_id}/checklist", headers=H).json()
    flight = next(i for i in after["checklist"] if i["id"] == "flight_itinerary")
    assert flight["status"] == "submitted"
    assert flight["binding"]["submitted_at"]
    assert after["checklist_counts"]["required_missing"] < \
        before["checklist_counts"]["required_missing"]


def test_duplicate_case_document_upload_creates_no_second_record(client, db):
    iid, _ = _resolve_with_guidance(
        client, EXEMPT_ANSWER, dict(ANSWERS_SGP, destination_country="KHM"))
    case_id = client.post(f"/intake/{iid}/continue", headers=H).json()["case_id"]
    payload = {"name": "hotel.pdf",
               "text": "Hotel booking confirmation\nGuest: J DOE\nCheck-in 26 Jul"}
    r1 = client.post(f"/cases/{case_id}/documents", json=payload, headers=H)
    r2 = client.post(f"/cases/{case_id}/documents", json=payload, headers=H)
    assert r1.json()["id"] == r2.json()["id"]
    assert r2.json()["duplicate"] is True
    docs = db.execute(select(core_models.StoredDocument).where(
        core_models.StoredDocument.application_id == case_id,
        core_models.StoredDocument.name == "hotel.pdf")).scalars().all()
    assert len(docs) == 1


def test_supporting_doc_classifier_is_deterministic():
    from app.providers.doc_classifier import classify_supporting_document
    assert classify_supporting_document("Airline e-ticket itinerary PNR X", "") == "flight_itinerary"
    assert classify_supporting_document("Booking confirmation — check-in 26 Jul", "hotel.pdf") == "hotel_booking"
    assert classify_supporting_document("Account statement closing balance", "") == "bank_statement"
    assert classify_supporting_document("Travel insurance policy number ###", "") == "travel_insurance"
    assert classify_supporting_document("random words", "notes.pdf") == "document"


def test_case_erasure_removes_journey_rows(client, db):
    iid, _ = _resolve_with_guidance(
        client, EXEMPT_ANSWER, dict(ANSWERS_SGP, destination_country="LAO"))
    case_id = client.post(f"/intake/{iid}/continue", headers=H).json()["case_id"]
    assert client.delete(f"/cases/{case_id}", headers=H).status_code == 200
    assert db.execute(select(CaseRouteGuidance).where(
        CaseRouteGuidance.case_id == case_id)).scalars().first() is None
    assert db.execute(select(RouteIntakeDocument).where(
        RouteIntakeDocument.intake_id == iid)).scalars().first() is None


# =========================================================================
# Review-hardening regressions
# =========================================================================
def test_icao_country_codes_map_to_iso3_or_require_selection():
    from datetime import date
    # Germany's ICAO code 'D' maps to DEU deterministically.
    p = intake_flow.build_passport_profile(
        ocr_fields={}, mrz={"surname": "MUELLER", "given_names": "ANNA",
                            "passport_number": "C01X00T47", "nationality": "D",
                            "issuing_country": "D", "birth_date": "900115",
                            "sex": "F", "expiry_date": "330101",
                            "mrz_valid": True},
        recognized_text="", mrz_valid=True, today=date(2026, 7, 24))
    assert p["prefill"]["passport_nationality"] == "DEU"
    assert p["prefill"]["passport_issuing_country"] == "DEU"
    # An unverifiable code never prefills route answers — flagged instead.
    p2 = intake_flow.build_passport_profile(
        ocr_fields={}, mrz={"surname": "X", "given_names": "Y",
                            "passport_number": "A12345678", "nationality": "ZZZ",
                            "issuing_country": "ZZZ", "birth_date": "900115",
                            "sex": "M", "expiry_date": "330101",
                            "mrz_valid": True},
        recognized_text="", mrz_valid=True, today=date(2026, 7, 24))
    assert "passport_nationality" not in p2["prefill"]
    assert p2["fields"]["nationality"]["needs_confirmation"] is True


def test_generic_required_documents_keep_separate_checklist_items():
    cl = intake_flow.derive_document_checklist({
        "disposition": "VISA_REQUIRED",
        "required_documents": ["Police clearance certificate",
                               "Notarized custody letter", "passport photo"]})
    ids = [i["id"] for i in cl]
    generic = [i for i in ids if i.startswith("doc:")]
    assert len(generic) == 2            # both unrecognized documents survive
    assert "photo" in ids


def test_vaccination_labels_never_become_generic_checklist_items():
    """A vaccination mention in required_documents is Kimi noise — health
    evidence appears ONLY through the structured, trigger-evaluated
    health_requirements (see test_vaccination_conditional.py)."""
    cl = intake_flow.derive_document_checklist({
        "disposition": "VISA_EXEMPT",
        "required_documents": ["Yellow fever vaccination certificate (if applicable)",
                               "passport"]})
    assert not any("vaccin" in i["label"].lower() for i in cl)
    assert not any(i["id"].startswith("health:") for i in cl)


def test_stamp_page_detection_vs_supporting_documents():
    from app.providers import passport_classifier as pc
    # A bare ARRIVAL/DEPARTURE stamp (little text) is still rejected.
    r = pc.classify_page(text="ARRIVAL 26 JUL 2026 CHANGI", mrz=None)
    assert r["page_type"] == "stamp_page" and r["reject"] is True
    # A flight itinerary containing 'Departure' is NOT a stamp page.
    r2 = pc.classify_page(text="Flight itinerary\nAirline: Example Air\n"
                               "Departure: SIN 26 JUL\nPNR: ABC123\n"
                               "Passenger name record and booking details follow.",
                          mrz=None)
    assert r2["reject"] is False
    # The SG Arrival Card (mentions the Immigration & Checkpoints Authority)
    # is a supporting form, never a stamp page.
    r3 = pc.classify_page(text="SG Arrival Card\nImmigration & Checkpoints "
                               "Authority\nDeclaration form for arriving "
                               "travellers\nHealth declaration included",
                          mrz=None)
    assert r3["reject"] is False
    # A real stamp page with strong markers still rejects.
    r4 = pc.classify_page(text="IMMIGRATION\nADMITTED\nPORT OF ENTRY: JFK", mrz=None)
    assert r4["page_type"] == "stamp_page"


def test_doc_classifier_argmax_beats_stray_keywords():
    from app.providers.doc_classifier import classify_supporting_document as c
    assert c("Hotel booking confirmation\nGuest name: J DOE\nRoom type: twin\n"
             "Check-out: 27 Jul\nDeparture: 27 Jul") == "hotel_booking"
    assert c("Account statement\nClosing balance: ###\nTransaction history\n"
             "Deposits are FDIC-insured") == "bank_statement"
    assert c("To whom it may concern,\nWe are delighted to invite you — this "
             "invitation covers your stay.") == "invitation_letter"


def test_passport_photo_upload_satisfies_photo_item(client):
    import base64 as b64
    iid, _ = _resolve_with_guidance(
        client, REQUIRED_ANSWER, dict(ANSWERS_SGP, destination_country="BRA"))
    case_id = client.post(f"/intake/{iid}/continue", headers=H).json()["case_id"]
    # A real photograph has bytes but no OCR-able text; the checklist-row
    # context (not the filename) identifies it as the photo requirement.
    r = client.post(f"/cases/{case_id}/documents",
                    json={"name": "IMG_0042.jpg", "mime": "image/jpeg",
                          "size_bytes": 900,
                          "content_b64": b64.b64encode(b"\xff\xd8\xff\xe0 fake").decode(),
                          "checklist_item_id": "photo"},
                    headers=H)
    assert r.status_code == 200
    assert r.json()["rejected"] is False
    assert r.json()["doc_type"] == "photo"
    assert r.json()["binding"]["match_verdict"] == "match"
    mid = client.get(f"/cases/{case_id}/checklist", headers=H).json()
    photo = next(i for i in mid["checklist"] if i["id"] == "photo")
    assert photo["status"] == "ready_to_submit"   # upload alone never fulfils
    s = client.post(f"/cases/{case_id}/checklist/photo/submit",
                    json={"document_id": r.json()["id"]}, headers=H)
    assert s.status_code == 200 and s.json()["submitted"] is True
    after = client.get(f"/cases/{case_id}/checklist", headers=H).json()
    photo = next(i for i in after["checklist"] if i["id"] == "photo")
    assert photo["status"] == "submitted"


def test_unconfirmed_passport_profile_is_not_auto_approved(client):
    """An applicant who uploads a passport but never applies the profile to
    their answers gets the document carried UNAPPROVED — case-stage review
    still stands between OCR and canonical data."""
    iid, _ = _resolve_with_guidance(
        client, EXEMPT_ANSWER, dict(ANSWERS_SGP, destination_country="NZL"))
    case_id = client.post(f"/intake/{iid}/continue", headers=H).json()["case_id"]
    review = client.get(f"/cases/{case_id}/review", headers=H).json()
    passports = [d for d in review["documents"] if d["doc_type"] == "passport"]
    assert len(passports) == 1 and passports[0]["approved"] is False


def test_unvalidated_page_never_accepted_as_intake_passport(client):
    """A page without a checksum-valid biodata MRZ is rejected at intake even
    when OCR labels it 'passport' — the acceptance gate is the classifier's
    accepted_as_passport_identity, never a model hint."""
    iid = _new_intake(client)
    r = client.post(f"/intake/{iid}/passport",
                    json={"name": "blurry.jpg", "mime": "image/jpeg",
                          "text": "Surname Given names Nationality Date of birth "
                                  "passport information page partially readable"},
                    headers=H)
    assert r.json()["rejected"] is True


def test_intake_erasure_removes_documents_and_answers(client, db):
    iid = _new_intake(client)
    client.post(f"/intake/{iid}/passport",
                json={"name": "p.pdf", "text": _passport_text()}, headers=H)
    r = client.delete(f"/intake/{iid}", headers=H)
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert db.execute(select(RouteIntakeDocument).where(
        RouteIntakeDocument.intake_id == iid)).scalars().first() is None
    assert client.get(f"/intake/{iid}", headers=H).status_code == 404
    # A converted intake refuses direct erasure (the case is the erasure home).
    iid2, _ = _resolve_with_guidance(
        client, EXEMPT_ANSWER, dict(ANSWERS_SGP, destination_country="FJI"))
    client.post(f"/intake/{iid2}/continue", headers=H)
    assert client.delete(f"/intake/{iid2}", headers=H).status_code == 409


# =========================================================================
# Continuation metadata mirrors (backend authoritative)
# =========================================================================
def test_continuation_meta_mapping():
    m = intake_flow.continuation_meta
    assert m({"status": "KIMI_PRIMARY", "guidance": {"disposition": "VISA_REQUIRED"}})["kind"] == "visa_application"
    assert m({"status": "KIMI_PRIMARY", "guidance": {"disposition": "VISA_EXEMPT"}})["kind"] == "entry_preparation"
    assert m({"status": "KIMI_PRIMARY",
              "guidance": {"disposition": "ELECTRONIC_AUTHORIZATION_REQUIRED"}})["kind"] == "authorization_application"
    cond = m({"status": "KIMI_PRIMARY", "guidance": {"disposition": "CONDITIONAL"}})
    assert cond["kind"] == "conditional_guidance" and cond["partial"] is True
    blocked = m({"status": "KIMI_UNCERTAIN", "guidance": {},
                 "missing_fields": ["disposition"]})
    assert blocked["blocked"] is True and blocked["blockers"]
    assert m({})["blocked"] is True
