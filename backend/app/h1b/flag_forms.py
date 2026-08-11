"""DOL FLAG preparation: the ETA-9141 prevailing wage request, and an honest
account of what Ellis can prefill on the ETA-9035 LCA.

Both forms are really filed by a human inside FLAG (flag.dol.gov). Ellis never
logs in, never signs, never submits: it fills the government's own blank so the
employer can review every answer first, and it names - in the employer's own
words - exactly what is still outstanding before they open the portal.

Doctrine this module holds to, on top of everything forms.py already enforces:

- NOTHING IS INVENTED. Every written value is a party's own recorded answer,
  the employer profile, or one of exactly three DERIVATIONS, each grounded in a
  government table or the case's own record and each reported by name in
  ``derived`` so a human can see it was Ellis and not them:
    * the classification symbol 'H-1B', from this case's own visa type;
    * the SOC occupation title, from DOL's own OES occupation table for the SOC
      code already on file (never a title Ellis composed);
    * worksite city/state/ZIP/county read from the sibling ``worksite_*`` key
      the same petitioner record already carries under a second spelling.
  A missing wage, SOC or worksite is REPORTED MISSING, never filled in.
- THE GOVERNMENT'S BLOCK IS THE GOVERNMENT'S. Section F (Prevailing Wage
  Determination) is marked FOR OFFICIAL GOVERNMENT USE ONLY and the printed
  'FOR DEPARTMENT OF LABOR USE ONLY' PW Tracking Number header is DOL's - on
  this blank neither carries a single AcroForm field, so they are unmappable by
  construction and this module writes nothing there. Ellis's own computed wage
  level travels in the PAYLOAD as a suggestion, never onto the form.
- PARTY WALL. The ETA-9141 is a petitioner act and receives NO beneficiary
  facts: the fill dict is forms.answers_for_form (petitioner shared facts +
  EmployerProfile), which hands beneficiary identity only to the I-129. DOL
  never sees the worker's record here. Callers must gate on the petitioner
  party the same way forms_api does (api._authorize_step_action(...,
  "petitioner")); these are library functions, not endpoints.
"""
from __future__ import annotations

from . import filing as h1b_filing
from . import forms as h1b_forms

# The form this module owns. forms.FORM_KEYS/STEP_BY_FORM know it too, so the
# generic prepare endpoint and this module fill from the same map and the same
# assembled answers.
FORM_KEY = "eta-9141"
LCA_FORM_KEY = "eta-9035"

FLAG_URL = "flag.dol.gov"


# ---------------------------------------------------------------------------
# Localized user-facing strings (en + zh-CN + zh-Hant, the sibling contract).
# ---------------------------------------------------------------------------
STRINGS = {
    "flag.pwd_preparation": {
        "en": ("Preparation copy only - the prevailing wage determination is "
               "requested electronically in FLAG (flag.dol.gov). Nothing has "
               "been sent to the Department of Labor."),
        "zh-CN": ("仅为准备件 - 现行工资裁定须在 FLAG（flag.dol.gov）在线申请。"
                  "尚未向劳工部提交任何材料。"),
        "zh-Hant": ("僅為準備件 - 現行工資裁定須在 FLAG（flag.dol.gov）線上申請。"
                    "尚未向勞工部提交任何材料。"),
    },
    "flag.human_acts": {
        "en": ("A person signs in to FLAG and files this themselves. Ellis "
               "never logs in, never signs, and never submits on anyone's "
               "behalf."),
        "zh-CN": ("须由本人登录 FLAG 亲自提交。Ellis 绝不代为登录、签名或提交。"),
        "zh-Hant": ("須由本人登入 FLAG 親自提交。Ellis 絕不代為登入、簽名或提交。"),
    },
    "flag.dol_block": {
        "en": ("The prevailing wage, the OES wage level, the PW tracking number "
               "and the determination dates are the Department of Labor's to "
               "write. Ellis leaves that whole block empty."),
        "zh-CN": ("现行工资、OES 工资等级、PW 追踪号及裁定日期均由劳工部填写，"
                  "Ellis 整块留空。"),
        "zh-Hant": ("現行工資、OES 工資等級、PW 追蹤號及裁定日期均由勞工部填寫，"
                    "Ellis 整塊留空。"),
    },
    "flag.lca_report": {
        "en": ("What Ellis can prefill on your LCA, and what is still missing. "
               "Every value is your own recorded answer - fill the gaps here "
               "and the FLAG session becomes a review instead of a retype."),
        "zh-CN": ("以下是 Ellis 可为您的 LCA 预填的内容，以及仍然缺失的内容。"
                  "所有数值均来自您自己填写的答案 - 先补齐缺口，"
                  "FLAG 上就只需核对而无需重新输入。"),
        "zh-Hant": ("以下是 Ellis 可為您的 LCA 預填的內容，以及仍然缺失的內容。"
                    "所有數值均來自您自己填寫的答案 - 先補齊缺口，"
                    "FLAG 上就只需核對而無需重新輸入。"),
    },
}


