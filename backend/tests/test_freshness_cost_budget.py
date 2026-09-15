"""Paid-budget deferral preserves free reads, partial proof, and real backlog."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

from app.providers import refresh_budget as rb
from app.visa_snapshot import freshness
from tests.test_freshness_comparison_reuse import h, seed, run, URL, TEXT
from tests.test_freshness_sweep_runtime import sweep, setup


def denied(*args,**kwargs):
    raise rb.RefreshBudgetExceeded('fixture cycle exhausted')


def test_real_recheck_fetches_source_but_never_verifies_or_renews_denied_call(h,monkeypatch):
    row=seed(h); original=deepcopy(row.guidance)
    monkeypatch.setattr(freshness,'_call',denied)
    result=run(h,row)
    assert result['outcome']=='cost_budget_exhausted'
    assert result['source_reads']==1 and result['model_comparisons']==0
    assert result['model_comparisons_deferred']==1
    assert row.guidance==original and row.fresh_until is None
    check=row.verification['grounded_check']
    assert not check.get('renewed') and not check.get('verified_fields')
    assert check['source_checks'][0]['source_read_at']==h.day


def test_paid_exhaustion_still_allows_fresh_exact_comparison_reuse(h,monkeypatch):
    row=seed(h);run(h,row)
    h.day='2026-09-09T15:00:00+00:00'
    monkeypatch.setattr(freshness,'_call',denied)
    result=run(h,row)
    assert result['model_comparisons_reused']==1
    assert result['model_comparisons_deferred']==0
    assert len(h.fetches)==2 and result['source_reads']==1


def test_partial_supported_page_then_denied_second_page_cannot_renew(h,monkeypatch):
    other='https://www.mofa.go.jp/visa/fees'
    h.texts[other]=TEXT+' Government fee is 10 JPY.'
    row=seed(h,{'disposition':'VISA_REQUIRED','source_url':URL,'official_portal_url':other})
    def compare(system,user,**kwargs):
        if other in user: # source_url may appear in stored guidance too
            import json
            if json.loads(user)['official_page_text'].endswith('10 JPY.'):
                return denied()
        return {'page_relevant':True,'page_is_nationality_specific':True,'consistent':True,
                'corrected_fields':{},'evidence':{}}
    monkeypatch.setattr(freshness,'_call',compare)
    result=run(h,row)
    assert result['model_comparisons_deferred']==1
    assert result['source_reads']==2
    assert result['renewed'] is False and row.fresh_until is None
    assert row.verification['grounded_check']['verified_fields']==['disposition']


def test_sweep_keeps_free_reads_and_reports_budget_deferred_rows_as_backlog(sweep,monkeypatch):
    rows=[SimpleNamespace(cache_key=str(i),route={},guidance={},verification={}) for i in range(3)]
    setup(monkeypatch,rows);seen=[]
    def check(db,row,**kwargs):
        assert rb.active() is not None
        seen.append(row.cache_key)
        row.verification={'grounded_check':{'at':datetime.now(timezone.utc).isoformat(),
            'outcome':'cost_budget_exhausted','model_comparisons_deferred':1}}
        return {'outcome':'cost_budget_exhausted','source_reads':1,'model_comparisons_deferred':1}
    monkeypatch.setattr(freshness,'recheck_row',check)
    assert sweep.main()==0
    status=freshness.read_sweep_status()
    assert len(seen)==3
    assert status['read']==status['deferred']==status['backlog_remaining']==3
    assert status['state']=='cost_budget_exhausted' and status['renewed']==status['verified']==0
    assert rb.active() is None
    cycle=status['cost_budget']['cycle_id']
    assert sweep.main()==0
    assert freshness.read_sweep_status()['cost_budget']['cycle_id']==cycle
    assert len(seen)==6
