"""USCIS H-1B Employer Data Hub — an employer's own past filing outcomes.

Read-only, and a WEAK SIGNAL by construction. This is aggregate history, not an
adjudication: it says how many petitions naming an employer were approved or
denied in a past fiscal year. It predicts nothing about the case in front of
the applicant, it is never quoted as USCIS's view of this petition, and it never
gates a filing. Its honest use is context for a human — an employer with a long
approval history and one with none are different conversations.

Access shape (researched live 2026-08-11)
-----------------------------------------
There is NO documented machine-readable API. The findings:

  * The live hub at uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub
    is a Tableau embed (bigdataanalyticspub-sb.uscis.dhs.gov). Its backing
    requests are an undocumented, unversioned internal surface — not something a
    government-filing product may depend on, and not something Ellis scrapes.
  * There is no data.gov / Socrata dataset. USCIS publishes this on uscis.gov.
  * The bulk CSV exports ARE real and stably addressed:
    ``.../sites/default/files/document/data/h1b_datahubexport-<YEAR>.csv``
    but only for FY2009-FY2023 (FY2024+ return 404), and the page that lists
    them now redirects to ``/archive/``. Meanwhile the live hub advertises data
    through FY2026 Q3.

So Ellis implements the honest hybrid: **lookups read a locally cached file and
never touch the network**, and `fetch_year` (explicit, opt-in, never on the
lookup path) pulls one of the published bulk exports into that cache. With no
cached file the module reports unavailable with a reason — it never guesses a
count, and it never reports FY2023 numbers as if they were current.

What the data actually is (columns verified against the FY2023 export)
----------------------------------------------------------------------
``Fiscal Year, Employer, Initial Approval, Initial Denial, Continuing Approval,
Continuing Denial, NAICS, Tax ID, State, City, ZIP``

Three facts that make a naive reading wrong, and are therefore enforced here:

  1. **"Tax ID" is only the LAST FOUR digits of the FEIN.** USCIS masks it. A
     FEIN lookup can NARROW a search; it can never confirm that a row is this
     employer. Every FEIN-qualified result says so, and the full FEIN a caller
     passes is reduced to its last four immediately and never echoed or logged.
  2. **Employer names are normalized by USCIS** (uppercased, punctuation
     stripped, "DBA" spelled out). A match is a name match on that normalized
     text, never a corporate-identity match.
  3. **Rows are pre-aggregated groups**, one per fiscal year / NAICS / tax-ID /
     city / state / ZIP, so a single employer spans many rows (including rows
     with no location reported). A company total is the SUM across its rows.

An approval rate is only computed when the denominator is real; there is no
default and no zero-filled rate.
"""
from __future__ import annotations

import csv
import os
import re
from pathlib import Path

from ..config import settings

SOURCE = "uscis_h1b_employer_data_hub"
HUB_URL = "https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub"
UNDERSTANDING_URL = (
    "https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub/"
    "understanding-our-h-1b-employer-data-hub")

# The stably addressed bulk export. Verified live 2026-08-11.
BULK_EXPORT_URL_TEMPLATE = (
    "https://www.uscis.gov/sites/default/files/document/data/"
    "h1b_datahubexport-{year}.csv")

# Fiscal years USCIS actually serves as a bulk export. FY2024+ return 404: the
# newer data exists only inside the Tableau UI. This tuple is a verified fact,
# not an assumption — a year outside it is refused rather than fetched blindly.
PUBLISHED_BULK_YEARS = tuple(range(2009, 2024))

NO_API_REASON = (
    "USCIS publishes no machine-readable API for the H-1B Employer Data Hub; "
    "the live hub is a Tableau embed and the bulk CSV exports stop at FY2023.")

CAVEAT = (
    "Aggregate past outcomes for petitions naming this employer. Not an "
    "adjudication and not a prediction: it says nothing about this petition. "
    "'Tax ID' in the government file is the last four FEIN digits only, so a "
    "FEIN match narrows a search but never confirms an employer's identity.")

# Marker statuses Ellis emits itself, snake_case so no consumer can confuse one
# with a government value.
UNAVAILABLE = "employer_history_unavailable"
NOT_FOUND = "employer_not_found"

