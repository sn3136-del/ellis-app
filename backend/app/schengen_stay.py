"""Schengen short stay: the 90/180 engine, the EES reality, ETIAS readiness.

Since 10 April 2026 the Entry/Exit System is fully operational at the external
borders of the 29 countries that use it. Passport stamps are gone, entries and
exits are digital records tied to a face and fingerprints, and — the fact that
changes the product — an overstay is now detected AUTOMATICALLY. Before EES, a
traveller who miscounted usually got away with it; now the machine counts, and
it counts at the border, in front of them.

So Ellis counts first, honestly, and shows its arithmetic.

WHAT THIS MODULE IS
===================
1. A deterministic 90/180 engine. The Commission's short-stay calculator
   methodology is PUBLISHED — a rolling 180-day look-back, a check mode and a
   planning mode — so replicating the arithmetic is sanctioned math, not
   scraping. Every number here is derived from dates the traveller gave Ellis
   and can be re-derived by hand from the payload.
2. Curated EES guidance: what happens at a first entry, that stamps are gone,
   that overstay is automatic, and a deep link so the traveller can check the
   EU's own remaining-days tool themselves.
3. An ETIAS READINESS check. Not a filing flow: ETIAS is not in force, there is
   no application portal, and anybody accepting an ETIAS application today is
   defrauding the applicant. Ellis says so in the payload.

WHAT THIS MODULE IS NOT, AND CAN NEVER BECOME
=============================================
* It performs NO network I/O. It does not query the EU checker, the EES, the
  VIS, or a border system, and it never will: the EES record is the border
  authority's and the traveller's, reachable at the border or through the
  authority, and nothing else here may pretend otherwise.
* It is NOT the border. Ellis counts the stays it was told about. It cannot
  see the EES record, so every payload says whose arithmetic this is and how
  to check it against the official tool.
* It does not file, book, pay, or sign anything.

THE HONEST DIVERGENCE (this is the product, not a bug)
======================================================
The EU's official checker has two documented blind spots:

  * it does not reflect any stay that BEGAN before 10 April 2026, whether that
    stay ended before that date or ran past it; and
  * until 6 October 2026 its "OK" answer can be wrong for a single or double
    entry visa holder who used an entry between 12 October 2025 and 9 April
    2026, when those entries were not yet recorded in EES.

Ellis's engine counts pre-EES stays, because the LAW counts them: the 90/180
rule did not begin on 10 April 2026, only the machine did. That means Ellis and
the official tool can legitimately disagree, and the traveller must know which
one to trust for which question. So whenever a counted day is invisible to the
official tool, the payload SAYS SO, states how many days differ and why, and
still links the official tool. A silent divergence would be worse than no
number at all.

Curated facts carry their source URL and an as_of date (mirroring
``app/h1b/guidance.py``); an input Ellis cannot read becomes an explicit
``unknown`` with the one action that resolves it, never a plausible number.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from . import dates

# Facts below were verified against the official pages on this date. Anything
# older than a review cycle is re-verified before it is shown to a traveller
# planning a border crossing.
AS_OF = "2026-08-11"

UNKNOWN = "unknown"
COMPUTED = "computed"
CURATED = "curated"
NOT_APPLICABLE = "not_applicable"
NOT_YET = "not_yet"

# Schengen Borders Code short stay. Both numbers are statutory, not tunable.
ALLOWANCE_DAYS = 90
WINDOW_DAYS = 180

# EES became fully operational at every external border on this date; the
# progressive rollout had started on 12 October 2025.
EES_START = date(2026, 4, 10)
EES_ROLLOUT_START = date(2025, 10, 12)
# Until this date (included) the official checker's "OK" can be wrong for a
# single/double entry visa holder who used an entry during the rollout.
EES_VISA_CAVEAT_UNTIL = date(2026, 10, 6)

# ETIAS: no start date is confirmed. There is deliberately NO constant here
# that a caller could read as "ETIAS begins on X" — see ``etias_in_force``.
ETIAS_FEE_EUR = 20.0
ETIAS_VALIDITY_YEARS = 3
ETIAS_FEE_EXEMPT_UNDER = 18
ETIAS_FEE_EXEMPT_FROM_AGE = 70

# --------------------------------------------------------------------------
# Sources. Every curated claim below points at one of these.
# --------------------------------------------------------------------------
SBC_SOURCE = "https://eur-lex.europa.eu/eli/reg/2016/399/oj"
SBC_CITE = ("Schengen Borders Code, Regulation (EU) 2016/399, Art. 6 — a short "
            "stay is 90 days in any 180-day period; the day of entry counts as "
            "the first day of stay and the day of exit as the last.")
CALCULATOR_METHOD_SOURCE = ("https://home-affairs.ec.europa.eu/policies/schengen/"
                            "border-crossing/short-stay-calculator_en")
OFFICIAL_CHECKER_URL = ("https://travel-europe.europa.eu/ees/"
                        "check-how-long-you-can-stay_en")
EES_POLICY_SOURCE = ("https://home-affairs.ec.europa.eu/policies/schengen/"
                     "smart-borders/entry-exit-system_en")
EES_OPERATIONAL_SOURCE = ("https://home-affairs.ec.europa.eu/news/entryexit-system-"
                          "will-become-fully-operational-10-april-2026-2026-03-30_en")
EES_REGULATION_SOURCE = "https://eur-lex.europa.eu/eli/reg/2017/2226/oj"
ETIAS_OFFICIAL_URL = "https://travel-europe.europa.eu/etias_en"
ETIAS_TIMELINE_SOURCE = ("https://travel-europe.europa.eu/en/etias/about-etias/"
                         "news-corner/revised-timeline-ees-and-etias")
ETIAS_FEE_SOURCE = ("https://travel-europe.europa.eu/etias/ltr/about-etias/"
                    "news-corner/ETIAS-will-cost-EUR-20")
ETIAS_REGULATION_SOURCE = "https://eur-lex.europa.eu/eli/reg/2018/1240/oj"

# The only domain an ETIAS application will ever be lodged on.
OFFICIAL_DOMAIN = "europa.eu"

# The lines this module holds, published in its payloads so a screen can state
# them in the same words the code enforces.
NEVER_AUTOMATED = (
    "reading the EES record",
    "querying the official remaining-days checker",
    "biometric enrolment",
    "border crossing",
    "filing an ETIAS application",
)


# --------------------------------------------------------------------------
# Country sets
# --------------------------------------------------------------------------

def ees_countries() -> frozenset[str]:
    """The 29 countries operating EES / applying the 90/180 short-stay rule.

    Read from ``consular_forms`` rather than copied: one list in the codebase,
    so a future accession cannot leave two of them disagreeing about whether
    somebody's trip is even in scope.
    """
    from . import consular_forms as cf
    return frozenset(cf._SCHENGEN)


def free_movement_nationalities() -> frozenset[str]:
    """Citizenships outside EES and ETIAS entirely: the EES states plus the two
    EU members that do not operate EES (Ireland, Cyprus) — an EU citizen is
    never an ETIAS applicant."""
    return ees_countries() | {"IRL", "CYP"}


# --------------------------------------------------------------------------
# Date plumbing — every calendar value goes through app/dates.py
# --------------------------------------------------------------------------

def _as_date(value) -> date | None:
    """A calendar date from anything Ellis is willing to read, else None.

    Delegates to ``dates.normalize_any`` so this module inherits the one
    parsing policy in the codebase, including its refusal to guess an
    ambiguous ``05/04/2026``.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    iso = dates.normalize_any(value, kind="expiry")
    if not iso:
        return None
    try:
        return date.fromisoformat(iso)
    except ValueError:                                  # pragma: no cover
        return None


