"""Exact current UK source order, own evidence and the actual cached reader."""
from copy import deepcopy
from datetime import datetime,timezone
import json
from pathlib import Path
import pytest
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from scripts import convert_reviewed_uk_ordered_workflow as c
from scripts.prepare_reviewed_product_patch import PatchRejected
from app.visa_snapshot import verified_overrides as vo,kimi_primary as kp,tstation,ordered_application_instructions as ordered
from app.visa_snapshot.models import KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog
SEED=Path(__file__).resolve().parents[2]/'data/database_seed'
@pytest.fixture(scope='module')
def frozen():
 m=json.loads((SEED/c.MANIFEST).read_text());l=[dict(cache_key=r['cache_key'],**r['baseline']) for r in m['routes']];o=json.loads((SEED/c.OVERLAY).read_text());return m,l,o
@pytest.fixture
def data(frozen):return deepcopy(frozen)
@pytest.fixture(scope='module')
def previews(frozen):return c.convert(frozen[0],frozen[1])[1]['routes']

def test_complete_rebuild_preserves_unrelated_facts_and_proof(data):
 m,l,o=data;before=deepcopy(data);report=c.verify_prepared(m['specification'],l,m,o)
 assert data==before and len(report['routes'])==17
 assert report['default_workflows_reviewed']==6 and report['product_workflows_reviewed']==60
 for old,new in zip(sorted(l,key=lambda x:x['cache_key']),report['routes'],strict=True):
  contract=c.CONTRACT[old['cache_key']];names={p['type'] for p in contract['products']}
  assert c._without_changes(new['guidance'],contract['default'],names)==c._without_changes(old['merged_guidance'],contract['default'],names)
  assert new['source_provenance']['field_provenance']['disposition']==old['source_provenance']['field_provenance']['disposition']
  assert tstation.records_for_route(old['route'],old['merged_guidance'],old['source_provenance'])==new['records']

@pytest.mark.parametrize('index',range(17))
@pytest.mark.parametrize('field',c.BASELINE_KEYS)
def test_all_102_layer_boundaries_rejected_before_output(data,index,field,monkeypatch):
 m,l,_=data;v=l[index][field]
 if isinstance(v,dict):v['unreviewed']='changed'
 else:v.append({'unreviewed':'changed'})
 before=deepcopy(l);monkeypatch.setattr(vo,'merge_verified_fields',lambda *a,**kw:pytest.fail('Output before all CAS'))
 with pytest.raises(PatchRejected):c.convert(m,l)
 assert l==before

@pytest.mark.parametrize('kind',['missing','duplicate','alias','missing_layer','source','quote','capture','extra_step','fee','overlay_metadata','overlay_proof'])
def test_control_tamper(data,kind):
 m,l,o=data
 if kind=='missing':l.pop()
 elif kind=='duplicate':l.append(deepcopy(l[0]))
 elif kind=='alias':l[0]['cache_key']=l[0]['cache_key'].replace('|default|','|Moscow|')
 elif kind=='missing_layer':l[0].pop('operator_entries')
 elif kind in {'source','quote','capture','extra_step','fee'}:m['specification'][kind]='unreviewed'
 elif kind=='overlay_metadata':o['entries'][0]['verified_at']='2099-01-01'
 else:o['entries'][0]['field_provenance']['disposition']['verifier']='human'
 with pytest.raises(PatchRejected):c.verify_prepared(m['specification'],l,m,o)

def test_six_defaults_and_sixty_products_have_own_order(previews):
 defaults=products=0
 for v in previews:
  r,g,p=v['route'],v['guidance'],v['source_provenance'];steps=ordered.ordered_instructions(g,r,p)
  if c.CONTRACT[v['cache_key']]['default']:
   defaults+=1;assert [s['id'] for s in steps]==['apply_online','book_appointment','attend_appointment','receive_decision']
   assert kp.canonical_steps(g,route=r,source_verified=p)==c.TEXT
   assert 'another country' in c.TEXT[1] and 'not in English or Welsh' in c.TEXT[2]
   plan=kp.derive_workflow_plan(g,route=r,source_verified=p)
   assert [s['step'] for s in plan]==['submission','appointment_booking','attend_appointment','track_status']
   assert [s['instruction'] for s in plan]==c.TEXT
   assert [s['after'] for s in plan]==[[],['apply_online'],['book_appointment'],['attend_appointment']]
  else:assert steps==[]
  for product in g.get('visa_products') or []:
   value=ordered.ordered_instructions(g,r,p,product=product)
   if product['type'] in ordered.SUBJECTS[r['passport_nationality']]['products']:
    products+=1;assert [s['text'] for s in value]==c.TEXT
   else:assert value==[]
 assert defaults==6 and products==60

