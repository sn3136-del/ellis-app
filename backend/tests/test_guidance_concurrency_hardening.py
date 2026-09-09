"""Bounded deterministic regressions for route lock and staged detail races."""
from types import SimpleNamespace
import threading
from weakref import WeakValueDictionary

import pytest

from app.visa_snapshot import kimi_primary as kp

ROUTE = {"passport_nationality": "HKG", "destination_country": "VNM",
         "travel_purpose": "tourism", "travel_document_type": "ordinary_passport"}


def test_waiting_request_keeps_same_lock_after_owner_releases(monkeypatch):
    """Pause the waiter before acquire: removing an unlocked registry entry in
    this exact window would let a third request start an independent decision."""
    parked, release_waiter = threading.Event(), threading.Event()
    entered, release_owner = threading.Event(), threading.Event()
    errors = []

    class ParkedLock:
        def __init__(self):
            self.lock = threading.Lock()
        def acquire(self, timeout):
            if threading.current_thread().name == "second":
                parked.set()
                assert release_waiter.wait(2)
            return self.lock.acquire(timeout=timeout)
        def release(self):
            self.lock.release()
        def locked(self):
            return self.lock.locked()

    key = kp.cache_key(ROUTE)
    lock = ParkedLock()
    monkeypatch.setattr(kp, "_INFLIGHT", WeakValueDictionary({key: lock}))
    monkeypatch.setattr(kp, "_cached", lambda *_: None)
    def generate(*_a, **_k):
        if threading.current_thread().name == "first":
            entered.set()
            assert release_owner.wait(2)
        return {"ok": True}
    monkeypatch.setattr(kp, "_get_route_guidance_locked", generate)
    db = SimpleNamespace(expire_all=lambda: None)
    def run():
        try:
            kp.get_route_guidance(db, ROUTE)
        except BaseException as e:
            errors.append(e)
    first = threading.Thread(target=run, name="first")
    second = threading.Thread(target=run, name="second")
    first.start()
    try:
        assert entered.wait(2)
        second.start()
        assert parked.wait(2)
        release_owner.set()
        first.join(2)
        assert not first.is_alive()
        assert kp._INFLIGHT.get(key) is lock
    finally:
        release_owner.set(); release_waiter.set()
        first.join(2)
        if second.ident is not None:
            second.join(2)
    assert not errors


def test_detail_join_waits_for_only_requested_route(monkeypatch):
    joined = []
    class Detail:
        def __init__(self, key):
            self.ellis_route_key, self.alive = key, True
        def join(self, timeout):
            joined.append((self.ellis_route_key, timeout))
            self.alive = False
        def is_alive(self):
            return self.alive
    ours, other = Detail("ours"), Detail("other")
    monkeypatch.setattr(kp, "_DETAIL_THREADS", [ours, other])
    assert kp.join_detail_stage(timeout=0.1, key="ours")
    assert [key for key, _ in joined] == ["ours"]
    assert kp._DETAIL_THREADS == [other]
    assert joined[0][1] <= 0.1


@pytest.mark.parametrize("completes", [True, False])
def test_full_cached_request_waits_for_own_detail_or_reports_pending(monkeypatch, completes):
    key = kp.cache_key(ROUTE)
    row = SimpleNamespace(status=kp.STATUS_PRIMARY, guidance={"disposition": "VISA_REQUIRED"},
                          missing_fields=[], contradictions=[], model="test",
                          verification={"detail_pending": True})
    joined = []
    monkeypatch.setattr(kp, "_cached", lambda *_: row)
    monkeypatch.setattr(kp, "served_guidance", lambda r: r.guidance)
    monkeypatch.setattr(kp, "_is_stale", lambda _: False)
    monkeypatch.setattr(kp, "deterministic_advisories", lambda *_: [])
    monkeypatch.setattr(kp, "_result", lambda *a, **_k: {"guidance": a[1]})
    monkeypatch.setattr(kp, "apply_portal_fallback", lambda out, _: out)
    monkeypatch.setattr(kp, "apply_verified_overrides", lambda out, _: out)
    def join(timeout, *, key):
        joined.append(key)
        if completes:
            row.verification = {}
        return completes
    monkeypatch.setattr(kp, "join_detail_stage", join)
    out = kp.get_route_guidance(SimpleNamespace(expire_all=lambda: None), ROUTE, stage="full")
    assert joined == [key]
    assert out["detail_pending"] is (not completes)
