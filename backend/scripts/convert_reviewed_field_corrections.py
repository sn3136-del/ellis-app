"""Detached, exact-layer correction of individually reviewed route-level fields.

Each change replaces one route-level field on one context with a value that
its own destination-government quote supports. Verdicts, products, policy
intervals, grades, freshness, raw records, operator entries and every other
field survive unchanged. The output is an additive reviewed overlay that a
release must preflight against the exact six production layers it was built
from.
"""
from copy import deepcopy
from datetime import date
import re

from scripts.convert_reviewed_general_batch import _check_proof
from scripts.convert_reviewed_product_patch import field_provenance
from scripts.convert_reviewed_product_validity import BASELINE_KEYS, _current_map, _sources
from scripts.prepare_reviewed_product_patch import PatchRejected, digest

KIND = 'reviewed_field_corrections'
SCOPE = 'reviewed_route_field_corrections'
ALLOWED_FIELDS = frozenset({'health_requirements', 'passport_validity', 'passport_validity_requirement', 'arrival_card'})
# Columns of the projected product records that these route fields feed.
RECORD_COLUMNS_MAY_CHANGE = frozenset({'entry_requirements', 'special_conditions', 'required_documents',
                                       'completeness', 'field_status'})
ARRIVAL_KEYS = frozenset({'required', 'name', 'url', 'submission_window', 'notes'})
_SIX_MONTHS = r'six \(?6?\)? ?months|6 months|6개월|6 bulan|six bulan'
_VALID_PASSPORT = r'shall be valid|valid passport|passport[^.]{0,40}\bvalid\b|유효한 여권|유효기간|passport[^.]{0,40}(?:valid|validity)'
_NUMBER_WORDS = {1: 'one', 2: 'two', 3: 'three', 6: 'six', 12: 'twelve'}


def _passages(proof):
    return '\n'.join(item['quote'] for item in proof['evidence'])


def _check_value(field, value, proof):
    passages = _passages(proof)
    note = str(proof.get('scope_note') or '')
    if field == 'health_requirements':
        if value != []:
            raise PatchRejected('health_requirements: this contract only removes unsupported items')
        explicit = re.search(r'要求されていません|not required|no vaccination', passages, re.I)
        absence = 'absence' in note.lower() and len(proof['evidence']) >= 2
        if not explicit and not absence:
            raise PatchRejected('health_requirements: the quotes neither state that no certificate is required nor document an absence')
    elif field == 'passport_validity':
        if not isinstance(value, str) or not value.strip():
            raise PatchRejected('passport_validity: a non-empty statement is required')
        six = re.search(_SIX_MONTHS, passages, re.I)
        validity = re.search(_VALID_PASSPORT, passages, re.I)
        if not ((six and re.search(r'entry|arrival|application|접수|visa', passages, re.I))
                or (validity and re.search(r'entry|enter|arrival|stay|입국|체류', passages, re.I))):
            raise PatchRejected('passport_validity: the quotes state neither a months rule nor a validity-at-entry rule')
        if re.search(r'\bmore than\b', value, re.I) and not re.search(r'\bmore than\b', passages, re.I):
            raise PatchRejected('passport_validity: the value is stricter than its quote')
        if re.search(r'6 months|six months', value, re.I) and not six:
            raise PatchRejected('passport_validity: the value states six months but no quote does')
    elif field == 'passport_validity_requirement':
        from app.passport_validity import passport_validity_rule_errors
        if not isinstance(value, dict) or passport_validity_rule_errors(value) or value.get('kind') is None:
            raise PatchRejected('passport_validity_requirement: an explicit rule of a known kind is required')
        kind = value['kind']
        if kind in ('valid_on_arrival', 'valid_through_departure'):
            if not re.search(_VALID_PASSPORT, passages, re.I) or not re.search(r'entry|enter|arrival|stay|입국|체류', passages, re.I):
                raise PatchRejected('passport_validity_requirement: the quotes do not state a validity-at-entry or through-stay rule')
        else:
            months = value.get('months')
            if not re.search(rf'\b{months}\b|{_NUMBER_WORDS.get(months, "")}', passages, re.I) or not re.search(r'months?|개월|bulan', passages, re.I):
                raise PatchRejected('passport_validity_requirement: the months figure is not on the quoted page')
    elif field == 'arrival_card':
        if not isinstance(value, dict) or set(value) != ARRIVAL_KEYS or value['required'] is not True:
            raise PatchRejected('arrival_card: expected a required filing with name, url, window and notes')
        if not all(isinstance(value[k], str) and value[k].strip() for k in ('name', 'url', 'submission_window', 'notes')):
            raise PatchRejected('arrival_card: every text cell must be filled')
        if not value['url'].startswith('https://') or not re.search(r'(?:diwajibkan|wajib|mandatory|required)', passages, re.I):
            raise PatchRejected('arrival_card: the quotes do not establish a mandatory filing')
        name = value['name'].split(' arrival')[0].split(' (')[0]
        if name.lower() not in passages.lower():
            raise PatchRejected('arrival_card: the filing name is not on the quoted page')
    else:
        raise PatchRejected('Unsupported reviewed field: ' + str(field))


