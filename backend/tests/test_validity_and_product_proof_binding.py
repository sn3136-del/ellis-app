from copy import deepcopy
import pytest
from app.visa_snapshot import tstation
from scripts.convert_reviewed_product_patch import field_provenance

ROUTE = {'passport_nationality':'IND','destination_country':'JPN',
         'travel_purpose':'tourism','travel_document_type':'ordinary_passport'}

@pytest.mark.parametrize('value', [
    '1/3/5 years at consular discretion', '1/3/5 years',
    '1 year, 3 years or 5 years', 'One (1) to three (3) months',
    '4 months to 1 year', '3–5 years', '3-5 years', '3 or 5 years',
    '5 years depending on eligibility', 'Usually 3 years',
    '3 years or until the passport expires', '3 years subject to examination',
    '90 days in any 180-day period', '6 months (180 days)',
    'Not valid for 3 months', 'At least 3 months', 'Six (7) months',
    '-5 days', '–5 days', '0 days', '3 working weeks',
    'Permitted stay is 90 days', '30 days stay', 'Processing takes 3 days', '−5 days',
])
@pytest.mark.parametrize('productless', [False, True])
def test_validity_options_remain_text_not_one_selected_duration(value, productless):
    g = {'disposition':'VISA_REQUIRED','requirement_detail':'paper_visa',
         'visa_category':'5-year multiple visa', 'permitted_stay':'90 days',
         'permitted_stay_days':90, 'validity':value}
    if not productless:
        g['visa_products']=[{'type':'5-year multiple visa','validity':value,
                             'max_stay_days':90,'requirement_detail':'paper_visa'}]
    before=deepcopy(g)
    row=tstation.records_for_route(ROUTE,g)[0]
    assert (row['validity_duration'],row['validity_unit']) == (None,None)
    assert row['validity_text']==value
    assert row['max_stay_duration']==90 and g==before

@pytest.mark.parametrize('value,expected', [
    ('Six (6) months',(6,'Month')), ('Three months from issue',(3,'Month')),
    ('not exceeding five years',(5,'Year')), ('Up to 5 years',(5,'Year')),
    ('2 calendar weeks',(14,'Day')), ('48 hours',(2,'Day')),
    ('Permanent',(0,'Long-term Valid')),
])
def test_one_explicit_validity_measure_survives(value,expected):
    g={'disposition':'VISA_REQUIRED','requirement_detail':'paper_visa',
       'validity':value,'permitted_stay':'30 days'}
    row=tstation.records_for_route(ROUTE,g)[0]
    assert (row['validity_duration'],row['validity_unit'])==expected
    assert row['max_stay_duration']==30


def product():
    p={'type':'Reviewed tourist visa','disposition':'VISA_REQUIRED',
       'requirement_detail':'paper_visa','max_stay_days':30}
    proof={'status':'reviewed','verified_at':'2026-09-09','verifier':'ai',
           'scope_note':'Exact ordinary Indian tourist visa program review.',
           'evidence':[{'source_id':'ind','source_url':'https://www.in.emb-japan.go.jp/itpr_en/Visa.html',
                        'quote':'Indian nationals can apply for the reviewed temporary visitor visa.'}]}
    p['field_provenance']={'disposition':field_provenance(proof,ROUTE,'disposition',p)}
    return p

@pytest.mark.parametrize('mutation', ['disposition','detail','both','missing_disposition_binding','missing_detail_binding'])
def test_stale_product_requirement_proof_never_inherits_parent_credit(mutation):
    p=product();proof=p['field_provenance']['disposition']
    assert proof['subject']['disposition']=='VISA_REQUIRED'
    assert proof['subject']['requirement_detail']=='paper_visa'
    assert tstation._explicit_product_verdict_provenance(p,ROUTE)[1]
    if mutation in ('disposition','both'):p['disposition']='VISA_EXEMPT'
    if mutation in ('detail','both'):p['requirement_detail']='unconditional_visa_free'
    if mutation=='missing_disposition_binding':proof['subject'].pop('disposition')
    if mutation=='missing_detail_binding':proof['subject'].pop('requirement_detail')
    assert tstation._explicit_product_verdict_provenance(p,ROUTE)==(True,None)
    parent=dict(proof,fields=['disposition','visa_products'])
    g={'disposition':'VISA_REQUIRED','requirement_detail':'paper_visa','visa_products':[p]}
    row=tstation.records_for_route(ROUTE,g,parent)[0]
    assert row['_product_source_verified'] is None
    assert row['confidence_level']=='Low'


def test_proof_of_unknown_detail_does_not_cover_a_later_known_detail():
    p=product();p['requirement_detail']=None
    p['field_provenance']['disposition']['subject']['requirement_detail']=None
    assert tstation._explicit_product_verdict_provenance(p,ROUTE)[1]
    p['requirement_detail']='evisa'
    assert tstation._explicit_product_verdict_provenance(p,ROUTE)==(True,None)


@pytest.mark.parametrize('key,value', [('disposition', []), ('requirement_detail', {}),
                                     ('requirement_detail', 'invented-permission')])
def test_malformed_subject_is_uncredited_without_crashing(key,value):
    p=product()
    p[key]=value
    p['field_provenance']['disposition']['subject'][key]=value
    assert tstation._explicit_product_verdict_provenance(p,ROUTE)==(True,None)
