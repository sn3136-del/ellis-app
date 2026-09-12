"""A failed extraction is not an irrelevant page or a successful fresh check.

The public Embassy fixture was captured on 2026-09-12; renderer link markers
are replaced only by their displayed labels, without changing policy text.
"""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base
from app.providers.kimi import KimiHttpError
from app.visa_snapshot import freshness, kimi_primary, structured_evidence as se
from app.visa_snapshot import verified_overrides as vo
from app.visa_snapshot.fetching import FetchResult
from app.visa_snapshot.models import DatabaseIssueReport, KimiRouteGuidanceCache

URL = 'https://www.cn.emb-japan.go.jp/itpr_zh/visa_kanko.html'
TEXT = (Path(__file__).parent / 'fixtures/japan_chinese_tourism_20260912.txt').read_text()
DAY = '2026-09-12'
ROUTE = dict(passport_nationality='CHN', destination_country='JPN',
             travel_document_type='ordinary_passport', travel_purpose='tourism')
HEADING, CLOSING = '1．什么是中国人赴日旅游签证', '2．签证类型'
SECTION = TEXT[TEXT.index(HEADING):TEXT.index('### ' + CLOSING)].strip()


def proof():
    return {'source_id': 'page', 'verified_at': DAY, 'quote': SECTION,
        'source_country_section': {'program': 'japan_chinese_tourist_visa',
            'source_id': 'page', 'heading_quote': HEADING,
            'closing_quote': CLOSING, 'section_quote': SECTION}}


def answer():
    return {'page_relevant': True, 'page_is_nationality_specific': True,
        'consistent': True, 'corrected_fields': {}, 'evidence': {}, 'route_evidence': proof()}


@pytest.fixture
def h(monkeypatch):
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    state = SimpleNamespace(db=db, answer=answer(), calls=0, exception=None,
        fetched=FetchResult(requested_url=URL, final_url=URL, final_hostname='www.cn.emb-japan.go.jp',
            ok=True, http_status=200, content_text=TEXT, content_hash='fixture', retrieved_at=DAY+'T12:00:00Z'))
    monkeypatch.setattr(vo, 'find', lambda route: None)
    monkeypatch.setattr(vo, 'apply', lambda guidance, route: (deepcopy(guidance), {}))
    monkeypatch.setattr(freshness, 'fetch', lambda *args, **kwargs: state.fetched)
    def compare(*args, **kwargs):
        state.calls += 1
        if state.exception: raise state.exception
        return deepcopy(state.answer)
    monkeypatch.setattr(freshness, '_call', compare)
    yield state
    db.close(); engine.dispose()


def seed(h, **guidance):
    row = KimiRouteGuidanceCache(cache_key=kimi_primary.cache_key(ROUTE), route=dict(ROUTE),
        status=kimi_primary.STATUS_PRIMARY, verification={}, guidance={
            'disposition': 'VISA_REQUIRED', 'source_url': URL,
            'application_channel': 'authorised_agent', **guidance})
    h.db.add(row); h.db.commit()
    return row


def run(h, row):
    return freshness.recheck_row(h.db, row, today=DAY+'T12:00:00+00:00')


def test_actual_chinese_section_checks_only_its_scoped_rule_and_preserves_other_fields(h):
    row = seed(h, government_fee={'amount': 715, 'currency': 'CNY'}, permitted_stay='15 or 30 days')
    before = deepcopy(row.guidance)
    result = run(h, row)
    check = row.verification['grounded_check']
    assert result['outcome'] == 'checked'
    assert check['verified_fields'] == ['disposition']
    assert set(check['unverified_fields']) >= {'government_fee', 'permitted_stay', 'application_channel'}
    assert check['source_checks'][0]['route_evidence']['program'] == 'japan_chinese_tourist_visa'
    assert row.guidance == before and not result['renewed'] and row.fresh_until is None
    assert h.db.query(DatabaseIssueReport).count() == 0


@pytest.mark.parametrize('field,value', [
    ('passport_nationality', 'USA'), ('passport_nationality', 'HKG'),
    ('destination_country', 'KOR'), ('travel_document_type', 'prc_travel_document'),
    ('travel_document_type', 'diplomatic_passport'), ('travel_purpose', 'business')])
def test_scoped_nationality_section_cannot_support_another_route(field, value):
    source = {'url': URL, 'text': TEXT, 'checked_at': DAY}
    result = se.validate_route_evidence(proof(), source, {'page': source},
        {**ROUTE, field: value}, 'VISA_REQUIRED', policy_date=DAY)
    assert result['ok'] is False


@pytest.mark.parametrize('mutation', [
    'wrong_url', 'spoof_host', 'source_id', 'second_nationality_heading', 'duplicate_heading',
    'body_other_section', 'short_quote', 'changed_obligation', 'added_exemption', 'product_only',
    'future_capture', 'visa_free_verdict'])
def test_exact_section_cannot_borrow_neighbouring_or_changed_rules(mutation):
    p = proof(); source = {'url': URL, 'text': TEXT, 'checked_at': DAY}
    disposition = 'VISA_REQUIRED'
    if mutation == 'wrong_url': source['url'] = URL.replace('visa_kanko', 'visa_tanki')
    elif mutation == 'spoof_host': source['url'] = URL.replace('.go.jp/', '.go.jp.evil.example/')
    elif mutation == 'source_id': p['source_country_section']['source_id'] = 'missing'
    elif mutation == 'second_nationality_heading': source['text'] = TEXT.replace(HEADING, '1．美国人赴日旅游签证')
    elif mutation == 'duplicate_heading': source['text'] += '\n' + HEADING
    elif mutation == 'body_other_section':
        source['text'] = TEXT.replace(HEADING, HEADING + '\n无内容。\n' + CLOSING)
    elif mutation == 'short_quote':
        p['quote'] = p['source_country_section']['section_quote'] = HEADING
    elif mutation == 'changed_obligation':
        source['text'] = TEXT.replace('不能由申请人自理', '可以由申请人自理')
    elif mutation == 'added_exemption': source['text'] = TEXT.replace(HEADING, HEADING + '\n中国公民免签。')
    elif mutation == 'product_only':
        p['quote'] = p['source_country_section']['section_quote'] = '该签证可以在3年有效期之内多次使用，停留期限为「30天」。'
    elif mutation == 'future_capture': source['checked_at'] = '2026-09-13'
    elif mutation == 'visa_free_verdict': disposition = 'VISA_EXEMPT'
    result = se.validate_route_evidence(p, source, {'page': source}, ROUTE, disposition, policy_date=DAY)
    assert result['ok'] is False


