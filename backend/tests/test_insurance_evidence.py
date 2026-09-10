from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.visa_snapshot import insurance_evidence as insurance, records_guard, tstation

DATA = Path(__file__).resolve().parents[2] / 'data/database_seed'
ROUTE = {'passport_nationality': 'HKG', 'destination_country': 'MYS',
         'travel_purpose': 'tourism', 'travel_document_type': 'ordinary_passport'}
QUOTE = 'Travel insurance will also not be a prerequisite for foreigners entering the country.'


def proof(value=False, **changes):
    base = {'source_url': 'https://www.tourism.gov.my/media/view/malaysia-relaxes-covid-19-testing-rules-travel-insurance-for-inbound-travellers',
            'verified_at': '2026-09-10', 'verified_by': 'Existing scoped review', 'verifier': 'ai',
            'note': 'This exact insurance entry field only.', 'status': 'reviewed',
            'subject': deepcopy(ROUTE), 'quote': QUOTE if not value else
            'Travel insurance is required for foreign tourists entering the country.'}
    base.update(changes)
    return base


def reader(value=False, own=None):
    return {'guidance': {'insurance_required': value, 'required_documents': ['Keep all explicit source-backed documents'],
                         'entry_requirements': 'Keep all entry conditions.', 'visa_products': []},
            'source_verified': {'fields': ['insurance_required'], 'field_provenance': {'insurance_required': proof() if own is None else own}},
            'status': 'KIMI_PRIMARY', 'stale': False, 'detail_pending': False,
            'grounded_check': {'at': '2026-09-10', 'consistent': True}, 'fresh_until': '2026-09-20'}


@pytest.mark.parametrize('value', [False, True])
def test_exact_scoped_entry_proof_retains_boolean_and_source_metadata_without_redating(value):
    original = reader(value, proof(value)); before = deepcopy(original)
    out = insurance.project_reader(ROUTE, original)
    assert out['guidance']['insurance_required'] is value
    state = out['requirement_evidence']['insurance_required']
    assert state['status'] == 'verified' and state['scope'] == 'entry' and state['value'] is value
    assert state['verified_at'] == '2026-09-10'
    assert out['source_verified'] == original['source_verified'] and original == before
    assert insurance.project_reader(ROUTE, out) == out


@pytest.mark.parametrize('mutation', ['no_proof', 'top_level_only', 'unknown', 'partial', 'np', 'future_review', 'wrong_country',
                                    'wrong_passport', 'wrong_doc', 'wrong_purpose', 'sibling_product', 'missing_subject',
                                    'no_quote', 'note_only', 'other_jurisdiction', 'nonofficial', 'expired', 'future_policy',
                                    'malformed_policy', 'stale', 'source_contradiction', 'string_boolean', 'no_claimed_field',
                                    'wrong_stage', 'conditional', 'recommended_only', 'museum_admission',
                                    'malformed_status', 'unselected_condition', 'wrong_residence',
                                    'later_contradiction', 'contrast_clause'])
