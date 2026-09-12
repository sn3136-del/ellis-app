"""The general review converter admits only quote-bound, destination-owned proof."""
from copy import deepcopy
import hashlib
import json
import re

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
    # The notice sits on its own captured page: on the shared page the paid
    # product's proof would have to record the same sunset (round five).
    b['sources'].append(source(notice, NOTICE_URL, 's2'))
    p = b['rows'][0]['verdict']['proof']
    p['evidence'].append({'source_id':'s2','source_url':NOTICE_URL,'quote':notice})
    p['effective_to'] = '2028-03-14'
    overlay,_ = convert(build_manifest(b,[layer()]),[layer()])
    entry = overlay['entries'][0]
    route_proof = entry['field_provenance']['disposition']
    free = next(p for p in entry['fields']['visa_products'] if p['disposition']=='VISA_EXEMPT')
    bound = free['field_provenance']['disposition']['policy_interval_evidence']['effective_to']
    assert bound['subject']['product_type'] == free['type']
    assert 'product_type' not in route_proof['policy_interval_evidence']['effective_to']['subject']
    assert 'effective_to' not in entry['fields']['visa_products'][0]['field_provenance']['disposition']


NOTICE_URL = URL + '?notice'


def product_interval_batch():
    """The product's own policy notice sits on its own captured page: a
    sunset on the route verdict's page would have to be recorded on the
    route verdict too (round five), and this fixture is about the product."""
    b = batch()
    notice = 'This visa policy ends on 14 March 2028.'
    b['sources'].append(source(notice, NOTICE_URL, 's2'))
    p = b['rows'][0]['products'][0]['proofs']['disposition']
    p['evidence'].append({'source_id': 's2', 'source_url': NOTICE_URL, 'quote': notice})
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


@pytest.mark.parametrize('sentence', [
    'Russian passport holders require a visa to enter Singapore for business or for social visit purposes.',
    'Los ciudadanos de India, Nepal, Sri Lanka, Maldivas y Bután necesitan un visado Schengen para entrar en el espacio Schengen.',
    'Nationals of India need a valid visa to enter the United Kingdom as a Standard Visitor.',
    'Les ressortissants indiens ont besoin d\'un visa pour entrer en France.',
])
def test_verdict_rules_accept_plain_requirement_wording(sentence):
    from scripts.convert_reviewed_general_batch import _decision_supported
    nat = 'RUS' if 'Russian' in sentence else 'IND'
    assert _decision_supported('VISA_REQUIRED', [sentence], nat)


@pytest.mark.parametrize('sentence,nat', [
    ('Nationals of Australia, Brazil, Canada and the United States are exempt from short-term stay visa.', 'PHL'),
    ('You will need a visa to enter Singapore if you are holding a travel document issued by this country.', 'RUS'),
    ('Russian nationals do not need a visa for stays of up to 30 days.', 'RUS'),
])
def test_verdict_rules_still_refuse_unnamed_or_contrary_sentences(sentence, nat):
    from scripts.convert_reviewed_general_batch import _decision_supported
    assert not _decision_supported('VISA_REQUIRED', [sentence], nat)


# Every quote below is a literal passage from a captured official page of the
# 2026-09-11 station batch; each case is one converter rule.
@pytest.mark.parametrize('value,quotes,nat', [
    # Demonym inflection: "canadienses" is Canada; Spanish "no necesitan un visado".
    ('VISA_EXEMPT', ['Los ciudadanos canadienses no necesitan un visado Schengen para estancias en el área Schengen de hasta 90 días (en cualquier período de 180 días).'], 'CAN'),
    # Contraction and "one of the following" beside the nationality's own line.
    ('VISA_EXEMPT', ['a US national or permanent resident', 'You don’t need a visa or an eTA to travel to Canada if you are one of the following:'], 'USA'),
    # "do not need to apply for a visa".
    ('VISA_EXEMPT', ['Japanese Nationals do not need to apply for a visa to travel to Singapore for leisure/business.'], 'JPN'),
    # A long comma-separated list line beside a "following countries" sentence.
    ('VISA_EXEMPT', ['Nationals of the following countries are eligible for the visa-exemption program, with a duration of stay of up to 90 days: Albania, Andorra, Australia, Austria, Belgium, Bulgaria, Canada, Chile, Croatia, Cyprus, Czech Republic, Denmark, Estonia',
                     'Slovakia, Slovenia, Spain, Sweden, Switzerland, Tuvalu*, United Kingdom*, and United States of America*.'], 'ESP'),
    # "List of countries whose citizens ... exempt from the requirement to obtain a visa".
    ('VISA_EXEMPT', ['United States of America', 'List of countries whose citizens with all types of passports are unilaterally exempt from the requirement to obtain a visa to enter the Republic of Armenia. They can stay in the territory of the Republic of Armenia up to 180 days per year.'], 'USA'),
    # "these countries" and "do not need to apply for visas".
    ('VISA_EXEMPT', ['Singapore', 'Citizens of these countries, who hold the appropriate passports, do not need to apply for visas in advance when traveling to China for short terms.'], 'SGP'),
    # "visa requirements are waived" with an inline comma list naming Hong Kong.
    ('VISA_EXEMPT', ['Guyana, Honduras, Hong Kong (HKSAR or HK COF I), Hungary, Iceland', 'In accordance with official international visa regulation agreements, Bahamian visa requirements are waived for citizens of the following countries who wish to visit and remain in The Bahamas for a period not exceeding three (3) months or eight (8) months as each individual agreement may dictate:'], 'HKG'),
    # A country label heading its own entry.
    ('VISA_EXEMPT', ['Canada: Official passport holders are required to have visa to enter Türkiye. Ordinary passport holders are exempted from visa up to 90 days in any 180-day period.'], 'CAN'),
    ('VISA_EXEMPT', ['Hong Kong SAR passport . Do not require visa, may stay up to 90 days.'], 'HKG'),
    # The previous sentence names the nationality; the rule sentence brings no subject of its own.
    ('VISA_ON_ARRIVAL', ['U.S. citizens are not required to apply for a visa before traveling to the UAE. A visa will be issued upon entry, allowing a maximum stay of 90 days non-renewable, whether continuously or intermittently, within 180 days calculated from the date of first entry.'], 'USA'),
    # Slovak, Bosnian, Dutch, Portuguese and French wording.
    ('VISA_EXEMPT', ['Spojené štáty americké', 'Krajiny, ktorých štátni príslušníci nepodliehajú vízovej povinnosti pri vstupe na územie SR:'], 'USA'),
    ('VISA_EXEMPT', ['Državljani Sjedinjenih Američkih Država izuzeti su od viznog režima prilikom ulaska, izlaska ili prelaska preko teritorije Bosne i Hercegovine do 90 dana, u periodu od šest mjeseci, počevši od dana prvog ulaska.'], 'USA'),
    ('VISA_EXEMPT', ['Reizigers vanuit de Verenigde Staten, Nederland, Belgiё, Frankrijk en Canada zullen binnenkort visumvrij naar Suriname kunnen afreizen.'], 'USA'),
    ('VISA_EXEMPT', ['EUA (Estados Unidos da América)', 'Cidadãos dos seguintes países e territórios podem entrar e permanecer em São Tomé e Príncipe por um período máximo de 15 dias sem visto, com possibilidade de prorrogação até 90 dias, desde que cumpram os requisitos de imigração:'], 'USA'),
    ('VISA_REQUIRED', ['ETATS-UNIS D\'AMERIQUE', 'Sous réserve des accords bilatéraux et ou multilatéraux, l’entrée des ressortissants des pays ci-après listés est soumise à l\'obtention d\'un visa.'], 'USA'),
    # Vietnamese name of the Republic of Korea and Chinese "泰方".
    ('VISA_EXEMPT', ['Về việc miễn thị thực cho công dân các nước: Cộng hòa Liên bang Đức, Cộng hòa Pháp, Cộng hòa I-ta-li-a, Vương quốc Tây Ban Nha, Liên hiệp Vương quốc Anh và Bắc Ai-len, Liên bang Nga, Nhật Bản, Đại hàn Dân Quốc'], 'KOR'),
    ('VISA_EXEMPT', ['届时，中方持公务普通护照、普通护照人员和泰方持普通护照人员，可免签入境对方国家单次停留不超过30日（每180日累计停留不超过90日）。'], 'THA'),
    # UK entry clearance is a visa; the nationality is its own list line.
    ('VISA_REQUIRED', ['Philippines', 'List of nationalities requiring entry clearance prior to travel to the UK as a Visitor, or for any other purpose for less than six months'], 'PHL'),
    # Korea's K-ETA stated as a requirement, in Thai and in English.
    ('ELECTRONIC_AUTHORIZATION_REQUIRED', ['ทว่า ในกรณีบุคคลสัญชาติไทยเดินทางเข้าประเทศเกาหลีจำเป็นต้องมีการขออนุมัติเดินทางเข้าประเทศผ่านระบบ K-ETA ก่อนการเดินทางเข้าประเทศ'], 'THA'),
    ('ELECTRONIC_AUTHORIZATION_REQUIRED', ['Singapore', 'An Electronic Travel Authorisation (ETA) is required by specified nationals in advance of travel to the UK.',
                                          'List of nationalities requiring an Electronic Travel Authorisation (ETA) prior to travel to the UK pursuant to Appendix Electronic Travel Authorisation.'], 'SGP'),
])
def test_verdict_rules_read_official_wording_from_the_station_batch(value, quotes, nat):
    from scripts.convert_reviewed_general_batch import _decision_supported
    assert _decision_supported(value, quotes, nat)


@pytest.mark.parametrize('value,quotes,nat', [
    # A universal statement never names the nationality.
    ('VISA_REQUIRED', ['All foreign visitors to Papua New Guinea need a visa.'], 'USA'),
    # VWP boilerplate about designated countries is not a verdict for Taiwan.
    ('ELECTRONIC_AUTHORIZATION_REQUIRED', ['permits citizens of designated countries to travel to the United States for business or tourism for stays of up to 90 days without a visa.'], 'TWN'),
    # A list line beside a sentence that is not general does not prove an exemption.
    ('VISA_EXEMPT', ['Hong Kong SAR', 'This Consulate does not issue Electronic Visas, e-visa, Visa On Arrival, etc.'], 'HKG'),
    # A label lends its nationality only to a sentence with no subject of its own.
    ('VISA_EXEMPT', ['Information for American citizens. Canadian citizens may enter without a visa.'], 'USA'),
    ('VISA_EXEMPT', ['Canada: Nationals of Japan are exempted from visa up to 90 days.'], 'CAN'),
    # The group rule needs a group the nationality belongs to, without a carve-out.
    ('VISA_EXEMPT', ['Visa is not required for a stay of less than one (1) month for ASEAN nationals except Myanmar.'], 'IND'),
    ('VISA_EXEMPT', ['Visa is not required for a stay of less than one (1) month for ASEAN nationals except Indonesia.'], 'IDN'),
    ('VISA_EXEMPT', ['EU-Bürger benötigen generell kein Visum für die Einreise nach Deutschland.'], 'USA'),
    # A Spanish verb is not the country.
    ('VISA_REQUIRED', ['Para entrar se usa el visado que requiere visa consular.'], 'USA'),
    # Vietnamese "không cần xin Visa" says no visa is needed; an exemption for other passport types is not a requirement.
    ('VISA_REQUIRED', ['Các trường hợp không cần xin Visa vào Hàn Quốc', 'Công dân Việt Nam có Hộ chiếu công vụ, Hộ chiếu ngoại giao và thẻ APEC (đi cùng hộ chiếu phổ thông) nếu đi dưới 90 ngày'], 'VNM'),
    # A long prose entry is not a nationality's own list line, even beside a list heading.
    ('ELECTRONIC_AUTHORIZATION_REQUIRED', ['Those who hold a passport issued by Taiwan that includes in it the number of the identification card issued by the competent authority in Taiwan',
                                          'List of nationalities requiring an Electronic Travel Authorisation (ETA) prior to travel to the UK pursuant to Appendix Electronic Travel Authorisation.'], 'TWN'),
    # A universal statement opens no list: a list line elsewhere cannot make it name the nationality.
    ('VISA_REQUIRED', ['Norway, Poland, Portugal, Qatar, Romania, Russia, and the United States,', 'Effective March 1, 2025, all travelers traveling to the Kurdistan Region of Iraq, will require a visa.'], 'USA'),
    # A document-checklist bullet about the opposite of the nationality and a footer are not list lines.
    ('VISA_REQUIRED', ['A Visa is required for Non-Eritreans and Eritreans without a National ID.', 'Proof of residency or Permanent Resident Card – for non-US citizens.',
                       'Eritrea’s diplomatic mission to the United States of America'], 'USA'),
    # A carried nationality cannot license a verdict the carrying sentence flips (optional longer-stay visa).
    ('VISA_REQUIRED', ['Canadian citizens do not need a visa for stays of up to 90 days. Those wishing to stay longer may apply for a visa at the embassy.'], 'CAN'),
    ('VISA_REQUIRED', ['Indian nationals may apply for a visa at the nearest embassy.'], 'IND'),
    # "on entry" without a visa noun in the clause is a stamp, not a visa on arrival.
    ('VISA_ON_ARRIVAL', ['US citizens do not need a visa to enter. An entry stamp is granted on entry for 90 days.'], 'USA'),
    ('VISA_ON_ARRIVAL', ['Indian nationals do not need a visa on arrival for stays under 30 days.'], 'IND'),
    # Regional compounds over the USA demonym stem are not US nationals.
    ('VISA_REQUIRED', ['Les ressortissants sud-américains doivent obtenir un visa avant leur arrivée.'], 'USA'),
    ('VISA_REQUIRED', ['Latin American nationals must obtain a visa before travel.'], 'USA'),
    # The fixed aliases ("American citizens", "Americans") inside a regional compound are not US nationals either.
    ('VISA_EXEMPT', ['Latin American citizens do not need a visa.'], 'USA'),
    ('VISA_EXEMPT', ['South American citizens are exempt from visa requirements.'], 'USA'),
    ('VISA_EXEMPT', ['Central Americans do not need a visa for stays up to 90 days.'], 'USA'),
    # The bare token "eu" is a French participle and a Portuguese pronoun, not the European Union.
    ('VISA_EXEMPT', ['Les ressortissants qui ont eu un titre de séjour sont dispensés de visa pour un séjour de moins de 90 jours.'], 'FRA'),
    ('VISA_EXEMPT', ['Les personnes ayant eu un visa de long séjour sont dispensées de visa pour un séjour de moins de 90 jours.'], 'ESP'),
    # The pronoun "us", the currency prefix "US$" and the US Embassy name nobody; a universal sentence after them names no one.
    ('VISA_REQUIRED', ['Tourist visa fee: US$50. All visitors must obtain a visa before arrival.'], 'USA'),
    ('VISA_REQUIRED', ['The fee is US $50 per entry. Every foreign national needs a visa to enter.'], 'USA'),
    ('VISA_EXEMPT', ['For enquiries please contact us. Visitors do not need a visa for stays up to 90 days.'], 'USA'),
    ('VISA_REQUIRED', ['Apply at the US Embassy in Hanoi. A visa is required.'], 'USA'),
    ('VISA_REQUIRED', ['Fees are payable in US dollars and a visa is required.'], 'USA'),
    # The Vietnamese pronoun "anh" is not the United Kingdom.
    ('VISA_EXEMPT', ['Anh được miễn thị thực 45 ngày.'], 'GBR'),
    # A negated mention in the rule sentence speaks about everyone else.
    ('VISA_REQUIRED', ['A visa is required for non-US citizens.'], 'USA'),
    # An authorisation named without a requirement word offers a service, names a product or prices it. It states no requirement.
    ('ELECTRONIC_AUTHORIZATION_REQUIRED', ['Chinese resident of Taiwan satisfying the following criteria can make use of this online service to apply for pre-arrival registration to visit the HKSAR:'], 'TWN'),
    ('ELECTRONIC_AUTHORIZATION_REQUIRED', ['Visa eVisitor pour les détenteurs de passeports européens (y compris français) et autres nationalités éligibles'], 'FRA'),
    ('ELECTRONIC_AUTHORIZATION_REQUIRED', ['Hong Kong SAR passport holders may be eligible for an Australian Electronic Authority (ETA).'], 'HKG'),
    ('ELECTRONIC_AUTHORIZATION_REQUIRED', ['Indian nationals may use the pre-arrival registration counter for baggage.'], 'IND'),
    # The Spanish demonstrative "esta" is not the ESTA.
    ('ELECTRONIC_AUTHORIZATION_REQUIRED', ['Esta informacion es para ciudadanos estadounidenses.'], 'USA'),
    ('ELECTRONIC_AUTHORIZATION_REQUIRED', ['Esta autorización es obligatoria para ciudadanos estadounidenses.'], 'USA'),
    # "可申請網簽" offers the online visa as an option. Only a requirement marker beside it states a requirement.
    ('VISA_REQUIRED', ['香港或澳門居民現行可申請網簽或入出境許可證來臺，'], 'HKG'),
    ('VISA_REQUIRED', ['香港居民來臺不需申請網簽。'], 'HKG'),
    # A fee schedule row prices a product for its buyer and carries no verdict (etakenya.go.ke AIC 14/25, USA|USA|KEN).
    ('ELECTRONIC_AUTHORIZATION_REQUIRED', ['USA 5-year multiple entry eTA $185'], 'USA'),
    ('VISA_ON_ARRIVAL', ['Visa on arrival for US citizens: $25'], 'USA'),
    # A price row lends its buyer to no universal sentence after it.
    ('VISA_REQUIRED', ['Visa fee for USA nationals: $50. All visitors must obtain a visa before arrival.'], 'USA'),
    # An exception clause the converter reads only in part ("those Canadians that fall under nonimmigrant
    # visa categories E, K, S, or V") carves the nationality out of the rule it cannot finish reading
    # (round three): the row is refused rather than proved from half a sentence.
    ('VISA_EXEMPT', ['A visa is generally not required for Canadian citizens, except those Canadians that fall under nonimmigrant visa categories E, K, S, or V as provided in paragraphs (h), (l), and (m) of this section and 22 CFR 41.2.'], 'CAN'),
])
def test_verdict_rules_still_refuse_unnamed_grouped_or_borrowed_sentences(value, quotes, nat):
    from scripts.convert_reviewed_general_batch import _decision_supported
    assert not _decision_supported(value, quotes, nat)


@pytest.mark.parametrize('value,quotes,nat', [
    ('VISA_EXEMPT', ['Visa is not required for a stay of less than one (1) month for ASEAN nationals except Myanmar. Visas required for duration of stay exceeds (1) month except for Brunei and Singapore nationals.'], 'IDN'),
    ('VISA_EXEMPT', ['Visa tidak diperlukan untuk tempoh tinggal kurang daripada satu (1) bulan bagi warganegara negara-negara ASEAN kecuali Myanmar.'], 'THA'),
    ('VISA_EXEMPT', ['EU-Bürger benötigen generell kein Visum für die Einreise nach Deutschland.'], 'FRA'),
    ('VISA_EXEMPT', ['EU citizens do not need a visa for stays of up to 90 days.'], 'ESP'),
    ('VISA_EXEMPT', ['Citizens of the European Union are exempt from visa requirements.'], 'ITA'),
])
def test_group_membership_resolves_asean_and_eu_against_the_membership_table(value, quotes, nat):
    from scripts.convert_reviewed_general_batch import _decision_supported
    explain = []
    assert _decision_supported(value, quotes, nat, explain=explain)
    assert explain and explain[-1].startswith('group membership')


