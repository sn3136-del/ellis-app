"""The general review converter admits only quote-bound, destination-owned proof."""
from copy import deepcopy
import hashlib
import json

import pytest

from scripts.convert_reviewed_general_batch import build_manifest, convert, validate_batch
from scripts.prepare_reviewed_product_patch import PatchRejected
from app.visa_snapshot import verified_overrides as vo, tstation

URL = 'https://evisa.xuatnhapcanh.gov.vn/en_US/web/guest/khai-thi-thuc-dien-tu/cap-thi-thuc-dien-tu'
TEXT = ('Vietnam Immigration Department. Holders of a Hong Kong SAR passport must obtain a visa before travel. '
        'Step 2: Pay E-visa fee. Your application will be processed in 3 working days; '
        '$25/single-entry electronic visa - $50/multiple-entry electronic visa. '
        'Vietnam E-visa is valid for maximum of 90 days, single or multiple entry, and the holder may stay up to 90 days. '
        'Nationals of Japan do not require a visa for stays of up to 45 days.')


def source(text=TEXT, url=URL, sid='s1'):
    return {'id': sid, 'url': url, 'checked_at': '2026-09-09', 'reading_method': 'httpx_text',
            'text': text, 'sha256': hashlib.sha256(text.encode()).hexdigest()}


def proof(*quotes, status='reviewed', reason=''):
    return {'status': status, 'verifier': 'ai', 'verified_at': '2026-09-09', 'scope_note': 'HKSAR ordinary passport, tourism',
            'reason': reason, 'evidence': [{'source_id': 's1', 'source_url': URL, 'quote': q} for q in quotes]}


ROUTE = {'passport_nationality': 'HKG', 'destination_country': 'VNM', 'travel_purpose': 'tourism',
         'travel_document_type': 'ordinary_passport'}
KEY = 'HKG|HKG|VNM|tourism|default|unknown|v6'
PRODUCT = {'type': 'Single-entry tourist e-visa', 'entry': 'single', 'validity': '90 days', 'max_stay_days': 90,
           'fee': {'amount': 25, 'currency': 'USD'}, 'notes': None}


def layer(**changes):
    raw = {'disposition': 'VISA_REQUIRED', 'requirement_detail': 'evisa', 'permitted_stay_days': 90,
           'visa_products': [deepcopy(PRODUCT)], 'application_channel': 'online_portal'}
    result = {'cache_key': KEY, 'route': dict(ROUTE), 'raw_guidance': raw, 'merged_guidance': deepcopy(raw),
              'source_provenance': None, 'seed_entries': [], 'operator_entries': []}
    result.update(changes)
    return result


def batch():
    return {'schema_version': 1, 'kind': 'general_reviewed_batch', 'id': 'test-batch', 'reviewed_at': '2026-09-09',
            'sources': [source()],
            'rows': [{'cache_key': KEY, 'route': dict(ROUTE),
                      'verdict': {'disposition': 'VISA_REQUIRED', 'requirement_detail': 'evisa',
                                  'proof': proof('Holders of a Hong Kong SAR passport must obtain a visa before travel.')},
                      'route_fields': {'permitted_stay_days': 90, 'permitted_stay': 'Up to 90 days',
                                       'government_fee': {'amount': 25, 'currency': 'USD'},
                                       'processing_time': '3 working days'},
                      'route_field_proofs': {
                          'permitted_stay_days': proof('the holder may stay up to 90 days'),
                          'government_fee': proof('$25/single-entry electronic visa - $50/multiple-entry electronic visa'),
                          'processing_time': proof('Your application will be processed in 3 working days')},
                      'products': [{'current_name': 'Single-entry tourist e-visa', 'action': 'patch',
                                    'product': dict(PRODUCT, disposition='VISA_REQUIRED', requirement_detail='evisa'),
                                    'proofs': {'disposition': proof('Holders of a Hong Kong SAR passport must obtain a visa before travel.'),
                                               'fee': proof('$25/single-entry electronic visa - $50/multiple-entry electronic visa'),
                                               'max_stay_days': proof('the holder may stay up to 90 days'),
                                               'entry': proof('$25/single-entry electronic visa'),
                                               'validity': proof('Vietnam E-visa is valid for maximum of 90 days')}}]}]}


