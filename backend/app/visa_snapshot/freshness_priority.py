"""Order an already-due source-check set using the agreed market priorities.

These are user/Trip.com priorities, not measured traffic. This module neither
selects eligibility nor changes a route, verification stamp, retry, or budget.
Every eighth dispatch position reserves the oldest remaining eligible row.
"""
from datetime import datetime, timezone

POLICY_ID = "trip18-plus-chn-fair8-v1"
STATIONS = frozenset({"HKG", "TWN", "JPN", "KOR", "USA", "THA", "SGP", "MYS",
                      "GBR", "RUS", "AUS", "IDN", "PHL", "FRA", "VNM", "ESP",
                      "IND", "CAN"})
MAJOR_DESTINATIONS = STATIONS | {"CHN"}
CRITICAL_KEYS = frozenset(f"{origin}|{origin}|{dest}|tourism|default|unknown|v6"
                          for origin, dest in (("IDN", "KOR"), ("MYS", "RUS"),
                                               ("THA", "AUS"), ("HKG", "VNM")))
OLDEST_EVERY = 8
_OLDEST = datetime.min.replace(tzinfo=timezone.utc)


def priority_tier(cache_key):
    """Critical tier is only the exact ordinary-tourism canonical policy row."""
    key = str(cache_key or "")
    if key in CRITICAL_KEYS:
        return 0
    parts = key.split("|")
    if (len(parts) < 7 or parts[0] != parts[1] or parts[4:7] != ["default", "unknown", "v6"]
            or any(p.startswith("via:") for p in parts[7:])):
        return 5
    origin, destination = parts[0], parts[2]
    if origin in {"USA", "HKG"} and destination in MAJOR_DESTINATIONS:
        return 1
    if origin in STATIONS and destination in MAJOR_DESTINATIONS:
        return 2
    if origin in {"USA", "HKG"}:
        return 3
    return 4 if origin in STATIONS else 5


def _attempt_age(row, now):
    verification = getattr(row, "verification", None)
    check = verification.get("grounded_check") if isinstance(verification, dict) else None
    value = check.get("at") if isinstance(check, dict) else None
    try:
        at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        at = at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at
        return at if at <= now else _OLDEST
    except (TypeError, ValueError, OverflowError):
        return _OLDEST


def prioritize_due_rows(rows, *, now=None, dispatch_offset=0):
    """Return the same rows, fairly ordered, without touching caller state.

    The caller must first apply due and continuation filters, and then apply
    its existing row cap to this result. The offset preserves the one-in-eight
    reservation across normal interrupted-cycle continuation. Unknown legacy
    checkpoint counts start this ordering at position one without buying time.
    """
    if isinstance(dispatch_offset, bool) or not isinstance(dispatch_offset, int) or dispatch_offset < 0:
        raise ValueError("dispatch_offset must be a nonnegative integer")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    indexed = [(i, row, _attempt_age(row, now), str(getattr(row, "cache_key", "") or ""))
               for i, row in enumerate(rows)]
    oldest = sorted(indexed, key=lambda item: (item[2], item[3], item[0]))
    priority = sorted(indexed, key=lambda item: (priority_tier(item[3]), item[2], item[3], item[0]))
    selected, result = set(), []
    oldest_cursor = priority_cursor = 0
    while len(result) < len(indexed):
        fair = (dispatch_offset + len(result) + 1) % OLDEST_EVERY == 0
        order = oldest if fair else priority
        cursor = oldest_cursor if fair else priority_cursor
        while order[cursor][0] in selected:
            cursor += 1
        index, row, _, _ = order[cursor]
        if fair:
            oldest_cursor = cursor + 1
        else:
            priority_cursor = cursor + 1
        selected.add(index)
        result.append(row)
    return result
