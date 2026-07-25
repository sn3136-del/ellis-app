"""Document language detection + applicant-requested Kimi K3 machine
translation.

Detection is LOCAL and deterministic (Unicode script ranges + Latin stopword
sets) — nothing leaves the backend just to label a language. Translation runs
ONLY when the applicant explicitly asks: the OCR-extracted TEXT (never raw
image/PDF bytes) is sent to Kimi K3 with identifiers masked via the i18n
sentinel scheme (passport/booking numbers, dates, amounts, emails, URLs are
restored byte-for-byte and can never be altered by the model). The result is
stored as a linked, clearly-labelled machine-translation artifact — never a
certified translation, and the original document is never modified.

No document text or PII is ever logged or audited — only language codes,
character counts and document ids.
"""
from __future__ import annotations

import re

from sqlalchemy import select

from . import models, audit
from .i18n import protect_tokens, restore_tokens

DISCLAIMER = "Machine translation by Kimi K3 — not a certified translation."

# Minimum extracted characters for detection/translation to be meaningful.
MIN_TEXT_CHARS = 40

NO_TEXT_MESSAGE = ("Ellis could not extract enough text to translate this "
                   "document.")

LANG_NAMES = {
    "en": "English", "zh": "Chinese", "es": "Spanish", "fr": "French",
    "ar": "Arabic", "ru": "Russian", "ja": "Japanese", "ko": "Korean",
    "pt": "Portuguese", "de": "German", "it": "Italian", "hi": "Hindi",
    "th": "Thai", "he": "Hebrew", "el": "Greek", "vi": "Vietnamese",
}


def language_name(code: str) -> str:
    return LANG_NAMES.get(str(code or ""), str(code or "unknown"))


# ---------------------------------------------------------------------------
# Local, deterministic language detection.
_SCRIPTS = (
    ("zh", re.compile(r"[一-鿿]")),          # Han
    ("ja", re.compile(r"[぀-ヿ]")),          # Hiragana/Katakana
    ("ko", re.compile(r"[가-힯]")),          # Hangul
    ("ar", re.compile(r"[؀-ۿ]")),          # Arabic
    ("he", re.compile(r"[֐-׿]")),          # Hebrew
    ("ru", re.compile(r"[Ѐ-ӿ]")),          # Cyrillic
    ("hi", re.compile(r"[ऀ-ॿ]")),          # Devanagari
    ("th", re.compile(r"[฀-๿]")),          # Thai
    ("el", re.compile(r"[Ͱ-Ͽ]")),          # Greek
)

# Distinctive Latin-script stopwords per language (deliberately excluding words
# shared across languages).
_LATIN_STOPWORDS = {
    "es": {"el", "la", "los", "las", "una", "del", "por", "para", "con", "está",
           "fecha", "señor", "cuenta", "banco", "hasta", "según", "número"},
    "fr": {"le", "la", "les", "une", "des", "du", "est", "avec", "pour", "dans",
           "monsieur", "madame", "compte", "banque", "jusqu", "numéro", "à"},
    "pt": {"o", "os", "uma", "das", "dos", "não", "com", "para", "conta",
           "banco", "até", "número", "senhor", "data"},
    "de": {"der", "die", "das", "und", "eine", "mit", "für", "nicht", "konto",
           "bank", "bis", "nummer", "herr", "frau", "datum"},
    "it": {"il", "lo", "gli", "una", "del", "della", "con", "per", "non",
           "conto", "banca", "fino", "numero", "signor", "data"},
    "vi": {"của", "và", "ngày", "tháng", "không", "ngân", "hàng", "tài",
           "khoản", "số"},
    "en": {"the", "and", "of", "to", "is", "for", "with", "date", "account",
           "bank", "statement", "name", "from", "this", "number", "balance"},
}


