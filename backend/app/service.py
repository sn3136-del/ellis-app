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
    # In real-only runtime modes this raises RealOnlyStop — MockPortal is never
    # bound and there is no live driver yet (brief section 3, fail closed).
    portal_state = (exec_row.snapshot or {}).get("portal") if exec_row else None
    portal = build_runtime_portal(portal_state)
    register_runtime_adapters(portal)

    country = app.destination_country
    adapter = select_runtime_adapter(country, app.visa_type)

    auth = db.execute(
        select(models.AuthorizationEnvelope).where(models.AuthorizationEnvelope.application_id == application_id)
    ).scalar_one_or_none()
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
    return wf


def persist_workflow(db, wf: VisaWorkflow):
    app = db.get(models.VisaApplication, wf.case_id)
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


def signal(db, application_id: str, name: str, **kwargs):
    """Load → enforce safety → apply a signal → persist. The single durable
    transition point; the safety gate here covers every caller."""
    wf = load_workflow(db, application_id)
    enforce_safety(db, application_id, wf)
    method = getattr(wf, name)
    status = method(**kwargs) if kwargs else method()
    persist_workflow(db, wf)
    return status, wf
