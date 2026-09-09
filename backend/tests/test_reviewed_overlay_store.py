from copy import deepcopy
import json
from pathlib import Path
import pytest
from app.visa_snapshot import verified_overrides as vo, kimi_primary as kp, scheduled_policies

URL='https://evisa.gov.vn/'
ROUTE={'passport_nationality':'HKG','destination_country':'VNM','travel_purpose':'tourism','travel_document_type':'ordinary_passport'}
BASE={'disposition':'VISA_REQUIRED','requirement_detail':'evisa','permitted_stay':'90 days','arrival_card':{'required':True,'name':'A retained independent card'},'unrelated_marker':{'keep':True}}

def entry(stay):
 return {'route':{'nationality':'HKG','destination':'VNM','travel_purpose':'tourism','travel_document_type':'ordinary_passport'},'source_url':URL,'verified_at':'2026-09-09','verifier':'ai','verified_by':'AI source review','note':'Exact HKSAR ordinary tourism source review','fields':{'disposition':'VISA_REQUIRED','requirement_detail':'evisa','permitted_stay':stay}}

@pytest.fixture
def stores(tmp_path,monkeypatch):
 core=tmp_path/'verified_overrides.json'; core.write_text('[]')
 operator=tmp_path/'operator.json'
 monkeypatch.setattr(vo,'OVERRIDES',core)
 monkeypatch.setattr(vo,'operator_overrides_path',lambda:operator)
 monkeypatch.setattr(scheduled_policies,'apply',lambda g,p,r:(dict(g),p))
 vo.reload()
 yield core,operator,tmp_path/vo.REVIEWED_OVERLAY_NAMES[0]
 vo.reload()

def write_overlay(path,rows):
 path.write_text(json.dumps({'schema_version':1,'kind':'reviewed_overlay_conversion','entries':rows}))

@pytest.mark.parametrize('kind',['core_missing','core_corrupt','core_wrong_shape','operator_corrupt','operator_wrong_shape','reviewed_corrupt','reviewed_wrong_shape','reviewed_wrong_authority'])
def test_missing_required_or_present_invalid_store_holds_and_preserves_facts(stores,kind):
 core,operator,reviewed=stores
 if kind=='core_missing':core.unlink()
 elif kind=='core_corrupt':core.write_text('{broken')
 elif kind=='core_wrong_shape':core.write_text('{}')
 elif kind=='operator_corrupt':operator.write_text('{broken')
 elif kind=='operator_wrong_shape':operator.write_text('{}')
 elif kind=='reviewed_corrupt':reviewed.write_text('{broken')
 elif kind=='reviewed_wrong_shape':reviewed.write_text('[]')
 else:
  bad=entry('100 days');bad['source_url']='https://eviza.mae.ro/TypeOfVisa';write_overlay(reviewed,[bad])
 before=deepcopy(BASE)
 g,_=vo.apply(BASE,ROUTE)
 assert g['source_verification_store_unavailable']['component']=='verified_overrides'
 assert kp.serve_time_invariants(g)
 assert g['permitted_stay']==before['permitted_stay']
 assert g['arrival_card']==before['arrival_card'] and g['unrelated_marker']==before['unrelated_marker']
 assert BASE==before
 assert str(core.parent) not in json.dumps(g['source_verification_store_unavailable'])

def test_missing_optional_stores_are_harmless(stores):
 g,_=vo.apply(BASE,ROUTE)
 assert 'source_verification_store_unavailable' not in g

def test_overlay_between_core_and_operator_and_file_changes_reload(stores):
 core,operator,reviewed=stores
 core.write_text(json.dumps([entry('10 days')]))
 write_overlay(reviewed,[entry('20 days')])
 assert vo.apply(BASE,ROUTE)[0]['permitted_stay']=='20 days'
 operator.write_text(json.dumps([entry('30 days')]))
 assert vo.apply(BASE,ROUTE)[0]['permitted_stay']=='30 days'
 operator.unlink()
 write_overlay(reviewed,[entry('40 days')])
 assert vo.apply(BASE,ROUTE)[0]['permitted_stay']=='40 days'

def test_repair_clears_only_own_unavailable_marker(stores):
 _,_,reviewed=stores
 reviewed.write_text('{bad')
 failed,_=vo.apply(BASE,ROUTE)
 assert failed['source_verification_store_unavailable']
 failed['some_other_hold']=['retain']
 write_overlay(reviewed,[entry('20 days')])
 repaired,_=vo.apply(failed,ROUTE)
 assert 'source_verification_store_unavailable' not in repaired
 assert repaired['permitted_stay']=='20 days'
 assert repaired['some_other_hold']==['retain'] and repaired['arrival_card']==BASE['arrival_card']

def test_corrupt_operator_file_cannot_be_overwritten_by_a_new_edit(stores):
 _,operator,_=stores;operator.write_text('{bad')
 before=operator.read_bytes()
 with pytest.raises(ValueError,match='store is unavailable'):
  vo.append_operator_entry(entry('30 days'),guidance=BASE)
 assert operator.read_bytes()==before

@pytest.mark.parametrize('store',['core','operator','reviewed'])
def test_unreadable_present_store_is_not_treated_as_an_absent_optional_file(stores,monkeypatch,store):
 core,operator,reviewed=stores
 target={'core':core,'operator':operator,'reviewed':reviewed}[store]
 target.write_text('[]')
 original=Path.stat
 def denied(path,*args,**kwargs):
  if path==target:raise PermissionError('private path must not reach output')
  return original(path,*args,**kwargs)
 monkeypatch.setattr(Path,'stat',denied)
 g,_=vo.apply(BASE,ROUTE)
 assert g['source_verification_store_unavailable']
 assert 'private path' not in json.dumps(g['source_verification_store_unavailable'])

