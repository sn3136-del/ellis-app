"""Production email (Phase 8): locale-aware templates for every case event,
delivery status, retry + dead-letter handling, and a sensitive-content guard.

Hard rules (enforced here, tested):
  * NO passport numbers, MRZ, or other sensitive identity values in any email.
  * NO Live View URLs in ordinary email.
  * Deep links are signed and expiring (HMAC action token), never raw IDs alone.
  * The local dev recorder never claims real delivery (status='recorded').
"""
from __future__ import annotations

import hmac
import hashlib
import json
import re
import time

from sqlalchemy import select

from . import models, audit
from .config import settings

MAX_ATTEMPTS = 3

# Every case event from the brief. Each maps to (subject, body) per locale.
# Bodies use {name}, {case_ref}, {detail}, {link} placeholders only.
EVENTS = (
    "case_created", "documents_received", "document_correction_required",
    "passport_expired", "application_review_required", "authorization_required",
    "portal_account_verification_required", "captcha_otp_action_required",
    "payment_required", "payment_confirmed", "appointment_options_available",
    "appointment_selected", "appointment_booked", "appointment_changed",
    "personal_declaration_required", "application_submitted",
    "confirmation_received", "error_requiring_action", "case_completed",
)

_EN = {
    "case_created": ("Your visa case has been created",
                     "Hello {name},\n\nYour visa case {case_ref} has been created. {detail}\n\nOpen your case: {link}\n\n— Ellis"),
    "documents_received": ("We received your documents",
                           "Hello {name},\n\nWe received your documents for case {case_ref}. {detail}\n\nOpen your case: {link}\n\n— Ellis"),
    "document_correction_required": ("A document needs your attention",
                                     "Hello {name},\n\nOne of your documents for case {case_ref} needs correction. {detail}\n\nOpen your case: {link}\n\n— Ellis"),
    "passport_expired": ("Your passport needs to be renewed",
                         "Hello {name},\n\nWe cannot continue case {case_ref} with the current passport. {detail}\n\nOpen your case: {link}\n\n— Ellis"),
    "application_review_required": ("Please review your application",
                                    "Hello {name},\n\nYour application {case_ref} is ready for your review. {detail}\n\nReview now: {link}\n\n— Ellis"),
    "authorization_required": ("Your authorization is required",
                               "Hello {name},\n\nCase {case_ref} needs your signed authorization to continue. {detail}\n\nSign here: {link}\n\n— Ellis"),
    "portal_account_verification_required": ("Verify your portal account",
                                             "Hello {name},\n\nThe official portal sent you a verification email for case {case_ref}. {detail}\n\nContinue: {link}\n\n— Ellis"),
    "captcha_otp_action_required": ("Action needed: security check",
                                    "Hello {name},\n\nCase {case_ref} is paused at a security step only you can complete. {detail}\n\nContinue: {link}\n\n— Ellis"),
    "payment_required": ("Payment required to continue",
                         "Hello {name},\n\nCase {case_ref} is ready for the official fee payment. {detail}\n\nContinue: {link}\n\n— Ellis"),
    "payment_confirmed": ("Payment confirmed",
                          "Hello {name},\n\nYour payment for case {case_ref} is confirmed. {detail}\n\nOpen your case: {link}\n\n— Ellis"),
    "appointment_options_available": ("Appointment options are available",
                                      "Hello {name},\n\nAppointment options are available for case {case_ref}. {detail}\n\nChoose a slot: {link}\n\n— Ellis"),
    "appointment_selected": ("Appointment selected",
                             "Hello {name},\n\nYour appointment selection for case {case_ref} was received. {detail}\n\nOpen your case: {link}\n\n— Ellis"),
    "appointment_booked": ("Your appointment is booked",
                           "Hello {name},\n\nYour appointment for case {case_ref} is booked. {detail}\n\nOpen your case: {link}\n\n— Ellis"),
    "appointment_changed": ("Your appointment has changed",
                            "Hello {name},\n\nYour appointment for case {case_ref} was changed. {detail}\n\nOpen your case: {link}\n\n— Ellis"),
    "personal_declaration_required": ("Your personal declaration is required",
                                      "Hello {name},\n\nCase {case_ref} requires a declaration only you can make. {detail}\n\nContinue: {link}\n\n— Ellis"),
    "application_submitted": ("Your application was submitted",
                              "Hello {name},\n\nYour application {case_ref} was submitted. {detail}\n\nOpen your case: {link}\n\n— Ellis"),
    "confirmation_received": ("Official confirmation received",
                              "Hello {name},\n\nWe received the official confirmation for case {case_ref}. {detail}\n\nOpen your case: {link}\n\n— Ellis"),
    "error_requiring_action": ("Your case needs attention",
                               "Hello {name},\n\nSomething in case {case_ref} needs your action. {detail}\n\nOpen your case: {link}\n\n— Ellis"),
    "case_completed": ("Your case is complete",
                       "Hello {name},\n\nCase {case_ref} is complete. {detail}\n\nOpen your case: {link}\n\n— Ellis"),
}

