"""Applicant journey glue: passport-profile extraction at intake, the
route-specific document checklist, and the guidance -> case continuation rules.

Everything here is DETERMINISTIC. Kimi supplies route guidance (labels for
required documents) and, elsewhere, OCR transcription — but every identity
field in a passport profile must carry OCR/MRZ provenance from this module or
it does not exist. Kimi can never invent a passport field, an age, or a
checklist state.

MRZ authority: when a checksum-valid MRZ is present its values win; the
printed visual zone is a cross-check only. A disagreement never silently picks
a side — the field is flagged and the applicant must confirm.
"""
from __future__ import annotations

import re
from datetime import date

# ---------------------------------------------------------------------------
# Passport profile
#
# Whitelisted identity fields. Only these keys may appear in a profile, and
# each must name its provenance (mrz | ocr_text | derived). Anything an LLM or
# provider returns outside this list is dropped.
PROFILE_FIELDS = (
    "surname", "given_names", "full_name", "passport_number", "nationality",
    "issuing_country", "document_type", "birth_date", "sex", "expiry_date",
    "issue_date", "place_of_birth", "place_of_issue", "issuing_authority",
)

# Confidence below this always requires applicant confirmation.
CONFIRM_BELOW = 0.9

# ICAO Doc 9303 country codes that are NOT ISO alpha-3 (Germany's famous "D",
# the British nationality variants). Deterministic mapping; anything still
# unknown to the registries is kept for display but EXCLUDED from prefill and
# flagged — junk must never flow into route resolution.
_ICAO_TO_ISO3 = {
    "D": "DEU", "GBD": "GBR", "GBN": "GBR", "GBO": "GBR", "GBP": "GBR",
    "GBS": "GBR",
}


def _registry_code(value: str, *, kind: str) -> str | None:
    """Map an MRZ country/nationality code to a registry-known ISO3 code, or
    None when it cannot be verified against the registry."""
    code = _ICAO_TO_ISO3.get(str(value or "").upper(), str(value or "").upper())
    try:
        from .registry import load_registry
        if kind == "nationality":
            known = {e["code"] for e in load_registry("nationalities")["entries"]}
        else:
            known = {e["alpha_3"] for e in load_registry("countries")["entries"]}
        return code if code in known else None
    except Exception:  # noqa: BLE001 - registry unavailable -> keep raw code
        return code


def mrz_date_to_iso(yymmdd: str, *, kind: str, today: date) -> str:
    """Deterministic MRZ YYMMDD -> canonical ISO date with CONTEXTUAL century
    resolution (app.dates): a birth date must be a plausible past date, an
    expiry a plausible recent-past/future date — never a fixed pivot."""
    from .. import dates as dates_mod
    return dates_mod.to_iso(dates_mod.parse_mrz_date(yymmdd, kind=kind, today=today))


def derive_age(birth_iso: str, today: date) -> int | None:
    """Age is ALWAYS calculated from the date of birth and the current date —
    never guessed by OCR or a model."""
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", birth_iso or "")
    if not m:
        return None
    by, bm, bd = int(m.group(1)), int(m.group(2)), int(m.group(3))
    age = today.year - by - (1 if (today.month, today.day) < (bm, bd) else 0)
    return age if 0 <= age <= 130 else None


# Visual-zone (printed text) extraction: a cross-check for the MRZ and the only
# best-effort source for fields the MRZ does not carry (issue date, places,
# authority). Low confidence by construction — always confirmed.
#
# Passport labels are MULTILINGUAL RUNS ("Surname/Nom/Apellidos",
# "出生日期/Date of birth") with the value usually on the NEXT line. Each
# pattern therefore consumes the label's whole line (so a sibling label like
# "Nom" can never be captured as the value — the source of a false
# MRZ-vs-printed conflict), then captures the value on the same line after a
# separator or on the following line. Labels match case-insensitively; the
# value classes stay CASE-SENSITIVE.
_DATE_VALUE = r"([0-9]{1,2}[^\n]{0,24}[0-9]{4}|[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{4}年[^\n]{0,12}日)"


def _vz_pattern(label: str, value: str) -> re.Pattern:
    # After the label line: optionally skip ONE line consisting ENTIRELY of
    # native script (Chinese passports print the local value above the Latin
    # one — a mixed line like "湖南/HUNAN" is NOT skipped, its Latin part is
    # the value), then an optional "native/" prefix on the value's own line.
    return re.compile(
        r"(?i:" + label + r")[^\n]*?(?:[:：]\s*|\n)\s*"
        r"(?:[^\x00-\x7F\n]+\n\s*)?(?:[^\x00-\x7F]+/\s*)?" + value)


