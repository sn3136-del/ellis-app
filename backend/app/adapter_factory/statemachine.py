"""Adapter build state machine (brief §11) — 30 durable states.

Every transition is explicit and audited by the build workflow; anything not
declared here is illegal. Terminal-ish states (DISABLED, QUARANTINED,
SUPERSEDED) only move forward through explicit admin action.
"""
from __future__ import annotations

BUILD_STATES = [
    "REQUESTED",
    "CONSENT_RECORDED",
    "ROUTE_RESEARCH_PENDING",
    "ROUTE_RESEARCH_RUNNING",
    "ROUTE_RESEARCH_INCOMPLETE",
    "PORTAL_VERIFICATION_PENDING",
    "PORTAL_VERIFIED",
    "JURISDICTION_VERIFICATION_PENDING",
    "JURISDICTION_VERIFIED",
    "AUTOMATION_POLICY_REVIEW",
    "RECON_PENDING",
    "RECON_RUNNING",
    "SPECIFICATION_GENERATED",
    "CODE_GENERATED",
    "STATIC_VALIDATION_RUNNING",
    "CONTRACT_TESTING",
    "SYNTHETIC_TESTING",
    "AUTHORIZED_BROWSER_TESTING",
    "AUTOMATIC_REPAIR",
    "TESTS_PASSED",
    "TESTS_FAILED",
    "MANUAL_REVIEW_REQUIRED",
    "RELEASE_RECOMMENDED",
    "AWAITING_INTERNAL_RELEASE",
    "RELEASED_SANDBOX",
    "RELEASED_STAGING",
    "RELEASED_PRODUCTION",
    "DISABLED",
    "QUARANTINED",
    "SUPERSEDED",
]

_T = {
    "REQUESTED": ["CONSENT_RECORDED"],
    "CONSENT_RECORDED": ["ROUTE_RESEARCH_PENDING"],
    "ROUTE_RESEARCH_PENDING": ["ROUTE_RESEARCH_RUNNING", "PORTAL_VERIFICATION_PENDING"],
    "ROUTE_RESEARCH_RUNNING": ["ROUTE_RESEARCH_INCOMPLETE", "PORTAL_VERIFICATION_PENDING"],
    "ROUTE_RESEARCH_INCOMPLETE": ["ROUTE_RESEARCH_PENDING", "MANUAL_REVIEW_REQUIRED"],
    "PORTAL_VERIFICATION_PENDING": ["PORTAL_VERIFIED", "MANUAL_REVIEW_REQUIRED"],
    "PORTAL_VERIFIED": ["JURISDICTION_VERIFICATION_PENDING"],
    "JURISDICTION_VERIFICATION_PENDING": ["JURISDICTION_VERIFIED", "MANUAL_REVIEW_REQUIRED"],
    "JURISDICTION_VERIFIED": ["AUTOMATION_POLICY_REVIEW"],
    "AUTOMATION_POLICY_REVIEW": ["RECON_PENDING", "MANUAL_REVIEW_REQUIRED"],
    "RECON_PENDING": ["RECON_RUNNING"],
    # RECON_PENDING is the POLITE outcome: the host's quiet period between
    # recon passes has not elapsed, and the build steps back to wait. Leaving
    # it off this list made the deferral itself an illegal transition, so a
    # rate-limited rebuild parked in MANUAL_REVIEW_REQUIRED instead of simply
    # waiting out the cooldown (2026-08-04, Singapore).
    "RECON_RUNNING": ["SPECIFICATION_GENERATED", "MANUAL_REVIEW_REQUIRED",
                      "RECON_PENDING"],
    # MANUAL_REVIEW_REQUIRED is the stage's honest escape when its spec row is
    # missing (a crash between announcing the state and persisting the spec id
    # once left builds stranded here with no legal exit).
    "SPECIFICATION_GENERATED": ["CODE_GENERATED", "MANUAL_REVIEW_REQUIRED"],
    "CODE_GENERATED": ["STATIC_VALIDATION_RUNNING"],
    "STATIC_VALIDATION_RUNNING": ["CONTRACT_TESTING", "TESTS_FAILED"],
    "CONTRACT_TESTING": ["SYNTHETIC_TESTING", "TESTS_FAILED"],
    "SYNTHETIC_TESTING": ["AUTHORIZED_BROWSER_TESTING", "TESTS_PASSED", "TESTS_FAILED"],
    "AUTHORIZED_BROWSER_TESTING": ["TESTS_PASSED", "TESTS_FAILED"],
    "AUTOMATIC_REPAIR": ["CODE_GENERATED", "MANUAL_REVIEW_REQUIRED", "QUARANTINED"],
    "TESTS_PASSED": ["RELEASE_RECOMMENDED"],
    "TESTS_FAILED": ["AUTOMATIC_REPAIR", "MANUAL_REVIEW_REQUIRED", "QUARANTINED"],
    "MANUAL_REVIEW_REQUIRED": ["RECON_PENDING", "CODE_GENERATED", "DISABLED",
                               "QUARANTINED", "ROUTE_RESEARCH_PENDING",
                               "AWAITING_INTERNAL_RELEASE"],
    "RELEASE_RECOMMENDED": ["AWAITING_INTERNAL_RELEASE"],
    "AWAITING_INTERNAL_RELEASE": ["RELEASED_SANDBOX", "RELEASED_STAGING",
                                  "RELEASED_PRODUCTION", "MANUAL_REVIEW_REQUIRED",
                                  "DISABLED"],
    "RELEASED_SANDBOX": ["RELEASED_STAGING", "RELEASED_PRODUCTION", "DISABLED",
                         "QUARANTINED", "SUPERSEDED", "AWAITING_INTERNAL_RELEASE"],
    "RELEASED_STAGING": ["RELEASED_PRODUCTION", "DISABLED", "QUARANTINED",
                         "SUPERSEDED", "AWAITING_INTERNAL_RELEASE"],
    "RELEASED_PRODUCTION": ["DISABLED", "QUARANTINED", "SUPERSEDED"],
    "DISABLED": ["AWAITING_INTERNAL_RELEASE"],
    "QUARANTINED": ["MANUAL_REVIEW_REQUIRED"],
    "SUPERSEDED": [],
}

