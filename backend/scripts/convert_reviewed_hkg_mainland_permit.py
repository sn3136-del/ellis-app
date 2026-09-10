"""Detached exact-layer correction for the HKSAR Chinese-resident permit route.

The destination government proves the permit and its age-specific products.
Only three application fields use one exact captured, government-delegated
CTS document. No general operator/platform whitelist, issue disposition,
cache update, freshness renewal, or policy-expiry claim is introduced.
"""
from copy import deepcopy
from datetime import date
from app.visa_snapshot._reviewed_hkg_mainland_contract import CASE,SOURCES,BASELINES,AUTHORITY_LINKS
from app.visa_snapshot.reviewed_hkg_mainland_fields import FIELDS,expected_proof,SCOPE
from scripts.convert_reviewed_product_validity import BASELINE_KEYS,_sources
from scripts.convert_reviewed_product_patch import field_provenance,_subject
from scripts.prepare_reviewed_product_patch import PatchRejected,digest
MANIFEST='reviewed_hkg_mainland_permit_manifest_20260910.json'
OVERLAY='reviewed_hkg_mainland_permit_overlay_20260910.json'

def _current(layers):
    wanted=CASE['cache_key']
    selected=[x for x in layers if x.get('cache_key')==wanted]
    if len(selected)!=1:raise PatchRejected('Exact current permit route is missing or duplicated')
    for x in layers:
        if x.get('route')==CASE['route'] and x.get('cache_key')!=wanted:
            raise PatchRejected('Canonical route has a cache-key alias')
    layer=selected[0]
    if any(k not in layer for k in BASELINE_KEYS):raise PatchRejected('All six layers must be explicit')
    return layer

def _proof(field,approved,route,product=None):
    from app.visa_snapshot import verified_overrides as vo
    if approved.get('unknown_reason'):
        return dict(status='unknown',verifier='ai',source_url='',verified_at=None,verified_by='',note=approved['unknown_reason'])
    if field in FIELDS:return expected_proof(field,product)
    raw=dict(status='reviewed',verifier='ai',verified_at='2026-09-10',scope_note=approved['scope_note'],evidence=deepcopy(approved['evidence']))
    result=vo._provenance(field_provenance(raw,route,field,product))
    if product is None and field in ('disposition','requirement_detail'):
        result['verification_scope']=SCOPE
    return result

def _check_change(field,approved,sources,route,product=None):
    from scripts.convert_reviewed_general_batch import _check_proof
    if approved.get('unknown_reason'):
        if approved['new'] is not None or approved['evidence']:raise PatchRejected('An unknown cannot introduce a fact or NP credit')
        return
    proof=dict(status='reviewed',verifier='ai',verified_at='2026-09-10',scope_note=approved['scope_note'],evidence=deepcopy(approved['evidence']))
    _check_proof(proof,sources,route,field,approved['new'],product=product)

