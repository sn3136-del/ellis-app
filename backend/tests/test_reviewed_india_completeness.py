from copy import deepcopy
from pathlib import Path
from collections import Counter
import json
import pytest
from scripts import convert_reviewed_india_completeness as c
from scripts.prepare_reviewed_product_patch import PatchRejected
from app.visa_snapshot import tstation as t
D=Path(__file__).resolve().parents[2]/'data/database_seed'
M=json.loads((D/'reviewed_india_completeness_manifest_20260910.json').read_text());O=json.loads((D/'reviewed_india_completeness_overlay_20260910.json').read_text())
L=[dict(deepcopy(r['baseline']),cache_key=r['cache_key']) for r in M['routes']];SPEC=M['specification']
@pytest.fixture(scope='module')
def result():return c.convert(M,L)
def test_full_prepared_rebuild_source_scope_and_grades(result):
 o,r=result;assert o==O and c.build_manifest(SPEC,L)==M
 assert len(o['entries'])==16 and len(r['routes'])==17
 assert sum(len(x['records']) for x in r['routes'])==75
 assert all(x['confidence_level'] in ('High','Medium') for route in r['routes'] for x in route['records'])  # checked: High when complete, Medium with gaps
 assert not any(r[x] for x in ('confidence_changed','raw_writes','operator_writes','issue_changes','renew_fresh_until','new_release'))
@pytest.mark.parametrize('i',range(17))
@pytest.mark.parametrize('field',c.BASELINE_KEYS)
def test_all_six_layers_on_each_route_reject_drift(i,field):
 ls=deepcopy(L)
 if isinstance(ls[i][field],dict):ls[i][field]['__unreviewed__']=True
 else:ls[i][field].append({'unreviewed':True})
 with pytest.raises(PatchRejected):c.convert(M,ls)
@pytest.mark.parametrize('mode',['missing','duplicate','extra','missing_source','changed_source_text','changed_source_body','wrong_proof','extra_fact','whole_grade','wrong_product','processing_value','policy_date'])
def test_instruction_source_ownership_or_scope_tampering_rejects(mode):
 m=deepcopy(M);ls=deepcopy(L)
 if mode=='missing':ls.pop()
 elif mode=='duplicate':ls[-1]=deepcopy(ls[0])
 elif mode=='extra':ls.append(deepcopy(ls[0]))
 elif mode=='missing_source':m['specification']['sources'].pop()
 elif mode=='changed_source_text':m['specification']['sources'][0]['text']+=' unreviewed'
 elif mode=='changed_source_body':m['specification']['sources'][0]['capture_body_sha256']='0'*64
 elif mode=='wrong_proof':m['specification']['routes'][0]['changes']['visa_products'][0]['field_provenance']['processing_time']['status']='reviewed'
 elif mode=='extra_fact':m['specification']['routes'][0]['changes']['visa_category']='Invented'
 elif mode=='whole_grade':m['specification']['routes'][0]['changes']['confidence']='High'
 elif mode=='wrong_product':m['specification']['routes'][0]['changes']['visa_products'][0]['type']='Other product'
 elif mode=='processing_value':m['specification']['routes'][0]['changes']['visa_products'][0]['processing_time']='4 days'
 elif mode=='policy_date':m['specification']['routes'][0]['changes']['policy_valid_until']='2027-01-01'
 with pytest.raises(PatchRejected):c.convert(m,ls)
def test_every_other_canonical_fact_and_proof_exactly_preserved(result):
 _,r=result;old={l['cache_key']:l for l in L}
 for new in r['routes']:
  l=old[new['cache_key']];a=deepcopy(l['merged_guidance']);b=deepcopy(new['guidance']);cfg=c.CONTRACT[l['cache_key']]
  for i in cfg['port_indices']:
   assert b['exceptions'][i]==c._port(a['exceptions'][i]);b['exceptions'][i]=a['exceptions'][i]
  for item in cfg['products']:
   p=a['visa_products'][item['index']];q=b['visa_products'][item['index']]
   for i in item['port_indices']:
    assert q['exceptions'][i]==c._port(p['exceptions'][i]);q['exceptions'][i]=p['exceptions'][i]
   fields={f for f,v in [('processing_time',item['processing_np']),('fee',item['zero_fee']),('validity',item['validity_equivalence']),('exceptions',item['port_indices'])] if v}
   for field in fields:q.get('field_provenance',{}).pop(field,None);p.get('field_provenance',{}).pop(field,None)
   for prod in (p,q):
    if prod.get('field_provenance')=={}:prod.pop('field_provenance')
  assert a==b
  pa=deepcopy(l['source_provenance']);pb=deepcopy(new['source_provenance'])
  if cfg['port_indices']:pa['field_provenance'].pop('exceptions',None);pb['field_provenance'].pop('exceptions',None)
  assert pa==pb

