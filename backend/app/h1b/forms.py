"""Official H1B form fill: the government's own I-129 blank and the ETA-9035
preparation copy, plus the mail-ready paper packet.

Doctrine, enforced here rather than trusted to callers:

- NOTHING IS INVENTED. Every written value is a party's own recorded answer;
  an unanswered field stays blank and is reported in `missing` so the case can
  ask. Unfilled beats wrong on a perjury form. Missing answers NEVER block the
  download - a partly-filled official blank is still the honest artifact.
- PERSONAL ACTS STAY PERSONAL. Signature fields, date-of-signature fields and
  the PDF417 barcodes are never written - the map's human-only list is a hard
  skip even if a mapping error ever pointed at one.
- PARTY WALL. The fill dict is assembled through filing.answers_for_step (the
  canonical cross-party-safe builder) plus the ACTING petitioner's own party
  answers (their own form). Beneficiary facts cross into the I-129 only through
  the imported beneficiary whitelist plus the small Part 3/4 vocabulary named
  below - never a wholesale merge. The ETA-9035 receives NO beneficiary facts:
  DOL never sees them.
- EXECUTION-CLASS HONESTY. The ETA-9035 output is watermarked as a preparation
  copy (the real LCA exists only in FLAG); the paper packet's cover sheet says
  plainly that nothing has been submitted and every signature line is still
  blank ink.

This filler is DELIBERATELY separate from consular_forms.fill_official_template:
that path hardcodes '/On' as the tick state and its load_field_map filters to
the consular answer vocabulary. These two forms use per-checkbox recorded
on-states ('/1', '/C', '/ STE ', literal 'X' into text fields) and the H1B
vocabulary, so the map's dicts are consumed directly.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import time
from io import BytesIO

from sqlalchemy import select

from .. import audit, consular_forms, dates, models
from ..providers import pdfgen
from . import filing as h1b_filing
from . import models as h1b_models

# The prevailing wage request exists on this server in two printed editions of
# the government's blank ('eta-9141' expires 05/31/2019, 'eta-9141-2021'
# expires 04/30/2021). They are the SAME petitioner act, so both assemble the
# same answers, run the same derivations and carry the same preparation-copy
# watermark - a case can never end up with two differently-filled copies of one
# perjury form. flag_forms owns the edition selector and the honest caveat that
# neither printed expiry is a claim about DOL's current form.
PWD_FORM_KEYS = ("eta-9141", "eta-9141-2021")
FORM_KEYS = ("i-129", "eta-9035", *PWD_FORM_KEYS)

# Which pipeline step each form's answers are assembled for. The step row must
# exist in the case's plan (every H1B plan carries lca + i129). The ETA-9141
# (prevailing wage request) precedes the LCA and states the same job/worksite
# facts, so it is assembled for the lca step.
STEP_BY_FORM = {"i-129": "i129", "eta-9035": "lca",
                **{key: "lca" for key in PWD_FORM_KEYS}}

PREPARED_DOC_TYPE = "prepared_form"

# The ETA-9035 PDF is a review artifact only - the real LCA is filed
# electronically in FLAG. ASCII hyphen (the minimal PDF writer is latin-1).
LCA_WATERMARK = ("PREPARATION COPY - the LCA is filed electronically in FLAG "
                 "(flag.dol.gov). This printout is for review only.")

# The ETA-9141 prevailing wage request is likewise made in FLAG, so its printout
# is a review artifact too.
PWD_WATERMARK = ("PREPARATION COPY - the prevailing wage request (ETA-9141) is "
                 "made electronically in FLAG (flag.dol.gov). This printout is "
                 "for review only.")

# form_key -> the preparation-copy watermark stamped on page 1. A form absent
# here (the I-129, which really is mailed on paper) is left unstamped. Every
# edition of the PWD request carries the same stamp: the edition changes which
# blank is printed, never who files it.
PREPARATION_WATERMARKS = {"eta-9035": LCA_WATERMARK,
                          **{key: PWD_WATERMARK for key in PWD_FORM_KEYS}}

# ---------------------------------------------------------------------------
# Localized user-facing strings (en + zh-CN + zh-Hant, the sibling contract).
# PDF artifacts themselves stay English: they are US-government-facing and the
# dependency-free PDF writer is latin-1 only.
# ---------------------------------------------------------------------------
STRINGS = {
    "forms.verify_address": {
        "en": ("VERIFY at uscis.gov/i-129-addresses before mailing - USCIS "
               "lockbox addresses change annually (usually February)."),
        "zh-CN": ("邮寄前请务必在 uscis.gov/i-129-addresses 核实收件地址 - "
                  "USCIS 收件地址每年更新（通常在二月）。"),
        "zh-Hant": ("郵寄前請務必在 uscis.gov/i-129-addresses 核實收件地址 - "
                    "USCIS 收件地址每年更新（通常在二月）。"),
    },
    "forms.wet_ink_intro": {
        "en": ("Wet-ink signatures required before mailing - Ellis never signs "
               "for anyone. Sign each line below by hand:"),
        "zh-CN": ("邮寄前必须亲笔签名 - Ellis 绝不代任何人签名。请亲笔签署以下每一处："),
        "zh-Hant": ("郵寄前必須親筆簽名 - Ellis 絕不代任何人簽名。請親筆簽署以下每一處："),
    },
    "forms.dependents_paper": {
        "en": ("H-4 dependents (spouse/children, I-539 and any I-765) cannot "
               "ride an online filing - their applications must be filed on "
               "PAPER together with this petition packet."),
        "zh-CN": ("H-4 家属（配偶/子女，I-539 及任何 I-765）无法随在线申请提交 - "
                  "其申请必须与本申请材料包一起以纸质方式邮寄。"),
        "zh-Hant": ("H-4 家屬（配偶/子女，I-539 及任何 I-765）無法隨線上申請提交 - "
                    "其申請必須與本申請材料包一起以紙質方式郵寄。"),
    },
    "forms.nothing_submitted": {
        "en": ("Nothing has been filed. This packet is a preparation - filing "
               "happens only when a person signs and mails it."),
        "zh-CN": ("尚未提交任何申请。本材料包仅为准备件 - 只有当事人亲笔签名并邮寄后才算提交。"),
        "zh-Hant": ("尚未提交任何申請。本材料包僅為準備件 - 只有當事人親筆簽名並郵寄後才算提交。"),
    },
    "forms.lca_preview": {
        "en": ("Preparation copy only - the real LCA is filed electronically "
               "in FLAG (flag.dol.gov)."),
        "zh-CN": "仅为准备预览件 - 正式 LCA 须在 FLAG（flag.dol.gov）在线提交。",
        "zh-Hant": "僅為準備預覽件 - 正式 LCA 須在 FLAG（flag.dol.gov）線上提交。",
    },
}


def tr(key: str, locale: str = "en") -> str:
    entry = STRINGS.get(key) or {}
    return entry.get(locale) or entry.get("en") or key


# ---------------------------------------------------------------------------
# The Dallas lockbox (since 2024-04-01 ALL paper I-129 petitions go there; see
# docs/H1B_PAPER_FILING.md). Addresses are re-verified by the human mailing the
# packet - the verify notice rides beside each one, always.
# ---------------------------------------------------------------------------
# Values per the 2026-08-09 research pass (docs/H1B_PAPER_FILING.md sources);
# uscis.gov 403-blocks automated reads, so neither value is portal-confirmed —
# which is exactly why the verify notice is mandatory, not decorative.
USPS_ADDRESS = ("USCIS", "Attn: I-129", "P.O. Box 660867",
                "Dallas, TX 75266")
COURIER_ADDRESS = ("USCIS", "Attn: I-129",
                   "2501 S. State Hwy. 121 Business, Suite 400",
                   "Lewisville, TX 75067")

# Human wording for the I-129's human-only signature/date lines (the wet-ink
# checklist on the paper packet cover). The barcode entry is machine-managed,
# not a signature, so it is absent here.
_SIGNATURE_LINE_LABELS = {
    "form1[0].#subform[6].P5_Line6a_SignatureofApplicant[0]":
        "Part 7 - petitioner signatory's signature (wet ink)",
    "form1[0].#subform[6].Line1b_DateofSignature[0]":
        "Part 7 - date of petitioner's signature",
    "form1[0].#subform[6].Line_Signature[0]":
        "Part 8 - preparer's signature (wet ink)",
    "form1[0].#subform[6].Line_DateofSignature[0]":
        "Part 8 - date of preparer's signature",
    "form1[0].#subform[15].P5_Line6a_SignatureofApplicant[2]":
        "H Classification Supplement - petitioner's signature (wet ink)",
    "form1[0].#subform[15].Sect1_DateSignedByPetitioner[0]":
        "H-1B Data Collection Supplement - date signed by petitioner",
}

# Applicant-worded labels for the H1B map vocabulary the consular FIELD_QUESTIONS
# table does not know. Same style: what Ellis ASKS, never a raw key.
FIELD_WORDING = {
    "employer_fein": "Employer's IRS Federal Employer Identification Number (FEIN)",
    "employer_legal_name": "Employer's full legal business name",
    "employer_naics": "Employer's NAICS code",
    "job_title": "Job title of the offered position",
    "soc_code": "SOC (O*NET/OES) occupation code",
    "soc_title": "SOC occupation title",
    "wage_offer": "Offered wage amount",
    "wage_offer_unit": "Offered wage is per (hour / week / month / year)",
    "employment_start_date": "Employment start date",
    "employment_end_date": "Employment end date",
    "full_time_position": "Is this a full-time position?",
    "worksite_address_line1": "Worksite street address",
    "worksite_address_city": "Worksite city",
    "worksite_address_state": "Worksite state",
    "worksite_address_zip": "Worksite ZIP code",
    "pw_tracking_number": "Prevailing wage determination tracking number",
    "birth_country": "Beneficiary's country of birth",
    "citizenship_country": "Beneficiary's country of citizenship",
    "middle_name": "Beneficiary's middle name (if any)",
    "h1b_field_of_study": "Beneficiary's field of study",
    "h1b_beneficiary_education": "Beneficiary's highest level of education",
}


def label_for_key(key: str) -> str:
    """Applicant-facing wording: the local H1B table first, then the consular
    FIELD_QUESTIONS style (with its humanized fallback)."""
    return FIELD_WORDING.get(key) or consular_forms.label_for(key)


# ---------------------------------------------------------------------------
# Map loading - the raw verified map, NEVER through consular_forms.load_field_map
# (which silently filters the fields dict down to the consular vocabulary).
# ---------------------------------------------------------------------------

def _template_path(form_key: str):
    return consular_forms._template_path(form_key)


def load_form_map(form_key: str) -> dict:
    if form_key not in FORM_KEYS:
        raise ValueError(f"unknown official form {form_key!r}")
    path = _template_path(form_key).with_suffix(".map.json")
    data = json.loads(path.read_text())
    return {
        "fields": dict(data.get("fields") or {}),
        "checkboxes": dict(data.get("checkboxes") or {}),
        "checkbox_states": dict(data.get("checkbox_states") or {}),
        "radio_groups": dict(data.get("radio_groups") or {}),
        # i-129 records "_human_only", eta-9035 "human_only" - honor both.
        "human_only": list(data.get("_human_only") or data.get("human_only") or []),
        "date_format": str(data.get("_date_format") or "MM/DD/YYYY"),
    }


def _is_date_key(key: str) -> bool:
    return key.endswith("_date") or key.endswith("_expiry")


def _text_value(key: str, raw, date_format: str) -> str:
    """A text field's written value. Dates go through the one canonical
    formatter (app/dates.to_portal) and FAIL CLOSED: a non-ISO stored date is
    never written half-translated onto a federal form."""
    if raw is None:
        return ""
    if isinstance(raw, bool):
        return "Yes" if raw else "No"
    value = str(raw).strip()
    if not value:
        return ""
    if _is_date_key(key):
        return dates.to_portal(value, date_format) if dates.is_iso(value) else ""
    return value


def _choice_value(raw):
    """The single chosen option for a tick group. Booleans become yes/no;
    a list is refused outright - every mapped group on these forms is
    select-only-ONE, and two ticks would be a false statement."""
    if isinstance(raw, bool):
        return "yes" if raw else "no"
    if raw in (None, "", [], {}) or isinstance(raw, (list, tuple, set, dict)):
        return None
    return str(raw).strip() or None


def build_fill(form_key: str, answers: dict) -> dict:
    """The deterministic write plan for one form - computed WITHOUT touching
    the PDF so it is unit-testable and auditable.

    Returns {text_values, tick_values, filled_keys, missing, total_mapped,
    human_only}. tick_values carry each checkbox's EXACT recorded on-state from
    checkbox_states (never a hardcoded '/On') - including the ETA-9035's three
    prevailing-wage TEXT fields whose on-state is the literal string 'X'.
    Exactly one tick per select-only-one group is structural: one chosen option
    resolves to one field; everything else in the group stays untouched (/Off).
    """
    m = load_form_map(form_key)
    answers = answers or {}
    human = set(m["human_only"])
    text_values: dict[str, str] = {}
    tick_values: dict[str, str] = {}
    filled: set[str] = set()
    mapped: set[str] = set()

    for pdf_field, ellis_key in m["fields"].items():
        mapped.add(ellis_key)
        if pdf_field in human:
            continue          # a personal act is never Ellis's to perform
        written = _text_value(ellis_key, answers.get(ellis_key), m["date_format"])
        if written:
            text_values[pdf_field] = written
            filled.add(ellis_key)

    for ellis_key, options in m["checkboxes"].items():
        mapped.add(ellis_key)
        choice = _choice_value(answers.get(ellis_key))
        if choice is None:
            continue
        pdf_field = options.get(choice) or options.get(choice.lower())
        if not pdf_field or pdf_field in human:
            continue          # unmatched answer: unfilled beats wrong
        state = m["checkbox_states"].get(pdf_field)
        if not state:
            continue          # no recorded on-state -> never guess '/On'
        tick_values[pdf_field] = state
        filled.add(ellis_key)

    for ellis_key, group in m["radio_groups"].items():
        mapped.add(ellis_key)
        choice = _choice_value(answers.get(ellis_key))
        pdf_field = group.get("field")
        state = (group.get("states") or {}).get(choice) if choice else None
        if not pdf_field or not state or pdf_field in human:
            continue
        tick_values[pdf_field] = state
        filled.add(ellis_key)

    missing = sorted(mapped - filled)
    return {"text_values": text_values, "tick_values": tick_values,
            "filled_keys": sorted(filled), "total_mapped": len(mapped),
            "missing": [{"key": k, "label": label_for_key(k)} for k in missing],
            "human_only": list(m["human_only"])}


def fill_form(form_key: str, answers: dict) -> dict:
    """Fill the government's own blank and return {pdf, filled_count,
    total_mapped, missing, human_only}. I-129: the /XFA key is stripped after
    filling (the hybrid static-XFA form renders blank in Acrobat otherwise) and
    NeedAppearances is set so every value and tick is visible. ETA-9035 and
    ETA-9141: the first page carries the preparation-copy watermark (both are
    really filed in FLAG); the 9035's Section J signature/date and Section L are
    human-only / unmapped and stay empty, and the 9141's DOL-use Section F
    carries no field at all."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import NameObject

    plan = build_fill(form_key, answers)
    reader = PdfReader(str(_template_path(form_key)))
    writer = PdfWriter()
    writer.append(reader)
    # Ticks and values must be visible in every viewer AND on paper.
    writer.set_need_appearances_writer(True)
    combined = {**plan["text_values"], **plan["tick_values"]}
    if combined:
        writer.update_page_form_field_values(None, combined)

    acroform = writer._root_object.get("/AcroForm")
    if acroform is not None:
        acroform = acroform.get_object()   # may arrive as an IndirectObject
        if "/XFA" in acroform:
            # Hybrid static-XFA (I-129): with the XFA stream still present,
            # Acrobat renders the untouched XFA layer and every AcroForm value
            # disappears.
            del acroform[NameObject("/XFA")]

    watermark = PREPARATION_WATERMARKS.get(form_key)
    if watermark:
        overlay = PdfReader(BytesIO(pdfgen.text_pdf([watermark], title="")))
        writer.pages[0].merge_page(overlay.pages[0])

    buf = BytesIO()
    writer.write(buf)
    return {"pdf": buf.getvalue(), "filled_count": len(plan["filled_keys"]),
            "total_mapped": plan["total_mapped"], "missing": plan["missing"],
            "human_only": plan["human_only"]}


