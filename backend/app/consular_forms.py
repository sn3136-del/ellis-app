"""Consular application forms Ellis fills FOR the applicant.

Most of the world's tourist visas cannot be filed online: a Schengen visa, a
US B1/B2, and many consular routes are decided in person, and the applicant
arrives at the consulate carrying a completed paper/online form. Ellis cannot
submit those — but it can do the part that actually takes people hours: fill
the form correctly from what they already told Ellis and what their uploaded
passport says, then hand them a print-ready form plus exact instructions.

Two rules make this trustworthy, and both are enforced here rather than
trusted to a caller:

  1. NOTHING IS INVENTED. Every rendered value comes from the applicant's own
     answers (or a document Ellis read). A field with no answer renders as an
     explicit blank the applicant must complete — never a plausible guess. A
     consular form is signed under penalty of perjury; a fabricated value
     would be the applicant's lie, in their name.
  2. MISSING REQUIRED FIELDS ARE REPORTED, not silently dropped, so the case
     can ask the applicant for them before they travel to an appointment with
     an incomplete form.

The output is a deterministic, hashable artifact (the same answers always
produce the same bytes), stored as a case document like every other file.
"""
from __future__ import annotations

from .providers import pdfgen

# ---------------------------------------------------------------- field maps
# Each entry: (form label, ellis answer key, required?). The label is the
# wording the official form itself uses, so the applicant can transcribe or
# check it against the real document field by field.

SCHENGEN_UNIFORM = [
    ("1. Surname (Family name)", "surname", True),
    ("2. Surname at birth (Former family name(s))", "surname_at_birth", False),
    ("3. First name(s) (Given name(s))", "given_names", True),
    ("4. Date of birth (day-month-year)", "birth_date", True),
    ("5. Place of birth", "place_of_birth", True),
    ("6. Country of birth", "country_of_birth", False),
    ("7. Current nationality", "nationality", True),
    ("8. Sex", "sex", True),
    ("9. Marital status", "marital_status", False),
    ("13. Type of travel document", "travel_document_type", True),
    ("14. Number of travel document", "passport_number", True),
    ("15. Date of issue", "passport_issue_date", True),
    ("16. Valid until", "passport_expiry_date", True),
    ("17. Issued by (country)", "issuing_country", True),
    ("18. Applicant's home address and e-mail address", "address_line1", True),
    ("    City", "address_city", True),
    ("    Postal code", "address_postal_code", False),
    ("    Country", "address_country", True),
    ("    E-mail address", "email", True),
    ("    Telephone number", "phone", True),
    # The printed form draws an asterisk beside both (its fields 21 and 22):
    # they are REQUIRED, and calling them optional here meant Ellis never
    # asked and the applicant carried a blank the consulate rejects.
    ("19. Current occupation", "occupation", True),
    ("20. Employer and employer's address and telephone number",
     "employer", True),
    ("21. Purpose(s) of the journey", "travel_purpose", True),
    ("23. Member State of main destination", "destination_country", True),
    ("26. Intended date of arrival in the Schengen area", "arrival_date", True),
    ("27. Intended date of departure from the Schengen area", "departure_date", True),
    ("31. Surname and first name of the inviting person / hotel(s)",
     "accommodation", True),
    ("    Address of the inviting person / hotel(s)", "accommodation_address", False),
    # Derived at build time (the applicant's city + the day the form is
    # produced), never asked: see derived_answers.
    ("36. Place and date", "signature_place_date", False),
]

# The DS-160 is submitted on the US Department of State's own site (CEAC) —
# Ellis prepares the exact data the applicant transcribes there, in the site's
# own section order, so the online form takes minutes instead of an evening.
DS160_PREP = [
    ("Personal Information 1 - Surnames", "surname", True),
    ("Personal Information 1 - Given Names", "given_names", True),
    ("Personal Information 1 - Full Name in Native Alphabet", "full_name_native", False),
    ("Personal Information 1 - Sex", "sex", True),
    ("Personal Information 1 - Marital Status", "marital_status", True),
    ("Personal Information 1 - Date of Birth", "birth_date", True),
    ("Personal Information 1 - City of Birth", "place_of_birth", True),
    ("Personal Information 1 - Country/Region of Birth", "country_of_birth", False),
    ("Personal Information 2 - Country/Region of Origin (Nationality)",
     "nationality", True),
    ("Personal Information 2 - National Identification Number", "national_id", False),
    ("Address and Phone - Street Address (Line 1)", "address_line1", True),
    ("Address and Phone - City", "address_city", True),
    ("Address and Phone - State/Province", "address_region", False),
    ("Address and Phone - Postal Zone/ZIP Code", "address_postal_code", False),
    ("Address and Phone - Country/Region", "address_country", True),
    ("Address and Phone - Primary Phone Number", "phone", True),
    ("Address and Phone - Email Address", "email", True),
    ("Passport - Passport/Travel Document Number", "passport_number", True),
    ("Passport - Passport Book Number", "passport_book_number", False),
    ("Passport - Country/Authority that Issued Passport", "issuing_country", True),
    ("Passport - Issuance Date", "passport_issue_date", True),
    ("Passport - Expiration Date", "passport_expiry_date", True),
    ("Travel - Purpose of Trip to the U.S.", "travel_purpose", True),
    ("Travel - Intended Date of Arrival", "arrival_date", True),
    ("Travel - Intended Length of Stay", "days_of_stay", False),
    ("Travel - Address Where You Will Stay in the U.S.", "accommodation", True),
    ("Work/Education - Primary Occupation", "occupation", False),
    ("Work/Education - Present Employer or School Name", "employer", False),
    ("Work/Education - Employer/School Address", "employer_address", False),
    ("Work/Education - Monthly Income (local currency)", "monthly_income", False),
    # --- Travel section, continued: CEAC asks these on the same screens, and
    # an applicant who has them ready fills that screen in one pass.
    ("Travel - Person/Entity Paying for Your Trip", "trip_payer", False),
    ("Travel - Specific Travel Plans (Yes/No)", "has_specific_plans", False),
    ("Travel - Intended Date of Departure from the U.S.", "departure_date", False),
    ("Travel Companions - Are you travelling with anyone?", "travelling_with", False),
    # --- Previous US travel: factual history the applicant supplies once.
    ("Previous U.S. Travel - Have you ever been to the U.S.?", "been_to_us_before", False),
    ("Previous U.S. Travel - Date of Last Arrival", "last_us_arrival_date", False),
    ("Previous U.S. Travel - Length of Last Stay", "last_us_stay_length", False),
    ("Previous U.S. Travel - Previous U.S. Visa Number", "previous_us_visa_number", False),
    # --- U.S. point of contact: CEAC requires one; a hotel is acceptable.
    ("U.S. Point of Contact - Name or Organisation", "us_contact_name", False),
    ("U.S. Point of Contact - Address", "us_contact_address", False),
    ("U.S. Point of Contact - Phone", "us_contact_phone", False),
    ("U.S. Point of Contact - Relationship to You", "us_contact_relationship", False),
    # --- Family: names only. Anything about a relative's status is theirs to
    # state, not Ellis's to infer.
    ("Family - Father's Surnames", "father_surname", False),
    ("Family - Father's Given Names", "father_given_names", False),
    ("Family - Mother's Surnames", "mother_surname", False),
    ("Family - Mother's Given Names", "mother_given_names", False),
    ("Family - Spouse's Full Name (if married)", "spouse_full_name", False),
]

