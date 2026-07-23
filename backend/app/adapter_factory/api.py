"""Adapter-factory HTTP API (brief §33).

Applicant endpoints: request a build, record consent, view build progress
(simple labels only) — never adapter code or portal internals.

Admin endpoints: list candidates, inspect the evidence package + sanitized
diff, release (sandbox/staging/production), reject/request-changes, quarantine,
disable, roll back, view failures + release history. Role-protected; Kimi and
generated code never hold admin credentials.

Internal worker endpoints: resume a build (idempotent).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..db import get_session
from ..security import Principal, get_principal, require_admin
from ..config import settings
from .. import models, audit
from . import models as fm
from . import build_workflow, generator, release as releasesvc, recon
from .build_workflow import (create_request, record_consent, run_build,
                             status_for_applicant, CONSENT_DISCLOSURES,
                             CONSENT_TEXT_VERSION)

router = APIRouter()


# ---------------------------------------------------------------- applicant --
class BuildRequestBody(BaseModel):
    application_id: str = ""
    route_key: str
    destination: str = ""
    visa_type: str = "tourist"
    portal_evidence: dict = {}
    jurisdiction_evidence: dict = {}
    standing_authorization_id: str = ""


class ConsentBody(BaseModel):
    locale: str = "en"


def _synthetic_observer(hostnames):
    """Only in mock/test runtime modes may a synthetic portal stand in for
    live reconnaissance — never in staging/production (fail closed)."""
    if not settings().mock_portal_allowed:
        return None
    from ..portal.synthetic import SyntheticPortal
    host = (hostnames or ["portal.gov.example"])[0]
    return SyntheticPortal(scenario="single_step_login", hostname=host).observe


def _live_observer(hostnames):
    """Credential-free live reconnaissance over Browserbase — real modes only,
    and only when a Browserbase key is configured. Never authenticates."""
    from ..providers import browser as bb
    if settings().mock_portal_allowed or not bb.is_configured():
        return None
    from ..portal.live_browser import build_observer_factory
    return build_observer_factory([h for h in (hostnames or []) if h])


def _observer_for(hostnames):
    """Pick the reconnaissance observer for the current runtime mode:
    synthetic in mock/test modes, live Browserbase in real modes when
    configured, else None (the build parks at MANUAL_REVIEW honestly)."""
    return _synthetic_observer(hostnames) or _live_observer(hostnames)


@router.get("/adapter-build/consent-copy")
def build_consent_copy(_: Principal = Depends(get_principal)):
    return {"text_version": CONSENT_TEXT_VERSION, "disclosures": CONSENT_DISCLOSURES}


@router.post("/adapter-build/request")
def request_build(body: BuildRequestBody, db=Depends(get_session),
                  p: Principal = Depends(get_principal)):
    req = create_request(
        db, org_id=p.org_id, user_id=p.user_id, application_id=body.application_id,
        route_key=body.route_key, destination=body.destination, visa_type=body.visa_type,
        portal_evidence=body.portal_evidence, runtime_mode=settings().runtime_mode,
        standing_authorization_id=body.standing_authorization_id)
    if body.jurisdiction_evidence:
        req.jurisdiction_evidence = body.jurisdiction_evidence
        db.commit()
    return status_for_applicant(req)


@router.post("/adapter-build/{request_id}/consent")
def consent_build(request_id: str, body: ConsentBody, db=Depends(get_session),
                  p: Principal = Depends(get_principal)):
    req = _owned_build(db, p, request_id)
    record_consent(db, req, user_id=p.user_id, locale=body.locale)
    # Advance the build as far as it can go without further human input.
    observer = _observer_for((req.portal_evidence or {}).get("hostnames", []))
    run_build(db, req.id, observer=observer)
    db.refresh(req)
    return status_for_applicant(req)


@router.get("/adapter-build/{request_id}")
def get_build_status(request_id: str, db=Depends(get_session),
                     p: Principal = Depends(get_principal)):
    req = _owned_build(db, p, request_id)
    return status_for_applicant(req)


@router.post("/adapter-build/{request_id}/resume")
def resume_build(request_id: str, db=Depends(get_session),
                 p: Principal = Depends(get_principal)):
    """Idempotent resume — safe after any restart; re-checks each stage."""
    req = _owned_build(db, p, request_id)
    if req.consent_given:
        observer = _observer_for((req.portal_evidence or {}).get("hostnames", []))
        run_build(db, req.id, observer=observer)
        db.refresh(req)
    return status_for_applicant(req)


# -------------------------------------------------------------------- admin --
class ReleaseBody(BaseModel):
    candidate_id: str
    version: int
    tier: str
    kind: str = "human_admin"


class QuarantineBody(BaseModel):
    candidate_id: str
    version: int
    reason: str = ""


class KillBody(BaseModel):
    candidate_id: str
    reason: str = ""


class RollbackBody(BaseModel):
    candidate_id: str
    tier: str
    reason: str = ""


class ReviewResolveBody(BaseModel):
    note: str
    decision: str = "resolved"   # resolved | rejected


@router.get("/admin/adapter-factory/queue")
def admin_build_queue(db=Depends(get_session), p: Principal = Depends(get_principal)):
    require_admin(p)
    reqs = db.execute(select(fm.AdapterBuildRequest).order_by(
        fm.AdapterBuildRequest.created_at.desc())).scalars().all()
    return {"builds": [{
        "id": r.id, "route_key": r.route_key, "destination": r.destination,
        "visa_type": r.visa_type, "state": r.state,
        "candidate_id": r.current_candidate_id,
        "consented": r.consent_given, "attempts": r.attempts,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None}
        for r in reqs]}


@router.get("/admin/adapter-factory/candidates")
def admin_candidates(db=Depends(get_session), p: Principal = Depends(get_principal)):
    require_admin(p)
    cands = db.execute(select(fm.AdapterCandidate).order_by(
        fm.AdapterCandidate.created_at.desc())).scalars().all()
    return {"candidates": [{
        "id": c.id, "adapter_id": c.adapter_id, "route_key": c.route_key,
        "status": c.status, "current_version": c.current_version} for c in cands]}


@router.get("/admin/adapter-factory/candidates/{candidate_id}/evidence")
def admin_candidate_evidence(candidate_id: str, version: Optional[int] = None,
                             db=Depends(get_session),
                             p: Principal = Depends(get_principal)):
    require_admin(p)
    cand = db.get(fm.AdapterCandidate, candidate_id)
    if not cand:
        raise HTTPException(404, "candidate not found")
    v = version or cand.current_version
    row = generator.get_version(db, candidate_id, v)
    if not row:
        raise HTTPException(404, "version not found")
    pkg = releasesvc.evidence_package(db, row)
    # Sanitized code diff = the typed flow itself (data, never secrets).
    return {"candidate_id": candidate_id, "version": v, "evidence": pkg,
            "flow": row.flow, "field_mappings": row.field_mappings,
            "document_mappings": row.document_mappings,
            "known_limitations": row.known_limitations,
            "quarantined": row.quarantined,
            "quarantine_reason": row.quarantine_reason}


@router.post("/admin/adapter-factory/release")
def admin_release(body: ReleaseBody, db=Depends(get_session),
                  p: Principal = Depends(get_principal)):
    require_admin(p)
    try:
        rel = releasesvc.release(db, candidate_id=body.candidate_id, version=body.version,
                                 tier=body.tier, actor=p.user_id, is_admin=True,
                                 kind="human_admin")
    except releasesvc.ReleaseRefused as e:
        raise HTTPException(409, str(e))
    return {"release_id": rel.id, "tier": rel.tier, "version": rel.candidate_version,
            "route_key": rel.route_key}


@router.post("/admin/adapter-factory/quarantine")
def admin_quarantine(body: QuarantineBody, db=Depends(get_session),
                     p: Principal = Depends(get_principal)):
    require_admin(p)
    try:
        releasesvc.quarantine(db, candidate_id=body.candidate_id, version=body.version,
                              actor=p.user_id, reason=body.reason)
    except releasesvc.ReleaseRefused as e:
        raise HTTPException(409, str(e))
    return {"quarantined": True}


@router.post("/admin/adapter-factory/kill")
def admin_kill(body: KillBody, db=Depends(get_session),
               p: Principal = Depends(get_principal)):
    require_admin(p)
    releasesvc.engage_kill_switch(db, candidate_id=body.candidate_id, actor=p.user_id,
                                  is_admin=True, reason=body.reason)
    return {"killed": True}


@router.post("/admin/adapter-factory/clear-kill")
def admin_clear_kill(body: KillBody, db=Depends(get_session),
                     p: Principal = Depends(get_principal)):
    require_admin(p)
    releasesvc.clear_kill_switch(db, candidate_id=body.candidate_id, actor=p.user_id,
                                 is_admin=True)
    return {"killed": False}


@router.post("/admin/adapter-factory/rollback")
def admin_rollback(body: RollbackBody, db=Depends(get_session),
                   p: Principal = Depends(get_principal)):
    require_admin(p)
    try:
        rb = releasesvc.rollback(db, candidate_id=body.candidate_id, tier=body.tier,
                                 actor=p.user_id, is_admin=True, reason=body.reason)
    except releasesvc.ReleaseRefused as e:
        raise HTTPException(409, str(e))
    return {"from_version": rb.from_version, "to_version": rb.to_version, "tier": rb.tier}


@router.get("/admin/adapter-factory/review-tasks")
def admin_review_tasks(db=Depends(get_session), p: Principal = Depends(get_principal)):
    require_admin(p)
    tasks = db.execute(select(fm.AdapterReviewTask).where(
        fm.AdapterReviewTask.status == "open").order_by(
        fm.AdapterReviewTask.created_at.desc())).scalars().all()
    return {"tasks": [{"id": t.id, "candidate_id": t.candidate_id, "kind": t.kind,
                       "reason": t.reason, "candidate_version": t.candidate_version}
                      for t in tasks]}


@router.post("/admin/adapter-factory/review-tasks/{task_id}/resolve")
def admin_resolve_review(task_id: str, body: ReviewResolveBody, db=Depends(get_session),
                         p: Principal = Depends(get_principal)):
    require_admin(p)
    if not body.note.strip():
        raise HTTPException(422, "a resolution note is required")
    task = db.get(fm.AdapterReviewTask, task_id)
    if not task:
        raise HTTPException(404, "task not found")
    task.status = "resolved" if body.decision == "resolved" else "rejected"
    task.resolved_by = p.user_id
    task.resolution_note = body.note[:400]
    db.commit()
    audit.record(db, org_id="platform", application_id=task.candidate_id,
                 action="adapter_review_resolved",
                 detail={"task": task_id, "decision": task.status}, actor=p.user_id)
    return {"status": task.status}


@router.get("/admin/adapter-factory/failures")
def admin_failures(db=Depends(get_session), p: Principal = Depends(get_principal)):
    require_admin(p)
    fails = db.execute(select(fm.AdapterFailure).order_by(
        fm.AdapterFailure.created_at.desc()).limit(100)).scalars().all()
    return {"failures": [{"candidate_id": f.candidate_id, "version": f.candidate_version,
                          "node_id": f.node_id, "failure_class": f.failure_class,
                          "detail": f.sanitized_detail} for f in fails]}


@router.get("/admin/adapter-factory/releases")
def admin_release_history(db=Depends(get_session), p: Principal = Depends(get_principal)):
    require_admin(p)
    rels = db.execute(select(fm.AdapterRelease).order_by(
        fm.AdapterRelease.created_at.desc()).limit(100)).scalars().all()
    return {"releases": [{"id": r.id, "candidate_id": r.candidate_id,
                          "version": r.candidate_version, "tier": r.tier,
                          "route_key": r.route_key, "released_by": r.released_by,
                          "kind": r.release_kind, "active": r.active,
                          "at": r.created_at.isoformat() if r.created_at else None}
                         for r in rels]}


# ---------------------------------------------------------------- internals --
def _owned_build(db, p: Principal, request_id: str) -> fm.AdapterBuildRequest:
    req = db.get(fm.AdapterBuildRequest, request_id)
    if req is None:
        raise HTTPException(404, "build request not found")
    if req.org_id != p.org_id:
        raise HTTPException(403, "not your build request")
    return req
