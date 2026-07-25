"""Applicant route-intake + snapshot admin API (brief sections 14-16, 27).

Mounted into the main FastAPI app. Applicant endpoints are tenant-scoped;
admin endpoints require the admin role. An AI model can never mark anything
human-reviewed here — review resolution requires an authenticated admin and
writes an immutable audit event.
"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from datetime import datetime, timezone

from sqlalchemy import select

from .. import audit
from ..security import Principal, get_principal, require_admin
from ..db import get_session
from . import SNAPSHOT_DATE, SNAPSHOT_LABEL, UPDATE_MODE, AUTOMATIC_RULE_REFRESH_ENABLED
from .models import (AdapterDevelopmentTask, ConsularJurisdictionRule,
                     HumanReviewTask, OfficialPortalRecord, RouteIntake,
                     RouteResolution, SnapshotConflict, SnapshotResearchBatch,
                     SourceEvidence, VisaRoute, VisaRouteVersion)
from . import resolution as resolution_mod

router = APIRouter()

DISCLAIMER_EN = "These requirements were captured as of July 23, 2026 and may have changed."

# The full intake field set (brief section 15). Conditional fields marked so the
# UI can drive the wizard entirely from this contract (no source edits needed).
INTAKE_FIELDS = [
    {"key": "passport_nationality", "required": True},
    {"key": "passport_issuing_country", "required": True},
    {"key": "travel_document_type", "required": True, "default": "ordinary_passport"},
    {"key": "lawful_country_of_residence", "required": True},
    {"key": "residence_status", "required": False,
     "condition": "lawful_country_of_residence != passport_nationality"},
    # Structured home address (commonly required by visa forms and portal
    # account creation). Country-aware: only line 1, city and country are
    # universally mandatory — many countries have no state/region or postal
    # code, so those stay optional and no U.S. format is ever assumed.
    {"key": "address_line1", "required": True},
    {"key": "address_line2", "required": False},
    {"key": "address_city", "required": True},
    {"key": "address_region", "required": False},
    {"key": "address_postal_code", "required": False},
    {"key": "address_country", "required": True},
    {"key": "mailing_address_same", "required": False, "default": True},
    {"key": "destination_country", "required": True},
    {"key": "visa_category", "required": True, "default": "tourist_visa"},
    {"key": "visa_subtype", "required": False},
    {"key": "travel_purpose", "required": True, "default": "tourism"},
    {"key": "arrival_date", "required": True},
    {"key": "departure_date", "required": True},
    {"key": "transit_countries", "required": False},
    {"key": "age", "required": True},
    {"key": "dependants", "required": False},
    {"key": "existing_destination_visas", "required": False},
    {"key": "existing_residence_permits", "required": False},
    {"key": "prior_refusals", "required": False},
    {"key": "existing_portal_account", "required": False},
    {"key": "preferred_language", "required": True, "default": "en"},
    {"key": "email", "required": True},
]


@router.get("/snapshot/info")
def snapshot_info(_: Principal = Depends(get_principal)):
    return {
        "snapshot_date": SNAPSHOT_DATE,
        "label": SNAPSHOT_LABEL,
        "update_mode": UPDATE_MODE,
        "automatic_refresh_enabled": AUTOMATIC_RULE_REFRESH_ENABLED,
        "display_line": "Visa requirements snapshot: July 23, 2026",
        "disclaimer": DISCLAIMER_EN,
        "intake_fields": INTAKE_FIELDS,
    }


@router.get("/snapshot/registries")
def snapshot_registries(_: Principal = Depends(get_principal)):
    """Reference registries for the intake UI pickers (versioned, read-only)."""
    from .registry import load_registry
    countries = [{"alpha_2": c["alpha_2"], "alpha_3": c["alpha_3"],
                  "name": c.get("common_name") or c["name"], "flag": c.get("flag"),
                  "is_territory": c["is_territory"]}
                 for c in load_registry("countries")["entries"]]
    nationalities = [{"code": n["code"], "name": n["name"], "kind": n["kind"]}
                     for n in load_registry("nationalities")["entries"]]
    return {
        "snapshot_date": SNAPSHOT_DATE,
        "countries": countries,
        "nationalities": nationalities,
        "travel_document_types": load_registry("travel_document_types")["entries"],
        "tourist_visa_categories": load_registry("tourist_visa_categories")["entries"],
        "languages": [{"alpha_2": l["alpha_2"], "name": l["name"]}
                      for l in load_registry("languages")["entries"]],
    }


class IntakeBody(BaseModel):
    answers: dict = {}
    preferred_language: str = "en"
    email: str = ""


@router.post("/intake")
def create_intake(body: IntakeBody, db=Depends(get_session), p: Principal = Depends(get_principal)):
    row = RouteIntake(org_id=p.org_id, user_id=p.user_id, answers=body.answers or {},
                      preferred_language=body.preferred_language, email=body.email)
    db.add(row)
    db.commit()
    audit.record(db, org_id=p.org_id, application_id="", action="route_intake_created",
                 detail={"intake_id": row.id}, actor=p.user_id)
    return {"id": row.id, "status": row.status, "answers": row.answers}


@router.get("/intake")
def list_intakes(db=Depends(get_session), p: Principal = Depends(get_principal)):
    rows = db.execute(select(RouteIntake).where(
        RouteIntake.org_id == p.org_id, RouteIntake.user_id == p.user_id)
        .order_by(RouteIntake.created_at.desc())).scalars().all()
    return {"intakes": [{"id": r.id, "status": r.status, "answers": r.answers,
                         "case_id": r.case_id, "updated_at": str(r.updated_at)} for r in rows]}


def _owned_intake(db, p: Principal, intake_id: str) -> RouteIntake:
    row = db.get(RouteIntake, intake_id)
    if not row or row.org_id != p.org_id:
        raise HTTPException(404, "intake not found")
    return row


@router.get("/intake/{intake_id}")
def get_intake(intake_id: str, db=Depends(get_session), p: Principal = Depends(get_principal)):
    r = _owned_intake(db, p, intake_id)
    out = {"id": r.id, "status": r.status, "answers": r.answers, "email": r.email,
           "preferred_language": r.preferred_language, "case_id": r.case_id,
           "resolution": None}
    if r.resolution_id:
        res = db.get(RouteResolution, r.resolution_id)
        if res:
            out["resolution"] = {"id": res.id, "readiness_status": res.readiness_status,
                                 "route_key": res.route_key, "checks": res.checks}
    return out


@router.delete("/intake/{intake_id}")
def delete_intake(intake_id: str, db=Depends(get_session),
                  p: Principal = Depends(get_principal)):
    """Erase an intake that never became a case: its answers (which may hold
    passport-derived identity data), every uploaded intake document (raw
    passport bytes + extracted profile), and its research linkage. Converted
    intakes are erased through DELETE /cases/{id} instead — one erasure home
    per datum. Leaves a single non-PII tombstone audit event."""
    from .models import RouteIntakeDocument
    r = _owned_intake(db, p, intake_id)
    if r.case_id:
        raise HTTPException(409, "intake was converted — erase the case instead")
    docs = db.execute(select(RouteIntakeDocument).where(
        RouteIntakeDocument.intake_id == r.id)).scalars().all()
    for d in docs:
        db.delete(d)
    db.delete(r)
    db.commit()
    audit.record(db, org_id=p.org_id, application_id="", action="route_intake_erased",
                 detail={"intake_id": intake_id, "documents": len(docs)}, actor=p.user_id)
    return {"deleted": True, "documents": len(docs)}


@router.put("/intake/{intake_id}")
def update_intake(intake_id: str, body: IntakeBody, db=Depends(get_session),
                  p: Principal = Depends(get_principal)):
    r = _owned_intake(db, p, intake_id)
    if r.status == "converted":
        raise HTTPException(409, "intake already converted to a case")
    merged = dict(r.answers or {})
    merged.update(body.answers or {})
    r.answers = merged
    if body.email:
        r.email = body.email
    if body.preferred_language:
        r.preferred_language = body.preferred_language
    db.commit()
    return {"id": r.id, "status": r.status, "answers": r.answers}


# ---- Intake passport upload (applicant journey, Part 1) ---------------------
# The applicant may START with a passport instead of typing passport-derived
# fields. The existing OCR hierarchy (Document AI -> flagged Kimi vision ->
# local deterministic MRZ) runs exactly as at the case stage; the profile
# builder is deterministic and whitelisted — Kimi can never invent identity
# data. Raw recognized text is transient and never persisted or logged.

_DOC_MIME_ALLOWLIST = ("application/pdf", "image/jpeg", "image/png", "image/tiff")
_DOC_MAX_BYTES = 10 * 1024 * 1024


class IntakeDocumentBody(BaseModel):
    name: str
    mime: str = "application/pdf"
    size_bytes: int = 1024
    text: str = ""          # embedded text layer / fixture for the local provider
    content_b64: str = ""   # base64 image/PDF bytes -> Document AI / Kimi vision


def _profile_response(doc, *, duplicate: bool = False) -> dict:
    profile = doc.passport_profile or {}
    return {"accepted": True, "rejected": False, "duplicate": duplicate,
            "document_id": doc.id, "doc_type": doc.doc_type,
            "mrz_valid": bool(profile.get("mrz_valid")),
            "execution_class": doc.execution_class,
            "profile": profile,
            "prefill": profile.get("prefill") or {},
            "conflicts": profile.get("conflicts") or [],
            "quality_warnings": doc.quality_warnings or []}


@router.post("/intake/{intake_id}/passport")
def upload_intake_passport(intake_id: str, body: IntakeDocumentBody,
                           db=Depends(get_session), p: Principal = Depends(get_principal)):
    import base64
    import hashlib

    from ..providers import ocr as ocr_provider
    from ..providers import passport_classifier
    from .. import execution
    from . import intake_flow
    from .models import RouteIntakeDocument

    r = _owned_intake(db, p, intake_id)
    if r.status == "converted":
        raise HTTPException(409, "intake already converted to a case")
    if body.mime not in _DOC_MIME_ALLOWLIST:
        raise HTTPException(415, "unsupported document type")
    if body.size_bytes > _DOC_MAX_BYTES:
        raise HTTPException(413, "document too large")
    content = b""
    if body.content_b64:
        try:
            content = base64.b64decode(body.content_b64)
        except Exception:
            raise HTTPException(400, "invalid content_b64")
        if len(content) > _DOC_MAX_BYTES:
            raise HTTPException(413, "document too large")
    sha = hashlib.sha256(content or body.text.encode()).hexdigest()

    # Duplicate upload (double click, retry after refresh): return the existing
    # record — never a second row.
    existing = db.execute(select(RouteIntakeDocument).where(
        RouteIntakeDocument.intake_id == r.id,
        RouteIntakeDocument.sha256 == sha)).scalars().first()
    if existing is not None:
        return _profile_response(existing, duplicate=True)

    result, ocr_meta = ocr_provider.process_with_failover(
        content=content, text=body.text, mime=body.mime, expect_passport=True)
    ec = execution.classify_ocr(ocr_meta)
    mrz = ocr_provider.parse_mrz(result.recognized_text) if result.recognized_text else None
    classification = passport_classifier.classify_page(
        text=result.recognized_text, mrz=mrz, has_image=bool(content),
        vision_hint=result.doc_type)
    # Only a validated biodata page may seed passport identity — a model hint
    # of "passport" without a checksum-valid MRZ is never enough.
    if classification["reject"] or not classification["accepted_as_passport_identity"]:
        # Honest rejection with the exact retry guidance; nothing is stored, so
        # the applicant can immediately retry with the biodata page.
        return {"accepted": False, "rejected": True,
                "page_type": classification["page_type"],
                "message": classification["message"] or
                "This page could not be used as the passport biodata page. "
                "Upload a clear photo of the photo page of your passport.",
                "quality_warnings": result.quality_warnings}

    fields_map = {f.key: {"value": f.value, "confidence": f.confidence, "page": f.page}
                  for f in result.fields}
    profile = intake_flow.build_passport_profile(
        ocr_fields=fields_map, mrz=mrz, recognized_text=result.recognized_text,
        mrz_valid=bool(mrz and mrz.get("mrz_valid")))
    doc = RouteIntakeDocument(
        org_id=p.org_id, user_id=p.user_id, intake_id=r.id, name=body.name,
        mime=body.mime, size_bytes=body.size_bytes, sha256=sha,
        content=content or None, text=body.text, doc_type="passport",
        execution_class=str(ec),
        page_classification={k: classification[k] for k in
                             ("page_type", "accepted_as_passport_identity",
                              "reject", "reasons")},
        extracted_fields=fields_map, passport_profile=profile,
        quality_warnings=result.quality_warnings)
    db.add(doc)
    db.commit()
    # Structural audit only — never field values (passport PII stays out of logs).
    audit.record(db, org_id=p.org_id, application_id="", action="intake_passport_ocr",
                 detail={"intake_id": r.id, "doc_type": doc.doc_type,
                         "mrz_valid": profile.get("mrz_valid"),
                         "engine": ocr_meta.get("primary"),
                         "fallback_used": ocr_meta.get("fallback_used"),
                         "execution_class": str(ec),
                         "fields_extracted": len(profile.get("fields") or {}),
                         "needs_confirmation": sum(
                             1 for f in (profile.get("fields") or {}).values()
                             if f.get("needs_confirmation")),
                         "conflicts": len(profile.get("conflicts") or [])},
                 actor=p.user_id)
    return _profile_response(doc)


@router.get("/intake/{intake_id}/passport")
def get_intake_passport(intake_id: str, db=Depends(get_session),
                        p: Principal = Depends(get_principal)):
    """Latest extracted passport profile for this intake (refresh-resume of the
    confirmation panel)."""
    from .models import RouteIntakeDocument
    r = _owned_intake(db, p, intake_id)
    doc = db.execute(select(RouteIntakeDocument).where(
        RouteIntakeDocument.intake_id == r.id)
        .order_by(RouteIntakeDocument.created_at.desc())).scalars().first()
    if doc is None:
        return {"profile": None}
    return _profile_response(doc)


@router.post("/intake/{intake_id}/resolve")
def resolve_intake(intake_id: str, background: BackgroundTasks,
                   db=Depends(get_session), p: Principal = Depends(get_principal)):
    r = _owned_intake(db, p, intake_id)
    missing = [f["key"] for f in INTAKE_FIELDS
               if f["required"] and not (r.answers or {}).get(f["key"])
               and not f.get("default")]
    if missing:
        raise HTTPException(422, detail={"missing_fields": missing})
    result = resolution_mod.resolve(db, org_id=p.org_id, answers=r.answers, case_id=r.case_id)
    r.resolution_id = result["resolution_id"]
    r.status = "resolved"
    db.commit()
    audit.record(db, org_id=p.org_id, application_id="", action="route_intake_resolved",
                 detail={"intake_id": r.id, "readiness": result["readiness_status"],
                         "route_key": result.get("route_key")}, actor=p.user_id)

    # The single-pass Kimi decision is the ONLY route analysis in the applicant
    # flow. No official-source research job is created or started here — the
    # research pipeline remains a separate developer/administrator tool
    # (POST /admin/snapshot/research-jobs).
    checks = result.get("checks", {})

    # Kimi-primary immediate guidance: attach instantly when cached; otherwise
    # the UI calls POST /intake/{id}/guidance (the bounded single-pass decision).
    from . import kimi_primary
    route = _guidance_route(r, checks)
    try:
        cached_row = kimi_primary._cached(db, kimi_primary.cache_key(route))
        if cached_row is not None:
            g = kimi_primary.get_route_guidance(db, route)
            result["kimi_guidance"] = g
            if g.get("stale"):
                background.add_task(kimi_primary.refresh_stale_async,
                                    _new_session, route)
        else:
            result["kimi_guidance_pending"] = kimi_primary.is_available()
    except kimi_primary.GuidanceUnavailable:
        result["kimi_guidance_pending"] = False
    return result


def _new_session():
    from ..db import SessionLocal
    return SessionLocal()


def _guidance_route(r, checks: dict | None = None) -> dict:
    """Route facts for guidance: intake answers + normalized codes when known."""
    route = dict(r.answers or {})
    norm = ((checks or {}).get("normalization", {}) or {}).get("normalized") or {}
    route.update({k: v for k, v in norm.items() if v})
    return route


@router.post("/intake/{intake_id}/guidance")
def route_guidance(intake_id: str, background: BackgroundTasks,
                   db=Depends(get_session), p: Principal = Depends(get_principal)):
    """The single-pass Kimi route decision (one structured analysis with
    deterministic validation) under one hard 60-second deadline. Cached
    identical routes return instantly. Never blocks on — or starts —
    official-source research."""
    r = _owned_intake(db, p, intake_id)
    from . import kimi_primary
    route = _guidance_route(r)
    try:
        g = kimi_primary.get_route_guidance(db, route)
    except kimi_primary.GuidanceUnavailable as e:
        raise HTTPException(503, detail={"status": kimi_primary.STATUS_UNAVAILABLE,
                                         "reason": str(e)})
    except kimi_primary.GuidanceTimeout:
        raise HTTPException(504, detail={"status": kimi_primary.STATUS_TIMEOUT,
                                         "reason": kimi_primary.TIMEOUT_MESSAGE})
    except kimi_primary.GuidanceProviderError as e:
        raise HTTPException(503, detail={"status": kimi_primary.STATUS_UNAVAILABLE,
                                         "reason": e.envelope.get("user_message"),
                                         "category": e.envelope.get("category"),
                                         "provider_status": e.envelope.get("provider_status")})
    if g.get("stale"):
        background.add_task(kimi_primary.refresh_stale_async, _new_session, route)
    # Guidance-driven adapter generation (authorized bridge; reversible build +
    # normal validation/auto-release pipeline) happens in the background.
    if r.case_id and (g.get("guidance") or {}).get("official_portal_url") \
            and g["status"] == kimi_primary.STATUS_PRIMARY:
        from .routekey import RouteInput, route_key as _rk
        try:
            key = _rk(RouteInput(
                passport_nationality=route.get("passport_nationality", ""),
                passport_issuing_country=route.get("passport_issuing_country", ""),
                travel_document_type=route.get("travel_document_type", "ordinary_passport"),
                lawful_country_of_residence=route.get("lawful_country_of_residence", ""),
                destination_country=route.get("destination_country", ""),
                visa_category=route.get("visa_category", "tourist_visa"),
                policy_period=route.get("arrival_date")))
            background.add_task(_guidance_build_bg, p.org_id, p.user_id, r.case_id,
                                route, key, g["guidance"])
            g["adapter_build"] = "scheduled"
        except Exception:  # noqa: BLE001 - build scheduling is best-effort
            pass
    g["intake_id"] = r.id
    return g


# ---- Continuation: guidance -> case (applicant journey, Part 3) -------------
# The primary "Continue" action after Kimi guidance. Creates (or reuses) the
# case, saves the guidance + route-specific checklist to it, carries the
# intake's confirmed passport document over, links the async official-source
# audit, and schedules adapter generation through the existing authorized
# bridge. Idempotent: a duplicate click returns the same case. NO administrator
# approval exists anywhere on this path.

def _continuation_summary(db, r, cg, case_row) -> dict:
    from . import kimi_primary
    from .. import checklist_intake
    status = checklist_intake.checklist_state(db, case_row, cg)
    # Serve-time normalization: stored two-pass-era guidance rows carry a label
    # claiming a retired second-pass check — it must never reach the UI.
    guidance = kimi_primary.normalize_guidance_label(cg.guidance)
    return {"case_id": case_row.id, "intake_id": r.id, "status": r.status,
            "case_state": case_row.state,
            "disposition": cg.disposition,
            "continuation_kind": cg.continuation_kind,
            "checklist": status["items"], "checklist_counts": status["counts"],
            "intake_stage": status["intake_stage"],
            "guidance": guidance,
            "verification": (guidance or {}).get("verification") or {}}


@router.post("/intake/{intake_id}/continue")
def continue_intake(intake_id: str, background: BackgroundTasks,
                    db=Depends(get_session), p: Principal = Depends(get_principal)):
    from .. import models as core_models
    from . import intake_flow, kimi_primary
    from .models import CaseRouteGuidance, RouteIntakeDocument
    from .registry import load_registry

    r = _owned_intake(db, p, intake_id)

    # Idempotent: already converted -> return the SAME case (duplicate clicks,
    # refresh, retries never create a second application).
    if r.case_id:
        case_row = db.get(core_models.VisaApplication, r.case_id)
        cg = db.execute(select(CaseRouteGuidance).where(
            CaseRouteGuidance.case_id == r.case_id)).scalars().first()
        if case_row is not None and cg is not None:
            out = _continuation_summary(db, r, cg, case_row)
            out["already_converted"] = True
            return out

    route = _guidance_route(r)
    try:
        g = kimi_primary.get_route_guidance(db, route)
    except kimi_primary.GuidanceUnavailable as e:
        raise HTTPException(503, detail={"status": kimi_primary.STATUS_UNAVAILABLE,
                                         "reason": str(e)})
    except kimi_primary.GuidanceTimeout:
        raise HTTPException(504, detail={"status": kimi_primary.STATUS_TIMEOUT,
                                         "reason": kimi_primary.TIMEOUT_MESSAGE})
    except kimi_primary.GuidanceProviderError as e:
        raise HTTPException(503, detail={"status": kimi_primary.STATUS_UNAVAILABLE,
                                         "reason": e.envelope.get("user_message"),
                                         "category": e.envelope.get("category")})
    meta = intake_flow.continuation_meta(g)
    if meta["blocked"]:
        # The precise unresolved blocker, never a silent dead-end.
        raise HTTPException(409, detail={"reason": "guidance_blocked",
                                         "blockers": meta["blockers"]})

    # The structured home address is mandatory before a case exists — visa
    # forms and portal account creation need it. Only the universal fields are
    # enforced (line 1 / city / country); region and postal code are optional
    # because many countries have neither.
    answers = dict(r.answers or {})
    address_missing = [k for k in ("address_line1", "address_city", "address_country")
                       if not str(answers.get(k) or "").strip()]
    if address_missing:
        raise HTTPException(422, detail={"reason": "address_required",
                                         "missing_fields": address_missing})
    full_name = str(answers.get("full_name") or "").strip()
    if not full_name:
        parts = [answers.get("given_names"), answers.get("surname")]
        full_name = " ".join(str(x) for x in parts if x).strip() or "Applicant"
    email = str(r.email or answers.get("email") or "")

    # Destination display name from the registry (case rows use country names).
    dest_code = str(answers.get("destination_country") or "")
    dest_name = dest_code
    for c in load_registry("countries")["entries"]:
        if dest_code in (c.get("alpha_3"), c.get("alpha_2")):
            dest_name = c.get("common_name") or c["name"]
            break

    applicant = core_models.Applicant(
        org_id=p.org_id, user_id=p.user_id, full_name=full_name, email=email,
        phone="", time_zone="UTC")
    db.add(applicant)
    db.flush()
    case_answers = dict(answers)
    case_answers.setdefault("full_name", full_name)
    case_answers.setdefault("email", email)
    # Canonical aliases the case pipeline reads (validity checks, adapters).
    alias = {"nationality": answers.get("passport_nationality"),
             "issuing_country": answers.get("passport_issuing_country"),
             "current_residence": answers.get("lawful_country_of_residence"),
             "expiry_date": answers.get("passport_expiry_date"),
             "intended_arrival": answers.get("arrival_date"),
             "intended_departure": answers.get("departure_date")}
    for k, v in alias.items():
        if v and not case_answers.get(k):
            case_answers[k] = v
    case_row = core_models.VisaApplication(
        org_id=p.org_id, user_id=p.user_id, applicant_id=applicant.id,
        destination_country=dest_name, visa_type="tourist", answers=case_answers)
    db.add(case_row)
    db.flush()

    # Carry the intake's passport document into the case (no re-upload). It
    # arrives pre-approved ONLY when the applicant demonstrably confirmed the
    # extracted profile — i.e. its prefill values were applied into the intake
    # answers ("Use these details"). Otherwise the document stays unapproved
    # and goes through the normal case-stage review like any upload.
    intake_docs = db.execute(select(RouteIntakeDocument).where(
        RouteIntakeDocument.intake_id == r.id)
        .order_by(RouteIntakeDocument.created_at.desc())).scalars().all()
    if intake_docs:
        d = intake_docs[0]
        pre = (d.passport_profile or {}).get("prefill") or {}
        confirmed = all(
            k in pre and str(answers.get(k, "")) == str(pre[k])
            for k in ("passport_number", "birth_date"))
        stored = core_models.StoredDocument(
            org_id=p.org_id, application_id=case_row.id, name=d.name,
            mime=d.mime, size_bytes=d.size_bytes, sha256=d.sha256,
            storage_ref=f"local://{d.sha256[:16]}", doc_type=d.doc_type,
            ocr_status="done", execution_class=d.execution_class,
            page_classification=d.page_classification,
            extracted_fields=d.extracted_fields,
            passport_profile=d.passport_profile,
            quality_warnings=d.quality_warnings, approved=confirmed)
        db.add(stored)
        db.flush()
        if d.content:
            db.add(core_models.DocumentBlob(document_id=stored.id, org_id=p.org_id,
                                            mime=d.mime, content=d.content))
            d.content = None   # bytes now live (and are erased) with the case
        # The applicant explicitly reviewed and applied the extracted profile
        # at Step 1 ("Use these details") — record that confirmation as the
        # passport requirement's submission. An unconfirmed carry-over is NOT
        # seeded: it goes through the normal upload → review → Submit flow.
        if confirmed:
            from .. import checklist_intake
            checklist_intake.seed_intake_confirmed_passport(
                db, org_id=p.org_id, application_id=case_row.id, document=stored)

    guidance_saved = dict(g)
    guidance_saved.pop("intake_id", None)
    checklist = intake_flow.derive_document_checklist(g.get("guidance") or {},
                                                     answers=case_answers)
    resolution = db.get(RouteResolution, r.resolution_id) if r.resolution_id else None
    cg = CaseRouteGuidance(
        org_id=p.org_id, case_id=case_row.id, intake_id=r.id,
        route_key=(resolution.route_key if resolution else ""),
        disposition=(g.get("guidance") or {}).get("disposition") or "",
        continuation_kind=meta["kind"], guidance=guidance_saved,
        checklist=checklist)
    db.add(cg)

    # Link the case everywhere the journey is tracked. No official-source
    # audit exists on this path — the Kimi two-pass result is authoritative.
    r.case_id = case_row.id
    r.status = "converted"
    if resolution is not None:
        resolution.case_id = case_row.id
    db.commit()

    audit.record(db, org_id=p.org_id, application_id=case_row.id,
                 action="route_intake_converted",
                 detail={"intake_id": r.id, "disposition": cg.disposition,
                         "continuation_kind": cg.continuation_kind,
                         "checklist_items": len(checklist),
                         "documents_carried": 1 if intake_docs else 0},
                 actor=p.user_id)
    audit.record(db, org_id=p.org_id, application_id=case_row.id,
                 action="case_created",
                 detail={"destination": dest_name, "via": "intake_continuation"},
                 actor=p.user_id)

    # Adapter generation through the existing authorized bridge (background;
    # the build pipeline's own gates + auto-release policy decide the rest).
    if (g.get("guidance") or {}).get("official_portal_url") and \
            g.get("status") == kimi_primary.STATUS_PRIMARY and resolution is not None:
        background.add_task(_guidance_build_bg, p.org_id, p.user_id, case_row.id,
                            route, resolution.route_key, g["guidance"])

    out = _continuation_summary(db, r, cg, case_row)
    out["already_converted"] = False
    return out


def _guidance_build_bg(org_id, user_id, case_id, route, key, guidance):  # pragma: no cover - thin wrapper
    from . import kimi_primary
    db = _new_session()
    try:
        kimi_primary.maybe_start_adapter_build(
            db, org_id=org_id, user_id=user_id, case_id=case_id,
            route=route, route_key=key, guidance=guidance)
    finally:
        db.close()


def _run_research_job_bg(job_id: str) -> None:  # pragma: no cover - thin wrapper
    from ..db import SessionLocal
    from . import ondemand
    db = SessionLocal()
    try:
        ondemand.run_job(db, job_id)
    finally:
        db.close()


@router.get("/research-jobs/{job_id}")
def get_research_job(job_id: str, db=Depends(get_session),
                     p: Principal = Depends(get_principal)):
    from .models import OnDemandRouteResearchJob
    job = db.get(OnDemandRouteResearchJob, job_id)
    if not job or job.org_id != p.org_id:
        raise HTTPException(404, "research job not found")
    return {"id": job.id, "status": job.status, "stage": job.stage,
            "route_key": job.route_key, "progress": job.progress,
            "counters": {k: v for k, v in (job.counters or {}).items()
                         if k in ("queries_used", "pages_fetched", "gov_candidates",
                                  "extraction_fields", "extraction_rejected",
                                  "disposition", "material_conflict")},
            "result": job.result, "researched_at": job.researched_at_date,
            "error": job.error or None}


@router.post("/research-jobs/{job_id}/resume")
def resume_research_job(job_id: str, background: BackgroundTasks,
                        db=Depends(get_session), p: Principal = Depends(get_principal)):
    """Developer/administrator tool only — never part of the applicant flow."""
    require_admin(p)
    from .models import OnDemandRouteResearchJob
    job = db.get(OnDemandRouteResearchJob, job_id)
    if not job:
        raise HTTPException(404, "research job not found")
    if job.status in ("complete", "failed", "conflicted"):
        return {"id": job.id, "status": job.status, "note": "job already finished"}
    background.add_task(_run_research_job_bg, job.id)
    return {"id": job.id, "status": "running", "stage": job.stage}


class AdminResearchBody(BaseModel):
    """Explicit developer/administrator request to research one exact route.
    This is the ONLY way an official-source research job is created — the
    applicant flow never starts one."""
    intake_id: str


@router.post("/admin/snapshot/research-jobs")
def admin_start_research(body: AdminResearchBody, background: BackgroundTasks,
                         db=Depends(get_session), p: Principal = Depends(get_principal)):
    require_admin(p)
    from . import ondemand
    r = db.get(RouteIntake, body.intake_id)
    if not r:
        raise HTTPException(404, "intake not found")
    res = db.get(RouteResolution, r.resolution_id) if r.resolution_id else None
    if not res or not res.route_key:
        raise HTTPException(409, "intake has no resolved route key — resolve it first")
    norm = (res.checks or {}).get("normalization", {}).get("normalized") or {}
    job = ondemand.create_job(
        db, org_id=r.org_id, user_id=r.user_id, intake_id=r.id, case_id=r.case_id,
        answers=r.answers, normalized=norm, key=res.route_key,
        language=r.preferred_language or "en")
    if job.status == "queued":
        background.add_task(_run_research_job_bg, job.id)
    return {"id": job.id, "status": job.status, "stage": job.stage,
            "route_key": job.route_key}


@router.get("/admin/snapshot/research-jobs")
def admin_research_jobs(db=Depends(get_session), p: Principal = Depends(get_principal)):
    require_admin(p)
    from .models import OnDemandRouteResearchJob
    rows = db.execute(select(OnDemandRouteResearchJob)
                      .order_by(OnDemandRouteResearchJob.created_at.desc())).scalars().all()
    return {"jobs": [{"id": j.id, "org_id": j.org_id, "route_key": j.route_key,
                      "status": j.status, "stage": j.stage, "attempts": j.attempts,
                      "researched_at": j.researched_at_date,
                      "kimi_model": j.kimi_model or None,
                      "created_at": str(j.created_at)} for j in rows[:200]]}


@router.get("/snapshot/route-evidence/{resolution_id}")
def route_evidence(resolution_id: str, db=Depends(get_session),
                   p: Principal = Depends(get_principal)):
    res = db.get(RouteResolution, resolution_id)
    if not res or res.org_id != p.org_id:
        raise HTTPException(404, "resolution not found")
    dest = (res.normalized_input or {}).get("destination_country", "")
    ev = db.execute(select(SourceEvidence).where(
        SourceEvidence.snapshot_date == SNAPSHOT_DATE,
        SourceEvidence.applicable_jurisdiction == dest,
        SourceEvidence.verification_status == "verified")
        .order_by(SourceEvidence.final_hostname)).scalars().all()
    return {"snapshot_date": SNAPSHOT_DATE, "disclaimer": DISCLAIMER_EN,
            "evidence": [{"final_url": e.final_url, "hostname": e.final_hostname,
                          "authority": e.source_authority, "retrieved_at": e.retrieved_at,
                          "excerpt": e.relevant_excerpt[:600],
                          "language": e.page_language,
                          "content_hash": e.content_hash} for e in ev[:40]]}


# ---- Resolvers (brief section 18) -------------------------------------------

@router.get("/snapshot/resolvers/portal")
def resolve_official_portal(destination: str, db=Depends(get_session),
                            p: Principal = Depends(get_principal)):
    """Official-portal resolver: only VERIFIED portals (official domain or an
    official page linking the exact contractor). Unverifiable -> honest empty
    list with MANUAL_REVIEW_REQUIRED, never a guess."""
    from .registry import RegistryError, normalize_country
    try:
        dest = normalize_country(destination, field="destination")
    except RegistryError as e:
        raise HTTPException(400, str(e))
    rows = db.execute(select(OfficialPortalRecord).where(
        OfficialPortalRecord.snapshot_date == SNAPSHOT_DATE,
        OfficialPortalRecord.destination_country == dest)).scalars().all()
    verified = [r for r in rows if r.verification_status in
                ("verified_official_domain", "verified_via_official_link")]
    return {
        "destination": dest, "snapshot_date": SNAPSHOT_DATE, "disclaimer": DISCLAIMER_EN,
        "status": "VERIFIED" if verified else "MANUAL_REVIEW_REQUIRED",
        "portals": [{"kind": r.portal_kind, "url": r.url, "operator": r.operator or None,
                     "operator_kind": r.operator_kind,
                     "verification_status": r.verification_status,
                     "official_linking_source": r.official_linking_source or None,
                     "hostnames": r.hostnames} for r in verified],
        "unverified_count": len(rows) - len(verified),
    }


@router.get("/snapshot/resolvers/jurisdiction")
def resolve_consular_jurisdiction(destination: str, residence: str,
                                  db=Depends(get_session),
                                  p: Principal = Depends(get_principal)):
    """Consular-jurisdiction resolver. Never inferred from geographic
    proximity: only verified snapshot rules backed by official evidence.
    Unverifiable -> MANUAL_REVIEW_REQUIRED."""
    from .registry import RegistryError, normalize_country
    try:
        dest = normalize_country(destination, field="destination")
        res = normalize_country(residence, field="residence")
    except RegistryError as e:
        raise HTTPException(400, str(e))
    rule = db.execute(select(ConsularJurisdictionRule).where(
        ConsularJurisdictionRule.snapshot_date == SNAPSHOT_DATE,
        ConsularJurisdictionRule.destination_country == dest,
        ConsularJurisdictionRule.residence_jurisdiction == res)).scalars().first()
    if not rule or rule.verification_status != "verified":
        return {"destination": dest, "residence": res, "snapshot_date": SNAPSHOT_DATE,
                "status": "MANUAL_REVIEW_REQUIRED",
                "reason": ("jurisdiction rule not verified in the snapshot"
                           if rule else "no jurisdiction rule in the snapshot"),
                "disclaimer": DISCLAIMER_EN}
    return {"destination": dest, "residence": res, "snapshot_date": SNAPSHOT_DATE,
            "status": "VERIFIED",
            "competent_post": {"name": rule.competent_post_name,
                               "kind": rule.competent_post_kind,
                               "url": rule.competent_post_url or None},
            "covers_nationalities": rule.covers_nationalities or None,
            "conditions": rule.conditions or None,
            "evidence": rule.evidence_ids or [], "disclaimer": DISCLAIMER_EN}


# ---- Administration (brief section 27) --------------------------------------

@router.get("/admin/snapshot/coverage")
def admin_coverage(db=Depends(get_session), p: Principal = Depends(get_principal)):
    require_admin(p)
    from .coverage import report
    return report(db)


@router.get("/admin/snapshot/batches")
def admin_batches(db=Depends(get_session), p: Principal = Depends(get_principal)):
    require_admin(p)
    rows = db.execute(select(SnapshotResearchBatch).where(
        SnapshotResearchBatch.snapshot_date == SNAPSHOT_DATE)
        .order_by(SnapshotResearchBatch.batch_key)).scalars().all()
    return {"batches": [{"key": b.batch_key, "destination": b.destination_country,
                         "stage": b.stage, "result": b.result, "attempts": b.attempt_count,
                         "records": b.record_count, "conflicts": b.conflict_count,
                         "reviews": b.review_count} for b in rows]}


@router.get("/admin/snapshot/review-queue")
def admin_review_queue(db=Depends(get_session), p: Principal = Depends(get_principal)):
    require_admin(p)
    rows = db.execute(select(HumanReviewTask).where(HumanReviewTask.status == "open")
                      .order_by(HumanReviewTask.created_at)).scalars().all()
    return {"tasks": [{"id": t.id, "kind": t.kind, "subject": t.subject_id,
                       "title": t.title, "created_at": str(t.created_at)} for t in rows]}


class ReviewResolveBody(BaseModel):
    resolution_note: str
    status: str = "resolved"   # resolved|rejected


@router.post("/admin/snapshot/review-queue/{task_id}/resolve")
def admin_resolve_review(task_id: str, body: ReviewResolveBody, db=Depends(get_session),
                         p: Principal = Depends(get_principal)):
    require_admin(p)
    t = db.get(HumanReviewTask, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    if body.status not in ("resolved", "rejected"):
        raise HTTPException(400, "status must be resolved|rejected")
    # Only an authenticated human administrator reaches this handler; the
    # actor is recorded immutably. An AI model has no admin credential.
    t.status = body.status
    t.resolved_by = p.user_id
    t.resolution_note = body.resolution_note
    db.commit()
    audit.record(db, org_id=p.org_id, application_id="", action="snapshot_review_resolved",
                 detail={"task_id": t.id, "status": body.status}, actor=p.user_id)
    return {"id": t.id, "status": t.status}


@router.get("/admin/snapshot/conflicts")
def admin_conflicts(db=Depends(get_session), p: Principal = Depends(get_principal)):
    require_admin(p)
    rows = db.execute(select(SnapshotConflict).where(
        SnapshotConflict.status == "open")).scalars().all()
    return {"conflicts": [{"id": c.id, "destination": c.destination_country,
                           "field": c.field, "values": c.values} for c in rows]}


@router.get("/admin/snapshot/route-queue")
def admin_route_queue(db=Depends(get_session), p: Principal = Depends(get_principal)):
    require_admin(p)
    rows = db.execute(select(RouteResolution)
                      .order_by(RouteResolution.created_at.desc())).scalars().all()
    return {"resolutions": [{"id": r.id, "org_id": r.org_id, "route_key": r.route_key,
                             "readiness": r.readiness_status, "case_id": r.case_id,
                             "created_at": str(r.created_at)} for r in rows[:200]]}


@router.get("/admin/snapshot/adapter-tasks")
def admin_adapter_tasks(db=Depends(get_session), p: Principal = Depends(get_principal)):
    require_admin(p)
    rows = db.execute(select(AdapterDevelopmentTask)
                      .order_by(AdapterDevelopmentTask.priority,
                                AdapterDevelopmentTask.created_at)).scalars().all()
    return {"tasks": [{"id": t.id, "route_key": t.route_key, "status": t.status,
                       "priority": t.priority, "case_id": t.case_id,
                       "portal_evidence": t.portal_evidence,
                       "jurisdiction_evidence": t.jurisdiction_evidence,
                       "notes": t.notes} for t in rows]}


@router.get("/admin/snapshot/routes/{route_key:path}/history")
def admin_route_history(route_key: str, db=Depends(get_session),
                        p: Principal = Depends(get_principal)):
    require_admin(p)
    head = db.execute(select(VisaRoute).where(
        VisaRoute.route_key == route_key)).scalar_one_or_none()
    if not head:
        raise HTTPException(404, "route not found")
    versions = db.execute(select(VisaRouteVersion).where(
        VisaRouteVersion.route_id == head.id)
        .order_by(VisaRouteVersion.version)).scalars().all()
    return {"route_key": route_key, "current_version": head.current_version,
            "versions": [{"version": v.version, "content_hash": v.content_hash,
                          "record": v.record} for v in versions]}


class ReverifyBody(BaseModel):
    """Manual live reverification evidence (brief section 4): an authenticated
    admin confirms the ACTIVE route/portal/fee immediately before any
    irreversible action. Stored as fresh SourceEvidence; never automatic."""
    destination_country: str
    urls: list[str]
    note: str
    fee_confirmed: bool = False
    portal_confirmed: bool = False


@router.post("/admin/snapshot/reverify")
def admin_manual_reverify(body: ReverifyBody, db=Depends(get_session),
                          p: Principal = Depends(get_principal)):
    require_admin(p)
    from .authority import hostname, is_government_host
    from .registry import normalize_country
    try:
        dest = normalize_country(body.destination_country, field="destination")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"unknown destination: {e}")
    stored = []
    for url in body.urls[:10]:
        host = hostname(url)
        ev = SourceEvidence(
            snapshot_date=SNAPSHOT_DATE, search_query="manual_reverification",
            original_url=url, final_url=url, final_hostname=host,
            source_authority="human_reviewed_evidence",
            applicable_jurisdiction=dest,
            relevant_excerpt=f"MANUAL REVERIFICATION by admin: {body.note[:500]}",
            retrieved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            verification_status="verified" if is_government_host(host) else "unverified",
            reviewer_status="human_verified",
        )
        db.add(ev)
        stored.append({"url": url, "government_domain": is_government_host(host)})
    db.commit()
    audit.record(db, org_id=p.org_id, application_id="", action="manual_reverification",
                 detail={"destination": dest, "urls": [s["url"] for s in stored],
                         "fee_confirmed": body.fee_confirmed,
                         "portal_confirmed": body.portal_confirmed}, actor=p.user_id)
    return {"destination": dest, "stored": stored, "note": "manual reverification recorded"}
