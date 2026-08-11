"""LCA Public Access File endpoints (20 CFR 655.760) and the posting notice
(20 CFR 655.734).

Party wall, enforced server-side: the public access file is the PETITIONER's
statutory duty and holds petitioner-private wage and worksite facts, so every
endpoint here is authorized with api.py's per-party helpers — the
beneficiary's account is refused, an admin may act. The attorney disclaimer
rides on every payload, and every user-facing string is localized the same way
h1b/forms.py does it.

Nothing here posts, files, signs or submits. Ellis renders the notice the
employer posts, records what the employer says it did, and tracks the two
clocks the regulation sets. It never claims to have witnessed a posting.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import models
from ..db import get_session
from ..security import Principal, get_principal, require_owner
from . import public_access_file as paf
from .api import _authorize_step_action
from .disclaimer import DISCLAIMER_VERSION, disclaimer

router = APIRouter(prefix="/h1b", tags=["h1b-paf"])


def _petitioner_case(db, case_id: str, principal: Principal):
    """Load the parent H1B case and enforce org tenancy + the petitioner-party
    (or admin) authorization every PAF endpoint requires."""
    parent = db.get(models.VisaApplication, case_id)
    if parent is None or parent.visa_type != "h1b":
        raise HTTPException(404, "h1b case not found")
    require_owner(principal, parent.org_id)
    _authorize_step_action(db, case_id, principal, "petitioner")
    return parent


def _envelope(locale: str) -> dict:
    """The framing every PAF payload carries: what the citations are, when
    they were last read, that Ellis records rather than witnesses, and the
    attorney disclaimer."""
    return {
        "citations": {"public_access_file": paf.PAF_CITATION,
                      "notice": paf.NOTICE_CITATION},
        "as_of": paf.AS_OF,
        "sources": list(paf.SOURCES),
        "nothing_filed_notice": paf.tr("paf.nothing_filed", locale),
        "attested_not_verified_notice": paf.tr("paf.attested_not_verified",
                                               locale),
        "attorney_disclaimer": disclaimer(locale),
        "disclaimer_version": DISCLAIMER_VERSION,
    }


class PostingRecordIn(BaseModel):
    """The employer's account of how the 655.734 notice was given."""
    method: str                                   # hard_copy | electronic
    start_date: Optional[str] = None
    # The date the notice CAME DOWN. Optional, and left empty when the employer
    # has not said: Ellis reports the 10-day question as unknown rather than
    # assuming the notice stayed up for the full window.
    end_date: Optional[str] = None
    locations: list[str] = []                     # hard copy: >= 2 conspicuous
    electronic_method: Optional[str] = None       # e-mail | intranet | ...
    electronic_evidence: Optional[str] = None
    individual_direct_email: bool = False
    lca_filed_date: Optional[str] = None


@router.get("/cases/{case_id}/paf/manifest")
def paf_manifest(case_id: str, locale: str = "en",
                 principal: Principal = Depends(get_principal),
                 db=Depends(get_session)):
    """Every item 20 CFR 655.760(a) requires, in order, each with its
    subparagraph citation and an honest status: present, partial (Ellis holds
    the facts but the file lacks the document), missing, not applicable, or
    unknown (a conditional item whose condition nobody has answered — never
    quietly dropped)."""
    parent = _petitioner_case(db, case_id, principal)
    try:
        manifest = paf.assemble_paf(db, parent)
    except LookupError as e:
        raise HTTPException(409, {"reason": str(e)})
    return {**manifest,
            "availability_notice": paf.tr("paf.availability", locale),
            "retention_notice": paf.tr("paf.retention", locale),
            **_envelope(locale)}


@router.post("/cases/{case_id}/paf/notice")
def generate_posting_notice(case_id: str, locale: str = "en",
                            principal: Principal = Depends(get_principal),
                            db=Depends(get_session)):
    """Render the 20 CFR 655.734 notice from case facts and store it behind a
    short-lived signed download URL, exactly like every prepared form.

    A fact Ellis does not hold is reported missing and left blank; the notice
    is then marked not-ready-to-post rather than completed with a guess. The
    download is never blocked — a draft the employer can see the gaps in beats
    a silent refusal."""
    parent = _petitioner_case(db, case_id, principal)
    try:
        result = paf.prepare_posting_notice(db, parent, actor=principal.user_id)
    except LookupError as e:
        raise HTTPException(409, {"reason": str(e)})
    guidance = (paf.tr("paf.ready_to_post", locale) if result["ready_to_post"]
                else paf.tr("paf.not_ready_to_post", locale))
    return {**result,
            "posting_guidance": guidance,
            "artifact_language_notice": paf.tr("paf.english_artifact", locale),
            **_envelope(locale)}


@router.post("/cases/{case_id}/paf/posting")
def record_posting(case_id: str, body: PostingRecordIn, locale: str = "en",
                   principal: Principal = Depends(get_principal),
                   db=Depends(get_session)):
    """Record the hard-copy posting (start/end dates + locations) or the
    electronic-notice evidence, and return every 655.734 test Ellis can apply
    to it. A shortfall is flagged, not hidden; a record Ellis cannot read (an
    unknown method, an impossible date) is refused rather than stored wrong."""
    parent = _petitioner_case(db, case_id, principal)
    try:
        posting = paf.record_posting(
            db, parent, method=body.method,
            start_date=body.start_date or "", end_date=body.end_date or "",
            locations=body.locations or [],
            electronic_method=body.electronic_method or "",
            electronic_evidence=body.electronic_evidence or "",
            individual_direct_email=body.individual_direct_email,
            lca_filed_date=body.lca_filed_date or "",
            actor=principal.user_id)
    except paf.PostingEvidenceError as e:
        raise HTTPException(400, {"reason": str(e),
                                  "allowed_methods": list(paf.POSTING_METHODS)})
    except LookupError as e:
        raise HTTPException(409, {"reason": str(e)})
    return {"posting": posting,
            "compliance": posting["compliance"],
            "availability": paf.availability_deadline(
                posting.get("lca_filed_date") or ""),
            "posting_requirements": paf.posting_requirements(),
            "availability_notice": paf.tr("paf.availability", locale),
            **_envelope(locale)}


@router.get("/cases/{case_id}/paf/package")
def paf_package(case_id: str, locale: str = "en",
                principal: Principal = Depends(get_principal),
                db=Depends(get_session)):
    """Assemble the public access file as one PDF: the manifest cover (items,
    citations, statuses, the two deadlines, the notice compliance read)
    followed by the generated posting notice. Party evidence is indexed rather
    than merged — those are the employer's own files, several of them images.

    This read ASSEMBLES, so it stores a fresh artifact each time; the returned
    document id is the copy the caller just downloaded."""
    parent = _petitioner_case(db, case_id, principal)
    try:
        result = paf.build_paf_package(db, parent, actor=principal.user_id)
    except LookupError as e:
        raise HTTPException(409, {"reason": str(e)})
    return {**result,
            "availability_notice": paf.tr("paf.availability", locale),
            "retention_notice": paf.tr("paf.retention", locale),
            **_envelope(locale)}
