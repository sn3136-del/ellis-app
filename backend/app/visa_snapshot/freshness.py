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
import re
import os
import time
import threading
from pathlib import Path
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .authority import hostname, is_government_host
from .fetching import fetch
from .evidence_validator import (quote_in_text, supports_disposition, jurisdiction_matches,
                                 field_value_supported, route_supporting_excerpt)
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
MAX_SOURCES = 8
ROUTE_BUDGET_SECONDS = 120.0
EVIDENCE_CONTRACT = 2

_SYSTEM = """You are checking a stored visa-requirements answer against the
OFFICIAL PAGE TEXT provided. Judge ONLY from the page text — never from your
own knowledge. Apply only the policy in force on policy_date. A future
announcement is not a correction to the current rule.

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
    corrected OR confirmed unchanged field; only explicitly supported fields
    earn renewed freshness; a correction without a matching quote is discarded),
 "note": "one short sentence"}
Rules: if the page does not mention a field, it is NOT a contradiction — leave
it alone. Never invent a fee, date or URL the page does not state. If the page
is irrelevant or unreadable, say page_relevant false and change nothing.
When in doubt about whether the page speaks for THIS nationality, say
page_is_nationality_specific false and correct nothing nationality-specific."""

_PROVIDER = None
_MODEL_SLOTS = threading.BoundedSemaphore(4)


def set_provider(fn) -> None:
    """Tests inject callable(system, user) -> dict. None resets to live Kimi."""
    global _PROVIDER
    _PROVIDER = fn


def _call(system: str, user: str, *, timeout_seconds: float = CALL_TIMEOUT_SECONDS) -> dict:
    from .bounded_io import call
    from . import kimi_primary
    budget = min(CALL_TIMEOUT_SECONDS, timeout_seconds)
    deadline = time.monotonic() + budget
    provider = _PROVIDER
    return call(lambda: provider(system, user) if provider is not None else
        kimi_primary._live_call(system, user, timeout=max(0.001, deadline - time.monotonic()),
                                max_tokens=6000), budget, _MODEL_SLOTS)


def _now():
    return datetime.now(timezone.utc)


def candidate_sources(guidance: dict, override: dict | None, *, limit: int | None = MAX_SOURCES) -> list[str]:
    """The official pages this answer rests on: the human override's source
    first (a person chose it), then the answer's own source, then the portal.
    Government domains only; order-preserving dedupe."""
    urls = []
    if override:
        urls.append(override.get("source_url") or "")
    g = guidance or {}
    urls.append(str(g.get("source_url") or ""))
    # A headline verdict page cannot attest the fees and conditions carried
    # by other cited pages. Visit their actual field/product provenance too.
    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"source_url", "url", "evidence_url"} and isinstance(item, str):
                    urls.append(item)
                elif key == "source_urls" and isinstance(item, list):
                    urls.extend(u for u in item if isinstance(u, str))
                elif isinstance(item, (dict, list)):
                    collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
    collect(override or {})
    collect(g)
    urls.append(str(g.get("official_portal_url") or ""))
    out, seen = [], set()
    for u in urls:
        if not isinstance(u, str):
            continue
        u = u.strip()
        if not u or u in seen or not u.lower().startswith(("http://", "https://")):
            continue
        if not is_government_host(hostname(u)):
            continue
        seen.add(u)
        out.append(u)
    return out if limit is None else out[:limit]


def _stamp(row, entry: dict) -> None:
    """Retain an earlier good read through outages, but retire a disproved read.
    Reading the same page and finding no route evidence is new evidence;
    failing to read it is not evidence against the previous check."""
    ver = dict(row.verification) if isinstance(row.verification, dict) else {}
    previous = ver.get("last_good_check") or ver.get("grounded_check") or {}
    previous = previous if isinstance(previous, dict) else {}
    irrelevant = set(entry.get("irrelevant_sources") or [])
    if entry.get("outcome") in ("page_not_relevant", "page_not_about_route"):
        irrelevant.update(entry.get("sources_tried") or [])
        if entry.get("source_url"):
            irrelevant.add(entry["source_url"])
    if previous.get("outcome") == "checked" and previous.get("source_url") in irrelevant:
        ver["superseded_check"] = dict(previous)
        ver.pop("last_good_check", None)
    ver["grounded_check"] = entry
    if isinstance(entry.get("source_reads"), int) and not isinstance(entry["source_reads"], bool) and entry["source_reads"] > 0:
        ver["last_source_read_at"] = entry.get("at")
    if entry.get("outcome") == "checked":
        ver["last_good_check"] = dict(entry)
    row.verification = ver


def json_unchanged(db, column, value):
    """Compare a JSON snapshot for guarded writes on SQLite or PostgreSQL."""
    from sqlalchemy import literal, cast, or_
    if db.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import JSONB
        comparison = cast(column, JSONB) == literal(value, type_=JSONB)
    else:
        comparison = column == literal(value, type_=column.type)
    return or_(column.is_(None), comparison) if value is None else comparison