_COUNT_COLUMNS = ("Initial Approval", "Initial Denial",
                  "Continuing Approval", "Continuing Denial")
_COUNT_KEYS = ("initial_approval", "initial_denial",
               "continuing_approval", "continuing_denial")

_TIMEOUT_SECONDS = 120  # a year export is ~2MB; only ever an explicit refresh


class InvalidEmployerQuery(ValueError):
    """Raised BEFORE any file is read when there is nothing to look up. A
    lookup that guesses at garbage input is worse than one that refuses."""


# ---------------------------------------------------------------------------
# Cache directory
# ---------------------------------------------------------------------------
def data_dir() -> Path:
    """The local reference cache. ``$ELLIS_USCIS_EMPLOYER_HUB_DIR`` overrides
    the default ``backend/data/reference/uscis_employer_hub/``."""
    override = os.getenv("ELLIS_USCIS_EMPLOYER_HUB_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    # backend/app/providers/uscis_employer_hub.py -> backend/data/reference/...
    return Path(__file__).resolve().parents[2] / "data" / "reference" / "uscis_employer_hub"


def year_path(year: int) -> Path:
    return data_dir() / f"{int(year)}.csv"


def available_years() -> list[int]:
    """Fiscal years actually present on disk, ascending. Never raises: an
    unreadable directory is an empty cache, which degrades honestly."""
    out: list[int] = []
    try:
        entries = list(data_dir().iterdir())
    except OSError:
        return out
    for entry in entries:
        m = re.fullmatch(r"(\d{4})\.csv", entry.name)
        if not m:
            continue
        try:
            if entry.is_file() and entry.stat().st_size > 0:
                out.append(int(m.group(1)))
        except OSError:
            continue
    return sorted(out)


def is_available() -> bool:
    """True only when at least one fiscal-year file is really on disk."""
    return bool(available_years())


def is_configured() -> bool:
    """True only when this deployment can actually answer an employer lookup:
    the feed is enabled AND there is cached government data to read."""
    return bool(settings().uscis_employer_hub_enabled) and is_available()


# ---------------------------------------------------------------------------
# CSV loading — cached by path + mtime, so a refreshed year invalidates itself
# ---------------------------------------------------------------------------
_CACHE: dict[str, tuple[int, dict]] = {}
# Fiscal years whose file is on disk but could NOT be parsed. Kept because an
# unreadable year yields an empty index, and an empty index is indistinguishable
# from "this employer never filed" unless the failure is reported.
_UNREADABLE: set[int] = set()


def reset_cache() -> None:
    _CACHE.clear()
    _UNREADABLE.clear()


def normalize_employer_name(value) -> str:
    """USCIS uppercases and strips punctuation from employer names. Match on the
    same normalization so a human typing "Acme, Inc." finds "ACME INC" — this
    normalizes text, it never resolves corporate identity."""
    text = re.sub(r"[^A-Z0-9 ]+", " ", str(value or "").upper())
    return re.sub(r"\s+", " ", text).strip()


def _fein_last4(value) -> str:
    """Reduce anything FEIN-shaped to the last four digits — the only part the
    government file carries. The full number never leaves this function, is
    never stored on a result, and is never logged."""
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-4:] if len(digits) >= 4 else ""


def _to_int(value) -> int:
    try:
        return int(str(value).strip() or 0)
    except (TypeError, ValueError):
        return 0


