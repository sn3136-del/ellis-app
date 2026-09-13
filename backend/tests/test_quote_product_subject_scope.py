"""Actual saved product proofs: scope checks must compare valid product keys."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from app.visa_snapshot import record_evidence as ev,tstation

CASES=json.loads((Path(__file__).parent/'fixtures/quote_subject_scope.json').read_text())

@pytest.mark.parametrize('case',CASES,ids=lambda c:c['product']['type'])
def test_saved_scoped_product_quotes_are_displayed_without_changing_any_proof(case):
    route=case['route'];product=deepcopy(case['product']);field=case['field'];proof=deepcopy(case['proof'])
    before=deepcopy((route,product,proof))
    assert ev._owned(proof,route,product,field,product[field])
    product['field_provenance']={field:proof}
    guidance={'disposition':product['disposition'],'requirement_detail':product['requirement_detail'],'visa_products':[product]}
    row=tstation.records_for_route(route,guidance)[0]
    cell='validity_duration' if field=='validity' else 'required_documents'
    result=ev.for_record(row,route,guidance,{}, {})
    assert result[cell] and result[cell][0]['quote']==proof['quote']
    product.pop('field_provenance')
    assert (route,product,proof)==before

@pytest.mark.parametrize('case',CASES,ids=lambda c:c['product']['type'])
def test_different_or_absent_product_entry_channel_cannot_borrow_saved_scope(case):
    field=case['field'];product=deepcopy(case['product']);q=case['proof'];key='entry' if 'entry'in q['subject'] else 'application_channel'
    product[key]='other'
    assert not ev._owned(q,case['route'],product,field,product[field])
    product.pop(key)
    assert not ev._owned(q,case['route'],product,field,product[field])
    assert not ev._owned(q,case['route'],None,field,product[field])

@pytest.mark.parametrize('change',['value','nationality','product','future','source','unknown_subject_key'])
def test_product_scope_extension_does_not_weaken_existing_guards(change):
    c=deepcopy(CASES[0]);q=c['proof'];p=c['product'];r=c['route'];f=c['field']
    if change=='value':p[f]='different validity'
    elif change=='nationality':r['passport_nationality']='USA'
    elif change=='product':p['type']='Different sibling'
    elif change=='future':q['verified_at']='2099-01-01'
    elif change=='source':q['source_url']='https://example.com/unofficial'
    elif change=='unknown_subject_key':q['subject']['unrecognised_scope']='required'
    assert not ev._owned(q,r,p,f,p[f])
