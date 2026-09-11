"""A reviewed field fill writes only into empty required cells, with its own quote.

The fixture is the owner's example: an Australian ordinary passport to Russia
for tourism, served with a government source_url and a tourist visa product
whose validity, fee and document list are still empty although the embassy
page states them. The regression tests reproduce, sentence for sentence, the
scenarios an independent review executed against the first converter: every
one of them must now be refused rather than served.
"""
from copy import deepcopy
import hashlib
import json

import pytest

from scripts import convert_reviewed_field_fill as c
from scripts.prepare_reviewed_product_patch import PatchRejected, digest
from app.visa_snapshot import tstation as t, verified_overrides as vo

URL = 'https://australia.mid.ru/en/consular_services/visas/tourist/'
FEES = 'https://australia.mid.ru/en/consular_services/fees/'
BUSINESS = 'https://australia.mid.ru/en/consular_services/visas/business/'
TEXT = ('Embassy of the Russian Federation in Australia. Tourist visa. '
        'Australian citizens need a visa to enter the Russian Federation. '
        'A tourist visa is valid for up to 30 days and is issued as a single-entry visa. '
        'The holder may stay in Russia for up to 30 days. '
        'Documents required: a valid passport, a completed visa application form, one photo, '
        'a tourist confirmation from a registered Russian tour operator and a medical insurance policy. '
        'Applications are lodged at the Embassy in Canberra or the Consulate General in Sydney.')
FEE_TEXT = ('Consular fees. The consular fee for a tourist visa is A$150 per application, '
            'payable at the Embassy in Australian dollars.')
BUSINESS_TEXT = ('Business visa. A business visa is valid for up to 90 days. '
                 'The visa centre also charges a service fee of A$62 per application, which is not a consular fee.')
ROUTE = {'passport_nationality': 'AUS', 'lawful_country_of_residence': 'AUS', 'destination_country': 'RUS',
         'visa_category': 'tourist_visa', 'travel_purpose': 'tourism', 'arrival_date': None, 'consular_jurisdiction': None}
KEY = 'AUS|AUS|RUS|tourism|default|unknown|v6'
VERDICT_QUOTE = 'Australian citizens need a visa to enter the Russian Federation.'
SUBJECT = {'passport_nationality': 'AUS', 'destination_country': 'RUS', 'travel_purpose': 'tourism',
           'travel_document_type': 'ordinary_passport'}
DECISION = {'source_id': 'ru_aus_tourist', 'source_url': URL, 'quote': VERDICT_QUOTE, 'verified_at': '2026-09-01',
            'verifier': 'ai', 'verified_by': 'Ellis AI official-source field review', 'status': 'reviewed',
            'note': 'Australian ordinary passport, tourism.'}
PRODUCT = {'type': 'Tourist visa', 'disposition': 'VISA_REQUIRED', 'requirement_detail': 'paper_visa',
           'validity': None, 'entry': 'single', 'max_stay_days': 30, 'fee': {'amount': None, 'currency': None},
           'notes': None, 'source_url': URL, 'source_quote': VERDICT_QUOTE, 'verified_at': '2026-09-01', 'verifier': 'ai',
           'field_provenance': {'disposition': dict(DECISION, subject=dict(SUBJECT, product_type='Tourist visa',
                                                                            disposition='VISA_REQUIRED',
                                                                            requirement_detail='paper_visa'))}}
RAW = {'disposition': 'VISA_REQUIRED', 'requirement_detail': 'paper_visa', 'visa_category': 'Tourist visa',
       'permitted_stay': 'Up to 30 days', 'permitted_stay_days': 30, 'application_channel': 'embassy_or_consulate',
       'application_channel_detail': 'Lodge the application at the Embassy of Russia in Canberra.',
       'processing_time': '10 working days',
       'exceptions': ['A tourist confirmation from a registered Russian tour operator is required.'],
       'visa_products': [dict(deepcopy(PRODUCT), field_provenance=None, source_url=None, source_quote=None,
                              verified_at=None, verifier=None)]}
SEED = {'route': {'nationality': 'AUS', 'destination': 'RUS', 'travel_purpose': 'tourism',
                  'travel_document_type': 'ordinary_passport'},
        'verified_at': '2026-09-01', 'verified_by': 'Ellis AI official-source field review', 'verifier': 'ai',
        'source_url': URL, 'note': 'Australian ordinary passport, tourism.',
        'fields': {'disposition': 'VISA_REQUIRED', 'requirement_detail': 'paper_visa',
                   'visa_products': [deepcopy(PRODUCT)], 'source_url': URL},
        'field_provenance': {'disposition': dict(DECISION, subject=dict(SUBJECT))}}


def source(sid, url, text):
    return {'id': sid, 'url': url, 'checked_at': '2026-09-11', 'text': text,
            'sha256': hashlib.sha256(text.encode()).hexdigest()}


def build_layer(route, key, raw, seed):
    """The six layers exactly as the production reader builds them."""
    raw, seed = deepcopy(raw), deepcopy(seed)
    identity = vo._key(route['passport_nationality'], route['destination_country'], route['travel_purpose'],
                       route.get('travel_document_type') or 'ordinary_passport')
    prior = vo._parse_rows([deepcopy(seed)], {})[identity]
    merged, checked = vo.merge_verified_fields(deepcopy(raw), prior['fields'], source_url=prior['source_url'])
    prov = dict(prior['field_provenance'].get('disposition') or vo._provenance(prior), fields=sorted(checked),
                field_provenance=prior['field_provenance'])
    return {'cache_key': key, 'route': deepcopy(route), 'raw_guidance': raw, 'merged_guidance': merged,
            'source_provenance': prov, 'seed_entries': [seed], 'operator_entries': []}


