"""Background portal execution (Part 2-6), applicant-safe progress (Part 3-4),
fee/payment confirmation fallbacks (Part 11), contact confirmation (Part 8),
payment-method-is-not-a-document (Part 1), and the envelope-binding fix.

All hermetic: the queue path is exercised by forcing the live-route predicate
on a mock-portal case (the executor then drives the same durable workflow),
and workflow-level behavior is unit-tested with fake drivers that fail the
test if touched when they must not be.
"""
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from tests.conftest import AUTH
from app import models, portal_queue, progress, service, vault, worker
from app.workflow import VisaWorkflow
from app.statemachine import CaseMachine


# ---------- helpers ----------------------------------------------------------

def _new_case(client, name="Queue Test", org_headers=AUTH):
    return client.post("/cases", headers=org_headers, json={
        "full_name": name, "email": "queue@example.com",
        "destination_country": "Mockland", "visa_type": "tourist",
        "answers": {"full_name": name, "email": "queue@example.com",
                    "passport_number": "L898902C3", "nationality": "UTO",
                    "birth_date": "1974-08-12", "sex": "F",
                    "passport_expiry": "2033-04-15",
                    "intended_arrival": "2026-10-10",
                    "intended_departure": "2026-10-20",
                    "entry_checkpoint": "Noi Bai", "accommodation": "Hotel"}}).json()


@pytest.fixture
def live_route(monkeypatch):
    """Force the live-background predicate + neutralize the live personal gate
    (its own behavior is pinned by test_personal_gate)."""
    from app import main as main_mod
    from app import personal_gate
    monkeypatch.setattr(main_mod, "_live_background_route", lambda db, app_row: True)
    monkeypatch.setattr(personal_gate, "assert_ready_for_live_action",
                        lambda *a, **k: None)
    yield


def _confirm_contact(client, case_id):
    r = client.post(f"/cases/{case_id}/contact-confirmation", headers=AUTH,
                    json={"phone": "+86 138 0000 1234", "confirm": True})
    assert r.status_code == 200, r.text
    return r.json()


class _UntouchableDriver:
    """A driver that FAILS the test if any portal method is invoked."""

    def __getattr__(self, name):
        raise AssertionError(f"live driver touched in foreground path: {name}")


class _Adapter:
    """Minimal adapter facade for unit-level workflow tests."""
    adapter_id = "fake"
    channel = "released_flow"
    account_required = False
    required_applicant_fields = []
    appointment_search = "none"
    appointment_booking = "prohibited"
    personal_declaration_required = False
    portal_operator = "Official Portal"
    third_party_payment_policy = "applicant"
    password_requirements = {"minLength": 12}

    def __init__(self, driver):
        self.driver = driver


def _wf(driver, state, **snapshot):
    wf = VisaWorkflow(case_id="c1", org_id="org1", adapter=_Adapter(driver),
                      applicant={"full_name": "T", "email": "t@example.com"},
                      answers={}, documents=[])
    wf.machine = CaseMachine(state, [{"state": state, "reason": "test"}])
    for k, v in snapshot.items():
        setattr(wf, k, v)
    return wf


# ---------- Part 2: fast Continue, idempotent queue --------------------------

def test_contact_confirmation_required_before_portal_start(client, live_route):
    case = _new_case(client)
    r = client.post(f"/cases/{case['id']}/signals/sign_authorization", headers=AUTH, json={})
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "contact_unconfirmed"


def test_contact_endpoint_masks_phone_and_confirms(client):
    case = _new_case(client)
    got = _confirm_contact(client, case["id"])
    assert got["confirmed"] is True
    assert got["phone_masked"].endswith("1234")
    assert "138" not in got["phone_masked"]      # only the last 4 digits show
    assert got["email"] == "queue@example.com"


