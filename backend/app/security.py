"""Authentication, tenant isolation, and short-lived action (step-up) tokens.

Local mode accepts a bearer token and caller-supplied org/user. Secure operator
mode requires a private admin token and binds the operator user to the server
configuration; a public reader token cannot assert an admin role. Clerk session
verification is a separate, currently unimplemented activation path. Object
authorization compares a resource's org_id with the caller's principal.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException

from .config import settings


@dataclass
class Principal:
    org_id: str
    user_id: str
    step_up: bool = False
    role: str = "applicant"   # applicant | admin (production: from the Clerk JWT)


def _require(cond: bool, code: int, msg: str):
    if not cond:
        raise HTTPException(status_code=code, detail=msg)


async def get_principal(
    authorization: str = Header(default=""),
    x_ellis_token: str = Header(default=""),
    x_org_id: str = Header(default=""),
    x_user_id: str = Header(default=""),
    x_role: str = Header(default=""),
) -> Principal:
    s = settings()
    # Deployed behind a password-gated proxy, the browser spends the
    # Authorization header on the site password, so the app's own token
    # travels in x-ellis-token. Locally the Bearer header is used as before;
    # whichever arrives is the token, and the same checks apply to both.
    token = (x_ellis_token.strip()
             or authorization.replace("Bearer ", "").strip())
    # An explicit public evaluation mode opens only the quality-control role.
    # The public client marker is not a secret or an authenticated identity.
    if getattr(s, "public_quality_control", False) and token == "public-quality-control":
        browser = hashlib.sha256(str(x_user_id or "visitor").encode()).hexdigest()[:16]
        return Principal(org_id="platform", user_id="public-qc-" + browser,
                         role="quality_tester")
    if s.clerk_secret_key:
        # Production path — verify a Clerk session token.
        return verify_clerk(token)
    if s.require_secure_admin and token == s.admin_token:
        # Public installations must never accept the bundled development
        # credential or let an operator invent the reviewer identity.
        _require(len(s.admin_token) >= 32 and s.admin_token != s.dev_api_token
                 and bool(s.admin_user_id), 401, "operator authentication unavailable")
    # Dev path — a shared dev token plus explicit org/user headers. The admin
    # role is granted only when the caller presents the dedicated admin token
    # (ELLIS_ADMIN_TOKEN), never merely by asserting x_role.
    _require(token == s.dev_api_token or token == s.admin_token, 401, "invalid token")
    _require(bool(x_org_id and x_user_id), 401, "missing org/user")
    role = "admin" if (token == s.admin_token and s.admin_token) else "applicant"
    if role == "admin" and s.require_secure_admin:
        x_user_id = s.admin_user_id
    return Principal(org_id=x_org_id, user_id=x_user_id, role=role)


def require_admin(principal: Principal):
    """Administrator-only actions (adapter approval/activation/kill/rollback)."""
    _require(principal.role == "admin", 403, "administrator role required")


def require_quality_control(principal: Principal):
    """Full Quality Control evaluation; other administrative features stay scoped."""
    _require(principal.role in {"admin", "quality_tester"}, 403,
             "quality-control access required")


def verify_clerk(token: str) -> Principal:  # pragma: no cover - activation stub
    # ACTIVATION: verify the Clerk JWT (JWKS), extract org_id + user_id.
    raise HTTPException(status_code=501, detail="Clerk verification not activated")


def require_owner(principal: Principal, resource_org_id: str):
    """Object-level authorization — reject cross-tenant access."""
    _require(principal.org_id == resource_org_id, 403, "not authorized for this resource")


# --- Short-lived signed action tokens (step-up for sensitive actions) --------
def issue_action_token(principal: Principal, action: str, application_id: str,
                       *, amount_cents: int | None = None, slot_id: str | None = None,
                       ttl_seconds: int = 300) -> str:
    payload = {
        "org": principal.org_id, "user": principal.user_id, "action": action,
        "app": application_id, "amount": amount_cents, "slot": slot_id,
        "exp": int(time.time()) + ttl_seconds, "nonce": hashlib.sha256(
            f"{application_id}{action}{time.time()}".encode()).hexdigest()[:16],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    sig = hmac.new(settings().action_token_secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    import base64
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=") + "." + sig


def verify_action_token(token: str, action: str, application_id: str) -> dict:
    import base64
    try:
        b64, sig = token.split(".")
        raw = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4)).decode()
        expected = hmac.new(settings().action_token_secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
        _require(hmac.compare_digest(sig, expected), 401, "bad action token signature")
        payload = json.loads(raw)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="malformed action token")
    _require(payload.get("exp", 0) > int(time.time()), 401, "action token expired")
    _require(payload.get("action") == action, 401, "action token mismatch")
    _require(payload.get("app") == application_id, 401, "action token application mismatch")
    return payload


PrincipalDep = Depends(get_principal)
