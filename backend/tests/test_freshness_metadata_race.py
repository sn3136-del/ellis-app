"""Separate sessions reproduce a worker overwriting newer API metadata."""
from copy import deepcopy
from dataclasses import replace

import pytest

from sqlalchemy import create_engine, update
from sqlalchemy.orm import sessionmaker
from app.visa_snapshot import freshness, verified_overrides as vo, kimi_primary
from app.visa_snapshot.fetching import FetchResult
from app.visa_snapshot.models import KimiRouteGuidanceCache, DatabaseChangeLog, DatabaseIssueReport


ROUTE = {'passport_nationality': 'CAN', 'destination_country': 'JPN', 'travel_purpose': 'tourism'}
URL = 'https://www.mofa.go.jp/visa'
PAGE = FetchResult(requested_url=URL, ok=True, final_url=URL, final_hostname='www.mofa.go.jp',
    http_status=200, content_text='Canadian citizens must obtain a visa for tourism in Japan.',
    content_hash='hash', retrieved_at='2026-09-09T12:00:00Z')


@pytest.fixture
def db(tmp_path, monkeypatch):
    # Both real sessions use this test's own file database, independent of
    # route rows left by other suites in the application fixture database.
    import sys
    engine = create_engine(f"sqlite:///{tmp_path / 'race.sqlite'}")
    for model in (KimiRouteGuidanceCache, DatabaseChangeLog, DatabaseIssueReport):
        model.__table__.create(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(sys.modules[__name__], 'SessionLocal', factory, raising=False)
    with factory() as session:
        yield session
    engine.dispose()


@pytest.fixture
def cached(db, tmp_path, monkeypatch):
    seed = tmp_path / 'seed.json'
    seed.write_text('[]')
    monkeypatch.setattr(vo, 'OVERRIDES', seed)
    monkeypatch.setenv('ELLIS_OPERATOR_OVERRIDES', str(tmp_path / 'operator.json'))
    vo.reload()
    row = KimiRouteGuidanceCache(cache_key=kimi_primary.cache_key(ROUTE), route=ROUTE,
        guidance={'disposition': 'VISA_REQUIRED', 'source_url': URL}, verification={'original': True})
    db.add(row); db.commit()
    yield row
    db.delete(row); db.commit()
    vo.reload()


def concurrent_metadata(row_id):
    with SessionLocal() as other:
        latest = other.get(KimiRouteGuidanceCache, row_id)
        latest.verification = {**latest.verification,
            'operator_released': {'by': 'operator', 'at': '2026-09-09T12:00:01Z'},
            'detail_history': [{'status': 'completed'}]}
        other.commit()
        return deepcopy(latest.verification)


@pytest.mark.parametrize('outcome', ['fetch_failed', 'page_not_relevant', 'provider_error', 'no_official_source'])
def test_failure_read_preserves_metadata_written_by_other_session(db, cached, monkeypatch, outcome):
    written = {}
    def update():
        written.update(concurrent_metadata(cached.id))
    def fetch(*_a, **_k):
        update()
        if outcome == 'fetch_failed':
            return replace(PAGE, ok=False, content_text='')
        if outcome == 'page_not_relevant':
            return replace(PAGE, content_text='Government homepage. News and public services.')
        return PAGE
    monkeypatch.setattr(freshness, 'fetch', fetch)
    if outcome == 'no_official_source':
        def sources(*_a, **_k):
            update()
            return []
        monkeypatch.setattr(freshness, 'candidate_sources', sources)
    monkeypatch.setattr(freshness, '_call', lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError('provider outage')))
    assert freshness.recheck_row(db, cached)['outcome'] == outcome
    db.refresh(cached)
    assert cached.verification['operator_released'] == written['operator_released']
    assert cached.verification['detail_history'] == written['detail_history']
    assert cached.verification['grounded_check']['outcome'] == outcome


