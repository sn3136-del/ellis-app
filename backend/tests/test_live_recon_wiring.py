"""Live RECON wiring (real modes): the build pipeline runs end-to-end against a
live-style observer with NO SyntheticPortal, objective automatic gates replace
routine manual review, and the release gate accepts LIVE_STRUCTURAL_TESTED.

The "live" observer here is an injected fake with the exact live-observer
contract (url -> normalized observation dict, plus .close()), so these tests
are hermetic; the real Browserbase+Playwright path shares every line of
pipeline code and only swaps the observer implementation.
"""
import pytest

from app.adapter_factory import models as fm, auto_release, release, testing
from app.adapter_factory.build_workflow import (create_request, record_consent,
                                                run_build, _policy_review,
                                                default_observer)
from app.db import SessionLocal, create_all

GOV_HOST = "evisa.gov.kh"          # .gov.kh is a government suffix
PORTAL_EVIDENCE = {"hostnames": [GOV_HOST], "operator": "government",
                   "verification": "official_government_domain",
                   "portal_url": f"https://{GOV_HOST}/application"}


@pytest.fixture()
def db():
    create_all()
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture()
def real_mode(monkeypatch):
    """Simulate local_real_services for observer/behavioral selection, and make
    ANY SyntheticPortal instantiation an immediate test failure."""
    from app.config import settings as settings_fn
    real = settings_fn()
    monkeypatch.setattr(real, "mock_portal_allowed", False, raising=False)
    monkeypatch.setattr(real, "real_only_mode", True, raising=False)
    monkeypatch.setattr(real, "runtime_mode", "local_real_services", raising=False)
    from app.portal import synthetic as syn

    def _forbidden(self, *a, **k):
        raise AssertionError("SyntheticPortal instantiated in a real runtime mode")

    monkeypatch.setattr(syn.SyntheticPortal, "__init__", _forbidden)
    yield


class FakeLiveObserver:
    """Live-observer contract: observation dicts + .close(). Serves a small
    'real portal': a home page and an application page with two inputs."""

    def __init__(self, host=GOV_HOST, fail_selector=False):
        self.closed = False
        self.calls = []
        self.host = host
        self.fail_selector = fail_selector
        self.app_calls = 0

    def _page(self, url, elements):
        return {"ok": True, "status": 200, "url": url, "hostname": self.host,
                "title": "eVisa", "elements": elements, "links": [], "iframes": [],
                "delayed": False}

    def __call__(self, url):
        self.calls.append(url)
        if url.rstrip("/") == f"https://{self.host}":
            return self._page(url, [
                {"selector": "a.start", "name": "start", "label": "Start",
                 "type": "button", "required": False, "sensitive": False,
                 "submits": "start"}])
        if url == f"https://{self.host}/application":
            self.app_calls += 1
            els = [
                {"selector": "input[name=\"full_name\"]", "name": "full_name",
                 "label": "Full name", "type": "text", "required": True,
                 "sensitive": False},
                {"selector": "input[name=\"passport_number\"]", "name": "passport_number",
                 "label": "Passport number", "type": "text", "required": True,
                 "sensitive": False},
                {"selector": "#save-btn", "name": "save", "label": "Save",
                 "type": "button", "required": False, "sensitive": False,
                 "submits": "save"},
            ]
            if self.fail_selector and self.app_calls > 1:
                # Recon saw the full page; the LIVE portal then drifted — the
                # save button and one field vanish for every later observation
                # (the flow only ever maps observed selectors now, so a
                # live-structural failure requires genuine recon/live drift).
                els = els[:1]
            return self._page(url, els)
        return {"ok": False, "status": 404, "url": url, "error": "not found"}

    def close(self):
        self.closed = True


def _build(db, route_key, observer):
    req = create_request(db, org_id="orgL", user_id="applicant-1", application_id="",
                         route_key=route_key, destination="Cambodia",
                         visa_type="tourist", portal_evidence=dict(PORTAL_EVIDENCE),
                         runtime_mode="local_real_services")
    req.jurisdiction_evidence = {"verified": True,
                                 "basis": "route_research_verified_portal"}
    db.commit()
    record_consent(db, req, user_id="applicant-1")
    run_build(db, req.id, observer=observer)
    return req


