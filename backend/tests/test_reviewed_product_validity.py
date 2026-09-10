"""Regression checks for the scoped six-product validity correction."""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch
import pytest

from scripts import convert_reviewed_product_validity as converter
from scripts.prepare_reviewed_product_patch import PatchRejected, digest
from app.visa_snapshot import verified_overrides as vo, tstation

ROOT = Path(__file__).resolve().parents[2] / 'data/database_seed'


@pytest.fixture
def inputs():
    manifest = json.loads((ROOT / 'reviewed_product_field_manifest_russia20260910d.json').read_text())
    return manifest['patch'], [dict(deepcopy(row['baseline']), cache_key=row['cache_key']) for row in manifest['routes']]


def _rebind(spec, layers):
    by_key = {l['cache_key']: l for l in layers}
    for row in spec['routes']:
        layer = by_key[row['cache_key']]
        row['baseline'] = {'raw_guidance_sha256': digest(layer['raw_guidance']),
            'effective_guidance_sha256': digest(layer['merged_guidance']),
            'override_entries': [{'entry_sha256': digest(e)} for e in layer['seed_entries'] + layer['operator_entries']]}
        for p in row['product_patches']:
            product = next(x for x in layer['merged_guidance']['visa_products'] if x['type'] == p['match']['type'])
            p['expected_product_sha256'] = digest(product)
    return converter.build_manifest(spec, layers)


def test_six_validity_bounds_change_without_review_credit_or_history_mutation(inputs):
    spec, layers = inputs; untouched = deepcopy(layers)
    overlay, reports, guidance = converter.convert(converter.build_manifest(spec, layers), layers)
    assert layers == untouched
    assert sum(r['changed_product_validities'] for r in reports) == 6
    assert {r['route']['nationality'] for r in overlay['entries']} == {'FRA', 'IND', 'TWN'}
    for report in reports:
        assert report['new_product_verdict_reviews'] == 0 and not report['new_release']
        for p in report['projection']:
            assert p['validity_duration'] == 3 and p['validity_unit'] == 'Month'
            assert p['source_url'] == converter.RULE_URL
            assert p['data_source'] == 'Ellis product information (reference only)'
            assert p['confidence_level'] == 'Low'
            assert p['collected_at'] is None and p['info_validity'] is None
            assert p['_product_source_verified'] is None
    for entry, layer in zip(overlay['entries'], layers, strict=True):
        route = layer['route']; key = vo._key(route['passport_nationality'], route['destination_country'], route['travel_purpose'], route['travel_document_type'])
        prior = vo._parse_rows(layer['seed_entries'], {})[key]
        assert entry['field_provenance'] == prior['field_provenance']
        for k in ('source_url', 'verified_at', 'verified_by', 'verifier', 'note'):
            assert entry[k] == prior[k]
        for before, after in zip(prior['fields']['visa_products'], entry['fields']['visa_products'], strict=True):
            if before.get('entry') in ('single', 'double') and before.get('requirement_detail') == 'paper_visa':
                assert before['validity'] == '6 months' and after['validity'] == 'Up to 3 months'
                assert after['field_provenance']['validity']['subject']['product_type'] == before['type']
                assert after['field_provenance']['validity']['verification_scope'] == 'product_validity_only'
            else:
                assert before == after
            for k in ('source_url', 'verified_at', 'source_quote', 'entry', 'max_stay_days', 'fee', 'disposition', 'requirement_detail'):
                assert before.get(k) == after.get(k)
            assert before.get('field_provenance', {}).get('disposition') == after.get('field_provenance', {}).get('disposition')


def test_real_registered_loader_and_apply_change_only_validity_duration(inputs, tmp_path):
    spec, layers = inputs
    overlay, _, _ = converter.convert(converter.build_manifest(spec, layers), layers)
    seed = tmp_path / 'seed.json'; overlay_path = tmp_path / 'validity.json'; operators = tmp_path / 'operators.json'
    seed.write_text(json.dumps([e for l in layers for e in l['seed_entries']]))
    overlay_path.write_text(json.dumps(overlay)); operators.write_text('[]')
    with patch.object(vo, 'OVERRIDES', seed), patch.object(vo, '_reviewed_overlay_paths', return_value=[overlay_path]), patch.object(vo, 'operator_overrides_path', return_value=operators):
        table = vo._load_table()
    for layer in layers:
        route = layer['route']; key = vo._key(route['passport_nationality'], route['destination_country'], route['travel_purpose'], route['travel_document_type'])
        with patch.object(vo, 'find', return_value=table[key]):
            guidance, provenance = vo.apply(layer['raw_guidance'], route)
        assert provenance == layer['source_provenance']
        before = tstation.records_for_route(route, layer['merged_guidance'], layer['source_provenance'])
        after = tstation.records_for_route(route, guidance, provenance)
        for b, a in zip(before, after, strict=True):
            if b['validity_duration'] != a['validity_duration']:
                assert b['validity_duration'] == 6 and a['validity_duration'] == 3
                assert b['validity_unit'] == a['validity_unit'] == 'Month'
            b.pop('validity_duration', None); a.pop('validity_duration', None)
            assert b == a


