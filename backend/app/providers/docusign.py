"""Applicant authorization.

Production: DocuSign embedded signing (activation: DOCUSIGN_INTEGRATION_KEY +
DOCUSIGN_ACCOUNT_ID). The authorization enumerates exactly what Ellis may do
(account creation, filling, uploads, appointment search/book, optional
rescheduling, payment initiation, max fee/currency, representative submission
where permitted) and what must stay applicant-personal.

Development fallback: an in-app authorization the applicant confirms, recorded
with a content hash. It is clearly NOT equivalent to DocuSign and is labeled as
such; it never substitutes for a government declaration under penalty of perjury.
"""
from __future__ import annotations

import hashlib
import hmac
import json

from ..config import settings


def is_configured() -> bool:
    s = settings()
    return bool(s.docusign_integration_key and s.docusign_account_id)


def authorization_payload(*, applicant: dict, destination: str, visa_type: str, portal: str,
                          max_fee_cents: int, currency: str, allow_auto_book: bool,
                          allow_auto_reschedule: bool, allow_representative_submit: bool) -> dict:
    return {
        "applicant": {"name": applicant.get("full_name"), "email": applicant.get("email")},
        "destination": destination, "visa_type": visa_type, "portal": portal,
        "authorizes": {
            "create_portal_account": True, "prepare_application": True, "upload_documents": True,
            "search_appointments": True, "book_earliest_appointment": allow_auto_book,
            "auto_reschedule_earlier": allow_auto_reschedule,
            "initiate_disclosed_payment": True, "max_fee_cents": max_fee_cents, "currency": currency,
            "representative_submission": allow_representative_submit,
        },
        "must_remain_applicant_personal": [
            "captcha", "otp", "identity_verification", "card_entry",
            "government_declaration_under_penalty_of_perjury",
        ],
    }


def create_envelope(payload: dict) -> dict:
    if is_configured():  # pragma: no cover - needs DocuSign creds
        # ACTIVATION: create an embedded envelope from a template; return the
        # embedded signing URL + envelopeId.
        raise RuntimeError("DocuSign not activated")
    # Dev fallback — recorded, hashed, clearly labeled non-production.
    artifact_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return {"provider": "in_app_authorization", "envelope_id": None,
            "artifact_hash": artifact_hash, "production_equivalent": False}


def verify_webhook(payload: bytes, signature: str) -> bool:
    s = settings()
    if not s.docusign_hmac_secret:
        # In dev fallback there is no webhook; explicit completion is used.
        return False
    expected = hmac.new(s.docusign_hmac_secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
