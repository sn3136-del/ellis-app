"""Reviewed imports are evidence-scoped, non-releasing and all-or-nothing."""
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.visa_snapshot.models import KimiRouteGuidanceCache, DatabaseIssueReport, DatabaseChangeLog

spec = importlib.util.spec_from_file_location('reviewed_materializer', Path(__file__).parents[1] / 'scripts/materialize_reviewed_routes.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
NOW = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
URL = 'https://www.mofa.go.jp/fixture-review'
TABLES = ('kimi_route_guidance_cache', 'database_issue_reports', 'database_change_log')


@pytest.fixture(autouse=True)
def isolated_overrides(tmp_path, monkeypatch):
    seed = tmp_path / 'overrides.json'
    seed.write_text('[]')
    monkeypatch.setattr(mod.overrides, 'OVERRIDES', seed)
    monkeypatch.setenv('ELLIS_OPERATOR_OVERRIDES', str(tmp_path / 'operator.json'))
    mod.overrides.reload()
    yield
    mod.overrides.reload()


def fixture_manifest(*nationalities):
    """Synthetic government-page text exercises the importer, not visa policy."""
    nationalities = nationalities or ('CAN',)
    names = {'CAN': 'Canadian', 'AUS': 'Australian', 'USA': 'United States'}
    manifest = {'schema_version': 1, 'id': 'synthetic-fixture', 'reviewed_at': '2026-09-09', 'sources': [], 'routes': []}
    for nat in nationalities:
        quote = f'{names[nat]} ordinary passport holders may enter Japan for tourism without a visa for a stay of 30 days.'
        source_id = nat.lower()
        manifest['sources'].append({'id': source_id, 'url': URL, 'checked_at': '2026-09-09', 'text': quote, 'reading_method': 'fetched_text'})
        guidance = {'disposition': 'VISA_EXEMPT', 'requirement_detail': 'unconditional_visa_free',
                    'permitted_stay_days': 30, 'source_url': URL, 'application_channel': 'not_required',
                    'government_fee': None, 'visa_products': []}
        proof = {'verifier': 'ai', 'verified_at': '2026-09-09', 'source_id': source_id,
                 'source_url': URL, 'quote': quote, 'note': 'Synthetic fixture review for the ordinary tourism baseline.'}
        manifest['routes'].append({'route': {'nationality': nat, 'destination': 'JPN',
            'travel_purpose': 'tourism', 'travel_document_type': 'ordinary_passport'},
            'guidance': guidance, 'field_provenance': {f: deepcopy(proof) for f, v in guidance.items() if v not in mod.UNKNOWN}})
    return manifest


def write_manifest(tmp_path, data):
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(data))
    return path


def route_key(nat='CAN'):
    return mod.kp.cache_key({'passport_nationality': nat, 'destination_country': 'JPN',
                             'travel_purpose': 'tourism', 'travel_document_type': 'ordinary_passport'})


@pytest.fixture
def cache_database(tmp_path):
    path = tmp_path / 'cache.db'
    engine = create_engine('sqlite:///' + str(path))
    for model in (KimiRouteGuidanceCache, DatabaseIssueReport, DatabaseChangeLog):
        model.__table__.create(engine)
    with Session(engine) as db:
        route = {'passport_nationality': 'AUS', 'destination_country': 'JPN', 'travel_purpose': 'tourism'}
        db.add(KimiRouteGuidanceCache(cache_key=route_key('AUS'), route=route,
                                     guidance={'preserved': 'operator facts'}, verification={'operator_released': 'kept'}))
        db.add(DatabaseIssueReport(cache_key=route_key('AUS'), route=route, status='open', note='reader history', proposal={'proof': 'retained'}))
        db.add(DatabaseChangeLog(cache_key=route_key('AUS'), route=route, changes={'existing': 'history'}))
        db.commit()
    yield path, engine
    engine.dispose()


