"""The workbook shows the two documented labels where the record surface
records a documented blank, and a filled cell verbatim."""
import asyncio
import io

from openpyxl import load_workbook

from app import main
from app.visa_snapshot import tstation


def _workbook(rows, monkeypatch):
    monkeypatch.setattr(main, '_tstation_rows', lambda *a, **kw: rows)
    monkeypatch.setattr(main, 'require_quality_control', lambda p: None)
    response = main.travel_database_export(db=None, p=None)

    async def consume():
        return b''.join([chunk async for chunk in response.body_iterator])

    return load_workbook(io.BytesIO(asyncio.run(consume())), read_only=True)


def test_export_labels_documented_blanks_and_keeps_filled_cells(monkeypatch):
    # A visa-free record: the validity cells cannot apply, the destination
    # was checked for a policy end date and does not publish one, and the
    # stay is a filled value.
    row = {'travel_document_type': 'ordinary_passport', 'travel_document_country': 'JPN',
           'destination_country': 'KOR', 'travel_purpose': 'tourism',
           'visa_requirement': 'Visa-free', 'visa_requirement_detail': 'Unconditional Visa-free',
           'visa_type_name': 'No visa needed', 'max_stay_duration': 90, 'max_stay_unit': 'Day',
           'visa_fee_amount': 0, 'required_documents': 'Valid passport',
           'data_source': 'Operator', 'source_url': 'https://www.mofa.go.kr/',
           'collected_at': '2026-09-09', 'confidence_level': 'High',
           '_unpublished': ['info_validity']}
    statuses = tstation.field_status(row)
    assert statuses['info_validity'] == 'not-published'
    assert statuses['application_method'] == 'not-applicable'
    assert statuses['max_stay_duration'] == 'filled'
    workbook = _workbook([row], monkeypatch)
    header, record = list(workbook['Data'].values)[:2]
    assert header == tstation.FIELD_ORDER
    cell = dict(zip(header, record, strict=True))
    assert cell['info_validity'] == 'Not publicly available'
    assert cell['application_method'] == 'Not applicable'
    assert cell['processing_min_days'] == 'Not applicable'
    assert cell['max_stay_duration'] == 90 and cell['max_stay_unit'] == 'Day'
    assert cell['source_url'] == 'https://www.mofa.go.kr/' and cell['visa_fee_amount'] == 0
    # A gap is never blank and never invented: the owner's two labels only.
    assert cell['consulate_district'] == {'not-applicable': 'Not applicable', 'optional-empty': 'Not publicly available'}[statuses['consulate_district']]
    assert cell['entry_requirements'] == 'Not publicly available' and statuses['entry_requirements'] == 'optional-empty'
    workbook.close()


def test_export_values_match_the_record_surface_verdicts():
    row = {'visa_requirement': 'Visa Required in Advance', 'visa_type_name': 'Tourist visa',
           'visa_fee_amount': None, '_unpublished': ['visa_fee_amount', 'visa_fee_currency']}
    values = dict(zip(tstation.FIELD_ORDER, tstation.export_values(row), strict=True))
    assert values['visa_fee_amount'] == values['visa_fee_currency'] == tstation.NOT_PUBLICLY_AVAILABLE
    assert values['visa_type_name'] == 'Tourist visa'
    assert values['validity_duration'] == tstation.NOT_PUBLICLY_AVAILABLE  # a gap reads as the label, and stays a gap for grading
    assert tstation.field_status(row)['validity_duration'] == 'missing'


def test_a_stay_stated_in_words_is_exported_verbatim_not_labelled():
    row = {'visa_requirement': 'Visa Required in Advance', 'visa_type_name': 'Tourist visa',
           'max_stay_text': 'Stay is determined by the e-Pass issued on arrival'}
    values = dict(zip(tstation.FIELD_ORDER, tstation.export_values(row), strict=True))
    assert values['max_stay_duration'] == 'Stay is determined by the e-Pass issued on arrival'
    assert values['max_stay_unit'] == tstation.NOT_APPLICABLE
    # The wording is the value the record holds, so it is filled for grading
    # too, and the unit cell is the record's own "Not applicable" verdict.
    status = tstation.field_status(row)
    assert status['max_stay_duration'] == 'filled'
    assert status['max_stay_unit'] == 'not-applicable'
    assert 'max_stay' not in [f for f, v in status.items() if v == 'missing']


def test_a_stay_in_words_does_not_count_as_a_gap_for_completeness():
    row = {'visa_requirement': 'Visa Required in Advance', 'visa_requirement_detail': 'eVisa',
           'visa_type_name': 'Standard Visitor', 'validity_duration': 6, 'validity_unit': 'Month',
           'entries': 'Multiple', 'visa_fee_amount': 135, 'visa_fee_currency': 'GBP',
           'application_method': 'Online Application', 'required_documents': 'Passport',
           'consulate_district': 'Any', 'entry_requirements': 'x', 'special_conditions': 'y',
           'data_source': 'Ellis AI official-source field review', 'source_url': 'https://www.gov.uk/standard-visitor',
           'collected_at': '2026-09-11', 'info_validity': '2027-09-11', 'confidence_level': 'High',
           'travel_document_type': 'ordinary_passport', 'travel_document_country': 'RUS',
           'destination_country': 'GBR', 'travel_purpose': 'tourism',
           'max_stay_duration': None, 'max_stay_unit': None,
           'max_stay_text': 'Up to 6 calendar months per visit'}
    assert tstation.completeness(row) == 1.0
    numeric = dict(row, max_stay_duration=180, max_stay_unit='Day', max_stay_text=None)
    assert tstation.field_status(numeric)['max_stay_duration'] == 'filled'
    assert tstation.field_status(numeric)['max_stay_unit'] == 'filled'
    values = dict(zip(tstation.FIELD_ORDER, tstation.export_values(numeric), strict=True))
    assert values['max_stay_duration'] == 180 and values['max_stay_unit'] == 'Day'


def test_a_validity_stated_in_words_is_a_filled_value_with_no_unit():
    row = {'visa_requirement': 'Visa Required in Advance', 'visa_requirement_detail': 'Paper Visa',
           'visa_type_name': 'Ordinary tourist visa', 'validity_duration': None, 'validity_unit': None,
           'validity_text': 'Up to 3 months for a single or double entry visa, up to 6 months for a multiple entry visa'}
    status = tstation.field_status(row)
    assert status['validity_duration'] == 'filled' and status['validity_unit'] == 'not-applicable'
    values = dict(zip(tstation.FIELD_ORDER, tstation.export_values(row), strict=True))
    assert values['validity_duration'] == row['validity_text']
    assert values['validity_unit'] == tstation.NOT_APPLICABLE
    # Wording that only points elsewhere is not a value of its own.
    for pointer in ('As above', 'Same as the single-entry visa', 'See notes below', 'n/a'):
        gap = dict(row, validity_text=pointer)
        assert tstation.field_status(gap)['validity_duration'] == 'missing'
        assert dict(zip(tstation.FIELD_ORDER, tstation.export_values(gap), strict=True))['validity_duration'] == tstation.NOT_PUBLICLY_AVAILABLE
    # A numeric validity is untouched.
    numeric = dict(row, validity_duration=6, validity_unit='Month', validity_text=None)
    assert tstation.field_status(numeric)['validity_unit'] == 'filled'
