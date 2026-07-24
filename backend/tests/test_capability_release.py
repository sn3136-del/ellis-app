"""Per-capability AutoReleasePolicyEngine + fail-closed runtime gating.

Each irreversible capability (account_registration, appointment_booking,
payment_preparation, submission_execution) auto-releases when its OBJECTIVE
structural gate passes — no administrator. The runtime refuses to perform a
capability unless it is released AND the case's standing authorization covers it
(and, for submission, a current signed review + confirmed payment). Kill switch
and quarantine revoke capability releases. All reversible: synthetic flows only.
"""
import pytest

from app.adapter_factory import (models as fm, release, auto_release, runtime,
                                 generator)
from app.adapter_factory.build_workflow import (create_request, record_consent,
                                                run_build)
from app.adapter_factory.compiler import compile_flow
from app.portal.synthetic import SyntheticPortal

HOST = "portal.gov.example"
PORTAL_EVIDENCE = {"hostnames": [HOST], "operator": "Test Consular Authority",
                   "verification": "synthetic_test_portal"}


def _obs(hostnames):
    return SyntheticPortal(scenario="single_step_login", hostname=HOST).observe


def _build_and_release(db, route_key, org="orgCAP"):
    req = create_request(db, org_id=org, user_id="applicant-1", application_id="",
                         route_key=route_key, destination="Testland", visa_type="tourist",
                         portal_evidence=PORTAL_EVIDENCE, runtime_mode="local_mock_demo")
    record_consent(db, req, user_id="applicant-1")
    run_build(db, req.id, observer=_obs([HOST]))
    auto_release.evaluate_build(db, req.id)
    return req


def _version(db, req):
    cand = db.get(fm.AdapterCandidate, req.current_candidate_id)
    return generator.get_version(db, cand.id, cand.current_version), cand


# ---- each irreversible capability auto-releases on its objective gate --------
def test_all_capabilities_auto_release_when_flow_supports_them(db):
    req = _build_and_release(db, "rk1|cap-all")
    caps = auto_release.released_capabilities(db, req.route_key)["capabilities"]
    for cap in auto_release.IRREVERSIBLE_CAPABILITIES:
        assert caps[cap]["released"] is True, cap
        assert caps[cap]["via"] == "capability_auto"
    # And a persisted, auditable evidence record backs each one.
    for cap in auto_release.IRREVERSIBLE_CAPABILITIES:
        row = auto_release.capability_released(db, route_key=req.route_key, capability=cap)
        assert row and row.released_by == auto_release.AUTO_ACTOR and row.evidence


def test_capability_gate_requires_the_safe_flow_structure(db):
    """A flow WITHOUT the submit reconciliation / official-evidence nodes must
    NOT release submission_execution — the gate is structural, never optimistic."""
    req = _build_and_release(db, "rk1|cap-gate")
    version_row, _ = _version(db, req)
    # Strip the submit reconciliation + official-record verify from the flow copy.
    flow = [n for n in (version_row.flow or [])
            if n.get("action") != "RECONCILE_OUTCOME"]

    class _V:
        pass
    v = _V(); v.flow = flow
    ok, problems, _ = auto_release.capability_gate(v, "submission_execution")
    assert ok is False and any("reconcil" in p for p in problems)
    ok2, problems2, _ = auto_release.capability_gate(v, "appointment_booking")
    assert ok2 is False and any("reconcil" in p for p in problems2)


def test_no_administrator_action_required(db):
    """The engine actor is the deterministic policy engine, never a human, and
    the whole flow ran through run_build + evaluate_build with no admin call."""
    req = _build_and_release(db, "rk1|cap-noadmin")
    for cap in auto_release.IRREVERSIBLE_CAPABILITIES:
        row = auto_release.capability_released(db, route_key=req.route_key, capability=cap)
        assert row.released_by == "auto-release-policy-engine"
        assert "admin" not in row.released_by


def test_kill_switch_revokes_all_capability_releases(db):
    req = _build_and_release(db, "rk1|cap-kill")
    _, cand = _version(db, req)
    assert auto_release.capability_released(db, route_key=req.route_key,
                                            capability="submission_execution")
    release.engage_kill_switch(db, candidate_id=cand.id, actor="admin-1",
                               is_admin=True, reason="emergency")
    for cap in auto_release.IRREVERSIBLE_CAPABILITIES:
        assert auto_release.capability_released(db, route_key=req.route_key,
                                                capability=cap) is None


def test_quarantine_revokes_capability_releases(db):
    req = _build_and_release(db, "rk1|cap-quar")
    version_row, cand = _version(db, req)
    release.quarantine(db, candidate_id=cand.id, version=version_row.version,
                       actor="admin-1", reason="bad")
    for cap in auto_release.IRREVERSIBLE_CAPABILITIES:
        assert auto_release.capability_released(db, route_key=req.route_key,
                                                capability=cap) is None


