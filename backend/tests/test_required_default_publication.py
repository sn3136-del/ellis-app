"""Required defaults retain application facts while distinct options wait."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from app.visa_snapshot import tstation
from app.visa_snapshot.records_guard import apply_records_hold
CASES=json.loads((Path(__file__).parent/'fixtures/required_default_publication.json').read_text())['routes']
EXEMPT=json.loads((Path(__file__).parent/'fixtures/optional_product_publication.json').read_text())
def envelope(c):
    assert not c['active_disputed_fields'] and not c['open_issues']
    return dict(deepcopy(c['reader_before_guard']),guidance=deepcopy(c['merged_guidance']),source_verified=deepcopy(c['source_provenance']),grounded_check=deepcopy(c['effective_grounded_check']),stale=False)
def india():return next(c for c in CASES if c['cache_key'].startswith('IND|'))
@pytest.mark.parametrize('case',CASES,ids=lambda c:c['cache_key'])
def test_live_required_default_and_qc_preservation(case,monkeypatch):
    monkeypatch.setenv('ELLIS_HOLD_UNCERTAIN','1');raw=envelope(case);before=deepcopy(raw);out=apply_records_hold(case['route'],raw)
    if case['cache_key'].startswith('FRA|'):
        assert out['held'] and out.get('publication_state')!='partial';return
    assert not out['held'] and out['publication_state']=='partial'
    g=out['guidance'];assert g['disposition']=='VISA_REQUIRED' and g['requirement_detail']=='evisa'
    assert g['government_fee']==raw['guidance']['government_fee'] is None
    for key in ['application_channel','official_portal_url']:
        assert g[key]==raw['guidance'][key]
    for key in ['required_documents', 'exceptions']:
        assert all(v in g[key] for v in raw['guidance'][key])
        assert all(v in g[key] for v in raw['guidance']['visa_products'][0].get(key, []))
    assert raw['guidance']['entry_requirements'] in g['entry_requirements']
    assert '86 to 4 days' in g['processing_time']
    assert g['application_channel']=='online_portal' and len(g['visa_products'])==1
    assert g['visa_products'][0]['source_url']==raw['guidance']['visa_products'][0]['source_url']
    assert g['visa_products'][0]['validity']==raw['guidance']['visa_products'][0]['validity']
    assert g['visa_products'][0]['validity'].startswith('120 days from the date of issue')
    assert len(out['product_publication'])==len(raw['guidance']['visa_products'])
    for p in raw['guidance']['visa_products'][1:]:assert p['type'] not in json.dumps(out)
    steps={p['step'] for p in out['workflow_plan']}
    assert steps == set()  # Independent visa fields do not establish a filing order.
    assert 'prepare_entry_documents' not in steps and raw==before
    rows=tstation.records_for_route(case['route'],raw['guidance'],raw['source_verified'])
    assert not rows[0]['_evidence_low'] and rows[0]['confidence_level']=='Low'
    assert all(r['_evidence_low'] for r in rows[1:])

def test_real_st_lucia_default_stay_is_not_passport_note():
    c=next(c for c in EXEMPT if c['cache_key'].startswith('HKG|HKG|LCA|'))
    raw=dict(deepcopy(c['reader_before_guard']),guidance=deepcopy(c['merged_guidance']),source_verified=deepcopy(c['source_provenance']),stale=False)
    assert raw['guidance']['visa_products'][0].get('max_stay_days') is None
    before=deepcopy(raw);out=apply_records_hold(c['route'],raw);p=out['guidance']['visa_products'][0]
    assert p['permitted_stay']==out['guidance']['permitted_stay']==raw['guidance']['permitted_stay']
    assert 'six months' in p['notes'] and 'six months' not in p['permitted_stay'] and raw==before

def test_separate_product_never_inherits_parent_stay():
    c=EXEMPT[0];raw=dict(deepcopy(c['reader_before_guard']),guidance=deepcopy(c['merged_guidance']),source_verified=deepcopy(c['source_provenance']),stale=False)
    own=deepcopy(raw['guidance']['visa_products'][1]);own.update(type='Published tourist eVisa',source_url=raw['source_verified']['source_url'],verified_at=raw['source_verified']['verified_at'],source_quote='Tourist eVisa available for eligible ordinary passport holders.',notes='Return ticket required.',max_stay_days=None,permitted_stay=None)
    raw['guidance']['visa_products'].append(own);out=apply_records_hold(c['route'],raw)
    assert out['publication_state']=='partial' and out['guidance']['visa_products'][0].get('permitted_stay')
    assert out['guidance']['visa_products'][1].get('permitted_stay') is None
    assert out['guidance']['visa_products'][1].get('max_stay_days') is None

def test_reviewed_positive_fee_and_reviewed_payment_steps_survive():
    c=india();raw=envelope(c);g=raw['guidance'];prov=raw['source_verified'];fee={'amount':42,'currency':'EUR'}
    g['government_fee']=deepcopy(fee);g['visa_products'][0]['fee']=deepcopy(fee)
    proof=dict(deepcopy(prov['field_provenance']['required_documents']),status='reviewed',note='Synthetic test tariff',quote='The e-visa fee is EUR 42.')
    prov['field_provenance']['government_fee']=proof;g['visa_products'][0]['field_provenance']['fee']=proof
    g['payment_process']=['Pay the e-visa fee online'];prov['fields'].append('payment_process');prov['field_provenance']['payment_process']=deepcopy(proof)
    out=apply_records_hold(c['route'],raw);assert out['publication_state']=='partial'
    assert out['guidance']['government_fee']==out['guidance']['visa_products'][0]['fee']==fee
    assert out['guidance']['payment_process']==g['payment_process']
    assert out['apply_steps']==[] and out['workflow_plan']==[]
    assert out['application_steps_status']=='unknown'  # Fee proof is not proof of global procedure order.

@pytest.mark.parametrize('reason',['unsupported_default','unknown_verdict','wrong_detail','own_unknown','missing_channel_proof','wrong_channel','missing_docs_proof','partial_docs','unreviewed_fee','unreviewed_product_fee','dispute','pending','stale','previous_hold','conflict','expired','future','malformed_bound','multiple_defaults'])
def test_other_holds_and_critical_facts_remain_blocking(reason,monkeypatch):
    monkeypatch.setenv('ELLIS_HOLD_UNCERTAIN','1');c=india();r=envelope(c);g=r['guidance'];p=r['source_verified']
    if reason=='unsupported_default':r['source_verified']=None
    elif reason=='unknown_verdict':p['field_provenance']['disposition']['status']='unknown'
    elif reason=='wrong_detail':g['requirement_detail']='paper_visa'
    elif reason=='own_unknown':g['visa_products'][0]['field_provenance']['disposition']['status']='unknown'
    elif reason=='missing_channel_proof':p['fields'].remove('application_channel')
    elif reason=='wrong_channel':g['application_channel']='not_required'
    elif reason=='missing_docs_proof':p['fields'].remove('required_documents')
    elif reason=='partial_docs':p['field_provenance']['required_documents'].update(status='partial',verified_elements=g['required_documents'][:1])
    elif reason=='unreviewed_fee':g['government_fee']={'amount':99,'currency':'USD'}
    elif reason=='unreviewed_product_fee':g['visa_products'][0]['fee']={'amount':99,'currency':'USD'}
    elif reason=='dispute':r['grounded_check']={'disputed_fields':['government_fee']}
    elif reason=='pending':r['detail_pending']=True
    elif reason=='stale':r['stale']=True
    elif reason=='previous_hold':r['held']=True
    elif reason=='conflict':r['contradictions']=['application channel conflicts with source']
    elif reason=='multiple_defaults':g['visa_products'].append(deepcopy(g['visa_products'][0]))
    else:
        q=p['field_provenance']['disposition']
        if reason=='expired':q['effective_to']='2000-01-01'
        elif reason=='future':q['effective_from']='2099-01-01'
        else:q['effective_to']='sometime next year'
    out=apply_records_hold(c['route'],r);assert out.get('publication_state')!='partial' and out['held']

@pytest.mark.parametrize('text',['You may apply for a consular visa for 90 USD.','The paper option costs 90 USD.','E-visa entry requires a return ticket, or you can obtain a regular tourist visa.'])
def test_withheld_offers_and_mixed_conditions_fail_closed(text):
    c=india();r=envelope(c);r['guidance']['visa_products'][0]['notes']=text
    assert apply_records_hold(c['route'],r).get('publication_state')!='partial'
    r=envelope(c);r['guidance']['exceptions'].append(text)
    assert apply_records_hold(c['route'],r).get('publication_state')!='partial'

def test_active_database_issue_still_blocks(monkeypatch):
    from app.visa_snapshot import freshness
    monkeypatch.setattr(freshness,'active_disputed_fields',lambda db,key:['required_documents'])
    c=india();out=apply_records_hold(c['route'],envelope(c),db=object())
    assert out['held'] and out.get('publication_state')!='partial'

def test_unreviewed_paper_workflow_and_future_containers_never_reappear():
    c=india();r=envelope(c);g=r['guidance'];needle='Book PAPER_VISA_COUNTER_987 at the consulate'
    for key in ['account_registration_steps','payment_process','submission_process']:g[key]=[needle]
    for key in ['advisories','workflow_plan','apply_steps','future_claims','reply']:r[key]=[needle]
    g['future_claims']=[needle];r['source_verified']['note']+=' '+needle
    out=apply_records_hold(c['route'],r);assert out['publication_state']=='partial' and needle not in json.dumps(out)
    assert out['guidance']['application_channel']=='online_portal'
    assert out['workflow_plan']==[] and out['application_steps_status']=='unknown'
