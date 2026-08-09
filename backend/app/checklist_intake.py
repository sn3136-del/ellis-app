"""Document-intake checklist operations: bind an upload to one requirement,
explicit applicant Submit/withdraw, manual type assignment for ambiguous files,
and server-validated completion of the document-intake stage.

State lives in the database (ChecklistSubmission + CaseStageProgress) so the
flow survives refresh, backend/worker restarts, and duplicate clicks. Pure
matching/status rules live in visa_snapshot.intake_flow; this module owns the
persistence and validation around them. No PII in audit details — item ids,
document types and verdicts only (never names, numbers, or file contents).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from . import models, audit
from .visa_snapshot import intake_flow
from .visa_snapshot.models import CaseRouteGuidance

STAGE_DOCUMENT_INTAKE = "document_intake"

# The applicant-facing continuation each route kind advances to once every
# mandatory document is fulfilled. Never creates a new case.
NEXT_STAGE_BY_KIND = {
    "visa_application": "application_preparation",
    "authorization_application": "application_preparation",
    "conditional_guidance": "application_preparation",
    "entry_preparation": "entry_preparation",
    "passport_renewal": "renewal_preparation",
    "h1b_petition": "petition_preparation",
}


class ChecklistError(Exception):
    """Validation failure with an HTTP status + structured, honest detail."""

    def __init__(self, status_code: int, detail):
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    """Stable UTC ISO form. SQLite returns naive datetimes for tz-aware
    columns — normalize both shapes so a freshly-written timestamp and its
    later read-back are byte-identical (idempotency checks depend on it)."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat() + "Z"


def case_guidance(db, application_id: str) -> CaseRouteGuidance | None:
    return db.execute(select(CaseRouteGuidance).where(
        CaseRouteGuidance.case_id == application_id)).scalars().first()


def current_checklist(db, app_row, cg: CaseRouteGuidance | None = None) -> list[dict]:
    """The case's current checklist items, re-derived against the CURRENT
    answers when conditional (health) requirements exist — an item Kimi later
    deems irrelevant disappears here. Persists the refresh. Also heals any
    previously saved checklist that contains a payment-labeled item: payment
    is a workflow stage, never a document requirement (Part 1)."""
    cg = cg or case_guidance(db, app_row.id)
    if cg is None:
        return []
    checklist = cg.checklist or []
    g_inner = (cg.guidance or {}).get("guidance") or {}
    if g_inner.get("health_requirements"):
        checklist = intake_flow.derive_document_checklist(
            g_inner, answers=app_row.answers or {})
    healed = [item for item in checklist
              if not intake_flow.is_payment_label(item.get("label", ""))]
    if healed != (cg.checklist or []):
        cg.checklist = healed
        db.commit()
    return healed


# Checklist slots that hold the applicant's face photograph, and the document
# types the classifier files one under. Used to find the image that belongs in
# a consular form's photo frame.
_PHOTO_HINTS = ("photo", "photograph", "foto", "picture", "照片")


def applicant_photo_bytes(db, application_id: str) -> bytes | None:
    """The applicant's passport-format photograph, if they uploaded one.

    Every consular form draws a frame for it and every applicant has already
    given Ellis the file. Returns the bytes of the most recent non-rejected
    image whose document type or filename says it is the photo — an image, so
    a PDF bank statement can never end up on somebody's visa application.
    """
    docs = db.execute(select(models.StoredDocument).where(
        models.StoredDocument.application_id == application_id).order_by(
        models.StoredDocument.created_at.desc())).scalars().all()
    for d in docs:
        if (d.page_classification or {}).get("reject"):
            continue
        if not str(d.mime or "").lower().startswith("image/"):
            continue
        haystack = f"{d.doc_type or ''} {d.name or ''}".lower()
        if not any(h in haystack for h in _PHOTO_HINTS):
            continue
        blob = db.get(models.DocumentBlob, d.id)
        content = getattr(blob, "content", None)
        if content:
            return content
    return None


