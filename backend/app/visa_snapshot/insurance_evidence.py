"""Reader-only insurance claims with explicit field and permission ownership.

A legacy false is an unknown, not proof of an entry exemption. The raw cache,
review stores and QC inputs remain untouched. This does not promote a route,
renew evidence, or replace required-document/condition text with a Boolean.
"""
from copy import deepcopy
import re

FIELD = 'insurance_required'
CONTRACT = 'ellis.insurance-evidence.v1'


def _supported(value, proof, route, product=None):
    from . import tstation, policy_intervals
    from .evidence_validator import jurisdiction_matches
    if type(value) is not bool or not isinstance(proof, dict):
        return None
    if proof.get('status') not in ('reviewed', 'verified'):
        return None
    if not tstation.verdict_provenance_supported(dict(proof, fields=['disposition'])):
        return None
    if not jurisdiction_matches(proof.get('source_url') or '', route.get('destination_country') or ''):
        return None
    subject = proof.get('subject')
    expected = {key: route.get(key) for key in
                ('passport_nationality', 'destination_country', 'travel_purpose')}
    expected['travel_document_type'] = route.get('travel_document_type') or 'ordinary_passport'
    if (not isinstance(subject, dict) or any(not value for value in expected.values())
            or any(subject.get(key) != value for key, value in expected.items())):
        return None
    if product is None:
        if subject.get('product_type'):
            return None
    elif subject.get('product_type') != product.get('type'):
        return None
    # A product-owned proof may not reuse a sibling's disposition/detail.
    if product is not None and any(key in subject and subject[key] != product.get(key)
                                   for key in ('disposition', 'requirement_detail')):
        return None
    product_fields = {'product_type', 'disposition', 'requirement_detail'} if product is not None else set()
    for key in set(subject) - set(expected) - product_fields:
        if key not in route or subject[key] != route[key]:
            return None  # Residence, transit/date or a personal condition may not be ignored.
    bounded = policy_intervals.annotate({'disposition': 'VISA_REQUIRED'},
        {'fields': ['disposition'], 'field_provenance': {'disposition': proof}}, route)
    if bounded.get(policy_intervals.MARKER):
        return None
    quote = proof.get('quote')
    if not isinstance(quote, str) or not quote.strip():
        return None
    statements = [part.strip() for part in re.split(r'[.!?。！？;；]+', quote) if part.strip()]
    if len(statements) != 1 or re.search(r'\b(?:but|however|although|whereas)\b', quote, re.I):
        return None  # A later pronoun/exception must not reverse the selected sentence unnoticed.
    # A Boolean cannot represent an unselected personal/conditional exception.
    # Keep the full source-backed conditions elsewhere; this small card waits
    # for an explicit unconditional statement for the reviewed subject.
    if re.search(r'\b(?:if|unless|except|only|vaccinated|unvaccinated|residents?|workers?|students?)\b|'
                 r'如果|除外|仅限|僅限|除非', quote, re.I):
        return None
    # Bind the obligation itself to entry/application. A bare country-entry
    # mention can be incidental to a tour, museum or residence-permit service.
    # Full clauses deliberately leave unfamiliar/conditional prose unknown.
    sentence = re.sub(r'\s+', ' ', statements[0]).strip()
    insurance = r'(?:travel |medical |health )?insurance'
    people = r'(?:all )?(?:foreigners|foreign (?:nationals|tourists|visitors)|travellers|travelers|visitors|tourists)'
    targets = {
        'entry': rf'(?:entry into the country|arrival in the country|{people} entering the country)',
        'visa_application': r'(?:(?:tourist|visitor|schengen) )?visa applications?',
    }
    negative = r'(?:is not (?:required|mandatory)|is optional|will (?:also )?not be a prerequisite)'
    positive = r'(?:is |will be )?(?:required|mandatory)'
    for scope, target in targets.items():
        if scope == 'visa_application' and product is None:
            continue
        claim = rf'{insurance} {positive if value else negative} for {target}'
        if re.fullmatch(claim, sentence, re.I):
            return scope
        if not value and re.fullmatch(rf'no {insurance} is required for {target}', sentence, re.I):
            return scope
    if value and re.fullmatch(rf'{people} must (?:hold|have|obtain|purchase) (?:valid )?{insurance} to enter the country', sentence, re.I):
        return 'entry'
    chinese = (r'入境(?:该国|該國)(?:必须|必須)(?:购买|購買|持有)(?:旅行|医疗|醫療)?(?:保险|保險)'
               if value else r'入境(?:该国|該國)(?:无需|無需)(?:购买|購買)?(?:旅行|医疗|醫療)?(?:保险|保險)')
    return 'entry' if re.fullmatch(chinese, sentence) else None


def _state(value, proof, route, product=None, *, stale=False):
    scope = None if stale else _supported(value, proof, route, product)
    result = {'contract': CONTRACT, 'field': FIELD,
              'status': 'verified' if scope else 'unknown',
              'scope': scope, 'value': value if scope else None}
    if scope:
        result.update(source_url=proof['source_url'], verified_at=proof['verified_at'])
    return result


def project_reader(route, out):
    """Project only served insurance cells after the original publication gate.

    Products own their proofs. Neither a parent route nor an optional sibling
    can certify another product's insurance. An active hold/pending status is
    never cleared, and held payloads retain the normal server redaction path.
    """
    original = out.get('guidance')
    if (not isinstance(original, dict) or out.get('held') or out.get('detail_pending')):
        return out
    g = deepcopy(original)
    result = dict(out, guidance=g)
    provenance = out.get('source_verified')
    provenance = provenance if isinstance(provenance, dict) else {}
    proofs = provenance.get('field_provenance')
    proofs = proofs if isinstance(proofs, dict) else {}
    claimed = provenance.get('fields')
    claimed = claimed if isinstance(claimed, (list, tuple, set)) else ()
    states = {}
    if FIELD in g:
        proof = proofs.get(FIELD) if FIELD in claimed else None
        states[FIELD] = _state(g[FIELD], proof, route, stale=bool(out.get('stale')))
        g[FIELD] = states[FIELD]['value']
    # Ignore any untrusted model-provided projection metadata on the envelope.
    result.pop('requirement_evidence', None)
    if states:
        result['requirement_evidence'] = states
    products = g.get('visa_products')
    if isinstance(products, list):
        for product in products:
            if not isinstance(product, dict):
                continue
            product.pop('requirement_evidence', None)
            if FIELD not in product:
                continue
            own = product.get('field_provenance')
            own = own if isinstance(own, dict) else {}
            state = _state(product[FIELD], own.get(FIELD), route, product,
                           stale=bool(out.get('stale')))
            product[FIELD] = state['value']
            product['requirement_evidence'] = {FIELD: state}
    return result
