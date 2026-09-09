"""Convert a general official-source review batch into a reviewed serving overlay.

The Schengen, Australia and Japan batches each carried a closed converter with
pinned rows. This converter is generic in shape but not in trust: every value
still needs its own literal quote from a captured government page whose owner
is the destination, a verdict needs a statement about THIS nationality on such a
page, figures must occur in their own evidence, products bind to the exact
current product identity, and the whole batch binds to captured production
layers that a deployer re-checks under maintenance. No database, seed, operator
or issue writes happen here; the output is a detached candidate.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import hashlib
import json
import re
import unicodedata

from scripts.prepare_reviewed_product_patch import PatchRejected, digest, route_identity
from scripts.convert_reviewed_product_patch import field_provenance

KIND = 'general_reviewed_batch'
VERIFIED_BY = 'Ellis AI official-source field review'
BASELINE_KEYS = ('route', 'raw_guidance', 'merged_guidance', 'source_provenance',
                 'seed_entries', 'operator_entries')
ROUTE_FIELDS = ('permitted_stay_days', 'permitted_stay', 'government_fee', 'application_channel',
                'application_channel_detail', 'processing_time', 'official_portal_url',
                'required_documents', 'exceptions', 'arrival_card')
# A route-level proof key covers the value keys listed with it.
ROUTE_PROOF_COVERS = {'permitted_stay_days': ('permitted_stay_days', 'permitted_stay'),
                      'government_fee': ('government_fee',),
                      'application_channel': ('application_channel', 'application_channel_detail'),
                      'processing_time': ('processing_time',),
                      'official_portal_url': ('official_portal_url',),
                      'required_documents': ('required_documents',),
                      'exceptions': ('exceptions',),
                      'arrival_card': ('arrival_card',)}
PRODUCT_FIELDS = ('entry', 'validity', 'max_stay_days', 'fee', 'notes')
PRODUCT_PROOF_FIELDS = ('disposition', 'entry', 'validity', 'max_stay_days', 'fee')
# Which 25-field record cells a not-published proof marks.
NOT_PUBLISHED_CELLS = {'permitted_stay_days': ('max_stay_duration', 'max_stay_unit'),
                       'max_stay_days': ('max_stay_duration', 'max_stay_unit'),
                       'validity': ('validity_duration', 'validity_unit'),
                       'fee': ('visa_fee_amount', 'visa_fee_currency'),
                       'government_fee': ('visa_fee_amount', 'visa_fee_currency'),
                       'processing_time': ('processing_min_days', 'processing_unit'),
                       'entry': ('entries',)}
_CONDITION_RE = re.compile(r"hold(?:er|ers|ing)?\b|valid (?:visa|permit|residence)|provided|only if|subject to|"
                           r"regist(?:er|ration)|in transit|transit(?:ing)? through|group|accompan", re.I)


def _norm(value):
    return ' '.join(unicodedata.normalize('NFKC', str(value or '')).casefold().split())


def _today():
    return date.today()


def _source_table(batch):
    sources = {}
    for source in batch.get('sources') or []:
        sid = source.get('id')
        if not sid or sid in sources:
            raise PatchRejected('Missing or duplicate source id')
        text = source.get('text')
        if not isinstance(text, str) or not text.strip():
            raise PatchRejected('Empty source capture: ' + str(source.get('url')))
        if hashlib.sha256(text.encode()).hexdigest() != source.get('sha256'):
            raise PatchRejected('Source capture hash mismatch: ' + str(source.get('url')))
        from app.visa_snapshot.authority import hostname, is_government_host
        if not is_government_host(hostname(str(source.get('url') or ''))):
            raise PatchRejected('Unofficial source: ' + str(source.get('url')))
        try:
            day = date.fromisoformat(str(source.get('checked_at', ''))[:10])
        except ValueError as exc:
            raise PatchRejected('Invalid source-read date') from exc
        if day > _today():
            raise PatchRejected('Future source-read date')
        sources[sid] = source
    return sources


def _check_proof(proof, sources, route, field, value, *, product=None):
    """Return the validated proof or None for an explicit unknown/not_published."""
    if not isinstance(proof, dict) or proof.get('verifier', 'ai') != 'ai':
        raise PatchRejected(f'{field}: the source review must be attributed to AI')
    status = proof.get('status')
    if status in ('unknown', 'not_published'):
        if value not in (None, [], {}, '') and not (isinstance(value, dict) and all(v is None for v in value.values())):
            raise PatchRejected(f'{field}: an unknown or unpublished value must be empty')
        if not str(proof.get('reason') or '').strip():
            raise PatchRejected(f'{field}: an unknown or unpublished value needs a reason')
        return None
    if status != 'reviewed':
        raise PatchRejected(f'{field}: unknown proof status')
    evidence = proof.get('evidence')
    if not isinstance(evidence, list) or not evidence:
        raise PatchRejected(f'{field}: missing evidence')
    from app.visa_snapshot.evidence_validator import quote_in_text, jurisdiction_matches
    destination = route['destination_country']
    for item in evidence:
        source = sources.get(item.get('source_id'))
        if not source or source['url'] != item.get('source_url'):
            raise PatchRejected(f'{field}: evidence cites an uncaptured page')
        quote = item.get('quote')
        if not isinstance(quote, str) or len(quote.strip()) < 8 or '...' in quote or '…' in quote:
            raise PatchRejected(f'{field}: a quote must be a literal passage without ellipsis')
        if not quote_in_text(quote, source['text']):
            raise PatchRejected(f'{field}: quote is not on its captured page')
    if not any(jurisdiction_matches(item['source_url'], destination) for item in evidence):
        raise PatchRejected(f'{field}: no destination-government page in the evidence')
    if not str(proof.get('scope_note') or '').strip():
        raise PatchRejected(f'{field}: the proof must state its scope')
    if not str(proof.get('verified_at') or '').strip():
        raise PatchRejected(f'{field}: the proof needs a verification date')
    passages = '\n'.join(item['quote'] for item in evidence)
    _check_value(field, value, passages, route, product)
    return proof


_CURRENCY_SYMBOLS = {
    'USD': (r'US\$', r'U\.S\.\$', r'\$'), 'EUR': (r'€', r'\beuros?\b'), 'GBP': (r'£',),
    'JPY': (r'¥', r'円', r'\byen\b'), 'CNY': (r'¥', r'元', r'\bRMB\b', r'人民币'), 'KRW': (r'₩', r'원', r'\bwon\b'),
    'INR': (r'₹', r'\bRs\.?', r'\brupees?\b'), 'THB': (r'฿', r'\bbaht\b'), 'IDR': (r'\bRp\.?', r'\brupiah\b'),
    'MYR': (r'\bRM\b', r'\bringgit\b'), 'SGD': (r'S\$', r'SG\$', r'\$'), 'HKD': (r'HK\$', r'\$'), 'AUD': (r'A\$', r'AU\$', r'\$'),
    'CAD': (r'C\$', r'CA\$', r'CAN\$', r'\$'), 'TWD': (r'NT\$', r'\$'), 'RUB': (r'₽', r'руб\.?', r'\brub\b'),
    'VND': (r'₫', r'đ', r'VNĐ', r'\bdong\b'), 'PHP': (r'₱', r'\bpesos?\b'), 'NZD': (r'NZ\$', r'\$'), 'MXN': (r'MX\$', r'\$'),
    'MOP': (r'MOP\$',), 'CHF': (r'\bfrancs?\b', r'\bFr\.'), 'AED': (r'\bdirhams?\b', r'\bDhs?\b'),
    'SAR': (r'\briyals?\b', r'\bSR\b'), 'TRY': (r'₺', r'\blira\b'), 'EGP': (r'\bE£', r'\bLE\b'), 'BRL': (r'R\$'),
}


def _monetary_text(passages, code):
    """Official pages write $25, Rp1.650.000 or 715元; the validator needs the
    ISO code beside the number. Only the value's own currency is rewritten."""
    text = passages
    for symbol in _CURRENCY_SYMBOLS.get(code, ()):
        text = re.sub(symbol, f' {code} ', text, flags=re.I)
    # Indonesian and some European pages use dots as thousands separators.
    if code in ('IDR', 'VND', 'EUR', 'RUB'):
        text = re.sub(r'(?<=\d)\.(?=\d{3}\b)', ',', text)
    return text


