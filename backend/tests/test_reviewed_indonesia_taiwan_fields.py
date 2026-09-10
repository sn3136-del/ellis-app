from copy import deepcopy
from pathlib import Path
from datetime import datetime,timezone
import json
import pytest
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from scripts import convert_reviewed_indonesia_taiwan_fields as c
from scripts.prepare_reviewed_product_patch import PatchRejected,digest
from app.visa_snapshot import verified_overrides as vo,tstation,kimi_primary as kp,records_guard
from app.visa_snapshot.models import KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog
DATA=Path(__file__).resolve().parents[2]/'data/database_seed'
@pytest.fixture
def data():
 m=json.loads((DATA/c.MANIFEST).read_text());o=json.loads((DATA/c.OVERLAY).read_text());l=[dict(deepcopy(r['baseline']),cache_key=r['cache_key']) for r in m['routes']]
 return m,l,o

def test_whole_rebuild_is_nonmutating_and_preserves_products_and_owners(data):
 before=deepcopy(data);m,l,o=data;actual,preview=c.convert(m,l)
 assert actual==o and data==before
 for x in preview:
  old=next(r for r in l if r['cache_key']==x['cache_key']);changed=set(c.APPROVED[x['cache_key']]['changes'])
  assert not x['raw_writes'] and not x['operator_writes'] and not x['issue_changes'] and not x['renew_fresh_until']
  assert len(x['guidance'].get('visa_products') or [])==len(old['merged_guidance'].get('visa_products') or [])
  for f,p in old['source_provenance']['field_provenance'].items():
   if f not in changed:assert x['source_verified']['field_provenance'][f]==p
  assert all(not row['_evidence_low'] for row in x['records'])

@pytest.mark.parametrize('index',range(3))
@pytest.mark.parametrize('layer',c.BASELINE_KEYS)
def test_every_layer_cas_fails_before_loader(data,index,layer,monkeypatch):
 m,l,o=data;v=l[index][layer]
 if isinstance(v,dict):v['changed_unreviewed']=True
 else:v.append({'changed_unreviewed':True})
 monkeypatch.setattr(vo,'_parse_rows',lambda *a,**k:pytest.fail('Output started before complete baseline validation'))
 with pytest.raises(PatchRejected):c.convert(m,l)

@pytest.mark.parametrize('kind',['wrong_route','document','purpose','residence','duplicate','missing','alias','raw_and_repin','source_owner','source_text','source_method','quote','wrong_stay','passport_clock','usd_currency','np_status','extra_product','product_type','deleted_optional'])
def test_exact_scope_fact_source_and_product_boundaries(data,kind):
 m,l,o=data;s=m['specification'];r=s['routes'][0]
 if kind=='wrong_route':r['route']['passport_nationality']='GBR'
 elif kind=='document':r['route']['travel_document_type']='diplomatic_passport'
 elif kind=='purpose':r['route']['travel_purpose']='work'
 elif kind=='residence':r['route']['lawful_country_of_residence']='CHN'
 elif kind=='duplicate':l.append(deepcopy(l[0]))
 elif kind=='missing':l.pop()
 elif kind=='alias':l[0]['cache_key']=l[0]['cache_key'].replace('HKG','HK',1)
 elif kind=='raw_and_repin':
  l[0]['raw_guidance']['new_fact']='changed';r=next(x for x in s['routes'] if x['cache_key']==l[0]['cache_key']);r['baseline_sha256']['raw_guidance']=digest(l[0]['raw_guidance'])
 elif kind.startswith('source_'):
  source=s['sources'][0]
  if kind=='source_owner':source['url']='https://imigrasi.go.id.attacker.example/'
  elif kind=='source_text':source['text']+=' Unreviewed supplement';source['sha256']=digest(source['text'])
  else:source['capture_method']='human verified every field'
 elif kind=='quote':r['changes'][0]['evidence'][0]['quote']='Visa free for everyone forever'
 elif kind=='wrong_stay':next(x for x in r['changes'] if x['field']=='permitted_stay_days')['new']=90
 elif kind=='passport_clock':next(x for x in r['changes'] if x['field']=='passport_validity_requirement')['new']={'kind':'months_after_departure','months':6}
 elif kind=='usd_currency':r['changes'].append({'field':'government_fee','new':{'amount':0,'currency':'USD'}})
 elif kind=='np_status':next(x for x in r['changes'] if x['field']=='accommodation_evidence')['new']='Not published'
 else:
  r=next(x for x in s['routes'] if x['cache_key'].startswith('USA|USA|IDN'))
  if kind=='extra_product':r['product_changes'].append(deepcopy(r['product_changes'][0]))
  elif kind=='product_type':r['product_changes'][0]['type']='C1 tourist visa'
  else:r['product_changes'].pop()
 with pytest.raises((PatchRejected,KeyError)):c.convert(m,l)

