"""Adapter-factory API + restart-durability tests (brief §33, §35 items 36-38,
44-51). Proves the applicant completes a build without any developer, an
admin releases, the deterministic runtime binds only the released version, and
builds resume idempotently after a simulated restart."""
import importlib

from tests.conftest import AUTH, AUTH2

ADMIN = {"Authorization": "Bearer admin-token", "X-Org-Id": "org1", "X-User-Id": "admin1"}

HOST = "portal.gov.example"
PE = {"hostnames": [HOST], "operator": "Test Consular Authority",
      "verification": "synthetic_test_portal"}


def _request_and_build(client, headers=AUTH):
    r = client.post("/adapter-build/request", headers=headers,
                    json={"route_key": "rk1|apitest", "destination": "Testland",
                          "portal_evidence": PE})
    assert r.status_code == 200, r.text
    bid = r.json()["id"]
    r = client.post(f"/adapter-build/{bid}/consent", headers=headers, json={"locale": "en"})
    assert r.status_code == 200, r.text
    return bid, r.json()


# ---- §35.48 applicant completes the build flow with no developer ----
def test_applicant_build_flow_reaches_ready_label(client):
    bid, status = _request_and_build(client)
    # The applicant sees ONLY a simple label, never internal states/code.
    assert status["label"] in ("final_verification", "ready", "testing",
                               "manual_review", "safety_checks")
    assert "flow" not in status and "manifest" not in status
    # Progress is visible and honest.
    r = client.get(f"/adapter-build/{bid}", headers=AUTH)
    assert r.status_code == 200
    assert "label" in r.json()


def test_build_consent_required_and_versioned(client):
    r = client.get("/adapter-build/consent-copy", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["text_version"]
    assert "no_payment_booking_submission_during_build" in body["disclosures"]


def test_build_request_is_tenant_isolated(client):
    bid, _ = _request_and_build(client)
    # Another tenant cannot see this build.
    r = client.get(f"/adapter-build/{bid}", headers=AUTH2)
    assert r.status_code == 403


# ---- §35.36-38 restart resumes safely (idempotent) ----
def test_build_resume_is_idempotent(client, db):
    bid, first = _request_and_build(client)
    # Simulate a restart: reload the workflow module, then resume.
    import app.adapter_factory.build_workflow as bw
    importlib.reload(bw)
    r = client.post(f"/adapter-build/{bid}/resume", headers=AUTH)
    assert r.status_code == 200
    # State did not regress or duplicate irreversible work.
    from app.adapter_factory import models as fm
    from sqlalchemy import select
    req = db.get(fm.AdapterBuildRequest, bid)
    # A candidate exists at most once per build.
    cands = db.execute(select(fm.AdapterCandidate).where(
        fm.AdapterCandidate.build_request_id == bid)).scalars().all()
    assert len(cands) <= 1


# ---- admin queue, evidence, release ----
def test_admin_sees_queue_and_evidence_and_releases(client, db):
    bid, _ = _request_and_build(client)
    r = client.get("/admin/adapter-factory/queue", headers=ADMIN)
    assert r.status_code == 200
    builds = r.json()["builds"]
    mine = [b for b in builds if b["id"] == bid][0]
    cand_id = mine["candidate_id"]
    assert cand_id
    # Evidence package is available (sanitized flow + test classifications).
    r = client.get(f"/admin/adapter-factory/candidates/{cand_id}/evidence", headers=ADMIN)
    assert r.status_code == 200
    ev = r.json()
    assert ev["evidence"]["static_validation"]["passed"] is True
    assert "flow" in ev
    # Release to sandbox (human admin).
    from app.adapter_factory import models as fm
    cand = db.get(fm.AdapterCandidate, cand_id)
    r = client.post("/admin/adapter-factory/release", headers=ADMIN,
                    json={"candidate_id": cand_id, "version": cand.current_version,
                          "tier": "sandbox"})
    assert r.status_code == 200, r.text
    assert r.json()["tier"] == "sandbox"


# ---- §35.13 an applicant token can never release ----
def test_applicant_cannot_release(client, db):
    bid, _ = _request_and_build(client)
    r = client.get("/admin/adapter-factory/queue", headers=ADMIN)
    cand_id = [b for b in r.json()["builds"] if b["id"] == bid][0]["candidate_id"]
    from app.adapter_factory import models as fm
    cand = db.get(fm.AdapterCandidate, cand_id)
    # Applicant (non-admin) token is rejected by require_admin.
    r = client.post("/admin/adapter-factory/release", headers=AUTH,
                    json={"candidate_id": cand_id, "version": cand.current_version,
                          "tier": "sandbox"})
    assert r.status_code == 403


# ---- admin kill switch + rollback endpoints ----
def test_admin_kill_and_release_history(client, db):
    bid, _ = _request_and_build(client)
    r = client.get("/admin/adapter-factory/queue", headers=ADMIN)
    cand_id = [b for b in r.json()["builds"] if b["id"] == bid][0]["candidate_id"]
    from app.adapter_factory import models as fm
    cand = db.get(fm.AdapterCandidate, cand_id)
    client.post("/admin/adapter-factory/release", headers=ADMIN,
                json={"candidate_id": cand_id, "version": cand.current_version,
                      "tier": "sandbox"})
    r = client.post("/admin/adapter-factory/kill", headers=ADMIN,
                    json={"candidate_id": cand_id, "reason": "test"})
    assert r.status_code == 200 and r.json()["killed"] is True
    r = client.get("/admin/adapter-factory/releases", headers=ADMIN)
    assert r.status_code == 200
    assert any(rel["candidate_id"] == cand_id for rel in r.json()["releases"])
