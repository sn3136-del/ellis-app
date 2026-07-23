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
            return {"ok": False, "code": "NO_SUCH_ELEMENT", "detail": str(e)[:120]}

    def click(self, selector: str) -> dict:
        try:
            self.page.click(selector, timeout=15000)
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
