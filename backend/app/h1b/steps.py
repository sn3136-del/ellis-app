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


def steps_for_case(db, application_id: str) -> list[h1b_models.H1bCaseStep]:
    rows = db.execute(select(h1b_models.H1bCaseStep).where(
        h1b_models.H1bCaseStep.application_id == application_id)).scalars().all()
    order = {k: i for i, k in enumerate(h1b_models.STEP_KEYS)}
    return sorted(rows, key=lambda r: order.get(r.step_key, 99))


def child_filing_verified(db, child_case_id: str) -> bool:
    """True only when the child filing case holds a SubmissionConfirmation —
    the row the workflow writes exclusively from verified portal evidence."""
    if not child_case_id:
        return False
    conf = db.execute(select(models.SubmissionConfirmation).where(
        models.SubmissionConfirmation.application_id == child_case_id)).scalars().first()
    return conf is not None


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
                       actor: str) -> None:
    """Record a step's verified government outcome. Callers must hold real
    evidence BEFORE calling (child SubmissionConfirmation, or an admin-reviewed
    document like the certified LCA); this function only persists + audits."""
    from .. import audit
    step.status = "verified"
    for field in ("lca_number", "beneficiary_confirmation_number",
                  "uscis_receipt_number"):
        if receipts.get(field):
            setattr(step, field, str(receipts[field]))
    db.commit()
    audit.record(db, org_id=step.org_id, application_id=step.application_id,
                 action="h1b_step_verified",
                 detail={"step_key": step.step_key,
                         "receipts": {k: v for k, v in receipts.items() if v}},
                 actor=actor)
    recompute_readiness(db, step.application_id)
