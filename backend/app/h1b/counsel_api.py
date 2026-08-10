"""H1B counsel endpoints: RFE risk read, narrative drafts, evidence index and
printable evidence cover. Implementation lives in this file only — the
aggregation include in main.py never changes.

Party scoping rides api.py's helpers (the per-party auth doctrine, findings
#3/#14/#20): narratives, the evidence index, and the cover are
petitioner-or-admin surfaces (they carry the petitioner's wage/FEIN-adjacent
facts); rfe-risks is visible to BOTH parties, but a beneficiary-bound caller
receives the risk grounds with the petitioner's private wage numbers redacted
from the signal facts. Every payload carries the attorney disclaimer —
RFE anticipation is eligibility-adjacent output by definition.
"""
from __future__ import annotations

import hashlib
import time as _time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import audit, models
from ..db import get_session
from ..security import Principal, get_principal, require_owner
from . import counsel
from .api import _authorize_step_action, _party_roles
from .disclaimer import DISCLAIMER_VERSION, disclaimer

router = APIRouter(prefix="/h1b", tags=["h1b"])


def _load_parent(db, case_id: str, principal: Principal) -> models.VisaApplication:
    parent = db.get(models.VisaApplication, case_id)
    if parent is None or parent.visa_type != "h1b":
        raise HTTPException(404, "h1b case not found")
    require_owner(principal, parent.org_id)
    return parent


def _localized_risks(risks: list[dict], locale: str) -> list[dict]:
    out = []
    for risk in risks:
        entry = dict(risk)
        titles = entry.pop("title", {}) or {}
        entry["title"] = titles.get(locale) or titles.get("en") or entry["ground"]
        entry["title_i18n"] = titles
        entry["severity_label"] = counsel.counsel_string(
            f"severity.{entry['severity']}", locale)
        out.append(entry)
    return out


@router.get("/cases/{case_id}/counsel/rfe-risks")
def get_rfe_risks(case_id: str, locale: str = "en",
                  principal: Principal = Depends(get_principal),
                  db=Depends(get_session)):
    """Deterministic RFE risk read. Visible to both parties: the beneficiary
    has a stake in every ground, but the petitioner's private wage numbers are
    redacted from the signal facts for a beneficiary-bound caller (party wall,
    read half)."""
    parent = _load_parent(db, case_id, principal)
    roles = _party_roles(db, case_id, principal)
    risks = counsel.redact_risks_for_roles(counsel.rfe_risks(db, parent), roles)
    return {"case_id": case_id,
            "as_of": counsel.TAXONOMY_AS_OF,
            "risks": _localized_risks(risks, locale),
            "attorney_disclaimer": disclaimer(locale),
            "disclaimer_version": DISCLAIMER_VERSION}


class NarrativeBody(BaseModel):
    kind: str = "support_letter"


@router.post("/cases/{case_id}/counsel/narrative")
def draft_narrative(case_id: str, body: NarrativeBody, locale: str = "en",
                    principal: Principal = Depends(get_principal),
                    db=Depends(get_session)):
    """Draft a DRAFT-labeled narrative for attorney review. Petitioner-or-admin:
    the letter is the petitioner's statement and carries their wage/role facts,
    so the beneficiary can never generate (or read) it here."""
    parent = _load_parent(db, case_id, principal)
    _authorize_step_action(db, case_id, principal, "petitioner")
    if body.kind not in counsel.NARRATIVE_KINDS:
        raise HTTPException(
            422, f"kind must be one of {counsel.NARRATIVE_KINDS}")
    out = counsel.draft_narrative(db, principal, parent, kind=body.kind)
    audit.record(db, org_id=parent.org_id, application_id=case_id,
                 action="h1b_narrative_drafted",
                 detail={"kind": body.kind, "engine": out["engine"],
                         "missing_facts": out["missing_facts"],
                         "grounded_fact_keys": sorted(out["grounded_facts"])},
                 actor=principal.user_id)
    return {"case_id": case_id, **out,
            "attorney_disclaimer": disclaimer(locale),
            "disclaimer_version": DISCLAIMER_VERSION}


@router.get("/cases/{case_id}/counsel/evidence-index")
def get_evidence_index(case_id: str, locale: str = "en",
                       principal: Principal = Depends(get_principal),
                       db=Depends(get_session)):
    """The ordered exhibit list, missing items included honestly.
    Petitioner-or-admin: the compiled pack is the petitioner's filing."""
    parent = _load_parent(db, case_id, principal)
    _authorize_step_action(db, case_id, principal, "petitioner")
    exhibits = counsel.evidence_index(db, principal, parent)
    for e in exhibits:
        e["status_label"] = counsel.counsel_string(f"status.{e['status']}", locale)
    missing = sum(1 for e in exhibits if e["status"] == "missing")
    return {"case_id": case_id, "exhibits": exhibits,
            "counts": {"total": len(exhibits), "missing": missing,
                       "on_file": len(exhibits) - missing},
            "attorney_disclaimer": disclaimer(locale),
            "disclaimer_version": DISCLAIMER_VERSION}


@router.post("/cases/{case_id}/counsel/evidence-cover")
def create_evidence_cover(case_id: str, locale: str = "en",
                          principal: Principal = Depends(get_principal),
                          db=Depends(get_session)):
    """Generate and store the evidence cover PDF, returning a short-lived
    signed download URL (the document-preview pattern: signature-authorized
    content endpoint, no filesystem paths, no credentials)."""
    parent = _load_parent(db, case_id, principal)
    _authorize_step_action(db, case_id, principal, "petitioner")
    pdf = counsel.build_evidence_cover(db, parent)
    doc = models.StoredDocument(
        org_id=parent.org_id, application_id=parent.id,
        name="H-1B evidence cover.pdf", mime="application/pdf",
        size_bytes=len(pdf), sha256=hashlib.sha256(pdf).hexdigest(),
        storage_ref="derived://h1b_evidence_cover",
        doc_type="h1b_evidence_cover", ocr_status="done",
        # Execution-class honesty: produced by Ellis's deterministic local
        # generator, and the artifact says so.
        execution_class="LOCAL_PROVIDER",
        page_classification={"page_type": "evidence_cover",
                             "classifier": "h1b_counsel",
                             "reject": False,
                             "accepted_as_passport_identity": False,
                             "reasons": ["Ellis-generated evidence cover"]})
    db.add(doc)
    db.flush()
    db.add(models.DocumentBlob(document_id=doc.id, org_id=parent.org_id,
                               mime="application/pdf", content=pdf))
    db.commit()
    audit.record(db, org_id=parent.org_id, application_id=case_id,
                 action="h1b_evidence_cover_generated",
                 detail={"document_id": doc.id, "size_bytes": len(pdf)},
                 actor=principal.user_id)
    # Lazily imported: main.py imports this router at module load (the same
    # cycle-avoidance steps.py uses for _adapter_verified_result).
    from ..main import _DOC_URL_TTL_SECONDS, _doc_sig
    exp = int(_time.time()) + _DOC_URL_TTL_SECONDS
    return {"case_id": case_id, "document_id": doc.id, "name": doc.name,
            "mime": "application/pdf", "size_bytes": len(pdf),
            "url": f"/documents/{doc.id}/content?exp={exp}&sig={_doc_sig(doc.id, exp)}",
            "expires_in": _DOC_URL_TTL_SECONDS,
            "attorney_disclaimer": disclaimer(locale),
            "disclaimer_version": DISCLAIMER_VERSION}
