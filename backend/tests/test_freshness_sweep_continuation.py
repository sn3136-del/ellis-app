"""An interrupted deployment must not buy completed source checks again."""
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from tests.test_freshness_sweep_runtime import sweep, setup
from app.visa_snapshot import freshness


def old_status(now, **changes):
    return {'schema_version': 2, 'state': 'interrupted', 'running': False,
            'started_at': (now-timedelta(hours=1)).isoformat(),
            'finished_at': (now-timedelta(minutes=1)).isoformat(),
            'time_budget_seconds': 18000, 'attempted': 3, **changes}


def row(key, at=None, outcome=None):
    return SimpleNamespace(cache_key=key, verification={
        'grounded_check': {'at': at.isoformat() if at else None, 'outcome': outcome}})


def test_restart_skips_completed_attempts_but_retries_cancelled_and_unattempted(sweep, monkeypatch):
    now=datetime.now(timezone.utc);start=now-timedelta(hours=1)
    prior=old_status(now)
    path=freshness.sweep_status_path();path.write_text(json.dumps(prior))
    rows=[row('read',start+timedelta(minutes=1),'checked'),
          row('unreadable',start+timedelta(minutes=2),'fetch_failed'),
          row('unsupported',start+timedelta(minutes=3),'page_not_relevant'),
          row('route-budget',start+timedelta(minutes=4),'budget_exhausted'),
          row('recent-before-cycle',start-timedelta(minutes=5),'checked'),
          row('cancelled',now-timedelta(minutes=1),'cancelled'),
          row('old',start-timedelta(hours=1),'fetch_failed'),row('never')]
    setup(monkeypatch,rows);seen=[]
    def check(db,r,**kwargs):
        seen.append(r.cache_key)
        assert 0 < kwargs['budget_seconds'] <=75
        r.verification={'grounded_check':{'at':datetime.now(timezone.utc).isoformat(),'outcome':'page_not_relevant'}}
        return {'outcome':'page_not_relevant','source_reads':1}
    monkeypatch.setattr(freshness,'recheck_row',check)
    assert sweep.main()==0
    s=freshness.read_sweep_status()
    assert set(seen)=={'cancelled','old','never'}
    assert s['selected']==s['attempted']==s['read']==3
    assert s['verified']==s['renewed']==0
    assert s['cycle_started_at']==prior['started_at']
    assert 14000 < s['time_budget_seconds'] <14401
    assert s['prior_attempt_results']==3 and s['backlog_remaining']==0
    assert json.loads(path.with_suffix('.previous.json').read_text())==prior


def test_second_interruption_does_not_reset_original_deadline(sweep):
    now=datetime.now(timezone.utc)
    original=now-timedelta(hours=4)
    previous=old_status(now, schema_version=3, cycle_started_at=original.isoformat(),
                        cycle_time_budget_seconds=18000, time_budget_seconds=7000)
    plan=sweep._continuation(previous,now)
    assert plan['remaining_seconds']==3600
    assert plan['cycle_started_at']==original.isoformat()


@pytest.mark.parametrize('changes',[
 {'state':'complete'}, {'state':'complete_with_errors'}, {'schema_version':1},
 {'time_budget_seconds':True}, {'time_budget_seconds':0}, {'time_budget_seconds':18001},
 {'started_at':'garbage'}, {'started_at':'2999-01-01T00:00:00Z'},
 {'started_at':'2000-01-01T00:00:00Z'},
])
def test_completed_expired_or_invalid_run_is_not_used_as_a_resume_plan(sweep,changes):
    now=datetime.now(timezone.utc)
    assert sweep._continuation(old_status(now,**changes),now) is None


def test_abandoned_running_status_can_resume_only_after_process_lock_is_obtained(sweep):
    now=datetime.now(timezone.utc)
    assert sweep._continuation(old_status(now,state='running',running=True),now)


