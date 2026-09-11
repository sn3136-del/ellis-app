"""Detached, exact-layer fill of empty required cells on reviewed routes.

Ellis serves product rows that cite a government page and still leave a
required cell empty (validity, permitted stay, entries, fee, documents or the
application method) although the official page states the value. Each fill
here writes one value into one such cell, on one context, with the value's own
destination-government quote, or records a documented absence when the pages
checked do not publish it. A fill never overwrites a value and never touches
the verdict, the product identities, policy intervals, freshness, raw records
or operator entries. The record can only become more complete, so its grade
may rise from Medium to High and may never fall. The output is an additive
reviewed overlay that a release must preflight against the exact six
production layers it was built from.
"""
from copy import deepcopy
from datetime import date
import math
import re

from scripts.convert_reviewed_field_corrections import _mask, _note_normalised, _tidy_notes
from scripts.convert_reviewed_general_batch import NOT_PUBLISHED_CELLS, _check_proof, _proof_for, _source_table
from scripts.convert_reviewed_product_validity import BASELINE_KEYS, _current_map
from scripts.prepare_reviewed_product_patch import PatchRejected, digest

KIND = 'reviewed_field_fill'
SCOPE = 'reviewed_field_fill'
MANIFEST_KIND = 'field_fill_exact_layers'
VERIFIED_BY = 'Ellis AI official-source field review'
PRODUCT_FIELDS = frozenset({'validity', 'max_stay_days', 'entry', 'fee', 'required_documents'})
ROUTE_FIELDS = frozenset({'permitted_stay', 'permitted_stay_days', 'government_fee', 'application_channel',
                          'required_documents'})
# The channel vocabulary the projection turns into an application method.
CHANNELS = frozenset({'online_portal', 'embassy_or_consulate', 'embassy_designated_agency', 'authorised_agent',
                      'visa_application_centre', 'on_arrival'})
ENTRIES = frozenset({'single', 'double', 'multiple'})
# The record cells each fillable field feeds. The general batch names the
# absence cells and the three fields it lacks are added here.
CELLS = dict(NOT_PUBLISHED_CELLS, permitted_stay=('max_stay_duration', 'max_stay_unit'),
             required_documents=('required_documents',), application_channel=('application_method',))
# Columns of the projected records a fill may change: the filled cells, the
# absence markers and the grade, nothing else.
RECORD_COLUMNS_MAY_CHANGE = frozenset({
    'validity_duration', 'validity_unit', 'validity_text', 'max_stay_duration', 'max_stay_unit', 'max_stay_text',
    '_max_stay_representation_reason', 'special_conditions', 'entries', 'visa_fee_amount', 'visa_fee_currency',
    'visa_fee_qualifier', 'required_documents', 'application_method', 'confidence_level', '_unpublished',
    'completeness', 'field_status'})
_EMPTY_FEE = {'amount': None, 'currency': None}
_REVIEWED_PROOF_KEYS = frozenset({'status', 'verifier', 'verified_at', 'scope_note', 'evidence'})
_ABSENCE_PROOF_KEYS = frozenset({'status', 'verifier', 'reason', 'verified_at'})
# A bare dollar sign is shared by a dozen currencies and the general batch's
# monetary rewrite trusts the value's own code. A prefixed dollar on the page
# names its currency, so a value in another dollar cannot borrow that figure.
_PREFIXED_DOLLARS = {'A$': 'AUD', 'AU$': 'AUD', 'NZ$': 'NZD', 'HK$': 'HKD', 'S$': 'SGD', 'SG$': 'SGD', 'C$': 'CAD',
                     'CA$': 'CAD', 'CAN$': 'CAD', 'US$': 'USD', 'U.S.$': 'USD', 'NT$': 'TWD', 'MX$': 'MXN',
                     'R$': 'BRL', 'MOP$': 'MOP'}
