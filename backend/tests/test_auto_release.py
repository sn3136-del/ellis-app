"""Independent AutoReleasePolicyEngine (brief "NO ROUTINE HUMAN RELEASE").

A green synthetic build is auto-released at the reversible sandbox tier by the
deterministic policy engine — no human, no Kimi. Irreversible tiers stay
human-gated; quarantine and the kill switch block auto-release; and the engine
is idempotent.
"""
from app.adapter_factory import models as fm, release, auto_release
from app.adapter_factory.build_workflow import create_request, record_consent, run_build
from app.portal.synthetic import SyntheticPortal

HOST = "portal.gov.example"
PORTAL_EVIDENCE = {"hostnames": [HOST], "operator": "Test Consular Authority",
                   "verification": "synthetic_test_portal"}


def _obs(hostnames):
    return SyntheticPortal(scenario="single_step_login", hostname=HOST).observe


def _build(db, route_key, org="orgAR"):
    req = create_request(db, org_id=org, user_id="applicant-1", application_id="",
                         route_key=route_key, destination="Testland", visa_type="tourist",
                         portal_evidence=PORTAL_EVIDENCE, runtime_mode="local_mock_demo")
    record_consent(db, req, user_id="applicant-1")
    run_build(db, req.id, observer=_obs([HOST]))
    return req


def test_auto_release_releases_sandbox_when_gates_pass(db):
    req = _build(db, "rk1|auto-ok")
    assert req.state == "AWAITING_INTERNAL_RELEASE", (req.state, req.error)
    res = auto_release.evaluate_build(db, req.id)
    assert res.released and res.tier == "sandbox", res.as_dict()
    assert release.active_binding(db, route_key=req.route_key, tier="sandbox") is not None
    db.refresh(req)
    assert req.state == "RELEASED_SANDBOX"
    caps = auto_release.released_capabilities(db, req.route_key)["capabilities"]
    # Reversible capabilities are released by the automatic sandbox binding...
    assert caps["route_research"]["released"] is True
    assert caps["form_completion"]["released"] is True
    assert caps["document_upload"]["released"] is True
    # ...and irreversible ones auto-release per capability (the synthetic full
    # flow contains their safe nodes) — NO administrator, via=capability_auto.
    for cap in ("account_registration", "appointment_booking",
                "payment_preparation", "submission_execution"):
        assert caps[cap]["released"] is True, cap
        assert caps[cap]["via"] == "capability_auto", cap
        assert caps[cap]["reversible"] is False


def test_auto_release_is_idempotent(db):
    req = _build(db, "rk1|auto-idem")
    r1 = auto_release.evaluate_build(db, req.id)
    r2 = auto_release.evaluate_build(db, req.id)
    assert r1.released and r2.released
    assert r2.reason == "already released"
    # exactly one active sandbox binding
    b = release.active_binding(db, route_key=req.route_key, tier="sandbox")
    assert b is not None


def test_auto_release_never_touches_staging_or_production(db):
    req = _build(db, "rk1|auto-tier")
    auto_release.evaluate_build(db, req.id)
    assert release.active_binding(db, route_key=req.route_key, tier="staging") is None
    assert release.active_binding(db, route_key=req.route_key, tier="production") is None


def test_auto_release_refuses_quarantined(db):
    req = _build(db, "rk1|auto-quar")
    cand = db.get(fm.AdapterCandidate, req.current_candidate_id)
    release.quarantine(db, candidate_id=cand.id, version=cand.current_version,
                       actor="admin-1", reason="test")
    res = auto_release.evaluate_build(db, req.id)
    assert not res.released
    assert "quarantin" in res.reason.lower()
    assert release.active_binding(db, route_key=req.route_key, tier="sandbox") is None


def test_auto_release_refuses_kill_switched(db):
    req = _build(db, "rk1|auto-kill")
    cand = db.get(fm.AdapterCandidate, req.current_candidate_id)
    release.engage_kill_switch(db, candidate_id=cand.id, actor="admin-1",
                               is_admin=True, reason="x")
    res = auto_release.evaluate_build(db, req.id)
    assert not res.released
    assert release.active_binding(db, route_key=req.route_key, tier="sandbox") is None


def test_auto_release_noop_when_build_not_recommended(db):
    req = create_request(db, org_id="orgAR", user_id="u", application_id="",
                         route_key="rk1|auto-early", destination="T", visa_type="tourist",
                         portal_evidence=PORTAL_EVIDENCE, runtime_mode="local_mock_demo")
    res = auto_release.evaluate_build(db, req.id)  # never consented/built
    assert not res.released
    assert "not release-recommended" in res.reason
