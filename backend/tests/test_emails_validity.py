"""Phase 8 (email templates, delivery status, retry/DLQ, content guard) and
Phase 5 (passport expiration validation + renewal instructions)."""
from datetime import date

import pytest

from tests.conftest import AUTH
from app import emails, passport_validity as pv, models, rules
from sqlalchemy import select

ADMIN = {"Authorization": "Bearer admin-token", "X-Org-Id": "org1", "X-User-Id": "mail-admin"}


def _case(db, org="org1", answers=None):
    a = models.Applicant(org_id=org, user_id="u", full_name="Anna Eriksson",
                         email="anna@example.com")
    db.add(a); db.flush()
    app_row = models.VisaApplication(org_id=org, user_id="u", applicant_id=a.id,
                                     destination_country="Mockland",
                                     answers=answers or {})
    db.add(app_row); db.commit()
    return app_row


# ---- Phase 8: templates ----
def test_every_event_has_all_three_locales():
    for locale in ("en", "zh-CN", "zh-Hant"):
        for event in emails.EVENTS:
            subject, body = emails.render(event, locale, name="A", case_ref="R", detail="", link="L")
            assert subject.strip() and body.strip(), f"{locale}:{event}"
            assert "Ellis" in body


def test_render_unknown_event_rejected():
    with pytest.raises(ValueError):
        emails.render("password_reset", "en", name="A", case_ref="R", detail="", link="L")


def test_email_guard_blocks_passport_number_and_liveview(db):
    app_row = _case(db, answers={"passport_number": "L898902C3"})
    with pytest.raises(emails.SensitiveContentError):
        emails.queue_case_email(db, app_row, event="case_created",
                                detail="Your passport L898902C3 was received")
    with pytest.raises(emails.SensitiveContentError):
        emails.queue_case_email(db, app_row, event="case_created",
                                detail="Continue at https://liveview.browserbase.com/x")


def test_queue_and_deep_link(db):
    app_row = _case(db)
    row = emails.queue_case_email(db, app_row, event="case_created", locale="zh-CN",
                                  detail="欢迎")
    assert row.status == "queued" and row.locale == "zh-CN"
    assert "/case/" + app_row.id in row.body and "sig=" in row.body
    # The deep link verifies and expires.
    import re as _re
    m = _re.search(r"exp=(\d+)&sig=([0-9a-f]+)", row.body)
    assert emails.verify_deep_link(app_row.id, int(m.group(1)), m.group(2)) is True
    assert emails.verify_deep_link(app_row.id, int(m.group(1)) - 10 ** 6, m.group(2)) is False


def test_retry_and_dead_letter(db):
    app_row = _case(db, org="orgDLQ")
    emails.queue_case_email(db, app_row, event="payment_required")

    calls = {"n": 0}
    def failing_sender(*, to, subject, body):
        calls["n"] += 1
        return {"ok": False, "detail": "boom"}

    for i in range(emails.MAX_ATTEMPTS):
        emails.process_queue(db, org_id="orgDLQ", sender=failing_sender)
    row = db.execute(select(models.EmailNotification).where(
        models.EmailNotification.application_id == app_row.id)).scalar_one()
    assert row.status == "dead" and row.attempts == emails.MAX_ATTEMPTS
    assert calls["n"] == emails.MAX_ATTEMPTS
    dead = emails.dead_letters(db, "orgDLQ")
    assert any(d["event"] == "payment_required" for d in dead)
    # A dead letter is never retried again.
    emails.process_queue(db, org_id="orgDLQ", sender=failing_sender)
    assert calls["n"] == emails.MAX_ATTEMPTS


def test_successful_delivery_and_local_recorder_honesty(db):
    app_row = _case(db, org="orgOK")
    emails.queue_case_email(db, app_row, event="case_completed")
    out = emails.process_queue(db, org_id="orgOK",
                               sender=lambda **k: {"ok": True, "delivered": True})
    assert out["sent"] == 1
    row = db.execute(select(models.EmailNotification).where(
        models.EmailNotification.application_id == app_row.id)).scalar_one()
    assert row.status == "sent" and row.sent is True
    # The local recorder path never claims real delivery.
    app2 = _case(db, org="orgOK")
    emails.queue_case_email(db, app2, event="case_created")
    out = emails.process_queue(db, org_id="orgOK",
                               sender=lambda **k: {"ok": True, "delivered": False})
    assert out["recorded"] == 1


