"""One reviewed processing-time correction with exact six-layer CAS.

This converter is read-only. It preserves every product, proof owner, source
verdict, review date, unknown, policy boundary and pending/issue outside the
single processing-time field. Installing an overlay is a separate operation.
"""
from copy import deepcopy
from datetime import date

from scripts._reviewed_russia_japan_processing_contract import (
    KEY, CASE, BASELINES, SOURCES, SOURCE_CATALOG_SHA256,
)
from scripts.convert_reviewed_product_validity import BASELINE_KEYS, _current_map, _sources
from scripts.convert_reviewed_product_patch import field_provenance, _subject
from scripts.prepare_reviewed_product_patch import PatchRejected, digest

MANIFEST = 'reviewed_russia_japan_processing_manifest_20260910.json'
OVERLAY = 'reviewed_russia_japan_processing_overlay_20260910.json'
REVIEW_ID = 'russia-japan-processing-20260910'


def _proof(route):
    return dict(status='reviewed', verifier='ai', verified_at='2026-09-10',
                subject=_subject(route),
                scope_note='Russian ordinary-passport tourist resident in Russia. '
                'Only processing_time is reviewed: the numeric 4–5 working-day '
                'estimate and 4-day minimum explicitly apply to Embassy/JVAC '
                'Moscow, with the September 2026 holiday qualification. No '
                'unsupported acceptance-day clock, guaranteed deadline, other '
                'Russian consular district, visa eligibility, product, expiry, '
                'confidence or cache freshness is certified.',
                evidence=deepcopy(CASE['evidence']))


def _validate(spec, layers):
    from scripts.convert_reviewed_general_batch import _check_proof
    if (not isinstance(spec, dict) or set(spec) != {'schema_version', 'kind', 'id', 'sources', 'routes'}
            or type(spec['schema_version']) is not int or spec['schema_version'] != 1
            or spec['kind'] != 'reviewed_russia_japan_processing' or spec['id'] != REVIEW_ID):
        raise PatchRejected('Unexpected reviewed processing contract')
    if date.today() < date(2026, 9, 10):
        raise PatchRejected('Review date is in the future')
    sources = _sources(spec)
    if digest(spec['sources']) != SOURCE_CATALOG_SHA256 or set(sources) != set(SOURCES):
        raise PatchRejected('Reviewed source catalog changed')
    if any({f: sources[k].get(f) for f in pins} != pins for k, pins in SOURCES.items()):
        raise PatchRejected('Captured official source identity changed')
    if not isinstance(layers, list) or len(layers) != 1:
        raise PatchRejected('Exactly one current canonical route is required')
    current = _current_map(layers, {KEY})[KEY]
    if any(k not in current for k in BASELINE_KEYS):
        raise PatchRejected('Incomplete six-layer baseline')
    if {k: digest(current[k]) for k in BASELINE_KEYS} != BASELINES:
        raise PatchRejected('Six-layer baseline changed')
    if current['operator_entries'] or digest(current['route']) != digest(CASE['route']):
        raise PatchRejected('Reviewed route/operator scope changed')
    expected = [dict(cache_key=KEY, route=CASE['route'], baseline_sha256=BASELINES,
                     change=dict(field='processing_time',
                                 old_raw=current['raw_guidance'].get('processing_time'),
                                 old_merged=current['merged_guidance'].get('processing_time'),
                                 new_value=CASE['new_value'], evidence=CASE['evidence']))]
    if digest(spec['routes']) != digest(expected):
        raise PatchRejected('Closed field/value/old-value/evidence contract changed')
    _check_proof(_proof(current['route']), sources, current['route'],
                 'processing_time', CASE['new_value'])
    return current


def build_manifest(spec, layers):
    current = _validate(spec, layers)
    return dict(schema_version=1, kind='russia_japan_processing_exact_layers',
                specification=deepcopy(spec),
                routes=[dict(cache_key=KEY, baseline={k: deepcopy(current[k]) for k in BASELINE_KEYS})])


