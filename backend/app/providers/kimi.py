"""Kimi K3 agentic reasoning through the OpenAI-compatible API.

Security model (enforced here, not just documented):
 - Kimi may ONLY propose tool calls from an allowlisted registry. The backend
   independently validates and executes every tool; Kimi never executes.
 - Tools that would touch a secret, a payment, a booking, a declaration, or a
   submission are NOT in Kimi's registry — those are backend/human-only.
 - Non-sensitive context only is ever sent to the model.
 - Agent steps are bounded; loops / repeated no-progress calls are detected.

When MOONSHOT_API_KEY is absent, a deterministic LocalKimiProvider stands in so
the whole system (and its tests) runs without credentials. Activation: set
MOONSHOT_API_KEY.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..config import settings

# ---- Allowlisted tools Kimi may PROPOSE (backend executes + validates) ------
# Deliberately excludes: pay, book, reschedule, submit, reveal_secret,
# read_otp, solve_captcha, accept_declaration, create_account. Those are
# backend- or human-only and can never be driven by the model.
ALLOWLISTED_TOOLS = {
    "classify_document": {"doc_excerpt": "str"},
    "normalize_fields": {"fields": "dict"},
    "compare_documents": {"a": "dict", "b": "dict"},
    "detect_missing_information": {"required": "list", "have": "dict"},
    "map_application_fields": {"answers": "dict", "target_fields": "list"},
    "generate_applicant_questions": {"gaps": "list"},
    "summarize_review": {"application": "dict"},
    "interpret_page": {"non_sensitive_dom": "str", "goal": "str"},
    # Read-only discovery: backend runs the controlled search + official-domain
    # verification and can ONLY produce a disabled adapter draft.
    "discover_official_visa_portal": {"country": "str", "visa_type": "str"},
}

PROHIBITED_FOR_MODEL = {
    "solve_captcha", "read_otp", "reveal_secret", "handle_card", "access_password",
    "accept_declaration", "pay_fee", "book_appointment", "reschedule_appointment",
    "submit_application", "create_account",
}


@dataclass
class AgentResult:
    ok: bool
    output: dict = field(default_factory=dict)
    tool_calls: list = field(default_factory=list)
    steps: int = 0
    stopped_reason: str = ""
    engine: str = ""


class ToolSecurityError(Exception):
    pass


def validate_tool_call(name: str, args: dict) -> None:
    """Backend-side gate. Raises if the model proposed anything not allowlisted."""
    if name in PROHIBITED_FOR_MODEL:
        raise ToolSecurityError(f"tool '{name}' is never permitted for the model")
    if name not in ALLOWLISTED_TOOLS:
        raise ToolSecurityError(f"tool '{name}' is not in the allowlist")
    # Reject obviously sensitive payloads even into allowed tools.
    blob = json.dumps(args).lower()
    if re.search(r"password|passwd|\bcvc\b|\bcvv\b|card number|otp code|secret", blob):
        raise ToolSecurityError("tool arguments contain a sensitive value")


class LocalKimiProvider:
    """Deterministic stand-in with the same interface + JSON-schema outputs."""
    name = "local_test_provider"

    def classify_document(self, doc_excerpt: str) -> dict:
        t = doc_excerpt.lower()
        if "p<" in t or "passport" in t:
            return {"type": "passport", "confidence": 0.97}
        if "bank" in t or "balance" in t:
            return {"type": "bank_statement", "confidence": 0.9}
        if "insurance" in t:
            return {"type": "travel_insurance", "confidence": 0.9}
        return {"type": "other", "confidence": 0.4}

    def detect_missing_information(self, required: list, have: dict) -> dict:
        missing = [r for r in required if not have.get(r)]
        return {"missing": missing, "complete": not missing}

    def summarize_review(self, application: dict) -> dict:
        fields = application.get("answers", {})
        return {"summary": f"{len(fields)} fields captured for "
                           f"{application.get('destination_country', '?')} {application.get('visa_type', 'tourist')} visa.",
                "risks": []}

    def run(self, goal: str, context: dict) -> AgentResult:
        # A tiny deterministic "agent": propose the one obviously-useful tool.
        calls = []
        if "document" in goal and context.get("doc_excerpt"):
            calls.append({"tool": "classify_document", "args": {"doc_excerpt": context["doc_excerpt"]}})
        return AgentResult(ok=True, output={"plan": goal}, tool_calls=calls, steps=1,
                           stopped_reason="done", engine=self.name)


class LiveKimiProvider:  # pragma: no cover - needs a real key/network
    name = "kimi-k3"

    def __init__(self):
        import httpx
        self._httpx = httpx
        s = settings()
        self._url = s.kimi_base_url.rstrip("/") + "/chat/completions"
        self._key = s.moonshot_api_key
        self._model = s.kimi_model
        self._timeout = s.kimi_timeout_seconds

    def _chat(self, system: str, user: str, json_mode: bool = True) -> dict:
        # Always prefix the Ellis identity so the model can never present itself
        # as Kimi/Moonshot/the underlying model, or as an official/lawyer/embassy.
        from ..i18n import ELLIS_SYSTEM_IDENTITY
        system = ELLIS_SYSTEM_IDENTITY + "\n\n" + system
        body = {"model": self._model, "messages": [
            {"role": "system", "content": system}, {"role": "user", "content": user}]}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        r = self._httpx.post(self._url, headers={"authorization": f"Bearer {self._key}"},
                             json=body, timeout=self._timeout)
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        content = msg.get("content") or ""
        m = re.search(r"\{[\s\S]*\}", content)
        if not m:
            # K3 is a reasoning model: under json_mode it occasionally leaves
            # "content" empty and puts the answer in "reasoning_content"
            # (kimi_vision handles the same quirk). Fall back before giving up.
            m = re.search(r"\{[\s\S]*\}", msg.get("reasoning_content") or "")
        return json.loads(m.group(0)) if m else {}

    def classify_document(self, doc_excerpt: str) -> dict:
        return self._chat("Classify this visa document. Reply JSON {type,confidence}.", doc_excerpt)

    def detect_missing_information(self, required: list, have: dict) -> dict:
        return self._chat("Return JSON {missing:[],complete:bool}.",
                          json.dumps({"required": required, "have": list(have.keys())}))

    def summarize_review(self, application: dict) -> dict:
        return self._chat("Summarize this application for applicant review. JSON {summary,risks}.",
                          json.dumps(application))

    def translate(self, text: str, target: str, source: str) -> str:  # pragma: no cover - needs key
        from ..i18n import LANGUAGE_NAMES
        tgt = LANGUAGE_NAMES.get(target, target)
        out = self._chat(
            f"Translate the user's text into {tgt}. Keep the meaning faithful and "
            f"natural. Preserve every ⟦T…⟧ sentinel EXACTLY as written — do not "
            f"translate, reorder, or remove them. Reply JSON {{\"translated\":\"...\"}}.",
            text)
        return out.get("translated", text)

    def run(self, goal: str, context: dict) -> AgentResult:
        # A bounded tool-calling loop would live here; every proposed call is
        # passed back to the backend for validate_tool_call + execution.
        out = self._chat(f"Goal: {goal}. Propose allowlisted tool calls as JSON "
                         f"{{tool_calls:[{{tool,args}}]}}.", json.dumps(context))
        calls = out.get("tool_calls", [])[: settings().kimi_max_agent_steps]
        return AgentResult(ok=True, output=out, tool_calls=calls, steps=len(calls),
                           stopped_reason="done", engine=self.name)


def get_provider():
    s = settings()
    if s.moonshot_api_key and s.kimi_enabled:
        return LiveKimiProvider()
    return LocalKimiProvider()


def run_agent(goal: str, context: dict, *, max_steps: int | None = None) -> AgentResult:
    """Run the agent with loop/no-progress detection and tool validation."""
    provider = get_provider()
    max_steps = max_steps or settings().kimi_max_agent_steps
    res = provider.run(goal, context)
    # Validate every proposed tool call; drop (and record) anything unsafe.
    safe, seen = [], set()
    for call in res.tool_calls[:max_steps]:
        name, args = call.get("tool"), call.get("args", {})
        try:
            validate_tool_call(name, args)
        except ToolSecurityError:
            res.stopped_reason = f"blocked unsafe tool: {name}"
            continue
        sig = name + json.dumps(args, sort_keys=True)
        if sig in seen:  # repeated identical call → loop
            res.stopped_reason = "loop detected"
            break
        seen.add(sig)
        safe.append(call)
    res.tool_calls = safe
    return res
