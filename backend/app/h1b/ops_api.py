"""H1B ops endpoints: org-account bulk registration, RFE response assembly,
cap-exemption determination.

Party scoping rides api.py's helpers (the per-party auth doctrine, findings
#3/#14/#20). All three surfaces are PETITIONER-OR-ADMIN:

 - the bulk registration file carries many beneficiaries' passport numbers
   aggregated for one employer, so it is authorized against EVERY case named,
   and a beneficiary never receives it;
 - the RFE response packet is the petitioner's filing and carries their wage,
   worksite and financial evidence;
 - cap exemption turns on the petitioner's own entity facts.

Every payload carries the attorney disclaimer, and every locale-bearing string
is served through the same en/zh-CN/zh-Hant contract h1b/forms.py uses.
"""
from __future__ import annotations

import base64
import hashlib
import time as _time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import audit, models
from ..db import get_session
from ..security import Principal, get_principal, require_owner
from . import bulk_registration, cap_exemption, rfe_response
from .api import _authorize_step_action
from .disclaimer import DISCLAIMER_VERSION, disclaimer

router = APIRouter(prefix="/h1b", tags=["h1b-ops"])

LOCALES = ("en", "zh-CN", "zh-Hant")


def _locale(value: str) -> str:
    return value if value in LOCALES else "en"


def _load_parent(db, case_id: str, principal: Principal) -> models.VisaApplication:
    parent = db.get(models.VisaApplication, case_id)
    if parent is None or parent.visa_type != "h1b":
        raise HTTPException(404, "h1b case not found")
    require_owner(principal, parent.org_id)
    return parent


def _envelope(locale: str) -> dict:
    return {"attorney_disclaimer": disclaimer(locale),
            "disclaimer_version": DISCLAIMER_VERSION}


# ---------------------------------------------------------------------------
# Bulk registration
# ---------------------------------------------------------------------------

class BulkRegistrationBody(BaseModel):
    case_ids: list[str] = []
    # Defaults to the next cap season Ellis computes from today's date; the
    # operator may pin it (an org registering for a specific fiscal year).
    fiscal_year: int | None = None
    # Write rows Ellis could not complete into the file with blank cells.
    # Off by default: USCIS rejects them, and a rejected batch is worse than a
    # short one. Either way every case is reported in `excluded`.
    include_invalid: bool = False


@router.post("/orgs/{org}/bulk-registration")
def build_bulk_registration(org: str, body: BulkRegistrationBody,
                            locale: str = "en",
                            principal: Principal = Depends(get_principal),
                            db=Depends(get_session)):
    """Build the USCIS Organizational Account bulk-registration file.

    Ellis GENERATES the file and hands it back as bytes; a human signs in to
    myUSCIS, uploads it, pays and submits. There is no registration API and
    Ellis would not use one for a submission act.

    The response deliberately does NOT echo the row VALUES: the passport
    numbers and dates of birth exist in exactly one place, the file itself.
    Per-row validation errors, their codes and the columns they belong to are
    returned in full — nothing is hidden, only un-duplicated.
    """
    require_owner(principal, org)
    loc = _locale(locale)
    case_ids = list(body.case_ids or [])
    if not case_ids:
        raise HTTPException(422, "case_ids must name at least one H-1B case")
    # USCIS's own cap, checked before anything expensive. A silently truncated
    # file would drop real beneficiaries out of the lottery.
    if len(case_ids) > bulk_registration.MAX_BENEFICIARIES:
        e = bulk_registration.BulkLimitExceeded(len(case_ids))
        raise HTTPException(422, {"reason": str(e), "requested": e.requested,
                                  "limit": e.limit})

    # Petitioner-or-admin on EVERY case: this one file aggregates many
    # beneficiaries' identity documents for the employer.
    for case_id in case_ids:
        _load_parent(db, case_id, principal)      # 404 / cross-tenant 403
        _authorize_step_action(db, case_id, principal, "petitioner")

    try:
        out = bulk_registration.build_workbook(
            db, org, case_ids, fiscal_year=body.fiscal_year,
            include_invalid=body.include_invalid)
    except bulk_registration.BulkLimitExceeded as e:
        raise HTTPException(422, {"reason": str(e), "requested": e.requested,
                                  "limit": e.limit})

    audit.record(db, org_id=org, application_id="",
                 action="h1b_bulk_registration_built",
                 detail={"fiscal_year": out["fiscal_year"],
                         "requested_count": out["requested_count"],
                         "ready_count": out["ready_count"],
                         "rows_in_file": out["rows_in_file"],
                         "template_version": out["template_version"],
                         # Codes only — never a beneficiary's own values.
                         "error_codes": sorted({e["code"] for errs in
                                                out["errors_by_row"].values()
                                                for e in errs})},
                 actor=principal.user_id)

    rows = [{"row": r["row"], "case_id": r["case_id"], "ready": r["ready"],
             "errors": r["errors"],
             "filled_columns": sorted(k for k, v in r["values"].items() if v)}
            for r in out["rows"]]

    return {
        "org_id": org,
        "fiscal_year": out["fiscal_year"],
        "weighted_selection": out["weighted_selection"],
        "template_version": out["template_version"],
        "template_as_of": out["template_as_of"],
        "template_source": out["template_source"],
        "columns": out["columns"],
        "rows": rows,
        "errors_by_row": {str(k): v for k, v in out["errors_by_row"].items()},
        "ready_count": out["ready_count"],
        "requested_count": out["requested_count"],
        "rows_in_file": out["rows_in_file"],
        "max_beneficiaries": out["max_beneficiaries"],
        "excluded": out["excluded"],
        "selection_entries_total": out["selection_entries_total"],
        "caveats": out["caveats"],
        "upload_instructions": out["upload_instructions"],
        "download": {"filename": out["filename"], "mime": "text/csv",
                     "size_bytes": len(out["csv_bytes"]),
                     "content_base64": base64.b64encode(
                         out["csv_bytes"]).decode("ascii")},
        # Execution-class honesty: the file exists, nothing has been filed.
        "submitted": False,
        "submission": "the file is yours to upload; Ellis never signs in to "
                      "myUSCIS, uploads, pays the registration fee, or submits",
        **_envelope(loc),
    }