def _iso(d: date | None) -> str:
    return dates.to_iso(d) if d else ""


def _window(on: date) -> tuple[date, date]:
    """The rolling reference period ending on ``on``: that day plus the 179
    days before it, which is the Commission calculator's own look-back."""
    return on - timedelta(days=WINDOW_DAYS - 1), on


def _ordinals(spans, win_start: date, win_end: date) -> set[int]:
    """The distinct calendar days inside the window covered by ``spans``.

    A set, so two overlapping stays cannot charge the traveller twice for the
    same day, and each span is clipped to the window first so a long stay
    cannot blow the count up.
    """
    out: set[int] = set()
    for start, end in spans:
        a = max(start, win_start)
        b = min(end, win_end)
        if a <= b:
            out.update(range(a.toordinal(), b.toordinal() + 1))
    return out


def _span_days(start: date, end: date) -> int:
    """Days of presence for a stay: entry and exit BOTH count (Art. 6)."""
    return (end - start).days + 1


# --------------------------------------------------------------------------
# Trips in, honestly
# --------------------------------------------------------------------------

_ENTRY_KEYS = ("entry", "entry_date", "arrival", "arrival_date", "start",
               "start_date", "from", "intended_arrival", "in")
_EXIT_KEYS = ("exit", "exit_date", "departure", "departure_date", "end",
              "end_date", "to", "intended_departure", "out")
_ONGOING_WORDS = {"present", "ongoing", "still_inside", "still inside",
                  "in_progress", "now", "current"}


def _pick(trip: dict, keys) -> object:
    for k in keys:
        if k in trip and trip[k] not in (None, ""):
            return trip[k]
    return None


def _as_trip_dict(trip) -> dict:
    if isinstance(trip, dict):
        return trip
    if isinstance(trip, (list, tuple)) and len(trip) == 2:
        return {"entry": trip[0], "exit": trip[1]}
    if isinstance(trip, (str, date, datetime)):
        return {"entry": trip, "exit": trip}
    return {}


