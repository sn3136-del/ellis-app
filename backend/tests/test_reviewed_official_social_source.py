from copy import deepcopy
from datetime import date,datetime,timezone
import json,sqlite3
from pathlib import Path
import pytest
from sqlalchemy import create_engine
from app.visa_snapshot import reviewed_social_authority as sa,evidence_validator as ev,kimi_primary as kp,verified_overrides as vo,tstation,records_guard
from app.visa_snapshot.authority import is_government_host
from app.visa_snapshot.models import KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog
from scripts import materialize_reviewed_routes as mat
DATA=Path(__file__).parents[2]/'data/database_seed'
MANIFEST=DATA/'reviewed_social_manifest_idn_vnm20260910.json'
REGISTRY=DATA/'reviewed_social_authorities.json'
DAY=date(2026,9,10);NOW=datetime(2026,9,10,8,tzinfo=timezone.utc)
@pytest.fixture
def batch(): return json.loads(MANIFEST.read_text())
@pytest.fixture
def isolated_data(tmp_path,monkeypatch):
 p=tmp_path/'overrides.json';p.write_text('[]');monkeypatch.setattr(vo,'OVERRIDES',p)
 monkeypatch.setenv('ELLIS_OPERATOR_OVERRIDES',str(tmp_path/'operator.json'));vo.reload()
 yield tmp_path
 vo.reload()
def parsed(batch):
 e=batch['routes'][0];r={'passport_nationality':'IDN','passport_issuing_country':'IDN','destination_country':'VNM','travel_purpose':'tourism','travel_document_type':'ordinary_passport'}
 ent=dict(deepcopy(e['field_provenance']['disposition']),route=e['route'],fields=e['guidance'],field_provenance=e['field_provenance'])
 hit=vo._parse_rows([ent], {})[vo._key('IDN','VNM','tourism','ordinary_passport')]
 g,checked=vo.merge_verified_fields(deepcopy(e['guidance']),hit['fields'],source_url=ent['source_url'])
 prov=dict(hit['field_provenance']['disposition'],fields=sorted(checked),field_provenance=hit['field_provenance'])
 return r,g,prov,ent
def test_current_default_and_accurate_attribution(batch):
 r,g,p,ent=parsed(batch);assert sa.entry_supported(ent)
 assert not is_government_host('www.instagram.com') and not ev.source_is_official(g['source_url'])
 assert not ev.jurisdiction_matches(g['source_url'],'VNM')
 assert kp.validate_answer(deepcopy(g))[0]['source_url']==g['source_url']
 row,=tstation.records_for_route(r,g,p)
 assert row['max_stay_duration']==14 and not row['_evidence_low']
 assert row['confidence_level']=='Medium' and row['info_validity'] is None and row['visa_fee_currency'] is None  # checked, with gaps
 assert row['source_url']=='https://www.instagram.com/p/Dab9NF9IILJ/'
 assert not records_guard.apply_records_hold(r,{'guidance':g,'source_verified':p}).get('held',False)
 assert all(k not in batch['routes'][0]['guidance'] for k in ('government_fee','required_documents','passport_validity','unpublished_fields'))
@pytest.mark.parametrize('field,value',[('passport_nationality','MYS'),('destination_country','THA'),('travel_document_type','diplomatic_passport'),('travel_document_type','service_passport'),('travel_purpose','work'),('travel_purpose','family_visit')])
def test_copied_proof_rejects_other_route(batch,field,value):
 r,g,p,_=parsed(batch);r[field]=value
 assert not sa.guidance_supported(g,p,r)
 assert records_guard.apply_records_hold(r,{'guidance':g,'source_verified':p})['held']
@pytest.mark.parametrize('field,value',[('permitted_stay_days',30),('permitted_stay','Up to90days'),('exceptions',[]),('source_url','https://www.instagram.com/p/OtherPost/')])
def test_altered_fact_is_held(batch,field,value):
 r,g,p,_=parsed(batch);g[field]=value
 assert records_guard.apply_records_hold(r,{'guidance':g,'source_verified':p})['held']
@pytest.mark.parametrize('field,value',[('source_url','https://www.instagram.com/p/OtherPost/'),('source_url','https://instagram.com.evil.example/p/Dab9NF9IILJ/'),('quote_sha256','0'*64),('authority_binding_id','unknown'),('authority_binding_sha256','0'*64),('verified_at','2026-09-11'),('verifier','public'),('verifier','human'),('quote','Invented exemption')])
def test_invalid_proof_never_credits_verdict(batch,field,value):
 r,g,p,_=parsed(batch);p[field]=value
 assert not sa.binding_for(p,r)
 assert records_guard.apply_records_hold(r,{'guidance':g,'source_verified':p})['held']