def test_quotes_split_across_one_page_sentence_or_table_row_are_read_together():
    """The captured page shows where the reviewer cut: two quotes inside one
    sentence, or a country line and its verdict cell in one table row, are one
    passage; a country line on one page and a rule on another are not."""
    from scripts.convert_reviewed_general_batch import _decision_supported
    page = ('Nationals holding valid ordinary passports of 48 countries, namely Brunei, France, Germany, Australia, Poland, '
            'Portugal, Greece, Cyprus and Croatia, are exempted from visa requirement if coming to China for the purpose of '
            'business, tourism, family or friends visits, exchange and transit. They can stay in China for no more than 30 days.')
    quotes = ['Nationals holding valid ordinary passports of 48 countries, namely Brunei, France, Germany, Australia, Poland',
              'are exempted from visa requirement if coming to China for the purpose of business, tourism, family or friends visits, exchange and transit.']
    assert not _decision_supported('VISA_EXEMPT', quotes, 'AUS')
    assert _decision_supported('VISA_EXEMPT', quotes, 'AUS', pages=[('p1', page), ('p1', page)])
    table = 'VISA REQUIREMENTS\nUNITED KINGDOM\nVisa required\nUNITED STATES OF AMERICA\nNO visa required for 6 months\nURUGUAY\nVisa required\n'
    row = ['UNITED STATES OF AMERICA', 'NO visa required for 6 months']
    assert _decision_supported('VISA_EXEMPT', row, 'USA', pages=[('p2', table), ('p2', table)])
    # The cell of another row does not travel: a line break separates the rows.
    wrong = ['UNITED KINGDOM', 'NO visa required for 6 months']
    assert not _decision_supported('VISA_EXEMPT', wrong, 'GBR', pages=[('p2', table), ('p2', table)])
    # A list line proves a general sentence only on the same captured page.
    rule = 'Citizens of the following states, holders of all types of passports, do not need visas for the entry.'
    assert not _decision_supported('VISA_EXEMPT', ['United States of America', rule], 'USA',
                                   pages=[('p3', 'United States of America\n'), ('p4', rule)])
    assert _decision_supported('VISA_EXEMPT', ['United States of America', rule], 'USA',
                               pages=[('p5', rule + '\nCanada\nUnited States of America\n')] * 2)


@pytest.mark.parametrize('sentence,nat', [
    ('Les ressortissants américains doivent obtenir un visa avant leur arrivée.', 'USA'),
    ('Nationals of the United States must obtain a visa before travel.', 'USA'),
    ('American citizens must obtain a visa before travel.', 'USA'),
    # A nationality-shaped "US" still names the United States: beside a nationality noun or after "the".
    ('US citizens must obtain a visa before travel.', 'USA'),
    ('Citizens of the US must obtain a visa before travel.', 'USA'),
    ('Nationals of the U.S. must obtain a visa before travel.', 'USA'),
    ('Công dân Anh phải xin thị thực trước khi nhập cảnh.', 'GBR'),
])
def test_the_plain_demonym_still_names_the_nationality(sentence, nat):
    from scripts.convert_reviewed_general_batch import _decision_supported
    assert _decision_supported('VISA_REQUIRED', [sentence], nat)


def test_a_genuine_visa_issued_on_entry_still_reads_as_visa_on_arrival():
    from scripts.convert_reviewed_general_batch import _decision_supported
    assert _decision_supported('VISA_ON_ARRIVAL', ['U.S. citizens are not required to apply for a visa before traveling to the UAE. '
                                                   'A visa will be issued upon entry, allowing a maximum stay of 90 days.'], 'USA')
    assert _decision_supported('VISA_ON_ARRIVAL', ['Indian nationals can obtain a visa on arrival at the airport.'], 'IND')


@pytest.mark.parametrize('quote,nat,expected', [
    ('9. Australia', 'AUS', True),
    ('a US national or permanent resident', 'USA', True),
    # A space-separated run whose names carry ampersands has no item
    # boundaries the converter can read (round five, minor 3): not a list line.
    ('St Lucia Trinidad & Tobago Uruguay St Vincent & Grenadines USA', 'USA', False),
    ('Norway, Poland, Portugal, Qatar, Romania, Russia, and the United States,', 'USA', True),
    ('EUA (Estados Unidos da América)', 'USA', True),
    ('Proof of residency or Permanent Resident Card – for non-US citizens.', 'USA', False),
    ('Eritrea’s diplomatic mission to the United States of America', 'USA', False),
    ('unless residing in Hong Kong SAR with a valid HKSAR ID Card', 'HKG', False),
    ('Those who hold a passport issued by Taiwan that includes in it the number of the identification card', 'TWN', False),
    ('other than Indian nationals', 'IND', False),
    # A dependency or another jurisdiction that carries the parent's name is not the parent's line.
    ('British Virgin Islands', 'GBR', False),
    ('British Indian Ocean Territory', 'GBR', False),
    ('British Overseas Territories', 'GBR', False),
    ('United States Minor Outlying Islands', 'USA', False),
    ('United States Virgin Islands', 'USA', False),
    ('US Virgin Islands', 'USA', False),
    ('Îles Vierges américaines', 'USA', False),
    ('îles mineures eloignees des etats-unis', 'USA', False),
    ('French Polynesia', 'FRA', False),
    ('Polynésie française', 'FRA', False),
    ('Chinese Taipei', 'CHN', False),
    ('Hong Kong SAR, China', 'CHN', False),
    ('Taiwan, Province of China', 'CHN', False),
    ('China, Hong Kong SAR', 'CHN', False),
    ('Korea (North)', 'KOR', False),
    # An entry that includes the sibling jurisdiction is still the line of both.
    ('China (including Hong Kong and Macau)', 'HKG', True),
    ('China (includes Hong Kong and Macau)', 'CHN', True),
    ('Hong Kong, China', 'HKG', True),
    # The parent's own line still is.
    ('United States of America', 'USA', True),
    ("ETATS-UNIS D'AMERIQUE", 'USA', True),
    ('Hong Kong (HKSAR or HK COF I)', 'HKG', True),
    ('Russian Federation', 'RUS', True),
    ('United Kingdom of Great Britain and Northern Ireland', 'GBR', True),
])
def test_a_list_line_is_the_name_with_at_most_a_short_qualifier(quote, nat, expected):
    from scripts.convert_reviewed_general_batch import _list_line
    assert _list_line(quote, nat) is expected


def test_a_list_line_proves_only_the_heading_of_its_own_list():
    """One page, a visa-required list then a visa-free list. India sits in
    the first list only; quoting the second heading beside it proves
    nothing, quoting the first does."""
    from scripts.convert_reviewed_general_batch import _decision_supported
    page = ('Countries with a visa requirement\n'
            'If you are a citizen of one of the following countries, you must have a visa in order to enter Denmark:\n'
            'Afghanistan\nAlbania**** (Citizens with biometric passports are exempt from the visa requirement.)\nIndia*\nIraq\n'
            'Visa-free countries\n'
            'If you are a citizen of one of the following countries, you do not need a visa in order to enter Denmark:\n'
            'Andorra\nAustralia\nCanada\nUSA\n')
    required = 'If you are a citizen of one of the following countries, you must have a visa in order to enter Denmark:'
    free = 'If you are a citizen of one of the following countries, you do not need a visa in order to enter Denmark:'
    pages = [('s979', page)] * 2
    explain = []
    assert not _decision_supported('VISA_EXEMPT', ['India', free], 'IND', pages=pages, explain=explain)
    assert any(e.startswith('list line on another page or under another heading') for e in explain)
    assert _decision_supported('VISA_REQUIRED', ['India', required], 'IND', pages=pages)
    assert _decision_supported('VISA_EXEMPT', ['Australia', free], 'AUS', pages=pages)
    assert not _decision_supported('VISA_REQUIRED', ['Australia', required], 'AUS', pages=pages)
    # A sentence that refers back to the list above it binds to the list before it.
    above = 'Andorra\nAustralia\nCanada\nNationals of the countries listed above do not need a visa.\nIndia\nNationals of the countries listed above must have a visa.\n'
    assert _decision_supported('VISA_EXEMPT', ['Australia', 'Nationals of the countries listed above do not need a visa.'], 'AUS',
                               pages=[('p', above)] * 2)
    assert not _decision_supported('VISA_REQUIRED', ['Australia', 'Nationals of the countries listed above must have a visa.'], 'AUS',
                                   pages=[('p', above)] * 2)


def test_a_dependency_line_cannot_prove_the_parent_nationality_on_a_page():
    from scripts.convert_reviewed_general_batch import _decision_supported
    page = ('Nationals of the following countries do not need a visa:\nBritish Virgin Islands\nCanada\nJapan\n'
            'Nationals of the following countries must obtain a visa:\nIndia\nUnited Kingdom\n')
    free = 'Nationals of the following countries do not need a visa:'
    assert not _decision_supported('VISA_EXEMPT', [free, 'British Virgin Islands'], 'GBR', pages=[('p', page)] * 2)
    assert _decision_supported('VISA_REQUIRED', ['Nationals of the following countries must obtain a visa:', 'United Kingdom'], 'GBR',
                               pages=[('p', page)] * 2)


def test_a_plain_verdict_heading_that_opens_a_list_cuts_the_earlier_headings_reach():
    """'Visa-free countries' carries no list-intro wording, but it ends its
    line and opens a block of entry lines: it is a section boundary, so the
    visa-required heading above it cannot reach the countries below it."""
    from scripts.convert_reviewed_general_batch import _decision_supported, _headings, _page_index
    page = ('Visa required countries: the nationals of the following countries require a visa to enter Freedonia.\n'
            'Brazil\nChina\nIndia\nVisa-free countries\nArgentina\nUnited States of America\nUruguay')
    heading = 'Visa required countries: the nationals of the following countries require a visa to enter Freedonia.'
    pages = [('p', page)] * 2
    assert [h.strip() for h in _headings(_page_index(page)[0])[1]] == [heading.rstrip('.').casefold(), 'visa-free countries']
    explain = []
    assert not _decision_supported('VISA_REQUIRED', [heading, 'United States of America'], 'USA', pages=pages, explain=explain)
    assert any(e.startswith('list line on another page or under another heading') for e in explain)
    assert _decision_supported('VISA_REQUIRED', [heading, 'India'], 'IND', pages=pages)
    # A line with a verdict word that is followed by prose, not entries, opens
    # no list, but every line standing alone in its own block is a section
    # boundary (round five): the title and the prose sentence both cut.
    prose = 'Visa fees\nThe fee depends on the visa type and is payable on application.\nBrazil\nIndia\n'
    assert _headings(_page_index(prose)[0])[1] == ['visa fees', 'the fee depends on the visa type and is payable on application']


def test_a_country_line_under_two_verdict_headings_is_refused_not_resolved():
    """When the nationality occurs in both lists of one page, the reviewer's
    other quote must not pick the occurrence; the row is refused."""
    from scripts.convert_reviewed_general_batch import _decision_supported
    page = ('Countries whose citizens must have a visa: Albania India Nepal Pakistan.' + ' filler' * 50 +
            '. Countries whose citizens do not need a visa: Australia Canada India Japan.')
    pages = [('p', page)] * 2
    for value, heading in (('VISA_EXEMPT', 'Countries whose citizens do not need a visa:'),
                           ('VISA_REQUIRED', 'Countries whose citizens must have a visa:')):
        explain = []
        assert not _decision_supported(value, ['India', heading], 'IND', pages=pages, explain=explain)
        assert any(e.startswith('list line occurs under different verdict headings') for e in explain)
    # Japan occurs once, under the visa-free heading only.
    assert _decision_supported('VISA_EXEMPT', ['Japan', 'Countries whose citizens do not need a visa:'], 'JPN', pages=pages)
    assert not _decision_supported('VISA_REQUIRED', ['Japan', 'Countries whose citizens must have a visa:'], 'JPN', pages=pages)


def test_the_carried_nationality_does_not_cross_a_flipped_sentence_on_a_page():
    from scripts.convert_reviewed_general_batch import _decision_supported
    page = ('Canadian citizens do not need a visa for stays of up to 90 days. '
            'Those wishing to stay longer must apply for a visa at the embassy before travel.')
    quotes = ['Canadian citizens do not need a visa for stays of up to 90 days.',
              'Those wishing to stay longer must apply for a visa at the embassy before travel.']
    assert not _decision_supported('VISA_REQUIRED', quotes, 'CAN', pages=[('p', page)] * 2)
    assert _decision_supported('VISA_EXEMPT', quotes, 'CAN', pages=[('p', page)] * 2)


def test_an_unpublished_stay_proof_cannot_cover_a_stated_stay_sentence():
    """A not_published permitted_stay_days proof leaves the prose it covers
    empty: the sentence is dropped with its reason and the served entry
    carries no invented stay."""
    b = batch()
    row = b['rows'][0]
    row['route_fields']['permitted_stay_days'] = None
    row['route_fields']['permitted_stay'] = 'The period of stay is set by the officer at the checkpoint on entry.'
    row['route_field_proofs']['permitted_stay_days'] = {'status': 'not_published', 'verifier': 'ai',
                                                        'reason': 'No fixed number of days is published'}
    _, accepted, rejected = validate_batch(b, strict=False)
    assert rejected == [] and len(accepted) == 1
    assert 'permitted_stay' not in accepted[0]['route_fields']
    assert any(d.startswith('permitted_stay: a value under an unknown or unpublished permitted_stay_days proof') for d in accepted[0]['dropped'])
    assert 'permitted_stay_days' in accepted[0]['route_field_proofs']
    overlay, _ = convert(build_manifest(b, [layer()]), [layer()])
    fields = overlay['entries'][0]['fields']
    assert fields['permitted_stay'] is None and fields['permitted_stay_days'] is None
    assert 'max_stay_duration' in fields['unpublished_fields']
    # The pop in _validate_row is the single mechanism: _check_proof carries
    # no covered= guard that production could never reach.
    import inspect
    from scripts.convert_reviewed_general_batch import _check_proof
    assert 'covered' not in inspect.signature(_check_proof).parameters


def test_a_product_carrying_the_route_verdict_inherits_the_route_proof_when_its_own_fails():
    """The product's own disposition quote does not state its verdict, but
    the product asserts exactly the route's proved verdict: it is served
    under the route's nationality-anchored proof, and the failure is
    reported instead of the product being erased."""
    b = batch()
    row = b['rows'][0]
    row['products'][0]['proofs']['disposition'] = proof('Nationals of Japan do not require a visa for stays of up to 45 days.')
    _, accepted, _ = validate_batch(b, strict=False)
    assert accepted[0]['products'][0]['verdict_unproved'].startswith('disposition:')
    overlay, reports = convert(build_manifest(b, [layer()]), [layer()])
    products = overlay['entries'][0]['fields']['visa_products']
    assert [p['type'] for p in products] == [PRODUCT['type']]
    assert products[0]['disposition'] == 'VISA_REQUIRED' and products[0]['requirement_detail'] == 'evisa'
    assert products[0]['field_provenance']['disposition']['quote'].startswith('Holders of a Hong Kong SAR passport')
    assert products[0]['fee'] == {'amount': 25, 'currency': 'USD'}
    assert reports[0]['removed_products'] == []
    assert reports[0]['route_verdict_products'] == [{'type': PRODUCT['type'], 'reason': accepted[0]['products'][0]['verdict_unproved']}]
    assert reports[0]['unsupported_products'] == []


def test_a_product_verdict_that_differs_from_the_route_and_fails_its_proof_is_not_served():
    """A product that asserts a verdict the route does not carry must prove
    it; when its quote does not, the product is dropped with the reason."""
    b = batch()
    row = b['rows'][0]
    row['products'][0]['product']['requirement_detail'] = 'paper_visa'
    row['products'][0]['proofs']['disposition'] = proof('Nationals of Japan do not require a visa for stays of up to 45 days.')
    _, accepted, _ = validate_batch(b, strict=False)
    assert accepted[0]['products'][0]['verdict_unproved'].startswith('disposition:')
    overlay, reports = convert(build_manifest(b, [layer()]), [layer()])
    assert overlay['entries'][0]['fields']['visa_products'] == []
    assert reports[0]['removed_products'] == [{'type': PRODUCT['type'], 'reason': accepted[0]['products'][0]['verdict_unproved']}]
    assert reports[0]['route_verdict_products'] == [] and reports[0]['unsupported_products'] == []


# Round-two review regressions. Each case reproduces a scenario the review
# verified live against the converter at 3be3699.

def route_batch(nat, dest, disposition, detail, text, quote, url, key):
    """A one-row batch whose verdict rests on one quote of one captured page."""
    b = batch()
    b['sources'] = [source(text=text, url=url)]
    route = {'passport_nationality': nat, 'destination_country': dest, 'travel_purpose': 'tourism',
             'travel_document_type': 'ordinary_passport'}
    verdict_proof = {'status': 'reviewed', 'verifier': 'ai', 'verified_at': '2026-09-09', 'scope_note': 'ordinary passport, tourism',
                     'evidence': [{'source_id': 's1', 'source_url': url, 'quote': quote}]}
    b['rows'] = [{'cache_key': key, 'route': route, 'verdict': {'disposition': disposition, 'requirement_detail': detail,
                                                                 'proof': verdict_proof},
                  'route_fields': {}, 'route_field_proofs': {}, 'products': []}]
    return b


ASEAN_SENTENCE = 'Visa is not required for a stay of less than one (1) month for ASEAN nationals except Myanmar.'
IMI_URL = 'https://www.imi.gov.my/index.php/en/main-services/visa/visa-requirement-by-country/'


def test_every_group_member_has_a_real_name_pattern():
    """A carve-out is read through the member's names, so a member without
    names could never be carved out. The import-time assertion guards the
    table. This test proves each member is really named by its own name."""
    from scripts.convert_reviewed_general_batch import _GROUPS, _EXTRA_ALIASES, _name_pattern, _named
    for _, members in _GROUPS.values():
        for member in sorted(members):
            assert _name_pattern(member).pattern != r'(?!x)x', member
            assert _EXTRA_ALIASES.get(member), member
            assert _named(_EXTRA_ALIASES[member][0], member), member


