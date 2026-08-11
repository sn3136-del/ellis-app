"""Appointment availability and monitoring — LEGITIMATE SOURCES ONLY.

THE PROHIBITION (read this before adding anything to this module)
================================================================
This module must NEVER search for, poll for, hold, or book an appointment
slot, and must NEVER read a booking system's calendar by machine.

Why, concretely: the consular booking systems treat automated slot search as
abuse, and the penalty does not fall on the software vendor — it falls on the
TRAVELER. Accounts touched by bots have had their appointments cancelled and
their visas revoked (documented at scale in India in 2025, roughly two thousand
appointments cancelled). A scraper here would silently destroy a real person's
trip. The US scheduling site for China, usvisascheduling.com (CGI Federal's
Atlas360), is additionally behind Cloudflare bot protection; defeating that is
forbidden for the same reason and would also be a terms violation.

So: Ellis prepares everything around the appointment, links the human to the
official site, and reminds them when to act. An authorized human searches and
books. There is no code path here that does otherwise, and
``assert_never_fetched`` exists to make an accidental one fail loudly.

WHAT IS LEGITIMATE, AND WHAT THIS MODULE THEREFORE DOES
-------------------------------------------------------
1. **Published wait-time data.** The Department of State publishes interview
   wait times per post. Research finding, 2026-08-11: there is NO documented
   JSON/CSV/RSS feed for it. The Global Visa Wait Times page is a
   human-readable interactive tool, the Quarterly Report on Visa Wait Times is
   a narrative page, and travel.state.gov refuses non-browser fetches (HTTP
   403). Rather than pretend a feed exists — or scrape a site that is telling
   us not to — this module reads a snapshot a HUMAN placed on disk at
   ``data/reference/visa_wait_times/<YYYY-MM-DD>.json`` (override the directory
   with ``$ELLIS_VISA_WAIT_TIMES_DIR``). With no snapshot, availability is
   reported as explicitly unavailable. It is never guessed.

2. **Deep links to the official sites**, so the human checks and books at the
   source, with attribution. Links are built only for hosts on
   ``OFFICIAL_LINK_HOSTS``; a scheduling host may be LINKED (a human goes
   there) but is on ``NEVER_FETCH_HOSTS`` and is never requested by Ellis.

3. **Monitoring as a reminder, not a poll.** A monitoring record is either a
   REMINDER for a human to go and look, or a record of what a human saw in
   their OWN attended session. Every record carries ``booked: False`` and
   ``slot_held: False`` — this module cannot book or hold anything, and its
   payloads never imply it did.

This module performs no network I/O of any kind. That is enforced by test
(``tests/test_appt_availability.py``) as well as by review.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import uuid
from pathlib import Path
from urllib.parse import urlsplit  # parsing only — never a request

# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------
SOURCE = ("U.S. Department of State published visa appointment wait times "
          "(travel.state.gov)")
ATTRIBUTION = ("Source: U.S. Department of State, travel.state.gov. Ellis links "
               "to the official site; the human checks and books there.")

GLOBAL_WAIT_TIMES_URL = ("https://travel.state.gov/content/travel/en/us-visas/"
                         "visa-information-resources/global-visa-wait-times.html")
WAIT_TIMES_TOOL_URL = ("https://travel.state.gov/content/travel/en/us-visas/"
                       "visa-information-resources/wait-times.html")
QUARTERLY_REPORT_URL = "https://www.state.gov/quarterly-report-on-visa-wait-times/"

# The official US scheduling site for consular posts in China (CGI Federal
# Atlas360, contracted through 2032). LINKED for a human; never fetched.
US_CHINA_SCHEDULING_URL = "https://www.usvisascheduling.com/"
US_GROUP_APPOINTMENT_URL = "https://www.usvisascheduling.com/en-US/group-appointment/"
DS160_URL = "https://ceac.state.gov/genniv/"

FRANCE_VISAS_URL = "https://france-visas.gouv.fr/"
EU_VISA_INFO_URL = ("https://home-affairs.ec.europa.eu/policies/schengen-borders-"
                    "and-visa/visa-policy_en")

# A published snapshot older than this is surfaced as stale. State refreshes the
# wait-time page about monthly, so ~6 weeks means the human should re-check.
STALE_AFTER_DAYS = 45

# Nothing plausible exceeds ten years; a larger number is a parsing accident.
MAX_WAIT_DAYS = 3650


# ---------------------------------------------------------------------------
# Host allowlists — the compliance boundary, in data
# ---------------------------------------------------------------------------
# A WAIT-TIME claim is only accepted from the Department of State itself.
WAIT_TIME_HOSTS = frozenset({"travel.state.gov", "www.state.gov", "state.gov"})

# Hosts Ellis may LINK a human to. Official government / official ESP only.
OFFICIAL_LINK_HOSTS = frozenset({
    "travel.state.gov", "www.state.gov", "state.gov", "ceac.state.gov",
    "www.usvisascheduling.com", "usvisascheduling.com",
    "ais.usvisa-info.com", "www.ustraveldocs.com", "ustraveldocs.com",
    "france-visas.gouv.fr", "home-affairs.ec.europa.eu",
    "visa.vfsglobal.com", "www.vfsglobal.com",
    "visas-de.tlscontact.com", "www.blsinternational.com",
})

# Booking / scheduling systems. Ellis may hand a human a link to these and may
# NEVER send a request to them from this module. Reading their calendars by
# machine is what gets a traveler's appointment cancelled.
NEVER_FETCH_HOSTS = frozenset({
    "www.usvisascheduling.com", "usvisascheduling.com",
    "ais.usvisa-info.com", "www.ustraveldocs.com", "ustraveldocs.com",
    "ceac.state.gov",
    "visa.vfsglobal.com", "www.vfsglobal.com",
    "visas-de.tlscontact.com", "www.blsinternational.com",
})

# The registrable domains behind those hosts. Every one of these operators runs
# a DIFFERENT booking hostname in every market — visa.vfsglobal.com,
# fr.tlscontact.com, ru-mow.tlscontact.com, ais.usvisa-info.com — so an exact
# host list is a guard rail with gaps in it. A subdomain of any of these is a
# scheduling system whether or not anyone has written its name down yet.
#
# state.gov is deliberately NOT here: travel.state.gov is the published
# wait-time page Ellis is allowed to treat as a source, and only the exact host
# ceac.state.gov (the DS-160 application system) is a never-fetch.
NEVER_FETCH_DOMAINS = frozenset({
    "usvisascheduling.com", "ustraveldocs.com", "usvisa-info.com",
    "vfsglobal.com", "tlscontact.com", "blsinternational.com",
})

NEVER_FETCH_REASON = (
    "Ellis never sends a request to a scheduling or booking system. Automated "
    "slot search gets the TRAVELER's appointment cancelled and their visa "
    "revoked; the human opens this link and acts there.")


class AvailabilityError(ValueError):
    """Base for the honest refusals in this module."""


class ForbiddenAvailabilitySource(AvailabilityError):
    """Raised when something asks this module to treat a SCHEDULING system as a
    source of availability. That is the one thing that must never happen."""


class UntrustedWaitTimeSource(AvailabilityError):
    """Raised when a wait-time snapshot claims a source outside the Department
    of State. A wait time is a government publication or it is nothing."""


class UnofficialLink(AvailabilityError):
    """Raised when a deep link would point somewhere that is not an official
    government or official-ESP host. Sending an applicant to a look-alike
    booking site is how people lose money to visa scams."""


class MonitoringError(AvailabilityError):
    """Raised when a monitoring record would be dishonest — an automated poll
    dressed up as an observation, a reminder already in the past, a claim that
    a slot was booked or held."""


def host_of(url: str) -> str:
    """The hostname of a URL, lowercased. Pure parsing; sends nothing.

    A scheme-less value ("usvisascheduling.com/calendar") is still resolved to
    its host, so the guard below cannot be walked past by dropping "https://"."""
    text = str(url or "").strip()
    if not text:
        return ""
    if "://" not in text and not text.startswith("//"):
        text = "//" + text
    try:
        return (urlsplit(text).hostname or "").lower()
    except ValueError:
        return ""


def is_scheduling_host(url: str) -> bool:
    """True for any booking system, named or not.

    Matched on the registrable domain as well as the exact host, because these
    operators mint a new hostname per market. A guard rail that only knows the
    six spellings someone happened to write down would wave through
    ``ru-mow.tlscontact.com`` — which is the same calendar, and the same
    cancelled appointment for the traveller."""
    host = host_of(url)
    if not host:
        return False
    if host in NEVER_FETCH_HOSTS:
        return True
    return any(host == domain or host.endswith("." + domain)
               for domain in NEVER_FETCH_DOMAINS)


def assert_never_fetched(url: str) -> None:
    """The guard rail. Any future code that is about to REQUEST a URL from this
    module must call this first; a scheduling host raises rather than being
    quietly polled. (Today nothing in this module requests anything at all.)"""
    if is_scheduling_host(url):
        raise ForbiddenAvailabilitySource(
            f"{host_of(url)} is a scheduling/booking system: {NEVER_FETCH_REASON}")


# ---------------------------------------------------------------------------
# Visa categories as the Department of State publishes them
# ---------------------------------------------------------------------------
CATEGORIES = ("visitor", "student_exchange", "petition_worker", "crew_transit",
              "other", "immigrant")

CATEGORY_LABELS = {
    "visitor": "Visitor visa (B1/B2)",
    "student_exchange": "Student / exchange visitor (F, M, J)",
    "petition_worker": "Petition-based temporary worker (H, L, O, P, Q)",
    "crew_transit": "Crew and transit (C, D)",
    "other": "All other nonimmigrant visas",
    "immigrant": "Immigrant visa",
}

_CATEGORY_ALIASES = {
    "b1_b2": "visitor", "b1b2": "visitor", "b_1_b_2": "visitor",
    "visitor_visa": "visitor", "visitor_visas": "visitor",
    "visitor_visa_b1_b2": "visitor", "visitor_b1_b2": "visitor",
    "visitors": "visitor", "tourist": "visitor", "b2": "visitor",
    "student": "student_exchange", "students": "student_exchange",
    "student_visa": "student_exchange", "f_m_j": "student_exchange",
    "student_exchange_visitor": "student_exchange",
    "student_exchange_visitors": "student_exchange",
    "student_exchange_visitor_f_m_j": "student_exchange",
    "student_and_exchange_visitor": "student_exchange",
    "petition_based_temporary_workers": "petition_worker",
    "petition_based_temporary_worker": "petition_worker",
    "petition_based_temporary_workers_h_l_o_p_q": "petition_worker",
    "petition_based": "petition_worker", "temporary_workers": "petition_worker",
    "temporary_worker": "petition_worker", "h_l_o_p_q": "petition_worker",
    "h1b": "petition_worker", "h_1b": "petition_worker",
    "crew_and_transit": "crew_transit", "crew_and_transit_c_d": "crew_transit",
    "crew_transit_c_d": "crew_transit", "c_d": "crew_transit",
    "transit_crew": "crew_transit", "crew": "crew_transit",
    "all_other_nonimmigrant_visas": "other", "other_nonimmigrant": "other",
    "all_others": "other", "all_other": "other",
    "immigrant_visa": "immigrant", "immigrant_visas": "immigrant",
    "iv": "immigrant",
}


def _slug(value) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def normalize_category(value) -> str:
    """A published category name -> a canonical key, or '' when unrecognized.
    Unrecognized is honest: a wait time filed under a category Ellis cannot
    name is not shown as if it answered the caller's question."""
    key = _slug(value)
    if key in CATEGORIES:
        return key
    return _CATEGORY_ALIASES.get(key, "")


