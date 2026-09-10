"""Detached exact three-route, six-layer-bound official field correction.

No cache/operator/issue writes, TTL renewal, blanket grade, or raw restoration.
Existing products survive; only the two already-seeded US B1 products receive
new independent proof. Unknown ancillary facts receive no absence credit.
"""
from copy import deepcopy
from datetime import date
from scripts._reviewed_indonesia_taiwan_contract import APPROVED,SOURCES,BASELINES,SOURCE_CATALOG_SHA256
from scripts.convert_reviewed_product_validity import BASELINE_KEYS,_current_map,_sources
from scripts.convert_reviewed_product_patch import field_provenance,_subject
from scripts.prepare_reviewed_product_patch import PatchRejected,digest
MANIFEST='reviewed_indonesia_taiwan_manifest_20260910.json'
OVERLAY='reviewed_indonesia_taiwan_overlay_20260910.json'

def _proof(route,field,item,product=None):
    if item.get('unknown_reason'):
        return dict(status='unknown',verifier='ai',reason=item['unknown_reason'])
    return dict(status='reviewed',verifier='ai',verified_at='2026-09-10',subject=_subject(route,product),scope_note='AI review of this exact ordinary-passport tourism field. Indonesia selector evidence was freshly read after selecting the exact passport and A1/B1 product. A1 is distinct from B1; pre-issued B1 validity is not a stay. US Taiwan passport validity is through intended stay. No other field, product, absence disposition, policy expiry or cache freshness is certified.',evidence=deepcopy(item['evidence']))

def _validate(spec,layers):
    from scripts.convert_reviewed_general_batch import _check_proof
    if (set(spec)!={'schema_version','kind','id','sources','routes'} or type(spec['schema_version']) is not int or spec['schema_version']!=1 or spec['kind']!='reviewed_indonesia_taiwan_fields' or spec['id']!='us-hk-indonesia-taiwan-20260910'):
        raise PatchRejected('Unexpected reviewed field contract')
    if date.today()<date(2026,9,10):raise PatchRejected('Review date is in the future')
    sources=_sources(spec)
    if digest(spec['sources'])!=SOURCE_CATALOG_SHA256:raise PatchRejected('Source metadata or text changed')
    if set(sources)!=set(SOURCES) or any({k:sources[s][k] for k in ('url','sha256')}!=pins for s,pins in SOURCES.items()) or any(s.get('checked_at')!='2026-09-10' for s in sources.values()):
        raise PatchRejected('Captured official catalog changed')
    if len(layers)!=3:raise PatchRejected('Exactly three canonical layers are required')
    current=_current_map(layers,set(APPROVED));routes=spec['routes']
    if not isinstance(routes,list) or len(routes)!=3 or {r.get('cache_key') for r in routes}!=set(APPROVED):raise PatchRejected('Exactly three reviewed routes are required')
    for r in routes:
        key=r['cache_key'];case=APPROVED[key];l=current[key]
        if set(r)!={'cache_key','route','baseline_sha256','changes','product_changes','form_migration'}:raise PatchRejected('Unexpected instruction')
        if digest(r['route'])!=digest(case['route']) or digest(r['route'])!=digest(l['route']) or l['operator_entries']:raise PatchRejected('Route or operator scope changed')
        if any(k not in l for k in BASELINE_KEYS) or r['baseline_sha256']!=BASELINES[key] or BASELINES[key]!={k:digest(l[k]) for k in BASELINE_KEYS}:raise PatchRejected('Six-layer baseline changed')
        expected=[dict(field=f,old_raw=l['raw_guidance'].get(f),old_merged=l['merged_guidance'].get(f),**v) for f,v in case['changes'].items()]
        if digest(r['changes'])!=digest(expected) or digest(r['product_changes'])!=digest(case['product_changes']) or digest(r['form_migration'])!=digest(case['form_migration']):raise PatchRejected('Closed reviewed field value/evidence changed')
        for f,a in case['changes'].items():_check_proof(_proof(r['route'],f,a),sources,r['route'],f,a['new'])
        existing=l['merged_guidance'].get('visa_products') or []
        names=[p.get('type') for p in existing]
        if len(set(names))!=len(names):raise PatchRejected('Ambiguous product identity')
        for patch in case['product_changes']:
            if names.count(patch['type'])!=1:raise PatchRejected('Exact existing product missing')
            product=deepcopy(existing[names.index(patch['type'])]);product.update({f:a['new'] for f,a in patch['changes'].items()})
            for f,a in patch['changes'].items():_check_proof(_proof(r['route'],f,a,product),sources,r['route'],f,a['new'],product=product)
    return current