def tr(key: str, locale: str = "en") -> str:
    entry = STRINGS.get(key) or {}
    return entry.get(locale) or entry.get("en") or key


# ---------------------------------------------------------------------------
# Applicant wording. What Ellis ASKS, never a raw key - for the ETA-9141's own
# vocabulary and for the ETA-9035 keys the shared table does not name.
# ---------------------------------------------------------------------------
FIELD_WORDING = {
    # --- ETA-9141: visa + requestor contact block
    "h1b_visa_classification": "Visa classification symbol (e.g. H-1B)",
    "h1b_contact_last_name": "Contact's last (family) name",
    "h1b_contact_first_name": "Contact's first (given) name",
    "h1b_contact_middle_names": "Contact's middle name(s)",
    "h1b_contact_job_title": "Contact's job title",
    "h1b_contact_address1": "Contact's street address",
    "h1b_contact_address2": "Contact's address line 2 (if any)",
    "h1b_contact_city": "Contact's city",
    "h1b_contact_state": "Contact's state",
    "h1b_contact_postal_code": "Contact's postal code",
    "h1b_contact_country": "Contact's country",
    "h1b_contact_province": "Contact's province (if outside the U.S.)",
    "h1b_contact_phone_ext": "Contact's telephone extension",
    "h1b_contact_fax": "Contact's fax number (if any)",
    "employer_contact_phone": "Employer contact's telephone number",
    "employer_contact_email": "Employer contact's e-mail address",
    "employer_contact_name": "Employer contact / signatory name",
    "employer_contact_title": "Employer contact's job title",
    # --- employer block
    "employer_legal_name": "Employer's full legal business name",
    "employer_dba": "Trade name / Doing Business As (if any)",
    "employer_address_line1": "Employer's street address",
    "employer_address_line2": "Employer's address line 2 (if any)",
    "employer_city": "Employer's city",
    "employer_state": "Employer's state",
    "employer_postal_code": "Employer's postal code",
    "employer_phone": "Employer's telephone number",
    "h1b_employer_country": "Employer's country",
    "h1b_employer_province": "Employer's province (if outside the U.S.)",
    "h1b_employer_phone_ext": "Employer's telephone extension",
    # --- ETA-9141 wage-processing questions (Section D)
    "h1b_pwd_acwia_covered": "Is the employer covered by ACWIA?",
    "h1b_pwd_cba_covered": ("Is the position covered by a Collective Bargaining "
                            "Agreement (CBA)?"),
    "h1b_pwd_dba_sca_requested": ("Are you asking DOL to consider the "
                                  "Davis-Bacon Act or the McNamara Service "
                                  "Contract Act?"),
    "h1b_pwd_dba_or_sca": "Which act applies - Davis-Bacon (DBA) or Service Contract (SCA)?",
    "h1b_pwd_survey_requested": ("Are you asking DOL to consider a wage survey "
                                 "instead of the OES wage?"),
    "h1b_pwd_survey_name": "Name of the wage survey you want considered",
    "h1b_pwd_survey_publication_date": "Date the wage survey was published",
    # --- job description (Section E.a)
    "job_title": "Job title of the offered position",
    "soc_code": "SOC (O*NET/OES) occupation code",
    "soc_title": "SOC (O*NET/OES) occupation title",
    "job_duties": "Description of the duties to be performed",
    "h1b_supervisor_job_title": "Job title of this position's supervisor (if any)",
    "h1b_supervises_others": "Does this position supervise other employees?",
    "h1b_supervised_employee_count": "Number of employees this position supervises",
    "h1b_supervised_employee_level": ("Level of the employees supervised "
                                      "(subordinate or peer)"),
    "h1b_travel_required": "Is travel required to perform the job duties?",
    "h1b_travel_details": "Details of the travel required (areas, frequency, nature)",
    # --- minimum job requirements (Section E.b) - the JOB's minimum, not the
    # beneficiary's own education.
    "h1b_min_education": ("Minimum U.S. diploma/degree the JOB requires (none / "
                          "high_school / associate / bachelor / master / "
                          "doctorate / other)"),
    "h1b_min_education_other": ("If 'other degree', the diploma/degree required "
                                "(e.g. JD, MD)"),
    "h1b_min_education_field": "Major(s) / field(s) of study required",
    "h1b_second_degree_required": "Does the job require a second U.S. diploma/degree?",
    "h1b_second_degree_detail": ("The second degree and the major(s)/field(s) it "
                                 "must be in"),
    "h1b_training_required": "Is training required for the job?",
    "h1b_training_months": "Months of training required",
    "h1b_training_fields": "Field(s)/name(s) of the training required",
    "h1b_experience_required": "Is employment experience required?",
    "h1b_experience_months": "Months of experience required",
    "h1b_experience_occupation": "Occupation the required experience must be in",
    "h1b_special_requirements": ("Special requirements: specific skills, "
                                 "licenses, certificates, certifications"),
    # --- place of employment (Section E.c)
    "worksite_address_line1": "Worksite street address",
    "h1b_worksite_address2": "Worksite address line 2 (if any)",
    "worksite_address_city": "Worksite city",
    "h1b_worksite_county": "Worksite county",
    "worksite_address_state": "Worksite state / district / territory",
    "worksite_address_zip": "Worksite postal code",
    "h1b_multiple_worksites": ("Will work be performed at more than one worksite "
                               "or somewhere other than the address above?"),
    "h1b_additional_worksites": ("The other place(s) of employment (each MSA, "
                                 "city/township/county, and state)"),
    # --- ETA-9035 (LCA) keys the shared table does not name, so the prefill
    # report reads as a question rather than a variable name.
    "h1b_employer_address1": "Employer's street address (as stated on the LCA)",
    "h1b_employer_address2": "Employer's address line 2 (as stated on the LCA)",
    "h1b_employer_city": "Employer's city (as stated on the LCA)",
    "h1b_employer_state": "Employer's state (as stated on the LCA)",
    "h1b_employer_postal_code": "Employer's postal code (as stated on the LCA)",
    "h1b_employer_phone": "Employer's telephone number (as stated on the LCA)",
    "h1b_wage_from_dollars": "Offered wage - dollars (the wage-range 'from' amount)",
    "h1b_wage_from_cents": "Offered wage - cents",
    "h1b_wage_to_dollars": "Top of the offered wage range - dollars (if a range)",
    "h1b_wage_to_cents": "Top of the offered wage range - cents (if a range)",
    "h1b_prevailing_wage_dollars": "Prevailing wage - dollars",
    "h1b_prevailing_wage_cents": "Prevailing wage - cents",
    "h1b_prevailing_wage_unit": "Prevailing wage is per (hour / week / biweekly / month / year)",
    "h1b_pw_source": "Source of the prevailing wage (PWD / OES / other survey)",
    "h1b_pw_wage_level": "OES wage level (I / II / III / IV / N/A)",
    "h1b_pw_oes_source_year": "OES wage source year",
    "h1b_pw_other_source_year": "Other-survey source year",
    "h1b_pw_survey_publisher": "Publisher of the other wage survey",
    "h1b_pw_survey_name": "Name of the other wage survey",
    "h1b_total_worker_positions": "Total worker positions requested for certification",
    "h1b_worksite_worker_count": ("Estimated number of workers at this place of "
                                  "employment"),
    "h1b_secondary_entity": "Will the worker be placed at a secondary entity?",
    "h1b_secondary_entity_name": "Legal business name of the secondary entity",
    "h1b_hiring_official_last_name": "Hiring/designated official's last name",
    "h1b_hiring_official_first_name": "Hiring/designated official's first name",
    "h1b_hiring_official_middle_initial": "Hiring/designated official's middle initial",
    "h1b_hiring_official_title": "Hiring/designated official's job title",
    "h1b_preparer_last_name": "Preparer's last name (if someone else prepared this)",
    "h1b_preparer_first_name": "Preparer's first name",
    "h1b_preparer_middle_initial": "Preparer's middle initial",
    "h1b_preparer_firm_name": "Preparer's firm/business name",
    "h1b_preparer_email": "Preparer's e-mail address",
}


