"""Actual scoped coverage evidence and the regressions it exposed."""
from copy import deepcopy
from datetime import date
import importlib.util
import json
import hashlib
from pathlib import Path
import pytest
from app.visa_snapshot import structured_evidence as scopes, verified_overrides as vo

ROOT=Path(__file__).parents[2]
DATA=ROOT/'data/database_seed/reviewed_remaining25_coverage_2026_09_09.json'
MANIFEST=json.loads(DATA.read_text())
SOURCES={s['id']:s for s in MANIFEST['sources']}
spec=importlib.util.spec_from_file_location('remaining_materializer',ROOT/'backend/scripts/materialize_reviewed_routes.py')
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)


def selected(n,d):
    return deepcopy(next(e for e in MANIFEST['routes'] if e['route']['nationality']==n and e['route']['destination']==d))

def internal(e):
    return dict(passport_nationality=e['route']['nationality'],destination_country=e['route']['destination'],travel_purpose=e['route']['travel_purpose'],travel_document_type=e['route']['travel_document_type'])

@pytest.fixture
def current_seed(tmp_path,monkeypatch):
    monkeypatch.setattr(vo,'OVERRIDES',ROOT/'data/database_seed/verified_overrides.json')
    monkeypatch.setenv('ELLIS_OPERATOR_OVERRIDES',str(tmp_path/'empty-operators.json'))
    vo.reload();yield;vo.reload()

@pytest.mark.parametrize('entry',MANIFEST['routes'],ids=lambda e:e['route']['nationality']+'-'+e['route']['destination'])
def test_exact_reviewed_route_and_current_overlay_agree(entry,current_seed):
    if entry['route'] == dict(nationality='VNM', destination='IND', travel_purpose='tourism', travel_document_type='ordinary_passport'):
        assert hashlib.sha256(DATA.read_bytes()).hexdigest() == '79a518e7cdfbb9fe89ef0a47263644d0d78b9bb8a869388670d9e619e476f0d3'
        from scripts.prepare_reviewed_product_patch import digest
        assert digest(entry) == '5f55036f495a80e2ad78c7fb3a09db0c20dccbd412fe4d91b427b36a5fce002d'
        before = deepcopy(entry)
        with pytest.raises(ValueError, match='existing overlay changes a reviewed field'):
            mod._validate_entry(deepcopy(entry),SOURCES,date(2026,9,9))
        assert entry == before
        return
    result=mod._validate_entry(deepcopy(entry),SOURCES,date(2026,9,9))
    assert result['guidance']==entry['guidance']
    assert entry['guidance'].get('confidence') != 'high'

@pytest.mark.parametrize('nat,dest,mutation',[('THA','TWN','diplomatic'),('PHL','TWN','expired'),('KOR','FRA','future_heading'),('IDN','ESP','wrong_list_member'),('HKG','TWN','missing_eligibility'),('VNM','IND','missing_general'),('VNM','IND','missing_exception'),('RUS','KOR','missing_age')])
def test_special_scopes_reject_lost_conditions_or_wrong_applicant(nat,dest,mutation):
    e=selected(nat,dest);p=e['field_provenance']['disposition'];sources=deepcopy(SOURCES);route=internal(e)
    if mutation=='diplomatic':route['travel_document_type']='diplomatic_passport'
    if mutation=='expired':
        p['quote']=p['quote'].replace('2027','2025');p['source_country_section']['rule_quote']=p['quote'];sources[p['source_id']]['text']=sources[p['source_id']]['text'].replace('2027','2025')
    if mutation=='future_heading':
        table=p['source_table']['table_quote'];sources[p['source_id']]['text']=sources[p['source_id']]['text'].replace(table,'Effective from 2027-01-01\n'+table)
    if mutation=='wrong_list_member':route['passport_nationality']='CAN'
    if mutation=='missing_eligibility':p['source_country_section'].pop('eligibility_source_id')
    if mutation=='missing_general':p['source_country_section'].pop('general_rule_source_id')
    if mutation=='missing_exception':p['source_country_section'].pop('exception_quote')
    if mutation=='missing_age':p['source_country_section'].pop('age_quote')
    result=scopes.validate_route_evidence(p,sources[p['source_id']],sources,route,e['guidance']['disposition'],policy_date='2026-09-09')
    assert not result['ok']


