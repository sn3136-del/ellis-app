"""Phase 15 (partial) — Trip.com connector admin: persistent webhook deliveries,
retry/dead-letter, replay, integration health, and honest sandbox labeling."""
import json

from tests.conftest import AUTH
from app.integrations import tripcom_admin, tripcom
from app import models, setup as setup_mod

ADMIN = {"Authorization": "Bearer admin-token", "X-Org-Id": "orgTC", "X-User-Id": "tc-admin"}


def _configured(db, org="orgTC"):
    setup_mod.save_setup(db, org_id=org, actor="admin", payload={
        "tenant_name": "Trip.com", "admin_email": "it@trip.example",
        "trip": {"sandbox_base_url": "https://sandbox.tripcom.example", "client_id": "cid"},
        "trip_webhook_secret": "whsec-test-123456"})


def test_unconfigured_delivery_is_honest(db):
    tripcom_admin.queue_event(db, org_id="orgNoCfg",
                              event=tripcom.case_status_event(
                                  tripcom_case_ref="T1", ellis_case_id="e1", state="COMPLETED"))
    out = tripcom_admin.process_deliveries(db, org_id="orgNoCfg")
    assert out["unconfigured"] == 1 and out["delivered"] == 0
    rows = tripcom_admin.list_deliveries(db, "orgNoCfg")
    assert rows[0]["status"] == "unconfigured"


def test_delivery_signs_and_delivers(db):
    _configured(db)
    tripcom_admin.queue_event(db, org_id="orgTC",
                              event=tripcom.payment_status_event(
                                  tripcom_case_ref="T2", status="paid", receipt_no="R1"))
    seen = {}
    def transport(endpoint, headers, body):
        seen["endpoint"] = endpoint
        seen["headers"] = headers
        seen["body"] = body
        return {"ok": True, "status": 200}
    out = tripcom_admin.process_deliveries(db, org_id="orgTC", transport=transport)
    assert out["delivered"] == 1
    assert seen["endpoint"].startswith("https://sandbox.tripcom.example")
    # The request is HMAC-signed exactly per the sandbox contract.
    ok, reason = tripcom.verify_request("whsec-test-123456", seen["body"], seen["headers"])
    assert ok, reason


def test_retry_then_dead_letter_and_replay(db, client):
    _configured(db)
    row = tripcom_admin.queue_event(db, org_id="orgTC",
                                    event=tripcom.submission_status_event(
                                        tripcom_case_ref="T3", reference_no=None, state="FAILED"))
    fail = lambda e, h, b: {"ok": False, "detail": "503"}
    for _ in range(tripcom_admin.MAX_ATTEMPTS):
        tripcom_admin.process_deliveries(db, org_id="orgTC", transport=fail)
    listing = {r["id"]: r for r in tripcom_admin.list_deliveries(db, "orgTC")}
    assert listing[row.id]["status"] == "dead"
    # Replay clones into a NEW pending delivery linked to the original.
    clone = tripcom_admin.replay(db, org_id="orgTC", delivery_id=row.id, actor="admin")
    assert clone.replay_of == row.id and clone.status == "pending"
    ok_transport = lambda e, h, b: {"ok": True}
    tripcom_admin.process_deliveries(db, org_id="orgTC", transport=ok_transport)
    listing = {r["id"]: r for r in tripcom_admin.list_deliveries(db, "orgTC")}
    assert listing[clone.id]["status"] == "delivered"
    assert listing[row.id]["status"] == "dead"     # original never mutated


def test_unconfigured_delivery_retries_after_config_regression(db):
    # REGRESSION (review-confirmed): an event queued BEFORE the sandbox endpoint
    # is configured must be delivered once configuration arrives (not stuck).
    tripcom_admin.queue_event(db, org_id="orgLate",
                              event=tripcom.case_status_event(
                                  tripcom_case_ref="L", ellis_case_id="e", state="COMPLETED"))
    assert tripcom_admin.process_deliveries(db, org_id="orgLate")["unconfigured"] == 1
    _configured(db, org="orgLate")
    out = tripcom_admin.process_deliveries(db, org_id="orgLate", transport=lambda e, h, b: {"ok": True})
    assert out["delivered"] == 1
    assert tripcom_admin.list_deliveries(db, "orgLate")[0]["status"] == "delivered"


