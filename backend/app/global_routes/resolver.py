"""Deterministic global route resolver.

resolve_route() maps ANY (nationality, issuing country, document type, lawful
residence, destination, transit flag) tuple to exactly ONE defined, honest
outcome record, assembled from the normalized layers:

  pair policy  ->  disposition/outcome + provenance      (nationality x dest)
  family reg.  ->  governing adapter / official channel  (shared platforms)
  jurisdiction ->  competent post for physical routes    (residence-dependent)
  snapshot     ->  passport-validity + verified fees     (destination-scoped)
  factory      ->  adapter release state                 (per portal family)

Rules the brief demands, enforced here and proven by tests:
  - nationality, issuing country, document type and residence are INDEPENDENT
    inputs; residence never changes the pair policy, only the jurisdiction;
  - non-ordinary travel documents never inherit the ordinary-passport policy —
    without document-specific research the outcome is honestly UNRESOLVED;
  - a physical route without a lawful-residence input resolves to
    REQUIRES_MANUAL_JURISDICTION_SELECTION rather than a guessed consulate;
  - visa-exempt / entry-preparation / on-arrival / physical routes require NO
    portal-submission adapter; electronic routes name their portal family;
  - no model call anywhere on this path.
"""
from __future__ import annotations

from sqlalchemy import select

from ..visa_snapshot import SNAPSHOT_DATE
from ..visa_snapshot.models import (ConsularJurisdictionRule,
                                    PassportValidityRuleRecord, VisaFeeVersion)
from ..visa_snapshot.registry import (RegistryError, load_registry,
                                      normalize_country, normalize_nationality)
from . import ELECTRONIC_OUTCOMES, NON_PORTAL_OUTCOMES
from .models import FamilyAdapterLink, PortalFamily, RoutePairPolicy, pair_key

ORDINARY = "ordinary_passport"

# Physical outcomes whose application jurisdiction depends on residence.
_JURISDICTION_OUTCOMES = ("EMBASSY_OR_CONSULATE_APPLICATION",
                          "AUTHORIZED_VISA_CENTER", "MAIL_APPLICATION",
                          "APPOINTMENT_REQUIRED")

# Applicant intervention points by outcome (always preserved; never automated).
_BASE_CONFIRMATIONS = {
    "VISA_EXEMPT": ["final_review_of_entry_preparation"],
    "ENTRY_PREPARATION": ["final_review_of_entry_preparation",
                          "arrival_form_submission_confirmation"],
    "ELECTRONIC_AUTHORIZATION": ["exact_payment_confirmation",
                                 "final_application_review",
                                 "final_submission_confirmation"],
    "EVISA": ["exact_payment_confirmation", "final_application_review",
              "final_submission_confirmation"],
    "VISA_ON_ARRIVAL": ["final_review_of_arrival_preparation",
                        "fee_payment_on_arrival"],
    "EMBASSY_OR_CONSULATE_APPLICATION": ["appointment_confirmation",
                                         "final_application_review",
                                         "personal_appearance"],
    "AUTHORIZED_VISA_CENTER": ["appointment_confirmation",
                               "final_application_review",
                               "personal_appearance"],
    "MAIL_APPLICATION": ["final_application_review",
                         "mailing_confirmation"],
    "APPOINTMENT_REQUIRED": ["appointment_confirmation"],
}


def _document_types() -> set[str]:
    return {d["code"] for d in load_registry("travel_document_types")["entries"]}


def normalize_inputs(*, nationality: str, destination: str,
                     issuing_country: str | None = None,
                     travel_document_type: str = ORDINARY,
                     residence: str | None = None) -> dict:
    """Registry normalization. Raises RegistryError on unknown values.
    Issuing country and residence are normalized INDEPENDENTLY of nationality —
    an absent issuer stays absent (recorded), it is never silently set to the
    nationality."""
    nat = normalize_nationality(nationality)
    dest = normalize_country(destination)
    doc = travel_document_type or ORDINARY
    if doc not in _document_types():
        raise RegistryError(f"unknown travel document type: {travel_document_type}")
    iss = normalize_country(issuing_country) if issuing_country else None
    res = normalize_country(residence) if residence else None
    return {"passport_nationality": nat, "destination_country": dest,
            "travel_document_type": doc, "passport_issuing_country": iss,
            "lawful_country_of_residence": res}


def _pair(db, nat: str, doc: str, dest: str) -> RoutePairPolicy | None:
    return db.execute(select(RoutePairPolicy).where(
        RoutePairPolicy.pair_key == pair_key(nat, doc, dest))).scalars().first()


def _family(db, family_id: str) -> PortalFamily | None:
    if not family_id:
        return None
    return db.execute(select(PortalFamily).where(
        PortalFamily.family_id == family_id)).scalars().first()