# CEAC's SECURITY AND BACKGROUND questions are deliberately absent from the
# list above. They ask whether the applicant has been arrested, deported,
# involved in genocide. Those are answered under penalty of perjury by the
# person they are about — preparing an answer, even a blank prompt shaped like
# one, would invite someone to click through the most consequential screen of
# the form. Ellis states plainly that the applicant answers those themselves.

# Three of the world's highest-volume destinations moved their application
# ONLINE and abolished the paper form entirely: China to COVA, the UK to
# gov.uk, India to indianvisaonline. There is no blank PDF to fill for any of
# them — so Ellis does what it does for the DS-160: prepares every value in the
# order the portal's own screens ask for it, and the applicant transcribes a
# completed sheet instead of hunting through their documents.

CHINA_COVA_PREP = [
    ("Section 1 - Surname (as in passport)", "surname", True),
    ("Section 1 - Given names (as in passport)", "given_names", True),
    ("Section 1 - Name in Chinese characters (if any)", "full_name_native", False),
    ("Section 1 - Sex", "sex", True),
    ("Section 1 - Date of birth", "birth_date", True),
    ("Section 1 - Place of birth (city, country)", "place_of_birth", True),
    ("Section 1 - Current nationality", "nationality", True),
    ("Section 1 - Marital status", "marital_status", True),
    ("Section 1 - National identity number", "national_id", False),
    ("Section 2 - Passport number", "passport_number", True),
    ("Section 2 - Passport issue date", "passport_issue_date", True),
    ("Section 2 - Passport expiry date", "passport_expiry_date", True),
    ("Section 2 - Passport issuing authority/country", "issuing_country", True),
    ("Section 2 - Current occupation", "occupation", True),
    ("Section 2 - Employer / school name", "employer", False),
    ("Section 2 - Employer / school address", "employer_address", False),
    ("Section 2 - Home address", "address_line1", True),
    ("Section 2 - City", "address_city", True),
    ("Section 2 - Country", "address_country", True),
    ("Section 2 - Mobile phone", "phone", True),
    ("Section 2 - Email", "email", True),
    ("Section 3 - Major purpose of visit", "travel_purpose", True),
    ("Section 3 - Intended date of entry", "arrival_date", True),
    ("Section 3 - Intended longest stay (days)", "days_of_stay", False),
    ("Section 3 - Itinerary / where you will stay in China",
     "accommodation", True),
    ("Section 3 - Address in China", "accommodation_address", False),
    ("Section 3 - Who is paying for the trip", "trip_payer", False),
    ("Section 4 - Emergency contact name", "emergency_contact_name", False),
    ("Section 4 - Emergency contact phone", "emergency_contact_phone", False),
]

UK_ONLINE_PREP = [
    ("About you - Given names (as in passport)", "given_names", True),
    ("About you - Family name (as in passport)", "surname", True),
    ("About you - Sex", "sex", True),
    ("About you - Relationship status", "marital_status", True),
    ("About you - Date of birth", "birth_date", True),
    ("About you - Country of birth", "country_of_birth", True),
    ("About you - Place of birth", "place_of_birth", True),
    ("About you - Nationality", "nationality", True),
    ("Passport - Passport number", "passport_number", True),
    ("Passport - Issuing authority/country", "issuing_country", True),
    ("Passport - Date of issue", "passport_issue_date", True),
    ("Passport - Date of expiry", "passport_expiry_date", True),
    ("Contact - Home address", "address_line1", True),
    ("Contact - City/town", "address_city", True),
    ("Contact - Postal code", "address_postal_code", False),
    ("Contact - Country", "address_country", True),
    ("Contact - Telephone number", "phone", True),
    ("Contact - Email address", "email", True),
    ("Travel - Reason for visiting the UK", "travel_purpose", True),
    ("Travel - Date you plan to arrive", "arrival_date", True),
    ("Travel - Date you plan to leave", "departure_date", True),
    ("Travel - Where you will stay in the UK", "accommodation", True),
    ("Travel - Address where you will stay", "accommodation_address", False),
    ("Money - Your job title", "occupation", False),
    ("Money - Employer name", "employer", False),
    ("Money - Employer address", "employer_address", False),
    ("Money - Your monthly income", "monthly_income", False),
    ("Money - Who is paying for this trip", "trip_payer", False),
    ("Family - Father's given names", "father_given_names", False),
    ("Family - Father's family name", "father_surname", False),
    ("Family - Mother's given names", "mother_given_names", False),
    ("Family - Mother's family name", "mother_surname", False),
]

