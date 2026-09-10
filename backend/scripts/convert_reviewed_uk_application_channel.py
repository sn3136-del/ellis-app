"""Detached UK filing-stage correction, bound to exact current 17 route contexts.

Six Standard Visitor defaults and sixty already-owned Standard Visitor products
start with online filing. Their later VAC appointment, biometrics, submission
instructions and all eleven ETA defaults stay unchanged. No database, operator,
issue, grade, policy date, raw guidance or freshness writes are performed.
"""
from copy import deepcopy
from datetime import date
from scripts._reviewed_uk_channel_contract import CONTRACT, SOURCES, SOURCE_CATALOG_SHA256, QUOTES
from scripts.convert_reviewed_product_validity import BASELINE_KEYS, _current_map, _sources
from scripts.convert_reviewed_product_patch import field_provenance, _subject
from scripts.prepare_reviewed_product_patch import PatchRejected, digest

MANIFEST = 'reviewed_uk_application_channel_manifest_20260910.json'
OVERLAY = 'reviewed_uk_application_channel_overlay_20260910.json'
REVIEW_ID = 'uk-standard-visitor-filing-stage-20260910'
SCOPE = ('AI review of the initial online filing stage for this exact existing Standard Visitor visa. '
         'The subsequent booked visa application centre appointment for identity, fingerprints, photograph '
         'and supporting documents remains required and is preserved in the existing workflow and detail. '
         'This does not review nationality eligibility, ETA, fees, stay, visa validity, policy expiry, '
         'other product fields or cache freshness.')


def proof(route, product=None):
    return dict(status='reviewed', verifier='ai', verified_at='2026-09-10',
                subject=_subject(route, product), scope_note=SCOPE,
                evidence=[dict(source_id='overview', source_url=SOURCES['overview']['url'],
                               quote=QUOTES['overview'])])


def _validate(spec, layers):
    from scripts.convert_reviewed_general_batch import _check_proof
    if (not isinstance(spec, dict) or set(spec) != {'schema_version','kind','id','sources','routes'}
            or type(spec['schema_version']) is not int or spec['schema_version'] != 1
            or spec['kind'] != 'reviewed_uk_application_channel' or spec['id'] != REVIEW_ID):
        raise PatchRejected('Unexpected reviewed UK filing-stage contract')
    if date.today() < date(2026,9,10):
        raise PatchRejected('Source review date is in the future')
    sources = _sources(spec)
    if digest(spec['sources']) != SOURCE_CATALOG_SHA256 or set(sources) != set(SOURCES):
        raise PatchRejected('Captured official UK source catalog changed')
    for sid, pins in SOURCES.items():
        source = sources[sid]
        if any(source.get(k) != v for k,v in pins.items()) or source['checked_at'] != '2026-09-10' or QUOTES[sid] not in source['text']:
            raise PatchRejected('Exact current filing and VAC stage evidence changed')
    if not isinstance(layers,list) or len(layers) != 17:
        raise PatchRejected('Exactly seventeen current canonical contexts required')
    current = _current_map(layers, set(CONTRACT))
    routes = spec['routes']
    if not isinstance(routes,list) or len(routes) != 17 or {r.get('cache_key') for r in routes} != set(CONTRACT):
        raise PatchRejected('Exactly seventeen reviewed route instructions required')
    for row in routes:
        key=row['cache_key']; c=CONTRACT[key]; layer=current[key]
        if set(row) != {'cache_key','route','baseline_sha256','route_channel','product_changes'}:
            raise PatchRejected('Unexpected field or proof instruction')
        if digest(row['route']) != digest(c['route']) or digest(layer['route']) != digest(c['route']):
            raise PatchRejected('Passport, residence, purpose, document or arrival scope changed')
        if any(k not in layer for k in BASELINE_KEYS) or layer['operator_entries']:
            raise PatchRejected('Every six-layer baseline must be explicit and operator-free')
        expected_baseline = {k:digest(layer[k]) for k in BASELINE_KEYS}
        if row['baseline_sha256'] != c['baseline_sha256'] or expected_baseline != c['baseline_sha256']:
            raise PatchRejected('Current six-layer baseline differs: '+key)
        required = {'old':'visa_center','new':'online_portal'} if c['route_channel'] else None
        products = [dict(p,field='application_channel',old='visa_center',new='online_portal') for p in c['products']]
        if digest(row['route_channel']) != digest(required) or digest(row['product_changes']) != digest(products):
            raise PatchRejected('Unreviewed route or product field/value/identity')
        g=layer['merged_guidance']; ps=g.get('visa_products') or []
        if c['route_channel']:
            if (g.get('disposition') != 'VISA_REQUIRED' or g.get('application_channel') != 'visa_center'
                    or g.get('route_workflow_type') != 'visa_center_submission'
                    or g.get('appointment_required') is not True or g.get('biometrics_required') is not True
                    or not str(g.get('application_channel_detail')).startswith('Apply online, then attend')):
                raise PatchRejected('Expected online-then-VAC workflow scope is absent')
            _check_proof(proof(row['route']),sources,row['route'],'application_channel','online_portal')
        for patch in c['products']:
            p=ps[patch['index']]
            if (digest(p) != patch['old_sha256'] or p.get('type') != patch['type']
                    or sum(x.get('type') == patch['type'] for x in ps) != 1
                    or p.get('application_channel') != 'visa_center'
                    or not str(p.get('application_channel_detail')).startswith('Apply online, then attend')):
                raise PatchRejected('Exact existing Standard Visitor product changed')
            _check_proof(proof(row['route'],p),sources,row['route'],'application_channel','online_portal',product=p)
    return current