def _load_year(year: int) -> dict:
    """Parse one fiscal-year export into {normalized_name: [rows]}. A missing or
    malformed file yields an empty index rather than raising — the caller
    degrades honestly on `available_years()`."""
    path = year_path(year)
    key = str(path)
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        _CACHE.pop(key, None)
        return {}
    cached = _CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    index: dict[str, list[dict]] = {}
    try:
        with path.open(newline="", encoding="utf-8-sig", errors="replace") as fh:
            reader = csv.DictReader(fh)
            if "Employer" not in (reader.fieldnames or []):
                # Not the government export. Every row would be dropped for
                # want of an employer name, and the year would then answer
                # "no such employer" about a file it never read.
                _UNREADABLE.add(int(year))
                return {}
            for raw in reader:
                if not isinstance(raw, dict):
                    continue
                name = normalize_employer_name(raw.get("Employer"))
                if not name:
                    # A row with no employer cannot be attributed to one.
                    continue
                index.setdefault(name, []).append({
                    "employer": str(raw.get("Employer") or "").strip(),
                    "fiscal_year": _to_int(raw.get("Fiscal Year")) or int(year),
                    "counts": {k: _to_int(raw.get(c))
                               for k, c in zip(_COUNT_KEYS, _COUNT_COLUMNS)},
                    "naics": str(raw.get("NAICS") or "").strip(),
                    "tax_id_last4": str(raw.get("Tax ID") or "").strip(),
                    "state": str(raw.get("State") or "").strip(),
                    "city": str(raw.get("City") or "").strip(),
                    "zip": str(raw.get("ZIP") or "").strip(),
                })
    except OSError:
        _UNREADABLE.add(int(year))
        return {}
    except (csv.Error, UnicodeError):
        # A corrupt export is an unavailable year, never a partial invented one
        # — and never a silent one: the year is remembered as unreadable so a
        # lookup over it cannot be reported as a clean "not found".
        _UNREADABLE.add(int(year))
        return {}

    _UNREADABLE.discard(int(year))
    _CACHE[key] = (mtime, index)
    return index


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------
def _coverage(years_loaded: list[int]) -> dict:
    return {
        "years_loaded": years_loaded,
        "published_bulk_years": list(PUBLISHED_BULK_YEARS),
        "latest_published_bulk_year": (PUBLISHED_BULK_YEARS[-1]
                                       if PUBLISHED_BULK_YEARS else None),
        "note": ("The bulk export stops at FY"
                 f"{PUBLISHED_BULK_YEARS[-1] if PUBLISHED_BULK_YEARS else '?'}; "
                 "more recent fiscal years exist only in the USCIS Tableau UI "
                 "and are NOT reflected here."),
    }


def _unavailable(reason: str, *, query: dict, years_loaded: list[int]) -> dict:
    return {
        "status": UNAVAILABLE,
        "available": False,
        "live": False,
        "query": query,
        "matches": [],
        "match_kind": "none",
        "coverage": _coverage(years_loaded),
        "source": SOURCE,
        "source_url": HUB_URL,
        "caveat": CAVEAT,
        "note": reason,
    }


def _approval_rate(totals: dict):
    """Approvals over all adjudications. Returns None when the denominator is
    zero — an employer with no adjudicated petitions has no rate, and 0.0 would
    read as "always denied"."""
    approvals = totals["initial_approval"] + totals["continuing_approval"]
    denials = totals["initial_denial"] + totals["continuing_denial"]
    decided = approvals + denials
    if decided <= 0:
        return None
    return round(approvals / decided, 4)


def _summarize(name: str, rows: list[dict]) -> dict:
    totals = {k: 0 for k in _COUNT_KEYS}
    by_year: dict[int, dict] = {}
    naics: list[str] = []
    tax_ids: list[str] = []
    locations: list[dict] = []
    for row in rows:
        for k in _COUNT_KEYS:
            totals[k] += row["counts"][k]
        year_bucket = by_year.setdefault(row["fiscal_year"],
                                         {k: 0 for k in _COUNT_KEYS})
        for k in _COUNT_KEYS:
            year_bucket[k] += row["counts"][k]
        if row["naics"] and row["naics"] not in naics:
            naics.append(row["naics"])
        if row["tax_id_last4"] and row["tax_id_last4"] not in tax_ids:
            tax_ids.append(row["tax_id_last4"])
        loc = {"city": row["city"], "state": row["state"], "zip": row["zip"]}
        if any(loc.values()) and loc not in locations:
            locations.append(loc)
    return {
        # The government's own spelling, not the normalized match key.
        "employer": rows[0]["employer"],
        "normalized_name": name,
        "fiscal_years": {str(y): by_year[y] for y in sorted(by_year)},
        "totals": totals,
        "approval_rate": _approval_rate(totals),
        "naics": sorted(naics),
        # Explicitly last-four-only, and plural: several distinct employers can
        # share the same four digits.
        "tax_id_last4": sorted(tax_ids),
        "locations": locations,
        "row_count": len(rows),
    }


