"""Real official list captures plus isolated fresh-read integration regressions."""
import json
from copy import deepcopy
from pathlib import Path
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models import Base
from app.visa_snapshot import fetching, freshness, verified_overrides as vo
from app.visa_snapshot import freshness_evidence as proofs
from app.visa_snapshot import structured_evidence as structured
from app.visa_snapshot.evidence_validator import field_value_supported
from app.visa_snapshot.fetching import FetchResult
from app.visa_snapshot.models import KimiRouteGuidanceCache

ROOT = Path(__file__).resolve().parents[2]
PHL = ROOT / 'data/database_seed/reviewed_phl_coverage_2026_09_09.json'

def route(nat='JPN', dest='PHL'):
    return {'passport_nationality':nat,'destination_country':dest,
            'travel_document_type':'ordinary_passport','travel_purpose':'tourism'}

@pytest.fixture
def db(monkeypatch):
    engine=create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    session=sessionmaker(bind=engine)()
    monkeypatch.setattr(vo,'apply',lambda guidance, route:(deepcopy(guidance),{}))
    yield session
    session.close(); engine.dispose()
    freshness.set_provider(None); fetching.set_fetcher(None)


def capture(url,text):
    from urllib.parse import urlsplit
    return FetchResult(requested_url=url,ok=True,final_url=url,final_hostname=urlsplit(url).hostname,
        http_status=200,content_text=text,content_hash='new:'+str(len(text)),retrieved_at='2026-09-09T10:00:00Z')


def seed(db,g,r):
    from app.visa_snapshot import kimi_primary
    row=KimiRouteGuidanceCache(cache_key=kimi_primary.cache_key(r),
                              route=r,guidance=g,status='KIMI_PRIMARY')
    db.add(row); db.commit(); return row


def test_actual_philippine_country_list_rechecks_and_derives_only_exempt_no_application(db,monkeypatch):
    data=json.loads(PHL.read_text())
    entry=next(e for e in data['routes'] if e['route']['nationality']=='JPN')
    proof=entry['field_provenance']['disposition']
    source=next(s for s in data['sources'] if s['id']==proof['source_id'])
    monkeypatch.setattr(vo,'find',lambda r:{'source_url':source['url'],'fields':{},'field_provenance':{'disposition':proof}})
    fetching.set_fetcher(lambda url,timeout_seconds=0:capture(url,source['text']))
    # The model itself cannot connect the list; exact structured proof can.
    freshness.set_provider(lambda *_:{'consistent':True,'page_relevant':True,
        'page_is_nationality_specific':False,'corrected_fields':{},'evidence':{}})
    row=seed(db,{'disposition':'VISA_EXEMPT','application_channel':'not_required','source_url':source['url']},route())
    result=freshness.recheck_row(db,row,today='2026-09-09')
    check=row.verification['grounded_check']
    assert result['outcome']=='checked'
    assert check['verified_fields']==['application_channel','disposition']
    assert check['renewed'] is True
    assert check['field_sources']['application_channel']['derived_from']=='disposition'
    assert check['field_sources']['disposition']['scope_quotes']
    # A future fetch cannot reuse the old snapshot when the member disappears.
    fetching.set_fetcher(lambda url,timeout_seconds=0:capture(url,source['text'].replace('\nJapan\n','\nJamaica\n')))
    result=freshness.recheck_row(db,row,today='2026-09-09')
    assert result['outcome']=='page_not_relevant'
    assert freshness.effective_check(row.verification)=={}


def test_actual_table_cannot_escape_document_or_purpose_scope():
    data=json.loads(PHL.read_text()); entry=next(e for e in data['routes'] if e['route']['nationality']=='JPN')
    proof=entry['field_provenance']['disposition']; source=next(s for s in data['sources'] if s['id']==proof['source_id'])
    for r in [dict(route(),travel_purpose='work'),dict(route(),travel_document_type='diplomatic_passport'),route('ZZZ')]:
        assert proofs.structured_route_result({},proof,source,{source['id']:source},r,'VISA_EXEMPT',{},'2026-09-09') is None


