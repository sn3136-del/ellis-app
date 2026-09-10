from copy import deepcopy
from pathlib import Path
from datetime import datetime,timezone
import json
import pytest
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from app.visa_snapshot.models import KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog
from app.visa_snapshot import verified_overrides as vo,tstation,records_guard,kimi_primary as kp
from app.visa_snapshot import reviewed_hkg_mainland_fields as authority
from app.visa_snapshot import reviewed_hkg_mainland_warning_resolution as warning
from app.visa_snapshot.authority import is_government_host
from app.visa_snapshot.evidence_validator import field_value_supported
from scripts import convert_reviewed_hkg_mainland_permit as c
from scripts.prepare_reviewed_product_patch import PatchRejected,digest
DATA=Path(__file__).resolve().parents[2]/'data/database_seed'
@pytest.fixture
def data():
 m=json.loads((DATA/c.MANIFEST).read_text());o=json.loads((DATA/c.OVERLAY).read_text())
 return m,[dict(deepcopy(x['baseline']),cache_key=x['cache_key']) for x in m['routes']],o
@pytest.fixture
def stores(data,tmp_path,monkeypatch):
 m,l,o=data;base=tmp_path/'baseline.json';base.write_text(json.dumps([x for a in l for x in a['seed_entries']]))
 (tmp_path/c.MANIFEST).write_text(json.dumps(m));(tmp_path/c.OVERLAY).write_text(json.dumps(o));op=tmp_path/'operator.json';op.write_text('[]')
 monkeypatch.setattr(vo,'OVERRIDES',base);monkeypatch.setattr(vo,'operator_overrides_path',lambda:op);monkeypatch.setattr(vo,'_reviewed_overlay_paths',lambda:[tmp_path/c.OVERLAY]);vo.reload();warning._rebuilt.cache_clear()
 yield tmp_path
 vo.reload();warning._rebuilt.cache_clear()

def test_complete_review_and_exact_delegate_ownership(data):
 m,l,o=data;original=deepcopy(data);actual,r,preview=c.convert(m,l)
 assert actual==o and data==original
 g=preview[0]['guidance'];p=preview[0]['source_verified']
 assert g['disposition']=='CONDITIONAL' and g['requirement_detail']=='conditional_visa_free'
 assert g['permitted_stay_days'] is None and g['permitted_stay'] is None
 assert g['passport_validity_requirement'] is None and g['appointment_required'] is True
 assert 'application' in g['passport_validity'] and 'more than six months' in g['passport_validity']
 assert not is_government_host('www.ctshk.com')
 for f in authority.FIELDS:assert p['field_provenance'][f]['source_url']==c.SOURCES['cts_pdf']['url']
 assert p['source_url']==c.SOURCES['nia_regulation']['url'] and p['verifier']=='ai'
 for field in r[0]['unknown_fields']:assert g[field] is None and p['field_provenance'][field]['status']=='unknown'
 assert not r[0]['renew_fresh_until']
 for old,new in zip(l[0]['merged_guidance']['visa_products'],g['visa_products'],strict=True):
  assert old['type']==new['type'] and old['fee']==new['fee'] and old['validity']==new['validity'] and new['max_stay_days'] is None
  for f in authority.FIELDS:assert new['field_provenance'][f]['source_url']==c.SOURCES['cts_pdf']['url']
 for row in r[0]['records']:
  assert not row['_evidence_low'] and row['max_stay_duration'] is None
  assert row['visa_fee_currency']=='HKD' and row['visa_fee_amount'] in (260,390)
 assert c.verify_prepared(m['specification'],l,m,o)

@pytest.mark.parametrize('field',c.BASELINE_KEYS)
@pytest.mark.parametrize('rewrite_hash',[False,True])
def test_every_layer_cas_cannot_be_reauthorized_by_rehashing(data,field,rewrite_hash):
 m,l,o=data;v=l[0][field]
 if isinstance(v,dict):v['different']='unreviewed'
 else:v.append({'different':'unreviewed'})
 if rewrite_hash:m['specification']['routes'][0]['baseline_sha256'][field]=digest(v)
 with pytest.raises(PatchRejected):c.build_manifest(m['specification'],l)

@pytest.mark.parametrize('kind',['missing','duplicate','alias','passport','residence','fee_bool','appointment_integer','source_url','source_text','body_hash','date','backlink','proof','unknown_np','products','extra'])
def test_exact_source_subject_and_typed_fact_contract(data,kind):
 m,l,_=data;s=m['specification'];r=s['routes'][0]
 if kind=='missing':l=[]
 elif kind=='duplicate':l.append(deepcopy(l[0]))
 elif kind=='alias':l.append(dict(deepcopy(l[0]),cache_key='HKG|HKG|CHN|tourism|alias|unknown|v6'))
 elif kind=='passport':r['route']['travel_document_type']='diplomatic_passport'
 elif kind=='residence':r['route']['lawful_country_of_residence']='USA'
 elif kind=='fee_bool':next(x for x in r['changes'] if x['field']=='government_fee')['new']['amount']=True
 elif kind=='appointment_integer':next(x for x in r['changes'] if x['field']=='appointment_required')['new']=1
 elif kind=='source_url':s['sources'][0]['url']='https://nia.gov.cn.evil.example/'
 elif kind=='source_text':s['sources'][0]['text']+=' unreviewed'
 elif kind=='body_hash':s['sources'][0]['http_body_sha256']='0'*64
 elif kind=='date':s['sources'][0]['checked_at']='2026-09-11'
 elif kind=='backlink':s['authority_links']['target']='https://evil.example/'
 elif kind=='proof':r['changes'][0]['evidence'][0]['quote']='Always visa-free.'
 elif kind=='unknown_np':next(x for x in r['changes'] if x['field']=='permitted_stay')['new']='Not published'
 elif kind=='products':r['products'].pop()
 else:s['certify_all']=True
 with pytest.raises(PatchRejected):c.build_manifest(s,l)

