from app.visa_snapshot import tstation


def full_row():
    return {f: 'value' for f in tstation.CONTRACT_FIELDS}


def test_numbered_dictionary_excludes_only_field_five_subcategory():
    assert len(tstation.CONTRACT_FIELDS) == 25
    assert set(tstation.FIELD_ORDER) - set(tstation.CONTRACT_FIELDS) == {'visa_requirement_detail'}


def test_zero_fee_and_missing_subcategory_do_not_invalidate_25_field_record():
    row = full_row()
    row['visa_fee_amount'] = 0
    result = tstation.acceptance_summary([row])
    assert result['field_completeness_rate'] == 1
    assert result['record_completeness_rate'] == 1
    assert result['requirement_support_rate'] == 0
    assert result['accuracy_certified'] is False


def test_unpublished_optional_and_disputed_fields_do_not_get_waived():
    row = full_row()
    row['consulate_district'] = None
    row['_unpublished'] = ['consulate_district']
    row['_disputed'] = ['visa_fee_amount']
    result = tstation.acceptance_summary([row])
    assert result['field_completeness_rate'] == 24 / 25
    assert result['record_completeness_rate'] == 0
    assert result['pending_review_cells'] == 1
    assert result['unchallenged_filled_cells'] == 23


def test_empty_scope_cannot_report_full_acceptance():
    result = tstation.acceptance_summary([])
    assert result['field_completeness_rate'] is None
    assert result['record_completeness_rate'] is None
    assert result['source_url_presence_rate'] is None
