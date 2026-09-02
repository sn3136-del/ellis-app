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
                          "Service / On-arrival Processing / Other",
    "required_documents": "Core document checklist, comma separated",
    "consulate_district": "Consulate district divisions, if any",
    "entry_requirements": "Other entry requirements besides the visa",
    "special_conditions": "Special policies or restrictions",
    "data_source": "Information source website / organization",
    "source_url": "Specific page link",
    "collected_at": "Data collection date",
    "info_validity": "Policy validity period until. Where the policy carries no "
                     "published end date, this is the date Ellis warrants the row "
                     "to and rechecks it against its official page",
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


def _confidence(guidance: dict, provenance: dict | None,
                grounded_ok: bool = False, *, complete: bool = True,
                disputed: bool = False) -> str:
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
        # Their §4.2.3 ladder: High is a single official source, complete, and
        # free of conflict. A verified override satisfies the first of those
        # three, never all three on its own, so a record that is still missing
        # a required field or carries a field its own official page disputes
        # is Medium however carefully a person checked the rest.
        if disputed:
            return "Medium"
        return "High" if complete else "Medium"
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
            parts.append(f"No visa. You must still file the {nm} before travel"
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
                    "conditional_visa_free"),
}


def _subcategory_for(product: dict, route_default: str | None,
                     requirement: str | None) -> str | None:
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
    electronic = any(k in name for k in ("e-visa", "evisa", "electronic visa",
                                         "online", "e-tourist", "etourist"))
    authorisation = any(k in name for k in (
        "eta", "esta", "electronic travel", "travel authoris",
        "travel authoriz", "authorisation", "authorization"))
    at_border = "on arrival" in name or "on-arrival" in name

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
        if "visa" in name or "visitor" in name or "permit" in name:
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
    if "visa" in name or "visitor" in name or "permit" in name:
        return SUBCATEGORY["paper_visa"] if "paper_visa" in allowed \
            else SUBCATEGORY[allowed[0]]
    key = _key_of(route_default)
    if key in allowed:
        return SUBCATEGORY[key]
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


def _regrade(row: dict, g: dict, disputed: list | None,
             unpublished: set | None = None) -> dict:
    """Apply their §4.2.3 ladder to the finished row.

    High means a single official source, complete, and free of conflict. Only
    the first of those is knowable before the row exists, which is why a
    record missing a required field, or carrying one its own official page
    disputes, was still being shown as High.
    """
    row = _strip_visa_only_fields(dict(row))
    prov, grounded = row.pop("_prov", False), row.pop("_grounded", False)
    st = field_status(row, unpublished)
    complete = not any(v == "missing" for v in st.values())
    conflicted = bool(disputed) and any(
        f in FIELD_ORDER or f in OVERRIDABLE_HINT for f in (disputed or []))
    row["confidence_level"] = _confidence(g, {"x": 1} if prov else None,
                                          grounded, complete=complete,
                                          disputed=conflicted)
    return row


OVERRIDABLE_HINT = frozenset({"disposition", "government_fee", "permitted_stay",
                              "visa_products", "requirement_detail"})


def records_for_route(route: dict, guidance: dict,
                      provenance: dict | None = None,
                      collected_at: str | None = None,
                      valid_until: str | None = None,
                      grounded_ok: bool = False,
                      disputed_fields: list | None = None) -> list[dict]:
    """The route's answer as T-Station 25-field records, one per visa
    product; a product-less route (visa-free, or detail still filling)
    yields a single route-level record."""
    g = guidance or {}
    # Fields a destination has been checked for and does not publish.
    _unpub = {str(x) for x in (g.get("unpublished_fields") or [])}
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
        "visa_fee_amount": None, "visa_fee_currency": None,
        "application_method": method,
        "required_documents": docs,
        "consulate_district": _consulate_district(g, route),
        "entry_requirements": (entry_req if isinstance(entry_req, str)
                               else _entry_requirements(g)),
        "special_conditions": exceptions if isinstance(exceptions, str) else None,
        "data_source": (provenance or {}).get("verified_by")
                       or ("Ellis verified route engine" if g else None),
        "source_url": _headline_source(g, provenance),
        "collected_at": ((provenance or {}).get("verified_at")
                         or (collected_at or "")[:10]) or None,
        # The date until which the pipeline actively stands behind this
        # answer: the policy's own end date when known, else the freshness
        # window that triggers the next official-source recheck.
        "info_validity": g.get("policy_valid_until")
                         or ((valid_until or "")[:10] or None),
        "confidence_level": _confidence(g, provenance, grounded_ok),
        # §4.2.1's cross-validation, bound one URL at a time. This sits
        # ALONGSIDE the 25 fields rather than inside them: field 22 is a
        # single source_url by their dictionary and stays that way, so the
        # export shape is unchanged. What was missing was any slot at all for
        # the second and third page a route was checked against.
        "corroborating_sources": _corroborating(g),
        "_prov": bool(provenance), "_grounded": grounded_ok,
        "_unpublished": sorted(_unpub),
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
            # A product-less route states no separate validity window, so the
            # granted stay is its honest bound, exactly as the product rows
            # and the visa-free branch already read it. Without this the
            # validity column sat empty on every product-less visa answer.
            row["validity_duration"], row["validity_unit"] = \
                _as_validity_unit(n, unit)
            amt, cur = _fee({}, g)
            row["visa_fee_amount"], row["visa_fee_currency"] = amt, cur
        return [_regrade({k: _clean_text(v) for k, v in row.items()}, g, disputed_fields, _unpub)]
    rows = []
    for p in products:
        row = dict(base)
        row["visa_type_name"] = str(p.get("type"))
        row["visa_requirement_detail"] = _subcategory_for(
            p, base.get("visa_requirement_detail"), requirement)
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
        if n is None and p.get("max_stay_days"):
            # Same rule as the _DISCRETIONARY markers, generalised: when a
            # product publishes its stay but no separate validity window
            # (visas issued at the border, Schengen C stickers cut to the
            # trip), the granted stay is the validity's honest lower bound,
            # which is how Trip.com's own display standard writes these.
            # A product whose validity genuinely exceeds its stay (a
            # multi-year visa) always names that validity and never lands
            # here.
            n, unit = int(p["max_stay_days"]), "Day"
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
        rows.append(_regrade({k: _clean_text(v) for k, v in row.items()}, g, disputed_fields, _unpub))
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
        row = {"url": url, "quote": _clean_text(item.get("quote"))}
        for k in ("authority", "checked_at", "agrees"):
            if item.get(k) not in (None, ""):
                row[k] = item[k]
        out.append(row)
    return out


def field_status(row: dict, unpublished: set | None = None) -> dict:
    """Their per-field checklist verdict, with the three kinds of blank kept apart.

    A blank cell is not one thing. "Validity" on a visa-free route is not
    missing, there is no visa to have a validity. "Validity" on a French
    Schengen visa is not missing either, the consulate sets it per applicant
    and France publishes no figure. Only the third kind, a value that exists
    somewhere official and nobody has fetched yet, should count against
    completeness, and only that one should read as a gap to an operator.
    """
    unpublished = set(unpublished or ()) | set(row.get("_unpublished") or ())
    out = {}
    exempt = str(row.get("visa_requirement") or "") == "Visa-free"
    for f in FIELD_ORDER:
        v = row.get(f)
        if v not in (None, "", []):
            out[f] = "filled"
        elif f in unpublished:
            out[f] = "not-published"
        elif exempt and f in _NOT_APPLICABLE_WHEN_EXEMPT:
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
