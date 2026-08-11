"""Steel.dev as a second browser provider — safety floor, wire shape, dispatch.

Every test is hermetic: httpx.request itself is replaced, so each test can
assert on the EXACT bytes the provider would have put on the wire (and prove
that an unconfigured or unselected provider put nothing there at all). No test
touches the network.

The invariants under test are the ones that would be expensive to get wrong in
a government filing:
  * CAPTCHA solving and stealth are OFF on the wire, not merely by default;
  * the self-hosted body never carries the cloud-only keys (the OSS server 400s
    on them, which would look like "Steel is broken");
  * a provider is used only when it was ASKED for and is configured — otherwise
    Browserbase, otherwise the local handoff;
  * a Steel failure degrades honestly: no invented session id, no guessed
    viewer URL, and never the API key in an error message.
"""
import json as _json

import httpx
import pytest

from app import config
from app.providers import browser as bb
from app.providers import steel_browser as steel

KEY = "steel-test-key-do-not-log"
SELF_HOST = "http://localhost:3000"

# A representative Steel create response (the fields the provider reads).
_SESSION = {"id": "sess-abc123", "status": "live",
            "websocketUrl": "wss://connect.steel.dev/v1/sessions/sess-abc123",
            "debugUrl": "https://api.steel.dev/v1/sessions/sess-abc123/debug",
            "sessionViewerUrl": "https://app.steel.dev/sessions/sess-abc123"}


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = _json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(f"HTTP {self.status_code}",
                                        request=None, response=None)


class _Http:
    """Scripted stand-in for httpx.request. Records every call so a test can
    prove what reached — or never reached — the network."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, *, headers=None, json=None, timeout=None, **kw):
        self.calls.append({"method": method, "url": url, "headers": headers or {},
                           "body": json, "timeout": timeout})
        if not self.responses:
            raise AssertionError(f"unscripted HTTP call: {method} {url}")
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return _Resp(*nxt)


def _unsafe_switches(node, path="") -> list:
    """Every truthy CAPTCHA/stealth/fingerprint switch anywhere in a body.
    Walking the whole structure means a future field cannot smuggle one in."""
    bad = []
    if isinstance(node, dict):
        for k, v in node.items():
            here = f"{path}.{k}" if path else k
            if any(w in k.lower() for w in
                   ("captcha", "stealth", "humanize", "fingerprint")) and v is True:
                bad.append(here)
            bad += _unsafe_switches(v, here)
    return bad


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Every test starts with no Steel, no Browserbase and the default
    provider, regardless of any real .env present."""
    for k in ("STEEL_API_KEY", "STEEL_BASE_URL", "BROWSERBASE_API_KEY",
              "STEEL_USE_PROXY"):
        monkeypatch.setenv(k, "")
    monkeypatch.setenv("ELLIS_BROWSER_PROVIDER", "browserbase")
    config.settings.cache_clear()
    yield
    config.settings.cache_clear()


def _cloud(monkeypatch, key=KEY):
    monkeypatch.setenv("STEEL_API_KEY", key)
    monkeypatch.setenv("ELLIS_BROWSER_PROVIDER", "steel")
    config.settings.cache_clear()


def _self_hosted(monkeypatch, base=SELF_HOST):
    monkeypatch.setenv("STEEL_BASE_URL", base)
    monkeypatch.setenv("ELLIS_BROWSER_PROVIDER", "steel")
    config.settings.cache_clear()


def _http(monkeypatch, *responses):
    """Replace httpx.request with a scripted seam, and make the verbs the
    Browserbase path uses hard errors — a routing slip must fail loudly here,
    never reach out to a real host."""
    http = _Http(*responses)
    monkeypatch.setattr(httpx, "request", http)

    def _boom(*a, **k):
        raise AssertionError(f"unexpected httpx call: {a[:1]}")
    monkeypatch.setattr(httpx, "post", _boom)
    monkeypatch.setattr(httpx, "get", _boom)
    return http


# ---------------------------------------------------------------------------
# Configuration + deployment detection
# ---------------------------------------------------------------------------
def test_unconfigured_is_honest_and_silent(monkeypatch):
    http = _http(monkeypatch)                      # any call at all is a failure
    assert steel.is_configured() is False
    assert steel.deployment() == ""
    sess = steel.create_session()
    assert sess["mode"] == "local" and sess["connect_url"] is None
    assert sess["id"].startswith("local-")
    assert steel.live_view_url("sess-abc123") is None
    assert steel.session_status("sess-abc123") == ""
    steel.close_session("sess-abc123")             # no-op, never raises
    with pytest.raises(steel.SteelUnavailable):
        steel.session_connect_info("sess-abc123")
    assert http.calls == []