def rows(path, table):
    with sqlite3.connect(path) as db:
        return db.execute('select * from ' + table + ' order by id').fetchall()


def snapshot(path):
    return {table: rows(path, table) for table in TABLES}


def test_dry_run_exact_import_backup_due_check_and_idempotency(cache_database, tmp_path):
    path, engine = cache_database
    data = fixture_manifest('CAN', 'AUS')
    manifest = write_manifest(tmp_path, data)
    before = snapshot(path)
    dry = mod.materialize(path, manifest=manifest, now=NOW)
    assert dry['skipped_invalid'] == 0, dry
    assert (dry['would_insert'], dry['existing'], dry['applied']) == (1, 1, False)
    assert snapshot(path) == before
    with pytest.raises(ValueError, match='backup'):
        mod.materialize(path, manifest=manifest, apply=True, now=NOW)
    backup = tmp_path / 'before.db'
    done = mod.materialize(path, manifest=manifest, apply=True, backup=backup, now=NOW)
    assert (done['inserted'], done['existing']) == (1, 1)
    assert snapshot(backup) == before
    assert rows(path, 'database_issue_reports') == before['database_issue_reports']
    with Session(engine) as db:
        added = db.query(KimiRouteGuidanceCache).filter_by(cache_key=route_key()).one()
        assert added.guidance == data['routes'][0]['guidance']
        assert 'confidence' not in added.guidance and added.guidance['government_fee'] is None
        assert added.fresh_until == NOW.replace(tzinfo=None)
        assert added.generated_at == added.fresh_until
        assert set(added.verification) == {'source_review'}
        review = added.verification['source_review']
        assert review['verifier'] == 'ai' and review['requires_initial_source_check'] is True
        assert review['sources'][0]['text'] == data['sources'][0]['text']
        log = db.query(DatabaseChangeLog).filter_by(cache_key=route_key()).one()
        assert log.action == 'add' and log.origin == 'reviewed-source-import'
        assert log.changes['disposition']['to'] == 'VISA_EXEMPT'
        old = db.query(KimiRouteGuidanceCache).filter_by(cache_key=route_key('AUS')).one()
        assert old.guidance == {'preserved': 'operator facts'} and old.verification == {'operator_released': 'kept'}
    after = snapshot(path)
    again = mod.materialize(path, manifest=manifest, apply=True, backup=tmp_path / 'second.db', now=NOW)
    assert (again['inserted'], again['existing']) == (0, 2)
    assert snapshot(path) == after
    with pytest.raises(ValueError, match='new, separate'):
        mod.materialize(path, manifest=manifest, apply=True, backup=backup, now=NOW)


@pytest.mark.parametrize('change,reason', [
    ('forged_quote', 'absent'), ('wrong_nationality', 'exact route'),
    ('wrong_document', 'exact route'), ('wrong_purpose', 'exact route'),
    ('human_stamp', 'AI field'), ('future_stamp', 'future review'),
    ('unsupported_number', 'numbers absent'), ('high_confidence', 'High confidence'),
    ('placeholder', 'placeholder'), ('contradiction', 'contradict'),
])
def test_one_bad_route_aborts_entire_batch_before_backup(cache_database, tmp_path, change, reason):
    path, _ = cache_database
    data = fixture_manifest('CAN', 'USA')
    entry = data['routes'][1]
    if change == 'forged_quote':
        entry['field_provenance']['disposition']['quote'] = 'Invented exemption proof.'
    elif change == 'wrong_nationality':
        entry['route']['nationality'] = 'GBR'
    elif change == 'wrong_document':
        entry['route']['travel_document_type'] = 'diplomatic_passport'
    elif change == 'wrong_purpose':
        entry['route']['travel_purpose'] = 'work'
    elif change == 'human_stamp':
        entry['field_provenance']['disposition']['verifier'] = 'human'
    elif change == 'future_stamp':
        entry['field_provenance']['disposition']['verified_at'] = '2027-01-01'
    elif change == 'unsupported_number':
        entry['guidance']['permitted_stay_days'] = 365
    elif change == 'high_confidence':
        entry['guidance']['confidence'] = 'high'
    elif change == 'placeholder':
        entry['guidance'].pop('permitted_stay_days')
    elif change == 'contradiction':
        entry['guidance']['requirement_detail'] = 'evisa'
    manifest = write_manifest(tmp_path, data)
    before = snapshot(path)
    dry = mod.materialize(path, manifest=manifest, now=NOW)
    assert dry['would_insert'] == 1 and dry['skipped_invalid'] == 1, dry
    assert reason in dry['invalid'][0]['reason'], dry
    backup = tmp_path / 'must-not-exist.db'
    with pytest.raises(mod.MaterializationError):
        mod.materialize(path, manifest=manifest, apply=True, backup=backup, now=NOW)
    assert not backup.exists() and snapshot(path) == before


