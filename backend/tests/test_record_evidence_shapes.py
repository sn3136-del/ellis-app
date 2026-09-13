"""Stored evidence shapes and strict current-product ownership; no network."""
from copy import deepcopy

from app.visa_snapshot import record_evidence as evidence, tstation
from tests.test_record_field_evidence import case, proof, ROUTE, URL


def test_bound_review_keeps_verbatim_us_dollar_symbol_without_regrading():
    row, g, p = case()
    p['field_provenance']['government_fee'] = proof('US$ 25/single-entry electronic visa',
        reviewed_value={'amount':25,'currency':'USD'},subject=ROUTE)
    assert evidence.for_record(row, ROUTE, g, p, {})['visa_fee_amount'][0]['quote'] == 'US$ 25/single-entry electronic visa'
    g['government_fee']['amount'] = 50
    assert not evidence.for_record(row, ROUTE, g, p, {})['visa_fee_amount']


def test_bound_processing_paraphrase_retains_actual_quote_not_display_wording():
    row, g, p = case()
    p['field_provenance']['processing_time'] = proof(
        'Within 03 working days from the date of receiving application for electronic visa and visa fee',
        reviewed_value=g['processing_time'])
    result = evidence.for_record(row, ROUTE, g, p, {})['processing_min_days']
    assert result and result[0]['quote'].startswith('Within 03 working days')
    assert result[0]['quote'] != g['processing_time']


def test_explicit_additional_quotes_preserve_same_page_and_other_page_identity():
    q = proof('Primary excerpt.',additional_quotes=['Second excerpt.',
        {'quote':'Other page excerpt.','source_url':URL+'/other'}])
    result=evidence._quotes(q,'source_review')
    assert [(x['quote'],x['source_url']) for x in result] == [
        ('Primary excerpt.',URL),('Second excerpt.',URL),('Other page excerpt.',URL+'/other')]


def test_only_closed_historical_quote_marker_is_used():
    q=proof(None,note='Quote: The fee is USD 25. Scope: Canadian tourist e-visa.')
    assert evidence._quotes(q,'source_review')[0]['quote']=='The fee is USD 25.'
    for note in ['Reviewer thinks the fee is25.', 'The note says Quote: something Scope: anything',
                 'Quote: USD25, and now some reviewer commentary without a closing marker']:
        assert evidence._quotes(dict(q,note=note),'source_review')==[]


def test_entry_conditions_use_actual_saved_field_names():
    row,g,p=case()
    fields={'onward_travel_evidence':'A return ticket may be requested.',
            'accommodation_evidence':'An accommodation booking may be requested.',
            'financial_evidence':'Evidence of funds may be requested.'}
    g.update(fields)
    row['entry_requirements']=' '.join(fields.values())
    for key,value in fields.items():p['field_provenance'][key]=proof(value,reviewed_value=value)
    result=evidence.for_record(row,ROUTE,g,p,{})['entry_requirements']
    assert {x['quote'] for x in result}==set(fields.values())


def test_whole_table_bound_review_is_available_without_sibling_borrowing():
    _,g,p=case()
    g['visa_products']=[{'type':'Single entry','fee':{'amount':25,'currency':'USD'}},
                        {'type':'Multiple entry','fee':{'amount':50,'currency':'USD'}}]
    p={'field_provenance':{'visa_products':proof('Single entry US$25; multiple entry US$50.',
        reviewed_value=deepcopy(g['visa_products']))}}
    rows=tstation.records_for_route(ROUTE,g)
    assert all(evidence.for_record(r,ROUTE,g,p,{})['visa_fee_amount'] for r in rows)
    g['visa_products'][1]['fee']['amount']=100
    assert not evidence.for_record(rows[1],ROUTE,g,p,{})['visa_fee_amount']
    assert not evidence.for_record(dict(rows[0],_product_index=1),ROUTE,g,p,{})['visa_fee_amount']


def test_explicit_unknown_product_proof_cannot_fall_back_to_table_quote():
    _,g,p=case()
    product={'type':'Single entry','fee':{'amount':25,'currency':'USD'},
             'field_provenance':{'fee':{'status':'unknown'}}}
    g['visa_products']=[product]
    p={'field_provenance':{'visa_products':proof('The fee is USD25.',reviewed_value=deepcopy(g['visa_products']))}}
    row=tstation.records_for_route(ROUTE,g)[0]
    assert not evidence.for_record(row,ROUTE,g,p,{})['visa_fee_amount']


def test_inapplicable_future_review_is_not_presented_as_current_value():
    row,g,p=case()
    p['field_provenance']['government_fee']=proof('The fee is USD25.',
        reviewed_value=g['government_fee'],effective_from='2090-01-01')
    assert not evidence.for_record(row,ROUTE,g,p,{})['visa_fee_amount']


def test_exemption_stay_only_maps_to_validity_when_the_display_has_a_value():
    row,g,p=case()
    g['disposition']='VISA_EXEMPT';g['permitted_stay_days']=30
    p['field_provenance']['permitted_stay_days']=proof('Permitted stay30days.',reviewed_value=30)
    row.update(visa_requirement='Visa-free',validity_duration=30,validity_unit='Day')
    assert evidence.for_record(row,ROUTE,g,p,{})['validity_duration']
    row.update(validity_duration=None,validity_unit=None)
    assert not evidence.for_record(row,ROUTE,g,p,{})['validity_duration']


def test_malformed_absence_container_does_not_crash_evidence_view():
    row,g,p=case();g['unpublished_evidence']=['broken']
    assert evidence.for_record(row,ROUTE,g,p,{})['visa_fee_amount']


def test_field_keyed_quote_dictionary_cannot_leak_other_field_quotes():
    q=proof(None,quotes={'government_fee':'The fee is USD25.','processing_time':'Three working days.'})
    assert evidence._quotes(q,'source_review','government_fee')[0]['quote']=='The fee is USD25.'
    assert evidence._quotes(q,'source_review','required_documents')==[]


def test_legacy_product_source_quote_requires_current_value_match():
    _,g,p=case()
    g['visa_products']=[{'type':'Single entry','fee':{'amount':25,'currency':'USD'},
                        'source_url':URL,'source_quote':'Government fee: USD25.',
                        'verified_at':'2026-08-01','verifier':'ai'}]
    row=tstation.records_for_route(ROUTE,g)[0]
    assert evidence.for_record(row,ROUTE,g,{}, {})['visa_fee_amount']
    g['visa_products'][0]['fee']['amount']=50
    assert not evidence.for_record(row,ROUTE,g,{}, {})['visa_fee_amount']


def test_revision_can_bind_source_metadata_without_changing_display_values():
    row,_,_=case()
    assert evidence.revision(dict(row,_evidence_version='old-source')) != evidence.revision(dict(row,_evidence_version='new-source'))