def lookup_employer(name=None, *, fein=None, years=None, limit: int = 25) -> dict:
    """Look up an employer's prior H-1B approvals and denials by fiscal year.

    Give a `name`, a `fein`, or both. Returns {status, available, live, query,
    matches, match_kind, coverage, source, source_url, caveat}. Each match
    carries {employer, fiscal_years: {"2023": {initial_approval, initial_denial,
    continuing_approval, continuing_denial}, ...}, totals, approval_rate, naics,
    tax_id_last4, locations, row_count}.

    `match_kind` is `exact_normalized` (the normalized names are equal),
    `contains` (the query is a substring — a candidate, not a confirmation), or
    `none`. A `fein` narrows results to rows whose last four digits match and
    sets `fein_match: "last4_only"` on the result; it can never confirm identity
    because the government file carries no more than four digits.

    Never touches the network. With no cached fiscal-year file it returns
    `available: False` with the reason and an EMPTY match list — it never
    invents a count and never implies "this employer has never filed".

    Raises `InvalidEmployerQuery` when neither a usable name nor a usable FEIN
    is supplied — checked before any file is opened.
    """
    normalized = normalize_employer_name(name)
    last4 = _fein_last4(fein)
    if not normalized and not last4:
        raise InvalidEmployerQuery(
            "an employer name or a FEIN (at least four digits) is required")
    if fein and not last4:
        raise InvalidEmployerQuery(
            "a FEIN must contain at least four digits to match the "
            "government file, which publishes only the last four")
    if not isinstance(limit, int) or limit < 1:
        raise InvalidEmployerQuery("limit must be a positive integer")

    # The query echo carries the last four digits only. The full FEIN a caller
    # passed is never placed on a result and never logged.
    query = {"name": normalized or None, "fein_last4": last4 or None,
             "years": None}

    loaded = available_years()
    if not settings().uscis_employer_hub_enabled:
        return _unavailable("USCIS H-1B Employer Data Hub feed is disabled",
                            query=query, years_loaded=loaded)
    if not loaded:
        return _unavailable(
            f"no cached H-1B Employer Data Hub file in {data_dir()}; "
            f"{NO_API_REASON} Run fetch_year(<year>, execute=True) or place "
            "<year>.csv there to enable lookups.",
            query=query, years_loaded=loaded)

    if years is None:
        wanted = loaded
    else:
        try:
            wanted = sorted({int(y) for y in years})
        except (TypeError, ValueError):
            raise InvalidEmployerQuery("years must be integers") from None
        wanted = [y for y in wanted if y in loaded]
        if not wanted:
            return _unavailable(
                "the requested fiscal years are not cached "
                f"(cached: {loaded or 'none'})",
                query={**query, "years": sorted({int(y) for y in years})},
                years_loaded=loaded)
    query["years"] = wanted

    exact: dict[str, list[dict]] = {}
    partial: dict[str, list[dict]] = {}
    for year in wanted:
        index = _load_year(year)
        if normalized:
            for key, rows in index.items():
                if key == normalized:
                    exact.setdefault(key, []).extend(rows)
                elif normalized in key:
                    partial.setdefault(key, []).extend(rows)
        else:
            for key, rows in index.items():
                bucket = [r for r in rows if r["tax_id_last4"] == last4]
                if bucket:
                    partial.setdefault(key, []).extend(bucket)

    # An exact normalized name match wins outright; substring candidates only
    # answer when nothing matched exactly, and say so via match_kind.
    chosen, match_kind = ((exact, "exact_normalized") if exact
                          else (partial, "contains" if normalized else "fein_last4"))
    if last4 and normalized:
        chosen = {k: [r for r in rows if r["tax_id_last4"] == last4]
                  for k, rows in chosen.items()}
        chosen = {k: rows for k, rows in chosen.items() if rows}

    matches = [_summarize(k, rows) for k, rows in chosen.items() if rows]
    matches.sort(key=lambda m: (-sum(m["totals"].values()), m["normalized_name"]))
    truncated = len(matches) > limit
    matches = matches[:limit]
    unreadable = sorted(y for y in wanted if y in _UNREADABLE)

    result = {
        "status": NOT_FOUND if not matches else "ok",
        "available": True,
        "live": bool(matches),
        "query": query,
        "matches": matches,
        "match_kind": match_kind if matches else "none",
        "truncated": truncated,
        "coverage": _coverage(loaded),
        "source": SOURCE,
        "source_url": HUB_URL,
        "caveat": CAVEAT,
    }
    if last4:
        # Say the limit of the evidence on the result itself, so no caller can
        # present a four-digit coincidence as a verified employer identity.
        result["fein_match"] = "last4_only"
        result["fein_match_note"] = (
            "The government file publishes only the last four FEIN digits; "
            "this narrows candidates and does not confirm the employer.")
    if unreadable:
        # Said on the result, not only in a log: a year that could not be read
        # was searched and answered nothing, which looks exactly like a year
        # that was read and held nothing.
        result["unreadable_years"] = unreadable
        result["coverage"]["unreadable_years"] = unreadable
        result["coverage"]["years_searched"] = [y for y in wanted
                                                if y not in unreadable]
    if not matches:
        result["note"] = (
            "No row in the cached fiscal years names this employer. That is not "
            "evidence the employer has never filed: the cache covers "
            f"{loaded[0]}-{loaded[-1]} and USCIS normalizes employer names.")
        if unreadable:
            result["note"] += (
                f" FY{', FY'.join(str(y) for y in unreadable)} could not be "
                "read at all, so this answer does not cover them.")
    return result