def test_broken_checkpoint_is_not_overwritten_or_used_to_spend(sweep,monkeypatch):
    path=freshness.sweep_status_path();path.write_text('invalid json')
    setup(monkeypatch,[])
    monkeypatch.setattr(freshness,'audit_integrity',lambda *_:(_ for _ in ()).throw(AssertionError('no work')))
    assert sweep.main()==1 and path.read_text()=='invalid json'


def test_future_or_malformed_attempt_cannot_skip_needed_check(sweep):
    now=datetime.now(timezone.utc);start=now-timedelta(hours=1)
    rows=[row('future',now+timedelta(hours=1),'checked'),row('missing')]
    assert sweep._remaining_cycle_rows(rows,start.isoformat(),now)==rows


def test_initial_checkpoint_write_failure_stops_before_audit_or_paid_work(sweep, monkeypatch):
    setup(monkeypatch, [row('never')])
    monkeypatch.setattr(sweep, '_write_status', lambda *_: (_ for _ in ()).throw(OSError('fixture disk full')))
    def forbidden(*args, **kwargs):
        raise AssertionError('no audit or paid checks without a durable cycle checkpoint')
    monkeypatch.setattr(freshness, 'audit_integrity', forbidden)
    monkeypatch.setattr(freshness, 'recheck_row', forbidden)
    assert sweep.main() == 1
    assert not freshness.sweep_status_path().exists()


def test_later_checkpoint_failure_stops_dispatch_and_drains_private_sessions(sweep, monkeypatch):
    rows = [row(str(i)) for i in range(10)]
    state = setup(monkeypatch, rows)
    original_write = sweep._write_status
    seen = []
    def fail_after_dispatch(path, status):
        if status.get('scheduled', 0):
            raise OSError('fixture disk full after dispatch')
        original_write(path, status)
    monkeypatch.setattr(sweep, '_write_status', fail_after_dispatch)
    def check(db, r, **kwargs):
        seen.append(r.cache_key)
        return {'outcome': 'page_not_relevant', 'source_reads': 1}
    monkeypatch.setattr(freshness, 'recheck_row', check)
    assert sweep.main() == 1
    assert len(seen) <= 1  # only the already-dispatched worker may have begun
    assert all(session.closed for session in state.sessions)
    assert freshness.read_sweep_status()['scheduled'] == 0


def test_final_inflight_cancellation_remains_interrupted_and_retryable(sweep, monkeypatch):
    now = datetime.now(timezone.utc)
    path = freshness.sweep_status_path()
    path.write_text(json.dumps(old_status(now)))
    unfinished = row('last-inflight')
    setup(monkeypatch, [unfinished])
    # Mirror due_rows' time filter, rather than the broad runtime fixture
    # that returns every row regardless of the requested freshness cutoff.
    def due(db, *, older_than_hours, **kwargs):
        at = sweep._timestamp(unfinished.verification['grounded_check'].get('at'))
        cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
        return [unfinished] if at is None or at < cutoff else []
    monkeypatch.setattr(freshness, 'due_rows', due)
    def cancel(db, r, **kwargs):
        kwargs['should_stop'].__self__.set()
        r.verification = {'grounded_check': {
            'at': datetime.now(timezone.utc).isoformat(), 'outcome': 'cancelled'}}
        return {'outcome': 'cancelled'}
    monkeypatch.setattr(freshness, 'recheck_row', cancel)
    assert sweep.main() == 1
    status = freshness.read_sweep_status()
    assert status['state'] == 'interrupted'
    assert status['deferred'] == status['backlog_remaining'] == 1
    assert status['in_flight'] == 0 and not status['running']
    # The next invocation still recognizes this original cycle and retries
    # the cancelled route despite its recent persisted attempt timestamp.
    assert sweep._continuation(status, datetime.now(timezone.utc))
    assert sweep._remaining_cycle_rows([unfinished], status['cycle_started_at'], datetime.now(timezone.utc)) == [unfinished]
