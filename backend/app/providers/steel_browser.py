"""Steel.dev browser sessions — a second, self-hostable source for the isolated
CDP session Ellis drives on an applicant's behalf.

Why a second provider exists at all: Steel is Apache-2.0 and self-hostable, so a
deployment that must keep an applicant's portal session on its own
infrastructure — or that simply loses its Browserbase plan mid-filing-season —
still has a real, isolated, CDP-shaped session. Both providers hand back the
same {id, mode, connect_url} shape, so nothing above this module changes.

Why CAPTCHA solving and stealth are hard-wired OFF here, on every single
request, even though Steel offers both: this is a government filing. It must be
made from a clean, attributable session, and answering a challenge that asks
"are you a person?" is the applicant's own act. Ellis opens the window; the
human answers in it. So the cloud create body always carries
`solveCaptcha: false` and `stealthConfig.autoCaptchaSolving: false`,
`humanizeInteractions` is never turned on, and `skipFingerprintInjection` is
never sent true on either deployment — a request that quietly flipped one of
those would turn a filing assistant into an anti-detection tool. The tests
assert on the captured request body, not on this paragraph.

Honest degradation, exactly as elsewhere: unconfigured → `is_configured()` is
False and `create_session()` returns the same local descriptor the Browserbase
provider returns. A Steel error raises `SteelUnavailable` rather than inventing
a session id or a viewer URL, and `live_view_url()` returns None rather than
guessing a URL shape. The API key is never logged and never appears in an
error message; live-view URLs are minted per request and never persisted.

Two deployments, two different create bodies (this is not a detail — the OSS
server rejects the cloud-only keys):
  cloud  https://api.steel.dev  → timeout, solveCaptcha, stealthConfig,
                                  debugConfig, dimensions, useProxy, blockAds
  self-hosted (configured origin) → dimensions, blockAds,
                                  skipFingerprintInjection (TOP-LEVEL bool)
Routes are shared: POST /v1/sessions, POST /v1/sessions/{id}/release,
GET /v1/sessions/{id}. The live view differs: cloud exposes
GET /v1/sessions/{id}/live-details, the OSS server GET /v1/sessions/debug.
"""
from __future__ import annotations

import os
import secrets
from urllib.parse import urlparse

from ..config import settings

# The session mode this provider reports. It names the PROVIDER, because the
# value is persisted and audited: an audit trail that called a Steel session
# "browserbase" would be recording a provenance that never happened.
MODE = "steel"

CLOUD_BASE = "https://api.steel.dev"
CLOUD_HOST_SUFFIX = "steel.dev"

_TIMEOUT_SECONDS = 30

# How long one applicant's portal session may live. Human steps (a CAPTCHA, an
# emailed code, reading the fee, paying) happen inside it, so minutes are not
# enough. Same knob, same default as the Browserbase path — Steel counts this
# one in milliseconds.
SESSION_TIMEOUT_SECONDS = int(os.getenv("ELLIS_BROWSER_SESSION_TIMEOUT", "3600"))

# A modest viewport keeps the applicant's embedded live view close to 1:1.
VIEWPORT = {"width": 1280, "height": 900}

# Viewer URL fields, most specific first. Cloud answers with sessionViewer*,
# the OSS debug surface with debugger*/debugUrl. An unknown shape yields None,
# never a constructed URL.
_VIEWER_FIELDS = ("sessionViewerFullscreenUrl", "sessionViewerUrl",
                  "debuggerFullscreenUrl", "debuggerUrl", "debugUrl")

DEPLOYMENT_CLOUD = "cloud"
DEPLOYMENT_SELF_HOSTED = "self_hosted"


class SteelUnavailable(RuntimeError):
    """Steel could not open, find, or answer for a session. Raised instead of
    returning something session-shaped: a caller must never be handed an id or
    a connect URL that no provider actually issued."""


