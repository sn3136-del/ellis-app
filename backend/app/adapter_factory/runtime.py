"""Deterministic production runtime (brief §19-§20): the FlowRunner.

Executes ONLY a released, pinned candidate version, compiled fresh from its
immutable record. Per node it: checks the kill switch, enforces the hostname
allowlist, runs the single declared deterministic action, verifies declared
evidence, records a redacted checkpoint, and either advances, pauses for the
applicant, reconciles, or FAILS CLOSED. No model is consulted anywhere on
this path — Kimi is structurally absent (nothing here imports a provider).

The driver passed in is duck-typed (goto/fill/click/read_text/network_events/
official_state): SyntheticPortal in synthetic testing, a Browserbase-backed
page driver in live tiers.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

# A portal refusing the AUTOMATED SESSION, as distinct from refusing a value.
# Cloudflare answers 403 with "We couldn't verify your request"; Akamai and
# PerimeterX have their own wording. Matched on the page's own text plus the
# status, so an ordinary 403 (a real permission error) is not mistaken for one.
_BOT_CHECK_RE = re.compile(
    r"(couldn'?t verify your request|verify you are (a )?human|"
    r"checking your browser|enable javascript and cookies to continue|"
    r"attention required.{0,20}cloudflare|access denied.{0,40}reference|"
    r"unusual traffic|automated (access|requests?) (is |are )?(not )?"
    r"(allowed|blocked|detected))", re.IGNORECASE)


def _bot_check_blocked(driver, res) -> bool:
    """True when the PAGE says it refused this session as automated.

    Deliberately narrow: the status must be a refusal AND the page must say so
    in its own words. A 403 alone is often a genuine permission answer, and a
    challenge page that still renders the form is not a block.
    """
    if not isinstance(res, dict):
        return False
    # The TEXT is the evidence, not the status. TDAC answers 200 and renders
    # its normal page with the refusal banner laid over it, so gating on 403
    # missed the very case this exists for. Only a status that cannot be a bot
    # check at all is excluded; the wording carries the rest.
    status = int(res.get("status") or 0)
    if status in (404, 410, 500, 502, 504):
        return False
    text = str(res.get("text") or res.get("title") or "")
    if not text and hasattr(driver, "read_text"):
        try:
            text = str((driver.read_text("body") or {}).get("text") or "")
        except Exception:  # noqa: BLE001 — an unreadable page is not a verdict
            return False
    return bool(_BOT_CHECK_RE.search(text))

from sqlalchemy import select

from .. import audit
from . import models as fm
from .compiler import CompiledFlow, compile_flow
from .release import active_binding, kill_engaged


class RuntimeRefused(Exception):
    """Fail-closed refusal: unreleased version, kill switch, scope breach."""


# The honest DISABLED-advance failure. A stable marker: released_flow routes
# this specific reason into page classification (fields the flow has no nodes
# for can complete the form) instead of surfacing it as a dead failure.
FORM_INCOMPLETE_REASON = ("the portal's Next button is still disabled — "
                          "the form is not complete")


_FEE_RE = None


def _country_display_name(code: str) -> str:
    """ISO alpha-2/3 -> registry display name ('' when not a country code).
    Pure reference-data lookup (data/reference/countries.json)."""
    import re as _re
    if not _re.fullmatch(r"[A-Za-z]{2,3}", code or ""):
        return ""
    try:
        from ..visa_snapshot.registry import _country_index
        entry = _country_index().get(code.upper())
        return (entry or {}).get("name", "")
    except Exception:  # noqa: BLE001 — registry unavailable: no transform
        return ""


def parse_fee_text(text: str, *, currency_hint: str = "") -> dict | None:
    """Deterministic parse of a displayed fee ('25 USD', 'USD 25.00', '$25').
    Returns {text, amount_cents, currency} or None when no exact single amount
    can be read — the caller must then refuse to proceed to payment."""
    import re
    t = (text or "").strip()
    cur = ""
    m = re.search(r"\b(USD|VND|EUR|CNY|RMB|KRW)\b", t, re.I)
    if m:
        cur = m.group(1).upper().replace("RMB", "CNY")
    elif "$" in t:
        cur = "USD"
    elif currency_hint:
        cur = currency_hint.upper()
    amounts = re.findall(r"(?<![\d.,])(\d{1,3}(?:[.,]\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)(?![\d])", t)
    if not cur or len(amounts) != 1:
        return None
    raw = amounts[0]
    # '1,000' / '1.000' thousands separators vs '25.00' decimals
    if "," in raw and "." not in raw:
        raw = raw.replace(",", "")
    elif raw.count(".") > 1:
        raw = raw.replace(".", "")
    try:
        cents = int(round(float(raw) * 100))
    except ValueError:
        return None
    if cents <= 0:
        return None
    return {"text": t[:120], "amount_cents": cents, "currency": cur}


class FlowRunner:
    def __init__(self, db, *, execution: fm.AdapterExecution, compiled: CompiledFlow,
                 driver, case_answers: dict | None = None, documents: list | None = None,
                 on_progress=None):
        self.db = db
        self.execution = execution
        self.flow = compiled
        self.driver = driver
        self.answers = case_answers or {}
        self.documents = documents or []
        self.fee_seen = None
        self.slots_seen = []
        # node_id -> the portal's own option labels, when a mapped answer was
        # not on the portal's fixed list (drives the applicant question).
        self.observed_options: dict[str, list] = {}
        # Fill nodes the PORTAL's own validation flagged (re-asked even though
        # an answer exists), and the portal's verbatim per-field complaints.
        self._rejected_fills: set[str] = set()
        self._portal_field_messages: dict[str, str] = {}
        # One silent repair pass per run: refill fields the portal dropped.
        self._repair_attempted = False
        # One declaration-tick pass per run when an advance is disabled.
        self._declaration_ticked_nodes = set()
        # Optional applicant-safe progress recorder: called with the node's
        # semantic step (never a selector) before and after each node.
        self.on_progress = on_progress

    def _progress(self, node: dict, status: str):
        if self.on_progress is None:
            return
        try:
            from .. import progress as progress_vocab
            step = progress_vocab.step_for_node(node)
            # A handoff node being ENTERED (probe) or STEPPED PAST (no
            # challenge present) is Ellis checking whether the applicant is
            # really needed — a "Waiting for you to …" message there (or as
            # "last completed") tells the applicant to act on nothing. The
            # waiting_* key is honest only when the pause actually happens.
            if status != "handoff" and step.startswith("waiting_") and \
                    node.get("action") in ("APPLICANT_HANDOFF", "PAUSE"):
                step = "checking_form"
            self.on_progress(step, status)
        except Exception:  # noqa: BLE001 — progress must never break the flow
            pass

    # -- entry ---------------------------------------------------------------
    def run(self, *, resume_from: str | None = None, max_nodes: int = 200,
            stop_before=None) -> dict:
        """Walk the flow. `stop_before` is an optional predicate(node) — when it
        matches a node that has not run yet, the runner parks there and returns
        a 'boundary' status so a caller can drive the flow in segments (the
        case-workflow driver uses this to align portal steps with the case
        state machine). The node itself is NOT executed."""
        node_id = resume_from or self.execution.current_node or self.flow.first()
        abort = getattr(self.on_progress, "should_abort", None)
        for _ in range(max_nodes):
            if node_id is None:
                return self._finish("completed", "flow exhausted")
            # A fenced-out executor (lease lost to a stall verdict, or a
            # cancel) stops HERE — before the next real portal action — so a
            # zombie can never interleave with its replacement run.
            if abort is not None and abort():
                self.execution.current_node = node_id
                self.db.commit()
                return {"status": "failed", "node": node_id,
                        "reason": "execution superseded — stopping before the next portal action"}
            node = self.flow.nodes.get(node_id)
            if node is None:
                return self._fail_closed(node_id, "unknown node — failing closed")
            if stop_before is not None and stop_before(node):
                self.execution.current_node = node_id
                self.db.commit()
                return {"status": "boundary", "node": node_id,
                        "action": node.get("action", ""),
                        "handoff_kind": node.get("handoff_kind", "")}
            # Kill switch before EVERY stage (§19).
            if kill_engaged(self.db, self.execution.candidate_id):
                self.execution.status = "killed"
                self.db.commit()
                self._audit("adapter_execution_killed", {"node": node_id})
                return {"status": "killed", "node": node_id}
            self._progress(node, "active")
            outcome = self._step(node)
            self._checkpoint(node_id, outcome["status"], outcome.get("detail", {}))
            self._progress(node, "done" if outcome["status"] == "ok"
                           else outcome["status"])
            if outcome["status"] == "handoff":
                self.execution.status = "paused_applicant_action"
                # A portal-validation pause rewinds to the FIRST flagged field
                # so the resume refills from there (reversible fills only).
                self.execution.current_node = outcome.get("resume_node") or node_id
                self.db.commit()
                result = {"status": "paused_applicant_action", "node": node_id,
                          "handoff_kind": outcome.get("handoff_kind")
                          or node.get("handoff_kind", "")}
                # Missing-answer pauses carry applicant-friendly questions —
                # ALL still-missing fields at once, so the applicant is asked
                # one time instead of field by field.
                if result["handoff_kind"] == "additional_information":
                    result["questions"] = (outcome.get("questions")
                                           or self._collect_missing_questions(node_id))
                if outcome.get("portal_messages"):
                    result["portal_messages"] = outcome["portal_messages"]
                return result
            if outcome["status"] == "uncertain":
                self.execution.status = "outcome_uncertain"
                self.execution.current_node = node_id
                self.db.commit()
                return {"status": "outcome_uncertain", "node": node_id}
            if outcome["status"] == "failed":
                return self._fail_closed(node_id, outcome.get("reason", "step failed"))
            if node["action"] == "COMPLETE":
                return self._finish("completed", "COMPLETE node reached")
            node_id = self.flow.next_of(node_id, outcome.get("branch", "ok"))
            self.execution.current_node = node_id or ""
            self.db.commit()
        return self._fail_closed(node_id, "node budget exhausted")

    # -- per-action execution -------------------------------------------------
    def _step(self, node: dict) -> dict:
        action = node["action"]
        if action == "NAVIGATE":
            url = (node.get("allowed_url_patterns") or [f"https://{node['allowed_hostname']}/"])[0]
            if not self._url_ok(url):
                return {"status": "failed", "reason": f"navigation outside allowlist: {url}"}
            res = self.driver.goto(url)
            if _bot_check_blocked(self.driver, res):
                # A bot check is a question about whether a human is here, and
                # the honest answer is to fetch one. Thailand's TDAC sits behind
                # Cloudflare bot management: it refuses the automated session
                # outright, so no amount of retrying reaches the form
                # (2026-08-03). The applicant clears it in their own secure
                # window — the same doctrine as a CAPTCHA — and Ellis resumes
                # in that now-trusted session and fills the form as normal.
                # Ellis never tries to look like a different browser.
                return {"status": "handoff", "handoff_kind": "portal_verification"}
            return self._from_driver(node, res)
        if action == "WAIT_FOR_STATE":
            return {"status": "ok"}
        if action == "CLICK":
            if node.get("requires_signed_terms"):
                # Ellis records the applicant's agreement to the portal's own
                # terms ONLY when they have signed the exact terms text in
                # Ellis. No matching signature -> fail closed to the consent
                # handoff; never enact agreement the applicant did not give.
                from .. import portal_terms as _pt
                consent = _pt.signed_consent(
                    self.db, application_id=self.execution.application_id,
                    portal_family_id=node.get("consent_family_id", ""),
                    expected_hash=node.get("consent_terms_hash", ""))
                if consent is None:
                    return {"status": "handoff",
                            "handoff_kind": "portal_terms_consent"}
            attempts = int(node.get("max_retries", 0)) + 1
            for attempt in range(attempts):
                if node.get("irreversibility") == "irreversible":
                    # Reconcile-before-act, EVERY attempt: never repeat an
                    # irreversible action that already succeeded.
                    if self._already_done(node, self._official()):
                        self._evidence(node, kind="official_record",
                                       category="reconciled_prior_success", strength=2)
                        return {"status": "ok", "detail": {"reconciled": True}}
                selector = node.get("selector") or ""
                if not selector and node.get("selector_source") == "runtime_primary_action":
                    # The spec honestly recorded that no selector could be
                    # observed for this step (a submit control that only exists
                    # past the applicant's own review). Resolve the portal's
                    # primary action HERE, where the page finally shows it.
                    find = getattr(self.driver, "primary_action_selector", None)
                    selector = (find() if find else "") or ""
                    if not selector:
                        return {"status": "failed",
                                "reason": "the portal's primary action could not "
                                          "be found on this page"}
                res = self.driver.click(selector)
                if res.get("ok"):
                    break
                code = res.get("code")
                if code == "DISABLED":
                    # The portal keeps its advance button disabled until the
                    # form satisfies it. The overwhelmingly common reason on
                    # e-visa forms: an unticked declaration box — tick every
                    # one (verified) and retry THIS declared click, once,
                    # INLINE (a `continue` would die with the attempts loop).
                    # Guarded PER NODE, burned only by a pass that actually
                    # ticked something: a portal family with declaration boxes
                    # on two different pages gets the fast path on each, and
                    # an empty/failed find doesn't consume the one chance.
                    _ticked_nodes = getattr(self, "_declaration_ticked_nodes", set())
                    if node["node_id"] not in _ticked_nodes:
                        ticker = getattr(self.driver, "tick_declarations", None)
                        try:
                            ticked = ticker() if ticker is not None else 0
                        except Exception:  # noqa: BLE001
                            ticked = 0
                        if ticked:
                            self._declaration_ticked_nodes = _ticked_nodes | {node["node_id"]}
                            res = self.driver.click(node["selector"])
                            if res.get("ok"):
                                break
                            # The re-click's failure is the truth now: a
                            # TIMEOUT here must reach the TIMEOUT recovery
                            # below, not be reported as "still disabled".
                            code = res.get("code")
                    if code == "DISABLED" and not self._repair_attempted:
                        self._repair_attempted = True
                        if self._repair_flagged_fields():
                            # Re-click INLINE — the attempts loop may already
                            # be spent, and exhausting it after a clean repair
                            # lets the post-loop check report a phantom ok.
                            res = self.driver.click(node["selector"])
                            if res.get("ok"):
                                break
                            code = res.get("code")
                    if code == "DISABLED":
                        # A REAL blocker to explain — never a click to force
                        # (forcing reports success on a button that cannot
                        # act) and never an advance.
                        blocked = self._portal_validation_questions(node)
                        if blocked is not None:
                            return blocked
                        return {"status": "failed",
                                "reason": FORM_INCOMPLETE_REASON}
                if code == "TIMEOUT":
                    # A timeout on an irreversible action is genuine uncertainty.
                    if node.get("irreversibility") == "irreversible":
                        return {"status": "uncertain",
                                "detail": {"reason": "no response to an irreversible action"}}
                    # A reversible advance the portal refuses often means the
                    # FORM is objecting, not the network. First try to REPAIR
                    # it silently: a field the portal dropped (a dependent
                    # select can reset one filled earlier) is refilled from
                    # the answer the applicant already gave — asking again for
                    # a value Ellis holds is the loop, not the fix. Only an
                    # unrepairable complaint becomes an applicant question.
                    if not self._repair_attempted:
                        self._repair_attempted = True
                        if self._repair_flagged_fields():
                            res = self.driver.click(node["selector"])
                            if res.get("ok"):
                                break
                    blocked = self._portal_validation_questions(node)
                    if blocked is not None:
                        return blocked
                    # No complaint from the portal: either the page ALREADY
                    # advanced past this step (a repeat attempt then finds no
                    # button and "times out"), or an overlay is intercepting
                    # the click. Check the first, then try the second.
                    if self._advanced_past(node):
                        return {"status": "ok", "detail": {"already_advanced": True}}
                    forcer = getattr(self.driver, "force_click", None)
                    if forcer is not None:
                        if (forcer(node["selector"]) or {}).get("ok"):
                            break
                    return {"status": "failed", "reason": "timeout"}
                if code == "FEE_CHANGED":
                    return {"status": "failed", "reason": "fee changed — new confirmation required"}
                if code == "SLOT_GONE" and attempt < attempts - 1:
                    # No booking occurred (reversible): re-read inventory and
                    # retry against the next available slot (§24 race handling).
                    self.slots_seen = getattr(self.driver, "slots", [])
                    continue
                if code in ("SLOT_GONE", "NO_SLOTS"):
                    return {"status": "failed", "reason": f"appointment: {code}"}
                if attempt < attempts - 1:
                    continue    # bounded retry for other transient codes
                return {"status": "failed", "reason": f"{node['node_id']}: {code}"}
            if node.get("success_evidence"):
                if not self._verify_evidence(node):
                    # A success banner without evidence is NOT success (§20).
                    if node.get("irreversibility") == "irreversible":
                        return {"status": "uncertain",
                                "detail": {"reason": "no qualifying evidence after irreversible action"}}
                    return {"status": "failed", "reason": "expected evidence missing"}
            elif node.get("irreversibility") != "irreversible":
                # A CLICK can land perfectly and still not advance: the form
                # rejects it and renders its own field errors. Clicking is not
                # progressing — verify, repair once, and otherwise ask.
                refused = self._advance_refused(node)
                if refused is not None:
                    return refused
            return {"status": "ok"}
        if action in ("FILL_NON_SENSITIVE", "SELECT_SEARCH"):
            value = self.answers.get(node.get("input_source", ""), "")
            if value in ("", None):
                if not bool(node.get("mandatory", True)):
                    return {"status": "ok", "detail": {"skipped_optional": True}}
                # Ellis never guesses (§21) — but a missing answer is not a
                # failure either: it becomes an applicant question and the flow
                # pauses HERE, resumable from this exact node once answered.
                return {"status": "handoff",
                        "handoff_kind": "additional_information"}
            # A node may declare the PORTAL's exact date format (tokens
            # DD/MM/YYYY/MON/MONTH/YY): the canonical ISO value is rendered in
            # that format — never the UI display format, never a guess (a
            # declared format with a non-canonical value fails the step).
            fmt = node.get("format")
            if fmt:
                from .. import dates as dates_mod
                formatted = dates_mod.to_portal(str(value), fmt)
                if not formatted:
                    return {"status": "failed",
                            "reason": f"answer {node.get('input_source')!r} is not a "
                                      "canonical date — refusing to guess the portal format"}
                value = formatted
            # Resume speed: a field the portal ALREADY holds with this exact
            # value needs no work. Re-typing every field on every resume is
            # what made a repeat pass take minutes.
            reader_v = getattr(self.driver, "read_value", None)
            if reader_v is not None:
                try:
                    current = str((reader_v(node["selector"]) or {}).get("value", "")).strip()
                except Exception:  # noqa: BLE001
                    current = ""
                if current and current.lower() == str(value).strip().lower():
                    return {"status": "ok", "detail": {"already_set": True}}
            if action == "SELECT_SEARCH":
                sel = getattr(self.driver, "select_search", None)
                res = sel(node["selector"], str(value)) if sel else \
                    self.driver.fill(node["selector"], str(value))
                if sel and not res.get("ok") and res.get("code") == "NO_OPTIONS":
                    # Portals list countries by display name; case answers may
                    # hold the ISO code. A failed ISO-shaped query retries ONCE
                    # with the registry's canonical name — a deterministic
                    # lookup, never a guess.
                    display = _country_display_name(str(value))
                    if display and display.lower() != str(value).lower():
                        res = sel(node["selector"], display)
                    if not res.get("ok") and res.get("code") == "NO_OPTIONS":
                        # The portal offers a fixed list this answer isn't on.
                        # Ellis never picks a near-miss — it asks the applicant
                        # to choose from the portal's OWN options.
                        opts = res.get("options") or []
                        if not opts:
                            # The typed query filtered EVERYTHING out (or the
                            # list loaded late): read the full unfiltered list
                            # — those are the real choices to show the
                            # applicant, not a dead-end failure.
                            lister = getattr(self.driver, "list_options", None)
                            if lister is not None:
                                try:
                                    listed = lister(node["selector"]) or {}
                                    opts = [str(o) for o in (listed.get("options") or [])
                                            if str(o).strip()]
                                    self._remember_options(
                                        node, opts, complete=bool(listed.get("complete")))
                                except Exception:  # noqa: BLE001
                                    opts = []
                        if opts:
                            self.observed_options[node["node_id"]] = opts
                            return {"status": "handoff",
                                    "handoff_kind": "additional_information"}
            else:
                res = self.driver.fill(node["selector"], str(value))
            if not res.get("ok") and res.get("code") == "SENSITIVE_FIELD_AUTOMATION":
                return {"status": "failed", "reason": "portal marked field sensitive — refusing"}
            return self._from_driver(node, res)
        if action == "SELECT_RADIO":
            opts = [o for o in (node.get("options") or []) if isinstance(o, dict)]
            labels = [str(o.get("label", "")).strip() for o in opts]
            labels = [t for t in labels if t]
            value = self.answers.get(node.get("input_source", ""), "")
            if value in ("", None):
                if not bool(node.get("mandatory", True)):
                    return {"status": "ok", "detail": {"skipped_optional": True}}
                # The group's own words ARE the choices — the applicant picks
                # from the portal's list, never a free-text guess.
                if labels:
                    self.observed_options[node["node_id"]] = labels
                return {"status": "handoff",
                        "handoff_kind": "additional_information"}
            picker = getattr(self.driver, "select_radio", None)
            if picker is None:
                return {"status": "failed",
                        "reason": "driver cannot answer a radio group"}
            res = picker(opts, str(value))
            if not res.get("ok") and res.get("code") == "NO_OPTIONS" and labels:
                # A fixed set the answer is not on: ask with the portal's real
                # choices rather than clicking the nearest-looking one.
                self.observed_options[node["node_id"]] = labels
                self._remember_options(node, labels, complete=True)
                return {"status": "handoff",
                        "handoff_kind": "additional_information"}
            return self._from_driver(node, res)
        if action == "SCROLL_TO_BOTTOM":
            scroll = getattr(self.driver, "scroll_bottom", None)
            if scroll is None:
                return {"status": "ok", "detail": {"noop": True}}
            return self._from_driver(node, scroll(node.get("selector", "")))
        if action == "CHECK":
            check = getattr(self.driver, "check", None)
            res = check(node["selector"]) if check else \
                self.driver.fill(node["selector"], node.get("input_source", "on"))
            return self._from_driver(node, res)
        if action == "SELECT":
            res = self.driver.fill(node["selector"], node.get("input_source", "on"))
            return self._from_driver(node, res)
        if action == "UPLOAD_AUTHORIZED_DOCUMENT":
            doc = self._document_for(node.get("doc_type", "passport"))
            if doc is None:
                # A missing document is an applicant action, not a dead end.
                return {"status": "handoff",
                        "handoff_kind": "additional_information"}
            upload = getattr(self.driver, "upload", None)
            if upload is None or not doc.get("path"):
                # No real upload capability on this driver (synthetic testing):
                # presence of the authorized document is the testable contract.
                return {"status": "ok", "detail": {"declared_only": True}}
            return self._from_driver(node, upload(node["selector"], doc["path"]))
        if action == "READ_TEXT":
            res = self.driver.read_text(node["selector"])
            return self._from_driver(node, res)
        if action == "READ_FEE":
            res = self.driver.read_text(node["selector"])
            if res.get("ok"):
                self.fee_seen = parse_fee_text(res.get("text", ""),
                                               currency_hint=node.get("currency_hint", ""))
                self._evidence(node, kind="dom_evidence", category="fee_read", strength=6)
                if self.fee_seen is None:
                    # A fee we cannot read exactly is a fee we never charge.
                    return {"status": "failed",
                            "reason": "displayed fee could not be read exactly"}
            return self._from_driver(node, res)
        if action == "READ_APPOINTMENT_INVENTORY":
            # Read the portal's live calendar through the driver's declared
            # selectors. A driver that exposes read_appointment_slots (the live
            # browser) reads the real DOM; a mock exposes a `.slots` list. Both
            # are read-only — observing availability reserves nothing.
            reader = getattr(self.driver, "read_appointment_slots", None)
            if reader is not None:
                res = reader(node)
                if not res.get("ok"):
                    return {"status": "failed",
                            "reason": f"{node['node_id']}: {res.get('code', 'slot read failed')}"}
                self.slots_seen = res.get("slots", [])
            else:
                self.slots_seen = getattr(self.driver, "slots", [])
            return {"status": "ok", "detail": {"slot_count": len(self.slots_seen)}}
        if action == "BOOK_APPOINTMENT":
            return self._book_appointment(node)
        if action == "WAIT_FOR_NETWORK":
            return {"status": "ok"}
        if action == "VERIFY_EVIDENCE":
            ok = self._verify_evidence(node)
            if ok and any((e or {}).get("category") == "session_authenticated"
                          for e in (node.get("success_evidence") or [])):
                # The account Ellis registered is now proven to sign in:
                # mark the applicant's stored portal account verified.
                from .. import models as core_models
                for acct in self.db.execute(select(core_models.PortalAccount).where(
                        core_models.PortalAccount.application_id
                        == self.execution.application_id)).scalars().all():
                    acct.verified = True
                self.db.commit()
            return {"status": "ok"} if ok else \
                {"status": "failed", "reason": "declared evidence not found"}
        if action == "REGISTER_ACCOUNT":
            return self._register_account(node)
        if action == "APPLICANT_HANDOFF":
            if node.get("handoff_kind") == "captcha":
                # Never send the applicant hunting for a CAPTCHA that is not
                # there. The flow DECLARES where one may appear; the page
                # decides whether one actually did.
                probe = getattr(self.driver, "captcha_state", None)
                if probe is not None:
                    try:
                        state = probe(True) or {}
                    except Exception:  # noqa: BLE001
                        state = {"ok": False}
                    if state.get("ok") and not state.get("present"):
                        # No challenge on this page: if the portal also
                        # refused to advance, its own complaint is the real
                        # blocker — surface that instead of a phantom CAPTCHA.
                        blocked = self._portal_validation_questions(node)
                        if blocked is not None:
                            return blocked
                        return {"status": "ok", "detail": {"no_captcha_present": True}}
            return {"status": "handoff"}
        if action == "RECONCILE_OUTCOME":
            self._official()
            return {"status": "ok"}
        if action == "PAUSE":
            return {"status": "handoff"}
        if action == "COMPLETE":
            return {"status": "ok"}
        return {"status": "failed", "reason": f"unknown action {action!r}"}

    # -- applicant questions / documents --------------------------------------
    def _question_for(self, node: dict) -> dict:
        """Applicant-friendly question for a node whose answer is missing.
        Never exposes selectors or developer terminology (§Part 4)."""
        q = dict(node.get("question") or {})
        observed = self.observed_options.get(node.get("node_id", ""))
        key = node.get("input_source", "") or q.get("key", "")
        label = q.get("question") or (node.get("label") or key.replace("_", " ")).strip()
        if node.get("action") == "UPLOAD_AUTHORIZED_DOCUMENT":
            dt = (node.get("doc_type") or "document").replace("_", " ")
            return {"key": f"document:{node.get('doc_type', 'document')}",
                    "question": q.get("question") or f"Please add your {dt} on the Documents tab.",
                    "why": q.get("why") or "The official application form requires this upload.",
                    "format": q.get("format", ""), "mandatory": True, "kind": "document"}
        nid = node.get("node_id", "")
        prev = str(self.answers.get(key, "") or "")
        why = q.get("why") or "The official application form requires this information."
        portal_msg = self._portal_field_messages.get(nid, "")
        if portal_msg:
            # The government form's own words — the honest reason for a re-ask.
            why = f"The official portal says: {portal_msg}"
        elif observed and prev:
            why = (f"The portal's list does not include “{prev}” — "
                   "please choose one of its real options.")
        return {"key": key,
                "question": label if label.endswith("?") else f"What is your {label}?"
                if not q.get("question") else q["question"],
                "why": why,
                "format": q.get("format") or ("DD/MM/YYYY" if node.get("format") else "free text"),
                "mandatory": bool(node.get("mandatory", True)),
                "kind": q.get("kind") or ("date" if node.get("format") else
                                          "select" if node.get("action") == "SELECT_SEARCH" else "text"),
                **({"previous_answer": prev} if prev else {}),
                **({"options": observed or q.get("options")}
                   if (observed or q.get("options")) else {})}

    @staticmethod
    def _option_matches(value: str, options: list) -> bool:
        """The same match rule select_search commits with: exact first, then
        substring. Used to pre-validate an ANSWERED select against the
        portal's real list."""
        v = str(value).strip().lower()
        if not v:
            return False
        lowered = [str(o).strip().lower() for o in options]
        return v in lowered or any(v in o or o in v for o in lowered)

    def _collect_missing_questions(self, from_node_id: str) -> list:
        """Every question the applicant must answer from this node onward, so
        they are asked ONCE instead of one field per portal round-trip.

        That includes selects whose stored answer the portal will reject: the
        real list is read now (a read-only widget open — the form is not
        modified) and a non-matching answer becomes a question with the
        portal's own options. A dependent list that is still empty (e.g. a
        ward before its province is set on the page) is NOT asked with an
        empty option list — it is deferred honestly until the portal can
        offer its choices."""
        out, seen, node_id = [], set(), from_node_id
        deferred = 0
        harvested = 0
        while node_id and node_id not in seen and len(out) < 40:
            seen.add(node_id)
            node = self.flow.nodes.get(node_id)
            if node is None:
                break
            act = node.get("action")
            if act in ("FILL_NON_SENSITIVE", "SELECT_SEARCH"):
                v = self.answers.get(node.get("input_source", ""), "")
                nid = node.get("node_id", "")
                rejected = nid in self.observed_options or nid in self._rejected_fills
                mandatory = bool(node.get("mandatory", True))
                if mandatory and act == "SELECT_SEARCH":
                    # Live option harvests are CAPPED per pause (same bound the
                    # page classifier uses): opening every remaining combobox
                    # delayed the applicant's prompt by seconds per list
                    # (measured ~13s on a real run, 2026-07-28). Within the
                    # cap, answered selects are still pre-validated so a value
                    # the portal will reject is re-asked in THIS batch.
                    if not rejected and harvested < 3:
                        self._harvest_options(node)
                        harvested += 1
                    opts = self.observed_options.get(nid) or []
                    declared = (node.get("question") or {}).get("options") or []
                    known = opts or declared
                    if v not in ("", None) and known and self._option_matches(v, known):
                        node_id = self.flow.next_of(node_id, "ok")
                        continue          # the portal will accept this answer
                    if v not in ("", None) and not known and not rejected:
                        # Past the harvest cap (or an unreadable list): the
                        # fill step verifies this answer when the run resumes;
                        # a genuine mismatch re-asks with the portal's list.
                        node_id = self.flow.next_of(node_id, "ok")
                        continue
                    if not known:
                        # No readable choices yet (dependent list, or past the
                        # cap). Asking with an empty dropdown would be a dead
                        # end; ask when the portal can offer its options.
                        deferred += 1
                        node_id = self.flow.next_of(node_id, "ok")
                        continue
                    out.append(self._question_for(node))
                elif (rejected or v in ("", None)) and mandatory:
                    out.append(self._question_for(node))
            elif act == "UPLOAD_AUTHORIZED_DOCUMENT":
                if self._document_for(node.get("doc_type", "passport")) is None:
                    out.append(self._question_for(node))
            elif act == "APPLICANT_HANDOFF":
                break   # never look past the next human checkpoint
            node_id = self.flow.next_of(node_id, "ok")
        # de-duplicate by key, preserving order
        uniq, keys = [], set()
        for q in out:
            if q["key"] not in keys:
                keys.add(q["key"])
                uniq.append(q)
        if deferred and uniq:
            uniq[-1] = dict(uniq[-1], deferred_followups=deferred)
        return uniq

    def _advance_refused(self, node: dict):
        """After a click that LANDED, did the portal actually accept it? If
        the page now shows its own field errors, the advance was refused:
        repair what Ellis can (values the form dropped) and retry once, then
        hand the portal's own complaints to the applicant. Returns an outcome
        when the advance was refused, else None."""
        reader = getattr(self.driver, "read_validation_errors", None)
        if reader is None:
            return None
        try:
            res = reader() or {}
        except Exception:  # noqa: BLE001
            return None
        if not (res.get("fields") or []):
            # Framework validation renders a beat AFTER the click: a clean
            # immediate read can hide a silent refusal (the click "lands",
            # the page stays, and the flow sails on believing it advanced).
            settle = getattr(self.driver, "settle", None)
            if settle is None:
                return None
            try:
                settle(700)
                res = reader() or {}
            except Exception:  # noqa: BLE001
                return None
            if not (res.get("fields") or []):
                return None
        if not self._repair_attempted:
            self._repair_attempted = True
            if self._repair_flagged_fields():
                retry = self.driver.click(node["selector"])
                if retry.get("ok"):
                    try:
                        again = reader() or {}
                    except Exception:  # noqa: BLE001
                        again = {}
                    if not (again.get("fields") or []):
                        return None      # accepted on the repaired retry
        blocked = self._portal_validation_questions(node)
        if blocked is not None:
            return blocked
        return {"status": "failed", "reason": "the portal did not accept this step"}

    def _advanced_past(self, node: dict) -> bool:
        """True when the portal has clearly moved beyond the page this node
        acts on: the form fields filled before it are no longer on screen.
        Prevents a step that ALREADY succeeded from being retried forever
        (the click 'times out' only because its button is gone)."""
        checker = getattr(self.driver, "is_visible", None)
        if checker is None:
            return False
        target = node.get("node_id")
        selectors: list[str] = []
        for nid in self.flow.order:
            if nid == target:
                break
            n = self.flow.nodes.get(nid) or {}
            if n.get("action") in ("FILL_NON_SENSITIVE", "SELECT_SEARCH") and n.get("selector"):
                selectors.append(n["selector"])
        if not selectors:
            return False
        # Sample the first and last preceding fields: both gone = new page.
        probes = [selectors[0], selectors[-1]] if len(selectors) > 1 else selectors
        for sel in probes:
            try:
                res = checker(sel) or {}
            except Exception:  # noqa: BLE001
                return False
            if not res.get("ok") or res.get("visible"):
                return False
        return True

    @staticmethod
    def _selector_targets_field(selector: str, fid: str) -> bool:
        """Does this node selector address the portal field the page flagged?
        The portal reports a field by its id or name; a node may address it as
        '#id', '[id="id"]', 'tag[name="name"]', with a ':visible' /
        '>> nth=' qualifier, or by a longer path ending in that id. Matching on
        the literal '#'+id alone silently loses every other form — and a lost
        match means the applicant is never asked about a field the government
        form is actively rejecting."""
        import re as _re
        sel = str(selector or "")
        fid = str(fid or "").strip()
        if not sel or not fid:
            return False
        esc = _re.escape(fid)
        return bool(
            _re.search(rf'#{esc}(?![\w-])', sel) or
            _re.search(rf'\[\s*id\s*=\s*["\']?{esc}["\']?\s*\]', sel) or
            _re.search(rf'\[\s*name\s*=\s*["\']?{esc}["\']?\s*\]', sel))

    def _node_for_flagged_field(self, fid: str):
        """The flow node that fills the portal-flagged field, or None."""
        if not fid:
            return None
        for n in self.flow.nodes.values():
            if n.get("action") not in ("FILL_NON_SENSITIVE", "SELECT_SEARCH",
                                       "SELECT", "CHECK"):
                continue
            if self._selector_targets_field(n.get("selector") or "", fid):
                return n
        return None

    def _repair_flagged_fields(self) -> int:
        """Refill every portal-flagged field whose answer Ellis already holds
        and whose value the portal has actually lost. Reversible fills only —
        never a select that would need a fresh choice, never an upload, never
        anything past a handoff. Returns how many fields were repaired."""
        reader = getattr(self.driver, "read_validation_errors", None)
        if reader is None:
            return 0
        try:
            res = reader() or {}
        except Exception:  # noqa: BLE001
            return 0
        fields = res.get("fields") or []
        if not fields:
            return 0
        repaired = 0
        for f in fields:
            fid = str(f.get("id") or "").strip()
            n = self._node_for_flagged_field(fid)
            if n is None or n.get("action") not in ("FILL_NON_SENSITIVE", "SELECT_SEARCH"):
                continue
            value = self.answers.get(n.get("input_source", ""), "")
            if value in ("", None):
                continue    # genuinely missing: the applicant must answer
            fmt = n.get("format")
            if fmt:
                from .. import dates as dates_mod
                value = dates_mod.to_portal(str(value), fmt) or ""
                if not value:
                    continue
            # Only repair what the portal actually LOST — a value still
            # present but rejected is a real question for the applicant.
            reader_v = getattr(self.driver, "read_value", None)
            if reader_v is not None:
                try:
                    cur = (reader_v(n["selector"]) or {}).get("value", "")
                except Exception:  # noqa: BLE001
                    cur = ""
                if str(cur).strip():
                    continue
            try:
                if n.get("action") == "SELECT_SEARCH":
                    sel = getattr(self.driver, "select_search", None)
                    out = sel(n["selector"], str(value)) if sel else {"ok": False}
                else:
                    out = self.driver.fill(n["selector"], str(value))
            except Exception:  # noqa: BLE001
                continue
            if out.get("ok"):
                repaired += 1
                self._checkpoint(n.get("node_id", ""), "repaired",
                                 {"reason": "portal dropped a filled value"})
        return repaired

    def _portal_validation_questions(self, node: dict):
        """When the government form itself flags fields (the portal's own
        validation messages are visible), those complaints become applicant
        questions with the portal's verbatim message — and the flow rewinds to
        the first flagged field so the resume refills it. Returns a handoff
        outcome, or None when the page shows no validation state."""
        reader = getattr(self.driver, "read_validation_errors", None)
        if reader is None:
            return None
        try:
            res = reader() or {}
        except Exception:  # noqa: BLE001
            return None
        fields = res.get("fields") or []
        if not fields:
            return None
        flagged: list[str] = []
        unmapped_msgs: list[str] = []
        for f in fields:
            fid = str(f.get("id") or "").strip()
            n = self._node_for_flagged_field(fid)
            if n is None:
                # A portal field the released flow has no node for (e.g. the
                # form's own inline legal declaration, or an expenses section)
                # — only the applicant can complete it, in the secure window.
                label = str(f.get("label") or "").strip()
                msg = str(f.get("message") or "").strip()
                text = " — ".join(x for x in (label, msg) if x)[:160]
                if text and text not in unmapped_msgs:
                    unmapped_msgs.append(text)
                continue
            nid = n.get("node_id", "")
            flagged.append(nid)
            msg = str(f.get("message") or "").strip()
            if msg:
                self._portal_field_messages[nid] = msg[:160]
            if n.get("action") == "SELECT_SEARCH":
                # drop any stale (possibly empty) harvest so the collector
                # re-reads the portal's current list for this field
                self.observed_options.pop(nid, None)
            else:
                self._rejected_fills.add(nid)
        if not flagged:
            if unmapped_msgs:
                return {"status": "handoff", "handoff_kind": "portal_form",
                        "portal_messages": unmapped_msgs[:8],
                        "detail": {"portal_unmapped_fields": len(unmapped_msgs)}}
            return None
        # Rewind to the first flagged field, but never across a handoff or an
        # irreversible node (only reversible refills may repeat).
        resume = None
        for nid in self.flow.order:
            n = self.flow.nodes.get(nid) or {}
            if nid == node.get("node_id"):
                break
            if n.get("action") in ("APPLICANT_HANDOFF", "PAUSE") or \
                    n.get("irreversibility") == "irreversible":
                resume = None    # cannot rewind past this point
                continue
            if resume is None and nid in flagged:
                resume = nid
        questions = self._collect_missing_questions(resume or node.get("node_id", ""))
        if not questions:
            if unmapped_msgs:
                return {"status": "handoff", "handoff_kind": "portal_form",
                        "portal_messages": unmapped_msgs[:8],
                        "detail": {"portal_unmapped_fields": len(unmapped_msgs)}}
            return None
        out = {"status": "handoff", "handoff_kind": "additional_information",
               "questions": questions, "resume_node": resume,
               "detail": {"portal_validation_fields": len(flagged)}}
        if unmapped_msgs:
            out["portal_messages"] = unmapped_msgs[:8]
        return out

    def _harvest_options(self, node: dict) -> None:
        """For a missing select answer, read the portal's REAL option list so
        the applicant chooses from actual choices — never a blank field and
        never a guess. Best-effort: a dependent list (e.g. ward before its
        province) may be empty now and re-harvests on the next pass."""
        nid = node.get("node_id", "")
        if not nid or nid in self.observed_options:
            return
        if (node.get("question") or {}).get("options"):
            return    # the released flow already declares the choices
        lister = getattr(self.driver, "list_options", None)
        if lister is None:
            return
        try:
            res = lister(node["selector"]) or {}
        except Exception:  # noqa: BLE001 — harvesting must never break a pause
            return
        opts = [str(o) for o in (res.get("options") or []) if str(o).strip()]
        if opts:
            self.observed_options[nid] = opts
            self._remember_options(node, opts, complete=bool(res.get("complete")))

    def _remember_options(self, node: dict, options: list,
                          complete: bool = False) -> None:
        """Cache what the portal actually offered, keyed to this adapter
        version, so the NEXT applicant is asked with a real dropdown before a
        browser session opens. Best-effort: caching must never break a pause,
        and the live page stays the authority (a value the portal no longer
        offers is caught by the fill step and re-asked with the real list)."""
        field_key = str(node.get("input_source") or "").strip()
        if not field_key:
            return
        try:
            from ..portal.released_flow import remember_field_options
            remember_field_options(
                self.db, candidate_id=self.execution.candidate_id,
                candidate_version=int(self.execution.candidate_version),
                field_key=field_key, node_id=str(node.get("node_id") or ""),
                options=options, complete=complete)
        except Exception:  # noqa: BLE001 — a cache write is never load-bearing
            pass

    def _document_for(self, doc_type: str):
        for d in self.documents:
            if (d.get("doc_type") or "") == doc_type:
                return d
        # A passport biodata-page image satisfies a generic 'passport' upload.
        if doc_type in ("passport", "passport_biodata"):
            for d in self.documents:
                if (d.get("doc_type") or "") in ("passport", "passport_biodata"):
                    return d
        return None

    # -- evidence (§20): sanitized network observation only -------------------
    def _verify_evidence(self, node: dict) -> bool:
        wanted = node.get("success_evidence") or []
        events = []
        try:
            events = self.driver.network_events()
        except Exception:  # noqa: BLE001
            pass
        official = None
        for rule in wanted:
            kind = rule.get("kind")
            cat = rule.get("category", "")
            if kind in ("network", "session_state"):
                for ev in events:
                    if 200 <= int(ev.get("status", 0)) < 300 and self._event_matches(rule, ev):
                        self._evidence(node, kind="network", category=cat, strength=1, event=ev)
                        return True
            if kind == "official_record":
                official = official if official is not None else self._official()
                if official.get(cat):
                    self._evidence(node, kind="official_record", category=cat, strength=2)
                    return True
        return False

    @staticmethod
    def _event_matches(rule: dict, ev: dict) -> bool:
        """Deterministic match of a sanitized network event against a declared
        rule. Pre-categorized events (synthetic testing) match on category;
        live events (category always '') match on the rule's declared
        url_substring and/or required response KEY NAMES — all static strings
        from the released bundle, never inferred."""
        cat = rule.get("category", "")
        if ev.get("category") and ev.get("category") == cat:
            return True
        sub = (rule.get("url_substring") or "").lower()
        need_keys = rule.get("response_key_names") or []
        if not sub and not need_keys:
            return False
        if sub and sub not in (ev.get("url") or "").lower():
            return False
        if need_keys:
            have = {str(k).lower() for k in (ev.get("response_keys") or [])}
            if not all(str(k).lower() in have for k in need_keys):
                return False
        return True

    # Case answer key the applicant's explicit slot choice is written under
    # when they pick a slot in the calendar UI. Booking NEVER proceeds without
    # it — Ellis never auto-reserves a government appointment.
    CHOSEN_SLOT_KEY = "chosen_appointment_slot_id"

    def _book_appointment(self, node: dict) -> dict:
        """Book the applicant's EXPLICITLY CHOSEN slot. Reconcile-first (never
        double-book) and never auto-pick: without a stored choice the flow
        pauses to the calendar handoff. Success is proven by the flow's
        VERIFY_EVIDENCE node, not by this click."""
        # 1. Reconcile: if the portal already shows a booked appointment for
        #    this applicant, use it — never create a second reservation.
        if self._already_done(node, self._official()):
            self._evidence(node, kind="official_record",
                           category="reconciled_prior_success", strength=2)
            return {"status": "ok", "detail": {"reconciled": True}}
        # 2. The applicant's own choice is required. No choice -> pause and ask
        #    (the handoff carries the slots the runner just read).
        chosen = str(self.answers.get(self.CHOSEN_SLOT_KEY) or "").strip()
        if not chosen:
            return {"status": "handoff", "handoff_kind": "appointment_selection",
                    "detail": {"slots": self.slots_seen}}
        # 3. The chosen slot must be one Ellis actually observed as bookable —
        #    never book a handle the applicant did not pick from real inventory.
        if not any(str(s.get("slotId")) == chosen for s in (self.slots_seen or [])):
            return {"status": "handoff", "handoff_kind": "appointment_selection",
                    "detail": {"slots": self.slots_seen,
                               "reason": "chosen slot is no longer available"}}
        booker = getattr(self.driver, "book_appointment_slot", None)
        if booker is None:
            return {"status": "failed",
                    "reason": "driver cannot book appointments in this runtime"}
        res = booker(node, chosen)
        if not res.get("ok"):
            code = res.get("code", "BOOK_FAILED")
            if code == "SLOT_GONE":
                # Someone took it first — re-ask with fresh inventory next pass.
                return {"status": "handoff", "handoff_kind": "appointment_selection",
                        "detail": {"reason": "slot_gone", "slots": self.slots_seen}}
            return {"status": "failed", "reason": f"appointment booking: {code}"}
        return {"status": "ok", "detail": {"booked_slot": chosen}}

    def _register_account(self, node: dict) -> dict:
        """Create the applicant's portal account: their OWN email plus a FRESH
        password Ellis generates and vaults. Reconcile-first — an already
        authenticated session is used, never a second account. The emailed
        verification code is NEVER read by Ellis: the flow's next node is the
        applicant's OTP handoff. The password is stored encrypted and never
        logged; only the applicant's email is a case value."""
        from .. import vault
        # 1. Reconcile: an existing authenticated session means the account
        # already exists — use it, never register again.
        session_probe = getattr(self.driver, "session_authenticated", None)
        if session_probe is not None:
            try:
                if session_probe():
                    self._evidence(node, kind="session_state",
                                   category="session_authenticated", strength=2)
                    return {"status": "ok", "detail": {"reconciled_existing": True}}
            except Exception:  # noqa: BLE001
                pass
        # 2. The applicant's own email is required; without it, ask.
        email = str(self.answers.get("email") or "").strip()
        if not email:
            return {"status": "handoff", "handoff_kind": "additional_information",
                    "detail": {"need": "email"}}
        # 3. Generate + vault a fresh password bound to this case.
        password = vault.generate_password({"minLength": 16})
        stored = vault.store(password, meta={"purpose": "portal_account",
                                             "application_id": self.execution.application_id,
                                             "candidate_id": self.execution.candidate_id})
        register = getattr(self.driver, "register_account", None)
        if register is None:
            return {"status": "failed",
                    "reason": "driver cannot create accounts in this runtime"}
        try:
            res = register(email=email, password=password,
                           email_selector=node.get("email_selector", ""),
                           password_selector=node.get("password_selector", ""),
                           confirm_selector=node.get("confirm_password_selector", ""),
                           submit_selector=node.get("submit_selector", ""))
        finally:
            password = None    # drop the plaintext promptly; vault holds it
        if not res or not res.get("ok"):
            code = (res or {}).get("code", "REGISTER_FAILED")
            return {"status": "failed", "reason": f"account registration: {code}"}
        # Remember the vault ref on the case so later steps (and the applicant's
        # own record) can reveal the credential; never the plaintext.
        self.execution.error = ""      # not an error state
        self._account_password_ref = stored["ref"]
        # The account belongs to the APPLICANT: persist it on their case so
        # Ellis can show them their portal sign-in (email + revealed password)
        # any time after this run — the portal account outlives the flow.
        from .. import models as core_models
        adapter_id = (self.flow.manifest or {}).get("adapter_id", "")
        acct = self.db.execute(select(core_models.PortalAccount).where(
            core_models.PortalAccount.application_id == self.execution.application_id,
            core_models.PortalAccount.adapter_id == adapter_id)).scalars().first()
        if acct is None:
            acct = core_models.PortalAccount(
                application_id=self.execution.application_id,
                adapter_id=adapter_id, username=email,
                credential_ref=stored["ref"])
            self.db.add(acct)
        else:
            acct.username = email
            acct.credential_ref = stored["ref"]
        self.db.commit()
        self._evidence(node, kind="network",
                       category="account_registration_submitted", strength=3,
                       event=res.get("evidence") or {})
        return {"status": "ok",
                "detail": {"account_email": email, "password_vault_ref": stored["ref"]}}

    def _evidence(self, node, *, kind, category, strength, event=None):
        ev = event or {}
        url = ev.get("url", "")
        parsed = urlparse(url) if url else None
        self.db.add(fm.AdapterOutcomeEvidence(
            execution_id=self.execution.id, node_id=node["node_id"], strength=strength,
            kind=kind, hostname=(parsed.netloc if parsed else ""),
            endpoint_pattern=(parsed.path if parsed else ""),
            method=ev.get("method", ""), status_code=int(ev.get("status", 0) or 0),
            content_type=ev.get("content_type", ""),
            response_keys=list(ev.get("response_keys", [])),
            state_category=category))
        self.db.commit()

    def _official(self) -> dict:
        try:
            return self.driver.official_state() or {"known": False}
        except Exception:  # noqa: BLE001
            return {"known": False}

    def _already_done(self, node: dict, official: dict) -> bool:
        if not official.get("known"):
            return False
        cat = ""
        for rule in node.get("success_evidence") or []:
            cat = rule.get("category", "")
        return bool(official.get("submitted") and "submission" in cat) or \
            bool(official.get("booked") and "appointment" in cat) or \
            bool(official.get("paid") and "payment" in cat)

    # -- plumbing --------------------------------------------------------------
    def _from_driver(self, node: dict, res: dict) -> dict:
        if res.get("ok"):
            return {"status": "ok"}
        detail = res.get("detail", "")
        return {"status": "failed",
                "reason": f"{node['node_id']}: {res.get('code', 'driver error')}"
                          + (f" ({detail})" if detail else "")}

    def _url_ok(self, url: str) -> bool:
        return self.flow.host_allowed(urlparse(url).netloc)

    def _checkpoint(self, node_id: str, status: str, detail: dict):
        safe = {k: v for k, v in (detail or {}).items()
                if isinstance(v, (int, bool)) or (isinstance(v, str) and len(v) < 200)}
        self.db.add(fm.AdapterCheckpoint(execution_id=self.execution.id,
                                         node_id=node_id, status=status, detail=safe))
        self.db.commit()

    def _finish(self, status: str, note: str) -> dict:
        self.execution.status = status
        self.db.commit()
        self._audit("adapter_execution_finished", {"status": status, "note": note[:120]})
        return {"status": status}

    def _fail_closed(self, node_id, reason: str) -> dict:
        self.execution.status = "failed"
        self.execution.error = reason[:400]
        self.execution.current_node = node_id or ""
        self.db.commit()
        self.db.add(fm.AdapterFailure(candidate_id=self.execution.candidate_id,
                                      candidate_version=self.execution.candidate_version,
                                      execution_id=self.execution.id,
                                      node_id=node_id or "", failure_class="fail_closed",
                                      sanitized_detail={"reason": reason[:300]}))
        self.db.commit()
        self._audit("adapter_execution_failed_closed", {"node": node_id, "reason": reason[:120]})
        return {"status": "failed", "node": node_id, "reason": reason}

    def _audit(self, action, detail):
        audit.record(self.db, org_id=self.execution.org_id,
                     application_id=self.execution.application_id,
                     action=action, detail=detail, actor="ellis")