def test_a_quote_bound_batch_converts_into_a_supported_product_row():
    b = batch(); manifest = build_manifest(b, [layer()])
    overlay, reports = convert(manifest, [layer()])
    assert overlay['kind'] == 'reviewed_overlay_conversion' and len(overlay['entries']) == 1
    entry = overlay['entries'][0]
    assert entry['fields']['disposition'] == 'VISA_REQUIRED'
    assert entry['field_provenance']['disposition']['quote'].startswith('Holders of a Hong Kong SAR passport')
    product = entry['fields']['visa_products'][0]
    assert product['source_url'] == URL and product['verifier'] == 'ai'
    assert product['field_provenance']['disposition']['subject']['product_type'] == PRODUCT['type']
    # The overlay parses through the real store loader and the record it
    # produces counts as quote-supported for the visa requirement.
    parsed = vo._parse_rows([entry], {})[vo._key('HKG', 'VNM', 'tourism', 'ordinary_passport')]
    guidance, prov = vo.merge_verified_fields(layer()['raw_guidance'], parsed['fields'], source_url=entry['source_url'])
    provenance = dict(parsed['field_provenance']['disposition'], fields=list(parsed['fields']),
                      field_provenance=parsed['field_provenance'])
    rows = tstation.records_for_route(ROUTE, guidance, provenance)
    assert rows and all(tstation.verdict_provenance_supported(r.get('_product_source_verified')) for r in rows)
    assert reports[0]['unsupported_products'] == []


@pytest.mark.parametrize('mutate, message', [
    (lambda b: b['rows'][0]['verdict']['proof']['evidence'].__setitem__(0, {'source_id': 's1', 'source_url': URL, 'quote': 'Hong Kong SAR passport holders are visa exempt'}), 'not on its captured page'),
    (lambda b: b['rows'][0]['verdict']['proof']['evidence'].__setitem__(0, {'source_id': 's1', 'source_url': URL, 'quote': 'Nationals of Japan do not require a visa for stays of up to 45 days.'}), 'does not state this verdict'),
    (lambda b: b['sources'][0].__setitem__('checked_at', '2999-01-01'), 'Future source-read date'),
    (lambda b: b['sources'][0].__setitem__('sha256', 'deadbeef'), 'hash mismatch'),
])
def test_unproven_or_unsound_evidence_is_rejected(mutate, message):
    b = batch(); mutate(b)
    with pytest.raises(PatchRejected, match=message):
        validate_batch(b)


def test_an_unproven_ancillary_value_is_dropped_not_published():
    """The verdict decides the row. A fee whose quote does not carry the
    amount, or a value with no proof at all, is removed with its reason and
    the row still publishes what it can prove."""
    b = batch()
    b['rows'][0]['route_field_proofs']['government_fee']['evidence'][0] = {'source_id': 's1', 'source_url': URL, 'quote': 'Your application will be processed in 3 working days'}
    b['rows'][0]['route_field_proofs'].pop('processing_time')
    _, accepted, rejected = validate_batch(b, strict=False)
    assert rejected == [] and len(accepted) == 1
    row = accepted[0]
    assert 'government_fee' not in row['route_fields'] and 'processing_time' not in row['route_fields']
    assert any(d.startswith('government_fee:') for d in row['dropped'])
    assert any(d.startswith('processing_time:') for d in row['dropped'])


def test_a_foreign_or_unofficial_page_cannot_prove_the_destination_rule():
    b = batch()
    other = 'https://www.immd.gov.hk/eng/service/travel_document/visa_free_access.html'
    b['sources'].append(source(text='Hong Kong SAR passport holders must obtain a visa before travel to Viet Nam.', url=other, sid='s2'))
    b['rows'][0]['verdict']['proof']['evidence'] = [{'source_id': 's2', 'source_url': other,
                                                    'quote': 'Hong Kong SAR passport holders must obtain a visa before travel to Viet Nam.'}]
    with pytest.raises(PatchRejected, match='no destination-government page'):
        validate_batch(b)
    b = batch()
    b['sources'][0] = source(url='https://www.vietnam-visa-agency.com/hk', sid='s1')
    b['rows'][0]['verdict']['proof']['evidence'][0]['source_url'] = 'https://www.vietnam-visa-agency.com/hk'
    with pytest.raises(PatchRejected, match='Unofficial source'):
        validate_batch(b)