INDIA_ONLINE_PREP = [
    ("Applicant Details - Surname", "surname", True),
    ("Applicant Details - Given name(s)", "given_names", True),
    ("Applicant Details - Date of birth", "birth_date", True),
    ("Applicant Details - Town/city of birth", "place_of_birth", True),
    ("Applicant Details - Country of birth", "country_of_birth", True),
    ("Applicant Details - Citizenship/national ID number", "national_id", False),
    ("Applicant Details - Nationality", "nationality", True),
    ("Applicant Details - Sex", "sex", True),
    ("Applicant Details - Marital status", "marital_status", True),
    ("Passport Details - Passport number", "passport_number", True),
    ("Passport Details - Place of issue", "issuing_country", True),
    ("Passport Details - Date of issue", "passport_issue_date", True),
    ("Passport Details - Date of expiry", "passport_expiry_date", True),
    ("Applicant Address - House no./street", "address_line1", True),
    ("Applicant Address - City", "address_city", True),
    ("Applicant Address - State/province", "address_region", False),
    ("Applicant Address - Postal code", "address_postal_code", False),
    ("Applicant Address - Country", "address_country", True),
    ("Applicant Address - Phone", "phone", True),
    ("Applicant Address - Email", "email", True),
    ("Family Details - Father's name", "father_given_names", False),
    ("Family Details - Father's nationality", "father_nationality", False),
    ("Family Details - Mother's name", "mother_given_names", False),
    ("Family Details - Spouse's name (if married)", "spouse_full_name", False),
    ("Profession - Present occupation", "occupation", True),
    ("Profession - Employer name", "employer", False),
    ("Profession - Employer address", "employer_address", False),
    ("Visa Details - Type of visa / purpose", "travel_purpose", True),
    ("Visa Details - Expected date of arrival", "arrival_date", True),
    ("Visa Details - Expected date of departure", "departure_date", False),
    ("Visa Details - Duration of visa (days)", "days_of_stay", False),
    ("Visa Details - Places to be visited", "accommodation", False),
    ("Previous Visit - Have you visited India before?", "been_to_india_before", False),
    ("Reference in India - Name", "india_reference_name", False),
    ("Reference in India - Address", "india_reference_address", False),
    ("Reference in India - Phone", "india_reference_phone", False),
]

JAPAN_VJW_PREP = [
    # Visit Japan Web covers ENTRY (immigration + customs), not a visa — most
    # passports need no visa for Japan tourism, and VJW is how the arrival
    # paperwork moves online. Sections follow the site's own flow: register a
    # trip, the disembarkation card, then the customs declaration.
    ("Trip Registration - Surname (as in passport)", "surname", True),
    ("Trip Registration - Given names (as in passport)", "given_names", True),
    ("Trip Registration - Date of birth", "birth_date", True),
    ("Trip Registration - Nationality", "nationality", True),
    ("Trip Registration - Passport number", "passport_number", True),
    ("Trip Registration - Passport expiry date", "passport_expiry_date", True),
    ("Trip Registration - Arrival date in Japan", "arrival_date", True),
    ("Trip Registration - Arrival flight number", "flight_number", True),
    ("Trip Registration - Intended length of stay (days)", "stay_length_days", True),
    ("Disembarkation Card - Purpose of visit", "travel_purpose", True),
    ("Disembarkation Card - Accommodation name in Japan", "accommodation", True),
    ("Disembarkation Card - Accommodation address in Japan", "accommodation_address", True),
    ("Disembarkation Card - Accommodation phone in Japan", "accommodation_phone", True),
    ("Disembarkation Card - Ever deported or refused entry to Japan?", "deported_before", True),
    ("Disembarkation Card - Any criminal conviction (any country)?", "criminal_record", True),
    ("Disembarkation Card - Carrying controlled drugs, guns or swords?", "restricted_items", True),
    ("Customs Declaration - Goods exceeding the duty-free allowance?", "customs_over_allowance", True),
    ("Customs Declaration - Commercial goods or samples?", "commercial_goods", True),
    ("Customs Declaration - Cash or securities over 1,000,000 yen?", "cash_over_1m_jpy", True),
    ("Customs Declaration - Unaccompanied baggage sent separately?", "unaccompanied_baggage", True),
    ("Customs Declaration - Email for the QR codes", "email", True),
]

