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
    assert 'validity_duration' not in fields['unpublished_fields']
    assert 'validity_duration' in fields['visa_products'][0]['unpublished_fields']
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


def interval_batch():
    b = batch()
    notice = 'This visa policy is effective from 15 March 2025 until 14 March 2028.'
    b['sources'][0] = source(TEXT + ' ' + notice)
    p = b['rows'][0]['verdict']['proof']
    p['evidence'].append({'source_id': 's1', 'source_url': URL, 'quote': notice})
    p.update(effective_from='2025-03-15', effective_to='2028-03-14')
    return b


def test_explicit_policy_interval_survives_conversion_and_holds_after_expiry():
    from app.visa_snapshot import policy_intervals
    b = interval_batch()
    manifest = build_manifest(b, [layer()])
    overlay, _ = convert(manifest, [layer()])
    entry = overlay['entries'][0]
    p = entry['field_provenance']['disposition']
    assert p['effective_from'] == '2025-03-15' and p['effective_to'] == '2028-03-14'
    end = p['policy_interval_evidence']['effective_to']
    assert end['quote'].endswith('14 March 2028.') and end['subject'] == ROUTE
    assert end['verified_at'] == '2026-09-09'
    prov = {'field_provenance': entry['field_provenance']}
    assert 'policy_interval_conflict' not in policy_intervals.annotate(entry['fields'], prov, {'arrival_date': '2028-03-14'})
    held = policy_intervals.annotate(entry['fields'], prov, {'arrival_date': '2028-03-15'})
    assert held['policy_interval_conflict']['fields'][0]['reason'] == 'policy_expired'
    assert tstation._reviewed_policy_end(prov, ROUTE) == '2028-03-14'


def test_later_review_retains_an_earlier_explicit_policy_notice():
    first, _ = convert(build_manifest(interval_batch(), [layer()]), [layer()])
    baseline = layer(seed_entries=first['entries'])
    later = batch()
    later['rows'][0]['verdict']['proof']['verified_at'] = '2026-09-10'
    converted, _ = convert(build_manifest(later, [baseline]), [baseline])
    p = converted['entries'][0]['field_provenance']['disposition']
    assert p['verified_at'] == '2026-09-10'
    assert p['effective_to'] == '2028-03-14'
    assert p['policy_interval_evidence']['effective_to']['verified_at'] == '2026-09-09'


def test_default_exemption_product_inherits_the_same_scoped_policy_end():
    b = optional_visa_batch()
    notice = 'The exemption policy ends on 14 March 2028.'
    b['sources'][0] = source(b['sources'][0]['text'] + ' ' + notice)
    p = b['rows'][0]['verdict']['proof']
    p['evidence'].append({'source_id':'s1','source_url':URL,'quote':notice})
    p['effective_to'] = '2028-03-14'
    overlay,_ = convert(build_manifest(b,[layer()]),[layer()])
    entry = overlay['entries'][0]
    route_proof = entry['field_provenance']['disposition']
    free = next(p for p in entry['fields']['visa_products'] if p['disposition']=='VISA_EXEMPT')
    bound = free['field_provenance']['disposition']['policy_interval_evidence']['effective_to']
    assert bound['subject']['product_type'] == free['type']
    assert 'product_type' not in route_proof['policy_interval_evidence']['effective_to']['subject']
    assert 'effective_to' not in entry['fields']['visa_products'][0]['field_provenance']['disposition']


def product_interval_batch():
    b = batch()
    notice = 'This visa policy ends on 14 March 2028.'
    b['sources'][0] = source(TEXT + ' ' + notice)
    p = b['rows'][0]['products'][0]['proofs']['disposition']
    p['evidence'].append({'source_id': 's1', 'source_url': URL, 'quote': notice})
    p['effective_to'] = '2028-03-14'
    return b


def test_same_product_recheck_preserves_its_independent_policy_interval():
    first, _ = convert(build_manifest(product_interval_batch(), [layer()]), [layer()])
    baseline = layer(seed_entries=first['entries'], merged_guidance={
        **layer()['merged_guidance'], **first['entries'][0]['fields']})
    later = batch()
    later['rows'][0]['products'][0]['proofs']['disposition']['verified_at'] = '2026-09-10'
    out, _ = convert(build_manifest(later, [baseline]), [baseline])
    p = out['entries'][0]['fields']['visa_products'][0]['field_provenance']['disposition']
    assert p['verified_at'] == '2026-09-10'
    assert p['effective_to'] == '2028-03-14'
    assert p['policy_interval_evidence']['effective_to']['verified_at'] == '2026-09-09'
    assert 'effective_to' not in out['entries'][0]['field_provenance']['disposition']


def test_replacement_product_does_not_inherit_the_old_program_interval():
    first, _ = convert(build_manifest(product_interval_batch(), [layer()]), [layer()])
    baseline = layer(seed_entries=first['entries'], merged_guidance={
        **layer()['merged_guidance'], **first['entries'][0]['fields']})
    later = batch()
    later['rows'][0]['products'][0]['product']['type'] = 'Replacement single-entry tourist e-visa'
    out, _ = convert(build_manifest(later, [baseline]), [baseline])
    p = out['entries'][0]['fields']['visa_products'][0]['field_provenance']['disposition']
    assert 'effective_to' not in p and 'policy_interval_evidence' not in p


