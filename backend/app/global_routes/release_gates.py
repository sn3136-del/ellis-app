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

    # 3. No mock/synthetic driver outside mock-allowed modes.
    mock_ok = synthetic is False or not settings().real_only_mode
    gate("no_mock_or_synthetic_driver", mock_ok,
         "driver class matches runtime mode" if mock_ok else
         f"missing: real portal driver — synthetic evidence is forbidden in "
         f"runtime mode {mode}")

    # 4. Safe read-only navigation succeeded (recon observed real pages).
    recon_pages = _recon_pages(db, build_request)
    gate("safe_navigation_succeeded", recon_pages > 0,
         f"{recon_pages} public page(s) observed credential-free" if recon_pages
         else "missing: successful read-only navigation of the real portal")

    # 5. Required applicant fields mapped — counted from FILL nodes actually
    #    IN the flow, not from the spec's mapping list (which can contain
    #    mappings for pages the flow never visits).
    fill_nodes = sum(1 for n in nodes if n.get("action") == "FILL_NON_SENSITIVE")
    gate("required_fields_mapped", fill_nodes > 0,
         f"{fill_nodes} grounded fill step(s) in the flow" if fill_nodes else
         "missing: grounded applicant field mappings wired into the flow "
         "(no form page was mappable from public observation)")

    # 6. Selector stability across repeated sessions: the behavioral layer
    #    re-observes every selector in a FRESH session after recon mapped it.
    behavioral = ("LIVE_STRUCTURAL_TESTED" in layers and layers["LIVE_STRUCTURAL_TESTED"]) or \
                 ("SYNTHETIC_TESTED" in layers and layers["SYNTHETIC_TESTED"])
    gate("selectors_verified_repeated_sessions", behavioral,
         "selectors re-verified in an independent session" if behavioral else
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
    uploads_observed = _uploads_observed(db, build_request)
    if uploads_observed:
        upload_ok = any(n.get("action") == "UPLOAD_AUTHORIZED_DOCUMENT" for n in nodes) \
            or bool(version.document_mappings)
        gate("upload_flow_mapped_where_applicable", upload_ok,
             "document upload mapping present" if upload_ok else
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
    needed = {k for k in ("captcha", "otp") if k in sensitive_observed}
    missing_handoffs = needed - handoffs
    gate("captcha_otp_handoffs_preserved", not missing_handoffs,
         "CAPTCHA/OTP handoffs preserved" if not missing_handoffs else
         f"missing: applicant handoff node(s) for observed {sorted(missing_handoffs)}")
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


def _uploads_observed(db, build_request) -> bool:
    job = db.execute(select(fm.AdapterReconJob).where(
        fm.AdapterReconJob.build_request_id == build_request.id)
        .order_by(fm.AdapterReconJob.created_at.desc())).scalars().first()
    if not job:
        return False
    for art in db.execute(select(fm.AdapterReconArtifact).where(
            fm.AdapterReconArtifact.recon_job_id == job.id)).scalars():
        for el in (art.structure or {}).get("elements", []):
            if (el.get("type") or "").lower() == "file":
                return True
    return False


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
    auto = auto_release.evaluate_build(db, build_request.id)
    binding = release.active_binding(db, route_key=build_request.route_key, tier="sandbox")
    # The binding must be for THIS candidate — a stale binding from an older
    # candidate on the same route key never counts as this build's release.
    released = binding is not None and binding.candidate_id == candidate.id
    caps = auto_release.released_capabilities(db, route_key=build_request.route_key)
    return dict(result, released=released,
                auto_release=auto.as_dict() if hasattr(auto, "as_dict") else str(auto),
                capabilities=caps)