_VZ_PATTERNS = {
    "surname": _vz_pattern(r"surname", r"([A-Z][A-Z '\-]{1,40})"),
    "given_names": _vz_pattern(r"given\s*names?", r"([A-Z][A-Z '\-]{1,40})"),
    # Combined name field ("姓名/Name" on Chinese passports): the printed
    # "SURNAME, GIVEN" line cross-checks the MRZ name zone, whose characters
    # are NOT checksum-protected (a noisy scan can corrupt them silently).
    "full_name": _vz_pattern(r"姓名|(?<![A-Za-z])name(?![A-Za-z])",
                             r"([A-Z][A-Z ,'\-]{1,50})"),
    "passport_number": _vz_pattern(r"passport\s*n|document\s*n", r"([A-Z0-9]{6,10})"),
    "birth_date": _vz_pattern(r"date\s*of\s*birth|出生日期", _DATE_VALUE),
    "expiry_date": _vz_pattern(r"date\s*of\s*expiry|date\s*of\s*expiration|有效期至", _DATE_VALUE),
    "issue_date": _vz_pattern(r"date\s*of\s*issue|签发日期", _DATE_VALUE),
    "place_of_birth": _vz_pattern(r"place\s*of\s*birth|出生地点", r"([A-Z][A-Za-z ,.'\-]{1,40})"),
    "place_of_issue": _vz_pattern(r"place\s*of\s*issue|签发地点", r"([A-Z][A-Za-z ,.'\-]{1,40})"),
    "issuing_authority": _vz_pattern(r"issuing\s*authority|(?<![a-z])authority|签发机关",
                                     r"([A-Z][A-Za-z ,.'\-]{1,60})"),
}


# An issuing authority routinely WRAPS across printed lines ("OFFICE OF THE
# COMMISSIONER OF" / "MFA OF P.R.CHINA IN H.K.SAR" on HK-SAR passports) — a
# single-line capture silently keeps only the first line. Continuation lines
# are uppercase-led value lines that are NOT another field's label.
_VALUE_CONT_RE = re.compile(r"^[A-Z][A-Za-z0-9 ,.'&/\-]{2,70}$")
_LABEL_HINT_RE = re.compile(
    r"(signature|date\s*of|place\s*of|passport|nationality|birth|expiry|"
    r"issue\b|\bsex\b|\btype\b|country\s*code|bearer|holder)", re.IGNORECASE)


def _join_wrapped_value(text: str, first: str) -> str:
    """Extend a captured value with its wrapped continuation lines."""
    lines = (text or "").splitlines()
    for i, line in enumerate(lines):
        if first not in line:
            continue
        parts = [first]
        for nxt in lines[i + 1:i + 3]:
            nxt = nxt.strip()
            if not _VALUE_CONT_RE.fullmatch(nxt) or _LABEL_HINT_RE.search(nxt) \
                    or re.search(r"[^\x00-\x7F]", nxt):
                break
            parts.append(nxt)
        return " ".join(parts)
    return first


def _visual_zone(text: str) -> dict:
    out = {}
    for key, pat in _VZ_PATTERNS.items():
        m = pat.search(text or "")
        if m:
            out[key] = m.group(1).strip()
    if out.get("issuing_authority"):
        out["issuing_authority"] = _join_wrapped_value(text or "",
                                                       out["issuing_authority"])
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^A-Z]", "", str(s or "").upper())


