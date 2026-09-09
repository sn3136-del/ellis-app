"""Finite Japan source review: detached corrections, never a blanket release."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from scripts.convert_reviewed_japan_patch import (
    build_manifest, convert_manifest, validate_review, UNSUPPORTED_ROWS,
    AMBIGUOUS_FORMAT_ROWS, BASELINE_KEYS, PROTECTED,
)
from scripts.prepare_reviewed_product_patch import PatchRejected
from app.visa_snapshot import verified_overrides as vo, tstation


@pytest.fixture(scope='module')
def review():
    path = Path(__file__).resolve().parents[2] / 'data/database_seed/reviewed_japan_products_2026_09_09.json'
    return json.loads(path.read_text())


@pytest.fixture(scope='module')
def manifest(review):
    return build_manifest(review, review['integration_baselines'])


@pytest.fixture(scope='module')
def candidate(review, manifest):
    return convert_manifest(manifest, review['integration_baselines'])


def result(candidate, review, nationality, purpose='tourism'):
    entry = next(e for e in candidate['entries'] if e['route']['nationality'] == nationality and e['route']['travel_purpose'] == purpose)
    baseline = next(e for e in review['integration_baselines'] if e['route']['passport_nationality'] == nationality and e['route']['travel_purpose'] == purpose)
    key = vo._key(nationality, 'JPN', purpose, 'ordinary_passport')
    parsed = vo._parse_rows([entry], {})[key]
    guidance, _ = vo.merge_verified_fields(baseline['raw_guidance'], parsed['fields'], source_url=entry['source_url'])
    provenance = dict(parsed['field_provenance']['disposition'], fields=list(parsed['fields']), field_provenance=parsed['field_provenance'])
    return entry, baseline, guidance, provenance


def projection(candidate, review, nationality, purpose='tourism', disputed_fields=()):
    e, b, g, p = result(candidate, review, nationality, purpose)
    return tstation.records_for_route(b['route'], g, p, disputed_fields=disputed_fields)


def test_exact_26_review_rows_16_routes_without_mutating_baselines(review, manifest, candidate):
    original = deepcopy(review['integration_baselines'])
    assert len(review['rows']) == 26 and len(candidate['entries']) == 16
    assert sum(len(e['fields'].get('visa_products') or []) for e in candidate['entries']) == 22
    blockers = [b for r in candidate['preflight'] for b in r['blocking_review']]
    assert sum('row' in b for b in blockers) == 13
    assert sum(b.get('kind') == 'application_date_guard' for b in blockers) == 1
    assert candidate == convert_manifest(manifest, review['integration_baselines'])
    assert original == review['integration_baselines']
    assert all(not p['new_release'] and not p['new_grounded_check'] and not p['renew_fresh_until'] for p in candidate['preflight'])
    assert all('New merged conflicts require review' != b['reason'] for p in candidate['preflight'] for b in p['blocking_review'])


def test_candidate_is_not_registered_and_reports_review_only(candidate):
    assert candidate['status'] == 'DETACHED_REVIEW_ONLY_NOT_REGISTERED'
    assert 'reviewed_japan_overlay_2026_09_09.json' not in vo.REVIEWED_OVERLAY_NAMES


@pytest.mark.parametrize('index', sorted(UNSUPPORTED_ROWS | AMBIGUOUS_FORMAT_ROWS))
def test_unestablished_named_option_or_format_cannot_receive_parent_credit(review, candidate, index):
    row = next(r for r in review['rows'] if r['row_index'] == index)
    entry, baseline, guidance, prov = result(candidate, review, row['route']['passport_nationality'], row['route']['travel_purpose'])
    name = row['match']['projected_visa_type_name']
    product = next(p for p in guidance['visa_products'] if p['type'] == name)
    proof = product['field_provenance']['disposition']
    assert proof['status'] == 'unknown' and not proof['source_url'] and proof['verified_at'] is None
    output = next(r for r in tstation.records_for_route(baseline['route'], guidance, prov) if r['visa_type_name'] == name)
    assert output['confidence_level'] == 'Low'
    if index in UNSUPPORTED_ROWS:
        old = next(p for p in baseline['merged_guidance']['visa_products'] if p['type'] == name)
        assert {k: v for k, v in product.items() if k != 'field_provenance'} == {k: v for k, v in old.items() if k != 'field_provenance'}


def test_unreviewed_four_products_keep_exact_claims_and_authorship(review, candidate):
    seen = 0
    names = {r['match']['projected_visa_type_name'] for r in review['rows']}
    for nationality in ('IDN', 'IND', 'PHL'):
        entry, baseline, guidance, _ = result(candidate, review, nationality)
        assert [p['type'] for p in guidance['visa_products']] == [p['type'] for p in baseline['merged_guidance']['visa_products']]
        for old, product in zip(baseline['merged_guidance']['visa_products'], guidance['visa_products'], strict=True):
            if product['type'] not in names:
                assert product == old
                seen += 1
    assert seen == 4


def test_current_vietnam_local_fees_and_multiple_stay_correct_without_global_fee_claim(review, candidate):
    entry, _, g, _ = result(candidate, review, 'VNM')
    assert g['government_fee']['amount'] == 2590000 and g['government_fee']['currency'] == 'VND'
    single, multiple = g['visa_products']
    assert multiple['fee']['amount'] == 5170000 and multiple['max_stay_days'] == 30
    assert '15 or 30' in multiple['permitted_stay'] and '1,3 or 5 years' in multiple['validity']
    for p in (single, multiple):
        assert '1 July 2026–31 March 2027' in p['fee']['notes']
        assert 'Hanoi embassy jurisdiction' in p['processing_time']
        assert p['field_provenance']['fee']['source_url'].endswith('VN_VisaFee.html')
    assert entry['field_provenance']['government_fee']['subject']['passport_nationality'] == 'VNM'
    proof = entry['field_provenance']['government_fee']
    window = {'date_role': 'application_acceptance', 'from': '2026-07-01', 'through': '2027-03-31'}
    assert proof['subject']['application_acceptance_window'] == window
    assert all(p['field_provenance']['fee']['subject']['application_acceptance_window'] == window for p in (single, multiple))
    parsed = vo._parse_rows([entry], {})[vo._key('VNM', 'JPN', 'tourism', 'ordinary_passport')]
    assert parsed['field_provenance']['government_fee']['subject']['application_acceptance_window'] == window
    report = next(p for p in candidate['preflight'] if p['cache_key'].startswith('VNM|'))
    assert any(b.get('kind') == 'application_date_guard' and b['fee_window'] == window for b in report['blocking_review'])


def test_chinese_student_local_fee_and_discretionary_calendar_stay(review, candidate):
    entry, _, g, _ = result(candidate, review, 'CHN', 'study')
    product = g['visa_products'][0]
    assert g['government_fee']['amount'] == product['fee']['amount'] == 715
    assert product['fee']['currency'] == 'CNY'
    assert g['permitted_stay_days'] is None and product['max_stay_days'] is None
    assert '4 years 3 calendar months' in product['permitted_stay']
    assert 'does not guarantee' in product['notes']
    p = product['field_provenance']['fee']
    assert p['source_url'].endswith('visa_qa.html') and p['verifier'] == 'ai'
    assert p['subject']['travel_purpose'] == 'study'
    out = projection(candidate, review, 'CHN', 'study')[0]
    assert out['max_stay_duration'] is None and out['confidence_level'] != 'High'


def test_business_headline_and_products_use_the_same_reviewed_beijing_timing(review, candidate):
    entry, _, g, _ = result(candidate, review, 'CHN', 'business')
    primary = g['visa_products'][0]
    assert g['processing_time'] == primary['processing_time']
    assert 'normally 4 working days' in g['processing_time']
    assert 'Beijing embassy jurisdiction' in g['processing_time']
    assert g['government_fee'] == primary['fee']
    assert 'Chinese nationals applying in China' in g['government_fee']['notes']
    assert entry['field_provenance']['processing_time']['subject']['travel_purpose'] == 'business'


def test_philippine_package_evisa_is_fifteen_days_gratis_and_its_own_scope(review, candidate):
    entry, _, g, _ = result(candidate, review, 'PHL')
    product = g['visa_products'][1]
    assert product['requirement_detail'] == 'evisa' and product['max_stay_days'] == 15
    assert product['fee']['amount'] == 0 and product['required_documents'] is None
    assert 'resident in the Philippines' in product['notes'] and 'designated packaged tour only' in product['notes']
    assert 'by air or international scheduled passenger ferry between Japan and Busan or Shanghai' in product['notes']
    assert 'may require an in-person interview' in product['notes']
    note = product['field_provenance']['notes']
    assert any('international scheduled passenger ferries' in q for q in note.get('additional_quotes', []))
    assert not product['field_provenance']['required_documents']['source_url']
    proof = product['field_provenance']['disposition']
    assert proof['subject']['product_type'] == product['type']
    assert proof['subject']['disposition'] == product['disposition']
    assert proof['subject']['requirement_detail'] == product['requirement_detail']


@pytest.mark.parametrize('nationality', ['GBR', 'DEU', 'CAN'])
def test_exemption_has_initial_ninety_days_and_no_invented_visa_validity(review, candidate, nationality):
    out = projection(candidate, review, nationality)[0]
    assert out['max_stay_duration'] == 90 and out['validity_duration'] is None
    assert out['visa_fee_amount'] == 0 and out['confidence_level'] != 'High'
    entry, _, _, _ = result(candidate, review, nationality)
    proof = entry['field_provenance']['exceptions']
    assert proof['status'] == 'partial' and proof['verification_scope'] == 'changed_elements_only'
    if nationality in ('GBR', 'DEU'):
        assert '6 calendar months' in proof['verified_elements'][0]
    if nationality == 'GBR':
        assert 'British Citizen passport only' in proof['verified_elements'][0]


def test_existing_dispute_still_holds_every_product_without_fabricating_new_release(review, candidate):
    out = projection(candidate, review, 'CHN', 'business', ['passport_validity'])
    assert all(r['confidence_level'] == 'Low' for r in out)
    assert all('passport_validity' in r['_disputed_fields'] for r in out)


def test_only_exact_unsupported_iceland_extension_is_archived_not_blanket_denied(review, candidate):
    from scripts.convert_reviewed_japan_patch import ISL_UNSUPPORTED_EXTENSION
    entry, baseline, g, _ = result(candidate, review, 'ISL')
    assert ISL_UNSUPPORTED_EXTENSION not in g['exceptions']
    assert "Entry is granted as 'Temporary Visitor' status; paid work is prohibited" in g['exceptions']
    assert not any('cannot extend' in str(x).lower() for x in g['exceptions'])
    report = next(p for p in candidate['preflight'] if p['cache_key'].startswith('ISL|'))
    assert report['archived_unsupported_clauses'][0]['text'] == ISL_UNSUPPORTED_EXTENSION
    assert ISL_UNSUPPORTED_EXTENSION in baseline['merged_guidance']['exceptions']
    proof = entry['field_provenance']['exceptions']
    assert proof['status'] == 'partial'
    assert ISL_UNSUPPORTED_EXTENSION not in proof['retained_unverified_elements']


@pytest.mark.parametrize('field', BASELINE_KEYS)
def test_changed_current_layer_requires_new_review(review, manifest, field):
    layers = deepcopy(review['integration_baselines'])
    value = layers[0][field]
    if isinstance(value, dict): value['new_value'] = 'changed'
    elif isinstance(value, list): value.append({'new_value': 'changed'})
    else: layers[0][field] = {'new_value': 'changed'}
    before = deepcopy(layers)
    with pytest.raises(PatchRejected, match='changed since reviewed baseline'):
        convert_manifest(manifest, layers)
    assert layers == before


@pytest.mark.parametrize('fault', ['missing_route', 'duplicate_route', 'missing_match', 'wrong_product', 'missing_hash', 'wrong_hash', 'extra_hash'])
def test_changed_manifest_selection_or_hash_contract_rejects(review, manifest, fault):
    m = deepcopy(manifest)
    if fault == 'missing_route': m['routes'].pop()
    elif fault == 'duplicate_route': m['routes'].append(deepcopy(m['routes'][0]))
    elif fault == 'missing_match': m['routes'][1]['matches'].pop()
    elif fault == 'wrong_product': m['routes'][1]['matches'][0]['product_type'] = 'Another visa'
    elif fault == 'missing_hash': m['routes'][0]['baseline_sha256'].pop('source_provenance')
    elif fault == 'wrong_hash': m['routes'][0]['baseline_sha256']['raw_guidance'] = '0' * 64
    else: m['routes'][0]['baseline_sha256']['unknown'] = '0' * 64
    with pytest.raises(PatchRejected): convert_manifest(m, review['integration_baselines'])


@pytest.mark.parametrize('fault', ['amount', 'scope', 'quote', 'source_url', 'source_text', 'date', 'unknown_value', 'source_id'])
def test_finite_review_rejects_unreviewed_facts_or_evidence(review, fault):
    r = deepcopy(review); row = r['rows'][1]
    if fault == 'amount': row['product_patch']['fee']['amount'] = 0
    elif fault == 'scope': row['route']['travel_document_type'] = 'diplomatic_passport'
    elif fault == 'quote': row['field_provenance']['fee']['evidence'][0]['quote'] = 'The visa is free.'
    elif fault == 'source_url': r['sources'][0]['url'] = 'https://example.com/'
    elif fault == 'source_text': r['sources'][0]['text'] += '\nA fabricated policy.'
    elif fault == 'date': r['sources'][0]['checked_at'] = '2027-01-01'
    elif fault == 'unknown_value': row['product_patch']['requirement_detail'] = 'evisa'
    else: row['field_provenance']['fee']['evidence'][0]['source_id'] = 'student'
    with pytest.raises(PatchRejected): validate_review(r)


def test_an_added_operator_layer_is_not_folded_into_ai_attribution(review):
    layers = deepcopy(review['integration_baselines'])
    layers[0]['operator_entries'] = [{'fields': {'passport_validity': 'Operator decision'}}]
    m = build_manifest(review, layers)
    with pytest.raises(PatchRejected, match='operator edits need separate review'):
        convert_manifest(m, layers)


def test_protected_old_fields_are_preserved_and_no_new_operator_or_expiry_writes(review, candidate):
    for entry in candidate['entries']:
        nat = entry['route']['nationality']; purpose = entry['route']['travel_purpose']
        baseline = next(x for x in review['integration_baselines'] if x['route']['passport_nationality'] == nat and x['route']['travel_purpose'] == purpose)
        old = vo._parse_rows(baseline['seed_entries'], {}).get(vo._key(nat, 'JPN', purpose, 'ordinary_passport'), {})
        for field in PROTECTED:
            assert entry['fields'].get(field) == old.get('fields', {}).get(field)
        assert not any(key in entry for key in ('fresh_until', 'operator_released', 'verification'))


def test_candidate_partitions_complete_routes_without_deleting_held_products(candidate):
    from scripts.convert_reviewed_japan_patch import partition_candidate
    before = deepcopy(candidate)
    split = partition_candidate(candidate)
    assert candidate == before
    ready = split['supported_requirement_corrections']
    held = split['held_reference_corrections']
    bounded = split['requires_application_date_guard']
    assert len(ready['entries']) == 11 and len(held['entries']) == 4 and len(bounded['entries']) == 1
    assert bounded['entries'][0]['route']['nationality'] == 'VNM'
    assert {(e['route']['nationality'], e['route']['travel_purpose']) for e in held['entries']} == {
        ('CHN', 'family_visit'), ('CHN', 'tourism'), ('IDN', 'tourism'), ('IND', 'tourism')}
    all_entries = [e for bucket in split.values() for e in bucket['entries']]
    assert len(all_entries) == len(candidate['entries'])
    assert all(e in candidate['entries'] for e in all_entries)
    assert sum(len(e['fields'].get('visa_products') or []) for e in all_entries) == 22
    assert all(not p['blocking_review'] for p in ready['preflight'])
    assert all(b['status'] == 'DETACHED_REVIEW_ONLY_NOT_REGISTERED' for b in split.values())


def test_cli_checks_saved_review_baseline_instead_of_rebasing_changed_raw(review, tmp_path, monkeypatch):
    from scripts.convert_reviewed_japan_patch import main
    saved = tmp_path / 'review.json'
    current = tmp_path / 'current.json'
    manifest_path = tmp_path / 'manifest.json'
    candidate_path = tmp_path / 'candidate.json'
    saved.write_text(json.dumps(review))
    changed = deepcopy(review['integration_baselines'])
    changed[0]['raw_guidance']['passport_validity'] = 'An intervening unreviewed edit'
    current.write_text(json.dumps(changed))
    monkeypatch.setattr('sys.argv', ['convert', '--review', str(saved), '--current-layers', str(current),
        '--manifest-output', str(manifest_path), '--candidate-output', str(candidate_path)])
    with pytest.raises(PatchRejected, match='changed since reviewed baseline'):
        main()
    assert not manifest_path.exists() and not candidate_path.exists()
