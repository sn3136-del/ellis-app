"""Pipeline sequencing: a step becomes ready only when every dependency is
verified, and 'verified' means government evidence, never inference.

For a filing child case, verification follows the _adapter_verified_result
discipline: a SubmissionConfirmation plus government-host AdapterOutcomeEvidence.
Adjudication outcomes that arrive later (LCA certification, lottery selection,
I-797 approval) are recorded by an evidence-reading re-check, not a timer.
An admin may record an offline outcome (e.g. a certified LCA PDF arriving by
upload) — that is evidence too, and it is audited as such.
"""
from __future__ import annotations

from sqlalchemy import select

from .. import models
from . import models as h1b_models

# Offline government artifacts an admin may accept as verification evidence when
# a step's outcome arrives outside the portal run (a certified LCA PDF from DOL,
# an I-797 approval notice). Only an ACCEPTED (admin-reviewed) document of one of
# these types, filed on the pipeline's own parent or child case, counts.
_OFFLINE_EVIDENCE_DOC_TYPES = ("certified_lca", "prior_i797")


class EvidenceRequired(ValueError):
    """mark_step_verified refused: a step may only become 'verified' on real
    government evidence, never on a bare receipt dict."""


def steps_for_case(db, application_id: str) -> list[h1b_models.H1bCaseStep]:
    rows = db.execute(select(h1b_models.H1bCaseStep).where(
        h1b_models.H1bCaseStep.application_id == application_id)).scalars().all()
    order = {k: i for i, k in enumerate(h1b_models.STEP_KEYS)}
    return sorted(rows, key=lambda r: order.get(r.step_key, 99))


def child_filing_verified(db, child_case_id: str) -> bool:
    """True only when the child filing case holds GOVERNMENT-verified evidence:
    a SubmissionConfirmation PLUS a completed adapter execution whose outcome
    evidence is on a government host (the main._adapter_verified_result
    discipline the architecture doc pins). persist_workflow writes a
    SubmissionConfirmation for ANY execution class — including MOCK / sandbox —
    so the confirmation row alone is not evidence; a mock or sandbox run returns
    False here and can never unblock the next real filing."""
    if not child_case_id:
        return False
    conf = db.execute(select(models.SubmissionConfirmation).where(
        models.SubmissionConfirmation.application_id == child_case_id)).scalars().first()
    if conf is None:
        return False
    # The government-host verification discipline lives in exactly one place;
    # imported lazily because main imports the h1b router at module load.
    from ..main import _adapter_verified_result
    return _adapter_verified_result(db, child_case_id)


def _accepted_offline_evidence(db, step: h1b_models.H1bCaseStep,
                               document_id: str) -> bool:
    """True when document_id is an admin-accepted offline government artifact
    (certified LCA / I-797) filed on this pipeline's parent or child case."""
    if not document_id:
        return False
    doc = db.get(models.StoredDocument, document_id)
    if doc is None or not doc.approved:
        return False
    if doc.doc_type not in _OFFLINE_EVIDENCE_DOC_TYPES:
        return False
    valid_cases = {step.application_id}
    if step.child_case_id:
        valid_cases.add(step.child_case_id)
    return doc.application_id in valid_cases


def step_has_verifying_evidence(db, step: h1b_models.H1bCaseStep, *,
                                offline_evidence_document_id: str = "") -> bool:
    """The single evidence test mark_step_verified enforces: either the released
    child filing holds government-verified evidence, or an admin supplied an
    accepted offline government artifact."""
    return (child_filing_verified(db, step.child_case_id)
            or _accepted_offline_evidence(db, step, offline_evidence_document_id))


def recompute_readiness(db, application_id: str) -> list[dict]:
    """Idempotent sweep: unblock any step whose dependencies are all verified.
    Returns the honest per-step status list. Never advances a step past
    'ready' — execution belongs to the child case's own workflow."""
    steps = steps_for_case(db, application_id)
    by_key = {s.step_key: s for s in steps}
    changed = False
    for s in steps:
        if s.status == "blocked":
            deps = [by_key.get(k) for k in (s.depends_on or [])]
            if all(d is not None and d.status == "verified" for d in deps):
                s.status = "ready"
                changed = True
    if changed:
        db.commit()
    return [{"step_key": s.step_key, "status": s.status,
             "acting_party": s.acting_party, "depends_on": s.depends_on or [],
             "child_case_id": s.child_case_id or None} for s in steps]


def mark_step_verified(db, step: h1b_models.H1bCaseStep, *, receipts: dict,
                       actor: str, offline_evidence_document_id: str = "") -> None:
    """Record a step's verified government outcome — refusing unless real
    evidence is on file. 'verified' is the transition that unblocks the next
    real government filing, so it is NEVER inferred from a receipt dict alone.

    Verification requires ONE of:
      - the released child filing case holding government-verified evidence
        (child_filing_verified: a SubmissionConfirmation PLUS government-host
        adapter outcome evidence — a mock/sandbox confirmation does not count), or
      - an admin-reviewed offline government artifact (offline_evidence_document_id
        pointing at an ACCEPTED certified-LCA / I-797 document on this case).

    Raises EvidenceRequired if neither is present. The receipts dict only labels
    the already-proven outcome; it can never stand in for the evidence."""
    from .. import audit
    if not step_has_verifying_evidence(
            db, step, offline_evidence_document_id=offline_evidence_document_id):
        raise EvidenceRequired(
            f"step '{step.step_key}' cannot be verified: no government-verified "
            f"child filing confirmation and no accepted offline government "
            f"artifact (certified LCA / I-797) was supplied")
    step.status = "verified"
    for field in ("lca_number", "beneficiary_confirmation_number",
                  "uscis_receipt_number"):
        if receipts.get(field):
            setattr(step, field, str(receipts[field]))
    db.commit()
    audit.record(db, org_id=step.org_id, application_id=step.application_id,
                 action="h1b_step_verified",
                 detail={"step_key": step.step_key,
                         "receipts": {k: v for k, v in receipts.items() if v},
                         "offline_evidence_document_id": offline_evidence_document_id},
                 actor=actor)
    # A certified LCA is the one document the I-129 legally cannot file without,
    # so the parent's checklist must demand it the moment the LCA verifies.
    if step.step_key == "lca":
        _flip_certified_lca_required(db, step.application_id)
    recompute_readiness(db, step.application_id)


def _flip_certified_lca_required(db, application_id: str) -> None:
    """Re-derive the parent umbrella checklist with lca_certified=True and
    persist it, so the 'Certified LCA' item flips from optional to required.
    The case shape (kind, abroad, in-US, first-timer) is read back from the
    persisted checklist so no other item changes."""
    from ..visa_snapshot.models import CaseRouteGuidance
    from .checklist import derive_h1b_checklist
    cg = db.execute(select(CaseRouteGuidance).where(
        CaseRouteGuidance.case_id == application_id)).scalars().first()
    if cg is None:
        return
    app_row = db.get(models.VisaApplication, application_id)
    answers = (app_row.answers or {}) if app_row is not None else {}
    existing = cg.checklist or []
    ids = {i.get("id") for i in existing}
    prior = next((i for i in existing if i.get("id") == "prior_i797"), None)
    cg.checklist = derive_h1b_checklist(
        case_kind=answers.get("h1b_case_kind", "extension"),
        beneficiary_abroad=bool(answers.get("beneficiary_abroad")),
        beneficiary_in_us=("i94_record" in ids),
        first_h1b=not bool(prior and prior.get("required")),
        lca_certified=True)
    db.add(cg)
    db.commit()
