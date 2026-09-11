from copy import deepcopy
import json
from pathlib import Path
import pytest
from app.visa_snapshot import verified_overrides as vo, tstation
from scripts.convert_reviewed_product_patch import convert_manifest
from scripts.prepare_reviewed_product_patch import PatchRejected

@pytest.fixture(scope='module')
def manifest():
 return json.loads((Path(__file__).resolve().parents[2]/'data/database_seed/reviewed_schengen_products_2026_09_09.json').read_text())
def layers(m):
 return {e['cache_key']:{'raw_guidance':deepcopy(e['baseline']['raw_guidance']),'merged_guidance':deepcopy(e['baseline']['effective_guidance']),'current_override_entries':[deepcopy(x['entry']) for x in e['baseline']['override_entries']]} for e in m['routes']}
@pytest.fixture(scope='module')
def converted(manifest): return convert_manifest(manifest,layers(manifest))
def output(m,c,nat,dest='FRA',purpose='tourism'):
 seed=next(e for e in c['entries'] if (e['route']['nationality'],e['route']['destination'],e['route']['travel_purpose'])==(nat,dest,purpose))
 old=next(e for e in m['routes'] if (e['route']['passport_nationality'],e['route']['destination_country'],e['route']['travel_purpose'])==(nat,dest,purpose))
 parsed=vo._parse_rows([seed],{})[vo._key(nat,dest,purpose,'ordinary_passport')]
 g,_=vo.merge_verified_fields(old['baseline']['raw_guidance'],parsed['fields'],source_url=seed['source_url'])
 prov=dict(parsed['field_provenance']['disposition'],fields=list(parsed['fields']),field_provenance=parsed['field_provenance'])
 return seed,old,g,prov

def test_actual16entries_load_independent_product_verdicts_without_work_publication(manifest,converted):
 assert len(converted['entries'])==16 and len(converted['blocked'])==1
 for seed in converted['entries']:
  assert seed['route']['travel_purpose']!='work'
  _,old,g,prov=output(manifest,converted,*[seed['route'][k] for k in ['nationality','destination','travel_purpose']])
  rows=tstation.records_for_route(old['route'],g,prov)
  assert [p['type'] for p in g.get('visa_products',[])]==old['retained_original_products']
  if g.get('visa_products'):
   assert all(tstation.verdict_provenance_supported(r['_product_source_verified']) for r in rows)
   assert all(r['_product_source_verified']['fields']==['disposition'] for r in rows)
 assert all(not p['new_grounded_check'] and not p['new_release'] and not p['renew_fresh_until'] for p in converted['preflight'])


def test_eu_citizenship_companion_does_not_replace_french_rule_headline(manifest, converted):
 seed, _, _, prov = output(manifest, converted, 'DEU')
 french = 'https://www.service-public.gouv.fr/particuliers/vosdroits/F13512?lang=en'
 german_membership = 'https://european-union.europa.eu/principles-countries-history/eu-countries/germany_en'
 assert seed['source_url'] == french
 for field in ('disposition', 'requirement_detail', 'source_url'):
  proof = seed['field_provenance'][field]
  assert proof['source_url'] == french
  assert proof['quote'] == 'You can enter and be present for up to 3 months in France without special formalities.'
  assert any(item['source_url'] == german_membership and item['quote'] == 'EU Member State: since 1 January 1958'
             for item in proof['supporting_evidence'])
 assert prov['source_url'] == french


def test_all_shipped_reviewed_stores_pass_the_actual_registered_loader():
 # Intentionally use the production path resolution and full table reader.
 # Individual _parse_rows tests miss file-level authority/schema rejection,
 # which holds every route when even one active overlay entry is invalid.
 seed = Path(__file__).resolve().parents[2] / 'data/database_seed'
 assert vo.OVERRIDES == seed / 'verified_overrides.json'
 assert all(path.is_file() for path in vo._reviewed_overlay_paths())
 vo.reload()
 try:
  table = vo._table()
  assert table.store_errors == ()
  for path in vo._reviewed_overlay_paths():
   overlay = json.loads(path.read_text())
   for entry in overlay['entries']:
    route = entry['route']
    key = vo._key(route['nationality'], route['destination'], route['travel_purpose'], route.get('travel_document_type'))
    assert key in table
  assert table[vo._key('DEU', 'FRA', 'tourism')]['source_url'] == 'https://www.service-public.gouv.fr/particuliers/vosdroits/F13512?lang=en'
 finally:
  vo.reload()

def test_unknown_clears_do_not_inherit_entry_source_or_date(manifest,converted):
 _,_,g,p=output(manifest,converted,'CHN',purpose='study')
 for field in ['government_fee','processing_time']:
  proof=p['field_provenance'][field]
  assert g[field] is None and proof['status']=='unknown'
  assert not proof['source_url'] and proof['verified_at'] is None
 assert g['visa_products'][1]['field_provenance']['fee']['status']=='unknown'
 _,_,_,p=output(manifest,converted,'CHN','ESP')
 assert p['field_provenance']['official_portal_url']['status']=='unknown'