def label_for_key(key: str) -> str:
    """Applicant-facing wording: this module's table first, then the shared H1B
    table and its humanized fallback. Never a raw key."""
    return FIELD_WORDING.get(key) or h1b_forms.label_for_key(key)


def _worded(items) -> list[dict]:
    """Re-word a build_fill missing list through the fuller local table."""
    return [{"key": item["key"], "label": label_for_key(item["key"])}
            for item in items]


# ---------------------------------------------------------------------------
# Derivations. Exactly three, each grounded and each reported by name.
# ---------------------------------------------------------------------------

# Worksite facts this codebase records under two spellings (the LCA/9141 map
# uses worksite_address_*, the wage/counsel surfaces use worksite_*). Reading
# the sibling key is an ALIAS of the same recorded fact - never a new fact.
WORKSITE_ALIASES = {
    "worksite_address_line1": ("worksite_address1", "worksite_line1"),
    "worksite_address_city": ("worksite_city",),
    "worksite_address_state": ("worksite_state",),
    "worksite_address_zip": ("worksite_zip", "worksite_postal_code"),
    "h1b_worksite_county": ("worksite_county",),
}

_DERIVATION_SOURCES = {
    "h1b_visa_classification": "this case's own visa type",
    "soc_title": "DOL OFLC's own occupation title for the SOC code on file",
}
for _target, _alts in WORKSITE_ALIASES.items():
    _DERIVATION_SOURCES[_target] = (
        f"the same worksite fact already recorded as {' / '.join(_alts)}")

