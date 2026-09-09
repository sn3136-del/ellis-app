"""Closed Japan26 correction candidate; no activation, network or database writes.

The exact separately researched policy values and their captured official text
are pinned below. This is a finite reviewed batch, not a generic claim validator:
changing a country, product, value, quote or source needs a new explicit review.
Current raw, merged, source-provenance and ordered seed/operator layers must all
match the captured baseline. Unreviewed products and old field authorship remain.
"""
from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path

from scripts.prepare_reviewed_product_patch import PatchRejected, digest, route_identity
from scripts.convert_reviewed_product_patch import field_provenance

REVIEW_ID = 'japan26-20260909'
UNSUPPORTED_ROWS = {0, 8, 9, 10}
AMBIGUOUS_FORMAT_ROWS = {1, 2, 3, 13, 14, 15, 16, 24, 25}
PRODUCTLESS_ROWS = {4, 5, 17, 18, 19, 20, 21, 23}
ISL_UNSUPPORTED_EXTENSION = 'Status extension may be requested at a Regional Immigration Services Bureau before the 90-day period expires'
VNM_FEE_WINDOW = {'date_role': 'application_acceptance',
                  'from': '2026-07-01', 'through': '2027-03-31'}
# Source audit checkpoint, not model-generated proof. See the file's contract.
PINNED_ROWS = {'0': '17ec863a379dec6a36cb4b9ab37878496ef7608cfffc39f0e1df19a894bd8524', '1': '55012b91aaf7d06893ca34036c51de4644fe142f08c0e5d6c87a4751a5f5be4b', '2': 'a5c56d147a276b19273a4c8d43cee5d353e0cdb1e0f84c2ddad7f8c2343a7f61', '3': '5d32723f0ea4fc79afa121cac3081dcaaa55eb0b392d125025f41ce584b078a0', '4': 'b625766bf8f04d5e940b1a7920420aff160fe7badc6f18521580f70942708bd1', '5': 'a2a28bd230f3d1b378436617d1761deebb2ffaddada6664666f5fab237956117', '6': 'c7ecb617d94d9196ae831a28ddf093dc9af98879415704a9aa65c1cb070b3ba6', '7': 'e2aea338632aa47f832cae12ef5da9922e7ba2c299b6e154b0ed824740581444', '8': '8f21d7bb997a5e6e9b4f9a3391d047cb990bee58cf07d5be30f4d8e5f3be3757', '9': '130402c62e3038e4ad4cbc95a3bbd478eb225858aba908bd63396d4301248a07', '10': '16a705fb6a2154653441a9e8e308a0f0d9245acada3ebaa0c8f501996ab701ca', '11': '4852e28d3489f40cbd2aa7909150fff551e241786948c2c2583af093e472e019', '12': '94d2d23617c7ce4c331c8bda12e8d94798f70e376d5e92a7f8fda1b0b98697bd', '13': '469b64a6992715d6314984342638c0635092f7090c31ea495205be2694c9c966', '14': '283b2ddf352b256fb55e581ef165eff89da56852e46790443b39b8723d6bdad9', '15': '8c69b9648d6587c0a652963d48ed59601917ddfbf81369d249b501ebdcf3ebde', '16': '4e050d2fd129ab132bb34ff51fad6124e172ec079116cb6f584dfb37c79c351d', '17': 'e90d3ad8a135ad3b52d73c9bee938788553df00538b15203122c02245d7e39ff', '18': 'f8e9861964fdd9d235b047a54edf5a839e3b666391ec9f5e02f98412215688a2', '19': 'f39887743e2f7224bfc1d2d18fed5c8235e8324a891e9bf47b58549d38e30ec5', '20': '117b79be09140a92c90d6b86c926ad412e72e146ae803010f188170202cda027', '21': 'bf170d22546f8f37a46575980ac6decc6e649c2a8fc104479cd1f8a7759d7c2e', '22': 'eda5d59755ae2880a2aded6c16b49c2f24c7c3afbf9bdf1ef09ca4104d005317', '23': 'fd6ad6e4bfc2812452ed686614876d5ee7956a1aac0bf004928e4418de230ece', '24': '7bdd0de6ab92757bced385498179112106598a29cba77ff335d83d3cd0d391b3', '25': 'dab75f3f61ab90f4e9c1a938e367f68cdee0f8a1e70779da9c6a36e1ea1aadb9'}
PINNED_SOURCES = {'exempt': 'ba448b96a3415c022eef582c2cb54e2d9270331dca18847d5dacdd7147a9edde', 'evisa': 'a2723861bce08fa6eed580191cdde547a58cd818bcd09dcb060d37e64c4daf33', 'faq': '927f27261c88bf6b2a83e59de01c54370cc6a4b09f8911e465ed8d4cf645d32a', 'evisa-faq': 'b4bbb9ce65f186c6328321f4abe72b144bcd8f605b4cae74fe77d81b77d53222', 'general-fees': 'b9be0750fd16f9388f9403d8f0477b42a721659fb20c1e282fe813cb182e0334', 'ind-multiple': 'f76a15081195ff4dead78f9e8959b156a0e15ff264ee33c16ddf1991b23a57a0', 'ind-fees': 'b0b17192fd7f253ba2db96735eaf1aede51dc9adf4c806cd6bab2ad3ea702138', 'vnm-main': 'c7f6ed5b61f5621c1bcbdf27b98738a65db54ad5ea8a254b9f59147d9474bb35', 'vnm-fees': 'abcb3781f88dae72919be7dd041fed03833b4a6b9ac23ad71f69e2a303f4013f', 'vnm-single': '68f0cec11361df92479d4bb9295ac137fe879c5681316a2a4b934cdfdcfc6527', 'vnm-multiple': 'e9c369fe8375f5dda105045883acdffab31e8c85d8278a656ebea3ae627af5f3', 'phl-main': '854b264f5902c4f668952a6d0410018f7147857e0a94c3b08ceefb7d26163248', 'chn-faq': 'd363875c3c1442bacf5895ba058bdec33cc701753713a4379d6b3a7fe85f433c', 'chn-tourism': 'b1398711ec8e676cbb4bd03e593b21b3ebdf4df52e09559c83e1436e9dd72486', 'chn-tourism-docs': '06a3c9715f59346f13688e545848eb8c09415f6915bbd748942a950ec14a28b8', 'chn-family': 'f5868aecddfa1f87bab4bdf7a8de54602a917df8ade72b5084baa505264a5c3a', 'chn-family-resident': '44d3b065dd342af489dd02bdbf4e4baac682ef4834c6185e1eeceb954a2c4214', 'chn-family-japanese': '55337e94add1a63e5523eece075031437aaa8b02145940c0f64203a6322957ab', 'chn-business': 'a3ec474da11aee71429b4e33cc8067a464efa2cf01a1d7ff6673bd5e14f09850', 'chn-business-multiple': '4e835d8515b3becb85150b16afe560883d8e22431c4e098d459a1d12332be2a8', 'student': 'b7aa874345306ede6dbd6eed1f70bfc35a7e41796c75255d36ebd1d9ce7bb344', 'idn-main': '1094d7cb2539c55818eecdd2fa75fc6a6ab8b7bae0bd18256a3fb0fffb01fe06', 'idn-single': 'bf86a0ac7521aefd81793ce48f2c7e41e391cf727f8d998724b80c50fa2ac2d6', 'idn-multiple': '29eac747fedf45545c528ec1a7d51ac9f33d46d7c2436689866e9c9145ae63bb', 'national-main': '8cdcb5f1e6e88880eb8dd22c80b8237e005568719207415cc5b7cc1223da380f', 'national-idn-multiple': 'c7aaa0a5e198ecdcb6093d52b498ed1c4f98b4f848537df9b925ce06aefd9fad', 'uk-scope': '0768bebd67daa9b39fd80e1d437c2d6f6a8bce6fc3e2e64417c87391549c951c', 'license': '5e11e54e6301ba6ee9fc4a684f4a1959a537b28104d8646d9fbcb8ea93019767'}
PROTECTED = {'held', 'operator_released', 'verification', 'fresh_until',
             'generated_at', 'policy_valid_until', 'confidence', 'grounded_check'}
