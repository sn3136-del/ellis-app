"""Production-TARGETED adapter: US nonimmigrant visa APPOINTMENT scheduling
(ais.usvisa-info.com — the official Dept. of State / CGI Federal system).

Why this route qualifies for agent-channel booking: the official scheduling
FAQ permits a COMPANY to schedule for its users, provided it uses the official
website ("If you use another company to provide these services that company
must still use the services on this website."). That is an agency permission,
so Ellis's agent reads the calendar and drives the booking INSIDE the
operator's own authorized session on this exact site — it never runs a headless
bot, and it never defeats the site's human-verification check (solve_captcha /
bypass_bot_detection stay in prohibited_actions). Automated slot-search abuse
is what gets the traveller's appointment cancelled; a staffed, signed-in
session with a human clearing any challenge is the compliant path.

STATUS: real portal facts (domain / URLs / policies) are filled in, but this
adapter is `production_approval_status='tested'` and `production_enabled=False`
— it is NOT activated against the live site. The calendar/appointment
SELECTORS below are best-effort and UNVERIFIED: the live DOM sits behind login
and Cloudflare and must be confirmed in an authorized attended session before
production (that is exactly why production_enabled stays False — a guessed
selector can never touch a real traveller). Tests bind `driver` to a
MockPortal; no live navigation, booking, or payment runs in CI.

REGION SCOPE (verified against official sources 2026-08-18): ais.usvisa-info.com
serves many posts (India, Mexico, Brazil, much of Europe) but NOT mainland
China. China runs on CGI's ustraveldocs.com/cn + www.usvisascheduling.com
(cutover 2024-12-07), whose Terms prohibit access "through automated means,
including bots, crawlers, or scripts" and sit behind an active bot check.
For China, therefore, this adapter must never be selected: slots are read and
booked by a named operator BY HAND (source='operator_manual' in the booking
desk), or through the sanctioned group-coordinator channel. Any future China
adapter is a separate decision with its own legal review.

ACTIVATION (one reviewed step at a time):
  1. Legal/ToS review of ais.usvisa-info.com for agent-channel scheduling.
  2. Provision the operator account(s) that will hold the signed-in session.
  3. Confirm the calendar + booking selectors on the live site, in an
     authorized attended session (replaces the UNVERIFIED values below).
  4. Implement/point the live Playwright driver at the confirmed DOM.
  5. Set production_approval_status='production_approved'.
  6. Set production_enabled=True (the validator then permits it), and register
     the adapter in real-only mode.
"""
from __future__ import annotations

from ..contract import DEFAULT_PROHIBITED, PortalAdapter, register_adapter
from .mockland import MockPortalDriver

# Real, public portal facts (used for config + the allowlist; NOT navigated in
# tests). The scheduling system is one host operated for every post.
BASE = "https://ais.usvisa-info.com"
APPROVED = ["ais.usvisa-info.com"]


def build_us_visa_scheduling_adapter(portal) -> PortalAdapter:
    return register_adapter(PortalAdapter(
        adapter_id="us-visa-scheduling-v1", adapter_version=1,
        destination_country="United States", visa_type="b1b2",
        portal_operator="U.S. Department of State — Consular Appointment "
                        "Scheduling (CGI Federal, ais.usvisa-info.com)",
        approved_domains=APPROVED,
        registration_url=f"{BASE}/en-us/niv/users/sign_up",
        login_url=f"{BASE}/en-us/niv/users/sign_in",
        application_url=f"{BASE}/en-us/niv/schedule",
        appointment_url=f"{BASE}/en-us/niv/schedule",
        required_applicant_fields=[
            "full_name", "passport_number", "nationality", "birth_date",
            "ds160_confirmation_number", "post",
        ],
        required_documents=["passport_data_page", "ds160_confirmation"],
        # Sign-in is personal (credentials never pass through Ellis); marked
        # sensitive so the driver never types them.
        registration_mappings=[
            {"field": "email", "selector": "#user_email"},
            {"field": "password", "selector": "#user_password", "sensitive": True},
        ],
        application_mappings=[
            {"field": "ds160_confirmation_number",
             "selector": "#atlas_client_appointment_confirmation"},
        ],
        # UNVERIFIED — confirm in the attended session (ACTIVATION step 3). The
        # calendar read walks these to extract each open date.
        appointment_slot_selector="td.available a, .available-date",
        appointment_date_field="",          # the cell's own text is the date
        appointment_time_field="",
        appointment_label_field=".consular-post",
        appointment_post_field="",           # single post per session -> default_post
        captcha_detect=".g-recaptcha, #captcha, [data-sitekey]",
        password_requirements={"minLength": 8, "requireUpper": True,
                               "requireDigit": True},
        # The MRV fee is paid by the applicant on the official channel; Ellis
        # never enters card details.
        payment_policy="applicant", third_party_payment_policy="applicant",
        appointment_search="automated",      # the agent reads the calendar
        appointment_booking="automated",     # driven in-session; evidence-gated
        reschedule_policy="applicant",
        # The FAQ permits a company to schedule on the applicant's behalf.
        representative_submission="automated",
        # The interview itself is the applicant's personal appearance.
        personal_declaration_required=True,
        fee_discovery="page",
        confirmation_extraction="#appointment-confirmation-number",
        receipt_extraction="#mrv-receipt-number",
        resume_behavior="re-open the operator session; reconcile the official "
                        "appointment state before any re-book",
        # Conservative: automated slot-search abuse is punished on the
        # traveller, so the agent reads at human pace, in a staffed session.
        rate_limits={"searchMinIntervalMs": 60000, "maxChecksPerDay": 12},
        portal_policy_review_date="2026-08-14",
        production_approval_status="tested",  # tested vs mock, NOT the live site
        production_enabled=False,             # NOT activated — see ACTIVATION
        allowed_actions=["navigate", "read", "fill", "click"],
        # Inherits solve_captcha / bypass_bot_detection / spoof_fingerprint /
        # rotate_proxy — the agent structurally cannot defeat a human check.
        prohibited_actions=list(DEFAULT_PROHIBITED),
        channel="agent_channel_scheduling",
        driver=MockPortalDriver(portal),
    ))