# The classification symbol printed on an H-1B PWD request. Written only when
# the case really is an H-1B case and the petitioner recorded no symbol - it is
# read off the case, never assumed.
_CLASSIFICATION_BY_VISA_TYPE = {"h1b": "H-1B"}


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _dol_soc_title(soc_code: str) -> str:
    """DOL's own OES occupation title for a SOC code, or '' when the OFLC data
    is not on this server. A lookup in the government's table - never a title
    Ellis composed."""
    if _blank(soc_code):
        return ""
    try:
        from . import wage_data
        title = wage_data.soc_title(soc_code)
    except Exception:  # noqa: BLE001 - an unavailable table derives nothing
        return ""
    return str(title or "").strip()


def derive_pwd_answers(parent, answers: dict) -> dict:
    """The ETA-9141 fill dict, plus every value Ellis derived rather than a
    party recording it.

    Returns {answers, derived:[{key, label, value, source}]}. Pure and
    idempotent: a derivation fires only when the key is genuinely absent, so
    running it twice adds nothing and overwrites nothing a human answered.
    forms.answers_for_form calls this for 'eta-9141', which is why the generic
    prepare endpoint and prepare_pwd_request can never fill differently.
    """
    out = dict(answers or {})
    derived: list[dict] = []

    def _set(key: str, value) -> None:
        if _blank(out.get(key)) and not _blank(value):
            out[key] = value
            derived.append({"key": key, "label": label_for_key(key),
                            "value": value,
                            "source": _DERIVATION_SOURCES.get(key, "the case record")})

    for target, alternates in WORKSITE_ALIASES.items():
        if not _blank(out.get(target)):
            continue
        for alt in alternates:
            if not _blank(out.get(alt)):
                _set(target, out.get(alt))
                break

    _set("h1b_visa_classification",
         _CLASSIFICATION_BY_VISA_TYPE.get(getattr(parent, "visa_type", "")))
    _set("soc_title", _dol_soc_title(out.get("soc_code")))
    return {"answers": out, "derived": derived}


