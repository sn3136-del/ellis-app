"""Actual two-source distinction, fail-closed scope and SQLite consumers."""
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
import json
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.models import Base
from app.visa_snapshot import fetching,freshness,verified_overrides as vo,reviewed_stay_concepts as guard
from app.visa_snapshot.models import DatabaseIssueReport,KimiRouteGuidanceCache
from app.visa_snapshot.fetching import FetchResult
X=json.loads((Path(__file__).parent/'fixtures/hkg_sgp_reviewed_stay_concepts.json').read_text());DAY='2026-09-10'
def arguments():
 return dict(quoted={'permitted_stay_days':30},evidence={'permitted_stay_days':guard.EXEMPTION_QUOTE},source_url=guard.EXEMPTION_URL,captures={u:{'url':u,'text':q,'checked_at':DAY} for u,q in ((guard.EXEMPTION_URL,guard.EXEMPTION_QUOTE),(guard.INDIVIDUAL_URL,guard.INDIVIDUAL_QUOTE))},guidance=deepcopy(X['merged_guidance']),fields=deepcopy(X['source_provenance']['field_provenance']),route=deepcopy(X['route']),day=DAY)
def test_exact_current_rule_preserved_without_mutation_or_credit():
 a=arguments();before=deepcopy(a);out=guard.rejected_stay_fields(**a);assert a==before
 assert out['permitted_stay_days']['retained_exemption_limit_days']==30
 assert out['permitted_stay_days']['individual_grant'] is None and out['permitted_stay_days']['verification_credit'] is False
@pytest.mark.parametrize('path,value',[
 (('quoted','permitted_stay_days'),14),(('quoted','permitted_stay_days'),True),(('quoted','permitted_stay_days'),30.0),
 (('guidance','permitted_stay_days'),90),(('guidance','permitted_stay'),'Follow the e-Pass.'),
 (('guidance','disposition'),'VISA_REQUIRED'),(('guidance','requirement_detail'),'conditional_visa_free'),
 (('route','passport_nationality'),'USA'),(('route','lawful_country_of_residence'),'USA'),(('route','destination_country'),'MYS'),
 (('route','travel_purpose'),'business'),(('route','travel_document_type'),'certificate_of_identity'),(('route','arrival_date'),'2026-09-12'),
 (('fields','permitted_stay_days'),{}),(('fields','disposition'),{}),(('fields','permitted_stay'),{}),
 (('fields',),'unexpected'),(('fields','permitted_stay_days'),'unexpected'),(('source_url',),'https://www.ica.gov.sg/other'),
 (('evidence','permitted_stay_days'),guard.EXEMPTION_QUOTE+' This rule was withdrawn.'),
 (('evidence','permitted_stay_days'),guard.EXEMPTION_QUOTE.replace('30','14')),
 (('captures',guard.INDIVIDUAL_URL,'checked_at'),'2026-09-09'),(('captures',guard.EXEMPTION_URL,'checked_at'),'2026-09-09'),
 (('captures',guard.INDIVIDUAL_URL,'text'),'All visitors automatically receive 90 days.'),
 (('captures',guard.EXEMPTION_URL,'text'),guard.EXEMPTION_QUOTE.replace('30','14')),
 (('captures',guard.INDIVIDUAL_URL,'url'),'https://www.ica.gov.sg/other'),(('captures',guard.INDIVIDUAL_URL),'unexpected'),
 (('day',),'today'),(('day',),'2026-09-09'),(('day',),'20260910')])
def test_changes_missing_proofs_or_scope_do_not_get_exception(path,value):
 a=arguments();node=a
 for k in path[:-1]:node=node[k]
 node[path[-1]]=value;assert guard.rejected_stay_fields(**a)=={}
@pytest.mark.parametrize('container,key',[('guidance','permitted_stay_days'),('route','lawful_country_of_residence'),('fields','permitted_stay_days'),('captures',guard.INDIVIDUAL_URL),('captures',guard.EXEMPTION_URL)])
def test_absent_data_never_get_exception(container,key):
 a=arguments();a[container].pop(key);assert guard.rejected_stay_fields(**a)=={}
