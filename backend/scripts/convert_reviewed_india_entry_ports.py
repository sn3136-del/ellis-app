"""Detached additive correction of one obsolete eVisa entry-port sentence.

The post-N source/seed/raw/operator contract is exact. Only entry_requirements
and its existing partial proof change, on the route or explicitly owned eVisa
product. All other facts, unresolved text, products and review metadata survive.
"""
from copy import deepcopy
from datetime import date
from scripts._reviewed_india_entry_ports_contract import CONTRACT, SOURCE_CATALOG_SHA256
from scripts.convert_reviewed_product_validity import BASELINE_KEYS, _current_map, _sources
from scripts.convert_reviewed_product_patch import _subject
from scripts.prepare_reviewed_product_patch import PatchRejected, digest

REVIEW_ID = 'india-evisa-entry-ports-followup-20260910'
PIB = 'https://www.pib.gov.in/newsite/erelcontent.aspx?lang=2&reg=48&relid=293125'
OLD = 'Enter through a designated e-Visa airport or seaport.'
NEW = ('Enter through a designated e-Visa airport, seaport or land port. '
       'Check the Ministry of Home Affairs list of eligible ports at ' + PIB + '.')
QUOTE = 'The newly added 11 International Ports include 09 land ports and 02 airports.'
SCOPE_QUOTE = 'The Government of India has notified 11 more International Ports, enabling E-visa holder foreigners to enter India.'


def _correct(text):
    if not isinstance(text, str) or text.count(OLD) != 1:
        raise PatchRejected('The one reviewed obsolete entry-port sentence changed')
    return text.replace(OLD, NEW)


def _proof(layer, product=None):
    owner = product if product is not None else layer['merged_guidance']
    existing = ((product or {}).get('field_provenance') if product is not None else
                layer['source_provenance'].get('field_provenance')) or {}
    proof = deepcopy(existing.get('entry_requirements'))
    if (not isinstance(proof, dict) or proof.get('status') != 'partial' or
            proof.get('verification_scope') != 'changed_elements_only' or
            proof.get('subject') != _subject(layer['route'], product) or
            not isinstance(proof.get('verified_elements'), list) or
            not isinstance(proof.get('retained_unverified_elements'), list)):
        raise PatchRejected('Existing partial entry proof ownership changed')
    if product is not None and product.get('requirement_detail') != 'evisa':
        raise PatchRejected('Entry-port evidence applies to this eVisa product only')
    if product is None and owner.get('requirement_detail') != 'evisa':
        raise PatchRejected('Entry-port evidence does not change a VOA or regular-visa default')
    retained = proof['retained_unverified_elements']
    if sum(x.count(OLD) for x in retained if isinstance(x, str)) != 1:
        raise PatchRejected('Obsolete clause is not the exact retained unreviewed entry text')
    rest = []
    for text in retained:
        if not isinstance(text, str): raise PatchRejected('Malformed retained partial-proof text')
        rest.extend(part.strip() for part in text.split(OLD) if part.strip())
    proof['retained_unverified_elements'] = rest
    proof['verified_elements'].append(NEW)
    proof.setdefault('supporting_evidence', []).append({
        'source_id': 'india_mha_ports_processing', 'source_url': PIB,
        'quote': SCOPE_QUOTE + ' ' + QUOTE, 'verified_at': '2026-09-10',
        'verifier': 'ai', 'subject': _subject(layer['route'], product),
        'verified_element': NEW,
        'scope_note': 'Only the designated eVisa entry-port clause. No all-land-crossings claim or unique port count. Prior declaration reviews and retained other conditions remain separately scoped.'})
    return proof