def test_changed_route_permission_does_not_inherit_earlier_program_interval():
    first, _ = convert(build_manifest(interval_batch(), [layer()]), [layer()])
    baseline = layer(seed_entries=first['entries'], merged_guidance={
        **layer()['merged_guidance'], **first['entries'][0]['fields']})
    later = optional_visa_batch()
    out, _ = convert(build_manifest(later, [baseline]), [baseline])
    assert out['entries'][0]['fields']['disposition'] == 'VISA_EXEMPT'
    assert 'effective_to' not in out['entries'][0]['field_provenance']['disposition']


@pytest.mark.parametrize('notice', [
    'The consular office will be closed on 14 March 2028.',
    'The visa exemption policy page was last updated on 14 March 2028.',
    'Under this visa policy, the visa is issued on 14 March 2028.',
    'Under the visa exemption policy, passports must be valid until 14 March 2028.',
    'The visa policy office is closed until 14 March 2028.',
])
@pytest.mark.parametrize('explicit_evidence', [False, True])
def test_a_nonpolicy_date_cannot_become_a_policy_end(notice, explicit_evidence):
    b = interval_batch()
    b['sources'][0] = source(TEXT + ' ' + notice)
    p = b['rows'][0]['verdict']['proof']
    p.pop('effective_from')
    p['evidence'][-1]['quote'] = notice
    if explicit_evidence:
        p['policy_interval_evidence'] = {'effective_to': {
            'source_id': 's1', 'source_url': URL, 'quote': notice,
            'verified_at': '2026-09-09', 'status': 'reviewed', 'verifier': 'ai',
            'subject': dict(ROUTE), 'effective_to': '2028-03-14'}}
    with pytest.raises(PatchRejected, match='Policy bound'):
        validate_batch(b)


def projected_entry(entry):
    parsed = vo._parse_rows([entry], {})[vo._key('HKG', 'VNM', 'tourism', 'ordinary_passport')]
    guidance, _ = vo.merge_verified_fields(layer()['raw_guidance'], parsed['fields'],
                                          source_url=entry['source_url'])
    provenance = dict(parsed['field_provenance']['disposition'], fields=list(parsed['fields']),
                      field_provenance=parsed['field_provenance'])
    return tstation.records_for_route(ROUTE, guidance, provenance)


def test_one_products_unpublished_validity_does_not_complete_an_unknown_sibling():
    b = batch()
    first = b['rows'][0]['products'][0]
    first['product']['validity'] = None
    first['proofs']['validity'] = proof(status='not_published', reason='No validity published for this product.')
    second = deepcopy(first)
    second.update(action='add', current_name=None)
    second['product'].update(type='Multiple-entry tourist e-visa', entry='multiple',
                             fee={'amount': 50, 'currency': 'USD'})
    second['proofs']['validity'] = proof(status='unknown', reason='This separate product has not yet been checked.')
    second['proofs']['entry'] = proof('$50/multiple-entry electronic visa')
    b['rows'][0]['products'].append(second)
    out, _ = convert(build_manifest(b, [layer()]), [layer()])
    entry = out['entries'][0]
    assert entry['fields']['unpublished_fields'] == []
    rows = projected_entry(entry)
    assert tstation.field_status(rows[0])['validity_duration'] == 'not-published'
    assert tstation.field_status(rows[1])['validity_duration'] == 'missing'
    assert 'validity_duration' not in rows[1]['_unpublished']
    # These two validity cells are the only completion difference between
    # the products, even though their fee and entry values legitimately differ.
    metrics = [tstation.acceptance_summary([r]) for r in rows]
    assert metrics[0]['documented_completed_cells'] == metrics[1]['documented_completed_cells'] + 2


def test_product_unknown_review_replaces_its_earlier_unpublished_state():
    b = batch()
    first = b['rows'][0]['products'][0]
    first['product']['validity'] = None
    first['proofs']['validity'] = proof(status='not_published', reason='No validity published for this product.')
    initial, _ = convert(build_manifest(b, [layer()]), [layer()])
    baseline = layer(seed_entries=initial['entries'], merged_guidance={
        **layer()['merged_guidance'], **initial['entries'][0]['fields']})
    later = batch()
    later['rows'][0]['products'][0]['product']['validity'] = None
    later['rows'][0]['products'][0]['proofs']['validity'] = proof(
        status='unknown', reason='The current policy validity has not yet been confirmed.')
    result, _ = convert(build_manifest(later, [baseline]), [baseline])
    row = projected_entry(result['entries'][0])[0]
    assert tstation.field_status(row)['validity_duration'] == 'missing'
    assert 'validity_duration' not in row['_unpublished']


