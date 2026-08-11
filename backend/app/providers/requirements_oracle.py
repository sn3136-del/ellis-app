"""Sherpa Requirements API — a SECOND OPINION on an entry route, never the answer.

Edition-neutral: this provider knows a nationality, a destination and a date.
It has no idea whether the case is a tourist trip or an H1B beneficiary flying
home for stamping, and nothing here keys off a government family.

WHAT THIS IS FOR
----------------
Ellis's own curated, sourced route record is the answer an applicant sees.
Sherpa is a commercial travel-requirements aggregator; it is a corroboration
source for the BUILDER's curation loop. `compare_with_curated` reduces both
opinions to a coarse class and reports agree / disagree / unknown. A
disagreement RAISES A REVIEW FLAG for a human — it never rewrites Ellis's
record, and `compare_with_curated` is a pure read that mutates nothing.

WHAT THIS IS NOT FOR
--------------------
Sherpa's prose is not shown to applicants. Every result carries
`applicant_visible: False` and `REDISTRIBUTION_CAVEAT`: the titles and
descriptions are licensed vendor content, not Ellis's own sourced wording, and
a third party is never quoted as the government. The government links Sherpa
cites come back separately as `government_sources` — leads a human can go read
at the source, not citations Ellis may pass off as its own research.
`source_url` is the SHERPA endpoint that answered, so the provenance of the
second opinion is never confusable with a government page.

CACHING
-------
Sherpa's FAQ says data is refreshed hourly after quality control and that "it
is not recommended to cache the data for longer than an hour". Live results are
cached in-process for CACHE_TTL_SECONDS (3600) and no longer. Degraded results
are never cached: a failure must not be pinned in place of an answer a retry
could obtain.

CONTRACT (verified against docs.joinsherpa.io, August 2026)
-----------------------------------------------------------
POST {base}/v3/trips
  headers: x-api-key: <key>, Content-Type: application/vnd.api+json
  body:    {"data": {"type": "TRIP", "attributes": {
              "locale", "traveller": {"passports": [ISO3, ...]},
              "travelNodes": [{"type": "ORIGIN"|"DESTINATION",
                               "locationCode": ISO3,
                               "departure"|"arrival": {"date": "YYYY-MM-DD",
                                                       "time": "HH:MM",
                                                       "travelMode": "AIR"}}]}}}
  response: data.attributes.travelOpenness  (LEVEL_1..LEVEL_4 | NO_INFORMATION)
            data.attributes.headline
            data.attributes.informationGroups[] (type VISA_REQUIREMENTS) with
              `enforcement` and possibly NESTED `groupings`, whose `data[]`
              entries reference PROCEDURE ids resolved from the top-level
              `included[]` array.
"""
from __future__ import annotations

import datetime as _dt
import re
import time

from ..config import settings

SOURCE = "sherpa"

_DEFAULT_BASE = "https://requirements-api.joinsherpa.com"
_TRIPS_PATH = "/v3/trips"
_CONTENT_TYPE = "application/vnd.api+json"
_TIMEOUT_SECONDS = 20

# Sherpa refreshes hourly and asks integrators not to cache beyond an hour.
CACHE_TTL_SECONDS = 3600

REDISTRIBUTION_CAVEAT = (
    "Sherpa is a commercial second opinion. Its wording is licensed vendor "
    "content and is never shown to an applicant or presented as a government "
    "source; use it only to flag Ellis's own curated record for human review.")

# The oracle's own verdict vocabulary, normalized from Sherpa's travelOpenness
# score (its documented meanings) and, failing that, the visa group's
# enforcement value.
VERDICTS = (
    "NO_VISA_REQUIRED",         # LEVEL_1 — passport only
    "ELECTRONIC_AUTHORIZATION",  # LEVEL_2 — eVisa / ETA / visa on arrival
    "VISA_REQUIRED",            # LEVEL_3 — paper visa applied for in advance
    "ENTRY_RESTRICTED",         # LEVEL_4 — entry restricted or not permitted
    # Sherpa says SOMETHING is required but not which kind (an enforcement
    # value with no openness level). Deliberately coarser than the four above:
    # inventing "paper visa" from "MANDATORY" would manufacture disagreements.
    "AUTHORIZATION_REQUIRED",
    "UNKNOWN",                  # NO_INFORMATION / hedged / nothing parseable
)

_OPENNESS_TO_VERDICT = {
    "LEVEL_1": "NO_VISA_REQUIRED",
    "LEVEL_2": "ELECTRONIC_AUTHORIZATION",
    "LEVEL_3": "VISA_REQUIRED",
    "LEVEL_4": "ENTRY_RESTRICTED",
    "NO_INFORMATION": "UNKNOWN",
}

