"""Detached exact-layer integration for six reviewed Russian validity bounds.

This does not review product eligibility, register an
overlay, or modify a cache, operator store, release, issue or freshness date.
Only the explicitly reviewed validity field and its own proof may change.
It deliberately rejects operator edits and products not already wholly owned
by the effective seed overlay: they require a separate integration contract.
"""
from copy import deepcopy
import hashlib

from scripts.prepare_reviewed_product_patch import PatchRejected, digest, prepare_layers
from scripts.convert_reviewed_product_patch import field_provenance

BASELINE_KEYS = ('route', 'raw_guidance', 'merged_guidance', 'source_provenance',
                 'seed_entries', 'operator_entries')
RULE_URL = 'https://www.kdmid.ru/cons/visas/'
RULE_QUOTE = 'Обыкновенная туристическая виза может быть однократной или двукратной на срок до 3 месяцев либо многократной на срок до 6 месяцев'


def _current_map(layers, wanted):
    selected = [x for x in layers if x.get('cache_key') in wanted]
    result = {x['cache_key']: x for x in selected}
    if len(result) != len(selected) or set(result) != wanted:
        raise PatchRejected('Current route set is missing or duplicated')
    return result


def _sources(patch):
    rows = patch.get('sources') or []
    sources = {x['id']: x for x in rows}
    if not rows or len(sources) != len(rows):
        raise PatchRejected('Source catalog is missing or duplicated')
    for source in rows:
        text = source.get('text')
        if (not isinstance(text, str) or not text.strip() or
                hashlib.sha256(text.encode()).hexdigest() != source.get('sha256')):
            raise PatchRejected('Captured source text hash does not match')
    return sources


def build_manifest(patch, layers):
    if patch.get('schema_version') != 1 or patch.get('kind') != 'reviewed_product_patch':
        raise PatchRejected('Malformed validity-field patch')
    _sources(patch)
    rows = patch.get('routes') or []
    wanted = {x['cache_key'] for x in rows}
    if not wanted or len(rows) != len(wanted):
        raise PatchRejected('Missing or duplicate validity routes')
    current = _current_map(layers, wanted)
    entries = []
    for row in rows:
        layer = current[row['cache_key']]
        if any(k not in layer for k in BASELINE_KEYS):
            raise PatchRejected('Every current layer must be explicitly supplied')
        if row['route'] != layer['route']:
            raise PatchRejected('Exact route scope differs')
        baseline = {k: deepcopy(layer[k]) for k in BASELINE_KEYS}
        entries.append({'cache_key': row['cache_key'], 'baseline': baseline,
                        'baseline_sha256': {k: digest(v) for k, v in baseline.items()}})
    return {'schema_version': 1, 'kind': 'product_validity_exact_layers',
            'patch': deepcopy(patch), 'routes': entries,
            'status': 'detached; requires exact-layer preflight before installation'}


def _validity_only(entry, candidate, sources):
    from datetime import date
    from scripts.convert_reviewed_general_batch import _check_proof
    from scripts.convert_reviewed_product_patch import _subject
    if entry.get('fields') or entry.get('field_provenance') or entry.get('expected_fields'):
        raise PatchRejected('Validity patches cannot change route fields')
    route = entry['route']
    if (route.get('passport_nationality') not in {'FRA', 'IND', 'TWN'} or
            route.get('destination_country') != 'RUS' or route.get('travel_purpose') != 'tourism' or
            route.get('travel_document_type') != 'ordinary_passport'):
        raise PatchRejected('Validity review does not cover this nationality, destination, purpose or document')
    patches = entry.get('product_patches') or []
    names = [p['match']['type'] for p in patches]
    if not patches or len(names) != len(set(names)):
        raise PatchRejected('Missing or duplicate product validity changes')
    existing = candidate.get('visa_products') or []
    for patch in patches:
        if (set(patch.get('fields') or {}) != {'validity'} or
                set(patch.get('field_provenance') or {}) != {'validity'} or
                set(patch.get('match') or {}) != {'type'}):
            raise PatchRejected('Only product validity and its own exact-field proof may change')
        matches = [p for p in existing if p.get('type') == patch['match']['type']]
        if len(matches) != 1:
            raise PatchRejected('Validity correction requires exactly one existing product')
        product = matches[0]
        if (product.get('entry') not in {'single', 'double'} or
                not product['type'].lower().startswith(product['entry'] + '-entry tourist visa') or
                product.get('requirement_detail') != 'paper_visa' or
                product.get('disposition') != 'VISA_REQUIRED' or product.get('validity') != '6 months'):
            raise PatchRejected('Validity evidence is scoped to existing six-month single/double paper-tourist products')
        proof = patch['field_provenance']['validity']
        value = patch['fields']['validity']
        if 'subject' in proof and proof['subject'] != _subject(route, product):
            raise PatchRejected('Validity proof subject differs from the exact current product')
        _check_proof(deepcopy(proof), sources, route, 'validity', value, product=product)
        # The general checker validates literal capture and numeric presence.
        # This bounded addition binds the UNIT, upper-bound qualifier and entry
        # branch as well: a six-month multiple rule or a three-day fee paragraph
        # cannot verify a single/double validity claim.
        if value != 'Up to 3 months' or not any(
                e['source_url'] == RULE_URL and ' '.join(e['quote'].split()) == RULE_QUOTE
                for e in proof['evidence']):
            raise PatchRejected('Validity proof does not establish this exact three-month maximum and entry branch')
        if any(k in proof for k in ('effective_from', 'effective_to', 'policy_interval_evidence')):
            raise PatchRejected('A visa-validity correction cannot change policy expiry')
        try:
            reviewed = date.fromisoformat(proof['verified_at'])
        except (ValueError, TypeError, KeyError) as exc:
            raise PatchRejected('Invalid validity-field review date') from exc
        if reviewed > date.today() or any(sources[e['source_id']]['checked_at'] != reviewed.isoformat() for e in proof['evidence']):
            raise PatchRejected('Validity-field review date must match its actual captured evidence date')


