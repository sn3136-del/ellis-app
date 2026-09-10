from copy import deepcopy
from pathlib import Path
from datetime import date
import hashlib,json
from unittest.mock import patch
import pytest
from scripts import convert_reviewed_uk_application_channel as c
from scripts.prepare_reviewed_product_patch import PatchRejected,digest
from app.visa_snapshot import verified_overrides as vo,tstation,records_guard
DATA=Path(__file__).resolve().parents[2]/'data/database_seed'
@pytest.fixture
def inputs():
 m=json.loads((DATA/c.MANIFEST).read_text());return m['specification'],[dict(deepcopy(r['baseline']),cache_key=r['cache_key']) for r in m['routes']]
def run(inputs):return c.convert(c.build_manifest(*inputs),inputs[1])

def test_exact17_six_defaults_and_sixty_products_only_initial_filing_stage_changes(inputs):
 before=deepcopy(inputs);overlay,report=run(inputs)
 assert inputs==before and len(overlay['entries'])==15
 assert report['contexts_checked']==17 and report['route_channels_changed']==6 and report['product_channels_changed']==60 and report['eta_defaults_unchanged']==11
 assert not any(report[x] for x in ('raw_writes','operator_writes','issue_changes','pending_changes','renew_fresh_until','confidence_changed','new_release'))
 total=0
 for layer,result in zip(inputs[1],report['routes'],strict=True):
  key=layer['cache_key'];assert key==result['cache_key'];contract=c.CONTRACT[key]
  old,new=layer['merged_guidance'],result['guidance'];op,np=layer['source_provenance'],result['source_provenance']
  changed={'application_channel'} if contract['route_channel'] else set()
  for f in set(old)|set(new):
   if f not in changed|{'visa_products'}:assert old.get(f)==new.get(f),(key,f)
  for f in set(op)|set(np):
   if f!='field_provenance':assert op.get(f)==np.get(f),(key,f)
  for f in set(op['field_provenance'])|set(np['field_provenance']):
   if f not in changed:assert op['field_provenance'].get(f)==np['field_provenance'].get(f)
  if contract['route_channel']:
   assert new['application_channel']=='online_portal' and old['application_channel']=='visa_center'
   assert new['appointment_required'] is new['biometrics_required'] is True
   assert new['route_workflow_type']=='visa_center_submission'
  else:assert new['disposition']=='ELECTRONIC_AUTHORIZATION_REQUIRED' and new['application_channel']=='online_portal'
  names={x['type'] for x in contract['products']}
  for p,q in zip(old['visa_products'],new['visa_products'],strict=True):
   if p['type'] not in names:assert p==q;continue
   total+=1;assert q['application_channel']=='online_portal' and p['application_channel']=='visa_center'
   for f in set(p)|set(q):
    if f not in {'application_channel','field_provenance'}:assert p.get(f)==q.get(f),(key,p['type'],f)
   pf,qf=p.get('field_provenance') or {},q['field_provenance']
   for f in set(pf)|set(qf):
    if f!='application_channel':assert pf.get(f)==qf.get(f)
   proof=qf['application_channel'];assert proof['status']=='reviewed' and proof['verifier']=='ai'
   assert proof['subject']['product_type']==q['type'] and proof['subject']['disposition']==p.get('disposition')
   assert proof['quote']==c.QUOTES['overview'] and proof['verified_at']=='2026-09-10'
   assert 'subsequent booked visa application centre appointment' in proof['note'] and not proof.get('effective_to')
  oldrows=tstation.records_for_route(layer['route'],old,op)
  assert len(oldrows)==len(result['records'])
  for a,b in zip(oldrows,result['records'],strict=True):
   for f in ('visa_type_name','visa_requirement','visa_requirement_detail','visa_fee_amount','visa_fee_currency','validity_duration','validity_unit','entries','max_stay_duration','max_stay_unit','confidence_level','info_validity','collected_at'):
    assert a.get(f)==b.get(f),(key,f)
 assert total==60
 for key in ('HKG','USA'):
  l=next(l for l in inputs[1] if l['cache_key'].startswith(key));r=next(r for r in report['routes'] if r['cache_key']==l['cache_key'])
  assert l['merged_guidance']==r['guidance'] and l['source_provenance']==r['source_provenance']

