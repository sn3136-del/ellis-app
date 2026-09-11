"""No fake visa product, passport requirement or no-authorisation promise."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from app.visa_snapshot import tstation as ts

@pytest.fixture
def source():
    return json.loads((Path(__file__).parent/'fixtures/idn_vnm_pure_exemption_projection.json').read_text())

def row(source, disputed=()):
    return ts.records_for_route(source['route'],source['merged_guidance'],source['source_verified'],disputed_fields=list(disputed))[0]

def test_exact_social_exemption_stay_retained_and_no_unreviewed_product_created(source):
    old=deepcopy(source); r=row(source)
    assert r['max_stay_duration']==14 and r['max_stay_unit']=='Day'
    assert all(r[f] is None for f in ['validity_duration','validity_unit','entries','required_documents','entry_requirements','visa_fee_currency'])
    assert r['visa_fee_amount']==0 and r['confidence_level']=='Medium'
    assert source==old

@pytest.mark.parametrize('disputed',[['source_audit'],['permitted_stay_days'],['disposition']])
def test_disputed_qc_does_not_restore_generated_product_facts(source,disputed):
    r=row(source,disputed)
    assert r['max_stay_duration']==14
    assert all(r[f] is None for f in ['validity_duration','validity_unit','entries','required_documents','entry_requirements','visa_fee_currency'])
    r.update(held=True,route_held=True,source_check='ai-quote')
    statuses=ts.field_status(r)
    assert all(statuses[f]=='missing' for f in ['validity_duration','validity_unit','entries','required_documents','visa_fee_currency'])
    assert r['confidence_level']=='Low'

@pytest.mark.parametrize('metadata',[{}, {'held':True,'source_check':'ai-quote'},
    {'held':False,'route_held':True,'source_check':'ai-quote'},
    {'held':False,'source_check':'unverified'}, {'held':False,'source_check':'operator-release'},
    {'held':False,'source_check':'ai-quote','_disputed':['passport_validity']},
    {'held':False,'source_check':'ai-quote','_contradictions':['wrong fee']},
    {'held':False,'source_check':'ai-quote','field_status':{'info_validity':'pending-review'}}])
def test_only_published_reviewed_exemption_receives_new_disposition_credit(source,metadata):
    r=row(source);r.pop('_reviewed_pure_exemption');r.update(metadata)
    statuses=ts.field_status(r)
    assert all(statuses[f]=='missing' for f in ['validity_duration','validity_unit','entries','visa_fee_currency'])

@pytest.mark.parametrize('check',['ai-quote','human-quote','grounded-consistent'])
def test_supported_published_plain_exemption_has_no_visa_currency_or_entry_count(source,check):
    r=row(source);r.update(held=False,route_held=False,source_check=check)
    statuses=ts.field_status(r)
    assert all(statuses[f]=='not-applicable' for f in ['validity_duration','validity_unit','entries','visa_fee_currency'])
    assert statuses['required_documents']=='missing'
    assert statuses['entry_requirements']=='optional-empty'
    # A checked answer whose page states no policy end date documents the
    # absence (field 24 is "Not publicly available", never a gap).
    assert statuses['info_validity']=='not-published'


def test_explicit_passport_and_other_conditions_remain_exact():
    g={'disposition':'VISA_EXEMPT','passport_validity':'Valid for the whole intended stay',
       'required_documents':['Ordinary passport valid for the whole stay'],
       'onward_travel_evidence':'A confirmed return ticket is required',
       'insurance_required':True}
    r=ts.records_for_route({'passport_nationality':'SGP','destination_country':'CHN','travel_purpose':'tourism'},g)[0]
    assert r['required_documents']==g['required_documents'][0]
    assert 'whole intended stay' in r['entry_requirements']
    assert 'confirmed return ticket' in r['entry_requirements']
    assert 'Travel insurance required' in r['entry_requirements']
    assert 'no travel authorisation' not in r['entry_requirements'].lower()


def test_arrival_card_requirement_and_submission_window_survive():
    g={'disposition':'VISA_EXEMPT','arrival_card':{'required':True,'name':'Arrival card','submission_window':'within 3 days before arrival'}}
    text=ts._entry_requirements(g)
    assert 'must still file the Arrival card' in text and 'within 3 days before arrival' in text


def test_verdict_does_not_verify_independently_present_passport_requirement():
    r={'visa_requirement':'Visa-free','required_documents':'Valid passport'}
    assert not ts._required_values_supported(r,{}, {'disposition'})
    assert ts._required_values_supported(r,{}, {'disposition','required_documents'})

@pytest.mark.parametrize('disposition,detail',[('CONDITIONAL','conditional_visa_free'),
  ('ELECTRONIC_AUTHORIZATION_REQUIRED','eta_electronic_authorization'),('VISA_REQUIRED','evisa')])
def test_other_permission_families_do_not_gain_pure_exemption_credit(disposition,detail):
    g={'disposition':disposition,'requirement_detail':detail,'application_channel':'online_portal',
       'government_fee':{'amount':20,'currency':'AUD'},'permitted_stay':'90 days'}
    r=ts.records_for_route({'passport_nationality':'USA','destination_country':'AUS','travel_purpose':'tourism'},g)[0]
    r.update(held=False,route_held=False,source_check='ai-quote')
    assert r['visa_fee_amount']==20 and r['visa_fee_currency']=='AUD'
    assert not ts._reviewed_no_visa_product(r)


def test_unknown_no_authorisation_stays_unknown_in_entry_prose():
    assert ts._entry_requirements({'disposition':'VISA_EXEMPT'}) is None
    assert ts._entry_requirements({'disposition':'VISA_EXEMPT','arrival_card':{'required':None}}) is None


@pytest.mark.parametrize('disputed',[[],['passport_validity']])
def test_mixed_exemption_product_never_borrows_usd_and_preserves_paid_sibling(disputed):
    g={'disposition':'VISA_EXEMPT','requirement_detail':'visa_free',
       'visa_products':[
           {'type':'Visa exemption','requirement_detail':'visa_free','disposition':'VISA_EXEMPT',
            'fee':{'amount':0,'currency':None},'max_stay_days':45},
           {'type':'Optional eVisa','requirement_detail':'evisa','disposition':'VISA_REQUIRED',
            'fee':{'amount':25,'currency':'USD'}}]}
    before=deepcopy(g)
    rs=ts.records_for_route({'passport_nationality':'HKG','destination_country':'VNM','travel_purpose':'tourism'},g,disputed_fields=disputed)
    assert len(rs)==2
    assert rs[0]['visa_fee_amount']==0 and rs[0]['visa_fee_currency'] is None
    assert rs[1]['visa_fee_amount']==25 and rs[1]['visa_fee_currency']=='USD'
    assert rs[0]['confidence_level']=='Low'
    assert g==before

@pytest.mark.parametrize('amount',[0,25])
def test_explicit_exemption_currency_and_nonzero_conflict_remain_visible(amount):
    g={'disposition':'VISA_EXEMPT','requirement_detail':'visa_free',
       'government_fee':{'amount':amount,'currency':'EUR'}}
    r=ts.records_for_route({'passport_nationality':'HKG','destination_country':'VNM','travel_purpose':'tourism'},g)[0]
    assert r['visa_fee_amount']==amount and r['visa_fee_currency']=='EUR'
    if amount:assert 'disposition' in r['_disputed_fields']
