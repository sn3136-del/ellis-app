"""A verified default survives unsupported alternatives without claim leakage."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path

import pytest
from app.visa_snapshot import kimi_primary as kp,tstation
from app.visa_snapshot.records_guard import apply_records_hold

CASES=json.loads((Path(__file__).parent/'fixtures/optional_product_publication.json').read_text())


def envelope(case):
    return dict(deepcopy(case['reader_before_guard']), guidance=deepcopy(case['merged_guidance']),
                source_verified=deepcopy(case['source_provenance']), grounded_check={}, stale=False)


@pytest.mark.parametrize('case',CASES,ids=lambda c:c['cache_key'])
def test_seven_live_shapes_preserve_qc_and_publish_only_separable_defaults(case,monkeypatch):
    monkeypatch.setenv('ELLIS_HOLD_UNCERTAIN','1')
    raw=envelope(case);before=deepcopy(raw)
    records=tstation.records_for_route(case['route'],raw['guidance'],raw['source_verified'])
    assert records[0]['confidence_level']=='Medium'  # a checked requirement with gaps is Medium, never High
    assert not records[0]['_evidence_low']
    quarantined=[r['visa_type_name'] for r in records if r['_evidence_low']]
    assert quarantined
    result=apply_records_hold(case['route'],raw)
    if case['cache_key'].startswith('CHN|CHN|THA|business'):
        # Mandatory employment restrictions share a sentence with the
        # optional Non-Immigrant product. Do not silently remove that sentence.
        assert result['held'] is True
        assert result.get('publication_state')!='partial'
        assert raw==before
        return
    assert result.get('held') is False
    assert result['publication_state']=='partial'
    assert result['withheld_product_count']==len(quarantined)
    assert result['guidance']['disposition']=='VISA_EXEMPT'
    assert result['apply_steps']==[]
    for name in quarantined:
        assert name not in json.dumps(result,ensure_ascii=False)
    assert raw==before  # no cache, evidence or QC mutation
    qc=tstation.records_for_route(case['route'],raw['guidance'],raw['source_verified'])
    assert [r['visa_type_name'] for r in qc]==[r['visa_type_name'] for r in records]
    assert len(qc)==len(result['product_publication'])
    assert [p['held'] for p in result['product_publication']]==[r['_evidence_low'] for r in records]
    assert [r['_product_index'] for r in qc]==list(range(len(qc)))


@pytest.mark.parametrize('reason',['unsupported_default','invariant','dispute','validation',
                                  'pending','stale','previous_hold','expired','future','malformed_bound'])
def test_partial_publication_never_clears_other_holds(reason,monkeypatch):
    monkeypatch.setenv('ELLIS_HOLD_UNCERTAIN','1')
    case=CASES[0];raw=envelope(case)
    if reason=='unsupported_default':raw['source_verified']=None
    elif reason=='invariant':raw['guidance']['government_fee']={'amount':999,'currency':'USD'}
    elif reason=='dispute':raw['grounded_check']={'disputed_fields':['passport_validity']}
    elif reason=='validation':raw['contradictions']=['application channel conflicts with official instructions']
    elif reason=='pending':raw['detail_pending']=True
    elif reason=='stale':raw['stale']=True
    elif reason=='previous_hold':raw['held']=True
    else:
        proof=raw['source_verified']['field_provenance']['disposition']
        if reason=='expired':proof['effective_to']='2000-01-01'
        elif reason=='future':proof['effective_from']='2099-01-01'
        else:proof['effective_to']='after Christmas'
    result=apply_records_hold(case['route'],raw)
    assert result['held'] is True
    assert result.get('publication_state')!='partial'


def test_plain_low_completeness_or_documented_absence_is_not_a_hold(monkeypatch):
    monkeypatch.setenv('ELLIS_HOLD_UNCERTAIN','1')
    case=CASES[0];raw=envelope(case)
    raw['guidance']['visa_products']=[]
    raw['guidance']['unpublished_fields']=['info_validity','consulate_district']
    row=tstation.records_for_route(case['route'],raw['guidance'],raw['source_verified'])[0]
    assert row['confidence_level']=='Medium'
    assert row['_evidence_low'] is False
    assert apply_records_hold(case['route'],raw)['held'] is False


def test_withdrawn_alternative_cannot_leak_via_derived_or_future_containers(monkeypatch):
    monkeypatch.setenv('ELLIS_HOLD_UNCERTAIN','1')
    case=CASES[0];raw=envelope(case);needle='UNVERIFIED_PRODUCT_CLAIM_987654321'
    raw['guidance']['visa_products'][1]['notes']=needle
    for key in ('workflow_plan','apply_steps','advisories','reply','future_claims'):
        raw[key]=[needle]
    raw['guidance']['future_claims']={'value':needle}
    raw['guidance']['uncertainty']=[needle]
    raw['source_verified']['note']=needle+' official evidence confirms default entry'
    raw['source_verified']['field_provenance']['visa_products']['note']=needle
    result=apply_records_hold(case['route'],raw)
    assert result['publication_state']=='partial'
    assert needle not in json.dumps(result)
    assert needle in json.dumps(raw)


def test_conditional_default_keeps_its_scope_limits():
    case=next(c for c in CASES if c['cache_key'].startswith('HKG|HKG|OMN|'))
    result=apply_records_hold(case['route'],envelope(case))
    assert result['publication_state']=='partial'
    assert result['guidance']['requirement_detail']=='conditional_visa_free'
    conditions=' '.join(result['guidance']['exceptions'])
    assert 'cannot be extended or converted' in conditions
    assert 'return ticket' in conditions
    assert 'health insurance' in conditions


def test_unknown_extra_proof_is_never_credited_to_default():
    case=CASES[0];raw=envelope(case)
    raw['source_verified']['fields']=['visa_products']
    raw['source_verified']['field_provenance'].pop('disposition')
    assert apply_records_hold(case['route'],raw).get('publication_state')!='partial'


def test_qc_api_retains_product_values_and_exposes_separate_publication_states(db,client,monkeypatch):
    from app.visa_snapshot.models import KimiRouteGuidanceCache
    from app import main
    case=CASES[0];route=case['route'];raw=envelope(case)
    db.query(KimiRouteGuidanceCache).delete();db.commit()
    now=datetime.now(timezone.utc).replace(tzinfo=None)
    cache=KimiRouteGuidanceCache(cache_key=case['cache_key'],route=route,guidance=deepcopy(raw['guidance']),
        status='KIMI_PRIMARY',model='fixture',generated_at=now,fresh_until=now+timedelta(days=1),
        verification={},missing_fields=[],contradictions=[])
    db.add(cache);db.commit()
    monkeypatch.setattr(kp,'apply_verified_overrides',lambda out,route:dict(out,source_verified=deepcopy(raw['source_verified']),held=False,review_required=False))
    monkeypatch.setenv('ELLIS_HOLD_UNCERTAIN','1')
    response=client.get('/database/records',headers={'authorization':'Bearer admin-token','x-org-id':'scope','x-user-id':'reviewer'})
    assert response.status_code==200,response.text
    records=response.json()['records']
    assert len(records)==3
    assert [r['held'] for r in records]==[False,True,True]
    assert all(r['route_held'] is False for r in records)
    assert [r['visa_fee_amount'] for r in records]==[0,125,190]
    assert records[1]['publication_reason']=='optional_product_evidence_pending'
    assert records[0]['publication_state']=='published'


def test_supported_separate_alternative_is_retained_with_its_conditions():
    case=CASES[0];raw=envelope(case)
    own=deepcopy(raw['guidance']['visa_products'][1])
    own.update(type='Published tourist eVisa',source_url=raw['source_verified']['source_url'],
               verified_at=raw['source_verified']['verified_at'],
               source_quote='Tourist eVisa available for eligible ordinary passport holders.',
               notes='Only for a stay longer than the visa exemption; return ticket required.')
    raw['guidance']['visa_products'].append(own)
    result=apply_records_hold(case['route'],raw)
    assert result['publication_state']=='partial'
    assert len(result['guidance']['visa_products'])==2
    assert result['guidance']['visa_products'][1]['type']==own['type']
    assert result['guidance']['visa_products'][1]['notes']==own['notes']
    assert result['withheld_product_count']==2


def test_same_permission_unsupported_product_cannot_be_called_optional():
    case=CASES[0];raw=envelope(case)
    raw['guidance']['visa_products'][0]['field_provenance']={'disposition':{'status':'unknown'}}
    assert apply_records_hold(case['route'],raw).get('publication_state')!='partial'


def test_oman_conditions_survive_excluding_longer_stay_evisa_instructions():
    case=next(c for c in CASES if c['cache_key'].startswith('HKG|HKG|OMN|'))
    result=apply_records_hold(case['route'],envelope(case))
    conditions=' '.join(result['guidance']['exceptions'])
    for term in ('return ticket','confirmed hotel booking','health insurance','sufficient funds'):
        assert term in conditions
    assert 'eVisa' not in conditions


def test_lookup_and_chat_share_sanitized_projection_and_history_cannot_restore_products(client,monkeypatch):
    from app.visa_snapshot import assistant
    from app import main
    case=CASES[0];raw=envelope(case)
    raw.update(cached=True,status='KIMI_PRIMARY')
    monkeypatch.setenv('ELLIS_HOLD_UNCERTAIN','1')
    monkeypatch.setattr(kp,'is_available',lambda:True)
    monkeypatch.setattr(kp,'get_route_guidance',lambda *a,**kw:deepcopy(raw))
    monkeypatch.setattr(main,'_ground_on_access',lambda *a,**kw:None)
    monkeypatch.setattr(assistant,'compose_reply_ex',lambda *a,**kw:pytest.fail('partial answer must not enter composer with withdrawn history'))
    headers={'authorization':'Bearer dev-token','x-org-id':'scope','x-user-id':'reader'}
    lookup=client.post('/database/lookup',headers=headers,json={'nationality':'HKG','destination':'LCA'})
    assert lookup.status_code==200,lookup.text
    result=lookup.json()
    assert result['publication_state']=='partial'
    chat=client.post('/database/ask',headers=headers,json={
        'question':'Hong Kong passport tourism in Saint Lucia?',
        'context':{'nationality':'HKG','destination':'LCA','travel_purpose':'tourism','travel_document_type':'ordinary_passport'},
        'history':[{'role':'assistant','text':'Buy Non-Immigrant Visa, multiple entry for 190 XCD.'}],'lang':'en'})
    assert chat.status_code==200,chat.text
    answer=chat.json()
    assert answer['publication_state']=='partial'
    assert answer['guidance']==result['guidance']
    assert answer['reply_source']=='facts'
    assert 'Some alternative visa options are still being checked' in answer['reply']
    assert '190 XCD' not in json.dumps(answer)


def test_scoped_projection_rebuilds_arithmetic_advisories_from_default_bound():
    case=CASES[0];raw=envelope(case);route=deepcopy(case['route'])
    route.update(arrival_date='2026-10-01',departure_date='2026-12-31')
    result=apply_records_hold(route,raw)
    assert result['publication_state']=='partial'
    assert any('exceeds the permitted stay' in s for s in result['advisories'])


@pytest.mark.parametrize('where',['default_note','default_exception'])
def test_generic_consular_visa_alias_cannot_leak_optional_fee(where):
    case=CASES[0];raw=envelope(case)
    text='For a longer stay, get a consular visa for 190 XCD.'
    if where=='default_note':raw['guidance']['visa_products'][0]['notes']=text
    else:
        raw['guidance']['exceptions']=[text]
        raw['source_verified']['fields'].append('exceptions')
    result=apply_records_hold(case['route'],raw)
    assert result['publication_state']=='partial'
    assert '190 XCD' not in json.dumps(result)


def test_explicit_unknown_field_proof_overrides_legacy_route_claim_list():
    case=CASES[0];raw=envelope(case)
    raw['guidance']['exceptions']=['UNREVIEWED_ENTRY_CONDITION_99']
    raw['source_verified']['fields'].append('exceptions')
    raw['source_verified']['field_provenance']['exceptions']={'status':'unknown'}
    result=apply_records_hold(case['route'],raw)
    assert result['publication_state']=='partial'
    assert 'UNREVIEWED_ENTRY_CONDITION_99' not in json.dumps(result)


def test_removing_optional_text_must_not_drop_a_mixed_entry_prerequisite():
    case=next(c for c in CASES if c['cache_key'].startswith('HKG|HKG|OMN|'))
    raw=envelope(case)
    mixed='You must hold a return ticket and health insurance for visa-free entry or apply for an eVisa.'
    raw['guidance']['exceptions']=['Visa-free entry is limited to 14 days.',mixed]
    raw['guidance']['application_channel_detail']=mixed
    result=apply_records_hold(case['route'],raw)
    assert result.get('publication_state')!='partial'
    assert result['held'] is True


@pytest.mark.parametrize('element_key',['verified_elements','reviewed_elements'])
def test_partially_reviewed_field_publishes_only_exact_reviewed_elements(element_key):
    case=CASES[0];raw=envelope(case)
    raw['guidance']['exceptions']=['Return ticket required.','UNREVIEWED_RESTRICTION_99']
    raw['source_verified']['fields'].append('exceptions')
    raw['source_verified']['field_provenance']['exceptions']=dict(
        deepcopy(raw['source_verified']['field_provenance']['disposition']),
        status='partial',**{element_key:['Return ticket required.','Invented visa-free promise.']})
    result=apply_records_hold(case['route'],raw)
    assert result['publication_state']=='partial'
    assert result['guidance']['exceptions']==['Return ticket required.']
    assert 'UNREVIEWED_RESTRICTION_99' not in json.dumps(result)
    assert 'Invented visa-free promise.' not in json.dumps(result)


def test_published_optional_product_cannot_forward_generic_withheld_sibling_prose():
    case=CASES[0];raw=envelope(case)
    own=deepcopy(raw['guidance']['visa_products'][1])
    own.update(type='Published tourist eVisa',source_url=raw['source_verified']['source_url'],
               verified_at=raw['source_verified']['verified_at'],
               source_quote='Tourist eVisa available for eligible ordinary passport holders.',
               notes='For a longer stay, get a consular visa for 190 XCD.')
    raw['guidance']['visa_products'].append(own)
    result=apply_records_hold(case['route'],raw)
    assert result.get('publication_state')!='partial'


def test_explicit_unknown_default_proof_cannot_inherit_top_level_disposition_credit():
    case=CASES[0];raw=envelope(case)
    raw['source_verified']['field_provenance']['disposition']['status']='unknown'
    assert apply_records_hold(case['route'],raw).get('publication_state')!='partial'


def test_exemption_does_not_invent_a_usd_currency_fact():
    case=CASES[0]
    result=apply_records_hold(case['route'],envelope(case))
    assert result['guidance']['government_fee']=={'amount':0,'currency':None}


@pytest.mark.parametrize('mixed',[
    '免签入境必须持有返程机票及保险，或申请电子签证。',
    '免簽入境須持有返程機票及保險，或申請電子簽證。',
])
def test_mixed_chinese_prerequisites_cannot_be_deleted_to_release_default(mixed):
    case=next(c for c in CASES if c['cache_key'].startswith('HKG|HKG|OMN|'))
    raw=envelope(case)
    raw['guidance']['exceptions']=['Visa-free entry is limited to 14 days.',mixed]
    assert apply_records_hold(case['route'],raw).get('publication_state')!='partial'


def test_beyond_stay_sentence_cannot_hide_an_independent_default_prerequisite():
    case=next(c for c in CASES if c['cache_key'].startswith('HKG|HKG|OMN|'))
    raw=envelope(case)
    mixed='For stays beyond 14 days, apply for an eVisa; otherwise entry requires a return ticket and health insurance.'
    raw['guidance']['exceptions']=['Visa-free entry is limited to 14 days.',mixed]
    raw['guidance']['application_channel_detail']=mixed
    assert apply_records_hold(case['route'],raw).get('publication_state')!='partial'