def applicant_signature_bytes(db, application_id: str) -> bytes | None:
    """The signature the applicant drew in Ellis's own pad, or None.

    Exact doc_type match — a signature is an act, not a guess, so nothing is
    ever inferred from filenames here the way the photo hunt allows."""
    doc = db.execute(select(models.StoredDocument).where(
        models.StoredDocument.application_id == application_id,
        models.StoredDocument.doc_type == "applicant_signature").order_by(
        models.StoredDocument.created_at.desc())).scalars().first()
    if doc is None:
        return None
    blob = db.get(models.DocumentBlob, doc.id)
    return getattr(blob, "content", None)


def latest_passport_profile(db, app_row) -> dict:
    """The verified passport profile from the applicant's own uploaded biodata
    page, if they uploaded one.

    Lives here because TWO places need it and only one had it: the consular-form
    endpoint looked it up properly while the appointment packet read
    `app_row.passport_profile`, an attribute VisaApplication does not have, so
    the packet always prepared the form with no passport behind it.
    """
    from sqlalchemy import select
    from .visa_snapshot.models import RouteIntakeDocument
    row = db.execute(select(RouteIntakeDocument).where(
        RouteIntakeDocument.doc_type == "passport",
        RouteIntakeDocument.user_id == app_row.user_id).order_by(
        RouteIntakeDocument.created_at.desc())).scalars().first()
    return (row.passport_profile or {}) if row is not None else {}


def document_rows(db, application_id: str) -> list[dict]:
    """Status-computation rows for every stored document (no extracted PII —
    language codes and a has-text flag only, never the text itself)."""
    docs = db.execute(select(models.StoredDocument).where(
        models.StoredDocument.application_id == application_id)).scalars().all()
    translations = {d.translation_of: d.id for d in docs if d.translation_of}
    rows = []
    for d in docs:
        pc = d.page_classification or {}
        rows.append({
            "id": d.id, "name": d.name, "doc_type": d.doc_type,
            "ocr_status": d.ocr_status, "rejected": bool(pc.get("reject")),
            "page_type": str(pc.get("page_type") or ""),
            "accepted_as_passport_identity": pc.get("accepted_as_passport_identity", False),
            "classifier": str(pc.get("classifier") or ""),
            "mime": d.mime, "size_bytes": d.size_bytes,
            "language": d.language or {},
            "has_text": bool((d.ocr_text or "").strip()),
            "translation_document_id": translations.get(d.id),
        })
    return rows


def submission_rows(db, application_id: str) -> list[dict]:
    subs = db.execute(select(models.ChecklistSubmission).where(
        models.ChecklistSubmission.application_id == application_id)).scalars().all()
    return [{
        "item_id": s.item_id, "document_id": s.document_id, "status": s.status,
        "match_verdict": s.match_verdict, "detected_type": s.detected_type,
        "confirmed_by_applicant": s.confirmed_by_applicant,
        "submitted_at": _iso(s.submitted_at),
    } for s in subs]


def stage_progress(db, application_id: str,
                   stage: str = STAGE_DOCUMENT_INTAKE) -> models.CaseStageProgress | None:
    return db.execute(select(models.CaseStageProgress).where(
        models.CaseStageProgress.application_id == application_id,
        models.CaseStageProgress.stage == stage)).scalars().first()


def checklist_state(db, app_row, cg: CaseRouteGuidance | None = None) -> dict:
    """Items with live status + counts + the durable intake-stage marker."""
    cg = cg or case_guidance(db, app_row.id)
    checklist = current_checklist(db, app_row, cg)
    status = intake_flow.apply_checklist_status(
        checklist, document_rows(db, app_row.id), submission_rows(db, app_row.id))
    prog = stage_progress(db, app_row.id)
    status["intake_stage"] = {
        "completed": prog is not None,
        "completed_at": _iso(prog.completed_at) if prog else None,
    }
    return status


def find_item(checklist: list[dict], item_id: str) -> dict | None:
    for item in checklist or []:
        if str(item.get("id")) == str(item_id):
            return item
    return None


def _doc_row(db, app_row, document_id: str) -> models.StoredDocument:
    doc = db.get(models.StoredDocument, document_id)
    if not doc or doc.application_id != app_row.id:
        raise ChecklistError(404, "document not found")
    return doc


def _verdict_for(item: dict, doc: models.StoredDocument) -> str:
    pc = doc.page_classification or {}
    return intake_flow.match_document_to_item(
        item, doc.doc_type, classifier=str(pc.get("classifier") or ""),
        rejected=bool(pc.get("reject")),
        page_type=str(pc.get("page_type") or ""))


