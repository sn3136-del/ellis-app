"""A consular application lane is not a default visa-obligation decision."""
from copy import deepcopy
import hashlib

import pytest

from scripts.convert_reviewed_general_batch import (_check_proof, _source_table,
    build_manifest, convert, validate_batch)
from scripts.prepare_reviewed_product_patch import PatchRejected
from app.visa_snapshot import records_guard, tstation, verified_overrides as vo

HOST='https://vietnamembassy.org.uk'
ACCEPT=('The Embassy only processes visa applications for holders of national passports '
        'with approval letters issued by the Immigration Department of Viet Nam.')
INVITE=('You are kindly invited to submit your visa application directly to the Embassy’s office. '
        'Booking appointment is required.')
EXEMPT='British citizens do not require a visa for stays of up to 45 days.'
ROUTE={'passport_nationality':'GBR','destination_country':'VNM','travel_purpose':'tourism',
       'travel_document_type':'ordinary_passport'}
KEY='GBR|GBR|VNM|tourism|default|unknown|v6'
NAME='Embassy/consulate tourist visa'


def fixture():
    texts={'general':ACCEPT,'tourist':'II. Tourist visa:', 'apply':INVITE,
           'nationality':'United Kingdom (UK)', 'exemption':EXEMPT}
    sources=[{'id':sid,'url':HOST+'/'+sid,'text':txt,'checked_at':'2026-09-09',
              'sha256':hashlib.sha256(txt.encode()).hexdigest(),'reading_method':'test capture'}
             for sid,txt in texts.items()]
    def proof(ids,mode=None):
        p={'status':'reviewed','verifier':'ai','verified_at':'2026-09-09',
           'scope_note':'British ordinary passport tourist; consular option needs prior immigration approval.',
           'evidence':[{'source_id':sid,'source_url':HOST+'/'+sid,'quote':texts[sid]} for sid in ids]}
        if mode:p['verification_scope']=mode
        return p
    p={'type':NAME,'disposition':'VISA_REQUIRED','requirement_detail':'paper_visa',
       'notes':ACCEPT,'entry':None,'validity':None,'max_stay_days':None,'fee':{'amount':None,'currency':None}}
    q=proof(['general','tourist','apply','nationality'],'consular_product_eligibility')
    free={'type':'Visa-free entry','disposition':'VISA_EXEMPT','requirement_detail':'unconditional_visa_free',
          'max_stay_days':45,'fee':{'amount':0,'currency':None}}
    fp={'disposition':proof(['exemption']), 'max_stay_days':proof(['exemption']), 'fee':proof(['exemption'])}
    b={'schema_version':1,'kind':'general_reviewed_batch','id':'consular-test','reviewed_at':'2026-09-09',
       'sources':sources,'rows':[{'cache_key':KEY,'route':dict(ROUTE),
       'verdict':{'disposition':'VISA_EXEMPT','requirement_detail':'unconditional_visa_free','proof':proof(['exemption'])},
       'route_fields':{},'route_field_proofs':{},'products':[
         {'action':'patch','current_name':'Visa-free entry','product':free,'proofs':fp},
         {'action':'patch','current_name':NAME,'product':p,'proofs':{'disposition':q}}]}]}
    raw={'disposition':'VISA_EXEMPT','requirement_detail':'unconditional_visa_free','application_channel':'not_required',
         'permitted_stay_days':45,'visa_products':[deepcopy(free),deepcopy(p)]}
    layer={'cache_key':KEY,'route':dict(ROUTE),'raw_guidance':raw,'merged_guidance':deepcopy(raw),
           'source_provenance':None,'seed_entries':[],'operator_entries':[]}
    return b,layer


def product_check(b,route=None,product=True):
    row=b['rows'][0];spec=row['products'][1]
    return _check_proof(spec['proofs']['disposition'],_source_table(b),route or row['route'],
                        'disposition','VISA_REQUIRED',product=spec['product'] if product else None)


def replace_capture(b,sid,text,quote=None):
    s=next(s for s in b['sources'] if s['id']==sid)
    s['text']=text;s['sha256']=hashlib.sha256(text.encode()).hexdigest()
    for e in b['rows'][0]['products'][1]['proofs']['disposition']['evidence']:
        if e['source_id']==sid:e['quote']=text if quote is None else quote


