"""Closed India source review: ports, processing minimum and owned fee/validity.

No policy-expiry/consular absence credit, new product, grade, TTL, raw guidance,
issue or operator writes. The numerical processing minimum remains empty;
its source-scoped absence is documented independently for each eVisa product.
"""
from copy import deepcopy
from datetime import date
from scripts._reviewed_india_completeness_contract import CONTRACT,SOURCE_CATALOG_SHA256,SOURCE_PINS,FEE_QUOTES
from scripts.convert_reviewed_product_validity import BASELINE_KEYS,_current_map,_sources
from scripts.convert_reviewed_product_patch import field_provenance,_subject
from scripts.prepare_reviewed_product_patch import PatchRejected,digest

REVIEW_ID='india-owned-product-completeness-20260910'
EVISA='https://indianvisaonline.gov.in/evisa/tvoa.html'
PIB='https://www.pib.gov.in/newsite/erelcontent.aspx?lang=2&reg=48&relid=293125'
FEE='https://indianvisaonline.gov.in/evisa/images/Etourist_fee_final.pdf'
OLD=('Entry on an e-Visa is restricted to designated airports and seaports. Biometrics are captured at immigration on arrival.','Entry is only through the designated e-Visa airports and seaports','e-Visa holders may only enter through the designated airports and seaports, not land borders')
PORT='Entry on an e-Visa is restricted to designated airports, seaports and land ports. Check the Ministry of Home Affairs list of eligible ports at '+PIB+'.'
PORT_QUOTE='The newly added 11 International Ports include 09 land ports and 02 airports.'
BIOMETRIC='Biometric details of the applicant will be mandatorily captured at Immigration on arrival in India.'
LEAD='applicants of the eligible countries/territories may apply online minimum 4 days in advance of the date of arrival.'
PERFORMANCE='Notably, India provides approximately 95% E-visas applications in less than 72 hours.'
VALIDITY='Duration: One year (365 Days) from the date of grant of ETA.'
NP_REASON=('Not published in the reviewed current Indian e-Visa portal instructions and FAQ and the Ministry of Home Affairs 10 August 2026 release: a shortest submission-to-issuance processing duration for this eVisa product. The minimum four days is advance application lead time; approximately 95% in less than 72 hours is observed performance, neither is an adjudication minimum or guarantee. This documented absence is limited to the numeric minimum and its unit; it makes no claim that all processing information is unpublished.')


def _proof(layer,field,product=None):
    p=dict(status='reviewed',verifier='ai',verified_at='2026-09-10',subject=_subject(layer['route'],product),scope_note='AI review of only this exact product/field. No eligibility, other price, stay, policy expiry, confidence or freshness certification.')
    if field=='exceptions':
        old=(product if product is not None else layer['merged_guidance'])['exceptions'];new=[_port(x) if x in OLD else x for x in old]
        p.update(evidence=[dict(source_id='india_mha_ports_processing',source_url=PIB,quote=PORT_QUOTE),dict(source_id='india_evisa',source_url=EVISA,quote=BIOMETRIC)],verified_elements=[v for u,v in zip(old,new) if u!=v],retained_unverified_elements=[v for u,v in zip(old,new) if u==v])
        p['scope_note']+=' Only the obsolete eVisa air/sea-only clause is corrected. The VOA airport restriction and retained other conditions are outside this new review. The official port table repeats Darranga; no unique port count is asserted.'
    elif field=='fee':
        p.update(evidence=[dict(source_id='india_fee_table',source_url=FEE,quote=FEE_QUOTES[layer['route']['passport_nationality']]),dict(source_id='india_fee_table',source_url=FEE,quote='Country/Territory Wise e-Tourist Visa Fee (in US $)')])
        p['scope_note']+=' Exact current nationality and product column in the four-column government fee table; the selected official tariff is zero USD. Paid sibling columns and the additional bank-charge qualifier are unchanged.'
    elif field=='validity':
        p['evidence']=[dict(source_id='india_evisa',source_url=EVISA,quote=VALIDITY)]
        p['scope_note']+=' The source itself explicitly states one year as 365 days from ETA grant. No calendar-year conversion or per-visit stay inference is made.'
    elif field=='processing_time':
        p.update(status='not_published',reason=NP_REASON,evidence=[dict(source_id='india_evisa',source_url=EVISA,quote=LEAD),dict(source_id='india_mha_ports_processing',source_url=PIB,quote=PERFORMANCE)])
    else:raise PatchRejected('Unreviewed field')
    return p


