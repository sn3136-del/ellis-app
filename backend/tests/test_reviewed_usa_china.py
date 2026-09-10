from copy import deepcopy
from pathlib import Path
import json
import pytest
from scripts import convert_reviewed_usa_china as c
from scripts.prepare_reviewed_product_patch import PatchRejected
from app.visa_snapshot import verified_overrides as vo,kimi_primary as kp,tstation
from app.visa_snapshot.records_guard import apply_records_hold,held_envelope
from app.visa_snapshot.reviewed_usa_china_workflow import steps,VALUES
ROOT=Path(__file__).resolve().parents[2]
M=json.loads((ROOT/'data/database_seed'/c.MANIFEST).read_text())
O=json.loads((ROOT/'data/database_seed'/c.OVERLAY).read_text())
S=M['specification'];L=[dict(deepcopy(M['baseline']),cache_key=M['cache_key'])]
def preview():return c.convert(deepcopy(M),deepcopy(L))[1]['routes'][0]

def test_full_prepared_rebuild_preserves_layers_and_scope():
 before=deepcopy(L);m=c.build_manifest(S,L);o,r=c.convert(m,L)
 assert m==M and o==O and L==before and c.verify_prepared(S,L,M,O)==r
 assert r['route_fields_reviewed']==29 and r['existing_products_preserved']==3
 assert all(r[f] is False for f in ('raw_writes','operator_writes','issue_changes','renew_fresh_until','confidence_changed','product_grants_guaranteed'))

@pytest.mark.parametrize('field',c.BASELINE_KEYS)
def test_all_six_layer_cas(field):
 x=deepcopy(L)
 if isinstance(x[0][field],list):x[0][field].append({'drift':True})
 else:x[0][field]['drift']=True
 with pytest.raises(PatchRejected):c.convert(M,x)

@pytest.mark.parametrize('kind',['missing','duplicate','alias','extra'])
def test_complete_exact_keyset(kind):
 x=deepcopy(L)
 if kind=='missing':x=[]
 elif kind=='duplicate':x.append(deepcopy(x[0]))
 elif kind=='alias':x[0]['cache_key']=x[0]['cache_key'].replace('USA|USA','US|US')
 else:x.append({'cache_key':'different'})
 with pytest.raises(PatchRejected):c.build_manifest(S,x)

@pytest.mark.parametrize('kind',['url','quote','body_hash','text','date','publisher','value','subject','rowspan','fee_end','product_type','product_fee','product_stay','sibling_proof'])
def test_tampered_sources_values_scope_and_products(kind):
 s=deepcopy(S)
 if kind=='url':s['sources'][0]['url']='https://example.com/visa'
 elif kind=='quote':s['changes'][0]['proof']['quote']='Visas are optional'
 elif kind=='body_hash':s['sources'][0]['http_body_sha256']='a'*64
 elif kind=='text':s['sources'][0]['text']='Changed'
 elif kind=='date':s['changes'][0]['proof']['verified_at']='2027-01-01'
 elif kind=='publisher':next(x for x in s['sources'] if x['id']=='us_state_entry')['authority']='Chinese destination authority'
 elif kind=='value':next(x for x in s['changes'] if x['field']=='permitted_stay_days')['new']=90
 elif kind=='subject':s['changes'][0]['proof']['subject']['passport_nationality']='CAN'
 elif kind=='rowspan':next(x for x in s['changes'] if x['field']=='government_fee')['proof']['verification_scope']['source_table']['us_fee_rowspan']=1
 elif kind=='fee_end':next(x for x in s['changes'] if x['field']=='government_fee')['proof']['effective_to']='2030-12-31'
 elif kind=='product_type':s['products'][0]['new']['type']='Guaranteed single-entry visa'
 elif kind=='product_fee':s['products'][1]['new']['fee']['amount']=23
 elif kind=='product_stay':s['products'][2]['new']['max_stay_days']=60
 else:s['products'][0]['new']['field_provenance']['disposition']=deepcopy(s['products'][1]['new']['field_provenance']['disposition'])
 with pytest.raises(PatchRejected):c.build_manifest(s,L)

@pytest.mark.parametrize('artifact',['manifest','overlay'])
def test_whole_prepared_equality(artifact):
 m,o=deepcopy(M),deepcopy(O)
 if artifact=='manifest':m['baseline']['raw_guidance']['confidence']='high'
 else:o['entries'][0]['note']='Different authorship'
 with pytest.raises(PatchRejected):c.verify_prepared(S,L,m,o)

def test_requested_options_keep_own_category_without_numeric_grants():
 p=preview();g=p['guidance'];rows=p['records']
 assert [x['type'] for x in g['visa_products']]==[x['type'] for x in L[0]['merged_guidance']['visa_products']]
 for product,row in zip(g['visa_products'],rows,strict=True):
  assert product['entry'] is None and product['max_stay_days'] is None
  assert product['validity']==product['permitted_stay']=='Determined by consular officials and the visa issued.'
  assert product['notes'].startswith('Requested ') and 'no fixed duration or grant is guaranteed' in product['notes']
  own=product['field_provenance']['disposition']
  assert own['subject']['product_type']==product['type'] and own['source_id']=='nia_entry_law'
  assert all(e['source_id']!='fee2026' for e in own['verification_scope']['evidence'])
  assert row['entries'] is row['max_stay_duration'] is row['validity_duration'] is None
  assert row['confidence_level']=='Low' and not row['_evidence_low'] and row['info_validity'] is None
  assert not set(row['_unpublished']) & {'entries','validity_duration','max_stay_duration'}

