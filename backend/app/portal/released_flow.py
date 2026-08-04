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
import re
import tempfile
from datetime import datetime, timezone

from sqlalchemy import select

from .. import models
from ..config import settings
from .contract import PortalAdapter

# Actions that end the create_application ("fill the form") segment.
_FILL_BOUNDARY_ACTIONS = {"UPLOAD_AUTHORIZED_DOCUMENT", "READ_FEE",
                          "READ_APPOINTMENT_INVENTORY"}
_FILL_BOUNDARY_HANDOFFS = {"payment_credentials", "legally_personal_declaration"}

# The applicant-facing handoff vocabulary. A pause names one of these, and the
# UI has a panel, copy and a resolving signal for each — see HANDOFF_UI /
# HANDOFF_COPY / HANDOFF_SIGNAL in visaBackend.js, which must cover this list.
APPLICANT_HANDOFFS = frozenset({
    "additional_information", "appointment_selection", "authorization",
    "captcha", "credentials", "document_upload", "email_verification",
    "fee_confirmation", "final_review", "identity", "login_challenge",
    "no_availability", "otp", "payment", "payment_approval",
    "payment_credentials", "personal_declaration", "portal_form",
    "portal_verification",
    "portal_terms_consent", "reschedule_approval", "review", "three_ds",
})

# A SPEC's internal node vocabulary is not the applicant's. These names are
# written by specgen to describe what a node IS; the applicant sees what they
# must DO. Anything not aliased passes through unchanged and must therefore
# already be an APPLICANT_HANDOFF — a released adapter carrying a kind that is
# neither is a dead end: the UI falls back to a bare "Action needed" card with
# no panel and no signal, and the case never leaves it. thailand-tdac's
# document_upload did exactly that (2026-08-03).
_HANDOFF_ALIASES = {
    "legally_personal_declaration": "personal_declaration",
}


def applicant_handoff(kind: str) -> str:
    """Map a flow node's handoff_kind onto the applicant-facing vocabulary."""
    return _HANDOFF_ALIASES.get(str(kind or ""), str(kind or ""))


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


