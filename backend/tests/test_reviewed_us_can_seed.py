"""Reviewed nationalities, exceptions and calendar units survive old defaults."""
import pytest
from app.visa_snapshot import verified_overrides as vo, kimi_primary as engine

def merged(nationality, destination):
    old = {'disposition': 'VISA_EXEMPT', 'government_fee': {'amount': 0, 'currency': 'USD'},
           'permitted_stay_days': 180, 'permitted_stay': '180 days', 'visa_products': []}
    route = {'passport_nationality': nationality, 'destination_country': destination,
             'travel_purpose': 'tourism', 'travel_document_type': 'ordinary_passport'}
    g, _ = vo.apply(old, route)
    assert not engine.serve_time_invariants(g)
    return g

@pytest.mark.parametrize('nationality', ['MYS', 'RUS', 'IDN', 'PHL', 'VNM', 'IND'])
def test_us_visitor_application_fee_does_not_invent_admission_or_validity(nationality):
    g = merged(nationality, 'USA')
    assert g['disposition'] == 'VISA_REQUIRED'
    assert g['government_fee'] == {'amount': 185, 'currency': 'USD'}
    assert g['permitted_stay_days'] is None and g['permitted_stay'] is None
    p, = g['visa_products']
    assert p['validity'] is None and p['entry'] is None
    assert 'issuance fee' in p['notes']
    assert any('Customs and Border Protection' in x for x in g['entry_requirements'])

@pytest.mark.parametrize('nationality', ['KOR', 'GBR', 'AUS', 'FRA'])
def test_canada_eta_preserves_mode_exceptions_and_calendar_months(nationality):
    g = merged(nationality, 'CAN')
    assert g['disposition'] == 'ELECTRONIC_AUTHORIZATION_REQUIRED'
    assert g['government_fee'] == {'amount': 7, 'currency': 'CAD'}
    assert g['permitted_stay_days'] is None and '6 months' in g['permitted_stay']
    assert any('Saint-Pierre-et-Miquelon' in x for x in g['exceptions'])
    if nationality == 'FRA':
        assert any('resident' in x and 'directly' in x and 'air or boat' in x for x in g['exceptions'])
    if nationality == 'GBR':
        assert any('British citizen' in x for x in g['exceptions'])

@pytest.mark.parametrize('nationality', ['MYS', 'IDN'])
def test_canada_conditional_eta_does_not_replace_baseline_visitor_visa(nationality):
    g = merged(nationality, 'CAN')
    assert g['disposition'] == 'VISA_REQUIRED'
    assert g['government_fee'] == {'amount': 100, 'currency': 'CAD'}
    assert g['permitted_stay_days'] is None and '6 months' in g['permitted_stay']
    assert any('past 10 years' in x and 'day you apply' in x for x in g['exceptions'])
    assert any('does not cover' in x and 'cruise' in x for x in g['exceptions'])
    assert g['visa_products'][0]['validity'] is None
