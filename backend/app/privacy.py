"""Applicant privacy: data export (portability) and deletion (erasure).

Export gathers everything Ellis holds for a case/tenant into a portable JSON
bundle (never provider secrets — those are redacted at source and never stored).
Deletion cascades every per-case row and, for applicant erasure, the applicant
plus all their cases, leaving only a minimal non-PII audit tombstone so the
append-only trail records that erasure happened.

Retention: retention_due() lists cases whose completion/last-activity is older
than the configured window, for a sweep to erase.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import select

from . import models

# Per-case child tables keyed by application_id (order doesn't matter — no FKs
# enforce ordering in SQLite; Postgres has no cross-table FKs here either).
_CASE_CHILD_MODELS = [
    models.VisaApplicationVersion, models.ApplicantApproval, models.AuthorizationEnvelope,
    models.PortalAccount, models.NativeSignature, models.SignatureEvent,
    models.ChecklistSubmission, models.CaseStageProgress, models.StoredDocument,
    models.AppointmentPreference, models.Appointment, models.PaymentAttempt,
    models.SubmissionConfirmation, models.WorkflowExecution, models.HumanHandoff,
    models.EmailNotification, models.ApplicantStandingAuthorization,
    models.ApplicationReviewVersion, models.PaymentAuthorization,
    models.PortalRun, models.CaseProgressEvent, models.BrowserSession,
]

# H1B two-party tables join the cascade (import kept below the legacy list so
# a missing h1b module can never break tourist-only deployments).
try:
    from .h1b import models as _h1b_models
    _CASE_CHILD_MODELS += [_h1b_models.CaseParty, _h1b_models.H1bCaseStep]
except Exception:  # pragma: no cover - h1b module always ships in this edition
    _h1b_models = None


def _rows(db, model, application_id):
    return db.execute(select(model).where(model.application_id == application_id)).scalars().all()


def export_case(db, application_id: str) -> dict:
    """A portable, secret-free snapshot of everything held for one case."""
    app = db.get(models.VisaApplication, application_id)
    if not app:
        raise KeyError("case not found")
    applicant = db.get(models.Applicant, app.applicant_id)
    docs = _rows(db, models.StoredDocument, application_id)
    appts = _rows(db, models.Appointment, application_id)
    confs = _rows(db, models.SubmissionConfirmation, application_id)
    sigs = _rows(db, models.NativeSignature, application_id)
    audit = _rows(db, models.AuditEvent, application_id)
    # H1B two-party data: the parties Ellis holds and the government-filing
    # pipeline (receipts included). The bundle legally must RETURN this data,
    # not only erase it — export and erasure are the two halves of the same
    # contract. Secret-free by construction: no vault refs live on these rows
    # (employer_profile_id is an id, party answers are petition facts).
    parties, steps, employer_profiles = [], [], []
    if _h1b_models is not None:
        parties = _rows(db, _h1b_models.CaseParty, application_id)
        steps = _rows(db, _h1b_models.H1bCaseStep, application_id)
        # The petitioner's org-scoped EmployerProfile holds FEIN, signatory
        # contact and financials that never live on a CaseParty row; portability
        # must RETURN them (export/erasure parity — finding #7).
        seen_pids = set()
        for p in parties:
            pid = getattr(p, "employer_profile_id", "") or ""
            if pid and pid not in seen_pids:
                prof = db.get(_h1b_models.EmployerProfile, pid)
                if prof is not None:
                    seen_pids.add(pid)
                    employer_profiles.append(prof)
    return {
        "exported_at": _now_iso(),
        "case": {"id": app.id, "state": app.state, "destination_country": app.destination_country,
                 "visa_type": app.visa_type, "answers": app.answers, "portal_reference": app.portal_reference},
        "applicant": ({"full_name": applicant.full_name, "email": applicant.email,
                       "phone": applicant.phone, "time_zone": applicant.time_zone} if applicant else None),
        "documents": [{"name": d.name, "mime": d.mime, "size_bytes": d.size_bytes,
                       "sha256": d.sha256, "doc_type": d.doc_type, "approved": d.approved,
                       "extracted_fields": d.extracted_fields} for d in docs],
        "signatures": [{"id": s.id, "provider": s.provider, "artifact_hash": s.artifact_hash,
                        "signature_method": s.signature_method, "invalidated": s.invalidated} for s in sigs],
        "appointments": [{"slot_id": a.slot_id, "location_id": a.location_id, "start_utc": a.start_utc,
                          "confirmation_no": a.confirmation_no, "reschedule_count": a.reschedule_count} for a in appts],
        "confirmations": [{"reference_no": c.reference_no, "receipt_no": c.receipt_no} for c in confs],
        "case_parties": [{"role": p.role, "party_kind": p.party_kind,
                          "display_name": p.display_name, "email": p.email, "phone": p.phone,
                          "status": p.status, "employer_profile_id": p.employer_profile_id,
                          "answers": p.answers} for p in parties],
        "h1b_steps": [{"step_key": s.step_key, "acting_party": s.acting_party,
                       "status": s.status, "child_case_id": s.child_case_id,
                       "depends_on": s.depends_on, "lca_number": s.lca_number,
                       "beneficiary_confirmation_number": s.beneficiary_confirmation_number,
                       "uscis_receipt_number": s.uscis_receipt_number} for s in steps],
        "employer_profiles": [{
            "legal_name": e.legal_name, "trade_name": e.trade_name, "fein": e.fein,
            "naics_code": e.naics_code, "address_line1": e.address_line1,
            "address_line2": e.address_line2, "city": e.city, "state": e.state,
            "postal_code": e.postal_code, "phone": e.phone,
            "signatory_name": e.signatory_name, "signatory_title": e.signatory_title,
            "signatory_email": e.signatory_email, "signatory_phone": e.signatory_phone,
            "gross_annual_income_cents": e.gross_annual_income_cents,
            "net_annual_income_cents": e.net_annual_income_cents,
            "parent_company_name": e.parent_company_name,
            "parent_company_country": e.parent_company_country}
            for e in employer_profiles],
        "audit": [{"seq": e.seq, "action": e.action, "actor": e.actor, "detail": e.detail} for e in audit],
    }


def export_org(db, org_id: str) -> dict:
    """Tenant export: every case owned by an org."""
    apps = db.execute(select(models.VisaApplication).where(
        models.VisaApplication.org_id == org_id)).scalars().all()
    return {"exported_at": _now_iso(), "org_id": org_id,
            "cases": [export_case(db, a.id) for a in apps]}


def delete_case(db, application_id: str, *, actor: str = "applicant", reason: str = "applicant_request") -> dict:
    """Erase one case: all child rows + portal state + the application row.
    Leaves a non-PII tombstone audit event so erasure is itself recorded."""
    app = db.get(models.VisaApplication, application_id)
    if not app:
        raise KeyError("case not found")
    org_id = app.org_id
    counts = {}
    # H1B child FILING cases are independent VisaApplication rows linked only by
    # H1bCaseStep.child_case_id (a plain string column, no FK cascade). Each
    # carries party PII (employer FEIN/wage on LCA/I-129 children, a copied
    # passport blob on the consular child) and each petitioner-acting child owns
    # a freshly-minted petitioner Applicant. Erase them FIRST — before the parent
    # row and its step rows go — so right-to-erasure actually reaches them
    # (finding #6). One level deep: children have no steps, so no recursion loop.
    child_n = 0
    if _h1b_models is not None:
        for step in _rows(db, _h1b_models.H1bCaseStep, application_id):
            cid = getattr(step, "child_case_id", "") or ""
            if not cid or cid == application_id:
                continue
            if db.get(models.VisaApplication, cid) is None:
                continue
            delete_case(db, cid, actor=actor, reason=f"{reason}:h1b_child")
            child_n += 1
    counts["h1b_child_cases"] = child_n
    # Petitioner EmployerProfile rows are org-scoped (org_id, no application_id),
    # so the per-case cascade never reaches them. Collect the profiles this
    # case's parties reference now; after the case (and its CaseParty rows) are
    # gone, erase any left unreferenced — otherwise petitioner PII (FEIN,
    # signatory contact, financials) survives erasure with no path to delete it
    # (finding #7). A profile still used by another case is kept.
    employer_profile_ids = set()
    if _h1b_models is not None:
        for cp in _rows(db, _h1b_models.CaseParty, application_id):
            pid = getattr(cp, "employer_profile_id", "") or ""
            if pid:
                employer_profile_ids.add(pid)
    # Document preview bytes are keyed by document_id (not application_id), so
    # erase them explicitly — otherwise raw passport-scan bytes would survive
    # applicant erasure indefinitely.
    doc_ids = [d.id for d in _rows(db, models.StoredDocument, application_id)]
    blob_n = 0
    for did in doc_ids:
        blob = db.get(models.DocumentBlob, did)
        if blob:
            db.delete(blob); blob_n += 1
    counts["document_blobs"] = blob_n
    # Vaulted secrets referenced by this case's rows (portal credentials,
    # session refs in the workflow snapshot, any one-time token still queued)
    # are destroyed — ciphertext must not survive erasure.
    from . import vault as vault_mod
    vault_n = 0
    for acct in _rows(db, models.PortalAccount, application_id):
        for ref in (acct.credential_ref or "", acct.session_ref or ""):
            if ref and vault_mod.destroy(ref):
                vault_n += 1
    exec_row = db.execute(select(models.WorkflowExecution).where(
        models.WorkflowExecution.application_id == application_id)).scalars().first()
    snap = (exec_row.snapshot or {}) if exec_row else {}
    for key in ("credential_ref", "session_ref"):
        ref = snap.get(key) or ""
        if ref and vault_mod.destroy(ref):
            vault_n += 1
    for run in _rows(db, models.PortalRun, application_id):
        for key in ("token_ref", "card_ref"):
            ref = (run.signal_kwargs or {}).get(key) or ""
            if ref and vault_mod.destroy(ref):
                vault_n += 1
    counts["vault_secrets"] = vault_n
    for model in _CASE_CHILD_MODELS:
        rows = _rows(db, model, application_id)
        counts[model.__tablename__] = len(rows)
        for r in rows:
            db.delete(r)
    # Portal state is keyed by case_id.
    ps = db.get(models.PortalState, application_id)
    if ps:
        db.delete(ps); counts["portal_states"] = 1
    # Journey rows keyed by case_id: the saved route guidance/checklist, and any
    # intake-stage document rows linked through the converted intake (their
    # bytes and extracted passport profile are personal data).
    from .visa_snapshot.models import CaseRouteGuidance, RouteIntake, RouteIntakeDocument
    cg = db.execute(select(CaseRouteGuidance).where(
        CaseRouteGuidance.case_id == application_id)).scalars().all()
    counts["case_route_guidance"] = len(cg)
    for row in cg:
        db.delete(row)
    intakes = db.execute(select(RouteIntake).where(
        RouteIntake.case_id == application_id)).scalars().all()
    idoc_n = 0
    for it in intakes:
        for idoc in db.execute(select(RouteIntakeDocument).where(
                RouteIntakeDocument.intake_id == it.id)).scalars().all():
            db.delete(idoc); idoc_n += 1
        db.delete(it)
    counts["route_intakes"] = len(intakes)
    counts["route_intake_documents"] = idoc_n
    # Old audit events for this case are personal-linked; remove them, then
    # write a single fresh tombstone.
    for e in _rows(db, models.AuditEvent, application_id):
        db.delete(e)
    applicant_id = app.applicant_id
    db.delete(app)
    db.flush()
    # Erase the applicant if they now have no remaining cases.
    orphan = db.execute(select(models.VisaApplication).where(
        models.VisaApplication.applicant_id == applicant_id)).scalars().first()
    if not orphan:
        applicant = db.get(models.Applicant, applicant_id)
        if applicant:
            db.delete(applicant); counts["applicants"] = 1
    # Erase petitioner EmployerProfile rows this case referenced that no other
    # case still uses (autoflush ensures the just-deleted CaseParty rows are gone
    # before this reference check runs).
    emp_n = 0
    if _h1b_models is not None and employer_profile_ids:
        for pid in employer_profile_ids:
            still = db.execute(select(_h1b_models.CaseParty).where(
                _h1b_models.CaseParty.employer_profile_id == pid)).scalars().first()
            if still is not None:
                continue
            prof = db.get(_h1b_models.EmployerProfile, pid)
            if prof is not None:
                db.delete(prof); emp_n += 1
    counts["employer_profiles"] = emp_n
    tomb = models.AuditEvent(org_id=org_id, application_id=application_id, actor=actor,
                             action="case_erased", detail={"reason": reason, "counts": counts})
    db.add(tomb)
    db.commit()
    return {"deleted": True, "counts": counts}


def delete_applicant(db, applicant_id: str, *, actor: str = "applicant") -> dict:
    """Right-to-erasure for an applicant: erase every case then the applicant."""
    app_ids = [a.id for a in db.execute(select(models.VisaApplication).where(
        models.VisaApplication.applicant_id == applicant_id)).scalars().all()]
    total = {"cases": 0}
    for aid in app_ids:
        # A case may already be gone if it was an H1B child erased when its
        # parent case (also this applicant's) was deleted earlier in the loop.
        if db.get(models.VisaApplication, aid) is None:
            continue
        delete_case(db, aid, actor=actor, reason="applicant_erasure")
        total["cases"] += 1
    applicant = db.get(models.Applicant, applicant_id)
    if applicant:
        db.delete(applicant); db.commit()
        total["applicant"] = 1
    return {"deleted": True, **total}


def retention_due(db, *, days: int | None = None) -> list[str]:
    """Case ids whose last update is older than the retention window."""
    days = days if days is not None else int(os.getenv("ELLIS_RETENTION_DAYS", "365"))
    cutoff = _now_ms() - days * 86_400_000
    out = []
    for a in db.execute(select(models.VisaApplication)).scalars().all():
        ts = a.updated_at
        ms = int(ts.timestamp() * 1000) if isinstance(ts, datetime) else None
        if ms is not None and ms < cutoff:
            out.append(a.id)
    return out


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)
