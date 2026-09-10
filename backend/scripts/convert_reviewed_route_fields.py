"""Detached, exact-layer integration of the reviewed HKSAR→Oman entry documents.

This deliberately bounded contract cannot review a verdict, product, policy
interval, expiry or freshness date. It preserves every prior seed-owned fact
and proof, adding only the three explicitly supported entry-document fields.
It does not register the output or write any serving, issue or operator store.
"""
from copy import deepcopy
from datetime import date

from scripts.convert_reviewed_product_validity import BASELINE_KEYS, _current_map, _sources
from scripts.convert_reviewed_product_patch import field_provenance, _subject
from scripts.prepare_reviewed_product_patch import PatchRejected, digest

CASE_KEY = 'HKG|HKG|OMN|tourism|default|unknown|v6'
SOURCE_URL = 'https://www.fm.gov.om/en/visitors/entry-visas/'
CONDITIONS = ('He must have a return ticket, a confirmed hotel reservation, health insurance, '
              'and a sum of money that will enable him to bear the burden of living during his stay in the Sultanate.')
PASSPORT = 'The visitor must hold a passport valid for no less than six (6) months.'
NATIONALITY = 'China (includes Hong Kong and Macau)'
REGULATIONS = 'Controls to facilitate the entry of foreigners into the Sultanate without a visa:'
VALUES = {
    'required_documents': ['Passport valid for no less than six (6) months', 'Return ticket',
                           'Confirmed hotel reservation', 'Health insurance',
                           'Sufficient funds to cover the stay in Oman'],
    'onward_travel_evidence': 'Return ticket',
    'accommodation_evidence': 'Confirmed hotel reservation',
}
FIELDS = frozenset(VALUES)


def _validate_spec(spec, layer):
    from scripts.convert_reviewed_general_batch import _check_proof
    if (spec.get('schema_version') != 1 or
            spec.get('kind') != 'detached_route_field_correction_specification' or
            not isinstance(spec.get('id'), str) or not spec['id'].strip()):
        raise PatchRejected('Malformed reviewed route-field specification')
    if set(spec) - {'schema_version', 'kind', 'status', 'id', 'cache_key', 'captured_at',
                    'route', 'baseline_sha256', 'baseline_file_sha256', 'sources', 'changes',
                    'preservation_requirements', 'integration_limit'}:
        raise PatchRejected('Unexpected mutation outside the route-field specification')
    route = spec.get('route') or {}
    expected = {'passport_nationality': 'HKG', 'lawful_country_of_residence': 'HKG',
                'destination_country': 'OMN', 'visa_category': 'tourist_visa',
                'travel_purpose': 'tourism', 'arrival_date': None, 'consular_jurisdiction': None}
    normalised = dict(route)
    if normalised.pop('travel_document_type', 'ordinary_passport') != 'ordinary_passport':
        raise PatchRejected('Entry-document evidence is scoped to an ordinary HKSAR passport')
    if (spec.get('cache_key') != CASE_KEY or normalised != expected or
            route != layer['route']):
        raise PatchRejected('Entry-document evidence does not cover the exact current route')
    sources = _sources(spec)
    if len(sources) != 1 or next(iter(sources.values()))['url'] != SOURCE_URL:
        raise PatchRejected('This review requires the captured Oman Foreign Ministry entry page')
    source = next(iter(sources.values()))
    text = source['text']
    # A nationality on the second list would need additional visa/residence
    # conditions. Membership and the full cumulative rule must be captured.
    try:
        first, second, member, rules = (text.index(s) for s in
                                        ('First group', 'Second group', NATIONALITY, REGULATIONS))
    except ValueError as exc:
        raise PatchRejected('Missing first-group eligibility or full entry regulations') from exc
    if not first < member < second < rules:
        raise PatchRejected('Hong Kong first-group eligibility is not established')
    if CONDITIONS not in text[rules:] or PASSPORT not in text[rules:]:
        raise PatchRejected('Full cumulative passport and entry conditions are not captured')
    changes = spec.get('changes') or []
    if (len(changes) != len(FIELDS) or
            {c.get('field') for c in changes} != FIELDS or
            any(set(c) != {'field', 'old_raw', 'old_merged', 'new', 'proof'} for c in changes)):
        raise PatchRejected('Only the three reviewed entry-document fields may change')
    for change in changes:
        field = change['field']; proof = change['proof']
        if (change['old_raw'] != layer['raw_guidance'].get(field) or
                change['old_merged'] != layer['merged_guidance'].get(field)):
            raise PatchRejected('Reviewed old field value no longer matches: ' + field)
        if change['new'] != VALUES[field]:
            raise PatchRejected('Correction must retain every exact cumulative entry requirement')
        if (not isinstance(proof, dict) or
                set(proof) - {'status', 'verifier', 'verified_at', 'scope_note', 'evidence', 'subject'} or
                proof.get('status') != 'reviewed' or proof.get('verifier') != 'ai'):
            raise PatchRejected('Only an AI-attributed, field-scoped source proof may be added')
        if 'subject' in proof and proof['subject'] != _subject(route):
            raise PatchRejected('Entry-document proof subject differs from the exact route')
        evidence = proof.get('evidence') or []
        needed = {CONDITIONS, NATIONALITY} | ({PASSPORT} if field == 'required_documents' else set())
        if (len(evidence) != len(needed) or {e.get('quote') for e in evidence} != needed or
                any(set(e) != {'source_id', 'source_url', 'quote'} for e in evidence)):
            raise PatchRejected('Exact cumulative requirements and Hong Kong eligibility quotations are required')
        _check_proof(deepcopy(proof), sources, route, field, change['new'])
        try:
            when = date.fromisoformat(proof['verified_at'])
        except (TypeError, ValueError, KeyError) as exc:
            raise PatchRejected('Invalid route-field source review date') from exc
        if when > date.today() or when.isoformat() != source.get('checked_at'):
            raise PatchRejected('Field review date must match the actual captured evidence date')
    return sources