# Sherpa enforcement values on the VISA_REQUIREMENTS group. MAY_BE_REQUIRED is
# Sherpa hedging, so it stays UNKNOWN rather than becoming a disagreement.
_ENFORCEMENT_TO_VERDICT = {
    "NOT_REQUIRED": "NO_VISA_REQUIRED",
    "MANDATORY": "AUTHORIZATION_REQUIRED",
    "MAY_BE_REQUIRED": "UNKNOWN",
}

# ---------------------------------------------------------------------------
# Coarse comparison classes. Both vocabularies collapse to these; only a
# confident class-vs-class difference is a disagreement.
# ---------------------------------------------------------------------------
COMPARISON_RESULTS = ("agree", "disagree", "unknown")

_CLASS_NONE = "no_authorization"
_CLASS_ELECTRONIC = "electronic_authorization"
_CLASS_VISA = "visa"
_CLASS_RESTRICTED = "restricted"
_CLASS_ANY = "some_authorization"   # oracle-only: kind unspecified
_CLASS_UNKNOWN = "unknown"

# Ellis's curated dispositions, across every vocabulary in the codebase
# (visa_snapshot.matrix, global_routes, kimi_primary). Anything not listed is
# UNKNOWN — an unmapped disposition must never manufacture a disagreement.
_CURATED_CLASS = {
    # nothing to obtain before travel
    "VISA_FREE": _CLASS_NONE,
    "VISA_EXEMPT": _CLASS_NONE,
    "ENTRY_PREPARATION": _CLASS_NONE,
    # obtained electronically or at the border
    "ETA_REQUIRED": _CLASS_ELECTRONIC,
    "EVISA_REQUIRED": _CLASS_ELECTRONIC,
    "VISA_ON_ARRIVAL": _CLASS_ELECTRONIC,
    "TRANSIT_AUTHORIZATION_REQUIRED": _CLASS_ELECTRONIC,
    "ELECTRONIC_AUTHORIZATION": _CLASS_ELECTRONIC,
    "ELECTRONIC_AUTHORIZATION_REQUIRED": _CLASS_ELECTRONIC,
    "EVISA": _CLASS_ELECTRONIC,
    # applied for in advance at a post / center / by mail
    "VISA_REQUIRED": _CLASS_VISA,
    "EMBASSY_VISA_REQUIRED": _CLASS_VISA,
    "AUTHORIZED_VISA_CENTER_REQUIRED": _CLASS_VISA,
    "EMBASSY_OR_CONSULATE_APPLICATION": _CLASS_VISA,
    "AUTHORIZED_VISA_CENTER": _CLASS_VISA,
    "MAIL_APPLICATION": _CLASS_VISA,
    "APPOINTMENT_REQUIRED": _CLASS_VISA,
    # no lawful route
    "NO_TOURIST_ROUTE": _CLASS_RESTRICTED,
    "NO_AVAILABLE_TOURIST_ROUTE": _CLASS_RESTRICTED,
}

_VERDICT_CLASS = {
    "NO_VISA_REQUIRED": _CLASS_NONE,
    "ELECTRONIC_AUTHORIZATION": _CLASS_ELECTRONIC,
    "VISA_REQUIRED": _CLASS_VISA,
    "ENTRY_RESTRICTED": _CLASS_RESTRICTED,
    "AUTHORIZATION_REQUIRED": _CLASS_ANY,
    "UNKNOWN": _CLASS_UNKNOWN,
}

# A coarse "something is required" oracle answer is consistent with either
# electronic or paper curated dispositions — it is not a disagreement.
_ANY_COMPATIBLE = (_CLASS_ELECTRONIC, _CLASS_VISA)

