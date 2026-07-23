"""Live Browserbase observer + runtime driver — hermetic tests (no browser).

Drives the pure normalizer + the driver adaptation with a FAKE Playwright page,
proving structural extraction, sensitive-field refusal, hostname enforcement,
and §20 network redaction are correct WITHOUT a live browser. The real live
smoke test (test_live_providers.py) is flag/creds-gated and run separately.
"""
import pytest

from app.portal.live_browser import (normalize_observation, LiveBrowserSession,
                                     build_observer_factory)
from app.adapter_factory.live_driver import (BrowserbasePageDriver,
                                             sanitize_network_event)


# ---- structural observation carries NO values, flags sensitive fields ----
def test_normalize_observation_shape_and_sensitivity():
    raw = {"title": "Sign in", "elements": [
        {"selector": "#username", "name": "username", "label": "Email",
         "type": "email", "required": True, "sensitive": False},
        {"selector": "#password", "name": "password", "label": "Password",
         "type": "password", "required": True, "sensitive": True},
        {"selector": "#go", "name": "login", "label": "Sign in",
         "type": "submit", "submits": "login"},
    ], "links": ["https://portal.gov.example/help?token=abc"], "iframes": []}
    obs = normalize_observation("https://portal.gov.example/login", 200,
                                "portal.gov.example", raw)
    assert obs["ok"] and obs["hostname"] == "portal.gov.example"
    names = {e["name"]: e for e in obs["elements"]}
    assert names["password"]["sensitive"] is True
    assert names["username"]["sensitive"] is False
    # No element ever carries a value.
    for e in obs["elements"]:
        assert "value" not in e
    assert names["login"]["submits"] == "login"


def test_normalize_observation_never_emits_values_even_if_present():
    # Even if a buggy extractor leaked a value key, the normalizer's whitelist
    # drops it — only structural keys survive.
    raw = {"title": "x", "elements": [
        {"selector": "#a", "name": "a", "label": "A", "type": "text",
         "value": "SECRET-LEAK", "required": False}]}
    obs = normalize_observation("https://h.gov/x", 200, "h.gov", raw)
    assert "SECRET-LEAK" not in str(obs)


# ---- observer refuses to leave the hostname allowlist (credential-free) ----
def test_observer_refuses_off_allowlist_host():
    session = LiveBrowserSession(allowed_hostnames=["portal.gov.example"],
                                 session={"id": "local-x"}, page=object())
    res = session.observe("https://evil.example/login")
    assert res["ok"] is False and "off-allowlist" in res["error"]


def test_build_observer_factory_none_without_key(monkeypatch):
    from app.providers import browser as bb
    monkeypatch.setattr(bb, "is_configured", lambda: False)
    assert build_observer_factory(["portal.gov.example"]) is None


# ---- driver: sensitive fields are never typed ----
class _FakePage:
    def __init__(self):
        self.url = "https://portal.gov.example/application"
        self.filled = {}
        self.clicked = []
    def goto(self, url, **kw):
        self.url = url
        return type("R", (), {"status": 200})()
    def fill(self, selector, value, **kw):
        self.filled[selector] = value
    def click(self, selector, **kw):
        self.clicked.append(selector)
    def query_selector(self, selector):
        return type("E", (), {"inner_text": lambda self: "USD 80.00"})()


def test_driver_refuses_sensitive_selectors():
    d = BrowserbasePageDriver(_FakePage(), allowed_hostnames=["portal.gov.example"])
    assert d.fill("#password", "x")["ok"] is False
    assert d.fill("#card_number", "x")["ok"] is False
    assert d.fill("#otp", "x")["ok"] is False
    # A normal field is filled.
    assert d.fill("#full_name", "Ada")["ok"] is True


def test_driver_enforces_hostname_allowlist():
    d = BrowserbasePageDriver(_FakePage(), allowed_hostnames=["portal.gov.example"])
    assert d.goto("https://evil.example/x")["ok"] is False
    assert d.goto("https://portal.gov.example/fees")["ok"] is True


def test_driver_official_state_defaults_unknown():
    d = BrowserbasePageDriver(_FakePage(), allowed_hostnames=["portal.gov.example"])
    # No probe → never an assumed success.
    assert d.official_state() == {"known": False}


# ---- §20 network redaction: keys only, never values / bodies / queries ----
def test_sanitize_network_event_keeps_keys_drops_values():
    ev = sanitize_network_event(
        "POST", "https://portal.gov.example/api/pay?card=4111111111111111",
        200, "application/json",
        '{"charge_id": "CHG-1", "receipt_no": "R-1", "card_number": "4111111111111111"}')
    # Query string stripped; only top-level KEY NAMES retained; NO values.
    assert "?" not in ev["url"]
    assert set(ev["response_keys"]) == {"charge_id", "receipt_no", "card_number"}
    assert "4111111111111111" not in str(ev)
    assert "CHG-1" not in str(ev)


def test_sanitize_network_event_ignores_non_json_bodies():
    ev = sanitize_network_event("GET", "https://h.gov/x", 200, "text/html",
                                "<html>secret token=abc</html>")
    assert ev["response_keys"] == []
    assert "abc" not in str(ev)


def test_driver_records_only_sanitized_events():
    d = BrowserbasePageDriver(_FakePage(), allowed_hostnames=["portal.gov.example"])
    d._record_event("POST", "https://portal.gov.example/api/x?q=SECRET", 200,
                    "application/json", '{"ok": true, "ref": "R"}')
    evs = d.network_events()
    assert len(evs) == 1
    assert "SECRET" not in str(evs) and "?" not in evs[0]["url"]
    assert evs[0]["response_keys"] == ["ok", "ref"]
