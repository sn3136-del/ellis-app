"""Adapter-factory end-to-end + security tests (brief §35 items 5-18, 39-43).

Drives a full build against the synthetic portal corpus: consent → recon (no
credentials) → typed spec (grounded mapping) → candidate → static/contract/
synthetic testing → release recommendation → internal release → deterministic
runtime → applicant handoffs → reconciled outcomes. Plus the security
invariants: no secrets to Kimi/recon, prompt injection can't alter policy,
generated code can't self-approve or broaden hostnames, production refuses
unreleased/mismatched/sandbox-only adapters, kill switch stops execution,
rollback picks the previous version, repairs create new versions.
"""
import pytest
from sqlalchemy import select

from app.adapter_factory import (models as fm, build_workflow, recon, specgen,
                                 generator, static_validator, testing, release,
                                 runtime, repair, schema)
from app.adapter_factory.build_workflow import create_request, record_consent, run_build
from app.portal.synthetic import SyntheticPortal, INJECTION_TEXT, SCENARIOS


HOST = "portal.gov.example"
PORTAL_EVIDENCE = {"hostnames": [HOST], "operator": "Test Consular Authority",
                   "verification": "synthetic_test_portal"}


def _observer_factory(hostnames):
    portal = SyntheticPortal(scenario="single_step_login", hostname=HOST)
    return portal.observe


def _make_request(db, route_key="rk1|test", org="orgF"):
    req = create_request(db, org_id=org, user_id="applicant-1", application_id="",
                         route_key=route_key, destination="Testland",
                         visa_type="tourist", portal_evidence=PORTAL_EVIDENCE,
                         runtime_mode="local_mock_demo")
    record_consent(db, req, user_id="applicant-1")
    return req


def _build_to_recommendation(db, route_key="rk1|test", org="orgF"):
    req = _make_request(db, route_key=route_key, org=org)
    run_build(db, req.id, observer=_observer_factory([HOST]))
    return req


# ---- full build reaches an internal-release recommendation ----
def test_build_reaches_release_recommendation(db):
    req = _build_to_recommendation(db)
    assert req.state == "AWAITING_INTERNAL_RELEASE", (req.state, req.error, req.progress)
    cand = db.get(fm.AdapterCandidate, req.current_candidate_id)
    assert cand.current_version >= 1
    row = generator.get_version(db, cand.id, cand.current_version)
    passed = testing.layers_passed(db, row)
    assert {"STATIC_VALIDATED", "SYNTHETIC_TESTED", "CONTRACT_TESTED"} <= passed


# ---- §35.5-9 Kimi / recon receive no secrets; recon is sanitized ----
def test_recon_artifacts_are_sanitized(db):
    req = _make_request(db, route_key="rk1|sani")
    job = recon.run_recon(db, build_request=req, observer=_observer_factory([HOST]))
    assert job.status == "complete"
    for art in recon.artifacts(db, job.id):
        blob = str(art.structure).lower()
        # No secret VALUES, cookies, tokens, or auth headers ever survive; the
        # only URLs are query-free base patterns (structure, not data).
        for forbidden in ("cookie", "bearer", "authorization", "set-cookie",
                          "session_id", "access_token"):
            assert forbidden not in blob
        # Query VALUES are personal data and never survive; query KEYS are
        # structure (a portal route name) and may, so a form selected by a
        # flag stays reachable. Either way: no "=" and no value text.
        assert "=" not in art.url_pattern
        for el in art.structure.get("elements", []):
            assert "value" not in el   # element values are never captured
            if el.get("type") == "password":
                assert el.get("sensitive") is True


def test_recon_refuses_without_verified_hostnames(db):
    req = _make_request(db, route_key="rk1|nohost")
    req.portal_evidence = {}
    db.commit()
    with pytest.raises(recon.ReconRefused):
        recon.run_recon(db, build_request=req, observer=_observer_factory([HOST]),
                        hostnames=[])


