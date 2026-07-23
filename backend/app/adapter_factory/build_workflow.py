"""Durable adapter-build workflow (brief §10-§11).

Drives one AdapterBuildRequest through the 30-state machine, persisting the
state after EVERY transition so any restart (API, worker, Electron, browser)
resumes at the checkpoint. Stages are idempotent: re-running a completed
stage is a no-op, and irreversible work never repeats because each stage
checks for its own prior output before producing it.

The build touches only public portal structure (recon is credential-free);
no payment, booking, or submission ever happens here (§10).
"""
from __future__ import annotations

from sqlalchemy import select

from .. import audit
from ..visa_snapshot.authority import is_government_host
from . import models as fm
from . import recon, specgen, generator, testing, repair
from .statemachine import transition, applicant_label

CONSENT_TEXT_VERSION = "abc-1"

# What the §10 consent screen explains, keyed for i18n parity.
CONSENT_DISCLOSURES = [
    "will_research_and_map_portal",
    "kimi_generates_candidate_code",
    "ellis_tests_before_use",
    "may_not_succeed",
    "no_payment_booking_submission_during_build",
    "may_close_and_return",
    "personal_secret_steps_protected",
    "notified_when_action_required",
]

_observer_factory = None


def set_observer_factory(fn):
    """fn(hostnames) -> observer(url)->observation. SyntheticPortal-backed in
    tests/demo; a Browserbase structural probe when live recon lands."""
    global _observer_factory
    _observer_factory = fn


class BuildError(Exception):
    pass


def create_request(db, *, org_id: str, user_id: str, application_id: str,
                   route_key: str, destination: str, visa_type: str,
                   portal_evidence: dict, runtime_mode: str,
                   standing_authorization_id: str = "") -> fm.AdapterBuildRequest:
    existing = db.execute(select(fm.AdapterBuildRequest).where(
        fm.AdapterBuildRequest.org_id == org_id,
        fm.AdapterBuildRequest.route_key == route_key,
        fm.AdapterBuildRequest.state.notin_(("DISABLED", "SUPERSEDED")))).scalars().first()
    if existing:
        return existing
    req = fm.AdapterBuildRequest(
        org_id=org_id, user_id=user_id, application_id=application_id,
        route_key=route_key, destination=destination, visa_type=visa_type,
        portal_evidence=portal_evidence or {}, runtime_mode=runtime_mode,
        standing_authorization_id=standing_authorization_id)
    db.add(req)
    db.commit()
    audit.record(db, org_id=org_id, application_id=application_id,
                 action="adapter_build_requested",
                 detail={"route_key": route_key[:120]}, actor=user_id)
    return req


def record_consent(db, req: fm.AdapterBuildRequest, *, user_id: str,
                   locale: str = "en") -> fm.AdapterBuildRequest:
    from datetime import datetime, timezone
    req.consent_given = True
    req.consent_text_version = CONSENT_TEXT_VERSION
    req.consent_locale = locale
    req.consent_at = datetime.now(timezone.utc)
    req.consent_by = user_id
    if req.state == "REQUESTED":
        transition(req, "CONSENT_RECORDED", "applicant consented")
    db.commit()
    audit.record(db, org_id=req.org_id, application_id=req.application_id,
                 action="adapter_build_consented",
                 detail={"text_version": CONSENT_TEXT_VERSION, "locale": locale},
                 actor=user_id)
    return req


def _note(db, req, text: str):
    notes = list(req.progress or [])
    notes.append(text[:200])
    req.progress = notes
    db.commit()