def layer(raw=None, seed=None):
    return build_layer(ROUTE, KEY, RAW if raw is None else raw, SEED if seed is None else seed)


def second_product(name='Business visa', **cells):
    product = dict(deepcopy(PRODUCT), type=name, **cells)
    product['field_provenance'] = {'disposition': dict(DECISION, subject=dict(
        SUBJECT, product_type=name, disposition='VISA_REQUIRED', requirement_detail='paper_visa'))}
    return product


def two_product_layer(name='Business visa', **cells):
    """The fixture with a second served product beside the tourist visa."""
    extra = second_product(name, **cells)
    raw = deepcopy(RAW)
    raw['visa_products'].append(dict(deepcopy(extra), field_provenance=None, source_url=None, source_quote=None,
                                     verified_at=None, verifier=None))
    seed = deepcopy(SEED)
    seed['fields']['visa_products'].append(extra)
    return layer(raw=raw, seed=seed)


def proof(*quotes, sid='ru_aus_tourist', url=URL):
    return {'status': 'reviewed', 'verifier': 'ai', 'verified_at': '2026-09-11',
            'scope_note': 'Australian ordinary passport, tourism, the tourist visa product.',
            'evidence': [{'source_id': sid, 'source_url': url, 'quote': q} for q in quotes]}


def absence(reason='The tourist visa page states no fee.', source_ids=('ru_aus_tourist',), verified_at='2026-09-11'):
    return {'status': 'not_published', 'verifier': 'ai', 'reason': reason, 'verified_at': verified_at,
            'source_ids': list(source_ids)}


def product_fill(field, value, proof_, product_type='Tourist visa'):
    return {'target': 'product', 'product_type': product_type, 'field': field, 'value': value, 'proof': proof_}


def route_fill(field, value, proof_):
    return {'target': 'route', 'field': field, 'value': value, 'proof': proof_}


VALIDITY = product_fill('validity', '30 days', proof('A tourist visa is valid for up to 30 days'))
SOURCES = [source('ru_aus_tourist', URL, TEXT), source('ru_aus_fees', FEES, FEE_TEXT),
           source('ru_aus_business', BUSINESS, BUSINESS_TEXT)]


def spec(*fills, current=None, sources=None):
    current = current or layer()
    return {'schema_version': 1, 'kind': 'reviewed_field_fill', 'id': 'field-fill-test',
            'sources': deepcopy(sources or SOURCES),
            'routes': [{'cache_key': current['cache_key'], 'route': deepcopy(current['route']),
                        'baseline_sha256': {k: digest(current[k]) for k in c.BASELINE_KEYS},
                        'fills': deepcopy(list(fills or [VALIDITY]))}]}


def run(*fills, current=None, **kw):
    current = current or layer()
    s = spec(*fills, current=current, **kw)
    manifest = c.build_manifest(s, [current])
    return c.convert(manifest, [current]), manifest, current


def before_rows(current):
    return t.records_for_route(current['route'], current['merged_guidance'], current['source_provenance'])


def test_the_fixture_serves_an_empty_validity_beside_a_government_source():
    row = before_rows(layer())[0]
    status = t.field_status(row)
    assert row['source_url'] == URL and row['confidence_level'] == 'Medium'
    assert status['validity_duration'] == 'missing' and status['visa_fee_amount'] == 'missing'


def test_a_quoted_validity_fill_projects_as_a_filled_validity():
    (overlay, report), manifest, current = run()
    assert overlay['kind'] == 'reviewed_overlay_conversion' and manifest['kind'] == c.MANIFEST_KIND
    assert len(overlay['entries']) == 1 and report['fills'] == 1 and report['absences'] == 0 and report['rejected'] == []
    preview = report['routes'][0]
    row = preview['records'][0]
    assert (row['validity_duration'], row['validity_unit']) == (30, 'Day')
    assert t.field_status(row)['validity_duration'] == 'filled'
    assert preview['changed_record_columns'] == ['validity_duration', 'validity_unit']
    product = preview['guidance']['visa_products'][0]
    assert product['validity'] == '30 days'
    proof_ = product['field_provenance']['validity']
    assert proof_['quote'] == 'A tourist visa is valid for up to 30 days' and proof_['source_url'] == URL
    assert proof_['verifier'] == 'ai' and proof_['verified_by'] == c.VERIFIED_BY
    assert proof_['verification_scope'] == c.SCOPE and proof_['subject']['product_type'] == 'Tourist visa'
    entry = overlay['entries'][0]
    assert entry['review_id'] == 'field-fill-test' and entry['partial_review'] is True
    assert not any(report[k] for k in ('raw_writes', 'operator_writes', 'issue_changes', 'renew_fresh_until', 'new_release'))
    # The overlay entry round-trips through the real store loader unchanged.
    parsed = vo._parse_rows([entry], {})[vo._key('AUS', 'RUS', 'tourism', 'ordinary_passport')]
    assert parsed['fields']['visa_products'][0]['validity'] == '30 days'
    applied = preview['fills'][0]
    assert applied['product_index'] == 0 and applied['grade_credited'] is True and applied['grade_reason'] is None


