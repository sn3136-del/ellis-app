"""Convert a reviewed product patch into scoped serving-overlay candidates.

No database, seed, operator-file, issue, release or freshness writes. A deployer
must evaluate the same preconditions against current production layers before
installing the reviewed entries. Persisted overlays retain prior unrelated
field authorship; nested products carry independent exact-subject proof.
"""
from __future__ import annotations
from copy import deepcopy

from scripts.prepare_reviewed_product_patch import (
    PatchRejected, prepare_layers, route_identity, _decision_scope,
)


def _subject(route, product=None):
    result = {k: route.get(k) for k in ('passport_nationality', 'destination_country',
                                       'travel_purpose', 'travel_document_type')}
    result['travel_document_type'] = result.get('travel_document_type') or 'ordinary_passport'
    if product is not None:
        result['product_type'] = product['type']
        # Explicit keys also bind unknown detail: a later edit cannot turn
        # old required-visa evidence into an exemption/authorisation proof.
        result['disposition'] = product.get('disposition')
        result['requirement_detail'] = product.get('requirement_detail')
    return result


def field_provenance(proof, route, field, product=None):
    """Translate each already-validated proof without inventing a source."""
    if proof.get('status') == 'unknown':
        return {'status': 'unknown', 'verifier': 'ai', 'source_url': '',
                'verified_at': None, 'verified_by': '', 'reason': proof['reason'],
                'note': proof['reason'], 'subject': _subject(route, product)}
    evidence = proof['evidence']
    primary = evidence[0]
    result = {'source_id': primary['source_id'], 'source_url': primary['source_url'],
              'quote': primary['quote'], 'verified_at': proof['verified_at'],
              'verifier': 'ai', 'verified_by': 'Ellis AI official-source field review',
              'status': 'reviewed', 'note': proof['scope_note'],
              'subject': _subject(route, product)}
    same = [p['quote'] for p in evidence[1:] if p['source_url'] == primary['source_url']]
    other = [deepcopy(p) for p in evidence[1:] if p['source_url'] != primary['source_url']]
    if same: result['additional_quotes'] = same
    if other: result['supporting_evidence'] = other
    if proof.get('retained_unverified_elements') is not None:
        result.update(status='partial', verification_scope='changed_elements_only',
                      verified_elements=deepcopy(proof.get('verified_elements') or []),
                      retained_unverified_elements=deepcopy(proof['retained_unverified_elements']))
    return result


