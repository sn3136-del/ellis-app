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

from urllib.parse import urlparse

from sqlalchemy import select

from .. import audit
from . import models as fm
from .compiler import CompiledFlow, compile_flow
from .release import active_binding, kill_engaged


class RuntimeRefused(Exception):
    """Fail-closed refusal: unreleased version, kill switch, scope breach."""


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
        # Optional applicant-safe progress recorder: called with the node's
        # semantic step (never a selector) before and after each node.
        self.on_progress = on_progress

    def _progress(self, node: dict, status: str):
        if self.on_progress is None:
            return
        try:
            from .. import progress as progress_vocab
            self.on_progress(progress_vocab.step_for_node(node), status)
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
                self.execution.current_node = node_id
                self.db.commit()
                result = {"status": "paused_applicant_action", "node": node_id,
                          "handoff_kind": outcome.get("handoff_kind")
                          or node.get("handoff_kind", "")}
                # Missing-answer pauses carry applicant-friendly questions —
                # ALL still-missing fields at once, so the applicant is asked
                # one time instead of field by field.
                if result["handoff_kind"] == "additional_information":
                    result["questions"] = self._collect_missing_questions(node_id)
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
            return self._from_driver(node, res)
        if action == "WAIT_FOR_STATE":
            return {"status": "ok"}
        if action == "CLICK":
            attempts = int(node.get("max_retries", 0)) + 1
            for attempt in range(attempts):
                if node.get("irreversibility") == "irreversible":
                    # Reconcile-before-act, EVERY attempt: never repeat an
                    # irreversible action that already succeeded.
                    if self._already_done(node, self._official()):
                        self._evidence(node, kind="official_record",
                                       category="reconciled_prior_success", strength=2)
                        return {"status": "ok", "detail": {"reconciled": True}}
                res = self.driver.click(node["selector"])
                if res.get("ok"):
                    break
                code = res.get("code")
                if code == "TIMEOUT":
                    # A timeout on an irreversible action is genuine uncertainty.
                    return {"status": "uncertain",
                            "detail": {"reason": "no response to an irreversible action"}} \
                        if node.get("irreversibility") == "irreversible" else \
                        {"status": "failed", "reason": "timeout"}
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
                        if opts:
                            self.observed_options[node["node_id"]] = opts
                            return {"status": "handoff",
                                    "handoff_kind": "additional_information"}
            else:
                res = self.driver.fill(node["selector"], str(value))
            if not res.get("ok") and res.get("code") == "SENSITIVE_FIELD_AUTOMATION":
                return {"status": "failed", "reason": "portal marked field sensitive — refusing"}
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
            self.slots_seen = getattr(self.driver, "slots", [])
            return {"status": "ok", "detail": {"slot_count": len(self.slots_seen)}}
        if action == "WAIT_FOR_NETWORK":
            return {"status": "ok"}
        if action == "VERIFY_EVIDENCE":
            ok = self._verify_evidence(node)
            return {"status": "ok"} if ok else \
                {"status": "failed", "reason": "declared evidence not found"}
        if action == "APPLICANT_HANDOFF":
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
        return {"key": key,
                "question": label if label.endswith("?") else f"What is your {label}?"
                if not q.get("question") else q["question"],
                "why": q.get("why") or "The official application form requires this information.",
                "format": q.get("format") or ("DD/MM/YYYY" if node.get("format") else "free text"),
                "mandatory": bool(node.get("mandatory", True)),
                "kind": q.get("kind") or ("date" if node.get("format") else
                                          "select" if node.get("action") == "SELECT_SEARCH" else "text"),
                **({"options": observed or q.get("options")}
                   if (observed or q.get("options")) else {})}

    def _collect_missing_questions(self, from_node_id: str) -> list:
        """All still-unanswered mandatory questions from this node onward, so
        the applicant is asked once, not one field at a time."""
        out, seen, node_id = [], set(), from_node_id
        while node_id and node_id not in seen and len(out) < 40:
            seen.add(node_id)
            node = self.flow.nodes.get(node_id)
            if node is None:
                break
            act = node.get("action")
            if act in ("FILL_NON_SENSITIVE", "SELECT_SEARCH"):
                v = self.answers.get(node.get("input_source", ""), "")
                rejected = node.get("node_id", "") in self.observed_options
                if (rejected or v in ("", None)) and bool(node.get("mandatory", True)):
                    if act == "SELECT_SEARCH":
                        self._harvest_options(node)
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
        return uniq

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
    handoff kinds and irreversible-node success evidence."""
    caps: set[str] = set()
    for n in compiled.nodes.values():
        if n.get("action") == "APPLICANT_HANDOFF":
            if n.get("handoff_kind") == "credentials":
                caps.add("account_registration")
            if n.get("handoff_kind") == "payment_credentials":
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
