"""Case execution over a RELEASED typed-flow adapter (the live bridge).

This is the missing link between the two halves of Ellis:

  adapter factory (build → deterministic 16-gate release → AdapterRuntimeBinding)
  case workflow  (VisaWorkflow state machine + applicant handoffs + payment/
                  final-review/submission gates)

In real-only runtime modes the case workflow historically had NO live driver at
all, so every start failed closed with RealOnlyStop. This module resolves the
case's route to its released portal-family adapter and adapts the deterministic
FlowRunner to the 15-method case driver interface, on a real Browserbase
session. Everything stays fail-closed:

- no pair policy / family link / active sandbox binding  -> None (caller keeps
  the existing RealOnlyStop behavior; nothing is ever simulated);
- irreversible steps still run ONLY through the FlowRunner's guards
  (capability releases, standing authorization, signed final review,
  exact-amount payment, reconcile-before-act, evidence-only success);
- CAPTCHA / payment / declaration / final submission remain applicant
  handoffs — this driver refuses secrets exactly like the classic live driver.

No model is consulted anywhere on this path.
"""
from __future__ import annotations

import os
import tempfile

from sqlalchemy import select

from .. import models
from ..config import settings
from .contract import PortalAdapter

# Actions that end the create_application ("fill the form") segment.
_FILL_BOUNDARY_ACTIONS = {"UPLOAD_AUTHORIZED_DOCUMENT", "READ_FEE",
                          "READ_APPOINTMENT_INVENTORY"}
_FILL_BOUNDARY_HANDOFFS = {"payment_credentials", "legally_personal_declaration"}


class ReleasedRoute:
    """Everything needed to execute a case against its released family adapter."""

    def __init__(self, *, family, link, binding, version_row, route_key: str):
        self.family = family
        self.link = link
        self.binding = binding
        self.version_row = version_row
        self.route_key = route_key


def _iso3(db, name_or_code: str) -> str:
    """Country display name -> ISO3 via the snapshot registry (deterministic)."""
    from ..visa_snapshot.registry import normalize_country
    try:
        return normalize_country(name_or_code, field="destination_country")
    except Exception:  # noqa: BLE001 — unknown destination = honest no-route
        return ""


def resolve_released_route(db, app_row) -> ReleasedRoute | None:
    """Case -> pair policy -> portal family -> released link -> active binding.

    Returns None (fail closed) unless every link in the chain exists and the
    binding's bound version is live. Never raises for 'not available' — the
    caller's RealOnlyStop already words that honestly for the applicant."""
    from ..adapter_factory import models as fm
    from ..adapter_factory.release import active_binding, kill_engaged
    from ..global_routes.models import FamilyAdapterLink, PortalFamily, RoutePairPolicy
    from ..global_routes.models import pair_key as make_pair_key

    answers = app_row.answers or {}
    nat = (answers.get("passport_nationality") or answers.get("nationality") or "").strip()
    doc = (answers.get("travel_document_type") or "ordinary_passport").strip()
    dest = _iso3(db, app_row.destination_country)
    if not nat or not dest:
        return None
    try:
        pk = make_pair_key(nat, doc, dest)
    except Exception:  # noqa: BLE001
        return None
    pair = db.execute(select(RoutePairPolicy).where(
        RoutePairPolicy.pair_key == pk)).scalar_one_or_none()
    if pair is None or not pair.portal_family_id:
        return None
    link = db.execute(select(FamilyAdapterLink).where(
        FamilyAdapterLink.family_id == pair.portal_family_id)).scalar_one_or_none()
    if link is None or not link.released or not link.representative_route_key:
        return None
    family = db.execute(select(PortalFamily).where(
        PortalFamily.family_id == pair.portal_family_id)).scalar_one_or_none()
    if family is None or family.verification_status not in (
            "verified_official_domain", "verified_live"):
        return None
    binding = active_binding(db, route_key=link.representative_route_key,
                             tier=link.release_tier or "sandbox")
    if binding is None:
        return None
    version_row = db.execute(select(fm.AdapterCandidateVersion).where(
        fm.AdapterCandidateVersion.candidate_id == binding.candidate_id,
        fm.AdapterCandidateVersion.version == binding.candidate_version)).scalar_one_or_none()
    if version_row is None or version_row.quarantined:
        return None
    if kill_engaged(db, binding.candidate_id):
        return None
    return ReleasedRoute(family=family, link=link, binding=binding,
                         version_row=version_row,
                         route_key=link.representative_route_key)