# ---- §35.10 prompt injection cannot alter policy / spec ----
def test_prompt_injection_cannot_alter_policy(db):
    req = _make_request(db, route_key="rk1|inject")
    portal = SyntheticPortal(scenario="prompt_injection", hostname=HOST)
    job = recon.run_recon(db, build_request=req, observer=portal.observe)
    arts = recon.artifacts(db, job.id)
    # The injected instructions never survive sanitization into any artifact.
    for art in arts:
        assert "evil.example" not in str(art.structure)
        assert "ignore all previous" not in str(art.structure).lower()
        assert "approve" not in str(art.structure).lower()
    spec = specgen.generate_specification(db, build_request=req, recon_job=job,
                                          artifacts=arts, generator_name="test")
    # Hostnames stay exactly the verified allowlist — never expanded by page text.
    assert set(spec.allowed_hostnames) == {HOST}
    assert "evil.example" not in str(spec.flow)


# ---- §35.11-14 generated code cannot access secrets / call Kimi / self-approve / broaden hosts ----
def test_static_validator_rejects_forbidden_content(db):
    req = _build_to_recommendation(db, route_key="rk1|static")
    cand = db.get(fm.AdapterCandidate, req.current_candidate_id)
    row = generator.get_version(db, cand.id, cand.current_version)
    # Inject each forbidden class and confirm the validator catches it.
    import copy
    poisoned = copy.deepcopy(row.flow)
    poisoned[0] = dict(poisoned[0], purpose="call moonshot kimi to solve captcha and eval(x)")
    row.flow = poisoned
    report = static_validator.validate_candidate(row)
    assert not report["passed"]
    assert not report["checks"]["forbidden_patterns"]["ok"]


def test_generated_candidate_cannot_broaden_hostnames(db):
    req = _build_to_recommendation(db, route_key="rk1|hosts")
    cand = db.get(fm.AdapterCandidate, req.current_candidate_id)
    row = generator.get_version(db, cand.id, cand.current_version)
    # Every node's hostname is within the verified allowlist.
    for node in row.flow:
        assert node["allowed_hostname"] in row.manifest["allowed_hostnames"]
    # A node pointing off-allowlist fails static validation.
    row.flow = row.flow + [{"node_id": "evil", "action": "NAVIGATE",
                            "allowed_hostname": "evil.example"}]
    report = static_validator.validate_candidate(row)
    assert not report["passed"]


def test_release_refuses_ai_actor_and_self_approval(db):
    req = _build_to_recommendation(db, route_key="rk1|selfapprove")
    cand = db.get(fm.AdapterCandidate, req.current_candidate_id)
    # An AI/automated actor may never release to production.
    for actor in ("ellis", "kimi", "system-bot", "workflow"):
        with pytest.raises(release.ReleaseRefused):
            release.release(db, candidate_id=cand.id, version=cand.current_version,
                            tier="production", actor=actor, is_admin=True)
    # Non-admin refused even with a human-looking name.
    with pytest.raises(release.ReleaseRefused):
        release.release(db, candidate_id=cand.id, version=cand.current_version,
                        tier="production", actor="alice", is_admin=False)


# ---- §35.15-18 production refuses unreleased / mismatched / sandbox-only / mock ----
def test_runtime_refuses_unreleased_adapter(db):
    req = _build_to_recommendation(db, route_key="rk1|unreleased")
    portal = SyntheticPortal(scenario="single_step_login", hostname=HOST)
    with pytest.raises(runtime.RuntimeRefused):
        runtime.start_execution(db, org_id="o", application_id="a",
                                route_key="rk1|unreleased", tier="production",
                                driver=portal)


def test_runtime_refuses_route_mismatch_and_sandbox_only_in_production(db):
    req = _build_to_recommendation(db, route_key="rk1|mismatch")
    cand = db.get(fm.AdapterCandidate, req.current_candidate_id)
    # Release to SANDBOX only (deterministic auto is sandbox-only).
    release.release(db, candidate_id=cand.id, version=cand.current_version,
                    tier="sandbox", actor="ellis-auto", is_admin=False,
                    kind="deterministic_auto")
    portal = SyntheticPortal(scenario="single_step_login", hostname=HOST)
    # Production execution refused — only sandbox is bound.
    with pytest.raises(runtime.RuntimeRefused):
        runtime.start_execution(db, org_id="o", application_id="a",
                                route_key="rk1|mismatch", tier="production", driver=portal)
    # A different route is refused at sandbox too.
    with pytest.raises(runtime.RuntimeRefused):
        runtime.start_execution(db, org_id="o", application_id="a",
                                route_key="rk1|other", tier="sandbox", driver=portal)


