"""Real route-specific portal driver over Browserbase Live View (brief §19-25).

BrowserbaseLiveViewDriver drives a VERIFIED official portal through an isolated
Browserbase session using DETERMINISTIC Playwright selectors that come from the
adapter config (never invented at runtime). It is the real counterpart to
MockPortalDriver and shares the same 15-method interface, so the workflow does
not change.

Absolute safety invariants (enforced here, not just documented):
  * Fail-closed construction. The driver refuses to exist unless the adapter is
    production-approved AND a real Browserbase key is configured AND the runtime
    mode is real-only. Outside that it raises — it can NEVER silently stand in
    for the mock, and the mock can never stand in for it.
  * Evidence-only success. Payment/appointment/submission success is read from
    OFFICIAL portal DOM/state via the adapter's extraction selectors. If the
    evidence is absent the result is OUTCOME_UNCERTAIN — success is NEVER
    inferred from a local callback, a click, a redirect, or an LLM opinion.
  * Reconcile before every irreversible action. pay/book/submit first read the
    official application state; if the action already succeeded they return that
    prior official result instead of repeating it (no duplicate pay/submit).
  * No secret capture. CAPTCHA, OTP, passwords, card/CVV/3-DS, and personal
    declarations are surfaced to the applicant via Live View and completed
    personally; the driver refuses to receive or log those values.
  * Hostname allow-listing. Navigation is confined to the adapter's approved
    domains; an unexpected host aborts the step.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from ..config import settings
from ..providers import browser as bb
from .driver_factory import (STOP_APPLICANT_ACTION_REQUIRED, STOP_OUTCOME_UNCERTAIN,
                             STOP_PORTAL_UNAVAILABLE, RealOnlyStop)

# The driver self-declares its execution class. Bound only to a production-
# approved adapter, so classify_driver() reports LIVE_PRODUCTION honestly; the
# adapter-level clamp still caps it if the adapter is not enabled/approved.
LIVE_PRODUCTION = "LIVE_PRODUCTION"

# Steps that MUST be completed personally by the applicant through Live View.
# The driver refuses to perform or receive the secret for any of these.
PERSONAL_ONLY_STEPS = ("captcha", "otp", "password", "payment_secret",
                       "three_ds", "personal_declaration")


class SecretCaptureRefused(RuntimeError):
    """Raised if any caller tries to hand the live driver a sensitive value."""


@dataclass
class _Evidence:
    """Official evidence extracted from the portal — the ONLY basis for success."""
    ok: bool
    status: str                     # e.g. "PAID" | "SUBMITTED" | "OUTCOME_UNCERTAIN"
    reference: str | None = None    # official registration/confirmation code
    receipt: str | None = None
    raw: dict = field(default_factory=dict)


_SENSITIVE_KEYS = re.compile(
    r"(password|passwd|pwd|otp|cvv|cvc|card|pan|secret|token|3ds|three_ds|"
    r"captcha_answer|declaration_signature)", re.I)


def _reject_secrets(kwargs: dict) -> None:
    for k in kwargs:
        if _SENSITIVE_KEYS.search(k):
            raise SecretCaptureRefused(
                f"live driver refuses to receive sensitive field '{k}' — it must "
                "be entered personally by the applicant in Live View")


def _host_ok(url: str, approved: set[str]) -> bool:
    host = (urlparse(url).netloc or "").lower()
    return any(host == a or host.endswith("." + a) for a in approved)


def _field_text(node, sub: str) -> str:
    """Text of a declared sub-selector within a calendar node. An UNDECLARED
    field (sub == "") reads nothing — "" — never the whole node's blob: a
    field the adapter did not map must not leak the entire slot's text into
    post/label/when. Any read failure is "" too."""
    if not sub:
        return ""
    try:
        child = node.query_selector(sub)
        return (child.inner_text().strip() if child else "")
    except Exception:  # noqa: BLE001
        return ""


def _whole_text(node) -> str:
    """The whole node's text — used ONLY for the intentional both-dates-absent
    branch, where the entire slot cell IS the datetime."""
    try:
        return (node.inner_text() or "").strip()
    except Exception:  # noqa: BLE001
        return ""


class BrowserbaseLiveViewDriver:
    """Route-specific live driver. Bind ONLY to a production-approved adapter.

    Injection points (for contract/recovery testing WITHOUT a live portal):
      - session:     a session dict {id, mode, connect_url}; if omitted a real
                     Browserbase session is created (requires a real key).
      - page:        an object exposing goto()/fill()/click()/inner_text()/url;
                     in production this is a Playwright page over the session's
                     CDP connect_url. Tests inject a fake page.
      - state_probe: callable(session_token, application_id)->dict returning the
                     OFFICIAL application state (payment/appointment/submission).
                     In production this reads the portal's status page; injected
                     in tests so reconciliation can be exercised deterministically.
    """

    execution_class = LIVE_PRODUCTION

    def __init__(self, adapter, *, session: dict | None = None, page=None,
                 state_probe=None, require_real_key: bool = True):
        s = settings()
        # Fail-closed gate 1: runtime mode must be real-only.
        if not s.real_only_mode:
            raise RealOnlyStop(
                STOP_PORTAL_UNAVAILABLE,
                f"BrowserbaseLiveViewDriver may only run in a real-only runtime "
                f"mode (got '{s.runtime_mode}')")
        # Fail-closed gate 2: adapter must be individually production-approved.
        if getattr(adapter, "production_approval_status", "") != "production_approved" \
                or not getattr(adapter, "production_enabled", False):
            raise RealOnlyStop(
                STOP_PORTAL_UNAVAILABLE,
                f"adapter '{getattr(adapter, 'adapter_id', '?')}' is not "
                "production-approved+enabled; live driver refuses to bind")
        # Fail-closed gate 3: a real Browserbase key must be configured (unless a
        # session is injected for a contract test).
        if require_real_key and session is None and not bb.is_configured():
            raise RealOnlyStop(
                STOP_PORTAL_UNAVAILABLE,
                "no Browserbase key configured; cannot open a real isolated "
                "session for the live portal")
        self.adapter = adapter
        self.approved = {d.lower() for d in getattr(adapter, "approved_domains", [])}
        self.session = session or bb.create_session()
        self.page = page                      # set by _ensure_page() in prod
        self._state_probe = state_probe
        self._live_view = None

    # --- Live View surfaced into the small Ellis window ---------------------
    def live_view_url(self) -> str | None:
        if self._live_view is None:
            self._live_view = bb.live_view_url(self.session.get("id", ""))
        return self._live_view

    def _ensure_page(self):
        if self.page is not None:
            return self.page
        # Production: connect Playwright to the Browserbase session over CDP.
        connect = self.session.get("connect_url")
        if not connect:
            raise RealOnlyStop(STOP_PORTAL_UNAVAILABLE,
                               "no Browserbase connect URL for the session")
        from playwright.sync_api import sync_playwright  # pragma: no cover
        self._pw = sync_playwright().start()             # pragma: no cover
        # Bounded connect: a stalled Browserbase session must never hang the
        # caller indefinitely (same 60s cap as live_browser).
        browser = self._pw.chromium.connect_over_cdp(connect, timeout=60_000)  # pragma: no cover
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()  # pragma: no cover
        self.page = ctx.pages[0] if ctx.pages else ctx.new_page()  # pragma: no cover
        return self.page

    def _goto(self, url: str):
        if not _host_ok(url, self.approved):
            raise RealOnlyStop(STOP_PORTAL_UNAVAILABLE,
                               f"refusing to navigate off-allowlist host: {url}")
        return self._ensure_page().goto(url)

    def _fill_mappings(self, mappings, answers: dict):
        """Fill deterministic selectors from adapter config. Sensitive-marked
        fields are SKIPPED — they are entered by the applicant in Live View.

        A mapping may declare `format` (tokens DD/MM/YYYY/MON/MONTH/YY) for
        date fields: the canonical ISO value is then rendered in the EXACT
        format that specific portal requires — never the UI display format. A
        declared format with a non-ISO value fails closed (field skipped for
        the applicant to complete in Live View) rather than writing a guess."""
        from .. import dates as dates_mod
        page = self._ensure_page()
        for m in mappings or []:
            if m.get("sensitive"):
                continue  # never typed by the driver
            field_name = m["field"]
            if field_name not in answers or answers[field_name] is None:
                continue
            value = str(answers[field_name])
            fmt = m.get("format")
            if fmt:
                formatted = dates_mod.to_portal(value, fmt)
                if not formatted:
                    continue           # non-canonical date: never write a guess
                value = formatted
            page.fill(m["selector"], value)

    def _extract(self, selector: str | None) -> str | None:
        if not selector:
            return None
        try:
            return self._ensure_page().inner_text(selector).strip() or None
        except Exception:  # noqa: BLE001 - a missing node means "no evidence"
            return None

    # --- Reconciliation (brief §25): read OFFICIAL state before any retry ----
    def get_application_state(self, *, session_token=None, application_id=None, **kw) -> dict:
        if self._state_probe is not None:
            return self._state_probe(session_token, application_id)
        # Production: read the portal's own status page. Absent probe -> unknown,
        # NEVER an assumed success.
        return {"known": False, "paid": None, "booked": None, "submitted": None,
                "reference": None}

    # --- Non-sensitive navigation steps -------------------------------------
    def register(self, **kw):
        _reject_secrets(kw)
        self._goto(self.adapter.registration_url)
        self._fill_mappings(getattr(self.adapter, "registration_mappings", []), kw)
        # Password + email verification are personal; the applicant sets them in
        # Live View. Return a handoff marker, not a fabricated account.
        return {"ok": False, "status": STOP_APPLICANT_ACTION_REQUIRED,
                "handoff": "registration_and_password", "live_view": self.live_view_url()}

    def login(self, **kw):
        _reject_secrets(kw)
        self._goto(self.adapter.login_url)
        return {"ok": False, "status": STOP_APPLICANT_ACTION_REQUIRED,
                "handoff": "login_password", "live_view": self.live_view_url()}

    def create_application(self, *, session_token=None, answers=None, **kw):
        _reject_secrets(kw)
        self._goto(self.adapter.application_url)
        self._fill_mappings(getattr(self.adapter, "application_mappings", []), answers or {})
        # The official application id is read from the portal, never invented.
        app_id = self._extract(getattr(self.adapter, "application_id_selector", None))
        return {"ok": bool(app_id), "application_id": app_id,
                "status": "CREATED" if app_id else STOP_OUTCOME_UNCERTAIN}

    def upload_document(self, **kw):
        _reject_secrets(kw)
        return {"ok": False, "status": STOP_APPLICANT_ACTION_REQUIRED,
                "handoff": "document_upload", "live_view": self.live_view_url()}

    def discover_fee(self, **kw):
        _reject_secrets(kw)
        fee_text = self._extract(getattr(self.adapter, "fee_selector", None))
        return {"ok": bool(fee_text), "fee_text": fee_text,
                "status": "READ" if fee_text else STOP_OUTCOME_UNCERTAIN}

    # --- Personal-only steps: refuse, hand to Live View ---------------------
    def submit_captcha(self, **kw):
        raise SecretCaptureRefused("CAPTCHA must be solved personally in Live View")

    def verify_email(self, **kw):
        return {"ok": False, "status": STOP_APPLICANT_ACTION_REQUIRED,
                "handoff": "email_verification", "live_view": self.live_view_url()}

    def declare_personally(self, **kw):
        # Ellis never signs a government declaration. The applicant does it in
        # Live View; the driver only verifies the portal recorded it.
        state = self.get_application_state(**kw)
        return {"ok": bool(state.get("declared")), "status":
                "DECLARED" if state.get("declared") else STOP_APPLICANT_ACTION_REQUIRED}

    # --- Irreversible actions: reconcile first, evidence-only success -------
    def _official_evidence(self, kind: str, state: dict) -> _Evidence:
        if kind == "pay" and state.get("paid"):
            return _Evidence(True, "PAID", receipt=state.get("receipt"),
                             reference=state.get("reference"), raw=state)
        if kind == "book" and state.get("booked"):
            return _Evidence(True, "BOOKED", reference=state.get("confirmation"), raw=state)
        if kind == "submit" and state.get("submitted"):
            return _Evidence(True, "SUBMITTED", reference=state.get("reference"),
                             receipt=state.get("receipt"), raw=state)
        return _Evidence(False, STOP_OUTCOME_UNCERTAIN, raw=state)

    def pay(self, *, session_token=None, application_id=None, payment_ref=None, **kw):
        _reject_secrets(kw)
        # Reconcile: if already paid officially, return that — never pay twice.
        prior = self.get_application_state(session_token=session_token,
                                           application_id=application_id)
        if prior.get("paid"):
            ev = self._official_evidence("pay", prior)
            return {"ok": True, "status": "PAID", "receipt": ev.receipt,
                    "reference": ev.reference, "reconciled": True}
        # The applicant enters card/3-DS personally in Live View; the driver
        # only reads the portal's OFFICIAL post-payment state afterwards.
        after = self.get_application_state(session_token=session_token,
                                           application_id=application_id)
        ev = self._official_evidence("pay", after)
        return {"ok": ev.ok, "status": ev.status, "receipt": ev.receipt,
                "reference": ev.reference,
                "live_view": self.live_view_url() if not ev.ok else None}

    def search_appointments(self, *, post=None, **kw):
        """Read the OPEN slots from the official calendar, deterministically,
        through the adapter's declared selectors. Read-only: it navigates only
        the allowlisted appointment_url and never books. An e-visa route (or an
        adapter that declares no calendar selectors) returns none by policy —
        never a guessed date."""
        _reject_secrets(kw)
        if getattr(self.adapter, "appointment_search", "none") == "none":
            return {"ok": True, "slots": []}
        url = getattr(self.adapter, "appointment_url", "")
        if url:
            self._goto(url)                       # host-checked
        return {"ok": True,
                "slots": self._read_calendar_slots(default_post=post or "")}

    def _read_calendar_slots(self, *, default_post: str = "") -> list[dict]:
        """Extract each open slot from the rendered calendar via the adapter's
        `appointment_*` selectors. Nothing declared -> nothing read. Any node
        that yields no `when` is skipped rather than invented."""
        slot_sel = getattr(self.adapter, "appointment_slot_selector", "")
        if not slot_sel:
            return []
        page = self._ensure_page()
        query_all = getattr(page, "query_selector_all", None)
        if not callable(query_all):
            return []
        try:
            nodes = query_all(slot_sel) or []
        except Exception:  # noqa: BLE001 - a calendar that won't read yields none
            return []
        date_f = getattr(self.adapter, "appointment_date_field", "")
        time_f = getattr(self.adapter, "appointment_time_field", "")
        label_f = getattr(self.adapter, "appointment_label_field", "")
        post_f = getattr(self.adapter, "appointment_post_field", "")
        out = []
        for node in nodes:
            if date_f or time_f:
                # Only the DECLARED half is read; an undeclared date/time
                # contributes "", never the whole cell.
                when = f"{_field_text(node, date_f)} {_field_text(node, time_f)}".strip()
            else:
                when = _whole_text(node)          # whole slot text is the datetime
            if not when:
                continue
            # An undeclared post field -> "" -> the applicant's preferred post
            # labels the slot (default_post), rather than the whole cell blob.
            out.append({"post": _field_text(node, post_f) or default_post,
                        "when": when, "label": _field_text(node, label_f)})
        return out

    def book_appointment(self, *, session_token=None, application_id=None, **kw):
        _reject_secrets(kw)
        prior = self.get_application_state(session_token=session_token,
                                           application_id=application_id)
        if prior.get("booked"):
            return {"ok": True, "status": "BOOKED",
                    "confirmation": prior.get("confirmation"), "reconciled": True}
        after = self.get_application_state(session_token=session_token,
                                           application_id=application_id)
        ev = self._official_evidence("book", after)
        return {"ok": ev.ok, "status": ev.status, "confirmation": ev.reference}

    def reschedule_appointment(self, **kw):
        _reject_secrets(kw)
        return {"ok": False, "status": STOP_OUTCOME_UNCERTAIN}

    def submit(self, *, session_token=None, application_id=None, **kw):
        _reject_secrets(kw)
        prior = self.get_application_state(session_token=session_token,
                                           application_id=application_id)
        if prior.get("submitted"):
            return {"ok": True, "status": "SUBMITTED",
                    "reference": prior.get("reference"), "reconciled": True}
        # Final submit is pressed personally when the portal requires it; the
        # driver reads the OFFICIAL confirmation/receipt afterwards.
        ref = self._extract(_leaf(getattr(self.adapter, "confirmation_extraction", None)))
        receipt = self._extract(_leaf(getattr(self.adapter, "receipt_extraction", None)))
        after = self.get_application_state(session_token=session_token,
                                           application_id=application_id)
        if not after.get("submitted") and not ref:
            return {"ok": False, "status": STOP_OUTCOME_UNCERTAIN,
                    "live_view": self.live_view_url()}
        return {"ok": True, "status": "SUBMITTED",
                "reference": ref or after.get("reference"),
                "receipt": receipt or after.get("receipt")}

    def close(self):
        try:
            bb.close_session(self.session.get("id", ""))
        except Exception:  # noqa: BLE001
            pass


def _leaf(dotted: str | None) -> str | None:
    """confirmation_extraction is stored as 'confirmation.referenceNo'; the live
    selector is configured separately. Return the config value verbatim so a
    real adapter can point it at a CSS selector."""
    return dotted