@pytest.mark.parametrize('index',range(17))
@pytest.mark.parametrize('field',c.BASELINE_KEYS)
def test_each_route_and_six_layer_CAS(inputs,index,field):
 spec,layers=inputs;manifest=c.build_manifest(spec,layers);v=layers[index][field]
 if isinstance(v,dict):v['changed']=True
 else:v.append({'changed':True})
 with pytest.raises(PatchRejected):c.convert(manifest,layers)

@pytest.mark.parametrize('kind',['source_text','source_rehash','url','body_hash','future_date','missing_source','duplicate_source','missing_route','duplicate_route','extra_route','wrong_passport','wrong_residence','arrival_date','route_eta_scope','product_eta_scope','product_removed','product_fee','proof_injection','bool_schema'])
def test_closed_source_scope_and_value_contract(inputs,kind):
 spec,layers=inputs;s=spec['sources'][0];r=spec['routes'][0]
 if kind=='source_text':s['text']+=' another rule'
 elif kind=='source_rehash':s['text']+=' another rule';s['sha256']=hashlib.sha256(s['text'].encode()).hexdigest()
 elif kind=='url':s['url']='https://other.example/'
 elif kind=='body_hash':s['capture_body_sha256']='0'*64
 elif kind=='future_date':s['checked_at']='2026-09-11'
 elif kind=='missing_source':spec['sources'].pop()
 elif kind=='duplicate_source':spec['sources'].append(deepcopy(s))
 elif kind=='missing_route':spec['routes'].pop()
 elif kind=='duplicate_route':spec['routes'][1]=deepcopy(r)
 elif kind=='extra_route':spec['routes'].append(deepcopy(r))
 elif kind=='wrong_passport':r['route']['travel_document_type']='diplomatic_passport'
 elif kind=='wrong_residence':r['route']['lawful_country_of_residence']='USA'
 elif kind=='arrival_date':r['route']['arrival_date']='2027-01-01'
 elif kind=='route_eta_scope':r['route_channel']={'old':'online_portal','new':'visa_center'}
 elif kind=='product_eta_scope':r['product_changes'][0]['index']=0
 elif kind=='product_removed':r['product_changes'].pop()
 elif kind=='product_fee':r['product_changes'][0]['field']='government_fee'
 elif kind=='proof_injection':r['proof']={'status':'reviewed','verified_by':'human'}
 else:spec['schema_version']=True
 with pytest.raises((PatchRejected,ValueError,TypeError,KeyError)):c.build_manifest(spec,layers)

@pytest.mark.parametrize('layer',c.BASELINE_KEYS)
def test_missing_layer_never_accepts_partial_capture(inputs,layer):
 spec,layers=inputs;layers[-1].pop(layer)
 with pytest.raises((PatchRejected,KeyError)):c.build_manifest(spec,layers)

@pytest.mark.parametrize('kind',['metadata','field','proof','product_proof_subject','drop_product','drop_entry','manifest'])
def test_entire_prepared_output_must_rebuild(inputs,kind):
 spec,layers=inputs;m=c.build_manifest(spec,layers);o,_=c.convert(m,layers)
 if kind=='metadata':o['actor']='human'
 elif kind=='field':next(e for e in o['entries'] if e['route']['nationality']=='IDN')['fields']['appointment_required']=False
 elif kind=='proof':o['entries'][0]['field_provenance']['application_channel']['status']='not_published'
 elif kind=='product_proof_subject':o['entries'][0]['fields']['visa_products'][1]['field_provenance']['application_channel']['subject']['product_type']='UK ETA'
 elif kind=='drop_product':o['entries'][0]['fields']['visa_products'].pop()
 elif kind=='drop_entry':o['entries'].pop()
 else:m['kind']='other'
 with pytest.raises((PatchRejected,KeyError)):c.verify_prepared(spec,layers,m,o)

def test_captured_primary_filing_instruction_is_unambiguous_and_VAC_stage_stays_evidenced(inputs):
 from app.visa_snapshot.evidence_validator import field_value_supported
 s={x['id']:x for x in inputs[0]['sources']}
 assert field_value_supported('application_channel','online_portal',c.QUOTES['overview'])
 for sid,q in c.QUOTES.items():assert q in s[sid]['text']
 assert not field_value_supported('application_channel','online_portal','Book an appointment at a visa application centre.')
 assert not field_value_supported('application_channel','online_portal','You can pay your application fee online.')