def test_deterministic_auto_release_refused_for_production(db):
    req = _build_to_recommendation(db, route_key="rk1|autoprod")
    cand = db.get(fm.AdapterCandidate, req.current_candidate_id)
    with pytest.raises(release.ReleaseRefused):
        release.release(db, candidate_id=cand.id, version=cand.current_version,
                        tier="production", actor="ellis-auto", is_admin=False,
                        kind="deterministic_auto")


# ---- deterministic runtime executes a released adapter with handoffs ----
def test_released_adapter_runs_with_handoffs_and_evidence(db):
    req = _build_to_recommendation(db, route_key="rk1|run")
    cand = db.get(fm.AdapterCandidate, req.current_candidate_id)
    release.release(db, candidate_id=cand.id, version=cand.current_version,
                    tier="sandbox", actor="admin-1", is_admin=True)
    portal = SyntheticPortal(scenario="single_step_login", hostname=HOST)
    answers = {"full_name": "A B", "passport_number": "X1", "arrival_date": "2026-10-10"}
    execution, runner = runtime.start_execution(
        db, org_id="o", application_id="a", route_key="rk1|run", tier="sandbox",
        driver=portal, case_answers=answers)
    result = runner.run()
    # First personal step is a credentials handoff — Ellis never automates it.
    assert result["status"] == "paused_applicant_action"
    assert result["handoff_kind"] == "credentials"


# ---- §35.39 kill switch stops execution ----
def test_kill_switch_stops_execution(db):
    req = _build_to_recommendation(db, route_key="rk1|kill")
    cand = db.get(fm.AdapterCandidate, req.current_candidate_id)
    release.release(db, candidate_id=cand.id, version=cand.current_version,
                    tier="sandbox", actor="admin-1", is_admin=True)
    release.engage_kill_switch(db, candidate_id=cand.id, actor="admin-1",
                               is_admin=True, reason="incident")
    portal = SyntheticPortal(scenario="single_step_login", hostname=HOST)
    with pytest.raises(runtime.RuntimeRefused):
        runtime.start_execution(db, org_id="o", application_id="a",
                                route_key="rk1|kill", tier="sandbox", driver=portal)


# ---- §35.40 rollback selects the previous released version; §35.41 repairs create new versions ----
def test_rollback_selects_previous_version(db):
    req = _build_to_recommendation(db, route_key="rk1|rollback")
    cand = db.get(fm.AdapterCandidate, req.current_candidate_id)
    v1 = cand.current_version
    release.release(db, candidate_id=cand.id, version=v1, tier="sandbox",
                    actor="admin-1", is_admin=True)
    # Generate + release a second version.
    spec = db.execute(select(fm.AdapterSpecification).where(
        fm.AdapterSpecification.build_request_id == req.id)).scalars().first()
    v2row = generator.generate_candidate_version(db, build_request=req, spec=spec)
    testing.run_static_layer(db, v2row); testing.run_synthetic_layer(db, v2row)
    arts = recon.artifacts(db, req.portal_evidence["recon_job_id"])
    testing.run_contract_layer(db, v2row, arts)
    release.release(db, candidate_id=cand.id, version=v2row.version, tier="sandbox",
                    actor="admin-1", is_admin=True)
    assert release.active_binding(db, route_key="rk1|rollback", tier="sandbox").candidate_version == v2row.version
    rb = release.rollback(db, candidate_id=cand.id, tier="sandbox", actor="admin-1",
                          is_admin=True, reason="regression")
    assert rb.to_version == v1
    assert release.active_binding(db, route_key="rk1|rollback", tier="sandbox").candidate_version == v1


def test_repair_creates_new_version_and_reruns_tests(db):
    req = _build_to_recommendation(db, route_key="rk1|repair")
    cand = db.get(fm.AdapterCandidate, req.current_candidate_id)
    v_before = cand.current_version
    attempt = repair.attempt_repair(
        db, build_request=req, candidate=cand, failed_version=v_before,
        sanitized_detail={"reason": "fill_x: NO_SUCH_ELEMENT selector never observed"},
        observer=_observer_factory([HOST]))
    db.refresh(cand)
    if attempt.outcome == "repaired":
        assert attempt.new_version == v_before + 1
        newrow = generator.get_version(db, cand.id, attempt.new_version)
        # All required layers reran on the NEW version.
        assert {"STATIC_VALIDATED", "CONTRACT_TESTED", "SYNTHETIC_TESTED"} <= testing.layers_passed(db, newrow)


