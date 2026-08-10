"""Adapter-learning + case-status endpoints. Stub router; implemented in place.
The aggregation include in main.py never changes.

Operator surface, not an applicant one: every route here is admin-only and
READ-ONLY. Advice about a parked build is evidence a person acts on by hand —
nothing under /factory releases, mutates a gate result, or extends the Ellis
vocabulary.
"""
from fastapi import APIRouter, Depends, HTTPException

from ..db import get_session
from ..security import Principal, get_principal, require_admin
from . import gate_advisor

router = APIRouter(prefix="/factory", tags=["factory-learning"])


@router.get("/builds/{build_id}/advice")
def build_advice(build_id: str, db=Depends(get_session),
                 p: Principal = Depends(get_principal)):
    """Concrete next steps for a parked build, translated from its own gate
    report. Advisory: the caller performs the fixes, this endpoint performs
    none of them."""
    require_admin(p)
    try:
        return gate_advisor.advise(db, build_id)
    except gate_advisor.UnknownSubject as e:
        raise HTTPException(404, str(e))
