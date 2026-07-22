"""Demonstration adapter — Mockland tourist visa. MOCK ONLY."""
from __future__ import annotations

from urllib.parse import urlparse

from ..contract import PortalAdapter, register_adapter, DEFAULT_PROHIBITED
from ..mock_portal import MOCK_BASE


class MockPortalDriver:
    """Imperative hooks. In a real adapter these use Playwright against a
    Browserbase page; here they call the in-process MockPortal."""
    def __init__(self, portal):
        self.p = portal

    def register(self, **kw): return self.p.register(**kw)
    def submit_captcha(self, **kw): return self.p.submit_captcha(**kw)
    def verify_email(self, **kw): return self.p.verify_email(**kw)
    def login(self, **kw): return self.p.login(**kw)
    def create_application(self, **kw): return self.p.create_application(**kw)
    def upload_document(self, **kw): return self.p.upload_document(**kw)
    def discover_fee(self, **kw): return self.p.discover_fee(**kw)
    def pay(self, **kw): return self.p.pay(**kw)
    def search_appointments(self, **kw): return self.p.search_appointments(**kw)
    def book_appointment(self, **kw): return self.p.book_appointment(**kw)
    def reschedule_appointment(self, **kw): return self.p.reschedule_appointment(**kw)
    def declare_personally(self, **kw): return self.p.declare_personally(**kw)
    def submit(self, **kw): return self.p.submit(**kw)
    def get_application_state(self, **kw): return self.p.get_application_state(**kw)


def build_mockland_adapter(portal) -> PortalAdapter:
    host = urlparse(MOCK_BASE).netloc
    return register_adapter(PortalAdapter(
        adapter_id="mockland-tourist-v1", adapter_version=1,
        destination_country="Mockland", visa_type="tourist",
        portal_operator="Mockland Bureau of Consular Affairs (MOCK)",
        approved_domains=[host],
        registration_url=f"{MOCK_BASE}/register", login_url=f"{MOCK_BASE}/login",
        application_url=f"{MOCK_BASE}/apply", appointment_url=f"{MOCK_BASE}/appointments",
        required_applicant_fields=["full_name", "email", "passport_number", "nationality", "birth_date"],
        required_documents=["passport", "photo"],
        registration_mappings=[{"field": "email", "selector": "#reg-email"},
                               {"field": "password", "selector": "#reg-password", "sensitive": True}],
        application_mappings=[{"field": "passport_number", "selector": "#app-passport"}],
        captcha_detect="#captcha-challenge",
        password_requirements={"minLength": 12},
        payment_policy="applicant", third_party_payment_policy="applicant",
        appointment_search="calendar", appointment_booking="automated",
        reschedule_policy="automated", representative_submission="applicant",
        personal_declaration_required=True, fee_discovery="page",
        confirmation_extraction="confirmation.referenceNo", receipt_extraction="receipt.receiptNo",
        resume_behavior="re-login; reconcile before any retry",
        rate_limits={"searchMinIntervalMs": 30000, "maxChecksPerDay": 24},
        portal_policy_review_date="2026-07-21",
        production_approval_status="mock", production_enabled=False,
        allowed_actions=["navigate", "read", "fill", "click", "upload"],
        prohibited_actions=list(DEFAULT_PROHIBITED),
        driver=MockPortalDriver(portal),
    ))
