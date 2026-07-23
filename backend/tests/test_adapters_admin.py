"""Phase 2: country-adapter administration + approval lifecycle."""
import pytest

from tests.conftest import AUTH
from app import adapters_admin as aa

ADMIN = {"Authorization": "Bearer admin-token", "X-Org-Id": "platform",
         "X-User-Id": "alice-admin", "X-Role": "admin"}
ADMIN_BOT = {"Authorization": "Bearer admin-token", "X-Org-Id": "platform",
             "X-User-Id": "ellis-workflow", "X-Role": "admin"}  # AI-looking actor


def _new(client, headers=AUTH):
    return client.post("/admin/adapters", headers=headers, json={
        "country": "Testland", "visa_type": "tourist",
        "config": {"portal_operator": "Testland MOI", "official_domains": ["evisa.testland.gov"]}}).json()


# ---- lifecycle state machine (unit) ----
def test_illegal_transition_rejected(db):
    rec = aa.create_adapter(db, country="Uni", visa_type="tourist", config={}, actor="u")
    with pytest.raises(aa.LifecycleError):
        aa.transition(db, rec.id, "production_active", actor="u", is_admin=True)


def test_activation_requires_admin(db):
    rec = aa.create_adapter(db, country="Uni2", visa_type="tourist", config={}, actor="u")
    for s in ("disabled_draft", "technical_review", "policy_review", "mock_tested", "staging_tested"):
        aa.transition(db, rec.id, s, actor="reviewer", is_admin=False)
    # non-admin cannot reach 'approved'
    with pytest.raises(aa.NotAuthorizedError):
        aa.transition(db, rec.id, "approved", actor="reviewer", is_admin=False)
    # an AI-looking actor cannot activate even as admin
    with pytest.raises(aa.NotAuthorizedError):
        aa.transition(db, rec.id, "approved", actor="ellis-bot", is_admin=True)
    # a human admin can
    rec = aa.transition(db, rec.id, "approved", actor="alice", is_admin=True)
    assert rec.lifecycle_state == "approved" and rec.production_enabled is False
    rec = aa.transition(db, rec.id, "limited_rollout", actor="alice", is_admin=True)
    assert rec.production_enabled is True


def test_kill_switch_disables_and_pauses(db):
    rec = aa.create_adapter(db, country="Uni3", visa_type="tourist", config={}, actor="u")
    for s in ("disabled_draft", "technical_review", "policy_review", "mock_tested", "staging_tested"):
        aa.transition(db, rec.id, s, actor="r", is_admin=False)
    aa.transition(db, rec.id, "approved", actor="alice", is_admin=True)
    aa.transition(db, rec.id, "limited_rollout", actor="alice", is_admin=True)
    rec = aa.kill(db, rec.id, actor="alice", is_admin=True, reason="portal outage")
    assert rec.kill_switch is True and rec.production_enabled is False and rec.lifecycle_state == "paused"


def test_config_frozen_after_approval(db):
    rec = aa.create_adapter(db, country="Uni4", visa_type="tourist", config={"a": 1}, actor="u")
    for s in ("disabled_draft", "technical_review", "policy_review", "mock_tested", "staging_tested"):
        aa.transition(db, rec.id, s, actor="r", is_admin=False)
    aa.transition(db, rec.id, "approved", actor="alice", is_admin=True)
    with pytest.raises(aa.LifecycleError):
        aa.update_config(db, rec.id, {"a": 2}, actor="r")


def test_rollback_restores_config(db):
    rec = aa.create_adapter(db, country="Uni5", visa_type="tourist", config={"v": "one"}, actor="u")
    v1 = rec.version
    aa.update_config(db, rec.id, {"v": "two"}, actor="u")
    rec = aa.rollback(db, rec.id, v1, actor="alice", is_admin=True)
    assert rec.config["v"] == "one" and rec.lifecycle_state == "rolled_back"


# ---- API + authorization + audit (integration) ----
def test_api_full_lifecycle_and_audit(client):
    rec = _new(client)
    aid = rec["id"]
    assert rec["lifecycle_state"] == "discovered"
    # advance through non-activation states as a normal reviewer
    for s in ("disabled_draft", "technical_review", "policy_review", "mock_tested", "staging_tested"):
        r = client.post(f"/admin/adapters/{aid}/transition", headers=AUTH, json={"to_state": s})
        assert r.status_code == 200, r.text
    # applicant (non-admin) is refused activation
    r = client.post(f"/admin/adapters/{aid}/transition", headers=AUTH, json={"to_state": "approved"})
    assert r.status_code == 403
    # AI-looking admin actor is refused activation
    r = client.post(f"/admin/adapters/{aid}/transition", headers=ADMIN_BOT, json={"to_state": "approved"})
    assert r.status_code == 403
    # human admin approves + activates
    assert client.post(f"/admin/adapters/{aid}/transition", headers=ADMIN, json={"to_state": "approved"}).status_code == 200
    assert client.post(f"/admin/adapters/{aid}/transition", headers=ADMIN, json={"to_state": "limited_rollout"}).status_code == 200
    got = client.get(f"/admin/adapters/{aid}", headers=ADMIN).json()
    assert got["production_enabled"] is True
    # immutable audit records the human activation
    actions = [e["action"] for e in got["audit"]]
    assert actions.count("adapter_transition") >= 6
    assert any(e["actor"] == "alice-admin" and e["detail"]["to"] == "approved" for e in got["audit"])


def test_api_kill_requires_admin(client):
    rec = _new(client)
    aid = rec["id"]
    assert client.post(f"/admin/adapters/{aid}/kill", headers=AUTH, json={"reason": "x"}).status_code == 403
    assert client.post(f"/admin/adapters/{aid}/kill", headers=ADMIN, json={"reason": "x"}).status_code == 200


def test_coverage_matrix_reports_service_levels(client):
    _new(client)
    cov = client.get("/admin/coverage", headers=AUTH).json()["coverage"]
    assert any(c["country"] == "Testland" for c in cov)
    for c in cov:
        assert c["service_level"] in {
            "requirements_only", "document_preparation", "form_preparation",
            "applicant_controlled_handoff", "staging_tested_automation",
            "production_approved_automation", "paused_killswitch"}
