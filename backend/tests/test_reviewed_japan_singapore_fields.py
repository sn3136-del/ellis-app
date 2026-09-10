from copy import deepcopy
from datetime import datetime,timezone
import json
from pathlib import Path
import pytest
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from scripts import convert_reviewed_japan_singapore_fields as c
from scripts.prepare_reviewed_product_patch import PatchRejected
from app.visa_snapshot import verified_overrides as vo,kimi_primary as kp,tstation,records_guard
from app.visa_snapshot import reviewed_japan_warning_resolution as warnings
from app.visa_snapshot.models import KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog

DATA=Path(__file__).resolve().parents[2]/'data/database_seed'
@pytest.fixture
def data():
    m=json.loads((DATA/c.MANIFEST).read_text());o=json.loads((DATA/c.OVERLAY).read_text())
    l=[dict(deepcopy(r['baseline']),cache_key=r['cache_key']) for r in m['routes']]
    return m,l,o

def test_exact_rebuild_preserves_inputs_products_and_unrelated_proofs(data):
    m,l,o=data;before=deepcopy(data)
    actual,reports,preview=c.convert(m,l)
    assert actual==o and data==before
    for report in reports:
        assert report['products_unchanged'] and report['open_issues_untouched'] and report['raw_pending_untouched']
        assert not report['renew_fresh_until']
    for p in preview:
        layer=next(x for x in l if x['cache_key']==p['cache_key']);changed=set(c.APPROVED[p['cache_key']]['changes'])
        assert p['guidance'].get('visa_products')==layer['merged_guidance'].get('visa_products')
        for f,proof in layer['source_provenance']['field_provenance'].items():
            if f not in changed:assert p['source_verified']['field_provenance'][f]==proof

@pytest.mark.parametrize('index',[0,1,2])
@pytest.mark.parametrize('field',c.BASELINE_KEYS)
def test_each_six_layer_mutation_aborts_before_output(data,index,field,monkeypatch):
    m,l,_=data;v=l[index][field]
    if isinstance(v,dict):v['new_review']='unreviewed'
    else:v.append({'new_review':'unreviewed'})
    monkeypatch.setattr(vo,'_parse_rows',lambda *a,**k:pytest.fail('Output began before complete CAS validation'))
    with pytest.raises(PatchRejected):c.convert(m,l)

@pytest.mark.parametrize('kind',['duplicate','missing','alias','route','source','quote','extra_field','old_warning','new_warning','unknown_credit'])
def test_scope_source_and_warning_boundaries(data,kind):
    m,l,_=data;s=m['specification'];r=s['routes'][0]
    if kind=='duplicate':l.append(deepcopy(l[0]))
    elif kind=='missing':l.pop()
    elif kind=='alias':l[0]['cache_key']=l[0]['cache_key'].replace('HKG','HK',1)
    elif kind=='route':r['route']['travel_document_type']='diplomatic_passport'
    elif kind=='source':s['sources'][0]['url']='https://mfa.gov.sg.evil.example/'
    elif kind=='quote':r['changes'][0]['evidence'][0]['quote']='Hong Kong is always visa free'
    elif kind=='extra_field':r['changes'].append(dict(r['changes'][0],field='insurance_required'))
    elif kind=='old_warning':r['resolved_uncertainty'].pop()
    elif kind=='new_warning':l[0]['merged_guidance']['uncertainty'].append({'field':'new','reason':'unresolved'})
    else:
        r=next(r for r in s['routes'] if 'SGP' in r['cache_key'])
        next(ch for ch in r['changes'] if ch['field']=='accommodation_evidence')['new']='Not published'
    with pytest.raises((PatchRejected,KeyError)):c.convert(m,l)

@pytest.mark.parametrize('kind',['metadata','value','proof','delete','warning'])
def test_complete_overlay_equality_rejects_staged_tampering(data,kind):
    m,l,o=data
    if kind=='metadata':o['new_release']=True
    elif kind=='value':o['entries'][0]['fields']['insurance_required']=True
    elif kind=='proof':o['entries'][0]['field_provenance']['disposition']['verifier']='human'
    elif kind=='delete':o['entries'].pop()
    else:o['entries'][0]['fields']['uncertainty']=[]
    with pytest.raises(PatchRejected):c.verify_prepared(m['specification'],l,m,o)

@pytest.mark.parametrize('bad',[{},'none',True,[None],[{'name':'Yellow fever'}],
    [{'name':'Yellow fever','applicability':'conditional','trigger_countries':[],'trigger':None,'question':None}],
    [{'name':'Yellow fever','applicability':'conditional','trigger_countries':['Russia'],'trigger':'travel','question':'travel?'}]])
def test_health_shape_quarantine_preserves_other_fields(data,bad):
    e=deepcopy(data[2]['entries'][1]);e['fields']['health_requirements']=bad
    assert warnings.health_shape_errors(bad)
    parsed=vo._parse_rows([e],{})
    hit=next(iter(parsed.values()))
    assert 'health_requirements' not in hit['fields'] and 'health_requirements' not in hit['field_provenance']
    assert hit['fields']['disposition']=='VISA_EXEMPT'

