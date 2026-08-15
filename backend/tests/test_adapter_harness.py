"""Adapter dry-validation harness + recovery sequence (§20, §27)."""
import pytest
from fastapi.testclient import TestClient

from app.main import app as fastapi_app
from app.portal.adapter_harness import dry_validate
from app.portal.contract import clear_registry
from app.portal.mock_portal import MockPortal

H = {"Authorization": "Bearer dev-token", "X-Org-Id": "org-h", "X-User-Id": "u1"}
ADMIN_H = {"Authorization": "Bearer admin-token", "X-Org-Id": "org-h", "X-User-Id": "adm"}


def _mockland():
    from app.portal.adapters.mockland import build_mockland_adapter
    clear_registry()
    return build_mockland_adapter(MockPortal())


def _vietnam():
    from app.portal.adapters.vietnam_evisa import build_vietnam_evisa_adapter
    clear_registry()
    return build_vietnam_evisa_adapter(MockPortal())


def test_harness_passes_wellformed_adapters():
    for a in (_mockland(), _vietnam()):
        rep = dry_validate(a)
        assert rep.passed, rep.checks
        assert rep.checks["live_binding_fail_closed"]["ok"]  # real driver refused
        assert "cannot approve" in rep.activation_note


def test_harness_catches_offlist_url():
    a = _vietnam()
    a.application_url = "https://evil.example/apply"
    rep = dry_validate(a)
    assert not rep.passed
    assert any("not on the approved allowlist" in d for d in rep.checks["domains"]["detail"])


def test_harness_catches_nondeterministic_selector():
    a = _vietnam()
    a.application_mappings = list(a.application_mappings) + [
        {"field": "sex", "selector": "ask the model to find the sex field"}]
    rep = dry_validate(a)
    assert not rep.passed
    assert any("not a\ndeterministic" in d.replace("not a deterministic", "not a\ndeterministic")
               for d in rep.checks["selectors"]["detail"])


def test_harness_catches_missing_extraction_and_recovery():
    a = _mockland()
    a.confirmation_extraction = ""
    a.resume_behavior = "just retry"
    a.rate_limits = {}
    rep = dry_validate(a)
    assert not rep.passed
    assert not rep.checks["extraction"]["ok"]
    assert not rep.checks["recovery"]["ok"]


def test_harness_flags_automated_personal_steps():
    a = _mockland()
    a.allowed_actions = list(a.allowed_actions) + ["enter_otp"]
    rep = dry_validate(a)
    assert any("enter_otp" in d for d in rep.checks["safety"]["detail"])


def test_harness_endpoint_admin_only():
    client = TestClient(fastapi_app)
    assert client.get("/admin/adapters/harness", headers=H).status_code == 403
    r = client.get("/admin/adapters/harness", headers=ADMIN_H)
    assert r.status_code == 200
    body = r.json()
    # mock-allowed test mode registers the demo adapters (mockland + vietnam
    # e-visa) plus the two `tested` scheduling adapters (US + Schengen); all
    # reports carry the cannot-approve note.
    ids = {rep["adapter_id"] for rep in body["reports"]}
    assert {"us-visa-scheduling-v1", "schengen-vfs-scheduling-v1"} <= ids
    assert len(body["reports"]) == 4
    assert all("cannot approve" in rep["activation_note"] for rep in body["reports"])


def test_recovery_sequence_uncertain_then_reconciled(monkeypatch):
    """§27: interrupted payment -> OUTCOME_UNCERTAIN -> reconcile discovers the
    official success -> retry short-circuits (no duplicate charge)."""
    import os
    from app import config
    os.environ["ELLIS_RUNTIME_MODE"] = "production"
    config.settings.cache_clear()
    try:
        from app.portal.live_driver import BrowserbaseLiveViewDriver

        class _A:
            adapter_id = "x"
            approved_domains = ["evisa.gov.vn"]
            production_approval_status = "production_approved"
            production_enabled = True
            appointment_search = "none"
            confirmation_extraction = None
            receipt_extraction = None

        states = iter([
            {"paid": None},                      # pre-check during interrupted attempt
            {"paid": None},                      # post-live-view read: still unknown
            {"paid": True, "receipt": "R-1"},    # reconcile after reconnect: it DID succeed
            {"paid": True, "receipt": "R-1"},    # retry pre-check -> short-circuit
        ])
        d = BrowserbaseLiveViewDriver(_A(), session={"id": "s"}, page=object(),
                                      state_probe=lambda t, a: next(states),
                                      require_real_key=False)
        first = d.pay(session_token="t", application_id="a")
        assert first["ok"] is False and first["status"] == "OUTCOME_UNCERTAIN"
        # reconnect + reconcile:
        state = d.get_application_state(session_token="t", application_id="a")
        assert state["paid"] is True
        retry = d.pay(session_token="t", application_id="a")
        assert retry["ok"] is True and retry["reconciled"] is True
        assert retry["receipt"] == "R-1"          # official receipt, single charge
    finally:
        os.environ.pop("ELLIS_RUNTIME_MODE", None)
        config.settings.cache_clear()
