"""New NP writes need a captured dated review; historical reads are stable."""
from copy import deepcopy
import hashlib
import pytest
from scripts.convert_reviewed_general_batch import _build_manifest, build_manifest, convert
from scripts.prepare_reviewed_product_patch import PatchRejected
from tests.test_reviewed_general_batch import URL, absence, batch, layer, projected_entry, source
from app.visa_snapshot import tstation, verified_overrides as vo


def omission_batch():
    b = batch(); p = b['rows'][0]['products'][0]
    p['product']['validity'] = None
    p['proofs']['validity'] = absence(b, 'validity', product=p['product'],
        reason='The reviewed tourist e-visa application page does not state visa validity.')
    return b


def np_proof(b):
    return b['rows'][0]['products'][0]['proofs']['validity']


def test_captured_omission_survives_real_store_without_claiming_a_verified_value():
    b = omission_batch()
    assert 'not published' not in b['sources'][0]['text'].lower()
    manifest = build_manifest(b, [layer()])
    assert manifest['schema_version'] == 2
    overlay, _ = convert(manifest, [layer()])
    entry = overlay['entries'][0]; p = entry['fields']['visa_products'][0]
    proof = p['field_provenance']['validity']; audit = proof['absence_review']
    assert p['validity'] is None and proof['status'] == 'unknown'
    assert proof['verified_at'] is None and proof['source_url'] == ''
    assert audit['subject']['product_type'] == p['type']
    assert audit['field'] == 'validity' and audit['verified_at'] == '2026-09-09'
    assert audit['checked_sources'] == [{'id': 's1', 'url': URL, 'sha256': b['sources'][0]['sha256'], 'checked_at': '2026-09-09'}]
    assert URL in proof['note']
    row = projected_entry(entry)[0]
    assert row['validity_duration'] is None
    assert tstation.field_status(row)['validity_duration'] == 'not-published'


@pytest.mark.parametrize('mutate, message', [
    (lambda p: p.pop('source_ids'), 'captured pages'),
    (lambda p: p.update(source_ids=[]), 'captured pages'),
    (lambda p: p.update(source_ids=['missing']), 'captured pages'),
    (lambda p: p.update(source_ids=['s1','s1']), 'captured pages'),
    (lambda p: p.update(source_ids=[{}]), 'captured pages'),
    (lambda p: p.pop('absence_review'), 'exact field and subject'),
    (lambda p: p['absence_review'].update(field='fee'), 'exact field and subject'),
    (lambda p: p['absence_review']['subject'].update(passport_nationality='JPN'), 'exact field and subject'),
    (lambda p: p['absence_review']['subject'].update(travel_document_type='prc_travel_document'), 'exact field and subject'),
    (lambda p: p['absence_review']['subject'].update(product_type='Other visa'), 'exact field and subject'),
    (lambda p: p['absence_review']['subject'].update(disposition='VISA_EXEMPT'), 'exact field and subject'),
    (lambda p: p.pop('verifier'), 'name its AI reviewer'),
    (lambda p: p.update(verifier='human'), 'attributed to AI'),
    (lambda p: p.pop('verified_at'), 'review date'),
    (lambda p: p.update(verified_at='2999-01-01'), 'review date'),
    (lambda p: p.update(verified_at='2026-09-08'), 'predates'),
    (lambda p: p.update(reason='  '), 'needs a reason'),
    (lambda p: p.update(reason='x'*400), 'stored note limit'),
])
def test_new_absences_cannot_be_reason_only_or_borrow_another_subject(mutate,message):
    b=omission_batch(); mutate(np_proof(b))
    with pytest.raises(PatchRejected, match=message): build_manifest(b,[layer()])


def test_absence_cannot_borrow_another_governments_capture():
    b=omission_batch()
    b['sources'].append(source('Hong Kong tourist visa application instructions.',
        url='https://www.immd.gov.hk/eng/services/visas/visit_transit.html',sid='foreign'))
    np_proof(b)['source_ids']=['foreign']
    with pytest.raises(PatchRejected,match='destination-government'): build_manifest(b,[layer()])


@pytest.mark.parametrize('change',['text','hash','future_date'])
def test_shared_capture_integrity_applies_to_absence(change):
    b=omission_batch(); page=b['sources'][0]
    if change=='text': page['text']+=' An uncaptured change.'
    elif change=='hash': page['sha256']='0'*64
    else: page['checked_at']='2999-01-01'
    with pytest.raises(PatchRejected,match='hash mismatch|Future source-read date'): build_manifest(b,[layer()])


