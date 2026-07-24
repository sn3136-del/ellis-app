"""ORM models for the frozen 2026-07-23 global tourist-visa snapshot.

Immutable versioning: *Version tables are append-only; head tables carry a
current_version pointer. No version row is ever updated or deleted by the
importer (rollback appends a new head pointer, it does not delete history).
Snapshot data is global reference data (not org-scoped); operational rows
that belong to a tenant (RouteResolution, review/adapter tasks) carry org_id.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Integer,
                        String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ..models import TimestampMixin, _now, _uuid


class VisaSnapshot(Base, TimestampMixin):
    __tablename__ = "visa_snapshots"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    snapshot_date: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(200))
    update_mode: Mapped[str] = mapped_column(String(16), default="manual")
    automatic_refresh_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(32), default="building")  # building|exported|complete
    git_commit: Mapped[str] = mapped_column(String(64), default="")
    counts: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[str] = mapped_column(Text, default="")


class VisaRoute(Base, TimestampMixin):
    """Head row per route_key; immutable content lives in VisaRouteVersion."""
    __tablename__ = "visa_routes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    snapshot_date: Mapped[str] = mapped_column(String(10), index=True)
    route_key: Mapped[str] = mapped_column(String(400), unique=True, index=True)
    passport_nationality: Mapped[str] = mapped_column(String(3), index=True)
    passport_issuing_country: Mapped[str] = mapped_column(String(3), index=True)
    travel_document_type: Mapped[str] = mapped_column(String(48))
    lawful_country_of_residence: Mapped[str] = mapped_column(String(3), index=True)
    destination_country: Mapped[str] = mapped_column(String(3), index=True)
    visa_category: Mapped[str] = mapped_column(String(48))
    visa_subtype: Mapped[str] = mapped_column(String(48), default="any")
    travel_purpose: Mapped[str] = mapped_column(String(24), default="tourism")
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    disposition: Mapped[str] = mapped_column(String(48), default="RESEARCH_INCOMPLETE", index=True)
    research_status: Mapped[str] = mapped_column(String(32), default="not_researched", index=True)
    human_review_status: Mapped[str] = mapped_column(String(32), default="not_reviewed")
    fee_status: Mapped[str] = mapped_column(String(24), default="unknown")
    portal_verification_status: Mapped[str] = mapped_column(String(24), default="unknown")
    adapter_status: Mapped[str] = mapped_column(String(32), default="none")
    conflict_status: Mapped[str] = mapped_column(String(16), default="none")

    versions: Mapped[list["VisaRouteVersion"]] = relationship(back_populates="route")


class VisaRouteVersion(Base):
    """Append-only immutable route record versions."""
    __tablename__ = "visa_route_versions"
    __table_args__ = (UniqueConstraint("route_id", "version", name="uq_route_version"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    route_id: Mapped[str] = mapped_column(ForeignKey("visa_routes.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    record: Mapped[dict] = mapped_column(JSON)              # full validated route record
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    superseded_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    import_batch_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    route: Mapped["VisaRoute"] = relationship(back_populates="versions")


class VisaPolicy(Base, TimestampMixin):
    __tablename__ = "visa_policies"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    snapshot_date: Mapped[str] = mapped_column(String(10), index=True)
    policy_uid: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    destination_country: Mapped[str] = mapped_column(String(3), index=True)
    policy_kind: Mapped[str] = mapped_column(String(48))
    policy_name: Mapped[str] = mapped_column(String(300), default="")
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    research_status: Mapped[str] = mapped_column(String(32), default="not_researched")


class VisaPolicyVersion(Base):
    __tablename__ = "visa_policy_versions"
    __table_args__ = (UniqueConstraint("policy_id", "version", name="uq_policy_version"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    policy_id: Mapped[str] = mapped_column(ForeignKey("visa_policies.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    record: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    superseded_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PolicyApplicabilityRule(Base, TimestampMixin):
    """Which nationalities/doc types/residences a policy version applies to."""
    __tablename__ = "policy_applicability_rules"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    policy_id: Mapped[str] = mapped_column(ForeignKey("visa_policies.id"), index=True)
    policy_version: Mapped[int] = mapped_column(Integer, default=1)
    dimension: Mapped[str] = mapped_column(String(32))  # nationality|document_type|residence|condition
    mode: Mapped[str] = mapped_column(String(16), default="include")  # include|exclude
    values: Mapped[list] = mapped_column(JSON, default=list)
    condition_text: Mapped[str] = mapped_column(Text, default="")


class RouteMatrixEntry(Base):
    """Exhaustive normalized matrix; every combination has an explicit row."""
    __tablename__ = "route_matrix_entries"
    __table_args__ = (UniqueConstraint("matrix_key", name="uq_matrix_key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    snapshot_date: Mapped[str] = mapped_column(String(10), index=True)
    matrix_key: Mapped[str] = mapped_column(String(400), index=True)
    passport_nationality: Mapped[str] = mapped_column(String(3), index=True)   # or ANY
    passport_issuing_country: Mapped[str] = mapped_column(String(8), default="DERIVED")
    travel_document_type: Mapped[str] = mapped_column(String(48), index=True)
    lawful_country_of_residence: Mapped[str] = mapped_column(String(8), default="ANY")
    destination_country: Mapped[str] = mapped_column(String(3), index=True)
    visa_category: Mapped[str] = mapped_column(String(48), index=True)
    visa_subtype: Mapped[str] = mapped_column(String(48), default="any")
    disposition: Mapped[str] = mapped_column(String(48), default="RESEARCH_INCOMPLETE", index=True)
    route_id: Mapped[str | None] = mapped_column(ForeignKey("visa_routes.id"), nullable=True, index=True)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    exclusion_reason: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RouteResolution(Base, TimestampMixin):
    """Audit of one applicant route-resolution run (tenant-scoped)."""
    __tablename__ = "route_resolutions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(64), index=True)
    case_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    raw_input: Mapped[dict] = mapped_column(JSON, default=dict)
    normalized_input: Mapped[dict] = mapped_column(JSON, default=dict)
    route_key: Mapped[str] = mapped_column(String(400), index=True)
    route_id: Mapped[str | None] = mapped_column(ForeignKey("visa_routes.id"), nullable=True)
    route_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    readiness_status: Mapped[str] = mapped_column(String(48), default="NOT_READY")
    checks: Mapped[dict] = mapped_column(JSON, default=dict)   # each resolution gate result
    snapshot_date: Mapped[str] = mapped_column(String(10), default="")


class PassportValidityRuleRecord(Base, TimestampMixin):
    __tablename__ = "snapshot_passport_validity_rules"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    snapshot_date: Mapped[str] = mapped_column(String(10), index=True)
    destination_country: Mapped[str] = mapped_column(String(3), index=True)
    visa_category: Mapped[str] = mapped_column(String(48), default="any")
    rule_kind: Mapped[str] = mapped_column(String(48))  # months_after_arrival|months_after_departure|valid_through_departure|valid_on_arrival|unknown
    months: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_blank_pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    condition_text: Mapped[str] = mapped_column(Text, default="")
    research_status: Mapped[str] = mapped_column(String(32), default="not_researched")
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)


class DocumentRequirementSet(Base, TimestampMixin):
    __tablename__ = "document_requirement_sets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    snapshot_date: Mapped[str] = mapped_column(String(10), index=True)
    route_id: Mapped[str | None] = mapped_column(ForeignKey("visa_routes.id"), nullable=True, index=True)
    policy_id: Mapped[str | None] = mapped_column(ForeignKey("visa_policies.id"), nullable=True, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    required_documents: Mapped[list] = mapped_column(JSON, default=list)
    conditional_documents: Mapped[list] = mapped_column(JSON, default=list)
    financial_evidence: Mapped[str] = mapped_column(Text, default="")
    research_status: Mapped[str] = mapped_column(String(32), default="not_researched")
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)


class VisaFeeVersion(Base):
    """Append-only fee versions for the snapshot fee schedules."""
    __tablename__ = "visa_fee_versions"
    __table_args__ = (UniqueConstraint("fee_uid", "version", name="uq_fee_version"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    snapshot_date: Mapped[str] = mapped_column(String(10), index=True)
    fee_uid: Mapped[str] = mapped_column(String(160), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    destination_country: Mapped[str] = mapped_column(String(3), index=True)
    visa_category: Mapped[str] = mapped_column(String(48))
    record: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    fee_status: Mapped[str] = mapped_column(String(24), default="unknown")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ConsularJurisdictionRule(Base, TimestampMixin):
    __tablename__ = "consular_jurisdiction_rules"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    snapshot_date: Mapped[str] = mapped_column(String(10), index=True)
    destination_country: Mapped[str] = mapped_column(String(3), index=True)
    residence_jurisdiction: Mapped[str] = mapped_column(String(3), index=True)
    residence_subdivisions: Mapped[list] = mapped_column(JSON, default=list)
    competent_post_name: Mapped[str] = mapped_column(String(300), default="")
    competent_post_kind: Mapped[str] = mapped_column(String(32), default="unknown")
    competent_post_url: Mapped[str] = mapped_column(String(500), default="")
    covers_nationalities: Mapped[list] = mapped_column(JSON, default=list)
    conditions: Mapped[list] = mapped_column(JSON, default=list)
    verification_status: Mapped[str] = mapped_column(String(32), default="unverified")
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)


class OfficialPortalRecord(Base, TimestampMixin):
    __tablename__ = "official_portals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    snapshot_date: Mapped[str] = mapped_column(String(10), index=True)
    portal_uid: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    destination_country: Mapped[str] = mapped_column(String(3), index=True)
    portal_kind: Mapped[str] = mapped_column(String(48))
    operator: Mapped[str] = mapped_column(String(300), default="")
    operator_kind: Mapped[str] = mapped_column(String(32), default="unknown")
    url: Mapped[str] = mapped_column(String(500))
    hostnames: Mapped[list] = mapped_column(JSON, default=list)
    allowed_redirect_hosts: Mapped[list] = mapped_column(JSON, default=list)
    official_linking_source: Mapped[str] = mapped_column(String(500), default="")
    supported_categories: Mapped[list] = mapped_column(JSON, default=list)
    verification_status: Mapped[str] = mapped_column(String(48), default="unverified")
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)


class AuthorizedVisaCenter(Base, TimestampMixin):
    __tablename__ = "authorized_visa_centers"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    snapshot_date: Mapped[str] = mapped_column(String(10), index=True)
    destination_country: Mapped[str] = mapped_column(String(3), index=True)
    residence_jurisdiction: Mapped[str] = mapped_column(String(3), default="")
    operator: Mapped[str] = mapped_column(String(300), default="")
    name: Mapped[str] = mapped_column(String(300), default="")
    url: Mapped[str] = mapped_column(String(500), default="")
    official_linking_source: Mapped[str] = mapped_column(String(500), default="")
    verification_status: Mapped[str] = mapped_column(String(48), default="unverified")
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)


class SourceEvidence(Base):
    __tablename__ = "source_evidence"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    snapshot_date: Mapped[str] = mapped_column(String(10), index=True)
    search_query: Mapped[str] = mapped_column(Text, default="")
    original_url: Mapped[str] = mapped_column(String(1000))
    final_url: Mapped[str] = mapped_column(String(1000))
    redirect_chain: Mapped[list] = mapped_column(JSON, default=list)
    final_hostname: Mapped[str] = mapped_column(String(300), index=True)
    source_authority: Mapped[str] = mapped_column(String(64), index=True)
    applicable_jurisdiction: Mapped[str] = mapped_column(String(120), default="")
    relevant_excerpt: Mapped[str] = mapped_column(Text, default="")
    retrieved_at: Mapped[str] = mapped_column(String(40), default="")
    effective_date_evidence: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    page_language: Mapped[str] = mapped_column(String(16), default="")
    translation_reference: Mapped[str] = mapped_column(String(120), default="")
    verification_status: Mapped[str] = mapped_column(String(32), default="unverified")
    reviewer_status: Mapped[str] = mapped_column(String(32), default="not_reviewed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SourceSnapshot(Base):
    """Content snapshot for change detection: hash + capped text (no binaries)."""
    __tablename__ = "source_snapshots"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    evidence_id: Mapped[str] = mapped_column(ForeignKey("source_evidence.id"), index=True)
    url: Mapped[str] = mapped_column(String(1000))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    content_text: Mapped[str] = mapped_column(Text, default="")  # capped extract, PII-free
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retrieved_at: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SnapshotResearchBatch(Base, TimestampMixin):
    __tablename__ = "snapshot_research_batches"
    __table_args__ = (UniqueConstraint("snapshot_date", "batch_key", name="uq_batch_key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    snapshot_date: Mapped[str] = mapped_column(String(10), index=True)
    batch_key: Mapped[str] = mapped_column(String(200), index=True)
    destination_country: Mapped[str] = mapped_column(String(3), index=True)
    nationality_group: Mapped[str] = mapped_column(String(120), default="all")
    residence_group: Mapped[str] = mapped_column(String(120), default="all")
    stage: Mapped[str] = mapped_column(String(48), default="ENUMERATE", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    conflict_count: Mapped[int] = mapped_column(Integer, default=0)
    review_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[str] = mapped_column(String(40), default="")
    completed_at: Mapped[str] = mapped_column(String(40), default="")
    result: Mapped[str] = mapped_column(String(48), default="")
    content_hashes: Mapped[list] = mapped_column(JSON, default=list)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class SnapshotConflict(Base, TimestampMixin):
    __tablename__ = "snapshot_conflicts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    snapshot_date: Mapped[str] = mapped_column(String(10), index=True)
    route_key: Mapped[str] = mapped_column(String(400), default="", index=True)
    destination_country: Mapped[str] = mapped_column(String(3), index=True)
    field: Mapped[str] = mapped_column(String(120), default="")
    values: Mapped[list] = mapped_column(JSON, default=list)   # [{value, evidence_id, authority}]
    status: Mapped[str] = mapped_column(String(24), default="open")  # open|resolved
    resolution: Mapped[str] = mapped_column(Text, default="")
    resolved_by: Mapped[str] = mapped_column(String(120), default="")


class HumanReviewTask(Base, TimestampMixin):
    __tablename__ = "human_review_tasks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    snapshot_date: Mapped[str] = mapped_column(String(10), default="", index=True)
    kind: Mapped[str] = mapped_column(String(48), index=True)  # route_review|fee_review|portal_review|jurisdiction_review|conflict|intake_not_ready
    subject_id: Mapped[str] = mapped_column(String(400), default="")   # route_key / record id
    title: Mapped[str] = mapped_column(String(400), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="open", index=True)  # open|in_review|resolved|rejected
    resolved_by: Mapped[str] = mapped_column(String(120), default="")
    resolution_note: Mapped[str] = mapped_column(Text, default="")


class AdapterDevelopmentTask(Base, TimestampMixin):
    __tablename__ = "adapter_development_tasks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    route_key: Mapped[str] = mapped_column(String(400), index=True)
    route_id: Mapped[str | None] = mapped_column(ForeignKey("visa_routes.id"), nullable=True)
    case_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)   # lower = more urgent
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)  # queued|in_development|blocked|done|cancelled
    portal_evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    fee_evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    jurisdiction_evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[str] = mapped_column(Text, default="")


class RouteIntake(Base, TimestampMixin):
    """Applicant 'Start your visa' intake: saved progress, resumable, entirely
    UI-driven (no source edits / env vars / JSON files required)."""
    __tablename__ = "route_intakes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)  # draft|resolved|converted
    answers: Mapped[dict] = mapped_column(JSON, default=dict)
    preferred_language: Mapped[str] = mapped_column(String(16), default="en")
    email: Mapped[str] = mapped_column(String(320), default="")
    resolution_id: Mapped[str | None] = mapped_column(ForeignKey("route_resolutions.id"), nullable=True)
    case_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class OnDemandRouteResearchJob(Base, TimestampMixin):
    """Focused research for ONE exact applicant route (strategy: cached
    on-demand research, no global fleet). Durable + resumable: the stage
    checkpoint persists after every transition; limits are enforced; partial
    results are saved honestly. Tenant-scoped."""
    __tablename__ = "on_demand_route_research_jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), default="")
    case_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    intake_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    route_key: Mapped[str] = mapped_column(String(400), index=True)
    route: Mapped[dict] = mapped_column(JSON, default=dict)       # normalized route components
    requested_language: Mapped[str] = mapped_column(String(16), default="en")
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    # queued|running|complete|research_incomplete|conflicted|timed_out|failed
    stage: Mapped[str] = mapped_column(String(48), default="RECEIVE_ROUTE", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[str] = mapped_column(String(40), default="")
    finished_at: Mapped[str] = mapped_column(String(40), default="")
    researched_at_date: Mapped[str] = mapped_column(String(10), default="")   # actual date (date honesty)
    limits: Mapped[dict] = mapped_column(JSON, default=dict)
    progress: Mapped[list] = mapped_column(JSON, default=list)    # [{stage, at, note}]
    counters: Mapped[dict] = mapped_column(JSON, default=dict)    # queries/pages/kimi calls used
    result: Mapped[dict] = mapped_column(JSON, default=dict)      # readiness + statuses (redacted)
    kimi_model: Mapped[str] = mapped_column(String(64), default="")
    extraction_version: Mapped[str] = mapped_column(String(24), default="")
    error: Mapped[str] = mapped_column(Text, default="")


class RouteReadinessEvaluation(Base, TimestampMixin):
    __tablename__ = "route_readiness_evaluations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    route_key: Mapped[str] = mapped_column(String(400), index=True)
    case_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    readiness_status: Mapped[str] = mapped_column(String(48), default="NOT_READY")
    gates: Mapped[dict] = mapped_column(JSON, default=dict)
    evaluated_by: Mapped[str] = mapped_column(String(120), default="system")
    snapshot_date: Mapped[str] = mapped_column(String(10), default="")


class KimiRouteGuidanceCache(Base, TimestampMixin):
    """Cached Kimi-primary route guidance: one immediate structured answer per
    nationality × residence × destination × purpose × jurisdiction × policy
    month. Reused instantly for identical routes; refreshed asynchronously when
    stale (fresh_until passed). AI guidance only — the official-source pipeline
    remains the asynchronous audit and supersedes it once grounded."""
    __tablename__ = "kimi_route_guidance_cache"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    cache_key: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    route: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="KIMI_PRIMARY", index=True)
    # KIMI_PRIMARY | KIMI_UNCERTAIN
    guidance: Mapped[dict] = mapped_column(JSON, default=dict)
    missing_fields: Mapped[list] = mapped_column(JSON, default=list)
    contradictions: Mapped[list] = mapped_column(JSON, default=list)
    model: Mapped[str] = mapped_column(String(64), default="")
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    fresh_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