BASELINE_KEYS = ('route', 'raw_guidance', 'merged_guidance', 'source_provenance',
                 'seed_entries', 'operator_entries')


def _norm(text):
    return ' '.join(str(text).split())


def validate_review(review):
    """Only the exact source-reviewed batch can supply new field evidence."""
    from app.visa_snapshot.authority import hostname, is_government_host
    from app.visa_snapshot.evidence_validator import jurisdiction_matches
    if review.get('id') != REVIEW_ID or len(review.get('rows', [])) != 26:
        raise PatchRejected('Not the reviewed Japan26 batch')
    sources = {s['id']: s for s in review['sources']}
    if len(sources) != len(review['sources']) or set(sources) != set(PINNED_SOURCES):
        raise PatchRejected('Changed or duplicate Japan source catalog')
    for sid, source in sources.items():
        snapshot = {k: source.get(k) for k in ('url', 'text', 'checked_at', 'sha256', 'reading_method')}
        if digest(snapshot) != PINNED_SOURCES[sid]:
            raise PatchRejected('Japan source capture changed; a new source review is required')
        if (not is_government_host(hostname(source['url']))
                or not jurisdiction_matches(source['url'], 'JPN')
                or hashlib.sha256(source['text'].encode()).hexdigest() != source['sha256']
                or date.fromisoformat(source['checked_at']) > date.today()):
            raise PatchRejected('Unread, invalid, foreign or future Japan evidence')
    if {str(row['row_index']) for row in review['rows']} != set(PINNED_ROWS):
        raise PatchRejected('Changed or duplicate Japan row scope')
    if len({row['row_index'] for row in review['rows']}) != 26:
        raise PatchRejected('Duplicate reviewed Japan row')
    for row in review['rows']:
        if digest(row) != PINNED_ROWS[str(row['row_index'])]:
            raise PatchRejected('Japan policy row changed; a new scoped review is required')
        route = row['route']
        if route['destination_country'] != 'JPN' or route['travel_document_type'] != 'ordinary_passport':
            raise PatchRejected('Wrong Japan destination/document scope')
        if set(row['product_patch']) != set(row['field_provenance']):
            raise PatchRejected('Every Japan changed value needs its own review or explicit unknown')
        for field, proof in row['field_provenance'].items():
            value = row['product_patch'][field]
            if proof['status'] == 'unknown':
                if value is not None or not proof.get('reason'):
                    raise PatchRejected('Unknown Japan facts must remain null')
                continue
            if proof['verifier'] != 'ai' or proof['verified_at'] != '2026-09-09' or not proof.get('scope_note'):
                raise PatchRejected('Missing dated AI field attribution')
            for item in proof['evidence']:
                src = sources[item['source_id']]
                if item['source_url'] != src['url'] or _norm(item['quote']) not in _norm(src['text']):
                    raise PatchRejected('Japan quote is not in its own official capture')
    return sources


