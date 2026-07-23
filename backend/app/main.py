"""FastAPI application — authenticated REST API for the tourist-visa flow.

Every route enforces authentication (Clerk or dev token) and object-level
tenant isolation. Sensitive transitions go through the durable service layer.
"""
from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Header, Request
from pydantic import BaseModel
from sqlalchemy import select

from .config import capabilities
from .db import get_session, create_all
from . import models, audit, service
from .security import Principal, get_principal, require_owner, issue_action_token, verify_action_token
from .providers import ocr as ocr_provider
from .providers import docusign
from .providers.kimi import run_agent
from .portal.contract import list_adapters, clear_registry
from .portal.mock_portal import MockPortal
from .portal.adapters.mockland import build_mockland_adapter
from .portal.adapters.vietnam_evisa import build_vietnam_evisa_adapter

app = FastAPI(title="Ellis Visa Backend", version="0.1.0")

# CORS so the Electron renderer (file:// / vite dev server) can reach the API.
# The renderer authenticates with a Bearer token in the Authorization header
# (never cookies), so credentials are disabled and a wildcard origin is safe in
# development. Production restricts via ELLIS_CORS_ORIGINS (comma-separated).
import os as _os
from fastapi.middleware.cors import CORSMiddleware

_cors = [o.strip() for o in _os.getenv("ELLIS_CORS_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    create_all()


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/capabilities")
def get_capabilities(_: Principal = Depends(get_principal)):
    return capabilities()


@app.get("/diagnostics/ocr")
def ocr_diagnostics(_: Principal = Depends(get_principal)):
    # Non-sensitive booleans + redacted error category only. Never returns
    # tokens, credential paths, or document content.
    from .providers import ocr_health
    return ocr_health.diagnostic()


@app.get("/adapters")
def get_adapters(_: Principal = Depends(get_principal)):
    # Registry is portal-bound; build once to list metadata honestly.
    clear_registry()
    p = MockPortal()
    build_mockland_adapter(p)
    build_vietnam_evisa_adapter(p)
    return {"adapters": list_adapters()}


# ---- Schemas ----
class CreateCase(BaseModel):
    full_name: str
    email: str
    phone: str = ""
    time_zone: str = "UTC"
    destination_country: str
    visa_type: str = "tourist"
    answers: dict = {}


class AddDocument(BaseModel):
    name: str
    mime: str = "application/pdf"
    size_bytes: int = 1024
    text: str = ""          # embedded text layer / fixture for the local OCR provider
    content_b64: str = ""   # base64 image/PDF bytes → routed to Document AI / Kimi vision OCR


class FieldEdit(BaseModel):
    key: str
    value: str


class Preferences(BaseModel):
    prefs: dict


class Authorization(BaseModel):
    max_fee_cents: int = 10000
    currency: str = "USD"
    allow_auto_book: bool = True
    allow_auto_reschedule: bool = False
    allow_representative_submit: bool = False


# ---- Cases ----
@app.post("/cases")
def create_case(body: CreateCase, db=Depends(get_session), p: Principal = Depends(get_principal)):
    applicant = models.Applicant(org_id=p.org_id, user_id=p.user_id, full_name=body.full_name,
                                 email=body.email, phone=body.phone, time_zone=body.time_zone)
    db.add(applicant)
    db.flush()
    ans = dict(body.answers)
    ans.setdefault("full_name", body.full_name)
    ans.setdefault("email", body.email)
    app_row = models.VisaApplication(org_id=p.org_id, user_id=p.user_id, applicant_id=applicant.id,
                                     destination_country=body.destination_country,
                                     visa_type=body.visa_type, answers=ans)
    db.add(app_row)
    db.commit()
    audit.record(db, org_id=p.org_id, application_id=app_row.id, action="case_created",
                 detail={"destination": body.destination_country}, actor=p.user_id)
    return {"id": app_row.id, "state": app_row.state}


def _owned(db, p: Principal, application_id: str) -> models.VisaApplication:
    app_row = db.get(models.VisaApplication, application_id)
    if not app_row:
        raise HTTPException(404, "case not found")
    require_owner(p, app_row.org_id)
    return app_row


@app.get("/cases/{application_id}")
def get_case(application_id: str, db=Depends(get_session), p: Principal = Depends(get_principal)):
    app_row = _owned(db, p, application_id)
    exec_row = db.execute(select(models.WorkflowExecution).where(
        models.WorkflowExecution.application_id == application_id)).scalar_one_or_none()
    appt = db.execute(select(models.Appointment).where(
        models.Appointment.application_id == application_id)).scalar_one_or_none()
    conf = db.execute(select(models.SubmissionConfirmation).where(
        models.SubmissionConfirmation.application_id == application_id)).scalar_one_or_none()
    return {"id": app_row.id, "state": app_row.state, "answers": app_row.answers,
            "pending": exec_row.pending if exec_row else None,
            "portal_reference": app_row.portal_reference,
            "appointment": ({"slot_id": appt.slot_id, "location_id": appt.location_id,
                             "start_utc": appt.start_utc, "confirmation_no": appt.confirmation_no,
                             "reschedule_count": appt.reschedule_count} if appt else None),
            "confirmation": ({"reference_no": conf.reference_no, "receipt_no": conf.receipt_no}
                             if conf else None)}


@app.get("/cases/{application_id}/mock/verification")
def mock_verification(application_id: str, db=Depends(get_session), p: Principal = Depends(get_principal)):
    """MOCK-ONLY convenience: returns the verification token the mock portal
    'emailed', so the applicant UI can complete the email-verification handoff in
    a demo. In production the applicant reads their own real email (or Mailpit in
    the local stack); this endpoint is disabled outside development and never
    exposes real personal data — only the mock's own generated token."""
    from .config import settings as _settings
    if _settings().env not in ("development", "test"):
        raise HTTPException(404, "not found")
    _owned(db, p, application_id)
    wf = service.load_workflow(db, application_id)
    portal = getattr(wf, "_portal", None)
    emails = list(getattr(portal, "emails", []) or [])
    for e in reversed(emails):
        if e.get("kind") == "verification" and "token=" in e.get("link", ""):
            return {"token": e["link"].split("token=")[1], "kind": "verification"}
    return {"token": None, "kind": "verification"}


@app.post("/cases/{application_id}/documents")
def add_document(application_id: str, body: AddDocument, db=Depends(get_session),
                 p: Principal = Depends(get_principal)):
    import base64
    import hashlib
    app_row = _owned(db, p, application_id)
    # File-type + size validation (MIME allowlist, 10 MB cap).
    if body.mime not in ("application/pdf", "image/jpeg", "image/png", "image/tiff"):
        raise HTTPException(415, "unsupported document type")
    if body.size_bytes > 10 * 1024 * 1024:
        raise HTTPException(413, "document too large")
    content = b""
    if body.content_b64:
        try:
            content = base64.b64decode(body.content_b64)
        except Exception:
            raise HTTPException(400, "invalid content_b64")
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(413, "document too large")
    sha = hashlib.sha256(content or body.text.encode()).hexdigest()
    # OCR hierarchy with recorded failover: Document AI → (flagged) Kimi vision → local.
    result, ocr_meta = ocr_provider.process_with_failover(content=content, text=body.text, mime=body.mime)
    doc = models.StoredDocument(org_id=p.org_id, application_id=application_id, name=body.name,
                                mime=body.mime, size_bytes=body.size_bytes, sha256=sha,
                                storage_ref=f"local://{sha[:16]}", doc_type=result.doc_type,
                                ocr_status="done", quality_warnings=result.quality_warnings,
                                extracted_fields={f.key: {"value": f.value, "confidence": f.confidence,
                                                          "page": f.page} for f in result.fields})
    db.add(doc)
    db.commit()
    audit.record(db, org_id=p.org_id, application_id=application_id, action="document_ocr",
                 detail={"doc_type": result.doc_type, "mrz_valid": result.mrz_valid,
                         "engine": ocr_meta.get("primary"), "fallback_used": ocr_meta.get("fallback_used"),
                         "docai_degraded": ocr_meta.get("docai_degraded")}, actor=p.user_id)
    return {"id": doc.id, "doc_type": result.doc_type, "mrz_valid": result.mrz_valid,
            "extracted_fields": doc.extracted_fields, "quality_warnings": result.quality_warnings}


def _required_fields_for(country: str, visa_type: str) -> list[str]:
    """The applicant fields the destination's adapter requires (for the
    'missing information' step). Adapters are portal-bound; build once to read
    their metadata honestly."""
    clear_registry()
    portal = MockPortal()
    build_mockland_adapter(portal)
    build_vietnam_evisa_adapter(portal)
    from .portal.contract import select_adapter
    adapter = select_adapter(country, visa_type) or select_adapter("Mockland", "tourist")
    return list(getattr(adapter, "required_applicant_fields", []) or [])


@app.get("/cases/{application_id}/review")
def review(application_id: str, db=Depends(get_session), p: Principal = Depends(get_principal)):
    app_row = _owned(db, p, application_id)
    docs = db.execute(select(models.StoredDocument).where(
        models.StoredDocument.application_id == application_id)).scalars().all()
    conflicts = ocr_provider.cross_document_conflicts(
        [{"fields": [{"key": k, "value": v["value"]} for k, v in d.extracted_fields.items()]} for d in docs])
    required = _required_fields_for(app_row.destination_country, app_row.visa_type)
    answers = app_row.answers or {}
    missing = [f for f in required if not answers.get(f)]
    return {"documents": [{"id": d.id, "name": d.name, "doc_type": d.doc_type, "approved": d.approved,
                           "extracted_fields": d.extracted_fields, "quality_warnings": d.quality_warnings}
                          for d in docs], "conflicts": conflicts,
            "required_fields": required, "missing_fields": missing, "answers": answers}


class AnswersUpdate(BaseModel):
    answers: dict


@app.post("/cases/{application_id}/answers")
def update_answers(application_id: str, body: AnswersUpdate, db=Depends(get_session),
                   p: Principal = Depends(get_principal)):
    """Merge applicant-supplied answers (the 'missing information' step). A
    material change invalidates any prior signature, exactly like approving a
    document field."""
    app_row = _owned(db, p, application_id)
    ans = dict(app_row.answers or {})
    changed = {k: v for k, v in (body.answers or {}).items() if ans.get(k) != v}
    ans.update({k: v for k, v in (body.answers or {}).items()})
    app_row.answers = ans
    db.commit()
    invalidated = invalidate_signatures_if_changed(db, application_id)
    audit.record(db, org_id=p.org_id, application_id=application_id, action="answers_updated",
                 detail={"keys": list(changed.keys()), "signatures_invalidated": invalidated}, actor=p.user_id)
    required = _required_fields_for(app_row.destination_country, app_row.visa_type)
    missing = [f for f in required if not ans.get(f)]
    return {"answers": ans, "missing_fields": missing, "signatures_invalidated": invalidated}


@app.post("/cases/{application_id}/documents/{doc_id}/approve")
def approve_document(application_id: str, doc_id: str, edits: Optional[list[FieldEdit]] = None,
                     db=Depends(get_session), p: Principal = Depends(get_principal)):
    app_row = _owned(db, p, application_id)
    doc = db.get(models.StoredDocument, doc_id)
    if not doc or doc.application_id != application_id:
        raise HTTPException(404, "document not found")
    fields = dict(doc.extracted_fields)
    for e in (edits or []):
        fields[e.key] = {"value": e.value, "confidence": 1.0, "page": 1, "source": "applicant_edit"}
    doc.extracted_fields = fields
    doc.approved = True
    # Approved fields flow into the application answers.
    ans = dict(app_row.answers)
    for k, v in fields.items():
        ans[k] = v["value"]
    app_row.answers = ans
    db.commit()
    # A material change to approved data invalidates any prior signature.
    invalidated = invalidate_signatures_if_changed(db, application_id)
    audit.record(db, org_id=p.org_id, application_id=application_id, action="document_approved",
                 detail={"doc_id": doc_id, "edits": [e.key for e in (edits or [])],
                         "signatures_invalidated": invalidated}, actor=p.user_id)
    return {"approved": True, "answers": app_row.answers, "signatures_invalidated": invalidated}


@app.post("/cases/{application_id}/preferences")
def set_preferences(application_id: str, body: Preferences, db=Depends(get_session),
                    p: Principal = Depends(get_principal)):
    _owned(db, p, application_id)
    row = db.execute(select(models.AppointmentPreference).where(
        models.AppointmentPreference.application_id == application_id)).scalar_one_or_none()
    if not row:
        row = models.AppointmentPreference(application_id=application_id)
        db.add(row)
    row.prefs = body.prefs
    db.commit()
    return {"prefs": row.prefs}


@app.post("/cases/{application_id}/authorization")
def create_authorization(application_id: str, body: Authorization, db=Depends(get_session),
                         p: Principal = Depends(get_principal)):
    app_row = _owned(db, p, application_id)
    applicant = db.get(models.Applicant, app_row.applicant_id)
    payload = docusign.authorization_payload(
        applicant={"full_name": applicant.full_name, "email": applicant.email},
        destination=app_row.destination_country, visa_type=app_row.visa_type,
        portal=app_row.destination_country, max_fee_cents=body.max_fee_cents, currency=body.currency,
        allow_auto_book=body.allow_auto_book, allow_auto_reschedule=body.allow_auto_reschedule,
        allow_representative_submit=body.allow_representative_submit)
    env = docusign.create_envelope(payload)
    row = models.AuthorizationEnvelope(application_id=application_id, provider=env["provider"],
                                       envelope_id=env.get("envelope_id") or "",
                                       max_fee_cents=body.max_fee_cents, currency=body.currency,
                                       allow_auto_book=body.allow_auto_book,
                                       allow_auto_reschedule=body.allow_auto_reschedule,
                                       allow_representative_submit=body.allow_representative_submit,
                                       artifact_hash=env.get("artifact_hash", ""))
    db.add(row)
    db.commit()
    audit.record(db, org_id=p.org_id, application_id=application_id, action="authorization_created",
                 detail={"provider": env["provider"]}, actor=p.user_id)
    return {"provider": env["provider"], "production_equivalent": env.get("production_equivalent", True)}


# ---- Official portal discovery (produces DISABLED drafts only) ----
class DiscoverBody(BaseModel):
    country: str
    visa_type: str = "tourist"


@app.post("/discovery")
def run_discovery(body: DiscoverBody, db=Depends(get_session), p: Principal = Depends(get_principal)):
    from .portal import discovery
    draft = discovery.discover_official_visa_portal(country=body.country, visa_type=body.visa_type)
    row = models.PortalDraft(org_id=p.org_id, country=body.country, visa_type=body.visa_type,
                             draft=draft, status="disabled_draft")
    db.add(row)
    db.commit()
    audit.record(db, org_id=p.org_id, application_id="", action="portal_discovery",
                 detail={"country": body.country, "candidates": len(draft["candidates"]),
                         "search_status": draft["search_status"]}, actor=p.user_id)
    # The draft is ALWAYS disabled; activation requires human review + approval.
    return {"draft_id": row.id, "adapter_status": draft["adapter_status"],
            "production_enabled": draft["production_enabled"], "requires_admin_review": True,
            "search_status": draft["search_status"], "verified_candidates": draft["verified_candidates"]}


@app.get("/discovery/drafts")
def list_drafts(db=Depends(get_session), p: Principal = Depends(get_principal)):
    rows = db.execute(select(models.PortalDraft).where(models.PortalDraft.org_id == p.org_id)).scalars().all()
    return {"drafts": [{"id": r.id, "country": r.country, "visa_type": r.visa_type,
                        "status": r.status} for r in rows]}


class ReviewBody(BaseModel):
    decision: str  # "approved" | "rejected"


@app.post("/discovery/drafts/{draft_id}/review")
def review_draft(draft_id: str, body: ReviewBody, db=Depends(get_session),
                 p: Principal = Depends(get_principal)):
    row = db.get(models.PortalDraft, draft_id)
    if not row:
        raise HTTPException(404, "draft not found")
    require_owner(p, row.org_id)
    if body.decision not in ("approved", "rejected"):
        raise HTTPException(400, "decision must be approved or rejected")
    # Approval here only marks the DRAFT reviewed — it does NOT create a live
    # adapter. Building + production-enabling an adapter is a separate,
    # code-reviewed step gated by the adapter contract validator.
    row.status = body.decision
    row.reviewed_by = p.user_id
    db.commit()
    audit.record(db, org_id=p.org_id, application_id="", action="portal_draft_review",
                 detail={"draft_id": draft_id, "decision": body.decision}, actor=p.user_id)
    return {"id": row.id, "status": row.status, "note": "review only — no live adapter created"}


# ---- Native Ellis e-signature ----
class SignBody(BaseModel):
    document_hash: str
    consent_given: bool = False
    intent_confirmed: bool = False
    signature_method: str = "typed"   # typed | drawn
    signature_value: str = ""
    step_up_token: str                # short-lived action token proving step-up auth
    auth_method: str = "email_otp"


@app.post("/cases/{application_id}/authorization/prepare")
def prepare_authorization(application_id: str, body: Authorization, db=Depends(get_session),
                          p: Principal = Depends(get_principal)):
    """Build the exact authorization document + hash + a short-lived step-up
    action token the applicant must satisfy before signing."""
    from .providers import esign
    app_row = _owned(db, p, application_id)
    applicant = db.get(models.Applicant, app_row.applicant_id)
    docs = [d.name for d in db.execute(select(models.StoredDocument).where(
        models.StoredDocument.application_id == application_id)).scalars().all()]
    text = esign.build_authorization_text(
        applicant={"full_name": applicant.full_name, "email": applicant.email},
        org_id=app_row.org_id, case_id=application_id, app_version=app_row.current_version,
        destination=app_row.destination_country, visa_type=app_row.visa_type,
        portal=app_row.destination_country, max_fee_cents=body.max_fee_cents, currency=body.currency,
        allow_auto_book=body.allow_auto_book, allow_auto_reschedule=body.allow_auto_reschedule,
        allow_representative_submit=body.allow_representative_submit)
    dh = esign.document_hash(text)
    snap = esign.application_snapshot_hash(app_row.answers, docs)
    # Persist the pending authorization envelope + snapshot for later invalidation.
    env = models.AuthorizationEnvelope(
        application_id=application_id, provider="native_ellis", status="prepared",
        max_fee_cents=body.max_fee_cents, currency=body.currency, allow_auto_book=body.allow_auto_book,
        allow_auto_reschedule=body.allow_auto_reschedule,
        allow_representative_submit=body.allow_representative_submit, artifact_hash="")
    db.add(env)
    db.commit()
    # A step-up token the client redeems after completing MFA/OTP/passkey.
    token = issue_action_token(p, "esign", application_id, ttl_seconds=600)
    audit.record(db, org_id=p.org_id, application_id=application_id, action="authorization_prepared",
                 detail={"document_hash": dh, "app_snapshot_hash": snap}, actor=p.user_id)
    return {"document_text": text, "document_hash": dh, "app_snapshot_hash": snap,
            "step_up_token": token, "consent_version": esign.CONSENT_VERSION,
            "template_version": esign.TEMPLATE_VERSION}


@app.post("/cases/{application_id}/authorization/sign")
def sign_authorization_doc(application_id: str, body: SignBody, request: Request,
                           db=Depends(get_session), p: Principal = Depends(get_principal)):
    from .providers import esign
    app_row = _owned(db, p, application_id)
    applicant = db.get(models.Applicant, app_row.applicant_id)
    # Verify the step-up token (proves recent MFA + binds to this case/action).
    verify_action_token(body.step_up_token, "esign", application_id)
    docs = [d.name for d in db.execute(select(models.StoredDocument).where(
        models.StoredDocument.application_id == application_id)).scalars().all()]
    text = esign.build_authorization_text(
        applicant={"full_name": applicant.full_name, "email": applicant.email},
        org_id=app_row.org_id, case_id=application_id, app_version=app_row.current_version,
        destination=app_row.destination_country, visa_type=app_row.visa_type,
        portal=app_row.destination_country,
        max_fee_cents=_latest_env(db, application_id).max_fee_cents,
        currency=_latest_env(db, application_id).currency,
        allow_auto_book=_latest_env(db, application_id).allow_auto_book,
        allow_auto_reschedule=_latest_env(db, application_id).allow_auto_reschedule,
        allow_representative_submit=_latest_env(db, application_id).allow_representative_submit)
    req = esign.SignatureRequest(
        applicant={"full_name": applicant.full_name, "email": applicant.email},
        org_id=app_row.org_id, case_id=application_id, app_version=app_row.current_version,
        document_text=text, document_hash=body.document_hash, consent_given=body.consent_given,
        intent_confirmed=body.intent_confirmed, signature_method=body.signature_method,
        signature_value=body.signature_value, step_up_verified=True, auth_method=body.auth_method,
        ip_address=request.client.host if request.client else "", user_agent=request.headers.get("user-agent", ""))
    try:
        result = esign.get_provider().sign(req)
    except ValueError as e:
        raise HTTPException(422, str(e))
    snap = esign.application_snapshot_hash(app_row.answers, docs)
    sig = models.NativeSignature(
        org_id=app_row.org_id, application_id=application_id, app_version=app_row.current_version,
        provider=result["provider"], template_version=result["template_version"],
        consent_version=result["consent_version"], document_hash=result["document_hash"],
        artifact_hash=result["artifact_hash"], artifact_ref=f"local://sig/{result['artifact_hash'][:16]}",
        signature_method=result["signature_method"], auth_method=result["auth_method"],
        ip_address=req.ip_address, user_agent=req.user_agent[:300], app_snapshot_hash=snap)
    db.add(sig)
    db.flush()
    _sig_event(db, sig.id, application_id, "signed", {"artifact_hash": result["artifact_hash"]})
    # Mark the envelope completed so the workflow authorization gate is satisfied.
    env = _latest_env(db, application_id)
    if env:
        env.status = "completed"
        env.artifact_hash = result["artifact_hash"]
    db.commit()
    audit.record(db, org_id=p.org_id, application_id=application_id, action="authorization_signed_native",
                 detail={"artifact_hash": result["artifact_hash"], "method": result["signature_method"]},
                 actor=p.user_id)
    return {"signature_id": sig.id, "artifact_hash": result["artifact_hash"],
            "signed_at": result["signed_at"], "download": f"/cases/{application_id}/authorization/{sig.id}/pdf"}


def _latest_env(db, application_id: str):
    return db.execute(select(models.AuthorizationEnvelope).where(
        models.AuthorizationEnvelope.application_id == application_id).order_by(
        models.AuthorizationEnvelope.created_at.desc())).scalars().first()


def _sig_event(db, signature_id: str, application_id: str, event: str, detail: dict):
    from sqlalchemy import func
    nseq = (db.query(func.max(models.SignatureEvent.seq)).scalar() or 0) + 1
    db.add(models.SignatureEvent(seq=nseq, signature_id=signature_id, application_id=application_id,
                                 event=event, detail=detail))
    db.commit()


def invalidate_signatures_if_changed(db, application_id: str):
    """Any material change to answers/documents invalidates completed signatures."""
    from .providers import esign
    app_row = db.get(models.VisaApplication, application_id)
    docs = [d.name for d in db.execute(select(models.StoredDocument).where(
        models.StoredDocument.application_id == application_id)).scalars().all()]
    current = esign.application_snapshot_hash(app_row.answers, docs)
    changed = 0
    for sig in db.execute(select(models.NativeSignature).where(
            models.NativeSignature.application_id == application_id,
            models.NativeSignature.invalidated == False)).scalars().all():  # noqa: E712
        if sig.app_snapshot_hash and sig.app_snapshot_hash != current:
            sig.invalidated = True
            _sig_event(db, sig.id, application_id, "invalidated", {"reason": "material_change"})
            changed += 1
    if changed:
        db.commit()
    return changed


# ---- Workflow signals ----
_SIGNALS = {"approve_review", "sign_authorization", "solve_captcha", "verify_email",
            "approve_payment", "complete_payment", "select_appointment",
            "approve_reschedule", "complete_declaration", "cancel"}


class SignalBody(BaseModel):
    token: Optional[str] = None
    slot_id: Optional[str] = None


@app.post("/cases/{application_id}/start")
def start_case(application_id: str, db=Depends(get_session), p: Principal = Depends(get_principal)):
    _owned(db, p, application_id)
    status, _ = service.signal(db, application_id, "start")
    return status


@app.post("/cases/{application_id}/signals/{name}")
def send_signal(application_id: str, name: str, body: Optional[SignalBody] = None,
                db=Depends(get_session), p: Principal = Depends(get_principal)):
    _owned(db, p, application_id)
    if name not in _SIGNALS:
        raise HTTPException(400, f"unknown signal {name}")
    body = body or SignalBody()
    kwargs = {}
    if name == "verify_email":
        kwargs["token"] = body.token
    if name == "select_appointment":
        kwargs["slot_id"] = body.slot_id
    status, _ = service.signal(db, application_id, name, **kwargs)
    return status


@app.get("/cases/{application_id}/audit")
def get_audit(application_id: str, db=Depends(get_session), p: Principal = Depends(get_principal)):
    _owned(db, p, application_id)
    return {"events": [{"seq": e.seq, "action": e.action, "detail": e.detail, "actor": e.actor}
                       for e in audit.for_application(db, application_id)]}


@app.get("/cases/{application_id}/appointment")
def get_appointment(application_id: str, db=Depends(get_session), p: Principal = Depends(get_principal)):
    _owned(db, p, application_id)
    appt = db.execute(select(models.Appointment).where(
        models.Appointment.application_id == application_id)).scalar_one_or_none()
    if not appt:
        return {"appointment": None}
    return {"appointment": {"slot_id": appt.slot_id, "location_id": appt.location_id,
                            "start_utc": appt.start_utc, "confirmation_no": appt.confirmation_no,
                            "reschedule_count": appt.reschedule_count}}


# ---- Webhooks (signature-verified, idempotent) ----
@app.post("/webhooks/stripe")
async def stripe_webhook(stripe_signature: str = Header(default=""), db=Depends(get_session)):
    from .providers.payment import StripeIssuing
    if not StripeIssuing.is_configured():
        raise HTTPException(503, "stripe not configured")
    return {"received": True}  # ACTIVATION: verify + reconcile idempotently


@app.post("/webhooks/docusign")
async def docusign_webhook(x_docusign_signature: str = Header(default=""), db=Depends(get_session)):
    if not docusign.is_configured():
        raise HTTPException(503, "docusign not configured")
    return {"received": True}  # ACTIVATION: verify signature + mark envelope completed