class _AnswersView:
    """The case row with a different answer set — so a pre-flight read can use
    the answers the workflow is holding in memory without writing them to the
    application first."""

    def __init__(self, row, answers: dict):
        self._row = row
        self.answers = answers

    def __getattr__(self, name):
        return getattr(self._row, name)


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
        self._docs_cache = None       # materialized documents (per attachment)
        self._progress_sink = None    # applicant-safe (step_key, status) recorder
        # Silent page-refill bookkeeping for portal_form pauses over fields
        # whose answers Ellis already holds (see _portal_form_to_questions):
        # allowed only inside the discover loop, once per page signature.
        self._allow_refill_retry = False
        self._portal_form_refilled_sigs: set[str] = set()

    def preflight_questions(self, answers: dict | None = None) -> list[dict]:
        """Everything this portal will demand that the case cannot answer yet —
        computed from the released flow's own nodes, with NO browser session.

        Ellis knows the government form's mandatory fields before it opens one:
        they are baked into the released adapter. Asking them at the start costs
        the applicant one prompt; discovering them mid-fill costs a portal
        round-trip per field, and on a form Ellis cannot complete it costs the
        run (Thailand, 2026-08-03: a mandatory Occupation nobody was asked for).

        The live page stays the authority — this is the questions Ellis already
        KNOWS about, never the whole set, so the mid-run pause path remains and
        is still what handles anything the stored flow could not predict.
        """
        row = self.app_row
        if answers is not None:
            # Read against the answers the workflow holds now, which include
            # everything provided since this case row was last written.
            merged = dict(row.answers or {})
            merged.update({k: v for k, v in (answers or {}).items()})
            row = _AnswersView(row, merged)
        return [q for q in known_missing_questions(self.db, row)
                if q.get("mandatory")]

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
        from ..portal_store import current_browser_session
        return current_browser_session(self.db, self.app_row.id)

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
        if row is not None and row.provider_session_id and not bb.session_alive(
                row.provider_session_id):
            # Ended at the provider (lifetime elapsed): don't waste a CDP
            # connect on a corpse — close it and open a fresh one below.
            row.status = "closed"
            self.db.commit()
            row = None
        if row is not None and row.provider_session_id:
            try:
                info = bb.session_connect_info(row.provider_session_id)
                session = LiveBrowserSession(
                    allowed_hostnames=self._hosts(), session=info)
                page = session._ensure_page()
                # Liveness ping: a reattach can return a page object whose
                # target already died — every later action would fail with
                # NO_SUCH_ELEMENT. A dead page routes to the fresh-session
                # path (with its honest reversible rewind) instead.
                page.evaluate("1")
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
            new_row = models.BrowserSession(
                org_id=self.app_row.org_id, application_id=self.app_row.id,
                provider_session_id=sid, mode="browserbase", status="open")
            self.db.add(new_row)
            self.db.commit()
            # Exactly one open session per case: the applicant's secure window
            # must show the session Ellis is actually driving, never a stale
            # blank one left open beside it.
            from ..portal_store import retire_other_sessions
            retire_other_sessions(self.db, self.app_row.id, new_row.id)
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
        live outside the repo with 0600 permissions and are deleted on close.

        The applicant's explicit checklist submission OVERRIDES the automatic
        classifier: a file they submitted against the 'photo' requirement IS
        the photo for portal-upload purposes, even when OCR classified it as a
        generic document — and that explicit submission also stands in for the
        OCR approval gate (a photo has no extractable fields to approve)."""
        if getattr(self, "_docs_cache", None) is not None:
            # Re-materializing every blob (megabytes each) on every segment
            # was a large share of the "checking the form" wall-clock.
            return self._docs_cache
        out = []
        submitted_types = self._checklist_submitted_types()
        # Newest first: a re-uploaded photo must win over the one the portal
        # rejected (the runner picks the first matching document).
        docs = self.db.execute(select(models.StoredDocument).where(
            models.StoredDocument.application_id == self.app_row.id).order_by(
            models.StoredDocument.created_at.desc())).scalars().all()
        for d in docs:
            blob = self.db.execute(select(models.DocumentBlob).where(
                models.DocumentBlob.document_id == d.id)).scalar_one_or_none()
            entry = {"doc_type": submitted_types.get(d.id, d.doc_type),
                     "name": d.name, "mime": d.mime, "path": ""}
            if blob is not None and (getattr(d, "approved", False)
                                     or d.id in submitted_types):
                suffix = {"image/jpeg": ".jpg", "image/png": ".png",
                          "application/pdf": ".pdf"}.get(d.mime, "")
                fd, path = tempfile.mkstemp(prefix="ellis-doc-", suffix=suffix)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(blob.content)
                os.chmod(path, 0o600)
                self._tmp_files.append(path)
                entry["path"] = path
            out.append(entry)
        self._docs_cache = out
        return out

    def _checklist_submitted_types(self) -> dict:
        """document_id -> effective doc type, from the applicant's explicit
        checklist submissions (status 'submitted'). Generic items keep the
        classifier's verdict; typed requirements (photo, passport, …) impose
        theirs."""
        from .. import checklist_intake
        out: dict = {}
        try:
            cg = checklist_intake.case_guidance(self.db, self.app_row.id)
            if cg is None:
                return out
            items = {i.get("id"): i for i in
                     (checklist_intake.current_checklist(self.db, self.app_row, cg) or [])}
            for sub in checklist_intake.submission_rows(self.db, self.app_row.id):
                if sub.get("status") != "submitted" or not sub.get("document_id"):
                    continue
                item = items.get(sub.get("item_id")) or {}
                satisfied = [s for s in (item.get("satisfied_by") or []) if s]
                eff = satisfied[0] if satisfied else str(item.get("id") or "")
                if eff and eff != "document":
                    out[sub["document_id"]] = eff
        except Exception:  # noqa: BLE001 — never break uploads on lookup issues
            return {}
        return out

    def _cleanup_tmp(self):
        for p in self._tmp_files:
            try:
                os.unlink(p)
            except OSError:
                pass
        self._tmp_files = []
        self._docs_cache = None

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
                out = {"ok": False, "code": "ADDITIONAL_INFORMATION_REQUIRED",
                       "questions": res.get("questions") or []}
            elif kind == "captcha":
                out = {"ok": False, "code": "CAPTCHA_REQUIRED"}
            else:
                if kind == "portal_form":
                    # The portal rejected the advance over fields the flow has
                    # no nodes for. Those are exactly what the page classifier
                    # can ask IN ELLIS (portal-flagged = mandatory, portal
                    # wording as the reason) — the secure window is the
                    # fallback, not the first resort.
                    upgraded = self._portal_form_to_questions(res)
                    if upgraded is not None:
                        return upgraded
                out = {"ok": False, "code": "APPLICANT_ACTION_REQUIRED",
                       "handoff": applicant_handoff(kind)}
            if res.get("portal_messages"):
                out["portal_messages"] = res["portal_messages"]
            return out
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

    def _result_retrying_refills(self, res, advance,
                                 *, ok_statuses=("boundary", "completed")) -> dict:
        """_result_from with refill-and-retry: when the portal_form upgrade
        filled held answers into the page (PORTAL_STEP_REFILLED — eVisa's
        passport "Type" from intake), re-run the SAME segment immediately.
        The cursor still sits at the refused node, so the walk resumes with
        the form now complete. Bounded by the per-page-signature refill
        guard plus a hard cap. Enabled ONLY for segments whose re-run is a
        plain resume (create/upload/submit and the discover loop) — never
        the captcha/pay/restore arms, whose workflow branches cannot retry
        a stray code and would dead-end in manual review."""
        self._allow_refill_retry = True
        try:
            out = self._result_from(res, ok_statuses=ok_statuses)
            for _ in range(3):
                if out.get("code") != "PORTAL_STEP_REFILLED":
                    break
                out = self._result_from(advance(), ok_statuses=ok_statuses)
            return out
        finally:
            self._allow_refill_retry = False

    def create_application(self, **_kwargs) -> dict:
        res = self._advance(stop_before=self._fill_boundary)
        out = self._result_retrying_refills(
            res, lambda: self._advance(stop_before=self._fill_boundary))
        if out["ok"]:
            out["applicationId"] = self._execution().id
        return out

    def submit_captcha(self, answer=None, **_kwargs) -> dict:
        # The human solved the CAPTCHA — either directly in the live portal
        # view, or by typing the solution in Ellis (answer), which Ellis
        # transcribes into the portal's own captcha input, then presses the
        # page's Next FOR the applicant (their typed code is the go-ahead).
        # Ellis never reads, interprets, or automates the challenge itself.
        if answer:
            try:
                driver = self._ensure_live()
                applier = getattr(driver, "apply_captcha_answer", None)
                if applier is not None and (applier(str(answer)) or {}).get("ok"):
                    clicker = getattr(driver, "click_next_button", None)
                    if clicker is not None:
                        clicker()
                    if hasattr(driver, "settle"):
                        driver.settle(1500)
                    # A blocking notice may mean the code was ACCEPTED (the
                    # registration/document-code notice) OR REJECTED (an
                    # "invalid security code" error dialog also carries a
                    # Confirm button). Presence alone is NOT acceptance: an
                    # error notice must never be scraped for a reference or
                    # treated as success.
                    notice = getattr(driver, "notice_state", None)
                    if notice is not None and (notice() or {}).get("present"):
                        if self._notice_is_error(driver):
                            return {"ok": False, "code": "CAPTCHA_WRONG"}
                        self._capture_registration_notice(driver)
                        confirm = getattr(driver, "confirm_notice", None)
                        if confirm is not None:
                            confirm()
                        if hasattr(driver, "settle"):
                            driver.settle(800)
                    else:
                        # No notice and the challenge still on screen: the
                        # portal rejected the code — ask for the fresh one.
                        probe = getattr(driver, "captcha_state", None)
                        state = probe() if probe is not None else {}
                        if state.get("ok") and state.get("present"):
                            return {"ok": False, "code": "CAPTCHA_WRONG"}
            except Exception:  # noqa: BLE001 — the advance verifies honestly
                pass
        if self._consume_handoff("captcha"):
            res = self._advance(stop_before=self._fill_boundary)
            return self._result_from(res)
        # No declared CAPTCHA node: this was an OBSERVED mid-flow challenge
        # (pause classification spotted it on the live page). Confirm it is
        # actually gone; the resumed state re-runs its own segment next.
        try:
            driver = self._ensure_live()
            state = driver.captcha_state() if hasattr(driver, "captcha_state") \
                else {"ok": False}
        except Exception:  # noqa: BLE001 — unreadable page: let the resume decide
            state = {"ok": False}
        if state.get("ok") and state.get("present"):
            return {"ok": False, "code": "CAPTCHA_STILL_PRESENT"}
        return {"ok": True, "note": "observed challenge cleared by the applicant"}

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
        return self._result_retrying_refills(
            res, lambda: self._advance(stop_before=boundary))

    def discover_fee(self, page_fill=None, declaration_fill=None,
                     refresh_uploads=False, **_kwargs) -> dict:
        def boundary(node):
            return node.get("action") == "APPLICANT_HANDOFF" and \
                node.get("handoff_kind") in _FILL_BOUNDARY_HANDOFFS
        # A document was re-provided after the portal rejected it (photo
        # banner): re-run the upload nodes so the NEW file reaches the form.
        if refresh_uploads:
            self._rewind_to_uploads()
        # Answers to PAGE-observed questions (fields the released flow has no
        # node for) are applied first, then the flow resumes FROM its own
        # advance click (guarded rewind) — Ellis fills and clicks Next; the
        # applicant never re-types on the form.
        if page_fill:
            try:
                self._apply_page_fill(page_fill)
            except _OutcomeUncertain as e:
                return {"ok": False, "code": "OUTCOME_UNCERTAIN", "detail": str(e)}
        # The applicant declared IN ELLIS (recorded, verbatim statement):
        # transcribe the tick onto the portal's own declaration checkbox and
        # resume from the flow's advance click.
        if declaration_fill:
            try:
                self._apply_declaration(declaration_fill)
            except _OutcomeUncertain as e:
                return {"ok": False, "code": "OUTCOME_UNCERTAIN", "detail": str(e)}
        repaired = False
        advance_attempted = False
        attempted_optional_keys: set = set()
        try:
            return self._discover_fee_loop(boundary, repaired, advance_attempted,
                                           attempted_optional_keys)
        finally:
            # One cleanup for the WHOLE discovery call: per-iteration cleanup
            # re-materialized every document blob on every pass.
            self._cleanup_tmp()

    def _tick_declaration_and_advance(self) -> bool:
        """Tick the page's own declaration checkbox and press Next — the
        single action a completed form is usually waiting on. Returns True
        only when the page ACTUALLY moved (the declaration box is gone or
        the advance button is no longer offered); a disabled Next or an
        unmoved page returns False so the caller classifies properly."""
        try:
            driver = self._ensure_live()
        except Exception:  # noqa: BLE001
            return False
        finder = getattr(driver, "find_declaration_checkbox", None)
        if finder is None:
            return False
        try:
            found = finder() or {}
        except Exception:  # noqa: BLE001
            return False
        boxes = [b for b in (found.get("boxes") or []) if b.get("selector")]
        if not found.get("found") or not boxes:
            return False
        # The form must be COMPLETE before the declaration is ticked and Next
        # pressed. Fields the flow has no nodes for can still be empty here —
        # eVisa's passport "Type" (intake's travel_document_type) is exactly
        # that — and pressing Next against them dead-ends the run. Fill every
        # empty field whose answer Ellis already holds; if genuinely
        # unanswered mandatory fields remain, this is not the fast path's
        # page — classification must ask them first.
        try:
            blockers = self._page_blockers()
        except Exception:  # noqa: BLE001
            blockers = {}
        refills = blockers.get("refill") or []
        if refills:
            try:
                if not self._fill_observed_values(refills):
                    return False
            except Exception:  # noqa: BLE001
                return False
        if any(q.get("mandatory") for q in (blockers.get("questions") or [])):
            return False
        if self._progress_sink:
            try:
                self._progress_sink("confirming_declaration", "active")
            except Exception:  # noqa: BLE001
                pass
        try:
            # EVERY unchecked declaration on the page — the form gates Next on
            # all of them, and an unverified tick is never counted as done.
            ticked = []
            for box in boxes:
                if (driver.check(box["selector"]) or {}).get("ok"):
                    ticked.append(box.get("text", ""))
            if not ticked:
                return False
            # FAIL CLOSED: only a readable, present, ENABLED advance button may
            # be pressed by this fast path. An unreadable state or a button the
            # driver doesn't recognize must NOT fall through to a blind click —
            # the flow's own declared CLICK node handles those pages under the
            # runtime's full guards.
            state = driver.next_button_state() if hasattr(driver, "next_button_state") else {}
            if not state.get("ok") or not state.get("present") or state.get("disabled"):
                return False
            sig_before = driver.page_signature() if hasattr(driver, "page_signature") else ""
            if not (driver.click_next_button() or {}).get("ok"):
                return False
            driver.settle(1500)
            # Proof of movement: the page's IDENTITY changed. The old check
            # ("the boxes we ticked are gone") was circular — the finder only
            # reports UNCHECKED boxes, so a refused advance read as moved.
            sig_after = driver.page_signature() if hasattr(driver, "page_signature") else ""
            if sig_before and sig_after:
                moved = sig_after != sig_before
            else:
                still = finder() or {}
                moved = not still.get("found")
        except Exception:  # noqa: BLE001
            return False
        if moved:
            for text in ticked:
                self._emit_declaration_audit(text)
        return bool(moved)

    def _emit_declaration_audit(self, text: str) -> None:
        try:
            from .. import audit
            audit.record(self.db, org_id=self.app_row.org_id,
                         application_id=self.app_row.id,
                         action="declaration_confirmed_on_portal",
                         detail={"statement": str(text)[:200]}, actor="ellis")
        except Exception:  # noqa: BLE001
            pass

    def _discover_fee_loop(self, boundary, repaired, advance_attempted,
                           attempted_optional_keys) -> dict:
        declaration_tried = False
        incomplete_classified = False
        # Refill-and-retry is safe HERE (this loop re-walks after a refill);
        # scope the permission to this call so other _result_from callers
        # keep their pause/upgrade behavior.
        self._allow_refill_retry = True
        try:
            return self._discover_fee_iterations(
                boundary, repaired, advance_attempted, attempted_optional_keys,
                declaration_tried, incomplete_classified)
        finally:
            self._allow_refill_retry = False

    def _discover_fee_iterations(self, boundary, repaired, advance_attempted,
                                 attempted_optional_keys, declaration_tried,
                                 incomplete_classified) -> dict:
        notices_confirmed = 0
        while True:
            runner = None
            try:
                runner = self._runner()
                res = runner.run(stop_before=boundary)
            except _OutcomeUncertain as e:
                return {"ok": False, "code": "OUTCOME_UNCERTAIN", "detail": str(e)}
            out = self._result_from(res)
            if not out["ok"]:
                if out.get("code") == "PORTAL_STEP_REFILLED":
                    # The portal_form upgrade just filled held answers into
                    # the page and rewound to the flow's advance click —
                    # re-walk immediately instead of pausing the applicant.
                    continue
                from ..adapter_factory.runtime import FORM_INCOMPLETE_REASON
                if FORM_INCOMPLETE_REASON in str(out.get("code") or "") \
                        and not incomplete_classified:
                    # The flow's own Next is disabled and the runtime could not
                    # complete the form from flow NODES alone. The page
                    # classifier below can: it also sees fields the flow does
                    # not model and refills the ones whose answers Ellis
                    # already holds (eVisa's passport "Type"). Only a page
                    # that stays incomplete AFTER that pass is a real failure.
                    incomplete_classified = True
                else:
                    return out
            fee = runner.fee_seen if runner is not None else None
            if not isinstance(fee, dict):
                fee = self._fee_from_page_text()
            if isinstance(fee, dict):
                break
            # FIRST, the cheap and overwhelmingly common case: the form is
            # complete and the page is only waiting for its own declaration
            # box to be ticked and Next pressed. Do exactly that — one page
            # read, one tick, one click — instead of a full classification
            # sweep that costs seconds and asks the applicant for nothing.
            if not declaration_tried:
                declaration_tried = True
                if self._tick_declaration_and_advance():
                    continue
            # A blocking notice sits ON TOP of the form and answers nothing:
            # everything behind it — the CAPTCHA included — still measures as
            # visible, so classifying the page underneath describes a page the
            # applicant cannot reach. Acknowledge the notice first (recording
            # what it says), then re-read.
            if notices_confirmed < self._MAX_NOTICE_CONFIRMS \
                    and self._confirm_blocking_notice():
                notices_confirmed += 1
                continue
            # Otherwise read what the live page ACTUALLY shows: a CAPTCHA,
            # fields whose answers Ellis already holds (repair silently,
            # once), or still-unanswered questions must pause as themselves —
            # a mislabeled pause sends the applicant to the wrong step.
            blockers = self._page_blockers()
            if blockers.get("bot_check"):
                # The portal is refusing the automated session, not a value.
                # Retrying cannot answer it; a human can.
                return {"ok": False, "code": "APPLICANT_ACTION_REQUIRED",
                        "handoff": "portal_verification"}
            if blockers.get("captcha"):
                return {"ok": False, "code": "CAPTCHA_REQUIRED"}
            if blockers.get("refill") and not repaired:
                repaired = True
                try:
                    self._apply_page_fill(blockers["refill"])
                except _OutcomeUncertain as e:
                    return {"ok": False, "code": "OUTCOME_UNCERTAIN", "detail": str(e)}
                continue
            questions = blockers.get("questions") or []
            mandatory_qs = [q for q in questions if q.get("mandatory")]
            if mandatory_qs:
                # Ask ONLY what the portal marks required. Optional fields are
                # never prompted — if the portal actually needs one, its own
                # validation flags it after the advance click and THAT ask
                # comes back precise, with the portal's own message.
                out = {"ok": False, "code": "ADDITIONAL_INFORMATION_REQUIRED",
                       "questions": mandatory_qs,
                       "page_field_map": blockers.get("page_field_map") or {}}
                if blockers.get("portal_messages"):
                    out["portal_messages"] = blockers["portal_messages"]
                if blockers.get("declaration_map"):
                    out["declaration_map"] = blockers["declaration_map"]
                return out
            if blockers.get("declaration"):
                # Every required field is in: the portal's own declaration is
                # the remaining step. The applicant declares it verbatim in
                # Ellis; the confirmed statement comes back as declaration_fill.
                return {"ok": False, "code": "APPLICANT_ACTION_REQUIRED",
                        "handoff": "personal_declaration",
                        "portal_messages": list(blockers["declaration"]),
                        "declaration_map": blockers.get("declaration_map") or {}}
            if questions and not advance_attempted:
                # Only optional (unmarked) fields remain: don't prompt — click
                # the flow's own Next and let the portal's validation be the
                # judge. One attempt; a refused advance surfaces the portal's
                # real complaints (which promote exactly the truly-required
                # fields to mandatory asks on the next pass).
                advance_attempted = True
                attempted_optional_keys = {q.get("key") for q in questions}
                self._rewind_to_advance_click()
                continue
            if questions:
                if {q.get("key") for q in questions} == attempted_optional_keys:
                    # The page did not move and the portal raised no readable
                    # complaint — asking the SAME skippable questions again is
                    # a circle, not progress. Hand the step to the applicant's
                    # own window honestly.
                    return {"ok": False, "code": "APPLICANT_ACTION_REQUIRED",
                            "handoff": "portal_form",
                            "portal_messages": [
                                "Ellis pressed the form's Next button, but the "
                                "official portal did not move on and reported no "
                                "readable reason. Check the highlighted items in "
                                "the secure window and press Next there."]}
                # A different set after the advance: a genuine new ask.
                return {"ok": False, "code": "ADDITIONAL_INFORMATION_REQUIRED",
                        "questions": questions,
                        "page_field_map": blockers.get("page_field_map") or {}}
            if blockers.get("portal_messages"):
                # Only items the applicant must complete personally remain
                # (signatures, security answers): the existing portal_form
                # handoff owns that, honestly.
                return {"ok": False, "code": "APPLICANT_ACTION_REQUIRED",
                        "handoff": "portal_form",
                        "portal_messages": blockers["portal_messages"]}
            return {"ok": False, "code": "FEE_NOT_DISPLAYED",
                    "detail": "the portal did not display a readable fee"}
        display = f"{fee['amount_cents'] / 100:.2f} {fee['currency']}"
        return {"ok": True, "amount": fee["amount_cents"], "currency": fee["currency"],
                "display": display,
                "government_fee_cents": fee["amount_cents"], "service_fee_cents": 0,
                "payee": self.released.family.operator or self.released.family.name}

    def _portal_form_to_questions(self, res: dict) -> dict | None:
        """A portal_form pause caused by validation on fields WITHOUT flow
        nodes: re-read the page. Fields whose answers Ellis ALREADY HOLDS are
        filled right here (never sent to the applicant's window — eVisa's
        passport "Type" is intake's travel_document_type) and the step
        retries; the rest classify as mandatory questions Ellis asks in its
        own UI. Returns PORTAL_STEP_REFILLED (retry the advance), the
        upgraded ADDITIONAL_INFORMATION result, or None to keep the
        portal_form pause (personal items, unreadable fields)."""
        try:
            blockers = self._page_blockers()
        except Exception:  # noqa: BLE001
            return None
        refills = blockers.get("refill") or []
        # Refill-and-retry ONLY where the caller can actually retry (the fee
        # discovery loop sets the flag around its walk). Elsewhere — e.g. the
        # captcha branch, whose workflow arm cannot re-enter — a leaked
        # PORTAL_STEP_REFILLED would dead-end the case, so those callers keep
        # the question-upgrade/pause behavior unchanged.
        if refills and getattr(self, "_allow_refill_retry", False):
            # One refill pass per PAGE (identity signature): a later page's
            # first refill must not be blocked by an earlier page's, while a
            # page that comes back incomplete after Ellis filled it needs a
            # human, not another silent loop.
            sig = ""
            try:
                live = self._ensure_live()
                sig = live.page_signature() if hasattr(live, "page_signature") else ""
            except Exception:  # noqa: BLE001
                sig = ""
            done = getattr(self, "_portal_form_refilled_sigs", set())
            key = sig or "__once__"
            if key not in done:
                self._portal_form_refilled_sigs = done | {key}
                try:
                    if self._fill_observed_values(refills):
                        # NO rewind: a paused_applicant_action keeps the
                        # cursor AT the refused CLICK node (runtime run():
                        # resume_node or node_id), so resuming re-runs
                        # exactly that click — a rewind here would land on
                        # the PREVIOUS page's advance and double-click it.
                        return {"ok": False, "code": "PORTAL_STEP_REFILLED"}
                except Exception:  # noqa: BLE001
                    pass
        mandatory_qs = [q for q in (blockers.get("questions") or [])
                        if q.get("mandatory")]
        if not mandatory_qs:
            return None
        out = {"ok": False, "code": "ADDITIONAL_INFORMATION_REQUIRED",
               "questions": mandatory_qs,
               "page_field_map": blockers.get("page_field_map") or {}}
        if blockers.get("declaration_map"):
            out["declaration_map"] = blockers["declaration_map"]
        if res.get("portal_messages"):
            out["portal_messages"] = res["portal_messages"]
        return out

    # -- pause classification (page truth) --------------------------------------

    # Fields only the applicant may act on personally: declarations,
    # signatures, security questions. Never asked in Ellis, never auto-filled
    # — they surface as portal_form items for the secure window.
    _PERSONAL_ACTION_RE = re.compile(
        r"(declar|signature|\bsign\b|security\s*(question|answer)|"
        r"secret\s*(question|answer)|perjury|oath|\bagree\b|terms\s*(and|&)\s*conditions)",
        re.IGNORECASE)

    # Placeholder that IS a date format ("DD/MM/YYYY", "MM-DD-YYYY", …).
    _DATE_FORMAT_RE = re.compile(r"^[DMY]{1,4}([./\- ][DMY]{1,4}){1,2}$", re.IGNORECASE)

    # A portal complaint about the submitted photo/portrait: the fix is a NEW
    # upload through Ellis (asked as a document question), never a free-text
    # answer. Deterministic wording match — no model touches the portal path.
    _PHOTO_ISSUE_RE = re.compile(
        r"(portrait|photo(graph)?|\bimage\b|ảnh|foto|retrato|фото|الصورة)",
        re.IGNORECASE)

    # A checkbox whose text IS the application's own TRUTHFULNESS declaration
    # (the applicant declares it verbatim in Ellis; Ellis transcribes the
    # tick). Deliberately narrow: only genuine "I declare … true/complete"
    # style attestations qualify. Bare "hereby" is excluded on purpose — a
    # "hereby authorize/consent/agree to the terms" box is NOT a truthfulness
    # declaration and must never be swept into the auto-ticked bundle; those
    # route to portal_form for the applicant to complete in the secure window.
    # Non-English stems are FIRST-PERSON anchored ("je déclare", "ich
    # erkläre") — a bare stem like "erklär" matches every German "-erklärung"
    # compound (Datenschutzerklärung = privacy policy), which names a document
    # to accept, not a truthfulness declaration.
    _DECLARATION_RE = re.compile(
        r"(declar|je\s+déclare|ich\s+erkläre|erkläre\s+ich|perjury|\boath\b|"
        r"(information|statements?|details|answers?|above|foregoing)[^.]{0,60}"
        r"(true|complete|correct|accurate)|"
        r"(true|complete|correct|accurate)[^.]{0,40}(and|&)[^.]{0,20}"
        r"(complete|correct|accurate|true)|"
        r"cam\s*đoan|beyan\s*ederim|dengan\s+ini\s+menyatakan|заявляю|أقر)",
        re.IGNORECASE)

    # Consent/privacy/T&C language DISQUALIFIES a checkbox from the
    # declarations bundle outright: those boxes are the applicant's to give
    # in the secure window, never transcribed by Ellis. Mirrors the consent
    # regex in live_driver._FIND_DECLARATION_JS.
    _CONSENT_RE = re.compile(
        r"(agree|consent|authoriz|autoris|autoriz|acept|accept|terms|términos|"
        r"condiciones|conditions|termos|bedingungen|zustimm|einverstanden|"
        r"einverständnis|einwillig|datenschutz|akzept|confidentialit|"
        r"privacidad|privacidade|gizlilik|kvkk|rıza|şart|koşul|syarat|"
        r"persetujuan|menyetuju|setuju|privasi|согласен|согласи|условия|"
        r"конфиденциальн|أوافق|موافق|الشروط|خصوصية|privacy|marketing|"
        r"newsletter|subscribe|third[-\s]*part)",
        re.IGNORECASE)

    @staticmethod
    def _field_selector(field: dict) -> str:
        """Deterministic selector for a page-observed field. Attribute form
        [id="…"] survives JSF-style ids with colons; the tag comes from the
        page so an id-less <select>/<textarea> is still reachable. Fields
        whose id/name embed quotes or backslashes are refused (unbuildable
        without escaping games — fail closed)."""
        fid = str(field.get("id") or "").strip()
        fname = str(field.get("name") or "").strip()
        tag = str(field.get("tag") or "input").strip().lower() or "input"
        if any(c in fid + fname for c in ('"', "\\")):
            return ""
        if fid:
            return f'[id="{fid}"]'
        if fname:
            return f'{tag}[name="{fname}"]'
        return ""

    def _page_blockers(self) -> dict:
        """What the LIVE page is actually waiting on: an observed CAPTCHA;
        fields whose answers Ellis already holds (silent `refill`); visible
        required fields with no value (`questions` for the applicant, plus a
        server-side selector map); and applicant-personal items
        (`portal_messages` — declarations, choices only they may make).
        Questions carry NO selectors — the applicant payload stays
        applicant-safe; the selector map travels separately."""
        # Classification can take a while (harvesting real option lists off
        # virtualized widgets): checkpoint honestly so the applicant sees
        # "checking the form", not a stale step — and the lease stays renewed.
        if self._progress_sink:
            try:
                self._progress_sink("checking_form", "active")
            except Exception:  # noqa: BLE001
                pass
        try:
            driver = self._ensure_live()
        except Exception:  # noqa: BLE001 — no page truth: report nothing
            return {}
        # ONE page round-trip for challenge + fields + validation (three
        # separate reads were three round-trips on every pass).
        probe = None
        if hasattr(driver, "page_probe"):
            try:
                probe = driver.page_probe() or {}
            except Exception:  # noqa: BLE001
                probe = None
        # A bot check FIRST, because it invalidates everything read below it:
        # the page underneath is a refusal, not a form, and classifying it
        # yields nonsense pauses. Checked here rather than at NAVIGATE because
        # Cloudflare injects its banner asynchronously — TDAC's goto() returns a
        # clean 200 and the challenge appears seconds later, during filling and
        # during restore_portal, so a navigation-time check never saw it
        # (2026-08-03). This runs on every classification pass, which is every
        # place the page is looked at.
        if self._bot_check_present(driver):
            return {"bot_check": True}
        if probe is not None:
            if (probe.get("captcha") or {}).get("present"):
                return {"captcha": True}
            if not probe.get("ok"):
                return {}
            form = {"ok": True, "fields": probe.get("fields") or []}
            validation = probe.get("validation") or {}
        else:
            try:
                cap = driver.captcha_state() if hasattr(driver, "captcha_state") else {}
            except Exception:  # noqa: BLE001
                cap = {}
            if cap.get("ok") and cap.get("present"):
                return {"captcha": True}
            if not hasattr(driver, "read_form_state"):
                return {}
            try:
                form = driver.read_form_state() or {}
            except Exception:  # noqa: BLE001
                return {}
            if not form.get("ok"):
                return {}
            validation = None
        # The portal's OWN validation complaints are the ground truth for
        # requiredness — DOM markers are often absent (hidden required marks).
        # A flagged field is required no matter what its attributes claim.
        flagged: dict[str, str] = {}
        banners: list[str] = []
        try:
            ve = validation if validation is not None else (
                driver.read_validation_errors()
                if hasattr(driver, "read_validation_errors") else {})
            for fdef in (ve or {}).get("fields") or []:
                fid = str(fdef.get("id") or "").strip()
                if fid:
                    flagged[fid] = str(fdef.get("message") or "").strip()
            # Page-level banners (alerts not tied to a field): the portal's
            # own words about what it rejected — Ellis reads them so the
            # applicant never has to hunt for error boxes on the form.
            for msg in (ve or {}).get("messages") or []:
                m = str(msg).strip()[:200]
                if m and m not in banners and m not in flagged.values():
                    banners.append(m)
        except Exception:  # noqa: BLE001
            flagged, banners = {}, []
        flow_nodes = list(self._flow().nodes.values())
        nodes_by_input = {str(n.get("input_source") or ""): n for n in flow_nodes
                          if n.get("input_source")}
        answers = dict(self.app_row.answers or {})
        questions, field_map, refill, messages = [], {}, [], []
        declarations: list[dict] = []
        seen_keys: set[str] = set()
        unreadable = 0
        harvested = 0
        for f in form.get("fields") or []:
            if not f.get("empty") or f.get("sensitive"):
                continue
            ftype = str(f.get("type") or "text")
            label = str(f.get("label") or "").strip()
            clean_label = label.rstrip(":：*＊ ").strip()
            fname = str(f.get("name") or "").strip()
            personal = bool(self._PERSONAL_ACTION_RE.search(f"{label} {fname}"))
            if ftype == "checkbox":
                selector = self._field_selector(f)
                if selector and self._DECLARATION_RE.search(label) \
                        and not self._CONSENT_RE.search(label):
                    # The portal's own declaration statement(s): the applicant
                    # declares them personally IN ELLIS (verbatim text shown),
                    # and Ellis transcribes the tick(s) + the advance click.
                    # No DOM 'required' marker needed — portals rarely set one
                    # on the declaration box.
                    declarations.append({"selector": selector,
                                         "label": clean_label[:300] or "I hereby declare"})
                elif clean_label and clean_label[:160] not in messages:
                    messages.append(clean_label[:160])
                continue
            if personal:
                # Signature/security-answer style items stay personal: the
                # applicant completes them in the secure window.
                if clean_label and clean_label[:160] not in messages:
                    messages.append(clean_label[:160])
                continue
            selector = self._field_selector(f)
            if not selector:
                unreadable += 1
                continue
            fid = str(f.get("id") or "").strip()
            # Match the node that ADDRESSES this field in any selector form
            # ('#id', '[id="id"]', 'tag[name="…"]', with ':visible'/'>> nth='):
            # a literal-string lookup loses every non-'#id' flow.
            from ..adapter_factory.runtime import FlowRunner as _FR
            node = None
            for cand in flow_nodes:
                if fid and _FR._selector_targets_field(cand.get("selector") or "", fid):
                    node = cand
                    break
                if fname and _FR._selector_targets_field(cand.get("selector") or "", fname):
                    node = cand
                    break
            kind = "select" if ftype == "select" else \
                "combobox" if ftype == "combobox" else \
                "radio" if ftype == "radio" else \
                "date" if ftype == "date" else "text"
            if node is not None and node.get("action") == "SELECT_SEARCH":
                kind = "combobox"
            if kind == "radio":
                # A radio GROUP is addressed by name (an id would reach only
                # its first option); the option itself is picked by label.
                if not fname or any(c in fname for c in ('"', "\\")):
                    unreadable += 1
                    continue
                selector = f'input[name="{fname}"]'
            # Portal date format: the flow node's declared format wins; a
            # placeholder that IS a format string ("DD/MM/YYYY") is the page's
            # own statement of what it wants.
            fmt = str((node or {}).get("format") or "")
            placeholder = str(f.get("placeholder") or "").strip()
            if not fmt and self._DATE_FORMAT_RE.fullmatch(placeholder):
                fmt = placeholder.upper()
            key, question = self._page_question(f, node)
            if key and node is None and key in nodes_by_input:
                # The page field IS a flow field whose selector drifted:
                # adopt the flow's key and wording so an answer already given
                # under that key is never asked for twice.
                node = nodes_by_input[key]
                _, question = self._page_question(f, node)
                key = question.get("key") or key
                fmt = str(node.get("format") or "") or fmt
            if not key or key in seen_keys:
                if not key:
                    unreadable += 1
                continue
            seen_keys.add(key)
            # Portal-flagged = REQUIRED, whatever the DOM claimed; the
            # portal's verbatim complaint is the honest reason for the ask.
            portal_msg = flagged.get(fid) if fid else None
            if portal_msg is None and fname:
                portal_msg = flagged.get(fname)
            if portal_msg is not None:
                question["mandatory"] = True
                if portal_msg:
                    question["why"] = f"The official portal says: {portal_msg[:160]}"
            held = str(answers.get(key) or "")
            if not held and key in ("type", "passport_type", "type_of_passport"):
                # The portal's "Type" is the passport type Ellis already knows
                # from intake (travel_document_type): fill it, never re-ask.
                doc_t = str(answers.get("passport_type")
                            or answers.get("travel_document_type") or "").strip()
                if doc_t:
                    held = doc_t.replace("_", " ").strip().capitalize()
            if held:
                # Ellis already holds this answer (the applicant gave it, or
                # the portal dropped a filled value): repair silently instead
                # of ever re-asking for data in hand.
                refill.append({"selector": selector, "kind": kind,
                               "value": held, "format": fmt})
                continue
            # Harvesting a virtualized widget's real option list costs seconds
            # per field: do it only for questions that will actually be ASKED
            # (mandatory). Optional fields are auto-skipped by fee discovery,
            # so their options would be minutes of work for nothing.
            if kind == "combobox" and question.get("mandatory") \
                    and not question.get("options") and harvested < 3:
                try:
                    # Bounded harvest: opening and scrolling a virtualized list
                    # costs SECONDS per field (measured 174s in one pass).
                    # Only the first few asks get real option lists; the rest
                    # stay free text, which the portal validates anyway.
                    harvested += 1
                    listed = driver.list_options(selector, max_options=60) or {}
                    opts = [str(o) for o in (listed.get("options") or [])
                            if str(o).strip()][:60]
                    if opts:
                        question["options"] = opts
                except Exception:  # noqa: BLE001 — an unlistable widget stays free text
                    pass
            questions.append(question)
            entry = {"selector": selector, "kind": kind}
            if fmt:
                entry["format"] = fmt
            field_map[key] = entry
            if len(questions) >= 40:
                break
        if len(questions) > 20:
            # Never crowd out the fields the portal marks required.
            ordered = [q for q in questions if q.get("mandatory")] + \
                      [q for q in questions if not q.get("mandatory")]
            questions = ordered[:20]
            keep = {q["key"] for q in questions}
            field_map = {k: v for k, v in field_map.items() if k in keep}
        # Photo/portrait banner complaints become a DOCUMENT ask: the applicant
        # uploads the new photo in Ellis, Ellis re-uploads it to the portal and
        # re-advances. Anything else the banners say rides along verbatim.
        photo_issues = [m for m in banners if self._PHOTO_ISSUE_RE.search(m)]
        for m in banners:
            if m not in photo_issues and m not in messages:
                messages.append(m[:160])
        if photo_issues:
            questions.insert(0, {
                "key": "document:photo", "kind": "document", "mandatory": True,
                "question": " ".join(photo_issues)[:300],
                "why": "Upload a new photo here — Ellis places it on the "
                       "official form and continues for you.",
                "format": ""})
        out: dict = {}
        if questions:
            out["questions"] = questions
            out["page_field_map"] = field_map
        if refill:
            out["refill"] = refill
        if declarations:
            out["declaration"] = [d["label"] for d in declarations]
            out["declaration_map"] = {"selectors": [d["selector"] for d in declarations],
                                      "label": declarations[0]["label"]}
        if unreadable and not out and not messages:
            messages.append("The official form still has required items on this "
                            "page — complete them in the secure window.")
        if messages:
            out["portal_messages"] = messages[:8]
        return out

    @staticmethod
    def _page_question(field: dict, node: dict | None) -> tuple[str, dict]:
        """One applicant-friendly question for a page field. Node-mapped fields
        keep their flow key (input_source) and declared wording; unmapped ones
        derive a readable key from the label — never a selector."""
        import re as _re
        label = str(field.get("label") or "").strip().rstrip(":：*＊ ").strip()
        if node is not None:
            declared = dict(node.get("question") or {})
            key = node.get("input_source") or declared.get("key") or ""
            text = declared.get("question") or (
                f"What is your {label}?" if label else "")
            q = {"key": key, "question": text or f"What is your {key.replace('_', ' ')}?",
                 "why": declared.get("why") or
                 "The official application form requires this information.",
                 "format": declared.get("format", ""),
                 "mandatory": bool(node.get("mandatory", True)),
                 "kind": declared.get("kind") or
                 ("date" if node.get("format") else
                  "select" if node.get("action") == "SELECT_SEARCH" else "text")}
            if declared.get("options"):
                q["options"] = declared["options"]
            return key, q
        # An unmapped field with no human label has no honest wording to ask
        # with (a name attribute like 'txtFld7' is portal-internal jargon):
        # skip it — the portal_form path covers what Ellis cannot phrase.
        if not label:
            return "", {}
        key = _re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:40]
        if not key:
            return "", {}
        ftype = str(field.get("type") or "text")
        # A label that already IS a question ("Who will cover the trip's
        # expenses…", "Did you buy insurance") stays verbatim — wrapping it in
        # "What is your …?" reads as nonsense.
        interrogative = bool(_re.match(
            r"(?i)\s*(who|what|when|where|which|why|how|do|does|did|have|has|is|are|will)\b",
            label))
        q = {"key": key,
             "question": label if label.endswith("?") else
             (f"{label}?" if interrogative else
              f"What is your {label}?" if label else f"What is your {key.replace('_', ' ')}?"),
             "why": "The official form still needs this on the current page."
             if field.get("required") else
             "This field on the official form is still blank — answer it, or "
             "leave it empty to skip.",
             "format": "",
             "mandatory": bool(field.get("required", False)),
             "kind": "select" if ftype in ("select", "combobox", "radio") else
             "date" if ftype == "date" else "text"}
        opts = [str(o) for o in (field.get("options") or []) if str(o).strip()]
        if opts:
            q["options"] = opts
        return key, q

    def _apply_page_fill(self, fills: list) -> None:
        """Fill the applicant's answers into the PAGE-observed fields the flow
        has no nodes for, then REWIND the persisted cursor to the flow's own
        advance CLICK so the next segment re-runs that click under every
        FlowRunner guard (validation re-check, evidence, kill switch) — never
        a page-blind direct click that could land on a control the flow never
        validated."""
        if self._fill_observed_values(fills):
            self._rewind_to_advance_click()

    def _fill_observed_values(self, fills: list) -> int:
        """The fill core alone (no cursor rewind): used by _apply_page_fill
        (which rewinds so the RUNNER re-clicks the advance) and by the
        declaration fast path (which presses Next itself right after and must
        not leave a rewound cursor behind). Returns how many fields landed."""
        driver = self._ensure_live()
        filler = getattr(driver, "fill_observed_field", None)
        if filler is None:
            return 0
        applied = 0
        for f in fills or []:
            sel = str((f or {}).get("selector") or "")
            val = str((f or {}).get("value") or "")
            if not sel or not val:
                continue
            fmt = str((f or {}).get("format") or "")
            if fmt:
                # The same single date authority every flow fill uses: a
                # canonical ISO answer renders in the portal's own format; a
                # non-date value passes through unchanged.
                from .. import dates as dates_mod
                converted = dates_mod.to_portal(val, fmt)
                if converted:
                    val = converted
            try:
                res = filler(sel, val, kind=str(f.get("kind") or "text")) or {}
            except Exception:  # noqa: BLE001 — one bad field never stops the rest
                continue
            if res.get("ok"):
                applied += 1
        return applied

    def _apply_declaration(self, spec: dict) -> None:
        """Transcribe the applicant's in-Ellis declaration onto the portal's
        own checkbox(es), then resume from the flow's advance click (guarded
        rewind — the click runs under every FlowRunner guard)."""
        driver = self._ensure_live()
        marker = getattr(driver, "apply_declaration_mark", None)
        spec = spec or {}
        selectors = [str(s) for s in (spec.get("selectors") or []) if s]
        if not selectors and spec.get("selector"):
            selectors = [str(spec["selector"])]
        if marker is None or not selectors:
            return
        marked = 0
        for selector in selectors:
            try:
                if (marker(selector) or {}).get("ok"):
                    marked += 1
            except Exception:  # noqa: BLE001 — the classifier re-reads the page
                continue
        if marked:
            self._rewind_to_advance_click()

    def _rewind_to_uploads(self) -> None:
        """A re-provided document must actually REACH the portal: move the
        cursor back to the first upload node in the latest reversible window
        so the segment re-uploads (newest document wins) and re-advances —
        all under FlowRunner guards."""
        execution = self._execution()
        flow = self._flow()
        current = execution.current_node or ""
        if not current or current not in flow.order:
            return
        candidate = None
        for nid in flow.order:
            if nid == current:
                break
            node = flow.nodes.get(nid) or {}
            if node.get("action") in ("APPLICANT_HANDOFF", "PAUSE") or \
                    node.get("irreversibility") not in (None, "", "reversible"):
                candidate = None
                continue
            if node.get("action") == "UPLOAD_AUTHORIZED_DOCUMENT" and candidate is None:
                candidate = nid
        if candidate is not None:
            execution.current_node = candidate
            execution.status = "running"
            self.db.commit()

    def _rewind_to_advance_click(self) -> None:
        """Move the persisted cursor back to the nearest preceding PLAINLY
        reversible CLICK node so the flow itself re-clicks its own Next with
        all runtime guards. Never rewinds across a handoff, PAUSE, or any
        non-reversible node, and only acts when the cursor is a real node of
        this flow (a blank cursor means the flow replays from the start under
        the same guards anyway)."""
        execution = self._execution()
        flow = self._flow()
        current = execution.current_node or ""
        if not current or current not in flow.order:
            return
        candidate = None
        for nid in flow.order:
            if nid == current:
                break
            node = flow.nodes.get(nid) or {}
            if node.get("action") in ("APPLICANT_HANDOFF", "PAUSE") or \
                    node.get("irreversibility") not in (None, "", "reversible"):
                candidate = None
                continue
            if node.get("action") == "CLICK":
                candidate = nid
        if candidate is not None:
            execution.current_node = candidate
            execution.status = "running"
            self.db.commit()

    def payment_fill_allowed(self) -> bool:
        """The same fail-closed gates that govern a DECLARED payment_credentials
        handoff apply to the Ellis-fills path: the route's payment_preparation
        capability must be auto-released AND the case's standing authorization
        must cover its action. Without both, the applicant types their card in
        the secure window personally — exactly the pre-redesign behavior."""
        try:
            from ..adapter_factory import auto_release
            from ..adapter_factory.runtime import _standing_auth_covers
            if auto_release.capability_released(
                    self.db, route_key=self.released.route_key,
                    capability="payment_preparation") is None:
                return False
            action = auto_release.CAPABILITY_ACTIONS["payment_preparation"]
            return bool(_standing_auth_covers(self.db, self.app_row.id, action))
        except Exception:  # noqa: BLE001 — any doubt fails closed
            return False

    def fill_payment_details(self, card: dict, **_kwargs) -> dict:
        """Fill the applicant's OWN payment details (explicitly provided for
        this, vault-transported, used once) into the official portal's payment
        form. Ellis never clicks Pay — the applicant reviews the filled page
        in their secure window and confirms the payment personally. Values
        never persist and never reach any error message or checkpoint."""
        if not self.payment_fill_allowed():
            return {"ok": False, "code": "PAYMENT_FILL_NOT_RELEASED"}
        if self._progress_sink:
            try:
                self._progress_sink("filling_payment_details", "active")
            except Exception:  # noqa: BLE001
                pass
        try:
            driver = self._ensure_live()
        except _OutcomeUncertain as e:
            return {"ok": False, "code": "OUTCOME_UNCERTAIN", "detail": str(e)}
        if not hasattr(driver, "read_payment_form"):
            return {"ok": False, "code": "PAYMENT_FILL_UNSUPPORTED"}
        try:
            form = driver.read_payment_form() or {}
        except Exception:  # noqa: BLE001
            form = {}
        if not form.get("present"):
            return {"ok": False, "code": "PAYMENT_FORM_NOT_FOUND"}
        try:
            res = driver.fill_payment_fields(form, card) or {}
        except Exception:  # noqa: BLE001 — code only, never a value
            return {"ok": False, "code": "PAYMENT_FILL_ERROR"}
        if not res.get("ok"):
            return {"ok": False, "code": "PAYMENT_FILL_INCOMPLETE",
                    "filled": res.get("filled") or [],
                    "missing": res.get("missing") or []}
        return {"ok": True, "filled": res.get("filled") or []}

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
        # The portal advancing PAST the payment page to the next step is the
        # real signal the payment step was accepted. A receipt code is optional
        # metadata, taken ONLY from a builder-declared receipt selector — never
        # scraped from body text, which cannot distinguish a captured payment
        # from a declined/pending transaction id on the same page.
        receipt_no = self._read_extract("receipt_extraction", declared_only=True)
        result = {"ok": True}
        if receipt_no:
            result["receipt"] = {"receiptNo": receipt_no}
        return result

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
        compiled = self._flow()
        # A flow that never MAPPED a submission (no node carrying
        # submission_accepted success evidence) cannot submit anything:
        # running it to COMPLETE and then scraping a reference off the page
        # would report SUBMITTED for an application the portal never
        # received. Fail closed before any irreversible-path bookkeeping.
        if not any("submission_accepted" in str(n.get("success_evidence") or "")
                   for n in compiled.nodes.values()):
            return {"ok": False, "code": "SUBMISSION_NOT_MAPPED",
                    "detail": "this adapter observed no submission control on "
                              "the portal; submission stays with the applicant"}
        try:
            assert_execution_allowed(
                self.db, route_key=self.released.route_key,
                application_id=self.app_row.id, compiled=compiled)
        except RuntimeRefused as e:
            return {"ok": False, "code": "SUBMISSION_BLOCKED", "detail": str(e)[:300]}
        res = self._advance(stop_before=None)   # run to COMPLETE
        out = self._result_retrying_refills(
            res, lambda: self._advance(stop_before=None),
            ok_statuses=("completed",))
        if not out["ok"]:
            return out
        ref = self._read_extract("confirmation_extraction")
        if ref is None:
            return {"ok": False, "code": "OUTCOME_UNCERTAIN",
                    "detail": "no official confirmation reference readable"}
        self._store_confirmation_screenshot()
        return {"ok": True, "confirmation": {"referenceNo": ref}}

    def _store_page_document(self, png, *, doc_type: str, name: str):
        """Persist a page capture as a case document (served only through the
        signed preview endpoint). Returns the document id, or None."""
        import hashlib
        if not png:
            return None
        try:
            doc = models.StoredDocument(
                org_id=self.app_row.org_id, application_id=self.app_row.id,
                name=name, mime="image/png",
                size_bytes=len(png), sha256=hashlib.sha256(png).hexdigest(),
                doc_type=doc_type, ocr_status="skipped",
                execution_class=self.execution_class)
            self.db.add(doc)
            self.db.flush()
            self.db.add(models.DocumentBlob(document_id=doc.id, content=png))
            self.db.commit()
            return doc.id
        except Exception:  # noqa: BLE001
            self.db.rollback()
            return None

    def _store_confirmation_screenshot(self) -> None:
        """Preserve the portal's own confirmation page as a case document —
        shown on the Ellis result screen and included in the data export.
        Best-effort: a failed capture never degrades a successful submission."""
        try:
            driver = self._ensure_live()
            shot = getattr(driver, "screenshot_png", None)
            png = shot() if shot else None
        except Exception:  # noqa: BLE001
            png = None
        self._store_page_document(png, doc_type="submission_confirmation",
                                  name="submission-confirmation.png")

    # Rejection language a portal prints in its own error dialog, across the
    # seeded families' languages. Presence of any of these means the notice
    # is a REJECTION, never an acceptance — so it is never scraped for a
    # reference nor confirmed past as success.
    # Every stem is \b-anchored. Unanchored, the Spanish `fall` matched inside
    # eVisa's own English entry conditions — "Not FALLing under the cases of
    # suspension from entry" — and turned the success notice into a rejection,
    # so a correct CAPTCHA code came back "wasn't accepted" (2026-08-03).
    # Prefixes that are legitimately word-initial keep a trailing \w* instead.
    _NOTICE_ERROR_RE = re.compile(
        r"(\binvalid|\bincorrect|\bwrong\b|\bfail\w*|\berror|"
        r"\bnot\s+match|\bdoes\s+not\s+match|\btry\s+again\b|\bexpired\b|"
        r"\bkhông\s+đúng|\bkhông\s+hợp\s+lệ|\bsai\b|"      # vi
        # Spanish failure words are spelled out, not stemmed: any `fall`
        # stem loose enough to catch "falló" also catches English "falling".
        r"\binválid|\bfall[aoó]s?\b|\bfallar\b|\bfallid[oa]s?\b|"   # es
        r"\binvalide|\berreur|\béchou\w*|"                 # fr
        r"\bungültig|\bfalsch\b|\bfehler\b|"               # de
        r"\bhatal\w*|\bgeçersiz\b|\byanlış\b|"             # tr
        r"\btidak\s+valid|\bsalah\b|\bgagal\b|"            # id
        r"\bневерн|\bошибк|\bнедейств|"                    # ru
        r"غير\s+صالح|خطأ|خاطئ)",                            # ar
        re.IGNORECASE)

    # An explicit completion marker outranks the error vocabulary: portals
    # print terms and conditions inside their success notices, and one stray
    # cautionary word must not reclassify a completed declaration.
    _NOTICE_SUCCESS_RE = re.compile(
        r"(\bcompleted\b|\bsuccessful\w*\b|\bsuccess\b|\bsubmitted\b|"
        r"\bthành\s+công\b|\bhoàn\s+thành\b|"              # vi
        r"\bexitosa\w*\b|\bcompletad\w*\b|"                # es
        r"\bréussi\w*\b|\bterminée?\b|"                    # fr
        r"\berfolgreich\b|\babgeschlossen\b|"              # de
        r"\bbaşarılı\b|\bberhasil\b|\bselesai\b|"          # tr / id
        r"\bуспешн\w*\b|"                                  # ru
        r"\bتم\s+بنجاح\b|"                                  # ar
        r"完了|成功|完成|완료|성공)",
        re.IGNORECASE)

    @staticmethod
    def _bot_check_present(driver) -> bool:
        """Is the page refusing this session as automated, right now?

        Reads the page's own words through the same narrow matcher the runtime
        uses, so one vocabulary describes a bot check everywhere. Unreadable
        pages are NOT a bot check — a driver problem must not send the
        applicant to clear a challenge that may not exist.
        """
        from ..adapter_factory.runtime import _BOT_CHECK_RE
        if not hasattr(driver, "read_text"):
            return False
        try:
            res = driver.read_text("body") or {}
        except Exception:  # noqa: BLE001
            return False
        if not res.get("ok"):
            return False
        return bool(_BOT_CHECK_RE.search(str(res.get("text") or "")))

    def _notice_is_error(self, driver) -> bool:
        """Is the on-screen notice a rejection dialog rather than a success
        notice?

        Reads the NOTICE's own text — never the page behind it, which on a
        government form carries pages of conditions and warnings that no
        error vocabulary can be safely matched against. An explicit success
        marker wins over an error word. Fail-safe: unreadable text is treated
        as an error, so an unreadable dialog is never scraped for a reference
        nor confirmed past as success.
        """
        reader = getattr(driver, "notice_text", None)
        if reader is None:
            return True          # no notice-scoped reader: refuse to guess
        try:
            res = reader()
        except Exception:  # noqa: BLE001
            return True
        if not isinstance(res, dict) or not res.get("ok"):
            return True
        text = res.get("text") or ""
        if not text.strip():
            return True
        if self._NOTICE_SUCCESS_RE.search(text):
            return False
        return bool(self._NOTICE_ERROR_RE.search(text))

    # A page that keeps re-raising notices is not making progress; stop
    # acknowledging and let classification report honestly.
    _MAX_NOTICE_CONFIRMS = 3

    def _confirm_blocking_notice(self) -> bool:
        """Acknowledge a blocking notice dialog and report whether the page
        moved.

        This is the eVisa "DECLARATION COMPLETED" notice and its equivalents:
        a dialog that states a result and offers one Confirm. It asks the
        applicant nothing, so waiting for a human is a stall — Ellis presses
        it, exactly as the flow always did after a CAPTCHA answer. What is new
        is only WHEN: any point the run reaches one, not just that single path.

        Refuses to press anything it cannot vouch for. An error notice is left
        standing for the caller to classify, its text is recorded first, and
        movement must be proved by the page's own signature rather than
        assumed from a successful click.
        """
        try:
            driver = self._ensure_live()
        except Exception:  # noqa: BLE001
            return False
        state = getattr(driver, "notice_state", None)
        confirm = getattr(driver, "confirm_notice", None)
        if state is None or confirm is None:
            return False
        try:
            if not (state() or {}).get("present"):
                return False
            # A rejection is a real answer about the run — never press past it.
            if self._notice_is_error(driver):
                return False
            self._capture_registration_notice(driver)
            sig_before = driver.page_signature() \
                if hasattr(driver, "page_signature") else ""
            if not (confirm() or {}).get("ok"):
                return False
            if hasattr(driver, "settle"):
                driver.settle(1200)
            sig_after = driver.page_signature() \
                if hasattr(driver, "page_signature") else ""
            if sig_before and sig_after:
                return sig_after != sig_before
            # No signature to compare: the notice being gone is the evidence.
            return not (state() or {}).get("present")
        except Exception:  # noqa: BLE001
            return False

    def _capture_registration_notice(self, driver) -> None:
        """The portal's registration NOTICE carries the e-Visa document code
        the applicant is told to note. Ellis notes it FOR them: screenshot the
        notice as a case document and record the code as the case's portal
        reference before confirming past it."""
        try:
            shot = getattr(driver, "screenshot_png", None)
            self._store_page_document(shot() if shot else None,
                                      doc_type="registration_notice",
                                      name="registration-notice.png")
        except Exception:  # noqa: BLE001
            pass
        try:
            code = self._labeled_reference_from_page(driver, "confirmation_extraction")
            if code and not (self.app_row.portal_reference or ""):
                self.app_row.portal_reference = code
                self.db.commit()
                from .. import audit
                audit.record(self.db, org_id=self.app_row.org_id,
                             application_id=self.app_row.id,
                             action="registration_code_recorded",
                             detail={"reference": code}, actor="ellis")
        except Exception:  # noqa: BLE001
            pass

    def captcha_context(self) -> dict:
        """Everything the applicant needs to solve the portal's challenge
        WITHOUT leaving Ellis: a capture of the challenge widget (the digits
        image) and of the full current page (the review of their application
        as the portal shows it). Ellis displays them; the human solves."""
        try:
            driver = self._ensure_live()
        except Exception:  # noqa: BLE001
            return {}
        out: dict = {}
        try:
            cap = getattr(driver, "captcha_screenshot_png", None)
            doc_id = self._store_page_document(
                cap() if cap else None,
                doc_type="captcha_challenge", name="captcha.png")
            if doc_id:
                out["captcha_document_id"] = doc_id
        except Exception:  # noqa: BLE001
            pass
        try:
            shot = getattr(driver, "screenshot_png", None)
            doc_id = self._store_page_document(
                shot() if shot else None,
                doc_type="portal_review", name="portal-review.png")
            if doc_id:
                out["review_document_id"] = doc_id
        except Exception:  # noqa: BLE001
            pass
        return out

    def restore_view(self, **_kwargs) -> dict:
        """Rebuild the live portal page at the persisted step after a session
        loss (the applicant's secure window must show the real form, never a
        blank tab). Reversible work only: _ensure_live's honest rewind plus
        the resumable segment re-run, stopping before any handoff or
        irreversible node. When the restored page displays the fee, it is
        read deterministically and returned for the applicant to CONFIRM —
        read from the real page, never invented."""
        def boundary(node):
            return node.get("action") in ("APPLICANT_HANDOFF", "PAUSE") or \
                node.get("irreversibility") == "irreversible"
        # A session that is blank (or off the portal entirely) has NO page to
        # resume: advancing from the persisted cursor would stop at the next
        # handoff without ever navigating — the applicant keeps staring at
        # about:blank. Rewind the REVERSIBLE cursor so the flow re-enters the
        # application from the start. Refused outright once anything
        # irreversible has been passed (reconciliation owns that case).
        try:
            driver = self._ensure_live()
            url = str(((getattr(driver, "current_url", None) or (lambda: {}))() or {})
                      .get("url") or "")
            hosts = [h.lower() for h in self._hosts()]
            from urllib.parse import urlparse
            host = urlparse(url).netloc.lower()
            on_portal = bool(host) and any(host == h or host.endswith("." + h) for h in hosts)
            if not on_portal:
                execution = self._execution()
                if execution.current_node and self._passed_irreversible(execution.current_node):
                    return {"ok": False, "code": "OUTCOME_UNCERTAIN",
                            "detail": "the portal session was lost after an "
                                      "irreversible step; Ellis will not replay it"}
                if execution.current_node:
                    execution.current_node = ""
                    self.db.commit()
        except _OutcomeUncertain as e:
            return {"ok": False, "code": "OUTCOME_UNCERTAIN", "detail": str(e)}
        except Exception:  # noqa: BLE001 — fall through to the normal advance
            pass
        res = self._advance(stop_before=boundary)
        out = self._result_from(res, ok_statuses=("boundary", "completed"))
        if not out.get("ok"):
            return out
        fee = self._fee_from_page_text()
        if isinstance(fee, dict):
            out["fee"] = {"ok": True, "amount": fee["amount_cents"],
                          "currency": fee["currency"],
                          "display": f"{fee['amount_cents'] / 100:.2f} {fee['currency']}",
                          "government_fee_cents": fee["amount_cents"],
                          "service_fee_cents": 0,
                          "payee": self.released.family.operator or self.released.family.name,
                          "source": "portal_page_read"}
        else:
            # The restored page shows no fee — report what it DOES show so a
            # mislabeled fee pause can swap to the real ask.
            try:
                blockers = self._page_blockers()
            except Exception:  # noqa: BLE001
                blockers = {}
            if blockers:
                out["blockers"] = blockers
        return out

    def read_current_fee(self, **_kwargs) -> dict:
        """Read the fee from the page the portal is showing RIGHT NOW —
        no navigation, no form work. Used on demand once the applicant has
        walked the portal to the step that displays the amount. When no fee is
        readable, the page's real blockers (CAPTCHA / unanswered questions)
        ride along so a mislabeled fee pause can reclassify itself."""
        fee = self._fee_from_page_text()
        if not isinstance(fee, dict):
            out = {"ok": False, "code": "FEE_NOT_DISPLAYED",
                   "detail": "no single readable fee on the current page"}
            try:
                blockers = self._page_blockers()
            except Exception:  # noqa: BLE001
                blockers = {}
            if blockers:
                out["blockers"] = blockers
            return out
        return {"ok": True, "fee": {
            "ok": True, "amount": fee["amount_cents"], "currency": fee["currency"],
            "display": f"{fee['amount_cents'] / 100:.2f} {fee['currency']}",
            "government_fee_cents": fee["amount_cents"], "service_fee_cents": 0,
            "payee": self.released.family.operator or self.released.family.name,
            "source": "portal_page_read"}}

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

    def _read_extract(self, kind: str, *, declared_only: bool = False):
        """Read a confirmation/receipt reference via the adapter's declared
        extraction selector, from the LIVE page. When the manifest declares no
        selector, fall back to a deterministic labeled-reference parse of the
        page's visible text — exactly one unambiguous match or nothing.
        None = not verifiable (the caller stays honest).

        declared_only=True refuses the page-text fallback: a scraped label
        cannot tell an accepted result from a rejected one ('Transaction:
        X — declined' matches the same regex as a success), so a PAYMENT claim
        may only come from a builder-declared selector, never body text."""
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
        if declared_only:
            return None
        return self._labeled_reference_from_page(driver, kind)

    # Labels the official portals themselves print next to the reference —
    # static strings, never inferred, covering the portal languages of the
    # seeded families (the single-candidate rule below keeps a broad list
    # safe: any ambiguity still returns None).
    _REFERENCE_LABELS = {
        "confirmation_extraction": (
            "registration code", "application code", "dossier code",
            "reference number", "application number", "mã hồ sơ", "mã đăng ký",
            "electronic document code", "e-visa app no",
            "número de solicitud", "código de solicitud", "folio",
            "numéro de demande", "numéro de dossier", "numéro de référence",
            "número do pedido", "código do pedido", "número de referência",
            "antragsnummer", "vorgangsnummer", "referenznummer",
            "başvuru numarası", "referans numarası",
            "nomor permohonan", "nomor referensi",
            "номер заявления", "номер заявки", "رقم الطلب"),
        "receipt_extraction": (
            "receipt", "transaction", "payment reference", "mã giao dịch",
            "số biên lai",
            "comprobante", "número de transacción", "referencia de pago",
            "numéro de transaction", "référence de paiement",
            "número da transação", "recibo",
            "transaktionsnummer", "zahlungsreferenz",
            "işlem numarası", "makbuz",
            "nomor transaksi", "bukti pembayaran",
            "номер транзакции", "квитанция", "رقم المعاملة"),
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
        # Keyword gating only selects LINES for the strict parse; ambiguity
        # (two distinct amounts) still returns None, so breadth is safe.
        keywords = ("fee", "phí", "lệ phí", "payment amount", "amount to pay",
                    "tasa", "tarifa", "monto a pagar", "frais", "montant",
                    "taxa", "valor a pagar", "gebühr", "zu zahlen", "ücret",
                    "biaya", "сбор", "стоимость", "к оплате", "الرسوم")
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

    # ---- appointments -------------------------------------------------------
    # An e-visa flow declares no appointment nodes, so these correctly report
    # "not applicable". A flow that DOES declare them (a visa-centre/consular
    # route) reads the portal's real calendar and books the applicant's chosen
    # slot through the same evidence-gated FlowRunner as every other step.

    def _appointment_nodes(self) -> tuple[dict | None, dict | None]:
        read_node = book_node = None
        for n in self._flow().nodes.values():
            if n.get("action") == "READ_APPOINTMENT_INVENTORY":
                read_node = read_node or n
            if n.get("action") == "BOOK_APPOINTMENT":
                book_node = book_node or n
        return read_node, book_node

    def search_appointments(self, **_kwargs) -> dict:
        """Read the portal's REAL bookable inventory (read-only; reserves
        nothing), then annotate each slot with its distance from the
        applicant's address and rank nearest-first."""
        read_node, _ = self._appointment_nodes()
        if read_node is None:
            return {"ok": True, "slots": []}     # this route has no appointments
        try:
            driver = self._ensure_live()
        except Exception:  # noqa: BLE001
            return {"ok": False, "code": "PORTAL_UNAVAILABLE"}
        reader = getattr(driver, "read_appointment_slots", None)
        if reader is None:
            return {"ok": False, "code": "NOT_SUPPORTED"}
        res = reader(read_node)
        if not res.get("ok"):
            return {"ok": False, "code": res.get("code", "SLOT_READ_FAILED")}
        from .. import appointments as appt
        centers = (self.released.version_row.manifest or {}).get("appointment_centers") or {}
        slots = appt.annotate_distance(res.get("slots", []), centers,
                                       self._applicant_location())
        return {"ok": True, "slots": appt.rank_by_nearest(slots, self._appt_prefs())}

    def _applicant_location(self) -> dict | None:
        """The applicant's own coordinates, if their case carries them (set
        when they gave Ellis an address). Never inferred from IP or guessed."""
        a = self.app_row.answers or {}
        lat, lon = a.get("address_lat"), a.get("address_lon")
        if lat is None or lon is None:
            return None
        try:
            return {"lat": float(lat), "lon": float(lon)}
        except (TypeError, ValueError):
            return None

    def _appt_prefs(self) -> dict:
        a = self.app_row.answers or {}
        return {"maxTravelKm": a.get("max_travel_km")}

    def book_appointment(self, *, slot_id: str = "", **_kwargs) -> dict:
        """Book the slot the APPLICANT chose. Their explicit choice is recorded
        on the case and the flow's BOOK_APPOINTMENT node (reconcile-first,
        evidence-verified) performs the reservation — Ellis never auto-books."""
        _, book_node = self._appointment_nodes()
        if book_node is None:
            return {"ok": False, "code": "NOT_APPLICABLE"}
        if not slot_id:
            return {"ok": False, "code": "NO_SLOT_CHOSEN"}
        from ..adapter_factory.runtime import FlowRunner
        self.app_row.answers = dict(self.app_row.answers or {},
                                    **{FlowRunner.CHOSEN_SLOT_KEY: str(slot_id)})
        self.db.commit()
        res = self._advance(stop_before=None)
        out = self._result_from(res)
        if not out["ok"]:
            return out
        return {"ok": True, "appointment": {"slot_id": slot_id}}

    def reschedule_appointment(self, *, slot_id: str = "", **_kwargs) -> dict:
        """Rescheduling is a fresh booking of a newly chosen slot; the flow's
        reconcile-first booking node replaces the existing reservation rather
        than creating a second one."""
        return self.book_appointment(slot_id=slot_id)

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


