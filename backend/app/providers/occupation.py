"""Occupation and industry classification — SOC (2018) and NAICS (2017/2022).

A classification is a SUGGESTION with a confidence, never an authoritative
answer. The SOC code and NAICS code are LEGAL REPRESENTATIONS on the LCA and
the I-129; Ellis computes and suggests them, and a human confirms. Nothing here
ever auto-fills a filed answer, and nothing here fabricates a code: when a
provider is unconfigured or unreachable, the result degrades honestly with
`live: False` and carries no invented code.

Three surfaces, three official free sources:

  classify_nioccs  — CDC NIOCCS (wwwn.cdc.gov/nioccs), no key. Maps free-text
      industry + occupation to a NAICS 2017 code and a SOC 2018 code, each with
      a probability that doubles as an RFE/confidence signal. CDC has marked the
      tool "no longer supported" (a RIF), though it is live today — so every
      result carries that caveat, and no caller may treat it as a hard
      dependency. Results are cached in-process per (industry, occupation, n).

  search_onet      — O*NET Web Services v2 (api-v2.onetcenter.org), needs a free
      API key (X-API-Key). Maps a keyword or code to O*NET-SOC codes.
      Unconfigured, it degrades honestly rather than guessing.

  validate_naics   — validates a NAICS code against a compact, committed 2022
      NAICS reference. Offline and deterministic; the Census reference file is
      the full authority and `fetch_census_naics_map` documents how to refresh
      the committed subset (it is never called from the request path or tests).
"""
from __future__ import annotations

from ..config import settings

# ---------------------------------------------------------------------------
# Sources and caveats
# ---------------------------------------------------------------------------
NIOCCS_SOURCE = "nioccs"
ONET_SOURCE = "onet"
CENSUS_SOURCE = "census"

# CDC states NIOCCS is "no longer supported" (a reduction in force) even though
# the service still answers. Every NIOCCS result carries this so a caller can
# cache, human-confirm, and never build a hard dependency on it.
NIOCCS_CAVEAT = ("CDC NIOCCS is officially 'no longer supported'; this is a "
                 "suggestion to be human-confirmed, not an authoritative code.")

_NIOCCS_URL = "https://wwwn.cdc.gov/nioccs/IOCode"
_ONET_BASE = "https://api-v2.onetcenter.org"

_TIMEOUT_SECONDS = 20


class InvalidClassificationInput(ValueError):
    """Raised BEFORE any network call when the classification text is empty or
    not text. A classifier that guesses at garbage input is worse than one that
    refuses."""


# ---------------------------------------------------------------------------
# HTTP seam — every network byte this module sends passes through here, so a
# test that stubs it proves nothing escaped to the real network.
# ---------------------------------------------------------------------------
def _http_json(method: str, url: str, *, headers: dict | None = None,
               params: dict | None = None) -> tuple[int, dict]:
    import httpx
    r = httpx.request(method, url, headers=headers or {}, params=params,
                      timeout=_TIMEOUT_SECONDS)
    try:
        body = r.json()
    except Exception:  # noqa: BLE001 - a non-JSON body is an error body
        body = {}
    return r.status_code, (body if isinstance(body, dict) else {})


# ---------------------------------------------------------------------------
# NIOCCS — CDC industry/occupation coding (SOC 2018 + NAICS 2017), no key
# ---------------------------------------------------------------------------
# In-process cache keyed by (industry, occupation, n). Only LIVE results are
# cached: a degraded (network-failure) answer must never be pinned in place of
# a real one that a retry could obtain.
_CLASSIFY_CACHE: dict = {}


def reset_cache() -> None:
    _CLASSIFY_CACHE.clear()