def test_unproven_or_wrong_scope_boolean_is_genuinely_unknown(mutation):
    original = reader(); p = original['source_verified']['field_provenance']['insurance_required']
    if mutation == 'no_proof': original['source_verified']['field_provenance'] = {}
    elif mutation == 'top_level_only': original['source_verified'] = dict(p, fields=['insurance_required'])
    elif mutation in ('unknown', 'partial', 'np'): p['status'] = {'np': 'not_published'}.get(mutation, mutation)
    elif mutation == 'future_review': p['verified_at'] = '2999-01-01'
    elif mutation == 'wrong_country': p['subject']['destination_country'] = 'IDN'
    elif mutation == 'wrong_passport': p['subject']['passport_nationality'] = 'USA'
    elif mutation == 'wrong_doc': p['subject']['travel_document_type'] = 'diplomatic_passport'
    elif mutation == 'wrong_purpose': p['subject']['travel_purpose'] = 'business'
    elif mutation == 'sibling_product': p['subject']['product_type'] = 'Student visa'
    elif mutation == 'missing_subject': p.pop('subject')
    elif mutation == 'no_quote': p.pop('quote')
    elif mutation == 'note_only': p['note'] = p.pop('quote')
    elif mutation == 'other_jurisdiction': p['source_url'] = 'https://immi.homeaffairs.gov.au/'
    elif mutation == 'nonofficial': p['source_url'] = 'https://insurance.example.com/'
    elif mutation == 'expired': p['effective_to'] = '2000-01-01'
    elif mutation == 'future_policy': p['effective_from'] = '2999-01-01'
    elif mutation == 'malformed_policy': p['effective_to'] = 'soon'
    elif mutation == 'stale': original['stale'] = True
    elif mutation == 'source_contradiction': p['quote'] = proof(True)['quote']
    elif mutation == 'string_boolean': original['guidance']['insurance_required'] = 'false'
    elif mutation == 'no_claimed_field': original['source_verified']['fields'] = ['disposition']
    elif mutation == 'wrong_stage': p['quote'] = 'Travel insurance is not required for visa applications.'
    elif mutation == 'conditional': p['quote'] = 'Travel insurance is not required for entry if vaccinated.'
    elif mutation == 'recommended_only': p['quote'] = 'Travel insurance is recommended for entry.'
    elif mutation == 'museum_admission': p['quote'] = 'Travel insurance is not required for museum admission.'
    elif mutation == 'malformed_status': p['status'] = {'reviewed': True}
    elif mutation == 'unselected_condition': p['subject']['vaccinated'] = True
    elif mutation == 'wrong_residence': p['subject']['lawful_country_of_residence'] = 'USA'
    elif mutation == 'later_contradiction': p['quote'] = QUOTE + ' It is mandatory for every visitor.'
    elif mutation == 'contrast_clause': p['quote'] = QUOTE.rstrip('.') + ', but it is mandatory for every visitor.'
    before = deepcopy(original)
    out = insurance.project_reader(ROUTE, original)
    assert out['guidance']['insurance_required'] is None
    assert out['requirement_evidence']['insurance_required']['status'] == 'unknown'
    assert not out['requirement_evidence']['insurance_required'].get('source_url')
    assert out['source_verified'] == original['source_verified'] and original == before
    assert out['guidance']['required_documents'] == original['guidance']['required_documents']
    assert out['guidance']['entry_requirements'] == original['guidance']['entry_requirements']


def test_real_reviewed_malaysia_false_proofs_remain_confirmed():
    for filename in ('reviewed_major_malaysia_overlay_20260910.json', 'reviewed_malaysia_field_overlay_idn20260910.json'):
        for row in json.loads((DATA / filename).read_text())['entries']:
            p = row['field_provenance']['insurance_required']
            route = deepcopy(p['subject'])
            out = insurance.project_reader(route, reader(False, p))
            assert out['guidance']['insurance_required'] is False
            assert out['requirement_evidence']['insurance_required']['status'] == 'verified'


def test_actual_canonical_malaysia_override_reader_retains_scoped_false_proofs():
    from app.visa_snapshot import verified_overrides as vo
    major = json.loads((DATA / 'reviewed_major_malaysia_manifest_20260910.json').read_text())
    idn = json.loads((DATA / 'reviewed_malaysia_field_manifest_idn20260910.json').read_text())
    for baseline in [row['baseline'] for row in major['routes']] + [idn['baseline']]:
        route = baseline['route']
        g, prov = vo.apply(deepcopy(baseline['raw_guidance']), route)
        before = deepcopy(g)
        out = records_guard.apply_records_hold(route, {'guidance': g, 'source_verified': prov,
            'held': False, 'detail_pending': False, 'stale': False, 'grounded_check': {}})
        assert not out.get('held')
        assert out['guidance']['insurance_required'] is False
        assert out['requirement_evidence']['insurance_required']['status'] == 'verified'
        assert out['source_verified'] == prov and g == before


