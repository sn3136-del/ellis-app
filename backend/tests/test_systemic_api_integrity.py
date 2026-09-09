"""Recurrence tests for route identity and every reader gate."""
import pytest
from app import main
from app.visa_snapshot import kimi_primary as kp, verified_overrides as vo

READER = {'authorization': 'Bearer dev-token', 'x-org-id': 'integrity', 'x-user-id': 'reader'}
ADMIN = dict(READER, authorization='Bearer admin-token')
ROUTE = {'passport_nationality': 'ISL', 'destination_country': 'FSM', 'travel_purpose': 'tourism'}
GUIDANCE = {'disposition': 'VISA_REQUIRED', 'requirement_detail': 'paper_visa',
            'source_url': 'https://www.mofa.go.jp/visa', 'confidence': 'high',
            'government_fee': {'amount': 25, 'currency': 'USD'},
            'visa_products': [{'type': 'Tourist visa', 'fee': {'amount': 25, 'currency': 'USD'}}]}


@pytest.fixture(autouse=True)
def no_seed(tmp_path, monkeypatch):
    path = tmp_path / 'overrides.json'
    path.write_text('[]')
    monkeypatch.setattr(vo, 'OVERRIDES', path)
    monkeypatch.setenv('ELLIS_OPERATOR_OVERRIDES', str(tmp_path / 'operator.json'))
    vo.reload()
    monkeypatch.setattr(kp, 'is_available', lambda: True)
    yield
    kp.set_provider(None)
    vo.reload()


def test_all_product_rows_are_graded_and_pending_core_is_held(monkeypatch):
    from app.visa_snapshot import tstation
    monkeypatch.setattr(tstation, 'records_for_route', lambda *a, **k: [
        {'confidence_level': 'Medium'}, {'confidence_level': 'Low'}])
    assert main._apply_records_hold(ROUTE, {'guidance': GUIDANCE})['held']
    monkeypatch.setattr(tstation, 'records_for_route', lambda *a, **k: [{'confidence_level': 'High'}])
    assert main._apply_records_hold(ROUTE, {'guidance': GUIDANCE, 'detail_pending': True})['held']


def test_release_cannot_bypass_new_dispute_or_contradiction():
    for out in ({'guidance': GUIDANCE, 'grounded_check': {'disputed_fields': ['permitted_stay_days']}},
                {'guidance': dict(GUIDANCE, disposition='VISA_EXEMPT')}):
        assert main._apply_records_hold(ROUTE, dict(out, operator_released=True))['held']


def test_lookup_ask_and_research_share_the_gate(client, monkeypatch):
    monkeypatch.setattr(kp, 'get_route_guidance', lambda *a, **k: {
        'status': kp.STATUS_PRIMARY, 'guidance': dict(GUIDANCE), 'held': False})
    from app.visa_snapshot import freshness
    monkeypatch.setattr(freshness, 'recheck_route', lambda *a, **k: {'outcome': 'fetch_failed'})
    look = client.post('/database/lookup', headers=READER, json={'nationality': 'ISL', 'destination': 'FSM'}).json()
    ask = client.post('/database/ask', headers=READER, json={'question': 'from Iceland to Japan for tourism'}).json()
    research = client.post('/database/routes/research', headers=ADMIN, json={'nationality': 'ISL', 'destination': 'FSM'}).json()
    assert look['held'] and look['guidance'] is None
    assert ask['held'] and ask['guidance'] is None
    assert research['held'] and research['disposition'] is None


def test_context_cannot_change_shared_prompt_or_identity():
    base = dict(ROUTE, lawful_country_of_residence='ISL')
    contextual = dict(base, arrival_date='2027-03-01', departure_date='2027-05-01',
                      lawful_country_of_residence='SGP', consular_jurisdiction='shanghai',
                      departure_city='Shanghai', age=17, passport_expiry_date='2027-01-01')
    assert kp.cache_key(base) == kp.cache_key(contextual)
    assert kp.build_prompt(base) == kp.build_prompt(contextual)
    legacy = kp.cache_key(base).replace('ISL|ISL|', 'ISL|SGP|').replace('|default|unknown|', '|shanghai|2027-03|')
    assert not kp.is_canonical_key(legacy)
    assert kp.canonical_key(legacy) == kp.cache_key(base)


