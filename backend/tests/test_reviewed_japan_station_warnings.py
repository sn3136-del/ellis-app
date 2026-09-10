from copy import deepcopy
from datetime import date
from pathlib import Path
import json,hashlib
import pytest
from app.visa_snapshot import verified_overrides as vo,reviewed_japan_station_warnings as runtime,records_guard,tstation
from scripts import prepare_reviewed_japan_station_warnings as c
from scripts.prepare_reviewed_product_patch import PatchRejected,digest
DATA=Path(__file__).resolve().parents[2]/'data/database_seed'
@pytest.fixture
def inputs():
 m=json.loads((DATA/c.MANIFEST).read_text());return m,[dict(deepcopy(x['baseline']),cache_key=x['cache_key']) for x in m['routes']]
@pytest.fixture
def installed(inputs,monkeypatch,tmp_path):
 monkeypatch.setattr(vo,'OVERRIDES',DATA/'verified_overrides.json');monkeypatch.setenv('ELLIS_OPERATOR_OVERRIDES',str(tmp_path/'empty.json'));vo.reload();runtime._reviewed.cache_clear()
 yield inputs
 vo.reload();runtime._reviewed.cache_clear()
def expected(layer):
 g=deepcopy(layer['merged_guidance'])
 for item in c.RESOLUTIONS.get(layer['cache_key'],[]):g['uncertainty'].remove(item['warning'])
 return g

def test_exact19_route_reader_values_proofs_unchanged_only8_old_warnings_removed(installed,monkeypatch):
 # This assertion measures the warning-only release before the separate
 # Moscow processing-time correction, which has its own current proof.
 # The later field-correction release (empty Japan health lists) is filtered
 # the same way; it has its own exact-layer tests.
 listed=vo._listed_reviewed_overlay_names()
 monkeypatch.setattr(vo,'_listed_reviewed_overlay_names',lambda:[
  name for name in listed if name not in ('reviewed_russia_japan_processing_overlay_20260910.json',
                                          'reviewed_field_corrections_overlay_20260910.json')])
 vo.reload()
 m,layers=installed;old=deepcopy(layers);removed=0
 for layer in layers:
  g,p=vo.apply(deepcopy(layer['raw_guidance']),layer['route'])
  assert digest(p)==digest(layer['source_provenance']),layer['cache_key']
  assert digest(g)==digest(expected(layer)),(layer['cache_key'],{k:(g.get(k),expected(layer).get(k)) for k in set(g)|set(expected(layer)) if g.get(k)!=expected(layer).get(k)})
  removed+=len(layer['merged_guidance'].get('uncertainty',[]))-len(g.get('uncertainty',[]))
  assert g.get('visa_products')==layer['merged_guidance'].get('visa_products')
  assert tstation.records_for_route(layer['route'],g,p)==tstation.records_for_route(layer['route'],layer['merged_guidance'],layer['source_provenance'])
 assert removed==8 and layers==old

def test_later_moscow_processing_proof_does_not_relabel_japan_sibling_fields(installed,monkeypatch):
 # Measured before the later field-correction release, which empties the
 # unsupported RUS to JPN health list under its own exact-layer contract.
 listed=vo._listed_reviewed_overlay_names()
 monkeypatch.setattr(vo,'_listed_reviewed_overlay_names',lambda:[
  name for name in listed if name!='reviewed_field_corrections_overlay_20260910.json'])
 vo.reload()
 _,layers=installed
 layer=next(x for x in layers if x['cache_key']=='RUS|RUS|JPN|tourism|default|unknown|v6')
 g,p=vo.apply(deepcopy(layer['raw_guidance']),layer['route'])
 proof=p['field_provenance']['processing_time']
 assert proof['source_url']=='https://www.ru.emb-japan.go.jp/itpr_ja/vc20260904.html'
 assert proof['verified_at']=='2026-09-10'
 assert 'Moscow' in g['processing_time'] and '4–5' in g['processing_time']
 assert g['visa_products']==layer['merged_guidance']['visa_products']
 prior=layer['source_provenance']['field_provenance']
 assert {k:v for k,v in p['field_provenance'].items() if k!='processing_time'}=={
  k:v for k,v in prior.items() if k!='processing_time'}

@pytest.mark.parametrize('index',range(19))
@pytest.mark.parametrize('field',c.BASELINE_KEYS)
def test_each_current_layer_CAS(inputs,index,field):
 m,layers=inputs;v=layers[index][field]
 if isinstance(v,dict):v['changed']=True
 else:v.append({'changed':True})
 with pytest.raises(PatchRejected):c.validate_prepared(m,layers)

