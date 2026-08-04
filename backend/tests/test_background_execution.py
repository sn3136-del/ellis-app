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

def test_no_contact_confirmation_gate_before_portal_start(client, live_route):
    # Product decision 2026-07-27: the intake email IS the portal contact —
    # signing must never 409 on a separate contact-confirmation step.
    case = _new_case(client)
    r = client.post(f"/cases/{case['id']}/signals/sign_authorization", headers=AUTH, json={})
    assert r.status_code == 200
    assert r.json().get("queued") is True


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


# ---------- rejected answers fall back to the portal's FULL option list ------

def test_empty_filtered_options_reharvest_full_list_instead_of_failing(db):
    """'tourism' filtering the travel-purpose list to nothing must ask the
    applicant with the portal's real (unfiltered) choices — never fail-closed
    into terminal manual review."""
    from app.adapter_factory import models as fm
    from app.adapter_factory.compiler import CompiledFlow
    from app.adapter_factory.runtime import FlowRunner

    class FilteringDriver:
        def select_search(self, selector, value):
            return {"ok": False, "code": "NO_OPTIONS", "options": []}

        def list_options(self, selector, max_options=300):
            return {"ok": True, "options": ["Du lịch (Tourist)", "Business"]}

    nodes = [
        {"node_id": "fill_purpose", "action": "SELECT_SEARCH",
         "selector": "#basic_ttcdMucDich", "input_source": "travel_purpose",
         "allowed_hostname": "evisa.gov.vn", "mandatory": True},
        {"node_id": "done", "action": "COMPLETE", "allowed_hostname": "evisa.gov.vn"},
    ]
    execution = fm.AdapterExecution(org_id="org1", application_id="c-opt2",
                                    candidate_id="cand", candidate_version=1,
                                    tier="sandbox", status="running")
    db.add(execution); db.commit()
    runner = FlowRunner(db, execution=execution,
                        compiled=CompiledFlow(nodes, [n["node_id"] for n in nodes],
                                              {"allowed_hostnames": ["evisa.gov.vn"]}),
                        driver=FilteringDriver(),
                        case_answers={"travel_purpose": "tourism"})
    res = runner.run()
    assert res["status"] == "paused_applicant_action"
    q = {x["key"]: x for x in res["questions"]}["travel_purpose"]
    assert q["options"] == ["Du lịch (Tourist)", "Business"]


# ---------- portal questions pause cleanly at EVERY stage --------------------

def test_upload_stage_question_pauses_instead_of_manual_review():
    """A dynamic question raised during document upload (or fee discovery)
    must pause for the applicant exactly like one raised during form filling
    — the exact bug that dead-ended the live run: _pause() returned None, the
    blocked-step check read it as an ordinary failure, and RECOVERABLE_FAILURE
    had no legal way back to DOCUMENT_UPLOAD_PENDING → terminal manual review."""
    class QuestionOnUpload:
        def upload_document(self, **_kw):
            return {"ok": False, "code": "ADDITIONAL_INFORMATION_REQUIRED",
                    "questions": [{"key": "vietnam_address", "kind": "text",
                                   "question": "What is your address in Vietnam?",
                                   "mandatory": True}]}

        def detach(self):
            pass

    wf = _wf(QuestionOnUpload(), "DOCUMENT_UPLOAD_PENDING")
    wf.documents = [{"name": "photo.jpg", "mime": "image/jpeg", "size_bytes": 4}]
    wf.session_ref = vault.store("session-token")["ref"]
    wf.application_id = "app-1"
    wf.start()
    assert wf.machine.state == "DOCUMENT_UPLOAD_PENDING"      # not manual review
    assert wf.pending["handoff"] == "additional_information"
    assert wf.pending["questions"][0]["key"] == "vietnam_address"
    # No failure-loop pollution: the pause never routed through
    # RECOVERABLE_FAILURE at all.
    assert all(h["state"] != "RECOVERABLE_FAILURE" for h in wf.machine.history)


def test_recoverable_failure_can_resume_document_upload():
    from app.statemachine import can_transition
    assert can_transition("RECOVERABLE_FAILURE", "DOCUMENT_UPLOAD_PENDING")
    assert can_transition("RECOVERABLE_FAILURE", "FEE_DISCOVERY_PENDING")


# ---------- one batch of questions: pre-validated selects, deferred lists ----

def _flow_runner(db, nodes, driver, answers, app_id="c-batch"):
    from app.adapter_factory import models as fm
    from app.adapter_factory.compiler import CompiledFlow
    from app.adapter_factory.runtime import FlowRunner
    execution = fm.AdapterExecution(org_id="org1", application_id=app_id,
                                    candidate_id="cand", candidate_version=1,
                                    tier="sandbox", status="running")
    db.add(execution); db.commit()
    return FlowRunner(db, execution=execution,
                      compiled=CompiledFlow(nodes, [n["node_id"] for n in nodes],
                                            {"allowed_hostnames": ["evisa.gov.vn"]}),
                      driver=driver, case_answers=answers)