@pytest.fixture
def cached(monkeypatch,tmp_path):
 engine=create_engine('sqlite://');Base.metadata.create_all(engine);db=Session(engine)
 base=tmp_path/'base.json';overlay=tmp_path/'reviewed.json';op=tmp_path/'ops.json'
 base.write_text(json.dumps(X['seed_entries'][:1]));op.write_text('[]');overlay.write_text(json.dumps({'schema_version':1,'kind':'reviewed_overlay_conversion','entries':X['seed_entries'][1:]}))
 monkeypatch.setattr(vo,'OVERRIDES',base);monkeypatch.setattr(vo,'_reviewed_overlay_paths',lambda:[overlay]);monkeypatch.setattr(vo,'operator_overrides_path',lambda:op);vo.reload()
 row=KimiRouteGuidanceCache(cache_key=X['cache_key'],route=deepcopy(X['route']),guidance=deepcopy(X['raw_guidance']),status='KIMI_PRIMARY',fresh_until=datetime(2020,1,1));db.add(row);db.commit()
 monkeypatch.setattr(freshness,'candidate_sources',lambda *a,**k:[guard.EXEMPTION_URL,guard.INDIVIDUAL_URL]);monkeypatch.setattr(freshness,'_now',lambda:datetime(2026,9,10,12,0,tzinfo=timezone.utc))
 yield db,row,overlay
 fetching.set_fetcher(None);freshness.set_provider(None);vo.reload();db.close();engine.dispose()
def sources(limit=30,missing_ica=False,route_support=False):
 quote=guard.EXEMPTION_QUOTE.replace('30',str(limit))
 def fetch(url,**kw):
  missing=missing_ica and url==guard.INDIVIDUAL_URL;text=quote if url==guard.EXEMPTION_URL else guard.INDIVIDUAL_QUOTE
  if route_support and url==guard.EXEMPTION_URL:text+='\nHong Kong passport holders are visa-exempt for tourism in Singapore.'
  return FetchResult(requested_url=url,final_url=url,final_hostname=url.split('/')[2],ok=not missing,http_status=503 if missing else 200,content_text=None if missing else text,content_hash='current-stay-'+str(limit),retrieved_at=DAY)
 fetching.set_fetcher(fetch)
 def provider(system,payload):
  d=json.loads(payload);mfa=d['official_page_url']==guard.EXEMPTION_URL;assert 'individual admission period' in system
  return {'page_relevant':mfa,'page_is_nationality_specific':mfa,'consistent':not mfa,'corrected_fields':{'permitted_stay_days':limit} if mfa else {},'evidence':{'permitted_stay_days':quote,'disposition':quote} if mfa else {'permitted_stay_days':guard.INDIVIDUAL_QUOTE}}
 freshness.set_provider(provider)
def test_scheduled_no_new_issue_facts_grade_ttl_or_comparison_credit(cached):
 db,row,_=cached;sources();before=deepcopy(row.guidance);out=freshness.recheck_row(db,row)
 assert out['outcome']=='validation_error' and out['changed']==[] and out['disputed']==[]
 assert row.guidance==before and row.status=='KIMI_PRIMARY' and row.fresh_until==datetime(2020,1,1)
 assert db.query(DatabaseIssueReport).count()==0
 gc=row.verification['grounded_check'];assert not gc.get('renewed')
 assert 'stay concept mismatch: permitted_stay_days' in gc['validation_errors']
 c=next(c for c in gc['source_checks'] if c['source_url']==guard.EXEMPTION_URL)
 assert 'permitted_stay_days' not in c['verified_fields'] and c['proposed_fields']=={}
 assert c['rejected_stay_concept_fields']['permitted_stay_days']['verification_credit'] is False
 assert 'last_good_check' not in row.verification and not (row.verification.get('comparison_cache') or {}).get('entries')
