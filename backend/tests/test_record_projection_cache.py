from app.visa_snapshot import record_projection_cache as cache
from app.visa_snapshot import freshness, kimi_primary
from app.visa_snapshot.models import KimiRouteGuidanceCache


def test_reuses_only_identical_source_row_and_dispute_inputs(monkeypatch):
    cache._CACHE.clear()
    disputes = []
    monkeypatch.setattr(freshness, 'active_disputed_fields', lambda db, key: list(disputes))
    monkeypatch.setattr(kimi_primary, '_is_stale', lambda row: False)
    row = KimiRouteGuidanceCache(cache_key='USA|USA|NPL|tourism|default|unknown|v6',
        route={'passport_nationality': 'USA', 'destination_country': 'NPL'},
        guidance={'government_fee': {'amount': 30}}, verification={}, status='KIMI_PRIMARY')
    calls = []
    def build(db, row, route):
        calls.append(row.cache_key)
        return [{'fee': row.guidance['government_fee']['amount'], 'disputes': list(disputes)}]
    def read(version='source-v1'):
        return cache.project(None, row, row.route, version, build)
    first = read(); first[0]['disputes'].append('caller mutation')
    assert read() == [{'fee': 30, 'disputes': []}]
    assert len(calls) == 1
    row.guidance = {'government_fee': {'amount': 50}}
    assert read()[0]['fee'] == 50
    disputes.append('government_fee')
    assert read()[0]['disputes'] == ['government_fee']
    read('source-v2')
    assert len(calls) == 4
    row.verification = {'detail_pending': True}
    read('source-v2')
    assert len(calls) == 5
    monkeypatch.setattr(kimi_primary, '_is_stale', lambda row: True)
    read('source-v2')
    assert len(calls) == 6