def test_country_table_scope_cannot_bleed_into_neighbouring_policy(cache_database, tmp_path):
    path, _ = cache_database
    data = fixture_manifest()
    heading = 'Nationals from the following countries are allowed to enter Japan without a visa for an initial stay of 30 days:'
    table = heading + '\nAustralia\nCanada\nUnited States of America'
    data['sources'][0]['text'] = table
    entry = data['routes'][0]
    for proof in entry['field_provenance'].values():
        proof['quote'] = heading
    proof = entry['field_provenance']['disposition']
    proof['source_table'] = {'heading_quote': heading, 'table_quote': table, 'nationality_quote': 'Canada'}
    assert mod.materialize(path, manifest=write_manifest(tmp_path, data), now=NOW)['skipped_invalid'] == 0
    bad_table = heading + '\nAustralia\nVisa-required countries:\nCanada'
    data['sources'][0]['text'] = bad_table
    proof['source_table']['table_quote'] = bad_table
    failed = mod.materialize(path, manifest=write_manifest(tmp_path, data), now=NOW)
    assert failed['skipped_invalid'] == 1 and 'exact route' in failed['invalid'][0]['reason']


def test_additional_literal_quotes_and_current_policy_dates_are_recorded(cache_database, tmp_path):
    path, engine = cache_database
    data = fixture_manifest()
    extra = 'The policy is effective from 1 July 2026 until 30 June 2027. Arrival registration is required within 72 hours before arrival.'
    data['sources'][0]['text'] += '\n' + extra
    entry = data['routes'][0]
    proof = deepcopy(entry['field_provenance']['disposition'])
    proof['additional_quotes'] = [extra]
    entry['policy_valid_from'] = '2026-07-01'
    entry['policy_valid_through'] = '2027-06-30'
    entry['guidance']['arrival_card'] = {'required': True, 'name': 'Arrival registration', 'submission_window': 'Within 72 hours before arrival'}
    for field in ('policy_valid_from', 'policy_valid_through', 'arrival_card'):
        entry['field_provenance'][field] = deepcopy(proof)
    manifest = write_manifest(tmp_path, data)
    done = mod.materialize(path, manifest=manifest, apply=True, backup=tmp_path / 'before.db', now=NOW)
    assert done['inserted'] == 1
    with Session(engine) as db:
        review = db.query(KimiRouteGuidanceCache).filter_by(cache_key=route_key()).one().verification['source_review']
        assert review['policy_valid_from'] == '2026-07-01' and review['policy_valid_through'] == '2027-06-30'
    entry['field_provenance']['arrival_card']['additional_quotes'].append('Registration costs 999 dollars.')
    bad = mod.materialize(path, manifest=write_manifest(tmp_path, data), now=NOW)
    assert bad['skipped_invalid'] == 1 and 'additional quote' in bad['invalid'][0]['reason']
    entry['field_provenance']['arrival_card']['additional_quotes'].pop()
    entry['policy_valid_through'] = '2026-06-30'
    expired = mod.materialize(path, manifest=write_manifest(tmp_path, data), now=NOW)
    assert expired['skipped_invalid'] == 1 and 'not effective' in expired['invalid'][0]['reason']


