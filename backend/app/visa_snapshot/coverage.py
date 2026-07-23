"""Separate, honest coverage metrics (brief section 8).

MATRIX_COMPLETENESS is structural only. Verified coverage metrics count only
entries/destinations whose data is backed by verified official evidence.
100% matrix completeness is NEVER "100% global visa processing".
"""
from __future__ import annotations

from sqlalchemy import func, select

from . import SNAPSHOT_DATE
from .matrix import completeness as matrix_completeness
from .models import (AuthorizedVisaCenter, ConsularJurisdictionRule, HumanReviewTask,
                     OfficialPortalRecord, RouteMatrixEntry, SnapshotConflict,
                     SourceEvidence, VisaFeeVersion, VisaPolicy, VisaRoute)
from .registry import load_registry

RESEARCHED_DISPOSITIONS = (
    "VISA_FREE", "ETA_REQUIRED", "EVISA_REQUIRED", "VISA_ON_ARRIVAL",
    "EMBASSY_VISA_REQUIRED", "AUTHORIZED_VISA_CENTER_REQUIRED",
    "TRANSIT_AUTHORIZATION_REQUIRED",
)
# Dispositions that imply an application (and thus a fee/portal) exists.
APPLICATION_DISPOSITIONS = (
    "ETA_REQUIRED", "EVISA_REQUIRED", "VISA_ON_ARRIVAL",
    "EMBASSY_VISA_REQUIRED", "AUTHORIZED_VISA_CENTER_REQUIRED",
    "TRANSIT_AUTHORIZATION_REQUIRED",
)


def _count(db, q) -> int:
    return db.execute(q).scalar_one()


def metrics(db) -> dict:
    mc = matrix_completeness(db)
    n_dest = len(load_registry("countries")["entries"])

    total_entries = mc["created_entries"] or 1
    not_applicable = mc["dispositions"].get("NOT_APPLICABLE", 0)
    decidable = max(total_entries - not_applicable, 1)
    researched = sum(mc["dispositions"].get(d, 0) for d in RESEARCHED_DISPOSITIONS)

    dest_with_verified_source = _count(db, select(func.count(func.distinct(
        SourceEvidence.applicable_jurisdiction))).where(
        SourceEvidence.snapshot_date == SNAPSHOT_DATE,
        SourceEvidence.verification_status == "verified"))

    app_entries = sum(mc["dispositions"].get(d, 0) for d in APPLICATION_DISPOSITIONS)
    dest_with_verified_fee = _count(db, select(func.count(func.distinct(
        VisaFeeVersion.destination_country))).where(
        VisaFeeVersion.snapshot_date == SNAPSHOT_DATE,
        VisaFeeVersion.fee_status == "verified"))
    app_dests = [d for (d,) in db.execute(select(func.distinct(
        RouteMatrixEntry.destination_country)).where(
        RouteMatrixEntry.disposition.in_(APPLICATION_DISPOSITIONS))).all()]
    fee_cov = (dest_with_verified_fee / len(app_dests)) if app_dests else 0.0

    dest_with_verified_portal = _count(db, select(func.count(func.distinct(
        OfficialPortalRecord.destination_country))).where(
        OfficialPortalRecord.snapshot_date == SNAPSHOT_DATE,
        OfficialPortalRecord.verification_status.in_(
            ("verified_official_domain", "verified_via_official_link"))))
    portal_cov = (dest_with_verified_portal / len(app_dests)) if app_dests else 0.0

    # Preparation needs verified requirements; handoff additionally a verified
    # portal; production additionally an approved adapter.
    prep_cov = researched / decidable
    handoff_cov = min(prep_cov, portal_cov)
    prod_routes = _count(db, select(func.count(VisaRoute.id)).where(
        VisaRoute.adapter_status == "production_approved"))
    prod_cov = prod_routes / decidable

    return {
        "MATRIX_COMPLETENESS": round(mc["matrix_completeness"], 6),
        "SOURCE_COVERAGE": round(min(dest_with_verified_source / n_dest, 1.0), 6),
        "REQUIREMENTS_VERIFIED_COVERAGE": round(researched / decidable, 6),
        "FEE_VERIFIED_COVERAGE": round(min(fee_cov, 1.0), 6),
        "PREPARATION_COVERAGE": round(prep_cov, 6),
        "APPLICANT_HANDOFF_COVERAGE": round(handoff_cov, 6),
        "PRODUCTION_ADAPTER_COVERAGE": round(prod_cov, 6),
    }


def report(db, *, git_commit: str = "") -> dict:
    mc = matrix_completeness(db)
    counts = {
        "routes": _count(db, select(func.count(VisaRoute.id))),
        "policies": _count(db, select(func.count(VisaPolicy.id))),
        "fee_versions": _count(db, select(func.count(VisaFeeVersion.id))),
        "portals": _count(db, select(func.count(OfficialPortalRecord.id))),
        "visa_centers": _count(db, select(func.count(AuthorizedVisaCenter.id))),
        "jurisdiction_rules": _count(db, select(func.count(ConsularJurisdictionRule.id))),
        "source_evidence": _count(db, select(func.count(SourceEvidence.id))),
        "source_evidence_verified": _count(db, select(func.count(SourceEvidence.id)).where(
            SourceEvidence.verification_status == "verified")),
        "conflicts_open": _count(db, select(func.count(SnapshotConflict.id)).where(
            SnapshotConflict.status == "open")),
        "human_review_open": _count(db, select(func.count(HumanReviewTask.id)).where(
            HumanReviewTask.status == "open")),
    }
    return {
        "snapshot_date": SNAPSHOT_DATE,
        "generated_from": git_commit or "unknown",
        "matrix": {
            "expected_entries": mc["expected_entries"],
            "created_entries": mc["created_entries"],
            "excluded_entries": 0,
            "exclusion_rules": [
                "destination == holder's own state -> explicit NOT_APPLICABLE row (never omitted)",
                "non-ordinary travel documents grouped as nationality=ANY per destination/category (documented normalization, refined by research)",
                "residence normalized to ANY at matrix level; residence-specific overrides added when research shows a residence-dependent disposition",
            ],
            "dispositions": mc["dispositions"],
        },
        "metrics": metrics(db),
        "counts": counts,
        "notes": "Matrix completeness is structural only; verified coverage is measured separately and is the honest service-level signal.",
    }