@pytest.mark.parametrize('quote,nat', [
    (ASEAN_SENTENCE, 'MMR'),
    ('Visa is not required for a stay of less than one (1) month for ASEAN nationals except Laos.', 'LAO'),
    ('Visa is not required for a stay of less than one (1) month for ASEAN nationals except Brunei.', 'BRN'),
    ('Visa is not required for a stay of less than one (1) month for ASEAN nationals except Timor-Leste.', 'TLS'),
    ('EU citizens do not need a visa for stays of up to 90 days, except for Bulgaria and Romania.', 'BGR'),
    ('EU citizens do not need a visa for stays of up to 90 days, except for Bulgaria and Romania.', 'ROU'),
    ('Citizens of the European Union are exempt from visa requirements, except Bulgarian and Romanian citizens.', 'ROU'),
])
def test_a_group_carve_out_excludes_every_member_by_name(quote, nat):
    from scripts.convert_reviewed_general_batch import _decision_supported, _carved_out
    assert _carved_out(quote, nat)
    assert not _decision_supported('VISA_EXEMPT', [quote], nat)


def test_the_carved_out_member_row_is_refused_while_the_other_members_still_convert():
    """MMR|MMR|MYS on the exact imi.gov.my sentence three kept rows use: the
    page states the opposite verdict for Myanmar by name, so the row is
    refused. Laos, Brunei and Timor-Leste are still read as members."""
    from scripts.convert_reviewed_general_batch import _decision_supported
    text = 'Visa Requirement by Country. ' + ASEAN_SENTENCE + ' Visas required for duration of stay exceeds (1) month except for Brunei and Singapore nationals.'
    b = route_batch('MMR', 'MYS', 'VISA_EXEMPT', 'unconditional_visa_free', text, ASEAN_SENTENCE, IMI_URL,
                    'MMR|MMR|MYS|tourism|default|unknown|v6')
    with pytest.raises(PatchRejected, match='does not state this verdict for this nationality'):
        validate_batch(b)
    for nat in ('LAO', 'BRN', 'TLS', 'IDN'):
        explain = []
        assert _decision_supported('VISA_EXEMPT', [ASEAN_SENTENCE], nat, explain=explain)
        assert explain[-1].startswith('group membership')
    assert _decision_supported('VISA_EXEMPT', ['EU citizens do not need a visa for stays of up to 90 days, except for Bulgaria and Romania.'], 'ESP')


@pytest.mark.parametrize('quote,members', [
    ('EU citizens do not need a visa for stays of up to 90 days, except for the nationalities listed below.', ('ESP', 'FRA')),
    ('ASEAN nationals do not need a visa except:', ('IDN', 'MMR')),
    ('Visa is not required for a stay of less than one (1) month for ASEAN nationals except the countries listed in the table.', ('IDN', 'MMR')),
])
def test_a_group_sentence_with_an_unreadable_exception_proves_nothing_for_any_member(quote, members):
    """An exception the converter cannot resolve to a nationality may except
    this member in words it cannot read: the group path is refused."""
    from scripts.convert_reviewed_general_batch import _decision_supported, _unreadable_exception
    assert _unreadable_exception(quote) is not None
    for nat in members:
        explain = []
        assert not _decision_supported('VISA_EXEMPT', [quote], nat, explain=explain)
        # Round five reads the whole evidence before any sentence decides, so
        # the refusal is recorded there; the per-sentence gate still stands.
        assert any('exception' in e and 'cannot be read' in e for e in explain)


FREEDONIA_RULE = 'Nationals of the following countries must obtain a visa before travelling to Freedonia.'
FREEDONIA_PAGE = (FREEDONIA_RULE + '\n'
                  'Afghanistan, Bangladesh, India, Pakistan\n'
                  'Visa-free countries\n'
                  'Argentina, Australia, France, Germany, Japan, Spain, United States\n')


def test_a_verdict_heading_over_one_comma_list_line_is_a_section_boundary():
    """The Freedonia page with each list on one comma line: the plain
    "Visa-free countries" heading cuts the reach of the visa-required rule
    sentence, so Spain, France, Japan and the USA are not proved
    VISA_REQUIRED from their line in the visa-free list."""
    from scripts.convert_reviewed_general_batch import _decision_supported, _headings, _page_index
    assert [h.strip() for h in _headings(_page_index(FREEDONIA_PAGE)[0])[1]] == [FREEDONIA_RULE.rstrip('.').casefold(), 'visa-free countries']
    pages = [('p', FREEDONIA_PAGE)] * 2
    free_line = 'Argentina, Australia, France, Germany, Japan, Spain, United States'
    for nat in ('ESP', 'FRA', 'JPN', 'USA'):
        explain = []
        assert not _decision_supported('VISA_REQUIRED', [free_line, FREEDONIA_RULE], nat, pages=pages, explain=explain)
        assert any(e.startswith('list line on another page or under another heading') for e in explain)
    assert _decision_supported('VISA_REQUIRED', ['Afghanistan, Bangladesh, India, Pakistan', FREEDONIA_RULE], 'IND', pages=pages)
    # A verdict line ending its own line is a boundary whatever follows it, and
    # so is the prose sentence standing alone after it (round five).
    followed_by_prose = 'Visa-free countries\nThe nationals listed here may enter for 90 days.\nFrance, Spain\n'
    assert _headings(_page_index(followed_by_prose)[0])[1] == ['visa-free countries', 'the nationals listed here may enter for 90 days']
    # A rule-word line that states no verdict still opens a list when one comma line of entries follows it.
    assert _headings(_page_index('ETA countries\nAustralia, Canada, Japan\n')[0])[1] == ['eta countries']
    assert _headings(_page_index('Visa fees\nThe fee depends on the visa type and is payable on application.\nBrazil\nIndia\n')[0])[1] == [
        'visa fees', 'the fee depends on the visa type and is payable on application']


def test_the_freedonia_row_is_refused_end_to_end():
    b = route_batch('ESP', 'VNM', 'VISA_REQUIRED', 'evisa', FREEDONIA_PAGE,
                    'Argentina, Australia, France, Germany, Japan, Spain, United States', URL, 'ESP|ESP|VNM|tourism|default|unknown|v6')
    b['rows'][0]['verdict']['proof']['evidence'].append({'source_id': 's1', 'source_url': URL, 'quote': FREEDONIA_RULE})
    with pytest.raises(PatchRejected, match='does not state this verdict for this nationality'):
        validate_batch(b)


@pytest.mark.parametrize('value,quote,nat', [
    ('VISA_EXEMPT', 'Holders of British Virgin Islands passports do not require a visa for stays of up to 90 days.', 'GBR'),
    ('VISA_EXEMPT', 'Holders of French Polynesia travel documents do not require a visa.', 'FRA'),
    ('VISA_REQUIRED', 'Chinese Taipei nationals must obtain a visa before arrival.', 'CHN'),
    ('VISA_REQUIRED', 'Residents of Hong Kong SAR, China must obtain a visa before travel.', 'CHN'),
    ('VISA_REQUIRED', 'Nationals of Canada, the British Virgin Islands and Japan need a visa.', 'GBR'),
    ('VISA_EXEMPT', 'British Virgin Islands: holders do not require a visa.', 'GBR'),
])
def test_a_dependency_in_a_rule_sentence_or_label_does_not_name_the_parent(value, quote, nat):
    """The dependency guard applies wherever the nationality is read, not
    only on the list-line path: rule sentences, labels and carries."""
    from scripts.convert_reviewed_general_batch import _decision_supported, _named
    assert not _named(quote, nat)
    assert not _decision_supported(value, [quote], nat)


@pytest.mark.parametrize('quote,nat', [
    ('Nationals of Canada, the British Virgin Islands and Japan need a visa.', 'CAN'),
    ('Nationals of the Cayman Islands, Chile and China must obtain a visa.', 'CHN'),
    ('Residents of Hong Kong SAR, China must obtain a visa before travel.', 'HKG'),
    ('Nationals of China, Hong Kong and Macau need a visa.', 'CHN'),
    ('Nationals of China, Hong Kong and Macau need a visa.', 'HKG'),
    ('US citizens residing in the territory of another state must obtain a visa at the nearest consulate.', 'USA'),
    ('Nationals of China (including Hong Kong and Macau) must obtain a visa.', 'HKG'),
])
def test_a_territory_word_elsewhere_in_the_sentence_still_leaves_the_nationality_named(quote, nat):
    from scripts.convert_reviewed_general_batch import _decision_supported, _named
    assert _named(quote, nat)
    assert _decision_supported('VISA_REQUIRED', [quote], nat)


def test_a_tour_group_only_visa_rule_is_refused_for_the_general_route_because_the_condition_gate_runs_for_every_disposition():
    """Round five, major 1: 'Indian nationals who are part of a tour group
    must obtain a visa.' still names India (the territory guard is not the
    reason), but a rule scoped to tour groups is conditional, and a visa
    requirement has no conditional subcategory to carry it. The earlier
    test asserted this sentence proved the general route."""
    from scripts.convert_reviewed_general_batch import _named
    quote = 'Indian nationals who are part of a tour group must obtain a visa.'
    assert _named(quote, 'IND')
    ok, explain = supported('VISA_REQUIRED', [quote], 'IND', detail='evisa')
    assert not ok and any(e.startswith('conditional wording') for e in explain)


def test_a_dropped_field_proof_leaves_the_prior_value_and_provenance_and_is_reported():
    """Round two blanked every cell whose new proof failed, and round three
    measured 175 correct prior fields erased across 76 kept rows (CAN|CAN|KOR
    lost its K-ETA channel, detail and portal URL). A failed proof asserts
    nothing: the prior layer's value and its prior provenance stand, the
    report lists the drop, and with no prior the field is simply absent."""
    good = batch()
    prior_overlay, _ = convert(build_manifest(good, [layer()]), [layer()])
    prior = prior_overlay['entries'][0]
    assert prior['fields']['government_fee'] == {'amount': 25, 'currency': 'USD'}
    with_prior = layer(seed_entries=[prior])
    b = batch()
    row = b['rows'][0]
    row['route_field_proofs']['government_fee']['evidence'][0] = {'source_id': 's1', 'source_url': URL,
                                                                  'quote': 'Your application will be processed in 3 working days'}
    row['route_field_proofs'].pop('processing_time')
    _, accepted, rejected = validate_batch(b, strict=False)
    assert rejected == [] and len(accepted) == 1
    fee_proof = accepted[0]['route_field_proofs']['government_fee']
    assert fee_proof['status'] == 'unknown' and fee_proof['dropped'] and 'fee amount is not in its evidence' in fee_proof['reason']
    time_proof = accepted[0]['route_field_proofs']['processing_time']
    assert time_proof['status'] == 'unknown' and time_proof['dropped'] and 'a value without a proof' in time_proof['reason']
    # With a prior: the prior value and the prior review's provenance survive untouched.
    overlay, reports = convert(build_manifest(b, [with_prior]), [with_prior])
    fields = overlay['entries'][0]['fields']
    provenance = overlay['entries'][0]['field_provenance']
    assert fields['government_fee'] == {'amount': 25, 'currency': 'USD'}
    assert fields['processing_time'] == '3 working days'
    assert provenance['government_fee'] == prior['field_provenance']['government_fee']
    assert provenance['processing_time'] == prior['field_provenance']['processing_time']
    assert provenance['government_fee']['status'] == 'reviewed'
    assert any(d.startswith('government_fee: the fee amount is not in its evidence') for d in reports[0]['dropped'])
    assert any(d.startswith('processing_time: a value without a proof') for d in reports[0]['dropped'])
    # Without a prior: nothing is asserted and nothing is invented.
    overlay, reports = convert(build_manifest(b, [layer()]), [layer()])
    fields = overlay['entries'][0]['fields']
    assert fields.get('government_fee') is None and fields.get('processing_time') is None
    assert 'government_fee' not in overlay['entries'][0]['field_provenance']
    assert 'processing_time' not in overlay['entries'][0]['field_provenance']
    assert any(d.startswith('government_fee: the fee amount is not in its evidence') for d in reports[0]['dropped'])
    # The proved values are still served either way.
    assert fields['permitted_stay_days'] == 90


def test_a_dropped_proof_keeps_the_prior_not_published_state():
    """A cell the earlier review marked not published stays so when a later
    proof for it fails: the drop must not degrade it to a plain blank."""
    first = batch()
    first['rows'][0]['route_fields']['processing_time'] = None
    first['rows'][0]['route_field_proofs']['processing_time'] = proof(
        status='not_published', reason='No processing time is published.')
    prior_overlay, _ = convert(build_manifest(first, [layer()]), [layer()])
    prior = prior_overlay['entries'][0]
    assert 'processing_min_days' in prior['fields']['unpublished_fields']
    later = batch()
    later['rows'][0]['route_field_proofs']['processing_time']['evidence'][0] = {
        'source_id': 's1', 'source_url': URL, 'quote': 'Vietnam E-visa is valid for maximum of 90 days'}
    current = layer(seed_entries=[prior])
    overlay, reports = convert(build_manifest(later, [current]), [current])
    fields = overlay['entries'][0]['fields']
    assert fields['processing_time'] is None
    assert 'processing_min_days' in fields['unpublished_fields']
    from scripts.convert_reviewed_general_batch import _prior
    stored = _prior(current, ROUTE)['field_provenance']['processing_time']
    assert overlay['entries'][0]['field_provenance']['processing_time'] == stored
    assert stored['status'] == 'unknown' and 'dropped' not in stored
    assert any(d.startswith('processing_time:') for d in reports[0]['dropped'])


def test_only_an_explicit_reviewer_unknown_blanks_a_prior_value():
    """The reviewer's own unknown or not_published proof is the one thing
    that empties a field the earlier review had filled."""
    good = batch()
    prior_overlay, _ = convert(build_manifest(good, [layer()]), [layer()])
    current = layer(seed_entries=[prior_overlay['entries'][0]])
    later = batch()
    later['rows'][0]['route_fields']['processing_time'] = None
    later['rows'][0]['route_field_proofs']['processing_time'] = proof(status='unknown', reason='Not checked this round.')
    overlay, _ = convert(build_manifest(later, [current]), [current])
    fields = overlay['entries'][0]['fields']
    provenance = overlay['entries'][0]['field_provenance']
    assert fields['processing_time'] is None
    assert provenance['processing_time']['status'] == 'unknown' and 'dropped' not in provenance['processing_time']


def test_a_row_without_a_proof_table_still_records_its_dropped_value():
    b = batch()
    row = b['rows'][0]
    row.pop('route_field_proofs')
    row['route_fields'] = {'processing_time': '3 working days'}
    _, accepted, _ = validate_batch(b, strict=False)
    assert accepted[0]['route_field_proofs']['processing_time']['status'] == 'unknown'
    assert accepted[0]['route_field_proofs']['processing_time']['dropped']
    overlay, reports = convert(build_manifest(b, [layer()]), [layer()])
    assert overlay['entries'][0]['fields'].get('processing_time') is None
    assert 'processing_time' not in overlay['entries'][0]['field_provenance']
    assert reports[0]['dropped'] == ['processing_time: a value without a proof']


@pytest.mark.parametrize('quote,nat', [
    ('US citizens must obtain an ESTA before boarding.', 'USA'),
    ('Canadian citizens need an eTA to fly to Australia.', 'CAN'),
    ('Indian nationals are required to hold a valid ETA before travelling.', 'IND'),
    ('Travellers holding a Japanese passport must complete pre-arrival registration before boarding.', 'JPN'),
])
def test_an_authorisation_beside_a_requirement_word_still_proves_the_verdict(quote, nat):
    from scripts.convert_reviewed_general_batch import _decision_supported
    assert _decision_supported('ELECTRONIC_AUTHORIZATION_REQUIRED', [quote], nat)


def test_an_online_visa_beside_a_requirement_marker_still_proves_the_verdict():
    from scripts.convert_reviewed_general_batch import _decision_supported
    assert _decision_supported('VISA_REQUIRED', ['香港居民來臺須申請網簽。'], 'HKG')


def test_a_fee_row_is_no_rule_sentence_but_an_eligibility_sentence_with_a_fee_is():
    from scripts.convert_reviewed_general_batch import _decision_supported, _fee_row
    assert _fee_row('usa 5-year multiple entry eta $185')
    assert _fee_row('standard eta applications $30')
    assert not _fee_row('indian nationals are eligible for a visa on arrival at a fee of usd 25.')
    explain = []
    assert not _decision_supported('VISA_ON_ARRIVAL', ['Visa on arrival for US citizens: $25'], 'USA', explain=explain)
    assert any(e.startswith('fee or price row') for e in explain)
    assert _decision_supported('VISA_ON_ARRIVAL', ['Indian nationals are eligible for a visa on arrival at a fee of USD 25.'], 'IND')


def test_south_korean_names_korea_and_north_korean_does_not():
    from scripts.convert_reviewed_general_batch import _decision_supported, _named
    assert _named('South Korean nationals do not need a visa.', 'KOR')
    assert _decision_supported('VISA_EXEMPT', ['South Korean nationals do not need a visa.'], 'KOR')
    assert not _named('North Korean nationals must obtain a visa.', 'KOR')
    # The regional guard still holds for a nationality with no such name of its own.
    assert not _named('South American citizens are exempt from visa requirements.', 'USA')


@pytest.mark.parametrize('quote,nat', [
    ('L’entrée des ressortissants français est soumise à l’obtention d’un visa.', 'FRA'),
    ('Les ressortissants indiens ont besoin d’un visa pour entrer en France.', 'IND'),
    ('Les ressortissants indiens doivent être munis d’un visa.', 'IND'),
])
def test_french_requirement_clauses_read_the_typographic_apostrophe(quote, nat):
    from scripts.convert_reviewed_general_batch import _decision_supported
    assert _decision_supported('VISA_REQUIRED', [quote], nat)
    assert _decision_supported('VISA_REQUIRED', [quote.replace('’', "'")], nat)


@pytest.mark.parametrize('quote,nat', [
    ('Los ciudadanos de Indiana no necesitan visado.', 'IND'),
    ('Francesca no necesita visado.', 'FRA'),
    ('Mr Russell does not need a visa.', 'RUS'),
])
def test_a_demonym_stem_does_not_swallow_a_place_or_person_name(quote, nat):
    from scripts.convert_reviewed_general_batch import _decision_supported, _named
    assert not _named(quote, nat)
    assert not _decision_supported('VISA_EXEMPT', [quote], nat)


@pytest.mark.parametrize('quote,nat', [
    ('Les ressortissants indiens ont besoin d’un visa.', 'IND'),
    ('Indians need a visa.', 'IND'),
    ('Los ciudadanos indios necesitan visado.', 'IND'),
    ('Los ciudadanos franceses no necesitan visado.', 'FRA'),
    ('I cittadini francesi non hanno bisogno di visto.', 'FRA'),
    ('Les russes doivent obtenir un visa.', 'RUS'),
    ('Los ciudadanos rusos necesitan visado.', 'RUS'),
])
def test_the_retired_stems_inflected_forms_are_still_named(quote, nat):
    from scripts.convert_reviewed_general_batch import _named
    assert _named(quote, nat)


# Round-three review regressions. Every sentence below is one the review
# executed against the converter at 8051e48 and found proving the wrong
# verdict. Each case is refused now and the positive shape beside it still
# reads.

def supported(value, quotes, nat, **kw):
    from scripts.convert_reviewed_general_batch import _decision_supported
    explain = []
    ok = _decision_supported(value, quotes, nat, explain=explain, **kw)
    return ok, explain


