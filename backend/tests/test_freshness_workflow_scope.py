"""A real consular-footer regression plus synthetic policy-change boundaries."""
from copy import deepcopy
from datetime import datetime
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.models import Base
from app.visa_snapshot import fetching, freshness, freshness_evidence as proof, kimi_primary, verified_overrides
from app.visa_snapshot.fetching import FetchResult
from app.visa_snapshot.models import DatabaseIssueReport, KimiRouteGuidanceCache

URL = 'https://vietnamembassy.org.uk/consular-services/visa/ii-tourist-visa/'
FOOTER = ('Consular/Visa Section\nOpening hours: 09:30 - 12:30 (morning only), '
          'Monday, Tuesday, Wednesday, Thursday and Friday (Appointment required)')
ROUTE = {'passport_nationality': 'GBR', 'destination_country': 'VNM',
         'travel_document_type': 'ordinary_passport', 'travel_purpose': 'tourism'}
EXEMPT = {'disposition': 'VISA_EXEMPT', 'application_channel': 'not_required',
          'appointment_required': False, 'source_url': URL, 'confidence': 'high'}
RULE = 'British citizens are visa exempt for tourism in Vietnam.'

@pytest.mark.parametrize('quote', [
    FOOTER, 'Appointment required.',
    'Visa-free entry for British citizens. Consular visa appointments are required.',
    'Visa-free entry for British citizens\nConsular appointments required.',
    'Visa exemption certificate applicants must book an appointment.',
    'Visa-free tourists applying for an extension must book an appointment.',
    'Unlike visa-free visitors, tourist visa applicants must book an appointment.',
    'Visa-free visitors choosing a consular visa must book an appointment.',
    'Visa-free visitors applying for a visa must book an appointment.',
    'Visa-free visitors using an optional service must book an appointment.',
    'Visa-free visitors requesting consular services must book an appointment.',
    'Visa-free visitors seeking document legalization must book an appointment.',
    'Visa-free visitors visiting a museum must book an appointment.',
    'Visa-free visitors requesting consular services before travel must book an appointment.',
    'Visa-free visitors seeking document legalization before entry must book an appointment.',
    'Visa-free visitors must book an appointment before travel for a museum visit.',
    'For a museum visit, visa-free visitors must book an appointment before travel.',
    'French citizens travelling visa-free must book an appointment.',
    'Visa-exempt diplomatic passport holders must book an appointment.',
    'Visa-exempt visitors with a work visa must book an appointment.',
])
def test_other_application_scope_cannot_become_default_appointment_proof(quote):
    assert not proof.field_workflow_matches('appointment_required', quote, EXEMPT, ROUTE)

@pytest.mark.parametrize('quote', [
    'British citizens travelling visa-free must book an appointment before travel.',
    'Visa-exempt visitors must book an appointment before entry.',
    'For visa-free entry, British citizens must book an appointment.',
    'Visa-free British citizens do not need an appointment before travel.',
    'British citizens travelling visa-free who arrive by air must book an appointment before entry.',
    'British citizens travelling visa-free must book an appointment before arrival if entering through Hanoi.',
    'Visa-free visitors must book an appointment before entry unless they hold a residence permit.',
])
def test_scoped_new_exempt_entry_rule_is_not_silently_discarded(quote):
    assert proof.field_workflow_matches('appointment_required', quote, EXEMPT, ROUTE)

def test_other_default_products_and_other_fields_keep_existing_scope_contract():
    assert proof.field_workflow_matches('appointment_required', FOOTER,
        dict(EXEMPT, disposition='VISA_REQUIRED', application_channel='embassy'), ROUTE)
    assert proof.field_workflow_matches('government_fee', FOOTER, EXEMPT, ROUTE)

@pytest.fixture
def cached(monkeypatch):
    engine = create_engine('sqlite://'); Base.metadata.create_all(engine); db = Session(engine)
    row = KimiRouteGuidanceCache(cache_key=kimi_primary.cache_key(ROUTE), route=deepcopy(ROUTE),
        guidance=deepcopy(EXEMPT), status='KIMI_PRIMARY', fresh_until=datetime(2020, 1, 1))
    db.add(row); db.commit()
    monkeypatch.setattr(verified_overrides, 'find', lambda _: {'fields': deepcopy(EXEMPT)})
    monkeypatch.setattr(verified_overrides, 'apply', lambda g, _: (deepcopy(g), {}))
    yield db, row
    freshness.set_provider(None); fetching.set_fetcher(None); db.close(); engine.dispose()