def _adapter_state(db, family_id: str) -> dict:
    """Honest adapter release state for the governing portal family."""
    if not family_id:
        return {"required": False, "family_id": "", "released": False,
                "status": "not_required",
                "reason": "route requires no portal-submission adapter"}
    link = db.execute(select(FamilyAdapterLink).where(
        FamilyAdapterLink.family_id == family_id)).scalars().first()
    if link is None:
        return {"required": True, "family_id": family_id, "released": False,
                "status": "not_built",
                "reason": "adapter for this portal family has not been built yet"}
    return {"required": True, "family_id": family_id,
            "released": bool(link.released), "status": link.status,
            "tier": link.release_tier,
            "reason": link.last_error or ("released" if link.released
                                          else f"build status: {link.status}")}


def _jurisdiction(db, dest: str, residence: str | None, nat: str,
                  subdivision: str | None = None) -> dict:
    """Residence-dependent competent-post resolution. Never guesses.

    A rule with residence_subdivisions is SUBDIVISION-SCOPED (e.g. KVAC
    Shanghai covers Shanghai/Jiangsu/Zhejiang/Anhui): it is served only when
    the applicant's stated subdivision is on its list. Without a subdivision,
    only a country-wide rule (empty subdivision list) may answer — a
    subdivision-scoped post is never guessed for the whole country."""
    if residence is None:
        return {"status": "residence_required",
                "detail": "lawful country of residence is required to determine "
                          "the competent application jurisdiction"}
    rules = db.execute(select(ConsularJurisdictionRule).where(
        ConsularJurisdictionRule.destination_country == dest,
        ConsularJurisdictionRule.residence_jurisdiction == residence,
        ConsularJurisdictionRule.verification_status == "verified")
        .order_by(ConsularJurisdictionRule.competent_post_name)).scalars().all()
    rules = [r for r in rules
             if not r.covers_nationalities or nat in r.covers_nationalities]
    if not rules:
        return {"status": "manual_selection_required",
                "detail": f"no verified consular jurisdiction rule for residence "
                          f"{residence} -> destination {dest}; the applicant must "
                          f"confirm the competent post"}
    countrywide = [r for r in rules if not (r.residence_subdivisions or [])]
    sub = (subdivision or "").strip().lower()
    if sub:
        scoped = [r for r in rules if any(
            str(s).strip().lower() == sub for s in (r.residence_subdivisions or []))]
        candidates = scoped or countrywide
    else:
        candidates = countrywide
    if not candidates:
        return {"status": "manual_selection_required",
                "detail": (f"the competent post for destination {dest} depends on "
                           f"the province/subdivision of residence within "
                           f"{residence}; the applicant must confirm the competent "
                           f"post"
                           + (f" — no verified rule covers subdivision "
                              f"{subdivision!r}" if sub else
                              " — provide the residence subdivision")),
                "options": [{"competent_post_name": r.competent_post_name,
                             "competent_post_kind": r.competent_post_kind,
                             "residence_subdivisions": r.residence_subdivisions or []}
                            for r in rules]}
    r = candidates[0]
    return {"status": "verified", "competent_post_name": r.competent_post_name,
            "competent_post_kind": r.competent_post_kind,
            "competent_post_url": r.competent_post_url,
            "residence_subdivisions": r.residence_subdivisions or [],
            "conditions": r.conditions or []}


def _passport_validity(db, dest: str) -> dict | None:
    rec = db.execute(select(PassportValidityRuleRecord).where(
        PassportValidityRuleRecord.destination_country == dest)).scalars().first()
    if rec is None:
        return None
    return {"rule_kind": rec.rule_kind, "months": rec.months,
            "min_blank_pages": rec.min_blank_pages,
            "condition_text": rec.condition_text,
            "research_status": rec.research_status}


def _verified_fee(db, dest: str, category: str) -> dict | None:
    if not category:
        return None
    rec = db.execute(select(VisaFeeVersion).where(
        VisaFeeVersion.destination_country == dest,
        VisaFeeVersion.visa_category == category,
        VisaFeeVersion.fee_status == "verified")
        .order_by(VisaFeeVersion.version.desc())).scalars().first()
    if rec is None:
        return None
    return {"fee_uid": rec.fee_uid, "version": rec.version,
            "record": rec.record, "fee_status": rec.fee_status}


def _confirmations(outcome: str, family: PortalFamily | None) -> list[str]:
    conf = list(_BASE_CONFIRMATIONS.get(outcome, []))
    if family is not None and family.account_required:
        conf.insert(0, "portal_account_credentials")
    if outcome in ELECTRONIC_OUTCOMES:
        # CAPTCHA/OTP are applicant handoffs whenever the portal presents them.
        conf.extend(["captcha_if_presented", "otp_if_presented"])
    return conf


