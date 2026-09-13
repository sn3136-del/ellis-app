"""Restarted detail jobs keep their hold until their generation is source-checked.

All sources/providers below are deterministic fixtures; no network/model calls.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy.sql.dml import Update

from app.db import SessionLocal
from app.visa_snapshot import detail_jobs as jobs, fetching, freshness, kimi_primary as kp
from app.visa_snapshot.fetching import FetchResult
from app.visa_snapshot.models import DatabaseIssueReport, KimiRouteGuidanceCache as Cache

ROUTE = {"passport_nationality": "CHN", "passport_issuing_country": "CHN",
         "destination_country": "JPN", "travel_document_type": "ordinary_passport",
         "travel_purpose": "tourism"}
URL = "https://www.mofa.go.jp/visa/recovery-test"
GUIDANCE = {"disposition": "VISA_EXEMPT", "visa_category": "Tourism",
            "permitted_stay": "30 days", "passport_validity": "valid for the stay",
            "required_documents": ["passport"], "application_channel": "not_required",
            "visa_products": [], "source_url": URL}
TEXT = ("Chinese nationals may visit Japan visa-free for tourism. The permitted stay is 30 days. "
        "Visa category: Tourism. The passport must be valid for the stay. Required documents: passport. "
        "No appointment is required. No interview is required.")
PAGE = FetchResult(requested_url=URL, ok=True, final_url=URL, final_hostname="www.mofa.go.jp",
    http_status=200, content_text=TEXT, content_hash="test-source", retrieved_at="2026-09-12T00:00:00Z")
ANSWER = {"page_relevant": True, "page_is_nationality_specific": True, "consistent": True,
          "corrected_fields": {}, "evidence": {
              "visa_category": "Visa category: Tourism",
              "appointment_required": "No appointment is required",
              "interview_required": "No interview is required",
              "permitted_stay": "The permitted stay is 30 days",
              "passport_validity": "The passport must be valid for the stay",
              "required_documents": "Required documents: passport"}}


@pytest.fixture(autouse=True)
def isolate(db, monkeypatch, tmp_path):
    from app.visa_snapshot import verified_overrides as vo
    empty = tmp_path / "empty.json"; empty.write_text("[]")
    monkeypatch.setattr(vo, "OVERRIDES", empty); vo.reload()
    db.query(DatabaseIssueReport).delete(); db.query(Cache).delete(); db.commit()
    monkeypatch.setattr(kp, "provider_suspension", lambda: None)
    yield
    fetching.set_fetcher(None); freshness.set_provider(None); kp.set_provider(None); vo.reload()


def seed(db, *, guidance=None, verification=None, missing=None):
    row = Cache(cache_key=kp.cache_key(ROUTE), route=deepcopy(ROUTE), status=kp.STATUS_PRIMARY,
        guidance=deepcopy(GUIDANCE if guidance is None else guidance), missing_fields=missing or [],
        contradictions=[], generated_at=datetime.now(timezone.utc)-timedelta(days=1),
        verification=deepcopy(verification or {"detail_pending": True,
            "operator_released": {"by": "old-operator"}, "history": ["preserve"]}), model="detail-recovery-test")
    db.add(row); db.commit(); return row


def expire(db, row):
    ver = deepcopy(row.verification)
    ver[jobs.JOB]["lease_until"] = (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
    ver[jobs.JOB]["retry_at"] = (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
    row.verification = ver; db.commit()


def source_stubs():
    calls = []
    fetching.set_fetcher(lambda url, **kw: PAGE)
    def answer(system, user):
        calls.append(user); return deepcopy(ANSWER)
    freshness.set_provider(answer)
    return calls


def test_old_legacy_job_is_due_but_recent_job_is_not(db):
    row = seed(db)
    assert jobs.retry_eligible(row)
    assert freshness.due_rows(db) == [row]
    row.generated_at = datetime.now(timezone.utc); db.commit()
    assert not jobs.retry_eligible(row)
    assert freshness.due_rows(db) == []
    assert freshness.recheck_row(db, row)["outcome"] == "detail_pending"


def test_job_owner_survives_new_session_and_expired_owner_cannot_finish(db):
    row = seed(db); lease = jobs.claim(db, row, recovery=True)
    assert lease and jobs.owns(lease, row)
    with SessionLocal() as restarted:
        persisted = restarted.get(Cache, row.id)
        assert jobs.owns(lease, persisted)
        assert jobs.claim(restarted, persisted, recovery=True) is None
    expire(db, row)
    newer = jobs.claim(db, row, recovery=True)
    assert newer["token"] != lease["token"]
    assert jobs.finish(db, row, lease, success=True, reason="obsolete") is False
    assert row.verification["detail_pending"] is True
    assert row.verification[jobs.JOB]["attempts"] == 2


def test_claim_race_has_one_winner_and_does_not_duplicate_attempts(db, monkeypatch):
    row = seed(db); execute = db.execute; winners = []
    def intercepted(stmt, *args, **kwargs):
        if isinstance(stmt, Update) and not winners:
            with SessionLocal() as other:
                second = other.get(Cache, row.id)
                winners.append(jobs.claim(other, second, recovery=True))
        return execute(stmt, *args, **kwargs)
    monkeypatch.setattr(db, "execute", intercepted)
    assert jobs.claim(db, row, recovery=True) is None
    assert len(winners) == 1 and winners[0]
    assert row.verification[jobs.JOB]["token"] == winners[0]["token"]
    assert row.verification[jobs.JOB]["attempts"] == 1


def test_failed_attempts_back_off_then_pause_window_without_releasing(db):
    row = seed(db); original = deepcopy(row.guidance)
    for attempt in range(1, jobs.MAX_ATTEMPTS + 1):
        lease = jobs.claim(db, row, recovery=True)
        assert lease
        assert jobs.finish(db, row, lease, success=False, reason="source_unavailable")
        assert row.guidance == original and row.verification["detail_pending"] is True
        assert row.verification["operator_released"] == {"by": "old-operator"}
        assert row.verification["history"] == ["preserve"]
        assert not jobs.retry_eligible(row)
        assert freshness.due_rows(db) == []
        assert row.verification[jobs.JOB]["attempts"] == attempt
        expire(db, row)
    assert row.verification[jobs.JOB]["state"] == "cooldown"
    assert jobs.claim(db, row, recovery=True) is None
    assert freshness.due_rows(db) == []


def test_generation_change_during_initial_detail_cannot_write_or_clear(db):
    row = seed(db); original = deepcopy(row.guidance)
    def provider(system, user):
        with SessionLocal() as other:
            new = other.get(Cache, row.id)
            new.generated_at = datetime.now(timezone.utc)
            new.guidance = dict(new.guidance, exceptions=["new generation"])
            new.verification = dict(new.verification, detail_job=jobs.reserve(new.generated_at))
            other.commit()
        return {"exceptions": ["obsolete worker"]}
    kp.set_provider(provider)
    kp.fill_detail(db, row.cache_key, ROUTE, "{}")
    db.refresh(row)
    assert row.guidance == dict(original, exceptions=["new generation"])
    assert row.verification["detail_pending"] is True
    assert row.verification[jobs.JOB]["state"] == "queued"


@pytest.mark.parametrize("answer", [None, {}, {"disposition": "VISA_EXEMPT"}])
def test_empty_detail_is_failure_and_never_runs_completion_callback(db, answer):
    row = seed(db); callbacks = []
    kp.set_provider(lambda *_: answer)
    kp.fill_detail(db, row.cache_key, ROUTE, "{}", after=lambda *_: callbacks.append(True))
    assert row.verification["detail_pending"] is True
    assert row.verification[jobs.JOB]["state"] == "retry_wait"
    assert callbacks == [] and row.guidance == GUIDANCE


def test_concurrent_answer_between_validation_and_finish_cannot_be_overwritten(db, monkeypatch):
    row = seed(db); lease = jobs.claim(db, row, recovery=True)
    with SessionLocal() as other:
        changed = other.get(Cache, row.id)
        changed.guidance = dict(changed.guidance, exceptions=["new authoritative correction"])
        other.commit()
    assert not jobs.finish(db, row, lease, success=True, reason="stale", values={"guidance": GUIDANCE})
    assert row.guidance["exceptions"] == ["new authoritative correction"]
    assert row.verification["detail_pending"] is True


def test_finish_cas_preserves_metadata_written_after_ownership_check(db, monkeypatch):
    row = seed(db); lease = jobs.claim(db, row, recovery=True)
    execute = db.execute; writes = []
    def intercepted(stmt, *args, **kwargs):
        if isinstance(stmt, Update) and not writes:
            with SessionLocal() as other:
                r = other.get(Cache, row.id)
                r.verification = dict(r.verification, new_review={"by": "another-reviewer"})
                other.commit(); writes.append(True)
        return execute(stmt, *args, **kwargs)
    monkeypatch.setattr(db, "execute", intercepted)
    assert not jobs.finish(db, row, lease, success=True, reason="complete")
    assert row.verification["new_review"] == {"by": "another-reviewer"}
    assert row.verification["detail_pending"] is True


def test_sql_expiry_guard_rejects_finish_even_if_python_owner_check_is_stale(db, monkeypatch):
    row = seed(db); lease = jobs.claim(db, row, recovery=True); expire(db, row)
    monkeypatch.setattr(jobs, "owns", lambda *a, **k: True)
    assert not jobs.finish(db, row, lease, success=True, reason="late")
    assert row.verification["detail_pending"] is True


@pytest.mark.parametrize("case", ["cancelled", "budget", "suspended"])
def test_cancelled_or_unfunded_work_does_not_claim_or_consume_attempt(db, monkeypatch, case):
    row = seed(db)
    monkeypatch.setattr(kp, "provider_suspension", lambda: {"reason": "billing"} if case == "suspended" else None)
    result = freshness.recheck_row(db, row, budget_seconds=0 if case == "budget" else 30,
                                  should_stop=lambda: case == "cancelled")
    assert result["outcome"] == "budget_exhausted"
    assert jobs.JOB not in row.verification and row.verification["detail_pending"] is True


def test_real_source_retry_completes_only_current_owned_generation(db):
    row = seed(db, missing=["old_detail_marker"]); calls = source_stubs()
    result = freshness.recheck_row(db, row)
    assert result["outcome"] == "checked", result
    assert result["renewed"] is True, str(result) + str(row.verification["grounded_check"])
    assert result["detail_recovered"] is True, result
    assert result["detail_pending"] is False and calls
    assert row.verification[jobs.JOB]["state"] == "complete"
    assert row.verification["grounded_check"]["renewed"] is True
    assert row.verification["last_good_check"]["renewed"] is True
    assert row.fresh_until is not None
    assert row.verification["operator_released"] == {"by": "old-operator"}
    assert row.missing_fields == [] and row.contradictions == []
    assert row.guidance == GUIDANCE
    assert "disposition" in row.verification["grounded_check"]["verified_fields"]


def test_source_partial_result_and_original_hold_survive_failed_completion(db):
    row = seed(db); source_stubs()
    freshness.set_provider(lambda *_: dict(ANSWER, evidence={}))
    result = freshness.recheck_row(db, row)
    assert result["outcome"] == "checked" and result["source_reads"] == 1
    assert result["detail_pending"] is True and result["detail_recovered"] is False
    assert result["renewed"] is False and result["unverified_fields"]
    assert row.verification[jobs.JOB]["state"] == "retry_wait"
    assert row.verification["grounded_check"]["verified_fields"] == ["disposition"]
    assert row.guidance == GUIDANCE


def test_source_check_cannot_commit_after_concurrent_new_generation(db):
    row = seed(db); source_stubs()
    def provider(*_):
        with SessionLocal() as other:
            new = other.get(Cache, row.id)
            new.generated_at = datetime.now(timezone.utc)
            new.verification = dict(new.verification, detail_job=jobs.reserve(new.generated_at))
            other.commit()
        return deepcopy(ANSWER)
    freshness.set_provider(provider)
    result = freshness.recheck_row(db, row)
    assert result["outcome"] == "concurrent_change", result
    assert result["detail_pending"] is True
    assert "grounded_check" not in row.verification
    assert row.verification[jobs.JOB]["state"] == "queued"


def test_existing_released_flag_cannot_finish_incomplete_source_checked_details(db):
    row = seed(db, guidance={"disposition": "VISA_EXEMPT", "source_url": URL})
    source_stubs()
    result = freshness.recheck_row(db, row)
    assert not result["detail_recovered"] and result["detail_pending"]
    assert row.verification["operator_released"] == {"by": "old-operator"}
    assert row.guidance == {"disposition": "VISA_EXEMPT", "source_url": URL}


def test_active_lease_context_resets_after_exception():
    assert jobs.active_lease() is None
    with pytest.raises(RuntimeError):
        with jobs.source_owner({"token": "example"}):
            raise RuntimeError("test")
    assert jobs.active_lease() is None


def test_expired_queued_worker_cannot_restart_the_memory_detail_call(db):
    row = seed(db)
    reservation = jobs.reserve(row.generated_at)
    row.verification = dict(row.verification, detail_job=reservation); db.commit()
    expire(db, row)
    calls = []
    kp.set_provider(lambda *_: calls.append(True))
    kp.fill_detail(db, row.cache_key, ROUTE, "{}", expected_generation=reservation["generation"],
                   expected_token=reservation["token"])
    assert calls == [] and row.verification["detail_pending"] is True
    assert jobs.claim(db, row, recovery=True)


def test_expired_source_owner_cannot_stamp_or_write_even_with_stale_python_check(db, monkeypatch):
    row = seed(db); lease = jobs.claim(db, row, recovery=True); expire(db, row)
    before = deepcopy(row.guidance)
    monkeypatch.setattr(jobs, "owns", lambda *a, **k: True)
    row.guidance = dict(row.guidance, permitted_stay="90 days")
    with jobs.source_owner(lease):
        committed = freshness._commit_recheck(db, row, {"outcome": "checked", "consistent": True},
            expected_guidance=before, expected_route=ROUTE)
    assert not committed and row.guidance == before
    assert "grounded_check" not in row.verification


def test_source_commit_loses_cas_to_new_owner_without_history_or_fact_write(db, monkeypatch):
    row = seed(db); lease = jobs.claim(db, row, recovery=True)
    before = deepcopy(row.guidance); execute = db.execute; writes = []
    def intercepted(stmt, *args, **kwargs):
        if isinstance(stmt, Update) and not writes:
            with SessionLocal() as other:
                newer = other.get(Cache, row.id)
                newer.generated_at = datetime.now(timezone.utc)
                newer.verification = dict(newer.verification, detail_job=jobs.reserve(newer.generated_at))
                other.commit(); writes.append(True)
        return execute(stmt, *args, **kwargs)
    monkeypatch.setattr(db, "execute", intercepted)
    row.guidance = dict(row.guidance, permitted_stay="90 days")
    with jobs.source_owner(lease):
        committed = freshness._commit_recheck(db, row, {"outcome": "checked", "consistent": True},
            expected_guidance=before, expected_route=ROUTE)
    assert not committed and row.guidance == before
    assert row.verification[jobs.JOB]["state"] == "queued"
    assert "grounded_check" not in row.verification


def test_provider_exception_retains_source_attempt_and_core(db):
    row = seed(db); source_stubs()
    def unavailable(*_):
        raise RuntimeError("provider unavailable")
    freshness.set_provider(unavailable)
    result = freshness.recheck_row(db, row)
    assert result["outcome"] == "provider_error" and result["detail_pending"] is True
    assert result["source_reads"] == 1 and result["model_comparisons"] == 1
    assert row.guidance == GUIDANCE
    assert row.verification[jobs.JOB]["state"] == "retry_wait"
    assert row.verification["operator_released"] == {"by": "old-operator"}


def test_sweep_preserves_partial_fact_counters_without_false_full_renewal(db):
    import importlib.util
    from pathlib import Path
    import threading
    import time
    spec = importlib.util.spec_from_file_location("detail_recovery_sweep",
        Path(__file__).resolve().parents[1] / "scripts" / "freshness_sweep.py")
    sweep = importlib.util.module_from_spec(spec); spec.loader.exec_module(sweep)
    row = seed(db); source_stubs()
    freshness.set_provider(lambda *_: dict(ANSWER, evidence={}))
    result = sweep._check_route(row.cache_key, time.monotonic()+30, threading.Event())
    assert result["attempted"] == result["source_reads"] == result["model_comparisons"] == 1
    assert result["verified"] == 1  # the visa requirement was actually verified
    assert result["renewed"] == 0 and result["partial"] == 1
    db.refresh(row)
    assert row.verification["detail_pending"] is True


def test_full_lookup_recovers_abandoned_job_with_sources_without_new_route_decision(db, monkeypatch):
    row = seed(db); calls = source_stubs(); started = []; joined = []
    def source_retry(key, **kwargs):
        started.append((key, kwargs))
        freshness.recheck_row(db, row)
    def no_new_decision(*_):
        pytest.fail("cached recovery must not regenerate a decision from memory")
    kp.set_provider(no_new_decision)
    monkeypatch.setattr(kp, "is_available", lambda: True)
    monkeypatch.setattr(kp, "_recover_detail_async", source_retry)
    monkeypatch.setattr(kp, "join_detail_stage", lambda **kwargs: joined.append(kwargs) or True)
    result = kp.get_route_guidance(db, ROUTE, stage="full")
    assert len(started) == len(calls) == len(joined) == 1
    assert started[0][0] == joined[0]["key"] == row.cache_key
    assert result["cached"] is True and not result.get("detail_pending")
    assert not row.verification.get("detail_pending")


def test_worker_registry_deduplicates_same_owner_but_not_a_new_generation(monkeypatch):
    threads = []
    class Thread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs; self.live = False; threads.append(self)
        def start(self): self.live = True
        def is_alive(self): return self.live
    monkeypatch.setattr(kp.threading, "Thread", Thread)
    monkeypatch.setattr(kp, "_DETAIL_THREADS", [])
    for generation in ("old", "old", "new", ("new", "expired-token")):
        kp._start_detail_thread("route", lambda: None, name="test", generation=generation)
    assert len(kp._DETAIL_THREADS) == 3
    assert sum(t.live for t in threads) == 3


def test_window_expiry_recovers_after_transient_failure_with_one_cas_winner(db, monkeypatch):
    row = seed(db)
    for _ in range(jobs.MAX_ATTEMPTS):
        lease = jobs.claim(db, row, recovery=True)
        assert jobs.finish(db, row, lease, success=False, reason="provider_timeout")
        expire(db, row)
    assert not jobs.retry_eligible(row) and row.verification["detail_pending"] is True
    ver = deepcopy(row.verification)
    old_start = datetime.now(timezone.utc) - timedelta(seconds=jobs.WINDOW_SECONDS + 1)
    ver[jobs.JOB]["window_started_at"] = old_start.isoformat()
    row.verification = ver; db.commit()
    assert jobs.retry_eligible(row) and freshness.due_rows(db) == [row]
    execute = db.execute; winner = []
    def intercepted(stmt, *args, **kwargs):
        if isinstance(stmt, Update) and not winner:
            with SessionLocal() as restarted:
                persistent = restarted.get(Cache, row.id)
                winner.append(jobs.claim(restarted, persistent, recovery=True))
        return execute(stmt, *args, **kwargs)
    monkeypatch.setattr(db, "execute", intercepted)
    assert jobs.claim(db, row, recovery=True) is None
    assert winner[0] and row.verification[jobs.JOB]["attempts"] == 1
    assert row.verification[jobs.JOB]["token"] == winner[0]["token"]
    assert jobs._date(row.verification[jobs.JOB]["window_started_at"]) > old_start
    assert row.verification["operator_released"] == {"by": "old-operator"}
    assert row.verification["detail_pending"] is True


def test_window_boundary_does_not_steal_a_still_live_owner(db):
    row = seed(db); lease = jobs.claim(db, row, recovery=True)
    ver = deepcopy(row.verification)
    ver[jobs.JOB].update(attempts=3, window_started_at=(datetime.now(timezone.utc)
        - timedelta(seconds=jobs.WINDOW_SECONDS+1)).isoformat())
    row.verification = ver; db.commit()
    assert jobs.owns(lease, row)
    assert not jobs.retry_eligible(row)
    assert jobs.claim(db, row, recovery=True) is None


def test_completed_source_recovery_updates_uncertain_status_in_actual_reader_and_records(db):
    from app import main
    row = seed(db, missing=["old_detail_marker"])
    row.status = kp.STATUS_UNCERTAIN; db.commit()
    source_stubs()
    before = [r for r in main._build_tstation_rows(db) if r.get("_cache_key") == row.cache_key]
    assert before and all(r["_held"] for r in before), before
    result = freshness.recheck_row(db, row)
    assert result["detail_recovered"] is True
    assert row.status == kp.STATUS_PRIMARY
    reader = kp.get_route_guidance(db, ROUTE, stage="full")
    records = [r for r in main._build_tstation_rows(db) if r.get("_cache_key") == row.cache_key]
    assert reader["status"] == kp.STATUS_PRIMARY and reader["cached"] is True
    assert not reader.get("detail_pending") and not reader["held"], reader
    assert records and all(r["_held"] is False for r in records), records
    assert all(not r.get("_evidence_low") for r in records), records


def test_failed_source_recovery_preserves_uncertain_status_and_pending(db):
    row = seed(db, guidance={"disposition": "VISA_EXEMPT", "source_url": URL})
    row.status = kp.STATUS_UNCERTAIN; db.commit()
    source_stubs()
    result = freshness.recheck_row(db, row)
    assert result["detail_pending"] is True and not result["detail_recovered"]
    assert row.status == kp.STATUS_UNCERTAIN


def test_post_source_commit_raw_edit_does_not_rebase_proof_or_clear_pending(db, monkeypatch):
    row = seed(db); source_stubs(); real_check = freshness.recheck_row; changed = []
    def check_then_edit(session, current, **kwargs):
        report = real_check(session, current, **kwargs)
        if jobs.active_lease() is not None and report.get("renewed") and not changed:
            with SessionLocal() as other:
                newer = other.get(Cache, row.id)
                newer.guidance = dict(newer.guidance, permitted_stay="90 days")
                other.commit(); changed.append(True)
        return report
    monkeypatch.setattr(freshness, "recheck_row", check_then_edit)
    result = freshness.recheck_row(db, row)
    assert changed and result["detail_pending"] is True and not result["detail_recovered"]
    assert result["renewed"] is False
    assert_not_renewed_in_readers(db, row)
    assert row.guidance["permitted_stay"] == "90 days"
    assert row.verification["grounded_check"]["field_sources"]["permitted_stay"]["quote"] == "The permitted stay is 30 days"


def install_new_stay_override():
    import json
    from app.visa_snapshot import verified_overrides as vo
    vo.OVERRIDES.write_text(json.dumps([{"route": {"nationality": "CHN", "destination": "JPN",
        "travel_purpose": "tourism", "travel_document_type": "ordinary_passport"},
        "fields": {"permitted_stay": "90 days"}, "source_url": URL,
        "verified_at": datetime.now(timezone.utc).isoformat(), "verified_by": "fixture reviewer",
        "verifier": "human", "note": "Fixture: concurrently reviewed stay correction"}]))
    vo.reload()
    assert vo.apply(GUIDANCE, ROUTE)[0]["permitted_stay"] == "90 days"


def test_post_source_commit_override_edit_without_raw_edit_stays_pending(db, monkeypatch):
    row = seed(db); source_stubs(); real_check = freshness.recheck_row; changed = []
    def check_then_edit(session, current, **kwargs):
        report = real_check(session, current, **kwargs)
        if jobs.active_lease() is not None and report.get("renewed") and not changed:
            install_new_stay_override(); changed.append(True)
        return report
    monkeypatch.setattr(freshness, "recheck_row", check_then_edit)
    result = freshness.recheck_row(db, row)
    assert changed and result["detail_pending"] is True and not result["detail_recovered"]
    assert result["renewed"] is False
    assert_not_renewed_in_readers(db, row)
    assert row.guidance == GUIDANCE  # only the independently stored override changed


def test_override_edit_between_completion_validation_and_final_write_is_rejected(db, monkeypatch):
    row = seed(db); source_stubs(); real_finish = jobs.finish; changed = []
    def change_before_finish(*args, **kwargs):
        if kwargs.get("success") and not changed:
            install_new_stay_override(); changed.append(True)
        return real_finish(*args, **kwargs)
    monkeypatch.setattr(jobs, "finish", change_before_finish)
    result = freshness.recheck_row(db, row)
    assert changed and result["detail_pending"] is True and not result["detail_recovered"]
    assert result["renewed"] is False
    assert row.verification["detail_pending"] is True and row.guidance == GUIDANCE


def test_source_completeness_never_closes_an_existing_material_issue(db):
    row = seed(db); source_stubs()
    issue = DatabaseIssueReport(org_id="platform", cache_key=row.cache_key, route=ROUTE,
        reported_by="freshness_monitor", status="open", field="permitted_stay", note="Fixture source disagreement",
        proposal={"fields": {"permitted_stay": {"page_says": "90 days", "record_holds": "30 days"}}})
    db.add(issue); db.commit()
    result = freshness.recheck_row(db, row)
    assert result["detail_pending"] is True and not result["detail_recovered"]
    assert row.verification["detail_pending"] is True and row.guidance == GUIDANCE
    db.refresh(issue)
    assert issue.status == "open"


def spawn_actual_operator_writer():
    import os, subprocess, sys
    from app.visa_snapshot import verified_overrides as vo
    code = '''
import json, pathlib, sys
from app.visa_snapshot import verified_overrides as vo
vo.OVERRIDES = pathlib.Path(sys.argv[1]); vo.reload()
entry = {"route": {"nationality": "CHN", "destination": "JPN"},
    "source_url": "https://www.mofa.go.jp/visa/recovery-test", "verified_at": "2026-09-12",
    "verified_by": "fixture reviewer", "verifier": "human", "note": "Reviewed stay fixture",
    "fields": {"permitted_stay": "90 days"}}
print("ready", flush=True)
vo.append_operator_entry(entry)
print("written", flush=True)
'''
    env = dict(os.environ, ELLIS_OPERATOR_OVERRIDES=str(vo.operator_overrides_path()),
        PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    return subprocess.Popen([sys.executable, '-B', '-c', code, str(vo.OVERRIDES)],
        env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def test_actual_cross_process_operator_write_serializes_after_final_source_cas(db, monkeypatch):
    import select
    from app.visa_snapshot import verified_overrides as vo
    row = seed(db); source_stubs(); original_write = jobs._write; child = []
    def interleave(*args, **kwargs):
        # This is after the final source snapshot comparison, inside its lock.
        if (kwargs.get("values") or {}).get("status") == kp.STATUS_PRIMARY and not child:
            process = spawn_actual_operator_writer(); child.append(process)
            assert process.stdout.readline().strip() == 'ready'
            assert select.select([process.stdout], [], [], .1)[0] == [], 'writer bypassed source completion lock'
        return original_write(*args, **kwargs)
    monkeypatch.setattr(jobs, "_write", interleave)
    try:
        result = freshness.recheck_row(db, row)
        assert result["detail_recovered"] and not result["detail_pending"]
        stdout, stderr = child[0].communicate(timeout=10)
        assert child[0].returncode == 0, stderr
        assert stdout.strip() == 'written'
        # A later reviewed edit is allowed, but cannot win BEFORE the old
        # generation's proof-bound completion commits.
        vo.reload()
        assert vo.apply(GUIDANCE, ROUTE)[0]["permitted_stay"] == '90 days'
    finally:
        for process in child:
            if process.poll() is None: process.kill(); process.communicate()


def test_final_source_check_bypasses_a_pinned_reader_table(db, monkeypatch):
    from app.visa_snapshot import verified_overrides as vo
    row = seed(db); source_stubs(); real_finish = jobs.finish
    # The helper above intentionally asserts current lookup, so replace it
    # with the same write executed outside the pinned reader snapshot.
    def edit_with_fresh_store(*args, **kwargs):
        if kwargs.get("success"):
            old = vo._PINNED.table; vo._PINNED.table = None
            try: install_new_stay_override()
            finally: vo._PINNED.table = old
        return real_finish(*args, **kwargs)
    monkeypatch.setattr(jobs, "finish", edit_with_fresh_store)
    with vo.pinned_table() as pinned:
        result = freshness.recheck_row(db, row)
        assert vo._PINNED.table is pinned
    assert not result["detail_recovered"] and result["detail_pending"] and not result["renewed"]


def test_lock_timeout_after_completed_source_read_keeps_counts_and_pending(db, monkeypatch):
    from contextlib import contextmanager
    from app.visa_snapshot import verified_overrides as vo
    row = seed(db); source_stubs()
    @contextmanager
    def busy():
        raise ValueError('another source edit is in progress; retry shortly')
        yield
    monkeypatch.setattr(vo, 'operator_write_lock', busy)
    result = freshness.recheck_row(db, row)
    assert result['outcome'] == 'detail_retry_failed' and result['detail_pending']
    assert result['source_reads'] == result['model_comparisons'] == 1
    assert not result['renewed'] and not result['detail_recovered']
    assert row.verification[jobs.JOB]['state'] == 'retry_wait'
    assert row.verification['grounded_check']['verified_fields']
    assert_not_renewed_in_readers(db, row)


def test_cross_process_operator_lock_wait_is_bounded():
    import os, subprocess, sys, time
    from app.visa_snapshot import verified_overrides as vo
    code = '''
import pathlib, sys
from app.visa_snapshot import verified_overrides as vo
vo.OVERRIDES=pathlib.Path(sys.argv[1])
with vo.operator_write_lock():
 print("locked",flush=True)
 sys.stdin.readline()
'''
    env = dict(os.environ, ELLIS_OPERATOR_OVERRIDES=str(vo.operator_overrides_path()),
        PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    process = subprocess.Popen([sys.executable, '-B', '-c', code, str(vo.OVERRIDES)], env=env,
        text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert process.stdout.readline().strip() == 'locked'
        start = time.monotonic()
        with pytest.raises(ValueError, match='source edit is in progress'):
            with vo.operator_write_lock(timeout_seconds=.03):
                pytest.fail('cross-process lock was bypassed')
        assert time.monotonic() - start < .5
    finally:
        process.communicate('\n', timeout=10)
    assert process.returncode == 0
    with vo.operator_write_lock(timeout_seconds=.1):
        pass  # timeout released both process and thread lock resources


def assert_not_renewed_in_readers(db, row):
    from app import main
    from sqlalchemy import select
    from app.visa_snapshot import tstation, verified_overrides as vo
    assert row.fresh_until is None
    assert row.verification['grounded_check']['renewed'] is False
    assert row.verification['last_good_check']['renewed'] is False
    listed = main._freshness_rows(db, datetime.now(timezone.utc), select, vo, tstation, kp, Cache, freshness)
    public = next(item for item in listed if item['cache_key'] == row.cache_key)
    assert public['renewed'] is False and public['fresh_until'] is None
    reader = kp.get_route_guidance(db, ROUTE, stage='core')
    assert reader.get('grounded_check', {}).get('renewed') is not True
    assert row.verification['detail_pending'] is True


def test_concurrent_expiry_change_cannot_be_overwritten_by_detail_completion(db, monkeypatch):
    row = seed(db); source_stubs(); real_check = freshness.recheck_row; changed = []
    other_expiry = datetime.now(timezone.utc) + timedelta(days=7)
    def check_then_edit(session, current, **kwargs):
        report = real_check(session, current, **kwargs)
        if jobs.active_lease() is not None and report.get('renewed') and not changed:
            with SessionLocal() as other:
                newer = other.get(Cache, row.id)
                newer.fresh_until = other_expiry
                other.commit(); changed.append(True)
        return report
    monkeypatch.setattr(freshness, 'recheck_row', check_then_edit)
    result = freshness.recheck_row(db, row)
    assert changed and result['detail_pending'] and not result['detail_recovered']
    assert not result['renewed'] and row.verification['grounded_check']['renewed'] is False
    assert row.fresh_until.replace(tzinfo=timezone.utc) == other_expiry
