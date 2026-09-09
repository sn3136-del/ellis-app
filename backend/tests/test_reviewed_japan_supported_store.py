"""Simulate the proposed optional Japan store without activating it in code."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.visa_snapshot import verified_overrides as vo, scheduled_policies, records_guard, kimi_primary
from scripts.convert_reviewed_japan_patch import prepare_supported_overlay
from scripts.prepare_reviewed_product_patch import PatchRejected, digest

NAME = 'reviewed_japan_supported_overlay_2026_09_09.json'
SEED = Path(__file__).resolve().parents[2] / 'data/database_seed'


@pytest.fixture(scope='module')
def data():
    review = json.loads((SEED / 'reviewed_japan_products_2026_09_09.json').read_text())
    overlay = json.loads((SEED / NAME).read_text())
    return review, overlay


@pytest.fixture
def stores(tmp_path, monkeypatch, data):
    review, overlay = data
    core = tmp_path / 'verified_overrides.json'
    rows = [row for layer in review['integration_baselines'] for row in layer['seed_entries']]
    core.write_text(json.dumps(rows))
    operator = tmp_path / 'operator.json'
    monkeypatch.setattr(vo, 'OVERRIDES', core)
    monkeypatch.setattr(vo, 'operator_overrides_path', lambda: operator)
    monkeypatch.setattr(vo, 'REVIEWED_OVERLAY_NAMES', (*vo.REVIEWED_OVERLAY_NAMES, NAME))
    monkeypatch.setattr(scheduled_policies, 'apply', lambda g, p, r: (deepcopy(g), p))
    vo.reload()
    yield tmp_path / NAME, operator, review, overlay
    vo.reload()


def install(path, overlay):
    path.write_text(json.dumps(overlay))
    vo.reload()


def test_shipped_supported_file_is_exact_guarded_eleven_route_conversion(data):
    review, overlay = data
    assert overlay == prepare_supported_overlay(review, review['integration_baselines'])
    assert overlay['kind'] == 'reviewed_overlay_conversion' and len(overlay['entries']) == 11
    assert overlay['status'] == 'PREPARED_NOT_REGISTERED'
    expected = {('CAN', 'tourism'), ('CHN', 'business'), ('CHN', 'study'),
        ('DEU', 'tourism'), ('ESP', 'tourism'), ('GBR', 'tourism'), ('HKG', 'business'),
        ('ISL', 'tourism'), ('PHL', 'tourism'), ('SGP', 'tourism'), ('TWN', 'tourism')}
    assert {(e['route']['nationality'], e['route']['travel_purpose']) for e in overlay['entries']} == expected
    assert all(not p['blocking_review'] and not p['new_release'] for p in overlay['preflight'])
    assert all(e['route']['travel_document_type'] == 'ordinary_passport' for e in overlay['entries'])
    assert all(e['source_url'] and e['verified_at'] and e['field_provenance']['disposition'] for e in overlay['entries'])


def test_optional_registration_loads_actual_file_without_any_store_failure(stores):
    path, _, review, overlay = stores
    assert vo._load_table().store_errors == ()
    install(path, overlay)
    assert vo._load_table().store_errors == ()
    supported = {(e['route']['nationality'], e['route']['travel_purpose']) for e in overlay['entries']}
    for layer in review['integration_baselines']:
        g, proof = vo.apply(layer['raw_guidance'], layer['route'])
        assert 'source_verification_store_unavailable' not in g
        if (layer['route']['passport_nationality'], layer['route']['travel_purpose']) in supported:
            assert proof['field_provenance']['disposition']['verifier'] == 'ai'
            assert not kimi_primary.serve_time_invariants(g)


def test_excluded_five_routes_keep_identical_guidance_and_provenance(stores):
    path, _, review, overlay = stores
    selected = {(e['route']['nationality'], e['route']['travel_purpose']) for e in overlay['entries']}
    excluded = [l for l in review['integration_baselines'] if (l['route']['passport_nationality'], l['route']['travel_purpose']) not in selected]
    before = {l['cache_key']: vo.apply(l['raw_guidance'], l['route']) for l in excluded}
    install(path, overlay)
    assert len(excluded) == 5
    for layer in excluded:
        assert vo.apply(layer['raw_guidance'], layer['route']) == before[layer['cache_key']]


def test_ready_routes_pass_shared_evidence_gate_but_existing_dispute_always_holds(stores):
    path, _, review, overlay = stores
    install(path, overlay)
    selected = {(e['route']['nationality'], e['route']['travel_purpose']) for e in overlay['entries']}
    for layer in review['integration_baselines']:
        route = layer['route']
        if (route['passport_nationality'], route['travel_purpose']) not in selected: continue
        g, proof = vo.apply(layer['raw_guidance'], route)
        out = {'guidance':g, 'source_verified':proof, 'status':'primary', 'held':False}
        assert not records_guard.apply_records_hold(route, out).get('held')
        disputed = dict(out, operator_released=True,
                        grounded_check={'disputed_fields':['passport_validity']})
        held = records_guard.apply_records_hold(route, disputed)
        assert held['held'] and held['review_required']
        public = records_guard.held_envelope(held)
        assert public['guidance'] is None and 'source_verified' not in public


def test_operator_remains_last_with_its_own_field_authorship(stores):
    path, operator, review, overlay = stores
    install(path, overlay)
    operator.write_text(json.dumps([{'route':{'nationality':'CAN','destination':'JPN','travel_purpose':'tourism', 'travel_document_type':'ordinary_passport'},
        'source_url':'https://www.mofa.go.jp/j_info/visit/visa/short/novisa.html',
        'verified_at':'2026-09-09','verifier':'human','verified_by':'Fixture reviewer',
        'note':'Explicit operator passport review in a fixture',
        'fields':{'passport_validity':'Operator-controlled passport rule'}}]))
    vo.reload()
    layer = next(l for l in review['integration_baselines'] if l['route']['passport_nationality']=='CAN')
    g, proof = vo.apply(layer['raw_guidance'], layer['route'])
    assert g['passport_validity'] == 'Operator-controlled passport rule'
    assert proof['field_provenance']['passport_validity']['verifier'] == 'human'
    assert proof['field_provenance']['disposition']['verifier'] == 'ai'


@pytest.mark.parametrize('malformed', ['json', 'wrong_destination_source', 'wrong_container'])
def test_present_invalid_optional_file_holds_original_claims(stores, malformed):
    path, _, review, overlay = stores
    bad = deepcopy(overlay)
    if malformed == 'json': path.write_text('{invalid')
    elif malformed == 'wrong_container': path.write_text('[]')
    else:
        bad['entries'][0]['source_url'] = 'https://www.gov.uk/eta'
        path.write_text(json.dumps(bad))
    vo.reload()
    layer = next(l for l in review['integration_baselines'] if l['route']['passport_nationality']=='CAN')
    original = deepcopy(layer['raw_guidance'])
    g, proof = vo.apply(original, layer['route'])
    assert g.pop('source_verification_store_unavailable')['stores'] == ['reviewed_overlay']
    assert g == original and proof is None


@pytest.mark.parametrize('field', ['raw_guidance','merged_guidance','source_provenance','seed_entries','operator_entries','route'])
def test_supported_preparer_still_refuses_current_layer_drift(data, field):
    review, _ = data
    layers = deepcopy(review['integration_baselines'])
    value = layers[0][field]
    if isinstance(value, dict): value['new_fact'] = 'changed'
    elif isinstance(value, list): value.append({'new_fact':'changed'})
    else: layers[0][field] = {'new_fact':'changed'}
    before = deepcopy(layers)
    with pytest.raises(PatchRejected, match='changed since reviewed baseline'):
        prepare_supported_overlay(review, layers)
    assert layers == before