def test_email_admin_endpoints(client):
    assert client.post("/admin/email/process-queue", headers=AUTH).status_code == 403
    assert client.post("/admin/email/process-queue", headers=ADMIN).status_code == 200
    assert client.get("/admin/email/dead-letters", headers=ADMIN).status_code == 200


# ---- Phase 5: passport validity ----
TODAY = date(2026, 7, 22)


def test_parse_expiry_formats():
    assert pv.parse_expiry("300415") == date(2030, 4, 15)     # MRZ YYMMDD
    assert pv.parse_expiry("981231") == date(1998, 12, 31)    # century pivot
    assert pv.parse_expiry("2031-01-02") == date(2031, 1, 2)  # ISO
    assert pv.parse_expiry("") is None and pv.parse_expiry("garbage") is None


def test_expired_passport_blocks_with_renewal_instructions(db):
    app_row = _case(db, answers={"expiry_date": "120415", "issuing_country": "USA"})
    v = pv.check_case_passport(db, app_row, today=TODAY)
    assert v["status"] == "expired" and v["blocking"] is True
    assert v["renewal"]["authority"].startswith("U.S. Department of State")
    assert "travel.state.gov" in v["renewal"]["url"]
    assert "try again" in v["retry"]


def test_unknown_issuing_country_is_honest(db):
    app_row = _case(db, answers={"expiry_date": "120415", "issuing_country": "ZZZ"})
    v = pv.check_case_passport(db, app_row, today=TODAY)
    assert v["renewal"]["url"] == ""
    assert "does not have the official renewal source" in v["renewal"]["note"]


def test_destination_rule_enforced_not_generic(db):
    # Verified rule: 6 months after arrival. Passport valid 3 months past arrival
    # → insufficient — and the explanation cites the destination rule.
    r = rules.create_rule(db, destination="Mockland", source_url="https://mock.gov.example",
                          passport_validity_rule={"kind": "months_after_arrival", "months": 6})
    rules.review_rule(db, rule_id=r.id, decision="verified", actor="admin")
    app_row = _case(db, answers={"expiry_date": "2026-12-01",
                                 "intended_arrival": "2026-09-01",
                                 "intended_departure": "2026-09-20"})
    v = pv.check_case_passport(db, app_row, today=TODAY)
    assert v["status"] == "insufficient_validity" and v["blocking"] is True
    assert v["required_valid_until"] == "2027-03-01"
    assert "6 months after arrival" in v["explanation"]


def test_valid_passport_without_verified_rule_is_flagged_not_blocked(db):
    app_row = _case(db, answers={"expiry_date": "2031-01-01"})
    app_row.destination_country = "NeverVerifiedLand"
    db.commit()
    v = pv.check_case_passport(db, app_row, today=TODAY)
    assert v["status"] == "ok_rule_unverified" and v["blocking"] is False


def test_expired_passport_blocks_start_and_emails(client, db):
    cid = client.post("/cases", headers=AUTH, json={
        "full_name": "Anna", "email": "a@e.com", "destination_country": "Mockland",
        "answers": {"expiry_date": "120415", "issuing_country": "USA"}}).json()["id"]
    r = client.post(f"/cases/{cid}/start", headers=AUTH)
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "passport_validity"
    # The renewal email was queued (Phase 8 pipeline), exactly once.
    emails_list = client.get(f"/cases/{cid}/emails", headers=AUTH).json()["emails"]
    assert sum(1 for e in emails_list if e["event"] == "passport_expired") == 1
    client.post(f"/cases/{cid}/start", headers=AUTH)   # idempotent — still one email
    emails_list = client.get(f"/cases/{cid}/emails", headers=AUTH).json()["emails"]
    assert sum(1 for e in emails_list if e["event"] == "passport_expired") == 1


def test_renewed_passport_unblocks(client):
    cid = client.post("/cases", headers=AUTH, json={
        "full_name": "Anna", "email": "a@e.com", "destination_country": "Mockland",
        "answers": {"expiry_date": "120415"}}).json()["id"]
    assert client.post(f"/cases/{cid}/start", headers=AUTH).status_code == 409
    # "Upload renewed passport and try again": new expiry unblocks the case.
    client.post(f"/cases/{cid}/answers", headers=AUTH,
                json={"answers": {"expiry_date": "330415"}})
    assert client.post(f"/cases/{cid}/start", headers=AUTH).status_code == 200