def test_generic_canadian_eta_fee_needs_separate_current_route_proof(db,monkeypatch):
    policy='https://www.canada.ca/en/immigration-refugees-citizenship/services/visit-canada/entry-requirements-country.html'
    fees='https://www.canada.ca/en/immigration-refugees-citizenship/services/visit-canada/eta/about.html'
    texts={policy:'British citizens need an eTA to fly to Canada for tourism.', fees:'Pay CAN$7 for your eTA.'}
    monkeypatch.setattr(vo,'find',lambda r:None)
    fetching.set_fetcher(lambda url,timeout_seconds=0:capture(url,texts[url]))
    def compare(_, user):
        p=json.loads(user); is_route=p['official_page_url']==policy
        return {'consistent':True,'page_relevant':True,'page_is_nationality_specific':is_route,
                'corrected_fields':{},'evidence':{} if is_route else {'government_fee':texts[fees]}}
    freshness.set_provider(compare)
    g={'disposition':'ELECTRONIC_AUTHORIZATION_REQUIRED','requirement_detail':'eta_electronic_authorization',
       'source_url':policy,'official_portal_url':fees,'government_fee':{'amount':7,'currency':'CAD'}}
    row=seed(db,g,route('GBR','CAN'))
    assert freshness.recheck_row(db,row,today='2026-09-09')['outcome']=='checked'
    check=row.verification['grounded_check']
    assert 'government_fee' in check['verified_fields']
    assert check['field_sources']['government_fee']['source_url']==fees
    texts[policy]='General travel advice for Canada.'
    assert freshness.recheck_row(db,row,today='2026-09-09')['outcome']=='page_not_relevant'


@pytest.mark.parametrize('text', ['Pay CAN$7 for your eTA.','Pay $CAN 7.00 for your eTA.','The eTA costs 7 Canadian dollars.'])
def test_canadian_currency_alias_and_decimal_semantics(text):
    assert field_value_supported('government_fee',{'amount':7,'currency':'CAD'},text)
    assert not field_value_supported('government_fee',{'amount':70,'currency':'CAD'},text)
    assert not field_value_supported('government_fee',{'amount':7,'currency':'USD'},text)


def test_qualified_canadian_air_eta_rule_must_keep_surface_distinction():
    proof={'ok':True,'program':'canada_eta_member','conditions':[]}
    assert not structured.guidance_conditions_preserved(proof,{'entry_requirements':'Get an eTA.'})['ok']
    assert structured.guidance_conditions_preserved(proof,{'entry_requirements':'An eTA is required for air travel. An eTA is not required when entering by land.'})['ok']


def test_ancillary_price_for_other_program_cannot_attest_eta():
    source={'url':'https://www.canada.ca/eta','text':'A visitor visa costs CAD100.'}
    answer={'evidence':{'government_fee':source['text']}}
    g={'disposition':'ELECTRONIC_AUTHORIZATION_REQUIRED','requirement_detail':'eta_electronic_authorization',
       'government_fee':{'amount':100,'currency':'CAD'}}
    assert proofs.ancillary_fields(source,answer,g,route('GBR','CAN'),[{'quote':'British citizens need an eTA to fly to Canada.'}])==set()


def test_provenance_keeps_structured_and_multi_source_fragments():
    p={'source_url':'https://www.canada.ca/eta','verified_at':'2026-09-09',
       'source_id':'policy','source_table':{'nationality_quote':'Japan'},
       'supporting_evidence':[{'source_id':'fees','source_url':'https://www.canada.ca/fees','quote':'CAD7'}]}
    result=vo._provenance(p)
    assert result['source_table']==p['source_table']
    assert result['supporting_evidence']==p['supporting_evidence']
    result['source_table']['nationality_quote']='Other'
    assert p['source_table']['nationality_quote']=='Japan'


@pytest.mark.parametrize('nationality','JPN KOR USA THA SGP MYS GBR RUS AUS IDN FRA VNM ESP CAN'.split())
def test_all_fourteen_actual_phl_list_members_are_readable_as_routes(db,monkeypatch,nationality):
    data=json.loads(PHL.read_text()); entry=next(e for e in data['routes'] if e['route']['nationality']==nationality)
    proof=entry['field_provenance']['disposition']; source=next(s for s in data['sources'] if s['id']==proof['source_id'])
    monkeypatch.setattr(vo,'find',lambda r:{'source_url':source['url'],'fields':{},'field_provenance':{'disposition':proof}})
    fetching.set_fetcher(lambda url,**_:capture(url,source['text']))
    freshness.set_provider(lambda *_:{'consistent':True,'page_relevant':True,'page_is_nationality_specific':False,'corrected_fields':{},'evidence':{}})
    row=seed(db,{'disposition':'VISA_EXEMPT','source_url':source['url']},route(nationality))
    assert freshness.recheck_row(db,row,today='2026-09-09')['outcome']=='checked'
    assert 'disposition' in row.verification['grounded_check']['verified_fields']