# `date_format` is the order THIS form asks for, in dates.to_portal tokens.
# An official blank gets the order it prints beside the field — the Schengen
# form says "(day-month-year)" and means it. A preparation sheet is read by a
# person retyping into a website, so it spells the month out: nobody can
# mis-key "13 June 1988", and everybody eventually mis-keys 06/13 vs 13/06.
FORMS = {
    "schengen_uniform": {
        "title": "Schengen Uniform Visa Application",
        "fields": SCHENGEN_UNIFORM,
        "date_format": "DD-MM-YYYY",
        "submission": "in_person",
        "note": ("Print this form, sign it by hand (fields 37 and 38), and "
                 "bring it to your appointment with your passport, photo and "
                 "supporting documents. Ellis cannot submit a Schengen visa "
                 "application: it is decided in person, with biometrics."),
    },
    "japan_vjw_prep": {
        "title": "Visit Japan Web Preparation Sheet (arrival, not a visa)",
        "fields": JAPAN_VJW_PREP,
        "date_format": "DD MONTH YYYY",
        "submission": "applicant_online",
        "note": ("Complete Visit Japan Web at vjw-lp.digital.go.jp before you "
                 "fly — it needs an account, so you register and enter these "
                 "answers yourself, then show the QR codes at immigration and "
                 "customs. Skipping it is allowed: paper cards on arrival "
                 "remain accepted. This is entry paperwork; if your passport "
                 "is visa-exempt for Japan, there is no visa to apply for."),
    },
    "china_cova_prep": {
        "title": "China Visa (COVA) Preparation Sheet",
        "fields": CHINA_COVA_PREP,
        "date_format": "DD MONTH YYYY",
        "submission": "applicant_online",
        "note": ("Fill these into COVA at cova.cs.mfa.gov.cn, then print the "
                 "completed form it generates and take it to the visa centre "
                 "with your passport and photo. China abolished the paper "
                 "form: COVA generates the only version a consulate accepts, "
                 "so Ellis prepares your answers rather than a form."),
    },
    "uk_online_prep": {
        "title": "UK Visa Online Application Preparation Sheet",
        "fields": UK_ONLINE_PREP,
        "date_format": "DD MONTH YYYY",
        "submission": "applicant_online",
        "note": ("Apply at gov.uk and copy these values into the matching "
                 "screens. You then attend a visa application centre for "
                 "fingerprints and a photograph only — the application itself "
                 "is already submitted online by then. Ellis never submits it "
                 "for you: you declare its truth as you send it."),
    },
    "india_online_prep": {
        "title": "India e-Visa / Regular Visa Preparation Sheet",
        "fields": INDIA_ONLINE_PREP,
        "date_format": "DD MONTH YYYY",
        "submission": "applicant_online",
        "note": ("Apply at indianvisaonline.gov.in and copy these values into "
                 "the matching sections. Save your temporary application ID "
                 "at every step — the site times out and loses unsaved work. "
                 "You sign the declaration yourself when you submit."),
    },
    "ds160_prep": {
        "title": "US DS-160 Preparation Sheet",
        "fields": DS160_PREP,
        "date_format": "DD MONTH YYYY",
        "submission": "applicant_online",
        "note": ("Sign in at ceac.state.gov/genniv and copy these values into "
                 "the matching DS-160 sections — they are listed in CEAC's own "
                 "order, so each screen fills in one pass. The DS-160 exists "
                 "only online; there is no paper original for Ellis to fill. "
                 "You answer the Security and Background questions yourself, "
                 "and you sign the form electronically: it is sworn under "
                 "penalty of perjury, so only you may do either."),
    },
}

# Which form a destination's consular route needs. Every Schengen state shares
# the one uniform form; the US has its own.
_SCHENGEN = {"AUT", "BEL", "BGR", "HRV", "CZE", "DNK", "EST", "FIN", "FRA",
             "DEU", "GRC", "HUN", "ISL", "ITA", "LVA", "LIE", "LTU", "LUX",
             "MLT", "NLD", "NOR", "POL", "PRT", "ROU", "SVK", "SVN", "ESP",
             "SWE", "CHE"}


def form_for_destination(dest_iso3: str) -> str | None:
    """The consular form this destination's in-person route uses, or None when
    Ellis has no verified form for it (honest: no form is better than the
    wrong country's form)."""
    d = (dest_iso3 or "").upper()
    if d in _SCHENGEN:
        return "schengen_uniform"
    # Destinations that abolished the paper form: what Ellis can give is a
    # complete set of answers in the portal's own screen order.
    return {"USA": "ds160_prep", "CHN": "china_cova_prep", "JPN": "japan_vjw_prep",
            "GBR": "uk_online_prep", "IND": "india_online_prep"}.get(d)


BLANK = "________________________"

# Passport-profile key -> form answer key. The applicant uploads their passport
# biodata page; Ellis reads it once (checksum-validated MRZ) and those verified
# identity values fill the form, so nobody retypes a passport number by hand.
_PASSPORT_TO_ANSWER = {
    "surname": "surname",
    "given_names": "given_names",
    "full_name": "full_name",
    "birth_date": "birth_date",
    "sex": "sex",
    "nationality": "nationality",
    "passport_number": "passport_number",
    "issuing_country": "issuing_country",
    "expiry_date": "passport_expiry_date",
}


def answers_from_documents(answers: dict, passport_profile: dict | None) -> dict:
    """Merge verified passport-document values into the applicant's answers.

    The applicant's OWN typed answer always wins — Ellis never overwrites what
    a person explicitly told it with a machine reading. A document value is
    used only to fill a gap, and only when the reader did not flag it as
    needing confirmation (a low-confidence scan is a question, not a fact)."""
    merged = dict(answers or {})
    for doc_key, ans_key in _PASSPORT_TO_ANSWER.items():
        if merged.get(ans_key) not in (None, ""):
            continue                      # the applicant already answered
        entry = (passport_profile or {}).get(doc_key) or {}
        value = entry.get("value") if isinstance(entry, dict) else entry
        if isinstance(entry, dict) and entry.get("needs_confirmation"):
            continue                      # unverified reading is never a fact
        if value not in (None, ""):
            merged[ans_key] = str(value)
    return merged


# ------------------------------------------------------- writing the values
# Ellis stores answers in ITS canonical vocabulary: dates as ISO, countries as
# ISO-3, choices as snake_case keys. None of that is what a consular officer
# reads. The Schengen blank prints "(day-month-year)" beside field 4, so
# "1988-06-13" written there is not merely ugly — it is the wrong order on a
# form somebody signs under penalty of perjury, and "CHN" is not what field 7
# asks for. This is the one place that translates a stored answer into the
# words the form itself uses, and both the printed sheet and the filled PDF go
# through it so they can never disagree.

# Answer keys that hold an ISO-3 country code rather than free text.
_COUNTRY_KEYS = frozenset({
    "nationality", "issuing_country", "address_country", "country_of_birth",
    "destination_country", "employer_country",
})

# Answer keys that hold a snake_case choice from a fixed list. Only these are
# re-worded — a free-text field is written exactly as the applicant typed it.
_CODED_KEYS = frozenset({
    "travel_document_type", "travel_purpose", "marital_status",
    "costs_covered_by", "means_of_support", "prior_refusals",
    "has_specific_plans", "travelling_with", "been_to_us_before",
})

_SEX = {"F": "Female", "M": "Male", "X": "X"}


def _country_name(code: str) -> str:
    """The country's own name for an ISO code, or the code unchanged when the
    registry does not know it — an unknown code is still true, a guessed name
    would not be."""
    c = str(code or "").strip()
    if not c:
        return ""
    try:
        from .visa_snapshot.registry import _country_index
        entry = (_country_index() or {}).get(c.upper()) or {}
        return str(entry.get("name") or "").strip() or c
    except Exception:  # noqa: BLE001 — registry optional; the code is honest
        return c


