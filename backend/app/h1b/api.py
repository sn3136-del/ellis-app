"""H1B case creation + pipeline endpoints.

H1B never enters through the tourist intake (routekey purpose walls are
deliberate); this is the renewal-style dedicated registration point. The
attorney disclaimer is part of every creation and pipeline payload.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from .. import models, audit
from ..db import get_session
from ..security import Principal, get_principal, require_owner
from ..visa_snapshot.models import CaseRouteGuidance
from . import models as h1b_models
from . import steps as h1b_steps
from .checklist import derive_h1b_checklist
from .disclaimer import DISCLAIMER_VERSION, disclaimer
from .guidance import CASE_KINDS, CONTINUATION_KIND, build_guidance, step_plan

router = APIRouter(prefix="/h1b", tags=["h1b"])


class EmployerProfileBody(BaseModel):
    legal_name: str
    trade_name: str = ""
    fein: str = ""
    naics_code: str = ""
    address_line1: str = ""
    address_line2: str = ""
    city: str = ""
    state: str = ""
    postal_code: str = ""
    phone: str = ""
    year_established: int = 0
    total_employees: int = 0
    signatory_name: str = ""
    signatory_title: str = ""
    signatory_email: str = ""
    signatory_phone: str = ""
    parent_company_name: str = ""
    parent_company_country: str = ""


class CreateH1bCaseBody(BaseModel):
    case_kind: str
    beneficiary_full_name: str
    beneficiary_email: str
    beneficiary_abroad: bool = True
    beneficiary_in_us: bool = False
    first_h1b: bool = True
    employer_profile_id: str = ""
    petitioner_email: str = ""
    petitioner_name: str = ""
    locale: str = "en"


@router.post("/employer-profiles")
def create_employer_profile(body: EmployerProfileBody,
                            principal: Principal = Depends(get_principal),
                            db=Depends(get_session)):
    fein = "".join(ch for ch in body.fein if ch.isdigit())
    if body.fein and len(fein) != 9:
        raise HTTPException(422, "FEIN must be 9 digits")
    row = h1b_models.EmployerProfile(
        org_id=principal.org_id, created_by=principal.user_id, fein=fein,
        **body.model_dump(exclude={"fein"}))
    db.add(row)
    db.commit()
    audit.record(db, org_id=principal.org_id, application_id="",
                 action="employer_profile_created",
                 detail={"employer_profile_id": row.id,
                         "legal_name": body.legal_name},
                 actor=principal.user_id)
    return {"employer_profile_id": row.id}


@router.get("/employer-profiles")
def list_employer_profiles(principal: Principal = Depends(get_principal),
                           db=Depends(get_session)):
    rows = db.execute(select(h1b_models.EmployerProfile).where(
        h1b_models.EmployerProfile.org_id == principal.org_id)).scalars().all()
    return {"profiles": [{"id": r.id, "legal_name": r.legal_name,
                          "fein_last4": r.fein[-4:] if r.fein else "",
                          "signatory_name": r.signatory_name}
                         for r in rows]}


@router.post("/cases")
def create_h1b_case(body: CreateH1bCaseBody,
                    principal: Principal = Depends(get_principal),
                    db=Depends(get_session)):
    if body.case_kind not in CASE_KINDS:
        raise HTTPException(422, f"case_kind must be one of {CASE_KINDS}")

    employer = None
    if body.employer_profile_id:
        employer = db.get(h1b_models.EmployerProfile, body.employer_profile_id)
        if employer is None:
            raise HTTPException(404, "employer profile not found")
        require_owner(principal, employer.org_id)

    applicant = models.Applicant(
        org_id=principal.org_id, user_id=principal.user_id,
        full_name=body.beneficiary_full_name, email=body.beneficiary_email)
    db.add(applicant)
    db.flush()

    parent = models.VisaApplication(
        org_id=principal.org_id, user_id=principal.user_id,
        applicant_id=applicant.id, destination_country="United States",
        visa_type="h1b",
        answers={"h1b_case_kind": body.case_kind,
                 "beneficiary_abroad": body.beneficiary_abroad,
                 "email": body.beneficiary_email,
                 "full_name": body.beneficiary_full_name})
    db.add(parent)
    db.flush()

    db.add(h1b_models.CaseParty(
        org_id=principal.org_id, application_id=parent.id, role="beneficiary",
        user_id=principal.user_id, display_name=body.beneficiary_full_name,
        email=body.beneficiary_email))
    db.add(h1b_models.CaseParty(
        org_id=principal.org_id, application_id=parent.id, role="petitioner",
        party_kind="organization",
        display_name=(employer.legal_name if employer else body.petitioner_name),
        email=(employer.signatory_email if employer else body.petitioner_email),
        employer_profile_id=(employer.id if employer else "")))

    guidance = build_guidance(body.case_kind,
                              beneficiary_abroad=body.beneficiary_abroad)
    checklist = derive_h1b_checklist(
        case_kind=body.case_kind, beneficiary_abroad=body.beneficiary_abroad,
        beneficiary_in_us=body.beneficiary_in_us, first_h1b=body.first_h1b)
    db.add(CaseRouteGuidance(
        org_id=principal.org_id, case_id=parent.id, intake_id=None,
        route_key=f"h1b:{body.case_kind}", disposition="VISA_REQUIRED",
        continuation_kind=CONTINUATION_KIND,
        guidance={"status": "curated", "guidance": guidance},
        checklist=checklist))

    plan = step_plan(body.case_kind, beneficiary_abroad=body.beneficiary_abroad)
    for s in plan:
        db.add(h1b_models.H1bCaseStep(
            org_id=principal.org_id, application_id=parent.id,
            step_key=s["step_key"], acting_party=s["acting_party"],
            depends_on=s["depends_on"],
            status="ready" if not s["depends_on"] else "blocked"))
    db.commit()

    audit.record(db, org_id=principal.org_id, application_id=parent.id,
                 action="h1b_case_created",
                 detail={"case_kind": body.case_kind,
                         "beneficiary_abroad": body.beneficiary_abroad,
                         "steps": [s["step_key"] for s in plan],
                         "disclaimer_version": DISCLAIMER_VERSION},
                 actor=principal.user_id)

    return {"case_id": parent.id, "case_kind": body.case_kind,
            "steps": h1b_steps.recompute_readiness(db, parent.id),
            "checklist": checklist,
            "attorney_disclaimer": disclaimer(body.locale),
            "disclaimer_version": DISCLAIMER_VERSION}


@router.get("/cases/{case_id}/pipeline")
def get_pipeline(case_id: str, locale: str = "en",
                 principal: Principal = Depends(get_principal),
                 db=Depends(get_session)):
    parent = db.get(models.VisaApplication, case_id)
    if parent is None or parent.visa_type != "h1b":
        raise HTTPException(404, "h1b case not found")
    require_owner(principal, parent.org_id)
    parties = db.execute(select(h1b_models.CaseParty).where(
        h1b_models.CaseParty.application_id == case_id)).scalars().all()
    return {"case_id": case_id,
            "case_kind": (parent.answers or {}).get("h1b_case_kind"),
            "steps": h1b_steps.recompute_readiness(db, case_id),
            "parties": [{"role": p.role, "display_name": p.display_name,
                         "status": p.status} for p in parties],
            "attorney_disclaimer": disclaimer(locale),
            "disclaimer_version": DISCLAIMER_VERSION}
