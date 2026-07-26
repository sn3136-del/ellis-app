"""Durable, resumable visa workflow — the backend orchestrator.

Design: an explicit state machine whose entire state is persisted to the
`workflow_executions` row after every step. A signal loads the row, advances
until it hits a human handoff or terminal state, and saves. This survives a
worker restart with no in-memory state — the durability Temporal provides,
expressed against the DB so it runs here without a Temporal server.

Activation: with TEMPORAL_HOST set, the same step functions become Temporal
activities and the loop becomes a Temporal workflow; the state set and signals
are already Temporal-shaped.

Safety: payment/booking/submission reconcile before acting; CAPTCHA/OTP/
verification/payment/declaration are human-only; generated passwords go to the
vault (refs only); a post-review material change invalidates approval.
"""
from __future__ import annotations

import hashlib
import json

from . import vault, audit
from .statemachine import CaseMachine, is_terminal
from .appointments import default_preferences, validate_preferences, earliest_qualifying, is_improvement
from .providers import browser


def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


class VisaWorkflow:
    def __init__(self, *, case_id, org_id, adapter, applicant, answers, documents,
                 preferences=None, authorization=None, exec_row=None, db=None, emailer=None):
        self.case_id = case_id
        self.org_id = org_id
        self.adapter = adapter
        self.applicant = applicant
        self.answers = answers or {}
        self.documents = documents or []
        self.prefs = default_preferences(**(preferences or {}))
        self.authorization = authorization or {}
        self.db = db
        self.emailer = emailer
        # Injected by the service layer (DB-backed); None in pure unit tests.
        self.payments_hook = None      # (event, payload) -> None
        self.submission_gate = None    # () -> (ok, reason)
        # Route fee context (official fee record: methods/refundability) for
        # the applicant's fee/payment confirmation screens. Display-only.
        self.fee_context = None
        # Foreground mode: an HTTP request never performs live portal work —
        # _drive parks before any state that needs the live driver and the
        # caller queues a durable background run instead (deferred_live=True).
        self.defer_live = False
        self.deferred_live = False
        # Background runs may only enter SUBMITTING when the enqueuing action
        # was the applicant's explicit post-final-review confirmation.
        self.block_submit = False

        if exec_row and exec_row.get("snapshot"):
            snap = exec_row["snapshot"]
            self.machine = CaseMachine(exec_row.get("state", "DRAFT"), exec_row.get("history"))
            self.inputs = snap.get("inputs", self._fresh_inputs())
            for k in ("credential_ref", "session_ref", "application_id", "receipt",
                      "appointment", "confirmation", "reschedules", "fee", "target_slot",
                      "_captcha_return_state", "_awaiting_payment_result"):
                setattr(self, k, snap.get(k))
            self.payment_authorization = snap.get("payment_authorization")
            # Searched slots must survive the reload between signals, else the
            # APPOINTMENT_AVAILABLE step sees an empty list after select/book and
            # falsely reports no availability.
            self._last_slots = snap.get("last_slots", [])
            self.pending = exec_row.get("pending")
        else:
            self.machine = CaseMachine("DRAFT")
            self.inputs = self._fresh_inputs()
            self.credential_ref = self.session_ref = self.application_id = None
            self.receipt = self.appointment = self.confirmation = self.fee = self.target_slot = None
            self.reschedules = 0
            self._last_slots = []
            self.pending = None
            self.payment_authorization = None
            self._awaiting_payment_result = None
        self.artifacts = []

    def _fresh_inputs(self):
        return {"review_approved": False, "authorization_signed": False, "captcha_solved": False,
                "email_verify_token": None, "payment_approved": False, "payment_completed": False,
                "selected_slot_id": None, "reschedule_approved": False, "declaration_completed": False}

    # ---- persistence ----
    def snapshot(self) -> dict:
        return {"inputs": self.inputs, "credential_ref": self.credential_ref,
                "session_ref": self.session_ref, "application_id": self.application_id,
                "receipt": self.receipt, "appointment": self.appointment,
                "confirmation": self.confirmation, "reschedules": self.reschedules,
                "fee": self.fee, "target_slot": self.target_slot,
                "last_slots": getattr(self, "_last_slots", []),
                "payment_authorization": getattr(self, "payment_authorization", None),
                "_captcha_return_state": getattr(self, "_captcha_return_state", None),
                "_awaiting_payment_result": getattr(self, "_awaiting_payment_result", None)}

    def status(self) -> dict:
        return {"case_id": self.case_id, "state": self.machine.state, "pending": self.pending,
                "application_id": self.application_id, "appointment": self.appointment,
                "confirmation": self.confirmation}

    def _emit(self, action, detail=None):
        if self.db is not None:
            audit.record(self.db, org_id=self.org_id, application_id=self.case_id,
                         action=action, detail=detail or {}, actor="ellis")

    def _portal_step_blocked(self, res: dict, *, return_state: str):
        """Common handling for a live portal step that needs the applicant:
        a mid-flow CAPTCHA, missing information (dynamic questions), or an
        applicant-only action. Returns the pause/transition result, or None
        when the response is an ordinary failure."""
        code = res.get("code", "")
        if code == "CAPTCHA_REQUIRED":
            self._captcha_return_state = return_state
            return self.machine.transition("CAPTCHA_ACTION_REQUIRED", "portal CAPTCHA")
        if code == "ADDITIONAL_INFORMATION_REQUIRED":
            return self._pause("Additional information required",
                               "additional_information",
                               questions=res.get("questions") or [])
        if code == "APPLICANT_ACTION_REQUIRED":
            return self._pause("This step needs you in the secure portal window.",
                               res.get("handoff") or "identity",
                               **({"portal_messages": res["portal_messages"]}
                                  if res.get("portal_messages") else {}))
        if code == "OUTCOME_UNCERTAIN":
            return self.machine.transition("MANUAL_REVIEW_REQUIRED",
                                           "outcome uncertain — human check required")
        return None

    def _pause(self, reason, handoff, **extra):
        lv = None
        if handoff in browser.HANDOFF_KINDS:
            lv = browser.create_handoff(kind=handoff, reason=reason, case_id=self.case_id).as_dict()
        self.pending = {"state": self.machine.state, "reason": reason, "handoff": handoff,
                        "live_view": lv, **extra}
        self._emit("handoff_requested", {"state": self.machine.state, "handoff": handoff})
        # TRUTHY on purpose: callers use `return self._pause(...)` and several
        # step branches test that return value (`if blocked is not None`) to
        # distinguish "paused for the applicant" from an ordinary failure. A
        # None here made an upload-stage question read as a failure and dead-
        # ended reversible work into terminal manual review.
        return self.status()

    # ---- signals ----
    def approve_review(self):
        self.inputs["review_approved"] = True
        self._emit("review_approved", {"hash": _hash({"answers": self.answers})})
        return self._drive()

    def sign_authorization(self):
        self.inputs["authorization_signed"] = True
        self._emit("authorization_signed", {"mode": self.authorization.get("mode", "in_app_authorization")})
        return self._drive()

    def solve_captcha(self):
        self.inputs["captcha_solved"] = True
        self._emit("captcha_solved_by_applicant")
        return self._drive()

    def provide_information(self, answers=None):
        """Dynamic missing-information answers (Part 4). The answers were
        validated and persisted by the API layer; here they join the live
        answer set and the SAME portal execution resumes from the same step —
        never a restart, never a duplicate application."""
        answers = answers or {}
        self.answers.update(answers)
        self.pending = None
        self._emit("additional_information_provided",
                   {"keys": sorted(answers.keys())[:40]})
        return self._drive()

    def verify_email(self, token):
        self.inputs["email_verify_token"] = token
        try:
            return self._drive()
        finally:
            # A one-time code must never persist in the durable snapshot —
            # whatever path the drive took, it is gone after this signal.
            self.inputs["email_verify_token"] = None

    def approve_payment(self, amount_cents=None, currency=None):
        # Portal displayed no machine-readable fee (fee_confirmation pause):
        # the applicant confirms the EXACT amount + currency the official
        # portal shows in the secure window. Ellis never invents an amount.
        if self.fee is None:
            import re as _re
            amt = int(amount_cents or 0)
            cur = str(currency or "").upper().strip()
            # Any ISO-4217-shaped code the portal displays is acceptable —
            # a fixed whitelist would strand routes billed in other currencies.
            if amt <= 0 or not _re.fullmatch(r"[A-Z]{3}", cur):
                self._pause("Confirm the exact fee shown by the official portal.",
                            "fee_confirmation", fee_context=self.fee_context)
                return self.status()
            self.fee = {"ok": True, "amount": amt, "currency": cur,
                        "display": f"{amt / 100:.2f} {cur}",
                        "government_fee_cents": amt, "service_fee_cents": 0,
                        "payee": self.adapter.portal_operator,
                        "source": "portal_display_confirmed_by_applicant"}
            self._emit("fee_confirmed_by_applicant", {"amount": amt, "currency": cur})
        # The approval binds to the EXACT fee shown at pause time (§6): amount,
        # currency, payee. When the client echoes the amount it saw, a mismatch
        # with the current fee means the display was stale — refuse and re-show.
        elif amount_cents is not None and self.fee and (
                int(amount_cents) != self.fee["amount"]
                or (currency or self.fee["currency"]) != self.fee["currency"]):
            self._emit("payment_approval_rejected_stale_amount",
                       {"shown_amount_cents": int(amount_cents),
                        "current_amount_cents": self.fee["amount"]})
            self._pause(f"The fee changed. Approve the {self.fee['display']} fee.",
                        "payment_approval", fee=self.fee)
            return self.status()
        if self.fee:
            self.payment_authorization = {
                "amount_cents": self.fee["amount"], "currency": self.fee["currency"],
                "payee": self.adapter.portal_operator, "status": "authorized"}
            if self.payments_hook:
                self.payments_hook("authorized", dict(self.payment_authorization,
                                                      fee=dict(self.fee)))
        self.inputs["payment_approved"] = True
        self._emit("payment_approved", {
            "amount_cents": (self.fee or {}).get("amount"),
            "currency": (self.fee or {}).get("currency"),
            "max_fee_cents": self.authorization.get("max_fee_cents")})
        return self._drive()

    def _payment_authorization_matches_fee(self) -> bool:
        pa = getattr(self, "payment_authorization", None)
        return bool(pa and pa.get("status") == "authorized" and self.fee
                    and pa.get("amount_cents") == self.fee["amount"]
                    and pa.get("currency") == self.fee["currency"])

    def _void_payment_authorization(self, why: str):
        self.inputs["payment_approved"] = False
        self.payment_authorization = None
        if self.payments_hook and self.fee:
            self.payments_hook("invalidated", {"amount_cents": self.fee["amount"],
                                               "currency": self.fee["currency"]})
        self._emit("payment_authorization_invalidated", {"reason": why[:120]})

    def _consume_payment_authorization(self):
        pa = getattr(self, "payment_authorization", None)
        if pa and pa.get("status") == "authorized":
            pa["status"] = "consumed"
            if self.payments_hook:
                self.payments_hook("consumed", dict(pa))

    def complete_payment(self):
        self.inputs["payment_completed"] = True
        self._emit("payment_completed_by_applicant")
        return self._drive()

    def select_appointment(self, slot_id):
        self.inputs["selected_slot_id"] = slot_id
        return self._drive()

    def approve_reschedule(self):
        self.inputs["reschedule_approved"] = True
        return self._drive()

    def complete_declaration(self):
        self.inputs["declaration_completed"] = True
        self._emit("declaration_completed_by_applicant")
        return self._drive()

    def cancel(self, reason="applicant cancelled"):
        self.machine.transition("CANCELLED", reason)
        self.pending = None
        self._emit("cancelled", {"reason": reason})
        return self.status()

    def restore_portal(self):
        """Rebuild the applicant's live portal page after a session loss —
        the secure window must show the real form at its current step, not a
        blank tab. Reversible-only (the driver stops before any handoff or
        irreversible node); the workflow state and the recorded pause stay
        exactly as they were. When the restored page displays the fee and no
        fee is bound yet, the READ amount replaces the type-it-yourself pause
        with an approve-this-exact-amount pause — read, never invented."""
        restore = getattr(self.adapter.driver, "restore_view", None)
        if restore is None:
            return self.status()
        try:
            res = restore()
        except Exception as e:  # noqa: BLE001 — restoration is best-effort
            self._emit("portal_view_restore_failed", {"code": str(e)[:120]})
            return self.status()
        if not res.get("ok"):
            self._emit("portal_view_restore_incomplete",
                       {"code": str(res.get("code", ""))[:60]})
            return self.status()
        fee = res.get("fee")
        if (isinstance(fee, dict) and self.fee is None
                and (self.pending or {}).get("handoff") == "fee_confirmation"):
            self.fee = fee
            self._emit("fee_read_from_portal_page",
                       {"amount": fee["amount"], "currency": fee["currency"]})
            self._pause(f"Approve the {fee['display']} fee.", "payment_approval",
                        fee=self.fee, fee_context=self.fee_context)
        else:
            self._emit("portal_view_restored", {})
        return self.status()

    def start(self):
        # A portal_form pause is confirmed by the applicant's "I completed
        # this step" (which re-drives via start): clear it so the flow
        # re-verifies the page instead of parking on the stale pause.
        if self.pending and self.pending.get("handoff") == "portal_form":
            self.pending = None
        return self._drive()

    # ---- driver ----
    @property
    def _online(self):
        return (self.adapter.channel != "agency"
                and self.adapter.appointment_booking == "prohibited")

    # States whose _step invokes the live portal driver (given current inputs).
    def _state_needs_driver(self, st: str) -> bool:
        if st in ("PORTAL_ACCOUNT_CREATING", "PORTAL_LOGIN_REQUIRED",
                  "APPLICATION_FILLING", "DOCUMENT_UPLOAD_PENDING",
                  "FEE_DISCOVERY_PENDING", "PAYMENT_PROCESSING",
                  "APPOINTMENT_SEARCHING", "APPOINTMENT_BOOKING",
                  "APPOINTMENT_RESCHEDULING", "SUBMITTING"):
            return True
        if st == "CAPTCHA_ACTION_REQUIRED":
            return bool(self.inputs.get("captcha_solved"))
        if st == "PORTAL_VERIFICATION_REQUIRED":
            return bool(self.inputs.get("email_verify_token"))
        if st == "PERSONAL_DECLARATION_REQUIRED":
            return bool(self.inputs.get("declaration_completed"))
        return False

    def _drive(self):
        for _ in range(200):
            st = self.machine.state
            if is_terminal(st):
                self.pending = None
                return self.status()
            if self.defer_live and self._state_needs_driver(st):
                # Foreground callers stop here; a background run continues
                # from this exact persisted state.
                self.deferred_live = True
                return self.status()
            before = st
            try:
                self._step()
            except Exception as e:  # portal/network → recoverable, never a crash
                self.pending = None
                if not is_terminal(self.machine.state):
                    self.machine.transition("RECOVERABLE_FAILURE", str(e)[:80])
                self._emit("recoverable_failure", {"from": before, "code": str(e)[:120]})
                return self.status()
            if self.pending and self.pending["state"] == self.machine.state:
                return self.status()
            if self.machine.state == before and not self.pending:
                return self.status()
        return self.status()

    def _step(self):
        d = self.adapter.driver
        m = self.machine
        st = m.state
        if st == "DRAFT":
            missing = [f for f in self.adapter.required_applicant_fields if not self.answers.get(f)]
            m.transition("DATA_INCOMPLETE" if missing else "READY_FOR_REVIEW",
                         f"missing {missing}" if missing else "")
        elif st == "DATA_INCOMPLETE":
            missing = [f for f in self.adapter.required_applicant_fields if not self.answers.get(f)]
            if not missing:
                m.transition("READY_FOR_REVIEW")
        elif st == "READY_FOR_REVIEW":
            m.transition("APPLICANT_REVIEW_REQUIRED")
        elif st == "APPLICANT_REVIEW_REQUIRED":
            if not self.inputs["review_approved"]:
                return self._pause("Review every answer and approve.", "review")
            self.pending = None
            m.transition("AUTHORIZATION_REQUIRED")
        elif st == "AUTHORIZATION_REQUIRED":
            m.transition("AUTHORIZATION_PENDING")
        elif st == "AUTHORIZATION_PENDING":
            if not self.inputs["authorization_signed"]:
                return self._pause("Sign the Ellis authorization.", "authorization")
            self.pending = None
            m.transition("AUTHORIZATION_SIGNED")
        elif st == "AUTHORIZATION_SIGNED":
            m.transition("PORTAL_ACCOUNT_REQUIRED")
        elif st == "PORTAL_ACCOUNT_REQUIRED":
            # Account-less portals (e.g. Vietnam eVisa personal applications)
            # skip account creation honestly — nothing is registered.
            if not getattr(self.adapter, "account_required", True):
                m.transition("PORTAL_LOGIN_REQUIRED", "portal requires no account")
            else:
                m.transition("PORTAL_ACCOUNT_CREATING")
        elif st == "PORTAL_ACCOUNT_CREATING":
            password = vault.generate_password(self.adapter.password_requirements)
            stored = vault.store(password, {"portal": self.adapter.adapter_id, "kind": "portal_password"})
            self.credential_ref = stored["ref"]
            self._emit("portal_password_stored", {"ref": stored["ref"]})
            res = d.register(email=self.applicant["email"], password=password,
                             full_name=self.applicant.get("full_name", ""))
            if not res["ok"] and res.get("code") == "ACCOUNT_EXISTS":
                return m.transition("PORTAL_LOGIN_REQUIRED", "account exists")
            if not res["ok"]:
                return m.transition("RECOVERABLE_FAILURE", res.get("code", "register"))
            self._pending_captcha = res.get("captchaToken")
            if res.get("captchaToken"):
                return m.transition("CAPTCHA_ACTION_REQUIRED")
            if res.get("needsEmailVerification"):
                return m.transition("PORTAL_VERIFICATION_REQUIRED")
            m.transition("PORTAL_ACCOUNT_READY")
        elif st == "CAPTCHA_ACTION_REQUIRED":
            if not self.inputs["captcha_solved"]:
                return self._pause("Complete the CAPTCHA. Ellis never solves it.", "captcha")
            ok = d.submit_captcha(captcha_token=getattr(self, "_pending_captcha", None),
                                  human_answer=browser.HUMAN_MARKERS["captcha"])
            self.inputs["captcha_solved"] = False
            self.pending = None
            if not ok["ok"]:
                return m.transition("RECOVERABLE_FAILURE", "CAPTCHA_FAILED")
            # A CAPTCHA can appear mid-form (application filling) as well as at
            # account creation; return to wherever it interrupted.
            m.transition(getattr(self, "_captcha_return_state", None)
                         or "PORTAL_VERIFICATION_REQUIRED")
            self._captcha_return_state = None
        elif st == "PORTAL_VERIFICATION_REQUIRED":
            if not self.inputs["email_verify_token"]:
                return self._pause("Click the verification link in your email.", "email_verification")
            ok = d.verify_email(token=self.inputs["email_verify_token"])
            self.inputs["email_verify_token"] = None
            self.pending = None
            if not ok["ok"]:
                return self._pause("That link did not verify; open the latest email.", "email_verification")
            m.transition("PORTAL_ACCOUNT_READY")
        elif st == "PORTAL_ACCOUNT_READY":
            m.transition("PORTAL_LOGIN_REQUIRED")
        elif st == "PORTAL_LOGIN_REQUIRED":
            if not getattr(self.adapter, "account_required", True):
                # Account-less portal: no stored credential exists (none was
                # ever created). The driver returns its live-session reference.
                res = d.login(email=self.applicant.get("email", ""))
            else:
                password = vault.reveal(self.credential_ref)
                res = d.login(email=self.applicant["email"], password=password)
            if not res["ok"]:
                return m.transition("RECOVERABLE_FAILURE", res.get("code", "login"))
            self.session_ref = vault.store(res["sessionToken"], {"kind": "portal_session"})["ref"]
            self._emit("portal_login", {"session_ref": self.session_ref})
            m.transition("APPLICATION_FILLING")
        elif st == "APPLICATION_FILLING":
            token = vault.reveal(self.session_ref)
            # A mid-form OTP/verification pause is consumed HERE: the flow's
            # handoff node must be stepped past (driver.verify_email) before
            # resuming the fill, or the same pause would recur forever.
            if self.inputs.get("email_verify_token"):
                d.verify_email(token=self.inputs["email_verify_token"])
                self.inputs["email_verify_token"] = None
                self.pending = None
            # Released-flow drivers are resumable (they continue the SAME portal
            # session from the same step), so re-entering this state after a
            # question/CAPTCHA advances the form instead of creating a duplicate.
            resumable = getattr(self.adapter, "channel", "") == "released_flow"
            if not self.application_id or resumable:
                res = d.create_application(session_token=token, answers=self.answers)
                if not res["ok"]:
                    blocked = self._portal_step_blocked(res, return_state="APPLICATION_FILLING")
                    if blocked is not None:
                        return blocked
                    return m.transition("RECOVERABLE_FAILURE", res.get("code", "apply"))
                if not self.application_id:
                    self.application_id = res.get("applicationId") or self.case_id
                    self._emit("application_created", {"application_id": self.application_id})
            m.transition("DOCUMENT_UPLOAD_PENDING")
        elif st == "DOCUMENT_UPLOAD_PENDING":
            token = vault.reveal(self.session_ref)
            for doc in self.documents:
                res = d.upload_document(session_token=token, application_id=self.application_id,
                                        name=doc["name"], size_bytes=doc.get("size_bytes", 1024),
                                        mime=doc.get("mime", "application/pdf"))
                if not res["ok"]:
                    blocked = self._portal_step_blocked(res, return_state="DOCUMENT_UPLOAD_PENDING")
                    if blocked is not None:
                        return blocked
                    return m.transition("RECOVERABLE_FAILURE", f"upload:{res.get('code')}")
            self._emit("documents_uploaded", {"count": len(self.documents)})
            m.transition("FEE_DISCOVERY_PENDING")
        elif st == "FEE_DISCOVERY_PENDING":
            token = vault.reveal(self.session_ref)
            fee = d.discover_fee(session_token=token, application_id=self.application_id)
            if not fee["ok"]:
                if fee.get("code") == "FEE_NOT_DISPLAYED":
                    # The portal did not display a machine-readable fee. Never
                    # invent one: the applicant confirms the exact fee shown in
                    # the secure portal window at the approval step instead.
                    self.fee = None
                    self._emit("fee_not_machine_readable", {})
                    return m.transition("PAYMENT_APPROVAL_REQUIRED",
                                        "fee to be confirmed by the applicant")
                blocked = self._portal_step_blocked(fee, return_state="FEE_DISCOVERY_PENDING")
                if blocked is not None:
                    return blocked
                return m.transition("RECOVERABLE_FAILURE", "fee")
            self.fee = fee
            self._emit("fee_discovered", {"amount": fee["amount"], "currency": fee["currency"]})
            m.transition("PAYMENT_APPROVAL_REQUIRED")
        elif st == "PAYMENT_APPROVAL_REQUIRED":
            # An approval is valid only for the EXACT amount + currency shown.
            if self.inputs["payment_approved"] and not self._payment_authorization_matches_fee():
                self._void_payment_authorization("fee changed since approval")
            if not self.inputs["payment_approved"]:
                if not self.fee:
                    return self._pause(
                        "Confirm the exact fee shown by the official portal.",
                        "fee_confirmation", fee_context=self.fee_context)
                return self._pause(f"Approve the {self.fee['display']} fee.", "payment_approval",
                                   fee=self.fee, fee_context=self.fee_context)
            mx = self.authorization.get("max_fee_cents")
            auth_cur = str(self.authorization.get("currency") or "").upper()
            # The cap is only meaningful in its own currency: comparing raw
            # integers across currencies would brick honest confirmations
            # (e.g. a VND portal fee against a USD cap).
            comparable = not auth_cur or auth_cur == self.fee["currency"]
            if mx is not None and comparable and self.fee["amount"] > mx:
                self.pending = None
                return m.transition("MANUAL_REVIEW_REQUIRED", "fee exceeds authorized maximum")
            if mx is not None and not comparable:
                self._emit("fee_cap_not_comparable",
                           {"cap_currency": auth_cur, "fee_currency": self.fee["currency"]})
            self.pending = None
            m.transition("PAYMENT_ACTION_REQUIRED")
        elif st == "PAYMENT_ACTION_REQUIRED":
            from .providers.payment import choose_mode
            plan = choose_mode(adapter_third_party_policy=self.adapter.third_party_payment_policy,
                               amount_cents=self.fee["amount"], currency=self.fee["currency"],
                               applicant_approved=self.inputs["payment_approved"])
            if plan.mode == "applicant_window" and not self.inputs["payment_completed"]:
                return self._pause("Complete the payment in the secure window. Ellis never sees your card.",
                                   "payment", fee=self.fee)
            self.pending = None
            m.transition("PAYMENT_PROCESSING")
        elif st == "PAYMENT_PROCESSING":
            token = vault.reveal(self.session_ref)
            cur = d.get_application_state(session_token=token, application_id=self.application_id)
            if cur["ok"] and cur["paid"]:
                self.receipt = cur.get("receipt") or self.receipt
                self._consume_payment_authorization()
                self._awaiting_payment_result = None
                return m.transition("PAYMENT_COMPLETED", "already paid (reconciled)")
            # Final pre-charge check: the authorization must still match the
            # exact fee. A drift here voids it and returns to approval (§6).
            if not self._payment_authorization_matches_fee():
                self._void_payment_authorization("authorization no longer matches the exact fee")
                return m.transition("PAYMENT_APPROVAL_REQUIRED", "exact-amount re-approval required")
            res = d.pay(session_token=token, application_id=self.application_id, payment_ref=self.case_id)
            if res["ok"]:
                self._awaiting_payment_result = None
            if not res["ok"] and res.get("code") == "REQUIRES_3DS":
                self._awaiting_payment_result = None
                self.inputs["payment_completed"] = False
                return m.transition("PAYMENT_ACTION_REQUIRED", "3DS required")
            if not res["ok"] and res.get("code") == "OUTCOME_UNCERTAIN":
                # The portal's payment result is not machine-readable here.
                # Never assume success: ask the applicant what the OFFICIAL
                # portal actually showed, and record it as applicant-reported.
                reported = (self.answers.get("payment_result", "")
                            if getattr(self, "_awaiting_payment_result", None) else "")
                if reported == "The portal confirmed my payment":
                    ref = str(self.answers.get("payment_receipt_reference", "") or "")[:80]
                    self._awaiting_payment_result = None
                    self.receipt = {"receiptNo": ref, "source": "applicant_reported"}
                    self._consume_payment_authorization()
                    self._emit("payment_reported_by_applicant", {"has_reference": bool(ref)})
                    return m.transition("PAYMENT_COMPLETED",
                                        "applicant-verified payment (portal result not machine-readable)")
                if reported == "Payment failed or was cancelled":
                    self._awaiting_payment_result = None
                    self.inputs["payment_completed"] = False
                    return m.transition("PAYMENT_ACTION_REQUIRED",
                                        "applicant reported payment failure")
                self._awaiting_payment_result = True
                return self._pause(
                    "Tell Ellis what the official portal showed for your payment.",
                    "additional_information", purpose="payment_verification",
                    questions=[
                        {"key": "payment_result", "kind": "select", "mandatory": True,
                         "question": "What did the official portal show for your payment?",
                         "why": ("The portal's payment result was not readable automatically. "
                                 "Ellis never assumes a payment succeeded."),
                         "options": ["The portal confirmed my payment",
                                     "Payment failed or was cancelled",
                                     "The portal is still processing"]},
                        {"key": "payment_receipt_reference", "kind": "text", "mandatory": False,
                         "question": "Receipt or transaction reference shown by the portal (if any)",
                         "why": "Saved with your case as part of the official record.",
                         "format": "exactly as the portal shows it"}])
            if not res["ok"]:
                return m.transition("RECOVERABLE_FAILURE", res.get("code", "pay"))
            self.receipt = res["receipt"]
            self._consume_payment_authorization()
            self.artifacts.append({"kind": "receipt", "value": res["receipt"]})
            self._emit("payment_captured", {"receipt_no": res["receipt"]["receiptNo"]})
            m.transition("PAYMENT_COMPLETED")
        elif st == "PAYMENT_COMPLETED":
            # Online e-visa routes skip appointments entirely.
            if self.adapter.appointment_search == "none" or self.adapter.appointment_booking == "prohibited":
                m.transition("FINAL_REVIEW_REQUIRED")
            else:
                m.transition("APPOINTMENT_SEARCHING")
        elif st == "APPOINTMENT_SEARCHING":
            token = vault.reveal(self.session_ref)
            res = d.search_appointments(session_token=token,
                                        location_ids=[x for x in [self.prefs["preferredLocation"],
                                                      *self.prefs["alternativeLocations"]] if x])
            if not res["ok"]:
                return m.transition("RECOVERABLE_FAILURE", "search")
            self._last_slots = res["slots"]
            self._emit("appointments_checked", {"count": len(res["slots"])})
            m.transition("APPOINTMENT_AVAILABLE")
        elif st == "APPOINTMENT_AVAILABLE":
            problems = validate_preferences(self.prefs)
            if problems:
                return m.transition("MANUAL_REVIEW_REQUIRED", f"pref: {problems[0]}")
            earliest = earliest_qualifying(getattr(self, "_last_slots", []), self.prefs)
            if not earliest:
                return self._pause("No appointment matches your preferences yet.", "no_availability")
            if self.prefs["allowAutoBook"] or self.inputs["selected_slot_id"]:
                sel = self.inputs["selected_slot_id"]
                self.target_slot = next((s for s in self._last_slots if s["slotId"] == sel), earliest) if sel else earliest
                self.pending = None
                return m.transition("APPOINTMENT_BOOKING")
            return self._pause("Choose an appointment from your calendar.", "appointment_selection",
                               slots=self._last_slots[:20])
        elif st == "APPOINTMENT_BOOKING":
            token = vault.reveal(self.session_ref)
            fresh = d.search_appointments(session_token=token)
            still = fresh["ok"] and any(s["slotId"] == self.target_slot["slotId"] for s in fresh["slots"])
            if not still:
                self.inputs["selected_slot_id"] = None
                return m.transition("APPOINTMENT_AVAILABLE", "slot gone")
            res = d.book_appointment(session_token=token, application_id=self.application_id,
                                     slot_id=self.target_slot["slotId"])
            if not res["ok"]:
                return m.transition("APPOINTMENT_AVAILABLE", res.get("code", "book"))
            self.appointment = res["appointment"]
            self.artifacts.append({"kind": "appointment", "value": res["appointment"]})
            self._emit("appointment_booked", {"confirmation_no": res["appointment"]["confirmationNo"]})
            self._notify("appointment", f"Your {self.adapter.destination_country} visa appointment is booked.")
            m.transition("APPOINTMENT_BOOKED")
        elif st == "APPOINTMENT_BOOKED":
            if self.prefs["allowAutoReschedule"] and self.reschedules < self.prefs["maxAutoReschedules"]:
                token = vault.reveal(self.session_ref)
                fresh = d.search_appointments(session_token=token)
                cand = earliest_qualifying(fresh.get("slots", []), self.prefs)
                if cand and is_improvement(self.appointment["startUtc"], cand["startUtc"], self.prefs):
                    self.target_slot = cand
                    if self.prefs.get("askBeforeReschedule") and not self.inputs["reschedule_approved"]:
                        return self._pause("An earlier appointment is available. Approve reschedule?",
                                           "reschedule_approval", candidate=cand)
                    return m.transition("APPOINTMENT_RESCHEDULING")
            m.transition("FINAL_REVIEW_REQUIRED")
        elif st == "APPOINTMENT_RESCHEDULING":
            self.inputs["reschedule_approved"] = False
            self.pending = None
            token = vault.reveal(self.session_ref)
            res = d.reschedule_appointment(session_token=token, application_id=self.application_id,
                                           new_slot_id=self.target_slot["slotId"])
            if not res["ok"]:
                return m.transition("APPOINTMENT_BOOKED", res.get("code", "reschedule"))
            self.appointment = res["appointment"]
            self.reschedules += 1
            self._emit("appointment_rescheduled", {"count": self.reschedules})
            m.transition("APPOINTMENT_BOOKED")
        elif st == "RECOVERABLE_FAILURE":
            # Bounded automatic retry: resume at the state that failed when the
            # machine allows it; repeated failures stop honestly for a human.
            hist = self.machine.history
            retries = sum(1 for h in hist[-8:] if h["state"] == "RECOVERABLE_FAILURE")
            last_reason = (hist[-1].get("reason") or "") if hist else ""
            prior = next((h["state"] for h in reversed(hist[:-1])
                          if h["state"] != "RECOVERABLE_FAILURE"), None)
            # A lost vault ref / portal session is repaired by re-login (which
            # re-mints the session reference), not by re-running the failed step.
            if "vault ref" in last_reason or "session" in last_reason.lower():
                self.session_ref = None
                prior = "PORTAL_LOGIN_REQUIRED"
            from .statemachine import can_transition
            if retries > 3 or not prior or not can_transition(st, prior):
                return m.transition("MANUAL_REVIEW_REQUIRED",
                                    "repeated recoverable failures")
            m.transition(prior, "automatic retry after recoverable failure")
        elif st == "FINAL_REVIEW_REQUIRED":
            m.transition("PERSONAL_DECLARATION_REQUIRED" if self.adapter.personal_declaration_required
                         else "READY_TO_SUBMIT")
        elif st == "PERSONAL_DECLARATION_REQUIRED":
            if not self.inputs["declaration_completed"]:
                return self._pause("Only you can sign the declaration under penalty of perjury.",
                                   "personal_declaration")
            token = vault.reveal(self.session_ref)
            ok = d.declare_personally(session_token=token, application_id=self.application_id,
                                      human_confirmed=browser.HUMAN_MARKERS["personal_declaration"])
            self.pending = None
            if not ok["ok"]:
                return m.transition("RECOVERABLE_FAILURE", "declaration")
            m.transition("READY_TO_SUBMIT")
        elif st == "READY_TO_SUBMIT":
            # §25: submission needs a valid standing authorization + a signed,
            # unchanged final review version. The DB-backed gate is injected by
            # the service layer; without one (pure unit tests) nothing blocks.
            if self.submission_gate:
                ok, why = self.submission_gate()
                if not ok:
                    return self._pause(why or "Final review and signature required.",
                                       "final_review")
            # A background run may cross into SUBMITTING only when it was
            # enqueued by the applicant's explicit submission confirmation —
            # a worker on its own can never submit.
            if self.block_submit:
                return self._pause("Review and confirm your submission to continue.",
                                   "final_review")
            self.pending = None
            m.transition("SUBMITTING")
        elif st == "SUBMITTING":
            token = vault.reveal(self.session_ref)
            cur = d.get_application_state(session_token=token, application_id=self.application_id)
            if cur["ok"] and cur["submitted"]:
                self.confirmation = cur["confirmation"]
                return m.transition("SUBMITTED", "already submitted (reconciled)")
            # Not yet submitted: a retry/background run without the applicant's
            # explicit confirmation stops here — reconcile only, never the click.
            if self.block_submit:
                return self._pause("Review and confirm your submission to continue.",
                                   "final_review")
            res = d.submit(session_token=token, application_id=self.application_id)
            if not res["ok"]:
                return m.transition("RECOVERABLE_FAILURE", res.get("code", "submit"))
            self.confirmation = res["confirmation"]
            self.artifacts.append({"kind": "confirmation", "value": res["confirmation"]})
            self._emit("submitted", {"reference_no": res["confirmation"]["referenceNo"]})
            m.transition("SUBMITTED")
        elif st == "SUBMITTED":
            m.transition("CONFIRMATION_PENDING")
        elif st == "CONFIRMATION_PENDING":
            self._notify("submitted", f"Your {self.adapter.destination_country} visa application is "
                                      f"submitted. Reference {self.confirmation['referenceNo']}.")
            self._emit("completed", {"reference_no": self.confirmation["referenceNo"]})
            m.transition("COMPLETED")

    def _notify(self, kind, body):
        if self.emailer:
            try:
                self.emailer(to=self.applicant["email"],
                             subject=f"{self.adapter.destination_country} visa — {kind}", body=body)
            except Exception:
                pass
