"""A suspended provider account (out of balance) is not a rate limit.

On 10 to 11 September 2026 the Moonshot account ran dry: every call returned
HTTP 429 with ``exceeded_current_quota_error`` and the freshness sweep burned
whole cycles (5,702 failed calls in 24 hours, zero verifications) while the
QC Freshness tab showed nothing but provider failures. These tests pin the
circuit breaker: one suspension notice stops the process from calling again
for a cooldown, the sweep stops its cycle and names the reason, and the
provider's message never carries a key.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.providers.kimi import KimiHttpError, _scrub
from app.visa_snapshot import kimi_primary as kp


@pytest.fixture(autouse=True)
def _clean_breaker():
    kp.clear_provider_suspension()
    kp._rate_gate_clear()
    yield
    kp.clear_provider_suspension()
    kp._rate_gate_clear()


def _fake_settings():
    return type("S", (), {"moonshot_api_key": "k", "kimi_enabled": True,
                          "runtime_mode": "local_real_services"})()


def test_provider_message_is_scrubbed_of_key_like_tokens():
    e = KimiHttpError(429, "exceeded_current_quota_error",
                      "Your account org-71b6 <ak-fc4twmny7hci11a44if1> is suspended due to insufficient balance")
    assert "ak-" not in e.message and "insufficient balance" in e.message
    assert e.account_suspended
    assert not KimiHttpError(429).account_suspended
    assert not KimiHttpError(500, "server_error", "try again").account_suspended
    assert "bearer <redacted>" in _scrub("Bearer sk-abcdefghijklmnop failed")


def test_a_suspended_account_trips_the_breaker_and_stops_further_calls(db):
    calls = {"n": 0}

    def dead(system, user, json_mode=None, timeout=None, max_tokens=None, model=None):
        calls["n"] += 1
        raise KimiHttpError(429, "exceeded_current_quota_error",
                            "Your account <ak-secret1234567> is suspended due to insufficient balance, please recharge")

    class _P:
        _chat = staticmethod(dead)
    import app.providers.kimi as kmod
    real_provider, real_settings = kmod.LiveKimiProvider, kp.settings
    kmod.LiveKimiProvider = lambda: _P()
    kp.settings = _fake_settings
    try:
        with pytest.raises(kp.GuidanceProviderSuspended) as first:
            kp._live_call("s", "u", timeout=40, max_tokens=100)
        assert calls["n"] == 1, "a quota error is never retried"
        assert "ak-" not in first.value.reason and "insufficient balance" in first.value.reason
        active = kp.provider_suspension()
        assert active and active["suspended"] and active["retry_in_seconds"] > 0
        assert "ak-" not in json.dumps(kp.provider_status())
        with pytest.raises(kp.GuidanceProviderSuspended):
            kp._live_call("s", "u", timeout=40, max_tokens=100)
        assert calls["n"] == 1, "while the cooldown runs no network call is made"
        kp.clear_provider_suspension()
        assert kp.provider_status() == {"suspended": False}
        with pytest.raises(kp.GuidanceProviderSuspended):
            kp._live_call("s", "u", timeout=40, max_tokens=100)
        assert calls["n"] == 2, "after the cooldown one call probes the account again"
    finally:
        kmod.LiveKimiProvider, kp.settings = real_provider, real_settings


def test_a_plain_rate_limit_still_retries_and_never_trips_the_breaker(db):
    calls = {"n": 0}

    def flaky(system, user, json_mode=None, timeout=None, max_tokens=None, model=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise KimiHttpError(429, "rate_limit_reached_error", "slow down")
        return {"disposition": "VISA_REQUIRED", "confidence": "high"}

    class _P:
        _chat = staticmethod(flaky)
    import app.providers.kimi as kmod
    real_provider, real_settings = kmod.LiveKimiProvider, kp.settings
    kmod.LiveKimiProvider = lambda: _P()
    kp.settings = _fake_settings
    try:
        out = kp._live_call("s", "u", timeout=40, max_tokens=100)
    finally:
        kmod.LiveKimiProvider, kp.settings = real_provider, real_settings
    assert out["disposition"] == "VISA_REQUIRED" and calls["n"] == 2
    assert kp.provider_suspension() is None


def test_a_rate_limit_paces_every_caller_and_a_success_resets_the_gate(db, monkeypatch):
    """One 429 opens a process-wide pause that grows on repeats (2 s, 4 s,
    capped) and closes on the next success, so four workers stop hammering
    an account whose limit is per account."""
    monkeypatch.setattr(kp, "RATE_GATE_BASE_SECONDS", 0.05)
    hits = []
    real_hit = kp._rate_gate_hit
    monkeypatch.setattr(kp, "_rate_gate_hit", lambda retry_after=None: hits.append(real_hit(retry_after)) or hits[-1])
    calls = {"n": 0}

    def limited_then_ok(system, user, json_mode=None, timeout=None, max_tokens=None, model=None):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise KimiHttpError(429, "rate_limit_reached_error", "slow down", retry_after=None)
        return {"disposition": "VISA_REQUIRED", "confidence": "high"}

    class _P:
        _chat = staticmethod(limited_then_ok)
    import app.providers.kimi as kmod
    real_provider, real_settings = kmod.LiveKimiProvider, kp.settings
    kmod.LiveKimiProvider = lambda: _P()
    kp.settings = _fake_settings
    try:
        out = kp._live_call("s", "u", timeout=120, max_tokens=100)
    finally:
        kmod.LiveKimiProvider, kp.settings = real_provider, real_settings
    assert out["disposition"] == "VISA_REQUIRED" and calls["n"] == 3
    assert len(hits) == 2 and hits[1] > hits[0], "the second rejection doubled the pause"
    assert kp._RATE_GATE["backoff"] == 0.0 and kp._RATE_GATE["until"] == 0.0, "a success resets the gate"
    assert kp.provider_status() == {"suspended": False}
    # Another caller arriving while the gate is closed waits, bounded by its own deadline.
    kp._rate_gate_hit(retry_after=5.0)
    assert kp.provider_status()["rate_limited_for_seconds"] >= 1
    assert kp._rate_gate_wait(deadline=kp.time.monotonic() + 2.0) is False, "a caller that cannot afford the wait times out honestly"