def test_success_validation_preserves_metadata_written_after_initial_refresh(db, cached, monkeypatch):
    written = {}
    monkeypatch.setattr(freshness, 'fetch', lambda *_a, **_k: PAGE)
    monkeypatch.setattr(freshness, '_call', lambda *_a, **_k: {
        'consistent': True, 'page_relevant': True, 'page_is_nationality_specific': True,
        'corrected_fields': {}, 'evidence': {}})
    calls = 0
    original = freshness._supports_route
    def support(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:  # final field evidence pass, after successful-path refresh
            written.update(concurrent_metadata(cached.id))
        return original(*args, **kwargs)
    monkeypatch.setattr(freshness, '_supports_route', support)
    assert freshness.recheck_row(db, cached)['outcome'] == 'checked'
    assert written
    db.refresh(cached)
    assert cached.verification['operator_released'] == written['operator_released']
    assert cached.verification['detail_history'] == written['detail_history']


@pytest.mark.parametrize('change', ['metadata', 'guidance'])
def test_write_between_final_read_and_update_defers_without_stale_flush(db, cached, monkeypatch, change):
    from sqlalchemy.sql.dml import Update
    original = deepcopy(cached.guidance)
    cached.guidance = {**original, 'permitted_stay': '15 days'}  # pending page correction
    issue = DatabaseIssueReport(org_id='platform', cache_key=cached.cache_key, route=ROUTE,
        field='processing_time', reported_by='freshness_monitor', note='pending source dispute')
    db.add(issue)
    execute = db.execute
    competing = {}
    def interleave(statement, *args, **kwargs):
        if isinstance(statement, Update) and statement.table.name == KimiRouteGuidanceCache.__tablename__:
            competing.update(concurrent_metadata(cached.id))
            if change == 'guidance':
                with SessionLocal() as other:
                    latest = other.get(KimiRouteGuidanceCache, cached.id)
                    latest.guidance = {'disposition': 'VISA_EXEMPT', 'source_url': URL}
                    other.commit()
        return execute(statement, *args, **kwargs)
    monkeypatch.setattr(db, 'execute', interleave)
    from datetime import datetime, timezone
    result = freshness._commit_recheck(db, cached, {'outcome': 'checked', 'renewed': True,
        'changed_fields': ['permitted_stay'], 'source_url': URL}, expected_guidance=original,
        expected_route=ROUTE, fresh_until=datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert result is False
    db.commit()  # must not flush an old ORM assignment after the failed CAS
    db.refresh(cached)
    expected = original if change == 'metadata' else {'disposition': 'VISA_EXEMPT', 'source_url': URL}
    assert cached.guidance == expected and cached.fresh_until is None
    assert cached.verification == competing
    assert 'grounded_check' not in cached.verification
    assert db.query(DatabaseIssueReport).count() == db.query(DatabaseChangeLog).count() == 0


def test_successful_guarded_correction_and_its_history_commit_together(db, cached):
    original = deepcopy(cached.guidance)
    cached.guidance = {**original, 'permitted_stay': '15 days'}
    assert freshness._commit_recheck(db, cached, {'outcome': 'checked', 'renewed': False,
        'changed_fields': ['permitted_stay'], 'source_url': URL}, expected_guidance=original,
        expected_route=ROUTE)
    with SessionLocal() as other:
        row = other.get(KimiRouteGuidanceCache, cached.id)
        log = other.query(DatabaseChangeLog).one()
        assert row.guidance['permitted_stay'] == '15 days'
        assert log.changes['permitted_stay'] == {'from': None, 'to': '15 days'}


def test_postgresql_guard_compiles_jsonb_equality():
    from sqlalchemy.dialects import postgresql
    from types import SimpleNamespace
    fake = SimpleNamespace(get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name='postgresql')))
    condition = freshness.json_unchanged(fake, KimiRouteGuidanceCache.verification, {'read': True})
    statement = update(KimiRouteGuidanceCache).where(condition).values(verification={'new': True})
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert 'CAST(kimi_route_guidance_cache.verification AS JSONB)' in compiled
    assert '::JSONB' in compiled
