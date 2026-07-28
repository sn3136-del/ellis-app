"""Adapter-factory ORM models (brief §32).

Versioned, immutable-where-it-matters records for the automated adapter
pipeline: build requests + consent, credential-free reconnaissance, typed
specifications, candidate versions, layered test runs, repairs, review tasks,
internal releases, runtime bindings, executions, checkpoints, outcome
evidence, failures, kill switches, rollbacks and portal-policy reviews.

Design notes (adapted per §32 "add or adapt"):
- AdapterFlowNode / AdapterEvidenceRule live as validated JSON inside
  AdapterSpecification.flow / AdapterCandidateVersion.flow — a flow is only
  ever read or replaced as one immutable unit, never node-by-node.
- AdapterTestArtifact rows hold sanitized artifacts per test run.
- No browser secrets, payment data, or Live View URLs are ever stored here.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import String, Integer, Boolean, Text, JSON, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class _Stamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class AdapterBuildRequest(Base, _Stamped):
    """One durable applicant-initiated request to build a route connector,
    with the §10 consent recorded on it (AdapterBuildConsent adapted in)."""
    __tablename__ = "adapter_build_requests"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), default="")
    application_id: Mapped[str] = mapped_column(String(32), index=True, default="")
    route_key: Mapped[str] = mapped_column(String(400), index=True)
    destination: Mapped[str] = mapped_column(String(80), default="")
    visa_type: Mapped[str] = mapped_column(String(80), default="tourist")
    research_version: Mapped[str] = mapped_column(String(64), default="")
    portal_evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    jurisdiction_evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    standing_authorization_id: Mapped[str] = mapped_column(String(32), default="")
    runtime_mode: Mapped[str] = mapped_column(String(30), default="")
    # §10 consent (recorded verbatim; the build may not start without it)
    consent_given: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_text_version: Mapped[str] = mapped_column(String(20), default="")
    consent_locale: Mapped[str] = mapped_column(String(12), default="en")
    consent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    consent_by: Mapped[str] = mapped_column(String(64), default="")
    # Durable build state machine (§11) — 30 states, checkpointed per stage.
    state: Mapped[str] = mapped_column(String(40), default="REQUESTED", index=True)
    state_history: Mapped[list] = mapped_column(JSON, default=list)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[list] = mapped_column(JSON, default=list)   # honest progress notes
    error: Mapped[str] = mapped_column(Text, default="")
    current_candidate_id: Mapped[str] = mapped_column(String(32), default="")


class AdapterReconJob(Base, _Stamped):
    """Credential-free reconnaissance of one public portal (§14)."""
    __tablename__ = "adapter_recon_jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    build_request_id: Mapped[str] = mapped_column(String(32), index=True)
    org_id: Mapped[str] = mapped_column(String(64), index=True)
    portal_hostnames: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    # queued | running | complete | failed
    pages_observed: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")


class AdapterReconArtifact(Base, _Stamped):
    """One SANITIZED page-structure observation. Never contains values,
    cookies, tokens, personal data, or free portal text beyond labels."""
    __tablename__ = "adapter_recon_artifacts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    recon_job_id: Mapped[str] = mapped_column(String(32), index=True)
    page_key: Mapped[str] = mapped_column(String(120), default="")
    hostname: Mapped[str] = mapped_column(String(200), default="")
    url_pattern: Mapped[str] = mapped_column(String(500), default="")
    structure: Mapped[dict] = mapped_column(JSON, default=dict)   # sanitized structure only
    content_class: Mapped[str] = mapped_column(String(40), default="public_page")


class AdapterSpecification(Base, _Stamped):
    """Typed adapter specification generated from recon artifacts (§16).
    Immutable: repairs create a new specification row."""
    __tablename__ = "adapter_specifications"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    build_request_id: Mapped[str] = mapped_column(String(32), index=True)
    route_key: Mapped[str] = mapped_column(String(400), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    portal_operator: Mapped[str] = mapped_column(String(200), default="")
    allowed_hostnames: Mapped[list] = mapped_column(JSON, default=list)
    allowed_redirect_hosts: Mapped[list] = mapped_column(JSON, default=list)
    flow: Mapped[list] = mapped_column(JSON, default=list)         # typed flow nodes
    field_mappings: Mapped[list] = mapped_column(JSON, default=list)
    document_mappings: Mapped[list] = mapped_column(JSON, default=list)
    generation_basis: Mapped[dict] = mapped_column(JSON, default=dict)  # artifact citations
    generator: Mapped[str] = mapped_column(String(64), default="")      # kimi model or seam


class AdapterCandidate(Base, _Stamped):
    """One adapter identity for one exact route (head row; versions append)."""
    __tablename__ = "adapter_candidates"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    build_request_id: Mapped[str] = mapped_column(String(32), index=True)
    route_key: Mapped[str] = mapped_column(String(400), index=True)
    adapter_id: Mapped[str] = mapped_column(String(120), unique=True)
    current_version: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default="generating", index=True)
    # generating | testing | tests_passed | tests_failed | quarantined |
    # release_recommended | released | superseded | disabled


class AdapterCandidateVersion(Base, _Stamped):
    """One immutable generated candidate version (§17). Repairs create the
    next version; a released version is never modified in place."""
    __tablename__ = "adapter_candidate_versions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    candidate_id: Mapped[str] = mapped_column(String(32), index=True)
    version: Mapped[int] = mapped_column(Integer)
    specification_id: Mapped[str] = mapped_column(String(32), default="")
    manifest: Mapped[dict] = mapped_column(JSON, default=dict)
    flow: Mapped[list] = mapped_column(JSON, default=list)
    field_mappings: Mapped[list] = mapped_column(JSON, default=list)
    document_mappings: Mapped[list] = mapped_column(JSON, default=list)
    evidence_rules: Mapped[dict] = mapped_column(JSON, default=dict)
    recovery: Mapped[dict] = mapped_column(JSON, default=dict)
    kill_switch_key: Mapped[str] = mapped_column(String(120), default="")
    rollback_to_version: Mapped[int] = mapped_column(Integer, default=0)
    known_limitations: Mapped[list] = mapped_column(JSON, default=list)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    storage_dir: Mapped[str] = mapped_column(String(400), default="")
    created_by: Mapped[str] = mapped_column(String(64), default="")
    repair_of_version: Mapped[int] = mapped_column(Integer, default=0)
    quarantined: Mapped[bool] = mapped_column(Boolean, default=False)
    quarantine_reason: Mapped[str] = mapped_column(String(300), default="")


class AdapterTestPlan(Base, _Stamped):
    __tablename__ = "adapter_test_plans"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    candidate_version_id: Mapped[str] = mapped_column(String(32), index=True)
    layers: Mapped[list] = mapped_column(JSON, default=list)   # ordered §26 layers
    required_for_release: Mapped[list] = mapped_column(JSON, default=list)


class AdapterTestRun(Base, _Stamped):
    """One test-layer execution with a TRUTHFUL classification (§26)."""
    __tablename__ = "adapter_test_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    candidate_version_id: Mapped[str] = mapped_column(String(32), index=True)
    layer: Mapped[str] = mapped_column(String(60))
    classification: Mapped[str] = mapped_column(String(60), default="")
    # STATIC_VALIDATED | SYNTHETIC_TESTED | CONTRACT_TESTED |
    # PUBLIC_NAVIGATION_TESTED | AUTHENTICATED_NAVIGATION_TESTED |
    # OFFICIAL_SANDBOX_TESTED | STAGING_TESTED | CONTROLLED_PRODUCTION_VERIFIED
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    executed_by: Mapped[str] = mapped_column(String(64), default="")


class AdapterTestArtifact(Base, _Stamped):
    __tablename__ = "adapter_test_artifacts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    test_run_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(60), default="")
    content: Mapped[dict] = mapped_column(JSON, default=dict)    # sanitized only


class AdapterRepairAttempt(Base, _Stamped):
    __tablename__ = "adapter_repair_attempts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    candidate_id: Mapped[str] = mapped_column(String(32), index=True)
    failed_version: Mapped[int] = mapped_column(Integer)
    new_version: Mapped[int] = mapped_column(Integer, default=0)
    failure_class: Mapped[str] = mapped_column(String(60), default="")
    sanitized_evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    outcome: Mapped[str] = mapped_column(String(30), default="pending")
    # pending | repaired | stopped | quarantined
    stop_reason: Mapped[str] = mapped_column(String(300), default="")


class AdapterReviewTask(Base, _Stamped):
    __tablename__ = "adapter_review_tasks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    candidate_id: Mapped[str] = mapped_column(String(32), index=True)
    candidate_version: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(60), default="manual_review")
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    resolved_by: Mapped[str] = mapped_column(String(64), default="")
    resolution_note: Mapped[str] = mapped_column(Text, default="")


class AdapterRelease(Base, _Stamped):
    """One internal release action: version-specific, route-specific,
    role-protected, audited, immutable, reversible (§13)."""
    __tablename__ = "adapter_releases"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    candidate_id: Mapped[str] = mapped_column(String(32), index=True)
    candidate_version: Mapped[int] = mapped_column(Integer)
    route_key: Mapped[str] = mapped_column(String(400), index=True)
    tier: Mapped[str] = mapped_column(String(30), index=True)
    # sandbox | staging | production
    released_by: Mapped[str] = mapped_column(String(64))
    release_kind: Mapped[str] = mapped_column(String(40), default="human_admin")
    # human_admin | deterministic_auto (sandbox/staging tiers only)
    evidence_package: Mapped[dict] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    revoked_by: Mapped[str] = mapped_column(String(64), default="")
    revoked_reason: Mapped[str] = mapped_column(String(300), default="")
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class AdapterCapabilityRelease(Base, _Stamped):
    """A single capability of a released adapter, auto-released by the
    deterministic AutoReleasePolicyEngine when that capability's OBJECTIVE
    evidence gate passes — never by an administrator. Records the evidence that
    justified it (auditable) and can be revoked by kill switch / quarantine.
    Exactly one active row per (route_key, capability)."""
    __tablename__ = "adapter_capability_releases"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    route_key: Mapped[str] = mapped_column(String(400), index=True)
    capability: Mapped[str] = mapped_column(String(40), index=True)
    candidate_id: Mapped[str] = mapped_column(String(32), index=True)
    candidate_version: Mapped[int] = mapped_column(Integer)
    released_by: Mapped[str] = mapped_column(String(64), default="auto-release-policy-engine")
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    revoked_by: Mapped[str] = mapped_column(String(64), default="")
    revoked_reason: Mapped[str] = mapped_column(String(300), default="")
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class AdapterRuntimeBinding(Base, _Stamped):
    """What the deterministic runtime may execute right now, per route+tier.
    Exactly one active binding per (route_key, tier)."""
    __tablename__ = "adapter_runtime_bindings"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    route_key: Mapped[str] = mapped_column(String(400), index=True)
    tier: Mapped[str] = mapped_column(String(30), index=True)
    candidate_id: Mapped[str] = mapped_column(String(32))
    candidate_version: Mapped[int] = mapped_column(Integer)
    release_id: Mapped[str] = mapped_column(String(32))
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class AdapterExecution(Base, _Stamped):
    """One runtime execution of a released candidate version for one case."""
    __tablename__ = "adapter_executions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(64), index=True)
    application_id: Mapped[str] = mapped_column(String(32), index=True)
    candidate_id: Mapped[str] = mapped_column(String(32), index=True)
    candidate_version: Mapped[int] = mapped_column(Integer)
    tier: Mapped[str] = mapped_column(String(30), default="")
    status: Mapped[str] = mapped_column(String(30), default="running", index=True)
    # running | paused_applicant_action | completed | failed | killed |
    # outcome_uncertain
    current_node: Mapped[str] = mapped_column(String(120), default="")
    error: Mapped[str] = mapped_column(Text, default="")


class AdapterCheckpoint(Base, _Stamped):
    """Redacted per-node checkpoint for resume after any restart."""
    __tablename__ = "adapter_checkpoints"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    execution_id: Mapped[str] = mapped_column(String(32), index=True)
    node_id: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(30))   # ok | handoff | uncertain | failed
    detail: Mapped[dict] = mapped_column(JSON, default=dict)   # redacted


class AdapterOutcomeEvidence(Base, _Stamped):
    """Privacy-preserving outcome evidence (§20). Never request/response
    bodies, cookies, tokens, or personal values."""
    __tablename__ = "adapter_outcome_evidence"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    execution_id: Mapped[str] = mapped_column(String(32), index=True)
    node_id: Mapped[str] = mapped_column(String(120), default="")
    strength: Mapped[int] = mapped_column(Integer, default=7)  # §20 ladder, 1 strongest
    kind: Mapped[str] = mapped_column(String(60), default="")
    hostname: Mapped[str] = mapped_column(String(200), default="")
    endpoint_pattern: Mapped[str] = mapped_column(String(300), default="")
    method: Mapped[str] = mapped_column(String(10), default="")
    status_code: Mapped[int] = mapped_column(Integer, default=0)
    content_type: Mapped[str] = mapped_column(String(100), default="")
    response_keys: Mapped[list] = mapped_column(JSON, default=list)  # names only
    state_category: Mapped[str] = mapped_column(String(60), default="")


class AdapterFailure(Base, _Stamped):
    __tablename__ = "adapter_failures"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    candidate_id: Mapped[str] = mapped_column(String(32), index=True)
    candidate_version: Mapped[int] = mapped_column(Integer, default=0)
    execution_id: Mapped[str] = mapped_column(String(32), default="")
    node_id: Mapped[str] = mapped_column(String(120), default="")
    failure_class: Mapped[str] = mapped_column(String(60), default="")
    sanitized_detail: Mapped[dict] = mapped_column(JSON, default=dict)
    irreversible_context: Mapped[bool] = mapped_column(Boolean, default=False)


class AdapterKillSwitch(Base, _Stamped):
    """First-class kill switch per candidate (checked before every node)."""
    __tablename__ = "adapter_kill_switches"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    candidate_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    engaged: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str] = mapped_column(String(300), default="")
    engaged_by: Mapped[str] = mapped_column(String(64), default="")
    engaged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class AdapterRollback(Base, _Stamped):
    __tablename__ = "adapter_rollbacks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    candidate_id: Mapped[str] = mapped_column(String(32), index=True)
    from_version: Mapped[int] = mapped_column(Integer)
    to_version: Mapped[int] = mapped_column(Integer)
    tier: Mapped[str] = mapped_column(String(30), default="")
    performed_by: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(String(300), default="")


class PortalAutomationPolicyReview(Base, _Stamped):
    """Recorded review of whether a portal permits representative automation."""
    __tablename__ = "portal_automation_policy_reviews"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    route_key: Mapped[str] = mapped_column(String(400), index=True)
    portal_operator: Mapped[str] = mapped_column(String(200), default="")
    hostnames: Mapped[list] = mapped_column(JSON, default=list)
    decision: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    # pending | automation_permitted | handoff_only | prohibited
    basis: Mapped[dict] = mapped_column(JSON, default=dict)   # cited evidence
    reviewed_by: Mapped[str] = mapped_column(String(64), default="")
    notes: Mapped[str] = mapped_column(Text, default="")


class PortalFieldOptionCache(Base, _Stamped):
    """The option list a portal's OWN page offered for one select field, read
    live during an earlier run of this exact adapter version.

    Purpose: ask the applicant with a real dropdown BEFORE any browser session
    opens, instead of making them wait a minute into a run to learn what the
    government form will accept. Never a hand-authored list — an empty cache
    simply means the portal has not been read yet, and the applicant types a
    free-text answer that the fill step verifies against the live page.

    Version-keyed on purpose: a new adapter version re-reads the portal rather
    than inheriting a list that may no longer match the form.
    """
    __tablename__ = "portal_field_option_cache"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    candidate_id: Mapped[str] = mapped_column(String(32), index=True)
    candidate_version: Mapped[int] = mapped_column(Integer)
    # The Ellis answer key (node input_source) — never a selector.
    field_key: Mapped[str] = mapped_column(String(80), index=True)
    # The flow node this list belongs to: one answer key can be collected by
    # two widgets on different pages, whose lists must not overwrite each other.
    node_id: Mapped[str] = mapped_column(String(120), default="", index=True)
    options: Mapped[list] = mapped_column(JSON, default=list)
    # True only when the portal's widget itself ran out of options. A PARTIAL
    # read is still recorded (it improves on nothing) but is never presented as
    # the portal's whole list — the applicant would be unable to pick a value
    # the form really offers.
    complete: Mapped[bool] = mapped_column(Boolean, default=False)
    # How many times this EXACT list was seen. A list that depends on another
    # answer (wards of the province this applicant chose) or on who is signed
    # in never repeats, so it never reaches the corroboration bar and is never
    # re-served to a different applicant.
    observations: Mapped[int] = mapped_column(Integer, default=1)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
