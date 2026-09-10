from copy import deepcopy
from pathlib import Path
from datetime import datetime,timezone
import json
import pytest
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from scripts import convert_reviewed_russia_japan_processing as c
from scripts.prepare_reviewed_product_patch import PatchRejected,digest
from app.visa_snapshot import verified_overrides as vo,tstation,kimi_primary as kp,records_guard
from app.visa_snapshot.models import KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog
DATA=Path(__file__).resolve().parents[2]/'data/database_seed'
@pytest.fixture
def data():
 m=json.loads((DATA/c.MANIFEST).read_text());o=json.loads((DATA/c.OVERLAY).read_text());l=[dict(deepcopy(r['baseline']),cache_key=r['cache_key']) for r in m['routes']]
 return m,l,o

def test_complete_overlay_rebuild_changes_only_processing_and_own_proof(data):
 before=deepcopy(data);m,l,o=data;actual,preview=c.convert(m,l);assert actual==o and data==before
 x=preview[0];old=l[0];assert x['changed_fields']==['processing_time']
 for f,value in old['merged_guidance'].items():
  if f!='processing_time':assert x['guidance'][f]==value
 for f,proof in old['source_provenance']['field_provenance'].items():
  if f!='processing_time':assert x['source_verified']['field_provenance'][f]==proof
 for f,value in old['source_provenance'].items():
  if f!='field_provenance':assert x['source_verified'][f]==value
 assert x['guidance']['visa_products']==old['merged_guidance']['visa_products']
 assert all(not row['_evidence_low'] for row in x['records'])
 oldrows=tstation.records_for_route(old['route'],old['merged_guidance'],old['source_provenance'])
 for before_row,after_row in zip(oldrows,x['records']):
  for f in ('_evidence_low','confidence','source_url','collected_at','info_valid_until','_source_verified','_quality_grade'):
   assert before_row.get(f)==after_row.get(f),(f,before_row.get(f),after_row.get(f))
 assert not any(x[k] for k in ('raw_writes','operator_writes','issue_changes','renew_fresh_until'))

@pytest.mark.parametrize('layer',c.BASELINE_KEYS)
def test_every_baseline_cas_precedes_output(data,layer,monkeypatch):
 m,l,o=data;v=l[0][layer]
 if isinstance(v,dict):v['new_fact']=True
 else:v.append({'new_fact':True})
 monkeypatch.setattr(vo,'_parse_rows',lambda *a,**k:pytest.fail('Output before CAS'))
 with pytest.raises(PatchRejected):c.convert(m,l)

@pytest.mark.parametrize('kind',['document','purpose','residence','destination','arrival','alias','duplicate','missing','omit_layer','raw_repin','source_host','source_text','source_method','source_date','source_published','source_capture_http200','new_quote','wrong_days','guaranteed','wrong_clock','all_russia','new_field','human_proof','changed_old'])
def test_exact_scope_source_and_fact_contract_rejects(data,kind):
 m,l,o=data;s=m['specification'];r=s['routes'][0]
 if kind in ('document','purpose','residence','destination','arrival'):
  r['route'][{'document':'travel_document_type','purpose':'travel_purpose','residence':'lawful_country_of_residence','destination':'destination_country','arrival':'arrival_date'}[kind]]={'document':'diplomatic_passport','purpose':'work','residence':'USA','destination':'KOR','arrival':'2026-08-01'}[kind]
 elif kind=='alias':l[0]['cache_key']=l[0]['cache_key'].replace('RUS','RU',1)
 elif kind=='duplicate':l.append(deepcopy(l[0]))
 elif kind=='missing':l.clear()
 elif kind=='omit_layer':l[0].pop('operator_entries')
 elif kind=='raw_repin':l[0]['raw_guidance']['processing_time']='1 day';r['baseline_sha256']['raw_guidance']=digest(l[0]['raw_guidance'])
 elif kind.startswith('source_'):
  source=s['sources'][0]
  if kind=='source_host':source['url']='https://ru.emb-japan.go.jp.attacker.example/'
  elif kind=='source_text':source['text']+=' guaranteed';source['sha256']=digest(source['text'])
  elif kind=='source_method':source['capture_method']='human verified'
  elif kind=='source_date':source['checked_at']='2099-01-01'
  elif kind=='source_published':source['published_at']='2026-09-10'
  else:source['http_status']=200
 elif kind=='new_quote':r['change']['evidence'][0]['quote']='Guaranteed 4 days'
 elif kind=='new_field':r['change']['field']='permitted_stay'
 elif kind=='human_proof':r['change']['verifier']='human'
 elif kind=='changed_old':r['change']['old_raw']='irrelevant'
 else:r['change']['new_value']={'wrong_days':'3 working days','guaranteed':'Guaranteed within 4 working days','wrong_clock':'4 working days from acceptance','all_russia':'All applications in Russia take 4–5 working days'}[kind]
 with pytest.raises((PatchRejected,KeyError)):c.convert(m,l)

