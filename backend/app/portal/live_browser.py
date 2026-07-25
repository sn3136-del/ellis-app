"""Live Browserbase browser session + credential-free structural observer
(brief §14, §15, §28).

`LiveBrowserSession` owns one isolated Browserbase session and a Playwright
page connected over CDP. `observe(url)` navigates a PUBLIC page and returns a
STRUCTURAL observation (roles, labels, input types, required flags, a stable
selector, navigation relationships) — never values, cookies, tokens, or the
Live View URL. The observation is then passed through `recon.sanitize_structure`
before anything downstream sees it, so this module is the live counterpart of
`SyntheticPortal.observe`.

Credential-free by construction: the observer only navigates and reads DOM
structure; it never authenticates, never fills a field, never reads storage.
The heavy Playwright import is lazy so this module imports without Playwright
installed (tests exercise the pure `normalize_observation`).
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from ..providers import browser as bb

# Extraction runs IN the page and returns JSON-serializable structure only.
# It deliberately never reads element .value, cookies, or storage.
_EXTRACT_JS = r"""
() => {
  const sensitive = /(password|passcode|otp|one[-_]?time|cvv|cvc|card|pan|secret|token|captcha|pin|3ds|passkey)/i;
  const cssPath = (el) => {
    if (el.id) return '#' + CSS.escape(el.id);
    if (el.name) return el.tagName.toLowerCase() + '[name="' + el.name + '"]';
    const parts = [];
    let n = el;
    while (n && n.nodeType === 1 && parts.length < 4) {
      let s = n.tagName.toLowerCase();
      if (n.className && typeof n.className === 'string') {
        const c = n.className.trim().split(/\s+/)[0];
        if (c) s += '.' + CSS.escape(c);
      }
      parts.unshift(s);
      n = n.parentElement;
    }
    return parts.join(' > ');
  };
  const labelFor = (el) => {
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
    if (el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) return l.innerText; }
    if (el.placeholder) return el.placeholder;
    const p = el.closest('label'); if (p) return p.innerText;
    if (el.tagName === 'BUTTON' || el.type === 'submit') return el.innerText || el.value || '';
    return el.name || '';
  };
  const els = [];
  document.querySelectorAll('input, select, textarea, button, a[href]').forEach((el) => {
    const tag = el.tagName.toLowerCase();
    let type = (el.type || (tag === 'a' ? 'link' : tag)).toLowerCase();
    const name = (el.name || el.id || '').slice(0, 80);
    const label = (labelFor(el) || '').trim().slice(0, 120);
    const rec = { selector: cssPath(el).slice(0, 200), name, label, type,
                  required: !!el.required || el.getAttribute('aria-required') === 'true',
                  sensitive: type === 'password' || sensitive.test(name) || sensitive.test(label) };
    if (tag === 'button' || type === 'submit') rec.submits = (name || 'submit').replace(/[^a-z_]/gi, '').toLowerCase().slice(0, 40);
    if (tag === 'a' && el.getAttribute('href')) {
      try { rec.navigates_to = new URL(el.href, location.href).pathname.slice(0, 200); } catch (e) {}
    }
    els.push(rec);
  });
  const links = Array.from(document.querySelectorAll('a[href]')).slice(0, 40).map((a) => {
    try { return new URL(a.href, location.href).href; } catch (e) { return ''; }
  }).filter(Boolean);
  const iframes = Array.from(document.querySelectorAll('iframe')).slice(0, 10).map((f) => {
    try { return new URL(f.src || '', location.href).pathname; } catch (e) { return ''; }
  });
  return { title: (document.title || '').slice(0, 120), elements: els.slice(0, 200),
           links, iframes, hasShadow: !!document.querySelector('*') && [...document.querySelectorAll('*')].some((e) => e.shadowRoot) };
}
"""

# Hard deadline for attaching to a remote browser session. Without it a
# stalled Browserbase session hangs a build indefinitely.
CONNECT_TIMEOUT_MS = 60_000

_ALLOWED_TYPES = {"text", "email", "password", "date", "select", "checkbox",
                  "radio", "file", "button", "submit", "tel", "number", "link"}


def normalize_observation(url: str, status: int, hostname: str, raw: dict,
                          delayed: bool = False) -> dict:
    """Pure normalizer: shape a raw page-extraction dict into the observation
    contract `recon.sanitize_structure` consumes. No live browser needed, so
    this is what the hermetic tests drive. Still emits NO values — the JS never
    captured any."""
    elements = []
    for el in (raw or {}).get("elements", []) or []:
        etype = el.get("type")
        etype = etype if etype in _ALLOWED_TYPES else "text"
        rec = {
            "selector": str(el.get("selector", ""))[:200],
            "name": re.sub(r"[^a-zA-Z0-9_\-]", "", str(el.get("name", "")))[:80],
            "label": str(el.get("label", ""))[:120],
            "type": "text" if etype in ("link",) and el.get("navigates_to") is None else etype,
            "required": bool(el.get("required", False)),
            "sensitive": bool(el.get("sensitive", False)) or etype == "password",
        }
        if el.get("submits"):
            rec["submits"] = re.sub(r"[^a-z_]", "", str(el.get("submits")))[:40]
        if el.get("navigates_to"):
            rec["navigates_to"] = str(el.get("navigates_to"))[:200]
        elements.append(rec)
    return {
        "ok": 200 <= int(status) < 400,
        "status": int(status),
        "url": url,
        "hostname": (hostname or urlparse(url).netloc).lower(),
        "title": str((raw or {}).get("title", ""))[:120],
        "elements": elements,
        "links": [str(l)[:300] for l in ((raw or {}).get("links") or [])][:40],
        "iframes": [str(f)[:200] for f in ((raw or {}).get("iframes") or [])][:10],
        "delayed": bool(delayed or (raw or {}).get("hasShadow")),
    }


class LiveBrowserSession:
    """One isolated Browserbase session + Playwright page over CDP. Used for
    credential-free reconnaissance (observe) and, reusing the same page, as the
    backing page for the live runtime driver."""

    def __init__(self, *, allowed_hostnames: list[str], session: dict | None = None,
                 page=None):
        self.allowed = [h.lower() for h in (allowed_hostnames or [])]
        self.session = session
        self.page = page          # injected in tests; else lazily connected
        self._pw = None
        self._owns_session = session is None

    # ---- lifecycle ----------------------------------------------------------
    def _ensure_page(self):
        if self.page is not None:
            return self.page
        if self.session is None:
            self.session = bb.create_session()
        connect = self.session.get("connect_url")
        if not connect:
            raise RuntimeError("no Browserbase connect URL — is BROWSERBASE_API_KEY set?")
        from playwright.sync_api import sync_playwright  # pragma: no cover
        self._pw = sync_playwright().start()             # pragma: no cover
        chromium = self._pw.chromium                     # pragma: no cover
        # A stalled Browserbase session would otherwise block the whole build
        # forever — connect_over_cdp has no default deadline.
        browser = chromium.connect_over_cdp(             # pragma: no cover
            connect, timeout=CONNECT_TIMEOUT_MS)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()  # pragma: no cover
        self.page = ctx.pages[0] if ctx.pages else ctx.new_page()  # pragma: no cover
        return self.page

    def close(self):
        try:
            if self._pw is not None:
                self._pw.stop()  # pragma: no cover
        finally:
            if self._owns_session and self.session and not str(
                    self.session.get("id", "")).startswith("local-"):
                bb.close_session(self.session.get("id", ""))

    # ---- observation --------------------------------------------------------
    def _host_ok(self, url: str) -> bool:
        h = urlparse(url).netloc.lower()
        return any(h == a or h.endswith("." + a) for a in self.allowed)

    def observe(self, url: str) -> dict:
        """Navigate a PUBLIC page and return its sanitized-shape structure. The
        observer never authenticates and refuses to leave the allowlist."""
        if self.allowed and not self._host_ok(url):
            return {"ok": False, "status": 0, "url": url,
                    "error": "off-allowlist host refused"}
        page = self._ensure_page()
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)  # pragma: no cover
            status = resp.status if resp else 200                                # pragma: no cover
            final = page.url                                                     # pragma: no cover
            # A redirect that leaves the allowlist is not observed further.
            if self.allowed and not self._host_ok(final):                        # pragma: no cover
                return {"ok": False, "status": status, "url": final,
                        "error": "redirected off allowlist"}
            raw = page.evaluate(_EXTRACT_JS)                                     # pragma: no cover
        except Exception as e:  # noqa: BLE001                                    # pragma: no cover
            return {"ok": False, "status": 0, "url": url, "error": str(e)[:200]}
        return normalize_observation(final, status, urlparse(final).netloc, raw)  # pragma: no cover


def build_observer_factory(hostnames: list[str]):
    """Return `observer(url) -> observation` backed by ONE live session, for
    `recon.run_recon` / `build_workflow.run_build`. Real Browserbase only.
    The returned callable carries `.close()` so the caller (run_build's finally
    block, or the API layer) can release the Browserbase session when done."""
    if not bb.is_configured():
        return None
    session = LiveBrowserSession(allowed_hostnames=hostnames)

    def observe(url: str) -> dict:
        return session.observe(url)

    observe.close = session.close
    observe.session = session          # runtime reuse: same page drives execution
    return observe
