"""ORM models. Multi-tenant (org_id on every owned row). No raw card data and
no reusable plaintext passwords are ever stored here — credentials live in the
vault and only a reference is persisted."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import String, Integer, BigInteger, Float, Boolean, ForeignKey, Text, JSON, DateTime
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


class PortalDraft(Base, TimestampMixin):
    __tablename__ = "portal_drafts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(64), index=True)
    country: Mapped[str] = mapped_column(String(80))
    visa_type: Mapped[str] = mapped_column(String(80), default="tourist")
    draft: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="disabled_draft")  # disabled_draft|approved|rejected
    reviewed_by: Mapped[str] = mapped_column(String(64), default="")


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