def _check_value(field, value, passages, route, product):
    from app.visa_snapshot.evidence_validator import field_value_supported, supports_disposition, _NATIONALITY_NAMES
    if field == 'disposition':
        nat = route['passport_nationality']
        aliases = _NATIONALITY_NAMES.get(nat, ())
        low = _norm(passages)
        named = any(_norm(alias) in low for alias in aliases)
        if value == 'CONDITIONAL':
            if not (named and _CONDITION_RE.search(passages)):
                raise PatchRejected('disposition: a conditional rule needs the nationality and its condition in the evidence')
            return
        if supports_disposition(passages, value, nationality=nat):
            return
        if named and supports_disposition(passages, value):
            return
        raise PatchRejected('disposition: evidence does not state this verdict for this nationality')
    if field in ('permitted_stay_days', 'max_stay_days'):
        if value is not None and not field_value_supported(field, value, passages):
            raise PatchRejected(f'{field}: the figure is not in its evidence')
    elif field in ('government_fee', 'fee'):
        if isinstance(value, dict) and value.get('amount') not in (None, 0):
            text = _monetary_text(passages, str(value.get('currency') or '').upper())
            if not field_value_supported(field, value, text):
                raise PatchRejected(f'{field}: the fee amount is not in its evidence')
        elif isinstance(value, dict) and value.get('amount') == 0:
            if not re.search(r'free|no fee|no charge|gratis|waived|exempt|0\b|no visa', passages, re.I):
                raise PatchRejected(f'{field}: a zero fee needs an explicit free, waived or no-visa statement')
    elif field == 'processing_time':
        if value and not field_value_supported(field, value, passages):
            # Working-day and calendar figures must appear in the evidence.
            digits = re.findall(r'\d+', str(value))
            if digits and not all(d in passages for d in digits):
                raise PatchRejected('processing_time: the figure is not in its evidence')
    elif field == 'application_channel':
        if value and not field_value_supported(field, {'online_portal': 'online', 'embassy_or_consulate': 'embassy',
                                                       'embassy_designated_agency': 'authorised_agent',
                                                       'authorised_agent': 'authorised_agent', 'on_arrival': 'on_arrival',
                                                       'not_required': 'none'}.get(value, value), passages):
            raise PatchRejected('application_channel: the channel is not described in its evidence')
    elif field == 'official_portal_url':
        if value and _norm(value) not in _norm(passages) and not re.search(r'apply|portal|online|website|e-?visa', passages, re.I):
            raise PatchRejected('official_portal_url: the portal is not described in its evidence')
    elif field == 'entry':
        if value and not re.search({'single': r'single|one entry|一次', 'double': r'double|two entries|二次|兩次|两次',
                                    'multiple': r'multiple|multi|数次|多次'}[value], passages, re.I):
            raise PatchRejected('entry: the entry count is not in its evidence')
    elif field == 'validity':
        digits = re.findall(r'\d+', str(value or ''))
        if value and digits and not all(d in passages for d in digits):
            raise PatchRejected('validity: the validity figure is not in its evidence')


