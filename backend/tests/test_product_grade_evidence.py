"""Display-grade regressions; detached data only, never source certification."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.visa_snapshot import tstation as t

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/product_grade_usa_ind_current.json').read_text())


def complete_case(*, illustrative_field_quotes=True):
    layer = deepcopy(FIXTURE)
    # Existing reproduction: only close the last metadata blank, independently
    # of the seven required filled policy fields under this test.
    for product in layer['merged_guidance']['visa_products']:
        product['field_provenance']['policy_valid_until'] = {
            'status': 'not_published', 'verifier': 'ai',
            'reason': 'Detached test-only documented absence; never installed.'}
    if illustrative_field_quotes:
        # Controlled matcher positives, NOT fresh real-world source reviews.
        # Actual captured wrappers below remain separately tested as unproven.
        for product in layer['merged_guidance']['visa_products']:
            for field in ('type', 'entry', 'validity', 'max_stay_days', 'fee',
                          'application_channel', 'application_channel_detail', 'required_documents', 'permitted_stay'):
                if field not in product or product[field] is None:
                    continue
                value = product[field]
                text = str(value)
                if field == 'required_documents':
                    text = '\n'.join('Required document: ' + item for item in value)
                elif field == 'fee': text = f"Government fee: {value['currency']} {value['amount']}"
                elif field == 'application_channel': text = 'Visa applications must be submitted online.'
                elif field == 'max_stay_days': text = f'Maximum stay is {value} days.'
                elif field == 'validity': text = 'Validity: ' + value
                product['field_provenance'].setdefault(field, deepcopy(product['field_provenance']['type'])).update(quote=text, note='Illustrative field-specific test quotation; not installed.')
        docs = layer['merged_guidance']['required_documents']
        layer['source_provenance']['field_provenance']['required_documents'].update(
            quote='\n'.join('Required document: ' + item for item in docs), note='Illustrative parent document proof; not installed.')
    return layer


def rows(layer):
    return t.records_for_route(layer['route'], layer['merged_guidance'], layer['source_provenance'])


def proof(layer, product, field):
    result = deepcopy(product['field_provenance'][field])
    result.update(status='reviewed', subject={
        key: layer['route'].get(key) for key in
        ('passport_nationality', 'destination_country', 'travel_purpose', 'travel_document_type')})
    result['subject'].update(product_type=product['type'], disposition=product.get('disposition'),
                             requirement_detail=product.get('requirement_detail'))
    return result


def test_field_specific_qualified_proofs_can_grade_high_when_complete():
    layer = complete_case(); before = deepcopy(layer)
    result = rows(layer)
    assert [r['confidence_level'] for r in result] == ['High'] * 4
    assert layer == before
    assert all(not r['_evidence_low'] for r in result)
    assert all(not any(k.startswith('_grade_') for k in r) for r in result)


def test_documented_absence_cannot_certify_unrelated_unknown_documents():
    layer = complete_case(); old = rows(layer)
    for product in layer['merged_guidance']['visa_products']:
        product['required_documents'] = ['A deliberately unverified test-only document']
        product['field_provenance']['required_documents'] = {'status': 'unknown', 'reason': 'Not verified'}
    result = rows(layer)
    assert [r['confidence_level'] for r in result] == ['Low'] * 4
    assert all(r['required_documents'] == 'A deliberately unverified test-only document' for r in result)
    assert [r['_evidence_low'] for r in result] == [r['_evidence_low'] for r in old]
    assert all(t.completeness(r) == 1 for r in result)


@pytest.mark.parametrize('field', ['type', 'entry', 'validity', 'max_stay_days', 'fee',
                                  'application_channel', 'application_channel_detail', 'required_documents'])
@pytest.mark.parametrize('status', ['unknown', 'not_published', 'partial'])
def test_explicit_product_field_disposition_cannot_borrow_container_credit(field, status):
    layer = complete_case(); product = layer['merged_guidance']['visa_products'][0]
    product['field_provenance'][field]['status'] = status
    result = rows(layer)
    assert result[0]['confidence_level'] == 'Low'
    assert [r['confidence_level'] for r in result[1:]] == ['High'] * 3
    assert not result[0]['_evidence_low']


@pytest.mark.parametrize('mutation', ['foreign_subject', 'wrong_product', 'wrong_purpose', 'foreign_source',
                                    'future_review', 'malformed', 'public', 'empty_note'])
def test_explicit_owned_documents_require_current_qualified_scope(mutation):
    layer = complete_case(); product = layer['merged_guidance']['visa_products'][0]
    p = proof(layer, product, 'required_documents')
    if mutation == 'foreign_subject': p['subject']['passport_nationality'] = 'CHN'
    elif mutation == 'wrong_product': p['subject']['product_type'] = 'Paper tourist visa'
    elif mutation == 'wrong_purpose': p['subject']['travel_purpose'] = 'business'
    elif mutation == 'foreign_source': p['source_url'] = 'https://immi.homeaffairs.gov.au/visas'
    elif mutation == 'future_review': p['verified_at'] = '2099-01-01'
    elif mutation == 'malformed': p = 'reviewed'
    elif mutation == 'public': p['verifier'] = 'public'
    elif mutation == 'empty_note': p['note'] = ''
    product['field_provenance']['required_documents'] = p
    assert rows(layer)[0]['confidence_level'] == 'Low'


def test_exact_owned_subject_and_full_list_review_remain_supported():
    layer = complete_case(); product = layer['merged_guidance']['visa_products'][0]
    p = proof(layer, product, 'required_documents')
    p.update(status='partial', verified_elements=deepcopy(product['required_documents']))
    product['field_provenance']['required_documents'] = p
    assert rows(layer)[0]['confidence_level'] == 'High'
    p['verified_elements'].pop()
    assert rows(layer)[0]['confidence_level'] == 'Low'


def test_unreviewed_filled_product_value_cannot_borrow_a_different_parent_value():
    layer = complete_case(); product = layer['merged_guidance']['visa_products'][0]
    product['required_documents'] = ['A new unreviewed product document']
    del product['field_provenance']['required_documents']
    assert rows(layer)[0]['confidence_level'] == 'Low'


def test_equal_same_permission_documents_can_inherit_individually_checked_parent():
    layer = complete_case(); product = layer['merged_guidance']['visa_products'][0]
    product['required_documents'] = deepcopy(layer['merged_guidance']['required_documents'])
    del product['field_provenance']['required_documents']
    assert rows(layer)[0]['confidence_level'] == 'High'
    layer['source_provenance']['field_provenance']['required_documents']['status'] = 'unknown'
    assert rows(layer)[0]['confidence_level'] == 'Low'


def test_unknown_own_empty_documents_cannot_inherit_parent_list():
    layer = complete_case(); product = layer['merged_guidance']['visa_products'][0]
    del product['required_documents']
    product['field_provenance']['required_documents']['status'] = 'unknown'
    assert rows(layer)[0]['confidence_level'] == 'Low'


def test_duplicate_names_do_not_borrow_first_product_field_evidence():
    layer = complete_case(); products = layer['merged_guidance']['visa_products']
    products[1]['type'] = products[0]['type']
    products[1]['required_documents'] = ['Unknown second product document']
    products[1]['field_provenance']['required_documents']['status'] = 'unknown'
    result = rows(layer)
    assert result[0]['confidence_level'] == 'High'
    assert result[1]['confidence_level'] == 'Low'


def test_disputes_remain_low_without_changing_values_or_publication_boundary():
    layer = complete_case(); base = rows(layer)
    result = t.records_for_route(layer['route'], layer['merged_guidance'], layer['source_provenance'],
                                  disputed_fields=['government_fee'])
    assert all(r['confidence_level'] == 'Low' and r['_evidence_low'] for r in result)
    assert [r['required_documents'] for r in base] == [r['required_documents'] for r in result]


@pytest.mark.parametrize('bounds', [
    {'effective_to': '2000-01-01'}, {'effective_from': '2099-01-01'},
    {'effective_to': 'unknown'}, {'effective_from': '2026-09-09', 'effective_to': '2026-09-08'}])
def test_own_field_review_outside_its_explicit_interval_cannot_certify(bounds):
    layer = complete_case(); product = layer['merged_guidance']['visa_products'][0]
    product['field_provenance']['required_documents'].update(bounds)
    assert rows(layer)[0]['confidence_level'] == 'Low'
    assert not rows(layer)[0]['_evidence_low']


def test_own_field_interval_uses_actual_selected_arrival_scope():
    layer = complete_case(); product = layer['merged_guidance']['visa_products'][0]
    product['field_provenance']['required_documents'].update(effective_from='2026-10-01', effective_to='2026-10-31')
    layer['route']['arrival_date'] = '2026-10-15'
    assert rows(layer)[0]['confidence_level'] == 'High'
    layer['route']['arrival_date'] = '2026-11-01'
    assert rows(layer)[0]['confidence_level'] == 'Low'


@pytest.mark.parametrize('reviewed', [['Correct supported document'], None, 'Correct supported document'])
def test_explicit_reviewed_value_binds_the_owned_field(reviewed):
    layer = complete_case(); product = layer['merged_guidance']['visa_products'][0]
    product['required_documents'] = ['Completely invented test-only document']
    product['field_provenance']['required_documents']['reviewed_value'] = reviewed
    assert rows(layer)[0]['confidence_level'] == 'Low'


def test_explicit_reviewed_value_accepts_exact_typed_value():
    layer = complete_case(); product = layer['merged_guidance']['visa_products'][0]
    product['field_provenance']['fee']['reviewed_value'] = deepcopy(product['fee'])
    assert rows(layer)[0]['confidence_level'] == 'High'
    product['fee']['amount'] = True
    product['field_provenance']['fee']['reviewed_value']['amount'] = 1
    assert rows(layer)[0]['confidence_level'] == 'Low'


@pytest.mark.parametrize('invented', [False, True])
def test_actual_legacy_stay_quote_does_not_certify_product_documents(invented):
    layer = complete_case(illustrative_field_quotes=False)
    if invented:
        layer['merged_guidance']['visa_products'][0]['required_documents'] = ['Pay a fictional agent 999 USD for an entry permit']
    assert [r['confidence_level'] for r in rows(layer)] == ['Low'] * 4
    assert all(not r['_evidence_low'] for r in rows(layer))


@pytest.mark.parametrize('quote', ['A passport is not required.', 'A passport is required only for minors.',
                                  'A passport is required if entering by land.'])
def test_contrary_or_conditional_document_evidence_does_not_certify_unconditional_value(quote):
    layer = complete_case(); product = layer['merged_guidance']['visa_products'][0]
    product['required_documents'] = ['Passport required']
    product['field_provenance']['required_documents'].update(quote=quote)
    assert rows(layer)[0]['confidence_level'] == 'Low'


def test_scope_annotation_is_not_quoted_document_evidence():
    layer = complete_case(); product = layer['merged_guidance']['visa_products'][0]
    product['required_documents'] = ['Passport required']
    product['field_provenance']['required_documents'].pop('quote')
    product['field_provenance']['required_documents']['note'] = 'Quote: Maximum stay is 180 days. Scope: Passport required.'
    assert rows(layer)[0]['confidence_level'] == 'Low'


@pytest.mark.parametrize('malformed', [7, 'text', {}, [None], [{'quote': 'Passport required'}]])
def test_malformed_quote_containers_lower_grade_without_crashing(malformed):
    layer = complete_case(); product = layer['merged_guidance']['visa_products'][0]
    product['field_provenance']['required_documents']['quotes'] = malformed
    assert rows(layer)[0]['confidence_level'] == 'Low'


def test_legacy_parent_checked_list_cannot_certify_new_unsupported_value():
    layer = complete_case(); product = layer['merged_guidance']['visa_products'][0]
    layer['merged_guidance']['required_documents'] = product['required_documents'] = ['An invented agent registration receipt']
    del product['field_provenance']['required_documents']
    del layer['source_provenance']['field_provenance']['required_documents']
    assert rows(layer)[0]['confidence_level'] == 'Low'


def test_conflicting_document_statements_cannot_select_only_positive_sentence():
    layer = complete_case(); product = layer['merged_guidance']['visa_products'][0]
    product['required_documents'] = ['Passport required']
    product['field_provenance']['required_documents']['quote'] = 'A passport is required. A passport is not required.'
    assert rows(layer)[0]['confidence_level'] == 'Low'


def test_named_human_legacy_parent_note_must_match_inherited_value():
    layer = complete_case(); product = layer['merged_guidance']['visa_products'][0]
    layer['merged_guidance']['required_documents'] = product['required_documents'] = ['Valid passport']
    del product['field_provenance']['required_documents']
    del layer['source_provenance']['field_provenance']['required_documents']
    layer['source_provenance'].update(verifier='human', note='Official page confirms a valid passport is required.')
    assert rows(layer)[0]['confidence_level'] == 'High'
    product['required_documents'] = layer['merged_guidance']['required_documents'] = ['A fictional agent receipt']
    assert rows(layer)[0]['confidence_level'] == 'Low'


@pytest.mark.parametrize(('field', 'quote'), [
    ('fee', 'Government fee is not USD 10.'),
    ('validity', 'Visa validity is not 30 days from first arrival.'),
    ('max_stay_days', 'The maximum stay is not 30 days.'),
    ('fee', 'Government fee USD 10 is not payable.'),
    ('max_stay_days', 'The maximum stay is 30 days. The maximum stay is not 30 days.')])
def test_direct_contrary_filled_product_value_cannot_grade_high(field, quote):
    layer = complete_case(); product = layer['merged_guidance']['visa_products'][0]
    product['field_provenance'][field]['quote'] = quote
    assert rows(layer)[0]['confidence_level'] == 'Low'


def test_no_refunds_does_not_negate_an_explicit_payable_fee():
    layer = complete_case(); product = layer['merged_guidance']['visa_products'][0]
    product['field_provenance']['fee']['quote'] = 'Government fee: USD 10. No refunds are provided.'
    assert rows(layer)[0]['confidence_level'] == 'High'


def test_not_charged_is_not_evidence_for_a_positive_fee():
    layer = complete_case(); product = layer['merged_guidance']['visa_products'][0]
    product['field_provenance']['fee']['quote'] = 'A visa fee is not charged.'
    assert rows(layer)[0]['confidence_level'] == 'Low'
