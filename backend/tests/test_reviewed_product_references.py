"""Regression checks against the five captured production route layers.

Run with PYTHONPATH containing this directory and Ellis/backend. All mutations
are in memory; the loader test uses temporary files. No live stores are used.
"""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch
import pytest

from scripts import convert_reviewed_product_references as refs
from scripts.prepare_reviewed_product_patch import PatchRejected, digest
from app.visa_snapshot import verified_overrides as vo, tstation

ROOT = Path(__file__).parent


@pytest.fixture
def inputs():
    manifest = json.loads((ROOT.parents[1] / 'data/database_seed/reviewed_product_reference_manifest_russia20260910.json').read_text())
    spec = manifest['patch']
    layers = [dict(deepcopy(row['baseline']), cache_key=row['cache_key']) for row in manifest['routes']]
    return spec, layers


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
    return refs.build_manifest(spec, layers)


def test_all_11_links_are_references_with_no_verdict_date_or_confidence_credit(inputs):
    spec, layers = inputs
    overlay, reports, effective = refs.convert(refs.build_manifest(spec, layers), layers)
    assert sum(r['changed_product_links'] for r in reports) == 11
    assert len(overlay['entries']) == 5
    for report in reports:
        assert report['retained_integrity_issues'] == []
        for row in report['projection']:
            assert row['source_url'] == 'https://www.kdmid.ru/cons/visas/'
            assert row['data_source'] == 'Ellis product information (reference only)'
            assert row['collected_at'] is None and row['info_validity'] is None
            assert row['confidence_level'] == 'Low'
            assert row['_product_source_verified'] is None
    for layer, entry in zip(layers, overlay['entries'], strict=True):
        old = vo._parse_rows(layer['seed_entries'], {})
        route = layer['route']; key = vo._key(route['passport_nationality'], route['destination_country'], route['travel_purpose'], route['travel_document_type'])
        current = old[key]
        assert entry['field_provenance'] == current['field_provenance']
        for field in ('source_url', 'verified_at', 'verified_by', 'verifier', 'note'):
            assert entry[field] == current[field]
        # The untouched e-visa product is byte-identical and paper validity
        # facts remain explicitly outside this reference-only review.
        assert entry['fields']['visa_products'][0] == current['fields']['visa_products'][0]
        for before, after in zip(current['fields']['visa_products'], entry['fields']['visa_products'], strict=True):
            for field in ('disposition', 'requirement_detail', 'validity', 'entry', 'fee', 'verified_at', 'source_quote'):
                assert before.get(field) == after.get(field)
    jpn = next(e for e in overlay['entries'] if e['route']['nationality'] == 'JPN')['fields']['visa_products'][-1]
    assert 'disposition' not in jpn
    assert jpn['field_provenance']['source_url']['subject']['disposition'] is None
    assert jpn['field_provenance']['source_url']['verification_scope'] == 'source_reference_only'


def test_registered_file_loader_and_real_apply_preserve_verdict_authorship(inputs, tmp_path):
    spec, layers = inputs
    overlay, _, _ = refs.convert(refs.build_manifest(spec, layers), layers)
    seed = tmp_path / 'seed.json'; refs_file = tmp_path / 'references.json'; operators = tmp_path / 'operators.json'
    seed.write_text(json.dumps([r for l in layers for r in l['seed_entries']]))
    refs_file.write_text(json.dumps(overlay)); operators.write_text('[]')
    with patch.object(vo, 'OVERRIDES', seed), patch.object(vo, '_reviewed_overlay_paths', return_value=[refs_file]), patch.object(vo, 'operator_overrides_path', return_value=operators):
        table = vo._load_table()
    for layer in layers:
        route = layer['route']; key = vo._key(route['passport_nationality'], route['destination_country'], route['travel_purpose'], route['travel_document_type'])
        with patch.object(vo, 'find', return_value=table[key]):
            guidance, provenance = vo.apply(layer['raw_guidance'], route)
        assert provenance == layer['source_provenance']
        before = tstation.records_for_route(route, layer['merged_guidance'], layer['source_provenance'])
        after = tstation.records_for_route(route, guidance, provenance)
        for b, a in zip(before, after, strict=True):
            if b['source_url'] != a['source_url']:
                assert not b['source_url'] and a['source_url'] == 'https://www.kdmid.ru/cons/visas/'
            b.pop('source_url', None); a.pop('source_url', None)
            assert b == a


@pytest.mark.parametrize('name', refs.BASELINE_KEYS)
def test_drift_in_any_last_route_layer_fails_before_constructing_any_candidate(inputs, name):
    spec, layers = inputs
    manifest = refs.build_manifest(spec, layers)
    changed = deepcopy(layers)
    value = changed[-1][name]
    if isinstance(value, dict): value['unexpected_change'] = True
    else: value.append({'unexpected_change': True})
    with patch.object(refs, '_convert_entry') as conversion:
        with pytest.raises(PatchRejected, match='Layer changed since review'):
            refs.convert(manifest, changed)
        conversion.assert_not_called()


