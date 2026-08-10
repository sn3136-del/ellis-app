"""Official case-status tracking: an edition-neutral read-only provider and the
H1B surface that reads it.

Two things are pinned here. First, honesty: a malformed receipt never reaches
the government API, and an unconfigured, unreachable, sandbox, or unrecognized
answer is never presented as a real government outcome. Second, neutrality: the
provider is keyed by receipt number alone — no visa type, no family, no
edition — so a tourist route adopts it unchanged.

Every test is hermetic: the provider's single HTTP seam is replaced, and each
test asserts what did (or did not) pass through it.
"""
import datetime as dt
import inspect
import os
import re

import pytest
from sqlalchemy import select

from app import config
from app.execution import ExecutionClass
from app.h1b import models as h1b_models
from app.providers import torch_status

from .conftest import AUTH, AUTH2

ADMIN_AUTH = {"Authorization": "Bearer admin-token",
              "X-Org-Id": "org1", "X-User-Id": "admin1"}

VALID_RECEIPT = "IOE0912345678"
PROD_HOST = "https://api.uscis.gov"
SANDBOX_HOST = "https://api-int.uscis.gov"

_TOKEN_OK = (200, {"access_token": "tok-abc", "expires_in": "1799"})

_APPROVED = {"case_status": {
    "receiptNumber": VALID_RECEIPT, "formType": "I-129",
    "submittedDate": "1746057600000",
    "modifiedDate": str(int(dt.datetime(2026, 7, 14, tzinfo=dt.timezone.utc)
                            .timestamp() * 1000)),
    "current_case_status_text_en": "Case Was Approved",
    "current_case_status_desc_en": "We approved your Form I-129."}}