_DOCUMENT_WORDS = re.compile(
    r'passport|photo|form|application|ticket|insurance|proof|invitation|itinerary|bank|booking|letter|certificate|'
    r'copy|document|voucher|confirmation|паспорт|фото|анкет|страхов|билет|приглашен|документ|справк|подтвержден|'
    r'ваучер|护照|護照|照片|申请|申請|文件|パスポート|旅券|写真|申請書|書類|여권|사진|신청서|서류|hộ chiếu|ảnh|'
    r'passeport|pasaporte|formulaire|formulario|paspor|surat|dokumen|foto', re.I)


def _empty(value):
    if isinstance(value, dict) and 'amount' in value:
        return value.get('amount') is None
    return value in (None, '', [], {})


def _label(fill):
    if fill.get('target') == 'product':
        return 'product %s %s' % (fill.get('product_type'), fill.get('field'))
    return 'route %s' % fill.get('field')


def _wants_absence(proof):
    return isinstance(proof, dict) and proof.get('status') == 'not_published'


def _product_named(products, name, label):
    hits = [p for p in (products if isinstance(products, list) else []) if isinstance(p, dict) and p.get('type') == name]
    if not hits:
        raise PatchRejected('%s: no served product is named %s' % (label, name))
    if len(hits) > 1:
        raise PatchRejected('%s: more than one served product is named %s' % (label, name))
    return hits[0]


def _checked_proof(proof, sources, route, field, value, label, product=None):
    """The general batch's proof check, its reasons re-labelled with the fill
    (it names the field alone, which is ambiguous across products)."""
    try:
        return _check_proof(proof, sources, route, field, value, product=product)
    except PatchRejected as exc:
        message = str(exc)
        if message.startswith(field + ': '):
            message = message[len(field) + 2:]
        raise PatchRejected(label + ': ' + message) from exc


def _check_absence(proof, sources, route, field, value, label):
    if set(proof) - _ABSENCE_PROOF_KEYS:
        raise PatchRejected(label + ': extra instruction on the absence proof')
    if value is not None and value != _EMPTY_FEE:
        raise PatchRejected(label + ': a documented absence carries no value')
    # The general batch's own absence rule: an empty value and a reason.
    _checked_proof(proof, sources, route, field, value, label)
    reason = str(proof.get('reason') or '').strip()
    from app.visa_snapshot.evidence_validator import jurisdiction_matches
    named = [s for s in sources.values()
             if (s['url'] in reason or re.search(r'(?<![\w-])' + re.escape(str(s['id'])) + r'(?![\w-])', reason))
             and jurisdiction_matches(s['url'], route['destination_country'])]
    if not named:
        raise PatchRejected(label + ': the absence reason must name a captured destination-government page it checked')


def _check_fill_value(field, value, proof, label):
    """Shape and projection checks the general batch does not make. The quote
    and figure checks already ran inside _check_proof."""
    from app.visa_snapshot.verified_overrides import _reads_like_review
    passages = '\n'.join(item['quote'] for item in proof['evidence'])
    if field == 'validity':
        from app.visa_snapshot.tstation import _validity_num_unit
        n, unit = _validity_num_unit(value) if isinstance(value, str) else (None, None)
        if n is None or len(value.strip()) > 60:
            raise PatchRejected(label + ': the value must be one unambiguous duration such as "30 days"')
    elif field in ('max_stay_days', 'permitted_stay_days'):
        if type(value) is not int or value <= 0:
            raise PatchRejected(label + ': a positive whole number of days is required')
    elif field == 'entry':
        if value not in ENTRIES:
            raise PatchRejected(label + ': expected single, double or multiple')
    elif field in ('fee', 'government_fee'):
        if (not isinstance(value, dict) or set(value) != {'amount', 'currency'} or isinstance(value['amount'], bool)
                or not isinstance(value['amount'], (int, float)) or not math.isfinite(value['amount'])
                or value['amount'] <= 0 or not isinstance(value['currency'], str)
                or not re.fullmatch(r'[A-Z]{3}', value['currency'])):
            raise PatchRejected(label + ': expected a positive amount with an ISO 4217 currency code')
        named = {code for symbol, code in _PREFIXED_DOLLARS.items()
                 if re.search(r'(?<![A-Za-z])' + re.escape(symbol), passages)}
        if named and value['currency'] not in named:
            raise PatchRejected(label + ': the quote prices in %s, not %s' % ('/'.join(sorted(named)), value['currency']))
    elif field == 'required_documents':
        if (not isinstance(value, list) or not value or len(value) > 25
                or any(not isinstance(d, str) or not d.strip() or len(d) > 160 for d in value)):
            raise PatchRejected(label + ': expected a short list of document names')
        if _reads_like_review(value):
            raise PatchRejected(label + ': a document name reads like the reviewer talking')
        if not _DOCUMENT_WORDS.search(passages):
            raise PatchRejected(label + ': the quotes do not describe documents')
    elif field == 'permitted_stay':
        if not isinstance(value, str) or not value.strip() or len(value) > 120:
            raise PatchRejected(label + ': expected a short stay statement')
        if _reads_like_review(value):
            raise PatchRejected(label + ': the stay statement reads like the reviewer talking')
        digits = re.findall(r'\d+', value)
        if digits and not all(d in passages for d in digits):
            raise PatchRejected(label + ': the stay figure is not in its evidence')
    elif field == 'application_channel':
        if value not in CHANNELS:
            raise PatchRejected(label + ': the channel is outside the projection vocabulary')