def build_manifest(spec, layers):
    layer = _current_map(layers, {spec.get('cache_key')})[spec['cache_key']]
    if any(k not in layer for k in BASELINE_KEYS):
        raise PatchRejected('Every current layer must be explicitly supplied')
    baseline = {k: deepcopy(layer[k]) for k in BASELINE_KEYS}
    hashes = {k: digest(v) for k, v in baseline.items()}
    if spec.get('baseline_sha256') != hashes:
        raise PatchRejected('Specification is not bound to these exact six current layers')
    _validate_spec(spec, layer)
    return {'schema_version': 1, 'kind': 'route_fields_exact_layers', 'specification': deepcopy(spec),
            'cache_key': spec['cache_key'], 'baseline': baseline, 'baseline_sha256': hashes,
            'status': 'detached; requires exact-layer preflight before installation'}


def _assert_unchanged_other_fields(before, after):
    if {k: v for k, v in before.items() if k not in FIELDS} != {
            k: v for k, v in after.items() if k not in FIELDS}:
        raise PatchRejected('Entry-document correction changed another fact or metadata field')


def _convert_entry(spec, layer):
    from app.visa_snapshot import verified_overrides as vo, tstation
    from app.visa_snapshot.kimi_primary import serve_time_invariants
    if layer['operator_entries']:
        raise PatchRejected('Operator-authored routes require a separate integration contract')
    _validate_spec(spec, layer)
    route = layer['route']
    key = vo._key('HKG', 'OMN', 'tourism', 'ordinary_passport')
    prior = vo._parse_rows(layer['seed_entries'], {}).get(key)
    if not prior or prior['fields'].get('visa_products') != layer['merged_guidance'].get('visa_products'):
        raise PatchRejected('Existing product inventory must be wholly retained from its current seed owner')
    original, checked = vo.merge_verified_fields(layer['raw_guidance'], prior['fields'], source_url=prior['source_url'])
    original_prov = dict(prior['field_provenance'].get('disposition') or vo._provenance(prior),
                         fields=sorted(checked), field_provenance=prior['field_provenance'])
    if original != layer['merged_guidance'] or original_prov != layer['source_provenance']:
        raise PatchRejected('Current guidance and verdict authorship cannot be reconstructed exactly')
    output = deepcopy(prior)
    output.update(route={'nationality': 'HKG', 'destination': 'OMN', 'travel_purpose': 'tourism',
                         'travel_document_type': 'ordinary_passport'}, review_id=spec['id'],
                  partial_review=True, field_review_scope='route_entry_document_fields_only')
    for change in spec['changes']:
        field = change['field']
        proof = field_provenance(change['proof'], route, field)
        proof.update(verification_scope='route_entry_document_fields_only')
        output['fields'][field] = deepcopy(change['new'])
        output['field_provenance'][field] = vo._provenance(proof)
    parsed = vo._parse_rows([output], {}).get(key)
    expected = deepcopy(prior)
    expected['fields'].update(deepcopy(VALUES))
    expected['field_provenance'].update({f: output['field_provenance'][f] for f in FIELDS})
    if parsed != expected:
        raise PatchRejected('Serving loader changed facts, source authorship or review metadata')
    effective, fields = vo.merge_verified_fields(layer['raw_guidance'], parsed['fields'], source_url=parsed['source_url'])
    if set(fields) != set(checked) | FIELDS:
        raise PatchRejected('Correction altered checked fields outside its exact three-field scope')
    provenance = dict(original_prov, fields=sorted(fields), field_provenance=parsed['field_provenance'])
    _assert_unchanged_other_fields(original, effective)
    if serve_time_invariants(original) != serve_time_invariants(effective):
        raise PatchRejected('Entry-document correction cannot alter integrity holds')
    before = tstation.records_for_route(route, original, original_prov)
    after = tstation.records_for_route(route, effective, provenance)
    if len(before) != len(after):
        raise PatchRejected('Entry-document correction changed projected product inventory')
    changed_default = False
    for old_row, new_row in zip(before, after, strict=True):
        old, new = deepcopy(old_row), deepcopy(new_row)
        if old.get('visa_type_name') == 'Visa-free entry':
            previous = old.get('entry_requirements') or ''
            expected_entry = previous.replace('confirmed return/onward ticket', 'Return ticket').replace(
                'confirmed hotel booking or host invitation', 'Confirmed hotel reservation')
            if (new.get('required_documents') != ', '.join(VALUES['required_documents']) or
                    new.get('entry_requirements') != expected_entry or previous == expected_entry):
                raise PatchRejected('Projected entry requirements do not faithfully carry the correction')
            for field in ('entry_requirements', 'required_documents'):
                old.pop(field, None); new.pop(field, None)
            changed_default = True
        if old != new:
            raise PatchRejected('Correction changed product facts, projected grades or review dates')
    if not changed_default:
        raise PatchRejected('Current projected default entry-document scope was not found')
    report = {'cache_key': CASE_KEY, 'changed_fields': sorted(FIELDS),
              'new_verdict_reviews': 0, 'new_product_reviews': 0, 'new_release': False,
              'new_grounded_check': False, 'renew_fresh_until': False,
              'retained_integrity_issues': serve_time_invariants(effective),
              'before_records': before, 'after_records': after}
    return output, report, effective, provenance


