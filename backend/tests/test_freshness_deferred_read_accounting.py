"""A discarded write cannot erase the source reads and comparisons it used."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import threading
import time

import pytest

from app.visa_snapshot import freshness
from .test_freshness_comparison_reuse import h, seed, run, URL


@pytest.mark.parametrize('supported', [True, False])
def test_cas_failure_reports_completed_io_without_persisting_verification(h, monkeypatch, supported):
    row = seed(h)
    before = deepcopy(row.verification)
    failed_url = 'https://www.mofa.go.jp/unavailable'
    monkeypatch.setattr(freshness, 'candidate_sources', lambda *_a, **_k: [URL, failed_url])
    if not supported:
        h.texts[URL] = 'Visitors should contact a consulate for general visa information.'
        h.answer['page_relevant'] = False

    def reject(db, row, entry, **kwargs):
        assert entry['outcome'] == ('checked' if supported else 'page_not_relevant')
        db.rollback()
        return False
    monkeypatch.setattr(freshness, '_commit_recheck', reject)
    result = run(h, row)
    assert result['outcome'] == 'concurrent_change'
    assert result['source_reads'] == 1 and result['source_fetch_failures'] == 1
    assert result['model_comparisons'] == 1 and result['model_comparisons_reused'] == 0
    assert result['changed'] == [] and not result.get('disputed')
    h.db.refresh(row)
    assert row.verification == before and row.fresh_until is None


def test_worker_counts_read_and_deferred_without_claiming_verification(h, monkeypatch):
    import app.db
    row = seed(h)
    spec = importlib.util.spec_from_file_location('freshness_deferred_accounting_sweep',
        Path(__file__).resolve().parents[1] / 'scripts' / 'freshness_sweep.py')
    sweep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sweep)
    monkeypatch.setattr(app.db, 'SessionLocal', lambda: h.db)
    def reject(db, row, entry, **kwargs):
        db.rollback()
        return False
    monkeypatch.setattr(freshness, '_commit_recheck', reject)
    delta = sweep._check_route(row.cache_key, time.monotonic() + 30, threading.Event())
    assert delta['attempted'] == delta['read'] == delta['source_reads'] == delta['deferred'] == 1
    assert delta['model_comparisons'] == 1
    assert delta['verified'] == delta['renewed'] == delta['corrected'] == delta['errors'] == 0


def test_detail_stage_started_during_source_read_keeps_io_counts(h, monkeypatch):
    row = seed(h)
    compare = freshness._call
    def start_detail(*args, **kwargs):
        row.verification = {'detail_pending': True, 'concurrent_detail_metadata': 'preserve'}
        h.db.commit()
        return compare(*args, **kwargs)
    monkeypatch.setattr(freshness, '_call', start_detail)
    result = run(h, row)
    assert result['outcome'] == 'detail_pending'
    assert result['source_reads'] == result['model_comparisons'] == 1
    assert result['source_fetch_failures'] == 0
    h.db.refresh(row)
    assert row.verification == {'detail_pending': True, 'concurrent_detail_metadata': 'preserve'}
    assert row.fresh_until is None
