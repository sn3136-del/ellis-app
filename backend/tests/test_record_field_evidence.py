"""Detached proof-presentation cases; no live visa claims or source calls."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from app.visa_snapshot import record_evidence as evidence, tstation, kimi_primary as kp
from app.visa_snapshot.models import KimiRouteGuidanceCache

ROUTE = {'passport_nationality': 'CAN', 'destination_country': 'VNM',
         'travel_purpose': 'tourism', 'travel_document_type': 'ordinary_passport'}
URL = 'https://evisa.gov.vn/instructions'
ADMIN = {'authorization': 'Bearer admin-token', 'x-org-id': 'org-b', 'x-user-id': 'operator-1'}


def proof(quote, **extra):
    return {'source_url': URL, 'quote': quote, 'note': 'Review summary, not a quote',
            'verified_at': '2026-08-01', 'verifier': 'ai', 'status': 'reviewed', **extra}


def case():
    g = {'disposition': 'VISA_REQUIRED', 'requirement_detail': 'evisa',
         'government_fee': {'amount': 25, 'currency': 'USD'},
         'processing_time': '3 working days', 'visa_category': 'Tourist eVisa',
         'required_documents': ['Passport', 'Photo'], 'source_url': URL}
    p = {'fields': ['government_fee', 'processing_time', 'required_documents'],
         'field_provenance': {
             'government_fee': proof('Government fee: USD 25.'),
             'processing_time': proof('Processing takes 3 working days.'),
             'required_documents': proof('Required document: Passport.\nRequired document: Photo.')}}
    row = tstation.records_for_route(ROUTE, g)[0]
    return row, g, p


def test_every_field_exposes_only_its_explicit_quote_and_real_link():
    row, g, p = case(); before = deepcopy((row, g, p))
    out = evidence.for_record(row, ROUTE, g, p, {})
    assert set(out) == set(tstation.FIELD_ORDER)
    assert out['visa_fee_amount'][0]['quote'] == 'Government fee: USD 25.'
    assert out['visa_fee_currency'] == out['visa_fee_amount']
    assert out['processing_min_days'][0]['source_url'] == URL
    assert out['required_documents'][0]['quote'].count('\n') == 1
    assert not out['confidence_level'] and not out['source_url']
    assert (row, g, p) == before


@pytest.mark.parametrize('change', ['note_only', 'wrong_price', 'missing_link', 'unsafe_link',
                                   'public', 'unknown', 'foreign_subject', 'future', 'wrong_value'])
def test_unowned_stale_or_non_verbatim_proof_does_not_get_a_field_quote(change):
    row, g, p = case(); q = p['field_provenance']['government_fee']
    if change == 'note_only': q.pop('quote')
    elif change == 'wrong_price': q['quote'] = 'Government fee: USD 50.'
    elif change == 'missing_link': q.pop('source_url')
    elif change == 'unsafe_link': q['source_url'] = 'javascript:alert(1)'
    elif change == 'public': q['verifier'] = 'public'
    elif change == 'unknown': q['status'] = 'unknown'
    elif change == 'foreign_subject': q['subject'] = {'passport_nationality': 'USA'}
    elif change == 'future': q['verified_at'] = '2099-01-01'
    elif change == 'wrong_value': q['reviewed_value'] = {'amount': 50, 'currency': 'USD'}
    assert evidence.for_record(row, ROUTE, g, p, {})['visa_fee_amount'] == []


def test_sibling_products_cannot_borrow_each_others_fee_or_unknown_credit():
    _, g, p = case()
    g['visa_products'] = [
        {'type': 'Single entry', 'fee': {'amount': 25, 'currency': 'USD'},
         'field_provenance': {'fee': proof('Single entry government fee: USD 25.')}},
        {'type': 'Multiple entry', 'fee': {'amount': 50, 'currency': 'USD'},
         'field_provenance': {'fee': proof('Single entry government fee: USD 25.')}}]
    rows = tstation.records_for_route(ROUTE, g)
    assert evidence.for_record(rows[0], ROUTE, g, p, {})['visa_fee_amount']
    assert not evidence.for_record(rows[1], ROUTE, g, p, {})['visa_fee_amount']
    g['visa_products'][0]['field_provenance']['fee']['status'] = 'unknown'
    assert not evidence.for_record(rows[0], ROUTE, g, p, {})['visa_fee_amount']


def test_current_grounded_fields_keep_quote_with_supporting_page_identity():
    row, g, _ = case()
    check = {'verified_fields': ['government_fee'], 'field_sources': {
        'government_fee': {'source_url': URL, 'checked_at': '2026-08-01',
                           'quote': 'Government fee: USD 25.', 'supporting_evidence': [
                               {'quote': 'The fee applies to single entry.', 'source_url': URL + '/scope'}]}}}
    out = evidence.for_record(row, ROUTE, g, {}, check)
    assert len(out['visa_fee_amount']) == 2
    assert out['visa_fee_amount'][1]['source_url'].endswith('/scope')
    check['disputed_fields'] = ['government_fee']
    assert evidence.for_record(row, ROUTE, g, {}, check)['visa_fee_amount'] == []


def test_revision_binds_product_display_values_and_qualified_wording():
    row, _, _ = case(); r = evidence.revision(row)
    for field, value in [('visa_fee_amount', 50), ('_product_index', 1),
                         ('max_stay_text', '30 days in any 90 days'), ('processing_text', 'within 24 hours')]:
        assert evidence.revision(dict(row, **{field: value})) != r
    assert evidence.revision(deepcopy(row)) == r


def test_lazy_endpoint_rejects_old_revision_bad_product_and_reader(client, db, monkeypatch):
    import app.main as main
    from app.visa_snapshot import verified_overrides as vo
    from app.visa_snapshot import freshness
    route = dict(ROUTE, passport_nationality='NZL')
    _, g, _ = case(); key = kp.cache_key(route)
    prior = db.query(KimiRouteGuidanceCache).filter_by(cache_key=key).first()
    if prior: db.delete(prior); db.commit()
    row = KimiRouteGuidanceCache(cache_key=key, route=route, guidance=g, verification={},
                               status=kp.STATUS_PRIMARY, generated_at=datetime.now(timezone.utc))
    db.add(row); db.commit()
    monkeypatch.setattr(vo, 'find', lambda route: None)
    monkeypatch.setattr(freshness, 'active_disputed_fields', lambda *args: [])
    def forbidden(*args, **kwargs): raise AssertionError('Quote lookup must not invoke a model')
    monkeypatch.setattr(kp, '_call_provider', forbidden, raising=False)
    try:
        records = client.get('/database/records', params={'nationality': 'NZL', 'destination': 'VNM'}, headers=ADMIN)
        rec = records.json()['records'][0]
        assert 'field_evidence' not in rec and rec['product_index'] is None
        params = {'cache_key': key, 'revision': rec['evidence_revision']}
        response = client.get('/database/record-evidence', params=params, headers=ADMIN)
        assert response.status_code == 200, response.text
        assert 'no-store' in response.headers['cache-control']
        assert response.json()['revision'] == rec['evidence_revision']
        assert set(response.json()['field_evidence']) == set(tstation.FIELD_ORDER)
        assert client.get('/database/record-evidence', params=dict(params, revision='old'), headers=ADMIN).status_code == 409
        assert client.get('/database/record-evidence', params=dict(params, product_index=999), headers=ADMIN).status_code == 404
        assert client.get('/database/record-evidence', params=dict(params, product_index=-1), headers=ADMIN).status_code == 422
        assert client.get('/database/record-evidence', params=params,
                          headers=dict(ADMIN, authorization='Bearer dev-token')).status_code == 403
        row.guidance = dict(g, government_fee={'amount': 50, 'currency': 'USD'}); db.commit()
        assert client.get('/database/record-evidence', params=params, headers=ADMIN).status_code == 409
    finally:
        db.delete(row); db.commit()
