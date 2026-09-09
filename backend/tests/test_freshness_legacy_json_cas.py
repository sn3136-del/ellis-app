"""A formatting-only legacy JSON representation is not a concurrent edit."""
from copy import deepcopy
import json

import pytest
from sqlalchemy import text, update

from app.visa_snapshot import freshness
from app.visa_snapshot.models import KimiRouteGuidanceCache
from .test_freshness_metadata_race import db, cached


@pytest.mark.parametrize('column', ['guidance', 'verification', 'route'])
@pytest.mark.parametrize('encoding', ['compact', 'pretty', 'literal_unicode'])
def test_unchanged_legacy_json_representation_can_record_source_attempt(db, cached, column, encoding):
    value = deepcopy(getattr(cached, column))
    if encoding == 'literal_unicode':
        value['fixture_note'] = 'Visa · 日本'
        stored = json.dumps(value, ensure_ascii=False)
    elif encoding == 'pretty':
        stored = json.dumps(value, indent=2)
    else:
        stored = json.dumps(value, separators=(',', ':'))
    db.execute(text(f'UPDATE kimi_route_guidance_cache SET {column}=:stored WHERE id=:id'),
               {'stored': stored, 'id': cached.id})
    db.commit()
    db.refresh(cached)
    guidance, route = deepcopy(cached.guidance), deepcopy(cached.route)
    accepted = freshness._commit_recheck(db, cached,
        {'outcome':'page_not_relevant','at':'2026-09-09T20:25:00Z','source_reads':1},
        expected_guidance=guidance, expected_route=route)
    assert accepted, 'An unchanged parsed row must not be rejected as a concurrent edit'
    db.refresh(cached)
    assert cached.guidance == guidance and cached.route == route
    assert cached.verification['grounded_check']['outcome'] == 'page_not_relevant'
    assert cached.verification['grounded_check']['source_reads'] == 1


@pytest.mark.parametrize('stored, expected', [
    ('{ "b":2, "a":1 }', {'a': 1, 'b': 2}),
    ('{"outer":{"z":[1,true,null],"a":"日本"}}', {'outer': {'a': '日本', 'z': [1, True, None]}}),
    ('{"cl\\u00e9":"visa","a\\u002eb":1}', {'a.b': 1, 'clé': 'visa'}),
    ('{"quote\\\"key": "line\\nnext"}', {'quote"key': 'line\nnext'}),
    ('{"value":1.000e0}', {'value': 1.0}),
    ('{"value":0.10000}', {'value': 0.1}),
    ('null', None),
])
def test_semantic_guard_accepts_only_representation_changes_and_null_forms(db, cached, stored, expected):
    db.execute(text('UPDATE kimi_route_guidance_cache SET verification=:stored WHERE id=:id'),
               {'stored': stored, 'id': cached.id})
    db.commit()
    result = db.execute(update(KimiRouteGuidanceCache).where(
        KimiRouteGuidanceCache.id == cached.id,
        freshness.json_unchanged(db, KimiRouteGuidanceCache.verification, expected),
    ).values(verification={'guard_accepted': True}).execution_options(synchronize_session=False))
    assert result.rowcount == 1
    db.commit()
    db.refresh(cached)
    assert cached.verification == {'guard_accepted': True}


@pytest.mark.parametrize('stored, expected', [
    ('{"value":true}', {'value': 1}),
    ('{"value":1}', {'value': True}),
    ('{"value":1.0}', {'value': 1}),
    ('{"value":1}', {'value': 1.0}),
    ('{"value":9007199254740993.0}', {'value': 9007199254740992.0}),
    ('{"value":0.10000000000000001}', {'value': 0.1}),
    ('{"value":1e-400}', {'value': 0.0}),
    ('{"value":null}', {}),
    ('{"value":"1"}', {'value': 1}),
    ('{"array":[2,1]}', {'array': [1, 2]}),
    ('{"value":"null"}', {'value': None}),
    ('"null"', None),
    ('null', {}),
    ('{"new_operator_edit":true}', {'new_operator_edit': False}),
    ('{"x":1,"x":2}', {'x': 2}),
    ('{"x":1,"\\u0078":2}', {'x': 2}),
    ('{"nested":{"x":1,"x":2}}', {'nested': {'x': 2}}),
    ('{"value":NaN}', {'value': float('nan')}),
    ('{"value":Infinity}', {'value': float('inf')}),
    ('{"value":-Infinity}', {'value': float('-inf')}),
    ('{"value":1e400}', {'value': float('inf')}),
    ('{"value":', {'value': 1}),
])
def test_semantic_guard_rejects_changed_types_content_or_ambiguous_json(db, cached, stored, expected):
    row_id = cached.id
    db.execute(text('UPDATE kimi_route_guidance_cache SET verification=:stored WHERE id=:id'),
               {'stored': stored, 'id': row_id})
    db.commit()
    result = db.execute(update(KimiRouteGuidanceCache).where(
        KimiRouteGuidanceCache.id == row_id,
        freshness.json_unchanged(db, KimiRouteGuidanceCache.verification, expected),
    ).values(verification={'must_not_write': True}).execution_options(synchronize_session=False))
    assert result.rowcount == 0
    db.rollback()
    actual = db.execute(text('SELECT verification FROM kimi_route_guidance_cache WHERE id=:id'),
                        {'id': row_id}).scalar_one()
    assert actual == stored
    # Restore valid data so the ordinary fixture can dispose its ORM row.
    db.execute(text('UPDATE kimi_route_guidance_cache SET verification=:stored WHERE id=:id'),
               {'stored': '{}', 'id': row_id})
    db.commit()
    db.refresh(cached)


@pytest.mark.parametrize('stored, expected, accepted', [
    (None, None, True), ('null', None, True),
    (None, {}, False), ('null', {}, False), ('"null"', None, False),
])
def test_legacy_nullable_json_column_distinguishes_sql_null_json_null_and_string(db, stored, expected, accepted):
    from sqlalchemy import Table, Column, MetaData, Integer, JSON
    # Modern cache schemas are NOT NULL; older stores may contain SQL NULL.
    table = Table('legacy_nullable_guard', MetaData(),
                  Column('id', Integer, primary_key=True), Column('value', JSON, nullable=True))
    table.create(db.get_bind())
    db.execute(text('INSERT INTO legacy_nullable_guard (id,value) VALUES (1,:stored)'), {'stored': stored})
    db.commit()
    result = db.execute(update(table).where(table.c.id == 1,
        freshness.json_unchanged(db, table.c.value, expected)).values(value={'accepted': True}))
    assert result.rowcount == int(accepted)
    db.rollback()


def test_sqlite_guard_registers_on_each_direct_engine_connection(db, cached):
    from sqlalchemy.orm import Session
    first_connection = db.connection().connection.driver_connection
    assert db.execute(text('SELECT 1')).scalar_one() == 1
    with Session(db.get_bind()) as another:
        assert another.connection().connection.driver_connection is not first_connection
        for session in (db, another):
            condition = freshness.json_unchanged(session, KimiRouteGuidanceCache.verification, {'original': True})
            assert session.query(KimiRouteGuidanceCache.id).filter(condition).scalar() == cached.id