# ---------------------------------------------------------------------------
# RFE response assembly
# ---------------------------------------------------------------------------

class RfeAssembleBody(BaseModel):
    # Issue keys, or {"issue_key": ..., "officer_wording": "..."} objects.
    issues: list = []
    # The notice's text, entered or pasted. When `issues` is empty this is
    # classified deterministically against the taxonomy.
    notice_text: str = ""
    # A StoredDocument on THIS case holding the uploaded notice; its OCR text
    # is used the same way as notice_text.
    notice_document_id: str = ""
    # The deadline PRINTED ON THE NOTICE. Never computed if absent.
    response_due_date: str = ""
    receipt_number: str = ""
    # Also render and store the printable packet, returning a signed URL.
    generate_pdf: bool = False


@router.post("/cases/{case_id}/rfe/assemble")
def assemble_rfe_response(case_id: str, body: RfeAssembleBody, locale: str = "en",
                          principal: Principal = Depends(get_principal),
                          db=Depends(get_session)):
    """Assemble the RFE response packet: each ground mapped to the curing
    evidence, the exhibits on file that answer it, the gaps that do not, and a
    DRAFT narrative for attorney review.

    Petitioner-or-admin: the packet is the petitioner's filing. Ellis never
    files it, never signs it, and never touches the premium-processing clock —
    the clock behaviour is explained in the payload and acted on by nobody."""
    parent = _load_parent(db, case_id, principal)
    _authorize_step_action(db, case_id, principal, "petitioner")
    loc = _locale(locale)

    notice_text = body.notice_text or ""
    notice_source = "entered" if notice_text else ""
    if body.notice_document_id:
        doc = db.get(models.StoredDocument, body.notice_document_id)
        if doc is None or doc.application_id != case_id:
            raise HTTPException(404, "notice document not found on this case")
        require_owner(principal, doc.org_id)
        if not (doc.ocr_text or "").strip():
            raise HTTPException(409, {
                "reason": "the uploaded notice has no readable text yet, so "
                          "Ellis cannot classify its grounds; enter them or "
                          "wait for the document to finish processing",
                "document_id": doc.id, "ocr_status": doc.ocr_status})
        notice_text = f"{notice_text}\n{doc.ocr_text}" if notice_text else doc.ocr_text
        notice_source = "uploaded_document"

    packet = rfe_response.assemble(
        db, principal, parent, body.issues, locale=loc,
        notice_text=notice_text, response_due_date=body.response_due_date,
        receipt_number=body.receipt_number)
    packet["notice_source"] = notice_source or "none"

    document = None
    if body.generate_pdf:
        pdf = rfe_response.build_packet_pdf(
            packet, case_kind=str((parent.answers or {}).get("h1b_case_kind") or ""))
        doc = models.StoredDocument(
            org_id=parent.org_id, application_id=parent.id,
            name="H-1B RFE response packet (DRAFT).pdf", mime="application/pdf",
            size_bytes=len(pdf), sha256=hashlib.sha256(pdf).hexdigest(),
            storage_ref="derived://h1b_rfe_response_packet",
            doc_type="h1b_rfe_response_packet", ocr_status="done",
            execution_class="LOCAL_PROVIDER",
            page_classification={"page_type": "rfe_response_packet",
                                 "classifier": "h1b_rfe_response",
                                 "reject": False,
                                 "accepted_as_passport_identity": False,
                                 "reasons": ["Ellis-generated RFE response "
                                             "packet, unsigned and unfiled"]})
        db.add(doc)
        db.flush()
        db.add(models.DocumentBlob(document_id=doc.id, org_id=parent.org_id,
                                   mime="application/pdf", content=pdf))
        db.commit()
        # Lazily imported: main.py imports this router at module load (the same
        # cycle-avoidance counsel_api uses).
        from ..main import _DOC_URL_TTL_SECONDS, _doc_sig
        exp = int(_time.time()) + _DOC_URL_TTL_SECONDS
        document = {
            "document_id": doc.id, "name": doc.name, "mime": "application/pdf",
            "size_bytes": len(pdf),
            "url": f"/documents/{doc.id}/content?exp={exp}&sig={_doc_sig(doc.id, exp)}",
            "expires_in": _DOC_URL_TTL_SECONDS}

    audit.record(db, org_id=parent.org_id, application_id=case_id,
                 action="h1b_rfe_response_assembled",
                 detail={"issues": [i["issue_key"] for i in packet["issues"]],
                         "unknown_issue_keys": packet["unknown_issue_keys"],
                         "notice_source": packet["notice_source"],
                         "narrative_engine": packet["narrative"]["engine"],
                         "missing_facts": packet["narrative"]["missing_facts"],
                         "gap_count": sum(len(i["gaps"]) for i in packet["issues"]),
                         "deadline_known": packet["response_deadline"]["known"],
                         "document_id": (document or {}).get("document_id", "")},
                 actor=principal.user_id)

    return {**packet, "document": document, "filed": False, **_envelope(loc)}