class ReleasedFlowPortal:
    """Placeholder for the workflow's portal slot: the real state lives in the
    AdapterExecution row + Browserbase session, both DB-persisted."""

    def to_state(self) -> dict:
        return {"kind": "released_flow"}

    @classmethod
    def from_state(cls, _state):
        return cls()


class ReleasedFlowDriver:
    """The 15-method case driver, backed by the deterministic FlowRunner over a
    persistent Browserbase session.

    Segment execution: each workflow stage advances the released flow up to the
    next boundary (upload nodes, fee read, payment/declaration handoffs, the
    irreversible submit). Progress persists in AdapterExecution.current_node,
    so answering a question or solving a CAPTCHA resumes the SAME portal
    session at the SAME step — never a restart, never a duplicate application.
    """

    execution_class = "LIVE_PRODUCTION"

    def __init__(self, db, *, app_row, released: ReleasedRoute):
        self.db = db
        self.app_row = app_row
        self.released = released
        self._session = None          # LiveBrowserSession
        self._page_driver = None      # BrowserbasePageDriver
        self._compiled = None
        self._tmp_files: list[str] = []
        self._progress_sink = None    # applicant-safe (step_key, status) recorder

    def set_progress_sink(self, sink) -> None:
        self._progress_sink = sink

    # -- flow plumbing ---------------------------------------------------------

    def _flow(self):
        if self._compiled is None:
            from ..adapter_factory.compiler import compile_flow
            self._compiled = compile_flow(self.released.version_row)
        return self._compiled

    def _hosts(self) -> list[str]:
        return list((self.released.version_row.manifest or {}).get("allowed_hostnames") or [])

    def _execution(self):
        """The single AdapterExecution row for this case (resumable)."""
        from ..adapter_factory import models as fm
        row = self.db.execute(select(fm.AdapterExecution).where(
            fm.AdapterExecution.application_id == self.app_row.id,
            fm.AdapterExecution.candidate_id == self.released.binding.candidate_id,
            fm.AdapterExecution.status.notin_(("completed", "killed"))).order_by(
            fm.AdapterExecution.created_at.desc())).scalars().first()
        if row is None:
            row = fm.AdapterExecution(
                org_id=self.app_row.org_id, application_id=self.app_row.id,
                candidate_id=self.released.binding.candidate_id,
                candidate_version=self.released.binding.candidate_version,
                tier=self.released.binding.tier, status="running")
            self.db.add(row)
            self.db.commit()
        return row

    # -- browser session (persistent across HTTP requests) ---------------------

    def _session_row(self):
        return self.db.execute(select(models.BrowserSession).where(
            models.BrowserSession.application_id == self.app_row.id,
            models.BrowserSession.status == "open").order_by(
            models.BrowserSession.created_at.desc())).scalars().first()

    def _ensure_live(self):
        """Attach to the case's open Browserbase session, or open a new one.
        On a NEW session, reversible progress is honestly rewound to the flow
        start (the old page state is gone); anything at or past an irreversible
        node instead fails closed to reconciliation."""
        if self._page_driver is not None:
            return self._page_driver
        from ..adapter_factory.live_driver import BrowserbasePageDriver
        from .live_browser import LiveBrowserSession
        from ..providers import browser as bb

        execution = self._execution()
        row = self._session_row()
        session = None
        if row is not None and row.provider_session_id:
            try:
                info = bb.session_connect_info(row.provider_session_id)
                session = LiveBrowserSession(
                    allowed_hostnames=self._hosts(), session=info)
                session._ensure_page()
            except Exception:  # noqa: BLE001 — stale session: fall through
                if session is not None:
                    try:  # stop any half-started local Playwright before retrying
                        session._owns_session = False
                        session.close()
                    except Exception:  # noqa: BLE001
                        pass
                session = None
                row.status = "closed"
                self.db.commit()
        fresh = session is None
        if fresh:
            session = LiveBrowserSession(allowed_hostnames=self._hosts())
            page = session._ensure_page()   # opens the real Browserbase session
            sid = (getattr(session, "session", None) or {}).get("id", "")
            self.db.add(models.BrowserSession(
                org_id=self.app_row.org_id, application_id=self.app_row.id,
                provider_session_id=sid, mode="browserbase", status="open"))
            self.db.commit()
            if execution.current_node:
                if self._passed_irreversible(execution.current_node):
                    raise _OutcomeUncertain(
                        "the portal session was lost after an irreversible step; "
                        "Ellis will not repeat it without reconciliation")
                execution.current_node = ""   # honest rewind of reversible work
                self.db.commit()
        else:
            page = session._ensure_page()
        self._session = session
        self._page_driver = BrowserbasePageDriver(page, allowed_hostnames=self._hosts())
        return self._page_driver

    def _passed_irreversible(self, current_node: str) -> bool:
        flow = self._flow()
        node_id = flow.first()
        while node_id and node_id != current_node:
            node = flow.nodes.get(node_id) or {}
            if node.get("irreversibility") == "irreversible":
                return True
            node_id = flow.next_of(node_id, "ok")
        return False

    # -- documents -------------------------------------------------------------

    def _documents(self) -> list[dict]:
        """Approved case documents as temp files for real uploads. Temp copies
        live outside the repo with 0600 permissions and are deleted on close."""
        out = []
        docs = self.db.execute(select(models.StoredDocument).where(
            models.StoredDocument.application_id == self.app_row.id)).scalars().all()
        for d in docs:
            blob = self.db.execute(select(models.DocumentBlob).where(
                models.DocumentBlob.document_id == d.id)).scalar_one_or_none()
            entry = {"doc_type": d.doc_type, "name": d.name, "mime": d.mime, "path": ""}
            if blob is not None and getattr(d, "approved", False):
                suffix = {"image/jpeg": ".jpg", "image/png": ".png",
                          "application/pdf": ".pdf"}.get(d.mime, "")
                fd, path = tempfile.mkstemp(prefix="ellis-doc-", suffix=suffix)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(blob.content)
                os.chmod(path, 0o600)
                self._tmp_files.append(path)
                entry["path"] = path
            out.append(entry)
        return out

    def _cleanup_tmp(self):
        for p in self._tmp_files:
            try:
                os.unlink(p)
            except OSError:
                pass
        self._tmp_files = []

    # -- segment runner --------------------------------------------------------

    def _runner(self):
        from ..adapter_factory.runtime import FlowRunner
        execution = self._execution()
        driver = self._ensure_live()
        return FlowRunner(self.db, execution=execution, compiled=self._flow(),
                          driver=driver, case_answers=dict(self.app_row.answers or {}),
                          documents=self._documents(),
                          on_progress=self._progress_sink)

    def _advance(self, stop_before=None) -> dict:
        try:
            runner = self._runner()
            res = runner.run(stop_before=stop_before)
        except _OutcomeUncertain as e:
            self.detach()   # broken attachment: never reuse it for a retry
            return {"status": "outcome_uncertain", "reason": str(e)}
        except Exception:
            self.detach()
            raise
        finally:
            self._cleanup_tmp()
        if res.get("status") == "failed":
            # A failed step often means the page/session went bad; drop the
            # local attachment so an in-request retry starts clean instead of
            # stacking a second Playwright onto this thread.
            self.detach()
        return res

    def _consume_handoff(self, kind: str) -> bool:
        """After the applicant completed a handoff (in the live view), step the
        flow past the APPLICANT_HANDOFF node. Only the exact declared kind is
        consumable; nothing else ever skips a node."""
        from ..adapter_factory import models as fm  # noqa: F401
        execution = self._execution()
        flow = self._flow()
        node = flow.nodes.get(execution.current_node or "")
        if not node or node.get("action") not in ("APPLICANT_HANDOFF", "PAUSE"):
            return False
        if node.get("handoff_kind", "") != kind:
            return False
        execution.current_node = flow.next_of(execution.current_node, "ok") or ""
        execution.status = "running"
        self.db.commit()
        return True

    @staticmethod
    def _fill_boundary(node: dict) -> bool:
        if node.get("action") in _FILL_BOUNDARY_ACTIONS:
            return True
        return node.get("action") == "APPLICANT_HANDOFF" and \
            node.get("handoff_kind") in _FILL_BOUNDARY_HANDOFFS

    def _result_from(self, res: dict, *, ok_statuses=("boundary", "completed")) -> dict:
        st = res.get("status")
        if st in ok_statuses:
            return {"ok": True, "flow": res}
        if st == "paused_applicant_action":
            kind = res.get("handoff_kind", "")
            if kind == "additional_information":
                return {"ok": False, "code": "ADDITIONAL_INFORMATION_REQUIRED",
                        "questions": res.get("questions") or []}
            if kind == "captcha":
                return {"ok": False, "code": "CAPTCHA_REQUIRED"}
            return {"ok": False, "code": "APPLICANT_ACTION_REQUIRED", "handoff": kind}
        if st == "outcome_uncertain":
            return {"ok": False, "code": "OUTCOME_UNCERTAIN",
                    "detail": res.get("reason", "")}
        return {"ok": False, "code": res.get("reason") or st or "flow error"}

    # -- 15-method case driver interface ---------------------------------------
    # Account-less portals (Vietnam eVisa): register/login are honest no-ops —
    # the portal itself has no account step; nothing is claimed or simulated.

    def register(self, **_kwargs) -> dict:
        if self._account_required():
            return {"ok": False, "status": "APPLICANT_ACTION_REQUIRED",
                    "handoff": "credentials"}
        return {"ok": True, "needsEmailVerification": False,
                "note": "portal requires no account"}

    def login(self, **_kwargs) -> dict:
        if self._account_required():
            return {"ok": False, "status": "APPLICANT_ACTION_REQUIRED",
                    "handoff": "login_challenge"}
        row = self._session_row()
        token = (row.provider_session_id if row else "") or "no-account-required"
        return {"ok": True, "sessionToken": token}

    def _account_required(self) -> bool:
        return bool(getattr(self.released.family, "account_required", False))

    def create_application(self, **_kwargs) -> dict:
        res = self._advance(stop_before=self._fill_boundary)
        out = self._result_from(res)
        if out["ok"]:
            out["applicationId"] = self._execution().id
        return out

    def submit_captcha(self, **_kwargs) -> dict:
        # The human solved the CAPTCHA in the live portal view. Ellis records
        # the marker and moves past the declared handoff node — it never sees,
        # transcribes, or automates the CAPTCHA itself.
        if not self._consume_handoff("captcha"):
            return {"ok": False, "code": "NO_CAPTCHA_PENDING"}
        res = self._advance(stop_before=self._fill_boundary)
        return self._result_from(res)

    def verify_email(self, **_kwargs) -> dict:
        if self._consume_handoff("otp") or self._consume_handoff("email_verification"):
            return self._result_from(self._advance(stop_before=self._fill_boundary))
        return {"ok": True, "note": "no verification step pending"}

    def upload_document(self, **_kwargs) -> dict:
        def boundary(node):
            a = node.get("action")
            if a in ("READ_FEE", "READ_APPOINTMENT_INVENTORY"):
                return True
            return a == "APPLICANT_HANDOFF" and \
                node.get("handoff_kind") in _FILL_BOUNDARY_HANDOFFS
        res = self._advance(stop_before=boundary)
        return self._result_from(res)

    def discover_fee(self, **_kwargs) -> dict:
        def boundary(node):
            return node.get("action") == "APPLICANT_HANDOFF" and \
                node.get("handoff_kind") in _FILL_BOUNDARY_HANDOFFS
        runner = None
        try:
            runner = self._runner()
            res = runner.run(stop_before=boundary)
        except _OutcomeUncertain as e:
            return {"ok": False, "code": "OUTCOME_UNCERTAIN", "detail": str(e)}
        finally:
            self._cleanup_tmp()
        out = self._result_from(res)
        if not out["ok"]:
            return out
        fee = runner.fee_seen if runner is not None else None
        if not isinstance(fee, dict):
            fee = self._fee_from_page_text()
        if not isinstance(fee, dict):
            return {"ok": False, "code": "FEE_NOT_DISPLAYED",
                    "detail": "the portal did not display a readable fee"}
        display = f"{fee['amount_cents'] / 100:.2f} {fee['currency']}"
        return {"ok": True, "amount": fee["amount_cents"], "currency": fee["currency"],
                "display": display,
                "government_fee_cents": fee["amount_cents"], "service_fee_cents": 0,
                "payee": self.released.family.operator or self.released.family.name}

    def pay(self, **_kwargs) -> dict:
        # The applicant already completed payment in the portal's own secure
        # window (payment handoff). Step past the handoff and require REAL
        # evidence before reporting a receipt — a banner is never success.
        self._consume_handoff("payment_credentials")
        self._consume_handoff("payment")

        def boundary(node):
            if node.get("action") == "APPLICANT_HANDOFF" and \
                    node.get("handoff_kind") == "legally_personal_declaration":
                return True
            return node.get("irreversibility") == "irreversible"
        res = self._advance(stop_before=boundary)
        out = self._result_from(res)
        if not out["ok"]:
            return out
        receipt_no = self._read_extract("receipt_extraction")
        if receipt_no is None:
            return {"ok": False, "code": "OUTCOME_UNCERTAIN",
                    "detail": "payment result not verifiable from the portal"}
        return {"ok": True, "receipt": {"receiptNo": receipt_no}}

    def declare_personally(self, *, human_confirmed: str = "", **_kwargs) -> dict:
        from ..providers import browser as bb
        if human_confirmed != bb.HUMAN_MARKERS.get("personal_declaration"):
            return {"ok": False, "code": "DECLARATION_NOT_CONFIRMED"}
        if not self._consume_handoff("legally_personal_declaration"):
            # Some flows place the declaration checkbox on the review page and
            # the handoff later; nothing to consume is not an error.
            pass
        return {"ok": True}

    def submit(self, **_kwargs) -> dict:
        """The ONLY irreversible segment. Every FlowRunner guard applies:
        capability release, standing authorization, signed final review and
        exact-amount payment were checked by assert_execution_allowed before
        the case workflow ever reaches READY_TO_SUBMIT; the node itself is
        reconcile-before-act with evidence-only success."""
        from ..adapter_factory.runtime import assert_execution_allowed, RuntimeRefused
        try:
            assert_execution_allowed(
                self.db, route_key=self.released.route_key,
                application_id=self.app_row.id, compiled=self._flow())
        except RuntimeRefused as e:
            return {"ok": False, "code": "SUBMISSION_BLOCKED", "detail": str(e)[:300]}
        res = self._advance(stop_before=None)   # run to COMPLETE
        out = self._result_from(res, ok_statuses=("completed",))
        if not out["ok"]:
            return out
        ref = self._read_extract("confirmation_extraction")
        if ref is None:
            return {"ok": False, "code": "OUTCOME_UNCERTAIN",
                    "detail": "no official confirmation reference readable"}
        return {"ok": True, "confirmation": {"referenceNo": ref}}

    def get_application_state(self, **_kwargs) -> dict:
        """Reconciliation from recorded REAL evidence only — never assumed."""
        from ..adapter_factory import models as fm
        execution = self._execution()
        rows = self.db.execute(select(fm.AdapterOutcomeEvidence).where(
            fm.AdapterOutcomeEvidence.execution_id == execution.id)).scalars().all()
        cats = {r.state_category for r in rows}
        submitted = any("submission" in c for c in cats)
        paid = any("payment" in c or "receipt" in c for c in cats)
        out = {"ok": True, "submitted": submitted, "paid": paid}
        if submitted:
            ref = self._read_extract("confirmation_extraction")
            out["confirmation"] = {"referenceNo": ref or ""}
        if paid:
            rec = self._read_extract("receipt_extraction")
            out["receipt"] = {"receiptNo": rec or ""}
        return out

    def _read_extract(self, kind: str):
        """Read a confirmation/receipt reference via the adapter's declared
        extraction selector, from the LIVE page. When the manifest declares no
        selector, fall back to a deterministic labeled-reference parse of the
        page's visible text — exactly one unambiguous match or nothing.
        None = not verifiable (the caller stays honest)."""
        sel = ((self.released.version_row.manifest or {}).get(kind) or "").strip()
        try:
            driver = self._ensure_live()
        except Exception:  # noqa: BLE001
            return None
        if sel:
            try:
                res = driver.read_text(sel)
            except Exception:  # noqa: BLE001
                return None
            if not res.get("ok"):
                return None
            text = (res.get("text") or "").strip()
            return text or None
        return self._labeled_reference_from_page(driver, kind)

    # Labels the official portals themselves print next to the reference —
    # static strings, never inferred. Vietnamese labels are the eVisa portal's.
    _REFERENCE_LABELS = {
        "confirmation_extraction": (
            "registration code", "application code", "dossier code",
            "reference number", "application number", "mã hồ sơ", "mã đăng ký"),
        "receipt_extraction": (
            "receipt", "transaction", "payment reference", "mã giao dịch",
            "số biên lai"),
    }

    def _labeled_reference_from_page(self, driver, kind: str):
        """Deterministic parse: a known label followed by a code-shaped token
        in the page's visible text. Requires exactly ONE distinct candidate;
        any ambiguity returns None (fail closed, never a guess)."""
        import re
        labels = self._REFERENCE_LABELS.get(kind) or ()
        if not labels:
            return None
        try:
            res = driver.read_text("body")
        except Exception:  # noqa: BLE001
            return None
        if not res.get("ok"):
            return None
        text = res.get("text") or ""
        found: set[str] = set()
        for label in labels:
            for m in re.finditer(
                    re.escape(label) + r"[^A-Za-z0-9\n]{0,8}([A-Z0-9][A-Z0-9-]{5,24})",
                    text, re.IGNORECASE):
                found.add(m.group(1).strip())
        return found.pop() if len(found) == 1 else None

    def _fee_from_page_text(self):
        """Deterministic fee parse from the live page's visible text when the
        flow declares no READ_FEE node: fee-labeled lines run through the same
        strict parse_fee_text used everywhere. Exactly one distinct amount or
        nothing — an ambiguous page never becomes a charge."""
        from ..adapter_factory.runtime import parse_fee_text
        try:
            driver = self._ensure_live()
            res = driver.read_text("body")
        except Exception:  # noqa: BLE001
            return None
        if not res.get("ok"):
            return None
        keywords = ("fee", "phí", "lệ phí", "payment amount", "amount to pay")
        seen: dict[tuple, dict] = {}
        for line in (res.get("text") or "").splitlines():
            low = line.lower()
            if not any(k in low for k in keywords):
                continue
            parsed = parse_fee_text(line)
            if parsed:
                seen[(parsed["amount_cents"], parsed["currency"])] = parsed
        if len(seen) != 1:
            return None
        return next(iter(seen.values()))

    # Appointments never apply to e-visa flows (adapter declares none).
    def search_appointments(self, **_kwargs) -> dict:
        return {"ok": True, "slots": []}

    def book_appointment(self, **_kwargs) -> dict:
        return {"ok": False, "code": "NOT_APPLICABLE"}

    def reschedule_appointment(self, **_kwargs) -> dict:
        return {"ok": False, "code": "NOT_APPLICABLE"}

    def close(self):
        self._cleanup_tmp()
        # The Browserbase session intentionally stays open between signals —
        # it is the applicant's in-progress portal session. It is closed by
        # the case browser-session endpoints or provider TTL.

    def detach(self):
        """End-of-request cleanup: stop the local Playwright attachment but
        KEEP the remote Browserbase session alive (the applicant's in-progress
        portal session, re-attached on the next signal)."""
        self._cleanup_tmp()
        session = self._session
        self._session = None
        self._page_driver = None
        if session is not None:
            try:
                session._owns_session = False   # never release the remote session
                session.close()                 # stops the local playwright only
            except Exception:  # noqa: BLE001
                pass


