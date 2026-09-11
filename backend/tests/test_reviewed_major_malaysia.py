from copy import deepcopy
import hashlib,json
from pathlib import Path
from unittest.mock import patch
import pytest
from scripts import convert_reviewed_major_malaysia as c
from scripts.prepare_reviewed_product_patch import PatchRejected,digest
from app.visa_snapshot import verified_overrides as vo,records_guard,tstation
DATA=Path(__file__).resolve().parents[2]/'data/database_seed'
@pytest.fixture
def inputs():
 m=json.loads((DATA/'reviewed_major_malaysia_manifest_20260910.json').read_text())
 return m['specification'],[dict(deepcopy(r['baseline']),cache_key=r['cache_key']) for r in m['routes']]
def run(spec,layers):return c.convert(c.build_manifest(spec,layers),layers)

def test_actual_result_preserves_raw_unreviewed_scope_and_explicit_unknown(inputs):
 spec,layers=inputs;before=deepcopy(layers);o,r=run(spec,layers)
 assert layers==before and len(o['entries'])==2
 for x in r['routes']:
  l=next(a for a in layers if a['cache_key']==x['cache_key']);g=x['guidance'];p=x['provenance']
  for f in ('disposition','requirement_detail','permitted_stay','permitted_stay_days','confidence','health_requirements','uncertainty'):
   assert g[f]==l['merged_guidance'][f]
  assert g['permitted_stay_days']==90 and not g.get('visa_products') and 'unpublished_fields' not in g
  assert g['insurance_required'] is False and p['field_provenance']['insurance_required']['quote']==c.INSURANCE
  assert p['verified_at']=='2026-09-10' and 'effective_to' not in p and 'effective_from' not in p
  for f in ('photo_requirements','biometrics_required'):
   fp=p['field_provenance'][f];assert g[f] is None and fp['status']=='unknown' and not fp['source_url'] and fp['verified_at'] is None
  row,=x['records'];assert row['confidence_level']=='Medium' and row['max_stay_duration']==90 and not row.get('info_validity')  # checked, with gaps
 assert not any(r[k] for k in ('new_grounded_check','renew_fresh_until','new_release','raw_or_issue_writes'))

def test_mdac_and_documents_keep_conditions_without_false_child_exception(inputs):
 _,r=run(*inputs)
 for x in r['routes']:
  g=x['guidance'];assert g['arrival_card']=={'required':True,'name':'Malaysia Digital Arrival Card (MDAC)','submission_window':'Submit before arrival; your arrival must be within 3 days including the date of submission.'}
  assert 'is required' in g['onward_travel_evidence'] and 'for the full stay is required' in g['accommodation_evidence']
  assert c.MDAC_NOTE in g['exceptions'] and 'children under 3 may be exempt' not in json.dumps(g)
  assert 'unless covered by an official exemption' in g['required_documents'][-1]
  ep=x['provenance']['field_provenance']['exceptions'];assert ep['status']=='partial' and len(ep['retained_unverified_elements'])==1
  assert not set(ep['verified_elements']) & set(ep['retained_unverified_elements'])
 hk=next(x for x in r['routes'] if x['cache_key'].startswith('HKG'))
 assert hk['guidance']['passport_validity']=='More than 6 months from entry into Malaysia'
 assert hk['guidance']['passport_validity_requirement'] is None and hk['provenance']['field_provenance']['passport_validity_requirement']['status']=='unknown'
 assert 'select Hong Kong' in hk['guidance']['exceptions'][-1]
 us=next(x for x in r['routes'] if x['cache_key'].startswith('USA'))
 assert us['guidance']['passport_validity_requirement']=={'kind':'months_after_arrival','months':6}
 assert us['guidance']['exceptions'][0]==c.EXTENSION_VALUE

def test_registered_loader_and_active_conflict_guard(inputs,tmp_path):
 spec,layers=inputs;o,r=run(spec,layers);base=tmp_path/'base.json';over=tmp_path/'over.json';op=tmp_path/'op.json'
 base.write_text(json.dumps([e for l in layers for e in l['seed_entries']]));over.write_text(json.dumps(o));op.write_text('[]')
 with patch.object(vo,'OVERRIDES',base),patch.object(vo,'_reviewed_overlay_paths',return_value=[over]),patch.object(vo,'operator_overrides_path',return_value=op):table=vo._load_table()
 for x in r['routes']:
  l=next(a for a in layers if a['cache_key']==x['cache_key']);key=vo._key(l['route']['passport_nationality'],'MYS','tourism','ordinary_passport')
  with patch.object(vo,'find',return_value=table[key]):g,p=vo.apply(l['raw_guidance'],l['route'])
  assert (g,p)==(x['guidance'],x['provenance']) and tstation.records_for_route(l['route'],g,p)==x['records']
  out=records_guard.apply_records_hold(l['route'],{'guidance':g,'source_verified':p,'grounded_check':{'disputed_fields':['passport_validity']},'operator_released':True})
  assert out['held'] and records_guard.held_envelope(out)['guidance'] is None

