"""The aggregated conditions tile keeps separately owned component quotes."""
from copy import deepcopy
from app.visa_snapshot import record_evidence as ev,tstation

ROUTE={'passport_nationality':'MYS','destination_country':'RUS','travel_purpose':'tourism','travel_document_type':'ordinary_passport'}
URL='https://evisa.kdmid.ru/'

def fixture():
    p={'type':'Unified electronic visa (tourism)','disposition':'VISA_REQUIRED','requirement_detail':'evisa','entry':'single',
       'application_channel':'online_portal','notes':'Payment is non-refundable.',
       'exceptions':['Every child needs a separate e-visa.'],
       'processing_time':'No more than 4 calendar days, excluding the day of application.'}
    quotes={'notes':'The payment is non-refundable regardless of the result of the application processing.',
            'exceptions':'All minor children travelling with their parents must have a separate e-visa.',
            'processing_time':'the time period for processing your application (4 calendar days) does not include the day of application submission.'}
    p['field_provenance']={field:{'source_url':URL,'quote':quote,'verified_at':'2026-09-13','verifier':'ai','status':'reviewed',
        'reviewed_value':deepcopy(p[field]),'subject':dict(ROUTE,product_type=p['type'],entry='single')} for field,quote in quotes.items()}
    g={'disposition':'VISA_REQUIRED','requirement_detail':'evisa','visa_products':[p]}
    return p,g,quotes

def test_special_conditions_show_all_current_product_components_without_changing_values():
    p,g,quotes=fixture();before=deepcopy(g)
    row=tstation.records_for_route(ROUTE,g)[0]
    assert all(text in row['special_conditions'] for text in (p['notes'],p['exceptions'][0],p['processing_time']))
    found=ev.for_record(row,ROUTE,g,{}, {})['special_conditions']
    assert {q['quote'] for q in found}==set(quotes.values())
    assert g==before

def test_stale_or_different_sibling_component_proof_cannot_join_other_valid_quotes():
    p,g,quotes=fixture()
    p['field_provenance']['exceptions']['subject']['entry']='multiple'
    p['field_provenance']['processing_time']['reviewed_value']='Old processing time'
    row=tstation.records_for_route(ROUTE,g)[0]
    found=ev.for_record(row,ROUTE,g,{}, {})['special_conditions']
    assert [q['quote'] for q in found]==[quotes['notes']]

def test_processing_evidence_is_not_attached_when_timing_is_not_in_conditions():
    p,g,quotes=fixture();row=tstation.records_for_route(ROUTE,g)[0]
    row['special_conditions']=p['notes']+'. '+p['exceptions'][0]
    found=ev.for_record(row,ROUTE,g,{}, {})['special_conditions']
    assert {q['quote'] for q in found}=={quotes['notes'],quotes['exceptions']}
