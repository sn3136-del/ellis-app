"""Old audit_integrity findings use the factual, independently reviewed loop."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from app.visa_snapshot import kimi_primary as kp, sweep_issues, verified_overrides as vo
from app.visa_snapshot.issue_revision import revision_sha256, snapshot
from app.visa_snapshot.models import DatabaseChangeLog, DatabaseIssueReport, KimiRouteGuidanceCache

ROUTE = {'passport_nationality': 'TWN', 'destination_country': 'VNM',
         'travel_purpose': 'tourism', 'travel_document_type': 'ordinary_passport'}
KEY = kp.cache_key(ROUTE)
SOURCE = 'https://evisa.gov.vn/'
OLD_FAILURES = ['disposition ELECTRONIC_AUTHORIZATION_REQUIRED but requirement_detail is evisa',
                'disposition ELECTRONIC_AUTHORIZATION_REQUIRED but requirement_detail is evisa, which is a visa']


def good():
    return {'disposition': 'VISA_REQUIRED', 'requirement_detail': 'evisa',
            'government_fee': {'amount': 25, 'currency': 'USD'},
            'application_channel': 'online_portal', 'source_url': SOURCE,
            'visa_products': [{'type': 'Tourist e-visa', 'fee': {'amount': 25, 'currency': 'USD'}}]}


def bad():
    return dict(good(), disposition='ELECTRONIC_AUTHORIZATION_REQUIRED')


@pytest.fixture(autouse=True)
def isolated(db, monkeypatch):
    # Keep real override/display code, but isolate the route from shipped seeds.
    monkeypatch.setattr(vo, 'find', lambda route: None)
    for cls in (DatabaseChangeLog, DatabaseIssueReport, KimiRouteGuidanceCache):
        db.query(cls).filter_by(cache_key=KEY).delete(synchronize_session=False)
    db.commit()
    yield
    db.rollback()
    for cls in (DatabaseChangeLog, DatabaseIssueReport, KimiRouteGuidanceCache):
        db.query(cls).filter_by(cache_key=KEY).delete(synchronize_session=False)
    db.commit()


def seed(db, guidance=None, *, status='acknowledged', failures=None, cache=True):
    failures = failures or OLD_FAILURES
    if cache:
        db.add(KimiRouteGuidanceCache(cache_key=KEY, route=deepcopy(ROUTE),
                                     guidance=deepcopy(guidance if guidance is not None else good())))
    issue = DatabaseIssueReport(cache_key=KEY, route=deepcopy(ROUTE), field='integrity',
        reported_by='freshness_monitor', status=status,
        resolved_by='old-source-author' if status in ('corrected', 'reviewed') else '',
        note='Deterministic integrity check: ' + '; '.join(failures),
        proposal={'outcome': 'integrity_failed', 'checked_at': '2026-09-12T18:40:03.632694+00:00',
                  'contradictions': failures, 'fields': {},
                  'resolution_check': {'outcome': 'integrity_passed', 'contradictions': []}},
        created_at=datetime.now(timezone.utc) - timedelta(minutes=10))
    db.add(issue); db.commit()
    return issue.id


def log(db, field='disposition', *, origin='operator-edit', delta=None):
    db.add(DatabaseChangeLog(cache_key=KEY, route=deepcopy(ROUTE), action='modify', origin=origin,
        changes={field: deepcopy(delta if delta is not None else {'from': 'ELECTRONIC_AUTHORIZATION_REQUIRED', 'to': 'VISA_REQUIRED'})},
        note='Reviewed official-source correction', created_at=datetime.now(timezone.utc)))
    db.commit()


def advance(client, db, identity, status='corrected', *, actor='source-author', **extra):
    db.expire_all()
    issue = db.get(DatabaseIssueReport, identity)
    payload = {'status': status, 'resolution': 'Codex AI reviewed the actual sourced correction.',
               'source_url': SOURCE, 'source_excerpt': 'Official e-visa information.',
               'expected_issue_sha256': revision_sha256(snapshot(issue)), **extra}
    return client.post('/database/issues/' + identity, headers={
        'Authorization': 'Bearer admin-token', 'X-Org-Id': 'legacy-integrity-test',
        'X-User-Id': actor}, json=payload)


@pytest.mark.parametrize('field,failures', [
    ('disposition', OLD_FAILURES),
    ('requirement_detail', ['disposition VISA_REQUIRED but requirement_detail is unconditional_visa_free']),
    ('visa_products', ['disposition VISA_EXEMPT but priced visa products are listed']),
])
def test_real_legacy_shape_advances_after_actual_policy_correction(client, db, field, failures):
    guidance = good()
    if field == 'disposition':
        old_value = 'ELECTRONIC_AUTHORIZATION_REQUIRED'
    elif field == 'requirement_detail':
        old_value = 'unconditional_visa_free'
    else:
        old_value = deepcopy(guidance['visa_products'])
        guidance.update(disposition='VISA_EXEMPT', requirement_detail='unconditional_visa_free',
                        government_fee={'amount': 0, 'currency': 'USD'},
                        application_channel='not_required')
        guidance['visa_products'] = [
            {'type': 'Visa-free tourist entry', 'fee': {'amount': 0, 'currency': 'USD'}},
            *guidance['visa_products'],
        ]
    identity = seed(db, guidance, failures=failures)
    log(db, field, delta={'from': old_value, 'to': deepcopy(guidance[field])})
    before = deepcopy(db.get(DatabaseIssueReport, identity).proposal)
    for status, actor in [('corrected', 'source-author'), ('reviewed', 'independent-reviewer'),
                          ('published', 'independent-reviewer')]:
        response = advance(client, db, identity, status, actor=actor)
        assert response.status_code == 200, response.text
    db.expire_all()
    assert db.get(DatabaseIssueReport, identity).proposal == before
    assert db.get(DatabaseIssueReport, identity).status == 'published'


@pytest.mark.parametrize('status,initial', [('corrected', 'acknowledged'), ('reviewed', 'corrected'),
                                           ('published', 'reviewed'), ('dismissed', 'open')])
def test_every_terminal_stage_rechecks_current_wrong_facts(client, db, status, initial):
    identity = seed(db, bad(), status=initial)
    log(db)
    response = advance(client, db, identity, status, actor='independent-reviewer')
    assert response.status_code == 422, response.text
    assert 'still' in response.json()['detail']
    db.expire_all()
    assert db.get(DatabaseIssueReport, identity).status == initial


@pytest.mark.parametrize('kind', ['none', 'noop', 'malformed', 'engine', 'unrelated', 'written', 'accepted'])
def test_passing_invariants_do_not_replace_a_real_relevant_source_delta(client, db, kind):
    identity = seed(db)
    if kind == 'noop': log(db, delta={'from': 'VISA_REQUIRED', 'to': 'VISA_REQUIRED'})
    elif kind == 'malformed': log(db, delta={'to': 'VISA_REQUIRED'})
    elif kind == 'engine': log(db, origin='engine')
    elif kind == 'unrelated': log(db, 'confidence')
    elif kind == 'accepted':
        issue = db.get(DatabaseIssueReport, identity)
        issue.proposal = {**issue.proposal, 'correction_evidence': {'fields': ['disposition']}}
        db.commit()
    extra = {'correction_evidence': {'fields': ['disposition'], 'source_url': SOURCE}} if kind == 'written' else {}
    response = advance(client, db, identity, **extra)
    assert response.status_code == 422, response.text
    assert 'apply a sourced correction' in response.json()['detail']


@pytest.mark.parametrize('status,initial', [('corrected', 'acknowledged'), ('reviewed', 'corrected'),
                                           ('published', 'reviewed'), ('dismissed', 'open')])
def test_missing_canonical_answer_never_counts_as_a_fixed_invariant(client, db, status, initial):
    identity = seed(db, cache=False, status=initial); log(db)
    response = advance(client, db, identity, status, actor='independent-reviewer')
    assert response.status_code == 422, response.text
    assert 'no canonical row' in response.json()['detail']


def test_actual_merged_correction_is_checked_without_rewriting_raw_cache(client, db, monkeypatch):
    identity = seed(db, bad()); log(db)
    monkeypatch.setattr(vo, 'find', lambda route: {
        'route': {'nationality': 'TWN', 'destination': 'VNM', 'travel_purpose': 'tourism'},
        'fields': {'disposition': 'VISA_REQUIRED', 'requirement_detail': 'evisa'},
        'source_url': SOURCE, 'verified_at': '2026-09-12',
        'quote': 'Taiwan passport holders must obtain an e-visa.', 'verifier': 'ai'})
    response = advance(client, db, identity)
    assert response.status_code == 200, response.text
    db.expire_all()
    assert db.query(KimiRouteGuidanceCache).filter_by(cache_key=KEY).one().guidance == bad()


def test_fact_regression_after_review_blocks_publication(client, db):
    identity = seed(db); log(db)
    assert advance(client, db, identity).status_code == 200
    assert advance(client, db, identity, 'reviewed', actor='reviewer').status_code == 200
    row = db.query(KimiRouteGuidanceCache).filter_by(cache_key=KEY).one(); row.guidance = bad(); db.commit()
    response = advance(client, db, identity, 'published', actor='reviewer')
    assert response.status_code == 422, response.text
    db.expire_all()
    assert db.get(DatabaseIssueReport, identity).status == 'reviewed'


@pytest.mark.parametrize('tamper', ['reporter', 'note', 'outcome', 'fields', 'contradictions'])
def test_free_text_or_forged_integrity_label_is_not_the_legacy_audit(client, db, tamper):
    identity = seed(db); issue = db.get(DatabaseIssueReport, identity)
    if tamper == 'reporter': issue.reported_by = 'reader'
    elif tamper == 'note': issue.note = 'I think there is an integrity problem'
    else:
        proposal = dict(issue.proposal)
        proposal[tamper] = {'outcome': 'unchecked', 'fields': {'disposition': {}},
                            'contradictions': 'not the audit list'}[tamper]
        issue.proposal = proposal
    db.commit(); log(db)
    assert sweep_issues.legacy_integrity_meta(issue) is None
    response = advance(client, db, identity)
    assert response.status_code == 422, response.text


def test_old_audit_does_not_bypass_independent_review_or_revision(client, db):
    identity = seed(db); log(db)
    assert advance(client, db, identity, expected_issue_sha256='0' * 64).status_code == 409
    assert advance(client, db, identity).status_code == 200
    assert advance(client, db, identity, 'reviewed').status_code == 422


def test_dismissal_still_requires_evidence_and_a_passing_recheck(client, db):
    identity = seed(db, bad(), status='open')
    response = advance(client, db, identity, 'dismissed', dismiss_reason='check_false_positive')
    assert response.status_code == 422
    row = db.query(KimiRouteGuidanceCache).filter_by(cache_key=KEY).one(); row.guidance = good(); db.commit()
    response = advance(client, db, identity, 'dismissed', source_url='', source_excerpt='')
    assert response.status_code == 422
    response = advance(client, db, identity, 'dismissed')
    assert response.status_code == 200, response.text