def test_duplicate_and_legacy_orphan_require_explicit_review(cache_database, tmp_path):
    path, engine = cache_database
    data = fixture_manifest()
    data['routes'].append(deepcopy(data['routes'][0]))
    duplicate = mod.materialize(path, manifest=write_manifest(tmp_path, data), now=NOW)
    assert duplicate['skipped_invalid'] == 1 and 'duplicate canonical' in duplicate['invalid'][0]['reason']
    data['routes'].pop()
    with Session(engine) as db:
        db.add(KimiRouteGuidanceCache(cache_key=route_key().replace('|unknown|', '|2026-10|'), route={}, guidance={'old': True}))
        db.commit()
    before = snapshot(path)
    manifest = write_manifest(tmp_path, data)
    dry = mod.materialize(path, manifest=manifest, now=NOW)
    assert dry['plan'][0]['action'] == 'invalid_legacy_orphan'
    with pytest.raises(mod.MaterializationError):
        mod.materialize(path, manifest=manifest, apply=True, backup=tmp_path / 'no.db', now=NOW)
    assert snapshot(path) == before


def test_concurrent_insert_is_preserved_after_review_before_write(cache_database, tmp_path, monkeypatch):
    path, engine = cache_database
    manifest = write_manifest(tmp_path, fixture_manifest())
    original = mod._manifest
    def race(*args):
        result = original(*args)
        with Session(engine) as db:
            db.add(KimiRouteGuidanceCache(cache_key=route_key(), route={}, guidance={'concurrent': 'newer answer'}))
            db.commit()
        return result
    monkeypatch.setattr(mod, '_manifest', race)
    result = mod.materialize(path, manifest=manifest, apply=True, backup=tmp_path / 'before.db', now=NOW)
    assert result['inserted'] == 0 and result['existing'] == 1
    with Session(engine) as db:
        assert db.query(KimiRouteGuidanceCache).filter_by(cache_key=route_key()).one().guidance == {'concurrent': 'newer answer'}
        assert db.query(DatabaseChangeLog).filter_by(cache_key=route_key()).count() == 0


def test_history_failure_rolls_back_all_inserted_routes(cache_database, tmp_path):
    path, _ = cache_database
    manifest = write_manifest(tmp_path, fixture_manifest('CAN', 'USA'))
    with sqlite3.connect(path) as db:
        db.execute("CREATE TRIGGER stop_import_history BEFORE INSERT ON database_change_log BEGIN SELECT RAISE(ABORT, 'history write failed'); END")
    before = snapshot(path)
    backup = tmp_path / 'before.db'
    with pytest.raises(sqlite3.IntegrityError, match='history write failed'):
        mod.materialize(path, manifest=manifest, apply=True, backup=backup, now=NOW)
    assert snapshot(path) == before and snapshot(backup) == before


def test_conflicting_verified_overlay_does_not_silently_change_approved_guidance(cache_database, tmp_path, monkeypatch):
    path, _ = cache_database
    manifest = write_manifest(tmp_path, fixture_manifest())
    def conflicting_overlay(guidance, route):
        return {**guidance, 'permitted_stay_days': 90}, {'fields': ['permitted_stay_days']}
    monkeypatch.setattr(mod.overrides, 'apply', conflicting_overlay)
    result = mod.materialize(path, manifest=manifest, now=NOW)
    assert result['skipped_invalid'] == 1 and 'changes a reviewed field' in result['invalid'][0]['reason']


def test_unofficial_source_is_rejected_without_opening_database(tmp_path):
    data = fixture_manifest()
    data['sources'][0]['url'] = 'https://visa-example.com/not-government'
    with pytest.raises(ValueError, match='unofficial'):
        mod.materialize(tmp_path / 'does-not-exist.db', manifest=write_manifest(tmp_path, data), now=NOW)