# ---------------------------------------------------------------------------
# The snapshot directory
# ---------------------------------------------------------------------------
_SNAPSHOT_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")


def wait_times_dir() -> Path:
    """Where human-placed wait-time snapshots live.

    ``$ELLIS_VISA_WAIT_TIMES_DIR`` wins; otherwise
    ``$ELLIS_DATA_DIR``/reference/visa_wait_times, otherwise the repo's
    ``data/reference/visa_wait_times``. Read fresh every call so a test (or a
    container remount) can point it elsewhere."""
    override = os.getenv("ELLIS_VISA_WAIT_TIMES_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    data_dir = os.getenv("ELLIS_DATA_DIR", "").strip()
    # backend/app/appt_availability.py -> <repo>/data
    base = Path(data_dir) if data_dir else Path(__file__).resolve().parents[2] / "data"
    return base / "reference" / "visa_wait_times"


def available_snapshots() -> list[str]:
    """The ISO dates of snapshots really on disk, ascending. Never raises: an
    unreadable directory is an empty list, which degrades honestly."""
    out: list[str] = []
    try:
        entries = list(wait_times_dir().iterdir())
    except OSError:
        return out
    for entry in entries:
        m = _SNAPSHOT_NAME.match(entry.name)
        if m and entry.is_file() and _parse_iso_date(m.group(1)) is not None:
            out.append(m.group(1))
    return sorted(out)


def is_available() -> bool:
    """True only when at least one readable snapshot is really on disk."""
    return bool(available_snapshots())


def ingest_plan() -> dict:
    """How a human produces the snapshot Ellis reads — stated plainly, because
    there is no feed to automate.

    This is documentation, not a fetcher: nothing here runs, and Ellis does not
    request travel.state.gov (it refuses automated fetches with HTTP 403)."""
    d = wait_times_dir()
    return {
        "why_manual": (
            "Researched 2026-08-11: the Department of State publishes visa "
            "appointment wait times only as a human-readable interactive tool "
            "and a narrative quarterly report. There is no documented JSON, "
            "CSV, or RSS feed, and travel.state.gov refuses automated fetches "
            "(HTTP 403). Ellis therefore reads a snapshot a human placed on "
            "disk rather than scraping a site that is declining to be read."),
        "official_pages": [GLOBAL_WAIT_TIMES_URL, WAIT_TIMES_TOOL_URL,
                           QUARTERLY_REPORT_URL],
        "directory": str(d),
        "filename": "<YYYY-MM-DD>.json  (the date the figures were published)",
        # Placeholders, deliberately: a worked example with real-looking dates
        # and day counts could be mistaken for data. Nothing in this plan is a
        # wait time.
        "shape": {
            "as_of": "<YYYY-MM-DD: the date the figures were published>",
            "source_url": GLOBAL_WAIT_TIMES_URL,
            "collected_by": "<who transcribed it>",
            "posts": [{
                "post": "<post name>", "post_code": "<3-letter post code>",
                "country": "<country>",
                "visitor": "<whole days>", "student_exchange": "<whole days>",
                "petition_worker": "<whole days>", "crew_transit": "<whole days>",
            }],
        },
        "categories": list(CATEGORIES),
        "rules": [
            "source_url must be a Department of State host; a snapshot "
            "sourced from a scheduling system is refused outright.",
            "wait_days is a whole number of days; an unpublished figure is "
            "left out, never zero-filled.",
            f"a snapshot older than {STALE_AFTER_DAYS} days is served with "
            "stale: true so the human re-checks the official page.",
        ],
        "note": "Never run by tests; tests read a fixture via "
                "$ELLIS_VISA_WAIT_TIMES_DIR.",
    }


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def _parse_iso_date(value) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value or "").strip()[:10])
    except (ValueError, TypeError):
        return None


