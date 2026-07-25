"""Applicant privacy: data export (portability) and deletion (erasure).

Export gathers everything Ellis holds for a case/tenant into a portable JSON
bundle (never provider secrets — those are redacted at source and never stored).
Deletion cascades every per-case row and, for applicant erasure, the applicant
plus all their cases, leaving only a minimal non-PII audit tombstone so the
append-only trail records that erasure happened.

Retention: retention_due() lists cases whose completion/last-activity is older
than the configured window, for a sweep to erase.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import select

from . import models

# Per-case child tables keyed by application_id (order doesn't matter — no FKs
# enforce ordering in SQLite; Postgres has no cross-table FKs here either).
_CASE_CHILD_MODELS = [
    models.VisaApplicationVersion, models.ApplicantApproval, models.AuthorizationEnvelope,
    models.PortalAccount, models.NativeSignature, models.SignatureEvent,
    models.ChecklistSubmission, models.CaseStageProgress, models.StoredDocument,
    models.AppointmentPreference, models.Appointment, models.PaymentAttempt,
    models.SubmissionConfirmation, models.WorkflowExecution, models.HumanHandoff,
    models.EmailNotification, models.ApplicantStandingAuthorization,
    models.ApplicationReviewVersion, models.PaymentAuthorization,
]


def _rows(db, model, application_id):
    return db.execute(select(model).where(model.application_id == application_id)).scalars().all()


def export_case(db, application_id: str) -> dict:
    """A portable, secret-free snapshot of everything held for one case."""
    app = db.get(models.VisaApplication, application_id)
    if not app:
        raise KeyError("case not found")
    applicant = db.get(models.Applicant, app.applicant_id)
    docs = _rows(db, models.StoredDocument, application_id)
    appts = _rows(db, models.Appointment, application_id)
    confs = _rows(db, models.SubmissionConfirmation, application_id)
    sigs = _rows(db, models.NativeSignature, application_id)
    audit = _rows(db, models.AuditEvent, application_id)
    return {
        "exported_at": _now_iso(),
        "case": {"id": app.id, "state": app.state, "destination_country": app.destination_country,
                 "visa_type": app.visa_type, "answers": app.answers, "portal_reference": app.portal_reference},
        "applicant": ({"full_name": applicant.full_name, "email": applicant.email,
                       "phone": applicant.phone, "time_zone": applicant.time_zone} if applicant else None),
        "documents": [{"name": d.name, "mime": d.mime, "size_bytes": d.size_bytes,
                       "sha256": d.sha256, "doc_type": d.doc_type, "approved": d.approved,
                       "extracted_fields": d.extracted_fields} for d in docs],
        "signatures": [{"id": s.id, "provider": s.provider, "artifact_hash": s.artifact_hash,
                        "signature_method": s.signature_method, "invalidated": s.invalidated} for s in sigs],
        "appointments": [{"slot_id": a.slot_id, "location_id": a.location_id, "start_utc": a.start_utc,
                          "confirmation_no": a.confirmation_no, "reschedule_count": a.reschedule_count} for a in appts],
        "confirmations": [{"reference_no": c.reference_no, "receipt_no": c.receipt_no} for c in confs],
        "audit": [{"seq": e.seq, "action": e.action, "actor": e.actor, "detail": e.detail} for e in audit],
    }


def export_org(db, org_id: str) -> dict:
    """Tenant export: every case owned by an org."""
    apps = db.execute(select(models.VisaApplication).where(
        models.VisaApplication.org_id == org_id)).scalars().all()
    return {"exported_at": _now_iso(), "org_id": org_id,
            "cases": [export_case(db, a.id) for a in apps]}


def delete_case(db, application_id: str, *, actor: str = "applicant", reason: str = "applicant_request") -> dict:
    """Erase one case: all child rows + portal state + the application row.
    Leaves a non-PII tombstone audit event so erasure is itself recorded."""
    app = db.get(models.VisaApplication, application_id)
    if not app:
        raise KeyError("case not found")
    org_id = app.org_id
    counts = {}
    # Document preview bytes are keyed by document_id (not application_id), so
    # erase them explicitly — otherwise raw passport-scan bytes would survive
    # applicant erasure indefinitely.
    doc_ids = [d.id for d in _rows(db, models.StoredDocument, application_id)]
    blob_n = 0
    for did in doc_ids:
        blob = db.get(models.DocumentBlob, did)
        if blob:
            db.delete(blob); blob_n += 1
    counts["document_blobs"] = blob_n
    for model in _CASE_CHILD_MODELS:
        rows = _rows(db, model, application_id)
        counts[model.__tablename__] = len(rows)
        for r in rows:
            db.delete(r)
    # Portal state is keyed by case_id.
    ps = db.get(models.PortalState, application_id)
    if ps:
        db.delete(ps); counts["portal_states"] = 1
    # Journey rows keyed by case_id: the saved route guidance/checklist, and any
    # intake-stage document rows linked through the converted intake (their
    # bytes and extracted passport profile are personal data).
    from .visa_snapshot.models import CaseRouteGuidance, RouteIntake, RouteIntakeDocument
    cg = db.execute(select(CaseRouteGuidance).where(
        CaseRouteGuidance.case_id == application_id)).scalars().all()
    counts["case_route_guidance"] = len(cg)
    for row in cg:
        db.delete(row)
    intakes = db.execute(select(RouteIntake).where(
        RouteIntake.case_id == application_id)).scalars().all()
    idoc_n = 0
    for it in intakes:
        for idoc in db.execute(select(RouteIntakeDocument).where(
                RouteIntakeDocument.intake_id == it.id)).scalars().all():
            db.delete(idoc); idoc_n += 1
        db.delete(it)
    counts["route_intakes"] = len(intakes)
    counts["route_intake_documents"] = idoc_n
    # Old audit events for this case are personal-linked; remove them, then
    # write a single fresh tombstone.
    for e in _rows(db, models.AuditEvent, application_id):
        db.delete(e)
    applicant_id = app.applicant_id
    db.delete(app)
    db.flush()
    # Erase the applicant if they now have no remaining cases.
    orphan = db.execute(select(models.VisaApplication).where(
        models.VisaApplication.applicant_id == applicant_id)).scalars().first()
    if not orphan:
        applicant = db.get(models.Applicant, applicant_id)
        if applicant:
            db.delete(applicant); counts["applicants"] = 1
    tomb = models.AuditEvent(org_id=org_id, application_id=application_id, actor=actor,
                             action="case_erased", detail={"reason": reason, "counts": counts})
    db.add(tomb)
    db.commit()
    return {"deleted": True, "counts": counts}


def delete_applicant(db, applicant_id: str, *, actor: str = "applicant") -> dict:
    """Right-to-erasure for an applicant: erase every case then the applicant."""
    apps = db.execute(select(models.VisaApplication).where(
        models.VisaApplication.applicant_id == applicant_id)).scalars().all()
    total = {"cases": 0}
    for a in apps:
        delete_case(db, a.id, actor=actor, reason="applicant_erasure")
        total["cases"] += 1
    applicant = db.get(models.Applicant, applicant_id)
    if applicant:
        db.delete(applicant); db.commit()
        total["applicant"] = 1
    return {"deleted": True, **total}


def retention_due(db, *, days: int | None = None) -> list[str]:
    """Case ids whose last update is older than the retention window."""
    days = days if days is not None else int(os.getenv("ELLIS_RETENTION_DAYS", "365"))
    cutoff = _now_ms() - days * 86_400_000
    out = []
    for a in db.execute(select(models.VisaApplication)).scalars().all():
        ts = a.updated_at
        ms = int(ts.timestamp() * 1000) if isinstance(ts, datetime) else None
        if ms is not None and ms < cutoff:
            out.append(a.id)
    return out


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)
