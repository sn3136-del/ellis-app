"""A paid refresh attempt is durably reserved before transport, not after it."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import threading

import pytest

from app.providers import provider_usage, refresh_budget as rb
from app.visa_snapshot.bounded_io import call


def request(**changes):
    return dict(model='kimi-k3', messages=[{'role': 'user', 'content': 'quoted official source'}], max_tokens=6000, **changes)


def cycle(tmp_path):
    return rb.open_cycle(tmp_path / 'budget.db')


def usage(prompt=100, output=10, cached=0):
    return {'prompt_tokens': prompt, 'completion_tokens': output,
            'total_tokens': prompt+output, 'cached_prompt_tokens': cached}


def test_reserve_settle_cached_price_and_idempotence(tmp_path):
    b=cycle(tmp_path); attempt=b.reserve(request())
    assert b.snapshot()['charged_or_reserved_usd'] > .09
    b.settle(attempt, usage(1000,100,400), 'kimi-k3')
    assert b.snapshot()['charged_or_reserved_usd'] == .00342
    b.settle(attempt, usage(0,0), 'kimi-k3')
    assert b.snapshot()['charged_or_reserved_usd'] == .00342
    assert b.snapshot()['settled_attempts'] == 1


@pytest.mark.parametrize('bad', [{}, {'prompt_tokens': 1}, usage(-1), usage(cached=101),
    dict(usage(), total_tokens=999), dict(usage(), completion_tokens=True)])
def test_unknown_or_invalid_usage_never_zeroes_reservation(tmp_path, bad):
    b=cycle(tmp_path); attempt=b.reserve(request()); before=b.snapshot()['charged_or_reserved_usd']
    b.settle(attempt,bad)
    assert b.snapshot()['charged_or_reserved_usd'] == before


def test_unknown_model_unbounded_or_multimodal_request_is_not_sent(tmp_path):
    b=cycle(tmp_path)
    for changes in ({'model':'different'}, {'max_tokens':None}, {'max_tokens':0},
                    {'messages':[{'content':[{'type':'image_url'}]}]}, {'n':2}, {'stream':True}):
        body=request();body.update(changes)
        with pytest.raises(rb.RefreshBudgetExceeded): b.reserve(body)
    assert b.snapshot()['charged_or_reserved_usd'] == 0


def test_concurrent_independent_ledger_connections_never_exceed_nine(tmp_path):
    b=cycle(tmp_path)
    def spend(_):
        try: return rb.Budget(b.path,b.cycle_id).reserve(request())
        except rb.RefreshBudgetExceeded: return None
    with ThreadPoolExecutor(max_workers=12) as pool: results=list(pool.map(spend,range(250)))
    assert 0 < sum(r is not None for r in results) < 250
    snap=b.snapshot()
    assert 8.8 < snap['charged_or_reserved_usd'] <= 9
    assert snap['unknown_usage_attempts'] == sum(r is not None for r in results)


def test_resume_completed_cycle_and_expired_runtime_cannot_reset_cost(tmp_path):
    now=datetime.now(timezone.utc); path=tmp_path/'b.db'
    b=rb.open_cycle(path,now=now); b.reserve(request())
    resumed=rb.open_cycle(path,now=now+timedelta(hours=5,minutes=30),prior_cycle=b.cycle_id)
    assert resumed.cycle_id == b.cycle_id
    assert resumed.snapshot()['charged_or_reserved_usd'] > 0
    next_cycle=rb.open_cycle(path,now=now+timedelta(hours=6),prior_cycle=b.cycle_id)
    assert next_cycle.cycle_id != b.cycle_id
    assert next_cycle.snapshot()['charged_or_reserved_usd'] == 0


def test_recent_legacy_cycle_is_unknown_spend_and_missing_ledger_fails_closed(tmp_path):
    now=datetime.now(timezone.utc)
    b=rb.open_cycle(tmp_path/'b.db',now=now,legacy_started_at=(now-timedelta(hours=2)).isoformat())
    assert b.snapshot()['charged_or_reserved_usd'] == 9
    with pytest.raises(rb.RefreshBudgetExceeded): b.reserve(request())
    with pytest.raises(rb.RefreshBudgetExceeded): rb.open_cycle(tmp_path/'missing.db',prior_cycle=b.cycle_id)


def test_provider_usage_hook_reserves_before_http_and_timeout_retains_cost(tmp_path,monkeypatch):
    b=cycle(tmp_path); events=[];monkeypatch.setattr(provider_usage,'_emit',events.append)
    def timeout(*args,**kwargs):
        assert b.snapshot()['unknown_usage_attempts']==1
        raise TimeoutError('fixture')
    with rb.bind(b), pytest.raises(TimeoutError):
        provider_usage.post(timeout,'https://api.moonshot.ai/v1/chat/completions',headers={},json=request(),timeout=1)
    assert b.snapshot()['charged_or_reserved_usd'] > .09
    assert events[0]['refresh_budget_cycle']==b.cycle_id
    assert rb.active() is None


def test_final_usage_settles_even_if_caller_later_rejects_response(tmp_path,monkeypatch):
    b=cycle(tmp_path);monkeypatch.setattr(provider_usage,'_emit',lambda _:None)
    class Response:
        status_code=200;headers={}
        def json(self): return {'model':'kimi-k3','usage':usage(100,10),'choices':[]}
    with rb.bind(b):
        provider_usage.post(lambda *a,**k:Response(),'https://api.moonshot.ai',headers={},json=request(),timeout=1)
    assert b.snapshot()['charged_or_reserved_usd']==.00045


def test_budget_exhausted_never_calls_transport_and_unbound_api_is_unchanged(tmp_path,monkeypatch):
    now=datetime.now(timezone.utc)
    b=rb.open_cycle(tmp_path/'b.db',now=now,legacy_started_at=now.isoformat())
    calls=[];monkeypatch.setattr(provider_usage,'_emit',lambda _:None)
    with rb.bind(b),pytest.raises(rb.RefreshBudgetExceeded):
        provider_usage.post(lambda *a,**k:calls.append(1),'https://api.moonshot.ai',headers={},json=request(),timeout=1)
    assert calls==[]
    provider_usage.post(lambda *a,**k:calls.append(1),'https://api.moonshot.ai',headers={},json={},timeout=1)
    assert calls==[1]


def test_late_bounded_thread_retains_original_cycle_after_context_exit(tmp_path):
    b=cycle(tmp_path); release=threading.Event();done=threading.Event();observed=[]
    def later():
        release.wait(2)
        observed.append(rb.active().cycle_id)
        rb.active().reserve(request())
        done.set()
    with rb.bind(b),pytest.raises(TimeoutError):
        call(later,.02,threading.BoundedSemaphore(1))
    assert rb.active() is None
    release.set();assert done.wait(2)
    assert observed==[b.cycle_id]
    assert b.snapshot()['unknown_usage_attempts']==1


def test_provider_bound_violation_blocks_further_dispatch(tmp_path):
    b=cycle(tmp_path); a=b.reserve(request()); before=b.snapshot()['charged_or_reserved_usd']
    b.settle(a,usage(output=6001),'kimi-k3')
    assert b.snapshot()['blocked']
    assert b.snapshot()['charged_or_reserved_usd']==before
    with pytest.raises(rb.RefreshBudgetExceeded):b.reserve(request())