def _prior(layer):
    from app.visa_snapshot import verified_overrides as vo
    route = layer['route']
    doc = route.get('travel_document_type') or 'ordinary_passport'
    identity = vo._key(route['passport_nationality'], route['destination_country'], route['travel_purpose'], doc)
    prior = vo._parse_rows(layer['seed_entries'], {}).get(identity)
    if not prior:
        raise PatchRejected('Existing seed ownership missing: ' + str(layer.get('cache_key')))
    return identity, prior


def _validate_fill(fill, layer, sources, prior):
    """Every check one fill must pass against its current layer. Returns
    'fill' for a quoted value and 'absence' for a documented absence."""
    if not isinstance(fill, dict):
        raise PatchRejected('Malformed fill')
    route, merged = layer['route'], layer['merged_guidance']
    label = _label(fill)
    target = fill.get('target')
    if target == 'product':
        expected_keys, allowed = {'target', 'product_type', 'field', 'value', 'proof'}, PRODUCT_FIELDS
    elif target == 'route':
        expected_keys, allowed = {'target', 'field', 'value', 'proof'}, ROUTE_FIELDS
    else:
        raise PatchRejected('A fill targets a route or a product')
    if set(fill) != expected_keys:
        raise PatchRejected(label + ': extra instruction on a fill')
    field = fill['field']
    if field not in allowed:
        raise PatchRejected(label + ': only empty required cells may be filled')
    product = None
    if target == 'product':
        if 'visa_products' not in prior['fields'] or digest(prior['fields']['visa_products']) != digest(merged.get('visa_products')):
            raise PatchRejected(label + ': the served product table is not owned by the reviewed seed, product fills need a product review')
        product = _product_named(merged.get('visa_products'), fill['product_type'], label)
        current = product.get(field)
    else:
        current = merged.get(field)
    if not _empty(current):
        raise PatchRejected(label + ': the cell already holds a value')
    proof = fill['proof']
    if not isinstance(proof, dict) or proof.get('status') not in ('reviewed', 'not_published'):
        raise PatchRejected(label + ': a fill is a quoted value or a documented absence, nothing else')
    owner = product if product is not None else merged
    if _wants_absence(proof):
        _check_absence(proof, sources, route, field, fill['value'], label)
        if set(CELLS[field]) <= set(owner.get('unpublished_fields') or []):
            raise PatchRejected(label + ': already documented as not published')
        return 'absence'
    if set(proof) - _REVIEWED_PROOF_KEYS:
        raise PatchRejected(label + ': extra instruction on the proof')
    if _checked_proof(deepcopy(proof), sources, route, field, fill['value'], label, product=product) is None:
        raise PatchRejected(label + ': a fill needs a reviewed proof')
    try:
        when = date.fromisoformat(proof['verified_at'])
    except (TypeError, ValueError) as exc:
        raise PatchRejected(label + ': invalid review date') from exc
    if when > date.today():
        raise PatchRejected(label + ': review date is in the future')
    _check_fill_value(field, fill['value'], proof, label)
    return 'fill'