def known_missing_questions(db, app_row) -> list[dict]:
    """The form questions Ellis ALREADY KNOWS the portal will ask, computed
    from the released flow's stored nodes — no browser, no portal round-trip.

    Surfaced when the case page opens so the applicant answers BEFORE the run
    instead of a minute into it (measured 2026-07-28: start-click → mid-run
    'additional information' pause took ~65s on the real Vietnam portal).
    Wording/format/mandatory ride the nodes' baked-in question metadata (the
    same metadata FlowRunner._question_for uses mid-run). Live-only context —
    the portal's real option lists, its own validation messages — still comes
    from the mid-run pause path, which remains the fallback and the authority
    (page truth) for anything the stored flow does not know."""
    released = resolve_released_route(db, app_row)
    if released is None:
        return []
    # Through the same derivation the runner applies: a key SPLIT from an
    # answer already given (phone_country_code from '+86 138…') is not a
    # question — asking it up front would ask the applicant to retype half
    # their own phone number.
    from ..adapter_factory.runtime import _with_derived_answers
    answers = _with_derived_answers(app_row.answers or {})
    cached = cached_option_lists(db, released)
    out: list[dict] = []
    seen: set[str] = set()
    for node in (released.version_row.flow or []):
        act = node.get("action")
        if act not in ("FILL_NON_SENSITIVE", "SELECT_SEARCH", "SELECT_RADIO"):
            continue
        if not bool(node.get("mandatory", True)):
            continue
        key = str(node.get("input_source") or "").strip()
        if not key or key in seen:
            continue
        if str(answers.get(key, "") or "").strip():
            continue
        seen.add(key)
        q = dict(node.get("question") or {})
        label = (q.get("question")
                 or (node.get("label") or key.replace("_", " ")).strip())
        entry = {
            "key": key,
            "question": q.get("question")
            or (label if label.endswith("?") else f"What is your {label}?"),
            "why": q.get("why")
            or "The official application form requires this information.",
            "format": q.get("format")
            or ("DD/MM/YYYY" if node.get("format") else "free text"),
            "mandatory": True,
            "kind": q.get("kind") or ("date" if node.get("format") else
                                      "select" if act in ("SELECT_SEARCH",
                                                          "SELECT_RADIO") else "text"),
        }
        options = list(q.get("options") or [])
        row = None
        # A radio group's choices are FIXED and were observed at build time —
        # the applicant can be offered the portal's own words up front, with
        # no browser session and no cached read needed.
        if not options and act == "SELECT_RADIO":
            options = [str((o or {}).get("label", "")).strip()
                       for o in (node.get("options") or []) if isinstance(o, dict)]
            options = [t for t in options if t]
        if not options and entry["kind"] == "select":
            row = cached.get(node.get("node_id") or "") or cached.get(key)
            if row is not None:
                options = list(row.options or [])
        if options:
            entry["options"] = options
            # Only a COMPLETE list may be presented as the portal's own set of
            # choices. A partial read is offered as suggestions on a field the
            # applicant can still type into — otherwise a traveller whose real
            # border gate sits past the rows Ellis managed to read would have
            # no way to enter it.
            if row is not None and not row.complete:
                entry["options_partial"] = True
                entry["format"] = ""
        elif entry["kind"] == "select":
            # A select with no known choices must NOT claim "choose from list"
            # over a free-text box (the portal's real list is only readable on
            # its page). Say so honestly: the applicant types an answer, and
            # the fill step verifies it against the live list, re-asking with
            # the real options if it does not match.
            entry["format"] = ""
            entry["options_pending"] = True
        out.append(entry)
    return out


