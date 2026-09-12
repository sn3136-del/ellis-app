"""A literal quotation must support the field's meaning, not just a token."""
import hashlib

import pytest

from scripts.convert_reviewed_general_batch import build_manifest, convert
from tests.test_reviewed_general_batch import batch, layer, proof


def convert_field(field, value, quote):
    candidate = batch()
    source = candidate['sources'][0]
    source['text'] += ' ' + quote
    source['sha256'] = hashlib.sha256(source['text'].encode()).hexdigest()
    product = candidate['rows'][0]['products'][0]
    product['product'][field] = value
    product['proofs'][field] = proof(quote)
    overlay, reports = convert(build_manifest(candidate, [layer()]), [layer()])
    assert len(overlay['entries']) == 1
    return overlay['entries'][0]['fields']['visa_products'][0], reports


@pytest.mark.parametrize('quote', [
    'No fee information is published on this page.',
    'The official site publishes no issuance fee.',
    'The visa fee is not publicly available.',
    'Free help is available for completing this application.',
    'Free help is available and the visa fee is USD 40.',
    'Visa fees are not waived.',
    'Visa fees are waived. The fee amount is unverified.',
])
def test_unpublished_fees_free_help_and_negated_waivers_do_not_create_zero(quote):
    product, reports = convert_field('fee', {'amount': 0, 'currency': 'USD'}, quote)
    assert (product.get('fee') or {}).get('amount') != 0
    assert 'fee' not in (product.get('field_provenance') or {})
    assert any('zero fee' in reason for reason in reports[0]['dropped'])


def test_an_expired_waiver_does_not_support_zero():
    from scripts.convert_reviewed_general_batch import _zero_fee_supported
    assert not _zero_fee_supported('The visa fee waiver has expired.')


@pytest.mark.parametrize('quote', [
    'The tourist visa fee is waived.',
    'No visa fee is charged.',
    'The tourist visa is free of charge.',
    'The visa fee is USD 0.',
    'Fee: waived.',
])
def test_explicit_visa_fee_waivers_and_numeric_zero_remain_supported(quote):
    product, reports = convert_field('fee', {'amount': 0, 'currency': 'USD'}, quote)
    assert product['fee']['amount'] == 0
    assert product['field_provenance']['fee']['quote'] == quote
    assert not [reason for reason in reports[0]['dropped'] if 'scope note:' not in reason]


@pytest.mark.parametrize('value,quote', [
    ('90 years', 'Vietnam E-visa is valid for maximum of 90 days, single or multiple entry.'),
    ('3 years', 'Your application will be processed in 3 working days.'),
    ('3 days', 'Your application will be processed in 3 working days.'),
    ('6 months', 'Your passport must be valid for 6 months.'),
    ('30 days', 'The visa is valid for 90 days and the permitted stay is 30 days.'),
    ('90 days (5-year options are also available)', 'The visa is valid for 90 days.'),
    ('90 days', 'Visa Validity\nProcessing time\n90 days'),
    ('90 days', 'Travel insurance must be valid for 90 days.'),
    ('90 days', 'The travel insurance policy is valid for 90 days.'),
    ('90 days', 'Your residence permit must be valid for 90 days.'),
    ('90 days', 'Visa validity requires travel insurance valid for 90 days.'),
    ('90 days', 'Travel insurance is valid for 90 days from issuance.'),
    ('90 days', 'Your passport remains valid for 90 days from the date of issue.'),
    ('90 days', 'The residence permit lasts 90 days from issue.'),
])
def test_validity_needs_its_own_amount_unit_and_subject(value, quote):
    product, reports = convert_field('validity', value, quote)
    # Rejection can preserve an identical historical value, but it must not
    # attach this unrelated quote as new verification of that value.
    assert 'validity' not in (product.get('field_provenance') or {})
    assert any('duration and unit' in reason for reason in reports[0]['dropped'])


@pytest.mark.parametrize('value,quote', [
    ('90 days', 'Vietnam E-visa is valid for maximum of 90 days, single or multiple entry.'),
    ('120 days from the date of issue', 'The validity period of a unified electronic visa is 120 days from the date of issue.'),
    ('2 years', 'This visa lasts 2, 5 or 10 years. You can stay for a maximum of 6 months on each visit.'),
    ('Five years from the date of grant of ETA', 'Five years from the date of grant of ETA.'),
    ('90 days from issuance', 'Masa berlaku visa adalah 90 hari sejak diterbitkan.'),
    ('90 days to use', 'Visa Validity\n90 day'),
    ('90 days', 'Travel insurance is valid for 30 days while the visa is valid for 90 days.'),
    ('90 days', 'A passport valid for 6 months is required. The visa is valid for 90 days.'),
    ('Up to 3 months', 'Обыкновенная туристическая виза может быть однократной или двукратной на срок до 3 месяцев либо многократной на срок до 6 месяцев'),
])
def test_supported_validity_durations_are_preserved(value, quote):
    product, reports = convert_field('validity', value, quote)
    assert product['validity'] == value
    assert product['field_provenance']['validity']['quote'] == quote
    assert not [reason for reason in reports[0]['dropped'] if 'scope note:' not in reason]


@pytest.mark.parametrize('note', [
    'The official page grants a free five-year visa to all Hong Kong applicants without documentation.',
    'The official page states "Holders of a Hong Kong SAR passport must obtain a visa before travel." and grants a free five-year visa.',
])
def test_a_scope_note_cannot_publish_an_unquoted_factual_claim(note):
    candidate = batch()
    candidate['rows'][0]['verdict']['proof']['scope_note'] = note
    overlay, reports = convert(build_manifest(candidate, [layer()]), [layer()])
    served_note = overlay['entries'][0]['field_provenance']['disposition']['note']
    assert served_note == 'Ordinary passport, tourism.'
    assert any('scope note:' in reason for reason in reports[0]['dropped'])


def test_literal_cited_scope_note_remains_available():
    candidate = batch()
    note = candidate['rows'][0]['verdict']['proof']['evidence'][0]['quote']
    candidate['rows'][0]['verdict']['proof']['scope_note'] = note
    overlay, _ = convert(build_manifest(candidate, [layer()]), [layer()])
    assert overlay['entries'][0]['field_provenance']['disposition']['note'] == note