def normalize_trips(trips, *, close_ongoing_at: date | None = None):
    """Split what the caller has into (counted, unresolved).

    ``counted`` are stays with two readable dates in the right order; each
    carries whether it began before EES existed, which is what makes Ellis's
    total legitimately differ from the EU's tool.

    ``unresolved`` are stays Ellis refuses to guess at — an unreadable date, a
    missing exit, an exit before the entry — each with the one thing that would
    resolve it. They are never silently dropped and never defaulted: a dropped
    stay understates the days used, and understating days used is how somebody
    gets refused entry at a border.

    A stay in progress (``{"entry": ..., "ongoing": True}``) is closed at
    ``close_ongoing_at`` when the caller supplies a meaningful "now"; planning
    functions pass None, because nobody can plan a next trip around a stay with
    no end date.
    """
    if trips is None:
        items = []
    elif isinstance(trips, dict):
        items = [trips]
    elif isinstance(trips, (str, date, datetime)):
        items = [trips]
    else:
        items = list(trips)

    counted: list[dict] = []
    unresolved: list[dict] = []
    for i, raw in enumerate(items):
        trip = _as_trip_dict(raw)
        entry_raw = _pick(trip, _ENTRY_KEYS)
        exit_raw = _pick(trip, _EXIT_KEYS)
        ongoing = bool(trip.get("ongoing")) or (
            isinstance(exit_raw, str)
            and exit_raw.strip().lower() in _ONGOING_WORDS)

        entry = _as_date(entry_raw)
        if entry is None:
            unresolved.append({
                "index": i, "entry": str(entry_raw or ""),
                "exit": "" if ongoing else str(exit_raw or ""),
                "reason": ("Ellis cannot read the entry date for this stay."
                           if entry_raw else
                           "This stay has no entry date."),
                "how_to_resolve": ("Give the date you entered the area as "
                                   "YYYY-MM-DD. Ellis will not guess between "
                                   "day/month and month/day."),
            })
            continue

        if ongoing:
            if close_ongoing_at is None:
                unresolved.append({
                    "index": i, "entry": _iso(entry), "exit": "",
                    "ongoing": True,
                    "reason": ("This stay is recorded as still in progress, so "
                               "its length is not yet a fact."),
                    "how_to_resolve": ("Give the date you left, or the date you "
                                       "plan to leave, and Ellis can count it."),
                })
                continue
            exit_d = max(entry, close_ongoing_at)
        else:
            exit_d = _as_date(exit_raw)
            if exit_d is None:
                unresolved.append({
                    "index": i, "entry": _iso(entry), "exit": str(exit_raw or ""),
                    "reason": ("Ellis cannot read the exit date for this stay."
                               if exit_raw else "This stay has no exit date."),
                    "how_to_resolve": ("Give the date you left as YYYY-MM-DD, or "
                                       "mark the stay as still in progress."),
                })
                continue
            if exit_d < entry:
                unresolved.append({
                    "index": i, "entry": _iso(entry), "exit": _iso(exit_d),
                    "reason": "The exit date is before the entry date.",
                    "how_to_resolve": "Check which way round these two dates go.",
                })
                continue

        counted.append({
            "index": i,
            "entry": _iso(entry),
            "exit": _iso(exit_d),
            "days": _span_days(entry, exit_d),
            "ongoing": bool(ongoing),
            "began_before_ees": entry < EES_START,
            "_entry": entry,
            "_exit": exit_d,
        })
    return counted, unresolved


def _spans(counted) -> list[tuple[date, date]]:
    return [(t["_entry"], t["_exit"]) for t in counted]


def _public(counted) -> list[dict]:
    return [{k: v for k, v in t.items() if not k.startswith("_")} for t in counted]


# --------------------------------------------------------------------------
# The official tool, and where Ellis honestly differs from it
# --------------------------------------------------------------------------

def official_checker(*, invisible_days: int | None = None,
                     on_date: date | None = None) -> dict:
    """The EU's own remaining-days tool: what it is, its two documented blind
    spots, and — when Ellis has counted days the tool cannot see — a plain
    statement that the two answers will differ and by how much.

    Ellis LINKS this tool and never calls it. The traveller opens it, types
    their own dates, and gets the authority's own answer; Ellis's number is a
    second opinion computed from the stays the traveller listed.
    """
    on = on_date or date.today()
    caveat_live = on <= EES_VISA_CAVEAT_UNTIL
    out = {
        "name": "EU official checker: how long you can stay (EES)",
        "url": OFFICIAL_CHECKER_URL,
        "as_of": AS_OF,
        "ellis_reads_it": False,
        "how_to_use": ("Open it yourself and enter your dates. Ellis never "
                       "queries it, and never reads the EES record — that "
                       "record belongs to you and the border authority."),
        "blind_spots": [
            {"key": "pre_ees_stays",
             "detail": ("It does not reflect any stay that began before "
                        f"{_iso(EES_START)}, whether that stay ended before "
                        "that date or continued past it."),
             "active": True,
             "source": OFFICIAL_CHECKER_URL},
            {"key": "single_double_entry_visa",
             "detail": ("Until 6 October 2026 its 'OK' answer can be wrong for "
                        "a single or double entry visa holder who already used "
                        "an entry between 12 October 2025 and 9 April 2026, "
                        "when those entries were not yet recorded in EES."),
             "active": caveat_live,
             "until": _iso(EES_VISA_CAVEAT_UNTIL),
             "source": OFFICIAL_CHECKER_URL},
        ],
    }
    if invisible_days is None:
        return out
    out["days_ellis_counted_that_the_tool_cannot_see"] = invisible_days
    out["differs_from_ellis"] = invisible_days > 0
    if invisible_days > 0:
        out["divergence"] = (
            f"Ellis counted {invisible_days} day(s) from stays that began "
            f"before {_iso(EES_START)}. The official checker leaves those out, "
            "so it will show more days available than Ellis does. The 90/180 "
            "rule itself did not start on that date — only the machine that "
            "records it did — so plan against the lower number.")
    else:
        out["divergence"] = ""
    return out


# --------------------------------------------------------------------------
# The engine: check mode
# --------------------------------------------------------------------------

def _base_report(on: date) -> dict:
    win_start, win_end = _window(on)
    return {
        "as_of": AS_OF,
        "on_date": _iso(on),
        "window_start": _iso(win_start),
        "window_end": _iso(win_end),
        "allowance_days": ALLOWANCE_DAYS,
        "window_days": WINDOW_DAYS,
        "rule": SBC_CITE,
        "sources": [SBC_SOURCE, CALCULATOR_METHOD_SOURCE],
        "computed_by": "ellis",
        "ellis_never": list(NEVER_AUTOMATED),
    }