def values(layer):
    config = CONTRACT[layer['cache_key']]; g = layer['merged_guidance']; out = {}
    if config['route_changed']: out['entry_requirements'] = _correct(g['entry_requirements'])
    if config['products']:
        ps = deepcopy(g['visa_products'])
        for item in config['products']:
            old = g['visa_products'][item['index']]; p = ps[item['index']]
            if old['type'] != item['type'] or digest(old) != item['sha256']:
                raise PatchRejected('Exact owned product changed')
            p['entry_requirements'] = _correct(old['entry_requirements'])
            p['field_provenance']['entry_requirements'] = _proof(layer, old)
        out['visa_products'] = ps
    return out


def validate(spec, layers):
    if (not isinstance(spec, dict) or set(spec) != {'schema_version', 'kind', 'id', 'sources', 'routes'} or
            type(spec['schema_version']) is not int or spec['schema_version'] != 1 or
            spec['kind'] != 'reviewed_india_entry_ports' or spec['id'] != REVIEW_ID or date.today() < date(2026, 9, 10)):
        raise PatchRejected('Unexpected entry-port review contract/date')
    sources = _sources(spec)
    if digest(spec['sources']) != SOURCE_CATALOG_SHA256 or set(sources) != {'india_mha_ports_processing'}:
        raise PatchRejected('Exact current destination source capture changed')
    s = sources['india_mha_ports_processing']
    if s['url'] != PIB or s['checked_at'] != '2026-09-10' or any(q not in s['text'] for q in [SCOPE_QUOTE, QUOTE]):
        raise PatchRejected('Official source does not establish the reviewed eVisa port extension')
    if not isinstance(layers, list) or len(layers) != 17:
        raise PatchRejected('Exactly seventeen post-N India baseline contexts required')
    current = _current_map(layers, set(CONTRACT))
    if not isinstance(spec['routes'], list) or len(spec['routes']) != 17 or {r.get('cache_key') for r in spec['routes']} != set(CONTRACT):
        raise PatchRejected('Exact seventeen review instructions required')
    for row in spec['routes']:
        if set(row) != {'cache_key', 'route', 'baseline_sha256', 'changes'}: raise PatchRejected('Extra instruction')
        layer = current[row['cache_key']]; c = CONTRACT[row['cache_key']]
        if (any(k not in layer for k in BASELINE_KEYS) or layer['operator_entries'] or
                digest(row['route']) != digest(c['route']) or digest(layer['route']) != digest(c['route']) or
                row['baseline_sha256'] != c['baseline_sha256'] or {k: digest(layer[k]) for k in BASELINE_KEYS} != c['baseline_sha256']):
            raise PatchRejected('Exact six-layer baseline changed')
        if digest(row['changes']) != digest(values(layer)): raise PatchRejected('Unreviewed fact/proof change')
        if c['route_changed']: _proof(layer)
    return current


def build_manifest(spec, layers):
    current = validate(spec, layers)
    return dict(schema_version=1, kind='india_entry_ports_exact_layers', specification=deepcopy(spec),
                routes=[dict(cache_key=k, baseline={f: deepcopy(current[k][f]) for f in BASELINE_KEYS}) for k in sorted(CONTRACT)])


def _mask_changed(g, config):
    out = deepcopy(g)
    if config['route_changed']: out.pop('entry_requirements', None)
    for item in config['products']:
        p = out['visa_products'][item['index']]
        p.pop('entry_requirements', None); p['field_provenance'].pop('entry_requirements', None)
    return out


