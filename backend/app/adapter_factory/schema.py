"""Typed adapter specification (brief §16): the deterministic flow contract.

A flow is an ordered list of action NODES. Each node fully declares what it
does, where it may do it, what proves success, and how failures recover.
Nothing outside this schema can execute: the runtime compiles the flow and
refuses anything unknown (fail closed).
"""
from __future__ import annotations

import re

# Deterministic action types (§16). Anything else is rejected at compile time.
# SELECT_SEARCH: search-combobox (click, type value, pick the exact/first
#   matching option deterministically) — sensitivity-screened like FILL.
# SCROLL_TO_BOTTOM: scroll a container (window when selector empty) to the
#   bottom — reversible by construction (entry-gate instruction modals).
ACTION_TYPES = [
    "NAVIGATE", "WAIT_FOR_STATE", "CLICK", "FILL_NON_SENSITIVE", "SELECT",
    "SELECT_SEARCH", "CHECK", "SCROLL_TO_BOTTOM", "UPLOAD_AUTHORIZED_DOCUMENT",
    "READ_TEXT", "READ_FEE",
    "READ_APPOINTMENT_INVENTORY", "BOOK_APPOINTMENT",
    "WAIT_FOR_NETWORK", "VERIFY_EVIDENCE",
    "REGISTER_ACCOUNT",
    "APPLICANT_HANDOFF", "RECONCILE_OUTCOME", "PAUSE", "COMPLETE",
]

# Steps that must ALWAYS be an applicant handoff, never automated (§16).
# portal_terms_consent: the portal's own terms/agreement gate — the applicant
# signs the verbatim terms INSIDE Ellis and Ellis transcribes the choice,
# exactly like legally_personal_declaration transcribes the truthfulness tick.
SENSITIVE_HANDOFF_KINDS = [
    "credentials", "otp", "captcha", "passkey", "payment_credentials",
    "portal_native_signature", "legally_personal_declaration",
    "portal_terms_consent",
    # Reserving a government appointment commits the applicant to attend on a
    # specific date at a specific consulate: the slot is ALWAYS theirs to
    # choose, never auto-picked from their preferences.
    "appointment_selection",
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
    "doc_type": "",
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
    # Portal-terms transcription: a CHECK/CLICK node that ENACTS the
    # applicant's agreement to the portal's own terms. It runs only after the
    # applicant has SIGNED the verbatim terms in Ellis (a preceding
    # portal_terms_consent handoff); the runtime binds their signature to
    # consent_terms_hash before it may act.
    "requires_signed_terms": False,
    "consent_terms_hash": "",
    "consent_family_id": "",
    # REGISTER_ACCOUNT field selectors: Ellis fills the applicant's OWN email
    # and a FRESH password it generates and vaults (never the applicant's
    # existing secret, never an authentication into a pre-existing account).
    "email_selector": "",
    "password_selector": "",
    "confirm_password_selector": "",
    "submit_selector": "",
    "next": [],
}

_REQUIRED = ("node_id", "action", "allowed_hostname")

_SELECTOR_OK = ("#", ".", "[", "input", "select", "textarea", "button", "form",
                "a", "label", "iframe", "main", "div")

_NODE_ID_RE = re.compile(r"^[a-z0-9_\-]{2,120}$")

# Two bounded Playwright selector forms remain deterministic despite spaces:
#   tag:has-text("Exact visible text")   (short, quote-bounded text)
#   <simple selector> >> nth=N           (fixed index over a simple selector)
_PW_TEXT_RE = re.compile(r'^[a-z]+:has-text\("[^"<>]{1,60}"\)$')
_PW_NTH_RE = re.compile(r'^(?P<base>\S+) >> nth=\d{1,2}$')


