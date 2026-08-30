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

import re

# Their field order, exactly as numbered 1-25 in the requirements document.
FIELD_ORDER = (
    "travel_document_type", "travel_document_country", "destination_country",
    "travel_purpose", "visa_requirement", "visa_type_name",
    "validity_duration", "validity_unit", "max_stay_duration", "max_stay_unit",
    "entries", "processing_min_days", "processing_unit",
    "visa_fee_amount", "visa_fee_currency", "application_method",
    "required_documents", "consulate_district", "entry_requirements",
    "special_conditions",
    "data_source", "source_url", "collected_at", "info_validity",
    "confidence_level",
)

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
                          "Service / On-arrival Processing / Other",
    "required_documents": "Core document checklist, comma separated",
    "consulate_district": "Consulate district divisions, if any",
    "entry_requirements": "Other entry requirements besides the visa",
    "special_conditions": "Special policies or restrictions",
    "data_source": "Information source website / organization",
    "source_url": "Specific page link",
    "collected_at": "Data collection date",
    "info_validity": "Policy validity period until",
    "confidence_level": "High / Medium / Low",
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
    """Whether a visa-exempt traveller must still submit a form before travel.

    A mandatory arrival card or electronic authorisation IS an online
    application, and it is the difference between boarding and being turned
    away, so the channel has to say so.
    """
    ac = g.get("arrival_card")
    if isinstance(ac, dict) and ac.get("required"):
        return True
    hay = " ".join(str(x) for x in (
        [g.get("application_channel_detail"), g.get("requirement_detail")]
        + list(g.get("exceptions") or []))).lower()
    return any(k in hay for k in (
        "esta", "e-ta", "eta ", "k-eta", "keta", "arrival card",
        "electronic travel authoris", "electronic travel authoriz",
        "travel authorisation is mandatory", "must be obtained online"))


def _method_from_product(g: dict) -> str | None:
    """Last resort: read the channel off the visa product's own name. Only
    unambiguous words count, so a route stays "Other" rather than guessing."""
    names = " ".join(str((p or {}).get("type") or "")
                     for p in (g.get("visa_products") or []))
    names = f"{names} {g.get('visa_category') or ''}".lower()
    if not names.strip():
        return None
    if any(k in names for k in ("e-visa", "evisa", "electronic travel",
                                "eta-", "e-ta", "online")):
        return "Online Application"
    if any(k in names for k in ("on arrival", "on-arrival", "voa")):
        return "On-arrival Processing"
    if any(k in names for k in ("consular sticker", "consulate", "embassy")):
        return "Embassy Submission"
    return None


def _method_for_channel(channel: str) -> str | None:
    """The engine's channel vocabulary (lowercase, many variants) mapped to
    their five application_method values. Substring rules, because a live
    audit found 96% of records collapsing to "Other" when this was an exact
    uppercase table."""
    c = str(channel or "").lower()
    if not c:
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
    return "Other"


_DISCRETIONARY = ("set by the consulate", "consulate discretion", "as granted",
                  "determined by the consular", "determined at issuance",
                  "not published", "trip duration", "trip dates",
                  "aligned to the itinerary", "per the itinerary")


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
    """Their max_stay_unit enum is Hour | Day only: month- and year-denominated
    stays are converted to days rather than shipping an off-enum unit."""
    if n is None or unit in (None, "Hour", "Day"):
        return n, unit
    factor = {"Month": 30, "Year": 365}.get(unit)
    return (n * factor, "Day") if factor else (n, unit)


def _as_validity_unit(n, unit):
    """Their validity_unit enum has no Hour: sub-day validities round up to
    one day."""
    if unit == "Hour":
        return 1, "Day"
    return n, unit


def _entries(text) -> str | None:
    t = str(text or "").lower()
    if "single" in t or t == "1":
        return "Single"
    if "unlimit" in t:
        return "Unlimited"
    if "multiple" in t or "double" in t or "two" in t:
        return "Multiple"
    return None


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
    if amount == int(amount):
        amount = int(amount)
    if amount == 0:
        # A zero consular fee on a visa that must be applied for is almost
        # always a hallucinated "free": the acceptance audit found sources
        # charging 60-90 EUR where 0 was stored. Zero survives only when the
        # answer itself says the visa is free; otherwise the fee is honestly
        # missing (and the completeness campaign researches it).
        disposition = str(guidance.get("disposition") or "").upper()
        if disposition not in ("VISA_EXEMPT", ""):
            texts = " ".join(str(x or "") for x in (
                product.get("notes"), fee.get("note"), fee.get("notes"),
                guidance.get("requirement_detail"),
                guidance.get("application_channel_detail"))).lower()
            if not any(k in texts for k in ("free", "gratis", "no fee",
                                            "waived", "免费", "免簽費", "免签费")):
                return None, str(currency) if currency else None
    return amount, str(currency) if currency else None