def test_continue_returns_immediately_and_is_idempotent(client, live_route, db):
    case = _new_case(client)
    _confirm_contact(client, case["id"])
    t0 = time.monotonic()
    r1 = client.post(f"/cases/{case['id']}/signals/sign_authorization", headers=AUTH, json={})
    elapsed = time.monotonic() - t0
    assert r1.status_code == 200, r1.text
    body = r1.json()
    assert body["queued"] is True and body["run_id"]
    assert elapsed < 2.0          # no portal work inside the HTTP request
    # Repeated clicks reuse the SAME run — no duplicate portal run can exist.
    r2 = client.post(f"/cases/{case['id']}/signals/sign_authorization", headers=AUTH, json={})
    assert r2.json()["run_id"] == body["run_id"]
    assert r2.json()["run_reused"] is True
    active = db.execute(select(models.PortalRun).where(
        models.PortalRun.application_id == case["id"],
        models.PortalRun.status.in_(("queued", "running")))).scalars().all()
    assert len(active) == 1


def test_a_different_signal_is_never_silently_dropped(client, live_route, db):
    case = _new_case(client)
    _confirm_contact(client, case["id"])
    r1 = client.post(f"/cases/{case['id']}/start", headers=AUTH)
    r2 = client.post(f"/cases/{case['id']}/signals/sign_authorization", headers=AUTH, json={})
    assert r1.json()["run_id"] != r2.json()["run_id"]   # appended, not swallowed
    runs = db.execute(select(models.PortalRun).where(
        models.PortalRun.application_id == case["id"],
        models.PortalRun.status == "queued").order_by(
        models.PortalRun.created_at.asc())).scalars().all()
    assert [r.signal_name for r in runs] == ["start", "sign_authorization"]
    # Queued runs execute strictly one at a time per case.
    executed = portal_queue.run_pending_once("test-exec")
    assert executed >= 2


def test_queue_backlog_is_capped_honestly(client, live_route, db):
    case = _new_case(client)
    _confirm_contact(client, case["id"])
    for name in ("start", "sign_authorization", "approve_review"):
        assert client.post(f"/cases/{case['id']}/signals/{name}" if name != "start"
                           else f"/cases/{case['id']}/start",
                           headers=AUTH, json={}).status_code == 200
    r = client.post(f"/cases/{case['id']}/signals/complete_payment", headers=AUTH, json={})
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "case_busy"


def test_executor_drives_queued_run_outside_the_request(client, live_route, db):
    case = _new_case(client)
    _confirm_contact(client, case["id"])
    client.post(f"/cases/{case['id']}/start", headers=AUTH)
    assert portal_queue.run_pending_once("test-exec") >= 1
    run = portal_queue.latest_run(db, case["id"])
    assert run.status in ("waiting_applicant", "completed")
    exec_row = db.execute(select(models.WorkflowExecution).where(
        models.WorkflowExecution.application_id == case["id"])).scalar_one()
    assert exec_row.state != "DRAFT"          # the workflow actually advanced
    assert run.finished_at is not None


def test_worker_tick_never_touches_cases_without_queued_runs(client, db):
    case = _new_case(client)      # no run enqueued
    before = db.get(models.VisaApplication, case["id"]).state
    assert worker.tick_once(db) == 0
    db.expire_all()
    assert db.get(models.VisaApplication, case["id"]).state == before


# ---------- Parts 3-4: progress ----------------------------------------------

def test_progress_endpoint_reflects_real_checkpoints(client, live_route, db):
    case = _new_case(client)
    _confirm_contact(client, case["id"])
    client.post(f"/cases/{case['id']}/start", headers=AUTH)
    portal_queue.run_pending_once("test-exec")
    r = client.get(f"/cases/{case['id']}/progress", headers=AUTH)
    assert r.status_code == 200
    pr = r.json()
    assert pr["step"]["key"] in progress.STEP_MESSAGES
    assert pr["step"]["message"] == progress.STEP_MESSAGES[pr["step"]["key"]]
    assert pr["run_status"] in ("waiting_applicant", "completed")
    # Applicant-safe: no selectors, vault refs, or stack traces anywhere.
    dump = r.text
    for needle in ("vault://", "Traceback", "#basic_", "css=", "xpath"):
        assert needle not in dump