def test_first_batch_prevalidates_selects_and_defers_dependent_lists(db):
    """The applicant is asked ONCE, and only about what they can actually
    answer. Ellis fills every field it has an answer for; a stored answer the
    portal REFUSES comes back with the portal's own list; a dependent list the
    portal cannot show yet is never asked with an empty dropdown."""
    class Driver:
        def __init__(self):
            self.filled = []

        def list_options(self, selector, max_options=300):
            if selector == "#purpose":
                return {"ok": True, "options": ["Du lịch (Tourist)", "Business"]}
            if selector == "#sex":
                return {"ok": True, "options": ["Male", "Female"]}
            return {"ok": True, "options": []}     # dependent ward: not yet

        def select_search(self, selector, value):
            # The portal's real list decides. "tourism" is not on it.
            if selector == "#purpose":
                return {"ok": False, "code": "NO_OPTIONS", "options": []}
            self.filled.append((selector, value))
            return {"ok": True, "shown": value}

        def fill(self, selector, value):
            self.filled.append((selector, value))
            return {"ok": True}

    nodes = [
        {"node_id": "fill_religion", "action": "FILL_NON_SENSITIVE",
         "selector": "#religion", "input_source": "religion",
         "allowed_hostname": "evisa.gov.vn", "mandatory": True},
        {"node_id": "pick_sex", "action": "SELECT_SEARCH", "selector": "#sex",
         "input_source": "sex", "allowed_hostname": "evisa.gov.vn", "mandatory": True},
        {"node_id": "pick_purpose", "action": "SELECT_SEARCH", "selector": "#purpose",
         "input_source": "travel_purpose", "allowed_hostname": "evisa.gov.vn",
         "mandatory": True},
        {"node_id": "pick_ward", "action": "SELECT_SEARCH", "selector": "#ward",
         "input_source": "vietnam_ward", "allowed_hostname": "evisa.gov.vn",
         "mandatory": True},
        {"node_id": "done", "action": "COMPLETE", "allowed_hostname": "evisa.gov.vn"},
    ]
    drv = Driver()
    runner = _flow_runner(db, nodes, drv, {"sex": "Female", "travel_purpose": "tourism"})
    res = runner.run()
    assert res["status"] == "paused_applicant_action"
    keys = {q["key"]: q for q in res["questions"]}
    # The answer that matched was COMMITTED, not asked about.
    assert ("#sex", "Female") in drv.filled
    assert "sex" not in keys
    # The one the portal refuses comes back with the portal's own words.
    assert keys["travel_purpose"]["options"] == ["Du lịch (Tourist)", "Business"]
    assert "tourism" in keys["travel_purpose"]["why"]
    # A dependent list with nothing to offer is not asked with an empty box.
    assert "vietnam_ward" not in keys


def test_a_missing_field_does_not_stop_the_ones_after_it(db):
    """Thailand stopped dead at a blank Occupation with Gender and the whole
    trip section still empty. Everything Ellis CAN fill is filled first; the
    gaps are asked together at the advance."""
    class Driver:
        def __init__(self):
            self.filled = []

        def fill(self, selector, value):
            self.filled.append((selector, value))
            return {"ok": True}

    nodes = [
        {"node_id": "fill_surname", "action": "FILL_NON_SENSITIVE",
         "selector": "#surname", "input_source": "surname",
         "allowed_hostname": "evisa.gov.vn", "mandatory": True},
        {"node_id": "fill_occupation", "action": "FILL_NON_SENSITIVE",
         "selector": "#occupation", "input_source": "occupation",
         "allowed_hostname": "evisa.gov.vn", "mandatory": True},
        {"node_id": "fill_sex", "action": "FILL_NON_SENSITIVE",
         "selector": "#sex", "input_source": "sex",
         "allowed_hostname": "evisa.gov.vn", "mandatory": True},
        {"node_id": "done", "action": "COMPLETE", "allowed_hostname": "evisa.gov.vn"},
    ]
    drv = Driver()
    runner = _flow_runner(db, nodes, drv, {"surname": "CAO", "sex": "M"})
    res = runner.run()
    assert res["status"] == "paused_applicant_action"
    assert [s for s, _ in drv.filled] == ["#surname", "#sex"], \
        "the fields Ellis could fill must all be filled before it asks"
    assert [q["key"] for q in res["questions"]] == ["occupation"]


def test_portal_dropped_value_is_repaired_silently_not_re_asked(db):
    """The live loop: Ellis fills the Vietnam address, the province/ward
    selects that run AFTER it clear the field, and the portal then reports
    'please enter …'. Ellis must REFILL from the answer it already holds and
    advance — re-asking the applicant for a value it has is the loop."""
    state = {"address": "138/1300A St. 26/3", "cleared": True, "clicks": 0,
             "refills": []}

    class DroppingPortal:
        def fill(self, selector, value):
            state["refills"].append((selector, value))
            # The FIRST fill is wiped by the province/ward selects that run
            # after it (the real portal behavior); the repair fill sticks.
            state["cleared"] = len(state["refills"]) == 1
            return {"ok": True}

        def read_value(self, selector):
            return {"ok": True, "value": "" if state["cleared"] else state["address"]}

        def click(self, selector):
            state["clicks"] += 1
            if state["cleared"]:
                return {"ok": False, "code": "TIMEOUT"}
            return {"ok": True}

        def read_validation_errors(self):
            if state["cleared"]:
                return {"ok": True, "messages": ["Please enter Residential address in Viet Nam"],
                        "fields": [{"id": "basic_address", "label": "Address",
                                    "message": "Please enter Residential address in Viet Nam"}]}
            return {"ok": True, "messages": [], "fields": []}

    nodes = [
        {"node_id": "fill_address", "action": "FILL_NON_SENSITIVE",
         "selector": "#basic_address", "input_source": "vietnam_address",
         "allowed_hostname": "evisa.gov.vn", "mandatory": True},
        {"node_id": "next", "action": "CLICK", "selector": "#next",
         "allowed_hostname": "evisa.gov.vn"},
        {"node_id": "done", "action": "COMPLETE", "allowed_hostname": "evisa.gov.vn"},
    ]
    runner = _flow_runner(db, nodes, DroppingPortal(),
                          {"vietnam_address": state["address"]}, app_id="c-repair")
    res = runner.run()
    assert res["status"] == "completed"            # advanced, never paused
    assert ("#basic_address", state["address"]) in state["refills"]
    assert state["clicks"] == 2                    # refused once, then accepted


