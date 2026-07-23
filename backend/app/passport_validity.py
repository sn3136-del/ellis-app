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


def parse_expiry(value: str) -> date | None:
    """Accept MRZ YYMMDD or ISO YYYY-MM-DD. MRZ century pivot: <70 → 20xx."""
    v = (value or "").strip()
    if not v:
        return None
    try:
        if len(v) == 10 and v[4] == "-":
            return date.fromisoformat(v)
        if len(v) == 6 and v.isdigit():
            yy, mm, dd = int(v[:2]), int(v[2:4]), int(v[4:6])
            year = 2000 + yy if yy < 70 else 1900 + yy
            return date(year, mm, dd)
    except ValueError:
        return None
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


def check_case_passport(db, app_row: models.VisaApplication, *, today: date | None = None) -> dict:
    """The full Phase 5 verdict for a case. Reads the approved passport expiry
    (answers) and the applicant's intended dates; applies the VERIFIED
    destination rule when one exists (never a generic default)."""
    from . import rules as rules_mod
    today = today or date.today()
    answers = app_row.answers or {}
    expiry = parse_expiry(str(answers.get("expiry_date") or answers.get("passport_expiry") or ""))
    issuing = str(answers.get("issuing_country") or answers.get("nationality") or "")
    arrival = _iso(answers.get("intended_arrival"))
    departure = _iso(answers.get("intended_departure"))

    if expiry is None:
        return {"status": "unknown", "blocking": False,
                "explanation": "No passport expiry on file yet — upload and approve the passport biodata page."}

    if expiry < today:
        return {"status": "expired", "blocking": True,
                "expiry_date": expiry.isoformat(),
                "explanation": (f"This passport expired on {expiry.isoformat()}. Processing cannot "
                                "continue: no government portal accepts an application on an expired "
                                "passport, and any data extracted from it cannot be used as approved "
                                "application data."),
                "renewal": renewal_instructions(issuing),
                "retry": "Upload the renewed passport and try again — your case is preserved."}

    # Destination-specific rule from the VERIFIED route rule (if one exists).
    rule = None
    verified = rules_mod.latest_rule(db, destination=app_row.destination_country,
                                     visa_type=app_row.visa_type,
                                     nationality=str(answers.get("passport_nationality", "") or ""),
                                     residence=str(answers.get("current_residence", "") or ""))
    if verified and verified.passport_validity_rule:
        rule = verified.passport_validity_rule
    if rule:
        need_until, need_text = required_valid_until(rule, arrival, departure)
        if need_until and expiry < need_until:
            return {"status": "insufficient_validity", "blocking": True,
                    "expiry_date": expiry.isoformat(),
                    "required_valid_until": need_until.isoformat(),
                    "rule": rule,
                    "travel_dates": {"arrival": arrival.isoformat() if arrival else None,
                                     "departure": departure.isoformat() if departure else None},
                    "explanation": (f"{app_row.destination_country} requires a passport {need_text} — "
                                    f"until {need_until.isoformat()} for your dates — but this passport "
                                    f"expires {expiry.isoformat()}."),
                    "renewal": renewal_instructions(issuing),
                    "retry": "Upload the renewed passport and try again — your case is preserved."}
        min_pages = int(rule.get("min_blank_pages", 0) or 0)
        if min_pages:
            return {"status": "ok_with_conditions", "blocking": False,
                    "expiry_date": expiry.isoformat(),
                    "conditions": [f"{app_row.destination_country} requires at least {min_pages} blank "
                                   "pages — please confirm your passport has them."]}
        return {"status": "ok", "blocking": False, "expiry_date": expiry.isoformat(),
                "explanation": f"Passport validity satisfies the verified {app_row.destination_country} rule."}
    return {"status": "ok_rule_unverified", "blocking": False,
            "expiry_date": expiry.isoformat(),
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
    Phase 8 pipeline) and record the block. Idempotent per (case, status)."""
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
