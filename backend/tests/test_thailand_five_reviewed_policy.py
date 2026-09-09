"""Current five-route evidence and the reviewed September arrival boundary."""
from copy import deepcopy
from datetime import date
import json

import pytest

from app.visa_snapshot import kimi_primary as kp, scheduled_policies as sp, verified_overrides as vo
from app.visa_snapshot.records_guard import apply_records_hold, held_envelope

NATIONALITIES = ('HKG', 'SGP', 'USA', 'KOR', 'MAC')
TDAC = 'https://tdac.immigration.go.th/manual/en/'
SIXTY = 'https://image.mfa.go.th/mfa/0/qcuTAU8W4b/New_Visa_Measures/Visa_Exemption_60_Days_2024-07-15.pdf'
BILATERAL = 'https://image.mfa.go.th/mfa/0/zE6021nSnu/0303/Bilat_Ordinary.pdf'
TRANSITION = 'https://www.mfa.go.th/en/content/pb-summary-03092026-en'


def reviewed_rows():
    rows = json.loads(vo.OVERRIDES.read_text())
    return [r for r in rows if r.get('route', {}).get('nationality') in NATIONALITIES
            and r['route'].get('destination') == 'THA'
            and r['route'].get('travel_purpose', 'tourism') == 'tourism'
            and r['route'].get('travel_document_type', 'ordinary_passport') == 'ordinary_passport']


@pytest.fixture(autouse=True)
def isolated_review(monkeypatch):
    table = vo._parse_rows(reviewed_rows(), {})
    assert len(table) == 5
    monkeypatch.setattr(vo, '_load_table', lambda: deepcopy(table))
    monkeypatch.setattr(sp, '_today', lambda: date(2026, 9, 9))
    schedules = sp._parse_rows(json.loads(sp.POLICIES.read_text()))
    monkeypatch.setattr(sp, '_load', lambda: deepcopy(schedules))
    vo.reload()
    yield
    vo.reload()


def route(nationality, arrival='2026-09-09'):
    return {'passport_nationality': nationality, 'destination_country': 'THA',
            'travel_purpose': 'tourism', 'travel_document_type': 'ordinary_passport',
            'arrival_date': arrival}


def raw(nationality):
    # Existing cache's stale Macao30/Korea60, paper-card and onward claims.
    # Ancillary facts are placeholders, explicitly outside this source review.
    return {'disposition': 'VISA_EXEMPT', 'requirement_detail': 'unconditional_visa_free',
            'visa_category': 'Tourist visa exemption',
            'permitted_stay_days': 30 if nationality == 'MAC' else 60,
            'permitted_stay': '30 days' if nationality == 'MAC' else '60 days',
            'government_fee': {'amount': None, 'currency': None},
            'application_channel': 'not_required', 'visa_products': [],
            'arrival_card': {'required': True, 'name': 'TM6 paper arrival card', 'submission_window': 'On arrival'},
            'exceptions': ['Air arrivals do not need an arrival card.', 'Extension before the 60-day limit.'],
            'onward_travel_evidence': ('Ticket departing within 30 days' if nationality == 'MAC'
                                     else 'Ticket departing within 60 days') if nationality in ('SGP', 'USA', 'MAC')
                                    else 'Onward ticket may be requested.',
            'passport_validity': 'Existing independent passport wording.',
            'financial_evidence': 'Existing independent financial wording.',
            'required_documents': ['Existing independent document wording.']}


