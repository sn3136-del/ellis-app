"""Route resolution ladder + intake API (brief sections 15-17, 20)."""
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select

from app.db import SessionLocal, create_all
from app.main import app as fastapi_app
from app.visa_snapshot import SNAPSHOT_DATE, matrix, pipeline
from app.visa_snapshot import resolution as res_mod
from app.visa_snapshot.models import (AdapterDevelopmentTask, HumanReviewTask,
                                      OfficialPortalRecord, RouteMatrixEntry,
                                      SnapshotResearchBatch, SourceEvidence,
                                      VisaFeeVersion)

H = {"Authorization": "Bearer dev-token", "X-Org-Id": "org-res", "X-User-Id": "u1"}
ADMIN_H = {"Authorization": "Bearer admin-token", "X-Org-Id": "org-res", "X-User-Id": "admin1"}


@pytest.fixture(scope="module")
def db():
    create_all()
    s = SessionLocal()
    yield s
    s.close()


def _seed_matrix(db, dest: str, nats=("CHN", "USA")):
    have = {k for (k,) in db.execute(select(RouteMatrixEntry.matrix_key).where(
        RouteMatrixEntry.destination_country == dest)).all()}
    rows = [e for e in matrix.iter_entries()
            if e["destination_country"] == dest and e["passport_nationality"] in nats
            and e["matrix_key"] not in have]
    if rows:
        db.execute(insert(RouteMatrixEntry),
                   [dict(e, id=uuid.uuid4().hex, snapshot_date=SNAPSHOT_DATE, route_id=None)
                    for e in rows])
        db.commit()


ANSWERS = {
    "address_line1": "12 Harbor Lane", "address_city": "Springfield",
    "address_region": "IL", "address_postal_code": "62704",
    "address_country": "USA",
    "passport_nationality": "CHN", "passport_issuing_country": "CHN",
    "travel_document_type": "ordinary_passport", "lawful_country_of_residence": "USA",
    "destination_country": "JP", "visa_category": "tourist_visa",
    "travel_purpose": "tourism", "arrival_date": "2026-09-01",
    "departure_date": "2026-09-10", "age": 30, "preferred_language": "en",
    # Required in truth (it becomes a statement on the government form and can
    # never be defaulted) — a completed intake always carries an answer.
    "prior_refusals": "no",
    "email": "t@example.com",
}


def test_unresearched_route_is_not_ready_and_creates_review_task(db):
    _seed_matrix(db, "JPN")
    out = res_mod.resolve(db, org_id="org-res", answers=ANSWERS)
    assert out["readiness_status"] == "NOT_READY"
    assert out["route_key"].startswith("rk1|nat=CHN")
    task = db.execute(select(HumanReviewTask).where(
        HumanReviewTask.kind == "intake_not_ready")).scalars().first()
    assert task is not None
    # No invented requirements: the checks say exactly what is unknown.
    assert out["checks"]["snapshot_resolution"]["disposition"] == "RESEARCH_INCOMPLETE"
    assert out["snapshot"]["date"] == SNAPSHOT_DATE
    assert "may have changed" in out["snapshot"]["disclaimer"]


def _verify_route_in_db(db, dest3="JPN"):
    """Simulate the pipeline having verified CHN->dest tourist_visa with
    evidence, fee, and portal (what LINK/VERIFY stages produce)."""
    row = db.execute(select(RouteMatrixEntry).where(
        RouteMatrixEntry.passport_nationality == "CHN",
        RouteMatrixEntry.destination_country == dest3,
        RouteMatrixEntry.visa_category == "tourist_visa")).scalars().one()
    row.disposition = "EMBASSY_VISA_REQUIRED"
    db.add(SourceEvidence(snapshot_date=SNAPSHOT_DATE, search_query="q",
                          original_url="https://www.mofa.go.jp/x", final_url="https://www.mofa.go.jp/x",
                          final_hostname="www.mofa.go.jp",
                          source_authority="destination_foreign_ministry",
                          applicable_jurisdiction=dest3, relevant_excerpt="official",
                          retrieved_at="2026-07-23T12:00:00Z", content_hash="a" * 64,
                          verification_status="verified"))
    db.commit()
    return row


def test_verified_requirements_without_fee_is_preparation_only(db):
    _verify_route_in_db(db)
    out = res_mod.resolve(db, org_id="org-res", answers=ANSWERS)
    # requirements verified but fee unverified + jurisdiction unresolved -> PREPARATION_ONLY
    assert out["readiness_status"] == "PREPARATION_ONLY"
    assert out["checks"]["readiness_reasoning"]["requirements_verified"] is True
    assert out["checks"]["readiness_reasoning"]["fee_verified"] is False
    # First-real-route rule: an AdapterDevelopmentTask is queued for THIS route.
    t = db.execute(select(AdapterDevelopmentTask).where(
        AdapterDevelopmentTask.route_key == out["route_key"])).scalars().first()
    assert t is not None and t.status == "queued"


