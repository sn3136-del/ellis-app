"""A stay's day count is distinct from validity, extensions and rolling windows."""
import pytest
from app.visa_snapshot import tstation


@pytest.mark.parametrize('text,days', [
    ('up to 90 days. ETA-IL valid 2 years or until passport expiry', 90),
    ('31 days issued free on arrival, extendable up to six months', 31),
    ('120 days (4 months) on a tourist visa obtainable on arrival for exempt nationalities; the separate 30-day visitor visa is the wrong product for tourism', 120),
    ('up to 90 days during 6 months from the date of first entry', 90),
    ('up to 90 days in any 180-day period (Odluka o vizama: 90 days within six months from first entry)', 90),
    ("Visitor's visa issued on arrival for 31 days, free of charge, extendable up to six months", 31),
])
@pytest.mark.parametrize('numeric_field_present', [True, False])
def test_explicit_day_stay_is_not_erased_by_other_calendar_measure(text, days, numeric_field_present):
    row = {}
    tstation._set_stay(row, text, days if numeric_field_present else None)
    assert (row['max_stay_duration'], row['max_stay_unit']) == (days, 'Day')
    assert row['max_stay_text'] == text
    assert '_max_stay_representation_reason' not in row


@pytest.mark.parametrize('text,days', [
    ('Up to 6 months', 180),
    ('Usually six calendar months, decided on arrival', 180),
    ('Up to 1 year', 365),
    ('3 months or 90 days depending on the permit issued', 90),
    ('90 days or 6 months depending on the permit issued', 90),
    ('Up to 90 days for short-stay study, or duration of studies up to 1 year for long-stay student visa', 90),
    ('Up to 90 days for short-stay study, or duration of studies up to 1 year for long-stay student visa', 365),
    ('90 days for one visa type; stays up to 6 months for the other', 90),
    ('90 days for one visa type or 30 days for the other; visa valid 1 year', 90),
    ('체류기간 90일 이하, 유효기간 3개월의 단수사증 (stay up to 90 days, single entry valid 3 months), a double-entry option allows 30 days per', 30),
    ('up to 90 days. ETA-IL valid 2 years or until passport expiry', 180),
])
def test_calendar_stays_and_ambiguous_product_scopes_remain_unconverted(text, days):
    row = {}
    tstation._set_stay(row, text, days)
    assert (row['max_stay_duration'], row['max_stay_unit']) == (None, None)
    assert row['max_stay_text'] == text


def test_eta_record_keeps_ninety_day_stay_beside_two_year_validity():
    route = {'passport_nationality': 'HKG', 'destination_country': 'ISR',
             'travel_purpose': 'tourism', 'travel_document_type': 'ordinary_passport'}
    guidance = {'disposition': 'ELECTRONIC_AUTHORIZATION_REQUIRED',
                'requirement_detail': 'eta_electronic_authorization',
                'permitted_stay': 'up to 90 days. ETA-IL valid 2 years or until passport expiry',
                'permitted_stay_days': 90,
                'visa_products': [{'type': 'ETA-IL', 'validity': '2 years', 'max_stay_days': 90,
                                   'entry': 'multiple', 'fee': {'amount': 25, 'currency': 'ILS'}}]}
    record = tstation.records_for_route(route, guidance)[0]
    assert (record['max_stay_duration'], record['max_stay_unit']) == (90, 'Day')
    assert (record['validity_duration'], record['validity_unit']) == (2, 'Year')
    assert tstation.field_status(record)['max_stay_duration'] == 'filled'
    assert record['confidence_level'] == 'Low'  # formatting is not new source evidence