def _route_checks(row, layer):
    """The context-level gates: exact shape, exact six-layer baseline, no
    operator authorship, existing seed ownership."""
    if not isinstance(row, dict) or set(row) != {'cache_key', 'route', 'baseline_sha256', 'fills'}:
        raise PatchRejected('Extra instruction')
    if any(k not in layer for k in BASELINE_KEYS) or layer['operator_entries']:
        raise PatchRejected('Operator-authored or incomplete layer')
    if digest(row['route']) != digest(layer['route']):
        raise PatchRejected('Route identity changed')
    if row['baseline_sha256'] != {k: digest(layer[k]) for k in BASELINE_KEYS}:
        raise PatchRejected('Exact six-layer baseline changed')
    if not isinstance(row['fills'], list) or not row['fills']:
        raise PatchRejected('Each context needs at least one fill')
    return _prior(layer)[1]


def _fill_key(fill):
    return (fill.get('target'), fill.get('product_type'), fill.get('field')) if isinstance(fill, dict) else None


def _validate_route(row, layer, sources):
    prior = _route_checks(row, layer)
    keys = [_fill_key(f) for f in row['fills']]
    if len(set(keys)) != len(keys):
        raise PatchRejected('Duplicate fill on one cell')
    return [_validate_fill(f, layer, sources, prior) for f in row['fills']]


def _check_spec_shape(spec):
    if (not isinstance(spec, dict) or set(spec) != {'schema_version', 'kind', 'id', 'sources', 'routes'} or
            type(spec['schema_version']) is not int or spec['schema_version'] != 1 or
            spec['kind'] != KIND or not isinstance(spec['id'], str) or not spec['id'].strip()):
        raise PatchRejected('Unexpected field-fill contract')


def validate(spec, layers):
    _check_spec_shape(spec)
    sources = _source_table(spec)
    rows = spec['routes']
    if not isinstance(rows, list) or not rows or len({r.get('cache_key') for r in rows if isinstance(r, dict)}) != len(rows):
        raise PatchRejected('Missing or duplicate fill routes')
    current = _current_map(layers, {r['cache_key'] for r in rows})
    if len(layers) != len(rows):
        raise PatchRejected('Exactly the filled contexts must be supplied')
    for row in rows:
        _validate_route(row, current[row['cache_key']], sources)
    return current


def build_manifest(spec, layers):
    current = validate(spec, layers)
    return dict(schema_version=1, kind=MANIFEST_KIND, specification=deepcopy(spec),
                routes=[dict(cache_key=r['cache_key'], baseline={f: deepcopy(current[r['cache_key']][f]) for f in BASELINE_KEYS})
                        for r in sorted(spec['routes'], key=lambda r: r['cache_key'])])


def _mask_product(product, fields):
    """A product without the cells a fill may write, their proofs and its
    absence markers, so everything else can be compared exactly."""
    out = deepcopy(product)
    for field in fields:
        out.pop(field, None)
    proofs = out.get('field_provenance')
    if isinstance(proofs, dict):
        for field in fields:
            proofs.pop(field, None)
        if not proofs:
            out.pop('field_provenance')
    elif proofs is None:
        out.pop('field_provenance', None)
    out.pop('unpublished_fields', None)
    return out


