"""Internal adapter release (brief §13): version-specific, route-specific,
role-protected, audited, immutable, reversible — and separate from Kimi.

Hard rules enforced in code:
- Production release requires an authenticated human administrator; actors
  that look automated (ellis/kimi/system/ai/bot/agent/auto/workflow) are
  refused outright, exactly like adapters_admin.
- Deterministic AUTO release is allowed only for the sandbox tier (synthetic /
  local / reversible-navigation functionality) — never staging risk paths,
  never production.
- Releasing binds exactly one (route, tier) → (candidate, version); the prior
  binding is deactivated, giving rollback a real target.
- The kill switch and rollback are first-class, admin-only, audited.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from .. import audit
from . import models as fm
from .static_validator import validate_candidate

TIERS = ("sandbox", "staging", "production")
_AI_ACTOR_MARKERS = ("ellis", "kimi", "system", "ai", "bot", "agent", "auto", "workflow")


class ReleaseRefused(Exception):
    pass


def _looks_like_ai(actor: str) -> bool:
    a = (actor or "").lower()
    return any(m in a for m in _AI_ACTOR_MARKERS)


def evidence_package(db, version_row: fm.AdapterCandidateVersion) -> dict:
    """The §13 evidence package an admin reviews before releasing."""
    runs = db.execute(select(fm.AdapterTestRun).where(
        fm.AdapterTestRun.candidate_version_id == version_row.id)).scalars().all()
    static = validate_candidate(version_row)
    return {
        "route_scope": (version_row.manifest or {}).get("route_scope", {}),
        "route_key": (version_row.manifest or {}).get("route_key", ""),
        "portal_operator": (version_row.manifest or {}).get("portal_operator", ""),
        "allowed_hostnames": (version_row.manifest or {}).get("allowed_hostnames", []),
        "allowed_redirect_hosts": (version_row.manifest or {}).get("allowed_redirect_hosts", []),
        "candidate_version": version_row.version,
        "content_hash": version_row.content_hash,
        "flow_nodes": len(version_row.flow or []),
        "field_mappings": len(version_row.field_mappings or []),
        "document_mappings": len(version_row.document_mappings or []),
        "evidence_rules": version_row.evidence_rules,
        "recovery": version_row.recovery,
        "kill_switch_key": version_row.kill_switch_key,
        "rollback_target": version_row.rollback_to_version,
        "known_limitations": version_row.known_limitations,
        "static_validation": static,
        "test_classifications": [
            {"layer": r.layer, "classification": r.classification, "passed": r.passed}
            for r in runs],
        "repair_of_version": version_row.repair_of_version,
    }


def release(db, *, candidate_id: str, version: int, tier: str, actor: str,
            is_admin: bool, kind: str = "human_admin") -> fm.AdapterRelease:
    if tier not in TIERS:
        raise ReleaseRefused(f"unknown tier {tier!r}")
    cand = db.get(fm.AdapterCandidate, candidate_id)
    version_row = db.execute(select(fm.AdapterCandidateVersion).where(
        fm.AdapterCandidateVersion.candidate_id == candidate_id,
        fm.AdapterCandidateVersion.version == version)).scalar_one_or_none()
    if cand is None or version_row is None:
        raise ReleaseRefused("candidate version not found")
    if version_row.quarantined:
        raise ReleaseRefused("a quarantined version can never be released")

    if kind == "deterministic_auto":
        # §13: automatic release only for synthetic/local/reversible tiers.
        if tier != "sandbox":
            raise ReleaseRefused("deterministic auto-release is limited to the sandbox tier")
    else:
        if not is_admin:
            raise ReleaseRefused(f"{tier} release requires an authenticated administrator")
        if _looks_like_ai(actor):
            raise ReleaseRefused("an AI/automated actor may not perform a release")

    # Independent evidence gate: static validation + required layers green.
    pkg = evidence_package(db, version_row)
    if not pkg["static_validation"]["passed"]:
        raise ReleaseRefused("static validation is not green — release refused")
    passed_layers = {r["classification"] for r in pkg["test_classifications"] if r["passed"]}
    required = {"STATIC_VALIDATED", "SYNTHETIC_TESTED"} if tier == "sandbox" else \
        {"STATIC_VALIDATED", "SYNTHETIC_TESTED", "CONTRACT_TESTED"}
    missing = required - passed_layers
    if missing:
        raise ReleaseRefused(f"missing required passing test layers for {tier}: {sorted(missing)}")

    rel = fm.AdapterRelease(candidate_id=candidate_id, candidate_version=version,
                            route_key=cand.route_key, tier=tier, released_by=actor,
                            release_kind=kind, evidence_package=pkg)
    db.add(rel)
    db.flush()
    _bind(db, cand, version, tier, rel.id)
    cand.status = "released"
    db.commit()
    audit.record(db, org_id="platform", application_id=candidate_id,
                 action="adapter_released",
                 detail={"version": version, "tier": tier, "kind": kind,
                         "content_hash": version_row.content_hash},
                 actor=actor)
    return rel


def _bind(db, cand: fm.AdapterCandidate, version: int, tier: str, release_id: str):
    for b in db.execute(select(fm.AdapterRuntimeBinding).where(
            fm.AdapterRuntimeBinding.route_key == cand.route_key,
            fm.AdapterRuntimeBinding.tier == tier,
            fm.AdapterRuntimeBinding.active.is_(True))).scalars().all():
        b.active = False
    db.add(fm.AdapterRuntimeBinding(route_key=cand.route_key, tier=tier,
                                    candidate_id=cand.id, candidate_version=version,
                                    release_id=release_id))


def active_binding(db, *, route_key: str, tier: str) -> fm.AdapterRuntimeBinding | None:
    return db.execute(select(fm.AdapterRuntimeBinding).where(
        fm.AdapterRuntimeBinding.route_key == route_key,
        fm.AdapterRuntimeBinding.tier == tier,
        fm.AdapterRuntimeBinding.active.is_(True))).scalars().first()


def rollback(db, *, candidate_id: str, tier: str, actor: str, is_admin: bool,
             reason: str = "") -> fm.AdapterRollback:
    if not is_admin:
        raise ReleaseRefused("rollback requires an administrator")
    cand = db.get(fm.AdapterCandidate, candidate_id)
    binding = active_binding(db, route_key=cand.route_key, tier=tier)
    if binding is None:
        raise ReleaseRefused("nothing is bound at this tier")
    current = binding.candidate_version
    target = None
    # §35.40: rollback selects the PREVIOUS released version at this tier.
    for rel in db.execute(select(fm.AdapterRelease).where(
            fm.AdapterRelease.candidate_id == candidate_id,
            fm.AdapterRelease.tier == tier).order_by(
            fm.AdapterRelease.created_at.desc())).scalars().all():
        if rel.candidate_version < current:
            target = rel
            break
    if target is None:
        raise ReleaseRefused("no earlier released version to roll back to")
    binding.active = False
    _bind(db, cand, target.candidate_version, tier, target.id)
    rb = fm.AdapterRollback(candidate_id=candidate_id, from_version=current,
                            to_version=target.candidate_version, tier=tier,
                            performed_by=actor, reason=reason[:300])
    db.add(rb)
    db.commit()
    audit.record(db, org_id="platform", application_id=candidate_id,
                 action="adapter_rolled_back",
                 detail={"tier": tier, "from": current, "to": target.candidate_version},
                 actor=actor)
    return rb


def engage_kill_switch(db, *, candidate_id: str, actor: str, is_admin: bool,
                       reason: str = "") -> fm.AdapterKillSwitch:
    if not is_admin:
        raise ReleaseRefused("kill switch requires an administrator")
    row = db.execute(select(fm.AdapterKillSwitch).where(
        fm.AdapterKillSwitch.candidate_id == candidate_id)).scalar_one_or_none()
    if row is None:
        row = fm.AdapterKillSwitch(candidate_id=candidate_id)
        db.add(row)
    row.engaged = True
    row.reason = reason[:300]
    row.engaged_by = actor
    row.engaged_at = datetime.now(timezone.utc)
    db.commit()
    audit.record(db, org_id="platform", application_id=candidate_id,
                 action="adapter_kill_switch_engaged", detail={"reason": reason[:120]},
                 actor=actor)
    return row


def clear_kill_switch(db, *, candidate_id: str, actor: str, is_admin: bool):
    if not is_admin:
        raise ReleaseRefused("kill switch requires an administrator")
    row = db.execute(select(fm.AdapterKillSwitch).where(
        fm.AdapterKillSwitch.candidate_id == candidate_id)).scalar_one_or_none()
    if row:
        row.engaged = False
        db.commit()
    audit.record(db, org_id="platform", application_id=candidate_id,
                 action="adapter_kill_switch_cleared", detail={}, actor=actor)
    return row


def kill_engaged(db, candidate_id: str) -> bool:
    row = db.execute(select(fm.AdapterKillSwitch).where(
        fm.AdapterKillSwitch.candidate_id == candidate_id)).scalar_one_or_none()
    return bool(row and row.engaged)


def quarantine(db, *, candidate_id: str, version: int, actor: str, reason: str = ""):
    version_row = db.execute(select(fm.AdapterCandidateVersion).where(
        fm.AdapterCandidateVersion.candidate_id == candidate_id,
        fm.AdapterCandidateVersion.version == version)).scalar_one_or_none()
    if version_row is None:
        raise ReleaseRefused("version not found")
    version_row.quarantined = True
    version_row.quarantine_reason = reason[:300]
    cand = db.get(fm.AdapterCandidate, candidate_id)
    cand.status = "quarantined"
    for b in db.execute(select(fm.AdapterRuntimeBinding).where(
            fm.AdapterRuntimeBinding.candidate_id == candidate_id,
            fm.AdapterRuntimeBinding.candidate_version == version,
            fm.AdapterRuntimeBinding.active.is_(True))).scalars().all():
        b.active = False
    db.add(fm.AdapterReviewTask(candidate_id=candidate_id, candidate_version=version,
                                kind="quarantine_review", reason=reason[:400]))
    db.commit()
    audit.record(db, org_id="platform", application_id=candidate_id,
                 action="adapter_version_quarantined",
                 detail={"version": version, "reason": reason[:120]}, actor=actor)
    return version_row
