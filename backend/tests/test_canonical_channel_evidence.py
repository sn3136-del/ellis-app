"""Current schema channel names need literal filing evidence, not mere web access."""
from copy import deepcopy
from datetime import datetime

import pytest

from app.visa_snapshot.evidence_validator import field_value_supported
from app.visa_snapshot import freshness, freshness_evidence, fetching, kimi_primary, verified_overrides as vo
from app.visa_snapshot.fetching import FetchResult


@pytest.mark.parametrize('value,quote', [
    ('online_portal', 'For e-Tourist and e-Business visa, Applicants of the eligible countries/territories may apply online minimum 4 days in advance of the date of arrival.'),
    ('online_portal', 'In order to apply for a unified e-visa, it is necessary to submit an application from your personal account on the specialized website of the Ministry of Foreign Affairs of Russia or through the Russian Foreign Ministry'),
    ('online_portal', 'Ans- No, https://indianvisaonline.gov.in/evisa/ is only the official website to apply for the e-Visa Services.'),
    ('online_portal', 'You must submit your visa application online.'),
    ('online_portal', 'Applications must be submitted online.'),
    ('visa_center', 'Applicants may apply directly at the visa application centre.'),
    ('visa_center', 'Submit your visa application at the visa application center.'),
    ('visa_center', 'The visa application centre accepts applications.'),
    ('not_required', 'No application is required.'),
])
def test_current_schema_names_recognize_only_explicit_application_instructions(value, quote):
    assert value in kimi_primary.APPLICATION_CHANNELS
    assert field_value_supported('application_channel', value, quote)


@pytest.mark.parametrize('value,quote', [
    ('online_portal', 'You cannot apply online.'),
    ('online_portal', 'Applying online is not permitted.'),
    ('online_portal', 'The website to apply for visas is unavailable.'),
    ('online_portal', 'Applicants are not eligible to apply online.'),
    ('online_portal', 'Do not use this website to apply for a visa.'),
    ('online_portal', 'Apply online for an appointment.'),
    ('online_portal', 'Applicants are ineligible to submit applications online.'),
    ('online_portal', 'Use this portal to apply for a biometrics appointment.'),
    ('online_portal', 'You may apply online only if you hold a residence permit.'),
    ('online_portal', 'You may apply online provided that you hold a residence permit.'),
    ('online_portal', 'Online applications are not accepted. Apply online for appointment tracking.'),
    ('online_portal', 'Visa fees can be paid online.'),
    ('online_portal', 'You may check your application status online.'),
    ('online_portal', 'Submit your application in person; pay the fee online.'),
    ('online_portal', 'Submit your application in person, then pay the fee online.'),
    ('online_portal', 'You must submit your visa application online, then print it and submit it to the embassy.'),
    ('online_portal', 'Complete your visa application online.'),
    ('online_portal', 'Applications must be lodged through an authorized agency; applicants may apply online for an appointment.'),
    ('online_portal', 'Authorized agents may apply online for their clients.'),
    ('online_portal', 'Log in or create an ImmiAccount.'),
    ('online_portal', 'China (including Hong Kong and Macau)'),
    ('visa_center', 'Applicants cannot apply directly at the visa application centre.'),
    ('visa_center', 'The visa application centre does not accept applications.'),
    ('visa_center', 'Applicants are not eligible to apply at the visa application centre.'),
    ('visa_center', 'Apply for an appointment at the visa application centre.'),
    ('visa_center', 'Agents submit applications at the visa application centre.'),
    ('visa_center', 'Applications must be submitted through an authorized agent at the visa application centre.'),
    ('visa_center', 'Submit your fingerprints at the visa application centre.'),
    ('visa_center', 'A visa application centre is located in the city.'),
    ('authorized_agent', 'Applicants may apply directly or through an authorized agent.'),
    ('authorized_agent', 'Authorized agents may assist.'),
    ('authorized_agent', 'Do not use an authorized agent.'),
    ('not_required', 'No visa is required, but an ETA application is required.'),
    ('not_required', 'No application is required for visa cancellation.'),
    ('not_required', 'No application is required to pay the visa fee.'),
    ('not_required', 'No application is required unless you intend to work.'),
    ('not_required', 'No application is required for children under 12.'),
    ('not_required', 'No application is required. Adults must apply before travel.'),
])
def test_canonical_channel_does_not_borrow_tracking_payment_agency_or_conditional_proof(value, quote):
    assert not field_value_supported('application_channel', value, quote)


@pytest.mark.parametrize('value,quote', [
    ('online', 'Apply online'), ('visa_application_centre', 'Visa application centre'),
    ('none', 'No application is required'), ('authorised_agent', 'Authorized agent'),
    ('embassy', 'Apply at the embassy'), ('on_arrival', 'Obtain the visa on arrival'),
])
def test_existing_legacy_names_remain_readable_without_normalizing_data(value, quote):
    assert field_value_supported('application_channel', value, quote)


