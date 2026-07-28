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


def test_single_ceremony_grants_standing_auth_and_contact_and_advance_consent(client):
    # Product decision 2026-07-27: the ONE authorization signature also grants
    # the standing authorization, records the intake email as the portal
    # contact, and stands as advance consent for the final review — the
    # applicant never signs a second time.
    from app.db import SessionLocal
    from app import authorization as standing, final_review, models
    cid = _make_case(client)
    prep = client.post(f"/cases/{cid}/authorization/prepare", headers=AUTH,
                       json={"max_fee_cents": 10000, "currency": "USD"}).json()
    r = client.post(f"/cases/{cid}/authorization/sign", headers=AUTH, json={
        "document_hash": prep["document_hash"], "consent_given": True,
        "intent_confirmed": True, "signature_method": "typed",
        "signature_value": "Anna Eriksson", "step_up_token": prep["step_up_token"],
        "auth_method": "email_otp"})
    assert r.status_code == 200, r.text
    db = SessionLocal()
    try:
        app_row = db.get(models.VisaApplication, cid)
        grant = standing.current(db, cid)
        assert grant is not None and not grant.revoked
        assert "submit_after_signed_final_review" in grant.permitted_actions
        # Contact is confirmed from the intake email — no separate step.
        from app import checklist_intake
        assert checklist_intake.stage_progress(db, cid, stage="contact_confirmed") is not None
        # Advance consent freezes + signs the review version without a second
        # ceremony, and verify_ready_to_submit accepts it.
        row = final_review.ensure_advance_signed(db, app_row)
        assert row is not None and row.signed and not row.invalidated
        assert final_review.verify_ready_to_submit(db, app_row).id == row.id
    finally:
        db.close()


def test_advance_consent_refreshes_after_applicant_answers_change(client):
    # New answers the applicant provides through Ellis re-freeze the review
    # version under the SAME signature — never a second signing ceremony,
    # and never a silent submit of unseen material: the frozen package is
    # rebuilt from the current answers and re-audited.
    from app.db import SessionLocal
    from app import final_review, models
    cid = _make_case(client)
    prep = client.post(f"/cases/{cid}/authorization/prepare", headers=AUTH,
                       json={"max_fee_cents": 10000, "currency": "USD"}).json()
    assert client.post(f"/cases/{cid}/authorization/sign", headers=AUTH, json={
        "document_hash": prep["document_hash"], "consent_given": True,
        "intent_confirmed": True, "signature_method": "typed",
        "signature_value": "Anna Eriksson", "step_up_token": prep["step_up_token"],
        "auth_method": "email_otp"}).status_code == 200
    db = SessionLocal()
    try:
        app_row = db.get(models.VisaApplication, cid)
        first = final_review.ensure_advance_signed(db, app_row)
        assert first is not None
        app_row.answers = dict(app_row.answers or {}, vietnam_ward="Ward 4")
        db.commit()
        second = final_review.ensure_advance_signed(db, app_row)
        assert second is not None and second.version == first.version + 1
        assert second.content_hash != first.content_hash
        assert final_review.signed_current(db, app_row).id == second.id
    finally:
        db.close()


def test_answer_changes_never_revoke_the_single_authorization_signature(client):
    # THE dead-end the review caught: ensure_advance_signed binds the review
    # version to the applicant's ONE authorization signature, and
    # check_and_invalidate used to cascade into it — so the first mid-run
    # answer revoked the authorization itself and nothing could ever re-sign
    # it. Only the stale review VERSION may die.
    from app.db import SessionLocal
    from app import final_review, models
    cid = _make_case(client)
    prep = client.post(f"/cases/{cid}/authorization/prepare", headers=AUTH,
                       json={"max_fee_cents": 10000, "currency": "USD"}).json()
    signed = client.post(f"/cases/{cid}/authorization/sign", headers=AUTH, json={
        "document_hash": prep["document_hash"], "consent_given": True,
        "intent_confirmed": True, "signature_method": "typed",
        "signature_value": "Anna Eriksson", "step_up_token": prep["step_up_token"],
        "auth_method": "email_otp"}).json()
    db = SessionLocal()
    try:
        app_row = db.get(models.VisaApplication, cid)
        first = final_review.ensure_advance_signed(db, app_row)
        assert first is not None
        # Applicant answers a portal question mid-run.
        app_row.answers = dict(app_row.answers or {}, vietnam_ward="Ward 4")
        db.commit()
        assert final_review.check_and_invalidate(db, app_row) is True
        sig = db.get(models.NativeSignature, signed["signature_id"])
        assert sig.invalidated is False, "the authorization signature must survive"
        # And the case can still reach a signed-current version — no dead end.
        again = final_review.ensure_advance_signed(db, app_row)
        assert again is not None and again.version == first.version + 1
        assert final_review.verify_ready_to_submit(db, app_row).id == again.id
    finally:
        db.close()


