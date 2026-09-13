"""Adding names a real canonical row; a partial source read is not publication."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from app import main
from app.visa_snapshot import freshness, kimi_primary as kp, verified_overrides as vo
from app.visa_snapshot.models import KimiRouteGuidanceCache

ADMIN = {'authorization': 'Bearer admin-token', 'x-org-id': 'add-test', 'x-user-id': 'operator'}
BODY = {'nationality': 'ISL', 'destination': 'JPN', 'travel_purpose': 'tourism'}
ANSWER = {'disposition': 'VISA_REQUIRED', 'requirement_detail': 'paper_visa',
          'visa_category': 'Tourist visa', 'permitted_stay': '30 days',
          'passport_validity': 'Valid passport', 'required_documents': ['Passport'],
          'application_channel': 'embassy', 'government_fee': {'amount': 30, 'currency': 'USD'},
          'processing_time': 'Five working days', 'confidence': 'high',
          'source_url': 'https://www.mofa.go.jp/j_info/visit/visa/index.html',
          'visa_products': [{'type': 'Tourist visa', 'entry': 'single',
                             'validity': '90 days', 'max_stay_days': 30,
                             'fee': {'amount': 30, 'currency': 'USD'}}]}

@pytest.fixture(autouse=True)
def isolate(db, monkeypatch):
    db.query(KimiRouteGuidanceCache).filter(KimiRouteGuidanceCache.cache_key.like('ISL|%|JPN|%')).delete(synchronize_session=False)
    db.commit()
    monkeypatch.setattr(vo, '_table', lambda: {})
    monkeypatch.setattr(main, '_ground_on_access', lambda *a: None)
    monkeypatch.setattr(main, '_after_cold_answer', lambda *a: None)
    monkeypatch.setattr(freshness, 'recheck_route', lambda *a: {'outcome': 'checked', 'consistent': True,
        'renewed': False, 'verified_fields': ['government_fee'], 'unverified_fields': ['disposition']})
    yield
    kp.set_provider(None)


def test_empty_completed_answer_is_not_added_and_does_not_start_a_second_generation(client, monkeypatch):
    calls = []
    monkeypatch.setattr(kp, 'get_route_guidance', lambda _db, route, **kw:
        calls.append(kw['stage']) or {'status': kp.STATUS_UNCERTAIN, 'guidance': {}, 'held': True})
    monkeypatch.setattr(freshness, 'recheck_route', lambda *a: pytest.fail('no row to research'))
    out = client.post('/database/routes/research', headers=ADMIN, json=BODY)
    assert out.status_code == 200
    assert calls == ['full']
    assert out.json()['ok'] is False and out.json()['record_present'] is False
    assert out.json()['status'] == 'not_added' and out.json()['held'] is True
    assert out.json()['disposition'] is None and out.json()['research'] is None


def test_add_repeated_add_lookup_and_records_share_document_specific_rows(client, db):
    calls = []
    kp.set_provider(lambda system, user: calls.append(user) or deepcopy(ANSWER))
    keys = []
    for document in ['ordinary_passport', 'prc_travel_document']:
        body = dict(BODY, travel_document_type=document)
        first = client.post('/database/routes/research', headers=ADMIN, json=body).json()
        assert first['record_present'] is True and first['status'] == 'stored'
        assert first['route']['travel_document_type'] == document
        assert first['research']['renewed'] is False
        count = len(calls)
        second = client.post('/database/routes/research', headers=ADMIN, json=body).json()
        look = client.post('/database/lookup', headers=ADMIN,
                           json=dict(body, arrival_date='2027-03-15', residence='Singapore')).json()
        assert second['cache_key'] == look['cache_key'] == first['cache_key']
        assert look['record_present'] is True and len(calls) == count
        keys.append(first['cache_key'])
    assert keys[0] != keys[1]
    rows = main._tstation_rows(db, nationality='ISL', destination='JPN')
    assert {r['_cache_key'] for r in rows} == set(keys)
    assert db.query(KimiRouteGuidanceCache).filter(KimiRouteGuidanceCache.cache_key.like('ISL|%|JPN|%')).count() == 2


def test_pending_details_are_stored_but_never_reported_as_published(client, db, monkeypatch):
    route = {'passport_nationality': 'ISL', 'destination_country': 'JPN', 'travel_purpose': 'tourism'}
    db.add(KimiRouteGuidanceCache(cache_key=kp.cache_key(route), route=route, guidance=deepcopy(ANSWER),
        status=kp.STATUS_UNCERTAIN, generated_at=datetime.now(timezone.utc), verification={'detail_pending': True}))
    db.commit()
    monkeypatch.setattr(kp, 'get_route_guidance', lambda *a, **k: {'guidance': deepcopy(ANSWER), 'held': False})
    monkeypatch.setattr(main, '_apply_records_hold', lambda r, o, db: o)
    out = client.post('/database/routes/research', headers=ADMIN, json=BODY).json()
    assert out['record_present'] and out['detail_pending'] and out['status'] == 'pending'
    assert out['held'] and out['disposition'] is None


def test_source_exception_keeps_real_row_but_does_not_hide_failed_check(client, monkeypatch):
    kp.set_provider(lambda *a: deepcopy(ANSWER))
    def fail(*a): raise RuntimeError('private provider secret')
    monkeypatch.setattr(freshness, 'recheck_route', fail)
    response = client.post('/database/routes/research', headers=ADMIN, json=BODY)
    out = response.json()
    assert out['record_present'] is True
    assert out['research']['outcome'] == 'research_error' and out['research']['renewed'] is False
    assert 'private provider secret' not in response.text


@pytest.mark.parametrize('field,value', [('travel_purpose', 'invented'), ('travel_document_type', 'invented')])
def test_manual_scope_is_rejected_before_override_write(client, monkeypatch, field, value):
    monkeypatch.setattr(vo, 'append_operator_entry', lambda *a, **k: pytest.fail('invalid route written'))
    response = client.post('/database/records/edit', headers=ADMIN, json={**BODY, field: value,
        'fields': {'permitted_stay': '30 days'}, 'source_url': ANSWER['source_url'], 'note': 'Official source'})
    assert response.status_code == 422


def test_manual_aliases_match_lookup_identity_before_write(client, monkeypatch):
    captured = []
    monkeypatch.setattr(vo, 'append_operator_entry', lambda entry, **kw: captured.append(entry))
    response = client.post('/database/records/edit', headers=ADMIN, json={**BODY,
        'travel_purpose': 'tourist', 'travel_document_type': 'travel_document',
        'fields': {'permitted_stay': '30 days'}, 'source_url': ANSWER['source_url'], 'note': 'Official source'})
    assert response.status_code == 200
    assert captured[0]['route']['travel_purpose'] == 'tourism'
    assert captured[0]['route']['travel_document_type'] == 'prc_travel_document'


def test_research_rejects_itinerary_date_instead_of_minting_dated_records(client, monkeypatch):
    monkeypatch.setattr(kp, 'get_route_guidance', lambda *a, **k: pytest.fail('invalid body reached engine'))
    assert client.post('/database/routes/research', headers=ADMIN,
                       json=dict(BODY, arrival_date='2027-03-15')).status_code == 422


def test_failed_post_check_projection_never_returns_old_policy(client, db, monkeypatch):
    route = {'passport_nationality': 'ISL', 'destination_country': 'JPN', 'travel_purpose': 'tourism'}
    db.add(KimiRouteGuidanceCache(cache_key=kp.cache_key(route), route=route, guidance=deepcopy(ANSWER),
        status=kp.STATUS_PRIMARY, generated_at=datetime.now(timezone.utc)))
    db.commit()
    def answer(_db, _route, **kw):
        if kw['stage'] == 'core': raise RuntimeError('private projection failure')
        return {'guidance': deepcopy(ANSWER), 'held': False}
    monkeypatch.setattr(kp, 'get_route_guidance', answer)
    response = client.post('/database/routes/research', headers=ADMIN, json=BODY)
    assert response.status_code == 503
    assert 'Reload the list' in response.json()['detail']
    assert 'private projection failure' not in response.text
    assert 'disposition' not in response.json()
