"""Explicit negative exemptions must not pass the shared source validator."""
import pytest
from app.visa_snapshot.evidence_validator import supports_disposition,route_supporting_excerpt,validate_disposition
from scripts.convert_reviewed_general_batch import _decision_supported

@pytest.mark.parametrize('text',[
 'American citizens are not visa-free.',
 'American citizens are not visa-exempt.',
 'American citizens are not exempt from the visa requirement.',
 'American citizens are not eligible for visa-free entry.',
 'American citizens are never entitled to visa-free travel.',
 'Visa-free entry is not available to American citizens.',
 'Visa-exempt access is not permitted for American citizens.',
 'Visa-free travel is not allowed for American citizens.',
 'American citizens are not visa-free; an eVisa is required.',
 'Hong Kong passport holders are not visa-free.',
 'Indonesian citizens are not visa-exempt.',
])
def test_explicit_negative_is_not_exemption_evidence(text):
 nat='HKG' if 'Hong Kong' in text else 'IDN' if 'Indonesian' in text else 'USA'
 assert not supports_disposition(text,'VISA_EXEMPT',nationality=nat)
 assert not _decision_supported('VISA_EXEMPT',[text],nat)
 route={'passport_nationality':nat,'destination_country':'MYS','travel_purpose':'tourism','travel_document_type':'ordinary_passport'}
 assert not route_supporting_excerpt(text,'VISA_EXEMPT',route)
 url='https://www.kln.gov.my/web/usa_los-angeles/requirement_foreigner'
 assert not validate_disposition(route,'VISA_EXEMPT',[url],[{'url':url,'text':text}])['ok']

@pytest.mark.parametrize('text',[
 'American citizens do not require a visa to visit Malaysia for business or social reasons if their stay in Malaysia is 90 days or less.',
 'American citizens are visa-free for tourism visits of up to 90 days.',
 'No visa is required for American citizens visiting Malaysia for tourism.',
 'Visa-free travel is not restricted for American citizens.',
])
def test_positive_official_rule_and_nonflipping_negation_remain_supported(text):
 assert supports_disposition(text,'VISA_EXEMPT',nationality='USA')
 assert _decision_supported('VISA_EXEMPT',[text],'USA')
 assert not supports_disposition(text,'VISA_EXEMPT',nationality='CAN')

@pytest.mark.parametrize('text',[
 'American citizens require a visa for Malaysia.',
 'American citizens must apply for a visa before travel.',
 'Information for American citizens. Canadian citizens may enter without a visa.',
])
def test_plural_alias_cannot_borrow_another_rule(text):
 assert not supports_disposition(text,'VISA_EXEMPT',nationality='USA')
 assert not _decision_supported('VISA_EXEMPT',[text],'USA')
