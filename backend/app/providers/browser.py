"""Isolated browser sessions + human Live View handoffs.

Production: Browserbase (isolated context per applicant/portal) driven by
Playwright, with Stagehand as a validated fallback (activation:
BROWSERBASE_API_KEY). Live View surfaces CAPTCHA/OTP/verification/identity/
payment/3-DS/declaration to the applicant.

Local fallback: a `local_handoff` descriptor the Electron client renders as an
instruction panel while the applicant completes the step on the real portal in
their own browser. Sensitive values are never logged, recorded, or sent to an
LLM in either mode.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

from ..config import settings

HANDOFF_KINDS = {
    "captcha", "otp", "email_verification", "login_challenge", "identity",
    "payment", "three_ds", "personal_declaration",
}


@dataclass
class LiveViewHandoff:
    kind: str
    reason: str
    mode: str            # "browserbase_liveview" | "local_handoff"
    token: str
    expires_at: float
    url: str | None = None

    def as_dict(self) -> dict:
        return {"kind": self.kind, "reason": self.reason, "mode": self.mode,
                "token": self.token, "expires_at": self.expires_at, "url": self.url}


def is_configured() -> bool:
    return bool(settings().browserbase_api_key)


_BB_BASE = "https://api.browserbase.com/v1"


def create_session() -> dict:
    """Create an ISOLATED Browserbase session (one per applicant/case). Returns
    {id, connect_url} for a live session, or a local descriptor when unconfigured."""
    s = settings()
    if not s.browserbase_api_key:
        return {"id": "local-" + secrets.token_hex(6), "mode": "local", "connect_url": None}
    import httpx
    proj = s.browserbase_project_id or _default_project()
    # keepAlive: the applicant's in-progress portal session must survive
    # handoff waits (CAPTCHA/OTP/payment) instead of expiring on idle. Plans
    # without keep-alive support fall back to a standard session.
    body = {"projectId": proj, "keepAlive": True}
    r = httpx.post(f"{_BB_BASE}/sessions",
                   headers={"X-BB-API-Key": s.browserbase_api_key, "content-type": "application/json"},
                   json=body, timeout=30)
    if r.status_code >= 400:
        r = httpx.post(f"{_BB_BASE}/sessions",
                       headers={"X-BB-API-Key": s.browserbase_api_key,
                                "content-type": "application/json"},
                       json={"projectId": proj}, timeout=30)
    r.raise_for_status()
    data = r.json()
    return {"id": data.get("id"), "mode": "browserbase", "connect_url": data.get("connectUrl")}


def _default_project() -> str:
    s = settings()
    import httpx
    r = httpx.get(f"{_BB_BASE}/projects", headers={"X-BB-API-Key": s.browserbase_api_key}, timeout=30)
    r.raise_for_status()
    projs = r.json()
    return projs[0]["id"] if projs else ""


def close_session(session_id: str) -> None:
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