def read(*, quote=FOOTER, relevant=False, corrected=True, also=None):
    text = (RULE + '\n' if relevant else 'Tourist visa: apply in person, by post or email.\n') + quote
    fetching.set_fetcher(lambda requested, **_: FetchResult(requested_url=requested, final_url=URL,
        final_hostname='vietnamembassy.org.uk', ok=True, http_status=200,
        content_text=text, content_hash='workflow-scope-test', retrieved_at='2026-09-10'))
    def provider(system, payload):
        assert 'default entry product' in system
        fields = {'appointment_required': True} if corrected else {}
        fields.update(also or {})
        return {'page_relevant': relevant, 'page_is_nationality_specific': relevant,
            'consistent': not corrected, 'corrected_fields': fields,
            'evidence': {'appointment_required': quote}}
    freshness.set_provider(provider)

@pytest.mark.parametrize('relevant', [False, True])
def test_actual_footer_neither_creates_policy_dispute_nor_regrades_or_renews(cached, relevant):
    db, row = cached; before = deepcopy(row.guidance)
    read(relevant=relevant)
    result = freshness.recheck_row(db, row)
    assert result['outcome'] == 'validation_error'
    assert result['changed'] == [] and result['disputed'] == []
    assert db.query(DatabaseIssueReport).count() == 0
    assert row.guidance == before and row.status == 'KIMI_PRIMARY'
    assert row.fresh_until == datetime(2020, 1, 1)
    check = row.verification['grounded_check']
    assert check['validation_errors'] == ['unscoped default workflow evidence: appointment_required']
    source = check['source_checks'][0]
    assert source['rejected_workflow_fields']['appointment_required'] == {'value': True, 'quote': FOOTER}
    assert 'appointment_required' not in source['verified_fields']
    assert 'last_good_check' not in row.verification
    assert not (row.verification.get('comparison_cache') or {}).get('entries')

def test_unrelated_office_confirmation_cannot_reconfirm_current_false(cached):
    db, row = cached
    read(quote='Consular/Visa Section: No appointment is required.', relevant=True, corrected=False)
    freshness.recheck_row(db, row)
    check = row.verification['grounded_check']
    assert 'appointment_required' not in check['verified_fields']
    assert not check['renewed'] and check['outcome'] == 'validation_error'

def test_scoped_changed_default_condition_still_files_protected_dispute(cached):
    db, row = cached
    quote = 'British citizens travelling visa-free must book an appointment before travel.'
    read(quote=quote, relevant=True)
    result = freshness.recheck_row(db, row)
    assert result['disputed'] == ['appointment_required']
    issue = db.query(DatabaseIssueReport).one()
    assert issue.proposal['fields']['appointment_required']['quote'] == quote
    assert issue.status == 'open' and row.guidance['appointment_required'] is False

def test_existing_issue_and_prior_good_read_survive_scope_failure(cached):
    db, row = cached
    issue = DatabaseIssueReport(org_id='platform', cache_key=row.cache_key, route=row.route,
        field='appointment_required', note='Original footer finding', reported_by='freshness_monitor',
        status='open', proposal={'source_url':URL, 'fields': {'appointment_required': {
            'page_says':True, 'record_holds':False, 'quote':FOOTER}}})
    db.add(issue)
    prior = {'outcome':'checked','evidence_contract':freshness.EVIDENCE_CONTRACT,
             'at':'2020-01-01','source_url':URL,'verified_fields':['disposition'],'renewed':True}
    row.verification = {'grounded_check':deepcopy(prior),'last_good_check':deepcopy(prior)}
    db.commit(); before = deepcopy(issue.proposal)
    read(relevant=True); freshness.recheck_row(db, row)
    assert issue.status == 'open' and issue.proposal == before
    assert row.verification['last_good_check'] == prior
    assert freshness.effective_check(row.verification) == prior
    assert db.query(DatabaseIssueReport).count() == 1

def test_requested_issue_recheck_reports_scope_failure_without_dismissal(cached):
    db, row = cached
    issue = DatabaseIssueReport(org_id='platform', cache_key=row.cache_key, route=row.route,
        field='appointment_required', note='Original footer finding', reported_by='reviewer', status='open')
    db.add(issue); db.commit(); read()
    result = freshness.propose_for_issue(db, issue.id)
    assert result['outcome'] == 'validation_error' and not result['consistent']
    assert result['fields'] == {} and result['verified_fields'] == []
    assert result['rejected_workflow_fields'][0]['fields']['appointment_required']['quote'] == FOOTER
    assert issue.status == 'open' and not issue.resolved_at
    assert row.fresh_until == datetime(2020,1,1)

