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
    assert _scrub("Bearer sk-abcdefghijklmnop failed") == "[redacted] failed"
    assert "ak-" not in _scrub("key ak-fc4twmny7hci11a44if1 rejected")


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


def test_a_retry_after_header_is_capped_by_the_gate_maximum(monkeypatch):
    """A provider Retry-After of two minutes must not park every caller in
    the process for two minutes: the gate's cap bounds it."""
    monkeypatch.setattr(kp, "RATE_GATE_MAX_SECONDS", 30.0)
    pause = kp._rate_gate_hit(retry_after=120.0)
    assert pause == 30.0
    assert kp._RATE_GATE["until"] - kp.time.monotonic() <= 30.0
    kp._rate_gate_clear()
    assert kp._rate_gate_hit(retry_after=5.0) == 5.0


def test_a_paced_caller_never_holds_an_engine_slot_while_it_waits(monkeypatch):
    """Review finding on ops20260911a: the rate-gate wait ran inside the
    engine slot, so one paced caller kept the other readers out for the
    length of the pause. Every sleep now happens with the slot released."""
    import threading
    monkeypatch.setattr(kp, "RATE_GATE_BASE_SECONDS", 0.05)
    monkeypatch.setattr(kp, "_LIVE_SLOTS", threading.Semaphore(1))
    sleeps = []
    real_sleep = kp.time.sleep

    def watched_sleep(seconds):
        sleeps.append((seconds, kp._LIVE_SLOTS._value))
        real_sleep(min(seconds, 0.05))
    monkeypatch.setattr(kp.time, "sleep", watched_sleep)
    calls = {"n": 0}

    def limited_then_ok(system, user, json_mode=None, timeout=None, max_tokens=None, model=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise KimiHttpError(429, "rate_limit_reached_error", "slow down")
        return {"disposition": "VISA_REQUIRED", "confidence": "high"}

    class _P:
        _chat = staticmethod(limited_then_ok)
    import app.providers.kimi as kmod
    real_provider, real_settings = kmod.LiveKimiProvider, kp.settings
    kmod.LiveKimiProvider = lambda: _P()
    kp.settings = _fake_settings
    try:
        out = kp._live_call("s", "u", timeout=60, max_tokens=100)
    finally:
        kmod.LiveKimiProvider, kp.settings = real_provider, real_settings
    assert out["disposition"] == "VISA_REQUIRED" and calls["n"] == 2
    assert sleeps and all(free == 1 for _seconds, free in sleeps), sleeps
    assert kp._LIVE_SLOTS._value == 1, "the slot is released after the call"


def test_engine_saturation_for_the_whole_budget_is_the_rate_limit_answer(monkeypatch):
    """A caller that cannot get a slot before its own deadline is told the
    engine is busy instead of waiting past the deadline it promised."""
    import threading
    slots = threading.Semaphore(1)
    slots.acquire()
    monkeypatch.setattr(kp, "_LIVE_SLOTS", slots)
    import app.providers.kimi as kmod
    real_settings = kp.settings
    kp.settings = _fake_settings
    started = kp.time.monotonic()
    try:
        with pytest.raises(kp.GuidanceProviderError) as err:
            kp._live_call("s", "u", timeout=0.3, max_tokens=100)
    finally:
        kp.settings = real_settings
        slots.release()
    assert err.value.envelope["category"] == "kimi_rate_limited"
    assert kp.time.monotonic() - started < 2.0


def test_a_suspension_names_the_quota_category_and_raises_the_admin_alert(db):
    def dead(system, user, json_mode=None, timeout=None, max_tokens=None, model=None):
        raise KimiHttpError(429, "exceeded_current_quota_error",
                            "Your account is suspended due to insufficient balance, please recharge")

    class _P:
        _chat = staticmethod(dead)
    import app.providers.kimi as kmod
    real_provider, real_settings = kmod.LiveKimiProvider, kp.settings
    kmod.LiveKimiProvider = lambda: _P()
    kp.settings = _fake_settings
    try:
        with pytest.raises(kp.GuidanceProviderSuspended) as first:
            kp._live_call("s", "u", timeout=40, max_tokens=100)
        with pytest.raises(kp.GuidanceProviderSuspended) as second:
            kp._live_call("s", "u", timeout=40, max_tokens=100)
    finally:
        kmod.LiveKimiProvider, kp.settings = real_provider, real_settings
    for err in (first, second):
        assert err.value.envelope["category"] == "kimi_quota_exhausted"
        assert err.value.envelope["admin_alert"] is True
        assert err.value.envelope["provider_status"] == "quota_exhausted"
    assert kp._LIVE_SLOTS._value == kp._LIVE_SLOTS._initial_value if hasattr(kp._LIVE_SLOTS, "_initial_value") else True


def test_a_lapsed_cooldown_clears_its_since_stamp():
    kp.note_provider_suspension("insufficient balance", seconds=0.01)
    first_since = kp._PROVIDER_STATE["since"]
    assert first_since and kp.provider_suspension()["suspended"] is True
    kp.time.sleep(0.02)
    assert kp.provider_suspension() is None
    assert kp._PROVIDER_STATE["since"] == "" and kp._PROVIDER_STATE["reason"] == ""
    kp.time.sleep(0.01)
    kp.note_provider_suspension("suspended again", seconds=5)
    assert kp._PROVIDER_STATE["since"] >= first_since
    kp.clear_provider_suspension()


def test_env_floats_fall_back_when_unparseable(monkeypatch):
    monkeypatch.setenv("ELLIS_KIMI_RATE_GATE_MAX", "thirty")
    assert kp._env_float("ELLIS_KIMI_RATE_GATE_MAX", 30.0) == 30.0
    monkeypatch.setenv("ELLIS_KIMI_RATE_GATE_MAX", " ")
    assert kp._env_float("ELLIS_KIMI_RATE_GATE_MAX", 30.0) == 30.0
    monkeypatch.setenv("ELLIS_KIMI_RATE_GATE_MAX", "45")
    assert kp._env_float("ELLIS_KIMI_RATE_GATE_MAX", 30.0) == 45.0
    assert kp.rate_gate_saturated() is False
