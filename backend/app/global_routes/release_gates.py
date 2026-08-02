"""Deterministic automatic-release gates for portal-family adapters.

Composes the objective evidence the factory already records (static
validation, contract tests, behavioral layers, recon artifacts, typed-flow
structure) plus family-level identity checks into the full brief gate list.
Every gate is computed from stored evidence — no model, no admin. All gates
pass -> the adapter auto-releases (reversible sandbox tier + per-capability
releases via the existing AutoReleasePolicyEngine). Any gate fails -> the
build fails CLOSED and the precise missing capability is recorded verbatim.

Routine human release approval is removed; what remains human is only the
applicant's own confirmations (OTP/CAPTCHA/payment/final submission), which
several gates below exist to prove are preserved.
"""
from __future__ import annotations

from sqlalchemy import select

from ..adapter_factory import auto_release, release
from ..adapter_factory import models as fm
from ..config import settings
from ..visa_snapshot.authority import is_government_host

# Gate names in brief order. Each maps to a deterministic check.
GATE_NAMES = (
    "official_portal_identity_confirmed",
    "destination_and_jurisdiction_correct",
    "no_mock_or_synthetic_driver",
    "safe_navigation_succeeded",
    "required_fields_mapped",
    "selectors_verified_repeated_sessions",
    "account_flow_mapped_where_applicable",
    "upload_flow_mapped_where_applicable",
    "applicant_confirmation_gates_preserved",
    "captcha_otp_handoffs_preserved",
    "payment_confirmation_preserved",
    "submission_confirmation_preserved",
    "no_irreversible_action_executed_in_testing",
    "structured_provider_errors",
    "security_scan_passed",
    "regression_tests_passed",
)


def _layers(db, version_id: str) -> dict[str, bool]:
    """Latest run PER LAYER wins — an old green run can never mask a newer
    failure (failing runs use *_FAILED classifications, so keying by
    classification alone would keep a stale pass forever)."""
    runs = db.execute(select(fm.AdapterTestRun).where(
        fm.AdapterTestRun.candidate_version_id == version_id)
        .order_by(fm.AdapterTestRun.created_at.asc())).scalars().all()
    latest: dict[str, fm.AdapterTestRun] = {}
    for r in runs:
        latest[r.layer] = r
    return {r.classification: bool(r.passed) for r in latest.values()}


def _flow_nodes(version) -> list[dict]:
    return list(version.flow or [])


def _handoff_kinds(nodes) -> set[str]:
    return {n.get("handoff_kind") for n in nodes if n.get("action") == "APPLICANT_HANDOFF"}