def test_registered_link_alone_not_proof_and_other_post_stripped(batch):
 r,g,_,_=parsed(batch);assert records_guard.apply_records_hold(r,{'guidance':g})['held']
 g['source_url']='https://www.instagram.com/p/Unreviewed/'
 assert kp.validate_answer(g)[0]['source_url'] is None
def test_before_effective_date_held_without_historical_invention(batch):
 from app.visa_snapshot import policy_intervals
 r,g,p,_=parsed(batch);r['arrival_date']='2026-07-14';g=policy_intervals.annotate(g,p,r)
 out=records_guard.apply_records_hold(r,{'guidance':g,'source_verified':p})
 assert out['held'] and records_guard.held_envelope(out)['guidance'] is None and g['permitted_stay_days']==14
 r['arrival_date']='2026-07-15';g=policy_intervals.annotate(g,p,r)
 assert not records_guard.apply_records_hold(r,{'guidance':g,'source_verified':p}).get('held',False)
@pytest.mark.parametrize('change',['missing_backlink','wrong_account','wrong_owner','wrong_capture_hash','wrong_post_author'])
def test_corrupt_registry_fails_closed(tmp_path,monkeypatch,batch,change):
 x=json.loads(REGISTRY.read_text());r=x['bindings'][0];b=r['binding'];auth,post,image=r['captures']
 if change=='missing_backlink': auth['links']=[]
 elif change=='wrong_account':b['account_url']='https://www.instagram.com/impostor/'
 elif change=='wrong_owner':b['owner_country']='VNM'
 elif change=='wrong_capture_hash':b['post_capture_sha256']='0'*64
 else:post['author_account_url']='https://www.instagram.com/impostor/'
 f=tmp_path/'registry.json';f.write_text(json.dumps(x));monkeypatch.setattr(sa,'REGISTRY_PATH',f)
 assert not sa.registered_reference(batch['routes'][0]['guidance']['source_url'])
@pytest.mark.parametrize('change',['post_text','author','author_capture_text','missing_government_capture','false_30day','false_fee'])
def test_materializer_rejects_tampered_evidence(batch,isolated_data,change):
 if change=='post_text':batch['sources'][1]['text']+=' invented facts'
 elif change=='author':batch['sources'][1]['author_account_url']='https://www.instagram.com/impostor/'
 elif change=='author_capture_text':batch['sources'][1]['author_capture_text']='False author capture'
 elif change=='missing_government_capture':batch['sources']=batch['sources'][1:]
 elif change=='false_30day':batch['routes'][0]['guidance']['permitted_stay_days']=30
 else:
  batch['routes'][0]['guidance']['government_fee']={'amount':0,'currency':'USD'}
  batch['routes'][0]['field_provenance']['government_fee']=deepcopy(batch['routes'][0]['field_provenance']['disposition'])
 f=isolated_data/'bad.json';f.write_text(json.dumps(batch))
 try: result=mat._manifest(f,DAY)
 except ValueError:return
 assert result[3]
def test_materialization_absence_and_atomic_preservation(batch,isolated_data,monkeypatch):
 batch,_=install_exact_candidate(batch,isolated_data,monkeypatch)
 f=isolated_data/'manifest.json';f.write_text(json.dumps(batch));path=isolated_data/'cache.db'
 engine=create_engine('sqlite:///'+str(path))
 for model in (KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog):model.__table__.create(engine)
 dry=mat.materialize(path,manifest=f,now=NOW);assert dry['would_insert']==1 and not dry['skipped_invalid']
 done=mat.materialize(path,manifest=f,now=NOW,apply=True,backup=isolated_data/'before.db');assert done['inserted']==1
 with sqlite3.connect(path) as db:
  raw,v=db.execute('select guidance,verification from kimi_route_guidance_cache').fetchone()
  assert json.loads(raw)==batch['routes'][0]['guidance']
  review=json.loads(v)['source_review'];assert len(review['sources'])==3 and review['requires_initial_source_check']
  before=list(db.iterdump())
 with pytest.raises(mat.MaterializationError):mat.materialize(path,manifest=f,now=NOW,apply=True,backup=isolated_data/'never.db')
 assert not (isolated_data/'never.db').exists()
 with sqlite3.connect(path) as db:assert list(db.iterdump())==before
 engine.dispose()

