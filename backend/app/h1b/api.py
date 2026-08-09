"""H1B case creation + pipeline endpoints.

H1B never enters through the tourist intake (routekey purpose walls are
deliberate); this is the renewal-style dedicated registration point. The
attorney disclaimer is part of every creation and pipeline payload.
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from .. import models, audit
from ..db import get_session
from ..security import Principal, get_principal, require_owner
from ..visa_snapshot.models import CaseRouteGuidance
from . import filing as h1b_filing
from . import models as h1b_models
from . import steps as h1b_steps
from .checklist import derive_h1b_checklist
from .disclaimer import DISCLAIMER_VERSION, disclaimer
from .guidance import (CASE_KINDS, CONTINUATION_KIND, STEP_FACTS, build_guidance,
                       step_plan)

router = APIRouter(prefix="/h1b", tags=["h1b"])


# ---------------------------------------------------------------------------
# Per-party authorization (findings #3/#14/#20). acting_party was recorded but
# enforced nowhere: any same-org principal could release or verify the OTHER
# party's penalty-of-perjury filings. Org tenancy is the floor; ON TOP of it a
# MUTATING action on a step is permitted only for the step's acting party (the
# principal whose user_id operates that party's CaseParty row) or an admin.
# Reads stay org-visible (shared reads are deferred by the architecture doc),
# but a caller BOUND to one party never receives the other party's payload.
# ---------------------------------------------------------------------------
def _party_roles(db, case_id: str, principal: Principal) -> set[str]:
    """The party roles this principal operates on the case. An admin resolves
    to {'admin'} (full access). A principal bound to no party resolves to the
    empty set — org-visible for reads, but never authorized to act for a party
    it does not operate."""
    if principal.role == "admin":
        return {"admin"}
    if not principal.user_id:
        return set()
    rows = db.execute(select(h1b_models.CaseParty.role).where(
        h1b_models.CaseParty.application_id == case_id,
        h1b_models.CaseParty.user_id == principal.user_id)).scalars().all()
    return set(rows)


def _authorize_step_action(db, case_id: str, principal: Principal,
                           acting_party: str) -> None:
    """Enforce that a mutating step action belongs to the caller's party. The
    beneficiary can never release/verify the petitioner's LCA/registration/I-129
    and vice versa; only that party's own principal (or an admin) may act."""
    roles = _party_roles(db, case_id, principal)
    if "admin" in roles or acting_party in roles:
        return
    raise HTTPException(403, {
        "reason": f"this action is the {acting_party} party's; your account "
                  f"does not act for that party on this case",
        "acting_party": acting_party,
        "your_roles": sorted(roles)})


def _valid_fein(fein: str) -> bool:
    """A FEIN that can lawfully appear on a federal filing is exactly 9 digits."""
    return len("".join(ch for ch in (fein or "") if ch.isdigit())) == 9


# H1B child filing cases assembled for one party carry that party's private
# facts in their answers dict (the petitioner's FEIN/wage in an LCA/I-129 child;
# the beneficiary's passport/identity in a DS-160 child). The generic
# GET /cases/{id} read is org-scoped, so without this a same-org member of the
# OTHER party could read those facts. This closes the read half of the party
# wall for the shared case-read surface. Non-H1B and parent H1B cases are
# untouched (parent answers are beneficiary-shared identity, visible to both).
_CHILD_VISA_TYPES = frozenset(h1b_filing.VISA_TYPE_BY_STEP.values())


def scope_child_case_read(db, app_row, principal) -> dict | None:
    """For an H1B child FILING case, return the answers dict the caller is
    allowed to see: full answers for the child's acting party or an admin,
    a redacted dict (private filing facts removed) for anyone else in the org.
    Returns None when no scoping applies (not an H1B child), so the caller uses
    the row's answers unchanged and non-H1B reads pay only one cheap check."""
    if getattr(app_row, "visa_type", "") not in _CHILD_VISA_TYPES:
        return None
    answers = dict(app_row.answers or {})
    parent_id = answers.get("h1b_parent_case_id")
    if not parent_id:
        return None
    step = db.execute(select(h1b_models.H1bCaseStep).where(
        h1b_models.H1bCaseStep.child_case_id == app_row.id)).scalars().first()
    acting = step.acting_party if step else "petitioner"
    roles = _party_roles(db, parent_id, principal)
    if "admin" in roles or acting in roles:
        return answers
    # A non-acting party sees the filing exists but not its private facts.
    return {k: v for k, v in answers.items() if k in _CROSS_PARTY_VISIBLE_KEYS}