def run_build(db, request_id: str, *, observer=None, max_stages: int = 40) -> fm.AdapterBuildRequest:
    """Advance the build as far as it can go without human input. Safe to call
    repeatedly (resume); every stage re-checks its own completion."""
    req = db.get(fm.AdapterBuildRequest, request_id)
    if req is None:
        raise BuildError("build request not found")
    if not req.consent_given:
        raise BuildError("consent required before the build may start (§10)")
    req.attempts += 1
    db.commit()

    for _ in range(max_stages):
        st = req.state
        try:
            if st == "CONSENT_RECORDED":
                transition(req, "ROUTE_RESEARCH_PENDING")
            elif st == "ROUTE_RESEARCH_PENDING":
                # Reuse stored research; a build never re-crawls the world.
                transition(req, "PORTAL_VERIFICATION_PENDING",
                           "stored route research reused" if req.research_version
                           else "route research linked at request time")
            elif st == "PORTAL_VERIFICATION_PENDING":
                hosts = (req.portal_evidence or {}).get("hostnames", [])
                verified = (req.portal_evidence or {}).get("verification") == "synthetic_test_portal" \
                    or all(is_government_host(h) for h in hosts if h)
                if hosts and verified:
                    transition(req, "PORTAL_VERIFIED", f"{len(hosts)} verified hostname(s)")
                else:
                    transition(req, "MANUAL_REVIEW_REQUIRED", "portal identity unverified")
                    _review(db, req, "portal_verification",
                            "portal hostnames missing or not verifiably official")
            elif st == "PORTAL_VERIFIED":
                transition(req, "JURISDICTION_VERIFICATION_PENDING")
            elif st == "JURISDICTION_VERIFICATION_PENDING":
                juris_ok = bool((req.jurisdiction_evidence or {}).get("verified")) or \
                    (req.portal_evidence or {}).get("verification") == "synthetic_test_portal"
                if juris_ok:
                    transition(req, "JURISDICTION_VERIFIED")
                else:
                    transition(req, "MANUAL_REVIEW_REQUIRED", "jurisdiction unverified")
                    _review(db, req, "jurisdiction_verification", "jurisdiction evidence missing")
            elif st == "JURISDICTION_VERIFIED":
                transition(req, "AUTOMATION_POLICY_REVIEW")
            elif st == "AUTOMATION_POLICY_REVIEW":
                decision = _policy_review(db, req)
                if decision in ("automation_permitted", "handoff_only"):
                    transition(req, "RECON_PENDING", f"policy: {decision}")
                else:
                    transition(req, "MANUAL_REVIEW_REQUIRED", f"policy: {decision}")
                    _review(db, req, "automation_policy", f"portal policy decision: {decision}")
            elif st == "RECON_PENDING":
                transition(req, "RECON_RUNNING")
            elif st == "RECON_RUNNING":
                obs = observer or (_observer_factory and _observer_factory(
                    (req.portal_evidence or {}).get("hostnames", [])))
                if obs is None:
                    transition(req, "MANUAL_REVIEW_REQUIRED",
                               "no credential-free portal observer available")
                    _review(db, req, "recon_unavailable",
                            "live structural reconnaissance is not yet wired for this portal")
                    break
                job = recon.run_recon(db, build_request=req, observer=obs)
                if job.status != "complete":
                    transition(req, "MANUAL_REVIEW_REQUIRED", f"recon: {job.error[:80]}")
                    _review(db, req, "recon_failed", job.error or "recon failed")
                else:
                    req.portal_evidence = dict(req.portal_evidence or {},
                                               recon_job_id=job.id)
                    transition(req, "SPECIFICATION_GENERATED",
                               f"{job.pages_observed} pages mapped")
                    arts = recon.artifacts(db, job.id)
                    spec = specgen.generate_specification(
                        db, build_request=req, recon_job=job, artifacts=arts,
                        generator_name="kimi-k3+deterministic-skeleton")
                    req.portal_evidence = dict(req.portal_evidence, spec_id=spec.id)
                    db.commit()
            elif st == "SPECIFICATION_GENERATED":
                spec = db.get(fm.AdapterSpecification,
                              (req.portal_evidence or {}).get("spec_id", ""))
                if spec is None:
                    transition(req, "MANUAL_REVIEW_REQUIRED", "specification missing")
                    _review(db, req, "spec_missing", "specification row disappeared")
                else:
                    generator.generate_candidate_version(db, build_request=req, spec=spec)
                    transition(req, "CODE_GENERATED")
            elif st == "CODE_GENERATED":
                transition(req, "STATIC_VALIDATION_RUNNING")
            elif st == "STATIC_VALIDATION_RUNNING":
                row = _current_version(db, req)
                run = testing.run_static_layer(db, row)
                transition(req, "CONTRACT_TESTING" if run.passed else "TESTS_FAILED",
                           "static green" if run.passed else "static failed")
            elif st == "CONTRACT_TESTING":
                row = _current_version(db, req)
                arts = recon.artifacts(db, (req.portal_evidence or {}).get("recon_job_id", ""))
                run = testing.run_contract_layer(db, row, arts)
                transition(req, "SYNTHETIC_TESTING" if run.passed else "TESTS_FAILED",
                           "contract green" if run.passed else "contract failed")
            elif st == "SYNTHETIC_TESTING":
                row = _current_version(db, req)
                run = testing.run_synthetic_layer(db, row)
                transition(req, "TESTS_PASSED" if run.passed else "TESTS_FAILED",
                           "synthetic corpus green" if run.passed else "synthetic failed")
            elif st == "TESTS_FAILED":
                cand = db.get(fm.AdapterCandidate, req.current_candidate_id)
                row = _current_version(db, req)
                failure = _last_failure_detail(db, row)
                transition(req, "AUTOMATIC_REPAIR")
                obs = observer or (_observer_factory and _observer_factory(
                    (req.portal_evidence or {}).get("hostnames", [])))
                attempt = repair.attempt_repair(
                    db, build_request=req, candidate=cand,
                    failed_version=row.version, sanitized_detail=failure,
                    observer=obs)
                if attempt.outcome == "repaired":
                    transition(req, "CODE_GENERATED", f"repaired as v{attempt.new_version}")
                    # Version already tested by repair; jump the state machine
                    # forward honestly by re-running the (idempotent) layers.
                else:
                    transition(req, "QUARANTINED", attempt.stop_reason[:100])
                    break
            elif st == "TESTS_PASSED":
                cand = db.get(fm.AdapterCandidate, req.current_candidate_id)
                cand.status = "release_recommended"
                transition(req, "RELEASE_RECOMMENDED",
                           "all required layers green — recommendation recorded")
            elif st == "RELEASE_RECOMMENDED":
                transition(req, "AWAITING_INTERNAL_RELEASE",
                           "internal release action required")
                break
            else:
                break
        except Exception as e:  # noqa: BLE001 — honest failure, never a crash
            req.error = str(e)[:400]
            _note(db, req, f"error at {st}: {str(e)[:120]}")
            if req.state not in ("MANUAL_REVIEW_REQUIRED", "QUARANTINED"):
                try:
                    transition(req, "MANUAL_REVIEW_REQUIRED", str(e)[:100])
                except Exception:  # noqa: BLE001
                    pass
            db.commit()
            break
        db.commit()
    db.commit()
    return req


