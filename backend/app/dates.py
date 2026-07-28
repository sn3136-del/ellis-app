"""Canonical calendar-date handling for the passport/visa pipeline.

ONE internal representation everywhere: ISO ``YYYY-MM-DD`` strings (or
``datetime.date``). Applicant-facing DISPLAY is U.S. ``MM/DD/YYYY`` (see
``to_display``). Government portals get the exact format THEIR specification
requires (see ``to_portal`` with a per-adapter pattern) — never the UI format.

Passport dates are calendar dates, not timestamps: nothing in this module
touches timezones, so a date can never shift by a day in conversion.

Century resolution for ICAO MRZ ``YYMMDD`` values is contextual, never a fixed
pivot: a birth date must be a plausible PAST date, an expiry a plausible
recent-past/future date, an issue date a plausible past date. Printed-zone
parsing is deterministic (English month names, ICAO MRZ, ISO, Chinese
passport ``D M月/MON YYYY`` layouts) — ambiguous all-numeric forms such as
``05/04/2031`` are REJECTED rather than guessed.
"""
from __future__ import annotations

import re
from datetime import date

_MONTHS = {
    "JAN": 1, "JANUARY": 1, "FEB": 2, "FEBRUARY": 2, "MAR": 3, "MARCH": 3,
    "APR": 4, "APRIL": 4, "MAY": 5, "JUN": 6, "JUNE": 6, "JUL": 7, "JULY": 7,
    "AUG": 8, "AUGUST": 8, "SEP": 9, "SEPT": 9, "SEPTEMBER": 9, "OCT": 10,
    "OCTOBER": 10, "NOV": 11, "NOVEMBER": 11, "DEC": 12, "DECEMBER": 12,
}

_MONTH_ABBR = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
               "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
_MONTH_FULL = ("JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY",
               "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER")

ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def is_iso(value) -> bool:
    return bool(ISO_RE.match(str(value or "")))


def to_iso(d: date | None) -> str:
    return d.isoformat() if d else ""


# ---------------------------------------------------------------------------
# ICAO MRZ YYMMDD with CONTEXTUAL century resolution.

def parse_mrz_date(yymmdd: str, *, kind: str, today: date | None = None) -> date | None:
    """``kind`` is ``birth`` | ``expiry`` | ``issue``.

    - birth:  the latest century candidate that is NOT in the future
              (a future birth date is impossible);
    - expiry: the candidate inside [today - 30y, today + 30y]; if none is,
              the candidate closest to today (expiries live near the present);
    - issue:  the latest candidate not after today (documents are not issued
              in the future).
    """
    s = str(yymmdd or "").strip()
    if not re.fullmatch(r"\d{6}", s):
        return None
    today = today or date.today()
    yy, mm, dd = int(s[0:2]), int(s[2:4]), int(s[4:6])
    candidates = [c for c in (_safe_date(1900 + yy, mm, dd),
                              _safe_date(2000 + yy, mm, dd)) if c]
    if not candidates:
        return None
    if kind == "birth":
        past = [c for c in candidates if c <= today]
        return max(past) if past else None
    if kind == "issue":
        past = [c for c in candidates if c <= today]
        return max(past) if past else None
    if kind == "expiry":
        lo = _shift_years(today, -30)
        hi = _shift_years(today, 30)
        inside = [c for c in candidates if lo <= c <= hi]
        if inside:
            return min(inside, key=lambda c: abs((c - today).days))
        return min(candidates, key=lambda c: abs((c - today).days))
    return None


def _shift_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:                      # Feb 29 in a non-leap target year
        return d.replace(year=d.year + years, day=28)


# ---------------------------------------------------------------------------
# Printed-zone parsing (deterministic; ambiguity is rejected, never guessed).

# 15 FEB 1990 / 12 Aug 1994 / 01 OCT 1988 / 5 May 2021
_D_MON_Y = re.compile(
    r"\b(\d{1,2})\s*[.\-/ ]\s*(" + "|".join(_MONTH_FULL + _MONTH_ABBR) +
    r")[a-z]*\s*[.\-/, ]\s*(\d{4})\b", re.IGNORECASE)
# May 5, 2021 / MAY 05 2021
_MON_D_Y = re.compile(
    r"\b(" + "|".join(_MONTH_FULL + _MONTH_ABBR) +
    r")[a-z]*\s*[.\- ]\s*(\d{1,2})\s*[,\s]\s*(\d{4})\b", re.IGNORECASE)
# Chinese passport layout: 21 3月/MAR 2018  (day, numeric month + 月, /, month name, year)
_CN_D_MY = re.compile(
    r"\b(\d{1,2})\s+(\d{1,2})月\s*/?\s*([A-Za-z]{3,9})?\s*(\d{4})\b")
# Chinese full form: 2018年3月21日
_CN_YMD = re.compile(r"\b(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日")
# All-numeric with 4-digit year: 18/05/2017, 05-18-2017, 2017.05.18
_NUM_DMY_OR_MDY = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b")
_NUM_YMD = re.compile(r"\b(\d{4})[./-](\d{1,2})[./-](\d{1,2})\b")


