"""Personal centre filing and optional agents must not become agency-only rules."""
import pytest
from app.visa_snapshot.kimi_primary import validate_answer, _direct_visa_centre_option


def channel_conflicts(detail, channel='visa_center'):
    _, _, conflicts = validate_answer({'disposition': 'VISA_REQUIRED',
        'application_channel': channel, 'application_channel_detail': detail,
        'visa_category': 'Tourist visa', 'visa_products': [{'type': 'Tourist visa'}]})
    return [problem for problem in conflicts if 'individuals may not file directly' in problem]


@pytest.mark.parametrize('detail', [
    'After completing the France-Visas online form, Chinese ordinary passport holders must book and attend an appointment at a TLScontact visa application centre in China; the embassy/consulate does not accept direct individual walk-ins.',
    'Holders of Chinese ordinary passports lodge the C-3-4 application at the Korea Visa Application Center or through a consulate-designated agency/travel agency.',
    'Hong Kong residents holding a HKSAR Document of Identity must apply before travel, in person by appointment at the Japan Visa Application Centre in Hong Kong, through an accredited agency, or as an eVISA via the Japan Visa Application Centre website.',
    'For applications in Russia, submit at the Japan Visa Centre in person or through an authorised representative. The embassy does not accept visa applications by post.',
    'You may submit at the visa application center or through an authorized agent.',
])
def test_personal_centre_option_is_not_an_agency_only_requirement(detail):
    assert not channel_conflicts(detail)


@pytest.mark.parametrize('detail', [
    'Applications must be lodged through a designated travel agency; individuals cannot apply directly.',
    'Apply in person at the visa centre. Individuals cannot apply directly; use an authorised agent.',
    'Apply in person at the visa centre, but applications must be lodged through a designated agency.',
    'Applicants must submit at the visa centre through an authorised agent.',
    'Applicants may only apply through an accredited agency. Attend an appointment at the visa centre.',
    'The visa centre does not accept individual applications; use a designated agency.',
    'An accredited agency will attend in person at the consulate.',
    'Apply through a designated agency.',
    'You may not submit in person at the visa centre; use an authorised agent.',
    'Apply in person at the visa centre through an authorised agent only.',
    'An authorised agent may submit in person at the visa centre.',
    'You cannot book and attend an appointment at the visa centre; apply through a designated agency.',
    'Applicants are not permitted to submit at the visa centre in person; apply through an authorised agent.',
    'The embassy does not accept direct applications, and applicants cannot submit in person at the visa centre; use an authorised agent.',
    'Applicants must attend an appointment at the visa centre for biometrics. Applications are submitted exclusively through a designated agency.',
    'Applicants must attend in person at the visa centre for biometrics; only an authorised agent may lodge applications.',
    'You must apply in person at the visa centre, via an authorised agent.',
    'Book and attend an appointment at the visa centre for fingerprint collection; an authorised agent handles the application.',
    'You must apply in person at the visa centre, via a government-authorised agent.',
    'Apply in person at the visa centre. The embassy does not accept direct applications because applicants cannot submit personally; use an authorised agent.',
    'Apply in person at the visa centre. The embassy says the visa centre does not accept individual applications; use an authorised agent.',
])
def test_actual_mandatory_or_ambiguous_agency_channels_remain_blocked(detail):
    assert channel_conflicts(detail)


@pytest.mark.parametrize('detail', [
    'Applicants book and attend an appointment at the visa centre for biometrics. An authorised representative files the application.',
    'Only accredited agencies lodge applications. Applicants attend the visa centre in person.',
])
def test_other_agency_spellings_do_not_supply_a_positive_filing_option(detail):
    assert not _direct_visa_centre_option(detail)
    assert channel_conflicts(detail)


@pytest.mark.parametrize('channel', ['embassy', 'online_portal'])
def test_centre_option_does_not_clear_wrong_headline_channel(channel):
    assert channel_conflicts('Submit at the visa centre in person or through an authorised agent. '
                            'The embassy does not accept direct applications.', channel)


def test_validator_preserves_channel_and_text_without_manufacturing_evidence():
    detail = 'Submit at the visa centre in person or through an authorised representative.'
    clean, _, _ = validate_answer({'disposition': 'VISA_REQUIRED', 'application_channel': 'visa_center',
        'application_channel_detail': detail, 'confidence': 'low'})
    assert clean['application_channel'] == 'visa_center'
    assert clean['application_channel_detail'] == detail
    assert clean['confidence'] == 'low'
    assert not clean.get('source_url')
