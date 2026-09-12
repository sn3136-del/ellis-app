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


# ---------------------------------------------------------------------------
# guard-20260912 T1: the four routes Trip.com reported, pinned before any
# rule moves. Malaysia to Russia and Hong Kong to Vietnam had no test of
# their own until here, so either could have reverted with the suite green.
# ---------------------------------------------------------------------------

def _mys_rus_raw():
    # The answer as the engine stored it on the live row: an unconditional
    # exemption with no products, which is Trip.com finding 2.
    return dict(disposition='VISA_EXEMPT', requirement_detail='unconditional_visa_free',
                visa_category='Tourist / visa-free entry', source_url=None, official_portal_url=None,
                government_fee=None, permitted_stay='30 days per visit; maximum 90 days in any 180-day period',
                permitted_stay_days=30, visa_products=[], application_channel='not_required',
                processing_time=None)


def _hkg_vnm_raw():
    return dict(disposition='VISA_REQUIRED', requirement_detail='evisa', visa_category='tourist e-Visa',
                source_url='https://evisa.gov.vn/', official_portal_url='https://evisa.gov.vn/',
                government_fee={'amount': None, 'currency': None}, permitted_stay='90 days',
                permitted_stay_days=90, application_channel='online_portal', processing_time='3 working days',
                visa_products=[
                    {'type': 'Single-entry tourist e-Visa', 'entry': 'single', 'validity': '90 days',
                     'max_stay_days': 90, 'fee': {'amount': 25, 'currency': 'USD'}, 'notes': 'Apply online'},
                    {'type': 'Multiple-entry tourist e-Visa', 'entry': 'multiple', 'validity': '90 days',
                     'max_stay_days': 90, 'fee': {'amount': 50, 'currency': 'USD'}, 'notes': 'Apply online'},
                    {'type': 'Visa on arrival after pre-approved letter', 'entry': 'single',
                     'validity': 'Varies by approval letter', 'max_stay_days': None,
                     'fee': {'amount': None, 'currency': None}, 'notes': 'Requires pre-approved visa letter'}])


def test_malaysian_tourist_to_russia_is_unified_evisa_not_visa_free(shipped):
    from urllib.parse import urlparse
    g, provenance = vo.apply(_mys_rus_raw(), route('MYS', 'RUS'))
    assert g['disposition'] == 'VISA_REQUIRED' and g['requirement_detail'] == 'evisa'
    products = g['visa_products']
    assert len(products) == 1
    assert 'unified electronic visa' in products[0]['type'].lower()
    assert products[0]['max_stay_days'] == 30 and g['permitted_stay_days'] == 30
    assert products[0]['validity'].startswith('120 days')
    assert products[0]['entry'] == 'single'
    assert urlparse(g['source_url']).hostname == 'evisa.kdmid.ru'
    assert kp.serve_time_invariants(g) == []
    records = tstation.records_for_route(route('MYS', 'RUS'), g, provenance=provenance)
    assert len(records) == 1
    assert records[0]['visa_requirement'] == 'Visa Required in Advance'
    assert records[0]['visa_requirement_detail'] == 'eVisa'
    assert records[0]['max_stay_duration'] == 30 and records[0]['validity_duration'] == 120
    assert records[0]['entries'] == 'Single'


def test_hongkong_to_vietnam_serves_one_evisa_lane(shipped):
    g, provenance = vo.apply(_hkg_vnm_raw(), route('HKG', 'VNM'))
    assert g['disposition'] == 'VISA_REQUIRED' and g['requirement_detail'] == 'evisa'
    exemption_words = ('visa-free', 'visa free', 'no visa', 'exempt', 'waiver', 'without a visa')
    for p in g['visa_products']:
        words = f"{p.get('type') or ''} {p.get('notes') or ''}".lower()
        assert not any(w in words for w in exemption_words), p
    fees = sorted((p['fee']['amount'], p['fee']['currency']) for p in g['visa_products'])
    assert fees == [(25, 'USD'), (50, 'USD')]
    assert kp.serve_time_invariants(g) == []
    r = route('HKG', 'VNM')
    assert kp.canonical_key(kp.cache_key(r)) == kp.cache_key(r)
    records = tstation.records_for_route(r, g, provenance=provenance)
    assert [x['visa_requirement'] for x in records] == ['Visa Required in Advance'] * 2
    assert sorted(x['visa_fee_amount'] for x in records) == [25, 50]


@pytest.mark.parametrize('nationality,destination', [('IDN', 'KOR'), ('MYS', 'RUS'),
                                                     ('THA', 'AUS'), ('HKG', 'VNM')])
def test_reported_routes_serve_from_one_canonical_row(nationality, destination):
    base = route(nationality, destination)
    key = kp.cache_key(base)
    assert kp.is_canonical_key(key) and kp.canonical_key(key) == key
    assert kp.cache_key(dict(base, arrival_date='2026-12-01')) == key
    assert kp.cache_key(dict(base, arrival_date='2027-03-15')) == key
    assert kp.cache_key(dict(base, lawful_country_of_residence='ARE')) == key
    assert kp.cache_key(dict(base, lawful_country_of_residence='ARE', arrival_date='2026-12-01')) == key