def form_value(form_key: str, key: str, raw) -> str:
    """The applicant's stored answer, written the way THIS form asks for it."""
    value = str(raw).strip() if raw not in (None, "", []) else ""
    if not value:
        return ""
    from . import dates
    if dates.date_kind_for_key(key) and dates.is_iso(value):
        fmt = (FORMS.get(form_key) or {}).get("date_format") or ""
        return dates.to_portal(value, fmt) or value
    if key in _COUNTRY_KEYS:
        return _country_name(value)
    if key == "sex":
        return _SEX.get(value.upper(), value)
    if key in _CODED_KEYS and value == value.lower() and " " not in value:
        return value.replace("_", " ").capitalize()
    return value


def prepare(form_key: str, answers: dict) -> dict:
    """Fill a consular form from the applicant's OWN answers.

    Returns {form_key, title, lines, filled, missing_required, note}. `lines`
    is the print-ready body; `missing_required` names every required field the
    applicant has not answered, so the case can ask instead of letting them
    arrive with an incomplete form. No value is ever guessed or defaulted.
    """
    spec = FORMS.get(form_key)
    if spec is None:
        raise ValueError(f"unknown consular form {form_key!r}")
    answers = answers or {}
    lines: list[str] = []
    missing: list[str] = []
    filled = 0
    for label, key, required in spec["fields"]:
        value = form_value(form_key, key, answers.get(key))
        if value:
            filled += 1
        else:
            if required:
                missing.append(key)
            value = BLANK
        lines.append(f"{label}: {value}")
    return {"form_key": form_key, "title": spec["title"], "lines": lines,
            "filled": filled, "total": len(spec["fields"]),
            "missing_required": missing, "note": spec["note"],
            "submission": spec["submission"]}


# ------------------------------------------------------- official templates
# The ONLY way to produce a form a consulate will accept is to fill the
# government's own blank PDF. Ellis never draws a look-alike of an official
# form: a home-made facsimile is not the official document, and presenting one
# at a consulate is at best a rejection and at worst treated as a forgery.
#
# Drop the official blank (downloaded from the issuing authority) at
#   data/reference/forms/<form_key>.pdf
# and Ellis fills its real AcroForm fields. Without the official blank, Ellis
# produces the preparation sheet instead and says so plainly.
TEMPLATE_DIRNAME = "forms"


def _template_path(form_key: str):
    from pathlib import Path
    from .config import settings
    base = getattr(settings(), "data_dir", "") or ""
    root = Path(base) if base else Path(__file__).resolve().parents[2] / "data"
    return root / "reference" / TEMPLATE_DIRNAME / f"{form_key}.pdf"


def official_template_available(form_key: str) -> bool:
    return _template_path(form_key).is_file()


def template_field_names(form_key: str) -> list[str]:
    """The AcroForm field names in the official blank — used to build the
    mapping from Ellis answers to the government's own field ids."""
    path = _template_path(form_key)
    if not path.is_file():
        return []
    from pypdf import PdfReader
    try:
        return sorted((PdfReader(str(path)).get_fields() or {}).keys())
    except Exception:  # noqa: BLE001 — an unreadable template yields nothing
        return []


def _map_path(form_key: str):
    return _template_path(form_key).with_suffix(".map.json")


def load_checkbox_map(form_key: str) -> dict:
    """Checkbox groups: {ellis_answer_key: {answer_value: pdf_field_name}}.

    A consular form asks most of its questions as tick-boxes — marital status,
    purpose of travel, who is paying. Ellis asks the applicant the plain
    question and ticks the box THEY chose, so they receive a form that needs
    nothing further, rather than one they must finish by hand.
    """
    import json
    path = _map_path(form_key)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except ValueError:
        return {}
    groups = (data or {}).get("checkboxes") or {}
    out = {}
    for key, options in groups.items():
        if isinstance(options, dict):
            out[str(key)] = {str(k): str(v) for k, v in options.items()
                             if isinstance(v, str)}
    return out


# Applicant-facing wording for the form's WRITTEN fields — the counterpart of
# CHECKBOX_LABELS below, which covers only the tick-boxes.
#
# Ellis stores answers under its own keys, and those keys had been reaching
# people's eyes: the packet cover printed "Form answers Ellis does not have:
# place_of_birth / phone / accommodation", and the case screen listed the same
# three. A key is a name for a column, not a question anybody can answer. Each
# entry here is what Ellis ASKS, plus the kind of box to ask it in and a line
# of help where the form's meaning is not obvious from its label.
FIELD_QUESTIONS = {
    "surname_at_birth": {
        "label": "Surname at birth, if different from now",
        "type": "text",
        "help": "Leave blank if your family name has never changed."},
    "place_of_birth": {
        "label": "Which city were you born in?",
        "type": "text",
        "help": "The city exactly as it appears on your passport."},
    "country_of_birth": {
        "label": "Which country were you born in?",
        "type": "country"},
    "phone": {
        "label": "Your phone number",
        "type": "tel",
        "help": "Include the country code, so the consulate can reach you."},
    "address_postal_code": {
        "label": "Your postal code",
        "type": "text"},
    "occupation": {
        "label": "Your current occupation",
        "type": "text",
        "help": "What you do for a living. Write 'Student', 'Retired' or "
                "'Unemployed' if that is the case."},
    "employer": {
        "label": "Your employer, and their address and phone number",
        "type": "textarea",
        "help": "If you are a student, your school's name and address instead."},
    "accommodation": {
        "label": "Where will you stay?",
        "type": "text",
        "help": "The hotel's name, or the name of the person inviting you."},
    "accommodation_address": {
        "label": "The address you will stay at",
        "type": "textarea"},
    "national_id": {
        "label": "National identity number, if you have one",
        "type": "text"},
}


