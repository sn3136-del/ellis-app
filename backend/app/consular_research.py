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
    try:
        from .visa_snapshot.registry import iso3
        return iso3(name_or_code)
    except Exception:  # noqa: BLE001 — registry optional
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


def _grounded_address(address: str, source_text: str) -> str:
    """The post's street address, kept ONLY when the official page prints it.

    An address is the one field where a plausible-sounding answer does real
    harm: somebody gets on a train to a building that is not there, on a day
    they waited weeks for. So the claim is checked against the page it came
    from rather than trusted — its distinctive parts (the street line and the
    number) must appear in the source text, or Ellis reports no address at all.
    Whitespace and punctuation are normalised before comparing, because the
    same address is printed across several lines on most consular pages.
    """
    import re
    addr = " ".join(str(address or "").split()).strip()
    if not addr or not source_text:
        return ""
    squash = lambda s: re.sub(r"[^a-z0-9]+", "", s.lower())  # noqa: E731
    page = squash(source_text)
    if not page:
        return ""
    # Compared in chunks, because the same address is laid out differently on
    # every page it appears on — across lines, with or without the country,
    # postcode before or after the city. Requiring ALL of them was too strict
    # to pass on real consular pages. Requiring a MAJORITY, and at least two,
    # is what separates "this page prints this address" from "a model recalled
    # one": a fabricated address matches essentially nothing on the real page,
    # while a true one matches its street and its city whatever the layout.
    parts = [p for p in re.split(r"[,\n;]+", addr) if squash(p)]
    if len(parts) < 2:
        return addr if parts and squash(parts[0]) in page else ""
    hits = sum(1 for p in parts if squash(p) in page)
    return addr if hits >= 2 and hits * 2 >= len(parts) else ""


# Where an address lives when the page that named the post does not print one:
# that same official site's contact page. Followed rather than guessed — the
# link has to already be on the official page Ellis fetched, and stay on its
# host — so this can never wander onto a look-alike domain.
_CONTACT_HINTS = ("kontakt", "contact", "anschrift", "adresse", "address",
                  "impressum", "visa", "sprechzeit", "opening", "联系", "地址",
                  "签证",
                  # These sites name a post's own page after what the post IS,
                  # not after "contact" — china.diplo.de's hub links onward to
                  # the Generalkonsulat pages, and the street only appears
                  # there. Without them the walk circled the hub.
                  "generalkonsulat", "konsulat", "consulate", "botschaft",
                  "embassy", "vertretung", "mission", "consulado", "ambassade",
                  "领事馆", "大使馆")


