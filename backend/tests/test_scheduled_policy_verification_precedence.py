"""Refreshing today's rule cannot silently cancel an announced future rule."""
from datetime import date
import pytest
from app.visa_snapshot import scheduled_policies as sp, kimi_primary as kp

BASE = {'disposition':'VISA_EXEMPT','requirement_detail':'unconditional_visa_free',
        'permitted_stay_days':60,'permitted_stay':'60 days',
        'government_fee':{'amount':0,'currency':None},'visa_products':[],
        'exceptions':[],'required_documents':['Valid passport']}

def route(nationality='FRA',arrival='2026-09-15'):
    return {'passport_nationality':nationality,'destination_country':'THA',
            'travel_purpose':'tourism','travel_document_type':'ordinary_passport',
            'arrival_date':arrival}

def proof(**changes):
    return dict({'fields':['permitted_stay_days'],'verifier':'ai',
                 'verified_at':'2026-09-13','source_url':'https://consular.mfa.go.th/'},**changes)

@pytest.fixture(autouse=True)
def check_clock(monkeypatch):
    monkeypatch.setattr(sp,'_today',lambda:date(2026,9,13))

@pytest.mark.parametrize('nationality',['FRA','ESP','IND'])
def test_sep13_current_rule_recheck_does_not_cancel_sep15_policy(nationality):
    # The next day's refresh of the CURRENT60-day rule must not cause the
    # announced Sep15 30-day rule, independently rechecked Sep12, to be held.
    source=proof();source['field_provenance']={k:proof() for k in ('permitted_stay_days','permitted_stay')}
    source['fields']=['permitted_stay_days','permitted_stay']
    g,p=sp.apply(BASE,source,route(nationality))
    assert g['permitted_stay_days']==30 and g['scheduled_policy']['effective_from']=='2026-09-15'
    assert 'scheduled_policy_conflict' not in g and not kp.serve_time_invariants(g)
    assert p['field_provenance']['permitted_stay_days']['effective_from']=='2026-09-15'

@pytest.mark.parametrize('arrival,stay',[('2026-09-14',60),('2026-09-15',30),('2026-09-16',30)])
def test_current_and_future_date_boundary_remains_deterministic(arrival,stay):
    g,_=sp.apply(BASE,proof(),route(arrival=arrival))
    assert g['permitted_stay_days']==stay and 'scheduled_policy_conflict' not in g
    assert kp.cache_key(route(arrival=arrival))==kp.cache_key(route(arrival=''))

@pytest.mark.parametrize('days',[45,60])
def test_new_explicit_future_replacement_or_cancellation_is_preserved_for_review(days):
    source=proof(effective_from='2026-09-15');baseline=dict(BASE,permitted_stay_days=days)
    g,p=sp.apply(baseline,source,route())
    assert g['permitted_stay_days']==days and p==source
    assert g['scheduled_policy_conflict']['fields']==['permitted_stay_days'] and 'scheduled_policy' not in g

def test_new_explicit_extension_of_baseline_through_future_date_is_preserved():
    g,_=sp.apply(BASE,proof(effective_from='2024-07-15',effective_to='2026-10-01'),route())
    assert g['permitted_stay_days']==60 and g.get('scheduled_policy_conflict')

@pytest.mark.parametrize('bounds',[{'effective_to':'2026-09-14'},{'effective_from':'2026-09-16'}])
def test_competing_interval_must_apply_to_the_requested_date(bounds):
    g,_=sp.apply(BASE,proof(**bounds),route())
    assert g['permitted_stay_days']==30 and 'scheduled_policy_conflict' not in g

def test_old_open_ended_start_is_not_new_evidence_about_the_future():
    g,_=sp.apply(BASE,proof(effective_from='2024-07-15'),route())
    assert g['permitted_stay_days']==30 and 'scheduled_policy_conflict' not in g

def test_inherited_old_end_date_does_not_borrow_current_recheck_timestamp():
    source=proof(effective_to='2026-12-31',policy_interval_evidence={
        'effective_to':{'verified_at':'2026-09-09','source_url':'https://consular.mfa.go.th/old-notice'}})
    g,_=sp.apply(BASE,source,route())
    assert g['permitted_stay_days']==30 and 'scheduled_policy_conflict' not in g

def test_new_rechecked_interval_evidence_can_compete_with_schedule():
    source=proof(effective_to='2026-12-31',policy_interval_evidence={
        'effective_to':{'verified_at':'2026-09-13','source_url':'https://consular.mfa.go.th/replacement-notice'}})
    g,_=sp.apply(BASE,source,route())
    assert g['permitted_stay_days']==60 and g.get('scheduled_policy_conflict')

def test_fresh_contradiction_after_effective_day_still_requires_review(monkeypatch):
    monkeypatch.setattr(sp,'_today',lambda:date(2026,9,16))
    g,_=sp.apply(BASE,proof(verified_at='2026-09-16'),route(arrival='2026-09-17'))
    assert g['permitted_stay_days']==60 and g['scheduled_policy_conflict']['fields']==['permitted_stay_days']

def test_later_recheck_of_explicitly_expired_baseline_does_not_extend_its_scope():
    g,_=sp.apply(BASE,proof(verified_at='2026-09-16',effective_to='2026-09-14'),route(arrival='2026-09-17'))
    assert g['permitted_stay_days']==30 and 'scheduled_policy_conflict' not in g

def test_invalid_explicit_interval_remains_conflict_instead_of_disappearing():
    g,_=sp.apply(BASE,proof(effective_from='invalid'),route())
    assert g['permitted_stay_days']==60 and g.get('scheduled_policy_conflict')

@pytest.mark.parametrize('bounds',[
    {'effective_from':''}, {'effective_to':''},
    {'effective_from':'   ', 'effective_to':'\t\n'},
    {'effective_from':'', 'effective_to':None},
])
def test_blank_optional_bounds_do_not_claim_future_applicability(bounds):
    source=proof(**bounds)
    g,_=sp.apply(BASE,source,route())
    assert g['permitted_stay_days']==30 and 'scheduled_policy_conflict' not in g
    assert all(source[key]==value for key,value in bounds.items())

@pytest.mark.parametrize('bounds',[
    {'effective_from':'', 'effective_to':'invalid'},
    {'effective_from':'\t', 'effective_to':'2026-09-31'},
    {'effective_from':'2026-09-15 ', 'effective_to':''},
])
def test_blank_bound_does_not_hide_other_malformed_nonempty_bound(bounds):
    g,_=sp.apply(BASE,proof(**bounds),route())
    assert g['permitted_stay_days']==60 and g.get('scheduled_policy_conflict')