def start_execution(db, *, org_id: str, application_id: str, route_key: str,
                    tier: str, driver, case_answers: dict | None = None,
                    documents: list | None = None,
                    resume_execution_id: str | None = None) -> tuple[fm.AdapterExecution, FlowRunner]:
    """The ONLY way to run an adapter: resolves the active released binding for
    (route, tier) and refuses anything else — unreleased candidates, other
    routes, engaged kill switches (§35.15-17)."""
    binding = active_binding(db, route_key=route_key, tier=tier)
    if binding is None:
        raise RuntimeRefused(
            f"no released adapter is bound for this exact route at tier {tier!r} — "
            "execution refused (fail closed)")
    version_row = db.execute(select(fm.AdapterCandidateVersion).where(
        fm.AdapterCandidateVersion.candidate_id == binding.candidate_id,
        fm.AdapterCandidateVersion.version == binding.candidate_version)).scalar_one_or_none()
    if version_row is None or version_row.quarantined:
        raise RuntimeRefused("bound version missing or quarantined — execution refused")
    if kill_engaged(db, binding.candidate_id):
        raise RuntimeRefused("kill switch engaged — execution refused")
    if (version_row.manifest or {}).get("route_key") != route_key:
        raise RuntimeRefused("route-scope mismatch — execution refused")
    compiled = compile_flow(version_row)
    if resume_execution_id:
        execution = db.get(fm.AdapterExecution, resume_execution_id)
        if execution is None:
            raise RuntimeRefused("execution to resume not found")
        execution.status = "running"
    else:
        execution = fm.AdapterExecution(
            org_id=org_id, application_id=application_id,
            candidate_id=binding.candidate_id, candidate_version=binding.candidate_version,
            tier=tier, status="running")
        db.add(execution)
    db.commit()
    runner = FlowRunner(db, execution=execution, compiled=compiled, driver=driver,
                        case_answers=case_answers, documents=documents)
    return execution, runner


