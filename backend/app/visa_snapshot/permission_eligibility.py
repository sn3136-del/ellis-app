"""Reject known-ineligible products using reviewed, explicit government lists.

This is a negative eligibility check, never evidence that a listed passport
qualifies in every circumstance. It does not replace source verification. A
newly expanded government list requires a captured registry update before
the new nationality's product can be released; a model cannot override this
gate.

guard-20260912 T6: the loop is driven by scheme_registry (one entry per
list-based programme, each list read from its official page) instead of the
one-element PROGRAMS tuple. PROGRAMS stays as a view over the registry so no
caller breaks. Everything rides behind issues(), which serve_time_invariants
reads and which verified_overrides calls on every write and read, so serve
time and edit time inherit the guard with no new wiring. The rules:

  1. a product (or the headline answer) that resolves to an ESTABLISHED
     entry whose list does not name the nationality gets an issue naming
     the list, its check date and its URL; a document the entry excludes
     gets the entry's own refusal note
  2. no entry is ever selected by product NAME alone: resolution is the
     registry's (explicit program_id, portal marker, name pattern, then the
     requirement detail only where one programme of that family exists)
  3. an unconditional exemption contradicted by a published scheme: a
     VISA_EXEMPT verdict with unconditional_visa_free or no detail, for a
     nationality that IS on an established ETA, e-visa or visa-waiver list,
     is not unconditional; the answer must state the scheme or the
     exemption condition (the Malaysia to Russia shape, which does not
     depend on a product being present at all)
  4. a not_established or not_publicly_available list refuses NOTHING and
     releases NOTHING: it adds no issue and is reported through
     unstated_lists() instead, the fail-safe direction. The ids are NOT
     written into the served guidance: an extra key there changed the served
     payload of every route to a destination with a placeholder entry
     (India, the United States, Canada, the United Kingdom and others) and
     broke seven exact-payload tests, so the sweep reads unstated_lists()
     directly (defect fix after T6)
  5. the reviewed Indonesia to Korea branch STAYS beside the registry. The
     design allowed retiring it once the K-ETA list is established with
     Indonesia absent, behind a test proving an identical message set; that
     proof fails, because the branch also refuses a blanket unconditional
     exemption for an unlisted passport (the MOFA rule "if your country is
     not listed, you must apply for a visa"), a shape the registry's product
     rule does not express. A live guard is never removed to make room for
     a registry entry, and _keta_established_without_indonesia() records
     which state the registry is in.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from . import scheme_registry


@dataclass(frozen=True)
class Program:
    id: str
    destination: str
    name: str
    eligible_nationalities: frozenset[str]
    pattern: str
    portal_marker: str
    source_url: str
    checked_at: str


# The Australian ETA list as reviewed on 2026-09-09, kept as the fallback the
# registry view returns when scheme_lists.json cannot supply an established
# aus_eta_601. Rule 5 above applies to it as much as to Korea.
_LEGACY_AUS_ETA = Program(
    id="aus_eta_601", destination="AUS",
    name="Australian Electronic Travel Authority (subclass 601)",
    eligible_nationalities=frozenset("AND AUT BEL BRN CAN DNK FIN FRA DEU GRC HKG ISL IRL ITA JPN "
        "LIE LUX MYS MLT MCO NOR PRT SMR SGP KOR ESP SWE CHE TWN NLD GBR USA VAT".split()),
    pattern=r"\beta\b|electronic\s+travel\s+authorit(?:y|ies)|\b(?:subclass\s*)?601\b",
    portal_marker="electronic-travel-authority-601",
    source_url="https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601",
    checked_at="2026-09-09",
)

_AUS_ETA_EXCLUDED_DOCUMENTS = frozenset({
    "identity_certificate", "certificate_of_identity", "document_of_identity",
    "refugee_travel_document", "stateless_travel_document", "prc_travel_document",
    "laissez_passer", "non_citizen_passport", "noncitizen_passport",
    "alien_passport", "emergency_travel_document"})
_AUS_ETA_DOCUMENT_REASON = ("the official ETA rules exclude non-citizen passports, certificates of "
                            "identity and other non-passport travel documents")
_OFFICIAL_DOCUMENTS = frozenset({"diplomatic", "diplomatic_passport", "official", "official_passport",
                                 "service", "service_passport"})
_LIST_SCHEMES = ("eta", "evisa", "visa_waiver")


def _program_of(entry: dict) -> Program:
    return Program(id=entry["id"], destination=entry["destination"], name=entry["name"],
                   eligible_nationalities=frozenset(entry.get("eligible_nationalities") or ()),
                   pattern="|".join(entry.get("patterns") or ()),
                   portal_marker=str(entry.get("portal_marker") or ""),
                   source_url=str(entry.get("source_url") or ""),
                   checked_at=str(entry.get("checked_at") or ""))


class _ProgramView:
    """PROGRAMS as a live view over the registry's ESTABLISHED entries, the
    Australian ETA first so PROGRAMS[0] keeps its meaning, and the legacy
    Australian list standing in when the registry cannot establish it."""

    def _items(self) -> list[Program]:
        established = [e for e in scheme_registry.entries() if e.get("list_state") == "established"]
        out = [_program_of(e) for e in established]
        if not any(p.id == "aus_eta_601" for p in out):
            out.insert(0, _LEGACY_AUS_ETA)
        out.sort(key=lambda p: (p.id != "aus_eta_601", p.destination, p.id))
        return out

    def __getitem__(self, index):
        return self._items()[index]

    def __iter__(self):
        return iter(self._items())

    def __len__(self):
        return len(self._items())


PROGRAMS = _ProgramView()


def _entries_for(destination: str) -> list[dict]:
    """The registry's entries for a destination, with the legacy Australian
    ETA standing in as an established entry when the registry lacks one."""
    entries = list(scheme_registry.for_destination(destination))
    if destination == "AUS" and not any(e["id"] == "aus_eta_601" and e["list_state"] == "established"
                                        for e in entries):
        entries = [e for e in entries if e["id"] != "aus_eta_601"]
        entries.insert(0, {"id": _LEGACY_AUS_ETA.id, "destination": "AUS", "scheme_kind": "eta",
                           "name": _LEGACY_AUS_ETA.name, "list_state": "established",
                           "eligible_nationalities": sorted(_LEGACY_AUS_ETA.eligible_nationalities),
                           "excluded_documents": sorted(_AUS_ETA_EXCLUDED_DOCUMENTS),
                           "refusal_note": _AUS_ETA_DOCUMENT_REASON,
                           "requirement_details": ["eta_electronic_authorization"],
                           "patterns": [_LEGACY_AUS_ETA.pattern],
                           "portal_marker": _LEGACY_AUS_ETA.portal_marker,
                           "source_url": _LEGACY_AUS_ETA.source_url,
                           "checked_at": _LEGACY_AUS_ETA.checked_at, "legacy": True})
    return entries


def _selected(program, item: dict, *, headline: bool) -> bool:
    """Whether an item (a product or the headline answer) resolves to this
    programme through the registry's four resolvers. Kept for callers of the
    old private name; the resolution itself lives in scheme_registry.select."""
    pid = program["id"] if isinstance(program, dict) else program.id
    dest = program["destination"] if isinstance(program, dict) else program.destination
    hit = _select(item, dest, headline=headline)
    return bool(hit and hit["id"] == pid)


def _select(item: dict, destination: str, *, headline: bool) -> dict | None:
    entries = _entries_for(destination)
    if not entries:
        return None
    legacy = next((e for e in entries if e.get("legacy")), None)
    hit = scheme_registry.select(item, destination, headline=headline)
    if hit is not None or legacy is None:
        return hit
    # The legacy Australian entry is not in the registry file, so resolve it here
    # with the registry's own order: portal marker, pattern, then the detail.
    portal = str(item.get("official_portal_url") or "").lower()
    text = " ".join(str(item.get(k) or "") for k in ("type", "visa_category", "visa_label", "name"))
    if (str(item.get("program_id") or "") == legacy["id"]
            or (legacy["portal_marker"] and legacy["portal_marker"] in portal)
            or re.search(legacy["patterns"][0], text, re.I)
            or item.get("requirement_detail") == "eta_electronic_authorization"
            or (headline and not item.get("requirement_detail")
                and item.get("disposition") == "ELECTRONIC_AUTHORIZATION_REQUIRED")):
        return legacy
    return None


def _keta_established_without_indonesia() -> bool:
    keta = next((e for e in scheme_registry.for_destination("KOR") if e["id"] == "kor_keta"), None)
    return bool(keta and keta.get("list_state") == "established"
                and "IDN" not in set(keta.get("eligible_nationalities") or ()))


def _legacy_indonesia_korea(guidance: dict, route: dict, products: list, destination: str,
                            nationality: str, document: str) -> list[str]:
    # Indonesia is absent from the Ministry of Justice's K-ETA eligibility
    # list. Independent tourists use C-3-9; approved group/Jeju/transit
    # schemes are conditional exceptions, never unconditional 90-day entry.
    # Keep this negative check scoped to the reviewed ordinary-passport
    # tourism case; do not infer rules for diplomatic or other documents.
    if not (destination == "KOR" and nationality == "IDN" and
            document in {"ordinary_passport", "ordinary", "passport"} and
            str(route.get("travel_purpose") or "tourism").lower() == "tourism"):
        return []
    names = " ".join(str(guidance.get(k) or "") for k in ("visa_category", "visa_label", "name"))
    keta = (guidance.get("disposition") == "ELECTRONIC_AUTHORIZATION_REQUIRED" or
            guidance.get("requirement_detail") == "eta_electronic_authorization" or
            bool(re.search(r"\bk[ -]?eta\b", names, re.I)) or any(
                isinstance(p, dict) and (
                    p.get("requirement_detail") == "eta_electronic_authorization" or
                    bool(re.search(r"\bk[ -]?eta\b", str(p.get("type") or ""), re.I)))
                for p in products))
    unconditional = (guidance.get("disposition") == "VISA_EXEMPT" and
                     guidance.get("requirement_detail") not in {
                         "conditional_visa_free", "transit_visa_free"})
    if keta or unconditional:
        return ["product eligibility: Indonesian ordinary-passport independent tourism to Korea "
                "requires a visa; Indonesia is not K-ETA eligible. Jeju, transit and approved-group "
                "exemptions require their separate conditions (official list checked 2026-09-09: "
                "https://www.k-eta.go.kr/portal/guide/viewetaalification.do)"]
    return []


def _route_parts(route: dict) -> tuple[str, str, str]:
    destination = str(route.get("destination_country") or route.get("destination") or "").upper()
    nationality = str(route.get("passport_nationality") or route.get("nationality") or "").upper()
    document = re.sub(r"[\s-]+", "_", str(
        route.get("travel_document_type") or "ordinary_passport").strip().lower())
    return destination, nationality, document


def _evaluate(guidance: dict, route: dict) -> tuple[list[str], list[str]]:
    """(issues, unstated entry ids) for one answer."""
    destination, nationality, document = _route_parts(route)
    products = guidance.get("visa_products")
    products = [p for p in products if isinstance(p, dict)] if isinstance(products, list) else []
    out: list[str] = []
    unstated: list[str] = []
    # The reviewed Indonesia branch stays live in both registry states: it
    # carries the MOFA rule "if your country is not listed, you must apply
    # for a visa", which refuses a BLANKET exemption for an unlisted passport,
    # a shape the registry's product rule alone does not cover. Retiring it
    # would remove a live guard (rule 5 in the module docstring).
    out.extend(_legacy_indonesia_korea(guidance, route, products, destination, nationality, document))
    selected: list[dict] = []
    seen = set()
    for item, headline in [(guidance, True)] + [(p, False) for p in products]:
        entry = _select(item, destination, headline=headline)
        if entry is not None and entry["id"] not in seen:
            seen.add(entry["id"])
            selected.append(entry)
    for entry in selected:
        if entry.get("list_state") != "established":
            unstated.append(entry["id"])
            continue
        stamp = f"list checked {entry.get('checked_at') or 'unknown'}"
        if entry.get("source_url"):
            stamp += f": {entry['source_url']}"
        reason = None
        if nationality not in set(entry.get("eligible_nationalities") or ()):
            reason = f"passport nationality {nationality or 'unknown'} is absent from the official eligible list"
        elif document in set(entry.get("excluded_documents") or ()):
            reason = entry.get("refusal_note") or f"the official list excludes the {document} travel document"
        elif entry["id"] == "aus_eta_601" and nationality == "TWN" and document in _OFFICIAL_DOCUMENTS:
            # The same official page lists Taiwan "excluding official or
            # diplomatic passports".
            reason = "the official list excludes Taiwan official and diplomatic passports"
        if reason:
            out.append(f"product eligibility: {entry['name']}: {reason} ({stamp})")
    # Rule 3: an unconditional exemption over a published scheme the
    # nationality is on.
    disposition = str(guidance.get("disposition") or "").upper()
    detail = guidance.get("requirement_detail")
    if disposition == "VISA_EXEMPT" and detail in (None, "", "unconditional_visa_free"):
        for entry in _entries_for(destination):
            if entry.get("scheme_kind") not in _LIST_SCHEMES:
                continue
            if entry.get("list_state") != "established":
                if entry["id"] not in unstated and entry["id"] not in seen:
                    unstated.append(entry["id"])
                continue
            if nationality in set(entry.get("eligible_nationalities") or ()):
                out.append(f"{destination} operates {entry['name']} and {nationality} is on its list, "
                           "so entry is not unconditional. State the scheme or the exemption condition.")
    return list(dict.fromkeys(out)), unstated


def issues(guidance: dict, route: dict) -> list[str]:
    if not isinstance(guidance, dict) or not isinstance(route, dict):
        return []
    return _evaluate(guidance, route)[0]


def unstated_lists(guidance: dict, route: dict) -> list[str]:
    """The registry entries this answer touches whose list nobody could
    read: reported, never enforced."""
    if not isinstance(guidance, dict) or not isinstance(route, dict):
        return []
    return _evaluate(guidance, route)[1]


def annotate(guidance, route):
    """Recompute a private hold reason after merging; never trust a stored tag.
    Only the hold reason is written; the unstated list ids are reported by
    unstated_lists() and never ride in the served guidance (a stale key from
    an earlier build is removed)."""
    if not isinstance(guidance, dict):
        return guidance
    problems = _evaluate(guidance, route)[0] if isinstance(route, dict) else []
    if (not problems and "_permission_eligibility_issues" not in guidance
            and "scheme_list_unstated" not in guidance):
        return guidance
    out = dict(guidance)
    out.pop("_permission_eligibility_issues", None)
    out.pop("scheme_list_unstated", None)
    if problems:
        out["_permission_eligibility_issues"] = problems
    return out