class _Http:
    """Scripted stand-in for the provider's ONE HTTP seam. It records every
    call, so a test can prove that nothing reached the network at all."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, *, headers, data=None):
        self.calls.append({"method": method, "url": url, "headers": headers,
                           "data": data})
        if not self.responses:
            raise AssertionError(f"unscripted HTTP call: {method} {url}")
        return self.responses.pop(0)


_TORCH_ENV = ("USCIS_TORCH_BASE_URL", "USCIS_TORCH_CLIENT_ID",
              "USCIS_TORCH_CLIENT_SECRET", "USCIS_TORCH_ACCESS_TOKEN",
              "USCIS_TORCH_ENABLED")


@pytest.fixture()
def torch_env():
    """Yields a configure(**env) callable. Starts UNCONFIGURED (the deployment
    default) and restores the process environment on the way out."""
    saved = {k: os.environ.get(k) for k in _TORCH_ENV}

    def configure(**env):
        for k in _TORCH_ENV:
            os.environ.pop(k, None)
        os.environ.update(env)
        config.settings.cache_clear()
        torch_status.reset_token_cache()

    configure()
    yield configure
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    config.settings.cache_clear()
    torch_status.reset_token_cache()


def _live(configure, base_url=PROD_HOST):
    configure(USCIS_TORCH_BASE_URL=base_url,
              USCIS_TORCH_CLIENT_ID="cid", USCIS_TORCH_CLIENT_SECRET="secret")


@pytest.fixture()
def status_client():
    """The status router is included by main.py in the shipped app; this wires
    it defensively so the suite exercises the real endpoint either way."""
    from fastapi.testclient import TestClient

    from app.h1b.status_api import router
    from app.main import app
    path = "/h1b/cases/{case_id}/status"
    if not any(getattr(r, "path", "") == path for r in app.routes):
        app.include_router(router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# receipt validation — before any call
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "", "   ", "IOE091234567", "IOE09123456789", "ABC0912345678",
    "YSC0912345678", "IOE09123456AB", "0912345678", "IOE-0912-34567",
    "'; SELECT 1 --",
])
def test_malformed_receipt_rejected_before_any_call(torch_env, monkeypatch, bad):
    _live(torch_env)
    http = _Http()  # every response unscripted: a call would fail loudly
    monkeypatch.setattr(torch_status, "_http_json", http)
    with pytest.raises(torch_status.InvalidReceiptNumber):
        torch_status.get_case_status(bad)
    assert http.calls == [], "a malformed receipt reached the government API"


def test_normalization_strips_human_formatting_only(torch_env):
    assert torch_status.normalize_receipt_number(" ioe-0912-345-678 ") == VALID_RECEIPT
    assert torch_status.is_valid_receipt_number("wac 091 234 5678")
    # Normalization removes formatting; it never repairs a receipt.
    assert not torch_status.is_valid_receipt_number("wac 091 234 567")


def test_every_documented_prefix_is_accepted(torch_env):
    for prefix in torch_status.RECEIPT_PREFIXES:
        assert torch_status.is_valid_receipt_number(prefix + "0912345678"), prefix


# ---------------------------------------------------------------------------
# honest degradation
# ---------------------------------------------------------------------------

def test_unconfigured_returns_live_false_and_never_calls(torch_env, monkeypatch):
    http = _Http()
    monkeypatch.setattr(torch_status, "_http_json", http)
    assert torch_status.is_configured() is False
    out = torch_status.get_case_status(VALID_RECEIPT)
    assert http.calls == []
    assert out["live"] is False
    assert out["status"] == "tracking_unavailable"
    assert out["note"] == "USCIS Torch API not configured"
    assert out["source"] == "uscis_torch"
    # Nothing invented to fill the gap.
    assert out["status_date"] is None and out["description"] == ""
    assert torch_status.is_real_government_status(out) is False


def test_disabled_flag_turns_configured_credentials_off(torch_env, monkeypatch):
    torch_env(USCIS_TORCH_BASE_URL=PROD_HOST, USCIS_TORCH_ACCESS_TOKEN="tok",
              USCIS_TORCH_ENABLED="false")
    http = _Http()
    monkeypatch.setattr(torch_status, "_http_json", http)
    assert torch_status.is_configured() is False
    assert torch_status.get_case_status(VALID_RECEIPT)["status"] == "tracking_unavailable"
    assert http.calls == []


def test_authentication_failure_degrades_honestly(torch_env, monkeypatch):
    _live(torch_env)
    http = _Http((401, {"error": "invalid_client"}))
    monkeypatch.setattr(torch_status, "_http_json", http)
    out = torch_status.get_case_status(VALID_RECEIPT)
    assert out["live"] is False
    assert out["status"] == "tracking_unavailable"
    assert "authentication failed" in out["note"]
    # The case endpoint was never reached without a token.
    assert len(http.calls) == 1 and http.calls[0]["url"].endswith("/oauth/accesstoken")


def test_unknown_receipt_is_not_a_status(torch_env, monkeypatch):
    _live(torch_env)
    monkeypatch.setattr(torch_status, "_http_json",
                        _Http(_TOKEN_OK, (404, {"error": "not found"})))
    out = torch_status.get_case_status(VALID_RECEIPT)
    assert out["status"] == "receipt_not_found"
    assert out["live"] is False
    assert torch_status.is_real_government_status(out) is False


def test_provider_error_and_transport_failure_degrade(torch_env, monkeypatch):
    _live(torch_env)
    monkeypatch.setattr(torch_status, "_http_json",
                        _Http(_TOKEN_OK, (503, {})))
    out = torch_status.get_case_status(VALID_RECEIPT)
    assert out["status"] == "tracking_unavailable" and out["live"] is False
    assert "503" in out["note"]

    torch_status.reset_token_cache()

    def _boom(*a, **kw):
        raise RuntimeError("connection reset by peer")

    monkeypatch.setattr(torch_status, "_http_json", _boom)
    out = torch_status.get_case_status(VALID_RECEIPT)
    assert out["live"] is False and out["status"] == "tracking_unavailable"
    # Transport internals never leak into the applicant-facing note.
    assert "connection reset" not in out["note"]


def test_empty_status_text_is_not_a_status(torch_env, monkeypatch):
    _live(torch_env)
    monkeypatch.setattr(torch_status, "_http_json",
                        _Http(_TOKEN_OK, (200, {"case_status": {"formType": "I-129"}})))
    out = torch_status.get_case_status(VALID_RECEIPT)
    assert out["live"] is False and out["status"] == "tracking_unavailable"


# ---------------------------------------------------------------------------
# a live read
# ---------------------------------------------------------------------------

def test_live_production_read_is_verbatim_and_real(torch_env, monkeypatch):
    _live(torch_env)
    http = _Http(_TOKEN_OK, (200, _APPROVED))
    monkeypatch.setattr(torch_status, "_http_json", http)
    out = torch_status.get_case_status("ioe-0912-345-678")
    assert out["live"] is True
    # The government's own wording, unrephrased.
    assert out["status"] == "Case Was Approved"
    assert out["description"] == "We approved your Form I-129."
    assert out["form_type"] == "I-129"
    assert out["status_date"] == "2026-07-14"       # epoch millis -> ISO
    assert out["execution_class"] == str(ExecutionClass.LIVE_PRODUCTION)
    assert torch_status.is_real_government_status(out) is True
    case_call = http.calls[-1]
    assert case_call["method"] == "GET"
    assert case_call["url"] == f"{PROD_HOST}/case-status/{VALID_RECEIPT}"
    assert case_call["headers"]["authorization"] == "Bearer tok-abc"


def test_token_is_reused_across_receipts(torch_env, monkeypatch):
    _live(torch_env)
    http = _Http(_TOKEN_OK, (200, _APPROVED), (200, _APPROVED))
    monkeypatch.setattr(torch_status, "_http_json", http)
    torch_status.get_case_status(VALID_RECEIPT)
    torch_status.get_case_status("WAC0912345678")
    assert sum(1 for c in http.calls if "accesstoken" in c["url"]) == 1


def test_sandbox_host_is_live_but_never_a_real_government_result(torch_env,
                                                                 monkeypatch):
    _live(torch_env, base_url=SANDBOX_HOST)
    monkeypatch.setattr(torch_status, "_http_json",
                        _Http(_TOKEN_OK, (200, _APPROVED)))
    out = torch_status.get_case_status(VALID_RECEIPT)
    assert out["live"] is True
    assert out["execution_class"] == str(ExecutionClass.LIVE_SANDBOX)
    # A sandbox answer is a real round trip and still not a real adjudication.
    assert torch_status.is_real_government_status(out) is False


def test_display_guard_rejects_hand_built_claims():
    for forged in ({"live": True, "source": "uscis_torch",
                    "execution_class": str(ExecutionClass.LIVE_PRODUCTION),
                    "status": "tracking_unavailable"},
                   {"live": False, "source": "uscis_torch",
                    "execution_class": str(ExecutionClass.LIVE_PRODUCTION),
                    "status": "Case Was Approved"},
                   {"live": True, "source": "myuscis_scrape",
                    "execution_class": str(ExecutionClass.LIVE_PRODUCTION),
                    "status": "Case Was Approved"}):
        assert torch_status.is_real_government_status(forged) is False


def test_provider_has_no_filing_path():
    """Structural, not aspirational: only two URLs exist, only one of them is a
    POST (the token exchange), and only one function may touch the network."""
    src = inspect.getsource(torch_status)
    urls = sorted(re.findall(r'f"\{_base_url\(\)\}([^"]*)"', src))
    assert urls == ["/case-status/{receipt}", "/oauth/accesstoken"]
    assert src.count('"POST"') == 1
    assert src.count("import httpx") == 1


# ---------------------------------------------------------------------------
# capability reporting
# ---------------------------------------------------------------------------

def test_capabilities_reports_case_status_honestly(torch_env):
    caps = config.capabilities()
    assert caps["uscis_case_status"] is False
    assert caps["fallbacks"]["case_status"] == "tracking_unavailable"
    torch_env(USCIS_TORCH_BASE_URL=PROD_HOST, USCIS_TORCH_ACCESS_TOKEN="tok")
    caps = config.capabilities()
    assert caps["uscis_case_status"] is True
    assert caps["fallbacks"]["case_status"] == "uscis_torch"


# ---------------------------------------------------------------------------
# CROSS-EDITION: the provider is not an H1B object
# ---------------------------------------------------------------------------

def test_provider_is_edition_neutral(torch_env, monkeypatch):
    """The tourist editions must be able to adopt this unchanged. It is keyed by
    receipt number alone: no case, no visa type, no portal family."""
    src = inspect.getsource(torch_status)
    assert "h1b" not in src.lower() and "h-1b" not in src.lower()
    assert "app.h1b" not in src and "from ..h1b" not in src
    assert "visa_type" not in src and "family_id" not in src
    params = list(inspect.signature(torch_status.get_case_status).parameters)
    assert params == ["receipt_number"]


def test_tourist_family_can_track_a_receipt_without_any_h1b_case(torch_env,
                                                                 monkeypatch):
    """A tourist-edition caller (vietnam-evisa, usa-esta) gets the identical
    answer from the identical call — the provider never learns who asked."""
    _live(torch_env)
    monkeypatch.setattr(torch_status, "_http_json",
                        _Http(_TOKEN_OK, (200, _APPROVED), (200, _APPROVED)))
    answers = {}
    for family_id in ("vietnam-evisa", "usa-esta"):
        # The family is the CALLER's context; the provider takes the receipt.
        answers[family_id] = torch_status.get_case_status(VALID_RECEIPT)
    assert answers["vietnam-evisa"] == answers["usa-esta"]
    assert answers["usa-esta"]["live"] is True
    assert torch_status.is_real_government_status(answers["usa-esta"]) is True


def test_unconfigured_tourist_call_is_honest_too(torch_env, monkeypatch):
    http = _Http()
    monkeypatch.setattr(torch_status, "_http_json", http)
    out = torch_status.get_case_status("MSC0912345678")
    assert out == {"receipt_number": "MSC0912345678",
                   "status": "tracking_unavailable", "status_date": None,
                   "description": "", "form_type": "", "source": "uscis_torch",
                   "live": False,
                   "execution_class": str(ExecutionClass.UNSUPPORTED),
                   "note": "USCIS Torch API not configured"}
    assert http.calls == []


# ---------------------------------------------------------------------------
# the H1B surface
# ---------------------------------------------------------------------------

def _create_case(client, **overrides):
    body = {"case_kind": "cap_initial", "beneficiary_full_name": "WEI ZHANG",
            "beneficiary_email": "wei.zhang@example.com",
            "beneficiary_abroad": True, "beneficiary_in_us": False,
            "first_h1b": True}
    body.update(overrides)
    r = client.post("/h1b/cases", json=body, headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()["case_id"]


def _set_receipt(db, case_id, step_key, receipt):
    step = db.execute(select(h1b_models.H1bCaseStep).where(
        h1b_models.H1bCaseStep.application_id == case_id,
        h1b_models.H1bCaseStep.step_key == step_key)).scalars().first()
    step.uscis_receipt_number = receipt
    db.commit()
    return step


def test_only_uscis_steps_are_tracked_and_receiptless_steps_say_so(
        status_client, torch_env):
    case_id = _create_case(status_client)
    r = status_client.get(f"/h1b/cases/{case_id}/status", headers=ADMIN_AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    # The LCA is the Labor Department's and the consular leg is State's.
    assert [s["step_key"] for s in body["steps"]] == ["registration", "i129"]
    for step in body["steps"]:
        assert step["tracked"] is False
        assert step["status"] == "no_receipt_yet"
        assert step["message"] == "no receipt yet"
        assert step["receipt_number"] is None
        assert step["is_real_government_result"] is False
    assert body["tracking"]["read_only"] is True
    assert body["attorney_disclaimer"]
    assert body["disclaimer_version"]


def test_step_with_receipt_returns_a_tracked_government_entry(
        status_client, db, torch_env, monkeypatch):
    case_id = _create_case(status_client)
    _set_receipt(db, case_id, "i129", VALID_RECEIPT)
    _live(torch_env)
    monkeypatch.setattr(torch_status, "_http_json",
                        _Http(_TOKEN_OK, (200, _APPROVED)))
    body = status_client.get(f"/h1b/cases/{case_id}/status",
                             headers=ADMIN_AUTH).json()
    i129 = next(s for s in body["steps"] if s["step_key"] == "i129")
    assert i129["tracked"] is True
    assert i129["receipt_number"] == VALID_RECEIPT
    assert i129["status"] == "Case Was Approved"
    assert i129["status_date"] == "2026-07-14"
    assert i129["live"] is True
    assert i129["is_real_government_result"] is True
    reg = next(s for s in body["steps"] if s["step_key"] == "registration")
    assert reg["status"] == "no_receipt_yet"


def test_unconfigured_tracking_is_never_a_real_government_result(
        status_client, db, torch_env, monkeypatch):
    case_id = _create_case(status_client)
    _set_receipt(db, case_id, "i129", VALID_RECEIPT)
    http = _Http()
    monkeypatch.setattr(torch_status, "_http_json", http)
    body = status_client.get(f"/h1b/cases/{case_id}/status",
                             headers=ADMIN_AUTH).json()
    i129 = next(s for s in body["steps"] if s["step_key"] == "i129")
    assert i129["live"] is False
    assert i129["status"] in torch_status.MARKER_STATUSES
    assert i129["is_real_government_result"] is False
    assert body["tracking"]["configured"] is False
    assert http.calls == []


def test_stored_garbage_receipt_is_reported_never_sent(
        status_client, db, torch_env, monkeypatch):
    case_id = _create_case(status_client)
    _set_receipt(db, case_id, "i129", "NOT-A-RECEIPT")
    _live(torch_env)
    http = _Http()
    monkeypatch.setattr(torch_status, "_http_json", http)
    body = status_client.get(f"/h1b/cases/{case_id}/status",
                             headers=ADMIN_AUTH).json()
    i129 = next(s for s in body["steps"] if s["step_key"] == "i129")
    assert i129["tracked"] is False
    assert i129["status"] == "invalid_receipt_number"
    assert i129["is_real_government_result"] is False
    assert http.calls == []


def test_receipt_is_masked_for_the_other_party(status_client, db, torch_env,
                                               monkeypatch):
    # AUTH created the case, so user1 operates the BENEFICIARY party; the I-129
    # is the petitioner's filing.
    case_id = _create_case(status_client)
    _set_receipt(db, case_id, "i129", VALID_RECEIPT)
    monkeypatch.setattr(torch_status, "_http_json", _Http())
    ben = status_client.get(f"/h1b/cases/{case_id}/status", headers=AUTH).json()
    i129 = next(s for s in ben["steps"] if s["step_key"] == "i129")
    assert i129["receipt_masked"] is True
    assert i129["receipt_number"] != VALID_RECEIPT
    assert i129["receipt_number"].startswith("IOE")
    assert i129["receipt_number"].endswith("5678")
    assert "*" in i129["receipt_number"]
    # The outcome itself stays visible to the beneficiary.
    assert i129["status"] == "tracking_unavailable"
    admin = status_client.get(f"/h1b/cases/{case_id}/status",
                              headers=ADMIN_AUTH).json()
    admin_i129 = next(s for s in admin["steps"] if s["step_key"] == "i129")
    assert admin_i129["receipt_number"] == VALID_RECEIPT
    assert admin_i129["receipt_masked"] is False


def test_cross_org_access_is_refused(status_client, torch_env):
    case_id = _create_case(status_client)
    assert status_client.get(f"/h1b/cases/{case_id}/status",
                             headers=AUTH2).status_code == 403
    assert status_client.get("/h1b/cases/does-not-exist/status",
                             headers=AUTH).status_code == 404
