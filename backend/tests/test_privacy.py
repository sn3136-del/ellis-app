"""Phase 10 privacy (export + erasure) and Phase 11 ops (readiness, metrics,
kill switches) tests."""
import os

from tests.conftest import AUTH, AUTH2
from app import models


def _make_case(client, headers=AUTH):
    r = client.post("/cases", headers=headers, json={
        "full_name": "Priv Test", "email": "priv@example.com",
        "destination_country": "Mockland", "visa_type": "tourist"}).json()
    cid = r["id"]
    client.post(f"/cases/{cid}/documents", headers=headers,
                json={"name": "p.pdf", "mime": "application/pdf", "text": "PASSPORT"})
    return cid


def test_case_export_is_complete_and_secret_free(client):
    cid = _make_case(client)
    r = client.get(f"/cases/{cid}/export", headers=AUTH)
    assert r.status_code == 200
    bundle = r.json()
    assert bundle["case"]["id"] == cid
    assert bundle["applicant"]["email"] == "priv@example.com"
    assert isinstance(bundle["documents"], list) and len(bundle["documents"]) == 1
    assert "audit" in bundle
    # No provider secret patterns anywhere in the export.
    import json as _json
    blob = _json.dumps(bundle)
    assert "sk-" not in blob and "BEGIN PRIVATE KEY" not in blob


def test_org_export_lists_all_cases(client):
    a = _make_case(client)
    b = _make_case(client)
    r = client.get("/export", headers=AUTH).json()
    ids = {c["case"]["id"] for c in r["cases"]}
    assert a in ids and b in ids


def test_case_deletion_cascades_and_leaves_tombstone(client, db):
    cid = _make_case(client)
    r = client.delete(f"/cases/{cid}", headers=AUTH)
    assert r.status_code == 200 and r.json()["deleted"] is True
    # The case and its documents are gone...
    assert db.get(models.VisaApplication, cid) is None
    from sqlalchemy import select
    docs = db.execute(select(models.StoredDocument).where(
        models.StoredDocument.application_id == cid)).scalars().all()
    assert docs == []
    # ...but a non-PII erasure tombstone remains in the audit trail.
    events = db.execute(select(models.AuditEvent).where(
        models.AuditEvent.application_id == cid)).scalars().all()
    assert any(e.action == "case_erased" for e in events)


def test_deletion_is_tenant_isolated(client):
    cid = _make_case(client, headers=AUTH)
    # A different org cannot export or delete this case.
    assert client.get(f"/cases/{cid}/export", headers=AUTH2).status_code == 403
    assert client.delete(f"/cases/{cid}", headers=AUTH2).status_code == 403


def test_applicant_erasure_removes_all_their_cases(client, db):
    cid = _make_case(client)
    app_row = db.get(models.VisaApplication, cid)
    applicant_id = app_row.applicant_id
    r = client.delete(f"/applicants/{applicant_id}", headers=AUTH)
    assert r.status_code == 200
    db.expire_all()  # drop identity-map cache so we re-read the committed state
    assert db.get(models.Applicant, applicant_id) is None
    assert db.get(models.VisaApplication, cid) is None
    # And the case is gone through the API too.
    assert client.get(f"/cases/{cid}", headers=AUTH).status_code == 404


def test_readiness_endpoint(client):
    r = client.get("/readyz")
    assert r.status_code == 200 and r.json()["ready"] is True


def test_metrics_are_org_scoped_and_secret_free(client):
    _make_case(client)
    r = client.get("/metrics", headers=AUTH).json()
    assert r["cases_total"] >= 1
    assert "cases_by_state" in r and "kill_switches" in r
    import json as _json
    assert "sk-" not in _json.dumps(r)


def test_kill_switches_reflect_env(monkeypatch):
    from app import config
    monkeypatch.setenv("ELLIS_KILL_BROWSERBASE", "1")
    monkeypatch.setenv("ELLIS_KILL_ADAPTERS", "vietnam_evisa, mockland")
    ks = config.killswitches()
    assert ks["browserbase"] is True
    assert "vietnam_evisa" in ks["adapters"] and "mockland" in ks["adapters"]
    assert ks["kimi"] is False
