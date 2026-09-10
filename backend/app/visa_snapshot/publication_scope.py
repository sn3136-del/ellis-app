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
        if not _product_interval_current(p, route):
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
        if not rows[i].get('_separate_permission'):
            _inherit_default_stay(safe_p, safe_g, p)
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


def _inherit_default_stay(product, guidance, original=None):
    """A default product shares its reviewed route stay, never a sibling's.

    Callers already established that this is the default permission. Notes
    may describe passport validity and must never substitute for stay.
    """
    proofs = ((original or product).get('field_provenance') or {})
    for field in ('max_stay_days', 'permitted_stay'):
        if field in proofs:
            proof = proofs[field]
            if not isinstance(proof, dict) or proof.get('status') not in (None, 'reviewed', 'verified'):
                return
    if product.get('max_stay_days') in (None, '') and not product.get('permitted_stay'):
        stay = guidance.get('permitted_stay')
        if isinstance(stay, str) and stay.strip():
            product['permitted_stay'] = stay
        elif isinstance(guidance.get('permitted_stay_days'), int) and guidance['permitted_stay_days'] > 0:
            product['permitted_stay'] = f"Up to {guidance['permitted_stay_days']} days"


def _empty_fee(value):
    return value in (None, {}) or (isinstance(value, dict)
        and set(value) <= {'amount', 'currency'} and all(v is None for v in value.values()))


def _fee_supported(value, proof):
    """Retain a monetary claim only when its own quoted amount is supported."""
    from . import tstation
    from .evidence_validator import field_value_supported
    if _empty_fee(value):
        return True
    if (not isinstance(proof, dict) or proof.get('status') not in (None, 'reviewed', 'verified')
            or not tstation.verdict_provenance_supported(dict(proof, fields=['disposition']))):
        return False
    passages = [proof.get('quote')] + list(proof.get('additional_quotes') or [])
    passages.extend(e.get('quote') for e in proof.get('supporting_evidence') or [] if isinstance(e, dict))
    # Older audited overlays kept their literal quotation before a Scope
    # note. The note can name a withdrawn wrong price and is not evidence.
    note = str(proof.get('note') or '')
    legacy = re.search(r'(?s)Quote:\s*(.*?)\s+Scope:', note)
    if legacy:
        passages.append(legacy.group(1))
    text = '\n'.join(q for q in passages if isinstance(q, str))
    return bool(text) and field_value_supported('government_fee', value, text)


def _proof_subject_matches(proof, route, product=None):
    if not isinstance(proof, dict):
        return False
    if 'subject' not in proof:
        return True  # Existing attributed legacy reviews lack a subject object.
    subject = proof['subject']
    if not isinstance(subject, dict):
        return False
    expected = {k: route.get(k) for k in ('passport_nationality', 'destination_country', 'travel_purpose')}
    expected['travel_document_type'] = route.get('travel_document_type') or 'ordinary_passport'
    if product is not None:
        expected.update(product_type=product.get('type'), disposition=product.get('disposition') or 'VISA_REQUIRED',
                        requirement_detail=product.get('requirement_detail'))
    elif subject.get('product_type'):
        return False
    route_keys = {'passport_nationality', 'destination_country', 'travel_purpose', 'travel_document_type'}
    return all(subject.get(k) == v for k, v in expected.items() if k in route_keys or k in subject)


def _proof_scope_supported(proof, route, product=None):
    from .evidence_validator import jurisdiction_matches
    return (isinstance(proof, dict)
            and jurisdiction_matches(proof.get('source_url') or '', route['destination_country'])
            and _proof_subject_matches(proof, route, product))


def _required_route_value(g, prov, key, route, product):
    proofs = prov.get('field_provenance') or {}
    if key in proofs and not _proof_scope_supported(proofs[key], route, product):
        return None
    return _reviewed_route_value(g, prov, key)


def _product_interval_current(product, route):
    from . import policy_intervals
    prov = {'fields': ['disposition', 'requirement_detail'],
            'field_provenance': product.get('field_provenance') or {}}
    return not policy_intervals.annotate(product, prov, route).get(policy_intervals.MARKER)


def _portal_bound(value, proof):
    if not isinstance(value, str) or not isinstance(proof, dict):
        return False
    if value.rstrip('/') == str(proof.get('source_url') or '').rstrip('/'):
        return True
    quoted = [proof.get('quote')] + list(proof.get('additional_quotes') or [])
    legacy = re.search(r'(?s)Quote:\s*(.*?)\s+Scope:', str(proof.get('note') or ''))
    if legacy:
        quoted.append(legacy.group(1))
    return any(isinstance(q, str) and value in q for q in quoted)