@pytest.mark.parametrize('bad', [{'method': 'online_portal'}, ['online_portal'], 'made_up_portal'])
def test_malformed_or_unknown_channel_is_not_verified(bad):
    assert not field_value_supported('application_channel', bad, 'You may apply online.')


def test_fresh_quote_proposals_keep_canonical_value_and_require_literal_capture():
    quote = 'Submit your visa application online.'
    answer = {'corrected_fields': {'application_channel': 'online_portal'},
              'evidence': {'application_channel': quote}}
    original = deepcopy(answer)
    fields, _, missing = freshness._quoted_proposals(answer, quote, {'passport_nationality': 'IND', 'destination_country': 'RUS', 'travel_purpose': 'tourism'})
    assert fields == {'application_channel': 'online_portal'} and missing == []
    fields, _, missing = freshness._quoted_proposals(answer, 'Official page unavailable')
    assert fields == {} and missing == ['application_channel'] and answer == original


def test_stored_review_is_only_reused_after_its_literal_page_has_been_read():
    url = 'https://evisa.kdmid.ru/'
    quote = 'You must submit your visa application online.'
    proof = {'application_channel': {'source_url': url, 'quote': quote, 'verified_at': '2020-01-01'}}
    route = {'passport_nationality': 'IND', 'destination_country': 'RUS', 'travel_purpose': 'tourism'}
    before = deepcopy(proof)
    assert freshness_evidence.reviewed_field_quote('application_channel', 'online_portal', proof, {'url': url, 'text': quote}, {}, route)
    assert freshness_evidence.reviewed_field_quote('application_channel', 'online_portal', proof, {'url': url, 'text': 'Source unavailable'}, {}, route) is None
    assert proof == before


def test_one_newly_matched_channel_never_renews_unverified_facts_or_clears_disputes(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.models import Base
    from app.visa_snapshot.models import KimiRouteGuidanceCache, DatabaseIssueReport
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    empty = tmp_path / 'overrides.json'; empty.write_text('[]')
    monkeypatch.setattr(vo, 'OVERRIDES', empty)
    monkeypatch.setattr(vo, '_reviewed_overlay_paths', lambda: [])
    monkeypatch.setattr(vo, 'operator_overrides_path', lambda: tmp_path / 'absent-operators.json')
    vo.reload()
    route = {'passport_nationality': 'CHN', 'lawful_country_of_residence': 'CHN',
             'destination_country': 'JPN', 'travel_purpose': 'tourism', 'travel_document_type': 'ordinary_passport'}
    url = 'https://www.mofa.go.jp/j_info/visit/visa/index.html'
    application = 'You must submit your visa application online.'
    # Official-shaped injected text tests the mechanism, not Japan policy.
    text = 'Chinese nationals must obtain a visa for tourism in Japan. ' + application
    guidance = {'disposition': 'VISA_REQUIRED', 'requirement_detail': 'evisa',
                'visa_category': 'Tourist e-visa', 'application_channel': 'online_portal',
                'required_documents': ['Valid passport'], 'permitted_stay': '30 days',
                'government_fee': {'amount': 50, 'currency': 'USD'}, 'processing_time': '5 working days',
                'confidence': 'high', 'source_url': url,
                'visa_products': [{'type': 'Tourist e-visa', 'fee': {'amount': 50, 'currency': 'USD'}, 'max_stay_days': 30}]}
    old = datetime(2020, 1, 1)
    fetching.set_fetcher(lambda url, **kw: FetchResult(requested_url=url, ok=True, final_url=url,
        final_hostname='www.mofa.go.jp', http_status=200, content_text=text, content_hash='injected-channel-page',
        retrieved_at='2026-09-10T00:00:00Z'))
    freshness.set_provider(lambda system, user: {'consistent': True, 'page_relevant': True,
        'page_is_nationality_specific': True, 'corrected_fields': {}, 'evidence': {'application_channel': application}})
    try:
        with Session(engine) as db:
            row = KimiRouteGuidanceCache(cache_key=kimi_primary.cache_key(route), route=route,
                guidance=deepcopy(guidance), status='KIMI_PRIMARY', fresh_until=old)
            issue = DatabaseIssueReport(org_id='platform', cache_key=row.cache_key, route=route,
                field='passport_validity', note='Independent unresolved passport validity conflict',
                reported_by='freshness_monitor', status='open', proposal={'fields': {'passport_validity': {'page_says': 'Different rule'}}})
            db.add_all([row, issue]); db.commit()
            result = freshness.recheck_route(db, route)
            db.refresh(row); db.refresh(issue)
            check = row.verification['grounded_check']
            assert result['outcome'] == 'checked'
            assert 'application_channel' in check['verified_fields']
            assert 'government_fee' in check['unverified_fields']
            assert check['renewed'] is False and row.fresh_until == old
            assert row.guidance == guidance and issue.status == 'open'
    finally:
        freshness.set_provider(None); fetching.set_fetcher(None); vo.reload(); engine.dispose()
