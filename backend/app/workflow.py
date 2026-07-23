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

        if exec_row and exec_row.get("snapshot"):
            snap = exec_row["snapshot"]
            self.machine = CaseMachine(exec_row.get("state", "DRAFT"), exec_row.get("history"))
            self.inputs = snap.get("inputs", self._fresh_inputs())
            for k in ("credential_ref", "session_ref", "application_id", "receipt",
                      "appointment", "confirmation", "reschedules", "fee", "target_slot"):
                setattr(self, k, snap.get(k))
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
                "last_slots": getattr(self, "_last_slots", [])}

    def status(self) -> dict:
        return {"case_id": self.case_id, "state": self.machine.state, "pending": self.pending,
                "application_id": self.application_id, "appointment": self.appointment,
                "confirmation": self.confirmation}

    def _emit(self, action, detail=None):
        if self.db is not None:
            audit.record(self.db, org_id=self.org_id, application_id=self.case_id,
                         action=action, detail=detail or {}, actor="ellis")

    def _pause(self, reason, handoff, **extra):
        lv = None
        if handoff in browser.HANDOFF_KINDS:
            lv = browser.create_handoff(kind=handoff, reason=reason, case_id=self.case_id).as_dict()
        self.pending = {"state": self.machine.state, "reason": reason, "handoff": handoff,
                        "live_view": lv, **extra}
        self._emit("handoff_requested", {"state": self.machine.state, "handoff": handoff})

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

    def verify_email(self, token):
        self.inputs["email_verify_token"] = token
        return self._drive()

    def approve_payment(self):
        self.inputs["payment_approved"] = True
        self._emit("payment_approved", {"max_fee_cents": self.authorization.get("max_fee_cents")})
        return self._drive()

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

    def start(self):
        return self._drive()

    # ---- driver ----
    @property
    def _online(self):
        return (self.adapter.channel != "agency"
                and self.adapter.appointment_booking == "prohibited")

    def _drive(self):
        for _ in range(200):
            st = self.machine.state
            if is_terminal(st):
                self.pending = None
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
            m.transition("PORTAL_VERIFICATION_REQUIRED")
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
            password = vault.reveal(self.credential_ref)
            res = d.login(email=self.applicant["email"], password=password)
            if not res["ok"]:
                return m.transition("RECOVERABLE_FAILURE", res.get("code", "login"))
            self.session_ref = vault.store(res["sessionToken"], {"kind": "portal_session"})["ref"]
            self._emit("portal_login", {"session_ref": self.session_ref})
            m.transition("APPLICATION_FILLING")
        elif st == "APPLICATION_FILLING":
            token = vault.reveal(self.session_ref)
            if not self.application_id:
                res = d.create_application(session_token=token, answers=self.answers)
                if not res["ok"]:
                    return m.transition("RECOVERABLE_FAILURE", res.get("code", "apply"))
                self.application_id = res["applicationId"]
                self._emit("application_created", {"application_id": self.application_id})
            m.transition("DOCUMENT_UPLOAD_PENDING")
        elif st == "DOCUMENT_UPLOAD_PENDING":
            token = vault.reveal(self.session_ref)
            for doc in self.documents:
                res = d.upload_document(session_token=token, application_id=self.application_id,
                                        name=doc["name"], size_bytes=doc.get("size_bytes", 1024),
                                        mime=doc.get("mime", "application/pdf"))
                if not res["ok"]:
                    return m.transition("RECOVERABLE_FAILURE", f"upload:{res.get('code')}")
            self._emit("documents_uploaded", {"count": len(self.documents)})
            m.transition("FEE_DISCOVERY_PENDING")
        elif st == "FEE_DISCOVERY_PENDING":
            token = vault.reveal(self.session_ref)
            fee = d.discover_fee(session_token=token, application_id=self.application_id)
            if not fee["ok"]:
                return m.transition("RECOVERABLE_FAILURE", "fee")
            self.fee = fee
            self._emit("fee_discovered", {"amount": fee["amount"], "currency": fee["currency"]})
            m.transition("PAYMENT_APPROVAL_REQUIRED")
        elif st == "PAYMENT_APPROVAL_REQUIRED":
            if not self.inputs["payment_approved"]:
                return self._pause(f"Approve the {self.fee['display']} fee.", "payment_approval", fee=self.fee)
            mx = self.authorization.get("max_fee_cents")
            if mx is not None and self.fee["amount"] > mx:
                self.pending = None
                return m.transition("MANUAL_REVIEW_REQUIRED", "fee exceeds authorized maximum")
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
                return m.transition("PAYMENT_COMPLETED", "already paid (reconciled)")
            res = d.pay(session_token=token, application_id=self.application_id, payment_ref=self.case_id)
            if not res["ok"] and res.get("code") == "REQUIRES_3DS":
                self.inputs["payment_completed"] = False
                return m.transition("PAYMENT_ACTION_REQUIRED", "3DS required")
            if not res["ok"]:
                return m.transition("RECOVERABLE_FAILURE", res.get("code", "pay"))
            self.receipt = res["receipt"]
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
            m.transition("SUBMITTING")
        elif st == "SUBMITTING":
            token = vault.reveal(self.session_ref)
            cur = d.get_application_state(session_token=token, application_id=self.application_id)
            if cur["ok"] and cur["submitted"]:
                self.confirmation = cur["confirmation"]
                return m.transition("SUBMITTED", "already submitted (reconciled)")
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