def _assert_only_validity(before, after, names):
    left, right = deepcopy(before), deepcopy(after)
    lp, rp = left.get('visa_products') or [], right.get('visa_products') or []
    if len(lp) != len(rp):
        raise PatchRejected('A validity correction cannot add or remove products')
    for old, new in zip(lp, rp, strict=True):
        if old.get('type') not in names:
            continue
        new.pop('validity', None)
        old.pop('validity', None)
        (new.get('field_provenance') or {}).pop('validity', None)
        (old.get('field_provenance') or {}).pop('validity', None)
        # An added field proof may create the otherwise absent container.
        if not new.get('field_provenance'): new.pop('field_provenance', None)
        if not old.get('field_provenance'): old.pop('field_provenance', None)
    if left != right:
        raise PatchRejected('A validity correction altered another fact or metadata field')


def _convert_entry(entry, sources, layer, review_id):
    from app.visa_snapshot import verified_overrides as vo, tstation
    from app.visa_snapshot.kimi_primary import serve_time_invariants
    route = layer['route']
    if layer['operator_entries']:
        raise PatchRejected('Operator-authored routes require separate validity integration')
    _validity_only(entry, layer['merged_guidance'], sources)
    prepared = prepare_layers(layer['raw_guidance'], layer['merged_guidance'], route,
                              entry, sources, layer['seed_entries'])
    key = vo._key(route['passport_nationality'], route['destination_country'],
                  route['travel_purpose'], route.get('travel_document_type') or 'ordinary_passport')
    prior = vo._parse_rows(layer['seed_entries'], {}).get(key)
    if not prior or prior['fields'].get('visa_products') != layer['merged_guidance'].get('visa_products'):
        raise PatchRejected('Existing products must already be owned by the effective seed overlay')
    original, checked_fields = vo.merge_verified_fields(layer['raw_guidance'], prior['fields'], source_url=prior['source_url'])
    # A route subject to a separately applied scheduled policy/annotation needs
    # its own serving integration, not assumptions about this static merge.
    if original != layer['merged_guidance']:
        raise PatchRejected('Current serving guidance cannot be reconstructed exactly')
    original_prov = dict(prior['field_provenance'].get('disposition') or vo._provenance(prior),
                         fields=sorted(checked_fields), field_provenance=prior['field_provenance'])
    if original_prov != layer['source_provenance']:
        raise PatchRejected('Current verdict authorship cannot be reconstructed exactly')
    output = deepcopy(prior)
    output['route'] = {'nationality': route['passport_nationality'], 'destination': route['destination_country'],
                       'travel_purpose': route['travel_purpose'],
                       'travel_document_type': route.get('travel_document_type') or 'ordinary_passport'}
    output.update(review_id=review_id, partial_review=True,
                  field_review_scope='product_validity_only')
    products = deepcopy(prepared['guidance']['visa_products'])
    patches = {p['match']['type']: p for p in entry['product_patches']}
    for product in products:
        if product['type'] not in patches: continue
        proof = field_provenance(patches[product['type']]['field_provenance']['validity'], route, 'validity', product)
        proof.update(verification_scope='product_validity_only',
                     verified_by='Ellis AI product-validity field review')
        product['field_provenance']['validity'] = proof
    output['fields']['visa_products'] = products
    parsed = vo._parse_rows([output], {}).get(key)
    if not parsed:
        raise PatchRejected('Serving loader rejected the validity overlay')
    expected = deepcopy(prior)
    expected['fields']['visa_products'] = products
    if parsed != expected:
        raise PatchRejected('Serving loader changed field authorship or facts')
    effective, fields = vo.merge_verified_fields(layer['raw_guidance'], parsed['fields'], source_url=parsed['source_url'])
    if fields.keys() != checked_fields.keys():
        raise PatchRejected('A validity correction cannot claim a newly checked route field')
    _assert_only_validity(original, effective, set(patches))
    if serve_time_invariants(effective) != serve_time_invariants(original):
        raise PatchRejected('A validity correction cannot change integrity holds')
    before_rows = tstation.records_for_route(route, original, original_prov)
    after_rows = tstation.records_for_route(route, effective, original_prov)
    if len(before_rows) != len(after_rows):
        raise PatchRejected('A validity correction cannot change projected product inventory')
    projected = []
    for before, after in zip(before_rows, after_rows, strict=True):
        old, new = deepcopy(before), deepcopy(after)
        if after['visa_type_name'] in patches:
            if before.get('validity_duration') != 6 or after.get('validity_duration') != 3 or after.get('validity_unit') != 'Month':
                raise PatchRejected('Reviewed maximum validity is not projected faithfully')
            old.pop('validity_duration', None); new.pop('validity_duration', None)
            projected.append({k: after.get(k) for k in ('visa_type_name', 'source_url', 'data_source',
                             'validity_duration', 'validity_unit', 'collected_at', 'info_validity',
                             'confidence_level', '_product_source_verified')})
        if old != new:
            raise PatchRejected('Validity projection changes other facts, authorship, review dates or verification credit')
    return output, {'cache_key': entry['cache_key'], 'changed_product_validities': len(patches),
                    'changes': ['product.validity', 'product.field_provenance.validity'],
                    'projection': projected, 'retained_integrity_issues': serve_time_invariants(effective),
                    'new_product_verdict_reviews': 0, 'new_release': False,
                    'new_grounded_check': False, 'renew_fresh_until': False}, effective


