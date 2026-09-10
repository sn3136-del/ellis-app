"""Reviewed insurance booleans may change without inventing an exemption."""
from copy import deepcopy

import pytest

from app.visa_snapshot import verified_overrides as vo


@pytest.mark.parametrize('value', [True, False, None])
def test_explicit_nullable_insurance_value_is_retained(value):
    entry = {'route': {'nationality': 'KOR', 'destination': 'CHN',
                      'travel_document_type': 'ordinary_passport', 'travel_purpose': 'tourism'},
             'source_url': 'https://cs.mfa.gov.cn/', 'verified_at': '2026-09-10',
             'verifier': 'ai', 'fields': {'insurance_required': value}}
    if value is None:
        entry['field_provenance'] = {'insurance_required': {
            'status': 'unknown', 'reason': 'No supported insurance claim is retained.'}}
    parsed = vo._parse_rows([entry], {})[vo._key('KOR', 'CHN', 'tourism', 'ordinary_passport')]
    assert parsed['fields']['insurance_required'] is value
    merged, _ = vo.merge_verified_fields({'insurance_required': not bool(value)}, parsed['fields'])
    assert merged['insurance_required'] is value
    if value is None:
        proof = parsed['field_provenance']['insurance_required']
        assert proof['status'] == 'unknown'
        assert proof['verified_at'] is None and not proof['source_url']
        assert 'unpublished' not in str(proof).lower()


@pytest.mark.parametrize('value', ['false', 'true', 'No', 'Unknown', 0, 1, [], {}, 0.0])
def test_invalid_insurance_value_is_quarantined_without_altering_raw(value):
    entry = {'route': {'nationality': 'KOR', 'destination': 'CHN'},
             'source_url': 'https://cs.mfa.gov.cn/', 'verified_at': '2026-09-10',
             'fields': {'insurance_required': value, 'processing_time': 'Existing processing fact'}}
    before = deepcopy(entry)
    parsed = vo._parse_rows([entry], {})['KOR|CHN|tourism']
    assert 'insurance_required' not in parsed['fields']
    assert parsed['fields']['processing_time'] == 'Existing processing fact'
    assert entry == before
