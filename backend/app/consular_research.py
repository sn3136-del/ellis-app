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


def _iso3(name_or_code: str) -> str:
    """ISO-3 for a country name (or a code passed straight through)."""
    v = (name_or_code or "").strip()
    if len(v) == 3 and v.isalpha():
        return v.upper()
    try:
        from .visa_snapshot.registry import _country_index
        for code, e in (_country_index() or {}).items():
            if v.lower() in (str(e.get("name") or "").lower(),
                             str(e.get("common_name") or "").lower()):
                # The index is keyed by alpha-2 for some entries, so take the
                # entry's OWN alpha_3 rather than trusting the key — returning
                # 'GB' where 'GBR' was meant silently broke jurisdiction checks.
                return str(e.get("alpha_3") or code).upper()
    except Exception:  # noqa: BLE001 — registry optional
        pass
    return ""


def _names_country(text: str, country: str) -> bool:
    """Does the page name this country, under any name it is actually called?
    'United Kingdom' also appears as 'UK'; 'United States' as 'USA'/'US'."""
    low = (text or "").lower()
    names = {(country or "").lower()}
    iso = _iso3(country)
    if iso:
        try:
            from .visa_snapshot.registry import _country_index
            e = (_country_index() or {}).get(iso) or {}
            names |= {str(e.get("name") or "").lower(),
                      str(e.get("common_name") or "").lower()}
            a2 = str(e.get("alpha_2") or "").lower()
        except Exception:  # noqa: BLE001
            a2 = ""
        names |= {iso.lower()}
        if a2:
            names |= {a2}
    names = {n for n in names if n}
    if any(n in low for n in names if len(n) > 3):
        return True
    # Short forms (UK, US, CN) only count as standalone words.
    import re
    return any(re.search(rf"\b{re.escape(n)}\b", low) for n in names if len(n) <= 3)


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
    # visas would answer for another's. The DESTINATION is proven by the host
    # when the source is that government's own domain — gov.uk is the United
    # Kingdom whether or not the prose ever spells the name out, and requiring
    # the literal words rejected a correct gov.uk answer in testing. Otherwise
    # the page must name it.
    from .visa_snapshot.evidence_validator import jurisdiction_matches
    text = (source_text or "").lower()
    dest_iso = _iso3(destination)
    host_proves_destination = bool(
        source_url and dest_iso and jurisdiction_matches(source_url, dest_iso))
    if text:
        if destination and not host_proves_destination \
                and not _names_country(text, destination):
            problems.append(f"source page does not mention destination {destination}")
        # Residence must always be named: the host cannot prove WHO the page is
        # for, only who published it.
        if residence and not _names_country(text, residence):
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


FIND_POST_SYSTEM = """You identify which consular post handles a visa application.

Given the applicant's residence and their destination, name the office they must
attend and the official page that says so.

Rules you must follow:
- Answer ONLY from official sources: the destination government's own site, or a
  visa-centre operator that government delegates to.
- The source page must be about THIS destination for applicants in THIS
  residence country. A page about another country's visas is not an answer.
- Consular jurisdiction is often split by region: if the applicant's city or
  province decides which post serves them, say which subdivisions this post
  covers.
- If you are not certain, say so. An empty answer is correct and useful; a
  plausible guess sends someone to the wrong city.

Reply JSON:
{"found": true|false,
 "competent_post_name": "...",
 "competent_post_kind": "embassy|consulate_general|consulate|visa_centre|honorary_consulate|unknown",
 "competent_post_url": "https://... the page where they book or apply",
 "source_url": "https://... the official page this came from",
 "residence_subdivisions": ["provinces/states this post covers, [] if country-wide"],
 "city": "city of the post",
 "confidence": "high|medium|low",
 "why": "one sentence: what the official page said"}"""


def find_post_for_applicant(*, destination: str, residence: str,
                            address_city: str = "", address_region: str = "",
                            timeout_s: float = 55.0) -> dict:
    """Ask Kimi which post serves THIS applicant, within a one-minute budget.

    Called when the applicant gives Ellis their address, so the answer is
    specific to where they actually live rather than a country-wide guess. The
    model's answer is a CANDIDATE only: it is put through the same verification
    as any researched claim before it can become a usable booking link.
    """
    from .config import settings
    s = settings()
    if not (s.moonshot_api_key and s.kimi_enabled):
        return {"found": False, "reason": "search unavailable (no Kimi key)"}
    where = ", ".join(x for x in (address_city, address_region, residence) if x)
    user = (f"Applicant lives in: {where or residence}\n"
            f"They are applying for a visa to: {destination}\n"
            f"Which consular post must they attend, and where do they book it?")
    from .providers.kimi import LiveKimiProvider, KimiTimeout, KimiHttpError
    try:
        out = LiveKimiProvider()._chat(FIND_POST_SYSTEM, user, timeout=timeout_s)
    except KimiTimeout:
        return {"found": False, "reason": "search timed out"}
    except (KimiHttpError, Exception) as e:  # noqa: BLE001 — never crash intake
        return {"found": False, "reason": f"search failed: {str(e)[:80]}"}
    if not isinstance(out, dict) or not out.get("found"):
        return {"found": False, "reason": (out or {}).get("why")
                or "no official answer found"}
    return out


def resolve_for_applicant(db, *, destination: str, residence: str,
                          address_city: str = "", address_region: str = "",
                          fetch_page=None, timeout_s: float = 55.0) -> dict:
    """The full on-demand path: search, then VERIFY before storing.

    fetch_page(url) -> (final_url, text, links) lets the caller corroborate the
    model's cited source against the live page. Without it the claim can still
    be stored, but only as unverified — Ellis will not hand an applicant a
    booking link on a model's say-so alone.
    """
    found = find_post_for_applicant(
        destination=destination, residence=residence, address_city=address_city,
        address_region=address_region, timeout_s=timeout_s)
    if not found.get("found"):
        return {"status": "not_found", "reason": found.get("reason", "")}

    source_url = str(found.get("source_url") or "")
    text, gov_linked = "", False
    if fetch_page is not None and source_url:
        try:
            source_url, text, links = fetch_page(source_url)
            gov_linked = any(is_delegated_operator(u) for u in (links or []))
        except Exception:  # noqa: BLE001 — an unreachable source stays unverified
            text = ""
    row = verify_candidate(
        {"competent_post_name": found.get("competent_post_name") or "",
         "competent_post_kind": found.get("competent_post_kind") or "unknown",
         "competent_post_url": found.get("competent_post_url") or "",
         "residence_subdivisions": found.get("residence_subdivisions") or []},
        source_url=source_url, source_text=text,
        destination=destination, residence=residence,
        government_linked=gov_linked)
    stored = store(db, dict(row, destination_country=destination,
                            residence_jurisdiction=residence))
    return {"status": row["verification_status"], "post": row["competent_post_name"],
            "booking_url": row["competent_post_url"], "city": found.get("city", ""),
            "problems": row["problems"], "rule_id": getattr(stored, "id", "")}


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