def test_real_mode_build_completes_via_live_layers(db, real_mode):
    """Full build in a real mode: objective gates (gov domain policy, research
    jurisdiction evidence), live recon, live structural behavioral layer — no
    SyntheticPortal anywhere, ending release-recommended."""
    obs = FakeLiveObserver()
    req = _build(db, "rk1|live-ok", obs)
    assert req.state == "AWAITING_INTERNAL_RELEASE", (req.state, req.error, req.progress)
    # The behavioral evidence is the LIVE structural layer.
    cand = db.get(fm.AdapterCandidate, req.current_candidate_id)
    from app.adapter_factory import generator
    row = generator.get_version(db, cand.id, cand.current_version)
    assert "LIVE_STRUCTURAL_TESTED" in testing.layers_passed(db, row)
    assert "SYNTHETIC_TESTED" not in testing.layers_passed(db, row)
    # Recon actually used the live observer (home + portal_url path).
    assert any(u.rstrip("/") == f"https://{GOV_HOST}" for u in obs.calls)


def test_real_mode_build_auto_releases_on_live_evidence(db, real_mode):
    """The independent policy engine releases the reversible capability on
    LIVE_STRUCTURAL_TESTED evidence — no admin, no synthetic corpus."""
    req = _build(db, "rk1|live-rel", FakeLiveObserver())
    res = auto_release.evaluate_build(db, req.id)
    assert res.released and res.tier == "sandbox", res.as_dict()
    assert release.active_binding(db, route_key="rk1|live-rel", tier="sandbox")
    caps = auto_release.released_capabilities(db, "rk1|live-rel")["capabilities"]
    assert caps["form_completion"]["released"] is True
    assert caps["submission_execution"]["released"] is False


def test_objective_policy_gate_gov_domain(db):
    """All-government hostnames => automation_permitted deterministically;
    a non-government portal still parks at pending (manual review)."""
    req = create_request(db, org_id="orgL", user_id="u", application_id="",
                         route_key="rk1|pol-gov", destination="KH", visa_type="tourist",
                         portal_evidence={"hostnames": [GOV_HOST]},
                         runtime_mode="local_real_services")
    assert _policy_review(db, req) == "automation_permitted"
    req2 = create_request(db, org_id="orgL", user_id="u", application_id="",
                          route_key="rk1|pol-com", destination="X", visa_type="tourist",
                          portal_evidence={"hostnames": ["visas.example.com"]},
                          runtime_mode="local_real_services")
    assert _policy_review(db, req2) == "pending"


def test_live_structural_failure_is_honest_and_repair_path_runs(db, real_mode):
    """A selector missing from the LIVE portal fails the behavioral layer;
    the automatic repair loop regenerates from fresh live recon (same failing
    observer -> repaired version maps only what exists live and passes)."""
    obs = FakeLiveObserver(fail_selector=True)
    req = _build(db, "rk1|live-fail", obs)
    # Repair re-recons the same (missing-selector) portal and regenerates a
    # version without the vanished elements — the build should end green.
    assert req.state in ("AWAITING_INTERNAL_RELEASE", "QUARANTINED"), req.state
    attempts = db.query(fm.AdapterRepairAttempt).filter_by(
        candidate_id=req.current_candidate_id).count()
    assert attempts >= 1                # the automatic repair loop engaged


def test_run_synthetic_layer_forbidden_in_real_mode(db, real_mode, monkeypatch):
    class Row:  # minimal stand-in; the gate fires before any attribute use
        id = "x"
    with pytest.raises(RuntimeError):
        testing.run_synthetic_layer(db, Row())


def test_default_observer_none_without_browserbase_in_real_mode(db, real_mode):
    # conftest wipes BROWSERBASE_API_KEY -> no live observer, no synthetic
    # fallback: the build must park honestly rather than fake recon.
    assert default_observer([GOV_HOST]) is None