def validate_batch(batch):
    if batch.get('kind') != KIND or batch.get('schema_version') != 1 or not batch.get('id'):
        raise PatchRejected('Not a general reviewed batch')
    sources = _source_table(batch)
    rows = batch.get('rows') or []
    if not rows or len({r.get('cache_key') for r in rows}) != len(rows):
        raise PatchRejected('Empty batch or duplicate route rows')
    from app.visa_snapshot.kimi_primary import DISPOSITIONS
    from app.visa_snapshot.verified_overrides import _DETAIL_FAMILY
    for row in rows:
        route = row.get('route') or {}
        if not all(route.get(k) for k in ('passport_nationality', 'destination_country', 'travel_purpose')):
            raise PatchRejected('Incomplete route identity')
        route.setdefault('travel_document_type', 'ordinary_passport')
        verdict = row.get('verdict') or {}
        disp, detail = verdict.get('disposition'), verdict.get('requirement_detail')
        if disp not in DISPOSITIONS or detail not in _DETAIL_FAMILY.get(disp, ()):
            raise PatchRejected('Verdict outside the disposition and subcategory vocabulary')
        if _check_proof(verdict.get('proof'), sources, route, 'disposition', disp) is None:
            raise PatchRejected('A published verdict cannot be unknown')
        proofs = row.get('route_field_proofs') or {}
        values = row.get('route_fields') or {}
        for key, covered in ROUTE_PROOF_COVERS.items():
            if key in proofs:
                _check_proof(proofs[key], sources, route, key, values.get(key))
            elif any(values.get(c) not in (None, [], '') for c in covered):
                raise PatchRejected(f'{key}: a value without a proof')
        for product in row.get('products') or []:
            if product.get('action') not in ('keep', 'patch', 'remove', 'add'):
                raise PatchRejected('Unknown product action')
            if product['action'] == 'remove':
                if not str(product.get('remove_reason') or '').strip():
                    raise PatchRejected('A removed product needs a reason')
                continue
            spec = product.get('product') or {}
            if not str(spec.get('type') or '').strip():
                raise PatchRejected('A product needs a name')
            pd, pdet = spec.get('disposition'), spec.get('requirement_detail')
            if pd not in DISPOSITIONS or pdet not in _DETAIL_FAMILY.get(pd, ()):
                raise PatchRejected('Product verdict outside the vocabulary: ' + spec['type'])
            pproofs = product.get('proofs') or {}
            for field in PRODUCT_PROOF_FIELDS:
                value = spec.get(field)
                if field in pproofs:
                    _check_proof(pproofs[field], sources, route, field, value, product=spec)
                elif value not in (None, '', {}) and not (isinstance(value, dict) and value.get('amount') is None):
                    raise PatchRejected(f'{spec["type"]}: {field} has a value but no proof')
    return sources


