"""A cached response cannot wait for the source check it schedules."""
import threading
from types import SimpleNamespace

import pytest

from app.visa_snapshot import freshness, kimi_primary as kp

ROUTE = {"passport_nationality": "USA", "lawful_country_of_residence": "USA",
         "destination_country": "NPL", "travel_purpose": "tourism",
         "travel_document_type": "ordinary_passport", "visa_category": "tourist_visa"}


class Session:
    def __init__(self):
        self.closed = False

    def execute(self, statement):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: object()))

    def close(self):
        self.closed = True


def test_worker_is_nonblocking_deduplicated_and_owns_session_and_route(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    main_thread = threading.get_ident()
    session = Session()
    calls = []
    route = dict(ROUTE, transit_countries=["IND"])

    def factory():
        assert threading.get_ident() != main_thread
        entered.set()
        assert release.wait(3)
        return session

    def recheck(db, copied):
        assert db is session
        calls.append(copied)
        return {"outcome": "checked"}

    monkeypatch.setattr(freshness, "recheck_route", recheck)
    worker = kp.refresh_stale_async(factory, route)
    try:
        assert worker is not None and entered.wait(1)
        assert worker.is_alive(), "session creation is still waiting inside the worker"
        assert kp.refresh_stale_async(factory, route) is None
        route["transit_countries"].append("THA")
    finally:
        release.set()
        if worker is not None:
            worker.join(3)
    assert not worker.is_alive() and session.closed
    assert len(calls) == 1 and calls[0]["transit_countries"] == ["IND"]
    assert kp.cache_key(route) not in kp._REFRESH_IN_FLIGHT


def test_completed_source_worker_commits_update_visible_to_next_cached_read(db, monkeypatch):
    from copy import deepcopy
    from datetime import datetime, timedelta, timezone
    from app.db import SessionLocal
    from app.visa_snapshot.models import KimiRouteGuidanceCache
    key = kp.cache_key(ROUTE)
    previous = db.query(KimiRouteGuidanceCache).filter_by(cache_key=key).first()
    saved = {column.name: deepcopy(getattr(previous, column.name))
             for column in KimiRouteGuidanceCache.__table__.columns} if previous else None
    db.query(KimiRouteGuidanceCache).filter_by(cache_key=key).delete()
    at = datetime.now(timezone.utc)
    row = KimiRouteGuidanceCache(cache_key=key, route=dict(ROUTE), status="KIMI_PRIMARY",
        guidance={"processing_time": "Previous published time"}, missing_fields=[], contradictions=[],
        model="fixture", verification={}, generated_at=at - timedelta(days=2), fresh_until=at - timedelta(days=1))
    db.add(row)
    db.commit()
    entered, release = threading.Event(), threading.Event()
    def source_check(session, route):
        entered.set()
        assert release.wait(3)
        current = session.query(KimiRouteGuidanceCache).filter_by(cache_key=key).one()
        current.guidance = {"processing_time": "Source-confirmed updated time"}
        session.commit()
        return {"outcome": "checked"}
    monkeypatch.setattr(freshness, "recheck_route", source_check)
    worker = kp.refresh_stale_async(SessionLocal, ROUTE)
    try:
        try:
            assert worker is not None and entered.wait(1)
            db.expire_all()
            assert kp._cached(db, key).guidance["processing_time"] == "Previous published time"
        finally:
            release.set()
            if worker is not None:
                worker.join(3)
        assert not worker.is_alive()
        db.expire_all()
        assert kp._cached(db, key).guidance["processing_time"] == "Source-confirmed updated time"
    finally:
        db.query(KimiRouteGuidanceCache).filter_by(cache_key=key).delete()
        if saved:
            db.add(KimiRouteGuidanceCache(**saved))
        db.commit()


@pytest.mark.parametrize("failure", ["factory", "execute", "recheck", "note", "close"])
def test_worker_failures_release_key_and_close_created_session(monkeypatch, failure):
    session = Session()
    def fail(*args, **kwargs):
        raise RuntimeError("fixture failure")
    if failure == "execute":
        session.execute = fail
    if failure == "close":
        def failed_close():
            session.closed = True
            fail()
        session.close = failed_close
    monkeypatch.setattr(freshness, "recheck_route", fail if failure == "recheck" else lambda *a: None)
    monkeypatch.setattr(freshness, "note_unreadable", fail if failure == "note" else lambda *a: None)
    worker = kp.refresh_stale_async(fail if failure == "factory" else lambda: session, ROUTE)
    assert worker is not None
    worker.join(3)
    assert not worker.is_alive()
    assert kp.cache_key(ROUTE) not in kp._REFRESH_IN_FLIGHT
    assert session.closed is (failure != "factory")


@pytest.mark.parametrize("failure", ["construction", "start"])
def test_failed_thread_scheduling_does_not_leave_route_inflight(monkeypatch, failure):
    def broken_thread(*args, **kwargs):
        if failure == "construction":
            raise RuntimeError("fixture thread allocation failure")
        return SimpleNamespace(start=lambda: (_ for _ in ()).throw(RuntimeError("fixture start failure")))
    monkeypatch.setattr(kp.threading, "Thread", broken_thread)
    assert kp.refresh_stale_async(lambda: pytest.fail("factory must stay in worker"), ROUTE) is None
    assert kp.cache_key(ROUTE) not in kp._REFRESH_IN_FLIGHT


def _lookup_fixture(monkeypatch):
    from app import main, config, db as db_module
    from app.visa_snapshot import special_policies
    monkeypatch.setattr(config, "settings", lambda: SimpleNamespace(runtime_mode="live"))
    monkeypatch.setenv("ELLIS_BACKGROUND_RENEWAL", "1")
    monkeypatch.setattr(kp, "is_available", lambda: True)
    monkeypatch.setattr(kp, "_cached", lambda *args: object())
    answer = {"cached": True, "stale": True, "guidance": {"disposition": "VISA_REQUIRED"},
              "held": False, "status": "KIMI_PRIMARY"}
    monkeypatch.setattr(kp, "get_route_guidance", lambda *args, **kwargs: dict(answer))
    monkeypatch.setattr(main, "_apply_records_hold", lambda route, out, db: out)
    monkeypatch.setattr(main.audit, "record", lambda *args, **kwargs: None)
    monkeypatch.setattr(special_policies, "attach", lambda out, **kwargs: out)
    monkeypatch.setattr(db_module, "SessionLocal", Session)
    def lookup():
        return main.travel_database_lookup(main.DatabaseLookupIn(nationality="USA", destination="NPL"),
            db=object(), p=SimpleNamespace(org_id="fixture", user_id="fixture"))
    return main, lookup


def test_actual_cached_lookup_returns_while_source_check_is_blocked(monkeypatch):
    main, lookup = _lookup_fixture(monkeypatch)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    checks, output, workers = [], [], []
    original = kp.refresh_stale_async
    def schedule(*args, **kwargs):
        worker = original(*args, **kwargs)
        if worker is not None:
            workers.append(worker)
        return worker
    monkeypatch.setattr(kp, "refresh_stale_async", schedule)
    def recheck(db, route):
        checks.append(route)
        entered.set()
        assert release.wait(3)
        return {"outcome": "checked"}
    monkeypatch.setattr(freshness, "recheck_route", recheck)
    def request():
        try:
            output.append(lookup())
        finally:
            finished.set()
    request_thread = threading.Thread(target=request)
    request_thread.start()
    try:
        assert entered.wait(1)
        assert finished.wait(1), "the cached handler must finish before the source check is released"
        assert len(checks) == 1
        assert output[0]["held"] is False
        assert output[0]["guidance"] == {"disposition": "VISA_REQUIRED"}
        # A second cached request while the first source check runs shares it.
        assert lookup()["cached"] is True
        assert len(checks) == 1 and len(workers) == 1
    finally:
        release.set()
        request_thread.join(3)
        for worker in workers:
            worker.join(3)


def test_stale_and_ground_due_lookup_dispatches_only_once_even_if_worker_finishes(monkeypatch):
    main, lookup = _lookup_fixture(monkeypatch)
    dispatched = []
    monkeypatch.setattr(kp, "refresh_stale_async", lambda *args: dispatched.append(args))
    assert lookup()["cached"] is True
    assert len(dispatched) == 1
    # An unexpired grounding does not suppress refresh of an actually stale row.
    monkeypatch.setattr(main, "should_reground", lambda out: False)
    assert lookup()["cached"] is True
    assert len(dispatched) == 2


@pytest.mark.parametrize("mode,enabled", [("test", "1"), ("live", "0")])
def test_background_switch_prevents_source_dispatch(monkeypatch, mode, enabled):
    from app import config
    main, lookup = _lookup_fixture(monkeypatch)
    monkeypatch.setattr(config, "settings", lambda: SimpleNamespace(runtime_mode=mode))
    monkeypatch.setenv("ELLIS_BACKGROUND_RENEWAL", enabled)
    monkeypatch.setattr(kp, "refresh_stale_async", lambda *args: pytest.fail("background dispatch disabled"))
    assert lookup()["cached"] is True