def _commit_recheck(db, row, entry: dict, *, expected_guidance: dict,
                    expected_route: dict, fresh_until=None) -> bool:
    """Merge metadata freshly and commit only if that exact row still exists.

    API and sweep processes have separate memory/locks. A final refresh alone
    still loses writes between SELECT and UPDATE, so the update compares the
    read metadata and the guidance this check actually evaluated. A conflict
    rolls back this attempt's pending issues/corrections/history, without
    retrying evidence against an answer it did not read.
    """
    from types import SimpleNamespace
    from sqlalchemy import update
    from sqlalchemy.orm.attributes import set_committed_value

    model = KimiRouteGuidanceCache
    key, row_id = row.cache_key, row.id
    desired_guidance = row.guidance
    # Column selection bypasses the ORM identity map and never autoflushes
    # the pending raw correction before its compare-and-swap guard.
    with db.no_autoflush:
        latest = db.execute(select(model.verification, model.guidance, model.route,
            model.fresh_until).where(model.id == row_id)).first()
    if (latest is None or latest.guidance != expected_guidance or latest.route != expected_route
            or isinstance(latest.verification, dict) and latest.verification.get("detail_pending")):
        db.rollback()
        if latest is not None:
            db.refresh(row)
        return False
    stamped = SimpleNamespace(verification=latest.verification)
    _stamp(stamped, entry)

    values = {"verification": stamped.verification}
    changed = desired_guidance != expected_guidance
    if changed:
        values["guidance"] = desired_guidance
    if fresh_until is not None:
        values["fresh_until"] = fresh_until
    stmt = update(model).where(model.id == row_id,
        json_unchanged(db, model.verification, latest.verification),
        json_unchanged(db, model.guidance, expected_guidance), json_unchanged(db, model.route, expected_route),
        model.fresh_until == latest.fresh_until).values(**values).execution_options(synchronize_session=False)
    with db.no_autoflush:
        result = db.execute(stmt)
    if result.rowcount != 1:
        db.rollback()
        db.refresh(row)
        return False
    # The guarded UPDATE already wrote these values. Mark them clean so the
    # subsequent ORM commit cannot issue an unconditional stale UPDATE.
    for name, value in values.items():
        set_committed_value(row, name, value)
    if changed:
        from . import change_log
        change_log.record(db, key, expected_route, expected_guidance, desired_guidance,
            origin="grounded_recheck", note=f"corrected against {entry.get('source_url') or ''}")
    db.commit()
    return True


def effective_check(verification: dict | None) -> dict:
    """Latest route-supported reading, or the last one through a failed read."""
    ver = verification if isinstance(verification, dict) else {}
    gc = ver.get("grounded_check") if isinstance(ver.get("grounded_check"), dict) else {}
    if gc.get("outcome") == "checked" and gc.get("evidence_contract") == EVIDENCE_CONTRACT:
        return gc
    good = ver.get("last_good_check") if isinstance(ver.get("last_good_check"), dict) else {}
    return good if good.get("outcome") == "checked" and good.get("evidence_contract") == EVIDENCE_CONTRACT else {}


def last_source_read_at(verification: dict | None):
    """Transport evidence only; it never supplies a policy verification stamp."""
    ver = verification if isinstance(verification, dict) else {}
    check = ver.get("grounded_check") if isinstance(ver.get("grounded_check"), dict) else {}
    if isinstance(check.get("source_reads"), int) and not isinstance(check["source_reads"], bool) and check["source_reads"] > 0:
        return check.get("at")
    return ver.get("last_source_read_at") or effective_check(ver).get("at")


def _page_has_visa_topic(text: str) -> bool:
    return bool(re.search(r"visa|travel authori[sz]|\beta\b|\besta\b|免签|免簽|签证|簽證|thị thực", text or "", re.I))


def _quoted_proposals(raw: dict, text: str) -> tuple[dict, dict, list[str]]:
    fields = raw.get("corrected_fields") or {}
    evidence = raw.get("evidence") or {}
    if not isinstance(fields, dict):
        fields = {}
    if not isinstance(evidence, dict):
        evidence = {}
    quoted, unquoted = {}, []
    for k, v in fields.items():
        if k not in OVERRIDABLE:
            continue
        if (not isinstance(evidence.get(k), str) or not quote_in_text(evidence[k], text)
                or (v not in (None, "", [], {}) and not field_value_supported(k, v, evidence[k]))):
            unquoted.append(k)
        else:
            quoted[k] = v
    return quoted, evidence, sorted(unquoted)