def build_passport_profile(*, ocr_fields: dict, mrz: dict | None,
                           recognized_text: str, mrz_valid: bool,
                           today: date | None = None) -> dict:
    """Deterministic passport profile from OCR output + parsed MRZ.

    ocr_fields: {key: {value, confidence, ...}} as stored per document.
    Returns {fields, prefill, conflicts, mrz_valid, missing}:
      fields:  {key: {value, confidence, source, needs_confirmation, note?}}
      prefill: intake-wizard answer keys derived from confirmed-able fields.
    """
    today = today or date.today()
    vz = _visual_zone(recognized_text or "")
    fields: dict[str, dict] = {}
    conflicts: list[dict] = []

    def put(key, value, confidence, source, needs_confirmation=False, note=""):
        if key not in PROFILE_FIELDS or value in (None, ""):
            return
        entry = {"value": str(value), "confidence": round(float(confidence), 3),
                 "source": source,
                 "needs_confirmation": bool(needs_confirmation or confidence < CONFIRM_BELOW)}
        if note:
            entry["note"] = note
        fields[key] = entry

    if mrz:
        # MRZ is authoritative when its check digits validate.
        base_conf = 0.98 if mrz_valid else 0.6
        needs = not mrz_valid
        put("surname", mrz.get("surname"), 0.99 if mrz_valid else 0.6, "mrz", needs)
        put("given_names", mrz.get("given_names"), 0.99 if mrz_valid else 0.6, "mrz", needs)
        full = " ".join(x for x in (mrz.get("given_names"), mrz.get("surname")) if x)
        put("full_name", full, 0.99 if mrz_valid else 0.6, "mrz", needs)
        put("passport_number", mrz.get("passport_number"), base_conf, "mrz", needs)
        put("nationality", mrz.get("nationality"), base_conf, "mrz", needs)
        put("issuing_country", mrz.get("issuing_country"), base_conf, "mrz", needs)
        doc_code = str(mrz.get("document_code") or "").strip()
        if doc_code:
            put("document_type", doc_code, base_conf, "mrz", needs,
                note="ICAO document code")
        sex = str(mrz.get("sex") or "").strip("<")
        if sex in ("M", "F", "X"):
            put("sex", sex, base_conf, "mrz", needs)
        # Dates: MRZ YYMMDD -> canonical ISO with contextual century resolution.
        birth_iso = mrz_date_to_iso(mrz.get("birth_date") or "", kind="birth", today=today)
        put("birth_date", birth_iso, base_conf, "mrz", needs)
        expiry_iso = mrz_date_to_iso(mrz.get("expiry_date") or "", kind="expiry", today=today)
        put("expiry_date", expiry_iso, base_conf, "mrz", needs)
    else:
        # No MRZ: only whitelisted OCR fields, all requiring confirmation.
        for key in ("surname", "given_names", "passport_number", "nationality",
                    "issuing_country", "sex"):
            f = (ocr_fields or {}).get(key)
            if f and f.get("value"):
                put(key, f["value"], float(f.get("confidence") or 0.5), "ocr_text", True)

    # Visual-zone cross-check (MRZ wins; disagreement -> applicant confirms).
    for key in ("surname", "given_names", "passport_number"):
        vz_val = vz.get(key)
        if not vz_val:
            continue
        cur = fields.get(key)
        if cur is None:
            put(key, vz_val, 0.6, "ocr_text", True)
        elif _norm(cur["value"]) != _norm(vz_val):
            cur["needs_confirmation"] = True
            cur["note"] = "printed zone disagrees with the machine-readable zone"
            conflicts.append({"field": key, "mrz": cur["value"], "visual": vz_val})

    # Combined printed name ("CHEN, NINGYAN") vs the MRZ name zone. MRZ name
    # characters carry NO check digit, so scanner noise can corrupt them
    # silently; token-set comparison (order-independent: the printed line is
    # "SURNAME, GIVEN", the MRZ full name "GIVEN SURNAME") flags any mismatch
    # for applicant confirmation — never a silent wrong name.
    vz_full = vz.get("full_name")
    if not vz_full and fields.get("surname"):
        # Fallback when OCR ordering separates the label from its value: the
        # printed "SURNAME, GIVEN" comma line is distinctive on its own. Only
        # a line sharing at least one token with the MRZ name is treated as
        # the printed name (unrelated comma lines are ignored).
        mrz_toks = ({_norm(t) for t in str(fields["surname"]["value"]).split()} |
                    {_norm(t) for t in str((fields.get("given_names") or {}).get("value", "")).split()})
        for line in (recognized_text or "").splitlines():
            m = re.fullmatch(r"\s*([A-Z][A-Z'\-]{1,24},\s?[A-Z][A-Z '\-]{1,30})\s*",
                             line)
            if not m:
                continue
            toks = {_norm(t) for t in re.split(r"[ ,]+", m.group(1)) if _norm(t)}
            if toks & mrz_toks:
                vz_full = m.group(1).strip()
                break
    if vz_full and fields.get("surname") and fields.get("given_names"):
        printed_tokens = {_norm(t) for t in re.split(r"[ ,]+", vz_full) if _norm(t)}
        mrz_tokens = ({_norm(t) for t in str(fields["surname"]["value"]).split()} |
                      {_norm(t) for t in str(fields["given_names"]["value"]).split()})
        if printed_tokens and printed_tokens != mrz_tokens:
            for key in ("surname", "given_names", "full_name"):
                if key in fields:
                    fields[key]["needs_confirmation"] = True
                    fields[key]["note"] = ("printed name disagrees with the "
                                           "machine-readable zone")
            conflicts.append({"field": "full_name",
                              "mrz": (fields.get("full_name") or {}).get("value", ""),
                              "visual": vz_full})

    # Printed-zone DATES: deterministic parse (English month names, Chinese
    # layouts, ISO — ambiguous numeric forms rejected), then compared with the
    # MRZ date. A checksum-valid MRZ that AGREES with the printed zone is
    # authoritative; a disagreement is flagged for applicant confirmation —
    # never silently overwritten in either direction.
    from .. import dates as dates_mod
    for key, kind in (("birth_date", "birth"), ("expiry_date", "expiry")):
        vz_raw = vz.get(key)
        if not vz_raw:
            continue
        printed = dates_mod.parse_printed_date(vz_raw)
        cur = fields.get(key)
        if cur is None:
            if printed:
                put(key, dates_mod.to_iso(printed), 0.6, "ocr_text", True)
        elif printed and dates_mod.to_iso(printed) != cur["value"]:
            cur["needs_confirmation"] = True
            cur["note"] = "printed date disagrees with the machine-readable zone"
            conflicts.append({"field": key, "mrz": cur["value"],
                              "visual": dates_mod.to_iso(printed)})

    # Visual-only extras the MRZ cannot supply — always confirmed by the
    # applicant. The issue date is normalized to canonical ISO when the
    # printed form parses deterministically; an unparseable form is kept
    # verbatim (flagged) rather than guessed.
    if vz.get("issue_date"):
        issue_printed = dates_mod.parse_printed_date(vz["issue_date"])
        put("issue_date", dates_mod.to_iso(issue_printed) if issue_printed
            else vz["issue_date"], 0.6, "ocr_text", True,
            note="" if issue_printed else "could not read this date — please correct it")
    for key in ("place_of_birth", "place_of_issue", "issuing_authority"):
        if vz.get(key):
            put(key, vz[key], 0.6, "ocr_text", True)

    # Expired passport is worth flagging immediately (full rule check happens
    # against the destination's verified rule at the case stage).
    exp = fields.get("expiry_date")
    if exp and exp["value"] and exp["value"] <= today.isoformat():
        exp["needs_confirmation"] = True
        exp["note"] = "passport appears expired"

    # ---- intake-wizard prefill (answer keys) --------------------------------
    prefill: dict = {}

    def take(key):
        f = fields.get(key)
        return f["value"] if f else None

    # Passport TYPE from the checksum-valid MRZ document code: ICAO makes the
    # first character 'P' for every passport; the country-assigned second
    # letter distinguishes diplomatic (D) and service/official (S) books —
    # anything else (PO on Chinese ordinary passports, plain P<, PA, PC…) is
    # an ordinary passport. The visible dropdown stays editable, so a wrong
    # read is one click to correct — never a silent lock-in.
    if mrz and mrz_valid:
        code = str(mrz.get("document_code") or "").strip("<").upper()
        if code.startswith("P"):
            prefill["travel_document_type"] = {
                "D": "diplomatic_passport",
                "S": "service_passport",
            }.get(code[1:2], "ordinary_passport")

    # Country codes go through the ICAO->ISO3 map + registry check; an
    # unverifiable code stays visible in the profile (flagged) but never
    # prefills route answers.
    nat = _registry_code(take("nationality"), kind="nationality")
    if nat:
        prefill["passport_nationality"] = nat
        fields["nationality"]["value"] = nat
    elif "nationality" in fields:
        fields["nationality"]["needs_confirmation"] = True
        fields["nationality"]["note"] = "could not verify this country code — please select it"
    iss = _registry_code(take("issuing_country"), kind="country")
    if iss:
        prefill["passport_issuing_country"] = iss
        fields["issuing_country"]["value"] = iss
        # The passport's country also drives the address/residence defaults —
        # the applicant edits them only when they actually live elsewhere.
        prefill["address_country"] = iss
        prefill["lawful_country_of_residence"] = iss
    elif "issuing_country" in fields:
        fields["issuing_country"]["needs_confirmation"] = True
        fields["issuing_country"]["note"] = "could not verify this country code — please select it"
    for src_key, ans_key in (("full_name", "full_name"), ("surname", "surname"),
                             ("given_names", "given_names"),
                             ("passport_number", "passport_number"),
                             ("birth_date", "birth_date"), ("sex", "sex"),
                             ("expiry_date", "passport_expiry_date"),
                             ("issue_date", "passport_issue_date")):
        val = take(src_key)
        if not val:
            continue
        # Date answers must be canonical ISO — an unparseable printed date
        # stays visible in the profile (flagged) but never prefills answers.
        if ans_key.endswith("_date") or src_key.endswith("_date"):
            from .. import dates as dates_mod
            if not dates_mod.is_iso(val):
                continue
        prefill[ans_key] = val
    age = derive_age(take("birth_date") or "", today)
    if age is not None:
        prefill["age"] = age
        fields["_age"] = {"value": str(age), "confidence": 1.0, "source": "derived",
                          "needs_confirmation": False,
                          "note": "calculated from date of birth"}

    missing = [k for k in ("surname", "given_names", "passport_number",
                           "nationality", "birth_date", "expiry_date")
               if k not in fields]
    return {"fields": fields, "prefill": prefill, "conflicts": conflicts,
            "mrz_valid": bool(mrz and mrz_valid), "missing": missing}