def test_progress_survives_refresh_and_records_last_completed(client, live_route, db):
    case = _new_case(client)
    _confirm_contact(client, case["id"])
    client.post(f"/cases/{case['id']}/start", headers=AUTH)
    portal_queue.run_pending_once("test-exec")
    first = client.get(f"/cases/{case['id']}/progress", headers=AUTH).json()
    again = client.get(f"/cases/{case['id']}/progress", headers=AUTH).json()
    assert first["step"] == again["step"]      # persisted, not per-request state
    events = db.execute(select(models.CaseProgressEvent).where(
        models.CaseProgressEvent.application_id == case["id"])).scalars().all()
    assert events, "progress events must persist"


def test_no_progress_message_credits_kimi_with_portal_work():
    for key, msg in progress.STEP_MESSAGES.items():
        assert "kimi" not in msg.lower(), key


def test_stalled_run_shows_honest_message_and_safe_retry(client, live_route, db):
    case = _new_case(client)
    _confirm_contact(client, case["id"])
    r = client.post(f"/cases/{case['id']}/start", headers=AUTH)
    run_id = r.json()["run_id"]
    run = db.get(models.PortalRun, run_id)
    run.status = "running"
    run.claimed_by = "dead-worker"
    run.started_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    run.lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    db.commit()
    assert portal_queue.expire_stale_leases(db) == 1
    pr = client.get(f"/cases/{case['id']}/progress", headers=AUTH).json()
    assert pr["stalled"] is True
    assert "Retry the connection" in pr["stall_message"]
    assert pr["retry_available"] is True
    # The applicant-triggered retry enqueues a fresh run; nothing retried alone.
    rr = client.post(f"/cases/{case['id']}/portal/retry", headers=AUTH)
    assert rr.status_code == 200 and rr.json()["queued"] is True


def test_retry_unavailable_while_a_run_is_active(client, live_route, db):
    case = _new_case(client)
    _confirm_contact(client, case["id"])
    client.post(f"/cases/{case['id']}/start", headers=AUTH)   # queued, unclaimed
    rr = client.post(f"/cases/{case['id']}/portal/retry", headers=AUTH)
    assert rr.status_code == 409


# ---------- Part 2 (unit): foreground never drives the live portal -----------

def test_foreground_defers_before_any_live_driver_state():
    wf = _wf(_UntouchableDriver(), "AUTHORIZATION_PENDING")
    wf.defer_live = True
    wf.sign_authorization()       # would hit d.login without the defer guard
    assert wf.deferred_live is True
    assert wf.machine.state == "PORTAL_LOGIN_REQUIRED"


def test_background_workers_cannot_submit_without_applicant_confirmation():
    wf = _wf(_UntouchableDriver(), "READY_TO_SUBMIT")
    wf.block_submit = True
    wf.start()
    assert wf.machine.state == "READY_TO_SUBMIT"
    assert wf.pending["handoff"] == "final_review"


def test_submitting_retry_without_confirmation_reconciles_but_never_clicks():
    class ReconcileOnly:
        def get_application_state(self, **_kw):
            return {"ok": True, "submitted": False, "paid": True}

        def submit(self, **_kw):
            raise AssertionError("submit clicked by an unconfirmed background run")

        def detach(self):
            pass

    wf = _wf(ReconcileOnly(), "SUBMITTING")
    wf.block_submit = True
    wf.session_ref = vault.store("session-token")["ref"]
    wf.start()
    assert wf.machine.state == "SUBMITTING"
    assert wf.pending["handoff"] == "final_review"


# ---------- Part 11: fee confirmation + payment verification -----------------

def _fee_wf(driver, state, **kw):
    wf = _wf(driver, state, **kw)
    wf.session_ref = vault.store("session-token")["ref"]
    return wf