def test_absent_route_converter_binds_all_layers(batch):
 from scripts import prepare_reviewed_social_route as convert
 path=DATA/'reviewed_social_baseline_idn_vnm20260910.json'
 layer=json.loads(path.read_text())['layers'][0]
 manifest=convert.build_manifest(batch,layer);overlay,_,report=convert.convert(manifest,[layer])
 assert report['existing_seed_entries']==1 and report['existing_operator_entries']==0
 assert overlay['entries'][0]['fields']['permitted_stay_days']==14
 for field in convert.BASELINE_FIELDS:
  changed=deepcopy(layer);changed[field]={'changed':True}
  with pytest.raises(convert.CandidateRejected):convert.convert(manifest,[changed])
 assert convert.convert(manifest,[layer])[0]==overlay

@pytest.mark.parametrize('change',['absence_contract','policy_start','disposition_start','unreviewed_end'])
def test_materializer_rejects_missing_or_changed_policy_contract(batch,isolated_data,change):
 e=batch['routes'][0]
 if change=='absence_contract':e.pop('expected_absent')
 elif change=='policy_start':e.pop('policy_valid_from')
 elif change=='disposition_start':e['field_provenance']['disposition'].pop('effective_from')
 else:e['policy_valid_through']='2027-07-15'
 f=isolated_data/'missing-contract.json';f.write_text(json.dumps(batch))
 try:result=mat._manifest(f,DAY)
 except ValueError:return
 assert result[3]

@pytest.mark.parametrize('url',['https://www.instagram.com@evil.example/p/Dab9NF9IILJ/',
 'https://www.instagram.com/p/Dab9NF9IILJ/?unreviewed=1','http://www.instagram.com/p/Dab9NF9IILJ/',
 'https://www.instagram.com:443/p/Dab9NF9IILJ/','https://www.instagram.com/p/Dab9NF9IILJ/#other'])
def test_only_exact_registered_citation_retained(url):
 assert not sa.registered_reference(url)

@pytest.mark.parametrize('change',['wrong_passport','before_effective','empty_fields_altered_stay','no_provenance'])
@pytest.mark.parametrize('operator_released',[False,True])
def test_exact_social_scope_and_start_are_unconditional_shared_holds(batch,monkeypatch,change,operator_released):
 r,g,p,_=parsed(batch)
 if change=='wrong_passport':r['passport_nationality']='MYS'
 elif change=='before_effective':r['arrival_date']='2026-07-14'
 elif change=='empty_fields_altered_stay':p['fields']=[];g['permitted_stay_days']=365
 else:p=None
 monkeypatch.setattr(kp,'hold_enabled',lambda:False)
 out=records_guard.apply_records_hold(r,{'guidance':g,'source_verified':p,'operator_released':operator_released})
 assert out['held'] and out['review_required'] and records_guard.held_envelope(out)['guidance'] is None

@pytest.mark.parametrize('field',['disposition','permitted_stay_days','permitted_stay','exceptions','source_url'])
def test_each_bound_fact_proof_is_mandatory_even_when_field_list_is_empty(batch,field):
 r,g,p,_=parsed(batch);p['fields']=[];p['field_provenance'].pop(field)
 assert not sa.guidance_supported(g,p,r)


def install_exact_candidate(batch,tmp_path,monkeypatch):
 from scripts.prepare_reviewed_social_route import build_manifest,convert
 layer=json.loads((DATA/'reviewed_social_baseline_idn_vnm20260910.json').read_text())['layers'][0]
 vo.OVERRIDES.write_text(json.dumps(layer['seed_entries']))
 manifest=build_manifest(batch,layer);overlay,materialization,_=convert(manifest,[layer])
 path=tmp_path/'reviewed-overlay.json';path.write_text(json.dumps(overlay))
 monkeypatch.setattr(vo,'_reviewed_overlay_paths',lambda:[path]);vo.reload()
 return materialization,path