@pytest.mark.parametrize('nationality', NATIONALITIES)
@pytest.mark.parametrize('arrival', ['2026-09-14', '2026-09-15', '2026-10-01'])
def test_five_current_routes_cross_effective_date_without_a_new_model_or_cache_key(nationality, arrival):
    before = raw(nationality)
    original = deepcopy(before)
    r = route(nationality, arrival)
    g, proof = vo.apply(before, r)
    expected = 90 if nationality == 'KOR' else 60 if arrival < '2026-09-15' else 30
    assert g['disposition'] == 'VISA_EXEMPT'
    assert g['requirement_detail'] == 'unconditional_visa_free'
    assert g['permitted_stay_days'] == expected
    assert kp.cache_key(r) == kp.cache_key(route(nationality))
    assert before == original
    assert not kp.serve_time_invariants(g)
    assert not g.get('policy_interval_conflict') and not g.get('scheduled_policy_conflict')
    assert g['arrival_card']['name'] == 'Thailand Digital Arrival Card (TDAC)'
    assert 'paper' in g['arrival_card']['note'] and 'not a visa' in g['arrival_card']['note']
    assert not any('TM6' in x or 'do not need an arrival card' in x for x in g['exceptions'])
    for field in ('passport_validity', 'financial_evidence', 'required_documents'):
        assert g[field] == original[field]
        assert field not in proof['fields'] and field not in proof['field_provenance']
    if nationality in ('SGP', 'USA', 'MAC'):
        assert g['onward_travel_evidence'] is None
        assert 'onward_travel_evidence' not in g.get('unpublished_fields', [])
    verdict_proof = proof['field_provenance']['disposition']
    assert verdict_proof['verifier'] == 'ai'
    if arrival >= '2026-09-15':
        assert verdict_proof['effective_from'] == '2026-09-15'
        assert verdict_proof.get('effective_to') is None
    elif nationality != 'KOR':
        assert verdict_proof['effective_to'] == '2026-09-14'
    else:
        assert not verdict_proof.get('effective_to')
    assert proof['field_provenance']['arrival_card']['source_url'] == TDAC
    assert not proof['field_provenance']['arrival_card'].get('effective_to')


def test_arrival_source_cannot_take_authorship_of_the_verified_verdict():
    for row in reviewed_rows():
        nat = row['route']['nationality']
        p = row['field_provenance']
        assert p['disposition']['source_url'] == (BILATERAL if nat == 'KOR' else SIXTY)
        assert p['arrival_card']['source_url'] == TDAC
        assert 'replace the traditional paper-based arrival card' in ' '.join(p['arrival_card']['additional_quotes'])
        assert 'not visa' in ' '.join(p['arrival_card']['additional_quotes'])
        assert p['arrival_card']['verifier'] == p['disposition']['verifier'] == 'ai'
        if nat != 'KOR':
            bound = p['disposition']['policy_interval_evidence']['effective_to']
            quotes = ' '.join(bound['additional_quotes'])
            assert bound['source_url'] == TRANSITION
            assert 'revoking the 60-day visa exemption scheme' in quotes
            assert 'indicated on the immigration stamp on their passport' in quotes
            assert 'tourism purposes only' in quotes
            assert bound['quote'] == 'These measures will therefore take effect from 15 September 2026'


def test_unrelated_portal_proof_keeps_original_source_date_and_verifier():
    usa = next(row for row in reviewed_rows() if row['route']['nationality'] == 'USA')
    proof = usa['field_provenance']['official_portal_url']
    assert proof['verified_at'] == '2026-08-30'
    assert proof['source_url'] == 'https://tdac.immigration.go.th/arrival-card/#/home'
    assert proof['verifier'] == 'ai'
    assert not proof.get('effective_to')


@pytest.mark.parametrize('change', [{'travel_document_type': 'diplomatic_passport'},
                                    {'travel_document_type': 'prc_travel_document'},
                                    {'travel_purpose': 'business'},
                                    {'destination_country': 'VNM'}])
def test_five_route_sources_do_not_certify_other_purposes_documents_or_destinations(change):
    for nat in NATIONALITIES:
        r = {**route(nat, '2026-09-15'), **change}
        assert vo.find(r) is None
        g, proof = vo.apply(raw(nat), r)
        assert proof is None and not g.get('scheduled_policy')
        assert g['arrival_card']['name'] == 'TM6 paper arrival card'


@pytest.mark.parametrize('arrival', ['2026-09-14', '2026-09-15'])
def test_new_data_does_not_clear_a_separate_dispute_or_release_it(arrival):
    r = route('USA', arrival)
    g, proof = vo.apply(raw('USA'), r)
    result = apply_records_hold(r, {'guidance': g, 'source_verified': proof,
                                   'operator_released': True, 'contradictions': ['Separate official passport-source dispute.']})
    assert result['held'] and result['review_required']
    assert result['contradictions'] == ['Separate official passport-source dispute.']
    assert held_envelope(result)['guidance'] is None