@pytest.mark.parametrize('kind',['no_route','country','residence','purpose','document','transit','category','proof','order','fee','no_proof','map','fields','sibling','date'])
def test_unbound_scope_or_raw_claim_never_ordered(previews,kind):
 v=deepcopy(next(v for v in previews if v['route']['passport_nationality']=='IDN'));r,g,p=v['route'],v['guidance'],v['source_provenance']
 edits={'country':('destination_country','JPN'),'residence':('lawful_country_of_residence','JPN'),'purpose':('travel_purpose','business'),'document':('travel_document_type','diplomatic_passport'),'transit':('transit_countries',['JPN']),'category':('visa_category','work'),'district':('consular_jurisdiction','San Francisco')}
 if kind=='no_route':r=None
 elif kind in edits:r[edits[kind][0]]=edits[kind][1]
 elif kind=='proof':p['field_provenance']['submission_process']['quote']='Pay an agent.'
 elif kind=='order':g['submission_process'].reverse()
 elif kind=='fee':g['submission_process'].append('Pay an agency.')
 elif kind=='no_proof':p['field_provenance'].pop('submission_process')
 elif kind=='map':p['field_provenance']='bad'
 elif kind=='fields':p['fields']='submission_process'
 else:p['field_provenance']['submission_process']=g['visa_products'][0]['field_provenance']['submission_process']
 before=deepcopy((g,r,p));assert kp.canonical_steps(g,route=r,source_verified=p)==[]
 assert kp.derive_workflow_plan(g,route=r,source_verified=p)==[] and (g,r,p)==before

@pytest.mark.parametrize('kind',['legacy_uk','free_japan_fee','random','malformed','exempt'])
def test_unreviewed_arrays_not_confirmed(kind):
 g={'disposition':'VISA_REQUIRED','application_channel':'visa_center','official_portal_url':'https://www.mofa.go.jp/','government_fee':{'amount':0},'account_registration_steps':['Create UKVI account'],'payment_process':['Pay the official visa fee and the agency service fee.'],'submission_process':['Attend the appointment','Submit online']}
 if kind=='random':g['submission_process']=['A','B']
 elif kind=='malformed':g['submission_process']={'step':'pay'}
 elif kind=='exempt':g.update(disposition='VISA_EXEMPT',application_channel='none')
 before=deepcopy(g);out=kp.application_instructions(g)
 assert out['apply_steps']==[] and out['application_steps_status']==('not_applicable' if kind=='exempt' else 'unknown') and g==before
 if kind!='exempt':assert kp.derive_workflow_plan(g)==[]

def test_reviewed_eta_app_conditional_followup_preserved():
 o=json.loads((SEED/'reviewed_australia_workflow_overlay_20260910.json').read_text())
 for e in o['entries']:
  r={'passport_nationality':e['route']['nationality'],'lawful_country_of_residence':e['route']['nationality'],'destination_country':'AUS','travel_purpose':'tourism','travel_document_type':'ordinary_passport'}
  g=e['fields'];p={'fields':list(g),'field_provenance':e['field_provenance']};steps=kp.canonical_steps(g,route=r,source_verified=p)
  assert len(steps)==7 and steps[0]=='Download and open the official Australian ETA app.'
  assert steps[-1].endswith('Do not lodge another ETA app application for the same request.') and 'provide the requested documents and Form 1554' in steps[-1]
  assert 'camera, NFC and location services' in steps[1] and 'physically present' in steps[2]
  plan=kp.derive_workflow_plan(g,route=r,source_verified=p)
  assert [s['instruction'] for s in plan]==steps
  g.update(appointment_required=True,government_fee={'amount':999,'currency':'USD'})
  assert kp.derive_workflow_plan(g,route=r,source_verified=p)==plan
  assert not any(s['step'] in {'generate_route_adapter','account_registration','appointment_booking','display_exact_fees'} for s in plan)
  p['field_provenance']['submission_process']['quote']='Unreviewed account';assert kp.canonical_steps(g,route=r,source_verified=p)==[]