def _required_capabilities(compiled) -> set:
    """The irreversible capabilities a compiled flow would exercise, from its
    handoff kinds and irreversible-node success evidence.

    A payment HANDOFF is the applicant paying personally in their own secure
    window — Ellis performs no payment action, so it needs no capability. The
    payment_preparation capability governs the path where ELLIS acts: reading
    the official fee (READ_FEE) for exact-amount confirmation and autofilling.
    Requiring it for a bare handoff refused submission on every
    applicant-pays-personally route (Vietnam, UK, Ethiopia, Angola) at the
    applicant's final step, because the capability gate needs a READ_FEE node
    those flows correctly do not have."""
    caps: set[str] = set()
    reads_fee = any(n.get("action") == "READ_FEE" for n in compiled.nodes.values())
    for n in compiled.nodes.values():
        if n.get("action") == "APPLICANT_HANDOFF":
            if n.get("handoff_kind") == "credentials":
                caps.add("account_registration")
            if n.get("handoff_kind") == "payment_credentials" and reads_fee:
                caps.add("payment_preparation")
        if n.get("irreversibility") == "irreversible":
            for e in (n.get("success_evidence") or []):
                if e.get("category") == "appointment_booked":
                    caps.add("appointment_booking")
                if e.get("category") == "submission_accepted":
                    caps.add("submission_execution")
    return caps