# ---------------------------------------------------------------------------
# Continuation rules (guidance -> case). The backend is authoritative; the
# frontend mirrors this mapping for display only.
KIND_BY_DISPOSITION = {
    "VISA_REQUIRED": "visa_application",
    "VISA_EXEMPT": "entry_preparation",
    "ELECTRONIC_AUTHORIZATION_REQUIRED": "authorization_application",
    "CONDITIONAL": "conditional_guidance",
}


def continuation_meta(guidance_result: dict) -> dict:
    """Which continuation the guidance supports. Blocked ONLY when the missing
    issue prevents safe preparation (no known disposition); a known disposition
    with residual gaps continues 'with available guidance'."""
    g = (guidance_result or {}).get("guidance") or {}
    status = (guidance_result or {}).get("status")
    disp = g.get("disposition")
    kind = KIND_BY_DISPOSITION.get(disp)
    blockers = list((guidance_result or {}).get("missing_fields") or []) + \
        list((guidance_result or {}).get("contradictions") or [])
    if kind and status == "KIMI_PRIMARY":
        return {"blocked": False, "kind": kind, "disposition": disp,
                "partial": disp == "CONDITIONAL", "blockers": []}
    if kind and status == "KIMI_UNCERTAIN":
        return {"blocked": False, "kind": kind, "disposition": disp,
                "partial": True, "blockers": blockers}
    return {"blocked": True, "kind": None, "disposition": disp or None,
            "partial": False, "blockers": blockers or ["disposition"]}