def test_product_unknown_overrides_parent_np_without_erasing_unrelated_route_absence():
    guidance = layer()['raw_guidance']
    guidance['unpublished_fields'] = ['validity_duration', 'validity_unit',
                                     'processing_min_days', 'processing_unit']
    guidance['visa_products'][0]['validity'] = None
    guidance['visa_products'][0]['field_provenance'] = {
        'validity': {'status': 'unknown', 'reason': 'This product has not yet been checked.'}}
    row = tstation.records_for_route(ROUTE, guidance)[0]
    states = tstation.field_status(row)
    assert states['validity_duration'] == states['validity_unit'] == 'missing'
    assert states['processing_min_days'] == states['processing_unit'] == 'not-published'


@pytest.mark.parametrize('sibling_review', [None, {'status': 'unknown', 'reason': 'Not checked yet.'}])
def test_registered_legacy_product_np_is_not_a_global_sibling_absence(sibling_review):
    guidance = layer()['raw_guidance']
    first = guidance['visa_products'][0]
    first['validity'] = None
    first['field_provenance'] = {'validity': {'status': 'unknown',
        'reason': 'Not published by the destination: no validity stated for the single-entry product.'}}
    sibling = deepcopy(first)
    sibling['type'] = 'Multiple-entry tourist e-visa'
    sibling['field_provenance'] = {'validity': sibling_review} if sibling_review else {}
    guidance['visa_products'].append(sibling)
    guidance['unpublished_fields'] = ['validity_duration', 'validity_unit']
    rows = tstation.records_for_route(ROUTE, guidance)
    assert tstation.field_status(rows[0])['validity_duration'] == 'not-published'
    assert tstation.field_status(rows[1])['validity_duration'] == 'missing'
    assert 'validity_duration' not in rows[1]['_unpublished']


def test_explicit_unknown_supersedes_a_stale_product_local_np_flag():
    guidance = layer()['raw_guidance']
    p = guidance['visa_products'][0]
    p['validity'] = None
    p['unpublished_fields'] = ['validity_duration', 'validity_unit']
    p['field_provenance'] = {'validity': {'status': 'unknown', 'reason': 'Unresolved new policy.'}}
    row = tstation.records_for_route(ROUTE, guidance)[0]
    assert tstation.field_status(row)['validity_duration'] == 'missing'


@pytest.mark.parametrize('stale_product_text', [None, 'Up to 90 days'])
def test_unknown_product_stay_cannot_borrow_a_route_stay_and_count_as_filled(stale_product_text):
    guidance = layer()['raw_guidance']
    guidance['permitted_stay'] = 'Up to 90 days'
    p = guidance['visa_products'][0]
    p['max_stay_days'] = None
    p['permitted_stay'] = stale_product_text
    p['field_provenance'] = {'max_stay_days': {'status': 'unknown', 'reason': 'Separate product stay is unconfirmed.'}}
    row = tstation.records_for_route(ROUTE, guidance)[0]
    assert row['max_stay_duration'] is None and row['max_stay_unit'] is None
    assert tstation.field_status(row)['max_stay_duration'] == 'missing'


def test_new_route_unknown_review_replaces_its_earlier_unpublished_state():
    b = batch()
    b['rows'][0]['route_fields']['processing_time'] = None
    b['rows'][0]['route_field_proofs']['processing_time'] = proof(
        status='not_published', reason='No processing time published in the reviewed source.')
    first, _ = convert(build_manifest(b, [layer()]), [layer()])
    assert 'processing_min_days' in first['entries'][0]['fields']['unpublished_fields']
    baseline = layer(seed_entries=first['entries'], merged_guidance={
        **layer()['merged_guidance'], **first['entries'][0]['fields']})
    later = batch()
    later['rows'][0]['route_fields']['processing_time'] = None
    later['rows'][0]['route_field_proofs']['processing_time'] = proof(
        status='unknown', reason='The new procedure has not yet been checked.')
    out, _ = convert(build_manifest(later, [baseline]), [baseline])
    assert 'processing_min_days' not in out['entries'][0]['fields']['unpublished_fields']


@pytest.mark.parametrize('mutation', ['wrong_date', 'invalid_date', 'reversed', 'foreign_notice', 'wrong_subject', 'invented_quote'])
def test_unproven_policy_intervals_are_rejected(mutation):
    b = interval_batch()
    validate_batch(b)
    p = b['rows'][0]['verdict']['proof']
    bound = p['policy_interval_evidence']['effective_to']
    if mutation == 'wrong_date':
        p['effective_to'] = '2029-03-14'
    elif mutation == 'invalid_date':
        p['effective_to'] = '2028-02-30'
    elif mutation == 'reversed':
        p['effective_from'], p['effective_to'] = p['effective_to'], p['effective_from']
        p.pop('policy_interval_evidence')
    elif mutation == 'foreign_notice':
        foreign = source('This visa policy ends on 14 March 2028.', 'https://www.immd.gov.hk/eng/news.html', 'foreign')
        b['sources'].append(foreign)
        bound.update(source_id='foreign', source_url=foreign['url'], quote=foreign['text'])
    elif mutation == 'wrong_subject':
        bound['subject']['passport_nationality'] = 'JPN'
    else:
        bound['quote'] = 'The updated policy is extended until 14 March 2028.'
    with pytest.raises(PatchRejected, match='[Pp]olicy'):
        validate_batch(b)


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