# The only child-answer keys a non-acting party may read: the linkage and the
# beneficiary's own name (shared identity across the case), never employer
# FEIN/wage/worksite or beneficiary passport/contact particulars.
_CROSS_PARTY_VISIBLE_KEYS = frozenset({
    "h1b_parent_case_id", "h1b_case_kind", "full_name"})


# --- Statutory registration window (finding #9) ----------------------------
# The H-1B cap electronic registration is an ANNUAL window (~early-mid March);
# outside it USCIS accepts no registration, so releasing one out of window would
# open a real filing months early. Extensions/amendments/transfers are
# year-round and carry no registration step, so this gate only ever touches
# cap_initial. Dates are curated from guidance.STEP_FACTS["registration"]; the
# FY2028 window is USCIS's announced early-March target and firms up when USCIS
# posts it. Never a silent proceed: a closed window returns an honest 409 naming
# the next window.
_REGISTRATION_WINDOWS = (
    (_dt.date(2027, 3, 1), _dt.date(2027, 3, 22), "FY2028 cap (~March 2027)"),
)


def _today() -> _dt.date:  # seam so timing is deterministic in tests
    return _dt.date.today()


def _registration_window_status(today: _dt.date | None = None) -> dict:
    today = today or _today()
    for opens, closes, label in _REGISTRATION_WINDOWS:
        if opens <= today <= closes:
            return {"open": True, "window": label,
                    "closes": closes.isoformat(),
                    "source": STEP_FACTS["registration"]["source"]}
    upcoming = [w for w in _REGISTRATION_WINDOWS if w[0] > today]
    nxt = min(upcoming, key=lambda w: w[0]) if upcoming else None
    return {"open": False,
            "next_window": (nxt[2] if nxt else "not yet scheduled"),
            "next_window_opens": (nxt[0].isoformat() if nxt else ""),
            "source": STEP_FACTS["registration"]["source"]}


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
        # Finding #15/#21: FEIN validation lived only on profile creation and was
        # skippable (a profile could be created with an empty FEIN). The FEIN
        # becomes a statement on ETA-9035/I-129, so an employer profile may only
        # be BOUND to a filing case when it carries a valid 9-digit FEIN.
        if not _valid_fein(employer.fein):
            raise HTTPException(422, "employer profile has no valid 9-digit FEIN; "
                                     "it cannot be bound to a filing case")

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


