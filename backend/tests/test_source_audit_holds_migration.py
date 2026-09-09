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
    assert len(entries) == 6
    with Session(engine) as db:
        for key, route, hold in entries:
            if hold.get('protect_uncached_route'):
                continue
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


def test_six_preview_backup_and_idempotent_holds_preserve_absent_routes(cache_database, tmp_path):
    path, engine, entries = cache_database
    before = {t: table_rows(path, t) for t in ('kimi_route_guidance_cache', 'database_change_log', 'database_issue_reports')}
    dry = mod.migrate(path)
    assert dry['matched_routes'] == 4 and dry['missing_canonical'] == []
    assert len(dry['uncached_protected_routes']) == 2
    assert [p['action'] for p in dry['plan']].count('insert_hold') == 3
    assert [p['action'] for p in dry['plan']].count('insert_uncached_hold') == 2
    assert [p['action'] for p in dry['plan']].count('reuse_existing') == 1
    assert not dry['applied']
    assert before == {t: table_rows(path, t) for t in before}
    with pytest.raises(ValueError, match='backup'):
        mod.migrate(path, apply=True)
    backup = tmp_path / 'before-holds.db'
    done = mod.migrate(path, apply=True, backup=backup)
    assert (done['inserted'], done['reused'], done['repointed_existing']) == (5, 1, 1)
    assert before == {t: table_rows(backup, t) for t in before}
    for table in ('kimi_route_guidance_cache', 'database_change_log'):
        assert before[table] == table_rows(path, table)
    with Session(engine) as db:
        for key, _, _ in entries:
            assert active_disputed_fields(db, key) == ['source_audit']
        reports = db.query(DatabaseIssueReport).all()
        assert len(reports) == 8
        for key in dry['uncached_protected_routes']:
            assert db.query(KimiRouteGuidanceCache).filter_by(cache_key=key).first() is None
            issue = next(r for r in reports if r.cache_key == key)
            assert issue.proposal['protect_uncached_route'] is True
        old = next(r for r in reports if r.note == 'preserve evidence')
        assert old.proposal == {'old': 'proof'} and old.status == 'acknowledged'
        assert any(r.note == 'reader history' and r.reported_by == 'reader' for r in reports)
        assert any(r.note == 'earlier review' and r.status == 'corrected' for r in reports)
        new = next(r for r in reports if r.note.startswith('Reviewed source audit:'))
        assert new.proposal['sources'] and new.proposal['unresolved']
    first = table_rows(path, 'database_issue_reports')
    again = mod.migrate(path, apply=True, backup=tmp_path / 'second.db')
    assert (again['inserted'], again['reused'], again['repointed_existing']) == (0, 6, 0)
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


@pytest.mark.parametrize('value', ['true', 'false', 1, 0, None, []])
def test_uncached_protection_requires_literal_boolean(tmp_path, value):
    data = json.loads(mod.DEFAULT_MANIFEST.read_text())
    data['holds'] = [dict(data['holds'][-1], protect_uncached_route=value)]
    manifest = tmp_path / 'invalid.json'; manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='must be a boolean'):
        mod._read_manifest(manifest)


def test_false_uncached_flag_does_not_waive_missing_row(cache_database, tmp_path):
    path, _, _ = cache_database
    data = json.loads(mod.DEFAULT_MANIFEST.read_text())
    data['holds'] = [dict(data['holds'][-1], protect_uncached_route=False)]
    manifest = tmp_path / 'not-authorized.json'; manifest.write_text(json.dumps(data))
    before = table_rows(path, 'database_issue_reports')
    assert mod.migrate(path, manifest=manifest)['plan'][0]['action'] == 'missing_canonical'
    with pytest.raises(ValueError, match='without explicit uncached protection'):
        mod.migrate(path, manifest=manifest, apply=True, backup=tmp_path / 'unused.db')
    assert before == table_rows(path, 'database_issue_reports')
    assert not (tmp_path / 'unused.db').exists()


def test_failed_uncached_insert_rolls_back_cached_and_uncached_holds(cache_database, tmp_path):
    path, _, _ = cache_database
    before = {t: table_rows(path, t) for t in
              ('kimi_route_guidance_cache', 'database_change_log', 'database_issue_reports')}
    with sqlite3.connect(path) as db:
        db.execute("""create trigger fail_uncached before insert on database_issue_reports
            when NEW.cache_key like 'IDN|%|VNM|family_visit|%'
            begin select raise(ABORT, 'fixture failure'); end""")
    with pytest.raises(sqlite3.IntegrityError, match='fixture failure'):
        mod.migrate(path, apply=True, backup=tmp_path / 'rollback.db')
    assert before == {t: table_rows(path, t) for t in before}
    assert before == {t: table_rows(tmp_path / 'rollback.db', t) for t in before}