def test_fee_scope_does_not_expire_whole_program():
 p=preview();g=p['guidance'];fp=p['source_provenance']['field_provenance']
 assert g['government_fee']=={'amount':140,'currency':'USD'}
 assert fp['government_fee']['effective_to']=='2026-12-31'
 assert fp['government_fee']['verification_scope']['source_table']['us_fee_rowspan']==4
 assert 'effective_to' not in fp['disposition']
 for product in g['visa_products']:
  assert product['fee']==g['government_fee']
  assert product['field_provenance']['fee']['effective_to']=='2026-12-31'
  assert 'effective_to' not in product['field_provenance']['disposition']

def test_passport_publishers_and_clocks_remain_separate():
 p=preview();g=p['guidance'];fp=p['source_provenance']['field_provenance']
 assert g['passport_validity_requirement']=={'kind':'months_after_arrival','months':6}
 assert fp['passport_validity_requirement']['source_url']=='https://travel.state.gov/en/international-travel/travel-advisories/china.html'
 assert 'not attributed to a Chinese destination authority' in fp['passport_validity_requirement']['note']
 assert 'more than 6 months' in g['required_documents'][0] and 'at least 6 months beyond arrival' in g['passport_validity']

def test_application_waiver_does_not_claim_border_evidence_exemption():
 p=preview();g=p['guidance'];fp=p['source_provenance']['field_provenance']
 assert 'no longer routinely required' in g['required_documents'][-1] and 'case by case' in g['required_documents'][-1]
 for f in ['onward_travel_evidence','accommodation_evidence','financial_evidence','health_requirements','interview_required']:
  assert g[f] is None and fp[f]['status']=='unknown' and fp[f]['verified_at'] is None
 assert 'interview' in g['application_channel_detail'] and g['appointment_required'] is g['biometrics_required'] is False

def test_all_seven_arrival_card_exemptions_and_paper_alternative():
 card=preview()['guidance']['arrival_card']
 assert card['required'] is True and 'on arrival' in card['submission_window']
 for text in ['Foreign Permanent Resident','non-Chinese Citizens','group visa','24-hour direct transit','same vessel','E-channel','crew members','paper']:assert text in card['notes']

def test_regional_transit_conditions_do_not_change_default():
 g=preview()['guidance'];text=' '.join(g['exceptions'])
 assert g['disposition']=='VISA_REQUIRED' and '144' not in text
 for part in ['240-hour','third-country','confirmed onward seats','65 ports','24 provinces','10-day','within Hainan','30 days','15 days','same scheduled cruise','a 10-year','may apply']:assert part in text

def test_complete_order_and_washington_qualifiers():
 p=preview();g=p['guidance'];s=p['apply_steps']
 assert p['application_steps_status']=='source_ordered' and s==g['submission_process'] and len(s)==4
 assert [x['instruction'] for x in p['workflow_plan']]==s and all(x['manual_only'] for x in p['workflow_plan'])
 assert 'select the Chinese embassy or consulate' in s[0] and 'preliminary review' in s[1] and 'interview if' in s[2]
 assert 'At Washington' in s[3] and 'estimated date is not a guarantee' in s[3]
 assert 'Washington' in g['processing_time'] and 'average from passport submission' in g['processing_time']
 assert all(x['processing_min_days'] is None for x in p['records'])
 assert 'Appointment confirmation' not in g['forms'] and g['route_workflow_type']=='embassy_submission'

@pytest.mark.parametrize('field',list(VALUES))
def test_order_needs_all_own_values(field):
 p=preview();g=deepcopy(p['guidance']);g[field]='unreviewed injection'
 assert steps(g,p['route'],p['source_provenance'])==[]

@pytest.mark.parametrize('field',list(VALUES))
def test_order_needs_all_own_proofs(field):
 p=preview();fp=deepcopy(p['source_provenance']);fp['field_provenance'][field]['quote']='Unreviewed'
 assert steps(p['guidance'],p['route'],fp)==[]

@pytest.mark.parametrize('field,value',[('passport_nationality','CAN'),('lawful_country_of_residence','CAN'),('destination_country','HKG'),('travel_purpose','business'),('travel_document_type','diplomatic_passport'),('arrival_date','2025-01-01'),('consular_jurisdiction','San Francisco'),('transit_countries',['JPN'])])
def test_ordered_scope_is_exact(field,value):
 p=preview();route=dict(p['route']);route[field]=value
 assert steps(p['guidance'],route,p['source_provenance'])==[]

