"""Personal real-visa test safety gate (brief item #6).

Ellis must not begin any irreversible live action until (a) the applicant has
supplied every required piece of information, and (b) the EXACT route
(destination + visa type + nationality + residence) has passed all 15 readiness
gates. If any gate is incomplete, Ellis switches to preparation-and-handoff mode
and explains exactly what remains unsupported.

Gates are recorded by a human administrator with evidence. Kimi/search results
can never mark a gate complete (the admin endpoint requires the admin role and
is not in the model's tool allowlist). The gate composes with the execution
classification: today every runtime route is MOCK, so live enforcement is
dormant but structurally in place — the moment a LIVE_SANDBOX/LIVE_PRODUCTION
adapter exists, an incomplete route hard-blocks.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from . import models, audit
from .execution import ExecutionClass, coerce

# --- The 15 gates, verbatim from the brief (order preserved) -----------------
GATES: dict[str, str] = {
    "official_portal_verified": "Verified official government or officially authorized contractor portal",
    "official_evidence_stored": "Official-source evidence stored",
    "requirements_verified": "Current eligibility and requirements verified",
    "fees_verified": "Current fee rules verified",
    "auth_implemented": "Registration and login behavior implemented",
    "fields_documents_mapped": "Required fields and documents mapped",
    "checkpoints_mapped": "CAPTCHA, OTP, payment, appointment, and declaration checkpoints mapped",
    "representative_policy_reviewed": "Representative-submission policy reviewed",
    "contract_tests_passing": "Portal-specific contract tests passing",
    "staging_tested": "Safe staging or authorized non-destructive testing completed where available",
    "admin_approval_recorded": "Administrator approval recorded",
    "kill_switch_rollback_configured": "Kill switch and rollback configured",
    "monitoring_enabled": "Monitoring enabled",
    "final_review_enabled": "Final applicant review enabled",
    "explicit_confirmation_configured": "Explicit confirmation before payment, appointment booking, rescheduling, declaration, or submission",
}

# --- Required applicant information (brief item #6) ---------------------------
# key -> (answers key, human label). Presence of a non-empty answer is required;
# "none" is a valid answer for prior refusals.
REQUIRED_APPLICANT_INFO: dict[str, str] = {
    "passport_nationality": "Passport nationality",
    "current_residence": "Current legal residence",
    "visa_subtype": "Tourist-visa subtype",
    "travel_purpose": "Travel purpose",
    "intended_arrival": "Intended arrival date",
    "intended_departure": "Intended departure date",
    "birth_date": "Applicant date of birth (for age rules)",
    "prior_visa_refusals": "Prior visa/refusal information (\"none\" if none)",
    "has_portal_account": "Whether the applicant already has a portal account",
    "representative_submission_permitted": "Whether representative/automated submission is permitted",
}

# Execution classes that involve a REAL external portal → gate-enforced.
LIVE_CLASSES = {ExecutionClass.LIVE_SANDBOX, ExecutionClass.LIVE_PRODUCTION}
# Classes that must NEVER auto-proceed: an unknown/undeclared driver
# (MANUAL_REVIEW_REQUIRED) or an unsupported route. Only MOCK / LOCAL_PROVIDER
# proceed without gates (they cannot touch a real portal and are labeled).
BLOCKED_CLASSES = {ExecutionClass.MANUAL_REVIEW_REQUIRED, ExecutionClass.UNSUPPORTED}


class PreparationOnlyMode(Exception):
    """Raised when a live action is attempted on a route that has not passed
    every gate. Carries the honest, user-facing list of what remains."""

    def __init__(self, missing_gates: list[str], missing_info: list[str]):
        self.missing_gates = missing_gates
        self.missing_info = missing_info
        super().__init__(
            "route not approved for live processing; Ellis is in preparation-and-handoff mode. "
            f"Incomplete gates: {', '.join(missing_gates) or 'none'}. "
            f"Missing applicant information: {', '.join(missing_info) or 'none'}.")


def _row(db, *, destination: str, visa_type: str, nationality: str = "",
         residence: str = "", create: bool = False) -> models.RouteReadiness | None:
    row = db.execute(select(models.RouteReadiness).where(
        models.RouteReadiness.destination == destination,
        models.RouteReadiness.visa_type == visa_type,
        models.RouteReadiness.nationality == nationality,
        models.RouteReadiness.residence == residence)).scalar_one_or_none()
    if row is None and create:
        row = models.RouteReadiness(destination=destination, visa_type=visa_type,
                                    nationality=nationality, residence=residence, gates={})
        db.add(row)
        db.flush()
    return row


def readiness(db, *, destination: str, visa_type: str = "tourist",
              nationality: str = "", residence: str = "", include_evidence: bool = False) -> dict:
    """The honest gate report for one exact route. A route with no record has
    every gate incomplete — absence of evidence is never readiness. Gate
    evidence text + the recording admin's id are internal audit material and are
    included ONLY when include_evidence=True (admin views)."""
    row = _row(db, destination=destination, visa_type=visa_type,
               nationality=nationality, residence=residence)
    stored = (row.gates if row else {}) or {}
    gates = {}
    missing = []
    for key, label in GATES.items():
        entry = stored.get(key) or {}
        complete = bool(entry.get("complete"))
        g = {"label": label, "complete": complete}
        if include_evidence:
            g.update({"evidence": entry.get("evidence", ""),
                      "by": entry.get("by", ""), "at": entry.get("at", "")})
        gates[key] = g
        if not complete:
            missing.append(key)
    return {"destination": destination, "visa_type": visa_type,
            "nationality": nationality, "residence": residence,
            "gates": gates, "missing_gates": missing,
            "route_approved_for_live": not missing,
            "mode": "live_ready" if not missing else "preparation_and_handoff"}


def set_gate(db, *, destination: str, visa_type: str, nationality: str, residence: str,
             gate: str, complete: bool, evidence: str, actor: str) -> dict:
    """Admin-only (enforced at the endpoint). Records who/when/evidence.
    admin_approval_recorded additionally requires non-empty evidence."""
    if gate not in GATES:
        raise ValueError(f"unknown gate '{gate}'")
    if complete and not evidence.strip():
        raise ValueError("evidence is required to mark a gate complete")
    row = _row(db, destination=destination, visa_type=visa_type,
               nationality=nationality, residence=residence, create=True)
    gates = dict(row.gates or {})
    gates[gate] = {"complete": bool(complete), "evidence": evidence.strip(),
                   "by": actor, "at": datetime.now(timezone.utc).isoformat()}
    row.gates = gates
    db.commit()
    audit.record(db, org_id="platform", application_id="", action="route_gate_set",
                 detail={"destination": destination, "visa_type": visa_type,
                         "nationality": nationality, "residence": residence,
                         "gate": gate, "complete": complete}, actor=actor)
    return readiness(db, destination=destination, visa_type=visa_type,
                     nationality=nationality, residence=residence, include_evidence=True)


def missing_applicant_info(app_row: models.VisaApplication) -> list[dict]:
    """Which of the brief's required applicant fields are still missing."""
    answers = app_row.answers or {}
    missing = []
    for key, label in REQUIRED_APPLICANT_INFO.items():
        val = answers.get(key)
        if val is None or (isinstance(val, str) and not val.strip()):
            missing.append({"key": key, "label": label})
    return missing