def _apply_fills(out, fills, route):
    """Write the fills into the seed entry copy. Returns the applied fills
    and the route-level fields whose value changed."""
    from app.visa_snapshot import verified_overrides as vo
    fields, proofs = out['fields'], out['field_provenance']
    applied, touched = [], set()
    for fill in fills:
        field, absence, label = fill['field'], _wants_absence(fill['proof']), _label(fill)
        record = dict(target=fill['target'], product_type=fill.get('product_type'), field=field,
                      kind='absence' if absence else 'fill', value=deepcopy(fill['value']), cells=list(CELLS[field]))
        if fill['target'] == 'product':
            product = _product_named(fields['visa_products'], fill['product_type'], label)
            unpublished = set(product.get('unpublished_fields') or [])
            if absence:
                product[field] = deepcopy(_EMPTY_FEE) if field == 'fee' else None
                unpublished.update(CELLS[field])
            else:
                product[field] = deepcopy(fill['value'])
                unpublished.difference_update(CELLS[field])
            proof = _proof_for(fill['proof'], route, field, product)
            proof['verification_scope'] = SCOPE
            if not isinstance(product.get('field_provenance'), dict):
                product['field_provenance'] = {}
            product['field_provenance'][field] = proof
            if unpublished or 'unpublished_fields' in product:
                product['unpublished_fields'] = sorted(unpublished)
            touched.add('visa_products')
        else:
            unpublished = set(fields.get('unpublished_fields') or [])
            if absence:
                fields[field] = None
                unpublished.update(CELLS[field])
                # The loader's own shape for a deliberate null, so reloading
                # the stored entry changes nothing.
                proofs[field] = {'status': 'unknown', 'verifier': 'ai', 'source_url': '', 'verified_at': None,
                                 'verified_by': '', 'note': 'Not published by the destination: '
                                 + str(fill['proof'].get('reason') or '').strip()}
            else:
                fields[field] = deepcopy(fill['value'])
                unpublished.difference_update(CELLS[field])
                proof = _proof_for(fill['proof'], route, field)
                proof['verification_scope'] = SCOPE
                proofs[field] = vo._provenance(proof)
            touched.add(field)
            if sorted(unpublished) != sorted(fields.get('unpublished_fields') or []):
                fields['unpublished_fields'] = sorted(unpublished)
                touched.add('unpublished_fields')
                if 'unpublished_fields' not in proofs:
                    proofs['unpublished_fields'] = vo._provenance(out)
        applied.append(record)
    return applied, touched


def _served_product_cells(applied, old, new, key):
    """Product by product: identity and order unchanged, only the filled
    cells, their proofs and the absence markers may differ."""
    old_products = old.get('visa_products') or []
    new_products = new.get('visa_products') or []
    if len(old_products) != len(new_products):
        raise PatchRejected('Product inventory changed: ' + key)
    by_product = {}
    for a in applied:
        if a['target'] == 'product':
            by_product.setdefault(a['product_type'], []).append(a)
    for before, after in zip(old_products, new_products, strict=True):
        if before.get('type') != after.get('type'):
            raise PatchRejected('Product identity changed: ' + key)
        fills = by_product.get(before.get('type'), [])
        names = {a['field'] for a in fills}
        if digest(_mask_product(before, names)) != digest(_mask_product(after, names)):
            raise PatchRejected('Unreviewed product facts changed on %s: %s' % (key, before.get('type')))
        expected = set(before.get('unpublished_fields') or [])
        for a in fills:
            served = after.get(a['field'])
            if a['kind'] == 'absence':
                expected.update(a['cells'])
                if not _empty(served):
                    raise PatchRejected('%s: a documented absence left a value behind on %s' % (key, a['field']))
            else:
                expected.difference_update(a['cells'])
                if digest(served) != digest(a['value']):
                    raise PatchRejected('Served product value differs from the reviewed value: ' + key)
        if set(after.get('unpublished_fields') or []) != expected:
            raise PatchRejected('Absence markers changed outside the reviewed fills: ' + key)


def _served_route_cells(applied, old, new, key):
    expected = set(old.get('unpublished_fields') or [])
    for a in applied:
        if a['target'] != 'route':
            continue
        if a['kind'] == 'absence':
            expected.update(a['cells'])
            if new.get(a['field']) is not None:
                raise PatchRejected('%s: a documented absence left a value behind on %s' % (key, a['field']))
        else:
            expected.difference_update(a['cells'])
            if digest(new.get(a['field'])) != digest(a['value']):
                raise PatchRejected('Served value differs from the reviewed value: ' + key)
    if set(new.get('unpublished_fields') or []) != expected:
        raise PatchRejected('Absence markers changed outside the reviewed fills: ' + key)


