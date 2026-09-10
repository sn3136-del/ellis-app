"""No consular district exists for a published, evidenced no-application lane."""
from copy import deepcopy
import pytest
from app.visa_snapshot import tstation as t
from scripts.audit_acceptance_snapshot import audit


def record(**changes):
    row = {
        'travel_document_type':'ordinary_passport','travel_document_country':'HKG',
        'destination_country':'JPN','travel_purpose':'tourism','visa_requirement':'Visa-free',
        'visa_requirement_detail':'Unconditional Visa-free','visa_type_name':'Visa-free entry',
        'validity_duration':None,'validity_unit':None,'max_stay_duration':90,'max_stay_unit':'Day',
        'entries':None,'processing_min_days':None,'processing_unit':None,
        'visa_fee_amount':0,'visa_fee_currency':None,'application_method':None,
        'required_documents':'Valid passport','consulate_district':None,
        'entry_requirements':None,'special_conditions':None,
        'data_source':'Recorded official-source review','source_url':'https://www.mofa.go.jp/',
        'collected_at':'2026-09-09','info_validity':None,'confidence_level':'Low',
        '_source_check':'ai-quote','_held':False,'_route_held':False,'_disputed':[],
        '_contradictions':[],'cache_key':'HKG|HKG|JPN|tourism|default|unknown|v6',
    }
    row.update(changes)
    return row


@pytest.mark.parametrize('detail',['Unconditional Visa-free','Conditional Visa-free','Transit Visa-free'])
def test_published_exemption_has_no_consular_district_without_filling_a_fact(detail):
    row=record(visa_requirement_detail=detail);original=deepcopy(row)
    assert t.field_status(row)['consulate_district']=='not-applicable'
    assert row==original and row['consulate_district'] is None
    assert row['confidence_level']=='Low' and row['info_validity'] is None
    assert t.field_status(row)['info_validity']=='missing'


@pytest.mark.parametrize('case',['held','route_held','no_publication_state','reference','unchecked',
    'disputed_field','pending_status','contradiction','online_visa','paper_visa',
    'eta','on_arrival','contradictory_subcategory','explicit_filing','missing_subcategory'])
def test_unknown_or_inapplicable_shortcuts_cannot_create_new_completion(case):
    row=record()
    if case=='held':row['_held']=True
    elif case=='route_held':row['_route_held']=True
    elif case=='no_publication_state':row.pop('_held');row.pop('_route_held')
    elif case in {'reference','unchecked'}:row['_source_check']=case
    elif case=='disputed_field':row['_disputed']=['passport_validity']
    elif case=='pending_status':row['field_status']={'source_url':'pending-review'}
    elif case=='contradiction':row['_contradictions']=['Visa-free label conflicts with application requirement']
    elif case=='online_visa':row.update(visa_requirement='Visa Required in Advance',visa_requirement_detail='eVisa',application_method='Online Application')
    elif case=='paper_visa':row.update(visa_requirement='Visa Required in Advance',visa_requirement_detail='Paper Visa',application_method='Embassy Submission')
    elif case=='eta':row.update(visa_requirement='Conditional',visa_requirement_detail='ETA Electronic Authorization',application_method='Online Application')
    elif case=='on_arrival':row.update(visa_requirement='Visa on Arrival',visa_requirement_detail='Paper Visa on Arrival',application_method='On-arrival Processing')
    elif case=='contradictory_subcategory':row['visa_requirement_detail']='eVisa'
    elif case=='explicit_filing':row['application_method']='Embassy Submission'
    else:row['visa_requirement_detail']=None
    assert t.field_status(row)['consulate_district']=='optional-empty'


def test_recorded_value_and_documented_absence_are_not_reclassified():
    assert t.field_status(record(consulate_district='Consular jurisdiction supplied by review'))['consulate_district']=='filled'
    row=record(_unpublished=['consulate_district'])
    assert t.field_status(row)['consulate_district']=='not-published'


def test_internal_serialized_and_acceptance_auditor_use_the_same_disposition():
    row=record();internal=t.acceptance_summary([row])
    exported={k:v for k,v in row.items() if not k.startswith('_')}
    exported.update(source_check=row['_source_check'],held=False,route_held=False,field_status=t.field_status(row))
    result=audit({'fields':list(t.FIELD_ORDER),'required_fields':sorted(t.REQUIRED_FIELDS),
                  'records':[exported],'summary':{'total':1}})
    assert result['documented_completed_cells']==internal['documented_completed_cells']
    assert result['documented_disposition_cells']==internal['documented_disposition_cells']
    assert result['contract_field_completeness_percent']==pytest.approx(100*internal['field_completeness_rate'])
    reference=dict(row,_source_check='reference')
    baseline=t.acceptance_summary([reference])
    # The later pure-exemption projection also documents the four visa-only
    # cells as inapplicable; neither check invents a literal value.
    expected_new_na={'consulate_district','validity_duration','validity_unit','entries','visa_fee_currency'}
    before_status=t.field_status(reference);after_status=t.field_status(row)
    assert {f for f in t.FIELD_ORDER if before_status[f]!=after_status[f]}==expected_new_na
    assert all(after_status[f]=='not-applicable' for f in expected_new_na)
    assert internal['documented_completed_cells']==baseline['documented_completed_cells']+len(expected_new_na)
    assert internal['filled_cells']==baseline['filled_cells']
    assert internal['complete_records']==baseline['complete_records']==0
    assert internal['documented_complete_records']==baseline['documented_complete_records']==0
    assert internal['accuracy_certified'] is False and result['acceptance_certified'] is False


def test_held_optional_sibling_and_low_grade_are_preserved():
    default=record();optional=record(visa_requirement='Visa Required in Advance',visa_requirement_detail='Paper Visa',visa_type_name='Optional consular visa',application_method=None,_held=True)
    assert t.field_status(default)['consulate_district']=='not-applicable'
    assert t.field_status(optional)['consulate_district']=='optional-empty'
    assert default['confidence_level']==optional['confidence_level']=='Low'
