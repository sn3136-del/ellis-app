from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import stat
from types import SimpleNamespace

import httpx
import pytest
from app.visa_snapshot.tstation import FIELD_ORDER, REQUIRED_FIELDS


@pytest.fixture
def report():
    spec = importlib.util.spec_from_file_location('monthly_quality_report',
        Path(__file__).resolve().parents[1] / 'scripts/monthly_quality_report.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def payload():
    def row(status, check, held, confidence):
        return {**dict.fromkeys(FIELD_ORDER, 'value'),
            'visa_fee_amount': 0,
            'field_status': {**dict.fromkeys(FIELD_ORDER, 'filled'), 'visa_fee_amount': status},
            'source_url': 'https://official.example/visa', 'source_check': check,
            'held': held, 'review_required': held, 'confidence_level': confidence}
    return {'fields': list(FIELD_ORDER),
        'required_fields': sorted(REQUIRED_FIELDS),
        'records': [row('pending-review', 'unchecked', True, 'Low'),
                    row('filled', 'ai-quote', False, 'High')],
        'summary': {'total': 2, 'source_coverage': 0.5}}


def config(token='private-' + 's' * 48):
    return SimpleNamespace(admin_token=token, dev_api_token='dev-token',
        admin_user_id='sammy-owner', require_secure_admin=True)


def test_counts_pending_separately_and_links_never_equal_source_evidence(report):
    out = report.quality_metrics(payload(), datetime(2026, 9, 9, tzinfo=timezone.utc))
    assert out['field_completeness_pct'] == 97.5
    assert out['record_completeness_pct'] == out['high_confidence_pct'] == 50
    assert out['source_link_coverage_pct'] == 100 and out['source_coverage_pct'] == 50
    assert out['held_records'] == out['pending_review_records'] == 1
    assert out['field_counts']['filled'] == 51 and out['field_counts']['pending-review'] == 1
    assert 'do not certify' in out['scope']


def test_literal_contract_metric_keeps_unpublished_blank_and_pending_value_visible(report):
    data = payload()
    data['records'][1]['info_validity'] = None
    data['records'][1]['field_status']['info_validity'] = 'not-published'
    out = report.quality_metrics(data, datetime(2026, 9, 10, tzinfo=timezone.utc))
    contract = out['contract_acceptance_metrics']
    assert contract['field_count'] == 25 and contract['required_cells'] == 50
    assert contract['filled_cells'] == 49  # A real zero fee is populated.
    assert contract['field_completeness_rate'] == 0.98
    assert contract['complete_records'] == 1
    assert contract['unchallenged_filled_cells'] == 48
    assert contract['unchallenged_complete_records'] == 0
    assert contract['pending_review_cells'] == 1
    assert contract['requirement_support_rate'] == 0.5
    assert contract['accuracy_certified'] is False
    assert contract['documented_completed_cells'] == 49
    assert contract['documented_complete_records'] == 1
    assert out['complete_records'] == 1  # Explicit unpublished disposition is separate.


@pytest.mark.parametrize('fault', ['short-fields', 'unknown-field', 'short-required', 'duplicate-required'])
def test_contract_denominator_cannot_be_shrunk_by_payload_schema(report, fault):
    data = payload()
    if fault == 'short-fields':
        data['fields'] = ['visa_requirement']
    elif fault == 'unknown-field':
        data['fields'][-1] = 'unknown'
    elif fault == 'short-required':
        data['required_fields'] = ['visa_requirement']
    else:
        data['required_fields'].append(data['required_fields'][0])
    with pytest.raises(ValueError, match='schema or record count'):
        report.quality_metrics(data, datetime(2026, 9, 10, tzinfo=timezone.utc))


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
