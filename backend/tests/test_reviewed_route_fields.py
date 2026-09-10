"""The Oman correction must never buy a new verdict or product review."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import convert_reviewed_route_fields as c
from scripts.prepare_reviewed_product_patch import PatchRejected, digest
from app.visa_snapshot import verified_overrides as vo, tstation

ROOT = Path(__file__).resolve().parents[2] / 'data/database_seed'


@pytest.fixture
def inputs():
    manifest = json.loads((ROOT / 'reviewed_route_field_manifest_oman20260910e.json').read_text())
    return manifest['specification'], [dict(deepcopy(manifest['baseline']), cache_key=manifest['cache_key'])]


def rebound(spec, layers):
    spec['baseline_sha256'] = {k: digest(layers[0][k]) for k in c.BASELINE_KEYS}
    return c.build_manifest(spec, layers)


def convert(spec, layers):
    return c.convert(c.build_manifest(spec, layers), layers)


def test_only_three_document_fields_gain_own_proofs_without_renewing_verdict(inputs):
    spec, layers = inputs; untouched = deepcopy(layers)
    overlay, report, guidance, provenance = convert(spec, layers)
    assert layers == untouched
    baseline = layers[0]; original = baseline['merged_guidance']
    assert {k for k in guidance if guidance.get(k) != original.get(k)} == c.FIELDS
    assert guidance['visa_products'] == original['visa_products']
    assert guidance['passport_validity'] == original['passport_validity'] == '6 months beyond arrival'
    assert guidance['financial_evidence'] == original['financial_evidence']
    assert guidance['insurance_required'] == original['insurance_required'] is True
    entry = overlay['entries'][0]
    prior = vo._parse_rows(baseline['seed_entries'], {})[vo._key('HKG', 'OMN', 'tourism', 'ordinary_passport')]
    for key in ('source_url', 'verified_at', 'verified_by', 'verifier', 'note'):
        assert entry[key] == prior[key]
    assert entry['verified_at'] == '2026-09-01'
    for field, proof in prior['field_provenance'].items():
        assert entry['field_provenance'][field] == proof
    for field in c.FIELDS:
        proof = entry['field_provenance'][field]
        assert proof['verified_at'] == '2026-09-10'
        assert proof['source_url'] == c.SOURCE_URL and proof['verifier'] == 'ai'
        assert proof['status'] == 'reviewed' and proof['verification_scope'] == 'route_entry_document_fields_only'
        assert proof['subject'] == {'passport_nationality': 'HKG', 'destination_country': 'OMN',
                                   'travel_purpose': 'tourism', 'travel_document_type': 'ordinary_passport'}
        assert c.NATIONALITY in proof['additional_quotes']
    for key in ('verified_at', 'verified_by', 'source_url', 'verifier', 'note'):
        assert provenance[key] == baseline['source_provenance'][key]
    assert set(provenance['fields']) == set(baseline['source_provenance']['fields']) | c.FIELDS
    assert report['new_verdict_reviews'] == report['new_product_reviews'] == 0
    assert not any(report[x] for x in ('new_release', 'new_grounded_check', 'renew_fresh_until'))


def test_registered_loader_projects_return_ticket_and_hotel_without_paid_product_review(inputs, tmp_path):
    spec, layers = inputs
    overlay, _, expected, expected_provenance = convert(spec, layers)
    base = tmp_path / 'base.json'; target = tmp_path / 'fields.json'; operator = tmp_path / 'operator.json'
    base.write_text(json.dumps(layers[0]['seed_entries'])); target.write_text(json.dumps(overlay)); operator.write_text('[]')
    with patch.object(vo, 'OVERRIDES', base), patch.object(vo, '_reviewed_overlay_paths', return_value=[target]), patch.object(vo, 'operator_overrides_path', return_value=operator):
        table = vo._load_table()
    with patch.object(vo, 'find', return_value=table[vo._key('HKG', 'OMN', 'tourism', 'ordinary_passport')]):
        guidance, provenance = vo.apply(layers[0]['raw_guidance'], layers[0]['route'])
    assert guidance == expected and provenance == expected_provenance
    old = tstation.records_for_route(layers[0]['route'], layers[0]['merged_guidance'], layers[0]['source_provenance'])
    rows = tstation.records_for_route(layers[0]['route'], guidance, provenance)
    assert len(rows) == len(old) == 3
    for before, after in zip(old, rows, strict=True):
        if after['visa_type_name'] == 'Visa-free entry':
            assert 'Return ticket' in after['entry_requirements']
            assert 'Confirmed hotel reservation' in after['entry_requirements']
            assert 'return/onward' not in after['entry_requirements'] and 'host invitation' not in after['entry_requirements']
            assert 'Health insurance' in after['required_documents'] and 'Sufficient funds' in after['required_documents']
            before = {k: v for k, v in before.items() if k not in ('required_documents', 'entry_requirements')}
            after = {k: v for k, v in after.items() if k not in ('required_documents', 'entry_requirements')}
        else:
            assert after['confidence_level'] == 'Low'
            assert after['_product_source_verified'] is None
        assert before == after


@pytest.mark.parametrize('name', c.BASELINE_KEYS)
def test_each_live_layer_is_cas_bound_before_candidate_construction(inputs, name):
    spec, layers = inputs; manifest = c.build_manifest(spec, layers)
    value = layers[0][name]
    if isinstance(value, dict): value['concurrent_change'] = True
    else: value.append({'concurrent_change': True})
    with patch.object(c, '_convert_entry') as construction:
        with pytest.raises(PatchRejected, match='Layer changed since review'):
            c.convert(manifest, layers)
        construction.assert_not_called()


def test_seed_order_is_bound_even_if_effective_route_looks_unchanged(inputs):
    spec, layers = inputs
    earlier = deepcopy(layers[0]['seed_entries'][0]); earlier['note'] = 'Earlier superseded seed'
    layers[0]['seed_entries'].insert(0, earlier)
    manifest = rebound(spec, layers)
    layers[0]['seed_entries'].reverse()
    with pytest.raises(PatchRejected, match='seed_entries'): c.convert(manifest, layers)


@pytest.mark.parametrize('field,value', [('passport_nationality', 'CHN'), ('lawful_country_of_residence', 'USA'),
                                       ('destination_country', 'SAU'), ('travel_purpose', 'business'),
                                       ('travel_document_type', 'diplomatic_passport'), ('arrival_date', '2027-01-01')])
def test_other_subjects_cannot_borrow_oman_hksar_tourist_conditions(inputs, field, value):
    spec, layers = inputs
    layers[0]['route'][field] = spec['route'][field] = value
    with pytest.raises(PatchRejected): rebound(spec, layers)


def test_operator_edits_cannot_be_replaced_by_this_contract(inputs):
    spec, layers = inputs; layers[0]['operator_entries'].append({'note': 'Operator-authored route correction'})
    manifest = rebound(spec, layers)
    with pytest.raises(PatchRejected, match='Operator-authored'): c.convert(manifest, layers)


@pytest.mark.parametrize('field', ['disposition', 'visa_products', 'passport_validity', 'confidence', 'exceptions', 'fresh_until'])
def test_allowlist_refuses_any_fourth_changed_field(inputs, field):
    spec, layers = inputs
    spec['changes'].append({'field': field, 'old_raw': None, 'old_merged': None, 'new': None, 'proof': {}})
    with pytest.raises(PatchRejected, match='three reviewed'): convert(spec, layers)


@pytest.mark.parametrize('field,value', [('required_documents', ['Return ticket', 'Confirmed hotel reservation']),
                                       ('onward_travel_evidence', 'Return or onward ticket'),
                                       ('accommodation_evidence', 'Hotel reservation or host invitation')])
def test_cumulative_conditions_cannot_be_removed_or_weakened(inputs, field, value):
    spec, layers = inputs; next(x for x in spec['changes'] if x['field'] == field)['new'] = value
    with pytest.raises(PatchRejected, match='cumulative'): convert(spec, layers)


def test_old_field_expectations_cannot_be_rewritten(inputs):
    spec, layers = inputs; spec['changes'][0]['old_merged'] = []
    with pytest.raises(PatchRejected, match='old field'): convert(spec, layers)


@pytest.mark.parametrize('name,value', [('status', 'unknown'), ('verifier', 'human'),
                                      ('effective_to', '2027-01-01'), ('verification_scope', 'all_fields'),
                                      ('subject', {'passport_nationality': 'CHN'})])
def test_review_cannot_change_verdict_scope_or_attribution(inputs, name, value):
    spec, layers = inputs; spec['changes'][0]['proof'][name] = value
    with pytest.raises(PatchRejected): convert(spec, layers)


@pytest.mark.parametrize('mutation', ['foreign_url', 'changed_quote', 'missing_hong_kong', 'second_group', 'hash_drift', 'future_date', 'wrong_date'])
def test_source_authority_literal_scope_hash_and_date_are_required(inputs, mutation):
    spec, layers = inputs; source = spec['sources'][0]
    if mutation == 'foreign_url':
        source['url'] = 'https://www.homeaffairs.gov.au/'
        for change in spec['changes']:
            for proof in change['proof']['evidence']: proof['source_url'] = source['url']
    if mutation == 'changed_quote': spec['changes'][0]['proof']['evidence'][0]['quote'] = 'He must have a return ticket or onward ticket.'
    if mutation == 'missing_hong_kong': source['text'] = source['text'].replace(c.NATIONALITY, 'China')
    if mutation == 'second_group': source['text'] = source['text'].replace(c.NATIONALITY, '').replace('Second group', 'Second group\n' + c.NATIONALITY)
    if mutation == 'hash_drift': source['text'] += 'Changed without recapture'
    else: source['sha256'] = hashlib.sha256(source['text'].encode()).hexdigest()
    if mutation in ('future_date', 'wrong_date'):
        for change in spec['changes']: change['proof']['verified_at'] = '2099-01-01' if mutation == 'future_date' else '2026-09-09'
        if mutation == 'future_date': source['checked_at'] = '2099-01-01'
    with pytest.raises(PatchRejected): convert(spec, layers)


def test_documented_source_cannot_refresh_any_top_level_date(inputs):
    spec, layers = inputs; spec['fields'] = {'verified_at': '2026-09-10'}
    with pytest.raises(PatchRejected, match='Unexpected mutation'): convert(spec, layers)


def test_tampered_manifest_or_source_provenance_reconstruction_is_rejected(inputs):
    spec, layers = inputs; manifest = c.build_manifest(spec, layers)
    manifest['baseline']['source_provenance']['verified_at'] = '2026-09-10'
    with pytest.raises(PatchRejected): c.convert(manifest, layers)
    layers[0]['source_provenance']['verified_at'] = '2026-09-10'
    manifest = rebound(spec, layers)
    with pytest.raises(PatchRejected, match='authorship'): c.convert(manifest, layers)


def test_failure_cannot_create_or_overwrite_candidate_file(inputs, tmp_path):
    spec, layers = inputs; manifest = c.build_manifest(spec, layers)
    layers[0]['raw_guidance']['new_live_fact'] = True
    path = tmp_path / 'candidate.json'
    with pytest.raises(PatchRejected): c.convert_to_file(manifest, layers, path)
    assert not path.exists()
    path.write_text('Prior candidate')
    with pytest.raises(PatchRejected): c.convert_to_file(manifest, layers, path)
    assert path.read_text() == 'Prior candidate' and list(tmp_path.iterdir()) == [path]


def test_no_registered_release_and_candidate_file_matches_in_memory(inputs, tmp_path):
    spec, layers = inputs; manifest = c.build_manifest(spec, layers)
    path = tmp_path / 'candidate.json'
    result = c.convert_to_file(manifest, layers, path)
    assert json.loads(path.read_text()) == result[0]
    assert result[0]['status'] == 'not_installed'