def test_calendar_stays_and_india_fees_keep_exact_scope():
    assert selected('ESP','FRA')['guidance']['permitted_stay_days'] is None
    hk=selected('HKG','TWN')['guidance'];assert hk['permitted_stay_days'] is None
    assert [(p['fee']['amount'],p['max_stay_days']) for p in hk['visa_products']]==[(0,30),(600,None),(1000,None),(2000,None)]
    india=selected('VNM','IND')['guidance'];assert india['government_fee'] is None
    assert [p['fee']['amount'] for p in india['visa_products']]==[10,25,40,200]
    assert 'April to June' in india['visa_products'][0]['notes']
    assert 'July to March' in india['visa_products'][1]['notes']
    assert all('calendar year' in p['permitted_stay'] for p in india['visa_products'][2:])


def test_korean_passport_conflict_does_not_erase_verified_visa(current_seed):
    rows=json.loads((ROOT/'data/database_seed/verified_overrides.json').read_text())
    row=next(r for r in rows if r['route'].get('nationality')=='IDN' and r['route'].get('destination')=='KOR' and r['route'].get('travel_purpose')=='tourism' and r['route'].get('travel_document_type','ordinary_passport')=='ordinary_passport')
    f=row['fields'];assert f['passport_validity_requirement'] is None
    assert 'conflicts' in f['passport_validity']
    assert f['disposition']=='VISA_REQUIRED' and f['requirement_detail']=='paper_visa'
    assert 'C-3-9' in json.dumps(f,ensure_ascii=False)
    assert any('Jeju' in x for x in f['exceptions'])
    assert any('group' in x.lower() for x in f['exceptions'])


def test_taiwan_hongkong_validity_uses_supported_arrival_kind(current_seed):
    rows=json.loads((ROOT/'data/database_seed/verified_overrides.json').read_text())
    row=next(r for r in rows if r['route'].get('nationality')=='TWN' and r['route'].get('destination')=='HKG' and r['route'].get('travel_purpose')=='tourism')
    assert row['fields']['passport_validity_requirement']=={'kind':'months_after_arrival','months':6}
    assert 'register' in row['fields']['passport_validity'].lower()


def test_unresolved_vietnam_change_is_not_silently_materialized():
    assert len(MANIFEST['routes'])==25
    assert not any(e['route']['nationality']=='IDN' and e['route']['destination']=='VNM' for e in MANIFEST['routes'])


def test_current_india_border_form_overlay_is_exact_rebuilt_supersession(current_seed):
    from scripts import convert_reviewed_india_entry as current
    from scripts.prepare_reviewed_product_patch import digest
    seed_root=ROOT/'data/database_seed'
    manifest_path=seed_root/'reviewed_india_entry_manifest_20260910.json'
    overlay_path=seed_root/'reviewed_india_entry_overlay_20260910.json'
    assert hashlib.sha256(manifest_path.read_bytes()).hexdigest() == '8cd9c606c50264f6d305856f85a30d7cd7a714626a10f4923f4107097dfc9617'
    assert hashlib.sha256(overlay_path.read_bytes()).hexdigest() == '11744d6a1fd007556695d0f9809ca505226b8d1149dcf54e119b251327224006'
    manifest=json.loads(manifest_path.read_text());overlay=json.loads(overlay_path.read_text())
    layers=[dict(deepcopy(row['baseline']),cache_key=row['cache_key']) for row in manifest['routes']]
    report=current.verify_prepared(manifest['specification'],layers,manifest,overlay)
    reviewed=next(row for row in report['routes'] if row['cache_key']=='VNM|VNM|IND|tourism|default|unknown|v6')
    baseline=next(row for row in layers if row['cache_key']==reviewed['cache_key'])
    actual,provenance=vo.apply(deepcopy(baseline['raw_guidance']),baseline['route'])
    assert digest(actual)==digest(reviewed['guidance'])
    assert digest(provenance)==digest(reviewed['source_provenance'])
    assert actual['arrival_card']['required'] is True and '72 hours' in actual['arrival_card']['submission_window']
    assert current.CARD in str(actual['entry_requirements']) and current.DECL in str(actual['entry_requirements'])
    assert provenance['field_provenance']['arrival_card']['status']=='reviewed'
    assert provenance['field_provenance']['entry_requirements']['status']=='partial'
    assert provenance['field_provenance']['entry_requirements']['verified_elements']==[current.CARD,current.DECL]
    for product in actual['visa_products']:
        assert current.CARD in str(product['entry_requirements']) and current.DECL in str(product['entry_requirements'])
        assert product['field_provenance']['entry_requirements']['status']=='partial'
    assert not report['raw_writes'] and not report['operator_writes'] and not report['issue_changes']
    assert not report['renew_fresh_until'] and not report['confidence_changed']
