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
    assert tstation.field_status(row)['max_stay_duration'] == 'missing'
