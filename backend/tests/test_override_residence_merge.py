"""Residence-specific fields survive the real seed/operator merge and edits."""
from copy import deepcopy
import json
import pytest
from app.visa_snapshot import verified_overrides as vo

ROUTE={'passport_nationality':'IDN','destination_country':'KOR','travel_purpose':'tourism',
       'travel_document_type':'ordinary_passport','lawful_country_of_residence':'IDN'}
URL='https://overseas.mofa.go.kr/id-id/brd/m_2710/view.do?seq=748814'
KEY=vo._key('IDN','KOR','tourism','ordinary_passport')

def entry(fields,scope=None):
    out={'route':{'nationality':'IDN','destination':'KOR','travel_purpose':'tourism','travel_document_type':'ordinary_passport'},
         'source_url':URL,'verified_at':'2026-09-13','verified_by':'test source reviewer','verifier':'ai',
         'fields':deepcopy(fields),'field_provenance':{},'note':'Source-backed test values'}
    if scope:out['applicability']={'lawful_country_of_residence':scope[0],'fields':scope[1]}
    return out

@pytest.fixture
def stores(monkeypatch,tmp_path):
    seed=tmp_path/'verified_overrides.json';ops=tmp_path/'operators.json'
    seed.write_text('[]');ops.write_text('[]')
    monkeypatch.setattr(vo,'OVERRIDES',seed)
    monkeypatch.setattr(vo,'_reviewed_overlay_paths',lambda:[])
    monkeypatch.setenv('ELLIS_OPERATOR_OVERRIDES',str(ops))
    vo.reload()
    def write(seeds=(),operators=()):
        seed.write_text(json.dumps(seeds));ops.write_text(json.dumps(operators));vo.reload()
    yield write,ops
    vo.reload()

def base():
    return entry({'disposition':'VISA_REQUIRED','requirement_detail':'paper_visa',
                  'government_fee':{'amount':40,'currency':'USD'},'processing_time':'National processing wording'})

def jakarta():
    return entry({'required_documents':['Indonesian family card'],
                  'application_channel_detail':'Apply through KVAC Jakarta',
                  'visa_products':[{'type':'C-3-9 Tourist visa','disposition':'VISA_REQUIRED',
                                    'requirement_detail':'paper_visa','validity':'3 months','max_stay_days':30}]},
                 ('IDN',['required_documents','application_channel_detail','visa_products']))

@pytest.mark.parametrize('residence',['IDN','USA','',None])
def test_actual_load_table_keeps_only_applicable_product_documents_and_proofs(stores,residence):
    write,_=stores;write([base()],[jakarta()])
    table=vo._load_table();original=deepcopy(table)
    assert set(table[KEY]['field_applicability'])=={'required_documents','application_channel_detail','visa_products'}
    hit=vo.find(dict(ROUTE,lawful_country_of_residence=residence))
    assert hit['fields']['disposition']=='VISA_REQUIRED' and hit['fields']['government_fee']=={'amount':40,'currency':'USD'}
    for field in jakarta()['fields']:
        assert (field in hit['fields'])==(residence=='IDN')
        assert (field in hit['field_provenance'])==(residence=='IDN')
    assert table==original

def test_missing_residence_cannot_select_a_wholly_local_entry(stores):
    write,_=stores;write([],[entry({'required_documents':['Local document']},('IDN',['required_documents']))])
    assert vo.find({k:v for k,v in ROUTE.items() if k!='lawful_country_of_residence'}) is None
    assert vo.find(dict(ROUTE,lawful_country_of_residence='IDN'))['fields']['required_documents']==['Local document']

def test_unrelated_global_operator_field_preserves_seed_scope(stores):
    write,_=stores
    seed=entry({'required_documents':['Local card'],'processing_time':'Local timing'},('IDN',['required_documents','processing_time']))
    write([seed],[entry({'passport_validity':'Valid passport'})])
    hit=vo.find(dict(ROUTE,lawful_country_of_residence='USA'))
    assert hit['fields']=={'passport_validity':'Valid passport'}
    assert set(hit['inapplicable_fields'])=={'required_documents','processing_time'}

def test_explicit_global_replacement_removes_only_replaced_seed_constraint(stores):
    write,_=stores
    seed=entry({'required_documents':['Local card'],'processing_time':'Local timing'},('IDN',['required_documents','processing_time']))
    write([seed],[entry({'processing_time':'Global timing'})])
    table=vo._load_table();assert table[KEY]['field_applicability']=={'required_documents':{'lawful_country_of_residence':'IDN'}}
    hit=vo.find(dict(ROUTE,lawful_country_of_residence='USA'))
    assert hit['fields']=={'processing_time':'Global timing'}

