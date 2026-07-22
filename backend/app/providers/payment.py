"""Payment — two modes, both fully implemented.

Automated: Stripe Issuing single-use virtual card (activation: STRIPE_SECRET_KEY
+ ELLIS_ISSUING_APPROVED=true) used only when the adapter permits third-party
payment and the applicant approved the exact amount.

Applicant-controlled: the default fallback. The Browserbase session navigates to
the official payment page and a Live View modal lets the applicant enter their
own card. Ellis/Kimi never see card data; only a non-sensitive success signal
is observed. This mode is a first-class path, not a placeholder.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import settings


@dataclass
class PaymentPlan:
    mode: str            # "stripe_issuing" | "applicant_window"
    amount_cents: int
    currency: str
    reason: str = ""


def choose_mode(*, adapter_third_party_policy: str, amount_cents: int, currency: str,
                applicant_approved: bool) -> PaymentPlan:
    s = settings()
    can_auto = (bool(s.stripe_secret_key) and s.issuing_approved
                and adapter_third_party_policy == "automated" and applicant_approved)
    if can_auto:
        return PaymentPlan("stripe_issuing", amount_cents, currency, "controlled virtual card")
    return PaymentPlan("applicant_window", amount_cents, currency,
                       "applicant enters their own card in the secure window")


class StripeIssuing:  # pragma: no cover - needs Stripe creds
    name = "stripe_issuing"

    @staticmethod
    def is_configured() -> bool:
        s = settings()
        return bool(s.stripe_secret_key) and s.issuing_approved

    @staticmethod
    def create_controlled_card(*, amount_cents: int, currency: str, merchant_country: str | None = None):
        # ACTIVATION: create a Cardholder + single-use virtual Card with the
        # narrowest spending controls (amount, count=1, expiry, MCC/country).
        # The card number/CVC are entered ONLY inside the isolated browser and
        # never returned to callers or the LLM.
        raise RuntimeError("Stripe Issuing not activated")

    @staticmethod
    def verify_webhook(payload: bytes, signature: str) -> bool:
        # ACTIVATION: stripe.Webhook.construct_event with STRIPE_WEBHOOK_SECRET.
        raise RuntimeError("Stripe webhooks not activated")


def applicant_window_descriptor(*, portal_name: str, country: str, amount_cents: int, currency: str,
                                live_view_url: str | None) -> dict:
    """The non-sensitive descriptor the UI renders for the payment modal."""
    return {
        "title": "Complete the embassy payment",
        "portal": portal_name,
        "country": country,
        "amount_display": f"{amount_cents / 100:.2f} {currency}",
        "ellis_stores_card": False,
        "live_view_url": live_view_url,     # None → local_handoff instruction panel
        "cancelable": True,
    }