# ---------------------------------------------------------------------------
# Answers assembly. filing.answers_for_step is the canonical cross-party-safe
# builder; its whitelists are IMPORTED, never duplicated.
# ---------------------------------------------------------------------------

# I-129 Part 3/4 + supplement facts ABOUT the beneficiary that the petition
# itself asks for, beyond filing.BENEFICIARY_SHARED_KEYS. Additive vocabulary
# only - the imported whitelist stays authoritative for everything it names.
I129_BENEFICIARY_KEYS = frozenset({
    "i94_number", "h1b_last_arrival_date", "h1b_current_status",
    "current_status_expiry", "h1b_consulate_city",
    "h1b_consulate_state_or_country", "h1b_field_of_study",
    "h1b_beneficiary_education", "h1b_valid_passport", "h1b_notify_office_type",
})
I129_BENEFICIARY_PREFIXES = ("h1b_beneficiary_",)


def _step_row(db, parent: models.VisaApplication, step_key: str):
    return db.execute(select(h1b_models.H1bCaseStep).where(
        h1b_models.H1bCaseStep.application_id == parent.id,
        h1b_models.H1bCaseStep.step_key == step_key)).scalars().first()


def answers_for_form(db, parent: models.VisaApplication, form_key: str) -> dict:
    """The fill dict for one form. Base: filing.answers_for_step for the form's
    step (petitioner shared facts + EmployerProfile mapping + the whitelisted
    beneficiary identity for i129). On top: the ACTING petitioner's own party
    answers (their own form - no wall crossing), a handful of EmployerProfile
    columns the I-129 itself prints, and - for the I-129 only - parent-case
    beneficiary facts gated by the imported beneficiary whitelist plus the
    Part 3/4 vocabulary above. The ETA-9035 gets no beneficiary facts at all."""
    step_key = STEP_BY_FORM[form_key]
    step = _step_row(db, parent, step_key)
    if step is None:
        raise LookupError(f"this case's plan has no '{step_key}' step")
    out = h1b_filing.answers_for_step(db, parent, step)

    petitioner = h1b_filing._petitioner_party(db, parent.id)
    for key, value in ((petitioner.answers or {}) if petitioner else {}).items():
        if value in (None, ""):
            continue
        out.setdefault(key, value)

    profile = None
    if petitioner is not None and petitioner.employer_profile_id:
        profile = db.get(h1b_models.EmployerProfile, petitioner.employer_profile_id)
    if profile is not None:
        # Columns the I-129 prints that filing._EMPLOYER_PROFILE_KEYS does not
        # carry. Zero/empty defaults mean "never answered" and stay absent -
        # a statement on a federal form is never defaulted.
        if profile.year_established:
            out.setdefault("h1b_year_established", str(profile.year_established))
        if profile.total_employees:
            out.setdefault("h1b_us_employee_count", str(profile.total_employees))
        if profile.gross_annual_income_cents:
            out.setdefault("h1b_gross_annual_income",
                           str(profile.gross_annual_income_cents // 100))
        if profile.net_annual_income_cents:
            out.setdefault("h1b_net_annual_income",
                           str(profile.net_annual_income_cents // 100))

    if form_key == "i-129":
        src = parent.answers or {}
        for key, value in src.items():
            if value in (None, ""):
                continue
            if (key in h1b_filing.BENEFICIARY_SHARED_KEYS
                    or key.startswith(h1b_filing.BENEFICIARY_SHARED_PREFIXES)
                    or key in I129_BENEFICIARY_KEYS
                    or key.startswith(I129_BENEFICIARY_PREFIXES)):
                out.setdefault(key, value)
        # The passport pipeline stores expiry under expiry_date /
        # passport_expiry_date; the map asks for passport_expiry. One reader,
        # same as filing.answers_for_step.
        if not out.get("passport_expiry"):
            alias = src.get("expiry_date") or src.get("passport_expiry_date")
            if alias not in (None, ""):
                out["passport_expiry"] = alias
    elif form_key in PWD_FORM_KEYS:
        # The PWD request's own derivations (worksite key aliases, the case's
        # classification symbol, the DOL SOC title) live with the form that owns
        # them. Lazy import: flag_forms imports this module. Both entry points -
        # this generic one and flag_forms.prepare_pwd_request - and BOTH
        # editions of the blank therefore fill from the SAME dict; they can
        # never drift apart.
        from . import flag_forms
        out = flag_forms.derive_pwd_answers(parent, out)["answers"]
    return out


# ---------------------------------------------------------------------------
# Storage + signed download URL (the main.py document-preview pattern: blob in
# the DB, short-lived signed URL, bytes served only by the signature-checked
# content endpoint).
# ---------------------------------------------------------------------------

def store_prepared_pdf(db, parent: models.VisaApplication, *, name: str,
                       pdf: bytes, detail: dict) -> models.StoredDocument:
    sha = hashlib.sha256(pdf).hexdigest()
    doc = models.StoredDocument(
        org_id=parent.org_id, application_id=parent.id, name=name,
        mime="application/pdf", size_bytes=len(pdf), sha256=sha,
        storage_ref=f"local://{sha[:16]}", doc_type=PREPARED_DOC_TYPE,
        ocr_status="done", execution_class="LOCAL_PROVIDER",
        # stored_documents has no party column in this build; the petitioner
        # scoping is recorded with the artifact itself.
        extracted_fields={"party": "petitioner", **(detail or {})})
    db.add(doc)
    db.flush()
    db.add(models.DocumentBlob(document_id=doc.id, org_id=parent.org_id,
                               mime="application/pdf", content=pdf))
    db.commit()
    return doc


def mint_download_url(document_id: str) -> dict:
    # Lazy import: main.py imports the h1b routers at module load (the same
    # seam steps.py uses for _adapter_verified_result).
    from ..main import _DOC_URL_TTL_SECONDS, _doc_sig
    exp = int(time.time()) + _DOC_URL_TTL_SECONDS
    return {"download_url":
                f"/documents/{document_id}/content?exp={exp}&sig={_doc_sig(document_id, exp)}",
            "expires_in": _DOC_URL_TTL_SECONDS}


def prepare_form(db, parent: models.VisaApplication, form_key: str, *,
                 actor: str = "") -> dict:
    """Fill + store one official form; returns the payload minus the
    endpoint-added disclaimer/localization."""
    answers = answers_for_form(db, parent, form_key)
    result = fill_form(form_key, answers)
    doc = store_prepared_pdf(
        db, parent, name=f"{form_key}-prepared.pdf", pdf=result["pdf"],
        detail={"form_key": form_key, "filled_count": result["filled_count"],
                "total_mapped": result["total_mapped"]})
    audit.record(db, org_id=parent.org_id, application_id=parent.id,
                 action="h1b_form_prepared",
                 detail={"form_key": form_key, "document_id": doc.id,
                         "filled_count": result["filled_count"],
                         "total_mapped": result["total_mapped"],
                         "missing_count": len(result["missing"])},
                 actor=actor)
    return {"form_key": form_key, "document_id": doc.id,
            "filled_count": result["filled_count"],
            "total_mapped": result["total_mapped"],
            "missing": result["missing"], "human_only": result["human_only"],
            **mint_download_url(doc.id)}


# ---------------------------------------------------------------------------
# Paper packet (docs/H1B_PAPER_FILING.md): cover sheet + filled I-129 +
# exhibit list, merged into one mail-ready PDF.
# ---------------------------------------------------------------------------

def wet_ink_lines(human_only: list[str]) -> list[str]:
    """Every human-only SIGNATURE line, in human words. Barcode/system entries
    are excluded - they are machine-managed, not signatures."""
    out = []
    for field in human_only:
        label = _SIGNATURE_LINE_LABELS.get(field)
        if label:
            out.append(label)
        elif "PDF417" not in field:
            out.append(f"Signature/date line: {field}")
    return out


def _accepted_documents(db, parent: models.VisaApplication) -> list[dict]:
    """The exhibit list: documents the parties explicitly SUBMITTED against the
    case checklist (an upload alone never fulfils a requirement). Prepared-form
    artifacts are Ellis output, not exhibits."""
    subs = db.execute(select(models.ChecklistSubmission).where(
        models.ChecklistSubmission.application_id == parent.id,
        models.ChecklistSubmission.status == "submitted")).scalars().all()
    out = []
    seen = set()
    for sub in sorted(subs, key=lambda s: s.item_id):
        doc = db.get(models.StoredDocument, sub.document_id)
        if doc is None or doc.doc_type == PREPARED_DOC_TYPE:
            continue
        if doc.id in seen:
            continue
        seen.add(doc.id)
        out.append({"item_id": sub.item_id, "name": doc.name,
                    "doc_type": doc.doc_type})
    return out


def _party_names(db, parent: models.VisaApplication) -> dict:
    rows = db.execute(select(h1b_models.CaseParty).where(
        h1b_models.CaseParty.application_id == parent.id)).scalars().all()
    return {p.role: (p.display_name or "") for p in rows}


def _address_block(title: str, address: tuple) -> list[str]:
    lines = [title]
    lines += [f"    {ln}" for ln in address]
    lines.append(f"    {tr('forms.verify_address', 'en')}")
    return lines


def build_paper_packet(db, parent: models.VisaApplication, *,
                       actor: str = "") -> dict:
    """Assemble the mail packet: pdfgen cover sheet (case summary, wet-ink
    signature checklist, Dallas lockbox USPS + courier addresses each carrying
    the verify notice, dependents-must-be-paper note), the filled I-129, and
    the exhibit list of accepted case documents - one merged PDF, stored and
    served exactly like every prepared form."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import NameObject

    answers = answers_for_form(db, parent, "i-129")
    filled = fill_form("i-129", answers)
    names = _party_names(db, parent)
    exhibits = _accepted_documents(db, parent)
    wet_ink = wet_ink_lines(filled["human_only"])
    today = _dt.date.today().isoformat()
    case_kind = (parent.answers or {}).get("h1b_case_kind", "")

    cover = ["H-1B PAPER FILING PACKET - COVER SHEET", ""]
    cover.append(f"Case: {parent.id}   Kind: {case_kind or 'h1b'}   "
                 f"Generated: {today}")
    cover.append(f"Beneficiary: {names.get('beneficiary', '')}")
    cover.append(f"Petitioner: {names.get('petitioner', '')}")
    cover.append("")
    cover += consular_forms._wrap(tr("forms.nothing_submitted", "en"), 92)
    cover.append("")
    cover.append("WET-INK SIGNATURES REQUIRED BEFORE MAILING:")
    cover += [f"  [ ] {line}" for line in wet_ink]
    cover.append("")
    cover.append("MAIL TO - USCIS Dallas Lockbox (all paper I-129 filings "
                 "since 2024-04-01):")
    cover += _address_block("  U.S. Postal Service:", USPS_ADDRESS)
    cover += _address_block("  FedEx / UPS / DHL (courier):", COURIER_ADDRESS)
    cover.append("")
    cover += consular_forms._wrap("DEPENDENTS: "
                                  + tr("forms.dependents_paper", "en"), 92)
    cover.append("")
    cover += consular_forms._wrap("DISCLAIMER: " + _disclaimer_en(), 92)
    cover_pdf = pdfgen.text_pdf(cover, title="H-1B Paper Filing Packet")

    exhibit_lines = ["EXHIBIT LIST - documents accepted on this case", ""]
    if exhibits:
        for i, ex in enumerate(exhibits, start=1):
            exhibit_lines.append(
                f"  {i}. {ex['name']}  ({ex['doc_type']}; checklist item "
                f"{ex['item_id']})")
    else:
        exhibit_lines.append("  (no documents have been accepted on this case "
                             "yet)")
    exhibit_lines += ["", "Attach each exhibit behind the signed I-129, in "
                          "this order."]
    exhibit_pdf = pdfgen.text_pdf(exhibit_lines, title="Exhibit List")

    writer = PdfWriter()
    writer.append(PdfReader(BytesIO(cover_pdf)))
    writer.append(PdfReader(BytesIO(filled["pdf"])))
    writer.append(PdfReader(BytesIO(exhibit_pdf)))
    writer.set_need_appearances_writer(True)
    acroform = writer._root_object.get("/AcroForm")
    if acroform is not None:                          # defense in depth
        acroform = acroform.get_object()
        if "/XFA" in acroform:
            del acroform[NameObject("/XFA")]
    buf = BytesIO()
    writer.write(buf)
    packet = buf.getvalue()

    doc = store_prepared_pdf(
        db, parent, name="i-129-paper-packet.pdf", pdf=packet,
        detail={"kind": "paper_packet", "exhibit_count": len(exhibits),
                "filled_count": filled["filled_count"],
                "total_mapped": filled["total_mapped"]})
    audit.record(db, org_id=parent.org_id, application_id=parent.id,
                 action="h1b_paper_packet_built",
                 detail={"document_id": doc.id, "exhibits": len(exhibits),
                         "missing_count": len(filled["missing"])},
                 actor=actor)
    return {"document_id": doc.id, "form_key": "i-129",
            "filled_count": filled["filled_count"],
            "total_mapped": filled["total_mapped"],
            "missing": filled["missing"], "human_only": filled["human_only"],
            "wet_ink_lines": wet_ink, "exhibits": exhibits,
            "usps_address": list(USPS_ADDRESS),
            "courier_address": list(COURIER_ADDRESS),
            **mint_download_url(doc.id)}


def _disclaimer_en() -> str:
    from .disclaimer import ATTORNEY_DISCLAIMER
    return ATTORNEY_DISCLAIMER["en"]