def field_questions(form_key: str, answers: dict | None = None,
                    *, only_missing: bool = True) -> list[dict]:
    """The written questions THIS form still needs, in the form's own order.

    Every field the form declares, matched to applicant-facing wording, with
    the answer already on file when there is one. `required` is the form's own
    requirement — the same flag prepare() reports as missing_required — so a
    caller can gate on the questions that would otherwise leave the downloaded
    form incomplete, and ask the rest as optional.
    """
    spec = FORMS.get(form_key)
    if spec is None:
        return []
    answers = answers or {}
    checkbox_keys = set(load_checkbox_map(form_key))
    out = []
    for label, key, required in spec["fields"]:
        if key in checkbox_keys:
            continue          # asked as a choice by checkbox_questions instead
        meta = FIELD_QUESTIONS.get(key)
        if meta is None:
            continue          # derived from the passport or the trip: never asked
        answered = answers.get(key)
        if only_missing and answered not in (None, ""):
            continue
        out.append({"key": key, "label": meta["label"], "type": meta["type"],
                    "help": meta.get("help", ""), "required": bool(required),
                    "answer": answered if answered not in (None, "") else "",
                    "form_label": label.strip()})
    return out


# Where each answer comes from. A required field with no question behind it
# would be a dead end: the screen would say something is missing and offer the
# applicant no way to supply it. Saying WHICH source is short is the difference
# between "answer this" and "upload your passport biodata page".
_FROM_PASSPORT = frozenset({
    "surname", "given_names", "full_name", "birth_date", "sex", "nationality",
    "issuing_country", "passport_number", "passport_issue_date",
    "passport_expiry_date",
})
_FROM_TRIP = frozenset({
    "destination_country", "arrival_date", "departure_date", "travel_purpose",
})


def source_for(form_key: str, key: str) -> str:
    """'ask' | 'passport' | 'trip' | 'intake' — who can close this gap."""
    if key in FIELD_QUESTIONS or key in load_checkbox_map(form_key):
        return "ask"
    if key in _FROM_PASSPORT:
        return "passport"
    if key in _FROM_TRIP:
        return "trip"
    return "intake"


def missing_breakdown(form_key: str, answers: dict) -> list[dict]:
    """Every REQUIRED field still blank, in the applicant's words, with the
    place its answer would come from. The raw list of keys says what is wrong;
    this says what to do about it."""
    prepared = prepare(form_key, answers)
    return [{"key": k, "label": label_for(k), "source": source_for(form_key, k),
             "required": True}
            for k in prepared["missing_required"]]


def official_form_fillable(form_key: str, answers: dict) -> bool:
    """Would the official blank actually come out FILLED for these answers?

    The same three conditions fill_official_template returns None on — no
    template, no field map, nothing to write — asked without rendering a PDF.
    Distinguishing this from 'the blank exists' matters because the screen
    promises the applicant a filled government form, and a preparation sheet
    is a different thing that must not be described as one.
    """
    if not official_template_available(form_key):
        return False
    mapping = load_field_map(form_key)
    if not mapping:
        return False
    answers = answers or {}
    if any(form_value(form_key, k, answers.get(k)) for k in mapping.values()):
        return True
    return any(answers.get(g) not in (None, "", [])
               for g in load_checkbox_map(form_key))


def label_for(key: str) -> str:
    """Applicant-facing wording for one answer key — never the raw key."""
    meta = FIELD_QUESTIONS.get(key)
    if meta:
        return meta["label"]
    meta = (CHECKBOX_LABELS.get("schengen_uniform") or {}).get(key)
    if meta and meta.get("label"):
        return meta["label"]
    return str(key or "").replace("_", " ").capitalize()


def checkbox_questions(form_key: str) -> list[dict]:
    """The tick-box questions this form needs, as plain choices Ellis can ask.
    The applicant answers once and the correct box is ticked for them."""
    labels = CHECKBOX_LABELS.get(form_key, {})
    out = []
    for key, options in load_checkbox_map(form_key).items():
        meta = labels.get(key) or {}
        out.append({
            "key": key,
            "label": meta.get("label") or key.replace("_", " ").capitalize(),
            "multi": bool(meta.get("multi")),
            "options": [{"value": v,
                         "label": (meta.get("options") or {}).get(v)
                         or v.replace("_", " ").capitalize()}
                        for v in options],
        })
    return out


# Applicant-facing wording for the tick-box questions. The form's own phrasing
# is legalese ("Cost of travelling and living during the applicant's stay is
# covered by"); the applicant is asked something they can actually answer.
CHECKBOX_LABELS = {
    "schengen_uniform": {
        "marital_status": {"label": "Marital status", "options": {
            "single": "Single", "married": "Married",
            "registered_partnership": "Registered partnership",
            "divorced": "Divorced", "widowed": "Widowed", "other": "Other"}},
        "travel_purpose": {"label": "Main purpose of your journey", "options": {
            "tourism": "Tourism", "business": "Business",
            "visiting_family_or_friends": "Visiting family or friends",
            "cultural": "Cultural", "sports": "Sports",
            "official_visit": "Official visit", "medical": "Medical treatment",
            "study": "Study", "airport_transit": "Airport transit",
            "other": "Other"}},
        "costs_covered_by": {"label": "Who is paying for your trip?", "options": {
            "self": "I am paying for myself",
            "sponsor": "A sponsor, host or company is paying"}},
        "means_of_support": {"label": "How will you cover your costs?",
                             "multi": True, "options": {
            "cash": "Cash", "travellers_cheques": "Traveller's cheques",
            "credit_card": "Credit card",
            "prepaid_accommodation": "Accommodation already paid",
            "prepaid_transport": "Transport already paid"}},
    },
}


def load_field_map(form_key: str) -> dict:
    """Operator-supplied mapping: the official PDF's own AcroForm field name ->
    the Ellis answer key. Kept beside the template as <form_key>.map.json so
    adding a country's real form is a data step, never a code change."""
    import json
    path = _map_path(form_key)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except ValueError:
        return {}
    fields = data.get("fields", data) if isinstance(data, dict) else {}
    return {str(k): str(v) for k, v in fields.items()
            if isinstance(v, str) and v in _KNOWN_ANSWER_KEYS}


