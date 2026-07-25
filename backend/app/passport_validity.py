"""Passport expiration validation (Phase 5).

Validates passport expiry immediately after OCR + applicant approval, against
the DESTINATION-SPECIFIC validity rule from the verified route rule — never a
generic six-month assumption. When expired: block submission, explain, provide
official renewal instructions for the ISSUING country, email them, and offer
"upload renewed passport and try again". Invalid extracted data is never kept
as approved application data (the rejected/expired document stays visible but
its expiry blocks progression until replaced).

Kimi may explain these official instructions in accessible language, but the
instructions and sources here are deterministic — never invented.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from . import models

# Official passport-renewal authorities by ISSUING country (ISO alpha-3 from
# the MRZ). Every entry is the official government authority. The fallback is
# honest: consult the issuing authority — Ellis never fabricates a source.
RENEWAL_AUTHORITIES = {
    "USA": {"authority": "U.S. Department of State — Bureau of Consular Affairs",
            "url": "https://travel.state.gov/content/travel/en/passports.html"},
    "CHN": {"authority": "国家移民管理局 (National Immigration Administration of China)",
            "url": "https://www.nia.gov.cn/"},
    "SGP": {"authority": "Immigration & Checkpoints Authority (ICA), Singapore",
            "url": "https://www.ica.gov.sg/documents/passport"},
    "GBR": {"authority": "HM Passport Office (GOV.UK)",
            "url": "https://www.gov.uk/renew-adult-passport"},
    "IND": {"authority": "Passport Seva, Ministry of External Affairs, India",
            "url": "https://www.passportindia.gov.in/"},
    "CAN": {"authority": "Immigration, Refugees and Citizenship Canada",
            "url": "https://www.canada.ca/en/services/canadian-passports.html"},
    "AUS": {"authority": "Australian Passport Office",
            "url": "https://www.passports.gov.au/"},
    "JPN": {"authority": "Ministry of Foreign Affairs of Japan — Passports",
            "url": "https://www.mofa.go.jp/toko/passport/"},
    "KOR": {"authority": "Ministry of Foreign Affairs, Republic of Korea — Passport Services",
            "url": "https://www.passport.go.kr/"},
    "VNM": {"authority": "Vietnam Immigration Department",
            "url": "https://xuatnhapcanh.gov.vn/"},
}

RULE_KINDS = ("valid_on_arrival", "valid_through_departure",
              "months_after_arrival", "months_after_departure")


class PassportBlocked(Exception):
    """Raised to hard-block a workflow transition when the passport is expired
    or has insufficient validity for the destination. Carries the verdict."""
    def __init__(self, verdict: dict):
        self.verdict = verdict
        super().__init__(verdict.get("explanation", "passport validity blocks this action"))


def parse_expiry(value: str) -> date | None:
    """Accept canonical ISO, MRZ YYMMDD (contextual century — an expiry is a
    plausible recent-past/future date, never a fixed pivot), or a printed
    date form (deterministic parse; ambiguity rejected)."""
    from . import dates as dates_mod
    iso = dates_mod.normalize_any(value, kind="expiry")
    try:
        return date.fromisoformat(iso) if iso else None
    except ValueError:
        return None


def _add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


def required_valid_until(rule: dict, arrival: date | None, departure: date | None) -> tuple[date | None, str]:
    """The date the passport must remain valid until, per the destination rule,
    plus a human explanation. None when travel dates are unknown."""
    kind = (rule or {}).get("kind", "")
    months = int((rule or {}).get("months", 0) or 0)
    if kind == "valid_on_arrival":
        return arrival, "valid on the day of arrival"
    if kind == "valid_through_departure":
        return departure, "valid through your departure date"
    if kind == "months_after_arrival" and arrival:
        return _add_months(arrival, months), f"valid for {months} months after arrival"
    if kind == "months_after_departure" and departure:
        return _add_months(departure, months), f"valid for {months} months after departure"
    return None, ""


def renewal_instructions(issuing_country: str) -> dict:
    entry = RENEWAL_AUTHORITIES.get((issuing_country or "").upper())
    if entry:
        return {"issuing_country": issuing_country.upper(), **entry,
                "note": ("Renew through the official authority above, then use "
                         "\"Upload renewed passport and try again\" in Ellis.")}
    return {"issuing_country": (issuing_country or "").upper(),
            "authority": "your passport-issuing authority",
            "url": "",
            "note": ("Ellis does not have the official renewal source for this issuing "
                     "country on file. Consult your passport-issuing authority directly, "
                     "then use \"Upload renewed passport and try again\" in Ellis.")}


def _document_expiry(db, app_row: models.VisaApplication) -> tuple[date | None, str]:
    """Fallback expiry from the case's accepted passport document (OCR/MRZ) so
    the verdict is CALCULATED whenever the date exists anywhere — never
    'unknown' with an extracted expiration on file. Returns (date, source)."""
    docs = db.query(models.StoredDocument).filter_by(
        application_id=app_row.id, doc_type="passport").all()
    for d in docs:
        if not (d.page_classification or {}).get("accepted_as_passport_identity", True):
            continue
        profile_field = ((d.passport_profile or {}).get("fields") or {}).get("expiry_date") or {}
        cand = parse_expiry(str(profile_field.get("value") or ""))
        if cand:
            return cand, "passport_document_mrz" if profile_field.get("source") == "mrz" \
                else "passport_document_ocr"
        raw = ((d.extracted_fields or {}).get("expiry_date") or {})
        cand = parse_expiry(str(raw.get("value") or ""))
        if cand:
            return cand, "passport_document_ocr"
    return None, ""


def _guidance_rule(db, app_row: models.VisaApplication) -> dict | None:
    """The Kimi two-pass structured passport-validity requirement saved with the
    case ({kind, months}) — used when no officially verified rule exists."""
    from .visa_snapshot.models import CaseRouteGuidance
    cg = db.query(CaseRouteGuidance).filter_by(case_id=app_row.id).first()
    if not cg:
        return None
    req = ((cg.guidance or {}).get("guidance") or {}).get("passport_validity_requirement")
    if isinstance(req, dict) and req.get("kind") in RULE_KINDS:
        return {"kind": req["kind"], "months": int(req.get("months") or 0)}
    return None


def check_case_passport(db, app_row: models.VisaApplication, *, today: date | None = None) -> dict:
    """The full validity verdict for a case. Expiry comes from the approved
    answers, falling back to the accepted passport document's extracted date
    (so a verdict is calculated whenever the date exists). The rule comes from
    the VERIFIED destination rule when one exists, else the Kimi two-pass
    structured requirement saved with the case — each labeled by source."""
    from . import rules as rules_mod
    today = today or date.today()
    answers = app_row.answers or {}
    expiry = parse_expiry(str(answers.get("expiry_date") or answers.get("passport_expiry")
                              or answers.get("passport_expiry_date") or ""))
    expiry_source = "applicant_confirmed" if expiry else ""
    if expiry is None:
        expiry, expiry_source = _document_expiry(db, app_row)
    issuing = str(answers.get("issuing_country") or answers.get("nationality") or "")
    arrival = _iso(answers.get("intended_arrival") or answers.get("arrival_date"))
    departure = _iso(answers.get("intended_departure") or answers.get("departure_date"))

    if expiry is None:
        return {"status": "unknown", "blocking": False,
                "explanation": "No passport expiry on file yet — upload and approve the passport biodata page."}

    from . import dates as dates_mod
    if expiry < today:
        return {"status": "expired", "blocking": True,
                "expiry_date": expiry.isoformat(), "expiry_source": expiry_source,
                "explanation": (f"This passport expired on {dates_mod.to_display(expiry.isoformat())}. "
                                "Processing cannot continue: no government portal accepts an "
                                "application on an expired passport, and any data extracted from "
                                "it cannot be used as approved application data."),
                "renewal": renewal_instructions(issuing),
                "renewal_recommended": True,
                "retry": "Upload the renewed passport and try again — your case is preserved."}

    # Destination-specific rule: the VERIFIED route rule when one exists,
    # otherwise the Kimi two-pass structured requirement saved with the case.
    rule, rule_source = None, ""
    verified = rules_mod.latest_rule(db, destination=app_row.destination_country,
                                     visa_type=app_row.visa_type,
                                     nationality=str(answers.get("passport_nationality", "") or ""),
                                     residence=str(answers.get("current_residence", "") or ""))
    if verified and verified.passport_validity_rule:
        rule, rule_source = verified.passport_validity_rule, "verified_route_rule"
    if rule is None:
        g_rule = _guidance_rule(db, app_row)
        if g_rule:
            rule, rule_source = g_rule, "kimi_two_pass_guidance"
    if rule:
        need_until, need_text = required_valid_until(rule, arrival, departure)
        if need_until and expiry < need_until:
            return {"status": "insufficient_validity", "blocking": True,
                    "expiry_date": expiry.isoformat(), "expiry_source": expiry_source,
                    "required_valid_until": need_until.isoformat(),
                    "rule": rule, "rule_source": rule_source,
                    "travel_dates": {"arrival": arrival.isoformat() if arrival else None,
                                     "departure": departure.isoformat() if departure else None},
                    "explanation": (f"{app_row.destination_country} requires a passport {need_text} — "
                                    f"until {dates_mod.to_display(need_until.isoformat())} for your "
                                    f"dates — but this passport expires "
                                    f"{dates_mod.to_display(expiry.isoformat())}."),
                    "renewal": renewal_instructions(issuing),
                    "renewal_recommended": True,
                    "retry": "Upload the renewed passport and try again — your case is preserved."}
        # The rule needs travel dates we don't have yet → we did NOT evaluate it.
        # Report honestly (do not claim the rule was satisfied).
        if need_until is None and rule.get("kind") in ("valid_on_arrival", "valid_through_departure",
                                                       "months_after_arrival", "months_after_departure"):
            return {"status": "ok_pending_travel_dates", "blocking": False,
                    "expiry_date": expiry.isoformat(), "expiry_source": expiry_source,
                    "rule": rule, "rule_source": rule_source,
                    "explanation": (f"The passport is not expired, but {app_row.destination_country}'s "
                                    "validity rule depends on your intended travel dates, which aren't "
                                    "provided yet — it will be checked once you supply them.")}
        min_pages = int(rule.get("min_blank_pages", 0) or 0)
        if min_pages:
            return {"status": "ok_with_conditions", "blocking": False,
                    "expiry_date": expiry.isoformat(), "expiry_source": expiry_source,
                    "rule_source": rule_source,
                    "conditions": [f"{app_row.destination_country} requires at least {min_pages} blank "
                                   "pages — please confirm your passport has them."]}
        return {"status": "ok", "blocking": False, "expiry_date": expiry.isoformat(),
                "expiry_source": expiry_source, "rule": rule, "rule_source": rule_source,
                "explanation": (f"Passport validity satisfies the "
                                f"{'verified' if rule_source == 'verified_route_rule' else 'Kimi-checked'} "
                                f"{app_row.destination_country} rule ({need_text or 'valid'}).")}
    return {"status": "ok_rule_unverified", "blocking": False,
            "expiry_date": expiry.isoformat(), "expiry_source": expiry_source,
            "explanation": (f"The passport is not expired, but {app_row.destination_country}'s exact "
                            "validity requirement has not been verified yet — it will be enforced "
                            "once the route rule is verified.")}


def _iso(v) -> date | None:
    try:
        return date.fromisoformat(str(v)) if v else None
    except ValueError:
        return None


def enforce_and_notify(db, app_row: models.VisaApplication, verdict: dict, *, locale: str = "en") -> None:
    """On a blocking verdict: email the renewal instructions (queued through the
    Phase 8 pipeline) and record the block. The renewal email is sent at most
    once per case (deduped on the 'passport_expired' event), so repeated blocked
    transitions don't spam the applicant."""
    from . import emails, audit
    if not verdict.get("blocking"):
        return
    already = [e for e in db.query(models.EmailNotification).filter_by(
        application_id=app_row.id, event="passport_expired").all()]
    if not already:
        renewal = verdict.get("renewal", {})
        detail = (verdict["explanation"] + " Renewal: " + renewal.get("authority", "") +
                  ((" — " + renewal["url"]) if renewal.get("url") else ""))
        emails.queue_case_email(db, app_row, event="passport_expired", locale=locale, detail=detail)
    audit.record(db, org_id=app_row.org_id, application_id=app_row.id,
                 action="passport_validity_block",
                 detail={"status": verdict["status"]})