def test_valid_changed_default_visa_verdict_does_not_keep_old_no_application_scope(cached):
    db, row = cached
    changed = 'British citizens must obtain a visa for tourism in Vietnam.'
    text = changed + '\n' + FOOTER
    fetching.set_fetcher(lambda requested, **_: FetchResult(requested_url=requested, final_url=URL,
        final_hostname='vietnamembassy.org.uk', ok=True, http_status=200,
        content_text=text, content_hash='changed-default-test', retrieved_at='2026-09-10'))
    freshness.set_provider(lambda *_: {'page_relevant':True, 'page_is_nationality_specific':True,
        'consistent':False, 'corrected_fields':{'disposition':'VISA_REQUIRED','appointment_required':True},
        'evidence':{'disposition':changed,'appointment_required':FOOTER}})
    result = freshness.recheck_row(db,row)
    assert set(result['disputed']) == {'disposition','appointment_required'}
    assert row.guidance['disposition'] == 'VISA_EXEMPT' and row.guidance['appointment_required'] is False
    assert row.verification['grounded_check']['validation_errors'] == []
    assert db.query(DatabaseIssueReport).count() >= 1
    assert row.fresh_until == datetime(2020,1,1)


def test_conditional_scoped_entry_change_is_a_dispute_even_for_unprotected_raw_fact(cached,monkeypatch):
    db,row=cached
    monkeypatch.setattr(verified_overrides,'find',lambda _:None)
    quote='British citizens travelling visa-free who arrive by air must book an appointment before entry.'
    read(quote=quote,relevant=True)
    result=freshness.recheck_row(db,row)
    assert result['disputed']==['appointment_required'] and result['changed']==[]
    assert row.guidance['appointment_required'] is False
    assert db.query(DatabaseIssueReport).one().proposal['fields']['appointment_required']['quote']==quote


def test_conditional_confirmation_cannot_verify_an_unconditional_boolean(cached):
    db,row=cached
    quote='British citizens travelling visa-free do not need an appointment before entry if arriving by sea.'
    read(quote=quote,relevant=True,corrected=False)
    freshness.recheck_row(db,row)
    assert 'appointment_required' not in row.verification['grounded_check']['verified_fields']
    assert not row.verification['grounded_check']['renewed']

@pytest.mark.parametrize('field',['application_channel','application_channel_detail','account_registration_steps','payment_process','submission_process','appointment_required'])
@pytest.mark.parametrize('value',[None,'',[],{}])
def test_empty_workflow_extraction_is_not_a_policy_value(field,value):
    quote='You may apply for a tourist visa at the nearest embassy.'
    raw={'corrected_fields':{field:value},'evidence':{field:quote}}
    assert freshness._empty_workflow_proposal_errors(raw)==[field]
    fields,evidence,unquoted=freshness._quoted_proposals(raw,quote,ROUTE)
    assert fields=={} and unquoted==[field] and evidence[field]==quote

@pytest.mark.parametrize('field',['application_channel_detail','account_registration_steps','payment_process','submission_process'])
def test_empty_proposal_cannot_create_deferred_source_dispute(cached,field):
    db,row=cached
    row.guidance=dict(row.guidance,**{field:['Published existing instructions'] if field.endswith(('steps','process')) else 'Apply for K-ETA at least 72 hours before departure.'})
    db.commit();before=deepcopy(row.guidance)
    quote='You may apply for a tourist visa at the nearest embassy.'
    fetching.set_fetcher(lambda requested,**_:FetchResult(requested_url=requested,final_url=URL,final_hostname='vietnamembassy.org.uk',ok=True,http_status=200,content_text=quote,content_hash='empty-workflow',retrieved_at='2026-09-10'))
    freshness.set_provider(lambda *_:{'page_relevant':False,'page_is_nationality_specific':False,'consistent':False,'corrected_fields':{field:None},'evidence':{field:quote}})
    out=freshness.recheck_row(db,row)
    assert out['outcome']=='validation_error' and out['disputed']==[] and out['changed']==[]
    assert db.query(DatabaseIssueReport).count()==0 and row.guidance==before
    check=row.verification['grounded_check']['source_checks'][0]
    assert check['empty_workflow_fields'][field]=={'value':None,'quote':quote}
    assert row.fresh_until==datetime(2020,1,1)


