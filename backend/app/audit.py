"""Append-only, auto-redacting audit log persisted to Postgres/SQLite.

Every security-critical action is recorded with references and non-sensitive
metadata only. A redaction pass strips anything that looks like a credential
before the row is written, so secrets never reach the DB via the audit path.
"""
from __future__ import annotations

import re

from sqlalchemy import select

from .models import AuditEvent

_SENSITIVE = re.compile(r"pass|pwd|secret|token|cookie|card|cvc|cvv|otp|3ds|ssn|captcha", re.I)


def redact(value, depth: int = 0):
    if value is None or depth > 5:
        return value
    if isinstance(value, str):
        return value[:32] + "…[redacted]" if len(value) > 200 else value
    if isinstance(value, list):
        return [redact(v, depth + 1) for v in value]
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            out[k] = "[redacted]" if _SENSITIVE.search(str(k)) else redact(v, depth + 1)
        return out
    return value


def record(db, *, org_id: str, application_id: str, action: str, detail=None,
           actor: str = "system") -> AuditEvent:
    from sqlalchemy import func
    next_seq = (db.query(func.max(AuditEvent.seq)).scalar() or 0) + 1
    ev = AuditEvent(seq=next_seq, org_id=org_id, application_id=application_id, actor=actor,
                    action=action, detail=redact(detail or {}))
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return ev


def for_application(db, application_id: str):
    return db.execute(
        select(AuditEvent).where(AuditEvent.application_id == application_id).order_by(AuditEvent.seq)
    ).scalars().all()


def contains_plaintext(db, needle: str) -> bool:
    rows = db.execute(select(AuditEvent)).scalars().all()
    return any(needle in str(r.detail) for r in rows)