def test_click_that_already_advanced_is_not_retried_forever(db):
    """The live dead end: the portal HAD moved past the form, so the 'Next'
    button was gone and every retry read as a timeout with no validation
    error — four failures into terminal manual review. When the form's own
    fields are no longer on screen, the step already succeeded."""
    class AdvancedPortal:
        def fill(self, selector, value):
            return {"ok": True}

        def click(self, selector):
            return {"ok": False, "code": "TIMEOUT"}   # the button is gone

        def read_validation_errors(self):
            return {"ok": True, "messages": [], "fields": []}

        def is_visible(self, selector):
            return {"ok": True, "visible": False}     # the form page is gone

    nodes = [
        {"node_id": "fill_name", "action": "FILL_NON_SENSITIVE", "selector": "#name",
         "input_source": "full_name", "allowed_hostname": "evisa.gov.vn"},
        {"node_id": "next", "action": "CLICK", "selector": "#next",
         "allowed_hostname": "evisa.gov.vn"},
        {"node_id": "done", "action": "COMPLETE", "allowed_hostname": "evisa.gov.vn"},
    ]
    runner = _flow_runner(db, nodes, AdvancedPortal(), {"full_name": "A"},
                          app_id="c-advanced")
    assert runner.run()["status"] == "completed"


def test_intercepted_click_falls_back_to_a_forced_click(db):
    """An overlay swallowing a reversible navigation click is retried with a
    forced click before anything fails — but only when the form is still on
    screen (so a genuinely-advanced page is never re-clicked)."""
    calls = {"force": 0}

    class OverlayPortal:
        def fill(self, selector, value):
            return {"ok": True}

        def click(self, selector):
            return {"ok": False, "code": "TIMEOUT"}

        def read_validation_errors(self):
            return {"ok": True, "messages": [], "fields": []}

        def is_visible(self, selector):
            return {"ok": True, "visible": True}      # still on the form

        def force_click(self, selector):
            calls["force"] += 1
            return {"ok": True, "method": "force"}

    nodes = [
        {"node_id": "fill_name", "action": "FILL_NON_SENSITIVE", "selector": "#name",
         "input_source": "full_name", "allowed_hostname": "evisa.gov.vn"},
        {"node_id": "next", "action": "CLICK", "selector": "#next",
         "allowed_hostname": "evisa.gov.vn"},
        {"node_id": "done", "action": "COMPLETE", "allowed_hostname": "evisa.gov.vn"},
    ]
    runner = _flow_runner(db, nodes, OverlayPortal(), {"full_name": "A"},
                          app_id="c-overlay")
    assert runner.run()["status"] == "completed"
    assert calls["force"] == 1


def test_irreversible_click_never_force_clicks_or_assumes_advance(db):
    """The submit node keeps its strict contract: a timeout is UNCERTAIN —
    never forced, never assumed to have advanced."""
    forced = {"n": 0}

    class SubmitPortal:
        def click(self, selector):
            return {"ok": False, "code": "TIMEOUT"}

        def force_click(self, selector):
            forced["n"] += 1
            return {"ok": True}

        def is_visible(self, selector):
            return {"ok": True, "visible": False}

        def official_state(self):
            return {"known": False}

    nodes = [
        {"node_id": "submit", "action": "CLICK", "selector": "#submit",
         "irreversibility": "irreversible", "allowed_hostname": "evisa.gov.vn",
         "success_evidence": [{"kind": "network", "category": "submission_accepted"}]},
        {"node_id": "done", "action": "COMPLETE", "allowed_hostname": "evisa.gov.vn"},
    ]
    runner = _flow_runner(db, nodes, SubmitPortal(), {}, app_id="c-irrev")
    assert runner.run()["status"] == "outcome_uncertain"
    assert forced["n"] == 0


def test_captcha_handoff_skipped_when_no_challenge_is_on_the_page(db):
    """The declared CAPTCHA node fires only when a challenge actually exists —
    the live run sent the applicant hunting for a CAPTCHA that was not there."""
    class NoChallenge:
        def captcha_state(self, highlight=False):
            return {"ok": True, "present": False}

        def read_validation_errors(self):
            return {"ok": True, "messages": [], "fields": []}

    nodes = [
        {"node_id": "captcha", "action": "APPLICANT_HANDOFF", "handoff_kind": "captcha",
         "allowed_hostname": "evisa.gov.vn"},
        {"node_id": "done", "action": "COMPLETE", "allowed_hostname": "evisa.gov.vn"},
    ]
    runner = _flow_runner(db, nodes, NoChallenge(), {}, app_id="c-nocaptcha")
    assert runner.run()["status"] == "completed"


def test_captcha_handoff_pauses_and_highlights_when_present(db):
    highlighted = {"n": 0}

    class Challenge:
        def captcha_state(self, highlight=False):
            if highlight:
                highlighted["n"] += 1
            return {"ok": True, "present": True, "kind": ".g-recaptcha"}

    nodes = [
        {"node_id": "captcha", "action": "APPLICANT_HANDOFF", "handoff_kind": "captcha",
         "allowed_hostname": "evisa.gov.vn"},
        {"node_id": "done", "action": "COMPLETE", "allowed_hostname": "evisa.gov.vn"},
    ]
    runner = _flow_runner(db, nodes, Challenge(), {}, app_id="c-captcha")
    res = runner.run()
    assert res["status"] == "paused_applicant_action"
    assert res["handoff_kind"] == "captcha"
    assert highlighted["n"] == 1               # scrolled + outlined for the applicant


def test_unmapped_portal_fields_become_a_secure_window_handoff(db):
    """Portal fields the released flow has no nodes for (its inline legal
    declaration, an expenses section) go to the applicant in the secure
    window with the portal's own wording — never a silent failure loop."""
    class DeclarationPortal:
        def fill(self, selector, value):
            return {"ok": True}

        def click(self, selector):
            return {"ok": False, "code": "TIMEOUT"}

        def is_visible(self, selector):
            return {"ok": True, "visible": True}

        def read_validation_errors(self):
            return {"ok": True, "messages": [],
                    "fields": [{"id": "declare_1", "label": "Declaration",
                                "message": "Please confirm the declaration"}]}

    nodes = [
        {"node_id": "fill_name", "action": "FILL_NON_SENSITIVE", "selector": "#name",
         "input_source": "full_name", "allowed_hostname": "evisa.gov.vn"},
        {"node_id": "next", "action": "CLICK", "selector": "#next",
         "allowed_hostname": "evisa.gov.vn"},
        {"node_id": "done", "action": "COMPLETE", "allowed_hostname": "evisa.gov.vn"},
    ]
    runner = _flow_runner(db, nodes, DeclarationPortal(), {"full_name": "A"},
                          app_id="c-declare")
    res = runner.run()
    assert res["status"] == "paused_applicant_action"
    assert res["handoff_kind"] == "portal_form"
    assert "Declaration — Please confirm the declaration" in res["portal_messages"]


