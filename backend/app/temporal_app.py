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
from temporalio.common import RetryPolicy

# --- per-case portal resolution (worker side) ------------------------------
# Two modes:
#   1. In-process (unit tests): register_portal() puts a live MockPortal in the
#      registry; activities mutate it in place — no DB needed.
#   2. Live worker (docker compose): no in-memory registry entry exists, so the
#      portal is loaded from / saved to a DB-backed store on every activity. This
#      makes payment/booking/submission reconciliation correct even if the worker
#      is killed and restarted mid-case (the restarted worker reloads the portal
#      and sees it already paid/booked/submitted → no duplicate side effects).
_PORTALS: dict = {}
_STORE = None  # optional Db-backed store, set by temporal_worker on startup


def register_portal(case_id: str, portal):
    _PORTALS[case_id] = portal


def set_portal_store(store):
    global _STORE
    _STORE = store


def _run_op(case_id: str, fn):
    """Resolve the portal, run one operation, and persist if DB-backed."""
    if case_id in _PORTALS:
        return fn(_PORTALS[case_id])          # in-memory (tests): mutate in place
    if _STORE is not None:
        portal = _STORE.load(case_id)          # live worker: durable reconcile
        result = fn(portal)
        _STORE.save(case_id, portal)
        return result
    raise KeyError(f"no portal registered for case {case_id}")


def _portal(case_id: str):
    # Read-only convenience (used where a single call both reads+returns).
    if case_id in _PORTALS:
        return _PORTALS[case_id]
    if _STORE is not None:
        return _STORE.load(case_id)
    raise KeyError(f"no portal registered for case {case_id}")


# --- activity inputs -------------------------------------------------------
@dataclass
class CaseCtx:
    case_id: str
    email: str
    password: str
    answers: dict


# --- activities (side effects; may retry, must be idempotent/reconciled) ---
# Every activity goes through _run_op so that, on the live worker, the portal is
# loaded from and saved to the DB store around each call — reconciliation stays
# correct across a worker kill/restart.
@activity.defn
async def act_register(ctx: CaseCtx) -> dict:
    return _run_op(ctx.case_id, lambda p: p.register(email=ctx.email, password=ctx.password, full_name="Applicant"))


@activity.defn
async def act_verify_email(case_id: str, token: str) -> dict:
    return _run_op(case_id, lambda p: p.verify_email(token=token))


@activity.defn
async def act_login(ctx: CaseCtx) -> dict:
    return _run_op(ctx.case_id, lambda p: p.login(email=ctx.email, password=ctx.password))


@activity.defn
async def act_create_application(case_id: str, session_token: str, answers: dict) -> dict:
    return _run_op(case_id, lambda p: p.create_application(session_token=session_token, answers=answers))


def _pay_op(case_id, session_token, application_id):
    def op(p):
        # RECONCILE before charging — never double-pay across a retry/restart.
        cur = p.get_application_state(session_token=session_token, application_id=application_id)
        if cur.get("ok") and cur.get("paid"):
            return {"ok": True, "receipt": cur.get("receipt"), "reconciled": True}
        return p.pay(session_token=session_token, application_id=application_id, payment_ref=case_id)
    return op


@activity.defn
async def act_pay(case_id: str, session_token: str, application_id: str) -> dict:
    return _run_op(case_id, _pay_op(case_id, session_token, application_id))


def _book_op(session_token, application_id):
    def op(p):
        cur = p.get_application_state(session_token=session_token, application_id=application_id)
        if cur.get("ok") and cur.get("appointment"):
            return {"ok": True, "appointment": cur["appointment"], "reconciled": True}
        slots = p.search_appointments(session_token=session_token)
        if not slots.get("ok") or not slots["slots"]:
            return {"ok": False, "code": "NO_SLOTS"}
        return p.book_appointment(session_token=session_token, application_id=application_id,
                                  slot_id=slots["slots"][0]["slotId"])
    return op


@activity.defn
async def act_book(case_id: str, session_token: str, application_id: str) -> dict:
    return _run_op(case_id, _book_op(session_token, application_id))


@activity.defn
async def act_declare(case_id: str, session_token: str, application_id: str) -> dict:
    return _run_op(case_id, lambda p: p.declare_personally(session_token=session_token,
                                                           application_id=application_id,
                                                           human_confirmed="HUMAN_DECLARED"))


def _submit_op(session_token, application_id):
    def op(p):
        cur = p.get_application_state(session_token=session_token, application_id=application_id)
        if cur.get("ok") and cur.get("submitted"):
            return {"ok": True, "confirmation": cur["confirmation"], "reconciled": True}
        return p.submit(session_token=session_token, application_id=application_id)
    return op


@activity.defn
async def act_submit(case_id: str, session_token: str, application_id: str) -> dict:
    return _run_op(case_id, _submit_op(session_token, application_id))


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


# --- One-time durable snapshot research processing (brief section 13) --------
# A ONE-SHOT workflow: it drains the resumable research batches and completes.
# There is deliberately NO schedule, NO cron, NO recurring refresh — restart
# durability comes from Temporal's replay + the DB-persisted batch stages.

@activity.defn
async def act_snapshot_resume() -> dict:
    from .db import SessionLocal
    from .visa_snapshot.pipeline import resume as _resume
    db = SessionLocal()
    try:
        return _resume(db)
    finally:
        db.close()


@workflow.defn
class OneTimeSnapshotResearchWorkflow:
    """Drains all research batches whose input files exist, then finishes.
    Re-run manually (one-shot) after more research input lands; never scheduled."""

    @workflow.run
    async def run(self, max_rounds: int = 50) -> dict:
        totals = {"rounds": 0, "completed": 0}
        for _ in range(max_rounds):
            r = await workflow.execute_activity(
                act_snapshot_resume,
                start_to_close_timeout=timedelta(minutes=30),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            totals["rounds"] += 1
            totals["completed"] += r.get("completed_now", 0)
            # Done when nothing is left that CAN progress (only batches still
            # awaiting research input remain, or everything is complete).
            if r.get("processed", 0) == 0 or r.get("completed_now", 0) == 0:
                totals["remaining_awaiting_input"] = r.get("awaiting_research_input", 0)
                break
        return totals


ALL_ACTIVITIES.append(act_snapshot_resume)
ALL_WORKFLOWS = [VisaProcessingWorkflow, OneTimeSnapshotResearchWorkflow]


# --- Durable on-demand route research (strategy change; one job at a time) ---

@activity.defn
async def act_run_ondemand_research(job_id: str) -> dict:
    from .db import SessionLocal
    from .visa_snapshot.ondemand import run_job
    db = SessionLocal()
    try:
        job = run_job(db, job_id)
        return {"id": job.id, "status": job.status, "stage": job.stage}
    finally:
        db.close()


@workflow.defn
class OnDemandRouteResearchWorkflow:
    """One-shot durable wrapper for a single focused research job. The job's
    own stage checkpoints make the activity idempotent, so a worker restart
    resumes exactly where it stopped. NO schedules; one route per execution."""

    @workflow.run
    async def run(self, job_id: str) -> dict:
        return await workflow.execute_activity(
            act_run_ondemand_research, job_id,
            start_to_close_timeout=timedelta(minutes=15),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )


ALL_ACTIVITIES.append(act_run_ondemand_research)
ALL_WORKFLOWS.append(OnDemandRouteResearchWorkflow)