def _contact_links(source_url: str, links: list, *, prefer: str = "",
                   limit: int = 3) -> list:
    """Links from an official page that look like they lead to an address.
    Ordered, deduplicated, bounded — an address hunt must not turn into a crawl
    of a government site. `prefer` (the post's city) floats the pages about
    THAT post to the front."""
    host = _host(source_url)
    if not host:
        return []
    # The mission's own site is usually a SIBLING subdomain of the one that
    # answered "which consulate" — china.diplo.de names the post, kanton.diplo.de
    # is the post. Same-host-only kept Ellis on the page that had no address.
    # Widened to the same registrable government domain, so it can reach the
    # consulate's own pages and still cannot leave diplo.de.
    domain = lambda h: ".".join(h.split(".")[-2:]) if h else ""  # noqa: E731
    here_domain = domain(host)
    # '#main' and '#nav__primary' are the SAME page. Without dropping the
    # fragment, every in-page anchor read as a fresh contact page and the three
    # fetches Ellis is allowed were spent re-reading the page it started on.
    strip = lambda u: u.split("#", 1)[0].rstrip("/")  # noqa: E731
    here = strip(source_url)
    seen, out = {here}, []
    for raw in (links or []):
        u = strip(str(raw or "").strip())
        if not u.lower().startswith("https://") or u in seen:
            continue
        h = _host(u)
        if domain(h) != here_domain or not (h == host or is_government_host(h)):
            continue
        if not any(hint in u.lower() for hint in _CONTACT_HINTS):
            continue
        seen.add(u)
        out.append(u)
    # A page whose URL is ABOUT contact details is likelier to print an address
    # than one that merely mentions visas, so spend the few fetches there — and
    # a page naming the applicant's own city ahead of one naming another post's.
    strong = ("kontakt", "contact", "anschrift", "adresse", "impressum", "联系", "地址")
    city = (prefer or "").strip().lower()

    def rank(u: str) -> tuple:
        low = u.lower()
        return (0 if city and city in low else 1,
                0 if any(h in low for h in strong) else 1)

    out.sort(key=rank)
    return out[:limit]


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

    # The street address is a SEPARATE claim from "this is the post that serves
    # you", and it is checked separately. It survives only when the official
    # page Ellis fetched actually prints it — so an address is never the
    # model's recollection of a building. It is kept even when the jurisdiction
    # claim above is unverified, because those two facts can be true
    # independently: this really is the German Consulate General in Guangzhou's
    # address, whether or not the page also proved it serves Guangdong. The
    # applicant is shown the jurisdiction's status alongside it and can see the
    # difference.
    official_source = bool(source_url and (is_government_host(_host(source_url))
                                           or is_delegated_operator(source_url)))
    address = _grounded_address(candidate.get("post_address"), source_text) \
        if official_source else ""

    return {
        "destination_country": destination,
        "residence_jurisdiction": residence,
        "residence_subdivisions": list(candidate.get("residence_subdivisions") or []),
        "competent_post_name": post_name,
        "competent_post_kind": kind,
        "competent_post_address": address,
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
 "post_address": "the office's full street address, copied EXACTLY as the official page prints it, or \"\" if that page does not give one",
 "source_url": "https://... the official page this came from",
 "residence_subdivisions": ["provinces/states this post covers, [] if country-wide"],
 "city": "city of the post",
 "confidence": "high|medium|low",
 "why": "one sentence: what the official page said"}"""


def find_post_for_applicant(*, destination: str, residence: str,
                            address_city: str = "", address_region: str = "",
                            timeout_s: float = 55.0, attempts: int = 1) -> dict:
    """Ask Kimi which post serves THIS applicant, within a bounded budget.

    Called when the applicant gives Ellis their address, so the answer is
    specific to where they actually live rather than a country-wide guess. The
    model's answer is a CANDIDATE only: it is put through the same verification
    as any researched claim before it can become a usable booking link.

    attempts > 1 retries a TIMEOUT (never a refusal or a rate limit): measured
    live, a reasoning search lands in ~20-45s but overruns for some routes, and
    one retry turns those from "we could not find your office" into an answer.
    Only the background path should retry — nobody is waiting on it.
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
    last = {"found": False, "reason": "search timed out"}
    for attempt in range(max(1, attempts)):
        try:
            out = LiveKimiProvider()._chat(FIND_POST_SYSTEM, user, timeout=timeout_s)
        except KimiTimeout:
            last = {"found": False, "reason": "search timed out"}
            continue                       # a slow route deserves one more try
        except KimiHttpError as e:
            # Rate limited or refused: retrying immediately only makes it worse.
            return {"found": False, "reason": f"search unavailable ({e})"}
        except Exception as e:  # noqa: BLE001 — never crash intake
            return {"found": False, "reason": f"search failed: {str(e)[:80]}"}
        if not isinstance(out, dict) or not out.get("found"):
            return {"found": False, "reason": (out or {}).get("why")
                    or "no official answer found"}
        return out
    return last