def _own_proof(l,field,p=None):
    proof=_proof(l,field,p)
    if field=='processing_time':
        # Absence is an explicitly reviewed disposition, not a guessed value.
        return dict(status='not_published',verifier='ai',verified_at='2026-09-10',verified_by='Ellis AI official-source field review',source_url=EVISA,quote=LEAD,reason=NP_REASON,note=NP_REASON,subject=_subject(l['route'],p),verification_scope='numeric_processing_minimum_only',supporting_evidence=deepcopy(proof['evidence'][1:]),reviewed_source_ids=['india_evisa','india_mha_ports_processing'])
    out=field_provenance(proof,l['route'],field,p)
    if field=='fee':out.update(verification_scope='india_evisa_fee_table_cell',reviewed_value=dict(amount=0,currency='USD'))
    return out


def _port(s):
    return PORT+(' Biometrics are captured at immigration on arrival.' if s==OLD[0] else '')


def values(l):
    c=CONTRACT[l['cache_key']];g=l['merged_guidance'];out={}
    if c['port_indices']:
        ex=deepcopy(g['exceptions'])
        for j in c['port_indices']:
            if ex[j] not in OLD:raise PatchRejected('Original route port clause changed')
            ex[j]=_port(ex[j])
        out['exceptions']=ex
    if c['products']:
        ps=deepcopy(g['visa_products'])
        for item in c['products']:
            p=ps[item['index']];old=g['visa_products'][item['index']]
            if p['type']!=item['type'] or digest(p)!=item['sha256']:raise PatchRejected('Original product changed')
            fp=p.setdefault('field_provenance',{})
            if item['processing_np']:
                if p.get('processing_time') is not None or p.get('requirement_detail')!='evisa':raise PatchRejected('Processing absence scope changed')
                fp['processing_time']=_own_proof(l,'processing_time',old)
            if item['zero_fee']:
                if p.get('fee')!={'amount':0,'currency':'USD'}:raise PatchRejected('Reviewed zero tariff differs')
                fp['fee']=_own_proof(l,'fee',old)
            if item['validity_equivalence']:
                if p['validity']!='One year (365 days) from the date of grant of ETA':raise PatchRejected('Exact equivalent validity changed')
                fp['validity']=_own_proof(l,'validity',old)
            if item['port_indices']:
                for j in item['port_indices']:
                    if p['exceptions'][j] not in OLD:raise PatchRejected('Original product port clause changed')
                    p['exceptions'][j]=_port(p['exceptions'][j])
                fp['exceptions']=_own_proof(l,'exceptions',old)
        out['visa_products']=ps
    return out


def validate(spec,layers):
    if (not isinstance(spec,dict) or set(spec)!={'schema_version','kind','id','sources','routes'} or type(spec['schema_version']) is not int or spec['schema_version']!=1 or spec['kind']!='reviewed_india_completeness' or spec['id']!=REVIEW_ID or date.today()<date(2026,9,10)):
        raise PatchRejected('Unexpected India completeness contract/date')
    sources=_sources(spec)
    if digest(spec['sources'])!=SOURCE_CATALOG_SHA256 or set(sources)!=set(SOURCE_PINS) or any(any(sources[k].get(f)!=v for f,v in pins.items()) for k,pins in SOURCE_PINS.items()):raise PatchRejected('Exact source captures changed')
    if not isinstance(layers,list) or len(layers)!=17:raise PatchRejected('Exactly seventeen current routes required')
    current=_current_map(layers,set(CONTRACT))
    if not isinstance(spec['routes'],list) or len(spec['routes'])!=17 or {r.get('cache_key') for r in spec['routes']}!=set(CONTRACT):raise PatchRejected('Exactly seventeen reviewed instructions required')
    from scripts.convert_reviewed_general_batch import _check_proof
    for row in spec['routes']:
        if set(row)!={'cache_key','route','baseline_sha256','changes'}:raise PatchRejected('Extra instruction')
        l=current[row['cache_key']];c=CONTRACT[row['cache_key']]
        if any(k not in l for k in BASELINE_KEYS) or l['operator_entries'] or digest(row['route'])!=digest(c['route']) or digest(l['route'])!=digest(c['route']) or row['baseline_sha256']!=c['baseline_sha256'] or {k:digest(l[k]) for k in BASELINE_KEYS}!=c['baseline_sha256']:raise PatchRejected('Exact six-layer baseline changed')
        ps=l['merged_guidance'].get('visa_products') or [];names=[p.get('type') for p in ps]
        if len(names)!=len(set(names)) or not all(isinstance(n,str) and n for n in names):raise PatchRejected('Duplicate/missing product identity')
        v=values(l)
        if digest(row['changes'])!=digest(v):raise PatchRejected('Unreviewed fact/proof change')
        if c['port_indices']:_check_proof(_proof(l,'exceptions'),sources,l['route'],'exceptions',v['exceptions'])
        for pc in c['products']:
            p=ps[pc['index']]
            for field,needed,value in [('processing_time',pc['processing_np'],None),('fee',pc['zero_fee'],p.get('fee')),('validity',pc['validity_equivalence'],p.get('validity')),('exceptions',bool(pc['port_indices']),v['visa_products'][pc['index']].get('exceptions'))]:
                if needed:_check_proof(_proof(l,field,p),sources,l['route'],field,value,product=p)
    return current


