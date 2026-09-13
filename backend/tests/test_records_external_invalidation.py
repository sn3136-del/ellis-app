"""External source writers invalidate warm QC rows without model calls or restarts."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
import sqlite3
import threading

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import main
from app.models import Base
from app.visa_snapshot import kimi_primary as kp, verified_overrides as vo
from app.visa_snapshot.models import KimiRouteGuidanceCache
from tests.test_tstation_backend import ANSWER

ROUTE = {'passport_nationality': 'CHN', 'passport_issuing_country': 'CHN',
         'lawful_country_of_residence': 'CHN', 'destination_country': 'JPN',
         'travel_document_type': 'ordinary_passport', 'travel_purpose': 'tourism'}


@pytest.fixture
def state(tmp_path, monkeypatch):
    path = tmp_path / 'sources.db'
    engine = create_engine('sqlite:///' + str(path), connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(KimiRouteGuidanceCache(cache_key=kp.cache_key(ROUTE), route=deepcopy(ROUTE),
            guidance=deepcopy(ANSWER), status=kp.STATUS_PRIMARY,
            generated_at=datetime.now(timezone.utc)))
        db.commit()
    core = tmp_path / 'verified_overrides.json'; core.write_text('[]')
    operators = tmp_path / 'operators.json'; operators.write_text('[]')
    monkeypatch.setattr(vo, 'OVERRIDES', core)
    monkeypatch.setattr(vo, 'operator_overrides_path', lambda: operators)
    monkeypatch.setattr(vo, '_reviewed_overlay_paths', lambda: [])
    monkeypatch.setattr(main, '_RECORDS_CACHE', {'rows': None, 'built_at': 0.0, 'building': False,
        'dirty': False, 'generation': 0, 'built_generation': -1, 'external_version': None, 'test_enabled': True})
    monkeypatch.setattr(main, 'RECORDS_CACHE_SECONDS', 3600)
    monkeypatch.setattr(main, '_refresh_records_cache_in_background', lambda: pytest.fail('No background restart needed'))
    vo.reload()
    yield path, factory, operators
    vo.reload(); engine.dispose()


def read(factory):
    with factory() as db:
        return main._all_tstation_rows(db)


def external_processing(path, value, updated='2026-09-13T22:00:00.123456+00:00'):
    with sqlite3.connect(path) as db:
        raw = json.loads(db.execute('select guidance from kimi_route_guidance_cache').fetchone()[0])
        raw['processing_time'] = value
        db.execute('update kimi_route_guidance_cache set guidance=?, updated_at=?', (json.dumps(raw), updated))


def test_external_operator_replacement_changes_warm_actual_projection_even_with_preserved_stat(state):
    _, factory, operators = state
    entry = {'route': {'nationality': 'CHN', 'destination': 'JPN', 'travel_purpose': 'tourism',
                       'travel_document_type': 'ordinary_passport'}, 'verified_at': '2026-09-01',
             'verified_by': 'Source review', 'source_url': ANSWER['source_url'],
             'fields': {'processing_time': 'within 24 hours'}}
    operators.write_text(json.dumps([entry]))
    before = read(factory)
    assert before[0]['processing_text'] == 'within 24 hours'
    old_stat = operators.stat()
    entry['fields']['processing_time'] = 'within 48 hours'
    replacement = operators.with_suffix('.new'); replacement.write_text(json.dumps([entry]))
    os.replace(replacement, operators)
    os.utime(operators, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    assert vo._stat_sig(operators) == (old_stat.st_mtime_ns, old_stat.st_size)
    after = read(factory)
    assert after[0]['processing_text'] == 'within 48 hours'
    assert after is not before
    assert [r['visa_fee_amount'] for r in after] == [r['visa_fee_amount'] for r in before]


def test_external_sqlite_commit_refreshes_warm_actual_records_without_orm_event(state):
    path, factory, _ = state
    before = read(factory)
    generation = main._RECORDS_CACHE['generation']
    external_processing(path, 'within 24 hours')
    assert main._RECORDS_CACHE['generation'] == generation  # Separate writer had no Python callback.
    after = read(factory)
    assert before[0]['processing_text'] == '5 working days'
    assert after[0]['processing_text'] == 'within 24 hours'


def test_unrelated_audit_commit_does_not_discard_warm_rows(state):
    path, factory, _ = state
    before = read(factory)
    generation = main._RECORDS_CACHE['generation']
    with sqlite3.connect(path) as db:
        db.execute('create table unrelated_audit (id text)')
        db.execute('insert into unrelated_audit values (?)', ('customer lookup event',))
    assert read(factory) is before
    assert main._RECORDS_CACHE['generation'] == generation


def test_reader_after_external_commit_cannot_take_an_inflight_old_build(state, monkeypatch):
    path, factory, _ = state
    entered, release, observed_new = threading.Event(), threading.Event(), threading.Event()
    calls = []
    original_observe = main._observe_records_sources
    def observe(db):
        original_observe(db)
        if main._RECORDS_CACHE['generation'] > 0:
            observed_new.set()
    monkeypatch.setattr(main, '_observe_records_sources', observe)
    def build(db):
        raw = db.execute(select(KimiRouteGuidanceCache.guidance)).scalar_one()
        calls.append(raw['processing_time'])
        if len(calls) == 1:
            entered.set()
            assert release.wait(3)
        return [{'processing_text': raw['processing_time']}]
    monkeypatch.setattr(main, '_build_tstation_rows', build)
    with ThreadPoolExecutor(max_workers=2) as pool:
        old_reader = pool.submit(read, factory)
        assert entered.wait(3)
        external_processing(path, 'within 24 hours')
        new_reader = pool.submit(read, factory)
        assert observed_new.wait(3)
        release.set()
        assert old_reader.result(timeout=3) == [{'processing_text': '5 working days'}]
        assert new_reader.result(timeout=3) == [{'processing_text': 'within 24 hours'}]
    assert calls == ['5 working days', 'within 24 hours']
    assert not main._RECORDS_CACHE['dirty']


def test_version_observation_failure_releases_build_claim(state, monkeypatch):
    def failed(_):
        raise RuntimeError('source version unavailable')
    monkeypatch.setattr(main, '_records_external_version', failed)
    assert main._claim_records_build()
    with pytest.raises(RuntimeError, match='source version unavailable'):
        main._build_records_cache(None)
    assert not main._RECORDS_CACHE['building']


def test_row_version_change_is_not_masked_by_another_rows_later_timestamp(state):
    path, factory, _ = state
    with factory() as db:
        db.add(KimiRouteGuidanceCache(cache_key='other', updated_at=datetime(2027, 1, 1), guidance={}))
        db.commit()
        before = main._records_external_version(db)
    with sqlite3.connect(path) as db:
        db.execute('update kimi_route_guidance_cache set updated_at=? where cache_key=?',
                   ('2026-09-13 22:00:00.456789', kp.cache_key(ROUTE)))
    with factory() as db:
        assert main._records_external_version(db) != before
