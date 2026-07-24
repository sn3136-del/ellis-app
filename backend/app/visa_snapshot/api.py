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

    # Strategy: cached on-demand research. When the exact route is missing/
    # incomplete (NOT a material conflict awaiting human review), start a
    # focused research job for THIS route only and report it to the UI.
    checks = result.get("checks", {})
    sr = checks.get("snapshot_resolution", {})
    needs_research = (result["readiness_status"] == "NOT_READY"
                      and result.get("route_key")
                      and sr.get("disposition") in ("RESEARCH_INCOMPLETE", None)
                      and not checks.get("conflicts", {}).get("route_conflicted"))
    if needs_research:
        from . import ondemand
        norm = checks.get("normalization", {}).get("normalized") or {}
        job = ondemand.create_job(
            db, org_id=p.org_id, user_id=p.user_id, intake_id=r.id, case_id=r.case_id,
            answers=r.answers, normalized=norm, key=result["route_key"],
            language=r.preferred_language or "en")
        # The official-source job is the asynchronous AUDIT — it never blocks
        # the applicant; Kimi-primary guidance (below) drives the flow now.
        result["research_job"] = {"id": job.id, "status": job.status, "stage": job.stage,
                                  "role": "async_official_source_audit"}
        if job.status == "queued":
            background.add_task(_run_research_job_bg, job.id)

    # Kimi-primary immediate guidance: attach instantly when cached; otherwise
    # the UI calls POST /intake/{id}/guidance (one structured Kimi call).
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
    """Immediate Kimi-primary route guidance (status KIMI_PRIMARY, clearly
    labeled AI-generated). Cached identical routes return instantly; a new route
    makes ONE structured Kimi call (with one targeted retry). Never blocks on —
    or starts — broad research; the official-source audit runs separately."""
    r = _owned_intake(db, p, intake_id)
    from . import kimi_primary
    route = _guidance_route(r)
    try:
        g = kimi_primary.get_route_guidance(db, route)
    except kimi_primary.GuidanceUnavailable as e:
        raise HTTPException(503, detail={"status": kimi_primary.STATUS_UNAVAILABLE,
                                         "reason": str(e)})
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
    from .models import OnDemandRouteResearchJob
    job = db.get(OnDemandRouteResearchJob, job_id)
    if not job or job.org_id != p.org_id:
        raise HTTPException(404, "research job not found")
    if job.status in ("complete", "failed", "conflicted"):
        return {"id": job.id, "status": job.status, "note": "job already finished"}
    background.add_task(_run_research_job_bg, job.id)
    return {"id": job.id, "status": "running", "stage": job.stage}


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
