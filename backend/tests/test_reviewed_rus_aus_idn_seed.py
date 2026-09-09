"""Fresh coverage corrections must survive historical contradictory defaults."""
import pytest

from app.visa_snapshot import kimi_primary as engine
from app.visa_snapshot import verified_overrides as overrides


def route(nationality, destination):
    return {'passport_nationality': nationality, 'destination_country': destination,
            'travel_purpose': 'tourism', 'travel_document_type': 'ordinary_passport'}


@pytest.mark.parametrize('nationality', ['GBR', 'FRA', 'ESP'])
def test_evisitor_preserves_calendar_months_and_own_application(nationality):
    raw = {'disposition': 'CONDITIONAL', 'requirement_detail': 'eta_electronic_authorization',
           'visa_category': 'Electronic Travel Authority (601)',
           'permitted_stay_days': 90, 'permitted_stay': '90 days',
           'government_fee': {'amount': 20, 'currency': 'AUD'},
           'official_portal_url': 'https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/visitor-600',
           'visa_products': []}
    g, p = overrides.apply(raw, route(nationality, 'AUS'))
    assert g['disposition'] == 'VISA_REQUIRED'
    assert g['requirement_detail'] == 'evisa'
    assert g['government_fee']['amount'] == 0
    assert g['permitted_stay_days'] is None
    assert '3 months' in g['permitted_stay']
    assert g['official_portal_url'] == 'https://online.immi.gov.au/lusc/login'
    product, = g['visa_products']
    assert '651' in product['type']
    assert product['max_stay_days'] is None
    assert product['validity'] == '12 months from grant'
    assert not engine.serve_time_invariants(g)
    assert p['field_provenance']['disposition']['verifier'] == 'ai'
    if nationality == 'GBR':
        assert any('British Citizen passport' in text for text in g['exceptions'])


@pytest.mark.parametrize('nationality', ['TWN', 'JPN', 'KOR', 'GBR', 'FRA', 'ESP'])
def test_indonesia_arrival_options_keep_their_own_validity_and_documents(nationality):
    g, _ = overrides.apply({'disposition': 'VISA_EXEMPT', 'requirement_detail': 'unconditional_visa_free',
                            'government_fee': {'amount': 0, 'currency': None}}, route(nationality, 'IDN'))
    assert g['disposition'] == 'VISA_ON_ARRIVAL'
    assert g['government_fee'] == {'amount': 500000, 'currency': 'IDR'}
    assert g['permitted_stay_days'] == 30
    products = {p['requirement_detail']: p for p in g['visa_products']}
    paper, electronic = products['paper_visa_on_arrival'], products['evisa_on_arrival']
    assert paper['validity'] is None
    assert electronic['validity'] == '90 days from issue'
    assert not any('photograph' in d.lower() for d in paper['required_documents'])
    assert any('photograph' in d.lower() for d in electronic['required_documents'])
    assert paper['application_channel'] == 'on_arrival'
    assert electronic['application_channel'] == 'online_portal'
    assert g['arrival_card']['required'] is True
    assert g['arrival_card']['name'] == 'All Indonesia integrated arrival declaration'
    assert not engine.serve_time_invariants(g)


@pytest.mark.parametrize('nationality', ['SGP', 'IDN', 'VNM', 'ESP'])
def test_russia_evisa_has_no_invented_fee_or_paper_invitation(nationality):
    g, _ = overrides.apply({'disposition': 'VISA_EXEMPT', 'requirement_detail': 'unconditional_visa_free',
                            'government_fee': {'amount': 0, 'currency': None}}, route(nationality, 'RUS'))
    assert g['disposition'] == 'VISA_REQUIRED'
    assert g['requirement_detail'] == 'evisa'
    assert g['government_fee'] is None
    assert g['permitted_stay_days'] == 30
    product, = g['visa_products']
    assert product['validity'] == '120 days from issue'
    assert product['fee'] is None
    assert not any('invitation' in d.lower() or 'hotel' in d.lower() for d in product['required_documents'])
    assert '4 calendar days' in g['processing_time']
    assert not engine.serve_time_invariants(g)


@pytest.mark.parametrize('nationality,days', [('KOR', 60), ('THA', 30)])
def test_russia_bilateral_ordinary_waivers_do_not_inherit_diplomatic_ninety_days(nationality, days):
    g, _ = overrides.apply({'disposition': 'VISA_REQUIRED', 'permitted_stay_days': 90}, route(nationality, 'RUS'))
    assert g['disposition'] == 'VISA_EXEMPT'
    assert g['permitted_stay_days'] == days
    assert g['visa_products'] == []
    assert not engine.serve_time_invariants(g)
    if nationality == 'KOR':
        assert any('90 days within each 180-day period' in text for text in g['exceptions'])
