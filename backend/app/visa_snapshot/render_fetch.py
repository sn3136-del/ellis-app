"""Browserbase-backed render fallback for JS-rendered official portals (brief §8
"JavaScript-rendered retrieval through Browserbase").

Some official portals (e.g. inm.gob.mx) serve an empty shell to a plain GET and
only populate content via JavaScript. When a plain fetch fails or returns no
extractable text, the research fetch layer makes ONE render attempt through this
module: an isolated Browserbase session drives a real headless browser (over CDP
with Playwright), navigates the PUBLIC page, and returns the rendered text as an
ordinary FetchResult — which is then verified/grounded downstream exactly like a
plain fetch (government-domain check + cited-source grounding). Nothing is
fabricated: if Browserbase is not configured, or the browser driver is not
available, this module is honestly absent and the plain-fetch failure stands.

Credential-free by construction: it only navigates and reads document text; it
never authenticates, fills a field, or reads storage/cookies. Anti-bot/CAPTCHA
is never bypassed — a challenged page simply yields no usable text and stays a
recorded failure.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from ..config import settings
from ..providers import browser as bb
from .fetching import FetchResult, _detect_lang, html_to_text


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _playwright_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except Exception:  # noqa: BLE001 - optional heavy dependency
        return False


def is_available() -> bool:
    """A render fallback is only offered when Browserbase is configured AND a
    browser driver is importable. Otherwise honestly unavailable."""
    return bool(bb.is_configured() and _playwright_available())


def browserbase_render_fetch(url: str, *, timeout_seconds: float = 20.0) -> FetchResult:
    """Render `url` in an isolated Browserbase session and return its text.
    Any failure returns an honest failed FetchResult (never fabricated)."""
    if not is_available():
        return FetchResult(requested_url=url, ok=False, retrieved_at=_now(),
                           error="render fallback unavailable (browserbase/playwright not configured)")
    from playwright.sync_api import sync_playwright  # lazy: heavy import
    session = None
    try:
        session = bb.create_session()
        connect_url = session.get("connect_url")
        if not connect_url:
            return FetchResult(requested_url=url, ok=False, retrieved_at=_now(),
                               error="render fallback: no live session connect_url")
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(connect_url,
                                                   timeout=timeout_seconds * 1000)
            try:
                ctx = browser.contexts[0] if browser.contexts else browser.new_context()
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                resp = page.goto(url, wait_until="networkidle",
                                 timeout=timeout_seconds * 1000)
                html = page.content()
                final_url = page.url
                status = resp.status if resp else None
            finally:
                browser.close()
        text = html_to_text(html or "")
        host = (final_url.split("/")[2] if "//" in final_url else "").lower()
        return FetchResult(
            requested_url=url, ok=bool(text), final_url=final_url,
            redirect_chain=[final_url] if final_url != url else [],
            final_hostname=host, http_status=status, content_text=text,
            content_hash=hashlib.sha256((html or "").encode("utf-8", "ignore")).hexdigest(),
            page_language=_detect_lang(text), retrieved_at=_now(),
            error="" if text else "render produced no extractable text")
    except Exception as e:  # noqa: BLE001 - honest failure
        return FetchResult(requested_url=url, ok=False, retrieved_at=_now(),
                           error=f"render fallback error: {str(e)[:200]}")
    finally:
        if session and session.get("id"):
            try:
                bb.close_session(session["id"])
            except Exception:  # noqa: BLE001
                pass


def default_render_fetcher():
    """The render fetcher `fetching.fetch()` falls back to, or None when a render
    fallback is not available (honest — plain-fetch failures then stand)."""
    return browserbase_render_fetch if is_available() else None