def convert(manifest, layers):
    from app.visa_snapshot import verified_overrides as vo, tstation
    from app.visa_snapshot.kimi_primary import serve_time_invariants
    if (not isinstance(manifest, dict)
            or set(manifest) != {'schema_version', 'kind', 'specification', 'routes'}):
        raise PatchRejected('Malformed reviewed manifest')
    try:
        embedded = [dict(deepcopy(r['baseline']), cache_key=r['cache_key']) for r in manifest['routes']]
    except (TypeError, KeyError) as exc:
        raise PatchRejected('Malformed embedded baseline') from exc
    if digest(build_manifest(manifest['specification'], embedded)) != digest(manifest):
        raise PatchRejected('Complete manifest binding changed')
    current = _validate(manifest['specification'], layers)
    route = current['route']
    identity = vo._key('RUS', 'JPN', 'tourism', 'ordinary_passport')
    prior = vo._parse_rows(current['seed_entries'], {}).get(identity)
    if not prior:
        raise PatchRejected('Original seeded ownership missing')
    before, checked = vo.merge_verified_fields(current['raw_guidance'], prior['fields'], source_url=prior['source_url'])
    provenance = dict(prior['field_provenance'].get('disposition') or vo._provenance(prior),
                      fields=sorted(checked), field_provenance=prior['field_provenance'])
    if digest(before) != digest(current['merged_guidance']) or digest(provenance) != digest(current['source_provenance']):
        raise PatchRejected('Canonical baseline reconstruction differs')
    entry = deepcopy(prior)
    entry.update(route=dict(nationality='RUS', destination='JPN', travel_purpose='tourism',
                            travel_document_type='ordinary_passport'),
                 review_id=REVIEW_ID, partial_review=True)
    entry['fields']['processing_time'] = CASE['new_value']
    entry['field_provenance']['processing_time'] = vo._provenance(
        field_provenance(_proof(route), route, 'processing_time'))
    expected_proofs = deepcopy(entry['field_provenance'])
    # A historical note truncated at 400 characters may end in whitespace;
    # feeding that already-normalized note through strip/truncate again loses
    # a byte. Reuse its exact original seed descriptor, rather than changing
    # any unrelated canonical proof or inventing padding to defeat the loader.
    for field, expected in expected_proofs.items():
        if field == 'processing_time' or digest(vo._provenance(expected)) == digest(expected):
            continue
        originals = [row.get('field_provenance', {}).get(field, row)
                     for row in current['seed_entries'] if field in row.get('fields', {})]
        original = next((p for p in reversed(originals)
                         if digest(vo._provenance(p)) == digest(expected)), None)
        if original is None:
            raise PatchRejected('Exact original proof descriptor unavailable: ' + field)
        entry['field_provenance'][field] = deepcopy(original)
    parsed = vo._parse_rows([entry], {}).get(identity)
    if (not parsed or digest(parsed['fields']) != digest(entry['fields'])
            or digest(parsed['field_provenance']) != digest(expected_proofs)):
        raise PatchRejected('Loader changed reviewed fields or proof ownership')
    after, checked = vo.merge_verified_fields(current['raw_guidance'], parsed['fields'], source_url=parsed['source_url'])
    proof = dict(parsed['field_provenance'].get('disposition') or vo._provenance(parsed),
                 fields=sorted(checked), field_provenance=parsed['field_provenance'])
    without = lambda value: {k: v for k, v in value.items() if k != 'processing_time'}
    if digest(without(before)) != digest(without(after)):
        raise PatchRejected('Unrelated route/product fact changed')
    if digest(without(prior['field_provenance'])) != digest(without(parsed['field_provenance'])):
        raise PatchRejected('Unrelated field or product proof owner changed')
    if digest(without(current['source_provenance']['field_provenance'])) != digest(without(proof['field_provenance'])):
        raise PatchRejected('Unrelated canonical provenance changed')
    if digest({k: v for k, v in provenance.items() if k != 'field_provenance'}) != digest({k: v for k, v in proof.items() if k != 'field_provenance'}):
        raise PatchRejected('Global source verdict, date or checked-field set changed')
    if set(serve_time_invariants(after)) - set(serve_time_invariants(before)):
        raise PatchRejected('New serving contradiction')
    rows = tstation.records_for_route(route, after, proof)
    preview = dict(cache_key=KEY, route=route, guidance=after, source_verified=proof,
                   records=rows, changed_fields=['processing_time'], raw_writes=False,
                   operator_writes=False, issue_changes=False, renew_fresh_until=False)
    return dict(schema_version=1, kind='reviewed_overlay_conversion', review_id=REVIEW_ID,
                entries=[entry], status='detached; exact-layer deployment preflight required'), [preview]


def verify_prepared(spec, layers, prepared_manifest, prepared_overlay):
    manifest = build_manifest(spec, layers)
    overlay, preview = convert(manifest, layers)
    if digest(manifest) != digest(prepared_manifest) or digest(overlay) != digest(prepared_overlay):
        raise PatchRejected('Complete prepared manifest/overlay differs from fresh rebuild')
    return preview