def _derived_report(db, parent, answers: dict) -> list[dict]:
    """Which of the derivable keys in the FINAL fill dict came from Ellis and
    not from a party. A key the petitioner recorded themselves is theirs and is
    never claimed here."""
    petitioner = h1b_filing._petitioner_party(db, parent.id)
    recorded = {k for k, v in ((petitioner.answers or {}) if petitioner else {}).items()
                if not _blank(v)}
    out = []
    for key, source in _DERIVATION_SOURCES.items():
        value = answers.get(key)
        if _blank(value) or key in recorded:
            continue
        out.append({"key": key, "label": label_for_key(key), "value": value,
                    "source": source})
    return out


# ---------------------------------------------------------------------------
# Choice vocabulary - read off the map so the asked question can never drift
# from what the PDF actually accepts.
# ---------------------------------------------------------------------------

def choice_vocabulary(form_key: str = FORM_KEY) -> dict:
    """{answer key: [accepted values]} for every tick/radio question on the
    form, straight from the verified map."""
    m = h1b_forms.load_form_map(form_key)
    out = {key: sorted(options) for key, options in (m["checkboxes"] or {}).items()}
    for key, group in (m["radio_groups"] or {}).items():
        out[key] = sorted((group.get("states") or {}))
    return out


# ---------------------------------------------------------------------------
# Wage + NAICS context. Computed from official data, carried in the PAYLOAD,
# never written onto this form (Section F belongs to DOL).
# ---------------------------------------------------------------------------
_WAGE_CONTEXT_NOTE = (
    "Ellis's own computed OEWS wage level, from DOL OFLC data, shown so the "
    "employer knows what to expect. It is NOT written on the ETA-9141: the "
    "prevailing wage and the wage level are the Department of Labor's answer to "
    "this request, not the employer's statement.")


def _petitioner_answers(db, parent) -> dict:
    petitioner = h1b_filing._petitioner_party(db, parent.id)
    return dict((petitioner.answers or {}) if petitioner else {})


def _wage_lookup_answers(pet: dict) -> dict:
    """The petitioner record in the spelling the wage brain reads. The worksite
    alias runs in BOTH directions (the forms use worksite_address_*, the OEWS
    area resolver reads worksite_*); it is the same recorded fact either way, so
    a case that answered one spelling still gets a real area lookup instead of a
    spurious 'worksite unresolved'."""
    out = dict(pet or {})
    for target, alternates in WORKSITE_ALIASES.items():
        for alt in alternates:
            if _blank(out.get(alt)) and not _blank(out.get(target)):
                out[alt] = out[target]
            elif _blank(out.get(target)) and not _blank(out.get(alt)):
                out[target] = out[alt]
    return out


