"""The T-Station 25-field record — Trip.com's own data dictionary, exactly.

Trip.com's requirements specification defines a 25-field record (6 core,
10 visa-detail, 4 additional, 5 data-source) with exact English field names
and enumerations. Their acceptance standard measures field completeness and
accuracy against THIS shape, per "passport type × destination" unit, and the
quality-control backend, the Excel export and the sampling checklists must
all speak it. This module converts Ellis's internal guidance into that
record, one record per visa product (or one for the route when no products
exist), without inventing a value it does not hold: a field Ellis cannot
fill stays None and shows as "missing" in the checklist — the honest state.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone

# Their field order, exactly as numbered 1-25 in the requirements document.
FIELD_ORDER = (
    "travel_document_type", "travel_document_country", "destination_country",
    "travel_purpose", "visa_requirement", "visa_requirement_detail",
    "visa_type_name",
    "validity_duration", "validity_unit", "max_stay_duration", "max_stay_unit",
    "entries", "processing_min_days", "processing_unit",
    "visa_fee_amount", "visa_fee_currency", "application_method",
    "required_documents", "consulate_district", "entry_requirements",
    "special_conditions",
    "data_source", "source_url", "collected_at", "info_validity",
    "confidence_level",
)

# Requirements dictionary items 1–25. The separately exported subcategory
# belongs to item 5; it is not an additional contractual completeness field.
CONTRACT_FIELDS = tuple(f for f in FIELD_ORDER if f != "visa_requirement_detail")


def acceptance_summary(rows: list[dict]) -> dict:
    """Literal acceptance measurements, separate from fillable completeness.

    The requirements dictionary marks five items "provide if available",
    while acceptance §4.2.2 explicitly measures all 25 as non-null. Expose
    both denominators; never manufacture a value or treat a pending field
    as approved to reconcile that difference.
    """
    def present(v):
        return v is not None and v != [] and v != {} and (
            not isinstance(v, str) or bool(v.strip()))

    def rate(n, d):
        return n / d if d else None

    total = len(rows)
    filled = approved = complete = approved_complete = pending = 0
    documented_cells = documented_records = disposition_cells = 0
    for row in rows:
        disputed = set(row.get("_disputed") or ())
        statuses = field_status(row)
        count = sum(present(row.get(f)) for f in CONTRACT_FIELDS)
        reviewed = sum(present(row.get(f)) and f not in disputed for f in CONTRACT_FIELDS)
        pending += sum(present(row.get(f)) and f in disputed for f in CONTRACT_FIELDS)
        filled += count
        approved += reviewed
        complete += count == len(CONTRACT_FIELDS)
        # Unchallenged content is not an accuracy certificate: this metric
        # only indicates the absence of outstanding field disputes.
        approved_complete += reviewed == len(CONTRACT_FIELDS)
        # The user-approved completion definition accepts recorded N/A and
        # Not published dispositions. Unknown/optional-empty and pending
        # fields remain gaps; the literal non-null diagnostic stays separate.
        documented = sum(f not in disputed and (present(row.get(f)) or
            statuses.get(f) in {"not-applicable", "not-published"}) for f in CONTRACT_FIELDS)
        disposition_cells += sum(f not in disputed and not present(row.get(f)) and
            statuses.get(f) in {"not-applicable", "not-published"} for f in CONTRACT_FIELDS)
        documented_cells += documented
        documented_records += documented == len(CONTRACT_FIELDS)
    return {
        "field_names": list(CONTRACT_FIELDS),
        "field_count": len(CONTRACT_FIELDS),
        "dictionary_required_field_count": len(REQUIRED_FIELDS),
        "record_count": total,
        "filled_cells": filled,
        "required_cells": total * len(CONTRACT_FIELDS),
        "field_completeness_rate": rate(filled, total * len(CONTRACT_FIELDS)),
        "complete_records": complete,
        "record_completeness_rate": rate(complete, total),
        "unchallenged_filled_cells": approved,
        "unchallenged_complete_records": approved_complete,
        "pending_review_cells": pending,
        "documented_completed_cells": documented_cells,
        "documented_disposition_cells": disposition_cells,
        "documented_complete_records": documented_records,
        "documented_field_completeness_rate": rate(documented_cells, total * len(CONTRACT_FIELDS)),
        "documented_record_completeness_rate": rate(documented_records, total),
        "documented_completion_policy": "All 25 dictionary fields. Recorded Not applicable and Not published "
            "count as complete; missing, optional-empty and pending review do not. Completion does not certify accuracy.",
        "source_url_presence_rate": rate(sum(present(r.get("source_url")) for r in rows), total),
        "requirement_support_rate": rate(sum(r.get("_source_check") in
            {"human-quote", "ai-quote", "grounded-consistent"} for r in rows), total),
        "accuracy_certified": False,
        "denominator_policy": "All 25 dictionary fields; no blank-field exclusions. Subcategory is part of field 5.",
    }

# 6 core + the detail/source fields their completeness metric counts as
# required. "Provide if available" fields (12, 13, 18, 19, 20) are excluded
# from the completeness denominator, per the spec's own Required column.
REQUIRED_FIELDS = frozenset({
    "travel_document_type", "travel_document_country", "destination_country",
    "travel_purpose", "visa_requirement", "visa_type_name",
    "validity_duration", "validity_unit", "max_stay_duration", "max_stay_unit",
    "entries", "visa_fee_amount", "visa_fee_currency", "application_method",
    "required_documents",
    "data_source", "source_url", "collected_at", "info_validity",
    "confidence_level",
})

FIELD_DESCRIPTIONS = {
    "travel_document_type": "Type of travel document held",
    "travel_document_country": "Document issuing country (applicant nationality)",
    "destination_country": "Visa destination",
    "travel_purpose": "User's travel purpose",
    "visa_requirement": "Visa-free / Visa on Arrival / Visa Required in Advance",
    "visa_requirement_detail": "Field 5's subcategory: Unconditional / Conditional / "
                               "Transit Visa-free, eVisa or Paper Visa on Arrival, "
                               "eVisa / Paper Visa / ETA Electronic Authorization",
    "visa_type_name": "Visa type name for this purpose and destination",
    "validity_duration": "How long the visa is valid after approval (number)",
    "validity_unit": "Day / Month / Year / Long-term Valid",
    "max_stay_duration": "Maximum length of stay per entry (number)",
    "max_stay_unit": "Hour / Day",
    "entries": "Single / Multiple / Unlimited",
    "processing_min_days": "Shortest time from submission to visa issuance",
    "processing_unit": "Working Day / Calendar Day",
    "visa_fee_amount": "Official visa fee amount (consular fee only)",
    "visa_fee_currency": "ISO 4217 currency code",
    "application_method": "Embassy Submission / Online Application / Agency "
                          "Service / On-arrival Processing / Government "
                          "Office Submission",
    "required_documents": "Core document checklist, comma separated",
    "consulate_district": "Consulate district divisions, if any",
    "entry_requirements": "Other entry requirements besides the visa",
    "special_conditions": "Special policies or restrictions",
    "data_source": "Information source website / organization",
    "source_url": "Specific page link",
    "collected_at": "Data collection date",
    "info_validity": "Published policy validity end date, when known. "
                     "Blank when no policy end date is available; the separate "
                     "freshness_valid_until metadata is an internal recheck deadline, "
                     "not the policy's expiry date",
    "confidence_level": "High / Low",
}

_DISPOSITION_TO_REQUIREMENT = {
    "VISA_EXEMPT": "Visa-free",
    "VISA_ON_ARRIVAL": "Visa on Arrival",
    # An ESTA, an eTA or a K-ETA is not a visa: the traveller is visa-exempt
    # and files an authorisation instead. Calling that "Visa Required in
    # Advance" tells a Japanese tourist the United States needs a visa, which
    # is false; calling it "Visa-free" invites them to skip the filing and be
    # denied boarding. Conditional is the only honest cell, and the detail
    # fields carry what the condition is.
    "ELECTRONIC_AUTHORIZATION_REQUIRED": "Conditional",
    "VISA_REQUIRED": "Visa Required in Advance",
    "CONDITIONAL": "Conditional",
    "NOT_ADMITTED": "Not admitted",
}

def _files_something_online(g: dict) -> bool:
    """Use explicit requirements, not mentions of optional/exempt filings."""
    if g.get("requirement_detail") == "eta_electronic_authorization":
        return True
    ac = g.get("arrival_card")
    if isinstance(ac, dict) and ac.get("required"):
        # A card that can be handed over on arrival need not be filed online.
        # Merely providing a portal URL does not make an optional service
        # mandatory (for example, Japan's paper ED card vs Visit Japan Web).
        when = str(ac.get("submission_window") or "").lower()
        return (g.get("application_channel") == "online_portal" and
                not re.search(r"\bon arrival\b|\bat (?:the )?arrival\b", when))
    return False


def _method_from_product(g: dict) -> str | None:
    """Last resort: read the channel off the visa product's own name. Only
    unambiguous words count, so a route stays "Other" rather than guessing."""
    products = g.get("visa_products")
    names = " ".join(str(p.get("type") or "")
                     for p in (products if isinstance(products, list) else [])
                     if isinstance(p, dict))
    names = f"{names} {g.get('visa_category') or ''}".lower()
    if not names.strip():
        return None
    if any(k in names for k in ("e-visa", "evisa", "electronic travel",
                                "eta-", "e-ta", "esta", "travel authoris",
                                "travel authoriz", "online")):
        return "Online Application"
    if any(k in names for k in ("on arrival", "on-arrival", "voa")):
        return "On-arrival Processing"
    if any(k in names for k in ("consular sticker", "consulate", "embassy")):
        return "Embassy Submission"
    return None


# Channel values that state there is nothing to apply for. They are not a
# place, so they never become a method.
_NO_CHANNEL = frozenset({
    "not_required", "none", "n/a", "na", "not_applicable",
    "no_application_required", "none_or_port_of_entry",
})
_AGENCY_RE = re.compile(
    r"china travel service|authori[sz]ed (?:visa |travel )?agent|appointed agent|designated agent"
    r"|accredited agent|travel agency|visa application cent(?:re|er)|visa cent(?:re|er)"
    r"|\bvfs\b|bls international|tls ?contact|through an agent", re.I)
_GOVERNMENT_OFFICE_RE = re.compile(
    r"exit[- ]entry|exit and entry|public security|immigration (?:office|department"
    r"|bureau)\b|service hall|police station", re.I)
_MISSION_RE = re.compile(
    r"embassy|consulate|consular|diplomatic (?:mission|or consular)"
    r"|at (?:the|a) mission", re.I)
_ONLINE_RE = re.compile(
    r"online|portal|web ?site|\besta\b|e-?visa|electronic", re.I)
_BORDER_RE = re.compile(
    r"on arrival|upon arrival|at the border|port of entry|at the airport", re.I)


def _method_from_detail(g: dict) -> str | None:
    """Where to apply, read off the route's own description of its channel.

    Overrides write "in_person" and "not_required" as the channel, which say
    whether the traveller goes somewhere but not where. The sentence beside
    it does: a public security exit-entry office, China Travel Service, a
    Montenegrin mission, the CBP ESTA site. Thirty-nine live records read
    "Other" because only the token was consulted."""
    text = " ".join(str(g.get(k) or "") for k in (
        "application_channel_detail", "submission_process"))
    if not text.strip():
        return None
    if _AGENCY_RE.search(text):
        return "Agency Service"
    if _GOVERNMENT_OFFICE_RE.search(text):
        return "Government Office Submission"
    if _MISSION_RE.search(text):
        return "Embassy Submission"
    if _ONLINE_RE.search(text):
        return "Online Application"
    if _BORDER_RE.search(text):
        return "On-arrival Processing"
    return None


_VISA_FREE_DETAILS = frozenset({
    "Unconditional Visa-free", "Conditional Visa-free", "Transit Visa-free",
})
_IN_PERSON_METHODS = frozenset({
    "Embassy Submission", "Agency Service", "Government Office Submission",
})
_NAME_AGENCY_RE = re.compile(r"agenc|agent|china travel service|\bvfs\b", re.I)
_NAME_VISA_RE = re.compile(r"(?<!no )\bvisa\b(?![- ]free)", re.I)
_NAME_MISSION_RE = re.compile(r"embassy|consulate|consular", re.I)
_NAME_BORDER_RE = re.compile(r"on[- ]arrival", re.I)
_NAME_ONLINE_RE = re.compile(
    r"e-?visa|online|electronic|\besta\b|\be-?ta\b|travel authori[sz]", re.I)


def _method_for_detail(detail: str | None, route_method: str | None,
                       row: dict, from_channel: bool) -> str | None:
    """The channel this row's own kind of permission implies.

    A route can offer an ETA and a consular sticker side by side, and one
    route-level method labels one of them wrongly. Precedence: the product's
    stated channel when the source gave one, then the product's own words
    ("consular sticker", "visa on arrival", "e-Visa") when they say one
    thing, then the kind the subcategory implies. A conditional exemption has nothing to apply for.
    "Other" is never an answer: it told a traveller nothing and it is not
    what any official page says."""
    d = str(detail or "")
    name = str(row.get("visa_type_name") or "")
    if d in _VISA_FREE_DETAILS:
        if from_channel and route_method in _IN_PERSON_METHODS and _NAME_VISA_RE.search(name):
            # A product-less conditional route whose row itself names a
            # visa ("Schengen short-stay (C) visa through the French or
            # Spanish visa centre") keeps the channel its source stated.
            return route_method
        return ("Online Application"
                if route_method == "Online Application"
                and _names_something_to_file(row) else None)
    if from_channel and route_method:
        # A channel the source stated outright outranks every reading below:
        # Japan's eVisa is lodged by a designated agency, and a consular
        # sticker for Israel goes through a visa centre. The product's name
        # describes the permission, not always where it is lodged.
        return route_method
    name = str(row.get("visa_type_name") or "")
    said = [m for m, rx in (("Agency Service", _NAME_AGENCY_RE),
                            ("Embassy Submission", _NAME_MISSION_RE),
                            ("On-arrival Processing", _NAME_BORDER_RE),
                            ("Online Application", _NAME_ONLINE_RE))
            if rx.search(name)]
    if len(said) == 1:
        return said[0]
    if d in ("eVisa", "ETA Electronic Authorization", "eVisa on Arrival"):
        return "Online Application"
    if d == "Paper Visa on Arrival":
        return "On-arrival Processing"
    if d == "Paper Visa":
        if route_method in _IN_PERSON_METHODS:
            return route_method
        # A paper permission does not establish where it must be lodged
        # (mission, agent, visa centre or government office). A verdict-only
        # correction must leave this unknown until its method is sourced.
        return None
    return route_method


def _method_for_channel(channel: str) -> str | None:
    """The engine's channel vocabulary (lowercase, many variants) mapped to
    their five application_method values. Substring rules, because a live
    audit found 96% of records collapsing to "Other" when this was an exact
    uppercase table."""
    c = str(channel or "").lower().strip()
    if not c or c in _NO_CHANNEL:
        return None
    if "arrival" in c:
        return "On-arrival Processing"
    if any(k in c for k in ("online", "portal", "evisa", "e-visa", "eta",
                            "electronic")):
        return "Online Application"
    if any(k in c for k in ("agency", "agent", "centre", "center", "vfs",
                            "bls", "tls")):
        return "Agency Service"
    if any(k in c for k in ("embassy", "consulate", "consular", "mission")):
        return "Embassy Submission"
    if any(k in c for k in ("office", "bureau", "public_security",
                            "exit_entry", "exit-entry")):
        return "Government Office Submission"
    # "in_person" and any value outside the vocabulary say nothing about
    # WHERE. The channel sentence and the product decide, never "Other".
    return None


_DISCRETIONARY = ("set by the consulate", "consulate discretion", "as granted",
                  "determined by the consular", "determined at issuance",
                  "not published", "trip duration", "trip dates",
                  "aligned to the itinerary", "per the itinerary",
                  # A visa issued at the border has no pre-arrival validity
                  # window: the permit begins when it is granted, so its
                  # validity is the stay it grants.
                  "on arrival", "upon arrival", "at arrival")


def _num_unit(text, stay_bound=None) -> tuple[float | None, str | None]:
    """'90 days' -> (90, 'Day'); '5 years' -> (5, 'Year'); '6 months' ->
    (6, 'Month'). A DISCRETIONARY validity ("set by the consulate", "as
    granted") maps to the product's stay length as the upper bound when one
    is known — Trip.com's own display standard writes these as "Up to 90
    days (determined at issuance)". Otherwise a range or prose is not
    silently collapsed to a guess."""
    t = str(text or "").strip().lower()
    if not t:
        return None, None
    if any(k in t for k in _DISCRETIONARY):
        if stay_bound:
            return int(stay_bound), "Day"
        return None, None
    if "long-term" in t or "long term" in t or "permanent" in t:
        return 0, "Long-term Valid"
    # Sources write "Six (6) months" and "One (1) to three (3) months"; the
    # parentheses sit between the digit and its unit. Dropping them lets the
    # search land on the number bound to the unit — for a spelled-out range
    # that is the upper figure, the one written beside the unit word.
    t = t.replace("(", " ").replace(")", " ")
    # And they spell numbers out: "Three months from issue", "not exceeding
    # five years". Reading a written-out number is reading, not guessing.
    for word, digit in (("one", "1"), ("two", "2"), ("three", "3"),
                        ("four", "4"), ("five", "5"), ("six", "6"),
                        ("seven", "7"), ("eight", "8"), ("nine", "9"),
                        ("ten", "10"), ("eleven", "11"), ("twelve", "12")):
        t = re.sub(rf"\b{word}\b", digit, t)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:working\s+|business\s+|calendar\s+)?"
                  r"(hour|day|month|year|week)s?", t)
    if not m:
        return None, None
    n = float(m.group(1))
    unit = {"hour": "Hour", "day": "Day", "month": "Month",
            "year": "Year", "week": "Day"}[m.group(2)]
    if m.group(2) == "week":
        n *= 7
    return (int(n) if n == int(n) else n), unit


def _as_stay_unit(n, unit):
    """Calendar months/years cannot be converted to exact days without dates.

    The external enum only permits Hour/Day. Keep a calendar stay in the
    accompanying text instead of manufacturing a numerical duration.
    """
    if n is None or unit in (None, "Hour", "Day"):
        return n, unit
    return None, None


def _validity_num_unit(text) -> tuple[float | None, str | None]:
    """Read one explicitly stated validity measure, never a stay or a grant option.

    The shared stay parser deliberately has broader semantics. A validity
    column cannot select one member of a range, a consular choice or a rolling
    stay window, even when its product name happens to contain the same number.
    """
    if not isinstance(text, str) or not text.strip():
        return None, None
    t = text.strip().lower()
    # A field may contain copied stay/processing prose despite its name.
    # Its number remains that other fact, not a visa entry window.
    if re.search(r"\b(?:stay|stays|staying|processing|turnaround)\b", t):
        return None, None
    if any(k in t for k in _DISCRETIONARY) or re.search(
            r"\b(?:discretion\w*|determin\w*|decid\w*|depend\w*|var(?:y|ies|iable)|"
            r"may|might|usually|normally|generally|whichever)\b|"
            r"\b(?:as issued|subject to|case[- ]by[- ]case|until (?:the )?passport)\b", t):
        return None, None
    if re.fullmatch(r"(?:permanent|long[- ]term(?: valid(?:ity)?)?)", t):
        return 0, "Long-term Valid"
    numbers = {word: str(index) for index, word in enumerate(
        ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"), 1)}
    # Collapse only matching duplicated notation: Six (6), not Six (7).
    for word, digit in numbers.items():
        t = re.sub(rf"\b{word}\s*\(\s*{digit}\s*\)", digit, t)
        t = re.sub(rf"\b{word}\b", digit, t)
    # Exactly one number must own exactly one unit. This rejects 1/3/5 years,
    # 1–3 years, 1 year or 3 years, mixed units and 90 days in any 180 days.
    if len(re.findall(r"\d+(?:\.\d+)?", t)) != 1:
        return None, None
    if re.search(r"\b(?:or|alternatively|working|business)\b|[或至]", t):
        return None, None
    if re.search(r"\b(?:not|never)\b(?!\s+(?:exceeding|more than|longer than)\b)|"
                 r"\b(?:at least|minimum|unknown)\b", t):
        return None, None
    match = re.search(r"(?<![\d.\-–—−])(\d+(?:\.\d+)?)\s*(?:calendar\s+)?"
                      r"(hour|day|month|year|week)s?\b", t)
    if not match or float(match.group(1)) <= 0:
        return None, None
    n = float(match.group(1))
    unit = {"hour": "Hour", "day": "Day", "month": "Month",
            "year": "Year", "week": "Day"}[match.group(2)]
    if match.group(2) == "week":
        n *= 7
    return (int(n) if n == int(n) else n), unit


_CALENDAR_MEASURE = re.compile(
    r"\b(?:\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
    r"\s*(?:\(\d+\)\s*)?(?:calendar\s+)?(?:months?|years?)\b", re.I)


def _day_stay_with_separate_calendar_context(raw: str, days=None):
    """Read an explicit initial day stay, never convert calendar duration.

    A later visa-validity, extension or rolling-window measure is a different
    fact. Unknown/alternative stay scopes still leave the numeric field blank.
    """
    first = re.match(
        r"^\s*(?:(?:up to|not exceeding|a maximum of|maximum(?: stay)?(?: of)?|"
        r"visitor['’]s visa issued on arrival for)\s+)?(\d+)\s*(?:calendar\s+)?days?\b",
        raw, re.I)
    if not first:
        return None
    value = int(first.group(1))
    if days is not None and (isinstance(days, bool) or days != value):
        return None  # a numeric sibling cannot silently resolve conflicting text
    tail = raw[first.end():]
    if re.search(r"\bor\b.*?\b\d+\s*(?:calendar\s+)?(?:days?|months?|years?)\b", tail, re.I):
        return None  # multiple stay alternatives need product-specific wording
    measures = list(_CALENDAR_MEASURE.finditer(raw))
    if not measures:
        return None
    for measure in measures:
        prefix = raw[:measure.start()]
        suffix = raw[measure.end():]
        separate_role = re.search(
            r"\b(?:valid(?:ity)?(?:\s+(?:for|of|is))?(?:\s+up\s+to)?|"
            r"extend(?:able|ed|ible)(?:\s+(?:up\s+to|for|to))?|"
            r"extension(?:\s+(?:up\s+to|for|of|to))?|"
            r"within|during|over|in any|in a)\s*$", prefix, re.I)
        # When the source supplies both '120 days (4 months)', keep its
        # explicit day figure, without calculating one from the month figure.
        parenthetical = prefix.rstrip().endswith('(') and suffix.lstrip().startswith(')')
        if not separate_role and not parenthetical:
            return None
    return value


def _set_stay(row: dict, text, days=None) -> None:
    raw = str(text or "").strip()
    n, unit = _num_unit(raw)
    # Even discretionary wording must not fall back to a cached 180-day
    # approximation when the source gives a calendar-month stay.
    calendar = unit in ("Month", "Year") or bool(_CALENDAR_MEASURE.search(raw))
    if raw:
        row["max_stay_text"] = raw
    scoped_days = _day_stay_with_separate_calendar_context(raw, days) if calendar else None
    if scoped_days is not None:
        row["max_stay_duration"], row["max_stay_unit"] = scoped_days, "Day"
        row.pop("_max_stay_representation_reason", None)
        return
    if calendar:
        row["max_stay_duration"], row["max_stay_unit"] = None, None
        row["_max_stay_representation_reason"] = (
            "The source uses calendar months or years; the Hour/Day numeric contract cannot express it exactly.")
        clause = f"Permitted stay: {raw}"
        prior = str(row.get("special_conditions") or "")
        if raw not in prior:
            row["special_conditions"] = f"{prior}. {clause}".lstrip(". ")
        return
    if days is not None:
        n, unit = days, "Day"
    row["max_stay_duration"], row["max_stay_unit"] = _as_stay_unit(n, unit)


def _as_validity_unit(n, unit):
    """Only convert hours when the allowed Day unit expresses them exactly."""
    if unit == "Hour":
        if n is not None and n % 24 == 0:
            return n // 24, "Day"
        return None, None
    return n, unit


def _set_validity(row: dict, n, unit, text=None) -> None:
    row["validity_duration"], row["validity_unit"] = _as_validity_unit(n, unit)
    raw = str(text or "").strip()
    if n is None and raw:
        row["validity_text"] = raw
        row["_validity_representation_reason"] = (
            "The stated validity is not one unambiguous duration; its exact wording is retained.")
    if unit != "Hour" or row.get("visa_requirement") == "Visa-free":
        return
    raw = raw or f"{n} hours"
    row["validity_text"] = raw
    if row["validity_duration"] is None:
        row["_validity_representation_reason"] = (
            "The source uses hours that are not a whole number of days; the Day/Month/Year numeric contract cannot express it exactly.")
        prior = str(row.get("special_conditions") or "")
        if raw not in prior:
            row["special_conditions"] = f"{prior}. Validity: {raw}".lstrip(". ")


def _entries(text) -> str | None:
    t = str(text or "").lower()
    if "single" in t or t == "1":
        return "Single"
    if "unlimit" in t:
        return "Unlimited"
    if "multiple" in t or "double" in t or "two" in t:
        return "Multiple"
    return None


def _fee_qualifier(product: dict, guidance: dict) -> str | None:
    fee = product.get("fee") if isinstance(product.get("fee"), dict) else None
    if not fee:
        fee = guidance.get("government_fee")
    return "from" if isinstance(fee, dict) and fee.get("qualifier") == "from" else None


def _fee(product: dict, guidance: dict) -> tuple[float | None, str | None]:
    fee = product.get("fee") if isinstance(product.get("fee"), dict) else None
    if not fee:
        g = guidance.get("government_fee")
        fee = g if isinstance(g, dict) else None
    if not fee:
        return None, None
    amount = fee.get("amount")
    currency = fee.get("currency")
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return None, str(currency) if currency else None
    if not math.isfinite(amount) or amount < 0:
        return None, str(currency) if currency else None
    if amount == int(amount):
        amount = int(amount)
    if amount == 0:
        # A zero consular fee on a visa that must be applied for is almost
        # always a hallucinated "free": the acceptance audit found sources
        # charging 60-90 EUR where 0 was stored. Zero survives only when the
        # answer itself says the fee is waived; otherwise the fee is honestly
        # missing (and the completeness campaign researches it).
        disposition = str(guidance.get("disposition") or "").upper()
        if disposition not in ("VISA_EXEMPT", ""):
            texts = " ".join(str(x or "") for x in (
                product.get("notes"), fee.get("note"), fee.get("notes"),
                guidance.get("requirement_detail"),
                guidance.get("application_channel_detail"))).lower()
            if not any(k in texts for k in ("free", "gratis", "no fee", "fee waiver",
                                            "no visa fee", "no visa application fee",
                                            "waived", "exempt", "nil",
                                            "zero-fee", "免费", "免簽費",
                                            "免签费", "免收")):
                return None, str(currency) if currency else None
        # A genuinely zero fee has no meaningful currency; the exempt branch
        # already writes "0 USD", so a proven-free product does the same
        # rather than leaving the currency cell counting as a gap.
        if not currency:
            currency = "USD"
    return amount, str(currency) if currency else None


def verdict_provenance_supported(provenance: dict | None) -> bool:
    """Validate recorded verdict evidence, without inventing its authorship.

    Manual note semantics remain the operator's checked assertion; this
    structural gate does not reinterpret multilingual source notes.
    """
    from .authority import hostname, is_government_host
    if not isinstance(provenance, dict):
        return False
    # Public evaluation edits are attributed, not certified source reviews.
    if provenance.get("verifier") == "public":
        return False
    fields = provenance.get("fields")
    if not isinstance(fields, (list, tuple, set, dict)) or "disposition" not in fields:
        return False
    if not is_government_host(hostname(str(provenance.get("source_url") or ""))):
        return False
    note = provenance.get("note")
    raw_date = provenance.get("verified_at")
    if not isinstance(note, str) or not note.strip():
        return False
    if not isinstance(raw_date, str) or not re.match(r"^\d{4}-\d{2}-\d{2}(?:T|$)", raw_date):
        return False
    try:
        checked = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
        return checked <= datetime.now(timezone.utc)
    except ValueError:
        return False


def _reviewed_policy_end(provenance: dict | None, route: dict) -> str | None:
    """Project a quoted verdict-policy bound, not an ancillary/refresh date.

    A later verdict recheck can retain an earlier interval notice. Its bound
    keeps its own official source, quote and verification date; the newer
    recheck's timestamp does not renew or replace that notice.
    """
    from .policy_intervals import _date
    from .evidence_validator import jurisdiction_matches, quote_in_text
    if not isinstance(provenance, dict):
        return None
    field_proofs = provenance.get("field_provenance")
    field_proofs = field_proofs if isinstance(field_proofs, dict) else {}
    claimed = provenance.get("fields")
    claimed = claimed if isinstance(claimed, (list, tuple, set, dict)) else ()
    proof = field_proofs.get("disposition")
    if not isinstance(proof, dict):
        if "disposition" in field_proofs or "disposition" not in claimed:
            return None
        proof = provenance
    end = _date(proof.get("effective_to"))
    start = _date(proof.get("effective_from"))
    if (not end or (proof.get("effective_from") is not None and not start)
            or (start and end < start)):
        return None
    if not verdict_provenance_supported(dict(proof, fields=["disposition"])):
        return None
    bounds = proof.get("policy_interval_evidence")
    bounds = bounds if isinstance(bounds, dict) else {}
    evidence = bounds.get("effective_to", proof)
    if not isinstance(evidence, dict) or evidence.get("verifier") not in ("ai", "human"):
        return None
    if evidence.get("effective_to") not in (None, end.isoformat()):
        return None
    quotes = evidence.get("quotes")
    quotes = quotes if isinstance(quotes, dict) else {}
    quote = quotes.get("effective_to") or evidence.get("quote")
    if not isinstance(quote, str) or not quote.strip():
        return None
    candidate = dict(evidence, fields=["disposition"], note=evidence.get("note") or quote)
    if (not verdict_provenance_supported(candidate)
            or not jurisdiction_matches(str(evidence.get("source_url") or ""),
                                        route.get("destination_country", ""))):
        return None
    forms = (end.isoformat(), f"{end.day} {end.strftime('%B')} {end.year}",
             f"{end.strftime('%B')} {end.day}, {end.year}",
             f"{end.day:02d} {end.strftime('%B')} {end.year}",
             f"{end.year}年{end.month}月{end.day}日")
    return end.isoformat() if any(quote_in_text(value, quote) for value in forms) else None


def _confidence(guidance: dict, provenance: dict | None,
                grounded_ok: bool = False, *, complete: bool = True,
                disputed: bool = False) -> str:
    """Two grades: complete official-source-checked records are High.

    AI and human source checks use the same grade. Authorship stays in the
    provenance; public edits, missing evidence, gaps and disputes remain Low.
    A model's self-rating or an official URL alone is never verification.
    """
    if disputed or not complete:
        return "Low"
    if isinstance(provenance, dict) and (provenance.get("verifier") == "public" or
            any(isinstance(proof, dict) and proof.get("verifier") == "public"
                for proof in (provenance.get("field_provenance") or {}).values())):
        return "Low"
    from .authority import hostname, is_government_host
    source = (provenance or {}).get("source_url") or guidance.get("source_url") or guidance.get("official_portal_url")
    if not is_government_host(hostname(str(source or ""))):
        return "Low"
    if verdict_provenance_supported(provenance) or grounded_ok:
        return "High"
    return "Low"


def _clean_text(v):
    """Tidy prose without changing range punctuation in policy facts.

    Dashes can denote continuous date, age, fee and processing intervals.
    Replacing them with commas changed April–June into April, June and could
    alter which seasonal fee applies. Source quotations bypass this helper.
    """
    if not isinstance(v, str) or not v:
        return v
    out = v
    # A semicolon separating clauses becomes a sentence; one inside a list of
    # short items becomes a comma, which is what it was standing in for.
    out = re.sub(r";\s+(?=[A-Z\u4e00-\u9fff])", ". ", out)
    out = out.replace("; ", ", ").replace(";", ",")
    out = re.sub(r",\s*,", ",", out)
    out = re.sub(r"\s{2,}", " ", out).strip()
    out = re.sub(r"[,\.]+$", "", out)
    return out


# Fields whose verification means the override established what this record
# fundamentally IS. An override that sets one of these has earned the record's
# headline source link; one that only corrects a processing time or names a
# consular district has not, and letting it take the link over is how a record
# about a US visitor visa ends up citing an appointment-wait page.
_RECORD_DEFINING = frozenset({
    "disposition", "requirement_detail", "visa_products", "government_fee",
    "visa_category", "permitted_stay", "permitted_stay_days",
})


def _headline_source(guidance: dict, provenance: dict | None) -> str | None:
    """The page a reviewer should land on when they click through the record.

    It has to back the record's main facts. A narrow field correction keeps
    its own citation in the change log and in its note, but it does not get to
    relabel the other twenty-four fields.
    """
    g = guidance or {}
    own = g.get("source_url") or g.get("official_portal_url")
    if not provenance:
        return own
    raw = provenance.get("fields") or []
    fields = set(raw.keys()) if isinstance(raw, dict) else set(raw)
    if fields and not (fields & _RECORD_DEFINING):
        return own or provenance.get("source_url")
    return provenance.get("source_url") or own


# Field 5's second half: 细化子类, the precise subcategory under each of the
# four headline values. The engine already decides this, it simply never
# reached the record, so the enumeration shipped half implemented.
SUBCATEGORY = {
    "unconditional_visa_free": "Unconditional Visa-free",
    "conditional_visa_free": "Conditional Visa-free",
    "transit_visa_free": "Transit Visa-free",
    "evisa_on_arrival": "eVisa on Arrival",
    "paper_visa_on_arrival": "Paper Visa on Arrival",
    "evisa": "eVisa",
    "paper_visa": "Paper Visa",
    "eta_electronic_authorization": "ETA Electronic Authorization",
}


def _entry_requirements(g: dict) -> str | None:
    """Their field 附加信息 "other entry requirements besides the visa",
    marked provide-if-available.

    It was reading a key the engine never writes, so the column was empty on
    every record while the facts sat one level down, already verified: the
    passport-validity rule, a mandatory arrival card, onward travel, funds,
    accommodation, insurance, biometrics, health. Nothing here is invented or
    inferred; each clause appears only when that field is actually set, so a
    route with none of them still returns None rather than filler.
    """
    parts: list[str] = []
    # On a visa-free route this field is the whole answer to "so what DO I
    # need?". Lead with the filing that decides whether they board, and when
    # there is none, say so outright rather than leaving a reader to infer it
    # from an absence.
    if str(g.get("disposition") or "").upper() == "VISA_EXEMPT":
        ac = g.get("arrival_card")
        if isinstance(ac, dict) and ac.get("required"):
            nm = str(ac.get("name") or "arrival card").strip()
            when = str(ac.get("submission_window") or "").strip()
            parts.append(f"No visa. You must still file the {nm}"
                         + (f", {when}" if when else ""))
        elif _files_something_online(g):
            parts.append("No visa. You must still hold an approved travel "
                         "authorisation before boarding")
        else:
            parts.append("No visa and no travel authorisation. Travel on a "
                         "valid passport")
    pv = g.get("passport_validity")
    if isinstance(pv, str) and pv.strip():
        parts.append(f"Passport: {pv.strip().rstrip('.')}")
    ac = g.get("arrival_card")
    if isinstance(ac, dict) and ac.get("required") and not any(
            "must still file" in p for p in parts):
        name = str(ac.get("name") or "arrival card").strip()
        when = str(ac.get("submission_window") or "").strip()
        parts.append(f"{name} required" + (f" ({when})" if when else ""))
    if isinstance(ac, dict):
        notes = []
        for key in ("notes", "note"):
            value = ac.get(key)
            for note in value if isinstance(value, list) else [value]:
                if isinstance(note, str) and note.strip() and note.strip() not in notes:
                    notes.append(note.strip())
        if notes:
            if ac.get("required") is not True:
                name = str(ac.get("name") or "Arrival card").strip()
                when = str(ac.get("submission_window") or "").strip()
                parts.append(name + (f" ({when})" if when and ac.get("required") is not False else ""))
            parts.extend(notes)
    for key, label in (("onward_travel_evidence", "Onward travel"),
                       ("accommodation_evidence", "Accommodation"),
                       ("financial_evidence", "Funds")):
        v = g.get(key)
        if isinstance(v, str) and v.strip():
            parts.append(f"{label}: {v.strip().rstrip('.')}")
    if g.get("insurance_required") is True:
        parts.append("Travel insurance required")
    if g.get("biometrics_required") is True:
        parts.append("Biometrics collected")
    for h in (g.get("health_requirements") or []):
        if isinstance(h, dict) and h.get("applicability") == "always_required":
            nm = str(h.get("name") or "").strip()
            if nm:
                parts.append(f"Health: {nm}")
    return ". ".join(parts) or None


def _consulate_district(g: dict, route: dict) -> str | None:
    """Which mission handles this applicant, when the route says so. Left
    null rather than guessed: naming the wrong consulate sends someone to the
    wrong city."""
    for src in (g, route):
        v = (src or {}).get("consular_jurisdiction")
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, dict):
            name = str(v.get("mission") or v.get("name") or "").strip()
            if name:
                return name
    return None


_PROCESSING_MEASURE = re.compile(
    r"(?<![\d.])(\d+(?:\.\d+)?)(?:\s*(?:[-–—]|to|or)\s*(\d+(?:\.\d+)?))?"
    r"\s*(?:(working|business|calendar)\s+)?(hours?|days?|weeks?|months?|years?)\b", re.I)
_PROCESSING_SCOPE = re.compile(
    r"\b(?:short[- ]stay|long[- ]stay|short[- ]term|long[- ]term|"
    r"standard|priority|express|urgent|student|tourist|transit|e-?visa|e-?ta|esta)\b", re.I)


def _processing(guidance: dict) -> tuple[float | None, str | None]:
    """Read a minimum only in the contract's day units, never relabel hours.

    Calendar weeks have seven days; working hours/weeks and calendar months
    have no fixed day conversion. Multiple timings and upper bounds do not
    establish a single minimum. Their full wording remains in the record.
    """
    raw = guidance.get("processing_time")
    if not isinstance(raw, str):
        return None, None
    text = raw.lower()
    for word, digit in (("one", "1"), ("two", "2"), ("three", "3"),
                        ("four", "4"), ("five", "5"), ("six", "6"),
                        ("seven", "7"), ("eight", "8"), ("nine", "9"),
                        ("ten", "10"), ("eleven", "11"), ("twelve", "12")):
        text = re.sub(rf"\b{word}\b(?:\s*\({digit}\))?", digit, text)
    measures = list(_PROCESSING_MEASURE.finditer(text))
    if len(measures) != 1:
        return None, None
    measure, = measures
    lo, hi, kind, unit = measure.groups()
    n = float(lo)
    if hi is not None and float(hi) < n:
        return None, None
    prefix = text[:measure.start()]
    if re.search(r"[-+−–—]\s*$|\b(?:not|never|no)\b(?:[\s-]+\w+){0,6}\s*$", prefix):
        return None, None  # malformed signs or a negated duration are not facts
    if re.search(r"(?:\b(?:up to|within|at most|maximum(?: of)?|less than|under)|[<≤])\s*$", prefix):
        return None, None  # an upper limit is not a minimum
    if unit.startswith("day"):
        result_unit = "Working Day" if kind in ("working", "business") else "Calendar Day"
    elif unit.startswith("week") and kind not in ("working", "business"):
        n *= 7
        result_unit = "Calendar Day"
    else:
        return None, None
    return (int(n) if n.is_integer() else n), result_unit


def _processing_note(guidance: dict, *, ambiguous: bool = False):
    text = guidance.get("processing_time")
    if not isinstance(text, str) or not text.strip():
        return None
    # Keep bounds, conditions and estimates even when a numeric minimum is
    # representable. No verification status is inferred from this wording.
    n, _ = _processing(guidance)
    simple = re.fullmatch(r"\s*\d+(?:\.\d+)?\s*(?:(?:working|business|calendar)\s+)?days?\s*[.]?\s*", text, re.I)
    if n is not None and simple and not ambiguous:
        return None
    label = ("Route processing information (product scope must be checked)"
             if ambiguous else "Processing time (as stated)")
    return {"label": label, "text": text.strip()}


def _ambiguous_inherited_processing(guidance: dict, products: list[dict]) -> bool:
    if len(products) < 2:
        return False
    text = str(guidance.get("processing_time") or "")
    if _PROCESSING_SCOPE.search(text):
        return True
    # A labelled category such as 'Children: 5 days' cannot be silently
    # assigned to another product. Generic timing labels do not narrow scope.
    label = re.match(r"\s*([^:;\n]{1,80}):", text)
    return bool(label and label.group(1).strip().lower() not in (
        "processing", "processing time", "processing times", "decision", "decisions"))


# Which subcategories each primary classification owns. Their tree nests the
# eight 细化子类 under the three primaries, so a pairing outside this map is not
# a fine distinction, it is a contradiction: "Visa-free / Paper Visa" printed
# beside a visa type of "No visa needed" on 74 records, because the
# subcategory was read off the product's name with no reference to the verdict
# above it.
_NESTED_UNDER = {
    "Visa-free": ("unconditional_visa_free", "conditional_visa_free",
                  "transit_visa_free"),
    "Visa on Arrival": ("evisa_on_arrival", "paper_visa_on_arrival"),
    "Visa Required in Advance": ("evisa", "paper_visa",
                                 "eta_electronic_authorization"),
    # Ellis's own fourth primary, used where the answer turns on a condition
    # the traveller has to meet. It can carry either an advance permission or
    # a conditional exemption, and nothing that is issued at the border.
    "Conditional": ("evisa", "paper_visa", "eta_electronic_authorization",
                    "conditional_visa_free", "transit_visa_free"),
}

# A fee exemption changes the price of a visa, not the need for that visa.
# Require an explicit visa/entry exemption phrase; bare "waiver", "exempt"
# and "free of charge" also describe fees, interviews and biometrics.
_VISA_EXEMPTION_WORDING = re.compile(
    r"\b(?:visa[-\s]+free(?!\s+of\s+charge)|visa[-\s]+exempt(?:ion)?|"
    r"visa\s+waiver|free\s+entry|"
    r"no\s+visa\b(?![-\s]+(?:fee|charge|cost|application|processing|service|exemption|waiver))|"
    r"without\s+(?:a\s+)?visa\b(?![-\s]+(?:fee|charge|cost|application|processing|service))|"
    r"visa\s+(?:is\s+)?not\s+(?:required|needed)|"
    r"exempt(?:ed)?\s+from\s+(?:the\s+|a\s+)?visa\b(?![-\s]+(?:fee|charge|cost|application|processing|service)))",
    re.I)


def _explicit_exemption_wording(text: str) -> bool:
    for match in _VISA_EXEMPTION_WORDING.finditer(text):
        # Do not turn "not eligible for visa-free entry" or "no visa
        # exemption" into an exemption. This is classification, not proof.
        before = re.split(r"[.;\n]", text[:match.start()])[-1]
        after = text[match.end():]
        if re.search(r"\b(?:not|never|ineligible)\b(?:[\s-]+\w+){0,6}[\s-]*$|\bno\s+$", before):
            continue
        if re.match(r"\s+(?:(?:entry|travel|status|scheme)\s+)?(?:(?:is|are|does)\s+)?(?:unavailable|not\s+(?:available|permitted|allowed|applicable|apply))\b", after):
            continue
        return True
    return False


def _product_is_exemption(product: dict) -> bool:
    explicit = _key_of(product.get("requirement_detail"))
    if explicit:
        return SUBCATEGORY[explicit] in _VISA_FREE_DETAILS
    fee = product.get("fee") if isinstance(product.get("fee"), dict) else {}
    amount = (fee or {}).get("amount")
    # A price cannot establish an exemption. An absent price also cannot
    # erase an explicitly named entry exemption; the wording below must
    # still identify a visa exemption, rather than a fee waiver.
    if amount is not None and (type(amount) not in (int, float) or amount != 0):
        return False
    name = str(product.get("type") or "").lower()
    notes = str(product.get("notes") or "").lower()
    if re.search(r"\b(?:e[- ]?ta|esta|k[- ]?eta)\b|electronic\s+(?:travel|authori)", name):
        return False
    words = name + ". " + notes
    if re.search(r"\b(?:fee|charge|payment)[- ]only\b", words):
        return False
    for clause in re.split(r"[.;\n]", words):
        required = re.search(r"\bvisa\s+(?:(?:is|are)\s+)?(?:still\s+)?(?:required|needed)\b", clause)
        if (required and not re.search(r"\b(?:no|without)\s+$", clause[:required.start()])
                and not re.search(r"\b(?:if|unless|otherwise|outside|over|longer|beyond|except)\b", clause)):
            return False
    return _explicit_exemption_wording(name) or _explicit_exemption_wording(notes)


def _subcategory_for(product: dict, route_default: str | None,
                     requirement: str | None,
                     method: str | None = None) -> str | None:
    """Field 5's subcategory for THIS product, inside the primary it sits under.

    A route can offer several kinds of permission at once. A Japanese traveller
    to the United Kingdom needs an ETA and may separately hold a Standard
    Visitor visa; labelling the visa rows "ETA Electronic Authorization"
    because the route's headline is an ETA is wrong on four rows out of five.
    So the product's own name still chooses.

    But it chooses from the set its primary owns. Reading the name alone put a
    paper visa under a visa-free verdict and an ordinary eVisa under a
    visa-on-arrival one. The name says what KIND of permission this is; the
    verdict says WHEN it is obtained, and only the two together name a
    subcategory that exists in their tree.
    """
    allowed = _NESTED_UNDER.get(str(requirement or ""))
    name = f"{product.get('type') or ''}".lower()
    visa_product = any(k in name for k in ("visa", "visitor", "permit", "endorsement")) or bool(
        re.search(r"\bschengen\s+[acd]\b", name))
    electronic = any(k in name for k in ("e-visa", "evisa", "electronic visa",
                                         "online", "e-tourist", "etourist"))
    authorisation = any(k in name for k in (
        "eta", "esta", "electronic travel", "travel authoris",
        "travel authoriz", "authorisation", "authorization"))
    at_border = "on arrival" in name or "on-arrival" in name
    exempt_name = not authorisation and _product_is_exemption(product)

    if allowed is None:
        # An unknown or absent primary: fall back to the old name-only reading
        # rather than inventing a nesting for a verdict we do not recognise.
        if authorisation:
            return SUBCATEGORY["eta_electronic_authorization"]
        if at_border:
            return SUBCATEGORY["evisa_on_arrival" if electronic
                               else "paper_visa_on_arrival"]
        if electronic:
            return SUBCATEGORY["evisa"]
        if visa_product:
            return SUBCATEGORY["paper_visa"]
        return route_default

    if "Visa-free" == requirement:
        # There is no visa, so no product can name its kind. The route's own
        # detail decides whether the exemption is unconditional, conditional
        # or transit-only; nothing about the product may override that.
        key = _key_of(route_default)
        return SUBCATEGORY[key if key in allowed else "unconditional_visa_free"]

    if requirement == "Visa on Arrival":
        return SUBCATEGORY["evisa_on_arrival" if electronic
                           else "paper_visa_on_arrival"]

    # Advance permissions, and the conditional case that can hold either an
    # advance permission or a conditional exemption.
    if authorisation and "eta_electronic_authorization" in allowed:
        return SUBCATEGORY["eta_electronic_authorization"]
    if (electronic or at_border) and "evisa" in allowed:
        # "Visa on arrival, pre-applied online" on a route that requires
        # advance action is an eVisa: the applying happens before travel and
        # only the sticker is handed over at the border.
        return SUBCATEGORY["evisa"]
    if exempt_name and "conditional_visa_free" in allowed:
        # "Visa-free entry as an organised tourist group", "Free Entry for
        # 14 Days": the product itself says no visa is issued. Reading the
        # word "visa" in it and filing it as a paper visa gave an exemption
        # an embassy to apply at.
        return SUBCATEGORY["conditional_visa_free"]
    if visa_product:
        # A product whose name says nothing electronic stays a paper visa
        # even on a route whose channel is online: a UK visitor visa is
        # applied for online and issued as a vignette. Only the product's
        # own words, or an override, make it an eVisa.
        return SUBCATEGORY["paper_visa"] if "paper_visa" in allowed \
            else SUBCATEGORY[allowed[0]]
    key = _key_of(route_default)
    if key in allowed:
        return SUBCATEGORY[key]
    # A name that says nothing ("Single-entry tourist") takes the kind its
    # channel implies: lodged in person it is a sticker, filed online an
    # eVisa. Defaulting every such product to eVisa printed "eVisa" beside
    # "Embassy Submission" on the same row.
    if method in _IN_PERSON_METHODS and "paper_visa" in allowed:
        return SUBCATEGORY["paper_visa"]
    if method == "Online Application" and "evisa" in allowed:
        return SUBCATEGORY["evisa"]
    return SUBCATEGORY[allowed[0]]


_LABEL_TO_KEY = {v: k for k, v in SUBCATEGORY.items()}


def _nested_detail(raw, requirement: str | None) -> str | None:
    """The route's own subcategory, forced inside the primary it sits under.

    Used on the product-less path, where there is no product name to read and
    the engine's requirement_detail is taken as given. When the two disagree
    the verdict wins, because the verdict is what the customer reads first."""
    key = _key_of(raw)
    allowed = _NESTED_UNDER.get(str(requirement or ""))
    if allowed is None:
        return SUBCATEGORY.get(key)
    if key in allowed:
        return SUBCATEGORY[key]
    if requirement == "Visa-free":
        return SUBCATEGORY["unconditional_visa_free"]
    return SUBCATEGORY[allowed[0]] if key else None


def _permission_family(detail, disposition=None) -> str | None:
    key = _key_of(detail)
    if key in ("evisa", "paper_visa"):
        return "visa"
    if key in ("evisa_on_arrival", "paper_visa_on_arrival"):
        return "arrival"
    if key == "eta_electronic_authorization":
        return "authorisation"
    if key in ("unconditional_visa_free", "conditional_visa_free", "transit_visa_free"):
        return "exemption"
    return {"VISA_REQUIRED": "visa", "VISA_ON_ARRIVAL": "arrival",
            "ELECTRONIC_AUTHORIZATION_REQUIRED": "authorisation",
            "VISA_EXEMPT": "exemption"}.get(disposition)


def _product_detail(product: dict, route_detail=None) -> str | None:
    explicit = _key_of(product.get("requirement_detail"))
    if explicit:
        return SUBCATEGORY[explicit]
    if _product_is_exemption(product):
        key = _key_of(route_detail)
        return SUBCATEGORY[key if key in ("conditional_visa_free", "transit_visa_free")
                           else "unconditional_visa_free"]
    return _subcategory_for(product, None, None)


def _product_fields(row: dict, product: dict) -> None:
    """Read explicit product facts; missing facts never borrow another visa."""
    for key in ("required_documents", "entry_requirements"):
        if key in product:
            v = product[key]
            row[key] = ", ".join(str(x) for x in v if x) if isinstance(v, list) else v
    if "processing_time" in product:
        row["processing_min_days"], row["processing_unit"] = _processing(product)
        row["_processing_note"] = _processing_note(product)
    if "consular_jurisdiction" in product:
        row["consulate_district"] = _consulate_district(product, {})
    if "exceptions" in product:
        v = product["exceptions"]
        row["special_conditions"] = ". ".join(str(x) for x in v if x) if isinstance(v, list) else v


_PRODUCT_FIELD_CELLS = {
    "validity": ("validity_duration", "validity_unit"),
    "entry": ("entries",),
    "max_stay_days": ("max_stay_duration", "max_stay_unit"),
    "permitted_stay": ("max_stay_duration", "max_stay_unit"),
    "fee": ("visa_fee_amount", "visa_fee_currency"),
    "processing_time": ("processing_min_days", "processing_unit"),
    "application_channel": ("application_method",),
    "application_channel_detail": ("application_method",),
    "policy_valid_until": ("info_validity",),
    "consular_jurisdiction": ("consulate_district",),
    "exceptions": ("special_conditions",),
    "notes": ("special_conditions",),
}


def _product_unpublished_fields(product: dict, inherited) -> set:
    """An explicit product review supersedes a route's absence claim.

    Preserve a route-wide Not published state only for cells the product has
    not independently reviewed. A sibling's documented absence never supplies
    another product's unknown field; own recorded dispositions stay local.
    """
    result = set(inherited)
    own = set(product.get("unpublished_fields") or [])
    proofs = product.get("field_provenance")
    for field, proof in (proofs.items() if isinstance(proofs, dict) else ()):
        if isinstance(proof, dict):
            cells = _PRODUCT_FIELD_CELLS.get(field, (field,) if field in CONTRACT_FIELDS else ())
            result.difference_update(cells)
            # Old registered conversions stored these scoped proofs but put
            # their flags on the route. Recover only the named product's
            # explicit disposition, never a generic unknown reason.
            recorded_np = (proof.get("status") in {"not_published", "not-published"}
                           or (proof.get("status") == "unknown" and
                               str(proof.get("reason") or "").startswith("Not published by the destination:")))
            if recorded_np:
                own.update(cells)
            else:
                own.difference_update(cells)
    result.update(own)
    return result


def _explicit_product_verdict_provenance(product: dict, route: dict) -> tuple[bool, dict | None]:
    """A new explicit product review replaces, never inherits, parent proof."""
    proofs = product.get("field_provenance")
    if not isinstance(proofs, dict) or "disposition" not in proofs:
        return False, None
    proof = proofs.get("disposition")
    if not isinstance(proof, dict) or proof.get("status") not in (None, "reviewed", "verified"):
        return True, None
    subject = proof.get("subject") or {}
    if not isinstance(subject, dict):
        return True, None
    if (not isinstance(subject.get("disposition"), str)
            or subject["disposition"] not in _DISPOSITION_TO_REQUIREMENT
            or (subject.get("requirement_detail") is not None and
                (not isinstance(subject["requirement_detail"], str) or
                 subject["requirement_detail"] not in SUBCATEGORY)) or any(
            key not in subject or subject[key] != product.get(key)
            for key in ("disposition", "requirement_detail"))):
        return True, None
    route = dict(route, travel_document_type=route.get('travel_document_type') or 'ordinary_passport')
    if (subject.get("product_type") != product.get("type") or any(
            subject.get(key) != route.get(key) for key in
            ("passport_nationality", "destination_country", "travel_purpose", "travel_document_type"))):
        return True, None
    from .evidence_validator import jurisdiction_matches
    if not jurisdiction_matches(str(proof.get("source_url") or ""), route.get("destination_country", "")):
        return True, None
    candidate = dict(proof, fields=["disposition"])
    return True, candidate if verdict_provenance_supported(candidate) else None


def _product_field_reference(product: dict, route: dict) -> str | None:
    """Locate a product-owned field citation without crediting its verdict.

    A fee or validity review can supply a useful source link even while the
    product's nationality eligibility remains unchecked. Parent citations,
    unscoped legacy notes and another product's proof cannot supply it.
    """
    proofs = product.get("field_provenance")
    if not isinstance(proofs, dict):
        return None
    from .authority import hostname, is_government_host
    from .evidence_validator import jurisdiction_matches
    expected = dict(route, travel_document_type=route.get("travel_document_type") or "ordinary_passport")
    for field in ("fee", "validity", "max_stay_days", "entry"):
        proof = proofs.get(field)
        if (not isinstance(proof, dict) or proof.get("status") not in ("reviewed", "verified")
                or proof.get("verifier") not in ("ai", "human")
                or not isinstance(proof.get("quote"), str) or not proof["quote"].strip()
                or product.get(field) in (None, "", {})):
            continue
        subject = proof.get("subject")
        if not isinstance(subject, dict) or subject.get("product_type") != product.get("type"):
            continue
        if any(key not in subject or subject[key] != expected.get(key) for key in
               ("passport_nationality", "destination_country", "travel_purpose", "travel_document_type")):
            continue
        if any(key not in subject or subject[key] != product.get(key) for key in
               ("disposition", "requirement_detail")):
            continue
        url = proof.get("source_url")
        if (isinstance(url, str) and is_government_host(hostname(url))
                and jurisdiction_matches(url, expected.get("destination_country", ""))):
            return url
    return None


def _separate_product_method(product: dict, detail: str, guidance: dict) -> str | None:
    explicit = (_method_for_channel(product.get("application_channel"))
                or _method_from_detail(product))
    if explicit:
        return explicit
    # Electronic permissions and arrival processing state their method in
    # their own kind. A paper visa alone does not say where it is lodged.
    if detail in ("eVisa", "ETA Electronic Authorization", "eVisa on Arrival"):
        return "Online Application"
    if detail == "Paper Visa on Arrival":
        return "On-arrival Processing"
    name_method = _method_from_detail({"application_channel_detail": product.get("type")})
    if name_method:
        return name_method
    if _permission_family(detail) == "visa":
        # Some mixed routes explicitly describe the alternative in a route
        # sentence: "apply for a paper visa through JVAC" is evidence for
        # that visa's method. An ETA portal sentence is not.
        for sentence in re.split(r"[.;]\s*", str(guidance.get("application_channel_detail") or "")):
            if re.search(r"\b(?:paper|consular|visitor|short[- ]stay)\s+visa\b", sentence, re.I):
                method = _method_from_detail({"application_channel_detail": sentence})
                if method:
                    return method
    return None


def _key_of(value) -> str:
    """A subcategory arrives either as its key or as its printed label."""
    raw = str(value or "").strip()
    if raw in SUBCATEGORY:
        return raw
    return _LABEL_TO_KEY.get(raw, "")


def _strip_visa_only_fields(row: dict) -> dict:
    """Remove properties of a visa from a route that issues none.

    A visa-free record was showing "Validity 90 days" beside "Max stay 90
    days" and an application method of "Other". There is no visa, so it has no
    validity and no entry count, and nothing is applied for. The stay and the
    entry rules live in max_stay and entry_requirements, where they belong.
    """
    if str(row.get("visa_requirement") or "") != "Visa-free":
        return row
    row = dict(row)
    for f in ("validity_duration", "validity_unit", "entries",
              "processing_min_days", "processing_unit"):
        row[f] = None
    # Their enum has no "not applicable", so an empty cell carries it and the
    # checklist says why. "Other" implied a channel that does not exist.
    if row.get("application_method") == "Other":
        row["application_method"] = None
    # A method only belongs on a visa-free route when the record actually
    # names something the traveller files: an arrival card, a travel
    # authorisation, a passenger declaration. Forty records offered "Online
    # Application" with nothing anywhere in them to apply for, which sends a
    # traveller looking for a form that does not exist.
    if row.get("application_method") and not _names_something_to_file(row):
        row["application_method"] = None
    # These two ran only when a verified override declared the route
    # visa-free, so a route the engine alone called visa-free kept every
    # contradiction. They belong here, on the one path every record takes.
    docs = row.get("required_documents")
    if docs:
        row["required_documents"] = _documents_without_an_application(docs)
    for f in ("special_conditions", "entry_requirements"):
        text = row.get(f)
        if text:
            row[f] = _text_without_a_required_authorisation(text)
    return row


def _documents_without_an_application(docs):
    """Drop items that exist only to feed a form, on a route with no form.

    What survives is what a border officer can ask to see: the passport, the
    onward ticket, the accommodation booking, the funds. Japan to Italy asked
    a visa-free traveller for an email address and a payment card, left over
    from an ETIAS the Commission has not yet brought into operation."""
    def keep(item: str) -> bool:
        low = str(item).strip().lower()
        return bool(low) and not any(m in low for m in _APPLICATION_ONLY_DOCUMENTS)

    if isinstance(docs, (list, tuple)):
        kept = [d for d in docs if keep(d)]
        return kept if kept else docs
    text = str(docs or "")
    if not text:
        return docs
    kept = [part.strip() for part in text.split(",") if keep(part)]
    return ", ".join(kept) if kept else docs


_APPLICATION_ONLY_DOCUMENTS = (
    "email address", "e-mail address", "payment card", "credit card",
    "debit card", "bank card", "application form", "application fee",
    "payment method",
)

# A sentence asserting the traveller must hold an authorisation, on a route
# whose verdict is that none is required. The explanations stay: "ETIAS is an
# entry authorisation, not a visa" and "ETIAS is not in operation" are both
# true and both worth saying. Only the assertion goes.
_ASSERTS_AN_AUTHORISATION = re.compile(
    r"[^.]*?(requires?\s+(only\s+)?an?\s+(approved\s+)?"
    r"(ETIAS|ESTA|K-ETA|ETA|electronic travel authoris\w*)"
    r"|must\s+(hold|obtain|apply\s+for|have)\s+an?\s+(approved\s+)?"
    r"(ETIAS|ESTA|K-ETA|ETA|electronic travel authoris\w*)"
    r"|needs?\s+an?\s+(approved\s+)?(ETIAS|ESTA|K-ETA|ETA)\b"
    r"|(ETIAS|ESTA|K-ETA)\s+is\s+required)[^.]*\.?", re.I)


def _text_without_a_required_authorisation(value):
    """Remove only the sentences that contradict a no-authorisation verdict."""
    if isinstance(value, (list, tuple)):
        kept = [v for v in value
                if not _ASSERTS_AN_AUTHORISATION.search(str(v or ""))]
        return kept if kept else None
    text = str(value or "")
    if not text:
        return value
    cleaned = _ASSERTS_AN_AUTHORISATION.sub("", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" .,;")
    return cleaned or None


# The things a visa-free traveller can still be required to file before
# travelling. If a record names none of them, there is nothing to apply for.
_FILINGS = re.compile(
    r"arrival card|travel authoris\w*|travel authoriz\w*|ETIAS|ESTA|K-ETA"
    r"|\bETA\b|eTA\b|declaration|registration|register\b|pre-?arrival"
    r"|MDAC|TDAC|SG Arrival|eTravel|e-Ticket|entry permit|entry form", re.I)


def _names_something_to_file(row: dict) -> bool:
    text = " ".join(str(row.get(f) or "") for f in (
        "entry_requirements", "required_documents", "special_conditions",
        "visa_type_name"))
    return bool(_FILINGS.search(text))


def _required_values_supported(row: dict, g: dict, checked: set[str]) -> bool:
    """A checked verdict cannot certify unrelated filled product fields."""
    products = g.get("visa_products")
    product = next((p for p in products if isinstance(p, dict) and
                    p.get("type") == row.get("visa_type_name")), {}) if isinstance(products, list) else {}
    # A separated product is passed as its own guidance dictionary.
    if g.get("type") == row.get("visa_type_name"):
        product = g
    product_checked = "visa_products" in checked
    def own(field):
        return product_checked and product.get(field) not in (None, "", [], {})
    exempt = row.get("visa_requirement") == "Visa-free"
    stay_checked = bool(checked & {"permitted_stay", "permitted_stay_days"}) or own("max_stay_days") or own("permitted_stay")
    checks = {
        "visa_type_name": (exempt and "disposition" in checked) or "visa_category" in checked or product_checked,
        "max_stay_duration": stay_checked,
        "validity_duration": (stay_checked if exempt else own("validity") or "validity" in checked),
        "entries": (exempt and "disposition" in checked) or own("entry") or product_checked,
        "visa_fee_amount": (exempt and "disposition" in checked) or "government_fee" in checked or own("fee"),
        "application_method": ("application_channel" in checked or own("application_channel") or
                               own("application_channel_detail") or
                               ("requirement_detail" in checked and row.get("visa_requirement_detail") in
                                {"eVisa", "ETA Electronic Authorization", "Paper Visa on Arrival", "eVisa on Arrival"})),
        "required_documents": ("required_documents" in checked or own("required_documents") or
                               (exempt and row.get("required_documents") == "Valid passport" and "disposition" in checked)),
    }
    statuses = field_status(row)
    return all(supported for field, supported in checks.items() if statuses.get(field) == "filled")


def _regrade(row: dict, g: dict, disputed: list | None,
             unpublished: set | None = None) -> dict:
    """Apply their §4.2.3 ladder to the finished row.

    High means official-source checked, complete, and free of conflict. Only
    the first of those is knowable before the row exists, which is why a
    record missing a required field, or carrying one its own official page
    disputes, was still being shown as High.
    """
    row = dict(row) if disputed else _strip_visa_only_fields(dict(row))
    if row.get("application_method") == "Other":
        # No path emits this any more. Kept so a stale row can never say it.
        row["application_method"] = None
    prov, grounded = row.pop("_prov", None), row.pop("_grounded", False)
    checked = set(row.pop("_grade_checked_fields", ()))
    if verdict_provenance_supported(prov):
        checked.update((prov or {}).get("fields") or ())
    timing = row.pop("_processing_note", None)
    if (timing and row.get("visa_requirement") != "Visa-free"
            and row.get("visa_requirement_detail") not in _VISA_FREE_DETAILS):
        current = str(row.get("special_conditions") or "")
        if timing["text"] not in current:
            note = f"{timing['label']}: {timing['text']}"
            row["special_conditions"] = (current.rstrip(". ") + ". " + note) if current else note
    st = field_status(row, unpublished)
    complete = not any(v == "missing" for v in st.values())
    conflicted = bool(disputed)
    # Keep the pre-existing source/conflict publication boundary separate
    # from the requested binary display grade. Missing fields never become
    # invented values, but relabeling Medium must not create a new hold.
    row["_evidence_low"] = _confidence(g, prov, grounded, complete=True,
                                       disputed=conflicted) == "Low"
    if not (prov and "disposition" in (prov.get("fields") or ())):
        row["_evidence_low"] = row["_evidence_low"] or str(g.get("confidence") or "").lower() == "low"
    row["confidence_level"] = _confidence(g, prov,
                                          grounded, complete=complete and _required_values_supported(row, g, checked),
                                          disputed=conflicted)
    return row


def records_for_route(route: dict, guidance: dict,
                      provenance: dict | None = None,
                      collected_at: str | None = None,
                      valid_until: str | None = None,
                      grounded_ok: bool = False,
                      disputed_fields: list | None = None,
                      grounded_fields: list | None = None) -> list[dict]:
    """The route's answer as T-Station 25-field records, one per visa
    product; a product-less route (visa-free, or detail still filling)
    yields a single route-level record. ``valid_until`` is the cache's
    freshness deadline, never evidence of the policy's expiry date."""
    g = dict(guidance or {})
    disposition = str(g.get("disposition") or "").upper()
    # The on-arrival subtype defines the category even for historical rows
    # that used the broad VISA_REQUIRED enum.
    if disposition == "VISA_REQUIRED" and g.get("requirement_detail") in (
            "evisa_on_arrival", "paper_visa_on_arrival"):
        disposition = g["disposition"] = "VISA_ON_ARRIVAL"
    from .kimi_primary import serve_time_invariants
    contradictions = serve_time_invariants(g)
    disputed_fields = list(disputed_fields or [])
    if contradictions:
        disputed_fields.append("disposition")
    # Fields a destination has been checked for and does not publish.
    _unpub = {str(x) for x in (g.get("unpublished_fields") or [])}
    requirement = _DISPOSITION_TO_REQUIREMENT.get(disposition)
    docs = g.get("required_documents")
    docs = ", ".join(str(d) for d in docs if d) if isinstance(docs, list) else (
        str(docs) if docs else None)
    method = _method_for_channel(g.get("application_channel"))
    method_from_channel = method is not None
    if method is None:
        method = _method_from_detail(g)
        # A lodging place read from the route's own sentence (an agency, a
        # mission, a government office) is a stated channel too: an eVisa
        # "lodged only through designated agencies" is an agency filing.
        # A stale "online" or "at the border" phrase stays advisory.
        method_from_channel = method in _IN_PERSON_METHODS
    if method is None and disposition != "VISA_EXEMPT":
        # The engine left the channel blank but the product names it: a thing
        # called an e-Visa is applied for online, a consular sticker at a
        # mission. "Other" on a route that needs a visa tells an applicant
        # nothing about where to go.
        method = _method_from_product(g)
    if disposition == "VISA_EXEMPT":
        # A visa-free traveller often still files something before boarding:
        # an arrival card, an ESTA, an eTA. Collapsing all of them to "Other"
        # buried the one instruction that decides whether they are let on the
        # plane. A route with genuinely nothing to file has no method, and
        # the checklist marks the cell not applicable.
        method = "Online Application" if _files_something_online(g) else None
        method_from_channel = False
    entry_req = g.get("entry_requirements")
    if isinstance(entry_req, list):
        entry_req = ". ".join(str(x) for x in entry_req if x) or None
    if isinstance(entry_req, str):
        arrival_text = _entry_requirements({"arrival_card": g.get("arrival_card")})
        if arrival_text and arrival_text not in entry_req:
            entry_req = entry_req.rstrip(". ") + ". " + arrival_text
    exceptions = g.get("exceptions")
    if isinstance(exceptions, list):
        exceptions = ". ".join(str(x) for x in exceptions if x) or None
    proc_n, proc_unit = _processing(g)
    base = {
        "travel_document_type": route.get("travel_document_type")
                                or "ordinary_passport",
        "travel_document_country": route.get("passport_nationality"),
        "destination_country": route.get("destination_country"),
        "travel_purpose": route.get("travel_purpose") or "tourism",
        "visa_requirement": requirement,
        # Constrained to the primary above it. A route whose verdict is
        # visa-free cannot carry "Paper Visa" as its subcategory, and nine
        # product-less routes did exactly that, printing "Visa-free / Paper
        # Visa" beside a visa type of "No visa needed".
        "visa_requirement_detail": _nested_detail(
            g.get("requirement_detail"), requirement),
        "visa_type_name": None,
        "validity_duration": None, "validity_unit": None,
        "max_stay_duration": None, "max_stay_unit": None,
        "entries": None,
        "processing_min_days": proc_n, "processing_unit": proc_unit,
        "_processing_note": _processing_note(g),
        "visa_fee_amount": None, "visa_fee_currency": None,
        "visa_fee_qualifier": _fee_qualifier({}, g),
        "application_method": method,
        "required_documents": docs,
        "consulate_district": _consulate_district(g, route),
        "entry_requirements": (entry_req if isinstance(entry_req, str)
                               else _entry_requirements(g)),
        "special_conditions": exceptions if isinstance(exceptions, str) else None,
        "data_source": ((provenance or {}).get("verified_by")
                       or ("Ellis official-page check" if grounded_ok else
                           "Ellis route engine (reference only)" if g else None)),
        "source_url": _headline_source(g, provenance),
        "collected_at": ((provenance or {}).get("verified_at")
                         or (collected_at or "")[:10]) or None,
        # Dictionary field 24 is the policy's own published end date.
        # A cache TTL schedules rechecking; it cannot fill a missing policy
        # fact or make a record appear contractually complete.
        "info_validity": g.get("policy_valid_until") or _reviewed_policy_end(provenance, route),
        "freshness_valid_until": valid_until or None,
        "confidence_level": _confidence(g, provenance, grounded_ok),
        # §4.2.1's cross-validation, bound one URL at a time. This sits
        # ALONGSIDE the 25 fields rather than inside them: field 22 is a
        # single source_url by their dictionary and stays that way, so the
        # export shape is unchanged. What was missing was any slot at all for
        # the second and third page a route was checked against.
        "corroborating_sources": _corroborating(g),
        "_prov": provenance, "_grounded": grounded_ok,
        "_grade_checked_fields": sorted((set((provenance or {}).get("fields") or ()) if verdict_provenance_supported(provenance) else set()) |
                                        (set(grounded_fields or ()) if grounded_ok else set())),
        "_disputed_fields": sorted(set(disputed_fields)),
        "_unpublished": sorted(_unpub),
    }
    raw_products = g.get("visa_products")
    products = [p for p in (raw_products if isinstance(raw_products, list) else [])
                if isinstance(p, dict) and p.get("type")]
    # Legacy conversions leaked one product's absence flags onto the route.
    # Such a flag cannot close an unreviewed sibling's cell. Each explicitly
    # documented product gets its own state back in the product loop below.
    product_absence = set().union(*(_product_unpublished_fields(p, ()) for p in products))
    product_route_unpublished = _unpub - product_absence
    exempt_conflict = disposition == "VISA_EXEMPT" and bool(contradictions)
    product_families = {_permission_family(_product_detail(p, base.get("visa_requirement_detail")))
                        for p in products} - {None}
    mixed_exemption_products = "exemption" in product_families and bool(product_families - {"exemption"})
    # A verified exemption can coexist with an optional visa for a longer
    # visit. Project each explicit lane instead of erasing the paid options;
    # the ordinary separate-product rules keep their evidence independent.
    if (disposition == "VISA_EXEMPT" and not exempt_conflict and not mixed_exemption_products) or not products:
        row = dict(base)
        if disposition == "VISA_EXEMPT":
            row["visa_type_name"] = (g.get("visa_category") or
                                     "Requirement under review") if exempt_conflict else "No visa needed"
            if not row["required_documents"]:
                row["required_documents"] = "Valid passport"
            _set_stay(row, g.get("permitted_stay"), g.get("permitted_stay_days"))
            n, unit = row["max_stay_duration"], row["max_stay_unit"]
            # The stay may legitimately be in hours (a transit exemption),
            # but their validity_unit enum has no Hour: route it through the
            # same conversion the product rows use.
            _set_validity(row, n, unit, g.get("permitted_stay"))
            row["entries"] = "Unlimited"
            row["visa_fee_amount"], row["visa_fee_currency"] = (
                _fee({}, g) if exempt_conflict else (0, "USD"))
        else:
            row["visa_type_name"] = g.get("visa_category") or None
            _set_stay(row, g.get("permitted_stay"), g.get("permitted_stay_days"))
            # A separate, explicit validity may exist on a product-less route.
            # Permitted stay never supplies that independent entry window.
            n, unit = _validity_num_unit(g.get("validity"))
            _set_validity(row, n, unit, g.get("validity"))
            amt, cur = _fee({}, g)
            row["visa_fee_amount"], row["visa_fee_currency"] = amt, cur
        row["visa_fee_qualifier"] = _fee_qualifier({}, g)
        row["application_method"] = _method_for_detail(
            row.get("visa_requirement_detail"), method, row, method_from_channel)
        return [_regrade({k: _clean_text(v) for k, v in row.items()}, g, disputed_fields, _unpub)]
    rows = []
    ambiguous_timing = _ambiguous_inherited_processing(g, products)
    for product_index, p in enumerate(products):
        row = dict(base)
        row["_product_index"] = product_index
        if ambiguous_timing:
            row["processing_min_days"], row["processing_unit"] = None, None
            row["_processing_note"] = _processing_note(g, ambiguous=True)
        row["visa_type_name"] = str(p.get("type"))
        row["visa_requirement_detail"] = _subcategory_for(
            p, base.get("visa_requirement_detail"), requirement, method)
        own_detail = _product_detail(p, base.get("visa_requirement_detail"))
        if _key_of(p.get("requirement_detail")):
            # Electronic issuance is an explicit product fact, independent
            # of its name (UK Standard Visitor visas now issue as eVisas).
            row["visa_requirement_detail"] = own_detail
        own_family = _permission_family(own_detail)
        route_family = _permission_family(g.get("requirement_detail"), disposition)
        separate_permission = bool(own_family and (
            (route_family and own_family != route_family) or
            (own_family == route_family == "visa" and
             _key_of(g.get("requirement_detail")) in ("evisa", "paper_visa") and
             _key_of(own_detail) != _key_of(g.get("requirement_detail"))) or
            (disposition == "CONDITIONAL" and not route_family and len(product_families) > 1)))
        product_g, product_unpublished = g, _unpub
        if separate_permission:
            # ETA eligibility does not verify an optional Standard Visitor
            # visa's documents, timing, application method, or source. The
            # same separation applies to exemptions, visas, and arrival visas.
            own_disposition = {"visa": "VISA_REQUIRED", "arrival": "VISA_ON_ARRIVAL",
                               "authorisation": "ELECTRONIC_AUTHORIZATION_REQUIRED",
                               "exemption": "VISA_EXEMPT"}[own_family]
            row["visa_requirement"] = _DISPOSITION_TO_REQUIREMENT[own_disposition]
            row["visa_requirement_detail"] = own_detail
            for key in ("required_documents", "entry_requirements", "special_conditions",
                        "processing_min_days", "processing_unit", "_processing_note", "application_method",
                        "consulate_district", "source_url", "collected_at", "info_validity"):
                row[key] = None
            product_g = dict(p, disposition=own_disposition, requirement_detail=_key_of(own_detail))
            product_unpublished = set(p.get("unpublished_fields") or [])
            row["_unpublished"] = sorted(product_unpublished)
            row["_prov"], row["_grounded"] = None, False
            row["_grade_checked_fields"] = []
            row["_separate_permission"] = True
            row["_product_source_verified"] = None
            row["corroborating_sources"] = []
            row["data_source"] = "Ellis product information (reference only)"
            from .authority import hostname, is_government_host
            own_url = str(p.get("source_url") or "")
            if is_government_host(hostname(own_url)):
                row["source_url"] = own_url
                row["collected_at"] = p.get("verified_at")
                product_prov = ((provenance or {}).get("field_provenance") or {}).get("visa_products")
                if not product_prov and "visa_products" in ((provenance or {}).get("fields") or []):
                    product_prov = provenance
                if product_prov and p.get("source_quote"):
                    row["_prov"] = dict(product_prov, source_url=own_url,
                                        note=p["source_quote"],
                                        verified_at=p.get("verified_at") or product_prov.get("verified_at"),
                                        fields=["disposition", "visa_products"])
                    row["_product_source_verified"] = row["_prov"]
                    row["data_source"] = product_prov.get("verified_by") or "Ellis product source check"
        # A product may have a different policy interval from the route's
        # default permission. An explicitly unknown product date also must
        # not inherit the route date. Separate permission families already
        # clear the parent's date above.
        if "policy_valid_until" in p:
            row["info_validity"] = p.get("policy_valid_until") or None
        product_unpublished = _product_unpublished_fields(
            p, () if separate_permission else product_route_unpublished)
        row["_unpublished"] = sorted(product_unpublished)
        _product_fields(row, p)
        if not separate_permission and p.get("source_url"):
            from .authority import hostname, is_government_host
            own_url = str(p["source_url"])
            if is_government_host(hostname(own_url)):
                row["source_url"] = own_url
                row["collected_at"] = p.get("verified_at")
                parent_prov = ((provenance or {}).get("field_provenance") or {}).get("visa_products")
                if not parent_prov and "visa_products" in ((provenance or {}).get("fields") or []):
                    parent_prov = provenance
                own_prov = (dict(parent_prov, source_url=own_url, note=p["source_quote"],
                                 verified_at=p.get("verified_at") or parent_prov.get("verified_at"),
                                 fields=["disposition", "visa_products"])
                            if parent_prov and p.get("source_quote") else None)
                row["_product_source_verified"] = row["_prov"] = own_prov
                row["_grounded"] = False
        if p.get("corroborating_sources"):
            row["corroborating_sources"] = _corroborating(p)
        explicit_review, own_review = _explicit_product_verdict_provenance(p, route)
        if explicit_review:
            # An invalid explicit review must not fall back to a valid parent
            # visa-products citation. Only this product's reviewed verdict is
            # credited; fees/documents remain separately scoped evidence.
            row["_product_source_verified"] = row["_prov"] = own_review
            row["_grounded"] = False
            row["collected_at"] = own_review.get("verified_at") if own_review else None
            row["data_source"] = (own_review.get("verified_by") or "Ellis product visa requirement source review") if own_review else "Ellis product information (reference only)"
            if own_review:
                row["source_url"] = own_review["source_url"]
            if "policy_valid_until" not in p:
                row["info_validity"] = _reviewed_policy_end(own_review, route)
        if separate_permission and not row.get("source_url"):
            reference_url = _product_field_reference(p, route)
            if reference_url:
                # Link presence does not verify eligibility, fill review
                # dates, or inherit any of the parent permission's credit.
                row["source_url"] = reference_url
                row["data_source"] = "Ellis product field source (reference only)"
        exemption_lane = requirement == "Conditional" and _product_is_exemption(p)
        if exemption_lane:
            # The lane is the route's own kind of exemption (transit-only or
            # conditional), never a visa kind read off a stray word.
            route_key = _key_of(base.get("visa_requirement_detail"))
            row["visa_requirement_detail"] = SUBCATEGORY[
                route_key if route_key in ("transit_visa_free",
                                           "conditional_visa_free")
                else "conditional_visa_free"]
        # A permitted stay and a marketed product name cannot establish the
        # visa's separate validity. In particular, "5-year multiple entry"
        # may name a conditional option whose grant remains discretionary.
        n, unit = _validity_num_unit(p.get("validity"))
        _set_validity(row, n, unit, p.get("validity"))
        stay_proofs = p.get("field_provenance") or {}
        owns_unknown_stay = isinstance(stay_proofs, dict) and any(
            isinstance(stay_proofs.get(field), dict)
            and stay_proofs[field].get("status") in {"unknown", "not_published", "not-published"}
            for field in ("max_stay_days", "permitted_stay"))
        stay_text = p.get("permitted_stay")
        if owns_unknown_stay:
            own_text = stay_proofs.get("permitted_stay")
            if not isinstance(own_text, dict) or own_text.get("status") not in {"reviewed", "verified"}:
                stay_text = None
        else:
            stay_text = stay_text or product_g.get("permitted_stay")
        _set_stay(row, stay_text, p.get("max_stay_days"))
        # Definitional fallback: "single-entry" / "multiple-entry" in the
        # product's own name states the entries field.
        row["entries"] = _entries(p.get("entry")) or _entries(p.get("type"))
        amt, cur = _fee(p, product_g)
        row["visa_fee_amount"], row["visa_fee_currency"] = amt, cur
        row["visa_fee_qualifier"] = _fee_qualifier(p, product_g)
        note = p.get("notes")
        if note:
            row["special_conditions"] = (str(note) if not row["special_conditions"]
                                         else f"{row['special_conditions']}. {note}")
        own_method = _method_for_channel(p.get("application_channel")) or _method_from_detail(p)
        explicitly_unknown_method = ('application_channel' in p and p['application_channel'] is None
                                     and 'application_channel_detail' in p and p['application_channel_detail'] is None)
        if explicitly_unknown_method:
            # Electronic issuance does not establish who may file the
            # application. A reviewed unknown must not inherit online filing.
            row['application_method'] = None
        elif own_method:
            row["application_method"] = own_method
        elif separate_permission:
            row["application_method"] = _separate_product_method(
                p, row["visa_requirement_detail"], g)
        elif exemption_lane:
            # Nothing is applied for on an exemption lane, unless the lane
            # itself is an online registration (Japan's e-passport waiver).
            words = f"{p.get('type') or ''} {p.get('notes') or ''}".lower()
            row["application_method"] = (
                "Online Application" if any(k in words for k in
                                            ("regist", "online", "portal"))
                else None)
        elif requirement == "Conditional" and any(
                _product_is_exemption(q) for q in products):
            # A visa product on a mixed route: the route-level channel
            # describes the exemption, so this product answers for itself.
            # Its own place words first, then the sentence that says where
            # visas are lodged, then its kind.
            row["application_method"] = _method_for_detail(
                row["visa_requirement_detail"],
                _method_from_detail(g) or method, row,
                _method_from_detail(g) in _IN_PERSON_METHODS)
        else:
            row["application_method"] = _method_for_detail(
                row["visa_requirement_detail"], method, row, method_from_channel)
        rows.append(_regrade({k: _clean_text(v) for k, v in row.items()}, product_g,
                             disputed_fields, product_unpublished))
    return rows


def _corroborating(g: dict) -> list:
    """The other official pages this answer was checked against.

    Their clause asks for each source to be bound to its own URL where no
    single official source settles the route. One URL per record could not
    express that, so a route checked against three ministries showed one and
    the rest were unauditable."""
    out = []
    for item in (g or {}).get("corroborating_sources") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        # Preserve exact evidence text, including punctuation and ranges.
        row = {"url": url, "quote": item.get("quote")}
        for k in ("authority", "checked_at", "agrees"):
            if item.get(k) not in (None, ""):
                row[k] = item[k]
        out.append(row)
    return out


def _no_consular_application(row: dict) -> bool:
    """A supported, published exemption has no consular filing district.

    The publication and evidence metadata is attached by the dataset reader
    after grading. Requiring it keeps an unchecked label, operator release,
    held product or disputed field from creating new completion credit.
    Online filing alone does not establish absence of consular jurisdiction.
    """
    if (row.get("_held", row.get("held")) is not False
            or row.get("_route_held", row.get("route_held", False)) is True
            or row.get("_source_check", row.get("source_check")) not in {
                "human-quote", "ai-quote", "grounded-consistent"}
            or row.get("_disputed") or row.get("_contradictions")
            or row.get("contradictions")):
        return False
    statuses = row.get("field_status")
    if isinstance(statuses, dict) and "pending-review" in statuses.values():
        return False
    return (row.get("visa_requirement") in {"Visa-free", "Conditional"}
            and row.get("visa_requirement_detail") in _VISA_FREE_DETAILS
            and row.get("application_method") in (None, "", []))


def field_status(row: dict, unpublished: set | None = None) -> dict:
    """Their per-field checklist verdict, with the three kinds of blank kept apart.

    A visa exemption has no visa validity. A documented unpublished value
    remains distinct from that inapplicability. Missing facts stay incomplete
    unless the record holds evidence for an explicit disposition.
    """
    unpublished = set(unpublished or ()) | set(row.get("_unpublished") or ())
    out = {}
    exempt = str(row.get("visa_requirement") or "") == "Visa-free"
    # A conditional exemption product ("Free Entry for 14 Days") is filed
    # nowhere, exactly like a visa-free route.
    no_application = exempt or str(
        row.get("visa_requirement_detail") or "") in _VISA_FREE_DETAILS
    for f in FIELD_ORDER:
        v = row.get(f)
        if v not in (None, "", []):
            out[f] = "filled"
        elif f in unpublished:
            out[f] = "not-published"
        elif f == "application_method" and no_application:
            out[f] = "not-applicable"
        elif exempt and f in _NOT_APPLICABLE_WHEN_EXEMPT:
            out[f] = "not-applicable"
        elif f == "consulate_district" and _no_consular_application(row):
            out[f] = "not-applicable"
        elif f in REQUIRED_FIELDS:
            out[f] = "missing"
        else:
            out[f] = "optional-empty"
    return out


# A route with no visa cannot have a visa's validity, entry count or
# processing time. Counting those as gaps made a correct record look wrong.
_NOT_APPLICABLE_WHEN_EXEMPT = frozenset({
    "validity_duration", "validity_unit", "entries",
    "processing_min_days", "processing_unit", "application_method",
})


def completeness(row: dict, unpublished: set | None = None) -> float:
    """Share of required fields filled, out of those that could be filled.

    A field the destination does not publish, or that cannot apply to a
    visa-free route, is excluded from the denominator rather than counted as
    a gap. Counting them punished the database for being accurate: blanking a
    validity that France genuinely does not publish made the metric fall.
    """
    st = field_status(row, unpublished)
    need = [f for f in FIELD_ORDER if f in REQUIRED_FIELDS
            and st[f] not in ("not-applicable", "not-published")]
    if not need:
        return 1.0
    return sum(1 for f in need if st[f] == "filled") / len(need)
