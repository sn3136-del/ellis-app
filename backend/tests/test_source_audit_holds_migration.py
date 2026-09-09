"""Protective data migration must be explicit, recoverable and idempotent."""
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.visa_snapshot.models import KimiRouteGuidanceCache, DatabaseIssueReport, DatabaseChangeLog
from app.visa_snapshot.freshness import active_disputed_fields

spec = importlib.util.spec_from_file_location('source_hold_migration', Path(__file__).parents[1] / 'scripts/apply_source_audit_holds.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


@pytest.fixture
def cache_database(tmp_path):
    path = tmp_path / 'cache.db'
    engine = create_engine('sqlite:///' + str(path))
    for model in (KimiRouteGuidanceCache, DatabaseIssueReport, DatabaseChangeLog):
        model.__table__.create(engine)
    _, entries = mod._read_manifest(mod.DEFAULT_MANIFEST)
    assert len(entries) == 4
    with Session(engine) as db:
        for key, route, _ in entries:
            db.add(KimiRouteGuidanceCache(cache_key=key, route=route, guidance={'untouched': key}))
            db.add(DatabaseChangeLog(cache_key=key, route=route, changes={'preserve': True}))
        # Existing reader and resolved audit reports must retain their history.
        key, route, _ = entries[0]
        db.add(DatabaseIssueReport(cache_key=key, route=route, field='source_audit',
                                   reported_by='reader', status='open', note='reader history'))
        db.add(DatabaseIssueReport(cache_key=key, route=route, field='source_audit',
                                   reported_by='freshness_monitor', status='corrected', note='earlier review'))
        # Reuse a still-open legacy dated audit without mutating its evidence.
        key, route, _ = entries[1]
        db.add(DatabaseIssueReport(cache_key=key.replace('|unknown|', '|2026-10|'),
                                   route=route, field='source_audit', reported_by='freshness_monitor',
                                   status='acknowledged', note='preserve evidence', proposal={'old': 'proof'}))
        db.commit()
    yield path, engine, entries
    engine.dispose()


def table_rows(path, table):
    with sqlite3.connect(path) as db:
        return db.execute('select * from ' + table + ' order by id').fetchall()


def test_all_four_preview_backup_and_idempotent_holds(cache_database, tmp_path):
    path, engine, entries = cache_database
    before = {t: table_rows(path, t) for t in ('kimi_route_guidance_cache', 'database_change_log', 'database_issue_reports')}
    dry = mod.migrate(path)
    assert dry['matched_routes'] == 4 and dry['missing_canonical'] == []
    assert [p['action'] for p in dry['plan']].count('insert_hold') == 3
    assert [p['action'] for p in dry['plan']].count('reuse_existing') == 1
    assert not dry['applied']
    assert before == {t: table_rows(path, t) for t in before}
    with pytest.raises(ValueError, match='backup'):
        mod.migrate(path, apply=True)
    backup = tmp_path / 'before-holds.db'
    done = mod.migrate(path, apply=True, backup=backup)
    assert (done['inserted'], done['reused'], done['repointed_existing']) == (3, 1, 1)
    assert before == {t: table_rows(backup, t) for t in before}
    for table in ('kimi_route_guidance_cache', 'database_change_log'):
        assert before[table] == table_rows(path, table)
    with Session(engine) as db:
        for key, _, _ in entries:
            assert active_disputed_fields(db, key) == ['source_audit']
        reports = db.query(DatabaseIssueReport).all()
        assert len(reports) == 6
        old = next(r for r in reports if r.note == 'preserve evidence')
        assert old.proposal == {'old': 'proof'} and old.status == 'acknowledged'
        assert any(r.note == 'reader history' and r.reported_by == 'reader' for r in reports)
        assert any(r.note == 'earlier review' and r.status == 'corrected' for r in reports)
        new = next(r for r in reports if r.note.startswith('Reviewed source audit:'))
        assert new.proposal['sources'] and new.proposal['unresolved']
    first = table_rows(path, 'database_issue_reports')
    again = mod.migrate(path, apply=True, backup=tmp_path / 'second.db')
    assert (again['inserted'], again['reused'], again['repointed_existing']) == (0, 4, 0)
    assert first == table_rows(path, 'database_issue_reports')
    with pytest.raises(ValueError, match='new, separate'):
        mod.migrate(path, apply=True, backup=backup)


def test_missing_canonical_aborts_every_hold_before_backup(cache_database, tmp_path):
    path, _, entries = cache_database
    with sqlite3.connect(path) as db:
        db.execute('delete from kimi_route_guidance_cache where cache_key=?', (entries[0][0],))
    original = table_rows(path, 'database_issue_reports')
    dry = mod.migrate(path)
    assert dry['matched_routes'] == 3 and dry['missing_canonical'] == [entries[0][0]]
    backup = tmp_path / 'must-not-exist.db'
    with pytest.raises(ValueError, match='All manifest routes'):
        mod.migrate(path, apply=True, backup=backup)
    assert not backup.exists() and original == table_rows(path, 'database_issue_reports')


def test_manifest_document_scope_and_duplicate_validation(tmp_path):
    data = json.loads(mod.DEFAULT_MANIFEST.read_text())
    data['holds'] = [dict(data['holds'][0], route_key='USA|BFA|tourism|diplomatic_passport')]
    p = tmp_path / 'scoped.json'; p.write_text(json.dumps(data))
    _, entries = mod._read_manifest(p)
    assert entries[0][0].endswith('|doc:diplomatic_passport')
    data['holds'].append(data['holds'][0]);p.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='duplicate canonical'):
        mod._read_manifest(p)