@pytest.mark.parametrize('quote,nat', [
    ('EU citizens do not need a visa, with the exception of Bulgarian nationals.', 'BGR'),
    ('EU citizens do not need a visa, apart from Bulgarian nationals.', 'BGR'),
    ('EU citizens do not need a visa, aside from Bulgarian nationals.', 'BGR'),
    ("Les ressortissants de l'Union européenne sont dispensés de visa, à l'exception des Bulgares et des Roumains.", 'BGR'),
    ("Les ressortissants de l'Union européenne sont dispensés de visa, à l'exception des Bulgares et des Roumains.", 'ROU'),
    ("Les ressortissants de l'Union européenne sont dispensés de visa, hormis les Roumains.", 'ROU'),
    ('Los ciudadanos de la Unión Europea están exentos de visado, con excepción de los búlgaros.', 'BGR'),
    ('EU-Bürger benötigen kein Visum, mit Ausnahme von Bulgarien und Rumänien.', 'BGR'),
    ('EU-Bürger benötigen kein Visum, mit Ausnahme von Bulgarien und Rumänien.', 'ROU'),
    ('EU-Bürger benötigen kein Visum, ausgenommen Rumänien.', 'ROU'),
    ("I cittadini dell'Unione europea sono esenti dal visto, ad eccezione dei bulgari.", 'BGR'),
    ('Os cidadãos da União Europeia estão isentos de visto, com exceção dos búlgaros.', 'BGR'),
    ('Công dân ASEAN được miễn thị thực, ngoại trừ Bulgaria và Romania.', 'BGR'),
    ('Warga negara Uni Eropa bebas visa, selain Bulgaria.', 'BGR'),
    ('东盟国家公民免签，缅甸除外。', 'MMR'),
    ('東南アジア諸国連合の国民はビザ免除ですが、ミャンマーを除く。', 'MMR'),
    ('아세안 국민은 무비자입니다. 미얀마는 제외합니다.', 'MMR'),
    # A clause the converter reads only in part carves out both names, whatever their length.
    ('EU citizens do not need a visa, except Bulgarian and Romanian nationals holding non-biometric passports.', 'ROU'),
    ('EU citizens do not need a visa, except Bulgarian and Romanian nationals holding non-biometric passports.', 'BGR'),
])
def test_every_exception_wording_carves_the_excepted_nationality_out_of_the_sentence(quote, nat):
    from scripts.convert_reviewed_general_batch import _carved_out
    sentence, _, following = quote.partition('. ')
    assert _carved_out(sentence, nat, following or None)
    ok, explain = supported('VISA_EXEMPT', [quote], nat)
    # The whole-evidence reading of round five records the same carve-out
    # under 'rejected:' when the name is read there first.
    assert not ok and any('carved out by name' in e for e in explain)


def test_a_readable_exception_still_lets_the_other_members_through():
    ok, explain = supported('VISA_EXEMPT', ['EU citizens do not need a visa, with the exception of Bulgarian nationals.'], 'ESP')
    assert ok and explain[-1].startswith('group membership')
    ok, explain = supported('VISA_EXEMPT', ['아세안 국민은 무비자입니다. 미얀마는 제외합니다.'], 'THA')
    assert ok and explain[-1].startswith('group membership')
    ok, explain = supported('VISA_EXEMPT', ['东盟国家公民免签，缅甸除外。'], 'THA')
    assert ok and explain[-1].startswith('group membership')


@pytest.mark.parametrize('quote,nat', [
    # The excepted item is not a nationality the converter can name: nothing is proved for anyone, named or grouped.
    ('Indian nationals do not need a visa, with the exception of holders of emergency travel documents.', 'IND'),
    ('Les ressortissants indiens sont dispensés de visa, hormis les titulaires de documents de voyage provisoires.', 'IND'),
    ('EU citizens do not need a visa, apart from those listed in the annex.', 'FRA'),
    ('東南アジア諸国連合の国民はビザ免除ですが、外交旅券所持者を除く。', 'THA'),
])
def test_an_unreadable_exception_refuses_the_sentence_for_the_named_nationality_too(quote, nat):
    from scripts.convert_reviewed_general_batch import _unreadable_exception
    assert _unreadable_exception(quote) is not None
    ok, explain = supported('VISA_EXEMPT', [quote], nat)
    # "with the exception of holders of" also flips the exemption outright, and
    # a clause the round-seven scope test resolves to another document class
    # leaves the sentence scoped to that class. Every gate refuses the row.
    assert not ok and any('cannot be read' in e or e.startswith('flipped in the same sentence')
                          or e.startswith('scoped to') for e in explain), explain


def test_an_exception_clause_that_opens_a_list_is_the_rule_of_that_list():
    """imi.gov.my: "...except for the following countries which do not require
    a visa for any purpose of entry:" heads the exempt list. The clause proves
    nothing by name or group, but the country line under it proves the
    verdict the clause states."""
    rule = 'EXCEPT for the following countries which do not require a visa for any purpose of entry:'
    page = ('This visa facility is open to all foreign nationals who require a visa to enter Malaysia, ' + rule.lower() + '\n'
            'No. | Country\n1. | South Africa\n2. | Australia\n3. | United Kingdom\n4. | Canada\n')
    ok, explain = supported('VISA_EXEMPT', [rule, 'United Kingdom'], 'GBR', pages=[('p', page)] * 2)
    assert ok and explain[-1].startswith('list line beside the rule sentence')
    ok, _ = supported('VISA_REQUIRED', [rule, 'United Kingdom'], 'GBR', pages=[('p', page)] * 2)
    assert not ok
    ok, _ = supported('VISA_EXEMPT', [rule], 'GBR')
    assert not ok


@pytest.mark.parametrize('value,quote,nat', [
    ('VISA_REQUIRED', 'Non-EU citizens must obtain a visa before travelling.', 'FRA'),
    ('VISA_REQUIRED', 'Non-EU nationals are required to hold a visa.', 'FRA'),
    ('VISA_REQUIRED', 'Citizens of non-EU countries need a visa.', 'FRA'),
    ('VISA_REQUIRED', 'Les ressortissants hors Union européenne doivent obtenir un visa.', 'FRA'),
    ('VISA_REQUIRED', 'Non-ASEAN nationals must obtain a visa.', 'THA'),
    ('VISA_EXEMPT', 'Nicht-EU-Bürger benötigen kein Visum.', 'FRA'),
    ('VISA_REQUIRED', 'Warga negara bukan ASEAN wajib memiliki visa.', 'THA'),
    ('VISA_REQUIRED', '非东盟国家公民需要签证。', 'THA'),
])
def test_a_negated_group_names_everyone_but_its_members(value, quote, nat):
    from scripts.convert_reviewed_general_batch import _group_named
    assert not _group_named(quote, nat)
    ok, _ = supported(value, [quote], nat)
    assert not ok
    assert _group_named('EU citizens do not need a visa.', 'FRA')


@pytest.mark.parametrize('quote,nat', [
    ('Visa-free entry for Indian nationals has been suspended with effect from 1 July 2026.', 'IND'),
    ('The visa exemption agreement with China has been terminated.', 'CHN'),
    ('La exención de visado para los ciudadanos españoles queda suspendida.', 'ESP'),
    ('日本国民に対する査証免除措置は一時停止されています。', 'JPN'),
    ('대한민국 국민에 대한 사증면제 조치가 중단되었습니다.', 'KOR'),
    ('Việc miễn thị thực cho công dân Việt Nam đã bị tạm dừng.', 'VNM'),
    ('Bebas visa bagi warga negara Indonesia telah dihentikan sementara.', 'IDN'),
    ('การยกเว้นวีซ่าสำหรับคนไทยถูกระงับชั่วคราว', 'THA'),
    ('Безвизовый режим для граждан России приостановлен.', 'RUS'),
    ('Die Visumfreiheit für deutsche Staatsangehörige ist ausgesetzt.', 'DEU'),
    ('Indian nationals were exempt from the visa requirement until the agreement was withdrawn.', 'IND'),
    ('Indian nationals will be exempt from the visa requirement.', 'IND'),
])
def test_a_suspended_terminated_or_dated_out_exemption_is_not_an_exemption(quote, nat):
    from scripts.convert_reviewed_general_batch import _sentence_block, _VERDICT_RULES
    ok, explain = supported('VISA_EXEMPT', [quote], nat)
    # Round five reads the suspension over the whole evidence first; the
    # per-sentence flip is kept and still fires on its own.
    assert not ok and any(e.startswith('flipped in the same sentence') or 'suspended, withdrawn or dated out' in e for e in explain)
    assert _sentence_block(quote, None, 'VISA_EXEMPT', nat, _VERDICT_RULES['VISA_EXEMPT'][1], 'ordinary_passport', 'tourism', False) == 'flipped in the same sentence'


@pytest.mark.parametrize('value,quote,nat', [
    ('VISA_ON_ARRIVAL', 'Visa on arrival for Indian nationals has been suspended until further notice.', 'IND'),
    ('VISA_ON_ARRIVAL', '인도 국민에 대한 도착비자 발급이 중단되었습니다.', 'IND'),
    ('ELECTRONIC_AUTHORIZATION_REQUIRED', 'The K-ETA requirement for Japanese nationals is temporarily suspended.', 'JPN'),
    ('ELECTRONIC_AUTHORIZATION_REQUIRED', '일본 국민의 K-ETA 의무는 일시 중단되었습니다.', 'JPN'),
])
def test_suspension_also_flips_arrival_and_authorisation_verdicts(value, quote, nat):
    ok, _ = supported(value, [quote], nat)
    assert not ok


def test_no_longer_reads_in_its_two_senses():
    ok, _ = supported('VISA_EXEMPT', ['Indian nationals no longer need a visa for stays of up to 30 days.'], 'IND')
    assert ok
    ok, _ = supported('VISA_REQUIRED', ['A visa is no longer required for Indian nationals.'], 'IND')
    assert not ok
    ok, _ = supported('VISA_EXEMPT', ['Indian nationals are no longer visa-exempt.'], 'IND')
    assert not ok


@pytest.mark.parametrize('quote,nat,reason', [
    ('From 1 January 2024 to 31 December 2024, United States nationals are exempt from the visa requirement.', 'USA', 'ended on 2024-12-31'),
    ('中方决定对法国持普通护照人员试行免签政策，实施期限为2023年12月1日至2024年11月30日。', 'FRA', 'ended on 2024-11-30'),
    ('From 1 January 2027, Indian nationals will be exempt from the visa requirement.', 'IND', 'starts on 2027-01-01'),
    ('The visa exemption for Indian nationals applied until 30 June 2025.', 'IND', 'ended on 2025-06-30'),
    ('对法国持普通护照人员的免签政策施行至2025年12月31日。', 'FRA', 'ended on 2025-12-31'),
    ('单方面免签政策延期至2025年12月31日24时。', 'FRA', 'ended on 2025-12-31'),
    ('2027년 1월 1일부터 대한민국 국민은 무비자 입국이 가능합니다.', 'KOR', 'starts on 2027-01-01'),
])
def test_an_expired_or_future_policy_window_proves_nothing_today(quote, nat, reason):
    from scripts.convert_reviewed_general_batch import _window_violation
    assert reason in (_window_violation([quote]) or '')
    ok, explain = supported('VISA_EXEMPT', [quote], nat)
    assert not ok and any(reason in e for e in explain)


def test_a_current_window_must_be_recorded_by_the_reviewer():
    """The three kept China rows quoted a sunset ("延期至2026年12月31日24时",
    "From 00:00 on February 17, 2026 to 24:00 on December 31, 2026") and
    recorded no bound. The end date, and the start of a full interval, must
    be recorded to be served; a past start alone needs no record."""
    from scripts.convert_reviewed_general_batch import _window_violation
    british = ('From 00:00 on February 17, 2026 to 24:00 on December 31, 2026 (Beijing time), ordinary passport holders '
               'from the UK and Canada can be exempted from visa to enter China and stay for up to 30 days.')
    assert 'not recorded as effective_to' in _window_violation([british])
    assert 'not recorded as effective_from' in _window_violation([british], {'effective_to': '2026-12-31'})
    assert _window_violation([british], {'effective_from': '2026-02-17', 'effective_to': '2026-12-31'}) is None
    assert 'not recorded as effective_to' in _window_violation(['为持续便利中外人员往来，中方决定将对法国等国单方面免签政策延期至2026年12月31日24时。'])
    assert 'not recorded as effective_to' in _window_violation(['对其余48国持普通护照人员的免签政策施行至2026年12月31日'])
    assert _window_violation(['e-Visa facility is available for holders of Spanish passports with effect from November 3, 2015.']) is None
    ok, _ = supported('VISA_EXEMPT', [british], 'GBR')
    assert not ok
    ok, _ = supported('VISA_EXEMPT', [british], 'GBR', bounds={'effective_from': '2026-02-17', 'effective_to': '2026-12-31'})
    assert ok
    # End to end: the recorded bound must also be the literal one the page states.
    b = interval_batch()
    b['rows'][0]['verdict']['proof'].pop('effective_to')
    with pytest.raises(PatchRejected, match='policy window until 2028-03-14 is not recorded'):
        validate_batch(b)
    validate_batch(interval_batch())
    b = route_batch('HKG', 'VNM', 'VISA_EXEMPT', 'unconditional_visa_free',
                    'From 1 January 2024 to 31 December 2024, holders of a Hong Kong SAR passport are exempt from the visa requirement.',
                    'From 1 January 2024 to 31 December 2024, holders of a Hong Kong SAR passport are exempt from the visa requirement.', URL, KEY)
    with pytest.raises(PatchRejected, match='policy window ended on 2024-12-31'):
        validate_batch(b)


@pytest.mark.parametrize('notice', [
    'The consular office will be closed from 1 March 2026 to 30 April 2026.',
    'Visas issued from 1 January 2026 are valid until 31 December 2026.',
    'This page was updated on 1 January 2026.',
    'Your passport must be valid until 31 December 2026.',
])
def test_an_office_issue_update_or_passport_date_is_no_policy_window(notice):
    from scripts.convert_reviewed_general_batch import _window_violation
    assert _window_violation([notice]) is None


def test_a_parenthesised_bound_on_another_list_entry_does_not_bound_this_nationality():
    """Round six: the pinned Taiwan review quotes the boca.gov.tw run
    '..., North Macedonia*(effective until March 31, 2030), Norway, ...'
    and records no bound, and the fail-closed window gate refused it. A
    bound in a parenthesis of its own on one entry of a list run is that
    entry's alone. On this nationality's own entry, on a group it belongs
    to, with any other word in the parenthesis, on a quantified entry, on
    the sentence's subject, or read without a nationality, the window
    bounds the verdict and must be recorded."""
    from scripts.convert_reviewed_general_batch import _window_violation
    run = ('Nationals of the following countries are eligible for the visa-exemption program, with a duration of stay of up to 90 days: '
           'Albania, Japan*, Marshall Islands*, New Zealand, North Macedonia*(effective until March 31, 2030), Norway, '
           'United Kingdom*, and United States of America*.')
    assert 'until 2030-03-31 is not recorded as effective_to' in _window_violation([run])
    for nat in ('USA', 'GBR', 'JPN', 'NZL', 'MHL'):
        assert _window_violation([run], nat=nat) is None, nat
    own = ('Nationals of the following countries are eligible for the visa-exemption program: '
           'Albania, United States of America (effective until March 31, 2030), Norway.')
    assert 'until 2030-03-31 is not recorded as effective_to' in _window_violation([own], nat='USA')
    assert _window_violation([own], {'effective_to': '2030-03-31'}, nat='USA') is None
    assert _window_violation([own], nat='GBR') is None
    ranged = 'Visa-free entry applies to: Albania, North Macedonia (from 1 April 2026 to 31 March 2030), Norway, United States.'
    assert _window_violation([ranged], nat='USA') is None
    assert 'from 2026-04-01 is not recorded as effective_from' in _window_violation([ranged], {'effective_to': '2030-03-31'})
    group = ('Nationals of the following countries are eligible for the visa-exemption program: '
             'Albania, EU member states (effective until March 31, 2030), Norway.')
    assert 'until 2030-03-31 is not recorded as effective_to' in _window_violation([group], nat='FRA')
    for sentence, nat in (
        ('Nationals of the following countries are eligible for the visa-exemption program: '
         'Albania, North Macedonia (effective until March 31, 2030 for all listed countries), Norway.', 'USA'),
        ('Nationals of the following countries are eligible for the visa-exemption program: '
         'Albania, all of the above (effective until March 31, 2030), Norway.', 'USA'),
        ('Nationals of the following countries are eligible for the visa-exemption program: '
         'Albania, other countries listed (until March 31, 2030), Norway.', 'USA'),
        ('The visa-exemption program (effective until March 31, 2030) covers Albania, Norway and the United States.', 'USA'),
        ('Nationals of the Kingdom of Thailand (effective until July 31, 2027), except those holding diplomatic or '
         'official/service passports, are eligible for the visa-exemption program.', 'THA'),
    ):
        assert 'is not recorded as effective_to' in (_window_violation([sentence], nat=nat) or ''), sentence
    # A clause that names another nationality but speaks of everyone is not
    # that nationality's alone; a clause that names only another is.
    everyone = 'The visa waiver programme agreed with Japan applies to all listed nationals until 31 December 2026.'
    assert 'until 2026-12-31 is not recorded as effective_to' in _window_violation([everyone], nat='USA')
    theirs = 'The visa-free policy runs for Russian passport holders until 31 December 2027, for the other 48 countries until 31 December 2026.'
    assert 'until 2026-12-31 is not recorded as effective_to' in _window_violation([theirs], nat='JPN')
    assert _window_violation([theirs], {'effective_to': '2026-12-31'}, nat='JPN') is None
    # The decision reads the same way: the run proves the USA row with no
    # bound recorded, the bound on its own entry still has to be.
    ok, explain = supported('VISA_EXEMPT', [run], 'USA')
    assert ok, explain
    ok, explain = supported('VISA_EXEMPT', [own], 'USA')
    assert not ok and any('until 2030-03-31 is not recorded as effective_to' in e for e in explain), explain


HEADING_RULE = 'Nationals of the following countries must obtain a visa before travelling to Freedonia.'
HEADING_FREE_LINE = 'France, Spain, Japan, United States, United Kingdom, Singapore, Canada, Australia'


@pytest.mark.parametrize('heading', [
    'Schedule 2', 'Annex II', 'Part II', 'Category B', 'List B', 'Group II', 'Second Schedule',
    'Countries whose nationals may enter freely', 'Freedom of movement list',
    'Visa waiver countries', 'Visa Waiver Program', 'ETA countries', 'Visa nationals',
])
def test_a_section_heading_by_shape_or_vocabulary_ends_the_rule_sentences_reach(heading):
    from scripts.convert_reviewed_general_batch import _headings, _page_index
    page = HEADING_RULE + '\nAfghanistan, Bangladesh, India, Pakistan\n' + heading + '\n' + HEADING_FREE_LINE + '\n'
    assert heading.casefold() in [h.strip() for h in _headings(_page_index(page)[0])[1]]
    pages = [('p', page)] * 2
    for nat in ('FRA', 'ESP', 'JPN', 'USA', 'GBR', 'SGP', 'CAN', 'AUS'):
        ok, explain = supported('VISA_REQUIRED', [HEADING_FREE_LINE, HEADING_RULE], nat, pages=pages)
        assert not ok and any(e.startswith('list line on another page or under another heading') for e in explain)
    ok, _ = supported('VISA_REQUIRED', ['Afghanistan, Bangladesh, India, Pakistan', HEADING_RULE], 'IND', pages=pages)
    assert ok


