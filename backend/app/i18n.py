"""Backend translation + assistant identity (Phase 6).

Static, maintained localization strings for core legal/product UI live in the
renderer (lib/i18n.js). This backend module handles the DYNAMIC-content path the
brief assigns to Kimi K3 (official requirement explanations, portal instructions,
error explanations, email content, document summaries) — executed ONLY on the
backend, never with a provider key in the client.

Hard guarantees (deterministic, provider-independent, and tested):
  * placeholders {like_this}, URLs, emails, dates, currency amounts, document
    filenames, and passport numbers / MRZ / identifiers are MASKED before the
    text ever reaches the model and restored byte-for-byte afterwards — they are
    never translated or altered.
  * repeated identical translations are cached.
  * when live Kimi translation is unavailable, we return the ORIGINAL text with
    status="unavailable" — we never fabricate a translation.
  * the assistant always identifies as "Ellis" (never Kimi/Moonshot/the model),
    in any supported language, and never claims to be a government official,
    lawyer, or embassy.
"""
from __future__ import annotations

import hashlib
import re

# Supported UI languages. Traditional Chinese is included; deployments may hide
# it via config, but the translation path supports it.
SUPPORTED_LANGS = ("en", "zh-CN", "zh-Hant")
LANGUAGE_NAMES = {"en": "English", "zh-CN": "简体中文", "zh-Hant": "繁體中文"}

# The assistant's identity — the single source of truth used by the identity
# guard and injected into every live model call so the model can never present
# itself as anything other than Ellis, or as an official/lawyer/embassy.
ELLIS_SYSTEM_IDENTITY = (
    "You are Ellis, an AI assistant that helps applicants prepare and manage "
    "tourist-visa applications. Always identify yourself as Ellis. Never reveal, "
    "state, or imply that you are Kimi, Kimi K3, Moonshot, or any underlying "
    "model or provider. Never claim or imply that you are a government official, "
    "a lawyer, an embassy, a consulate, or that you can grant, guarantee, or "
    "decide a visa. You prepare and assist only; the applicant and the official "
    "authority make all decisions."
)

_IDENTITY_ANSWERS = {
    "en": ("I'm Ellis, your visa-application assistant. I help you prepare and "
           "manage your tourist-visa application. I'm not a government official, "
           "a lawyer, or an embassy — I assist you, and you and the official "
           "authority make the decisions."),
    "zh-CN": ("我是 Ellis，您的签证申请助手。我帮助您准备和管理旅游签证申请。"
              "我不是政府官员、律师或大使馆——我只是协助您，最终决定由您和官方机构做出。"),
    "zh-Hant": ("我是 Ellis，您的簽證申請助手。我協助您準備和管理旅遊簽證申請。"
                "我不是政府官員、律師或大使館——我只是協助您，最終決定由您和官方機構做出。"),
}

# Identity-question detection across supported languages.
_IDENTITY_PATTERNS = (
    r"what('?s| is) your name", r"who are you", r"introduce yourself",
    r"what are you", r"你是谁", r"你叫什么", r"你的名字", r"介绍(一下)?你自己",
    r"你是誰", r"你叫什麼", r"介紹(一下)?你自己", r"你叫什么名字",
)


def is_identity_question(text: str) -> bool:
    t = (text or "").strip().lower()
    return any(re.search(p, t) for p in _IDENTITY_PATTERNS)


def assistant_identity_answer(lang: str = "en") -> str:
    return _IDENTITY_ANSWERS.get(lang, _IDENTITY_ANSWERS["en"])