def _missing_wage_inputs(pet: dict) -> list[dict]:
    missing = []
    if _blank(pet.get("soc_code")):
        missing.append("soc_code")
    if _blank(pet.get("wage_offer")):
        missing.append("wage_offer")
    if not any(not _blank(pet.get(k)) for k in
               ("worksite_area", "worksite_county", "worksite_city",
                "worksite_state")):
        missing.append("worksite")
    return [{"key": k, "label": label_for_key(k)} for k in missing]


def wage_context(db, parent) -> dict:
    """Ellis's computed OFLC wage-level SUGGESTION for this case, or an honest
    account of why there is none. Never a fabricated level, never a value on the
    form. The computation itself is wage_data's, reached through counsel's one
    case-wage assembly so there is a single wage brain, not a third copy of the
    worksite-area resolution ladder."""
    base = {"available": False, "is_suggestion": True, "written_on_form": False,
            "note": _WAGE_CONTEXT_NOTE}
    pet = _wage_lookup_answers(_petitioner_answers(db, parent))
    missing = _missing_wage_inputs(pet)
    if missing:
        return {**base, "missing_inputs": missing,
                "reason": ("this case does not yet record the facts a wage level "
                           "is computed from, so no level was computed")}
    try:
        from . import wage_data
        available = bool(wage_data.is_available())
    except Exception:  # noqa: BLE001
        available = False
    if not available:
        return {**base, "data_status": "unavailable",
                "reason": ("the DOL OFLC wage data is not downloaded on this "
                           "server, so no wage level was computed")}
    try:
        # counsel._compute_case_wage reads only ctx['petitioner_answers']; it
        # owns the availability + area-resolution + degradation ladder, so this
        # module is not a third copy of it.
        from .counsel import _compute_case_wage
    except Exception:  # noqa: BLE001 - a missing seam is not an unresolved area
        return {**base, "data_status": "unavailable",
                "reason": ("the wage-level computation is not available on this "
                           "server, so no level was computed")}
    try:
        result = _compute_case_wage({"petitioner_answers": pet})
    except Exception:  # noqa: BLE001
        result = None
    if not isinstance(result, dict):
        return {**base, "data_status": "worksite_unresolved",
                "reason": ("the worksite could not be resolved to a single OFLC "
                           "wage area, so no wage level was computed; confirm "
                           "the worksite county")}
    level_wages = result.get("level_wages") or {}
    return {**base,
            "available": bool(result.get("available")),
            "area_code": result.get("area_code"),
            "soc_code": result.get("soc_code"),
            "soc_title": result.get("soc_title"),
            "level": result.get("level"),
            "level_wages": result.get("level_wages"),
            "prevailing_wage": level_wages.get(1),
            "offered_annual": result.get("offered_annual"),
            "meets_prevailing": result.get("meets_prevailing"),
            "geo_caveat": result.get("geo_caveat") or "",
            "invalid_2080_conversion": bool(result.get("invalid_2080_conversion")),
            "caveats": result.get("caveats") or [],
            "status": result.get("status"),
            "source": result.get("source") or "",
            "as_of": result.get("as_of") or ""}


def naics_check(answers: dict) -> dict:
    """Validate the employer's NAICS code against the committed Census 2022
    reference. Ellis NEVER rewrites the code: an unverified code is reported as
    unverified (the full Census file is the authority), not as wrong, and not
    replaced by a guess."""
    code = str((answers or {}).get("employer_naics") or "").strip()
    if not code:
        return {"code": "", "checked": False,
                "reason": "no NAICS code is on file for this employer"}
    try:
        from ..providers import occupation
        result = occupation.validate_naics(code)
    except Exception:  # noqa: BLE001
        return {"code": code, "checked": False,
                "reason": "the NAICS reference is unavailable on this server"}
    if not isinstance(result, dict):
        return {"code": code, "checked": False,
                "reason": "the NAICS reference returned no usable answer"}
    verified = bool(result.get("valid"))
    out = {"code": code, "checked": True, "verified": verified,
           "source": result.get("source") or "census"}
    if verified:
        out["title"] = result.get("title") or ""
    else:
        out["note"] = ("this code is not in Ellis's committed Census subset. "
                       "That is NOT proof it is wrong - the Census reference "
                       "file is the authority. Confirm it before filing.")
    return out