def _supports_route(page_text: str, raw: dict, route: dict, guidance: dict,
                    proposed: dict, source_url: str = "", policy_date: str = "") -> bool:
    """Silence, generic pages and the model's relevance flag cannot renew a row.
    A sourced NEW verdict is eligible too, so a wrong stored verdict can be
    corrected instead of making its own correction impossible."""
    from . import kimi_primary
    nationality = str(route.get("passport_nationality") or "")
    if not nationality or raw.get("page_relevant") is not True \
            or raw.get("page_is_nationality_specific") is not True:
        return False
    verdict = proposed.get("disposition", guidance.get("disposition"))
    detail = proposed.get("requirement_detail")
    if detail and "disposition" not in proposed:
        verdict = next((d for d, details in kimi_primary.DETAIL_FAMILY.items()
                        if detail in details), verdict)
    statement = route_supporting_excerpt(page_text, verdict, route, policy_date=policy_date)
    # A narrowly structured outbound table may scope its rows with a heading,
    # e.g. 'Visa requirements for HKSAR passport holders. Vietnam: visa required'.
    # Never join an unrelated nationality-bearing sentence to that heading.
    if not statement and source_url and jurisdiction_matches(source_url, nationality):
        from .evidence_validator import _NATIONALITY_NAMES
        nat_names = _NATIONALITY_NAMES.get(nationality, ())
        dest_names = _NATIONALITY_NAMES.get(route.get("destination_country", ""), ())
        for nat_name in nat_names:
            heading = re.search(r"Visa requirements for\s+" + re.escape(nat_name.strip()) +
                                r"(?: SAR)? passport holders[.:\n]", page_text, re.I)
            if not heading:
                continue
            for dest_name in dest_names:
                row_match = re.search(r"(?:^|[.\n])\s*" + re.escape(dest_name.strip()) +
                                     r"\s*:\s*(?:no visa required|visa required|visa[- ]free|visa[- ]exempt)\s*[.\n]",
                                     page_text[heading.end():], re.I)
                if row_match:
                    combined = nationality + " " + nat_name.strip() + " passport holders: " + row_match.group().strip(".\n ")
                    statement = route_supporting_excerpt(combined, verdict, route, policy_date=policy_date)
    if not statement:
        return False
    if not source_url or jurisdiction_matches(source_url, route.get("destination_country", "")):
        return True
    # Home-government outbound guidance can settle its citizens' route too.
    # It must explicitly name the traveller group and the DESTINATION in the
    # supporting statement, so an inbound rule for visitors to that government
    # cannot be recycled as a rule for travelling elsewhere.
    from .evidence_validator import _NATIONALITY_NAMES
    if not jurisdiction_matches(source_url, nationality):
        return False
    lower = statement.casefold()
    names = _NATIONALITY_NAMES.get(nationality, ())
    if not any(re.search(re.escape(n.strip()) + r".{0,25}(?:passport|citizen|national)|(?:passport|citizen|national).{0,25}" + re.escape(n.strip()), lower)
               for n in names):
        return False
    destination_names = _NATIONALITY_NAMES.get(route.get("destination_country", ""), ())
    return any(re.search(r"(?<![a-z])" + re.escape(n.strip()) + r"(?![a-z])", statement, re.I)
               for n in destination_names)


def _source_authority_matches(url: str, route: dict) -> bool:
    return (jurisdiction_matches(url, route.get("destination_country", "")) or
            jurisdiction_matches(url, route.get("passport_nationality", "")))


def _file_dispute(db, row, route, guidance, fields, evidence, source_url, when):
    if not fields:
        return
    field_key = ",".join(sorted(fields))[:64]
    proposal = {"source_url": source_url, "checked_at": when,
                "fields": {k: {"page_says": v, "record_holds": guidance.get(k),
                               "quote": str(evidence.get(k) or "")}
                           for k, v in sorted(fields.items())}}
    note = (f"Automatic source check against {source_url}: " + "; ".join(
        f"{k}: page says {json.dumps(v, ensure_ascii=False)[:120]} "
        f"(quote: {str(evidence.get(k) or '')[:160]})" for k, v in sorted(fields.items())))[:1000]
    existing = db.execute(select(DatabaseIssueReport).where(
        DatabaseIssueReport.cache_key == row.cache_key,
        DatabaseIssueReport.reported_by == "freshness_monitor",
        DatabaseIssueReport.field == field_key,
        DatabaseIssueReport.status.in_(("open", "acknowledged")))).scalars().first()
    if existing is not None:
        existing.note, existing.proposal = note, proposal
    else:
        db.add(DatabaseIssueReport(org_id="platform", cache_key=row.cache_key,
                                   route=route, field=field_key, note=note,
                                   reported_by="freshness_monitor", status="open",
                                   proposal=proposal))


def _future_scheduled_source(url: str, route: dict, policy_date: str) -> bool:
    """Known future schedules cannot rewrite today's raw canonical answer."""
    from . import scheduled_policies as scheduled
    on = scheduled._date(policy_date) or scheduled._today()
    route_key = (route.get("passport_nationality"), route.get("destination_country"),
                 route.get("travel_purpose", "tourism"), route.get("travel_document_type", "ordinary_passport"))
    normalized = str(url).split("?")[0].rstrip("/")
    matches = [p for p in scheduled._load() if scheduled._key(p["route"]) == route_key and
               normalized in {str(p["source_url"]).split("?")[0].rstrip("/"),
                              str(p["evidence"]["source_url"]).split("?")[0].rstrip("/")}]
    return bool(matches) and all(scheduled._date(p["effective_from"]) > on for p in matches)


