"""H1B forms endpoints: official form fill + download, and the paper packet.

Party wall, enforced server-side: both endpoints are PETITIONER acts (the
I-129 and ETA-9035 are the petitioner's own statements), authorized with the
same per-party check every other h1b endpoint uses - the beneficiary's account
is refused, an admin may act. The attorney disclaimer rides on every payload.

Nothing here signs, pays, submits, or accepts a declaration: the output is a
prepared artifact whose signature lines are explicitly human-only, downloaded
through the same short-lived signed-URL pattern as every case document.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .. import models
from ..db import get_session
from ..security import Principal, get_principal, require_owner
from . import forms as h1b_forms
from .api import _authorize_step_action
from .disclaimer import DISCLAIMER_VERSION, disclaimer

router = APIRouter(prefix="/h1b", tags=["h1b"])


def _petitioner_case(db, case_id: str, principal: Principal):
    """Load the parent H1B case and enforce org tenancy + the petitioner-party
    (or admin) authorization both form endpoints require."""
    parent = db.get(models.VisaApplication, case_id)
    if parent is None or parent.visa_type != "h1b":
        raise HTTPException(404, "h1b case not found")
    require_owner(principal, parent.org_id)
    _authorize_step_action(db, case_id, principal, "petitioner")
    return parent


@router.post("/cases/{case_id}/forms/{form_key}/prepare")
def prepare_official_form(case_id: str, form_key: str, locale: str = "en",
                          principal: Principal = Depends(get_principal),
                          db=Depends(get_session)):
    """Fill the government's own blank (i-129 | eta-9035) from the case's
    party-partitioned answers. Missing answers are reported applicant-worded
    and NEVER block the download - a partly-filled official form is the honest
    artifact; signature lines stay empty for the humans they belong to."""
    if form_key not in h1b_forms.FORM_KEYS:
        raise HTTPException(404, {
            "reason": f"unknown form '{form_key}'",
            "known_forms": list(h1b_forms.FORM_KEYS)})
    parent = _petitioner_case(db, case_id, principal)
    try:
        result = h1b_forms.prepare_form(db, parent, form_key,
                                        actor=principal.user_id)
    except LookupError as e:
        raise HTTPException(409, {"reason": str(e)})
    out = {"form_key": result["form_key"],
           "filled_count": result["filled_count"],
           "total_mapped": result["total_mapped"],
           "missing": result["missing"],
           "human_only": result["human_only"],
           "document_id": result["document_id"],
           "download_url": result["download_url"],
           "expires_in": result["expires_in"],
           "attorney_disclaimer": disclaimer(locale),
           "disclaimer_version": DISCLAIMER_VERSION}
    if form_key == "eta-9035":
        # Execution-class honesty: this PDF is a review artifact; the real LCA
        # exists only in FLAG. The PDF itself is watermarked; the payload says
        # so in the caller's language.
        out["preparation_notice"] = h1b_forms.tr("forms.lca_preview", locale)
    return out


@router.post("/cases/{case_id}/paper-packet")
def build_paper_packet(case_id: str, locale: str = "en",
                       principal: Principal = Depends(get_principal),
                       db=Depends(get_session)):
    """Assemble the mail-ready paper packet per docs/H1B_PAPER_FILING.md:
    cover sheet (wet-ink signature checklist, Dallas lockbox addresses with the
    verify-before-mailing notice, dependents-must-be-paper note) + the filled
    I-129 + the exhibit list of accepted case documents, merged into one PDF."""
    parent = _petitioner_case(db, case_id, principal)
    try:
        result = h1b_forms.build_paper_packet(db, parent,
                                              actor=principal.user_id)
    except LookupError as e:
        raise HTTPException(409, {"reason": str(e)})
    wet_ink_warnings = ([h1b_forms.tr("forms.wet_ink_intro", locale)]
                        + result["wet_ink_lines"])
    return {"document_id": result["document_id"],
            "form_key": result["form_key"],
            "filled_count": result["filled_count"],
            "total_mapped": result["total_mapped"],
            "missing": result["missing"],
            "human_only": result["human_only"],
            "exhibits": result["exhibits"],
            "usps_address": result["usps_address"],
            "courier_address": result["courier_address"],
            "wet_ink_warnings": wet_ink_warnings,
            "verify_address_notice": h1b_forms.tr("forms.verify_address", locale),
            "dependents_paper_notice": h1b_forms.tr("forms.dependents_paper", locale),
            "nothing_submitted_notice": h1b_forms.tr("forms.nothing_submitted", locale),
            "download_url": result["download_url"],
            "expires_in": result["expires_in"],
            "attorney_disclaimer": disclaimer(locale),
            "disclaimer_version": DISCLAIMER_VERSION}
