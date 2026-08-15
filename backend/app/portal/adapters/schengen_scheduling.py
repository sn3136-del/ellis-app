"""Production-TARGETED adapter: Schengen visa APPOINTMENT scheduling through an
accredited external service provider (VFS Global — visa.vfsglobal.com).

Why this route qualifies for agent-channel booking: EU Visa Code Art. 45 — "a
visa application may be submitted by the applicant, an accredited commercial
intermediary or a professional". Member states outsource appointment/lodging
to accredited ESPs (VFS Global, TLScontact, BLS). Ellis's agent reads the ESP
calendar and drives the booking INSIDE the accredited operator's own session;
biometrics and the VIS remain the applicant's own acts (Arts. 43/45), and the
agent never defeats a human-verification check (solve_captcha /
bypass_bot_detection stay prohibited).

Scope: this first adapter targets the VFS Global ESP flow, which is common
across many missions (including China -> several Schengen states). Per-mission
selector differences are confirmed at activation; the destination is modelled
as the Schengen Area because the ESP portal, not the member state, is what the
agent navigates.

STATUS: real ESP facts (domain / URLs / policies) are filled in, but this
adapter is `production_approval_status='tested'` and `production_enabled=False`
— NOT activated live. The SELECTORS are best-effort and UNVERIFIED (the ESP
calendar sits behind login); they must be confirmed per mission in an
authorized attended session before production. Tests bind `driver` to a
MockPortal.

ACTIVATION (one reviewed step at a time):
  1. Confirm the ESP accreditation + contract for each member-state mission.
  2. Provision the accredited operator account(s) for the session.
  3. Confirm the calendar + booking selectors on the live ESP portal, per
     mission, in an authorized attended session.
  4. Implement/point the live Playwright driver at the confirmed DOM.
  5. Set production_approval_status='production_approved'.
  6. Set production_enabled=True and register the adapter in real-only mode.
"""
from __future__ import annotations

from ..contract import DEFAULT_PROHIBITED, PortalAdapter, register_adapter
from .mockland import MockPortalDriver

BASE = "https://visa.vfsglobal.com"
APPROVED = ["visa.vfsglobal.com", "vfsglobal.com"]


def build_schengen_scheduling_adapter(portal) -> PortalAdapter:
    return register_adapter(PortalAdapter(
        adapter_id="schengen-vfs-scheduling-v1", adapter_version=1,
        destination_country="Schengen Area", visa_type="schengen",
        portal_operator="VFS Global — accredited Schengen external service "
                        "provider (visa.vfsglobal.com)",
        approved_domains=APPROVED,
        registration_url=f"{BASE}/en/register",
        login_url=f"{BASE}/en/login",
        application_url=f"{BASE}/en/application",
        appointment_url=f"{BASE}/en/appointment",
        required_applicant_fields=[
            "full_name", "passport_number", "nationality", "birth_date",
            "member_state", "post",
        ],
        required_documents=["passport_data_page", "portrait_photo"],
        registration_mappings=[
            {"field": "email", "selector": "#emailId"},
            {"field": "password", "selector": "#password", "sensitive": True},
        ],
        application_mappings=[
            {"field": "passport_number", "selector": "#passportNumber"},
            {"field": "birth_date", "selector": "#dateOfBirth",
             "format": "DD/MM/YYYY"},
        ],
        # UNVERIFIED — confirm per mission in the attended session.
        appointment_slot_selector=".mat-calendar-body-cell.available, "
                                  ".available-slot",
        appointment_date_field=".slot-date",
        appointment_time_field=".slot-time",
        appointment_label_field=".slot-category",
        appointment_post_field=".vac-centre",
        captcha_detect=".g-recaptcha, [data-sitekey], .captcha",
        password_requirements={"minLength": 8, "requireUpper": True,
                               "requireDigit": True},
        # Visa fee + ESP service fee are paid by the applicant or the
        # accredited intermediary's own account; Ellis never enters card data.
        payment_policy="applicant", third_party_payment_policy="applicant",
        appointment_search="automated",
        appointment_booking="automated",
        reschedule_policy="applicant",
        # Art. 45: an accredited intermediary may submit.
        representative_submission="automated",
        # Fingerprints + appearance at the VAC remain the applicant's own act.
        personal_declaration_required=True,
        fee_discovery="page",
        confirmation_extraction="#appointmentReferenceNumber",
        receipt_extraction="#paymentReferenceNumber",
        resume_behavior="re-open the accredited operator session; reconcile the "
                        "official appointment state before any re-book",
        rate_limits={"searchMinIntervalMs": 60000, "maxChecksPerDay": 12},
        portal_policy_review_date="2026-08-14",
        production_approval_status="tested",
        production_enabled=False,
        allowed_actions=["navigate", "read", "fill", "click", "upload"],
        prohibited_actions=list(DEFAULT_PROHIBITED),
        channel="agent_channel_scheduling",
        driver=MockPortalDriver(portal),
    ))
