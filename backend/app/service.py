"""Service layer: persist and rehydrate the workflow + its portal so a worker
restart resumes with no in-memory state. Also owns adapter selection and the
per-case portal lifecycle.

In production the "portal" is the real external government portal reached over
Browserbase (persistent on its own server); reconciliation re-reads its state.
For the mock, we persist the MockPortal state next to the workflow so the
restart demonstration is faithful end-to-end.
"""
from __future__ import annotations

from sqlalchemy import select

from . import models
from .config import settings
from .portal.driver_factory import (RealOnlyStop, build_runtime_portal,
                                    register_runtime_adapters,
                                    select_runtime_adapter)
from .workflow import VisaWorkflow


def _emailer(db, application_id):
    def send(*, to, subject, body):
        db.add(models.EmailNotification(application_id=application_id, to_addr=to,
                                        subject=subject, body=body, sent=True))
        db.commit()
    return send


def load_workflow(db, application_id: str) -> VisaWorkflow:
    app = db.get(models.VisaApplication, application_id)
    if not app:
        raise KeyError("application not found")
    applicant = db.get(models.Applicant, app.applicant_id)
    exec_row = db.execute(
        select(models.WorkflowExecution).where(models.WorkflowExecution.application_id == application_id)
    ).scalar_one_or_none()

    # Rehydrate the portal from persisted state (or a fresh one on first run).
    # Real-only runtime modes: a route whose portal family has a RELEASED
    # adapter (deterministic 16-gate release → active runtime binding) executes
    # through the live FlowRunner bridge; every other route still fails closed
    # with RealOnlyStop — MockPortal is never bound (brief section 3).
    portal = adapter = None
    if not settings().mock_portal_allowed:
        from .portal.released_flow import build_for_case
        built = build_for_case(db, app)
        if built is not None:
            portal, adapter = built
    if adapter is None:
        portal_state = (exec_row.snapshot or {}).get("portal") if exec_row else None
        portal = build_runtime_portal(portal_state)
        register_runtime_adapters(portal)
        adapter = select_runtime_adapter(app.destination_country, app.visa_type)

    # A case can accumulate several envelopes (each modal open prepares one);
    # the operative authorization is the newest COMPLETED (signed) envelope —
    # an unsigned row prepared later must never swap the signed terms.
    auth = db.execute(
        select(models.AuthorizationEnvelope)
        .where(models.AuthorizationEnvelope.application_id == application_id,
               models.AuthorizationEnvelope.status == "completed")
        .order_by(models.AuthorizationEnvelope.created_at.desc())
    ).scalars().first()
    if auth is None:
        auth = db.execute(
            select(models.AuthorizationEnvelope)
            .where(models.AuthorizationEnvelope.application_id == application_id)
            .order_by(models.AuthorizationEnvelope.created_at.desc())
        ).scalars().first()
    authorization = {}
    if auth:
        authorization = {"max_fee_cents": auth.max_fee_cents, "currency": auth.currency,
                         "mode": auth.provider}
    pref_row = db.execute(
        select(models.AppointmentPreference).where(models.AppointmentPreference.application_id == application_id)
    ).scalar_one_or_none()
    preferences = pref_row.prefs if pref_row else {}

    docs = [{"name": d.name, "mime": d.mime, "size_bytes": d.size_bytes}
            for d in db.execute(select(models.StoredDocument).where(
                models.StoredDocument.application_id == application_id)).scalars().all()]

    exec_dict = None
    if exec_row:
        exec_dict = {"state": exec_row.state, "history": exec_row.history,
                     "snapshot": exec_row.snapshot, "pending": exec_row.pending}

    wf = VisaWorkflow(case_id=application_id, org_id=app.org_id, adapter=adapter,
                      applicant={"full_name": applicant.full_name, "email": applicant.email},
                      answers=app.answers, documents=docs, preferences=preferences,
                      authorization=authorization, exec_row=exec_dict, db=db,
                      emailer=_emailer(db, application_id))
    wf._portal = portal
    _wire_case_hooks(db, wf, app)
    return wf


