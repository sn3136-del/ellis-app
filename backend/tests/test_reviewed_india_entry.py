from copy import deepcopy
from pathlib import Path
import json,hashlib
from unittest.mock import patch
import pytest
from scripts import convert_reviewed_india_entry as c
from scripts.prepare_reviewed_product_patch import PatchRejected,digest
from app.visa_snapshot import verified_overrides as vo,tstation,records_guard
DATA=Path(__file__).resolve().parents[2]/'data/database_seed'
@pytest.fixture
def inputs():
 m=json.loads((DATA/'reviewed_india_entry_manifest_20260910.json').read_text())
 return m['specification'],[dict(deepcopy(r['baseline']),cache_key=r['cache_key']) for r in m['routes']]
def run(spec,layers):return c.convert(c.build_manifest(spec,layers),layers)

def test_all_actual_products_keep_visa_terms_proofs_dates_and_grades(inputs):
 spec,layers=inputs;before=deepcopy(layers);o,r=run(*inputs)
 assert layers==before and len(r['routes'])==17 and sum(len(x['records']) for x in r['routes'])==75
 for x,l in zip(sorted(r['routes'],key=lambda x:x['cache_key']),sorted(layers,key=lambda x:x['cache_key'])):
  old=l['merged_guidance'];new=x['guidance'];prov=x['source_provenance'];oldprov=l['source_provenance']
  assert new['arrival_card']==c.ARRIVAL
  for key in set(old)|set(new):
   if key not in {'arrival_card','entry_requirements','visa_products'}:assert old.get(key)==new.get(key),key
  for key in set(oldprov)|set(prov):
   if key not in {'field_provenance','fields'}:assert oldprov.get(key)==prov.get(key),key
  assert set(prov['fields'])==set(oldprov['fields'])|{'arrival_card','entry_requirements','visa_products'}
  for key,value in oldprov.get('field_provenance',{}).items():
   if key not in {'arrival_card','entry_requirements'}:assert prov['field_provenance'][key]==value,key
  ep=prov['field_provenance']['entry_requirements'];assert ep['status']=='partial' and ep['verified_elements']==[c.CARD,c.DECL]
  for p,q in zip(old.get('visa_products') or [],new.get('visa_products') or []):
   assert q['entry_requirements']==c.append_entry(p.get('entry_requirements'))
   assert q['field_provenance']['entry_requirements']['status']=='partial'
   for f in set(p)|set(q):
    if f not in {'entry_requirements','field_provenance'}:assert p.get(f)==q.get(f),(p['type'],f)
   for f,v in p.get('field_provenance',{}).items():
    if f!='entry_requirements':assert q['field_provenance'][f]==v
  oldrows=tstation.records_for_route(l['route'],old,oldprov)
  assert len(oldrows)==len(x['records'])
  for a,b in zip(oldrows,x['records']):
   for f in ('visa_type_name','visa_requirement','visa_requirement_detail','visa_fee_amount','visa_fee_currency','visa_fee_qualifier','validity_duration','validity_unit','entries','max_stay_duration','max_stay_unit','confidence_level','info_validity','collected_at'):
    assert a.get(f)==b.get(f),(x['cache_key'],f)
   assert c.CARD in b['entry_requirements'] and c.DECL.rstrip('.') in b['entry_requirements']
 assert not any(r[k] for k in ('raw_writes','operator_writes','issue_changes','renew_fresh_until','confidence_changed','new_release'))

def test_genuine_missing_false_values_are_corrected_without_fee_or_np_invention(inputs):
 _,r=run(*inputs);before={l['cache_key'][:3]:l['merged_guidance'].get('arrival_card') for l in inputs[1]}
 assert {k for k,v in before.items() if isinstance(v,dict) and v.get('required') is False}=={'AUS','CAN','FRA','IDN','USA'}
 assert before['TWN']['required'] is None and before['VNM'] is None
 for x in r['routes']:
  p=x['source_provenance']['field_provenance']['arrival_card'];assert p['status']=='reviewed' and p['quote']==c.CARD_QUOTE
  assert 'visa' not in x['guidance']['arrival_card']['name'].lower()
  assert p['verified_at']=='2026-09-10' and not p.get('effective_to')

