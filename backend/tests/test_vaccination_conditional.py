"""Conditional vaccination/health requirements.

Proves: a direct USA -> Singapore tourist sees NO yellow-fever item and NO
travel-history question; a qualifying recent origin/transit triggers the
question and, once confirmed, the certificate item with its explanation;
irrelevant vaccination items are entirely absent (never 'optional'); the
travel-history question is asked ONLY when a conditional rule needs it."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal, create_all
from app.main import app as fastapi_app
from app.visa_snapshot import intake_flow, kimi_primary
from app.visa_snapshot.models import KimiRouteGuidanceCache

H = {"Authorization": "Bearer dev-token", "X-Org-Id": "org-vax", "X-User-Id": "uv"}

YF = {"name": "Yellow fever vaccination certificate",
      "applicability": "conditional",
      "trigger_countries": ["BRA", "COL", "KEN", "NGA"],
      "trigger": "required only when arriving from, or transiting more than 12 "
                 "hours through, a yellow-fever-risk country",
      "question": "In the last 6 days, were you in a yellow-fever-risk country?"}

SGP_GUIDANCE = {
    "disposition": "VISA_EXEMPT", "visa_category": "Visa-free entry (tourism)",
    "permitted_stay": "90 days", "permitted_stay_days": 90,
    "passport_validity": "6 months beyond entry",
    "required_documents": ["passport", "onward or return ticket",
                           "Yellow fever vaccination certificate (if applicable)"],
    "forms": [], "application_channel": "not_required",
    "official_portal_url": None,
    "government_fee": {"amount": None, "currency": None},
    "processing_time": "none (exempt)", "appointment_required": False,
    "health_requirements": [YF],
    "arrival_card": {"required": True, "name": "SG Arrival Card",
                     "submission_window": "within 3 days before arrival"},
    "route_workflow_type": "visa_exempt_preparation",
    "uncertainty": [], "confidence": "high",
}

USA_SGP = {
    "passport_nationality": "USA", "passport_issuing_country": "USA",
    "travel_document_type": "ordinary_passport",
    "lawful_country_of_residence": "USA", "destination_country": "SGP",
    "visa_category": "tourist_visa", "travel_purpose": "tourism",
    "arrival_date": "2026-08-10", "departure_date": "2026-08-20",
    "age": 30, "preferred_language": "en", "email": "vax@example.com",
}


def _two_pass(answer):
    def provider(system, user):
        if "verifier" in system:
            return {"verdict": "ACCEPT", "issues": [], "corrected": None}
        return dict(answer)
    return provider


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


# ---- pure deterministic logic ------------------------------------------------
def test_direct_us_route_gets_no_yellow_fever_item():
    answers = dict(USA_SGP)                     # no transit, no recent travel
    cl = intake_flow.derive_document_checklist(SGP_GUIDANCE, answers=answers)
    assert not any(i["id"].startswith("health:") for i in cl)
    assert not any("vaccin" in i["label"].lower() or "yellow" in i["label"].lower()
                   for i in cl)


def test_risk_country_transit_triggers_the_certificate_with_explanation():
    answers = dict(USA_SGP, transit_countries=["BRA"])
    cl = intake_flow.derive_document_checklist(SGP_GUIDANCE, answers=answers)
    items = [i for i in cl if i["id"].startswith("health:")]
    assert len(items) == 1
    assert "Yellow fever" in items[0]["label"]
    assert "yellow-fever-risk country" in items[0]["note"]   # the triggering rule
    assert "vaccination_certificate" in items[0]["satisfied_by"]


def test_confirmed_recent_travel_triggers_the_certificate():
    answers = dict(USA_SGP, recent_travel_countries=["KEN"])
    cl = intake_flow.derive_document_checklist(SGP_GUIDANCE, answers=answers)
    assert any(i["id"].startswith("health:") for i in cl)


def test_answered_no_recent_travel_omits_item_and_question():
    answers = dict(USA_SGP, recent_travel_countries=[])
    cl = intake_flow.derive_document_checklist(SGP_GUIDANCE, answers=answers)
    assert not any(i["id"].startswith("health:") for i in cl)
    assert intake_flow.pending_health_questions(SGP_GUIDANCE, answers=answers) == []


def test_question_is_asked_only_when_conditional_and_unanswered():
    qs = intake_flow.pending_health_questions(SGP_GUIDANCE, answers=dict(USA_SGP))
    assert len(qs) == 1
    assert qs[0]["question"] == YF["question"]
    assert qs[0]["answer_key"] == "recent_travel_countries"
    # No health requirements -> no question, ever.
    assert intake_flow.pending_health_questions(
        dict(SGP_GUIDANCE, health_requirements=[]), answers=dict(USA_SGP)) == []


def test_always_required_health_item_needs_no_question():
    g = dict(SGP_GUIDANCE, health_requirements=[
        {"name": "Polio vaccination certificate", "applicability": "always_required",
         "trigger_countries": [], "trigger": "required for all travelers"}])
    cl = intake_flow.derive_document_checklist(g, answers=dict(USA_SGP))
    assert any(i["id"].startswith("health:") for i in cl)
    assert intake_flow.pending_health_questions(g, answers=dict(USA_SGP)) == []


def test_not_applicable_is_entirely_absent_not_optional():
    g = dict(SGP_GUIDANCE, health_requirements=[
        dict(YF, applicability="not_applicable")])
    cl = intake_flow.derive_document_checklist(g, answers=dict(USA_SGP))
    assert not any(i["id"].startswith("health:") for i in cl)
    assert intake_flow.pending_health_questions(g, answers=dict(USA_SGP)) == []


# ---- API flow: question -> answer -> checklist update ------------------------
def test_checklist_updates_after_travel_history_answer(client, db):
    kimi_primary.set_provider(_two_pass(SGP_GUIDANCE))
    r = client.post("/intake", json={"answers": dict(USA_SGP)}, headers=H)
    iid = r.json()["id"]
    client.post(f"/intake/{iid}/resolve", headers=H)
    client.post(f"/intake/{iid}/guidance", headers=H)
    case_id = client.post(f"/intake/{iid}/continue", headers=H).json()["case_id"]

    j = client.get(f"/cases/{case_id}/checklist", headers=H).json()
    # Unanswered conditional -> question pending, no certificate item yet.
    assert len(j["health_questions"]) == 1
    assert not any(i["id"].startswith("health:") for i in j["checklist"])

    # The applicant answers YES (was in Kenya recently).
    client.post(f"/cases/{case_id}/answers", headers=H,
                json={"answers": {"recent_travel_countries": ["KEN"]}})
    j2 = client.get(f"/cases/{case_id}/checklist", headers=H).json()
    assert j2["health_questions"] == []
    health = [i for i in j2["checklist"] if i["id"].startswith("health:")]
    assert len(health) == 1 and "Yellow fever" in health[0]["label"]

    # A vaccination-certificate upload satisfies the item.
    up = client.post(f"/cases/{case_id}/documents", headers=H, json={
        "name": "yellow-fever-card.pdf", "mime": "application/pdf",
        "size_bytes": 1024,
        "text": "International Certificate of Vaccination or Prophylaxis\n"
                "Vaccine: Yellow fever\nDate of vaccination: 12 Jan 2026\n"
                "Batch number: YF-1234"})
    assert up.json()["doc_type"] == "vaccination_certificate"
    j3 = client.get(f"/cases/{case_id}/checklist", headers=H).json()
    health3 = [i for i in j3["checklist"] if i["id"].startswith("health:")]
    assert health3[0]["status"] == "provided"


def test_answered_no_keeps_checklist_clean(client, db):
    kimi_primary.set_provider(_two_pass(SGP_GUIDANCE))
    r = client.post("/intake", json={"answers": dict(USA_SGP, email="v2@example.com")},
                    headers=H)
    iid = r.json()["id"]
    client.post(f"/intake/{iid}/guidance", headers=H)
    case_id = client.post(f"/intake/{iid}/continue", headers=H).json()["case_id"]
    client.post(f"/cases/{case_id}/answers", headers=H,
                json={"answers": {"recent_travel_countries": []}})
    j = client.get(f"/cases/{case_id}/checklist", headers=H).json()
    assert j["health_questions"] == []
    assert not any(i["id"].startswith("health:") for i in j["checklist"])