def test_a_heading_between_entry_lines_needs_list_vocabulary_but_a_comma_run_needs_none():
    from scripts.convert_reviewed_general_batch import _headings, _page_index
    lines = HEADING_RULE + '\nAfghanistan\nBangladesh\nIndia\nAnnex II\nFrance\nSpain\nJapan\n'
    assert [h.strip() for h in _headings(_page_index(lines)[0])[1]] == [HEADING_RULE.rstrip('.').casefold(), 'annex ii']
    ok, _ = supported('VISA_REQUIRED', ['Japan', HEADING_RULE], 'JPN', pages=[('p', lines)] * 2)
    assert not ok
    ok, _ = supported('VISA_REQUIRED', ['India', HEADING_RULE], 'IND', pages=[('p', lines)] * 2)
    assert ok
    # A country line is never a heading by shape, and a region sub-heading does not cut its list.
    regions = 'Nationals of the following countries do not need a visa:\nEurope\nFrance, Germany\nAsia\nJapan, Singapore\n'
    assert [h.strip() for h in _headings(_page_index(regions)[0])[1]] == ['nationals of the following countries do not need a visa:']
    ok, _ = supported('VISA_EXEMPT', ['Japan, Singapore', 'Nationals of the following countries do not need a visa:'], 'JPN', pages=[('p', regions)] * 2)
    assert ok


def test_heading_vocabulary_states_a_verdict_and_a_restating_heading_does_not_cut():
    from scripts.convert_reviewed_general_batch import _states_other_verdict, _boundary
    for heading in ('Visa waiver countries', 'Visa Waiver Program', 'ETA countries', 'Visa-free countries'):
        assert _states_other_verdict(heading, 'VISA_REQUIRED'), heading
    assert _states_other_verdict('Visa nationals', 'VISA_EXEMPT')
    assert _states_other_verdict('ETA countries', 'VISA_EXEMPT')
    assert not _states_other_verdict('Visa required (Asia)', 'VISA_REQUIRED')
    assert _boundary('Visa nationals', 'VISA_REQUIRED')
    assert not _boundary('Visa required (Asia)', 'VISA_REQUIRED')
    assert not _boundary('Nationals of the following countries also require a visa:', 'VISA_REQUIRED')
    assert _boundary('Annex II', 'VISA_REQUIRED')


@pytest.mark.parametrize('quote,nat', [
    ('Holders of diplomatic and official passports of India are exempt from the visa requirement.', 'IND'),
    ('Indian nationals holding diplomatic or service passports do not require a visa.', 'IND'),
    ('中国外交护照持有人免签。', 'CHN'),
    ('Visa-free entry applies to holders of United States diplomatic passports.', 'USA'),
    ('Les titulaires de passeports diplomatiques français sont dispensés de visa.', 'FRA'),
    ('Người mang hộ chiếu ngoại giao Việt Nam được miễn thị thực.', 'VNM'),
    ('Pemegang paspor diplomatik Indonesia bebas visa.', 'IDN'),
    ('ผู้ถือหนังสือเดินทางทูตไทยได้รับยกเว้นวีซ่า', 'THA'),
    ('대한민국 외교관 여권 소지자는 무비자입니다.', 'KOR'),
    ('British National (Overseas) passport holders do not require a visa.', 'GBR'),
])
def test_a_rule_for_another_passport_class_does_not_prove_the_ordinary_route(quote, nat):
    ok, _ = supported('VISA_EXEMPT', [quote], nat, document_type='ordinary_passport')
    assert not ok


def test_the_passport_class_gate_is_symmetric_and_reads_a_sentence_naming_both():
    ok, explain = supported('VISA_EXEMPT', ['Holders of diplomatic and official passports of India are exempt from the visa requirement.'],
                            'IND', document_type='diplomatic_passport')
    assert ok
    ok, explain = supported('VISA_EXEMPT', ['Ordinary passport holders of India are exempted from visa up to 90 days.'], 'IND',
                            document_type='official_passport')
    assert not ok and any('scoped to ordinary passports' in e for e in explain)
    ok, _ = supported('VISA_EXEMPT', ['Citizens holding a national passport (diplomatic, official, or ordinary) of the United States do not need a visa for visiting South Africa.'], 'USA')
    assert ok
    ok, _ = supported('VISA_EXEMPT', ['Citizens of the United States, holders of all types of passports, do not need visas for the entry.'], 'USA')
    assert ok
    ok, _ = supported('VISA_REQUIRED', ['All Indian nationals must obtain a visa through the Korean diplomatic mission.'], 'IND')
    assert ok
    # The mfa.gov.tr label: the route's own class is the half that decides.
    label = 'Canada: Official passport holders are required to have visa to enter Türkiye. Ordinary passport holders are exempted from visa up to 90 days in any 180-day period.'
    ok, _ = supported('VISA_EXEMPT', [label], 'CAN')
    assert ok
    ok, _ = supported('VISA_EXEMPT', [label], 'CAN', document_type='official_passport')
    assert not ok


@pytest.mark.parametrize('quote,nat', [
    ('Indian nationals in direct transit through the international airport do not require a visa.', 'IND'),
    ('Chinese citizens are eligible for the 144-hour visa-free transit policy.', 'CHN'),
    ('日本国民は通過の場合、ビザ免除となります。', 'JPN'),
])
def test_a_transit_scoped_exemption_proves_only_a_transit_route(quote, nat):
    ok, explain = supported('VISA_EXEMPT', [quote], nat, purpose='tourism')
    assert not ok and any(e.startswith('scoped to transit') for e in explain)
    ok, _ = supported('VISA_EXEMPT', [quote], nat, purpose='transit')
    assert ok


def test_transit_beside_entry_or_tourism_is_not_a_transit_scope():
    ok, _ = supported('VISA_EXEMPT', ['The holders of passports issued in the Special Administrative Region of the People\'s Republic of China: Hong Kong and Macau are exempted from visa regime when entering, exiting and transiting through the territory of Bosnia and Herzegovina, up to 90 days.'], 'HKG')
    assert ok
    ok, _ = supported('VISA_EXEMPT', ['上述国家持普通护照人员来华经商、旅游观光、探亲访友、交流访问、过境不超过30天，可免办签证入境。', '法国、德国'], 'FRA')
    assert ok


@pytest.mark.parametrize('line,nat', [
    ('Japan (diplomatic passports only)', 'JPN'),
    ('United Kingdom (BN(O) only)', 'GBR'),
    ('India (service passports)', 'IND'),
])
def test_an_annotated_list_line_for_another_passport_class_is_no_list_line(line, nat):
    from scripts.convert_reviewed_general_batch import _list_line
    assert not _list_line(line, nat)
    assert _list_line(line.split(' (')[0], nat)


@pytest.mark.parametrize('quote,nat', [
    ('Indian nationals holding a valid United States visa do not require a visa.', 'IND'),
    ('Chinese nationals who are permanent residents of Hong Kong do not require a visa.', 'CHN'),
    ('Nationals who hold a residence permit of a Schengen State (for example Indian nationals resident in Germany) do not require a visa.', 'IND'),
    ('Indian nationals who are crew members of an aircraft do not require a visa.', 'IND'),
    ('Indian nationals do not require a visa provided they hold a return ticket.', 'IND'),
    ('Holders of biometric Indian passports do not require a visa.', 'IND'),
])
def test_a_conditional_sentence_cannot_prove_an_unconditional_verdict(quote, nat):
    ok, explain = supported('VISA_EXEMPT', [quote], nat, detail='unconditional_visa_free')
    assert not ok and any(e.startswith(('conditional wording', 'nationality named only as an example')) for e in explain)
    ok, _ = supported('VISA_EXEMPT', [quote], nat, detail='conditional_visa_free')
    assert ok


def test_every_item_of_an_example_run_is_an_example():
    """Round six: once "e.g." stopped splitting the sentence, the Monaco EES
    page's 'All third-country nationals exempt from a short-stay visa (e.g.
    UK citizens, Americans, Australians, Canadians, Japanese, etc.)' proved
    the unconditional USA route, because the example gate read only the
    first item after the marker. Every item of the run the marker opens is
    an example; a mention after the run closes, or before the marker, is
    not."""
    from scripts.convert_reviewed_general_batch import _example_only
    monaco = 'All third-country nationals exempt from a short-stay visa (e.g. UK citizens, Americans, Australians, Canadians, Japanese, etc.).'
    for nat in ('GBR', 'USA', 'AUS', 'CAN', 'JPN'):
        assert _example_only(monaco, nat), nat
        ok, explain = supported('VISA_EXEMPT', [monaco], nat, detail='unconditional_visa_free')
        assert not ok and any(e.startswith('nationality named only as an example') for e in explain), (nat, explain)
    such_as = 'Nationals of some countries, such as the United States, Canada and Australia, are exempt from the visa requirement.'
    for nat in ('USA', 'CAN', 'AUS'):
        assert _example_only(such_as, nat), nat
    for sentence, nat in (('Some documents (e.g. a residence permit) let Japanese nationals enter without a visa.', 'JPN'),
                          ('Japanese nationals do not need a visa (e.g. for tourism).', 'JPN'),
                          ('Nationals of the following countries, e.g. those of Japan, need no visa: Japan, Korea, India.', 'IND')):
        assert not _example_only(sentence, nat), sentence


def test_a_passport_holder_subject_and_a_purpose_example_are_no_condition():
    for quote, nat in (('Holders of a Hong Kong SAR passport do not require a visa for stays of up to 30 days.', 'HKG'),
                       ('Canadian passport holders do not require a visa for the entry purpose of a short term visit (e.g Tourism, Visiting friends or relatives) up to 180 days.', 'CAN'),
                       ('Under the amended Compact of Free Association, U.S. citizens need only valid passport for entry.', 'USA'),
                       ('Singaporean citizens, who hold the appropriate passports, do not need to apply for visas in advance when traveling to China for short terms.', 'SGP')):
        ok, explain = supported('VISA_EXEMPT', [quote], nat, detail='unconditional_visa_free')
        assert ok, (quote, explain)


def test_the_uruguay_cell_serves_no_unconditional_exemption():
    """HKG|HKG|URY: the gub.uy cell says "No necesita Visa (solo Pte. HKSAR)
    (4)" and footnote (4) states the condition. The unconditional
    subcategory is refused, the conditional one reads."""
    url = 'https://www.gub.uy/ministerio-interior/comunicacion/publicaciones/regimen-visas'
    page = ('Régimen de visas\nPaís | Visa\nHong Kong\nNo necesita Visa (solo Pte. HKSAR) (4)\nHungría\nNo necesita Visa\n'
            '(4) Si en el pasaporte no luce la sigla "HKSAR" u "OVERSEAS" requerirá visa.\n')
    b = route_batch('HKG', 'URY', 'VISA_EXEMPT', 'unconditional_visa_free', page, 'Hong Kong', url, 'HKG|HKG|URY|tourism|default|unknown|v6')
    b['rows'][0]['verdict']['proof']['evidence'].append({'source_id': 's1', 'source_url': url, 'quote': 'No necesita Visa (solo Pte. HKSAR) (4)'})
    with pytest.raises(PatchRejected, match='does not state this verdict'):
        validate_batch(b)
    b['rows'][0]['verdict']['requirement_detail'] = 'conditional_visa_free'
    validate_batch(b)


def test_a_footnote_on_the_nationalitys_own_list_line_refuses_the_unconditional_subcategory():
    from scripts.convert_reviewed_general_batch import _footnote_marked
    line = 'Slovakia, Slovenia, Spain, Sweden, Switzerland, Tuvalu*, United Kingdom*, and United States of America*.'
    assert _footnote_marked(line, 'USA') and _footnote_marked(line, 'GBR') and not _footnote_marked(line, 'ESP')
    assert _footnote_marked('Japan (1)', 'JPN') and not _footnote_marked('Visa is not required for a stay of less than one (1) month for ASEAN nationals except Myanmar.', 'THA')
    rule = 'Nationals of the following countries are eligible for the visa-exemption program, with a duration of stay of up to 90 days:'
    page = rule + '\n' + line + '\n'
    ok, explain = supported('VISA_EXEMPT', [line, rule], 'USA', pages=[('p', page)] * 2, detail='unconditional_visa_free')
    assert not ok and any(e.startswith('footnote on the list line') for e in explain)
    ok, _ = supported('VISA_EXEMPT', [line, rule], 'USA', pages=[('p', page)] * 2, detail='conditional_visa_free')
    assert ok
    ok, _ = supported('VISA_EXEMPT', [line, rule], 'ESP', pages=[('p', page)] * 2, detail='unconditional_visa_free')
    assert ok


@pytest.mark.parametrize('quote,nat', [
    ('British National (Overseas) passport holders do not require a visa.', 'GBR'),
    ('Holders of Bermuda passports issued by the United Kingdom do not require a visa.', 'GBR'),
    ('Holders of Dutch Caribbean identity cards do not require a visa.', 'NLD'),
])
def test_a_bracketed_or_document_phrase_dependency_does_not_name_the_parent(quote, nat):
    from scripts.convert_reviewed_general_batch import _named, _list_line
    assert not _named(quote, nat)
    ok, _ = supported('VISA_EXEMPT', [quote], nat)
    assert not ok
    assert not _list_line('United Kingdom (BN(O) only)', 'GBR')
    assert _named('Holders of passports issued by the United Kingdom do not require a visa.', 'GBR')


def test_a_currency_or_product_mention_carries_no_subject_forward():
    from scripts.convert_reviewed_general_batch import _label, _named
    quote = 'Fees are payable in cash (Indian rupees are not accepted). A visa is required for all visitors.'
    assert _label(quote, 'IND') is None
    assert not _named('Indian rupees are not accepted', 'IND')
    ok, _ = supported('VISA_REQUIRED', [quote], 'IND')
    assert not ok
    for quote, nat in (('Korean won is not accepted. A visa is required for all visitors.', 'KOR'),
                       ('Thai cuisine is served on board. A visa is required for all visitors.', 'THA'),
                       ('Please contact the Indian Embassy. A visa is required for all visitors.', 'IND'),
                       ('Chinese yuan: not accepted. A visa is required for all visitors.', 'CHN')):
        ok, _ = supported('VISA_REQUIRED', [quote], nat)
        assert not ok, quote
    ok, _ = supported('VISA_REQUIRED', ['Indian nationals: a visa is required before travel.'], 'IND')
    assert ok
    assert _named('俄罗斯、瑞典、加拿大、英国50国持普通护照人员来华可免签入境。', 'GBR')


@pytest.mark.parametrize('quote,nat', [
    ('Indian nationals: the ETA portal is necessary reading before you travel.', 'IND'),
    ('한국 국민은 K-ETA 없이도 입국할 수 있으나 필요 시 사전등록을 권장합니다.', 'KOR'),
    ('Canadian citizens must carry a valid passport, and the ETA website has more information.', 'CAN'),
    ('Les ressortissants indiens peuvent entrer sans ETA mais doivent présenter un passeport.', 'IND'),
])
def test_an_authorisation_token_outside_the_requirement_clause_states_no_requirement(quote, nat):
    ok, _ = supported('ELECTRONIC_AUTHORIZATION_REQUIRED', [quote], nat)
    assert not ok


def test_the_group_table_check_is_an_explicit_raise_on_the_compiled_pattern(monkeypatch):
    import inspect
    import scripts.convert_reviewed_general_batch as c
    source = inspect.getsource(c)
    assert 'assert _EXTRA_ALIASES' not in source
    assert '\n_check_group_names()\n' in source
    c._check_group_names()
    monkeypatch.setitem(c._EXTRA_ALIASES, 'MLT', ('',))
    monkeypatch.setitem(c._DEMONYM_STEMS, 'MLT', ())
    with pytest.raises(PatchRejected, match='group member without a name pattern: MLT'):
        c._check_group_names()
    monkeypatch.delitem(c._EXTRA_ALIASES, 'MLT')
    with pytest.raises(PatchRejected, match='MLT'):
        c._check_group_names()
    # The check also runs under python -O: compiled with asserts stripped, a
    # module whose Malta names and stems are both empty still refuses the
    # table, while the real table imports cleanly.
    nameless = (source.replace("'MLT': ('malta', 'malte'),", "'MLT': ('',),")
                .replace("'MLT': ('maltese', 'maltais', 'maltesisch', 'maltés', 'maltes'),", "'MLT': (),"))
    assert nameless != source and "'MLT': (),\n" in nameless
    namespace = {'__name__': 'scripts._round_three_probe', '__file__': c.__file__}
    with pytest.raises(PatchRejected, match='MLT'):
        exec(compile(nameless, c.__file__, 'exec', optimize=1), namespace)
    exec(compile(source, c.__file__, 'exec', optimize=1), dict(namespace))


def test_a_country_name_inside_navigation_boilerplate_is_no_list_line():
    """kdmid.ru renders every country of its site index between the same
    three link texts; the JPN|JPN|RUS row quoted "Япония" from there."""
    rule = 'Информация о визовом/безвизовом режиме поездок: граждане следующих государств должны иметь визу.'
    triad = 'информация о стране\nконсульские учреждения россии\nнормативная база двусторонних консульских отношений\n'
    page = rule + '\n' + 'Ямайка\n' + triad + 'Япония\n' + triad + 'Йемен\n' + triad
    ok, explain = supported('VISA_REQUIRED', ['Япония', rule], 'JPN', pages=[('p', page)] * 2)
    assert not ok and any(e.startswith('list line sits in navigation boilerplate') for e in explain)
    plain = rule + '\nЯмайка\nЯпония\nЙемен\n'
    ok, _ = supported('VISA_REQUIRED', ['Япония', rule], 'JPN', pages=[('p', plain)] * 2)
    assert ok
    # A verdict or stay cell repeating down a table column is not boilerplate.
    table = 'Nationals of the following countries do not need a visa:\nJamaica | 90 days | Not required\nJapan | 90 days | Not required\nJordan | 90 days | Not required\n'
    ok, _ = supported('VISA_EXEMPT', ['Japan', 'Nationals of the following countries do not need a visa:'], 'JPN', pages=[('p', table)] * 2)
    assert ok


# Round-five review regressions. Every sentence below is one the review
# executed against the converter at b898ecc and found proving the wrong
# verdict. The gates now fail closed: an exception, suspension, exclusion,
# window or date the converter cannot resolve refuses the row.

