"""Federal Register API v1 — a STALENESS ALARM over Ellis's curated facts.

Edition-neutral by construction. This provider knows agency slugs, search terms
and a date; it knows nothing about H1B, portal families, or which edition asked.
The tourist editions point it at State Department rules the same way the H1B
edition points it at USCIS and DOL rules.

What it is for
--------------
`app/h1b/guidance.py` carries curated, human-reviewed government facts (fee
amounts, form editions, filing windows) with an `AS_OF` date. Those facts go
stale silently: a rule publishes, the fee moves, and nothing in Ellis notices
until an applicant pays the wrong amount. This module watches the Federal
Register for rules published AFTER that curated date from the agencies that can
move those facts, and raises a flag for a human.

What it is NOT
--------------
It is not a source of truth and it is not an updater. A rule surfaced here is a
citation and a publication date. It NEVER rewrites a fee, a window, or a form
edition — Ellis's curated answer stands untouched until a human re-verifies it
against the rule text and edits `guidance.py`. Nothing in this module returns a
dollar amount, and nothing here is ever quoted as the government's answer to an
applicant's question.

Honest degradation
------------------
An empty result list from a search means "the Federal Register really has no
matching rule" — a genuine all-clear. It must NEVER also mean "the lookup
failed", because a false all-clear on a fee change is exactly the failure this
module exists to prevent. A list cannot carry an "unavailable" flag, so a
failure RAISES `FederalRegisterUnavailable` instead of returning `[]`, and the
dict-returning `check_curated_facts_stale` catches it and answers
`live: False, stale: None` — explicitly unknown, never "fine".

The API needs no key and no vendor (federalregister.gov, Office of the Federal
Register / GPO). Shapes below were verified live on 2026-08-11.
"""
from __future__ import annotations

import datetime as _dt
import time

from ..config import settings

SOURCE = "federal_register"

BASE_URL = "https://www.federalregister.gov/api/v1"
DOCUMENTS_PATH = "/documents.json"

# Agency slugs verified against GET /api/v1/agencies on 2026-08-11. These are
# the only agencies that can move a US immigration filing fact by rule: DHS and
# USCIS (fees, forms, e-filing, registration), DOL and its two sub-agencies
# (LCA / prevailing wage), and State (visa issuance and the consular leg).
AGENCY_SLUGS = (
    "u-s-citizenship-and-immigration-services",
    "homeland-security-department",
    "labor-department",
    "employment-and-training-administration",
    "wage-and-hour-division",
    "state-department",
)

# Federal Register document types. Final rules and proposed rules carry the
# regulatory change; notices carry fee schedules and cap/registration
# announcements; presidential documents carry proclamations.
DOCUMENT_TYPES = ("RULE", "PRORULE", "NOTICE", "PRESDOCU")

# Full-text search terms. One request per term (the API's `conditions[term]` is
# a single query string), merged and de-duplicated by document number. Kept
# deliberately short: the agency filter already does most of the narrowing, and
# every extra term is another round trip.
DEFAULT_TERMS = (
    "H-1B",
    "I-129",
    "premium processing",
    "labor condition application",
    "electronic filing",
)

# Which curated fact a rule might move, matched OFFLINE against the document's
# own title/abstract/action text. This is a routing hint for the human who
# re-verifies — never an assertion about what the rule actually says.
CURATED_FACT_AREAS: dict[str, tuple[str, ...]] = {
    "fees": ("fee", "fees", "premium processing", "asylum program",
             "fraud prevention", "biometric", "surcharge", "cost recovery"),
    "form_edition": ("i-129", "form edition", "edition date", "revised form",
                     "form revision", "information collection"),
    "filing_window": ("registration", "cap season", "lottery", "filing period",
                      "filing window", "cap-subject", "selection process"),
    "signature_efiling": ("electronic filing", "e-filing", "efiling", "efile",
                          "signature", "online filing", "digital signature"),
}

# The alarm is advisory and re-verification is a human act, so a one hour TTL is
# plenty and keeps a sweep over many cases down to one round trip per term.
CACHE_TTL_SECONDS = 3600
_TIMEOUT_SECONDS = 20

