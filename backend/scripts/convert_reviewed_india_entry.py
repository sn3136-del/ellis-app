"""Exact reviewed India border forms across 17 ordinary-tourist station routes.

Only arrival-card data, appended entry instructions, and the same appended
product entry instructions may change. All visa eligibility, prices, seasons,
validity, stays, conditions, unrelated proofs and confidence remain untouched.
This converter does not clear issues, write raw/operator records or renew TTL.
"""
from copy import deepcopy
from datetime import date
from scripts.convert_reviewed_product_validity import BASELINE_KEYS, _current_map, _sources
from scripts.convert_reviewed_product_patch import field_provenance, _subject
from scripts.prepare_reviewed_product_patch import PatchRejected, digest
from scripts._reviewed_india_entry_contract import BASELINES, SOURCE_PINS

KEYS=set(BASELINES)
HOME='https://indianvisaonline.gov.in/'
EVISA='https://indianvisaonline.gov.in/evisa/tvoa.html'
CARD_QUOTE='Foreign Nationals and OCI/eOCI cardholders must complete and submit the eArrival Card ( https://indianvisaonline.gov.in/earrival/ ).'
WINDOW_QUOTE="Foreigners and OCI Card holders can complete and submit the e-Arrival card online within 72 hours before their arrival in India at boi.gov.in or indianvisaonline.gov.in or via official 'Indian Visa Su-Swagatam' Mobile App. This is for arrival information, not a visa."
PIB='https://www.pib.gov.in/PressReleasePage.aspx?PRID=2277639&lang=1&reg=48'
DECL_QUOTE='Developed in collaboration with the Directorate General of Health Services (DGHS), Ministry of Health & Family Welfare, the portal enables International arriving passengers to submit a mandatory online Health Self-Declaration — covering 21-day travel history, exposure history and related symptoms, if any — prior to immigration clearance.'
DECL_TIMING_QUOTE='Airsuvidha Self Declaration Form (SDF) can be completed 24 hours in advance before arrival to India. Passengers are requested to fill the forms before boarding of the flight , during web check-in for swift clearance on arrival and only required to show the downloaded SDF at the International Travel Health Desk or Immigration counter.'
DECL_CONTEXT_QUOTE='The Ministry of Civil Aviation and Delhi International Airport Limited (DIAL) today launched AIR SUVIDHA 2.0, an upgraded contactless Passenger Health Self-Declaration Portal, to strengthen public health surveillance at Points of Entry in response to the ongoing Ebola disease outbreak.'
CARD='Complete the e-Arrival Card online within 72 hours before arrival in India at https://indianvisaonline.gov.in/earrival/. This arrival information does not replace a visa.'
DECL='For international arrivals by air, the Air Suvidha 2.0 health self-declaration is mandatory before immigration clearance. Complete it at https://airsuvidha.civilaviation.gov.in/ before boarding your flight, as requested by the Ministry of Civil Aviation during web check-in. The form can be completed 24 hours in advance before arrival in India. Show the downloaded declaration at the International Travel Health Desk or immigration counter.'
ARRIVAL={'required':True,'name':'e-Arrival Card','submission_window':'Complete online within 72 hours before arrival in India at https://indianvisaonline.gov.in/earrival/.'}


def append_entry(value):
    if value is None: return CARD+' '+DECL
    if isinstance(value,str): return value.rstrip()+' '+CARD+' '+DECL
    if isinstance(value,list) and all(isinstance(v,str) for v in value): return deepcopy(value)+[CARD,DECL]
    raise PatchRejected('Unreviewed entry-information shape')


def proof(layer, field, product=None):
    evidence=[dict(source_id='india_home',source_url=HOME,quote=CARD_QUOTE),dict(source_id='india_evisa',source_url=EVISA,quote=WINDOW_QUOTE)]
    if field=='entry_requirements':
        evidence.extend(dict(source_id='india_pib_airsuvidha',source_url=PIB,quote=q) for q in (DECL_CONTEXT_QUOTE,DECL_QUOTE,DECL_TIMING_QUOTE))
    p=dict(status='reviewed',verifier='ai',verified_at='2026-09-10',subject=_subject(layer['route'],product),
        scope_note='AI review of the e-Arrival Card for foreign nationals and the separate Air Suvidha 2.0 declaration for international air arrivals only. Land/sea applicability of Air Suvidha is not established by this evidence. Mandatory before immigration clearance; before-boarding completion is requested, and 24 hours in advance is permissive. No visa/product eligibility, price, policy expiry, grade, or freshness certification.',evidence=evidence)
    if field=='entry_requirements':
        prior=(product if product is not None else layer['merged_guidance']).get(field)
        retained=deepcopy(prior) if isinstance(prior,list) else [prior] if prior else []
        p.update(verified_elements=[CARD,DECL],retained_unverified_elements=retained)
    return p