def live_preflight(db, app_row: models.VisaApplication, ec) -> dict:
    """Per-case preflight: execution class + route gates + applicant info.
    `live_ready` is True ONLY when the route class is live, every gate is
    complete, and every required applicant answer is present."""
    ec = coerce(ec)
    answers = app_row.answers or {}
    route = readiness(db, destination=app_row.destination_country,
                      visa_type=app_row.visa_type,
                      nationality=str(answers.get("passport_nationality", "") or ""),
                      residence=str(answers.get("current_residence", "") or ""))
    info_missing = missing_applicant_info(app_row)
    is_live_route = ec in LIVE_CLASSES
    live_ready = (is_live_route and not route["missing_gates"] and not info_missing)
    if not is_live_route:
        mode = "mock_preparation"     # honest: this route runs on a mock/local driver
    elif live_ready:
        mode = "live_ready"
    else:
        mode = "preparation_and_handoff"
    return {
        "execution_class": str(ec),
        "is_live_route": is_live_route,
        "mode": mode,
        "live_ready": live_ready,
        "route": route,
        "missing_applicant_info": info_missing,
        "explanation": (
            "This route runs on the automated-test portal — no real government action can occur."
            if not is_live_route else
            ("All gates passed. Each irreversible action still requires your explicit confirmation."
             if live_ready else
             "This route is not yet approved for live processing. Ellis will prepare your "
             "application and hand off anything unsupported for you to complete directly.")),
    }


def assert_ready_for_live_action(db, app_row: models.VisaApplication, ec) -> None:
    """The hard gate. Called before starting/continuing any workflow whose
    execution class is live. Mock/local classes pass through (they cannot touch
    a real portal, and are labeled MOCK end-to-end)."""
    ec = coerce(ec)
    # An unknown/undeclared driver or unsupported route must never auto-proceed
    # (fail safe) — treat it as fully un-ready pending manual review.
    if ec in BLOCKED_CLASSES:
        raise PreparationOnlyMode(list(GATES.keys()), [])
    if ec not in LIVE_CLASSES:
        return
    pre = live_preflight(db, app_row, ec)
    if not pre["live_ready"]:
        raise PreparationOnlyMode(
            pre["route"]["missing_gates"],
            [m["key"] for m in pre["missing_applicant_info"]])