def test_a_changed_product_or_layer_blocks_conversion():
    b = batch(); manifest = build_manifest(b, [layer()])
    changed = layer(); changed['merged_guidance']['visa_products'][0]['fee'] = {'amount': 30, 'currency': 'USD'}
    with pytest.raises(PatchRejected, match='Layer changed'):
        convert(manifest, [changed])
    b['rows'][0]['products'][0]['current_name'] = 'A product that does not exist'
    with pytest.raises(PatchRejected, match='Missing or ambiguous current product'):
        build_manifest(b, [layer()])


def test_a_visa_free_verdict_keeps_only_a_free_entry_product():
    text = ('Immigration Department of Viet Nam. Holders of a Hong Kong SAR passport do not require a visa for stays of up to 30 days. '
            'Entry is free of charge for exempt visitors.')
    b = batch(); b['sources'] = [source(text=text)]
    row = b['rows'][0]
    row['verdict'] = {'disposition': 'VISA_EXEMPT', 'requirement_detail': 'unconditional_visa_free',
                      'proof': proof('Holders of a Hong Kong SAR passport do not require a visa for stays of up to 30 days.')}
    row['route_fields'] = {'permitted_stay_days': 30, 'permitted_stay': 'Up to 30 days', 'government_fee': {'amount': 0, 'currency': None}}
    row['route_field_proofs'] = {'permitted_stay_days': proof('do not require a visa for stays of up to 30 days'),
                                 'government_fee': proof('Entry is free of charge for exempt visitors.')}
    row['products'] = [{'current_name': 'Single-entry tourist e-visa', 'action': 'remove', 'remove_reason': 'No visa is issued to exempt visitors'},
                       {'current_name': None, 'action': 'add',
                        'product': {'type': 'Visa-free entry', 'entry': None, 'validity': None, 'max_stay_days': 30,
                                    'fee': {'amount': 0, 'currency': None}, 'notes': 'Entry is free of charge.',
                                    'disposition': 'VISA_EXEMPT', 'requirement_detail': 'unconditional_visa_free'},
                        'proofs': {'disposition': proof('Holders of a Hong Kong SAR passport do not require a visa for stays of up to 30 days.'),
                                   'fee': proof('Entry is free of charge for exempt visitors.'),
                                   'max_stay_days': proof('do not require a visa for stays of up to 30 days'),
                                   'entry': proof(status='unknown', reason='The page does not state an entry count'),
                                   'validity': proof(status='not_published', reason='No validity is published for an exemption')}}]
    manifest = build_manifest(b, [layer()])
    overlay, reports = convert(manifest, [layer()])
    fields = overlay['entries'][0]['fields']
    assert fields['disposition'] == 'VISA_EXEMPT'
    assert [p['type'] for p in fields['visa_products']] == ['Visa-free entry']
    assert 'validity_duration' in fields['unpublished_fields']
    assert reports[0]['removed_products'][0]['type'] == 'Single-entry tourist e-visa'


def optional_visa_batch():
    """Synthetic policy fixture: short visits exempt; longer visits need a visa."""
    b = batch()
    exempt = 'Holders of a Hong Kong SAR passport do not require a visa for stays of up to 30 days.'
    longer = 'Holders of a Hong Kong SAR passport must obtain a visa for stays longer than 30 days.'
    text = TEXT.replace('Holders of a Hong Kong SAR passport must obtain a visa before travel.', longer)
    b['sources'] = [source(text + ' ' + exempt + ' Entry is free of charge for exempt visitors.')]
    row = b['rows'][0]
    row['verdict'] = {'disposition': 'VISA_EXEMPT', 'requirement_detail': 'unconditional_visa_free', 'proof': proof(exempt)}
    row['route_fields'] = {'permitted_stay_days': 30, 'permitted_stay': 'Up to 30 days',
                           'government_fee': {'amount': 0, 'currency': None}}
    row['route_field_proofs'] = {'permitted_stay_days': proof('do not require a visa for stays of up to 30 days'),
                                'government_fee': proof('Entry is free of charge for exempt visitors.')}
    row['products'][0]['proofs']['disposition'] = proof(longer)
    row['products'].append({'action': 'add', 'current_name': None,
        'product': {'type': 'Visa-free entry', 'disposition': 'VISA_EXEMPT',
                    'requirement_detail': 'unconditional_visa_free', 'entry': None,
                    'validity': None, 'max_stay_days': 30, 'fee': {'amount': 0, 'currency': None}},
        'proofs': {'max_stay_days': proof('do not require a visa for stays of up to 30 days'),
                   'fee': proof('Entry is free of charge for exempt visitors.')}})
    return b


