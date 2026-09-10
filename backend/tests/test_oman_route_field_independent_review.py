"""Read-only independent checks of the exact three-field Oman correction."""
from pathlib import Path
from copy import deepcopy
import json,hashlib
import pytest
BASE=Path(__file__).resolve().parents[2]
from scripts.convert_reviewed_route_fields import convert,build_manifest,VALUES,BASELINE_KEYS,PatchRejected,CONDITIONS,NATIONALITY,PASSPORT
from app.visa_snapshot.records_guard import apply_records_hold
MANIFEST=json.loads((BASE/'data/database_seed/reviewed_route_field_manifest_oman20260910e.json').read_text())
LAYERS=[dict(deepcopy(MANIFEST['baseline']),cache_key=MANIFEST['cache_key'])]

def test_actual_scope_proofs_publication_and_qc():
    before=deepcopy(LAYERS);m=deepcopy(MANIFEST)
    overlay,report,g,proof=convert(m,LAYERS)
    layer=LAYERS[0]
    assert {k for k in set(g)|set(layer['merged_guidance']) if g.get(k)!=layer['merged_guidance'].get(k)}==set(VALUES)
    assert {k for k in set(proof['field_provenance'])|set(layer['source_provenance']['field_provenance']) if proof['field_provenance'].get(k)!=layer['source_provenance']['field_provenance'].get(k)}==set(VALUES)
    assert {k:v for k,v in proof.items() if k not in ('field_provenance','fields')}=={k:v for k,v in layer['source_provenance'].items() if k not in ('field_provenance','fields')}
    assert g['visa_products']==layer['merged_guidance']['visa_products']
    assert report['before_records'][1:]==report['after_records'][1:]
    for key in ['confidence_level','collected_at','info_validity','visa_requirement','visa_requirement_detail','visa_fee_amount','visa_fee_currency','max_stay_duration','validity_duration','_evidence_low']:
        assert report['before_records'][0][key]==report['after_records'][0][key]
    row=report['after_records'][0]
    assert 'return/onward' not in row['required_documents'].lower()+row['entry_requirements'].lower()
    assert 'host invitation' not in row['entry_requirements'].lower()
    assert 'Return ticket' in row['entry_requirements'] and 'Confirmed hotel reservation' in row['entry_requirements']
    a=apply_records_hold(layer['route'],{'guidance':layer['merged_guidance'],'source_verified':layer['source_provenance'],'stale':False,'held':False})
    b=apply_records_hold(layer['route'],{'guidance':g,'source_verified':proof,'stale':False,'held':False})
    assert a['held']==b['held'] is False and a['publication_state']==b['publication_state']=='partial'
    assert a['product_publication']==b['product_publication']
    assert {k:b['guidance'][k] for k in VALUES}==VALUES
    assert LAYERS==before and MANIFEST==m

@pytest.mark.parametrize('key',BASELINE_KEYS)
def test_every_current_layer_change_invalidates_cas(key):
    changed=deepcopy(LAYERS)
    if isinstance(changed[0][key],list):changed[0][key].append({'unexpected':'change'})
    else:changed[0][key]['unexpected']='change'
    with pytest.raises(PatchRejected):convert(MANIFEST,changed)

@pytest.mark.parametrize('reason',['onward_alias','host_alias','drop_insurance','extra_field','other_passport','wrong_source','source_edit','missing_condition','missing_passport','second_group','wrong_proof_subject','future_date'])
def test_out_of_scope_or_incomplete_evidence_rejected(reason):
    spec=deepcopy(MANIFEST['specification']);source=spec['sources'][0]
    if reason=='onward_alias':spec['changes'][1]['new']='Return or onward ticket'
    elif reason=='host_alias':spec['changes'][2]['new']='Confirmed hotel or host invitation'
    elif reason=='drop_insurance':spec['changes'][0]['new'].remove('Health insurance')
    elif reason=='extra_field':spec['changes'].append({'field':'government_fee','old_raw':None,'old_merged':None,'new':0,'proof':{}})
    elif reason=='other_passport':spec['route']['travel_document_type']='diplomatic_passport'
    elif reason=='wrong_source':source['url']='https://evisa.rop.gov.om/'
    elif reason=='source_edit':source['text']+='unreviewed addition'
    elif reason in ('missing_condition','missing_passport','second_group'):
        if reason=='missing_condition':source['text']=source['text'].replace(CONDITIONS,'')
        elif reason=='missing_passport':source['text']=source['text'].replace(PASSPORT,'')
        else:source['text']=source['text'].replace(NATIONALITY,'').replace('Second group','Second group '+NATIONALITY)
        source['sha256']=hashlib.sha256(source['text'].encode()).hexdigest()
    elif reason=='wrong_proof_subject':spec['changes'][0]['proof']['subject']={'passport_nationality':'CHN'}
    else:spec['changes'][0]['proof']['verified_at']='2099-01-01'
    with pytest.raises(PatchRejected):build_manifest(spec,LAYERS)
