"""Trip.com integration layer — versioned SANDBOX contract.

Trip.com has not published final specs, so this implements a self-consistent
sandbox: HMAC request signing, replay protection (timestamp + nonce),
idempotency keys, and typed webhook payloads for case/payment/appointment/
submission status. The exact production schemas/keys are listed in
docs/TRIPCOM_REQUIREMENTS.json and are NOT invented as final.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid

CONTRACT_VERSION = "tripcom-sandbox-v1"
_SIGN_HEADER = "X-Tripcom-Signature"
_TS_HEADER = "X-Tripcom-Timestamp"
_NONCE_HEADER = "X-Tripcom-Nonce"
_MAX_SKEW_S = 300

# In-memory replay + idempotency stores (Redis/Postgres in production).
_SEEN_NONCES: dict[str, float] = {}
_IDEMPOTENCY: dict[str, dict] = {}


def sign_payload(secret: str, body: bytes, timestamp: str, nonce: str) -> str:
    mac = hmac.new(secret.encode(), timestamp.encode() + b"." + nonce.encode() + b"." + body,
                   hashlib.sha256)
    return mac.hexdigest()


def verify_request(secret: str, body: bytes, headers: dict) -> tuple[bool, str]:
    ts = headers.get(_TS_HEADER) or headers.get(_TS_HEADER.lower(), "")
    nonce = headers.get(_NONCE_HEADER) or headers.get(_NONCE_HEADER.lower(), "")
    sig = headers.get(_SIGN_HEADER) or headers.get(_SIGN_HEADER.lower(), "")
    if not (ts and nonce and sig):
        return False, "missing_signing_headers"
    try:
        skew = abs(time.time() - float(ts))
    except ValueError:
        return False, "bad_timestamp"
    if skew > _MAX_SKEW_S:
        return False, "timestamp_out_of_window"
    if nonce in _SEEN_NONCES:
        return False, "replay_detected"
    expected = sign_payload(secret, body, ts, nonce)
    if not hmac.compare_digest(expected, sig):
        return False, "bad_signature"
    _SEEN_NONCES[nonce] = time.time()
    return True, "ok"


def idempotent(key: str, compute):
    """Return the stored response for an idempotency key, else compute + store."""
    if not key:
        return compute(), False
    if key in _IDEMPOTENCY:
        return _IDEMPOTENCY[key], True
    res = compute()
    _IDEMPOTENCY[key] = res
    return res, False


# Typed webhook payloads Ellis emits back to Trip.com (shapes are sandbox-final).
def case_status_event(*, tripcom_case_ref: str, ellis_case_id: str, state: str) -> dict:
    return {"type": "case.status", "contract": CONTRACT_VERSION,
            "tripcom_case_ref": tripcom_case_ref, "ellis_case_id": ellis_case_id, "state": state}


def payment_status_event(*, tripcom_case_ref: str, status: str, receipt_no: str | None) -> dict:
    return {"type": "payment.status", "contract": CONTRACT_VERSION,
            "tripcom_case_ref": tripcom_case_ref, "status": status, "receipt_no": receipt_no}


def appointment_status_event(*, tripcom_case_ref: str, confirmation_no: str | None, start_utc: int | None) -> dict:
    return {"type": "appointment.status", "contract": CONTRACT_VERSION,
            "tripcom_case_ref": tripcom_case_ref, "confirmation_no": confirmation_no, "start_utc": start_utc}


def submission_status_event(*, tripcom_case_ref: str, reference_no: str | None, state: str) -> dict:
    return {"type": "submission.status", "contract": CONTRACT_VERSION,
            "tripcom_case_ref": tripcom_case_ref, "reference_no": reference_no, "state": state}


def applicant_deep_link(base_url: str, ellis_case_id: str, token: str) -> str:
    return f"{base_url.rstrip('/')}/case/{ellis_case_id}?t={token}"


class SandboxClient:
    """A local stand-in for the Trip.com API used by contract tests. Signs
    requests exactly as the real client will."""
    def __init__(self, secret: str, base_url: str = "https://sandbox.tripcom.example"):
        self.secret = secret
        self.base_url = base_url

    def signed_headers(self, body: dict) -> dict:
        raw = json.dumps(body, sort_keys=True).encode()
        ts = str(int(time.time()))
        # A random nonce (not derived from body+ts) so two identical payloads
        # signed in the same second — e.g. a retry and an admin replay — get
        # DISTINCT nonces and neither is rejected as a replay of the other.
        nonce = uuid.uuid4().hex[:16]
        return {_TS_HEADER: ts, _NONCE_HEADER: nonce, _SIGN_HEADER: sign_payload(self.secret, raw, ts, nonce)}


def _reset_for_tests():
    _SEEN_NONCES.clear()
    _IDEMPOTENCY.clear()
