from copy import deepcopy
import json
from pathlib import Path
import pytest

from scripts.convert_reviewed_australia_patch import convert_manifest, convert_entry, digest, IDN_NAMES
from scripts.prepare_reviewed_product_patch import PatchRejected
from app.visa_snapshot import verified_overrides as vo, tstation

SEED = Path(__file__).resolve().parents[2] / 'data/database_seed'


@pytest.fixture(scope='module')
def manifest():
    return json.loads((SEED/'reviewed_australia_products_2026_09_09.json').read_text())


def layers(manifest):
    return [deepcopy(e['baseline']) for e in manifest['routes']]


def selected(manifest, nat):
    e = deepcopy(next(e for e in manifest['routes'] if e['route']['passport_nationality']==nat))
    return e, {s['id']:deepcopy(s) for s in manifest['sources']}, deepcopy(e['baseline'])


def projected(result, route, disputed_fields=()):
    seed=result['entry']; key=vo._key(seed['route']['nationality'],'AUS','tourism','ordinary_passport')
    parsed=vo._parse_rows([seed],{})[key]
    prov=dict(parsed['field_provenance']['disposition'],fields=list(parsed['fields']),field_provenance=parsed['field_provenance'])
    return tstation.records_for_route(route,result['effective_guidance'],prov,disputed_fields=disputed_fields)


def test_actual15_current_layers_convert18_reviewed_products_without_certification(manifest):
    ls=layers(manifest); before=deepcopy(ls); out=convert_manifest(manifest,ls)
    assert ls==before and len(out['entries'])==15 and out['blocked']==[]
    assert sum(len(e['fields']['visa_products']) for e in out['entries'])==19
    reviewed=[p for e in out['entries'] for p in e['fields']['visa_products'] if p.get('field_provenance',{}).get('disposition',{}).get('verified_at')=='2026-09-09']
    assert len(reviewed)==18
    for product in reviewed:
        proof=product['field_provenance']['disposition']
        assert proof['subject']['product_type']==product['type'] and proof['verifier']=='ai'
        assert proof['subject']['disposition']==product['disposition']
        assert proof['subject']['requirement_detail']==product['requirement_detail']
        assert all(product.get(k) is None for k in ['max_stay_days','processing_min_days','processing_max_days'])
    assert all(not p['new_grounded_check'] and not p['new_release'] and not p['renew_fresh_until'] for p in out['preflight'])


def test_exact_idn4_to1_keeps_original_archive_and_unrelated_fields(manifest):
    e,s,c=selected(manifest,'IDN'); old=deepcopy(c); out=convert_entry(e,s,c)
    assert c==old
    assert [p['type'] for p in out['preflight']['consolidated_original_products']]==IDN_NAMES
    assert out['preflight']['consolidated_original_products']==c['merged_guidance']['visa_products']
    assert len(out['effective_guidance']['visa_products'])==1
    for field in set(c['merged_guidance'])-set(e['fields'])-{'visa_products','source_url'}:
        assert out['effective_guidance'].get(field)==c['merged_guidance'].get(field)


def test_deu_evisitor_primary_is_byte_equal_and_not_given_eta_proof(manifest):
    e,s,c=selected(manifest,'DEU'); out=convert_entry(e,s,c)
    assert e['fields']=={}
    assert out['effective_guidance']['visa_products'][0]==c['merged_guidance']['visa_products'][0]
    assert out['entry']['field_provenance']['disposition']['verified_at']=='2026-08-30'
    assert out['effective_guidance']['visa_products'][1]['fee']['amount']==20


@pytest.mark.parametrize('nat',['CAN','HKG','JPN','KOR','MYS','SGP','TWN','USA','DEU'])
def test_eta_stay_fee_and_app_are_product_specific(manifest,nat):
    e,s,c=selected(manifest,nat); out=convert_entry(e,s,c)
    rows=projected(out,e['route']); eta=next(r for r in rows if '601' in r['visa_type_name'])
    assert eta['visa_requirement_detail']=='ETA Electronic Authorization'
    assert eta['visa_fee_amount']==20 and eta['visa_fee_currency']=='AUD'
    assert eta['max_stay_duration'] is None and '3 calendar months' in eta['max_stay_text']
    assert eta['application_method']=='Online Application'
    assert 'ETA app' in out['effective_guidance']['visa_products'][1 if nat=='DEU' else 0]['application_channel_detail']
    assert 'passport' in eta['special_conditions'] and 'expires' in eta['special_conditions']
    assert eta['confidence_level']!='High'