def validate_template(form_key: str) -> dict:
    """Check an operator-supplied template + map before it is ever used on a
    real case: does the PDF exist, is it a fillable AcroForm, does every
    mapped field actually exist in it, and does every mapped answer key exist
    in Ellis's vocabulary? Returns a report; empty `problems` means ready."""
    problems: list[str] = []
    path = _template_path(form_key)
    if form_key not in FORMS:
        problems.append(f"unknown form {form_key!r}")
    if not path.is_file():
        problems.append(f"no official blank at {path}")
        return {"form_key": form_key, "ready": False, "problems": problems,
                "template_fields": [], "mapped": 0}
    template_fields = template_field_names(form_key)
    if not template_fields:
        problems.append("template has no AcroForm fields (not a fillable PDF) — "
                        "a flattened scan cannot be filled")
    fmap = load_field_map(form_key)
    if not fmap:
        problems.append(f"no field map at {_map_path(form_key)}")
    for field_name, ellis_key in fmap.items():
        if template_fields and field_name not in template_fields:
            problems.append(f"mapped field {field_name!r} is not in the template")
        if ellis_key not in _KNOWN_ANSWER_KEYS:
            problems.append(f"mapped answer key {ellis_key!r} is not an Ellis field")
    return {"form_key": form_key, "ready": not problems, "problems": problems,
            "template_fields": template_fields, "mapped": len(fmap)}


def _known_answer_keys() -> set:
    keys = set()
    for spec in FORMS.values():
        for _label, key, _req in spec["fields"]:
            keys.add(key)
    return keys


_KNOWN_ANSWER_KEYS = _known_answer_keys()


def fill_official_template(form_key: str, answers: dict,
                           field_map: dict | None = None) -> bytes | None:
    """Fill the government's own blank PDF with the applicant's answers.

    `field_map` maps the template's AcroForm field name -> Ellis answer key.
    Only fields with a real answer are written; an unanswered field is left
    blank for the applicant to complete by hand, never invented. Returns None
    when no official template is present (the caller then falls back to the
    preparation sheet rather than fabricating a form)."""
    path = _template_path(form_key)
    if not path.is_file():
        return None
    from io import BytesIO
    from pypdf import PdfReader, PdfWriter
    mapping = field_map or load_field_map(form_key)
    if not mapping:
        return None          # never guess which government field is which
    values = {}
    for field_name, ellis_key in mapping.items():
        # Written the way the form asks for it, through the same translator the
        # printed sheet uses — so the PDF an officer stamps and the sheet the
        # applicant checked can never say different things.
        written = form_value(form_key, ellis_key, (answers or {}).get(ellis_key))
        if written:
            values[field_name] = written
    # Tick-boxes the applicant answered: their choice becomes the /On state of
    # exactly the box they picked. A group they did not answer is left entirely
    # untouched — this form is signed under penalty of perjury, so a guessed
    # tick is never a convenience worth taking.
    ticks = {}
    for group_key, options in load_checkbox_map(form_key).items():
        chosen = (answers or {}).get(group_key)
        if chosen in (None, "", []):
            continue
        picks = chosen if isinstance(chosen, (list, tuple, set)) else [chosen]
        for pick in picks:
            field = options.get(str(pick))
            if field:
                ticks[field] = "/On"
    if not values and not ticks:
        # An official blank with nothing written on it is worse than no form:
        # it looks like Ellis produced the applicant's application when it
        # produced an empty government document. Fall back to the preparation
        # sheet, which at least says what it is.
        return None
    try:
        reader = PdfReader(str(path))
        writer = PdfWriter()
        writer.append(reader)
        # Checkbox appearances only render when the viewer is told to rebuild
        # them; without this the tick is in the data but invisible on paper,
        # which on a printed consular form means it was never ticked.
        writer.set_need_appearances_writer(True)
        for page in writer.pages:
            writer.update_page_form_field_values(page, {**values, **ticks})
        buf = BytesIO()
        writer.write(buf)
        return buf.getvalue()
    except Exception:  # noqa: BLE001 — a template we cannot fill is not a form
        return None


def _image_box(form_key: str, key: str) -> dict:
    """A named image frame from <form_key>.map.json (photo_box,
    signature_box), in PDF points, or {} when the form has none. Data, not
    code — another country's form stays a data step."""
    import json
    path = _map_path(form_key)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except ValueError:
        return {}
    box = data.get(key) if isinstance(data, dict) else None
    if not isinstance(box, dict):
        return {}
    try:
        return {"page": int(box.get("page", 1)), "x": float(box["x"]),
                "y": float(box["y"]), "width": float(box["width"]),
                "height": float(box["height"])}
    except (KeyError, TypeError, ValueError):
        return {}


def photo_box(form_key: str) -> dict:
    """The frame this form draws for the applicant's photograph, or {}."""
    return _image_box(form_key, "photo_box")


def signature_box(form_key: str) -> dict:
    """The cell this form draws for the applicant's signature, or {}."""
    return _image_box(form_key, "signature_box")


def derived_answers(form_key: str, answers: dict) -> dict:
    """Answers the FORM needs that no person should be asked for, because the
    build itself knows them. Today that is 'Place and date': the city the
    applicant gave, and the day this form is produced, in the form's own date
    order. Only ever fills a gap — an explicit answer always wins."""
    out = dict(answers or {})
    if not out.get("signature_place_date"):
        from datetime import date
        from . import dates
        city = str(out.get("address_city") or "").strip()
        fmt = (FORMS.get(form_key) or {}).get("date_format") or "DD-MM-YYYY"
        today = dates.to_portal(date.today().isoformat(), fmt)
        if city and today:
            out["signature_place_date"] = f"{city}, {today}"
    return out