def detect_language(text: str) -> dict:
    """Detect the primary language of extracted text. Local + deterministic.
    Returns {code, name, confidence} or {} when there is too little text."""
    t = (text or "").strip()
    if len(t) < MIN_TEXT_CHARS:
        return {}
    # Non-Latin scripts: proportion of script characters decides.
    letters = [c for c in t if c.isalpha()]
    if not letters:
        return {}
    for code, pattern in _SCRIPTS:
        hits = len(pattern.findall(t))
        if hits and hits / max(1, len(letters)) >= 0.25:
            return {"code": code, "name": language_name(code),
                    "confidence": "high"}
    # Latin scripts: distinctive stopword vote.
    words = re.findall(r"[a-záàâäãéèêëíìîïóòôöõúùûüçñß]+", t.lower())
    if not words:
        return {}
    wordset = set(words)
    scores = {code: len(wordset & sw) for code, sw in _LATIN_STOPWORDS.items()}
    best = max(scores, key=lambda c: scores[c])
    if scores[best] == 0:
        return {}
    runner_up = sorted(scores.values(), reverse=True)[1] if len(scores) > 1 else 0
    confidence = "high" if scores[best] >= 3 and scores[best] > runner_up else "low"
    return {"code": best, "name": language_name(best), "confidence": confidence}


# ---------------------------------------------------------------------------
# Target language for the destination's visa process. Honest default: English
# is accepted by most visa processes; specific destinations that routinely
# require documents in their official language are mapped explicitly.
_TARGET_BY_DESTINATION = {
    "CHN": "zh", "TWN": "zh", "JPN": "ja", "KOR": "ko",
    "FRA": "fr", "BEL": "fr", "ESP": "es", "MEX": "es", "ARG": "es",
    "COL": "es", "CHL": "es", "PER": "es", "DEU": "de", "AUT": "de",
    "CHE": "de", "ITA": "it", "BRA": "pt", "PRT": "pt", "RUS": "ru",
    "SAU": "ar", "ARE": "ar", "EGY": "ar", "QAT": "ar", "KWT": "ar",
    "THA": "th", "VNM": "vi", "GRC": "el", "ISR": "he",
}


def target_for_destination(destination: str) -> str:
    """Target language code for a destination country (name or alpha-3)."""
    code = str(destination or "").strip()
    if len(code) != 3 or not code.isupper():
        try:
            from .visa_snapshot.registry import normalize_country
            code = normalize_country(code, field="destination_country")
        except Exception:  # noqa: BLE001 - unknown destination → English
            return "en"
    return _TARGET_BY_DESTINATION.get(code, "en")


# Certified-translation signal in the route guidance (Kimi guidance carries no
# structured field for it — scan the honest free-text lists only).
_CERTIFIED_RE = re.compile(r"(certif|sworn|notari[sz])", re.I)


def certified_translation_flag(guidance: dict) -> bool:
    g = guidance or {}
    blobs: list[str] = []
    for key in ("required_documents", "exceptions", "uncertainty"):
        v = g.get(key)
        if isinstance(v, list):
            blobs.extend(str(x) for x in v)
    return any(_CERTIFIED_RE.search(b) and re.search(r"translat", b, re.I)
               for b in blobs)


# ---------------------------------------------------------------------------
# Kimi K3 translation of one document's extracted text.
class TranslationError(Exception):
    def __init__(self, status_code: int, detail):
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


_translator_override = None


def set_translator(fn) -> None:
    """Test hook: fn(text, target, source) -> translated text."""
    global _translator_override
    _translator_override = fn


