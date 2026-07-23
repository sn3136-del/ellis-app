"""Execution classification — the brief's #1 absolute requirement.

Proves: every external action carries an exact classification; a MOCK result is
never presented as a real government outcome; classification follows the DRIVER
(never a config claim); and it is persisted to the DB + audit + API responses.
"""
import pytest

from tests.conftest import AUTH, PASSPORT_MRZ
from tests.test_e2e import _new_case, _drive_to_completion
from app import execution, models
from app.execution import ExecutionClass as EC
from app.portal.mock_portal import MockPortal
from sqlalchemy import select


# ---- the enum matches the brief exactly ----
def test_enum_has_exactly_the_seven_brief_classes():
    assert {c.value for c in EC} == {
        "MOCK", "LOCAL_PROVIDER", "LIVE_SANDBOX", "LIVE_PRODUCTION",
        "APPLICANT_ACTION_REQUIRED", "MANUAL_REVIEW_REQUIRED", "UNSUPPORTED"}


# ---- classification follows the driver, never a claim ----
def test_mock_portal_driver_is_mock():
    assert execution.classify_driver(MockPortal()) == EC.MOCK


def test_unknown_driver_is_manual_review_not_real():
    assert execution.classify_driver(None) == EC.MANUAL_REVIEW_REQUIRED
    assert execution.classify_driver(object()) == EC.MANUAL_REVIEW_REQUIRED


def test_clamp_cannot_upgrade_a_mock_driver_to_production():
    # Even a LIVE_PRODUCTION *claim* is clamped down to the driver's real class.
    assert execution.clamp(EC.LIVE_PRODUCTION, EC.MOCK) == EC.MOCK
    assert execution.clamp(EC.LIVE_SANDBOX, EC.LOCAL_PROVIDER) == EC.LOCAL_PROVIDER
    assert execution.clamp(EC.LOCAL_PROVIDER, EC.LIVE_PRODUCTION) == EC.LOCAL_PROVIDER


def test_classify_adapter_bound_to_mock_is_never_real():
    # A fabricated adapter that CLAIMS production but binds a MockPortal driver.
    class FakeAdapter:
        production_approval_status = "production_approved"
        production_enabled = True
        driver = MockPortal()
    assert execution.classify_adapter(FakeAdapter()) == EC.MOCK
    assert execution.is_real_government_result(execution.classify_adapter(FakeAdapter())) is False


def test_classify_adapter_none_is_unsupported():
    assert execution.classify_adapter(None) == EC.UNSUPPORTED


class _LiveDriver:
    execution_class = "LIVE_PRODUCTION"


class _UndeclaredDriver:
    """A real-ish driver that forgot to declare its class (e.g. a future
    Playwright driver mid-build). Must NOT be trusted as real."""


def test_config_cannot_elevate_an_undeclared_driver_regression():
    # REGRESSION (review-confirmed high): a driver that is not a known provider
    # class must never be elevated to LIVE_PRODUCTION by a config claim.
    class A:
        production_approval_status = "production_approved"
        production_enabled = True
        driver = _UndeclaredDriver()
    ec = execution.classify_adapter(A())
    assert ec != EC.LIVE_PRODUCTION
    assert execution.is_real_government_result(ec) is False


def test_live_driver_not_enabled_is_at_most_sandbox_regression():
    # REGRESSION: an approved-but-not-enabled (or paused/mid-activation) adapter
    # with a real live driver is at most LIVE_SANDBOX — never a real result.
    class A:
        production_approval_status = "production_approved"
        production_enabled = False
        driver = _LiveDriver()
    ec = execution.classify_adapter(A())
    assert ec == EC.LIVE_SANDBOX
    assert execution.is_real_government_result(ec) is False


def test_live_driver_approved_and_enabled_is_real():
    # The legitimate real path still works: verified live driver + approved+enabled.
    class A:
        production_approval_status = "production_approved"
        production_enabled = True
        driver = _LiveDriver()
    assert execution.classify_adapter(A()) == EC.LIVE_PRODUCTION


# ---- OCR classification ----
def test_classify_ocr_local_vs_live():
    assert execution.classify_ocr({"primary": "local_text"}) == EC.LOCAL_PROVIDER
    assert execution.classify_ocr({"primary": "google_document_ai"}) == EC.LIVE_PRODUCTION
    assert execution.classify_ocr(
        {"primary": "google_document_ai", "fallback_used": True,
         "fallback_engine": "kimi_k3_vision"}) == EC.LIVE_PRODUCTION
    assert execution.classify_ocr({"docai_degraded": True, "primary": None}) == EC.LOCAL_PROVIDER