def _reaches(applied, before, after, key):
    """Every fill must surface on at least one served record as a filled
    cell (or a not-published cell for an absence) that did not read so
    before. A fill nobody can see is not a fill."""
    from app.visa_snapshot import tstation
    for a in applied:
        cells = a['cells']
        expected = ('not-published',) if a['kind'] == 'absence' else ('filled', 'not-applicable')
        name = tstation._clean_text(str(a['product_type'])) if a['target'] == 'product' else None
        hit = False
        for x, y in zip(before, after, strict=True):
            if name is not None and y.get('visa_type_name') != name:
                continue
            sx, sy = tstation.field_status(x), tstation.field_status(y)
            now = all(sy.get(c) in expected for c in cells) and sy.get(cells[0]) == expected[0]
            was = all(sx.get(c) in expected for c in cells) and sx.get(cells[0]) == expected[0]
            if now and not was:
                hit = True
                break
        if not hit:
            raise PatchRejected('%s: the fill does not reach any served cell on %s' % (_label(a), key))


def _grade_credited(a, guidance, provenance, route):
    """Whether the grader will count this fill as its own proved value."""
    from app.visa_snapshot.grade_evidence import _proof_supported
    if a['kind'] == 'absence':
        return None
    try:
        if a['target'] == 'product':
            product = _product_named(guidance.get('visa_products'), a['product_type'], _label(a))
            proof = (product.get('field_provenance') or {}).get(a['field'])
            return bool(_proof_supported(proof, route, product, a['field'], product.get(a['field'])))
        proof = (provenance.get('field_provenance') or {}).get(a['field'])
        return bool(_proof_supported(proof, route, None, a['field'], guidance.get(a['field'])))
    except (PatchRejected, KeyError, TypeError, AttributeError):
        return False