def resolve_route(db, *, nationality: str, destination: str,
                  issuing_country: str | None = None,
                  travel_document_type: str = ORDINARY,
                  residence: str | None = None,
                  residence_subdivision: str | None = None,
                  transit: bool = False) -> dict:
    """Resolve one tuple to a full, honest route record. Deterministic:
    identical inputs always produce the identical record.
    residence_subdivision (province/state within the residence country) only
    ever narrows the competent post for physical routes — it never changes
    the pair policy."""
    n = normalize_inputs(nationality=nationality, destination=destination,
                         issuing_country=issuing_country,
                         travel_document_type=travel_document_type,
                         residence=residence)
    nat, dest, doc = (n["passport_nationality"], n["destination_country"],
                      n["travel_document_type"])
    res = n["lawful_country_of_residence"]
    res_sub = (residence_subdivision or "").strip() or None

    record: dict = {
        "snapshot_date": SNAPSHOT_DATE,
        "inputs": dict(n, residence_subdivision=res_sub,
                       transit_status="transit" if transit else "entry"),
        "route_outcome": "UNRESOLVED",
        "disposition": "RESEARCH_INCOMPLETE",
        "verification_status": "unresolved",
        "source": "none",
        "source_ref": "",
        "official_source_urls": [],
        "last_verified": "",
        "release_status": "unresolved",
        "release_reason": "",
        "max_stay_days": None,
        "governing_adapter": None,
        "official_channel": None,
        "jurisdiction": None,
        "applicant_confirmations": [],
        "passport_validity_rule": None,
        "verified_fee": None,
        "notes": [],
    }

    # 1) Non-ordinary documents never inherit ordinary-passport policy.
    if doc != ORDINARY:
        p = _pair(db, nat, doc, dest)
        if p is None:
            record["release_reason"] = (
                f"no researched policy for travel document '{doc}'; "
                f"document-specific official research is required — the "
                f"ordinary-passport rule is never assumed to apply")
            record["notes"].append("non_ordinary_document_unresolved")
            return record
    else:
        p = _pair(db, nat, ORDINARY, dest)

    if p is None:
        record["release_reason"] = ("no pair policy row exists for this "
                                    "combination — run the global build")
        return record

    outcome = p.route_outcome
    record.update({
        "route_outcome": outcome,
        "disposition": p.disposition,
        "verification_status": p.verification_status,
        "source": p.source,
        "source_ref": p.source_ref,
        "official_source_urls": p.official_source_urls or [],
        "last_verified": p.retrieved_at if p.verification_status == "verified" else "",
        "release_status": p.release_status,
        "release_reason": p.release_reason,
        "max_stay_days": p.max_stay_days,
    })

    if outcome in ("UNRESOLVED", "NO_AVAILABLE_TOURIST_ROUTE"):
        return record

    family = _family(db, p.portal_family_id)

    # 2) Physical routes: jurisdiction is residence-dependent, never guessed.
    if outcome in _JURISDICTION_OUTCOMES:
        j = _jurisdiction(db, dest, res, nat, res_sub)
        record["jurisdiction"] = j
        if j["status"] == "residence_required":
            record["route_outcome"] = "REQUIRES_MANUAL_JURISDICTION_SELECTION"
            record["notes"].append("residence_missing_for_physical_route")

    # 3) Governing adapter / channel (shared portal families).
    if outcome in ELECTRONIC_OUTCOMES:
        record["governing_adapter"] = _adapter_state(db, p.portal_family_id)
        if family is not None:
            record["official_channel"] = {
                "family_id": family.family_id, "name": family.name,
                "kind": family.kind, "url": family.base_url,
                "operator": family.operator, "operator_kind": family.operator_kind,
                "verification_status": family.verification_status,
                "account_required": family.account_required,
            }
        else:
            record["governing_adapter"] = {
                "required": True, "family_id": "", "released": False,
                "status": "no_official_portal_identified",
                "reason": "no real official portal family is registered for this "
                          "destination/outcome — the route stays electronic-"
                          "unsupported until one is verified (never invented)"}
            record["notes"].append("electronic_route_missing_portal_family")
    elif outcome in NON_PORTAL_OUTCOMES:
        record["governing_adapter"] = {
            "required": False, "family_id": "", "released": False,
            "status": "not_required",
            "reason": "non-portal workflow: no submission adapter exists or is needed"}
        if family is not None:  # e.g. arrival-card platform for ENTRY_PREPARATION
            record["official_channel"] = {
                "family_id": family.family_id, "name": family.name,
                "kind": family.kind, "url": family.base_url,
                "operator": family.operator, "operator_kind": family.operator_kind,
                "verification_status": family.verification_status,
                "account_required": family.account_required,
            }

    # 4) Applicant intervention points — always preserved.
    record["applicant_confirmations"] = _confirmations(record["route_outcome"], family)

    # 5) Destination-scoped verified data (honest None when absent).
    record["passport_validity_rule"] = _passport_validity(db, dest)
    record["verified_fee"] = _verified_fee(db, dest, p.primary_category)

    if transit:
        record["notes"].append(
            "transit_rules_not_separately_verified: transit privileges can "
            "differ from tourist entry; verify before relying on this outcome")

    return record
