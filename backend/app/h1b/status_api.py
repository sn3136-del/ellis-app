"""Case-status tracking: the government's own answer about a filed receipt.

Read-only by construction. USCIS publishes a Case Status API and nothing that
files, so this surface can only ever REPORT — there is no submit path here and
myUSCIS is never scraped. The tracking itself lives in the edition-neutral
provider (app/providers/torch_status.py); this router only decides which of a
pipeline's steps carry a USCIS receipt and who may see it.

Party scoping rides api.py's helpers (findings #3/#14/#20). The tracked OUTCOME
is visible to both parties — an adjudication decides the beneficiary's status as
much as the petitioner's filing — but the receipt number itself is a filing
credential, so a caller bound only to the other party sees it masked.

Execution-class honesty: an entry is a real government result only when the
provider's own display guard says so. Unconfigured, unreachable, or
receipt-less, the entry says exactly that and `is_real_government_result` stays
False. Every payload carries the attorney disclaimer: an adjudication status is
eligibility-adjacent output by definition.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .. import models
from ..db import get_session
from ..providers import torch_status
from ..security import Principal, get_principal, require_owner
from . import steps as h1b_steps
from .api import _party_roles
from .disclaimer import DISCLAIMER_VERSION, disclaimer

router = APIRouter(prefix="/h1b", tags=["h1b"])

# The pipeline steps whose outcome is a USCIS receipt. The LCA is the Labor
# Department's and the consular leg is the State Department's; neither is
# adjudicated by USCIS and neither ever carries a USCIS receipt number.
USCIS_STEP_KEYS = ("registration", "i129")


def _load_parent(db, case_id: str, principal: Principal) -> models.VisaApplication:
    parent = db.get(models.VisaApplication, case_id)
    if parent is None or parent.visa_type != "h1b":
        raise HTTPException(404, "h1b case not found")
    require_owner(principal, parent.org_id)
    return parent


def _mask_receipt(receipt: str) -> str:
    # Enough to recognize a filing (office prefix + last four), never enough to
    # quote it. A too-short stored value is masked whole rather than leaked.
    if len(receipt) < 8:
        return "*" * len(receipt)
    return receipt[:3] + "*" * (len(receipt) - 7) + receipt[-4:]


@router.get("/cases/{case_id}/status")
def get_case_status(case_id: str, locale: str = "en",
                    principal: Principal = Depends(get_principal),
                    db=Depends(get_session)):
    """Track every USCIS step of this pipeline against USCIS's own Case Status
    API. A step with no receipt yet says so; a step whose receipt cannot be
    tracked says that too. Nothing here files anything."""
    parent = _load_parent(db, case_id, principal)
    roles = _party_roles(db, case_id, principal)

    def _receipt_visible(acting_party: str) -> bool:
        # Mirrors the pipeline read rule: admins and unbound org callers see
        # everything; a party-bound caller sees its own party's filing facts.
        return ("admin" in roles) or (not roles) or (acting_party in roles)

    entries = []
    for step in h1b_steps.steps_for_case(db, case_id):
        if step.step_key not in USCIS_STEP_KEYS:
            continue
        entry = {"step_key": step.step_key, "acting_party": step.acting_party,
                 "step_status": step.status, "source": torch_status.SOURCE}
        receipt = (step.uscis_receipt_number or "").strip()
        if not receipt:
            entry.update({
                "tracked": False, "receipt_number": None,
                "receipt_masked": False,
                "status": torch_status.NO_RECEIPT_STATUS,
                "message": torch_status.NO_RECEIPT_MESSAGE,
                "status_date": None, "description": "", "live": False,
                "is_real_government_result": False,
                "note": "no USCIS receipt number is recorded for this step yet"})
            entries.append(entry)
            continue
        try:
            result = torch_status.get_case_status(receipt)
        except torch_status.InvalidReceiptNumber as exc:
            # A stored value that is not a receipt number is a data problem, not
            # a status. It is reported as such and never sent to USCIS.
            entry.update({"tracked": False, "status": "invalid_receipt_number",
                          "message": "the recorded receipt number is not a "
                                     "valid USCIS receipt number",
                          "status_date": None, "description": "", "live": False,
                          "is_real_government_result": False, "note": str(exc)})
        else:
            entry.update({k: v for k, v in result.items() if k != "receipt_number"})
            entry["tracked"] = True
            entry["is_real_government_result"] = \
                torch_status.is_real_government_status(result)
        visible = _receipt_visible(step.acting_party)
        entry["receipt_number"] = receipt if visible else _mask_receipt(receipt)
        entry["receipt_masked"] = not visible
        entries.append(entry)

    return {"case_id": case_id,
            "case_kind": (parent.answers or {}).get("h1b_case_kind"),
            "tracking": {"source": torch_status.SOURCE,
                         "configured": torch_status.is_configured(),
                         "read_only": True,
                         "note": "Ellis tracks receipts it has already filed; "
                                 "USCIS publishes no filing API and Ellis "
                                 "never submits through this surface"},
            "steps": entries,
            "attorney_disclaimer": disclaimer(locale),
            "disclaimer_version": DISCLAIMER_VERSION}