@pytest.mark.parametrize('index',[0,1])
@pytest.mark.parametrize('field',c.BASELINE_KEYS)
def test_each_six_layer_cas_race_rejected(inputs,index,field):
 spec,layers=inputs;m=c.build_manifest(spec,layers);v=layers[index][field]
 if isinstance(v,dict):v['concurrent_change']=True
 else:v.append({'concurrent_change':True})
 with pytest.raises(PatchRejected):c.convert(m,layers)
@pytest.mark.parametrize('kind',['missing','duplicate','extra'])
def test_route_set_exact(inputs,kind):
 spec,layers=inputs
 if kind=='missing':spec['routes'].pop()
 elif kind=='duplicate':spec['routes'][1]=deepcopy(spec['routes'][0])
 else:spec['routes'].append(deepcopy(spec['routes'][0]))
 with pytest.raises(PatchRejected):c.build_manifest(spec,layers)
@pytest.mark.parametrize('f,v',[('passport_nationality','CHN'),('lawful_country_of_residence','SGP'),('destination_country','SGP'),('travel_purpose','business'),('travel_document_type','bno'),('arrival_date','2026-12-01')])
def test_wrong_scope_rejected(inputs,f,v):
 spec,layers=inputs;l=layers[0];l['route'][f]=v;r=next(a for a in spec['routes'] if a['cache_key']==l['cache_key']);r['route']=deepcopy(l['route']);r['baseline_sha256']={k:digest(l[k]) for k in c.BASELINE_KEYS}
 with pytest.raises(PatchRejected):c.build_manifest(spec,layers)
@pytest.mark.parametrize('field,value',[('permitted_stay_days',180),('requirement_detail','evisa'),('disposition','VISA_REQUIRED'),('source_url','https://example.com/'),('arrival_card',{'required':False}),('required_documents',['Passport']),('insurance_required',True),('insurance_required',0),('photo_requirements','Not applicable'),('biometrics_required',False),('financial_evidence','Show 1000 USD'),('passport_validity_requirement',{'kind':'months_after_arrival','months':6})])
def test_altered_fact_cannot_borrow_source(inputs,field,value):
 spec,layers=inputs;r=next(a for a in spec['routes'] if a['cache_key'].startswith('HKG'));next(a for a in r['changes'] if a['field']==field)['new']=value
 with pytest.raises(PatchRejected):c.build_manifest(spec,layers)
@pytest.mark.parametrize('mutation',['quote','subject','date','human','source','not_published','new_expiry','duplicate_field','full_exception_credit','extra_field'])
def test_proof_scope_cannot_expand(inputs,mutation):
 spec,layers=inputs;r=spec['routes'][0];p=r['changes'][0]['proof']
 if mutation=='quote':p['evidence'][0]['quote']='No visas ever required.'
 elif mutation=='subject':p['subject']['travel_document_type']='diplomatic_passport'
 elif mutation=='date':p['verified_at']='2026-09-11'
 elif mutation=='human':p['verifier']='human'
 elif mutation=='source':p['evidence'][0]['source_url']=c.URLS['insurance']
 elif mutation=='not_published':next(a for a in r['changes'] if a['field']=='photo_requirements')['proof']['status']='not_published'
 elif mutation=='new_expiry':p['effective_to']='2026-12-31'
 elif mutation=='duplicate_field':r['changes'][1]=deepcopy(r['changes'][0])
 elif mutation=='extra_field':r['changes'].append({'field':'confidence','new':'high'})
 else:
  p=next(a for a in r['changes'] if a['field']=='exceptions')['proof'];p['verified_elements']+=p['retained_unverified_elements'];p['retained_unverified_elements']=[]
 with pytest.raises(PatchRejected):c.build_manifest(spec,layers)
@pytest.mark.parametrize('mutation',['bad_hash','redated','url','changed_context','duplicate'])
def test_source_capture_immutable(inputs,mutation):
 spec,layers=inputs;s=spec['sources'][0]
 if mutation=='bad_hash':s['sha256']='0'*64
 elif mutation=='redated':s['checked_at']='2026-09-11'
 elif mutation=='url':s['url']='https://www.kln.gov.my/other'
 elif mutation=='duplicate':spec['sources'].append(deepcopy(s))
 else:s['text']='Except HKG/USA, '+s['text'];s['sha256']=hashlib.sha256(s['text'].encode()).hexdigest()
 with pytest.raises(PatchRejected):c.build_manifest(spec,layers)