def build_manifest(batch, layers):
    """Bind the batch to exact captured serving layers; no writes."""
    validate_batch(batch)
    by_key = {r['cache_key']: r for r in batch['rows']}
    if not isinstance(layers, list) or {l.get('cache_key') for l in layers} != set(by_key) or len(layers) != len(by_key):
        raise PatchRejected('Missing, extra or duplicate baseline routes')
    entries = []
    for layer in layers:
        row = by_key[layer['cache_key']]
        if any(k not in layer for k in BASELINE_KEYS):
            raise PatchRejected('Missing raw/merged/ordered source layer capture')
        if route_identity(layer['route']) != route_identity(row['route']):
            raise PatchRejected('Stored route differs from the reviewed subject: ' + layer['cache_key'])
        products = layer['merged_guidance'].get('visa_products') or []
        matches = []
        for product in row.get('products') or []:
            name = product.get('current_name')
            if product['action'] == 'add':
                if any(p.get('type') == (product.get('product') or {}).get('type') for p in products):
                    raise PatchRejected('Added product already exists: ' + str((product.get('product') or {}).get('type')))
                matches.append({'current_name': None, 'product_sha256': None})
                continue
            found = [p for p in products if p.get('type') == name]
            if len(found) != 1:
                raise PatchRejected('Missing or ambiguous current product identity: ' + str(name))
            matches.append({'current_name': name, 'product_sha256': digest(found[0])})
        baseline = {k: deepcopy(layer[k]) for k in BASELINE_KEYS}
        entries.append({'cache_key': layer['cache_key'], 'route': deepcopy(layer['route']), 'matches': matches,
                        'baseline': baseline, 'baseline_sha256': {k: digest(v) for k, v in baseline.items()}})
    return {'schema_version': 1, 'kind': 'general_reviewed_exact_layers', 'id': batch['id'],
            'batch': deepcopy(batch), 'routes': entries,
            'status': 'detached candidate; no registration or activation'}


def _prior(layer, route):
    from app.visa_snapshot import verified_overrides as vo
    table = vo._parse_rows(layer['seed_entries'], {})
    if layer['operator_entries']:
        raise PatchRejected('Operator edits on this route need separate review: ' + layer['cache_key'])
    return table.get(vo._key(route['passport_nationality'], route['destination_country'],
                            route['travel_purpose'], route.get('travel_document_type') or 'ordinary_passport')) or {}


def _proof_for(proof, route, field, product=None):
    if proof.get('status') == 'not_published':
        # The store records an explicit null with its reason; the record
        # cell reads "not published" through unpublished_fields.
        proof = {'status': 'unknown', 'verifier': 'ai',
                 'reason': 'Not published by the destination: ' + str(proof.get('reason') or '').strip()}
    return field_provenance(proof, route, field, product)