@pytest.mark.parametrize('purpose', ['tourism', 'family_visit'])
def test_uncached_hold_blocks_actual_cold_lookup_and_future_canonical_reads(
        client, db, tmp_path, monkeypatch, purpose):
    from app.visa_snapshot import kimi_primary as kp, verified_overrides as vo, tstation
    route = {'passport_nationality': 'IDN', 'destination_country': 'VNM',
             'travel_purpose': purpose, 'travel_document_type': 'ordinary_passport'}
    key = kp.cache_key(route)
    db.query(KimiRouteGuidanceCache).filter_by(cache_key=key).delete()
    db.query(DatabaseIssueReport).filter_by(cache_key=key).delete()
    db.commit()
    data = json.loads(mod.DEFAULT_MANIFEST.read_text())
    data['holds'] = [h for h in data['holds'] if h['route_key'] == f'IDN|VNM|{purpose}|ordinary_passport']
    manifest = tmp_path / 'cold-route.json'; manifest.write_text(json.dumps(data))
    report = mod.migrate(db.bind.url.database, manifest=manifest,
                         apply=True, backup=tmp_path / 'before-cold.db')
    assert report['matched_routes'] == 0 and report['inserted'] == 1
    assert db.query(KimiRouteGuidanceCache).filter_by(cache_key=key).first() is None
    assert active_disputed_fields(db, key) == ['source_audit']
    # Keep unrelated evidence/grading gates permissive: the persistent issue
    # alone must block even a generated answer that would otherwise be served.
    monkeypatch.setattr(kp, 'is_available', lambda: True)
    monkeypatch.setattr(kp, 'hold_enabled', lambda: False)
    monkeypatch.setattr(kp, '_official_portals', lambda: {})
    monkeypatch.setattr(vo, 'find', lambda route: None)
    monkeypatch.setattr(tstation, 'records_for_route', lambda *a, **kw: [{'confidence_level': 'Medium'}])
    calls = []
    def provider(system, user):
        calls.append(user)
        return {'disposition': 'VISA_EXEMPT', 'requirement_detail': 'unconditional_visa_free',
                'visa_category': 'No visa needed', 'permitted_stay': '30 days',
                'permitted_stay_days': 30, 'government_fee': {'amount': 0, 'currency': None},
                'required_documents': ['Passport'], 'passport_validity': 'Valid for intended stay',
                'application_channel': 'not_required', 'visa_products': [],
                'processing_time': 'Not applicable (no visa)', 'confidence': 'high',
                'source_url': 'https://web.mofa.gov.vn/visa/'}
    kp.set_provider(provider)
    headers = {'Authorization': 'Bearer dev-token', 'X-Org-Id': 'source-hold', 'X-User-Id': 'reader'}
    payload = {'nationality': 'IDN', 'destination': 'VNM', 'travel_purpose': purpose}
    try:
        first = client.post('/database/lookup', headers=headers, json=payload)
        assert first.status_code == 200, first.text
        assert first.json()['held'] and first.json()['guidance'] is None
        assert first.json()['cache_key'] == key and calls
        count = len(calls)
        row = db.query(KimiRouteGuidanceCache).filter_by(cache_key=key).one()
        assert row.guidance['permitted_stay_days'] == 30  # stored history remains intact
        contextual = client.post('/database/lookup', headers=headers,
            json=dict(payload, arrival_date='2027-03-01', residence='Singapore'))
        assert contextual.status_code == 200, contextual.text
        assert contextual.json()['cache_key'] == key and contextual.json()['cached']
        assert contextual.json()['held'] and contextual.json()['guidance'] is None
        assert len(calls) == count
        assert active_disputed_fields(db, key) == ['source_audit']
        assert active_disputed_fields(db, kp.cache_key(dict(route, travel_document_type='diplomatic_passport'))) == []
        # Prove the only remaining blocker was the explicit unresolved issue.
        db.query(DatabaseIssueReport).filter_by(cache_key=key).update({'status': 'corrected'})
        db.commit()
        released = client.post('/database/lookup', headers=headers, json=payload)
        assert released.status_code == 200, released.text
        assert not released.json()['held'] and released.json()['guidance'] is not None
    finally:
        kp.set_provider(None)
        db.query(DatabaseIssueReport).filter_by(cache_key=key).delete()
        db.query(KimiRouteGuidanceCache).filter_by(cache_key=key).delete()
        db.query(DatabaseChangeLog).filter_by(cache_key=key).delete()
        db.commit()