def test_unreadable_fee_becomes_applicant_fee_confirmation_never_invented():
    class NoFee:
        def discover_fee(self, **_kw):
            return {"ok": False, "code": "FEE_NOT_DISPLAYED"}

        def detach(self):
            pass

    wf = _fee_wf(NoFee(), "FEE_DISCOVERY_PENDING")
    wf.start()
    assert wf.machine.state == "PAYMENT_APPROVAL_REQUIRED"
    assert wf.pending["handoff"] == "fee_confirmation"
    assert wf.fee is None                       # nothing invented
    # Approving without an amount re-asks; a malformed currency re-asks.
    wf.approve_payment()
    assert wf.pending["handoff"] == "fee_confirmation"
    wf.approve_payment(amount_cents=2500, currency="US1")
    assert wf.pending["handoff"] == "fee_confirmation"
    # The applicant confirms the exact fee the official portal displayed.
    wf.approve_payment(amount_cents=2500, currency="USD")
    assert wf.fee["amount"] == 2500 and wf.fee["currency"] == "USD"
    assert wf.fee["source"] == "portal_display_confirmed_by_applicant"
    assert wf.machine.state == "PAYMENT_ACTION_REQUIRED"
    assert wf.pending["handoff"] == "payment"


def test_unverifiable_payment_result_asks_the_applicant_never_assumes():
    class UncertainPay:
        def get_application_state(self, **_kw):
            return {"ok": True, "paid": False, "submitted": False}

        def pay(self, **_kw):
            return {"ok": False, "code": "OUTCOME_UNCERTAIN"}

        def detach(self):
            pass

    fee = {"ok": True, "amount": 2500, "currency": "USD", "display": "25.00 USD",
           "government_fee_cents": 2500, "service_fee_cents": 0, "payee": "Portal"}
    wf = _fee_wf(UncertainPay(), "PAYMENT_PROCESSING", fee=fee,
                 payment_authorization={"amount_cents": 2500, "currency": "USD",
                                        "payee": "Portal", "status": "authorized"})
    wf.block_submit = True     # scope this test to payment; submission is gated
    wf.inputs["payment_approved"] = True
    wf.inputs["payment_completed"] = True
    wf.start()
    assert wf.machine.state == "PAYMENT_PROCESSING"
    assert wf.pending["handoff"] == "additional_information"
    assert wf.pending["purpose"] == "payment_verification"
    keys = [q["key"] for q in wf.pending["questions"]]
    assert "payment_result" in keys
    # Applicant reports what the OFFICIAL portal showed.
    wf.provide_information({"payment_result": "The portal confirmed my payment",
                            "payment_receipt_reference": "TXN-991"})
    assert wf.machine.state in ("PAYMENT_COMPLETED", "FINAL_REVIEW_REQUIRED",
                                "READY_TO_SUBMIT")
    assert wf.receipt == {"receiptNo": "TXN-991", "source": "applicant_reported"}


def test_applicant_reported_payment_failure_returns_to_payment_never_success():
    class UncertainPay:
        def get_application_state(self, **_kw):
            return {"ok": True, "paid": False, "submitted": False}

        def pay(self, **_kw):
            return {"ok": False, "code": "OUTCOME_UNCERTAIN"}

        def detach(self):
            pass

    fee = {"ok": True, "amount": 2500, "currency": "USD", "display": "25.00 USD",
           "government_fee_cents": 2500, "service_fee_cents": 0, "payee": "Portal"}
    wf = _fee_wf(UncertainPay(), "PAYMENT_PROCESSING", fee=fee,
                 payment_authorization={"amount_cents": 2500, "currency": "USD",
                                        "payee": "Portal", "status": "authorized"})
    wf.inputs["payment_approved"] = True
    wf.inputs["payment_completed"] = True
    wf.start()
    wf.provide_information({"payment_result": "Payment failed or was cancelled"})
    assert wf.machine.state == "PAYMENT_ACTION_REQUIRED"
    assert wf.receipt is None


# ---------- Part 6: vault persistence + OTP hygiene --------------------------

