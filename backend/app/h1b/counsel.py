"""H1B counsel layer: RFE anticipation, narrative drafting, evidence compilation.

Three disciplines, each doctrine-bound:

 - rfe_risks is DETERMINISTIC. docs/H1B_RFE_TAXONOMY.md is encoded as DATA in
   RISK_RULES: each ground carries USCIS's own wording, the curing evidence,
   and named predicate signals over the case's real state (party answers,
   employer profile, checklist satisfaction, beneficiary nationality). No model
   is ever in this loop — a risk either fires from facts on record or it does
   not exist. Severity follows the taxonomy's cross-cutting notes: when two or
   more highest-weight composite grounds fire together, each unpinned composite
   ground escalates one level.

 - draft_narrative is GROUNDED OR DROPPED. The draft uses ONLY facts assembled
   from the case record (petitioner party answers + employer profile +
   beneficiary degree facts from accepted documents' extracted_fields); every
   fact carries its source; anything unknown lands in missing_facts, never in
   the text. The live Kimi K3 path is verified deterministically (no number in
   the draft that is not in the facts) and falls back to the deterministic
   local template — the same honest fallback hermetic tests exercise. The
   local fallback lives HERE, not in providers/kimi.py (that file is another
   wave's).

 - evidence_index / build_evidence_cover are HONEST. The exhibit list follows
   the umbrella checklist's order and includes required-but-missing items as
   missing — a cover page never implies a document that is not on file.

Every user-facing payload the API layer builds from this module carries the
attorney disclaimer from h1b/disclaimer.py. USCIS wording and regulatory cites
stay in English by design (they quote the government's own language, like
guidance.STEP_FACTS); every Ellis-authored user-visible string here carries
en + zh-CN + zh-Hant.

Party wall: risk facts include petitioner wage numbers; redact_risks_for_roles
strips them for a beneficiary-bound caller. Narratives and the evidence index
are petitioner-or-admin surfaces (enforced in counsel_api).
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import textwrap

from sqlalchemy import select

from .. import checklist_intake, dates, models
from ..providers import kimi as _kimi
from ..providers import pdfgen
from . import models as h1b_models
from .disclaimer import disclaimer

# The research pass this module's rules encode. Re-verify before a filing
# season; the taxonomy names its own sources and confidence per section.
TAXONOMY_AS_OF = "2026-08-09"
TAXONOMY_DOC = "docs/H1B_RFE_TAXONOMY.md"

SEVERITIES = ("advisory", "elevated", "high")

NARRATIVE_KINDS = ("support_letter",)

# The exact label the product contract pins for every narrative draft.
DRAFT_LABEL = {
    "en": "DRAFT — for review by a licensed immigration attorney",
    "zh-CN": "草稿——须经持牌移民律师审阅",
    "zh-Hant": "草稿——須經持牌移民律師審閱",
}

# Ellis-authored user-visible strings (en + zh-CN + zh-Hant parity contract).
COUNSEL_STRINGS = {
    "severity.advisory": {"en": "Advisory", "zh-CN": "提示", "zh-Hant": "提示"},
    "severity.elevated": {"en": "Elevated", "zh-CN": "较高风险", "zh-Hant": "較高風險"},
    "severity.high": {"en": "High", "zh-CN": "高风险", "zh-Hant": "高風險"},
    "status.accepted": {"en": "Accepted", "zh-CN": "已审核接受", "zh-Hant": "已審核接受"},
    "status.submitted": {"en": "Submitted", "zh-CN": "已提交", "zh-Hant": "已提交"},
    "status.missing": {"en": "Missing", "zh-CN": "缺失", "zh-Hant": "缺失"},
}


def counsel_string(key: str, locale: str = "en") -> str:
    entry = COUNSEL_STRINGS.get(key) or {}
    return entry.get(locale) or entry.get("en") or key


def _today() -> _dt.date:  # seam so time-dependent rules are deterministic in tests
    return _dt.date.today()


# ---------------------------------------------------------------------------
# Case context: the one assembly every deterministic predicate reads.
# ---------------------------------------------------------------------------

# Document types whose ACCEPTED extracted_fields may ground beneficiary degree
# facts, in authority order (the third-party evaluation outranks the raw
# certificate for equivalency statements).
_DEGREE_DOC_TYPES = ("credential_evaluation", "degree_certificate",
                     "graduation_certificate", "transcript")

_DEGREE_FACT_ALIASES = {
    "degree_name": ("degree_name", "degree", "degree_title"),
    "degree_field": ("degree_field", "major", "field_of_study"),
    "degree_institution": ("institution", "university", "school"),
    "degree_equivalency": ("us_equivalency", "equivalency"),
}

# Checklist item statuses that genuinely satisfy a requirement. An upload alone
# never fulfils (intake doctrine) — only the explicit Submit, or a waiver.
_SATISFIED_STATUSES = ("submitted", "waived")


def _petitioner_party(db, application_id: str):
    return db.execute(select(h1b_models.CaseParty).where(
        h1b_models.CaseParty.application_id == application_id,
        h1b_models.CaseParty.role == "petitioner")).scalars().first()


def _beneficiary_party(db, application_id: str):
    return db.execute(select(h1b_models.CaseParty).where(
        h1b_models.CaseParty.application_id == application_id,
        h1b_models.CaseParty.role == "beneficiary")).scalars().first()


def _accepted_document_facts(db, case) -> tuple[dict, dict]:
    """Beneficiary degree facts read from ACCEPTED documents' extracted_fields.
    'Accepted' means admin-approved or explicitly submitted against a checklist
    item — never a bare upload. Returns (facts, sources) where every fact names
    the document it came from (grounded-or-dropped)."""
    submitted_ids = {s.document_id for s in db.execute(
        select(models.ChecklistSubmission).where(
            models.ChecklistSubmission.application_id == case.id,
            models.ChecklistSubmission.status == "submitted")).scalars().all()}
    docs = db.execute(select(models.StoredDocument).where(
        models.StoredDocument.application_id == case.id)).scalars().all()
    order = {t: i for i, t in enumerate(_DEGREE_DOC_TYPES)}
    candidates = sorted(
        (d for d in docs if d.doc_type in order
         and not (d.page_classification or {}).get("reject")
         and (d.approved or d.id in submitted_ids)),
        key=lambda d: order[d.doc_type])
    facts: dict = {}
    sources: dict = {}
    for doc in candidates:
        extracted = doc.extracted_fields or {}
        for canon, aliases in _DEGREE_FACT_ALIASES.items():
            if canon in facts:
                continue
            for alias in aliases:
                value = extracted.get(alias)
                if value not in (None, ""):
                    facts[canon] = value
                    sources[canon] = f"document:{doc.id}"
                    break
    return facts, sources


# PRC nationality — exact normalized values only. Deliberately NOT a substring
# match: "Taiwan, Republic of China" must never read as PRC.
_PRC_NATIONALITY_VALUES = frozenset({
    "cn", "chn", "china", "pr china", "p.r. china", "p. r. china",
    "people's republic of china", "peoples republic of china",
    "china (mainland)", "mainland china", "中国", "中國",
})


def _beneficiary_nationality(db, case, parent_answers: dict) -> str:
    for key in ("citizenship_country", "nationality", "passport_nationality",
                "birth_country"):
        value = parent_answers.get(key)
        if value not in (None, ""):
            return str(value)
    # Fall back to the accepted passport's own extracted nationality.
    doc = db.execute(select(models.StoredDocument).where(
        models.StoredDocument.application_id == case.id,
        models.StoredDocument.doc_type == "passport")).scalars().first()
    if doc is not None and not (doc.page_classification or {}).get("reject"):
        value = (doc.extracted_fields or {}).get("nationality")
        if value not in (None, ""):
            return str(value)
    return ""


# ---------------------------------------------------------------------------
# Official-data seams (Agent 1 wage_data / Agent 2 occupation). Two RFE signals
# are upgraded from self-reported to COMPUTED: the OEWS wage LEVEL and the
# below-prevailing check come from DOL OFLC data (a pure offline computation
# over the cached wage CSVs); an advisory SOC-mismatch signal is grounded on
# CDC NIOCCS. Both providers are imported LAZILY and degrade to nothing on any
# failure — missing module, missing function, unconfigured data, or a raised
# error. When the data is unavailable the deterministic rules fall back to the
# petitioner's self-reported answers, exactly as before this seam existed. A
# computed result carries the DOL source, its as_of date, and the geo/label
# caveats (execution-class honesty), and is never written into a filing.
#
# Contract consumed (Agent 1 wage_data):
#   is_available() -> bool
#   lookup_area(query) -> {"area_code", "ambiguous", "candidates", ...}
#   compute_wage_level(*, area_code, soc_code, offered_wage, wage_unit) -> dict
#     carrying level (1-4 or None), level_wages ({1..4} annual or None),
#     meets_prevailing (bool | None), geo_level, geo_caveat,
#     invalid_2080_conversion, label, status, source, as_of.
# Contract consumed (Agent 2 occupation):
#   classify_nioccs(*, occupation_text, industry_text) -> {"soc": [{code, title,
#     probability}], "naics": [...], "live": bool, "caveat": str}.
# ---------------------------------------------------------------------------

def _wage_data_module():
    try:
        from . import wage_data
    except Exception:  # noqa: BLE001 — a missing/broken seam degrades to self-report
        return None
    return wage_data


def _resolve_area_code(wd, pet: dict) -> str:
    """Resolve the petitioner's worksite to an OFLC area code via wage_data's
    Geography lookup. A directly-supplied area code wins; otherwise the worksite
    COUNTY (then city), state-qualified, is looked up. An ambiguous or unresolved
    worksite returns "" so the seam degrades to self-report rather than guess a
    wrong OEWS area — a wrong worksite area is a real filing error."""
    direct = str(pet.get("worksite_area") or "").strip()
    if direct:
        return direct
    lookup = getattr(wd, "lookup_area", None)
    if not callable(lookup):
        return ""
    state = str(pet.get("worksite_state") or "").strip()
    for place in (str(pet.get("worksite_county") or "").strip(),
                  str(pet.get("worksite_city") or "").strip()):
        if not place:
            continue
        query = f"{place}, {state}" if state else place
        try:
            out = lookup(query)
        except Exception:  # noqa: BLE001
            continue
        if (isinstance(out, dict) and out.get("area_code")
                and not out.get("ambiguous")):
            return str(out["area_code"])
    return ""


def _compute_case_wage(ctx: dict) -> dict | None:
    """The DOL OEWS wage-level determination for this case (worksite area + SOC +
    offered wage), or None when the OFLC data is unavailable or the case lacks
    those inputs. None means 'fall back to the self-reported answers' — never a
    fabricated level. Every failure degrades to None (honest, offline-first)."""
    wd = _wage_data_module()
    is_avail = getattr(wd, "is_available", None)
    compute = getattr(wd, "compute_wage_level", None)
    if not (callable(is_avail) and callable(compute)):
        return None
    try:
        if not is_avail():
            return None
    except Exception:  # noqa: BLE001
        return None
    pet = ctx["petitioner_answers"]
    soc_code = str(pet.get("soc_code") or "").strip()
    offered = _num(pet.get("wage_offer"))
    if not soc_code or offered is None:
        return None
    area_code = _resolve_area_code(wd, pet)
    if not area_code:
        return None
    unit = str(pet.get("wage_offer_unit") or "year").strip().lower() or "year"
    try:
        result = compute(area_code=area_code, soc_code=soc_code,
                         offered_wage=offered, wage_unit=unit)
    except Exception:  # noqa: BLE001 — any signature/runtime failure degrades
        return None
    return result if isinstance(result, dict) else None


def _computed_label_caveat(cw) -> str | None:
    """The 'the 2080-hour conversion is invalid' note when DOL labelled the row
    High/Annual Wage — surfaced, never silently converted away."""
    if isinstance(cw, dict) and cw.get("invalid_2080_conversion"):
        return cw.get("status") or (
            "DOL labelled this wage row un-annualisable (High/Annual Wage); the "
            "2080-hour conversion is invalid and no level was computed.")
    return None


def _occupation_module():
    try:
        from ..providers import occupation
    except Exception:  # noqa: BLE001
        return None
    return occupation


def _classified_top_soc(ctx: dict) -> tuple[str, float | None]:
    """(top CDC NIOCCS SOC code, probability) for the petition's stated duties,
    or ("", None) when the classifier is unavailable. Cached on ctx so the fact
    getters reuse the one call the signal made. Network-gated and fully
    degrading — NIOCCS is 'no longer supported' and is never a hard dependency."""
    cached = ctx.get("_classified_top_soc")
    if cached is not None:
        return cached
    result: tuple[str, float | None] = ("", None)
    occ = _occupation_module()
    classify = getattr(occ, "classify_nioccs", None)
    pet = _pet(ctx)
    occ_text = str(pet.get("job_title") or pet.get("job_duties")
                   or pet.get("duties") or "").strip()
    if callable(classify) and occ_text:
        try:
            out = classify(occupation_text=occ_text, industry_text="")
            rows = (out or {}).get("soc") or []
            if rows and isinstance(rows[0], dict):
                top = rows[0]
                result = (str(top.get("code") or ""), top.get("probability"))
        except Exception:  # noqa: BLE001 — InvalidClassificationInput / transport
            result = ("", None)
    ctx["_classified_top_soc"] = result
    return result


def case_context(db, case) -> dict:
    """Everything the deterministic signals read, assembled once. Keys:
    parent_answers, petitioner_answers, employer (EmployerProfile row or None),
    checklist_ids / required / satisfied (item-id sets), degree facts,
    beneficiary_nationality, case_kind, today."""
    parent_answers = dict(case.answers or {})
    petitioner = _petitioner_party(db, case.id)
    petitioner_answers = dict((petitioner.answers or {}) if petitioner else {})
    employer = None
    if petitioner is not None and petitioner.employer_profile_id:
        employer = db.get(h1b_models.EmployerProfile,
                          petitioner.employer_profile_id)
    state = checklist_intake.checklist_state(db, case)
    items = [i for i in (state.get("items") or []) if i.get("kind") == "document"]
    degree_facts, degree_sources = _accepted_document_facts(db, case)
    ctx = {
        "parent_answers": parent_answers,
        "petitioner_answers": petitioner_answers,
        "employer": employer,
        "checklist_items": items,
        "checklist_ids": {str(i.get("id")) for i in items},
        "required": {str(i.get("id")) for i in items if i.get("required")},
        "satisfied": {str(i.get("id")) for i in items
                      if i.get("status") in _SATISFIED_STATUSES},
        "degree_facts": degree_facts,
        "degree_fact_sources": degree_sources,
        "beneficiary_nationality": _beneficiary_nationality(db, case, parent_answers),
        "case_kind": str(parent_answers.get("h1b_case_kind") or ""),
        "today": _today(),
    }
    # Upgrade wage self-report to a COMPUTED DOL determination when the data and
    # the case inputs are present; None (the common case) means self-report
    # governs, unchanged. A monkeypatched ctx that omits this key degrades the
    # same way.
    ctx["computed_wage"] = _compute_case_wage(ctx)
    return ctx


# ---------------------------------------------------------------------------
# Small named predicates (the taxonomy's deterministic signals).
# ---------------------------------------------------------------------------

def _pet(ctx: dict) -> dict:
    return ctx["petitioner_answers"]


def _truthy_yes(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("yes", "y", "true", "1")


def _num(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value or "").strip().replace(",", "").replace("$", "")
    try:
        return float(s)
    except ValueError:
        return None


def _squash(value) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _norm_phrase(value) -> str:
    return " ".join(str(value or "").split()).casefold()


def _degree_field(ctx: dict) -> str:
    for key in ("degree_field", "degree_major", "major"):
        value = ctx["parent_answers"].get(key)
        if value not in (None, ""):
            return str(value)
    return str(ctx["degree_facts"].get("degree_field") or "")


def _acceptable_fields(ctx: dict) -> list[str]:
    raw = _pet(ctx).get("acceptable_degree_fields")
    if raw in (None, ""):
        raw = _pet(ctx).get("degree_fields")
    if raw in (None, ""):
        return []
    if isinstance(raw, (list, tuple)):
        parts = [str(x) for x in raw]
    else:
        parts = re.split(r"[,;/]|\bor\b", str(raw))
    return [p.strip() for p in parts if p.strip()]


def _worksite_location(pet_answers: dict) -> str:
    parts = [pet_answers.get("worksite_address_line1"),
             pet_answers.get("worksite_city"), pet_answers.get("worksite_state")]
    return ", ".join(str(p) for p in parts if p not in (None, ""))


def _employer_location(employer) -> str:
    if employer is None:
        return ""
    parts = [employer.address_line1, employer.city, employer.state]
    return ", ".join(str(p) for p in parts if p)


def _status_expiry_iso(ctx: dict) -> str:
    return dates.normalize_any(ctx["parent_answers"].get("current_status_expiry"),
                               kind="expiry", us_numeric=True)


# Generic-title heuristic straight from taxonomy §1: "Analyst / Consultant /
# Specialist / Coordinator" with no technical qualifier in the title.
_GENERIC_TITLE_WORDS = ("analyst", "consultant", "specialist", "coordinator")
_TITLE_TECH_QUALIFIERS = (
    "software", "data", "systems", "system", "security", "machine learning",
    "cloud", "network", "database", "hardware", "embedded", "computer",
    "engineer", "developer", "scientist", "devops", "sre")

# EAR-sensitive sectors by NAICS prefix (taxonomy §6). A prefix is a HINT for a
# review-advised flag, never a determination — Ellis cannot know the industry.
SENSITIVE_NAICS_PREFIXES = {
    "3254": "pharmaceutical / biotech manufacturing",
    "3341": "computer and peripheral equipment (advanced computing)",
    "3344": "semiconductors and electronic components",
    "3345": "navigational / measuring / electromedical instruments",
    "3364": "aerospace products and parts",
    "5112": "software publishers (EDA / encryption)",
    "517": "telecommunications",
    "5417": "scientific research and development (incl. quantum / AI)",
}


def _sig_wage_level_is_one(ctx: dict) -> bool:
    computed = ctx.get("computed_wage")
    if computed is not None:
        # DOL data present: the wage level is what the OFLC data computes for the
        # offered wage, NOT what was self-reported. A None level means the offer
        # sits below prevailing — a different, more severe signal owns that case,
        # so this one stays silent rather than double-counting.
        return computed.get("level") == 1
    level = str(_pet(ctx).get("wage_level") or "").strip().upper()
    return level in ("I", "1", "LEVEL I", "LEVEL 1")


def _sig_job_title_generic(ctx: dict) -> bool:
    title = str(_pet(ctx).get("job_title") or "").lower()
    if not title:
        return False
    return (any(w in title for w in _GENERIC_TITLE_WORDS)
            and not any(q in title for q in _TITLE_TECH_QUALIFIERS))


def _sig_degree_field_mismatch(ctx: dict) -> bool:
    field = _norm_phrase(_degree_field(ctx))
    acceptable = [_norm_phrase(f) for f in _acceptable_fields(ctx)]
    if not field or not acceptable:
        return False  # unknown is asked, never presumed mismatched
    return not any(field in acc or acc in field for acc in acceptable)


def _sig_credential_evaluation_unsatisfied(ctx: dict) -> bool:
    return ("credential_evaluation" in ctx["checklist_ids"]
            and "credential_evaluation" not in ctx["satisfied"])


def _sig_exactly_one_chinese_degree_document(ctx: dict) -> bool:
    pair = {"degree_certificate", "graduation_certificate"}
    if not pair <= ctx["checklist_ids"]:
        return False
    return len(ctx["satisfied"] & pair) == 1


def _sig_worksite_declared_third_party(ctx: dict) -> bool:
    return _truthy_yes(_pet(ctx).get("h1b_worksite_third_party"))


def _sig_worksite_differs_from_employer(ctx: dict) -> bool:
    employer = ctx["employer"]
    if employer is None:
        return False
    pet = _pet(ctx)
    w_line1, e_line1 = _squash(pet.get("worksite_address_line1")), _squash(employer.address_line1)
    if w_line1 and e_line1:
        return w_line1 != e_line1
    w_city, e_city = _squash(pet.get("worksite_city")), _squash(employer.city)
    if w_city and e_city:
        return w_city != e_city
    return False


def _sig_parent_company_abroad(ctx: dict) -> bool:
    employer = ctx["employer"]
    if employer is None or not employer.parent_company_country:
        return False
    return _squash(employer.parent_company_country) not in (
        "us", "usa", "unitedstates", "unitedstatesofamerica")


def _sig_subsidiary_established_within_2_years(ctx: dict) -> bool:
    employer = ctx["employer"]
    if employer is None or not employer.year_established:
        return False
    age_years = ctx["today"].year - int(employer.year_established)
    return 0 <= age_years < 2


def _sig_export_control_attestation_absent(ctx: dict) -> bool:
    # An explicit False IS an answer (box 1: no license required); only a
    # missing/blank answer fires. Never defaulted — doctrine.
    pet = _pet(ctx)
    return ("export_control_license_required" not in pet
            or pet.get("export_control_license_required") in (None, ""))


def _sig_beneficiary_nationality_prc(ctx: dict) -> bool:
    return str(ctx["beneficiary_nationality"] or "").strip().lower() in _PRC_NATIONALITY_VALUES


def _sig_naics_in_sensitive_sector(ctx: dict) -> bool:
    employer = ctx["employer"]
    code = str(getattr(employer, "naics_code", "") or "")
    return bool(code) and any(code.startswith(p) for p in SENSITIVE_NAICS_PREFIXES)


def _sig_current_status_expired(ctx: dict) -> bool:
    iso = _status_expiry_iso(ctx)
    return bool(iso) and iso < ctx["today"].isoformat()


def _sig_wage_offer_below_prevailing(ctx: dict) -> bool:
    computed = ctx.get("computed_wage")
    if computed is not None and computed.get("meets_prevailing") is not None:
        # The DOL Level-I prevailing wage is authoritative over the self-report:
        # offered_annual < computed Level-I => meets_prevailing False => fire.
        return computed["meets_prevailing"] is False
    pet = _pet(ctx)
    offer, prevailing = _num(pet.get("wage_offer")), _num(pet.get("prevailing_wage"))
    if offer is None or prevailing is None:
        return False
    offer_unit = _squash(pet.get("wage_offer_unit"))
    prevailing_unit = _squash(pet.get("prevailing_wage_unit"))
    if offer_unit and prevailing_unit and offer_unit != prevailing_unit:
        return False  # hourly vs annual is incomparable, not a violation
    return offer < prevailing


def _sig_case_kind_is_extension(ctx: dict) -> bool:
    return ctx["case_kind"] == "extension"


def _sig_no_pay_evidence_on_file(ctx: dict) -> bool:
    return ("employer_financials" in ctx["checklist_ids"]
            and "employer_financials" not in ctx["satisfied"])


def _sig_soc_title_duties_mismatch(ctx: dict) -> bool:
    # Grounded ONLY on a real classifier disagreement: fires when the petition
    # states a SOC code AND CDC NIOCCS classifies the stated duties to a
    # DIFFERENT top SOC. When no SOC is stated, or the classifier is
    # unavailable, this stays silent — never a presumed mismatch.
    stated = _squash(_pet(ctx).get("soc_code"))
    if not stated:
        return False
    classified, _prob = _classified_top_soc(ctx)
    classified = _squash(classified)
    if not classified:
        return False
    return classified != stated


SIGNALS = {
    "wage_level_is_one": _sig_wage_level_is_one,
    "job_title_generic_without_qualifier": _sig_job_title_generic,
    "degree_field_not_in_acceptable_fields": _sig_degree_field_mismatch,
    "credential_evaluation_unsatisfied": _sig_credential_evaluation_unsatisfied,
    "exactly_one_chinese_degree_document": _sig_exactly_one_chinese_degree_document,
    "worksite_declared_third_party": _sig_worksite_declared_third_party,
    "worksite_differs_from_employer_address": _sig_worksite_differs_from_employer,
    "parent_company_abroad": _sig_parent_company_abroad,
    "subsidiary_established_within_2_years": _sig_subsidiary_established_within_2_years,
    "export_control_attestation_absent": _sig_export_control_attestation_absent,
    "beneficiary_nationality_prc": _sig_beneficiary_nationality_prc,
    "naics_in_sensitive_sector": _sig_naics_in_sensitive_sector,
    "current_status_expired": _sig_current_status_expired,
    "wage_offer_below_prevailing_wage": _sig_wage_offer_below_prevailing,
    "case_kind_is_extension": _sig_case_kind_is_extension,
    "no_pay_evidence_on_file": _sig_no_pay_evidence_on_file,
    "soc_title_duties_mismatch": _sig_soc_title_duties_mismatch,
}

# Per-risk transparency facts (what fired and from where). Petitioner-private
# numbers are redacted for beneficiary callers by redact_risks_for_roles.
FACT_GETTERS = {
    "wage_level": lambda ctx: _pet(ctx).get("wage_level"),
    "wage_offer": lambda ctx: _pet(ctx).get("wage_offer"),
    "prevailing_wage": lambda ctx: _pet(ctx).get("prevailing_wage"),
    "job_title": lambda ctx: _pet(ctx).get("job_title"),
    "degree_field": _degree_field,
    "acceptable_degree_fields": _acceptable_fields,
    "worksite_location": lambda ctx: _worksite_location(_pet(ctx)),
    "employer_location": lambda ctx: _employer_location(ctx["employer"]),
    "h1b_worksite_third_party": lambda ctx: _pet(ctx).get("h1b_worksite_third_party"),
    "parent_company_country": lambda ctx: getattr(ctx["employer"], "parent_company_country", ""),
    "year_established": lambda ctx: getattr(ctx["employer"], "year_established", 0) or None,
    "beneficiary_nationality": lambda ctx: ctx["beneficiary_nationality"],
    "naics_code": lambda ctx: getattr(ctx["employer"], "naics_code", ""),
    "current_status_expiry": _status_expiry_iso,
    "case_kind": lambda ctx: ctx["case_kind"],
    "chinese_degree_documents_on_file": lambda ctx: sorted(
        ctx["satisfied"] & {"degree_certificate", "graduation_certificate"}),
    # Computed DOL determination (present only when the OFLC data grounded the
    # signal). The level and the prevailing floor are petitioner-private numbers;
    # the caveats and source are methodology notes, safe to show either party.
    "computed_wage_level": lambda ctx: (ctx.get("computed_wage") or {}).get("level"),
    "computed_prevailing_wage": lambda ctx: (
        (ctx.get("computed_wage") or {}).get("level_wages") or {}).get(1),
    "wage_determination_source": lambda ctx: (
        ctx.get("computed_wage") or {}).get("source"),
    "wage_geo_caveat": lambda ctx: (ctx.get("computed_wage") or {}).get("geo_caveat"),
    "wage_label_caveat": lambda ctx: _computed_label_caveat(ctx.get("computed_wage")),
    # NIOCCS classification grounding for the SOC-mismatch advisory.
    "stated_soc_code": lambda ctx: _pet(ctx).get("soc_code"),
    "nioccs_suggested_soc": lambda ctx: _classified_top_soc(ctx)[0],
    "nioccs_probability": lambda ctx: _classified_top_soc(ctx)[1],
}

_PETITIONER_PRIVATE_FACT_KEYS = frozenset({"wage_offer", "prevailing_wage",
                                           "wage_level", "computed_wage_level",
                                           "computed_prevailing_wage"})
REDACTED = "[petitioner-private]"


# ---------------------------------------------------------------------------
# RISK_RULES — docs/H1B_RFE_TAXONOMY.md as data. uscis_wording quotes the
# government's own phrasing (English by design); title is Ellis's label.
# ---------------------------------------------------------------------------

# Taxonomy cross-cutting notes: highest-weight composite signals. Two or more
# of these firing together escalates each unpinned member one severity level.
HIGH_WEIGHT_COMPOSITES = frozenset({
    "wage_level_1", "third_party_worksite", "foreign_parent_young_subsidiary",
    "degree_field_mismatch", "missing_pay_evidence_for_extension",
    "export_control_china_sensitive",
})

RISK_RULES = (
    {
        "ground": "wage_level_1",
        "title": {"en": "Wage level I undermines specialty-occupation claims",
                  "zh-CN": "一级工资等级削弱专业职位论证",
                  "zh-Hant": "一級工資等級削弱專業職位論證"},
        "signals": ("wage_level_is_one",),
        "base_severity": "elevated",
        "uscis_wording": "Officers argue Level I (\"basic understanding, close "
                         "supervision\") contradicts claims that duties are "
                         "\"specialized and complex\" under 8 CFR "
                         "214.2(h)(4)(iii)(A) criterion 4; Level I also "
                         "collapses selection odds under the weighted rule.",
        "curing_evidence": [
            "Never rely on criterion 4 with a Level I wage",
            "Explain Level I as entry into a profession that still requires the degree",
            "Show a supervision structure consistent with Level I",
            "Duty-by-duty breakdown mapped to specific degree coursework",
        ],
        "cite": f"{TAXONOMY_DOC} §1 (specialty occupation / wage level), §7",
        "fact_keys": ("wage_level", "computed_wage_level",
                      "wage_determination_source", "wage_geo_caveat",
                      "wage_label_caveat"),
    },
    {
        "ground": "generic_job_title",
        "title": {"en": "Generic job title without a technical qualifier",
                  "zh-CN": "职位名称过于宽泛，缺少技术限定词",
                  "zh-Hant": "職位名稱過於寬泛，缺少技術限定詞"},
        "signals": ("job_title_generic_without_qualifier",),
        "base_severity": "advisory",
        "uscis_wording": "\"The evidence does not establish that the proffered "
                         "position qualifies as a specialty occupation\" under "
                         "8 CFR 214.2(h)(4)(iii)(A) — generic "
                         "Analyst/Consultant/Specialist/Coordinator titles "
                         "without a technical qualifier draw the RFE.",
        "curing_evidence": [
            "Duty-by-duty breakdown with % time allocations mapped to degree coursework",
            "OOH excerpt for the SOC code",
            "Comparable postings from similar-size same-industry employers",
            "Org chart showing where the role sits",
        ],
        "cite": f"{TAXONOMY_DOC} §1 (specialty occupation)",
        "fact_keys": ("job_title",),
    },
    {
        "ground": "degree_field_mismatch",
        "title": {"en": "Degree field not among the stated acceptable fields",
                  "zh-CN": "学位专业不在职位声明的可接受专业范围内",
                  "zh-Hant": "學位專業不在職位聲明的可接受專業範圍內"},
        "signals": ("degree_field_not_in_acceptable_fields",),
        "base_severity": "elevated",
        "uscis_wording": "\"The evidence does not establish that the beneficiary "
                         "is qualified to perform services in a specialty "
                         "occupation\" under 8 CFR 214.2(h)(4)(iii)(C) — "
                         "self-inflicted when the petitioner's own stated "
                         "requirements exclude the beneficiary's major.",
        "curing_evidence": [
            "Course-by-course evaluation showing major coursework substantially "
            "in the required specialty",
            "Combined education+experience evaluation by an evaluator authorized "
            "to grant college credit",
            "Duty-nexus explanation tying the actual degree field to the duties",
        ],
        "cite": f"{TAXONOMY_DOC} §2 (beneficiary qualifications)",
        "fact_keys": ("degree_field", "acceptable_degree_fields"),
    },
    {
        "ground": "missing_credential_evaluation",
        "title": {"en": "Foreign degree without a credential evaluation",
                  "zh-CN": "外国学位缺少第三方学历认证报告",
                  "zh-Hant": "外國學位缺少第三方學歷認證報告"},
        "signals": ("credential_evaluation_unsatisfied",),
        "base_severity": "elevated",
        "uscis_wording": "A foreign degree must be shown \"equivalent to a US "
                         "bachelor's in the specialty\" (8 CFR "
                         "214.2(h)(4)(iii)(C)); a foreign degree with no "
                         "credential evaluation attached is a listed "
                         "deterministic RFE signal.",
        "curing_evidence": [
            "NACES-member credential evaluation stating US bachelor's "
            "equivalency in the specific field",
            "Transcripts showing coursework in the required field",
            "CHESICC / CDGDC verification reports for Chinese credentials",
        ],
        "cite": f"{TAXONOMY_DOC} §2 (beneficiary qualifications)",
        "fact_keys": (),
    },
    {
        "ground": "single_chinese_degree_certificate",
        "title": {"en": "Only one of the two Chinese degree documents on file",
                  "zh-CN": "学位证与毕业证仅提交其一",
                  "zh-Hant": "學位證與畢業證僅提交其一"},
        "signals": ("exactly_one_chinese_degree_document",),
        "base_severity": "high",
        "uscis_wording": "Chinese completion requires BOTH 学位证 (degree "
                         "certificate) AND 毕业证 (graduation certificate); "
                         "submitting only one means no conferred degree is "
                         "proven — equivalency RFE or denial.",
        "curing_evidence": [
            "Submit both certificates with certified translations",
            "CHESICC (学信网) verification for 毕业证",
            "CDGDC verification for 学位证",
        ],
        "cite": f"{TAXONOMY_DOC} §2 (China-specific)",
        "fact_keys": ("chinese_degree_documents_on_file",),
    },
    {
        "ground": "third_party_worksite",
        "title": {"en": "Third-party worksite placement",
                  "zh-CN": "第三方工作地点派驻",
                  "zh-Hant": "第三方工作地點派駐"},
        "signals": ("worksite_declared_third_party",
                    "worksite_differs_from_employer_address"),
        "base_severity": "elevated",
        "uscis_wording": "\"The evidence does not establish that a bona fide "
                         "position in a specialty occupation is available as of "
                         "the start date\" — for third-party placement the "
                         "specialty-occupation analysis runs against the "
                         "END-CLIENT's requirements.",
        "curing_evidence": [
            "MSA + SOW/work order covering the validity period, naming the role "
            "with duties",
            "End-client letter describing duties, required degree, duration, worksite",
            "Evidence of non-speculative work at filing",
            "LCA listing every worksite",
        ],
        "cite": f"{TAXONOMY_DOC} §3 (bona fide job offer / third-party placement)",
        "fact_keys": ("worksite_location", "employer_location",
                      "h1b_worksite_third_party"),
    },
    {
        "ground": "foreign_parent_young_subsidiary",
        "title": {"en": "Newly established US subsidiary of a foreign parent",
                  "zh-CN": "外国母公司新设的美国子公司",
                  "zh-Hant": "外國母公司新設的美國子公司"},
        "signals": ("parent_company_abroad",
                    "subsidiary_established_within_2_years"),
        "require": "all",
        "base_severity": "elevated",
        "uscis_wording": "\"The evidence does not establish that the petitioner "
                         "is a bona fide, operating business\" / ability-to-pay "
                         "questions; FDNS targeting criteria include employers "
                         "\"whose business cannot be verified through publicly "
                         "available information\" and newly formed subsidiaries "
                         "with parent-funded payroll.",
        "curing_evidence": [
            "Federal tax returns, audited financials or bank statements",
            "Payroll records (W-3/941s), lease, business licenses",
            "Documented intercompany services agreement if the parent funds the "
            "subsidiary",
            "Org chart with headcount; verifiable web presence",
        ],
        "cite": f"{TAXONOMY_DOC} §4 (employer viability), §8 (foreign-parent petitioners)",
        "fact_keys": ("parent_company_country", "year_established"),
    },
    {
        "ground": "export_control_unanswered",
        "title": {"en": "I-129 Part 6 export-control attestation unanswered",
                  "zh-CN": "I-129 第六部分出口管制声明未作答",
                  "zh-Hant": "I-129 第六部分出口管制聲明未作答"},
        "signals": ("export_control_attestation_absent",),
        "base_severity": "elevated",
        "uscis_wording": "Part 6 is a petitioner attestation under penalty of "
                         "perjury (EAR 15 CFR 770-774 / ITAR 22 CFR 120-130); "
                         "an unanswered or inconsistent Part 6 means rejection "
                         "or RFE.",
        "curing_evidence": [
            "Documented export-control classification review (ECCN "
            "determination) predating filing",
            "Technology control plan (TCP) if a license is required",
        ],
        "cite": f"{TAXONOMY_DOC} §6 (export control / deemed export)",
        "fact_keys": (),
    },
    {
        "ground": "export_control_china_sensitive",
        "title": {"en": "Deemed-export review advised (PRC national, sensitive "
                        "NAICS sector)",
                  "zh-CN": "建议进行视同出口审查（中国籍受益人，敏感行业）",
                  "zh-Hant": "建議進行視同出口審查（中國籍受益人，敏感行業）"},
        "signals": ("beneficiary_nationality_prc", "naics_in_sensitive_sector"),
        "require": "all",
        # Review advised, permanently: Ellis cannot know the actual technology
        # from a NAICS prefix, so this never claims more than "have counsel
        # review the deemed-export posture". Pinned — never escalated.
        "base_severity": "advisory",
        "severity_pinned": True,
        "uscis_wording": "Beneficiary nationality in Country Group D:1/D:5 makes "
                         "a deemed-export license far more likely in "
                         "EAR-sensitive sectors; review advised — the NAICS "
                         "prefix is a hint, not a determination of the "
                         "employer's actual technology.",
        "curing_evidence": [
            "Export-control classification review (ECCN) by qualified counsel",
            "Fundamental-research exclusion memo where applicable",
            "Technology control plan before any Part 6 box 2 answer",
        ],
        "cite": f"{TAXONOMY_DOC} §6 (deemed export), §8 (national-security vetting)",
        "fact_keys": ("beneficiary_nationality", "naics_code"),
    },
    {
        "ground": "late_filing_risk",
        "title": {"en": "Current status expires before filing",
                  "zh-CN": "现有身份已过期，存在逾期申请风险",
                  "zh-Hant": "現有身份已過期，存在逾期申請風險"},
        "signals": ("current_status_expired",),
        "base_severity": "high",
        "severity_pinned": True,
        "uscis_wording": "An extension \"must be filed before the expiration of "
                         "the current period of stay\"; late filing is "
                         "excusable only for \"extraordinary circumstances "
                         "beyond the control of the petitioner or beneficiary\" "
                         "(8 CFR 214.1(c)(4)).",
        "curing_evidence": [
            "Contemporaneous evidence of the extraordinary circumstance",
            "All pay stubs and I-94 history for the current validity period",
            "Immediate attorney review before any filing decision",
        ],
        "cite": f"{TAXONOMY_DOC} §5 (maintenance of status)",
        "fact_keys": ("current_status_expiry", "case_kind"),
    },
    {
        "ground": "lca_wage_below_prevailing",
        "title": {"en": "Offered wage below the prevailing wage",
                  "zh-CN": "拟付工资低于现行工资标准",
                  "zh-Hant": "擬付工資低於現行工資標準"},
        "signals": ("wage_offer_below_prevailing_wage",),
        "base_severity": "high",
        "severity_pinned": True,
        "uscis_wording": "The LCA obligation requires paying the required wage "
                         "(the higher of actual or prevailing) from day 1; an "
                         "offered wage below the prevailing wage cannot be "
                         "certified and, post-registration, \"an actual offered "
                         "wage lower than the registered wage level\" is a "
                         "denial ground.",
        "curing_evidence": [
            "Raise the offered wage to at least the prevailing wage",
            "Re-verify the OEWS prevailing wage for the SOC/MSA and wage level",
            "Alternative wage-source documentation where OEWS does not apply",
        ],
        "cite": f"{TAXONOMY_DOC} §4 (LCA obligation), §7 (wage consistency)",
        "fact_keys": ("wage_offer", "prevailing_wage", "computed_prevailing_wage",
                      "wage_determination_source", "wage_geo_caveat",
                      "wage_label_caveat"),
    },
    {
        "ground": "missing_pay_evidence_for_extension",
        "title": {"en": "Extension without pay or financial evidence on file",
                  "zh-CN": "延期申请缺少工资或财务证明",
                  "zh-Hant": "延期申請缺少工資或財務證明"},
        "signals": ("case_kind_is_extension", "no_pay_evidence_on_file"),
        "require": "all",
        "base_severity": "elevated",
        "uscis_wording": "\"Submit evidence that the beneficiary has maintained "
                         "valid H-1B status\" — officers sample pay records for "
                         "the current validity period; gaps read as benching.",
        "curing_evidence": [
            "All pay stubs for the current validity period",
            "W-2s and employer financial documents",
            "Leave documentation (FMLA, maternity) explaining any pay gaps",
        ],
        "cite": f"{TAXONOMY_DOC} §5 (maintenance of status), cross-cutting notes",
        "fact_keys": ("case_kind",),
    },
    {
        "ground": "soc_title_duties_mismatch",
        "title": {"en": "Classifier SOC disagrees with the petition's stated SOC",
                  "zh-CN": "职业分类系统建议的 SOC 代码与申请填报的不一致",
                  "zh-Hant": "職業分類系統建議的 SOC 代碼與申請填報的不一致"},
        "signals": ("soc_title_duties_mismatch",),
        "base_severity": "advisory",
        # Pinned advisory: NIOCCS is a signal to CONFIRM, never a determination.
        # CDC states the service is 'no longer supported', so it never escalates
        # and never claims more than 'a human should confirm the SOC'.
        "severity_pinned": True,
        "uscis_wording": "The SOC code is a legal representation on the "
                         "ETA-9035/I-129, not a lookup; when CDC NIOCCS "
                         "classifies the stated duties to a different SOC, an "
                         "officer may question whether the OES prevailing wage "
                         "and the specialty-occupation analysis (8 CFR "
                         "214.2(h)(4)(iii)(A)) used the correct occupation. This "
                         "is an advisory signal to confirm, not a determination.",
        "curing_evidence": [
            "Confirm the SOC code with a licensed attorney before filing",
            "Reconcile the job duties against the selected SOC's OES definition",
            "Re-run the OES prevailing wage under the confirmed SOC and MSA",
        ],
        "cite": f"{TAXONOMY_DOC} §1 (specialty occupation / SOC selection), "
                f"§7 (wage consistency)",
        "fact_keys": ("stated_soc_code", "nioccs_suggested_soc",
                      "nioccs_probability"),
    },
)


def _escalate(severity: str) -> str:
    i = SEVERITIES.index(severity)
    return SEVERITIES[min(i + 1, len(SEVERITIES) - 1)]


def rfe_risks(db, case) -> list[dict]:
    """Deterministic risk read for one parent H1B case. Every entry names its
    fired signals, USCIS's wording, the curing evidence, and the taxonomy cite.
    No model, no probability theater — a rule fires from facts on record or
    stays silent."""
    ctx = case_context(db, case)
    fired: list[tuple[dict, list[str]]] = []
    for rule in RISK_RULES:
        names = [n for n in rule["signals"] if SIGNALS[n](ctx)]
        if rule.get("require", "any") == "all":
            ok = len(names) == len(rule["signals"])
        else:
            ok = bool(names)
        if ok:
            fired.append((rule, names))

    composite_fired = {r["ground"] for r, _ in fired} & HIGH_WEIGHT_COMPOSITES
    out = []
    for rule, names in fired:
        severity = rule["base_severity"]
        if (rule["ground"] in HIGH_WEIGHT_COMPOSITES
                and len(composite_fired) >= 2
                and not rule.get("severity_pinned")):
            severity = _escalate(severity)
        facts = {}
        for key in rule.get("fact_keys", ()):
            value = FACT_GETTERS[key](ctx)
            if value not in (None, "", [], {}):
                facts[key] = value
        out.append({
            "ground": rule["ground"],
            "severity": severity,
            "signals_fired": names,
            "uscis_wording": rule["uscis_wording"],
            "curing_evidence": list(rule["curing_evidence"]),
            "cite": rule["cite"],
            "title": dict(rule["title"]),
            "facts": facts,
        })
    rank = {s: i for i, s in enumerate(SEVERITIES)}
    out.sort(key=lambda r: (-rank[r["severity"]], r["ground"]))
    return out


def redact_risks_for_roles(risks: list[dict], roles: set[str]) -> list[dict]:
    """Party wall on the shared risk read: a caller bound ONLY to the
    beneficiary party sees every ground but never the petitioner's private
    wage numbers. Admins, the petitioner, and unbound org callers (shared reads
    are deferred, matching the pipeline read) see the full facts."""
    if "admin" in roles or "petitioner" in roles or not roles:
        return risks
    redacted = []
    for risk in risks:
        entry = dict(risk)
        facts = dict(risk.get("facts") or {})
        for key in list(facts):
            if key in _PETITIONER_PRIVATE_FACT_KEYS:
                facts[key] = REDACTED
        entry["facts"] = facts
        redacted.append(entry)
    return redacted


# ---------------------------------------------------------------------------
# Narrative drafting (grounded-or-dropped, DRAFT-labeled, attorney-reviewed).
# ---------------------------------------------------------------------------

# The fact slots a support letter needs. Absent -> missing_facts, never text.
REQUIRED_NARRATIVE_FACTS = {
    "support_letter": ("beneficiary_full_name", "employer_legal_name",
                       "job_title", "job_duties", "soc_code", "wage_offer",
                       "worksite_location", "degree_field"),
}

NARRATIVE_SYSTEM = """You draft an H-1B employer support letter for review by a \
licensed immigration attorney.

