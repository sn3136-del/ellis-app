"""Passport renewal workflow.

Proves: expired / insufficient-validity passports offer renewal and a valid
passport does not (unless the applicant explicitly asks); the renewal case is a
real linked case that reuses the OCR-extracted passport data; a completed
renewal (approved NEW passport) updates the travel case's passport fields and
re-evaluates destination validity; creation is idempotent; and no
administrator approval exists anywhere on the path."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import models as core_models
from app.db import SessionLocal, create_all
from app.main import app as fastapi_app
from app.providers.ocr import mrz_check_digit
from app.visa_snapshot import kimi_primary
from app.visa_snapshot.models import CaseRouteGuidance, KimiRouteGuidanceCache

H = {"Authorization": "Bearer dev-token", "X-Org-Id": "org-renew", "X-User-Id": "ur"}

ROUTE_GUIDANCE = {
    "disposition": "VISA_EXEMPT", "visa_category": "Visa-free entry (tourism)",
    "permitted_stay": "90 days", "permitted_stay_days": 90,
    "passport_validity": "6 months beyond entry",
    "passport_validity_requirement": {"kind": "months_after_arrival", "months": 6},
    "required_documents": ["passport", "onward or return ticket"],
    "forms": [], "application_channel": "not_required",
    "official_portal_url": None,
    "government_fee": {"amount": None, "currency": None},
    "processing_time": "none (exempt)", "appointment_required": False,
    "health_requirements": [], "route_workflow_type": "visa_exempt_preparation",
    "uncertainty": [], "confidence": "high",
}

RENEWAL_GUIDANCE = {
    "eligible_for_renewal": True, "channel": "online_or_mail",
    "renewal_form": "DS-82", "required_documents": [
        "current passport", "passport photo", "name change document if applicable"],
    "photo_requirements": "2x2 inch, white background, taken within 6 months",
    "old_passport_surrender": "mail the current passport with the application",
    "name_change_evidence": None,
    "government_fee": {"amount": 130, "currency": "USD"},
    "processing_time_normal": "6-8 weeks",
    "processing_time_expedited": "2-3 weeks (extra fee)",
    "appointment_required": False,
    "submission_method": "online (renewal portal) or mail",
    "delivery_method": "mail", "official_portal_url": None,
    "in_person_locations": [], "uncertainty": [], "confidence": "high",
}


def provider(system, user):
    if "passport-renewal" in system:
        return dict(RENEWAL_GUIDANCE)
    if "verifier" in system:
        return {"verdict": "ACCEPT", "issues": [], "corrected": None}
    return dict(ROUTE_GUIDANCE)


def _mrz_text(passport_no="X1234567", expiry="261001"):
    pn = passport_no.ljust(9, "<")
    l1 = "P<USADOE<<JANE".ljust(44, "<")[:44]
    l2 = (pn + mrz_check_digit(pn) + "USA" + "900115"
          + mrz_check_digit("900115") + "F" + expiry + mrz_check_digit(expiry)
          + "<" * 14 + mrz_check_digit("<" * 14) + "0")
    return f"PASSPORT\nUnited States of America\n{l1}\n{l2}\n"


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
    kimi_primary.set_provider(provider)
    yield
    kimi_primary.set_provider(None)


def _make_case(client, *, expiry_iso="2026-10-01", email="r@example.com",
               arrival="2026-08-10", departure="2026-08-20"):
    """Travel case via the real intake continuation, with a confirmed passport
    expiry in the answers (as the applicant confirmation produces)."""
    answers = {
        "address_line1": "12 Harbor Lane", "address_city": "Springfield",
        "address_country": "USA",
        "passport_nationality": "USA", "passport_issuing_country": "USA",
        "travel_document_type": "ordinary_passport",
        "lawful_country_of_residence": "USA", "destination_country": "SGP",
        "visa_category": "tourist_visa", "travel_purpose": "tourism",
        "arrival_date": arrival, "departure_date": departure,
        "age": 36, "preferred_language": "en", "email": email,
        "passport_number": "X1234567", "birth_date": "1990-01-15",
        "passport_expiry_date": expiry_iso,
    }
    r = client.post("/intake", json={"answers": answers}, headers=H)
    iid = r.json()["id"]
    mrz_expiry = expiry_iso[2:4] + expiry_iso[5:7] + expiry_iso[8:10]
    client.post(f"/intake/{iid}/passport",
                json={"name": "passport.pdf", "text": _mrz_text(expiry=mrz_expiry)},
                headers=H)
    client.post(f"/intake/{iid}/guidance", headers=H)
    rr = client.post(f"/intake/{iid}/continue", headers=H)
    assert rr.status_code == 200
    return rr.json()["case_id"]


# ---- trigger ----------------------------------------------------------------
def test_insufficient_validity_offers_renewal(client):
    # Trip arrives 2026-08-10; SGP needs +6 months => 2027-02-10; passport
    # expires 2026-10-01 -> insufficient (from the Kimi structured requirement).
    case_id = _make_case(client, expiry_iso="2026-10-01")
    v = client.get(f"/cases/{case_id}/passport-validity", headers=H).json()
    assert v["status"] == "insufficient_validity"
    assert v["rule_source"] == "kimi_two_pass_guidance"
    assert v["renewal_offered"] is True
    assert v["required_valid_until"] == "2027-02-10"


def test_valid_passport_never_offers_renewal(client):
    case_id = _make_case(client, expiry_iso="2033-01-01", email="ok@example.com")
    v = client.get(f"/cases/{case_id}/passport-validity", headers=H).json()
    assert v["status"] == "ok"
    assert v["renewal_offered"] is False
    # Automatic creation is refused for a valid passport…
    r = client.post(f"/cases/{case_id}/renewal", headers=H, json={"manual": False})
    assert r.status_code == 409
    # …but the applicant's explicit "Renew my passport" still works.
    r2 = client.post(f"/cases/{case_id}/renewal", headers=H, json={"manual": True})
    assert r2.status_code == 200 and r2.json()["renewal_case_id"]


# ---- the renewal case --------------------------------------------------------
def test_renewal_case_is_real_linked_and_prefilled(client, db):
    case_id = _make_case(client, expiry_iso="2026-10-01", email="r2@example.com")
    r = client.post(f"/cases/{case_id}/renewal", headers=H, json={})
    assert r.status_code == 200
    body = r.json()
    rid = body["renewal_case_id"]
    assert body["already_exists"] is False

    renewal_case = db.get(core_models.VisaApplication, rid)
    assert renewal_case.visa_type == "passport_renewal"
    assert renewal_case.answers["linked_travel_case_id"] == case_id
    assert renewal_case.answers["issuing_country"] == "USA"
    assert renewal_case.answers["current_passport_number"] == "X1234567"

    # Kimi renewal analysis saved with the case; checklist derived from it.
    cg = db.execute(select(CaseRouteGuidance).where(
        CaseRouteGuidance.case_id == rid)).scalars().first()
    assert cg.continuation_kind == "passport_renewal"
    g = cg.guidance["guidance"]
    assert g["renewal_form"] == "DS-82" and g["channel"] == "online_or_mail"
    labels = " | ".join(i["label"] for i in cg.checklist)
    assert "DS-82" in labels and "photo" in labels.lower()
    assert any(i["id"] == "old_passport_surrender" for i in cg.checklist)

    # The travel case's OCR'd passport document was reused — no re-upload.
    docs = db.execute(select(core_models.StoredDocument).where(
        core_models.StoredDocument.application_id == rid)).scalars().all()
    passports = [d for d in docs if d.doc_type == "passport"]
    assert len(passports) == 1
    assert passports[0].extracted_fields["passport_number"]["value"] == "X1234567"

    # The travel case links back.
    travel = db.get(core_models.VisaApplication, case_id)
    assert travel.answers["renewal_case_id"] == rid

    # Idempotent: a second request returns the SAME renewal case.
    r2 = client.post(f"/cases/{case_id}/renewal", headers=H, json={})
    assert r2.json()["renewal_case_id"] == rid
    assert r2.json()["already_exists"] is True


def test_expired_passport_offers_renewal_with_official_authority(client):
    case_id = _make_case(client, expiry_iso="2026-01-01", email="exp@example.com")
    v = client.get(f"/cases/{case_id}/passport-validity", headers=H).json()
    assert v["status"] == "expired" and v["renewal_offered"] is True
    r = client.post(f"/cases/{case_id}/renewal", headers=H, json={})
    assert r.status_code == 200
    auth = r.json()["guidance"]["authority"]
    assert auth["authority"].startswith("U.S. Department of State")
    assert auth["url"].startswith("https://travel.state.gov")


# ---- completion: renewed passport resumes the travel case --------------------
def test_completed_renewal_updates_and_resumes_the_travel_case(client, db):
    case_id = _make_case(client, expiry_iso="2026-10-01", email="r3@example.com")
    rid = client.post(f"/cases/{case_id}/renewal", headers=H, json={}).json()["renewal_case_id"]

    # The applicant received the NEW passport and uploads it to the renewal case.
    up = client.post(f"/cases/{rid}/documents", headers=H, json={
        "name": "new-passport.pdf", "mime": "application/pdf", "size_bytes": 2048,
        "text": _mrz_text(passport_no="Z7654321", expiry="360801")})
    assert up.json()["doc_type"] == "passport" and up.json()["mrz_valid"] is True
    doc_id = up.json()["id"]

    # Applicant approval (their confirmation — still required, never automatic).
    ap = client.post(f"/cases/{rid}/documents/{doc_id}/approve", headers=H)
    assert ap.status_code == 200
    body = ap.json()

    # Propagation: the travel case's passport fields updated + validity re-ran.
    tv = body["travel_case_validity"]
    assert tv is not None and tv["status"] == "ok"
    travel = db.get(core_models.VisaApplication, case_id)
    assert travel.answers["passport_number"] == "Z7654321"
    assert travel.answers["expiry_date"] == "2036-08-01"
    v = client.get(f"/cases/{case_id}/passport-validity", headers=H).json()
    assert v["status"] == "ok" and v["renewal_offered"] is False


def test_approving_the_old_passport_propagates_nothing(client, db):
    case_id = _make_case(client, expiry_iso="2026-10-01", email="r4@example.com")
    rid = client.post(f"/cases/{case_id}/renewal", headers=H, json={}).json()["renewal_case_id"]
    docs = db.execute(select(core_models.StoredDocument).where(
        core_models.StoredDocument.application_id == rid)).scalars().all()
    old_doc = [d for d in docs if d.doc_type == "passport"][0]
    ap = client.post(f"/cases/{rid}/documents/{old_doc.id}/approve", headers=H)
    assert ap.json()["travel_case_validity"] is None   # same passport — no update
    travel = db.get(core_models.VisaApplication, case_id)
    assert travel.answers["passport_number"] == "X1234567"


def test_no_admin_approval_on_the_renewal_path(client, db):
    from app.visa_snapshot.models import HumanReviewTask
    open_before = db.query(HumanReviewTask).filter_by(status="open").count()
    case_id = _make_case(client, expiry_iso="2026-10-01", email="r5@example.com")
    r = client.post(f"/cases/{case_id}/renewal", headers=H, json={})
    assert r.status_code == 200
    assert db.query(HumanReviewTask).filter_by(status="open").count() == open_before