def reviewed_values(layer):
    products=deepcopy(layer['merged_guidance'].get('visa_products') or [])
    for p in products:
        own_proof=proof(layer,'entry_requirements',p)
        p['entry_requirements']=append_entry(p.get('entry_requirements'))
        p.setdefault('field_provenance',{})['entry_requirements']=field_provenance(own_proof,layer['route'],'entry_requirements',p)
    result={'arrival_card':deepcopy(ARRIVAL),'entry_requirements':append_entry(layer['merged_guidance'].get('entry_requirements'))}
    if products:result['visa_products']=products
    return result


def validate(spec,layers):
    if set(spec)!={'schema_version','kind','id','sources','routes'} or type(spec['schema_version']) is not int or spec['schema_version']!=1 or spec['kind']!='reviewed_india_entry_forms' or spec['id']!='india-entry-forms-20260910':
        raise PatchRejected('Unexpected India entry review specification')
    if date.today()<date(2026,9,10):raise PatchRejected('Source review is in the future')
    sources=_sources(spec)
    if {k:(v['url'],v['sha256'],v.get('capture_body_sha256'),v.get('checked_at')) for k,v in sources.items()}!=SOURCE_PINS:
        raise PatchRejected('Sources differ from the exact reviewed captures')
    current=_current_map(layers,KEYS)
    if len(layers)!=17 or len(spec['routes'])!=17 or {r.get('cache_key') for r in spec['routes']}!=KEYS:
        raise PatchRejected('Exactly the 17 reviewed canonical routes are required')
    from scripts.convert_reviewed_general_batch import _check_proof
    for r in spec['routes']:
        if set(r)!={'cache_key','route','baseline_sha256','changes'}:raise PatchRejected('Extra route instruction')
        l=current[r['cache_key']]
        if any(k not in l for k in BASELINE_KEYS) or {k:digest(l[k]) for k in BASELINE_KEYS}!=BASELINES[r['cache_key']] or r['baseline_sha256']!=BASELINES[r['cache_key']] or digest(r['route'])!=digest(l['route']):
            raise PatchRejected('Exact six-layer reviewed baseline changed')
        if l['operator_entries']:raise PatchRejected('Operator-owned route is outside this review')
        names=[p.get('type') for p in l['merged_guidance'].get('visa_products') or []]
        if any(not isinstance(n,str) or not n for n in names) or len(set(names))!=len(names):raise PatchRejected('Missing/duplicate product identity')
        values=reviewed_values(l)
        expected=[dict(field=f,old_raw=l['raw_guidance'].get(f),old_merged=l['merged_guidance'].get(f),new=v) for f,v in values.items()]
        if digest(r['changes'])!=digest(expected):raise PatchRejected('Changed facts or product scope differ from reviewed entry correction')
        for field in ('arrival_card','entry_requirements'):
            _check_proof(proof(l,field),sources,l['route'],field,values[field])
        for old,new in zip(l['merged_guidance'].get('visa_products') or [],values.get('visa_products') or []):
            _check_proof(proof(l,'entry_requirements',old),sources,l['route'],'entry_requirements',new['entry_requirements'],product=old)
    return current


def build_manifest(spec,layers):
    current=validate(spec,layers)
    return dict(schema_version=1,kind='india_entry_forms_exact_layers',specification=deepcopy(spec),routes=[dict(cache_key=k,baseline={f:deepcopy(current[k][f]) for f in BASELINE_KEYS}) for k in sorted(KEYS)])


