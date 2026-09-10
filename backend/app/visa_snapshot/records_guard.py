"""Shared evidence and contradiction gate for all route readers."""
def grounded_verdict_supported(check: dict | None) -> bool:
    """An old consistency flag or an ancillary check cannot verify a verdict."""
    from . import freshness
    check = freshness.effective_check({"grounded_check": check})
    fields = check.get("verified_fields")
    return bool(check.get("consistent") is True
                and isinstance(fields, (list, tuple, set))
                and "disposition" in fields)


def apply_records_hold(route: dict, out: dict, db=None) -> dict:
    """Apply the same evidence and contradiction gate to every reader path."""
    from . import kimi_primary, tstation
    if not out.get("guidance"):
        return out
    out = dict(out)
    gc = out.get("grounded_check")
    gc = gc if isinstance(gc, dict) else {}
    problems = kimi_primary.serve_time_invariants(out["guidance"])
    from .reviewed_social_authority import registered_reference, guidance_supported
    provenance = out.get("source_verified") or {}
    social_claim = (registered_reference(out["guidance"].get("source_url"))
                    or isinstance(provenance, dict) and bool(provenance.get("authority_binding_id")))
    if social_claim and not guidance_supported(out["guidance"], provenance, route):
        # Scope/date/capture failures are hard conflicts. Neither a manual
        # release nor disabling the ordinary low-evidence hold can bypass them.
        problems.append("social_authority_scope_or_policy_conflict")
    disputed = list(gc.get("disputed_fields") or [])
    if db is not None:
        from . import freshness
        disputed.extend(freshness.active_disputed_fields(db, kimi_primary.cache_key(route)))
    rows = tstation.records_for_route(
        route, out["guidance"], out.get("source_verified") or None,
        grounded_ok=grounded_verdict_supported(gc),
        disputed_fields=disputed + problems, grounded_fields=gc.get("verified_fields"))
    # An operator release cannot make a self-contradiction or a later
    # official-page dispute safe. All product rows count, not just the first.
    conflict = bool(problems or disputed or any(
        "no visa_products were listed" not in issue
        for issue in (out.get("contradictions") or [])))
    low = any(r.get("_evidence_low", r.get("confidence_level") == "Low") for r in rows)
    pending = bool(out.get("detail_pending"))
    if low:
        from .publication_scope import scoped_exemption, scoped_required_evisa, project_reader
        scope = (scoped_exemption(route, out, rows, conflict=conflict, pending=pending)
                 or scoped_required_evisa(route, out, rows, conflict=conflict, pending=pending))
        if scope is not None:
            return project_reader(route, out, scope)
    if conflict or pending or (low and not out.get("operator_released")):
        out["review_required"] = True
        out["held"] = True if conflict or pending else kimi_primary.hold_enabled()
    return out


_HELD_STATUS_FIELDS = frozenset({
    "status", "cached", "stale", "held", "review_required", "cache_key",
    "route", "operator_released", "detail_pending", "transit_countries",
    "model", "elapsed_seconds", "approximate", "approximate_reason",
})


def held_envelope(out: dict) -> dict:
    """A held answer's claims never leave the server — and a claim is not
    only the guidance: the workflow plan, advisories and missing-field lists
    all hint at the withheld verdict (a live audit read the verdict straight
    out of workflow_plan on a held row). Only the identity and the flags
    survive."""
    # An allowlist also closes indirect disclosures through provenance notes,
    # grounded-check explanations and future engine additions. Removing only
    # known claim containers silently exposed all of those on held answers.
    out = {k: v for k, v in out.items() if k in _HELD_STATUS_FIELDS}
    out["guidance"] = None
    return out
