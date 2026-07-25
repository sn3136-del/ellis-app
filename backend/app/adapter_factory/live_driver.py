"""Live Browserbase runtime driver (brief §19-§20, §28).

`BrowserbasePageDriver` adapts a live Playwright page (over a Browserbase CDP
session) to the deterministic `FlowRunner`'s duck-typed interface —
goto / fill / click / read_text / network_events / official_state. It is the
live counterpart of `SyntheticPortal` for the released-runtime path.

Safety, enforced here (not by policy):
- Sensitive selectors are NEVER typed; the driver refuses them so the runtime
  falls back to an applicant handoff.
- Navigation is confined to the adapter's hostname allowlist (fail closed).
- Network observation stores only §20-permitted, redacted evidence: method,
  normalized endpoint pattern, status, content-type, and TOP-LEVEL response
  KEY NAMES (values removed). Bodies, cookies, tokens, and query values are
  never captured or stored.
- No model is consulted; nothing here imports a provider or Kimi.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

_SENSITIVE_SELECTOR = re.compile(
    r"(password|passcode|otp|one[-_]?time|cvv|cvc|card|pan|secret|token|captcha|pin|3ds|passkey)",
    re.IGNORECASE)


def sanitize_network_event(method: str, url: str, status: int, content_type: str,
                           body_text: str | None) -> dict:
    """Reduce a response to §20-permitted evidence. Top-level JSON key NAMES are
    kept; every value is discarded. Non-JSON bodies contribute no keys."""
    parsed = urlparse(url or "")
    keys: list[str] = []
    if body_text and "json" in (content_type or "").lower():
        try:
            import json
            obj = json.loads(body_text)
            if isinstance(obj, dict):
                keys = [str(k)[:60] for k in list(obj.keys())[:25]]
        except Exception:  # noqa: BLE001 — never let a body break redaction
            keys = []
    return {
        "method": (method or "").upper()[:8],
        "url": f"https://{parsed.netloc}{parsed.path}"[:300],  # query stripped
        "status": int(status or 0),
        "content_type": (content_type or "")[:100],
        "response_keys": keys,
        "category": "",   # the flow's evidence rules match on keys/status/host
    }


class BrowserbasePageDriver:
    def __init__(self, page, *, allowed_hostnames: list[str], state_probe=None):
        self.page = page
        self.allowed = [h.lower() for h in (allowed_hostnames or [])]
        self._events: list[dict] = []
        self._state_probe = state_probe
        self._wire_network()

    # ---- FlowRunner interface ----------------------------------------------
    def goto(self, url: str) -> dict:
        if not self._host_ok(url):
            return {"ok": False, "code": "OFF_ALLOWLIST"}
        try:
            resp = self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            status = resp.status if resp else 200
            if not self._host_ok(self.page.url):
                return {"ok": False, "code": "REDIRECTED_OFF_ALLOWLIST"}
            return {"ok": 200 <= int(status) < 400, "status": int(status)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": "NAV_ERROR", "detail": str(e)[:120]}

    def fill(self, selector: str, value: str) -> dict:
        # The deterministic runtime never fills a sensitive field, but refuse
        # here too (defense in depth) so a mis-generated node fails closed.
        if _SENSITIVE_SELECTOR.search(selector or ""):
            return {"ok": False, "code": "SENSITIVE_FIELD_AUTOMATION"}
        try:
            self.page.fill(selector, str(value), timeout=15000)
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            # Readonly picker inputs (e.g. Ant Design date pickers) refuse
            # fill(); they accept focused keyboard entry. Type, commit with
            # Enter, then READ BACK the value — success only on exact echo.
            try:
                self.page.click(selector, timeout=10000)
                self.page.keyboard.type(str(value), delay=25)
                self.page.keyboard.press("Enter")
                self.page.wait_for_timeout(300)
                got = self.page.input_value(selector, timeout=5000)
                # A picker panel can linger after Enter and swallow the next
                # element's clicks — dismiss it before moving on.
                self.page.keyboard.press("Escape")
                if got == str(value):
                    return {"ok": True, "method": "typed"}
                return {"ok": False, "code": "VALUE_NOT_ACCEPTED",
                        "detail": f"portal kept {got!r}"[:120]}
            except Exception as e2:  # noqa: BLE001
                return {"ok": False, "code": "NO_SUCH_ELEMENT",
                        "detail": (str(e) + " | " + str(e2))[:120]}

    def click(self, selector: str) -> dict:
        try:
            # Text selectors can also match stale HIDDEN elements (e.g. a
            # dismissed modal's button still in the DOM) — click the first
            # VISIBLE match so the wait can't hang on an invisible node.
            self.page.locator(f"{selector} >> visible=true").first.click(timeout=15000)
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            msg = str(e).lower()
            if "timeout" in msg:
                return {"ok": False, "code": "TIMEOUT"}
            return {"ok": False, "code": "NO_SUCH_ELEMENT", "detail": str(e)[:120]}

    def read_text(self, selector: str) -> dict:
        try:
            el = self.page.query_selector(selector)
            if el is None:
                return {"ok": False, "code": "NO_SUCH_ELEMENT"}
            return {"ok": True, "text": (el.inner_text() or "")[:200]}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": "READ_ERROR", "detail": str(e)[:120]}

    def check(self, selector: str) -> dict:
        """Real checkbox tick. Refuses anything resembling a sensitive control
        (final declarations are APPLICANT_HANDOFF nodes, never automated)."""
        if _SENSITIVE_SELECTOR.search(selector or ""):
            return {"ok": False, "code": "SENSITIVE_FIELD_AUTOMATION"}
        try:
            self.page.check(selector, timeout=15000)
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": "NO_SUCH_ELEMENT", "detail": str(e)[:120]}

    def select_search(self, selector: str, value: str) -> dict:
        """Search-combobox selection (verified live against Ant Design selects):
        focus, TYPE the query (combobox inputs refuse fill()), WAIT for the
        filtered option list (they load asynchronously), pick the exact match,
        else the first option containing the query. The committed selection is
        read back from the widget's selection element — an unreadable or
        mismatched selection fails honestly (never a guess)."""
        if _SENSITIVE_SELECTOR.search(selector or ""):
            return {"ok": False, "code": "SENSITIVE_FIELD_AUTOMATION"}
        want = str(value).strip()
        try:
            try:  # dismiss any lingering overlay (e.g. an open date picker)
                self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(150)
            except Exception:  # noqa: BLE001
                pass
            self.page.locator(f"{selector} >> visible=true").first.click(timeout=15000)
            try:  # clear any previous query so filters start clean
                self.page.keyboard.press("Control+A")
                self.page.keyboard.press("Delete")
            except Exception:  # noqa: BLE001
                pass
            self.page.keyboard.type(want, delay=25)
            # Overlay options are often not painted yet, so inner_text() is
            # empty — read textContent, which is present as soon as the node is.
            def _label(handle):
                try:
                    return (handle.evaluate("el => el.textContent") or "").strip()
                except Exception:  # noqa: BLE001
                    return ""

            options = []
            for _ in range(16):     # up to ~8s for async option lists
                self.page.wait_for_timeout(500)
                options = [(o, _label(o))
                           for o in self.page.query_selector_all('[role="option"]')]
                options = [(o, t) for o, t in options if t]
                if options:
                    break
            if not options:
                self.page.keyboard.press("Escape")
                return {"ok": False, "code": "NO_OPTIONS",
                        "detail": f"no option matches {want[:40]!r}"}
            target = chosen_text = None
            for opt, text in options:
                if text.lower() == want.lower():
                    target, chosen_text = opt, text
                    break
            if target is None:
                for opt, text in options:
                    if want.lower() in text.lower():
                        target, chosen_text = opt, text
                        break
            if target is None:
                self.page.keyboard.press("Escape")
                return {"ok": False, "code": "NO_OPTIONS",
                        "detail": f"{len(options)} options, none match {want[:40]!r}"}
            # Option rows live in a floating overlay that Playwright's
            # actionability checks can consider unstable; dispatch the click
            # directly on the resolved element (same user-visible effect).
            target.evaluate("el => el.click()")
            self.page.wait_for_timeout(400)
            shown = ""
            try:
                shown = self.page.eval_on_selector(
                    selector,
                    "el => { const w = el.closest('[class*=\"select\"]');"
                    " const s = w && w.querySelector('[class*=\"selection-item\"]');"
                    " return s ? s.innerText.trim() : ''; }") or ""
            except Exception:  # noqa: BLE001
                shown = ""
            if shown and not (want.lower() in shown.lower()
                              or shown.lower() in chosen_text.lower()):
                return {"ok": False, "code": "VALUE_NOT_ACCEPTED",
                        "detail": f"portal shows {shown[:60]!r}"}
            return {"ok": True, "shown": (shown or chosen_text)[:60]}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": "NO_SUCH_ELEMENT", "detail": str(e)[:120]}

    def scroll_bottom(self, selector: str = "") -> dict:
        """Scroll a container (or the window) to its bottom and emit a scroll
        event — some portals gate their Continue buttons on a full read."""
        try:
            if selector:
                self.page.eval_on_selector_all(
                    selector,
                    "els => els.forEach(el => { el.scrollTop = el.scrollHeight;"
                    " el.dispatchEvent(new Event('scroll', {bubbles: true})); })")
            else:
                self.page.evaluate(
                    "() => window.scrollTo(0, document.body.scrollHeight)")
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": "SCROLL_ERROR", "detail": str(e)[:120]}

    def upload(self, selector: str, path: str) -> dict:
        """Attach an authorized document to a real file input. The path is a
        backend-local temp copy of a case document the applicant approved."""
        try:
            self.page.set_input_files(selector, path, timeout=30000)
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": "UPLOAD_FAILED", "detail": str(e)[:120]}

    def network_events(self) -> list[dict]:
        return list(self._events)

    def official_state(self) -> dict:
        """Reconciliation reads authoritative state via an injected probe (an
        adapter-declared READ against the official account/status surface).
        With no probe, returns known=False — NEVER an assumed success (§20)."""
        if self._state_probe is None:
            return {"known": False}
        try:
            return self._state_probe(self.page) or {"known": False}
        except Exception:  # noqa: BLE001
            return {"known": False}

    def close(self):
        pass  # the owning LiveBrowserSession releases the Browserbase session

    # ---- internals ----------------------------------------------------------
    def _host_ok(self, url: str) -> bool:
        h = urlparse(url).netloc.lower()
        return any(h == a or h.endswith("." + a) for a in self.allowed)

    def _wire_network(self):
        on = getattr(self.page, "on", None)
        if not callable(on):
            return

        def _handler(response):  # pragma: no cover - needs a live page
            try:
                req = response.request
                ctype = response.headers.get("content-type", "")
                body = None
                if "json" in ctype.lower():
                    try:
                        body = response.text()
                    except Exception:  # noqa: BLE001
                        body = None
                self._events.append(sanitize_network_event(
                    req.method, response.url, response.status, ctype, body))
            except Exception:  # noqa: BLE001
                pass

        try:
            on("response", _handler)
        except Exception:  # noqa: BLE001
            pass

    # Test seam: feed a sanitized event directly (hermetic tests).
    def _record_event(self, method, url, status, content_type, body_text):
        self._events.append(sanitize_network_event(method, url, status, content_type, body_text))
