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
    "issuing_country", "birth_date", "sex", "expiry_date", "issue_date",
    "place_of_birth", "issuing_authority",
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
    """Deterministic MRZ YYMMDD -> ISO date. Century pivot: a birth year in the
    future is impossible (74 -> 1974 while today is 2026); an expiry uses the
    same <70 pivot as passport_validity.parse_expiry."""
    if not yymmdd or not re.fullmatch(r"\d{6}", yymmdd):
        return ""
    yy, mm, dd = int(yymmdd[0:2]), int(yymmdd[2:4]), int(yymmdd[4:6])
    if kind == "birth":
        century = 1900 if yy > (today.year % 100) else 2000
    else:
        century = 2000 if yy < 70 else 1900
    try:
        return date(century + yy, mm, dd).isoformat()
    except ValueError:
        return ""


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
# best-effort source for fields the MRZ does not carry (issue date, place of
# birth, authority). Low confidence by construction — always confirmed.
_VZ_PATTERNS = {
    "surname": r"(?:surname|apellidos|nom)\s*[:/]?\s*\n?\s*([A-Z][A-Z '\-]{1,40})",
    "given_names": r"(?:given\s*names?|prenoms|nombres)\s*[:/]?\s*\n?\s*([A-Z][A-Z '\-]{1,40})",
    "passport_number": r"(?:passport\s*(?:no|number)|document\s*no)\s*[.:/]?\s*\n?\s*([A-Z0-9]{6,10})",
    "issue_date": r"(?:date\s*of\s*issue|issued?\s*(?:on|date))\s*[:/]?\s*\n?\s*([0-9]{1,2}\s*[A-Za-z]{3}\s*[0-9]{4}|[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{1,2}/[0-9]{1,2}/[0-9]{4})",
    "place_of_birth": r"(?:place\s*of\s*birth)\s*[:/]?\s*\n?\s*([A-Z][A-Za-z ,.'\-]{1,40})",
    "issuing_authority": r"(?:authority|issuing\s*authority)\s*[:/]?\s*\n?\s*([A-Z][A-Za-z ,.'\-]{1,60})",
}


def _visual_zone(text: str) -> dict:
    out = {}
    for key, pat in _VZ_PATTERNS.items():
        m = re.search(pat, text or "", re.IGNORECASE)
        if m:
            out[key] = m.group(1).strip()
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
        sex = str(mrz.get("sex") or "").strip("<")
        if sex in ("M", "F", "X"):
            put("sex", sex, base_conf, "mrz", needs)
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

    # Visual-only extras the MRZ cannot supply — always confirmed by the applicant.
    for key in ("issue_date", "place_of_birth", "issuing_authority"):
        if vz.get(key):
            put(key, vz[key], 0.6, "ocr_text", True)

    # Expired passport is worth flagging immediately (full rule check happens
    # against the destination's verified rule at the case stage).
    exp = fields.get("expiry_date")
    if exp and exp["value"] and exp["value"] <= today.isoformat():
        exp["needs_confirmation"] = True
        exp["note"] = "passport appears expired"

    # ---- intake-wizard prefill (answer keys) --------------------------------
    # Deliberately NOT prefilled: travel_document_type — the applicant may have
    # picked a non-ordinary document manually and the preview does not show
    # this field, so overwriting it silently is never allowed.
    prefill: dict = {}

    def take(key):
        f = fields.get(key)
        return f["value"] if f else None

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
    elif "issuing_country" in fields:
        fields["issuing_country"]["needs_confirmation"] = True
        fields["issuing_country"]["note"] = "could not verify this country code — please select it"
    for src_key, ans_key in (("full_name", "full_name"), ("surname", "surname"),
                             ("given_names", "given_names"),
                             ("passport_number", "passport_number"),
                             ("birth_date", "birth_date"), ("sex", "sex"),
                             ("expiry_date", "passport_expiry_date")):
        if take(src_key):
            prefill[ans_key] = take(src_key)
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


def doc_type_for_label(label: str) -> str:
    low = str(label or "").lower()
    for doc_type, keys in _DOC_TYPE_KEYWORDS:
        if any(k in low for k in keys):
            return doc_type
    return "document"


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

    for label in (g.get("required_documents") or []):
        if is_health_label(label):
            continue  # handled via structured health_requirements only
        dt = doc_type_for_label(label)
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
    for req in g.get("health_requirements") or []:
        state = health_requirement_state(req, answers)
        if state != "required":
            continue  # not_applicable -> omitted; question -> asked separately
        name = str(req.get("name") or "Health certificate")
        note = str(req.get("trigger") or "")
        add(f"health:{_slug(name)}", name,
            satisfied_by=["vaccination_certificate", "document"], note=note)

    # Arrival card / electronic entry form (visa-exempt routes included).
    card = g.get("arrival_card") or {}
    if isinstance(card, dict) and card.get("required"):
        label = str(card.get("name") or "Arrival card")
        window = str(card.get("submission_window") or "")
        add("arrival_card", label + (f" — {window}" if window else ""),
            kind="form", satisfied_by=["destination_form"])

    for form in (g.get("forms") or []):
        add(f"form:{_slug(form)}", form, kind="form", satisfied_by=["destination_form"],
            required=disp != "VISA_EXEMPT")
    return items


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s or "").lower()).strip("_")[:40] or "item"


def apply_checklist_status(checklist: list[dict], documents: list[dict]) -> dict:
    """Compute per-item status from the case's classified documents.
    documents: [{doc_type, approved, accepted_as_passport_identity}]."""
    provided_types: set[str] = set()
    passport_ok = False
    for d in documents or []:
        dt = str(d.get("doc_type") or "")
        if dt:
            provided_types.add(dt)
        if dt == "passport" and d.get("accepted_as_passport_identity", True):
            passport_ok = True
    items = []
    required_missing = 0
    for item in checklist or []:
        it = dict(item)
        if it["kind"] == "check":
            it["status"] = "auto"
        elif it["id"] == "passport":
            it["status"] = "provided" if passport_ok else "pending"
        elif it["kind"] == "form":
            it["status"] = "provided" if "destination_form" in provided_types else "prepared_later"
        else:
            sat = set(it.get("satisfied_by") or [])
            it["status"] = "provided" if sat & provided_types else "pending"
        if it["required"] and it["status"] == "pending":
            required_missing += 1
        items.append(it)
    return {"items": items,
            "counts": {"total": len(items), "required_missing": required_missing}}
