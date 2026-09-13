"""Read-only, field-owned quotations for the lazy QC evidence drawer.

This is provenance presentation, never a new grade, policy or publication
decision. A reference link or reviewer summary is not a source quotation.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from . import tstation
from .grade_evidence import _same_value, _value_supported

# Each dictionary cell names its product fields and route-level equivalents.
FIELDS = {
    'visa_requirement': (('disposition',), ('disposition',)),
    'visa_requirement_detail': (('requirement_detail',), ('requirement_detail',)),
    'visa_type_name': (('type',), ('visa_category',)),
    'validity_duration': (('validity',), ('validity',)),
    'validity_unit': (('validity',), ('validity',)),
    'max_stay_duration': (('max_stay_days', 'permitted_stay'), ('permitted_stay_days', 'permitted_stay')),
    'max_stay_unit': (('max_stay_days', 'permitted_stay'), ('permitted_stay_days', 'permitted_stay')),
    'entries': (('entry',), ('entries',)),
    'processing_min_days': (('processing_time',), ('processing_time',)),
    'processing_unit': (('processing_time',), ('processing_time',)),
    'visa_fee_amount': (('fee',), ('government_fee',)),
    'visa_fee_currency': (('fee',), ('government_fee',)),
    'application_method': (('application_channel', 'application_channel_detail'),
                           ('application_channel', 'application_channel_detail')),
    'required_documents': (('required_documents',), ('required_documents',)),
    'consulate_district': (('consular_jurisdiction',), ('consular_jurisdiction',)),
    'entry_requirements': (('entry_requirements', 'passport_validity', 'arrival_card'),
                           ('entry_requirements', 'passport_validity', 'passport_validity_requirement',
                            'arrival_card', 'onward_travel_evidence', 'accommodation_evidence',
                            'financial_evidence', 'insurance_required', 'health_requirements')),
    'special_conditions': (('notes',), ('exceptions',)),
    'info_validity': (('policy_valid_until',), ('policy_valid_until',)),
}
_EMPTY = (None, '', [], {})


def revision(row: dict) -> str:
    """Bind a lazy response to exactly the displayed product and field values."""
    values = {k: row.get(k) for k in (*tstation.FIELD_ORDER, '_cache_key', '_product_index',
                                     'max_stay_text', 'validity_text', 'processing_text',
                                     'visa_fee_qualifier', '_disputed', '_evidence_version')}
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, default=str).encode()).hexdigest()


def _url(value):
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme in ('https', 'http') and parsed.hostname and not parsed.username and not parsed.password:
            return value
    except ValueError:
        pass
    return None


def _quotes(proof, kind, field=None):
    """Return only explicit verbatim quote slots, with their own page URLs."""
    if not isinstance(proof, dict):
        return []
    result = []
    stamp = proof.get('verified_at') or proof.get('checked_at')
    def add(quote, url, when=stamp):
        if isinstance(quote, str) and quote.strip() and _url(url):
            item = {'quote': quote, 'source_url': url, 'kind': kind}
            if isinstance(when, str):
                item['verified_at'] = when
            if item not in result:
                result.append(item)
    add(proof.get('quote'), proof.get('source_url'))
    add(proof.get('source_quote'), proof.get('source_url'))
    for quote in proof.get('quotes', []) if isinstance(proof.get('quotes'), list) else []:
        add(quote, proof.get('source_url'))
    if field is not None and isinstance(proof.get('quotes'), dict):
        quote = proof['quotes'].get(field)
        if isinstance(quote, dict):
            add(quote.get('quote'), quote.get('source_url') or quote.get('url'))
        else:
            add(quote, proof.get('source_url'))
    # Scope excerpts share the primary page only when stored as plain text;
    # supporting page objects must carry their own URL.
    for key in ('supporting_evidence', 'additional_quotes', 'scope_quotes'):
        for item in proof.get(key, []) if isinstance(proof.get(key), list) else []:
            if isinstance(item, dict):
                add(item.get('quote'), item.get('source_url') or item.get('url'),
                    item.get('verified_at') or item.get('checked_at') or stamp)
            elif key in ('scope_quotes', 'additional_quotes'):
                add(item, proof.get('source_url'))
    # Historical converters sometimes stored an explicitly delimited excerpt
    # in this exact format. Never turn an arbitrary reviewer note or everything
    # following an unclosed "Quote:" marker into a quotation.
    if not result and isinstance(proof.get('note'), str):
        match = re.fullmatch(r'\s*Quote:\s*(.+?)\s+(?:Scope|Subject):\s*.+',
                             proof['note'], re.S)
        if match:
            add(match.group(1), proof.get('source_url'))
    return result


def _owned(proof, route, product, field, value, *, checked=False):
    from .source_authority import is_competent
    if not isinstance(proof, dict) or proof.get('verifier') == 'public':
        return []
    if proof.get('status') not in (None, 'reviewed', 'verified'):
        return []
    if 'reviewed_value' in proof and not _same_value(proof['reviewed_value'], value):
        return []
    try:
        stamp = datetime.fromisoformat(str(proof.get('verified_at') or proof.get('checked_at') or '').replace('Z', '+00:00'))
        if stamp.replace(tzinfo=stamp.tzinfo or timezone.utc) > datetime.now(timezone.utc):
            return []
    except ValueError:
        return []
    if not _url(proof.get('source_url')) or not is_competent(proof['source_url'], route, field):
        return []
    from .policy_intervals import _date, _today
    selected = _date(route.get('arrival_date')) or _today()
    start, end = _date(proof.get('effective_from')), _date(proof.get('effective_to'))
    if ((proof.get('effective_from') is not None and start is None)
            or (proof.get('effective_to') is not None and end is None)
            or (start and end and end < start)
            or (start and selected < start) or (end and selected > end)):
        return []
    subject = proof.get('subject')
    expected = dict(route, travel_document_type=route.get('travel_document_type') or 'ordinary_passport')
    if product is not None:
        expected.update(product_type=product.get('type'), disposition=product.get('disposition'),
                        requirement_detail=product.get('requirement_detail'),
                        entry=product.get('entry'), application_channel=product.get('application_channel'))
    if subject is not None and (not isinstance(subject, dict) or any(expected.get(k) != v for k, v in subject.items())):
        return []
    quotes = _quotes(proof, 'official_page_check' if checked else 'source_review', field)
    if not quotes:
        return []
    # Revalidate against the current saved value: a later edit cannot inherit
    # an old price or a different product's evidence. Never treat note as quote.
    explicit = dict(proof, note='', quote=quotes[0]['quote'], quotes=[q['quote'] for q in quotes[1:]])
    # This viewer presents a field review already bound to the exact saved
    # value. Regrading its paraphrase here would hide real recorded evidence
    # (for example a USD value with an official "US$" tariff quotation).
    # Older proofs without that binding still need the conservative matcher.
    bound_review = ('reviewed_value' in proof and proof.get('status') in ('reviewed', 'verified'))
    if not bound_review and not _value_supported(field, value, explicit):
        return []
    return quotes


def for_record(row: dict, route: dict, guidance: dict, provenance: dict | None,
               check: dict | None, *, active_override: dict | None = None) -> dict:
    """All contract fields are present; unrecorded provenance remains empty."""
    out = {field: [] for field in tstation.FIELD_ORDER}
    prov = provenance if isinstance(provenance, dict) else {}
    check = check if isinstance(check, dict) else {}
    products = [p for p in guidance.get('visa_products', []) if isinstance(p, dict) and p.get('type')] \
        if isinstance(guidance.get('visa_products'), list) else []
    index = row.get('_product_index')
    product = products[index] if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(products) else None
    # The endpoint already chooses the current row index. If a malformed or
    # stale supplied row does not name that product, do not fall back to route
    # quotes and accidentally attribute them to another sibling.
    if index is not None and (product is None or tstation._clean_text(str(product.get('type'))) != row.get('visa_type_name')):
        return out
    own = product.get('field_provenance', {}) if product else {}
    own = own if isinstance(own, dict) else {}
    parents = prov.get('field_provenance') or {}
    parents = parents if isinstance(parents, dict) else {}
    # The official override loader intentionally narrows provenance, dropping
    # reviewed_value. Recover ownership only in this viewer, from the exact
    # applicable loaded field and the same projected proof. A later override,
    # scheduled rule or raw-field edit cannot borrow an earlier review.
    active = active_override if isinstance(active_override, dict) else {}
    active_fields = active.get('fields') if isinstance(active.get('fields'), dict) else {}
    active_proofs = active.get('field_provenance') if isinstance(active.get('field_provenance'), dict) else {}
    parents = dict(parents)
    for field, proof in parents.items():
        if (isinstance(proof, dict) and 'reviewed_value' not in proof
                and proof.get('status') in ('reviewed', 'verified')
                and field in active_fields and active_proofs.get(field) == proof
                and _same_value(active_fields[field], guidance.get(field))):
            parents[field] = dict(proof, reviewed_value=active_fields[field])
    checked = set(check.get('verified_fields') or []) - set(check.get('disputed_fields') or [])
    sources = check.get('field_sources') or {}
    sources = sources if isinstance(sources, dict) else {}

    def checked_after_empty_parent(proof, field):
        # A source link or reviewer narrative is not an asserting quotation.
        # A later independently checked field may supply its own exact quote;
        # no product-scoped, partial, disputed or different-value proof is
        # replaced through this path.
        if not isinstance(proof, dict) or proof.get('status') not in (None, 'reviewed', 'verified'):
            return []
        if _quotes(proof, 'source_review', field) or any(k in proof for k in (
                'reviewed_value', 'verified_elements', 'retained_unverified_elements',
                'effective_from', 'effective_to')):
            return []
        subject = proof.get('subject')
        if subject is not None and (not isinstance(subject, dict)
                or any(route.get(k) != v for k, v in subject.items())):
            return []
        source = sources.get(field)
        if field not in checked or check.get('outcome') != 'checked' or not isinstance(source, dict):
            return []
        if field in (row.get('_disputed') or []) or field in (row.get('_disputed_fields') or []) or row.get('_contradictions'):
            return []
        def when(value):
            dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
            return dt.replace(tzinfo=dt.tzinfo or timezone.utc)
        try:
            current = when(check.get('at'))
            if current > datetime.now(timezone.utc) or when(source.get('checked_at')) != current:
                return []
            older = proof.get('verified_at') or proof.get('checked_at')
            if older and when(older) > current:
                return []
        except (TypeError, ValueError):
            return []
        return _owned(source, route, None, field, guidance.get(field), checked=True)

    def parent(field):
        value = guidance.get(field)
        if value in _EMPTY:
            return []
        if field in parents:
            return (_owned(parents[field], route, None, field, value)
                    or checked_after_empty_parent(parents[field], field))
        if field in (prov.get('fields') or []):
            quotes = _owned(prov, route, None, field, value)
            if quotes:
                return quotes
        if field in checked:
            return _owned(sources.get(field), route, None, field, value, checked=True)
        return []

    def table_quotes():
        proof = parents.get('visa_products')
        if not isinstance(proof, dict) or 'reviewed_value' not in proof:
            return []
        return _owned(proof, route, None, 'visa_products', guidance.get('visa_products'))

    for cell, (own_fields, parent_fields) in FIELDS.items():
        wording = ('processing_text' if cell in ('processing_min_days', 'processing_unit') else
                   'max_stay_text' if cell in ('max_stay_duration', 'max_stay_unit') else
                   'validity_text' if cell in ('validity_duration', 'validity_unit') else None)
        if row.get(cell) in _EMPTY and (wording is None or row.get(wording) in _EMPTY):
            continue
        # In the QC schema an exemption's validity is its permitted stay;
        # visa validity must never inherit this conversion for required visas.
        if cell in ('validity_duration', 'validity_unit') and row.get('visa_requirement') == 'Visa-free':
            own_fields = ('permitted_stay', 'max_stay_days')
            parent_fields = ('permitted_stay', 'permitted_stay_days')
        if product is None:
            candidates = [quote for field in parent_fields for quote in parent(field)]
        else:
            candidates = []
            present = [field for field in own_fields if product.get(field) not in _EMPTY]
            if present:
                for field in present:
                    if field in own:
                        candidates.extend(_owned(own[field], route, product, field, product[field]))
                    else:
                        matched = []
                        for route_field in parent_fields:
                            if _same_value(product[field], guidance.get(route_field)):
                                matched.extend(parent(route_field))
                        if not matched and product.get('source_quote'):
                            legacy = {k: product[k] for k in ('source_url', 'source_quote', 'verified_at', 'verifier') if k in product}
                            matched = _owned(legacy, route, product, field, product[field])
                        candidates.extend(matched or table_quotes())
            elif not row.get('_separate_permission') and not any(field in own for field in own_fields):
                candidates = [quote for field in parent_fields for quote in parent(field)]
        for quote in candidates:
            if quote not in out[cell]:
                out[cell].append(quote)

    # Documented absence can have evidence; a blank or generic source URL
    # never manufactures a quote for "Not publicly available".
    absent_proofs = guidance.get('unpublished_evidence')
    absence = dict(absent_proofs) if isinstance(absent_proofs, dict) else {}
    if product and isinstance(product.get('unpublished_evidence'), dict):
        absence.update(product['unpublished_evidence'])
    for field in tstation.proven_absences(absence, route):
        if field in out and row.get(field) in _EMPTY:
            out[field] = _quotes(absence[field], 'documented_absence')
    return out