@pytest.mark.parametrize('value,quote', [
    (False, 'Travel insurance is optional for booking a guided tour before entering the country.'),
    (True, 'Travel insurance is required for a residence permit before entering the country.'),
    (False, 'Travel insurance is not required to visit the museum after entering the country.'),
])
def test_incidental_country_entry_cannot_relabel_an_unrelated_service(value, quote):
    original = reader(value, proof(value, quote=quote))
    out = insurance.project_reader(ROUTE, original)
    assert out['guidance']['insurance_required'] is None
    assert out['requirement_evidence']['insurance_required']['status'] == 'unknown'


@pytest.mark.parametrize('value,quote', [
    (False, 'Travel insurance is not required for entry into the country.'),
    (True, 'Foreign tourists must hold valid medical insurance to enter the country.'),
    (False, '入境该国无需购买旅行保险。'),
    (True, '入境該國必須持有旅行保險。'),
])
def test_direct_country_entry_predicate_remains_supported(value, quote):
    out = insurance.project_reader(ROUTE, reader(value, proof(value, quote=quote)))
    assert out['guidance']['insurance_required'] is value
    assert out['requirement_evidence']['insurance_required']['scope'] == 'entry'


@pytest.mark.parametrize('kind', ['same', 'sibling', 'parent', 'own_unknown'])
def test_product_owns_insurance_and_never_borrows_route_or_sibling_proof(kind):
    original = reader(); own = proof(True)
    own['subject']['product_type'] = 'Tourist visa'
    own['quote'] = 'Travel insurance is required for visa applications.'
    product = {'type': 'Tourist visa', 'insurance_required': True, 'notes': 'Keep actual conditions',
               'field_provenance': {'insurance_required': own}}
    if kind == 'sibling': own['subject']['product_type'] = 'Student visa'
    elif kind == 'parent': product['field_provenance'] = {}
    elif kind == 'own_unknown': own['status'] = 'unknown'
    original['guidance']['visa_products'] = [product]
    before = deepcopy(original)
    out = insurance.project_reader(ROUTE, original)
    projected = out['guidance']['visa_products'][0]
    assert projected['insurance_required'] is (True if kind == 'same' else None)
    assert projected['notes'] == product['notes'] and projected['field_provenance'] == product['field_provenance']
    assert out['guidance']['insurance_required'] is False
    assert original == before


@pytest.mark.parametrize('flag', ['held', 'detail_pending'])
def test_existing_hold_or_pending_is_untouched_and_held_response_hides_evidence(flag):
    original = reader(); original[flag] = True
    original['requirement_evidence'] = {'arbitrary': 'untrusted'}
    out = insurance.project_reader(ROUTE, original)
    assert out == original
    assert records_guard.held_envelope(dict(out, held=True))['guidance'] is None
    assert 'requirement_evidence' not in records_guard.held_envelope(dict(out, held=True))


@pytest.mark.parametrize('index', range(3))
def test_captured_live_l_routes_no_longer_publish_unproven_insurance_exemptions(index):
    rows = json.loads((Path(__file__).parent / 'fixtures/insurance_unproven_live_routes.json').read_text())['layers']
    row = rows[index]; g = row['merged_guidance']; prov = row['source_provenance']; route = row['route']
    assert g['insurance_required'] is False and 'insurance_required' not in prov['field_provenance']
    original = {'guidance': deepcopy(g), 'source_verified': deepcopy(prov), 'held': False,
                'detail_pending': False, 'stale': False, 'grounded_check': {}}
    before = deepcopy(original)
    out = records_guard.apply_records_hold(route, original)
    assert not out.get('held'), row['cache_key']
    assert out['guidance']['insurance_required'] is None
    assert out['requirement_evidence']['insurance_required']['status'] == 'unknown'
    assert {k:v for k,v in out['guidance'].items() if k != 'insurance_required'} == {
        k:v for k,v in g.items() if k != 'insurance_required'}
    assert out['source_verified'] == prov and original == before
    # Insurance is not its own25-column cell. No Not published/Not applicable
    # credit or confidence is manufactured by this display correction.
    assert tstation.records_for_route(route, g, prov) == tstation.records_for_route(route, out['guidance'], prov)


