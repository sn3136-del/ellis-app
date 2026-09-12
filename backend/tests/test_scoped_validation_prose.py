"""Scoped source-backed prose must not become an unrelated publication conflict."""
from copy import deepcopy
import pytest
from app.visa_snapshot.kimi_primary import validate_answer, _named_stay_limits

# Minimal actual served values captured 12 September 2026 after source repairs.
LIVE = {'IDN': {'disposition': 'CONDITIONAL',
         'application_channel': 'online_portal',
         'confidence': 'high',
         'visa_products': [{'type': 'E-passport visa exemption registration (JAVES or sticker)',
                            'disposition': 'CONDITIONAL',
                            'requirement_detail': 'conditional_visa_free',
                            'max_stay_days': 15,
                            'notes': 'For Indonesian ordinary ICAO e-passport holders registered '
                                     'before departure. JAVES online registration is for residents '
                                     'of Indonesia visiting by air; present the live notice on a '
                                     'device, not a screenshot or printout. Government '
                                     'registration is free; JVAC service charges may apply to '
                                     'paper registration.'},
                           {'type': 'Single-entry tourism visa (applications in Indonesia)',
                            'disposition': 'VISA_REQUIRED',
                            'requirement_detail': 'evisa',
                            'max_stay_days': 90,
                            'notes': 'For non-exempt travelers or stays exceeding15days. Residents '
                                     'of Indonesia apply through an accredited agency/JVAC; direct '
                                     'self-application on JAPAN eVISA is unavailable for Indonesia '
                                     'residents. Published Indonesia government tariff '
                                     'from1July2026 is IDR1,650,000 plus any visa-center service '
                                     'charge. Other countries of application use that mission’s '
                                     'currency and tariff.'},
                           {'type': 'Multiple-entry short-term tourist visa (Jakarta application '
                                    'tariff)',
                            'disposition': 'VISA_REQUIRED',
                            'requirement_detail': 'paper_visa',
                            'max_stay_days': 30,
                            'notes': 'Indonesian ordinary MRP/e-passport applicants must meet the '
                                     'published travel-history, financial-capacity or qualifying '
                                     'family criteria. Stay is15or30days and validity1,3or5years '
                                     'as issued; multiple entry is not guaranteed. Jakarta '
                                     'publishes IDR3,330,000 from1July2026; Medan and Makassar '
                                     'publish IDR3,300,000. Follow the responsible mission’s own '
                                     'tariff and service charges. These figures are not a '
                                     'worldwide fee.'}],
         'application_channel_detail': 'Eligible Indonesian e-passport holders residing in '
                                       'Indonesia and arriving by air can register on JAVES before '
                                       'departure; display the live registration notice on a '
                                       'device. Paper visa-waiver registration is also available '
                                       'through JVAC. Travelers who do not meet exemption '
                                       'requirements need a visa. For Indonesia residents the '
                                       'tourism eVISA application goes through an accredited '
                                       'agency/JVAC, not direct self-application.',
         'requirement_detail': 'conditional_visa_free'},
 'PHL': {'disposition': 'VISA_REQUIRED',
         'application_channel': 'visa_center',
         'confidence': 'medium',
         'visa_products': [{'type': 'C-3-9 Single-entry tourist (59days or less)',
                            'disposition': 'VISA_REQUIRED',
                            'requirement_detail': 'paper_visa',
                            'max_stay_days': 59,
                            'notes': 'For Filipino nationals applying in the Philippines for a '
                                     'stay of59days or less, the government visa fee is waived. '
                                     'The separate KVAC service fee remains payable; the issued '
                                     'visa controls the permitted stay.'},
                           {'type': 'C-3-9 Single-entry tourist (60–90days)',
                            'disposition': 'VISA_REQUIRED',
                            'requirement_detail': 'paper_visa',
                            'max_stay_days': 90,
                            'notes': 'For Filipino nationals applying in the Philippines '
                                     'requesting a stay of60–90days. The government visa fee '
                                     'isPHP2000; the separate KVAC service fee remains payable. '
                                     'The issued visa controls the permitted stay.'},
                           {'type': 'C-3-9 Multiple-entry tourist, subject to consular approval',
                            'disposition': 'VISA_REQUIRED',
                            'requirement_detail': 'paper_visa',
                            'max_stay_days': None,
                            'notes': 'A multiple-entry visa is conditional on consular approval. '
                                     'The issued visa grant determines the validity, number of '
                                     'entries and permitted stay. Confirm the government fee for '
                                     'the approved visa type with the issuing mission before '
                                     'paying; the KVAC service fee is separate. Qualifying for '
                                     'simplified financial documentation does not guarantee a '
                                     'multiple-entry visa.'}],
         'application_channel_detail': 'Tourist applications in the Philippines are lodged at '
                                       'KVAC. The current embassy checklist does not restrict all '
                                       'individual applicants to using an accredited travel '
                                       'agency; follow KVAC instructions for personal, '
                                       'representative or available mail submission.',
         'requirement_detail': 'paper_visa'}}


def conflicts(guidance):
    return validate_answer(guidance)[2]