def test_residence_normalizes_and_invalid_residence_is_rejected(client, monkeypatch):
    routes = []
    def answer(db, route, **kw):
        routes.append(route)
        return {'guidance': {}, 'held': False}
    monkeypatch.setattr(kp, 'get_route_guidance', answer)
    assert client.post('/database/lookup', headers=READER, json={
        'nationality': 'ISL', 'destination': 'FSM', 'residence': 'Singapore'}).status_code == 200
    assert routes[-1]['lawful_country_of_residence'] == 'SGP'
    assert client.post('/database/lookup', headers=READER, json={
        'nationality': 'ISL', 'destination': 'FSM', 'residence': 'Atlantis'}).status_code == 422


def test_cross_month_lookups_use_one_row(client, db):
    from app.visa_snapshot.models import KimiRouteGuidanceCache
    db.query(KimiRouteGuidanceCache).filter(KimiRouteGuidanceCache.cache_key.like('ISL|%|NRU|%')).delete(synchronize_session=False)
    db.commit()
    calls = []
    def provider(system, user):
        calls.append(user)
        return dict(GUIDANCE, visa_category='Tourist visa', permitted_stay='30 days',
                    passport_validity='6 months', required_documents=['Passport'],
                    application_channel='embassy', processing_time='5 days')
    kp.set_provider(provider)
    first = client.post('/database/lookup', headers=READER, json={'nationality': 'ISL', 'destination': 'NRU'}).json()
    count = len(calls)
    second = client.post('/database/lookup', headers=READER, json={'nationality': 'ISL', 'destination': 'NRU',
                         'arrival_date': '2027-03-01', 'residence': 'Singapore'}).json()
    assert first['cache_key'] == second['cache_key'] and second['cached']
    assert len(calls) == count
    assert db.query(KimiRouteGuidanceCache).filter(KimiRouteGuidanceCache.cache_key.like('ISL|%|NRU|%')).count() == 1


def test_orphan_legacy_variants_never_become_current_console_records(db):
    from datetime import datetime
    from app.visa_snapshot.models import KimiRouteGuidanceCache
    route = dict(ROUTE, destination_country='NRU')
    base = kp.cache_key(route)
    db.query(KimiRouteGuidanceCache).filter(
        KimiRouteGuidanceCache.cache_key.like('ISL|%|NRU|%')).delete(synchronize_session=False)
    legacy_keys = [base.replace('|unknown|', '|2027-03|'),
                   base.replace('ISL|ISL|', 'ISL|SGP|'), base + '|via:JPN']
    for key in legacy_keys:
        db.add(KimiRouteGuidanceCache(cache_key=key, route=route,
            guidance=GUIDANCE, status=kp.STATUS_PRIMARY, generated_at=datetime.now()))
    document_route = dict(route, travel_document_type='diplomatic_passport')
    document_key = kp.cache_key(document_route)
    assert kp.is_canonical_key(document_key)
    db.add(KimiRouteGuidanceCache(cache_key=document_key, route=document_route,
        guidance=GUIDANCE, status=kp.STATUS_PRIMARY, generated_at=datetime.now()))
    db.commit()
    records = main._tstation_rows(db, nationality='ISL', destination='NRU')
    assert records and {r['_cache_key'] for r in records} == {document_key}
    assert all(r['travel_document_type'] == 'diplomatic_passport' for r in records)
    assert db.query(KimiRouteGuidanceCache).filter(
        KimiRouteGuidanceCache.cache_key.in_(legacy_keys)).count() == 3