def test_published_validity_cannot_be_called_absent_with_reason_and_capture():
    b=omission_batch(); page=b['sources'][0]
    page['text']+=' The tourist e-visa is valid for 90 days.'
    page['sha256']=hashlib.sha256(page['text'].encode()).hexdigest()
    with pytest.raises(PatchRejected,match='already states a value'): build_manifest(b,[layer()])


@pytest.mark.parametrize('reason',[
    'The agency fee is not published, so the government fee is unavailable.',
    'Document acceptance is uncertain, so the government fee is not published.',
])
def test_published_government_fee_cannot_be_hidden_for_another_unknown_fact(reason):
    b=batch(); row=b['rows'][0]
    row['route_fields']['government_fee']={'amount':None,'currency':None}
    row['route_field_proofs']['government_fee']=absence(b,'government_fee',reason=reason)
    with pytest.raises(PatchRejected,match='already states a value'): build_manifest(b,[layer()])


def test_route_absence_retains_consulted_url_in_parsed_unknown_note():
    b=batch(); row=b['rows'][0]; row['route_fields']['processing_time']=None
    row['route_field_proofs']['processing_time']=absence(b,'processing_time',reason='The reviewed page does not give a processing time.')
    out,_=convert(build_manifest(b,[layer()]),[layer()])
    parsed=vo._parse_rows(out['entries'],{})[vo._key('HKG','VNM','tourism','ordinary_passport')]
    proof=parsed['field_provenance']['processing_time']
    assert URL in proof['note'] and '2026-09-09' in proof['note']
    assert proof['status']=='unknown' and proof['verified_at'] is None
    assert parsed['fields']['processing_time'] is None


def test_new_manifest_cannot_skip_absence_revalidation():
    m=build_manifest(omission_batch(),[layer()]); np_proof(m['batch']).pop('source_ids')
    with pytest.raises(PatchRejected,match='captured pages'): convert(m,[layer()])


def test_saved_v1_absences_reconstruct_but_cannot_be_built_as_new_writes():
    b=omission_batch(); p=np_proof(b); p.pop('source_ids'); p.pop('absence_review')
    saved=_build_manifest(deepcopy(b),[layer()],schema_version=1); before=deepcopy(saved)
    out,_=convert(saved,[layer()]); assert saved==before
    assert 'validity_duration' in out['entries'][0]['fields']['visa_products'][0]['unpublished_fields']
    with pytest.raises(PatchRejected,match='exact field and subject'): build_manifest(b,[layer()])


def test_existing_unknown_and_positive_fee_rules_remain():
    b=batch(); p=b['rows'][0]['products'][0]; p['product']['validity']=None
    p['proofs']['validity']={'status':'unknown','verifier':'ai','reason':'Not checked yet.'}
    out,_=convert(build_manifest(b,[layer()]),[layer()]); p=out['entries'][0]['fields']['visa_products'][0]
    assert p['fee']=={'amount':25,'currency':'USD'}
    assert 'validity_duration' not in p.get('unpublished_fields',[])


@pytest.mark.parametrize('statement', [
    'Your application will be processed in 3 working days.',
    'Visa processing normally takes five business days.',
    '审查所需时间为5个工作日。',
])
def test_published_processing_time_cannot_be_marked_absent(statement):
    b=batch(); row=b['rows'][0]; row['route_fields']['processing_time']=None
    row['route_field_proofs']['processing_time']=absence(b,'processing_time',reason='No processing time is stated.')
    page=b['sources'][0]; page['text']+=' '+statement
    page['sha256']=hashlib.sha256(page['text'].encode()).hexdigest()
    with pytest.raises(PatchRejected,match='already states a value'): build_manifest(b,[layer()])


def test_china_mission_published_yuan_tariff_defeats_agency_absence_reason():
    from scripts.convert_reviewed_general_batch import _check_audited_absence, _source_table
    from scripts.convert_reviewed_product_patch import _subject
    route=dict(passport_nationality='CHN',destination_country='JPN',travel_purpose='tourism',travel_document_type='prc_travel_document')
    url='https://www.cn.emb-japan.go.jp/itpr_zh/visa_qa.html'
    page=source('中国人申请签证，单次签证715元/回，多次签证1,430元/回，以上费用均在获批签证时收取。此外、指定旅行社或指定代办机构会收取额外手续费。',url=url)
    proof={'status':'not_published','verifier':'ai','verified_at':'2026-09-09',
           'reason':'The agency charge is unknown and document acceptance is uncertain.',
           'source_ids':['s1'],'absence_review':{'field':'government_fee','subject':_subject(route)}}
    with pytest.raises(PatchRejected,match='already states a value'):
        _check_audited_absence(proof,_source_table({'sources':[page]}),route,'government_fee',{'amount':None,'currency':None})
