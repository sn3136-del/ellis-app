"""Regression for the two Trip.com nationality/program mismatches, 2026-09-09."""
import copy
from pathlib import Path
import pytest
from app.visa_snapshot import kimi_primary as kp, tstation, verified_overrides as vo

SEED = Path(__file__).resolve().parents[2] / 'data/database_seed/verified_overrides.json'

@pytest.fixture
def shipped(monkeypatch, tmp_path):
    monkeypatch.setattr(vo, 'OVERRIDES', SEED)
    monkeypatch.setenv('ELLIS_OPERATOR_OVERRIDES', str(tmp_path / 'operator.json'))
    vo.reload()
    yield
    vo.reload()

def route(nationality, destination):
    return dict(passport_nationality=nationality, destination_country=destination,
                travel_purpose='tourism', travel_document_type='ordinary_passport')

def eta_raw():
    return dict(disposition='ELECTRONIC_AUTHORIZATION_REQUIRED', requirement_detail='eta_electronic_authorization',
                visa_category='ETA (subclass 601)', application_channel='online_portal',
                government_fee={'amount':20,'currency':'AUD'}, permitted_stay_days=90, permitted_stay='90 days',
                processing_time='Usually minutes to 24 hours',
                photo_requirements='Take a selfie in the Australian ETA mobile app',
                forms=['ETA app'], account_registration_steps=['Scan NFC passport in ETA app'],
                payment_process=['Pay AUD20 in the ETA app'], submission_process=['Submit ETA app'],
                biometrics_required=False, appointment_required=False, interview_required=False,
                visa_products=[{'type':'ETA (subclass 601)', 'fee':{'amount':20,'currency':'AUD'},
                                'max_stay_days':90,'entry':'multiple','validity':'12 months'}])

def test_thai_tourism_is_visitor_600_and_does_not_inherit_eta_machinery(shipped):
    raw = eta_raw(); original = copy.deepcopy(raw)
    g, provenance = vo.apply(raw, route('THA','AUS'))
    assert raw == original
    assert g['disposition'] == 'VISA_REQUIRED' and g['requirement_detail'] == 'evisa'
    assert '600' in g['visa_category'] and '601' not in g['visa_category']
    assert len(g['visa_products']) == 1 and '600' in g['visa_products'][0]['type']
    assert g['government_fee']['amount'] == 250 and g['government_fee']['qualifier'] == 'from'
    assert g['government_fee']['currency'] == 'AUD'
    assert g['visa_products'][0]['fee'] == g['government_fee']
    for key in ('photo_requirements','forms','account_registration_steps','payment_process','submission_process'):
        assert key not in g
    assert all(g[key] is None for key in ('biometrics_required','appointment_required','interview_required'))
    assert g['permitted_stay_days'] is None and g['visa_products'][0]['max_stay_days'] is None
    assert '3 months' in g['permitted_stay'] and '12 months' in g['permitted_stay']
    assert g['visa_products'][0]['entry'] is None and g['visa_products'][0]['validity'] is None
    assert 'ImmiAccount' in g['application_channel_detail'] and 'minutes' not in g['processing_time']
    assert provenance['verifier'] == 'ai'
    assert 'visitor-600' in provenance['field_provenance']['government_fee']['source_url']
    assert kp.serve_time_invariants(g) == []
    records = tstation.records_for_route(route('THA','AUS'),g,provenance=provenance)
    assert len(records)==1
    assert records[0]['visa_requirement'] == 'Visa Required in Advance'
    assert records[0]['visa_fee_amount'] == 250 and records[0]['visa_fee_qualifier'] == 'from'
    assert records[0]['max_stay_duration'] is None and '3 months' in records[0]['max_stay_text']
    assert records[0]['confidence_level'] == 'Medium'  # Checked against the official page; required field verification is incomplete.

