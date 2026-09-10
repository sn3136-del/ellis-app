import importlib.util
from pathlib import Path

import pytest
from app.visa_snapshot import tstation

spec = importlib.util.spec_from_file_location(
    'acceptance_audit', Path(__file__).parents[1] / 'scripts/audit_acceptance_snapshot.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def snapshot(**updates):
    row = {field: 'value' for field in tstation.FIELD_ORDER}
    row.update(cache_key='sample', visa_fee_amount=0, source_url='https://example.gov/',
               special_conditions=None, confidence_level='Low', held=True,
               source_check='reference', field_status={'visa_fee_amount': 'pending-review'})
    row.update(updates)
    return dict(records=[row], fields=list(tstation.FIELD_ORDER),
                required_fields=sorted(tstation.REQUIRED_FIELDS), summary={'total': 1})


def test_zero_fee_is_filled_but_optional_blanks_stay_in_strict_denominator():
    result = module.audit(snapshot())
    assert result['strict_exported_cell_completeness_percent'] == pytest.approx(2500 / 26)
    assert result['contract_field_count'] == 25
    assert result['contract_field_completeness_percent'] == 96
    assert result['strict_exported_complete_records'] == 0
    assert result['pending_review_cells'] == 1
    assert result['acceptance_certified'] is False


def test_url_cannot_turn_into_evidence_or_accuracy_certificate():
    result = module.audit(snapshot())
    assert result['source_url_present_percent'] == 100
    assert result['requirement_supported_percent'] == 0
    assert result['accuracy_certified'] is False


def test_exposed_low_conflict_is_reported_even_with_source_support():
    result = module.audit(snapshot(held=False, contradictions=['conflict'], source_check='ai-quote'))
    assert result['low_rows_exposed'] == result['conflict_rows_exposed'] == 1
    assert result['route_remediation_queue'][0]['conflict_rows'] == 1


def test_empty_data_does_not_pass_with_vacuous_100_percent():
    data = snapshot()
    data['records'] = []
    data['summary']['total'] = 0
    result = module.audit(data)
    assert result['source_url_present_percent'] is None
    assert result['acceptance_certified'] is False


def test_incomplete_snapshot_rejected():
    data = snapshot()
    data['total'] = 2
    with pytest.raises(ValueError, match='incomplete snapshot'):
        module.audit(data)


def test_real_records_api_summary_total_rejects_truncated_snapshot():
    data = snapshot()
    data['summary']['total'] = 9000
    with pytest.raises(ValueError, match='incomplete snapshot'):
        module.audit(data)


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'unknown', 'nonstring'])
def test_contract_denominator_cannot_be_shrunk_or_duplicated(change):
    data = snapshot()
    if change == 'missing':
        data['fields'].remove('info_validity')
    elif change == 'duplicate':
        data['fields'].append('visa_fee_amount')
    elif change == 'unknown':
        data['fields'].append('extra')
    else:
        data['fields'].append(None)
    with pytest.raises(ValueError, match='exact 25-field'):
        module.audit(data)


def test_exact_25_field_shape_without_export_subcategory_is_supported():
    data = snapshot()
    data['fields'] = list(tstation.CONTRACT_FIELDS)
    result = module.audit(data)
    assert result['contract_field_count'] == 25
    assert result['contract_field_completeness_percent'] == 96


@pytest.mark.parametrize('total', [None, True, 1.0, '1'])
def test_snapshot_count_must_be_an_actual_integer(total):
    data = snapshot()
    data['summary']['total'] = total
    with pytest.raises(ValueError, match='incomplete snapshot'):
        module.audit(data)


def test_missing_count_cannot_claim_complete_snapshot():
    data = snapshot()
    data.pop('summary')
    with pytest.raises(ValueError, match='incomplete snapshot'):
        module.audit(data)


def test_legacy_top_level_total_is_validated_and_supported():
    data = snapshot()
    data.pop('summary')
    data['total'] = 1
    assert module.audit(data)['product_rows'] == 1