def test_invalid_nested_record_shape_holds_instead_of_crashing(stores):
 core,_,_=stores
 core.write_text(json.dumps([{'route':{'nationality':'HKG','destination':'VNM'},'fields':['invalid']}]))
 g,_=vo.apply(BASE,ROUTE)
 assert g['source_verification_store_unavailable']['stores']==['core_seed']


def test_failed_snapshot_stays_held_after_concurrent_repair_reload(stores, monkeypatch):
 """A second response cannot clear the status of the first selected table."""
 from concurrent.futures import ThreadPoolExecutor
 from threading import Event, current_thread
 _, _, reviewed = stores
 reviewed.write_text('{bad')
 selected, repaired = Event(), Event()
 real_find = vo.find
 def paused_find(route):
  result = real_find(route)
  if current_thread().name.startswith('failed-snapshot'):
   selected.set()
   assert repaired.wait(3)
  return result
 monkeypatch.setattr(vo, 'find', paused_find)
 with ThreadPoolExecutor(max_workers=1, thread_name_prefix='failed-snapshot') as pool:
  first = pool.submit(vo.apply, BASE, ROUTE)
  try:
   assert selected.wait(3)
   write_overlay(reviewed, [entry('20 days')])
   second, _ = vo.apply(BASE, ROUTE)
   assert 'source_verification_store_unavailable' not in second
   assert second['permitted_stay'] == '20 days'
  finally:
   repaired.set()
  failed, proof = first.result(timeout=3)
 assert failed['source_verification_store_unavailable']['stores'] == ['reviewed_overlay']
 assert failed['permitted_stay'] == BASE['permitted_stay']
 assert proof is None
 # A later call in the original caller's context also reads the repaired table.
 assert 'source_verification_store_unavailable' not in vo.apply(BASE, ROUTE)[0]


def test_store_failure_precedes_any_merge_normalization_or_finalization(stores, monkeypatch):
 core, _, reviewed = stores
 exempt = entry('30 days')
 exempt['fields'] = {'disposition': 'VISA_EXEMPT', 'requirement_detail': 'visa_free'}
 core.write_text(json.dumps([exempt]))
 reviewed.write_text('{bad')
 original = dict(BASE, government_fee={'amount': 25, 'currency': 'USD'},
                 visa_products=[{'type': 'Tourist eVisa', 'fee': {'amount': 25, 'currency': 'USD'}}],
                 required_documents='Legacy text retained for review')
 before = deepcopy(original)
 # Parsing the seed itself normalizes verified fields; preload that snapshot
 # before guarding the request merge path against any claim mutation.
 vo._table()
 def forbidden(*args, **kwargs):
  pytest.fail('incomplete store must hold before changing cached claims')
 monkeypatch.setattr(vo, '_normalise_legacy_shapes', forbidden)
 monkeypatch.setattr(vo, 'merge_verified_fields', forbidden)
 monkeypatch.setattr(vo, '_finalize_guidance', forbidden)
 held, proof = vo.apply(original, ROUTE)
 assert held.pop('source_verification_store_unavailable')['stores'] == ['reviewed_overlay']
 assert held == before and original == before
 assert proof is None


@pytest.mark.parametrize('prior', [
 {'component': 'other_evidence_catalog', 'reason': 'not_available'},
 ['independent-source-store-hold'],
 True,
])
def test_independent_store_marker_survives_failure_retries_and_recovery(stores, prior):
 _, _, reviewed = stores
 before = deepcopy(prior)
 original = dict(BASE, source_verification_store_unavailable=prior)
 # A healthy loader may not consume a different component's hold.
 healthy, proof = vo.apply(original, ROUTE)
 assert healthy == original and proof is None
 reviewed.write_text('{bad')
 failed, _ = vo.apply(original, ROUTE)
 marker = failed['source_verification_store_unavailable']
 assert marker['component'] == 'verified_overrides'
 assert marker['prior_unavailable'] == before
 retried, _ = vo.apply(failed, ROUTE)
 assert retried['source_verification_store_unavailable'] == marker
 write_overlay(reviewed, [entry('20 days')])
 recovered, proof = vo.apply(retried, ROUTE)
 assert recovered == original
 assert recovered['source_verification_store_unavailable'] == before
 assert prior == before and proof is None


def test_loaded_table_error_tuple_survives_later_load(stores):
 _, _, reviewed = stores
 reviewed.write_text('{bad')
 failed = vo._load_table()
 assert failed.store_errors == ('reviewed_overlay',)
 write_overlay(reviewed, [])
 repaired = vo._load_table()
 assert repaired.store_errors == ()
 assert failed.store_errors == ('reviewed_overlay',)
 with pytest.raises(AttributeError):
  failed.store_errors = ()


def test_operator_append_uses_its_loaded_snapshot_even_after_another_load(stores, monkeypatch):
 _, operator, _ = stores
 operator.write_text('{bad')
 original_load = vo._load_table
 failed = original_load()
 operator.write_text('[]')
 repaired = original_load()
 assert repaired.store_errors == ()
 monkeypatch.setattr(vo, '_load_table', lambda: failed)
 before = operator.read_bytes()
 with pytest.raises(ValueError, match='store is unavailable'):
  vo.append_operator_entry(entry('30 days'), guidance=BASE)
 assert operator.read_bytes() == before