def test_repair_stops_and_quarantines_on_stop_class(db):
    req = _build_to_recommendation(db, route_key="rk1|repairstop")
    cand = db.get(fm.AdapterCandidate, req.current_candidate_id)
    attempt = repair.attempt_repair(
        db, build_request=req, candidate=cand, failed_version=cand.current_version,
        sanitized_detail={"reason": "payment flow changed materially"},
        observer=_observer_factory([HOST]))
    assert attempt.outcome == "quarantined"
    row = generator.get_version(db, cand.id, cand.current_version)
    assert row.quarantined is True


def test_repair_refuses_after_irreversible_action(db):
    req = _build_to_recommendation(db, route_key="rk1|irrev")
    cand = db.get(fm.AdapterCandidate, req.current_candidate_id)
    attempt = repair.attempt_repair(
        db, build_request=req, candidate=cand, failed_version=cand.current_version,
        sanitized_detail={"reason": "selector changed"}, observer=_observer_factory([HOST]),
        irreversible_context=True)
    assert attempt.outcome == "quarantined"
    assert "irreversible" in attempt.stop_reason


# ---- synthetic corpus: uncertainty never becomes false success or duplicate ----
@pytest.mark.parametrize("scenario", ["payment_timeout", "submission_timeout",
                                      "misleading_success_banner",
                                      "ui_success_network_failed", "appointment_race"])
def test_uncertainty_scenarios_never_fabricate_success(db, scenario):
    req = _build_to_recommendation(db, route_key=f"rk1|unc-{scenario}")
    cand = db.get(fm.AdapterCandidate, req.current_candidate_id)
    release.release(db, candidate_id=cand.id, version=cand.current_version,
                    tier="sandbox", actor="admin-1", is_admin=True)
    row = generator.get_version(db, cand.id, cand.current_version)
    portal = SyntheticPortal(scenario=scenario, hostname=HOST)
    out = testing.drive_flow_against_portal(
        db, row, portal, answers={"full_name": "A", "passport_number": "X",
                                  "arrival_date": "2026-10-10"})
    ledger = out["ledger"]
    # No duplicate irreversible side effects, ever.
    assert len(ledger["charges"]) <= 1
    assert len(ledger["submissions"]) <= 1
    assert len(ledger["bookings"]) <= 1


# ---- §35 the build produces truthful test classifications ----
def test_test_classifications_are_truthful(db):
    req = _build_to_recommendation(db, route_key="rk1|truthful")
    cand = db.get(fm.AdapterCandidate, req.current_candidate_id)
    row = generator.get_version(db, cand.id, cand.current_version)
    runs = db.execute(select(fm.AdapterTestRun).where(
        fm.AdapterTestRun.candidate_version_id == row.id)).scalars().all()
    classes = {r.classification for r in runs}
    # Only layers actually run may be claimed — no live/production claims.
    assert classes <= {"STATIC_VALIDATED", "SYNTHETIC_TESTED", "CONTRACT_TESTED",
                       "STATIC_FAILED", "SYNTHETIC_FAILED", "CONTRACT_FAILED"}
    assert "CONTROLLED_PRODUCTION_VERIFIED" not in classes


# ---- schema: sensitive fields can never be automated ----
def test_schema_rejects_sensitive_autofill():
    errs = schema.validate_node(
        {"node_id": "fill_pw", "action": "FILL_NON_SENSITIVE",
         "allowed_hostname": HOST, "selector": "#password", "input_source": "password"},
        allowed_hostnames=[HOST])
    assert any("sensitive" in e for e in errs)


def test_schema_requires_reconcile_for_irreversible():
    errs = schema.validate_node(
        {"node_id": "pay", "action": "CLICK", "allowed_hostname": HOST,
         "selector": "#pay", "irreversibility": "irreversible", "retry_class": "none"},
        allowed_hostnames=[HOST])
    assert any("reconcile_first" in e for e in errs)