# ---- the display guard ----
def test_only_live_production_is_a_real_government_result():
    assert execution.is_real_government_result(EC.LIVE_PRODUCTION) is True
    for c in (EC.MOCK, EC.LOCAL_PROVIDER, EC.LIVE_SANDBOX,
              EC.APPLICANT_ACTION_REQUIRED, EC.MANUAL_REVIEW_REQUIRED, EC.UNSUPPORTED):
        assert execution.is_real_government_result(c) is False


def test_completed_mock_case_never_reads_as_real():
    d = execution.result_disposition("COMPLETED", EC.MOCK)
    assert d["is_real_government_result"] is False
    assert d["real_terminal_claims_allowed"] is False
    assert "MOCK" in d["display_status"]
    assert d["disclaimer"]  # a plain-language explanation is present


def test_completed_live_production_reads_as_real():
    d = execution.result_disposition("COMPLETED", EC.LIVE_PRODUCTION)
    assert d["is_real_government_result"] is True
    assert d["display_status"] == "COMPLETED"
    assert d["disclaimer"] == ""


def test_live_production_not_verified_is_not_real():
    d = execution.result_disposition("COMPLETED", EC.LIVE_PRODUCTION, adapter_verified=False)
    assert d["is_real_government_result"] is False


def test_assert_not_mock_in_production():
    # In production, a mock/sandbox/local class may not back a real outcome.
    with pytest.raises(execution.MockAsProductionError):
        execution.assert_not_mock_in_production(EC.MOCK, production_mode=True)
    with pytest.raises(execution.MockAsProductionError):
        execution.assert_not_mock_in_production(EC.LIVE_SANDBOX, production_mode=True)
    # LIVE_PRODUCTION passes; and outside production nothing is blocked.
    execution.assert_not_mock_in_production(EC.LIVE_PRODUCTION, production_mode=True)
    execution.assert_not_mock_in_production(EC.MOCK, production_mode=False)


# ---- API integration ----
def test_capabilities_exposes_classification_legend(client):
    caps = client.get("/capabilities", headers=AUTH).json()
    ec = caps["execution_classification"]
    assert set(ec["legend"]) == {c.value for c in EC}
    assert ec["runtime_portal_execution_class"] == "MOCK"
    assert ec["real_result_class"] == "LIVE_PRODUCTION"


def test_ocr_endpoint_returns_and_persists_local_class(client, db):
    cid = client.post("/cases", headers=AUTH, json={
        "full_name": "Anna", "email": "a@e.com", "destination_country": "Mockland"}).json()["id"]
    r = client.post(f"/cases/{cid}/documents", headers=AUTH,
                    json={"name": "passport.pdf", "mime": "application/pdf", "text": PASSPORT_MRZ})
    body = r.json()
    # No cloud creds in hermetic tests → the deterministic local parser ran.
    assert body["execution_class"] == "LOCAL_PROVIDER"
    doc = db.execute(select(models.StoredDocument).where(
        models.StoredDocument.application_id == cid)).scalar_one()
    assert doc.execution_class == "LOCAL_PROVIDER"
    rec = db.execute(select(models.ExecutionRecord).where(
        models.ExecutionRecord.application_id == cid,
        models.ExecutionRecord.action == "document_ocr")).scalar_one()
    assert rec.execution_class == "LOCAL_PROVIDER" and rec.is_real_government_result is False


def test_ocr_record_is_not_a_real_government_result(client, db):
    # REGRESSION (review-confirmed): a document_ocr execution record must NOT be
    # flagged is_real_government_result — OCR is a provider run, not a gov outcome.
    cid = client.post("/cases", headers=AUTH, json={
        "full_name": "Anna", "email": "a@e.com", "destination_country": "Mockland"}).json()["id"]
    client.post(f"/cases/{cid}/documents", headers=AUTH,
                json={"name": "passport.pdf", "mime": "application/pdf", "text": PASSPORT_MRZ})
    rec = db.execute(select(models.ExecutionRecord).where(
        models.ExecutionRecord.application_id == cid,
        models.ExecutionRecord.action == "document_ocr")).scalar_one()
    assert rec.is_real_government_result is False