@pytest.mark.parametrize('kind',['proof','value','metadata','product','bool'])
def test_complete_prepared_equality(data,kind):
 m,l,o=data
 if kind=='proof':o['entries'][0]['field_provenance']['passport_validity']['source_url']=c.SOURCES['nia_guide']['url']
 elif kind=='value':o['entries'][0]['fields']['permitted_stay_days']=90
 elif kind=='metadata':o['human_approved']=True
 elif kind=='product':o['entries'][0]['fields']['visa_products'].pop()
 else:m['schema_version']=True
 with pytest.raises(PatchRejected):c.verify_prepared(m['specification'],l,m,o)

@pytest.mark.parametrize('kind',['nationality','passport','residence','proof','link','product_removed','product_proof','field_value','binding'])
def test_wrong_delegate_claim_is_hard_hold_even_with_operator_release(data,kind,monkeypatch):
 m,l,_=data;_,_,pv=c.convert(m,l);out=deepcopy(pv[0]);route=deepcopy(out['route']);g=out['guidance'];p=out['source_verified']
 if kind=='nationality':route['passport_nationality']='GBR'
 elif kind=='passport':route['travel_document_type']='diplomatic_passport'
 elif kind=='residence':route['lawful_country_of_residence']='USA'
 elif kind=='proof':p['field_provenance']['passport_validity']['quote']='No passport required'
 elif kind=='link':p['field_provenance']['photo_requirements']['source_url']='https://www.ctshk.com/unreviewed.pdf'
 elif kind=='product_removed':g['visa_products'].pop()
 elif kind=='product_proof':g['visa_products'][0]['field_provenance']['required_documents'].pop('authority_binding_sha256')
 elif kind=='field_value':g['required_documents']=[]
 else:p['field_provenance']['required_documents']['authority_binding_sha256']='0'*64
 out['operator_released']=True;monkeypatch.setattr(kp,'hold_enabled',lambda:False)
 held=records_guard.apply_records_hold(route,out);assert held['held'] and records_guard.held_envelope(held)['guidance'] is None

@pytest.mark.parametrize('kind',['proof','value','product','scope'])
def test_invalid_registered_overlay_fails_closed(data,stores,kind):
 m,l,o=data;row=o['entries'][0]
 if kind=='proof':row['field_provenance']['passport_validity']['verified_by']='human'
 elif kind=='value':row['fields']['required_documents']=[]
 elif kind=='product':row['fields']['visa_products'].pop()
 else:row['route']['nationality']='GBR'
 (stores/c.OVERLAY).write_text(json.dumps(o));vo.reload()
 g,p=vo.apply(l[0]['raw_guidance'],l[0]['route']);assert g.get('source_verification_store_unavailable') and p is None

@pytest.mark.parametrize('kind',['none','added','altered','other_fact','missing_manifest','tampered_overlay'])
def test_exact_installed_warning_reconciliation(data,stores,kind):
 _,l,o=data;raw=deepcopy(l[0]['raw_guidance']);old=deepcopy(raw['uncertainty']);extra={'field':'new','reason':'Unresolved latest notice'}
 if kind=='added':raw['uncertainty'].append(extra)
 elif kind=='altered':raw['uncertainty'][0]['reason']+=' Now under a new dispute.'
 elif kind=='other_fact':raw['confidence']='different'
 elif kind=='missing_manifest':(stores/c.MANIFEST).unlink()
 elif kind=='tampered_overlay':o['extra']=True;(stores/c.OVERLAY).write_text(json.dumps(o));vo.reload()
 g,p=vo.apply(raw,l[0]['route'])
 if kind=='none':assert g['uncertainty']==old[2:]
 elif kind=='added':assert g['uncertainty']==old[2:]+[extra]
 elif kind=='altered':assert g['uncertainty']==[raw['uncertainty'][0],old[2]]
 else:assert g['uncertainty']==raw['uncertainty']

