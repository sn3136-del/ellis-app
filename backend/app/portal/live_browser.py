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
    if (el.tagName === 'BUTTON') {
      // A button with stable visible text gets a bounded, deterministic
      // text selector instead of a brittle deep ancestor path.
      const t = (el.innerText || '').trim().replace(/\s+/g, ' ').replace(/["<>]/g, '').slice(0, 40);
      if (t) return 'button:has-text("' + t + '")';
    }
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
    // Search-combobox detection (ARIA-based): SPA select widgets whose entry
    // control is a text input driving a filtered option list.
    if (tag === 'input' && (type === 'text' || type === 'search' || type === '')) {
      if (el.getAttribute('role') === 'combobox' ||
          el.getAttribute('aria-autocomplete') === 'list' ||
          el.closest('[role="combobox"]')) type = 'search-combobox';
    }
    const name = (el.name || el.id || '').slice(0, 80);
    const label = (labelFor(el) || '').trim().slice(0, 120);
    const rec = { selector: cssPath(el).slice(0, 200), name, label, type,
                  placeholder: (el.placeholder || '').slice(0, 60),
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
                  "radio", "file", "button", "submit", "tel", "number", "link",
                  "search-combobox"}


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
        if el.get("placeholder"):
            rec["placeholder"] = str(el.get("placeholder"))[:60]
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
        # Reattach to a LIVE page — pages[0] can be a closed leftover, and a
        # dead page turns every later action into NO_SUCH_ELEMENT noise.
        live = [p for p in ctx.pages if not p.is_closed()]  # pragma: no cover
        self.page = live[0] if live else ctx.new_page()     # pragma: no cover
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

    # ---- declarative entry-gate replay (credential-free, reversible) --------
    # Some SPA portals gate their application form behind an in-session
    # instruction sequence (open modal, scroll acknowledgment text, tick the
    # acknowledgment checkboxes, continue). The sequence is DECLARED, curated
    # data — never inferred from page content — and only these reversible
    # navigation/acknowledgment actions are permitted.
    ENTRY_GATE_ACTIONS = ("CLICK", "SCROLL_TO_BOTTOM", "CHECK")
    ENTRY_GATE_MAX_ACTIONS = 12

    _SENSITIVE_TARGET_RE = re.compile(
        r"(password|passcode|otp|one[-_]?time|cvv|cvc|card|pan|secret|token|"
        r"captcha|pin|3ds|passkey|pay)", re.IGNORECASE)

    def _assert_gate_target_safe(self, locator, action: str, selector: str):
        """No entry-gate action may ever touch a password/payment/sensitive
        field: CHECK only real checkboxes, CLICK never a value-bearing input."""
        info = locator.evaluate(
            "el => ({tag: el.tagName.toLowerCase(),"
            " type: (el.getAttribute('type') || '').toLowerCase(),"
            " ident: ((el.name || '') + ' ' + (el.id || ''))})")
        if info.get("type") == "password" or \
                self._SENSITIVE_TARGET_RE.search(info.get("ident", "")):
            raise RuntimeError(f"entry gate refused: {selector!r} is a sensitive field")
        if action == "CHECK" and not (info.get("tag") == "input"
                                      and info.get("type") == "checkbox"):
            raise RuntimeError(f"entry gate refused: CHECK target {selector!r} "
                               f"is not a checkbox")
        if action == "CLICK" and info.get("tag") in ("input", "textarea", "select") \
                and info.get("type") not in ("button", "submit", "checkbox", "radio"):
            raise RuntimeError(f"entry gate refused: CLICK target {selector!r} "
                               f"is a form input")

    # Applied to a RESOLVED element, never to a selector string: entry-gate
    # selectors are Playwright syntax (':visible', '>> nth=N', ':has-text()')
    # which document.querySelectorAll cannot parse.
    _SCROLL_ONE_JS = """(t) => {
         if (!t) return;
         t.scrollTop = t.scrollHeight;
         t.dispatchEvent(new Event('scroll', {bubbles: true}));
       }"""

    @staticmethod
    def _scroll_container_to_bottom(page, selector: str):
        """Set scrollTop to max on every matching container (window when the
        selector is empty) and dispatch a scroll event — SPAs enable their
        'Next' button on the scroll event, not on scrollTop alone.

        Resolution goes through Playwright's selector engine so a declared
        entry-gate selector behaves identically here and at runtime (the
        runtime path uses eval_on_selector_all)."""
        if not selector:
            page.evaluate(
                """() => {
                     const t = document.scrollingElement;
                     if (!t) return;
                     t.scrollTop = t.scrollHeight;
                     t.dispatchEvent(new Event('scroll', {bubbles: true}));
                   }""")
            return
        loc = page.locator(selector)
        for i in range(min(loc.count(), 20)):
            try:
                loc.nth(i).evaluate(LiveBrowserSession._SCROLL_ONE_JS)
            except Exception:  # noqa: BLE001 — one container never blocks the rest
                continue

    def observe_with_entry_gate(self, base_url: str, entry_gate: dict) -> dict:
        """Replay a DECLARED entry gate from base_url and observe the
        destination page's structure. Actions are restricted to the reversible
        navigation/acknowledgment vocabulary; the observer still never
        authenticates, never fills a value, and never leaves the allowlist."""
        gate = entry_gate or {}
        actions = list(gate.get("actions") or [])[: self.ENTRY_GATE_MAX_ACTIONS]
        for a in actions:
            if (a or {}).get("action") not in self.ENTRY_GATE_ACTIONS:
                return {"ok": False, "status": 0, "url": base_url,
                        "error": f"entry gate action {(a or {}).get('action')!r} "
                                 f"not in the declared vocabulary"}
        if self.allowed and not self._host_ok(base_url):
            return {"ok": False, "status": 0, "url": base_url,
                    "error": "off-allowlist host refused"}
        page = self._ensure_page()
        performed: list[dict] = []
        try:                                                                     # pragma: no cover
            resp = page.goto(base_url, wait_until="domcontentloaded", timeout=45000)
            status = resp.status if resp else 200
            for a in actions:
                act = a["action"]
                sel = str(a.get("selector") or "")
                if act == "SCROLL_TO_BOTTOM":
                    self._scroll_container_to_bottom(page, sel)
                    performed.append({"action": act, "selector": sel, "ok": True})
                    continue
                loc = page.locator(sel).first
                loc.wait_for(state="visible",
                             timeout=int(a.get("timeout_ms") or 30000))
                self._assert_gate_target_safe(loc, act, sel)
                if act == "CLICK":
                    loc.click(timeout=15000)
                else:  # CHECK — a real checkbox check, acknowledgment only
                    loc.check(timeout=15000)
                performed.append({"action": act, "selector": sel, "ok": True})
            expect_path = str(gate.get("expect_path") or "")
            if expect_path:
                page.wait_for_url(
                    lambda u: urlparse(u).path.rstrip("/") == expect_path.rstrip("/"),
                    timeout=30000)
            # SPA render readiness (declared, portal-agnostic):
            #  1. the destination path is already confirmed (wait_for_url);
            #  2. wait for the declared concrete control ATTACHED — not
            #     visible: styled upload inputs are permanently display:none
            #     behind "Choose file" buttons, and a visibility wait on an
            #     [id^=...] locator can pin itself to exactly those;
            #  3. wait until the declared minimum number of form controls
            #     exists — SPA forms hydrate field-by-field while nomenclature
            #     lists load, which can be slow from a datacenter egress.
            ready = str(gate.get("form_ready_selector") or "")
            count_spec = gate.get("form_ready_all") or {}
            count_sel = str(count_spec.get("selector") or "")
            count_min = int(count_spec.get("min") or 0)
            try:
                if ready:
                    page.wait_for_selector(ready, state="attached", timeout=90000)
                if count_sel and count_min:
                    # Poll through Playwright's engine — a declared selector
                    # with ':visible'/'>> nth=' must count here exactly as it
                    # does everywhere else, not SyntaxError in a CSS parser.
                    deadline = 60.0
                    while True:
                        if page.locator(count_sel).count() >= count_min:
                            break
                        if deadline <= 0:
                            raise TimeoutError(
                                f"form_ready_all: fewer than {count_min} matches")
                        page.wait_for_timeout(500)
                        deadline -= 0.5
                elif not ready:
                    page.wait_for_load_state("networkidle", timeout=30000)
            except Exception as e:  # noqa: BLE001 — honest, diagnosable failure
                probe = count_sel or ready or "*"
                try:
                    have = page.locator(probe).count()
                except Exception:  # noqa: BLE001
                    have = -1
                return {"ok": False, "status": status, "url": page.url,
                        "error": f"form not ready at "
                                 f"{urlparse(page.url).path[:80]!r} "
                                 f"({probe[:40]!r} matches={have}, "
                                 f"need>={count_min or 1}): {str(e)[:100]}"}
            final = page.url
            if self.allowed and not self._host_ok(final):
                return {"ok": False, "status": status, "url": final,
                        "error": "entry gate left the allowlist"}
            if expect_path and urlparse(final).path.rstrip("/") != expect_path.rstrip("/"):
                return {"ok": False, "status": status, "url": final,
                        "error": f"entry gate ended at {urlparse(final).path!r}, "
                                 f"expected {expect_path!r}"}
            raw = page.evaluate(_EXTRACT_JS)
        except Exception as e:  # noqa: BLE001                                    # pragma: no cover
            return {"ok": False, "status": 0, "url": base_url,
                    "error": f"entry gate replay failed: {str(e)[:160]}"}
        # The replayed gate controls WERE observed (interacted with): echo them
        # as structural elements so downstream contract checks can ground the
        # flow's entry-gate node selectors in recorded observation.
        for i, st in enumerate(performed):                                       # pragma: no cover
            if st["action"] in ("CLICK", "CHECK"):
                raw.setdefault("elements", []).append({
                    "selector": st["selector"],
                    "name": f"entry_gate_step_{i + 1}",
                    "label": "entry gate control",
                    "type": "checkbox" if st["action"] == "CHECK" else "button",
                    "required": False, "sensitive": False})
        obs = normalize_observation(final, status, urlparse(final).netloc, raw)   # pragma: no cover
        obs["entry_gate_replayed"] = performed                                    # pragma: no cover
        return obs                                                                # pragma: no cover


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
    # Entry-gate replay (declared, reversible) shares the same session/page.
    observe.observe_with_entry_gate = session.observe_with_entry_gate
    # A brand-new isolated session for repeated-session selector verification
    # (the selectors_verified_repeated_sessions gate needs a SECOND session).
    observe.spawn_independent = lambda: _threaded_observer_factory(hostnames)
    return observe


def _threaded_observer_factory(hostnames: list[str]):
    """Independent live observer that runs ALL its Playwright work on one
    dedicated OS thread. Playwright's sync API refuses a SECOND driver on a
    thread that already hosts one ("Sync API inside the asyncio loop" — the
    first driver's loop stays attached to the thread), so the second
    independent session gets its own thread with no loop at all. Same
    credential-free LiveBrowserSession underneath; calls are marshalled with
    submit(...).result() so callers stay synchronous."""
    if not bb.is_configured():
        return None
    from concurrent.futures import ThreadPoolExecutor
    ex = ThreadPoolExecutor(max_workers=1,
                            thread_name_prefix="live-observer-2")
    holder: dict = {}

    def _session() -> LiveBrowserSession:
        if "s" not in holder:
            holder["s"] = LiveBrowserSession(allowed_hostnames=hostnames)
        return holder["s"]

    def observe(url: str) -> dict:
        return ex.submit(lambda: _session().observe(url)).result()

    def observe_with_entry_gate(base_url: str, entry_gate: dict) -> dict:
        return ex.submit(
            lambda: _session().observe_with_entry_gate(base_url, entry_gate)).result()

    def close():
        try:
            if "s" in holder:
                ex.submit(holder["s"].close).result()
        finally:
            ex.shutdown(wait=False)

    observe.observe_with_entry_gate = observe_with_entry_gate
    observe.close = close
    return observe