def test_the_emitted_overlay_passes_the_reviewed_store_gate(tmp_path):
    (overlay, report), manifest, current = run()
    path = tmp_path / 'overlay.json'
    path.write_text(json.dumps(overlay, ensure_ascii=False))
    errors = []
    rows = vo._read_verification_store(path, 'reviewed_overlay', reviewed=True, errors=errors)
    assert len(rows) == 1 and errors == []
    table = vo._parse_rows(rows, {})
    assert table[vo._key('AUS', 'RUS', 'tourism', 'ordinary_passport')]['fields']['visa_products'][0]['validity'] == '30 days'


def test_a_fill_never_changes_the_verdict_or_any_other_fact():
    (overlay, report), manifest, current = run()
    before, after = current['merged_guidance'], report['routes'][0]['guidance']
    assert after['disposition'] == before['disposition'] == 'VISA_REQUIRED'
    assert after['requirement_detail'] == before['requirement_detail'] == 'paper_visa'
    assert c._mask(before, ['visa_products']) == c._mask(after, ['visa_products'])
    old_product, new_product = before['visa_products'][0], after['visa_products'][0]
    assert c._mask_product(old_product, {'validity'}) == c._mask_product(new_product, {'validity'})
    assert new_product['field_provenance']['disposition'] == old_product['field_provenance']['disposition']
    old_row, new_row = before_rows(current)[0], report['routes'][0]['records'][0]
    assert new_row['visa_type_name'] == old_row['visa_type_name'] and new_row['visa_requirement'] == old_row['visa_requirement']
    assert new_row['confidence_level'] == old_row['confidence_level']
    assert report['confidence_changed'] is False and report['grade_changes'] == []


def test_a_quote_that_is_not_on_the_captured_page_is_rejected():
    fill = product_fill('validity', '30 days', proof('A tourist visa is valid for up to 30 days from the date of issue'))
    with pytest.raises(PatchRejected, match='not on its captured page'):
        run(fill)


def test_a_figure_missing_from_its_quote_is_rejected():
    fill = product_fill('validity', '90 days', proof('A tourist visa is valid for up to 30 days'))
    with pytest.raises(PatchRejected, match='validity'):
        run(fill)


def test_a_cell_that_already_holds_a_value_is_never_overwritten():
    fill = product_fill('entry', 'multiple', proof('is issued as a single-entry visa'))
    with pytest.raises(PatchRejected, match='already holds a value'):
        run(fill)
    stay = route_fill('permitted_stay_days', 30, proof('The holder may stay in Russia for up to 30 days.'))
    with pytest.raises(PatchRejected, match='already holds a value'):
        run(stay)


def test_a_fill_naming_a_missing_product_is_rejected():
    fill = product_fill('validity', '30 days', proof('A tourist visa is valid for up to 30 days'), product_type='Business visa')
    with pytest.raises(PatchRejected, match='Business visa'):
        run(fill)


def test_a_route_with_operator_entries_is_rejected():
    current = layer()
    current['operator_entries'] = [{'route': {}, 'fields': {}}]
    with pytest.raises(PatchRejected, match='Operator'):
        run(current=current)


# Binding the quote to the cell's subject (blocking finding 1).

def test_a_validity_is_not_taken_from_a_stay_sentence():
    fill = product_fill('validity', '30 days', proof('The holder may stay in Russia for up to 30 days.'))
    with pytest.raises(PatchRejected, match='names the product and states this value'):
        run(fill)


def test_a_validity_is_not_taken_from_another_products_sentence():
    current = two_product_layer()
    sources = SOURCES + [source('ru_aus_both', 'https://australia.mid.ru/en/consular_services/visas/',
                                'Visas. A tourist visa is valid for up to 30 days. A business visa is valid for up to 90 days.')]
    wrong = product_fill('validity', '90 days', proof('A business visa is valid for up to 90 days.', sid='ru_aus_both',
                                                       url='https://australia.mid.ru/en/consular_services/visas/'))
    with pytest.raises(PatchRejected, match='product Tourist visa validity: no quoted sentence names the product'):
        run(wrong, current=current, sources=sources)
    right = product_fill('validity', '30 days', proof('A tourist visa is valid for up to 30 days.', sid='ru_aus_both',
                                                       url='https://australia.mid.ru/en/consular_services/visas/'))
    (overlay, report), manifest, current = run(right, current=current, sources=sources)
    rows = report['routes'][0]['records']
    assert [(r['visa_type_name'], r['validity_duration']) for r in rows] == [('Tourist visa', 30), ('Business visa', None)]


def test_a_validity_stated_for_another_nationality_is_rejected():
    page = "Citizens of the People's Republic of China are issued tourist visas valid for up to 10 years."
    sources = SOURCES + [source('ru_chn', 'https://australia.mid.ru/en/consular_services/visas/china/', page)]
    fill = product_fill('validity', '10 years', proof(page, sid='ru_chn', url='https://australia.mid.ru/en/consular_services/visas/china/'))
    with pytest.raises(PatchRejected, match='about CHN, not AUS'):
        run(fill, sources=sources)


def test_a_validity_range_is_not_one_validity():
    page = 'A tourist visa is issued with a validity of 3 to 6 months.'
    sources = SOURCES + [source('ru_range', 'https://australia.mid.ru/en/consular_services/visas/range/', page)]
    fill = product_fill('validity', '6 months', proof(page, sid='ru_range', url='https://australia.mid.ru/en/consular_services/visas/range/'))
    with pytest.raises(PatchRejected, match='range or a choice'):
        run(fill, sources=sources)