def build_manifest(spec,layers):
    current=_validate(spec,layers)
    return dict(schema_version=1,kind='uk_application_channel_exact_layers',specification=deepcopy(spec),
                routes=[dict(cache_key=k,baseline={f:deepcopy(current[k][f]) for f in BASELINE_KEYS}) for k in sorted(CONTRACT)])


def _only_channel(before,after,route_channel,product_names):
    a,b=deepcopy(before),deepcopy(after)
    if route_channel:
        a.pop('application_channel',None);b.pop('application_channel',None)
    aps,bps=a.get('visa_products') or [],b.get('visa_products') or []
    if len(aps)!=len(bps):raise PatchRejected('Product inventory changed')
    for p,q in zip(aps,bps,strict=True):
        if p['type'] not in product_names:continue
        for x in (p,q):
            x.pop('application_channel',None)
            if isinstance(x.get('field_provenance'),dict):
                x['field_provenance'].pop('application_channel',None)
                if not x['field_provenance']:x.pop('field_provenance')
    if digest(a)!=digest(b):raise PatchRejected('Unreviewed field, workflow, condition, product or metadata changed')


def convert(manifest,layers):
    from app.visa_snapshot import verified_overrides as vo,tstation
    from app.visa_snapshot.kimi_primary import serve_time_invariants
    if not isinstance(manifest,dict) or set(manifest)!={'schema_version','kind','specification','routes'}:
        raise PatchRejected('Malformed exact-layer UK manifest')
    baselines=[dict(deepcopy(r['baseline']),cache_key=r['cache_key']) for r in manifest['routes']]
    if digest(build_manifest(manifest['specification'],baselines))!=digest(manifest):
        raise PatchRejected('Prepared UK manifest differs from the exact reviewed contract')
    current=_validate(manifest['specification'],layers);entries=[];previews=[]
    for key in sorted(CONTRACT):
        l=current[key];route=l['route'];c=CONTRACT[key]
        identity=vo._key(route['passport_nationality'],'GBR','tourism','ordinary_passport')
        prior=vo._parse_rows(l['seed_entries'],{}).get(identity)
        if not prior:raise PatchRejected('Original seed ownership missing')
        before,checked=vo.merge_verified_fields(l['raw_guidance'],prior['fields'],source_url=prior['source_url'])
        prov=dict(prior['field_provenance'].get('disposition') or vo._provenance(prior),fields=sorted(checked),field_provenance=prior['field_provenance'])
        if digest(before)!=digest(l['merged_guidance']) or digest(prov)!=digest(l['source_provenance']):
            raise PatchRejected('Canonical guidance/proof baseline cannot be reconstructed')
        names={p['type'] for p in c['products']}; out=deepcopy(prior)
        if c['route_channel']:
            out['fields']['application_channel']='online_portal'
            out['field_provenance']['application_channel']=vo._provenance(field_provenance(proof(route),route,'application_channel'))
        if names:
            if digest(prior['fields'].get('visa_products'))!=digest(before.get('visa_products')):
                raise PatchRejected('Existing products must already be wholly seed-owned')
            products=deepcopy(before['visa_products'])
            for p in products:
                if p['type'] not in names:continue
                p['application_channel']='online_portal'
                p.setdefault('field_provenance',{})['application_channel']=field_provenance(proof(route,p),route,'application_channel',p)
            out['fields']['visa_products']=products
        expected=deepcopy(out)
        out.update(route=dict(nationality=route['passport_nationality'],destination='GBR',travel_purpose='tourism',travel_document_type='ordinary_passport'),review_id=REVIEW_ID,partial_review=True,field_review_scope='initial_standard_visitor_filing_channel_only')
        parsed=vo._parse_rows([out],{}).get(identity)
        if digest(parsed)!=digest(expected):raise PatchRejected('Serving loader changed prepared facts, metadata or authorship')
        after,checked2=vo.merge_verified_fields(l['raw_guidance'],parsed['fields'],source_url=parsed['source_url'])
        after_prov=dict(parsed['field_provenance'].get('disposition') or vo._provenance(parsed),fields=sorted(checked2),field_provenance=parsed['field_provenance'])
        if set(checked2)!=set(checked):raise PatchRejected('New unrelated route field verification credit')
        _only_channel(before,after,c['route_channel'],names)
        old_p,new_p=deepcopy(prov),deepcopy(after_prov)
        if c['route_channel']:
            old_p['field_provenance'].pop('application_channel',None);new_p['field_provenance'].pop('application_channel',None)
        if digest(old_p)!=digest(new_p):raise PatchRejected('Unreviewed route proof changed')
        if serve_time_invariants(after)!=serve_time_invariants(before):raise PatchRejected('Independent serving invariant changed')
        if c['route_channel'] or names:entries.append(out)
        previews.append(dict(cache_key=key,route=route,guidance=after,source_provenance=after_prov,
                             records=tstation.records_for_route(route,after,after_prov),
                             route_channel_changed=c['route_channel'],product_channels_changed=len(names)))
    report=dict(routes=previews,contexts_checked=17,route_channels_changed=6,product_channels_changed=60,
                eta_defaults_unchanged=11,unchanged_contexts_without_overlay=2,raw_writes=False,operator_writes=False,
                issue_changes=False,pending_changes=False,renew_fresh_until=False,confidence_changed=False,new_release=False)
    return dict(schema_version=1,kind='reviewed_overlay_conversion',review_id=REVIEW_ID,entries=entries,
                status='detached; exact six-layer deployment preflight required'),report


def verify_prepared(spec,layers,prepared_manifest,prepared_overlay):
    manifest=build_manifest(spec,layers);overlay,report=convert(manifest,layers)
    if digest(manifest)!=digest(prepared_manifest) or digest(overlay)!=digest(prepared_overlay):
        raise PatchRejected('Complete prepared manifest/overlay differs from current verified rebuild')
    return report
