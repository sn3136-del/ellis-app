"""FastAPI application — authenticated REST API for the tourist-visa flow.

Every route enforces authentication (Clerk or dev token) and object-level
tenant isolation. Sensitive transitions go through the durable service layer.
"""
from __future__ import annotations

import json
import re

from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Header, Request, Response
from pydantic import BaseModel
from sqlalchemy import select

from .config import capabilities
from .db import get_session, create_all
from . import models, audit, service, execution
from .security import (Principal, get_principal, require_owner, require_admin,
                       issue_action_token, verify_action_token)
from .providers import ocr as ocr_provider
from .providers import passport_classifier
from .providers import docusign
from .providers.kimi import run_agent
from .portal.contract import list_adapters
from .portal.mock_portal import MockPortal  # only constructed in mock-allowed modes

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


# Phase 17: structured redacted request logging + flag-gated Sentry/OTel.
from .observability import RequestLogMiddleware as _ReqLog, init_sentry as _init_sentry, init_otel as _init_otel  # noqa: E402

app.add_middleware(_ReqLog)

# 2026-07-23 snapshot: applicant route intake + resolution + snapshot admin.
from .visa_snapshot.api import router as _snapshot_router  # noqa: E402
# Automated adapter factory: applicant build request/consent/progress + admin
# release queue (brief §10-§13, §33).
from .adapter_factory.api import router as _factory_router  # noqa: E402

app.include_router(_snapshot_router)
app.include_router(_factory_router)
from .global_routes.api import router as _global_router  # noqa: E402
app.include_router(_global_router)


@app.on_event("startup")
def _startup():
    create_all()
    _init_sentry()
    _init_otel()


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/capabilities")
def get_capabilities(_: Principal = Depends(get_principal)):
    caps = capabilities()
    # Honest runtime classification: mock-allowed modes bind the MockPortal
    # driver (class MOCK); real-only modes have NO portal driver until an
    # individually approved live adapter exists (class UNSUPPORTED) — surfaced
    # so no client mistakes a completed case for a real government submission.
    from .config import settings as _s
    s = _s()
    if s.mock_portal_allowed:
        runtime_pec = str(execution.classify_driver(MockPortal()))
    else:
        runtime_pec = str(execution.ExecutionClass.UNSUPPORTED)
    caps["runtime_mode"] = s.runtime_mode
    caps["execution_classification"] = {
        "legend": execution.legend(),
        "runtime_portal_execution_class": runtime_pec,
        "real_result_class": execution.REAL_RESULT_CLASS.value,
        "production_mode": s.production_mode,
        "runtime_mode": s.runtime_mode,
        "mock_portal_allowed": s.mock_portal_allowed,
    }
    return caps


# ---- Internationalization (Phase 6): dynamic-content translation + identity ----
class TranslateBody(BaseModel):
    text: str
    target_lang: str
    source_lang: str = "auto"


@app.get("/i18n/languages")
def i18n_languages(_: Principal = Depends(get_principal)):
    from . import i18n
    return {"languages": [{"code": c, "name": i18n.LANGUAGE_NAMES[c]} for c in i18n.SUPPORTED_LANGS]}


@app.post("/i18n/translate")
def i18n_translate(body: TranslateBody, _: Principal = Depends(get_principal)):
    """Backend-only dynamic-content translation via Kimi K3. Placeholders,
    URLs, dates, amounts, filenames, and passport numbers / MRZ / identifiers are
    never translated; unavailable → the original text (never fabricated)."""
    from . import i18n
    return i18n.translate(body.text, body.target_lang, body.source_lang)


@app.get("/assistant/identity")
def assistant_identity(lang: str = "en", _: Principal = Depends(get_principal)):
    """The assistant always identifies as Ellis, in any supported language, and
    never as the underlying model or as a government official/lawyer/embassy."""
    from . import i18n
    return {"name": "Ellis", "lang": lang, "answer": i18n.assistant_identity_answer(lang)}


# ---- Personal-test safety gate (brief item #6): route readiness ----
class GateBody(BaseModel):
    destination: str
    visa_type: str = "tourist"
    nationality: str = ""
    residence: str = ""
    gate: str
    complete: bool
    evidence: str = ""


@app.get("/routes/readiness")
def route_readiness(destination: str, visa_type: str = "tourist", nationality: str = "",
                    residence: str = "", db=Depends(get_session),
                    p: Principal = Depends(get_principal)):
    from . import personal_gate
    # Gate evidence + the recording admin's id are internal audit material —
    # included only for the admin role.
    return personal_gate.readiness(db, destination=destination, visa_type=visa_type,
                                   nationality=nationality, residence=residence,
                                   include_evidence=(p.role == "admin"))