def test_exact_158_documented_cells_no_invented_processing_or_policy_date(result):
 rs=[z for route in result[1]['routes'] for z in route['records']];counts=Counter(t.field_status(x)[k] for x in rs for k in t.CONTRACT_FIELDS)
 # Field 24 on a checked answer is a documented absence (75 rows), no longer a gap.
 assert counts=={'filled':1618,'not-published':223,'missing':18,'optional-empty':16}
 assert t.acceptance_summary(rs)['documented_completed_cells']==1841
 assert all(x['info_validity'] is None and x['confidence_level'] in ('High','Medium') for x in rs)
 assert sum(t.field_status(x)['processing_min_days']=='not-published' for x in rs)==72
 assert all(x['processing_min_days'] is None and x['processing_unit'] is None for x in rs)
 assert all(t.field_status(x)['processing_min_days']!='not-published' for x in rs if x['visa_type_name'] in ['Visa on arrival','Regular tourist visa'])
 assert sum(x['visa_fee_amount']==0 for x in rs)==12
 for x in rs:
  if x['travel_document_country']=='TWN' and x['visa_type_name']=='1-year e-Tourist Visa':assert (x['validity_duration'],x['validity_unit'])==(365,'Day')
  if x['travel_document_country']=='USA' and x['visa_type_name']=='5-year e-Tourist Visa':assert (x['visa_fee_amount'],x['visa_fee_currency'])==(160,'USD')

def test_air_only_declaration_annual_caps_and_voa_airports_unchanged(result):
 old={x['cache_key']:x for x in L}
 for x in result[1]['routes']:
  a=old[x['cache_key']]['merged_guidance'];b=x['guidance']
  for f in ['entry_requirements','arrival_card','permitted_stay','permitted_stay_days','policy_valid_until','confidence']:assert a.get(f)==b.get(f)
  for p,q in zip(a['visa_products'],b['visa_products'],strict=True):
   for f in ['entry_requirements','max_stay_days','permitted_stay','fee','validity','entry','application_channel','application_channel_detail','required_documents']:assert p.get(f)==q.get(f)
  if x['cache_key'].startswith(('JPN|','KOR|')):assert a['visa_products'][0]==b['visa_products'][0]
  if x['cache_key'].startswith('HKG|'):assert a==b
@pytest.mark.parametrize('text',['One year (365 days) from the date of grant of ETA','1 year (365 days) from date of grant of ETA'])
def test_explicit_source_equivalent_validity(text):assert t._validity_num_unit(text)==(365,'Day')
@pytest.mark.parametrize('text',['One year (366 days) from the date of grant of ETA','One year or 365 days from the date of grant of ETA','One year (365 days) stay from the date of grant of ETA','One year (365 days) from the date of grant of ETA if eligible','One year (365 days) from the date of grant of ETA; other option2years','One year (365 working days) from the date of grant of ETA','One year (180 days) from the date of grant of ETA','Not one year (365 days) from the date of grant of ETA'])
def test_alternative_or_conditional_validity_unknown(text):assert t._validity_num_unit(text)==(None,None)
def fee_case():
 l=next(x for x in L if x['cache_key'].startswith('MYS|'));return l,deepcopy(c.values(l)['visa_products'][0])
def test_own_official_zero_does_not_need_waiver_word():
 l,p=fee_case();assert t._fee(p,l['merged_guidance'],l['route'])==(0,'USD')
