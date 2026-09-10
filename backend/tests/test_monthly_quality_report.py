from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import stat
from types import SimpleNamespace

import httpx
import pytest


@pytest.fixture
def report():
    spec = importlib.util.spec_from_file_location('monthly_quality_report',
        Path(__file__).resolve().parents[1] / 'scripts/monthly_quality_report.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def payload():
    def row(status, check, held, confidence):
        return {'field_status': {'visa_requirement': 'filled', 'visa_fee': status},
            'source_url': 'https://official.example/visa', 'source_check': check,
            'held': held, 'review_required': held, 'confidence_level': confidence}
    return {'fields': ['visa_requirement', 'visa_fee'],
        'required_fields': ['visa_requirement', 'visa_fee'],
        'records': [row('pending-review', 'unchecked', True, 'Low'),
                    row('filled', 'ai-quote', False, 'High')],
        'summary': {'total': 2, 'source_coverage': 0.5}}


def config(token='private-' + 's' * 48):
    return SimpleNamespace(admin_token=token, dev_api_token='dev-token',
        admin_user_id='sammy-owner', require_secure_admin=True)


def test_counts_pending_separately_and_links_never_equal_source_evidence(report):
    out = report.quality_metrics(payload(), datetime(2026, 9, 9, tzinfo=timezone.utc))
    assert out['field_completeness_pct'] == 75
    assert out['record_completeness_pct'] == out['high_confidence_pct'] == 50
    assert out['source_link_coverage_pct'] == 100 and out['source_coverage_pct'] == 50
    assert out['held_records'] == out['pending_review_records'] == 1
    assert out['field_counts']['filled'] == 3 and out['field_counts']['pending-review'] == 1
    assert 'do not certify' in out['scope']


def test_loopback_request_uses_bound_private_credential_and_writes_private_atomic_report(report, monkeypatch, tmp_path, capsys):
    cfg = config()
    monkeypatch.setattr(report, 'settings', lambda: cfg)
    real_client = httpx.Client
    def handle(request):
        assert str(request.url) == report.RECORDS_URL
        assert request.headers['Authorization'] == 'Bearer ' + cfg.admin_token
        assert request.headers['X-User-Id'] == 'sammy-owner'
        return httpx.Response(200, json=payload())
    def client(**options):
        assert options == {'timeout': 30, 'follow_redirects': False, 'trust_env': False}
        return real_client(transport=httpx.MockTransport(handle), **options)
    monkeypatch.setattr(report.httpx, 'Client', client)
    assert report.main(report_dir=tmp_path, now=datetime(2026, 9, 9, tzinfo=timezone.utc)) == 0
    saved = tmp_path / '2026-09.json'
    assert json.loads(saved.read_text())['records'] == 2
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert list(tmp_path.iterdir()) == [saved]
    output = capsys.readouterr()
    assert cfg.admin_token not in output.out + output.err + saved.read_text()
    assert 'official.example' not in output.out + saved.read_text()


@pytest.mark.parametrize('fault', ['http', 'redirect', 'malformed', 'coverage', 'replace'])
def test_failures_leave_previous_report_unchanged_without_sensitive_errors(report, monkeypatch, tmp_path, capsys, fault):
    cfg = config()
    monkeypatch.setattr(report, 'settings', lambda: cfg)
    saved = tmp_path / '2026-09.json'
    saved.write_text('previous valid report')
    def fetch(_):
        if fault == 'http':
            raise RuntimeError('private response containing ' + cfg.admin_token)
        if fault == 'redirect':
            response = httpx.Response(302, headers={'location': 'https://external.example/'},
                request=httpx.Request('GET', report.RECORDS_URL))
            response.raise_for_status()
        data = payload()
        if fault == 'malformed':
            data.pop('records')
        if fault == 'coverage':
            data['summary']['source_coverage'] = 1
        return data
    monkeypatch.setattr(report, 'fetch_records', fetch)
    if fault == 'replace':
        monkeypatch.setattr(report.os, 'replace', lambda *_: (_ for _ in ()).throw(OSError(cfg.admin_token)))
    assert report.main(report_dir=tmp_path, now=datetime(2026, 9, 9, tzinfo=timezone.utc)) == 1
    assert saved.read_text() == 'previous valid report' and list(tmp_path.iterdir()) == [saved]
    output = capsys.readouterr()
    assert cfg.admin_token not in output.out + output.err and not output.out


@pytest.mark.parametrize('token', ['', 'admin-token', 'dev-token', 'too-short'])
def test_default_or_missing_operator_credentials_fail_before_network(report, monkeypatch, token):
    monkeypatch.setattr(report.httpx, 'Client', lambda **_: (_ for _ in ()).throw(AssertionError('must not connect')))
    with pytest.raises(ValueError, match='private operator credential'):
        report.fetch_records(config(token))
