"""Scoped override fields and residence applicability in the verified-override reader."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from app.visa_snapshot import verified_overrides as vo

D = Path(__file__).resolve().parents[2] / 'data/database_seed'
GOV = 'https://www.gov.uk/standard-visitor'
ROUTE = {'passport_nationality': 'HKG', 'destination_country': 'GBR', 'travel_purpose': 'tourism',
         'travel_document_type': 'ordinary_passport', 'lawful_country_of_residence': 'HKG'}


def _entry(**extra):
    base = {'route': {'nationality': 'HKG', 'destination': 'GBR', 'travel_purpose': 'tourism'},
            'source_url': GOV, 'verified_at': '2026-09-10', 'verified_by': 'test', 'verifier': 'ai',
            'note': 'test entry', 'fields': {'processing_time': 'about 3 weeks', 'forms': []},
            'field_provenance': {'forms': {'source_url': GOV, 'verified_at': '2026-09-10', 'verified_by': 'test', 'verifier': 'ai', 'note': 'legacy'}}}
    base.update(extra)
    return base


def _scoped_proof(field):
    return {'status': 'reviewed', 'source_url': GOV, 'verified_at': '2026-09-10', 'verified_by': 'test', 'verifier': 'ai',
            'note': 'scoped review', 'quote': 'Apply online', 'subject': {'passport_nationality': 'HKG', 'destination_country': 'GBR', 'travel_purpose': 'tourism', 'travel_document_type': 'ordinary_passport'},
            'verification_scope': {'kind': 'test_scoped_review', 'field': field, 'evidence': [{'source_url': GOV, 'quote': 'Apply online'}]}}


def test_legacy_forms_entry_stays_ignored():
    parsed = vo._parse_rows([_entry()], {})[vo._key('HKG', 'GBR', 'tourism', 'ordinary_passport')]
    assert 'forms' not in parsed['fields'] and 'forms' not in parsed['field_provenance']
    assert parsed['fields'] == {'processing_time': 'about 3 weeks'}


@pytest.mark.parametrize('field,value', [('forms', ['Online application']), ('route_workflow_type', 'embassy_submission')])
def test_explicitly_scoped_proof_admits_the_field(field, value):
    entry = _entry(fields={'processing_time': 'about 3 weeks', field: value}, field_provenance={field: _scoped_proof(field)})
    parsed = vo._parse_rows([entry], {})[vo._key('HKG', 'GBR', 'tourism', 'ordinary_passport')]
    assert parsed['fields'][field] == value and parsed['field_provenance'][field]['status'] == 'reviewed'


@pytest.mark.parametrize('tamper', ['status', 'kind', 'field'])
def test_scoped_proof_must_name_review_kind_and_field(tamper):
    proof = _scoped_proof('forms')
    if tamper == 'status': proof['status'] = 'partial'
    elif tamper == 'kind': proof['verification_scope'].pop('kind')
    else: proof['verification_scope']['field'] = 'processing_time'
    entry = _entry(fields={'processing_time': 'about 3 weeks', 'forms': ['Online application']}, field_provenance={'forms': proof})
    parsed = vo._parse_rows([entry], {})[vo._key('HKG', 'GBR', 'tourism', 'ordinary_passport')]
    assert 'forms' not in parsed['fields']


@pytest.mark.parametrize('field,value', [('forms', ['x']), ('route_workflow_type', 'embassy_submission')])
def test_operator_edits_cannot_set_scoped_fields(monkeypatch, tmp_path, field, value):
    monkeypatch.setenv('ELLIS_OPERATOR_OVERRIDES', str(tmp_path / 'operator.json'))
    entry = {'route': {'nationality': 'HKG', 'destination': 'GBR', 'travel_purpose': 'tourism'}, 'source_url': GOV,
             'verified_at': '2026-09-10', 'verified_by': 'operator', 'verifier': 'human', 'note': 'operator edit',
             'fields': {field: value}}
    with pytest.raises(ValueError, match='cannot be edited'):
        vo.append_operator_entry(entry, guidance={})
    assert not (tmp_path / 'operator.json').exists()


def _residence_entry():
    return _entry(fields={'processing_time': 'about 3 weeks', 'application_channel_detail': 'File at the London mission', 'permitted_stay_days': 180},
                  field_provenance={}, applicability={'lawful_country_of_residence': 'GBR', 'fields': ['processing_time', 'application_channel_detail']})


def test_residence_applicability_is_parsed_and_enforced_by_find(monkeypatch):
    table = vo._parse_rows([_residence_entry()], {})
    key = vo._key('HKG', 'GBR', 'tourism', 'ordinary_passport')
    assert table[key]['field_applicability'] == {'processing_time': {'lawful_country_of_residence': 'GBR'},
                                                  'application_channel_detail': {'lawful_country_of_residence': 'GBR'}}
    monkeypatch.setattr(vo, '_table', lambda: table)
    same = vo.find(dict(ROUTE, lawful_country_of_residence='GBR'))
    assert set(same['fields']) == {'processing_time', 'application_channel_detail', 'permitted_stay_days'}
    unknown = vo.find({k: v for k, v in ROUTE.items() if k != 'lawful_country_of_residence'})
    assert set(unknown['fields']) == set(same['fields'])
    other = vo.find(dict(ROUTE, lawful_country_of_residence='CAN'))
    assert set(other['fields']) == {'permitted_stay_days'} and other['inapplicable_fields'] == ['application_channel_detail', 'processing_time']
    assert 'processing_time' not in other['field_provenance']
    assert table[key]['fields']['processing_time'] == 'about 3 weeks'  # the table itself is never mutated


def test_all_fields_inapplicable_means_no_override(monkeypatch):
    entry = _residence_entry(); entry['fields'].pop('permitted_stay_days'); entry['applicability']['fields'] = ['processing_time', 'application_channel_detail']
    table = vo._parse_rows([entry], {})
    monkeypatch.setattr(vo, '_table', lambda: table)
    assert vo.find(dict(ROUTE, lawful_country_of_residence='CAN')) is None


@pytest.mark.parametrize('bad', [{'lawful_country_of_residence': 'gb', 'fields': ['processing_time']}, {'lawful_country_of_residence': 'GBR', 'fields': []},
                                 {'lawful_country_of_residence': 'GBR', 'fields': ['not_a_field']}, {'lawful_country_of_residence': 'GBR'}, 'GBR'])
def test_malformed_applicability_skips_the_entry(bad):
    entry = _residence_entry(); entry['applicability'] = bad
    assert vo._parse_rows([entry], {}) == {}


def test_usa_china_overlay_scopes_its_procedure_to_us_residents(monkeypatch):
    overlay = json.loads((D / 'reviewed_usa_china_overlay_20260910.json').read_text())
    entry = overlay['entries'][0]
    assert entry['applicability']['lawful_country_of_residence'] == 'USA'
    assert {'application_channel', 'submission_process', 'government_fee', 'forms', 'route_workflow_type'} <= set(entry['applicability']['fields'])
    for nationwide in ('disposition', 'requirement_detail', 'permitted_stay', 'arrival_card', 'exceptions', 'entry_requirements'):
        assert nationwide not in entry['applicability']['fields']
    table = vo._parse_rows([entry], {})
    monkeypatch.setattr(vo, '_table', lambda: table)
    route = {'passport_nationality': 'USA', 'destination_country': 'CHN', 'travel_purpose': 'tourism', 'travel_document_type': 'ordinary_passport'}
    us = vo.find(dict(route, lawful_country_of_residence='USA'))
    assert us['fields']['application_channel'] == 'online_portal' and us['fields']['forms'] is not None
    abroad = vo.find(dict(route, lawful_country_of_residence='CAN'))
    assert 'application_channel' not in abroad['fields'] and 'submission_process' not in abroad['fields'] and 'forms' not in abroad['fields']
    assert abroad['fields']['disposition'] == 'VISA_REQUIRED' and abroad['fields']['arrival_card'] == us['fields']['arrival_card']
    from app.visa_snapshot.reviewed_usa_china_workflow import steps
    merged_abroad, prov_abroad = vo.merge_verified_fields({'disposition': 'VISA_REQUIRED', 'requirement_detail': 'paper_visa'}, abroad['fields'], source_url=abroad['source_url'])
    assert steps(merged_abroad, dict(route, lawful_country_of_residence='CAN'), dict(fields=sorted(prov_abroad), field_provenance=abroad['field_provenance'])) == []