def test_a_sentence_about_another_entry_type_cannot_fill_this_product():
    page = 'A multiple-entry tourist visa is valid for up to 180 days.'
    sources = SOURCES + [source('ru_multi', 'https://australia.mid.ru/en/consular_services/visas/multi/', page)]
    fill = product_fill('validity', '180 days', proof(page, sid='ru_multi', url='https://australia.mid.ru/en/consular_services/visas/multi/'))
    with pytest.raises(PatchRejected, match="qualified by a multiple entry, not the subject's single"):
        run(fill, sources=sources)


def test_the_scope_note_never_stands_in_for_the_sentence():
    fill = product_fill('validity', '30 days', proof('The holder may stay in Russia for up to 30 days.'))
    fill['proof']['scope_note'] = 'The tourist visa validity for Australian citizens is 30 days.'
    with pytest.raises(PatchRejected, match='names the product and states this value'):
        run(fill)


# required_documents (blocking finding 2).

def test_documents_the_quote_never_mentions_are_rejected():
    invented = ['Valid passport', 'Bank statement showing at least A$10,000', 'Police clearance certificate issued within 3 months',
                'HIV test certificate', 'Return air ticket paid in full']
    fill = route_fill('required_documents', invented, proof('Documents required: a valid passport, a completed visa application form, one photo,'))
    with pytest.raises(PatchRejected, match='no quoted sentence states the document "Bank statement'):
        run(fill)


# permitted_stay (blocking finding 3).

def stayless_layer():
    """The fixture with no stay anywhere: neither the route nor the product."""
    raw = deepcopy(RAW)
    raw.pop('permitted_stay'); raw.pop('permitted_stay_days')
    raw['visa_products'][0]['max_stay_days'] = None
    seed = deepcopy(SEED)
    seed['fields']['visa_products'][0]['max_stay_days'] = None
    return layer(raw=raw, seed=seed)


def test_a_digit_free_stay_statement_needs_its_own_stay_sentence():
    current = stayless_layer()
    fill = route_fill('permitted_stay', 'Stay of up to six months per entry, renewable locally',
                      proof('Documents required: a valid passport, a completed visa application form, one photo,'))
    with pytest.raises(PatchRejected, match='route permitted_stay: no quoted sentence states this value'):
        run(fill, current=current)


def test_a_stay_is_not_taken_from_a_validity_sentence():
    current = stayless_layer()
    fill = route_fill('permitted_stay', 'Up to 30 days', proof('A tourist visa is valid for up to 30 days'))
    with pytest.raises(PatchRejected, match='route permitted_stay: no quoted sentence states this value'):
        run(fill, current=current)
    page = 'A tourist visa is valid for up to 30 days and the holder may stay for up to 15 days.'
    sources = SOURCES + [source('ru_mixed', 'https://australia.mid.ru/en/consular_services/visas/mixed/', page)]
    mixed = route_fill('permitted_stay', 'Up to 30 days', proof(page, sid='ru_mixed', url='https://australia.mid.ru/en/consular_services/visas/mixed/'))
    with pytest.raises(PatchRejected, match='not bound to a stay word'):
        run(mixed, current=current, sources=sources)
    good = route_fill('permitted_stay', 'Up to 30 days', proof('The holder may stay in Russia for up to 30 days.'))
    (overlay, report), manifest, current = run(good, current=current)
    row = report['routes'][0]['records'][0]
    assert (row['max_stay_duration'], row['max_stay_unit']) == (30, 'Day')


# max_stay_days / permitted_stay_days (blocking finding 4).

def test_a_months_quote_never_fills_a_days_cell():
    page = 'A tourist visa holder may stay in the Russian Federation for up to 3 months.'
    sources = SOURCES + [source('ru_months', 'https://australia.mid.ru/en/consular_services/visas/months/', page)]
    current = stayless_layer()
    fill = product_fill('max_stay_days', 3, proof(page, sid='ru_months', url='https://australia.mid.ru/en/consular_services/visas/months/'))
    with pytest.raises(PatchRejected, match='does not stand beside a day word'):
        run(fill, current=current, sources=sources)
    page = 'A tourist visa holder may stay in the Russian Federation for up to 30 days.'
    sources = SOURCES + [source('ru_days', 'https://australia.mid.ru/en/consular_services/visas/days/', page)]
    good = product_fill('max_stay_days', 30, proof(page, sid='ru_days', url='https://australia.mid.ru/en/consular_services/visas/days/'))
    (overlay, report), manifest, current = run(good, current=current, sources=sources)
    row = report['routes'][0]['records'][0]
    assert (row['max_stay_duration'], row['max_stay_unit']) == (30, 'Day')


# Currency (blocking finding 5).

HK_URL = 'https://www.immd.gov.hk/eng/services/visas/visit-transit/visit-visa-entry-permit.html'
HK_TEXT = ('Immigration Department. Visit visa / entry permit. Australian nationals require a visit visa to enter Hong Kong. '
           'The fee for a visit visa is $230 payable on collection. '
           'A visit visa holder may stay in Hong Kong for up to 30 days.')


