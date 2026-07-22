"""Production-TARGETED adapter: Vietnam e-Visa (evisa.gov.vn).

Chosen per the brief's criteria: an official ONLINE e-visa portal with a clear
applicant/authorized-agent workflow, no CAPTCHA-bypass requirement, publicly
accessible registration + application, and testable without submitting
fraudulent information (we test against the mock, never the live portal).

STATUS: real domain/field/document/fee/checkpoint configuration is filled in
here, but `production_enabled=False` and `production_approval_status='tested'`
(against the mock). It is NOT activated against the live portal. See
ACTIVATION below. Automated tests bind `driver` to a MockPortal — no live
navigation, no real payment, no real submission ever runs in CI.

ACTIVATION (one reviewed step at a time):
  1. Legal review of evisa.gov.vn terms for authorized-agent automation.
  2. Implement the Playwright driver against the live DOM (replace MockPortalDriver).
  3. Verify selectors on the live portal (manually, authorized).
  4. Set production_approval_status='production_approved'.
  5. Set production_enabled=True (validator then permits it).
"""
from __future__ import annotations

from ..contract import PortalAdapter, register_adapter, DEFAULT_PROHIBITED
from .mockland import MockPortalDriver

# Real, public portal facts (used for config + the allowlist; NOT navigated in tests).
BASE = "https://evisa.gov.vn"
APPROVED = ["evisa.gov.vn", "thithucdientu.gov.vn"]


def build_vietnam_evisa_adapter(portal) -> PortalAdapter:
    return register_adapter(PortalAdapter(
        adapter_id="vietnam-evisa-tourist-v1", adapter_version=1,
        destination_country="Vietnam", visa_type="tourist",
        portal_operator="Vietnam Immigration Department — National e-Visa Portal",
        approved_domains=APPROVED,
        registration_url=f"{BASE}/en_US/dang-ky-tai-khoan",
        login_url=f"{BASE}/en_US/dang-nhap",
        application_url=f"{BASE}/en_US/thong-tin-ho-so",
        appointment_url=f"{BASE}/en_US/thong-tin-ho-so",  # e-visa: no in-person appointment
        required_applicant_fields=[
            "full_name", "email", "passport_number", "nationality", "birth_date",
            "sex", "passport_expiry", "intended_arrival", "intended_departure",
            "entry_checkpoint", "accommodation",
        ],
        required_documents=["passport_data_page", "portrait_photo"],
        registration_mappings=[
            {"field": "email", "selector": "#email"},
            {"field": "password", "selector": "#password", "sensitive": True},
        ],
        application_mappings=[
            {"field": "full_name", "selector": "#fullName"},
            {"field": "passport_number", "selector": "#passportNumber"},
            {"field": "nationality", "selector": "#nationality"},
            {"field": "birth_date", "selector": "#dateOfBirth"},
            {"field": "entry_checkpoint", "selector": "#allowedToEntryThrough"},
            {"field": "intended_arrival", "selector": "#grantEvisaValidFrom"},
        ],
        captcha_detect=".captcha, #captcha",
        password_requirements={"minLength": 8, "requireUpper": True, "requireDigit": True},
        # e-visa fee is paid on the portal by the applicant (or Stripe Issuing
        # when approved). Default stays applicant-controlled.
        payment_policy="applicant", third_party_payment_policy="applicant",
        appointment_search="none",           # pure online e-visa; no appointment
        appointment_booking="prohibited",
        reschedule_policy="prohibited",
        representative_submission="applicant",  # final submit + declaration are personal
        personal_declaration_required=True,
        fee_discovery="page",
        confirmation_extraction="registrationCode",   # portal's e-visa registration code
        receipt_extraction="paymentReceiptNo",
        resume_behavior="re-login; reconcile application/payment before retry",
        rate_limits={"searchMinIntervalMs": 60000, "maxChecksPerDay": 12},
        portal_policy_review_date="2026-07-21",
        production_approval_status="tested",   # tested against the mock, NOT the live portal
        production_enabled=False,              # NOT activated — see ACTIVATION
        allowed_actions=["navigate", "read", "fill", "click", "upload"],
        prohibited_actions=list(DEFAULT_PROHIBITED),
        driver=MockPortalDriver(portal),       # tests use the mock; live driver is the activation step
    ))