def convert(manifest, current_layers):
    """Return the overlay candidate and a per-route report."""
    from app.visa_snapshot import verified_overrides as vo
    from app.visa_snapshot.kimi_primary import serve_time_invariants
    if manifest.get('kind') != 'general_reviewed_exact_layers' or manifest.get('schema_version') != 1:
        raise PatchRejected('Malformed general integration manifest')
    batch = manifest['batch']
    sources = validate_batch(batch)
    rebuilt = build_manifest(batch, [dict(deepcopy(e['baseline']), cache_key=e['cache_key']) for e in manifest['routes']])
    for actual, checked in zip(manifest['routes'], rebuilt['routes'], strict=True):
        if actual != checked:
            raise PatchRejected('Match or baseline contract was altered')
    rows = {r['cache_key']: r for r in batch['rows']}
    current = {l['cache_key']: l for l in current_layers}
    if len(current) != len(current_layers) or set(current) != {e['cache_key'] for e in manifest['routes']}:
        raise PatchRejected('Current route set differs from baseline')
    today = _today().isoformat()
    entries, reports = [], []
    for entry in manifest['routes']:
        layer = current[entry['cache_key']]; route = entry['route']; row = rows[entry['cache_key']]
        for key in BASELINE_KEYS:
            if digest(layer.get(key)) != entry['baseline_sha256'][key]:
                raise PatchRejected('Layer changed since the reviewed baseline: ' + key + ' ' + entry['cache_key'])
        old = _prior(layer, route)
        fields = deepcopy(old.get('fields') or {})
        proofs = deepcopy(old.get('field_provenance') or {})
        unpublished = set(fields.get('unpublished_fields') or [])
        verdict = row['verdict']
        fields['disposition'] = verdict['disposition']
        fields['requirement_detail'] = verdict['requirement_detail']
        vproof = _proof_for(verdict['proof'], route, 'disposition')
        proofs['disposition'] = vproof
        proofs['requirement_detail'] = dict(vproof, note='The subcategory is read from the same verified verdict passage.')
        values = row.get('route_fields') or {}
        for key, covered in ROUTE_PROOF_COVERS.items():
            proof = (row.get('route_field_proofs') or {}).get(key)
            if proof is None:
                continue
            if proof.get('status') in ('unknown', 'not_published'):
                for c in covered:
                    fields[c] = None
                    proofs[c] = _proof_for(proof, route, c)
                if proof.get('status') == 'not_published':
                    unpublished.update(NOT_PUBLISHED_CELLS.get(key, ()))
                continue
            for c in covered:
                if c in values and values[c] not in (None, '', []):
                    fields[c] = deepcopy(values[c])
                    proofs[c] = _proof_for(proof, route, c)
        # Products: the current merged list is the starting point.
        products = deepcopy(layer['merged_guidance'].get('visa_products') or [])
        by_name = {p.get('type'): p for p in products}
        final, unsupported, removed = [], [], []
        seen = set()
        for spec in row.get('products') or []:
            action = spec['action']
            if action == 'remove':
                removed.append({'type': spec.get('current_name'), 'reason': spec.get('remove_reason')})
                seen.add(spec.get('current_name'))
                continue
            base = deepcopy(by_name.get(spec.get('current_name')) or {}) if action != 'add' else {}
            seen.add(spec.get('current_name'))
            product = dict(base)
            pspec = spec['product']
            product['type'] = pspec['type']
            product['disposition'] = pspec['disposition']
            product['requirement_detail'] = pspec['requirement_detail']
            pproofs = deepcopy(product.get('field_provenance') or {})
            for field in PRODUCT_FIELDS:
                proof = (spec.get('proofs') or {}).get(field)
                if field in pspec and (proof is not None or action == 'add'):
                    if proof is not None and proof.get('status') in ('unknown', 'not_published'):
                        product[field] = None if field != 'fee' else {'amount': None, 'currency': None}
                        pproofs[field] = _proof_for(proof, route, field, product)
                        if proof.get('status') == 'not_published':
                            unpublished.update(NOT_PUBLISHED_CELLS.get(field, ()))
                    elif proof is not None:
                        product[field] = deepcopy(pspec[field])
                        pproofs[field] = _proof_for(proof, route, field, product)
            decision = (spec.get('proofs') or {}).get('disposition')
            if decision is None or decision.get('status') != 'reviewed':
                unsupported.append(product['type'])
                product['field_provenance'] = pproofs
                final.append(product)
                continue
            dproof = _proof_for(decision, route, 'disposition', product)
            pproofs['disposition'] = dproof
            pproofs['requirement_detail'] = dict(dproof, note='The subcategory is read from the same verified verdict passage.')
            product['field_provenance'] = pproofs
            product['source_url'] = dproof['source_url']
            product['source_quote'] = dproof['quote']
            product['verified_at'] = dproof['verified_at']
            product['verifier'] = 'ai'
            product['corroborating_sources'] = [
                {'url': e['source_url'], 'quote': e['quote'], 'checked_at': dproof['verified_at'],
                 'authority': 'Official source; AI field review'}
                for e in decision['evidence'][1:]]
            final.append(product)
        for name, product in by_name.items():
            if name not in seen:
                unsupported.append(name)
                final.append(deepcopy(product))
        if fields['disposition'] == 'VISA_EXEMPT':
            final = [p for p in final if (p.get('fee') or {}).get('amount') in (None, 0)]
        fields['visa_products'] = final
        fields['unpublished_fields'] = sorted(unpublished)
        fields['source_url'] = vproof['source_url']
        proofs['visa_products'] = dict(vproof, note='Product rows carry their own reviewed verdict proofs.')
        errors = vo._field_errors(fields)
        if errors:
            raise PatchRejected(entry['cache_key'] + ': ' + '; '.join(errors))
        # Preview exactly what the store loader will serve, including the
        # exemption and application leftover clean-up, before accepting.
        parsed = vo._parse_rows([{'route': {'nationality': route['passport_nationality'], 'destination': route['destination_country'],
                                            'travel_purpose': route['travel_purpose'],
                                            'travel_document_type': route.get('travel_document_type') or 'ordinary_passport'},
                                  'verified_at': today, 'verified_by': VERIFIED_BY, 'verifier': 'ai',
                                  'source_url': vproof['source_url'], 'note': 'preview', 'fields': fields,
                                  'field_provenance': proofs}], {})
        served_fields = next(iter(parsed.values()))['fields'] if parsed else {}
        merged, _ = vo.merge_verified_fields(deepcopy(layer['raw_guidance']), served_fields, source_url=vproof['source_url'])
        problems = serve_time_invariants(merged)
        if problems:
            raise PatchRejected(entry['cache_key'] + ': the reviewed answer would contradict itself: ' + '; '.join(problems))
        entries.append({'route': {'nationality': route['passport_nationality'], 'destination': route['destination_country'],
                                  'travel_purpose': route['travel_purpose'],
                                  'travel_document_type': route.get('travel_document_type') or 'ordinary_passport'},
                        'verified_at': today, 'verified_by': VERIFIED_BY, 'verifier': 'ai',
                        'source_url': vproof['source_url'],
                        'note': str(verdict['proof'].get('scope_note') or '')[:400],
                        'fields': fields, 'field_provenance': proofs,
                        'review_id': batch['id'], 'cache_key': entry['cache_key']})
        reports.append({'cache_key': entry['cache_key'], 'disposition': fields['disposition'],
                        'products': [p['type'] for p in final], 'unsupported_products': unsupported,
                        'removed_products': removed, 'unpublished': sorted(unpublished)})
    overlay = {'schema_version': 1, 'kind': 'reviewed_overlay_conversion', 'review_id': batch['id'],
               'reviewed_at': today, 'status': 'candidate; registered only after preflight under maintenance',
               'contract': 'Every value carries its own literal official-page quote; verdicts name the nationality; '
                           'products bind to the exact current product; baseline layers are hash-pinned.',
               'entries': entries}
    return overlay, reports


if __name__ == '__main__':
    import sys
    batch = json.loads(open(sys.argv[1]).read())
    layers = json.loads(open(sys.argv[2]).read())
    layers = layers.get('layers', layers)
    manifest = build_manifest(batch, layers)
    overlay, reports = convert(manifest, layers)
    json.dump(overlay, open(sys.argv[3], 'w'), ensure_ascii=False, indent=1)
    json.dump({'manifest_routes': [{k: e[k] for k in ('cache_key', 'matches', 'baseline_sha256')} for e in manifest['routes']],
               'reports': reports}, open(sys.argv[4], 'w'), ensure_ascii=False, indent=1)
    print(json.dumps({'entries': len(overlay['entries']), 'unsupported_products': sum(len(r['unsupported_products']) for r in reports)}))
