"""Official-source fetching layer for on-demand route research.

Plain HTTP fetches of official pages with full evidence recording (redirect
chain, final hostname, content hash, retrieval time). NO anti-bot bypass: a
blocked/failed fetch is recorded honestly as a failure and never guessed
around. Injectable for tests via set_fetcher().
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class FetchResult:
    requested_url: str
    ok: bool
    final_url: str = ""
    redirect_chain: list = field(default_factory=list)
    final_hostname: str = ""
    http_status: int | None = None
    content_text: str = ""          # extracted text, capped
    content_hash: str = ""
    page_language: str = ""
    retrieved_at: str = ""
    error: str = ""
    links: list = field(default_factory=list)   # absolute http(s) links on the page
    challenge: bool = False                      # anti-bot / JS-shell detected


_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_HTML_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_HREF_RE = re.compile(r"""<a\b[^>]*\bhref\s*=\s*["']([^"'#>]+)["']""", re.I)

MAX_TEXT_CHARS = 40_000

# A 200 response whose body is one of these (or is a tiny shell) is an anti-bot
# / JS-render challenge, NOT the official content — it must route to the render
# fallback, never be stored as a "source". No challenge is ever bypassed; a real
# browser (Browserbase) simply renders the same public page.
_CHALLENGE_RE = re.compile(
    r"verifying your browser|checking your browser|just a moment|enable javascript|"
    r"please enable js|radware|perfdrive|incident id|cf-browser-verification|"
    r"attention required|access denied|请开启 ?javascript|正在验证|人机验证|"
    r"unsupported browser|ddos|bot ?detection|captcha-delivery", re.I)
_MIN_REAL_TEXT = 120


def html_to_text(html: str) -> str:
    text = _TAG_RE.sub(" ", html)
    text = _HTML_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text)
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)[:MAX_TEXT_CHARS]


def _is_challenge(text: str, status: int | None) -> bool:
    if status == 200 and len(text.strip()) < _MIN_REAL_TEXT:
        return True
    return bool(_CHALLENGE_RE.search(text[:1500]))


def extract_links(html: str, base_url: str, *, max_links: int = 80) -> list[str]:
    """Absolute http(s) links on the page (for bounded internal-link following)."""
    from urllib.parse import urljoin, urlparse
    out, seen = [], set()
    for m in _HREF_RE.finditer(html or ""):
        raw = m.group(1).strip()
        if not raw or raw.lower().startswith(("javascript:", "mailto:", "tel:")):
            continue
        try:
            u = urljoin(base_url, raw)
        except Exception:  # noqa: BLE001
            continue
        if urlparse(u).scheme not in ("http", "https"):
            continue
        u = u.split("#")[0]
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
        if len(out) >= max_links:
            break
    return out


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _detect_lang(text: str) -> str:
    """Cheap script-based hint (not authoritative)."""
    sample = text[:2000]
    if re.search(r"[一-鿿]", sample):
        return "zh"
    if re.search(r"[؀-ۿ]", sample):
        return "ar"
    if re.search(r"[Ѐ-ӿ]", sample):
        return "ru"
    if re.search(r"[฀-๿]", sample):
        return "th"
    return "en" if sample else ""


def _default_fetch(url: str, *, timeout_seconds: float = 20.0) -> FetchResult:
    import httpx
    # A browser-like UA reduces spurious anti-bot challenges on official sites;
    # a genuine challenge is still detected and routed to the render fallback.
    ua = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/125.0 Safari/537.36 "
          "EllisVisaResearch/1.0 (+official-source verification)")
    chain: list[str] = []
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout_seconds,
                          headers={"User-Agent": ua, "Accept-Language": "en,zh;q=0.8,es;q=0.7"}) as c:
            r = c.get(url)
        chain = [str(h.url) for h in r.history] + ([str(r.url)] if r.history else [])
        text = html_to_text(r.text or "")
        challenge = _is_challenge(text, r.status_code)
        return FetchResult(
            requested_url=url, ok=r.status_code == 200 and bool(text) and not challenge,
            final_url=str(r.url), redirect_chain=chain,
            final_hostname=(r.url.host or "").lower(), http_status=r.status_code,
            content_text=text,
            content_hash=hashlib.sha256((r.text or "").encode("utf-8", "ignore")).hexdigest(),
            page_language=_detect_lang(text), retrieved_at=_now(),
            links=extract_links(r.text or "", str(r.url)),
            challenge=challenge,
            error=("anti-bot/JS challenge shell" if challenge
                   else "" if r.status_code == 200 else f"http {r.status_code}"))
    except Exception as e:  # noqa: BLE001 - recorded honestly, never guessed around
        return FetchResult(requested_url=url, ok=False, redirect_chain=chain,
                           retrieved_at=_now(), error=str(e)[:300])


_FETCHER = None

# Optional render fallback for JS-rendered official portals (e.g. inm.gob.mx
# serves an empty shell to a plain GET). callable(url, timeout_seconds=...) ->
# FetchResult. Registered by the real Browserbase renderer when configured;
# injectable in tests. Absent -> a failed/empty plain fetch stays an honest
# failure (never fabricated around).
_RENDER_FETCHER = None


def set_fetcher(fn) -> None:
    """Inject callable(url, timeout_seconds=...) -> FetchResult (tests). None resets."""
    global _FETCHER
    _FETCHER = fn


def set_render_fetcher(fn) -> None:
    """Inject a JS-render fallback fetcher (Browserbase-backed in prod, fake in
    tests). callable(url, timeout_seconds=...) -> FetchResult. None resets."""
    global _RENDER_FETCHER
    _RENDER_FETCHER = fn


def _render_fetcher():
    """The render fallback to use: an injected one wins; otherwise the real
    Browserbase renderer when it is configured + available; otherwise None."""
    if _RENDER_FETCHER is not None:
        return _RENDER_FETCHER
    try:
        from .render_fetch import default_render_fetcher
        return default_render_fetcher()
    except Exception:  # noqa: BLE001 - render fallback is best-effort, never required
        return None


def fetch(url: str, *, timeout_seconds: float = 20.0) -> FetchResult:
    if _FETCHER is not None:
        return _FETCHER(url, timeout_seconds=timeout_seconds)
    res = _default_fetch(url, timeout_seconds=timeout_seconds)
    # A blocked or JS-empty official page (200 but no extractable text, or a
    # transport error) gets ONE render attempt when a renderer is available. The
    # rendered result is verified/grounded downstream exactly like a plain fetch.
    if not res.ok:
        rf = _render_fetcher()
        if rf is not None:
            try:
                rendered = rf(url, timeout_seconds=timeout_seconds)
            except Exception as e:  # noqa: BLE001 - honest failure, never fabricated
                return res if res.error else FetchResult(
                    requested_url=url, ok=False, retrieved_at=_now(),
                    error=f"render fallback failed: {str(e)[:200]}")
            if rendered.ok:
                return rendered
    return res


# --- Search provider (query -> candidate URLs). Never fabricates: candidates
# only become evidence after a successful fetch + domain verification. ---------

_SEARCH = None


def set_search_provider(fn) -> None:
    """Inject callable(query: str) -> list[str] of candidate URLs. None resets."""
    global _SEARCH
    _SEARCH = fn


class SearchUnavailable(Exception):
    pass


def search(query: str) -> list[str]:
    if _SEARCH is not None:
        return list(_SEARCH(query) or [])
    # No injected provider: fall back to the real, controlled discovery provider
    # (Kimi K3 proposes candidate official URLs; they are verified + grounded
    # downstream). Only active in real runtime modes with a configured Kimi key.
    try:
        from .source_discovery import default_search_provider
        prov = default_search_provider()
    except Exception:  # noqa: BLE001 - discovery must never crash research
        prov = None
    if prov is not None:
        return list(prov(query) or [])
    raise SearchUnavailable(
        "no external search provider configured — discovery uses stored "
        "sources/portals only (honest; nothing is fabricated)")
