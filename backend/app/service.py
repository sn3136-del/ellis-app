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
from .portal.contract import select_adapter, clear_registry
from .portal.mock_portal import MockPortal
from .portal.adapters.mockland import build_mockland_adapter
from .portal.adapters.vietnam_evisa import build_vietnam_evisa_adapter
from .workflow import VisaWorkflow


def _register_adapters(portal):
    # Adapters are bound to a portal driver. For the mock/tested adapters that
    # is the in-process MockPortal; a production adapter binds a Browserbase
    # Playwright driver instead.
    clear_registry()
    build_mockland_adapter(portal)
    build_vietnam_evisa_adapter(portal)


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
    portal_state = (exec_row.snapshot or {}).get("portal") if exec_row else None
    portal = MockPortal.from_state(portal_state) if portal_state else MockPortal()
    _register_adapters(portal)

    country = app.destination_country
    adapter = select_adapter(country, app.visa_type) or select_adapter("Mockland", "tourist")

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


def signal(db, application_id: str, name: str, **kwargs):
    """Load → apply a signal → persist. The single durable transition point."""
    wf = load_workflow(db, application_id)
    method = getattr(wf, name)
    status = method(**kwargs) if kwargs else method()
    persist_workflow(db, wf)
    return status, wf