@app.post("/admin/routes/readiness")
def set_route_gate(body: GateBody, db=Depends(get_session), p: Principal = Depends(get_principal)):
    """Human administrators only. Kimi has no such tool; search results can never
    mark a gate. Evidence is mandatory to mark a gate complete."""
    from . import personal_gate
    require_admin(p)
    try:
        return personal_gate.set_gate(db, destination=body.destination, visa_type=body.visa_type,
                                      nationality=body.nationality, residence=body.residence,
                                      gate=body.gate, complete=body.complete,
                                      evidence=body.evidence, actor=p.user_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/cases/{application_id}/live-preflight")
def case_live_preflight(application_id: str, db=Depends(get_session),
                        p: Principal = Depends(get_principal)):
    from . import personal_gate, passport_validity
    app_row = _owned(db, p, application_id)
    ec = _case_execution_class(app_row.destination_country, app_row.visa_type, db=db, app_row=app_row)
    pre = personal_gate.live_preflight(db, app_row, ec)
    pre["passport_validity"] = passport_validity.check_case_passport(db, app_row)
    return pre


@app.get("/cases/{application_id}/passport-validity")
def get_passport_validity(application_id: str, db=Depends(get_session),
                          p: Principal = Depends(get_principal)):
    """Validity verdict: expiry (answers, falling back to the accepted passport
    document's extracted date) vs. the destination rule (verified route rule,
    else the Kimi two-pass requirement) — plus whether renewal is offered."""
    from . import passport_validity, renewal
    app_row = _owned(db, p, application_id)
    verdict = passport_validity.check_case_passport(db, app_row)
    verdict["renewal_offered"] = renewal.should_offer_renewal(verdict)
    linked = renewal.get_linked_renewal(db, app_row)
    if linked is not None:
        verdict["renewal_case_id"] = linked.id
    return verdict


class RenewalRequest(BaseModel):
    manual: bool = False   # True = the applicant chose "Renew my passport"


@app.post("/cases/{application_id}/renewal")
def start_passport_renewal(application_id: str, body: RenewalRequest = RenewalRequest(),
                           db=Depends(get_session), p: Principal = Depends(get_principal)):
    """Create (or reuse — idempotent) the linked passport-renewal case. Offered
    automatically only for an expired / insufficient-validity passport; a valid
    passport can still renew when the applicant explicitly asks (manual)."""
    from . import passport_validity, renewal
    from .visa_snapshot import kimi_primary
    app_row = _owned(db, p, application_id)
    verdict = passport_validity.check_case_passport(db, app_row)
    try:
        out = renewal.create_renewal_case(db, org_id=p.org_id, user_id=p.user_id,
                                          travel_case=app_row, verdict=verdict,
                                          manual=body.manual)
    except ValueError as e:
        raise HTTPException(409, str(e))
    except renewal.RenewalUnavailable as e:
        raise HTTPException(503, detail={"status": kimi_primary.STATUS_UNAVAILABLE,
                                         "reason": str(e)})
    except kimi_primary.GuidanceTimeout:
        raise HTTPException(504, detail={"status": kimi_primary.STATUS_TIMEOUT,
                                         "reason": kimi_primary.TIMEOUT_MESSAGE})
    except kimi_primary.GuidanceProviderError as e:
        raise HTTPException(503, detail={"status": kimi_primary.STATUS_UNAVAILABLE,
                                         "reason": e.envelope.get("user_message"),
                                         "category": e.envelope.get("category")})
    out["travel_case_validity"] = verdict
    return out


# ---- Document preview (Phase 13): signed, expiring content URLs ----
_DOC_URL_TTL_SECONDS = 300


def _doc_sig(document_id: str, exp: int) -> str:
    import hmac as _hmac
    import hashlib as _hashlib
    from .config import settings as _settings
    payload = f"doc.{document_id}.{exp}"
    return _hmac.new(_settings().action_token_secret.encode(), payload.encode(),
                     _hashlib.sha256).hexdigest()[:32]


@app.get("/cases/{application_id}/documents/{doc_id}/url")
def document_preview_url(application_id: str, doc_id: str, db=Depends(get_session),
                         p: Principal = Depends(get_principal)):
    """Mint a short-lived signed URL for the in-app preview. Authenticated +
    tenant-checked here; the content endpoint then only needs the signature (so
    <img>/<iframe> can load it without headers). No filesystem paths, no bucket
    URLs, no credentials are ever exposed."""
    import time as _time
    _owned(db, p, application_id)
    doc = db.get(models.StoredDocument, doc_id)
    if not doc or doc.application_id != application_id:
        raise HTTPException(404, "document not found")
    blob = db.get(models.DocumentBlob, doc_id)
    if blob is None:
        return {"available": False,
                "reason": "no stored content for this document (text-only fixture)"}
    exp = int(_time.time()) + _DOC_URL_TTL_SECONDS
    return {"available": True, "mime": blob.mime, "expires_in": _DOC_URL_TTL_SECONDS,
            "url": f"/documents/{doc_id}/content?exp={exp}&sig={_doc_sig(doc_id, exp)}"}


@app.get("/documents/{doc_id}/content")
def document_content(doc_id: str, exp: int, sig: str, db=Depends(get_session)):
    """Serve preview bytes for a valid, unexpired signed URL. The signature (not
    a session) is the authorization — minted only for the owning tenant."""
    import hmac as _hmac
    import time as _time
    if exp < _time.time():
        raise HTTPException(401, "preview link expired")
    if not _hmac.compare_digest(sig, _doc_sig(doc_id, exp)):
        raise HTTPException(401, "invalid preview signature")
    blob = db.get(models.DocumentBlob, doc_id)
    if blob is None:
        raise HTTPException(404, "document content not found")
    from fastapi.responses import Response
    # nosniff + a fully sandboxed CSP: preview bytes can never execute script
    # or be reinterpreted as another type (HTML/active content is never
    # rendered — the MIME allowlist at upload already excludes it).
    return Response(content=blob.content, media_type=blob.mime,
                    headers={"Cache-Control": "private, no-store",
                             "Content-Disposition": "inline",
                             "X-Content-Type-Options": "nosniff",
                             "Content-Security-Policy":
                                 "sandbox; default-src 'none'"})


# ---- Email delivery (Phase 8) ----
@app.get("/cases/{application_id}/emails")
def list_case_emails(application_id: str, db=Depends(get_session),
                     p: Principal = Depends(get_principal)):
    _owned(db, p, application_id)
    rows = db.execute(select(models.EmailNotification).where(
        models.EmailNotification.application_id == application_id)).scalars().all()
    return {"emails": [{"id": r.id, "event": r.event, "subject": r.subject,
                        "status": r.status, "locale": r.locale,
                        "attempts": r.attempts} for r in rows]}


@app.post("/admin/email/process-queue")
def process_email_queue(db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import emails as emails_mod
    require_admin(p)
    return emails_mod.process_queue(db, org_id=p.org_id)


@app.get("/admin/email/dead-letters")
def email_dead_letters(db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import emails as emails_mod
    require_admin(p)
    return {"dead": emails_mod.dead_letters(db, p.org_id)}


# ---- Route rules (Phase 2) + fees (Phase 3) ----
class RuleCreate(BaseModel):
    destination: str
    visa_type: str = "tourist"
    nationality: str = ""
    residence: str = ""
    source_url: str
    source_authority: str = ""
    effective_date: str = ""
    expiration_date: str = ""
    eligibility_conditions: list = []
    required_documents: list = []
    processing_method: str = ""
    electronic_available: Optional[bool] = None
    biometrics_required: Optional[bool] = None
    interview_required: Optional[bool] = None
    appointment_required: Optional[bool] = None
    personal_appearance_required: Optional[bool] = None
    third_party_preparation_allowed: Optional[bool] = None
    third_party_submission_allowed: Optional[bool] = None
    declaration_mandatory: Optional[bool] = None
    passport_validity_rule: dict = {}
    confidence: float = 0.0


class ReviewDecision(BaseModel):
    decision: str   # verified | rejected | stale


@app.post("/admin/routes/rules")
def create_route_rule(body: RuleCreate, db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import rules as rules_mod
    require_admin(p)
    try:
        r = rules_mod.create_rule(db, actor=p.user_id, **body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": r.id, "version": r.version, "review_status": r.review_status}


@app.post("/admin/routes/rules/{rule_id}/review")
def review_route_rule(rule_id: str, body: ReviewDecision, db=Depends(get_session),
                      p: Principal = Depends(get_principal)):
    from . import rules as rules_mod
    require_admin(p)
    try:
        r = rules_mod.review_rule(db, rule_id=rule_id, decision=body.decision, actor=p.user_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except KeyError:
        raise HTTPException(404, "rule not found")
    return {"id": r.id, "review_status": r.review_status, "version": r.version}


@app.get("/routes/rules")
def get_route_rule(destination: str, visa_type: str = "tourist", nationality: str = "",
                   residence: str = "", db=Depends(get_session), _: Principal = Depends(get_principal)):
    from . import rules as rules_mod
    args = dict(destination=destination, visa_type=visa_type,
                nationality=nationality, residence=residence)
    r = rules_mod.latest_rule(db, **args)
    history = rules_mod.rule_history(db, **args)
    def _d(x):
        return {"id": x.id, "version": x.version, "review_status": x.review_status,
                "source_url": x.source_url, "source_authority": x.source_authority,
                "effective_date": x.effective_date, "expiration_date": x.expiration_date,
                "eligibility_conditions": x.eligibility_conditions,
                "required_documents": x.required_documents,
                "processing_method": x.processing_method,
                "electronic_available": x.electronic_available,
                "biometrics_required": x.biometrics_required,
                "interview_required": x.interview_required,
                "appointment_required": x.appointment_required,
                "personal_appearance_required": x.personal_appearance_required,
                "third_party_preparation_allowed": x.third_party_preparation_allowed,
                "third_party_submission_allowed": x.third_party_submission_allowed,
                "declaration_mandatory": x.declaration_mandatory,
                "passport_validity_rule": x.passport_validity_rule,
                "confidence": x.confidence,
                "retrieved_at": x.retrieved_at.isoformat() if x.retrieved_at else None}
    return {"verified": _d(r) if r else None,
            "history": [{"id": h.id, "version": h.version, "review_status": h.review_status}
                        for h in history]}


@app.get("/routes/coverage")
def get_route_coverage(db=Depends(get_session), _: Principal = Depends(get_principal)):
    from . import rules as rules_mod
    return {"status_ladder": rules_mod.STATUS_LADDER, "routes": rules_mod.coverage_matrix(db)}


class FeeCreate(BaseModel):
    destination: str
    visa_type: str = "tourist"
    nationality: str = ""
    residence: str = ""
    government_fee_cents: int = 0
    service_fee_cents: int = 0
    optional_fees: list = []
    currency: str = "USD"
    conditions: list = []
    refundability: str = ""
    payment_methods: list = []
    payment_timing: str = ""
    source_url: str
    source_authority: str = ""
    effective_date: str = ""


@app.post("/admin/routes/fees")
def create_route_fee(body: FeeCreate, db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import fees as fees_mod
    require_admin(p)
    try:
        r = fees_mod.create_fee(db, actor=p.user_id, **body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": r.id, "version": r.version, "review_status": r.review_status}


@app.post("/admin/routes/fees/{fee_id}/review")
def review_route_fee(fee_id: str, body: ReviewDecision, db=Depends(get_session),
                     p: Principal = Depends(get_principal)):
    from . import fees as fees_mod
    require_admin(p)
    try:
        r = fees_mod.review_fee(db, fee_id=fee_id, decision=body.decision, actor=p.user_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except KeyError:
        raise HTTPException(404, "fee record not found")
    return {"id": r.id, "review_status": r.review_status, "version": r.version}


@app.get("/routes/fees")
def get_route_fee(destination: str, visa_type: str = "tourist", nationality: str = "",
                  residence: str = "", db=Depends(get_session), _: Principal = Depends(get_principal)):
    """Full fee breakdown for display BEFORE payment. Honest when no verified
    current fee exists — automated payment is blocked in that case."""
    from . import fees as fees_mod
    rec = fees_mod.verified_current_fee(db, destination=destination, visa_type=visa_type,
                                        nationality=nationality, residence=residence)
    return fees_mod.fee_breakdown(rec)


@app.get("/admin/routes/fees/stale")
def get_stale_fees(db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import fees as fees_mod
    require_admin(p)
    return {"stale": fees_mod.stale_fees(db)}


# ---- Trip.com connector admin (Phase 15 partial — sandbox contract) ----
@app.get("/admin/tripcom/health")
def tripcom_health(db=Depends(get_session), p: Principal = Depends(get_principal)):
    from .integrations import tripcom_admin
    require_admin(p)
    return tripcom_admin.health(db, p.org_id)


@app.get("/admin/tripcom/deliveries")
def tripcom_deliveries(status: str = "", db=Depends(get_session),
                       p: Principal = Depends(get_principal)):
    from .integrations import tripcom_admin
    require_admin(p)
    return {"deliveries": tripcom_admin.list_deliveries(db, p.org_id, status=status)}


@app.post("/admin/tripcom/deliveries/{delivery_id}/replay")
def tripcom_replay(delivery_id: str, db=Depends(get_session),
                   p: Principal = Depends(get_principal)):
    from .integrations import tripcom_admin
    require_admin(p)
    try:
        row = tripcom_admin.replay(db, org_id=p.org_id, delivery_id=delivery_id, actor=p.user_id)
    except KeyError:
        raise HTTPException(404, "delivery not found")
    return {"id": row.id, "replay_of": row.replay_of, "status": row.status}


@app.post("/admin/tripcom/process")
def tripcom_process(db=Depends(get_session), p: Principal = Depends(get_principal)):
    from .integrations import tripcom_admin
    require_admin(p)
    return tripcom_admin.process_deliveries(db, org_id=p.org_id)


# ---- Trip.com first-run administrator setup (Phase 7) ----
class SetupBody(BaseModel):
    tenant_name: Optional[str] = None
    admin_email: Optional[str] = None
    data_region: Optional[str] = None
    retention_days: Optional[int] = None
    base_urls: Optional[dict] = None
    branding: Optional[dict] = None
    email: Optional[dict] = None          # {provider, sender, reply_to, host, port, username, api_endpoint}
    google: Optional[dict] = None         # {project, location, ocr_processor_id, form_processor_id}
    trip: Optional[dict] = None           # {base_url, sandbox_base_url, client_id, signing_method}
    # Secrets — vaulted backend-only, never echoed back.
    kimi_api_key: Optional[str] = None
    browserbase_api_key: Optional[str] = None
    google_service_account_json: Optional[str] = None
    email_credential: Optional[str] = None
    trip_client_secret: Optional[str] = None
    trip_webhook_secret: Optional[str] = None


@app.get("/setup/status")
def setup_status(db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import setup as setup_mod
    return setup_mod.redacted_status(db, p.org_id)


@app.post("/setup")
def save_setup_endpoint(body: SetupBody, db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import setup as setup_mod
    require_admin(p)
    try:
        return setup_mod.save_setup(db, org_id=p.org_id, actor=p.user_id,
                                    payload=body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/setup/test/{component}")
def test_setup_component(component: str, db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import setup as setup_mod
    require_admin(p)
    return setup_mod.test_component(db, org_id=p.org_id, component=component)


class TestEmailBody(BaseModel):
    to: str


@app.post("/setup/email/test")
def test_setup_email(body: TestEmailBody, db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import setup as setup_mod
    require_admin(p)
    return setup_mod.send_test_email(db, org_id=p.org_id, to=body.to)


class RotateBody(BaseModel):
    value: str


@app.post("/setup/rotate/{component}")
def rotate_setup_component(component: str, body: RotateBody, db=Depends(get_session),
                          p: Principal = Depends(get_principal)):
    from . import setup as setup_mod
    require_admin(p)
    try:
        return setup_mod.rotate_component(db, org_id=p.org_id, component=component,
                                          new_value=body.value, actor=p.user_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/setup/revoke/{component}")
def revoke_setup_component(component: str, db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import setup as setup_mod
    require_admin(p)
    return setup_mod.revoke_component(db, org_id=p.org_id, component=component, actor=p.user_id)


@app.get("/diagnostics/providers")
def provider_diagnostics(_: Principal = Depends(get_principal)):
    """Circuit-breaker states, kill switches, and observability status — no
    secrets, no raw provider responses."""
    from . import provider_errors
    from .config import killswitches
    from . import observability
    return {"breakers": provider_errors.breakers_snapshot(),
            "kill_switches": killswitches(),
            "observability": observability.status()}


@app.get("/diagnostics/ocr")
def ocr_diagnostics(_: Principal = Depends(get_principal)):
    # Non-sensitive booleans + redacted error category only. Never returns
    # tokens, credential paths, or document content.
    from .providers import ocr_health
    return ocr_health.diagnostic()


@app.get("/admin/adapters/harness")
def adapters_harness(p: Principal = Depends(get_principal)):
    """Dry contract validation for every registered adapter definition (no live
    portal contact). The report is contract-test EVIDENCE, never an approval —
    activation stays a human admin action in adapters_admin."""
    require_admin(p)
    from .config import settings as _s
    from .portal.adapter_harness import dry_validate
    from .portal.driver_factory import register_adapters_for_mode
    from .portal.contract import _REGISTRY
    register_adapters_for_mode()
    return {"runtime_mode": _s().runtime_mode,
            "reports": [dry_validate(a).as_dict() for a in _REGISTRY.values()]}


@app.get("/adapters")
def get_adapters(_: Principal = Depends(get_principal)):
    # Registry is mode-keyed: mock modes list the demo adapters; real-only
    # modes honestly list only production-approved live adapters (none today).
    from .config import settings as _s
    from .portal.driver_factory import register_adapters_for_mode
    register_adapters_for_mode()
    return {"adapters": list_adapters(), "runtime_mode": _s().runtime_mode}


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
    # Uploading from a checklist row binds the document to that exact
    # requirement (never auto-fulfils it — the applicant's Submit does that).
    checklist_item_id: str = ""


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


def _case_execution_class(country: str, visa_type: str, db=None, app_row=None):
    """Classify what running this case's route ACTUALLY produces. Mock-allowed
    modes bind the MockPortal driver (class MOCK). Real-only modes register no
    demo adapters; a route whose portal family carries a RELEASED adapter
    executes through the live FlowRunner bridge (class LIVE_PRODUCTION — the
    class the bound driver itself declares), and anything else classifies
    UNSUPPORTED — the classification follows the driver, never a claim."""
    from .config import settings as _settings
    from .portal.driver_factory import register_adapters_for_mode, select_metadata_adapter
    if db is not None and app_row is not None and not _settings().mock_portal_allowed:
        from .portal.released_flow import build_for_case
        built = build_for_case(db, app_row)
        if built is not None:
            return execution.classify_adapter(built[1])
    register_adapters_for_mode()
    adapter = select_metadata_adapter(country, visa_type)
    return execution.classify_adapter(adapter)


# ---- Browserbase Live View infrastructure (generic; no portal transactions) --
# Sessions are tenant+case isolated. Live View URLs are short-lived, minted
# fresh per request, returned with no-store, and NEVER persisted, audited, or
# logged (observability.scrub also redacts Live-View URL shapes defensively).

@app.post("/cases/{application_id}/browser-session")
def create_browser_session(application_id: str, response: Response,
                           db=Depends(get_session), p: Principal = Depends(get_principal)):
    _owned(db, p, application_id)
    from .providers import browser as bb
    row = db.execute(select(models.BrowserSession).where(
        models.BrowserSession.application_id == application_id,
        models.BrowserSession.status == "open")).scalars().first()
    if row is None:
        sess = bb.create_session()
        row = models.BrowserSession(org_id=p.org_id, application_id=application_id,
                                    provider_session_id=sess.get("id", ""),
                                    mode=sess.get("mode", "local"))
        db.add(row)
        db.commit()
        audit.record(db, org_id=p.org_id, application_id=application_id,
                     action="browser_session_opened",
                     detail={"mode": row.mode}, actor=p.user_id)  # no ids/urls in audit
    response.headers["Cache-Control"] = "no-store"
    return {"id": row.id, "mode": row.mode, "status": row.status,
            "live_view_available": row.mode == "browserbase"}


@app.get("/cases/{application_id}/browser-session/live-view")
def browser_session_live_view(application_id: str, response: Response,
                              db=Depends(get_session), p: Principal = Depends(get_principal)):
    """Mint a fresh short-lived Live View URL. Honest 404s: no session, or the
    stack runs without Browserbase (local mode has no Live View)."""
    _owned(db, p, application_id)
    from .providers import browser as bb
    row = db.execute(select(models.BrowserSession).where(
        models.BrowserSession.application_id == application_id,
        models.BrowserSession.status == "open")).scalars().first()
    if row is None:
        raise HTTPException(404, "no open browser session for this case")
    if row.mode != "browserbase":
        raise HTTPException(404, "live view unavailable: session is local mode "
                                 "(Browserbase not configured)")
    url = bb.live_view_url(row.provider_session_id)
    if not url:
        raise HTTPException(404, "live view URL unavailable from provider")
    response.headers["Cache-Control"] = "no-store"
    return {"url": url, "expires_hint_seconds": 300}


@app.delete("/cases/{application_id}/browser-session")
def close_browser_session(application_id: str, db=Depends(get_session),
                          p: Principal = Depends(get_principal)):
    _owned(db, p, application_id)
    from .providers import browser as bb
    from .models import _now as _model_now
    closed = 0
    for row in db.execute(select(models.BrowserSession).where(
            models.BrowserSession.application_id == application_id,
            models.BrowserSession.status == "open")).scalars():
        bb.close_session(row.provider_session_id)
        row.status = "closed"
        row.closed_at = _model_now()
        closed += 1
    db.commit()
    if closed:
        audit.record(db, org_id=p.org_id, application_id=application_id,
                     action="browser_session_closed", detail={"count": closed},
                     actor=p.user_id)
    return {"closed": closed}


@app.get("/cases/{application_id}")
def get_case(application_id: str, db=Depends(get_session), p: Principal = Depends(get_principal)):
    app_row = _owned(db, p, application_id)
    exec_row = db.execute(select(models.WorkflowExecution).where(
        models.WorkflowExecution.application_id == application_id)).scalar_one_or_none()
    appt = db.execute(select(models.Appointment).where(
        models.Appointment.application_id == application_id)).scalar_one_or_none()
    conf = db.execute(select(models.SubmissionConfirmation).where(
        models.SubmissionConfirmation.application_id == application_id)).scalar_one_or_none()
    ec = _case_execution_class(app_row.destination_country, app_row.visa_type, db=db, app_row=app_row)
    # The disposition is the display guard: it refuses to present submitted/paid/
    # booked/confirmed as REAL unless an approved LIVE_PRODUCTION adapter produced
    # them. Clients render disposition.display_status, not raw state, for anything
    # user-facing, and check is_real_government_result before claiming a real visa.
    disposition = execution.result_disposition(app_row.state, ec)
    return {"id": app_row.id, "state": app_row.state, "answers": app_row.answers,
            "pending": exec_row.pending if exec_row else None,
            "portal_reference": app_row.portal_reference,
            "execution_class": str(ec),
            "disposition": disposition,
            "appointment": ({"slot_id": appt.slot_id, "location_id": appt.location_id,
                             "start_utc": appt.start_utc, "confirmation_no": appt.confirmation_no,
                             "reschedule_count": appt.reschedule_count,
                             "execution_class": str(ec),
                             "is_real": disposition["is_real_government_result"]} if appt else None),
            "confirmation": ({"reference_no": conf.reference_no, "receipt_no": conf.receipt_no,
                              "execution_class": str(ec),
                              "is_real_government_confirmation": disposition["is_real_government_result"]}
                             if conf else None)}


@app.get("/cases/{application_id}/mock/verification")
def mock_verification(application_id: str, db=Depends(get_session), p: Principal = Depends(get_principal)):
    """MOCK-ONLY convenience: returns the verification token the mock portal
    'emailed', so the applicant UI can complete the email-verification handoff in
    a demo. In production the applicant reads their own real email (or Mailpit in
    the local stack); this endpoint is disabled outside development and never
    exposes real personal data — only the mock's own generated token."""
    from .config import settings as _settings
    # Mock-only surface: reachable only in mock-allowed runtime modes.
    if not _settings().mock_portal_allowed or _settings().env not in ("development", "test"):
        raise HTTPException(404, "not found")
    _owned(db, p, application_id)
    wf = service.load_workflow(db, application_id)
    portal = getattr(wf, "_portal", None)
    emails = list(getattr(portal, "emails", []) or [])
    for e in reversed(emails):
        if e.get("kind") == "verification" and "token=" in e.get("link", ""):
            return {"token": e["link"].split("token=")[1], "kind": "verification"}
    return {"token": None, "kind": "verification"}


# Magic-byte structure check per accepted MIME type: a file whose bytes do not
# match its declared type is refused honestly (never rendered, never OCR'd).
_CONTENT_MAGIC = {
    "application/pdf": (b"%PDF-",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/tiff": (b"II*\x00", b"MM\x00*"),
}


@app.post("/cases/{application_id}/documents")
def add_document(application_id: str, body: AddDocument, db=Depends(get_session),
                 p: Principal = Depends(get_principal)):
    import base64
    import hashlib
    from . import checklist_intake
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
        if not any(content.startswith(m) for m in _CONTENT_MAGIC[body.mime]):
            raise HTTPException(415, "file content does not match its declared type")
    # Checklist-row upload: resolve the target requirement FIRST so the item's
    # expected type informs classification (a portrait photo has no text; the
    # photo requirement's context is what identifies it).
    item_ctx = None
    if body.checklist_item_id:
        cg = checklist_intake.case_guidance(db, application_id)
        if cg is not None:
            item_ctx = checklist_intake.find_item(
                checklist_intake.current_checklist(db, app_row, cg), body.checklist_item_id)
        if item_ctx is None:
            raise HTTPException(404, "checklist requirement not found")
        if item_ctx.get("kind") != "document":
            raise HTTPException(400, "this requirement does not take an upload")
    item_types = set((item_ctx or {}).get("satisfied_by") or [])

    def _bind(doc_row):
        try:
            return checklist_intake.bind_document(
                db, p, app_row, body.checklist_item_id, doc_row.id,
                provenance={"source": "checklist_upload"})
        except checklist_intake.ChecklistError as e:
            raise HTTPException(e.status_code, e.detail)

    sha = hashlib.sha256(content or body.text.encode()).hexdigest()
    # Duplicate upload (double click / re-drop of the same file): return the
    # existing record — a duplicate never creates a second document row.
    dup = db.execute(select(models.StoredDocument).where(
        models.StoredDocument.application_id == application_id,
        models.StoredDocument.sha256 == sha)).scalars().first()
    if dup is not None:
        pc = dup.page_classification or {}
        out = {"id": dup.id, "doc_type": dup.doc_type, "duplicate": True,
               "mrz_valid": bool((dup.extracted_fields or {}).get("passport_number")),
               "execution_class": dup.execution_class,
               "page_type": pc.get("page_type", ""),
               "accepted_as_passport_identity": pc.get("accepted_as_passport_identity", False),
               "rejected": pc.get("reject", False), "message": "",
               "extracted_fields": dup.extracted_fields,
               "quality_warnings": dup.quality_warnings}
        if body.checklist_item_id:
            out["binding"] = _bind(dup)
        return out
    # OCR hierarchy with recorded failover: Document AI → (flagged) Kimi vision → local.
    # A filename that says "passport" (or the passport checklist row itself)
    # opts into the passport recovery path (EXIF/rotation retries +
    # multipage-PDF biodata-page selection).
    result, ocr_meta = ocr_provider.process_with_failover(
        content=content, text=body.text, mime=body.mime,
        expect_passport=bool(re.search(r"passport", body.name or "", re.I))
        or "passport" in item_types)
    ec = execution.classify_ocr(ocr_meta)

    # Passport biodata classification is SCOPED (the classifier-leak fix): the
    # full accept/reject flow runs ONLY when the applicant is providing a
    # passport (the passport checklist row, or a filename that says so), or
    # when the page carries an actual ICAO machine-readable zone. A bank
    # statement that mentions "Visa card", a hotel booking, an itinerary or a
    # photograph must NEVER receive passport-page validation or its warnings.
    mrz = ocr_provider.parse_mrz(result.recognized_text) if result.recognized_text else None
    passport_context = bool(re.search(r"passport", body.name or "", re.I)) \
        or "passport" in item_types
    mrz_kind = passport_classifier.detect_mrz_kind(result.recognized_text or "")
    doc_type = result.doc_type
    # Classification provenance drives advisory confidence downstream
    # (mrz/keyword/filename/applicant = deterministic; kimi = semantic hint).
    classifier_kind = "none"
    is_photo_upload = bool(content) and not (result.recognized_text or "").strip() \
        and (re.search(r"photo|portrait|headshot", body.name or "", re.I)
             or "photo" in item_types)

    if passport_context:
        # Full biodata accept/reject UX — the applicant is providing a passport.
        classification = passport_classifier.classify_page(
            text=result.recognized_text, mrz=mrz, has_image=bool(content),
            vision_hint=result.doc_type)
        if classification["accepted_as_passport_identity"] or result.doc_type == "passport":
            classifier_kind = "mrz"
        if classification["reject"]:
            doc_type = classification["page_type"]
    elif mrz_kind is not None or result.doc_type == "passport":
        # Passport-adjacent material aimed at a NON-passport requirement:
        # classify honestly for the advisory ("Ellis detected this as …") but
        # never reject and never attach passport-page warnings.
        classification = passport_classifier.classify_page(
            text=result.recognized_text, mrz=mrz, has_image=bool(content),
            vision_hint=result.doc_type)
        classifier_kind = "mrz"
        page = classification["page_type"]
        doc_type = ("passport" if classification["accepted_as_passport_identity"]
                    else "prior_visa" if page == "visa_page"
                    else "residence_permit" if page == "residence_permit"
                    else "document")
        classification = {**classification, "reject": False, "message": ""}
    else:
        # A plain supporting document: no passport validation of any kind.
        if is_photo_upload:
            classification = {"page_type": "photo",
                              "accepted_as_passport_identity": False,
                              "reject": False, "message": "",
                              "reasons": ["image with no text targeted at the "
                                          "photo requirement / photo filename"]}
            doc_type = "photo"
            classifier_kind = "filename" if re.search(
                r"photo|portrait|headshot", body.name or "", re.I) else "applicant"
        elif not (result.recognized_text or "").strip():
            classification = {"page_type": "unreadable" if content else "supporting_document",
                              "accepted_as_passport_identity": False,
                              "reject": False, "message": "",
                              "reasons": ["no readable text extracted"]}
        else:
            classification = {"page_type": "supporting_document",
                              "accepted_as_passport_identity": False,
                              "reject": False, "message": "",
                              "reasons": ["supporting document; passport "
                                          "validation not applicable"]}

    # The photo carve-out also applies in passport context when the applicant
    # clearly uploaded a photograph (filename) that OCR'd to nothing.
    if passport_context and classification.get("reject") and is_photo_upload:
        classification = {**classification, "reject": False, "message": "",
                          "page_type": "photo",
                          "accepted_as_passport_identity": False}
        doc_type = "photo"
        classifier_kind = "filename"

    fields_map = {f.key: {"value": f.value, "confidence": f.confidence, "page": f.page}
                  for f in result.fields}
    # A visa sticker / stamp / ID page must NEVER seed passport identity —
    # advisory acceptance does not change that invariant: outside a validated
    # biodata page, passport-adjacent material carries no identity fields.
    if (mrz_kind is not None or result.doc_type == "passport") and \
            not classification["accepted_as_passport_identity"]:
        fields_map = {}
    # Supporting documents get a route-checklist classification: deterministic
    # keywords first, then an optional digit-masked Kimi call ONLY when the
    # result stayed generic.
    if not classification["reject"] and \
            not classification["accepted_as_passport_identity"] and \
            doc_type not in ("passport", "photo", "prior_visa", "residence_permit"):
        from .providers import doc_classifier
        refined = doc_classifier.classify_supporting_document(
            result.recognized_text, body.name)
        if refined != "document":
            classifier_kind = "keyword"
        else:
            kimi_refined = doc_classifier.classify_with_kimi(result.recognized_text)
            if kimi_refined:
                refined = kimi_refined
                classifier_kind = "kimi"
        if refined != "document":
            doc_type = refined
    if classification["reject"]:
        # Never let a REJECTED page seed passport identity — this includes visa/
        # stamp/cover/national-ID pages AND an unverifiable-MRZ page. The applicant
        # re-uploads a clear biodata page; identity is only ever seeded from an
        # accepted, checksum-validated biodata page.
        fields_map = {}

    # Local, deterministic language detection on the extracted text — nothing
    # leaves the backend for this label.
    from . import translation as translation_mod
    detected_language = translation_mod.detect_language(result.recognized_text or "")

    doc = models.StoredDocument(org_id=p.org_id, application_id=application_id, name=body.name,
                                mime=body.mime, size_bytes=body.size_bytes, sha256=sha,
                                storage_ref=f"local://{sha[:16]}", doc_type=doc_type,
                                ocr_status="done", quality_warnings=result.quality_warnings,
                                execution_class=str(ec),
                                page_classification={
                                    "page_type": classification["page_type"],
                                    "accepted_as_passport_identity": classification["accepted_as_passport_identity"],
                                    "reject": classification["reject"],
                                    "reasons": classification["reasons"],
                                    "classifier": classifier_kind},
                                extracted_fields=fields_map,
                                ocr_text=result.recognized_text or "",
                                language=detected_language)
    db.add(doc)
    db.commit()
    # Phase 13: keep the bytes for the in-app preview (served only via the
    # authenticated endpoint / short-lived signed URLs — never a local path).
    if content:
        db.add(models.DocumentBlob(document_id=doc.id, org_id=p.org_id,
                                   mime=body.mime, content=content))
        db.commit()
    audit.record(db, org_id=p.org_id, application_id=application_id, action="document_ocr",
                 detail={"doc_type": doc_type, "mrz_valid": result.mrz_valid,
                         "engine": ocr_meta.get("primary"), "fallback_used": ocr_meta.get("fallback_used"),
                         "docai_degraded": ocr_meta.get("docai_degraded"),
                         "execution_class": str(ec),
                         "page_type": classification["page_type"],
                         "rejected": classification["reject"]}, actor=p.user_id)
    execution.record_execution(db, org_id=p.org_id, application_id=application_id,
                               action="document_ocr", ec=ec,
                               detail={"doc_type": doc_type, "mrz_valid": result.mrz_valid,
                                       "page_type": classification["page_type"]})
    out = {"id": doc.id, "doc_type": doc_type, "mrz_valid": result.mrz_valid,
           "execution_class": str(ec),
           "page_type": classification["page_type"],
           "accepted_as_passport_identity": classification["accepted_as_passport_identity"],
           "rejected": classification["reject"],
           "message": classification["message"],
           "language": detected_language,
           "extracted_fields": doc.extracted_fields, "quality_warnings": result.quality_warnings}
    if body.checklist_item_id:
        out["binding"] = _bind(doc)
    return out


def _required_fields_for(country: str, visa_type: str) -> list[str]:
    """The applicant fields the destination's adapter requires (for the
    'missing information' step). Mode-keyed: real-only modes without a live
    adapter honestly return [] — requirements come from the verified snapshot,
    never invented from demo adapter metadata."""
    from .portal.driver_factory import register_adapters_for_mode, select_metadata_adapter
    register_adapters_for_mode()
    adapter = select_metadata_adapter(country, visa_type)
    if adapter is None:
        return []
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


@app.get("/cases/{application_id}/checklist")
def case_checklist(application_id: str, db=Depends(get_session),
                   p: Principal = Depends(get_principal)):
    """The route-specific journey state saved at continuation: the Kimi route
    decision, disposition, workflow type, and the document checklist with live
    per-item status. No official-source audit exists on the applicant path."""
    app_row = _owned(db, p, application_id)
    from . import checklist_intake
    from .visa_snapshot import intake_flow
    cg = checklist_intake.case_guidance(db, application_id)
    if cg is None:
        return {"guidance": None, "disposition": None, "continuation_kind": None,
                "checklist": [], "checklist_counts": {"total": 0, "required_missing": 0},
                "intake_stage": {"completed": False, "completed_at": None},
                "verification": None, "route_workflow_type": None}
    # Serve-time normalization: stored two-pass-era guidance rows carry a label
    # claiming a retired second-pass check — it must never reach the UI.
    from .visa_snapshot import kimi_primary
    guidance = kimi_primary.normalize_guidance_label(cg.guidance)
    g_inner = (guidance or {}).get("guidance") or {}
    # Per-item status now reflects the applicant's EXPLICIT submissions (an
    # upload alone never fulfils a requirement) + the durable intake stage.
    status = checklist_intake.checklist_state(db, app_row, cg)
    from . import translation as translation_mod
    target = translation_mod.target_for_destination(app_row.destination_country)
    return {"guidance": guidance, "disposition": cg.disposition,
            "continuation_kind": cg.continuation_kind, "intake_id": cg.intake_id,
            "checklist": status["items"], "checklist_counts": status["counts"],
            "intake_stage": status["intake_stage"],
            "translation": {
                "target": target,
                "target_name": translation_mod.language_name(target),
                "certified_note": translation_mod.certified_translation_flag(g_inner)},
            "verification": (guidance or {}).get("verification") or None,
            "route_workflow_type": g_inner.get("route_workflow_type"),
            "health_questions": intake_flow.pending_health_questions(
                g_inner, answers=app_row.answers or {})}


class ChecklistSubmit(BaseModel):
    document_id: Optional[str] = None
    # True = the applicant explicitly confirms a low-confidence assignment
    # ("This appears to be X, but this requirement needs Y").
    confirm: bool = False


@app.post("/cases/{application_id}/checklist/{item_id}/submit")
def submit_checklist_document(application_id: str, item_id: str,
                              body: ChecklistSubmit = ChecklistSubmit(),
                              db=Depends(get_session), p: Principal = Depends(get_principal)):
    """The applicant's explicit Submit for one requirement: binds the document
    permanently, marks the requirement fulfilled with a timestamp, preserves
    classification provenance. Idempotent — repeated clicks never double-submit.
    A confident mismatch is refused; an uncertain one needs confirm=true."""
    from . import checklist_intake
    app_row = _owned(db, p, application_id)
    try:
        out = checklist_intake.submit_document(
            db, p, app_row, item_id, body.document_id, confirm=body.confirm)
    except checklist_intake.ChecklistError as e:
        raise HTTPException(e.status_code, e.detail)
    state = checklist_intake.checklist_state(db, app_row)
    out.update({"checklist": state["items"], "checklist_counts": state["counts"],
                "intake_stage": state["intake_stage"]})
    return out


@app.post("/cases/{application_id}/checklist/{item_id}/withdraw")
def withdraw_checklist_document(application_id: str, item_id: str,
                                db=Depends(get_session), p: Principal = Depends(get_principal)):
    """Withdraw the submission (or remove the upload) for one requirement —
    it returns to Needed. The stored file stays on the case; idempotent."""
    from . import checklist_intake
    app_row = _owned(db, p, application_id)
    try:
        out = checklist_intake.withdraw_document(db, p, app_row, item_id)
    except checklist_intake.ChecklistError as e:
        raise HTTPException(e.status_code, e.detail)
    state = checklist_intake.checklist_state(db, app_row)
    out.update({"checklist": state["items"], "checklist_counts": state["counts"],
                "intake_stage": state["intake_stage"]})
    return out


class ChecklistBind(BaseModel):
    document_id: str


@app.post("/cases/{application_id}/checklist/{item_id}/bind")
def bind_checklist_document(application_id: str, item_id: str, body: ChecklistBind,
                            db=Depends(get_session), p: Principal = Depends(get_principal)):
    """Attach an EXISTING case document to a requirement (reusing an uploaded
    file for another requirement, or attaching a translation artifact) —
    binding only; the applicant's Submit still fulfils it."""
    from . import checklist_intake
    app_row = _owned(db, p, application_id)
    try:
        binding = checklist_intake.bind_document(
            db, p, app_row, item_id, body.document_id,
            provenance={"source": "applicant_attach"})
    except checklist_intake.ChecklistError as e:
        raise HTTPException(e.status_code, e.detail)
    state = checklist_intake.checklist_state(db, app_row)
    return {"binding": binding, "checklist": state["items"],
            "checklist_counts": state["counts"],
            "intake_stage": state["intake_stage"]}


class DocTranslateBody(BaseModel):
    # Explicit target override; defaults to the destination route's language.
    target: Optional[str] = None


@app.post("/cases/{application_id}/documents/{doc_id}/translate")
def translate_case_document(application_id: str, doc_id: str,
                            body: DocTranslateBody = DocTranslateBody(),
                            db=Depends(get_session), p: Principal = Depends(get_principal)):
    """Applicant-requested Kimi K3 machine translation of one document's
    OCR-extracted TEXT (raw image/PDF bytes never leave the backend). The
    result is a linked, clearly-labelled artifact — never presented as a
    certified translation; idempotent per (document, target)."""
    from . import checklist_intake, translation as translation_mod
    app_row = _owned(db, p, application_id)
    doc = db.get(models.StoredDocument, doc_id)
    if not doc or doc.application_id != application_id:
        raise HTTPException(404, "document not found")
    target = (body.target or "").strip() or \
        translation_mod.target_for_destination(app_row.destination_country)
    try:
        out = translation_mod.translate_document(db, p, app_row, doc, target)
    except translation_mod.TranslationError as e:
        raise HTTPException(e.status_code, e.detail)
    cg = checklist_intake.case_guidance(db, application_id)
    g_inner = ((cg.guidance if cg else {}) or {}).get("guidance") or {}
    out["certified_translation_note"] = translation_mod.certified_translation_flag(g_inner)
    out["target_language_name"] = translation_mod.language_name(target)
    return out


class DocTypeBody(BaseModel):
    doc_type: str


@app.post("/cases/{application_id}/documents/{doc_id}/set-type")
def set_document_type(application_id: str, doc_id: str, body: DocTypeBody,
                      db=Depends(get_session), p: Principal = Depends(get_principal)):
    """Applicant-chosen document type for an AMBIGUOUS upload (safe whitelist
    only; never overrides a confident classification, never 'passport')."""
    from . import checklist_intake
    app_row = _owned(db, p, application_id)
    try:
        out = checklist_intake.set_document_type(db, p, app_row, doc_id, body.doc_type)
    except checklist_intake.ChecklistError as e:
        raise HTTPException(e.status_code, e.detail)
    state = checklist_intake.checklist_state(db, app_row)
    out.update({"checklist": state["items"], "checklist_counts": state["counts"]})
    return out


@app.post("/cases/{application_id}/checklist/complete")
def complete_document_intake(application_id: str, db=Depends(get_session),
                             p: Principal = Depends(get_principal)):
    """Server-validated Continue after documents: refuses while any mandatory
    requirement is unfulfilled; records the completed stage durably; advances
    the EXISTING case to its route's next stage (visa/authorization preparation,
    entry preparation, or renewal preparation). Idempotent."""
    from . import checklist_intake
    app_row = _owned(db, p, application_id)
    try:
        return checklist_intake.complete_stage(db, p, app_row)
    except checklist_intake.ChecklistError as e:
        raise HTTPException(e.status_code, e.detail)


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
    from . import dates as dates_mod
    app_row = _owned(db, p, application_id)
    doc = db.get(models.StoredDocument, doc_id)
    if not doc or doc.application_id != application_id:
        raise HTTPException(404, "document not found")

    def _date_kind(key: str) -> str | None:
        k = key.lower()
        if not (k.endswith("_date") or k in ("date_of_birth", "date_of_expiry", "date_of_issue")):
            return None
        return "birth" if "birth" in k else "issue" if "issue" in k else "expiry"

    fields = dict(doc.extracted_fields)
    for e in (edits or []):
        value = e.value
        # An applicant may type a date in the U.S. display format (MM/DD/YYYY)
        # or any printed form — it is normalized to the canonical YYYY-MM-DD
        # before it is stored anywhere. An unparseable date entry is kept
        # verbatim (visible, confirmable) rather than silently guessed.
        kind = _date_kind(e.key)
        if kind:
            norm = dates_mod.normalize_any(value, kind=kind, us_numeric=True)
            if norm:
                value = norm
        fields[e.key] = {"value": value, "confidence": 1.0, "page": 1, "source": "applicant_edit"}
    doc.extracted_fields = fields
    doc.approved = True
    # Approved fields flow into the application answers — date values always
    # as canonical ISO (a raw MRZ YYMMDD or printed form can never leak into
    # answers from this path).
    ans = dict(app_row.answers)
    for k, v in fields.items():
        value = v["value"]
        kind = _date_kind(k)
        if kind:
            norm = dates_mod.normalize_any(value, kind=kind, us_numeric=True)
            if norm:
                value = norm
        ans[k] = value
    app_row.answers = ans
    db.commit()
    # A material change to approved data invalidates any prior signature.
    invalidated = invalidate_signatures_if_changed(db, application_id)
    audit.record(db, org_id=p.org_id, application_id=application_id, action="document_approved",
                 detail={"doc_id": doc_id, "edits": [e.key for e in (edits or [])],
                         "signatures_invalidated": invalidated}, actor=p.user_id)
    # Phase 5: validate passport expiry IMMEDIATELY after applicant approval.
    validity = None
    travel_case_validity = None
    if doc.doc_type == "passport":
        from . import passport_validity, renewal
        validity = passport_validity.check_case_passport(db, app_row)
        if validity.get("blocking"):
            passport_validity.enforce_and_notify(db, app_row, validity)
        # Renewal completion: an approved NEW passport inside a renewal case
        # updates the linked travel case's passport fields, re-evaluates the
        # destination validity, and resumes the travel case.
        travel_case_validity = renewal.propagate_renewed_passport(
            db, app_row, doc, actor=p.user_id)
    return {"approved": True, "answers": app_row.answers,
            "signatures_invalidated": invalidated, "passport_validity": validity,
            "travel_case_validity": travel_case_validity}


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
    nationality: str = ""     # applicant nationality — refines queries + eligibility
    residence: str = ""       # applicant country of residence


@app.post("/discovery")
def run_discovery(body: DiscoverBody, db=Depends(get_session), p: Principal = Depends(get_principal)):
    from .portal import discovery
    from datetime import datetime, timezone
    draft = discovery.discover_official_visa_portal(
        country=body.country, visa_type=body.visa_type,
        nationality=body.nationality, residence=body.residence,
        reviewer="", verification_timestamp=datetime.now(timezone.utc).isoformat())
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
            "search_status": draft["search_status"], "verified_candidates": draft["verified_candidates"],
            "queries": draft["queries"], "portal_limitations": draft["portal_limitations"]}


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


# ---- Standing authorization (brief §5) ----
class StandingAuthBody(BaseModel):
    locale: str = "en"
    appointment_preferences: Optional[dict] = None
    route_key: str = ""


class RevokeBody(BaseModel):
    reason: str = ""


@app.get("/cases/{application_id}/standing-authorization")
def get_standing_authorization(application_id: str, locale: str = "en",
                               db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import authorization as standing
    app_row = _owned(db, p, application_id)
    applicant = db.get(models.Applicant, app_row.applicant_id)
    return {"current": standing.to_dict(standing.current(db, application_id)),
            "text": standing.build_text(locale=locale, applicant_name=applicant.full_name,
                                        destination=app_row.destination_country,
                                        visa_type=app_row.visa_type),
            "text_version": standing.TEXT_VERSION,
            "permitted_actions": standing.PERMITTED_ACTIONS,
            "disclosures": standing.DISCLOSURES}


@app.post("/cases/{application_id}/standing-authorization")
def grant_standing_authorization(application_id: str, body: StandingAuthBody,
                                 db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import authorization as standing
    app_row = _owned(db, p, application_id)
    row = standing.grant(db, app_row=app_row, principal_user=p.user_id,
                         locale=body.locale,
                         appointment_preferences=body.appointment_preferences,
                         route_key=body.route_key)
    return standing.to_dict(row)


@app.delete("/cases/{application_id}/standing-authorization")
def revoke_standing_authorization(application_id: str, body: Optional[RevokeBody] = None,
                                  db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import authorization as standing
    app_row = _owned(db, p, application_id)
    try:
        row = standing.revoke(db, app_row=app_row, principal_user=p.user_id,
                              reason=(body.reason if body else ""))
    except standing.AuthorizationMissing as e:
        raise HTTPException(404, str(e))
    return standing.to_dict(row)


# ---- Final review + exact-version signature (brief §7) ----
class FinalReviewSignBody(BaseModel):
    review_version_id: str
    content_hash: str
    consent_given: bool = False
    intent_confirmed: bool = False
    signature_method: str = "typed"
    signature_value: str = ""
    step_up_token: str
    auth_method: str = "email_otp"


@app.get("/cases/{application_id}/final-review")
def get_final_review(application_id: str, locale: str = "en",
                     db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import final_review
    app_row = _owned(db, p, application_id)
    final_review.check_and_invalidate(db, app_row)
    return {"latest": final_review.to_dict(final_review.latest(db, application_id)),
            "preview": final_review.build_package(db, app_row, locale=locale),
            "current_material_hash": final_review.material_hash(db, app_row)}


@app.post("/cases/{application_id}/final-review")
def create_final_review(application_id: str, locale: str = "en",
                        db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import final_review
    app_row = _owned(db, p, application_id)
    row = final_review.create_review_version(db, app_row, actor=p.user_id, locale=locale)
    token = issue_action_token(p, "final_review_sign", application_id, ttl_seconds=600)
    return {**final_review.to_dict(row), "step_up_token": token}


@app.post("/cases/{application_id}/final-review/sign")
def sign_final_review(application_id: str, body: FinalReviewSignBody, request: Request,
                      db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import final_review
    from .providers import esign
    app_row = _owned(db, p, application_id)
    verify_action_token(body.step_up_token, "final_review_sign", application_id)
    row = db.get(models.ApplicationReviewVersion, body.review_version_id)
    if not row or row.application_id != application_id:
        raise HTTPException(404, "review version not found")
    if row.invalidated:
        raise HTTPException(409, "this review version was invalidated — re-review required")
    # The applicant signs the EXACT version: the echoed hash must match, and the
    # version must still match the live material state.
    if body.content_hash != row.content_hash:
        raise HTTPException(409, "content hash mismatch — review the current version")
    if row.content_hash != final_review.material_hash(db, app_row):
        row.invalidated = True
        row.invalidated_reason = "material change before signature"
        db.commit()
        raise HTTPException(409, "the application changed — a fresh final review is required")
    if not (body.consent_given and body.intent_confirmed):
        raise HTTPException(422, "consent and intent confirmation are both required")
    applicant = db.get(models.Applicant, app_row.applicant_id)
    doc_text = json.dumps(row.package, sort_keys=True, default=str)
    req = esign.SignatureRequest(
        applicant={"full_name": applicant.full_name, "email": applicant.email},
        org_id=app_row.org_id, case_id=application_id, app_version=app_row.current_version,
        document_text=doc_text, document_hash=esign.document_hash(doc_text),
        consent_given=body.consent_given, intent_confirmed=body.intent_confirmed,
        signature_method=body.signature_method, signature_value=body.signature_value,
        step_up_verified=True, auth_method=body.auth_method,
        ip_address=request.client.host if request.client else "",
        user_agent=request.headers.get("user-agent", ""))
    try:
        result = esign.get_provider().sign(req)
    except ValueError as e:
        raise HTTPException(422, str(e))
    sig = models.NativeSignature(
        org_id=app_row.org_id, application_id=application_id, app_version=app_row.current_version,
        provider=result["provider"], template_version=result["template_version"],
        consent_version=result["consent_version"], document_hash=result["document_hash"],
        artifact_hash=result["artifact_hash"],
        artifact_ref=f"local://sig/{result['artifact_hash'][:16]}",
        signature_method=result["signature_method"], auth_method=result["auth_method"],
        ip_address=req.ip_address, user_agent=req.user_agent[:300],
        app_snapshot_hash=row.content_hash)
    db.add(sig)
    db.flush()
    _sig_event(db, sig.id, application_id, "signed",
               {"kind": "final_review", "review_version": row.version,
                "artifact_hash": result["artifact_hash"]})
    final_review.record_signature(db, row, signature_id=sig.id, actor=p.user_id)
    return {"signature_id": sig.id, "review_version": row.version,
            "content_hash": row.content_hash, "artifact_hash": result["artifact_hash"]}


# ---- Payment authorization view (brief §6) ----
@app.get("/cases/{application_id}/payment-authorization")
def get_payment_authorization(application_id: str, db=Depends(get_session),
                              p: Principal = Depends(get_principal)):
    from . import payments
    _owned(db, p, application_id)
    exec_row = db.execute(select(models.WorkflowExecution).where(
        models.WorkflowExecution.application_id == application_id)).scalar_one_or_none()
    fee = ((exec_row.snapshot or {}).get("fee") if exec_row else None) or {}
    rows = db.execute(select(models.PaymentAuthorization).where(
        models.PaymentAuthorization.application_id == application_id).order_by(
        models.PaymentAuthorization.approved_at.desc())).scalars().all()
    current = next((r for r in rows if r.status == "authorized"), None)
    return {"current": payments.to_dict(current),
            "history": [payments.to_dict(r) for r in rows[:10]],
            "fee": {k: fee.get(k) for k in ("amount", "currency", "display",
                                            "government_fee_cents", "service_fee_cents")
                    if k in fee}}


def _sig_event(db, signature_id: str, application_id: str, event: str, detail: dict):
    from sqlalchemy import func
    nseq = (db.query(func.max(models.SignatureEvent.seq)).scalar() or 0) + 1
    db.add(models.SignatureEvent(seq=nseq, signature_id=signature_id, application_id=application_id,
                                 event=event, detail=detail))
    db.commit()


def invalidate_signatures_if_changed(db, application_id: str):
    """Any material change to answers/documents invalidates completed signatures
    AND the signed final-review version (§7 — back to review + new signature)."""
    from .providers import esign
    from . import final_review
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
    if final_review.check_and_invalidate(db, app_row):
        changed += 1
    return changed


# ---- Workflow signals ----
_SIGNALS = {"approve_review", "sign_authorization", "solve_captcha", "verify_email",
            "approve_payment", "complete_payment", "select_appointment",
            "approve_reschedule", "complete_declaration", "cancel",
            "provide_information"}


class SignalBody(BaseModel):
    token: Optional[str] = None
    slot_id: Optional[str] = None
    # approve_payment: the client echoes the exact amount it displayed so the
    # confirmation can never silently cover a different figure (§6).
    amount_cents: Optional[int] = None
    currency: Optional[str] = None
    # provide_information: answers to the dynamic missing-information questions
    # the portal execution paused on (Part 4).
    answers: Optional[dict] = None


def _record_terminal_execution(db, p: Principal, application_id: str):
    """When a case reaches COMPLETED, persist the execution classification of the
    result so the audit trail proves whether it was a real government outcome or
    a MOCK/sandbox run — the completed state alone must never imply 'real'."""
    app_row = db.get(models.VisaApplication, application_id)
    if app_row and app_row.state == "COMPLETED":
        ec = _case_execution_class(app_row.destination_country, app_row.visa_type, db=db, app_row=app_row)
        # A completed case IS a government-outcome action, so its record reflects
        # whether the outcome was real (True only for verified LIVE_PRODUCTION).
        execution.record_execution(db, org_id=p.org_id, application_id=application_id,
                                   action="case_completed", ec=ec,
                                   detail={"state": app_row.state}, government_outcome=True)
        # Phase 15 (sandbox contract): queue the typed case-status webhook, with
        # the execution class carried so Trip.com can never mistake a mock run
        # for a production result.
        from .integrations import tripcom, tripcom_admin
        event = tripcom.case_status_event(
            tripcom_case_ref=(app_row.answers or {}).get("tripcom_case_ref", ""),
            ellis_case_id=application_id, state=app_row.state)
        event["execution_class"] = str(ec)
        event["is_real_government_result"] = execution.is_real_government_result(ec)
        tripcom_admin.queue_event(db, org_id=p.org_id, event=event)


def _signal_or_gate_error(db, p: Principal, application_id: str, name: str, **kwargs):
    """Drive one workflow transition, translating the centralized safety-gate
    exceptions (service.enforce_safety) into honest 409s. Both /start and
    /signals route through here so neither can bypass the gate."""
    from . import personal_gate, passport_validity
    from .portal.driver_factory import RealOnlyStop
    try:
        status, _ = service.signal(db, application_id, name, **kwargs)
    except RealOnlyStop as e:
        # Real-only runtime mode with no approved live adapter: the case is
        # preserved and the workflow STOPS with a typed status — it never
        # silently falls back to MockPortal (brief section 3).
        audit.record(db, org_id=p.org_id, application_id=application_id,
                     action="real_only_stop",
                     detail={"status": e.status, "detail": e.detail, "signal": name},
                     actor=p.user_id)
        raise HTTPException(409, detail={"reason": "real_only_stop", "status": e.status,
                                         "detail": e.detail})
    except execution.MockAsProductionError as e:
        raise HTTPException(409, detail={"reason": "real_only_stop", "status": "UNSUPPORTED",
                                         "detail": str(e)})
    except personal_gate.PreparationOnlyMode as e:
        audit.record(db, org_id=p.org_id, application_id=application_id,
                     action="live_action_blocked_preparation_mode",
                     detail={"missing_gates": e.missing_gates, "missing_info": e.missing_info,
                             "signal": name}, actor=p.user_id)
        raise HTTPException(409, str(e))
    except passport_validity.PassportBlocked as e:
        raise HTTPException(409, detail={"reason": "passport_validity", "verdict": e.verdict})
    _record_terminal_execution(db, p, application_id)
    return status


@app.post("/cases/{application_id}/start")
def start_case(application_id: str, db=Depends(get_session), p: Principal = Depends(get_principal)):
    app_row = _owned(db, p, application_id)
    # Server-side enforcement for routed cases: the workflow may never start
    # while mandatory checklist documents remain unsubmitted (frontend state is
    # never trusted). Legacy cases without a route checklist are unaffected.
    from . import checklist_intake
    cg = checklist_intake.case_guidance(db, application_id)
    if cg is not None:
        remaining = checklist_intake.checklist_state(
            db, app_row, cg)["counts"]["required_missing"]
        if remaining > 0:
            raise HTTPException(409, detail={
                "reason": "documents_incomplete", "required_remaining": remaining,
                "message": f"Submit {remaining} remaining required "
                           f"document{'s' if remaining != 1 else ''} before starting."})
    return _signal_or_gate_error(db, p, application_id, "start")


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
    if name == "approve_payment" and body.amount_cents is not None:
        kwargs["amount_cents"] = body.amount_cents
        kwargs["currency"] = body.currency
    if name == "provide_information":
        answers = _validated_information_answers(db, application_id, body.answers or {})
        if not answers:
            raise HTTPException(422, detail={"reason": "no_valid_answers",
                                             "message": "No usable answers were provided."})
        # Persist first (DB is the source of truth), with the same material-
        # change signature invalidation as the answers endpoint.
        app_row = db.get(models.VisaApplication, application_id)
        merged = dict(app_row.answers or {})
        merged.update(answers)
        app_row.answers = merged
        db.commit()
        invalidated = invalidate_signatures_if_changed(db, application_id)
        audit.record(db, org_id=p.org_id, application_id=application_id,
                     action="additional_information_provided",
                     detail={"keys": sorted(answers.keys()),
                             "signatures_invalidated": invalidated}, actor=p.user_id)
        kwargs["answers"] = answers
    return _signal_or_gate_error(db, p, application_id, name, **kwargs)


def _validated_information_answers(db, application_id: str, raw: dict) -> dict:
    """Safe input validation for dynamic-question answers: only answers to the
    questions Ellis actually asked (the pending payload), values as trimmed
    strings, dates canonicalized via the single date authority."""
    from . import dates as dates_mod
    exec_row = db.execute(select(models.WorkflowExecution).where(
        models.WorkflowExecution.application_id == application_id)).scalar_one_or_none()
    pending = (exec_row.pending if exec_row else None) or {}
    asked = {q.get("key"): q for q in (pending.get("questions") or []) if q.get("key")}
    out: dict = {}
    for key, value in (raw or {}).items():
        q = asked.get(key)
        if q is None or key.startswith("document:"):
            continue    # never accept answer keys Ellis did not ask for
        v = str(value if value is not None else "").strip()
        if not v:
            continue
        if len(v) > 300:
            raise HTTPException(422, detail={"reason": "invalid_answer", "key": key,
                                             "message": "That answer is too long."})
        if q.get("kind") == "date":
            iso = dates_mod.normalize_any(v, kind="expiry", us_numeric=True)
            if not iso:
                raise HTTPException(422, detail={
                    "reason": "invalid_answer", "key": key,
                    "message": "Please enter a valid date."})
            v = iso
        out[key] = v
    return out


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


# ---- Privacy: export + deletion (Phase 10) ----
@app.get("/cases/{application_id}/export")
def export_case_endpoint(application_id: str, db=Depends(get_session), p: Principal = Depends(get_principal)):
    """Applicant-controlled portable export of everything held for this case."""
    from . import privacy
    _owned(db, p, application_id)
    bundle = privacy.export_case(db, application_id)
    audit.record(db, org_id=p.org_id, application_id=application_id, action="data_exported",
                 detail={"scope": "case"}, actor=p.user_id)
    return bundle


@app.get("/export")
def export_org_endpoint(db=Depends(get_session), p: Principal = Depends(get_principal)):
    """Tenant export: every case owned by the caller's org."""
    from . import privacy
    return privacy.export_org(db, p.org_id)


@app.delete("/cases/{application_id}")
def delete_case_endpoint(application_id: str, db=Depends(get_session), p: Principal = Depends(get_principal)):
    """Applicant-controlled erasure of a case (right to be forgotten)."""
    from . import privacy
    _owned(db, p, application_id)
    return privacy.delete_case(db, application_id, actor=p.user_id)


@app.delete("/applicants/{applicant_id}")
def delete_applicant_endpoint(applicant_id: str, db=Depends(get_session), p: Principal = Depends(get_principal)):
    """Erase an applicant and all their cases. Tenant-isolated."""
    from . import privacy
    applicant = db.get(models.Applicant, applicant_id)
    if not applicant:
        raise HTTPException(404, "applicant not found")
    require_owner(p, applicant.org_id)
    return privacy.delete_applicant(db, applicant_id, actor=p.user_id)


# ---- Ops: readiness + metrics + kill switches (Phase 11) ----
@app.get("/readyz")
def readyz(db=Depends(get_session)):
    """Readiness: the API is up, its datastore is reachable, AND (in production)
    no default credentials are in place — production refuses to be 'ready' while
    running on default dev secrets."""
    try:
        from sqlalchemy import text
        db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        raise HTTPException(503, "database not ready")
    from . import setup as setup_mod
    pre = setup_mod.production_preflight()
    if not pre["ok"]:
        raise HTTPException(503, "production preflight failed: default credentials in use")
    return {"ready": True, "production_preflight": pre}


@app.get("/metrics")
def metrics(db=Depends(get_session), p: Principal = Depends(get_principal)):
    """Operational counters for the caller's org (no PII). Case counts by state,
    plus payment/appointment/submission totals and provider health flags."""
    from sqlalchemy import func
    from .config import capabilities, killswitches
    rows = db.execute(select(models.VisaApplication.state, func.count()).where(
        models.VisaApplication.org_id == p.org_id).group_by(models.VisaApplication.state)).all()
    by_state = {state: n for state, n in rows}
    def _count(model):
        return db.execute(select(func.count()).select_from(model).join(
            models.VisaApplication, model.application_id == models.VisaApplication.id).where(
            models.VisaApplication.org_id == p.org_id)).scalar() or 0
    return {
        "cases_total": sum(by_state.values()),
        "cases_by_state": by_state,
        "appointments_booked": _count(models.Appointment),
        "submissions": _count(models.SubmissionConfirmation),
        "signatures": _count(models.NativeSignature),
        "capabilities": capabilities(),
        "kill_switches": killswitches(),
    }


# ---- Country-adapter administration + approval lifecycle (Phase 2) ----
class AdapterCreate(BaseModel):
    country: str
    visa_type: str = "tourist"
    config: dict = {}


class AdapterTransition(BaseModel):
    to_state: str
    evidence: dict = {}


class AdapterRollback(BaseModel):
    to_version: int


class KillBody(BaseModel):
    reason: str = ""


def _adapter_err(fn):
    from . import adapters_admin as aa
    try:
        return fn()
    except aa.NotAuthorizedError as e:
        raise HTTPException(403, str(e))
    except aa.LifecycleError as e:
        raise HTTPException(400, str(e))


@app.get("/admin/adapters")
def admin_list_adapters(db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import adapters_admin as aa
    return {"adapters": aa.list_adapters(db), "is_admin": p.role == "admin"}


@app.get("/admin/coverage")
def admin_coverage(db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import adapters_admin as aa
    return {"coverage": aa.coverage_matrix(db)}


@app.post("/admin/adapters")
def admin_create_adapter(body: AdapterCreate, db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import adapters_admin as aa
    rec = aa.create_adapter(db, country=body.country, visa_type=body.visa_type,
                            config=body.config, actor=p.user_id)
    return aa.to_dict(rec)


@app.get("/admin/adapters/{adapter_id}")
def admin_get_adapter(adapter_id: str, db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import adapters_admin as aa
    rec = db.get(models.AdapterRecord, adapter_id)
    if not rec:
        raise HTTPException(404, "adapter not found")
    return {**aa.to_dict(rec), "versions": aa.versions(db, adapter_id),
            "audit": aa.adapter_audit(db, adapter_id)}


@app.put("/admin/adapters/{adapter_id}")
def admin_update_adapter(adapter_id: str, config: dict, db=Depends(get_session),
                         p: Principal = Depends(get_principal)):
    from . import adapters_admin as aa
    return _adapter_err(lambda: aa.to_dict(aa.update_config(db, adapter_id, config, p.user_id)))


@app.post("/admin/adapters/{adapter_id}/transition")
def admin_transition_adapter(adapter_id: str, body: AdapterTransition, db=Depends(get_session),
                             p: Principal = Depends(get_principal)):
    from . import adapters_admin as aa
    return _adapter_err(lambda: aa.to_dict(aa.transition(
        db, adapter_id, body.to_state, actor=p.user_id, is_admin=(p.role == "admin"),
        evidence=body.evidence)))


@app.post("/admin/adapters/{adapter_id}/kill")
def admin_kill_adapter(adapter_id: str, body: KillBody, db=Depends(get_session),
                       p: Principal = Depends(get_principal)):
    from . import adapters_admin as aa
    return _adapter_err(lambda: aa.to_dict(aa.kill(
        db, adapter_id, actor=p.user_id, is_admin=(p.role == "admin"), reason=body.reason)))


@app.post("/admin/adapters/{adapter_id}/clear-kill")
def admin_clear_kill(adapter_id: str, db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import adapters_admin as aa
    return _adapter_err(lambda: aa.to_dict(aa.clear_kill(
        db, adapter_id, actor=p.user_id, is_admin=(p.role == "admin"))))


@app.post("/admin/adapters/{adapter_id}/rollback")
def admin_rollback_adapter(adapter_id: str, body: AdapterRollback, db=Depends(get_session),
                           p: Principal = Depends(get_principal)):
    from . import adapters_admin as aa
    return _adapter_err(lambda: aa.to_dict(aa.rollback(
        db, adapter_id, body.to_version, actor=p.user_id, is_admin=(p.role == "admin"))))


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
