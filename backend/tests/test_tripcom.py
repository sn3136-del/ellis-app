"""Trip.com sandbox contract: signing, replay protection, idempotency, payloads."""
import json

from app.integrations import tripcom


def setup_function():
    tripcom._reset_for_tests()


def test_signed_request_verifies():
    c = tripcom.SandboxClient("secret123")
    body = {"case": "abc", "state": "SUBMITTED"}
    raw = json.dumps(body, sort_keys=True).encode()
    h = c.signed_headers(body)
    ok, reason = tripcom.verify_request("secret123", raw, h)
    assert ok and reason == "ok"


def test_bad_signature_rejected():
    c = tripcom.SandboxClient("secret123")
    body = {"x": 1}
    raw = json.dumps(body, sort_keys=True).encode()
    h = c.signed_headers(body)
    ok, reason = tripcom.verify_request("WRONG-secret", raw, h)
    assert not ok and reason == "bad_signature"


def test_replay_detected():
    c = tripcom.SandboxClient("s")
    body = {"x": 1}
    raw = json.dumps(body, sort_keys=True).encode()
    h = c.signed_headers(body)
    ok1, _ = tripcom.verify_request("s", raw, h)
    ok2, reason2 = tripcom.verify_request("s", raw, h)  # same nonce again
    assert ok1 and not ok2 and reason2 == "replay_detected"


def test_stale_timestamp_rejected():
    raw = b"{}"
    headers = {tripcom._TS_HEADER: "1000000000", tripcom._NONCE_HEADER: "n1",
               tripcom._SIGN_HEADER: tripcom.sign_payload("s", raw, "1000000000", "n1")}
    ok, reason = tripcom.verify_request("s", raw, headers)
    assert not ok and reason == "timestamp_out_of_window"


def test_idempotency():
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return {"result": calls["n"]}

    r1, replay1 = tripcom.idempotent("key-1", compute)
    r2, replay2 = tripcom.idempotent("key-1", compute)
    assert r1 == r2 and replay1 is False and replay2 is True and calls["n"] == 1


def test_webhook_payload_shapes():
    e = tripcom.case_status_event(tripcom_case_ref="T1", ellis_case_id="E1", state="COMPLETED")
    assert e["type"] == "case.status" and e["contract"] == tripcom.CONTRACT_VERSION
    p = tripcom.payment_status_event(tripcom_case_ref="T1", status="paid", receipt_no="R1")
    assert p["type"] == "payment.status"
    a = tripcom.appointment_status_event(tripcom_case_ref="T1", confirmation_no="A1", start_utc=1)
    assert a["type"] == "appointment.status"
    s = tripcom.submission_status_event(tripcom_case_ref="T1", reference_no="REF1", state="submitted")
    assert s["type"] == "submission.status"
