"""OFLC prevailing-wage level computation — offline, deterministic, honest.

The wage level on an LCA/I-129 is a legal representation, not a lookup. Ellis
COMPUTES a suggestion from the official U.S. DOL Office of Foreign Labor
Certification (OFLC) OEWS wage file and a human confirms it. This module never
files a value, never fabricates a wage, and never guesses when the government
data does not support a clean answer.

Data source (free, no key, refreshed each July 1):
  https://flag.dol.gov/sites/default/files/wages/OFLC_Wages_2026-27.zip
Unzipped it carries the four CSVs this module reads from a local cache dir
(default ``backend/data/oflc/`` or ``$ELLIS_OFLC_WAGE_DIR``):

  ALC_Export.csv    Area,SocCode,GeoLvl,Level1..4,Average,Label  (wages HOURLY)
  Geography.csv     Area,AreaName,StateAb,State,CountyTownName
  oes_soc_occs.csv  soccode,Title,Description
  xwalk_plus.csv    OES_SOCCODE,OES_SOCTITLE,TruncOnetCode,OnetCode,ONetTitle

Honesty invariants baked in here:
  * A missing cache dir degrades honestly (``available: False``) — never a
    fabricated level.
  * Wages are hourly; the annual figure is ``hourly * 2080`` rounded. When the
    DOL row is labelled "High Wage" (OEWS cannot estimate, >= $115/hr) or
    "Annual Wage" (the estimate is not on a 2080-hour basis), the 2080
    conversion is INVALID: the row is surfaced with ``invalid_2080_conversion``
    and NO level is invented.
  * ``GeoLvl >= 2`` means the DOL estimate fell off the worksite MSA; the
    fallback is surfaced as a caveat, because a wrong worksite area is a real
    filing error.
  * Every result carries its ``source`` and ``as_of`` date.

The wage-level computation is a pure offline read over the cached CSV — there is
no live network call at fill time.
"""
from __future__ import annotations

import csv
import os
import re
from pathlib import Path

# --- provenance -------------------------------------------------------------
WAGE_DATA_URL = "https://flag.dol.gov/sites/default/files/wages/OFLC_Wages_2026-27.zip"
WAGE_DATA_PERIOD = "2026-2027"          # OFLC wage year, effective each July 1
WAGE_DATA_AS_OF = "2026-07-01"          # ISO effective date of this vintage
SOURCE = "U.S. DOL OFLC prevailing-wage data (OEWS/ACWIA), flag.dol.gov"

# Full-time-year hour basis OEWS uses to annualise an hourly wage.
HOURS_PER_YEAR = 2080

_ALC_FILE = "ALC_Export.csv"
_GEO_FILE = "Geography.csv"
_SOC_FILE = "oes_soc_occs.csv"

# wage_unit -> multiplier that turns one period into an annual figure.
_UNIT_FACTORS = {
    "hour": HOURS_PER_YEAR, "hourly": HOURS_PER_YEAR, "hr": HOURS_PER_YEAR, "h": HOURS_PER_YEAR,
    "week": 52, "weekly": 52, "wk": 52,
    "biweek": 26, "biweekly": 26,
    "month": 12, "monthly": 12, "mo": 12,
    "year": 1, "yearly": 1, "annual": 1, "annum": 1, "yr": 1,
}


