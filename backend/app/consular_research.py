"""Researching WHICH consulate serves an applicant, and where they book.

Ellis cannot show an appointment button without answering a concrete question:
"this person lives in China and wants a UK visa — which office do they attend,
and what is that office's booking address?" That is the
consular_jurisdiction_rules table, and its seed file says plainly that it is
"populated only by the verified research pipeline with SourceEvidence backing".

This module is that pipeline for jurisdictions. It exists because the answer
must never be typed from memory: a wrong consulate sends someone to another
city for an appointment that took months to get, and a wrong booking link is
indistinguishable from the visa-scam sites that take people's money. So every
row this writes carries the official page it came from, and a row that cannot
be verified is stored as UNVERIFIED — which the resolver refuses to use — or
not stored at all.

What "verified" requires here, all of it, or the row is not verified:
  1. The claim came from a page on an OFFICIAL host — the destination's own
     government domain, or a visa-centre operator that destination's government
     actually links to.
  2. The page names BOTH the residence jurisdiction and the destination, so a
     page about French visas in Morocco can never answer for France in China.
  3. The booking URL is HTTPS and on a host the same official source links to.
"""
from __future__ import annotations

from urllib.parse import urlparse

from .visa_snapshot.authority import is_government_host

# Visa-centre operators governments genuinely delegate to. Being on this list
# is NOT enough on its own: an official government page must link to the host
# for a given route before Ellis will send an applicant there.
DELEGATED_OPERATORS = (
    "vfsglobal.com", "tlscontact.com", "blsinternational.com",
    "visaforchina.cn", "vfsevisa.com", "usvisa-info.com",
    "visaforkorea-sh.com", "almaviva.it",
)

POST_KINDS = ("embassy", "consulate_general", "consulate", "visa_centre",
              "honorary_consulate", "unknown")


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def is_delegated_operator(url: str) -> bool:
    h = _host(url)
    return any(h == d or h.endswith("." + d) for d in DELEGATED_OPERATORS)


def acceptable_booking_host(url: str, *, government_linked: bool) -> bool:
    """May Ellis send an applicant to this booking address?

    A government host always qualifies. A delegated visa-centre operator
    qualifies ONLY when an official government page for this route links to it
    — otherwise any look-alike on a contractor-shaped domain would pass, which
    is precisely how applicants get sent to scam booking sites.
    """
    if not url.lower().startswith("https://"):
        return False
    if is_government_host(_host(url)):
        return True
    return bool(government_linked and is_delegated_operator(url))


def verify_candidate(candidate: dict, *, source_url: str, source_text: str,
                     destination: str, residence: str,
                     government_linked: bool = False) -> dict:
    """Decide whether a researched jurisdiction claim may be stored as
    VERIFIED. Returns the row to store plus the reasons, so a rejection is
    always explainable rather than a silent drop."""
    problems: list[str] = []
    post_name = str(candidate.get("competent_post_name") or "").strip()
    booking = str(candidate.get("competent_post_url") or "").strip()
    kind = str(candidate.get("competent_post_kind") or "unknown").strip()

    if not post_name:
        problems.append("no competent post named")
    if kind not in POST_KINDS:
        problems.append(f"unknown post kind {kind!r}")
    if not source_url:
        problems.append("no source page")
    elif not (is_government_host(_host(source_url))
              or is_delegated_operator(source_url)):
        problems.append(f"source host {_host(source_url)!r} is not official")

    # The page must actually be about THIS route, or a page about one country's
    # visas would answer for another's.
    text = (source_text or "").lower()
    if text:
        if destination and destination.lower() not in text:
            problems.append(f"source page does not mention destination {destination}")
        if residence and residence.lower() not in text:
            problems.append(f"source page does not mention residence {residence}")
    else:
        problems.append("no source text to corroborate the claim")

    if booking and not acceptable_booking_host(
            booking, government_linked=government_linked):
        problems.append(
            f"booking host {_host(booking)!r} is neither a government domain nor "
            f"an operator this route's official page links to")

    return {
        "destination_country": destination,
        "residence_jurisdiction": residence,
        "residence_subdivisions": list(candidate.get("residence_subdivisions") or []),
        "competent_post_name": post_name,
        "competent_post_kind": kind,
        "competent_post_url": booking if not problems else "",
        "covers_nationalities": list(candidate.get("covers_nationalities") or []),
        "conditions": list(candidate.get("conditions") or []),
        "verification_status": "verified" if not problems else "unverified",
        "source_url": source_url,
        "problems": problems,
    }