def test_supported_optional_consular_lane_preserves_exemption_and_independent_guards():
    b,l=fixture();product_check(b)
    m=build_manifest(b,[l]);overlay,report=convert(m,[l])
    assert report[0]['unsupported_products']==[]
    entry=overlay['entries'][0]
    assert entry['fields']['disposition']=='VISA_EXEMPT'
    assert entry['fields']['requirement_detail']=='unconditional_visa_free'
    products=entry['fields']['visa_products']
    assert [p['type'] for p in products]==['Visa-free entry',NAME]
    assert products[1]['field_provenance']['disposition']['verification_scope']=='consular_product_eligibility'
    assert products[1]['notes']==ACCEPT
    assert products[1]['fee']['amount'] is None and products[1]['max_stay_days'] is None
    parsed=next(iter(vo._parse_rows([entry],{}).values()))
    g,_=vo.merge_verified_fields(l['raw_guidance'],parsed['fields'],source_url=entry['source_url'])
    prov=dict(parsed['field_provenance']['disposition'],fields=list(parsed['fields']),field_provenance=parsed['field_provenance'])
    rows=tstation.records_for_route(ROUTE,g,prov)
    assert len(rows)==2 and all(not r['_evidence_low'] for r in rows)
    assert all(r['confidence_level']=='Medium' for r in rows)  # checked, with gaps; never High
    answer={'guidance':g,'source_verified':prov,'held':False,'review_required':False}
    assert not records_guard.apply_records_hold(ROUTE,answer)['held']
    assert records_guard.apply_records_hold(ROUTE,dict(answer,grounded_check={'disputed_fields':['fee']}))['held']
    assert records_guard.apply_records_hold(ROUTE,dict(answer,detail_pending=True))['held']
    changed=deepcopy(g);changed['visa_products'][1]['type']='Different tourist visa'
    assert records_guard.apply_records_hold(ROUTE,dict(answer,guidance=changed))['held']


def test_positive_application_evidence_cannot_turn_default_exemption_into_required_visa():
    b,_=fixture()
    with pytest.raises(PatchRejected,match='consular-product eligibility'):
        product_check(b,product=False)
    row=b['rows'][0];row['verdict']={'disposition':'VISA_REQUIRED','requirement_detail':'paper_visa',
                                   'proof':deepcopy(row['products'][1]['proofs']['disposition'])}
    with pytest.raises(PatchRejected,match='consular-product eligibility'):
        validate_batch(b)


@pytest.mark.parametrize('mutate',[
    lambda b:b['rows'][0]['products'][1]['product'].__setitem__('requirement_detail','evisa'),
    lambda b:b['rows'][0]['products'][1]['product'].__setitem__('disposition','VISA_EXEMPT'),
    lambda b:b['rows'][0]['products'][1]['product'].__setitem__('type','Embassy business visa'),
    lambda b:b['rows'][0]['products'][1]['product'].__setitem__('notes','Contact the embassy; approval is optional.'),
    lambda b:b['rows'][0]['route'].__setitem__('travel_purpose','work'),
    lambda b:b['rows'][0]['route'].__setitem__('travel_document_type','diplomatic_passport'),
    lambda b:b['rows'][0]['route'].__setitem__('travel_document_type','refugee_travel_document'),
    lambda b:replace_capture(b,'general','The Embassy does not process tourist visa applications.'),
    lambda b:replace_capture(b,'general','The Embassy processes tourist visa applications only for diplomatic passports.'),
    lambda b:replace_capture(b,'tourist','III. Business visa:'),
    lambda b:replace_capture(b,'apply','Tourist visa application form. Nationality: Passport: Signature:'),
    lambda b:replace_capture(b,'nationality','United States of America'),
])
def test_scope_or_critical_condition_mismatches_reject_product_eligibility(mutate):
    b,_=fixture();mutate(b)
    with pytest.raises(PatchRejected,match='consular-product eligibility'):
        product_check(b)


@pytest.mark.parametrize('notice',[
 'Tourist visa applications are suspended.',
 'Tourist visa services are temporarily unavailable.',
 'Tourist visa services are closed.',
 'British passports are not eligible for a tourist visa.',
 'The Embassy no longer accepts visa applications.',
 'Applications from British passport holders are not accepted.',
 'Applications from British passport holders or refugees are not accepted.',
 'British applications without approval letters are not processed and all tourist applications are suspended.',
 'Visa applications by email or in person are not accepted.',
])
def test_old_invitation_cannot_hide_a_restriction_elsewhere_on_the_same_capture(notice):
    b,_=fixture();replace_capture(b,'general',ACCEPT+' '+notice,quote=ACCEPT)
    with pytest.raises(PatchRejected,match='consular-product eligibility'):
        product_check(b)


