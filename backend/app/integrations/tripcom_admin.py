"""Trip.com connector administration (Phase 15, partial — honest scope).

Adds on top of the sandbox contract (tripcom.py): persistent outbound webhook
deliveries with retry + dead-letter, admin REPLAY, and an integration-health
report. Configuration (base URLs, client id, secrets) comes from the tenant
setup (Phase 7) — secrets stay in the vault.

HONEST LABELING: this remains the LOCAL SANDBOX CONTRACT. It must not be called
"Trip.com production" until Trip.com supplies its actual specifications and
credentials and the contract tests pass against their environment — health
reports carry that label explicitly.
"""
from __future__ import annotations

import json

from sqlalchemy import select

from .. import models, audit, vault
from . import tripcom

MAX_ATTEMPTS = 3
INTEGRATION_LABEL = ("local sandbox contract — NOT Trip.com production "
                     "(awaiting Trip.com's real specifications and credentials)")


def _trip_config(db, org_id: str) -> dict:
    row = db.get(models.TenantSetup, org_id)
    cfg = ((row.config or {}).get("trip") or {}) if row else {}
    secret = ""
    if row and (row.credential_refs or {}).get("trip_webhook_secret"):
        try:
            secret = vault.reveal(row.credential_refs["trip_webhook_secret"])
        except KeyError:
            secret = ""
    return {"sandbox_base_url": cfg.get("sandbox_base_url", ""),
            "base_url": cfg.get("base_url", ""),
            "client_id": cfg.get("client_id", ""),
            "webhook_secret": secret}


def queue_event(db, *, org_id: str, event: dict) -> models.WebhookDelivery:
    """Persist one outbound event for delivery. The endpoint is resolved at
    delivery time from tenant config (sandbox first; production only when
    Trip.com's real environment exists)."""
    row = models.WebhookDelivery(org_id=org_id, event_type=event.get("type", "unknown"),
                                 payload=event, status="pending")
    db.add(row)
    db.commit()
    audit.record(db, org_id=org_id, application_id="", action="tripcom_event_queued",
                 detail={"event_type": row.event_type})
    return row


def _default_transport(endpoint: str, headers: dict, body: bytes) -> dict:  # pragma: no cover
    import httpx
    try:
        r = httpx.post(endpoint, headers=headers, content=body, timeout=15)
        return {"ok": 200 <= r.status_code < 300, "status": r.status_code}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": str(e)[:60]}


def process_deliveries(db, *, org_id: str, transport=None,
                       max_attempts: int = MAX_ATTEMPTS) -> dict:
    """Attempt pending/failed deliveries. Without a configured endpoint+secret
    the delivery is honestly marked 'unconfigured' (never fake-delivered).
    `transport(endpoint, headers, body)->{ok,...}` is injectable for tests."""
    cfg = _trip_config(db, org_id)
    endpoint = cfg["sandbox_base_url"] or ""
    # Include 'unconfigured' so events queued BEFORE the tenant configured the
    # sandbox endpoint are retried once it is configured (never silently stuck).
    rows = db.execute(select(models.WebhookDelivery).where(
        models.WebhookDelivery.org_id == org_id,
        models.WebhookDelivery.status.in_(("pending", "failed", "unconfigured")))).scalars().all()
    delivered = failed = dead = unconfigured = 0
    for row in rows:
        if not endpoint or not cfg["webhook_secret"]:
            row.status = "unconfigured"
            row.last_error = "no sandbox endpoint / webhook secret configured"
            unconfigured += 1
            continue
        body = json.dumps(row.payload, sort_keys=True).encode()
        client = tripcom.SandboxClient(cfg["webhook_secret"], endpoint)
        headers = client.signed_headers(row.payload)
        send = transport or _default_transport
        try:
            result = send(endpoint.rstrip("/") + "/webhooks/ellis", headers, body)
        except Exception as e:  # noqa: BLE001
            result = {"ok": False, "detail": str(e)[:60]}
        row.attempts += 1
        row.endpoint = endpoint
        if result.get("ok"):
            row.status = "delivered"
            delivered += 1
        else:
            row.last_error = str(result.get("detail", result.get("status", "")))[:200]
            if row.attempts >= max_attempts:
                row.status = "dead"
                dead += 1
            else:
                row.status = "failed"
                failed += 1
    db.commit()
    return {"processed": len(rows), "delivered": delivered, "failed": failed,
            "dead_lettered": dead, "unconfigured": unconfigured}


def replay(db, *, org_id: str, delivery_id: str, actor: str) -> models.WebhookDelivery:
    """Admin replay: clone the payload into a NEW delivery (fresh signature at
    send time), linked via replay_of. The original row is never mutated."""
    src = db.get(models.WebhookDelivery, delivery_id)
    if not src or src.org_id != org_id:
        raise KeyError("delivery not found")
    row = models.WebhookDelivery(org_id=org_id, event_type=src.event_type,
                                 payload=src.payload, status="pending",
                                 replay_of=src.id)
    db.add(row)
    db.commit()
    audit.record(db, org_id=org_id, application_id="", action="tripcom_delivery_replayed",
                 detail={"delivery_id": delivery_id, "new_delivery_id": row.id}, actor=actor)
    return row


def list_deliveries(db, org_id: str, *, status: str = "") -> list[dict]:
    q = select(models.WebhookDelivery).where(models.WebhookDelivery.org_id == org_id)
    if status:
        q = q.where(models.WebhookDelivery.status == status)
    return [{"id": r.id, "event_type": r.event_type, "status": r.status,
             "attempts": r.attempts, "last_error": r.last_error,
             "replay_of": r.replay_of, "endpoint": r.endpoint}
            for r in db.execute(q.order_by(models.WebhookDelivery.created_at)).scalars().all()]


def health(db, org_id: str) -> dict:
    """Integration health: config presence (fingerprints only), delivery stats,
    and the honest contract label."""
    cfg = _trip_config(db, org_id)
    row = db.get(models.TenantSetup, org_id)
    fps = (row.credential_fingerprints or {}) if row else {}
    counts: dict[str, int] = {}
    for r in db.execute(select(models.WebhookDelivery).where(
            models.WebhookDelivery.org_id == org_id)).scalars().all():
        counts[r.status] = counts.get(r.status, 0) + 1
    configured = bool(cfg["sandbox_base_url"] and cfg["webhook_secret"])
    return {
        "contract": tripcom.CONTRACT_VERSION,
        "label": INTEGRATION_LABEL,
        "is_tripcom_production": False,
        "config": {
            "sandbox_base_url": cfg["sandbox_base_url"],
            "production_base_url": cfg["base_url"],
            "client_id": cfg["client_id"],
            "webhook_secret_configured": bool(cfg["webhook_secret"]),
            "webhook_secret_fingerprint": fps.get("trip_webhook_secret"),
            "client_secret_fingerprint": fps.get("trip_client_secret"),
        },
        "ready_for_sandbox_delivery": configured,
        "deliveries": counts,
    }
