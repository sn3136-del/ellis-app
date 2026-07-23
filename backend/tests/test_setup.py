"""Phase 7 — Trip.com first-run administrator setup wizard.

Proves: credentials are backend-only (never echoed), a sender address alone is
rejected, connection tests + rotation + revocation work, a saved credential is
never displayed again (only a fingerprint), and production refuses default
credentials.
"""
import json
import os

import pytest

from tests.conftest import AUTH
from app import setup as setup_mod, models
from sqlalchemy import select

ADMIN = {"Authorization": "Bearer admin-token", "X-Org-Id": "org1", "X-User-Id": "admin1"}
ADMIN2 = {"Authorization": "Bearer admin-token", "X-Org-Id": "orgS", "X-User-Id": "adminS"}


def test_status_before_setup(client):
    r = client.get("/setup/status", headers=ADMIN).json()
    assert r["setup_complete"] is False
    assert "tenant_name" in r["missing"]


def test_non_admin_cannot_save(client):
    # AUTH is the applicant dev token → 403 on the admin-only setup mutation.
    r = client.post("/setup", headers=AUTH, json={"tenant_name": "Trip.com"})
    assert r.status_code == 403


def test_sender_address_alone_is_rejected(client):
    r = client.post("/setup", headers=ADMIN2, json={
        "email": {"provider": "", "sender": "noreply@trip.example"}})
    assert r.status_code == 400
    assert "sender address alone" in r.json()["detail"]


def test_smtp_requires_credentials(client):
    r = client.post("/setup", headers=ADMIN2, json={
        "email": {"provider": "smtp", "sender": "noreply@trip.example", "host": "smtp.trip.example"}})
    assert r.status_code == 400
    assert "credential" in r.json()["detail"].lower()


def test_secrets_are_vaulted_and_never_echoed(client, db):
    secret = "sk-kimi-SUPERSECRET-123456"
    r = client.post("/setup", headers=ADMIN, json={
        "tenant_name": "Trip.com IT", "admin_email": "it@trip.example",
        "kimi_api_key": secret,
        "email": {"provider": "smtp", "sender": "noreply@trip.example",
                  "host": "smtp.trip.example", "username": "apikey"},
        "email_credential": "smtp-password-abcdef"})
    assert r.status_code == 200, r.text
    body = r.json()
    # The response carries fingerprints, never the secret values.
    blob = json.dumps(body)
    assert secret not in blob and "smtp-password-abcdef" not in blob
    assert body["configured"]["kimi_api_key"]["configured"] is True
    assert len(body["configured"]["kimi_api_key"]["fingerprint"]) == 12
    # The DB row stores only a vault ref + fingerprint — never the plaintext.
    row = db.get(models.TenantSetup, "org1")
    assert secret not in json.dumps(row.config)
    assert secret not in json.dumps(row.credential_refs)
    assert row.credential_refs["kimi_api_key"].startswith("vault://")
    # And GET status never leaks the secret either.
    status = client.get("/setup/status", headers=ADMIN).json()
    assert secret not in json.dumps(status)


def test_full_valid_setup_is_complete(client):
    r = client.post("/setup", headers=ADMIN, json={
        "tenant_name": "Trip.com IT", "admin_email": "it@trip.example",
        "data_region": "eu", "retention_days": 365,
        "kimi_api_key": "sk-kimi-live-000000",
        "email": {"provider": "api", "sender": "noreply@trip.example",
                  "api_endpoint": "https://email.example/api/send"},
        "email_credential": "api-key-zzzzzzzz"}).json()
    assert r["setup_complete"] is True
    assert r["missing"] == []


def test_connection_test_and_rotation_and_revocation(client, db):
    client.post("/setup", headers=ADMIN2, json={
        "tenant_name": "T", "admin_email": "a@b.c", "browserbase_api_key": "bb-key-12345678"})
    # Configured component tests ok; an unconfigured one is honest.
    ok = client.post("/setup/test/browserbase_api_key", headers=ADMIN2).json()
    assert ok["ok"] is True and ok["status"] == "configured"
    none = client.post("/setup/test/trip_client_secret", headers=ADMIN2).json()
    assert none["ok"] is False and none["status"] == "unconfigured"
    # Rotation changes the fingerprint.
    fp1 = client.get("/setup/status", headers=ADMIN2).json()["configured"]["browserbase_api_key"]["fingerprint"]
    rot = client.post("/setup/rotate/browserbase_api_key", headers=ADMIN2,
                      json={"value": "bb-key-ROTATED-999"}).json()
    assert rot["rotated"] is True and rot["fingerprint"] != fp1
    # Revocation clears it.
    client.post("/setup/revoke/browserbase_api_key", headers=ADMIN2)
    after = client.get("/setup/status", headers=ADMIN2).json()
    assert after["configured"]["browserbase_api_key"]["configured"] is False


def test_send_test_email_without_provider_is_honest(client):
    # A fresh org with no email provider configured → we never claim delivery.
    fresh = {"Authorization": "Bearer admin-token", "X-Org-Id": "orgEmail", "X-User-Id": "adminE"}
    client.post("/setup", headers=fresh, json={"tenant_name": "T", "admin_email": "a@b.c"})
    r = client.post("/setup/email/test", headers=fresh, json={"to": "dest@example.com"}).json()
    assert r["ok"] is False and r["delivered"] is False
    assert r["status"] == "no_transactional_provider"


def test_local_email_never_claims_real_delivery(db):
    setup_mod.save_setup(db, org_id="orgL", actor="admin", payload={
        "tenant_name": "T", "admin_email": "a@b.c",
        "email": {"provider": "local", "sender": "noreply@trip.example"}})
    res = setup_mod.send_test_email(db, org_id="orgL", to="x@y.z")
    assert res["delivered"] is False   # local recorder never claims real delivery


def test_production_preflight_blocks_default_credentials():
    from app.config import settings
    original = os.environ.get("ELLIS_ENV")
    try:
        os.environ["ELLIS_ENV"] = "production"
        settings.cache_clear()
        pre = setup_mod.production_preflight()
        assert pre["production_mode"] is True
        assert pre["ok"] is False
        assert any("ELLIS_DEV_TOKEN" in b or "ELLIS_ADMIN_TOKEN" in b for b in pre["blockers"])
    finally:
        if original is None:
            os.environ.pop("ELLIS_ENV", None)
        else:
            os.environ["ELLIS_ENV"] = original
        settings.cache_clear()