def evaluate_gates(db, *, build_request, candidate, version, family) -> dict:
    """Return {gate: {passed, reason}} — every gate present, every failure
    naming the exact missing capability."""
    nodes = _flow_nodes(version)
    layers = _layers(db, version.id)
    handoffs = _handoff_kinds(nodes)
    sensitive_observed = _observed_sensitive_kinds(db, build_request)
    mode = settings().runtime_mode
    report: dict[str, dict] = {}

    def gate(name: str, passed: bool, reason: str):
        report[name] = {"passed": bool(passed), "reason": reason}

    # 1. Official portal identity: family verified by domain or live recon.
    fam_ok = family is not None and family.verification_status in (
        "verified_official_domain", "verified_live")
    hosts = (build_request.portal_evidence or {}).get("hostnames", [])
    synthetic = (build_request.portal_evidence or {}).get("verification") == "synthetic_test_portal"
    hosts_ok = synthetic or (bool(hosts) and all(is_government_host(h) for h in hosts))
    gate("official_portal_identity_confirmed", fam_ok and hosts_ok,
         "portal family identity verified" if (fam_ok and hosts_ok) else
         "missing: verified official portal identity (government-domain or live "
         "official-link evidence) — seed portals are never released unverified")

    # 2. Destination + jurisdiction correctness.
    dest_ok = family is not None and build_request.destination in (family.destinations or [])
    gate("destination_and_jurisdiction_correct", dest_ok,
         "family serves the route destination" if dest_ok else
         f"missing: portal family does not serve destination "
         f"{build_request.destination} — cross-destination contamination refused")

    # 3. No mock/synthetic driver in a real-only runtime — read from the
    #    ACTUAL behavioral evidence, never a self-declared flag. In a real-only
    #    mode the behavioral layer must be the LIVE structural layer; a
    #    SYNTHETIC_TESTED row (or the absence of any live layer) means the
    #    observations came from SyntheticPortal and must never release. The
    #    self-declared `synthetic` flag can only RELAX gates in mock-allowed
    #    modes; it can never let a real-mode build skip this check.
    synthetic_layer = bool(layers.get("SYNTHETIC_TESTED"))
    live_layer = bool(layers.get("LIVE_STRUCTURAL_TESTED"))
    if settings().real_only_mode:
        mock_ok = live_layer and not synthetic_layer
    else:
        mock_ok = True
    gate("no_mock_or_synthetic_driver", mock_ok,
         "live driver evidence (LIVE_STRUCTURAL_TESTED), no synthetic layer"
         if mock_ok else
         f"missing: real portal driver — behavioral evidence is "
         f"{'synthetic' if synthetic_layer else 'absent'}, forbidden in runtime "
         f"mode {mode}")

    # 4. Safe read-only navigation succeeded (recon observed real pages).
    #    A portal that DECLARES an entry gate must additionally have reached
    #    the gated application form via the declared reversible replay —
    #    instruction pages alone are not the portal's flow.
    recon_pages = _recon_pages(db, build_request)
    entry_gate = _entry_gate_declared(build_request)
    form_observed = _entry_gated_form_observed(db, build_request)
    if entry_gate:
        nav_ok = recon_pages > 0 and form_observed
        gate("safe_navigation_succeeded", nav_ok,
             f"{recon_pages} public page(s) observed credential-free; declared "
             f"entry gate replayed to the application form" if nav_ok else
             ("missing: entry-gate replay did not reach the application form "
              "credential-free" if recon_pages else
              "missing: successful read-only navigation of the real portal"))
    else:
        gate("safe_navigation_succeeded", recon_pages > 0,
             f"{recon_pages} public page(s) observed credential-free" if recon_pages
             else "missing: successful read-only navigation of the real portal")

    # 5. Required applicant fields mapped — counted from FILL/SELECT_SEARCH
    #    nodes actually IN the flow, not from the spec's mapping list (which
    #    can contain mappings for pages the flow never visits).
    #    A portal whose form only exists after sign-in can never be mapped
    #    credential-free. Its fields may come instead from a CONSENTED
    #    signed-in applicant session — but only with that consent on record,
    #    and the report always says which it was, so a release can never
    #    describe a signed-in observation as a credential-free one.
    from ..authorized_observation import has_consent
    fill_nodes = sum(1 for n in nodes
                     if n.get("action") in ("FILL_NON_SENSITIVE", "SELECT_SEARCH"))
    provenance = _form_evidence_provenance(db, build_request)
    if provenance == "authorized_session":
        consented = has_consent(build_request)
        gate("required_fields_mapped", bool(fill_nodes) and consented,
             f"{fill_nodes} fill/select step(s) mapped from a CONSENTED "
             f"signed-in applicant session (this portal shows no form to a "
             f"credential-free visitor)" if (fill_nodes and consented) else
             ("missing: the applicant's consent to learn this portal from their "
              "signed-in session was not recorded" if fill_nodes else
              "missing: grounded applicant field mappings wired into the flow"))
    else:
        gate("required_fields_mapped", fill_nodes > 0,
             f"{fill_nodes} grounded fill/select step(s) in the flow "
             f"(credential-free public observation)" if fill_nodes else
             "missing: grounded applicant field mappings wired into the flow "
             "(no form page was mappable from public observation)")

    # 6. Selector stability across repeated sessions: the behavioral layer
    #    re-observes every selector in a FRESH session after recon mapped it.
    #    Live evidence must show TWO independent sessions (the recon session
    #    is never one of them); the synthetic corpus re-drives per scenario.
    # In a real-only runtime the synthetic corpus can NEVER satisfy this gate:
    # only two independent LIVE sessions count (a synthetic pass there means
    # the observations were fabricated, which gate 3 already refuses).
    synthetic_ok = (bool(layers.get("SYNTHETIC_TESTED"))
                    and not settings().real_only_mode)
    live_ok = "LIVE_STRUCTURAL_TESTED" in layers and layers["LIVE_STRUCTURAL_TESTED"]
    live_sessions = _live_structural_sessions(db, version.id) if live_ok else 0
    if synthetic_ok:
        gate("selectors_verified_repeated_sessions", True,
             "selectors re-verified across the synthetic behavioral corpus")
    elif live_ok:
        gate("selectors_verified_repeated_sessions", live_sessions >= 2,
             f"selectors verified live in {live_sessions} independent sessions"
             if live_sessions >= 2 else
             "missing: live selector re-verification ran in only one session — "
             "a second independent session is required")
    else:
        gate("selectors_verified_repeated_sessions", False,
             "missing: selector re-verification in a second independent session "
             "(live structural or synthetic behavioral layer)")

    # 7. Account flow mapped where the portal requires an account.
    if family is not None and family.account_required:
        acct = "credentials" in handoffs
        gate("account_flow_mapped_where_applicable", acct,
             "credentials handoff mapped" if acct else
             "missing: account/login flow mapping (credentials applicant handoff)")
    else:
        gate("account_flow_mapped_where_applicable", True,
             "portal requires no account")

    # 8. Upload flow mapped where the portal exposes uploads.
    uploads_observed = _uploads_observed_count(db, build_request)
    if uploads_observed:
        upload_nodes = [n for n in nodes
                        if n.get("action") == "UPLOAD_AUTHORIZED_DOCUMENT"]
        upload_ok = bool(upload_nodes) or bool(version.document_mappings)
        doc_types = sorted({n.get("doc_type", "") for n in upload_nodes if n.get("doc_type")}
                           or {d.get("doc_type", "") for d in (version.document_mappings or [])})
        gate("upload_flow_mapped_where_applicable", upload_ok,
             f"{len(upload_nodes) or len(version.document_mappings or [])} upload "
             f"mapping(s) for {uploads_observed} observed file field(s) "
             f"(doc types: {', '.join(t for t in doc_types if t) or 'passport'})"
             if upload_ok else
             "missing: document upload mapping for observed portal upload fields")
    else:
        gate("upload_flow_mapped_where_applicable", True,
             "no upload fields observed on public pages")

    # 9-12. Applicant confirmation gates preserved (structural, typed flow).
    conf_ok = all(n.get("action") != "FILL_NON_SENSITIVE" or
                  not n.get("sensitive") for n in nodes)
    gate("applicant_confirmation_gates_preserved", conf_ok,
         "no sensitive field is automated" if conf_ok else
         "missing: applicant control of sensitive steps")
    # OBSERVED kinds (public recon) plus DECLARED kinds (curated: e.g. the
    # Vietnam portal shows its CAPTCHA only at review/submit, which recon
    # cannot observe credential-free — the declared handoff still guarantees
    # the applicant completes it personally). Both must be handoff nodes.
    declared_kinds = {k for k in (entry_gate.get("declared_handoffs") or [])
                      if k in ("captcha", "otp")}
    needed = {k for k in ("captcha", "otp") if k in sensitive_observed} | declared_kinds
    missing_handoffs = needed - handoffs
    if not missing_handoffs:
        detail = []
        for k in sorted(needed):
            basis = "observed on public pages" if k in sensitive_observed \
                else "DECLARED (shown at review/submit; not observable credential-free)"
            detail.append(f"{k} handoff present — {basis}")
        gate("captcha_otp_handoffs_preserved", True,
             "; ".join(detail) or "no CAPTCHA/OTP observed or declared")
    else:
        gate("captcha_otp_handoffs_preserved", False,
             f"missing: applicant handoff node(s) for {sorted(missing_handoffs)} "
             f"(observed: {sorted(sensitive_observed & needed)}, "
             f"declared: {sorted(declared_kinds)})")
    def _evidence_categories(n) -> set[str]:
        ev = n.get("success_evidence") or []
        if isinstance(ev, dict):
            ev = [ev]
        return {e.get("category") for e in ev if isinstance(e, dict)}

    pay_nodes = [n for n in nodes if "payment_captured" in _evidence_categories(n)
                 or n.get("action") == "READ_FEE"]
    pay_ok = ("payment_credentials" in handoffs) or not pay_nodes
    gate("payment_confirmation_preserved", pay_ok,
         "payment stays applicant-confirmed" if pay_ok else
         "missing: exact-amount applicant payment handoff before any payment step")
    submit_nodes = [n for n in nodes if "submission_accepted" in _evidence_categories(n)]
    submit_ok = (not submit_nodes) or ("legally_personal_declaration" in handoffs)
    gate("submission_confirmation_preserved", submit_ok,
         "final submission stays applicant-confirmed" if submit_ok else
         "missing: applicant declaration/final-confirmation handoff before submission")

    # 13. No irreversible action executed against a REAL portal during
    #     testing. In mock-allowed modes no live driver can exist (proven by
    #     test_runtime_modes), so behavioral evidence there is in-process
    #     synthetic by construction. In real-only modes the behavioral layer
    #     is read-only structural — ANY irreversible evidence is a violation.
    if settings().real_only_mode:
        irreversible_execs = _irreversible_test_evidence(db, candidate)
        gate("no_irreversible_action_executed_in_testing", irreversible_execs == 0,
             "testing executed no irreversible real action" if irreversible_execs == 0
             else f"violation: {irreversible_execs} irreversible outcome record(s) "
                  f"during real-mode testing — release refused")
    else:
        gate("no_irreversible_action_executed_in_testing", True,
             "mock-allowed mode: behavioral corpus runs against the in-process "
             "synthetic portal only; no real portal can be touched")

    # 14. Structured provider errors: every recorded failure on this build
    #     AND on the candidate's failure rows carries a typed reason/class.
    err_ok = _structured_errors_ok(build_request) and \
        _failures_typed(db, candidate)
    gate("structured_provider_errors", err_ok,
         "failures recorded with typed reasons" if err_ok else
         "missing: structured provider-error reporting (untyped failure "
         "recorded on this build/candidate)")

    # 15. Security scan (static validator over the full bundle).
    static_ok = layers.get("STATIC_VALIDATED", False)
    gate("security_scan_passed", static_ok,
         "static security validation green" if static_ok else
         "missing: static security scan (STATIC_VALIDATED)")

    # 16. Regression tests (contract layer against recorded structure).
    contract_ok = layers.get("CONTRACT_TESTED", False)
    gate("regression_tests_passed", contract_ok,
         "contract regression tests green" if contract_ok else
         "missing: contract regression tests (CONTRACT_TESTED)")

    passed = all(g["passed"] for g in report.values())
    return {"passed": passed, "gates": report,
            "missing": [f"{k}: {v['reason']}" for k, v in report.items()
                        if not v["passed"]]}


