"""A reviewed field fill writes only into empty required cells, with its own quote.

The fixture is the owner's example: an Australian ordinary passport to Russia
for tourism, served with a government source_url and a tourist visa product
whose validity, fee and document list are still empty although the embassy
page states them.
"""
from copy import deepcopy
import hashlib

import pytest

from scripts import convert_reviewed_field_fill as c
from scripts.prepare_reviewed_product_patch import PatchRejected, digest
from app.visa_snapshot import tstation as t, verified_overrides as vo

URL = 'https://australia.mid.ru/en/consular_services/visas/tourist/'
FEES = 'https://australia.mid.ru/en/consular_services/fees/'
TEXT = ('Embassy of the Russian Federation in Australia. Tourist visa. '
        'Australian citizens need a visa to enter the Russian Federation. '
        'A tourist visa is valid for up to 30 days and is issued as a single-entry visa. '
        'The holder may stay in Russia for up to 30 days. '
        'Documents required: a valid passport, a completed visa application form, one photo, '
        'a tourist confirmation from a registered Russian tour operator and a medical insurance policy. '
        'Applications are lodged at the Embassy in Canberra or the Consulate General in Sydney.')
FEE_TEXT = ('Consular fees. The consular fee for a tourist visa is A$150 per application, '
            'payable at the Embassy in Australian dollars.')
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


def layer(raw=None, seed=None):
    """The six layers exactly as the production reader builds them."""
    raw = deepcopy(RAW if raw is None else raw)
    seed = deepcopy(SEED if seed is None else seed)
    identity = vo._key('AUS', 'RUS', 'tourism', 'ordinary_passport')
    prior = vo._parse_rows([deepcopy(seed)], {})[identity]
    merged, checked = vo.merge_verified_fields(deepcopy(raw), prior['fields'], source_url=prior['source_url'])
    prov = dict(prior['field_provenance'].get('disposition') or vo._provenance(prior), fields=sorted(checked),
                field_provenance=prior['field_provenance'])
    return {'cache_key': KEY, 'route': deepcopy(ROUTE), 'raw_guidance': raw, 'merged_guidance': merged,
            'source_provenance': prov, 'seed_entries': [seed], 'operator_entries': []}


def proof(*quotes, sid='ru_aus_tourist', url=URL):
    return {'status': 'reviewed', 'verifier': 'ai', 'verified_at': '2026-09-11',
            'scope_note': 'Australian ordinary passport, tourism, the tourist visa product.',
            'evidence': [{'source_id': sid, 'source_url': url, 'quote': q} for q in quotes]}


def absence(reason='Checked ' + URL + ' and ' + FEES + ': neither page states it.'):
    return {'status': 'not_published', 'verifier': 'ai', 'reason': reason}


def product_fill(field, value, proof_, product_type='Tourist visa'):
    return {'target': 'product', 'product_type': product_type, 'field': field, 'value': value, 'proof': proof_}


def route_fill(field, value, proof_):
    return {'target': 'route', 'field': field, 'value': value, 'proof': proof_}


VALIDITY = product_fill('validity', '30 days', proof('A tourist visa is valid for up to 30 days'))


def spec(*fills, current=None, sources=None):
    current = current or layer()
    return {'schema_version': 1, 'kind': 'reviewed_field_fill', 'id': 'field-fill-test',
            'sources': sources or [source('ru_aus_tourist', URL, TEXT), source('ru_aus_fees', FEES, FEE_TEXT)],
            'routes': [{'cache_key': KEY, 'route': deepcopy(current['route']),
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


def test_a_documented_absence_writes_unpublished_fields_and_reads_not_published():
    fill = product_fill('fee', None, absence())
    (overlay, report), manifest, current = run(fill)
    assert report['fills'] == 0 and report['absences'] == 1
    product = report['routes'][0]['guidance']['visa_products'][0]
    assert product['unpublished_fields'] == ['visa_fee_amount', 'visa_fee_currency']
    assert product['fee'] == {'amount': None, 'currency': None}
    assert product['field_provenance']['fee']['status'] == 'unknown'
    assert product['field_provenance']['fee']['reason'].startswith('Not published by the destination: Checked')
    row = report['routes'][0]['records'][0]
    status = t.field_status(row)
    assert status['visa_fee_amount'] == 'not-published' and status['visa_fee_currency'] == 'not-published'
    assert report['routes'][0]['documented_absences'] == ['fee']


def test_an_absence_must_name_a_captured_destination_page():
    fill = product_fill('fee', None, absence('Looked everywhere, nothing.'))
    with pytest.raises(PatchRejected, match='name a captured'):
        run(fill)


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
