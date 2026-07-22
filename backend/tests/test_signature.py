"""Native Ellis e-signature: consent/intent/step-up gates, tamper-evidence,
invalidation on material change, cross-tenant protection."""
import pytest

from tests.conftest import AUTH, AUTH2
from app.providers import esign


def _make_case(client, headers=AUTH):
    r = client.post("/cases", headers=headers, json={
        "full_name": "Anna Eriksson", "email": "anna@example.com", "destination_country": "Mockland"})
    assert r.status_code == 200
    return r.json()["id"]


def test_prepare_returns_document_hash_and_stepup(client):
    cid = _make_case(client)
    r = client.post(f"/cases/{cid}/authorization/prepare", headers=AUTH,
                    json={"max_fee_cents": 10000, "currency": "USD"})
    assert r.status_code == 200
    body = r.json()
    assert body["document_hash"] and body["step_up_token"]
    assert body["template_version"] == esign.TEMPLATE_VERSION


def test_sign_requires_consent_intent_and_stepup(client):
    cid = _make_case(client)
    prep = client.post(f"/cases/{cid}/authorization/prepare", headers=AUTH,
                       json={"max_fee_cents": 10000, "currency": "USD"}).json()
    base = {"document_hash": prep["document_hash"], "signature_method": "typed",
            "signature_value": "Anna Eriksson", "step_up_token": prep["step_up_token"],
            "auth_method": "email_otp"}
    # Missing consent → 422.
    r = client.post(f"/cases/{cid}/authorization/sign", headers=AUTH,
                    json={**base, "consent_given": False, "intent_confirmed": True})
    assert r.status_code == 422
    # Missing intent → 422.
    r = client.post(f"/cases/{cid}/authorization/sign", headers=AUTH,
                    json={**base, "consent_given": True, "intent_confirmed": False})
    assert r.status_code == 422
    # Full consent + intent + valid step-up → signed.
    r = client.post(f"/cases/{cid}/authorization/sign", headers=AUTH,
                    json={**base, "consent_given": True, "intent_confirmed": True})
    assert r.status_code == 200, r.text
    assert r.json()["artifact_hash"]


def test_sign_rejects_forged_stepup_token(client):
    cid = _make_case(client)
    prep = client.post(f"/cases/{cid}/authorization/prepare", headers=AUTH,
                       json={"max_fee_cents": 10000, "currency": "USD"}).json()
    r = client.post(f"/cases/{cid}/authorization/sign", headers=AUTH, json={
        "document_hash": prep["document_hash"], "consent_given": True, "intent_confirmed": True,
        "signature_method": "typed", "signature_value": "Anna Eriksson",
        "step_up_token": "forged.deadbeef", "auth_method": "email_otp"})
    assert r.status_code == 401


def test_cross_tenant_cannot_sign(client):
    cid = _make_case(client, AUTH)
    prep = client.post(f"/cases/{cid}/authorization/prepare", headers=AUTH,
                       json={"max_fee_cents": 10000, "currency": "USD"}).json()
    # org2 tries to sign org1's case.
    r = client.post(f"/cases/{cid}/authorization/sign", headers=AUTH2, json={
        "document_hash": prep["document_hash"], "consent_given": True, "intent_confirmed": True,
        "signature_method": "typed", "signature_value": "x", "step_up_token": prep["step_up_token"],
        "auth_method": "email_otp"})
    assert r.status_code == 403


def test_tamper_evidence_and_hash_binding():
    text = esign.build_authorization_text(
        applicant={"full_name": "A", "email": "a@e.com"}, org_id="o", case_id="c", app_version=1,
        destination="Mockland", visa_type="tourist", portal="Mockland", max_fee_cents=10000,
        currency="USD", allow_auto_book=True, allow_auto_reschedule=False, allow_representative_submit=False)
    dh = esign.document_hash(text)
    prov = esign.NativeEllisSignatureProvider()
    req = esign.SignatureRequest(
        applicant={"full_name": "A", "email": "a@e.com"}, org_id="o", case_id="c", app_version=1,
        document_text=text, document_hash=dh, consent_given=True, intent_confirmed=True,
        signature_method="typed", signature_value="A", step_up_verified=True, auth_method="mfa",
        ip_address="127.0.0.1", user_agent="pytest")
    res = prov.sign(req)
    assert res["signed_pdf"].startswith(b"%PDF")
    # A different document with the SAME claimed hash is rejected (hash binding).
    req.document_text = text + "\nSNEAKILY ADDED CLAUSE"
    with pytest.raises(ValueError, match="hash mismatch"):
        prov.sign(req)


def test_material_change_invalidates_signature(client):
    cid = _make_case(client)
    # Add + approve a document so there is an app snapshot.
    d = client.post(f"/cases/{cid}/documents", headers=AUTH,
                    json={"name": "a.pdf", "mime": "application/pdf", "text": "note"}).json()
    client.post(f"/cases/{cid}/documents/{d['id']}/approve", headers=AUTH, json=[])
    prep = client.post(f"/cases/{cid}/authorization/prepare", headers=AUTH,
                       json={"max_fee_cents": 10000, "currency": "USD"}).json()
    signed = client.post(f"/cases/{cid}/authorization/sign", headers=AUTH, json={
        "document_hash": prep["document_hash"], "consent_given": True, "intent_confirmed": True,
        "signature_method": "typed", "signature_value": "Anna", "step_up_token": prep["step_up_token"],
        "auth_method": "email_otp"})
    assert signed.status_code == 200
    # Now materially change the application (approve an edit) → invalidates.
    r = client.post(f"/cases/{cid}/documents/{d['id']}/approve", headers=AUTH,
                    json=[{"key": "surname", "value": "CHANGED"}])
    assert r.json()["signatures_invalidated"] == 1
