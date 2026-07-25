"""HTTP surface for global route coverage.

Admin endpoints (developer dashboard, require_admin) plus one applicant-safe
resolver endpoint that exposes route outcomes without any operator language.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..db import get_session as get_db
from ..security import Principal, get_principal, require_admin
from ..visa_snapshot.registry import RegistryError
from . import dashboard as dashboard_mod
from . import resolver as resolver_mod

router = APIRouter()


@router.get("/admin/global/coverage")
def admin_global_coverage(p: Principal = Depends(get_principal), db=Depends(get_db)):
    require_admin(p)
    return dashboard_mod.coverage(db)


@router.get("/admin/global/unsupported")
def admin_global_unsupported(limit: int = 200,
                             p: Principal = Depends(get_principal),
                             db=Depends(get_db)):
    require_admin(p)
    return dashboard_mod.unsupported(db, limit=min(max(limit, 1), 2000))


@router.get("/global/route-outcome")
def route_outcome(nationality: str, destination: str,
                  residence: str | None = None,
                  residence_subdivision: str | None = None,
                  issuing_country: str | None = None,
                  travel_document_type: str = "ordinary_passport",
                  transit: bool = False,
                  p: Principal = Depends(get_principal), db=Depends(get_db)):
    """Applicant-safe deterministic route outcome. No admin/operator fields."""
    try:
        rec = resolver_mod.resolve_route(
            db, nationality=nationality, destination=destination,
            residence=residence, residence_subdivision=residence_subdivision,
            issuing_country=issuing_country,
            travel_document_type=travel_document_type, transit=transit)
    except RegistryError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Applicant-facing subset: outcome + verification + channel + applicant
    # steps. Build/orchestrator internals stay operator-only, and a portal
    # whose official identity is NOT yet verified is never presented as the
    # official channel.
    channel = rec.get("official_channel")
    if channel and channel.get("verification_status") not in (
            "verified_official_domain", "verified_live"):
        channel = None
    return {
        "route_outcome": rec["route_outcome"],
        "verification_status": rec["verification_status"],
        "max_stay_days": rec.get("max_stay_days"),
        "official_channel": ({"name": channel["name"], "url": channel["url"],
                              "operated_by": ("government"
                                              if channel["operator_kind"] == "government"
                                              else "authorized service provider")}
                             if channel else None),
        "jurisdiction": rec.get("jurisdiction"),
        "applicant_confirmations": rec.get("applicant_confirmations", []),
        "passport_validity_rule": rec.get("passport_validity_rule"),
        "notes": rec.get("notes", []),
        "disclaimer": ("Requirements may have changed since verification; "
                       "Ellis confirms against official sources before any "
                       "application step."),
    }
