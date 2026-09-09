"""A timer countdown and successful fetch must never imply verified policy."""
from datetime import datetime, timezone
from types import SimpleNamespace
from app import main


def test_missing_or_inactive_timer_cannot_fabricate_next_run(monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0,
        stdout='LoadState=loaded\nActiveState=inactive\nNextElapseUSecRealtime=Wed 2099-09-09 06:20:00 UTC'))
    assert main._sweep_timer_status() == {'status': 'inactive', 'next_sweep_at': None}
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1, stdout=''))
    assert main._next_sweep_at() is None


def test_reporting_distinguishes_attempt_from_read_and_verified():
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    def row(destination, **data):
        return dict(cache_key=f'HKG|HKG|{destination}|tourism|default|unknown|v6', **data)
    recent = '2026-09-08T01:00:00Z'
    result = main._recheck_coverage([
        row('VNM', last_attempt_at=recent),
        row('THA', last_attempt_at=recent, read_at=recent),
        row('JPN', last_attempt_at=recent, read_at=recent, grounded_at=recent),
        row('CAN', last_attempt_at='2099-01-01T00:00:00Z', grounded_at='2099-01-01T00:00:00Z'),
    ], now)
    assert result['attempted_48h'] == 3
    assert result['read_48h'] == 2
    assert result['checked_48h'] == 1
    assert result['overdue_attempts'] == 4
    assert result['attempted_target'] == result['verified_target'] == 0


def test_freshness_with_no_overrides_does_not_crash(client, db, monkeypatch):
    from app.visa_snapshot import verified_overrides
    monkeypatch.setattr(verified_overrides, 'apply', lambda guidance, route: (guidance or {}, None))
    monkeypatch.setattr(main, '_sweep_timer_status', lambda: {'status': 'unavailable', 'next_sweep_at': None})
    response = client.get('/database/freshness', headers={'Authorization':'Bearer admin-token', 'X-Org-Id':'integrity', 'X-User-Id':'operator'})
    assert response.status_code == 200
    assert response.json()['summary']['next_sweep_at'] is None


def test_dead_sweep_process_cannot_remain_running(monkeypatch):
    from app.visa_snapshot import freshness
    data = {'running': True, 'state': 'running', 'process_id': 43210,
        'updated_at': datetime.now(timezone.utc).isoformat(), 'finished_at': None,
        'attempted': 7, 'last_error': {'message': 'private log'}, 'secret': 'private'}
    monkeypatch.setattr(freshness, 'read_sweep_status', lambda: data)
    monkeypatch.setattr(main.os, 'kill', lambda *_: (_ for _ in ()).throw(ProcessLookupError()))
    result = main._last_sweep_status()
    assert result['running'] is False and result['state'] == 'interrupted'
    assert result['finished_at'] is None and result['attempted'] == 7
    assert 'last_error' not in result and 'secret' not in result and 'process_id' not in result
    assert data['running'] is True  # read-only reporting, no fabricated completion write