def store(db, row: dict, *, snapshot_date: str = "") -> object:
    """Persist a researched rule. An UNVERIFIED row is still stored — the gap
    is data Ellis knows it lacks, and the resolver ignores anything that is not
    verified — but it never gains a booking URL."""
    from sqlalchemy import select
    from .visa_snapshot import SNAPSHOT_DATE
    from .visa_snapshot.models import ConsularJurisdictionRule

    snap = snapshot_date or SNAPSHOT_DATE
    existing = db.execute(select(ConsularJurisdictionRule).where(
        ConsularJurisdictionRule.destination_country == row["destination_country"],
        ConsularJurisdictionRule.residence_jurisdiction == row["residence_jurisdiction"],
        ConsularJurisdictionRule.competent_post_name == row["competent_post_name"],
    )).scalars().first()
    target = existing or ConsularJurisdictionRule(
        snapshot_date=snap,
        destination_country=row["destination_country"],
        residence_jurisdiction=row["residence_jurisdiction"])
    target.residence_subdivisions = row["residence_subdivisions"]
    target.competent_post_name = row["competent_post_name"]
    target.competent_post_kind = row["competent_post_kind"]
    target.competent_post_url = row["competent_post_url"]
    target.covers_nationalities = row["covers_nationalities"]
    target.conditions = row["conditions"]
    target.verification_status = row["verification_status"]
    target.evidence_ids = [row["source_url"]] if row.get("source_url") else []
    if existing is None:
        db.add(target)
    db.commit()
    return target


def pairs_needing_rules(db, *, limit: int = 0) -> list[tuple[str, str]]:
    """(destination, residence) pairs whose route is decided in person and that
    have no verified rule yet — the actual work list, so a run can be measured
    against something real instead of guessed at."""
    from sqlalchemy import select, text as sql_text
    from .visa_snapshot.models import ConsularJurisdictionRule
    from .assisted_booking import NEEDS_APPOINTMENT

    have = {(r.destination_country, r.residence_jurisdiction) for r in db.execute(
        select(ConsularJurisdictionRule).where(
            ConsularJurisdictionRule.verification_status == "verified")).scalars()}
    outcomes = ",".join(f"'{o}'" for o in sorted(NEEDS_APPOINTMENT))
    rows = db.execute(sql_text(
        f"SELECT DISTINCT destination_country, passport_nationality "
        f"FROM global_route_pair_policies WHERE route_outcome IN ({outcomes})"
    )).fetchall()
    # Residence defaults to nationality for the work list; a real run refines it
    # per applicant, and a rule keyed on residence serves every nationality it
    # covers.
    todo = [(d, n) for d, n in rows if (d, n) not in have]
    todo.sort()
    return todo[:limit] if limit else todo


def coverage(db) -> dict:
    """How much of the in-person world Ellis can actually route — reported as
    the honest fraction, never rounded up."""
    from sqlalchemy import select
    from .visa_snapshot.models import ConsularJurisdictionRule
    rules = db.execute(select(ConsularJurisdictionRule)).scalars().all()
    verified = [r for r in rules if r.verification_status == "verified"]
    bookable = [r for r in verified if (r.competent_post_url or "").startswith("https://")]
    todo = pairs_needing_rules(db)
    return {"rules_total": len(rules), "verified": len(verified),
            "with_booking_url": len(bookable),
            "pairs_still_unrouted": len(todo),
            "destinations_covered": len({r.destination_country for r in verified})}
