"""Automatic repair (brief §27): sanitized-evidence-only, new-version-only.

Kimi (or the deterministic mapper) may repair REVERSIBLE failures using
sanitized evidence: fresh recon of the same verified hostnames, a regenerated
specification, and a NEW immutable candidate version that must re-pass every
required test layer. Repair STOPS — quarantining the version and opening a
review task — for any of the §27 stop classes, and hostnames can never change
during repair.
"""
from __future__ import annotations

from .. import audit
from . import models as fm
from . import recon, specgen, generator, testing

# Failure classes automatic repair may touch (§27 "potentially safe").
SAFE_FAILURE_CLASSES = {
    "selector_changed", "menu_moved", "informational_modal",
    "section_reordered", "label_changed", "route_path_changed",
    "non_sensitive_page_added",
}

# Stop classes: quarantine + review, never auto-repair (§27).
STOP_FAILURE_CLASSES = {
    "portal_identity_uncertain", "jurisdiction_uncertain",
    "auth_architecture_changed", "captcha_changed", "payment_flow_changed",
    "appointment_semantics_changed", "fee_evidence_conflict",
    "declaration_wording_changed", "signature_rules_changed",
    "submission_behavior_changed", "representative_authority_unclear",
    "official_sources_conflict", "outcome_evidence_weakened",
}

MAX_REPAIR_ATTEMPTS = 2


def classify_failure(sanitized_detail: dict) -> str:
    """Deterministic failure classification from sanitized evidence only."""
    reason = str((sanitized_detail or {}).get("reason", "")).lower()
    if "selector" in reason and ("never observed" in reason or "no_such_element" in reason.replace(" ", "_")):
        return "selector_changed"
    if "fee changed" in reason:
        return "fee_evidence_conflict"
    if "captcha" in reason:
        return "captcha_changed"
    if "declaration" in reason:
        return "declaration_wording_changed"
    if "hostname" in reason or "allowlist" in reason:
        return "portal_identity_uncertain"
    if "auth" in reason:
        return "auth_architecture_changed"
    return "selector_changed" if "no_such_element" in reason else "unknown"


def attempt_repair(db, *, build_request: fm.AdapterBuildRequest,
                   candidate: fm.AdapterCandidate, failed_version: int,
                   sanitized_detail: dict, observer,
                   irreversible_context: bool = False) -> fm.AdapterRepairAttempt:
    """One bounded repair pass. Preconditions enforced here (§27): failure was
    before any irreversible action, hostnames stay fixed, a new version is
    created, and all required layers rerun on it."""
    failure_class = classify_failure(sanitized_detail)
    attempt = fm.AdapterRepairAttempt(
        candidate_id=candidate.id, failed_version=failed_version,
        failure_class=failure_class, sanitized_evidence=sanitized_detail or {})
    db.add(attempt)
    db.flush()

    prior_attempts = db.query(fm.AdapterRepairAttempt).filter_by(
        candidate_id=candidate.id).count()

    def stop(reason: str):
        attempt.outcome = "quarantined"
        attempt.stop_reason = reason[:300]
        from .release import quarantine
        quarantine(db, candidate_id=candidate.id, version=failed_version,
                   actor="ellis-factory", reason=reason)
        db.commit()
        audit.record(db, org_id=build_request.org_id,
                     application_id=build_request.application_id,
                     action="adapter_repair_stopped",
                     detail={"class": failure_class, "reason": reason[:120]},
                     actor="ellis")
        return attempt

    if irreversible_context:
        return stop("failure occurred at/after an irreversible action — human review required")
    if failure_class in STOP_FAILURE_CLASSES:
        return stop(f"stop class {failure_class}")
    if failure_class not in SAFE_FAILURE_CLASSES:
        return stop(f"unclassified failure {failure_class!r} — refusing automatic repair")
    if prior_attempts > MAX_REPAIR_ATTEMPTS:
        return stop("repeated repair attempts failed")

    # Fresh recon on the SAME verified hostnames (never expanded), and only
    # once the portal's quiet period has passed — an automatic repair loop is
    # exactly the thing that must not hammer a government site.
    try:
        recon_job = recon.run_recon(
            db, build_request=build_request, observer=observer,
            hostnames=(build_request.portal_evidence or {}).get("hostnames", []))
    except recon.ReconRefused as e:
        return stop(str(e)[:200])
    if recon_job.status != "complete":
        return stop("repair recon could not observe the portal")
    artifacts = recon.artifacts(db, recon_job.id)
    old_version_row = generator.get_version(db, candidate.id, failed_version)
    old_hosts = set((old_version_row.manifest or {}).get("allowed_hostnames", []))
    new_hosts = {a.hostname for a in artifacts}
    if not new_hosts.issubset(old_hosts):
        return stop("observed hostnames differ from the released allowlist")

    spec = specgen.generate_specification(db, build_request=build_request,
                                          recon_job=recon_job, artifacts=artifacts,
                                          generator_name="repair")
    new_row = generator.generate_candidate_version(
        db, build_request=build_request, spec=spec, created_by="ellis-repair",
        repair_of_version=failed_version,
        limitations=[f"repair of v{failed_version}: {failure_class}"])

    # All required layers rerun on the NEW version (§27, §35.42). The
    # behavioral layer is mode-aware: synthetic corpus in mock/test modes,
    # reversible live structural verification in real modes.
    hosts = (build_request.portal_evidence or {}).get("hostnames", [])
    static_run = testing.run_static_layer(db, new_row)
    contract_run = testing.run_contract_layer(db, new_row, artifacts)
    behavioral_run = testing.run_behavioral_layer(
        db, new_row, observer=observer,
        hostname=(hosts[0] if hosts else "portal.gov.example"))
    ok = static_run.passed and contract_run.passed and behavioral_run.passed
    attempt.new_version = new_row.version
    attempt.outcome = "repaired" if ok else "stopped"
    if not ok:
        attempt.stop_reason = "repaired version failed required test layers"
        db.add(fm.AdapterReviewTask(candidate_id=candidate.id,
                                    candidate_version=new_row.version,
                                    kind="repair_failed",
                                    reason="automatic repair produced a failing version"))
    db.commit()
    audit.record(db, org_id=build_request.org_id,
                 application_id=build_request.application_id,
                 action="adapter_repair_attempted",
                 detail={"class": failure_class, "failed_version": failed_version,
                         "new_version": new_row.version, "outcome": attempt.outcome},
                 actor="ellis")
    return attempt
