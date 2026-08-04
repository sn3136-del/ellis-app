"""Reading form answers out of the documents the applicant already uploaded.

An applicant who has given Ellis their hotel confirmation should not then be
asked where they are staying. The answer is in the file — Ellis just never
looked. This module looks, so the questions that reach a person are only the
ones nothing on file can answer.

Two rules, and they are the whole reason this is safe:

  1. GROUNDED OR DROPPED. Every value a model returns must appear in the text
     of the document it claims to have read it from. A consular form is signed
     under penalty of perjury; a plausible-sounding hotel name that is not in
     the booking would be the applicant's lie, in their name.
  2. SUGGESTED, NEVER FILED. Nothing here is written into the case's answers.
     A derived value arrives as a pre-filled question the applicant sees and
     confirms. The moment they save it, it is theirs; until then it is Ellis
     saying "is this right?" — which is what reading somebody's document
     entitles it to say, and no more.
"""
from __future__ import annotations

import re

from sqlalchemy import select

from . import models

# Where each answer can be read from, by document type. A key is only ever
# looked for in documents that would actually contain it: a bank statement is
# not asked where somebody is staying.
SOURCES = {
    "accommodation": ("hotel_booking", "invitation_letter", "accommodation_proof"),
    "accommodation_address": ("hotel_booking", "invitation_letter",
                              "accommodation_proof"),
    "employer": ("employment_letter", "payslip"),
    "occupation": ("employment_letter", "payslip"),
}

EXTRACT_SYSTEM = """You read one uploaded travel document and pull out specific \
facts for a visa application form.

Rules you must follow:
- Copy values EXACTLY as they appear in the document. Do not tidy, translate,
  reformat or complete them.
- If the document does not state a value, return "" for it. An empty answer is
  correct and useful; a guessed one goes onto a form somebody signs under
  penalty of perjury.
- Never infer a value from another value. The hotel's address is the address
  printed for the hotel, not one you know.

Reply with JSON holding exactly the requested keys and nothing else."""

_ASKS = {
    "accommodation": "the name of the hotel or the person the applicant is staying with",
    "accommodation_address": "the full street address of that accommodation",
    "employer": "the employer's name, address and telephone number as printed",
    "occupation": "the applicant's job title",
}


def _squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def grounded(value: str, source_text: str) -> bool:
    """Is this value actually printed in the document it came from?

    Compared with punctuation and whitespace removed, because the same address
    is laid out across several lines on most confirmations. A multi-part value
    passes when a majority of its parts appear — enough to survive real layout,
    far too strict for a value nobody wrote down.
    """
    v = " ".join(str(value or "").split())
    page = _squash(source_text)
    if not v or not page:
        return False
    parts = [p for p in re.split(r"[,\n;]+", v) if _squash(p)]
    if len(parts) < 2:
        return _squash(v) in page
    hits = sum(1 for p in parts if _squash(p) in page)
    return hits >= 2 and hits * 2 >= len(parts)


def _documents(db, application_id: str, doc_types: tuple) -> list:
    rows = db.execute(select(models.StoredDocument).where(
        models.StoredDocument.application_id == application_id).order_by(
        models.StoredDocument.created_at.desc())).scalars().all()
    return [d for d in rows
            if d.doc_type in doc_types
            and not (d.page_classification or {}).get("reject")
            and (d.ocr_text or "").strip()]


def suggest(db, application_id: str, wanted: list[str], *,
            timeout_s: float = 25.0) -> dict:
    """Answers Ellis can read out of this case's own documents.

    Returns {key: {"value", "document", "doc_type"}} for the wanted keys it
    could ground. Keys it cannot are simply absent — the applicant is asked
    those, which is the honest outcome, not a failure.
    """
    from .config import settings
    s = settings()
    if not (s.moonshot_api_key and s.kimi_enabled):
        return {}
    # Group the wanted keys by the document that would hold them, so one
    # document is read once for everything it can answer.
    by_doc: dict[tuple, list[str]] = {}
    for key in wanted:
        types = SOURCES.get(key)
        if types:
            by_doc.setdefault(types, []).append(key)
    if not by_doc:
        return {}

    from .providers.kimi import LiveKimiProvider
    out: dict = {}
    for doc_types, keys in by_doc.items():
        for doc in _documents(db, application_id, doc_types):
            text = (doc.ocr_text or "")[:6000]
            asks = "\n".join(f"- {k}: {_ASKS.get(k, k)}" for k in keys)
            user = (f"Document type: {doc.doc_type}\n\nExtract:\n{asks}\n\n"
                    f"--- DOCUMENT TEXT ---\n{text}")
            try:
                got = LiveKimiProvider()._chat(EXTRACT_SYSTEM, user,
                                               timeout=timeout_s)
            except Exception:  # noqa: BLE001 — a failed read just means we ask
                continue
            if not isinstance(got, dict):
                continue
            for key in keys:
                if key in out:
                    continue
                value = str(got.get(key) or "").strip()
                # The rule that makes this safe: it has to be IN the document.
                if value and grounded(value, doc.ocr_text or ""):
                    out[key] = {"value": value, "document": doc.name or "",
                                "doc_type": doc.doc_type or ""}
            if all(k in out for k in keys):
                break
    return out