def test_a_suspension_in_an_adjacent_sentence_of_the_evidence_blocks_the_verdict():
    """Round five, blocking 1: the suspension vocabulary was scoped to the
    deciding sentence. It now runs over every sentence of the evidence and
    of the captured page section the quotes sit in."""
    rule = 'Nationals of the countries and territories listed below are exempt from the visa requirement for stays of up to 90 days.'
    note = 'Note: the visa exemption for Japan has been suspended until further notice.'
    page = rule + '\nJapan\nKorea\nSingapore\n' + note + '\n'
    # End to end through _check_proof, all three lines quoted.
    b = route_batch('JPN', 'VNM', 'VISA_EXEMPT', 'unconditional_visa_free', page, rule, URL, 'JPN|JPN|VNM|tourism|default|unknown|v6')
    b['rows'][0]['verdict']['proof']['evidence'] += [{'source_id': 's1', 'source_url': URL, 'quote': 'Japan'},
                                                    {'source_id': 's1', 'source_url': URL, 'quote': note}]
    with pytest.raises(PatchRejected, match='does not state this verdict'):
        validate_batch(b)
    for quotes in (['Japanese nationals are exempt from the visa requirement.', 'The visa exemption has been suspended.'],
                   ['Japanese nationals are exempt from the visa requirement. This measure has been suspended since 1 April 2026.'],
                   ['Visa exemption for Japanese nationals is suspended. Japanese nationals are exempt from the visa requirement for stays of up to 90 days.']):
        ok, explain = supported('VISA_EXEMPT', quotes, 'JPN')
        assert not ok and any('suspended, withdrawn or dated out in the evidence' in e for e in explain), explain
    # The unquoted note bounding the same page section refuses too, for every
    # nationality listed there: a section that suspends a listed exemption
    # cannot serve any of its lines. Japan itself also occurs in the note, so
    # its line is ambiguous on the page as well.
    ok, explain = supported('VISA_EXEMPT', [rule, 'Korea'], 'KOR', pages=[('p', page)] * 2)
    assert not ok and any('suspended, withdrawn or dated out in the same page section' in e for e in explain), explain
    ok, _ = supported('VISA_EXEMPT', [rule, 'Japan'], 'JPN', pages=[('p', page)] * 2)
    assert not ok
    # Without the note the list still proves.
    plain = rule + '\nJapan\nKorea\nSingapore\n'
    ok, _ = supported('VISA_EXEMPT', [rule, 'Japan'], 'JPN', pages=[('p', plain)] * 2)
    assert ok


def test_a_prose_carve_out_in_an_adjacent_sentence_blocks_the_excepted_nationality():
    """Round five, blocking 2: a carve-out written as prose before or after
    the rule sentence excludes the nationality it names; the other members
    are still read."""
    cases = [
        (['ASEAN nationals do not require a visa for stays of up to one month.', 'Myanmar nationals are not covered by this arrangement.'], 'MMR'),
        (['EU citizens do not need a visa.', 'Bulgarian nationals remain subject to the visa requirement.'], 'BGR'),
        (['ASEAN nationals are exempt from the visa requirement. This does not apply to Myanmar.'], 'MMR'),
        (['Myanmar is excluded from the arrangement. ASEAN nationals do not require a visa.'], 'MMR'),
    ]
    for quotes, nat in cases:
        ok, explain = supported('VISA_EXEMPT', quotes, nat)
        assert not ok and any('excluded from the rule' in e or 'opposite verdict is stated' in e for e in explain), (quotes, explain)
    ok, explain = supported('VISA_EXEMPT', cases[0][0], 'THA')
    assert ok and explain[-1].startswith('group membership')
    ok, explain = supported('VISA_EXEMPT', cases[1][0], 'ESP')
    assert ok and explain[-1].startswith('group membership')


@pytest.mark.parametrize('phrase', ['with the sole exception of', 'with the single exception of', 'but not', 'save in the case of', 'saving',
                                    'bar', 'less', 'to the exclusion of', 'not including', 'with the exclusion of'])
def test_an_exception_construction_outside_the_old_list_still_carves_out_and_unless_flips_a_requirement(phrase):
    """Round five, blocking 3: the exception opener is read as a
    construction, so a wording outside the enumeration carves the named
    nationality out and still lets the other members through."""
    from scripts.convert_reviewed_general_batch import _carved_out
    quote = f'EU citizens do not need a visa, {phrase} Bulgarian nationals.'
    assert _carved_out(quote, 'BGR')
    ok, explain = supported('VISA_EXEMPT', [quote], 'BGR')
    assert not ok and any('carved out by name' in e for e in explain)
    ok, explain = supported('VISA_EXEMPT', [quote], 'ESP')
    assert ok and explain[-1].startswith('group membership')
    ok, explain = supported('VISA_REQUIRED', ['All travellers must obtain a visa unless they are Japanese nationals.'], 'JPN')
    assert not ok


@pytest.mark.parametrize('quote,nat,ended', [
    ('日本国民に対する査証免除は令和6年12月31日まで有効です。', 'JPN', '2024-12-31'),
    ('การยกเว้นวีซ่าสำหรับคนไทยมีผลจนถึงวันที่ 31 ธันวาคม 2567', 'THA', '2024-12-31'),
    ('Bebas visa bagi warga negara Indonesia berlaku hingga 31 Desember 2024.', 'IDN', '2024-12-31'),
    ('Безвизовый режим для граждан России действует до 31 декабря 2024 года.', 'RUS', '2024-12-31'),
    ('Chính sách miễn thị thực cho công dân Việt Nam có hiệu lực đến ngày 31 tháng 12 năm 2024.', 'VNM', '2024-12-31'),
    ('The visa exemption for Chinese nationals is valid until 2024/12/31.', 'CHN', '2024-12-31'),
    ('中方对法国持普通护照人员的免签政策实施期限为2023/12/01至2024/11/30。', 'FRA', '2024-11-30'),
])
def test_policy_windows_in_the_destination_date_shapes_are_read_and_refuse_when_expired(quote, nat, ended):
    """Round five, blocking 4(a): Thai Buddhist-era, Japanese era, Indonesian,
    Russian, Vietnamese and Y/M/D dates parse, so an expired window in any
    of them proves nothing today."""
    from scripts.convert_reviewed_general_batch import _dates_in, _norm, _window_violation
    assert ended in [d.isoformat() for _, _, d in _dates_in(_norm(quote))]
    assert f'ended on {ended}' in (_window_violation([quote]) or '')
    ok, explain = supported('VISA_EXEMPT', [quote], nat)
    assert not ok and any(f'ended on {ended}' in e for e in explain)


def test_an_unquoted_sunset_on_the_captured_page_section_and_an_unreadable_date_refuse_the_verdict():
    """Round five, blocking 4(b): the cs.mfa.gov.cn sunset the JPN, KOR and
    GBR|transit rows never quoted ('对其余48国持普通护照人员的免签政策施行至2026年12月31日')
    is read from the page section and must be recorded, with the quote, as
    effective_to; a policy sentence whose date the parser cannot read
    refuses outright."""
    from scripts.convert_reviewed_general_batch import _unreadable_date, _norm
    rule = '上述国家持普通护照人员来华经商、旅游观光、探亲访友、交流访问、过境不超过30天，可免办签证入境。'
    sunset = '对其余48国持普通护照人员的免签政策施行至2026年12月31日。'
    page = '为进一步便利中外人员往来，中方决定扩大免签国家范围。\n法国、德国、日本、韩国\n' + rule + '\n' + sunset + '\n'
    url = 'https://cs.mfa.gov.cn/gyls/lsgz/fwxx/202511/t20251110_11749824.shtml'
    ok, explain = supported('VISA_EXEMPT', [rule, '法国、德国、日本、韩国'], 'JPN', pages=[('p', page)] * 2)
    assert not ok and any('a policy window on the captured page until 2026-12-31 is not recorded as effective_to' in e for e in explain), explain
    # The real cs.mfa.gov.cn answer states Russia's window and the 48-country
    # window in one sentence: the Russian clause is Russia's, the other
    # clause still expires Japan's verdict.
    qa = ('答：目前，中方对文莱持普通护照人员的免签政策未设施行期限，对俄罗斯持普通护照人员的免签政策施行至2027年12月31日，'
          '对其余48国持普通护照人员的免签政策施行至2026年12月31日。')
    ok, explain = supported('VISA_EXEMPT', [rule, '法国、德国、日本、韩国'], 'JPN', pages=[('p', page + qa + '\n')] * 2)
    assert not ok and any('until 2026-12-31 is not recorded as effective_to' in e for e in explain), explain
    # End to end: refused without the bound, served only with the sunset
    # quoted and recorded as effective_to.
    b = route_batch('JPN', 'CHN', 'VISA_EXEMPT', 'unconditional_visa_free', page, rule, url, 'JPN|JPN|CHN|tourism|default|unknown|v6')
    b['rows'][0]['verdict']['proof']['evidence'].append({'source_id': 's1', 'source_url': url, 'quote': '法国、德国、日本、韩国'})
    with pytest.raises(PatchRejected, match='until 2026-12-31 is not recorded as effective_to'):
        validate_batch(b)
    b['rows'][0]['verdict']['proof']['effective_to'] = '2026-12-31'
    with pytest.raises(PatchRejected, match='Policy bound has no literal destination-source date'):
        validate_batch(b)
    b['rows'][0]['verdict']['proof']['evidence'].append({'source_id': 's1', 'source_url': url, 'quote': sunset})
    validate_batch(b)
    assert b['rows'][0]['verdict']['proof']['effective_to'] == '2026-12-31'
    # A date the parser cannot read in a rule sentence.
    undated = 'The visa exemption for Indian nationals is valid until December 2026.'
    assert _unreadable_date(_norm(undated)) == 'december 2026'
    ok, explain = supported('VISA_EXEMPT', [undated], 'IND')
    assert not ok and any('cannot be read: december 2026' in e for e in explain)
    ok, _ = supported('VISA_EXEMPT', ['Under Regulation (EU) 2019/592, United Kingdom nationals have been exempt from the visa requirement since 1 January 2021.'], 'GBR')
    assert ok


@pytest.mark.parametrize('quote,nat,scope', [
    ('Indian nationals travelling for business purposes do not require a visa.', 'IND', 'business'),
    ('Indian nationals coming for medical treatment do not require a visa.', 'IND', 'medical'),
    ('Chinese nationals enrolled in a degree programme do not require a visa.', 'CHN', 'study'),
    ('Indian nationals travelling on official government missions do not require a visa.', 'IND', 'official'),
])
def test_a_rule_scoped_to_another_purpose_does_not_prove_the_tourism_route(quote, nat, scope):
    """Round five, blocking 5: a verdict proves only the purposes the
    sentence states; tourism needs tourism, visitor or general wording."""
    ok, explain = supported('VISA_EXEMPT', [quote], nat, purpose='tourism')
    assert not ok and any(e.startswith('scoped to ' + scope) for e in explain), explain
    ok, _ = supported('VISA_EXEMPT', [quote], nat, purpose=scope)
    assert ok
    ok, _ = supported('VISA_EXEMPT', ['Indian nationals do not require a visa for stays of up to 30 days.'], 'IND', purpose='tourism')
    assert ok
    ok, _ = supported('VISA_EXEMPT', ['Indian nationals visiting for tourism or business do not require a visa.'], 'IND', purpose='business')
    assert ok


@pytest.mark.parametrize('quote,nat', [
    ('Chinese citizens are eligible for visa-free entry under the 144-hour transit policy.', 'CHN'),
    ('Indian nationals in transit may enter without a visa for up to 24 hours.', 'IND'),
    ('Japanese nationals qualify for transit without visa entry at the airport.', 'JPN'),
    ('Indian nationals in direct transit through the airport do not require a visa.', 'IND'),
])
def test_the_transit_gate_is_not_satisfied_by_an_entry_word(quote, nat):
    """Round five, blocking 6: entering is the transit itself, not a purpose,
    so a transit rule that says 'entry' still proves only a transit route."""
    ok, explain = supported('VISA_EXEMPT', [quote], nat, purpose='tourism')
    assert not ok and any(e.startswith('scoped to transit') for e in explain), explain
    ok, _ = supported('VISA_EXEMPT', [quote], nat, purpose='transit')
    assert ok


@pytest.mark.parametrize('layout', ['Visa-free countries.', 'Annex II.', 'Visa-free countries|', 'Visa-free countries |'])
def test_a_heading_closed_by_a_period_or_a_pipe_is_a_section_boundary(layout):
    """Round five, blocking 7: a heading is decided by the shape of its line,
    so 'Visa-free countries.' and 'Annex II.' (trailing period) and a
    heading cell closed by a pipe cut the visa-required rule's reach."""
    from scripts.convert_reviewed_general_batch import _headings, _page_index
    if layout.endswith(' |'):
        page = HEADING_RULE + '\nAfghanistan, Bangladesh, India, Pakistan\n' + layout + ' ' + HEADING_FREE_LINE + '\n'
    else:
        page = HEADING_RULE + '\nAfghanistan, Bangladesh, India, Pakistan\n' + layout + '\n' + HEADING_FREE_LINE + '\n'
    assert layout.rstrip('.| ').casefold() in [h.strip() for h in _headings(_page_index(page)[0])[1]]
    for nat in ('FRA', 'ESP', 'JPN', 'USA'):
        ok, explain = supported('VISA_REQUIRED', [HEADING_FREE_LINE, HEADING_RULE], nat, pages=[('p', page)] * 2)
        # Cut by the heading, or, when the heading cell and the list share
        # one line, refused because that line states the opposite verdict.
        assert not ok and any(e.startswith('list line on another page or under another heading') or 'opposite verdict is stated' in e
                              for e in explain), (layout, nat, explain)
    ok, _ = supported('VISA_REQUIRED', ['Afghanistan, Bangladesh, India, Pakistan', HEADING_RULE], 'IND', pages=[('p', page)] * 2)
    assert ok


@pytest.mark.parametrize('quote', [
    'Japan is not on the visa-free list.',
    'Japan has been removed from the visa-free list.',
    'The visa exemption does not extend to Japanese nationals.',
    'Japanese nationals are excluded from the visa-free scheme.',
    'The visa-free arrangement with Japan is under review and does not currently apply.',
    'Japan was struck from the visa waiver list.',
])
def test_a_negation_or_removal_predicate_on_the_verdict_noun_refuses_the_exemption(quote):
    """Round five, blocking 8: negation and removal wording outside the flip
    lists is read as an exclusion applied to the verdict noun."""
    ok, explain = supported('VISA_EXEMPT', [quote], 'JPN')
    assert not ok and any('excluded from the rule' in e or 'negated or removed' in e for e in explain), explain
    # An exclusion from another verdict's noun is not an exclusion from this one.
    ok, explain = supported('VISA_REQUIRED', ['India is not eligible for K-ETA.', 'All Indian nationals must obtain a visa through the Korean diplomatic mission.'], 'IND')
    assert ok


@pytest.mark.parametrize('value,quote,nat', [
    ('VISA_REQUIRED', 'Kenyan citizens travelling to Japan must obtain a visa prior to departure.', 'JPN'),
    ('VISA_EXEMPT', 'Kenyan citizens do not require a visa for Japan.', 'JPN'),
    ('VISA_EXEMPT', 'Our nationals are exempt from the visa requirement when travelling to India.', 'IND'),
    ('VISA_EXEMPT', 'Holders of our passports do not need a visa to visit the Republic of Korea.', 'KOR'),
])
def test_a_country_named_as_the_destination_never_proves_a_verdict_for_its_own_nationals(value, quote, nat):
    """Round five, blocking 9: the mention must occupy a traveller role."""
    from scripts.convert_reviewed_general_batch import _named, _named_as_traveller
    assert _named(quote, nat) and not _named_as_traveller(quote, nat)
    ok, explain = supported(value, [quote], nat)
    assert not ok and any('does not name the nationality' in e or 'never named' in e for e in explain), explain
    ok, _ = supported('VISA_REQUIRED', ['Japanese nationals travelling to Kenya must obtain a visa prior to departure.'], 'JPN')
    assert ok
    ok, _ = supported('VISA_EXEMPT', ['Holders of passports issued by the United Kingdom do not require a visa.'], 'GBR')
    assert ok


@pytest.mark.parametrize('value,detail,quote,nat', [
    ('VISA_ON_ARRIVAL', 'paper_visa_on_arrival', 'Indian nationals holding a valid United States visa may obtain a visa on arrival.', 'IND'),
    ('VISA_ON_ARRIVAL', 'paper_visa_on_arrival', 'Chinese nationals who are permanent residents of Hong Kong may be issued a visa on arrival.', 'CHN'),
    ('ELECTRONIC_AUTHORIZATION_REQUIRED', 'eta_electronic_authorization', 'Indian nationals who are crew members must obtain an ETA.', 'IND'),
    ('VISA_ON_ARRIVAL', 'paper_visa_on_arrival',
     'Visa-on-Arrival facility is available to the citizens of Japan, South Korea & UAE(only for such UAE nationals who had earlier obtained e-Visa or regular/paper visa for India).', 'KOR'),
])
def test_the_condition_gate_runs_for_every_disposition(value, detail, quote, nat):
    """Round five, major 1: a condition, footnote or example in the deciding
    sentence refuses every subcategory that is not a conditional one; the
    KOR|KOR|IND parenthetical binds to no nationality the converter can
    tell apart, so it is refused too."""
    ok, explain = supported(value, [quote], nat, detail=detail)
    assert not ok and any(e.startswith('conditional wording') for e in explain), explain
    ok, _ = supported('VISA_ON_ARRIVAL', ['Indian nationals can obtain a visa on arrival at the airport.'], 'IND', detail='paper_visa_on_arrival')
    assert ok
    ok, _ = supported('VISA_REQUIRED', ['Nationals of India need a valid visa to enter the United Kingdom as a Standard Visitor.'], 'IND', detail='evisa')
    assert ok
    ok, _ = supported('ELECTRONIC_AUTHORIZATION_REQUIRED', ['Travellers holding a Japanese passport must complete pre-arrival registration before boarding.'], 'JPN',
                      detail='eta_electronic_authorization')
    assert ok


@pytest.mark.parametrize('quote,nat', [
    ('Holders of Indian refugee travel documents do not require a visa.', 'IND'),
    ('Indian seafarers holding a seafarer identity document do not require a visa.', 'IND'),
    ('Holders of Russian alien passports do not require a visa.', 'RUS'),
    ('Holders of a certificate of identity issued by India do not require a visa.', 'IND'),
])
def test_a_travel_document_that_is_not_a_passport_does_not_prove_the_ordinary_passport_route(quote, nat):
    """Round five, major 2."""
    ok, explain = supported('VISA_EXEMPT', [quote], nat, document_type='ordinary_passport')
    assert not ok and any(e.startswith('scoped to travel documents other than a passport') for e in explain), explain