def _documented_fee_cells(g, prov, product, route):
    # Keep documented absence; raw status flags alone cannot promote unknown.
    proof = (prov.get('field_provenance') or {}).get('government_fee') or {}
    own = (product.get('field_provenance') or {}).get('fee') or {}
    def documented(p):
        return isinstance(p, dict) and (p.get('status') in {'not_published', 'not-published'}
            or str(p.get('reason') or '').startswith('Not published by the destination:'))
    from .evidence_validator import jurisdiction_matches
    def own_scope(p):
        return _proof_subject_matches(p, route, product) and (not p.get('source_url')
            or jurisdiction_matches(p['source_url'], route['destination_country']))
    if (_empty_fee(g.get('government_fee')) and documented(proof) and documented(own)
            and own_scope(proof) and own_scope(own)):
        return sorted(set(g.get('unpublished_fields') or ()) & {'visa_fee_amount', 'visa_fee_currency'})
    return []


def _explicit_product_values_supported(product, route):
    """A value cannot survive an explicit unknown or malformed field proof."""
    from . import tstation
    for field in ('disposition', 'requirement_detail', 'entry', 'validity', 'max_stay_days', 'fee', 'notes', 'processing_time',
                  'application_channel', 'application_channel_detail', 'required_documents',
                  'entry_requirements', 'exceptions', 'forms'):
        value = product.get(field)
        empty = _empty_fee(value) if field == 'fee' else value in (None, '', [], {})
        proofs = product.get('field_provenance') or {}
        if field in {'required_documents', 'entry_requirements', 'exceptions',
                     'application_channel', 'application_channel_detail'} and field in proofs:
            own = proofs[field]
            if (not isinstance(own, dict) or own.get('status') not in (None, 'reviewed', 'verified')
                    or not _proof_scope_supported(own, route, product)):
                return False
        if field == 'fee' and not _fee_supported(value, proofs.get('fee')):
            return False
        if empty or field not in proofs:
            continue
        proof = proofs[field]
        if (not _proof_scope_supported(proof, route, product)
                or proof.get('status') not in (None, 'reviewed', 'verified')
                or not tstation.verdict_provenance_supported(dict(proof, fields=['disposition']))):
            return False
    return True


# Required e-visas keep their application facts. This scope deliberately does
# not support paper-visa defaults or multiple published default products yet.
_REQUIRED_ROUTE_FIELDS = _ROUTE_FIELDS | frozenset({
    'disposition', 'visa_category', 'government_fee', 'application_channel',
    'official_portal_url', 'processing_time', 'entry_requirements', 'forms',
    'account_registration_steps', 'payment_process', 'submission_process',
    'appointment_required', 'biometrics_required', 'interview_required',
})
_REQUIRED_CONDITIONS = frozenset({
    'required_documents', 'passport_validity', 'passport_validity_requirement',
    'entry_requirements', 'exceptions', 'application_channel_detail',
})
_SEPARATE_VISA = re.compile(
    r'\b(?:paper|sticker|regular|traditional|conventional|consular|embassy)'
    r'(?:\s+[a-z-]+){0,3}\s+(?:visas?|options?|products?)\b|\bvisa\s+on\s+arrival\b|'
    r'纸质签证|紙本簽證|贴纸签证|貼紙簽證|落地签|落地簽', re.I)


def _contains_withheld_offer(value, blocked_names):
    """Fail closed on a separate visa offer; never trim entry conditions.

    A restriction to the e-visa's eligible purposes, or travel on a visa
    already held, does not advertise eligibility for a new consular product.
    """
    if isinstance(value, dict):
        return any(_contains_withheld_offer(v, blocked_names) for v in value.values())
    if isinstance(value, list):
        return any(_contains_withheld_offer(v, blocked_names) for v in value)
    if not isinstance(value, str):
        return False
    low = value.casefold()
    if any(name in low for name in blocked_names):
        return True
    for sentence in re.split(r'(?<=[.!?。！？])\s+', value):
        if not _SEPARATE_VISA.search(sentence):
            continue
        # Only complete simple exclusions are allowed. A safe first clause
        # cannot lend support to a second clause offering a different visa.
        exclusions = (
            r'Other purposes need the appropriate regular visa[.!?]?',
            r'Trips for purposes other than private, business, tourism or listed events need a regular paper visa[.!?]?',
            r'Travellers holding a valid regular [A-Za-z -]+ visa do not need an e-visa[.!?]?',
            r'Holders of a valid regular [A-Za-z -]+ visa do not need an e-visa and travel on that visa instead[.!?]?',
            r'If you already have a valid [A-Za-z -]+ visa, travel under that visa; a new e-visa does not extend its validity[.!?]?',
        )
        if not any(re.fullmatch(rule, sentence.strip(), re.I) for rule in exclusions):
            return True
    return False