def test_installed_overlay_path_and_pending_guard_stay_truthful(inputs,tmp_path):
 overlay,report=run(*inputs);layers=inputs[1];base=tmp_path/'base.json';out=tmp_path/'out.json';op=tmp_path/'op.json'
 base.write_text(json.dumps([e for l in layers for e in l['seed_entries']]));out.write_text(json.dumps(overlay));op.write_text('[]')
 with patch.object(vo,'OVERRIDES',base),patch.object(vo,'_reviewed_overlay_paths',return_value=[out]),patch.object(vo,'operator_overrides_path',return_value=op):table=vo._load_table()
 for x in report['routes']:
  l=next(l for l in layers if l['cache_key']==x['cache_key']);key=vo._key(l['route']['passport_nationality'],'IND','tourism','ordinary_passport')
  with patch.object(vo,'find',return_value=table[key]):g,p=vo.apply(l['raw_guidance'],l['route'])
  assert (g,p)==(x['guidance'],x['source_provenance'])
  for field in ('visa_products','arrival_card','passport_validity'):
   held=records_guard.apply_records_hold(l['route'],{'guidance':g,'source_verified':p,'grounded_check':{'disputed_fields':[field]},'operator_released':True})
   assert held['held'] and records_guard.held_envelope(held)['guidance'] is None

@pytest.mark.parametrize('index',range(17))
@pytest.mark.parametrize('field',c.BASELINE_KEYS)
def test_each_actual_route_each_layer_cas(inputs,index,field):
 spec,layers=inputs;m=c.build_manifest(spec,layers);value=layers[index][field]
 if isinstance(value,dict):value['changed']=True
 else:value.append({'changed':True})
 with pytest.raises(PatchRejected):c.convert(m,layers)

@pytest.mark.parametrize('kind',['missing','duplicate','extra','wrong_key','wrong_passport'])
def test_route_set_and_scope(inputs,kind):
 spec,layers=inputs
 if kind=='missing':layers.pop()
 elif kind=='duplicate':layers[1]=deepcopy(layers[0])
 elif kind=='extra':layers.append(deepcopy(layers[0]))
 elif kind=='wrong_key':layers[0]['cache_key']=layers[0]['cache_key'].replace('IND','USA')
 else:layers[0]['route']['travel_document_type']='diplomatic_passport'
 with pytest.raises(PatchRejected):c.build_manifest(spec,layers)

@pytest.mark.parametrize('kind',['text','url','date','body_hash','duplicate','missing','rehash'])
def test_source_binding(inputs,kind):
 spec,layers=inputs;s=spec['sources'][0]
 if kind=='text':s['text']+='extra'
 elif kind=='url':s['url']='https://example.com/'
 elif kind=='date':s['checked_at']='2026-09-11'
 elif kind=='body_hash':s['capture_body_sha256']='0'*64
 elif kind=='duplicate':spec['sources'].append(deepcopy(s))
 elif kind=='missing':spec['sources'].pop()
 else:s['text']=s['text'].replace('must complete','may complete');s['sha256']=hashlib.sha256(s['text'].encode()).hexdigest()
 with pytest.raises(PatchRejected):c.build_manifest(spec,layers)

@pytest.mark.parametrize('kind',['required_false','required_integer','scope','fee','product_removed','condition_removed','fake_expiry'])
def test_no_unreviewed_value_or_type_can_be_laundered(inputs,kind):
 spec,layers=inputs;r=spec['routes'][0];changes={x['field']:x for x in r['changes']}
 if kind=='required_false':changes['arrival_card']['new']['required']=False
 elif kind=='required_integer':changes['arrival_card']['new']['required']=1
 elif kind=='scope':r['changes'].append(dict(field='disposition',new='VISA_EXEMPT'))
 elif kind=='fee':changes['visa_products']['new'][0]['fee']['amount']=0
 elif kind=='product_removed':changes['visa_products']['new'].pop()
 elif kind=='condition_removed':changes['entry_requirements']['new']=c.CARD+' '+c.DECL
 else:changes['visa_products']['new'][0]['policy_valid_until']='2027-01-01'
 with pytest.raises(PatchRejected):c.build_manifest(spec,layers)

