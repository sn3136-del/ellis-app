"""Reviewed PHL fixture: passport-specific rules and evidence stay isolated."""
import json
from app.visa_snapshot import structured_evidence
import os
from pathlib import Path
from datetime import date
import pytest

from scripts import materialize_reviewed_routes as importer
from app.visa_snapshot import verified_overrides, kimi_primary, tstation, records_guard

MANIFEST = Path(os.environ.get('ELLIS_PHL_MANIFEST', str(
    Path(importer.__file__).resolve().parents[2] / 'data/database_seed/reviewed_phl_coverage_2026_09_09.json')))
STATIONS = set('HKG TWN JPN KOR USA THA SGP MYS GBR RUS AUS IDN FRA VNM ESP IND CAN'.split())
MISSING = set('ESP FRA GBR IDN IND JPN MYS RUS THA TWN VNM'.split())


def _rows():
    return {r['route']['nationality']:r for r in json.loads(MANIFEST.read_text())['routes']}


def test_researched_ordinary_tourism_scope_has_seventeen_real_rules_and_eleven_new_routes():
    rows = _rows()
    assert set(rows) == STATIONS
    assert {n for n,r in rows.items() if r['inventory_action'] == 'create_missing_canonical_row'} == MISSING
    for nat,row in rows.items():
        assert row['route'] == {'nationality':nat,'destination':'PHL','travel_purpose':'tourism','travel_document_type':'ordinary_passport'}
        g = row['guidance']
        assert g['disposition'] == 'VISA_EXEMPT'
        assert g['permitted_stay_days'] == (14 if nat in {'HKG','TWN','IND'} else 30)
        assert g['application_channel'] == 'not_required'
        assert g['visa_products'] == []
        assert 'confidence' not in g
        assert 'grounded_check' not in row
        assert g['government_fee'] is None
        assert g['processing_time'] is None
        assert all(p['verifier']=='ai' for p in row['field_provenance'].values())


def test_indian_extended_option_and_taiwan_expiry_cannot_replace_the_baseline():
    rows = _rows()
    ind = rows['IND']
    assert ind['guidance']['permitted_stay_days'] == 14
    assert ind['guidance']['requirement_detail'] == 'unconditional_visa_free'
    assert '30-day' in ' '.join(ind['guidance']['exceptions'])
    assert 'visa or residence permit' in ' '.join(ind['guidance']['exceptions'])
    assert ind['policy_valid_from'] == '2025-06-08'
    tw = rows['TWN']
    assert (tw['policy_valid_from'],tw['policy_valid_through']) == ('2026-07-01','2027-06-30')
    assert tw['field_provenance']['policy_valid_through']['source_id'] == 'taiwan_extension'
    assert tw['guidance']['permitted_stay_days'] == 14
    assert tw['guidance']['visa_products'] == []
    assert rows['HKG']['guidance']['passport_validity'] is None


def test_field_evidence_is_literal_with_bound_nationality_tables(monkeypatch):
    # This checks the manifest's own evidence independently of seed overlay
    # rollout order. A separate integration test must use the real overlays.
    monkeypatch.setattr(importer.overrides,'apply',lambda g,r:(g,None))
    _, _, entries, invalid, _ = importer._manifest(MANIFEST, date(2026,9,9))
    assert invalid == []
    assert len(entries) == 17
    assert all(r['guidance'].get('confidence') is None for r in entries)


def test_tampered_table_cannot_borrow_another_nationalitys_rule():
    data = json.loads(MANIFEST.read_text())
    row = next(r for r in data['routes'] if r['route']['nationality']=='JPN')
    proof = row['field_provenance']['disposition']
    source = next(s for s in data['sources'] if s['id']==proof['source_id'])
    route = {'passport_nationality':'JPN','destination_country':'PHL','travel_purpose':'tourism','travel_document_type':'ordinary_passport'}
    assert structured_evidence._table_support(proof, source, route, 'VISA_EXEMPT')
    wrong = dict(proof, source_table=dict(proof['source_table'],nationality_quote='Indonesia'))
    assert not structured_evidence._table_support(wrong, source, route, 'VISA_EXEMPT')
    assert not structured_evidence._table_support(proof, source, dict(route,travel_document_type='diplomatic_passport'), 'VISA_EXEMPT')
    assert not structured_evidence._table_support(proof, source, dict(route,travel_purpose='work'), 'VISA_EXEMPT')