def hong_kong_layer():
    route = dict(ROUTE, destination_country='HKG')
    decision = dict(DECISION, source_id='hk_visit', source_url=HK_URL,
                    quote='Australian nationals require a visit visa to enter Hong Kong.')
    subject = dict(SUBJECT, destination_country='HKG')
    product = dict(deepcopy(PRODUCT), type='Visit visa', source_url=HK_URL, source_quote=decision['quote'],
                   field_provenance={'disposition': dict(decision, subject=dict(subject, product_type='Visit visa',
                                                                                 disposition='VISA_REQUIRED',
                                                                                 requirement_detail='paper_visa'))})
    raw = dict(deepcopy(RAW), visa_category='Visit visa',
               application_channel_detail='Lodge the application with the Immigration Department.',
               exceptions=[], visa_products=[dict(deepcopy(product), field_provenance=None, source_url=None,
                                                  source_quote=None, verified_at=None, verifier=None)])
    seed = {'route': {'nationality': 'AUS', 'destination': 'HKG', 'travel_purpose': 'tourism',
                      'travel_document_type': 'ordinary_passport'},
            'verified_at': '2026-09-01', 'verified_by': 'Ellis AI official-source field review', 'verifier': 'ai',
            'source_url': HK_URL, 'note': 'Australian ordinary passport, tourism.',
            'fields': {'disposition': 'VISA_REQUIRED', 'requirement_detail': 'paper_visa',
                       'visa_products': [product], 'source_url': HK_URL},
            'field_provenance': {'disposition': dict(decision, subject=subject)}}
    return build_layer(route, 'AUS|AUS|HKG|tourism|default|unknown|v6', raw, seed)


def test_a_bare_dollar_on_a_hong_kong_page_is_not_a_us_dollar():
    current = hong_kong_layer()
    sources = [source('hk_visit', HK_URL, HK_TEXT)]
    quote = 'The fee for a visit visa is $230 payable on collection.'
    wrong = product_fill('fee', {'amount': 230, 'currency': 'USD'}, proof(quote, sid='hk_visit', url=HK_URL), product_type='Visit visa')
    with pytest.raises(PatchRejected, match='prices in HKD, not USD'):
        run(wrong, current=current, sources=sources)
    right = product_fill('fee', {'amount': 230, 'currency': 'HKD'}, proof(quote, sid='hk_visit', url=HK_URL), product_type='Visit visa')
    (overlay, report), manifest, current = run(right, current=current, sources=sources)
    row = report['routes'][0]['records'][0]
    assert (row['visa_fee_amount'], row['visa_fee_currency']) == (230, 'HKD')


def test_a_bare_dollar_on_a_page_whose_destination_has_no_dollar_is_ambiguous():
    page = 'The consular fee for a tourist visa is $150 per application.'
    sources = SOURCES + [source('ru_bare', 'https://australia.mid.ru/en/consular_services/fees/bare/', page)]
    fill = product_fill('fee', {'amount': 150, 'currency': 'AUD'}, proof(page, sid='ru_bare', url='https://australia.mid.ru/en/consular_services/fees/bare/'))
    with pytest.raises(PatchRejected, match='ambiguous'):
        run(fill, sources=sources)
    page = 'The consular fee for a tourist visa is $150 per application, payable in Australian dollars.'
    sources = SOURCES + [source('ru_named', 'https://australia.mid.ru/en/consular_services/fees/named/', page)]
    named = product_fill('fee', {'amount': 150, 'currency': 'AUD'}, proof(page, sid='ru_named', url='https://australia.mid.ru/en/consular_services/fees/named/'))
    (overlay, report), manifest, current = run(named, sources=sources)
    assert report['routes'][0]['records'][0]['visa_fee_amount'] == 150


def test_a_two_currency_sentence_binds_the_marker_beside_the_amount():
    page = 'The consular fee for a tourist visa is A$150 per application, or US$300 for applications lodged from abroad.'
    url = 'https://australia.mid.ru/en/consular_services/fees/two/'
    sources = SOURCES + [source('ru_two', url, page)]
    product_wrong = product_fill('fee', {'amount': 300, 'currency': 'AUD'}, proof(page, sid='ru_two', url=url))
    with pytest.raises(PatchRejected):
        run(product_wrong, sources=sources)
    route_wrong = route_fill('government_fee', {'amount': 300, 'currency': 'AUD'}, proof(page, sid='ru_two', url=url))
    with pytest.raises(PatchRejected, match='prices in USD, not AUD'):
        run(route_wrong, sources=sources)
    # The binder itself accepts each amount only in the currency written
    # beside it. (A route-level fee cannot surface on the fixture's product
    # row, which carries its own empty fee cell, so the binder is called
    # directly here.)
    current = layer()
    for amount, code in ((300, 'USD'), (150, 'AUD')):
        c._check_fill_binding('government_fee', {'amount': amount, 'currency': code}, proof(page, sid='ru_two', url=url),
                              current['route'], current['merged_guidance'], None, 'route government_fee')
    with pytest.raises(PatchRejected, match='prices in AUD, not USD'):
        c._check_fill_binding('government_fee', {'amount': 150, 'currency': 'USD'}, proof(page, sid='ru_two', url=url),
                              current['route'], current['merged_guidance'], None, 'route government_fee')


def test_a_fee_fill_with_a_currency_symbol_quote_passes_the_monetary_handling():
    fill = product_fill('fee', {'amount': 150, 'currency': 'AUD'},
                        proof('The consular fee for a tourist visa is A$150 per application', sid='ru_aus_fees', url=FEES))
    (overlay, report), manifest, current = run(fill)
    row = report['routes'][0]['records'][0]
    assert (row['visa_fee_amount'], row['visa_fee_currency']) == (150, 'AUD')
    assert t.field_status(row)['visa_fee_amount'] == 'filled'
    product = report['routes'][0]['guidance']['visa_products'][0]
    assert product['fee'] == {'amount': 150, 'currency': 'AUD'}
    assert product['field_provenance']['fee']['source_url'] == FEES
    wrong = product_fill('fee', {'amount': 150, 'currency': 'USD'},
                         proof('The consular fee for a tourist visa is A$150 per application', sid='ru_aus_fees', url=FEES))
    with pytest.raises(PatchRejected, match='prices in AUD, not USD'):
        run(wrong)