def test_official_portal_url_needs_the_address_in_its_quote_or_on_its_captured_page():
    """Round five, major 3: the word 'apply' proves no address."""
    from scripts.convert_reviewed_general_batch import _check_value
    with pytest.raises(PatchRejected, match='portal address is not in its quote or on its captured page'):
        _check_value('official_portal_url', 'https://evisa.example.gov/attacker', 'Nationals of India may apply at the embassy.', ROUTE, None,
                     pages=[('s1', 'Nationals of India may apply at the embassy.')], evidence_urls=[URL])
    _check_value('official_portal_url', 'https://evisa.example.gov/apply', 'Apply at https://evisa.example.gov/apply before travel.', ROUTE, None,
                 pages=[('s1', 'Apply at https://evisa.example.gov/apply before travel.')], evidence_urls=[URL])
    _check_value('official_portal_url', 'https://evisa.example.gov/apply', 'Nationals of India may apply online.', ROUTE, None,
                 pages=[('s1', 'Portal: www.evisa.example.gov/apply\nNationals of India may apply online.')], evidence_urls=[URL])
    _check_value('official_portal_url', URL, 'Nationals of India may apply online.', ROUTE, None, pages=[('s1', 'x')], evidence_urls=[URL])
    # End to end the field is dropped with its reason, never served on the word 'apply'.
    b = batch()
    b['rows'][0]['route_fields']['official_portal_url'] = 'https://evisa.example.gov/attacker'
    b['rows'][0]['route_field_proofs']['official_portal_url'] = proof('Your application will be processed in 3 working days')
    _, accepted, _ = validate_batch(b, strict=False)
    assert 'official_portal_url' not in accepted[0]['route_fields']
    assert any(d.startswith('official_portal_url: the portal address is not in its quote') for d in accepted[0]['dropped'])


def test_a_qualified_dollar_sign_never_proves_a_fee_in_another_dollar_currency():
    """Round five, major 4: HK$500 is not USD 500."""
    from scripts.convert_reviewed_general_batch import _check_value, _monetary_text
    assert 'USD 500' not in _monetary_text('The visa fee is HK$500 per application.', 'USD')
    for passage, amount, code in (('The visa fee is HK$500 per application.', 500, 'USD'), ('The fee is S$30.', 30, 'USD'), ('The fee is A$20.', 20, 'CAD')):
        with pytest.raises(PatchRejected, match='another dollar currency'):
            _check_value('government_fee', {'amount': amount, 'currency': code}, passage, ROUTE, None)
    _check_value('government_fee', {'amount': 500, 'currency': 'HKD'}, 'The visa fee is HK$500 per application.', ROUTE, None)
    _check_value('government_fee', {'amount': 30, 'currency': 'USD'}, 'The fee is US$30.', ROUTE, None)
    _check_value('government_fee', {'amount': 30, 'currency': 'USD'}, 'The fee is $30.', ROUTE, None)
    _check_value('government_fee', {'amount': 30, 'currency': 'SGD'}, 'The fee is $30.', ROUTE, None)


def test_a_zero_fee_needs_a_free_or_waived_statement_not_a_bare_zero_digit():
    """Round five, minor 1."""
    from scripts.convert_reviewed_general_batch import _check_value
    for passage in ('Indian nationals may stay for 30 days.', 'Processing takes 10 working days.', 'The stay is limited to 90 days within 180 days.', 'The fee is USD 40.'):
        with pytest.raises(PatchRejected, match='zero fee'):
            _check_value('government_fee', {'amount': 0, 'currency': None}, passage, ROUTE, None)
    for passage in ('Entry is free of charge for exempt visitors.', 'Fee: waived.', 'Nationals of Japan | 30 days | 0 |', 'The visa fee is USD 0.'):
        _check_value('government_fee', {'amount': 0, 'currency': None}, passage, ROUTE, None)


def test_a_processing_time_without_a_figure_must_occur_in_its_evidence():
    """Round five, minor 2."""
    from scripts.convert_reviewed_general_batch import _check_value
    with pytest.raises(PatchRejected, match='processing_time: a value without a figure must occur in its evidence'):
        _check_value('processing_time', 'same day', 'Your application will be processed in 3 working days', ROUTE, None)
    with pytest.raises(PatchRejected, match='processing_time'):
        _check_value('processing_time', 'varies', 'Your application will be processed in 3 working days', ROUTE, None)
    _check_value('processing_time', 'same day', 'Visas are issued the same day.', ROUTE, None)
    _check_value('processing_time', 'three working days', 'Processing takes three working days.', ROUTE, None)


def test_an_ampersand_run_is_not_a_list_the_converter_can_read():
    """Round five, minor 3: 'St Lucia Trinidad & Tobago Uruguay St Vincent &
    Grenadines USA' has no item boundaries the page states, so the
    USA|USA|VUT line is refused rather than read as 'Grenadines USA'."""
    from scripts.convert_reviewed_general_batch import _list_line
    line = 'St Lucia Trinidad & Tobago Uruguay St Vincent & Grenadines USA'
    assert not _list_line(line, 'USA')
    ok, _ = supported('VISA_EXEMPT', ['COUNTRIES NOT REQUIRING A VISITOR VISA TO ENTER VANUATU (EXEMPTED COUNTRIES)', line], 'USA')
    assert not ok
    assert _list_line('St Lucia, Trinidad & Tobago, Uruguay, St Vincent & Grenadines, USA', 'USA')


def test_the_dry_run_corrections_of_round_six_hold():
    """Shapes the batch-dry-e rerun exposed while closing the round-five
    findings: a rule that 'applies to' someone is present tense, 'subject
    to the requirement' and 'if you hold a passport from the following
    countries' are the rule not a condition, a FAQ question states no
    verdict, 'excluded from' has its subject before it, 'will not need a
    visa' is an exemption, a dated 'on or after' sub-heading whose date has
    passed continues the list above it, a table cell boundary is not a run
    under the cell before it, a suspension of another verdict's benefit
    is not a suspension of this one, and a tariff row with a zero cell
    proves a zero fee."""
    from scripts.convert_reviewed_general_batch import (_boundary, _check_value, _headings, _page_index, _NOT_TODAYS_RULE,
                                                       _exception_items)
    assert not re.search(_NOT_TODAYS_RULE, 'visa-exempt entry only applies to foreign visitors holding formal passports', re.I)
    assert re.search(_NOT_TODAYS_RULE, 'the visa exemption for indian nationals applied until 30 june 2025', re.I)
    ok, _ = supported('ELECTRONIC_AUTHORIZATION_REQUIRED', ['Nationalities of the following locations are subject to the requirement to obtain an ETA for travel to the UK.', 'Australia'],
                      'AUS', pages=[('p', 'Nationalities of the following locations are subject to the requirement to obtain an ETA for travel to the UK.\n(c) for travel to the UK on or after 8 January 2025:\nAustralia\nCanada\n')] * 2,
                      detail='eta_electronic_authorization')
    assert ok
    assert not _boundary('(c) for travel to the uk on or after 8 january 2025:', 'ELECTRONIC_AUTHORIZATION_REQUIRED')
    assert _boundary('(c) for travel to the uk on or after 8 january 2030:', 'ELECTRONIC_AUTHORIZATION_REQUIRED')
    ok, _ = supported('VISA_REQUIRED', ['If you hold valid passports from any of the following countries/regions, you are eligible for eVisa.', 'Canada'],
                      'CAN', pages=[('p', 'If you hold valid passports from any of the following countries/regions, you are eligible for eVisa.\nAustralia\nCanada\n')] * 2, detail='evisa')
    assert ok
    page = 'Do US citizens need a visa?\nNo. Under the Compact of Free Association, US citizens are fully exempt from visa requirements.\n'
    ok, _ = supported('VISA_EXEMPT', ['No. Under the Compact of Free Association, US citizens are fully exempt from visa requirements.'], 'USA', pages=[('p', page)])
    assert ok
    assert list(_exception_items('Serbian citizens holding passports issued by the coordination directorate are excluded from the visa waiver.')) == []
    ok, _ = supported('VISA_EXEMPT', ['British citizens will therefore not need a Schengen short-stay visa to spend up to 90 days in Italy.'], 'GBR')
    assert ok
    assert _headings(_page_index('12.\n|\nczech republic\n|\ndiplomatic passports only\n|\n13.\n|\negypt\n|\ndiplomatic, official and service passports only\n|\n')[0])[1] == []
    ok, _ = supported('VISA_REQUIRED', ['All Indian nationals must obtain a visa through the Korean diplomatic mission.'], 'IND',
                      pages=[('p', 'Notice on temporary exemption of K-ETA for 22 countries.\nAll Indian nationals must obtain a visa through the Korean diplomatic mission.\n')])
    assert ok
    ok, explain = supported('VISA_EXEMPT', ['Canadian passport holders do not require a visa for a short term visit.'], 'CAN',
                            pages=[('p', 'Canadian passport holders do not require a visa for a short term visit.\nTemporary exemption of K-ETA extended for Canadians until December 31, 2026.\n')])
    assert not ok
    _check_value('fee', {'amount': 0, 'currency': 'USD'}, 'Malaysia 00 00 40 200\nCountry/Territory Wise e-Tourist Visa Fee (in US $)', ROUTE, None)
    with pytest.raises(PatchRejected, match='zero fee'):
        _check_value('fee', {'amount': 0, 'currency': 'USD'}, 'Malaysia 10 25 40 200', ROUTE, None)


# Round-seven review regressions. The held-pairs pilot dry run kept 9 of 24
# bound rows because a footnote, a dated window or an exception clause that
# governs ANOTHER nationality, another scheme or another document class
# reached a row whose own rule is clean. Every gate now resolves the clause,
# the footnote mark and the window to its own subject before it binds, and
# still refuses whatever it cannot resolve to anyone.


def test_another_nationalitys_exception_clause_does_not_refuse_our_list_line():
    """boca.gov.tw: the 30-day list line that names Singapore stands in the
    same page section as "Nationals of Honduras*, except those holding
    diplomatic or official/service passports, are eligible ...". That clause
    governs Honduras, and the Brunei note two sections down governs Brunei."""
    rule = ('Nationals of the following countries are eligible for the visa-exemption program, '
            'with a duration of stay of up to 30 days: Belize*, Malaysia, Saint Lucia*, and Singapore.')
    others = ('Nationals of Honduras*, except those holding diplomatic or official/service passports, are eligible for '
              'the visa-exemption program, with a duration of stay of up to 90 days.\n'
              'Nationals of the Philippines, except those holding diplomatic or official/service passports, are eligible '
              'for the visa-exemption program, with a duration of stay of up to 14 days.\n'
              'Those holding emergency, temporary, or other informal passports or travel documents are ineligible, with '
              'the exception of those holding a Brunei certificate of identity.\n')
    ok, explain = supported('VISA_EXEMPT', [rule], 'SGP', pages=[('p', rule + '\n' + others)])
    assert ok, explain
    # Fail closed twice over. An exception the converter can resolve to
    # nobody still refuses, wherever on the page it stands, and one that
    # names this nationality carves it out of its own rule.
    annex = 'All visitors are eligible for the visa-exemption program, except those listed in the annex.\n'
    ok, explain = supported('VISA_EXEMPT', [rule], 'SGP', pages=[('p', rule + '\n' + annex)])
    assert not ok and any('an exception clause in the same page section cannot be read' in e for e in explain), explain
    ours = 'ASEAN nationals are eligible for the visa-exemption program, except Singapore.\n'
    ok, explain = supported('VISA_EXEMPT', [rule], 'SGP', pages=[('p', rule + '\n' + ours)])
    assert not ok and any('carved out by name in the same page section' in e for e in explain), explain
    # A clause that excepts only a document class the route is not is that
    # class's own; against a route of that class it is unreadable again, and
    # read without a route it stays unreadable as round five left it.
    from scripts.convert_reviewed_general_batch import _unreadable_exception
    clause = ('ASEAN nationals, except those holding diplomatic or official/service passports, are eligible for '
              'the visa-exemption program.')
    assert _unreadable_exception(clause) is not None
    assert _unreadable_exception(clause, nat='SGP', document_type='ordinary_passport') is None
    assert _unreadable_exception(clause, nat='SGP', document_type='diplomatic_passport') is not None


def test_another_nationalitys_sunset_on_the_captured_page_does_not_expire_our_verdict():
    """imi.gov.my carries the row "India** citizen: visa exempts until 31st
    December 2026" beside the ASEAN rule sentence that proves Indonesia,
    Thailand, Vietnam and the United Kingdom. That sunset is India's."""
    rule = ASEAN_SENTENCE
    page = rule + '\n1) Indonesia citizen: visa exempts for 30 days\n2) India** citizen: visa exempts until 31st December 2026\n'
    ok, explain = supported('VISA_EXEMPT', [rule], 'IDN', pages=[('p', page)])
    assert ok, explain
    # Fail closed: the same row against our own nationality must be recorded
    # as the bound it is, and a sunset of everyone's rule still binds.
    ours = rule + '\n1) India citizen: visa exempts for 30 days\n2) Indonesia** citizen: visa exempts until 31st December 2026\n'
    ok, explain = supported('VISA_EXEMPT', [rule], 'IDN', pages=[('p', ours)])
    assert not ok and any('until 2026-12-31 is not recorded as effective_to' in e for e in explain), explain
    ok, _ = supported('VISA_EXEMPT', [rule], 'IDN', pages=[('p', ours)], bounds={'effective_to': '2026-12-31'})
    assert ok
    everyone = rule + '\n1) India citizen: visa exempts for 30 days\n2) The visa exemption for all listed nationals runs until 31st December 2026.\n'
    ok, explain = supported('VISA_EXEMPT', [rule], 'IDN', pages=[('p', everyone)])
    assert not ok and any('until 2026-12-31 is not recorded as effective_to' in e for e in explain), explain


def test_another_schemes_footnote_window_does_not_expire_our_footnotes_verdict():
    """mofa.gov.vn's exemption table marks Japan's row * (the unilateral
    policy, in force to 14 March 2028) and Poland's ** (the 2025 tourism
    stimulus programme, which ended on 31 December 2025). The ** footnote
    names its own nationalities, so its window is theirs."""
    rule = 'No | Country | Duration of stay'
    rows = ('43 | japan | 45 days for other passport types under the unilateral visa exemption policy*\n'
            '71 | poland | 45 days for other passport types under the tourism stimulus program in 2025**\n')
    marks = ('| * In accordance with Resolution No. 44/NQ-CP, citizens of 12 countries (Germany, France, Italy, Spain, '
             'United Kingdom, Russia, Japan, Korea, Denmark, Sweden, Norway and Finland) enjoy visa exemption with a stay '
             'duration of 45 days from the date of entry.\n'
             'The unilateral visa exemption policy is effective from 15 March 2025 until 14 March 2028.\n'
             '| ** In accordance with Resolution No.11/NQ-CP, under the tourism stimulus program in 2025, citizens of '
             'Poland, Czech and Switzerland enjoy visa exemption with a stay duration of 45 days from the date of entry.\n'
             'The visa exemption policy under the tourism stimulus program in 2025 is effective from 01 March 2025 '
             'until 31 December 2025.\n')
    page = rule + '\n' + rows + marks
    quote = '43 | japan | 45 days for other passport types under the unilateral visa exemption policy*'
    bounds = {'effective_from': '2025-03-15', 'effective_to': '2028-03-14'}
    ok, explain = supported('VISA_EXEMPT', [marks.split('\n')[0].lstrip('| '), quote], 'JPN',
                            pages=[('p', page)] * 2, bounds=bounds, detail='conditional_visa_free')
    assert ok, explain
    # Fail closed: the footnote that does govern our entry expires it. Poland
    # carries the ** mark, so the ended window is Poland's own.
    pol = '71 | poland | 45 days for other passport types under the tourism stimulus program in 2025**'
    ok, explain = supported('VISA_EXEMPT', [marks.split('\n')[2].lstrip('| '), pol], 'POL',
                            pages=[('p', page)] * 2, detail='conditional_visa_free')
    assert not ok and any('ended on 2025-12-31' in e for e in explain), explain
    # And a footnote that names no nationality is resolved to nobody, so its
    # window still bounds every row of the page.
    anonymous = page.replace('citizens of Poland, Czech and Switzerland enjoy', 'the listed citizens enjoy')
    ok, explain = supported('VISA_EXEMPT', [marks.split('\n')[0].lstrip('| '), quote], 'JPN',
                            pages=[('p', anonymous)] * 2, bounds=bounds, detail='conditional_visa_free')
    assert not ok and any('ended on 2025-12-31' in e for e in explain), explain


def test_a_closing_footnote_mark_belongs_to_every_entry_until_the_footnote_hands_it_to_one():
    """Round eight, BLOCKING-3. A mark that closes a cell holding several
    entries is every entry's. Position is not evidence: only the footnote the
    mark points to can hand it to one of them, and then only when that body
    names the neighbour and nobody else."""
    from scripts.convert_reviewed_general_batch import _footnote_marked
    assert _footnote_marked('Japan, Thailand (1)', 'THA') and _footnote_marked('Japan, Thailand (1)', 'JPN')
    assert _footnote_marked('Japan (1)', 'JPN') and _footnote_marked('No necesita Visa (4)', 'JPN')
    theirs = 'Japan, Thailand (1)\n(1) Thailand nationals must hold a biometric passport.\n'
    assert not _footnote_marked('Japan, Thailand (1)', 'JPN', theirs)
    assert _footnote_marked('Japan, Thailand (1)', 'THA', theirs)
    # A body that speaks of the whole list, or of this entry too, is ours.
    ours = 'Japan, Thailand (1)\n(1) The exemption applies only to holders of biometric passports.\n'
    assert _footnote_marked('Japan, Thailand (1)', 'JPN', ours)
    rule = 'Nationals of the following countries do not need a visa for stays of up to 90 days:'
    page = rule + '\nJapan, Thailand (1)\n'
    for nat in ('JPN', 'THA'):
        ok, explain = supported('VISA_EXEMPT', [rule, 'Japan, Thailand (1)'], nat, pages=[('p', page)] * 2,
                                detail='unconditional_visa_free')
        assert not ok and any(e.startswith('footnote on the list line') for e in explain), explain
    page = rule + '\nJapan, Thailand (1)\n(1) Thailand nationals must hold a biometric passport.\n'
    ok, explain = supported('VISA_EXEMPT', [rule, 'Japan, Thailand (1)'], 'JPN', pages=[('p', page)] * 2,
                            detail='unconditional_visa_free')
    assert ok, explain
    ok, explain = supported('VISA_EXEMPT', [rule, 'Japan, Thailand (1)'], 'THA', pages=[('p', page)] * 2,
                            detail='unconditional_visa_free')
    assert not ok and any(e.startswith('footnote on the list line') for e in explain), explain


# Round-eight regressions. Each test carries the round-seven reviewer's own
# sentences: refused at ca24a10 the way the review found them, refusing now,
# and beside each the clean row the review lists as correct, still proving.