def _unknown(reason, route, product=None):
    proof = {'status': 'unknown', 'reason': reason, 'verifier': 'ai'}
    return field_provenance(proof, route, 'disposition', product)


def _translate(proof, route, field, product=None):
    result = field_provenance(proof, route, field, product)
    result['review_id'] = REVIEW_ID
    if field in {'fee', 'government_fee'} and any(
            e.get('source_id') == 'vnm-fees' for e in proof.get('evidence', [])):
        # This fee window concerns application acceptance, never travel date.
        # Keep it machine-readable in the field's retained subject and leave an
        # explicit activation blocker until a field-specific reader supports it.
        result['subject']['application_acceptance_window'] = deepcopy(VNM_FEE_WINDOW)
    return result


def build_manifest(review, layers):
    """Bind the reviewed rows to an exact captured serving state, without writes."""
    validate_review(review)
    grouped = {}
    for row in review['rows']:
        grouped.setdefault(row['cache_key'], []).append(row)
    if (not isinstance(layers, list) or len(layers) != len(grouped)
            or {r['cache_key'] for r in layers} != set(grouped)):
        raise PatchRejected('Missing, extra or duplicate Japan baseline routes')
    entries = []
    for layer in layers:
        key = layer['cache_key']; rows = grouped[key]; route = layer['route']
        if any(route_identity(route) != route_identity(row['route']) for row in rows):
            raise PatchRejected('Actual stored route differs from the reviewed Japan subject')
        if any(k not in layer for k in BASELINE_KEYS):
            raise PatchRejected('Missing raw/merged/ordered source layer capture')
        products = layer['merged_guidance'].get('visa_products') or []
        matches = []
        for row in rows:
            name = row['match']['projected_visa_type_name']
            if row['row_index'] in PRODUCTLESS_ROWS:
                if products or layer['merged_guidance'].get('disposition') != 'VISA_EXEMPT':
                    raise PatchRejected('The productless exemption baseline changed shape')
                matches.append({'review_row': row['row_index'], 'product_type': None, 'product_sha256': None})
            else:
                found = [p for p in products if p.get('type') == name]
                if len(found) != 1:
                    raise PatchRejected('Missing or ambiguous exact Japan product identity: ' + name)
                matches.append({'review_row': row['row_index'], 'product_type': name, 'product_sha256': digest(found[0])})
        baseline = {k: deepcopy(layer[k]) for k in BASELINE_KEYS}
        entries.append({'cache_key': key, 'route': deepcopy(route), 'matches': matches,
                        'baseline': baseline, 'baseline_sha256': {k: digest(v) for k,v in baseline.items()}})
    return {'schema_version': 1, 'kind': 'reviewed_japan_exact_layers', 'id': REVIEW_ID,
            'review': deepcopy(review), 'routes': entries,
            'status': 'detached candidate; no registration or activation',
            'captured_route_count': len(entries)}


