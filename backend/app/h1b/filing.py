"""Filing coordinator: a 'ready' pipeline step becomes a real, linked child
filing case (renewal.py is the sanctioned precedent).

The flat answers namespace is party-blind, so a child filing NEVER receives a
wholesale merge of both parties. Each step gets an explicitly assembled dict:

 - lca: the whitelisted job/wage/worksite facts from the petitioner party plus
   the employer profile. No beneficiary facts — DOL never sees them.
 - registration / i129: those plus the four beneficiary identity keys from the
   parent case (full_name, birth_date, passport_number, passport_expiry).
 - ds160_consular: the beneficiary's own answers (the parent case's dict) and
   nothing from the petitioner side.

Anything not on a whitelist stays home. The parent's accepted passport is
carried by sha into beneficiary-acting children only, exactly like renewal.py
carries it (same bytes, same OCR provenance, never a second upload). Release
is idempotent: a step that already holds a child_case_id returns it.
"""
from __future__ import annotations

from sqlalchemy import select

from .. import models, audit
from ..security import Principal
from ..visa_snapshot.models import CaseRouteGuidance
from . import models as h1b_models
from .guidance import AS_OF, STEP_FACTS

CONTINUATION_KIND = "h1b_filing"

VISA_TYPE_BY_STEP = {
    "lca": "h1b_lca",
    "registration": "h1b_registration",
    "i129": "h1b_i129",
    "ds160_consular": "h1b_ds160",
}

# Petitioner facts shareable into petitioner-acting filings — the specgen
# petition vocabulary. Explicit keys plus the worksite_ address prefix; any
# other petitioner answer stays on the party row.
PETITIONER_SHARED_KEYS = (
    "job_title", "soc_code", "soc_title", "wage_offer", "wage_offer_unit",
    "prevailing_wage", "pw_tracking_number", "wage_level",
    "employment_start_date", "employment_end_date", "full_time_position",
)
PETITIONER_SHARED_PREFIXES = ("worksite_",)

# Beneficiary identity shared into registration/i129 (the petitioner states
# who the beneficiary is on those filings). Never email, phone, or anything
# beyond identity.
BENEFICIARY_IDENTITY_KEYS = ("full_name", "birth_date", "passport_number",
                             "passport_expiry")

# EmployerProfile column -> answer key (the specgen employer vocabulary).
_EMPLOYER_PROFILE_KEYS = (
    ("legal_name", "employer_legal_name"),
    ("trade_name", "employer_dba"),
    ("fein", "employer_fein"),
    ("naics_code", "employer_naics"),
    ("address_line1", "employer_address_line1"),
    ("address_line2", "employer_address_line2"),
    ("city", "employer_city"),
    ("state", "employer_state"),
    ("postal_code", "employer_postal_code"),
    ("phone", "employer_phone"),
    ("signatory_name", "employer_contact_name"),
    ("signatory_title", "employer_contact_title"),
    ("signatory_email", "employer_contact_email"),
    ("signatory_phone", "employer_contact_phone"),
)


def _petitioner_party(db, application_id: str):
    return db.execute(select(h1b_models.CaseParty).where(
        h1b_models.CaseParty.application_id == application_id,
        h1b_models.CaseParty.role == "petitioner")).scalars().first()


def answers_for_step(db, parent: models.VisaApplication,
                     step: h1b_models.H1bCaseStep) -> dict:
    """The explicitly assembled answers dict for one filing. Whitelist-only —
    growing a filing's vocabulary means growing a whitelist here, never
    merging a party's dict wholesale."""
    if step.step_key == "ds160_consular":
        # The beneficiary acts; their answers ARE the parent case's dict.
        return dict(parent.answers or {})

    out: dict = {}
    petitioner = _petitioner_party(db, parent.id)
    for key, value in ((petitioner.answers or {}) if petitioner else {}).items():
        if value in (None, ""):
            continue
        if key in PETITIONER_SHARED_KEYS or key.startswith(PETITIONER_SHARED_PREFIXES):
            out[key] = value

    profile = None
    if petitioner is not None and petitioner.employer_profile_id:
        profile = db.get(h1b_models.EmployerProfile, petitioner.employer_profile_id)
    if profile is not None:
        for attr, key in _EMPLOYER_PROFILE_KEYS:
            value = getattr(profile, attr)
            if value:
                out[key] = value
        # Nullable attestations ride only when genuinely answered — an absent
        # answer is asked on the filing, never defaulted (doctrine).
        if profile.h1b_dependent is not None:
            out["h1b_dependent_employer"] = profile.h1b_dependent
        if profile.willful_violator is not None:
            out["willful_violator"] = profile.willful_violator

    if step.step_key in ("registration", "i129"):
        answers = parent.answers or {}
        for key in BENEFICIARY_IDENTITY_KEYS:
            value = answers.get(key)
            if key == "passport_expiry" and value in (None, ""):
                # The passport pipeline writes expiry_date; keep one reader.
                value = answers.get("expiry_date") or answers.get("passport_expiry_date")
            if value not in (None, ""):
                out[key] = value
    return out