def test_email_suspension_does_not_cancel_explicit_physical_application_lane():
    b,_=fixture();replace_capture(b,'general',ACCEPT+' Visa applications by email are not accepted.',quote=ACCEPT)
    product_check(b)


@pytest.mark.parametrize('sid',['general','tourist','apply'])
def test_rule_purpose_and_invitation_must_come_from_the_same_destination_embassy(sid):
    b,_=fixture();url='https://www.immd.gov.hk/'+sid
    next(s for s in b['sources'] if s['id']==sid)['url']=url
    for e in b['rows'][0]['products'][1]['proofs']['disposition']['evidence']:
        if e['source_id']==sid:e['source_url']=url
    with pytest.raises(PatchRejected,match='consular-product eligibility'):
        product_check(b)


def test_origin_government_cannot_supply_the_only_nationality_anchor():
    b,_=fixture();s=next(s for s in b['sources'] if s['id']=='nationality');s['url']='https://www.gov.uk/foreign-travel-advice/vietnam'
    b['rows'][0]['products'][1]['proofs']['disposition']['evidence'][-1]['source_url']=s['url']
    with pytest.raises(PatchRejected,match='consular-product eligibility'):
        product_check(b)


def test_capture_hash_literal_quote_and_exact_layer_checks_are_unchanged():
    b,l=fixture();b['sources'][0]['sha256']='bad'
    with pytest.raises(PatchRejected,match='hash mismatch'):product_check(b)
    b,l=fixture();b['rows'][0]['products'][1]['proofs']['disposition']['evidence'][0]['quote']=ACCEPT.replace('only processes','always accepts')
    with pytest.raises(PatchRejected,match='not on its captured page'):product_check(b)
    b,l=fixture();m=build_manifest(b,[l]);altered=deepcopy(l);altered['raw_guidance']['new']='Changed after review'
    with pytest.raises(PatchRejected,match='Layer changed'):convert(m,[altered])


@pytest.mark.parametrize('sid',['general','tourist','apply'])
def test_another_destination_embassy_cannot_supply_missing_local_instructions(sid):
    b,_=fixture();url='https://vnembassy-london.mofa.gov.vn/'+sid
    next(s for s in b['sources'] if s['id']==sid)['url']=url
    for e in b['rows'][0]['products'][1]['proofs']['disposition']['evidence']:
        if e['source_id']==sid:e['source_url']=url
    with pytest.raises(PatchRejected,match='consular-product eligibility'):product_check(b)


def test_approval_must_come_from_the_destination_and_cannot_be_called_optional():
    b,_=fixture();wrong=ACCEPT.replace('of Viet Nam','of Hong Kong')
    replace_capture(b,'general',wrong)
    b['rows'][0]['products'][1]['product']['notes']=wrong
    with pytest.raises(PatchRejected,match='consular-product eligibility'):product_check(b)
    b,_=fixture();b['rows'][0]['products'][1]['product']['notes']=ACCEPT+' Approval letters are optional.'
    with pytest.raises(PatchRejected,match='consular-product eligibility'):product_check(b)


def test_london_embassy_authority_is_exact_and_belongs_to_vietnam():
    from app.visa_snapshot.authority import is_government_host
    from app.visa_snapshot.evidence_validator import jurisdiction_matches
    assert is_government_host('vietnamembassy.org.uk')
    assert jurisdiction_matches(HOST,'VNM')
    assert not jurisdiction_matches(HOST,'GBR')
    for host in ['fake.vietnamembassy.org.uk','vietnamembassy.org.uk.attacker.com','fakevietnamembassy.org.uk']:
        assert not is_government_host(host)
        assert not jurisdiction_matches('https://'+host,'VNM')


def test_exact_refugee_and_missing_approval_restriction_preserves_scoped_lane():
    b,_=fixture()
    notice='Any other travel documents such as documents issued for refugees or national passports without approval letters are not acceptable.'
    replace_capture(b,'general',ACCEPT+' '+notice,quote=ACCEPT)
    product_check(b)


def test_email_notice_heading_does_not_expand_email_only_suspension_to_post():
    b,_=fixture()
    text=ACCEPT+'\nII.5. Apply by email, receive loose-leaf visa by post\nWe curently do not receive visa application by email. Please apply either in person or by post.'
    replace_capture(b,'general',text,quote=ACCEPT)
    product_check(b)