@pytest.mark.parametrize('nationality','KOR GBR AUS FRA'.split())
def test_actual_canadian_country_lists_and_separate_eta_price(db,monkeypatch,nationality):
    data=json.loads((ROOT/'data/database_seed/reviewed_us_can12_coverage_2026_09_09.json').read_text())
    entry=next(e for e in data['routes'] if e['route']['nationality']==nationality and e['route']['destination']=='CAN')
    sources={s['url']:s for s in data['sources']}; fields=entry['field_provenance']
    override={'source_url':entry['guidance']['source_url'],'fields':{},'field_provenance':fields}
    monkeypatch.setattr(vo,'find',lambda r:override)
    fetching.set_fetcher(lambda url,**_:capture(url,sources[url]['text']) if url in sources else
                        FetchResult(requested_url=url,ok=False,error='fixture missing'))
    def compare(_,user):
        p=json.loads(user); fee=fields['government_fee']
        return {'consistent':True,'page_relevant':True,'page_is_nationality_specific':False,'corrected_fields':{},
            'evidence':{'government_fee':fee['quote']} if p['official_page_url']==fee['source_url'] else {}}
    freshness.set_provider(compare)
    row=seed(db,entry['guidance'],route(nationality,'CAN'))
    assert freshness.recheck_row(db,row,today='2026-09-09')['outcome']=='checked'
    check=row.verification['grounded_check']
    assert {'disposition','government_fee'} <= set(check['verified_fields'])
    assert check['field_sources']['government_fee']['source_url']==fields['government_fee']['source_url']
    # Partial supporting fields must not turn this into whole-detail renewal.
    assert check['renewed'] is False


def test_conflicting_source_identifier_cannot_recover_on_third_occurrence():
    fields={'x':{'source_id':'same','source_url':'https://www.canada.ca/one'},
            'y':{'source_id':'same','source_url':'https://www.canada.ca/two'},
            'z':{'source_id':'same','source_url':'https://www.canada.ca/one'}}
    assert 'same' not in proofs.reviewed_evidence({'field_provenance':fields},{})[1]


def test_invalid_structured_model_proof_cannot_fall_back_to_true_but_unscoped_text(db,monkeypatch):
    url='https://www.mofa.go.jp/visa'
    text='Canadian citizens need a visa for tourism in Japan.'
    monkeypatch.setattr(vo,'find',lambda r:None)
    fetching.set_fetcher(lambda url,**_:capture(url,text))
    freshness.set_provider(lambda *_:{'consistent':True,'page_relevant':True,'page_is_nationality_specific':True,'corrected_fields':{},'evidence':{},
        'route_evidence':{'quote':text,'source_table':{'heading_quote':text,'table_quote':text,'nationality_quote':'China'}}})
    row=seed(db,{'disposition':'VISA_REQUIRED','source_url':url},route('CAN','JPN'))
    assert freshness.recheck_row(db,row,today='2026-09-09')['outcome']=='page_not_relevant'


def test_old_price_provenance_cannot_verify_an_ineligible_program(db,monkeypatch):
    url='https://www.canada.ca/eta'
    fee_quote='Pay CAN$7 for your eTA.'
    monkeypatch.setattr(vo,'find',lambda r:{'source_url':url,'fields':{},'field_provenance':{
        'government_fee':{'source_id':'price','source_url':url,'quote':fee_quote,'verified_at':'2026-09-09'}}})
    fetching.set_fetcher(lambda url,**_:capture(url,fee_quote))
    freshness.set_provider(lambda *_:{'consistent':True,'page_relevant':True,'page_is_nationality_specific':False,'corrected_fields':{},
        'evidence':{'government_fee':fee_quote}})
    row=seed(db,{'disposition':'VISA_REQUIRED','government_fee':{'amount':7,'currency':'CAD'},'source_url':url},route('CHN','CAN'))
    assert freshness.recheck_row(db,row,today='2026-09-09')['outcome']=='page_not_relevant'
    assert not freshness.effective_check(row.verification)


