"""In-process mock visa portal (Python) — the safe target for automated tests.
Mirrors the JS mock: register/verify/captcha/login/apply/upload/fee/pay(3ds)/
appointments(concurrent)/book/reschedule/declare/submit/reconcile, plus
maintenance + session expiry. Never a real government portal."""
from __future__ import annotations

import hashlib
import time
import uuid

HOST = "portal.mockland.gov.example"
MOCK_BASE = f"https://{HOST}"


def _uid(n=8):
    return uuid.uuid4().hex[:n].upper()


def _hash(s):
    return hashlib.sha256(str(s).encode()).hexdigest()


class MockPortal:
    # The driver truthfully declares what it is. execution.classify_driver reads
    # this so a MockPortal result is ALWAYS classified MOCK — no configuration can
    # make an in-process mock produce a "real" (LIVE_PRODUCTION) classification.
    execution_class = "MOCK"

    def __init__(self, *, maintenance=False, payment_outcome="success",
                 require_captcha=True, session_ttl_ms=30 * 60_000, now=None, inventory=None):
        self.maintenance = maintenance
        self.payment_outcome = payment_outcome
        self.require_captcha = require_captcha
        self.session_ttl_ms = session_ttl_ms
        self.now = now or (lambda: int(time.time() * 1000))
        self.accounts, self.sessions, self.applications = {}, {}, {}
        self.emails = []
        self.log = []
        self.inventory = inventory if inventory is not None else self._seed()

    def _seed(self):
        out, base = [], self.now()
        for d in range(3, 45):
            day = base + d * 86_400_000
            wd = time.gmtime(day / 1000).tm_wday
            if wd >= 5:
                continue
            for h in (9, 11, 14):
                for loc in ("MOCKLAND-CAP", "MOCKLAND-NORTH"):
                    start = day - (day % 86_400_000) + h * 3_600_000
                    out.append({"slotId": f"S-{loc}-{start}", "locationId": loc,
                                "startUtc": start, "taken": False})
        return out

    def _up(self):
        if self.maintenance:
            raise RuntimeError("MAINTENANCE")

    def _session(self, token):
        s = self.sessions.get(token)
        if not s:
            raise RuntimeError("NO_SESSION")
        if self.now() - s["createdAt"] > self.session_ttl_ms:
            del self.sessions[token]
            raise RuntimeError("SESSION_EXPIRED")
        return s

    def register(self, *, email, password, full_name=""):
        self._up()
        if not email or not password:
            return {"ok": False, "code": "MISSING_FIELDS"}
        if email in self.accounts:
            return {"ok": False, "code": "ACCOUNT_EXISTS"}
        if len(password) < 12:
            return {"ok": False, "code": "WEAK_PASSWORD"}
        vt = _uid(12)
        self.accounts[email] = {"pw": _hash(password), "verified": False, "vt": vt}
        self.emails.append({"to": email, "kind": "verification",
                            "link": f"{MOCK_BASE}/verify?token={vt}"})
        return {"ok": True, "captchaToken": _uid() if self.require_captcha else None,
                "needsEmailVerification": True}

    def submit_captcha(self, *, captcha_token, human_answer):
        self._up()
        if human_answer == "HUMAN_SOLVED":
            return {"ok": True}
        return {"ok": False, "code": "CAPTCHA_FAILED"}

    def verify_email(self, *, token):
        self._up()
        for acc in self.accounts.values():
            if acc["vt"] == token:
                acc["verified"] = True
                return {"ok": True}
        return {"ok": False, "code": "BAD_TOKEN"}

    def login(self, *, email, password):
        self._up()
        acc = self.accounts.get(email)
        if not acc or acc["pw"] != _hash(password):
            return {"ok": False, "code": "BAD_CREDENTIALS"}
        if not acc["verified"]:
            return {"ok": False, "code": "NOT_VERIFIED"}
        tok = _uid(24)
        self.sessions[tok] = {"email": email, "createdAt": self.now()}
        return {"ok": True, "sessionToken": tok}

    def create_application(self, *, session_token, answers):
        s = self._session(session_token)
        aid = "APP-" + _uid()
        self.applications[aid] = {"owner": s["email"], "answers": dict(answers), "documents": [],
                                  "paid": False, "declared": False, "submitted": False,
                                  "appointment": None, "receipt": None, "confirmation": None}
        return {"ok": True, "applicationId": aid}

    def _app(self, session_token, application_id):
        s = self._session(session_token)
        app = self.applications.get(application_id)
        if not app or app["owner"] != s["email"]:
            return None
        return app

    def upload_document(self, *, session_token, application_id, name, size_bytes, mime):
        app = self._app(session_token, application_id)
        if app is None:
            return {"ok": False, "code": "NOT_FOUND"}
        if size_bytes > 10 * 1024 * 1024:
            return {"ok": False, "code": "FILE_TOO_LARGE"}
        if mime not in ("application/pdf", "image/jpeg", "image/png"):
            return {"ok": False, "code": "BAD_MIME"}
        app["documents"].append({"name": name})
        return {"ok": True, "documentCount": len(app["documents"])}

    def discover_fee(self, *, session_token, application_id):
        app = self._app(session_token, application_id)
        if app is None:
            return {"ok": False, "code": "NOT_FOUND"}
        return {"ok": True, "amount": 8000, "currency": "USD", "display": "US$80.00"}

    def pay(self, *, session_token, application_id, payment_ref=None):
        app = self._app(session_token, application_id)
        if app is None:
            return {"ok": False, "code": "NOT_FOUND"}
        if self.payment_outcome == "decline":
            return {"ok": False, "code": "PAYMENT_DECLINED"}
        if self.payment_outcome == "3ds":
            return {"ok": False, "code": "REQUIRES_3DS", "threeDsToken": _uid()}
        app["paid"] = True
        app["receipt"] = {"receiptNo": "RCPT-" + _uid(), "amount": 8000, "currency": "USD"}
        # Append-only side-effect ledger — lets a durability drill COUNT actual
        # charges and prove no double-charge across a worker restart/retry.
        self.log.append({"event": "charge", "applicationId": application_id,
                         "receiptNo": app["receipt"]["receiptNo"]})
        return {"ok": True, "receipt": app["receipt"]}

    def search_appointments(self, *, session_token, location_ids=None):
        self._session(session_token)
        wanted = set(location_ids or [i["locationId"] for i in self.inventory])
        slots = [{"slotId": i["slotId"], "locationId": i["locationId"], "startUtc": i["startUtc"]}
                 for i in self.inventory if not i["taken"] and i["locationId"] in wanted]
        slots.sort(key=lambda x: x["startUtc"])
        return {"ok": True, "slots": slots, "checkedAt": self.now()}

    def book_appointment(self, *, session_token, application_id, slot_id):
        app = self._app(session_token, application_id)
        if app is None:
            return {"ok": False, "code": "NOT_FOUND"}
        slot = next((i for i in self.inventory if i["slotId"] == slot_id), None)
        if not slot:
            return {"ok": False, "code": "SLOT_GONE"}
        if slot["taken"]:
            return {"ok": False, "code": "SLOT_TAKEN"}
        slot["taken"] = True
        app["appointment"] = {"slotId": slot_id, "locationId": slot["locationId"],
                              "startUtc": slot["startUtc"], "confirmationNo": "APT-" + _uid()}
        self.log.append({"event": "book", "applicationId": application_id, "slotId": slot_id})
        return {"ok": True, "appointment": app["appointment"]}

    def reschedule_appointment(self, *, session_token, application_id, new_slot_id):
        app = self._app(session_token, application_id)
        if app is None or not app["appointment"]:
            return {"ok": False, "code": "NOT_FOUND"}
        nxt = next((i for i in self.inventory if i["slotId"] == new_slot_id), None)
        if not nxt or nxt["taken"]:
            return {"ok": False, "code": "SLOT_TAKEN"}
        prev = next((i for i in self.inventory if i["slotId"] == app["appointment"]["slotId"]), None)
        nxt["taken"] = True
        if prev:
            prev["taken"] = False
        app["appointment"] = {"slotId": new_slot_id, "locationId": nxt["locationId"],
                              "startUtc": nxt["startUtc"], "confirmationNo": "APT-" + _uid()}
        return {"ok": True, "appointment": app["appointment"]}

    def declare_personally(self, *, session_token, application_id, human_confirmed):
        app = self._app(session_token, application_id)
        if app is None:
            return {"ok": False, "code": "NOT_FOUND"}
        if human_confirmed != "HUMAN_DECLARED":
            return {"ok": False, "code": "DECLARATION_REQUIRED"}
        app["declared"] = True
        return {"ok": True}

    def submit(self, *, session_token, application_id):
        app = self._app(session_token, application_id)
        if app is None:
            return {"ok": False, "code": "NOT_FOUND"}
        if not app["paid"]:
            return {"ok": False, "code": "NOT_PAID"}
        if not app["declared"]:
            return {"ok": False, "code": "DECLARATION_REQUIRED"}
        if app["submitted"]:
            return {"ok": True, "idempotent": True, "confirmation": app["confirmation"]}
        app["submitted"] = True
        app["confirmation"] = {"referenceNo": "REF-" + _uid(10)}
        self.log.append({"event": "submit", "applicationId": application_id,
                         "referenceNo": app["confirmation"]["referenceNo"]})
        return {"ok": True, "confirmation": app["confirmation"],
                "receipt": app["receipt"], "appointment": app["appointment"]}

    def get_application_state(self, *, session_token, application_id):
        app = self._app(session_token, application_id)
        if app is None:
            return {"ok": False, "code": "NOT_FOUND"}
        return {"ok": True, "paid": app["paid"], "declared": app["declared"],
                "submitted": app["submitted"], "appointment": app["appointment"],
                "confirmation": app["confirmation"], "receipt": app["receipt"]}

    # --- Durable persistence: the portal state survives a worker restart, so
    # the restart demo is faithful (in production the portal is the persistent
    # government server; reconciliation re-reads its authoritative state). ---
    def to_state(self) -> dict:
        return {"maintenance": self.maintenance, "payment_outcome": self.payment_outcome,
                "require_captcha": self.require_captcha, "session_ttl_ms": self.session_ttl_ms,
                "accounts": self.accounts, "sessions": self.sessions,
                "applications": self.applications, "emails": self.emails,
                "inventory": self.inventory, "log": self.log}

    @classmethod
    def from_state(cls, state: dict) -> "MockPortal":
        p = cls(maintenance=state.get("maintenance", False),
                payment_outcome=state.get("payment_outcome", "success"),
                require_captcha=state.get("require_captcha", True),
                session_ttl_ms=state.get("session_ttl_ms", 30 * 60_000),
                inventory=state.get("inventory"))
        p.accounts = state.get("accounts", {})
        p.sessions = state.get("sessions", {})
        p.applications = state.get("applications", {})
        p.emails = state.get("emails", [])
        p.log = state.get("log", [])
        return p
