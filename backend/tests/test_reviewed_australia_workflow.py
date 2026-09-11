"""Real exact-layer ETA workflow correction, loader and canonical reader checks."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from scripts import convert_reviewed_australia_workflow as c
from scripts.prepare_reviewed_product_patch import PatchRejected
from app.visa_snapshot import verified_overrides as vo, kimi_primary as kp, records_guard, tstation
from app.visa_snapshot.models import KimiRouteGuidanceCache, DatabaseIssueReport, DatabaseChangeLog
SEED=Path(__file__).resolve().parents[2]/'data/database_seed'
NAME='reviewed_australia_workflow_overlay_20260910.json'
@pytest.fixture
def data():
    m=json.loads((SEED/'reviewed_australia_workflow_manifest_20260910.json').read_text())
    l=[dict(deepcopy(e['baseline']),cache_key=e['cache_key']) for e in m['entries']]
    return m,l,json.loads((SEED/NAME).read_text())

def test_real_exact_rebuild_and_preservation(data):
    m,l,o=data;before=deepcopy(data)
    actual,reports,preview=c.verify_prepared(m,l,o)
    assert actual==o and len(reports)==2 and data==before
    for layer in l:
        v=preview[layer['cache_key']]
        assert {k:v['guidance'][k] for k in c.FIELDS}==c.VALUES
        assert {k:x for k,x in v['guidance'].items() if k not in c.FIELDS}=={k:x for k,x in layer['merged_guidance'].items() if k not in c.FIELDS}
        assert tstation.records_for_route(layer['route'],v['guidance'],v['source_provenance'])==tstation.records_for_route(layer['route'],layer['merged_guidance'],layer['source_provenance'])
        assert v['source_provenance']['field_provenance']['disposition']==layer['source_provenance']['field_provenance']['disposition']
        assert v['guidance']['visa_products']==layer['merged_guidance']['visa_products']
        for f in c.FIELDS:
            p=v['source_provenance']['field_provenance'][f]
            assert p['verified_at']=='2026-09-10' and p['verifier']=='ai'
            assert p['subject']['passport_nationality']==layer['route']['passport_nationality']

@pytest.mark.parametrize('route_index',[0,1])
@pytest.mark.parametrize('field',c.BASELINE_KEYS)
def test_each_current_layer_change_fails_before_output(data,monkeypatch,route_index,field):
    m,l,_=data;v=l[route_index][field]
    if isinstance(v,dict):v['unreviewed']='changed'
    else:v.append({'unreviewed':'changed'})
    before=deepcopy(l)
    monkeypatch.setattr(c,'_entry',lambda *a:pytest.fail('Output constructed before all CAS checks'))
    with pytest.raises(PatchRejected,match='Current layer changed'):c.convert(m,l)
    assert l==before

@pytest.mark.parametrize('kind',['missing','duplicate','wrong_key','missing_layer','seed_reordered'])
def test_live_baseline_boundaries(data,kind):
    m,l,_=data
    if kind=='missing':l.pop()
    elif kind=='duplicate':l.append(deepcopy(l[0]))
    elif kind=='wrong_key':l[0]['cache_key']=l[0]['cache_key'].replace('HKG','CHN')
    elif kind=='missing_layer':l[0].pop('operator_entries')
    else:l[0]['seed_entries'].reverse()
    with pytest.raises(PatchRejected):c.convert(m,l)

@pytest.mark.parametrize('kind',['field','url','quote','hash','capture','date','subject','extra','delete','verifier','passport','operator'])
def test_unreviewed_fact_and_evidence_changes_rejected(data,kind):
    m,l,_=data;s=deepcopy(m['entries'][0]['specification']);ch=s['changes'][0]
    if kind=='field':ch['new']=['Apply at a private online portal.']
    elif kind=='url':ch['proof']['evidence'][0]['source_url']='https://www.gov.uk/eta'
    elif kind=='quote':ch['proof']['evidence'][0]['quote']='Eligible'
    elif kind=='hash':ch['proof']['evidence'][0]['quote_sha256']='0'*64
    elif kind=='capture':s['sources'][0]['text']+=' altered'
    elif kind=='date':ch['proof']['verified_at']='2099-01-01'
    elif kind=='subject':ch['proof']['subject']['passport_nationality']='CHN'
    elif kind=='extra':s['changes'].append(dict(ch,field='government_fee'))
    elif kind=='delete':s['changes'].pop()
    elif kind=='verifier':ch['proof']['verifier']='human'
    elif kind=='passport':s['route']['travel_document_type']='diplomatic_passport'
    else:l[0]['operator_entries'].append({'fields':{'processing_time':'operator'}})
    with pytest.raises(PatchRejected):c._validate(s,l[0])

@pytest.mark.parametrize('kind',['root','entry_metadata','product','field_proof','field','delete_entry'])
def test_complete_prepared_output_equality(data,kind):
    m,l,o=data
    if kind=='root':o['new_release']=True
    elif kind=='entry_metadata':o['entries'][0]['verified_at']='2026-09-10'
    elif kind=='product':o['entries'][0]['fields']['visa_products'][0]['fee']['amount']=0
    elif kind=='field_proof':o['entries'][0]['field_provenance']['disposition']['verifier']='human'
    elif kind=='field':o['entries'][0]['fields']['photo_requirements']='A saved photograph is enough'
    else:o['entries'].pop()
    with pytest.raises(PatchRejected,match='Complete prepared'):c.verify_prepared(m,l,o)

@pytest.mark.parametrize('field',sorted(c.FIELDS))
@pytest.mark.parametrize('bad',[True,42,{'step':'scan'},['ok',None]])
def test_loader_quarantines_invalid_workflow_shapes_but_keeps_facts(data,field,bad):
    entry=deepcopy(data[2]['entries'][0]);entry['fields'][field]=bad
    assert any(x.startswith(field) for x in vo._field_errors(entry['fields']))
    parsed=vo._parse_rows([entry],{})['HKG|AUS|tourism']
    assert field not in parsed['fields'] and field not in parsed['field_provenance']
    assert parsed['fields']['visa_products']==entry['fields']['visa_products']
    assert parsed['field_provenance']['disposition']==entry['field_provenance']['disposition']

@pytest.mark.parametrize('field',sorted(c.FIELDS))
def test_reviewers_voice_rejected(data,field):
    entry=deepcopy(data[2]['entries'][0]);entry['fields'][field]='The submitted row is wrong' if field=='photo_requirements' else ['The submitted row is wrong']
    assert field not in vo._parse_rows([entry],{})['HKG|AUS|tourism']['fields']

@pytest.fixture
def stores(tmp_path,monkeypatch,data):
    m,l,o=data;core=tmp_path/'verified_overrides.json';overlay=tmp_path/NAME;op=tmp_path/'operator.json'
    core.write_text(json.dumps([r for layer in l for r in layer['seed_entries']]))
    overlay.write_text(json.dumps(o));op.write_text('[]')
    monkeypatch.setattr(vo,'OVERRIDES',core)
    monkeypatch.setattr(vo,'operator_overrides_path',lambda:op)
    monkeypatch.setattr(vo,'_reviewed_overlay_paths',lambda:[overlay])
    vo.reload()
    yield core,overlay,op
    vo.reload()

def test_actual_registered_loader_exact_guidance_and_authorship(data,stores):
    m,l,o=data;_,_,preview=c.convert(m,l)
    assert vo._load_table().store_errors==()
    for layer in l:
        g,p=vo.apply(layer['raw_guidance'],layer['route'])
        assert g==preview[layer['cache_key']]['guidance'] and p==preview[layer['cache_key']]['source_provenance']

@pytest.mark.parametrize('with_open_issue',[False,True])
def test_canonical_reader_rebuilds_workflow_without_store_writes(data,stores,monkeypatch,with_open_issue):
    _,layers,_=data;engine=create_engine('sqlite:///:memory:')
    for model in (KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog):model.__table__.create(engine)
    monkeypatch.setattr(kp,'_call',lambda *a,**kw:pytest.fail('Cached reader must not generate'))
    with Session(engine) as db:
        for layer in layers:
            db.add(KimiRouteGuidanceCache(cache_key=layer['cache_key'],route=deepcopy(layer['route']),guidance=deepcopy(layer['raw_guidance']),verification={'fixture':'unchanged'},fresh_until=datetime(2099,1,1,tzinfo=timezone.utc),status='KIMI_PRIMARY'))
            db.add(DatabaseChangeLog(cache_key=layer['cache_key'],action='add',origin='fixture',changes={'history':'retained'}))
            if with_open_issue:db.add(DatabaseIssueReport(cache_key=layer['cache_key'],route=deepcopy(layer['route']),field='government_fee',status='open',reported_by='freshness_monitor',proposal={'fields':{'government_fee':{'record_holds':'7 calendar days'}}}))
        db.commit()
        def snapshot():
            return {model.__name__:[{col.name:deepcopy(getattr(r,col.name)) for col in model.__table__.columns} for r in db.scalars(select(model))] for model in (KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog)}
        before=snapshot()
        for layer in layers:
            out=kp.get_route_guidance(db,layer['route'])
            assert out['cached'] and not out['stale']
            assert {f:out['guidance'][f] for f in c.FIELDS}==c.VALUES
            assert out['guidance']['visa_products']==layer['merged_guidance']['visa_products']
            assert out['source_verified']['field_provenance']['disposition']==layer['source_provenance']['field_provenance']['disposition']
            assert out['apply_steps']==kp.canonical_steps(out['guidance'],route=layer['route'],source_verified=out['source_verified'])
            assert out['workflow_plan']==kp.derive_workflow_plan(out['guidance'],route=layer['route'],source_verified=out['source_verified'])
            initial=' '.join(out['guidance']['account_registration_steps']).lower()
            assert 'immiaccount' not in initial and 'online eta service' not in initial
            assert 'further information' in out['guidance']['submission_process'][-1] and 'ImmiAccount' in out['guidance']['submission_process'][-1]
            guarded=records_guard.apply_records_hold(layer['route'],out,db)
            assert bool(guarded.get('held')) is with_open_issue
            if with_open_issue:assert records_guard.held_envelope(guarded)['guidance'] is None
        assert snapshot()==before

@pytest.mark.parametrize('route_index',[0,1])
def test_actual_reader_eta_app_sequence_has_full_conditions_and_no_web_filing(data,stores,route_index):
    layer=data[1][route_index]
    out=kp.apply_verified_overrides(kp._result('KIMI_PRIMARY',layer['raw_guidance'],cached=True,stale=False),layer['route'])
    assert out['apply_steps']==[*c.VALUES['account_registration_steps'],*c.VALUES['submission_process']]
    assert len(out['apply_steps'])==7
    assert out['apply_steps'][0]=='Download and open the official Australian ETA app.'
    assert out['apply_steps'][-1].startswith('If Home Affairs sends a request for further information,')
    assert out['apply_steps'][-1].endswith('Do not lodge another ETA app application for the same request.')
    names={s['step'] for s in out['workflow_plan']}
    assert not names & {'generate_route_adapter','account_registration'}
    assert [s['instruction'] for s in out['workflow_plan']]==out['apply_steps']
    assert 'pay the service fee and submit' in out['apply_steps'][4]
    assert out['guidance']['government_fee']==layer['merged_guidance']['government_fee']

@pytest.mark.parametrize('changed',[
    {'disposition':'VISA_REQUIRED'},
    {'requirement_detail':'evisa'},
    {'official_portal_url':'https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/visitor-600'},
    {'official_portal_url':'https://immi.homeaffairs.gov.au.evil.example/visas/getting-a-visa/visa-listing/electronic-travel-authority-601'},
    {'official_portal_url':'https://immi.homeaffairs.gov.au@evil.example/visas/getting-a-visa/visa-listing/electronic-travel-authority-601'},
    {'official_portal_url':'http://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601'},
    {'application_channel_detail':'Apply through ImmiAccount or the Australian ETA app.'},
])
def test_app_selector_does_not_expand_to_other_visa_or_portal(data,changed):
    g=deepcopy(data[1][0]['merged_guidance']);g.update(changed)
    assert not kp._australian_eta_app_workflow(g)
    # Neither an information URL nor an unsupported product establishes an application sequence.
    assert kp.derive_workflow_plan(g)==[]