def test_vault_survives_process_restart_via_db_backend():
    stored = vault.store("portal-session-xyz", {"kind": "portal_session"})
    vault._BACKENDS.clear()                    # simulate a backend restart
    assert vault.reveal(stored["ref"]) == "portal-session-xyz"
    assert vault.destroy(stored["ref"]) is True
    with pytest.raises(KeyError):
        vault.reveal(stored["ref"])


def test_vault_never_stores_plaintext():
    stored = vault.store("super-secret-value")
    db = service  # noqa: F841 — direct engine query below
    from app.db import SessionLocal
    s = SessionLocal()
    try:
        row = s.get(models.VaultSecret, stored["ref"])
        assert row is not None
        assert "super-secret-value" not in row.ciphertext
    finally:
        s.close()
    vault.destroy(stored["ref"])


def test_otp_token_is_vaulted_in_queue_and_destroyed_after_use(client, live_route, db, monkeypatch):
    case = _new_case(client)
    _confirm_contact(client, case["id"])
    r = client.post(f"/cases/{case['id']}/signals/verify_email", headers=AUTH,
                    json={"token": "123456"})
    assert r.status_code == 200 and r.json()["queued"] is True
    run = db.get(models.PortalRun, r.json()["run_id"])
    assert "token" not in (run.signal_kwargs or {})
    ref = run.signal_kwargs.get("token_ref")
    assert ref and ref.startswith("vault://")
    assert "123456" not in str(run.signal_kwargs)
    captured = {}

    def fake_signal(db_, app_id, name, **kw):
        captured.update(kw)
        exec_row = db_.execute(select(models.WorkflowExecution).where(
            models.WorkflowExecution.application_id == app_id)).scalar_one_or_none()
        return ({"case_id": app_id, "state": "DRAFT",
                 "pending": exec_row.pending if exec_row else None}, None)

    monkeypatch.setattr(service, "signal", fake_signal)
    portal_queue.run_pending_once("test-exec")
    assert captured.get("token") == "123456"   # revealed exactly once
    with pytest.raises(KeyError):
        vault.reveal(ref)                      # destroyed immediately after use


# ---------- Part 1: payment method is never a document -----------------------

def test_payment_method_labels_never_become_checklist_documents():
    from app.visa_snapshot.intake_flow import derive_document_checklist
    items = derive_document_checklist({
        "disposition": "VISA_REQUIRED",
        "required_documents": ["Passport", "Payment method (credit/debit card)",
                               "Hotel booking", "Visa fee payment"],
    })
    labels = " | ".join(i["label"].lower() for i in items)
    assert "payment" not in labels
    assert any(i["id"] == "hotel_booking" for i in items)


def test_saved_payment_checklist_items_are_healed_away(client, db):
    from app import checklist_intake
    from app.visa_snapshot.models import CaseRouteGuidance
    case = _new_case(client)
    db.add(CaseRouteGuidance(
        case_id=case["id"], org_id="org1",
        guidance={"guidance": {}}, checklist=[
            {"id": "passport", "label": "Passport (biodata page)", "kind": "document",
             "required": True, "satisfied_by": ["passport"]},
            {"id": "doc:payment_method_credit_debit_card",
             "label": "Payment method (credit/debit card)", "kind": "document",
             "required": True, "satisfied_by": ["document"]},
        ]))
    db.commit()
    app_row = db.get(models.VisaApplication, case["id"])
    healed = checklist_intake.current_checklist(db, app_row)
    assert [i["id"] for i in healed] == ["passport"]
    # And checklist completion no longer depends on it server-side.
    state = checklist_intake.checklist_state(db, app_row)
    labels = [i["label"].lower() for i in state["items"]]
    assert not any("payment" in l for l in labels)


# ---------- Part 7: real portal options for missing select answers -----------