@pytest.fixture
def stores(data,tmp_path,monkeypatch):
    m,l,o=data;core=tmp_path/'verified_overrides.json';op=tmp_path/'operators.json';overlay=tmp_path/c.OVERLAY
    core.write_text(json.dumps([e for layer in l for e in layer['seed_entries']]));op.write_text('[]')
    overlay.write_text(json.dumps(o));(tmp_path/c.MANIFEST).write_text(json.dumps(m))
    monkeypatch.setattr(vo,'OVERRIDES',core);monkeypatch.setattr(vo,'operator_overrides_path',lambda:op)
    monkeypatch.setattr(vo,'_reviewed_overlay_paths',lambda:[overlay]);vo.reload();warnings._rebuilt.cache_clear()
    yield tmp_path
    vo.reload();warnings._rebuilt.cache_clear()

def test_actual_registered_reader_only_resolves_exact_japan_objects(data,stores):
    for layer in data[1]:
        g,p=vo.apply(layer['raw_guidance'],layer['route'])
        if 'JPN' in layer['cache_key']:assert g['uncertainty']==[]
        else:
            assert g['permitted_stay_days'] is None
            row=tstation.records_for_route(layer['route'],g,p)[0]
            assert row['max_stay_duration'] is None and row['max_stay_unit'] is None
            assert 'e-Pass' in row['max_stay_text']
            assert 'more than 12 hours' in g['health_requirements'][0]['trigger']
            assert g['biometrics_required'] is None and 'during immigration clearance' in ' '.join(g['exceptions'])

@pytest.mark.parametrize('kind',['added','altered','other_fact','other_route','no_manifest','tampered_overlay'])
def test_warning_resolution_is_not_an_arbitrary_clear(data,stores,kind):
    layer=next(l for l in data[1] if 'JPN' in l['cache_key']);g=deepcopy(layer['raw_guidance']);route=deepcopy(layer['route'])
    added={'field':'new_current_condition','reason':'Unresolved; do not erase.'}
    if kind=='added':g['uncertainty'].append(added)
    elif kind=='altered':g['uncertainty'][0]['reason']+=' New source conflict.'
    elif kind=='other_fact':g['insurance_required']=True
    elif kind=='other_route':route['travel_document_type']='diplomatic_passport'
    elif kind=='no_manifest':(stores/c.MANIFEST).unlink()
    else:
        o=json.loads((stores/c.OVERLAY).read_text());o['tampered']=True;(stores/c.OVERLAY).write_text(json.dumps(o));vo.reload()
    out,_=vo.apply(g,route)
    if kind=='added':assert out['uncertainty']==[added]
    elif kind=='altered':assert out['uncertainty']==[g['uncertainty'][0]]
    else:assert out['uncertainty']==g['uncertainty']

@pytest.mark.parametrize('issue',[False,True])
@pytest.mark.parametrize('pending',[False,True])
def test_actual_canonical_read_keeps_raw_pending_issues_cache_dates_and_history(data,stores,monkeypatch,issue,pending):
    engine=create_engine('sqlite:///:memory:')
    for model in (KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog):model.__table__.create(engine)
    monkeypatch.setattr(kp,'_call',lambda *a,**k:pytest.fail('No regeneration'))
    with Session(engine) as db:
        for layer in data[1]:
            db.add(KimiRouteGuidanceCache(cache_key=layer['cache_key'],route=deepcopy(layer['route']),guidance=deepcopy(layer['raw_guidance']),verification={'detail_pending':pending},fresh_until=datetime(2099,1,1,tzinfo=timezone.utc),status='KIMI_PRIMARY'))
            db.add(DatabaseChangeLog(cache_key=layer['cache_key'],action='add',origin='fixture',changes={'unchanged':True}))
            if issue:db.add(DatabaseIssueReport(cache_key=layer['cache_key'],route=deepcopy(layer['route']),field='disposition',status='open',reported_by='freshness_monitor',proposal={'fields':{'disposition':{'record_holds':'VISA_EXEMPT'}}}))
        db.commit()
        def snapshot():return {m.__name__:[{col.name:deepcopy(getattr(r,col.name)) for col in m.__table__.columns} for r in db.scalars(select(m))] for m in (KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog)}
        before=snapshot()
        for layer in data[1]:
            out=kp.get_route_guidance(db,layer['route']);assert out['cached']
            if pending:assert out['detail_pending']
            guarded=records_guard.apply_records_hold(layer['route'],out,db)
            if issue or pending:assert guarded.get('held')
            else:assert not guarded.get('held')
            assert out['guidance'].get('visa_products')==layer['merged_guidance'].get('visa_products')
        assert snapshot()==before

@pytest.mark.parametrize('text,days,expected',[
    ('Up to 30 days',30,30),
    ('Actual permitted stay is determined by the e-Pass issued at entry. Visa-free social visits up to 30 days.',None,None),
    ('Actual stay is determined by the e-Pass. A stale field says 90 days.',90,None),
    ('30 days; apply for an e-Pass before travel',30,30),
    ('The stay is not determined by the e-Pass. Maximum 30 days.',30,30),
    ('A stay of 30 days is granted on your e-Pass.',30,30),
    ('Maximum stay of 90 days. An e-Pass is issued.',90,90),
])
def test_stay_projection_keeps_explicit_epass_discretion(text,days,expected):
    row={};tstation._set_stay(row,text,days);assert row['max_stay_duration']==expected