# ---------------------------------------------------------------------------
# Route-specific document checklist, derived deterministically from guidance
# FIELDS (never free text execution). Kimi's document labels are kept for
# display; satisfaction is matched on classified doc types.
_DOC_TYPE_KEYWORDS = (
    # H1B labels FIRST: "Labor Condition Application (Form ETA-9035)" must hit
    # certified_lca before the generic "form" keyword turns it into a
    # non-uploadable destination_form item, and "FEIN evidence" must beat
    # bank_statement's "statement"/"financial" net.
    ("certified_lca", ("labor condition", "eta-9035", "eta 9035",
                       "certified lca")),
    ("prior_i797", ("i-797", "i797", "notice of action")),
    ("i94_record", ("i-94", "i94")),
    ("credential_evaluation", ("credential evaluation", "credentials evaluation",
                               "degree evaluation", "equivalency report")),
    ("degree_certificate", ("degree certificate", "degree", "diploma", "学位证")),
    ("graduation_certificate", ("graduation certificate", "graduation", "毕业证")),
    ("transcript", ("transcript", "academic record", "成绩单")),
    ("resume_cv", ("resume", "curriculum vitae", " cv", "简历")),
    ("employer_support_letter", ("support letter", "letter of support")),
    ("job_description", ("job description", "position description")),
    ("fein_evidence", ("fein", "employer identification number", "ein evidence",
                       "cp-575", "cp 575")),
    ("corporate_relationship_evidence", ("corporate relationship",
                                         "parent company", "subsidiary")),
    ("employer_financials", ("ability to pay", "employer financial",
                             "company financial")),
    ("photo", ("photograph", "passport photo", "photo")),
    ("flight_itinerary", ("flight", "itinerar", "onward", "return ticket",
                          "round-trip", "round trip", "air ticket", "booking reference")),
    ("hotel_booking", ("hotel", "accommodation", "lodging", "stay booking")),
    ("bank_statement", ("bank", "financial", "funds", "statement", "balance")),
    ("employment_letter", ("employment", "employer", "work certificate", "salary")),
    ("student_letter", ("student", "enrolment", "enrollment", "school letter")),
    ("invitation_letter", ("invitation",)),
    ("travel_insurance", ("insurance",)),
    ("residence_permit", ("residence permit", "green card", "resident card")),
    ("prior_visa", ("previous visa", "prior visa", "old visa")),
    ("destination_form", ("form", "arrival card", "application form", "declaration")),
    ("passport", ("passport",)),
)

# A vaccination/health label is NEVER a plain checklist document: it is either
# covered by the structured health_requirements (with its trigger evaluated
# against the applicant's actual route) or omitted entirely. This keyword strip
# is what keeps "Yellow fever certificate (if applicable)" noise out of a
# direct U.S. -> Singapore checklist.
_HEALTH_LABEL_WORDS = ("vaccin", "immuniz", "immunis", "yellow fever",
                       "health certificate", "health declaration", "inoculat")


def is_health_label(label: str) -> bool:
    low = str(label or "").lower()
    return any(w in low for w in _HEALTH_LABEL_WORDS)


# A payment method is NEVER a document: it belongs to the payment stage of the
# workflow, where the applicant sees the real fee and chooses among the
# portal's actual payment options. A guidance label like "Payment method
# (credit/debit card)" must not become an uploadable checklist requirement —
# checklist completion can never depend on it.
_PAYMENT_LABEL_WORDS = ("payment method", "payment card", "credit card",
                        "debit card", "method of payment", "means of payment",
                        "payment of the fee", "visa fee payment", "fee payment")


def is_payment_label(label: str) -> bool:
    low = str(label or "").lower()
    if any(w in low for w in _PAYMENT_LABEL_WORDS):
        return True
    # A bare "payment"-titled item with no document-ish qualifier is the
    # payment stage, not a document (e.g. "Payment", "Online payment").
    return bool(re.fullmatch(r"(online |e-?)?payment( method)?s?", low.strip()))


def doc_type_for_label(label: str) -> str:
    low = str(label or "").lower()
    for doc_type, keys in _DOC_TYPE_KEYWORDS:
        if any(k in low for k in keys):
            return doc_type
    return "document"


