"""End-to-end tests through the durable service + the FastAPI API, including the
worker-restart durability property and the safety invariants."""
import importlib

from tests.conftest import AUTH, AUTH2, PASSPORT_MRZ
from app import service, models


def _drive_to_completion(db, app_id, portal_emailbox=None):
    """Advance a case through every human handoff to a terminal state."""
    status, wf = service.signal(db, app_id, "start")
    for _ in range(60):
        st = status["state"]
        if st in ("COMPLETED", "CANCELLED", "MANUAL_REVIEW_REQUIRED", "RECOVERABLE_FAILURE"):
            break
        pending = status.get("pending")
        if not pending:
            break
        h = pending["handoff"]
        if h == "review":
            status, wf = service.signal(db, app_id, "approve_review")
        elif h == "authorization":
            status, wf = service.signal(db, app_id, "sign_authorization")
        elif h == "captcha":
            status, wf = service.signal(db, app_id, "solve_captcha")
        elif h == "email_verification":
            token = _latest_verify_token(wf)
            status, wf = service.signal(db, app_id, "verify_email", token=token)
        elif h == "payment_approval":
            status, wf = service.signal(db, app_id, "approve_payment")
        elif h == "payment":
            status, wf = service.signal(db, app_id, "complete_payment")
        elif h == "appointment_selection":
            status, wf = service.signal(db, app_id, "select_appointment",
                                        slot_id=pending["slots"][0]["slotId"])
        elif h == "personal_declaration":
            status, wf = service.signal(db, app_id, "complete_declaration")
        elif h == "final_review":
            _sign_final_review(db, app_id)
            status, wf = service.signal(db, app_id, "start")
        else:
            break
    return status, wf


def _sign_final_review(db, app_id):
    """Review + sign the exact final application version (§7)."""
    from app import final_review
    app_row = db.get(models.VisaApplication, app_id)
    row = final_review.create_review_version(db, app_row, actor="user1")
    sig = models.NativeSignature(org_id=app_row.org_id, application_id=app_id,
                                 app_version=app_row.current_version,
                                 template_version="t1", consent_version="c1",
                                 signature_method="typed", auth_method="email_otp",
                                 document_hash=row.content_hash, artifact_hash="test",
                                 app_snapshot_hash=row.content_hash)
    db.add(sig)
    db.flush()
    final_review.record_signature(db, row, signature_id=sig.id, actor="user1")
    return row


def _latest_verify_token(wf):
    for e in reversed(wf._portal.emails):
        if e["kind"] == "verification":
            return e["link"].split("token=")[1]
    return None


def _new_case(db, org="org1", user="user1", country="Mockland", grant=True):
    a = models.Applicant(org_id=org, user_id=user, full_name="Anna Eriksson",
                         email="anna@example.com", time_zone="UTC")
    db.add(a); db.flush()
    app_row = models.VisaApplication(
        org_id=org, user_id=user, applicant_id=a.id, destination_country=country,
        answers={"full_name": "Anna Eriksson", "email": "anna@example.com",
                 "passport_number": "L898902C3", "nationality": "UTO", "birth_date": "740812",
                 "sex": "F", "passport_expiry": "330415", "intended_arrival": "2026-10-10",
                 "intended_departure": "2026-10-20", "entry_checkpoint": "Noi Bai",
                 "accommodation": "Hotel"})
    db.add(app_row)
    db.flush()  # populate app_row.id (uuid default) before referencing it
    # Documents + preferences + authorization.
    db.add(models.StoredDocument(org_id=org, application_id=app_row.id, name="passport.pdf",
                                 mime="application/pdf", doc_type="passport", approved=True))
    db.add(models.AppointmentPreference(application_id=app_row.id,
                                        prefs={"preferredLocation": "MOCKLAND-CAP", "allowAutoBook": True,
                                               "applicantTimeZone": "UTC", "minAdvanceMs": 0}))
    db.add(models.AuthorizationEnvelope(application_id=app_row.id, provider="in_app_authorization",
                                        max_fee_cents=10000, currency="USD", allow_auto_book=True))
    db.commit()
    # Standing authorization (§5) — granted once at onboarding.
    if grant:
        from app import authorization as standing
        standing.grant(db, app_row=app_row, principal_user=user)
    return app_row.id


def test_full_e2e_completes(db):
    app_id = _new_case(db, country="Mockland")
    status, wf = _drive_to_completion(db, app_id)
    assert status["state"] == "COMPLETED"
    from sqlalchemy import select
    conf = db.execute(select(models.SubmissionConfirmation).where(
        models.SubmissionConfirmation.application_id == app_id)).scalar_one_or_none()
    assert conf and conf.reference_no.startswith("REF-")
    appt = db.execute(select(models.Appointment).where(
        models.Appointment.application_id == app_id)).scalar_one_or_none()
    assert appt and appt.confirmation_no.startswith("APT-")


def test_vietnam_evisa_skips_appointment(db):
    app_id = _new_case(db, country="Vietnam")
    status, wf = _drive_to_completion(db, app_id)
    assert status["state"] == "COMPLETED"
    from sqlalchemy import select
    # e-visa has no appointment
    appt = db.execute(select(models.Appointment).where(
        models.Appointment.application_id == app_id)).scalar_one_or_none()
    assert appt is None