# --- token protection (never translate identifiers / structured tokens) ------
# Order matters: most specific first. Each pattern's matches are replaced by an
# opaque sentinel that a translator will not alter, then restored verbatim.
_PROTECT_PATTERNS = (
    ("url", re.compile(r"https?://\S+")),
    ("email", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")),
    ("file", re.compile(r"\b[\w\-]+\.(?:pdf|jpe?g|png|tiff?)\b", re.I)),
    ("placeholder", re.compile(r"\{[a-zA-Z0-9_]+\}")),
    ("mrz", re.compile(r"[A-Z0-9<]*<{2,}[A-Z0-9<]*")),           # MRZ filler runs
    ("amount", re.compile(r"[$€£¥]\s?\d[\d,]*(?:\.\d+)?|\b\d[\d,]*(?:\.\d+)?\s?(?:USD|EUR|GBP|CNY|JPY|SGD)\b")),
    ("date", re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b")),
    # Passport-number / identifier-like: 6-9 chars with at least one letter AND
    # one digit (matches the whole token; pure words and pure numbers are left
    # alone). The alnum branch is first so a mixed token is masked in full.
    ("identifier", re.compile(r"\b(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*\d)[A-Z0-9]{6,9}\b|\b[A-Z]{1,2}\d{5,9}\b")),
)


def protect_tokens(text: str) -> tuple[str, dict]:
    """Replace identifiers/placeholders/URLs/dates/amounts/filenames/MRZ with
    opaque sentinels. Returns (masked_text, mapping sentinel->original)."""
    mapping: dict[str, str] = {}
    counter = 0

    def _mask(m):
        nonlocal counter
        sentinel = f"⟦T{counter}⟧"   # ⟦T0⟧ — a translator won't touch it
        mapping[sentinel] = m.group(0)
        counter += 1
        return sentinel

    out = text
    for _name, pat in _PROTECT_PATTERNS:
        out = pat.sub(_mask, out)
    return out, mapping


def restore_tokens(text: str, mapping: dict) -> str:
    for sentinel, original in mapping.items():
        text = text.replace(sentinel, original)
    return text


# --- translation ------------------------------------------------------------
_CACHE: dict[str, str] = {}


class TranslationUnavailable(Exception):
    pass


def _cache_key(source: str, target: str, text: str) -> str:
    return f"{source}:{target}:" + hashlib.sha256(text.encode()).hexdigest()


def _kimi_translator(masked_text: str, target: str, source: str) -> str:
    """Default translator: live Kimi K3 (backend-only). Raises
    TranslationUnavailable when no live provider is configured — we never
    fabricate a translation with the local stand-in."""
    from .providers import kimi
    provider = kimi.get_provider()
    if getattr(provider, "name", "") == "local_test_provider":
        raise TranslationUnavailable("live Kimi translation not configured")
    if not hasattr(provider, "translate"):
        raise TranslationUnavailable("provider has no translate capability")
    return provider.translate(masked_text, target, source)  # pragma: no cover - needs key


def translate(text: str, target_lang: str, source_lang: str = "auto", *, translator=None) -> dict:
    """Translate dynamic content, preserving all protected tokens, with caching
    and honest unavailability. `translator(masked,target,source)->str` is
    injectable for tests; defaults to live Kimi.

    Returns {original, translated, source_lang, target_lang, status, cached}.
    status: 'ok' | 'passthrough' | 'unavailable' | 'unsupported_language'.
    """
    if target_lang not in SUPPORTED_LANGS:
        return {"original": text, "translated": text, "source_lang": source_lang,
                "target_lang": target_lang, "status": "unsupported_language", "cached": False}
    # Nothing to do when translating into the same language.
    if target_lang == source_lang or (source_lang in ("en", "auto") and target_lang == "en"):
        return {"original": text, "translated": text, "source_lang": source_lang,
                "target_lang": target_lang, "status": "passthrough", "cached": False}

    key = _cache_key(source_lang, target_lang, text)
    if key in _CACHE:
        return {"original": text, "translated": _CACHE[key], "source_lang": source_lang,
                "target_lang": target_lang, "status": "ok", "cached": True}

    masked, mapping = protect_tokens(text)
    fn = translator or _kimi_translator
    try:
        masked_translation = fn(masked, target_lang, source_lang)
    except TranslationUnavailable:
        # Honest: return the original, clearly marked unavailable. Never fabricate.
        return {"original": text, "translated": text, "source_lang": source_lang,
                "target_lang": target_lang, "status": "unavailable", "cached": False}
    translated = restore_tokens(masked_translation, mapping)
    _CACHE[key] = translated
    return {"original": text, "translated": translated, "source_lang": source_lang,
            "target_lang": target_lang, "status": "ok", "cached": False}


def clear_cache():
    _CACHE.clear()