def _confidence(guidance: dict, provenance: dict | None,
                grounded_ok: bool = False) -> str:
    """The spec's own ladder: High is a single official source, complete, no
    conflict (here: a person verified it against a named page). Medium is
    official-source-backed but with gaps. Low is conflicting or NON-OFFICIAL
    ONLY, which by definition includes an answer carrying no source URL at
    all: the model's memory alone is not an official source.

    An answer that ASSERTS VISA PRODUCTS — fees, validity, entry counts — but
    has never been checked against its official page is Low as well. That is
    not caution, it is measurement: an adversarial audit of every such record
    (2026-08-29, 21 route+purpose combinations) confirmed none of them and
    found 19 wrong, including superseded fees, products the destination does
    not issue, and visas demanded of travellers who are visa-exempt. A URL
    attached to an unread claim is not a source."""
    if provenance:
        return "High"
    if not (guidance.get("source_url") or guidance.get("official_portal_url")):
        return "Low"
    c = str(guidance.get("confidence") or "").lower()
    if c == "low":
        return "Low"
    if (guidance.get("visa_products") or []) and not grounded_ok:
        return "Low"
    return "Medium"


# House style for everything a reader sees: no em dashes and no semicolons.
# A semicolon joins two clauses a reader has to hold at once, and an em dash
# hides a pause that a full stop states plainly, so both are rewritten rather
# than banned at the source, which would only push the problem into whichever
# page the next fact is quoted from.
_DASHES = ("\u2014", "\u2013", " -- ")


def _clean_text(v):
    """Rewrite one served string into house style, leaving the facts alone."""
    if not isinstance(v, str) or not v:
        return v
    out = v
    for d in _DASHES:
        out = out.replace(f" {d} ", ", ").replace(d, ", ")
    # A semicolon separating clauses becomes a sentence; one inside a list of
    # short items becomes a comma, which is what it was standing in for.
    out = re.sub(r";\s+(?=[A-Z\u4e00-\u9fff])", ". ", out)
    out = out.replace("; ", ", ").replace(";", ",")
    out = re.sub(r",\s*,", ",", out)
    out = re.sub(r"\s{2,}", " ", out).strip()
    out = re.sub(r"[,\.]+$", "", out)
    return out


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
    pv = g.get("passport_validity")
    if isinstance(pv, str) and pv.strip():
        parts.append(f"Passport: {pv.strip().rstrip('.')}")
    ac = g.get("arrival_card")
    if isinstance(ac, dict) and ac.get("required"):
        name = str(ac.get("name") or "arrival card").strip()
        when = str(ac.get("submission_window") or "").strip()
        parts.append(f"{name} required" + (f" ({when})" if when else ""))
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


def _processing(guidance: dict) -> tuple[float | None, str | None]:
    n, unit = _num_unit(guidance.get("processing_time"))
    if n is None:
        return None, None
    text = str(guidance.get("processing_time") or "").lower()
    return n, ("Working Day" if "working" in text or "business" in text
               else "Calendar Day")