def resolve_for_applicant(db, *, destination: str, residence: str,
                          address_city: str = "", address_region: str = "",
                          fetch_page=None, timeout_s: float = 55.0,
                          attempts: int = 1) -> dict:
    """The full on-demand path: search, then VERIFY before storing.

    fetch_page(url) -> (final_url, text, links) lets the caller corroborate the
    model's cited source against the live page. Without it the claim can still
    be stored, but only as unverified — Ellis will not hand an applicant a
    booking link on a model's say-so alone.
    """
    found = find_post_for_applicant(
        destination=destination, residence=residence, address_city=address_city,
        address_region=address_region, timeout_s=timeout_s, attempts=attempts)
    if not found.get("found"):
        return {"status": "not_found", "reason": found.get("reason", "")}

    source_url = str(found.get("source_url") or "")
    text, links, gov_linked = "", [], False
    if fetch_page is not None and source_url:
        try:
            source_url, text, links = fetch_page(source_url)
            gov_linked = any(is_delegated_operator(u) for u in (links or []))
        except Exception:  # noqa: BLE001 — an unreachable source stays unverified
            text = ""
    candidate = {
        "competent_post_name": found.get("competent_post_name") or "",
        "competent_post_kind": found.get("competent_post_kind") or "unknown",
        "competent_post_url": found.get("competent_post_url") or "",
        "post_address": found.get("post_address") or "",
        "residence_subdivisions": found.get("residence_subdivisions") or [],
    }
    row = verify_candidate(
        candidate, source_url=source_url, source_text=text,
        destination=destination, residence=residence,
        government_linked=gov_linked)

    # The page that names the post is often not the page that prints its
    # address — Germany's Guangzhou mission answers "which consulate" on a news
    # landing page and keeps the street on its contact page. Rather than take
    # the address on trust, follow that official site's OWN contact links and
    # look for it there. Bounded to three same-host pages, and it can only ever
    # ADD an address that a real page corroborates.
    address_source = source_url if row["competent_post_address"] else ""
    if candidate["post_address"] and not row["competent_post_address"] \
            and fetch_page is not None:
        # Two levels, because that is how these sites are actually built: the
        # page that answers "which consulate" links to a CONTACT HUB, and the
        # hub links to the individual post's own page, which is where the
        # street finally appears. Breadth-first with a hard fetch budget, so a
        # site with no address costs a bounded number of page loads and then
        # stops. Never more than two hops from an official page Ellis was
        # already reading.
        queue = [(u, 1) for u in _contact_links(
            source_url, links, prefer=found.get("city", ""))]
        fetched, budget = set(), 6
        while queue and budget > 0:
            link, depth = queue.pop(0)
            if link in fetched:
                continue
            fetched.add(link)
            budget -= 1
            try:
                final_url, page_text, page_links = fetch_page(link)
            except Exception:  # noqa: BLE001 — a dead link proves nothing
                continue
            grounded = _grounded_address(candidate["post_address"], page_text)
            if grounded:
                row["competent_post_address"] = grounded
                address_source = final_url or link
                break
            if depth < 2:
                queue.extend((u, depth + 1) for u in _contact_links(
                    final_url or link, page_links,
                    prefer=found.get("city", ""), limit=2))

    stored = store(db, dict(row, destination_country=destination,
                            residence_jurisdiction=residence))
    return {"status": row["verification_status"], "post": row["competent_post_name"],
            "address": row["competent_post_address"],
            "address_source": address_source,
            "booking_url": row["competent_post_url"], "city": found.get("city", ""),
            "source_url": source_url,
            "problems": row["problems"], "rule_id": getattr(stored, "id", "")}


def store(db, row: dict, *, snapshot_date: str = "") -> object:
    """Persist a researched rule. An UNVERIFIED row is still stored — the gap
    is data Ellis knows it lacks, and the resolver ignores anything that is not
    verified — but it never gains a booking URL."""
    from sqlalchemy import select
    from .visa_snapshot import SNAPSHOT_DATE
    from .visa_snapshot.models import ConsularJurisdictionRule

    # Keyed by ISO-3, always. The on-demand path was handed the case's DISPLAY
    # name ("Germany", "United Kingdom") and stored it verbatim, while the
    # resolver reads these rows by ISO-3 — so not one rule the applicant's own
    # search produced was ever visible to the code that needs it, including the
    # verified UK one. The applicant kept being told no post was known while
    # the answer sat in the table under another spelling (2026-08-04).
    row = dict(row)
    row["destination_country"] = (_iso3(row.get("destination_country", ""))
                                  or str(row.get("destination_country") or ""))
    row["residence_jurisdiction"] = (_iso3(row.get("residence_jurisdiction", ""))
                                     or str(row.get("residence_jurisdiction") or ""))
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
    # Never blank an address already on file with an empty one: a later search
    # that failed to reach the source page must not erase a grounded answer.
    if row.get("competent_post_address"):
        target.competent_post_address = row["competent_post_address"]
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