def _clean_text(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise InvalidClassificationInput("classification text must be a string")
    return value.strip()


def _to_probability(value):
    """NIOCCS returns a probability as a number or a numeric string. Coerce it
    to a float; anything unparseable becomes None rather than an invented
    confidence."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_nioccs_list(items) -> list[dict]:
    """Parse a NIOCCS Industry/Occupation array into {code, title, probability}.
    An entry with no code is dropped: a suggestion with no code is not a
    suggestion."""
    out: list[dict] = []
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        code = str(item.get("Code") or "").strip()
        if not code:
            continue
        out.append({
            "code": code,
            "title": str(item.get("Title") or "").strip(),
            "probability": _to_probability(item.get("Probability")),
        })
    return out


def _nioccs_degraded(note: str) -> dict:
    return {"soc": [], "naics": [], "source": NIOCCS_SOURCE, "live": False,
            "caveat": NIOCCS_CAVEAT, "note": note}


def classify_nioccs(*, industry_text: str = "", occupation_text: str = "",
                    n: int = 3) -> dict:
    """Classify free-text industry + occupation via CDC NIOCCS.

    Returns {soc: [{code, title, probability}], naics: [{code, title,
    probability}], source: 'nioccs', live: bool, caveat: str}. `soc` codes are
    SOC 2018 (e.g. 15-1252); `naics` codes are NAICS 2017. Probability is a
    usable confidence/RFE signal.

    Raises InvalidClassificationInput when neither text is provided (or a value
    is not a string) — checked before any network call. On a network failure the
    result is honestly degraded (live: False, empty lists); no code is ever
    fabricated. Live results are cached in-process per (industry, occupation, n).
    """
    industry = _clean_text(industry_text)
    occupation = _clean_text(occupation_text)
    if not industry and not occupation:
        raise InvalidClassificationInput(
            "at least one of industry_text or occupation_text is required")
    if not isinstance(n, int) or n < 1:
        raise InvalidClassificationInput("n must be a positive integer")

    key = (industry.lower(), occupation.lower(), n)
    cached = _CLASSIFY_CACHE.get(key)
    if cached is not None:
        return cached

    try:
        code, body = _http_json(
            "GET", _NIOCCS_URL,
            headers={"accept": "application/json"},
            params={"i": industry, "o": occupation, "n": n, "t": "json"})
    except Exception:  # noqa: BLE001 - never surface transport internals
        return _nioccs_degraded("CDC NIOCCS is unreachable")
    if code >= 400:
        return _nioccs_degraded(f"CDC NIOCCS returned HTTP {code}")

    result = {
        "soc": _parse_nioccs_list(body.get("Occupation")),
        "naics": _parse_nioccs_list(body.get("Industry")),
        "source": NIOCCS_SOURCE,
        "live": True,
        "caveat": NIOCCS_CAVEAT,
    }
    _CLASSIFY_CACHE[key] = result
    return result


# ---------------------------------------------------------------------------
# O*NET Web Services v2 — keyword/code -> O*NET-SOC (needs a free API key)
# ---------------------------------------------------------------------------
def is_onet_configured() -> bool:
    return bool(settings().onet_api_key)


def _parse_onet(body: dict) -> list[dict]:
    """Parse an O*NET online-search response into {code, title}. The v2 search
    returns an `occupation` array; be defensive about shape."""
    items = body.get("occupation")
    if not isinstance(items, list):
        return []
    out: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").strip()
        if not code:
            continue
        out.append({"code": code, "title": str(item.get("title") or "").strip()})
    return out


def search_onet(keyword: str) -> dict:
    """Search O*NET for occupations matching a keyword or code.

    Returns {matches: [{code, title}], source: 'onet', live: bool}. Unconfigured
    (no ONET_API_KEY) it returns live: False with a note rather than guessing; a
    network failure degrades the same honest way with no fabricated match.

    Raises InvalidClassificationInput on empty input."""
    kw = _clean_text(keyword)
    if not kw:
        raise InvalidClassificationInput("keyword is required")

    s = settings()
    if not s.onet_api_key:
        return {"matches": [], "source": ONET_SOURCE, "live": False,
                "note": "O*NET API key not configured"}

    try:
        code, body = _http_json(
            "GET", f"{_ONET_BASE}/online/search",
            headers={"X-API-Key": s.onet_api_key, "accept": "application/json"},
            params={"keyword": kw})
    except Exception:  # noqa: BLE001 - never surface transport internals
        return {"matches": [], "source": ONET_SOURCE, "live": False,
                "note": "O*NET API unreachable"}
    if code >= 400:
        return {"matches": [], "source": ONET_SOURCE, "live": False,
                "note": f"O*NET API returned HTTP {code}"}

    return {"matches": _parse_onet(body), "source": ONET_SOURCE, "live": True}


# ---------------------------------------------------------------------------
# NAICS validation — compact committed 2022 reference (offline, deterministic)
# ---------------------------------------------------------------------------
# A curated subset of the 2022 NAICS structure, weighted to the professional,
# scientific, and technical sector where H1B petitions concentrate. This is NOT
# the full code set — the Census reference file is the authority (see
# `fetch_census_naics_map`). It exists so a code can be validated OFFLINE at
# fill time with no network dependency. Titles are the Census 2022 wording.
_NAICS_2022: dict[str, str] = {
    # Sector / high-level
    "51": "Information",
    "52": "Finance and Insurance",
    "54": "Professional, Scientific, and Technical Services",
    "61": "Educational Services",
    "62": "Health Care and Social Assistance",
    "541": "Professional, Scientific, and Technical Services",
    # Legal
    "5411": "Legal Services",
    "541110": "Offices of Lawyers",
    # Accounting
    "5412": "Accounting, Tax Preparation, Bookkeeping, and Payroll Services",
    "541211": "Offices of Certified Public Accountants",
    # Architecture / engineering
    "5413": "Architectural, Engineering, and Related Services",
    "541310": "Architectural Services",
    "541330": "Engineering Services",
    # Computer systems design (the H1B core)
    "5415": "Computer Systems Design and Related Services",
    "54151": "Computer Systems Design and Related Services",
    "541511": "Custom Computer Programming Services",
    "541512": "Computer Systems Design Services",
    "541513": "Computer Facilities Management Services",
    "541519": "Other Computer Related Services",
    # Management consulting
    "5416": "Management, Scientific, and Technical Consulting Services",
    "54161": "Management Consulting Services",
    "541611": "Administrative Management and General Management Consulting Services",
    "541618": "Other Management Consulting Services",
    # Scientific research and development
    "5417": "Scientific Research and Development Services",
    "541713": "Research and Development in Nanotechnology",
    "541714": "Research and Development in Biotechnology (except Nanobiotechnology)",
    "541715": ("Research and Development in the Physical, Engineering, and Life "
               "Sciences (except Nanotechnology and Biotechnology)"),
    # Education (universities employ many H1B researchers)
    "611310": "Colleges, Universities, and Professional Schools",
    # Health / labs
    "6215": "Medical and Diagnostic Laboratories",
}


def validate_naics(code: str) -> dict:
    """Validate a NAICS code against the committed 2022 reference subset.

    Returns {valid: bool, source: 'census'} and, when valid, the official
    `title`. A code absent from the committed subset returns valid: False — it
    is unverified here, NOT proven wrong; the full Census file is the authority.
    """
    normalized = "".join(ch for ch in str(code or "") if ch.isdigit())
    title = _NAICS_2022.get(normalized)
    if title:
        return {"valid": True, "code": normalized, "title": title,
                "source": CENSUS_SOURCE}
    return {"valid": False, "code": normalized, "source": CENSUS_SOURCE}


def fetch_census_naics_map() -> dict[str, str]:  # pragma: no cover - documented, not run
    """Refresh helper (NOT on the request path, NOT exercised by tests).

    The U.S. Census Bureau publishes the authoritative 2022 NAICS structure as a
    downloadable reference file (the "2-6 digit" index) at census.gov/naics. This
    fetches and parses it into a {code: title} map that a maintainer can use to
    regenerate the committed `_NAICS_2022` subset. It is documented, not run:
    runtime validation stays offline and deterministic against the committed map.
    """
    import csv
    import io

    import httpx

    url = "https://www.census.gov/naics/2022NAICS/2022_NAICS_Descriptions.csv"
    r = httpx.get(url, timeout=60)
    r.raise_for_status()
    out: dict[str, str] = {}
    reader = csv.DictReader(io.StringIO(r.text))
    for row in reader:
        c = "".join(ch for ch in str(row.get("Code") or "") if ch.isdigit())
        title = str(row.get("Title") or "").strip()
        if c and title:
            out[c] = title
    return out