def days_used(trips, on_date=None, *, today=None) -> dict:
    """Days of presence inside the rolling 180-day window ending on
    ``on_date`` — check mode, the Commission calculator's own question.

    Returns the whole assessment (both ``days_used`` and ``days_remaining``:
    one computation, and callers ask it from both sides).

    ``status`` is ``computed`` only when every stay was readable. One stay Ellis
    could not read makes the total a floor rather than a fact, so the status
    becomes ``unknown`` and the numbers are published as ``at_least_days_used``
    / ``at_most_days_remaining`` — bounds that are true, instead of a total that
    might not be.
    """
    now = _as_date(today) or date.today()
    on = _as_date(on_date) if on_date is not None else now
    if on is None:
        # No window is stated here: a window computed from a date Ellis could
        # not read would be an invented one.
        return {
            "as_of": AS_OF,
            "status": UNKNOWN,
            "on_date": str(on_date or ""),
            "days_used": None,
            "days_remaining": None,
            "allowance_days": ALLOWANCE_DAYS,
            "window_days": WINDOW_DAYS,
            "rule": SBC_CITE,
            "sources": [SBC_SOURCE, CALCULATOR_METHOD_SOURCE],
            "ellis_never": list(NEVER_AUTOMATED),
            "reason": "Ellis cannot read the date to check.",
            "how_to_resolve": "Give the date as YYYY-MM-DD.",
        }

    # A stay still in progress can be counted up to a date that has happened.
    close_at = on if on <= now else None
    counted, unresolved = normalize_trips(trips, close_ongoing_at=close_at)
    win_start, win_end = _window(on)

    spans = _spans(counted)
    used_days = _ordinals(spans, win_start, win_end)
    visible = _ordinals([(t["_entry"], t["_exit"]) for t in counted
                         if not t["began_before_ees"]], win_start, win_end)
    used = len(used_days)
    invisible = len(used_days - visible)

    per_trip = []
    for t in counted:
        in_window = len(_ordinals([(t["_entry"], t["_exit"])], win_start, win_end))
        entry = dict({k: v for k, v in t.items() if not k.startswith("_")})
        entry["days_in_window"] = in_window
        per_trip.append(entry)
    overlap = sum(t["days_in_window"] for t in per_trip) - used

    report = _base_report(on)
    report.update({
        "trips_counted": per_trip,
        "trips_unresolved": unresolved,
        "overlapping_days_counted_once": max(overlap, 0),
        "days_before_ees_counted": invisible,
        "official_tool": official_checker(invisible_days=invisible, on_date=on),
        "basis": (f"Computed from the {len(per_trip)} stay(s) you gave Ellis. "
                  "Ellis cannot see the EES record, so this is arithmetic on "
                  "your own dates, not the border's answer."),
    })
    if not per_trip and not unresolved:
        report["no_stays_recorded"] = True
        report["basis"] = ("You have given Ellis no Schengen stays, so this "
                           "assumes there are none in the window. If you have "
                           "travelled, add those dates before you rely on it.")

    if unresolved:
        report.update({
            "status": UNKNOWN,
            "days_used": None,
            "days_remaining": None,
            "at_least_days_used": used,
            "at_most_days_remaining": max(ALLOWANCE_DAYS - used, 0),
            "overstay": True if used > ALLOWANCE_DAYS else None,
            "reason": (f"{len(unresolved)} stay(s) could not be read, so the "
                       "total would be a guess. What Ellis can say is that you "
                       f"have used AT LEAST {used} of your {ALLOWANCE_DAYS} "
                       "days."),
            "how_to_resolve": [u["how_to_resolve"] for u in unresolved],
        })
        return report

    report.update({
        "status": COMPUTED,
        "days_used": used,
        "days_remaining": max(ALLOWANCE_DAYS - used, 0),
        "overstay": used > ALLOWANCE_DAYS,
        "days_over": max(used - ALLOWANCE_DAYS, 0),
    })
    if report["overstay"]:
        report["overstay_note"] = (
            "Since 10 April 2026 an overstay is detected automatically at the "
            "border from the EES record. Do not travel on these dates without "
            "advice; entry can be refused and a ban can follow.")
    return report


def days_remaining(trips, on_date=None, *, today=None) -> dict:
    """Days still available on ``on_date``. The same assessment ``days_used``
    returns, named for the question most travellers actually ask."""
    return days_used(trips, on_date, today=today)


# --------------------------------------------------------------------------
# The engine: planning mode
# --------------------------------------------------------------------------

def _first_violation(spans, start: date, limit_days: int):
    """Walk a continuous stay from ``start`` one day at a time, checking the
    rolling window on EVERY day of it.

    Day by day rather than once at the exit, for two reasons. It names the
    exact date the limit is crossed, which is what the traveller has to act on
    ("you go over on the 12th", not "this trip is too long"). And it makes the
    result independent of any argument about which day of a stay is the worst:
    the window slides, old stays drop out of it while the new one runs, and the
    engine simply checks all of them rather than reasoning about it.
    """
    worst = 0
    for k in range(1, limit_days + 1):
        day = start + timedelta(days=k - 1)
        win = _window(day)
        used = len(_ordinals(list(spans) + [(start, day)], *win))
        worst = max(worst, used)
        if used > ALLOWANCE_DAYS:
            return k, day, used, worst
    return None, None, None, worst


