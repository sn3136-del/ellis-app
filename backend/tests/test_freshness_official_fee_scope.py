"""Missing agency prices or document eligibility cannot erase official fees."""
from copy import deepcopy
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base
from app.visa_snapshot import freshness, fetching, kimi_primary, verified_overrides
from app.visa_snapshot.fetching import FetchResult
from app.visa_snapshot.models import DatabaseIssueReport, KimiRouteGuidanceCache

URL = 'https://www.cn.emb-japan.go.jp/itpr_zh/00_000485_00222.html'
ROUTE = {'passport_nationality': 'CHN', 'destination_country': 'JPN',
         'travel_document_type': 'prc_travel_document', 'travel_purpose': 'tourism'}
OFFICIAL_AGENCY_SENTENCE = '除了上述的签证手续等费用之外，还需要代理机关的手续费。详情请咨询各代理申请机关。'
QUOTES = [OFFICIAL_AGENCY_SENTENCE,
          'Agency fees are not publicly available; ask the designated travel agency.',
          'Acceptance of a PRC Travel Document is not published; consult the Japanese mission.',
          'For Chinese nationals applying in China, the official single-entry visa fee is 715 CNY. Agency fee not included.']
EMPTY = [None, '', [], {}, {'amount': None, 'currency': None},
         {'amount': None, 'currency': 'CNY', 'note': 'Agency charge not published'}]


@pytest.mark.parametrize('quote', QUOTES)
@pytest.mark.parametrize('value', EMPTY)
def test_empty_government_fee_cannot_borrow_an_unrelated_literal_quote(quote, value):
    answer = {'corrected_fields': {'government_fee': value}, 'evidence': {'government_fee': quote}}
    fields, evidence, rejected = freshness._quoted_proposals(answer, quote, ROUTE)
    assert fields == {} and rejected == ['government_fee']
    assert evidence['government_fee'] == quote


@pytest.fixture
def cached(monkeypatch):
    engine = create_engine('sqlite://'); Base.metadata.create_all(engine); db = Session(engine)
    guidance = {'disposition': 'VISA_REQUIRED', 'requirement_detail': 'paper_visa',
        'source_url': URL, 'government_fee': {'amount': 715, 'currency': 'CNY'},
        'uncertainty': [{'field': 'travel_document_type', 'reason': 'Confirm document acceptance with the mission.'}]}
    row = KimiRouteGuidanceCache(cache_key=kimi_primary.cache_key(ROUTE), route=deepcopy(ROUTE),
        guidance=guidance, status='KIMI_PRIMARY', fresh_until=datetime(2020, 1, 1))
    db.add(row); db.commit()
    monkeypatch.setattr(verified_overrides, 'find', lambda _: {'fields': deepcopy(guidance)})
    monkeypatch.setattr(verified_overrides, 'apply', lambda g, _: (deepcopy(g), {}))
    yield db, row
    freshness.set_provider(None); fetching.set_fetcher(None); db.close(); engine.dispose()


@pytest.mark.parametrize('value', EMPTY)
@pytest.mark.parametrize('relevant', [False, True])
def test_real_recheck_preserves_published_tariff_without_false_dispute_or_renewal(cached, value, relevant):
    db, row = cached
    before = deepcopy(row.guidance)
    quote = OFFICIAL_AGENCY_SENTENCE
    fetching.set_fetcher(lambda requested, **_: FetchResult(requested_url=requested,
        final_url=URL, final_hostname='www.cn.emb-japan.go.jp', ok=True, http_status=200,
        content_text='2026年7月1日起，签证等手续费用有所变更。\n单次签证 715元\n'+quote,
        content_hash='official-fee-scope', retrieved_at='2026-09-12'))
    freshness.set_provider(lambda *_: {'page_relevant': relevant,
        'page_is_nationality_specific': relevant, 'consistent': False,
        'corrected_fields': {'government_fee': value}, 'evidence': {'government_fee': quote}})
    result = freshness.recheck_row(db, row)
    assert result['outcome'] == 'validation_error'
    assert result['changed'] == [] and result['disputed'] == []
    assert db.query(DatabaseIssueReport).count() == 0
    assert row.guidance == before and row.fresh_until == datetime(2020, 1, 1)
    check = row.verification['grounded_check']['source_checks'][0]
    assert check['empty_official_fee_fields']['government_fee'] == {'value': value, 'quote': quote}


@pytest.mark.parametrize('amount,quote', [
    (715, 'Chinese nationals applying for a single-entry visa in China pay an official fee of 715 CNY. Agency fee not included.'),
    (1430, 'Chinese nationals applying for a multiple-entry visa in China pay an official fee of 1430 CNY.'),
    (0, 'Chinese nationals applying for this visa pay no official visa fee: the fee is 0 CNY.'),
])
def test_actual_numeric_tariffs_and_explicit_waiver_remain_valid_proposals(amount, quote):
    fee = {'amount': amount, 'currency': 'CNY'}
    answer = {'corrected_fields': {'government_fee': fee}, 'evidence': {'government_fee': quote}}
    fields, _, rejected = freshness._quoted_proposals(answer, quote, ROUTE)
    assert fields == {'government_fee': fee} and rejected == []


def test_explicit_reviewed_not_published_contract_remains_available():
    from scripts.convert_reviewed_general_batch import _check_proof
    proof = {'status': 'not_published', 'verifier': 'ai',
             'reason': 'The government fee is not published on the checked consular fee schedule.'}
    assert _check_proof(proof, {}, ROUTE, 'government_fee', None,
                        general_batch_semantics=True) is None
