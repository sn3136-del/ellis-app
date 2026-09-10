"""Publish an evidenced default while quarantining separate optional products.

This is a reader projection only. Stored guidance and QC products retain their
values, evidence and missing-field states. A dispute, incomplete background
stage, stale reader or pre-existing hold never qualifies for this projection.
"""
from copy import deepcopy
import re

# These are route facts; application instructions and arbitrary future fields
# are excluded from the exemption projection. Only evidenced fields survive.
_ROUTE_FIELDS = frozenset({
    'requirement_detail', 'permitted_stay', 'permitted_stay_days',
    'passport_validity', 'passport_validity_requirement', 'required_documents',
    'onward_travel_evidence', 'accommodation_evidence', 'financial_evidence',
    'arrival_card', 'health_requirements', 'exceptions', 'transit_requirement',
    'policy_valid_until', 'application_channel_detail',
})
_PRODUCT_FIELDS = frozenset({'type', 'entry', 'validity', 'max_stay_days',
                            'permitted_stay', 'fee', 'requirement_detail',
                            'source_url', 'verified_at', 'notes', 'disposition'})
_PROOF_META = frozenset({'source_url', 'verified_at', 'verified_by', 'verifier'})
_OTHER_PERMISSION = re.compile(
    r'\be[ -]?visa\b|\bETA\b|\bESTA\b|\bKAZA\b|\bnon[ -]?immigrant\b|'
    r'\b(?:tourist|visitor|sponsored|single[ -]entry|multiple[ -]entry)\s+visa\b|'
    r'\bvisa\s+(?:on\s+arrival|application)\b|电子签证|電子簽證|落地签|落地簽', re.I)


class _MixedDefaultCondition(ValueError):
    pass


def _mentions_other_permission(text):
    if _OTHER_PERMISSION.search(text):
        return True
    # Product names vary: "consular visa" and "embassy visa" cannot evade a
    # quarantine merely because the catalog called it "Non-Immigrant Visa".
    remaining = re.sub(r"visa[- ](?:free|exempt(?:ion)?)|no visa|"
                       r"visas? (?:is |are )?(?:not required|waived|exempt)",
                       "", text, flags=re.I)
    return bool(re.search(r"\bvisas?\b", remaining, re.I))


def _default_text(value):
    """Exclude alternative prose only when that cannot erase a prerequisite."""
    if isinstance(value, str):
        kept=[]
        for part in re.split(r'(?<=[.!?。！？])\s+', value):
            if not _mentions_other_permission(part):
                kept.append(part)
                continue
            if (re.search(r"\b(?:must|needs?|requires?|required|only|provided|subject|unless|"
                          r"conditions?|ticket|insurance|funds|residen[ct]\w*)\b|"
                          r"必须|必須|需要|须|須|仅|僅|条件|條件|凭|憑|持有|机票|機票|保险|保險|资金|資金|居留", part, re.I)
                    and (not re.match(r"\s*(?:for\s+)?stays?\s+(?:longer|beyond)\b", part, re.I)
                         or re.search(r"\b(?:otherwise|ticket|insurance|funds|residen[ct]\w*|"
                                      r"passport|documents?|booking|hotel|accommodation|provided|"
                                      r"unless|subject|conditions?|citizens?|nationals?|must)\b|"
                                      r"必须|必須|须|須|机票|機票|保险|保險|资金|資金|居留|条件|條件", part, re.I))):
                # A remaining exception is not proof that all original
                # eligibility conditions survived this projection.
                raise _MixedDefaultCondition()
        return ' '.join(kept) or None
    if isinstance(value, list):
        return [v for item in value if (v := _default_text(item)) not in (None, '', [], {})]
    if isinstance(value, dict):
        return {k: v for k, item in value.items()
                if (v := _default_text(item)) not in (None, '', [], {})}
    return deepcopy(value)


def _reviewed_route_value(g, prov, key):
    if key not in set(prov.get('fields') or ()) or key not in g:
        return None
    proof = (prov.get('field_provenance') or {}).get(key)
    if isinstance(proof, dict):
        from . import tstation
        if not tstation.verdict_provenance_supported(dict(proof, fields=['disposition'])):
            return None
    if isinstance(proof, dict) and 'status' in proof:
        status = proof.get('status')
        if status in {'partial', 'partially_reviewed'}:
            elements = proof.get('verified_elements', proof.get('reviewed_elements'))
            value = g[key]
            if not isinstance(value, list) or not isinstance(elements, list):
                return None
            # An exact stored subset only; a reviewer label cannot introduce
            # new claims or revive a sibling element that remains unknown.
            return [deepcopy(v) for v in value if v in elements]
        if status not in {'reviewed', 'verified'}:
            return None
    return deepcopy(g[key])