def _flag(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _base_url() -> str:
    """The origin every request goes to: the configured self-hosted (or private)
    origin when set, else Steel Cloud."""
    return (settings().steel_base_url or "").strip().rstrip("/") or CLOUD_BASE


def deployment() -> str:
    """DEPLOYMENT_CLOUD | DEPLOYMENT_SELF_HOSTED | '' (not configured).

    Decided by the origin that would actually answer, never by a config claim:
    an api.steel.dev origin is the cloud regardless of what else is set, and any
    other configured origin is somebody's own Steel server."""
    s = settings()
    base = (s.steel_base_url or "").strip().rstrip("/")
    if not base:
        # No origin configured: only a cloud key can make this reachable.
        return DEPLOYMENT_CLOUD if s.steel_api_key else ""
    host = (urlparse(base).hostname or "").lower()
    if host == CLOUD_HOST_SUFFIX or host.endswith("." + CLOUD_HOST_SUFFIX):
        return DEPLOYMENT_CLOUD
    return DEPLOYMENT_SELF_HOSTED


def is_configured() -> bool:
    """True only when this deployment can actually reach a Steel API: a cloud
    API key, or a self-hosted origin (the OSS server needs no key)."""
    return deployment() != ""


def _headers() -> dict:
    """Steel authenticates with its own header. The key is attached here and
    nowhere else, so it can never reach a log line, an audit row, or an LLM."""
    h = {"content-type": "application/json", "accept": "application/json"}
    key = settings().steel_api_key
    if key:
        h["steel-api-key"] = key
    return h


def _http_json(method: str, url: str, *, headers: dict,
               json: dict | None = None) -> tuple[int, dict]:
    """The single HTTP seam. Every network byte this provider sends passes
    through here — raw httpx with an explicit timeout, imported lazily so no
    module import ever opens a socket."""
    import httpx
    r = httpx.request(method, url, headers=headers, json=json,
                      timeout=_TIMEOUT_SECONDS)
    try:
        body = r.json()
    except Exception:  # noqa: BLE001 - a non-JSON body is an error body
        body = {}
    return r.status_code, (body if isinstance(body, dict) else {})


def _cloud_create_body() -> dict:
    """Steel Cloud's create body. The two safety flags are unconditional: they
    are not options an operator, an env var, or a caller can turn on."""
    body = {
        "timeout": SESSION_TIMEOUT_SECONDS * 1000,   # Steel counts milliseconds
        # --- the safety floor, sent explicitly on every session -------------
        # Steel's default for solveCaptcha is already false; it is sent anyway
        # so the wire — not a vendor default — is what guarantees it.
        "solveCaptcha": False,
        "stealthConfig": {
            "autoCaptchaSolving": False,
            # Synthetic "human" mouse movement is anti-detection dressing. A
            # government portal is entitled to see an ordinary browser.
            "humanizeInteractions": False,
            # Never true: skipping fingerprint injection is how a session hides
            # what it is. Ellis's sessions stay attributable.
            "skipFingerprintInjection": False,
        },
        # The applicant answers challenges with their own hands in this viewer,
        # so it must be interactive — that is the point of the handoff.
        "debugConfig": {"interactive": True},
        "dimensions": dict(VIEWPORT),
        "blockAds": True,
    }
    if _flag("STEEL_USE_PROXY"):
        # Some government portals refuse datacenter egress outright (Germany's
        # RK-Termin among them). Unlike the Browserbase path there is no
        # fallback ladder here: if the plan will not honor the proxy, create
        # FAILS rather than quietly opening the egress the portal rejects.
        body["useProxy"] = True
    return body


def _self_hosted_create_body() -> dict:
    """The OSS server's create body — deliberately smaller. It has no timeout,
    no solveCaptcha, no stealthConfig and no debugConfig; sending them is how a
    self-hosted deployment gets a 400. `skipFingerprintInjection` is a
    TOP-LEVEL bool there, and stays False for the same reason as in the cloud."""
    return {"dimensions": dict(VIEWPORT), "blockAds": True,
            "skipFingerprintInjection": False}


def _local_descriptor() -> dict:
    return {"id": "local-" + secrets.token_hex(6), "mode": "local",
            "connect_url": None}


def _is_local(session_id: str) -> bool:
    return not session_id or str(session_id).startswith("local-")


def create_session() -> dict:
    """Create an ISOLATED Steel session (one per applicant/case). Returns
    {id, mode, connect_url, deployment, proxied} for a live session, or the
    same local descriptor the Browserbase provider returns when Steel is not
    configured. Raises SteelUnavailable rather than returning a session that
    does not exist."""
    mode = deployment()
    if not mode:
        return _local_descriptor()
    body = (_cloud_create_body() if mode == DEPLOYMENT_CLOUD
            else _self_hosted_create_body())
    try:
        code, data = _http_json("POST", f"{_base_url()}/v1/sessions",
                                headers=_headers(), json=body)
    except Exception:  # noqa: BLE001 - transport internals never surface
        raise SteelUnavailable("Steel session API unreachable") from None
    if code >= 400:
        raise SteelUnavailable(f"Steel refused to open a session (HTTP {code})")
    session_id = str(data.get("id") or "")
    connect = str(data.get("websocketUrl") or "")
    if not session_id or not connect:
        # A 200 with nothing drivable in it is not a session. Returning it would
        # strand the applicant on a window that was never opened.
        raise SteelUnavailable("Steel returned no usable session")
    return {"id": session_id, "mode": MODE, "connect_url": connect,
            "deployment": mode,
            # Honest because there is no silent fallback: the flag was either
            # honored or the create above failed.
            "proxied": bool(body.get("useProxy"))}


def _get_json(url: str) -> dict:
    """GET that never raises: an unreachable provider is answered with {}, and
    the caller turns that into an honest 'unavailable', not a guess."""
    try:
        code, data = _http_json("GET", url, headers=_headers())
    except Exception:  # noqa: BLE001
        return {}
    return data if code < 400 else {}


def _viewer_url(data: dict) -> str | None:
    for field in _VIEWER_FIELDS:
        url = str(data.get(field) or "").strip()
        if url:
            return url
    return None


def live_view_url(session_id: str) -> str | None:
    """The applicant-facing viewer URL for a live session — the surface where
    CAPTCHA/OTP/payment/declaration are completed BY THE APPLICANT. Minted
    fresh per request, never persisted, never emailed. None when there is
    nothing real to show."""
    if _is_local(session_id) or not is_configured():
        return None
    base = _base_url()
    if deployment() == DEPLOYMENT_SELF_HOSTED:
        # The OSS server runs one session and exposes one debug surface for it.
        return _viewer_url(_get_json(f"{base}/v1/sessions/debug"))
    url = _viewer_url(_get_json(f"{base}/v1/sessions/{session_id}/live-details"))
    if url:
        return url
    # live-details is the documented surface, but the session record carries the
    # same viewer URL — one honest second try before telling the applicant no.
    return _viewer_url(_get_json(f"{base}/v1/sessions/{session_id}"))


def close_session(session_id: str) -> None:
    """Release a session at the provider. Best effort: a provider that cannot be
    reached must not turn cleanup in a caller's `finally` into an exception."""
    if _is_local(session_id) or not is_configured():
        return
    try:
        _http_json("POST", f"{_base_url()}/v1/sessions/{session_id}/release",
                   headers=_headers(), json={})
    except Exception:  # noqa: BLE001 - release is best effort
        pass


def session_status(session_id: str) -> str:
    """Steel's own status for a session ('live', 'released', 'failed'), '' when
    unknown. A session Ellis believes is open may already be gone — the
    applicant must never be sent to a window that no longer exists."""
    if _is_local(session_id) or not is_configured():
        return ""
    return str(_get_json(f"{_base_url()}/v1/sessions/{session_id}").get("status") or "")


def session_alive(session_id: str) -> bool:
    """False ONLY when Steel explicitly says the session is no longer live. An
    unknown status (provider unreachable) counts as alive, exactly as on the
    Browserbase path: a probe that cannot see the truth must never destroy a
    working session."""
    status = session_status(session_id).lower()
    if not status:
        return True
    return status == "live"


def session_connect_info(session_id: str) -> dict:
    """Re-attach info for an EXISTING session ({id, mode, connect_url}) so a
    later request can drive the SAME applicant portal session (resume after a
    question or a handoff). Raises if the session is gone or not live."""
    if _is_local(session_id) or not is_configured():
        raise SteelUnavailable("no live Steel session to re-attach")
    try:
        code, data = _http_json("GET", f"{_base_url()}/v1/sessions/{session_id}",
                                headers=_headers())
    except Exception:  # noqa: BLE001
        raise SteelUnavailable("Steel session API unreachable") from None
    if code >= 400:
        raise SteelUnavailable(f"Steel session {session_id} is unavailable (HTTP {code})")
    if str(data.get("status") or "").lower() != "live":
        raise SteelUnavailable(f"Steel session {session_id} is not live")
    connect = str(data.get("websocketUrl") or "")
    if not connect:
        raise SteelUnavailable("session has no connect URL")
    return {"id": session_id, "mode": MODE, "connect_url": connect}