def max_stay_from(start_date, trips=(), *, today=None) -> dict:
    """The longest unbroken stay that could begin on ``start_date`` without
    breaking the rule — the Commission calculator's planning mode.

    Returns ``max_days`` (0 when the traveller cannot enter that day at all,
    with the earliest date they could) and the last day they may still be
    inside. Because the exit day counts as a day of presence, that last day IS
    the last day they may leave.

    ``today`` is accepted for symmetry with the check-mode functions and is not
    used: planning needs no "now", because a stay with no end date is refused
    here rather than closed at an assumed one.
    """
    start = _as_date(start_date)
    if start is None:
        return {"as_of": AS_OF, "status": UNKNOWN, "max_days": None,
                "start_date": str(start_date or ""),
                "reason": "Ellis cannot read the date you want to arrive.",
                "how_to_resolve": "Give the arrival date as YYYY-MM-DD."}

    counted, unresolved = normalize_trips(trips, close_ongoing_at=None)
    spans = _spans(counted)
    out = {
        "as_of": AS_OF,
        "start_date": _iso(start),
        "allowance_days": ALLOWANCE_DAYS,
        "window_days": WINDOW_DAYS,
        "rule": SBC_CITE,
        "sources": [SBC_SOURCE, CALCULATOR_METHOD_SOURCE],
        "trips_counted": _public(counted),
        "trips_unresolved": unresolved,
        "official_tool": official_checker(on_date=start),
        "ellis_never": list(NEVER_AUTOMATED),
    }
    if unresolved:
        out.update({
            "status": UNKNOWN, "max_days": None,
            "reason": ("Ellis cannot plan around a stay it cannot read: an "
                       "unknown stay could be the one that uses up the days."),
            "how_to_resolve": [u["how_to_resolve"] for u in unresolved],
        })
        return out

    violated_on, _day, _used, _worst = _first_violation(spans, start,
                                                        ALLOWANCE_DAYS)
    max_days = ALLOWANCE_DAYS if violated_on is None else violated_on - 1
    out.update({
        "status": COMPUTED,
        "max_days": max_days,
        "last_day_inside": _iso(start + timedelta(days=max_days - 1)) if max_days else "",
        "must_leave_on_or_before": _iso(start + timedelta(days=max_days - 1)) if max_days else "",
        "days_before_ees_counted": len(
            _ordinals([(t["_entry"], t["_exit"]) for t in counted
                       if t["began_before_ees"]], *_window(start))),
    })
    if max_days == 0:
        earliest = _earliest_entry(spans, start)
        out["reason"] = ("On that date you have no days left in the rolling "
                         "180-day window, so you cannot enter that day.")
        out["earliest_entry_date"] = _iso(earliest) if earliest else ""
        if not earliest:
            out["earliest_entry_note"] = (
                "Ellis looked 180 days ahead and found no day with an "
                "allowance; check the official tool and take advice.")
    return out


def _earliest_entry(spans, start: date) -> date | None:
    """The first day from ``start`` on which at least one day of stay is
    available. Bounded by the window: past 180 days every earlier stay has
    dropped out, so an answer always exists inside that horizon."""
    for offset in range(0, WINDOW_DAYS + 1):
        day = start + timedelta(days=offset)
        if len(_ordinals(list(spans) + [(day, day)], *_window(day))) <= ALLOWANCE_DAYS:
            return day
    return None


def plan_trip(trips, entry_date=None, exit_date=None, *, trip=None,
              today=None) -> dict:
    """Planning mode: "can I take this trip?"

    Answers with a verdict and the arithmetic behind it — how many days the
    trip costs, the worst day in the window during it, and, when the answer is
    no, the date the traveller would have to leave instead. It refuses rather
    than rounds: a trip that overstays by one day is refused by one day,
    because the border now counts by machine.

    ``today`` is accepted for symmetry with the check-mode functions and is not
    used, for the same reason as in ``max_stay_from``.
    """
    if trip is not None:
        d = _as_trip_dict(trip)
        entry_date = entry_date if entry_date is not None else _pick(d, _ENTRY_KEYS)
        exit_date = exit_date if exit_date is not None else _pick(d, _EXIT_KEYS)

    entry = _as_date(entry_date)
    exit_d = _as_date(exit_date)
    out = {
        "as_of": AS_OF,
        "mode": "planning",
        "entry_date": _iso(entry) or str(entry_date or ""),
        "exit_date": _iso(exit_d) or str(exit_date or ""),
        "allowance_days": ALLOWANCE_DAYS,
        "window_days": WINDOW_DAYS,
        "rule": SBC_CITE,
        "sources": [SBC_SOURCE, CALCULATOR_METHOD_SOURCE],
        "ellis_never": list(NEVER_AUTOMATED),
    }
    if entry is None or exit_d is None:
        out.update({
            "status": UNKNOWN, "allowed": None, "verdict": UNKNOWN,
            "reason": ("Ellis cannot read the "
                       + ("arrival" if entry is None else "departure")
                       + " date for the trip you are planning."),
            "how_to_resolve": "Give both dates as YYYY-MM-DD.",
        })
        return out
    if exit_d < entry:
        out.update({
            "status": UNKNOWN, "allowed": None, "verdict": UNKNOWN,
            "reason": "The departure date is before the arrival date.",
            "how_to_resolve": "Check which way round these two dates go.",
        })
        return out

    counted, unresolved = normalize_trips(trips, close_ongoing_at=None)
    spans = _spans(counted)
    requested = _span_days(entry, exit_d)
    out.update({
        "requested_days": requested,
        "trips_counted": _public(counted),
        "trips_unresolved": unresolved,
        "official_tool": official_checker(on_date=exit_d),
    })
    if unresolved:
        out.update({
            "status": UNKNOWN, "allowed": None, "verdict": UNKNOWN,
            "reason": ("Ellis cannot say whether this trip fits while a stay it "
                       "cannot read might be using up the same window."),
            "how_to_resolve": [u["how_to_resolve"] for u in unresolved],
        })
        return out

    violated_on, violated_day, used_then, worst = _first_violation(
        spans, entry, requested)
    allowed = violated_on is None
    ceiling = max_stay_from(entry, trips)
    max_days = ceiling.get("max_days")

    out.update({
        "status": COMPUTED,
        "allowed": allowed,
        "verdict": "fits" if allowed else "would_overstay",
        "max_days_available": max_days,
        "last_day_inside_if_you_go": ceiling.get("last_day_inside", ""),
        "days_used_on_exit_day": len(
            _ordinals(spans + [(entry, exit_d)], *_window(exit_d))),
        "peak_days_used_during_trip": worst,
        "days_before_ees_counted": len(
            _ordinals([(t["_entry"], t["_exit"]) for t in counted
                       if t["began_before_ees"]], *_window(exit_d))),
    })
    if allowed:
        out["days_remaining_after"] = max(
            ALLOWANCE_DAYS - out["days_used_on_exit_day"], 0)
        out["reason"] = (f"{requested} day(s) fits: the busiest day of this "
                         f"trip uses {worst} of your {ALLOWANCE_DAYS} days.")
        return out

    over = used_then - ALLOWANCE_DAYS
    out.update({
        "days_over": over,
        "first_day_over": _iso(violated_day),
        "reason": (f"This trip would put you over the limit on "
                   f"{dates.to_display(_iso(violated_day))} — day {violated_on} "
                   f"of the trip — by {over} day(s). The rolling window allows "
                   f"{max_days} day(s) from this arrival date."),
        "suggested_exit_date": ceiling.get("last_day_inside", ""),
        "overstay_note": ("An overstay is now detected automatically from the "
                          "EES record at the border, so this is not a risk "
                          "worth taking on the day."),
    })
    if not max_days:
        out["earliest_entry_date"] = ceiling.get("earliest_entry_date", "")
        out["suggested_exit_date"] = ""
    return out