def channel_conflicts(guidance):
    return [x for x in conflicts(guidance) if 'individuals may not file directly' in x]


def stay_conflicts(note, days=90):
    return [x for x in conflicts({'disposition':'VISA_REQUIRED', 'visa_products':[
        {'type':'Tourist visa', 'max_stay_days':days, 'notes':note}]}) if 'shorter granted stay' in x]


@pytest.mark.parametrize('route', ['IDN', 'PHL'])
def test_live_source_backed_branch_and_negation_do_not_create_false_conflicts(route):
    original=deepcopy(LIVE[route])
    clean, _, diagnostics=validate_answer(original)
    assert not [x for x in diagnostics if 'individuals may not file directly' in x or 'shorter granted stay' in x]
    for field in ['application_channel','application_channel_detail','visa_products','confidence']:
        assert clean[field]==original[field]


@pytest.mark.parametrize('suffix', [
    '. All applicants must apply through an accredited agency.',
    '; individuals cannot apply directly.',
    ', but applications must be lodged through a designated agency.',
])
def test_negated_agency_requirement_does_not_clear_a_separate_actual_denial(suffix):
    g=deepcopy(LIVE['PHL']);g['application_channel_detail']+=suffix
    assert channel_conflicts(g)


@pytest.mark.parametrize('detail', [
    'The embassy does not restrict all individual applicants to using an accredited travel agency, but all applicants must use one.',
    'The embassy does not restrict all individual applicants to using an accredited travel agency. All applicants must use one.',
    'Tourist applications must be lodged through an accredited travel agency.',
    'The embassy does not permit individual applicants to apply directly; use an accredited travel agency.',
    'The embassy does not restrict accredited travel agencies; applicants must use them.',
    'It is unclear whether individual applicants must use an accredited travel agency.',
])
def test_positive_or_uncertain_agency_claims_remain_flagged(detail):
    g=deepcopy(LIVE['PHL']);g['application_channel_detail']=detail
    assert channel_conflicts(g)


@pytest.mark.parametrize('mutation', ['no_products','missing_baseline','missing_required_sibling','different_baseline','no_online_registration','required_headline'])
def test_branch_exclusion_requires_structured_baseline_and_alternative(mutation):
    g=deepcopy(LIVE['IDN'])
    if mutation=='no_products':g['visa_products']=[]
    elif mutation=='missing_baseline':g['visa_products']=g['visa_products'][1:]
    elif mutation=='missing_required_sibling':g['visa_products']=g['visa_products'][:1]
    elif mutation=='different_baseline':g['visa_products'][0]['requirement_detail']='evisa'
    elif mutation=='no_online_registration':g['visa_products'][0]['notes']='Registration information is not available.'
    elif mutation=='required_headline':g['disposition']='VISA_REQUIRED';g['requirement_detail']='evisa'
    assert channel_conflicts(g)


@pytest.mark.parametrize('suffix', [
    ' Registration must be lodged through an accredited agency.',
    ' All applicants cannot apply directly.',
    ' The tourism eVISA application goes through an accredited agency and waiver registration must also use the agency.',
    ' The tourism eVISA application goes through an accredited agency, but all applicants must use that agency.',
])
def test_separate_product_clause_never_suppresses_a_baseline_or_mixed_scope_rule(suffix):
    g=deepcopy(LIVE['IDN']);g['application_channel_detail']+=suffix
    assert channel_conflicts(g)


@pytest.mark.parametrize('note', [
    'For non-exempt travelers or stays exceeding15days.',
    'For stays exceeding 15 days.', 'For stays over 15 days.',
    'For stays longer than 15 days.', 'For stays more than 15 days.',
    'For stays of at least 15 days.',
    'For stays of at least 15 days per entry.',
    'For stays exceeding15days per visit.',
    'A minimum of 15 days stay is required.',
])
def test_lower_bound_is_not_a_shorter_maximum_stay(note):
    assert not stay_conflicts(note)
    assert _named_stay_limits(note)==[]


@pytest.mark.parametrize('note', [
    'A stay of 15 days is granted.', 'Stay not exceeding 15 days.',
    'Stay must not exceed 15 days.', 'Stay cannot exceed 15 days.',
    'Stay is no more than 15 days.', 'Stay is not longer than 15 days.',
    '15 days per entry.', '停留15天',
    'For stays exceeding15days. The granted stay is30days.',
    'For stays exceeding15days but no more than30days.',
    'Stay is at least15days and at most30days.',
    'You may not stay more than15days.',
    'Travelers are not allowed to stay over15days.',
    'The permit does not permit stays exceeding15days.',
    'Travelers cannot be granted a stay longer than15days.',
    'Visitors must never stay over15days.',
])
def test_genuine_shorter_upper_limits_remain_conflicts(note):
    assert stay_conflicts(note)


def test_same_note_and_stay_values_are_preserved_without_promoting_confidence():
    g=deepcopy(LIVE['IDN']);g['confidence']='low'
    clean,_,_=validate_answer(g)
    assert clean['confidence']=='low'
    assert clean['visa_products']==g['visa_products']
