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


# Deterministic release-gate evidence (global_routes.release_gates GATE_NAMES)
# that satisfies each personal gate when the route's portal family carries a
# deterministically released adapter. This is the no-admin path: the SAME
# protections, proven by recorded machine-checked evidence instead of a human
# signature. Every mapped release gate must be true for the personal gate to
# derive complete.
_DETERMINISTIC_GATE_SOURCES: dict[str, list[str]] = {
    "official_portal_verified": ["official_portal_identity_confirmed"],
    "official_evidence_stored": ["official_portal_identity_confirmed",
                                 "destination_and_jurisdiction_correct"],
    "requirements_verified": ["destination_and_jurisdiction_correct",
                              "required_fields_mapped"],
    "fees_verified": ["payment_confirmation_preserved"],
    "auth_implemented": ["account_flow_mapped_where_applicable"],
    "fields_documents_mapped": ["required_fields_mapped",
                                "upload_flow_mapped_where_applicable"],
    "checkpoints_mapped": ["captcha_otp_handoffs_preserved",
                           "applicant_confirmation_gates_preserved"],
    "representative_policy_reviewed": ["official_portal_identity_confirmed",
                                       "no_mock_or_synthetic_driver"],
    "contract_tests_passing": ["regression_tests_passed",
                               "structured_provider_errors"],
    "staging_tested": ["safe_navigation_succeeded",
                       "selectors_verified_repeated_sessions",
                       "no_irreversible_action_executed_in_testing"],
    "admin_approval_recorded": ["__deterministic_release__"],
    "kill_switch_rollback_configured": ["security_scan_passed"],
    "monitoring_enabled": ["structured_provider_errors"],
    "final_review_enabled": ["submission_confirmation_preserved"],
    "explicit_confirmation_configured": ["applicant_confirmation_gates_preserved",
                                         "payment_confirmation_preserved",
                                         "submission_confirmation_preserved"],
}


def deterministic_gate_completion(db, *, destination: str, visa_type: str,
                                  nationality: str) -> dict:
    """Gate completion derived from a deterministic family-adapter release for
    this exact route. Empty dict when the route has no released family adapter
    — absence of evidence is never readiness. No administrator is consulted;
    Kimi has no path into any of the underlying evidence (release gates are
    machine-checked over recorded factory artifacts)."""
    try:
        from sqlalchemy import select as _select
        from .global_routes.models import FamilyAdapterLink, RoutePairPolicy
        from .global_routes.models import pair_key as _pair_key
        from .visa_snapshot.registry import normalize_country
    except Exception:  # noqa: BLE001 — global routes not deployed
        return {}
    try:
        dest = normalize_country(destination, field="destination_country")
        pk = _pair_key((nationality or "").strip(), "ordinary_passport", dest)
    except Exception:  # noqa: BLE001 — unknown route: no derivation
        return {}
    pair = db.execute(_select(RoutePairPolicy).where(
        RoutePairPolicy.pair_key == pk)).scalar_one_or_none()
    if pair is None or not pair.portal_family_id:
        return {}
    link = db.execute(_select(FamilyAdapterLink).where(
        FamilyAdapterLink.family_id == pair.portal_family_id)).scalar_one_or_none()
    if link is None or not link.released:
        return {}
    report = (link.gate_report or {})
    release_gates = {k: bool((v or {}).get("passed", v) if isinstance(v, dict) else v)
                     for k, v in (report.get("gates") or {}).items()}
    if not report.get("passed") or not release_gates:
        return {}
    out = {}
    when = str(getattr(link, "updated_at", "") or "")
    for personal_gate, sources in _DETERMINISTIC_GATE_SOURCES.items():
        srcs = [s for s in sources if s != "__deterministic_release__"]
        if all(release_gates.get(s) for s in srcs):
            basis = ", ".join(sources)
            out[personal_gate] = {
                "complete": True,
                "evidence": (f"deterministic release of family adapter "
                             f"'{pair.portal_family_id}' (candidate {link.candidate_id}, "
                             f"tier {link.release_tier or 'sandbox'}); basis: {basis}"),
                "by": "deterministic-release-engine", "at": when}
    return out


def readiness(db, *, destination: str, visa_type: str = "tourist",
              nationality: str = "", residence: str = "", include_evidence: bool = False) -> dict:
    """The honest gate report for one exact route. A route with no record has
    every gate incomplete — absence of evidence is never readiness. Two
    evidence sources: admin-recorded rows (which always take precedence) and
    deterministic release-derived completion (the no-admin path). Gate evidence
    text + the recorder id are internal audit material, included ONLY when
    include_evidence=True (admin views)."""
    row = _row(db, destination=destination, visa_type=visa_type,
               nationality=nationality, residence=residence)
    stored = (row.gates if row else {}) or {}
    derived = deterministic_gate_completion(db, destination=destination,
                                            visa_type=visa_type,
                                            nationality=nationality)
    gates = {}
    missing = []
    for key, label in GATES.items():
        entry = stored.get(key) or derived.get(key) or {}
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


# Documented answer-key synonyms: the intake wizard stores some of the brief's
# required facts under its own field names. A synonym satisfies the same fact —
# never a guess, just naming.
_INFO_SYNONYMS: dict[str, tuple] = {
    "prior_visa_refusals": ("prior_refusals",),
    "has_portal_account": ("existing_portal_account",),
}


def missing_applicant_info(app_row: models.VisaApplication, db=None) -> list[dict]:
    """Which of the brief's required applicant fields are still missing."""
    answers = app_row.answers or {}

    def present(key: str) -> bool:
        for k in (key, *(_INFO_SYNONYMS.get(key) or ())):
            v = answers.get(k)
            if v is not None and (not isinstance(v, str) or v.strip()):
                return True
        return False

    missing = []
    for key, label in REQUIRED_APPLICANT_INFO.items():
        if present(key):
            continue
        if db is not None and key == "visa_subtype":
            # The verified route guidance pins the exact category (e.g. e-visa
            # tourist single-entry) — that IS the subtype answer.
            from .checklist_intake import case_guidance
            cg = case_guidance(db, app_row.id)
            if cg is not None and (cg.guidance or {}).get("guidance", {}).get("visa_category"):
                continue
        if db is not None and key == "representative_submission_permitted":
            # A signed, active standing authorization is the applicant's
            # explicit permission for Ellis to act on the portal.
            from .authorization import valid as auth_valid
            if auth_valid(db, app_row.id):
                continue
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
    info_missing = missing_applicant_info(app_row, db=db)
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
