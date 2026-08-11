"""Isolated browser sessions + human Live View handoffs.

Production: Browserbase (isolated context per applicant/portal) driven by
Playwright, with Stagehand as a validated fallback (activation:
BROWSERBASE_API_KEY). Live View surfaces CAPTCHA/OTP/verification/identity/
payment/3-DS/declaration to the applicant.

Second source: Steel (Apache-2.0, self-hostable) serves the same CDP-shaped
session when ELLIS_BROWSER_PROVIDER=steel and Steel is actually configured. The
choice is a dispatch at the top of the lifecycle functions below and nothing
more — every Browserbase path here is untouched, and an unconfigured stack
still falls through to `local_handoff`. Neither provider will ever solve a
CAPTCHA or wear a stealth fingerprint; see providers/steel_browser.py.

Local fallback: a `local_handoff` descriptor the Electron client renders as an
instruction panel while the applicant completes the step on the real portal in
their own browser. Sensitive values are never logged, recorded, or sent to an
LLM in either mode.
"""
from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass

from ..config import settings
from . import steel_browser

HANDOFF_KINDS = {
    "captcha", "otp", "email_verification", "login_challenge", "identity",
    "payment", "three_ds", "personal_declaration",
    # The government form needs items only the applicant may complete in the
    # secure window (e.g. its own inline legal declaration, or fields the
    # released flow does not cover).
    "portal_form",
    # The applicant provides payment details IN ELLIS and Ellis fills them on
    # the official portal; the live view accompanies the ask so the applicant
    # can always see (or use) the portal's own payment page instead.
    "payment_credentials",
}


@dataclass
class LiveViewHandoff:
    kind: str
    reason: str
    # CLIENT vocabulary, not a vendor claim: it selects the embedded live-view
    # renderer vs the instruction panel. Which provider actually serves that
    # view is reported by capabilities()["browser_provider"] and by
    # active_provider() below. The strings are frozen — the Electron client and
    # the Node workflow both switch on them.
    mode: str            # "browserbase_liveview" | "local_handoff"
    token: str
    expires_at: float
    url: str | None = None

    def as_dict(self) -> dict:
        return {"kind": self.kind, "reason": self.reason, "mode": self.mode,
                "token": self.token, "expires_at": self.expires_at, "url": self.url}


# --- provider dispatch -------------------------------------------------------
# Steel is used only when the deployment asks for it BY NAME and Steel is
# actually reachable; anything else keeps the Browserbase behaviour below
# exactly as it was, and an unconfigured stack still lands on local_handoff.
# A provider is never inferred from a stray key: an operator who sets
# STEEL_API_KEY while ELLIS_BROWSER_PROVIDER is unset keeps Browserbase.
PROVIDER_STEEL = "steel"
PROVIDER_BROWSERBASE = "browserbase"
PROVIDER_LOCAL = "local_handoff"

# Session modes that mean "a real remote provider session with a live view",
# as opposed to the "local" descriptor. Callers that gate a live view on the
# stored mode should ask is_remote_mode() rather than compare to one vendor.
REMOTE_MODES = ("browserbase", "steel")


def is_remote_mode(mode: str) -> bool:
    return str(mode or "") in REMOTE_MODES


def _steel_active() -> bool:
    return ((settings().browser_provider or "").strip().lower() == PROVIDER_STEEL
            and steel_browser.is_configured())


def active_provider() -> str:
    """What a session opened right now would ACTUALLY run on:
    'steel' | 'browserbase' | 'local_handoff'."""
    if _steel_active():
        return PROVIDER_STEEL
    return PROVIDER_BROWSERBASE if settings().browserbase_api_key else PROVIDER_LOCAL


def is_configured() -> bool:
    # True when a real remote session (and therefore a live view) is available
    # from whichever provider this deployment selected.
    return _steel_active() or bool(settings().browserbase_api_key)


_BB_BASE = "https://api.browserbase.com/v1"

# How long one applicant's portal session may live. Human steps (CAPTCHA, an
# emailed code, reading the fee, paying) happen inside it, so minutes are not
# enough; the provider's per-project default is far shorter.
SESSION_TIMEOUT_SECONDS = int(os.getenv("ELLIS_BROWSER_SESSION_TIMEOUT", "3600"))