# Government fee only (blocking finding 6).

def test_a_service_charge_is_never_the_government_fee():
    quote = 'The visa centre also charges a service fee of A$62 per application, which is not a consular fee.'
    route_service = route_fill('government_fee', {'amount': 62, 'currency': 'AUD'}, proof(quote, sid='ru_aus_business', url=BUSINESS))
    with pytest.raises(PatchRejected, match='service, agency, centre'):
        run(route_service)
    page = 'A tourist visa application also carries a VFS service fee of A$62.'
    sources = SOURCES + [source('ru_vfs', 'https://australia.mid.ru/en/consular_services/fees/vfs/', page)]
    product_service = product_fill('fee', {'amount': 62, 'currency': 'AUD'}, proof(page, sid='ru_vfs', url='https://australia.mid.ru/en/consular_services/fees/vfs/'))
    with pytest.raises(PatchRejected, match='service, agency, centre'):
        run(product_service, sources=sources)
    page = 'Tourist visa: A$150 per application.'
    sources = SOURCES + [source('ru_bare_fee', 'https://australia.mid.ru/en/consular_services/fees/bare2/', page)]
    unnamed = product_fill('fee', {'amount': 150, 'currency': 'AUD'}, proof(page, sid='ru_bare_fee', url='https://australia.mid.ru/en/consular_services/fees/bare2/'))
    with pytest.raises(PatchRejected):
        run(unnamed, sources=sources)


# Documented absences (blocking finding 7 and the minor date finding).

def test_a_documented_absence_writes_unpublished_fields_and_reads_not_published():
    fill = product_fill('fee', None, absence())
    (overlay, report), manifest, current = run(fill)
    assert report['fills'] == 0 and report['absences'] == 1
    product = report['routes'][0]['guidance']['visa_products'][0]
    assert product['unpublished_fields'] == ['visa_fee_amount', 'visa_fee_currency']
    assert product['fee'] == {'amount': None, 'currency': None}
    proof_ = product['field_provenance']['fee']
    assert proof_['status'] == 'unknown' and proof_['verified_at'] == '2026-09-11'
    assert proof_['reason'] == ('Not published by the destination: The tourist visa page states no fee. '
                                'Checked on 2026-09-11: ' + URL + '.')
    assert proof_['checked_source_urls'] == [URL] and proof_['verification_scope'] == c.SCOPE
    row = report['routes'][0]['records'][0]
    status = t.field_status(row)
    assert status['visa_fee_amount'] == 'not-published' and status['visa_fee_currency'] == 'not-published'
    applied = report['routes'][0]['fills'][0]
    assert applied['kind'] == 'absence' and applied['checked_source_urls'] == [URL]
    assert report['routes'][0]['documented_absences'] == ['fee']


def test_a_route_level_absence_carries_its_date_and_pages_in_the_stored_note():
    fill = route_fill('government_fee', None, absence('The tourist visa page states no government fee.'))
    (overlay, report), manifest, current = run(fill)
    note = overlay['entries'][0]['field_provenance']['government_fee']['note']
    assert note == ('Not published by the destination: The tourist visa page states no government fee. '
                    'Checked on 2026-09-11: ' + URL + '.')
    assert t.field_status(report['routes'][0]['records'][0])['visa_fee_amount'] == 'not-published'


def test_an_absence_over_a_page_that_states_the_value_is_rejected():
    fee = product_fill('fee', None, absence('Neither page states it.', source_ids=('ru_aus_tourist', 'ru_aus_fees')))
    with pytest.raises(PatchRejected, match='names ' + FEES + ', which states a value: "The consular fee for a tourist visa is A\\$150'):
        run(fee)
    validity = product_fill('validity', None, absence('No validity is stated.'))
    with pytest.raises(PatchRejected, match='which states a value: "A tourist visa is valid for up to 30 days'):
        run(validity)


def test_an_absence_over_an_unrelated_page_is_rejected():
    fill = product_fill('fee', None, absence('The business page states no tourist fee.', source_ids=('ru_aus_business',)))
    with pytest.raises(PatchRejected, match='none of the pages checked mentions the product Tourist visa'):
        run(fill)


def test_an_absence_must_name_captured_destination_pages_and_a_date():
    with pytest.raises(PatchRejected, match='captured page ids'):
        run(product_fill('fee', None, absence(source_ids=())))
    with pytest.raises(PatchRejected, match='captured page ids'):
        run(product_fill('fee', None, absence(source_ids=('nowhere',))))
    with pytest.raises(PatchRejected, match='invalid review date'):
        run(product_fill('fee', None, absence(verified_at=None)))
    with pytest.raises(PatchRejected, match='review date is in the future'):
        run(product_fill('fee', None, absence(verified_at='2999-01-01')))
    foreign = source('au_smartraveller', 'https://www.smartraveller.gov.au/destinations/europe/russia', 'Russia travel advice.')
    with pytest.raises(PatchRejected, match='destination-government page'):
        run(product_fill('fee', None, absence(source_ids=('au_smartraveller',))), sources=SOURCES + [foreign])
    legacy = {'status': 'not_published', 'verifier': 'ai', 'reason': 'Checked ' + URL + ': nothing.'}
    with pytest.raises(PatchRejected):
        run(product_fill('fee', None, legacy))