def test_already_correct_field_is_not_refilled_on_resume(db):
    """Resume speed: a field the portal already holds with the exact value is
    skipped instead of retyped."""
    touched = []

    class Portal:
        def read_value(self, selector):
            return {"ok": True, "value": "Nguyen Van A"}

        def fill(self, selector, value):
            touched.append(selector)
            return {"ok": True}

    nodes = [
        {"node_id": "fill_name", "action": "FILL_NON_SENSITIVE", "selector": "#name",
         "input_source": "full_name", "allowed_hostname": "evisa.gov.vn"},
        {"node_id": "done", "action": "COMPLETE", "allowed_hostname": "evisa.gov.vn"},
    ]
    runner = _flow_runner(db, nodes, Portal(), {"full_name": "Nguyen Van A"},
                          app_id="c-skip")
    assert runner.run()["status"] == "completed"
    assert touched == []                            # nothing retyped


def test_portal_validation_errors_become_questions_with_rewind(db):
    """A reversible 'Next' the portal refuses while showing its own field
    errors pauses with THOSE fields as questions (portal message verbatim)
    and rewinds to the first flagged field for the refill."""
    class Driver:
        def fill(self, selector, value):
            return {"ok": True}

        def click(self, selector):
            return {"ok": False, "code": "TIMEOUT"}

        def read_validation_errors(self):
            return {"ok": True, "messages": ["Email không hợp lệ"],
                    "fields": [{"id": "basic_email", "label": "Email",
                                "message": "Email không hợp lệ"}]}

    nodes = [
        {"node_id": "fill_email", "action": "FILL_NON_SENSITIVE",
         "selector": "#basic_email", "input_source": "email",
         "allowed_hostname": "evisa.gov.vn", "mandatory": True},
        {"node_id": "next", "action": "CLICK", "selector": "#next",
         "allowed_hostname": "evisa.gov.vn"},
        {"node_id": "done", "action": "COMPLETE", "allowed_hostname": "evisa.gov.vn"},
    ]
    runner = _flow_runner(db, nodes, Driver(), {"email": "broken@@example"},
                          app_id="c-valid")
    res = runner.run()
    assert res["status"] == "paused_applicant_action"
    q = {x["key"]: x for x in res["questions"]}["email"]
    assert "Email không hợp lệ" in q["why"]        # the portal's own words
    assert runner.execution.current_node == "fill_email"   # refill on resume


# ---------- reversible manual-review dead ends are applicant-releasable ------

def _strand_in_manual_review(client, db):
    case = _new_case(client)
    exec_row = models.WorkflowExecution(
        application_id=case["id"], state="MANUAL_REVIEW_REQUIRED",
        snapshot={"inputs": {}}, pending=None,
        history=[{"state": "APPLICATION_FILLING", "reason": ""},
                 {"state": "RECOVERABLE_FAILURE", "reason": "NO_OPTIONS"},
                 {"state": "MANUAL_REVIEW_REQUIRED", "reason": "repeated recoverable failures"}])
    db.add(exec_row)
    app_row = db.get(models.VisaApplication, case["id"])
    app_row.state = "MANUAL_REVIEW_REQUIRED"
    db.commit()
    return case


def test_reversible_manual_review_is_released_by_applicant_retry(client, live_route, db):
    case = _strand_in_manual_review(client, db)
    r = client.post(f"/cases/{case['id']}/portal/retry", headers=AUTH)
    assert r.status_code == 200 and r.json()["queued"] is True
    db.expire_all()
    assert db.get(models.VisaApplication, case["id"]).state == "APPLICATION_FILLING"
    run = portal_queue.latest_run(db, case["id"])
    assert run is not None and run.status == "queued"


def test_manual_review_with_irreversible_evidence_is_never_released(client, live_route, db):
    case = _strand_in_manual_review(client, db)
    db.add(models.SubmissionConfirmation(application_id=case["id"],
                                         reference_no="REF-1"))
    db.commit()
    app_row = db.get(models.VisaApplication, case["id"])
    assert portal_queue.manual_review_releasable(db, app_row) is False
    r = client.post(f"/cases/{case['id']}/portal/retry", headers=AUTH)
    assert r.status_code == 409
    db.expire_all()
    assert db.get(models.VisaApplication, case["id"]).state == "MANUAL_REVIEW_REQUIRED"


def test_a_case_with_no_pending_and_no_run_can_always_be_resumed(client, live_route, db):
    """A pause that was cleared (a reset, a voided confirmation) must never
    leave the applicant with no way forward."""
    case = _new_case(client)
    pr = client.get(f"/cases/{case['id']}/progress", headers=AUTH).json()
    assert pr["resume_available"] is True
    _confirm_contact(client, case["id"])
    client.post(f"/cases/{case['id']}/start", headers=AUTH)
    busy = client.get(f"/cases/{case['id']}/progress", headers=AUTH).json()
    assert busy["resume_available"] is False        # a run is already working


# ---------- the secure window shows the real page: restore + fee read -------

