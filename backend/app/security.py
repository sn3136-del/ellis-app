"""Authentication, tenant isolation, and short-lived action (step-up) tokens.

Dev mode accepts a bearer token and a caller-supplied org/user (for local +
tests). Production verifies Clerk session JWTs (activation: set CLERK_SECRET_KEY
and implement `verify_clerk`). Object-level authorization is enforced by
comparing the resource's org_id to the caller's principal in every route.
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
    x_org_id: str = Header(default=""),
    x_user_id: str = Header(default=""),
    x_role: str = Header(default=""),
) -> Principal:
    s = settings()
    token = authorization.replace("Bearer ", "").strip()
    if s.clerk_secret_key:
        # Production path — verify a Clerk session token.
        return verify_clerk(token)
    # Dev path — a shared dev token plus explicit org/user headers. The admin
    # role is granted only when the caller presents the dedicated admin token
    # (ELLIS_ADMIN_TOKEN), never merely by asserting x_role.
    _require(token == s.dev_api_token or token == s.admin_token, 401, "invalid token")
    _require(bool(x_org_id and x_user_id), 401, "missing org/user")
    role = "admin" if (token == s.admin_token and s.admin_token) else "applicant"
    return Principal(org_id=x_org_id, user_id=x_user_id, role=role)


def require_admin(principal: Principal):
    """Administrator-only actions (adapter approval/activation/kill/rollback)."""
    _require(principal.role == "admin", 403, "administrator role required")


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