def convert(manifest, layers):
    from app.visa_snapshot import verified_overrides as vo, tstation
    from app.visa_snapshot.kimi_primary import serve_time_invariants
    if not isinstance(manifest, dict) or set(manifest) != {'schema_version', 'kind', 'specification', 'routes'}:
        raise PatchRejected('Malformed entry-port manifest')
    baselines = [dict(deepcopy(r['baseline']), cache_key=r['cache_key']) for r in manifest['routes']]
    if digest(build_manifest(manifest['specification'], baselines)) != digest(manifest): raise PatchRejected('Prepared manifest changed')
    current = validate(manifest['specification'], layers); entries = []; previews = []
    for key in sorted(CONTRACT):
        layer = current[key]; c = CONTRACT[key]; route = layer['route']
        identity = vo._key(route['passport_nationality'], 'IND', 'tourism', 'ordinary_passport')
        prior = vo._parse_rows(layer['seed_entries'], {}).get(identity)
        if not prior: raise PatchRejected('Existing seed ownership missing')
        old, checked = vo.merge_verified_fields(layer['raw_guidance'], prior['fields'], source_url=prior['source_url'])
        old_prov = dict(prior['field_provenance'].get('disposition') or vo._provenance(prior), fields=sorted(checked), field_provenance=prior['field_provenance'])
        if digest(old) != digest(layer['merged_guidance']) or digest(old_prov) != digest(layer['source_provenance']):
            raise PatchRejected('Original canonical source reconstruction changed')
        changes = values(layer)
        if not changes:
            previews.append(dict(cache_key=key, guidance=old, source_provenance=old_prov, records=tstation.records_for_route(route, old, old_prov), changed_fields=[])); continue
        if 'visa_products' in changes and digest(prior['fields'].get('visa_products')) != digest(old['visa_products']):
            raise PatchRejected('Product ownership changed')
        out = deepcopy(prior)
        out.update(route=dict(nationality=route['passport_nationality'], destination='IND', travel_purpose='tourism', travel_document_type='ordinary_passport'), partial_review=True, review_id=REVIEW_ID)
        out['fields'].update(changes)
        if c['route_changed']: out['field_provenance']['entry_requirements'] = vo._provenance(_proof(layer))
        parsed = vo._parse_rows([out], {}).get(identity)
        if not parsed or digest(parsed['fields']) != digest(out['fields']) or digest(parsed['field_provenance']) != digest(out['field_provenance']):
            raise PatchRejected('Loader altered reviewed fields/proofs')
        g, checked = vo.merge_verified_fields(layer['raw_guidance'], parsed['fields'], source_url=parsed['source_url'])
        prov = dict(parsed['field_provenance'].get('disposition') or vo._provenance(parsed), fields=sorted(checked), field_provenance=parsed['field_provenance'])
        if digest(_mask_changed(g, c)) != digest(_mask_changed(old, c)): raise PatchRejected('Unreviewed facts changed')
        op, np = deepcopy(old_prov), deepcopy(prov)
        if c['route_changed']:
            op['field_provenance'].pop('entry_requirements'); np['field_provenance'].pop('entry_requirements')
        if digest(op) != digest(np): raise PatchRejected('Unreviewed route provenance changed')
        if set(serve_time_invariants(g)) - set(serve_time_invariants(old)): raise PatchRejected('New serving contradiction')
        rows = tstation.records_for_route(route, g, prov); before = tstation.records_for_route(route, old, old_prov)
        if len(rows) != len(before): raise PatchRejected('Product inventory changed')
        for a, b in zip(before, rows, strict=True):
            left, right = deepcopy(a), deepcopy(b)
            left.pop('entry_requirements', None); right.pop('entry_requirements', None)
            if digest(left) != digest(right): raise PatchRejected('Non-entry record, grade or completion changed')
        entries.append(out); previews.append(dict(cache_key=key, guidance=g, source_provenance=prov, records=rows, changed_fields=sorted(changes)))
    return dict(schema_version=1, kind='reviewed_overlay_conversion', review_id=REVIEW_ID, entries=entries, status='detached; exact-layer preflight required'), dict(routes=previews, raw_writes=False, operator_writes=False, issue_changes=False, renew_fresh_until=False, confidence_changed=False, new_release=False)


def verify_prepared(spec, layers, prepared_manifest, prepared_overlay):
    manifest = build_manifest(spec, layers); overlay, report = convert(manifest, layers)
    if digest(manifest) != digest(prepared_manifest) or digest(overlay) != digest(prepared_overlay): raise PatchRejected('Prepared artifacts differ from full rebuild')
    return report