@pytest.mark.parametrize('mutation',['unknown','legacy','missing_subject','sibling','wrong_passport','wrong_destination','wrong_purpose','wrong_document','other_currency','bool_value','future','no_date','other_host','other_source','no_quote','other_nationality_row','paid_column','no_currency_quote','nonzero_token','no_reviewed_value','interval','parent_proof'])
def test_zero_cannot_inherit_or_select_neighbouring_tariff(mutation):
 l,p=fee_case();route=deepcopy(l['route']);proof=p['field_provenance']['fee']
 if mutation=='unknown':proof['status']='unknown'
 elif mutation=='legacy':proof.pop('verification_scope')
 elif mutation=='missing_subject':proof.pop('subject')
 elif mutation=='sibling':proof['subject']['product_type']='Different visa'
 elif mutation=='wrong_passport':proof['subject']['passport_nationality']='THA'
 elif mutation=='wrong_destination':proof['subject']['destination_country']='IDN'
 elif mutation=='wrong_purpose':proof['subject']['travel_purpose']='work'
 elif mutation=='wrong_document':proof['subject']['travel_document_type']='diplomatic_passport'
 elif mutation=='other_currency':proof['reviewed_value']['currency']='EUR'
 elif mutation=='bool_value':proof['reviewed_value']['amount']=False
 elif mutation=='future':proof['verified_at']='2999-01-01'
 elif mutation=='no_date':proof.pop('verified_at')
 elif mutation=='other_host':proof['source_url']='https://unreviewed.example/fees'
 elif mutation=='other_source':proof['source_url']='https://indianvisaonline.gov.in/evisa/tvoa.html'
 elif mutation=='no_quote':proof.pop('quote')
 elif mutation=='other_nationality_row':proof['quote']='Indonesia 00 00 00 00'
 elif mutation=='paid_column':p['type']='1-year e-Tourist Visa';proof['subject']['product_type']=p['type']
 elif mutation=='no_currency_quote':proof['additional_quotes']=[]
 elif mutation=='nonzero_token':proof['quote']='Malaysia 10 25 40 200'
 elif mutation=='no_reviewed_value':proof.pop('reviewed_value')
 elif mutation=='interval':proof['effective_to']='2027-01-01'
 elif mutation=='parent_proof':l['merged_guidance']['field_provenance']={'fee':deepcopy(proof)};p['field_provenance'].pop('fee')
 assert not t._reviewed_product_zero_fee(p,p['fee'],route)

@pytest.mark.parametrize('issue',[False,True])
@pytest.mark.parametrize('pending',[False,True])
def test_actual_cached17_readers_preserve_all_independent_guards_and_database(result,monkeypatch,tmp_path,issue,pending):
 from datetime import datetime,timezone
 from unittest.mock import patch
 from sqlalchemy import create_engine,select
 from sqlalchemy.orm import Session
 from app.visa_snapshot import verified_overrides as vo,kimi_primary as kp,records_guard
 from app.visa_snapshot.models import KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog
 before_table=vo._parse_rows([e for l in L for e in l['seed_entries']],{});after_table=vo._parse_rows(result[0]['entries'],deepcopy(before_table))
 engine=create_engine('sqlite:///:memory:')
 for model in (KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog):model.__table__.create(engine)
 monkeypatch.setattr(kp,'_call',lambda *a,**k:pytest.fail('No model regeneration'))
 monkeypatch.setenv('ELLIS_OPERATOR_OVERRIDES',str(tmp_path/'none.json'));monkeypatch.setattr(vo,'_table',lambda:after_table)
 with Session(engine) as db:
  for l in L:
   db.add(KimiRouteGuidanceCache(cache_key=l['cache_key'],route=deepcopy(l['route']),guidance=deepcopy(l['raw_guidance']),verification={'detail_pending':pending},fresh_until=datetime(2099,1,1,tzinfo=timezone.utc),status='KIMI_PRIMARY'))
   db.add(DatabaseChangeLog(cache_key=l['cache_key'],action='add',origin='fixture',changes={'unchanged':True}))
   if issue:db.add(DatabaseIssueReport(cache_key=l['cache_key'],route=deepcopy(l['route']),field='passport_validity',status='open',reported_by='freshness_monitor',proposal={'fields':{'passport_validity':{'record_holds':'old','page_says':'new'}}}))
  db.commit()
  def snapshot():return {m.__name__:[{col.name:deepcopy(getattr(r,col.name)) for col in m.__table__.columns} for r in db.scalars(select(m))] for m in (KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog)}
  initial=snapshot()
  for l in L:
   with patch.object(vo,'_table',return_value=before_table):before=kp.get_route_guidance(db,l['route']);old_held=records_guard.apply_records_hold(l['route'],deepcopy(before),db)
   after=kp.get_route_guidance(db,l['route']);held=records_guard.apply_records_hold(l['route'],deepcopy(after),db)
   assert before['cached'] and after['cached'];assert bool(held.get('held'))==bool(old_held.get('held'))
   if issue or pending:assert held['held']
   assert before['workflow_plan']==after['workflow_plan']
   for field in ('entry_requirements','arrival_card','permitted_stay','permitted_stay_days','confidence','insurance_required','government_fee'):assert before['guidance'].get(field)==after['guidance'].get(field)
   if not issue and not pending:
    rows=t.records_for_route(l['route'],after['guidance'],after['source_verified'])
    assert all(x['confidence_level'] in ('High','Medium') for x in rows)
    if l['cache_key'].startswith('TWN|'):assert next(x for x in rows if x['visa_type_name']=='1-year e-Tourist Visa')['validity_duration']==365
  assert snapshot()==initial