@pytest.mark.parametrize('part',['raw_guidance','merged_guidance','operator_entries'])
def test_new_product_or_operator_cannot_be_rebased_away(inputs,part):
 spec,layers=inputs;l=layers[0]
 if part=='operator_entries':l[part]=[{'fields':{'permitted_stay':'180 days'}}]
 else:l[part]['visa_products']=[{'type':'New optional paid visa','fee':{'amount':100,'currency':'MYR'}}]
 r=next(a for a in spec['routes'] if a['cache_key']==l['cache_key']);r['baseline_sha256']={k:digest(l[k]) for k in c.BASELINE_KEYS}
 with pytest.raises(PatchRejected):c.build_manifest(spec,layers)
def test_usa_plural_demonym_only_owns_its_positive_sentence():
 from app.visa_snapshot.evidence_validator import supports_disposition
 from scripts.convert_reviewed_general_batch import _decision_supported
 assert _decision_supported('VISA_EXEMPT',[c.US_QUOTE],'USA')
 assert supports_disposition(c.US_QUOTE,'VISA_EXEMPT',nationality='USA')
 assert not _decision_supported('VISA_EXEMPT',[c.US_QUOTE],'CAN')
 assert not _decision_supported('VISA_EXEMPT',['American citizens require a visa to visit Malaysia.'],'USA')
 assert not _decision_supported('VISA_EXEMPT',['American citizens are not visa-free.'],'USA')

@pytest.mark.parametrize('active_issue',[False,True])
def test_actual_captured_metadata_cached_reader_neither_regenerates_nor_changes_history(inputs,tmp_path,monkeypatch,active_issue):
 from datetime import datetime
 from sqlalchemy import create_engine,select
 from sqlalchemy.orm import Session
 from app.visa_snapshot import kimi_primary as kp
 from app.visa_snapshot.models import KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog
 spec,layers=inputs;overlay,report=run(spec,layers)
 metadata=json.loads((Path(__file__).parent/'fixtures/major_malaysia_metadata.json').read_text())['rows']
 core=tmp_path/'base.json';over=tmp_path/'over.json';op=tmp_path/'op.json'
 core.write_text(json.dumps([e for l in layers for e in l['seed_entries']]));over.write_text(json.dumps(overlay));op.write_text('[]')
 monkeypatch.setattr(vo,'OVERRIDES',core);monkeypatch.setattr(vo,'_reviewed_overlay_paths',lambda:[over]);monkeypatch.setattr(vo,'operator_overrides_path',lambda:op);vo.reload()
 monkeypatch.setattr(kp,'_call',lambda *a,**k:pytest.fail('A sourced cached route must not regenerate'))
 engine=create_engine('sqlite:///:memory:')
 for model in (KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog):model.__table__.create(engine)
 with Session(engine) as db:
  for l in layers:
   meta=next(x for x in metadata if x['cache_key']==l['cache_key']);kwargs={k:deepcopy(meta[k]) for k in ('id','cache_key','route','status','missing_fields','contradictions','model','verification')}
   kwargs.update({k:datetime.fromisoformat(meta[k]) for k in ('generated_at','fresh_until','created_at','updated_at')});kwargs['guidance']=deepcopy(l['raw_guidance'])
   db.add(KimiRouteGuidanceCache(**kwargs));db.add(DatabaseChangeLog(cache_key=l['cache_key'],action='add',origin='fixture',changes={'unchanged':True}))
   if active_issue:db.add(DatabaseIssueReport(cache_key=l['cache_key'],route=l['route'],field='passport_validity',status='open',reported_by='freshness_monitor',proposal={'fields':{'passport_validity':{'record_holds':'old','page_says':'changed'}}}))
  db.commit()
  def snapshot():return {m.__name__:[{col.name:deepcopy(getattr(r,col.name)) for col in m.__table__.columns} for r in db.scalars(select(m))] for m in (KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog)}
  before=snapshot()
  for l in layers:
   out=kp.get_route_guidance(db,l['route']);assert out['cached'] and not out['stale']
   assert out['guidance']['permitted_stay_days']==90 and out['guidance']['photo_requirements'] is None
   assert out['guidance']['arrival_card']['submission_window'].startswith('Submit before arrival;')
   guarded=records_guard.apply_records_hold(l['route'],out,db)
   assert bool(guarded.get('held')) is active_issue
   if active_issue:assert records_guard.held_envelope(guarded)['guidance'] is None
  assert snapshot()==before
 vo.reload()

@pytest.mark.parametrize('mutation',['metadata','field','proof','omission'])
def test_complete_prepared_artifact_must_match_rebuild(inputs,mutation):
 spec,layers=inputs;m=c.build_manifest(spec,layers);o,_=c.convert(m,layers)
 if mutation=='metadata':o['actor']='human'
 elif mutation=='field':o['entries'][0]['fields']['unpublished_fields']=['passport_validity_requirement']
 elif mutation=='proof':o['entries'][0]['field_provenance']['photo_requirements']['status']='not_published'
 else:o['entries'].pop()
 with pytest.raises(PatchRejected):c.verify_prepared(spec,layers,m,o)
