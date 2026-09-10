"""Detached, six-layer-bound review of three ordinary-tourism routes.

Only the fields and literal evidence in the closed contract may change. Raw
facts, products, operators, cache dates, issue state and all other field owners
are preserved. Warning resolution is a separate exact installed-review reader;
this converter never clears pending extraction state or a source issue.
"""
from copy import deepcopy
from datetime import date
from scripts._reviewed_japan_singapore_contract import APPROVED, SOURCES
from scripts.convert_reviewed_product_validity import BASELINE_KEYS, _current_map, _sources
from scripts.convert_reviewed_product_patch import field_provenance, _subject
from scripts.prepare_reviewed_product_patch import PatchRejected, digest

MANIFEST='reviewed_japan_singapore_manifest_20260910.json'
OVERLAY='reviewed_japan_singapore_overlay_20260910.json'

def _validate(spec, layers):
    from scripts.convert_reviewed_general_batch import _check_proof, quote_literal
    if (set(spec) != {'schema_version','kind','id','sources','routes'} or spec['schema_version'] != 1
            or spec['kind'] != 'reviewed_japan_singapore_fields'
            or spec['id'] != 'japan-singapore-ordinary-tourism-20260910'):
        raise PatchRejected('Invalid closed Japan/Singapore field review')
    sources=_sources(spec)
    if set(sources) != set(SOURCES) or any(sources[s]['url'] != c['url'] or sources[s]['sha256'] != c['sha256'] for s,c in SOURCES.items()):
        raise PatchRejected('Captured official source catalog changed')
    if any(s.get('checked_at') != '2026-09-10' for s in sources.values()) or date.today() < date(2026,9,10):
        raise PatchRejected('Actual source review date changed or is in the future')
    routes=spec['routes']
    if not isinstance(routes,list) or len(routes)!=3 or {r.get('cache_key') for r in routes}!=set(APPROVED):
        raise PatchRejected('Exactly the three reviewed routes are required')
    for item in routes:
        key=item['cache_key']; case=APPROVED[key]; layer=layers[key]
        if set(item) != {'cache_key','route','baseline_sha256','changes','resolved_uncertainty'}:
            raise PatchRejected('Unexpected review instruction')
        if item['route'] != case['route'] or item['route'] != layer['route'] or layer['operator_entries']:
            raise PatchRejected('Passport, residence, purpose, document or operator scope changed')
        if item['baseline_sha256'] != {k:digest(layer[k]) for k in BASELINE_KEYS}:
            raise PatchRejected('Current six-layer baseline changed')
        if item['resolved_uncertainty'] != case['resolved_uncertainty']:
            raise PatchRejected('Only the exact reviewed old Japan warnings can be resolved')
        if case['resolved_uncertainty'] and layer['merged_guidance'].get('uncertainty') != case['resolved_uncertainty']:
            raise PatchRejected('Warning baseline differs from the exact reviewed objects')
        changes=item['changes']
        if (not isinstance(changes,list) or len(changes)!=len(case['changes'])
                or {c.get('field') for c in changes}!=set(case['changes'])):
            raise PatchRejected('Closed field set changed')
        for change in changes:
            field=change['field']; approved=case['changes'][field]
            if (set(change) != set(approved)|{'field','old_raw','old_merged'}
                    or {k:v for k,v in change.items() if k not in ('field','old_raw','old_merged')} != approved
                    or change['old_raw'] != layer['raw_guidance'].get(field)
                    or change['old_merged'] != layer['merged_guidance'].get(field)):
                raise PatchRejected('Reviewed value, qualification or evidence changed: '+field)
            if approved.get('unknown_reason'):
                if approved['new'] is not None or approved['evidence']:
                    raise PatchRejected('An unknown cannot introduce a fact or absence credit')
                continue
            proof=_proof(item['route'],field,approved)
            # Exact closed field semantics supplement the normal literal and
            # jurisdiction validation; no generic evidence check is weakened.
            _check_proof(deepcopy(proof),sources,item['route'],field,approved['new'])
        for evidence in case['changes'].get('exceptions',{}).get('evidence',[]):
            if not quote_literal(evidence['quote'],sources[evidence['source_id']]['text']):
                raise PatchRejected('Missing literal resolution evidence')
    return sources

def _proof(route,field,approved):
    if approved.get('unknown_reason'):
        return {'status':'unknown','verifier':'ai','reason':approved['unknown_reason']}
    result=dict(status='reviewed',verifier='ai',verified_at='2026-09-10',subject=_subject(route),
                scope_note=approved.get('scope_note') or 'AI review of this exact ordinary-tourism field; each cited source retains its own government ownership. No other fact, product, policy expiry or freshness claim is reviewed.',
                evidence=deepcopy(approved['evidence']))
    for k in ('verified_elements','retained_unverified_elements'):
        if k in approved:result[k]=deepcopy(approved[k])
    return result

def build_manifest(spec,layers):
    current=_current_map(layers,set(APPROVED));_validate(spec,current)
    return dict(schema_version=1,kind='japan_singapore_fields_exact_layers',specification=deepcopy(spec),
                routes=[dict(cache_key=k,baseline={f:deepcopy(current[k][f]) for f in BASELINE_KEYS},
                             baseline_sha256={f:digest(current[k][f]) for f in BASELINE_KEYS}) for k in sorted(APPROVED)])