def _validate(spec,layer):
    from app.visa_snapshot.authority import hostname,is_government_host
    from scripts.convert_reviewed_general_batch import quote_literal
    if (set(spec)!={'schema_version','kind','id','sources','routes','authority_links'} or type(spec.get('schema_version')) is not int or spec.get('schema_version')!=1
            or spec.get('kind')!='reviewed_hkg_mainland_permit_fields' or spec.get('id')!='hkg-mainland-permit-20260910'):
        raise PatchRejected('Unexpected permit review specification')
    sources=_sources(spec)
    if set(sources)!=set(SOURCES) or any(digest({f:sources[k].get(f) for f in v})!=digest(v) for k,v in SOURCES.items()):
        raise PatchRejected('Exact reviewed source capture changed')
    # Both non-government pages are exact captured delegated permit-service
    # references. Their inclusion never changes government-host classification.
    for k,s in sources.items():
        if not is_government_host(hostname(s['url'])) and k not in {'cts_booking','cts_pdf'}:
            raise PatchRejected('Unreviewed non-government source')
        if s.get('checked_at')!='2026-09-10' or date.today()<date(2026,9,10):
            raise PatchRejected('Actual source-read date changed or is in the future')
    if not quote_literal('（一）公安部委托香港中旅集团负责香港居民申请通行证的受理工作。',sources['nia_guide']['text']):
        raise PatchRejected('Destination delegation evidence is missing')
    if digest(spec['authority_links'])!=digest(AUTHORITY_LINKS):
        raise PatchRejected('Exact government delegation/backlink capture changed')
    if any(not quote_literal(v,sources['hksar_chengdu']['text']) for v in ("For details, please refer to CTS's website and make telephone enquiry on (852) 2998 7888.",)):
        raise PatchRejected('Government backlink context is missing')
    rows=spec['routes']
    if not isinstance(rows,list) or len(rows)!=1:raise PatchRejected('Exactly one reviewed route is required')
    row=rows[0]
    if set(row)!={'cache_key','route','baseline_sha256','changes','products'} or row['cache_key']!=CASE['cache_key']:
        raise PatchRejected('Unexpected route contract')
    if row['route']!=CASE['route'] or row['route']!=layer['route'] or layer['operator_entries']:
        raise PatchRejected('Passport, nationality, residence, purpose or operator scope changed')
    if row['baseline_sha256']!=BASELINES or {k:digest(layer[k]) for k in BASELINE_KEYS}!=BASELINES:
        raise PatchRejected('Current six-layer baseline changed')
    changes=row['changes']
    if not isinstance(changes,list) or len(changes)!=len(CASE['changes']) or {c.get('field') for c in changes}!=set(CASE['changes']):
        raise PatchRejected('Reviewed route field set changed')
    for c in changes:
        f=c['field'];a=CASE['changes'][f]
        if (set(c)!=set(a)|{'field','old_raw','old_merged'} or digest({k:v for k,v in c.items() if k not in ('field','old_raw','old_merged')})!=digest(a)
                or c['old_raw']!=layer['raw_guidance'].get(f) or c['old_merged']!=layer['merged_guidance'].get(f)):
            raise PatchRejected('Reviewed route value or proof changed: '+f)
        _check_change(f,a,sources,row['route'])
    if digest(row['products'])!=digest(CASE['products']):raise PatchRejected('Reviewed permit product values or proofs changed')
    products=layer['merged_guidance'].get('visa_products') or []
    if [p.get('type') for p in products]!=[p['current_name'] for p in CASE['products']]:
        raise PatchRejected('The two existing permit products or their order changed')
    for old,specification in zip(products,CASE['products'],strict=True):
        product=dict(old,disposition='CONDITIONAL',requirement_detail='conditional_visa_free')
        for f,a in specification['changes'].items():_check_change(f,a,sources,row['route'],product)
    return sources

def build_manifest(spec,layers):
    layer=_current(layers);_validate(spec,layer)
    return dict(schema_version=1,kind='hkg_mainland_permit_exact_layers',specification=deepcopy(spec),
                routes=[dict(cache_key=CASE['cache_key'],baseline={k:deepcopy(layer[k]) for k in BASELINE_KEYS},
                             baseline_sha256={k:digest(layer[k]) for k in BASELINE_KEYS})])