@pytest.mark.parametrize('kind',['metadata','proof','output','manifest','omission'])
def test_complete_prepared_integrity(inputs,kind):
 spec,layers=inputs;m=c.build_manifest(spec,layers);o,_=c.convert(m,layers)
 if kind=='metadata':o['actor']='human'
 elif kind=='proof':o['entries'][0]['field_provenance']['arrival_card']['status']='not_published'
 elif kind=='output':o['entries'][0]['fields']['insurance_required']=False
 elif kind=='manifest':m['schema_version']=True
 else:o['entries'].pop()
 with pytest.raises(PatchRejected):c.verify_prepared(spec,layers,m,o)


def test_air_declaration_distinguishes_scope_obligation_request_and_permissive_timing(inputs):
 spec,layers=inputs;_,report=run(spec,layers)
 assert c.DECL.startswith('For international arrivals by air,')
 assert 'mandatory before immigration clearance' in c.DECL
 assert 'before boarding your flight, as requested' in c.DECL
 assert 'can be completed 24 hours in advance' in c.DECL
 assert 'at least 24 hours' not in c.DECL and 'must complete 24 hours' not in c.DECL
 assert '72 hours' in c.ARRIVAL['submission_window'] and 'by air' not in c.CARD
 for route in report['routes']:
  proof=route['source_provenance']['field_provenance']['entry_requirements']
  assert proof['verified_elements']==[c.CARD,c.DECL]
  assert 'Land/sea applicability of Air Suvidha is not established' in proof['note']
  evidence=proof['supporting_evidence']
  assert any(e['source_url']==c.PIB and e['quote']==c.DECL_TIMING_QUOTE for e in evidence)
  assert any(e['source_url']==c.PIB and e['quote']==c.DECL_QUOTE for e in evidence)
  assert c.DECL in str(route['guidance']['entry_requirements'])
  for product in route['guidance']['visa_products']:
   assert c.DECL in str(product['entry_requirements'])
   assert product['field_provenance']['entry_requirements']['verified_elements']==[c.CARD,c.DECL]


@pytest.mark.parametrize('change',['air_scope','boarding_request','permissive_timing','immigration_deadline'])
def test_cannot_remove_air_qualifier_or_change_timing_strength(inputs,change):
 spec,layers=inputs
 replacements={
  'air_scope':('For international arrivals by air, ','For all arrivals, '),
  'boarding_request':('before boarding your flight, as requested','before boarding your flight, as legally required'),
  'permissive_timing':('can be completed 24 hours in advance','must be completed at least 24 hours in advance'),
  'immigration_deadline':('mandatory before immigration clearance','optional before immigration clearance'),
 }
 old,new=replacements[change]
 for route in spec['routes']:
  for field in route['changes']:
   if field['field']=='entry_requirements':
    v=field['new'];field['new']=[x.replace(old,new) for x in v] if isinstance(v,list) else v.replace(old,new)
 with pytest.raises(PatchRejected):c.build_manifest(spec,layers)


@pytest.mark.parametrize('change',['missing','quote','url','body_hash','date'])
def test_air_suvidha_pib_capture_cannot_be_replaced_or_dropped(inputs,change):
 spec,layers=inputs;source=next(s for s in spec['sources'] if s['id']=='india_pib_airsuvidha')
 if change=='missing':spec['sources'].remove(source)
 elif change=='quote':source['text']=source['text'].replace('are requested to fill','must fill');source['sha256']=hashlib.sha256(source['text'].encode()).hexdigest()
 elif change=='url':source['url']='https://www.pib.gov.in/PressReleasePage.aspx?PRID=1111111'
 elif change=='body_hash':source['capture_body_sha256']='0'*64
 else:source['checked_at']='2026-09-11'
 with pytest.raises(PatchRejected):c.build_manifest(spec,layers)