_ZH_CN = {
    "case_created": ("您的签证案件已创建", "您好 {name}，\n\n您的签证案件 {case_ref} 已创建。{detail}\n\n查看案件：{link}\n\n— Ellis"),
    "documents_received": ("我们已收到您的文件", "您好 {name}，\n\n我们已收到案件 {case_ref} 的文件。{detail}\n\n查看案件：{link}\n\n— Ellis"),
    "document_correction_required": ("有文件需要您处理", "您好 {name}，\n\n案件 {case_ref} 的一份文件需要更正。{detail}\n\n查看案件：{link}\n\n— Ellis"),
    "passport_expired": ("您的护照需要更新", "您好 {name}，\n\n当前护照无法继续办理案件 {case_ref}。{detail}\n\n查看案件：{link}\n\n— Ellis"),
    "application_review_required": ("请审核您的申请", "您好 {name}，\n\n您的申请 {case_ref} 已可供审核。{detail}\n\n立即审核：{link}\n\n— Ellis"),
    "authorization_required": ("需要您的授权", "您好 {name}，\n\n案件 {case_ref} 需要您签署授权后才能继续。{detail}\n\n前往签署：{link}\n\n— Ellis"),
    "portal_account_verification_required": ("请验证您的门户账户", "您好 {name}，\n\n官方门户已向您发送案件 {case_ref} 的验证邮件。{detail}\n\n继续：{link}\n\n— Ellis"),
    "captcha_otp_action_required": ("需要您完成安全验证", "您好 {name}，\n\n案件 {case_ref} 暂停在只有您本人才能完成的安全步骤。{detail}\n\n继续：{link}\n\n— Ellis"),
    "payment_required": ("需要付款以继续", "您好 {name}，\n\n案件 {case_ref} 已可支付官方费用。{detail}\n\n继续：{link}\n\n— Ellis"),
    "payment_confirmed": ("付款已确认", "您好 {name}，\n\n您对案件 {case_ref} 的付款已确认。{detail}\n\n查看案件：{link}\n\n— Ellis"),
    "appointment_options_available": ("预约时段可选", "您好 {name}，\n\n案件 {case_ref} 有可选的预约时段。{detail}\n\n选择时段：{link}\n\n— Ellis"),
    "appointment_selected": ("已收到您的预约选择", "您好 {name}，\n\n已收到您对案件 {case_ref} 的预约选择。{detail}\n\n查看案件：{link}\n\n— Ellis"),
    "appointment_booked": ("您的预约已确认", "您好 {name}，\n\n案件 {case_ref} 的预约已确认。{detail}\n\n查看案件：{link}\n\n— Ellis"),
    "appointment_changed": ("您的预约已变更", "您好 {name}，\n\n案件 {case_ref} 的预约已变更。{detail}\n\n查看案件：{link}\n\n— Ellis"),
    "personal_declaration_required": ("需要您的个人声明", "您好 {name}，\n\n案件 {case_ref} 需要只有您本人才能作出的声明。{detail}\n\n继续：{link}\n\n— Ellis"),
    "application_submitted": ("您的申请已提交", "您好 {name}，\n\n您的申请 {case_ref} 已提交。{detail}\n\n查看案件：{link}\n\n— Ellis"),
    "confirmation_received": ("已收到官方确认", "您好 {name}，\n\n我们已收到案件 {case_ref} 的官方确认。{detail}\n\n查看案件：{link}\n\n— Ellis"),
    "error_requiring_action": ("您的案件需要处理", "您好 {name}，\n\n案件 {case_ref} 需要您的操作。{detail}\n\n查看案件：{link}\n\n— Ellis"),
    "case_completed": ("您的案件已完成", "您好 {name}，\n\n案件 {case_ref} 已完成。{detail}\n\n查看案件：{link}\n\n— Ellis"),
}

