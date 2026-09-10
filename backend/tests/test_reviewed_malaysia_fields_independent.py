"""Independent bounded review of the detached Malaysia entry-field correction."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from scripts import convert_reviewed_malaysia_fields as c
from scripts.prepare_reviewed_product_patch import PatchRejected

ROOT = Path(__file__).resolve().parents[2] / 'data/database_seed'


def inputs():
    manifest = json.loads((ROOT / 'reviewed_malaysia_field_manifest_idn20260910.json').read_text())
    layer = dict(deepcopy(manifest['baseline']), cache_key=c.CASE_KEY)
    return manifest, layer


def test_entire_prepared_overlay_rebuilds_and_untouched_proofs_remain_exact():
    m, layer = inputs(); before = deepcopy(layer)
    overlay, report, guidance, provenance = c.convert(m, [layer])
    assert overlay == json.loads((ROOT / 'reviewed_malaysia_field_overlay_idn20260910.json').read_text())
    assert layer == before
    for field, proof in layer['source_provenance']['field_provenance'].items():
        if field not in c.FIELDS:
            assert provenance['field_provenance'][field] == proof
    assert guidance['insurance_required'] is False
    assert provenance['field_provenance']['exceptions']['status'] == 'partial'
    assert report['after_records'][0]['confidence_level'] == report['before_records'][0]['confidence_level']


@pytest.mark.parametrize('field', ['route', 'raw_guidance', 'merged_guidance', 'source_provenance', 'seed_entries', 'operator_entries'])
def test_missing_current_layer_is_rejected_without_mutation(field):
    m, layer = inputs(); del layer[field]; before = deepcopy(layer)
    with pytest.raises((PatchRejected, KeyError)):
        c.convert(m, [layer])
    assert layer == before


@pytest.mark.parametrize('field', ['verification_scope', 'effective_from', 'effective_to'])
def test_extra_scope_or_policy_date_cannot_be_injected(field):
    m, layer = inputs()
    m['specification']['changes'][0]['proof'][field] = 'Unrelated broadened claim'
    with pytest.raises(PatchRejected):
        c.build_manifest(m['specification'], [layer])


def test_no_current_route_or_duplicate_route_is_rejected():
    m, layer = inputs()
    for rows in [[], [layer, deepcopy(layer)]]:
        with pytest.raises(PatchRejected):
            c.convert(m, rows)
