"""Official-portal + consular-jurisdiction resolvers (brief section 18)."""
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app as fastapi_app
from app.visa_snapshot import SNAPSHOT_DATE
from app.visa_snapshot.models import ConsularJurisdictionRule, OfficialPortalRecord

H = {"Authorization": "Bearer dev-token", "X-Org-Id": "org-rsv", "X-User-Id": "u1"}


def _client():
    return TestClient(fastapi_app)


def test_portal_resolver_manual_review_when_nothing_verified():
    r = _client().get("/snapshot/resolvers/portal", params={"destination": "TD"}, headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "MANUAL_REVIEW_REQUIRED" and body["portals"] == []
    assert "may have changed" in body["disclaimer"]


def test_portal_resolver_returns_only_verified():
    db = SessionLocal()
    try:
        db.add(OfficialPortalRecord(snapshot_date=SNAPSHOT_DATE, portal_uid="KOR:evisa:r1",
                                    destination_country="KOR", portal_kind="evisa_portal",
                                    url="https://www.visa.go.kr/", hostnames=["www.visa.go.kr"],
                                    verification_status="verified_official_domain"))
        db.add(OfficialPortalRecord(snapshot_date=SNAPSHOT_DATE, portal_uid="KOR:seo:r1",
                                    destination_country="KOR", portal_kind="authorized_visa_center",
                                    url="https://random-agency.example/", hostnames=["random-agency.example"],
                                    verification_status="unverified"))
        db.commit()
    finally:
        db.close()
    body = _client().get("/snapshot/resolvers/portal", params={"destination": "KR"}, headers=H).json()
    assert body["status"] == "VERIFIED"
    assert [p["url"] for p in body["portals"]] == ["https://www.visa.go.kr/"]
    assert body["unverified_count"] == 1   # the unverified one is counted, never served


def test_jurisdiction_resolver_never_guesses():
    body = _client().get("/snapshot/resolvers/jurisdiction",
                         params={"destination": "KR", "residence": "US"}, headers=H).json()
    assert body["status"] == "MANUAL_REVIEW_REQUIRED"
    db = SessionLocal()
    try:
        db.add(ConsularJurisdictionRule(snapshot_date=SNAPSHOT_DATE, destination_country="KOR",
                                        residence_jurisdiction="USA",
                                        competent_post_name="Consulate General of the Republic of Korea in New York",
                                        competent_post_kind="consulate_general",
                                        competent_post_url="https://overseas.mofa.go.kr/us-newyork-en/index.do",
                                        verification_status="verified"))
        # An UNVERIFIED rule for another residence must still yield manual review.
        db.add(ConsularJurisdictionRule(snapshot_date=SNAPSHOT_DATE, destination_country="KOR",
                                        residence_jurisdiction="CAN",
                                        competent_post_name="guessed post",
                                        verification_status="manual_review_required"))
        db.commit()
    finally:
        db.close()
    ok = _client().get("/snapshot/resolvers/jurisdiction",
                       params={"destination": "KR", "residence": "US"}, headers=H).json()
    assert ok["status"] == "VERIFIED"
    assert "New York" in ok["competent_post"]["name"]
    notok = _client().get("/snapshot/resolvers/jurisdiction",
                          params={"destination": "KR", "residence": "CA"}, headers=H).json()
    assert notok["status"] == "MANUAL_REVIEW_REQUIRED"


def test_resolvers_reject_unknown_countries():
    c = _client()
    assert c.get("/snapshot/resolvers/portal", params={"destination": "Narnia"}, headers=H).status_code == 400
    assert c.get("/snapshot/resolvers/jurisdiction",
                 params={"destination": "KR", "residence": "Narnia"}, headers=H).status_code == 400