@pytest.mark.parametrize('part',['overlay_value','overlay_proof','overlay_metadata','manifest_metadata','manifest_bool','overlay_delete'])
def test_full_prepared_equality(data,part):
 m,l,o=data
 if part=='overlay_value':o['entries'][0]['fields']['permitted_stay_days']=365
 elif part=='overlay_proof':o['entries'][0]['field_provenance']['disposition']['verifier']='human'
 elif part=='overlay_metadata':o['tampered']=True
 elif part=='manifest_metadata':m['unreviewed']=True
 elif part=='manifest_bool':m['schema_version']=True
 else:o['entries'].pop()
 with pytest.raises(PatchRejected):c.verify_prepared(m['specification'],l,m,o)

@pytest.fixture
def stores(data,tmp_path,monkeypatch):
 m,l,o=data;core=tmp_path/'core.json';operator=tmp_path/'operators.json';overlay=tmp_path/c.OVERLAY
 core.write_text(json.dumps([e for x in l for e in x['seed_entries']]));operator.write_text('[]');overlay.write_text(json.dumps(o))
 monkeypatch.setattr(vo,'OVERRIDES',core);monkeypatch.setattr(vo,'operator_overrides_path',lambda:operator);monkeypatch.setattr(vo,'_reviewed_overlay_paths',lambda:[overlay]);vo.reload()
 yield
 vo.reload()

def test_real_registered_reader_has_exact_conditions_and_separate_product_proofs(data,stores):
 for l in data[1]:
  g,p=vo.apply(l['raw_guidance'],l['route']);rows=tstation.records_for_route(l['route'],g,p)
  assert not any(row['_evidence_low'] for row in rows)
  if l['route']['destination_country']=='TWN':
   assert g['passport_validity_requirement']=={'kind':'valid_through_departure','months':0}
   assert g['arrival_card']==l['merged_guidance']['arrival_card']
   assert p['field_provenance']['arrival_card']==l['source_provenance']['field_provenance']['arrival_card']
   assert g['financial_evidence'] is None and g['accommodation_evidence'] is None
   assert 'fund' not in ' '.join(g['required_documents']).lower()
  else:
   assert g['arrival_card']['required'] is True and '3 days' in g['arrival_card']['submission_window']
   assert g['passport_validity_requirement']=={'kind':'months_after_arrival','months':6}
   assert g['financial_evidence']=='Have sufficient living expenses while in Indonesia.'
   assert g['accommodation_evidence'] is None
   if l['route']['passport_nationality']=='HKG':
    assert not g.get('visa_products')
    assert not g.get('government_fee',{}).get('currency')
   else:
    assert len(g['visa_products'])==2
    for product in g['visa_products']:
     assert product['fee']=={'amount':500000,'currency':'IDR'}
     assert product['field_provenance']['disposition']['subject']['product_type']==product['type']
     assert product['source_url']=='https://evisa.imigrasi.go.id/web/visa-selection'

