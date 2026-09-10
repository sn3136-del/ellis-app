"""Independent regression checks, outside the candidate author-owned files."""
from pathlib import Path
from copy import deepcopy
import json
import pytest
BASE=Path(__file__).resolve().parents[1]
@pytest.fixture(autouse=True)
def _hold_enabled(monkeypatch):
    monkeypatch.setenv('ELLIS_HOLD_UNCERTAIN','1')
from app.visa_snapshot.records_guard import apply_records_hold
CASES=json.loads((BASE/'tests/fixtures/required_default_publication.json').read_text())['routes']
EXEMPT=json.loads((BASE/'tests/fixtures/optional_product_publication.json').read_text())

def env(c):
    return dict(deepcopy(c['reader_before_guard']),guidance=deepcopy(c['merged_guidance']),source_verified=deepcopy(c['source_provenance']),grounded_check=deepcopy(c.get('effective_grounded_check')),stale=False)

def india():return next(c for c in CASES if c['cache_key'].startswith('IND|'))

@pytest.mark.parametrize('reason',['product_validity_wrong_subject','channel_wrong_jurisdiction','docs_wrong_subject','unbound_application_portal','product_expired','product_future','product_malformed'])
def test_wrong_field_evidence_and_product_intervals_hold(reason):
    c=india();r=env(c);p=r['guidance']['visa_products'][0];v=r['source_verified']['field_provenance']
    if reason=='product_validity_wrong_subject':p['field_provenance']['validity']['subject']['product_type']='Single-entry tourist visa (sticker)'
    elif reason=='channel_wrong_jurisdiction':v['application_channel']['source_url']='https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing'
    elif reason=='docs_wrong_subject':v['required_documents']['subject'].update(passport_nationality='CHN',product_type='Paper tourist visa')
    elif reason=='unbound_application_portal':r['guidance']['official_portal_url']='https://www.kdmid.ru/cons/visas/'
    elif reason=='product_expired':p['field_provenance']['disposition']['effective_to']='2000-01-01'
    elif reason=='product_future':p['field_provenance']['disposition']['effective_from']='2099-01-01'
    else:p['field_provenance']['requirement_detail']['effective_to']='eventually'
    out=apply_records_hold(c['route'],r)
    assert out['held'] and out.get('publication_state')!='partial'

@pytest.mark.parametrize('text',['For longer stays, use a traditional visa.','A regular tourist visa guarantees a 90-day stay.','Other purposes need the appropriate regular visa; it grants a 90-day stay.','Holders of a valid regular visa may request a traditional tourist visa.'])
@pytest.mark.parametrize('field',['notes','exceptions'])
def test_withheld_offer_aliases_do_not_survive(text,field):
    c=india();r=env(c)
    if field=='notes':r['guidance']['visa_products'][0]['notes']=text
    else:r['guidance']['exceptions'].append(text)
    out=apply_records_hold(c['route'],r)
    assert out['held'] and out.get('publication_state')!='partial'

def test_unknown_fee_does_not_become_route_unpublished():
    c=india();r=env(c);r['guidance']['unpublished_fields']=['visa_fee_amount','visa_fee_currency']
    out=apply_records_hold(c['route'],r)
    if not out['held']:
        assert not set(out['guidance'].get('unpublished_fields') or []).intersection({'visa_fee_amount','visa_fee_currency'})
        assert out['guidance']['government_fee'] is None

def test_unknown_default_stay_is_not_parent_filled():
    c=next(c for c in EXEMPT if c['cache_key'].startswith('HKG|HKG|LCA|'));r=env(c)
    r['guidance']['visa_products'][0].setdefault('field_provenance',{})['max_stay_days']={'status':'unknown','reason':'Product stay not verified'}
    out=apply_records_hold(c['route'],r)
    if not out['held']:
        assert out['guidance']['visa_products'][0].get('permitted_stay') is None

def test_default_application_window_and_child_prerequisites_preserved():
    c=india();r=env(c);out=apply_records_hold(c['route'],r)
    assert out.get('publication_state')=='partial'
    p=out['guidance']['visa_products'][0]
    assert p['processing_time']==r['guidance']['visa_products'][0]['processing_time']
    assert '86 to 4 days' in p['processing_time']
    assert p['required_documents']==r['guidance']['visa_products'][0]['required_documents']
    assert p['exceptions']==r['guidance']['visa_products'][0]['exceptions']
    assert any('Every child needs a separate' in x for x in p['exceptions'])

def test_explicit_product_unknown_documents_do_not_inherit_parent_list():
    c=india();r=env(c);p=r['guidance']['visa_products'][0]
    p['required_documents']=None
    p['field_provenance']['required_documents']={'status':'unknown','reason':'These documents were withdrawn as unsupported for this product'}
    out=apply_records_hold(c['route'],r)
    assert out['held'] and out.get('publication_state')!='partial'