def _standing_auth_covers(db, application_id: str, action: str) -> bool:
    from ..authorization import valid as auth_valid
    row = auth_valid(db, application_id)
    return bool(row and action in (row.permitted_actions or []))


def _submission_preconditions(db, application_id: str) -> tuple[bool, str]:
    """Submission requires a CURRENT signed final review (material changes
    invalidate it) and a confirmed exact-amount payment."""
    from .. import models as app_models, final_review, payments  # noqa: F401
    app_row = db.get(app_models.VisaApplication, application_id)
    if app_row is None:
        return False, "application not found for submission preconditions"
    try:
        final_review.check_and_invalidate(db, app_row)   # invalidate on material change
    except Exception:  # noqa: BLE001
        pass
    rv = final_review.latest(db, application_id)
    if rv is None or not rv.signed or getattr(rv, "invalidated", False):
        return False, "no current signed final review"
    rows = db.execute(select(app_models.PaymentAuthorization).where(
        app_models.PaymentAuthorization.application_id == application_id,
        app_models.PaymentAuthorization.status.in_(("authorized", "consumed")))).scalars().all()
    if not rows:
        return False, "exact-amount payment not confirmed"
    return True, ""


def assert_execution_allowed(db, *, route_key: str, application_id: str, compiled) -> None:
    """Fail-closed runtime gate for automatic secure execution. For every
    irreversible capability the flow would exercise, require (a) the capability
    is auto-released, (b) the case's standing authorization covers its action,
    and — for submission — a current signed final review + confirmed exact-amount
    payment. Applicant handoffs (CAPTCHA/OTP/identity/declaration/payment) are
    enforced by the flow itself. NO administrator is ever consulted."""
    from . import auto_release
    required = _required_capabilities(compiled)
    blockers: list[str] = []
    for cap in sorted(required):
        if auto_release.capability_released(db, route_key=route_key, capability=cap) is None:
            blockers.append(f"capability '{cap}' not released")
            continue
        action = auto_release.CAPABILITY_ACTIONS[cap]
        if not _standing_auth_covers(db, application_id, action):
            blockers.append(f"standing authorization does not cover '{cap}'")
        if cap == "submission_execution":
            ok, why = _submission_preconditions(db, application_id)
            if not ok:
                blockers.append(f"submission blocked ({why})")
    if blockers:
        raise RuntimeRefused("automatic execution refused: " + "; ".join(blockers))