def scoped_exemption(route, out, rows, *, conflict=False, pending=False):
    """Return publication state only for the narrow safe default/option split."""
    from . import tstation, policy_intervals, kimi_primary
    g = out.get('guidance') or {}
    if (g.get('disposition') != 'VISA_EXEMPT' or conflict or pending
            or out.get('held') or out.get('stale') or out.get('operator_released')):
        return None
    prov = out.get('source_verified')
    if (not tstation.verdict_provenance_supported(prov)
            or _reviewed_route_value(g, prov, 'disposition') != 'VISA_EXEMPT'):
        return None
    # Recheck bounds even when a caller supplied a pre-built reader envelope.
    bounded = policy_intervals.annotate(g, prov, route)
    if bounded.get(policy_intervals.MARKER):
        return None
    products = g.get('visa_products')
    if (not isinstance(products, list) or len(products) != len(rows)
            or any(not isinstance(p, dict) or not p.get('type') for p in products)):
        return None
    default_rows = [r for r in rows if not r.get('_separate_permission')]
    blocked = [i for i, r in enumerate(rows) if r.get('_evidence_low')]
    if (not default_rows or any(r.get('_evidence_low') for r in default_rows)
            or not blocked or any(not rows[i].get('_separate_permission') for i in blocked)):
        return None
    # The parent default must itself have evidence; a quoted optional product
    # cannot establish an unsupported route verdict.
    parent = tstation.records_for_route(route, dict(g, visa_products=[]), prov)
    if not parent or any(r.get('_evidence_low') for r in parent):
        return None
    published = [i for i in range(len(products)) if i not in blocked]
    try:
        safe_g = {key: _default_text(_reviewed_route_value(g, prov, key))
                  for key in _ROUTE_FIELDS}
    except _MixedDefaultCondition:
        return None
    safe_g = {k: v for k, v in safe_g.items() if v not in (None, '', [], {})}
    if not safe_g.get('permitted_stay') and isinstance(safe_g.get('permitted_stay_days'), int):
        safe_g['permitted_stay'] = f"Up to {safe_g['permitted_stay_days']} days"
    # Definitions of the independently evidenced default; no generated fee,
    # application steps or route label can reintroduce an optional product.
    safe_g.update(disposition='VISA_EXEMPT', visa_category='Visa-free entry',
                  application_channel='not_required',
                  government_fee={'amount': 0, 'currency': None},
                  source_url=prov.get('source_url'),
                  visa_products=[])
    blocked_names = [str(products[i]['type']).casefold() for i in blocked]
    for i in published:
        p = products[i]
        # Keep a published product's own conditions; do not silently erase a
        # restriction. A note that mixes in a quarantined sibling requires
        # review before this narrow projection can safely separate it.
        if any(name in str(p.get('notes') or '').casefold() for name in blocked_names):
            return None
        safe_p = {k: deepcopy(p[k]) for k in _PRODUCT_FIELDS if k in p}
        if rows[i].get('_separate_permission') and _mentions_other_permission(str(p.get('notes') or '')):
            # A published alternative may itself mention a quarantined
            # consular/paper option. Keep its conditions intact in QC and
            # fail closed until that prose is separately scoped.
            return None
        if not rows[i].get('_separate_permission') and safe_p.get('notes'):
            try:
                safe_p['notes'] = _default_text(safe_p['notes'])
            except _MixedDefaultCondition:
                return None
        # Nested evidence containers remain in QC, never in traveler claims.
        safe_g['visa_products'].append(safe_p)
    # A known conditional default cannot be stripped of the conditions that
    # distinguish it. If projection cannot retain them, keep the route held.
    detail = str(g.get('requirement_detail') or '').lower()
    if ('conditional' in detail and safe_g.get('application_channel_detail')
            and safe_g['application_channel_detail'] not in (safe_g.get('exceptions') or [])):
        safe_g['exceptions'] = list(safe_g.get('exceptions') or []) + [safe_g['application_channel_detail']]
    if 'conditional' in detail and (not safe_g.get('exceptions')
            or safe_g.get('requirement_detail') != g.get('requirement_detail')):
        return None
    if kimi_primary.serve_time_invariants(safe_g):
        return None
    proof = {k: deepcopy(prov[k]) for k in _PROOF_META if k in prov}
    proof['fields'] = ['disposition']
    return {'guidance': safe_g, 'source_verified': proof,
            'product_publication': [
                {'product_index': i, 'held': i in blocked,
                 'state': 'withheld' if i in blocked else 'published',
                 'reason': 'optional_product_evidence_pending' if i in blocked else 'default_or_product_evidence_supported'}
                for i in range(len(products))],
            'publication_state': 'partial', 'withheld_product_count': len(blocked)}


def project_reader(route, out, scope):
    """Allowlist claim containers so raw alternatives cannot leak indirectly."""
    from . import kimi_primary
    from .records_guard import _HELD_STATUS_FIELDS
    projected = {k: deepcopy(v) for k, v in out.items()
                 if k in _HELD_STATUS_FIELDS and k != 'approximate_reason'}
    projected.update(scope)
    projected.update(held=False, review_required=False)
    projected['apply_steps'] = kimi_primary.canonical_steps(scope['guidance'])
    projected['workflow_plan'] = kimi_primary.derive_workflow_plan(scope['guidance'])
    projected['advisories'] = kimi_primary.deterministic_advisories(route, scope['guidance'])
    return projected
