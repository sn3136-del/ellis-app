"""Reject contradictions before operator files, issues or history are changed."""
from copy import deepcopy
from datetime import date
import json

import pytest

from app.models import AuditEvent
from app.visa_snapshot import kimi_primary, verified_overrides as vo
from app.visa_snapshot.models import DatabaseChangeLog, DatabaseIssueReport, KimiRouteGuidanceCache


ROUTE = {'passport_nationality': 'CAN', 'passport_issuing_country': 'CAN',
    'destination_country': 'JPN', 'travel_purpose': 'business',
    'travel_document_type': 'diplomatic_passport'}
SOURCE = 'https://www.mofa.go.jp/visa/fees'
ADMIN = {'Authorization': 'Bearer admin-token', 'X-Org-Id': 'authoring-integrity',
    'X-User-Id': 'operator'}


@pytest.fixture
def files(tmp_path, monkeypatch):
    seed, operator = tmp_path / 'seed.json', tmp_path / 'operator.json'
    seed.write_text('[]\n')
    operator.write_text('[]\n')
    monkeypatch.setattr(vo, 'OVERRIDES', seed)
    monkeypatch.setenv('ELLIS_OPERATOR_OVERRIDES', str(operator))
    vo.reload()
    yield operator
    vo.reload()


def entry(fields):
    return {'route': {'nationality': 'CAN', 'destination': 'JPN',
        'travel_purpose': 'business', 'travel_document_type': 'diplomatic_passport'},
        'fields': fields, 'source_url': SOURCE, 'verified_at': date.today().isoformat(),
        'note': 'Official fee schedule checked.', 'verifier': 'human', 'verified_by': 'Operator'}


def test_locked_authoring_checks_raw_fields_and_keeps_file_unchanged(files):
    raw = {'disposition': 'VISA_EXEMPT', 'permitted_stay': '30 days'}
    before = files.read_bytes()
    with pytest.raises(ValueError, match='complete cached answer.*application_channel is embassy'):
        vo.append_operator_entry(entry({'application_channel': 'embassy'}), guidance=raw)
    assert files.read_bytes() == before and vo.find(ROUTE) is None
    assert raw == {'disposition': 'VISA_EXEMPT', 'permitted_stay': '30 days'}
    # A consistent channel edit still succeeds through the same full merge gate.
    vo.append_operator_entry(entry({'application_channel': 'not_required'}), guidance=raw)
    assert json.loads(files.read_text())[0]['fields']['application_channel'] == 'not_required'


@pytest.mark.parametrize('endpoint', ['edit', 'accept'])
def test_endpoint_rejects_conflict_before_any_persistent_mutation(endpoint, files, client, db):
    key = kimi_primary.cache_key(ROUTE)
    raw = {'disposition': 'VISA_EXEMPT', 'permitted_stay': '30 days', 'confidence': 'high'}
    cached = KimiRouteGuidanceCache(cache_key=key, route=ROUTE, guidance=raw,
        verification={'original': 'retained'})
    # A legacy variant must not let acceptance evade the canonical verdict.
    variant = KimiRouteGuidanceCache(cache_key=key + '|via:SGP', route=ROUTE,
        guidance={'disposition': 'VISA_REQUIRED'})
    proposal = {'source_url': SOURCE, 'fields': {'application_channel': {
        'page_says': 'embassy', 'quote': 'Visa applications must be submitted at the embassy.'}}}
    issue = DatabaseIssueReport(org_id='authoring-integrity', cache_key=variant.cache_key,
        route=ROUTE, field='application_channel', note='Please check application channel.', reported_by='reader',
        status='open', proposal=proposal)
    db.add_all([cached, variant, issue]); db.commit()
    saved = (files.read_bytes(), deepcopy(cached.guidance), deepcopy(cached.verification),
        deepcopy(issue.proposal), db.query(DatabaseChangeLog).count(), db.query(AuditEvent).count())
    try:
        if endpoint == 'edit':
            response = client.post('/database/records/edit', headers=ADMIN, json={
                **entry({})['route'], 'fields': {'application_channel': 'embassy'},
                'source_url': SOURCE, 'note': 'Official fee schedule checked.'})
        else:
            response = client.post(f'/database/issues/{issue.id}/accept-proposal', headers=ADMIN)
        assert response.status_code == 422
        assert 'complete cached answer' in response.json()['detail']
        db.refresh(cached); db.refresh(issue)
        assert files.read_bytes() == saved[0]
        assert cached.guidance == saved[1] and cached.verification == saved[2]
        assert issue.proposal == saved[3] and issue.status == 'open'
        assert issue.resolved_at is None and not issue.resolution
        assert db.query(DatabaseChangeLog).count() == saved[4]
        assert db.query(AuditEvent).count() == saved[5]
        assert vo.find(ROUTE) is None
    finally:
        for row in (issue, variant, cached):
            db.delete(row)
        db.commit()
