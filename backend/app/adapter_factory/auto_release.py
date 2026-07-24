"""Independent, deterministic AutoReleasePolicyEngine (brief "NO ROUTINE HUMAN
RELEASE").

Kimi generates and repairs adapters, but it must never approve or release its
own work. This engine is that independent authority: a pure, deterministic
policy — NO model, NO Kimi output, NO credential access — that automatically
releases a built adapter's REVERSIBLE capability (the sandbox tier: synthetic /
local / navigation-only, never a payment/booking/submission) the moment the
independent evidence gate passes. It removes the routine administrator "click
release" break for everything that is safe to release without a human, while
leaving genuinely irreversible tiers (staging/production) on the existing
human-administrator path — which is also what the "no unauthorized irreversible
action" rule requires.

Why this cannot be Kimi approving itself:
 - The engine never receives or runs model output; it only reads persisted test
   results and the static validator's verdict.
 - The release primitive it calls (`release.release(kind="deterministic_auto")`)
   is code-limited to the sandbox tier and re-checks the evidence gate itself.
 - It touches no secret, credential, session, or payment.

Capability model: the spec asks for capabilities released independently. We map
the reversible capabilities onto the auto-released sandbox binding and keep the
irreversible ones gated on the human-released staging/production tiers, so
useful progress (research, discovery, form completion, uploads, appointment
search) is never blocked waiting on a later irreversible capability.
"""
from __future__ import annotations

from sqlalchemy import select

from . import models as fm
from . import release as releasesvc

# Capabilities that are reversible (no irreversible external effect) and are
# therefore released by the automatic sandbox release.
REVERSIBLE_CAPABILITIES = (
    "route_research", "portal_discovery", "account_registration_prep",
    "form_completion", "document_upload", "appointment_search",
    "submission_preparation", "status_tracking",
)
# Capabilities that create an irreversible external effect and stay on the
# human-administrator release path (staging/production).
IRREVERSIBLE_CAPABILITIES = (
    "account_registration", "appointment_booking", "payment_preparation",
    "submission_execution",
)

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


def evaluate_build(db, request_id: str) -> AutoReleaseResult:
    """Independently evaluate one build and auto-release its reversible (sandbox)
    capability when every evidence gate passes. Idempotent: a route already bound
    at sandbox is reported as released without re-releasing."""
    req = db.get(fm.AdapterBuildRequest, request_id)
    if req is None:
        return AutoReleaseResult(False, reason="build request not found")
    if not _release_recommended(req):
        return AutoReleaseResult(False, reason=f"build not release-recommended (state {req.state})")
    cand = db.get(fm.AdapterCandidate, req.current_candidate_id)
    if cand is None:
        return AutoReleaseResult(False, reason="no candidate on build")

    # Idempotent: already bound at sandbox → nothing to do.
    existing = releasesvc.active_binding(db, route_key=cand.route_key, tier="sandbox")
    if existing is not None:
        return AutoReleaseResult(True, tier="sandbox", reason="already released",
                                 release_id=existing.release_id,
                                 capabilities=REVERSIBLE_CAPABILITIES)

    # A quarantined or kill-switched candidate is never auto-released.
    if cand.status == "quarantined" or releasesvc.kill_engaged(db, cand.id):
        return AutoReleaseResult(False, reason="candidate quarantined or kill-switched")

    version = cand.current_version
    try:
        rel = releasesvc.release(db, candidate_id=cand.id, version=version,
                                 tier="sandbox", actor=AUTO_ACTOR, is_admin=False,
                                 kind="deterministic_auto")
    except releasesvc.ReleaseRefused as e:
        # Evidence gate not satisfied (e.g. a layer not green) — honest, no release.
        return AutoReleaseResult(False, tier="sandbox", reason=str(e))
    if req.state == "AWAITING_INTERNAL_RELEASE":
        try:
            from .statemachine import transition
            transition(req, "RELEASED_SANDBOX", "auto-released (sandbox) by policy engine")
            db.commit()
        except Exception:  # noqa: BLE001 - release recorded regardless of label
            db.rollback()
    return AutoReleaseResult(True, tier="sandbox", reason="evidence gates passed",
                             release_id=rel.id, capabilities=REVERSIBLE_CAPABILITIES)


def released_capabilities(db, route_key: str) -> dict:
    """Per-capability release status for a route, independent of any single build.
    Reversible capabilities come from the auto-released sandbox binding;
    irreversible ones require a human-released staging/production binding."""
    sandbox = releasesvc.active_binding(db, route_key=route_key, tier="sandbox")
    staging = releasesvc.active_binding(db, route_key=route_key, tier="staging")
    production = releasesvc.active_binding(db, route_key=route_key, tier="production")
    caps: dict[str, dict] = {}
    for c in REVERSIBLE_CAPABILITIES:
        caps[c] = {"released": sandbox is not None,
                   "via": "sandbox_auto" if sandbox is not None else None,
                   "reversible": True}
    higher = production or staging
    for c in IRREVERSIBLE_CAPABILITIES:
        caps[c] = {"released": higher is not None,
                   "via": ("production_admin" if production is not None
                           else "staging_admin" if staging is not None else None),
                   "reversible": False}
    return {"route_key": route_key,
            "any_released": bool(sandbox or staging or production),
            "capabilities": caps}