def test_a_route_level_document_list_fills_the_product_row():
    docs = ['Valid passport', 'Completed visa application form', 'One photo',
            'Tourist confirmation from a registered Russian tour operator', 'Medical insurance policy']
    fill = route_fill('required_documents', docs, proof(
        'Documents required: a valid passport, a completed visa application form, one photo, '
        'a tourist confirmation from a registered Russian tour operator and a medical insurance policy.'))
    (overlay, report), manifest, current = run(fill)
    row = report['routes'][0]['records'][0]
    assert row['required_documents'].startswith('Valid passport, Completed visa application form')
    entry = overlay['entries'][0]
    assert entry['fields']['required_documents'] == docs
    assert entry['field_provenance']['required_documents']['verification_scope'] == c.SCOPE
    assert report['routes'][0]['filled_fields'] == ['required_documents']


def test_a_baseline_sha_mismatch_is_rejected():
    current = layer()
    s = spec(current=current)
    s['routes'][0]['baseline_sha256']['merged_guidance'] = 'f' * 64
    with pytest.raises(PatchRejected, match='baseline'):
        c.build_manifest(s, [current])
    drifted = layer()
    drifted['raw_guidance']['processing_time'] = '12 working days'
    with pytest.raises(PatchRejected, match='baseline'):
        c.build_manifest(spec(current=layer()), [drifted])


def test_duplicate_fills_on_one_cell_are_rejected():
    with pytest.raises(PatchRejected, match='Duplicate'):
        run(VALIDITY, deepcopy(VALIDITY))


@pytest.mark.parametrize('mode', ['extra_key', 'unknown_field', 'unknown_status', 'foreign_page', 'missing_scope',
                                  'future_date', 'unofficial_source', 'zero_fee', 'ambiguous_validity', 'scope_key'])
def test_tampering_rejects(mode):
    current = layer()
    s = spec(current=current)
    fill = s['routes'][0]['fills'][0]
    if mode == 'extra_key':
        fill['confidence'] = 'high'
    elif mode == 'unknown_field':
        fill['field'] = 'notes'
    elif mode == 'unknown_status':
        fill['proof'] = {'status': 'unknown', 'verifier': 'ai', 'reason': 'No quote found on ' + URL}
    elif mode == 'foreign_page':
        s['sources'].append(source('au_smartraveller', 'https://www.smartraveller.gov.au/destinations/europe/russia', TEXT))
        fill['proof']['evidence'] = [{'source_id': 'au_smartraveller', 'quote': 'A tourist visa is valid for up to 30 days',
                                      'source_url': 'https://www.smartraveller.gov.au/destinations/europe/russia'}]
    elif mode == 'missing_scope':
        fill['proof']['scope_note'] = ''
    elif mode == 'future_date':
        fill['proof']['verified_at'] = '2999-01-01'
    elif mode == 'unofficial_source':
        s['sources'][0]['url'] = 'https://www.wikipedia.org/russia'
        fill['proof']['evidence'][0]['source_url'] = s['sources'][0]['url']
    elif mode == 'zero_fee':
        s['routes'][0]['fills'] = [product_fill('fee', {'amount': 0, 'currency': 'AUD'},
                                                proof('The consular fee for a tourist visa is A$150 per application',
                                                      sid='ru_aus_fees', url=FEES))]
    elif mode == 'ambiguous_validity':
        fill['value'] = '30 days or 90 days'
    elif mode == 'scope_key':
        fill['proof']['verification_scope'] = 'consular_product_eligibility'
    with pytest.raises(PatchRejected):
        c.convert(c.build_manifest(s, [current]), [current])


def test_a_product_table_the_seed_does_not_own_cannot_take_product_fills():
    seed = deepcopy(SEED)
    seed['fields'].pop('visa_products')
    current = layer(seed=seed)
    with pytest.raises(PatchRejected, match='not owned'):
        run(current=current)


def test_the_prepared_manifest_and_overlay_rebuild_exactly():
    (overlay, report), manifest, current = run()
    assert c.verify_prepared(manifest['specification'], [current], manifest, overlay)['fills'] == 1
    stale = deepcopy(manifest)
    stale['kind'] = 'field_corrections_exact_layers'
    with pytest.raises(PatchRejected, match='Prepared manifest changed'):
        c.convert(stale, [current])
    drifted = deepcopy(manifest)
    drifted['routes'][0]['baseline']['raw_guidance']['processing_time'] = '12 working days'
    with pytest.raises(PatchRejected, match='baseline'):
        c.convert(drifted, [current])


# Majors: the store gate, the channel family, triage and the CLI report.

def test_a_seed_citing_another_governments_page_is_rejected_before_release():
    foreign = 'https://www.smartraveller.gov.au/destinations/europe/russia'
    seed = deepcopy(SEED)
    seed['source_url'] = foreign
    seed['fields']['source_url'] = foreign
    current = layer(seed=seed)
    with pytest.raises(PatchRejected, match='Entry source_url is not a RUS government page'):
        run(current=current)