can_take_trip = plan_trip


# --------------------------------------------------------------------------
# EES guidance (curated)
# --------------------------------------------------------------------------

def _route_iso3(route=None, case=None) -> str:
    """The destination ISO3 a route names, or '' when it names none."""
    value = route
    if isinstance(route, dict):
        value = ""
        for field in ("destination", "destination_country", "member_state",
                      "destination_iso3", "country", "route_key"):
            if route.get(field):
                value = route[field]
                break
    if not value and case is not None:
        value = getattr(case, "destination_country", "") or ""
    text = str(value or "").strip()
    if not text:
        return ""
    if text.lower() in ("schengen", "schengen_uniform", "ees", "eu", "eea"):
        return "SCHENGEN"
    from .visa_snapshot import registry
    return registry.iso3(text, default="").upper()


def ees_guidance(route=None, case=None) -> dict:
    """What EES means for this journey: curated, sourced, and honest about the
    fact that Ellis is not the border.

    A route that is not a Schengen journey gets an explicit not-applicable and
    a route Ellis cannot place gets an explicit unknown — telling a traveller
    to expect fingerprinting at a border that does not collect it would be its
    own kind of harm.
    """
    iso3 = _route_iso3(route, case)
    members = ees_countries()
    if iso3 and iso3 != "SCHENGEN" and iso3 not in members:
        return {
            "status": NOT_APPLICABLE, "as_of": AS_OF, "applies": False,
            "destination": iso3,
            "reason": ("This journey is not to a country operating the EU "
                       "Entry/Exit System, so EES and the 90/180 short-stay "
                       "rule do not govern it."),
            "source": EES_POLICY_SOURCE,
        }
    if not iso3:
        return {
            "status": UNKNOWN, "as_of": AS_OF, "applies": None,
            "reason": ("Ellis cannot tell which country this journey is to, so "
                       "it will not say whether EES applies."),
            "how_to_resolve": "Set the destination country on the case.",
            "source": EES_POLICY_SOURCE,
        }

    return {
        "status": CURATED,
        "as_of": AS_OF,
        "applies": True,
        "destination": "" if iso3 == "SCHENGEN" else iso3,
        "operational_since": _iso(EES_START),
        "rollout_started": _iso(EES_ROLLOUT_START),
        "country_count": len(members),
        "countries": sorted(members),
        "headline": ("Passport stamps are gone. Your entries and exits are now "
                     "digital records tied to your face and fingerprints, and "
                     "an overstay is detected automatically."),
        "what_changes": [
            {"key": "first_entry_biometrics",
             "title": "Your first entry takes longer",
             "detail": ("At your first crossing after EES went live you are "
                        "enrolled: your facial image and fingerprints are taken "
                        "and linked to your passport, and you are asked about "
                        "the purpose and length of your stay. Later crossings "
                        "reuse that record and are quicker. Fingerprints are "
                        "not taken from children under 12."),
             "source": EES_POLICY_SOURCE},
            {"key": "no_more_stamps",
             "title": "No stamp in your passport",
             "detail": ("A stamp is no longer the evidence of when you entered "
                        "or left. Keep boarding passes and bookings: if the "
                        "record is ever wrong, those are what you correct it "
                        "with."),
             "source": EES_OPERATIONAL_SOURCE},
            {"key": "automatic_overstay",
             "title": "Overstay is now automatic, not discretionary",
             "detail": ("The system computes your days for you and flags an "
                        "overstay at the border. Miscounting used to be "
                        "survivable; now it is detected on the spot, and the "
                        "consequences fall on you — refused entry, and "
                        "potentially a ban."),
             "source": EES_OPERATIONAL_SOURCE},
            {"key": "who_is_in_scope",
             "title": "Who is enrolled",
             "detail": ("Non-EU nationals crossing an external border for a "
                        "short stay, whether visa-free or on a short-stay visa. "
                        "EU/EEA/Swiss citizens, and holders of a residence "
                        "permit or a long-stay visa of one of these countries, "
                        "are outside it."),
             "source": EES_REGULATION_SOURCE},
        ],
        "rule": SBC_CITE,
        "official_tool": official_checker(),
        "ellis_role": ("Ellis counts your days from the dates you give it, "
                       "explains what the border will do, and links you to the "
                       "EU's own checker. It does not read the EES record and "
                       "cannot cross a border for you."),
        "ellis_never": list(NEVER_AUTOMATED),
        "sources": [EES_POLICY_SOURCE, EES_OPERATIONAL_SOURCE,
                    EES_REGULATION_SOURCE, OFFICIAL_CHECKER_URL],
    }


