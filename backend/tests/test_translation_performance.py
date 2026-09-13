"""Translation request contracts and avoidable work; offline HTTP only."""
import json
from types import SimpleNamespace

import httpx
import pytest

from app import i18n
from app.providers import kimi, provider_usage


def test_k3_translation_uses_low_effort_without_invalid_sampling_or_model_change(monkeypatch):
    posted=[]
    cfg=SimpleNamespace(moonshot_api_key='offline-placeholder',kimi_base_url='https://api.invalid/v1',
        kimi_model='kimi-k3',kimi_timeout_seconds=120)
    monkeypatch.setattr(kimi,'settings',lambda:cfg)
    def fake_post(_fn,_url,**kwargs):
        posted.append(kwargs)
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'{"translated":"护照","v0":"护照"}'}}]})
    monkeypatch.setattr(provider_usage,'post',fake_post)
    p=kimi.LiveKimiProvider()
    assert p.translate('Passport','zh-CN','en')=='护照'
    assert p.translate_batch({'v0':'Passport'},'zh-Hant','en')['v0']=='护照'
    p._chat('Unrelated reasoning task','Evidence',max_tokens=6000)
    p.translate_batch({'v0':'Passport'},'zh-CN','en',model='explicit-other-model')
    for call in posted[:2]:
        assert call['json']['model']=='kimi-k3'
        assert call['json']['reasoning_effort']=='low'
        assert 'thinking' not in call['json']
        assert 'temperature' not in call['json']
        assert call['json']['response_format']=={'type':'json_object'}
    assert posted[1]['timeout']==120
    assert posted[1]['json']['max_tokens']==8000
    assert 'reasoning_effort' not in posted[2]['json']
    assert posted[3]['json']['model']=='explicit-other-model'
    assert 'reasoning_effort' not in posted[3]['json']


def test_catalog_writes_one_complete_atomic_snapshot_and_warm_hit_has_zero_work(monkeypatch,tmp_path):
    monkeypatch.setenv('ELLIS_DATA_DIR',str(tmp_path))
    monkeypatch.setattr(i18n,'_CACHE',{})
    calls=[]; snapshots=[]
    original=i18n.flush_cache
    def flush():
        snapshots.append(len(i18n._CACHE)); original()
    monkeypatch.setattr(i18n,'flush_cache',flush)
    def batch(items,target,source):
        calls.append(len(items)); return {k:'译'+v for k,v in items.items()}
    entries={f'k{i}':f'Instruction number {i}, passport E12345678 on 2026-09-13' for i in range(95)}
    out=i18n.translate_catalog(entries,'zh-CN',batch_translator=batch)
    assert out['status']=='ok'
    assert sorted(calls)==[15,40,40]
    assert snapshots==[95]  # baseline rewrote the entire growing cache19times
    saved=json.loads((tmp_path/'i18n_cache.json').read_text())
    assert saved==i18n._CACHE
    assert all('E12345678' in v and '2026-09-13' in v for v in out['entries'].values())
    assert not list(tmp_path.glob('.i18n-*'))
    again=i18n.translate_catalog(entries,'zh-CN',batch_translator=batch)
    assert again==out and len(calls)==3 and snapshots==[95]


def test_one_unavailable_chunk_does_not_discard_paid_successes(monkeypatch,tmp_path):
    monkeypatch.setenv('ELLIS_DATA_DIR',str(tmp_path));monkeypatch.setattr(i18n,'_CACHE',{})
    def batch(items,target,source):
        if 'k0' in items: raise i18n.TranslationUnavailable('fixture')
        return {k:'译'+v for k,v in items.items()}
    entries={f'k{i}':f'Condition {i}' for i in range(45)}
    out=i18n.translate_catalog(entries,'zh-CN',batch_translator=batch)
    assert out['status']=='partial'
    assert out['entries']['k0']=='Condition 0'
    assert out['entries']['k44']=='译Condition 44'
    assert len(i18n._CACHE)==5