def recheck_row(db, row, *, today: str | None = None, budget_seconds: float | None = None,
                should_stop=None) -> dict:
    """Read a canonical answer's actual source. Only route-supported, fully
    quoted and consistent corrections can renew it. All other outcomes retain
    the answer without claiming verification or inventing replacement facts."""
    from . import kimi_primary, verified_overrides
    if not kimi_primary.is_canonical_key(row.cache_key):
        return {"outcome": "noncanonical", "route_key": row.cache_key}
    if (row.verification or {}).get("detail_pending"):
        return {"outcome": "detail_pending", "route_key": row.cache_key}
    route, original = dict(row.route or {}), dict(row.guidance or {})
    override = verified_overrides.find(route)
    guidance, _ = verified_overrides.apply(dict(original), route)
    all_sources = candidate_sources(guidance, override, limit=None)
    previous = (row.verification or {}).get("grounded_check") or {}
    cursor = previous.get("source_cursor", 0)
    cursor = cursor if isinstance(cursor, int) and cursor >= 0 else 0
    offset = cursor % len(all_sources) if all_sources else 0
    ordered = all_sources[offset:] + all_sources[:offset]
    sources = ordered[:MAX_SOURCES]
    route_budget = ROUTE_BUDGET_SECONDS if budget_seconds is None else max(0.0, min(ROUTE_BUDGET_SECONDS, budget_seconds))
    deadline = time.monotonic() + route_budget
    when = today or _now().isoformat()
    if not sources:
        if not _commit_recheck(db, row, {"at": when, "outcome": "no_official_source",
                "note": "the answer names no government page to check"},
                expected_guidance=original, expected_route=route):
            return {"outcome": "concurrent_change", "route_key": row.cache_key, "changed": []}
        return {"outcome": "no_official_source", "route_key": row.cache_key}

    tried, irrelevant, unquoted_all = [], [], set()
    readings, source_checks = [], []
    visited, completed = set(), set()
    page = raw = None
    fallback = None
    for url in sources:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or (should_stop is not None and should_stop()):
            break
        visited.add(url)
        fr = fetch(url, timeout_seconds=min(FETCH_TIMEOUT_SECONDS, remaining),
                   total_timeout_seconds=min(FETCH_TIMEOUT_SECONDS, remaining))
        if not (fr.ok and fr.content_text and not fr.challenge
                and is_government_host(fr.final_hostname)):
            source_checks.append({"source_url": url, "outcome": "fetch_failed", "at": when})
            continue
        if fr.final_url in completed:
            continue
        completed.add(fr.final_url)
        tried.append(fr.final_url)
        if (not _source_authority_matches(fr.final_url, route) or
                _future_scheduled_source(fr.final_url, route, when[:10])):
            irrelevant.extend((url, fr.final_url))
            source_checks.append({"source_url": fr.final_url, "outcome": "scope_or_effective_date_mismatch", "at": when})
            continue
        if not _page_has_visa_topic(fr.content_text):
            irrelevant.extend((url, fr.final_url))
            source_checks.append({"source_url": fr.final_url, "outcome": "page_not_relevant", "at": when})
            continue
        payload = {"route": {k: route.get(k) for k in
                              ("passport_nationality", "destination_country",
                               "travel_purpose", "travel_document_type")},
                   "policy_date": when[:10],
                   "stored_answer": {k: guidance[k] for k in OVERRIDABLE if k in guidance},
                   "official_page_url": fr.final_url,
                   "official_page_text": fr.content_text[:MAX_PAGE_CHARS]}
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or (should_stop is not None and should_stop()):
                visited.discard(url)
                break
            answer = _call(_SYSTEM, json.dumps(payload, ensure_ascii=False), timeout_seconds=remaining)
        except Exception as e:
            source_checks.append({"source_url": fr.final_url, "outcome": "provider_error", "at": when,
                                  "error": str(e)[:160]})
            continue
        if not isinstance(answer, dict):
            answer = {}
        quoted, evidence, unquoted = _quoted_proposals(answer, fr.content_text[:MAX_PAGE_CHARS])
        unquoted_all.update(unquoted)
        supported = _supports_route(fr.content_text[:MAX_PAGE_CHARS], answer, route, guidance, quoted, fr.final_url, when[:10])
        source_checks.append({"source_url": fr.final_url, "outcome": "checked" if supported else "page_not_relevant",
                              "at": when, "content_hash": fr.content_hash, "verified_fields": [],
                              "unquoted_fields": unquoted,
                              "proposed_fields": {k: {"value": v, "quote": evidence[k]} for k, v in quoted.items()}})
        if supported:
            if page is None:
                page, raw = fr, answer
            readings.append((fr, answer, quoted, evidence, source_checks[-1]))
            continue
        irrelevant.extend((url, fr.final_url))
        # A generic page cannot change THIS route, but its discrepancies need
        # an operator, even if another candidate source can confirm the route.
        if quoted:
            _file_dispute(db, row, route, guidance, quoted, evidence, fr.final_url, when)
            fallback = (fr, quoted)

    # Advance by actual attempts. Advancing by the selected eight URLs could
    # return to the same offset forever when a slow first page used the budget.
    source_cursor = (offset + max(1, len(visited))) % len(all_sources) if all_sources else 0
    if page is None:
        if should_stop is not None and should_stop():
            outcome = "cancelled"
        elif time.monotonic() >= deadline:
            outcome = "budget_exhausted"
        else:
            outcome = "provider_error" if any(s["outcome"] == "provider_error" for s in source_checks) else ("page_not_relevant" if tried else "fetch_failed")
        entry = {"at": when, "outcome": outcome, "sources": sources,
                 "sources_tried": tried, "irrelevant_sources": irrelevant,
                 "source_reads": len(tried),
                 "source_fetch_failures": sum(s["outcome"] == "fetch_failed" for s in source_checks),
                 "unquoted_fields": sorted(unquoted_all), "source_checks": source_checks,
                 "source_cursor": source_cursor, "unchecked_sources": [u for u in all_sources if u not in visited]}
        if not _commit_recheck(db, row, entry, expected_guidance=original, expected_route=route):
            return {"outcome": "concurrent_change", "route_key": row.cache_key, "changed": []}
        return {**entry, "route_key": row.cache_key, "changed": [],
                "disputed": sorted(fallback[1]) if fallback else [],
                "generic_skipped": sorted(set(fallback[1]) & NATIONALITY_SPECIFIC) if fallback else []}

    proposed, evidence, field_sources, conflicts = {}, {}, {}, set()
    unquoted = sorted(unquoted_all)
    for fr, answer, quoted, quotes, check in readings:
        for key, value in quoted.items():
            if key in proposed and proposed[key] != value:
                conflicts.add(key)
            proposed[key], evidence[key], field_sources[key] = value, quotes[key], fr.final_url
    # The source read can take a minute. Refresh both the row and the override,
    # and refuse to overwrite any field changed during that time.
    db.refresh(row)
    if (row.verification or {}).get("detail_pending"):
        return {"outcome": "detail_pending", "route_key": row.cache_key}
    override = verified_overrides.find(route)
    protected = set((override or {}).get("fields") or {})
    current = dict(row.guidance or {})
    seen, _ = verified_overrides.apply(dict(current), route)
    disputed, applied = {}, {}
    for k, v in proposed.items():
        if k in conflicts:
            disputed[k] = v
            continue
        if v == seen.get(k):
            continue
        if k in protected or k in conflicts or v in (None, "", [], {}) or current.get(k) != original.get(k):
            disputed[k] = v
        else:
            applied[k] = v
    if applied:
        candidate = {**current, **applied}
        merged, _ = verified_overrides.apply(dict(candidate), route)
        clean, missing, contradictions = kimi_primary.validate_answer(merged)
        contradictions = list(contradictions) + kimi_primary.serve_time_invariants(merged)
        # Validation may discard malformed fields. Never write the rejected
        # original value into raw guidance while stamping it as corrected.
        rejected = any(k not in clean or clean[k] != v for k, v in applied.items())
        if contradictions or missing or rejected:
            disputed.update(applied)
            applied = {}
        else:
            row.guidance = candidate  # only page corrections, never override copies
            seen = merged
    for source_url in {field_sources.get(k, page.final_url) for k in disputed}:
        own_disputes = {k: v for k, v in disputed.items() if field_sources.get(k, page.final_url) == source_url}
        _file_dispute(db, row, route, seen, own_disputes, evidence, source_url, when)
    remaining_contradictions = kimi_primary.serve_time_invariants(seen)
    consistent = (all(answer.get("consistent") is True for _, answer, _, _, _ in readings) and not proposed and not unquoted
                  and not disputed and fallback is None and not remaining_contradictions)
    # Verifying the verdict does not verify every accompanying fee, duration,
    # document and condition. Renew the whole row only with explicit evidence
    # for every substantive populated field; existing old provenance does not
    # make an unmentioned detail newly checked today.
    metadata_fields = {"confidence", "source_url", "official_portal_url", "corroborating_sources", "unpublished_fields"}
    substantive = {k for k in OVERRIDABLE - metadata_fields if seen.get(k) not in (None, "", [], {})}
    verified_fields, verified_field_sources = set(), {}
    for fr, answer, quoted, quotes, check in readings:
        supported_fields = {k for k in substantive if isinstance(quotes.get(k), str)
                            and quote_in_text(quotes[k], fr.content_text[:MAX_PAGE_CHARS])
                            and field_value_supported(k, seen[k], quotes[k])}
        verified_fields.update(supported_fields)
        if _supports_route(fr.content_text, answer, route, seen, {}, fr.final_url, when[:10]):
            supported_fields.add("disposition")
            verified_fields.add("disposition")
            excerpt = route_supporting_excerpt(fr.content_text, seen.get("disposition"), route, policy_date=when[:10])
            # Outbound tables bind their literal row to a separate literal
            # applicant heading. Preserve both, never invent a joined quote.
            if not excerpt:
                from .evidence_validator import _NATIONALITY_NAMES
                dest_names = _NATIONALITY_NAMES.get(route.get("destination_country"), ())
                for name in dest_names:
                    hit = re.search(r"(?:^|[.\n])\s*" + re.escape(name.strip()) + r"\s*:[^.\n]+", fr.content_text, re.I)
                    if hit:
                        excerpt = hit.group().strip(".\n ")
                        break
            verified_field_sources["disposition"] = {"source_url": fr.final_url, "checked_at": when, "quote": excerpt}
            heading = re.search(r"Visa requirements for\s+[^.\n]{1,80} passport holders", fr.content_text, re.I)
            if heading and not route_supporting_excerpt(fr.content_text, seen.get("disposition"), route, policy_date=when[:10]):
                verified_field_sources["disposition"]["scope_quote"] = heading.group()
        check["verified_fields"] = sorted(supported_fields)
        for key in supported_fields:
            if key != "disposition":
                verified_field_sources[key] = {"source_url": fr.final_url, "checked_at": when, "quote": quotes[key]}
    unverified_fields = sorted(substantive - verified_fields)
    unchecked_sources = [u for u in all_sources if u not in visited]
    failed_sources = any(c["outcome"] in {"fetch_failed", "provider_error"} for c in source_checks)
    renewed = (not (disputed or unquoted or fallback or remaining_contradictions or row.missing_fields
                    or unverified_fields or unchecked_sources or failed_sources
                    or active_disputed_fields(db, row.cache_key)) and (consistent or bool(applied)))
    entry = {"at": when, "outcome": "checked", "evidence_contract": EVIDENCE_CONTRACT,
                 "source_reads": len(tried),
                 "source_fetch_failures": sum(s["outcome"] == "fetch_failed" for s in source_checks),
                 "source_url": page.final_url,
                 "content_hash": page.content_hash, "consistent": consistent,
                 "changed_fields": sorted(applied), "disputed_fields": sorted(disputed),
                 "unquoted_fields": unquoted, "irrelevant_sources": irrelevant,
                 "verified_fields": sorted(verified_fields), "unverified_fields": unverified_fields,
                 "field_sources": verified_field_sources, "source_checks": source_checks,
                 "source_cursor": source_cursor, "unchecked_sources": unchecked_sources,
                 "renewed": renewed,
                 "generic_page_skipped": sorted(fallback[1]) if fallback else [],
                 "note": str(raw.get("note") or "")[:200]}
    # No unresolved proposal, fabricated quote, missing cell or unexplained
    # disagreement can silently extend the freshness promise.
    fresh_until = None
    if renewed:
        ttl = (kimi_primary.TTL_DAYS if row.status == kimi_primary.STATUS_PRIMARY
               else kimi_primary.UNCERTAIN_TTL_DAYS)
        fresh_until = _now() + timedelta(days=ttl)
    if not _commit_recheck(db, row, entry, expected_guidance=current, expected_route=route,
            fresh_until=fresh_until):
        return {"outcome": "concurrent_change", "route_key": row.cache_key, "changed": [], "disputed": []}
    return {"outcome": "checked", "route_key": row.cache_key, "consistent": consistent,
            "source_reads": entry["source_reads"], "source_fetch_failures": entry["source_fetch_failures"],
            "changed": sorted(applied), "disputed": sorted(disputed),
            "generic_skipped": sorted(fallback[1]) if fallback else [],
            "unquoted_fields": unquoted, "source_url": page.final_url}