def _kimi_translate(text: str, target: str, source: str) -> str:
    if _translator_override is not None:
        return _translator_override(text, target, source)
    from .providers import kimi
    from .providers.kimi import KimiHttpError, KimiTimeout
    provider = kimi.get_provider()
    if getattr(provider, "name", "") == "local_test_provider" or \
            not hasattr(provider, "translate"):
        raise TranslationError(503, {
            "reason": "translation_unavailable",
            "message": "Translation is not available — the Kimi provider is "
                       "not configured. The original document is unchanged."})
    from . import provider_errors
    try:
        return provider.translate(text, target, source or "auto")
    except KimiTimeout:
        raise TranslationError(504, {
            "reason": "translation_timeout",
            "message": "The translation provider timed out. Your document is "
                       "unchanged — try again."})
    except KimiHttpError as e:
        env = provider_errors.user_error(f"kimi moonshot HTTP {e.status}")
        raise TranslationError(503, {
            "reason": "translation_failed", "category": env.get("category"),
            "message": env.get("user_message")})


def existing_translation(db, document_id: str, target: str):
    """Cached artifact for (source, target) — translation runs once."""
    rows = db.execute(select(models.StoredDocument).where(
        models.StoredDocument.translation_of == document_id)).scalars().all()
    for r in rows:
        if (r.language or {}).get("code") == target:
            return r
    return None


def translate_document(db, p, app_row, doc: models.StoredDocument,
                       target: str) -> dict:
    """Translate one document's OCR text with Kimi K3 and store the result as
    a linked machine-translation artifact. Identifiers (numbers, codes, dates,
    emails, amounts) are sentinel-masked before the model sees the text and
    restored verbatim afterwards. Idempotent per (document, target)."""
    source_text = (doc.ocr_text or "").strip()
    if len(source_text) < MIN_TEXT_CHARS:
        raise TranslationError(422, {"reason": "no_text",
                                     "message": NO_TEXT_MESSAGE})
    cached = existing_translation(db, doc.id, target)
    if cached is not None:
        return _artifact_response(doc, cached, cached=True)

    masked, mapping = protect_tokens(source_text)
    detected = doc.language or detect_language(source_text)
    translated = restore_tokens(
        _kimi_translate(masked, target, detected.get("code") or "auto"), mapping)
    if not str(translated or "").strip():
        raise TranslationError(502, {
            "reason": "translation_failed",
            "message": "The provider returned no translation. Your document "
                       "is unchanged — try again."})

    body = f"{DISCLAIMER}\n\n{translated}"
    artifact = models.StoredDocument(
        org_id=p.org_id, application_id=app_row.id,
        name=f"{doc.name} — {language_name(target)} machine translation.txt",
        mime="text/plain", size_bytes=len(body.encode("utf-8")),
        sha256="", storage_ref="derived://translation",
        doc_type="translation", ocr_status="done",
        execution_class="LIVE_PRODUCTION",
        page_classification={"page_type": "translation",
                             "classifier": "kimi_translation",
                             "reject": False,
                             "accepted_as_passport_identity": False,
                             "reasons": ["machine translation artifact"]},
        extracted_fields={}, quality_warnings=[],
        ocr_text=translated,
        language={"code": target, "name": language_name(target),
                  "confidence": "machine_translation"},
        translation_of=doc.id)
    db.add(artifact)
    db.flush()
    db.add(models.DocumentBlob(document_id=artifact.id, org_id=p.org_id,
                               mime="text/plain",
                               content=body.encode("utf-8")))
    db.commit()
    audit.record(db, org_id=p.org_id, application_id=app_row.id,
                 action="document_translated",
                 detail={"source_document_id": doc.id,
                         "artifact_document_id": artifact.id,
                         "source_language": detected.get("code") or "unknown",
                         "target_language": target,
                         "characters": len(source_text)},
                 actor=p.user_id)
    return _artifact_response(doc, artifact, cached=False)


def _artifact_response(source: models.StoredDocument,
                       artifact: models.StoredDocument, *, cached: bool) -> dict:
    return {"translated": True, "cached": cached, "disclaimer": DISCLAIMER,
            "document_id": artifact.id, "name": artifact.name,
            "source_document_id": source.id,
            "source_language": (source.language or {}).get("code") or "unknown",
            "target_language": (artifact.language or {}).get("code"),
            "original_text": source.ocr_text or "",
            "translated_text": artifact.ocr_text or ""}
