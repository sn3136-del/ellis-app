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


def _fee_within_authorized_ceiling(db, app_row) -> bool:
    """Does the CURRENT total fee sit within the ceiling the applicant signed?
    No readable fee yet, or no envelope, is not a breach — the payment gates
    still apply later; only a fee that exceeds the signed ceiling blocks
    advance consent."""
    env = db.execute(select(models.AuthorizationEnvelope).where(
        models.AuthorizationEnvelope.application_id == app_row.id,
        models.AuthorizationEnvelope.status == "completed",
    ).order_by(models.AuthorizationEnvelope.created_at.desc())).scalars().first()
    ceiling = int(getattr(env, "max_fee_cents", 0) or 0) if env is not None else 0
    if ceiling <= 0:
        return True
    fee_rec = fees.verified_current_fee(
        db, destination=app_row.destination_country, visa_type=app_row.visa_type)
    if fee_rec is None:
        return True
    total = int(fee_rec.government_fee_cents or 0) + int(fee_rec.service_fee_cents or 0)
    return total <= ceiling


def _is_authorization_signature(db, application_id: str, sig) -> bool:
    """True when this NativeSignature is the applicant's AUTHORIZATION
    signature (the one bound to a completed authorization envelope), as
    opposed to a signature made in a dedicated final-review ceremony."""
    if sig is None or not getattr(sig, "artifact_hash", ""):
        return False
    env = db.execute(select(models.AuthorizationEnvelope).where(
        models.AuthorizationEnvelope.application_id == application_id,
        models.AuthorizationEnvelope.artifact_hash == sig.artifact_hash,
    )).scalars().first()
    return env is not None


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
        # A review version signed by ADVANCE CONSENT reuses the applicant's
        # single AUTHORIZATION signature. Only the stale review VERSION dies
        # here — cascading into that signature would revoke the applicant's
        # authorization itself on the first mid-run answer, permanently
        # dead-ending the case (nothing could re-sign it without a second
        # ceremony, which the single-ceremony flow does not have).
        if sig is not None and not _is_authorization_signature(db, app_row.id, sig):
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


def ensure_advance_signed(db, app_row, *,
                          actor: str = "ellis") -> models.ApplicationReviewVersion | None:
    """Single-ceremony advance consent (product decision 2026-07-27): the
    applicant signs ONCE — the authorization ceremony — and that signature
    stands as their consent to submit the application they completed. When a
    valid standing authorization permits submit_after_signed_final_review AND
    a completed, uninvalidated authorization signature exists, the CURRENT
    material state is frozen into a review version recorded as signed by that
    same signature (every version and hash still audited — the records are
    identical to a manual re-sign). Returns the signed-current row, or None
    when the preconditions are absent (signal-driven tests, revoked grants) —
    those flows keep the explicit final-review signature ceremony."""
    from . import authorization
    try:
        authorization.require(db, app_row.id, "submit_after_signed_final_review")
    except Exception:  # noqa: BLE001 — no/insufficient grant: no advance path
        return None
    sig = db.execute(select(models.NativeSignature).where(
        models.NativeSignature.application_id == app_row.id,
        models.NativeSignature.invalidated.is_(False),
    ).order_by(models.NativeSignature.created_at.desc())).scalars().first()
    if sig is None:
        return None
    row = signed_current(db, app_row)
    if row is not None:
        return row
    # Advance consent covers the money the applicant actually authorized. The
    # signed authorization carries a fee CEILING; a fee above it was never
    # consented to, so the case falls back to an explicit review rather than
    # freezing a bigger amount under the old signature.
    if not _fee_within_authorized_ceiling(db, app_row):
        return None
    row = create_review_version(db, app_row, actor=actor)
    record_signature(db, row, signature_id=sig.id, actor=actor)
    audit.record(db, org_id=app_row.org_id, application_id=app_row.id,
                 action="final_review_signed_by_advance_consent",
                 detail={"version": row.version, "content_hash": row.content_hash,
                         "signature_id": sig.id}, actor=actor)
    return row


def verify_ready_to_submit(db, app_row) -> models.ApplicationReviewVersion:
    """§25: valid standing authorization + valid exact-version signature +
    no material change since signature. The single-ceremony advance consent
    (ensure_advance_signed) satisfies it without a second signing step.
    Raises ReviewRequired otherwise."""
    from . import authorization
    authorization.require(db, app_row.id, "submit_after_signed_final_review")
    check_and_invalidate(db, app_row)
    row = signed_current(db, app_row) or ensure_advance_signed(db, app_row)
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