def validate(spec, layers):
    if (not isinstance(spec, dict) or set(spec) != {'schema_version', 'kind', 'id', 'sources', 'routes'} or
            type(spec['schema_version']) is not int or spec['schema_version'] != 1 or
            spec['kind'] != KIND or not isinstance(spec['id'], str) or not spec['id'].strip()):
        raise PatchRejected('Unexpected field-correction contract')
    sources = _sources(spec)
    rows = spec['routes']
    if not isinstance(rows, list) or not rows or len({r.get('cache_key') for r in rows}) != len(rows):
        raise PatchRejected('Missing or duplicate correction routes')
    current = _current_map(layers, {r['cache_key'] for r in rows})
    if len(layers) != len(rows):
        raise PatchRejected('Exactly the corrected contexts must be supplied')
    for row in rows:
        if set(row) != {'cache_key', 'route', 'baseline_sha256', 'changes'}:
            raise PatchRejected('Extra instruction')
        layer = current[row['cache_key']]
        if any(k not in layer for k in BASELINE_KEYS) or layer['operator_entries']:
            raise PatchRejected('Operator-authored or incomplete layer')
        if digest(row['route']) != digest(layer['route']):
            raise PatchRejected('Route identity changed')
        if row['baseline_sha256'] != {k: digest(layer[k]) for k in BASELINE_KEYS}:
            raise PatchRejected('Exact six-layer baseline changed')
        changes = row['changes']
        if not isinstance(changes, list) or not changes or len({c.get('field') for c in changes}) != len(changes):
            raise PatchRejected('Each context needs distinct reviewed changes')
        for change in changes:
            if set(change) != {'field', 'old_merged', 'new', 'proof'} or change['field'] not in ALLOWED_FIELDS:
                raise PatchRejected('Only reviewed route-level fields may change')
            field = change['field']
            if digest(change['old_merged']) != digest(layer['merged_guidance'].get(field)):
                raise PatchRejected('Reviewed old value no longer matches: ' + field)
            if digest(change['new']) == digest(change['old_merged']):
                raise PatchRejected('A correction must change the value: ' + field)
            proof = change['proof']
            if _check_proof(proof, sources, layer['route'], field, change['new']) is None:
                raise PatchRejected('A correction needs a reviewed proof: ' + field)
            try:
                when = date.fromisoformat(proof['verified_at'])
            except (TypeError, ValueError) as exc:
                raise PatchRejected('Invalid review date') from exc
            if when > date.today():
                raise PatchRejected('Review date is in the future')
            _check_value(field, change['new'], proof)
    return current


def build_manifest(spec, layers):
    current = validate(spec, layers)
    return dict(schema_version=1, kind='field_corrections_exact_layers', specification=deepcopy(spec),
                routes=[dict(cache_key=r['cache_key'], baseline={f: deepcopy(current[r['cache_key']][f]) for f in BASELINE_KEYS})
                        for r in sorted(spec['routes'], key=lambda r: r['cache_key'])])


def _mask(value, fields):
    out = deepcopy(value)
    for field in fields:
        out.pop(field, None)
    return out


def _tidy_notes(entry):
    """The seed loader strips and trims every note to 400 characters. A stored
    entry must already be that fixed point so reloading it changes nothing."""
    entry['note'] = str(entry.get('note') or '').strip()[:400]
    for proof in entry['field_provenance'].values():
        if isinstance(proof, dict) and 'note' in proof:
            proof['note'] = str(proof.get('note') or '').strip()[:400]
    return entry


def _note_normalised(provenance):
    out = deepcopy(provenance)
    for proof in out.get('field_provenance', {}).values():
        if isinstance(proof, dict) and 'note' in proof:
            proof['note'] = str(proof.get('note') or '').strip()
    if 'note' in out:
        out['note'] = str(out.get('note') or '').strip()
    return out


