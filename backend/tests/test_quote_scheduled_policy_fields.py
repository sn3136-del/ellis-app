"""Existing schedule quotes keep their exact route, date, value and source owner."""
from copy import deepcopy
import json
import pytest
from app.visa_snapshot import record_evidence as ev, scheduled_policies as sp, tstation as ts

# Resolve from the app's installed seed location; no live reads or invented text.
POLICY=next(p for p in json.loads(sp.POLICIES.read_text()) if p['route']['nationality']=='CAN')
ROUTE={'passport_nationality':'CAN','destination_country':'THA','travel_purpose':'tourism',
       'travel_document_type':'ordinary_passport','lawful_country_of_residence':'CAN','arrival_date':'2026-09-15'}

def current(monkeypatch):
    policy=deepcopy(POLICY)
    parsed=sp._parse_rows([policy])
    monkeypatch.setattr(sp,'_load',lambda:parsed)
    g,p=sp.apply({'disposition':'VISA_EXEMPT','permitted_stay_days':60,
                  'required_documents':['A retained unrelated document'],
                  'exceptions':['Retained independent entry condition.']},None,ROUTE)
    return g,p,policy

def drawer(g,p,route=ROUTE):
    row=ts.records_for_route(route,g,p)[0]
    return row,ev.for_record(row,route,g,p,{})

def test_current_official_schedule_excerpts_map_only_to_proven_current_fields(monkeypatch):
    g,p,policy=current(monkeypatch);before=deepcopy((g,p))
    row,quotes=drawer(g,p)
    proven={'visa_requirement','visa_requirement_detail','visa_type_name',
            'max_stay_duration','max_stay_unit'}
    assert {k for k,v in quotes.items() if v}==proven
    for field in proven:
        assert {q['quote'] for q in quotes[field]}==set(policy['evidence']['quotes'].values())
        assert all(q['source_url']==policy['evidence']['source_url'] for q in quotes[field])
    assert quotes['visa_fee_amount']==quotes['processing_min_days']==quotes['special_conditions']==[]
    assert row['max_stay_duration']==30 and (g,p)==before

@pytest.mark.parametrize('change',[
    'nationality','destination','purpose','document','previous_day','metadata_id',
    'metadata_date','metadata_provenance','proof_url','proof_evidence_url','proof_quotes',
    'different_verdict','different_detail','different_products','proof_date','proof_effective','proof_status','proof_subject','proof_bound_value','proof_partial','value','future_review','missing_registry',
])
def test_changed_scope_value_or_proof_cannot_borrow_schedule_quotes(monkeypatch,change):
    g,p,policy=current(monkeypatch);route=deepcopy(ROUTE)
    for field in p['field_provenance']:
        proof=p['field_provenance'][field]
        if change=='proof_url':proof['source_url']='https://www.mfa.go.th/other'
        if change=='proof_evidence_url':proof['evidence_url']='https://image.mfa.go.th/other.png'
        if change=='proof_quotes':proof['quotes']['stay']='90 days'
        if change=='proof_date':proof['verified_at']='2026-09-01'
        if change=='proof_effective':proof['effective_from']='2026-09-01'
        if change=='proof_status':proof['status']='unknown'
        if change=='proof_subject':proof['subject']={'lawful_country_of_residence':'USA'}
        if change=='proof_bound_value':proof['reviewed_value']='different previous value'
        if change=='proof_partial':proof['retained_unverified_elements']=['Unverified component']
    if change=='nationality':route['passport_nationality']='USA'
    if change=='destination':route['destination_country']='VNM'
    if change=='purpose':route['travel_purpose']='work'
    if change=='document':route['travel_document_type']='diplomatic_passport'
    if change=='previous_day':route['arrival_date']='2026-09-14'
    if change=='metadata_id':g['scheduled_policy']['id']='other'
    if change=='metadata_date':g['scheduled_policy']['date_used']='2026-09-16'
    if change=='metadata_provenance':p.pop('scheduled_policy')
    if change=='value':g['permitted_stay_days']=90;g['permitted_stay']='90 days'
    if change=='different_verdict':g['disposition']='VISA_REQUIRED'
    if change=='different_detail':g['requirement_detail']='conditional_visa_free'
    if change=='different_products':g['visa_products']=[{'type':'Separate visa','disposition':'VISA_REQUIRED','max_stay_days':30}]
    if change=='future_review':
        policy['verified_at']='2099-01-01'
        for proof in p['field_provenance'].values():proof['verified_at']='2099-01-01'
        monkeypatch.setattr(sp,'_load',lambda:sp._parse_rows([policy]))
    if change=='missing_registry':monkeypatch.setattr(sp,'_load',lambda:[])
    _,quotes=drawer(g,p,route)
    assert quotes['max_stay_duration']==quotes['max_stay_unit']==[]


def test_expired_policy_and_invalid_store_do_not_supply_quotes(monkeypatch):
    g,p,policy=current(monkeypatch)
    policy['effective_to']='2026-09-15'
    policy['evidence']['quotes']['effective_to']='Effective until 15 September 2026'
    policy['evidence']['text']+='\nEffective until 15 September 2026'
    monkeypatch.setattr(sp,'_load',lambda:sp._parse_rows([policy]))
    expired=dict(ROUTE,arrival_date='2026-09-16')
    assert not ev._scheduled_policy_quotes(g,p,expired)
    def broken():raise sp.PolicyStoreUnavailable('invalid saved store')
    monkeypatch.setattr(sp,'_load',broken)
    assert not ev._scheduled_policy_quotes(g,p,ROUTE)