@pytest.mark.parametrize('field', ['disposition', 'insurance_required', 'required_documents'])
def test_active_policy_conflict_does_not_become_an_insurance_only_unknown(field):
    row = json.loads((Path(__file__).parent / 'fixtures/insurance_unproven_live_routes.json').read_text())['layers'][0]
    out = records_guard.apply_records_hold(row['route'], {'guidance': deepcopy(row['merged_guidance']),
        'source_verified': row['source_provenance'], 'grounded_check': {'disputed_fields': [field]}})
    assert out['held'] and records_guard.held_envelope(out)['guidance'] is None


@pytest.fixture
def without_later_field_corrections(monkeypatch):
    # The fixture pins the served provenance as captured before the later
    # field-correction release changed HKG to IDN's arrival card under its
    # own exact-layer contract; measure the insurance projection without it.
    from app.visa_snapshot import verified_overrides as vo
    listed = vo._listed_reviewed_overlay_names()
    monkeypatch.setattr(vo, '_listed_reviewed_overlay_names',
                        lambda: [n for n in listed if n != 'reviewed_field_corrections_overlay_20260910.json'])
    vo.reload()
    yield
    vo.reload()


@pytest.mark.parametrize('index', range(3))
@pytest.mark.parametrize('pending', [False, True])
def test_actual_cached_api_keeps_raw_boolean_dates_proofs_and_pending(db, client, monkeypatch, index, pending, without_later_field_corrections):
    from datetime import datetime, timedelta, timezone
    from app import main
    from app.visa_snapshot import kimi_primary as kp
    from app.visa_snapshot.models import KimiRouteGuidanceCache
    row = json.loads((Path(__file__).parent / 'fixtures/insurance_unproven_live_routes.json').read_text())['layers'][index]
    db.query(KimiRouteGuidanceCache).delete(); db.commit()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    record = KimiRouteGuidanceCache(cache_key=row['cache_key'], route=deepcopy(row['route']),
        guidance=deepcopy(row['raw_guidance']), status='KIMI_PRIMARY', model='captured-fixture',
        generated_at=now, fresh_until=now+timedelta(days=1),
        verification={'detail_pending': pending, 'unrelated_history': 'keep'}, missing_fields=[], contradictions=[])
    db.add(record); db.commit()
    original = {key:deepcopy(getattr(record, key)) for key in
                ('guidance', 'route', 'generated_at', 'fresh_until', 'verification', 'status')}
    monkeypatch.setattr(kp, 'is_available', lambda: True)
    monkeypatch.setattr(kp, '_call', lambda *a,**kw: pytest.fail('No model regeneration'))
    monkeypatch.setattr(main, '_ground_on_access', lambda *a,**kw: None)
    monkeypatch.setenv('ELLIS_HOLD_UNCERTAIN', '1')
    response = client.post('/database/lookup', headers={'authorization':'Bearer dev-token',
        'x-org-id':'insurance-review','x-user-id':'reader'}, json={
        'nationality':row['route']['passport_nationality'], 'destination':row['route']['destination_country'],
        'travel_document_type':'ordinary_passport', 'travel_purpose':'tourism'})
    assert response.status_code == 200, response.text
    out = response.json()
    if pending:
        assert out['held'] and out['guidance'] is None and 'requirement_evidence' not in out
    else:
        assert not out.get('held') and out['guidance']['insurance_required'] is None
        assert out['requirement_evidence']['insurance_required']['status'] == 'unknown'
        assert out['source_verified'] == row['source_provenance']
    db.expire_all(); db.refresh(record)
    assert {key:deepcopy(getattr(record,key)) for key in original} == original
