"""Prompt excerpts must not truncate deterministic official-source proof."""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
from urllib.parse import urlsplit

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base
from app.visa_snapshot import fetching, freshness, kimi_primary, verified_overrides as vo
from app.visa_snapshot.models import DatabaseIssueReport, KimiRouteGuidanceCache


@pytest.fixture
def db(monkeypatch):
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(vo, 'apply', lambda guidance, route: (deepcopy(guidance), {}))
    yield session
    session.close(); engine.dispose()
    fetching.set_fetcher(None); freshness.set_provider(None)


def setup_review(db, monkeypatch):
    manifest = json.loads((Path(__file__).resolve().parents[2] /
        'data/database_seed/reviewed_phl_coverage_2026_09_09.json').read_text())
    entry = next(e for e in manifest['routes'] if e['route']['nationality'] == 'JPN')
    proof = entry['field_provenance']['disposition']
    source = next(s for s in manifest['sources'] if s['id'] == proof['source_id'])
    # A long official document can place its nationality annex after the model
    # excerpt. The reviewed exact closed-list contract still locates its proof.
    prefix = 'Official document introductory material.\n' * 760
    assert len(prefix) > freshness.MAX_PAGE_CHARS
    texts = {source['url']: prefix + '\n' + source['text']}
    monkeypatch.setattr(vo, 'find', lambda route: {'source_url': source['url'],
        'fields': {}, 'field_provenance': {'disposition': deepcopy(proof)}})
    def fetch(url, **kwargs):
        text = texts[url]
        return fetching.FetchResult(requested_url=url, ok=True, final_url=url,
            final_hostname=urlsplit(url).hostname, http_status=200, content_text=text,
            content_hash=hashlib.sha256(text.encode()).hexdigest(),
            retrieved_at='2026-09-09T10:00:00Z')
    fetching.set_fetcher(fetch)
    calls = []
    def provider(_, user):
        payload = json.loads(user); calls.append(payload)
        assert len(payload['official_page_text']) == freshness.MAX_PAGE_CHARS
        assert source['text'] not in payload['official_page_text']
        return {'consistent': True, 'page_relevant': True,
                'page_is_nationality_specific': False, 'corrected_fields': {}, 'evidence': {}}
    freshness.set_provider(provider)
    route = {'passport_nationality': 'JPN', 'destination_country': 'PHL',
             'travel_document_type': 'ordinary_passport', 'travel_purpose': 'tourism'}
    row = KimiRouteGuidanceCache(cache_key=kimi_primary.cache_key(route), route=route,
        guidance={'disposition': 'VISA_EXEMPT', 'application_channel': 'not_required',
                  'source_url': source['url']}, status='KIMI_PRIMARY')
    db.add(row); db.commit()
    return row, source, texts, calls


def test_worker_rechecks_known_country_annex_beyond_prompt_without_more_model_text(db, monkeypatch):
    row, source, texts, calls = setup_review(db, monkeypatch)
    result = freshness.recheck_row(db, row, today='2026-09-09')
    assert result['outcome'] == 'checked' and len(calls) == 1
    check = row.verification['grounded_check']
    assert check['renewed'] is True
    assert 'disposition' in check['verified_fields']
    assert check['field_sources']['disposition']['scope_quotes']
    # It must re-read the tail, not trust the stored contract or reuse a prior
    # successful model comparison after the nationality disappears.
    texts[source['url']] = texts[source['url']].replace('\nJapan\n', '\nJamaica\n')
    result = freshness.recheck_row(db, row, today='2026-09-09')
    assert result['outcome'] == 'page_not_relevant'
    assert freshness.effective_check(row.verification) == {}


def test_reader_issue_uses_same_full_country_annex_and_does_not_mutate_policy(db, monkeypatch):
    row, source, texts, calls = setup_review(db, monkeypatch)
    before = deepcopy(row.guidance)
    issue = DatabaseIssueReport(org_id='test', cache_key=row.cache_key, route=row.route,
        field='disposition', note='Check current nationality list', reported_by='reader', status='open')
    db.add(issue); db.commit()
    proposal = freshness.propose_for_issue(db, issue.id)
    assert proposal['outcome'] == 'checked' and proposal['consistent'] is True
    assert proposal['verified_fields'] == ['disposition'] and calls
    assert row.guidance == before
    texts[source['url']] = texts[source['url']].replace('\nJapan\n', '\nJamaica\n')
    proposal = freshness.propose_for_issue(db, issue.id)
    assert proposal['outcome'] == 'page_not_relevant'
    assert row.guidance == before


@pytest.mark.parametrize('for_issue', [False, True])
def test_literal_field_quote_after_excerpt_is_checked_against_actual_full_source(db, monkeypatch, for_issue):
    url = 'https://www.canada.ca/en/immigration-refugees-citizenship/services/visit-canada/eta/about.html'
    route = {'passport_nationality': 'GBR', 'destination_country': 'CAN',
             'travel_document_type': 'ordinary_passport', 'travel_purpose': 'tourism'}
    rule = 'British citizens need an eTA to fly to Canada for tourism.'
    fee_quote = 'Pay CAN$7 for your eTA.'
    text = rule + '\n' + 'Other introductory information.\n' * 950 + '\n' + fee_quote
    assert text.index(fee_quote) > freshness.MAX_PAGE_CHARS
    monkeypatch.setattr(vo, 'find', lambda route: None)
    fetching.set_fetcher(lambda url, **kwargs: fetching.FetchResult(requested_url=url,
        ok=True, final_url=url, final_hostname='www.canada.ca', content_text=text))
    calls = []
    def provider(_, user):
        payload = json.loads(user); calls.append(payload)
        assert len(payload['official_page_text']) == freshness.MAX_PAGE_CHARS
        assert fee_quote not in payload['official_page_text']
        return {'consistent': True, 'page_relevant': True, 'page_is_nationality_specific': True,
                'corrected_fields': {}, 'evidence': {'government_fee': fee_quote}}
    freshness.set_provider(provider)
    row = KimiRouteGuidanceCache(cache_key=kimi_primary.cache_key(route), route=route,
        guidance={'disposition': 'ELECTRONIC_AUTHORIZATION_REQUIRED',
                  'requirement_detail': 'eta_electronic_authorization', 'source_url': url,
                  'government_fee': {'amount': 7, 'currency': 'CAD'}}, status='KIMI_PRIMARY')
    db.add(row); db.commit()
    if for_issue:
        issue = DatabaseIssueReport(org_id='test', cache_key=row.cache_key, route=route,
            field='government_fee', note='Check amount', reported_by='reader', status='open')
        db.add(issue); db.commit()
        outcome = freshness.propose_for_issue(db, issue.id)
        assert outcome['consistent'] is True and outcome['verified_fields'] == ['government_fee']
    else:
        outcome = freshness.recheck_row(db, row, today='2026-09-09')
        assert outcome['outcome'] == 'checked'
        assert 'government_fee' in row.verification['grounded_check']['verified_fields']
    assert calls