def deterministic_selector(sel: str) -> bool:
    """True for selectors a deterministic runtime can target: simple CSS with
    no descendant combinators, or the two bounded Playwright forms above.
    Deep ancestor paths (spaces / '>') are refused."""
    sel = (sel or "").strip()
    if not sel or not sel.startswith(_SELECTOR_OK):
        return False
    if " " not in sel and ">" not in sel:
        return True
    if _PW_TEXT_RE.match(sel):
        return True
    m = _PW_NTH_RE.match(sel)
    return bool(m and ">" not in m.group("base"))


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
    if action in ("CLICK", "FILL_NON_SENSITIVE", "SELECT", "SELECT_SEARCH",
                  "CHECK", "READ_TEXT", "READ_FEE", "UPLOAD_AUTHORIZED_DOCUMENT"):
        sel = (node.get("selector") or "").strip()
        if not sel:
            # ONE honest exception: a control that provably cannot be observed
            # at build time. A portal's final submit lives past the applicant's
            # own review step, which is exactly where credential-free recon
            # must stop, so no selector for it can exist. The node declares
            # that with selector_source, and the runtime resolves the portal's
            # primary action where the page finally shows it — failing closed
            # if there is none. The alternative was specgen writing down a
            # plausible '#submit-btn' it had never seen, which is worse in
            # every way: it looks valid here and misleads downstream.
            if not (action == "CLICK"
                    and node.get("selector_source") == "runtime_primary_action"):
                errs.append(f"{nid}: {action} requires a deterministic selector")
        elif not sel.startswith(_SELECTOR_OK):
            errs.append(f"{nid}: selector {sel!r} is not a deterministic CSS selector")
    if action == "SCROLL_TO_BOTTOM":
        # Selector-or-empty (empty scrolls the window); always reversible.
        # 'html'/'body' ARE the window in every document — treat them as the
        # whole-page target rather than rejecting them as non-deterministic
        # (a curated gate that dwells on the page declares exactly those).
        sel = (node.get("selector") or "").strip()
        if sel.lower() in ("html", "body"):
            sel = ""
        if sel and not sel.startswith(_SELECTOR_OK):
            errs.append(f"{nid}: selector {sel!r} is not a deterministic CSS selector")
        if node.get("irreversibility") != "reversible":
            errs.append(f"{nid}: SCROLL_TO_BOTTOM must be reversible")
        if node.get("sensitive"):
            errs.append(f"{nid}: SCROLL_TO_BOTTOM may never be marked sensitive")
    if action in ("FILL_NON_SENSITIVE", "SELECT_SEARCH"):
        src = node.get("input_source") or ""
        if not src:
            errs.append(f"{nid}: {action} requires input_source")
        if _SENSITIVE_FIELD_RE.search(src) or node.get("sensitive"):
            errs.append(f"{nid}: sensitive input may never be automated — use APPLICANT_HANDOFF")
    if action == "CHECK":
        # Instruction/commitment acknowledgments may be checked; anything that
        # smells like a signature/declaration-signing or otherwise sensitive
        # control must remain an applicant handoff.
        probe = f"{node.get('selector', '')} {node.get('input_source', '')}"
        if _SENSITIVE_FIELD_RE.search(probe) or node.get("sensitive"):
            errs.append(f"{nid}: sensitive checkbox may never be automated — "
                        f"use APPLICANT_HANDOFF")
    if node.get("requires_signed_terms"):
        # A terms-transcription node must bind to the EXACT text the
        # applicant will sign, and must be an enactment action.
        if action not in ("CLICK", "CHECK"):
            errs.append(f"{nid}: requires_signed_terms only applies to "
                        f"CLICK/CHECK transcription nodes")
        if not (node.get("consent_terms_hash") or "").strip():
            errs.append(f"{nid}: requires_signed_terms needs consent_terms_hash "
                        f"binding the exact terms text")
        if not (node.get("consent_family_id") or "").strip():
            errs.append(f"{nid}: requires_signed_terms needs consent_family_id")
    if action == "UPLOAD_AUTHORIZED_DOCUMENT" and not (node.get("doc_type") or "").strip():
        errs.append(f"{nid}: UPLOAD_AUTHORIZED_DOCUMENT requires a declared doc_type")
    if action == "REGISTER_ACCOUNT":
        # Ellis creates a NEW account: applicant email + a fresh vaulted
        # password. It must be reconcile-first (never double-register), name
        # the observed email + password + submit controls, and prove success
        # only by an authenticated-session evidence transition — never a
        # banner. The emailed verification code stays an applicant handoff.
        for f in ("email_selector", "password_selector", "submit_selector"):
            if not (node.get(f) or "").strip():
                errs.append(f"{nid}: REGISTER_ACCOUNT requires {f}")
        if node.get("retry_class") != "reconcile_first":
            errs.append(f"{nid}: REGISTER_ACCOUNT must be reconcile_first "
                        f"(never create a duplicate account)")
        ok_cats = {"account_registration_submitted", "session_authenticated"}
        if not any(e.get("category") in ok_cats
                   for e in (node.get("success_evidence") or [])):
            errs.append(f"{nid}: REGISTER_ACCOUNT must declare success_evidence "
                        f"(account_registration_submitted)")
    if action == "READ_APPOINTMENT_INVENTORY":
        # Read-only calendar observation: the slot rows must be a DECLARED,
        # observed selector — recon proves it exists; nothing here is guessed.
        if not (node.get("slot_selector") or "").strip():
            errs.append(f"{nid}: READ_APPOINTMENT_INVENTORY requires slot_selector")
    if action == "BOOK_APPOINTMENT":
        # Reserving a government appointment for a named person is
        # irreversible: reconcile-first (never double-book), bounded retries,
        # and success proven only by official booking evidence. The slot the
        # applicant chose is clicked by its portal handle attribute.
        if not (node.get("handle_attr") or "").strip():
            errs.append(f"{nid}: BOOK_APPOINTMENT requires handle_attr "
                        f"(the portal attribute carrying each slot's handle)")
        if node.get("irreversibility") != "irreversible":
            errs.append(f"{nid}: BOOK_APPOINTMENT must be declared irreversible")
        if node.get("retry_class") != "reconcile_first":
            errs.append(f"{nid}: BOOK_APPOINTMENT must be reconcile_first "
                        f"(never create a duplicate booking)")
        if not any(e.get("category") == "appointment_booked"
                   for e in (node.get("success_evidence") or [])):
            errs.append(f"{nid}: BOOK_APPOINTMENT must declare success_evidence "
                        f"(appointment_booked)")
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
    # A terms-transcription node may never appear without a portal_terms_consent
    # handoff earlier in the flow — Ellis never enacts agreement the applicant
    # was not first shown and asked to sign.
    if any(normalize_node(n).get("requires_signed_terms") for n in flow):
        seen_consent = False
        for n in flow:
            nn = normalize_node(n)
            if nn.get("action") == "APPLICANT_HANDOFF" \
                    and nn.get("handoff_kind") == "portal_terms_consent":
                seen_consent = True
            if nn.get("requires_signed_terms") and not seen_consent:
                errs.append(f"{nn.get('node_id')}: terms transcription precedes "
                            f"its portal_terms_consent handoff")
    return errs


def validate_field_mapping(m: dict) -> list[str]:
    errs = []
    for f in ("ellis_field", "portal_field", "required"):
        if f not in (m or {}):
            errs.append(f"field mapping missing {f!r}")
    if m and _SENSITIVE_FIELD_RE.search(str(m.get("ellis_field", ""))):
        errs.append(f"sensitive field {m.get('ellis_field')!r} may never be mapped for autofill")
    return errs
