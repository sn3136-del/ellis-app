"""ORM models for global route coverage.

RoutePairPolicy is the operational policy layer: exactly one row per
(nationality, travel document, destination) pair, carrying the entry
disposition, the brief outcome, and honest provenance. The structural
route_matrix_entries table (visa_snapshot) stays the exhaustive per-category
matrix; this table is the pair-level layer the resolver and dashboard read.

PortalFamily deduplicates real official portals: one family per platform,
one adapter per family (FamilyAdapterLink), reused across every route that
applies through it.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ..models import TimestampMixin, _now, _uuid


def pair_key(nationality: str, doc_type: str, destination: str) -> str:
    return f"gp1|nat={nationality}|doc={doc_type}|dest={destination}"


class RoutePairPolicy(Base, TimestampMixin):
    """One row per (nationality, document type, destination) pair."""
    __tablename__ = "global_route_pair_policies"
    __table_args__ = (UniqueConstraint("pair_key", name="uq_global_pair_key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    snapshot_date: Mapped[str] = mapped_column(String(10), index=True)
    pair_key: Mapped[str] = mapped_column(String(120), index=True)
    passport_nationality: Mapped[str] = mapped_column(String(3), index=True)
    travel_document_type: Mapped[str] = mapped_column(String(48), default="ordinary_passport")
    destination_country: Mapped[str] = mapped_column(String(3), index=True)
    # matrix-vocabulary disposition (+ NO_TOURIST_ROUTE), see matrix.DISPOSITIONS
    disposition: Mapped[str] = mapped_column(String(48), default="RESEARCH_INCOMPLETE", index=True)
    # brief-vocabulary outcome, see global_routes.ROUTE_OUTCOMES
    route_outcome: Mapped[str] = mapped_column(String(48), default="UNRESOLVED", index=True)
    primary_category: Mapped[str] = mapped_column(String(48), default="")
    max_stay_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # provenance / honesty
    source: Mapped[str] = mapped_column(String(32), default="none", index=True)  # PAIR_SOURCES
    verification_status: Mapped[str] = mapped_column(String(16), default="unresolved", index=True)  # VERIFICATION_LEVELS
    source_ref: Mapped[str] = mapped_column(String(400), default="")
    official_source_urls: Mapped[list] = mapped_column(JSON, default=list)
    retrieved_at: Mapped[str] = mapped_column(String(32), default="")
    dataset_date: Mapped[str] = mapped_column(String(10), default="")
    # portal binding (shared families; empty when non-portal or unresolved)
    portal_family_id: Mapped[str] = mapped_column(String(120), default="", index=True)
    # release classification, see RELEASE_STATUSES
    release_status: Mapped[str] = mapped_column(String(32), default="unresolved", index=True)
    release_reason: Mapped[str] = mapped_column(String(400), default="")
    notes: Mapped[str] = mapped_column(Text, default="")


class PortalFamily(Base, TimestampMixin):
    """One deduplicated official portal platform (may serve many destinations,
    nationalities and consular jurisdictions). Adapters are built per family
    and reused — never per redundant route copy."""
    __tablename__ = "portal_families"
    __table_args__ = (UniqueConstraint("family_id", name="uq_portal_family_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    family_id: Mapped[str] = mapped_column(String(120), index=True)  # stable slug
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(48), index=True)  # PORTAL_FAMILY_KINDS
    operator: Mapped[str] = mapped_column(String(200), default="")
    operator_kind: Mapped[str] = mapped_column(String(32), default="government")  # government|contracted_visa_center
    base_url: Mapped[str] = mapped_column(String(400))
    hostnames: Mapped[list] = mapped_column(JSON, default=list)
    destinations: Mapped[list] = mapped_column(JSON, default=list)  # alpha-3 served
    # nationality scoping: empty list = family itself does not restrict
    nationality_scope: Mapped[list] = mapped_column(JSON, default=list)
    supported_outcomes: Mapped[list] = mapped_column(JSON, default=list)
    account_required: Mapped[bool] = mapped_column(Boolean, default=False)
    # Curated, human-authored entry-gate declaration for portals whose
    # application form sits behind an in-session instruction sequence:
    # {actions: [{action: CLICK|SCROLL_TO_BOTTOM|CHECK, selector}], expect_path,
    #  form_ready_selector?, declared_handoffs?}. Empty dict = no gate.
    entry_gate: Mapped[dict] = mapped_column(JSON, default=dict)
    verification_status: Mapped[str] = mapped_column(String(32), default="seed_unverified", index=True)  # FAMILY_VERIFICATION
    verification_evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stale: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")


class FamilyAdapterLink(Base, TimestampMixin):
    """Exactly one adapter build per portal family (shared-portal reuse)."""
    __tablename__ = "portal_family_adapters"
    __table_args__ = (UniqueConstraint("family_id", name="uq_family_adapter"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    family_id: Mapped[str] = mapped_column(String(120), index=True)
    build_request_id: Mapped[str] = mapped_column(String(32), default="")
    candidate_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    representative_route_key: Mapped[str] = mapped_column(String(400), default="")
    status: Mapped[str] = mapped_column(String(48), default="queued", index=True)
    # gate report from release_gates (fail-closed reasons preserved verbatim)
    gate_report: Mapped[dict] = mapped_column(JSON, default=dict)
    released: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    release_tier: Mapped[str] = mapped_column(String(16), default="")
    last_error: Mapped[str] = mapped_column(Text, default="")


class GlobalBuildRun(Base, TimestampMixin):
    """One resumable orchestrator run (build-all / build-destination / ...)."""
    __tablename__ = "global_build_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    command: Mapped[str] = mapped_column(String(64), index=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)  # running|stopped|complete|failed
    checkpoint: Mapped[dict] = mapped_column(JSON, default=dict)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    stopped_reason: Mapped[str] = mapped_column(String(300), default="")


class GlobalBuildTask(Base, TimestampMixin):
    """One idempotent unit of orchestrator work. dedup_key prevents duplicate
    builds platform-wide; failed tasks record a precise, honest error class."""
    __tablename__ = "global_build_tasks"
    __table_args__ = (UniqueConstraint("dedup_key", name="uq_global_task_dedup"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(48), index=True)
    subject: Mapped[str] = mapped_column(String(200), default="", index=True)
    dedup_key: Mapped[str] = mapped_column(String(300), index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)  # queued|running|complete|failed|skipped
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    error: Mapped[str] = mapped_column(Text, default="")
    error_class: Mapped[str] = mapped_column(String(16), default="")  # transient|permanent
    result: Mapped[dict] = mapped_column(JSON, default=dict)