def create_session() -> dict:
    """Create an ISOLATED Browserbase session (one per applicant/case). Returns
    {id, connect_url} for a live session, or a local descriptor when unconfigured."""
    if _steel_active():
        return steel_browser.create_session()
    s = settings()
    if not s.browserbase_api_key:
        return {"id": "local-" + secrets.token_hex(6), "mode": "local", "connect_url": None}
    import httpx
    proj = s.browserbase_project_id or _default_project()
    # The applicant's in-progress portal session must outlive the human
    # steps: CAPTCHA, an emailed code, reading the fee, paying. A project's
    # DEFAULT session timeout is minutes — long enough to strand every
    # handoff and force a full form refill on the next resume — so the
    # lifetime is requested explicitly (keepAlive stops idle disconnects
    # from ending it). Plans that reject either option fall back honestly.
    headers = {"X-BB-API-Key": s.browserbase_api_key, "content-type": "application/json"}
    # A modest viewport keeps the applicant's embedded live view close to 1:1
    # — the provider's default is large and scales down to unreadable.
    viewport = {"browserSettings": {"viewport": {"width": 1280, "height": 900}}}
    # Some government portals refuse datacenter egress outright: Germany's
    # RK-Termin (service2.diplo.de, the booking system for 190+ German
    # missions) times out from a plain Browserbase session while loading
    # normally from an ordinary browser. Residential proxying is requested
    # FIRST so those portals are reachable at all; a plan without proxies
    # simply falls through to the next attempt, exactly as the timeout and
    # viewport options already do.
    proxied = {"proxies": True} if s.browserbase_proxies else {}
    attempts = ({"projectId": proj, "keepAlive": True,
                 "timeout": SESSION_TIMEOUT_SECONDS, **viewport, **proxied},
                {"projectId": proj, "keepAlive": True,
                 "timeout": SESSION_TIMEOUT_SECONDS, **viewport},
                {"projectId": proj, "keepAlive": True, "timeout": SESSION_TIMEOUT_SECONDS},
                {"projectId": proj, "keepAlive": True},
                {"projectId": proj})
    r = None
    used = {}
    for body in attempts:
        r = httpx.post(f"{_BB_BASE}/sessions", headers=headers, json=body, timeout=30)
        if r.status_code < 400:
            used = body
            break
    r.raise_for_status()
    data = r.json()
    # WHICH attempt won matters. The fallbacks drop `proxies`, so a portal that
    # refuses datacenter egress can be handed a datacenter session and answer
    # 403 — Thailand's TDAC did exactly that twice, with Cloudflare's own
    # Turnstile reporting Success and the request refused anyway (2026-08-03).
    # Silently degrading to the egress a portal rejects is indistinguishable
    # from the portal being down, so the result says what was actually opened.
    return {"id": data.get("id"), "mode": "browserbase",
            "connect_url": data.get("connectUrl"),
            "proxied": bool(used.get("proxies")),
            "proxies_requested": bool(proxied),
            "degraded": bool(proxied) and not used.get("proxies")}


def session_status(session_id: str) -> str:
    """The PROVIDER's own status for a session ('RUNNING', 'COMPLETED', …).
    '' when unknown. A session Ellis believes is open may already be gone —
    the applicant must never be sent to a window that no longer exists.

    Provider-native wording ('RUNNING' here, 'live' on Steel): read it through
    session_alive() rather than comparing strings across providers."""
    if _steel_active():
        return steel_browser.session_status(session_id)
    s = settings()
    if not s.browserbase_api_key or not session_id or session_id.startswith("local-"):
        return ""
    import httpx
    try:
        r = httpx.get(f"{_BB_BASE}/sessions/{session_id}",
                      headers={"X-BB-API-Key": s.browserbase_api_key}, timeout=15)
        if r.status_code == 200:
            return str(r.json().get("status") or "")
    except Exception:  # noqa: BLE001
        pass
    return ""