def test_seed_order_is_part_of_cas(inputs):
    spec, layers = inputs; manifest = refs.build_manifest(spec, layers)
    layers[0]['seed_entries'].reverse()
    with pytest.raises(PatchRejected, match='seed_entries'):
        refs.convert(manifest, layers)


@pytest.mark.parametrize('field,value', [('held', False), ('verified_at', '2026-09-09'), ('fee', {'amount': 0, 'currency': 'USD'}), ('disposition', 'VISA_EXEMPT')])
def test_product_fact_or_release_changes_reject(inputs, field, value):
    spec, layers = inputs
    spec['routes'][0]['product_patches'][0]['fields'][field] = value
    with pytest.raises(PatchRejected, match='Only a product source URL'):
        refs.convert(refs.build_manifest(spec, layers), layers)


def test_route_field_changes_reject(inputs):
    spec, layers = inputs; spec['routes'][0]['fields']['disposition'] = 'VISA_EXEMPT'
    with pytest.raises(PatchRejected, match='cannot change route fields'):
        refs.convert(refs.build_manifest(spec, layers), layers)


def test_unsupported_literal_quote_rejects(inputs):
    spec, layers = inputs
    spec['routes'][0]['product_patches'][0]['field_provenance']['source_url']['evidence'][0]['quote'] = 'This fabricated quotation is not in the official capture.'
    with pytest.raises(PatchRejected, match='(?i)quote|quotation|passage'):
        refs.convert(refs.build_manifest(spec, layers), layers)


def test_source_capture_hash_rejects_tampering(inputs):
    spec, layers = inputs; spec['sources'][0]['text'] += '\nInvented fact'
    with pytest.raises(PatchRejected, match='hash'):
        refs.build_manifest(spec, layers)


def test_unread_link_is_not_accepted_as_generic_coverage(inputs):
    spec, layers = inputs
    spec['routes'][0]['product_patches'][0]['fields']['source_url'] = 'https://evisa.kdmid.ru/'
    with pytest.raises(PatchRejected, match='URL|source'):
        refs.convert(refs.build_manifest(spec, layers), layers)


def test_operator_authorship_is_rejected_instead_of_silently_overwritten(inputs):
    spec, layers = inputs
    layers[0]['operator_entries'] = [deepcopy(layers[0]['seed_entries'][-1])]
    with pytest.raises(PatchRejected, match='Operator-authored'):
        refs.convert(_rebind(spec, layers), layers)


def test_missing_route_and_duplicate_route_reject_atomically(inputs):
    spec, layers = inputs; manifest = refs.build_manifest(spec, layers)
    for changed in (layers[:-1], layers + [layers[-1]]):
        with pytest.raises(PatchRejected, match='missing or duplicated'):
            refs.convert(manifest, changed)


def test_existing_quote_that_would_gain_parent_verification_blocks_source_only_change(inputs):
    spec, layers = inputs
    layer = next(l for l in layers if l['route']['passport_nationality'] == 'JPN')
    # A legacy source quote plus a newly attached URL can otherwise activate
    # parent/container credit. The reference converter must not allow that.
    for collection in (layer['merged_guidance']['visa_products'], layer['seed_entries'][-1]['fields']['visa_products']):
        next(p for p in collection if p['type'] == 'Paper tourist visa')['source_quote'] = 'Legacy unscoped product quote'
    with pytest.raises(PatchRejected, match='projection changes'):
        refs.convert(_rebind(spec, layers), layers)


def test_existing_hold_metadata_is_retained_byte_for_byte(inputs):
    spec, layers = inputs
    for layer in layers:
        for field, value in [('held', True), ('operator_released', False), ('fresh_until', '2026-09-01'), ('quality_issues', ['manual fact review pending'])]:
            layer['raw_guidance'][field] = deepcopy(value)
            layer['merged_guidance'][field] = deepcopy(value)
    overlay, _, effective = refs.convert(_rebind(spec, layers), layers)
    for layer in layers:
        after = effective[layer['cache_key']]
        for field in ('held', 'operator_released', 'fresh_until', 'quality_issues'):
            assert after[field] == layer['merged_guidance'][field]


def test_atomic_failure_leaves_existing_candidate_untouched(inputs, tmp_path):
    spec, layers = inputs; manifest = refs.build_manifest(spec, layers)
    target = tmp_path / 'candidate.json'; target.write_text('previous artifact')
    layers[-1]['raw_guidance']['source_quote'] = 'Concurrent refresh'
    with pytest.raises(PatchRejected, match='Layer changed since review'):
        refs.convert_to_file(manifest, layers, target)
    assert target.read_text() == 'previous artifact'
    assert list(tmp_path.iterdir()) == [target]