def note_unreadable(db, row, outcome: dict | None) -> None:
    """A stale answer whose official page could not be read is never
    regenerated from the model's memory. The answer stays, marked stale, and
    ONE open issue tells a person which page failed and why. Refreshed in
    place on every attempt, never stacked."""
    from sqlalchemy import select as _sel
    o = dict(outcome or {})
    # A fetched page that lacks this route's proof is an evidence gap, not
    # a transport outage. Never create a misleading source_unreadable issue.
    if o.get("outcome") != "fetch_failed" or o.get("source_reads", 0):
        return
    reason = str(o.get("outcome") or "unreadable")
    pages = o.get("sources") or o.get("sources_tried") or []
    note = (f"The official page could not be read ({reason}), so the answer "
            f"was not renewed. Pages tried: {', '.join(str(p) for p in pages)[:400]}")
    existing = db.execute(_sel(DatabaseIssueReport).where(
        DatabaseIssueReport.cache_key == row.cache_key,
        DatabaseIssueReport.reported_by == "freshness_monitor",
        DatabaseIssueReport.field == "source_unreadable",
        DatabaseIssueReport.status.in_(("open", "acknowledged")))).scalars().first()
    if existing is not None:
        existing.note = note[:1000]
    else:
        db.add(DatabaseIssueReport(
            org_id="platform", cache_key=row.cache_key, route=dict(row.route or {}),
            field="source_unreadable", note=note[:1000],
            reported_by="freshness_monitor", status="open"))
    db.commit()