def records_for_route(route: dict, guidance: dict,
                      provenance: dict | None = None,
                      collected_at: str | None = None,
                      valid_until: str | None = None,
                      grounded_ok: bool = False) -> list[dict]:
    """The route's answer as T-Station 25-field records, one per visa
    product; a product-less route (visa-free, or detail still filling)
    yields a single route-level record."""
    g = guidance or {}
    disposition = str(g.get("disposition") or "").upper()
    requirement = _DISPOSITION_TO_REQUIREMENT.get(disposition)
    docs = g.get("required_documents")
    docs = ", ".join(str(d) for d in docs if d) if isinstance(docs, list) else (
        str(docs) if docs else None)
    method = _method_for_channel(g.get("application_channel"))
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
        # plane. Only a route with genuinely nothing to file falls through to
        # "Other", which is their enum's own value for no channel applying.
        method = "Online Application" if _files_something_online(g) else "Other"
    entry_req = g.get("entry_requirements")
    if isinstance(entry_req, list):
        entry_req = ". ".join(str(x) for x in entry_req if x) or None
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
        "visa_type_name": None,
        "validity_duration": None, "validity_unit": None,
        "max_stay_duration": None, "max_stay_unit": None,
        "entries": None,
        "processing_min_days": proc_n, "processing_unit": proc_unit,
        "visa_fee_amount": None, "visa_fee_currency": None,
        "application_method": method,
        "required_documents": docs,
        "consulate_district": _consulate_district(g, route),
        "entry_requirements": (entry_req if isinstance(entry_req, str)
                               else _entry_requirements(g)),
        "special_conditions": exceptions if isinstance(exceptions, str) else None,
        "data_source": (provenance or {}).get("verified_by")
                       or ("Ellis verified route engine" if g else None),
        "source_url": (provenance or {}).get("source_url")
                      or g.get("source_url") or g.get("official_portal_url"),
        "collected_at": ((provenance or {}).get("verified_at")
                         or (collected_at or "")[:10]) or None,
        # The date until which the pipeline actively stands behind this
        # answer: the policy's own end date when known, else the freshness
        # window that triggers the next official-source recheck.
        "info_validity": g.get("policy_valid_until")
                         or ((valid_until or "")[:10] or None),
        "confidence_level": _confidence(g, provenance, grounded_ok),
    }
    products = [p for p in (g.get("visa_products") or [])
                if isinstance(p, dict) and p.get("type")]
    if disposition == "VISA_EXEMPT" or not products:
        row = dict(base)
        if disposition == "VISA_EXEMPT":
            row["visa_type_name"] = "No visa needed"
            if not row["required_documents"]:
                row["required_documents"] = "Valid passport"
            n, unit = _as_stay_unit(*_num_unit(g.get("permitted_stay")))
            if n is None and g.get("permitted_stay_days"):
                n, unit = g.get("permitted_stay_days"), "Day"
            row["max_stay_duration"], row["max_stay_unit"] = n, unit
            # The stay may legitimately be in hours (a transit exemption),
            # but their validity_unit enum has no Hour: route it through the
            # same conversion the product rows use.
            row["validity_duration"], row["validity_unit"] = _as_validity_unit(n, unit)
            row["entries"] = "Unlimited"
            row["visa_fee_amount"], row["visa_fee_currency"] = 0, "USD"
        else:
            row["visa_type_name"] = g.get("visa_category") or None
            n, unit = _as_stay_unit(*_num_unit(g.get("permitted_stay")))
            if n is None and g.get("permitted_stay_days"):
                n, unit = g.get("permitted_stay_days"), "Day"
            row["max_stay_duration"], row["max_stay_unit"] = n, unit
            amt, cur = _fee({}, g)
            row["visa_fee_amount"], row["visa_fee_currency"] = amt, cur
        return [{k: _clean_text(v) for k, v in row.items()}]
    rows = []
    for p in products:
        row = dict(base)
        row["visa_type_name"] = str(p.get("type"))
        n, unit = _num_unit(p.get("validity"),
                            stay_bound=p.get("max_stay_days"))
        if n is None:
            # Definitional: a product NAMED "3-year multiple" or "5年多次"
            # states its own validity; reading it is not a guess.
            m = re.search(r"(\d+)[\s-]*(?:year|年)", str(p.get("type") or ""),
                          re.I)
            if m:
                n, unit = int(m.group(1)), "Year"
            else:
                m = re.search(r"(\d+)[\s-]*(?:month|个月|個月)",
                              str(p.get("type") or ""), re.I)
                if m:
                    n, unit = int(m.group(1)), "Month"
        row["validity_duration"], row["validity_unit"] = _as_validity_unit(n, unit)
        stay = p.get("max_stay_days")
        if stay:
            row["max_stay_duration"], row["max_stay_unit"] = stay, "Day"
        else:
            n2, u2 = _num_unit(g.get("permitted_stay"))
            row["max_stay_duration"], row["max_stay_unit"] = _as_stay_unit(n2, u2)
        # Definitional fallback: "single-entry" / "multiple-entry" in the
        # product's own name states the entries field.
        row["entries"] = _entries(p.get("entry")) or _entries(p.get("type"))
        amt, cur = _fee(p, g)
        row["visa_fee_amount"], row["visa_fee_currency"] = amt, cur
        note = p.get("notes")
        if note:
            row["special_conditions"] = (str(note) if not row["special_conditions"]
                                         else f"{row['special_conditions']}. {note}")
        rows.append({k: _clean_text(v) for k, v in row.items()})
    return rows


def field_status(row: dict) -> dict:
    """Their per-field checklist verdict: filled / missing / optional-empty."""
    out = {}
    for f in FIELD_ORDER:
        v = row.get(f)
        filled = v is not None and str(v).strip() != ""
        out[f] = "filled" if filled else (
            "missing" if f in REQUIRED_FIELDS else "optional-empty")
    return out


def completeness(row: dict) -> float:
    """Share of REQUIRED fields filled — the acceptance metric's numerator
    rule (records with all required fields filled count as complete)."""
    st = field_status(row)
    need = [f for f in FIELD_ORDER if f in REQUIRED_FIELDS]
    return sum(1 for f in need if st[f] == "filled") / len(need)