@pytest.mark.parametrize('case',['changed_limit','missing_ica','missing_proof','missing_threshold'])
def test_real_change_or_insufficient_evidence_keeps_protected_dispute(cached,case):
 db,row,overlay=cached
 if case in ('missing_proof','missing_threshold'):
  data=json.loads(overlay.read_text())
  if case=='missing_proof':data['entries'][0]['field_provenance'].pop('permitted_stay_days')
  else:data['entries'][0]['fields']['permitted_stay']='Actual stay is on the e-Pass.'
  overlay.write_text(json.dumps(data));vo.reload()
 sources(limit=14 if case=='changed_limit' else 30,missing_ica=case=='missing_ica');before=deepcopy(row.guidance);out=freshness.recheck_row(db,row)
 assert out['changed']==[] and out['disputed']==['permitted_stay_days']
 issue=db.query(DatabaseIssueReport).one();assert issue.status=='open'
 assert issue.proposal['fields']['permitted_stay_days']['page_says']==(14 if case=='changed_limit' else 30)
 assert row.guidance==before and row.fresh_until==datetime(2020,1,1)
def test_existing_monitor_issue_and_last_good_are_not_rewritten(cached):
 db,row,_=cached;original={'source_url':guard.EXEMPTION_URL,'fields':{'permitted_stay_days':{'page_says':30,'record_holds':None,'quote':guard.EXEMPTION_QUOTE}}}
 issue=DatabaseIssueReport(org_id='platform',cache_key=row.cache_key,route=row.route,field='permitted_stay_days',note='Original disputed scalar',reported_by='freshness_monitor',status='open',proposal=deepcopy(original))
 prior={'outcome':'checked','evidence_contract':freshness.EVIDENCE_CONTRACT,'at':'2020-01-01','renewed':True,'verified_fields':['disposition']};row.verification={'grounded_check':deepcopy(prior),'last_good_check':deepcopy(prior)};db.add(issue);db.commit()
 sources();freshness.recheck_row(db,row)
 assert issue.status=='open' and issue.proposal==original and not issue.resolved_at
 assert db.query(DatabaseIssueReport).count()==1 and row.verification['last_good_check']==prior and row.fresh_until==datetime(2020,1,1)
def test_manual_check_preserves_original_issue_and_has_no_credit(cached):
 db,row,_=cached;original={'fields':{'permitted_stay_days':{'page_says':30,'record_holds':None}}}
 issue=DatabaseIssueReport(org_id='tripcom',cache_key=row.cache_key,route=row.route,field='permitted_stay_days',note='Original reader concern',reported_by='reviewer',status='open',proposal=deepcopy(original));db.add(issue);db.commit();sources();before=deepcopy(row.guidance)
 out=freshness.propose_for_issue(db,issue.id)
 assert out['outcome']=='validation_error' and out['issue_unchanged'] and out['fields']=={} and out['verified_fields']==[]
 assert out['rejected_stay_concept_fields'];db.refresh(issue)
 assert issue.status=='open' and issue.proposal==original and not issue.resolved_at and row.guidance==before and row.fresh_until==datetime(2020,1,1)
def test_manual_changed_threshold_still_proposes(cached):
 db,row,_=cached;issue=DatabaseIssueReport(org_id='tripcom',cache_key=row.cache_key,route=row.route,field='permitted_stay_days',note='Reader concern',reported_by='reviewer',status='open');db.add(issue);db.commit();sources(limit=14,route_support=True)
 out=freshness.propose_for_issue(db,issue.id);assert out['fields']['permitted_stay_days']['page_says']==14 and issue.status=='open' and row.fresh_until==datetime(2020,1,1)

def test_route_supported_comparison_still_cannot_renew_or_cache_extraction(cached):
 db,row,_=cached;sources(route_support=True);out=freshness.recheck_row(db,row)
 gc=row.verification['grounded_check']
 assert out['outcome']=='validation_error' and gc['renewed'] is False
 assert gc['source_checks'][0]['route_supported'] is True
 assert row.fresh_until==datetime(2020,1,1) and db.query(DatabaseIssueReport).count()==0
 assert 'permitted_stay_days' not in gc['verified_fields']
 assert not (row.verification.get('comparison_cache') or {}).get('entries')
