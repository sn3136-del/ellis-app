"""Applicant standing authorization (brief §5).

One clear, versioned standing authorization obtained at onboarding. It lets
Ellis perform ROUTINE permitted actions (portal selection, form completion,
uploads, appointment booking within saved preferences, payment navigation,
post-signature submission where permitted) without re-prompting the applicant.

Payment amount confirmation is NEVER covered — it is always a separate
exact-amount approval (payments.py). Personal/legal steps (CAPTCHA, OTP,
credentials, card entry, portal-native signatures, personally-required
declarations) always remain applicant actions regardless of this grant.

Records are immutable: a new grant supersedes the prior version; revocation
flips a flag and is honored before any subsequent irreversible action.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy import select

from . import models, audit

# Bump when the authorization text materially changes; old grants keep the
# version they were shown and remain auditable verbatim via text_hash.
TEXT_VERSION = "sa-2"

# The canonical routine actions a standing authorization may cover.
PERMITTED_ACTIONS = [
    "select_official_portal",
    "create_or_use_portal_account",
    "complete_forms_from_case_record",
    "upload_authorized_documents",
    "retrieve_appointment_inventory",
    "book_appointment_within_preferences",
    "navigate_to_payment",
    "submit_after_signed_final_review",
    "retrieve_receipts_and_status",
    "resume_interrupted_workflow",
    "reconcile_uncertain_outcomes",
]

# What the authorization screen must explain, keyed for i18n parity tests.
DISCLOSURES = [
    "official_portals_only",
    "routine_steps_automated",
    "never_invents_answers",
    "not_every_portal_automatable",
    "applicant_responsible_for_truth",
    "exact_final_review_and_signature",
    "payment_separate_confirmation",
    "secure_participation_may_be_required",
    "some_actions_legally_personal",
]


class AuthorizationMissing(Exception):
    """No valid (granted, unrevoked, unsuperseded) standing authorization."""


class ActionNotAuthorized(Exception):
    """The standing authorization does not cover the requested action."""


def build_text(*, locale: str, applicant_name: str, destination: str,
               visa_type: str) -> str:
    """Deterministic authorization text (EN master; zh handled by the UI layer
    alongside this authoritative original). Hashed and bound to the grant."""
    lines = [
        f"ELLIS STANDING AUTHORIZATION (version {TEXT_VERSION})",
        f"Applicant: {applicant_name}",
        f"Case: {destination} — {visa_type}",
        "",
        "You authorize Ellis, where legally and technically permitted, to:",
    ]
    lines += [f"  - {a.replace('_', ' ')}" for a in PERMITTED_ACTIONS]
    lines += [
        "",
        "Ellis will use official or authorized visa portals only.",
        "Ellis may complete routine steps automatically.",
        "Ellis will never invent answers on your behalf.",
        "Ellis may not be able to automate every portal.",
        "You remain responsible for the truth of submitted information.",
        "You must review and sign the exact final application before submission.",
        "Payment always requires a separate confirmation of the exact amount.",
        "CAPTCHA, one-time codes, passkeys and credentials always require "
        "your secure participation.",
        "Payment card details are entered by you in the secure window — or, "
        "only at your explicit request for a specific payment, provided to "
        "Ellis once so it can fill the official payment form. Ellis never "
        "stores them and never confirms a payment for you.",
        "Some government declarations or final actions may legally or "
        "technically require you to act personally.",
        "You may revoke this authorization before an irreversible action "
        "where operationally possible.",
        f"UI locale: {locale}",
    ]
    return "\n".join(lines)


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def grant(db, *, app_row, principal_user: str, locale: str = "en",
          appointment_preferences: dict | None = None,
          route_key: str = "") -> models.ApplicantStandingAuthorization:
    """Grant (or re-grant) the standing authorization for one case. A prior
    grant is superseded, never edited."""
    applicant = db.get(models.Applicant, app_row.applicant_id)
    text = build_text(locale=locale, applicant_name=applicant.full_name,
                      destination=app_row.destination_country, visa_type=app_row.visa_type)
    prior = current(db, app_row.id)
    row = models.ApplicantStandingAuthorization(
        org_id=app_row.org_id, application_id=app_row.id,
        applicant_id=app_row.applicant_id, route_key=route_key,
        version=(prior.version + 1 if prior else 1),
        text_version=TEXT_VERSION, text_hash=text_hash(text), ui_locale=locale,
        permitted_actions=list(PERMITTED_ACTIONS),
        appointment_preferences=appointment_preferences or {},
        payment_confirmation_required=True, granted_by=principal_user)
    db.add(row)
    db.flush()
    if prior:
        prior.superseded_by = row.id
    db.commit()
    audit.record(db, org_id=app_row.org_id, application_id=app_row.id,
                 action="standing_authorization_granted",
                 detail={"version": row.version, "text_version": TEXT_VERSION,
                         "text_hash": row.text_hash, "locale": locale},
                 actor=principal_user)
    return row


def revoke(db, *, app_row, principal_user: str, reason: str = "") -> models.ApplicantStandingAuthorization:
    row = current(db, app_row.id)
    if row is None:
        raise AuthorizationMissing("no standing authorization to revoke")
    row.revoked = True
    row.revoked_at = datetime.now(timezone.utc)
    row.revoked_reason = reason[:300]
    db.commit()
    audit.record(db, org_id=app_row.org_id, application_id=app_row.id,
                 action="standing_authorization_revoked",
                 detail={"version": row.version, "reason": reason[:120]},
                 actor=principal_user)
    return row


def current(db, application_id: str) -> models.ApplicantStandingAuthorization | None:
    """The latest unsuperseded grant (revoked or not — caller checks)."""
    return db.execute(select(models.ApplicantStandingAuthorization).where(
        models.ApplicantStandingAuthorization.application_id == application_id,
        models.ApplicantStandingAuthorization.superseded_by == "",
    ).order_by(models.ApplicantStandingAuthorization.version.desc())).scalars().first()


def valid(db, application_id: str) -> models.ApplicantStandingAuthorization | None:
    row = current(db, application_id)
    if row is None or row.revoked:
        return None
    return row


def require(db, application_id: str, action: str) -> models.ApplicantStandingAuthorization:
    """Assert a valid grant covering `action`. Payment confirmation can never
    be satisfied here — payments.py owns that separate approval."""
    if action == "confirm_payment":
        raise ActionNotAuthorized(
            "payment always requires a separate exact-amount confirmation")
    row = valid(db, application_id)
    if row is None:
        raise AuthorizationMissing(
            "standing authorization missing or revoked — the applicant must "
            "grant it in Ellis before Ellis acts on their behalf")
    if action not in (row.permitted_actions or []):
        raise ActionNotAuthorized(f"standing authorization does not cover {action!r}")
    return row


def to_dict(row: models.ApplicantStandingAuthorization | None) -> dict:
    if row is None:
        return {"granted": False}
    return {"granted": True, "id": row.id, "version": row.version,
            "text_version": row.text_version, "text_hash": row.text_hash,
            "ui_locale": row.ui_locale, "permitted_actions": row.permitted_actions,
            "appointment_preferences": row.appointment_preferences,
            "payment_confirmation_required": row.payment_confirmation_required,
            "signature_policy": row.signature_policy,
            "granted_at": row.granted_at.isoformat() if row.granted_at else None,
            "revoked": row.revoked,
            "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None}