@pytest.mark.parametrize('issue',[False,True])
@pytest.mark.parametrize('pending',[False,True])
def test_actual_canonical_reader_preserves_raw_dates_pending_issues_history(data,stores,monkeypatch,issue,pending):
 m,l,_=data;layer=l[0];engine=create_engine('sqlite:///:memory:')
 for model in (KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog):model.__table__.create(engine)
 monkeypatch.setattr(kp,'_call',lambda *a,**k:pytest.fail('No regeneration'))
 with Session(engine) as db:
  db.add(KimiRouteGuidanceCache(cache_key=layer['cache_key'],route=deepcopy(layer['route']),guidance=deepcopy(layer['raw_guidance']),verification={'detail_pending':pending},fresh_until=datetime(2099,1,1,tzinfo=timezone.utc),status='KIMI_PRIMARY'))
  db.add(DatabaseChangeLog(cache_key=layer['cache_key'],action='add',origin='fixture',changes={'unchanged':True}))
  if issue:db.add(DatabaseIssueReport(cache_key=layer['cache_key'],route=deepcopy(layer['route']),field='passport_validity',status='open',reported_by='freshness_monitor',proposal={'fields':{'passport_validity':{'record_holds':'six months'}}}))
  db.commit()
  def snapshot():return {x.__name__:[{col.name:deepcopy(getattr(r,col.name)) for col in x.__table__.columns} for r in db.scalars(select(x))] for x in (KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog)}
  before=snapshot();out=kp.get_route_guidance(db,layer['route']);assert out['cached']
  guarded=records_guard.apply_records_hold(layer['route'],out,db)
  assert bool(guarded.get('held'))==bool(issue or pending)
  assert out['guidance']['permitted_stay_days'] is None and len(out['guidance']['visa_products'])==2
  assert out['guidance']['uncertainty']==layer['raw_guidance']['uncertainty'][2:]
  assert snapshot()==before

@pytest.mark.parametrize('text,expected',[
 ('港澳居民申请港澳居民来往内地通行证应当本人前往受理机构提出申请，未满十八周岁的申请人须由法定监护人陪同申请并提供法定监护人的身份证件。',True),
 ('申請人須親自到受理部門遞交申請。',False),('You must submit the visa application in person.',True),('Permit applications must be submitted in person.',True),
 ('Applicants should not submit a visa application in person.',False),
 ('If you hold a qualifying residence permit, submit your application in person.',False),
 ('You are ineligible to apply. Applications must be submitted in person.',False),
 ('Submit a permit application in person only for replacement permits.',False),
 ('Submit an application in person to renew a driving licence.',False),
 ('Submit a driving permit application in person.',False),
 ('You may submit the permit application in person provided that you qualify.',False),
 ('Applicants must attend in person for biometrics only.',False),('Applications must be submitted by an agent. Applicants attend in person for biometrics.',False),
 ('Applications must be submitted in person by an authorized agent.',False),('Applications must not be submitted in person.',False),('You cannot submit an application in person.',False),
 ('申請人無需親自到受理部門遞交申請。',False),('申请人应当本人前往受理机构采集指纹，申请仅限代理。',False),
])
def test_in_person_evidence_requires_self_filing_not_biometrics(text,expected):assert field_value_supported('application_channel','in_person',text)==expected

@pytest.mark.parametrize('quote,amount,currency,expected',[
 ('有效期为5年的港澳居民来往内地通行证收费260元港币',260,'HKD',True),('有效期為10年的通行證收費390元港幣',390,'HKD',True),
 ('有效期为5年的通行证收费260元人民币',260,'HKD',False),('有效期为5年的通行证收费260元港币',260,'USD',False),('有效期为5年的通行证收费260元港币',390,'HKD',False),
])
def test_literal_hkd_currency_does_not_certify_rmb_or_other_amount(quote,amount,currency,expected):
 from scripts.convert_reviewed_general_batch import _monetary_text
 import re
 text=_monetary_text(quote,currency)
 assert bool(re.search(rf'(?<!\d){amount}(?:\.0+)?\s*{currency}\b|\b{currency}\s*{amount}(?!\d)',text,re.I))==expected

@pytest.mark.parametrize('kind',['route_proof_map','product_proof_map','products_map','products_item','route_proof_item'])
def test_registered_malformed_proofs_hold_without_projector_crash(data,kind,monkeypatch):
 m,l,_=data;_,_,pv=c.convert(m,l);out=deepcopy(pv[0]);g=out['guidance'];p=out['source_verified']
 if kind=='route_proof_map':p['field_provenance']='unexpected'
 elif kind=='product_proof_map':g['visa_products'][0]['field_provenance']='unexpected'
 elif kind=='products_map':g['visa_products']={'unexpected':True}
 elif kind=='products_item':g['visa_products']=[None]
 else:p['field_provenance']['required_documents']='unexpected'
 out['operator_released']=True;monkeypatch.setattr(kp,'hold_enabled',lambda:False)
 held=records_guard.apply_records_hold(out['route'],out)
 assert held['held'] and records_guard.held_envelope(held)['guidance'] is None

@pytest.mark.parametrize('text',[
 '港澳居民申请港澳居民来往内地通行证应当本人前往受理机构提出申请，但本规定不适用于旅游申请。',
 '本规定已停止执行：港澳居民申请港澳居民来往内地通行证应当本人前往受理机构提出申请。',
])
def test_rescinded_or_inapplicable_chinese_rule_cannot_be_revived(text):
 assert not field_value_supported('application_channel','in_person',text)