@pytest.mark.parametrize('quote,accepted',[
    ('Your passport must be valid for the entire duration of your stay.',True),
    ('Your passport must be valid for six months from application.',False),
    ('Your passport must be valid for the entire stay and six months from application.',False),
    ('The passport kind is valid_for_duration_of_stay.',False),
])
def test_passport_provider_synonym_needs_the_exact_entry_constraint(quote,accepted):
    raw={'corrected_fields':{'passport_validity_requirement':{'kind':'valid_for_duration_of_stay','months':None}},
         'evidence':{'passport_validity_requirement':quote}}
    quoted,_,rejected=freshness._quoted_proposals(raw,quote)
    if accepted:
        assert quoted['passport_validity_requirement']=={'kind':'valid_through_departure','months':0}
    else:
        assert rejected==['passport_validity_requirement']
        assert quoted=={}


def test_flagged_generic_fee_uses_same_independent_route_proof(db,monkeypatch):
    from app.visa_snapshot.models import DatabaseIssueReport
    policy='https://www.canada.ca/entry'; price='https://www.canada.ca/eta'
    texts={policy:'British citizens need an eTA to fly to Canada for tourism.',price:'Pay CAN$7 for your eTA.'}
    monkeypatch.setattr(vo,'find',lambda r:None)
    fetching.set_fetcher(lambda url,**_:capture(url,texts[url]))
    def compare(_,user):
        url=json.loads(user)['official_page_url']
        return {'consistent':url==policy,'page_relevant':True,'page_is_nationality_specific':url==policy,
          'corrected_fields':{} if url==policy else {'government_fee':{'amount':7,'currency':'CAD'}},
          'evidence':{} if url==policy else {'government_fee':texts[price]}}
    freshness.set_provider(compare)
    g={'disposition':'ELECTRONIC_AUTHORIZATION_REQUIRED','requirement_detail':'eta_electronic_authorization',
       'government_fee':{'amount':9,'currency':'CAD'},'source_url':policy,'official_portal_url':price}
    row=seed(db,g,route('GBR','CAN'))
    issue=DatabaseIssueReport(org_id='test',cache_key=row.cache_key,route=row.route,field='government_fee',
                              note='Check price',reported_by='reader',status='open')
    db.add(issue);db.commit()
    proposal=freshness.propose_for_issue(db,issue.id)
    assert proposal['source_url']==price
    assert proposal['fields']['government_fee']['page_says']=={'amount':7,'currency':'CAD'}
    assert row.guidance['government_fee']['amount']==9


@pytest.mark.parametrize('program,good',[
    ('korea_russian_keta','Travellers aged 17 or younger or 65 or older are exempt from K-ETA; a traveller turning 18 by entry must obtain it.'),
    ('india_vietnam_tourist_visa','OCI/eOCI holders and travellers covered by an applicable bilateral exemption have separate entry rules.'),
    ('taiwan_hk_entry_permit','Temporary permit requires birth in Hong Kong or previous admission. Hong Kong permanent residence and restrictions on another passport apply. Others need a different permit.'),
])
def test_named_special_rules_keep_their_applicability_conditions(program,good):
    result={'ok':True,'program':program}
    assert structured.guidance_conditions_preserved(result,{'exceptions':[good]})['ok']
    assert not structured.guidance_conditions_preserved(result,{'entry_requirements':'Everyone must apply.'})['ok']


def test_monetary_grouping_is_numeric_and_never_changes_a_negative_fee():
    assert field_value_supported('government_fee',{'amount':10000,'currency':'KRW'},'K-ETA fee: 10,000 KRW.')
    assert not field_value_supported('government_fee',{'amount':1000,'currency':'KRW'},'K-ETA fee: 10,000 KRW.')
    assert not field_value_supported('government_fee',{'amount':7,'currency':'CAD'},'A credit of -7 CAD applies.')
    assert not field_value_supported('government_fee',{'amount':1000,'currency':'KRW'},'Malformed 10,00 KRW.')