def test_record_execution_government_outcome_flag(db):
    # government_outcome=False never marks real, even for LIVE_PRODUCTION.
    execution.record_execution(db, org_id="orgX", application_id="", action="document_ocr",
                               ec=EC.LIVE_PRODUCTION, government_outcome=False)
    r = db.execute(select(models.ExecutionRecord).where(
        models.ExecutionRecord.org_id == "orgX")).scalars().first()
    assert r.is_real_government_result is False


def test_get_case_carries_mock_disposition(client, db):
    cid = client.post("/cases", headers=AUTH, json={
        "full_name": "Anna", "email": "a@e.com", "destination_country": "Mockland"}).json()["id"]
    case = client.get(f"/cases/{cid}", headers=AUTH).json()
    assert case["execution_class"] == "MOCK"
    assert case["disposition"]["is_real_government_result"] is False


def test_completed_case_confirmation_is_not_a_real_government_confirmation(db):
    """The crux: a case that runs to COMPLETED on the mock portal must NOT be
    presentable as a real government confirmation, and the DB must record that."""
    app_id = _new_case(db, country="Mockland")
    status, _ = _drive_to_completion(db, app_id)
    assert status["state"] == "COMPLETED"
    # A confirmation row exists (the mock produced a reference)…
    conf = db.execute(select(models.SubmissionConfirmation).where(
        models.SubmissionConfirmation.application_id == app_id)).scalar_one()
    assert conf.reference_no
    # …but classified honestly, it is NOT a real government result.
    disp = execution.result_disposition("COMPLETED", EC.MOCK)
    assert disp["is_real_government_result"] is False


def test_terminal_execution_record_persisted_via_api(client, db):
    """Driving a case to COMPLETED through the API records a case_completed
    execution classification of MOCK (never real)."""
    cid = client.post("/cases", headers=AUTH, json={
        "full_name": "Anna Eriksson", "email": "anna@example.com",
        "destination_country": "Mockland",
        "answers": {"passport_number": "L898902C3", "nationality": "UTO",
                    "birth_date": "740812", "sex": "F", "passport_expiry": "300415"}}).json()["id"]
    # Minimal prerequisites for the workflow to run to completion.
    db.add(models.AppointmentPreference(application_id=cid,
           prefs={"preferredLocation": "MOCKLAND-CAP", "allowAutoBook": True,
                  "applicantTimeZone": "UTC", "minAdvanceMs": 0}))
    db.add(models.AuthorizationEnvelope(application_id=cid, provider="in_app_authorization",
           max_fee_cents=10000, currency="USD", allow_auto_book=True))
    db.commit()
    client.post(f"/cases/{cid}/start", headers=AUTH)
    for name in ("approve_review", "sign_authorization", "solve_captcha"):
        client.post(f"/cases/{cid}/signals/{name}", headers=AUTH, json={})
    # Complete the remaining handoffs by reading pending each time.
    for _ in range(20):
        case = client.get(f"/cases/{cid}", headers=AUTH).json()
        if case["state"] == "COMPLETED":
            break
        pending = case.get("pending")
        if not pending:
            break
        h = pending.get("handoff")
        if h == "email_verification":
            tok = client.get(f"/cases/{cid}/mock/verification", headers=AUTH).json()["token"]
            client.post(f"/cases/{cid}/signals/verify_email", headers=AUTH, json={"token": tok})
        elif h == "payment_approval":
            client.post(f"/cases/{cid}/signals/approve_payment", headers=AUTH, json={})
        elif h == "payment":
            client.post(f"/cases/{cid}/signals/complete_payment", headers=AUTH, json={})
        elif h == "appointment_selection":
            client.post(f"/cases/{cid}/signals/select_appointment", headers=AUTH,
                        json={"slot_id": pending["slots"][0]["slotId"]})
        elif h == "personal_declaration":
            client.post(f"/cases/{cid}/signals/complete_declaration", headers=AUTH, json={})
        else:
            break
    case = client.get(f"/cases/{cid}", headers=AUTH).json()
    if case["state"] == "COMPLETED":
        assert case["confirmation"]["is_real_government_confirmation"] is False
        rec = db.execute(select(models.ExecutionRecord).where(
            models.ExecutionRecord.application_id == cid,
            models.ExecutionRecord.action == "case_completed")).scalars().first()
        assert rec is not None and rec.execution_class == "MOCK"