# Federal Register paginates and caps at 2000 results. A narrow agency+term
# filter over a staleness window returns single digits; these bounds exist so a
# pathological window cannot turn one check into a hundred requests.
_PER_PAGE = 100
_MAX_PAGES = 3

_CACHE: dict = {}


class FederalRegisterUnavailable(RuntimeError):
    """The Federal Register could not be consulted (disabled, unreachable, or
    an error response). Raised instead of returning an empty list, because an
    empty list means "no such rule exists" and a failure must never be able to
    impersonate an all-clear."""


class InvalidSearchWindow(ValueError):
    """The search request is malformed — `since_date` is not an ISO
    (YYYY-MM-DD) date, or there is no term to search for. Raised BEFORE any
    network call, so a bad request never reaches the API and never comes back
    wearing an all-clear."""


def is_configured() -> bool:
    """True when this deployment is allowed to consult the Federal Register.
    The API is key-free, so the only gate is the operator switch."""
    return bool(settings().federal_register_enabled)


def reset_cache() -> None:
    _CACHE.clear()


# ---------------------------------------------------------------------------
# HTTP seam — every network byte this module sends passes through here, so a
# test that stubs it proves nothing escaped to the real network.
# ---------------------------------------------------------------------------
def _http_json(method: str, url: str, *, params: dict | None = None,
               headers: dict | None = None) -> tuple[int, dict]:
    import httpx
    r = httpx.request(method, url, params=params, headers=headers or {},
                      timeout=_TIMEOUT_SECONDS)
    try:
        body = r.json()
    except Exception:  # noqa: BLE001 - a non-JSON body is an error body
        body = {}
    return r.status_code, (body if isinstance(body, dict) else {})