def test_a_dedicated_final_review_signature_is_still_invalidated(client):
    # The cascade must still fire for a signature made in its OWN ceremony:
    # only the reused authorization signature is protected.
    from app.db import SessionLocal
    from app import final_review, models
    cid = _make_case(client)
    db = SessionLocal()
    try:
        app_row = db.get(models.VisaApplication, cid)
        row = final_review.create_review_version(db, app_row, actor="applicant")
        sig = models.NativeSignature(
            org_id=app_row.org_id, application_id=cid, app_version=app_row.current_version,
            provider="native", template_version="t", consent_version="c",
            document_hash="d", artifact_hash="final-review-only-artifact",
            artifact_ref="local://x", signature_method="typed", auth_method="email_otp",
            ip_address="", user_agent="", app_snapshot_hash="s")
        db.add(sig)
        db.flush()
        final_review.record_signature(db, row, signature_id=sig.id, actor="applicant")
        app_row.answers = dict(app_row.answers or {}, changed="yes")
        db.commit()
        assert final_review.check_and_invalidate(db, app_row) is True
        assert db.get(models.NativeSignature, sig.id).invalidated is True
    finally:
        db.close()


def test_post_answers_never_voids_the_single_authorization_signature(client):
    """Answers the applicant TYPES INTO ELLIS (POST /answers — the pre-run
    'a few more details' card, health questions) are consented by
    construction: the frozen review version goes stale, the ONE authorization
    signature survives (same policy as the provide_information signal path).
    Document EDITS keep strict §7 invalidation — pinned separately by
    test_material_change_invalidates_signature."""
    from app.db import SessionLocal
    from app import final_review, models
    from sqlalchemy import select as sa_select
    cid = _make_case(client)
    prep = client.post(f"/cases/{cid}/authorization/prepare", headers=AUTH,
                       json={"max_fee_cents": 10000, "currency": "USD"}).json()
    assert client.post(f"/cases/{cid}/authorization/sign", headers=AUTH, json={
        "document_hash": prep["document_hash"], "consent_given": True,
        "intent_confirmed": True, "signature_method": "typed",
        "signature_value": "Anna Eriksson", "step_up_token": prep["step_up_token"],
        "auth_method": "email_otp"}).status_code == 200
    r = client.post(f"/cases/{cid}/answers", headers=AUTH,
                    json={"answers": {"religion": "None",
                                      "birth_date": "08/12/1974"}})
    assert r.status_code == 200
    assert r.json()["signatures_invalidated"] == 0
    db = SessionLocal()
    try:
        sigs = db.execute(sa_select(models.NativeSignature).where(
            models.NativeSignature.application_id == cid)).scalars().all()
        assert sigs and all(not s.invalidated for s in sigs)
        # Dates typed in the U.S. display format are stored canonical ISO.
        app_row = db.get(models.VisaApplication, cid)
        assert app_row.answers["birth_date"] == "1974-08-12"
        # Advance consent still re-freezes under the surviving signature.
        assert final_review.ensure_advance_signed(db, app_row) is not None
    finally:
        db.close()


def test_ceremonyless_standing_grant_endpoint_is_gone(client):
    """Regression (2026-07-28): the standing authorization is granted ONLY
    inside the signature ceremony (/authorization/sign). A bare POST grant
    would let a case reach "authorized" with no recorded NativeSignature —
    and service.signal treats a valid grant as proof the ceremony happened,
    so the ceremony-less endpoint was an authorization bypass. GET (read)
    and DELETE (revoke) remain applicant actions on the same path."""
    from app.db import SessionLocal
    from app import authorization as standing
    cid = _make_case(client)
    r = client.post(f"/cases/{cid}/standing-authorization", headers=AUTH, json={})
    assert r.status_code == 405
    db = SessionLocal()
    try:
        assert standing.current(db, cid) is None
    finally:
        db.close()
    assert client.get(f"/cases/{cid}/standing-authorization",
                      headers=AUTH).status_code == 200


def test_advance_consent_refuses_a_fee_above_the_authorized_ceiling(client):
    # The applicant's signature carries a fee CEILING. A fee that drifts above
    # it between signing and enqueue was never consented to — advance consent
    # must step aside (explicit review), not freeze the bigger amount under
    # the old signature.
    from app.db import SessionLocal
    from app import final_review, models
    cid = _make_case(client)
    prep = client.post(f"/cases/{cid}/authorization/prepare", headers=AUTH,
                       json={"max_fee_cents": 5000, "currency": "USD"}).json()
    assert client.post(f"/cases/{cid}/authorization/sign", headers=AUTH, json={
        "document_hash": prep["document_hash"], "consent_given": True,
        "intent_confirmed": True, "signature_method": "typed",
        "signature_value": "Anna Eriksson", "step_up_token": prep["step_up_token"],
        "auth_method": "email_otp"}).status_code == 200
    db = SessionLocal()
    try:
        app_row = db.get(models.VisaApplication, cid)
        # Within the ceiling (no verified fee record at all): allowed.
        assert final_review.ensure_advance_signed(db, app_row) is not None
        # Now publish a verified fee ABOVE the signed ceiling.
        from datetime import datetime, timezone
        db.add(models.FeeRecord(
            destination=app_row.destination_country, visa_type=app_row.visa_type,
            government_fee_cents=9000, service_fee_cents=1000, currency="USD",
            version=1, source_url="https://example.gov/fees",
            review_status="verified", retrieved_at=datetime.now(timezone.utc)))
        db.commit()
        # The frozen row no longer matches (fee is in material_state), and a
        # NEW one may not be minted under the old ceiling.
        final_review.check_and_invalidate(db, app_row)
        assert final_review.ensure_advance_signed(db, app_row) is None
    finally:
        db.close()