# --- cache dir + availability ----------------------------------------------
def wage_dir() -> Path:
    """The local OFLC cache directory. ``$ELLIS_OFLC_WAGE_DIR`` overrides the
    default ``backend/data/oflc/`` (config.py is intentionally not touched)."""
    override = os.getenv("ELLIS_OFLC_WAGE_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    # backend/app/h1b/wage_data.py -> backend/data/oflc
    return Path(__file__).resolve().parents[2] / "data" / "oflc"


def _alc_path() -> Path:
    return wage_dir() / _ALC_FILE


def is_available() -> bool:
    """True only when the essential wage table is really on disk. The level
    computation depends on ALC_Export.csv; without it we degrade honestly."""
    p = _alc_path()
    try:
        return p.is_file() and p.stat().st_size > 0
    except OSError:
        return False


def ensure_wage_data(*, download: bool = False) -> dict:
    """Report cache health (and optionally fetch). Never raises on a missing
    cache — callers degrade honestly on the returned ``available`` flag.

    Set ``download=True`` to actually pull the zip (never done in tests)."""
    d = wage_dir()
    present = {name: (d / name).is_file() for name in (_ALC_FILE, _GEO_FILE, _SOC_FILE)}
    missing = [name for name, ok in present.items() if not ok]
    status = {
        "available": is_available(),
        "dir": str(d),
        "present": present,
        "missing": missing,
        "source": SOURCE,
        "as_of": WAGE_DATA_AS_OF,
        "period": WAGE_DATA_PERIOD,
        "fetch": download_plan(d),
    }
    if download and missing:
        status["download_result"] = download_wage_data(d, execute=True)
        status["available"] = is_available()
    return status


# --- fetch helper (documented; never auto-runs in tests) --------------------
def download_plan(dest_dir: Path | str | None = None) -> dict:
    """The documented, resumable download recipe. ``curl --continue-at -``
    uses HTTP Range so a partial ~12.7MB pull resumes instead of restarting."""
    dest = Path(dest_dir) if dest_dir is not None else wage_dir()
    zip_path = dest / "OFLC_Wages_2026-27.zip"
    return {
        "url": WAGE_DATA_URL,
        "dest_dir": str(dest),
        "zip_path": str(zip_path),
        "commands": [
            f'mkdir -p "{dest}"',
            f'curl -L --fail --continue-at - -o "{zip_path}" "{WAGE_DATA_URL}"',
            f'unzip -o "{zip_path}" -d "{dest}"',
        ],
        "note": ("Free, no API key. Refreshed each July 1. The download is not "
                 "run by tests; tests read a committed fixture via "
                 "$ELLIS_OFLC_WAGE_DIR."),
    }


def download_wage_data(dest_dir: Path | str | None = None, *, execute: bool = False) -> dict:
    """Return the download plan. With ``execute=True`` actually run curl + unzip
    (guarded so a test never touches the network). Default ``execute=False``."""
    dest = Path(dest_dir) if dest_dir is not None else wage_dir()
    plan = download_plan(dest)
    if not execute:
        return plan
    import subprocess  # local import: only reached on an explicit real fetch
    dest.mkdir(parents=True, exist_ok=True)
    zip_path = Path(plan["zip_path"])
    curl = subprocess.run(
        ["curl", "-L", "--fail", "--continue-at", "-", "-o", str(zip_path), WAGE_DATA_URL],
        capture_output=True, text=True, timeout=600)
    if curl.returncode != 0:
        return {**plan, "ok": False, "stage": "curl", "returncode": curl.returncode}
    unzip = subprocess.run(["unzip", "-o", str(zip_path), "-d", str(dest)],
                           capture_output=True, text=True, timeout=120)
    return {**plan, "ok": unzip.returncode == 0, "stage": "unzip",
            "returncode": unzip.returncode}


# --- CSV loading (cached by path + mtime) -----------------------------------
_CACHE: dict[str, tuple[int, object]] = {}


def _load_rows(path: Path) -> list[dict]:
    """Read a CSV once and memoise it, keyed by path + mtime so a fixture swap
    (tests) or a July refresh (prod) invalidates the cache automatically."""
    key = str(path)
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        _CACHE.pop(key, None)
        return []
    cached = _CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]  # type: ignore[return-value]
    with path.open(newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    _CACHE[key] = (mtime, rows)
    return rows


def _norm_soc(soc_code: str) -> str:
    """Accept ``11-1011``, ``111011`` or ``11-1011.00`` -> canonical ``NN-NNNN``."""
    digits = re.sub(r"\D", "", str(soc_code or ""))
    if len(digits) >= 6:
        return f"{digits[:2]}-{digits[2:6]}"
    return str(soc_code or "").strip()


def _alc_index() -> dict[tuple[str, str], dict]:
    idx: dict[tuple[str, str], dict] = {}
    for row in _load_rows(_alc_path()):
        area = (row.get("Area") or "").strip()
        soc = _norm_soc(row.get("SocCode") or "")
        if area and soc:
            idx[(area, soc)] = row
    return idx


def _soc_titles() -> dict[str, str]:
    out: dict[str, str] = {}
    for row in _load_rows(wage_dir() / _SOC_FILE):
        code = _norm_soc(row.get("soccode") or "")
        title = (row.get("Title") or "").strip()
        if code and title:
            out[code] = title
    return out


# --- parsing helpers --------------------------------------------------------
def _to_float(value) -> float | None:
    s = str(value or "").strip().replace(",", "").replace("$", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _is_invalid_label(label: str) -> bool:
    """DOL flags a row's estimate as un-annualisable. Either flag voids the
    2080-hour conversion."""
    low = (label or "").strip().lower()
    return "high wage" in low or "annual wage" in low


def _published_period(label: str) -> str | None:
    """The time basis of the as-published Level values. Blank label -> hourly
    (2080 basis). 'Annual Wage' -> the values are already annual. 'High Wage'
    -> OEWS cannot estimate, so the basis is undefined."""
    low = (label or "").strip().lower()
    if "high wage" in low:
        return None
    if "annual wage" in low:
        return "year"
    return "hour"


def _geo_caveat(geo_level: int | None) -> str | None:
    if geo_level is None or geo_level <= 1:
        return None
    if geo_level == 2:
        return ("DOL fell off the worksite MSA to the area plus contiguous "
                "counties (GeoLvl 2); confirm the worksite area.")
    if geo_level == 3:
        return ("DOL fell back to a statewide estimate (GeoLvl 3); the worksite "
                "MSA had no OEWS estimate. Confirm the worksite area.")
    return ("DOL fell back to a national estimate (GeoLvl 4); no state or area "
            "OEWS estimate was available. Confirm the worksite area.")


def _normalize_annual(offered_wage: float, wage_unit: str) -> int:
    unit = str(wage_unit or "").strip().lower()
    factor = _UNIT_FACTORS.get(unit)
    if factor is None:
        raise ValueError(f"unknown wage_unit: {wage_unit!r}")
    return round(float(offered_wage) * factor)


def _base_result(area_code: str, soc_code: str, offered_wage, wage_unit) -> dict:
    """Every return shares this shape so callers never guess which keys exist."""
    return {
        "available": is_available(),
        "area_code": (str(area_code) if area_code is not None else None),
        "soc_code": _norm_soc(soc_code) if soc_code else None,
        "soc_title": None,
        "level": None,
        "level_wages": None,          # trusted ANNUAL basis {1..4}, or None
        "raw_levels": None,           # DOL as-published Level1..4, or None
        "raw_levels_period": None,    # "hour" | "year" | None
        "offered_wage": offered_wage,
        "wage_unit": wage_unit,
        "offered_annual": None,
        "meets_prevailing": None,
        "geo_level": None,
        "geo_caveat": None,
        "label": None,
        "invalid_2080_conversion": False,
        "average_hourly": None,
        "average_annual": None,
        "caveats": [],
        "status": None,
        "source": SOURCE,
        "as_of": WAGE_DATA_AS_OF,
    }


# --- the crown jewel --------------------------------------------------------
def compute_wage_level(*, area_code, soc_code, offered_wage, wage_unit) -> dict:
    """Suggest the OFLC wage LEVEL (1-4) for a worksite area + SOC + offer.

    Deterministic, offline. The result is a SUGGESTION a human confirms — never
    an auto-filed value. Keys always present (see ``_base_result``); the ones
    that matter:
      level                    1-4, or None (below Level I / undeterminable)
      level_wages              annual prevailing wage per level, or None
      meets_prevailing         offer >= Level I annual, or None
      geo_level / geo_caveat   OEWS geographic fallback (>=2 left the MSA)
      invalid_2080_conversion  DOL row is High/Annual — 2080 basis void
      source / as_of           provenance on every result
    """
    res = _base_result(area_code, soc_code, offered_wage, wage_unit)

    if not is_available():
        res["status"] = ("OFLC wage data not downloaded. Run the fetch plan "
                         "(download_plan) or set $ELLIS_OFLC_WAGE_DIR.")
        res["caveats"].append(res["status"])
        return res

    area = (str(area_code) if area_code is not None else "").strip()
    soc = _norm_soc(soc_code)
    row = _alc_index().get((area, soc))
    if row is None:
        res["status"] = f"No OFLC wage row for area {area or '?'} SOC {soc or '?'}."
        res["caveats"].append(res["status"])
        res["soc_title"] = _soc_titles().get(soc)
        return res

    res["soc_title"] = _soc_titles().get(soc)
    label = (row.get("Label") or "").strip()
    res["label"] = label or None
    geo_level = None
    gl = _to_float(row.get("GeoLvl"))
    if gl is not None:
        geo_level = int(gl)
    res["geo_level"] = geo_level
    res["geo_caveat"] = _geo_caveat(geo_level)
    if res["geo_caveat"]:
        res["caveats"].append(res["geo_caveat"])

    raw = {i: _to_float(row.get(f"Level{i}")) for i in (1, 2, 3, 4)}
    res["raw_levels"] = raw
    res["raw_levels_period"] = _published_period(label)
    avg = _to_float(row.get("Average"))
    res["average_hourly"] = avg if res["raw_levels_period"] == "hour" else None

    # High Wage / Annual Wage: the 2080 conversion is invalid. Surface the
    # as-published numbers, flag it, and refuse to invent a level or verdict.
    if _is_invalid_label(label):
        res["invalid_2080_conversion"] = True
        res["status"] = (
            f"DOL labelled this row '{label}': the 2080-hour annualisation does "
            "not apply. A human must set the wage basis; no level was computed.")
        res["caveats"].append(res["status"])
        return res

    # Normal (hourly) row: annualise the DOL levels and the offer, then place
    # the offer against the levels.
    if any(v is None for v in raw.values()):
        res["status"] = "DOL row is missing one or more level wages; no level computed."
        res["caveats"].append(res["status"])
        return res

    level_wages = {i: round(raw[i] * HOURS_PER_YEAR) for i in (1, 2, 3, 4)}
    res["level_wages"] = level_wages
    if avg is not None:
        res["average_annual"] = round(avg * HOURS_PER_YEAR)

    if offered_wage is None:
        res["status"] = "No offered wage provided; level_wages returned for reference."
        res["caveats"].append(res["status"])
        return res

    offered_annual = _normalize_annual(offered_wage, wage_unit)
    res["offered_annual"] = offered_annual

    meets = offered_annual >= level_wages[1]
    res["meets_prevailing"] = meets
    if not meets:
        # Below Level I: the offer does not meet the prevailing-wage floor.
        res["level"] = None
        res["status"] = ("Offered wage is below Level I prevailing wage; it does "
                         "not meet the prevailing wage for this area and SOC.")
        res["caveats"].append(res["status"])
        return res

    level = 1
    for i in (2, 3, 4):
        if offered_annual >= level_wages[i]:
            level = i
    res["level"] = level
    return res


# --- geography + occupation lookups (best-effort, honest) -------------------
def soc_title(soc_code: str) -> str | None:
    """The SOC occupation title, or None when unknown / data unavailable."""
    return _soc_titles().get(_norm_soc(soc_code))


def lookup_area(query: str) -> dict:
    """Best-effort worksite area lookup. The worksite COUNTY drives the OEWS
    area, and a wrong area is a real filing error, so ambiguity is surfaced as
    candidates rather than resolved by guesswork.

    Accepts an area code, a county/town name, a state name or 2-letter code,
    optionally state-qualified ("Jones County, TX"). Geography.csv carries no
    ZIP column, so a raw ZIP degrades to an honest 'needs a ZIP->county
    crosswalk' note rather than a fabricated area.
    """
    out = {
        "available": False, "query": query, "area_code": None,
        "ambiguous": False, "candidates": [], "note": None,
        "source": SOURCE, "as_of": WAGE_DATA_AS_OF,
    }
    rows = _load_rows(wage_dir() / _GEO_FILE)
    if not rows:
        out["note"] = ("Geography.csv not downloaded; cannot resolve a worksite "
                       "area. Run the fetch plan or set $ELLIS_OFLC_WAGE_DIR.")
        return out
    out["available"] = True

    q = str(query or "").strip()
    if not q:
        out["note"] = "Empty query."
        return out

    # Exact area code (numeric) first.
    if q.isdigit():
        hit = {(r.get("Area") or "").strip() for r in rows if (r.get("Area") or "").strip() == q}
        if hit:
            out["area_code"] = q
            out["candidates"] = _area_candidates(rows, {q})
            return out
        if len(q) == 5:
            out["note"] = ("A 5-digit ZIP has no direct area mapping in "
                           "Geography.csv (which is county-keyed); resolve the "
                           "worksite county, then look that up.")
            return out
        out["note"] = f"No OFLC area matches code {q!r}."
        return out

    # Optional trailing state hint: "Jones County, TX" or "... Texas".
    name, state_hint = q, None
    if "," in q:
        name, tail = q.rsplit(",", 1)
        name, state_hint = name.strip(), tail.strip()

    def _state_match(row: dict, hint: str) -> bool:
        h = hint.lower()
        return h in (row.get("StateAb") or "").lower() or h in (row.get("State") or "").lower()

    needle = name.lower()
    matched_areas: set[str] = set()
    for r in rows:
        if state_hint and not _state_match(r, state_hint):
            continue
        haystacks = [
            (r.get("CountyTownName") or ""),
            (r.get("AreaName") or ""),
            (r.get("State") or ""),
            (r.get("StateAb") or ""),
        ]
        if any(needle in h.lower() for h in haystacks if h):
            matched_areas.add((r.get("Area") or "").strip())
    matched_areas.discard("")

    if not matched_areas:
        out["note"] = f"No OFLC area matched {query!r}."
        return out
    out["candidates"] = _area_candidates(rows, matched_areas)
    if len(matched_areas) == 1:
        out["area_code"] = next(iter(matched_areas))
    else:
        out["ambiguous"] = True
        out["note"] = (f"{len(matched_areas)} areas match {query!r}; the worksite "
                       "county decides. Confirm which area before filing.")
    return out


def _area_candidates(rows: list[dict], areas: set[str]) -> list[dict]:
    seen: dict[str, dict] = {}
    for r in rows:
        a = (r.get("Area") or "").strip()
        if a in areas and a not in seen:
            seen[a] = {
                "area_code": a,
                "area_name": (r.get("AreaName") or "").strip(),
                "state": (r.get("State") or "").strip(),
                "state_ab": (r.get("StateAb") or "").strip(),
            }
    return sorted(seen.values(), key=lambda c: c["area_code"])