@pytest.mark.parametrize('name', converter.BASELINE_KEYS)
def test_last_route_layer_drift_prevents_any_candidate_construction(inputs, name):
    spec, layers = inputs; manifest = converter.build_manifest(spec, layers)
    value = layers[-1][name]
    if isinstance(value, dict): value['concurrent_change'] = True
    else: value.append({'concurrent_change': True})
    with patch.object(converter, '_convert_entry') as construction:
        with pytest.raises(PatchRejected, match='Layer changed since review'):
            converter.convert(manifest, layers)
        construction.assert_not_called()


def test_seed_precedence_is_cas_bound(inputs):
    spec, layers = inputs; manifest = converter.build_manifest(spec, layers)
    layers[0]['seed_entries'].reverse()
    with pytest.raises(PatchRejected, match='seed_entries'):
        converter.convert(manifest, layers)


@pytest.mark.parametrize('value', ['3 months', 'Up to 3 days', '90 days', '6 months', None])
def test_units_maximum_qualifier_and_entry_branch_cannot_be_substituted(inputs, value):
    spec, layers = inputs; spec['routes'][0]['product_patches'][0]['fields']['validity'] = value
    with pytest.raises(PatchRejected):
        converter.convert(converter.build_manifest(spec, layers), layers)


def test_literal_but_wrong_product_clause_cannot_supply_a_numeric_match(inputs):
    spec, layers = inputs
    spec['routes'][0]['product_patches'][0]['field_provenance']['validity']['evidence'][0]['quote'] = 'Деловые и гуманитарные визы могут быть однократные или двукратные сроком до трех месяцев и многократные — до одного года.'
    with pytest.raises(PatchRejected):
        converter.convert(converter.build_manifest(spec, layers), layers)


def test_multiple_entry_part_of_same_page_cannot_prove_single_entry_maximum(inputs):
    spec, layers = inputs
    p = spec['routes'][0]['product_patches'][0]
    p['fields']['validity'] = '6 months'
    p['field_provenance']['validity']['evidence'][0]['quote'] = 'многократной на срок до 6 месяцев'
    with pytest.raises(PatchRejected, match='entry branch'):
        converter.convert(converter.build_manifest(spec, layers), layers)


@pytest.mark.parametrize('field,value', [('held', False), ('verified_at', '2026-09-09'), ('max_stay_days', 90), ('fee', {'amount': 0, 'currency': 'USD'}), ('disposition', 'VISA_EXEMPT'), ('source_url', converter.RULE_URL)])
def test_eligibility_fee_stay_source_date_or_hold_changes_reject(inputs, field, value):
    spec, layers = inputs; spec['routes'][0]['product_patches'][0]['fields'][field] = value
    with pytest.raises(PatchRejected, match='Only product validity'):
        converter.convert(converter.build_manifest(spec, layers), layers)


def test_wrong_explicit_product_subject_rejects(inputs):
    spec, layers = inputs
    spec['routes'][0]['product_patches'][0]['field_provenance']['validity']['subject'] = {'product_type': 'Unified electronic visa (tourism)'}
    with pytest.raises(PatchRejected, match='subject differs'):
        converter.convert(converter.build_manifest(spec, layers), layers)


@pytest.mark.parametrize('day', ['2099-01-01', '2026-09-08', 'not-a-date'])
def test_review_dates_must_be_real_and_equal_actual_capture(inputs, day):
    spec, layers = inputs; spec['routes'][0]['product_patches'][0]['field_provenance']['validity']['verified_at'] = day
    with pytest.raises(PatchRejected, match='date'):
        converter.convert(converter.build_manifest(spec, layers), layers)


def test_captured_text_hash_rejects_tampering(inputs):
    spec, layers = inputs; spec['sources'][0]['text'] += ' invented rule'
    with pytest.raises(PatchRejected, match='hash'):
        converter.build_manifest(spec, layers)


def test_operator_edit_is_preserved_by_rejecting_unsupported_integration(inputs):
    spec, layers = inputs; layers[0]['operator_entries'].append(deepcopy(layers[0]['seed_entries'][-1]))
    with pytest.raises(PatchRejected, match='Operator-authored'):
        converter.convert(_rebind(spec, layers), layers)


def test_existing_hold_and_freshness_metadata_remain_unchanged(inputs):
    spec, layers = inputs
    for layer in layers:
        for name, value in [('held', True), ('operator_released', False), ('fresh_until', '2026-09-01'), ('quality_issues', ['paper eligibility unresolved'])]:
            layer['raw_guidance'][name] = deepcopy(value); layer['merged_guidance'][name] = deepcopy(value)
    _, _, guidance = converter.convert(_rebind(spec, layers), layers)
    for layer in layers:
        for name in ('held', 'operator_released', 'fresh_until', 'quality_issues'):
            assert guidance[layer['cache_key']][name] == layer['merged_guidance'][name]


def test_last_route_drift_preserves_existing_output_atomically(inputs, tmp_path):
    spec, layers = inputs; manifest = converter.build_manifest(spec, layers)
    output = tmp_path / 'candidate.json'; output.write_text('previous candidate')
    layers[-1]['merged_guidance']['source_quote'] = 'Concurrent official refresh'
    with pytest.raises(PatchRejected, match='Layer changed since review'):
        converter.convert_to_file(manifest, layers, output)
    assert output.read_text() == 'previous candidate'
    assert list(tmp_path.iterdir()) == [output]
