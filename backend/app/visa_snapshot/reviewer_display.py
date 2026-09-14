"""Display-only reviewer attribution; stored evidence and actor IDs stay exact."""
from __future__ import annotations

import re


_BRANDED_REVIEWER = re.compile(
    r"(?<![a-z0-9])(?:codex|chatgpt|openai|claude)(?:ai)?(?![a-z0-9])", re.IGNORECASE)
_URL_LABEL = re.compile(r"^\s*(?:[a-z][a-z0-9+.-]*://|www\.)", re.IGNORECASE)


def reviewer_label(value, verifier=None):
    """Normalize branded attribution, including compound agent IDs.

    Call only for reviewer/data-source labels, never for policy text, source
    quotations, URLs or raw audit records. A name cannot establish whether a
    person or an AI verified a value; retain a neutral label without metadata.
    """
    if not isinstance(value, str) or _URL_LABEL.search(value) or not _BRANDED_REVIEWER.search(value):
        return value
    return "AI review" if str(verifier or "").strip().lower() == "ai" else "Ellis review"