def test_restore_portal_rebuilds_view_and_reads_fee_for_confirmation():
    """After a session loss the secure window must show the real form, and a
    fee READ off the restored page replaces the type-it-yourself pause with
    an approve-this-exact-amount pause — read, never invented."""
    class RestoringDriver:
        def restore_view(self, **_kw):
            return {"ok": True, "fee": {
                "ok": True, "amount": 2500, "currency": "USD",
                "display": "25.00 USD", "government_fee_cents": 2500,
                "service_fee_cents": 0, "payee": "Vietnam Immigration Department",
                "source": "portal_page_read"}}

    wf = _wf(RestoringDriver(), "PAYMENT_APPROVAL_REQUIRED")
    wf.pending = {"state": "PAYMENT_APPROVAL_REQUIRED",
                  "reason": "Confirm the exact fee shown by the official portal.",
                  "handoff": "fee_confirmation", "live_view": None}
    wf.restore_portal()
    assert wf.machine.state == "PAYMENT_APPROVAL_REQUIRED"    # state untouched
    assert wf.fee["amount"] == 2500
    assert wf.fee["source"] == "portal_page_read"
    assert wf.pending["handoff"] == "payment_approval"        # now approve-exact
    assert wf.pending["fee"]["display"] == "25.00 USD"


def test_restore_rewinds_a_blank_session_instead_of_parking_on_the_cursor(db):
    """The live failure: the fresh session was blank, but the flow cursor said
    'you are at the payment handoff', so restore hit that boundary instantly
    and navigated nothing — the applicant kept staring at about:blank."""
    from app.portal.released_flow import ReleasedFlowDriver

    class BlankPageDriver:
        def current_url(self):
            return {"ok": True, "url": "about:blank"}

    class Execution:
        current_node = "payment_handoff"

    drv = ReleasedFlowDriver.__new__(ReleasedFlowDriver)
    execution = Execution()
    advanced = {"ran": False}

    class FakeDb:
        def commit(self):
            pass

    drv.db = FakeDb()
    drv._ensure_live = lambda: BlankPageDriver()
    drv._execution = lambda: execution
    drv._hosts = lambda: ["evisa.gov.vn"]
    drv._passed_irreversible = lambda n: False
    drv._fee_from_page_text = lambda: None

    def fake_advance(stop_before=None):
        advanced["ran"] = True
        return {"status": "boundary"}

    drv._advance = fake_advance
    drv._result_from = lambda res, ok_statuses=(): {"ok": True}
    out = drv.restore_view()
    assert out["ok"] is True
    assert execution.current_node == ""     # reversible cursor rewound
    assert advanced["ran"] is True          # and the flow actually re-ran


def test_restore_refuses_to_replay_after_an_irreversible_step(db):
    from app.portal.released_flow import ReleasedFlowDriver

    class BlankPageDriver:
        def current_url(self):
            return {"ok": True, "url": "about:blank"}

    class Execution:
        current_node = "submit"

    drv = ReleasedFlowDriver.__new__(ReleasedFlowDriver)
    drv.db = type("D", (), {"commit": lambda self: None})()
    drv._ensure_live = lambda: BlankPageDriver()
    drv._execution = lambda: Execution()
    drv._hosts = lambda: ["evisa.gov.vn"]
    drv._passed_irreversible = lambda n: True
    drv._advance = lambda stop_before=None: (_ for _ in ()).throw(
        AssertionError("must not replay past an irreversible step"))
    out = drv.restore_view()
    assert out["ok"] is False and out["code"] == "OUTCOME_UNCERTAIN"


def test_concurrent_session_opens_keep_exactly_one(client, db, monkeypatch):
    """Two dialogs mounting at once each found no session and each created
    one — the case must keep exactly one isolated session."""
    from app.providers import browser as bb
    case = _new_case(client)
    made = []

    def fake_create():
        made.append(len(made))
        return {"id": f"s-{len(made)}", "mode": "browserbase", "connect_url": "ws://x"}

    monkeypatch.setattr(bb, "create_session", fake_create)
    monkeypatch.setattr(bb, "session_alive", lambda sid: True)
    monkeypatch.setattr(bb, "close_session", lambda sid: None)
    # Simulate the race: a second create that also saw no open row.
    db.add(models.BrowserSession(org_id="org1", application_id=case["id"],
                                 provider_session_id="s-early", mode="browserbase",
                                 status="open"))
    db.commit()
    r = client.post(f"/cases/{case['id']}/browser-session", headers=AUTH)
    assert r.status_code == 200
    open_rows = db.execute(select(models.BrowserSession).where(
        models.BrowserSession.application_id == case["id"],
        models.BrowserSession.status == "open")).scalars().all()
    assert len(open_rows) == 1


def test_driver_and_applicant_window_always_agree_on_the_session(client, db, monkeypatch):
    """The live failure: the applicant's window watched one open session
    while the driver opened and drove another, so the window stayed blank
    forever. Exactly one open session per case, and one shared definition
    of which it is."""
    from app.providers import browser as bb
    from app.portal_store import current_browser_session, retire_other_sessions
    case = _new_case(client)
    monkeypatch.setattr(bb, "session_alive", lambda sid: True)
    monkeypatch.setattr(bb, "close_session", lambda sid: None)
    # Two rows open at once (the race that actually happened).
    for sid in ("older-session", "newer-session"):
        db.add(models.BrowserSession(org_id="org1", application_id=case["id"],
                                     provider_session_id=sid, mode="browserbase",
                                     status="open"))
        db.commit()
    picked = current_browser_session(db, case["id"])
    assert picked.provider_session_id == "newer-session"   # deterministic
    retire_other_sessions(db, case["id"], picked.id)
    open_rows = db.execute(select(models.BrowserSession).where(
        models.BrowserSession.application_id == case["id"],
        models.BrowserSession.status == "open")).scalars().all()
    assert [r.provider_session_id for r in open_rows] == ["newer-session"]
    # And the endpoint the applicant's window uses returns that same one.
    r = client.post(f"/cases/{case['id']}/browser-session", headers=AUTH)
    assert r.json()["id"] == picked.id


