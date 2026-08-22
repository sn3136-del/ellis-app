"""Grounded renewal: an answer is re-checked against its own official page.

WHY. The Database's fast path answers from Kimi's model memory, and the
14-day "refresh" used to re-ask the same memory — which is how an answer
whose policy changed (Japan's fees, the Philippines going visa-free) could
be renewed, stale, forever. The 2026-08-22 source audit found ten routes
wrong on the verdict itself; every one was a change the model's knowledge
predates. Asking the model again louder does not fix that. Reading the
official page does.

WHAT THIS DOES. Every stored answer carries the URL of the official page it
rests on (source_url, or the human override's source, or the official
portal). A recheck:

  1. fetches that page — government domains only, with the fetching layer's
     honest challenge detection and its render fallback; a blocked page is a
     recorded failure, never guessed around;
  2. asks Kimi to compare the STORED answer against the PAGE TEXT — not
     against its memory — and name any field the page contradicts, quoting
     the page for each;
  3. validates the proposed corrections deterministically (same whitelist and
     contradiction checks as a fresh answer; a correction that introduces a
     contradiction is refused);
  4. applies what survives, stamps the row with when/where/what changed, and
     extends its freshness window;
  5. NEVER edits a field a human verified: if the page now contradicts an
     override, that is filed into the operator queue for a person — a machine
     does not silently outvote a person, in either direction.

Once a row has been grounded, memory-only regeneration is retired for it:
the page outranks memory, so a failed recheck keeps the existing answer
(still honestly marked stale) rather than reverting to what the model
happens to remember today.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .authority import hostname, is_government_host
from .fetching import fetch
from .models import DatabaseIssueReport, KimiRouteGuidanceCache

# Fields the page is allowed to correct — the same vocabulary a human
# override may correct, minus nothing: what a person may fix from a source,
# the machine may PROPOSE from the same source (and gets the extra
# deterministic gate below).
from .verified_overrides import OVERRIDABLE

FETCH_TIMEOUT_SECONDS = 20.0
CALL_TIMEOUT_SECONDS = 45.0
MAX_PAGE_CHARS = 28_000
MAX_SOURCES = 2

_SYSTEM = """You are checking a stored visa-requirements answer against the
OFFICIAL PAGE TEXT provided. Judge ONLY from the page text — never from your
own knowledge. Reply STRICT JSON:
{"page_relevant": true|false  (does this page actually cover this route/topic?),
 "consistent": true|false     (does the page contradict any stored field?),
 "corrected_fields": {field: new value, ...}  (ONLY fields the page text
    contradicts, with the value the page states; use the stored answer's own
    field names and shapes; {} when consistent),
 "evidence": {field: "short quote from the page", ...}  (a quote for EVERY
    corrected field; a correction without a quote will be discarded),
 "note": "one short sentence"}