# Traditional Chinese — maintained explicitly (never machine-converted).
_ZH_HANT = {
    "case_created": ("您的簽證案件已建立", "您好 {name}，\n\n您的簽證案件 {case_ref} 已建立。{detail}\n\n查看案件：{link}\n\n— Ellis"),
    "documents_received": ("我們已收到您的文件", "您好 {name}，\n\n我們已收到案件 {case_ref} 的文件。{detail}\n\n查看案件：{link}\n\n— Ellis"),
    "document_correction_required": ("有文件需要您處理", "您好 {name}，\n\n案件 {case_ref} 的一份文件需要更正。{detail}\n\n查看案件：{link}\n\n— Ellis"),
    "passport_expired": ("您的護照需要更新", "您好 {name}，\n\n目前護照無法繼續辦理案件 {case_ref}。{detail}\n\n查看案件：{link}\n\n— Ellis"),
    "application_review_required": ("請審核您的申請", "您好 {name}，\n\n您的申請 {case_ref} 已可供審核。{detail}\n\n立即審核：{link}\n\n— Ellis"),
    "authorization_required": ("需要您的授權", "您好 {name}，\n\n案件 {case_ref} 需要您簽署授權後才能繼續。{detail}\n\n前往簽署：{link}\n\n— Ellis"),
    "portal_account_verification_required": ("請驗證您的門戶帳戶", "您好 {name}，\n\n官方門戶已向您發送案件 {case_ref} 的驗證郵件。{detail}\n\n繼續：{link}\n\n— Ellis"),
    "captcha_otp_action_required": ("需要您完成安全驗證", "您好 {name}，\n\n案件 {case_ref} 暫停在只有您本人才能完成的安全步驟。{detail}\n\n繼續：{link}\n\n— Ellis"),
    "payment_required": ("需要付款以繼續", "您好 {name}，\n\n案件 {case_ref} 已可支付官方費用。{detail}\n\n繼續：{link}\n\n— Ellis"),
    "payment_confirmed": ("付款已確認", "您好 {name}，\n\n您對案件 {case_ref} 的付款已確認。{detail}\n\n查看案件：{link}\n\n— Ellis"),
    "appointment_options_available": ("預約時段可選", "您好 {name}，\n\n案件 {case_ref} 有可選的預約時段。{detail}\n\n選擇時段：{link}\n\n— Ellis"),
    "appointment_selected": ("已收到您的預約選擇", "您好 {name}，\n\n已收到您對案件 {case_ref} 的預約選擇。{detail}\n\n查看案件：{link}\n\n— Ellis"),
    "appointment_booked": ("您的預約已確認", "您好 {name}，\n\n案件 {case_ref} 的預約已確認。{detail}\n\n查看案件：{link}\n\n— Ellis"),
    "appointment_changed": ("您的預約已變更", "您好 {name}，\n\n案件 {case_ref} 的預約已變更。{detail}\n\n查看案件：{link}\n\n— Ellis"),
    "personal_declaration_required": ("需要您的個人聲明", "您好 {name}，\n\n案件 {case_ref} 需要只有您本人才能作出的聲明。{detail}\n\n繼續：{link}\n\n— Ellis"),
    "application_submitted": ("您的申請已提交", "您好 {name}，\n\n您的申請 {case_ref} 已提交。{detail}\n\n查看案件：{link}\n\n— Ellis"),
    "confirmation_received": ("已收到官方確認", "您好 {name}，\n\n我們已收到案件 {case_ref} 的官方確認。{detail}\n\n查看案件：{link}\n\n— Ellis"),
    "error_requiring_action": ("您的案件需要處理", "您好 {name}，\n\n案件 {case_ref} 需要您的操作。{detail}\n\n查看案件：{link}\n\n— Ellis"),
    "case_completed": ("您的案件已完成", "您好 {name}，\n\n案件 {case_ref} 已完成。{detail}\n\n查看案件：{link}\n\n— Ellis"),
}