# ---------------------------------------------------------------------------
# Parsing — defensive by default. A malformed payload degrades to fewer rules,
# never to an exception and never to an invented rule.
# ---------------------------------------------------------------------------
def _iso_date(value) -> str | None:
    """The API publishes YYYY-MM-DD. Anything else returns None — a date Ellis
    cannot read is not a date it invents."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        return _dt.date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def _normalize_date(value, *, field: str) -> str:
    if isinstance(value, _dt.datetime):
        return value.date().isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    iso = _iso_date(value)
    if not iso:
        raise InvalidSearchWindow(f"{field} must be an ISO date (YYYY-MM-DD)")
    return iso


def _parse_agencies(items) -> list[str]:
    out: list[str] = []
    if not isinstance(items, list):
        return out
    for item in items:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("raw_name") or "").strip()
        else:
            name = str(item or "").strip()
        if name and name not in out:
            out.append(name)
    return out


def classify_fact_areas(document: dict) -> list[str]:
    """Which curated fact areas a rule's own words touch. A hint for the human
    reviewer, computed offline from title + abstract + action. An empty list
    means "matched the agency and term filter but named no known fact area" —
    the rule is still reported, never dropped."""
    haystack = " ".join(str(document.get(k) or "") for k in
                        ("title", "abstract", "action")).lower()
    return [area for area, keywords in CURATED_FACT_AREAS.items()
            if any(kw in haystack for kw in keywords)]


def _parse_document(item) -> dict | None:
    """One API result -> Ellis's rule shape. Returns None for anything without
    a document number: a citation Ellis cannot cite is not a citation."""
    if not isinstance(item, dict):
        return None
    number = str(item.get("document_number") or "").strip()
    if not number:
        return None
    return {
        "title": str(item.get("title") or "").strip(),
        "document_number": number,
        "publication_date": _iso_date(item.get("publication_date")),
        # The API calls the effective date `effective_on`; many documents have
        # none (a notice, or a rule whose date is set elsewhere) and None is the
        # honest answer for those.
        "effective_date": _iso_date(item.get("effective_on")),
        "html_url": str(item.get("html_url") or "").strip(),
        "agencies": _parse_agencies(item.get("agencies")),
        "abstract": str(item.get("abstract") or "").strip(),
        "type": str(item.get("type") or "").strip(),
        "action": str(item.get("action") or "").strip(),
        "source": SOURCE,
    }


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
_FIELDS = ("title", "document_number", "publication_date", "effective_on",
           "html_url", "agencies", "abstract", "type", "action")


def _request_params(term: str, since: str, agencies: tuple[str, ...],
                    types: tuple[str, ...], page: int) -> dict:
    params: dict = {
        "per_page": _PER_PAGE,
        "page": page,
        "order": "newest",
        "fields[]": list(_FIELDS),
        "conditions[term]": term,
        "conditions[publication_date][gte]": since,
    }
    if agencies:
        params["conditions[agencies][]"] = list(agencies)
    if types:
        params["conditions[type][]"] = list(types)
    return params


def _search_one_term(term: str, since: str, agencies: tuple[str, ...],
                     types: tuple[str, ...]) -> tuple[list[dict], int]:
    """One term -> (documents, total_count_reported_by_the_api). Cached with a
    TTL; only successes are cached, so a transient failure is never pinned in
    place of an answer a retry could obtain."""
    key = (term, since, agencies, types)
    cached = _CACHE.get(key)
    if cached is not None and cached[0] > time.monotonic():
        return list(cached[1]), cached[2]

    documents: list[dict] = []
    total = 0
    for page in range(1, _MAX_PAGES + 1):
        try:
            code, body = _http_json(
                "GET", f"{BASE_URL}{DOCUMENTS_PATH}",
                params=_request_params(term, since, agencies, types, page),
                headers={"accept": "application/json"})
        except Exception as exc:  # noqa: BLE001 - never surface transport internals
            raise FederalRegisterUnavailable(
                "Federal Register API unreachable") from exc
        if code >= 400:
            raise FederalRegisterUnavailable(
                f"Federal Register API returned HTTP {code}")
        try:
            total = int(body.get("count") or 0)
        except (TypeError, ValueError):
            total = total or 0
        results = body.get("results")
        if not isinstance(results, list) or not results:
            break
        for item in results:
            parsed = _parse_document(item)
            if parsed is not None:
                documents.append(parsed)
        if len(results) < _PER_PAGE:
            break

    _CACHE[key] = (time.monotonic() + CACHE_TTL_SECONDS, list(documents), total)
    return documents, total


def _search(since_date, terms, agencies, types) -> tuple[list[dict], int, bool]:
    """Shared engine. Returns (rules, total_matched, truncated)."""
    if not is_configured():
        raise FederalRegisterUnavailable("Federal Register feed is disabled")
    since = _normalize_date(since_date, field="since_date")

    if terms is None:
        term_list = list(DEFAULT_TERMS)
    elif isinstance(terms, str):
        term_list = [terms]
    else:
        term_list = [str(t).strip() for t in terms if str(t).strip()]
    if not term_list:
        raise InvalidSearchWindow("at least one search term is required")

    agency_tuple = tuple(agencies) if agencies is not None else AGENCY_SLUGS
    type_tuple = tuple(types) if types is not None else DOCUMENT_TYPES

    merged: dict[str, dict] = {}
    total = 0
    truncated = False
    for term in term_list:
        documents, term_total = _search_one_term(term, since, agency_tuple,
                                                 type_tuple)
        if term_total > len(documents):
            truncated = True
        total = max(total, term_total)
        for doc in documents:
            existing = merged.get(doc["document_number"])
            if existing is None:
                merged[doc["document_number"]] = {**doc,
                                                  "matched_terms": [term]}
            elif term not in existing["matched_terms"]:
                existing["matched_terms"].append(term)

    rules = sorted(merged.values(),
                   key=lambda d: (d.get("publication_date") or "",
                                  d["document_number"]),
                   reverse=True)
    return rules, max(total, len(rules)), truncated


def search_immigration_rules(since_date, terms=None, *, agencies=None,
                             types=None) -> list[dict]:
    """Rules published on or after `since_date` from the immigration agencies.

    Returns a list of {title, document_number, publication_date, effective_date,
    html_url, agencies, abstract, type, action, source, matched_terms}, newest
    first, de-duplicated across terms.

    `since_date` is an ISO string or a date/datetime. `terms` defaults to
    DEFAULT_TERMS; `agencies` and `types` default to AGENCY_SLUGS and
    DOCUMENT_TYPES. Pass an empty tuple for either to drop that filter.

    An EMPTY LIST means the Federal Register genuinely has no matching rule.
    Raises `FederalRegisterUnavailable` when the feed is disabled, unreachable,
    or errors, and `InvalidSearchWindow` for a malformed date — both BEFORE any
    caller can mistake a failure for an all-clear.
    """
    rules, _total, _truncated = _search(since_date, terms, agencies, types)
    return rules


# ---------------------------------------------------------------------------
# The staleness alarm
# ---------------------------------------------------------------------------
def _degraded(as_of: str, note: str) -> dict:
    return {
        "as_of": as_of,
        # Explicitly unknown. Not False — "we could not check" and "nothing
        # changed" are different answers and only one of them is safe to act on.
        "stale": None,
        "live": False,
        "rules": [],
        "count": 0,
        "undated_rules": [],
        "undated_count": 0,
        "total_matched": 0,
        "truncated": False,
        "action": "retry_or_verify_manually",
        "source": SOURCE,
        "note": note,
    }


def check_curated_facts_stale(as_of=None, *, terms=None, agencies=None,
                              types=None) -> dict:
    """Has any rule published SINCE Ellis's curated as_of date that could move a
    curated fact?

    `as_of` defaults to `app.h1b.guidance.AS_OF` (imported lazily so this
    provider stays edition-neutral and import-safe). A rule published ON the
    as_of date was already in front of the human who curated that day's facts,
    so the comparison is strictly AFTER.

    Returns {as_of, stale, live, rules, count, total_matched, truncated, action,
    source}. Each rule carries `fact_areas` — which curated facts its own words
    touch — as a routing hint for the reviewer.

    `stale: True` means RE-VERIFY, nothing more. This function never returns an
    amount, never edits guidance, and never overrides Ellis's curated answer. On
    any failure it returns `live: False, stale: None` (explicitly unknown) — it
    never reports a false all-clear.
    """
    if as_of is None:
        from ..h1b.guidance import AS_OF as _as_of  # lazy: edition-neutral
        as_of = _as_of
    # A malformed as_of raises before any network call: a staleness check
    # against a date Ellis cannot read is not a check.
    as_of_iso = _normalize_date(as_of, field="as_of")
    try:
        # Query inclusively at the boundary, then filter strictly after it: the
        # API's gte is inclusive and a same-day rule is not new information.
        rules, total, truncated = _search(as_of_iso, terms, agencies, types)
    except FederalRegisterUnavailable as exc:
        return _degraded(as_of_iso, str(exc))

    newer = [{**r, "fact_areas": classify_fact_areas(r)} for r in rules
             if (r.get("publication_date") or "") > as_of_iso]
    # A rule the API returned for a `publication_date >= as_of` query but whose
    # date Ellis could not read. An unreadable date is not evidence of
    # staleness, so it does not set `stale` — but it must not VANISH either:
    # dropping it silently would let the note below say "no rule matched" about
    # a rule the government just returned. It is surfaced for the same human.
    undated = [{**r, "fact_areas": classify_fact_areas(r)} for r in rules
               if not r.get("publication_date")]
    note = ("No rule published after the curated as_of date matched the "
            "immigration agency and term filter.")
    if newer:
        note = ("Rules published after the curated as_of date. Re-verify the "
                "cited rules against guidance.py before any amount or window "
                "is shown next to a payment; nothing here updates a curated "
                "fact automatically.")
    if undated:
        note += (f" {len(undated)} further rule(s) matched the search window "
                 "but carry no readable publication date; Ellis cannot place "
                 "them relative to the as_of date, so a human must look.")
    return {
        "as_of": as_of_iso,
        "stale": bool(newer),
        "live": True,
        "rules": newer,
        "count": len(newer),
        "undated_rules": undated,
        "undated_count": len(undated),
        "total_matched": total,
        "truncated": truncated,
        "action": "human_review" if (newer or undated) else "none",
        "source": SOURCE,
        "note": note,
    }