@pytest.mark.parametrize('kind',['manifest_bool','manifest_extra','overlay_extra','overlay_delete','overlay_value','overlay_proof','product_fee','product_owner'])
def test_entire_prepared_payload_equality(data,kind):
 m,l,o=data
 if kind=='manifest_bool':m['schema_version']=True
 elif kind=='manifest_extra':m['clear_pending']=True
 elif kind=='overlay_extra':o['publish_without_hold']=True
 elif kind=='overlay_delete':o['entries'].clear()
 elif kind=='overlay_value':o['entries'][0]['fields']['processing_time']='1 day'
 elif kind=='overlay_proof':o['entries'][0]['field_provenance']['processing_time']['verifier']='human'
 elif kind=='product_fee':o['entries'][0]['fields']['visa_products'][0]['fee']['amount']=100
 else:o['entries'][0]['fields']['visa_products'][0]['field_provenance']['fee']['verified_at']='2026-09-10'
 with pytest.raises(PatchRejected):c.verify_prepared(m['specification'],l,m,o)

@pytest.fixture
def stores(data,tmp_path,monkeypatch):
 m,l,o=data;core=tmp_path/'core.json';operator=tmp_path/'operators.json';overlay=tmp_path/c.OVERLAY
 core.write_text(json.dumps(l[0]['seed_entries']));operator.write_text('[]');overlay.write_text(json.dumps(o))
 monkeypatch.setattr(vo,'OVERRIDES',core);monkeypatch.setattr(vo,'operator_overrides_path',lambda:operator);monkeypatch.setattr(vo,'_reviewed_overlay_paths',lambda:[overlay]);vo.reload()
 yield
 vo.reload()

def test_actual_registered_reader_preserves_every_unrelated_byte_in_proof(data,stores):
 m,l,o=data;old=l[0];g,p=vo.apply(old['raw_guidance'],old['route']);_,preview=c.convert(m,l)
 assert g==preview[0]['guidance'] and p==preview[0]['source_verified']
 assert 'Moscow: normally 4–5 working days' in g['processing_time'] and 'minimum' in g['processing_time']
 assert '21–23 September' in g['processing_time'] and '10 days including weekends' in g['processing_time']
 assert 'from acceptance' not in g['processing_time'] and 'guaranteed' not in g['processing_time']
 # Historical notes end in a space at exactly 400 chars. Reuse their original
 # descriptor so loading this narrow overlay does not trim unrelated proofs.
 fields=['consular_jurisdiction','permitted_stay_days','official_portal_url','requirement_detail']
 assert all(old['source_provenance']['field_provenance'][f]['note'].endswith(' ') for f in fields)
 assert all(p['field_provenance'][f]==old['source_provenance']['field_provenance'][f] for f in fields)
 assert p['field_provenance']['processing_time']['verified_at']=='2026-09-10'
 assert p['verified_at']==old['source_provenance']['verified_at']

@pytest.mark.parametrize('issue',[False,True])
@pytest.mark.parametrize('pending',[False,True])
def test_sqlite_canonical_read_keeps_issues_pending_dates_and_history(data,stores,monkeypatch,issue,pending):
 engine=create_engine('sqlite:///:memory:')
 for model in (KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog):model.__table__.create(engine)
 monkeypatch.setattr(kp,'_call',lambda *a,**k:pytest.fail('No model call'))
 with Session(engine) as db:
  l=data[1][0]
  db.add(KimiRouteGuidanceCache(cache_key=l['cache_key'],route=deepcopy(l['route']),guidance=deepcopy(l['raw_guidance']),verification={'detail_pending':pending},fresh_until=datetime(2099,1,1,tzinfo=timezone.utc),status='KIMI_PRIMARY'))
  db.add(DatabaseChangeLog(cache_key=l['cache_key'],action='add',origin='fixture',changes={'retained':True}))
  if issue:db.add(DatabaseIssueReport(cache_key=l['cache_key'],route=deepcopy(l['route']),field='processing_time',status='open',reported_by='freshness_monitor',proposal={'fields':{'processing_time':{'record_holds':'4 working days'}}}))
  db.commit()
  def snap():return {m.__name__:[{col.name:deepcopy(getattr(r,col.name)) for col in m.__table__.columns} for r in db.scalars(select(m))] for m in (KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog)}
  before=snap();out=kp.get_route_guidance(db,l['route']);assert out['cached']
  held=records_guard.apply_records_hold(l['route'],out,db)
  if issue or pending:assert held.get('held')
  else:assert not held.get('held')
  if pending:assert out['detail_pending']
  assert out['guidance']['processing_time']==c.CASE['new_value']
  assert snap()==before