def test_agency_condition_is_required_and_no_contract_means_no_heading_inference(h):
    row = seed(h, application_channel='online_portal')
    assert run(h, row)['outcome'] == 'page_not_relevant'
    assert row.fresh_until is None
    h.answer.pop('route_evidence')
    row.guidance = dict(row.guidance, application_channel='authorised_agent'); h.db.commit()
    assert run(h, row)['outcome'] == 'page_not_relevant'
    assert row.verification['grounded_check']['source_checks'][0]['relevance_reason'] == 'route_evidence_not_supported'


BAD = [None, [], 'not JSON', {}, {'page_relevant': False}]
for name in ('page_relevant', 'page_is_nationality_specific', 'consistent', 'evidence', 'corrected_fields'):
    item = answer(); item.pop(name); BAD.append(item)
for name, value in [('page_relevant', 1), ('consistent', 'true'), ('evidence', []),
                    ('corrected_fields', None), ('route_evidence', 'visa required'), ('field_scope', [])]:
    item = answer(); item[name] = value; BAD.append(item)


@pytest.mark.parametrize('malformed', BAD)
def test_malformed_model_output_never_becomes_irrelevance_or_freshness(h, malformed):
    row = seed(h); before = deepcopy(row.guidance)
    h.answer = malformed
    result = run(h, row)
    check = row.verification['grounded_check']
    assert result['outcome'] == 'validation_error' and not result.get('renewed')
    assert check['source_checks'][0]['relevance_reason'] == 'invalid_comparison_response'
    assert check['source_checks'][0]['validation_errors']
    assert not check.get('comparison_cache') and row.guidance == before and row.fresh_until is None
    assert h.db.query(DatabaseIssueReport).count() == 0
    run(h, row)
    assert h.calls == 2  # a malformed response must never enter reuse cache


def test_genuine_irrelevant_response_retains_distinct_reason(h):
    row = seed(h)
    h.answer = {**answer(), 'page_relevant': False, 'page_is_nationality_specific': False}
    h.answer.pop('route_evidence')
    assert run(h, row)['outcome'] == 'page_not_relevant'
    assert row.verification['grounded_check']['source_checks'][0]['relevance_reason'] == 'model_marked_page_irrelevant'
    assert row.fresh_until is None


def test_http_provider_diagnostic_keeps_status_but_not_message_prompt_or_credentials(h):
    row = seed(h)
    err = kimi_primary.GuidanceProviderError({'category': 'unknown',
        'user_message': 'safe message', 'technical': 'private-prompt-and-credential'})
    err.__cause__ = KimiHttpError(400, 'content_filter', message='secret customer payload')
    h.exception = err
    assert run(h, row)['outcome'] == 'provider_error'
    diagnostic = row.verification['grounded_check']['source_checks'][0]['provider_diagnostic']
    assert diagnostic == {'error_type': 'GuidanceProviderError', 'category': 'unknown',
        'http_status': 400, 'provider_error_type': 'content_filter'}
    assert 'private' not in json.dumps(diagnostic) and 'secret' not in json.dumps(diagnostic)
    assert row.fresh_until is None


@pytest.mark.parametrize('unknown', ['invalid request contains private details', 'sk-private-credential-token'])
def test_timeout_and_unrecognized_provider_details_use_only_fixed_categories(unknown):
    assert freshness._provider_diagnostic(TimeoutError('private connection details')) == {
        'error_type': 'TimeoutError', 'category': 'kimi_unavailable', 'technical': 'comparison_timeout'}
    err = kimi_primary.GuidanceProviderError({'category': 'private-secret', 'technical': 'private-secret'})
    err.__cause__ = KimiHttpError(400, unknown)
    assert freshness._provider_diagnostic(err) == {
        'error_type': 'GuidanceProviderError', 'category': 'unknown', 'http_status': 400}


@pytest.mark.parametrize('error,code,status', [
    ('[SSL: CERTIFICATE_VERIFY_FAILED] private internal path', 'tls_certificate_error', None),
    ('read timeout credential=private', 'source_timeout', None),
    ('pdf_encrypted', 'pdf_encrypted', 200),
    ('pdf_image_only_or_no_extractable_text', 'pdf_image_only_or_no_extractable_text', 200),
    ('private response details', 'http_error', 503), ('private response details', 'source_fetch_error', None),
    ('pdf_private_secret', 'source_fetch_error', None)])
def test_fetch_failure_preserves_safe_reason_without_paid_comparison(h, error, code, status):
    row = seed(h)
    h.fetched = replace(h.fetched, ok=False, error=error, http_status=status, content_text='')
    assert run(h, row)['outcome'] == 'fetch_failed'
    d = row.verification['grounded_check']['source_checks'][0]['fetch_diagnostic']
    assert d['error_code'] == code and d['http_status'] == status
    assert 'private' not in json.dumps(d) and h.calls == 0 and row.fresh_until is None
