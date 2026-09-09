"""Reject known-ineligible products using reviewed, explicit government lists.

This is a negative eligibility check, never evidence that a listed passport
qualifies in every circumstance. It does not replace source verification. A
newly expanded government list requires a reviewed catalog update before the
new nationality's product can be released; a model cannot override this gate.
"""
from dataclasses import dataclass
import re


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


PROGRAMS = (Program(
    id="aus_eta_601", destination="AUS",
    name="Australian Electronic Travel Authority (subclass 601)",
    eligible_nationalities=frozenset("AND AUT BEL BRN CAN DNK FIN FRA DEU GRC HKG ISL IRL ITA JPN "
        "LIE LUX MYS MLT MCO NOR PRT SMR SGP KOR ESP SWE CHE TWN NLD GBR USA VAT".split()),
    pattern=r"\beta\b|electronic\s+travel\s+authorit(?:y|ies)|\b(?:subclass\s*)?601\b",
    portal_marker="electronic-travel-authority-601",
    source_url="https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601",
    checked_at="2026-09-09",
),)


def _selected(program: Program, item: dict, *, headline: bool) -> bool:
    text = " ".join(str(item.get(k) or "") for k in ("type", "visa_category", "visa_label", "name"))
    if re.search(program.pattern, text, re.IGNORECASE):
        return True
    if program.portal_marker in str(item.get("official_portal_url") or "").lower():
        return True
    # Australia uses this specific program for an ETA. A generic ETA label
    # must not evade eligibility merely by omitting the subclass number.
    return program.id == "aus_eta_601" and (
        item.get("requirement_detail") == "eta_electronic_authorization"
        or (headline and item.get("disposition") == "ELECTRONIC_AUTHORIZATION_REQUIRED"))


def issues(guidance: dict, route: dict) -> list[str]:
    if not isinstance(guidance, dict) or not isinstance(route, dict):
        return []
    destination = str(route.get("destination_country") or route.get("destination") or "").upper()
    nationality = str(route.get("passport_nationality") or route.get("nationality") or "").upper()
    document = re.sub(r"[\s-]+", "_", str(
        route.get("travel_document_type") or "ordinary_passport").strip().lower())
    products = guidance.get("visa_products")
    products = products if isinstance(products, list) else []
    out = []
    for program in PROGRAMS:
        if program.destination != destination:
            continue
        if not (_selected(program, guidance, headline=True)
                or any(_selected(program, p, headline=False) for p in products if isinstance(p, dict))):
            continue
        reason = None
        if nationality not in program.eligible_nationalities:
            reason = f"passport nationality {nationality or 'unknown'} is absent from the official eligible list"
        elif program.id == "aus_eta_601" and document in {
                "identity_certificate", "certificate_of_identity", "document_of_identity",
                "refugee_travel_document", "stateless_travel_document", "prc_travel_document",
                "laissez_passer", "non_citizen_passport", "noncitizen_passport",
                "alien_passport", "emergency_travel_document"}:
            # The same official eligibility page explicitly excludes
            # non-citizen passports, certificates of identity and other
            # travel documents. This is not an exclusion of BN(O) passports:
            # they are expressly listed alongside British Citizen passports.
            # Temporary/emergency PASSPORT eligibility is not inferred here.
            reason = "the official ETA rules exclude non-citizen passports, certificates of identity and other non-passport travel documents"
        elif program.id == "aus_eta_601" and nationality == "TWN" and document in {
                "diplomatic", "diplomatic_passport", "official", "official_passport", "service", "service_passport"}:
            reason = "the official list excludes Taiwan official and diplomatic passports"
        if reason:
            out.append(f"product eligibility: {program.name}: {reason} (list checked {program.checked_at})")
    return out


def annotate(guidance, route):
    """Recompute a private hold reason after merging; never trust a stored tag."""
    if not isinstance(guidance, dict):
        return guidance
    problems = issues(guidance, route)
    if not problems and "_permission_eligibility_issues" not in guidance:
        return guidance
    out = dict(guidance)
    out.pop("_permission_eligibility_issues", None)
    if problems:
        out["_permission_eligibility_issues"] = problems
    return out
