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


class FlowRunner:
    def __init__(self, db, *, execution: fm.AdapterExecution, compiled: CompiledFlow,
                 driver, case_answers: dict | None = None, documents: list | None = None):
        self.db = db
        self.execution = execution
        self.flow = compiled
        self.driver = driver
        self.answers = case_answers or {}
        self.documents = documents or []
        self.fee_seen = None
        self.slots_seen = []

    # -- entry ---------------------------------------------------------------
    def run(self, *, resume_from: str | None = None, max_nodes: int = 200) -> dict:
        node_id = resume_from or self.execution.current_node or self.flow.first()
        for _ in range(max_nodes):
            if node_id is None:
                return self._finish("completed", "flow exhausted")
            node = self.flow.nodes.get(node_id)
            if node is None:
                return self._fail_closed(node_id, "unknown node — failing closed")
            # Kill switch before EVERY stage (§19).
            if kill_engaged(self.db, self.execution.candidate_id):
                self.execution.status = "killed"
                self.db.commit()
                self._audit("adapter_execution_killed", {"node": node_id})
                return {"status": "killed", "node": node_id}
            outcome = self._step(node)
            self._checkpoint(node_id, outcome["status"], outcome.get("detail", {}))
            if outcome["status"] == "handoff":
                self.execution.status = "paused_applicant_action"
                self.execution.current_node = node_id
                self.db.commit()
                return {"status": "paused_applicant_action", "node": node_id,
                        "handoff_kind": node.get("handoff_kind", "")}
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
        if action == "FILL_NON_SENSITIVE":
            value = self.answers.get(node.get("input_source", ""), "")
            if value in ("", None):
                return {"status": "failed",
                        "reason": f"missing case answer {node.get('input_source')!r} — "
                                  "Ellis never guesses (§21)"}
            res = self.driver.fill(node["selector"], str(value))
            if not res.get("ok") and res.get("code") == "SENSITIVE_FIELD_AUTOMATION":
                return {"status": "failed", "reason": "portal marked field sensitive — refusing"}
            return self._from_driver(node, res)
        if action in ("SELECT", "CHECK"):
            res = self.driver.fill(node["selector"], node.get("input_source", "on"))
            return self._from_driver(node, res)
        if action == "UPLOAD_AUTHORIZED_DOCUMENT":
            if not self.documents:
                return {"status": "failed", "reason": "no authorized document available"}
            return {"status": "ok"}
        if action == "READ_TEXT":
            res = self.driver.read_text(node["selector"])
            return self._from_driver(node, res)
        if action == "READ_FEE":
            res = self.driver.read_text(node["selector"])
            if res.get("ok"):
                self.fee_seen = res.get("text", "")
                self._evidence(node, kind="dom_evidence", category="fee_read", strength=6)
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
                    if ev.get("category") == cat and 200 <= int(ev.get("status", 0)) < 300:
                        self._evidence(node, kind="network", category=cat, strength=1, event=ev)
                        return True
            if kind == "official_record":
                official = official if official is not None else self._official()
                if official.get(cat):
                    self._evidence(node, kind="official_record", category=cat, strength=2)
                    return True
        return False

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
        return {"status": "failed",
                "reason": f"{node['node_id']}: {res.get('code', 'driver error')}"}

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
