"""Malformed extraction must not create asserted policy disputes or renew TTL."""
from copy import deepcopy
from datetime import datetime
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base
from app.visa_snapshot import fetching, freshness, kimi_primary, verified_overrides
from app.visa_snapshot.fetching import FetchResult
from app.visa_snapshot.models import DatabaseIssueReport, KimiRouteGuidanceCache


@pytest.mark.parametrize('field,value,quote', [
    ('requirement_detail', 'visa_free_subject_to_visit_conditions', 'Visa free subject to visit conditions.'),
    ('requirement_detail', 'Visa not required for a stay of less than one (1) month', 'Visa not required for a stay of less than one (1) month.'),
    ('requirement_detail', 'visa_required', 'A visa is required.'),
    ('application_channel', 'online', 'Apply online.'),
    ('application_channel', 'visa_application_centre', 'Visa application centre.'),
    ('disposition', 'visa_required', 'Chinese nationals must obtain a visa.'),
    ('requirement_detail', {}, 'No visa required.'),
    ('application_channel', ['online_portal'], 'Submit your visa application online.'),
    ('disposition', True, 'Chinese nationals must obtain a visa.'),
    ('disposition', None, 'Chinese nationals must obtain a visa.'),
])
def test_literal_words_do_not_promote_invalid_enum_to_policy_proposal(field, value, quote):
    answer = {'corrected_fields': {field: value}, 'evidence': {field: quote}}
    before = deepcopy(answer)
    assert freshness._enum_proposal_errors(answer) == [field]
    fields, evidence, unquoted = freshness._quoted_proposals(answer, quote)
    assert fields == {} and unquoted == [field] and evidence[field] == quote
    assert answer == before


@pytest.mark.parametrize('field,value,quote', [
    ('requirement_detail', 'paper_visa', 'A paper visa is required.'),
    ('requirement_detail', 'evisa', 'Tourists must obtain an evisa.'),
    ('application_channel', 'embassy', 'Apply at the embassy.'),
    ('disposition', 'VISA_REQUIRED', 'Chinese nationals must obtain a visa.'),
    ('permitted_stay', '30 days', 'Tourists may stay 30 days.'),
    ('requirement_detail', None, 'The authority does not publish a subtype.'),
])
def test_valid_classifications_free_text_and_nullable_adjudication_keep_existing_proof_rules(field, value, quote):
    answer = {'corrected_fields': {field: value}, 'evidence': {field: quote}}
    assert freshness._enum_proposal_errors(answer) == []
    fields, _, unquoted = freshness._quoted_proposals(answer, quote)
    assert fields == {field: value} and unquoted == []
    assert freshness._quoted_proposals(answer, 'Missing capture')[0] == {}


@pytest.fixture
def cached(monkeypatch):
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    db = Session(engine)
    route = {'passport_nationality': 'CHN', 'destination_country': 'JPN',
             'travel_document_type': 'ordinary_passport', 'travel_purpose': 'tourism'}
    guidance = {'disposition': 'VISA_REQUIRED', 'requirement_detail': 'paper_visa',
                'source_url': 'https://www.mofa.go.jp/visa/tourism', 'confidence': 'high'}
    row = KimiRouteGuidanceCache(cache_key=kimi_primary.cache_key(route), route=route,
        guidance=deepcopy(guidance), status='KIMI_PRIMARY', fresh_until=datetime(2020, 1, 1))
    db.add(row); db.commit()
    monkeypatch.setattr(verified_overrides, 'find', lambda _: {'fields': {'requirement_detail': 'paper_visa', 'government_fee': None}})
    monkeypatch.setattr(verified_overrides, 'apply', lambda g, _: (deepcopy(g), {}))
    yield db, row
    freshness.set_provider(None); fetching.set_fetcher(None)
    db.close(); engine.dispose()


def _read(row, *, relevant=True, fee=False):
    # Synthetic official-shaped page exercises the mechanism, not Japan policy.
    quote = 'Chinese nationals must obtain a visa for tourism in Japan. A paper visa is required.'
    text = quote + (' The visa fee is 200 CNY.' if fee else '')
    url = row.guidance['source_url']
    fetching.set_fetcher(lambda requested, **_: FetchResult(requested_url=requested,
        final_url=url, final_hostname='www.mofa.go.jp', ok=True, http_status=200,
        content_text=text, content_hash='enum-fixture', retrieved_at='2026-09-10'))
    def provider(_, payload):
        assert json.loads(payload)['allowed_enum_values']['requirement_detail'] == list(kimi_primary.REQUIREMENT_DETAILS)
        fields = {'requirement_detail': 'visa_required'}
        evidence = {'requirement_detail': quote}
        if fee:
            fields['government_fee'] = {'amount': 200, 'currency': 'CNY'}
            evidence['government_fee'] = 'The visa fee is 200 CNY.'
        return {'page_relevant': relevant, 'page_is_nationality_specific': relevant,
                'consistent': True, 'corrected_fields': fields, 'evidence': evidence}
    freshness.set_provider(provider)