def _wire_case_hooks(db, wf: VisaWorkflow, app_row):
    """DB-backed enforcement hooks: exact-amount payment authorization rows
    (payments.py) and the §25 submission gate (final_review.py)."""
    from . import payments, final_review, fees

    # Official route-fee record (methods/refundability/source) for the
    # applicant's fee and payment confirmation screens. Display-only —
    # the binding amount always comes from the portal or the applicant.
    try:
        nat = (app_row.answers or {}).get("passport_nationality") or \
              (app_row.answers or {}).get("nationality") or ""
        rec = fees.verified_current_fee(db, destination=app_row.destination_country,
                                        visa_type=app_row.visa_type, nationality=nat)
        wf.fee_context = fees.fee_breakdown(rec) if rec is not None else None
    except Exception:  # noqa: BLE001 — fee context is advisory, never blocking
        wf.fee_context = None

    def payments_hook(event, payload):
        if event == "authorized":
            fee = payload.get("fee") or {}
            payments.authorize(
                db, app_row=app_row, principal_user="applicant",
                amount_cents=payload["amount_cents"], currency=payload["currency"],
                payee=payload.get("payee", ""),
                government_fee_cents=int(fee.get("government_fee_cents", 0) or 0),
                service_fee_cents=int(fee.get("service_fee_cents", 0) or 0),
                reason="visa application fee")
        elif event == "invalidated":
            payments.invalidate_mismatched(db, wf.case_id, payload["amount_cents"],
                                           payload["currency"], org_id=app_row.org_id)
        elif event == "consumed":
            row = payments.valid_authorization(db, wf.case_id,
                                               payload["amount_cents"], payload["currency"])
            if row:
                payments.consume(db, row.id)

    def submission_gate():
        try:
            final_review.verify_ready_to_submit(db, app_row)
            return True, ""
        except Exception as e:  # noqa: BLE001 — reason surfaces in the pause
            return False, str(e)[:200]

    wf.payments_hook = payments_hook
    wf.submission_gate = submission_gate


def record_terminal_execution(db, org_id: str, application_id: str):
    """When a case reaches COMPLETED, persist the execution classification and
    queue the typed Trip.com case-status webhook. Shared by the synchronous
    API path and the background executor."""
    from . import execution
    app_row = db.get(models.VisaApplication, application_id)
    if app_row is None or app_row.state != "COMPLETED":
        return
    wf = load_workflow(db, application_id)
    ec = execution.classify_adapter(getattr(wf, "adapter", None))
    execution.record_execution(db, org_id=org_id, application_id=application_id,
                               action="case_completed", ec=ec,
                               detail={"state": app_row.state}, government_outcome=True)
    from .integrations import tripcom, tripcom_admin
    event = tripcom.case_status_event(
        tripcom_case_ref=(app_row.answers or {}).get("tripcom_case_ref", ""),
        ellis_case_id=application_id, state=app_row.state)
    event["execution_class"] = str(ec)
    event["is_real_government_result"] = execution.is_real_government_result(ec)
    tripcom_admin.queue_event(db, org_id=org_id, event=event)


def persist_workflow(db, wf: VisaWorkflow):
    app = db.get(models.VisaApplication, wf.case_id)
    # Cancellation fence: if the applicant cancelled while a background
    # segment was executing, the cancel wins — a stale in-memory machine must
    # never resurrect a CANCELLED case (last-writer-wins would otherwise).
    db.refresh(app)
    if app.state == "CANCELLED" and wf.machine.state != "CANCELLED":
        return
    app.state = wf.machine.state
    exec_row = db.execute(
        select(models.WorkflowExecution).where(models.WorkflowExecution.application_id == wf.case_id)
    ).scalar_one_or_none()
    snap = wf.snapshot()
    # Persist the portal state so a restart resumes faithfully.
    snap["portal"] = wf._portal.to_state()
    if not exec_row:
        exec_row = models.WorkflowExecution(application_id=wf.case_id)
        db.add(exec_row)
    exec_row.state = wf.machine.state
    exec_row.snapshot = snap
    exec_row.pending = wf.pending
    exec_row.history = wf.machine.history
    # Persist appointment / confirmation as first-class rows too.
    if wf.appointment:
        appt = db.execute(select(models.Appointment).where(
            models.Appointment.application_id == wf.case_id)).scalar_one_or_none()
        if not appt:
            appt = models.Appointment(application_id=wf.case_id)
            db.add(appt)
        appt.slot_id = wf.appointment["slotId"]
        appt.location_id = wf.appointment["locationId"]
        appt.start_utc = wf.appointment["startUtc"]
        appt.confirmation_no = wf.appointment["confirmationNo"]
        appt.reschedule_count = wf.reschedules
    if wf.confirmation:
        conf = db.execute(select(models.SubmissionConfirmation).where(
            models.SubmissionConfirmation.application_id == wf.case_id)).scalar_one_or_none()
        if not conf:
            conf = models.SubmissionConfirmation(application_id=wf.case_id,
                                                 reference_no=wf.confirmation["referenceNo"],
                                                 receipt_no=(wf.receipt or {}).get("receiptNo", ""))
            db.add(conf)
    db.commit()