# H1B statutory document types are classified for UPLOAD matching (via
# doc_classifier) and assembled into the hardcoded H1B checklist — they are
# NEVER synthesized from a tourist/consular route's free-text guidance labels.
# Their broad keywords ("degree", "support letter", "parent company", "ability
# to pay") otherwise reclassify and silently collapse ordinary tourist checklist
# items in the SHARED derive_document_checklist path (finding #5), so that
# builder treats them as the generic "document" type it used before the H1B
# edition added them.
_H1B_ONLY_DOC_TYPES = frozenset({
    "certified_lca", "prior_i797", "i94_record", "credential_evaluation",
    "degree_certificate", "graduation_certificate", "transcript", "resume_cv",
    "employer_support_letter", "job_description", "fein_evidence",
    "corporate_relationship_evidence", "employer_financials"})


# Answer keys whose countries count as the applicant's route exposure when a
# health requirement is conditional on presence in trigger countries.
_EXPOSURE_ANSWER_KEYS = ("lawful_country_of_residence", "transit_countries",
                         "recent_travel_countries")


def _exposure_countries(answers: dict) -> set[str]:
    out: set[str] = set()
    for key in _EXPOSURE_ANSWER_KEYS:
        v = (answers or {}).get(key)
        if isinstance(v, str) and v.strip():
            out.add(v.strip().upper())
        elif isinstance(v, list):
            out.update(str(x).strip().upper() for x in v if str(x).strip())
    return out


def health_requirement_state(req: dict, answers: dict) -> str:
    """Deterministic per-applicant state of one structured health requirement:
      'required'        — applies to this applicant's route
      'not_applicable'  — provably irrelevant (omit entirely)
      'question'        — conditional and the deciding answer is missing
    """
    applicability = str((req or {}).get("applicability") or "").lower()
    if applicability == "always_required":
        return "required"
    if applicability != "conditional":
        return "not_applicable"
    triggers = {str(c).strip().upper() for c in (req.get("trigger_countries") or [])
                if str(c).strip()}
    exposure = _exposure_countries(answers or {})
    if triggers and exposure & triggers:
        return "required"
    # Conditional with no exposure match: only ask when the applicant has not
    # yet answered the travel-history question; an answered (even empty)
    # history means the condition provably does not apply.
    if "recent_travel_countries" in (answers or {}):
        recent = (answers or {}).get("recent_travel_countries")
        if not triggers and isinstance(recent, list) and recent:
            # No specific trigger list from the rule, but the applicant
            # affirmed recent presence in a risk country -> evidence required.
            return "required"
        return "not_applicable"
    return "question" if triggers or req.get("question") else "not_applicable"


def pending_health_questions(guidance: dict, *, answers: dict) -> list[dict]:
    """Travel-history questions to ask ONLY when a conditional health rule
    needs them. Empty for routes with no conditional health requirement."""
    out = []
    for req in (guidance or {}).get("health_requirements") or []:
        if health_requirement_state(req, answers) == "question":
            out.append({
                "id": f"health:{_slug(req.get('name'))}",
                "name": req.get("name"),
                "question": req.get("question") or (
                    "In the last 6 days, were you in "
                    + (", ".join(req.get("trigger_countries") or []) or "a risk country")
                    + "?"),
                "trigger": req.get("trigger") or "",
                "trigger_countries": req.get("trigger_countries") or [],
                "answer_key": "recent_travel_countries",
            })
    return out