_ISO3_RE = re.compile(r"^[A-Z]{3}$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class InvalidRouteInput(ValueError):
    """The route inputs are not a route. Raised BEFORE any network call so a
    typo is never handed to the vendor and never comes back wearing a verdict."""


# ---------------------------------------------------------------------------
# HTTP seam — every network byte this module sends passes through here, so a
# test that stubs it proves nothing escaped to the real network.
# ---------------------------------------------------------------------------
def _http_json(method: str, url: str, *, headers: dict,
               json_body: dict | None = None) -> tuple[int, dict]:
    import httpx
    r = httpx.request(method, url, headers=headers, json=json_body,
                      timeout=_TIMEOUT_SECONDS)
    try:
        body = r.json()
    except Exception:  # noqa: BLE001 - a non-JSON body is an error body
        body = {}
    return r.status_code, (body if isinstance(body, dict) else {})


def _base_url() -> str:
    """The production Requirements API host. Sherpa also runs a sandbox host
    (requirements-api.sandbox.joinsherpa.com) used in their own samples; it is
    not configurable here because a sandbox answer must never be mistaken for a
    real second opinion, and this deployment has one key."""
    return _DEFAULT_BASE


def is_configured() -> bool:
    """True only when this deployment holds a Sherpa API key. Unconfigured, the
    oracle is honestly unavailable and Ellis's own curated answer stands."""
    return bool(settings().sherpa_api_key)


# ---------------------------------------------------------------------------
# Short-TTL cache (live results only)
# ---------------------------------------------------------------------------
_CACHE: dict = {}


def reset_cache() -> None:
    _CACHE.clear()


def _cache_get(key):
    entry = _CACHE.get(key)
    if not entry:
        return None
    if entry["expires_at"] <= time.time():
        _CACHE.pop(key, None)
        return None
    return entry["value"]


def _cache_put(key, value) -> None:
    _CACHE[key] = {"value": value, "expires_at": time.time() + CACHE_TTL_SECONDS}


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------
def _iso3(value, field: str) -> str:
    code = str(value or "").strip().upper()
    if not _ISO3_RE.match(code):
        raise InvalidRouteInput(f"{field} must be an ISO 3166-1 alpha-3 code")
    return code


def _iso_date(value, field: str) -> str:
    text = str(value or "").strip()
    if not _ISO_DATE_RE.match(text):
        raise InvalidRouteInput(f"{field} must be an ISO date (YYYY-MM-DD)")
    try:
        _dt.date.fromisoformat(text)
    except ValueError as exc:  # noqa: PERF203 - explicit, one path
        raise InvalidRouteInput(f"{field} is not a real date") from exc
    return text


def _collect_procedure_ids(grouping) -> list[str]:
    """Walk a grouping and its NESTED groupings for PROCEDURE references.

    Sherpa documents that a grouping may contain groupings to arbitrary depth,
    so a flat read would silently miss the procedures that carry the answer."""
    ids: list[str] = []
    if not isinstance(grouping, dict):
        return ids
    for item in (grouping.get("data") or []):
        if isinstance(item, dict) and str(item.get("type") or "") == "PROCEDURE":
            pid = str(item.get("id") or "")
            if pid:
                ids.append(pid)
    for child in (grouping.get("groupings") or []):
        ids.extend(_collect_procedure_ids(child))
    return ids


def _visa_group(attributes: dict) -> dict:
    """The VISA_REQUIREMENTS information group, by documented type first and by
    name only as a fallback (older payloads carry a name and no type)."""
    groups = attributes.get("informationGroups")
    if not isinstance(groups, list):
        return {}
    for g in groups:
        if isinstance(g, dict) and str(g.get("type") or "").upper() == "VISA_REQUIREMENTS":
            return g
    for g in groups:
        if isinstance(g, dict) and "visa" in str(g.get("name") or "").lower():
            return g
    return {}


def _procedures(body: dict, ids: list[str]) -> list[dict]:
    """Resolve PROCEDURE ids against the top-level `included` array. Only the
    fields Ellis actually reviews are carried through; the vendor payload is
    never stored wholesale."""
    included = body.get("included")
    by_id = {}
    if isinstance(included, list):
        for item in included:
            if isinstance(item, dict) and str(item.get("type") or "") == "PROCEDURE":
                by_id[str(item.get("id") or "")] = item
    out: list[dict] = []
    for pid in ids:
        item = by_id.get(pid)
        if not item:
            continue
        attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        doc_types = attrs.get("documentTypes")
        out.append({
            "id": pid,
            "title": str(attrs.get("title") or "").strip(),
            "description": str(attrs.get("description") or "").strip(),
            "category": str(attrs.get("category") or "").strip(),
            "enforcement": str(attrs.get("enforcement") or "").strip().upper(),
            "document_types": [str(d) for d in doc_types] if isinstance(doc_types, list) else [],
            "last_updated_at": str(attrs.get("lastUpdatedAt") or "").strip(),
        })
    return out


def _government_sources(body: dict, ids: list[str]) -> list[dict]:
    """The government links Sherpa cites, as LEADS for a human reviewer. These
    are not Ellis citations and are never presented as Ellis's own research."""
    included = body.get("included")
    if not isinstance(included, list):
        return []
    wanted = set(ids)
    out: list[dict] = []
    seen = set()
    for item in included:
        if not isinstance(item, dict) or str(item.get("id") or "") not in wanted:
            continue
        attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        for src in (attrs.get("sources") or []):
            if not isinstance(src, dict):
                continue
            url = str(src.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            out.append({"title": str(src.get("title") or "").strip(),
                        "type": str(src.get("type") or "").strip().upper(),
                        "url": url})
    return out


def _verdict_from(attributes: dict, group: dict) -> tuple[str, str, str]:
    """(verdict, travel_openness, enforcement). travelOpenness is the documented
    at-a-glance score and is preferred; enforcement is the coarser fallback."""
    openness = str(attributes.get("travelOpenness") or "").strip().upper()
    enforcement = str(group.get("enforcement") or "").strip().upper()
    verdict = _OPENNESS_TO_VERDICT.get(openness, "")
    if not verdict:
        verdict = _ENFORCEMENT_TO_VERDICT.get(enforcement, "UNKNOWN")
    return verdict, openness, enforcement


def _unavailable(note: str, *, nationality: str = "", destination: str = "") -> dict:
    return {
        "available": False,
        "verdict": "UNKNOWN",
        "documents": [],
        "procedures": [],
        "government_sources": [],
        "headline": "",
        "travel_openness": "",
        "enforcement": "",
        "nationality": nationality,
        "destination": destination,
        "source": SOURCE,
        "source_url": "",
        "fetched_at": None,
        "applicant_visible": False,
        "caveat": REDISTRIBUTION_CAVEAT,
        "note": note,
    }


def check_route(nationality: str, destination: str, *, origin: str | None = None,
                departure_date: str | None = None, locale: str = "en-US") -> dict:
    """Ask Sherpa what it believes about one nationality/destination route.

    Args:
      nationality:    passport nationality, ISO 3166-1 alpha-3 (e.g. "CHN").
      destination:    destination territory, ISO alpha-3 (e.g. "USA").
      origin:         departure territory, ISO alpha-3. Defaults to the
                      nationality's own country — the ordinary case, and stated
                      in the result as `origin_assumed` rather than hidden.
      departure_date: ISO YYYY-MM-DD. Defaults to today (UTC), flagged as
                      `departure_date_assumed`; requirements are date-sensitive
                      so an assumed date is never silent.

    Returns {available, verdict, documents, source_url, fetched_at, ...}:
      available     False whenever the oracle could not answer (unconfigured,
                    unreachable, HTTP error). Never a fabricated verdict.
      verdict       one of VERDICTS.
      documents     the document types Sherpa says the route needs (e.g.
                    ["VISA"]), deduplicated — machine-readable, not prose.
      procedures    per-procedure detail for a human reviewer only.
      source_url    the SHERPA endpoint that answered. NOT a government page.
      fetched_at    UTC ISO timestamp of the live read.

    Raises InvalidRouteInput for codes/dates that are not codes/dates, before
    the provider is even asked whether it is configured."""
    nat = _iso3(nationality, "nationality")
    dest = _iso3(destination, "destination")
    origin_assumed = not bool(origin)
    orig = nat if origin_assumed else _iso3(origin, "origin")
    date_assumed = not bool(departure_date)
    depart = (_dt.datetime.now(tz=_dt.timezone.utc).date().isoformat()
              if date_assumed else _iso_date(departure_date, "departure_date"))
    loc = str(locale or "en-US").strip() or "en-US"

    if not is_configured():
        return _unavailable("Sherpa Requirements API not configured",
                            nationality=nat, destination=dest)

    key = (nat, dest, orig, depart, loc)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    url = f"{_base_url()}{_TRIPS_PATH}"
    payload = {"data": {"type": "TRIP", "attributes": {
        "locale": loc,
        "traveller": {"passports": [nat]},
        "travelNodes": [
            {"type": "ORIGIN", "locationCode": orig,
             "departure": {"date": depart, "time": "00:00", "travelMode": "AIR"}},
            {"type": "DESTINATION", "locationCode": dest,
             "arrival": {"date": depart, "time": "00:00", "travelMode": "AIR"}},
        ],
    }}}
    try:
        code, body = _http_json(
            "POST", url,
            headers={"x-api-key": settings().sherpa_api_key,
                     "content-type": _CONTENT_TYPE, "accept": _CONTENT_TYPE},
            json_body=payload)
    except Exception:  # noqa: BLE001 - never surface transport internals
        return _unavailable("Sherpa Requirements API unreachable",
                            nationality=nat, destination=dest)
    if code >= 400:
        return _unavailable(f"Sherpa Requirements API returned HTTP {code}",
                            nationality=nat, destination=dest)

    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    attributes = data.get("attributes") if isinstance(data.get("attributes"), dict) else {}
    group = _visa_group(attributes)
    verdict, openness, enforcement = _verdict_from(attributes, group)

    # Preserve order, drop duplicates (one procedure can appear in two groupings).
    ids = list(dict.fromkeys(_collect_procedure_ids(group)))
    procedures = _procedures(body, ids)

    documents: list[str] = []
    for p in procedures:
        for d in p["document_types"]:
            if d not in documents:
                documents.append(d)

    result = {
        "available": True,
        "verdict": verdict,
        "documents": documents,
        "procedures": procedures,
        "government_sources": _government_sources(body, ids),
        "headline": str(attributes.get("headline") or "").strip(),
        "travel_openness": openness,
        "enforcement": enforcement,
        "nationality": nat,
        "destination": dest,
        "origin": orig,
        "origin_assumed": origin_assumed,
        "departure_date": depart,
        "departure_date_assumed": date_assumed,
        "source": SOURCE,
        # Provenance of the SECOND OPINION, not of the requirement itself.
        "source_url": url,
        "fetched_at": _dt.datetime.now(tz=_dt.timezone.utc).isoformat(),
        # Vendor prose stays internal. See REDISTRIBUTION_CAVEAT.
        "applicant_visible": False,
        "caveat": REDISTRIBUTION_CAVEAT,
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
    }
    _cache_put(key, result)
    return result


# ---------------------------------------------------------------------------
# The curation loop's actual question
# ---------------------------------------------------------------------------
def _curated_class(disposition) -> tuple[str, str]:
    """(label, class) for Ellis's own record. Accepts the disposition string or
    any mapping carrying one; the input is only READ, never modified."""
    if isinstance(disposition, dict):
        label = str(disposition.get("disposition")
                    or disposition.get("outcome") or "").strip().upper()
    else:
        label = str(disposition or "").strip().upper()
    return label, _CURATED_CLASS.get(label, _CLASS_UNKNOWN)


def _oracle_class(oracle_verdict) -> tuple[str, str]:
    """(label, class) for the oracle. Accepts the verdict string or a whole
    check_route result; an unavailable result is always UNKNOWN, so a degraded
    oracle can never produce a disagreement."""
    if isinstance(oracle_verdict, dict):
        if not oracle_verdict.get("available"):
            return "UNKNOWN", _CLASS_UNKNOWN
        label = str(oracle_verdict.get("verdict") or "").strip().upper()
    else:
        label = str(oracle_verdict or "").strip().upper()
    return label, _VERDICT_CLASS.get(label, _CLASS_UNKNOWN)


def compare_with_curated(curated_disposition, oracle_verdict) -> dict:
    """Compare Ellis's curated disposition with the oracle's verdict.

    Returns {result, curated, curated_class, oracle, oracle_class, review_flag,
    note} where `result` is exactly one of COMPARISON_RESULTS:

      'agree'    both reduce to the same coarse class, or the oracle only says
                 "some authorization is required" and Ellis says which kind.
      'disagree' both are confident and the classes differ. This RAISES A
                 REVIEW FLAG (`review_flag: True`) for a human. It never
                 rewrites the curated record — this function is a pure read and
                 mutates neither argument.
      'unknown'  either side has nothing confident to say (unmapped
                 disposition, NO_INFORMATION, hedged enforcement, or an
                 unavailable oracle). Silence is not disagreement.
    """
    curated_label, c_class = _curated_class(curated_disposition)
    oracle_label, o_class = _oracle_class(oracle_verdict)

    if c_class == _CLASS_UNKNOWN or o_class == _CLASS_UNKNOWN:
        result = "unknown"
        note = ("no confident comparison: "
                + ("Ellis's disposition is not classifiable"
                   if c_class == _CLASS_UNKNOWN
                   else "the oracle has no confident verdict"))
    elif o_class == _CLASS_ANY:
        # "Something is required" agrees with any authorization Ellis names,
        # and disagrees only with "nothing is required" / "no route".
        agreed = c_class in _ANY_COMPATIBLE
        result = "agree" if agreed else "disagree"
        note = ("the oracle requires some authorization and Ellis names one"
                if agreed else
                "the oracle requires an authorization Ellis's record does not")
    elif c_class == o_class:
        result = "agree"
        note = "both reduce to the same coarse class"
    else:
        result = "disagree"
        note = (f"Ellis says {c_class}, the oracle says {o_class} — "
                "flag Ellis's record for human review; do not overwrite it")

    return {
        "result": result,
        "curated": curated_label,
        "curated_class": c_class,
        "oracle": oracle_label,
        "oracle_class": o_class,
        # The ONLY effect a disagreement may have.
        "review_flag": result == "disagree",
        "source": SOURCE,
        "note": note,
        "caveat": REDISTRIBUTION_CAVEAT,
    }