def test_china_streams_keep_discretion_and_independent_method_unknowns(manifest):
    e,s,c=selected(manifest,'CHN'); out=convert_entry(e,s,c); rows=projected(out,e['route'])
    tourist,frequent,ads=rows
    assert tourist['visa_fee_amount']==250 and tourist['visa_fee_qualifier']=='from'
    assert frequent['visa_fee_amount']==1845 and frequent['validity_duration'] is None
    assert frequent['application_method'] is None and frequent['required_documents'] is None
    assert ads['application_method']=='Agency Service' and ads['visa_fee_amount']==250
    assert all(r['entries'] is None for r in (tourist,ads))
    assert 'certain areas' in out['effective_guidance']['visa_products'][2]['source_quote']
    assert 'China at lodgement and decision' in ads['special_conditions']
    assert all(r['max_stay_duration'] is None for r in rows)


def test_null_clears_have_no_fresh_source_or_date_and_no_sibling_proof(manifest):
    e,s,c=selected(manifest,'CHN'); out=convert_entry(e,s,c)
    frequent=out['effective_guidance']['visa_products'][1]
    for field in ('application_channel','application_channel_detail','required_documents','processing_min_days','biometrics_required','consular_jurisdiction'):
        p=frequent['field_provenance'][field]
        assert frequent[field] is None and p['status']=='unknown'
        assert not p['source_url'] and p['verified_at'] is None
    for field in ('biometrics_required','appointment_required','interview_required'):
        p=out['entry']['field_provenance'][field]
        assert not p['source_url'] and p['verified_at'] is None


@pytest.mark.parametrize('nat',['CHN','IND','VNM'])
def test_family_sentence_correction_does_not_certify_untouched_exceptions(manifest,nat):
    e,s,c=selected(manifest,nat); out=convert_entry(e,s,c)
    p=out['entry']['field_provenance']['exceptions']
    assert p['status']=='partial' and p['verification_scope']=='changed_elements_only'
    assert 'separate application' in p['verified_elements'][0]
    assert p['retained_unverified_elements']


def test_existing_dispute_keeps_all_china_products_low(manifest):
    e,s,c=selected(manifest,'CHN'); out=convert_entry(e,s,c)
    rows=projected(out,e['route'],['passport_validity'])
    assert all(r['confidence_level']=='Low' for r in rows)
    assert all('passport_validity' in r['_disputed_fields'] for r in rows)


@pytest.mark.parametrize('field',['raw_guidance','merged_guidance','seed_entries','operator_entries'])
def test_any_changed_layer_rejects_atomic_conversion(manifest,field):
    ls=layers(manifest); original=deepcopy(ls)
    if isinstance(ls[0][field],dict):ls[0][field]['new_fact']='changed'
    else:ls[0][field].append({'new_fact':'changed'})
    after=deepcopy(ls)
    with pytest.raises(PatchRejected,match='Current layer changed'):convert_manifest(manifest,ls)
    assert ls==after and original!=ls


@pytest.mark.parametrize('fault',['raw_guidance','merged_guidance','seed_entries','operator_entries','empty','missing','invalid','extra'])
def test_preflight_hash_contract_cannot_be_deleted_or_malformed(manifest,fault):
    e,s,c=selected(manifest,'HKG')
    if fault in {'raw_guidance','merged_guidance','seed_entries','operator_entries'}:e['baseline_hashes'].pop(fault)
    elif fault=='empty':e['baseline_hashes']={}
    elif fault=='missing':e.pop('baseline_hashes')
    elif fault=='invalid':e['baseline_hashes']['raw_guidance']='not-a-digest'
    else:e['baseline_hashes']['unknown']='a'*64
    c['raw_guidance']['passport_validity']='Changed since source review.'
    with pytest.raises(PatchRejected,match='four valid baseline'):convert_entry(e,s,c)


@pytest.mark.parametrize('fault',['fee_amount','fee_program','fee_qualifier','fee_charge_kind','nationality','document','purpose','missing_baseline','digital_quote','quoted_text','unknown_nonnull','release_field','consolidation_name'])
def test_adversarial_source_and_scope_mutations_reject(manifest,fault):
    nat='IDN' if fault in {'fee_qualifier','consolidation_name'} else 'HKG'
    e,s,c=selected(manifest,nat); patch=e['product_patches'][0]
    if fault=='fee_amount':patch['fields']['fee']['amount']=0
    elif fault=='fee_program':patch['field_provenance']['fee']['evidence']=deepcopy(next(x for x in manifest['routes'] if x['route']['passport_nationality']=='IDN')['product_patches'][0]['field_provenance']['fee']['evidence'])
    elif fault=='fee_qualifier':patch['fields']['fee'].pop('qualifier')
    elif fault=='fee_charge_kind':patch['fields']['fee']['note']='Visa fee AUD20'
    elif fault in {'nationality','document','purpose'}:
        key={'nationality':'passport_nationality','document':'travel_document_type','purpose':'travel_purpose'}[fault]
        e['route'][key]={'nationality':'VNM','document':'diplomatic_passport','purpose':'work'}[fault];c['route'][key]=e['route'][key]
    elif fault=='missing_baseline':patch['field_provenance']['disposition']['evidence']=[p for p in patch['field_provenance']['disposition']['evidence'] if p['source_id']!='abf_entry']
    elif fault=='digital_quote':patch['field_provenance']['requirement_detail']['evidence']=[p for p in patch['field_provenance']['requirement_detail']['evidence'] if 'digitally link' not in p['quote']]
    elif fault=='quoted_text':patch['field_provenance']['fee']['evidence'][0]['quote']='The visa costs AUD0.'
    elif fault=='unknown_nonnull':patch['fields']['biometrics_required']=True
    elif fault=='release_field':e['fields']['operator_released']=True
    else:patch['fields']['type']='An unrelated guaranteed multiple-entry product'
    before=deepcopy(c)
    with pytest.raises(PatchRejected):convert_entry(e,s,c)
    assert c==before