def convert(manifest,layers):
    from app.visa_snapshot import verified_overrides as vo,tstation
    from app.visa_snapshot.kimi_primary import serve_time_invariants
    baseline=[dict(r['baseline'],cache_key=r['cache_key']) for r in manifest['routes']]
    if digest(build_manifest(manifest['specification'],baseline))!=digest(manifest):raise PatchRejected('Altered manifest')
    current=validate(manifest['specification'],layers);entries=[];previews=[]
    for r in manifest['specification']['routes']:
        l=current[r['cache_key']];route=l['route'];key=vo._key(route['passport_nationality'],'IND','tourism','ordinary_passport')
        prior=vo._parse_rows(l['seed_entries'],{}).get(key)
        if not prior:raise PatchRejected('Original seed ownership missing')
        original,checked=vo.merge_verified_fields(l['raw_guidance'],prior['fields'],source_url=prior['source_url'])
        prior_prov=dict(prior['field_provenance'].get('disposition') or vo._provenance(prior),fields=sorted(checked),field_provenance=prior['field_provenance'])
        if digest(original)!=digest(l['merged_guidance']) or digest(prior_prov)!=digest(l['source_provenance']):raise PatchRejected('Original merged source reconstruction differs')
        out=deepcopy(prior);out.update(route=dict(nationality=route['passport_nationality'],destination='IND',travel_purpose='tourism',travel_document_type='ordinary_passport'),partial_review=True,review_id=manifest['specification']['id'])
        values=reviewed_values(l);out['fields'].update(values)
        for f in ('arrival_card','entry_requirements'):out['field_provenance'][f]=vo._provenance(field_provenance(proof(l,f),route,f))
        # The visa-products container retains its historical proof. Only each
        # changed product entry field receives the new, explicitly partial proof.
        parsed=vo._parse_rows([out],{}).get(key)
        if parsed is None or digest(parsed['fields'])!=digest(out['fields']) or digest(parsed['field_provenance'])!=digest(out['field_provenance']):raise PatchRejected('Loader altered data/proof')
        effective,fields=vo.merge_verified_fields(l['raw_guidance'],parsed['fields'],source_url=parsed['source_url'])
        prov=dict(parsed['field_provenance'].get('disposition') or vo._provenance(parsed),fields=sorted(fields),field_provenance=parsed['field_provenance'])
        before_other={k:v for k,v in original.items() if k not in values};after_other={k:v for k,v in effective.items() if k not in values}
        if digest(before_other)!=digest(after_other):raise PatchRejected('Unreviewed route fact changed')
        for old,new in zip(original.get('visa_products') or [],effective.get('visa_products') or []):
            old=deepcopy(old);new=deepcopy(new)
            for p in (old,new):
                p.pop('entry_requirements',None);p.get('field_provenance',{}).pop('entry_requirements',None)
                if p.get('field_provenance')=={}:p.pop('field_provenance')
            if digest(old)!=digest(new):raise PatchRejected('Unreviewed product fact/proof changed')
        if len(original.get('visa_products') or [])!=len(effective.get('visa_products') or []):raise PatchRejected('Product set changed')
        if set(serve_time_invariants(effective))-set(serve_time_invariants(original)):raise PatchRejected('Entry correction introduced a serving conflict')
        rows=tstation.records_for_route(route,effective,prov)
        for row in rows:
            if CARD not in (row.get('entry_requirements') or '') or DECL.rstrip('.') not in (row.get('entry_requirements') or ''):raise PatchRejected('A QC product omitted a mandatory border form: '+str((r['cache_key'],row.get('visa_type_name'),row.get('entry_requirements'))))
        entries.append(out);previews.append(dict(cache_key=r['cache_key'],guidance=effective,source_provenance=prov,records=rows,changed_fields=sorted(values),visa_terms_preserved=True,prior_proofs_preserved_except_entry_fields=True))
    return dict(schema_version=1,kind='reviewed_overlay_conversion',review_id=manifest['specification']['id'],entries=entries,status='detached; exact-layer preflight required'),dict(routes=previews,raw_writes=False,operator_writes=False,issue_changes=False,renew_fresh_until=False,confidence_changed=False,new_release=False)


def verify_prepared(spec,layers,prepared_manifest,prepared_overlay):
    manifest=build_manifest(spec,layers);overlay,report=convert(manifest,layers)
    if digest(manifest)!=digest(prepared_manifest) or digest(overlay)!=digest(prepared_overlay):raise PatchRejected('Prepared manifest/overlay differs from full exact current rebuild')
    return report
