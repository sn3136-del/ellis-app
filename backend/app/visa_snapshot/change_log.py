"""Recording what changed in a served answer, for the ops change log.

Trip.com's quality-control backend must show, after every update, WHAT
changed — add / modify / delete, field by field, searchable history, so an
operator can audit an update instead of taking it on faith. This helper is
called at the three places an answer changes (a fresh engine answer, a
grounded-recheck correction, an operator action) and records a compact
field-level diff of the reader-visible fields only. Recording is best-effort
by design: a diff failure must never block the answer itself.
"""
from __future__ import annotations

import json

from .models import DatabaseChangeLog

# The reader-visible surface, in Trip.com's own field terms. Internal
# machinery (verification stamps, model names) is not a "change" to them.
# Every guidance key that can move any of the 25 delivered fields. The diff
# used to watch fourteen, so a change to validity, entries, fee currency, the
# consular district or the entry requirements produced no log entry at all:
# nine of the twenty-five fields could change silently, which is the opposite
# of what a change log is for.
_WATCHED = (
    "disposition", "requirement_detail", "visa_category", "permitted_stay",
    "permitted_stay_days", "application_channel", "application_channel_detail",
    "government_fee", "official_portal_url", "visa_products",
    "processing_time", "required_documents", "exceptions", "confidence",
    # added so the remaining delivered fields are traceable too
    "source_url", "validity", "entries", "arrival_card", "passport_validity",
    "consular_jurisdiction", "entry_requirements", "unpublished_fields",
    "onward_travel_evidence", "accommodation_evidence", "financial_evidence",
    "insurance_required", "biometrics_required", "health_requirements",
    "policy_valid_until",
)


def _norm(v):
    try:
        return json.loads(json.dumps(v, ensure_ascii=False, sort_keys=True))
    except (TypeError, ValueError):
        return str(v)


def diff(old: dict | None, new: dict | None) -> dict:
    """{field: {"from": ..., "to": ...}} over the reader-visible fields."""
    old, new = old or {}, new or {}
    out = {}
    for f in _WATCHED:
        a, b = _norm(old.get(f)), _norm(new.get(f))
        if a != b:
            out[f] = {"from": a, "to": b}
    return out


def record(db, cache_key: str, route: dict, old: dict | None, new: dict | None,
           *, origin: str, note: str = "") -> None:
    """Append one change event; commits with the caller's transaction."""
    try:
        action = "add" if not old else ("delete" if not new else "modify")
        changes = diff(old, new)
        if action == "modify" and not changes:
            return          # nothing a reader can see changed
        db.add(DatabaseChangeLog(
            cache_key=cache_key or "",
            route={k: (route or {}).get(k) for k in (
                "passport_nationality", "destination_country",
                "travel_purpose", "travel_document_type")},
            action=action, origin=origin, changes=changes, note=note[:900]))
    except Exception:  # noqa: BLE001 — the log must never break the answer
        pass