def place_photo(pdf_bytes: bytes, image_bytes: bytes, form_key: str,
                *, box: dict | None = None) -> bytes:
    """Put the applicant's OWN image in one of the form's drawn frames.

    The Schengen blank draws an empty box marked FOTOGRAFIA and a Signature
    cell, and the applicant has already given Ellis both images — the passport
    photo they uploaded, and the signature they drew in Ellis's own pad.
    Leaving the frames empty made them do by hand what they had already done.

    Nothing is generated or altered: these are the applicant's own files,
    fitted to frames the form itself draws. Returns the PDF unchanged if there
    is no frame, no image, or anything at all goes wrong — a form with an
    empty frame is still a usable form, and a corrupted one is not.
    """
    box = box or photo_box(form_key)
    if not box or not image_bytes or not pdf_bytes:
        return pdf_bytes
    try:
        from io import BytesIO
        from PIL import Image
        from pypdf import PdfReader, PdfWriter
        from .providers import pdfgen

        img = Image.open(BytesIO(image_bytes))
        img.load()
        if img.mode != "RGB":          # PNG with alpha, CMYK scan, greyscale
            img = img.convert("RGB")
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=92)
        jpeg = buf.getvalue()

        reader = PdfReader(BytesIO(pdf_bytes))
        index = max(0, int(box["page"]) - 1)
        if index >= len(reader.pages):
            return pdf_bytes
        target = reader.pages[index]
        page_size = (float(target.mediabox.width), float(target.mediabox.height))
        overlay = PdfReader(BytesIO(pdfgen.image_pdf(
            jpeg, pixels=img.size, page=page_size,
            box=(box["x"], box["y"], box["width"], box["height"]))))

        writer = PdfWriter()
        writer.append(reader)
        # The photo goes UNDER nothing and OVER nothing that matters: the frame
        # is empty by design, so merging on top leaves every filled value and
        # the form's own rules untouched.
        writer.pages[index].merge_page(overlay.pages[0])
        writer.set_need_appearances_writer(True)
        out = BytesIO()
        writer.write(out)
        return out.getvalue()
    except Exception:  # noqa: BLE001 — an unreadable photo is not a broken form
        return pdf_bytes


def build(form_key: str, answers: dict, *, applicant_name: str = "",
          photo: bytes | None = None, signature: bytes | None = None) -> dict:
    """Produce the best HONEST artifact for this form:
    the government's own filled PDF when its official blank is available,
    otherwise the preparation sheet — never a fabricated official form.
    Returns {kind, pdf, prepared} where kind is 'official_form' or
    'preparation_sheet'."""
    answers = derived_answers(form_key, answers)
    prepared = prepare(form_key, answers)
    official = fill_official_template(form_key, answers)
    if official is not None:
        placed = signed = False
        if photo:
            with_photo = place_photo(official, photo, form_key)
            placed = with_photo != official
            official = with_photo
        if signature:
            # The signature the applicant DREW in Ellis, placed in the cell
            # the form draws for it. Their act, their stroke — Ellis is only
            # the pen carrying it onto the page.
            sig_box = signature_box(form_key)
            if sig_box:
                with_sig = place_photo(official, signature, form_key,
                                       box=sig_box)
                signed = with_sig != official
                official = with_sig
        return {"kind": "official_form", "pdf": official, "prepared": prepared,
                "photo_placed": placed, "signature_placed": signed}
    return {"kind": "preparation_sheet",
            "pdf": render_pdf(prepared, applicant_name=applicant_name),
            "prepared": prepared, "photo_placed": False,
            "signature_placed": False}


def render_pdf(prepared: dict, *, applicant_name: str = "") -> bytes:
    """A print-ready PDF of the prepared form. Deterministic: identical
    answers always produce identical bytes, so the artifact is hashable and
    comparable across versions."""
    head = [prepared["title"], ""]
    if applicant_name:
        head.append(f"Prepared for: {applicant_name}")
    head += ["Prepared by Ellis from your own answers. Check every field "
             "before signing.", ""]
    body = list(prepared["lines"])
    tail = [""]
    if prepared["missing_required"]:
        tail += ["INCOMPLETE — these required fields still need your answer:"]
        tail += [f"  - {k}" for k in prepared["missing_required"]]
        tail += [""]
    # The note can be long; wrap it so the fixed-width writer stays readable.
    tail += _wrap(prepared["note"], 92)
    return pdfgen.text_pdf(head + body + tail, title=prepared["title"])


def main(argv: list[str] | None = None) -> int:
    """Operator CLI for adding a government form template:
        python -m app.consular_forms inspect  <form_key>   # list its real fields
        python -m app.consular_forms validate <form_key>   # check before use
        python -m app.consular_forms forms                 # what Ellis prepares
    """
    import sys
    args = list(argv if argv is not None else sys.argv[1:])
    cmd = args[0] if args else "forms"
    if cmd == "forms":
        for key, spec in FORMS.items():
            state = "official blank present" if official_template_available(key) \
                else "preparation sheet (no official blank on file)"
            print(f"{key:20} {spec['title']:42} {state}")
        return 0
    if cmd in ("inspect", "validate") and len(args) > 1:
        key = args[1]
        if cmd == "inspect":
            names = template_field_names(key)
            if not names:
                print(f"{key}: no fillable AcroForm fields found "
                      f"(is {_template_path(key)} present and not flattened?)")
                return 1
            print(f"{key}: {len(names)} AcroForm field(s)")
            for n in names:
                print(f"  {n}")
            return 0
        report = validate_template(key)
        print(f"{key}: {'READY' if report['ready'] else 'NOT READY'} "
              f"({report['mapped']} field(s) mapped)")
        for p in report["problems"]:
            print(f"  - {p}")
        return 0 if report["ready"] else 1
    print(main.__doc__)
    return 2


def _wrap(text: str, width: int) -> list[str]:
    words, out, line = str(text).split(), [], ""
    for w in words:
        if len(line) + len(w) + 1 > width:
            out.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        out.append(line)
    return out


if __name__ == "__main__":
    raise SystemExit(main())