@pytest.fixture
def stores(tmp_path,monkeypatch,data):
 _,layers,o=data;core=tmp_path/'verified.json';overlay=tmp_path/c.OVERLAY;op=tmp_path/'operator.json'
 core.write_text(json.dumps([e for l in layers for e in l['seed_entries']]))
 overlay.write_text(json.dumps(o));op.write_text('[]')
 monkeypatch.setattr(vo,'OVERRIDES',core);monkeypatch.setattr(vo,'operator_overrides_path',lambda:op);monkeypatch.setattr(vo,'_reviewed_overlay_paths',lambda:[overlay]);vo.reload()
 yield
 vo.reload()

@pytest.mark.parametrize('issue',[False,True])
def test_actual_reader_order_and_unchanged_database(data,previews,stores,monkeypatch,issue):
 _,layers,_=data;engine=create_engine('sqlite:///:memory:')
 for m in (KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog):m.__table__.create(engine)
 monkeypatch.setattr(kp,'_call',lambda *a,**kw:pytest.fail('No generation'))
 expected={v['cache_key']:v for v in previews}
 with Session(engine) as db:
  for l in layers:
   db.add(KimiRouteGuidanceCache(cache_key=l['cache_key'],route=deepcopy(l['route']),guidance=deepcopy(l['raw_guidance']),verification={'legacy':'unchanged'},fresh_until=datetime(2099,1,1,tzinfo=timezone.utc),status='KIMI_PRIMARY'))
   db.add(DatabaseChangeLog(cache_key=l['cache_key'],action='add',origin='fixture',changes={'history':'preserved'}))
   if issue:db.add(DatabaseIssueReport(cache_key=l['cache_key'],route=deepcopy(l['route']),field='government_fee',status='open',reported_by='freshness_monitor',proposal={'fields':{'government_fee':{'record_holds':'1 hour'}}}))
  db.commit()
  def snapshot():return {m.__name__:[{col.name:deepcopy(getattr(row,col.name)) for col in m.__table__.columns} for row in db.scalars(select(m))] for m in (KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog)}
  before=snapshot()
  for l in layers:
   from app.visa_snapshot.records_guard import apply_records_hold
   out=apply_records_hold(l['route'],kp.get_route_guidance(db,l['route']),db)
   if issue:
    if c.CONTRACT[l['cache_key']]['default']:
     assert out.get('held')
     from app.visa_snapshot.records_guard import held_envelope
     public=held_envelope(out)
     assert not public.get('apply_steps') and not public.get('workflow_plan') and public['guidance'] is None
   elif c.CONTRACT[l['cache_key']]['default']:
    assert not out.get('held'),out.get('hold_reason')
    assert out['application_steps_status']=='source_ordered' and out['apply_steps']==c.TEXT
    assert [s['step'] for s in out['workflow_plan']]==['submission','appointment_booking','attend_appointment','track_status']
    assert out['guidance']['visa_products']==expected[l['cache_key']]['guidance']['visa_products']
  assert snapshot()==before


@pytest.mark.parametrize('field,value',[('arrival_date','2026-10-01'),('consular_jurisdiction','default'),('visa_category','tourist_visa'),('transit_countries',[])])
def test_request_time_route_keys_keep_the_uk_order(previews,field,value):
 from app.visa_snapshot.ordered_application_instructions import ordered_instructions,route_in_scope,SUBJECTS
 for p in previews:
  if not SUBJECTS[p['route']['passport_nationality']]['default']:continue
  route=dict(p['route']);route[field]=value
  assert route_in_scope(route,SUBJECTS[route['passport_nationality']]['route'])
  assert ordered_instructions(p['guidance'],route,p['source_provenance'])
 assert not route_in_scope(dict(previews[0]['route'],consular_jurisdiction='San Francisco'),SUBJECTS[previews[0]['route']['passport_nationality']]['route'])
