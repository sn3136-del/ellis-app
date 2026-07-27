"""Exact-amount payment authorization (brief §6, §23).

Payment is the ONLY routine separate approval. Before any real payment the
applicant sees the exact amount, currency, payee, fee breakdown, refundability
and source, then presses "Confirm and continue to secure payment". The
resulting PaymentAuthorization covers ONLY that displayed amount + currency +
payee: any change invalidates it and requires a fresh confirmation. An
authorization is consumed at most once. Card details are entered by the
applicant in the embedded secure browser — or, only at their explicit request
for a specific payment, provided once (vault-transported, never persisted or
logged) so Ellis can fill the official payment form; the portal's own payment
confirmation click always stays with the applicant.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from . import models, audit


class PaymentNotAuthorized(Exception):
    """No valid exact-amount authorization for the requested payment."""


def authorize(db, *, app_row, principal_user: str, amount_cents: int, currency: str,
              government_fee_cents: int = 0, service_fee_cents: int = 0,
              payee: str = "", refundability: str = "", fee_source_url: str = "",
              fee_version: int = 0, reason: str = "") -> models.PaymentAuthorization:
    """Record the applicant's confirmation of one exact amount. Supersedes any
    prior still-authorized row for the case (a case pays one fee at a time)."""
    if amount_cents <= 0:
        raise ValueError("amount_cents must be positive")
    if not currency:
        raise ValueError("currency is required")
    for prior in _authorized_rows(db, app_row.id):
        prior.status = "superseded"
    row = models.PaymentAuthorization(
        org_id=app_row.org_id, application_id=app_row.id,
        amount_cents=int(amount_cents), currency=currency,
        government_fee_cents=int(government_fee_cents),
        service_fee_cents=int(service_fee_cents), payee=payee[:200],
        refundability=refundability[:200], fee_source_url=fee_source_url[:500],
        fee_version=int(fee_version), reason=reason[:300],
        status="authorized", approved_by=principal_user)
    db.add(row)
    db.commit()
    audit.record(db, org_id=app_row.org_id, application_id=app_row.id,
                 action="payment_authorized",
                 detail={"amount_cents": row.amount_cents, "currency": row.currency,
                         "payee": row.payee, "fee_version": row.fee_version},
                 actor=principal_user)
    return row


def valid_authorization(db, application_id: str, amount_cents: int,
                        currency: str) -> models.PaymentAuthorization | None:
    """The authorized, unconsumed row matching the EXACT amount + currency."""
    for row in _authorized_rows(db, application_id):
        if row.amount_cents == int(amount_cents) and row.currency == currency:
            return row
    return None


def invalidate_mismatched(db, application_id: str, amount_cents: int, currency: str,
                          *, org_id: str = "") -> int:
    """Invalidate every still-authorized row that does NOT match the current
    exact amount — called whenever the fee is (re)discovered or reverified.
    Returns how many were invalidated."""
    n = 0
    for row in _authorized_rows(db, application_id):
        if row.amount_cents != int(amount_cents) or row.currency != currency:
            row.status = "invalidated"
            row.invalidated_reason = (
                f"amount changed to {amount_cents} {currency} "
                f"(was {row.amount_cents} {row.currency})")
            n += 1
    if n:
        db.commit()
        audit.record(db, org_id=org_id, application_id=application_id,
                     action="payment_authorization_invalidated",
                     detail={"count": n, "new_amount_cents": int(amount_cents),
                             "new_currency": currency},
                     actor="ellis")
    return n


def consume(db, authorization_id: str) -> models.PaymentAuthorization:
    row = db.get(models.PaymentAuthorization, authorization_id)
    if row is None or row.status != "authorized":
        raise PaymentNotAuthorized("authorization missing or no longer valid")
    row.status = "consumed"
    row.consumed_at = datetime.now(timezone.utc)
    db.commit()
    audit.record(db, org_id=row.org_id, application_id=row.application_id,
                 action="payment_authorization_consumed",
                 detail={"amount_cents": row.amount_cents, "currency": row.currency},
                 actor="ellis")
    return row


def require(db, application_id: str, amount_cents: int, currency: str) -> models.PaymentAuthorization:
    row = valid_authorization(db, application_id, amount_cents, currency)
    if row is None:
        raise PaymentNotAuthorized(
            f"no valid authorization for exactly {amount_cents} {currency} — "
            "the applicant must confirm the displayed amount first")
    return row


def to_dict(row: models.PaymentAuthorization | None) -> dict:
    if row is None:
        return {"authorized": False}
    return {"authorized": row.status == "authorized", "id": row.id,
            "amount_cents": row.amount_cents, "currency": row.currency,
            "government_fee_cents": row.government_fee_cents,
            "service_fee_cents": row.service_fee_cents, "payee": row.payee,
            "refundability": row.refundability, "fee_source_url": row.fee_source_url,
            "fee_version": row.fee_version, "reason": row.reason,
            "status": row.status,
            "approved_at": row.approved_at.isoformat() if row.approved_at else None}


def _authorized_rows(db, application_id: str):
    return db.execute(select(models.PaymentAuthorization).where(
        models.PaymentAuthorization.application_id == application_id,
        models.PaymentAuthorization.status == "authorized",
    ).order_by(models.PaymentAuthorization.approved_at.desc())).scalars().all()