# --------------------------------------------------------------------------
# ETIAS readiness — a readiness check, never a filing flow
# --------------------------------------------------------------------------

SCAM_WARNING = (
    "ETIAS is not open. No website can take an ETIAS application today, and "
    "any site charging you for one is taking your money and your passport "
    "details for nothing. When it does open, applications will be lodged only "
    "through the EU's official site on europa.eu. Ellis will not accept an "
    "ETIAS application before then, and neither should anyone else.")


def etias_in_force(today=None) -> bool:
    """Whether ETIAS is in force. False, and not from a date comparison: no
    start date has been set, so there is no date to compare with. It flips only
    when a human reviews the announcement and changes this module."""
    return False


def _answers_of(case=None, answers=None) -> dict:
    if answers is not None:
        return dict(answers)
    return dict(getattr(case, "answers", None) or {})


def _passport_validity(answers: dict, *, today: date) -> dict:
    """Schengen entry rule on the travel document: valid at least three months
    after the intended departure, and issued within the previous ten years.

    Deliberately asymmetric about the unknown. A passport that already fails
    against today fails for any later departure, so ``False`` is safe. A
    passport that passes against today might still fail for a departure Ellis
    does not know about, so that case is ``unknown`` — not a reassuring True.
    """
    expiry = _as_date(answers.get("passport_expiry_date")
                      or answers.get("passport_expiry")
                      or answers.get("date_of_expiry"))
    issued = _as_date(answers.get("passport_issue_date")
                      or answers.get("date_of_issue"))
    departure = _as_date(answers.get("departure_date")
                         or answers.get("intended_departure")
                         or answers.get("return_date"))
    basis = ("The travel document must be valid for at least three months "
             "after you leave the area and must have been issued within the "
             "previous ten years.")
    out = {"basis": basis, "source": SBC_SOURCE, "cite": SBC_CITE,
           "passport_expiry_date": _iso(expiry),
           "passport_issue_date": _iso(issued),
           "checked_against": _iso(departure) if departure else _iso(today)}
    if expiry is None:
        out.update({"ok": UNKNOWN,
                    "reason": "Ellis does not have your passport expiry date.",
                    "how_to_resolve": ("Upload your passport biodata page, or "
                                       "give the expiry date.")})
        return out

    reference = departure or today
    needs_valid_until = _add_months(reference, 3)
    ok_expiry = expiry >= needs_valid_until
    ok_issue = None if issued is None else issued >= _add_years(reference, -10)

    if not ok_expiry:
        out.update({
            "ok": False,
            "reason": (f"Your passport expires {dates.to_display(_iso(expiry))}, "
                       "which is less than three months after "
                       + ("your departure date." if departure
                          else "today — so it cannot cover any trip from now.")),
            "how_to_resolve": "Renew the passport before you travel.",
        })
        return out
    if ok_issue is False:
        out.update({
            "ok": False,
            "reason": (f"Your passport was issued "
                       f"{dates.to_display(_iso(issued))}, more than ten years "
                       "before this trip."),
            "how_to_resolve": "Renew the passport before you travel.",
        })
        return out
    if departure is None:
        out.update({
            "ok": UNKNOWN,
            "reason": ("The rule is measured from the day you leave the area, "
                       "and Ellis does not have that date. Your passport is "
                       "valid more than three months from today, which is not "
                       "the same answer."),
            "how_to_resolve": "Give the date you plan to leave the area.",
        })
        return out
    out.update({"ok": True,
                "reason": ("Your passport covers the trip: valid more than "
                           "three months past your departure"
                           + ("" if ok_issue is None
                              else ", and issued within the last ten years")
                           + ".")})
    if ok_issue is None:
        out["note"] = ("Ellis does not have the issue date, so the ten-year "
                       "half of the rule is unchecked.")
    return out


# Calendar shifts for the passport rule. Local because the two ends of the
# rule are asymmetric — three months forward from the departure, ten years back
# from it — and both must land on a real date in every month and leap year.

def _add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, [31, 29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0))
                      else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


def _add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, day=28)