def _prior(layer, route):
    from app.visa_snapshot import verified_overrides as vo
    table = vo._parse_rows(layer['seed_entries'], {})
    # Current captures have no operator edits. Keep this explicit rather than
    # folding operator decisions into a new AI-authored serving layer.
    if layer['operator_entries']:
        raise PatchRejected('Japan operator edits need separate review and must remain above this candidate')
    return table.get(vo._key(route['passport_nationality'], 'JPN', route['travel_purpose'],
                            route.get('travel_document_type') or 'ordinary_passport')) or {}


def convert_manifest(manifest, current_layers):
    """Return a detached overlay-shaped candidate plus precise blocking reasons."""
    from app.visa_snapshot import verified_overrides as vo
    from app.visa_snapshot.kimi_primary import serve_time_invariants
    if (manifest.get('kind') != 'reviewed_japan_exact_layers'
            or manifest.get('schema_version') != 1 or manifest.get('id') != REVIEW_ID):
        raise PatchRejected('Malformed Japan integration manifest')
    sources = validate_review(manifest['review'])
    # Product selection is derived again from the captured complete route set.
    # An edited match list cannot omit a disputed option or reuse a sibling's
    # proof while leaving the baseline hashes superficially intact.
    try:
        expected = build_manifest(manifest['review'], [
            dict(deepcopy(e['baseline']), cache_key=e['cache_key'])
            for e in manifest['routes']])
        for actual, checked in zip(manifest['routes'], expected['routes'], strict=True):
            if actual != checked:
                raise PatchRejected('Japan match or baseline contract was altered')
    except (KeyError, TypeError, ValueError) as exc:
        raise PatchRejected('Malformed Japan baseline/match contract') from exc
    rows = {r['row_index']: r for r in manifest['review']['rows']}
    current = {r['cache_key']: r for r in current_layers}
    if len(current) != len(current_layers) or set(current) != {e['cache_key'] for e in manifest['routes']}:
        raise PatchRejected('Current Japan route set differs from baseline')
    converted = []; reports = []
    for entry in manifest['routes']:
        layer = current[entry['cache_key']]; route = entry['route']
        for key in BASELINE_KEYS:
            if digest(layer.get(key)) != entry['baseline_sha256'][key] or digest(entry['baseline'][key]) != entry['baseline_sha256'][key]:
                raise PatchRejected('Japan layer changed since reviewed baseline: ' + key)
        if route_identity(layer['route']) != route_identity(route):
            raise PatchRejected('Current Japan route scope changed')
        old = _prior(layer, route)
        fields = deepcopy(old.get('fields') or {})
        proofs = deepcopy(old.get('field_provenance') or {})
        products = deepcopy(layer['merged_guidance'].get('visa_products') or [])
        original_products = deepcopy(products)
        matches = entry['matches']; blockers = []; changed = []; archived_clauses = []
        primary_review = None
        for match in matches:
            row = rows[match['review_row']]; index = row['row_index']
            if route_identity(row['route']) != route_identity(route):
                raise PatchRejected('Reviewed row attached to another Japan route')
            if match['product_type'] is None:
                if index not in PRODUCTLESS_ROWS or products:
                    raise PatchRejected('Invalid productless Japan binding')
                mapping = {'fee': 'government_fee', 'max_stay_days': 'permitted_stay_days'}
                for field, value in row['product_patch'].items():
                    if field in {'validity','entry','notes'}:
                        continue
                    target = mapping.get(field, field)
                    fields[target] = deepcopy(value)
                    proofs[target] = _translate(row['field_provenance'][field], route, target)
                # Exact new qualifications are appended; old claims retain
                # their old proof and are not newly certified as a whole list.
                additions = [row['product_patch']['notes']]
                existing = deepcopy(layer['merged_guidance'].get('exceptions') or [])
                existing = existing if isinstance(existing,list) else [str(existing)]
                if index == 5:
                    if existing.count(ISL_UNSUPPORTED_EXTENSION) != 1:
                        raise PatchRejected('The exact unestablished Iceland extension clause changed')
                    existing.remove(ISL_UNSUPPORTED_EXTENSION)
                    archived_clauses.append({
                        'field': 'exceptions', 'text': ISL_UNSUPPORTED_EXTENSION,
                        'reason': 'The reviewed source does not establish this generic Iceland extension; no blanket denial of exceptional extensions is inferred.',
                        'original_provenance': deepcopy((layer.get('source_provenance') or {}).get('field_provenance', {}).get('exceptions'))})
                fields['exceptions'] = list(dict.fromkeys(existing + additions))
                proof = _translate(row['field_provenance']['notes'], route, 'exceptions')
                proof.update(status='partial', verification_scope='changed_elements_only',
                             verified_elements=additions, retained_unverified_elements=existing)
                proofs['exceptions'] = proof
                primary_review = row; changed.append(index)
                continue
            found = [p for p in products if p.get('type') == match['product_type']]
            if (len(found) != 1 or match['product_type'] != row['match']['projected_visa_type_name']
                    or digest(found[0]) != match['product_sha256']):
                raise PatchRejected('Exact Japan product changed since review')
            product = found[0]
            if index in UNSUPPORTED_ROWS:
                # Preserve every original claim/name for QC but remove any
                # inherited certification of this unestablished named option.
                pproof = deepcopy(product.get('field_provenance') or {})
                pproof['disposition'] = _unknown('The exact named visa eligibility is unestablished: ' + ' '.join(row['known_problems']), route, product)
                product['field_provenance'] = pproof
                blockers.append({'row': index, 'product': product['type'], 'reason':'Named product eligibility is not established; retain held reference'})
                continue
            product.update(deepcopy(row['product_patch']))
            pproof = deepcopy(product.get('field_provenance') or {})
            for field, proof in row['field_provenance'].items():
                pproof[field] = _translate(proof, route, field, product)
            if index in AMBIGUOUS_FORMAT_ROWS:
                # A generic tourist label may combine distinct issuance paths.
                # Field corrections are reviewable, but it earns no new
                # requirement credit until that exact product scope is settled.
                pproof['disposition'] = _unknown('The visa requirement is researched, but this existing named product has no independently established issuance format. Preserve corrected fields as a held reference; do not invent an electronic/paper split.', route, product)
                blockers.append({'row':index,'product':product['type'],'reason':'Exact issuance format unresolved; new product requirement proof withheld'})
            product['field_provenance'] = pproof
            # Retain unrelated nested fields and proof; only this review's
            # own source/date are new, never a copied parent source.
            product['verifier'] = 'ai'
            product['verified_at'] = '2026-09-09'
            product['review_id'] = REVIEW_ID
            if original_products and match['product_type'] == original_products[0]['type']:
                primary_review = row
            changed.append(index)
        if [p['type'] for p in products] != [p['type'] for p in original_products]:
            raise PatchRejected('Japan conversion changed product identity/order')
        if products:
            fields['visa_products'] = products
            # Establish only an independently supported route verdict if the
            # old seed lacks one. Prior conditional/eVisa routes retain theirs.
            if not fields.get('disposition') and primary_review:
                fields['disposition'] = primary_review['product_patch']['disposition']
                proofs['disposition'] = _translate(primary_review['field_provenance']['disposition'], route, 'disposition')
                if primary_review['product_patch'].get('requirement_detail'):
                    fields['requirement_detail'] = primary_review['product_patch']['requirement_detail']
                    proofs['requirement_detail'] = _translate(primary_review['field_provenance']['requirement_detail'], route, 'requirement_detail')
            # Headline facts belong to the exact reviewed primary product.
            # Do not retain a five-day business headline beside the freshly
            # sourced four-day Beijing business products, nor old JPY/VND fees.
            if primary_review and primary_review['row_index'] in {2,11,22}:
                for f, target in {'fee':'government_fee','permitted_stay':'permitted_stay','max_stay_days':'permitted_stay_days','processing_time':'processing_time'}.items():
                    fields[target]=deepcopy(primary_review['product_patch'][f])
                    proofs[target]=_translate(primary_review['field_provenance'][f],route,target)
            anchor = proofs.get('disposition')
            if not anchor:
                raise PatchRejected('Japan candidate has no independently retained route requirement anchor')
            pp = deepcopy(anchor)
            pp.update(status='partial', verification_scope='product_fields_only',
                      note='Only each explicitly reviewed product field has new evidence. Unreviewed products, unknown formats and unsupported named options remain unverified; this is not blanket product evidence.')
            proofs['visa_products'] = pp
        if route['passport_nationality'] == 'VNM':
            blockers.append({'kind': 'application_date_guard', 'rows': [2, 3],
                'reason': 'Do not activate the dated VNM fee fields until the reader enforces their application-acceptance window separately from travel date.',
                'fee_window': deepcopy(VNM_FEE_WINDOW)})
        anchor = proofs.get('disposition')
        if not anchor or not anchor.get('source_url') or not anchor.get('verified_at'):
            raise PatchRejected('Japan route verdict lacks retained or reviewed evidence')
        for key in PROTECTED:
            if fields.get(key) != (old.get('fields') or {}).get(key):
                raise PatchRejected('Japan conversion attempted to change protected state')
        output={'route':{'nationality':route['passport_nationality'],'destination':'JPN',
                         'travel_purpose':route['travel_purpose'],'travel_document_type':'ordinary_passport'},
                'verified_at':anchor['verified_at'],'verified_by':anchor.get('verified_by') or 'Ellis AI Japan source review',
                'verifier':anchor.get('verifier','ai'),'source_url':anchor['source_url'],'note':anchor.get('note',''),
                'fields':fields,'field_provenance':proofs,'review_id':REVIEW_ID,'partial_review':True}
        errors=vo._field_errors(fields)
        if errors:raise PatchRejected('Japan candidate schema: '+'; '.join(errors))
        key=vo._key(route['passport_nationality'],'JPN',route['travel_purpose'],'ordinary_passport')
        parsed=vo._parse_rows([output],{}).get(key)
        if not parsed or set(parsed['fields']) != set(fields):
            raise PatchRejected('Japan loader would discard candidate fields')
        effective,_=vo.merge_verified_fields(layer['raw_guidance'],parsed['fields'],source_url=output['source_url'])
        issues=serve_time_invariants(effective)
        old_issues=serve_time_invariants(layer['merged_guidance'])
        new_issues=sorted(set(issues)-set(old_issues))
        if new_issues: blockers.append({'reason':'New merged conflicts require review','issues':new_issues})
        converted.append(output)
        reports.append({'cache_key':entry['cache_key'],'changed_review_rows':changed,'blocking_review':blockers,
                        'archived_unsupported_clauses': archived_clauses,
                        'baseline_sha256':deepcopy(entry['baseline_sha256']),
                        'retained_integrity_issues':issues,'original_products':[p['type'] for p in original_products],
                        'new_release':False,'new_grounded_check':False,'renew_fresh_until':False})
    return {'schema_version':1,'kind':'reviewed_overlay_conversion','reviewed_at':'2026-09-09',
            'review_id':REVIEW_ID,'status':'DETACHED_REVIEW_ONLY_NOT_REGISTERED',
            'entries':converted,'preflight':reports,
            'contract':'Do not activate this candidate without reviewing its blocking entries and current raw/merged/ordered seed/operator/provenance baselines. No issue, release or TTL changes.'}