def test_b1_each_exception_item_answers_for_itself():
    """BLOCKING-1: one clause, two items. "Myanmar nationals" resolves and
    "persons from the countries listed in Annex III" does not; the readable
    item never dismisses its sibling."""
    from scripts.convert_reviewed_general_batch import _unreadable_exception
    quote = ('Visa-free entry for stays of up to 30 days applies to ASEAN nationals, except Myanmar nationals '
             'and persons from the countries listed in Annex III.')
    assert 'annex iii' in (_unreadable_exception(quote, nat='LAO', document_type='ordinary_passport') or '').lower()
    ok, explain = supported('VISA_EXEMPT', [quote], 'LAO')
    assert not ok and any('cannot be read' in e for e in explain), explain
    # The clean row: the same rule with only the readable item still proves
    # Laos and still carves Myanmar out.
    ok, explain = supported('VISA_EXEMPT', [ASEAN_SENTENCE], 'LAO')
    assert ok, explain
    ok, explain = supported('VISA_EXEMPT', [ASEAN_SENTENCE], 'MMR')
    assert not ok and any('carved out' in e for e in explain), explain


def test_b2_a_carve_out_binds_on_whom_it_names_not_on_verdict_words():
    """BLOCKING-2: a carve-out that names this nationality refuses the row
    whether or not the sentence spends a verdict word."""
    from scripts.convert_reviewed_general_batch import _exception_binds
    rule = 'Visa is not required for a stay of less than one (1) month for ASEAN nationals.'
    for extra in ('The arrangement covers all ASEAN members except Myanmar.',
                  'Participation is limited to the ASEAN members other than Myanmar.'):
        assert _exception_binds(extra, 'MMR', 'VISA_EXEMPT', None), extra
        ok, explain = supported('VISA_EXEMPT', [rule], 'MMR', pages=[('p', rule + '\n' + extra + '\n')])
        assert not ok and any('carved out by name in the same page section' in e for e in explain), (extra, explain)
        # Laos is not the one named, so the same page still proves Laos.
        ok, explain = supported('VISA_EXEMPT', [rule], 'LAO', pages=[('p', rule + '\n' + extra + '\n')])
        assert ok, (extra, explain)


def test_b3_a_mark_on_a_multi_entry_cell_is_read_through_its_footnote():
    """BLOCKING-3: the mark on "Japan and Thailand (1)" is Japan's too until
    the footnote it points to hands it to Thailand alone."""
    intro = 'Nationals of the following countries do not require a visa for stays of up to 45 days:'
    body = '(1) The exemption applies only to holders of biometric passports.'
    for cell in ('Japan and Thailand (1)', 'Japan, Korea, Singapore (1)', 'Japan / Thailand (1)', 'Japan,\nThailand (1)'):
        page = intro + '\n' + cell + '\n' + body + '\n'
        ok, explain = supported('VISA_EXEMPT', [intro, cell], 'JPN', pages=[('p', page)] * 2, detail='unconditional_visa_free')
        assert not ok, (cell, explain)
    control = intro + '\nJapan and Thailand (1)\n(1) Thailand nationals must hold a biometric passport.\n'
    ok, explain = supported('VISA_EXEMPT', [intro, 'Japan and Thailand (1)'], 'JPN', pages=[('p', control)] * 2,
                            detail='unconditional_visa_free')
    assert ok, explain


def test_b4_a_numbered_or_starred_list_row_opens_no_footnote_body():
    """BLOCKING-4: "1) Japan" and "* Japan" are list rows, so the scheme-wide
    sunset under them bounds Japan whatever shape the rows take."""
    from scripts.convert_reviewed_general_batch import _defines_mark, _scope_blocks
    intro = 'Nationals of the following countries do not require a visa for stays of up to 45 days:'
    sunset = 'The visa exemption scheme ends on 31 December 2025.'
    for a, b in (('Japan', 'Poland'), ('1) Japan', '2) Poland'), ('* Japan', '* Poland')):
        page = intro + '\n' + a + '\n' + b + '\n' + sunset + '\n'
        assert len(list(_scope_blocks(page))) == 1, a
        ok, explain = supported('VISA_EXEMPT', [intro, a], 'JPN', pages=[('p', page)] * 2)
        assert not ok and any('ended on 2025-12-31' in e for e in explain), (a, explain)
    # Only a line that defines a mark the page has used, and says something
    # of its own, opens a footnote body.
    assert _defines_mark('Japan*\nPoland', '*', 'Holders of biometric passports only.')
    assert not _defines_mark('Japan\nPoland', '*', 'Holders of biometric passports only.')
    assert not _defines_mark('Japan*\nPoland', '*', 'Poland')


def test_b5_a_bound_after_the_closing_item_of_a_conjunction_run_is_the_whole_runs():
    """BLOCKING-5: "Brunei, the Philippines and Thailand (effective until
    July 31, 2022)" bounds all three, not Thailand alone."""
    from scripts.convert_reviewed_general_batch import _norm, _window_entry, _window_violation
    quote = 'Nationals of Brunei, the Philippines and Thailand (effective until July 31, 2022) are eligible for visa-free entry.'
    low = _norm(quote)
    start = low.find('july 31, 2022')
    assert _window_entry(low, start, start + len('july 31, 2022')) is None
    for nat in ('BRN', 'PHL', 'THA'):
        assert 'ended on 2022-07-31' in (_window_violation([quote], nat=nat) or ''), nat
        ok, explain = supported('VISA_EXEMPT', [quote], nat)
        assert not ok and any('ended on 2022-07-31' in e for e in explain), (nat, explain)
    # The entry-scoped reading of round six still holds for a comma-opened
    # entry in the middle of a run.
    run = ('Nationals of the following countries are eligible for the visa-exemption program: '
           'Albania, North Macedonia (effective until March 31, 2030), Norway, United States.')
    assert _window_violation([run], nat='USA') is None
    assert 'until 2030-03-31 is not recorded as effective_to' in _window_violation([run])


@pytest.mark.parametrize('relation', [
    'ceases to apply on', 'shall cease on', 'lapses on', 'applies for arrivals before', 'runs to',
    'terminates on', 'ceases to be valid on', 'no later than', 'at the latest on', 'will cease on',
])
def test_m1_every_end_relation_is_read_by_both_readers(relation):
    """MAJOR-1: the window reader and the recorder's bound reader carry one
    end-of-window vocabulary, so a bound that expires the verdict for one is
    a bound the reviewer may record for the other."""
    from scripts.convert_reviewed_general_batch import _policy_bound_statement, _policy_windows
    sentence = 'The visa exemption scheme ' + relation + ' 31 December 2025.'
    assert [(w[0], str(w[1]), w[5]) for w in _policy_windows(sentence)] == [(None, '2025-12-31', None)], relation
    assert _policy_bound_statement(sentence, ['31 December 2025', '2025-12-31'], 'effective_to'), relation
    ok, explain = supported('VISA_EXEMPT', ['Nationals of Japan do not require a visa. ' + sentence], 'JPN')
    assert not ok and any('ended on 2025-12-31' in e for e in explain), (relation, explain)


def test_m1_a_date_no_relation_reaches_is_an_unreadable_bound_not_no_bound():
    """MAJOR-1, the fail-closed half: a readable date in a rule sentence
    that no relation reaches refuses the row instead of bounding nothing."""
    from scripts.convert_reviewed_general_batch import _policy_windows, _window_violation
    for sentence in ('The visa exemption scheme changed on 31 December 2025.',
                     'The visa exemption of 31 December 2025 applies to Japan.',
                     'Under the visa waiver agreement, Japanese nationals are exempt until the end of 31 December 2025.'):
        assert [w[5] for w in _policy_windows(sentence)] == ['31 december 2025'], sentence
        assert 'no relation reaches' in _window_violation([sentence]), sentence
        ok, explain = supported('VISA_EXEMPT', ['Nationals of Japan do not require a visa. ' + sentence], 'JPN')
        assert not ok and any('no relation reaches' in e for e in explain), (sentence, explain)
    # An office, issue, update or passport date is still no window at all.
    assert list(_policy_windows('This page was updated on 31 December 2025.')) == []


@pytest.mark.parametrize('sentence', [
    'In accordance with Resolution No. 44/NQ-CP of the Government dated 07 March 2025, citizens of 12 countries enjoy visa exemption.',
    'Chính phủ vừa ban hành Nghị quyết số 44/NQ-CP ngày 7/3/2025 về việc miễn thị thực cho công dân 12 nước.',
    'Сотрудники, аккредитованные в госпротоколе МИД России, находятся по визам (нота от 16.06.2014г.).',
    'Regulation (EU) 2018/1806 of the European Parliament and of the Council of 14 November 2018 listing the third countries whose nationals must be in possession of visas.',
    'Regulation (EC) No. 810/2009 of the European Parliament and of the Council of 13 July 2009 establishing a Community Code on Visas applies.',
    '►M2 Commission Delegated Regulation (EU) 2023/222 of 1 December 2022 L 32 1 3.2.2023',
    'GDPRの規定、2003年6月30日付緊急政令第196号の規定に基づく個人情報取り扱いを許可します',
    'Palau signed a mutual visa-waiver agreement with the European Union on 7 December 2015.',
    'The visa waiver programme was announced on 1 March 2026.',
])
def test_m1_an_instruments_own_date_is_no_bound(sentence):
    """The date of the resolution, decree, regulation or journal that states
    the rule, or the day it was signed, is a relation the converter reads
    and not a bound; the dry runs lost thirteen correct rows to it."""
    from scripts.convert_reviewed_general_batch import _policy_windows, _window_violation
    assert list(_policy_windows(sentence)) == [], sentence
    assert _window_violation([sentence]) is None


def test_m1_an_instrument_date_hides_no_bound_beside_it():
    from scripts.convert_reviewed_general_batch import _policy_windows, _window_violation
    assert [(w[0], str(w[1]), w[5]) for w in _policy_windows('The decree of 1 March 2025 applies until 31 December 2026.')] == [(None, '2026-12-31', None)]
    assert [(w[0], str(w[1]), w[5]) for w in _policy_windows('The agreement signed on 7 December 2015 ceases to apply on 31 December 2025.')] == [(None, '2025-12-31', None)]
    quote = ('Under Resolution No. 44/NQ-CP dated 07 March 2025, Japanese nationals enjoy visa exemption '
             'from 15 March 2025 until 14 March 2028.')
    assert 'until 2028-03-14 is not recorded as effective_to' in _window_violation([quote], nat='JPN')
    bounds = {'effective_from': '2025-03-15', 'effective_to': '2028-03-14'}
    assert _window_violation([quote], bounds, nat='JPN') is None
    ok, explain = supported('VISA_EXEMPT', [quote], 'JPN')
    assert not ok, explain
    ok, explain = supported('VISA_EXEMPT', [quote], 'JPN', bounds=bounds)
    assert ok, explain


M2_RULE = 'Visa is not required for a stay of less than one (1) month for ASEAN nationals.'


@pytest.mark.parametrize('extra', [
    'Myanmar is not a party to the arrangement.',
    'The list of beneficiary countries does not include Myanmar.',
    'Myanmar was left out of the 2026 arrangement.',
    'Myanmar joins the arrangement at a later date.',
    'Myanmar is not among the beneficiaries of the arrangement.',
    'Myanmar is excluded from the scheme.',
    'Cambodia, Laos and Myanmar are not parties to the arrangement.',
    'Cambodia, Laos and Myanmar are not, at present, parties to the arrangement.',
    'Myanmar will be added to the list of exempt countries from 1 January 2027.',
])
def test_m2_a_denial_of_membership_refuses_the_named_nationality_wherever_it_sits(extra):
    """MAJOR-2: a sentence whose subject is this nationality, or a list
    holding it, and whose predicate negates membership, coverage,
    participation or eligibility in the scheme refuses the exemption, before
    the rule or after it in the same page section, or inside the evidence.
    A membership deferred to a start still to come is refused by the window
    gate first, which is the same refusal."""
    from scripts.convert_reviewed_general_batch import _excluded_by_statement
    assert _excluded_by_statement(extra, 'MMR', 'VISA_EXEMPT'), extra
    refused = ('excluded from the rule', 'starts on 2027-01-01')
    for page in ('Visa exemption\n' + M2_RULE + ' ' + extra + '\n', 'Visa exemption\n' + extra + ' ' + M2_RULE + '\n'):
        ok, explain = supported('VISA_EXEMPT', [M2_RULE], 'MMR', pages=[('p', page)])
        assert not ok and any(r in e for e in explain for r in refused), (extra, explain)
    ok, explain = supported('VISA_EXEMPT', [M2_RULE, extra], 'MMR')
    assert not ok and any(r in e for e in explain for r in refused), (extra, explain)
    # Thailand is named by none of them, so the same page still proves Thailand.
    ok, explain = supported('VISA_EXEMPT', [M2_RULE], 'THA', pages=[('p', 'Visa exemption\n' + M2_RULE + ' ' + extra + '\n')])
    assert ok, (extra, explain)


@pytest.mark.parametrize('sentence,nat', [
    ('Myanmar is a party to the arrangement.', 'MMR'),
    ('Nationals who do not hold a machine-readable passport are members of the scheme.', 'MMR'),
    ('Myanmar is not a party to the dispute.', 'MMR'),
    ('In accordance with the provisions of the EU Regulation 2019/592, starting from 1 January 2021 (the end of the transition '
     'period) the United Kingdom will be added to the list of third countries whose nationals are exempt from the visa requirement.', 'GBR'),
])
def test_m2_a_negation_about_something_else_denies_no_membership(sentence, nat):
    """A negated passport, another party's membership, a dispute, or a
    membership whose stated start has passed is no denial."""
    from scripts.convert_reviewed_general_batch import _excluded_by_statement, _membership_denied, _norm
    assert not _membership_denied(_norm(sentence)), sentence
    assert not _excluded_by_statement(sentence, nat, 'VISA_EXEMPT'), sentence


def test_the_imi_india_sunset_is_read_from_its_own_line_whatever_the_page_split():
    """MAJOR-3: imi.gov.my keeps IDN, THA, VNM and GBR to MYS because the
    clause "India** citizen: visa exempts until 31st December 2026" names
    India, read from its own line, not because a block split parts it from
    the ASEAN rule. The page read whole, with no split at all, says the same."""
    from scripts.convert_reviewed_general_batch import _scope_blocks, _window_violation
    page = ('Countries required to apply for a visa to enter Malaysia (List of involved countries)\n'
            'Afghanistan***\nBangladesh\nIndia**\nNepal\nNOTE\n'
            'For countries marked as (*) are allowed to enter Malaysia by air only.\n'
            'India** citizen: visa exempts until 31st December 2026.\n'
            '***Afghanistan nationals who wish to enter Malaysia for social visit purposes must obtain a visa approval '
            'letter before applying for a VTR.\n' + ASEAN_SENTENCE + '\n')
    blocks = list(_scope_blocks(page))
    assert len(blocks) == 2
    assert not any('India** citizen' in block and 'ASEAN nationals' in block for block, _ in blocks)
    for nat in ('IDN', 'THA', 'VNM', 'GBR'):
        assert _window_violation([page], nat=nat) is None, nat
        assert all(_window_violation([block], nat=nat, scope=scope) is None for block, scope in blocks), nat
    assert 'until 2026-12-31 is not recorded as effective_to' in _window_violation([page], nat='IND')
    assert 'until 2026-12-31 is not recorded as effective_to' in _window_violation([page])
    assert _window_violation([page], {'effective_to': '2026-12-31'}, nat='IND') is None


def test_an_unreadable_exception_binds_only_on_the_rule_this_verdict_rests_on():
    """Round eight, on the reviewer's BLOCKING-2 rule. An item the converter
    cannot read refuses on a sentence that states, flips or contradicts the
    verdict or speaks of the scheme's membership, and binds nothing on a rule
    about another matter: converting a stay, processing an application, the
    validity of the document issued, how days of stay are counted, the papers
    an application needs, another document class's rule, or another
    nationality's own entry. An item excepts nobody only when it is read as
    a day, a period, a fee or an office matter."""
    from scripts.convert_reviewed_general_batch import _exception_binds, _exception_elsewhere, _unreadable_exception
    rule = 'Nationals of the following countries are eligible for the visa-exemption program: Japan, Korea, Singapore.'
    conversion = 'Visa-exempt entry cannot be converted to visa-based stay, unless any of the following applies:'
    assert _unreadable_exception(conversion, nat='JPN', document_type='ordinary_passport') is not None
    assert not _exception_binds(conversion, 'JPN', 'VISA_EXEMPT', None)
    ok, explain = supported('VISA_EXEMPT', [rule], 'JPN', pages=[('p', rule + '\n' + conversion + '\n')])
    assert ok, explain
    for other in (
        'Your visa application will be processed within three working days (excluding the day of submission).',
        'The standard period is 90 days on arrival unless otherwise specified in the applicable waiver agreement.',
        'Nationals of Malaysia may stay in Denmark for up to 3 months reckoned from the date of their first entry into '
        'Denmark or another Nordic country (not including Iceland).',
        'The time you have stayed in Denmark or another Nordic country (not including Iceland) within 6 months preceding '
        'any such entry shall be deducted from the mentioned 3 months.',
        'c. tiket kembali atau tiket terusan ke negara lain, kecuali bagi awak alat angkut yang akan singgah untuk '
        'bergabung dengan kapalnya dan melanjutkan perjalanan ke negara lain',
        'Orang asing pemegang dokumen perjalanan (bukan paspor kebangsaan) berupa paspor sementara, paspor darurat, '
        'titre du voyage, certificate of identity, laissez passer harus melampirkan dokumen izin (kecuali awak alat angkut).',
    ):
        assert not _exception_binds(other, 'USA', 'VISA_EXEMPT', None), other
    ours = (
        'Visa-free entry applies to ASEAN nationals, unless any of the following applies:',
        'The following people do not need a visit visa before they travel to the UK as a visitor, other than where VN 2.3. applies:',
        'A person who meets one or more of the criteria below needs entry clearance (a visa) in advance of travel to the UK '
        'for any purpose, unless they meet one of the exceptions set out in VN2.1 and VN2.2.',
        'Such aliens must secure a visa in order to be admitted to the United States as nonimmigrants, unless otherwise exempt.',
        'Anyone wishing to enter the Kingdom for tourism purposes shall obtain a valid visa, unless his entry does not require it.',
        'The arrangement covers all ASEAN members except those listed in Annex III.',
        'ASEAN nationals are exempt from the visa requirement, except crew members.',
        'Nationals of Japan do not need a visa, except stays exceeding 90 days.',
    )
    for sentence in ours:
        assert _exception_binds(sentence, 'JPN', 'VISA_EXEMPT', None), sentence
    for sentence in ours[1:2] + ours[3:4]:
        ok, explain = supported('VISA_EXEMPT', [rule], 'JPN', pages=[('p', rule + '\n' + sentence + '\n')])
        assert not ok and any('cannot be read' in e for e in explain), (sentence, explain)
    for item in ('the day of submission', 'weekends and public holidays', 'VAT'):
        assert _exception_elsewhere(item, item, item, 'JPN', 'ordinary_passport'), item
    for item in ('any of the following applies', 'where VN 2.3 applies', 'the passport has at least 6 months remaining',
                 'otherwise exempt', 'stays exceeding 90 days', 'crew members'):
        assert not _exception_elsewhere(item, item, item, 'JPN', 'ordinary_passport'), item
