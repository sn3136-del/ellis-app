"""Arrival-card corrections must propagate without certifying visa policies."""
import json
from pathlib import Path
import pytest
from app.visa_snapshot import verified_overrides as vo
from app.visa_snapshot.records_guard import apply_records_hold

ROOT = Path(__file__).parents[2]
SEED = ROOT / 'data/database_seed/verified_overrides.json'
ROWS = json.loads(SEED.read_text())
FOREIGN = ['JPN', 'KOR', 'USA', 'THA', 'SGP', 'MYS', 'GBR', 'RUS', 'AUS',
           'IDN', 'PHL', 'FRA', 'VNM', 'ESP', 'IND', 'CAN', 'DEU']
URL = 'https://twac.immigration.gov.tw/'


def route(nat, purpose='tourism', document='ordinary_passport'):
    return {'passport_nationality': nat, 'destination_country': 'TWN',
            'travel_purpose': purpose, 'travel_document_type': document}


@pytest.fixture
def current_seed(monkeypatch, tmp_path):
    monkeypatch.setattr(vo, 'OVERRIDES', SEED)
    monkeypatch.setenv('ELLIS_OPERATOR_OVERRIDES', str(tmp_path / 'no-operators.json'))
    vo.reload()
    yield
    vo.reload()


@pytest.mark.parametrize('nat', FOREIGN)
def test_foreign_tourist_card_survives_partial_override_quarantine(nat, current_seed):
    merged, provenance = vo.apply({'arrival_card': {'required': True,
        'name': 'Online or paper card', 'submission_window': 'within 3 days before arrival'}}, route(nat))
    card = merged['arrival_card']
    assert card['required'] is True and card['url'] == URL
    assert '7 days' in card['submission_window'] and '3 days' not in str(card)
    assert 'paper' not in str(card).lower()
    assert all(term in card['notes'] for term in
               ('Alien Resident Certificate', 'Resident Visa', 'Diplomatic ID card'))
    proof = provenance['field_provenance']['arrival_card']
    assert proof['verifier'] == 'ai' and proof['verified_at'] == '2026-09-09'
    assert proof['source_url'] == URL


@pytest.mark.parametrize('nat', ['HKG', 'CHN'])
def test_permit_categories_are_conditional_not_blanket_nationality_rules(nat, current_seed):
    merged, _ = vo.apply({}, route(nat))
    card = merged['arrival_card']
    assert card['required'] is None
    assert 'multiple' in card['notes'].lower() and 'permit' in card['notes'].lower()
    assert '7 days' in card['submission_window']
    if nat == 'CHN':
        assert 'purpose of travel' in card['notes'] and 'nationality alone' in card['notes']


def test_ancillary_australia_entry_does_not_verify_or_release_its_visa_verdict(current_seed):
    hit = vo.find(route('AUS'))
    assert set(hit['fields']) == {'arrival_card'}
    raw = {'disposition': 'VISA_EXEMPT', 'requirement_detail': 'unconditional_visa_free',
           'source_url': 'https://www.boca.gov.tw/cp-149-4486-7785a-2.html',
           'visa_products': [], 'confidence': 'high'}
    merged, provenance = vo.apply(raw, route('AUS'))
    assert merged['disposition'] == raw['disposition']
    assert 'disposition' not in provenance['fields']
    held = apply_records_hold(route('AUS'), {'guidance': merged, 'source_verified': provenance})
    assert held['held']


def test_new_arrival_proof_does_not_relabel_usa_sibling_fields(current_seed, monkeypatch):
    # Isolate the arrival-only revision. The later independent BOCA review
    # legitimately corrects USA eligibility/passport fields with its own proof.
    listed = vo._listed_reviewed_overlay_names()
    monkeypatch.setattr(vo, '_listed_reviewed_overlay_names', lambda: [
        name for name in listed
        if name != 'reviewed_indonesia_taiwan_overlay_20260910.json'])
    vo.reload()
    hit = vo.find(route('USA'))
    assert 'disposition' not in hit['fields']
    assert hit['field_provenance']['arrival_card']['verified_at'] == '2026-09-09'
    sibling = {k: p for k, p in hit['field_provenance'].items() if k != 'arrival_card'}
    assert sibling and all(p['verified_at'] == '2026-08-22' for p in sibling.values())
    assert all(p['source_url'] != URL for p in sibling.values())


def test_non_tourism_and_diplomatic_routes_do_not_inherit_tourist_card(current_seed):
    for nat in ['HKG', 'CHN', 'USA']:
        for context in [('work', 'ordinary_passport'), ('study', 'ordinary_passport'),
                        ('tourism', 'diplomatic_passport')]:
            hit = vo.find(route(nat, *context))
            assert not hit or (hit.get('field_provenance', {}).get('arrival_card', {})
                              .get('source_url') != URL)


def test_later_boca_review_keeps_twac_proof_separate(current_seed):
    hit = vo.find(route('USA'))
    proofs = hit['field_provenance']
    assert proofs['arrival_card']['source_url'] == URL
    assert proofs['arrival_card']['verified_at'] == '2026-09-09'
    for field in ('disposition', 'passport_validity_requirement', 'required_documents'):
        assert proofs[field]['source_url'] == 'https://www.boca.gov.tw/cp-149-4486-7785a-2.html'
        assert proofs[field]['verified_at'] == '2026-09-10'
        assert proofs[field]['source_url'] != proofs['arrival_card']['source_url']
