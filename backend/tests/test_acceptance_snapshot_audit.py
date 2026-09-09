import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    'acceptance_audit', Path(__file__).parents[1] / 'scripts/audit_acceptance_snapshot.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def snapshot(**updates):
    row = dict(cache_key='sample', visa_fee_amount=0, source_url='https://example.gov/',
               special_conditions=None, confidence_level='Low', held=True,
               source_check='reference', field_status={'visa_fee_amount': 'pending-review'})
    row.update(updates)
    return dict(records=[row], fields=['visa_fee_amount', 'source_url', 'special_conditions'],
                required_fields=['visa_fee_amount', 'source_url'])


def test_zero_fee_is_filled_but_optional_blanks_stay_in_strict_denominator():
    result = module.audit(snapshot())
    assert result['strict_exported_cell_completeness_percent'] == pytest.approx(200 / 3)
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
    result = module.audit(data)
    assert result['source_url_present_percent'] is None
    assert result['acceptance_certified'] is False


def test_incomplete_snapshot_rejected():
    data = snapshot()
    data['total'] = 2
    with pytest.raises(ValueError, match='incomplete snapshot'):
        module.audit(data)