def bind_document(db, p, app_row, item_id: str, document_id: str,
                  *, provenance: dict | None = None) -> dict:
    """Bind one uploaded document to one checklist requirement. Replaces any
    previous binding for the item (Replace); never marks anything fulfilled —
    only the applicant's Submit does. A document already serving another
    requirement may be reused only when it genuinely matches this one too."""
    cg = case_guidance(db, app_row.id)
    if cg is None:
        raise ChecklistError(409, "this case has no route checklist")
    item = find_item(current_checklist(db, app_row, cg), item_id)
    if item is None:
        raise ChecklistError(404, "checklist requirement not found")
    if item.get("kind") != "document":
        raise ChecklistError(400, "this requirement does not take an upload")
    doc = _doc_row(db, app_row, document_id)
    verdict = _verdict_for(item, doc)

    # The SAME stored document may serve any number of requirements the
    # applicant explicitly chooses (one booking covering flight + hotel, one
    # financial document covering several evidence items). Bytes are stored
    # once (sha-deduplicated); each requirement gets its own independent
    # binding and its own explicit Submit.
    existing = db.execute(select(models.ChecklistSubmission).where(
        models.ChecklistSubmission.application_id == app_row.id,
        models.ChecklistSubmission.item_id == str(item_id))).scalars().first()
    if existing is not None and existing.document_id == document_id:
        # Same file re-attached (duplicate upload / double click): keep the
        # existing binding — including an already-submitted state — untouched.
        return _binding_dict(existing, doc, verdict)
    replaced_submitted = bool(existing is not None and existing.status == "submitted")
    if existing is not None:
        db.delete(existing)
        db.flush()
    sub = models.ChecklistSubmission(
        org_id=p.org_id, application_id=app_row.id, item_id=str(item_id),
        document_id=document_id, status="bound", match_verdict=verdict,
        detected_type=doc.doc_type, confirmed_by_applicant=False,
        provenance=dict(provenance or {}))
    db.add(sub)
    db.commit()
    audit.record(db, org_id=p.org_id, application_id=app_row.id,
                 action="checklist_document_bound",
                 detail={"item_id": str(item_id), "detected_type": doc.doc_type,
                         "match_verdict": verdict,
                         "replaced_submitted": replaced_submitted},
                 actor=p.user_id)
    return _binding_dict(sub, doc, verdict)


def _binding_dict(sub: models.ChecklistSubmission, doc: models.StoredDocument,
                  verdict: str) -> dict:
    return {"item_id": sub.item_id, "document_id": sub.document_id,
            "document_name": doc.name, "detected_type": doc.doc_type,
            "match_verdict": verdict, "status": sub.status,
            "confirmed_by_applicant": sub.confirmed_by_applicant,
            "submitted_at": _iso(sub.submitted_at)}


