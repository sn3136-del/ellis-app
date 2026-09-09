"""Real wall-clock deadlines survive clients that ignore their own timeout."""
import threading
import time
import pytest
from app.visa_snapshot import bounded_io, fetching, freshness
from app.visa_snapshot.fetching import FetchResult


def test_timed_out_call_keeps_capacity_until_underlying_daemon_exits():
    slots = threading.BoundedSemaphore(1)
    release = threading.Event()
    entered = threading.Event()
    daemon = []
    def hung():
        daemon.append(threading.current_thread().daemon)
        entered.set(); release.wait(2)
    try:
        start = time.monotonic()
        with pytest.raises(TimeoutError):
            bounded_io.call(hung, 0.03, slots)
        assert entered.is_set() and daemon == [True] and time.monotonic() - start < 0.5
        with pytest.raises(TimeoutError):
            bounded_io.call(lambda: pytest.fail('timed-out worker must still own the slot'), 0.02, slots)
    finally:
        release.set()
    assert slots.acquire(timeout=1)
    slots.release()


def test_plain_plus_renderer_share_one_total_deadline(monkeypatch):
    release = threading.Event()
    entered = threading.Event()
    monkeypatch.setattr(fetching, '_FETCHER', None)
    monkeypatch.setattr(fetching, '_IO_SLOTS', threading.BoundedSemaphore(1))
    monkeypatch.setattr(fetching, '_default_fetch', lambda url, **_: FetchResult(requested_url=url, ok=False))
    def render(url, timeout_seconds):
        assert timeout_seconds <= 0.04
        entered.set(); release.wait(2)
        return FetchResult(requested_url=url, ok=True, content_text='late result must not be returned')
    monkeypatch.setattr(fetching, '_RENDER_FETCHER', render)
    try:
        start = time.monotonic()
        result = fetching.fetch('https://www.mofa.go.jp/visa', timeout_seconds=20, total_timeout_seconds=0.04)
        assert entered.is_set() and not result.ok and 'deadline' in result.error
        assert time.monotonic() - start < 0.5
    finally:
        release.set()


def test_freshness_model_total_deadline_does_not_wait_for_ignored_provider_timeout(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(freshness, '_MODEL_SLOTS', threading.BoundedSemaphore(1))
    monkeypatch.setattr(freshness, '_PROVIDER', lambda *_: release.wait(2))
    try:
        start = time.monotonic()
        with pytest.raises(TimeoutError):
            freshness._call('fixture', 'fixture', timeout_seconds=0.03)
        assert time.monotonic() - start < 0.5
    finally:
        release.set()
