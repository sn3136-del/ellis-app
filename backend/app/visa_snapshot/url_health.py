"""Are the links in an answer alive? Checked before a reader can click them.

A model-generated portal URL can be plausible and dead — the exact failure
Trip.com flagged in their demo review ("the official website link points to
the wrong destination"). Every link an answer carries is therefore checked
with a real request, and a dead one is REMOVED: the tile then shows the
channel with no link, which is honest, instead of a 404.

Dead means: the name does not resolve, the connection fails, the server says
404/410, or it says 200 while the page itself says "page not found" (VFS
Global serves that soft-404 for any path, which is how the Lithuania link
slipped through a status check). A 403/429 — bot walls are routine on
government sites — or a timeout is NOT dead: those pages open in a browser,
so the link is kept.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

TIMEOUT_SECONDS = 10.0
_DEAD_STATUS = {404, 410}
_SOFT_404 = ("page not found", "page cannot be found", "could not be found",
             "not be found", "does not exist", "no longer exists",
             "页面不存在", "找不到网页", "找不到頁面", "页面未找到")
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# The top-level answer fields that carry links a reader can click.
URL_FIELDS = ("official_portal_url", "source_url")

_CHECKER = None


def set_checker(fn) -> None:
    """Tests inject; live uses httpx."""
    global _CHECKER
    _CHECKER = fn


def url_is_dead(url: str) -> bool:
    if _CHECKER is not None:
        return bool(_CHECKER(url))
    try:
        import httpx
        with httpx.Client(follow_redirects=True, timeout=TIMEOUT_SECONDS,
                          headers={"user-agent": _UA}) as c:
            r = c.get(url)
        if r.status_code in _DEAD_STATUS:
            return True
        if r.status_code == 200:
            head = r.text[:6000].lower()
            return any(m in head for m in _SOFT_404)
        return False
    except Exception as e:  # noqa: BLE001
        # Only a name that does not RESOLVE is dead on a connection error.
        # Government sites routinely ship broken TLS chains (evisa.gov.vn,
        # boca.gov.tw) or reset automated clients (indianvisaonline.gov.in)
        # and still open fine in a real browser — those links are kept.
        msg = str(e).lower()
        return any(t in msg for t in ("nodename nor servname",
                                      "name or service not known",
                                      "getaddrinfo", "no address associated"))


def collect_urls(guidance: dict) -> list[str]:
    urls = []
    for f in URL_FIELDS:
        u = (guidance or {}).get(f)
        if isinstance(u, str) and u.startswith("http"):
            urls.append(u)
    return urls


def strip_dead_links(guidance: dict, *, dead: set | None = None) -> list[str]:
    """Remove dead links from an answer IN PLACE; returns what was removed.
    Pass `dead` when the caller already checked a batch of URLs; otherwise
    each link is checked here, concurrently."""
    urls = collect_urls(guidance)
    if not urls:
        return []
    if dead is None:
        with ThreadPoolExecutor(max_workers=min(4, len(urls))) as pool:
            dead = {u for u, d in zip(urls, pool.map(url_is_dead, urls)) if d}
    removed = []
    for f in URL_FIELDS:
        u = guidance.get(f)
        if isinstance(u, str) and u in dead:
            guidance[f] = None
            removed.append(u)
    return removed