def submit_document(db, p, app_row, item_id: str, document_id: str | None,
                    *, confirm: bool = False) -> dict:
    """The applicant's explicit Submit for one requirement. Idempotent: a
    repeated Submit returns the recorded submission unchanged.

    Classification is ADVISORY, never a blocking authority: when the detected
    type differs from the requirement (or could not be identified), Submit
    needs the applicant's explicit confirmation (confirm=True, recorded as
    applicant-confirmed) — and then always succeeds. Only files that failed
    security/structural validation (refused at upload) or offer no secure
    preview/download at all remain blocked."""
    cg = case_guidance(db, app_row.id)
    if cg is None:
        raise ChecklistError(409, "this case has no route checklist")
    item = find_item(current_checklist(db, app_row, cg), item_id)
    if item is None:
        raise ChecklistError(404, "checklist requirement not found")
    sub = db.execute(select(models.ChecklistSubmission).where(
        models.ChecklistSubmission.application_id == app_row.id,
        models.ChecklistSubmission.item_id == str(item_id))).scalars().first()
    if sub is None:
        raise ChecklistError(409, {"reason": "no_upload",
                                   "message": "Upload a document for this requirement first."})
    if document_id and sub.document_id != document_id:
        raise ChecklistError(409, {"reason": "stale_binding",
                                   "message": "A different file is now attached to this "
                                              "requirement. Review it and submit again."})
    doc = _doc_row(db, app_row, sub.document_id)
    if sub.status == "submitted":
        return {"submitted": True, "already_submitted": True,
                "binding": _binding_dict(sub, doc, sub.match_verdict)}
    verdict = _verdict_for(item, doc)
    if verdict != "match" and not confirm:
        detected = doc.doc_type.replace("_", " ") if doc.doc_type else "unrecognized"
        if verdict == "unreadable":
            message = ("Ellis could not read this file. You can replace it "
                       "with a clearer copy, or confirm you want to submit "
                       "it as-is.")
        elif verdict == "mismatch":
            message = (f"Ellis detected this as {detected}. You selected "
                       f"{item.get('label', 'this requirement')}. Confirm that "
                       f"you want to use this document.")
        else:
            message = ("Ellis could not confidently identify this document. "
                       "Confirm it satisfies this requirement, or pick its type.")
        raise ChecklistError(409, {
            "reason": "confirm_required", "match_verdict": verdict,
            "detected_type": doc.doc_type,
            "required_types": list(item.get("satisfied_by") or []),
            "message": message})
    if verdict == "unreadable":
        # Still honest about hard-broken files: with no stored bytes there is
        # no secure preview or download, so nothing reviewable to confirm.
        blob = db.get(models.DocumentBlob, doc.id)
        if blob is None or not blob.content:
            raise ChecklistError(409, {
                "reason": "unreadable",
                "message": "This file has no previewable content. Replace it "
                           "with a readable copy."})
    sub.status = "submitted"
    sub.submitted_at = _now()
    sub.match_verdict = verdict
    sub.detected_type = doc.doc_type
    if verdict != "match":
        sub.confirmed_by_applicant = True
    # The applicant's explicit Submit IS their approval of this document —
    # record it so the document enters the signed material state. Without
    # this the review version hashes an EMPTY document list and replacing a
    # submitted file after signature would not invalidate the signed version.
    doc.approved = True
    db.commit()
    audit.record(db, org_id=p.org_id, application_id=app_row.id,
                 action="checklist_document_submitted",
                 detail={"item_id": str(item_id), "detected_type": doc.doc_type,
                         "match_verdict": verdict,
                         "confirmed_by_applicant": sub.confirmed_by_applicant},
                 actor=p.user_id)
    return {"submitted": True, "already_submitted": False,
            "binding": _binding_dict(sub, doc, verdict)}


def withdraw_document(db, p, app_row, item_id: str) -> dict:
    """Withdraw a submission / remove an upload from a requirement — the item
    returns to Needed. Idempotent: withdrawing an unbound item is a no-op.
    The stored document itself is kept on the case (Replace uses this too)."""
    sub = db.execute(select(models.ChecklistSubmission).where(
        models.ChecklistSubmission.application_id == app_row.id,
        models.ChecklistSubmission.item_id == str(item_id))).scalars().first()
    if sub is None:
        return {"withdrawn": False, "status": "pending"}
    was_submitted = sub.status == "submitted"
    db.delete(sub)
    db.commit()
    audit.record(db, org_id=p.org_id, application_id=app_row.id,
                 action="checklist_document_withdrawn",
                 detail={"item_id": str(item_id), "was_submitted": was_submitted},
                 actor=p.user_id)
    return {"withdrawn": True, "status": "pending"}


