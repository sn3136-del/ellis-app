"""Per-product publication by grade (owner decision, 11 September 2026).

A route whose verdict-bearing row is not Low is published; only the sibling
product rows that are Low are withheld, each on its own. A route whose
verdict-bearing row is Low keeps the whole-route hold. A Low-only route
serves exactly what it served before.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from app.visa_snapshot import kimi_primary as kp, tstation
from app.visa_snapshot.records_guard import apply_records_hold
from tests.test_override_grading_integrity import FREE, PROV, ROUTE
from tests.test_required_default_publication import CASES, envelope, india

NEEDLE = 'UNVERIFIED_LONGSTAY_CLAIM_777'
SIBLING = 'Long-stay tourist visa'
TWIN = 'Unified electronic visa (business meetings)'


def _proof(quote):
    return {'status': 'reviewed', 'verifier': 'human', 'verified_by': 'Operator',
            'source_url': PROV['source_url'], 'verified_at': PROV['verified_at'],
            'note': quote, 'quote': quote}


def high_exemption_case():
    """A quoted, complete visa-free default (High) beside an unevidenced
    paper visa option (Low). The narrow exemption projection does not take
    this shape (the option carries no source), so the grade rule decides."""
    fee = {'amount': 0, 'currency': 'JPY'}
    proofs = {'disposition': _proof('Passport holders are exempt from a visa.'),
              'permitted_stay': _proof('Passport holders may stay up to 30 days.'),
              'permitted_stay_days': _proof('Passport holders may stay up to 30 days.'),
              'visa_category': _proof('Visa-free entry for short stays.'),
              'government_fee': _proof('Visa-free entry costs nothing: the fee is 0 JPY.'),
              'required_documents': _proof('Required document: Valid passport.')}
    g = dict(deepcopy(FREE), requirement_detail='unconditional_visa_free',
             visa_category='Visa-free entry', government_fee=deepcopy(fee),
             visa_products=[
                 {'type': 'Visa-free entry', 'requirement_detail': 'unconditional_visa_free',
                  'fee': deepcopy(fee),
                  'field_provenance': {'fee': deepcopy(proofs['government_fee']),
                                       'type': deepcopy(proofs['visa_category'])}},
                 {'type': SIBLING, 'requirement_detail': 'paper_visa', 'entry': 'single',
                  'validity': '90 days', 'max_stay_days': 90,
                  'fee': {'amount': 3000, 'currency': 'JPY'}, 'notes': NEEDLE}])
    prov = dict(deepcopy(PROV), field_provenance=proofs,
                fields=PROV['fields'] + ['requirement_detail', 'visa_category',
                                         'government_fee', 'permitted_stay'])
    route = dict(ROUTE, travel_document_type='ordinary_passport')
    raw = {'guidance': g, 'source_verified': prov, 'grounded_check': {}, 'stale': False,
           'status': kp.STATUS_PRIMARY, 'cached': True}
    return route, raw


def _rename_subjects(obj, old, new):
    if isinstance(obj, dict):
        return {k: (new if k == 'product_type' and v == old else _rename_subjects(v, old, new))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_rename_subjects(v, old, new) for v in obj]
    return obj


def two_default_evisa_case():
    """The IND to RUS reviewed e-visa default with a second evidenced e-visa
    product: two published defaults is outside the narrow e-visa projection,
    so the grade rule decides. Both defaults are checked with gaps (Medium,
    not Low); the sticker visas carry no evidence (Low)."""
    case = india()
    raw = envelope(case)
    first = raw['guidance']['visa_products'][0]
    twin = _rename_subjects(deepcopy(first), first['type'], TWIN)
    twin['type'] = TWIN
    raw['guidance']['visa_products'].insert(1, twin)
    return case['route'], raw


def grades(route, raw):
    return [(r['visa_type_name'], r['confidence_level'], bool(r['_evidence_low']))
            for r in tstation.records_for_route(route, raw['guidance'], raw['source_verified'])]


def test_high_verdict_row_publishes_and_withholds_only_the_low_sibling(monkeypatch):
    monkeypatch.setenv('ELLIS_HOLD_UNCERTAIN', '1')
    route, raw = high_exemption_case()
    before = deepcopy(raw)
    assert grades(route, raw) == [('Visa-free entry', 'High', False), (SIBLING, 'Low', True)]
    out = apply_records_hold(route, raw)
    assert out['held'] is False and out['review_required'] is False
    assert out['publication_state'] == 'partial' and out['withheld_product_count'] == 1
    assert out['product_publication'] == [
        {'product_index': 0, 'held': False, 'state': 'published',
         'reason': 'default_or_product_evidence_supported', 'review_required': False},
        {'product_index': 1, 'held': True, 'state': 'withheld',
         'reason': 'product_evidence_low', 'review_required': True}]
    assert [p['type'] for p in out['guidance']['visa_products']] == ['Visa-free entry']
    assert out['guidance']['disposition'] == 'VISA_EXEMPT'
    assert out['guidance']['permitted_stay_days'] == 30
    served = json.dumps(out, ensure_ascii=False)
    assert NEEDLE not in served and SIBLING not in served and '3000' not in served
    assert raw == before  # a reader projection only: no cache or QC mutation


@pytest.mark.parametrize('reason', ['no_source', 'unknown_verdict', 'public_edit', 'disputed'])
def test_low_verdict_row_keeps_the_whole_route_hold(reason, monkeypatch):
    monkeypatch.setenv('ELLIS_HOLD_UNCERTAIN', '1')
    route, raw = high_exemption_case()
    if reason == 'no_source':
        raw['source_verified'] = None
    elif reason == 'unknown_verdict':
        raw['source_verified']['field_provenance']['disposition']['status'] = 'unknown'
    elif reason == 'public_edit':
        raw['source_verified']['verifier'] = 'public'
    else:
        raw['grounded_check'] = {'disputed_fields': ['permitted_stay_days']}
    if reason in ('no_source', 'public_edit'):
        # A cited page nobody read displays Medium; a public edit is Low. Both hold.
        assert grades(route, raw)[0][1] == ('Medium' if reason == 'no_source' else 'Low')
    elif reason == 'disputed':
        disputed = tstation.records_for_route(route, raw['guidance'], raw['source_verified'],
                                              disputed_fields=['permitted_stay_days'])
        assert disputed[0]['confidence_level'] == 'Low'
    out = apply_records_hold(route, raw)
    assert out['held'] is True and out['review_required'] is True
    assert out.get('publication_state') != 'partial'
    assert 'product_publication' not in out


def test_low_only_route_serves_exactly_what_it_served_before(monkeypatch):
    monkeypatch.setenv('ELLIS_HOLD_UNCERTAIN', '1')
    route, raw = high_exemption_case()
    raw['source_verified'] = None
    raw['guidance']['visa_products'] = []
    assert grades(route, raw) == [('No visa needed', 'Medium', True)]
    out = apply_records_hold(route, raw)
    assert out['held'] is True and out.get('publication_state') != 'partial'
    monkeypatch.setenv('ELLIS_DATABASE_HOLD_LOW_CONFIDENCE', '0')
    served = apply_records_hold(route, raw)
    assert served['held'] is False and served['review_required'] is True
    assert served['guidance']['visa_products'] == [] and served.get('publication_state') != 'partial'


def test_two_evidenced_defaults_publish_and_withhold_each_low_sibling(monkeypatch):
    monkeypatch.setenv('ELLIS_HOLD_UNCERTAIN', '1')
    route, raw = two_default_evisa_case()
    before = deepcopy(raw)
    graded = grades(route, raw)
    assert [g[1] for g in graded] == ['Medium', 'Medium', 'Medium', 'Medium']  # the unread siblings display Medium and stay withheld
    out = apply_records_hold(route, raw)
    assert out['held'] is False and out['publication_state'] == 'partial'
    assert out['withheld_product_count'] == 2
    assert [(p['product_index'], p['state'], p['reason'], p['held'], p['review_required'])
            for p in out['product_publication']] == [
        (0, 'published', 'default_or_product_evidence_supported', False, False),
        (1, 'published', 'default_or_product_evidence_supported', False, False),
        (2, 'withheld', 'product_evidence_low', True, True),
        (3, 'withheld', 'product_evidence_low', True, True)]
    assert [p['type'] for p in out['guidance']['visa_products']] == [graded[0][0], TWIN]
    served = json.dumps(out, ensure_ascii=False)
    for name, _grade, _low in graded[2:]:
        assert name not in served
    assert raw == before


def test_lookup_returns_only_the_published_products(client, monkeypatch):
    from app import main
    route, raw = two_default_evisa_case()
    monkeypatch.setenv('ELLIS_HOLD_UNCERTAIN', '1')
    monkeypatch.setattr(kp, 'is_available', lambda: True)
    monkeypatch.setattr(kp, 'get_route_guidance', lambda *a, **kw: deepcopy(raw))
    monkeypatch.setattr(main, '_ground_on_access', lambda *a, **kw: None)
    headers = {'authorization': 'Bearer dev-token', 'x-org-id': 'scope', 'x-user-id': 'reader'}
    lookup = client.post('/database/lookup', headers=headers,
                         json={'nationality': 'IND', 'destination': 'RUS'})
    assert lookup.status_code == 200, lookup.text
    result = lookup.json()
    assert result['held'] is False and result['publication_state'] == 'partial'
    assert result['withheld_product_count'] == 2
    assert [p['type'] for p in result['guidance']['visa_products']] == [
        raw['guidance']['visa_products'][0]['type'], TWIN]
    assert [p['state'] for p in result['product_publication']] == [
        'published', 'published', 'withheld', 'withheld']
    served = json.dumps(result, ensure_ascii=False)
    for withheld in raw['guidance']['visa_products'][2:]:
        assert withheld['type'] not in served


def test_qc_records_expose_each_products_own_publication_state(db, client, monkeypatch):
    from app.visa_snapshot.models import KimiRouteGuidanceCache
    route, raw = two_default_evisa_case()
    db.query(KimiRouteGuidanceCache).delete()
    db.commit()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(KimiRouteGuidanceCache(
        cache_key=india()['cache_key'], route=route, guidance=deepcopy(raw['guidance']),
        status=kp.STATUS_PRIMARY, model='fixture', generated_at=now,
        fresh_until=now + timedelta(days=1), verification={}, missing_fields=[],
        contradictions=[]))
    db.commit()
    monkeypatch.setattr(kp, 'apply_verified_overrides', lambda out, route: dict(
        out, source_verified=deepcopy(raw['source_verified']), held=False, review_required=False))
    monkeypatch.setenv('ELLIS_HOLD_UNCERTAIN', '1')
    response = client.get('/database/records', headers={
        'authorization': 'Bearer admin-token', 'x-org-id': 'scope', 'x-user-id': 'reviewer'})
    assert response.status_code == 200, response.text
    records = response.json()['records']
    assert len(records) == 4
    assert [r['held'] for r in records] == [False, False, True, True]
    assert all(r['route_held'] is False for r in records)
    assert [r['publication_state'] for r in records] == [
        'published', 'published', 'withheld', 'withheld']
    assert [r['publication_reason'] for r in records[2:]] == ['product_evidence_low'] * 2
    assert [r['confidence_level'] for r in records] == ['Medium', 'Medium', 'Medium', 'Medium']
    # QC keeps the withheld products' values; only the reader loses them.
    assert all(r['visa_type_name'] for r in records)