def cached_option_lists(db, released: ReleasedRoute) -> dict:
    """Every option list read from THIS adapter version's live pages, by node
    id and by answer key. One query, because the case page waits on it.

    Only corroborated lists are returned. A list that depends on another answer
    (the wards of the province THIS applicant chose) or on who is signed in
    never repeats between applicants, so it never reaches the bar and is never
    re-served to someone else. A long list (25+) is trusted on first sight —
    a personalized list is never that long.

    The live page always remains the authority: a cached value the portal no
    longer offers is caught by the fill step and re-asked with the real list
    (runtime._option_matches / _rejected_fills)."""
    from ..adapter_factory import models as fm
    rows = db.execute(select(fm.PortalFieldOptionCache).where(
        fm.PortalFieldOptionCache.candidate_id == released.binding.candidate_id,
        fm.PortalFieldOptionCache.candidate_version == int(
            released.binding.candidate_version))).scalars().all()
    out: dict = {}
    for r in rows:
        if not (r.observations >= 2 or len(r.options or []) >= 25):
            continue          # not corroborated: may be applicant-specific
        if r.node_id:
            out[r.node_id] = r
        out.setdefault(r.field_key, r)
    return out


def cached_field_options(db, released: ReleasedRoute, field_key: str) -> list[str]:
    """The portal's own list for one field (see cached_option_lists)."""
    row = cached_option_lists(db, released).get(field_key)
    return list(row.options or []) if row is not None else []