RELEASED_STATES = {"RELEASED_SANDBOX", "RELEASED_STAGING", "RELEASED_PRODUCTION"}
TERMINALISH_STATES = {"DISABLED", "QUARANTINED", "SUPERSEDED"}


class IllegalBuildTransition(Exception):
    pass


def can_transition(frm: str, to: str) -> bool:
    return to in _T.get(frm, [])


def transition(request, to: str, reason: str = ""):
    """Advance a build request; appends to its immutable state history."""
    if request.state not in BUILD_STATES or to not in BUILD_STATES:
        raise IllegalBuildTransition(f"unknown state {request.state!r} -> {to!r}")
    if not can_transition(request.state, to):
        raise IllegalBuildTransition(f"illegal build transition {request.state} -> {to}")
    hist = list(request.state_history or [])
    hist.append({"from": request.state, "to": to, "reason": reason[:200]})
    request.state_history = hist
    request.state = to
    return request


# The applicant sees only these simple labels (§13/§29), never internals.
APPLICANT_LABELS = {
    "REQUESTED": "preparing",
    "CONSENT_RECORDED": "preparing",
    "ROUTE_RESEARCH_PENDING": "researching",
    "ROUTE_RESEARCH_RUNNING": "researching",
    "ROUTE_RESEARCH_INCOMPLETE": "researching",
    "PORTAL_VERIFICATION_PENDING": "verifying_portal",
    "PORTAL_VERIFIED": "verifying_portal",
    "JURISDICTION_VERIFICATION_PENDING": "verifying_jurisdiction",
    "JURISDICTION_VERIFIED": "verifying_jurisdiction",
    "AUTOMATION_POLICY_REVIEW": "verifying_portal",
    "RECON_PENDING": "mapping_portal",
    "RECON_RUNNING": "mapping_portal",
    "SPECIFICATION_GENERATED": "generating",
    "CODE_GENERATED": "generating",
    "STATIC_VALIDATION_RUNNING": "safety_checks",
    "CONTRACT_TESTING": "testing",
    "SYNTHETIC_TESTING": "testing",
    "AUTHORIZED_BROWSER_TESTING": "testing",
    "AUTOMATIC_REPAIR": "repairing",
    "TESTS_PASSED": "final_verification",
    "TESTS_FAILED": "repairing",
    "MANUAL_REVIEW_REQUIRED": "manual_review",
    "RELEASE_RECOMMENDED": "final_verification",
    "AWAITING_INTERNAL_RELEASE": "final_verification",
    "RELEASED_SANDBOX": "ready",
    "RELEASED_STAGING": "ready",
    "RELEASED_PRODUCTION": "ready",
    "DISABLED": "unavailable",
    "QUARANTINED": "manual_review",
    "SUPERSEDED": "unavailable",
}


def applicant_label(state: str) -> str:
    # Fail safe: an unknown state shows as manual review, never as ready.
    return APPLICANT_LABELS.get(state, "manual_review")