@router.get("/rfe/issues")
def list_rfe_issues(locale: str = "en",
                    principal: Principal = Depends(get_principal)):
    """The RFE ground vocabulary the assembler understands, for the UI's
    picker. Read-only reference data, so org-visible."""
    loc = _locale(locale)
    return {
        "issues": [{"key": i["key"],
                    "title": i["title"].get(loc) or i["title"]["en"],
                    "title_i18n": dict(i["title"]),
                    "taxonomy_section": i["taxonomy_section"],
                    "uscis_wording": i["uscis_wording"],
                    "citations": list(i["citations"]),
                    "curing_evidence": [e["label"] for e in i["curing_evidence"]]}
                   for i in rfe_response.ISSUES],
        "taxonomy_as_of": rfe_response.TAXONOMY_AS_OF,
        "taxonomy_doc": rfe_response.TAXONOMY_DOC,
        **_envelope(loc),
    }


# ---------------------------------------------------------------------------
# Cap exemption
# ---------------------------------------------------------------------------

@router.get("/cases/{case_id}/cap-exemption")
def get_cap_exemption(case_id: str, locale: str = "en",
                      principal: Principal = Depends(get_principal),
                      db=Depends(get_session)):
    """Advisory 8 CFR 214.2(h)(19) / INA 214(g)(5) cap-exemption determination
    from the facts recorded on this case.

    `exempt` is true, false, or the string 'unknown' — and 'unknown' is the
    honest answer whenever an entity fact has not been provided. The wizard
    answers `open_questions` by writing them through the existing party-answers
    endpoint (entity facts to the petitioner, beneficiary facts to the
    beneficiary), then re-reads this endpoint; no determination is persisted,
    because an advisory read is not a filing fact.

    Petitioner-or-admin: the determination is about the petitioner's entity."""
    parent = _load_parent(db, case_id, principal)
    _authorize_step_action(db, case_id, principal, "petitioner")
    loc = _locale(locale)
    out = cap_exemption.evaluate(db, parent, locale=loc)

    audit.record(db, org_id=parent.org_id, application_id=case_id,
                 action="h1b_cap_exemption_evaluated",
                 detail={"exempt": out["exempt"],
                         "matched_paths": out["matched_paths"],
                         "open_question_count": len(out["open_questions"])},
                 actor=principal.user_id)

    return {**out,
            "answer_facts_via": {
                "petitioner": f"POST /h1b/cases/{case_id}/party/petitioner/answers",
                "beneficiary": f"POST /h1b/cases/{case_id}/party/beneficiary/answers"},
            **_envelope(loc)}


@router.get("/cap-exemption/questions")
def cap_exemption_questions(locale: str = "en",
                            principal: Principal = Depends(get_principal)):
    """The full wizard question set with its decision paths, for a UI that
    wants to ask before a case exists. Reference data, org-visible."""
    loc = _locale(locale)
    return {
        "paths": [{"key": p["key"], "kind": p["kind"],
                   "title": p["title"].get(loc) or p["title"]["en"],
                   "title_i18n": dict(p["title"]),
                   "requires": list(p["requires"]),
                   "citations": list(p["citations"]),
                   "evidence_needed": list(p["evidence_needed"])}
                  for p in cap_exemption.PATHS],
        "questions": [{"fact": key,
                       "prompt": cap_exemption.fact_prompt(key, loc),
                       "prompt_i18n": dict(cap_exemption.FACT_PROMPTS[key]),
                       "asked_of": ("petitioner"
                                    if key in cap_exemption.ENTITY_FACT_KEYS
                                    else "beneficiary")}
                      for key in cap_exemption.FACT_KEYS
                      if key in cap_exemption.FACT_PROMPTS],
        "answers_accepted": ["yes", "no", "unknown (leave unanswered)"],
        "as_of": cap_exemption.AS_OF,
        "sources": dict(cap_exemption.SOURCES),
        "advisory": True,
        **_envelope(loc),
    }