def test_deployment_is_decided_by_the_origin_that_would_answer(monkeypatch):
    _cloud(monkeypatch)
    assert steel.deployment() == steel.DEPLOYMENT_CLOUD
    assert steel._base_url() == steel.CLOUD_BASE

    _self_hosted(monkeypatch)
    assert steel.deployment() == steel.DEPLOYMENT_SELF_HOSTED
    assert steel._base_url() == SELF_HOST
    assert steel.is_configured() is True            # the OSS server needs no key

    # An explicit cloud origin is the cloud, whatever else is set.
    _self_hosted(monkeypatch, base="https://api.steel.dev/")
    assert steel.deployment() == steel.DEPLOYMENT_CLOUD


# ---------------------------------------------------------------------------
# The safety floor, asserted on the wire
# ---------------------------------------------------------------------------
def test_cloud_create_sends_captcha_solving_and_stealth_off(monkeypatch):
    _cloud(monkeypatch)
    http = _http(monkeypatch, (200, _SESSION))

    sess = steel.create_session()

    call = http.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://api.steel.dev/v1/sessions"
    body = call["body"]
    # The whole point of this module: these are on the wire, not assumed.
    assert body["solveCaptcha"] is False
    assert body["stealthConfig"]["autoCaptchaSolving"] is False
    assert body["stealthConfig"]["humanizeInteractions"] is False
    assert body["stealthConfig"]["skipFingerprintInjection"] is False
    # ...and nothing anywhere else in the body switches a solver or a disguise on.
    assert _unsafe_switches(body) == []
    assert body["debugConfig"] == {"interactive": True}   # the human's own hands
    assert body["timeout"] == steel.SESSION_TIMEOUT_SECONDS * 1000
    assert body["dimensions"] == steel.VIEWPORT
    assert call["timeout"] == steel._TIMEOUT_SECONDS      # explicit, always
    assert call["headers"]["steel-api-key"] == KEY

    assert sess == {"id": "sess-abc123", "mode": "steel",
                    "connect_url": _SESSION["websocketUrl"],
                    "deployment": "cloud", "proxied": False}


def test_proxy_is_opt_in_and_never_a_silent_fallback(monkeypatch):
    _cloud(monkeypatch)
    http = _http(monkeypatch, (200, _SESSION))
    steel.create_session()
    assert "useProxy" not in http.calls[0]["body"]

    monkeypatch.setenv("STEEL_USE_PROXY", "1")
    http = _http(monkeypatch, (200, _SESSION))
    sess = steel.create_session()
    assert http.calls[0]["body"]["useProxy"] is True
    # Requested and honored, because a refusal would have raised below.
    assert sess["proxied"] is True


def test_self_hosted_create_omits_every_cloud_only_key(monkeypatch):
    _self_hosted(monkeypatch)
    http = _http(monkeypatch, (200, _SESSION))

    sess = steel.create_session()

    call = http.calls[0]
    assert call["url"] == f"{SELF_HOST}/v1/sessions"
    body = call["body"]
    for cloud_only in ("timeout", "solveCaptcha", "stealthConfig", "debugConfig",
                       "useProxy"):
        assert cloud_only not in body, f"{cloud_only} would 400 the OSS server"
    # Self-hosted spells this one as a top-level bool — and it is never true.
    assert body["skipFingerprintInjection"] is False
    assert body["dimensions"] == steel.VIEWPORT
    # No key configured, so no auth header is invented.
    assert "steel-api-key" not in call["headers"]
    assert sess["deployment"] == "self_hosted" and sess["mode"] == "steel"


# ---------------------------------------------------------------------------
# Live view
# ---------------------------------------------------------------------------
def test_cloud_live_view_prefers_the_fullscreen_viewer(monkeypatch):
    _cloud(monkeypatch)
    http = _http(monkeypatch, (200, {
        "sessionViewerUrl": "https://app.steel.dev/sessions/sess-abc123",
        "sessionViewerFullscreenUrl": "https://app.steel.dev/v1/sessions/full"}))

    url = steel.live_view_url("sess-abc123")

    assert url == "https://app.steel.dev/v1/sessions/full"
    assert http.calls[0]["url"] == (
        "https://api.steel.dev/v1/sessions/sess-abc123/live-details")


def test_cloud_live_view_falls_back_to_the_session_record(monkeypatch):
    _cloud(monkeypatch)
    http = _http(monkeypatch, (500, {}), (200, _SESSION))
    assert steel.live_view_url("sess-abc123") == _SESSION["sessionViewerUrl"]
    assert http.calls[1]["url"] == "https://api.steel.dev/v1/sessions/sess-abc123"


def test_self_hosted_live_view_uses_the_debug_surface(monkeypatch):
    _self_hosted(monkeypatch)
    http = _http(monkeypatch, (200, {"debuggerFullscreenUrl": f"{SELF_HOST}/v1/debug"}))
    assert steel.live_view_url("sess-abc123") == f"{SELF_HOST}/v1/debug"
    assert http.calls[0]["url"] == f"{SELF_HOST}/v1/sessions/debug"