def _recon_pages(db, build_request) -> int:
    job = db.execute(select(fm.AdapterReconJob).where(
        fm.AdapterReconJob.build_request_id == build_request.id)
        .order_by(fm.AdapterReconJob.created_at.desc())).scalars().first()
    return int(job.pages_observed or 0) if job else 0


def _observed_sensitive_kinds(db, build_request) -> set[str]:
    kinds: set[str] = set()
    job = db.execute(select(fm.AdapterReconJob).where(
        fm.AdapterReconJob.build_request_id == build_request.id)
        .order_by(fm.AdapterReconJob.created_at.desc())).scalars().first()
    if not job:
        return kinds
    for art in db.execute(select(fm.AdapterReconArtifact).where(
            fm.AdapterReconArtifact.recon_job_id == job.id)).scalars():
        for el in (art.structure or {}).get("elements", []):
            name = f"{el.get('name', '')} {el.get('label', '')}".lower()
            if el.get("sensitive"):
                if "captcha" in name:
                    kinds.add("captcha")
                if "otp" in name or "one-time" in name:
                    kinds.add("otp")
    return kinds


def _uploads_observed_count(db, build_request) -> int:
    job = db.execute(select(fm.AdapterReconJob).where(
        fm.AdapterReconJob.build_request_id == build_request.id)
        .order_by(fm.AdapterReconJob.created_at.desc())).scalars().first()
    if not job:
        return 0
    seen: set[str] = set()
    for art in db.execute(select(fm.AdapterReconArtifact).where(
            fm.AdapterReconArtifact.recon_job_id == job.id)).scalars():
        for el in (art.structure or {}).get("elements", []):
            if (el.get("type") or "").lower() == "file":
                seen.add(el.get("selector") or el.get("name") or "file")
    return len(seen)


