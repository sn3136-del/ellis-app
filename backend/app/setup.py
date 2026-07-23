"""Trip.com first-run administrator setup (Phase 7).

Collects tenant + provider configuration through a secure wizard. Provider
credentials are stored ONLY in the backend vault; the DB keeps just an opaque
vault ref + a redacted fingerprint per component. A saved credential is never
returned to any client. An email sender address alone is never sufficient — a
valid SMTP/API credential is required. Rotation and revocation are supported,
and production startup is blocked while default credentials are in place.
"""
from __future__ import annotations

import hashlib
import re

from . import models, audit, vault
from .providers.email import EmailConfig, config_valid, get_provider

# Defense-in-depth: even though secrets have dedicated vaulted fields, strip any
# secret-looking key a client might nest into the free-form config dicts
# (google/trip/base_urls/branding) so a credential can never land in the
# non-secret config that redacted_status returns.
_SENSITIVE_KEY = re.compile(r"secret|password|passwd|token|api[_-]?key|credential|private[_-]?key", re.I)


def _sanitize_config(obj):
    if isinstance(obj, dict):
        return {k: _sanitize_config(v) for k, v in obj.items() if not _SENSITIVE_KEY.search(str(k))}
    if isinstance(obj, list):
        return [_sanitize_config(v) for v in obj]
    return obj

# Secret components (vault-stored). Everything else is non-secret config.
SECRET_COMPONENTS = (
    "kimi_api_key", "browserbase_api_key", "google_service_account_json",
    "email_credential", "trip_client_secret", "trip_webhook_secret",
)


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def _row(db, org_id: str) -> models.TenantSetup:
    row = db.get(models.TenantSetup, org_id)
    if not row:
        row = models.TenantSetup(org_id=org_id)
        db.add(row)
        db.flush()
    return row


def _email_config_from(row: models.TenantSetup, *, credential: str = "") -> EmailConfig:
    e = (row.config or {}).get("email", {})
    return EmailConfig(provider=e.get("provider", "none"), sender=e.get("sender", ""),
                       reply_to=e.get("reply_to", ""), host=e.get("host", ""),
                       port=int(e.get("port", 587) or 587), username=e.get("username", ""),
                       api_endpoint=e.get("api_endpoint", ""), credential=credential)


def save_setup(db, *, org_id: str, actor: str, payload: dict) -> dict:
    """Persist non-secret config + vault every provided secret. Returns the
    redacted status (never any secret value). Raises ValueError on invalid input."""
    row = _row(db, org_id)
    cfg = dict(row.config or {})

    # Non-secret top-level fields.
    if "tenant_name" in payload:
        row.tenant_name = payload["tenant_name"]
    if "admin_email" in payload:
        row.admin_email = payload["admin_email"]
    for key in ("data_region", "retention_days", "base_urls", "branding", "google", "trip"):
        if key in payload and payload[key] is not None:
            # Sanitize free-form dicts so a nested secret can never be stored as
            # non-secret config (dedicated vaulted fields exist for real secrets).
            cfg[key] = _sanitize_config(payload[key]) if isinstance(payload[key], (dict, list)) else payload[key]

    # Email config (non-secret parts) + credential validation.
    email_in = payload.get("email")
    if email_in is not None:
        email_cfg = dict(cfg.get("email", {}))
        for k in ("provider", "sender", "reply_to", "host", "port", "username", "api_endpoint"):
            if k in email_in:
                email_cfg[k] = email_in[k]
        cfg["email"] = email_cfg
        # Determine the effective credential: newly supplied, or already stored.
        new_cred = payload.get("email_credential")
        has_stored = bool(row.credential_refs.get("email_credential"))
        effective = EmailConfig(
            provider=email_cfg.get("provider", "none"), sender=email_cfg.get("sender", ""),
            host=email_cfg.get("host", ""), api_endpoint=email_cfg.get("api_endpoint", ""),
            credential=(new_cred if new_cred else ("<stored>" if has_stored else "")))
        # A sender address alone is not sufficient — require real credentials.
        if email_cfg.get("provider") not in ("none", "", "local"):
            ok, reason = config_valid(effective)
            if not ok:
                raise ValueError(f"email configuration invalid: {reason}")
        elif email_cfg.get("sender") and email_cfg.get("provider") in ("none", ""):
            raise ValueError("an email sender address alone is not sufficient; "
                             "configure SMTP or an API provider with credentials")

    row.config = cfg

    # Vault every provided secret; keep only ref + fingerprint.
    refs = dict(row.credential_refs or {})
    fps = dict(row.credential_fingerprints or {})
    for comp in SECRET_COMPONENTS:
        val = payload.get(comp)
        if val:
            stored = vault.store(val, meta={"component": comp, "org": org_id})
            refs[comp] = stored["ref"]
            fps[comp] = fingerprint(val)
    row.credential_refs = refs
    row.credential_fingerprints = fps

    # Setup is complete when the essentials are present: tenant, admin, a working
    # email provider with credentials, and the Kimi key.
    row.setup_complete = _is_complete(row)
    if row.setup_complete and not row.completed_by:
        row.completed_by = actor
    db.commit()
    audit.record(db, org_id=org_id, application_id="", action="tenant_setup_saved",
                 detail={"components": [c for c in SECRET_COMPONENTS if c in refs],
                         "setup_complete": row.setup_complete}, actor=actor)
    return redacted_status(db, org_id)