def test_all_reviewed_fields_survive_the_real_overlay_before_materialization():
    _, _, entries, invalid, _ = importer._manifest(MANIFEST, date(2026,9,9))
    assert invalid == []
    assert len(entries) == 17


@pytest.mark.parametrize('nationality', sorted(STATIONS))
def test_all_seventeen_map_a_single_exempt_baseline_and_separate_arrival_registration(nationality):
    entry = _rows()[nationality]
    route = {'passport_nationality':nationality,'destination_country':'PHL',
             'travel_document_type':'ordinary_passport','travel_purpose':'tourism','arrival_date':'2026-09-09'}
    # A reviewed correction explicitly replaces the stale conflicting fields.
    old = {'disposition':'VISA_REQUIRED','requirement_detail':'evisa',
           'application_channel':'evisa','visa_category':'Old eVisa',
           'visa_products':[{'type':'Old eVisa','fee':{'amount':999,'currency':'USD'}}],
           'government_fee':{'amount':999,'currency':'USD'},'passport_validity':'6 months beyond arrival'}
    guidance, provenance = verified_overrides.apply(old, route)
    assert not kimi_primary.serve_time_invariants(guidance)
    assert guidance['disposition'] == 'VISA_EXEMPT'
    assert guidance['visa_products'] == []
    assert guidance['arrival_card']['required'] is True
    assert guidance['arrival_card']['url'] == 'https://etravel.gov.ph'
    rows = tstation.records_for_route(route, guidance, provenance, grounded_ok=False)
    assert len(rows) == 1
    assert rows[0]['visa_requirement'] == 'Visa-free'
    assert rows[0]['max_stay_duration'] == entry['guidance']['permitted_stay_days']
    assert rows[0]['application_method'] != 'Online Application'
    assert all(p['verifier']=='ai' for p in provenance['field_provenance'].values())
    assert old['government_fee']['amount'] == 999


@pytest.mark.parametrize('arrival,held', [('2026-06-30',True),('2026-07-01',False),('2027-06-30',False),('2027-07-01',True)])
def test_taiwan_announcement_interval_holds_public_claims_even_after_operator_release(arrival, held):
    route = {'passport_nationality':'TWN','destination_country':'PHL',
             'travel_document_type':'ordinary_passport','travel_purpose':'tourism','arrival_date':arrival}
    guidance, provenance = verified_overrides.apply(_rows()['TWN']['guidance'], route)
    assert provenance['field_provenance']['disposition']['effective_to'] == '2027-06-30'
    out = records_guard.apply_records_hold(route, {'guidance':guidance,'source_verified':provenance,
            'operator_released':True,'held':False,'route':route})
    assert out['held'] is held
    assert guidance['disposition'] == 'VISA_EXEMPT'  # internal evidence retained
    if held:
        assert records_guard.held_envelope(out)['guidance'] is None
        assert 'source_verified' not in records_guard.held_envelope(out)
    assert kimi_primary.cache_key(route) == kimi_primary.cache_key(dict(route,arrival_date='2030-01-01'))


def test_group_visa_exception_dates_do_not_limit_an_independent_destination_verdict():
    route = {'passport_nationality':'IDN','destination_country':'KOR','travel_purpose':'tourism','travel_document_type':'ordinary_passport','arrival_date':'2027-01-01'}
    guidance, _ = verified_overrides.apply({'disposition':'VISA_REQUIRED'}, route)
    assert not guidance.get('policy_interval_conflict')