def _policy_review(db, req) -> str:
    row = db.execute(select(fm.PortalAutomationPolicyReview).where(
        fm.PortalAutomationPolicyReview.route_key == req.route_key)).scalars().first()
    if row is None:
        hosts = (req.portal_evidence or {}).get("hostnames", [])
        synthetic = (req.portal_evidence or {}).get("verification") == "synthetic_test_portal"
        row = fm.PortalAutomationPolicyReview(
            route_key=req.route_key,
            portal_operator=(req.portal_evidence or {}).get("operator", ""),
            hostnames=hosts,
            decision="automation_permitted" if synthetic else "pending",
            basis={"synthetic": synthetic},
            reviewed_by="deterministic-default" if synthetic else "")
        db.add(row)
        db.commit()
    return row.decision


def _review(db, req, kind: str, reason: str):
    db.add(fm.AdapterReviewTask(candidate_id=req.current_candidate_id or req.id,
                                kind=kind, reason=reason[:400]))
    db.commit()


def _current_version(db, req) -> fm.AdapterCandidateVersion:
    cand = db.get(fm.AdapterCandidate, req.current_candidate_id)
    row = generator.get_version(db, cand.id, cand.current_version)
    if row is None:
        raise BuildError("current candidate version missing")
    return row


def _last_failure_detail(db, version_row) -> dict:
    run = db.execute(select(fm.AdapterTestRun).where(
        fm.AdapterTestRun.candidate_version_id == version_row.id,
        fm.AdapterTestRun.passed.is_(False)).order_by(
        fm.AdapterTestRun.created_at.desc())).scalars().first()
    if run is None:
        return {"reason": "unknown"}
    summary = run.summary or {}
    for scenario, det in (summary.get("scenarios") or {}).items():
        if not det.get("ok"):
            return {"reason": "; ".join(det.get("problems", []))[:200],
                    "scenario": scenario}
    detail = summary.get("detail") or summary.get("problems") or {}
    return {"reason": str(detail)[:200]}


def status_for_applicant(req: fm.AdapterBuildRequest) -> dict:
    """§13/§29: only simple labels reach the applicant."""
    return {"id": req.id, "route_key": req.route_key,
            "label": applicant_label(req.state),
            "consented": req.consent_given,
            "progress_notes": (req.progress or [])[-3:],
            "updated_at": req.updated_at.isoformat() if req.updated_at else None}