def convert(manifest, current_layers):
    """Validate all CAS layers first, then return a candidate; never write it."""
    if manifest.get('kind') != 'product_validity_exact_layers' or manifest.get('schema_version') != 1:
        raise PatchRejected('Malformed exact-layer validity manifest')
    baseline_layers = [dict(deepcopy(e['baseline']), cache_key=e['cache_key']) for e in manifest['routes']]
    if build_manifest(manifest['patch'], baseline_layers) != manifest:
        raise PatchRejected('Baseline validity contract was altered')
    wanted = {e['cache_key'] for e in manifest['routes']}
    current = _current_map(current_layers, wanted)
    # No candidate entries are even constructed until every route's complete
    # ordered source/seed/operator/raw/merged contract has passed.
    for entry in manifest['routes']:
        for name in BASELINE_KEYS:
            if name not in current[entry['cache_key']] or digest(current[entry['cache_key']][name]) != entry['baseline_sha256'][name]:
                raise PatchRejected('Layer changed since review: ' + name + ' ' + entry['cache_key'])
    patch = manifest['patch']; sources = _sources(patch)
    entries, reports, guidance = [], [], {}
    for row in patch['routes']:
        output, report, effective = _convert_entry(row, sources, current[row['cache_key']], patch['id'])
        entries.append(output); reports.append(report); guidance[row['cache_key']] = effective
    overlay = {'schema_version': 1, 'kind': 'reviewed_overlay_conversion', 'entries': entries,
               'review_id': patch['id'], 'review_scope': 'product_validity_fields_only',
               'status': 'not_installed', 'preflight': reports,
               'contract': 'Re-run exact raw, merged, route, source provenance, ordered seed and operator CAS immediately before atomic installation. No product verdict review, release, TTL or operator change.'}
    return overlay, reports, guidance


def convert_to_file(manifest, current_layers, output_path):
    """Write one review artifact only after all validation succeeds.

    This is not installation: the caller must independently recheck the same
    layers immediately before registering the file in the serving registry.
    """
    import json
    import os
    from pathlib import Path
    import tempfile
    overlay, reports, guidance = convert(manifest, current_layers)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=output_path.parent,
                                         prefix=output_path.name + '.', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(overlay, handle, ensure_ascii=False, indent=2)
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, output_path)
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)
    return overlay, reports, guidance


if __name__ == '__main__':
    import argparse
    import json
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--layers', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text())
    layers = json.loads(Path(args.layers).read_text())
    if isinstance(layers, dict): layers = layers['layers']
    _, reports, _ = convert_to_file(manifest, layers, args.output)
    print(json.dumps({'routes': len(reports), 'corrected_validity_bounds': sum(r['changed_product_validities'] for r in reports),
                      'new_verdict_reviews': 0, 'installed': False, 'output': args.output}))