def convert(manifest, layers):
    from app.visa_snapshot import verified_overrides as vo, tstation
    from app.visa_snapshot.kimi_primary import serve_time_invariants
    if not isinstance(manifest, dict) or set(manifest) != {'schema_version', 'kind', 'specification', 'routes'}:
        raise PatchRejected('Malformed field-fill manifest')
    baselines = [dict(deepcopy(r['baseline']), cache_key=r['cache_key']) for r in manifest['routes']]
    if digest(build_manifest(manifest['specification'], baselines)) != digest(manifest):
        raise PatchRejected('Prepared manifest changed')
    spec = manifest['specification']
    current = validate(spec, layers)
    entries, previews, grade_changes = [], [], []
    fills = absences = 0
    for row in sorted(spec['routes'], key=lambda r: r['cache_key']):
        key = row['cache_key']; layer = current[key]; route = layer['route']
        doc = route.get('travel_document_type') or 'ordinary_passport'
        identity, prior = _prior(layer)
        old, checked = vo.merge_verified_fields(layer['raw_guidance'], prior['fields'], source_url=prior['source_url'])
        old_prov = dict(prior['field_provenance'].get('disposition') or vo._provenance(prior), fields=sorted(checked),
                        field_provenance=prior['field_provenance'])
        if digest(old) != digest(layer['merged_guidance']) or digest(old_prov) != digest(layer['source_provenance']):
            raise PatchRejected('Original canonical source reconstruction changed: ' + key)
        out = deepcopy(prior)
        out.update(route=dict(nationality=route['passport_nationality'], destination=route['destination_country'],
                              travel_purpose=route['travel_purpose'], travel_document_type=doc),
                   partial_review=True, review_id=spec['id'], field_review_scope=SCOPE)
        applied, touched = _apply_fills(out, row['fills'], route)
        _tidy_notes(out)
        parsed = vo._parse_rows([out], {}).get(identity)
        if (not parsed or digest(parsed['fields']) != digest(out['fields']) or
                digest(parsed['field_provenance']) != digest(out['field_provenance'])):
            raise PatchRejected('Loader altered reviewed fields/proofs: ' + key)
        g, now_checked = vo.merge_verified_fields(layer['raw_guidance'], parsed['fields'], source_url=parsed['source_url'])
        if set(now_checked) != set(checked) | touched:
            raise PatchRejected('Fill altered checked fields outside its scope: ' + key)
        prov = dict(parsed['field_provenance'].get('disposition') or vo._provenance(parsed), fields=sorted(now_checked),
                    field_provenance=parsed['field_provenance'])
        if g.get('disposition') != old.get('disposition') or g.get('requirement_detail') != old.get('requirement_detail'):
            raise PatchRejected('Fill changed the verdict: ' + key)
        if digest(_mask(g, touched)) != digest(_mask(old, touched)):
            raise PatchRejected('Unreviewed facts changed: ' + key)
        _served_product_cells(applied, old, g, key)
        _served_route_cells(applied, old, g, key)
        op, np_ = deepcopy(old_prov), deepcopy(prov)
        for field in touched:
            op['field_provenance'].pop(field, None); np_['field_provenance'].pop(field, None)
        op.pop('fields'); np_.pop('fields')
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
            if a.get('visa_type_name') != b.get('visa_type_name') or a.get('visa_requirement') != b.get('visa_requirement'):
                raise PatchRejected('Product identity or requirement changed: ' + key)
            grade = (a.get('confidence_level'), b.get('confidence_level'))
            if grade[0] != grade[1]:
                if grade != ('Medium', 'High'):
                    raise PatchRejected('Grade changed other than Medium to High on %s: %s' % (key, b.get('visa_type_name')))
                grade_changes.append(dict(cache_key=key, visa_type_name=b.get('visa_type_name'), before=grade[0], after=grade[1]))
            diff = {c for c in set(a) | set(b) if a.get(c) != b.get(c)}
            if diff - RECORD_COLUMNS_MAY_CHANGE:
                raise PatchRejected('Unexpected record columns changed on %s: %s' % (key, sorted(diff - RECORD_COLUMNS_MAY_CHANGE)))
            changed_columns |= diff
        _reaches(applied, before, after, key)
        for a in applied:
            a['grade_credited'] = _grade_credited(a, g, prov, route)
        fills += sum(1 for a in applied if a['kind'] == 'fill')
        absences += sum(1 for a in applied if a['kind'] == 'absence')
        entries.append(out)
        previews.append(dict(cache_key=key, guidance=g, source_provenance=prov, records=after, fills=applied,
                             filled_fields=sorted(a['field'] for a in applied if a['kind'] == 'fill'),
                             documented_absences=sorted(a['field'] for a in applied if a['kind'] == 'absence'),
                             changed_fields=sorted(touched), changed_record_columns=sorted(changed_columns)))
    return (dict(schema_version=1, kind='reviewed_overlay_conversion', review_id=spec['id'], entries=entries,
                 status='detached; exact-layer preflight required'),
            dict(routes=previews, fills=fills, absences=absences, rejected=[], grade_changes=grade_changes,
                 raw_writes=False, operator_writes=False, issue_changes=False, renew_fresh_until=False,
                 confidence_changed=bool(grade_changes), new_release=False))


def verify_prepared(spec, layers, prepared_manifest, prepared_overlay):
    manifest = build_manifest(spec, layers)
    overlay, report = convert(manifest, layers)
    if digest(manifest) != digest(prepared_manifest) or digest(overlay) != digest(prepared_overlay):
        raise PatchRejected('Prepared artifacts differ from full rebuild')
    return report