# ---------------------------------------------------------------------------
# The two products of this module.
# ---------------------------------------------------------------------------

def prepare_pwd_request(db, parent, *, actor: str = "",
                        locale: str = "en") -> dict:
    """Fill, store and mint a download for the ETA-9141 prevailing wage request.

    The fill itself is forms.prepare_form - same map loader, same build_fill,
    same storage and same short-lived signed URL as every other prepared form -
    so this function adds only what the PWD needs: the derivation provenance,
    the applicant-worded gap list, the DOL-block honesty notice, and Ellis's
    computed wage/NAICS context (which travels beside the PDF, never on it).

    Raises LookupError when the case's plan has no 'lca' step (forms.py's
    contract). Callers must already have authorized the PETITIONER party.
    """
    result = h1b_forms.prepare_form(db, parent, FORM_KEY, actor=actor)
    # Deterministic re-assembly (pure DB reads) so the payload can name which
    # values were Ellis's derivations rather than a party's own answer.
    answers = h1b_forms.answers_for_form(db, parent, FORM_KEY)
    return {
        "form_key": FORM_KEY,
        "form_name": "ETA-9141, Application for Prevailing Wage Determination",
        "document_id": result["document_id"],
        "download_url": result["download_url"],
        "expires_in": result["expires_in"],
        "filled_count": result["filled_count"],
        "total_mapped": result["total_mapped"],
        "missing": _worded(result["missing"]),
        # This blank carries no signature widget at all - it is signed inside
        # FLAG - so the human-only list is empty by construction, not by
        # oversight. The notices below say who acts.
        "human_only": result["human_only"],
        "derived": _derived_report(db, parent, answers),
        "choice_vocabulary": choice_vocabulary(),
        "wage": wage_context(db, parent),
        "naics": naics_check(answers),
        "preparation_notice": tr("flag.pwd_preparation", locale),
        "human_acts_notice": tr("flag.human_acts", locale),
        "dol_use_only_notice": tr("flag.dol_block", locale),
        "filed_at": FLAG_URL,
    }


def lca_prefill_report(db, parent, *, locale: str = "en") -> dict:
    """What Ellis can fill on the ETA-9035 (LCA) and what is still outstanding,
    in the employer's own words, BEFORE they sign in to FLAG.

    This reports the REAL fill: the same answers assembly and the same
    build_fill plan the ETA-9035 prepare endpoint runs, so what it promises and
    what the PDF contains cannot disagree. Values are not echoed - only which
    questions are answered and which are not.

    Raises LookupError when the case's plan has no 'lca' step.
    """
    answers = h1b_forms.answers_for_form(db, parent, LCA_FORM_KEY)
    plan = h1b_forms.build_fill(LCA_FORM_KEY, answers)
    ready = [{"key": key, "label": label_for_key(key)}
             for key in plan["filled_keys"]]
    outstanding = _worded(plan["missing"])
    return {
        "form_key": LCA_FORM_KEY,
        "form_name": ("ETA-9035/9035E, Labor Condition Application for "
                      "Nonimmigrant Workers"),
        "intro": tr("flag.lca_report", locale),
        "ellis_can_fill": ready,
        "still_needed": outstanding,
        "counts": {"mapped": plan["total_mapped"], "ellis_can_fill": len(ready),
                   "still_needed": len(outstanding)},
        # Signature and date-signed lines on the LCA, plus the DOL certification
        # block: never Ellis's, on the PDF or in FLAG.
        "human_only": plan["human_only"],
        "human_acts_notice": tr("flag.human_acts", locale),
        "choice_vocabulary": choice_vocabulary(LCA_FORM_KEY),
        "filed_at": FLAG_URL,
    }