def partition_candidate(candidate):
    """Separate usable corrections from held references and an unsupported guard.

    Each route remains whole. No product is dropped to make a route appear
    verified, and partitioning confers no publication or operator approval.
    """
    buckets = {name: {'status': 'DETACHED_REVIEW_ONLY_NOT_REGISTERED',
                     'review_id': REVIEW_ID, 'entries': [], 'preflight': []}
               for name in ('supported_requirement_corrections',
                            'held_reference_corrections', 'requires_application_date_guard')}
    if candidate.get('review_id') != REVIEW_ID or candidate.get('status') != 'DETACHED_REVIEW_ONLY_NOT_REGISTERED':
        raise PatchRejected('Not a detached Japan candidate')
    reports = {p['cache_key']: p for p in candidate['preflight']}
    if len(reports) != len(candidate['entries']):
        raise PatchRejected('Japan partition needs each complete route report')
    for entry in candidate['entries']:
        route = entry['route']
        matched = [p for p in reports.values() if p['cache_key'].split('|')[0] == route['nationality']
                   and p['cache_key'].split('|')[2] == route['destination']
                   and p['cache_key'].split('|')[3] == route['travel_purpose']]
        if len(matched) != 1:
            raise PatchRejected('Japan partition route/report mismatch')
        report = matched[0]
        blocks = report['blocking_review']
        if any(b.get('kind') == 'application_date_guard' for b in blocks):
            name = 'requires_application_date_guard'
        elif blocks:
            if any('row' not in b for b in blocks):
                raise PatchRejected('Unclassified Japan route blocker')
            name = 'held_reference_corrections'
        else:
            name = 'supported_requirement_corrections'
        buckets[name]['entries'].append(deepcopy(entry))
        buckets[name]['preflight'].append(deepcopy(report))
    return buckets