def _entry_gate_declared(build_request) -> dict:
    return (build_request.portal_evidence or {}).get("entry_gate") or {}


def _form_evidence_provenance(db, build_request) -> str:
    """How was the application form seen — credential-free, or from a consented
    signed-in applicant session?

    Returns "public", "authorized_session", or "" when no form was seen at all.
    A release must never describe a signed-in observation as a credential-free
    one, so this is what the gate report is written from.
    """
    from ..authorized_observation import CONTENT_CLASS
    job = db.execute(select(fm.AdapterReconJob).where(
        fm.AdapterReconJob.build_request_id == build_request.id)
        .order_by(fm.AdapterReconJob.created_at.desc())).scalars().first()
    if not job:
        return ""
    saw_public = saw_authorized = False
    for art in db.execute(select(fm.AdapterReconArtifact).where(
            fm.AdapterReconArtifact.recon_job_id == job.id)).scalars():
        if not (art.structure or {}).get("elements"):
            continue
        if art.content_class == CONTENT_CLASS:
            saw_authorized = True
        elif art.content_class == "application_form":
            saw_public = True
    if saw_public:
        return "public"
    return "authorized_session" if saw_authorized else ""


def _entry_gated_form_observed(db, build_request) -> bool:
    """Did the LATEST recon job record the entry-gated application form
    (content_class set by the declared-entry-gate replay)?"""
    job = db.execute(select(fm.AdapterReconJob).where(
        fm.AdapterReconJob.build_request_id == build_request.id)
        .order_by(fm.AdapterReconJob.created_at.desc())).scalars().first()
    if not job:
        return False
    for art in db.execute(select(fm.AdapterReconArtifact).where(
            fm.AdapterReconArtifact.recon_job_id == job.id)).scalars():
        if art.content_class == "application_form" and \
                (art.structure or {}).get("elements"):
            return True
    return False