def build_manifest(spec,layers):
    current=validate(spec,layers)
    return dict(schema_version=1,kind='india_completeness_exact_layers',specification=deepcopy(spec),routes=[dict(cache_key=k,baseline={f:deepcopy(current[k][f]) for f in BASELINE_KEYS}) for k in sorted(CONTRACT)])


def convert(manifest,layers):
    from app.visa_snapshot import verified_overrides as vo,tstation
    from app.visa_snapshot.kimi_primary import serve_time_invariants
    if not isinstance(manifest,dict) or set(manifest)!={'schema_version','kind','specification','routes'}:raise PatchRejected('Malformed manifest')
    baselines=[dict(deepcopy(r['baseline']),cache_key=r['cache_key']) for r in manifest['routes']]
    if digest(build_manifest(manifest['specification'],baselines))!=digest(manifest):raise PatchRejected('Prepared manifest changed')
    current=validate(manifest['specification'],layers);entries=[];previews=[]
    for k in sorted(CONTRACT):
        l=current[k];route=l['route'];identity=vo._key(route['passport_nationality'],'IND','tourism','ordinary_passport');prior=vo._parse_rows(l['seed_entries'],{}).get(identity)
        if not prior:raise PatchRejected('Seed ownership missing')
        old,checked=vo.merge_verified_fields(l['raw_guidance'],prior['fields'],source_url=prior['source_url']);old_prov=dict(prior['field_provenance'].get('disposition') or vo._provenance(prior),fields=sorted(checked),field_provenance=prior['field_provenance'])
        if digest(old)!=digest(l['merged_guidance']) or digest(old_prov)!=digest(l['source_provenance']):raise PatchRejected('Original canonical source reconstruction changed')
        v=values(l)
        if not v:
            previews.append(dict(cache_key=k,guidance=old,source_provenance=old_prov,records=tstation.records_for_route(route,old,old_prov),changed_fields=[]));continue
        if 'visa_products' in v and digest(prior['fields'].get('visa_products'))!=digest(old['visa_products']):raise PatchRejected('Product ownership changed')
        out=deepcopy(prior);out.update(route=dict(nationality=route['passport_nationality'],destination='IND',travel_purpose='tourism',travel_document_type='ordinary_passport'),partial_review=True,review_id=REVIEW_ID);out['fields'].update(v)
        if 'exceptions' in v:out['field_provenance']['exceptions']=vo._provenance(_own_proof(l,'exceptions'))
        parsed=vo._parse_rows([out],{}).get(identity)
        if not parsed or digest(parsed['fields'])!=digest(out['fields']) or digest(parsed['field_provenance'])!=digest(out['field_provenance']):raise PatchRejected('Loader altered fields/proofs')
        g,checked=vo.merge_verified_fields(l['raw_guidance'],parsed['fields'],source_url=parsed['source_url']);prov=dict(parsed['field_provenance'].get('disposition') or vo._provenance(parsed),fields=sorted(checked),field_provenance=parsed['field_provenance'])
        if digest({f:x for f,x in g.items() if f not in v})!=digest({f:x for f,x in old.items() if f not in v}):raise PatchRejected('Unreviewed route fields changed')
        if set(serve_time_invariants(g))-set(serve_time_invariants(old)):raise PatchRejected('New serving contradiction')
        rows=tstation.records_for_route(route,g,prov);before=tstation.records_for_route(route,old,old_prov)
        if len(rows)!=len(before) or [r['confidence_level'] for r in rows]!=[r['confidence_level'] for r in before]:raise PatchRejected('Product inventory or derived confidence changed')
        for a,b in zip(before,rows,strict=True):
            if a['info_validity']!=b['info_validity']:raise PatchRejected('Policy date changed')
        entries.append(out);previews.append(dict(cache_key=k,guidance=g,source_provenance=prov,records=rows,changed_fields=sorted(v)))
    return dict(schema_version=1,kind='reviewed_overlay_conversion',review_id=REVIEW_ID,entries=entries,status='detached; exact-layer preflight required'),dict(routes=previews,raw_writes=False,operator_writes=False,issue_changes=False,renew_fresh_until=False,confidence_changed=False,new_release=False)


def verify_prepared(spec,layers,prepared_manifest,prepared_overlay):
    m=build_manifest(spec,layers);o,r=convert(m,layers)
    if digest(m)!=digest(prepared_manifest) or digest(o)!=digest(prepared_overlay):raise PatchRejected('Prepared artifacts differ from full rebuild')
    return r