def prepare_supported_overlay(review, current_layers):
    """Prepare only the eleven fully scoped route corrections, still inactive."""
    manifest = build_manifest(review, review['integration_baselines'])
    candidate = convert_manifest(manifest, current_layers)
    supported = partition_candidate(candidate)['supported_requirement_corrections']
    if len(supported['entries']) != 11:
        raise PatchRejected('The reviewed Japan supported route set changed')
    return dict(supported, schema_version=1, kind='reviewed_overlay_conversion',
                reviewed_at='2026-09-09', status='PREPARED_NOT_REGISTERED',
                current_layers_sha256=digest(current_layers),
                excluded_routes={'held_reference_routes':4, 'application_fee_date_guard_routes':1},
                contract='Exactly eleven complete supported-requirement route corrections. No issue/release/TTL changes. Install only after current-layer preflight and review; operators remain the last layer.')


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review', type=Path, required=True)
    parser.add_argument('--current-layers', type=Path, required=True)
    parser.add_argument('--manifest-output', type=Path, required=True)
    parser.add_argument('--candidate-output', type=Path, required=True)
    parser.add_argument('--supported-output', type=Path)
    args = parser.parse_args()
    review = json.loads(args.review.read_text())
    current = json.loads(args.current_layers.read_text())
    # Deployment checks the reviewed capture. Rebuilding the baseline from a
    # fresh input here would silently approve intervening model/operator edits.
    manifest = build_manifest(review, review['integration_baselines'])
    candidate = convert_manifest(manifest, current)
    supported = prepare_supported_overlay(review, current) if args.supported_output else None
    for path, data in ((args.manifest_output, manifest), (args.candidate_output, candidate)):
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    if args.supported_output:
        args.supported_output.write_text(json.dumps(supported, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': candidate['status'], 'routes': len(candidate['entries']),
                      'unresolved_product_rows': sum('row' in b for r in candidate['preflight'] for b in r['blocking_review']),
                      'activation_blockers': sum(len(r['blocking_review']) for r in candidate['preflight'])}))


if __name__ == '__main__':
    main()
