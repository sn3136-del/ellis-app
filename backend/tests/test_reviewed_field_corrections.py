from copy import deepcopy
from pathlib import Path
import json
import pytest
from scripts import convert_reviewed_field_corrections as c
from scripts.prepare_reviewed_product_patch import PatchRejected
from app.visa_snapshot import tstation as t

D = Path(__file__).resolve().parents[2] / 'data/database_seed'
M = json.loads((D / 'reviewed_field_corrections_manifest_20260910.json').read_text())
O = json.loads((D / 'reviewed_field_corrections_overlay_20260910.json').read_text())
L = [dict(deepcopy(r['baseline']), cache_key=r['cache_key']) for r in M['routes']]
SPEC = M['specification']
CHANGES = {r['cache_key']: r['changes'] for r in SPEC['routes']}


@pytest.fixture(scope='module')
def result():
    return c.convert(M, L)


def test_full_prepared_rebuild_and_scope(result):
    overlay, report = result
    assert overlay == O and c.build_manifest(SPEC, L) == M
    assert len(report['routes']) == 8 and len(overlay['entries']) == 8
    fields = sorted(ch['field'] for r in SPEC['routes'] for ch in r['changes'])
    assert fields == ['arrival_card'] + ['health_requirements'] * 6 + ['passport_validity']
    assert not any(report[k] for k in ('raw_writes', 'operator_writes', 'issue_changes', 'renew_fresh_until', 'confidence_changed', 'new_release'))


@pytest.mark.parametrize('index', range(8))
@pytest.mark.parametrize('field', c.BASELINE_KEYS)
def test_all_six_layers_for_every_context_reject_drift(index, field):
    layers = deepcopy(L)
    value = layers[index][field]
    if isinstance(value, dict):
        value['unreviewed'] = True
    else:
        value.append({'unreviewed': True})
    with pytest.raises(PatchRejected):
        c.validate(SPEC, layers)


@pytest.mark.parametrize('mode', ['missing_layer', 'duplicate_layer', 'extra_layer', 'source_text', 'missing_source', 'extra_source',
                                  'extra_field', 'unknown_field', 'unchanged_value', 'old_mismatch', 'quote_not_on_page', 'foreign_page',
                                  'health_not_empty', 'passport_stricter', 'arrival_optional', 'future_date', 'missing_scope', 'operator_entry'])
def test_tampering_rejects(mode):
    manifest = deepcopy(M); layers = deepcopy(L); spec = manifest['specification']
    by_field = {ch['field']: (r, ch) for r in spec['routes'] for ch in r['changes']}
    if mode == 'missing_layer': layers.pop()
    elif mode == 'duplicate_layer': layers[-1] = deepcopy(layers[0])
    elif mode == 'extra_layer': layers.append(deepcopy(layers[0]))
    elif mode == 'source_text': spec['sources'][0]['text'] += ' false'
    elif mode == 'missing_source': spec['sources'] = []
    elif mode == 'extra_source': spec['sources'].append(deepcopy(spec['sources'][0]))
    elif mode == 'extra_field': spec['routes'][0]['changes'][0]['confidence'] = 'high'
    elif mode == 'unknown_field': spec['routes'][0]['changes'][0]['field'] = 'government_fee'
    elif mode == 'unchanged_value':
        r, ch = by_field['passport_validity']; ch['new'] = ch['old_merged']
    elif mode == 'old_mismatch': spec['routes'][0]['changes'][0]['old_merged'] = 'something else'
    elif mode == 'quote_not_on_page': spec['routes'][0]['changes'][0]['proof']['evidence'][0]['quote'] = 'This sentence is not on the captured page at all.'
    elif mode == 'foreign_page':
        r, ch = by_field['passport_validity']
        ch['proof']['evidence'][0]['source_url'] = 'https://www.forth.go.jp/moreinfo/topics/yellow_fever_certificate.html'
        ch['proof']['evidence'][0]['source_id'] = 'jp_forth_yellow_fever'
    elif mode == 'health_not_empty':
        r, ch = by_field['health_requirements']; ch['new'] = [{'name': 'Something', 'applicability': 'conditional'}]
    elif mode == 'passport_stricter':
        r, ch = by_field['passport_validity']; ch['new'] = 'More than 6 months from the date of entry into Malaysia'
    elif mode == 'arrival_optional':
        r, ch = by_field['arrival_card']; ch['new'] = dict(ch['new'], required=False)
    elif mode == 'future_date': spec['routes'][0]['changes'][0]['proof']['verified_at'] = '2999-01-01'
    elif mode == 'missing_scope': spec['routes'][0]['changes'][0]['proof']['scope_note'] = ''
    elif mode == 'operator_entry': layers[0]['operator_entries'] = [{'route': {}, 'fields': {}}]
    with pytest.raises(PatchRejected):
        c.convert(manifest, layers) if mode in ('missing_layer', 'duplicate_layer', 'extra_layer', 'operator_entry') else c.build_manifest(spec, layers)


