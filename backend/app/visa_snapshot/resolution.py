"""Automatic route resolution (brief section 16) + readiness ladder (section 17).

resolve() runs the 13 steps and returns ONE readiness status:
  NOT_READY | PREPARATION_ONLY | APPLICANT_HANDOFF_READY |
  LIVE_SANDBOX_READY | LIVE_PRODUCTION_READY

Honesty: every check result is persisted (RouteResolution.checks) and surfaced
with snapshot-date labels. Nothing is invented: an unresearched route is
NOT_READY with an admin review task, never a guessed requirement.
"""
from __future__ import annotations

from sqlalchemy import select

from . import SNAPSHOT_DATE, SNAPSHOT_LABEL
from .matrix import matrix_key
from .models import (AdapterDevelopmentTask, ConsularJurisdictionRule,
                     HumanReviewTask, OfficialPortalRecord,
                     PassportValidityRuleRecord, RouteMatrixEntry,
                     RouteReadinessEvaluation, RouteResolution, SnapshotConflict,
                     SourceEvidence, VisaFeeVersion, VisaPolicy, VisaRoute)
from .registry import RegistryError
from .routekey import RouteInput, normalize_route, route_key

READINESS = ("NOT_READY", "PREPARATION_ONLY", "APPLICANT_HANDOFF_READY",
             "LIVE_SANDBOX_READY", "LIVE_PRODUCTION_READY")

RESEARCHED = ("VISA_FREE", "ETA_REQUIRED", "EVISA_REQUIRED", "VISA_ON_ARRIVAL",
              "EMBASSY_VISA_REQUIRED", "AUTHORIZED_VISA_CENTER_REQUIRED",
              "TRANSIT_AUTHORIZATION_REQUIRED", "NOT_APPLICABLE")


def _route_input(answers: dict) -> RouteInput:
    return RouteInput(
        passport_nationality=answers.get("passport_nationality", ""),
        passport_issuing_country=answers.get("passport_issuing_country")
            or answers.get("passport_nationality", ""),
        travel_document_type=answers.get("travel_document_type", "ordinary_passport"),
        lawful_country_of_residence=answers.get("lawful_country_of_residence", ""),
        destination_country=answers.get("destination_country", ""),
        visa_category=answers.get("visa_category") or "tourist_visa",
        visa_subtype=answers.get("visa_subtype"),
        travel_purpose=answers.get("travel_purpose", "tourism"),
        policy_period=answers.get("arrival_date") or None,
        consular_jurisdiction=answers.get("consular_jurisdiction") or None,
    )