def test_live_view_returns_none_rather_than_guessing_a_url(monkeypatch):
    _cloud(monkeypatch)
    # A 200 with no viewer field, then a session record with none either.
    _http(monkeypatch, (200, {"id": "sess-abc123"}), (200, {"id": "sess-abc123"}))
    assert steel.live_view_url("sess-abc123") is None


# ---------------------------------------------------------------------------
# Lifecycle: release, status, re-attach
# ---------------------------------------------------------------------------
def test_close_releases_the_session_and_never_raises(monkeypatch):
    _cloud(monkeypatch)
    http = _http(monkeypatch, (200, {"success": True}))
    steel.close_session("sess-abc123")
    assert http.calls[0]["method"] == "POST"
    assert http.calls[0]["url"] == "https://api.steel.dev/v1/sessions/sess-abc123/release"

    # A provider that cannot be reached must not break a caller's `finally`.
    _http(monkeypatch, httpx.ConnectError("down"))
    steel.close_session("sess-abc123")

    # Local descriptors are never released at a provider.
    http = _http(monkeypatch)
    steel.close_session("local-deadbeef")
    assert http.calls == []


def test_session_alive_only_false_when_steel_says_so(monkeypatch):
    _cloud(monkeypatch)
    _http(monkeypatch, (200, {"status": "live"}))
    assert steel.session_alive("sess-abc123") is True
    _http(monkeypatch, (200, {"status": "released"}))
    assert steel.session_alive("sess-abc123") is False
    # Unknown (provider unreachable) counts as alive: a blind probe must never
    # destroy a session the applicant is working in.
    _http(monkeypatch, httpx.ConnectError("down"))
    assert steel.session_alive("sess-abc123") is True


def test_connect_info_refuses_a_session_that_is_not_live(monkeypatch):
    _cloud(monkeypatch)
    _http(monkeypatch, (200, _SESSION))
    assert steel.session_connect_info("sess-abc123") == {
        "id": "sess-abc123", "mode": "steel",
        "connect_url": _SESSION["websocketUrl"]}

    _http(monkeypatch, (200, {"id": "sess-abc123", "status": "released",
                              "websocketUrl": "wss://x"}))
    with pytest.raises(steel.SteelUnavailable):
        steel.session_connect_info("sess-abc123")


# ---------------------------------------------------------------------------
# Honest degradation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("scripted", [
    (402, {"error": "quota"}),
    (500, {"error": "boom"}),
    (200, {"error": "no session for you"}),        # 200 with nothing drivable
])
def test_a_steel_error_never_fabricates_a_session(monkeypatch, scripted):
    _cloud(monkeypatch)
    _http(monkeypatch, scripted)
    with pytest.raises(steel.SteelUnavailable) as exc:
        steel.create_session()
    # No id, no connect URL, and never the credential in the message.
    assert KEY not in str(exc.value)
    assert "sess-" not in str(exc.value)


def test_an_unreachable_steel_raises_rather_than_returning_local(monkeypatch):
    _cloud(monkeypatch)
    _http(monkeypatch, httpx.ConnectError("dns"))
    with pytest.raises(steel.SteelUnavailable) as exc:
        steel.create_session()
    assert KEY not in str(exc.value)
    # A configured provider that fails is an outage, not a quiet downgrade to a
    # local descriptor the caller would treat as a working session.
    assert "unreachable" in str(exc.value)


# ---------------------------------------------------------------------------
# Dispatch in providers/browser.py
# ---------------------------------------------------------------------------
def test_dispatch_uses_steel_only_when_asked_for_and_configured(monkeypatch):
    # Selected + configured -> steel.
    _cloud(monkeypatch)
    assert bb.active_provider() == "steel"
    assert bb.is_configured() is True

    # Selected but NOT configured, with Browserbase available -> browserbase.
    monkeypatch.setenv("STEEL_API_KEY", "")
    monkeypatch.setenv("BROWSERBASE_API_KEY", "bb-key")
    config.settings.cache_clear()
    assert bb.active_provider() == "browserbase"

    # Selected, nothing configured at all -> the local handoff, as before.
    monkeypatch.setenv("BROWSERBASE_API_KEY", "")
    config.settings.cache_clear()
    assert bb.active_provider() == "local_handoff"
    assert bb.is_configured() is False

    # Configured but NOT selected -> Browserbase keeps the session. A stray key
    # must never silently re-home an applicant's filing onto another vendor.
    monkeypatch.setenv("STEEL_API_KEY", KEY)
    monkeypatch.setenv("BROWSERBASE_API_KEY", "bb-key")
    monkeypatch.setenv("ELLIS_BROWSER_PROVIDER", "browserbase")
    config.settings.cache_clear()
    assert bb.active_provider() == "browserbase"


