"""Independent, deterministic AutoReleasePolicyEngine (brief "NO ROUTINE HUMAN
RELEASE").

Kimi generates and repairs adapters, but it must never approve or release its
own work. This engine is that independent authority: a pure, deterministic
policy — NO model, NO Kimi output, NO credential access — that automatically
releases each capability of a built adapter the moment that capability's
OBJECTIVE evidence gate passes. No administrator, no staging/production sign-off
on the normal path.

Two release surfaces, both deterministic and both re-checking their own gate:

1. The whole-adapter REVERSIBLE (sandbox) binding — `evaluate_build` — via
   `release.release(kind="deterministic_auto")` (code-limited to sandbox).

2. Per-capability releases — `evaluate_capabilities` — one row per capability
   whose gate passes, recorded in AdapterCapabilityRelease. The gate is
   STRUCTURAL and objective: a capability releases only when the adapter's typed
   flow actually contains the safely-built nodes for it — the required applicant
   handoffs, reconciliation-before-irreversible, retry bounds, and official
   success-evidence — AND every required test layer is green. A capability whose
   safe structure the recon never observed simply does not release (honest).

What this NEVER does: perform an irreversible action, see a credential/OTP/card,
or bypass an applicant step. Capability RELEASE is build-time ("the adapter may
do X safely"); the runtime still pauses at every APPLICANT_HANDOFF (CAPTCHA,
OTP/passkey, identity, legally-personal declaration, exact payment) and enforces
the case's standing authorization, signed final review and satisfied payment
before any irreversible node — see runtime.execute_released_route_live.

Why this cannot be Kimi approving itself: the engine reads only persisted test
results and the typed-flow structure; it runs no model output; the sandbox
release primitive is code-limited to the reversible tier; capability releases
touch no secret and perform no external action.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from . import models as fm
from . import release as releasesvc

# Reversible capabilities — released by the sandbox binding (evaluate_build).
REVERSIBLE_CAPABILITIES = (
    "route_research", "portal_discovery", "account_registration_prep",
    "form_completion", "document_upload", "appointment_search",
    "submission_preparation", "status_tracking",
)
# Irreversible capabilities — each released independently by its objective gate.
IRREVERSIBLE_CAPABILITIES = (
    "account_registration", "appointment_booking", "payment_preparation",
    "submission_execution",
)

# The standing-authorization action each capability requires AT RUNTIME (checked
# per-case by the runner, never at release time — release is route-level).
CAPABILITY_ACTIONS = {
    "account_registration": "create_or_use_portal_account",
    "appointment_booking": "book_appointment_within_preferences",
    "payment_preparation": "navigate_to_payment",
    "submission_execution": "submit_after_signed_final_review",
}

AUTO_ACTOR = "auto-release-policy-engine"


class AutoReleaseResult:
    def __init__(self, released: bool, tier: str = "", reason: str = "",
                 release_id: str = "", capabilities: tuple = ()):
        self.released = released
        self.tier = tier
        self.reason = reason
        self.release_id = release_id
        self.capabilities = capabilities

    def as_dict(self) -> dict:
        return {"released": self.released, "tier": self.tier, "reason": self.reason,
                "release_id": self.release_id, "capabilities": list(self.capabilities)}


def _release_recommended(req: fm.AdapterBuildRequest) -> bool:
    return req.state in ("RELEASE_RECOMMENDED", "AWAITING_INTERNAL_RELEASE",
                         "RELEASED_SANDBOX", "RELEASED_STAGING", "RELEASED_PRODUCTION")


def _current_version_row(db, cand):
    return db.execute(select(fm.AdapterCandidateVersion).where(
        fm.AdapterCandidateVersion.candidate_id == cand.id,
        fm.AdapterCandidateVersion.version == cand.current_version)).scalar_one_or_none()


def _passed_layers(db, version_row) -> set:
    runs = db.execute(select(fm.AdapterTestRun).where(
        fm.AdapterTestRun.candidate_version_id == version_row.id,
        fm.AdapterTestRun.passed.is_(True))).scalars().all()
    return {r.classification for r in runs}


# --------------------------------------------------------------------------- #
#  Objective per-capability structural gates over the typed flow.             #
# --------------------------------------------------------------------------- #
def _has_handoff(flow, kind) -> bool:
    return any(n.get("action") == "APPLICANT_HANDOFF" and n.get("handoff_kind") == kind
               for n in flow)


def _has_action(flow, action) -> bool:
    return any(n.get("action") == action for n in flow)


def _has_reconcile(flow) -> bool:
    return any(n.get("action") == "RECONCILE_OUTCOME" for n in flow)


def _verify_has(flow, category) -> bool:
    for n in flow:
        if n.get("action") != "VERIFY_EVIDENCE":
            continue
        if any(e.get("category") == category for e in (n.get("success_evidence") or [])):
            return True
    return False


def _irreversible_with_evidence(flow, category) -> list:
    out = []
    for n in flow:
        if n.get("irreversibility") != "irreversible":
            continue
        if any(e.get("category") == category for e in (n.get("success_evidence") or [])):
            out.append(n)
    return out


def _bounded_reconcile_first(node) -> bool:
    return (node.get("retry_class") == "reconcile_first"
            and int(node.get("max_retries") or 0) <= 1)


def capability_gate(version_row, capability: str) -> tuple[bool, list, dict]:
    """Deterministic structural gate for one irreversible capability. Returns
    (ok, problems, evidence). The typed flow must contain the safely-built nodes
    for the capability — no model output is consulted."""
    flow = version_row.flow or []
    problems: list[str] = []
    ev: dict = {}
    if capability == "account_registration":
        # An authenticated session must be reached and proven by evidence, and
        # a duplicate is reconciled (an existing session is used, never
        # re-created). Two shapes qualify: the applicant signs in personally
        # (credentials handoff), OR Ellis creates the account itself
        # (REGISTER_ACCOUNT: applicant email + fresh vaulted password,
        # reconcile-first, emailed code as an OTP handoff).
        has_register = any(n.get("action") == "REGISTER_ACCOUNT" for n in flow)
        if not (_has_handoff(flow, "credentials") or has_register):
            problems.append("no credentials handoff or REGISTER_ACCOUNT — "
                            "registration/login not observed")
        if has_register:
            if not _has_reconcile(flow):
                problems.append("REGISTER_ACCOUNT without reconcile (duplicate-"
                                "account guard)")
            if not _has_handoff(flow, "otp"):
                problems.append("REGISTER_ACCOUNT without an OTP handoff for the "
                                "emailed verification code")
        if not _verify_has(flow, "session_authenticated"):
            problems.append("no authenticated-session success evidence")
        ev = {"credentials_handoff": _has_handoff(flow, "credentials"),
              "ellis_registers": has_register,
              "session_evidence": _verify_has(flow, "session_authenticated"),
              "duplicate_reconciliation": "verify_authenticated_session_before_create"}
    elif capability == "appointment_booking":
        book = _irreversible_with_evidence(flow, "appointment_booked")
        if not _has_action(flow, "READ_APPOINTMENT_INVENTORY"):
            problems.append("no official appointment-inventory read")
        if not book:
            problems.append("no booking node with official success evidence")
        if not _has_reconcile(flow):
            problems.append("no booking reconciliation (duplicate-booking guard)")
        if book and not all(_bounded_reconcile_first(n) for n in book):
            problems.append("booking is not reconcile-first / retry-bounded")
        ev = {"inventory_read": _has_action(flow, "READ_APPOINTMENT_INVENTORY"),
              "reconcile": _has_reconcile(flow), "booking_nodes": len(book)}
    elif capability == "payment_preparation":
        # Ellis reads the official fee for an EXACT-amount confirmation; the
        # applicant confirms + pays personally at the handoff (never Ellis, never
        # Kimi). Uncertain outcomes reconcile via the flow's reconcile-first.
        if not _has_action(flow, "READ_FEE"):
            problems.append("no official fee read for exact-amount confirmation")
        if not _has_handoff(flow, "payment_credentials"):
            problems.append("no exact-amount applicant payment handoff")
        ev = {"fee_read": _has_action(flow, "READ_FEE"),
              "payment_handoff": _has_handoff(flow, "payment_credentials")}
    elif capability == "submission_execution":
        submit = _irreversible_with_evidence(flow, "submission_accepted")
        if not _has_handoff(flow, "legally_personal_declaration"):
            problems.append("no legally-personal declaration handoff")
        if not submit:
            problems.append("no submit node with official success evidence")
        if not _verify_has(flow, "submitted"):
            problems.append("no official-record submission verification")
        if not _has_reconcile(flow):
            problems.append("no submission reconciliation (duplicate-submission guard)")
        if submit and not all(_bounded_reconcile_first(n) for n in submit):
            problems.append("submit is not reconcile-first / retry-bounded")
        ev = {"declaration_handoff": _has_handoff(flow, "legally_personal_declaration"),
              "official_record_verify": _verify_has(flow, "submitted"),
              "reconcile": _has_reconcile(flow), "submit_nodes": len(submit)}
    else:
        return False, [f"unknown capability {capability!r}"], {}
    return (not problems), problems, ev


def _upsert_capability(db, *, route_key, capability, cand, evidence) -> fm.AdapterCapabilityRelease:
    row = db.execute(select(fm.AdapterCapabilityRelease).where(
        fm.AdapterCapabilityRelease.route_key == route_key,
        fm.AdapterCapabilityRelease.capability == capability,
        fm.AdapterCapabilityRelease.active.is_(True))).scalars().first()
    if row is not None:
        return row
    row = fm.AdapterCapabilityRelease(
        route_key=route_key, capability=capability, candidate_id=cand.id,
        candidate_version=cand.current_version, released_by=AUTO_ACTOR,
        evidence=evidence, active=True)
    db.add(row)
    db.flush()
    return row


def evaluate_capabilities(db, request_id: str) -> dict:
    """Independently evaluate + auto-release each irreversible capability whose
    objective gate passes. No administrator. Idempotent."""
    out: dict = {"released": [], "capabilities": {}}
    req = db.get(fm.AdapterBuildRequest, request_id)
    if req is None or not _release_recommended(req):
        out["reason"] = "build not release-recommended"
        return out
    cand = db.get(fm.AdapterCandidate, req.current_candidate_id)
    if cand is None:
        out["reason"] = "no candidate"
        return out
    if cand.status == "quarantined" or releasesvc.kill_engaged(db, cand.id):
        out["reason"] = "candidate quarantined or kill-switched"
        return out
    version_row = _current_version_row(db, cand)
    if version_row is None:
        out["reason"] = "no candidate version"
        return out
    layers = _passed_layers(db, version_row)
    behavioral_ok = bool({"SYNTHETIC_TESTED", "LIVE_STRUCTURAL_TESTED"} & layers)
    base_ok = ("STATIC_VALIDATED" in layers and "CONTRACT_TESTED" in layers
               and behavioral_ok)
    for cap in IRREVERSIBLE_CAPABILITIES:
        if not base_ok:
            out["capabilities"][cap] = {"released": False,
                                        "reasons": ["required test layers not green"]}
            continue
        ok, problems, ev = capability_gate(version_row, cap)
        if ok:
            row = _upsert_capability(db, route_key=cand.route_key, capability=cap,
                                     cand=cand, evidence={**ev, "layers": sorted(layers),
                                                          "runtime_action": CAPABILITY_ACTIONS[cap]})
            out["released"].append(cap)
            out["capabilities"][cap] = {"released": True, "release_id": row.id, "evidence": ev}
        else:
            out["capabilities"][cap] = {"released": False, "reasons": problems}
    db.commit()
    return out


def revoke_capabilities(db, *, candidate_id: str, reason: str, actor: str) -> int:
    """Deactivate every active capability release bound to a candidate (called by
    kill switch / quarantine). Returns how many were revoked."""
    rows = db.execute(select(fm.AdapterCapabilityRelease).where(
        fm.AdapterCapabilityRelease.candidate_id == candidate_id,
        fm.AdapterCapabilityRelease.active.is_(True))).scalars().all()
    for r in rows:
        r.active = False
        r.revoked_by = actor
        r.revoked_reason = reason[:300]
        r.revoked_at = datetime.now(timezone.utc)
    if rows:
        db.commit()
    return len(rows)


def capability_released(db, *, route_key: str, capability: str) -> fm.AdapterCapabilityRelease | None:
    return db.execute(select(fm.AdapterCapabilityRelease).where(
        fm.AdapterCapabilityRelease.route_key == route_key,
        fm.AdapterCapabilityRelease.capability == capability,
        fm.AdapterCapabilityRelease.active.is_(True))).scalars().first()


def evaluate_build(db, request_id: str) -> AutoReleaseResult:
    """Auto-release the reversible (sandbox) binding when its evidence gate
    passes, THEN evaluate + auto-release each irreversible capability by its own
    objective gate. Idempotent."""
    req = db.get(fm.AdapterBuildRequest, request_id)
    if req is None:
        return AutoReleaseResult(False, reason="build request not found")
    if not _release_recommended(req):
        return AutoReleaseResult(False, reason=f"build not release-recommended (state {req.state})")
    cand = db.get(fm.AdapterCandidate, req.current_candidate_id)
    if cand is None:
        return AutoReleaseResult(False, reason="no candidate on build")

    if cand.status == "quarantined" or releasesvc.kill_engaged(db, cand.id):
        return AutoReleaseResult(False, reason="candidate quarantined or kill-switched")

    existing = releasesvc.active_binding(db, route_key=cand.route_key, tier="sandbox")
    if existing is not None:
        evaluate_capabilities(db, request_id)   # idempotent; catch up any new caps
        return AutoReleaseResult(True, tier="sandbox", reason="already released",
                                 release_id=existing.release_id,
                                 capabilities=REVERSIBLE_CAPABILITIES)

    version = cand.current_version
    try:
        rel = releasesvc.release(db, candidate_id=cand.id, version=version,
                                 tier="sandbox", actor=AUTO_ACTOR, is_admin=False,
                                 kind="deterministic_auto")
    except releasesvc.ReleaseRefused as e:
        return AutoReleaseResult(False, tier="sandbox", reason=str(e))
    if req.state == "AWAITING_INTERNAL_RELEASE":
        try:
            from .statemachine import transition
            transition(req, "RELEASED_SANDBOX", "auto-released (sandbox) by policy engine")
            db.commit()
        except Exception:  # noqa: BLE001 - release recorded regardless of label
            db.rollback()
    evaluate_capabilities(db, request_id)
    return AutoReleaseResult(True, tier="sandbox", reason="evidence gates passed",
                             release_id=rel.id, capabilities=REVERSIBLE_CAPABILITIES)


def released_capabilities(db, route_key: str) -> dict:
    """Per-capability release status for a route. Reversible capabilities come
    from the auto-released sandbox binding; irreversible ones from their own
    automatic per-capability releases (never an administrator)."""
    sandbox = releasesvc.active_binding(db, route_key=route_key, tier="sandbox")
    caps: dict[str, dict] = {}
    for c in REVERSIBLE_CAPABILITIES:
        caps[c] = {"released": sandbox is not None,
                   "via": "sandbox_auto" if sandbox is not None else None,
                   "reversible": True}
    for c in IRREVERSIBLE_CAPABILITIES:
        row = capability_released(db, route_key=route_key, capability=c)
        caps[c] = {"released": row is not None,
                   "via": "capability_auto" if row is not None else None,
                   "reversible": False,
                   "runtime_action": CAPABILITY_ACTIONS[c]}
    any_irrev = any(caps[c]["released"] for c in IRREVERSIBLE_CAPABILITIES)
    return {"route_key": route_key,
            "any_released": bool(sandbox) or any_irrev,
            "capabilities": caps}
