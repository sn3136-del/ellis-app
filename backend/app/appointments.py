"""Appointment preferences, validation, and timezone-aware earliest-qualifying
ranking — Python port of the tested JS engine."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

DAY_MS = 86_400_000


def default_preferences(**overrides) -> dict:
    base = {
        "preferredLocation": "",
        "alternativeLocations": [],
        "maxTravelKm": None,
        "earliestUtc": _now_ms(),
        "latestUtc": _now_ms() + 120 * DAY_MS,
        "preferredWeekdays": [],
        "excludedWeekdays": [],
        "preferredTimeRange": None,   # ["09:00","12:00"] in applicant tz
        "blackoutDates": [],           # ["YYYY-MM-DD"] applicant tz
        "minAdvanceMs": 2 * DAY_MS,
        "anyQualifyingTimeOk": True,
        "allowAutoBook": False,
        "allowAutoReschedule": False,
        "minRescheduleImprovementMs": 3 * DAY_MS,
        "maxRescheduleFeeCents": 0,
        "maxAutoReschedules": 2,
        "applicantTimeZone": "UTC",
        "askBeforeReschedule": True,
    }
    base.update(overrides)
    return base


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def validate_preferences(p: dict) -> list[str]:
    problems = []
    if p["earliestUtc"] >= p["latestUtc"]:
        problems.append("earliest date is not before latest date")
    if p["minAdvanceMs"] < 0:
        problems.append("minimum advance notice cannot be negative")
    pref = set(p.get("preferredWeekdays") or [])
    for d in p.get("excludedWeekdays") or []:
        if d in pref:
            problems.append(f"weekday {d} is both preferred and excluded")
    if p.get("preferredWeekdays") and p.get("excludedWeekdays") and all(
            d in p["excludedWeekdays"] for d in p["preferredWeekdays"]):
        problems.append("all preferred weekdays are excluded")
    if p.get("allowAutoReschedule") and p.get("maxAutoReschedules", 0) <= 0:
        problems.append("auto-reschedule enabled but max count is 0")
    tr = p.get("preferredTimeRange")
    if tr:
        a, b = tr
        if a >= b:
            problems.append("invalid preferred time range")
    return problems


def _local_parts(utc_ms: int, tz: str):
    dt = datetime.fromtimestamp(utc_ms / 1000, ZoneInfo(tz))
    return {"weekday": (dt.weekday() + 1) % 7,  # Python Mon=0 → JS Sun=0
            "date": dt.strftime("%Y-%m-%d"),
            "time": dt.strftime("%H:%M")}


def qualifying_slots(slots: list[dict], prefs: dict, now_ms: int | None = None) -> list[dict]:
    now_ms = _now_ms() if now_ms is None else now_ms
    loc_ok = {x for x in [prefs.get("preferredLocation")] + list(prefs.get("alternativeLocations") or []) if x}
    excluded = set(prefs.get("excludedWeekdays") or [])
    preferred = set(prefs.get("preferredWeekdays") or [])
    blackout = set(prefs.get("blackoutDates") or [])
    out = []
    for s in slots:
        if loc_ok and s["locationId"] not in loc_ok:
            continue
        if s["startUtc"] < prefs["earliestUtc"] or s["startUtc"] > prefs["latestUtc"]:
            continue
        if s["startUtc"] - now_ms < prefs["minAdvanceMs"]:
            continue
        lp = _local_parts(s["startUtc"], prefs["applicantTimeZone"])
        if lp["date"] in blackout:
            continue
        if lp["weekday"] in excluded:
            continue
        if preferred and lp["weekday"] not in preferred:
            continue
        tr = prefs.get("preferredTimeRange")
        if tr and (lp["time"] < tr[0] or lp["time"] >= tr[1]):
            continue
        out.append({**s, "local": lp})
    out.sort(key=lambda x: x["startUtc"])
    return out


def earliest_qualifying(slots, prefs, now_ms=None):
    q = qualifying_slots(slots, prefs, now_ms)
    return q[0] if q else None


def is_improvement(current_start_utc: int, candidate_start_utc: int, prefs: dict) -> bool:
    return current_start_utc - candidate_start_utc >= prefs.get("minRescheduleImprovementMs", 0)