def recheck_route(db, route: dict) -> dict | None:
    """Recheck by route (the stale-serving path). None when nothing is cached."""
    from . import kimi_primary
    key = kimi_primary.canonical_key(kimi_primary.cache_key(route))
    row = db.execute(select(KimiRouteGuidanceCache).where(
        KimiRouteGuidanceCache.cache_key == key)).scalars().first()
    if row is None:
        return None
    return recheck_row(db, row)


def has_been_grounded(row) -> bool:
    return bool(effective_check(row.verification))


def due_rows(db, *, older_than_hours: int = 48, limit: int = 400) -> list:
    """The cached answers whose last grounded check is older than the cycle,
    oldest first — the worklist for the automatic 48-hour sweep. A row never
    checked sorts first of all. Transit (via:) variants are skipped: the
    canonical row carries the route's facts and the variants inherit its
    corrections at read time through the same guidance fields."""
    from datetime import datetime, timedelta, timezone
    from . import kimi_primary
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=older_than_hours)
    out = []
    for row in db.execute(select(KimiRouteGuidanceCache)).scalars():
        key = row.cache_key or ""
        verification = row.verification if isinstance(row.verification, dict) else {}
        if not kimi_primary.is_canonical_key(key) or verification.get("detail_pending"):
            continue
        gc = verification.get("grounded_check") if isinstance(verification.get("grounded_check"), dict) else {}
        try:
            at = datetime.fromisoformat(str(gc.get("at") or "").replace("Z", "+00:00"))
            at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
            if at > now:
                at = None
        except (TypeError, ValueError, OverflowError):
            at = None
        if at is None or at < cutoff:
            out.append((at, row))
    out.sort(key=lambda pair: pair[0] or datetime.min.replace(tzinfo=timezone.utc))
    return [row for _at, row in out[:limit]]


