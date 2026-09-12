"""Durable operational run state distinguishes attempts, reads and verification."""
import fcntl
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest

from app.visa_snapshot import freshness, kimi_primary as kp, verified_overrides as vo, consistency_runtime


@pytest.fixture
def sweep(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("freshness_sweep_runtime", Path(__file__).resolve().parents[1] / "scripts" / "freshness_sweep.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    monkeypatch.setenv("ELLIS_FRESHNESS_STATUS_FILE", str(tmp_path / "status.json"))
    monkeypatch.setattr(module, "SPACING_SECONDS", 0)
    monkeypatch.setattr(module, "_load_row", lambda db, key: db.load(key))
    monkeypatch.setattr(freshness, "active_disputed_fields", lambda *_: [])
    monkeypatch.setattr(freshness, "audit_integrity", lambda _: {"checked": 1, "violated": 0, "created": 0})
    monkeypatch.setattr(vo, "apply", lambda g, _: (g, None))
    monkeypatch.setattr(consistency_runtime, "run_report", lambda *_a, **_k: {
        "state": "complete", "routes_checked": 0, "records_checked": 0, "findings_total": 0})
    return module


def setup(monkeypatch, rows):
    import app.db
    state = SimpleNamespace(sessions=[], rows={r.cache_key: r for r in rows})
    class FakeSession:
        def __init__(self):
            self.owner = threading.get_ident()
            self.closed = False
            self.rollbacks = 0
            state.sessions.append(self)
        def load(self, key):
            assert self.owner == threading.get_ident(), 'session crossed thread boundary'
            return state.rows.get(key)
        def rollback(self):
            assert self.owner == threading.get_ident()
            self.rollbacks += 1
        def close(self):
            assert self.owner == threading.get_ident()
            self.closed = True
    monkeypatch.setattr(app.db, "SessionLocal", FakeSession)
    monkeypatch.setattr(freshness, "due_rows", lambda *_a, **_k: rows)
    return state


def test_deterministic_report_precedes_paid_work_and_survives_provider_suspension(sweep, monkeypatch):
    row = SimpleNamespace(cache_key='a', route={}, guidance={}, verification={})
    setup(monkeypatch, [row])
    events = []
    def report(db, **kwargs):
        events.append('report')
        assert kwargs['deadline'] > sweep.time.monotonic()
        kwargs['on_progress']({'state': 'running', 'routes_checked': 0})
        return {'state': 'complete', 'routes_checked': 1, 'findings_total': 2}
    def check(*args, **kwargs):
        assert events == ['report']
        events.append('source')
        return {'outcome': 'provider_error', 'source_reads': 1, 'model_comparisons': 1}
    monkeypatch.setattr(consistency_runtime, 'run_report', report)
    monkeypatch.setattr(freshness, 'recheck_row', check)
    monkeypatch.setattr(kp, 'provider_suspension', lambda: {'reason': 'fixture account suspension'})
    monkeypatch.setattr(kp, 'rate_gate_saturated', lambda: False)
    assert sweep.main() == 1
    status = freshness.read_sweep_status()
    assert events == ['report', 'source']
    assert status['state'] == 'provider_suspended'
    assert status['consistency'] == {'state': 'complete', 'routes_checked': 1, 'findings_total': 2}
    assert status['attempted'] == status['read'] == status['model_comparisons'] == 1
    assert status['verified'] == 0 and status['backlog_remaining'] == 0


def test_report_failure_does_not_prevent_source_refresh_or_pollute_its_counters(sweep, monkeypatch):
    row = SimpleNamespace(cache_key='a', route={}, guidance={}, verification={})
    state = setup(monkeypatch, [row])
    monkeypatch.setattr(consistency_runtime, 'run_report', lambda *_a, **_k:
        {'state': 'failed', 'failure': 'report_write_failed', 'routes_checked': 4})
    monkeypatch.setattr(freshness, 'recheck_row', lambda *_a, **_k:
        {'outcome': 'page_not_relevant', 'source_reads': 1})
    assert sweep.main() == 0
    status = freshness.read_sweep_status()
    assert status['state'] == 'complete' and status['consistency']['state'] == 'failed'
    assert status['attempted'] == status['read'] == status['insufficient_evidence'] == 1
    assert status['errors'] == status['verified'] == 0
    assert sum(s.rollbacks for s in state.sessions) == 1


def test_metrics_never_count_attempt_or_disputed_read_as_verified(sweep, monkeypatch):
    rows = [SimpleNamespace(cache_key=str(i), route={}, guidance={"disposition": "VISA_REQUIRED"}, verification={}) for i in range(4)]
    setup(monkeypatch, rows)
    def check(_db, row, **_kwargs):
        i = int(row.cache_key)
        if i == 0:
            return {"outcome": "fetch_failed"}
        row.verification = {"grounded_check": {"outcome": "checked", "evidence_contract": freshness.EVIDENCE_CONTRACT,
            "consistent": i == 3, "verified_fields": ["disposition"]}}
        return {"outcome": "checked", "changed": ["fee"] if i == 1 else [], "disputed": ["stay"] if i == 2 else []}
    monkeypatch.setattr(freshness, "recheck_row", check)
    monkeypatch.setattr(freshness, "note_unreadable", lambda *_: None)
    assert sweep.main() == 0
    status = freshness.read_sweep_status()
    assert (status["attempted"], status["read"], status["verified"]) == (4, 3, 1)
    assert status["corrected"] == status["disputed"] == status["unreadable"] == 1
    assert not status["running"] and status["finished_at"] and status["started_at"]
    assert status["due_before"] == status["eligible_now"] == 4
    assert status["backlog_remaining"] == 0


def test_budget_exhaustion_reports_unprocessed_backlog(sweep, monkeypatch):
    rows = [SimpleNamespace(cache_key="a")]
    setup(monkeypatch, rows)
    monkeypatch.setattr(sweep, "MAX_SECONDS", 0)
    monkeypatch.setattr(freshness, "recheck_row", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no time left")))
    assert sweep.main() == 0
    status = freshness.read_sweep_status()
    assert status["state"] == "budget_exhausted" and status["attempted"] == 0
    assert status["backlog_remaining"] == 1 and not status["running"]


def test_overlapping_run_keeps_first_workers_progress(sweep, monkeypatch):
    path = freshness.sweep_status_path()
    path.write_text(json.dumps({"running": True, "attempted": 7}))
    with path.with_suffix(".lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert sweep.main() == 0
    assert freshness.read_sweep_status() == {"running": True, "attempted": 7}


def test_unexpected_failure_is_durable_and_marks_run_finished(sweep, monkeypatch):
    setup(monkeypatch, [])
    monkeypatch.setattr(freshness, "audit_integrity", lambda _: (_ for _ in ()).throw(RuntimeError("fixture failure")))
    assert sweep.main() == 1
    status = freshness.read_sweep_status()
    assert status["state"] == "failed" and status["last_error"]["message"].endswith("fixture failure")
    assert not status["running"] and status["finished_at"]


def test_legacy_weak_check_never_gets_run_verification_credit(sweep, monkeypatch):
    row = SimpleNamespace(cache_key="a", route={}, guidance={"disposition": "VISA_EXEMPT"},
        verification={"grounded_check": {"outcome": "checked", "consistent": True}})
    setup(monkeypatch, [row])
    monkeypatch.setattr(freshness, "recheck_row", lambda *_a, **_k: {"outcome": "checked"})
    assert sweep.main() == 0
    assert freshness.read_sweep_status()["verified"] == 0


def test_concurrent_writer_defers_attempt_without_filing_outage(sweep, monkeypatch):
    row = SimpleNamespace(cache_key='a')
    setup(monkeypatch, [row])
    monkeypatch.setattr(freshness, 'recheck_row', lambda *_a, **_k: {'outcome': 'concurrent_change'})
    monkeypatch.setattr(freshness, 'note_unreadable', lambda *_: (_ for _ in ()).throw(AssertionError('not an outage')))
    assert sweep.main() == 0
    status = freshness.read_sweep_status()
    assert status['attempted'] == status['deferred'] == 1
    assert status['unreadable'] == status['verified'] == status['renewed'] == 0


@pytest.mark.parametrize('outcome', ['checked', 'budget_exhausted', 'provider_error'])
@pytest.mark.parametrize('provider_state', ['suspended', 'rate_limited', 'available'])
def test_cycle_provider_stop_survives_partial_and_budget_outcomes(sweep, monkeypatch, outcome, provider_state):
    row = SimpleNamespace(cache_key='a', route={}, guidance={}, verification={
        'grounded_check': {'outcome': 'checked', 'evidence_contract': freshness.EVIDENCE_CONTRACT,
            'verified_fields': [], 'unverified_fields': ['disposition'], 'renewed': False,
            'source_checks': [{'outcome': 'checked'}, {'outcome': 'provider_error'}]}})
    setup(monkeypatch, [row])
    monkeypatch.setattr(freshness, 'recheck_row', lambda *_a, **_k:
        {'outcome': outcome, 'source_reads': 1, 'model_comparisons': 2})
    monkeypatch.setattr(kp, 'provider_suspension', lambda:
        {'reason': 'fixture account suspension'} if provider_state == 'suspended' else None)
    monkeypatch.setattr(kp, 'rate_gate_saturated', lambda: provider_state == 'rate_limited')
    delta = sweep._check_route('a', sweep.time.monotonic() + 30, threading.Event())
    assert delta['provider_suspended'] == int(provider_state == 'suspended')
    assert delta['provider_rate_limited'] == int(provider_state == 'rate_limited')
    assert delta['read'] == 1 and delta['model_comparisons'] == 2
    assert delta['verified'] == delta['renewed'] == 0
    assert delta['partial'] == int(outcome == 'checked')
    assert delta['deferred'] == int(outcome == 'budget_exhausted')


def test_reused_comparisons_are_separate_from_reads_renewals_and_deferred_work(sweep, monkeypatch):
    rows = [SimpleNamespace(cache_key=str(i), route={}, guidance={'disposition': 'VISA_REQUIRED'},
                            verification={}) for i in range(2)]
    setup(monkeypatch, rows)
    def check(db, row, **kwargs):
        if row.cache_key == '1':
            return {'outcome': 'concurrent_change', 'source_reads': 1, 'model_comparisons': 1,
                    'model_comparisons_reused': 0}
        row.verification = {'grounded_check': {'outcome': 'checked', 'evidence_contract': freshness.EVIDENCE_CONTRACT,
            'consistent': True, 'verified_fields': ['disposition'], 'unverified_fields': ['government_fee'],
            'renewed': False}}
        return {'outcome': 'checked', 'source_reads': 3, 'model_comparisons': 2, 'model_comparisons_reused': 1}
    monkeypatch.setattr(freshness, 'recheck_row', check)
    assert sweep.main() == 0
    status = freshness.read_sweep_status()
    assert status['model_comparisons'] == 3 and status['model_comparisons_reused'] == 1
    assert status['source_reads'] == 4 and status['read'] == 2
    assert status['verified'] == status['partial'] == status['deferred'] == 1
    assert status['renewed'] == 0


def test_four_workers_use_private_sessions_and_coordinator_only_status_writes(sweep, monkeypatch):
    rows = [SimpleNamespace(cache_key=str(i)) for i in range(12)]
    state = setup(monkeypatch, rows)
    barrier = threading.Barrier(4)
    guard = threading.Lock()
    active = peak = 0
    def check(db, row, **kwargs):
        nonlocal active, peak
        assert 0 < kwargs['budget_seconds'] <= 75
        with guard:
            active += 1
            peak = max(peak, active)
        barrier.wait(timeout=3)
        with guard:
            active -= 1
        return {'outcome': 'fetch_failed'}
    writer_thread = threading.get_ident()
    write = sweep._write_status
    def guarded_write(path, status):
        assert threading.get_ident() == writer_thread
        assert status['in_flight'] <= 4
        write(path, status)
    monkeypatch.setattr(sweep, '_write_status', guarded_write)
    monkeypatch.setattr(freshness, 'recheck_row', check)
    monkeypatch.setattr(freshness, 'note_unreadable', lambda *_: None)
    assert sweep.main() == 0
    status = freshness.read_sweep_status()
    assert peak == 4 and status['attempted'] == status['completed'] == 12
    assert status['in_flight'] == 0 and status['cycle_unattempted'] == 0
    assert len(state.sessions) == 14 and all(session.closed for session in state.sessions)


def test_worker_failure_rolls_back_its_session_and_other_routes_finish(sweep, monkeypatch):
    rows = [SimpleNamespace(cache_key=str(i)) for i in range(5)]
    state = setup(monkeypatch, rows)
    def check(db, row, **_):
        if row.cache_key == '2':
            raise RuntimeError('isolated worker failure')
        return {'outcome': 'fetch_failed'}
    monkeypatch.setattr(freshness, 'recheck_row', check)
    monkeypatch.setattr(freshness, 'note_unreadable', lambda *_: None)
    assert sweep.main() == 1
    status = freshness.read_sweep_status()
    assert status['state'] == 'complete_with_errors' and status['errors'] == 1
    assert status['completed'] == status['attempted'] == 5 and status['in_flight'] == 0
    assert sum(s.rollbacks for s in state.sessions) == 1 and all(s.closed for s in state.sessions)


def test_deadline_stops_dispatch_and_drains_only_four_inflight_routes(sweep, monkeypatch):
    rows = [SimpleNamespace(cache_key=str(i)) for i in range(20)]
    state = setup(monkeypatch, rows)
    monkeypatch.setattr(sweep, 'MAX_SECONDS', 0.2)
    entered = []
    def check(db, row, **kwargs):
        entered.append(row.cache_key)
        assert kwargs['budget_seconds'] <= 0.2
        while not kwargs['should_stop']():
            threading.Event().wait(0.005)
        return {'outcome': 'cancelled'}
    monkeypatch.setattr(freshness, 'recheck_row', check)
    assert sweep.main() == 0
    status = freshness.read_sweep_status()
    assert status['state'] == 'budget_exhausted' and 0 < len(entered) <= 4
    assert status['scheduled'] == status['completed'] == len(entered)
    assert status['cycle_unattempted'] == 20 - len(entered)
    assert status['in_flight'] == 0 and not status['running'] and all(s.closed for s in state.sessions)


def test_six_hour_cycle_selects_all_917_current_routes(sweep, monkeypatch):
    rows = [SimpleNamespace(cache_key=str(i)) for i in range(917)]
    setup(monkeypatch, rows)
    monkeypatch.setattr(freshness, 'recheck_row', lambda *_a, **_k: {'outcome': 'fetch_failed'})
    monkeypatch.setattr(freshness, 'note_unreadable', lambda *_: None)
    def due(db, **kwargs):
        assert kwargs['older_than_hours'] == 0.25
        return rows
    monkeypatch.setattr(freshness, 'due_rows', due)
    assert sweep.main() == 0
    status = freshness.read_sweep_status()
    assert status['target_cycle_hours'] == 6 and status['time_budget_seconds'] <= 5 * 3600
    assert status['selected'] == status['attempted'] == status['completed'] == 917
    assert status['cycle_unattempted'] == 0 and status['workers'] == 4


def test_readable_but_insufficient_and_interrupted_checks_are_not_fetch_failures(sweep, monkeypatch):
    reports = [
        {'outcome':'page_not_relevant', 'source_reads':3, 'source_fetch_failures':1},
        {'outcome':'provider_error', 'source_reads':1, 'source_fetch_failures':0},
        {'outcome':'budget_exhausted', 'source_reads':2, 'source_fetch_failures':1},
        {'outcome':'fetch_failed', 'source_reads':0, 'source_fetch_failures':2},
        {'outcome':'no_official_source'},
    ]
    rows = [SimpleNamespace(cache_key=str(i)) for i in range(len(reports))]
    setup(monkeypatch, rows)
    monkeypatch.setattr(freshness, 'recheck_row', lambda db, row, **_: reports[int(row.cache_key)])
    outage_rows = []
    monkeypatch.setattr(freshness, 'note_unreadable', lambda db, row, report: outage_rows.append(row.cache_key))
    assert sweep.main() == 0
    status = freshness.read_sweep_status()
    assert status['attempted'] == 5 and status['read'] == 3
    assert status['source_reads'] == 6 and status['source_fetch_failures'] == 4
    assert status['insufficient_evidence'] == status['provider_failed'] == status['no_official_source'] == 1
    assert status['unreadable'] == status['deferred'] == 1
    assert status['verified'] == status['renewed'] == 0
    assert outage_rows == ['3']


@pytest.mark.parametrize('outcome', ['page_not_relevant', 'provider_error', 'no_official_source', 'budget_exhausted'])
def test_non_transport_failure_cannot_file_source_unreadable_issue(outcome):
    class UnusableDB:
        def execute(self, *_):
            raise AssertionError('a readable-but-insufficient page is not an outage')
    freshness.note_unreadable(UnusableDB(), None, {'outcome':outcome, 'source_reads':1})


@pytest.mark.parametrize('outcome', ['provider_error', 'checked', 'budget_exhausted'])
def test_a_suspended_provider_stops_the_cycle_and_names_the_reason(sweep, monkeypatch, outcome):
    """One suspension notice ends dispatch: the remaining routes stay due for
    the next cycle instead of each failing in turn, the status names the
    reason for the Freshness tab, the exit is non-zero so systemd records
    the alert, and the next invocation treats the cycle as resumable."""
    rows = [SimpleNamespace(cache_key=str(i), route={}, guidance={"disposition": "VISA_REQUIRED"}, verification={}) for i in range(6)]
    setup(monkeypatch, rows)
    kp.clear_provider_suspension()
    attempted = []

    def check(_db, row, **_kwargs):
        attempted.append(row.cache_key)
        kp.note_provider_suspension("account suspended due to insufficient balance, please recharge")
        row.verification = {'grounded_check': {'outcome': 'checked',
            'evidence_contract': freshness.EVIDENCE_CONTRACT, 'consistent': True,
            'verified_fields': ['disposition'], 'unverified_fields': ['government_fee'], 'renewed': False}}
        return {"outcome": outcome, 'source_reads': 1}
    monkeypatch.setattr(freshness, "recheck_row", check)
    try:
        assert sweep.main() == 1
        status = freshness.read_sweep_status()
        assert status["state"] == "provider_suspended"
        assert "insufficient balance" in status["provider_notice"]
        assert status["provider_suspended"] >= 1
        assert status["provider_failed"] == (len(attempted) if outcome == 'provider_error' else 0)
        assert status['verified'] == status['partial'] == (len(attempted) if outcome == 'checked' else 0)
        assert status['renewed'] == 0 and status['read'] == status['completed'] == len(attempted)
        assert not status["running"] and status["finished_at"]
        assert len(attempted) < 6, "dispatch stopped before every route failed the same way"
        assert status["backlog_remaining"] >= 1
        from datetime import datetime, timezone
        plan = sweep._continuation(status, datetime.now(timezone.utc))
        assert plan is not None and plan["cycle_started_at"], "a suspended cycle resumes inside its own budget"
    finally:
        kp.clear_provider_suspension()


@pytest.mark.parametrize('outcome', ['provider_error', 'checked', 'budget_exhausted'])
def test_a_rate_gate_stuck_at_its_cap_stops_the_cycle_like_a_suspension(sweep, monkeypatch, outcome):
    """When every 429 has doubled the gate up to its cap and the account is
    still refusing, the remaining routes would each time out against a
    closed gate. The cycle stops with its own state, exits non-zero and
    resumes next run, the same as an account suspension."""
    rows = [SimpleNamespace(cache_key=str(i), route={}, guidance={"disposition": "VISA_REQUIRED"}, verification={}) for i in range(6)]
    setup(monkeypatch, rows)
    kp.clear_provider_suspension()
    kp._rate_gate_clear()
    attempted = []

    def check(_db, row, **_kwargs):
        attempted.append(row.cache_key)
        for _ in range(6):
            kp._rate_gate_hit()
        return {"outcome": outcome}
    monkeypatch.setattr(freshness, "recheck_row", check)
    try:
        assert kp.rate_gate_saturated() is False
        assert sweep.main() == 1
        status = freshness.read_sweep_status()
        assert status["state"] == "rate_limited"
        assert "rate limit" in status["provider_notice"]
        assert status["provider_rate_limited"] >= 1
        assert status["provider_failed"] == (len(attempted) if outcome == 'provider_error' else 0)
        assert status["provider_suspended"] == 0 and status["verified"] == 0
        assert len(attempted) < 6, "dispatch stopped before every route failed the same way"
        from datetime import datetime, timezone
        plan = sweep._continuation(status, datetime.now(timezone.utc))
        assert plan is not None and plan["cycle_started_at"], "a rate-limited cycle resumes inside its own budget"
    finally:
        kp._rate_gate_clear()
        kp.clear_provider_suspension()