Rules you must follow, without exception:
- Use ONLY the facts in the provided JSON. Every claim in the letter must be
  traceable to one of them.
- NEVER invent, estimate, or embellish a value. If a fact is not provided, do
  not mention it; the provided missing_facts list already names the gaps.
- No legal conclusions about eligibility; state facts, not judgments.
- Reply JSON: {"draft_text": "..."} and nothing else."""

# Government form numbers that may legitimately appear in a draft without
# being case facts (the grounding check whitelists exactly these digit runs).
_KNOWN_FORM_NUMBERS = frozenset({"129", "9035", "160", "797", "214"})


def _numbers_grounded(text: str, facts: dict) -> bool:
    """Deterministic guard on a live-model draft: every digit run of 3+ in the
    draft must appear in the provided facts (commas stripped on both sides),
    excepting well-known form numbers. An invented wage or date fails here and
    the draft is dropped for the deterministic template."""
    haystack = re.sub(r"[,\s]", "", json.dumps(facts, ensure_ascii=False))
    for run in re.findall(r"\d{3,}", re.sub(r",", "", text or "")):
        if run in _KNOWN_FORM_NUMBERS:
            continue
        if run not in haystack:
            return False
    return True


def narrative_facts(db, case, kind: str = "support_letter") -> tuple[dict, dict, list[str]]:
    """(facts, sources, missing) for a narrative. Facts come only from the case
    record; each carries its provenance. The degree facts here are shared
    lawfully — they are I-129 petition evidence, not private beneficiary data
    (architecture: whitelisted shared facts)."""
    ctx = case_context(db, case)
    facts: dict = {}
    sources: dict = {}

    def put(key: str, value, source: str) -> None:
        if key not in facts and value not in (None, ""):
            facts[key] = value
            sources[key] = source

    parent = ctx["parent_answers"]
    pet = ctx["petitioner_answers"]
    employer = ctx["employer"]

    put("beneficiary_full_name", parent.get("full_name"), "parent_answers")
    put("case_kind", ctx["case_kind"], "parent_answers")
    for key in ("job_title", "soc_code", "soc_title", "wage_offer",
                "wage_offer_unit", "wage_level", "employment_start_date"):
        put(key, pet.get(key), "party_answers.petitioner")
    put("job_duties", pet.get("job_duties") or pet.get("duties"),
        "party_answers.petitioner")
    put("worksite_location", _worksite_location(pet), "party_answers.petitioner")
    if employer is not None:
        put("employer_legal_name", employer.legal_name, "employer_profile")
        put("employer_location", _employer_location(employer), "employer_profile")
        put("parent_company_name", employer.parent_company_name, "employer_profile")
    for key, value in ctx["degree_facts"].items():
        put(key, value, ctx["degree_fact_sources"].get(key, "document"))
    # An explicit beneficiary answer for the degree field outranks extraction.
    put("degree_field", _degree_field(ctx), "parent_answers")

    required = REQUIRED_NARRATIVE_FACTS.get(kind, REQUIRED_NARRATIVE_FACTS["support_letter"])
    missing = [key for key in required if key not in facts]
    return facts, sources, missing


def _local_narrative_text(facts: dict, missing: list[str], kind: str) -> str:
    """Deterministic template narrative from the SAME facts the live path gets.
    Built paragraph by paragraph from present facts only: a fact that was not
    provided produces no sentence, no placeholder value, nothing invented."""
    beneficiary = facts.get("beneficiary_full_name")
    employer = facts.get("employer_legal_name")
    title = facts.get("job_title")
    lines: list[str] = ["To: U.S. Citizenship and Immigration Services", ""]

    subject = "Re: H-1B petition"
    if facts.get("case_kind"):
        subject += f" ({facts['case_kind']})"
    if beneficiary:
        subject += f" — beneficiary {beneficiary}"
    lines += [subject, ""]

    opening = (f"{employer} respectfully submits this letter in support of its "
               f"H-1B petition" if employer
               else "This letter is submitted in support of an H-1B petition")
    if beneficiary:
        opening += f" for {beneficiary}"
    if title:
        opening += f" for the position of {title}"
    if facts.get("soc_code"):
        opening += f" (SOC {facts['soc_code']}"
        if facts.get("soc_title"):
            opening += f", {facts['soc_title']}"
        opening += ")"
    lines += [opening + ".", ""]

    if facts.get("job_duties"):
        lines += [f"The duties of the position are: {facts['job_duties']}", ""]

    if "wage_offer" in facts:
        wage = f"The offered wage is {facts['wage_offer']}"
        if facts.get("wage_offer_unit"):
            wage += f" {facts['wage_offer_unit']}"
        if facts.get("wage_level"):
            wage += f", at OEWS wage level {facts['wage_level']}"
        if facts.get("worksite_location"):
            wage += f", for work performed at {facts['worksite_location']}"
        lines += [wage + ".", ""]
    elif facts.get("worksite_location"):
        lines += [f"The work will be performed at {facts['worksite_location']}.", ""]

    if facts.get("employment_start_date"):
        lines += [f"The intended employment start date is "
                  f"{facts['employment_start_date']}.", ""]

    degree_bits = [facts.get(k) for k in ("degree_name", "degree_field",
                                          "degree_institution")]
    if any(degree_bits):
        sentence = f"{beneficiary or 'The beneficiary'} holds "
        sentence += facts.get("degree_name") or "a degree"
        if facts.get("degree_field"):
            sentence += f" in {facts['degree_field']}"
        if facts.get("degree_institution"):
            sentence += f" from {facts['degree_institution']}"
        if facts.get("degree_equivalency"):
            sentence += (f"; a credential evaluation on file states "
                         f"{facts['degree_equivalency']}")
        lines += [sentence + ".", ""]

    if employer and facts.get("parent_company_name"):
        lines += [f"{employer} is a subsidiary of "
                  f"{facts['parent_company_name']}.", ""]

    lines += ["Every statement above is drawn from the case record; nothing "
              "has been assumed or estimated."]
    if missing:
        lines += ["", "The following facts are not yet on file and must be "
                      "provided and confirmed before this letter is used:"]
        lines += [f"  - {key}" for key in missing]
    return "\n".join(lines)


def draft_narrative(db, principal, case, kind: str = "support_letter") -> dict:
    """A DRAFT narrative for attorney review. Live Kimi K3 when configured
    (strict facts-only prompt + deterministic grounding check); the
    deterministic local template otherwise or whenever the live draft fails
    the check. The draft never claims more than the record holds."""
    if kind not in NARRATIVE_KINDS:
        raise ValueError(f"unknown narrative kind: {kind}")
    facts, sources, missing = narrative_facts(db, case, kind)

    body = ""
    engine = "local_template"
    provider = _kimi.get_provider()
    if getattr(provider, "name", "") != "local_test_provider" and hasattr(provider, "_chat"):
        try:
            out = provider._chat(
                NARRATIVE_SYSTEM,
                json.dumps({"kind": kind, "facts": facts,
                            "missing_facts": missing}, ensure_ascii=False),
                max_tokens=2400, temperature=0.2)
            draft = str((out or {}).get("draft_text") or "").strip()
            if draft and _numbers_grounded(draft, facts):
                body = draft
                engine = getattr(provider, "name", "kimi")
        except Exception:  # noqa: BLE001 — any live failure degrades honestly
            body = ""
    if not body:
        body = _local_narrative_text(facts, missing, kind)
        engine = "local_template"

    text = f"{DRAFT_LABEL['en']}\n\n{body}\n\n{disclaimer('en')}"
    return {"kind": kind,
            "draft_text": text,
            "label": DRAFT_LABEL["en"],
            "label_i18n": dict(DRAFT_LABEL),
            "grounded_facts": facts,
            "fact_sources": sources,
            "missing_facts": missing,
            "engine": engine}


# ---------------------------------------------------------------------------
# Evidence compilation (honest exhibit index + printable cover).
# ---------------------------------------------------------------------------

def evidence_index(db, principal, case) -> list[dict]:
    """Ordered exhibit list following the umbrella checklist's order. Statuses:
    'accepted' (submitted AND approved — the explicit Submit records the
    applicant's approval into the signed-material state, and admin review sets
    the same flag), 'submitted' (a submitted binding whose approval was later
    withdrawn), or 'missing'. Required-but-missing items are INCLUDED,
    honestly — the index never implies a complete pack that is not on file. A
    bound-but-unsubmitted upload does not fulfil (intake doctrine) and reads
    as missing."""
    state = checklist_intake.checklist_state(db, case)
    exhibits: list[dict] = []
    exhibit_no = 1
    for item in state.get("items") or []:
        if item.get("kind") != "document":
            continue
        binding = item.get("binding") or {}
        doc = None
        if binding.get("submitted") and binding.get("document_id"):
            doc = db.get(models.StoredDocument, binding["document_id"])
        if doc is not None:
            status = "accepted" if doc.approved else "submitted"
        else:
            status = "missing"
            if not item.get("required") or item.get("waived"):
                continue  # optional-and-absent: not part of the pack, not a gap
        exhibits.append({
            "exhibit_no": exhibit_no,
            "item_id": str(item.get("id")),
            "title": item.get("label") or str(item.get("id")),
            "doc_type": (doc.doc_type if doc is not None
                         else (item.get("satisfied_by") or [""])[0]),
            "party": item.get("party", "beneficiary"),
            "status": status,
            "document_id": (doc.id if doc is not None else None),
        })
        exhibit_no += 1
    return exhibits


def _pdf_safe(line: str) -> str:
    # pdfgen encodes latin-1; drop what it cannot carry rather than print '?'.
    return line.encode("latin-1", "ignore").decode("latin-1")


def _wrap(text: str, indent: str = "") -> list[str]:
    return textwrap.wrap(text, width=92, initial_indent=indent,
                         subsequent_indent=indent + "  ") or [""]


def build_evidence_cover(db, case) -> bytes:
    """The printable cover: case caption, exhibit index, an explicit
    missing-items section, and the attorney disclaimer. text_pdf artifact —
    deterministic, locally produced, and it says so."""
    exhibits = evidence_index(db, None, case)
    petitioner = _petitioner_party(db, case.id)
    beneficiary = _beneficiary_party(db, case.id)
    employer = None
    if petitioner is not None and petitioner.employer_profile_id:
        employer = db.get(h1b_models.EmployerProfile,
                          petitioner.employer_profile_id)
    steps = db.execute(select(h1b_models.H1bCaseStep).where(
        h1b_models.H1bCaseStep.application_id == case.id)).scalars().all()
    answers = case.answers or {}

    lines: list[str] = []
    lines += ["H-1B PETITION — EVIDENCE INDEX (DRAFT)", ""]
    lines += [f"Case: {case.id}"]
    if answers.get("h1b_case_kind"):
        lines += [f"Case kind: {answers['h1b_case_kind']}"]
    petitioner_name = ((employer.legal_name if employer else "")
                       or (petitioner.display_name if petitioner else ""))
    if petitioner_name:
        lines += [f"Petitioner: {petitioner_name}"]
    beneficiary_name = (answers.get("full_name")
                        or (beneficiary.display_name if beneficiary else ""))
    if beneficiary_name:
        lines += [f"Beneficiary: {beneficiary_name}"]
    for step in steps:
        for field, label in (("lca_number", "LCA number"),
                             ("uscis_receipt_number", "USCIS receipt number"),
                             ("beneficiary_confirmation_number",
                              "Registration confirmation number")):
            value = getattr(step, field)
            if value:
                lines += [f"{label}: {value}"]
    lines += [f"Prepared by Ellis, a non-attorney preparer, on "
              f"{_today().isoformat()}.", ""]

    lines += ["EXHIBITS"]
    on_file = [e for e in exhibits if e["status"] != "missing"]
    missing = [e for e in exhibits if e["status"] == "missing"]
    if not on_file:
        lines += ["  (no documents on file yet)"]
    for e in on_file:
        lines += _wrap(f"Exhibit {e['exhibit_no']}: {e['title']} "
                       f"[{e['party']}] — {e['status']}", indent="  ")

    lines += ["", "REQUIRED ITEMS NOT YET ON FILE"]
    if missing:
        for e in missing:
            lines += _wrap(f"Exhibit {e['exhibit_no']}: {e['title']} "
                           f"[{e['party']}] — MISSING", indent="  ")
    else:
        lines += ["  None — every required checklist item is on file."]

    lines += [""]
    lines += _wrap(disclaimer("en"))
    return pdfgen.text_pdf([_pdf_safe(line) for line in lines],
                           title="H-1B Evidence Index")