def test_missing_select_questions_carry_harvested_portal_options(db):
    """A missing select answer must reach the applicant WITH the portal's real
    option list — never a blank 'choose from list' field."""
    from app.adapter_factory import models as fm
    from app.adapter_factory.compiler import CompiledFlow
    from app.adapter_factory.runtime import FlowRunner

    class OptionDriver:
        def list_options(self, selector, max_options=300):
            assert selector == "#basic_ttcdNcCuaKhau"
            return {"ok": True, "options": ["Noi Bai Int Airport", "Tan Son Nhat Int Airport"]}

    nodes = [
        {"node_id": "pick_entry", "action": "SELECT_SEARCH",
         "selector": "#basic_ttcdNcCuaKhau", "input_source": "entry_checkpoint",
         "allowed_hostname": "evisa.gov.vn", "mandatory": True,
         "question": {"question": "Through which border checkpoint will you enter?",
                      "kind": "select", "format": "choose from list"}},
        {"node_id": "done", "action": "COMPLETE", "allowed_hostname": "evisa.gov.vn"},
    ]
    execution = fm.AdapterExecution(org_id="org1", application_id="c-opt",
                                    candidate_id="cand", candidate_version=1,
                                    tier="sandbox", status="running")
    db.add(execution); db.commit()
    compiled = CompiledFlow(nodes, [n["node_id"] for n in nodes],
                            {"allowed_hostnames": ["evisa.gov.vn"]})
    runner = FlowRunner(db, execution=execution, compiled=compiled,
                        driver=OptionDriver(), case_answers={})
    res = runner.run()
    assert res["status"] == "paused_applicant_action"
    q = {x["key"]: x for x in res["questions"]}["entry_checkpoint"]
    assert q["options"] == ["Noi Bai Int Airport", "Tan Son Nhat Int Airport"]


# ---------- Part 7/15: applicant's checklist submission beats the classifier --

def test_checklist_submission_overrides_classifier_doc_type(client, db):
    """A file explicitly submitted against the 'photo' requirement IS the
    photo for portal uploads — even when OCR classified it 'document' and no
    OCR approval exists (a photo has no fields to approve)."""
    from app.portal.released_flow import ReleasedFlowDriver
    from app.visa_snapshot.models import CaseRouteGuidance
    case = _new_case(client)
    doc = models.StoredDocument(org_id="org1", application_id=case["id"],
                                name="photo.jpg", mime="image/jpeg",
                                size_bytes=4, doc_type="document", approved=False)
    db.add(doc); db.flush()
    db.add(models.DocumentBlob(document_id=doc.id, org_id="org1",
                               mime="image/jpeg", content=b"\xff\xd8\xff\xd9"))
    db.add(CaseRouteGuidance(case_id=case["id"], org_id="org1",
                             guidance={"guidance": {}}, checklist=[
                                 {"id": "photo", "label": "digital passport photo",
                                  "kind": "document", "required": True,
                                  "satisfied_by": ["photo"]}]))
    db.add(models.ChecklistSubmission(org_id="org1", application_id=case["id"],
                                      item_id="photo", document_id=doc.id,
                                      status="submitted", match_verdict="uncertain",
                                      detected_type="document",
                                      confirmed_by_applicant=True))
    db.commit()
    drv = ReleasedFlowDriver.__new__(ReleasedFlowDriver)
    drv.db = db
    drv.app_row = db.get(models.VisaApplication, case["id"])
    drv._tmp_files = []
    docs = drv._documents()
    photo = next((d for d in docs if d["doc_type"] == "photo"), None)
    assert photo is not None, "checklist submission must impose doc_type photo"
    assert photo["path"], "explicit submission stands in for the OCR approval gate"
    drv._cleanup_tmp()


# ---------- Part 15: PDF documents become JPEGs for image-only portals -------

def _tiny_pdf(tmp_path):
    """A real one-page PDF wrapping a small photo (Pillow writes the PDF)."""
    from PIL import Image
    img = Image.new("RGB", (320, 400), (200, 180, 160))
    p = tmp_path / "photo.pdf"
    img.save(p, format="PDF")
    return str(p)


