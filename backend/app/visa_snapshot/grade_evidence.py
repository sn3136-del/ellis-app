"""Recorded field ownership for the display grade, never publication release.

A container review can establish that products were checked, but cannot undo
an individual field's explicit unknown status or certify a newly filled value.
"""
from __future__ import annotations

import json
import re

_EMPTY = (None, '', [], {})


def _same_value(left, right):
    try:
        return json.dumps(left, sort_keys=True, ensure_ascii=False, allow_nan=False) == json.dumps(
            right, sort_keys=True, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return False


def _quoted_passages(proof):
    """Extract source quotations, never the AI reviewer's scope annotation."""
    raw = proof.get('quotes', [])
    if not isinstance(raw, (list, tuple)) or any(not isinstance(q, str) for q in raw):
        return []
    single = proof.get('quote')
    if single is not None and not isinstance(single, str):
        return []
    quotes = [q.strip() for q in (single, *raw) if isinstance(q, str) and q.strip()]
    note = proof.get('note')
    if isinstance(note, str) and 'Quote:' in note:
        quotes.append(note.split('Quote:', 1)[1].split('Scope:', 1)[0].strip())
    elif not quotes and proof.get('verifier') == 'human' and isinstance(note, str):
        # The established manual-proof contract accepts an attributed human
        # field assertion; its actual words must still support the value.
        quotes.append(note)
    return quotes


def _value_supported(field, value, proof):
    from .evidence_validator import field_value_supported, quote_in_text
    passages = _quoted_passages(proof)
    if not passages:
        return False
    if field == 'required_documents':
        values = value if isinstance(value, list) else [value]
        def supported_document(document):
            supported = False
            for quote in passages:
                for statement in re.split(r'[\n;；]+|(?<=[.!?。！？])\s+', quote):
                    if not field_value_supported(field, document, statement):
                        continue
                    # Read all matching evidence: a later contrary statement
                    # must not be hidden by an earlier convenient match.
                    if re.search(r"\b(?:not|no|without|unless|except|only|if|when|optional)\b|"
                                 r"provided that|subject to|无需|無需|毋須|不得|仅|僅|如果|除非", statement, re.I):
                        literal = re.sub(r'^\s*(?:required documents?|documents?)\s*:\s*', '', statement, flags=re.I)
                        if not (quote_in_text(document, literal) and quote_in_text(literal, document)):
                            return False
                    supported = True
            return supported
        return bool(values) and all(supported_document(document) for document in values)
    if field == 'validity' and not any(re.search(r'valid|有效', q, re.I) for q in passages):
        return False
    for quote in passages:
        for statement in re.split(r'[\n;；]+|(?<=[.!?。！？])\s+', quote):
            if not field_value_supported(field, value, statement):
                continue
            # A contrary assertion of this same value cannot certify it.
            # Keep unrelated 'no refunds' prose separate from the fee fact.
            for negative in re.finditer(r'\b(?:not|no|never)\b|不是|并非|並非|不得', statement, re.I):
                tail = statement[negative.end():]
                if (field_value_supported('value', value, tail)
                        or re.match(r'\s+(?:required|payable|charged|applicable|available|valid|allowed|permitted|accepted)\b', tail, re.I)):
                    # Preserve an entire explicitly qualified string, rather
                    # than upgrading a scalar extracted from that statement.
                    if not (isinstance(value, str) and quote_in_text(value, statement)
                            and quote_in_text(statement, value)):
                        return False
    return any(field_value_supported(field, value, quote) for quote in passages)


def _proof_supported(proof, route, product, field, value):
    from . import tstation
    from .evidence_validator import jurisdiction_matches
    if not isinstance(proof, dict):
        return False
    if 'reviewed_value' in proof and not _same_value(proof['reviewed_value'], value):
        return False
    status = proof.get('status')
    if status in ('partial', 'partially_reviewed'):
        elements = proof.get('verified_elements', proof.get('reviewed_elements'))
        if (not isinstance(value, list) or not value or not isinstance(elements, list)
                or any(not any(_same_value(item, reviewed) for reviewed in elements) for item in value)):
            return False
    elif status not in (None, 'reviewed', 'verified'):
        return False
    if not tstation.verdict_provenance_supported(dict(proof, fields=['disposition'])):
        return False
    if not jurisdiction_matches(proof.get('source_url') or '', route.get('destination_country', '')):
        return False
    from .policy_intervals import _date, _today
    selected = _date(route.get('arrival_date')) or _today()
    start, end = _date(proof.get('effective_from')), _date(proof.get('effective_to'))
    if ((proof.get('effective_from') is not None and start is None)
            or (proof.get('effective_to') is not None and end is None)
            or (start and end and end < start)
            or (start and selected < start) or (end and selected > end)):
        return False
    if 'subject' in proof:
        subject = proof['subject']
        if not isinstance(subject, dict):
            return False
        expected = dict(route, travel_document_type=route.get('travel_document_type') or 'ordinary_passport')
        if product is not None:
            expected.update(product_type=product.get('type'), disposition=product.get('disposition'),
                            requirement_detail=product.get('requirement_detail'))
        if any(key not in expected or expected[key] != value for key, value in subject.items()):
            return False
        if any(subject.get(key) != expected.get(key) for key in
               ('passport_nationality', 'destination_country', 'travel_purpose', 'travel_document_type')):
            return False
    return _value_supported(field, value, proof)


def required_values_supported(row, g, checked, route, provenance):
    from . import tstation as t
    products = g.get('visa_products')
    products = [p for p in products if isinstance(p, dict) and p.get('type')] if isinstance(products, list) else []
    index = row.get('_product_index')
    product = products[index] if isinstance(index, int) and 0 <= index < len(products) else None
    if g.get('type') and t._clean_text(str(g.get('type'))) == row.get('visa_type_name'):
        product = g  # Separate permission already lost parent field inheritance.
    if product is not None and t._clean_text(str(product.get('type'))) != row.get('visa_type_name'):
        return False
    own_proofs = product.get('field_provenance') if product is not None else {}
    own_proofs = own_proofs if isinstance(own_proofs, dict) else {}
    route_proofs = (provenance or {}).get('field_provenance')
    route_proofs = route_proofs if isinstance(route_proofs, dict) else {}

    def parent(field):
        if row.get('_separate_permission') or field not in checked:
            return False
        # Productless route grounding retains its existing contract. This
        # guard addresses product wrappers and parent-to-product inheritance.
        if product is None:
            return True
        # An explicit field review supersedes a broad recorded field list.
        if field in route_proofs:
            return _proof_supported(route_proofs[field], route, None, field, g.get(field))
        return _proof_supported(provenance, route, None, field, g.get(field))

    def own(field):
        return bool(product is not None and product.get(field) not in _EMPTY
                    and field in own_proofs
                    and _proof_supported(own_proofs[field], route, product, field, product[field]))

    def supported(fields, parent_fields=(), definition=False):
        if product is None:
            return definition or any(parent(field) for field in parent_fields)
        # A field explicitly withdrawn as unsupported must never inherit its
        # old value's parent credit, including a value inherited by the mapper.
        values = [field for field in fields if product.get(field) not in _EMPTY]
        for field in fields:
            if ((field in values or not values) and field in own_proofs
                    and not _proof_supported(own_proofs[field], route, product, field, product.get(field))):
                return False
        if values:
            return all(own(field) or any(
                _same_value(product[field], g.get(parent_field)) and parent(parent_field)
                for parent_field in parent_fields) for field in values)
        return definition or any(parent(field) for field in parent_fields)

    exempt = row.get('visa_requirement') == 'Visa-free'
    verdict = 'disposition' in checked
    stay = supported(('permitted_stay', 'max_stay_days'), ('permitted_stay', 'permitted_stay_days'))
    # Literal entry words in a reviewed product name are a definition, not
    # permission to certify a conflicting or explicitly unreviewed entry field.
    named_entries = bool(product and product.get('entry') in _EMPTY and 'entry' not in own_proofs
                         and own('type') and t._entries(product.get('type')) == row.get('entries'))
    checks = {
        'visa_type_name': supported(('type',), ('visa_category',), exempt and verdict),
        'max_stay_duration': stay,
        'validity_duration': stay if exempt else supported(('validity',), ('validity',)),
        'entries': supported(('entry',), (), (exempt and verdict) or named_entries),
        'visa_fee_amount': supported(('fee',), ('government_fee',), exempt and verdict),
        'application_method': supported(('application_channel', 'application_channel_detail'),
                                         ('application_channel',),
                                         product is None and 'requirement_detail' in checked and
                                         row.get('visa_requirement_detail') in {
                                             'eVisa', 'ETA Electronic Authorization',
                                             'Paper Visa on Arrival', 'eVisa on Arrival'}),
        'required_documents': supported(('required_documents',), ('required_documents',)),
    }
    statuses = t.field_status(row)
    return all(supported for field, supported in checks.items() if statuses.get(field) == 'filled')
