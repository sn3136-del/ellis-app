"""Portal-adapter contract + validator + registry (Python). Conservative
defaults; unknown rule ⇒ applicant-controlled, no auto pay/submit."""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

POLICY_VALUES = {"automated", "applicant", "prohibited"}
APPROVAL_VALUES = {"mock", "implemented", "tested", "production_approved", "disabled"}

DEFAULT_PROHIBITED = [
    "solve_captcha", "bypass_bot_detection", "spoof_fingerprint", "rotate_proxy",
    "evade_rate_limit", "bypass_waiting_room", "access_other_account", "intercept_otp",
    "accept_declaration", "navigate_unapproved_domain",
]


@dataclass
class PortalAdapter:
    adapter_id: str
    adapter_version: int
    destination_country: str
    visa_type: str
    portal_operator: str
    approved_domains: list
    registration_url: str
    login_url: str
    application_url: str
    appointment_url: str
    required_applicant_fields: list
    required_documents: list
    registration_mappings: list
    application_mappings: list
    captcha_detect: str
    password_requirements: dict
    payment_policy: str
    third_party_payment_policy: str
    appointment_search: str
    appointment_booking: str
    reschedule_policy: str
    representative_submission: str
    personal_declaration_required: bool
    fee_discovery: str
    confirmation_extraction: str
    receipt_extraction: str
    resume_behavior: str
    rate_limits: dict
    portal_policy_review_date: str
    production_approval_status: str
    production_enabled: bool
    driver: object
    allowed_actions: list = field(default_factory=lambda: ["navigate", "read"])
    prohibited_actions: list = field(default_factory=lambda: list(DEFAULT_PROHIBITED))
    channel: str = ""
    # Portals like the Vietnam eVisa take personal applications with no account
    # at all; the workflow then skips registration/login instead of inventing one.
    account_required: bool = True


def validate_adapter(a: PortalAdapter) -> list[str]:
    errs = []
    for f in ("adapter_id", "destination_country", "visa_type", "portal_operator",
              "registration_url", "login_url", "application_url", "appointment_url",
              "confirmation_extraction", "receipt_extraction", "resume_behavior",
              "portal_policy_review_date"):
        if not getattr(a, f):
            errs.append(f"missing required field: {f}")
    if not isinstance(a.adapter_version, int) or a.adapter_version < 1:
        errs.append("adapter_version must be a positive integer")
    for f in ("payment_policy", "third_party_payment_policy", "appointment_booking",
              "reschedule_policy", "representative_submission"):
        if getattr(a, f) not in POLICY_VALUES:
            errs.append(f"{f} must be automated|applicant|prohibited")
    if a.production_approval_status not in APPROVAL_VALUES:
        errs.append("invalid production_approval_status")
    if "solve_captcha" not in a.prohibited_actions:
        errs.append("prohibited_actions must include solve_captcha")
    if set(a.allowed_actions) & set(a.prohibited_actions):
        errs.append("an action cannot be both allowed and prohibited")
    if a.production_enabled and a.production_approval_status != "production_approved":
        errs.append("production_enabled requires production_approved")
    allow = set(a.approved_domains)
    for url in (a.registration_url, a.login_url, a.application_url, a.appointment_url):
        host = urlparse(url).netloc
        if host and host not in allow:
            errs.append(f"approved_domains must include {host}")
    if a.driver is None:
        errs.append("adapter.driver required")
    return errs


_REGISTRY: dict[str, PortalAdapter] = {}


def register_adapter(a: PortalAdapter) -> PortalAdapter:
    errs = validate_adapter(a)
    if errs:
        raise ValueError(f"adapter {a.adapter_id} invalid: {'; '.join(errs)}")
    _REGISTRY[a.adapter_id] = a
    return a


def select_adapter(country: str, visa_type: str) -> PortalAdapter | None:
    matches = [a for a in _REGISTRY.values()
               if a.destination_country == country and a.visa_type == visa_type]
    matches.sort(key=lambda x: x.adapter_version, reverse=True)
    return matches[0] if matches else None


def list_adapters() -> list[dict]:
    return [{"adapter_id": a.adapter_id, "adapter_version": a.adapter_version,
             "destination_country": a.destination_country, "visa_type": a.visa_type,
             "production_approval_status": a.production_approval_status,
             "production_enabled": a.production_enabled} for a in _REGISTRY.values()]


def clear_registry():
    _REGISTRY.clear()
