"""Country-adapter administration lifecycle.

The full lifecycle an administrator drives an adapter through:

  discovered → disabled_draft → technical_review → policy_review → mock_tested
  → staging_tested → approved → limited_rollout → production_active
  (with paused / rolled_back reachable from the active states)

Hard rules (enforced here, not by policy alone):
  * No AI/automated actor may move an adapter into an ACTIVATION state
    (approved, limited_rollout, production_active) — that requires a human
    administrator. transition() rejects AI-looking actors for those states.
  * Every transition writes an immutable audit event.
  * production_enabled is True only in limited_rollout / production_active, and
    is forced False whenever the kill switch is on.
"""
from __future__ import annotations

from sqlalchemy import select, func

from . import models, audit

LIFECYCLE_STATES = [
    "discovered", "disabled_draft", "technical_review", "policy_review",
    "mock_tested", "staging_tested", "approved", "limited_rollout",
    "production_active", "paused", "rolled_back",
]

# Allowed forward/again transitions. Kept conservative and explicit.
ALLOWED_TRANSITIONS = {
    "discovered": {"disabled_draft"},
    "disabled_draft": {"technical_review"},
    "technical_review": {"policy_review", "disabled_draft"},
    "policy_review": {"mock_tested", "technical_review"},
    "mock_tested": {"staging_tested", "technical_review"},
    "staging_tested": {"approved", "technical_review"},
    "approved": {"limited_rollout", "paused"},
    "limited_rollout": {"production_active", "paused", "rolled_back"},
    "production_active": {"paused", "rolled_back"},
    "paused": {"limited_rollout", "production_active", "rolled_back", "disabled_draft"},
    "rolled_back": {"disabled_draft"},
}

# States that put the adapter live for real applicants — human admin only.
ACTIVATION_STATES = {"approved", "limited_rollout", "production_active"}
PRODUCTION_ENABLED_STATES = {"limited_rollout", "production_active"}

# Actor names that must never be allowed to activate an adapter.
_AI_ACTOR_MARKERS = ("ellis", "kimi", "system", "ai", "bot", "agent", "auto", "workflow")


class LifecycleError(Exception):
    pass


class NotAuthorizedError(Exception):
    pass


def _looks_like_ai(actor: str) -> bool:
    a = (actor or "").lower()
    return any(m in a for m in _AI_ACTOR_MARKERS)


def create_adapter(db, *, country: str, visa_type: str, config: dict, actor: str) -> models.AdapterRecord:
    rec = models.AdapterRecord(country=country, visa_type=visa_type or "tourist",
                               config=config or {}, lifecycle_state="discovered")
    db.add(rec)
    db.flush()
    _snapshot(db, rec, actor)
    _audit(db, rec.id, "adapter_created", {"country": country, "visa_type": visa_type}, actor)
    db.commit()
    return rec


def update_config(db, adapter_id: str, config_patch: dict, actor: str) -> models.AdapterRecord:
    rec = _get(db, adapter_id)
    # Config may only be edited before approval — after that a rollback/new draft
    # is required, so an approved adapter can't be silently changed.
    if rec.lifecycle_state in ACTIVATION_STATES:
        raise LifecycleError("cannot edit config of an approved/active adapter; roll back to draft first")
    merged = dict(rec.config or {})
    merged.update(config_patch or {})
    rec.config = merged
    rec.version += 1
    _snapshot(db, rec, actor)
    _audit(db, rec.id, "adapter_config_updated", {"version": rec.version,
           "keys": list((config_patch or {}).keys())}, actor)
    db.commit()
    return rec


def transition(db, adapter_id: str, to_state: str, *, actor: str, is_admin: bool,
               evidence: dict | None = None) -> models.AdapterRecord:
    rec = _get(db, adapter_id)
    if to_state not in LIFECYCLE_STATES:
        raise LifecycleError(f"unknown state {to_state}")
    allowed = ALLOWED_TRANSITIONS.get(rec.lifecycle_state, set())
    if to_state not in allowed:
        raise LifecycleError(f"illegal transition {rec.lifecycle_state} -> {to_state}")
    # Activation is human-admin only, and never by an AI/automated actor.
    if to_state in ACTIVATION_STATES:
        if not is_admin:
            raise NotAuthorizedError("activation requires an administrator")
        if _looks_like_ai(actor):
            raise NotAuthorizedError("an AI/automated actor may not activate an adapter")
    frm = rec.lifecycle_state
    rec.lifecycle_state = to_state
    rec.approval_status = "approved" if to_state in ACTIVATION_STATES else (
        "rolled_back" if to_state == "rolled_back" else "unapproved")
    rec.production_enabled = (to_state in PRODUCTION_ENABLED_STATES) and not rec.kill_switch
    if to_state in PRODUCTION_ENABLED_STATES:
        rec.monitoring_status = "monitored"
    _audit(db, rec.id, "adapter_transition",
           {"from": frm, "to": to_state, "evidence": evidence or {}, "is_admin": is_admin}, actor)
    db.commit()
    return rec