TEMPLATES = {"en": _EN, "zh-CN": _ZH_CN, "zh-Hant": _ZH_HANT}

_LIVEVIEW_PAT = re.compile(r"browserbase|live[-_ ]?view", re.I)
_MRZ_PAT = re.compile(r"[A-Z0-9<]*<{2,}[A-Z0-9<]*")


class SensitiveContentError(ValueError):
    pass


def _deep_link(application_id: str, *, ttl_seconds: int = 72 * 3600) -> str:
    """Signed, expiring case deep link (no session cookie, no PII in the URL)."""
    s = settings()
    # Configured tenant app URL (ELLIS_APP_BASE_URL); the placeholder is used
    # only in local/dev where no app URL is set.
    base = (getattr(s, "app_base_url", "") or "https://app.ellis.example")
    exp = int(time.time()) + ttl_seconds
    payload = f"{application_id}.{exp}"
    sig = hmac.new(s.action_token_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{base}/case/{application_id}?exp={exp}&sig={sig}"


def verify_deep_link(application_id: str, exp: int, sig: str) -> bool:
    s = settings()
    if int(exp) < time.time():
        return False
    payload = f"{application_id}.{exp}"
    expected = hmac.new(s.action_token_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(sig, expected)


def _guard_content(body: str, subject: str, app_row) -> None:
    """Refuse to queue an email containing sensitive identity values or a Live
    View URL. Checked against the actual case answers, not just patterns."""
    blob = subject + "\n" + body
    if _LIVEVIEW_PAT.search(blob):
        raise SensitiveContentError("Live View URLs are not permitted in ordinary email")
    if _MRZ_PAT.search(blob) and "<<" in blob:
        raise SensitiveContentError("MRZ-like content is not permitted in email")
    answers = (app_row.answers or {}) if app_row is not None else {}
    for key in ("passport_number", "birth_date"):
        val = str(answers.get(key, "") or "")
        if len(val) >= 6 and val in blob:
            raise SensitiveContentError(f"sensitive value ({key}) is not permitted in email")


def render(event: str, locale: str, *, name: str, case_ref: str, detail: str, link: str) -> tuple[str, str]:
    if event not in EVENTS:
        raise ValueError(f"unknown email event '{event}'")
    table = TEMPLATES.get(locale) or TEMPLATES["en"]
    subject, body = table.get(event) or TEMPLATES["en"][event]
    fmt = {"name": name, "case_ref": case_ref, "detail": detail, "link": link}
    return subject.format(**fmt), body.format(**fmt)


def queue_case_email(db, app_row, *, event: str, locale: str = "en",
                     detail: str = "") -> models.EmailNotification:
    """Render + guard + queue one case email. Delivery is attempted by
    process_queue (retry + dead-letter)."""
    applicant = db.get(models.Applicant, app_row.applicant_id)
    name = (applicant.full_name if applicant else "") or "applicant"
    to_addr = applicant.email if applicant else ""
    case_ref = app_row.id[:8].upper()
    link = _deep_link(app_row.id)
    subject, body = render(event, locale, name=name, case_ref=case_ref,
                           detail=detail, link=link)
    _guard_content(body, subject, app_row)
    row = models.EmailNotification(application_id=app_row.id, to_addr=to_addr,
                                   subject=subject, body=body, sent=False,
                                   status="queued", event=event, locale=locale)
    db.add(row)
    db.commit()
    audit.record(db, org_id=app_row.org_id, application_id=app_row.id,
                 action="email_queued", detail={"event": event, "locale": locale})
    return row


def _default_sender(db, org_id: str):
    """Resolve the tenant's configured provider (Phase 7 setup) or the local
    recorder. Returns a callable(to, subject, body)->result dict."""
    from . import setup as setup_mod
    from .providers.email import get_provider, EmailConfig
    row = db.get(models.TenantSetup, org_id)
    cred = ""
    cfg = EmailConfig()
    if row:
        e = (row.config or {}).get("email", {})
        cfg = EmailConfig(provider=e.get("provider", "local"), sender=e.get("sender", ""),
                          reply_to=e.get("reply_to", ""), host=e.get("host", ""),
                          port=int(e.get("port", 587) or 587), username=e.get("username", ""),
                          api_endpoint=e.get("api_endpoint", ""))
        ref = (row.credential_refs or {}).get("email_credential")
        if ref:
            from . import vault
            try:
                cfg.credential = vault.reveal(ref)
            except KeyError:
                pass
    provider = get_provider(cfg if cfg.provider else EmailConfig(provider="local"))
    return lambda *, to, subject, body: provider.send(to=to, subject=subject, body=body)


def process_queue(db, *, org_id: str = "", sender=None, max_attempts: int = MAX_ATTEMPTS) -> dict:
    """Attempt delivery for queued/failed emails. Retryable failures increment
    attempts; at max_attempts the message dead-letters (never silently dropped).
    `sender(to, subject, body)->{ok, delivered, ...}` is injectable for tests."""
    rows = db.execute(select(models.EmailNotification).where(
        models.EmailNotification.status.in_(("queued", "failed")))).scalars().all()
    sent = failed = dead = recorded = attempted = 0
    for row in rows:
        app_row = db.get(models.VisaApplication, row.application_id)
        if org_id and (not app_row or app_row.org_id != org_id):
            continue    # different tenant / orphaned — not attempted, not counted
        attempted += 1
        send = sender or _default_sender(db, app_row.org_id if app_row else "")
        try:
            result = send(to=row.to_addr, subject=row.subject, body=row.body)
        except Exception as e:  # noqa: BLE001 — a sender crash is a retryable failure
            result = {"ok": False, "detail": str(e)[:60]}
        row.attempts += 1
        if result.get("ok") and result.get("delivered"):
            row.status = "sent"
            row.sent = True
            sent += 1
        elif result.get("ok") and not result.get("delivered"):
            row.status = "recorded"     # local recorder — honest, not "sent"
            recorded += 1
        else:
            row.last_error = str(result.get("detail", ""))[:200]
            if row.attempts >= max_attempts:
                row.status = "dead"
                dead += 1
            else:
                row.status = "failed"
                failed += 1
    db.commit()
    return {"processed": attempted, "sent": sent, "recorded": recorded,
            "failed": failed, "dead_lettered": dead}


def dead_letters(db, org_id: str) -> list[dict]:
    rows = db.execute(select(models.EmailNotification).where(
        models.EmailNotification.status == "dead")).scalars().all()
    out = []
    for r in rows:
        app_row = db.get(models.VisaApplication, r.application_id)
        if app_row and app_row.org_id == org_id:
            out.append({"id": r.id, "event": r.event, "to": r.to_addr,
                        "attempts": r.attempts, "last_error": r.last_error})
    return out