def test_kimi_cannot_write_a_capability_release(db):
    """The capability release is a deterministic engine record; there is no path
    for a model actor to create one — the only writer is evaluate_capabilities,
    which reads persisted evidence, never model output."""
    import inspect
    src = inspect.getsource(auto_release.evaluate_capabilities)
    assert "kimi" not in src.lower() and "LiveKimi" not in src
    # And a human/AI-actor release() to a real tier is still refused entirely.
    req = _build_and_release(db, "rk1|cap-kimi")
    _, cand = _version(db, req)
    with pytest.raises(release.ReleaseRefused):
        release.release(db, candidate_id=cand.id, version=cand.current_version,
                        tier="production", actor="kimi-agent", is_admin=True,
                        kind="human_admin")


# ---- fail-closed runtime gating ---------------------------------------------
def _new_authorized_case(db, org="orgCAP"):
    from tests.test_e2e import _new_case
    return _new_case(db, org=org)


def test_runtime_refuses_capability_not_released(db):
    """A released sandbox binding is not enough: an irreversible node whose
    capability is NOT released is refused before any session opens."""
    req = _build_and_release(db, "rk1|cap-rt1")
    # Revoke submission capability only.
    _, cand = _version(db, req)
    row = auto_release.capability_released(db, route_key=req.route_key,
                                           capability="submission_execution")
    row.active = False
    db.commit()
    version_row, _ = _version(db, req)
    app_id = _new_authorized_case(db)
    with pytest.raises(runtime.RuntimeRefused) as e:
        runtime.assert_execution_allowed(db, route_key=req.route_key,
                                         application_id=app_id,
                                         compiled=compile_flow(version_row))
    assert "submission_execution" in str(e.value)


def test_runtime_refuses_without_standing_authorization(db):
    req = _build_and_release(db, "rk1|cap-rt2")
    version_row, _ = _version(db, req)
    with pytest.raises(runtime.RuntimeRefused) as e:
        runtime.assert_execution_allowed(db, route_key=req.route_key,
                                         application_id="no-such-app",
                                         compiled=compile_flow(version_row))
    assert "standing authorization" in str(e.value)


def test_runtime_refuses_submission_without_signature_and_payment(db):
    """Standing auth + released capability are not enough for submission: an
    unsigned/unpaid case is refused (the applicant's signature + exact payment
    remain mandatory)."""
    from app import models as app_models, payments
    req = _build_and_release(db, "rk1|cap-rt3")
    version_row, _ = _version(db, req)
    app_id = _new_authorized_case(db)   # authorized, but NOT signed / paid
    with pytest.raises(runtime.RuntimeRefused) as e:
        runtime.assert_execution_allowed(db, route_key=req.route_key,
                                         application_id=app_id,
                                         compiled=compile_flow(version_row))
    assert "submission blocked" in str(e.value)
    # After a signed final review + confirmed exact-amount payment, the
    # submission precondition is satisfied (other caps still need their checks).
    from tests.test_e2e import _sign_final_review
    _sign_final_review(db, app_id)
    app_row = db.get(app_models.VisaApplication, app_id)
    payments.authorize(db, app_row=app_row, principal_user="user1",
                       amount_cents=5000, currency="USD", government_fee_cents=5000)
    ok, why = runtime._submission_preconditions(db, app_id)
    assert ok is True, why


def test_signature_invalidates_after_material_change_blocks_submission(db):
    """§25: a material change after signature invalidates it — submission
    precondition then fails again until re-signed."""
    from app import models as app_models, payments, final_review
    req = _build_and_release(db, "rk1|cap-rt4")
    app_id = _new_authorized_case(db)
    from tests.test_e2e import _sign_final_review
    _sign_final_review(db, app_id)
    app_row = db.get(app_models.VisaApplication, app_id)
    payments.authorize(db, app_row=app_row, principal_user="user1",
                       amount_cents=5000, currency="USD", government_fee_cents=5000)
    assert runtime._submission_preconditions(db, app_id)[0] is True
    # Material change: edit the application answers after signing.
    app_row.answers = {**(app_row.answers or {}), "full_name": "Changed Name"}
    app_row.current_version = (app_row.current_version or 1) + 1
    db.commit()
    ok, why = runtime._submission_preconditions(db, app_id)
    assert ok is False and "signed final review" in why


def test_navigation_only_flow_needs_no_irreversible_capability(db):
    """A flow with only NAVIGATE/COMPLETE (the real INM case) requires no
    irreversible capability — execution is allowed with just the sandbox
    release (proven live earlier; here asserted structurally)."""
    from app.adapter_factory.compiler import CompiledFlow
    nav = CompiledFlow(
        nodes=[{"node_id": "open", "action": "NAVIGATE", "allowed_hostname": HOST},
               {"node_id": "done", "action": "COMPLETE"}],
        order=["open", "done"], manifest={})
    assert runtime._required_capabilities(nav) == set()
    # No capabilities required -> no gate blockers even without a case.
    runtime.assert_execution_allowed(db, route_key="rk1|nav", application_id="x",
                                     compiled=nav)