def test_eta_scope_cannot_borrow_the_neighbouring_visitor_visa_price():
    text='An eTA costs CAD7. A visitor visa costs CAD100.'
    answer={'evidence':{'government_fee':'A visitor visa costs CAD100.'},'field_scope':{'government_fee':text}}
    g={'disposition':'ELECTRONIC_AUTHORIZATION_REQUIRED','requirement_detail':'eta_electronic_authorization','government_fee':{'amount':100,'currency':'CAD'}}
    assert proofs.ancillary_fields({'url':'https://www.canada.ca/fees','text':text},answer,g,route('GBR','CAN'),
                                  [{'quote':'British citizens need an eTA to fly to Canada.'}])==set()


@pytest.mark.parametrize('quote',[
    'A passport is not required to be valid for the entire duration of your stay.',
    'Your passport is valid for the entire duration of your stay only if you hold a residence permit.',
])
def test_passport_negation_or_condition_cannot_become_unconditional(quote):
    raw={'corrected_fields':{'passport_validity_requirement':{'kind':'valid_for_duration_of_stay','months':None}},
         'evidence':{'passport_validity_requirement':quote}}
    assert freshness._quoted_proposals(raw,quote)[0]=={}
    assert not field_value_supported('passport_validity_requirement',{'kind':'valid_through_departure','months':0},quote)


def test_other_nationalities_fee_does_not_override_us_price(db,monkeypatch):
    url='https://www.mfa.gov.cn/fees'
    text='United States citizens need a visa for tourism in China. The visa fee for United States citizens is USD140. The visa fee for other nationalities is USD34.'
    monkeypatch.setattr(vo,'find',lambda r:None)
    fetching.set_fetcher(lambda url,**_:capture(url,text))
    freshness.set_provider(lambda *_:{'consistent':False,'page_relevant':True,'page_is_nationality_specific':True,
      'corrected_fields':{'government_fee':{'amount':34,'currency':'USD'}},
      'evidence':{'government_fee':'The visa fee for other nationalities is USD34.'}})
    row=seed(db,{'disposition':'VISA_REQUIRED','government_fee':{'amount':140,'currency':'USD'},'source_url':url},route('USA','CHN'))
    freshness.recheck_row(db,row,today='2026-09-09')
    assert row.guidance['government_fee']['amount']==140
    assert 'government_fee' not in row.verification['grounded_check']['verified_fields']
    assert row.verification['grounded_check']['renewed'] is False


def test_missing_stored_qualification_cannot_fall_back_to_loose_prose(db,monkeypatch):
    data=json.loads((ROOT/'data/database_seed/reviewed_us_can12_coverage_2026_09_09.json').read_text())
    entry=next(e for e in data['routes'] if e['route']['nationality']=='GBR' and e['route']['destination']=='CAN')
    proof=entry['field_provenance']['disposition']; source=next(s for s in data['sources'] if s['id']==proof['source_id'])
    text=source['text']+'\nBritish citizens need an eTA to fly to Canada for tourism.'
    monkeypatch.setattr(vo,'find',lambda r:{'source_url':source['url'],'fields':{},'field_provenance':{'disposition':proof}})
    fetching.set_fetcher(lambda url,**_:capture(url,text))
    freshness.set_provider(lambda *_:{'consistent':True,'page_relevant':True,'page_is_nationality_specific':True,'corrected_fields':{},'evidence':{}})
    row=seed(db,{'disposition':'ELECTRONIC_AUTHORIZATION_REQUIRED','requirement_detail':'eta_electronic_authorization','entry_requirements':'Get an eTA.','source_url':source['url']},route('GBR','CAN'))
    assert freshness.recheck_row(db,row,today='2026-09-09')['outcome']=='page_not_relevant'


def test_future_companion_country_list_cannot_support_current_rule():
    data=json.loads((ROOT/'data/database_seed/reviewed_rus_aus_idn15_coverage_2026_09_09.json').read_text())
    entry=next(e for e in data['routes'] if e['route']['destination']=='AUS' and e['field_provenance']['disposition'].get('source_closed_list',{}).get('program')=='australia_evisitor_member')
    proof=entry['field_provenance']['disposition']; sources={s['id']:deepcopy(s) for s in data['sources']}
    rule=proof['source_closed_list']; sid=rule['eligibility_source_id'] if 'eligibility_source_id' in rule else rule['source_id']
    sources[sid]['text']=sources[sid]['text'].replace(rule['heading_quote'],'Effective from 1 October 2026.\n'+rule['heading_quote'])
    r=route(entry['route']['nationality'],'AUS')
    assert proofs.structured_route_result({},proof,sources[proof['source_id']],sources,r,'VISA_REQUIRED',entry['guidance'],'2026-09-09') is None