def derive_document_checklist(guidance: dict, *, answers: dict | None = None) -> list[dict]:
    """Checklist items: {id, label, kind, required, satisfied_by}. kind is
    'document' (uploadable), 'form' (route form Ellis prepares/tracks), or
    'check' (Ellis evaluates automatically, e.g. passport validity).

    Health/vaccination evidence appears ONLY when the structured requirement
    applies to this applicant's actual route (origin/residence/transit/recent
    travel); irrelevant items are omitted entirely, never shown as optional."""
    g = guidance or {}
    answers = answers or {}
    disp = g.get("disposition")
    items: list[dict] = []
    seen: set[str] = set()

    # The DS-160 is a TRANSCRIPTION form: the only documents it consumes are the
    # passport (biodata -> identity fields) and the applicant's photograph.
    # Bank statements, hotel bookings, itineraries and insurance are
    # interview-DAY evidence the applicant brings to the consulate in person —
    # they are NOT inputs to the form. So the US consular route requests
    # exactly passport + photo and nothing else. Every other consular route
    # (e.g. Schengen, which files those at submission) keeps the full list.
    ds160_only = False
    try:
        from ..consular_forms import form_for_destination
        from .registry import normalize_country
        _dest_iso3 = normalize_country(
            (answers.get("destination_country") or ""),
            field="destination_country")
        ds160_only = form_for_destination(_dest_iso3) == "ds160_prep"
    except Exception:  # noqa: BLE001 - unknown destination keeps the full list
        ds160_only = False

    def add(item_id, label, *, kind="document", required=True, satisfied_by=None,
            note=""):
        if item_id in seen:
            return
        seen.add(item_id)
        item = {"id": item_id, "label": str(label), "kind": kind,
                "required": bool(required),
                "satisfied_by": list(satisfied_by or [])}
        if note:
            item["note"] = str(note)
        items.append(item)

    add("passport", "Passport (biodata page)", satisfied_by=["passport"])
    if g.get("passport_validity"):
        add("passport_validity", f"Passport validity: {g['passport_validity']}",
            kind="check", satisfied_by=[])

    for label in ([] if ds160_only else (g.get("required_documents") or [])):
        if is_health_label(label):
            continue  # handled via structured health_requirements only
        if is_payment_label(label):
            continue  # payment is a workflow stage, never a document (Part 1)
        dt = doc_type_for_label(label)
        if dt in _H1B_ONLY_DOC_TYPES:
            # Tourist/consular guidance never emits H1B statutory documents;
            # keep the label as a distinct generic item so any readable upload
            # satisfies it and near-synonym labels never collapse into one.
            dt = "document"
        if dt == "passport":
            continue
        if dt == "destination_form":
            add(f"form:{_slug(label)}", label, kind="form", satisfied_by=["destination_form"])
        elif dt == "document":
            # Unrecognized document types keep their OWN checklist item (a
            # police clearance and an invitation must never collapse into
            # one) — any generic upload can satisfy them.
            add(f"doc:{_slug(label)}", label, satisfied_by=["document"])
        else:
            add(dt, label, satisfied_by=[dt])

    # Structured evidence fields imply their document even when the free list
    # omitted them.
    if ds160_only:
        # The DS-160 requires a photograph; it needs no other document.
        add("photo", f"Passport photograph — {g['photo_requirements']}"
            if g.get("photo_requirements")
            else "Passport photograph (US visa photo standard)",
            satisfied_by=["photo"])
    else:
        if g.get("photo_requirements"):
            add("photo", f"Passport photograph — {g['photo_requirements']}",
                satisfied_by=["photo"])
        if g.get("onward_travel_evidence"):
            add("flight_itinerary", f"Onward/return travel — {g['onward_travel_evidence']}",
                satisfied_by=["flight_itinerary"])
        if g.get("accommodation_evidence"):
            add("hotel_booking", f"Accommodation — {g['accommodation_evidence']}",
                satisfied_by=["hotel_booking"])
        if g.get("financial_evidence"):
            add("bank_statement", f"Financial evidence — {g['financial_evidence']}",
                satisfied_by=["bank_statement"])
        if g.get("insurance_required"):
            add("travel_insurance", "Travel insurance", satisfied_by=["travel_insurance"])

    # Health/vaccination evidence: structured, trigger-evaluated, honest.
    for req in ([] if ds160_only else (g.get("health_requirements") or [])):
        state = health_requirement_state(req, answers)
        if state != "required":
            continue  # not_applicable -> omitted; question -> asked separately
        name = str(req.get("name") or "Health certificate")
        note = str(req.get("trigger") or "")
        add(f"health:{_slug(name)}", name,
            satisfied_by=["vaccination_certificate", "document"], note=note)

    # Arrival card / electronic entry form (visa-exempt routes included).
    card = {} if ds160_only else (g.get("arrival_card") or {})
    if isinstance(card, dict) and card.get("required"):
        label = str(card.get("name") or "Arrival card")
        window = str(card.get("submission_window") or "")
        add("arrival_card", label + (f" — {window}" if window else ""),
            kind="form", satisfied_by=["destination_form"])

    for form in ([] if ds160_only else (g.get("forms") or [])):
        add(f"form:{_slug(form)}", form, kind="form", satisfied_by=["destination_form"],
            required=disp != "VISA_EXEMPT")
    return items


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s or "").lower()).strip("_")[:40] or "item"


# Manually assignable document types (safe whitelist for ambiguous uploads).
# 'passport' is deliberately excluded — passport identity is only ever
# established by the MRZ/biodata classifier, never by an applicant label.
MANUAL_DOC_TYPES = (
    "photo", "flight_itinerary", "hotel_booking", "bank_statement",
    "employment_letter", "student_letter", "invitation_letter",
    "travel_insurance", "residence_permit", "prior_visa", "destination_form",
    "vaccination_certificate",
    # H1B edition (passport stays excluded: identity comes only from MRZ)
    "degree_certificate", "graduation_certificate", "transcript", "resume_cv",
    "prior_i797", "i94_record", "credential_evaluation",
    "employer_support_letter", "job_description", "fein_evidence",
    "employer_financials", "corporate_relationship_evidence", "certified_lca",
)

# Classifier provenance considered DETERMINISTIC (drives whether a differing
# type reads as a confident 'mismatch' advisory or a soft 'uncertain' one).
_STRONG_CLASSIFIERS = ("mrz", "keyword", "filename", "applicant")


