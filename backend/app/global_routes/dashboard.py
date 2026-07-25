"""Developer-facing global coverage dashboard (single aggregated payload).

Counts are computed live from the pair-policy layer, the portal-family
registry, the family adapter links and the orchestrator task log. The honesty
split is structural: released coverage counts ONLY verified released adapters,
verified released non-portal workflows and verified legally-unavailable
results; provisional reference-dataset rows are reported separately and never
inflate coverage.
"""
from __future__ import annotations

from sqlalchemy import func, select

from ..visa_snapshot import SNAPSHOT_DATE
from ..visa_snapshot.matrix import completeness
from .models import (FamilyAdapterLink, GlobalBuildRun, GlobalBuildTask,
                     PortalFamily, RoutePairPolicy)

_COVERED_STATUSES = ("released_adapter", "released_workflow", "legally_unavailable")


def _count_by(db, column) -> dict:
    return {k: c for k, c in db.execute(
        select(column, func.count()).group_by(column)).all()}


def coverage(db) -> dict:
    total = db.execute(select(func.count(RoutePairPolicy.id))).scalar_one()
    by_outcome = _count_by(db, RoutePairPolicy.route_outcome)
    by_release = _count_by(db, RoutePairPolicy.release_status)
    by_verification = _count_by(db, RoutePairPolicy.verification_status)
    by_source = _count_by(db, RoutePairPolicy.source)

    covered = sum(by_release.get(s, 0) for s in _COVERED_STATUSES)
    mapped = total - by_release.get("unresolved", 0)

    fam_by_status = _count_by(db, PortalFamily.verification_status)
    fam_total = db.execute(select(func.count(PortalFamily.id))).scalar_one()
    fam_stale = db.execute(select(func.count(PortalFamily.id)).where(
        PortalFamily.stale.is_(True))).scalar_one()

    links = db.execute(select(FamilyAdapterLink)).scalars().all()
    adapters = {
        "total": len(links),
        "released": sum(1 for l in links if l.released),
        "building": sum(1 for l in links if not l.released and l.status not in
                        ("released", "failed") and not l.last_error),
        "failed_gates": sum(1 for l in links if not l.released and l.last_error),
        "failure_reasons": {l.family_id: l.last_error for l in links
                            if not l.released and l.last_error},
    }

    task_counts = {f"{k}:{s}": c for k, s, c in db.execute(
        select(GlobalBuildTask.kind, GlobalBuildTask.status, func.count())
        .group_by(GlobalBuildTask.kind, GlobalBuildTask.status)).all()}
    failed_tasks = [
        {"kind": t.kind, "subject": t.subject, "error_class": t.error_class,
         "error": t.error[:300]}
        for t in db.execute(select(GlobalBuildTask).where(
            GlobalBuildTask.status == "failed")
            .order_by(GlobalBuildTask.updated_at.desc()).limit(50)).scalars()]

    last_run = db.execute(select(GlobalBuildRun)
                          .order_by(GlobalBuildRun.created_at.desc())).scalars().first()

    last_verified = db.execute(select(func.max(PortalFamily.last_verified_at))).scalar()

    electronic = (by_outcome.get("ELECTRONIC_AUTHORIZATION", 0) +
                  by_outcome.get("EVISA", 0))
    physical = (by_outcome.get("EMBASSY_OR_CONSULATE_APPLICATION", 0) +
                by_outcome.get("AUTHORIZED_VISA_CENTER", 0) +
                by_outcome.get("MAIL_APPLICATION", 0) +
                by_outcome.get("APPOINTMENT_REQUIRED", 0) +
                by_outcome.get("REQUIRES_MANUAL_JURISDICTION_SELECTION", 0))

    return {
        "snapshot_date": SNAPSHOT_DATE,
        "totals": {
            "route_pair_combinations": total,
            "mapped_combinations": mapped,
            "released_combinations": covered,
            "coverage_released_pct": round(covered / total, 6) if total else 0.0,
            "mapped_pct": round(mapped / total, 6) if total else 0.0,
        },
        "by_outcome": {
            "visa_exempt": by_outcome.get("VISA_EXEMPT", 0),
            "entry_preparation": by_outcome.get("ENTRY_PREPARATION", 0),
            "electronic_routes": electronic,
            "visa_on_arrival": by_outcome.get("VISA_ON_ARRIVAL", 0),
            "physical_or_manual_routes": physical,
            "no_available_tourist_route": by_outcome.get("NO_AVAILABLE_TOURIST_ROUTE", 0),
            "unresolved": by_outcome.get("UNRESOLVED", 0),
            "raw": by_outcome,
        },
        "by_release_status": by_release,
        "by_verification": by_verification,
        "by_source": by_source,
        "portal_families": {
            "total": fam_total,
            "by_verification": fam_by_status,
            "stale": fam_stale,
        },
        "adapters": adapters,
        "build_tasks": task_counts,
        "failed_tasks": failed_tasks,
        "last_run": ({"id": last_run.id, "command": last_run.command,
                      "status": last_run.status,
                      "stats": last_run.stats,
                      "updated_at": str(last_run.updated_at)} if last_run else None),
        "last_family_verification": str(last_verified) if last_verified else None,
        "matrix": completeness(db),
        "honesty_note": ("released_combinations counts ONLY verified released "
                         "adapters, verified released non-portal workflows and "
                         "verified legally-unavailable results. Provisional "
                         "reference-dataset rows are never counted as coverage."),
    }


def unsupported(db, limit: int = 500) -> dict:
    from .orchestrator import unsupported_routes
    rows = unsupported_routes(db, limit=limit)
    total_unresolved = db.execute(select(func.count(RoutePairPolicy.id)).where(
        RoutePairPolicy.release_status == "unresolved")).scalar_one()
    total_unavailable = db.execute(select(func.count(RoutePairPolicy.id)).where(
        RoutePairPolicy.release_status == "legally_unavailable")).scalar_one()
    return {"unresolved_total": total_unresolved,
            "legally_unavailable_total": total_unavailable,
            "sample": rows}
