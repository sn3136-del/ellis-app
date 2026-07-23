"""Versioned official visa-fee engine (Phase 3).

Fees come ONLY from official sources or officially authorized contractors and
require human review before use. Kimi never invents or estimates an official
fee. When no verified current fee exists for a route, automated payment is
BLOCKED and the applicant is routed to applicant-controlled verification.
Time-sensitive fees go stale and alert administrators.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from . import models, audit

# A verified fee older than this must be re-verified before automated payment.
FEE_MAX_AGE_DAYS = 90

_FEE_FIELDS = (
    "government_fee_cents", "service_fee_cents", "optional_fees", "currency",
    "conditions", "refundability", "payment_methods", "payment_timing",
    "source_url", "source_authority", "effective_date",
)


def _route_filter(destination, visa_type, nationality, residence):
    return (models.FeeRecord.destination == destination,
            models.FeeRecord.visa_type == visa_type,
            models.FeeRecord.nationality == nationality,
            models.FeeRecord.residence == residence)


def create_fee(db, *, destination: str, visa_type: str = "tourist", nationality: str = "",
               residence: str = "", actor: str = "system", **fields) -> models.FeeRecord:
    """Record the next PENDING fee version. Requires an official source URL.
    Never auto-verified."""
    unknown = set(fields) - set(_FEE_FIELDS)
    if unknown:
        raise ValueError(f"unknown fee fields: {sorted(unknown)}")
    if not fields.get("source_url"):
        raise ValueError("source_url (official source) is required")
    if int(fields.get("government_fee_cents", 0)) < 0:
        raise ValueError("government_fee_cents must be >= 0")
    prev = db.execute(select(models.FeeRecord).where(
        *_route_filter(destination, visa_type, nationality, residence)
    ).order_by(models.FeeRecord.version.desc())).scalars().first()
    rec = models.FeeRecord(destination=destination, visa_type=visa_type,
                           nationality=nationality, residence=residence,
                           version=(prev.version + 1 if prev else 1),
                           retrieved_at=datetime.now(timezone.utc),
                           review_status="pending", **fields)
    db.add(rec)
    db.commit()
    audit.record(db, org_id="platform", application_id="", action="fee_recorded",
                 detail={"destination": destination, "visa_type": visa_type,
                         "version": rec.version, "source_url": fields.get("source_url", "")},
                 actor=actor)
    return rec


def review_fee(db, *, fee_id: str, decision: str, actor: str) -> models.FeeRecord:
    if decision not in ("verified", "rejected", "stale"):
        raise ValueError("decision must be verified|rejected|stale")
    rec = db.get(models.FeeRecord, fee_id)
    if not rec:
        raise KeyError("fee record not found")
    prev_status = rec.review_status
    rec.review_status = decision
    rec.reviewed_by = actor
    db.commit()
    audit.record(db, org_id="platform", application_id="", action="fee_reviewed",
                 detail={"fee_id": fee_id, "decision": decision, "was": prev_status}, actor=actor)
    return rec


def verified_current_fee(db, *, destination: str, visa_type: str = "tourist",
                         nationality: str = "", residence: str = "") -> models.FeeRecord | None:
    """The latest VERIFIED, non-stale fee for a route — or None. None means
    automated payment is blocked."""
    rec = db.execute(select(models.FeeRecord).where(
        *_route_filter(destination, visa_type, nationality, residence),
        models.FeeRecord.review_status == "verified",
    ).order_by(models.FeeRecord.version.desc())).scalars().first()
    if rec is None:
        return None
    if rec.retrieved_at is not None:
        age = datetime.now(timezone.utc) - rec.retrieved_at.replace(tzinfo=timezone.utc)
        if age > timedelta(days=FEE_MAX_AGE_DAYS):
            return None    # stale — must be re-verified before automated payment
    return rec


def fee_breakdown(rec: models.FeeRecord | None) -> dict:
    """The full display breakdown shown BEFORE any payment. Honest when absent."""
    if rec is None:
        return {"available": False, "automated_payment_allowed": False,
                "reason": ("No verified current official fee for this route. Automated "
                           "payment is blocked; verify the fee directly on the official "
                           "portal before paying.")}
    total = rec.government_fee_cents + rec.service_fee_cents + sum(
        int(f.get("amount_cents", 0)) for f in (rec.optional_fees or []))
    return {
        "available": True, "automated_payment_allowed": True,
        "government_fee_cents": rec.government_fee_cents,
        "service_fee_cents": rec.service_fee_cents,
        "optional_fees": rec.optional_fees or [],
        "total_cents": total, "currency": rec.currency,
        "conditions": rec.conditions or [], "refundability": rec.refundability,
        "payment_methods": rec.payment_methods or [], "payment_timing": rec.payment_timing,
        "source_url": rec.source_url, "source_authority": rec.source_authority,
        "effective_date": rec.effective_date, "version": rec.version,
        "retrieved_at": rec.retrieved_at.isoformat() if rec.retrieved_at else None,
    }


def stale_fees(db) -> list[dict]:
    """Verified fees past the refresh window — the administrator alert list."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=FEE_MAX_AGE_DAYS)
    out = []
    for rec in db.execute(select(models.FeeRecord).where(
            models.FeeRecord.review_status == "verified")).scalars().all():
        if rec.retrieved_at is not None and rec.retrieved_at.replace(tzinfo=timezone.utc) < cutoff:
            out.append({"id": rec.id, "destination": rec.destination, "visa_type": rec.visa_type,
                        "nationality": rec.nationality, "residence": rec.residence,
                        "version": rec.version,
                        "retrieved_at": rec.retrieved_at.isoformat(),
                        "action_required": "re-verify against the official source"})
    return out