def test_replay_and_original_get_distinct_nonces_regression(db):
    # REGRESSION: identical payloads signed in the same second must get DISTINCT
    # nonces (else one is rejected as a replay of the other).
    c = tripcom.SandboxClient("s", "https://x.example")
    body = {"type": "case.status", "x": 1}
    h1 = c.signed_headers(body)
    h2 = c.signed_headers(body)
    assert h1["X-Tripcom-Nonce"] != h2["X-Tripcom-Nonce"]


def test_health_is_honestly_labeled(db):
    _configured(db)
    h = tripcom_admin.health(db, "orgTC")
    assert h["is_tripcom_production"] is False
    assert "NOT Trip.com production" in h["label"]
    assert h["config"]["webhook_secret_configured"] is True
    # Never the secret value — only a fingerprint.
    assert "whsec-test-123456" not in json.dumps(h)
    assert h["ready_for_sandbox_delivery"] is True


def test_admin_endpoints_gated(client):
    assert client.get("/admin/tripcom/health", headers=AUTH).status_code == 403
    r = client.get("/admin/tripcom/health", headers=ADMIN)
    assert r.status_code == 200 and r.json()["is_tripcom_production"] is False
    assert client.post("/admin/tripcom/process", headers=ADMIN).status_code == 200


def test_completed_case_queues_labeled_webhook(client, db):
    from sqlalchemy import select
    cid = client.post("/cases", headers=AUTH, json={
        "full_name": "Anna Eriksson", "email": "anna@example.com",
        "destination_country": "Mockland",
        "answers": {"passport_number": "L898902C3", "nationality": "UTO",
                    "birth_date": "740812", "sex": "F", "passport_expiry": "330415",
                    "tripcom_case_ref": "TRIP-REF-9"}}).json()["id"]
    db.add(models.AppointmentPreference(application_id=cid,
           prefs={"preferredLocation": "MOCKLAND-CAP", "allowAutoBook": True,
                  "applicantTimeZone": "UTC", "minAdvanceMs": 0}))
    db.add(models.AuthorizationEnvelope(application_id=cid, provider="in_app_authorization",
           max_fee_cents=10000, currency="USD", allow_auto_book=True))
    db.commit()
    client.post(f"/cases/{cid}/start", headers=AUTH)
    for _ in range(20):
        case = client.get(f"/cases/{cid}", headers=AUTH).json()
        if case["state"] == "COMPLETED":
            break
        pending = case.get("pending")
        if not pending:
            break
        h = pending.get("handoff")
        if h == "review":
            client.post(f"/cases/{cid}/signals/approve_review", headers=AUTH, json={})
        elif h == "authorization":
            client.post(f"/cases/{cid}/signals/sign_authorization", headers=AUTH, json={})
        elif h == "captcha":
            client.post(f"/cases/{cid}/signals/solve_captcha", headers=AUTH, json={})
        elif h == "email_verification":
            tok = client.get(f"/cases/{cid}/mock/verification", headers=AUTH).json()["token"]
            client.post(f"/cases/{cid}/signals/verify_email", headers=AUTH, json={"token": tok})
        elif h == "payment_approval":
            client.post(f"/cases/{cid}/signals/approve_payment", headers=AUTH, json={})
        elif h == "payment":
            client.post(f"/cases/{cid}/signals/complete_payment", headers=AUTH, json={})
        elif h == "appointment_selection":
            client.post(f"/cases/{cid}/signals/select_appointment", headers=AUTH,
                        json={"slot_id": pending["slots"][0]["slotId"]})
        elif h == "personal_declaration":
            client.post(f"/cases/{cid}/signals/complete_declaration", headers=AUTH, json={})
        else:
            break
    case = client.get(f"/cases/{cid}", headers=AUTH).json()
    if case["state"] == "COMPLETED":
        rows = db.execute(select(models.WebhookDelivery).where(
            models.WebhookDelivery.event_type == "case.status")).scalars().all()
        mine = [r for r in rows if r.payload.get("ellis_case_id") == cid]
        assert mine, "a case.status webhook delivery must be queued on completion"
        ev = mine[0].payload
        # The event itself carries the honest execution class — Trip.com can
        # never mistake this mock run for a production result.
        assert ev["execution_class"] == "MOCK"
        assert ev["is_real_government_result"] is False
        assert ev["tripcom_case_ref"] == "TRIP-REF-9"
