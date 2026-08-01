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
    ("19. Current occupation", "occupation", False),
    ("20. Employer and employer's address and telephone number",
     "employer", False),
    ("21. Purpose(s) of the journey", "travel_purpose", True),
    ("23. Member State of main destination", "destination_country", True),
    ("26. Intended date of arrival in the Schengen area", "arrival_date", True),
    ("27. Intended date of departure from the Schengen area", "departure_date", True),
    ("31. Surname and first name of the inviting person / hotel(s)",
     "accommodation", True),
    ("    Address of the inviting person / hotel(s)", "accommodation_address", False),
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
]

FORMS = {
    "schengen_uniform": {
        "title": "Schengen Uniform Visa Application",
        "fields": SCHENGEN_UNIFORM,
        "submission": "in_person",
        "note": ("Print this form, sign it by hand (fields 37 and 38), and "
                 "bring it to your appointment with your passport, photo and "
                 "supporting documents. Ellis cannot submit a Schengen visa "
                 "application: it is decided in person, with biometrics."),
    },
    "ds160_prep": {
        "title": "US DS-160 Preparation Sheet",
        "fields": DS160_PREP,
        "submission": "applicant_online",
        "note": ("Sign in at ceac.state.gov/genniv and copy these values into "
                 "the matching DS-160 sections. Ellis never submits the DS-160 "
                 "for you: it is signed electronically by the applicant under "
                 "penalty of perjury."),
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
    if d == "USA":
        return "ds160_prep"
    return None


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
        raw = answers.get(key)
        value = str(raw).strip() if raw not in (None, "") else ""
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
    spec = FORMS.get(form_key) or {}
    # Default mapping: the form spec's own (label, key) pairs keyed by the
    # template's field names when the operator supplied an explicit map.
    mapping = field_map or spec.get("template_field_map") or {}
    if not mapping:
        return None          # never guess which government field is which
    values = {}
    for field_name, ellis_key in mapping.items():
        raw = (answers or {}).get(ellis_key)
        if raw not in (None, ""):
            values[field_name] = str(raw)
    try:
        reader = PdfReader(str(path))
        writer = PdfWriter()
        writer.append(reader)
        for page in writer.pages:
            writer.update_page_form_field_values(page, values)
        buf = BytesIO()
        writer.write(buf)
        return buf.getvalue()
    except Exception:  # noqa: BLE001 — a template we cannot fill is not a form
        return None


def build(form_key: str, answers: dict, *, applicant_name: str = "") -> dict:
    """Produce the best HONEST artifact for this form:
    the government's own filled PDF when its official blank is available,
    otherwise the preparation sheet — never a fabricated official form.
    Returns {kind, pdf, prepared} where kind is 'official_form' or
    'preparation_sheet'."""
    prepared = prepare(form_key, answers)
    official = fill_official_template(form_key, answers)
    if official is not None:
        return {"kind": "official_form", "pdf": official, "prepared": prepared}
    return {"kind": "preparation_sheet",
            "pdf": render_pdf(prepared, applicant_name=applicant_name),
            "prepared": prepared}


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