def scoped_required_evisa(route, out, rows, *, conflict=False, pending=False):
    """Publish one evidenced required e-visa, quarantining separate products.

    No visa-free defaults are synthesized: required fees, channels, documents
    and source attribution survive intact. Unknown fee amounts stay unknown.
    Unreviewed process arrays do not override the reviewed application lane.
    """
    from . import tstation, policy_intervals, kimi_primary
    from .evidence_validator import jurisdiction_matches
    g = out.get('guidance') or {}
    if (g.get('disposition') != 'VISA_REQUIRED' or g.get('requirement_detail') != 'evisa'
            or conflict or pending or out.get('held') or out.get('stale')
            or out.get('operator_released')):
        return None
    prov = out.get('source_verified')
    if (not tstation.verdict_provenance_supported(prov)
            or _reviewed_route_value(g, prov, 'disposition') != 'VISA_REQUIRED'
            or _reviewed_route_value(g, prov, 'requirement_detail') != 'evisa'):
        return None
    if policy_intervals.annotate(g, prov, route).get(policy_intervals.MARKER):
        return None
    products = g.get('visa_products')
    if (not isinstance(products, list) or len(products) != len(rows)
            or any(not isinstance(p, dict) or not p.get('type') for p in products)):
        return None
    blocked = [i for i, r in enumerate(rows) if r.get('_evidence_low')]
    published = [i for i in range(len(products)) if i not in blocked]
    if (not blocked or len(published) != 1
            or rows[published[0]].get('_separate_permission')
            or any(not rows[i].get('_separate_permission') for i in blocked)):
        return None
    parent = tstation.records_for_route(route, dict(g, visa_products=[]), prov)
    if not parent or any(r.get('_evidence_low') for r in parent):
        return None
    product = products[published[0]]
    if not _product_interval_current(product, route):
        return None
    # An explicit unknown/malformed/partial prerequisite cannot be omitted to
    # make a default look publishable. Exact values, not inferred equivalents.
    for key in _REQUIRED_CONDITIONS:
        if g.get(key) not in (None, '', [], {}) and _required_route_value(g, prov, key, route, product) != g[key]:
            return None
    safe_g = {key: deepcopy(value) for key in _REQUIRED_ROUTE_FIELDS
              if (value := _required_route_value(g, prov, key, route, product)) not in (None, '', [], {})}
    if (safe_g.get('disposition') != 'VISA_REQUIRED' or safe_g.get('requirement_detail') != 'evisa'
            or safe_g.get('application_channel') != 'online_portal'
            or not safe_g.get('required_documents')
            or not jurisdiction_matches(safe_g.get('official_portal_url') or '', route['destination_country'])
            or not _portal_bound(safe_g.get('official_portal_url'),
                                 (prov.get('field_provenance') or {}).get('official_portal_url'))):
        return None
    # Keep absence as absence. A nonempty fee without its own reviewed value
    # blocks this projection rather than silently becoming free or unknown.
    fee = g.get('government_fee')
    if (not _fee_supported(fee, (prov.get('field_provenance') or {}).get('government_fee'))
            or (not _empty_fee(fee) and _required_route_value(g, prov, 'government_fee', route, product) != fee)):
        return None
    own_fee_proof = (product.get('field_provenance') or {}).get('fee')
    if not _empty_fee(fee) and isinstance(own_fee_proof, dict) and own_fee_proof.get('status') not in (None, 'reviewed', 'verified'):
        return None
    safe_g['government_fee'] = deepcopy(fee)
    safe_g['unpublished_fields'] = _documented_fee_cells(g, prov, product, route)
    safe_g['source_url'] = prov.get('source_url')
    blocked_names = [str(products[i]['type']).casefold() for i in blocked]
    if (not jurisdiction_matches(product.get('source_url') or prov.get('source_url') or '', route['destination_country'])
            or not _explicit_product_values_supported(product, route)):
        return None
    own_fields = _PRODUCT_FIELDS | {'processing_time', 'application_channel', 'application_channel_detail',
        'required_documents', 'entry_requirements', 'exceptions', 'forms'}
    safe_product = {k: deepcopy(product[k]) for k in own_fields if k in product}
    if product.get('application_channel') not in (None, safe_g['application_channel']):
        return None
    if _contains_withheld_offer(safe_g, blocked_names) or _contains_withheld_offer(safe_product, blocked_names):
        return None
    # The reader primarily displays route-level application facts. Retain
    # the verified default product's additional prerequisites there too,
    # without replacing any already-reviewed parent condition.
    for key in ('required_documents', 'exceptions', 'forms'):
        own = safe_product.get(key)
        if isinstance(own, list):
            prior = list(safe_g.get(key) or [])
            safe_g[key] = prior + [deepcopy(v) for v in own if v not in prior]
    for key in ('processing_time', 'application_channel_detail', 'entry_requirements'):
        own = safe_product.get(key)
        prior = safe_g.get(key)
        if isinstance(own, str) and own and own not in str(prior or ''):
            safe_g[key] = (str(prior).rstrip() + ' ' + own) if prior else own
    _inherit_default_stay(safe_product, safe_g, product)
    safe_g['visa_products'] = [safe_product]
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
    projected.update(kimi_primary.application_instructions(scope['guidance'], route=route, source_verified=scope.get('source_verified')))
    projected['workflow_plan'] = kimi_primary.derive_workflow_plan(scope['guidance'], route=route, source_verified=scope.get('source_verified'))
    projected['advisories'] = kimi_primary.deterministic_advisories(route, scope['guidance'])
    return projected
