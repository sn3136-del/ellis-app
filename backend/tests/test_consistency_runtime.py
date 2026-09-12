"""The scheduled report cannot spend money, publish records or file issues."""
import hashlib
import json
from pathlib import Path
import stat
import time

import pytest
from sqlalchemy import select

from app import main
from app.visa_snapshot import consistency_runtime as cr, consistency_sweep as cs
from app.visa_snapshot import freshness, kimi_primary as kp, verified_overrides as vo
from app.visa_snapshot.models import DatabaseChangeLog, DatabaseIssueReport
from tests import _guard_fixtures as gf
from tests.test_consistency_sweep import _guidance_digest, operator


def scan(db, tmp_path, **kwargs):
    return cr.run_report(db, status_path=tmp_path / 'status.json', deadline=time.monotonic() + 60,
                         should_stop=lambda: False, **kwargs)


def test_runtime_report_detects_conflict_without_model_issue_or_guidance_write(db, operator, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('no model calls in the deterministic report')
    monkeypatch.setattr(kp, '_call', forbidden)
    monkeypatch.setattr(kp, '_live_call', forbidden)
    original = cs._qc_projection
    def skewed(db_, row, route):
        records = original(db_, row, route)
        if row.cache_key.startswith('HKG|HKG|VNM'):
            for rec in records:
                rec['visa_fee_amount'] = 999
        return records
    monkeypatch.setattr(cs, '_qc_projection', skewed)
    with gf.seeded(db) as keys:
        before = _guidance_digest(db, keys)
        override_hash = hashlib.sha256(vo.OVERRIDES.read_bytes()).hexdigest()
        changes = len(db.execute(select(DatabaseChangeLog)).scalars().all())
        issues = len(db.execute(select(DatabaseIssueReport)).scalars().all())
        status = scan(db, tmp_path)
        assert status['state'] == 'complete'
        assert status['routes_checked'] == 12 and status['records_checked'] == 18
        assert status['by_type']['surface_divergence'] >= 2
        db.expire_all()
        assert _guidance_digest(db, keys) == before
        assert hashlib.sha256(vo.OVERRIDES.read_bytes()).hexdigest() == override_hash
        assert len(db.execute(select(DatabaseChangeLog)).scalars().all()) == changes
        assert len(db.execute(select(DatabaseIssueReport)).scalars().all()) == issues
    report_path = tmp_path / 'consistency-reports/latest.json'
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(report_path.parent.stat().st_mode) == 0o700
    private = json.loads(report_path.read_text())
    assert private['findings'] and private['trigger'] == 'freshness_timer_report_only'
    assert not any(key in json.dumps(status) for key in ('cache_key', 'source_url', 'fingerprint'))


@pytest.mark.parametrize('failure', ['scan', 'write'])
def test_failure_retains_private_report_and_prior_success_without_current_success(db, tmp_path, monkeypatch, failure):
    report_path = tmp_path / 'consistency-reports/latest.json'
    cr._write_private(report_path, {'private': 'prior report'})
    prior = report_path.read_bytes()
    fake = {'generated_at': '2026-09-12T18:00:00+00:00',
            'inventory': {'canonical_rows': 2, 'served_rows': 3}, 'findings': []}
    monkeypatch.setattr(cs, 'run', lambda *_a, **_k: fake)
    def broken(*args, **kwargs):
        raise OSError('private path /secret and private route HKG|VNM')
    monkeypatch.setattr(cs if failure == 'scan' else cr,
                        'run' if failure == 'scan' else '_write_private', broken)
    result = scan(db, tmp_path, previous={'state': 'complete', 'checked_at': '2026-09-12T12:00:00Z'})
    assert result['state'] == 'failed' and result['failure'] == f'{"scan" if failure == "scan" else "report_write"}_failed'
    assert result['last_success_at'] == '2026-09-12T12:00:00+00:00'
    assert 'checked_at' not in result and 'findings_total' not in result
    assert 'secret' not in json.dumps(result) and report_path.read_bytes() == prior


def test_atomic_replace_failure_does_not_truncate_existing_private_report(tmp_path, monkeypatch):
    target = tmp_path / 'private/latest.json'
    cr._write_private(target, {'before': True})
    before = target.read_bytes()
    monkeypatch.setattr(cr.os, 'replace', lambda *_: (_ for _ in ()).throw(OSError('disk error')))
    with pytest.raises(OSError):
        cr._write_private(target, {'after': True})
    assert target.read_bytes() == before
    assert [p.name for p in target.parent.iterdir()] == ['latest.json']


def test_deadline_or_stop_skips_scan_without_relabeling_prior_report(db, tmp_path, monkeypatch):
    monkeypatch.setattr(cs, 'run', lambda *_a, **_k: (_ for _ in ()).throw(AssertionError('must not start')))
    for expired, stopped, state in ((True, False, 'budget_exhausted'), (False, True, 'interrupted')):
        result = cr.run_report(db, status_path=tmp_path / 'status.json',
            deadline=time.monotonic() + (-1 if expired else 60), should_stop=lambda: stopped)
        assert result['state'] == state and result['routes_checked'] == 0
        assert 'checked_at' not in result
    assert not (tmp_path / 'consistency-reports/latest.json').exists()


def test_cooperative_scan_stop_keeps_partial_route_count(db, operator, tmp_path, monkeypatch):
    original = cs.check_row
    completed = 0
    def inspect(*args, **kwargs):
        nonlocal completed
        result = original(*args, **kwargs)
        completed += 1
        return result
    monkeypatch.setattr(cs, 'check_row', inspect)
    with gf.seeded(db):
        result = cr.run_report(db, status_path=tmp_path / 'status.json',
            deadline=time.monotonic() + 60, should_stop=lambda: completed == 2)
    assert result['state'] == 'interrupted' and result['routes_checked'] == completed == 2
    assert 'findings_total' not in result and not (tmp_path / 'consistency-reports/latest.json').exists()


def test_freshness_api_exposes_only_sanitized_diagnostic_counts(monkeypatch):
    public = {'state': 'complete', 'checked_at': '2026-09-12T18:00:00+00:00',
        'routes_checked': 4, 'records_checked': 5, 'findings_total': 2,
        'by_type': {'surface_divergence': 2, 'PRIVATE|ROUTE': 7, 'key_fork': True},
        'by_severity': {'blocking': 2, 'private reason': 1}, 'findings': [{'cache_key': 'PRIVATE'}],
        'report_path': '/secret', 'error': 'private source quote'}
    monkeypatch.setattr(freshness, 'read_sweep_status', lambda: {
        'running': False, 'state': 'provider_suspended', 'consistency': public})
    result = main._last_sweep_status()
    assert result['status'] == 'provider_suspended'
    assert result['consistency']['by_type'] == {'surface_divergence': 2}
    assert result['consistency']['by_severity'] == {'blocking': 2}
    assert not any(word in json.dumps(result) for word in ('PRIVATE', '/secret', 'source quote', 'report_path'))


def test_dead_timer_does_not_leave_diagnostic_scan_marked_running(monkeypatch):
    monkeypatch.setattr(freshness, 'read_sweep_status', lambda: {
        'running': True, 'state': 'running', 'process_id': 123456,
        'consistency': {'state': 'running', 'routes_checked': 3}})
    monkeypatch.setattr(main.os, 'kill', lambda *_: (_ for _ in ()).throw(ProcessLookupError()))
    result = main._last_sweep_status()
    assert result['status'] == 'interrupted'
    assert result['consistency'] == {'state': 'interrupted', 'routes_checked': 3}


def test_existing_six_hour_timer_calls_the_script_that_runs_the_report():
    root = Path(__file__).resolve().parents[2]
    service = (root / 'deploy/systemd/ellis-freshness.service').read_text()
    timer = (root / 'deploy/systemd/ellis-freshness.timer').read_text()
    assert '/opt/ellis/backend/scripts/freshness_sweep.py' in service
    assert 'OnCalendar=*-*-* 00/6:20:00' in timer