def test_new_residence_scope_replaces_old_scope_for_changed_field(stores):
    write,_=stores
    write([entry({'processing_time':'Jakarta timing'},('IDN',['processing_time']))],
          [entry({'processing_time':'US timing'},('USA',['processing_time']))])
    assert vo.find(ROUTE) is None
    assert vo.find(dict(ROUTE,lawful_country_of_residence='USA'))['fields']['processing_time']=='US timing'

def test_next_global_operator_edit_cannot_erase_prior_local_scope(stores):
    write,path=stores;write([base()],[jakarta()])
    vo.append_operator_entry(entry({'processing_time':'Updated global timing'}))
    stored=json.loads(path.read_text())
    assert len(stored)==1 and set(stored[0]['applicability']['fields'])==set(jakarta()['fields'])
    foreign=vo.find(dict(ROUTE,lawful_country_of_residence='USA'))
    assert foreign['fields']['processing_time']=='Updated global timing'
    assert 'required_documents' not in foreign['fields'] and 'visa_products' not in foreign['fields']
    assert vo.find(ROUTE)['fields']['required_documents']==['Indonesian family card']

def test_replacing_last_local_field_with_global_value_removes_serialized_scope(stores):
    write,path=stores
    write([base()],[entry({'processing_time':'Local timing'},('IDN',['processing_time']))])
    vo.append_operator_entry(entry({'processing_time':'Global timing'}))
    assert 'applicability' not in json.loads(path.read_text())[0]
    assert vo.find(dict(ROUTE,lawful_country_of_residence='USA'))['fields']['processing_time']=='Global timing'

def test_conflicting_residence_consolidation_rejects_without_losing_old_entry(stores):
    write,path=stores
    write([base()],[entry({'required_documents':['Local card']},('IDN',['required_documents']))]);before=path.read_bytes()
    with pytest.raises(ValueError,match='different residence scopes'):
        vo.append_operator_entry(entry({'processing_time':'USA timing'},('USA',['processing_time'])))
    assert path.read_bytes()==before

def test_malformed_scope_operator_write_is_rejected_before_file_change(stores):
    write,path=stores;write([base()],[]);before=path.read_bytes()
    bad=entry({'processing_time':'Local timing'},('IDN',['not_a_saved_field']))
    with pytest.raises(ValueError,match='malformed applicability'):
        vo.append_operator_entry(bad)
    assert path.read_bytes()==before

@pytest.mark.parametrize('residence',['IDN','USA',None,''])
def test_current_idn_packet_actual_apply_and_quotes_do_not_certify_other_residences(stores,residence):
    from pathlib import Path
    from app.visa_snapshot import tstation,record_evidence
    fixture=json.loads((Path(__file__).parent/'fixtures/idn_kor_residence_scope.json').read_text())
    assert fixture['packet_sha256']=='2c331fc67301bb0e0b135afc035ec39ae99c8bf4f247afb8ef0774b8bf7e041d'
    write,_=stores
    write(fixture['seed_entries'],fixture['operator_entries']+[fixture['operator_entry']])
    route=dict(fixture['route'],lawful_country_of_residence=residence)
    raw=deepcopy(fixture['raw_guidance']);original=deepcopy(raw)
    guidance,provenance=vo.apply(raw,route)
    rows=tstation.records_for_route(route,guidance,provenance,disputed_fields=[])
    quotes=[record_evidence.for_record(row,route,guidance,provenance,{},active_override=vo.find(route)) for row in rows]
    scope=set(fixture['operator_entry']['applicability']['fields'])
    assert raw==original
    assert guidance['disposition']=='VISA_REQUIRED'
    assert guidance['government_fee']=={'amount':40,'currency':'USD'}
    assert guidance['arrival_card']==fixture['operator_entry']['fields']['arrival_card']
    if residence=='IDN':
        assert scope<=set(provenance['fields'])
        assert len(rows)==1 and rows[0]['confidence_level']=='Medium' and not rows[0]['_evidence_low']
        assert len(guidance['visa_products'])==1
        assert guidance['visa_products'][0]['max_stay_days']==30
        assert guidance['visa_products'][0]['validity']=='3 months'
        assert guidance['required_documents']==fixture['operator_entry']['fields']['required_documents']
        assert quotes[0]['required_documents'] and quotes[0]['max_stay_duration'] and quotes[0]['validity_duration']
    else:
        assert not scope.intersection(provenance['fields'])
        assert not scope.intersection(provenance['field_provenance'])
        assert guidance.get('source_url')!=fixture['operator_entry']['fields']['source_url']
        assert guidance['required_documents']==[raw['required_documents']]
        assert guidance['permitted_stay_days']==raw['permitted_stay_days']==90
        # Historical fallback products remain unverified; none inherit Jakarta's proof.
        assert guidance['visa_products']==raw['visa_products']
        for proof in quotes:
            for field in ('required_documents','max_stay_duration','validity_duration','visa_type_name','application_method'):
                assert proof[field]==[]
