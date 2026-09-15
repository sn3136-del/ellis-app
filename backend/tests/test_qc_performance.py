"""QC loading optimizations must retain fresh canonical data and all history."""
from copy import deepcopy

import pytest
from app import main

AUTH = {'authorization': 'Bearer admin-token', 'x-org-id': 'qc-performance', 'x-user-id': 'reader'}


def test_schedule_read_never_projects_policy_or_reads_audit_blobs(client, monkeypatch):
    timer = {'status': 'active', 'next_sweep_at': '2026-09-15T12:20:00+00:00', 'schedule_basis': 'calendar'}
    monkeypatch.setattr(main, '_sweep_timer_status', lambda: dict(timer))
    monkeypatch.setattr(main, '_freshness_rows', lambda *a: pytest.fail('Clock must not project policy'))
    monkeypatch.setattr(main, '_last_sweep_status', lambda: pytest.fail('Clock must not load audit history'))
    result = client.get('/database/freshness?schedule_only=true', headers=AUTH)
    assert result.status_code == 200
    assert result.json() == {'summary': {'next_sweep_at': timer['next_sweep_at'], 'scheduler': timer}}
    assert result.headers['cache-control'] == 'private, no-store'
    timer.update(status='unavailable', next_sweep_at=None)
    assert client.get('/database/freshness?schedule_only=true', headers=AUTH).json()['summary']['scheduler'] == timer


def test_full_freshness_audit_remains_the_default(client, monkeypatch):
    called = []
    monkeypatch.setattr(main, '_freshness_rows', lambda *a: called.append(True) or [])
    monkeypatch.setattr(main, '_sweep_timer_status', lambda: {'status': 'inactive', 'next_sweep_at': None})
    monkeypatch.setattr(main, '_last_sweep_status', lambda: {'status': 'failed', 'error': 'provider unavailable'})
    result = client.get('/database/freshness', headers=AUTH).json()
    assert called == [True]
    assert result['answers'] == []
    assert result['summary']['last_run']['status'] == 'failed'
    assert result['summary']['total'] == 0


def test_record_validator_checks_current_source_before_reusing_and_cannot_cross_filters(client, monkeypatch):
    current = [[{'_cache_key': 'USA|USA|NPL|tourism|default|unknown|v6',
        'travel_document_country': 'USA', 'destination_country': 'NPL',
        'travel_document_type': 'ordinary_passport', 'travel_purpose': 'tourism',
        'visa_type_name': 'Tourist visa', 'visa_requirement': 'Visa on Arrival',
        'confidence_level': 'Medium', 'visa_fee_amount': 30, 'visa_fee_currency': 'USD'}]]
    observations = []
    monkeypatch.setattr(main, '_all_tstation_rows', lambda db: observations.append(True) or current[0])
    monkeypatch.setattr(main, '_RECORDS_JSON_CACHE', {'rows': None, 'body': None, 'etag': None})
    first = client.get('/database/records', headers=AUTH)
    etag = first.headers['etag']
    checked = {**AUTH, 'if-none-match': etag}
    unchanged = client.get('/database/records', headers=checked)
    assert unchanged.status_code == 304 and unchanged.content == b''
    assert unchanged.headers['cache-control'] == 'private, no-store'
    assert len(observations) == 2, 'No conditional response may skip canonical version validation'
    filtered = client.get('/database/records?nationality=JPN', headers=checked)
    assert filtered.status_code == 200 and filtered.json()['records'] == []
    current[0] = [dict(current[0][0], visa_fee_amount=50)]
    edited = client.get('/database/records', headers=checked)
    assert edited.status_code == 200
    assert edited.json()['records'][0]['visa_fee_amount'] == 50
    assert edited.headers['etag'] != etag
    assert client.get('/database/records', headers={**AUTH, 'if-none-match': edited.headers['etag']}).status_code == 304