def test_expiry_and_audit_dependent_route_do_not_reuse(monkeypatch):
    cache._CACHE.clear(); clock = [100.0]
    monkeypatch.setattr(cache.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(freshness, 'active_disputed_fields', lambda db, key: [])
    monkeypatch.setattr(kimi_primary, '_is_stale', lambda row: False)
    row = KimiRouteGuidanceCache(cache_key='USA|USA|NPL|tourism|default|unknown|v6')
    calls = []
    def build(*args):
        calls.append(1); return []
    for _ in range(2): cache.project(None, row, {}, 'v1', build, ttl=30)
    assert len(calls) == 1
    clock[0] = 131
    cache.project(None, row, {}, 'v1', build, ttl=30)
    assert len(calls) == 2
    row.cache_key = 'KOR|KOR|CHN|tourism|default|unknown|v6'
    for _ in range(2): cache.project(None, row, {}, 'v1', build)
    assert len(calls) == 4


def test_changed_override_only_invalidates_its_own_route(monkeypatch):
    from app.visa_snapshot import verified_overrides as vo
    cache._CACHE.clear()
    monkeypatch.setattr(freshness, 'active_disputed_fields', lambda db, key: [])
    monkeypatch.setattr(kimi_primary, '_is_stale', lambda row: False)
    sources = {'NPL': {'fee': 30}, 'DEU': {'fee': 90}}
    monkeypatch.setattr(vo, 'find', lambda route: sources[route['destination_country']])
    rows = [KimiRouteGuidanceCache(cache_key=dest,
        route={'destination_country': dest}) for dest in sources]
    calls = []
    def build(db, row, route):
        calls.append(row.cache_key); return [{'source': dict(sources[row.cache_key])}]
    for row in rows: cache.project(None, row, row.route, 'v1', build)
    sources['NPL'] = {'fee': 50}
    for row in rows: cache.project(None, row, row.route, 'v1', build)
    assert calls == ['NPL', 'DEU', 'NPL']


def test_unmatched_route_rebuilds_on_store_failure_and_recovery(monkeypatch):
    """A failed store changes serving even when find(route) stays None."""
    from datetime import datetime, timezone
    from app.visa_snapshot import verified_overrides as vo
    from app.visa_snapshot.row_projection import records_projection
    cache._CACHE.clear()
    route = {'passport_nationality': 'USA', 'destination_country': 'NPL',
             'travel_document_type': 'ordinary_passport', 'travel_purpose': 'tourism'}
    row = KimiRouteGuidanceCache(cache_key=kimi_primary.cache_key(route), route=route,
        status=kimi_primary.STATUS_PRIMARY, generated_at=datetime.now(timezone.utc),
        guidance={'disposition': 'VISA_EXEMPT', 'requirement_detail': 'unconditional_visa_free',
                  'source_url': 'https://www.immigration.gov.np/', 'permitted_stay': '30 days',
                  'confidence': 'high'}, verification={'operator_released': True})
    table = [vo._VerificationTable({})]
    monkeypatch.setattr(vo, '_table', lambda: table[0])
    monkeypatch.setattr(freshness, 'active_disputed_fields', lambda *a: [])
    def read():
        assert vo.find(route) is None
        return cache.project(None, row, route, cache.context_version(vo), records_projection)
    initial = read()
    assert initial and not any(r['_held'] for r in initial)
    table[0] = vo._VerificationTable({}, ['operator_overrides:unreadable'])
    failed = read()
    actual = records_projection(None, row, route)
    assert failed == actual and all(r['_held'] for r in failed)
    assert any('verification store is unavailable' in problem
               for r in failed for problem in r['_contradictions'])
    table[0] = vo._VerificationTable({})
    assert read() == initial


def test_json_reuse_binds_the_exact_snapshot_and_never_satisfies_a_filter(client, monkeypatch):
    from app import main
    cache_rows = [[{'_cache_key': 'USA|USA|NPL|tourism|default|unknown|v6',
                   'travel_document_country': 'USA', 'destination_country': 'NPL',
                   'travel_document_type': 'ordinary_passport', 'travel_purpose': 'tourism',
                   'visa_type_name': 'Tourist visa', 'visa_requirement': 'Visa on Arrival',
                   'confidence_level': 'Medium', 'visa_fee_amount': 30, 'visa_fee_currency': 'USD'}]]
    monkeypatch.setattr(main, '_all_tstation_rows', lambda db: cache_rows[0])
    monkeypatch.setattr(main, '_RECORDS_JSON_CACHE', {'rows': None, 'body': None})
    prepared = []
    original = main._record_payload
    def count(row):
        prepared.append(row['visa_fee_amount'])
        return original(row)
    monkeypatch.setattr(main, '_record_payload', count)
    headers = {'authorization': 'Bearer admin-token', 'x-org-id': 'cache-test', 'x-user-id': 'reader'}
    first = client.get('/database/records', headers=headers)
    second = client.get('/database/records', headers=headers)
    assert first.json() == second.json() and prepared == [30]
    assert second.headers['cache-control'] == 'private, no-store'
    assert client.get('/database/records?nationality=JPN', headers=headers).json()['records'] == []
    assert client.get('/database/records', headers=headers).json() == first.json()
    # An edit produces a different row-list object; neither key equality nor
    # the old serialized body may hide its updated fee.
    cache_rows[0] = [dict(cache_rows[0][0], visa_fee_amount=50)]
    changed = client.get('/database/records', headers=headers).json()
    assert changed['records'][0]['visa_fee_amount'] == 50 and prepared == [30, 50]
    assert client.get('/database/records', headers=headers).json() == changed
    assert prepared == [30, 50]


def test_context_tracks_external_eligibility_and_portal_files(tmp_path, monkeypatch):
    from app.visa_snapshot import verified_overrides as vo
    external = tmp_path / 'external-data'
    portal = external / 'database_seed' / 'official_portals.json'
    portal.parent.mkdir(parents=True)
    portal.write_text('{"portals": {}}')
    schemes = tmp_path / 'external-schemes.json'
    schemes.write_text('{"entries": []}')
    monkeypatch.setenv('ELLIS_SCHEME_LISTS', str(schemes))
    monkeypatch.setenv('ELLIS_DATA_DIR', str(external))
    monkeypatch.setattr(vo, '_table', lambda: vo._VerificationTable({}))
    initial = cache.context_version(vo)
    # The environment paths stay identical while the consulted files change.
    schemes.write_text('{"entries": [], "revision": "updated eligibility"}')
    changed_scheme = cache.context_version(vo)
    assert changed_scheme != initial
    portal.write_text('{"portals": {"NPL": "https://www.immigration.gov.np/"}}')
    changed_portal = cache.context_version(vo)
    assert changed_portal != changed_scheme
    schemes.unlink()
    assert cache.context_version(vo) != changed_portal


def test_exact_review_overlay_changes_invalidate_even_when_parsed_entry_is_identical(tmp_path, monkeypatch):
    from copy import deepcopy
    from pathlib import Path
    import json
    from app.visa_snapshot import verified_overrides as vo
    from app.visa_snapshot import reviewed_japan_warning_resolution as warning
    from scripts import convert_reviewed_japan_singapore_fields as conversion
    data = Path(__file__).resolve().parents[2] / 'data/database_seed'
    manifest = json.loads((data / conversion.MANIFEST).read_text())
    overlay = json.loads((data / conversion.OVERLAY).read_text())
    layers = [dict(deepcopy(r['baseline']), cache_key=r['cache_key']) for r in manifest['routes']]
    layer = next(r for r in layers if 'JPN' in r['cache_key'])
    core = tmp_path / 'verified_overrides.json'
    operator = tmp_path / 'operator.json'
    core.write_text(json.dumps([e for r in layers for e in r['seed_entries']]))
    operator.write_text('[]')
    (tmp_path / conversion.MANIFEST).write_text(json.dumps(manifest))
    prepared = tmp_path / conversion.OVERLAY
    prepared.write_text(json.dumps(overlay))
    monkeypatch.setattr(vo, 'OVERRIDES', core)
    monkeypatch.setattr(vo, 'operator_overrides_path', lambda: operator)
    monkeypatch.setattr(vo, '_reviewed_overlay_paths', lambda: [prepared])
    monkeypatch.setattr(freshness, 'active_disputed_fields', lambda *a: [])
    monkeypatch.setattr(kimi_primary, '_is_stale', lambda r: False)
    cache._CACHE.clear(); vo.reload(); warning._rebuilt.cache_clear()
    route = layer['route']
    row = KimiRouteGuidanceCache(cache_key=layer['cache_key'], route=route,
        guidance=layer['raw_guidance'], status=kimi_primary.STATUS_PRIMARY)
    def build(db, row, route):
        return [{'uncertainty': vo.apply(row.guidance, route)[0]['uncertainty']}]
    def read():
        return cache.project(None, row, route, cache.context_version(vo), build)
    try:
        original_entry = deepcopy(vo.find(route))
        assert read() == [{'uncertainty': []}]
        overlay['tampered'] = True
        prepared.write_text(json.dumps(overlay)); vo.reload()
        # This top-level key is intentionally ignored by ordinary parsing,
        # but invalidates the exact reviewed conversion that removes caveats.
        assert vo.find(route) == original_entry
        expected = build(None, row, route)
        assert expected[0]['uncertainty']
        assert read() == expected
    finally:
        vo.reload(); warning._rebuilt.cache_clear(); cache._CACHE.clear()