@pytest.mark.parametrize('mutation',['source_text','rehash','wrong_url','wrong_date','wrong_fiscal_year','missing_source','missing_route','duplicate_route','extra_route','warning','extra_instruction','bool_schema'])
def test_manifest_source_closed_scope_integrity(inputs,mutation):
 m,_=inputs
 if mutation=='source_text':m['sources'][0]['text']+='unreviewed'
 elif mutation=='rehash':m['sources'][0]['text']+='unreviewed';m['sources'][0]['sha256']=hashlib.sha256(m['sources'][0]['text'].encode()).hexdigest()
 elif mutation=='wrong_url':m['sources'][0]['url']='https://evil.example/'
 elif mutation=='wrong_date':m['sources'][2]['published_at']='2026-01-01'
 elif mutation=='wrong_fiscal_year':m['sources'][2]['text']=m['sources'][2]['text'].replace('２０２８','２０２６')
 elif mutation=='missing_source':m['sources'].pop()
 elif mutation=='missing_route':m['routes'].pop()
 elif mutation=='duplicate_route':m['routes'][1]=deepcopy(m['routes'][0])
 elif mutation=='extra_route':m['routes'].append(deepcopy(m['routes'][0]))
 elif mutation=='warning':next(r for r in m['routes'] if r['resolutions'])['resolutions'][0]['warning']['reason']='arbitrary disputed fact'
 elif mutation=='extra_instruction':m['clear_pending']=True
 else:m['schema_version']=True
 with pytest.raises((PatchRejected,ValueError,TypeError,KeyError)):c.validate_prepared(m)

@pytest.mark.parametrize('mutation',['new_warning','changed_warning','duplicate_warning','wrong_fact','wrong_proof','wrong_route','future_arrival','nonempty_transit','empty_transit','invalid_proof_type'])
def test_runtime_keeps_new_scope_or_changed_content(installed,mutation):
 _,layers=installed;layer=layers[0];g=deepcopy(layer['merged_guidance']);p=deepcopy(layer['source_provenance']);route=deepcopy(layer['route'])
 if mutation=='new_warning':g['uncertainty'].append(dict(field='disposition',reason='New current source dispute'))
 elif mutation=='changed_warning':g['uncertainty'][0]['reason']+=' New corroborating evidence.'
 elif mutation=='duplicate_warning':g['uncertainty'].append(deepcopy(g['uncertainty'][0]))
 elif mutation=='wrong_fact':g['permitted_stay']='Changed condition'
 elif mutation=='wrong_proof':p['verified_at']='2026-09-11'
 elif mutation=='wrong_route':route['lawful_country_of_residence']='USA'
 elif mutation=='future_arrival':route['arrival_date']='2028-04-01'
 elif mutation=='nonempty_transit':route['transit_countries']=['USA']
 elif mutation=='empty_transit':route['transit_countries']=[]
 else:p=[]
 before=deepcopy(g);result=runtime.reconcile(route,g,p);assert g==before
 if mutation=='new_warning':assert result['uncertainty'][-1]==g['uncertainty'][-1]
 elif mutation in ('changed_warning','duplicate_warning'):assert g['uncertainty'][0] in result['uncertainty']
 else:assert result==g

@pytest.mark.parametrize('when',['2026-09-09','2027-01-01','2028-04-01'])
def test_review_window_not_future_launch_guarantee(installed,monkeypatch,when):
 class Today(date):
  @classmethod
  def today(cls):return date.fromisoformat(when)
 monkeypatch.setattr(runtime,'date',Today);_,layers=installed;l=layers[0]
 assert runtime.reconcile(l['route'],l['merged_guidance'],l['source_provenance'])==l['merged_guidance']

def test_missing_corrupt_manifest_cannot_resolve(installed,monkeypatch,tmp_path):
 _,layers=installed;l=layers[0];monkeypatch.setattr(vo,'OVERRIDES',tmp_path/'base.json')
 assert runtime.reconcile(l['route'],l['merged_guidance'],l['source_provenance'])==l['merged_guidance']
 (tmp_path/c.MANIFEST).write_text('{broken')
 assert runtime.reconcile(l['route'],l['merged_guidance'],l['source_provenance'])==l['merged_guidance']

