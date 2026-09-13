"""Actual HTTP-envelope contracts for bounded source comparison, without network."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base
from app.providers import kimi, provider_usage
from app.visa_snapshot import freshness, kimi_primary as kp, verified_overrides as vo
from app.visa_snapshot.fetching import FetchResult
from app.visa_snapshot.models import KimiRouteGuidanceCache

ANSWER = {'page_relevant': True, 'page_is_nationality_specific': True,
          'consistent': True, 'corrected_fields': {}, 'evidence': {}}


def envelope(content=None, reason='stop', **message):
    return {'choices': [{'finish_reason': reason, 'message': {
        'content': json.dumps(ANSWER) if content is None else content, **message}}]}


@pytest.fixture
def transport(monkeypatch):
    state = SimpleNamespace(calls=[], payload=envelope())
    cfg = SimpleNamespace(moonshot_api_key='offline-placeholder', kimi_enabled=True,
        kimi_model='kimi-k3', kimi_timeout_seconds=120, kimi_base_url='https://api.invalid/v1')
    state.cfg = cfg
    monkeypatch.setattr(kimi, 'settings', lambda: cfg)
    monkeypatch.setattr(kp, 'settings', lambda: cfg)
    monkeypatch.delenv('KIMI_GUIDANCE_MODEL', raising=False)
    monkeypatch.setattr(kp, 'provider_suspension', lambda: None)
    monkeypatch.setattr(kp, '_rate_gate_wait', lambda deadline: True)
    monkeypatch.setattr(kp, '_rate_gate_clear', lambda: None)
    monkeypatch.setattr(freshness, '_PROVIDER', None)
    def post(_fn, _url, **kwargs):
        state.calls.append(kwargs)
        return httpx.Response(200, json=state.payload)
    monkeypatch.setattr(provider_usage, 'post', post)
    return state


def test_only_source_comparisons_use_low_effort_and_keep_existing_budget(transport):
    assert freshness._call('Compare current official text', 'Public route') == ANSWER
    call = transport.calls[0]
    assert 0 < call['timeout'] <= 45.0
    assert call['json']['model'] == 'kimi-k3'
    assert call['json']['reasoning_effort'] == 'low'
    assert call['json']['max_tokens'] == 6000
    assert call['json']['response_format'] == {'type': 'json_object'}
    assert 'thinking' not in call['json'] and 'temperature' not in call['json']
    assert freshness.CALL_TIMEOUT_SECONDS == 45.0
    assert freshness.ROUTE_BUDGET_SECONDS == 120.0
    # Normal guidance keeps its existing default request and extraction path.
    transport.payload = envelope('', reasoning_content='{"legacy":true}')
    assert kp._live_call('Generate guidance', 'Public route', timeout=10, max_tokens=500) == {'legacy': True}
    assert 'reasoning_effort' not in transport.calls[-1]['json']
    assert transport.calls[-1]['json']['max_tokens'] == 500


@pytest.mark.parametrize('override', [False, True])
def test_other_models_keep_their_defaults_but_comparisons_require_final_json(transport, monkeypatch, override):
    if override:
        monkeypatch.setenv('KIMI_GUIDANCE_MODEL', '  explicit-other-model  ')
    else:
        transport.cfg.kimi_model = 'explicit-other-model'
    assert freshness._call('Compare', 'Public route') == ANSWER
    body = transport.calls[0]['json']
    assert body['model'] == 'explicit-other-model' and 'reasoning_effort' not in body
    contract = kp.comparison_provider_contract()
    assert contract['model'] == body['model']
    assert contract['final_json_only'] is True and contract['reasoning_effort'] is None
    transport.payload = envelope('', reasoning_content=json.dumps(ANSWER))
    with pytest.raises(kimi.KimiInvalidResponse, match='incomplete_final_response'):
        freshness._call('Compare', 'Public route')


def test_final_object_wins_and_private_reasoning_is_ignored(transport):
    transport.payload = envelope(' \n' + json.dumps(ANSWER) + '\n ', reasoning_content='{"page_relevant":false}')
    assert freshness._call('Compare', 'Public route') == ANSWER


BAD_FINALS = [
    (envelope('', reasoning_content=json.dumps(ANSWER)), 'incomplete_final_response'),
    (envelope(' ', reasoning_content=json.dumps(ANSWER)), 'incomplete_final_response'),
    (envelope(reason='length'), 'output_truncated'),
    (envelope(reason='tool_calls'), 'incomplete_final_response'),
    (envelope(reason='content_filter'), 'incomplete_final_response'),
    (envelope(reason=None), 'incomplete_final_response'),
    (envelope(tool_calls=[{'function': {'name': 'review'}}]), 'incomplete_final_response'),
    (envelope(refusal='private provider reason'), 'incomplete_final_response'),
    (envelope(function_call={'name': 'review'}), 'incomplete_final_response'),
    (envelope('Preamble ' + json.dumps(ANSWER)), 'invalid_final_json'),
    (envelope(json.dumps(ANSWER) + ' trailing'), 'invalid_final_json'),
    (envelope(json.dumps(ANSWER) + '{}'), 'invalid_final_json'),
    (envelope('{"incomplete":' + json.dumps(ANSWER)), 'invalid_final_json'),
    (envelope('```json\n' + json.dumps(ANSWER) + '\n```'), 'invalid_final_json'),
    (envelope('[]'), 'invalid_final_json'),
    (envelope('{"consistent":true,"consistent":false}'), 'invalid_final_json'),
    (envelope('{"fee":NaN}'), 'invalid_final_json'),
    (envelope('{"fee":Infinity}'), 'invalid_final_json'),
    (envelope('{"corrected_fields":{"government_fee":{"amount":1e999}}}'), 'invalid_final_json'),
    (envelope('{"corrected_fields":{"government_fee":{"amount":-1e999}}}'), 'invalid_final_json'),
    ({'choices': []}, 'incomplete_final_response'),
    ({'choices': envelope()['choices'] * 2}, 'incomplete_final_response'),
    ({'choices': [{'finish_reason': 'stop', 'message': {'reasoning_content': json.dumps(ANSWER)}}]}, 'incomplete_final_response'),
]


@pytest.mark.parametrize('payload,reason', BAD_FINALS)
def test_no_salvage_no_reasoning_fallback_and_no_paid_retry(transport, payload, reason):
    transport.payload = payload
    with pytest.raises(kimi.KimiInvalidResponse) as raised:
        freshness._call('Compare private source text', 'Public route')
    assert str(raised.value) == reason
    assert raised.value.__cause__ is None
    assert len(transport.calls) == 1
    assert freshness._provider_diagnostic(raised.value) == {
        'error_type': 'KimiInvalidResponse', 'category': 'kimi_unavailable', 'technical': reason}


@pytest.mark.parametrize('payload,reason', [BAD_FINALS[0], BAD_FINALS[2], BAD_FINALS[12]])
def test_unfinished_comparison_cannot_change_facts_renew_or_poison_reuse(transport, monkeypatch, payload, reason):
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    route = {'passport_nationality': 'CAN', 'destination_country': 'JPN',
             'travel_document_type': 'ordinary_passport', 'travel_purpose': 'tourism'}
    url = 'https://www.mofa.go.jp/visa'
    text = 'Canadian citizens must obtain a visa for tourism in Japan.'
    monkeypatch.setattr(vo, 'find', lambda route: None)
    monkeypatch.setattr(vo, 'apply', lambda guidance, route: (deepcopy(guidance), {}))
    monkeypatch.setattr(freshness, 'fetch', lambda requested, **kwargs: FetchResult(
        requested_url=requested, ok=True, final_url=url, final_hostname='www.mofa.go.jp',
        content_text=text, content_hash='source-text', retrieved_at='2026-09-12T09:00:00+00:00'))
    row = KimiRouteGuidanceCache(cache_key=kp.cache_key(route), route=route,
        guidance={'disposition': 'VISA_REQUIRED', 'source_url': url},
        status='KIMI_PRIMARY', verification={}, fresh_until=datetime.now(timezone.utc)-timedelta(days=1))
    db.add(row); db.commit(); db.refresh(row)
    old_guidance = deepcopy(row.guidance); old_expiry = row.fresh_until
    transport.payload = payload
    try:
        result = freshness.recheck_row(db, row, today='2026-09-12T09:00:00+00:00')
        assert not result.get('renewed') and result['changed'] == []
        assert row.guidance == old_guidance and row.fresh_until == old_expiry
        check = row.verification['grounded_check']['source_checks'][0]
        assert check['outcome'] == 'provider_error'
        assert check['provider_diagnostic']['technical'] == reason
        assert not row.verification['grounded_check'].get('verified_fields')
        transport.payload = envelope()
        next_result = freshness.recheck_row(db, row, today='2026-09-12T09:00:00+00:00')
        assert next_result['model_comparisons'] == 1 and next_result['model_comparisons_reused'] == 0
        assert next_result['verified_fields'] == ['disposition']
        assert len(transport.calls) == 2
    finally:
        db.close(); engine.dispose()