def _is_complete(row: models.TenantSetup) -> bool:
    email = (row.config or {}).get("email", {})
    email_ok = email.get("provider") in ("smtp", "api") and bool(
        row.credential_refs.get("email_credential"))
    return bool(row.tenant_name and row.admin_email and email_ok
                and row.credential_refs.get("kimi_api_key"))


def missing_components(row: models.TenantSetup) -> list[str]:
    miss = []
    if not row.tenant_name:
        miss.append("tenant_name")
    if not row.admin_email:
        miss.append("admin_email")
    if not row.credential_refs.get("kimi_api_key"):
        miss.append("kimi_api_key")
    email = (row.config or {}).get("email", {})
    if email.get("provider") not in ("smtp", "api"):
        miss.append("email_provider")
    if not row.credential_refs.get("email_credential"):
        miss.append("email_credential")
    return miss


def redacted_status(db, org_id: str) -> dict:
    """Configuration status with fingerprints only — NEVER a credential value."""
    row = db.get(models.TenantSetup, org_id)
    if not row:
        return {"setup_complete": False, "configured": {}, "config": {},
                "missing": ["tenant_name", "admin_email", "kimi_api_key",
                            "email_provider", "email_credential"]}
    configured = {}
    for comp in SECRET_COMPONENTS:
        configured[comp] = {"configured": comp in row.credential_refs,
                            "fingerprint": row.credential_fingerprints.get(comp)}
    return {
        "tenant_name": row.tenant_name,
        "admin_email": row.admin_email,
        "config": row.config,               # non-secret only
        "configured": configured,           # per-secret {configured, fingerprint}
        "setup_complete": row.setup_complete,
        "missing": missing_components(row),
    }


def test_component(db, *, org_id: str, component: str) -> dict:
    """Non-destructive connection/readiness test. Reveals the stored credential
    only inside the backend, returns a boolean + redacted note (never a value)."""
    row = db.get(models.TenantSetup, org_id)
    if not row or component not in row.credential_refs:
        return {"component": component, "ok": False, "status": "unconfigured"}
    try:
        secret = vault.reveal(row.credential_refs[component])
    except KeyError:
        return {"component": component, "ok": False, "status": "vault_ref_missing"}
    if component == "email_credential":
        cfg = _email_config_from(row, credential=secret)
        return {"component": component, **get_provider(cfg).verify()}
    # For non-email secrets, verify presence + basic format only here; full live
    # verification happens when the backend runs with these creds in Secret
    # Manager (we never place a provider key in the client to test it).
    fmt_ok = len(secret) >= 8
    return {"component": component, "ok": fmt_ok,
            "status": "configured" if fmt_ok else "credential_too_short",
            "fingerprint": row.credential_fingerprints.get(component)}