def build_manifest(spec,layers):
    current=_validate(spec,layers)
    return dict(schema_version=1,kind='indonesia_taiwan_fields_exact_layers',specification=deepcopy(spec),routes=[dict(cache_key=k,baseline={f:deepcopy(current[k][f]) for f in BASELINE_KEYS}) for k in sorted(APPROVED)])

def convert(manifest,layers):
    from app.visa_snapshot import verified_overrides as vo,tstation
    from app.visa_snapshot.kimi_primary import serve_time_invariants
    if set(manifest)!={'schema_version','kind','specification','routes'}:raise PatchRejected('Malformed manifest')
    baseline=[dict(deepcopy(r['baseline']),cache_key=r['cache_key']) for r in manifest['routes']]
    if digest(build_manifest(manifest['specification'],baseline))!=digest(manifest):raise PatchRejected('Manifest binding changed')
    current=_validate(manifest['specification'],layers);entries=[];previews=[]
    for r in manifest['specification']['routes']:
        l=current[r['cache_key']];route=l['route'];case=APPROVED[r['cache_key']];identity=vo._key(route['passport_nationality'],route['destination_country'],'tourism','ordinary_passport')
        prior=vo._parse_rows(l['seed_entries'],{}).get(identity)
        if not prior:raise PatchRejected('Original seed ownership missing')
        before,checked=vo.merge_verified_fields(l['raw_guidance'],prior['fields'],source_url=prior['source_url'])
        prov=dict(prior['field_provenance'].get('disposition') or vo._provenance(prior),fields=sorted(checked),field_provenance=prior['field_provenance'])
        if digest(before)!=digest(l['merged_guidance']) or digest(prov)!=digest(l['source_provenance']):raise PatchRejected('Canonical baseline reconstruction differs')
        out=deepcopy(prior);out.update(route=dict(nationality=route['passport_nationality'],destination=route['destination_country'],travel_purpose='tourism',travel_document_type='ordinary_passport'),review_id=manifest['specification']['id'],partial_review=True)
        for f,a in case['changes'].items():
            out['fields'][f]=deepcopy(a['new']);out['field_provenance'][f]=vo._provenance(field_provenance(_proof(route,f,a),route,f))
            if a.get('unknown_reason'):out['field_provenance'][f].pop('subject',None)
        product_fields={}
        if case['product_changes']:
            if prior['fields'].get('visa_products')!=before.get('visa_products'):raise PatchRejected('Products must already have explicit seed ownership')
            products=deepcopy(before['visa_products'])
            for patch in case['product_changes']:
                p=next(p for p in products if p['type']==patch['type']);p.update({f:deepcopy(a['new']) for f,a in patch['changes'].items()})
                proofs=p.setdefault('field_provenance',{})
                for f,a in patch['changes'].items():proofs[f]=field_provenance(_proof(route,f,a,p),route,f,p)
                decision=proofs['disposition'];p['source_url']=decision['source_url'];p['source_quote']=decision['quote']
                proofs['source_url']=deepcopy(decision);proofs['source_quote']=deepcopy(decision)
                product_fields[p['type']]=set(patch['changes'])|{'source_url','source_quote','field_provenance'}
            out['fields']['visa_products']=products
            # Preserve its old container attribution; the product proof owns
            # exactly each newly reviewed fact, never a sibling's eligibility.
        parsed=vo._parse_rows([out],{}).get(identity)
        if not parsed or digest(parsed['fields'])!=digest(out['fields']) or digest(parsed['field_provenance'])!=digest(out['field_provenance']):raise PatchRejected('Loader changed reviewed fields/proofs: '+str((r['cache_key'],{k:(out['fields'].get(k),parsed['fields'].get(k)) for k in set(out['fields'])|set(parsed['fields']) if out['fields'].get(k)!=parsed['fields'].get(k)},{k:(out['field_provenance'].get(k),parsed['field_provenance'].get(k)) for k in set(out['field_provenance'])|set(parsed['field_provenance']) if out['field_provenance'].get(k)!=parsed['field_provenance'].get(k)})))
        after,checked=vo.merge_verified_fields(l['raw_guidance'],parsed['fields'],source_url=parsed['source_url'])
        proof=dict(parsed['field_provenance'].get('disposition') or vo._provenance(parsed),fields=sorted(checked),field_provenance=parsed['field_provenance'])
        changed=set(case['changes'])|({'visa_products'} if case['product_changes'] else set())
        a={k:v for k,v in before.items() if k not in changed};b={k:v for k,v in after.items() if k not in changed}
        # Plain exemption normalization may remove only a truly empty form
        # container; no nonempty content or product is silently discarded.
        if a.get('forms')==[] and 'forms' not in b:a.pop('forms')
        migration=case['form_migration']
        if migration:
            if a.get('forms')!=migration['old'] or b.get('forms') is not None or after.get('entry_requirements')!=case['changes']['entry_requirements']['new'] or after.get('arrival_card')!=case['changes']['arrival_card']['new']:
                raise PatchRejected('Exact customs-form migration differs or lost replacement instructions')
            a.pop('forms');b.pop('forms',None)
        if digest(a)!=digest(b):raise PatchRejected('An unrelated merged field changed: '+str({k:(a.get(k),b.get(k)) for k in set(a)|set(b) if a.get(k)!=b.get(k)}))
        if len(before.get('visa_products') or [])!=len(after.get('visa_products') or []):raise PatchRejected('Product count changed')
        for old,new in zip(before.get('visa_products') or [],after.get('visa_products') or []):
            fs=product_fields.get(old['type'],set());a={k:v for k,v in old.items() if k not in fs};b={k:v for k,v in new.items() if k not in fs}
            if digest(a)!=digest(b):raise PatchRejected('Unreviewed product fact changed')
            if fs:
                pf=set(next(p for p in case['product_changes'] if p['type']==old['type'])['changes'])|{'source_url','source_quote'}
                if {k:v for k,v in (old.get('field_provenance') or {}).items() if k not in pf}!={k:v for k,v in (new.get('field_provenance') or {}).items() if k not in pf}:raise PatchRejected('Unreviewed product proof changed')
        if set(serve_time_invariants(after))-set(serve_time_invariants(before)):raise PatchRejected('New serving contradiction')
        rows=tstation.records_for_route(route,after,proof)
        entries.append(out);previews.append(dict(cache_key=r['cache_key'],route=route,guidance=after,source_verified=proof,records=rows,changed_fields=sorted(changed),legacy_form_migration=deepcopy(case['form_migration']),raw_writes=False,operator_writes=False,issue_changes=False,renew_fresh_until=False))
    return dict(schema_version=1,kind='reviewed_overlay_conversion',review_id=manifest['specification']['id'],entries=entries,status='detached; exact-layer deployment preflight required'),previews

def verify_prepared(spec,layers,prepared_manifest,prepared_overlay):
    manifest=build_manifest(spec,layers);overlay,preview=convert(manifest,layers)
    if digest(manifest)!=digest(prepared_manifest) or digest(overlay)!=digest(prepared_overlay):raise PatchRejected('Complete prepared manifest/overlay differs from fresh rebuild')
    return preview