def _coerce_wait_days(value) -> int | None:
    """A published figure -> whole days, or None when the post published none.
    None is the honest answer for 'N/A', '', or anything unparseable — the
    caller then reports 'unknown', never 0."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        n = float(value)
    else:
        s = str(value).strip().lower().replace(",", "")
        if not s:
            return None
        if s in {"same day", "sameday", "same-day"}:
            return 0
        m = re.match(r"^(\d+(?:\.\d+)?)", s)
        if not m:
            return None
        n = float(m.group(1))
    if n < 0 or n > MAX_WAIT_DAYS:
        return None
    return int(round(n))


def parse_snapshot(payload, *, as_of_fallback: str = "", origin: str = "") -> dict:
    """A placed snapshot -> {as_of, source_url, collected_by, records, warnings}.

    Raises ForbiddenAvailabilitySource if the snapshot claims a scheduling
    system as its source (that would mean someone scraped a protected
    calendar), and UntrustedWaitTimeSource for any other non-State host."""
    if isinstance(payload, list):
        payload = {"posts": payload}
    if not isinstance(payload, dict):
        raise AvailabilityError("snapshot must be a JSON object or array")

    source_url = str(payload.get("source_url") or "").strip() or GLOBAL_WAIT_TIMES_URL
    assert_never_fetched(source_url)          # a scheduling host is never a source
    if host_of(source_url) not in WAIT_TIME_HOSTS:
        raise UntrustedWaitTimeSource(
            f"wait times must come from the Department of State; "
            f"{host_of(source_url) or 'this snapshot'} is not one of "
            f"{sorted(WAIT_TIME_HOSTS)}")

    as_of = str(payload.get("as_of") or "").strip() or str(as_of_fallback or "")
    if _parse_iso_date(as_of) is None:
        raise AvailabilityError(
            "snapshot needs an ISO as_of date (or a <YYYY-MM-DD>.json filename); "
            "an undated wait time cannot be judged fresh or stale")

    rows = payload.get("posts")
    if rows is None:
        rows = payload.get("records") or payload.get("rows") or []
    if not isinstance(rows, list):
        raise AvailabilityError("snapshot 'posts' must be a list")

    records: list[dict] = []
    warnings: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            warnings.append(f"row {index}: not an object, skipped")
            continue
        post = str(row.get("post") or row.get("post_name")
                   or row.get("location") or "").strip()
        if not post:
            warnings.append(f"row {index}: no post name, skipped")
            continue
        common = {
            "post": post,
            "post_code": str(row.get("post_code") or "").strip().upper(),
            "country": str(row.get("country") or "").strip(),
            "as_of": as_of,
            "source": SOURCE,
            "source_url": source_url,
        }
        if row.get("category") is not None:
            category = normalize_category(row.get("category"))
            if not category:
                warnings.append(
                    f"{post}: unrecognized category {row.get('category')!r}, skipped")
                continue
            days = _coerce_wait_days(row.get("wait_days", row.get("days")))
            if days is None:
                warnings.append(f"{post}/{category}: no published figure, omitted")
                continue
            records.append({**common, "category": category, "wait_days": days})
            continue
        # Wide form: one row per post, one column per category.
        matched = False
        for key, value in row.items():
            if key in {"post", "post_name", "location", "post_code", "country",
                       "as_of", "source", "source_url", "note"}:
                continue
            category = normalize_category(key)
            if not category:
                continue
            matched = True
            days = _coerce_wait_days(value)
            if days is None:
                warnings.append(f"{post}/{category}: no published figure, omitted")
                continue
            records.append({**common, "category": category, "wait_days": days})
        if not matched:
            warnings.append(f"{post}: no recognizable category column, skipped")

    return {
        "as_of": as_of,
        "source": SOURCE,
        "source_url": source_url,
        "collected_by": str(payload.get("collected_by") or "").strip()
                        or "human transcription of the published page",
        "origin": origin,
        "records": records,
        "warnings": warnings,
    }


def _unavailable(reason: str, **extra) -> dict:
    """The single shape for 'Ellis does not know'. There is never a fabricated
    date, a zero wait, or an implied slot in here."""
    out = {
        "available": False,
        "reason": reason,
        "records": [],
        "as_of": None,
        "source": SOURCE,
        "source_url": GLOBAL_WAIT_TIMES_URL,
        "directory": str(wait_times_dir()),
        "how_to_provide": ingest_plan(),
    }
    out.update(extra)
    return out


def load_wait_times(as_of: str | None = None, *, today: dt.date | None = None) -> dict:
    """The newest placed snapshot (or the one dated ``as_of``), parsed.

    Never raises. With no snapshot on disk the result is explicitly
    unavailable with the reason and the instructions for placing one."""
    snapshots = available_snapshots()
    if not snapshots:
        return _unavailable(
            "no published wait-time snapshot is present. There is no public API "
            "or feed for US visa appointment availability, so Ellis reads a "
            "snapshot of the Department of State's published wait-time page "
            f"placed at {wait_times_dir()}/<YYYY-MM-DD>.json. Until one is "
            "placed, wait times are unknown — check the official page.")
    wanted = str(as_of or "").strip()
    if wanted:
        if wanted not in snapshots:
            return _unavailable(
                f"no wait-time snapshot dated {wanted} is present",
                requested_as_of=wanted, available_snapshots=snapshots)
        chosen = wanted
    else:
        chosen = snapshots[-1]

    path = wait_times_dir() / f"{chosen}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return _unavailable(f"wait-time snapshot {path.name} could not be read: {exc}",
                            available_snapshots=snapshots)
    try:
        parsed = parse_snapshot(raw, as_of_fallback=chosen, origin=path.name)
    except ForbiddenAvailabilitySource as exc:
        return _unavailable(
            f"wait-time snapshot {path.name} was REFUSED: {exc}",
            refused=True, available_snapshots=snapshots)
    except AvailabilityError as exc:
        return _unavailable(f"wait-time snapshot {path.name} is not usable: {exc}",
                            available_snapshots=snapshots)

    day = today or dt.date.today()
    snapshot_date = _parse_iso_date(parsed["as_of"]) or _parse_iso_date(chosen)
    age_days = (day - snapshot_date).days if snapshot_date else None
    stale = bool(age_days is not None and age_days > STALE_AFTER_DAYS)
    return {
        "available": True,
        "reason": "",
        "as_of": parsed["as_of"],
        "age_days": age_days,
        "stale": stale,
        "stale_note": (
            f"this snapshot is {age_days} days old; the Department of State "
            "refreshes wait times about monthly, so check the official page "
            "before relying on it") if stale else "",
        "source": parsed["source"],
        "source_url": parsed["source_url"],
        "collected_by": parsed["collected_by"],
        "origin": parsed["origin"],
        "records": parsed["records"],
        "warnings": parsed["warnings"],
        "available_snapshots": snapshots,
        "directory": str(wait_times_dir()),
        "is_live_slot_data": False,
        "note": ("A published wait-time estimate, not appointment availability. "
                 "It says roughly how long applicants have been waiting at this "
                 "post; it is not a slot, not a hold, and not a promise. Only "
                 "the official scheduling site shows real availability, and only "
                 "a human may look."),
    }


def _matches_post(record: dict, post: str) -> bool:
    wanted = _slug(post)
    return wanted in {_slug(record.get("post")), _slug(record.get("post_code"))}


def wait_time(post: str, category: str = "visitor", *, as_of: str | None = None,
              snapshot: dict | None = None) -> dict:
    """The published wait for one post + category, or an honest unknown.

    Returns ``{post, category, wait_days, as_of, source_url, ...}`` with
    ``wait_days: None`` and ``known: False`` whenever the figure is not in a
    placed snapshot. It never interpolates, averages, or guesses."""
    snap = snapshot if snapshot is not None else load_wait_times(as_of)
    wanted_category = normalize_category(category) or _slug(category)
    base = {
        "post": post,
        "category": wanted_category,
        "category_label": CATEGORY_LABELS.get(wanted_category, wanted_category),
        "wait_days": None,
        "known": False,
        "as_of": snap.get("as_of"),
        "source": SOURCE,
        "source_url": snap.get("source_url") or GLOBAL_WAIT_TIMES_URL,
        "is_live_slot_data": False,
    }
    if not snap.get("available"):
        return {**base, "reason": snap.get("reason", "wait-time data unavailable")}
    if not post:
        return {**base, "reason": "no post was named, so no wait time was looked up"}
    for record in snap.get("records") or []:
        if _matches_post(record, post) and record.get("category") == wanted_category:
            return {**base,
                    "post": record.get("post") or post,
                    "post_code": record.get("post_code", ""),
                    "country": record.get("country", ""),
                    "wait_days": record.get("wait_days"),
                    "known": True,
                    "as_of": record.get("as_of") or snap.get("as_of"),
                    "source_url": record.get("source_url") or base["source_url"],
                    "stale": snap.get("stale", False),
                    "reason": ""}
    return {**base, "reason": (
        f"the placed snapshot ({snap.get('as_of')}) publishes no "
        f"{base['category_label']} wait for {post}; check the official page")}


def posts_for_country(country: str, *, snapshot: dict | None = None) -> list[str]:
    """Post names a placed snapshot actually carries for a country. Empty when
    nothing is placed — never a hardcoded guess at which posts exist."""
    snap = snapshot if snapshot is not None else load_wait_times()
    wanted = _slug(country)
    seen: list[str] = []
    for record in snap.get("records") or []:
        if wanted and _slug(record.get("country")) != wanted:
            continue
        name = record.get("post") or ""
        if name and name not in seen:
            seen.append(name)
    return sorted(seen)


# ---------------------------------------------------------------------------
# Official deep links — where the HUMAN goes
# ---------------------------------------------------------------------------
def deep_link(kind: str, url: str, label: str, *, human_only: bool = True,
              description: str = "") -> dict:
    """Build one attributed link, refusing any host that is not official.

    ``human_only`` marks a link only a person may open. Every link records
    whether Ellis is permitted to fetch it — scheduling hosts are never
    fetched, and the payload says so out loud."""
    host = host_of(url)
    if host not in OFFICIAL_LINK_HOSTS:
        raise UnofficialLink(
            f"{host or url!r} is not an official host; Ellis links applicants "
            f"only to government or accredited-ESP sites")
    scheduling = is_scheduling_host(url)
    if scheduling:
        note = NEVER_FETCH_REASON
    elif host in WAIT_TIME_HOSTS:
        # Permission is not capability: travel.state.gov refuses non-browser
        # requests (HTTP 403), which is why the wait-time ingester reads a
        # human-placed snapshot instead of pulling this page.
        note = ("Published by the Department of State for people to read; "
                "travel.state.gov refuses automated fetches, so Ellis reads a "
                "human-placed snapshot of it rather than pulling the page.")
    else:
        note = ""
    return {
        "kind": kind,
        "label": label,
        "url": url,
        "host": host,
        "description": description,
        "human_only": bool(human_only or scheduling),
        "is_scheduling_system": scheduling,
        "ellis_may_fetch": not scheduling,
        "attribution": ATTRIBUTION,
        "note": note,
    }


def official_links(*, route: str = "us", country: str = "",
                   member_state: str = "") -> list[dict]:
    """The official places a human checks availability and books, for a route.

    ``route``: 'us' (Department of State consular posts) or 'schengen'.
    Nothing here is fetched; these are handoff destinations."""
    route_key = _slug(route) or "us"
    links: list[dict] = []
    if route_key.startswith("schengen") or route_key in {"eu", "eu_schengen"}:
        links.append(deep_link(
            "visa_policy_info", EU_VISA_INFO_URL,
            "European Commission — Schengen visa policy",
            human_only=False,
            description="The rules, including the Article 13(3) 59-month "
                        "fingerprint reuse that decides whether the applicant "
                        "must appear in person at all."))
        if _slug(member_state) in {"fr", "france"}:
            links.append(deep_link(
                "official_application_portal", FRANCE_VISAS_URL,
                "France-Visas — the official French visa portal",
                description="The applicant's own account and the official "
                            "appointment path for a French visa."))
        else:
            links.append({
                "kind": "official_application_portal",
                "label": "Member state portal — not determined",
                "url": "",
                "host": "",
                "description": (
                    "Ellis has no verified official portal for this member "
                    "state yet. The competent member state decides the portal "
                    "and the external service provider (VFS, TLScontact, BLS); "
                    "confirm it with the consulate rather than searching."),
                "human_only": True,
                "is_scheduling_system": False,
                "ellis_may_fetch": False,
                "attribution": "",
                "note": "Unknown is reported as unknown; Ellis does not guess a "
                        "booking address.",
            })
        return links

    links.append(deep_link(
        "wait_times_tool", WAIT_TIMES_TOOL_URL,
        "Official visa appointment wait times (live tool)",
        human_only=False,
        description="The Department of State's own wait-time tool. This is the "
                    "authoritative live figure; Ellis's stored snapshot is a "
                    "dated copy of the same publication."))
    links.append(deep_link(
        "wait_times_global", GLOBAL_WAIT_TIMES_URL,
        "Global visa wait times (published table)", human_only=False,
        description="Wait times for every post, refreshed about monthly."))
    links.append(deep_link(
        "quarterly_report", QUARTERLY_REPORT_URL,
        "Quarterly Report on Visa Wait Times", human_only=False,
        description="The statutory quarterly report behind the published "
                    "figures."))
    if not country or _slug(country) in {"china", "cn", "peoples_republic_of_china",
                                         "people_s_republic_of_china"}:
        links.append(deep_link(
            "scheduling_site", US_CHINA_SCHEDULING_URL,
            "Official US visa scheduling site (China posts)",
            description="Where the applicant or an authorized coordinator logs "
                        "in, sees real availability, and books. Ellis never "
                        "opens this by machine."))
        links.append(deep_link(
            "group_appointment", US_GROUP_APPOINTMENT_URL,
            "Group appointment request (10+ travelling together)",
            description="The sanctioned batch channel: a human group "
                        "coordinator submits the roster, the consulate "
                        "approves, then the coordinator books each member."))
    links.append(deep_link(
        "ds160", DS160_URL, "DS-160 (Consular Electronic Application Center)",
        description="Each applicant completes and signs their own DS-160; the "
                    "10-digit confirmation number is required to schedule."))
    return links


# ---------------------------------------------------------------------------
# Monitoring — a reminder, or a human's own observation. Never a poll.
# ---------------------------------------------------------------------------
MONITORING_POLICY = (
    "Ellis monitors by REMINDING a human to look, and by recording what a human "
    "saw in their own session. It never polls, scrapes, or watches a booking "
    "calendar, and it never books or holds a slot. Automated slot search gets "
    "the traveler's appointment cancelled and their visa revoked.")

REMINDER_KINDS = ("check_availability", "check_wait_time",
                  "booking_window_opens", "recheck_after_no_slots",
                  "confirm_appointment_still_held")

# How a human's observation may have been obtained. Anything automated is
# refused at the door — the word "observation" must always mean a person looked.
OBSERVATION_SOURCES = ("attended_human_session", "human_report",
                       "consulate_notice", "accredited_agent_channel")

_FORBIDDEN_OBSERVATION_SOURCES = {
    "automated_poll", "scrape", "scraper", "bot", "crawler", "headless_poll",
    "slot_watcher", "sniper",
}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse_instant(value) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        day = _parse_iso_date(text)
        if day is None:
            return None
        parsed = dt.datetime.combine(day, dt.time(9, 0))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


# Every monitoring record carries these, unconditionally. They are the whole
# point: a monitoring surface must never read as if Ellis obtained a slot.
def _never_booked() -> dict:
    return {
        "booked": False,
        "slot_held": False,
        "slot_reserved": False,
        "automated_check": False,
        "ellis_action": "remind and record only",
        "policy": MONITORING_POLICY,
    }


def create_reminder(*, case_id: str = "", post: str = "",
                    category: str = "visitor", due_at, kind: str = "check_availability",
                    note: str = "", created_by: str = "",
                    now: dt.datetime | None = None) -> dict:
    """A reminder for a HUMAN to go and check the official site.

    Refuses an unknown kind, a missing post, and a due time already in the
    past — a reminder that has already elapsed is not monitoring, it is a
    false sense of coverage."""
    if kind not in REMINDER_KINDS:
        raise MonitoringError(
            f"unknown reminder kind {kind!r}; expected one of {list(REMINDER_KINDS)}")
    post_name = str(post or "").strip()
    if not post_name:
        raise MonitoringError("a reminder needs the post the human will check")
    when = _parse_instant(due_at)
    if when is None:
        raise MonitoringError("a reminder needs an ISO due_at instant")
    moment = now or _now()
    if when <= moment:
        raise MonitoringError(
            "due_at is already in the past; Ellis does not create a reminder "
            "that can never fire")
    canonical_category = normalize_category(category) or "other"
    return {
        "id": uuid.uuid4().hex,
        "record_kind": "reminder",
        "kind": kind,
        "case_id": str(case_id or ""),
        "post": post_name,
        "category": canonical_category,
        "category_label": CATEGORY_LABELS.get(canonical_category, canonical_category),
        "due_at": when.isoformat(),
        "created_at": moment.isoformat(),
        "created_by": str(created_by or ""),
        "status": "scheduled",
        "human_act": ("open the official scheduling site, sign in, and check "
                      "availability yourself"),
        "note": str(note or ""),
        **_never_booked(),
    }


def record_human_observation(*, case_id: str = "", post: str = "",
                             category: str = "visitor", observed_at=None,
                             earliest_offered_date: str = "",
                             slots_seen: int | None = None,
                             observer: str = "",
                             source_kind: str = "attended_human_session",
                             note: str = "",
                             now: dt.datetime | None = None) -> dict:
    """Record what a HUMAN saw in their own session — the only availability
    Ellis ever holds beyond published wait times.

    Refuses any source that describes automation. If a caller wants to log a
    poll, the answer is no: that is the prohibited act, and mislabelling it as
    an observation would launder it."""
    kind = _slug(source_kind)
    if kind in _FORBIDDEN_OBSERVATION_SOURCES:
        raise ForbiddenAvailabilitySource(
            f"{source_kind!r} describes an automated check of a booking "
            f"calendar. {NEVER_FETCH_REASON}")
    if kind not in OBSERVATION_SOURCES:
        raise MonitoringError(
            f"unknown observation source {source_kind!r}; expected one of "
            f"{list(OBSERVATION_SOURCES)}")
    post_name = str(post or "").strip()
    if not post_name:
        raise MonitoringError("an observation needs the post the human looked at")
    moment = now or _now()
    when = moment if observed_at in (None, "") else _parse_instant(observed_at)
    if when is None:
        raise MonitoringError(
            "observed_at must be an ISO instant; an observation with an "
            "unreadable time cannot be judged current")
    if when > moment:
        raise MonitoringError("an observation cannot be dated in the future")
    earliest = str(earliest_offered_date or "").strip()
    if earliest and _parse_iso_date(earliest) is None:
        raise MonitoringError("earliest_offered_date must be an ISO date")
    if slots_seen is not None:
        if isinstance(slots_seen, bool) or not isinstance(slots_seen, int) or slots_seen < 0:
            raise MonitoringError("slots_seen must be a non-negative whole number")
    canonical_category = normalize_category(category) or "other"
    return {
        "id": uuid.uuid4().hex,
        "record_kind": "observation",
        "case_id": str(case_id or ""),
        "post": post_name,
        "category": canonical_category,
        "category_label": CATEGORY_LABELS.get(canonical_category, canonical_category),
        "observed_at": when.isoformat(),
        "recorded_at": moment.isoformat(),
        "observer": str(observer or ""),
        "source_kind": kind,
        "provenance": "a human looked, in their own authenticated session",
        "earliest_offered_date": earliest,
        "slots_seen": slots_seen,
        "note": str(note or ""),
        "is_live_slot_data": False,
        "expires_note": ("What a person saw at one moment. Availability moves "
                         "constantly; this is a note, not an offer, and nothing "
                         "is held."),
        **_never_booked(),
    }


def due_reminders(records, *, now: dt.datetime | None = None) -> list[dict]:
    """The reminders that have come due, oldest first. Pure filtering — firing
    a reminder means telling a human, never checking anything for them."""
    moment = now or _now()
    due: list[dict] = []
    for record in records or []:
        if not isinstance(record, dict) or record.get("record_kind") != "reminder":
            continue
        if record.get("status") not in (None, "", "scheduled"):
            continue
        when = _parse_instant(record.get("due_at"))
        if when is not None and when <= moment:
            due.append(record)
    return sorted(due, key=lambda r: str(r.get("due_at") or ""))


def monitoring_summary(records=None, *, now: dt.datetime | None = None) -> dict:
    """The monitoring block a cockpit payload carries. Says what Ellis is
    watching for, and states plainly that nothing is booked or held."""
    records = list(records or [])
    reminders = [r for r in records if r.get("record_kind") == "reminder"]
    observations = [r for r in records if r.get("record_kind") == "observation"]
    return {
        "reminders": reminders,
        "observations": observations,
        "due_now": due_reminders(reminders, now=now),
        "counts": {"reminders": len(reminders), "observations": len(observations),
                   "due_now": len(due_reminders(reminders, now=now))},
        **_never_booked(),
    }


# ---------------------------------------------------------------------------
# The composite the cockpit reads
# ---------------------------------------------------------------------------
def availability(*, post: str = "", country: str = "", category: str = "visitor",
                 route: str = "us", member_state: str = "",
                 as_of: str | None = None, records=None,
                 now: dt.datetime | None = None) -> dict:
    """Everything Ellis legitimately knows about availability, plus where the
    human goes to find out for real.

    With nothing placed on disk this is entirely honest-unavailable: an
    explicit reason, the official links, and no invented date."""
    snapshot = load_wait_times(as_of)
    canonical_category = normalize_category(category) or "visitor"
    entry = wait_time(post, canonical_category, snapshot=snapshot) if post else None
    country_posts = posts_for_country(country, snapshot=snapshot) if country else []
    return {
        "route": _slug(route) or "us",
        "post": post,
        "country": country,
        "category": canonical_category,
        "category_label": CATEGORY_LABELS.get(canonical_category, canonical_category),
        "wait_time": entry,
        "wait_times": snapshot.get("records") or [],
        "wait_time_data": {k: v for k, v in snapshot.items() if k != "records"},
        "posts_in_country": country_posts,
        "official_links": official_links(route=route, country=country,
                                         member_state=member_state),
        "monitoring": monitoring_summary(records, now=now),
        "live_slot_data_available": False,
        "why_no_live_slots": (
            "No booking system publishes an availability feed, and Ellis will "
            "not read one by machine: automated slot search gets the traveler's "
            "appointment cancelled and their visa revoked. Real availability is "
            "visible only to a signed-in human on the official site."),
        "source": SOURCE,
        "attribution": ATTRIBUTION,
    }