def resolve(db, *, org_id: str, answers: dict, case_id: str | None = None,
            create_tasks: bool = True) -> dict:
    checks: dict = {"snapshot_date": SNAPSHOT_DATE, "snapshot_label": SNAPSHOT_LABEL}

    # 1-2: normalize + route key.
    try:
        inp = _route_input(answers)
        normalized = normalize_route(inp)
        key = route_key(inp)
    except (RegistryError, KeyError) as e:
        checks["normalization"] = {"ok": False, "error": str(e)}
        res = RouteResolution(org_id=org_id, case_id=case_id, raw_input=answers,
                              normalized_input={}, route_key="", readiness_status="NOT_READY",
                              checks=checks, snapshot_date=SNAPSHOT_DATE)
        db.add(res)
        db.commit()
        return {"resolution_id": res.id, "readiness_status": "NOT_READY",
                "route_key": None, "checks": checks,
                "reason": f"route could not be normalized: {e}"}
    checks["normalization"] = {"ok": True, "normalized": normalized}
    dest = normalized["destination_country"]
    nat = normalized["passport_nationality"]
    cat = normalized["visa_category"]

    # 3: snapshot resolution — explicit route record, else matrix entry.
    head = db.execute(select(VisaRoute).where(VisaRoute.route_key == key)).scalar_one_or_none()
    entry = None
    if head is None:
        mk = matrix_key(nat, normalized["travel_document_type"], dest, cat)
        entry = db.execute(select(RouteMatrixEntry).where(
            RouteMatrixEntry.matrix_key == mk)).scalar_one_or_none()
        if entry is None and normalized["travel_document_type"] != "ordinary_passport":
            mk = matrix_key("ANY", normalized["travel_document_type"], dest, cat)
            entry = db.execute(select(RouteMatrixEntry).where(
                RouteMatrixEntry.matrix_key == mk)).scalar_one_or_none()
    disposition = (head.disposition if head else entry.disposition if entry
                   else "RESEARCH_INCOMPLETE")
    research_status = head.research_status if head else (
        "verified" if entry is not None and entry.disposition in RESEARCHED else "not_researched")
    checks["snapshot_resolution"] = {
        "matched": "route_record" if head else "matrix_entry" if entry else "none",
        "disposition": disposition, "research_status": research_status,
    }

    # 4: linked policy.
    policies = db.execute(select(VisaPolicy).where(
        VisaPolicy.destination_country == dest)).scalars().all()
    checks["policy"] = {"count": len(policies),
                        "kinds": sorted({p.policy_kind for p in policies})}

    # 5: authoritative source evidence.
    verified_evidence = db.execute(select(SourceEvidence).where(
        SourceEvidence.snapshot_date == SNAPSHOT_DATE,
        SourceEvidence.applicable_jurisdiction == dest,
        SourceEvidence.verification_status == "verified")).scalars().all()
    checks["source_evidence"] = {
        "verified_count": len(verified_evidence),
        "authorities": sorted({e.source_authority for e in verified_evidence}),
        "retrieved_at": sorted({e.retrieved_at for e in verified_evidence})[-1:] or None,
        "sample_urls": [e.final_url for e in verified_evidence[:5]],
    }

    # 6: conflicts.
    open_conflicts = db.execute(select(SnapshotConflict).where(
        SnapshotConflict.snapshot_date == SNAPSHOT_DATE,
        SnapshotConflict.destination_country == dest,
        SnapshotConflict.status == "open")).scalars().all()
    route_conflicted = any(c.field.startswith(f"{nat}:") or c.field == f"{nat}:{cat}"
                           for c in open_conflicts) or disposition == "CONFLICTED"
    checks["conflicts"] = {"open_for_destination": len(open_conflicts),
                           "route_conflicted": route_conflicted}

    # 7: human review status.
    checks["human_review"] = {"status": head.human_review_status if head else "not_reviewed"}

    # 8: fee status.
    fees = db.execute(select(VisaFeeVersion).where(
        VisaFeeVersion.snapshot_date == SNAPSHOT_DATE,
        VisaFeeVersion.destination_country == dest)).scalars().all()
    fee_verified = any(f.fee_status == "verified" for f in fees)
    checks["fees"] = {"versions": len(fees), "verified": fee_verified}

    # 9: consular jurisdiction.
    jur = db.execute(select(ConsularJurisdictionRule).where(
        ConsularJurisdictionRule.snapshot_date == SNAPSHOT_DATE,
        ConsularJurisdictionRule.destination_country == dest,
        ConsularJurisdictionRule.residence_jurisdiction == normalized["lawful_country_of_residence"],
    )).scalars().first()
    needs_jurisdiction = disposition in ("EMBASSY_VISA_REQUIRED", "AUTHORIZED_VISA_CENTER_REQUIRED")
    checks["consular_jurisdiction"] = {
        "required": needs_jurisdiction,
        "resolved": bool(jur and jur.verification_status == "verified"),
        "status": (jur.verification_status if jur else
                   ("MANUAL_REVIEW_REQUIRED" if needs_jurisdiction else "not_required")),
        "competent_post": jur.competent_post_name if jur else None,
    }

    # 10: official portal.
    portals = db.execute(select(OfficialPortalRecord).where(
        OfficialPortalRecord.snapshot_date == SNAPSHOT_DATE,
        OfficialPortalRecord.destination_country == dest,
        OfficialPortalRecord.verification_status.in_(
            ("verified_official_domain", "verified_via_official_link")))).scalars().all()
    checks["official_portal"] = {
        "verified_count": len(portals),
        "portals": [{"kind": p.portal_kind, "url": p.url,
                     "operator_kind": p.operator_kind} for p in portals[:6]],
    }

    # 11: passport-validity rule.
    pv = db.execute(select(PassportValidityRuleRecord).where(
        PassportValidityRuleRecord.snapshot_date == SNAPSHOT_DATE,
        PassportValidityRuleRecord.destination_country == dest)).scalars().first()
    checks["passport_validity_rule"] = (
        {"rule_kind": pv.rule_kind, "months": pv.months,
         "min_blank_pages": pv.min_blank_pages,
         "research_status": pv.research_status} if pv
        else {"rule_kind": "unknown", "note": "no verified rule in snapshot — never guessed"})

    # 12: approved live adapter (adapters_admin lifecycle is authoritative).
    # AdapterRecord.country holds a country name or code; match on either.
    from ..models import AdapterRecord
    dest_name = answers.get("destination_country", "")
    adapters = db.execute(select(AdapterRecord).where(
        AdapterRecord.country.in_([dest, dest_name]),
        AdapterRecord.kill_switch.is_(False))).scalars().all()
    prod = [a for a in adapters if a.production_enabled
            and a.lifecycle_state in ("production_active", "limited_rollout")]
    sandbox = [a for a in adapters if a.lifecycle_state in ("staging_tested", "approved")]
    checks["adapter"] = {"records": len(adapters), "production_active": len(prod),
                         "sandbox_ready": len(sandbox)}

    # 13: ONE readiness status (fail-closed ladder; no rung skipping).
    requirements_ok = (disposition in RESEARCHED and research_status == "verified"
                       and not route_conflicted and len(verified_evidence) > 0)
    if not requirements_ok:
        readiness = "NOT_READY"
    elif disposition in ("VISA_FREE", "NOT_APPLICABLE"):
        # Nothing to file — preparation-only guidance (never claim a submission).
        readiness = "PREPARATION_ONLY"
    elif not fee_verified or (needs_jurisdiction and not checks["consular_jurisdiction"]["resolved"]):
        readiness = "PREPARATION_ONLY"
    elif len(portals) == 0:
        readiness = "PREPARATION_ONLY"
    elif prod:
        readiness = "LIVE_PRODUCTION_READY"
    elif sandbox:
        readiness = "LIVE_SANDBOX_READY"
    else:
        readiness = "APPLICANT_HANDOFF_READY"
    checks["readiness_reasoning"] = {
        "requirements_verified": requirements_ok,
        "fee_verified": fee_verified,
        "portal_verified": len(portals) > 0,
        "jurisdiction_ok": (not needs_jurisdiction) or checks["consular_jurisdiction"]["resolved"],
        "production_adapter": bool(prod), "sandbox_adapter": bool(sandbox),
    }

    res = RouteResolution(org_id=org_id, case_id=case_id, raw_input=answers,
                          normalized_input=normalized, route_key=key,
                          route_id=head.id if head else None,
                          route_version=head.current_version if head else None,
                          readiness_status=readiness, checks=checks,
                          snapshot_date=SNAPSHOT_DATE)
    db.add(res)
    db.add(RouteReadinessEvaluation(org_id=org_id, route_key=key, case_id=case_id,
                                    readiness_status=readiness,
                                    gates=checks["readiness_reasoning"],
                                    snapshot_date=SNAPSHOT_DATE))
    if create_tasks:
        if readiness == "NOT_READY":
            dup = db.execute(select(HumanReviewTask).where(
                HumanReviewTask.kind == "intake_not_ready",
                HumanReviewTask.subject_id == key,
                HumanReviewTask.status == "open")).scalar_one_or_none()
            if not dup:
                db.add(HumanReviewTask(org_id=org_id, snapshot_date=SNAPSHOT_DATE,
                                       kind="intake_not_ready", subject_id=key,
                                       title=f"Applicant route not ready: {nat} -> {dest} ({cat})",
                                       detail={"checks": checks["snapshot_resolution"],
                                               "case_id": case_id}))
        if readiness in ("PREPARATION_ONLY", "APPLICANT_HANDOFF_READY") and not prod:
            # First-real-route rule (brief section 20): queue the exact adapter.
            dup = db.execute(select(AdapterDevelopmentTask).where(
                AdapterDevelopmentTask.route_key == key,
                AdapterDevelopmentTask.status.in_(("queued", "in_development")))).scalar_one_or_none()
            if not dup:
                db.add(AdapterDevelopmentTask(
                    org_id=org_id, route_key=key, route_id=head.id if head else None,
                    case_id=case_id, priority=10,
                    portal_evidence={"portals": checks["official_portal"]["portals"]},
                    fee_evidence=checks["fees"],
                    jurisdiction_evidence=checks["consular_jurisdiction"],
                    notes="Auto-queued from applicant route intake."))
    db.commit()

    return {
        "resolution_id": res.id, "route_key": key, "normalized": normalized,
        "disposition": disposition, "readiness_status": readiness, "checks": checks,
        "snapshot": {
            "date": SNAPSHOT_DATE, "label": SNAPSHOT_LABEL,
            "disclaimer": ("These requirements were captured as of July 23, 2026 "
                           "and may have changed."),
        },
    }
