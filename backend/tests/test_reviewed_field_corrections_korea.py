from copy import deepcopy
from pathlib import Path
import json
import pytest
from scripts import convert_reviewed_field_corrections as c
from scripts.prepare_reviewed_product_patch import PatchRejected

D = Path(__file__).resolve().parents[2] / 'data/database_seed'
M = json.loads((D / 'reviewed_field_corrections_manifest_korea20260910.json').read_text())
O = json.loads((D / 'reviewed_field_corrections_overlay_korea20260910.json').read_text())
L = [dict(deepcopy(r['baseline']), cache_key=r['cache_key']) for r in M['routes']]
SPEC = M['specification']


def test_full_prepared_rebuild_and_values():
    overlay, report = c.convert(M, L)
    assert overlay == O and c.build_manifest(SPEC, L) == M and len(overlay['entries']) == 1
    g = report['routes'][0]['guidance']
    assert g['passport_validity_requirement'] == {'kind': 'valid_through_departure', 'months': None}
    assert g['passport_validity'].startswith('Valid at entry and throughout the authorised stay')
    assert 'at least 6 months of validity left on the submission date' in g['passport_validity']
    prov = report['routes'][0]['source_provenance']['field_provenance']
    assert prov['passport_validity_requirement']['source_url'].endswith('hikorea.go.kr/info/InfoDatail.pt?CAT_SEQ=160&PARENT_ID=135')
    assert prov['passport_validity']['source_url'].startswith('https://overseas.mofa.go.kr/id-ko/')
    assert all(row['confidence_level'] == 'Low' for row in report['routes'][0]['records'])


@pytest.mark.parametrize('field', c.BASELINE_KEYS)
def test_six_layer_drift_rejects(field):
    layers = deepcopy(L); value = layers[0][field]
    if isinstance(value, dict): value['unreviewed'] = True
    else: value.append({'unreviewed': True})
    with pytest.raises(PatchRejected):
        c.validate(SPEC, layers)


@pytest.mark.parametrize('mode', ['months_rule_without_figure', 'unknown_kind', 'six_months_in_text_without_quote', 'entry_rule_without_validity_quote'])
def test_passport_rules_are_bound_to_their_quotes(mode):
    spec = deepcopy(SPEC); rule, text = spec['routes'][0]['changes']
    if mode == 'months_rule_without_figure':
        rule['new'] = {'kind': 'months_after_arrival', 'months': 3}
    elif mode == 'unknown_kind':
        rule['new'] = {'kind': 'valid_for_duration_of_stay', 'months': None}
    elif mode == 'six_months_in_text_without_quote':
        text['proof']['evidence'] = [e for e in text['proof']['evidence'] if 'mofa.go.kr' not in e['source_url']]
    elif mode == 'entry_rule_without_validity_quote':
        rule['proof']['evidence'] = [e for e in rule['proof']['evidence'] if 'hikorea' not in e['source_url'] and 'klri' not in e['source_url']]
        rule['proof']['evidence'][0]['quote'] = '작성일 2021.06.29 조회수 19396'
    with pytest.raises(PatchRejected):
        c.build_manifest(spec, L)
