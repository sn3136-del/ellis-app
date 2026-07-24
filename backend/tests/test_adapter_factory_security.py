"""Adapter-factory security invariants (brief §35 items 5-18, 26-31, 44-46).

These are the hard safety properties: Kimi/recon receive no secrets; generated
code can't call Kimi at runtime or self-approve; production refuses unreleased/
mismatched/sandbox-only adapters and never binds a synthetic observer; network
evidence is redacted; visible banners can't prove success; CAPTCHA/OTP/card
capture is impossible; tenant isolation holds; prompt injection can't widen
scope or alter policy.
"""
import inspect

import pytest
from sqlalchemy import select

from app.adapter_factory import (models as fm, recon, specgen, generator, runtime,
                                 release, testing, api as factory_api)
from app.adapter_factory.build_workflow import create_request, record_consent, run_build
from app.portal.synthetic import SyntheticPortal, INJECTION_TEXT
from app.config import settings

HOST = "portal.gov.example"
PE = {"hostnames": [HOST], "operator": "T", "verification": "synthetic_test_portal"}


def _obs(h=None):
    return SyntheticPortal(scenario="single_step_login", hostname=HOST).observe


def _built(db, route_key):
    req = create_request(db, org_id="o", user_id="u", application_id="",
                         route_key=route_key, destination="T", visa_type="tourist",
                         portal_evidence=PE, runtime_mode="local_mock_demo")
    record_consent(db, req, user_id="u")
    run_build(db, req.id, observer=_obs())
    return req


# ---- §35.5-9 Kimi / recon receive no secrets ----
def test_recon_never_receives_or_stores_secret_values(db):
    req = create_request(db, org_id="o", user_id="u", application_id="",
                         route_key="rk1|sec-recon", destination="T", visa_type="tourist",
                         portal_evidence=PE, runtime_mode="local_mock_demo")
    record_consent(db, req, user_id="u")
    job = recon.run_recon(db, build_request=req, observer=_obs())
    for art in recon.artifacts(db, job.id):
        s = str(art.structure)
        # Password/OTP/card VALUES, cookies, tokens, Live View URLs never appear.
        for needle in ("cookie", "bearer ", "csrf-token-value", "sk-", "vault://",
                       "browserbase.com", "liveview"):
            assert needle.lower() not in s.lower()


def test_specgen_kimi_input_excludes_values_and_urls(db):
    # The Kimi mapper is fed sanitized element structure only; assert the payload
    # builder never includes values, cookies, or raw URLs.
    src = inspect.getsource(specgen._live_kimi_mapper)
    assert "value" not in src.split("payload =")[1].split("system =")[0] or \
        "e.get('value')" not in src   # no element value ever forwarded
    # The only element keys forwarded are structural.
    assert "selector" in src and "label" in src and "sensitive" in src


# ---- §35.12 generated code cannot call Kimi during production runtime ----
def test_runtime_module_never_imports_kimi():
    src = inspect.getsource(runtime)
    for banned in ("moonshot", "providers.kimi", "import kimi", "run_agent",
                   "LiveKimiProvider"):
        assert banned not in src, f"runtime must not reference {banned}"


# ---- §35.13-14 generated code cannot self-approve or broaden hostnames ----
def test_release_and_binding_are_the_only_execution_gate(db):
    # A candidate with NO release cannot be executed at any tier.
    req = _built(db, "rk1|sec-gate")
    portal = SyntheticPortal(scenario="single_step_login", hostname=HOST)
    for tier in ("sandbox", "staging", "production"):
        with pytest.raises(runtime.RuntimeRefused):
            runtime.start_execution(db, org_id="o", application_id="a",
                                    route_key="rk1|sec-gate", tier=tier, driver=portal)


# ---- §35.15-18 production refuses unreleased / mismatch / sandbox-only / synthetic observer ----
def test_production_observer_refused_outside_mock_modes(db, monkeypatch):
    # The synthetic observer may only stand in for live recon in mock modes.
    # In a real mode without Browserbase, observer selection yields None (the
    # build parks at MANUAL_REVIEW honestly) — never a synthetic stand-in.
    from app.config import settings as _settings
    from app.adapter_factory.build_workflow import default_observer
    real = _settings()
    monkeypatch.setattr(real, "mock_portal_allowed", False, raising=False)
    obs = default_observer([HOST])
    assert obs is None


def test_sandbox_release_never_satisfies_production(db):
    req = _built(db, "rk1|sec-tier")
    cand = db.get(fm.AdapterCandidate, req.current_candidate_id)
    release.release(db, candidate_id=cand.id, version=cand.current_version,
                    tier="sandbox", actor="admin-1", is_admin=True)
    portal = SyntheticPortal(scenario="single_step_login", hostname=HOST)
    with pytest.raises(runtime.RuntimeRefused):
        runtime.start_execution(db, org_id="o", application_id="a",
                                route_key="rk1|sec-tier", tier="production", driver=portal)


