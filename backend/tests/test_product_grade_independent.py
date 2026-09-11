from copy import deepcopy
import importlib.util
from pathlib import Path
import pytest

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('grade_author_tests',HERE/'test_product_grade_evidence.py')
author=importlib.util.module_from_spec(spec);spec.loader.exec_module(author)

def test_annual_stay_quote_does_not_review_owned_required_documents():
    layer=author.complete_case(illustrative_field_quotes=False)
    proof=layer['merged_guidance']['visa_products'][0]['field_provenance']['required_documents']
    assert '180 days' in proof['note']
    assert author.rows(layer)[0]['confidence_level']=='Medium'

def test_changed_unreviewed_document_cannot_keep_old_unrelated_quote_credit():
    layer=author.complete_case(illustrative_field_quotes=False)
    layer['merged_guidance']['visa_products'][0]['required_documents']=['Pay a fictional agent 999 USD for an entry permit']
    assert author.rows(layer)[0]['confidence_level']=='Medium'

def test_explicit_value_binding_is_respected():
    layer=author.complete_case();product=layer['merged_guidance']['visa_products'][0]
    product['required_documents']=['A different test-only requirement']
    product['field_provenance']['required_documents']['reviewed_value']=['The actual reviewed requirement']
    assert author.rows(layer)[0]['confidence_level']=='Medium'

@pytest.mark.parametrize('quotes',[7,True,{'bad':'quote'},'Wrong scalar', [None]])
def test_malformed_explicit_quote_list_fails_low_without_api_error(quotes):
    layer=author.complete_case();product=layer['merged_guidance']['visa_products'][0]
    product['field_provenance']['required_documents']['quotes']=quotes
    assert author.rows(layer)[0]['confidence_level']=='Medium'

def test_parent_field_list_without_document_evidence_cannot_certify_new_value():
    layer=author.complete_case();g=layer['merged_guidance'];product=g['visa_products'][0]
    g['required_documents']=['An invented agent registration receipt']
    product['required_documents']=deepcopy(g['required_documents'])
    product['field_provenance'].pop('required_documents')
    layer['source_provenance']['field_provenance'].pop('required_documents')
    assert author.rows(layer)[0]['confidence_level']=='Medium'

@pytest.mark.parametrize('quote',['A passport is not required.','A passport is required only if you apply for a residence permit.'])
def test_contrary_or_separate_conditional_document_quote_cannot_certify(quote):
    layer=author.complete_case();product=layer['merged_guidance']['visa_products'][0]
    product['required_documents']=['Passport required']
    product['field_provenance']['required_documents']['quote']=quote
    assert author.rows(layer)[0]['confidence_level']=='Medium'

def test_direct_document_contradiction_in_one_proof_is_low():
    layer=author.complete_case();product=layer['merged_guidance']['visa_products'][0]
    product['required_documents']=['Passport required']
    product['field_provenance']['required_documents']['quote']='A passport is required. A passport is not required.'
    assert author.rows(layer)[0]['confidence_level']=='Medium'

@pytest.mark.parametrize('field',['fee','validity','max_stay_days'])
def test_direct_negated_owned_fee_validity_or_stay_is_not_confirmation(field):
    layer=author.complete_case();product=layer['merged_guidance']['visa_products'][0];value=product[field]
    if field=='fee': quote=f"Government fee is not {value['currency']} {value['amount']}."
    elif field=='validity': quote=f'Visa validity is not {value}.'
    else:quote=f'The maximum stay is not {value} days.'
    product['field_provenance'][field]['quote']=quote
    assert author.rows(layer)[0]['confidence_level']=='Medium'
