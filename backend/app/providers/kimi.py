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


def _extract_json(text: str) -> dict:
    """Extract the LARGEST parseable JSON object from model output. Robust to
    preambles, trailing text, and multiple concatenated objects (a greedy
    first-{ to last-} regex spans unrelated objects and fails to parse)."""
    text = (text or "").strip()
    if not text:
        return {}
    try:
        out = json.loads(text)
        return out if isinstance(out, dict) else {}
    except (json.JSONDecodeError, ValueError):
        pass
    decoder = json.JSONDecoder()
    best: dict = {}
    best_len = 0
    idx = 0
    while True:
        start = text.find("{", idx)
        if start < 0:
            break
        try:
            obj, end = decoder.raw_decode(text, start)
            if isinstance(obj, dict) and (end - start) > best_len:
                best, best_len = obj, end - start
            idx = end
        except (json.JSONDecodeError, ValueError):
            idx = start + 1
    return best


class KimiHttpError(Exception):
    """A Moonshot/Kimi HTTP failure with its status code, so callers can map
    401 / 402 / 429 / 5xx to precise applicant-facing provider messages. The
    raw response body is never attached — only the status."""

    def __init__(self, status: int):
        self.status = int(status)
        super().__init__(f"kimi moonshot HTTP {status}")


class KimiTimeout(Exception):
    """The Kimi call exceeded its bounded wall-clock budget."""


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

    def _chat(self, system: str, user: str, json_mode: bool = True, *,
              timeout: float | None = None, max_tokens: int | None = None,
              temperature: float | None = None, model: str | None = None) -> dict:
        # Always prefix the Ellis identity so the model can never present itself
        # as Kimi/Moonshot/the underlying model, or as an official/lawyer/embassy.
        from ..i18n import ELLIS_SYSTEM_IDENTITY
        system = ELLIS_SYSTEM_IDENTITY + "\n\n" + system
        body = {"model": model or self._model, "messages": [
            {"role": "system", "content": system}, {"role": "user", "content": user}]}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if max_tokens is not None:
            body["max_tokens"] = int(max_tokens)
        if temperature is not None:
            body["temperature"] = float(temperature)
        try:
            r = self._httpx.post(self._url, headers={"authorization": f"Bearer {self._key}"},
                                 json=body, timeout=timeout if timeout is not None else self._timeout)
        except self._httpx.TimeoutException as e:
            raise KimiTimeout(f"kimi call exceeded its {timeout or self._timeout}s budget") from e
        if r.status_code >= 400:
            raise KimiHttpError(r.status_code)
        msg = r.json()["choices"][0]["message"]
        # K3 is a reasoning model: under json_mode it occasionally leaves
        # "content" empty and puts the answer in "reasoning_content"
        # (kimi_vision handles the same quirk). Fall back before giving up.
        return (_extract_json(msg.get("content") or "")
                or _extract_json(msg.get("reasoning_content") or ""))

    def classify_document(self, doc_excerpt: str) -> dict:
        return self._chat("Classify this visa document. Reply JSON {type,confidence}.", doc_excerpt)

    def detect_missing_information(self, required: list, have: dict) -> dict:
        return self._chat("Return JSON {missing:[],complete:bool}.",
                          json.dumps({"required": required, "have": list(have.keys())}))

    def summarize_review(self, application: dict) -> dict:
        return self._chat("Summarize this application for applicant review. JSON {summary,risks}.",
                          json.dumps(application))

    def translate(self, text: str, target: str, source: str) -> str:  # pragma: no cover - needs key
        """Translate a document's extracted text.

        K3 is a reasoning model, and this call used to set neither temperature
        nor max_tokens — so a translation could spend most of its wall clock
        deliberating about text that needs no deliberation, and applicants
        waited. Translation is not a reasoning task: temperature 0 is correct
        for it anyway, and the answer's length is bounded by the input's, so
        both are stated instead of left open.
        """
        from ..i18n import LANGUAGE_NAMES
        tgt = LANGUAGE_NAMES.get(target, target)
        # Output tokens scale with input; ~2 tokens per input word is generous
        # for every language pair, with a floor for very short documents and a
        # ceiling that keeps one runaway call from hanging the applicant.
        budget = max(1200, min(16000, int(len(text.split()) * 2.6) + 600))
        out = self._chat(
            f"Translate the user's text into {tgt}. Keep the meaning faithful and "
            f"natural. Preserve every ⟦T…⟧ sentinel EXACTLY as written — do not "
            f"translate, reorder, or remove them. Translate directly: do not "
            f"explain, do not deliberate, do not comment. Reply JSON "
            f"{{\"translated\":\"...\"}}.",
            text, max_tokens=budget, temperature=0.0)
        return out.get("translated", text)

    def translate_batch(self, items: dict, target: str, source: str) -> dict:  # pragma: no cover - needs key
        """One call per catalog chunk: translate every VALUE of a JSON object
        of short UI strings, keys untouched. K3 is a reasoning model — the
        generous max_tokens keeps the answer out of reasoning_content."""
        from ..i18n import LANGUAGE_NAMES
        tgt = LANGUAGE_NAMES.get(target, target)
        out = self._chat(
            f"Translate every VALUE of the user's JSON object into {tgt}. These "
            f"are short UI strings for a visa-application product. Keep each "
            f"translation faithful, natural, and about as short as the original. "
            f"Keys must stay EXACTLY as given. Preserve every ⟦T…⟧ sentinel "
            f"EXACTLY as written. Reply as a JSON object with the SAME keys.",
            json.dumps(items, ensure_ascii=False),
            max_tokens=8000, timeout=120)
        return {str(k): str(v) for k, v in (out or {}).items()}

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
