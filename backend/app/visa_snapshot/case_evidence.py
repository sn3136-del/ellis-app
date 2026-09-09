"""Keep saved applicant workflows behind the current route-evidence gate."""
from __future__ import annotations


class CaseEvidenceBlocked(Exception):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.detail = {"reason": reason, "message": message}


def ensure_current_case_guidance(db, app_row, *, for_filing: bool = False):
    """Validate and synchronize an existing routed case before using its facts.

    No model generation is started here. A canonical cached decision, when
    present, takes precedence over the case's saved copy. Cases that never
    entered the route-guidance workflow retain their existing behavior.
    """
    from .. import checklist_intake
    from . import kimi_primary
    from .api import _reader_guidance, _sync_case_reader_guidance
    from .registry import iso3

    cg = checklist_intake.case_guidance(db, app_row.id)
    if cg is None:
        return None
    if (cg.continuation_kind in {"h1b_petition", "h1b_filing"}
            and str(cg.route_key or "").startswith(("h1b:", "h1b_step:"))):
        # Petition and its filing children have their own curated statutory
        # workflow/evidence gates. They are not passport/destination decisions.
        return None
    route = dict(app_row.answers or {})
    route["destination_country"] = app_row.destination_country or route.get("destination_country")
    for field in ("passport_nationality", "destination_country", "lawful_country_of_residence"):
        if route.get(field):
            route[field] = iso3(route[field], default=str(route[field]).upper())
    current = kimi_primary.normalize_guidance_label(cg.guidance)
    if kimi_primary._cached(db, kimi_primary.cache_key(route)) is not None:
        current = kimi_primary.get_route_guidance(db, route, stage="core")
    current = _reader_guidance(db, route, current)
    if current.get("held") or not current.get("guidance"):
        raise CaseEvidenceBlocked(
            "guidance_requires_review",
            "This route is being checked against official sources. Please check back shortly.")
    _sync_case_reader_guidance(db, app_row, cg, current)
    inner = current["guidance"]
    if for_filing and (inner.get("disposition") == "VISA_ON_ARRIVAL"
                       or inner.get("requirement_detail") in {"paper_visa_on_arrival", "evisa_on_arrival"}):
        raise CaseEvidenceBlocked(
            "visa_on_arrival_preparation",
            "This route requires preparation for a visa on arrival. An advance visa filing cannot be started.")
    return current