Rules: if the page does not mention a field, it is NOT a contradiction — leave
it alone. Never invent a fee, date or URL the page does not state. If the page
is irrelevant or unreadable, say page_relevant false and change nothing."""

_PROVIDER = None


def set_provider(fn) -> None:
    """Tests inject callable(system, user) -> dict. None resets to live Kimi."""
    global _PROVIDER
    _PROVIDER = fn


def _call(system: str, user: str) -> dict:
    if _PROVIDER is not None:
        return _PROVIDER(system, user)
    from . import kimi_primary
    return kimi_primary._live_call(system, user, timeout=CALL_TIMEOUT_SECONDS,
                                   max_tokens=6000)


def _now():
    return datetime.now(timezone.utc)


def candidate_sources(guidance: dict, override: dict | None) -> list[str]:
    """The official pages this answer rests on: the human override's source
    first (a person chose it), then the answer's own source, then the portal.
    Government domains only; order-preserving dedupe."""
    urls = []
    if override:
        urls.append(override.get("source_url") or "")
    g = guidance or {}
    urls.append(str(g.get("source_url") or ""))
    urls.append(str(g.get("official_portal_url") or ""))
    out, seen = [], set()
    for u in urls:
        u = u.strip()
        if not u or u in seen or not u.lower().startswith(("http://", "https://")):
            continue
        if not is_government_host(hostname(u)):
            continue
        seen.add(u)
        out.append(u)
    return out[:MAX_SOURCES]


def _stamp(row, entry: dict) -> None:
    ver = dict(row.verification or {})
    ver["grounded_check"] = entry
    row.verification = ver


def recheck_row(db, row, *, today: str | None = None) -> dict:
    """Ground one cached answer against its official page. Returns an honest
    report dict; commits its own changes. Never raises for a page problem —
    a failed fetch is a recorded outcome, not an exception."""
    from . import kimi_primary
    from . import verified_overrides

    route = dict(row.route or {})
    override = verified_overrides.find(route)
    guidance = dict(row.guidance or {})
    sources = candidate_sources(guidance, override)
    when = today or _now().isoformat()

    if not sources:
        _stamp(row, {"at": when, "outcome": "no_official_source",
                     "note": "the answer names no government page to check"})
        db.commit()
        return {"outcome": "no_official_source", "route_key": row.cache_key}

    page = None
    for url in sources:
        fr = fetch(url, timeout_seconds=FETCH_TIMEOUT_SECONDS)
        if fr.ok and fr.content_text and not fr.challenge \
                and is_government_host(fr.final_hostname):
            page = fr
            break
    if page is None:
        _stamp(row, {"at": when, "outcome": "fetch_failed", "sources": sources})
        db.commit()
        return {"outcome": "fetch_failed", "route_key": row.cache_key,
                "sources": sources}

    payload = {
        "route": {k: route.get(k) for k in
                  ("passport_nationality", "destination_country",
                   "travel_purpose", "travel_document_type")},
        "stored_answer": {k: guidance.get(k) for k in OVERRIDABLE
                          if k in guidance},
        "official_page_url": page.final_url,
        "official_page_text": page.content_text[:MAX_PAGE_CHARS],
    }
    try:
        raw = _call(_SYSTEM, json.dumps(payload, ensure_ascii=False))
    except Exception as e:  # noqa: BLE001 — provider trouble is an outcome
        _stamp(row, {"at": when, "outcome": "provider_error",
                     "source_url": page.final_url, "error": str(e)[:160]})
        db.commit()
        return {"outcome": "provider_error", "route_key": row.cache_key}

    if not isinstance(raw, dict) or not raw.get("page_relevant"):
        _stamp(row, {"at": when, "outcome": "page_not_relevant",
                     "source_url": page.final_url,
                     "content_hash": page.content_hash})
        db.commit()
        return {"outcome": "page_not_relevant", "route_key": row.cache_key}

    # Corrections: whitelisted, quote-backed, override-protected.
    proposed = raw.get("corrected_fields") or {}
    evidence = raw.get("evidence") or {}
    proposed = {k: v for k, v in proposed.items()
                if k in OVERRIDABLE and str(evidence.get(k) or "").strip()}
    protected = set((override or {}).get("fields") or {})
    disputed = {}
    applied = {}
    for k, v in proposed.items():
        if k in protected:
            if v != (override or {}).get("fields", {}).get(k):
                disputed[k] = v          # page vs human — a person decides
        else:
            applied[k] = v

    if applied:
        merged = dict(guidance)
        merged.update(applied)
        clean, missing, contradictions = kimi_primary.validate_answer(merged)
        if contradictions or missing:
            # A correction that makes the answer contradict itself is refused
            # wholesale — the operator queue gets it instead.
            disputed.update(applied)
            applied = {}
        else:
            row.guidance = clean

    if disputed:
        note = "; ".join(f"{k}: page says {json.dumps(disputed[k], ensure_ascii=False)[:120]}"
                         f" (quote: {str(evidence.get(k) or '')[:160]})"
                         for k in sorted(disputed))
        db.add(DatabaseIssueReport(
            org_id="platform", cache_key=row.cache_key, route=route,
            field=",".join(sorted(disputed))[:64],
            note=(f"Automatic source check against {page.final_url}: " + note)[:1000],
            reported_by="freshness_monitor", status="open"))

    consistent = bool(raw.get("consistent")) and not proposed
    _stamp(row, {
        "at": when, "outcome": "checked",
        "source_url": page.final_url, "content_hash": page.content_hash,
        "consistent": consistent,
        "changed_fields": sorted(applied), "disputed_fields": sorted(disputed),
        "note": str(raw.get("note") or "")[:200],
    })
    # A checked answer — confirmed or corrected — is fresh again. A disputed
    # one is NOT extended: it stays due for attention until a person acts.
    if not disputed:
        row.fresh_until = _now() + timedelta(days=kimi_primary.TTL_DAYS)
    db.commit()
    return {"outcome": "checked", "route_key": row.cache_key,
            "consistent": consistent,
            "changed": sorted(applied), "disputed": sorted(disputed),
            "source_url": page.final_url}


def recheck_route(db, route: dict) -> dict | None:
    """Recheck by route (the stale-serving path). None when nothing is cached."""
    from . import kimi_primary
    key = kimi_primary.cache_key(route)
    row = db.execute(select(KimiRouteGuidanceCache).where(
        KimiRouteGuidanceCache.cache_key == key)).scalars().first()
    if row is None:
        return None
    return recheck_row(db, row)


def has_been_grounded(row) -> bool:
    gc = (row.verification or {}).get("grounded_check") or {}
    return gc.get("outcome") == "checked"