def enforce_safety(db, application_id: str, wf) -> None:
    """The single, centralized safety gate for EVERY workflow transition —
    HTTP /start, HTTP /signals, and the background worker all pass through here,
    so no entry point can skip it (the review found /signals and the worker
    bypassed a gate placed only in the /start handler).

    (1) Live-class routes are hard-blocked until every readiness gate + all
        required applicant info are complete (PreparationOnlyMode).
    (2) An expired / insufficient-validity passport blocks any transition
        (PassportBlocked), and queues the renewal email.

    Mock/local routes pass the live gate (they cannot touch a real portal), but
    the passport check applies to all classes."""
    from . import personal_gate, passport_validity, models
    from .config import settings
    from .execution import ExecutionClass, MockAsProductionError, classify_adapter, coerce
    app_row = db.get(models.VisaApplication, application_id)
    if app_row is None:
        return
    ec = classify_adapter(getattr(wf, "adapter", None))
    # Belt-and-suspenders for the real-only boundary: in real-only runtime
    # modes a MOCK/LOCAL execution class must never transition at all.
    # (load_workflow already refuses to bind MockPortal there; this guards any
    # future path that hands a pre-built workflow to signal().)
    if settings().real_only_mode and coerce(ec) in (ExecutionClass.MOCK, ExecutionClass.LOCAL_PROVIDER):
        raise MockAsProductionError(
            f"runtime mode '{settings().runtime_mode}' forbids executing a {coerce(ec)}-class portal flow")
    personal_gate.assert_ready_for_live_action(db, app_row, ec)   # PreparationOnlyMode
    verdict = passport_validity.check_case_passport(db, app_row)
    if verdict.get("blocking"):
        passport_validity.enforce_and_notify(db, app_row, verdict)
        raise passport_validity.PassportBlocked(verdict)


def signal(db, application_id: str, name: str, *, defer_live=False,
           block_submit=False, progress_sink=None, **kwargs):
    """Load → enforce safety → apply a signal → persist. The single durable
    transition point; the safety gate here covers every caller.

    defer_live: foreground callers (HTTP) — never perform live portal work in
    this call; park before the first driver-needing state so the caller can
    queue a background run (wf.deferred_live tells it to).
    block_submit: background runs without the applicant's explicit submission
    confirmation can never cross into SUBMITTING.
    progress_sink: applicant-safe checkpoint recorder (step_key, status)."""
    wf = load_workflow(db, application_id)
    wf.defer_live = bool(defer_live)
    wf.block_submit = bool(block_submit)
    if progress_sink is not None:
        set_sink = getattr(getattr(wf.adapter, "driver", None), "set_progress_sink", None)
        if callable(set_sink):
            set_sink(progress_sink)
    try:
        enforce_safety(db, application_id, wf)
        method = getattr(wf, name)
        status = method(**kwargs) if kwargs else method()
        persist_workflow(db, wf)
        return status, wf
    finally:
        # Live released-flow drivers hold a local Playwright attachment; it
        # must stop at request end (the REMOTE portal session stays open) or
        # the next signal's attach in this thread fails.
        detach = getattr(getattr(wf.adapter, "driver", None), "detach", None)
        if callable(detach):
            try:
                detach()
            except Exception:  # noqa: BLE001 — cleanup must never mask results
                pass