def test_operator_conflicting_product_edit_is_not_silently_overwritten(manifest):
    e,s,c=selected(manifest,'HKG')
    operator={'route':{'nationality':'HKG','destination':'AUS','travel_purpose':'tourism'},'source_url':s['eta_about']['url'],
              'verified_at':'2026-09-09','verified_by':'Operator','verifier':'human','note':'Reviewed fee decision',
              'fields':{'disposition':'ELECTRONIC_AUTHORIZATION_REQUIRED','government_fee':{'amount':21,'currency':'AUD'}}}
    c['operator_entries']=[operator];e['baseline_hashes']['operator_entries']=digest(c['operator_entries'])
    with pytest.raises(PatchRejected,match='operator correction conflicts'):convert_entry(e,s,c)


def test_unrelated_operator_field_remains_in_effective_reader_without_new_authorship(manifest):
    e,s,c=selected(manifest,'HKG')
    operator={'route':{'nationality':'HKG','destination':'AUS','travel_purpose':'tourism'},'source_url':s['abf_entry']['url'],
              'verified_at':'2026-09-08','verified_by':'Operator','verifier':'human','note':'Reviewed entry passport',
              'fields':{'passport_validity':'Valid passport; operator-controlled field.'}}
    c['operator_entries']=[operator];c['merged_guidance']['passport_validity']=operator['fields']['passport_validity']
    for key in ('operator_entries','merged_guidance'):e['baseline_hashes'][key]=digest(c[key])
    out=convert_entry(e,s,c)
    assert out['effective_guidance']['passport_validity']==operator['fields']['passport_validity']
    assert 'passport_validity' not in out['entry']['fields']
    assert 'passport_validity' not in out['entry']['field_provenance']


def test_retained_seed_field_authorship_not_promoted_to_new_review(manifest):
    e,s,c=selected(manifest,'DEU'); out=convert_entry(e,s,c)
    key=vo._key('DEU','AUS','tourism','ordinary_passport')
    old=vo._parse_rows(c['seed_entries'],{})[key]
    for field,proof in old['field_provenance'].items():
        if field!='visa_products':assert out['entry']['field_provenance'][field]==proof


@pytest.mark.parametrize('field,value',[('permitted_stay','90 days'),('validity','Guaranteed 12 months'),
                                      ('validity_duration',365),('validity_unit','Day'),
                                      ('entry','Single'),('application_channel','embassy')])
def test_true_source_quote_cannot_certify_changed_measure_or_method(manifest,field,value):
    e,s,c=selected(manifest,'HKG');e['product_patches'][0]['fields'][field]=value
    with pytest.raises(PatchRejected,match='Measure or filing method'):convert_entry(e,s,c)


@pytest.mark.parametrize('field,value',[('disposition','VISA_EXEMPT'),('requirement_detail','unconditional_visa_free')])
def test_new_product_proof_cannot_certify_a_later_permission_change(manifest,field,value):
    e,s,c=selected(manifest,'CHN'); out=convert_entry(e,s,c)
    p=out['effective_guidance']['visa_products'][1];p[field]=value
    rows=projected(out,e['route'])
    assert rows[1]['_product_source_verified'] is None
    assert rows[1]['confidence_level']=='Low'


@pytest.mark.parametrize('fault',['source_duplicate','current_duplicate','manifest_duplicate','same_authority_wrong_page'])
def test_identity_and_source_catalog_changes_reject(manifest,fault):
    m=deepcopy(manifest);ls=layers(m)
    if fault=='source_duplicate':m['sources'].append(deepcopy(m['sources'][0]))
    elif fault=='current_duplicate':ls.append(deepcopy(ls[0]))
    elif fault=='manifest_duplicate':m['routes'][-1]=deepcopy(m['routes'][0])
    else:
        source=next(s for s in m['sources'] if s['id']=='abf_entry');old=source['url'];source['url']='https://www.abf.gov.au/visas'
        for e in m['routes']:
            for proofs in [e['field_provenance']]+[p['field_provenance'] for p in e['product_patches']]:
                for p in proofs.values():
                    for ev in p.get('evidence',[]):
                        if ev['source_id']=='abf_entry':ev['source_url']=source['url']
    with pytest.raises(PatchRejected):convert_manifest(m,ls)