def test_russia_korea_quote_does_not_prove_withdrawal_of_keta_application_instructions(cached):
    db,row=cached
    url='https://overseas.mofa.go.kr/ru-ko/brd/m_7329/view.do?seq=1034438'
    quote='협정대상범위 : 근로, 유학, 거주를 제외한 60일 이하 체류자'
    row.route=dict(ROUTE,passport_nationality='RUS',destination_country='KOR')
    row.cache_key=kimi_primary.cache_key(row.route)
    row.guidance={'disposition':'ELECTRONIC_AUTHORIZATION_REQUIRED','requirement_detail':'eta_electronic_authorization','application_channel':'online_portal','application_channel_detail':'Apply for K-ETA at least 72 hours before departure.','source_url':url}
    db.commit();before=deepcopy(row.guidance)
    fetching.set_fetcher(lambda requested,**_:FetchResult(requested_url=requested,final_url=url,final_hostname='overseas.mofa.go.kr',ok=True,http_status=200,content_text='Visa agreement\n'+quote,content_hash='rus-kor-regression',retrieved_at='2026-09-10'))
    freshness.set_provider(lambda *_:{'page_relevant':False,'page_is_nationality_specific':False,'consistent':False,'corrected_fields':{'application_channel_detail':None},'evidence':{'application_channel_detail':quote}})
    out=freshness.recheck_row(db,row)
    assert out['outcome']=='validation_error' and out['disputed']==[]
    assert row.guidance==before and db.query(DatabaseIssueReport).count()==0


def test_known_legacy_channel_spelling_is_diagnostic_not_policy_dispute(cached):
    db,row=cached
    row.guidance=dict(row.guidance,disposition='VISA_REQUIRED',application_channel='online_application')
    db.commit();before=deepcopy(row.guidance)
    quote='You can apply online for a visa or permit by completing an application form'
    fetching.set_fetcher(lambda requested,**_:FetchResult(requested_url=requested,final_url=URL,final_hostname='vietnamembassy.org.uk',ok=True,http_status=200,content_text=quote,content_hash='legacy-spelling',retrieved_at='2026-09-10'))
    freshness.set_provider(lambda *_:{'page_relevant':False,'page_is_nationality_specific':False,'consistent':False,'corrected_fields':{'application_channel':'online_portal'},'evidence':{'application_channel':quote}})
    out=freshness.recheck_row(db,row)
    assert out['outcome']=='validation_error' and out['disputed']==[] and out['changed']==[]
    assert row.guidance==before and row.fresh_until==datetime(2020,1,1)
    assert db.query(DatabaseIssueReport).count()==0
    diag=row.verification['grounded_check']['source_checks'][0]['equivalent_legacy_fields']
    assert diag['application_channel']=={'record_holds':'online_application','value':'online_portal','quote':quote}


def test_explicit_payment_change_remains_reviewable_alongside_empty_workflow(cached,monkeypatch):
    db,row=cached
    old=['Pay by money order or cashiers check.'];new=['Payments must be made online using the payment methods available during checkout']
    row.guidance=dict(row.guidance,disposition='VISA_REQUIRED',application_channel='embassy',payment_process=old)
    db.commit();monkeypatch.setattr(verified_overrides,'find',lambda _: {'fields':{'payment_process':old}})
    # Synthetic route proof; the payment sentence reproduces the USA→ERI finding.
    route_quote='British citizens must obtain a visa for tourism in Vietnam.'
    text=route_quote+'\n'+new[0]
    fetching.set_fetcher(lambda requested,**_:FetchResult(requested_url=requested,final_url=URL,final_hostname='vietnamembassy.org.uk',ok=True,http_status=200,content_text=text,content_hash='real-payment-change',retrieved_at='2026-09-10'))
    freshness.set_provider(lambda *_:{'page_relevant':True,'page_is_nationality_specific':True,'consistent':False,'corrected_fields':{'payment_process':new,'submission_process':None},'evidence':{'payment_process':new[0],'submission_process':route_quote}})
    out=freshness.recheck_row(db,row)
    assert out['outcome']=='validation_error' and out['disputed']==['payment_process']
    assert row.guidance['payment_process']==old and row.fresh_until==datetime(2020,1,1)
    issue=db.query(DatabaseIssueReport).one()
    assert issue.field=='payment_process' and issue.proposal['fields']['payment_process']['page_says']==new