def _carry_passport(db, parent: models.VisaApplication,
                    child: models.VisaApplication) -> int:
    """Reuse the parent's accepted passport document (same OCR provenance,
    same bytes — deduped by sha256, never a second upload)."""
    src_docs = db.execute(select(models.StoredDocument).where(
        models.StoredDocument.application_id == parent.id,
        models.StoredDocument.doc_type == "passport")).scalars().all()
    carried = 0
    for d in src_docs:
        if not (d.page_classification or {}).get("accepted_as_passport_identity", True):
            continue
        copy = models.StoredDocument(
            org_id=parent.org_id, application_id=child.id, name=d.name,
            mime=d.mime, size_bytes=d.size_bytes, sha256=d.sha256,
            storage_ref=d.storage_ref, doc_type="passport", ocr_status="done",
            execution_class=d.execution_class,
            page_classification=d.page_classification,
            extracted_fields=d.extracted_fields,
            passport_profile=d.passport_profile,
            quality_warnings=d.quality_warnings, approved=d.approved)
        db.add(copy)
        db.flush()
        blob = db.get(models.DocumentBlob, d.id)
        if blob is not None:
            db.add(models.DocumentBlob(document_id=copy.id, org_id=parent.org_id,
                                       mime=blob.mime, content=blob.content))
        carried += 1
        break
    return carried


def release_step(db, *, parent: models.VisaApplication,
                 step: h1b_models.H1bCaseStep, principal: Principal) -> dict:
    """Create (or reuse — idempotent) the child filing case for a ready step.
    Callers gate on readiness; this function still refuses a non-ready step so
    the invariant never depends on the endpoint alone."""
    if step.child_case_id:
        child = db.get(models.VisaApplication, step.child_case_id)
        return {"child_case_id": step.child_case_id, "already_exists": True,
                "visa_type": (child.visa_type if child is not None
                              else VISA_TYPE_BY_STEP[step.step_key]),
                "step_key": step.step_key, "step_status": step.status}
    if step.status != "ready":
        raise ValueError(f"step '{step.step_key}' is {step.status}, not ready")

    visa_type = VISA_TYPE_BY_STEP[step.step_key]
    answers = answers_for_step(db, parent, step)
    answers["h1b_parent_case_id"] = parent.id
    answers["h1b_step_key"] = step.step_key
    child = models.VisaApplication(
        org_id=parent.org_id, user_id=principal.user_id,
        applicant_id=parent.applicant_id,
        destination_country="United States", visa_type=visa_type,
        answers=answers)
    db.add(child)
    db.flush()

    carried = 0
    if step.acting_party == "beneficiary":
        carried = _carry_passport(db, parent, child)

    db.add(CaseRouteGuidance(
        org_id=parent.org_id, case_id=child.id, intake_id=None,
        route_key=f"h1b_step:{step.step_key}",
        disposition="VISA_REQUIRED",
        continuation_kind=CONTINUATION_KIND,
        guidance={"status": "curated", "as_of": AS_OF,
                  "step_key": step.step_key,
                  "guidance": dict(STEP_FACTS[step.step_key])},
        checklist=[]))

    step.child_case_id = child.id
    step.status = "in_progress"
    db.commit()

    audit.record(db, org_id=parent.org_id, application_id=child.id,
                 action="h1b_filing_case_created",
                 detail={"parent_case_id": parent.id, "step_key": step.step_key,
                         "visa_type": visa_type, "acting_party": step.acting_party,
                         "documents_carried": carried},
                 actor=principal.user_id)
    audit.record(db, org_id=parent.org_id, application_id=parent.id,
                 action="h1b_step_released",
                 detail={"step_key": step.step_key, "child_case_id": child.id,
                         "visa_type": visa_type},
                 actor=principal.user_id)
    return {"child_case_id": child.id, "already_exists": False,
            "visa_type": visa_type, "step_key": step.step_key,
            "step_status": step.status, "documents_carried": carried}
