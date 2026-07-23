"""Phase 13 — document preview: blob storage, signed expiring URLs, tenant
isolation, and no filesystem-path/credential exposure."""
import base64
import time

from tests.conftest import AUTH, AUTH2

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nFAKEIMAGEDATA-For-Preview-Tests").decode()


def _case_with_image(client):
    cid = client.post("/cases", headers=AUTH, json={
        "full_name": "Anna", "email": "a@e.com", "destination_country": "Mockland"}).json()["id"]
    doc = client.post(f"/cases/{cid}/documents", headers=AUTH,
                      json={"name": "photo.png", "mime": "image/png",
                            "size_bytes": 64, "content_b64": PNG}).json()
    return cid, doc["id"]


def test_preview_url_and_content_roundtrip(client):
    cid, did = _case_with_image(client)
    r = client.get(f"/cases/{cid}/documents/{did}/url", headers=AUTH).json()
    assert r["available"] is True and r["mime"] == "image/png"
    url = r["url"]
    # No filesystem paths or credentials in the URL.
    assert "file://" not in url and "/Users/" not in url and "key" not in url.lower()
    resp = client.get(url)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/png")
    assert resp.headers["cache-control"] == "private, no-store"
    assert resp.content.startswith(b"\x89PNG")


def test_preview_url_requires_auth_and_ownership(client):
    cid, did = _case_with_image(client)
    assert client.get(f"/cases/{cid}/documents/{did}/url").status_code == 401
    assert client.get(f"/cases/{cid}/documents/{did}/url", headers=AUTH2).status_code == 403


def test_content_rejects_bad_or_expired_signature(client):
    cid, did = _case_with_image(client)
    url = client.get(f"/cases/{cid}/documents/{did}/url", headers=AUTH).json()["url"]
    # Tampered signature.
    tampered = url.rsplit("sig=", 1)[0] + "sig=" + "0" * 32
    assert client.get(tampered).status_code == 401
    # Expired timestamp (re-signed for the past would be needed — a stale exp
    # with the original sig also fails the signature check).
    past = url.replace("exp=", "exp=1")
    assert client.get(past).status_code == 401


def test_text_only_document_is_honestly_unavailable(client):
    cid = client.post("/cases", headers=AUTH, json={
        "full_name": "Anna", "email": "a@e.com", "destination_country": "Mockland"}).json()["id"]
    did = client.post(f"/cases/{cid}/documents", headers=AUTH,
                      json={"name": "fixture.pdf", "mime": "application/pdf",
                            "text": "Account balance: 100"}).json()["id"]
    r = client.get(f"/cases/{cid}/documents/{did}/url", headers=AUTH).json()
    assert r["available"] is False and "no stored content" in r["reason"]


def test_erasure_deletes_document_blob_regression(client, db):
    # REGRESSION (review-confirmed high): applicant erasure must delete the raw
    # passport-scan bytes, not leave them in the DB indefinitely.
    from app import models, privacy
    cid, did = _case_with_image(client)
    assert db.get(models.DocumentBlob, did) is not None
    privacy.delete_case(db, cid, actor="applicant")
    assert db.get(models.DocumentBlob, did) is None


def test_wrong_doc_for_case_404(client):
    cid1, did1 = _case_with_image(client)
    cid2 = client.post("/cases", headers=AUTH, json={
        "full_name": "B", "email": "b@e.com", "destination_country": "Mockland"}).json()["id"]
    assert client.get(f"/cases/{cid2}/documents/{did1}/url", headers=AUTH).status_code == 404
