"""Native Ellis electronic-signature system.

ESignatureProvider is the interface; NativeEllisSignatureProvider is the
implementation (no DocuSign dependency). The applicant personally signs after
consent + explicit intent + step-up authentication; the signed authorization is
a tamper-evident PDF bound to an immutable document hash and an append-only
event trail.

Kimi may prepare authorization LANGUAGE but never creates, types, draws, adopts,
confirms, or applies the applicant's signature — that path does not exist here.

A native Ellis authorization is NOT a government declaration; portal declarations
under penalty of perjury are completed by the applicant via Browserbase Live View.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .pdfgen import text_pdf

TEMPLATE_VERSION = "ellis-auth-v1"
CONSENT_VERSION = "eln-consent-v1"

_CONSENT_TEXT = (
    "ELECTRONIC RECORDS & SIGNATURE CONSENT: You agree to use electronic records "
    "and an electronic signature for this visa authorization. You may request a "
    "paper copy and may withdraw consent before Ellis takes any irreversible action.")


def build_authorization_text(*, applicant: dict, org_id: str, case_id: str, app_version: int,
                             destination: str, visa_type: str, portal: str,
                             max_fee_cents: int, currency: str, allow_auto_book: bool,
                             allow_auto_reschedule: bool, allow_representative_submit: bool) -> str:
    """Deterministic authorization document text (versioned). Same inputs → same
    bytes → same hash."""
    lines = [
        f"ELLIS TOURIST-VISA AUTHORIZATION  [{TEMPLATE_VERSION}]",
        "",
        f"Applicant: {applicant.get('full_name','')}",
        f"Case: {case_id}   Application version: {app_version}   Org: {org_id}",
        f"Destination: {destination}   Visa type: {visa_type}   Portal: {portal}",
        "",
        "You authorize Ellis to, on your behalf:",
        "  - create or connect your official visa-portal account;",
        "  - prepare and complete the application from your approved data;",
        "  - upload the documents you approved;",
        "  - search for appointments;",
        f"  - {'book the earliest qualifying appointment' if allow_auto_book else 'NOT auto-book (you choose the slot)'};",
        f"  - {'move you to an earlier qualifying slot automatically' if allow_auto_reschedule else 'NOT auto-reschedule'};",
        f"  - initiate the disclosed government fee up to {max_fee_cents/100:.2f} {currency};",
        f"  - {'submit the application where the portal permits representative submission' if allow_representative_submit else 'NOT submit on your behalf; you submit personally'}.",
        "",
        "You will personally complete: CAPTCHA, OTP, identity verification, card entry,",
        "and any government declaration made under penalty of perjury.",
        "",
        _CONSENT_TEXT,
    ]
    return "\n".join(lines)


def document_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def application_snapshot_hash(answers: dict, documents: list[str]) -> str:
    return hashlib.sha256(json.dumps({"answers": answers, "docs": sorted(documents)},
                                     sort_keys=True).encode()).hexdigest()


@dataclass
class SignatureRequest:
    applicant: dict
    org_id: str
    case_id: str
    app_version: int
    document_text: str
    document_hash: str
    consent_given: bool
    intent_confirmed: bool
    signature_method: str        # "typed" | "drawn"
    signature_value: str         # typed name, or an opaque drawn-signature ref
    step_up_verified: bool
    auth_method: str             # "email_otp" | "mfa" | "passkey" | "webauthn"
    ip_address: str
    user_agent: str


class ESignatureProvider:
    def sign(self, req: SignatureRequest) -> dict:  # pragma: no cover - interface
        raise NotImplementedError


class NativeEllisSignatureProvider(ESignatureProvider):
    name = "native_ellis"

    def sign(self, req: SignatureRequest) -> dict:
        # Hard preconditions — a signature cannot be recorded without them.
        if not req.consent_given:
            raise ValueError("electronic-records consent required")
        if not req.intent_confirmed:
            raise ValueError("explicit intent-to-sign required")
        if req.signature_method not in ("typed", "drawn"):
            raise ValueError("signature method must be typed or drawn")
        if not req.signature_value:
            raise ValueError("signature value required")
        if not req.step_up_verified:
            raise ValueError("step-up authentication required")
        # The signed document must match the exact text the applicant reviewed.
        if document_hash(req.document_text) != req.document_hash:
            raise ValueError("document hash mismatch — re-review required")

        import datetime
        signed_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        # Tamper-evident signed PDF: content + hash + audit certificate lines.
        cert_lines = req.document_text.split("\n") + [
            "",
            "----- ELLIS SIGNATURE CERTIFICATE -----",
            f"Template: {TEMPLATE_VERSION}   Consent: {CONSENT_VERSION}",
            f"Signed by: {req.signature_value}  (method: {req.signature_method})",
            f"Applicant present, intent confirmed, step-up: {req.auth_method}",
            f"Case: {req.case_id}  App version: {req.app_version}  Org: {req.org_id}",
            f"Document SHA-256: {req.document_hash}",
            f"Signed at (UTC): {signed_at}",
            f"IP: {req.ip_address}   Agent: {req.user_agent[:60]}",
        ]
        pdf = text_pdf(cert_lines, title="Ellis Authorization (signed)")
        # The signed-artifact hash covers the rendered PDF — any byte change is
        # detectable, which is what makes it tamper-evident.
        artifact_hash = hashlib.sha256(pdf).hexdigest()
        return {
            "provider": self.name,
            "document_hash": req.document_hash,
            "artifact_hash": artifact_hash,
            "signed_pdf": pdf,
            "signed_at": signed_at,
            "template_version": TEMPLATE_VERSION,
            "consent_version": CONSENT_VERSION,
            "signature_method": req.signature_method,
            "auth_method": req.auth_method,
        }


def get_provider() -> ESignatureProvider:
    return NativeEllisSignatureProvider()