def test_dispatch_routes_create_close_and_live_view_to_steel(monkeypatch):
    _cloud(monkeypatch)
    seen = {"created": 0, "closed": [], "lv": []}
    monkeypatch.setattr(steel, "create_session",
                        lambda: seen.__setitem__("created", seen["created"] + 1)
                        or {"id": "sess-abc123", "mode": "steel",
                            "connect_url": "wss://x", "deployment": "cloud"})
    monkeypatch.setattr(steel, "close_session", lambda sid: seen["closed"].append(sid))
    monkeypatch.setattr(steel, "live_view_url",
                        lambda sid: seen["lv"].append(sid) or "https://app.steel.dev/v")
    monkeypatch.setattr(steel, "session_alive", lambda sid: True)
    monkeypatch.setattr(steel, "session_status", lambda sid: "live")
    monkeypatch.setattr(steel, "session_connect_info",
                        lambda sid: {"id": sid, "mode": "steel", "connect_url": "wss://x"})
    # Any Browserbase HTTP would be a routing bug.
    http = _http(monkeypatch)

    sess = bb.create_session()
    assert sess["mode"] == "steel" and seen["created"] == 1
    assert bb.live_view_url("sess-abc123") == "https://app.steel.dev/v"
    assert bb.session_status("sess-abc123") == "live"
    assert bb.session_alive("sess-abc123") is True
    assert bb.session_connect_info("sess-abc123")["connect_url"] == "wss://x"
    bb.close_session("sess-abc123")
    assert seen["closed"] == ["sess-abc123"] and seen["lv"] == ["sess-abc123"]
    assert http.calls == []


def test_dispatch_leaves_the_browserbase_path_untouched(monkeypatch):
    monkeypatch.setenv("STEEL_API_KEY", KEY)     # present but not selected
    monkeypatch.setenv("BROWSERBASE_API_KEY", "bb-key")
    monkeypatch.setenv("BROWSERBASE_PROJECT_ID", "proj-1")
    config.settings.cache_clear()

    def _boom(*a, **k):
        raise AssertionError("Steel must not be touched when it is not selected")
    for name in ("create_session", "close_session", "live_view_url",
                 "session_status", "session_connect_info"):
        monkeypatch.setattr(steel, name, _boom)

    http = _Http((200, {"id": "bb-1", "connectUrl": "wss://bb/connect"}))
    # Browserbase posts positionally; adapt to the same recorder.
    monkeypatch.setattr(httpx, "post",
                        lambda url, **kw: http("POST", url, **kw))
    sess = bb.create_session()
    assert sess["mode"] == "browserbase" and sess["id"] == "bb-1"
    assert http.calls[0]["url"] == "https://api.browserbase.com/v1/sessions"
    assert http.calls[0]["headers"]["X-BB-API-Key"] == "bb-key"


def test_no_provider_configured_still_yields_the_local_descriptor(monkeypatch):
    monkeypatch.setenv("ELLIS_BROWSER_PROVIDER", "steel")   # asked for, absent
    config.settings.cache_clear()
    http = _http(monkeypatch)
    sess = bb.create_session()
    assert sess["mode"] == "local" and sess["id"].startswith("local-")
    assert bb.live_view_url(sess["id"]) is None
    assert http.calls == []


def test_handoff_contract_is_unchanged_when_steel_serves_the_view(monkeypatch):
    _cloud(monkeypatch)
    monkeypatch.setattr(steel, "live_view_url", lambda sid: "https://app.steel.dev/v")
    h = bb.create_handoff(kind="captcha", reason="portal asked", case_id="c1",
                          session_id="sess-abc123")
    # The client vocabulary is frozen: same modes, same fields, same order of
    # meaning — only the provider behind the URL changed.
    assert h.mode == "browserbase_liveview"
    assert h.as_dict()["url"] == "https://app.steel.dev/v"
    assert set(h.as_dict()) == {"kind", "reason", "mode", "token", "expires_at", "url"}


def test_observability_scrub_redacts_steel_live_view_urls():
    """A live-view URL is a key to the applicant's session. The scrubber knew
    only one vendor's URL shape; a second provider must not walk past it."""
    from app.observability import scrub
    out = str(scrub({"msg": "handoff at https://app.steel.dev/sessions/sess-abc123"}))
    assert "app.steel.dev/sessions" not in out
    # Self-hosted viewers carry no vendor name at all — the route shape is the tell.
    out = str(scrub({"msg": "handoff at http://localhost:3000/v1/sessions/debug"}))
    assert "localhost:3000/v1/sessions" not in out


def test_remote_mode_vocabulary_covers_both_providers():
    assert bb.is_remote_mode("browserbase") and bb.is_remote_mode("steel")
    assert not bb.is_remote_mode("local") and not bb.is_remote_mode("")