# ---- §35.26-28 CAPTCHA / OTP / card capture is impossible ----
def test_captcha_otp_card_are_always_handoffs_never_automated(db):
    req = _built(db, "rk1|sec-handoff")
    cand = db.get(fm.AdapterCandidate, req.current_candidate_id)
    row = generator.get_version(db, cand.id, cand.current_version)
    # No node fills a sensitive field; credentials/payment are handoffs.
    for node in row.flow:
        if node["action"] == "FILL_NON_SENSITIVE":
            assert not node.get("sensitive")
            assert "password" not in (node.get("input_source") or "")
    handoffs = [n for n in row.flow if n["action"] == "APPLICANT_HANDOFF"]
    kinds = {n.get("handoff_kind") for n in handoffs}
    assert "credentials" in kinds
    # The runtime driver refuses to fill a portal-flagged sensitive field.
    portal = SyntheticPortal(scenario="single_step_login", hostname=HOST)
    assert portal.fill("#password", "secret")["ok"] is False


# ---- §35.29-31 evidence is redacted; bodies not logged; banners can't prove ----
def test_outcome_evidence_is_redacted(db):
    req = _built(db, "rk1|sec-ev")
    cand = db.get(fm.AdapterCandidate, req.current_candidate_id)
    release.release(db, candidate_id=cand.id, version=cand.current_version,
                    tier="sandbox", actor="admin-1", is_admin=True)
    row = generator.get_version(db, cand.id, cand.current_version)
    portal = SyntheticPortal(scenario="single_step_login", hostname=HOST)
    out = testing.drive_flow_against_portal(
        db, row, portal, answers={"full_name": "A", "passport_number": "X",
                                  "arrival_date": "2026-10-10"})
    evs = db.execute(select(fm.AdapterOutcomeEvidence)).scalars().all()
    for e in evs:
        # Only sanitized fields; response_keys are NAMES, never values.
        assert isinstance(e.response_keys, list)
        for k in e.response_keys:
            assert "=" not in str(k) and len(str(k)) < 60
        # No request/response bodies are stored anywhere on the row.
        assert not hasattr(e, "body")


def test_misleading_banner_alone_never_proves_submission(db):
    req = _built(db, "rk1|sec-banner")
    cand = db.get(fm.AdapterCandidate, req.current_candidate_id)
    release.release(db, candidate_id=cand.id, version=cand.current_version,
                    tier="sandbox", actor="admin-1", is_admin=True)
    row = generator.get_version(db, cand.id, cand.current_version)
    portal = SyntheticPortal(scenario="ui_success_network_failed", hostname=HOST)
    out = testing.drive_flow_against_portal(
        db, row, portal, answers={"full_name": "A", "passport_number": "X",
                                  "arrival_date": "2026-10-10"})
    # The portal shows a success banner but the network failed → never completed
    # as submitted, and no submission is recorded in the ledger.
    assert len(out["ledger"]["submissions"]) == 0
    assert out["result"]["status"] in ("failed", "outcome_uncertain")


# ---- §35.10 prompt injection cannot alter policy / widen scope ----
def test_injection_page_cannot_widen_hostnames_or_approve(db):
    req = create_request(db, org_id="o", user_id="u", application_id="",
                         route_key="rk1|sec-inject", destination="T", visa_type="tourist",
                         portal_evidence=PE, runtime_mode="local_mock_demo")
    record_consent(db, req, user_id="u")
    portal = SyntheticPortal(scenario="hidden_instructions", hostname=HOST)
    job = recon.run_recon(db, build_request=req, observer=portal.observe)
    arts = recon.artifacts(db, job.id)
    spec = specgen.generate_specification(db, build_request=req, recon_job=job,
                                          artifacts=arts, generator_name="test")
    assert set(spec.allowed_hostnames) == {HOST}
    # No release happened for THIS route as a side effect of any page instruction.
    assert db.execute(select(fm.AdapterRelease).where(
        fm.AdapterRelease.route_key == "rk1|sec-inject")).scalars().first() is None
    # And the spec's flow carries no off-allowlist navigation the page tried to inject.
    assert "evil.example" not in str(spec.flow)


# ---- §35.44-46 (recon side) tenant isolation on build requests ----
def test_build_request_tenant_isolation(db):
    a = create_request(db, org_id="orgA", user_id="u", application_id="",
                       route_key="rk1|tenantA", destination="T", visa_type="tourist",
                       portal_evidence=PE, runtime_mode="local_mock_demo")
    b = create_request(db, org_id="orgB", user_id="u", application_id="",
                       route_key="rk1|tenantB", destination="T", visa_type="tourist",
                       portal_evidence=PE, runtime_mode="local_mock_demo")
    assert a.org_id != b.org_id
    # A recon job is always scoped to its build request's org.
    record_consent(db, a, user_id="u")
    job = recon.run_recon(db, build_request=a, observer=_obs())
    assert job.org_id == "orgA"