def test_only_exact_fields_change_and_grades_hold(result):
    old = {l['cache_key']: l for l in L}
    for preview in result[1]['routes']:
        layer = old[preview['cache_key']]
        fields = [ch['field'] for ch in CHANGES[preview['cache_key']]]
        before, after = layer['merged_guidance'], preview['guidance']
        assert c._mask(before, fields) == c._mask(after, fields)
        for ch in CHANGES[preview['cache_key']]:
            assert after[ch['field']] == ch['new'] and before.get(ch['field']) != ch['new']
            proof = preview['source_provenance']['field_provenance'][ch['field']]
            assert proof['source_url'] == ch['proof']['evidence'][0]['source_url']
            assert proof['quote'] == ch['proof']['evidence'][0]['quote']
            assert proof['verification_scope'] == c.SCOPE
        old_rows = t.records_for_route(layer['route'], layer['merged_guidance'], layer['source_provenance'])
        for a, b in zip(old_rows, preview['records'], strict=True):
            assert a['confidence_level'] == b['confidence_level'] and a['visa_type_name'] == b['visa_type_name']
            assert {k for k in set(a) | set(b) if a.get(k) != b.get(k)} <= c.RECORD_COLUMNS_MAY_CHANGE
        assert set(preview['changed_record_columns']) <= c.RECORD_COLUMNS_MAY_CHANGE


def test_japan_health_lists_are_empty_and_malaysia_boundary_is_inclusive(result):
    guidance = {r['cache_key']: r['guidance'] for r in result[1]['routes']}
    for nat in ('IDN', 'IND', 'PHL', 'RUS', 'VNM'):
        assert guidance[f'{nat}|{nat}|JPN|tourism|default|unknown|v6']['health_requirements'] == []
    assert guidance['CAN|CAN|HKG|tourism|default|unknown|v6']['health_requirements'] == []
    assert guidance['HKG|HKG|MYS|tourism|default|unknown|v6']['passport_validity'] == 'At least 6 months from the date of entry into Malaysia'
    card = guidance['HKG|HKG|IDN|tourism|default|unknown|v6']['arrival_card']
    assert card['required'] is True and card['url'].startswith('https://allindonesia.imigrasi.go.id/')


@pytest.mark.parametrize('issue', [False, True])
@pytest.mark.parametrize('pending', [False, True])
def test_actual_cached_readers_preserve_holds_raw_dates_history(result, monkeypatch, tmp_path, issue, pending):
    from datetime import datetime, timezone
    from unittest.mock import patch
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session
    from app.visa_snapshot import verified_overrides as vo, kimi_primary as kp, records_guard
    from app.visa_snapshot.models import KimiRouteGuidanceCache, DatabaseIssueReport, DatabaseChangeLog
    before_table = vo._parse_rows([e for l in L for e in l['seed_entries']], {})
    after_table = vo._parse_rows(result[0]['entries'], deepcopy(before_table))
    engine = create_engine('sqlite:///:memory:')
    for model in (KimiRouteGuidanceCache, DatabaseIssueReport, DatabaseChangeLog):
        model.__table__.create(engine)
    monkeypatch.setattr(kp, '_call', lambda *a, **k: pytest.fail('No model regeneration'))
    monkeypatch.setenv('ELLIS_OPERATOR_OVERRIDES', str(tmp_path / 'none.json'))
    monkeypatch.setattr(vo, '_table', lambda: after_table)
    with Session(engine) as db:
        for l in L:
            db.add(KimiRouteGuidanceCache(cache_key=l['cache_key'], route=deepcopy(l['route']), guidance=deepcopy(l['raw_guidance']),
                                          verification={'detail_pending': pending}, fresh_until=datetime(2099, 1, 1, tzinfo=timezone.utc), status='KIMI_PRIMARY'))
            db.add(DatabaseChangeLog(cache_key=l['cache_key'], action='add', origin='fixture', changes={'unchanged': True}))
            if issue:
                db.add(DatabaseIssueReport(cache_key=l['cache_key'], route=deepcopy(l['route']), field='government_fee', status='open',
                                           reported_by='freshness_monitor', proposal={'fields': {'government_fee': {'record_holds': 'old', 'page_says': 'new'}}}))
        db.commit()
        def snapshot():
            return {m.__name__: [{col.name: deepcopy(getattr(r, col.name)) for col in m.__table__.columns} for r in db.scalars(select(m))]
                    for m in (KimiRouteGuidanceCache, DatabaseIssueReport, DatabaseChangeLog)}
        initial = snapshot()
        for l in L:
            with patch.object(vo, '_table', return_value=before_table):
                before = kp.get_route_guidance(db, l['route']); old = records_guard.apply_records_hold(l['route'], deepcopy(before), db)
            after = kp.get_route_guidance(db, l['route']); public = records_guard.apply_records_hold(l['route'], deepcopy(after), db)
            assert before['cached'] and after['cached'] and bool(old.get('held')) == bool(public.get('held'))
            if issue or pending:
                assert public['held']
            elif not public.get('held'):
                g = public['guidance']
                for ch in CHANGES[l['cache_key']]:
                    assert g[ch['field']] == ch['new']
        assert snapshot() == initial