def session_alive(session_id: str) -> bool:
    """False ONLY when the provider explicitly reports the session is no longer
    running. An unknown status (provider unreachable, no API key) counts as
    alive: a probe that cannot see the truth must never destroy a working
    session — the caller's own connect attempt remains the real test."""
    if _steel_active():
        # Steel's own vocabulary, applied by Steel's own rule.
        return steel_browser.session_alive(session_id)
    status = session_status(session_id).upper()
    if not status:
        return True
    return status == "RUNNING"


def _default_project() -> str:
    s = settings()
    import httpx
    r = httpx.get(f"{_BB_BASE}/projects", headers={"X-BB-API-Key": s.browserbase_api_key}, timeout=30)
    r.raise_for_status()
    projs = r.json()
    return projs[0]["id"] if projs else ""


def close_session(session_id: str) -> None:
    # A provider switched mid-flight cannot release the OTHER provider's
    # sessions (the ids are opaque and look alike); those expire on their own
    # session timeout. Switching providers is an operator action between runs.
    if _steel_active():
        steel_browser.close_session(session_id)
        return
    s = settings()
    if not s.browserbase_api_key or session_id.startswith("local-"):
        return
    import httpx
    proj = s.browserbase_project_id or _default_project()
    try:
        httpx.post(f"{_BB_BASE}/sessions/{session_id}",
                   headers={"X-BB-API-Key": s.browserbase_api_key, "content-type": "application/json"},
                   json={"projectId": proj, "status": "REQUEST_RELEASE"}, timeout=30)
    except Exception:
        pass


def session_connect_info(session_id: str) -> dict:
    """Re-attach info for an EXISTING session ({id, mode, connect_url}) so a
    later request can drive the SAME applicant portal session (resume after a
    question/CAPTCHA). Raises if the session is gone or not running."""
    if _steel_active():
        return steel_browser.session_connect_info(session_id)
    s = settings()
    if not s.browserbase_api_key or session_id.startswith("local-"):
        raise RuntimeError("no live Browserbase session to re-attach")
    import httpx
    r = httpx.get(f"{_BB_BASE}/sessions/{session_id}",
                  headers={"X-BB-API-Key": s.browserbase_api_key}, timeout=30)
    r.raise_for_status()
    data = r.json()
    if str(data.get("status", "")).upper() not in ("RUNNING", "ACTIVE"):
        raise RuntimeError(f"session {session_id} is not running")
    connect = data.get("connectUrl")
    if not connect:
        raise RuntimeError("session has no connect URL")
    return {"id": session_id, "mode": "browserbase", "connect_url": connect}


def live_view_url(session_id: str) -> str | None:
    """Fetch the short-lived Live View (debugger) URL for a session. This is the
    surface the applicant uses for CAPTCHA/OTP/payment/declaration. Never emailed."""
    if _steel_active():
        return steel_browser.live_view_url(session_id)
    s = settings()
    if not s.browserbase_api_key or session_id.startswith("local-"):
        return None
    import httpx
    try:
        r = httpx.get(f"{_BB_BASE}/sessions/{session_id}/debug",
                      headers={"X-BB-API-Key": s.browserbase_api_key}, timeout=30)
        if r.status_code == 200:
            return r.json().get("debuggerFullscreenUrl") or r.json().get("debuggerUrl")
    except Exception:
        pass
    return None


def create_handoff(*, kind: str, reason: str, case_id: str, session_id: str | None = None,
                   ttl_seconds: int = 600) -> LiveViewHandoff:
    if kind not in HANDOFF_KINDS:
        raise ValueError(f"unknown handoff kind {kind}")
    token = secrets.token_urlsafe(24)
    if is_configured():
        # Bind the handoff to the case's live session; recording/logging for the
        # sensitive session is disabled at the session-config level in prod.
        url = live_view_url(session_id) if session_id else None
        return LiveViewHandoff(kind, reason, "browserbase_liveview", token,
                               time.time() + ttl_seconds, url=url)
    return LiveViewHandoff(kind, reason, "local_handoff", token, time.time() + ttl_seconds, url=None)


# Sensitive markers the handoff returns — only the RESULT, never the secret.
HUMAN_MARKERS = {
    "captcha": "HUMAN_SOLVED",
    "otp": "HUMAN_ENTERED",
    "email_verification": "HUMAN_VERIFIED",
    "payment": "HUMAN_PAID",
    "personal_declaration": "HUMAN_DECLARED",
}
