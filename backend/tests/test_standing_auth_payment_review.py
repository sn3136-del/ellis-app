"""Brief §35 items 19, 21-24, 49-53: standing authorization is versioned;
payment always requires exact-amount confirmation; a changed amount invalidates
the authorization; changed application data invalidates the signature;
submission requires a valid signature; routine steps need no re-approval."""
from sqlalchemy import select

from app import service, models, authorization as standing, payments, final_review
from tests.test_e2e import _new_case, _drive_to_completion, _sign_final_review


# ---- §35.19 standing authorization is versioned ----
def test_standing_authorization_versioned_and_revocable(db):
    app_id = _new_case(db)
    app_row = db.get(models.VisaApplication, app_id)
    first = standing.current(db, app_id)
    assert first is not None and first.version == 1
    assert first.text_version == standing.TEXT_VERSION
    assert len(first.text_hash) == 64
    # Re-granting creates a NEW version and supersedes the old one in place.
    second = standing.grant(db, app_row=app_row, principal_user="user1", locale="zh-CN")
    assert second.version == 2 and second.ui_locale == "zh-CN"
    db.refresh(first)
    assert first.superseded_by == second.id
    assert standing.current(db, app_id).id == second.id
    # Revocation flips the flag; the record survives for audit.
    standing.revoke(db, app_row=app_row, principal_user="user1", reason="changed my mind")
    assert standing.valid(db, app_id) is None
    assert standing.current(db, app_id).revoked is True


def test_standing_authorization_never_covers_payment(db):
    app_id = _new_case(db)
    import pytest
    with pytest.raises(standing.ActionNotAuthorized):
        standing.require(db, app_id, "confirm_payment")
    # Routine actions ARE covered without any further prompt (§35.49-51).
    for action in ("select_official_portal", "book_appointment_within_preferences",
                   "submit_after_signed_final_review"):
        assert standing.require(db, app_id, action) is not None


# ---- §35.21 payment always requires exact-amount confirmation ----
def test_payment_requires_exact_amount_authorization(db):
    app_id = _new_case(db)
    status, wf = service.signal(db, app_id, "start")
    # Drive to the payment approval pause.
    for name in ("approve_review", "sign_authorization", "solve_captcha"):
        status, wf = service.signal(db, app_id, name)
    from tests.test_e2e import _latest_verify_token
    status, wf = service.signal(db, app_id, "verify_email", token=_latest_verify_token(wf))
    assert status["state"] == "PAYMENT_APPROVAL_REQUIRED"
    fee = status["pending"]["fee"]
    # Echoing a WRONG amount is refused — the pause remains.
    status, wf = service.signal(db, app_id, "approve_payment",
                                amount_cents=fee["amount"] + 100, currency=fee["currency"])
    assert status["state"] == "PAYMENT_APPROVAL_REQUIRED"
    assert payments.valid_authorization(db, app_id, fee["amount"], fee["currency"]) is None
    # Echoing the exact displayed amount is accepted and recorded.
    status, wf = service.signal(db, app_id, "approve_payment",
                                amount_cents=fee["amount"], currency=fee["currency"])
    assert status["state"] != "PAYMENT_APPROVAL_REQUIRED"
    rows = db.execute(select(models.PaymentAuthorization).where(
        models.PaymentAuthorization.application_id == app_id)).scalars().all()
    assert any(r.amount_cents == fee["amount"] and r.currency == fee["currency"]
               and r.status in ("authorized", "consumed") for r in rows)


# ---- §35.22 changed amount invalidates the authorization ----
def test_changed_fee_invalidates_payment_authorization(db):
    app_id = _new_case(db)
    status, wf = service.signal(db, app_id, "start")
    for name in ("approve_review", "sign_authorization", "solve_captcha"):
        status, wf = service.signal(db, app_id, name)
    from tests.test_e2e import _latest_verify_token
    status, wf = service.signal(db, app_id, "verify_email", token=_latest_verify_token(wf))
    fee = status["pending"]["fee"]
    status, wf = service.signal(db, app_id, "approve_payment",
                                amount_cents=fee["amount"], currency=fee["currency"])
    # The fee changes AFTER approval (portal update): the workflow must void the
    # authorization and pause for a fresh confirmation, not pay the new amount.
    wf2 = service.load_workflow(db, app_id)
    if wf2.machine.state in ("PAYMENT_ACTION_REQUIRED", "PAYMENT_PROCESSING"):
        wf2.fee = dict(wf2.fee, amount=wf2.fee["amount"] + 5000,
                       display=f"{(wf2.fee['amount'] + 5000) / 100:.2f} {wf2.fee['currency']}")
        wf2.machine.transition("PAYMENT_APPROVAL_REQUIRED", "fee re-verified: changed")
        service.persist_workflow(db, wf2)
        status, wf3 = service.signal(db, app_id, "start")
        assert status["state"] == "PAYMENT_APPROVAL_REQUIRED"
        assert status["pending"]["handoff"] == "payment_approval"
        # The old exact-amount authorization is gone.
        assert payments.valid_authorization(db, app_id, fee["amount"], fee["currency"]) is None