def set_document_type(db, p, app_row, document_id: str, doc_type: str) -> dict:
    """Applicant-chosen type for an AMBIGUOUS upload, from the safe whitelist
    only. Never overrides a confident deterministic classification (replace the
    file instead), never a rejected page, and never claims 'passport'."""
    if doc_type not in intake_flow.MANUAL_DOC_TYPES:
        raise ChecklistError(400, "unsupported document type")
    doc = _doc_row(db, app_row, document_id)
    pc = dict(doc.page_classification or {})
    if pc.get("reject"):
        raise ChecklistError(409, "this file was unreadable — upload a clearer copy instead")
    if doc.doc_type not in ("", "document", "unknown") and \
            str(pc.get("classifier") or "") not in ("", "none", "kimi"):
        raise ChecklistError(409, {
            "reason": "confident_classification",
            "message": f"Ellis identified this as {doc.doc_type.replace('_', ' ')} — "
                       "replace the file if that is wrong."})
    doc.doc_type = doc_type
    pc["classifier"] = "applicant"
    doc.page_classification = pc
    # Refresh recorded verdicts on any binding of this document.
    cg = case_guidance(db, app_row.id)
    checklist = current_checklist(db, app_row, cg) if cg else []
    for sub in db.execute(select(models.ChecklistSubmission).where(
            models.ChecklistSubmission.application_id == app_row.id,
            models.ChecklistSubmission.document_id == document_id)).scalars().all():
        item = find_item(checklist, sub.item_id)
        if item is not None:
            sub.match_verdict = _verdict_for(item, doc)
        sub.detected_type = doc_type
    db.commit()
    audit.record(db, org_id=p.org_id, application_id=app_row.id,
                 action="document_type_set_by_applicant",
                 detail={"doc_type": doc_type}, actor=p.user_id)
    return {"document_id": document_id, "doc_type": doc_type}


def complete_stage(db, p, app_row) -> dict:
    """Server-side validation + durable completion of the document-intake
    stage. Refuses while any mandatory requirement is unfulfilled; idempotent
    once completed. Advances the EXISTING case to its route's next stage —
    never a new case, never a restarted wizard."""
    cg = case_guidance(db, app_row.id)
    if cg is None:
        raise ChecklistError(409, "this case has no route checklist")
    state = checklist_state(db, app_row, cg)
    remaining = state["counts"]["required_missing"]
    if remaining > 0:
        missing = [{"id": i["id"], "label": i["label"], "status": i["status"]}
                   for i in state["items"]
                   if i.get("kind") == "document" and i.get("required")
                   and i["status"] != "submitted" and not i.get("waived")]
        raise ChecklistError(409, {
            "reason": "checklist_incomplete", "required_remaining": remaining,
            "missing": missing,
            "message": f"Submit {remaining} remaining required "
                       f"document{'s' if remaining != 1 else ''} to continue."})
    next_stage = NEXT_STAGE_BY_KIND.get(cg.continuation_kind, "application_preparation")
    prog = stage_progress(db, app_row.id)
    if prog is not None:
        return {"completed": True, "already_completed": True,
                "next_stage": next_stage,
                "completed_at": _iso(prog.completed_at)}
    prog = models.CaseStageProgress(
        org_id=p.org_id, application_id=app_row.id, stage=STAGE_DOCUMENT_INTAKE,
        completed_at=_now(),
        detail={"required_documents": state["counts"]["required_documents"],
                "fulfilled": state["counts"]["fulfilled"],
                "continuation_kind": cg.continuation_kind})
    db.add(prog)
    db.commit()
    audit.record(db, org_id=p.org_id, application_id=app_row.id,
                 action="document_intake_completed",
                 detail={"required_documents": state["counts"]["required_documents"],
                         "next_stage": next_stage}, actor=p.user_id)
    return {"completed": True, "already_completed": False,
            "next_stage": next_stage, "completed_at": _iso(prog.completed_at)}


def seed_intake_confirmed_passport(db, *, org_id: str, application_id: str,
                                   document: models.StoredDocument) -> None:
    """At intake→case continuation: the applicant already reviewed the
    extracted passport profile and explicitly applied it ('Use these details'),
    so the passport requirement is recorded as submitted with that provenance.
    An unconfirmed carry-over is NOT seeded — it goes through the normal
    upload→review→Submit flow like every other requirement."""
    pc = document.page_classification or {}
    if not pc.get("accepted_as_passport_identity"):
        return
    existing = db.execute(select(models.ChecklistSubmission).where(
        models.ChecklistSubmission.application_id == application_id,
        models.ChecklistSubmission.item_id == "passport")).scalars().first()
    if existing is not None:
        return
    db.add(models.ChecklistSubmission(
        org_id=org_id, application_id=application_id, item_id="passport",
        document_id=document.id, status="submitted", submitted_at=_now(),
        match_verdict="match", detected_type=document.doc_type,
        confirmed_by_applicant=True,
        provenance={"source": "intake_confirmed"}))