def test_review_date_is_not_future(inputs,monkeypatch):
 class Yesterday(date):
  @classmethod
  def today(cls):return date(2026,9,9)
 monkeypatch.setattr(c,'date',Yesterday)
 with pytest.raises(PatchRejected):c.build_manifest(*inputs)

@pytest.mark.parametrize('issue',[False,True])
@pytest.mark.parametrize('pending',[False,True])
def test_actual17_cached_readers_preserve_VAC_workflow_ETA_and_active_guards(inputs,monkeypatch,tmp_path,issue,pending):
 from datetime import datetime,timezone
 from sqlalchemy import create_engine,select
 from sqlalchemy.orm import Session
 from app.visa_snapshot import kimi_primary as kp
 from app.visa_snapshot.models import KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog
 overlay,report=run(inputs);layers=inputs[1]
 before_table=vo._parse_rows([e for l in layers for e in l['seed_entries']],{})
 after_table=vo._parse_rows(overlay['entries'],deepcopy(before_table))
 engine=create_engine('sqlite:///:memory:')
 for model in (KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog):model.__table__.create(engine)
 monkeypatch.setattr(kp,'_call',lambda *a,**k:pytest.fail('No model regeneration'))
 monkeypatch.setenv('ELLIS_OPERATOR_OVERRIDES',str(tmp_path/'none.json'))
 monkeypatch.setattr(vo,'_table',lambda:after_table)
 with Session(engine) as db:
  for l in layers:
   db.add(KimiRouteGuidanceCache(cache_key=l['cache_key'],route=deepcopy(l['route']),guidance=deepcopy(l['raw_guidance']),verification={'detail_pending':pending},fresh_until=datetime(2099,1,1,tzinfo=timezone.utc),status='KIMI_PRIMARY'))
   db.add(DatabaseChangeLog(cache_key=l['cache_key'],action='add',origin='fixture',changes={'unchanged':True}))
   if issue:db.add(DatabaseIssueReport(cache_key=l['cache_key'],route=deepcopy(l['route']),field='application_channel',status='open',reported_by='freshness_monitor',proposal={'fields':{'application_channel':{'record_holds':'visa_center','page_says':'online_portal'}}}))
  db.commit()
  def snapshot():return {m.__name__:[{col.name:deepcopy(getattr(r,col.name)) for col in m.__table__.columns} for r in db.scalars(select(m))] for m in (KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog)}
  initial=snapshot()
  for l in layers:
   with patch.object(vo,'_table',return_value=before_table):before=kp.get_route_guidance(db,l['route']);old_held=records_guard.apply_records_hold(l['route'],deepcopy(before),db)
   after=kp.get_route_guidance(db,l['route']);held=records_guard.apply_records_hold(l['route'],deepcopy(after),db)
   assert after['cached'] and before['cached'];assert held.get('held')==old_held.get('held')
   if issue or pending:assert held['held']
   key=l['cache_key'];contract=c.CONTRACT[key]
   if contract['route_channel']:
    assert after['guidance']['application_channel']=='online_portal'
    assert after['guidance']['route_workflow_type']=='visa_center_submission'
    assert after['guidance']['appointment_required'] is after['guidance']['biometrics_required'] is True
    assert 'Apply online, then attend the booked visa application centre appointment' in after['guidance']['application_channel_detail']
   assert after['guidance'].get('route_workflow_type')==before['guidance'].get('route_workflow_type')
   assert after['guidance'].get('application_channel_detail')==before['guidance'].get('application_channel_detail')
   assert after['workflow_plan']==before['workflow_plan']
   direct_plan=kp.derive_workflow_plan(after['guidance'])
   assert direct_plan==kp.derive_workflow_plan(before['guidance'])
   if contract['route_channel']:
    assert {'appointment_search','appointment_booking'} <= {step['step'] for step in direct_plan}
    assert all(step['workflow_type']=='visa_center_submission' for step in direct_plan)
    assert all(step['requires_applicant_confirmation'] for step in direct_plan if step['step'] in {'appointment_booking','payment','submission'})
   if not contract['route_channel']:assert after['guidance']['application_channel']==before['guidance']['application_channel']=='online_portal'
  assert snapshot()==initial
