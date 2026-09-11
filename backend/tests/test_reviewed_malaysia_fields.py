from copy import deepcopy
import hashlib,json
from pathlib import Path
from unittest.mock import patch
import pytest

from scripts import convert_reviewed_malaysia_fields as c
from scripts.prepare_reviewed_product_patch import PatchRejected,digest
from app.visa_snapshot import verified_overrides as vo,records_guard,tstation

DATA=Path(__file__).resolve().parents[2]/'data/database_seed'

@pytest.fixture
def inputs():
    manifest=json.loads((DATA/'reviewed_malaysia_field_manifest_idn20260910.json').read_text())
    return manifest['specification'],[dict(deepcopy(manifest['baseline']),cache_key=c.CASE_KEY)]

def run(spec,layers):return c.convert(c.build_manifest(spec,layers),layers)

def test_only_five_values_and_six_owned_proofs_change(inputs):
    spec,layers=inputs;before=deepcopy(layers);o,r,g,p=run(spec,layers);base=layers[0]
    assert layers==before
    assert {k for k in set(g)|set(base['merged_guidance']) if g.get(k)!=base['merged_guidance'].get(k)}==c.FIELDS-{'insurance_required'}
    for k in ('disposition','requirement_detail','permitted_stay','permitted_stay_days','confidence',
              'passport_validity','passport_validity_requirement','arrival_card','health_requirements'):
        assert g[k]==base['merged_guidance'][k]
    assert g['permitted_stay']=='Less than 1 month' and g['permitted_stay_days'] is None
    assert not g.get('visa_products') and 'unpublished_fields' not in g
    for k in ('source_url','verified_at','verified_by','verifier','note'):
        assert p[k]==base['source_provenance'][k]
    assert p['verified_at']=='2026-08-30'
    assert p['field_provenance']['disposition']==base['source_provenance']['field_provenance']['disposition']
    assert set(p['fields'])=={'disposition'}|c.FIELDS
    assert g['insurance_required'] is False
    assert p['field_provenance']['insurance_required']['source_url']==c.URLS['insurance']
    assert p['field_provenance']['insurance_required']['quote']==c.INSURANCE
    assert p['field_provenance']['insurance_required']['verified_at']=='2026-09-10'
    assert not any(r[k] for k in ('new_grounded_check','new_release','renew_fresh_until','new_verdict_reviews','new_product_reviews'))

def test_extension_proof_does_not_certify_untouched_purpose_condition(inputs):
    _,_,g,p=run(*inputs);proof=p['field_provenance']['exceptions']
    assert g['exceptions']==[c.RETAINED,c.EXTENSION_VALUE]
    assert proof['status']=='partial' and proof['verification_scope']=='changed_elements_only'
    assert proof['verified_elements']==[c.EXTENSION_VALUE]
    assert proof['retained_unverified_elements']==[c.RETAINED]
    assert c.EXTENSION in proof['quote']
    assert '1-month limit' not in json.dumps(g) and '30-day visa-free' not in json.dumps(g)

def test_registered_loader_projects_exact_qc_row_and_holds_still_apply(inputs,tmp_path):
    spec,layers=inputs;o,r,expected,p=run(spec,layers);base=tmp_path/'base.json';over=tmp_path/'over.json';op=tmp_path/'operator.json'
    base.write_text(json.dumps(layers[0]['seed_entries']));over.write_text(json.dumps(o));op.write_text('[]')
    with patch.object(vo,'OVERRIDES',base),patch.object(vo,'_reviewed_overlay_paths',return_value=[over]),patch.object(vo,'operator_overrides_path',return_value=op):
        table=vo._load_table()
    with patch.object(vo,'find',return_value=table[vo._key('IDN','MYS','tourism','ordinary_passport')]):
        g,prov=vo.apply(layers[0]['raw_guidance'],layers[0]['route'])
    assert (g,prov)==(expected,p)
    row,=tstation.records_for_route(layers[0]['route'],g,prov)
    assert row==r['after_records'][0]
    assert '30-day' not in row['entry_requirements']
    assert 'confirmed lodging' in row['entry_requirements'] and 'is required' in row['entry_requirements']
    assert row['max_stay_duration'] is None and row['max_stay_text']=='Less than 1 month'
    assert row['confidence_level']==r['before_records'][0]['confidence_level']=='High'  # checked and complete (a stay in words is a value); the correction does not change the tier
    assert row['collected_at']==r['before_records'][0]['collected_at']
    out=records_guard.apply_records_hold(layers[0]['route'],{'guidance':g,'source_verified':prov,'grounded_check':{'disputed_fields':['passport_validity']},'operator_released':True})
    assert out['held'] and records_guard.held_envelope(out)['guidance'] is None