def test_on_demand_fee_read_binds_the_amount_from_the_current_page():
    """The applicant walks the portal to its payment step, presses 'Read the
    fee from the page', and the READ amount becomes a simple approval."""
    class PageWithFee:
        def read_current_fee(self, **_kw):
            return {"ok": True, "fee": {
                "ok": True, "amount": 2500, "currency": "USD",
                "display": "25.00 USD", "government_fee_cents": 2500,
                "service_fee_cents": 0, "payee": "Vietnam Immigration Department",
                "source": "portal_page_read"}}

    wf = _wf(PageWithFee(), "PAYMENT_APPROVAL_REQUIRED")
    wf.pending = {"state": "PAYMENT_APPROVAL_REQUIRED", "reason": "r",
                  "handoff": "fee_confirmation", "live_view": None}
    wf.read_fee()
    assert wf.fee["amount"] == 2500 and wf.fee["source"] == "portal_page_read"
    assert wf.pending["handoff"] == "payment_approval"


def test_on_demand_fee_read_stays_honest_when_the_page_shows_none():
    class NoFeePage:
        def read_current_fee(self, **_kw):
            return {"ok": False, "code": "FEE_NOT_DISPLAYED"}

    wf = _wf(NoFeePage(), "PAYMENT_APPROVAL_REQUIRED")
    wf.pending = {"state": "PAYMENT_APPROVAL_REQUIRED", "reason": "r",
                  "handoff": "fee_confirmation", "live_view": None}
    wf.read_fee()
    assert wf.fee is None                                   # nothing invented
    assert wf.pending["handoff"] == "fee_confirmation"


def test_read_fee_endpoint_queues_a_run(client, live_route, db):
    case = _new_case(client)
    r = client.post(f"/cases/{case['id']}/portal/read-fee", headers=AUTH)
    assert r.status_code == 200 and r.json()["queued"] is True
    run = portal_queue.latest_run(db, case["id"])
    assert run.signal_name == "read_fee"


def test_restore_portal_preserves_the_pause_when_no_fee_is_readable():
    class RestoreOnly:
        def restore_view(self, **_kw):
            return {"ok": True}

    wf = _wf(RestoreOnly(), "PAYMENT_APPROVAL_REQUIRED")
    wf.pending = {"state": "PAYMENT_APPROVAL_REQUIRED", "reason": "r",
                  "handoff": "fee_confirmation", "live_view": None}
    wf.restore_portal()
    assert wf.pending["handoff"] == "fee_confirmation"        # unchanged
    assert wf.fee is None                                     # nothing invented


def test_restore_endpoint_queues_a_run_and_fresh_sessions_say_so(client, live_route, db, monkeypatch):
    from app.providers import browser as bb
    case = _new_case(client)
    monkeypatch.setattr(bb, "create_session",
                        lambda: {"id": "s-1", "mode": "browserbase", "connect_url": "ws://x"})
    monkeypatch.setattr(bb, "session_alive", lambda sid: True)
    r = client.post(f"/cases/{case['id']}/browser-session", headers=AUTH)
    assert r.json()["fresh"] is True                          # blank until restored
    r2 = client.post(f"/cases/{case['id']}/browser-session", headers=AUTH)
    assert r2.json()["fresh"] is False                        # reused, not blank
    rr = client.post(f"/cases/{case['id']}/portal/restore", headers=AUTH)
    assert rr.status_code == 200 and rr.json()["queued"] is True
    run = portal_queue.latest_run(db, case["id"])
    assert run.signal_name == "restore_portal"


# ---------- the secure window must be REAL: liveness is reconciled ----------