def kill(db, adapter_id: str, *, actor: str, is_admin: bool, reason: str = "") -> models.AdapterRecord:
    """Emergency kill switch — instantly disables the adapter and pauses it."""
    if not is_admin:
        raise NotAuthorizedError("kill switch requires an administrator")
    rec = _get(db, adapter_id)
    rec.kill_switch = True
    rec.production_enabled = False
    if rec.lifecycle_state in ("limited_rollout", "production_active", "approved"):
        rec.lifecycle_state = "paused"
    _audit(db, rec.id, "adapter_killed", {"reason": reason}, actor)
    db.commit()
    return rec


def clear_kill(db, adapter_id: str, *, actor: str, is_admin: bool) -> models.AdapterRecord:
    if not is_admin:
        raise NotAuthorizedError("requires an administrator")
    rec = _get(db, adapter_id)
    rec.kill_switch = False
    _audit(db, rec.id, "adapter_kill_cleared", {}, actor)
    db.commit()
    return rec


def rollback(db, adapter_id: str, to_version: int, *, actor: str, is_admin: bool) -> models.AdapterRecord:
    if not is_admin:
        raise NotAuthorizedError("rollback requires an administrator")
    rec = _get(db, adapter_id)
    snap = db.execute(select(models.AdapterVersion).where(
        models.AdapterVersion.adapter_id == adapter_id,
        models.AdapterVersion.version == to_version)).scalar_one_or_none()
    if not snap:
        raise LifecycleError(f"no version {to_version} to roll back to")
    rec.config = dict(snap.config or {})
    rec.rollback_version = to_version
    rec.lifecycle_state = "rolled_back"
    rec.production_enabled = False
    rec.approval_status = "rolled_back"
    _audit(db, rec.id, "adapter_rolled_back", {"to_version": to_version}, actor)
    db.commit()
    return rec


def list_adapters(db) -> list[dict]:
    recs = db.execute(select(models.AdapterRecord)).scalars().all()
    return [to_dict(r) for r in recs]


def coverage_matrix(db) -> list[dict]:
    """Honest coverage: what level of service each country/visa-type has."""
    out = []
    for r in db.execute(select(models.AdapterRecord)).scalars().all():
        out.append({
            "country": r.country, "visa_type": r.visa_type,
            "lifecycle_state": r.lifecycle_state,
            "service_level": _service_level(r),
            "production_enabled": r.production_enabled,
            "kill_switch": r.kill_switch,
        })
    return out


def _service_level(r: models.AdapterRecord) -> str:
    if r.kill_switch:
        return "paused_killswitch"
    return {
        "discovered": "requirements_only",
        "disabled_draft": "document_preparation",
        "technical_review": "form_preparation",
        "policy_review": "form_preparation",
        "mock_tested": "applicant_controlled_handoff",
        "staging_tested": "staging_tested_automation",
        "approved": "staging_tested_automation",
        "limited_rollout": "production_approved_automation",
        "production_active": "production_approved_automation",
        "paused": "applicant_controlled_handoff",
        "rolled_back": "applicant_controlled_handoff",
    }.get(r.lifecycle_state, "requirements_only")


def versions(db, adapter_id: str) -> list[dict]:
    rows = db.execute(select(models.AdapterVersion).where(
        models.AdapterVersion.adapter_id == adapter_id).order_by(
        models.AdapterVersion.version.desc())).scalars().all()
    return [{"version": v.version, "lifecycle_state": v.lifecycle_state,
             "created_by": v.created_by, "created_at": v.created_at.isoformat() if v.created_at else None}
            for v in rows]


def adapter_audit(db, adapter_id: str) -> list[dict]:
    return [{"seq": e.seq, "action": e.action, "actor": e.actor, "detail": e.detail}
            for e in audit.for_application(db, adapter_id)]


def to_dict(r: models.AdapterRecord) -> dict:
    return {"id": r.id, "country": r.country, "visa_type": r.visa_type,
            "config": r.config, "lifecycle_state": r.lifecycle_state,
            "approval_status": r.approval_status, "production_enabled": r.production_enabled,
            "kill_switch": r.kill_switch, "version": r.version,
            "monitoring_status": r.monitoring_status, "rollback_version": r.rollback_version,
            "service_level": _service_level(r),
            "allowed_transitions": sorted(ALLOWED_TRANSITIONS.get(r.lifecycle_state, set()))}


# ---- internals ----
def _get(db, adapter_id: str) -> models.AdapterRecord:
    rec = db.get(models.AdapterRecord, adapter_id)
    if not rec:
        raise LifecycleError("adapter not found")
    return rec


def _snapshot(db, rec: models.AdapterRecord, actor: str):
    db.add(models.AdapterVersion(adapter_id=rec.id, version=rec.version,
                                 config=dict(rec.config or {}), lifecycle_state=rec.lifecycle_state,
                                 created_by=actor))


def _audit(db, adapter_id: str, action: str, detail: dict, actor: str):
    # Immutable, append-only. Reuses the audit table keyed by adapter id.
    audit.record(db, org_id="platform", application_id=adapter_id,
                 action=action, detail=detail, actor=actor)
