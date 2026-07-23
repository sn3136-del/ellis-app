"""Global visa-route rules engine + coverage matrix (Phase 2).

Versioned, official-source-backed route rules with mandatory human review.
The coverage matrix never claims a route is verified merely because a row
exists — every route reports the HONEST status ladder from the brief:

  not_researched → sources_discovered → requirements_verified → fees_verified
  → preparation_supported → applicant_handoff_supported → adapter_implemented
  → mock_tested → staging_tested → production_approved   (or temporarily_paused)

Automated research tooling may CREATE pending rules; administrative review is
required before a rule is 'verified' and usable for production decisions.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from . import models, audit

STATUS_LADDER = [
    "not_researched", "sources_discovered", "requirements_verified", "fees_verified",
    "preparation_supported", "applicant_handoff_supported", "adapter_implemented",
    "mock_tested", "staging_tested", "production_approved",
]

# Adapter lifecycle states (adapters_admin) → coverage rung they prove.
_ADAPTER_RUNG = {
    "mock_tested": "mock_tested",
    "staging_tested": "staging_tested",
    "approved": "staging_tested",
    "limited_rollout": "production_approved",
    "production_active": "production_approved",
}

_RULE_FIELDS = (
    "source_url", "source_authority", "effective_date", "expiration_date",
    "eligibility_conditions", "required_documents", "processing_method",
    "electronic_available", "biometrics_required", "interview_required",
    "appointment_required", "personal_appearance_required",
    "third_party_preparation_allowed", "third_party_submission_allowed",
    "declaration_mandatory", "passport_validity_rule", "confidence",
)


def _route_filter(q_model, destination, visa_type, nationality, residence):
    return (q_model.destination == destination, q_model.visa_type == visa_type,
            q_model.nationality == nationality, q_model.residence == residence)


def create_rule(db, *, destination: str, visa_type: str = "tourist", nationality: str = "",
                residence: str = "", actor: str = "system", **fields) -> models.RouteRule:
    """Create the next PENDING version of a route rule. Never auto-verified —
    review_status starts 'pending' regardless of caller."""
    unknown = set(fields) - set(_RULE_FIELDS)
    if unknown:
        raise ValueError(f"unknown rule fields: {sorted(unknown)}")
    if not fields.get("source_url"):
        raise ValueError("source_url (official source) is required")
    prev = latest_rule(db, destination=destination, visa_type=visa_type,
                       nationality=nationality, residence=residence, any_status=True)
    rule = models.RouteRule(destination=destination, visa_type=visa_type,
                            nationality=nationality, residence=residence,
                            version=(prev.version + 1 if prev else 1),
                            retrieved_at=datetime.now(timezone.utc),
                            review_status="pending", **fields)
    db.add(rule)
    db.flush()
    if prev:
        prev.superseded_by = rule.id
    db.commit()
    audit.record(db, org_id="platform", application_id="", action="route_rule_created",
                 detail={"destination": destination, "visa_type": visa_type,
                         "version": rule.version, "source_url": fields.get("source_url", "")},
                 actor=actor)
    return rule


def review_rule(db, *, rule_id: str, decision: str, actor: str) -> models.RouteRule:
    """Admin review (enforced at the endpoint). decision: verified|rejected|stale."""
    if decision not in ("verified", "rejected", "stale"):
        raise ValueError("decision must be verified|rejected|stale")
    rule = db.get(models.RouteRule, rule_id)
    if not rule:
        raise KeyError("rule not found")
    rule.review_status = decision
    rule.reviewed_by = actor
    db.commit()
    audit.record(db, org_id="platform", application_id="", action="route_rule_reviewed",
                 detail={"rule_id": rule_id, "decision": decision}, actor=actor)
    return rule


def latest_rule(db, *, destination: str, visa_type: str = "tourist", nationality: str = "",
                residence: str = "", any_status: bool = False) -> models.RouteRule | None:
    q = select(models.RouteRule).where(
        *_route_filter(models.RouteRule, destination, visa_type, nationality, residence))
    if not any_status:
        q = q.where(models.RouteRule.review_status == "verified")
    rows = db.execute(q.order_by(models.RouteRule.version.desc())).scalars().all()
    return rows[0] if rows else None


def rule_history(db, *, destination: str, visa_type: str = "tourist", nationality: str = "",
                 residence: str = "") -> list[models.RouteRule]:
    return db.execute(select(models.RouteRule).where(
        *_route_filter(models.RouteRule, destination, visa_type, nationality, residence)
    ).order_by(models.RouteRule.version)).scalars().all()


def route_status(db, *, destination: str, visa_type: str = "tourist", nationality: str = "",
                 residence: str = "") -> dict:
    """The honest coverage status of one route. Each rung requires positive
    evidence; a paused adapter reports temporarily_paused."""
    from .fees import verified_current_fee
    args = dict(destination=destination, visa_type=visa_type,
                nationality=nationality, residence=residence)
    verified = latest_rule(db, **args)
    pending = latest_rule(db, **args, any_status=True)
    drafts = db.execute(select(models.PortalDraft).where(
        models.PortalDraft.country == destination,
        models.PortalDraft.visa_type == visa_type)).scalars().all()
    has_sources = any((d.draft or {}).get("verified_candidates") for d in drafts)
    fee = verified_current_fee(db, **args)
    adapter = db.execute(select(models.AdapterRecord).where(
        models.AdapterRecord.country == destination,
        models.AdapterRecord.visa_type == visa_type)).scalars().first()

    status = "not_researched"
    if has_sources or pending is not None:
        status = "sources_discovered"
    if verified is not None:
        status = "requirements_verified"
        if fee is not None:
            # Requirements + fees verified ⇒ Ellis can prepare the application
            # and hand off — the highest rung evidence supports without an adapter.
            status = "applicant_handoff_supported"
    if adapter is not None and verified is not None:
        status = max(status, "adapter_implemented", key=STATUS_LADDER.index)
        rung = _ADAPTER_RUNG.get(adapter.lifecycle_state)
        if rung:
            status = max(status, rung, key=STATUS_LADDER.index)
        if adapter.lifecycle_state in ("paused", "rolled_back") or adapter.kill_switch:
            return {**args, "status": "temporarily_paused", "paused": True,
                    "rule_version": verified.version if verified else None,
                    "fee_verified": fee is not None}
    return {**args, "status": status, "paused": False,
            "rule_version": verified.version if verified else None,
            "rule_review": (pending.review_status if pending else None),
            "fee_verified": fee is not None}


def coverage_matrix(db) -> list[dict]:
    """Every route combination that has ANY record (rule, draft, or adapter),
    with its honest status. A row's existence is never itself a verification."""
    routes: set[tuple] = set()
    for r in db.execute(select(models.RouteRule)).scalars().all():
        routes.add((r.destination, r.visa_type, r.nationality, r.residence))
    for d in db.execute(select(models.PortalDraft)).scalars().all():
        routes.add((d.country, d.visa_type, "", ""))
    for a in db.execute(select(models.AdapterRecord)).scalars().all():
        routes.add((a.country, a.visa_type, "", ""))
    return [route_status(db, destination=d, visa_type=v, nationality=n, residence=res)
            for (d, v, n, res) in sorted(routes)]
