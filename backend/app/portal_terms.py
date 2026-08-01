"""Portal-terms consent: the applicant signs a specific portal's OWN terms
inside Ellis, and only then may the runtime transcribe their "agree" choice
on that portal (ESTA's disclaimer radios, K-ETA's agreement step, …).

Doctrine: a legal acceptance belongs to the applicant. Ellis's part is
mechanical transcription of a choice the applicant already made in full view
of the exact text — the same shape as the truthfulness declaration on the
released Vietnam route. The signature binds to the sha256 of the verbatim
terms text captured from the portal at build time; if the portal rewrites its
terms, the stored consent stops matching and the flow pauses for a fresh
signature instead of agreeing to words nobody saw.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy import select

from . import audit, models

TERMS_CONSENT_STATEMENT_VERSION = "portal-terms-consent-v1"


def terms_hash(text: str) -> str:
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()


def create_consent_request(db, app_row, *, portal_family_id: str,
                           terms_title: str, terms_text: str,
                           terms_source_url: str = "") -> models.PortalTermsConsent:
    """Stage the portal's verbatim terms for the applicant to read and sign.
    Idempotent per (case, family, terms_hash): the same text is never staged
    twice, and a superseded text creates a new row."""
    h = terms_hash(terms_text)
    existing = db.execute(select(models.PortalTermsConsent).where(
        models.PortalTermsConsent.application_id == app_row.id,
        models.PortalTermsConsent.portal_family_id == portal_family_id,
        models.PortalTermsConsent.terms_hash == h,
        models.PortalTermsConsent.revoked.is_(False))).scalars().first()
    if existing is not None:
        return existing
    row = models.PortalTermsConsent(
        org_id=app_row.org_id, application_id=app_row.id,
        applicant_id=getattr(app_row, "applicant_id", "") or "",
        portal_family_id=portal_family_id,
        terms_title=(terms_title or "")[:300], terms_text=terms_text or "",
        terms_hash=h, terms_source_url=(terms_source_url or "")[:500])
    db.add(row)
    db.commit()
    audit.record(db, org_id=app_row.org_id, application_id=app_row.id,
                 action="portal_terms_staged",
                 detail={"portal_family_id": portal_family_id,
                         "terms_hash": h, "title": (terms_title or "")[:120]},
                 actor="ellis")
    return row


def record_signature(db, consent_row, *, signature_id: str, actor: str):
    """Bind the applicant's signature to this exact terms text."""
    consent_row.signed = True
    consent_row.signature_id = signature_id
    consent_row.signed_at = datetime.now(timezone.utc)
    db.commit()
    audit.record(db, org_id=consent_row.org_id,
                 application_id=consent_row.application_id,
                 action="portal_terms_signed",
                 detail={"portal_family_id": consent_row.portal_family_id,
                         "terms_hash": consent_row.terms_hash,
                         "signature_id": signature_id},
                 actor=actor)
    return consent_row


def revoke(db, consent_row, *, reason: str, actor: str):
    consent_row.revoked = True
    consent_row.revoked_reason = (reason or "")[:300]
    db.commit()
    audit.record(db, org_id=consent_row.org_id,
                 application_id=consent_row.application_id,
                 action="portal_terms_revoked",
                 detail={"portal_family_id": consent_row.portal_family_id,
                         "reason": (reason or "")[:200]}, actor=actor)
    return consent_row


def signed_consent(db, *, application_id: str, portal_family_id: str,
                   expected_hash: str = "") -> models.PortalTermsConsent | None:
    """The case's signed, unrevoked consent for this portal — matched against
    the CURRENT terms hash when one is expected. A consent signed for older
    terms text never authorizes transcribing agreement to newer text."""
    q = select(models.PortalTermsConsent).where(
        models.PortalTermsConsent.application_id == application_id,
        models.PortalTermsConsent.portal_family_id == portal_family_id,
        models.PortalTermsConsent.signed.is_(True),
        models.PortalTermsConsent.revoked.is_(False))
    rows = db.execute(q.order_by(
        models.PortalTermsConsent.created_at.desc())).scalars().all()
    for row in rows:
        if not expected_hash or row.terms_hash == expected_hash:
            return row
    return None
