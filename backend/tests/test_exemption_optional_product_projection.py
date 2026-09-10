from copy import deepcopy

from app.visa_snapshot import tstation
from app.visa_snapshot.kimi_primary import serve_time_invariants


def test_explicit_free_lane_and_optional_evisas_survive_projection_independently():
    route = {'passport_nationality': 'GBR', 'destination_country': 'VNM',
             'travel_purpose': 'tourism', 'travel_document_type': 'ordinary_passport'}
    guidance = {'disposition': 'VISA_EXEMPT', 'requirement_detail': 'unconditional_visa_free',
        'permitted_stay_days': 45, 'policy_valid_until': '2028-03-14',
        'source_url': 'https://mofa.gov.vn/exemption',
        'required_documents': ['Free-lane-specific document'],
        'visa_products': [
            {'type': '45-day visa-free visit', 'disposition': 'VISA_EXEMPT',
             'requirement_detail': 'unconditional_visa_free', 'max_stay_days': 45,
             'fee': {'amount': 0, 'currency': 'USD'}},
            {'type': 'Single-entry tourist e-visa', 'disposition': 'VISA_REQUIRED',
             'requirement_detail': 'evisa', 'entry': 'single', 'validity': '90 days',
             'max_stay_days': 90, 'fee': {'amount': 25, 'currency': 'USD'},
             'source_url': 'https://evisa.gov.vn/'},
            {'type': 'Multiple-entry tourist e-visa', 'disposition': 'VISA_REQUIRED',
             'requirement_detail': 'evisa', 'entry': 'multiple', 'validity': '90 days',
             'max_stay_days': 90, 'fee': {'amount': 50, 'currency': 'USD'},
             'source_url': 'https://evisa.gov.vn/'}]}
    original = deepcopy(guidance)
    assert serve_time_invariants(guidance) == []
    rows = tstation.records_for_route(route, guidance)
    assert len(rows) == 3 and guidance == original
    assert [(r['visa_requirement'], r['visa_fee_amount']) for r in rows] == [
        ('Visa-free', 0), ('Visa Required in Advance', 25), ('Visa Required in Advance', 50)]
    assert [r['max_stay_duration'] for r in rows] == [45, 90, 90]
    assert rows[0]['info_validity'] == '2028-03-14'
    for row in rows[1:]:
        assert row['visa_requirement_detail'] == 'eVisa'
        assert row['info_validity'] is None
        assert row['required_documents'] is None
        assert row['collected_at'] is None
        assert row['_product_source_verified'] is None
        assert row['confidence_level'] == 'Low'


def test_priced_products_without_a_free_lane_remain_an_integrity_conflict():
    guidance = {'disposition': 'VISA_EXEMPT', 'requirement_detail': 'unconditional_visa_free',
        'visa_products': [{'type': 'Tourist e-visa', 'requirement_detail': 'evisa',
                           'fee': {'amount': 25, 'currency': 'USD'}}]}
    assert any('every listed visa product is priced' in issue for issue in serve_time_invariants(guidance))
