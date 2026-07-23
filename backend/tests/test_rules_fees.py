"""Phase 2 (route rules + coverage matrix) and Phase 3 (fee engine)."""
from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import AUTH
from app import rules, fees, models

ADMIN = {"Authorization": "Bearer admin-token", "X-Org-Id": "org1", "X-User-Id": "rules-admin"}
ROUTE = dict(destination="Ruritania", visa_type="tourist", nationality="Chinese", residence="Singapore")


# ---- rules ----
def test_rule_requires_official_source(db):
    with pytest.raises(ValueError):
        rules.create_rule(db, **ROUTE)      # no source_url


def test_rule_versions_and_review_flow(db):
    r1 = rules.create_rule(db, **ROUTE, source_url="https://evisa.ruritania.gov.example/rules",
                           source_authority="Ruritania Immigration",
                           required_documents=["passport", "photo"],
                           processing_method="evisa", electronic_available=True,
                           declaration_mandatory=True,
                           passport_validity_rule={"kind": "months_after_arrival", "months": 6})
    assert r1.version == 1 and r1.review_status == "pending"
    # A pending rule is NEVER served as verified.
    assert rules.latest_rule(db, **ROUTE) is None
    rules.review_rule(db, rule_id=r1.id, decision="verified", actor="admin")
    v = rules.latest_rule(db, **ROUTE)
    assert v is not None and v.version == 1
    # A newer pending version does not displace the verified one until reviewed.
    r2 = rules.create_rule(db, **ROUTE, source_url="https://evisa.ruritania.gov.example/rules2")
    assert r2.version == 2
    assert rules.latest_rule(db, **ROUTE).version == 1
    rules.review_rule(db, rule_id=r2.id, decision="verified", actor="admin")
    assert rules.latest_rule(db, **ROUTE).version == 2
    assert r1.superseded_by == r2.id


def test_rule_review_decisions_validated(db):
    r = rules.create_rule(db, destination="X1", source_url="https://x1.gov.example")
    with pytest.raises(ValueError):
        rules.review_rule(db, rule_id=r.id, decision="approved!", actor="a")


def test_rule_admin_endpoints(client):
    r = client.post("/admin/routes/rules", headers=AUTH, json={
        "destination": "Y1", "source_url": "https://y1.gov.example"})
    assert r.status_code == 403     # applicant token cannot author rules
    r = client.post("/admin/routes/rules", headers=ADMIN, json={
        "destination": "Y1", "source_url": "https://y1.gov.example",
        "required_documents": ["passport"]})
    assert r.status_code == 200 and r.json()["review_status"] == "pending"
    rid = r.json()["id"]
    rv = client.post(f"/admin/routes/rules/{rid}/review", headers=ADMIN,
                     json={"decision": "verified"})
    assert rv.status_code == 200 and rv.json()["review_status"] == "verified"
    got = client.get("/routes/rules", headers=AUTH, params={"destination": "Y1"}).json()
    assert got["verified"]["required_documents"] == ["passport"]


# ---- coverage matrix honesty ----
def test_coverage_status_ladder(db):
    dest = "Freedonia"
    # Nothing at all → not in the matrix; direct status = not_researched.
    st = rules.route_status(db, destination=dest)
    assert st["status"] == "not_researched"
    # A pending rule only proves sources were found — not verification.
    r = rules.create_rule(db, destination=dest, source_url="https://f.gov.example")
    st = rules.route_status(db, destination=dest)
    assert st["status"] == "sources_discovered"
    # Verified requirements.
    rules.review_rule(db, rule_id=r.id, decision="verified", actor="admin")
    st = rules.route_status(db, destination=dest)
    assert st["status"] == "requirements_verified"
    assert st["fee_verified"] is False
    # Verified fee ⇒ preparation + handoff supported.
    f = fees.create_fee(db, destination=dest, government_fee_cents=5000,
                        source_url="https://f.gov.example/fees")
    fees.review_fee(db, fee_id=f.id, decision="verified", actor="admin")
    st = rules.route_status(db, destination=dest)
    assert st["status"] == "applicant_handoff_supported"
    assert st["fee_verified"] is True
    # No adapter ⇒ never claims adapter_implemented or beyond.
    assert st["status"] != "production_approved"


def test_coverage_matrix_never_upgrades_rows(client, db):
    got = client.get("/routes/coverage", headers=AUTH).json()
    assert got["status_ladder"][0] == "not_researched"
    for row in got["routes"]:
        # No route may claim production approval without an adapter record.
        if row["status"] == "production_approved":
            adapter = db.execute(
                __import__("sqlalchemy").select(models.AdapterRecord).where(
                    models.AdapterRecord.country == row["destination"])).scalars().first()
            assert adapter is not None


# ---- fees ----
def test_fee_requires_source_and_review(db):
    with pytest.raises(ValueError):
        fees.create_fee(db, destination="Z1")
    f = fees.create_fee(db, destination="Z1", government_fee_cents=8000,
                        service_fee_cents=1500, currency="EUR",
                        source_url="https://z1.gov.example/fees")
    assert f.review_status == "pending"
    # Pending fee ⇒ no verified current fee ⇒ automated payment blocked.
    assert fees.verified_current_fee(db, destination="Z1") is None
    bd = fees.fee_breakdown(None)
    assert bd["available"] is False and bd["automated_payment_allowed"] is False
    fees.review_fee(db, fee_id=f.id, decision="verified", actor="admin")
    rec = fees.verified_current_fee(db, destination="Z1")
    assert rec is not None
    bd = fees.fee_breakdown(rec)
    assert bd["total_cents"] == 9500 and bd["currency"] == "EUR"
    assert bd["government_fee_cents"] == 8000 and bd["service_fee_cents"] == 1500
    assert bd["source_url"].startswith("https://z1.gov.example")


def test_stale_fee_blocks_automated_payment(db):
    f = fees.create_fee(db, destination="Z2", government_fee_cents=100,
                        source_url="https://z2.gov.example/fees")
    fees.review_fee(db, fee_id=f.id, decision="verified", actor="admin")
    # Age the record beyond the refresh window.
    f.retrieved_at = datetime.now(timezone.utc) - timedelta(days=fees.FEE_MAX_AGE_DAYS + 1)
    db.commit()
    assert fees.verified_current_fee(db, destination="Z2") is None
    stale = fees.stale_fees(db)
    assert any(s["destination"] == "Z2" for s in stale)


def test_fee_endpoints(client):
    r = client.post("/admin/routes/fees", headers=ADMIN, json={
        "destination": "Q1", "government_fee_cents": 12000, "currency": "USD",
        "source_url": "https://q1.gov.example/fees"})
    assert r.status_code == 200
    fid = r.json()["id"]
    # Before verification the public endpoint blocks automated payment.
    got = client.get("/routes/fees", headers=AUTH, params={"destination": "Q1"}).json()
    assert got["automated_payment_allowed"] is False
    client.post(f"/admin/routes/fees/{fid}/review", headers=ADMIN, json={"decision": "verified"})
    got = client.get("/routes/fees", headers=AUTH, params={"destination": "Q1"}).json()
    assert got["automated_payment_allowed"] is True and got["total_cents"] == 12000
    # Stale list is admin-only.
    assert client.get("/admin/routes/fees/stale", headers=AUTH).status_code == 403
    assert client.get("/admin/routes/fees/stale", headers=ADMIN).status_code == 200