class _OutcomeUncertain(Exception):
    pass


def build_released_adapter(db, app_row, released: ReleasedRoute) -> PortalAdapter:
    """A contract.PortalAdapter over the released typed flow, honestly labeled
    production-approved (it passed the deterministic release gates) and driven
    by the live ReleasedFlowDriver."""
    manifest = released.version_row.manifest or {}
    hosts = list(manifest.get("allowed_hostnames") or [])
    base = f"https://{hosts[0]}/" if hosts else (released.family.base_url or "")
    flow = released.version_row.flow or []
    declaration_required = any(
        n.get("action") == "APPLICANT_HANDOFF" and
        n.get("handoff_kind") == "legally_personal_declaration" for n in flow)
    required_fields = [n.get("input_source") for n in flow
                       if n.get("action") in ("FILL_NON_SENSITIVE", "SELECT_SEARCH")
                       and n.get("input_source") and bool(n.get("mandatory", True))]
    required_docs = sorted({n.get("doc_type", "passport") for n in flow
                            if n.get("action") == "UPLOAD_AUTHORIZED_DOCUMENT"})
    driver = ReleasedFlowDriver(db, app_row=app_row, released=released)
    return PortalAdapter(
        adapter_id=released.binding.candidate_id,
        adapter_version=int(released.binding.candidate_version),
        destination_country=app_row.destination_country,
        visa_type=app_row.visa_type,
        portal_operator=manifest.get("portal_operator", "") or released.family.operator,
        approved_domains=hosts,
        registration_url=base, login_url=base, application_url=base,
        appointment_url=base,
        required_applicant_fields=[],   # missing answers become questions, not blocks
        required_documents=required_docs,
        registration_mappings=[], application_mappings=[],
        captcha_detect="declared",
        password_requirements={"minLength": 12},
        payment_policy="applicant",
        third_party_payment_policy="applicant",
        appointment_search="none", appointment_booking="prohibited",
        reschedule_policy="prohibited",
        representative_submission="automated",
        personal_declaration_required=declaration_required,
        fee_discovery="portal",
        confirmation_extraction=manifest.get("confirmation_extraction", "declared"),
        receipt_extraction=manifest.get("receipt_extraction", "declared"),
        resume_behavior="resume_same_session",
        rate_limits={}, portal_policy_review_date=str(
            getattr(released.link, "updated_at", "") or "")[:10] or "2026-07-25",
        production_approval_status="production_approved",
        production_enabled=True,
        driver=driver,
        allowed_actions=["navigate", "read", "fill_non_sensitive", "upload_authorized"],
        channel="released_flow",
        account_required=bool(getattr(released.family, "account_required", False)))


def build_for_case(db, app_row):
    """(portal, adapter) for a case with a released live route, else None."""
    # `required_fields` computed for metadata honesty, not used to hard-block:
    released = resolve_released_route(db, app_row)
    if released is None:
        return None
    return ReleasedFlowPortal(), build_released_adapter(db, app_row, released)
