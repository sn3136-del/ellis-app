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

# Fields whose correct value depends on WHO is travelling. A page that does
# not name this nationality cannot correct these, however confidently it
# describes the destination's general rules.
NATIONALITY_SPECIFIC = frozenset({
    "disposition", "requirement_detail", "visa_category", "permitted_stay",
    "permitted_stay_days", "application_channel", "application_channel_detail",
    "government_fee", "visa_products",
})

FETCH_TIMEOUT_SECONDS = 20.0
CALL_TIMEOUT_SECONDS = 45.0
MAX_PAGE_CHARS = 28_000
MAX_SOURCES = 2

_SYSTEM = """You are checking a stored visa-requirements answer against the
OFFICIAL PAGE TEXT provided. Judge ONLY from the page text — never from your
own knowledge.

CRITICAL — NATIONALITY. Most government pages describe the destination's
rules for the WORLD, or for a different nationality than the one in the
route. Those pages must NOT be used to correct a fact that is specific to
this applicant's nationality. A generic page saying "you may apply at the
embassy, an accredited agency or a visa centre" does NOT contradict a stored
answer saying THIS nationality must use an accredited agency, and a generic
"stay up to 90 days" does NOT contradict a stored nationality-specific 15 or
30 days. Correct such a field ONLY when the page names this nationality (or
the applicant's country) and states the rule for them. When the page is
generic, treat those fields as unaddressed and leave them alone.

MONEY, VALIDITY AND STAY ARE THE HIGHEST-VALUE CHECKS. Government fees and
validity periods change by law and the stored answer's figure may predate the
change: a 2026 audit found a stored GBP 10 where the page said GBP 20, a
stored USD 21 where the fee had become USD 40.27, and a stored 60-day/16-day
e-visa where the rule had been 120/30 for a year. When the page states a fee
amount, a validity period, or a stay length for a product the stored answer
also carries, compare digit for digit — a different number IS a contradiction
and must be corrected with the page's own figure and quote. A single price the
page states for all applicants (an ESTA, an ETA, an e-visa) is not
nationality-specific; a country-wise fee table is, so read THIS nationality's
row and no other.

Reply STRICT JSON:
{"page_relevant": true|false  (does this page actually cover this route/topic?),
 "page_is_nationality_specific": true|false  (does the page state rules FOR
    this applicant's nationality, rather than general/worldwide rules?),
 "consistent": true|false     (does the page contradict any stored field?),
 "corrected_fields": {field: new value, ...}  (ONLY fields the page text
    contradicts, with the value the page states; use the stored answer's own
    field names and shapes; {} when consistent),
 "evidence": {field: "short quote from the page", ...}  (a quote for EVERY
    corrected field; a correction without a quote will be discarded),
 "note": "one short sentence"}
Rules: if the page does not mention a field, it is NOT a contradiction — leave
it alone. Never invent a fee, date or URL the page does not state. If the page
is irrelevant or unreadable, say page_relevant false and change nothing.
When in doubt about whether the page speaks for THIS nationality, say
page_is_nationality_specific false and correct nothing nationality-specific."""

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
    # Compare what customers SEE: the override-merged answer. Comparing the
    # raw engine answer kept re-disputing facts a sourced override already
    # corrects at serve time, so the queue filled with phantom conflicts.
    if override:
        guidance, _ = verified_overrides.apply(guidance, route)
    sources = candidate_sources(guidance, override)
    when = today or _now().isoformat()

    if not sources:
        _stamp(row, {"at": when, "outcome": "no_official_source",
                     "note": "the answer names no government page to check"})
        db.commit()
        return {"outcome": "no_official_source", "route_key": row.cache_key}

    # Walk the sources until one is BOTH readable and actually about this
    # route. A landing page that does not state the rule is not a check: the
    # first Japan recheck stopped at the embassy homepage, called it
    # irrelevant (honestly) and left the route unchecked. Irrelevance is a
    # reason to try the next page, not to give up.
    page = None
    raw = None
    fetched_any = False
    tried = []
    generic_skipped: list = []
    for url in sources:
        fr = fetch(url, timeout_seconds=FETCH_TIMEOUT_SECONDS)
        if not (fr.ok and fr.content_text and not fr.challenge
                and is_government_host(fr.final_hostname)):
            continue
        fetched_any = True
        tried.append(fr.final_url)
        payload = {
            "route": {k: route.get(k) for k in
                      ("passport_nationality", "destination_country",
                       "travel_purpose", "travel_document_type")},
            "stored_answer": {k: guidance.get(k) for k in OVERRIDABLE
                              if k in guidance},
            "official_page_url": fr.final_url,
            "official_page_text": fr.content_text[:MAX_PAGE_CHARS],
        }
        try:
            answer = _call(_SYSTEM, json.dumps(payload, ensure_ascii=False))
        except Exception as e:  # noqa: BLE001 — provider trouble is an outcome
            _stamp(row, {"at": when, "outcome": "provider_error",
                         "source_url": fr.final_url, "error": str(e)[:160]})
            db.commit()
            return {"outcome": "provider_error", "route_key": row.cache_key}
        if isinstance(answer, dict) and answer.get("page_relevant"):
            page, raw = fr, answer
            break

    if not fetched_any:
        _stamp(row, {"at": when, "outcome": "fetch_failed", "sources": sources})
        db.commit()
        return {"outcome": "fetch_failed", "route_key": row.cache_key,
                "sources": sources}
    if page is None:
        # Read, but none of the pages actually state this route's rule. Honest
        # non-answer: nothing is changed and the row is NOT marked fresh, so
        # it stays due for a better source.
        _stamp(row, {"at": when, "outcome": "page_not_relevant",
                     "sources_tried": tried})
        db.commit()
        return {"outcome": "page_not_relevant", "route_key": row.cache_key,
                "sources_tried": tried}

    # Corrections: whitelisted, quote-backed, override-protected.
    proposed = raw.get("corrected_fields") or {}
    evidence = raw.get("evidence") or {}
    proposed = {k: v for k, v in proposed.items()
                if k in OVERRIDABLE and str(evidence.get(k) or "").strip()}
    # A page that does not speak for THIS nationality may not touch a
    # nationality-specific field. This is the Japan failure exactly: the
    # ministry's worldwide page lists every channel and a 90-day stay, which
    # is true in general and wrong for a Chinese applicant. Enforced here and
    # not left to the prompt, because the prompt is a request and this is a
    # rule.
    if not raw.get("page_is_nationality_specific"):
        blocked = [k for k in proposed if k in NATIONALITY_SPECIFIC]
        for k in blocked:
            proposed.pop(k)
        if blocked:
            generic_skipped.extend(sorted(blocked))
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
            from . import change_log
            change_log.record(db, row.cache_key, route,
                              dict(guidance), clean,
                              origin="grounded_recheck",
                              note=f"corrected against {page.final_url}")
            row.guidance = clean

    if disputed:
        note = "; ".join(f"{k}: page says {json.dumps(disputed[k], ensure_ascii=False)[:120]}"
                         f" (quote: {str(evidence.get(k) or '')[:160]})"
                         for k in sorted(disputed))
        held = (override or {}).get("fields") or {}
        db.add(DatabaseIssueReport(
            org_id="platform", cache_key=row.cache_key, route=route,
            field=",".join(sorted(disputed))[:64],
            note=(f"Automatic source check against {page.final_url}: " + note)[:1000],
            reported_by="freshness_monitor", status="open",
            proposal={
                "source_url": page.final_url,
                "checked_at": when,
                "fields": {
                    k: {"page_says": disputed[k],
                        "record_holds": held.get(k, guidance.get(k)),
                        "quote": str(evidence.get(k) or "")}
                    for k in sorted(disputed)
                },
            }))

    consistent = bool(raw.get("consistent")) and not proposed
    _stamp(row, {
        "at": when, "outcome": "checked",
        "source_url": page.final_url, "content_hash": page.content_hash,
        "consistent": consistent,
        "changed_fields": sorted(applied), "disputed_fields": sorted(disputed),
        "generic_page_skipped": sorted(set(generic_skipped)),
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
            "generic_skipped": sorted(set(generic_skipped)),
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


def due_rows(db, *, older_than_hours: int = 48, limit: int = 400) -> list:
    """The cached answers whose last grounded check is older than the cycle,
    oldest first — the worklist for the automatic 48-hour sweep. A row never
    checked sorts first of all. Transit (via:) variants are skipped: the
    canonical row carries the route's facts and the variants inherit its
    corrections at read time through the same guidance fields."""
    from datetime import datetime, timedelta, timezone
    from . import kimi_primary
    cutoff = (datetime.now(timezone.utc)
              - timedelta(hours=older_than_hours)).isoformat()
    out = []
    for row in db.execute(select(KimiRouteGuidanceCache)).scalars():
        key = row.cache_key or ""
        if f"|{kimi_primary.CACHE_VERSION}" not in key or "|via:" in key:
            continue
        gc = (row.verification or {}).get("grounded_check") or {}
        at = str(gc.get("at") or "")
        if not at or at < cutoff:
            out.append((at, row))
    out.sort(key=lambda pair: pair[0])
    return [row for _at, row in out[:limit]]