def convert_entry(entry, sources, *, raw_guidance, merged_guidance,
                  current_override_entries):
    from app.visa_snapshot import verified_overrides as vo
    from app.visa_snapshot.kimi_primary import serve_time_invariants
    route = entry['route']
    prepared = prepare_layers(raw_guidance, merged_guidance, route, entry, sources,
                              current_override_entries)
    # _parse_rows follows existing seed precedence, including field authorship.
    # Consolidating only parsed prior overrides prevents full raw guidance from
    # acquiring a new blanket source when the new entry replaces old seed rows.
    table = vo._parse_rows(current_override_entries, {})
    key = vo._key(route['passport_nationality'], route['destination_country'],
                  route['travel_purpose'], route['travel_document_type'])
    prior = table.get(key) or {}
    fields = deepcopy(prior.get('fields') or {})
    provenance = deepcopy(prior.get('field_provenance') or {})
    fields.update(deepcopy(entry['fields']))
    changed_proofs = {k: field_provenance(p, route, k)
                      for k, p in entry['field_provenance'].items()}
    provenance.update(changed_proofs)
    candidate = prepared['guidance']
    products = deepcopy(candidate.get('visa_products') or [])
    patches = {p['match']['type']: p for p in entry['product_patches']}
    for product in products:
        patch = patches.get(product['type'])
        if patch is None:
            continue
        proofs = deepcopy(product.get('field_provenance') or {})
        for field, proof in patch['field_provenance'].items():
            proofs[field] = field_provenance(proof, route, field, product)
        product['field_provenance'] = proofs
        decision = proofs.get('disposition')
        if decision is None or decision.get('status') != 'reviewed':
            raise PatchRejected('A published product requires its own verified disposition scope')
        product['source_url'] = decision['source_url']
        product['source_quote'] = decision['quote']
        product['verified_at'] = decision['verified_at']
        product['verifier'] = 'ai'
        for field in ('source_url', 'source_quote'):
            proofs[field] = deepcopy(decision)
            proofs[field]['note'] = ('The product headline cites its independently reviewed visa requirement. '
                                     'Destination-purpose and nationality passages are retained separately in this proof.')
        # Each distinct supporting page stays visible; never a parent URL.
        product['corroborating_sources'] = [
            {'url': p['source_url'], 'quote': p['quote'], 'checked_at': decision['verified_at'],
             'authority': 'Official source; AI field review'}
            for p in patch['field_provenance']['disposition']['evidence'][1:]]
    # Some narrowly scoped old seeds supplied only a product/fee patch. The
    # required anchor here is proved from the current reviewed product, never
    # copied from the raw model verdict or from a sibling's fee quotation.
    if 'disposition' not in entry['fields'] and products:
        patch = patches[products[0]['type']]
        decision_proof = patch['field_provenance']['disposition']
        if not _decision_scope(candidate['disposition'], decision_proof['evidence'],
                               sources, route, products[0]):
            raise PatchRejected('A newly supplied route anchor lacks current decision proof')
        fields['disposition'] = candidate['disposition']
        provenance['disposition'] = field_provenance(decision_proof, route, 'disposition')
        if 'requirement_detail' in candidate:
            fields['requirement_detail'] = candidate['requirement_detail']
            provenance['requirement_detail'] = field_provenance(
                patch['field_provenance']['requirement_detail'], route, 'requirement_detail')
    if products:
        fields['visa_products'] = products
        # This container is intentionally partial: product-level fields name
        # exactly what was reviewed, and unknown fields remain unknown.
        product_anchor = deepcopy(products[0]['field_provenance']['disposition'])
        product_anchor.update(status='partial', verification_scope='product_fields_only',
            note='Each product retains its own exact-subject disposition proof and individual field evidence. This container does not verify untouched product or route details.')
        product_anchor.pop('subject', None)
        provenance['visa_products'] = product_anchor
    verdict = provenance.get('disposition')
    if not verdict or verdict.get('status') == 'unknown':
        raise PatchRejected('A serving overlay needs independently scoped route verdict provenance')
    output = {
        'route': {'nationality': route['passport_nationality'], 'destination': route['destination_country'],
                  'travel_purpose': route['travel_purpose'], 'travel_document_type': route['travel_document_type']},
        'verified_at': verdict['verified_at'], 'verified_by': 'Ellis AI official-source field review',
        'verifier': 'ai', 'source_url': verdict['source_url'],
        'note': verdict['note'], 'fields': fields, 'field_provenance': provenance,
        'review_id': 'schengen43-product-corrections-20260909',
        'partial_review': True,
    }
    errors = vo._field_errors(fields)
    if errors:
        raise PatchRejected('Converted overlay schema: ' + '; '.join(errors))
    parsed = vo._parse_rows([output], {}).get(key)
    if parsed is None or set(parsed['fields']) != set(fields):
        missing = sorted(set(fields) - set((parsed or {}).get('fields') or {}))
        raise PatchRejected('Serving loader would silently drop changed fields: ' + ', '.join(missing))
    effective, _ = vo.merge_verified_fields(raw_guidance, parsed['fields'], source_url=output['source_url'])
    # Report retained issues honestly; neither conversion nor installation is
    # a release. A new contradiction introduced by the patch is rejected.
    old_issues = set(serve_time_invariants(merged_guidance))
    issues = set(serve_time_invariants(effective))
    if issues - old_issues:
        raise PatchRejected('Patch introduces a new merged-answer conflict: ' + '; '.join(sorted(issues - old_issues)))
    return {'entry': output, 'effective_guidance': effective,
            'preconditions': {k: deepcopy(entry['baseline'][k]) for k in
                              ['raw_guidance_sha256', 'effective_guidance_sha256']},
            'scoped_override_sha256': [x['entry_sha256'] for x in entry['baseline']['override_entries']],
            'retained_override_fields': sorted(set((prior.get('fields') or {})) - set(entry['fields']) - {'visa_products'}),
            'new_or_changed_fields': sorted(set(entry['fields']) | ({'visa_products'} if products else set())),
            'retained_integrity_issues': sorted(issues), 'unresolved': deepcopy(entry['unresolved']),
            'new_grounded_check': False, 'new_release': False, 'renew_fresh_until': False}


def convert_manifest(manifest, current_layers):
    sources = {s['id']: s for s in manifest['sources']}
    converted, blocked = [], []
    for entry in manifest['routes']:
        if entry.get('publication_blocked'):
            blocked.append({'cache_key': entry['cache_key'], 'reasons': entry['publication_blocked']})
            continue
        current = current_layers[entry['cache_key']]
        result = convert_entry(entry, sources, **current)
        converted.append({'cache_key': entry['cache_key'], **result})
    return {'schema_version': 1, 'kind': 'reviewed_overlay_conversion',
            'reviewed_at': manifest['reviewed_at'], 'status': 'not_installed',
            'entries': [x['entry'] for x in converted],
            'preflight': [{k: v for k, v in x.items() if k not in {'entry', 'effective_guidance'}} for x in converted],
            'blocked': blocked,
            'contract': 'Re-run against current raw, merged and scoped override layers before installation; changed layers reject. No database, issue, release, TTL or operator changes are authorized by this artifact.'}
