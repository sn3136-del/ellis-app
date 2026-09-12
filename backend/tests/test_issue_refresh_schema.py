"""Issue research rejects malformed extraction without destroying its evidence."""
from copy import deepcopy
import json
import pytest

from app.visa_snapshot import freshness
from app.visa_snapshot.models import DatabaseIssueReport
from .test_freshness_comparison_reuse import h, seed, URL


def issue(h, row):
    report = DatabaseIssueReport(org_id='platform', cache_key=row.cache_key,
        route=deepcopy(row.route), field='disposition', note='Check this requirement',
        reported_by='test-reader', status='open',
        proposal={'outcome': 'checked', 'reader_evidence': 'Existing source evidence'})
    h.db.add(report); h.db.commit()
    return report


@pytest.mark.parametrize('bad', [[], None, {}, {'page_relevant': 'true'},
    {'consistent': 'false'}, {'corrected_fields': []}, {'evidence': 'quoted'},
    {'route_evidence': False}, {'field_scope': None}])
def test_invalid_comparison_does_not_rewrite_guidance_or_existing_issue(h, bad):
    row = seed(h); report = issue(h, row)
    before = deepcopy(report.proposal); guidance = deepcopy(row.guidance)
    h.answer = dict(h.answer, **bad) if isinstance(bad, dict) and bad else bad
    result = freshness.propose_for_issue(h.db, report.id)
    assert result['outcome'] == 'validation_error'
    assert result['issue_unchanged'] and not result['verified_fields']
    assert result['rejected_comparisons'][0]['validation_errors']
    h.db.refresh(report); h.db.refresh(row)
    assert report.proposal == before and report.status == 'open'
    assert row.guidance == guidance and row.fresh_until is None


def test_provider_exception_body_is_never_saved_to_public_issue(h):
    row = seed(h); report = issue(h, row)
    def fail(_):
        raise RuntimeError('SECRET_TOKEN private upstream request body')
    h.answer = fail
    result = freshness.propose_for_issue(h.db, report.id)
    assert result['outcome'] == 'provider_error'
    assert result['provider_diagnostic'] == {'error_type': 'RuntimeError', 'category': 'unknown'}
    h.db.refresh(report)
    assert 'SECRET_TOKEN' not in json.dumps(report.proposal)
    assert report.status == 'open'