def send_test_email(db, *, org_id: str, to: str, recorder=None) -> dict:
    """Send a test email through the configured provider and report REAL delivery.
    Without a transactional provider + credentials it reports that real delivery
    could not be verified — it never claims a message was delivered when it wasn't."""
    row = db.get(models.TenantSetup, org_id)
    if not row:
        return {"ok": False, "status": "unconfigured", "delivered": False}
    cred = ""
    if row.credential_refs.get("email_credential"):
        try:
            cred = vault.reveal(row.credential_refs["email_credential"])
        except KeyError:
            cred = ""
    cfg = _email_config_from(row, credential=cred)
    ok, reason = config_valid(cfg)
    if not ok and cfg.provider in ("none", ""):
        return {"ok": False, "status": "no_transactional_provider", "delivered": False,
                "note": "configure SMTP or an API provider with credentials to send real email"}
    if not ok:
        return {"ok": False, "status": "invalid_config", "delivered": False, "note": reason}
    result = get_provider(cfg, recorder=recorder).send(
        to=to, subject="Ellis test email",
        body="This is a test email from Ellis confirming your email configuration.")
    audit.record(db, org_id=org_id, application_id="", action="setup_test_email",
                 detail={"ok": result.get("ok"), "mode": result.get("mode"),
                         "delivered": result.get("delivered")})
    return result


def rotate_component(db, *, org_id: str, component: str, new_value: str, actor: str) -> dict:
    if component not in SECRET_COMPONENTS:
        raise ValueError("unknown secret component")
    if not new_value:
        raise ValueError("a new value is required to rotate")
    row = _row(db, org_id)
    refs = dict(row.credential_refs or {})
    if component in refs:
        try:
            vault.rotate(refs[component], new_value)
        except KeyError:
            refs[component] = vault.store(new_value)["ref"]
    else:
        refs[component] = vault.store(new_value)["ref"]
    row.credential_refs = refs
    fps = dict(row.credential_fingerprints or {})
    fps[component] = fingerprint(new_value)
    row.credential_fingerprints = fps
    db.commit()
    audit.record(db, org_id=org_id, application_id="", action="setup_rotate_credential",
                 detail={"component": component}, actor=actor)
    return {"component": component, "rotated": True, "fingerprint": fps[component]}


def revoke_component(db, *, org_id: str, component: str, actor: str) -> dict:
    row = _row(db, org_id)
    refs = dict(row.credential_refs or {})
    fps = dict(row.credential_fingerprints or {})
    if component in refs:
        try:
            vault.destroy(refs[component])
        except Exception:
            pass
        refs.pop(component, None)
        fps.pop(component, None)
    row.credential_refs = refs
    row.credential_fingerprints = fps
    row.setup_complete = _is_complete(row)
    db.commit()
    audit.record(db, org_id=org_id, application_id="", action="setup_revoke_credential",
                 detail={"component": component}, actor=actor)
    return {"component": component, "revoked": True}


# --- production startup guard -----------------------------------------------
_DEFAULTS = {
    "ELLIS_DEV_TOKEN": "dev-token",
    "ELLIS_ADMIN_TOKEN": "admin-token",
    "ELLIS_VAULT_PASSPHRASE": "local-dev-passphrase",
    "ELLIS_ACTION_SECRET": "local-action-secret",
}


def production_preflight() -> dict:
    """Refuse to run in production/staging with default credentials in place."""
    from .config import settings
    s = settings()
    blockers = []
    if not s.production_mode:
        return {"production_mode": False, "ok": True, "blockers": []}
    if s.dev_api_token == _DEFAULTS["ELLIS_DEV_TOKEN"]:
        blockers.append("default ELLIS_DEV_TOKEN in use")
    if s.admin_token == _DEFAULTS["ELLIS_ADMIN_TOKEN"]:
        blockers.append("default ELLIS_ADMIN_TOKEN in use")
    if s.vault_passphrase == _DEFAULTS["ELLIS_VAULT_PASSPHRASE"]:
        blockers.append("default ELLIS_VAULT_PASSPHRASE in use")
    if s.action_token_secret == _DEFAULTS["ELLIS_ACTION_SECRET"]:
        blockers.append("default ELLIS_ACTION_SECRET in use")
    if not s.clerk_secret_key:
        blockers.append("production auth (CLERK_SECRET_KEY) not configured")
    return {"production_mode": True, "ok": not blockers, "blockers": blockers}
