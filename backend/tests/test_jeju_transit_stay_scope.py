"""A component's location/time limit must not become the total visa stay."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.visa_snapshot import kimi_primary as kp

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/jeju_group_transit_20260912.json').read_text())


def guidance():
    return deepcopy(FIXTURE['guidance'])


def stay_errors(g):
    return [error for error in kp.validate_answer(g)[2] if 'shorter granted stay' in error]


def test_actual_jeju_group_local_segment_is_not_total_permission():
    g = guidance(); before = deepcopy(g); product = g['visa_products'][4]
    assert kp._named_stay_limits(product['notes']) == [5]
    assert kp._named_stay_limits(kp._total_stay_note(g, product, product['notes'])) == []
    assert stay_errors(g) == []
    assert g == before
    clean = kp.validate_answer(g)[0]
    assert clean['visa_products'] == before['visa_products']
    assert clean['disposition'] == 'CONDITIONAL'
    assert clean['requirement_detail'] == 'transit_visa_free'
    assert '5 days in the permitted tourism areas' in clean['visa_products'][4]['notes']
    assert 'through a designated agency' in clean['visa_products'][4]['notes']
    assert clean['visa_products'][4]['max_stay_days'] == 15


@pytest.mark.parametrize('change', [
    'different_source', 'spoof_source', 'missing_source', 'different_product_source',
    'different_program', 'different_disposition', 'different_requirement',
    'no_jeju_direction', 'no_local_scope', 'no_agency_condition', 'different_local_cap'])
def test_scope_must_be_the_exact_reviewed_program_and_local_condition(change):
    g = guidance(); p = g['visa_products'][4]
    if change == 'different_source': g['source_url'] = 'https://www.hikorea.go.kr/other'
    elif change == 'spoof_source': g['source_url'] = g['source_url'].replace('.go.kr/', '.go.kr.example.com/')
    elif change == 'missing_source': g.pop('source_url')
    elif change == 'different_product_source': p['source_url'] = 'https://www.hikorea.go.kr/other'
    elif change == 'different_program': p['type'] = 'Jeju direct entry (B-2)'
    elif change == 'different_disposition': g['disposition'] = 'VISA_EXEMPT'
    elif change == 'different_requirement': g['requirement_detail'] = 'unconditional_visa_free'
    elif change == 'no_jeju_direction': p['notes'] = p['notes'].replace('on the way to Jeju ', '')
    elif change == 'no_local_scope': p['notes'] = p['notes'].replace('in the permitted tourism areas', 'in total')
    elif change == 'no_agency_condition': p['notes'] = p['notes'].replace(', through a designated agency', '')
    elif change == 'different_local_cap': p['notes'] = p['notes'].replace('5 days', '4 days')
    assert stay_errors(g)


@pytest.mark.parametrize('days', [16, 30, 90])
def test_reviewed_local_clause_cannot_exempt_an_inflated_total(days):
    g = guidance(); g['visa_products'][4]['max_stay_days'] = days
    assert stay_errors(g)


@pytest.mark.parametrize('extra', [
    ' The total stay may not exceed 10 days.',
    ' All participants may stay for only 10 days.',
    ' The permit is granted for 10 days in total.',
    ' Visitors must never stay over 10 days.',
    ' No more than 10 days per entry are granted.',
    ' 总停留10天。'])
def test_real_additional_total_cap_is_never_removed(extra):
    g = guidance(); g['visa_products'][4]['notes'] += extra
    errors = stay_errors(g)
    assert errors and '10 days' in errors[0]


def test_another_products_true_overstay_still_blocks_the_route():
    g = guidance()
    g['visa_products'][2]['max_stay_days'] = 10
    g['visa_products'][2]['notes'] = 'The permit grants a stay of 3 days.'
    assert any('Incheon Airport' in error and '3 days' in error for error in stay_errors(g))


@pytest.mark.parametrize('negation', ['not', 'never', 'without', 'except', 'excluding'])
def test_negated_segment_scope_cannot_borrow_the_exception(negation):
    g = guidance(); p = g['visa_products'][4]
    p['notes'] = p['notes'].replace('on the way to Jeju', negation + ' on the way to Jeju')
    assert stay_errors(g)


def test_other_diagnostics_and_source_values_are_not_repaired_or_promoted():
    g = guidance()
    g['application_channel'] = 'online_portal'
    g['application_channel_detail'] = 'Individuals cannot apply directly; use a designated agency.'
    errors = kp.validate_answer(g)[2]
    assert any('may not file directly' in error for error in errors)
    assert not any('shorter granted stay' in error for error in errors)
    assert g['visa_products'][1]['type'].startswith('Third-country transit waiver')