def test_signed_ceremony_reconciles_a_lost_authorization_signal(db):
    """Regression (2026-07-28): the applicant's signature ceremony completed
    (standing authorization granted) but the sign-time workflow signal was
    rejected by a preflight gate, leaving the machine paused at the
    authorization handoff. Any later signal must reconcile the durable input
    mirror from the DB ceremony records — never re-ask for a signature that
    already exists."""
    app_id = _new_case(db, grant=False)
    status, wf = service.signal(db, app_id, "start")
    assert status["pending"]["handoff"] == "authorization"
    # The ceremony completes, but its workflow signal never arrives.
    from app import authorization as standing
    standing.grant(db, app_row=db.get(models.VisaApplication, app_id),
                   principal_user="user1")
    status, wf = service.signal(db, app_id, "start")
    assert wf.inputs["authorization_signed"] is True
    pending = status.get("pending") or {}
    assert pending.get("handoff") != "authorization"


def test_captcha_is_a_human_handoff(db):
    app_id = _new_case(db)
    status, wf = service.signal(db, app_id, "start")
    status, wf = service.signal(db, app_id, "approve_review")
    status, wf = service.signal(db, app_id, "sign_authorization")
    assert status["state"] == "CAPTCHA_ACTION_REQUIRED"
    assert status["pending"]["handoff"] == "captcha"
    # A guessed answer is rejected by the portal.
    assert wf._portal.submit_captcha(captcha_token="x", human_answer="ELLIS_GUESS")["ok"] is False


def test_worker_restart_preserves_completed_workflow(db):
    """The heart of durability: after completion, a brand-new process (fresh
    imports, no in-memory state) reads the same terminal state + confirmation."""
    app_id = _new_case(db)
    status, _ = _drive_to_completion(db, app_id)
    assert status["state"] == "COMPLETED"

    # Simulate a worker restart: reload modules + a fresh session, then read.
    import app.service as svc
    importlib.reload(svc)
    from app.db import SessionLocal
    fresh = SessionLocal()
    try:
        wf = svc.load_workflow(fresh, app_id)
        assert wf.machine.state == "COMPLETED"
        assert wf.confirmation and wf.confirmation["referenceNo"].startswith("REF-")
        # Re-driving a completed case is a no-op (idempotent) — no double submit.
        status2 = wf.start()
        assert status2["state"] == "COMPLETED"
    finally:
        fresh.close()


def test_generated_password_never_leaks(db):
    app_id = _new_case(db)
    _drive_to_completion(db, app_id)
    from app import vault, audit
    # Find the portal account's credential ref and reveal it.
    from sqlalchemy import select
    exec_row = db.execute(select(models.WorkflowExecution).where(
        models.WorkflowExecution.application_id == app_id)).scalar_one()
    cred_ref = exec_row.snapshot.get("credential_ref")
    pw = vault.reveal(cred_ref)
    assert len(pw) >= 12
    # Not in audit, not in any email row.
    assert audit.contains_plaintext(db, pw) is False
    emails = db.execute(select(models.EmailNotification).where(
        models.EmailNotification.application_id == app_id)).scalars().all()
    assert all(pw not in e.body for e in emails)


# ---- API tests ----
def test_api_flow_and_tenant_isolation(client):
    # Create a case.
    r = client.post("/cases", headers=AUTH, json={
        "full_name": "Anna Eriksson", "email": "anna@example.com",
        "destination_country": "Mockland"})
    assert r.status_code == 200, r.text
    cid = r.json()["id"]

    # Upload a passport (embedded MRZ text) → OCR extracts + validates.
    r = client.post(f"/cases/{cid}/documents", headers=AUTH,
                    json={"name": "passport.pdf", "mime": "application/pdf", "text": PASSPORT_MRZ})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["doc_type"] == "passport" and body["mrz_valid"] is True
    doc_id = body["id"]

    # Applicant reviews + approves the extracted fields.
    r = client.post(f"/cases/{cid}/documents/{doc_id}/approve", headers=AUTH, json=[])
    assert r.status_code == 200

    # Cross-tenant access is denied.
    r = client.get(f"/cases/{cid}", headers=AUTH2)
    assert r.status_code == 403

    # Auth required.
    r = client.get(f"/cases/{cid}")
    assert r.status_code == 401


def test_api_rejects_bad_mime(client):
    r = client.post("/cases", headers=AUTH, json={"full_name": "X", "email": "x@e.com",
                                                  "destination_country": "Mockland"})
    cid = r.json()["id"]
    r = client.post(f"/cases/{cid}/documents", headers=AUTH,
                    json={"name": "x.exe", "mime": "application/octet-stream", "text": ""})
    assert r.status_code == 415


def test_capabilities_endpoint(client):
    r = client.get("/capabilities", headers=AUTH)
    assert r.status_code == 200
    caps = r.json()
    # With no cloud creds, everything is on a safe local fallback.
    assert caps["fallbacks"]["payment"] == "applicant_payment_window"
    assert caps["fallbacks"]["ocr"] == "local_mrz_provider"
    assert caps["workflow_engine"] == "db_runner"