def test_invalid_enum_is_failed_check_even_when_all_current_populated_facts_are_confirmed(cached):
    db, row = cached
    before = deepcopy(row.guidance)
    _read(row)
    result = freshness.recheck_row(db, row)
    check = row.verification['grounded_check']
    assert result['outcome'] == 'validation_error' and result['changed'] == [] and result['disputed'] == []
    assert check['verified_fields'] == ['disposition', 'requirement_detail']
    assert check['unverified_fields'] == []  # No unrelated missing quote explains the TTL outcome.
    assert check['validation_errors'] == ['invalid enum proposal: requirement_detail']
    assert check['source_checks'][0]['outcome'] == 'validation_error'
    assert check['source_checks'][0]['proposed_fields'] == {}
    assert check['renewed'] is False and row.fresh_until == datetime(2020, 1, 1)
    assert row.guidance == before and db.query(DatabaseIssueReport).count() == 0
    assert 'last_good_check' not in row.verification and freshness.effective_check(row.verification) == {}


def test_invalid_enum_does_not_hide_a_real_fee_disagreement_or_clear_an_existing_issue(cached):
    db, row = cached
    row.guidance = dict(row.guidance, government_fee={'amount': 100, 'currency': 'CNY'})
    issue = DatabaseIssueReport(org_id='platform', cache_key=row.cache_key, route=row.route,
        field='passport_validity', note='Independent unresolved condition', reported_by='freshness_monitor',
        status='open', proposal={'fields': {'passport_validity': {'page_says': 'Different validity'}}})
    db.add(issue); db.commit()
    before = deepcopy(row.guidance)
    _read(row, fee=True)
    result = freshness.recheck_row(db, row)
    assert result['disputed'] == ['government_fee']
    assert {i.field for i in db.query(DatabaseIssueReport)} == {'government_fee', 'passport_validity'}
    assert issue.status == 'open' and row.guidance == before
    assert row.verification['grounded_check']['renewed'] is False


def test_invalid_enum_on_irrelevant_page_is_not_fallback_policy_dispute(cached):
    db, row = cached
    _read(row, relevant=False)
    result = freshness.recheck_row(db, row)
    assert result['outcome'] == 'validation_error' and result['disputed'] == []
    assert result['validation_errors'] == ['invalid enum proposal: requirement_detail']
    assert db.query(DatabaseIssueReport).count() == 0 and row.fresh_until == datetime(2020, 1, 1)


def test_valid_changed_visa_subtype_still_requires_adjudication(cached):
    db, row = cached
    url = row.guidance['source_url']
    quote = 'Chinese nationals must obtain a visa for tourism in Japan. Applicants must obtain an evisa.'
    fetching.set_fetcher(lambda requested, **_: FetchResult(requested_url=requested,
        final_url=url, final_hostname='www.mofa.go.jp', ok=True, http_status=200,
        content_text=quote, content_hash='valid-new-subtype', retrieved_at='2026-09-10'))
    freshness.set_provider(lambda *_: {'page_relevant': True, 'page_is_nationality_specific': True,
        'consistent': False, 'corrected_fields': {'requirement_detail': 'evisa'},
        'evidence': {'requirement_detail': quote}})
    result = freshness.recheck_row(db, row)
    assert result['disputed'] == ['requirement_detail']
    issue = db.query(DatabaseIssueReport).one()
    assert issue.proposal['fields']['requirement_detail']['page_says'] == 'evisa'
    assert row.guidance['requirement_detail'] == 'paper_visa' and issue.status == 'open'
    assert row.verification['grounded_check']['validation_errors'] == []


def test_validation_failure_preserves_earlier_good_read_without_redating_it(cached):
    db, row = cached
    prior = {'outcome': 'checked', 'evidence_contract': freshness.EVIDENCE_CONTRACT,
             'at': '2020-01-01', 'source_url': row.guidance['source_url'],
             'verified_fields': ['disposition', 'requirement_detail'], 'renewed': True}
    row.verification = {'grounded_check': deepcopy(prior), 'last_good_check': deepcopy(prior)}
    db.commit()
    _read(row)
    result = freshness.recheck_row(db, row)
    assert result['outcome'] == 'validation_error'
    assert row.verification['last_good_check'] == prior
    assert freshness.effective_check(row.verification) == prior
    assert row.fresh_until == datetime(2020, 1, 1)
