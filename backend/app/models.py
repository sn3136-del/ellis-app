"""ORM models. Multi-tenant (org_id on every owned row). No raw card data and
no reusable plaintext passwords are ever stored here — credentials live in the
vault and only a reference is persisted."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (String, Integer, BigInteger, Float, Boolean, ForeignKey, Text,
                        JSON, DateTime, LargeBinary)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class Applicant(Base, TimestampMixin):
    __tablename__ = "applicants"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    full_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320))
    phone: Mapped[str] = mapped_column(String(64), default="")
    time_zone: Mapped[str] = mapped_column(String(64), default="UTC")


class VisaApplication(Base, TimestampMixin):
    __tablename__ = "visa_applications"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    applicant_id: Mapped[str] = mapped_column(ForeignKey("applicants.id"))
    destination_country: Mapped[str] = mapped_column(String(80))
    visa_type: Mapped[str] = mapped_column(String(80), default="tourist")
    adapter_id: Mapped[str] = mapped_column(String(120), default="")
    state: Mapped[str] = mapped_column(String(48), default="DRAFT", index=True)
    answers: Mapped[dict] = mapped_column(JSON, default=dict)
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    portal_reference: Mapped[str] = mapped_column(String(120), default="")

    versions: Mapped[list["VisaApplicationVersion"]] = relationship(back_populates="application")
    documents: Mapped[list["StoredDocument"]] = relationship(back_populates="application")


class VisaApplicationVersion(Base, TimestampMixin):
    __tablename__ = "visa_application_versions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    application_id: Mapped[str] = mapped_column(ForeignKey("visa_applications.id"))
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    application: Mapped[VisaApplication] = relationship(back_populates="versions")


class ApplicantApproval(Base, TimestampMixin):
    __tablename__ = "applicant_approvals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    application_id: Mapped[str] = mapped_column(ForeignKey("visa_applications.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    approved_by: Mapped[str] = mapped_column(String(64))


class AuthorizationEnvelope(Base, TimestampMixin):
    __tablename__ = "authorization_envelopes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    application_id: Mapped[str] = mapped_column(ForeignKey("visa_applications.id"), index=True)
    provider: Mapped[str] = mapped_column(String(40))  # docusign | in_app_authorization
    envelope_id: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(40), default="created")
    max_fee_cents: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    allow_auto_book: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_auto_reschedule: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_representative_submit: Mapped[bool] = mapped_column(Boolean, default=False)
    artifact_hash: Mapped[str] = mapped_column(String(64), default="")
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class PortalAccount(Base, TimestampMixin):
    __tablename__ = "portal_accounts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    application_id: Mapped[str] = mapped_column(ForeignKey("visa_applications.id"), index=True)
    adapter_id: Mapped[str] = mapped_column(String(120))
    username: Mapped[str] = mapped_column(String(320))
    # ONLY a vault reference is stored — never the password itself.
    credential_ref: Mapped[str] = mapped_column(String(200), default="")
    session_ref: Mapped[str] = mapped_column(String(200), default="")
    verified: Mapped[bool] = mapped_column(Boolean, default=False)


class NativeSignature(Base, TimestampMixin):
    __tablename__ = "native_signatures"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(64), index=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("visa_applications.id"), index=True)
    app_version: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(40), default="native_ellis")
    template_version: Mapped[str] = mapped_column(String(40))
    consent_version: Mapped[str] = mapped_column(String(40))
    document_hash: Mapped[str] = mapped_column(String(64))
    artifact_hash: Mapped[str] = mapped_column(String(64))
    artifact_ref: Mapped[str] = mapped_column(String(300), default="")  # encrypted object-storage ref
    signature_method: Mapped[str] = mapped_column(String(20))
    auth_method: Mapped[str] = mapped_column(String(30))
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(300), default="")
    # Snapshot the signed application so a later material change invalidates it.
    app_snapshot_hash: Mapped[str] = mapped_column(String(64), default="")
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    invalidated: Mapped[bool] = mapped_column(Boolean, default=False)


class SignatureEvent(Base):
    __tablename__ = "signature_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    seq: Mapped[Optional[int]] = mapped_column(Integer, index=True, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    signature_id: Mapped[str] = mapped_column(String(32), index=True)
    application_id: Mapped[str] = mapped_column(String(32), index=True)
    event: Mapped[str] = mapped_column(String(40))  # prepared|consented|signed|invalidated|revoked
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class StoredDocument(Base, TimestampMixin):
    __tablename__ = "stored_documents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(64), index=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("visa_applications.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    mime: Mapped[str] = mapped_column(String(80))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    storage_ref: Mapped[str] = mapped_column(String(300), default="")  # s3:// or local ref
    doc_type: Mapped[str] = mapped_column(String(60), default="")
    ocr_status: Mapped[str] = mapped_column(String(30), default="pending")
    # How real the OCR that produced extracted_fields was (LOCAL_PROVIDER when the
    # deterministic parser ran; LIVE_PRODUCTION for Document AI / Kimi vision).
    execution_class: Mapped[str] = mapped_column(String(30), default="LOCAL_PROVIDER")
    # Passport biodata-page classification (accept | reject-with-reason).
    page_classification: Mapped[dict] = mapped_column(JSON, default=dict)
    extracted_fields: Mapped[dict] = mapped_column(JSON, default=dict)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    quality_warnings: Mapped[list] = mapped_column(JSON, default=list)
    application: Mapped[VisaApplication] = relationship(back_populates="documents")


class WebhookDelivery(Base, TimestampMixin):
    """Outbound Trip.com webhook deliveries (Phase 15 partial). Persisted so
    delivery is observable, retryable, dead-letterable, and replayable from the
    admin console. Payloads are typed sandbox-contract events — no PII beyond
    case references."""
    __tablename__ = "webhook_deliveries"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    endpoint: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending|delivered|failed|dead|unconfigured
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(String(200), default="")
    replay_of: Mapped[str] = mapped_column(String(32), default="")


class DocumentBlob(Base):
    """Document bytes for the in-app preview (Phase 13). Served ONLY through the
    authenticated content endpoint / short-lived signed URLs — never a local
    filesystem path, never a raw bucket URL. Production replaces this table with
    S3/GCS + KMS behind the same endpoint contract."""
    __tablename__ = "document_blobs"
    document_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(64), index=True)
    mime: Mapped[str] = mapped_column(String(80))
    content: Mapped[bytes] = mapped_column(LargeBinary)


class AppointmentPreference(Base, TimestampMixin):
    __tablename__ = "appointment_preferences"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    application_id: Mapped[str] = mapped_column(ForeignKey("visa_applications.id"), unique=True)
    prefs: Mapped[dict] = mapped_column(JSON, default=dict)


class Appointment(Base, TimestampMixin):
    __tablename__ = "appointments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    application_id: Mapped[str] = mapped_column(ForeignKey("visa_applications.id"), index=True)
    slot_id: Mapped[str] = mapped_column(String(120))
    location_id: Mapped[str] = mapped_column(String(80))
    start_utc: Mapped[int] = mapped_column(BigInteger)  # epoch ms (needs 64-bit)
    confirmation_no: Mapped[str] = mapped_column(String(120))
    reschedule_count: Mapped[int] = mapped_column(Integer, default=0)


class PaymentAttempt(Base, TimestampMixin):
    __tablename__ = "payment_attempts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    application_id: Mapped[str] = mapped_column(ForeignKey("visa_applications.id"), index=True)
    mode: Mapped[str] = mapped_column(String(30))  # stripe_issuing | applicant_window
    amount_cents: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    status: Mapped[str] = mapped_column(String(30), default="pending")
    receipt_no: Mapped[str] = mapped_column(String(120), default="")
    # Only non-sensitive metadata — never a card number/CVC.
    card_ref: Mapped[str] = mapped_column(String(120), default="")


class SubmissionConfirmation(Base, TimestampMixin):
    __tablename__ = "submission_confirmations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    application_id: Mapped[str] = mapped_column(ForeignKey("visa_applications.id"), index=True)
    reference_no: Mapped[str] = mapped_column(String(120))
    receipt_no: Mapped[str] = mapped_column(String(120), default="")


class WorkflowExecution(Base, TimestampMixin):
    __tablename__ = "workflow_executions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    application_id: Mapped[str] = mapped_column(ForeignKey("visa_applications.id"), unique=True, index=True)
    state: Mapped[str] = mapped_column(String(48), default="DRAFT")
    # The full resumable snapshot — the durable record a worker restart reloads.
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    pending: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    history: Mapped[list] = mapped_column(JSON, default=list)


class HumanHandoff(Base, TimestampMixin):
    __tablename__ = "human_handoffs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    application_id: Mapped[str] = mapped_column(ForeignKey("visa_applications.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))  # captcha | otp | payment | declaration | ...
    reason: Mapped[str] = mapped_column(Text, default="")
    live_view_mode: Mapped[str] = mapped_column(String(40), default="local_handoff")
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)


class EmailNotification(Base, TimestampMixin):
    __tablename__ = "email_notifications"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    application_id: Mapped[str] = mapped_column(ForeignKey("visa_applications.id"), index=True)
    to_addr: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    sent: Mapped[bool] = mapped_column(Boolean, default=False)
    # Phase 8 delivery lifecycle: queued -> sent | failed (retryable) -> dead (DLQ).
    # 'recorded' = local dev recorder (never claims real delivery).
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    event: Mapped[str] = mapped_column(String(60), default="")
    locale: Mapped[str] = mapped_column(String(12), default="en")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(String(200), default="")


class PortalDraft(Base, TimestampMixin):
    __tablename__ = "portal_drafts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(64), index=True)
    country: Mapped[str] = mapped_column(String(80))
    visa_type: Mapped[str] = mapped_column(String(80), default="tourist")
    draft: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="disabled_draft")  # disabled_draft|approved|rejected
    reviewed_by: Mapped[str] = mapped_column(String(64), default="")


class BrowserSession(Base, TimestampMixin):
    """Per-case isolated Browserbase session handle (tenant-scoped).

    Only the provider session id is stored — NEVER a Live View / debugger URL
    (those are short-lived, minted fresh per request, and never logged) and
    NEVER provider credentials (backend-only, from the vault/env)."""
    __tablename__ = "browser_sessions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(64), index=True)
    application_id: Mapped[str] = mapped_column(String(32), index=True)
    provider_session_id: Mapped[str] = mapped_column(String(120), default="")
    mode: Mapped[str] = mapped_column(String(24), default="local")   # browserbase|local
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open|closed
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    seq: Mapped[Optional[int]] = mapped_column(Integer, index=True, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    org_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    application_id: Mapped[str] = mapped_column(String(32), index=True, default="")
    actor: Mapped[str] = mapped_column(String(64), default="system")
    action: Mapped[str] = mapped_column(String(80))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class PortalState(Base, TimestampMixin):
    """Persisted per-case portal snapshot for the real-Temporal worker.

    Temporal makes the *workflow* durable across worker restarts, but the mock
    portal's own state (paid / booked / submitted) must also survive a restart —
    otherwise reconciliation on a restarted worker could double-pay. Activities
    load → mutate → save this row so a restarted worker reconciles correctly
    against the authoritative portal state. In production the portal is the real
    external government portal reached over Browserbase; this table is the mock's
    stand-in for that external durability."""
    __tablename__ = "portal_states"
    case_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    state: Mapped[dict] = mapped_column(JSON, default=dict)


class AdapterRecord(Base, TimestampMixin):
    """The authoritative lifecycle record for a country/visa-type portal adapter.
    Distinct from the runtime PortalAdapter (which binds a driver): this is what
    an administrator manages through discovered→…→production_active, and what the
    runtime consults before ever using an adapter in production."""
    __tablename__ = "adapter_records"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    country: Mapped[str] = mapped_column(String(80), index=True)
    visa_type: Mapped[str] = mapped_column(String(40), default="tourist")
    config: Mapped[dict] = mapped_column(JSON, default=dict)          # all portal metadata
    lifecycle_state: Mapped[str] = mapped_column(String(40), default="discovered", index=True)
    approval_status: Mapped[str] = mapped_column(String(40), default="unapproved")
    production_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    kill_switch: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    monitoring_status: Mapped[str] = mapped_column(String(40), default="not_monitored")
    rollback_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class ExecutionRecord(Base):
    """Persisted execution classification for every externally-facing action.

    The brief requires the classification to be persisted in the database and
    audit trail (not just returned in a response). One row per classified action
    so a case's whole history can be replayed with an honest realness label on
    each step — and so a report can prove no MOCK step was ever presented as a
    real government outcome."""
    __tablename__ = "execution_records"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    org_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    application_id: Mapped[str] = mapped_column(String(32), index=True, default="")
    action: Mapped[str] = mapped_column(String(80))          # e.g. document_ocr | portal_submit
    execution_class: Mapped[str] = mapped_column(String(30), index=True)
    is_real_government_result: Mapped[bool] = mapped_column(Boolean, default=False)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)  # non-sensitive metadata only


class RouteRule(Base, TimestampMixin):
    """One versioned visa-route rule (Phase 2). Route = destination + visa type +
    nationality + residence (+ purpose/duration in conditions). Only official
    sources; requires human review before production use. Kimi never authors an
    approved rule."""
    __tablename__ = "route_rules"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    destination: Mapped[str] = mapped_column(String(80), index=True)
    visa_type: Mapped[str] = mapped_column(String(80), default="tourist")
    nationality: Mapped[str] = mapped_column(String(80), default="")
    residence: Mapped[str] = mapped_column(String(80), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    # Source provenance (official government/embassy/consulate/contractor).
    source_url: Mapped[str] = mapped_column(String(500), default="")
    source_authority: Mapped[str] = mapped_column(String(200), default="")
    retrieved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_date: Mapped[str] = mapped_column(String(20), default="")
    expiration_date: Mapped[str] = mapped_column(String(20), default="")
    # Rule content (structured, non-secret).
    eligibility_conditions: Mapped[list] = mapped_column(JSON, default=list)
    required_documents: Mapped[list] = mapped_column(JSON, default=list)
    processing_method: Mapped[str] = mapped_column(String(60), default="")   # evisa|embassy|vac|on_arrival
    electronic_available: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    biometrics_required: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    interview_required: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    appointment_required: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    personal_appearance_required: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    third_party_preparation_allowed: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    third_party_submission_allowed: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    declaration_mandatory: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    # Destination-specific passport-validity requirement (Phase 5), e.g.
    # {"kind": "months_after_departure", "months": 6, "min_blank_pages": 2}
    passport_validity_rule: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    review_status: Mapped[str] = mapped_column(String(30), default="pending", index=True)  # pending|verified|rejected|stale
    reviewed_by: Mapped[str] = mapped_column(String(64), default="")
    superseded_by: Mapped[str] = mapped_column(String(32), default="")   # version chain


class FeeRecord(Base, TimestampMixin):
    """One versioned official-fee record for a route (Phase 3). Fees come only
    from official sources or authorized contractors; Kimi never invents or
    estimates one. Automated payment is blocked without a verified current fee."""
    __tablename__ = "fee_records"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    destination: Mapped[str] = mapped_column(String(80), index=True)
    visa_type: Mapped[str] = mapped_column(String(80), default="tourist")
    nationality: Mapped[str] = mapped_column(String(80), default="")
    residence: Mapped[str] = mapped_column(String(80), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    government_fee_cents: Mapped[int] = mapped_column(Integer, default=0)
    service_fee_cents: Mapped[int] = mapped_column(Integer, default=0)
    optional_fees: Mapped[list] = mapped_column(JSON, default=list)   # [{label, amount_cents, currency}]
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    conditions: Mapped[list] = mapped_column(JSON, default=list)      # entries/age/speed/reciprocity…
    refundability: Mapped[str] = mapped_column(String(200), default="")
    payment_methods: Mapped[list] = mapped_column(JSON, default=list)
    payment_timing: Mapped[str] = mapped_column(String(100), default="")
    source_url: Mapped[str] = mapped_column(String(500), default="")
    source_authority: Mapped[str] = mapped_column(String(200), default="")
    retrieved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_date: Mapped[str] = mapped_column(String(20), default="")
    review_status: Mapped[str] = mapped_column(String(30), default="pending", index=True)  # pending|verified|rejected|stale
    reviewed_by: Mapped[str] = mapped_column(String(64), default="")


class RouteReadiness(Base, TimestampMixin):
    """The 15-point safety gate for one exact route (destination + visa type +
    applicant nationality + residence). Every irreversible live action is blocked
    until every gate is complete. Gates are set ONLY by a human administrator
    through the admin endpoint — never by Kimi, never by search results."""
    __tablename__ = "route_readiness"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    destination: Mapped[str] = mapped_column(String(80), index=True)
    visa_type: Mapped[str] = mapped_column(String(80), default="tourist")
    nationality: Mapped[str] = mapped_column(String(80), default="")
    residence: Mapped[str] = mapped_column(String(80), default="")
    # gate_key -> {complete: bool, evidence: str, by: str, at: iso}
    gates: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[str] = mapped_column(Text, default="")


class TenantSetup(Base, TimestampMixin):
    """First-run administrator setup (Phase 7). Stores NON-SECRET configuration
    only. Provider credentials live in the vault (backend-only); this row keeps
    just the opaque vault ref + a redacted fingerprint per component, so a saved
    credential is never displayed again. Electron never persists any of this."""
    __tablename__ = "tenant_setup"
    org_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_name: Mapped[str] = mapped_column(String(200), default="")
    admin_email: Mapped[str] = mapped_column(String(320), default="")
    config: Mapped[dict] = mapped_column(JSON, default=dict)                 # non-secret only
    credential_refs: Mapped[dict] = mapped_column(JSON, default=dict)        # component -> vault ref
    credential_fingerprints: Mapped[dict] = mapped_column(JSON, default=dict)  # component -> sha256[:12]
    setup_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_by: Mapped[str] = mapped_column(String(64), default="")


class AdapterVersion(Base):
    """Immutable config snapshots for version history + rollback."""
    __tablename__ = "adapter_versions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    adapter_id: Mapped[str] = mapped_column(String(32), index=True)
    version: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    lifecycle_state: Mapped[str] = mapped_column(String(40), default="")
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ApplicantStandingAuthorization(Base, TimestampMixin):
    """One versioned standing authorization granted at onboarding. Immutable
    once granted: any change of the authorization text or permitted actions
    creates a NEW row (next version) and supersedes the prior one. Revocation
    flips revoked, never deletes. The applicant grants this once per case; it
    covers routine portal selection, form filling, uploads, appointment booking
    within saved preferences, payment navigation, and post-signature submission
    where permitted — payment amount confirmation always remains separate."""
    __tablename__ = "applicant_standing_authorizations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(64), index=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("visa_applications.id"), index=True)
    applicant_id: Mapped[str] = mapped_column(String(32), default="")
    route_key: Mapped[str] = mapped_column(String(400), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    text_version: Mapped[str] = mapped_column(String(20))       # authorization text version id
    text_hash: Mapped[str] = mapped_column(String(64))          # sha256 of the exact text shown
    ui_locale: Mapped[str] = mapped_column(String(12), default="en")
    permitted_actions: Mapped[list] = mapped_column(JSON, default=list)
    appointment_preferences: Mapped[dict] = mapped_column(JSON, default=dict)
    payment_confirmation_required: Mapped[bool] = mapped_column(Boolean, default=True)
    signature_policy: Mapped[str] = mapped_column(String(40), default="exact_version_esign")
    granted_by: Mapped[str] = mapped_column(String(64), default="")
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str] = mapped_column(String(300), default="")
    superseded_by: Mapped[str] = mapped_column(String(32), default="")


class ApplicationReviewVersion(Base, TimestampMixin):
    """The exact final review package the applicant saw. Immutable; the
    signature binds to content_hash. Any material change afterward invalidates
    the signature and requires a fresh review version + fresh signature."""
    __tablename__ = "application_review_versions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(64), index=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("visa_applications.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    package: Mapped[dict] = mapped_column(JSON, default=dict)   # full review package (redacted-safe)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    route_version: Mapped[str] = mapped_column(String(64), default="")
    adapter_version: Mapped[str] = mapped_column(String(64), default="")
    signed: Mapped[bool] = mapped_column(Boolean, default=False)
    signature_id: Mapped[str] = mapped_column(String(32), default="")
    invalidated: Mapped[bool] = mapped_column(Boolean, default=False)
    invalidated_reason: Mapped[str] = mapped_column(String(300), default="")


class PaymentAuthorization(Base, TimestampMixin):
    """One exact-amount payment approval. Authorizes ONLY the displayed amount,
    currency, and payee. A different final amount/currency invalidates it and a
    new confirmation is required. Consumed exactly once by a payment attempt."""
    __tablename__ = "payment_authorizations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(64), index=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("visa_applications.id"), index=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8))
    government_fee_cents: Mapped[int] = mapped_column(Integer, default=0)
    service_fee_cents: Mapped[int] = mapped_column(Integer, default=0)
    payee: Mapped[str] = mapped_column(String(200), default="")
    refundability: Mapped[str] = mapped_column(String(200), default="")
    fee_source_url: Mapped[str] = mapped_column(String(500), default="")
    fee_version: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(20), default="authorized", index=True)
    # authorized | consumed | invalidated | superseded
    approved_by: Mapped[str] = mapped_column(String(64), default="")
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    invalidated_reason: Mapped[str] = mapped_column(String(300), default="")