def convert(manifest,current_layers):
    from app.visa_snapshot import verified_overrides as vo,tstation
    from app.visa_snapshot.kimi_primary import serve_time_invariants
    if set(manifest) != {'schema_version','kind','specification','routes'}:
        raise PatchRejected('Malformed manifest')
    baseline=[dict(deepcopy(r['baseline']),cache_key=r['cache_key']) for r in manifest['routes']]
    if build_manifest(manifest['specification'],baseline)!=manifest:
        raise PatchRejected('Manifest or baseline binding changed')
    layers=_current_map(current_layers,set(APPROVED))
    _validate(manifest['specification'],layers)
    entries=[];reports=[];previews=[]
    for spec in manifest['specification']['routes']:
        key=spec['cache_key'];layer=layers[key];route=layer['route'];case=APPROVED[key]
        identity=vo._key(route['passport_nationality'],route['destination_country'],'tourism','ordinary_passport')
        prior=vo._parse_rows(layer['seed_entries'],{}).get(identity)
        if not prior:raise PatchRejected('Missing original field owners')
        original,checked=vo.merge_verified_fields(layer['raw_guidance'],prior['fields'],source_url=prior['source_url'])
        before_prov=dict(prior['field_provenance'].get('disposition') or vo._provenance(prior),
                         fields=sorted(checked),field_provenance=prior['field_provenance'])
        if original!=layer['merged_guidance'] or before_prov!=layer['source_provenance']:
            raise PatchRejected('Canonical baseline reconstruction differs')
        output=deepcopy(prior)
        output.update(route={'nationality':route['passport_nationality'],'destination':route['destination_country'],
                             'travel_purpose':'tourism','travel_document_type':'ordinary_passport'},
                      review_id=manifest['specification']['id'],partial_review=True)
        for field,approved in case['changes'].items():
            proof=_proof(route,field,approved)
            output['fields'][field]=deepcopy(approved['new'])
            if proof['status']=='unknown':
                output['field_provenance'][field]=dict(proof,source_url='',verified_at=None,verified_by='',note=proof['reason'])
            else:
                output['field_provenance'][field]=vo._provenance(field_provenance(proof,route,field))
        parsed=vo._parse_rows([output],{}).get(identity)
        if not parsed:raise PatchRejected('Reviewed row was dropped by loader')
        for field in case['changes']:
            if field not in parsed['fields'] or parsed['fields'][field]!=case['changes'][field]['new']:
                raise PatchRejected('Loader changed or dropped scoped field: '+field)
        fields=set(case['changes'])
        if ({k:v for k,v in parsed['fields'].items() if k not in fields} != {k:v for k,v in prior['fields'].items() if k not in fields}
                or {k:v for k,v in parsed['field_provenance'].items() if k not in fields} != {k:v for k,v in prior['field_provenance'].items() if k not in fields}):
            raise PatchRejected('Unrelated value or field owner changed')
        merged,checked=vo.merge_verified_fields(layer['raw_guidance'],parsed['fields'],source_url=parsed['source_url'])
        provenance=dict(parsed['field_provenance'].get('disposition') or vo._provenance(parsed),
                        fields=sorted(checked),field_provenance=parsed['field_provenance'])
        unchanged_before={k:v for k,v in original.items() if k not in fields}
        unchanged_after={k:v for k,v in merged.items() if k not in fields}
        # The existing exemption normalizer removes an empty application-form
        # container after a newly checked verdict. No nonempty form is exempt.
        empty_forms_removed=unchanged_before.get('forms')==[] and 'forms' not in unchanged_after
        if empty_forms_removed:unchanged_before.pop('forms')
        if unchanged_after!=unchanged_before:
            raise PatchRejected('Unrelated merged fact changed: '+str({k:(original.get(k),merged.get(k)) for k in set(original)|set(merged) if k not in fields and original.get(k)!=merged.get(k)}))
        if serve_time_invariants(merged)!=serve_time_invariants(original):
            raise PatchRejected('Existing integrity gate changed')
        rows=tstation.records_for_route(route,merged,provenance)
        reports.append(dict(cache_key=key,fields_reviewed=sorted(fields),
                            changed_values=sorted(f for f in fields if original.get(f)!=merged.get(f)),
                            products_unchanged=merged.get('visa_products')==original.get('visa_products'),
                            removed_empty_forms_container=empty_forms_removed,
                            open_issues_untouched=True,raw_pending_untouched=True,renew_fresh_until=False,
                            records=rows))
        previews.append(dict(cache_key=key,route=route,guidance=merged,source_verified=provenance))
        entries.append(output)
    return dict(schema_version=1,kind='reviewed_overlay_conversion',review_id=manifest['specification']['id'],
                entries=entries,status='detached; exact-layer deployment preflight required'),reports,previews

def verify_prepared(spec,current_layers,prepared_manifest,prepared_overlay):
    manifest=build_manifest(spec,current_layers);overlay,reports,previews=convert(manifest,current_layers)
    if manifest!=prepared_manifest or overlay!=prepared_overlay:
        raise PatchRejected('Prepared manifest or complete overlay differs from the fresh rebuild')
    return reports,previews