def propose_for_issue(db, issue_id: str) -> dict | None:
    """A human flagged a record, so Ellis checks the official page and
    attaches what it found as a PROPOSAL on the issue: page value, stored
    value and verbatim quote per field, for an operator to accept or
    decline in the correction queue. Nothing is changed here — a flag plus
    a page reading is still a proposal until a person accepts it."""
    from . import kimi_primary, verified_overrides
    from .models import DatabaseIssueReport
    issue = db.get(DatabaseIssueReport, issue_id)
    if issue is None or issue.reported_by == "freshness_monitor":
        return None
    route = dict(issue.route or {})
    expected_key = kimi_primary.canonical_key(kimi_primary.cache_key(route))
    if issue.cache_key and kimi_primary.canonical_key(issue.cache_key) != expected_key:
        issue.proposal = {"outcome": "route_identity_mismatch", "checked_at": _now().isoformat()}
        db.commit()
        return issue.proposal
    row = db.execute(select(KimiRouteGuidanceCache).where(
        KimiRouteGuidanceCache.cache_key ==
        kimi_primary.canonical_key(issue.cache_key or kimi_primary.cache_key(route)))).scalars().first()
    if row is None or (row.verification or {}).get("detail_pending"):
        return None
    guidance = dict(row.guidance or {})
    override = verified_overrides.find(route)
    if override:
        guidance, _ = verified_overrides.apply(guidance, route)
    when = _now().isoformat()
    outcome = "page_unreachable"
    proposal = None
    deadline = time.monotonic() + ROUTE_BUDGET_SECONDS
    requested = {"visa_requirement": "disposition", "visa_fee_amount": "government_fee",
                 "visa_fee_currency": "government_fee", "visa_type": "visa_category",
                 "application_method": "application_channel", "stay_duration": "permitted_stay"}.get(issue.field, issue.field)
    for url in candidate_sources(guidance, override):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        fr = fetch(url, timeout_seconds=min(FETCH_TIMEOUT_SECONDS, remaining),
                   total_timeout_seconds=min(FETCH_TIMEOUT_SECONDS, remaining))
        if not (fr.ok and fr.content_text and not fr.challenge
                and is_government_host(fr.final_hostname)):
            continue
        if (not _source_authority_matches(fr.final_url, route) or
                _future_scheduled_source(fr.final_url, route, when[:10])):
            outcome = "page_not_relevant"
            continue
        payload = {
            "policy_date": when[:10],
            "route": {k: route.get(k) for k in
                      ("passport_nationality", "destination_country",
                       "travel_purpose", "travel_document_type")},
            "flag_from_reader": {"field": issue.field, "note": issue.note},
            "stored_answer": {k: guidance.get(k) for k in OVERRIDABLE
                              if k in guidance},
            "official_page_url": fr.final_url,
            "official_page_text": fr.content_text[:MAX_PAGE_CHARS],
        }
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            raw = _call(_SYSTEM, json.dumps(payload, ensure_ascii=False), timeout_seconds=remaining)
        except Exception as e:  # noqa: BLE001 - an outcome, not a crash
            outcome = "provider_error"
            issue.proposal = {"outcome": outcome, "checked_at": when,
                              "error": str(e)[:160]}
            db.commit()
            return issue.proposal
        if not isinstance(raw, dict):
            outcome = "provider_error"
            continue
        quoted, evidence, unquoted = _quoted_proposals(raw, fr.content_text[:MAX_PAGE_CHARS])
        supported = _supports_route(fr.content_text[:MAX_PAGE_CHARS], raw, route, guidance, quoted, fr.final_url, when[:10])
        if not supported:
            outcome = "page_not_relevant"
            continue
        fields = {k: {"page_says": v, "record_holds": guidance.get(k),
                      "quote": evidence[k]} for k, v in quoted.items()}
        confirmed = (requested == "disposition" or
                     isinstance(evidence.get(requested), str)
                     and quote_in_text(evidence[requested], fr.content_text[:MAX_PAGE_CHARS])
                     and field_value_supported(requested, guidance.get(requested), evidence[requested]))
        proposal = {"outcome": "checked", "source_url": fr.final_url,
                    "checked_at": when,
                    "consistent": confirmed and raw.get("consistent") is True and not fields and not unquoted,
                    "verified_fields": [requested] if confirmed else [],
                    "unquoted_fields": unquoted, "fields": fields,
                    "note": str(raw.get("note") or "")[:200],
                    "proposed_by": "ellis-ai"}
        if fields or confirmed:
            break  # a proposal cites this exact source; no mixed-source claims
    issue.proposal = proposal or {"outcome": outcome, "checked_at": when}
    db.commit()
    return issue.proposal