def test_a_reviewed_optional_paid_visa_survives_beside_its_reviewed_exemption_lane():
    b = optional_visa_batch()
    overlay, reports = convert(build_manifest(b, [layer()]), [layer()])
    entry = overlay['entries'][0]
    products = entry['fields']['visa_products']
    assert [p['type'] for p in products] == [PRODUCT['type'], 'Visa-free entry']
    assert [p['disposition'] for p in products] == ['VISA_REQUIRED', 'VISA_EXEMPT']
    assert products[0]['fee'] == {'amount': 25, 'currency': 'USD'}
    assert 'longer than 30 days' in products[0]['field_provenance']['disposition']['quote']
    assert reports[0]['removed_products'] == []
    parsed = vo._parse_rows([entry], {})[vo._key('HKG', 'VNM', 'tourism', 'ordinary_passport')]
    guidance, _ = vo.merge_verified_fields(layer()['raw_guidance'], parsed['fields'], source_url=entry['source_url'])
    from app.visa_snapshot.kimi_primary import serve_time_invariants
    assert serve_time_invariants(guidance) == []
    assert len(guidance['visa_products']) == 2


@pytest.mark.parametrize('mutation', ['no_free_lane', 'unreviewed_free_lane', 'no_paid_verdict', 'wrong_paid_quote', 'wrong_fee'])
def test_optional_visa_products_cannot_bypass_independent_evidence(mutation):
    b = optional_visa_batch()
    current = layer()
    row = b['rows'][0]
    if mutation == 'no_free_lane':
        row['products'].pop()
    elif mutation == 'unreviewed_free_lane':
        free = row['products'].pop()['product']
        current['raw_guidance']['visa_products'].append(deepcopy(free))
        current['merged_guidance']['visa_products'].append(deepcopy(free))
    elif mutation == 'no_paid_verdict':
        row['products'][0]['proofs'].pop('disposition')
    elif mutation == 'wrong_paid_quote':
        row['products'][0]['proofs']['disposition'] = proof('Nationals of Japan do not require a visa for stays of up to 45 days.')
    else:
        row['products'][0]['proofs']['fee'] = proof('Your application will be processed in 3 working days')
    with pytest.raises(PatchRejected):
        convert(build_manifest(b, [current]), [current])


def test_a_listed_general_overlay_is_loaded_with_the_reviewed_gates(tmp_path, monkeypatch):
    """General batches register through reviewed_overlays.json beside the seed,
    not through code edits. A listed file still has to pass the reviewed
    overlay schema, and an unlisted or path-like name is ignored."""
    seed = tmp_path / 'verified_overrides.json'
    seed.write_text('[]')
    b = batch(); manifest = build_manifest(b, [layer()])
    overlay, _ = convert(manifest, [layer()])
    (tmp_path / 'reviewed_general_overlay_test.json').write_text(json.dumps(overlay))
    (tmp_path / 'reviewed_overlays.json').write_text(json.dumps(['reviewed_general_overlay_test.json', '../escape.json', 42]))
    monkeypatch.setattr(vo, 'OVERRIDES', seed)
    monkeypatch.setattr(vo, 'operator_overrides_path', lambda: tmp_path / 'operator_overrides.json')
    vo.reload()
    names = [p.name for p in vo._reviewed_overlay_paths()]
    assert 'reviewed_general_overlay_test.json' in names and '../escape.json' not in names
    hit = vo.find(ROUTE)
    assert hit and hit['fields']['disposition'] == 'VISA_REQUIRED'
    assert vo._table().store_errors == ()
    # A malformed listed overlay is a store error, never a silent skip.
    (tmp_path / 'reviewed_general_overlay_test.json').write_text(json.dumps({'kind': 'wrong'}))
    vo.reload()
    assert 'reviewed_overlay' in vo._table().store_errors
    vo.reload()