def convert(manifest, current_layers):
    if manifest.get('schema_version') != 1 or manifest.get('kind') != 'route_fields_exact_layers':
        raise PatchRejected('Malformed exact-layer route-field manifest')
    baseline = dict(deepcopy(manifest['baseline']), cache_key=manifest['cache_key'])
    if build_manifest(manifest['specification'], [baseline]) != manifest:
        raise PatchRejected('Baseline route-field contract was altered')
    layer = _current_map(current_layers, {manifest['cache_key']})[manifest['cache_key']]
    for name in BASELINE_KEYS:
        if name not in layer or digest(layer[name]) != manifest['baseline_sha256'][name]:
            raise PatchRejected('Layer changed since review: ' + name)
    output, report, guidance, provenance = _convert_entry(manifest['specification'], layer)
    overlay = {'schema_version': 1, 'kind': 'reviewed_overlay_conversion', 'entries': [output],
               'review_id': manifest['specification']['id'], 'review_scope': 'route_entry_document_fields_only',
               'status': 'not_installed', 'preflight': [{k: v for k, v in report.items() if not k.endswith('_records')}],
               'contract': 'Recheck exact route, raw, merged, source provenance, ordered seed and operator CAS immediately before installation. No product/verdict review, release, TTL, issue or operator changes.'}
    return overlay, report, guidance, provenance


def convert_to_file(manifest, current_layers, output_path):
    import json
    import os
    from pathlib import Path
    import tempfile
    result = convert(manifest, current_layers)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=output_path.parent,
                                         prefix=output_path.name + '.', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(result[0], handle, ensure_ascii=False, indent=2)
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, output_path)
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)
    return result


if __name__ == '__main__':
    import argparse
    import json
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--layers', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    layers = json.loads(Path(args.layers).read_text())
    if isinstance(layers, dict): layers = layers['layers']
    _, report, _, _ = convert_to_file(json.loads(Path(args.manifest).read_text()), layers, args.output)
    print(json.dumps({'changed_fields': report['changed_fields'], 'new_verdict_reviews': 0,
                      'installed': False, 'output': args.output}))