def etias_readiness(case=None, *, answers=None, route=None, today=None,
                    visa_exempt=None) -> dict:
    """Is this traveller ready for ETIAS, and what is true about it today.

    Readiness ONLY. There is no application to make: ETIAS is not in force,
    the portal does not exist, and the answer to "required?" is therefore
    ``not_yet`` for anyone in scope — never True, and never a filing flow.
    Regulation (EU) 2018/1240 Art. 15 does let a commercial intermediary
    authorised by the applicant lodge the application through the official
    site, so Ellis will be able to do that leg for the traveller once the
    system opens; until then that capability is stated as future, not offered.
    """
    now = _as_date(today) or date.today()
    data = _answers_of(case, answers)
    iso3 = _route_iso3(route, case)
    nationality = str(data.get("nationality") or data.get("citizenship") or "")
    from .visa_snapshot import registry
    nat3 = registry.iso3(nationality, default="").upper() if nationality else ""

    age = dates.derive_age(dates.normalize_any(
        data.get("birth_date") or data.get("date_of_birth"), kind="birth",
        today=now), today=now)
    fee_exempt = (UNKNOWN if age is None
                  else (age < ETIAS_FEE_EXEMPT_UNDER
                        or age >= ETIAS_FEE_EXEMPT_FROM_AGE))
    passport = _passport_validity(data, today=now)

    out = {
        "status": CURATED,
        "as_of": AS_OF,
        "required": UNKNOWN,
        "reason": "",
        "in_force": etias_in_force(now),
        "portal_status": "not_launched",
        "filing_available": False,
        "fee_eur": ETIAS_FEE_EUR,
        "fee_currency": "EUR",
        "standard_fee_eur": ETIAS_FEE_EUR,
        "exempt_by_age": fee_exempt,
        "exempt_by_age_note": (
            "Under 18 or 70 and over waives the FEE only — the application "
            "itself is still required (Regulation (EU) 2018/1240, Art. 18). "
            "Age is assessed on the application date, and that date does not "
            "exist yet, so this is checked against today."),
        "age_checked": age,
        "validity_years": ETIAS_VALIDITY_YEARS,
        "validity_note": ("Three years, or until the passport it is linked to "
                          "expires, whichever comes first."),
        "passport_validity_ok": passport["ok"],
        "passport_validity": passport,
        "expected_start": {
            "status": UNKNOWN,
            "detail": ("No start date is set. The launch has slipped to 2027 at "
                       "the earliest and the EU says it will announce the date "
                       "several months in advance."),
            "source": ETIAS_TIMELINE_SOURCE,
        },
        "scam_warning": SCAM_WARNING,
        "official_site": ETIAS_OFFICIAL_URL,
        "official_domain": OFFICIAL_DOMAIN,
        "how_to_apply_today": ("You cannot, and nobody can. There is nothing to "
                               "do and nothing to pay."),
        "ellis_can_file": {
            "today": False,
            "when_it_opens": True,
            "basis": ("Regulation (EU) 2018/1240, Art. 15: an application may "
                      "be lodged by the applicant or by a person or commercial "
                      "intermediary authorised by the applicant, through the "
                      "official site."),
            "note": ("Ellis will need your written authorisation at that point. "
                     "It has no ETIAS filing path today because no such path "
                     "exists."),
            "source": ETIAS_REGULATION_SOURCE,
        },
        "source": ETIAS_OFFICIAL_URL,
        "sources": [ETIAS_OFFICIAL_URL, ETIAS_TIMELINE_SOURCE, ETIAS_FEE_SOURCE,
                    ETIAS_REGULATION_SOURCE],
        "ellis_never": list(NEVER_AUTOMATED),
    }
    if fee_exempt is True:
        out["fee_eur"] = 0.0
        out["fee_basis"] = ("Waived: you are under 18 or 70 or over on the date "
                            "checked. The application is still required.")
    elif fee_exempt is False:
        out["fee_basis"] = "EUR 20 per application, from the fee's official page."
    else:
        out["fee_basis"] = ("EUR 20 is the standard fee; Ellis does not have a "
                            "date of birth, so it cannot say whether the age "
                            "waiver applies to you.")
        out["fee_how_to_resolve"] = "Give your date of birth."

    # Destination: is this even an ETIAS journey?
    members = ees_countries()
    if iso3 and iso3 != "SCHENGEN" and iso3 not in members:
        out.update({
            "required": False,
            "applies_when_launched": False,
            "reason": ("This journey is not to a country in the ETIAS area, so "
                       "ETIAS will not apply to it."),
        })
        return out
    if not iso3:
        out.update({
            "required": UNKNOWN,
            "applies_when_launched": UNKNOWN,
            "reason": ("Ellis cannot tell which country this journey is to, so "
                       "it will not say whether ETIAS will apply."),
            "how_to_resolve": "Set the destination country on the case.",
        })
        return out

    # Citizenship: an EU/EEA/Swiss national is never an ETIAS applicant.
    if nat3 and nat3 in free_movement_nationalities():
        out.update({
            "required": False,
            "applies_when_launched": False,
            "reason": ("You hold the nationality of an EU/EEA or Schengen "
                       "state, so ETIAS and EES do not apply to you at all."),
        })
        return out

    # Everyone else in scope: not yet, because it does not exist yet.
    if visa_exempt is True:
        applies = True
        who = ("You travel visa-free, so ETIAS is the authorisation you will "
               "need once it opens. ")
    elif visa_exempt is False:
        applies = False
        who = ("You need a short-stay visa for this trip, and ETIAS is only for "
               "visa-free travellers, so it will not apply to you. ")
    else:
        applies = UNKNOWN
        who = ("Whether ETIAS will apply to you depends on whether your "
               "nationality travels visa-free to the area, which Ellis has not "
               "confirmed for this case. ")
        out["how_to_resolve"] = ("Confirm the visa requirement for your "
                                 "nationality first; ETIAS is only for "
                                 "visa-free travellers.")
    out["applies_when_launched"] = applies
    out["required"] = False if applies is False else NOT_YET
    out["reason"] = who + (
        "Nothing is required for this trip: ETIAS is not in force, there is no "
        "portal, and no authorisation exists to hold." if applies is not False
        else "Nothing about ETIAS is required of you.")
    if nat3:
        out["nationality"] = nat3
    return out


# --------------------------------------------------------------------------
# One call for a screen
# --------------------------------------------------------------------------

def stay_report(trips, *, on_date=None, planned=None, route=None, case=None,
                today=None) -> dict:
    """Where the traveller stands, what a planned trip would do, and the EES
    facts around both — assembled, not decided, so a caller cannot end up
    showing the count without the caveats that go with it."""
    out = {
        "as_of": AS_OF,
        "stay": days_used(trips, on_date, today=today),
        "ees": ees_guidance(route, case=case),
        "plan": None,
    }
    if planned is not None:
        out["plan"] = plan_trip(trips, trip=planned, today=today)
    return out


__all__ = [
    "ALLOWANCE_DAYS", "WINDOW_DAYS", "EES_START", "AS_OF",
    "can_take_trip", "days_remaining", "days_used", "ees_countries",
    "ees_guidance", "etias_in_force", "etias_readiness",
    "free_movement_nationalities", "max_stay_from", "normalize_trips",
    "official_checker", "plan_trip", "stay_report",
]