@pytest.mark.parametrize('issue',[False,True])
@pytest.mark.parametrize('pending',[False,True])
def test_canonical_cache_read_preserves_raw_pending_issues_dates_and_history(data,stores,monkeypatch,issue,pending):
 engine=create_engine('sqlite:///:memory:')
 for model in (KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog):model.__table__.create(engine)
 monkeypatch.setattr(kp,'_call',lambda *a,**k:pytest.fail('No regeneration'))
 with Session(engine) as db:
  for l in data[1]:
   db.add(KimiRouteGuidanceCache(cache_key=l['cache_key'],route=deepcopy(l['route']),guidance=deepcopy(l['raw_guidance']),verification={'detail_pending':pending},fresh_until=datetime(2099,1,1,tzinfo=timezone.utc),status='KIMI_PRIMARY'))
   db.add(DatabaseChangeLog(cache_key=l['cache_key'],action='add',origin='fixture',changes={'retained':True}))
   if issue:db.add(DatabaseIssueReport(cache_key=l['cache_key'],route=deepcopy(l['route']),field='disposition',status='open',reported_by='freshness_monitor',proposal={'fields':{'disposition':{'record_holds':'VISA_REQUIRED'}}}))
  db.commit()
  def snap():return {m.__name__:[{col.name:deepcopy(getattr(r,col.name)) for col in m.__table__.columns} for r in db.scalars(select(m))] for m in (KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog)}
  before=snap()
  for l in data[1]:
   out=kp.get_route_guidance(db,l['route']);assert out['cached']
   held=records_guard.apply_records_hold(l['route'],out,db)
   if issue or pending:assert held.get('held')
   else:assert not held.get('held')
   if pending:assert out['detail_pending']
  assert snap()==before

def test_mode_specific_customs_survives_normalization_without_fake_fee(data,stores):
 for l in data[1]:
  if l['route']['destination_country']!='IDN':continue
  g,p=vo.apply(l['raw_guidance'],l['route'])
  text=g['entry_requirements'];assert text.startswith('For air and sea arrivals, complete') and 'For land arrivals' in text
  assert g['arrival_card']['name']=='Indonesia arrival declarations (by mode of entry)'
  assert g['arrival_card']['url']=='https://beacukai.go.id/ecd'
  assert g['arrival_card']['submission_window'].startswith('For air/sea arrivals,')
  assert 'Land arrivals: ECD Bea Cukai at https://ecd.beacukai.go.id/' in g['arrival_card']['notes']
  assert 'https://ecd.beacukai.go.id/' in text and 'https://allindonesia.imigrasi.go.id/' in text
  assert 'upon landing' in g['arrival_card']['submission_window']
  assert 'fee_amount' not in g['arrival_card'] and 'fee_currency' not in g['arrival_card']
  rows=tstation.records_for_route(l['route'],g,p)
  for row in rows:assert 'For land arrivals' in row['entry_requirements']
  if l['route']['passport_nationality']=='HKG':
   assert l['raw_guidance']['forms']==['Electronic Customs Declaration (e-CD)']
   assert not g.get('forms') and 'customs' in g['entry_requirements']

@pytest.mark.parametrize('kind',['remove_land','mandatory_preboarding','claim_no_customs','alter_migration','unknown_absence_credit','source_future','date_metadata','lost_product_proof'])
def test_preserve_condition_clock_unknown_and_proof_boundaries(data,kind):
 m,l,o=data;s=m['specification'];r=next(x for x in s['routes'] if x['cache_key'].startswith('HKG'))
 if kind=='alter_migration':r['form_migration']['old']=['Any form']
 elif kind=='unknown_absence_credit':next(x for x in r['changes'] if x['field']=='accommodation_evidence')['status']='not_published'
 elif kind=='source_future':s['sources'][0]['checked_at']='2099-01-01'
 elif kind=='date_metadata':s['sources'][0]['published_at']='2026-09-10'
 elif kind=='lost_product_proof':
  r=next(x for x in s['routes'] if x['cache_key'].startswith('USA|USA|IDN'));r['product_changes'][0]['changes'].pop('disposition')
 else:
  change=next(x for x in r['changes'] if x['field']=='entry_requirements')
  if kind=='remove_land':change['new']=change['new'].split('For land arrivals')[0]
  elif kind=='mandatory_preboarding':change['new']+=' Mandatory before boarding.'
  else:change['new']='No customs declaration is required.'
 with pytest.raises(PatchRejected):c.convert(m,l)