def convert(manifest, layers):
    from app.visa_snapshot import verified_overrides as vo, tstation
    from app.visa_snapshot.kimi_primary import serve_time_invariants
    if not isinstance(manifest, dict) or set(manifest) != {'schema_version', 'kind', 'specification', 'routes'}:
        raise PatchRejected('Malformed field-correction manifest')
    baselines = [dict(deepcopy(r['baseline']), cache_key=r['cache_key']) for r in manifest['routes']]
    if digest(build_manifest(manifest['specification'], baselines)) != digest(manifest):
        raise PatchRejected('Prepared manifest changed')
    spec = manifest['specification']
    current = validate(spec, layers)
    entries, previews = [], []
    for row in sorted(spec['routes'], key=lambda r: r['cache_key']):
        key = row['cache_key']; layer = current[key]; route = layer['route']
        doc = route.get('travel_document_type') or 'ordinary_passport'
        identity = vo._key(route['passport_nationality'], route['destination_country'], route['travel_purpose'], doc)
        prior = vo._parse_rows(layer['seed_entries'], {}).get(identity)
        if not prior:
            raise PatchRejected('Existing seed ownership missing: ' + key)
        old, checked = vo.merge_verified_fields(layer['raw_guidance'], prior['fields'], source_url=prior['source_url'])
        old_prov = dict(prior['field_provenance'].get('disposition') or vo._provenance(prior), fields=sorted(checked),
                        field_provenance=prior['field_provenance'])
        if digest(old) != digest(layer['merged_guidance']) or digest(old_prov) != digest(layer['source_provenance']):
            raise PatchRejected('Original canonical source reconstruction changed: ' + key)
        fields = [c['field'] for c in row['changes']]
        out = deepcopy(prior)
        out.update(route=dict(nationality=route['passport_nationality'], destination=route['destination_country'],
                              travel_purpose=route['travel_purpose'], travel_document_type=doc),
                   partial_review=True, review_id=spec['id'], field_review_scope=SCOPE)
        for change in row['changes']:
            proof = field_provenance(change['proof'], route, change['field'])
            proof.update(verification_scope=SCOPE)
            out['fields'][change['field']] = deepcopy(change['new'])
            out['field_provenance'][change['field']] = vo._provenance(proof)
        _tidy_notes(out)
        parsed = vo._parse_rows([out], {}).get(identity)
        if (not parsed or digest(parsed['fields']) != digest(out['fields']) or
                digest(parsed['field_provenance']) != digest(out['field_provenance'])):
            raise PatchRejected('Loader altered reviewed fields/proofs: ' + key)
        g, now_checked = vo.merge_verified_fields(layer['raw_guidance'], parsed['fields'], source_url=parsed['source_url'])
        if set(now_checked) != set(checked) | set(fields):
            raise PatchRejected('Correction altered checked fields outside its scope: ' + key)
        prov = dict(parsed['field_provenance'].get('disposition') or vo._provenance(parsed), fields=sorted(now_checked),
                    field_provenance=parsed['field_provenance'])
        if digest(_mask(g, fields)) != digest(_mask(old, fields)):
            raise PatchRejected('Unreviewed facts changed: ' + key)
        for change in row['changes']:
            if digest(g.get(change['field'])) != digest(change['new']):
                raise PatchRejected('Served value differs from the reviewed value: ' + key)
        op, np_ = deepcopy(old_prov), deepcopy(prov)
        for field in fields:
            op['field_provenance'].pop(field, None); np_['field_provenance'].pop(field, None)
        op.pop('fields'); np_.pop('fields')
        # Only note whitespace may differ, because the loader strips notes at
        # its 400-character cut and the stored entry is that fixed point.
        if digest(_note_normalised(op)) != digest(_note_normalised(np_)):
            raise PatchRejected('Unreviewed route provenance changed: ' + key)
        if set(serve_time_invariants(g)) - set(serve_time_invariants(old)):
            raise PatchRejected('New serving contradiction: ' + key)
        before = tstation.records_for_route(route, old, old_prov)
        after = tstation.records_for_route(route, g, prov)
        if len(before) != len(after):
            raise PatchRejected('Product inventory changed: ' + key)
        changed_columns = set()
        for a, b in zip(before, after, strict=True):
            if a.get('confidence_level') != b.get('confidence_level') or a.get('visa_type_name') != b.get('visa_type_name'):
                raise PatchRejected('Grade or product identity changed: ' + key)
            diff = {c for c in set(a) | set(b) if a.get(c) != b.get(c)}
            if diff - RECORD_COLUMNS_MAY_CHANGE:
                raise PatchRejected('Unexpected record columns changed on %s: %s' % (key, sorted(diff - RECORD_COLUMNS_MAY_CHANGE)))
            changed_columns |= diff
        entries.append(out)
        previews.append(dict(cache_key=key, guidance=g, source_provenance=prov, records=after,
                             changed_fields=sorted(fields), changed_record_columns=sorted(changed_columns)))
    return (dict(schema_version=1, kind='reviewed_overlay_conversion', review_id=spec['id'], entries=entries,
                 status='detached; exact-layer preflight required'),
            dict(routes=previews, raw_writes=False, operator_writes=False, issue_changes=False,
                 renew_fresh_until=False, confidence_changed=False, new_release=False))


def verify_prepared(spec, layers, prepared_manifest, prepared_overlay):
    manifest = build_manifest(spec, layers)
    overlay, report = convert(manifest, layers)
    if digest(manifest) != digest(prepared_manifest) or digest(overlay) != digest(prepared_overlay):
        raise PatchRejected('Prepared artifacts differ from full rebuild')
    return report