def convert(manifest,current_layers):
    from app.visa_snapshot import verified_overrides as vo,tstation
    from app.visa_snapshot.kimi_primary import serve_time_invariants
    if set(manifest)!={'schema_version','kind','specification','routes'}:
        raise PatchRejected('Malformed permit manifest')
    baseline=[dict(deepcopy(x['baseline']),cache_key=x['cache_key']) for x in manifest['routes']]
    if digest(build_manifest(manifest['specification'],baseline))!=digest(manifest):
        raise PatchRejected('Manifest or baseline binding changed')
    layer=_current(current_layers);_validate(manifest['specification'],layer);route=layer['route']
    identity=vo._key('HKG','CHN','tourism','ordinary_passport')
    prior=vo._parse_rows(layer['seed_entries'],{}).get(identity)
    if not prior:raise PatchRejected('Missing previous field owners')
    before,checked=vo.merge_verified_fields(layer['raw_guidance'],prior['fields'],source_url=prior['source_url'])
    old_prov=dict(prior['field_provenance'].get('disposition') or vo._provenance(prior),fields=sorted(checked),field_provenance=prior['field_provenance'])
    if before!=layer['merged_guidance'] or old_prov!=layer['source_provenance']:
        raise PatchRejected('Canonical baseline reconstruction changed')
    output=deepcopy(prior)
    output.update(route=dict(nationality='HKG',destination='CHN',travel_purpose='tourism',travel_document_type='ordinary_passport'),
                  review_id=manifest['specification']['id'],partial_review=True)
    for f,a in CASE['changes'].items():
        output['fields'][f]=deepcopy(a['new']);output['field_provenance'][f]=_proof(f,a,route)
    products=deepcopy(before['visa_products'])
    for p,pspec in zip(products,CASE['products'],strict=True):
        p.update(disposition='CONDITIONAL',requirement_detail='conditional_visa_free')
        proofs=deepcopy(p.get('field_provenance') or {})
        for f,a in pspec['changes'].items():
            p[f]=deepcopy(a['new']);proofs[f]=_proof(f,a,route,p)
        p['field_provenance']=proofs
        decision=proofs['disposition']
        p.update(source_url=decision['source_url'],source_quote=decision['quote'],verified_at=decision['verified_at'],verifier='ai')
        for f in ('source_url','source_quote'):proofs[f]=deepcopy(decision)
    output['fields']['visa_products']=products
    anchor=deepcopy(output['field_provenance']['disposition'])
    anchor.update(status='partial',verification_scope='product_fields_only',note='Each age-specific Mainland Travel Permit has its own exact-subject source proofs. Unknown stay and ancillary facts remain unknown; this container is not a blanket review.')
    anchor.pop('subject',None);output['field_provenance']['visa_products']=anchor
    decision=output['field_provenance']['disposition']
    output.update({k:deepcopy(decision[k]) for k in ('source_url','verified_at','verified_by','verifier','note')})
    parsed=vo._parse_rows([output],{}).get(identity)
    if not parsed:raise PatchRejected('Serving loader rejected the scoped permit review')
    changed=set(CASE['changes'])|{'visa_products'}
    if parsed['fields']!=output['fields'] or parsed['field_provenance']!=output['field_provenance']:
        raise PatchRejected('Serving loader dropped a value or changed its actual source ownership')
    if ({k:v for k,v in parsed['fields'].items() if k not in changed}!={k:v for k,v in prior['fields'].items() if k not in changed}
            or {k:v for k,v in parsed['field_provenance'].items() if k not in changed}!={k:v for k,v in prior['field_provenance'].items() if k not in changed}):
        raise PatchRejected('Unrelated seed fact or authorship changed')
    merged,checked=vo.merge_verified_fields(layer['raw_guidance'],parsed['fields'],source_url=parsed['source_url'])
    if {k:v for k,v in merged.items() if k not in changed}!={k:v for k,v in before.items() if k not in changed}:
        raise PatchRejected('Unrelated canonical guidance changed')
    if set(serve_time_invariants(merged))-set(serve_time_invariants(before)):
        raise PatchRejected('Review introduced an integrity conflict')
    provenance=dict(parsed['field_provenance']['disposition'],fields=sorted(checked),field_provenance=parsed['field_provenance'])
    rows=tstation.records_for_route(route,merged,provenance)
    from app.visa_snapshot.records_guard import apply_records_hold
    preview=dict(cache_key=CASE['cache_key'],route=route,guidance=merged,source_verified=provenance)
    guarded=apply_records_hold(route,deepcopy(preview))
    if guarded.get('held'):raise PatchRejected('Reviewed permit products remain unsupported by the shared guard')
    report=dict(cache_key=CASE['cache_key'],changed_values=sorted(k for k in changed if before.get(k)!=merged.get(k)),records=rows,
                retains_raw_history=True,retains_pending_and_issues=True,renew_fresh_until=False,
                unknown_fields=sorted(f for f,a in CASE['changes'].items() if a.get('unknown_reason')),
                no_not_published_credit=True,shared_guard_held=False)
    return dict(schema_version=1,kind='reviewed_overlay_conversion',review_id=manifest['specification']['id'],entries=[output],
                status='detached; exact-layer deployment preflight required'),[report],[preview]

def verify_prepared(spec,layers,prepared_manifest,prepared_overlay):
    manifest=build_manifest(spec,layers);overlay,reports,previews=convert(manifest,layers)
    if digest(manifest)!=digest(prepared_manifest) or digest(overlay)!=digest(prepared_overlay):raise PatchRejected('Complete staged permit output differs from fresh rebuild')
    return reports,previews
