"""A public operator console cannot authenticate using a bundled credential."""
import asyncio
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from app import security


def principal(token):
    return asyncio.run(security.get_principal(
        authorization=f"Bearer {token}", x_ellis_token="", x_org_id="ops",
        x_user_id="forged-reviewer", x_role="admin"))


def configure(monkeypatch, token, identity="ellis-owner"):
    monkeypatch.setattr(security, "settings", lambda: SimpleNamespace(
        clerk_secret_key="", require_secure_admin=True, admin_token=token,
        admin_user_id=identity, dev_api_token="dev-token"))


def test_public_installation_rejects_bundled_admin_credential(monkeypatch):
    configure(monkeypatch, "admin-token")
    with pytest.raises(HTTPException) as err:
        principal("admin-token")
    assert err.value.status_code == 401
    assert principal("dev-token").role == "applicant"


def test_operator_identity_is_bound_to_server_credential(monkeypatch):
    token = "private-test-operator-key-123456789012345"
    configure(monkeypatch, token)
    operator = principal(token)
    assert operator.role == "admin"
    assert operator.user_id == "ellis-owner"
    with pytest.raises(HTTPException):
        principal("admin-token")


@pytest.mark.parametrize("token,identity", [
    ("dev-token", "owner"), ("short", "owner"), ("x" * 40, "")])
def test_insecure_operator_configuration_fails_closed(monkeypatch, token, identity):
    configure(monkeypatch, token, identity)
    with pytest.raises(HTTPException):
        principal(token)


def test_uae_embassy_is_official_without_trusting_similar_hosts():
    from app.visa_snapshot.authority import is_government_host
    assert is_government_host("www.uae-embassy.org")
    assert not is_government_host("uae-embassy.org.example.com")


@pytest.mark.parametrize("header", ["Authorization", "X-Ellis-Token"])
@pytest.mark.parametrize("path", ["/database/freshness", "/database/changes.csv"])
def test_public_and_rotated_credentials_cannot_read_operator_data(
        client, monkeypatch, header, path):
    configure(monkeypatch, "private-test-operator-key-123456789012345")
    for token, status in [("dev-token", 403), ("admin-token", 401)]:
        headers = {header: f"Bearer {token}" if header == "Authorization" else token,
                   "X-Org-Id": "ops", "X-User-Id": "forged-reviewer", "X-Role": "admin"}
        response = client.get(path, headers=headers)
        assert response.status_code == status, response.text


def test_public_token_cannot_modify_adapter_or_disable_active_adapter(client, monkeypatch, db):
    from app import adapters_admin as aa, models
    configure(monkeypatch, "private-test-operator-key-123456789012345")
    public = {"X-Ellis-Token": "dev-token", "X-Org-Id": "platform",
              "X-User-Id": "forged-owner", "X-Role": "admin"}
    body = {"country": "Auth Test", "visa_type": "tourist", "config": {}}
    assert client.post("/admin/adapters", headers=public, json=body).status_code == 403
    draft = aa.create_adapter(db, country="Auth Draft", visa_type="tourist",
                              config={"official_domains": ["example.gov"]}, actor="owner")
    active = aa.create_adapter(db, country="Auth Active", visa_type="tourist",
                               config={}, actor="owner")
    active.lifecycle_state = "production_active"
    active.production_enabled = True
    db.commit()
    cases = [("put", f"/admin/adapters/{draft.id}", {"official_domains": ["attacker.example"]}),
             ("post", f"/admin/adapters/{draft.id}/transition", {"to_state": "disabled_draft"}),
             ("post", f"/admin/adapters/{active.id}/transition", {"to_state": "paused"}),
             ("post", f"/admin/adapters/{active.id}/transition", {"to_state": "rolled_back"})]
    for method, path, payload in cases:
        response = getattr(client, method)(path, headers=public, json=payload)
        assert response.status_code == 403, response.text
    db.expire_all()
    assert db.get(models.AdapterRecord, draft.id).config == {"official_domains": ["example.gov"]}
    assert db.get(models.AdapterRecord, active.id).production_enabled is True
    assert db.get(models.AdapterRecord, active.id).lifecycle_state == "production_active"


def test_authenticated_operator_adapter_write_binds_audit_actor(client, monkeypatch):
    token = "private-test-operator-key-123456789012345"
    configure(monkeypatch, token)
    headers = {"X-Ellis-Token": token, "X-Org-Id": "platform",
               "X-User-Id": "forged-reviewer", "X-Role": "applicant"}
    response = client.post("/admin/adapters", headers=headers, json={
        "country": "Auth Operator", "visa_type": "tourist", "config": {}})
    assert response.status_code == 200, response.text
    adapter = client.get(f"/admin/adapters/{response.json()['id']}", headers=headers).json()
    assert adapter["audit"]
    assert all(event["actor"] == "ellis-owner" for event in adapter["audit"])
