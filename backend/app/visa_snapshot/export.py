"""Export the snapshot to the required JSONL/JSON files (brief section 5)."""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from . import SNAPSHOT_DATE
from .coverage import report as coverage_report
from .models import (ConsularJurisdictionRule, HumanReviewTask, OfficialPortalRecord,
                     SnapshotConflict, SnapshotResearchBatch, SourceEvidence,
                     VisaFeeVersion, VisaPolicy, VisaPolicyVersion, VisaRoute,
                     VisaRouteVersion)
from .registry import REPO_ROOT, SNAPSHOT_DIR


def _w(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    return len(rows)


def export_all(db, *, git_commit: str = "") -> dict:
    out = SNAPSHOT_DIR / SNAPSHOT_DATE
    written = {}

    routes = []
    for head in db.execute(select(VisaRoute).where(
            VisaRoute.snapshot_date == SNAPSHOT_DATE)).scalars():
        v = db.execute(select(VisaRouteVersion).where(
            VisaRouteVersion.route_id == head.id,
            VisaRouteVersion.version == head.current_version)).scalar_one_or_none()
        if v:
            routes.append(v.record)
    written["global_visa_routes.jsonl"] = _w(out / "global_visa_routes.jsonl", routes)
    # Operational top-level copy (validated JSONL, not a free-form txt).
    written["data/global_visa_routes.jsonl"] = _w(REPO_ROOT / "data" / "global_visa_routes.jsonl", routes)

    policies = []
    for head in db.execute(select(VisaPolicy).where(
            VisaPolicy.snapshot_date == SNAPSHOT_DATE)).scalars():
        v = db.execute(select(VisaPolicyVersion).where(
            VisaPolicyVersion.policy_id == head.id,
            VisaPolicyVersion.version == head.current_version)).scalar_one_or_none()
        if v:
            rec = dict(v.record)
            rec.setdefault("policy_id", head.policy_uid)
            rec.setdefault("version", v.version)
            rec.setdefault("content_hash", v.content_hash)
            policies.append(rec)
    written["global_visa_policies.jsonl"] = _w(out / "global_visa_policies.jsonl", policies)

    fees = []
    for f in db.execute(select(VisaFeeVersion).where(
            VisaFeeVersion.snapshot_date == SNAPSHOT_DATE)).scalars():
        rec = dict(f.record)
        rec.setdefault("fee_id", f.fee_uid)
        rec.setdefault("version", f.version)
        rec.setdefault("content_hash", f.content_hash)
        rec.setdefault("fee_status", f.fee_status)
        fees.append(rec)
    written["global_fee_schedules.jsonl"] = _w(out / "global_fee_schedules.jsonl", fees)

    portals = [{
        "snapshot_date": SNAPSHOT_DATE, "portal_id": p.portal_uid, "version": 1,
        "destination_country": p.destination_country, "portal_kind": p.portal_kind,
        "operator": p.operator or None, "operator_kind": p.operator_kind,
        "url": p.url, "hostnames": p.hostnames,
        "allowed_redirect_hosts": p.allowed_redirect_hosts,
        "official_linking_source": p.official_linking_source or None,
        "supported_categories": p.supported_categories,
        "verification_status": p.verification_status,
        "verification_evidence_urls": p.evidence_ids, "content_hash": "0" * 64,
    } for p in db.execute(select(OfficialPortalRecord).where(
        OfficialPortalRecord.snapshot_date == SNAPSHOT_DATE)).scalars()]
    from .routekey import canonical_hash
    for p in portals:
        p["content_hash"] = canonical_hash(p)
    written["global_portals.jsonl"] = _w(out / "global_portals.jsonl", portals)

    jurs = [{
        "snapshot_date": SNAPSHOT_DATE, "jurisdiction_id": j.id, "version": 1,
        "destination_country": j.destination_country,
        "residence_jurisdiction": j.residence_jurisdiction,
        "residence_subdivisions": j.residence_subdivisions or None,
        "competent_post_name": j.competent_post_name or None,
        "competent_post_kind": j.competent_post_kind,
        "competent_post_url": j.competent_post_url or None,
        "covers_nationalities": j.covers_nationalities or None,
        "conditions": j.conditions or None,
        "verification_status": j.verification_status,
        "official_source_urls": j.evidence_ids or None, "content_hash": "0" * 64,
    } for j in db.execute(select(ConsularJurisdictionRule).where(
        ConsularJurisdictionRule.snapshot_date == SNAPSHOT_DATE)).scalars()]
    for j in jurs:
        j["content_hash"] = canonical_hash(j)
    written["global_consular_jurisdictions.jsonl"] = _w(
        out / "global_consular_jurisdictions.jsonl", jurs)

    evidence = [{
        "snapshot_date": SNAPSHOT_DATE, "evidence_id": e.id,
        "search_query": e.search_query or None, "original_url": e.original_url,
        "final_url": e.final_url, "redirect_chain": e.redirect_chain or None,
        "final_hostname": e.final_hostname, "source_authority": e.source_authority,
        "applicable_jurisdiction": e.applicable_jurisdiction or None,
        "relevant_excerpt": e.relevant_excerpt or None, "retrieved_at": e.retrieved_at,
        "effective_date_evidence": e.effective_date_evidence or None,
        "content_hash": e.content_hash, "page_language": e.page_language or None,
        "verification_status": e.verification_status, "reviewer_status": e.reviewer_status,
    } for e in db.execute(select(SourceEvidence).where(
        SourceEvidence.snapshot_date == SNAPSHOT_DATE)).scalars()]
    written["global_source_evidence.jsonl"] = _w(out / "global_source_evidence.jsonl", evidence)

    backlog = [{
        "snapshot_date": SNAPSHOT_DATE, "task_id": t.id, "kind": t.kind,
        "subject": t.subject_id, "title": t.title, "status": t.status,
    } for t in db.execute(select(HumanReviewTask).where(
        HumanReviewTask.snapshot_date == SNAPSHOT_DATE)).scalars()]
    written["research_backlog.jsonl"] = _w(out / "research_backlog.jsonl", backlog)

    conflicts = [{
        "snapshot_date": SNAPSHOT_DATE, "conflict_id": c.id,
        "destination_country": c.destination_country, "route_key": c.route_key or None,
        "field": c.field, "values": c.values, "status": c.status,
        "resolution": c.resolution or None,
    } for c in db.execute(select(SnapshotConflict).where(
        SnapshotConflict.snapshot_date == SNAPSHOT_DATE)).scalars()]
    written["conflicts.jsonl"] = _w(out / "conflicts.jsonl", conflicts)

    cov = coverage_report(db, git_commit=git_commit)
    (out / "coverage_report.json").write_text(
        json.dumps(cov, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    written["coverage_report.json"] = 1

    batches = [{"batch_key": b.batch_key, "stage": b.stage, "result": b.result,
                "attempts": b.attempt_count, "records": b.record_count,
                "conflicts": b.conflict_count, "reviews": b.review_count}
               for b in db.execute(select(SnapshotResearchBatch).where(
                   SnapshotResearchBatch.snapshot_date == SNAPSHOT_DATE)).scalars()]
    (out / "import_report.json").write_text(json.dumps({
        "snapshot_date": SNAPSHOT_DATE, "written": written, "batches_total": len(batches),
        "batches_complete": sum(1 for b in batches if b["result"] == "complete"),
        "batches_awaiting_input": sum(1 for b in batches if b["result"] == "awaiting_research_input"),
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    written["import_report.json"] = 1
    return written
