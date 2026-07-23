"""Final application review + exact-version signature (brief §7, §25).

Before submission the applicant reviews a complete, immutable review package
and signs that EXACT version. The signature binds to the package content hash.
Any material change afterward (answers, documents, destination, visa category,
fee, jurisdiction, declarations) invalidates the signature and returns the
case to final review. After a valid signature Ellis may submit without another
routine confirmation prompt, unless the portal legally requires the applicant
to act personally (handled by the workflow's handoff states).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select

from . import models, audit, fees


class ReviewRequired(Exception):
    """Submission attempted without a signed, current final review."""


def _canonical(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def material_state(db, app_row) -> dict:
    """Everything whose change after signature must invalidate it."""
    docs = db.execute(select(models.StoredDocument).where(
        models.StoredDocument.application_id == app_row.id,
        models.StoredDocument.approved.is_(True))).scalars().all()
    fee_rec = fees.verified_current_fee(
        db, destination=app_row.destination_country, visa_type=app_row.visa_type)
    return {
        "answers": app_row.answers or {},
        "destination": app_row.destination_country,
        "visa_type": app_row.visa_type,
        "documents": sorted([{"name": d.name, "sha256": d.sha256} for d in docs],
                            key=lambda x: x["sha256"]),
        "fee": ({"amount_cents": fee_rec.government_fee_cents + fee_rec.service_fee_cents,
                 "currency": fee_rec.currency, "version": fee_rec.version}
                if fee_rec else None),
    }


def material_hash(db, app_row) -> str:
    return _canonical(material_state(db, app_row))


def build_package(db, app_row, *, locale: str = "en") -> dict:
    """The complete final review package shown to the applicant. Everything in
    it is case-record data the applicant already provided or official fee data;
    no secrets ever belong here."""
    applicant = db.get(models.Applicant, app_row.applicant_id)
    docs = db.execute(select(models.StoredDocument).where(
        models.StoredDocument.application_id == app_row.id)).scalars().all()
    appt = db.execute(select(models.Appointment).where(
        models.Appointment.application_id == app_row.id)).scalar_one_or_none()
    fee_rec = fees.verified_current_fee(
        db, destination=app_row.destination_country, visa_type=app_row.visa_type)
    exec_row = db.execute(select(models.WorkflowExecution).where(
        models.WorkflowExecution.application_id == app_row.id)).scalar_one_or_none()
    wf_fee = ((exec_row.snapshot or {}).get("fee") if exec_row else None) or {}
    return {
        "applicant": {"full_name": applicant.full_name, "email": applicant.email},
        "travel": {"destination": app_row.destination_country,
                   "visa_type": app_row.visa_type},
        "answers": app_row.answers or {},
        "documents": [{"name": d.name, "doc_type": d.doc_type, "sha256": d.sha256,
                       "approved": d.approved} for d in docs],
        "appointment": ({"slot_id": appt.slot_id, "location_id": appt.location_id,
                         "start_utc": appt.start_utc,
                         "confirmation_no": appt.confirmation_no} if appt else None),
        "fees": (fees.fee_breakdown(fee_rec) if fee_rec else
                 {"available": bool(wf_fee), **wf_fee}),
        "declarations": (app_row.answers or {}).get("declarations", []),
        "portal": app_row.adapter_id or app_row.destination_country,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "application_version": app_row.current_version,
        "locale": locale,
    }


def create_review_version(db, app_row, *, actor: str,
                          locale: str = "en") -> models.ApplicationReviewVersion:
    """Freeze the current material state into a new immutable review version."""
    prior = latest(db, app_row.id)
    pkg = build_package(db, app_row, locale=locale)
    row = models.ApplicationReviewVersion(
        org_id=app_row.org_id, application_id=app_row.id,
        version=(prior.version + 1 if prior else 1), package=pkg,
        content_hash=material_hash(db, app_row),
        route_version=str((app_row.answers or {}).get("route_key", "")),
        adapter_version=app_row.adapter_id or "")
    db.add(row)
    db.commit()
    audit.record(db, org_id=app_row.org_id, application_id=app_row.id,
                 action="final_review_version_created",
                 detail={"version": row.version, "content_hash": row.content_hash},
                 actor=actor)
    return row


def latest(db, application_id: str) -> models.ApplicationReviewVersion | None:
    return db.execute(select(models.ApplicationReviewVersion).where(
        models.ApplicationReviewVersion.application_id == application_id,
    ).order_by(models.ApplicationReviewVersion.version.desc())).scalars().first()


def record_signature(db, review_row, *, signature_id: str, actor: str):
    review_row.signed = True
    review_row.signature_id = signature_id
    db.commit()
    audit.record(db, org_id=review_row.org_id, application_id=review_row.application_id,
                 action="final_review_signed",
                 detail={"version": review_row.version,
                         "content_hash": review_row.content_hash,
                         "signature_id": signature_id},
                 actor=actor)


def check_and_invalidate(db, app_row, *, reason: str = "material change") -> bool:
    """Compare the signed review version against the CURRENT material state.
    On mismatch: invalidate the review + its signature and audit it. Returns
    True when an invalidation happened. Call after any change to answers,
    documents, or fees."""
    row = latest(db, app_row.id)
    if row is None or not row.signed or row.invalidated:
        return False
    if row.content_hash == material_hash(db, app_row):
        return False
    row.invalidated = True
    row.invalidated_reason = reason[:300]
    if row.signature_id:
        sig = db.get(models.NativeSignature, row.signature_id)
        if sig:
            sig.invalidated = True
        db.add(models.SignatureEvent(signature_id=row.signature_id,
                                     application_id=app_row.id, event="invalidated",
                                     detail={"reason": reason[:120]}))
    db.commit()
    audit.record(db, org_id=app_row.org_id, application_id=app_row.id,
                 action="final_review_signature_invalidated",
                 detail={"version": row.version, "reason": reason[:120]},
                 actor="ellis")
    return True


def signed_current(db, app_row) -> models.ApplicationReviewVersion | None:
    """The signed, uninvalidated review version whose hash still matches the
    live material state — the only thing that authorizes submission."""
    row = latest(db, app_row.id)
    if row is None or not row.signed or row.invalidated:
        return None
    if row.content_hash != material_hash(db, app_row):
        return None
    return row


def verify_ready_to_submit(db, app_row) -> models.ApplicationReviewVersion:
    """§25: valid standing authorization + valid exact-version signature +
    no material change since signature. Raises ReviewRequired otherwise."""
    from . import authorization
    authorization.require(db, app_row.id, "submit_after_signed_final_review")
    check_and_invalidate(db, app_row)
    row = signed_current(db, app_row)
    if row is None:
        raise ReviewRequired(
            "final review and signature required: the applicant must review "
            "and sign the exact final application version before submission")
    return row


def to_dict(row: models.ApplicationReviewVersion | None) -> dict:
    if row is None:
        return {"exists": False}
    return {"exists": True, "id": row.id, "version": row.version,
            "package": row.package, "content_hash": row.content_hash,
            "signed": row.signed, "invalidated": row.invalidated,
            "invalidated_reason": row.invalidated_reason,
            "signature_id": row.signature_id}