def test_indonesian_independent_tourist_is_not_blanket_visa_free(shipped):
    raw = dict(disposition='VISA_EXEMPT',requirement_detail='unconditional_visa_free',
               visa_category='No visa needed',government_fee={'amount':0,'currency':None},
               permitted_stay='90 days',permitted_stay_days=90,visa_products=[])
    g, provenance = vo.apply(raw,route('IDN','KOR'))
    assert g['disposition'] == 'VISA_REQUIRED' and 'C-3-9' in g['visa_category']
    assert g['permitted_stay_days'] is None and g['government_fee'] is None
    assert provenance['source_url'] != 'https://www.k-eta.go.kr/portal/apply/index.do'
    assert provenance['verifier'] == 'ai'
    exception = next(x for x in g['exceptions'] if 'tour groups of at least 3' in x)
    for text in ('28 May','31 December 2026','15 days','designated','pre-screening and approval','same flight or vessel','independent tourists'):
        assert text in exception
    assert any('moj.go.kr' in s['url'] for s in g['corroborating_sources'])
    assert vo.find(dict(route('IDN','KOR'),travel_document_type='diplomatic_passport')) is None

def test_changed_permission_removes_unverified_photo_but_preserves_explicit_checked_fact():
    raw = eta_raw(); fields = {'disposition':'VISA_REQUIRED','requirement_detail':'paper_visa'}
    g, _ = vo.merge_verified_fields(raw,fields)
    assert 'photo_requirements' not in g
    g, _ = vo.merge_verified_fields(raw,dict(fields,photo_requirements='Two photographs specified by the mission'))
    assert g['photo_requirements'] == 'Two photographs specified by the mission'
    same, _ = vo.merge_verified_fields(raw,{'disposition':'ELECTRONIC_AUTHORIZATION_REQUIRED','requirement_detail':'eta_electronic_authorization'})
    assert same['photo_requirements'] == raw['photo_requirements']

def test_fee_floor_is_product_specific_not_inherited_across_permission_families():
    g = dict(disposition='ELECTRONIC_AUTHORIZATION_REQUIRED',requirement_detail='eta_electronic_authorization',
             government_fee={'amount':20,'currency':'AUD','qualifier':'from'}, visa_products=[
                 {'type':'Visitor visa','requirement_detail':'paper_visa','fee':{'amount':100,'currency':'AUD'}}])
    row = tstation.records_for_route(route('CAN','AUS'),g)[0]
    assert row['visa_fee_amount']==100 and row['visa_fee_qualifier'] is None
    g['visa_products']=[]
    row=tstation.records_for_route(route('CAN','AUS'),g)[0]
    assert row['visa_fee_amount']==20 and row['visa_fee_qualifier']=='from'

def test_records_endpoint_preserves_current_product_fee_floor(client, db, shipped):
    from app.visa_snapshot.models import KimiRouteGuidanceCache
    r = route('THA','AUS')
    key = kp.cache_key(r)
    db.query(KimiRouteGuidanceCache).filter_by(cache_key=key).delete()
    db.add(KimiRouteGuidanceCache(cache_key=key, route=r, guidance=eta_raw(),
                                 status=kp.STATUS_PRIMARY, model='tripcom-fixture', verification={}))
    db.commit()
    try:
        headers = {'Authorization':'Bearer admin-token','X-Org-Id':'tripcom-test','X-User-Id':'reviewer'}
        response = client.get('/database/records', headers=headers,
                              params={'nationality':'THA','destination':'AUS'})
        assert response.status_code == 200
        rows = response.json()['records']
        assert len(rows)==1
        assert rows[0]['visa_fee_amount']==250 and rows[0]['visa_fee_qualifier']=='from'
        assert rows[0]['max_stay_duration'] is None and '3 months' in rows[0]['max_stay_text']
        assert '600' in rows[0]['visa_type_name'] and not rows[0]['held']
    finally:
        db.query(KimiRouteGuidanceCache).filter_by(cache_key=key).delete()
        db.commit()