def triage(spec, layers):
    """Sort a research sweep's fills into the installable ones and the refused.

    Strict conversion refuses a whole specification on any doubt, which is
    right for a release and useless for reading a sweep. This runs the same
    checks fill by fill, keeps what passes, and returns the trimmed
    specification, the layers it needs and a report naming every accepted
    fill, every documented absence and every rejection with its reason.
    """
    _check_spec_shape(spec)
    sources = _source_table(spec)
    by_key = {l.get('cache_key'): l for l in layers if isinstance(l, dict)}
    kept_routes, kept_layers, accepted, absences, rejected = [], [], [], [], []

    def refuse(key, fill, reason):
        rejected.append(dict(cache_key=key, target=fill.get('target') if isinstance(fill, dict) else None,
                             product_type=fill.get('product_type') if isinstance(fill, dict) else None,
                             field=fill.get('field') if isinstance(fill, dict) else None, reason=reason))

    for row in spec['routes'] if isinstance(spec['routes'], list) else []:
        key = row.get('cache_key') if isinstance(row, dict) else None
        layer = by_key.get(key)
        row_fills = row.get('fills') if isinstance(row, dict) and isinstance(row.get('fills'), list) else []
        if layer is None:
            for fill in row_fills:
                refuse(key, fill, 'Current layer missing')
            continue
        try:
            prior = _route_checks(row, layer)
        except PatchRejected as exc:
            for fill in row_fills:
                refuse(key, fill, str(exc))
            continue
        fills, seen = [], set()
        for fill in row_fills:
            fill_key = _fill_key(fill)
            if fill_key in seen:
                refuse(key, fill, 'Duplicate fill on one cell')
                continue
            seen.add(fill_key)
            try:
                _validate_fill(fill, layer, sources, prior)
                fills.append(fill)
            except PatchRejected as exc:
                refuse(key, fill, str(exc))
        if not fills:
            continue

        def converts(subset):
            one = dict(spec, routes=[dict(row, fills=subset)])
            try:
                convert(build_manifest(one, [layer]), [layer])
            except PatchRejected as exc:
                return str(exc)
            return None

        problem = converts(fills)
        if problem:
            # Conversion judges a whole context. Find the fills that fail
            # alone, then confirm the survivors convert together.
            keep = []
            for fill in fills:
                reason = converts([fill])
                if reason:
                    refuse(key, fill, reason)
                else:
                    keep.append(fill)
            fills = keep
            problem = converts(fills) if fills else None
            if problem:
                for fill in fills:
                    refuse(key, fill, problem)
                continue
        if not fills:
            continue
        kept_routes.append(dict(row, fills=fills))
        kept_layers.append(layer)
        for fill in fills:
            (absences if _wants_absence(fill['proof']) else accepted).append(
                dict(cache_key=key, target=fill['target'], product_type=fill.get('product_type'), field=fill['field'],
                     value=deepcopy(fill['value'])))
    return dict(spec, routes=kept_routes), kept_layers, dict(accepted=accepted, absences=absences, rejected=rejected)


if __name__ == '__main__':
    import json
    import sys
    from pathlib import Path
    if len(sys.argv) != 4:
        raise SystemExit('usage: convert_reviewed_field_fill.py specification.json current-layers.json output-dir')
    spec = json.loads(Path(sys.argv[1]).read_text())
    layers = json.loads(Path(sys.argv[2]).read_text())
    layers = layers.get('layers', layers)
    out_dir = Path(sys.argv[3]); out_dir.mkdir(parents=True, exist_ok=True)
    kept, kept_layers, triage_report = triage(spec, layers)
    report = dict(triage_report, fills=0, absences=0, routes=[])
    if kept['routes']:
        manifest = build_manifest(kept, kept_layers)
        overlay, conversion = convert(manifest, kept_layers)
        (out_dir / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + '\n')
        (out_dir / 'overlay.json').write_text(json.dumps(overlay, ensure_ascii=False, indent=1) + '\n')
        (out_dir / 'specification.json').write_text(json.dumps(kept, ensure_ascii=False, indent=1) + '\n')
        report.update(fills=conversion['fills'], absences=conversion['absences'], grade_changes=conversion['grade_changes'],
                      routes=[dict(cache_key=r['cache_key'], fills=r['fills'], changed_record_columns=r['changed_record_columns'])
                              for r in conversion['routes']])
    (out_dir / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=1) + '\n')
    print(json.dumps(dict(routes=len(kept['routes']), fills=report['fills'], absences=report['absences'],
                          rejected=len(report['rejected']))))