def match_document_to_item(item: dict, doc_type: str, *, classifier: str = "",
                           rejected: bool = False, page_type: str = "") -> str:
    """How well a classified upload satisfies one checklist requirement.

    Every verdict is ADVISORY — classification never blocks the applicant's
    explicit submission (only security/structural validation at upload does):
      'match'      — the detected type satisfies this requirement
      'uncertain'  — could not be confidently identified / semantic-only guess
                     (Submit needs the applicant's explicit confirmation)
      'mismatch'   — confidently a DIFFERENT document type ("Submit anyway"
                     needs the applicant's explicit confirmation)
      'unreadable' — no readable text / rejected page (still submittable with
                     explicit confirmation while a secure preview exists)
    """
    if rejected or str(page_type or "") in ("unreadable", "blank_page", "blurry"):
        return "unreadable"
    dt = str(doc_type or "")
    sat = set((item or {}).get("satisfied_by") or [])
    if not sat or "document" in sat:
        # Generic requirement ("police clearance", health evidence): any
        # readable supporting document can satisfy it.
        return "match"
    if dt in sat:
        return "match"
    if dt in ("", "document", "unknown"):
        return "uncertain"
    # A specific known type that this requirement does not accept.
    return "mismatch" if classifier in _STRONG_CLASSIFIERS else "uncertain"


def apply_checklist_status(checklist: list[dict], documents: list[dict],
                           submissions: list[dict] | None = None) -> dict:
    """Compute per-item status from the case's documents AND the applicant's
    explicit submissions. An upload alone never completes a requirement — only
    a submitted binding (or an automatically-not-applicable/waived item) does.

    documents:   [{id, name, doc_type, ocr_status, rejected,
                   accepted_as_passport_identity, mime, size_bytes}]
    submissions: [{item_id, document_id, status('bound'|'submitted'),
                   match_verdict, confirmed_by_applicant, detected_type,
                   submitted_at}]

    Document-item statuses: pending (Needed) | processing | needs_review |
    mismatch | unreadable | ready_to_submit | submitted | waived.
    Non-document kinds keep 'auto' (check) and 'prepared_later' (form).
    """
    docs_by_id = {str(d.get("id") or ""): d for d in (documents or [])}
    sub_by_item = {str(s.get("item_id") or ""): s for s in (submissions or [])}
    items = []
    required_missing = 0
    required_docs = 0
    fulfilled = 0
    for item in checklist or []:
        it = dict(item)
        if it["kind"] == "check":
            it["status"] = "auto"
        elif it["kind"] == "form":
            it["status"] = "prepared_later"
        elif it.get("waived"):
            it["status"] = "waived"
        else:
            sub = sub_by_item.get(str(it["id"]))
            doc = docs_by_id.get(str(sub.get("document_id") or "")) if sub else None
            if not sub or doc is None:
                it["status"] = "pending"
            else:
                verdict = match_document_to_item(
                    it, doc.get("doc_type"),
                    classifier=str(doc.get("classifier") or ""),
                    rejected=bool(doc.get("rejected")),
                    page_type=str(doc.get("page_type") or ""))
                confirmed = bool(sub.get("confirmed_by_applicant"))
                # A SUBMITTED requirement stays fulfilled — the applicant's
                # explicit confirmation outranks any advisory verdict.
                if sub.get("status") == "submitted":
                    it["status"] = "submitted"
                elif str(doc.get("ocr_status") or "done") not in ("done", ""):
                    it["status"] = "processing"
                elif verdict == "unreadable":
                    it["status"] = "unreadable"
                elif verdict == "match" or confirmed:
                    it["status"] = "ready_to_submit"
                elif verdict == "mismatch":
                    it["status"] = "mismatch"
                else:
                    it["status"] = "needs_review"
                it["binding"] = {
                    "document_id": doc.get("id"),
                    "document_name": doc.get("name"),
                    "detected_type": doc.get("doc_type"),
                    "match_verdict": verdict,
                    "confirmed_by_applicant": confirmed,
                    "submitted": sub.get("status") == "submitted",
                    "submitted_at": sub.get("submitted_at"),
                    "mime": doc.get("mime"),
                    "size_bytes": doc.get("size_bytes"),
                    "language": doc.get("language") or {},
                    "has_text": bool(doc.get("has_text")),
                    "translation_document_id": doc.get("translation_document_id") or None,
                }
        if it["kind"] == "document" and it.get("required") and not it.get("waived"):
            required_docs += 1
            if it["status"] == "submitted":
                fulfilled += 1
            else:
                required_missing += 1
        elif it["kind"] == "document" and it.get("waived"):
            fulfilled += 1
        items.append(it)
    return {"items": items,
            "counts": {"total": len(items), "required_missing": required_missing,
                       "required_documents": required_docs, "fulfilled": fulfilled}}