@pytest.mark.parametrize('field,value',[('forms',{'form':'fake'}),('forms',['good',3]),('route_workflow_type','not_a_workflow'),('route_workflow_type',42)])
def test_new_override_shape_guards(field,value):
 row=deepcopy(O['entries'][0]);row['fields'][field]=value
 assert any(e.startswith(field) for e in vo._field_errors(row['fields']))
 assert field not in vo._parse_rows([row],{})[vo._key('USA','CHN','tourism','ordinary_passport')]['fields']

@pytest.mark.parametrize('entry,proof,expected',[(None,{'status':'unknown','reason':'Number granted is determined by consular officials; not a universal fixed value.'},None),(None,{'status':'unknown','reason':'No literal official quote was captured for this value'},'Single'),(None,{'status':'unknown'},'Single'),(None,None,'Single'),('single',{'status':'reviewed'},'Single')])
def test_only_explicit_unknown_entry_stops_name_fallback(entry,proof,expected):
 p=preview();g=deepcopy(p['guidance']);product=g['visa_products'][0];product['entry']=entry
 if proof is None:product['field_provenance'].pop('entry')
 else:product['field_provenance']['entry']=proof
 assert tstation.records_for_route(p['route'],g,p['source_provenance'])[0]['entries']==expected

@pytest.mark.parametrize('flag',['detail_pending','contradictions','held'])
def test_existing_holds_survive(flag):
 p=preview();out={'guidance':p['guidance'],'source_verified':p['source_provenance']}
 out[flag]=['open accuracy issue'] if flag=='contradictions' else True
 result=apply_records_hold(p['route'],out)
 assert result.get('held') is True and held_envelope(result)['guidance'] is None

def test_unknown_health_and_insurance_not_negative_claims():
 p=preview();out=apply_records_hold(p['route'],{'guidance':p['guidance'],'source_verified':p['source_provenance']})
 assert not out.get('held') and out['guidance']['health_requirements'] is None and out['guidance']['insurance_required'] is None
 assert out['requirement_evidence']['insurance_required']['status']=='unknown' and L[0]['raw_guidance']['insurance_required'] is False

@pytest.mark.parametrize('issue',[False,True])
@pytest.mark.parametrize('pending',[False,True])
def test_actual_cached_reader_keeps_conditions_history_and_holds(tmp_path,monkeypatch,issue,pending):
 from datetime import datetime,timezone
 from sqlalchemy import create_engine,select
 from sqlalchemy.orm import Session
 from app.visa_snapshot.models import KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog
 core=tmp_path/'seed.json';overlay=tmp_path/c.OVERLAY;operator=tmp_path/'operator.json'
 core.write_text(json.dumps(L[0]['seed_entries']));overlay.write_text(json.dumps(O));operator.write_text('[]')
 monkeypatch.setattr(vo,'OVERRIDES',core);monkeypatch.setattr(vo,'operator_overrides_path',lambda:operator)
 monkeypatch.setattr(vo,'_reviewed_overlay_paths',lambda:[overlay]);vo.reload()
 monkeypatch.setattr(kp,'_call',lambda *a,**kw:pytest.fail('No model generation'))
 engine=create_engine('sqlite:///:memory:')
 models=(KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog)
 for model in models:model.__table__.create(engine)
 layer=deepcopy(L[0]);raw=layer['raw_guidance']
 with Session(engine) as db:
  db.add(KimiRouteGuidanceCache(cache_key=layer['cache_key'],route=layer['route'],guidance=raw,verification={'preserved':'original','detail_pending':pending},fresh_until=datetime(2099,1,1,tzinfo=timezone.utc),status='KIMI_PRIMARY'))
  db.add(DatabaseChangeLog(cache_key=layer['cache_key'],action='add',origin='fixture',changes={'raw_history':'preserved'}))
  if issue:db.add(DatabaseIssueReport(cache_key=layer['cache_key'],route=layer['route'],field='government_fee',status='open',reported_by='freshness_monitor',proposal={'fields':{'government_fee':{'page_says':{'amount':999,'currency':'USD'}}}}))
  db.commit()
  def snapshot():return {model.__name__:[{col.name:deepcopy(getattr(row,col.name)) for col in model.__table__.columns} for row in db.scalars(select(model))] for model in models}
  before=snapshot();out=apply_records_hold(layer['route'],kp.get_route_guidance(db,layer['route']),db)
  if issue or pending:
   assert out.get('held') and held_envelope(out)['guidance'] is None
  else:
   assert not out.get('held')
   assert out['guidance']['forms']==VALUES.get('forms',preview()['guidance']['forms'])
   assert out['application_steps_status']=='source_ordered' and out['apply_steps']==preview()['apply_steps']
   assert out['guidance']['passport_validity_requirement']=={'kind':'months_after_arrival','months':6}
   assert out['guidance']['health_requirements'] is None and out['guidance']['insurance_required'] is None
   assert [x['entries'] for x in tstation.records_for_route(layer['route'],out['guidance'],out['source_verified'])]==[None]*3
  assert snapshot()==before
 vo.reload()