def execute_released_route_live(db, *, org_id: str, application_id: str,
                                route_key: str, tier: str = "sandbox",
                                case_answers: dict | None = None,
                                documents: list | None = None) -> dict:
    """Automatic secure execution of the RELEASED adapter for a route through a
    live Browserbase session (real modes) — the runner entry the continuous
    research → build → release chain hands over to.

    All start_execution guards apply unchanged (released binding for the exact
    route+tier, no quarantine, no kill switch). The driver is the credential-
    isolated BrowserbasePageDriver on one isolated session; the runner executes
    the typed flow until completion or the first APPLICANT_HANDOFF (CAPTCHA/
    OTP/identity/declaration/payment stay personal), and the session is always
    released. Kimi is never on this path."""
    from ..portal.live_browser import LiveBrowserSession
    from .live_driver import BrowserbasePageDriver

    binding = active_binding(db, route_key=route_key, tier=tier)
    if binding is None:
        raise RuntimeRefused(
            f"no released adapter is bound for this exact route at tier {tier!r} — "
            "execution refused (fail closed)")
    version_row = db.execute(select(fm.AdapterCandidateVersion).where(
        fm.AdapterCandidateVersion.candidate_id == binding.candidate_id,
        fm.AdapterCandidateVersion.version == binding.candidate_version)).scalar_one_or_none()
    hosts = ((version_row.manifest or {}).get("allowed_hostnames")
             if version_row else None) or []
    # Fail-closed capability + authorization + signature/payment gate BEFORE any
    # live session is opened. An unreleased capability, an uncovered standing
    # authorization, or an unsigned/unpaid submission stops here honestly.
    if version_row is not None:
        assert_execution_allowed(db, route_key=route_key,
                                 application_id=application_id,
                                 compiled=compile_flow(version_row))
    session = LiveBrowserSession(allowed_hostnames=hosts)
    try:
        page = session._ensure_page()
        driver = BrowserbasePageDriver(page, allowed_hostnames=hosts)
        execution, runner = start_execution(
            db, org_id=org_id, application_id=application_id, route_key=route_key,
            tier=tier, driver=driver, case_answers=case_answers, documents=documents)
        result = runner.run()
        return {"execution_id": execution.id, "result": result,
                "candidate_version": binding.candidate_version, "tier": tier}
    finally:
        session.close()