def test_an_application_channel_cannot_contradict_the_served_verdict():
    raw = deepcopy(RAW)
    raw.pop('application_channel'); raw.pop('application_channel_detail')
    current = layer(raw=raw)
    page = 'A tourist visa is issued on arrival at the airport.'
    sources = SOURCES + [source('ru_arrival', 'https://australia.mid.ru/en/consular_services/visas/arrival/', page)]
    fill = route_fill('application_channel', 'on_arrival', proof(page, sid='ru_arrival', url='https://australia.mid.ru/en/consular_services/visas/arrival/'))
    with pytest.raises(PatchRejected, match='on_arrival contradicts the served VISA_REQUIRED verdict'):
        run(fill, current=current, sources=sources)
    good = route_fill('application_channel', 'embassy_or_consulate',
                      proof('Applications are lodged at the Embassy in Canberra or the Consulate General in Sydney.'))
    (overlay, report), manifest, current = run(good, current=current)
    assert report['routes'][0]['guidance']['application_channel'] == 'embassy_or_consulate'
    assert c.CHANNELS_BY_DISPOSITION['VISA_ON_ARRIVAL'] == {'on_arrival'}
    assert 'on_arrival' not in c.CHANNELS_BY_DISPOSITION['VISA_REQUIRED']
    assert 'VISA_EXEMPT' not in c.CHANNELS_BY_DISPOSITION


def test_triage_keeps_the_good_fills_and_names_every_rejection():
    current = layer()
    bad_quote = product_fill('required_documents', ['Valid passport'], proof('This sentence is not on the page.'))
    taken = product_fill('entry', 'multiple', proof('is issued as a single-entry visa'))
    fee_absence = product_fill('fee', None, absence())
    s = spec(VALIDITY, bad_quote, taken, fee_absence, current=current)
    kept, kept_layers, report = c.triage(s, [current])
    assert [f['field'] for f in kept['routes'][0]['fills']] == ['validity', 'fee'] and kept_layers == [current]
    assert [(r['field'], r['reason'].split(':')[0]) for r in report['rejected']] == [
        ('required_documents', 'product Tourist visa required_documents'), ('entry', 'product Tourist visa entry')]
    assert 'not on its captured page' in report['rejected'][0]['reason']
    assert 'already holds a value' in report['rejected'][1]['reason']
    assert [a['field'] for a in report['accepted']] == ['validity'] and [a['field'] for a in report['absences']] == ['fee']
    overlay, converted = c.convert(c.build_manifest(kept, kept_layers), kept_layers)
    assert converted['fills'] == 1 and converted['absences'] == 1
    missing = c.triage(spec(current=current), [])[2]
    assert missing['rejected'][0]['reason'] == 'Current layer missing'


def test_triage_names_rows_it_cannot_read_instead_of_dropping_them():
    current = layer()
    s = spec(current=current)
    good = deepcopy(s['routes'][0])
    s['routes'] = [dict(good, fills={'validity': VALIDITY}), dict(good, fills=[]), 'not a row', good, deepcopy(good)]
    kept, kept_layers, report = c.triage(s, [current])
    assert len(kept['routes']) == 1 and kept_layers == [current] and [a['field'] for a in report['accepted']] == ['validity']
    assert [(r['cache_key'], r['field'], r['reason']) for r in report['rejected']] == [
        (KEY, None, 'fills must be a list'), (KEY, None, 'Each context needs at least one fill'),
        (None, None, 'Route row is not an object'), (KEY, 'validity', 'Duplicate route row: ' + KEY)]
    c.convert(c.build_manifest(kept, kept_layers), kept_layers)


def test_the_cli_writes_its_report_even_when_conversion_raises(tmp_path, monkeypatch):
    current = layer()
    s = spec(current=current)
    (tmp_path / 'spec.json').write_text(json.dumps(s))
    (tmp_path / 'layers.json').write_text(json.dumps({'layers': [current]}))
    out = tmp_path / 'out'
    report = c.main([str(tmp_path / 'spec.json'), str(tmp_path / 'layers.json'), str(out)])
    assert report['fills'] == 1 and (out / 'overlay.json').is_file() and (out / 'report.json').is_file()

    def boom(manifest, layers):
        raise PatchRejected('boom')
    monkeypatch.setattr(c, 'triage', lambda spec_, layers_: (s, [current], dict(accepted=[], absences=[], rejected=[])))
    monkeypatch.setattr(c, 'convert', boom)
    failed = tmp_path / 'failed'
    with pytest.raises(SystemExit, match='boom'):
        c.main([str(tmp_path / 'spec.json'), str(tmp_path / 'layers.json'), str(failed)])
    written = json.loads((failed / 'report.json').read_text())
    assert written['conversion_error'] == 'boom' and written['fills'] == 0 and not (failed / 'overlay.json').exists()


# Minors: the reach check binds by product index, the grader's refusal is explained.

def test_the_reach_check_judges_the_fills_own_product_row():
    current = two_product_layer()
    (overlay, report), manifest, current = run(current=current)
    before, after = before_rows(current), report['routes'][0]['records']
    applied = deepcopy(report['routes'][0]['fills'])
    assert applied[0]['product_index'] == 0
    c._reaches(applied, before, after, KEY)
    applied[0]['product_index'] = 1
    with pytest.raises(PatchRejected, match='does not reach any served cell'):
        c._reaches(applied, before, after, KEY)


def test_an_uncredited_fill_says_why_the_grader_refused_it():
    fill = product_fill('fee', {'amount': 150, 'currency': 'AUD'},
                        proof('The consular fee for a tourist visa is A$150 per application', sid='ru_aus_fees', url=FEES))
    (overlay, report), manifest, current = run(VALIDITY, fill)
    by_field = {a['field']: a for a in report['routes'][0]['fills']}
    assert by_field['validity']['grade_credited'] is True and by_field['validity']['grade_reason'] is None
    assert by_field['fee']['grade_credited'] is False
    assert 'ISO code beside the amount' in by_field['fee']['grade_reason']
    absent = product_fill('fee', None, absence())
    (overlay, report), manifest, current = run(absent)
    assert report['routes'][0]['fills'][0]['grade_credited'] is None
