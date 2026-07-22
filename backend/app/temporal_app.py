"""Real Temporal workflow + activities for the visa pipeline.

The workflow is deterministic orchestration; all side effects (portal calls) are
Temporal ACTIVITIES. Human actions arrive as Temporal SIGNALS. Payment, booking,
and submission activities reconcile against the portal's authoritative state
before acting, so a retry never duplicates them — and because Temporal replays
workflow history on worker restart, an in-flight case resumes exactly.

Activities reach the per-case portal through a process-local registry (the real
deployment injects a Browserbase-driven adapter; tests inject a MockPortal).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from temporalio import activity, workflow

# --- process-local portal registry (worker side) ---------------------------
_PORTALS: dict = {}


def register_portal(case_id: str, portal):
    _PORTALS[case_id] = portal


def _portal(case_id: str):
    return _PORTALS[case_id]


# --- activity inputs -------------------------------------------------------
@dataclass
class CaseCtx:
    case_id: str
    email: str
    password: str
    answers: dict


# --- activities (side effects; may retry, must be idempotent/reconciled) ---
@activity.defn
async def act_register(ctx: CaseCtx) -> dict:
    return _portal(ctx.case_id).register(email=ctx.email, password=ctx.password, full_name="Applicant")


@activity.defn
async def act_verify_email(case_id: str, token: str) -> dict:
    return _portal(case_id).verify_email(token=token)


@activity.defn
async def act_login(ctx: CaseCtx) -> dict:
    return _portal(ctx.case_id).login(email=ctx.email, password=ctx.password)


@activity.defn
async def act_create_application(case_id: str, session_token: str, answers: dict) -> dict:
    return _portal(case_id).create_application(session_token=session_token, answers=answers)


@activity.defn
async def act_pay(case_id: str, session_token: str, application_id: str) -> dict:
    p = _portal(case_id)
    # RECONCILE before charging — never double-pay.
    cur = p.get_application_state(session_token=session_token, application_id=application_id)
    if cur.get("ok") and cur.get("paid"):
        return {"ok": True, "receipt": cur.get("receipt"), "reconciled": True}
    return p.pay(session_token=session_token, application_id=application_id, payment_ref=case_id)


@activity.defn
async def act_book(case_id: str, session_token: str, application_id: str) -> dict:
    p = _portal(case_id)
    cur = p.get_application_state(session_token=session_token, application_id=application_id)
    if cur.get("ok") and cur.get("appointment"):
        return {"ok": True, "appointment": cur["appointment"], "reconciled": True}
    slots = p.search_appointments(session_token=session_token)
    if not slots.get("ok") or not slots["slots"]:
        return {"ok": False, "code": "NO_SLOTS"}
    return p.book_appointment(session_token=session_token, application_id=application_id,
                              slot_id=slots["slots"][0]["slotId"])


@activity.defn
async def act_declare(case_id: str, session_token: str, application_id: str) -> dict:
    return _portal(case_id).declare_personally(session_token=session_token,
                                               application_id=application_id, human_confirmed="HUMAN_DECLARED")


@activity.defn
async def act_submit(case_id: str, session_token: str, application_id: str) -> dict:
    p = _portal(case_id)
    cur = p.get_application_state(session_token=session_token, application_id=application_id)
    if cur.get("ok") and cur.get("submitted"):
        return {"ok": True, "confirmation": cur["confirmation"], "reconciled": True}
    return p.submit(session_token=session_token, application_id=application_id)


_RETRY = None  # default retry policy is fine for the mock


@workflow.defn
class VisaProcessingWorkflow:
    def __init__(self):
        self._review = False
        self._authorized = False
        self._captcha = False
        self._email_token = None
        self._payment_approved = False
        self._payment_done = False
        self._declared = False
        self._state = "DRAFT"

    # ---- signals (human actions) ----
    @workflow.signal
    def approve_review(self):
        self._review = True

    @workflow.signal
    def sign_authorization(self):
        self._authorized = True

    @workflow.signal
    def solve_captcha(self):
        self._captcha = True

    @workflow.signal
    def verify_email(self, token: str):
        self._email_token = token

    @workflow.signal
    def approve_payment(self):
        self._payment_approved = True

    @workflow.signal
    def complete_payment(self):
        self._payment_done = True

    @workflow.signal
    def complete_declaration(self):
        self._declared = True

    @workflow.query
    def state(self) -> str:
        return self._state

    @workflow.run
    async def run(self, ctx_dict: dict) -> dict:
        ctx = CaseCtx(**ctx_dict)
        opts = dict(start_to_close_timeout=timedelta(seconds=30))

        self._state = "APPLICANT_REVIEW_REQUIRED"
        await workflow.wait_condition(lambda: self._review)
        self._state = "AUTHORIZATION_PENDING"
        await workflow.wait_condition(lambda: self._authorized)

        self._state = "PORTAL_ACCOUNT_CREATING"
        reg = await workflow.execute_activity(act_register, ctx, **opts)
        if reg.get("captchaToken"):
            self._state = "CAPTCHA_ACTION_REQUIRED"
            await workflow.wait_condition(lambda: self._captcha)
        self._state = "PORTAL_VERIFICATION_REQUIRED"
        await workflow.wait_condition(lambda: self._email_token is not None)
        await workflow.execute_activity(act_verify_email, args=[ctx.case_id, self._email_token], **opts)

        self._state = "PORTAL_LOGIN_REQUIRED"
        login = await workflow.execute_activity(act_login, ctx, **opts)
        token = login["sessionToken"]
        app = await workflow.execute_activity(act_create_application,
                                              args=[ctx.case_id, token, ctx.answers], **opts)
        app_id = app["applicationId"]

        self._state = "PAYMENT_APPROVAL_REQUIRED"
        await workflow.wait_condition(lambda: self._payment_approved)
        self._state = "PAYMENT_ACTION_REQUIRED"
        await workflow.wait_condition(lambda: self._payment_done)
        self._state = "PAYMENT_PROCESSING"
        pay = await workflow.execute_activity(act_pay, args=[ctx.case_id, token, app_id], **opts)
        if not pay.get("ok"):
            self._state = "RECOVERABLE_FAILURE"
            return {"state": self._state, "reason": "payment"}

        self._state = "APPOINTMENT_BOOKING"
        book = await workflow.execute_activity(act_book, args=[ctx.case_id, token, app_id], **opts)

        self._state = "PERSONAL_DECLARATION_REQUIRED"
        await workflow.wait_condition(lambda: self._declared)
        await workflow.execute_activity(act_declare, args=[ctx.case_id, token, app_id], **opts)

        self._state = "SUBMITTING"
        sub = await workflow.execute_activity(act_submit, args=[ctx.case_id, token, app_id], **opts)
        if not sub.get("ok"):
            self._state = "RECOVERABLE_FAILURE"
            return {"state": self._state, "reason": "submit"}

        self._state = "COMPLETED"
        return {"state": self._state, "application_id": app_id,
                "receipt": pay.get("receipt"), "appointment": book.get("appointment"),
                "confirmation": sub.get("confirmation")}


ALL_ACTIVITIES = [act_register, act_verify_email, act_login, act_create_application,
                  act_pay, act_book, act_declare, act_submit]