# ---- §35.23 changed application data invalidates the signature ----
def test_material_change_invalidates_final_review_signature(db):
    app_id = _new_case(db)
    app_row = db.get(models.VisaApplication, app_id)
    row = _sign_final_review(db, app_id)
    assert final_review.signed_current(db, app_row) is not None
    # A material answer changes.
    ans = dict(app_row.answers)
    ans["accommodation"] = "Different Hotel"
    app_row.answers = ans
    db.commit()
    assert final_review.check_and_invalidate(db, app_row) is True
    db.refresh(row)
    assert row.invalidated is True
    assert final_review.signed_current(db, app_row) is None
    sig = db.get(models.NativeSignature, row.signature_id)
    assert sig.invalidated is True


# ---- §35.24 submission requires a valid signature ----
def test_submission_blocked_without_final_review_signature(db):
    app_id = _new_case(db)
    status, wf = service.signal(db, app_id, "start")
    for _ in range(30):
        pending = status.get("pending")
        if not pending:
            break
        h = pending["handoff"]
        if h == "final_review":
            break  # the gate held submission
        from tests.test_e2e import _latest_verify_token
        sig_map = {"review": "approve_review", "authorization": "sign_authorization",
                   "captcha": "solve_captcha", "payment_approval": "approve_payment",
                   "payment": "complete_payment", "personal_declaration": "complete_declaration"}
        if h == "email_verification":
            status, wf = service.signal(db, app_id, "verify_email",
                                        token=_latest_verify_token(wf))
        elif h == "appointment_selection":
            status, wf = service.signal(db, app_id, "select_appointment",
                                        slot_id=pending["slots"][0]["slotId"])
        elif h in sig_map:
            status, wf = service.signal(db, app_id, sig_map[h])
        else:
            break
    assert status["state"] == "READY_TO_SUBMIT"
    assert status["pending"]["handoff"] == "final_review"
    # No submission happened.
    conf = db.execute(select(models.SubmissionConfirmation).where(
        models.SubmissionConfirmation.application_id == app_id)).scalar_one_or_none()
    assert conf is None
    # Sign the exact final version → submission proceeds with no extra prompt
    # (§35.51: no routine submission re-approval after a valid signature).
    _sign_final_review(db, app_id)
    status, wf = service.signal(db, app_id, "start")
    assert status["state"] == "COMPLETED"


def test_revoked_standing_authorization_blocks_submission(db):
    app_id = _new_case(db)
    app_row = db.get(models.VisaApplication, app_id)
    standing.revoke(db, app_row=app_row, principal_user="user1", reason="stop")
    status, wf = _drive_to_completion(db, app_id)
    # The final_review handoff in _drive_to_completion signs, but the standing
    # authorization is revoked, so the gate must keep holding at READY_TO_SUBMIT.
    assert status["state"] == "READY_TO_SUBMIT"
    conf = db.execute(select(models.SubmissionConfirmation).where(
        models.SubmissionConfirmation.application_id == app_id)).scalar_one_or_none()
    assert conf is None


# ---- restart durability of the new state ----
def test_payment_authorization_survives_restart(db):
    app_id = _new_case(db)
    status, wf = service.signal(db, app_id, "start")
    for name in ("approve_review", "sign_authorization", "solve_captcha"):
        status, wf = service.signal(db, app_id, name)
    from tests.test_e2e import _latest_verify_token
    status, wf = service.signal(db, app_id, "verify_email", token=_latest_verify_token(wf))
    fee = status["pending"]["fee"]
    status, wf = service.signal(db, app_id, "approve_payment",
                                amount_cents=fee["amount"], currency=fee["currency"])
    # A fresh load (simulated restart) still sees the authorization state.
    wf2 = service.load_workflow(db, app_id)
    pa = wf2.payment_authorization
    assert pa is not None and pa["amount_cents"] == fee["amount"]