def test_existing_disputes_still_hold(installed):
 _,layers=installed
 for l in layers:
  g,p=vo.apply(deepcopy(l['raw_guidance']),l['route'])
  for field in ('disposition','permitted_stay','arrival_card'):
   original=dict(guidance=g,source_verified=p,grounded_check={'disputed_fields':[field]},operator_released=True)
   held=records_guard.apply_records_hold(l['route'],original)
   assert held['held'] and records_guard.held_envelope(held)['guidance'] is None

def test_pending_extraction_and_history_are_not_reconciled(installed):
 _,layers=installed;l=layers[0];g,p=vo.apply(deepcopy(l['raw_guidance']),l['route'])
 verification={'detail_pending':True,'fresh_until':'2026-09-20T00:00:00Z','historical_issue':'retained'}
 before=deepcopy(verification)
 out=dict(guidance=g,source_verified=p,detail_pending=True,operator_released=True,verification_json=verification)
 held=records_guard.apply_records_hold(l['route'],out)
 assert held['held'] and held['detail_pending'] is True
 assert verification==before and held['verification_json']==before

@pytest.mark.parametrize('issue',[False,True])
@pytest.mark.parametrize('pending',[False,True])
def test_actual_cached_reader_does_not_change_raw_history_or_independent_holds(installed,monkeypatch,issue,pending):
 from datetime import datetime,timezone
 from sqlalchemy import create_engine,select
 from sqlalchemy.orm import Session
 from app.visa_snapshot import kimi_primary as kp
 from app.visa_snapshot.models import KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog
 engine=create_engine('sqlite:///:memory:')
 for model in (KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog):model.__table__.create(engine)
 monkeypatch.setattr(kp,'_call',lambda *a,**k:pytest.fail('No model regeneration'))
 _,layers=installed
 with Session(engine) as db:
  for l in layers:
   db.add(KimiRouteGuidanceCache(cache_key=l['cache_key'],route=deepcopy(l['route']),guidance=deepcopy(l['raw_guidance']),verification={'detail_pending':pending},fresh_until=datetime(2099,1,1,tzinfo=timezone.utc),status='KIMI_PRIMARY'))
   db.add(DatabaseChangeLog(cache_key=l['cache_key'],action='add',origin='fixture',changes={'unchanged':True}))
   if issue:db.add(DatabaseIssueReport(cache_key=l['cache_key'],route=deepcopy(l['route']),field='disposition',status='open',reported_by='freshness_monitor',proposal={'fields':{'disposition':{'record_holds':'VISA_EXEMPT'}}}))
  db.commit()
  def snapshot():return {m.__name__:[{col.name:deepcopy(getattr(r,col.name)) for col in m.__table__.columns} for r in db.scalars(select(m))] for m in (KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog)}
  before=snapshot()
  for l in layers:
   original_reconcile=runtime.reconcile
   with monkeypatch.context() as patch:
    patch.setattr(runtime,'reconcile',lambda route,g,p:g)
    old=kp.get_route_guidance(db,l['route']);old_guard=records_guard.apply_records_hold(l['route'],deepcopy(old),db)
   new=kp.get_route_guidance(db,l['route']);new_guard=records_guard.apply_records_hold(l['route'],deepcopy(new),db)
   assert new['cached'] and old['cached']
   assert new_guard.get('held')==old_guard.get('held')
   if issue or pending:assert new_guard['held']
   assert {k:v for k,v in old['guidance'].items() if k!='uncertainty'}=={k:v for k,v in new['guidance'].items() if k!='uncertainty'}
   assert new['guidance'].get('uncertainty')==expected(l).get('uncertainty'),l['cache_key']
   assert new.get('source_verified')==old.get('source_verified')
  assert snapshot()==before

@pytest.mark.parametrize('transit',['missing',[],['KOR'],False,{}])
def test_existing_hkg_review_accepts_only_known_null_transit_omission(installed,transit):
 from app.visa_snapshot import reviewed_japan_warning_resolution as prior
 from scripts.convert_reviewed_japan_singapore_fields import MANIFEST,OVERLAY
 exp,resolved=prior._rebuilt((DATA/MANIFEST).read_text(),(DATA/OVERLAY).read_text())
 route=deepcopy(exp['route']);assert 'transit_countries' in route and route['transit_countries'] is None
 before=deepcopy(exp['guidance'])
 if transit=='missing':route.pop('transit_countries')
 else:route['transit_countries']=transit
 result=prior.reconcile(route,before,exp['source_verified'])
 if transit=='missing':
  assert len(before['uncertainty'])-len(result['uncertainty'])==len(resolved)==3
 else:assert result==before