@router.post("/cases/{case_id}/steps/{step_key}/release")
def release_step(case_id: str, step_key: str, locale: str = "en",
                 principal: Principal = Depends(get_principal),
                 db=Depends(get_session)):
    """Release a ready step into its real child filing case. Honest 409s:
    a blocked step names its unverified dependencies, never a vague 'later'."""
    parent = db.get(models.VisaApplication, case_id)
    if parent is None or parent.visa_type != "h1b":
        raise HTTPException(404, "h1b case not found")
    require_owner(principal, parent.org_id)
    # Idempotent sweep first so a step whose dependencies verified since the
    # last read releases now instead of 409ing on stale status.
    h1b_steps.recompute_readiness(db, case_id)
    step = db.execute(select(h1b_models.H1bCaseStep).where(
        h1b_models.H1bCaseStep.application_id == case_id,
        h1b_models.H1bCaseStep.step_key == step_key)).scalars().first()
    if step is None:
        raise HTTPException(404, f"step '{step_key}' is not in this case's plan")

    # Per-party authorization (findings #3/#14/#20): releasing a step opens the
    # acting party's real filing case and drives its ceremonies — only that
    # party (or an admin) may do it.
    _authorize_step_action(db, case_id, principal, step.acting_party)

    # Statutory window (finding #9): the cap registration cannot be filed out of
    # its annual window. An already-released step (idempotent re-release) is not
    # re-gated. Extensions/amendments carry no registration step, so untouched.
    if step_key == "registration" and not step.child_case_id:
        win = _registration_window_status()
        if not win["open"]:
            raise HTTPException(409, {
                "reason": "the H-1B cap registration window is not open; a "
                          "registration cannot be filed out of window",
                "next_window": win.get("next_window"),
                "next_window_opens": win.get("next_window_opens"),
                "source": win.get("source")})

    # FEIN-on-filing (finding #15/#21): a petitioner filing carries the employer
    # FEIN onto a federal form. Validate it wherever it feeds a filing, not only
    # at profile creation.
    if step.acting_party == "petitioner" and not step.child_case_id:
        petitioner = db.execute(select(h1b_models.CaseParty).where(
            h1b_models.CaseParty.application_id == case_id,
            h1b_models.CaseParty.role == "petitioner")).scalars().first()
        profile = None
        if petitioner is not None and petitioner.employer_profile_id:
            profile = db.get(h1b_models.EmployerProfile,
                             petitioner.employer_profile_id)
        if profile is not None and not _valid_fein(profile.fein):
            raise HTTPException(409, {
                "reason": "the bound employer profile has no valid 9-digit FEIN; "
                          "the filing cannot be released until it is corrected"})

    if not step.child_case_id and step.status != "ready":
        if step.status == "blocked":
            by_key = {s.step_key: s for s in h1b_steps.steps_for_case(db, case_id)}
            unverified = [k for k in (step.depends_on or [])
                          if by_key.get(k) is None or by_key[k].status != "verified"]
            raise HTTPException(409, {
                "reason": f"step '{step_key}' is blocked: dependencies not yet "
                          f"verified: {', '.join(unverified)}",
                "step_status": step.status,
                "unverified_dependencies": unverified})
        raise HTTPException(409, {
            "reason": f"step '{step_key}' is {step.status}, not ready",
            "step_status": step.status})

    result = h1b_filing.release_step(db, parent=parent, step=step,
                                     principal=principal)
    return {**result, "attorney_disclaimer": disclaimer(locale),
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
    # Finding #3/#20 (reads): a caller bound to one party must not receive the
    # OTHER party's payload (the petitioner party carries the employer contact /
    # FEIN-bearing profile the beneficiary must never read, and vice versa).
    # Admins and unbound org callers see both (shared reads are deferred).
    roles = _party_roles(db, case_id, principal)
    def _visible(role: str) -> bool:
        return ("admin" in roles) or (not roles) or (role in roles)
    return {"case_id": case_id,
            "case_kind": (parent.answers or {}).get("h1b_case_kind"),
            "steps": h1b_steps.recompute_readiness(db, case_id),
            "parties": [{"role": p.role, "display_name": p.display_name,
                         "status": p.status}
                        for p in parties if _visible(p.role)],
            "attorney_disclaimer": disclaimer(locale),
            "disclaimer_version": DISCLAIMER_VERSION}


class VerifyStepBody(BaseModel):
    # Government receipt facts persisted alongside the verified outcome. Never a
    # substitute for evidence — steps.mark_step_verified refuses without it.
    receipts: dict = {}
    # An admin may record an offline government outcome (e.g. an accepted
    # certified-LCA / I-797 PDF) by pointing at the StoredDocument holding it.
    offline_evidence_document_id: str = ""


@router.post("/cases/{case_id}/steps/{step_key}/verify")
def verify_step(case_id: str, step_key: str, body: VerifyStepBody, locale: str = "en",
                principal: Principal = Depends(get_principal),
                db=Depends(get_session)):
    """Record a step's verified government outcome so its successor unblocks
    (findings #4/#12: without this endpoint the pipeline dead-ends after the
    first filing — recompute_readiness was never reached with real evidence and
    mark_step_verified had no caller). The evidence discipline lives in
    steps.mark_step_verified (Agent A, findings #6/#8): it verifies ONLY on the
    child filing's government-host evidence or an admin-accepted offline
    government artifact, and raises EvidenceRequired otherwise — a receipt dict
    can never stand in for it. This endpoint adds per-party authorization and an
    admin gate on the offline-recording path, then delegates."""
    parent = db.get(models.VisaApplication, case_id)
    if parent is None or parent.visa_type != "h1b":
        raise HTTPException(404, "h1b case not found")
    require_owner(principal, parent.org_id)
    step = db.execute(select(h1b_models.H1bCaseStep).where(
        h1b_models.H1bCaseStep.application_id == case_id,
        h1b_models.H1bCaseStep.step_key == step_key)).scalars().first()
    if step is None:
        raise HTTPException(404, f"step '{step_key}' is not in this case's plan")
    _authorize_step_action(db, case_id, principal, step.acting_party)

    if body.offline_evidence_document_id:
        # Recording an offline government outcome is an administrator act — a
        # party can never self-attest a government result without a
        # portal-verified filing (doctrine; the doc must also be admin-accepted,
        # which steps._accepted_offline_evidence enforces).
        if "admin" not in _party_roles(db, case_id, principal):
            raise HTTPException(403, "recording an offline government outcome is "
                                     "an administrator action")
    elif not step.child_case_id:
        # Friendlier than the bare evidence error: nothing has been filed yet.
        raise HTTPException(409, {
            "reason": f"step '{step_key}' has not been released into a filing "
                      f"case; there is no evidence to verify yet",
            "step_status": step.status})

    try:
        h1b_steps.mark_step_verified(
            db, step, receipts=body.receipts, actor=principal.user_id,
            offline_evidence_document_id=body.offline_evidence_document_id)
    except h1b_steps.EvidenceRequired as e:
        # Evidence honesty: no government-verified confirmation and no accepted
        # offline artifact — the step stays where it is, honestly.
        raise HTTPException(409, {"reason": str(e), "step_key": step_key,
                                  "step_status": step.status,
                                  "child_case_id": step.child_case_id or None})

    return {"case_id": case_id, "step_key": step_key, "status": "verified",
            "steps": h1b_steps.recompute_readiness(db, case_id),
            "attorney_disclaimer": disclaimer(locale),
            "disclaimer_version": DISCLAIMER_VERSION}


class PartyAnswersBody(BaseModel):
    answers: dict


# Attestations that become penalty-of-perjury statements are never free-form
# answers: they live on EmployerProfile as nullable-never-defaulted booleans and
# are asked of the petitioner on the filing itself (doctrine; finding #20).
_ATTESTATION_KEYS = ("h1b_dependent_employer", "willful_violator")


@router.post("/cases/{case_id}/party/{role}/answers")
def write_party_answers(case_id: str, role: str, body: PartyAnswersBody,
                        principal: Principal = Depends(get_principal),
                        db=Depends(get_session)):
    """Write a party's own answers (the missing writer for petitioner job/wage/
    worksite facts that the filing partition reads). Authz-scoped to that party:
    only the petitioner may write petitioner facts, only the beneficiary theirs.
    Petitioner facts land on CaseParty.answers (filing.answers_for_step whitelists
    what reaches a filing — non-shared keys stay on the row); beneficiary facts
    stay on the parent case, where the intake/passport machinery reads them."""
    if role not in h1b_models.PARTY_ROLES:
        raise HTTPException(422, f"role must be one of {h1b_models.PARTY_ROLES}")
    parent = db.get(models.VisaApplication, case_id)
    if parent is None or parent.visa_type != "h1b":
        raise HTTPException(404, "h1b case not found")
    require_owner(principal, parent.org_id)
    _authorize_step_action(db, case_id, principal, role)

    incoming = dict(body.answers or {})
    bad = [k for k in incoming if k in _ATTESTATION_KEYS]
    if bad:
        raise HTTPException(422, {
            "reason": "penalty-of-perjury attestations are never free-form "
                      "answers; they are set on the employer profile and asked "
                      "on the filing",
            "rejected_keys": bad})

    if role == "beneficiary":
        merged = dict(parent.answers or {})
        merged.update(incoming)
        parent.answers = merged
        target_id = parent.id
    else:
        party = db.execute(select(h1b_models.CaseParty).where(
            h1b_models.CaseParty.application_id == case_id,
            h1b_models.CaseParty.role == role)).scalars().first()
        if party is None:
            raise HTTPException(404, f"{role} party not found on this case")
        merged = dict(party.answers or {})
        merged.update(incoming)
        party.answers = merged
        target_id = party.id
    db.commit()
    audit.record(db, org_id=parent.org_id, application_id=case_id,
                 action="h1b_party_answers_written",
                 detail={"role": role, "keys": sorted(incoming.keys())},
                 actor=principal.user_id)
    return {"case_id": case_id, "role": role, "written_keys": sorted(incoming.keys()),
            "target": target_id}