def _live_structural_sessions(db, version_id: str) -> int:
    """Independent live sessions recorded by the LATEST live_structural run."""
    run = db.execute(select(fm.AdapterTestRun).where(
        fm.AdapterTestRun.candidate_version_id == version_id,
        fm.AdapterTestRun.layer == "live_structural")
        .order_by(fm.AdapterTestRun.created_at.desc())).scalars().first()
    if run is None:
        return 0
    return int((run.summary or {}).get("independent_sessions", 1))


def _irreversible_test_evidence(db, candidate) -> int:
    """Outcome-evidence rows proving an irreversible action ran against a
    non-synthetic host during any execution of this candidate."""
    count = 0
    for ev in db.execute(select(fm.AdapterOutcomeEvidence)
                         .join(fm.AdapterExecution,
                               fm.AdapterOutcomeEvidence.execution_id == fm.AdapterExecution.id)
                         .where(fm.AdapterExecution.candidate_id == candidate.id)).scalars():
        if ev.state_category in ("submission_accepted", "appointment_booked",
                                 "payment_captured") and \
                not (ev.hostname or "").endswith(".example"):
            count += 1
    return count


def _failures_typed(db, candidate) -> bool:
    for f in db.execute(select(fm.AdapterFailure).where(
            fm.AdapterFailure.candidate_id == candidate.id)).scalars():
        if not (f.failure_class or "").strip():
            return False
    return True