def test_fee_document_and_purpose_scope_cannot_be_inherited_from_the_route_page():
    ordinary=route('CAN','JPN')
    assert not proofs.field_scope_matches_route('government_fee','Canadian diplomatic passport holders pay USD0.',ordinary)
    assert not proofs.field_scope_matches_route('government_fee','Canadian ordinary passport holders pay USD25.',dict(ordinary,travel_document_type='diplomatic_passport'))
    assert not proofs.field_scope_matches_route('government_fee','Canadian citizens pay USD25 for a work visa.',ordinary)
    assert proofs.field_scope_matches_route('government_fee','Canadian ordinary passport holders pay USD25.',ordinary)


def test_nested_product_fee_cannot_borrow_another_product_price():
    quote='The Standard Visitor visa costs GBP135. An ETA costs GBP16.'
    wrong=[{'type':'Standard Visitor visa','fee':{'amount':16,'currency':'GBP'}}]
    assert not field_value_supported('visa_products',wrong,quote)
    raw={'corrected_fields':{'visa_products':wrong},'evidence':{'visa_products':quote}}
    assert freshness._quoted_proposals(raw,quote)[0]=={}
    assert field_value_supported('visa_products',[{'type':'Standard Visitor visa','fee':{'amount':135,'currency':'GBP'}}],quote)
    assert not field_value_supported('visa_products',wrong,'The Standard Visitor visa costs GBP135; an ETA costs GBP16.')


def test_nested_product_also_requires_nationality_ownership():
    r=route('USA','CHN')
    quote='Chinese nationals pay USD34 for the Standard Tourist visa.'
    raw={'corrected_fields':{'visa_products':[{'type':'Standard Tourist visa','fee':{'amount':34,'currency':'USD'}}]},
         'evidence':{'visa_products':quote}}
    assert freshness._quoted_proposals(raw,quote,r)[0]=={}
    assert not proofs.field_scope_matches_route('government_fee','United States citizens pay USD140. Chinese citizens pay USD34.',r)


def test_changing_verdict_to_eta_cannot_bypass_new_program_fee_scope(db,monkeypatch):
    url='https://www.canada.ca/entry'
    text='British citizens need an eTA electronic authorization to fly to Canada for tourism. An eTA costs CAD7. A visitor visa costs CAD100.'
    monkeypatch.setattr(vo,'find',lambda r:None)
    fetching.set_fetcher(lambda url,**_:capture(url,text))
    freshness.set_provider(lambda *_:{'consistent':False,'page_relevant':True,'page_is_nationality_specific':True,
      'corrected_fields':{'disposition':'ELECTRONIC_AUTHORIZATION_REQUIRED','requirement_detail':'eta_electronic_authorization','government_fee':{'amount':100,'currency':'CAD'}},
      'evidence':{'disposition':'British citizens need an eTA electronic authorization to fly to Canada for tourism.',
      'requirement_detail':'British citizens need an eTA electronic authorization to fly to Canada for tourism.',
      'government_fee':'A visitor visa costs CAD100.'},
      'field_scope':{'government_fee':'An eTA costs CAD7. A visitor visa costs CAD100.'}})
    row=seed(db,{'disposition':'VISA_REQUIRED','requirement_detail':'paper_visa','government_fee':{'amount':9,'currency':'CAD'},'source_url':url},route('GBR','CAN'))
    freshness.recheck_row(db,row,today='2026-09-09')
    assert row.guidance['government_fee']['amount']==9
    assert 'government_fee' not in row.verification['grounded_check']['verified_fields']
    assert row.verification['grounded_check']['renewed'] is False


def test_paper_visa_fee_cannot_take_an_electronic_authorization_price():
    g={'disposition':'VISA_REQUIRED','requirement_detail':'paper_visa'}
    assert not proofs.field_program_matches('government_fee','An ETA costs GBP16.',g)
    assert proofs.field_program_matches('government_fee','The Standard Visitor visa costs GBP135.',g)