@pytest.mark.parametrize('field',c.BASELINE_KEYS)
def test_each_actual_layer_cas_change_rejected(inputs,field):
    spec,layers=inputs;m=c.build_manifest(spec,layers)
    if isinstance(layers[0][field],dict):layers[0][field]['concurrent_change']=True
    else:layers[0][field].append({'concurrent_change':True})
    with pytest.raises(PatchRejected):c.convert(m,layers)

@pytest.mark.parametrize('field,value',[('passport_nationality','MYS'),('lawful_country_of_residence','SGP'),
    ('destination_country','IDN'),('travel_purpose','business'),('travel_document_type','diplomatic_passport'),('arrival_date','2026-08-01')])
def test_rebound_other_route_is_not_covered(inputs,field,value):
    spec,layers=inputs;spec['route'][field]=value;layers[0]['route'][field]=value
    spec['baseline_sha256']={k:digest(layers[0][k]) for k in c.BASELINE_KEYS}
    with pytest.raises(PatchRejected):c.build_manifest(spec,layers)

@pytest.mark.parametrize('field,value',[('onward_travel_evidence','Onward ticket within 30 days'),
    ('accommodation_evidence','Hotel booking or host address may be requested'),('financial_evidence','Bring 1000 USD'),
    ('required_documents',['Passport']),('insurance_required',True),('insurance_required',0),
    ('exceptions',[c.RETAINED,'Extensions are available for another 30 days.'])])
def test_altered_value_cannot_borrow_reviewed_quote(inputs,field,value):
    spec,layers=inputs;next(x for x in spec['changes'] if x['field']==field)['new']=value
    with pytest.raises(PatchRejected):c.build_manifest(spec,layers)

@pytest.mark.parametrize('mutation',['drop_condition','wrong_subject','wrong_url','unknown_status','human','stale_date','future_date','duplicate_field','extra_field','full_exception_credit'])
def test_proof_scope_cannot_be_weakened(inputs,mutation):
    spec,layers=inputs;proof=spec['changes'][0]['proof']
    if mutation=='drop_condition':proof['evidence'].pop()
    elif mutation=='wrong_subject':proof['subject']['travel_purpose']='work'
    elif mutation=='wrong_url':proof['evidence'][0]['source_url']=c.URLS['extension']
    elif mutation=='unknown_status':proof['status']='not_published'
    elif mutation=='human':proof['verifier']='human'
    elif mutation=='stale_date':proof['verified_at']='2026-09-09'
    elif mutation=='future_date':proof['verified_at']='2099-01-01'
    elif mutation=='duplicate_field':spec['changes'][1]=deepcopy(spec['changes'][0])
    elif mutation=='extra_field':spec['changes'].append({'field':'confidence','new':'high'})
    else:
        proof=next(x for x in spec['changes'] if x['field']=='exceptions')['proof']
        proof['verified_elements']=[c.RETAINED,c.EXTENSION_VALUE];proof['retained_unverified_elements']=[]
    with pytest.raises(PatchRejected):c.build_manifest(spec,layers)

@pytest.mark.parametrize('mutation',['bad_hash','dropped_quote','wrong_source','dropped_context'])
def test_captured_source_and_scope_are_required(inputs,mutation):
    spec,layers=inputs;s=next(s for s in spec['sources'] if s['id']=='insurance')
    if mutation=='bad_hash':s['sha256']='0'*64
    elif mutation=='wrong_source':s['url']='https://www.imi.gov.my/unrelated/'
    else:
        s['text']=s['text'].replace(c.INSURANCE,'Insurance is mandatory.') if mutation=='dropped_quote' else s['text'].replace('Starting 1 May 2022','Archived update')
        s['sha256']=hashlib.sha256(s['text'].encode()).hexdigest()
    with pytest.raises(PatchRejected):c.build_manifest(spec,layers)

def test_operator_or_product_layer_cannot_be_rebound_into_six_field_review(inputs):
    spec,layers=inputs;layers[0]['operator_entries']=[{'fields':{'permitted_stay':'Longer'}}]
    spec['baseline_sha256']={k:digest(layers[0][k]) for k in c.BASELINE_KEYS}
    with pytest.raises(PatchRejected):c.build_manifest(spec,layers)

def test_new_product_cannot_be_silently_removed_by_same_review(inputs):
    spec,layers=inputs;layers[0]['raw_guidance']['visa_products']=[{'type':'New reviewed product'}]
    spec['baseline_sha256']={k:digest(layers[0][k]) for k in c.BASELINE_KEYS}
    with pytest.raises(PatchRejected):c.build_manifest(spec,layers)