@pytest.mark.parametrize('change',['missing_contract','seed_changed','operator_changed','overlay_not_last','candidate_changed','alias_present'])
def test_social_apply_rechecks_actual_installed_layers_before_any_write(batch,isolated_data,monkeypatch,change):
 from scripts.prepare_reviewed_social_route import CandidateRejected
 data,overlay_path=install_exact_candidate(batch,isolated_data,monkeypatch)
 raw=deepcopy(data['reviewed_social_cas']['baseline']['seed_entries'][0])
 if change=='missing_contract':data.pop('reviewed_social_cas')
 elif change=='seed_changed':
  raw['note']+=' later changed';vo.OVERRIDES.write_text(json.dumps([raw]))
 elif change=='operator_changed':vo.operator_overrides_path().write_text(json.dumps([raw]))
 elif change=='overlay_not_last':
  overlay=json.loads(overlay_path.read_text());overlay['entries'].append(raw);overlay_path.write_text(json.dumps(overlay))
 elif change=='candidate_changed':
  overlay=json.loads(overlay_path.read_text());overlay['entries'][0]['verified_by']='different reviewer';overlay_path.write_text(json.dumps(overlay))
 vo.reload();f=isolated_data/'cas-manifest.json';f.write_text(json.dumps(data));path=isolated_data/'cas.db'
 engine=create_engine('sqlite:///'+str(path))
 for model in (KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog):model.__table__.create(engine)
 if change=='alias_present':
  with sqlite3.connect(path) as db:
   # A complete old-key row is unnecessary for canonical-key detection.
   db.execute("insert into kimi_route_guidance_cache (id,cache_key,route,status,guidance,missing_fields,contradictions,model,verification,generated_at,fresh_until,created_at,updated_at) values (?,?,?,?,?,?,?,?,?,?,?,?,?)",
    ('legacy','IDN|IDN|VNM|tourism|default|2026-10|v6','{}','uncertain','{}','[]','[]','','{}','2026-09-10','2026-09-10','2026-09-10','2026-09-10'))
 with sqlite3.connect(path) as db:before=list(db.iterdump())
 with pytest.raises((CandidateRejected,mat.MaterializationError)):
  mat.materialize(path,manifest=f,now=NOW,apply=True,backup=isolated_data/'should-not-exist.db')
 assert not (isolated_data/'should-not-exist.db').exists()
 with sqlite3.connect(path) as db:assert list(db.iterdump())==before
 engine.dispose()

def test_changed_source_files_roll_back_social_insertion(batch,isolated_data,monkeypatch):
 from scripts import prepare_reviewed_social_route as prep
 data,_=install_exact_candidate(batch,isolated_data,monkeypatch)
 f=isolated_data/'manifest.json';f.write_text(json.dumps(data));path=isolated_data/'late-change.db'
 engine=create_engine('sqlite:///'+str(path))
 for model in (KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog):model.__table__.create(engine)
 original=prep.assert_materialization_files_unchanged
 def late_change(token):
  vo.operator_overrides_path().write_text('[]')
  original(token)
 monkeypatch.setattr(prep,'assert_materialization_files_unchanged',late_change)
 with pytest.raises(prep.CandidateRejected):mat.materialize(path,manifest=f,now=NOW,apply=True,backup=isolated_data/'before-late.db')
 with sqlite3.connect(path) as db:
  assert db.execute('select count(*) from kimi_route_guidance_cache').fetchone()[0]==0
  assert db.execute('select count(*) from database_change_log').fetchone()[0]==0
 engine.dispose()

@pytest.mark.parametrize('field,value',[('required_documents',['A visa issued by a private agent is mandatory.']),
 ('unpublished_fields',['passport_validity','required_documents']),('passport_validity','6 months after entry')])
def test_unreviewed_extra_claims_or_absence_dispositions_cannot_ride_reviewed_verdict(batch,field,value):
 r,g,p,_=parsed(batch);g[field]=value
 assert records_guard.apply_records_hold(r,{'guidance':g,'source_verified':p,'operator_released':True})['held']

@pytest.mark.parametrize('arrival',['2026-99-99','unparseable',123])
def test_malformed_explicit_arrival_cannot_fall_back_to_today(batch,arrival):
 r,g,p,_=parsed(batch);r['arrival_date']=arrival
 assert records_guard.apply_records_hold(r,{'guidance':g,'source_verified':p,'operator_released':True})['held']

def test_only_normalizer_structural_exemption_fields_may_be_added(batch):
 r,g,p,_=parsed(batch);normalized=kp.validate_answer(g)[0]
 assert not records_guard.apply_records_hold(r,{'guidance':normalized,'source_verified':p}).get('held',False)

@pytest.mark.parametrize('field,value',[('confidence','high'),('visa_products',[{'name':'Unreviewed option','product_type':'visa_exempt'}])])
def test_structural_metadata_cannot_be_changed_by_truncating_proof_list(batch,field,value):
 r,g,p,_=parsed(batch);p['fields']=[];g[field]=value
 assert not sa.guidance_supported(g,p,r)
 assert records_guard.apply_records_hold(r,{'guidance':g,'source_verified':p,'operator_released':True})['held']
