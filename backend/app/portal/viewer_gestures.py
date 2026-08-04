"""Viewer scroll relay for the watch-only live view.

While Ellis drives a portal session, the applicant's live view is a click
shield: nothing they do may change portal state under the runner. Scrolling is
the one exception — it is VIEW-ONLY assistance. The browser cannot pass a
wheel through the shield without also passing clicks (scroll gestures latch to
the element they start on), so the wheel deltas are forwarded here and applied
to the case's own Browserbase session as a plain document scroll.

Strictly read-only by construction: the only page call is scrollBy on the
document — never a click, never a keystroke, never a form value.

One daemon thread owns a sync Playwright instance and a small pool of CDP
connections (sync Playwright objects are thread-bound; FastAPI request threads
must never touch them). Requests enqueue deltas and return immediately;
the worker coalesces bursts per case and drops connections idle for a minute.
"""
from __future__ import annotations

import queue
import threading
import time

_QUEUE: "queue.Queue[tuple[str, str, float]]" = queue.Queue(maxsize=200)
_WORKER: threading.Thread | None = None
_LOCK = threading.Lock()

# session_connect_info is one provider HTTP call — cached per session so a
# scroll burst does not hammer the provider API. The connect URL is stable
# for the lifetime of a session.
_URL_CACHE: dict[str, tuple[str, float]] = {}
_URL_TTL_S = 60.0

_SCROLL_JS = ("(dy) => { (document.scrollingElement || document.documentElement)"
              ".scrollBy({top: dy, left: 0, behavior: 'instant'}); }")


def connect_url_for(session_id: str) -> str:
    """Provider connect URL for a live session, cached briefly. Raises
    RuntimeError (from the provider helper) when the session is gone."""
    hit = _URL_CACHE.get(session_id)
    now = time.time()
    if hit and now - hit[1] < _URL_TTL_S:
        return hit[0]
    from ..providers import browser as bb
    info = bb.session_connect_info(session_id)
    url = str(info.get("connect_url") or "")
    if not url:
        raise RuntimeError("session has no connect URL")
    _URL_CACHE[session_id] = (url, now)
    return url


def enqueue_scroll(app_id: str, connect_url: str, delta_y: float) -> bool:
    """Queue a scroll for the worker. Never blocks; a full queue drops the
    delta (the applicant just wheels again)."""
    global _WORKER
    with _LOCK:
        if _WORKER is None or not _WORKER.is_alive():
            _WORKER = threading.Thread(target=_run, name="viewer-scroll", daemon=True)
            _WORKER.start()
    try:
        _QUEUE.put_nowait((str(app_id), str(connect_url), float(delta_y)))
        return True
    except queue.Full:
        return False


def _close(conns: dict, app_id: str) -> None:
    c = conns.pop(app_id, None)
    if c is not None:
        try:
            c["browser"].close()
        except Exception:  # noqa: BLE001
            pass


def _run() -> None:
    from playwright.sync_api import sync_playwright
    conns: dict[str, dict] = {}
    with sync_playwright() as pw:
        while True:
            try:
                app_id, url, dy = _QUEUE.get(timeout=30)
            except queue.Empty:
                for k in list(conns):
                    if time.time() - conns[k]["last"] > 60:
                        _close(conns, k)
                continue
            # Coalesce the burst: drain queued deltas for the same case.
            while True:
                try:
                    n_app, n_url, n_dy = _QUEUE.get_nowait()
                except queue.Empty:
                    break
                if n_app == app_id and n_url == url:
                    dy += n_dy
                else:
                    try:
                        _QUEUE.put_nowait((n_app, n_url, n_dy))
                    except queue.Full:
                        pass
                    break
            if not dy:
                continue
            c = conns.get(app_id)
            if c is not None and c["url"] != url:
                _close(conns, app_id)
                c = None
            if c is None:
                try:
                    browser = pw.chromium.connect_over_cdp(url, timeout=8000)
                except Exception:  # noqa: BLE001 — session gone; drop the delta
                    continue
                c = conns[app_id] = {"browser": browser, "url": url, "last": 0.0}
            try:
                ctx = c["browser"].contexts[0]
                page = ctx.pages[-1] if ctx.pages else None
                if page is not None:
                    page.evaluate(_SCROLL_JS, max(-4000.0, min(4000.0, dy)))
                    c["last"] = time.time()
            except Exception:  # noqa: BLE001 — stale connection: rebuild next time
                _close(conns, app_id)