def remember_field_options(db, *, candidate_id: str, candidate_version: int,
                           field_key: str, options: list, node_id: str = "",
                           complete: bool = False, deliberate: bool = False) -> None:
    """Record what the portal actually offered for a field, so the NEXT
    applicant on this adapter version is asked with the portal's real list
    before any browser session opens.

    Keyed to the exact version (a new adapter version re-reads the portal
    rather than inheriting a list that may no longer match the form) and to
    the node (one answer key can be collected by two widgets whose lists must
    not overwrite each other). Repeating an identical list corroborates it;
    a changed list replaces it and starts counting again."""
    from ..adapter_factory import models as fm
    values = [str(o).strip() for o in (options or []) if str(o).strip()]
    if not values or not field_key:
        return
    q = select(fm.PortalFieldOptionCache).where(
        fm.PortalFieldOptionCache.candidate_id == candidate_id,
        fm.PortalFieldOptionCache.candidate_version == int(candidate_version))
    q = q.where(fm.PortalFieldOptionCache.node_id == node_id) if node_id else \
        q.where(fm.PortalFieldOptionCache.field_key == field_key)
    row = db.execute(q).scalars().first()
    # A deliberate warm-up read happens on an UNTOUCHED form with no applicant
    # answers set, so its list cannot be specific to anyone — it is
    # corroborated on its own (option_warmup.warm_option_lists).
    first_count = 2 if deliberate else 1
    if row is None:
        db.add(fm.PortalFieldOptionCache(
            candidate_id=candidate_id, candidate_version=int(candidate_version),
            field_key=field_key, node_id=node_id, options=values,
            complete=bool(complete), observations=first_count))
    elif list(row.options or []) == values:
        row.observations = int(row.observations or 1) + 1
        row.complete = bool(row.complete or complete)
        row.captured_at = datetime.now(timezone.utc)
    else:
        # The portal's list changed (or this read saw more of it). A longer
        # read supersedes a truncated one; anything else starts over.
        row.options = values
        row.complete = bool(complete)
        row.observations = first_count
        row.captured_at = datetime.now(timezone.utc)
    db.commit()


def _has_action(flow: list, action: str) -> bool:
    """Does the released typed flow declare this action? Capabilities are read
    from the flow itself, never asserted — a route can only claim to book
    appointments if its adapter actually carries a booking node."""
    return any((n or {}).get("action") == action for n in (flow or []))


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
                       if n.get("action") in ("FILL_NON_SENSITIVE", "SELECT_SEARCH",
                                              "SELECT_RADIO")
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
        # Appointment ability follows the FLOW: a route whose adapter declares
        # calendar/booking nodes really can read and book; every other route
        # honestly reports none/prohibited (an e-visa flow has no appointment).
        appointment_search=("automated" if _has_action(flow, "READ_APPOINTMENT_INVENTORY")
                            else "none"),
        appointment_booking=("applicant_selected"
                             if _has_action(flow, "BOOK_APPOINTMENT") else "prohibited"),
        reschedule_policy=("applicant_selected"
                           if _has_action(flow, "BOOK_APPOINTMENT") else "prohibited"),
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