def test_expired_provider_session_is_reconciled_not_presented_as_open(client, db, monkeypatch):
    """A session Ellis records as open but the provider already ended must
    never be handed to the applicant as a working secure window — the live
    run asked for a CAPTCHA and a card payment in a window that was gone."""
    from app.providers import browser as bb
    case = _new_case(client)
    db.add(models.BrowserSession(org_id="org1", application_id=case["id"],
                                 provider_session_id="dead-session",
                                 mode="browserbase", status="open"))
    db.commit()
    monkeypatch.setattr(bb, "session_alive", lambda sid: sid != "dead-session")
    monkeypatch.setattr(bb, "create_session",
                        lambda: {"id": "fresh-session", "mode": "browserbase",
                                 "connect_url": "ws://x"})
    r = client.post(f"/cases/{case['id']}/browser-session", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["live_view_available"] is True
    rows = db.execute(select(models.BrowserSession).where(
        models.BrowserSession.application_id == case["id"])).scalars().all()
    by_sid = {x.provider_session_id: x.status for x in rows}
    assert by_sid["dead-session"] == "closed"     # the corpse is retired
    assert by_sid["fresh-session"] == "open"      # a real one replaced it


def test_progress_does_not_claim_an_ended_session_is_active(client, db, monkeypatch):
    from app.providers import browser as bb
    case = _new_case(client)
    db.add(models.BrowserSession(org_id="org1", application_id=case["id"],
                                 provider_session_id="dead-session",
                                 mode="browserbase", status="open"))
    db.commit()
    monkeypatch.setattr(bb, "session_alive", lambda sid: False)
    pr = client.get(f"/cases/{case['id']}/progress", headers=AUTH).json()
    assert pr["browser_session_alive"] is False


def test_live_view_reports_session_ended_not_misconfiguration(client, db, monkeypatch):
    """'Browserbase is not configured' was shown for a session that had simply
    expired — a different problem with a different fix."""
    from app.providers import browser as bb
    case = _new_case(client)
    db.add(models.BrowserSession(org_id="org1", application_id=case["id"],
                                 provider_session_id="dead-session",
                                 mode="browserbase", status="open"))
    db.commit()
    monkeypatch.setattr(bb, "live_view_url", lambda sid: None)
    monkeypatch.setattr(bb, "session_alive", lambda sid: False)
    r = client.get(f"/cases/{case['id']}/browser-session/live-view", headers=AUTH)
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "session_ended"


def test_portal_sessions_request_a_lifetime_that_outlives_human_steps():
    """A provider-default session lifetime (minutes) strands every handoff."""
    from app.providers import browser as bb
    assert bb.SESSION_TIMEOUT_SECONDS >= 1800


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


def test_question_collection_caps_live_option_harvests(db):
    """Regression (2026-07-28): collecting the applicant's question batch used
    to open EVERY remaining combobox to pre-validate stored answers — seconds
    per list on the real portal (~13s measured before the applicant saw the
    prompt). Harvests are capped per pause (same bound as the page
    classifier); selects past the cap are verified by the fill step instead,
    and unanswered ones are deferred to the next pause — never asked with an
    empty list."""
    harvested = []

    class Driver:
        def list_options(self, selector, max_options=300):
            harvested.append(selector)
            return {"ok": True, "options": ["A", "B"]}

        # Ellis fills what it CAN on the way past, so the answered selects are
        # really committed before the gaps are asked about.
        def select_search(self, selector, value):
            return ({"ok": True, "shown": value} if value in ("A", "B")
                    else {"ok": False, "code": "NO_OPTIONS", "options": []})

        def fill(self, selector, value):
            return {"ok": True}

    def sel(i, key, answered):
        return {"node_id": f"pick_{i}", "action": "SELECT_SEARCH",
                "selector": f"#s{i}", "input_source": key,
                "allowed_hostname": "evisa.gov.vn", "mandatory": True}

    nodes = [sel(i, f"k{i}", False) for i in range(5)] + [
        {"node_id": "done", "action": "COMPLETE", "allowed_hostname": "evisa.gov.vn"}]
    answers = {"k3": "A", "k4": "ZZZ"}      # k0-k2 unanswered, k3 matches, k4 wrong
    runner = _flow_runner(db, nodes, Driver(), answers, app_id="c-cap")
    res = runner.run()
    assert res["status"] == "paused_applicant_action"
    # At most 3 live harvests before the applicant sees the prompt, wherever
    # they happen — the rejected answer's own read counts against the budget.
    assert len(harvested) <= 3, harvested
    assert harvested[0] == "#s4", "the refused answer reads its list at fill time"
    keys = {q["key"] for q in res["questions"]}
    # The answer that matched is committed, never asked about.
    assert "k3" not in keys
    # The refused one comes back, and so do the unanswered ones within budget.
    assert "k4" in keys
    assert keys & {"k0", "k1", "k2"}
    # Anything past the budget waits for the next pause rather than being
    # asked with an empty dropdown.
    assert len(keys) < 5


def test_a_live_harvest_caches_the_portals_own_options(db):
    """The loop that makes pre-run dropdowns real: when the runner reads a
    portal's option list during a pause, that list is remembered against the
    adapter version so the NEXT applicant is asked with a real dropdown before
    any browser session opens."""
    from app.adapter_factory import models as fm

    class Driver:
        def list_options(self, selector, max_options=300):
            return {"ok": True, "options": ["Noi Bai", "Tan Son Nhat"]}

    nodes = [
        {"node_id": "pick_entry", "action": "SELECT_SEARCH", "selector": "#entry",
         "input_source": "entry_checkpoint", "allowed_hostname": "evisa.gov.vn",
         "mandatory": True},
        {"node_id": "done", "action": "COMPLETE", "allowed_hostname": "evisa.gov.vn"},
    ]
    runner = _flow_runner(db, nodes, Driver(), {}, app_id="c-optcache")
    res = runner.run()
    assert res["status"] == "paused_applicant_action"
    row = db.execute(select(fm.PortalFieldOptionCache).where(
        fm.PortalFieldOptionCache.candidate_id == runner.execution.candidate_id,
        fm.PortalFieldOptionCache.candidate_version == runner.execution.candidate_version,
        fm.PortalFieldOptionCache.node_id == "pick_entry")).scalars().first()
    assert row is not None
    assert row.options == ["Noi Bai", "Tan Son Nhat"]
    assert row.field_key == "entry_checkpoint"
    # The driver did not report the widget as exhausted, so the read is
    # recorded as INCOMPLETE and can never be served as the portal's whole list.
    assert row.complete is False
    assert row.observations == 1


def test_verified_official_fee_record_spares_the_applicant_typing_the_amount():
    """When the portal renders no machine-readable amount, the route's VERIFIED
    official fee record is used instead of asking the applicant to transcribe a
    figure. That is the authority's own published schedule (versioned,
    source-cited) — not an invention — so the applicant goes straight to a
    single "pay the fee" step. The exact-amount confirmation still happens
    there, and a fee READ off the live page always outranks the record."""
    class NoFee:
        def discover_fee(self, **_kw):
            return {"ok": False, "code": "FEE_NOT_DISPLAYED"}

        def detach(self):
            pass

    wf = _fee_wf(NoFee(), "FEE_DISCOVERY_PENDING")
    wf.fee_context = {"available": True, "total_cents": 2500, "currency": "USD",
                      "source_url": "https://evisa.gov.vn/", "version": 1,
                      "source_authority": "Immigration Department"}
    wf.start()
    assert wf.machine.state == "PAYMENT_APPROVAL_REQUIRED"
    # No "type the amount you see" step: the amount is already bound.
    assert wf.pending["handoff"] == "payment_approval"
    assert wf.fee["amount"] == 2500 and wf.fee["currency"] == "USD"
    assert wf.fee["source"] == "verified_official_fee_record"
    assert wf.fee["source_url"] == "https://evisa.gov.vn/"
    # The applicant still confirms that exact amount before anything is paid.
    wf.approve_payment(amount_cents=2500, currency="USD")
    assert wf.machine.state == "PAYMENT_ACTION_REQUIRED"


def test_a_route_with_no_verified_fee_record_still_asks_the_applicant():
    """The 'never invent a fee' rule is untouched: no verified record means no
    amount, so the applicant is still asked rather than shown a guess."""
    class NoFee:
        def discover_fee(self, **_kw):
            return {"ok": False, "code": "FEE_NOT_DISPLAYED"}

        def detach(self):
            pass

    for ctx in (None, {"available": False}, {"available": True, "total_cents": 0,
                                             "currency": "USD"}):
        wf = _fee_wf(NoFee(), "FEE_DISCOVERY_PENDING")
        wf.fee_context = ctx
        wf.start()
        assert wf.fee is None
        assert wf.pending["handoff"] == "fee_confirmation"


# ---------- pre-flight: ask before the browser types anything ----------------

class _RecordingDriver:
    """A driver that remembers whether the form was ever touched."""

    def __init__(self, questions):
        self._questions = questions
        self.created = False

    def preflight_questions(self, answers=None):
        held = dict(answers or {})
        return [q for q in self._questions if not held.get(q["key"])]

    def login(self, **_kw):
        return {"ok": True, "sessionToken": "s"}

    def create_application(self, **_kw):
        self.created = True
        return {"ok": True, "applicationId": "a1"}


def _session_ref():
    """A real vault reference — the fill step reveals the portal session."""
    from app import vault
    return vault.store("session-token", {"kind": "portal_session"})["ref"]


_OCCUPATION = {"key": "occupation", "question": "What is your occupation?",
               "why": "The official application form requires this information.",
               "format": "free text", "mandatory": True, "kind": "text"}


def test_a_mandatory_question_is_asked_before_the_form_is_touched():
    """Thailand ran five fields deep into a government form before stalling on
    a mandatory Occupation nobody had been asked for."""
    drv = _RecordingDriver([_OCCUPATION])
    wf = _wf(drv, "APPLICATION_FILLING", session_ref=_session_ref())
    st = wf._drive()
    assert drv.created is False, "the portal form was filled before asking"
    assert st["pending"]["handoff"] == "additional_information"
    assert [q["key"] for q in st["pending"]["questions"]] == ["occupation"]


def test_answering_the_pre_flight_lets_the_fill_proceed():
    drv = _RecordingDriver([_OCCUPATION])
    wf = _wf(drv, "APPLICATION_FILLING", session_ref=_session_ref())
    wf._drive()
    wf.provide_information({"occupation": "Engineer"})
    assert drv.created is True
    assert wf.answers["occupation"] == "Engineer"


def test_the_applicant_is_asked_once_and_never_looped():
    """A question the applicant declines to answer must not re-prompt forever
    — past the one pre-flight batch, the live page is the authority."""
    drv = _RecordingDriver([_OCCUPATION])
    wf = _wf(drv, "APPLICATION_FILLING", session_ref=_session_ref())
    wf._drive()
    wf.provide_information({})            # answered nothing
    assert drv.created is True
    assert wf.pending is None


def test_a_case_with_every_answer_is_never_interrupted():
    drv = _RecordingDriver([])
    wf = _wf(drv, "APPLICATION_FILLING", session_ref=_session_ref())
    wf._drive()
    assert drv.created is True
    assert wf.pending is None


def test_a_driver_that_cannot_pre_flight_still_runs():
    """Non-released adapters have no stored flow to read — they must not be
    blocked by a capability they do not have."""
    class _Plain:
        created = False

        def create_application(self, **_kw):
            type(self).created = True
            return {"ok": True, "applicationId": "a1"}

    drv = _Plain()
    wf = _wf(drv, "APPLICATION_FILLING", session_ref=_session_ref())
    wf._drive()
    assert _Plain.created is True


def test_the_pre_flight_flag_survives_a_reload():
    drv = _RecordingDriver([_OCCUPATION])
    wf = _wf(drv, "APPLICATION_FILLING", session_ref=_session_ref())
    wf._drive()
    assert wf.snapshot()["_preflight_asked"] is True


def test_one_file_submitted_against_two_requirements_keeps_both_identities(client, db):
    """The applicant's passport page submitted as BOTH 'passport' and 'photo'
    must satisfy both portal uploads — the later binding silently erased the
    passport identity and the run asked for a passport the case already held
    (Vietnam, 2026-08-04)."""
    from app.portal.released_flow import ReleasedFlowDriver
    from app.visa_snapshot.models import CaseRouteGuidance
    case = _new_case(client)
    doc = models.StoredDocument(org_id="org1", application_id=case["id"],
                                name="passport.jpg", mime="image/jpeg",
                                size_bytes=4, doc_type="passport", approved=False)
    db.add(doc); db.flush()
    db.add(models.DocumentBlob(document_id=doc.id, org_id="org1",
                               mime="image/jpeg", content=b"\xff\xd8\xff\xd9"))
    db.add(CaseRouteGuidance(case_id=case["id"], org_id="org1",
                             guidance={"guidance": {}}, checklist=[
                                 {"id": "passport", "label": "passport",
                                  "kind": "document", "required": True,
                                  "satisfied_by": ["passport"]},
                                 {"id": "photo", "label": "digital passport photo",
                                  "kind": "document", "required": True,
                                  "satisfied_by": ["photo"]}]))
    for item in ("passport", "photo"):
        db.add(models.ChecklistSubmission(org_id="org1", application_id=case["id"],
                                          item_id=item, document_id=doc.id,
                                          status="submitted", match_verdict="match",
                                          detected_type="passport",
                                          confirmed_by_applicant=True))
    db.commit()
    drv = ReleasedFlowDriver.__new__(ReleasedFlowDriver)
    drv.db = db
    drv.app_row = db.get(models.VisaApplication, case["id"])
    drv._tmp_files = []
    docs = drv._documents()
    types = sorted(d["doc_type"] for d in docs if d["name"] == "passport.jpg")
    assert types == ["passport", "photo"], types
    assert all(d["path"] for d in docs if d["name"] == "passport.jpg")
    drv._cleanup_tmp()
