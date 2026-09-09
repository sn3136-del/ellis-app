"""Policy ranges and exact source quotations survive the QC projection."""
import pytest

from app.visa_snapshot import tstation


@pytest.mark.parametrize('label', [
    '30-day e-Tourist Visa (April–June)',
    '30-day e-Tourist Visa (July–March)',
    'Child visitor (ages 6–12)',
    'Visitor fee band USD 25—50',
])
def test_qc_product_names_keep_continuous_ranges(label):
    route = {'passport_nationality': 'AUS', 'destination_country': 'IND',
             'travel_document_type': 'ordinary_passport', 'travel_purpose': 'tourism'}
    guidance = {'disposition': 'VISA_REQUIRED', 'requirement_detail': 'evisa',
                'visa_category': label, 'source_url': 'https://indianvisaonline.gov.in/',
                'visa_products': [{'type': label, 'max_stay_days': 30,
                    'fee': {'amount': 10, 'currency': 'USD'}, 'entry': 'single'}]}
    rows = tstation.records_for_route(route, guidance)
    assert rows[0]['visa_type_name'] == label
    assert rows[0]['visa_fee_amount'] == 10


def test_corroborating_source_quote_is_not_rewritten_as_prose():
    quote = 'April–June: USD 10; July–March: USD 25.\nChildren aged 6–12: separate rate.'
    source = {'url': 'https://indianvisaonline.gov.in/evisa/images/Etourist_fee_final.pdf',
              'quote': quote, 'checked_at': '2026-09-09'}
    row = tstation.records_for_route(
        {'passport_nationality': 'AUS', 'destination_country': 'IND', 'travel_purpose': 'tourism'},
        {'disposition': 'VISA_REQUIRED', 'corroborating_sources': [source]})[0]
    assert row['corroborating_sources'][0]['quote'] == quote
    assert row['corroborating_sources'][0]['checked_at'] == '2026-09-09'
