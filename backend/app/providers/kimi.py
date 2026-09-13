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
    # H1B Ask-Ellis assistant tools (executed by app/h1b/assistant.py). READ
    # tools return party-scoped views assembled backend-side; ACTION tools
    # execute ONLY through the party-gated h1b api code paths AS the calling
    # human's principal, so the model can never exceed that human's authority
    # (an unauthorized proposal comes back as an honest 403 tool result, never
    # an execution). Sign / pay / submit / declaration surfaces remain
    # model-prohibited below.
    "get_h1b_pipeline": {"case_id": "str"},
    "get_h1b_checklist_status": {"case_id": "str"},
    "get_h1b_step_facts": {"step_key": "str"},
    "get_h1b_rfe_summary": {"case_id": "str"},
    "release_h1b_step": {"case_id": "str", "step_key": "str"},
    "prepare_h1b_form": {"case_id": "str", "form_key": "str"},
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

    # ---- H1B Ask-Ellis assistant (deterministic stand-in) ------------------
    # Obvious action phrases -> tool proposals. Real language understanding is
    # the live model's job; the stand-in only needs to exercise the SAME
    # validated, party-gated tool path the hermetic tests pin down.
    _H1B_ACTION_PATTERNS = (
        (re.compile(r"release\s+(?:the\s+)?([a-z0-9_]+)\s+step"),
         "release_h1b_step", "step_key"),
        (re.compile(r"prepare\s+(?:the\s+)?([a-z0-9_\-]+)\s+form"),
         "prepare_h1b_form", "form_key"),
    )

    def _run_h1b_assistant(self, context: dict) -> AgentResult:
        message = str(context.get("message") or "").lower()
        grounding = context.get("grounding") or {}
        case_id = str(context.get("case_id") or "")
        calls = []
        for pattern, tool, arg_name in self._H1B_ACTION_PATTERNS:
            m = pattern.search(message)
            if m:
                calls.append({"tool": tool,
                              "args": {"case_id": case_id, arg_name: m.group(1)}})
        # The reply echoes ONLY facts the backend already scoped for THIS
        # caller: the grounding is party-scoped upstream, so a fact the caller
        # may not see is absent from it and can never be echoed.
        parts = []
        steps = grounding.get("steps") or []
        if steps:
            parts.append("; ".join(f"{s.get('step_key')}: {s.get('status')}"
                                   for s in steps))
        for key, value in sorted((grounding.get("party_answers") or {}).items()):
            tokens = [t for t in str(key).split("_") if len(t) >= 3]
            if any(t in message for t in tokens):
                parts.append(f"{key}: {value}")
        sources = grounding.get("sources") or []
        if not parts and sources:
            parts.append(f"see official source: {sources[0]}")
        return AgentResult(ok=True, output={"reply": " | ".join(parts)},
                           tool_calls=calls, steps=1, stopped_reason="done",
                           engine=self.name)

    def run(self, goal: str, context: dict) -> AgentResult:
        if str(goal or "").startswith("h1b_assistant"):
            return self._run_h1b_assistant(context)
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
    raw response body is never attached: only the status, the provider's own
    error type (for example ``exceeded_current_quota_error``) and a short
    message with any key-like token removed, so a suspended account can be
    told apart from a momentary rate limit without leaking anything."""

    def __init__(self, status: int, error_type: str | None = None,
                 message: str | None = None, retry_after: float | None = None):
        self.status = int(status)
        self.error_type = str(error_type or "")[:80]
        self.message = _scrub(message)
        self.retry_after = retry_after
        super().__init__(f"kimi moonshot HTTP {status}"
                         + (f" ({self.error_type})" if self.error_type else ""))

    @property
    def account_suspended(self) -> bool:
        """The account itself is out of balance or suspended: retrying, or
        letting three more workers try, only burns the sweep's budget."""
        low = f"{self.error_type} {self.message}".lower()
        return (self.error_type == "exceeded_current_quota_error"
                or "insufficient balance" in low or "is suspended" in low
                or "recharge" in low)


def _scrub(message) -> str:
    """Keep a provider message readable for an operator, never a secret:
    the one redaction rule of provider_errors decides what a key looks like,
    so this message and the diagnostics envelope can never disagree."""
    from ..provider_errors import redact_diagnostic
    return redact_diagnostic(str(message or ""), limit=240)


def _http_error(response) -> KimiHttpError:
    """Read the provider's error envelope without ever attaching the body."""
    error_type = message = None
    try:
        data = response.json()
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            error_type = err.get("type")
            message = err.get("message")
    except Exception:  # noqa: BLE001 - a non-JSON body carries no envelope
        pass
    retry_after = None
    try:
        raw = response.headers.get("retry-after")
        if raw is not None:
            retry_after = min(120.0, max(0.0, float(raw)))
    except (TypeError, ValueError):
        retry_after = None
    return KimiHttpError(response.status_code, error_type, message, retry_after)


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
              temperature: float | None = None, model: str | None = None,
              reasoning_effort: str | None = None) -> dict:
        # Always prefix the Ellis identity so the model can never present itself
        # as Kimi/Moonshot/the underlying model, or as an official/lawyer/embassy.
        from ..i18n import ELLIS_SYSTEM_IDENTITY
        system = ELLIS_SYSTEM_IDENTITY + "\n\n" + system
        body = {"model": model or self._model, "messages": [
            {"role": "system", "content": system}, {"role": "user", "content": user}]}
        if reasoning_effort is not None:
            if body["model"] != "kimi-k3" or reasoning_effort not in {"low", "high", "max"}:
                raise ValueError("reasoning_effort requires a supported Kimi K3 setting")
            body["reasoning_effort"] = reasoning_effort
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if max_tokens is not None:
            body["max_tokens"] = int(max_tokens)
        if temperature is not None:
            body["temperature"] = float(temperature)
        from . import provider_usage
        try:
            r = provider_usage.post(self._httpx.post, self._url,
                headers={"authorization": f"Bearer {self._key}"}, json=body,
                timeout=timeout if timeout is not None else self._timeout)
        except self._httpx.TimeoutException as e:
            raise KimiTimeout(f"kimi call exceeded its {timeout or self._timeout}s budget") from e
        if r.status_code >= 400:
            raise _http_error(r)
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

        K3 supports low reasoning effort for this bounded transformation.
        Its sampling temperature is fixed, so passing the old temperature=0
        caused an invalid-request error instead of a translation.
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
            text, max_tokens=budget,
            temperature=None if self._model in {"kimi-k3", "kimi-k2.6", "kimi-k2.7-code", "kimi-k2.7-code-highspeed"} else 0.0,
            reasoning_effort="low" if self._model == "kimi-k3" else None)
        return out.get("translated", text)

    def translate_batch(self, items: dict, target: str, source: str,
                        model: str | None = None) -> dict:  # pragma: no cover - needs key
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
            max_tokens=8000, timeout=120, model=model,
            reasoning_effort="low" if (model or self._model) == "kimi-k3" else None)
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