# ---------------------------------------------------------------------------
# Refresh — explicit, opt-in, never on the lookup path
# ---------------------------------------------------------------------------
def _http_bytes(url: str) -> tuple[int, bytes]:
    """The single network seam. Only `fetch_year(execute=True)` reaches it; a
    lookup never does. A test that stubs this proves nothing escaped."""
    import httpx
    r = httpx.get(url, timeout=_TIMEOUT_SECONDS, follow_redirects=True)
    return r.status_code, r.content


def fetch_year(year: int, *, execute: bool = False, dest_dir=None) -> dict:
    """Populate the local cache for one fiscal year from the USCIS bulk export.

    Returns the plan {year, url, dest, commands, note}. With `execute=True` it
    actually downloads and writes `<year>.csv` (default `execute=False`, so a
    test can never pull 2MB from uscis.gov by accident).

    Refuses a year USCIS does not publish as a bulk export rather than fetching
    a URL that will 404 — FY2024 onward exist only in the Tableau UI.
    """
    year = int(year)
    dest = (Path(dest_dir).expanduser() if dest_dir is not None else data_dir())
    url = BULK_EXPORT_URL_TEMPLATE.format(year=year)
    target = dest / f"{year}.csv"
    plan = {
        "year": year,
        "url": url,
        "dest": str(target),
        "commands": [f'mkdir -p "{dest}"', f'curl -L --fail -o "{target}" "{url}"'],
        "published": year in PUBLISHED_BULK_YEARS,
        "note": ("Free, no API key. " + NO_API_REASON),
    }
    if year not in PUBLISHED_BULK_YEARS:
        return {**plan, "ok": False,
                "error": (f"USCIS publishes no bulk export for FY{year} "
                          f"(available: FY{PUBLISHED_BULK_YEARS[0]}-"
                          f"FY{PUBLISHED_BULK_YEARS[-1]})")}
    if not execute:
        return plan

    try:
        code, content = _http_bytes(url)
    except Exception:  # noqa: BLE001 - never surface transport internals
        return {**plan, "ok": False, "error": "USCIS bulk export unreachable"}
    if code >= 400 or not content:
        return {**plan, "ok": False,
                "error": f"USCIS bulk export returned HTTP {code}"}
    try:
        dest.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    except OSError as exc:
        return {**plan, "ok": False, "error": f"could not write cache file: {exc}"}
    reset_cache()
    return {**plan, "ok": True, "bytes": len(content)}


def refresh_status() -> dict:
    """Cache health, for an operator or a capability probe. Never raises and
    never fetches."""
    loaded = available_years()
    return {
        "enabled": bool(settings().uscis_employer_hub_enabled),
        "configured": is_configured(),
        "dir": str(data_dir()),
        "years_loaded": loaded,
        "missing_published_years": [y for y in PUBLISHED_BULK_YEARS
                                    if y not in loaded],
        "coverage": _coverage(loaded),
        "source": SOURCE,
        "source_url": HUB_URL,
        "documentation_url": UNDERSTANDING_URL,
        "access_note": NO_API_REASON,
    }