def _structured_errors_ok(build_request) -> bool:
    """Every recorded failure on this build carries a typed reason (state
    history entries all have a reason; error string is either empty or set
    alongside a review task)."""
    for h in (build_request.state_history or []):
        if h.get("to") in ("TESTS_FAILED", "MANUAL_REVIEW_REQUIRED", "QUARANTINED") \
                and not (h.get("reason") or "").strip():
            return False
    return True


# Capabilities with NO safe applicant fallback: if the flow drives one of
# these and its gate fails, the route resolves and then dead-ends at exactly
# the step the applicant is waiting on. payment_preparation and
# account_registration are deliberately EXCLUDED — when their gate does not
# pass the capability simply is not granted and the applicant does that step
# personally in the secure window (this is exactly how the released Vietnam
# route works: it carries a payment handoff, no READ_FEE, and ships with only
# submission_execution granted).
_NO_FALLBACK_CAPABILITIES = ("submission_execution", "appointment_booking")


def _capability_mismatches(db, *, build_request, version) -> list[str]:
    """No-fallback capabilities the flow EXERCISES but whose own structural
    gate fails. Releasing such a route would produce a live adapter that the
    runtime refuses at a step with no manual escape."""
    from ..adapter_factory.compiler import compile_flow
    from ..adapter_factory.runtime import _required_capabilities
    try:
        compiled = compile_flow(version)
    except Exception as e:  # noqa: BLE001 — an uncompilable flow never releases
        return [f"capability_runtime_consistency: flow does not compile ({str(e)[:80]})"]
    out: list[str] = []
    for cap in sorted(_required_capabilities(compiled)):
        if cap not in _NO_FALLBACK_CAPABILITIES:
            continue
        ok, problems, _ = auto_release.capability_gate(version, cap)
        if not ok:
            out.append(f"capability_runtime_consistency: flow exercises {cap} "
                       f"but its gate fails: {'; '.join(problems)[:200]}")
    return out


def evaluate_and_release(db, *, build_request, family) -> dict:
    """Run all gates; release automatically when every gate passes. Fail
    closed otherwise, recording each missing capability verbatim."""
    candidate = db.execute(select(fm.AdapterCandidate).where(
        fm.AdapterCandidate.build_request_id == build_request.id)).scalars().first()
    if candidate is None:
        return {"released": False, "passed": False,
                "missing": ["candidate: build produced no adapter candidate"],
                "gates": {}}
    version = db.execute(select(fm.AdapterCandidateVersion).where(
        fm.AdapterCandidateVersion.candidate_id == candidate.id)
        .order_by(fm.AdapterCandidateVersion.version.desc())).scalars().first()
    if version is None:
        return {"released": False, "passed": False,
                "missing": ["version: build produced no candidate version"],
                "gates": {}}
    result = evaluate_gates(db, build_request=build_request, candidate=candidate,
                            version=version, family=family)
    if not result["passed"]:
        return dict(result, released=False)
    # Cross-check: a released route must be one the RUNTIME can actually
    # execute. Every irreversible capability the flow exercises has to clear
    # its own capability gate, or the 16 gates would bless a route that is
    # permanently refused at the last step.
    unrunnable = _capability_mismatches(db, build_request=build_request,
                                        version=version)
    if unrunnable:
        return dict(result, passed=False, released=False,
                    missing=list(result.get("missing") or []) + unrunnable)
    auto = auto_release.evaluate_build(db, build_request.id)
    binding = release.active_binding(db, route_key=build_request.route_key, tier="sandbox")
    # The binding must be for THIS candidate — a stale binding from an older
    # candidate on the same route key never counts as this build's release.
    released = binding is not None and binding.candidate_id == candidate.id
    caps = auto_release.released_capabilities(db, route_key=build_request.route_key)
    return dict(result, released=released,
                auto_release=auto.as_dict() if hasattr(auto, "as_dict") else str(auto),
                capabilities=caps)