def test_partial_list_does_not_certify_untouched_exceptions(manifest,converted):
 _,_,_,p=output(manifest,converted,'CHN','ESP')
 proof=p['field_provenance']['exceptions']
 assert proof['status']=='partial' and proof['verification_scope']=='changed_elements_only'
 assert proof['verified_elements'] and proof['retained_unverified_elements']

def test_untouched_override_fields_retain_authorship_without_raw_blanket_verification(manifest,converted):
 for seed in converted['entries']:
  _,old,_,_=output(manifest,converted,*[seed['route'][k] for k in ['nationality','destination','travel_purpose']])
  key=vo._key(seed['route']['nationality'],seed['route']['destination'],seed['route']['travel_purpose'],'ordinary_passport')
  prior=vo._parse_rows([x['entry'] for x in old['baseline']['override_entries']],{}).get(key,{})
  for field,proof in prior.get('field_provenance',{}).items():
   if field not in old['fields'] and field not in {'visa_products','disposition','requirement_detail'}:assert seed['field_provenance'][field]==proof
  for field in set(old['baseline']['raw_guidance'])-set(prior.get('fields',{}))-set(old['fields'])-{'visa_products','disposition','requirement_detail'}:
   assert field not in seed['fields']

@pytest.mark.parametrize('fault',['product','nationality','document','purpose','unknown','foreign_source'])
def test_invalid_explicit_product_review_cannot_fallback_to_valid_parent(manifest,converted,fault):
 _,old,g,prov=output(manifest,converted,'IDN'); proof=g['visa_products'][-1]['field_provenance']['disposition']
 if fault=='product':proof['subject']['product_type']='Other adult visa'
 elif fault=='nationality':proof['subject']['passport_nationality']='CHN'
 elif fault=='document':proof['subject']['travel_document_type']='diplomatic_passport'
 elif fault=='purpose':proof['subject']['travel_purpose']='work'
 elif fault=='unknown':proof['status']='unknown'
 else:proof['source_url']='https://eviza.mae.ro/TypeOfVisa'
 rows=tstation.records_for_route(old['route'],g,prov)
 assert rows[-1]['_product_source_verified'] is None
 assert rows[-1]['confidence_level']=='Medium' and rows[-1]['_evidence_low'] is True and rows[-1]['visa_requirement']=='Visa Required in Advance'  # an unread official link reads Medium and stays held
 assert tstation.verdict_provenance_supported(rows[0]['_product_source_verified'])

def test_existing_dispute_still_holds_independently_reviewed_child(manifest,converted):
 _,old,g,prov=output(manifest,converted,'IDN')
 rows=tstation.records_for_route(old['route'],g,prov,disputed_fields=['passport_validity'])
 assert rows[-1]['visa_fee_amount']==0 and rows[-1]['visa_requirement']=='Visa Required in Advance'
 assert rows[-1]['confidence_level']=='Low'

def test_current_layer_change_rejects_conversion_without_mutation(manifest):
 current=layers(manifest); first=manifest['routes'][0]['cache_key']
 current[first]['raw_guidance']['government_fee']={'amount':120,'currency':'EUR'}; before=deepcopy(current)
 with pytest.raises(PatchRejected,match='Raw guidance changed'):convert_manifest(manifest,current)
 assert current==before

@pytest.mark.parametrize('validity',[None,'Set by the consulate','As granted','Trip duration','90 days in any180 days'])
def test_unknown_or_discretionary_validity_is_not_inferred_from_name_or_stay(validity):
 route={'passport_nationality':'SEN','destination_country':'FRA','travel_purpose':'tourism'}
 g={'disposition':'VISA_REQUIRED','requirement_detail':'paper_visa','visa_products':[{'type':'5-year multiple-entry tourist','validity':validity,'max_stay_days':90,'fee':{'amount':90,'currency':'EUR'}}]}
 row=tstation.records_for_route(route,g)[0]
 assert row['validity_duration'] is None and row['validity_unit'] is None
 assert row['max_stay_duration']==90

def test_explicit_electronic_product_application_unknown_never_infers_online_filing():
 route={'passport_nationality':'CHN','destination_country':'AUS','travel_purpose':'tourism'}
 g={'disposition':'VISA_REQUIRED','requirement_detail':'paper_visa','application_channel':'online_portal','visa_products':[{'type':'Visitor600 Frequent Traveller','requirement_detail':'evisa','application_channel':None,'application_channel_detail':None,'fee':None}]}
 assert tstation.records_for_route(route,g)[0]['application_method'] is None