def audit_integrity(db) -> dict:
    """Check every canonical served answer, independent of model/page access.
    Refresh one open issue per row so every sweep exposes contradictions
    without growing duplicate queue entries or modifying verified policy."""
    from . import kimi_primary, verified_overrides
    checked = violated = created = resolved = 0
    for row in db.execute(select(KimiRouteGuidanceCache)).scalars():
        if not kimi_primary.is_canonical_key(row.cache_key):
            continue
        checked += 1
        route = row.route if isinstance(row.route, dict) else {}
        if not isinstance(row.guidance, dict) or not isinstance(row.route, dict):
            failures = ["Cached route and guidance must be objects"]
        else:
            guidance, _ = verified_overrides.apply(dict(row.guidance), dict(route))
            failures = kimi_primary.serve_time_invariants(guidance)
        issues = db.execute(select(DatabaseIssueReport).where(
            DatabaseIssueReport.cache_key == row.cache_key,
            DatabaseIssueReport.reported_by == "freshness_monitor",
            DatabaseIssueReport.field == "integrity",
            DatabaseIssueReport.status.in_(("open", "acknowledged")))).scalars().all()
        if not failures:
            for issue in issues:
                prior = issue.proposal if isinstance(issue.proposal, dict) else {}
                if prior.get("outcome") != "integrity_failed":
                    continue  # close only findings this deterministic audit owns
                now = _now()
                issue.status, issue.resolved_by, issue.resolved_at = "corrected", "freshness_monitor", now
                issue.resolution = "The current merged route passes the deterministic integrity checks. Source accuracy and other disputes retain their separate status."
                issue.proposal = {**prior, "resolution_check": {"outcome": "integrity_passed",
                    "checked_at": now.isoformat(), "contradictions": []}}
                resolved += 1
            continue
        violated += 1
        issue = issues[0] if issues else None
        note = ("Deterministic integrity check: " + "; ".join(failures))[:1000]
        proposal = {"outcome": "integrity_failed", "checked_at": _now().isoformat(),
                    "contradictions": failures, "fields": {}}
        if issue is None:
            created += 1
            db.add(DatabaseIssueReport(org_id="platform", cache_key=row.cache_key,
                                       route=route, field="integrity",
                                       note=note, reported_by="freshness_monitor",
                                       status="open", proposal=proposal))
        else:
            issue.note, issue.proposal = note, proposal
    db.commit()
    return {"checked": checked, "violated": violated, "created": created, "resolved": resolved}


def active_disputed_fields(db, cache_key: str) -> list[str]:
    """Material, unresolved monitor findings survive a newer grounding stamp.
    A transient source outage is not a dispute of previously checked facts.
    All other open/acknowledged monitor issues require explicit resolution."""
    from . import kimi_primary
    key = kimi_primary.canonical_key(cache_key)
    issues = db.execute(select(DatabaseIssueReport).where(
        DatabaseIssueReport.cache_key == key,
        DatabaseIssueReport.reported_by == "freshness_monitor",
        DatabaseIssueReport.status.in_(("open", "acknowledged")),
        DatabaseIssueReport.field != "source_unreadable")).scalars()
    fields = set()
    for issue in issues:
        proposed = (issue.proposal or {}).get("fields") or {}
        if isinstance(proposed, dict) and proposed:
            fields.update(proposed)
        else:
            fields.update(f.strip() for f in str(issue.field or "source_dispute").split(",") if f.strip())
    return sorted(fields)



def sweep_status_path() -> Path:
    return Path(os.environ.get("ELLIS_FRESHNESS_STATUS_FILE", "/var/lib/ellis/freshness-sweep-status.json"))


def read_sweep_status() -> dict:
    """Last durable sweep progress, never an invented scheduled/verified time."""
    try:
        result = json.loads(sweep_status_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return result if isinstance(result, dict) else {}