def test_stale_running_record_is_unconfirmed_even_when_pid_was_reused(monkeypatch):
    from datetime import timedelta
    from app.visa_snapshot import freshness
    monkeypatch.setattr(freshness, 'read_sweep_status', lambda: {
        'running': True, 'state': 'running', 'process_id': 43210, 'finished_at': None,
        'updated_at': (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()})
    monkeypatch.setattr(main.os, 'kill', lambda *_: None)
    result = main._last_sweep_status()
    assert result['running'] is False and result['state'] == 'status_stale'
    assert result['finished_at'] is None


def test_recent_live_sweep_retains_real_running_progress(monkeypatch):
    from app.visa_snapshot import freshness
    monkeypatch.setattr(freshness, 'read_sweep_status', lambda: {
        'running': True, 'state': 'running', 'process_id': 43210,
        'updated_at': datetime.now(timezone.utc).isoformat(), 'attempted': 3})
    monkeypatch.setattr(main.os, 'kill', lambda *_: None)
    result = main._last_sweep_status()
    assert result['running'] is True and result['state'] == 'running' and result['attempted'] == 3


def test_sweep_summary_preserves_coverage_and_progress_distinctions(monkeypatch):
    from app.visa_snapshot import freshness
    counters = {'attempted': 10, 'read': 8, 'verified': 5, 'renewed': 2, 'partial': 6,
        'scheduled': 12, 'completed': 10, 'in_flight': 2, 'cycle_unattempted': 7,
        'backlog_remaining': 3, 'eligible_now': 9, 'target_cycle_hours': 6,
        'route_budget_seconds': 75, 'integrity_resolved': 4,
        'last_progress_at': '2026-09-09T01:00:00+00:00',
        'updated_at': '2026-09-09T01:00:30+00:00'}
    monkeypatch.setattr(freshness, 'read_sweep_status', lambda: {
        'running': False, 'state': 'budget_exhausted', **counters,
        'last_error': {'message': 'private provider response'}})
    result = main._last_sweep_status()
    assert all(result[field] == value for field, value in counters.items())
    assert result['checked'] == result['attempted'] == 10  # legacy alias, not verification
    assert result['verified'] == 5 and result['renewed'] == 2
    assert result['last_progress_at'] != result['updated_at']
    assert 'last_error' not in result


def test_api_verdict_only_read_exposes_unverified_details_without_renewal(client, db, monkeypatch):
    from app.visa_snapshot import verified_overrides, kimi_primary, freshness
    from app.visa_snapshot.models import KimiRouteGuidanceCache
    route = {'passport_nationality': 'HKG', 'passport_issuing_country': 'HKG',
        'destination_country': 'JPN', 'travel_purpose': 'tourism',
        'travel_document_type': 'diplomatic_passport'}
    at = datetime.now(timezone.utc).isoformat()
    url = 'https://www.mofa.go.jp/visa'
    quote = 'Hong Kong diplomatic passport holders must obtain a visa for tourism.'
    check = {'outcome': 'checked', 'evidence_contract': freshness.EVIDENCE_CONTRACT,
        'at': at, 'consistent': True, 'source_url': url,
        'verified_fields': ['disposition'], 'unverified_fields': ['government_fee', 'permitted_stay'],
        'renewed': False, 'unchecked_sources': ['https://www.mofa.go.jp/visa/fees'],
        'field_sources': {'disposition': {'source_url': url, 'checked_at': at, 'quote': quote,
            'provider_payload': 'private'}, 'government_fee': {'quote': 'unsupported fee'}},
        'source_checks': [{'source_url': url, 'outcome': 'checked', 'at': at,
            'verified_fields': ['disposition'], 'provider_response': 'private'}]}
    row = KimiRouteGuidanceCache(cache_key=kimi_primary.cache_key(route), route=route,
        guidance={'disposition': 'VISA_REQUIRED', 'government_fee': {'amount': 20, 'currency': 'USD'},
            'permitted_stay': '30 days'}, verification={'grounded_check': check}, fresh_until=None)
    db.add(row); db.commit()
    monkeypatch.setattr(verified_overrides, 'apply', lambda guidance, route: (guidance or {}, None))
    monkeypatch.setattr(main, '_sweep_timer_status', lambda: {'status': 'unavailable', 'next_sweep_at': None})
    try:
        response = client.get('/database/freshness', headers={'Authorization': 'Bearer admin-token',
            'X-Org-Id': 'integrity', 'X-User-Id': 'operator'})
        assert response.status_code == 200
        actual = next(item for item in response.json()['answers'] if item['cache_key'] == row.cache_key)
        assert actual['grounded'] is True and actual['grounded_at'] == at
        assert actual['route']['travel_document_type'] == 'diplomatic_passport'
        assert actual['verification_at'] == at
        assert actual['verified_fields'] == ['disposition']
        assert actual['unverified_fields'] == ['government_fee', 'permitted_stay']
        assert actual['renewed'] is False and actual['fresh_until'] is None
        assert actual['unchecked_sources'] == check['unchecked_sources']
        assert actual['field_sources'] == {'disposition': {'source_url': url, 'checked_at': at, 'quote': quote}}
        assert actual['source_checks'][0]['verified_fields'] == ['disposition']
        assert 'provider_response' not in actual['source_checks'][0]
    finally:
        db.delete(row); db.commit()
