"""Empty historical provenance cannot hide a newer, current checked quote."""
from copy import deepcopy
import pytest
from app.visa_snapshot import record_evidence as ev,tstation

ROUTE={'passport_nationality':'CAN','destination_country':'TWN','travel_purpose':'tourism','travel_document_type':'ordinary_passport'}
URL='https://www.boca.gov.tw/cp-149-4486-7785a-2.html'
STAMP='2026-09-13T19:22:11.738900+00:00'
QUOTE='with a duration of stay of up to 90 days: Albania, Andorra, Australia, Austria, Belgium, Bulgaria, Canada, Chile'


def case():
    g={'disposition':'VISA_EXEMPT','requirement_detail':'not_required','permitted_stay_days':90,'permitted_stay':'Up to 90 days'}
    old={'source_url':URL,'verified_at':'2026-08-29','verifier':'ai','note':'Historical reviewer summary without an exact quote slot.'}
    p={'field_provenance':{'permitted_stay_days':old}}
    check={'at':STAMP,'outcome':'checked','verified_fields':['permitted_stay_days'],'disputed_fields':[],
           'field_sources':{'permitted_stay_days':{'source_url':URL,'checked_at':STAMP,'quote':QUOTE}}}
    row=tstation.records_for_route(ROUTE,g,p)[0]
    return row,g,p,check


def test_actual_canadian_taiwan_checked_stay_quote_survives_empty_old_parent():
    row,g,p,c=case();before=deepcopy((row,g,p,c))
    out=ev.for_record(row,ROUTE,g,p,c)
    assert out['max_stay_duration'][0]=={'quote':QUOTE,'source_url':URL,'verified_at':STAMP,'kind':'official_page_check'}
    assert out['max_stay_unit']==out['max_stay_duration']
    assert (row,g,p,c)==before

@pytest.mark.parametrize('change',['disputed','unverified','failed','source_stamp_old','source_stamp_future',
                                 'newer_parent','different_value','wrong_source','wrong_subject',
                                 'old_asserted_quote','old_reviewed_value','old_partial','old_product_scope',
                                 'parent_policy_interval','live_dispute','live_contradiction'])
def test_fallback_preserves_current_value_scope_and_review_boundaries(change):
    row,g,p,c=case();old=p['field_provenance']['permitted_stay_days'];source=c['field_sources']['permitted_stay_days']
    if change=='disputed':c['disputed_fields']=['permitted_stay_days']
    elif change=='unverified':c['verified_fields']=[]
    elif change=='failed':c['outcome']='provider_error'
    elif change=='source_stamp_old':source['checked_at']='2026-08-01'
    elif change=='source_stamp_future':source['checked_at']=c['at']='2099-01-01'
    elif change=='newer_parent':old['verified_at']='2026-09-13T20:00:00+00:00'
    elif change=='different_value':g['permitted_stay_days']=30
    elif change=='wrong_source':source['source_url']='https://example.com'
    elif change=='wrong_subject':source['subject']={'passport_nationality':'USA'}
    elif change=='old_asserted_quote':old['quote']='Maximum stay is 30 days.'
    elif change=='old_reviewed_value':old['reviewed_value']=30
    elif change=='old_partial':old['status']='partial'
    elif change=='old_product_scope':old['subject']={'product_type':'Another product'}
    elif change=='parent_policy_interval':old['effective_from']='2026-09-15'
    elif change=='live_dispute':row['_disputed']=['permitted_stay_days']
    elif change=='live_contradiction':row['_contradictions']=['Actual route conflict']
    assert not ev.for_record(row,ROUTE,g,p,c)['max_stay_duration']


def test_explicit_product_proof_still_blocks_parent_and_checked_borrowing():
    row,g,p,c=case()
    product={'type':'Conditional exemption','disposition':'CONDITIONAL','requirement_detail':'conditional_visa_free',
             'max_stay_days':90,'field_provenance':{'max_stay_days':{'status':'unknown'}}}
    g['visa_products']=[product];row=tstation.records_for_route(ROUTE,g,p)[0]
    assert not ev.for_record(row,ROUTE,g,p,c)['max_stay_duration']