def test_pdf_first_page_converts_to_real_jpeg(tmp_path):
    from app.providers.pdf_image import pdf_first_page_jpeg
    out = pdf_first_page_jpeg(_tiny_pdf(tmp_path))
    assert out and out.endswith(".jpg")
    try:
        with open(out, "rb") as fh:
            magic = fh.read(3)
        assert magic == b"\xff\xd8\xff"          # a genuine JPEG
        from PIL import Image
        with Image.open(out) as img:
            assert img.size[0] > 0
    finally:
        import os as _os
        _os.unlink(out)


def test_upload_converts_pdf_when_portal_accepts_images_only(tmp_path):
    from app.adapter_factory.live_driver import BrowserbasePageDriver

    uploaded = {}

    class FakePage:
        url = "https://evisa.gov.vn/"

        def eval_on_selector(self, selector, js):
            return ".jpg,.jpeg,.png"             # the portal's real constraint

        def set_input_files(self, selector, path, timeout=0):
            with open(path, "rb") as fh:
                uploaded["magic"] = fh.read(3)
            uploaded["path"] = path

        def on(self, *_a, **_k):
            pass

    driver = BrowserbasePageDriver(FakePage(), allowed_hostnames=["evisa.gov.vn"])
    res = driver.upload("#basic_anhMat", _tiny_pdf(tmp_path))
    assert res["ok"] is True and res["converted_to_image"] is True
    assert uploaded["path"].endswith(".jpg")
    assert uploaded["magic"] == b"\xff\xd8\xff"  # the portal received a JPEG
    import os as _os
    assert not _os.path.exists(uploaded["path"])  # temp JPEG never lingers


def test_upload_keeps_pdf_when_portal_declares_pdf_support(tmp_path):
    from app.adapter_factory.live_driver import BrowserbasePageDriver

    uploaded = {}

    class FakePage:
        url = "https://evisa.gov.vn/"

        def eval_on_selector(self, selector, js):
            return ".pdf,.jpg,.png"

        def set_input_files(self, selector, path, timeout=0):
            uploaded["path"] = path

        def on(self, *_a, **_k):
            pass

    driver = BrowserbasePageDriver(FakePage(), allowed_hostnames=["evisa.gov.vn"])
    src = _tiny_pdf(tmp_path)
    res = driver.upload("#basic_anhHoChieu", src)
    assert res["ok"] is True and res["converted_to_image"] is False
    assert uploaded["path"] == src               # original PDF, untouched


# ---------- envelope binding (Continue diagnosis) ----------------------------

def test_signature_binds_to_the_prepared_envelope_not_the_newest(client, db):
    case = _new_case(client)
    p1 = client.post(f"/cases/{case['id']}/authorization/prepare", headers=AUTH,
                     json={"max_fee_cents": 5000, "currency": "USD"}).json()
    p2 = client.post(f"/cases/{case['id']}/authorization/prepare", headers=AUTH,
                     json={"max_fee_cents": 9900, "currency": "USD"}).json()
    assert p1["envelope_id"] != p2["envelope_id"]
    r = client.post(f"/cases/{case['id']}/authorization/sign", headers=AUTH, json={
        "document_hash": p1["document_hash"], "consent_given": True,
        "intent_confirmed": True, "signature_method": "typed",
        "signature_value": "Queue Test", "step_up_token": p1["step_up_token"],
        "auth_method": "email_otp", "envelope_id": p1["envelope_id"]})
    assert r.status_code == 200, r.text
    env1 = db.get(models.AuthorizationEnvelope, p1["envelope_id"])
    env2 = db.get(models.AuthorizationEnvelope, p2["envelope_id"])
    db.refresh(env1); db.refresh(env2)
    assert env1.status == "completed"
    assert env2.status == "prepared"           # the newer row never swapped in
    # The operative workflow authorization is the SIGNED envelope's terms.
    wf = service.load_workflow(db, case["id"])
    assert wf.authorization["max_fee_cents"] == 5000
