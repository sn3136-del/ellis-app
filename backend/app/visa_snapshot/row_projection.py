"""One projection per surface, extracted from the code that serves it.

The quality console, the workbook and the consistency sweep all need "the
25-field records of one cached answer". Until guard-20260912 that body lived
inline in app.main._collect_tstation_rows, so a sweep comparing surfaces
would have had to rebuild it by hand and would have drifted from the product
it polices. records_projection is that body lifted verbatim; main calls it
and the sweep calls it, so there is exactly one records path.

Read only: this module never writes a row, never commits, and never opens a
session of its own.
"""
from __future__ import annotations


def records_projection(db, r, route: dict) -> list[dict]:
    """The T-Station records of one cached answer, exactly as /database/records
    builds them: the merged guidance through the override layer, the grounded
    check and the open monitor findings, the publication hold, and the
    per-record provenance tags. ``r`` is a KimiRouteGuidanceCache row and
    ``route`` its route dict with the document type already normalised."""
    from . import kimi_primary, tstation
    from . import freshness as _fresh
    from .records_guard import (apply_records_hold as _apply_records_hold,
                                grounded_verdict_supported as _grounded_verdict_supported)
    out = []
    reader = kimi_primary.apply_verified_overrides(kimi_primary._result(
        r.status, kimi_primary.served_guidance(r), cached=True,
        stale=kimi_primary._is_stale(r), missing=r.missing_fields,
        contradictions=r.contradictions, model=r.model,
        released=bool((r.verification or {}).get("operator_released"))), route)
    g, prov = reader["guidance"], reader.get("source_verified")
    collected = (r.generated_at.isoformat() if r.generated_at else "")
    until = (r.fresh_until.isoformat() if r.fresh_until else "")
    # Whether the official page has actually been read and agreed with:
    # the difference between a source and a link nobody opened.
    _gc = _fresh.effective_check(r.verification)
    _grounded = _grounded_verdict_supported(_gc)
    _disputed_now = list(_gc.get("disputed_fields") or [])
    _disputed_now.extend(_fresh.active_disputed_fields(db, r.cache_key))
    _problems = kimi_primary.serve_time_invariants(g)
    if _problems:
        _disputed_now.extend(_problems)
    _route_state = _apply_records_hold(route, {
        **reader, "grounded_check": _gc,
        "detail_pending": bool((r.verification or {}).get("detail_pending")),
        "operator_released": bool((r.verification or {}).get("operator_released")),
    }, db)
    for rec in tstation.records_for_route(route, g, prov, collected, until,
                                          grounded_ok=_grounded,
                                          disputed_fields=_disputed_now, grounded_fields=_gc.get("verified_fields")):
        if not rec.get("source_url") and not rec.get("_separate_permission"):
            # The destination's browser-verified official portal is the
            # official reference page for a record whose answer carries
            # no page of its own (visa-free routes especially).
            portal = kimi_primary._official_portals().get(
                str(route.get("destination_country") or "").upper())
            if portal:
                rec["source_url"] = portal
                rec["data_source"] = (rec.get("data_source")
                                      or "Official portal (reference only)")
        rec["_cache_key"] = r.cache_key
        rec["_status"] = kimi_primary.STATUS_UNCERTAIN if _problems else r.status
        rec["_contradictions"] = _problems
        # Whether an operator has released this answer despite low
        # confidence: without it the release half of the confidence gate
        # cannot be audited from the records surface.
        rec["_released"] = bool((r.verification or {}).get("operator_released"))
        rec["_route_held"] = bool(_route_state.get("held"))
        publication = next((item for item in _route_state.get("product_publication", [])
                            if item.get("product_index") == rec.get("_product_index")), None)
        rec["_held"] = bool(publication["held"]) if publication else rec["_route_held"]
        rec["_review_required"] = rec["_held"] if publication else bool(_route_state.get("review_required"))
        rec["_publication_state"] = publication["state"] if publication else (
            "withheld" if rec["_held"] else "published")
        rec["_publication_reason"] = publication["reason"] if publication else None
        # How solidly the source BACKS what this record shows:
        #   human-quote        a person verified these fields against the
        #                      named page and quoted it
        #   grounded-consistent the pipeline fetched the official page and
        #                      found the stored answer consistent with it
        #   reference          an official page is linked but has not yet
        #                      been machine-compared to this answer
        gc = _gc
        # Fields the page disputed that no human has ruled on yet: the
        # spec's third checklist state, 未过审 (not approved).
        rec["_disputed"] = _disputed_now
        record_prov = rec.get("_product_source_verified") or (None if rec.get("_separate_permission") else prov)
        if rec.get("_separate_permission"):
            gc = {}
        if tstation.verdict_provenance_supported(record_prov):
            rec["_source_check"] = "human-quote" if record_prov.get("verifier") == "human" else "ai-quote"
        elif _grounded_verdict_supported(gc):
            rec["_source_check"] = "grounded-consistent"
        elif rec.get("source_url"):
            rec["_source_check"] = "reference"
        else:
            rec["_source_check"] = "unchecked"
        out.append(rec)
    return out


def canonical_route(r) -> dict:
    """The route dict the records builder derives for a cached row: a row
    cached before the document type was stored carries it only in its key
    ("doc:diplomatic_passport"), and the override layer must see it."""
    from . import kimi_primary
    route = dict(r.route or {})
    doc = str(route.get("travel_document_type") or "")
    if not doc:
        doc = next((part[4:] for part in str(r.cache_key or "").split("|")
                    if part.startswith("doc:")), "ordinary_passport")
    route["travel_document_type"] = kimi_primary.normalize_document_type(doc)
    return route
