"""Typed adapter specification (brief §16): the deterministic flow contract.

A flow is an ordered list of action NODES. Each node fully declares what it
does, where it may do it, what proves success, and how failures recover.
Nothing outside this schema can execute: the runtime compiles the flow and
refuses anything unknown (fail closed).
"""
from __future__ import annotations

import re

# Deterministic action types (§16). Anything else is rejected at compile time.
ACTION_TYPES = [
    "NAVIGATE", "WAIT_FOR_STATE", "CLICK", "FILL_NON_SENSITIVE", "SELECT",
    "CHECK", "UPLOAD_AUTHORIZED_DOCUMENT", "READ_TEXT", "READ_FEE",
    "READ_APPOINTMENT_INVENTORY", "WAIT_FOR_NETWORK", "VERIFY_EVIDENCE",
    "APPLICANT_HANDOFF", "RECONCILE_OUTCOME", "PAUSE", "COMPLETE",
]

# Steps that must ALWAYS be an applicant handoff, never automated (§16).
SENSITIVE_HANDOFF_KINDS = [
    "credentials", "otp", "captcha", "passkey", "payment_credentials",
    "portal_native_signature", "legally_personal_declaration",
    "representative_prohibited_action",
]

# Field-name patterns whose values must never be automated by FILL_NON_SENSITIVE.
# Word-boundaried so legitimate case fields like "passport_number" are NOT
# mistaken for "password"; passwords/OTP/card/CAPTCHA/PIN remain sensitive.
_SENSITIVE_FIELD_RE = re.compile(
    r"(password|passcode|\botp\b|one_?time|\bcvv\b|\bcvc\b|\bcard\b|card_?number|"
    r"\bpan\b|secret|\btoken\b|captcha|\bpin\b|3ds|declaration_sign|passkey)",
    re.IGNORECASE)

RETRY_CLASSES = ["none", "idempotent", "reconcile_first"]
IRREVERSIBILITY = ["reversible", "conditionally_reversible", "irreversible"]

NODE_DEFAULTS = {
    "purpose": "",
    "expected_state": "",
    "allowed_url_patterns": [],
    "selector": "",
    "fallback_selectors": [],
    "preconditions": [],
    "input_source": "",
    "sensitive": False,
    "applicant_action": False,
    "handoff_kind": "",
    "expected_network": [],
    "expected_transition": "",
    "success_evidence": [],
    "failure_evidence": [],
    "timeout_ms": 20000,
    "retry_class": "none",
    "max_retries": 0,
    "idempotency": "not_idempotent",
    "recovery_checkpoint": "",
    "irreversibility": "reversible",
    "screenshot_policy": "never",
    "recording_policy": "never",
    "logging_policy": "redacted",
    "redaction_policy": "strict",
    "next": [],
}

_REQUIRED = ("node_id", "action", "allowed_hostname")

_SELECTOR_OK = ("#", ".", "[", "input", "select", "textarea", "button", "form",
                "a", "label", "iframe", "main")

_NODE_ID_RE = re.compile(r"^[a-z0-9_\-]{2,120}$")


def normalize_node(raw: dict) -> dict:
    node = dict(NODE_DEFAULTS)
    node.update(raw or {})
    return node


def validate_node(raw: dict, *, allowed_hostnames: list[str]) -> list[str]:
    errs = []
    node = normalize_node(raw)
    for f in _REQUIRED:
        if not node.get(f):
            errs.append(f"node missing required field: {f}")
    nid = str(node.get("node_id", ""))
    if nid and not _NODE_ID_RE.match(nid):
        errs.append(f"invalid node_id {nid!r}")
    action = node.get("action")
    if action not in ACTION_TYPES:
        errs.append(f"{nid}: unknown action type {action!r}")
    host = (node.get("allowed_hostname") or "").lower()
    allow = [h.lower() for h in allowed_hostnames]
    if host and not any(host == a or host.endswith("." + a) for a in allow):
        errs.append(f"{nid}: hostname {host!r} outside the adapter allowlist")
    if action in ("CLICK", "FILL_NON_SENSITIVE", "SELECT", "CHECK", "READ_TEXT",
                  "READ_FEE", "UPLOAD_AUTHORIZED_DOCUMENT"):
        sel = (node.get("selector") or "").strip()
        if not sel:
            errs.append(f"{nid}: {action} requires a deterministic selector")
        elif not sel.startswith(_SELECTOR_OK):
            errs.append(f"{nid}: selector {sel!r} is not a deterministic CSS selector")
    if action == "FILL_NON_SENSITIVE":
        src = node.get("input_source") or ""
        if not src:
            errs.append(f"{nid}: FILL_NON_SENSITIVE requires input_source")
        if _SENSITIVE_FIELD_RE.search(src) or node.get("sensitive"):
            errs.append(f"{nid}: sensitive input may never be automated — use APPLICANT_HANDOFF")
    if action == "APPLICANT_HANDOFF":
        if node.get("handoff_kind") not in SENSITIVE_HANDOFF_KINDS:
            errs.append(f"{nid}: APPLICANT_HANDOFF requires a declared handoff_kind")
    if node.get("retry_class") not in RETRY_CLASSES:
        errs.append(f"{nid}: invalid retry_class")
    if node.get("irreversibility") not in IRREVERSIBILITY:
        errs.append(f"{nid}: invalid irreversibility class")
    if node.get("irreversibility") == "irreversible":
        if node.get("retry_class") != "reconcile_first":
            errs.append(f"{nid}: an irreversible action must declare retry_class=reconcile_first")
        if not node.get("success_evidence"):
            errs.append(f"{nid}: an irreversible action must declare success_evidence")
    if int(node.get("max_retries", 0)) > 3:
        errs.append(f"{nid}: max_retries above 3 is not permitted (unbounded-retry guard)")
    if int(node.get("timeout_ms", 0)) <= 0 or int(node.get("timeout_ms", 0)) > 300000:
        errs.append(f"{nid}: timeout_ms must be within (0, 300000]")
    for nxt in node.get("next") or []:
        if not isinstance(nxt, dict) or "to" not in nxt:
            errs.append(f"{nid}: each next branch needs a 'to' node id")
    return errs


def validate_flow(flow: list, *, allowed_hostnames: list[str]) -> list[str]:
    errs = []
    if not flow:
        return ["flow is empty"]
    ids = [n.get("node_id") for n in flow]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        errs.append(f"duplicate node ids: {sorted(dupes)}")
    known = set(ids)
    for raw in flow:
        errs.extend(validate_node(raw, allowed_hostnames=allowed_hostnames))
        for nxt in (raw.get("next") or []):
            if isinstance(nxt, dict) and nxt.get("to") not in known:
                errs.append(f"{raw.get('node_id')}: branch to unknown node {nxt.get('to')!r}")
    if not any(n.get("action") == "COMPLETE" for n in flow):
        errs.append("flow has no COMPLETE node")
    # Every irreversible action must be preceded somewhere by RECONCILE_OUTCOME.
    if any(normalize_node(n).get("irreversibility") == "irreversible" for n in flow):
        if not any(n.get("action") == "RECONCILE_OUTCOME" for n in flow):
            errs.append("flow with irreversible actions must include RECONCILE_OUTCOME")
    return errs


def validate_field_mapping(m: dict) -> list[str]:
    errs = []
    for f in ("ellis_field", "portal_field", "required"):
        if f not in (m or {}):
            errs.append(f"field mapping missing {f!r}")
    if m and _SENSITIVE_FIELD_RE.search(str(m.get("ellis_field", ""))):
        errs.append(f"sensitive field {m.get('ellis_field')!r} may never be mapped for autofill")
    return errs