def test_fee_portal_jurisdiction_unlock_handoff(db):
    from app.visa_snapshot.models import ConsularJurisdictionRule
    db.add(VisaFeeVersion(snapshot_date=SNAPSHOT_DATE, fee_uid="JPN:tourist_visa:t1",
                          version=1, destination_country="JPN", visa_category="tourist_visa",
                          record={"government_fee_amount": 3000}, content_hash="b" * 64,
                          fee_status="verified"))
    db.add(OfficialPortalRecord(snapshot_date=SNAPSHOT_DATE, portal_uid="JPN:evisa:t1",
                                destination_country="JPN", portal_kind="evisa_portal",
                                url="https://www.evisa.mofa.go.jp/", hostnames=["www.evisa.mofa.go.jp"],
                                verification_status="verified_official_domain"))
    db.add(ConsularJurisdictionRule(snapshot_date=SNAPSHOT_DATE, destination_country="JPN",
                                    residence_jurisdiction="USA",
                                    competent_post_name="Consulate-General of Japan in New York",
                                    competent_post_kind="consulate_general",
                                    competent_post_url="https://www.ny.us.emb-japan.go.jp/",
                                    verification_status="verified"))
    db.commit()
    out = res_mod.resolve(db, org_id="org-res", answers=ANSWERS)
    assert out["readiness_status"] == "APPLICANT_HANDOFF_READY"
    assert out["checks"]["consular_jurisdiction"]["resolved"] is True
    # No production adapter exists -> never LIVE_*.
    assert out["checks"]["adapter"]["production_active"] == 0


def test_conflicted_route_is_not_ready(db):
    from app.visa_snapshot.models import SnapshotConflict
    db.add(SnapshotConflict(snapshot_date=SNAPSHOT_DATE, destination_country="JPN",
                            field="CHN:tourist_visa", values=[{"value": "A"}, {"value": "B"}]))
    db.commit()
    out = res_mod.resolve(db, org_id="org-res", answers=ANSWERS)
    assert out["readiness_status"] == "NOT_READY"
    assert out["checks"]["conflicts"]["route_conflicted"] is True
    # cleanup: resolve the conflict so other tests are unaffected
    c = db.execute(select(SnapshotConflict).where(
        SnapshotConflict.field == "CHN:tourist_visa")).scalars().first()
    c.status = "resolved"
    db.commit()


def test_intake_api_save_resume_resolve():
    client = TestClient(fastapi_app)
    r = client.post("/intake", json={"answers": {"passport_nationality": "CHN"}}, headers=H)
    assert r.status_code == 200
    iid = r.json()["id"]
    # resume with partial updates (save progress)
    r2 = client.put(f"/intake/{iid}", json={"answers": {"destination_country": "JP"}}, headers=H)
    assert r2.json()["answers"]["passport_nationality"] == "CHN"
    # resolving with missing fields is a 422 listing them, not a guess
    r3 = client.post(f"/intake/{iid}/resolve", headers=H)
    assert r3.status_code == 422
    assert "arrival_date" in r3.json()["detail"]["missing_fields"]
    # complete and resolve
    client.put(f"/intake/{iid}", json={"answers": ANSWERS, "email": "t@example.com"}, headers=H)
    r4 = client.post(f"/intake/{iid}/resolve", headers=H)
    assert r4.status_code == 200
    body = r4.json()
    assert body["readiness_status"] in ("NOT_READY", "PREPARATION_ONLY",
                                        "APPLICANT_HANDOFF_READY")
    assert body["snapshot"]["date"] == SNAPSHOT_DATE
    # tenant isolation
    other = dict(H, **{"X-Org-Id": "org-other"})
    assert client.get(f"/intake/{iid}", headers=other).status_code == 404


def test_admin_endpoints_require_admin():
    client = TestClient(fastapi_app)
    assert client.get("/admin/snapshot/coverage", headers=H).status_code == 403
    r = client.get("/admin/snapshot/review-queue", headers=ADMIN_H)
    assert r.status_code == 200
    r2 = client.get("/admin/snapshot/adapter-tasks", headers=ADMIN_H)
    assert r2.status_code == 200


def test_ai_cannot_mark_human_reviewed():
    """Review resolution requires the admin token; the applicant/dev principal
    (what an AI agent would hold) is refused."""
    client = TestClient(fastapi_app)
    db = SessionLocal()
    try:
        t = HumanReviewTask(kind="route_review", subject_id="x", title="t")
        db.add(t)
        db.commit()
        tid = t.id
    finally:
        db.close()
    r = client.post(f"/admin/snapshot/review-queue/{tid}/resolve",
                    json={"resolution_note": "n"}, headers=H)
    assert r.status_code == 403
    r2 = client.post(f"/admin/snapshot/review-queue/{tid}/resolve",
                     json={"resolution_note": "checked against MOFA"}, headers=ADMIN_H)
    assert r2.status_code == 200


def test_snapshot_info_displays_honest_dates():
    client = TestClient(fastapi_app)
    info = client.get("/snapshot/info", headers=H).json()
    assert info["display_line"] == "Visa requirements snapshot: July 23, 2026"
    assert "may have changed" in info["disclaimer"]
    assert info["update_mode"] == "manual"
    assert info["automatic_refresh_enabled"] is False