def parse_printed_date(text: str) -> date | None:
    """Parse ONE printed date from passport visual-zone text. Deterministic:
    English month names, ISO, Chinese-passport layouts, and unambiguous
    all-numeric forms only (a form that could be DD/MM or MM/DD is rejected)."""
    t = str(text or "").strip()
    if not t:
        return None
    m = ISO_RE.match(t)
    if m:
        return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _D_MON_Y.search(t)
    if m:
        month = _MONTHS.get(m.group(2).upper()[:3]) or _MONTHS.get(m.group(2).upper())
        return _safe_date(int(m.group(3)), month, int(m.group(1))) if month else None
    m = _MON_D_Y.search(t)
    if m:
        month = _MONTHS.get(m.group(1).upper()[:3]) or _MONTHS.get(m.group(1).upper())
        return _safe_date(int(m.group(3)), month, int(m.group(2))) if month else None
    m = _CN_YMD.search(t)
    if m:
        return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _CN_D_MY.search(t)
    if m:
        day, mm_num, mon_name, year = m.groups()
        month = int(mm_num)
        if mon_name:
            named = _MONTHS.get(mon_name.upper()[:3]) or _MONTHS.get(mon_name.upper())
            if named and named != month:
                return None            # numeric month and month name disagree
        return _safe_date(int(year), month, int(day))
    m = _NUM_YMD.search(t)
    if m:
        return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _NUM_DMY_OR_MDY.search(t)
    if m:
        a, b, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if a > 12 and b <= 12:
            return _safe_date(year, b, a)      # unambiguous DD/MM/YYYY
        if b > 12 and a <= 12:
            return _safe_date(year, a, b)      # unambiguous MM/DD/YYYY
        return None                            # ambiguous — never guess
    return None


def normalize_any(value, *, kind: str = "expiry", today: date | None = None,
                  us_numeric: bool = False) -> str:
    """Best-effort normalization of a date value (ISO, MRZ YYMMDD, printed
    text) to canonical ISO. ``us_numeric=True`` additionally accepts
    ``MM/DD/YYYY`` (explicit U.S.-format context, e.g. applicant edits made in
    the U.S. display format). Returns '' when nothing parses."""
    s = str(value or "").strip()
    if not s:
        return ""
    if is_iso(s):
        d = parse_printed_date(s)
        return to_iso(d)
    if re.fullmatch(r"\d{6}", s):
        return to_iso(parse_mrz_date(s, kind=kind, today=today))
    if us_numeric:
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
        if m:
            d = _safe_date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
            if d:
                return to_iso(d)
    return to_iso(parse_printed_date(s))


# ---------------------------------------------------------------------------
# Output formatting.

def to_display(iso_value) -> str:
    """Applicant-facing U.S. display format MM/DD/YYYY. Pure string transform
    (no Date/timezone machinery — a calendar date can never shift). Anything
    that is not a canonical ISO date is returned unchanged."""
    m = ISO_RE.match(str(iso_value or ""))
    if not m:
        return str(iso_value or "")
    return f"{m.group(2)}/{m.group(3)}/{m.group(1)}"


_PORTAL_TOKENS = ("YYYY", "MONTH", "MON", "MM", "DD", "YY")


def to_portal(iso_value, fmt: str) -> str:
    """Format a canonical ISO date for a specific government portal using the
    adapter's declared pattern (tokens: YYYY, YY, MM, DD, MON, MONTH — e.g.
    'DD/MM/YYYY' for Vietnam eVisa, 'YYYYMMDD', 'DD-MON-YYYY'). NEVER the UI
    format by default. Non-ISO input or an empty pattern returns '' (fail
    closed — a portal must not receive a guessed date)."""
    m = ISO_RE.match(str(iso_value or ""))
    if not m or not fmt:
        return ""
    y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
    out = fmt
    replacements = {
        "YYYY": y,
        "MONTH": _MONTH_FULL[mo - 1].capitalize(),
        "MON": _MONTH_ABBR[mo - 1],
        "MM": f"{mo:02d}",
        "DD": f"{d:02d}",
        "YY": y[2:],
    }
    for token in _PORTAL_TOKENS:            # longest tokens first
        out = out.replace(token, replacements[token])
    return out


# ---------------------------------------------------------------------------
# Age — ALWAYS calculated from the normalized date of birth and today; an age
# guessed by OCR or a model is never accepted.

def derive_age(birth_iso, *, today: date | None = None) -> int | None:
    m = ISO_RE.match(str(birth_iso or ""))
    if not m:
        return None
    today = today or date.today()
    by, bm, bd = int(m.group(1)), int(m.group(2)), int(m.group(3))
    age = today.year - by - (1 if (today.month, today.day) < (bm, bd) else 0)
    return age if 0 <= age <= 130 else None


# ---------------------------------------------------------------------------
# Answer-key vocabulary — form answers arrive through several endpoints
# (document approval edits, POST /answers, mid-run provide_information) and
# every one must store canonical ISO. One shared rule for which keys hold
# calendar dates and which century pivot applies.

def date_kind_for_key(key: str) -> str | None:
    """``birth`` | ``issue`` | ``expiry`` for a date-like answer key, else
    None. ``expiry`` doubles as the pivot for travel dates (its ±30y window
    is the right plausibility band for arrivals/departures)."""
    k = str(key or "").lower()
    if not (k.endswith("_date") or k.endswith("_expiry")
            or k in ("date_of_birth", "date_of_expiry", "date_of_issue",
                     "intended_arrival", "intended_departure")):
        return None
    return "birth" if "birth" in k else "issue" if "issue" in k else "expiry"
