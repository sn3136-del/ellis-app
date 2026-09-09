"""Prepare the bounded Australia21 review as a detached serving overlay.

No database/operator/seed writes, releases, source renewal, or network calls.
This converter deliberately supports only the four reviewed Australian programs.
The source observations are selected browser-read excerpts, not whole-page reads.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import argparse
import json
import re
from pathlib import Path

from scripts.prepare_reviewed_product_patch import PatchRejected, digest, route_identity

BASE = 'https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/'
PATHS = {'eta': 'electronic-travel-authority-601',
         'tourist': 'visitor-600/tourist-stream-overseas',
         'frequent': 'visitor-600/frequent-traveller-stream',
         'ads': 'visitor-600/approved-destination-stream'}
SOURCE_URLS = {'abf_entry': 'https://www.abf.gov.au/entering-and-leaving-australia/crossing-the-border/at-the-border',
               'eta_eligibility': BASE + PATHS['eta'] + '#Eligibility',
               'evisitor_eligibility': BASE + 'evisitor-651#Eligibility',
               **{('eta_about' if k=='eta' else k+'_about'): BASE+v+'#About' for k,v in PATHS.items()},
               **{k+'_how': BASE+PATHS[k]+'#HowTo' for k in ('eta','tourist','ads')}}
ETA_NAMES = {'DEU': 'Germany', 'HKG': 'Hong Kong (SAR of China)',
             'TWN': 'Taiwan (excluding official or diplomatic passports)',
             'JPN': 'Japan', 'KOR': 'South Korea', 'USA': 'United States of America',
             'SGP': 'Singapore', 'CAN': 'Canada', 'MYS': 'Malaysia'}
TOURIST_NATS = {'IDN', 'IND', 'PHL', 'RUS', 'VNM', 'CHN', 'TWN'}
IDN_NAMES = ['Single-entry tourist', 'Multiple-entry tourist – 3 months per visit',
             'Multiple-entry tourist – 6 months per visit', 'Multiple-entry tourist – 12 months per visit']
# The old public projection replaced Unicode dashes with commas. These exact
# observed names are the only cross-version display aliases in this manifest.
DISPLAY_ALIASES = {('CAN', 'ETA (subclass 601), tourist'): 'ETA (subclass 601) – tourist',
                   ('PHL', 'Visitor visa (subclass 600), Tourist stream'): 'Visitor visa (subclass 600) — Tourist stream',
                   ('RUS', 'Visitor visa (subclass 600), Tourist stream'): 'Visitor visa (subclass 600) – Tourist stream'}
PROTECTED = {'held', 'confidence', 'operator_released', 'verification', 'fresh_until',
             'generated_at', 'policy_valid_until', 'source_verification_store_unavailable'}
STAY_TEXT = {
    'eta': 'Up to 3 calendar months after each entry',
    'tourist': 'Generally 3 calendar months; up to 12 months in certain circumstances. The grant letter determines the actual stay.',
    'frequent': 'Up to 3 calendar months per entry, with no more than 12 months in total in any 24-month period',
    'ads': 'As stated in the grant letter, taking the approved tour length into account',
}


def flatten(value):
    if isinstance(value, list):
        return [y for x in value for y in flatten(x)]
    return [value]


def unknown(reason):
    return {'status': 'unknown', 'verifier': 'ai', 'reason': reason}


def evidence(sources, source_id, *keys):
    source = sources[source_id]
    return [{'source_id': source_id, 'source_url': source['url'], 'quote': quote}
            for key in keys for quote in flatten(source['excerpts'][key])]


def reviewed(items, note, day):
    return {'status': 'reviewed', 'verifier': 'ai', 'verified_at': day,
            'evidence': deepcopy(items), 'scope_note': note}


def _subject(route, product=None):
    out = {k: route[k] for k in ('passport_nationality', 'destination_country',
                                'travel_purpose', 'travel_document_type')}
    if product is not None:
        out['product_type'] = product['type']
        out['disposition'] = product.get('disposition')
        out['requirement_detail'] = product.get('requirement_detail')
    return out


def _provenance(proof, route, product=None):
    if proof['status'] == 'unknown':
        return dict(proof, source_url='', verified_at=None, verified_by='',
                    note=proof['reason'], subject=_subject(route, product))
    items = proof['evidence']; first = items[0]
    out = dict(first, status=proof['status'], verifier='ai',
               verified_at=proof['verified_at'], verified_by='Ellis AI Australian product source review',
               note=proof['scope_note'], subject=_subject(route, product))
    same = [x['quote'] for x in items[1:] if x['source_url'] == first['source_url']]
    other = [deepcopy(x) for x in items[1:] if x['source_url'] != first['source_url']]
    if same: out['additional_quotes'] = same
    if other: out['supporting_evidence'] = other
    out['supporting_sources'] = list({x['source_id']: {'id': x['source_id'], 'url': x['source_url']} for x in items}.values())
    for key in ('verification_scope', 'verified_elements', 'retained_unverified_elements'):
        if key in proof: out[key] = deepcopy(proof[key])
    return out


def _program_proof(program, nat, sources, day):
    sid = 'eta_about' if program == 'eta' else program + '_about'
    keys = ('tourism', 'label') if program in {'eta', 'tourist'} else ('scope', 'label')
    items = evidence(sources, sid, *keys) + evidence(sources, 'abf_entry', 'visa')
    if program == 'eta':
        items += evidence(sources, 'eta_eligibility', 'heading', 'eligible_country_items', 'excluded_documents')
    elif program == 'tourist':
        items += evidence(sources, 'tourist_how', 'passport') + evidence(sources, sid, 'outside')
    elif program == 'ads':
        items += evidence(sources, sid, 'china', 'activities')
    return reviewed(items, f'{nat} ordinary passport, tourism, Australian {program} program only. '
                    'A visa is needed for a non-Australian citizen; this product remains subject to its own eligibility and grant conditions. '
                    'Choosing a visa alternative does not mean an ETA-eligible traveller must buy that alternative.', day)


def _normalised_product(row, sources, day, name):
    program = row['program']; nat = row['route']['nationality']
    fields = deepcopy(row['product_patch'])
    # Authorship metadata belongs to exact field proofs, not an independent claim.
    fields.pop('verified_at', None); fields.pop('verifier', None)
    fields['type'] = name
    fields['disposition'] = 'ELECTRONIC_AUTHORIZATION_REQUIRED' if program == 'eta' else 'VISA_REQUIRED'
    proofs = {}
    for field, value in fields.items():
        old = row['field_provenance'].get(field)
        if value is None:
            proofs[field] = unknown('No universal value is established for this product; null clears an unsupported legacy value. Calendar stays and discretionary grants remain in their exact text, not invented day counts.')
        elif old and old.get('source_id'):
            items = evidence(sources, old['source_id'], *old['excerpt_keys'])
            proofs[field] = reviewed(items, old.get('note') or f'Only this {program} product field is reviewed.', day)
        elif field == 'source_quote':
            sid = 'eta_about' if program == 'eta' else program + '_about'
            proofs[field] = reviewed(evidence(sources, sid, 'tourism' if program in {'eta', 'tourist'} else 'scope'), 'Literal product source excerpt.', day)
        elif field != 'disposition':
            raise PatchRejected('Unaccounted product field: ' + field)
    proofs['disposition'] = _program_proof(program, nat, sources, day)
    # The draft is independently reviewed here; remove claims absent from its captures.
    if program == 'eta':
        fields['notes'] = ('Apply outside Australia and remain outside when granted. Multiple entry; each stay is up to 3 calendar months. '
            'Travel validity is at most 12 months and ends earlier when the passport expires; a new passport requires a new ETA. '
            'The ETA does not permit paid work. Each family member needs a separate application. '
            'If you have a criminal conviction, Home Affairs says to apply for a Visitor visa (subclass 600) and provide conviction evidence.')
        proofs['notes']['evidence'] += evidence(sources, 'eta_eligibility', 'criminal')
        proofs['application_channel_detail']['evidence'] += evidence(sources, 'eta_about', 'outside')
        proofs['required_documents']['evidence'] += evidence(sources, 'eta_about', 'fee')
        proofs['requirement_detail'] = deepcopy(proofs['disposition'])
    elif program == 'tourist':
        fields['fee']['note'] = 'Base charge from AUD250 per applicant. Health checks, police certificates and biometrics can cost extra; nationality-specific concessions have separate conditions.'
        fields['notes'] = ('Single or multiple entries, validity and actual stay are determined in the grant letter. '
            'Each family member needs a separate application. The visa is digitally linked to the passport; no visa label is issued.')
        fields['application_channel_detail'] = ('Apply online through ImmiAccount while outside Australia. '
            'Attach supporting documents and pay the application fee; remain outside Australia when the decision is made.')
        proofs['application_channel_detail']['evidence'] += evidence(sources, 'tourist_about', 'outside')
        fields['required_documents'] = fields['required_documents'][:3]
        proofs['required_documents']['scope_note'] = 'Published general application documents and examples, with their if-held/if-applicable/if-requested conditions. This is not a complete personal checklist; individual age, health and other requirements remain unreviewed.'
        fields['official_portal_url'] = sources['tourist_how']['url']
        proofs['official_portal_url'] = reviewed(evidence(sources, 'tourist_how', 'apply'), 'Official application instructions linking to ImmiAccount; no claim that the login endpoint itself was reviewed.', day)
    elif program == 'frequent':
        fields['fee']['note'] = 'Per applicant. The stated Timor-Leste concession does not apply to this Chinese passport product.'
    else:
        fields['fee']['note'] = 'Visa charge. Health checks, police certificates and biometrics can cost extra.'
        fields['application_channel'] = 'authorised_agent'
        proofs['application_channel'] = reviewed(evidence(sources, 'ads_how', 'agent'),
            'The existing authorised_agent enum exactly represents compulsory ADS-registered-agent filing; digital issuance does not imply self-service filing.', day)
    # Do not borrow route procedure/border checklists for an alternative product.
    fields['entry_requirements'] = None
    proofs['entry_requirements'] = unknown('This product review covers application facts. It does not establish a separate complete border-admission checklist.')
    fields['consular_jurisdiction'] = None
    proofs['consular_jurisdiction'] = unknown('No applicant-specific consular district was reviewed for this product; do not borrow a sibling filing method as jurisdiction evidence.')
    return fields, proofs


def _validate_proofs(fields, proofs, sources, route, program=None):
    from app.visa_snapshot.authority import hostname, is_government_host
    from app.visa_snapshot.evidence_validator import jurisdiction_matches
    if set(fields) != set(proofs):
        raise PatchRejected('Every changed field requires its own proof or explicit unknown status')
    for field, value in fields.items():
        proof = proofs[field]
        if proof.get('verifier') != 'ai': raise PatchRejected('Review authorship must be AI')
        if proof.get('status') == 'unknown':
            if value is not None or not proof.get('reason'): raise PatchRejected('Unknown fields must be null with a reason')
            continue
        if proof.get('status') not in {'reviewed', 'partial'} or not proof.get('scope_note'):
            raise PatchRejected('Missing scoped review status')
        try: day = date.fromisoformat(proof.get('verified_at', '')[:10])
        except ValueError as exc: raise PatchRejected('Invalid review date') from exc
        if day > date.today(): raise PatchRejected('Future review date')
        items = proof.get('evidence')
        if not isinstance(items, list) or not items: raise PatchRejected('Missing source evidence')
        for item in items:
            source = sources.get(item.get('source_id'))
            if (not source or source['url'] != item.get('source_url') or source.get('checked_at') != day.isoformat()
                    or source['url'] != SOURCE_URLS.get(item.get('source_id'))
                    or not is_government_host(hostname(source['url'])) or not jurisdiction_matches(source['url'], 'AUS')):
                raise PatchRejected('Unread, changed, foreign or undated evidence source')
            passages = [q for v in source['excerpts'].values() for q in flatten(v)]
            if item.get('quote') not in passages:
                raise PatchRejected('Quote is absent from its own observed source excerpts')
        if field in {'source_url', 'official_portal_url'} and value not in {x['source_url'] for x in items}:
            raise PatchRejected('Published link was not independently read')
        if field == 'source_quote' and value not in {x['quote'] for x in items}:
            raise PatchRejected('Published quote is not literal evidence')
        if program and field in {'disposition', 'requirement_detail', 'fee', 'government_fee'}:
            _validate_program_value(field, value, items, sources, route, program)
        if program and field in {'permitted_stay', 'validity', 'validity_duration', 'validity_unit', 'entry', 'application_channel'}:
            _validate_measure_and_method(field, value, items, sources, program)
        if field in {'max_stay_days', 'permitted_stay_days'} and value is not None:
            raise PatchRejected('This reviewed batch has no exact calendar-month-to-day conversion')


def _validate_measure_and_method(field, value, items, sources, program):
    """Known program-specific measures cannot be changed by reusing a true quote."""
    sid='eta_about' if program=='eta' else program+'_about'
    if field=='permitted_stay':
        expected=STAY_TEXT[program]; required=[(sid,'stay')]
        if program in {'tourist','frequent'}:required.append((sid,'grant'))
    elif field in {'validity','validity_duration','validity_unit'}:
        values={'eta':{'validity':'Up to 12 months, or passport validity if shorter','validity_duration':12,'validity_unit':'Month'},
                'frequent':{'validity':'Up to 10 years, as granted'}}
        expected=values.get(program,{}).get(field)
        required=[(sid,'validity')]
    elif field=='entry':
        expected='Multiple' if program in {'eta','frequent'} else None;required=[(sid,'entries')]
    else:
        expected={'eta':'online_portal','tourist':'online_portal','ads':'authorised_agent','frequent':None}[program]
        required=[('eta_how','app')] if program=='eta' else [('tourist_how','apply')] if program=='tourist' else [('ads_how','agent')]
    if value!=expected:
        raise PatchRejected('Measure or filing method differs from its scoped reviewed rule: '+field)
    for source_id,key in required:
        for quote in flatten(sources[source_id]['excerpts'][key]):
            if not any(x['source_id']==source_id and x['quote']==quote for x in items):
                raise PatchRejected('Measure or filing method lacks its own program-specific clause: '+field)


def _validate_program_value(field, value, items, sources, route, program):
    nat, dest, purpose, doc = route_identity(route)
    if dest != 'AUS' or purpose != 'tourism' or doc != 'ordinary_passport' or program not in PATHS:
        raise PatchRejected('Australian program scope does not match the route')
    sid = 'eta_about' if program == 'eta' else program + '_about'
    def has(source_id, key):
        src = sources[source_id]
        expected_url = BASE + PATHS[program] + '#About' if source_id == sid else src['url']
        return (src['url'] == expected_url and all(any(x['source_id'] == source_id and x['quote'] == q for x in items)
                    for q in flatten(src['excerpts'][key])))
    if field in {'disposition', 'requirement_detail'}:
        expected = ('ELECTRONIC_AUTHORIZATION_REQUIRED' if program == 'eta' else 'VISA_REQUIRED') if field == 'disposition' else ('eta_electronic_authorization' if program == 'eta' else 'evisa')
        if value != expected: raise PatchRejected('Product permission family changed without proof')
        if not has(sid, 'label'): raise PatchRejected('Digital issuance requires its own product evidence')
        if field == 'requirement_detail' and program != 'eta': return
        if not has('abf_entry', 'visa'): raise PatchRejected('Eligibility alone does not establish a visa requirement')
        if program == 'eta':
            if nat not in ETA_NAMES or sources['eta_eligibility']['url'] != BASE + PATHS['eta'] + '#Eligibility':
                raise PatchRejected('Wrong ETA nationality or source program')
            if not has('eta_eligibility', 'heading') or not has('eta_eligibility', 'eligible_country_items') or not has('eta_eligibility', 'excluded_documents'):
                raise PatchRejected('Incomplete ordinary-passport ETA list evidence')
            if ETA_NAMES[nat] not in sources['eta_eligibility']['excerpts']['eligible_country_items'] or not has(sid, 'tourism'):
                raise PatchRejected('ETA membership or tourism scope absent')
        elif program == 'tourist':
            if nat not in TOURIST_NATS or not has(sid, 'tourism') or not has('tourist_how', 'passport') or not has(sid, 'outside'):
                raise PatchRejected('Tourist600 applicant/program scope absent')
        elif nat != 'CHN' or not has(sid, 'scope') or (program == 'ads' and (not has(sid, 'china') or not has(sid, 'activities'))):
            raise PatchRejected('China conditional product scope absent')
    else:
        amount = {'eta': 20, 'tourist': 250, 'frequent': 1845, 'ads': 250}[program]
        if not isinstance(value, dict) or isinstance(value.get('amount'), bool) or value.get('amount') != amount or value.get('currency') != 'AUD' or not has(sid, 'fee'):
            raise PatchRejected('Fee amount, currency or product-specific quote does not match')
        if program == 'tourist' and value.get('qualifier') != 'from':
            raise PatchRejected('Tourist600 starting-price qualifier is required')
        if program == 'eta' and ('service' not in str(value.get('note', '')).lower() or 'zero' not in str(value.get('note', '')).lower()):
            raise PatchRejected('ETA app charge must be distinguished from the zero VAC')


def build_manifest(draft, layers):
    """Compile reviewed facts and exact current layers into an auditable manifest."""
    if draft.get('audit_id') != 'australia21-2026-09-09' or len(draft.get('rows', [])) != 21:
        raise PatchRejected('Only the independently reviewed Australia21 batch is supported')
    sources = {s['id']: deepcopy(s) for s in draft['sources']}; day = draft['reviewed_at']
    rows_by_nat = {}
    for row in draft['rows']: rows_by_nat.setdefault(row['route']['nationality'], []).append(row)
    entries = []
    for layer in layers:
        route = dict(layer['route'], travel_document_type=layer['route'].get('travel_document_type') or 'ordinary_passport')
        nat = route['passport_nationality']; g = layer['merged_guidance']; products = g.get('visa_products') or []
        batch = rows_by_nat[nat]; patches = []; baseline = deepcopy(layer)
        for row in batch:
            if nat == 'IDN' and row['input_row_index'] != 0: continue
            original = row['selector']['visa_type_name']
            original = DISPLAY_ALIASES.get((nat, original), original)
            # Projection tidies whitespace and Unicode dashes; record the real
            # source identities and content hashes after this one-time binding.
            from app.visa_snapshot.tstation import _clean_text
            norm = lambda s: _clean_text(s or '').replace('—', '-').replace('–', '-')
            matches = [p for p in products if norm(p.get('type')) == norm(original)]
            if nat == 'IDN':
                matches = [p for p in products if p.get('type') in IDN_NAMES]
                if [p['type'] for p in matches] != IDN_NAMES: raise PatchRejected('Exact four IDN grant variants are absent')
                name = 'Visitor visa (subclass 600) — Tourist stream (apply outside Australia)'
            elif not products:
                if len(batch) != 1 or norm(g.get('visa_category')) != norm(original):
                    raise PatchRejected('Productless primary identity differs from review')
                name = original
            else:
                if len(matches) != 1: raise PatchRejected('Missing or ambiguous reviewed product: ' + original)
                name = matches[0]['type']
            fields, proofs = _normalised_product(row, sources, day, name)
            patches.append({'program': row['program'], 'input_row_indices': [0, 1, 2, 3] if nat == 'IDN' else [row['input_row_index']],
                'matches': [{'type': p['type'], 'sha256': digest(p)} for p in matches],
                'create_from_productless_primary': not products, 'fields': fields, 'field_provenance': proofs})
        route_fields = {}; route_proofs = {}
        primary = patches[0]
        # DEU's independently valid eVisitor is outside this ETA correction.
        if nat != 'DEU':
            mapping = {'type': 'visa_category', 'max_stay_days': 'permitted_stay_days', 'fee': 'government_fee'}
            take = ('type', 'disposition', 'requirement_detail', 'permitted_stay', 'max_stay_days', 'fee',
                    'application_channel', 'application_channel_detail', 'official_portal_url', 'required_documents',
                    'processing_time', 'biometrics_required', 'appointment_required', 'interview_required', 'source_url')
            for field in take:
                key = mapping.get(field, field); route_fields[key] = deepcopy(primary['fields'][field]); route_proofs[key] = deepcopy(primary['field_provenance'][field])
            # Remove only exact false legacy nonpublication flags for reviewed
            # validity. Discretionary individual grants are not nonpublication.
            flags = g.get('unpublished_fields')
            if isinstance(flags, list) and {'validity_duration', 'validity_unit'} & set(flags):
                route_fields['unpublished_fields'] = [x for x in flags if x not in {'validity_duration', 'validity_unit'}]
                proof = deepcopy(primary['field_provenance'].get('validity'))
                if proof['status'] == 'unknown':
                    sid = primary['program'] + '_about'
                    proof = reviewed(evidence(sources, sid, 'grant' if primary['program'] == 'tourist' else 'stay'), 'The authority publishes a discretionary grant rule; remove only false validity nonpublication flags.', day)
                proof.update(status='partial', verification_scope='changed_elements_only', verified_elements=[], retained_unverified_elements=route_fields['unpublished_fields'])
                route_proofs['unpublished_fields'] = proof
            exceptions = deepcopy(g.get('exceptions') or [])
            if isinstance(exceptions, list):
                corrections = {
                    'CHN': ('Additional applicants can be included in the same application with extra fees', 'Each family member, including those listed on a passport, requires a separate application.'),
                    'IND': ('Family members can be included as secondary applicants in the same ImmiAccount application', 'Each family member, including those listed on a passport, requires a separate application.'),
                    'VNM': ('Family members may be included in the same application', 'Each family member, including those listed on a passport, requires a separate application.'),
                    'JPN': ('Travellers unable to use the app/online service may arrange an ETA through a participating airline or registered travel agent', 'An agent can apply using the Australian ETA app, but the applicant must be physically present with their passport for scanning and a photo.'),
                    'SGP': ('Singapore citizens may also apply via the AustralianETA mobile app', 'Apply for an ETA through the Australian ETA app.'),
                    'CAN': ('An ETA is an electronic travel authority, not a visa', 'An ETA is an Australian visa digitally linked to the passport.'),
                    'USA': ('The ETA is an electronic travel authority, not a visa; final entry is decided by Australian Border Force officers', 'An ETA is an Australian visa digitally linked to the passport. Final entry is decided by Australian Border Force officers.'),
                }
                if nat in corrections and corrections[nat][0] in exceptions:
                    old, new = corrections[nat]; changed = [new if x == old else x for x in exceptions]
                    route_fields['exceptions'] = changed
                    sid, keys = ('tourist_about', ('family',)) if nat in {'CHN','IND','VNM'} else ('eta_how', ('app','presence')) if nat in {'JPN','SGP'} else ('eta_about', ('label','entries'))
                    p = reviewed(evidence(sources, sid, *keys), 'Only the exact replaced exception is reviewed; all other claims retain their prior state.', day)
                    p.update(status='partial', verification_scope='changed_elements_only', verified_elements=[new], retained_unverified_elements=[x for x in exceptions if x != old])
                    if nat == 'USA':
                        p['verified_elements'] = ['An ETA is an Australian visa digitally linked to the passport.']
                        p['retained_unverified_elements'].append('Final entry is decided by Australian Border Force officers.')
                    route_proofs['exceptions'] = p
        entries.append({'cache_key': layer['cache_key'], 'route': route, 'reviewed_at': day,
            'baseline': baseline, 'baseline_hashes': {k: digest(layer[k]) for k in ('raw_guidance','merged_guidance','seed_entries','operator_entries')},
            'fields': route_fields, 'field_provenance': route_proofs, 'product_patches': patches,
            'unresolved': sorted(set(x for r in batch for x in r['unresolved']))})
    if len(entries) != 15: raise PatchRejected('All 15 reviewed routes need current layer baselines')
    if {e['route']['passport_nationality'] for e in entries} != set(rows_by_nat):
        raise PatchRejected('A reviewed route was duplicated or omitted')
    return {'schema_version': 1, 'kind': 'reviewed_australia_product_patch', 'reviewed_at': day,
            'audit_id': draft['audit_id'], 'status': 'not_installed', 'sources': list(sources.values()), 'routes': entries,
            'scope': {'input_product_rows': 21, 'reviewed_replacement_products': 18, 'routes': 15, 'fully_certified_rows': 0},
            'source_scope': 'Selected exact browser-observed excerpts; no claim of full-page or automated fresh-source verification.'}


def convert_entry(entry, sources, current):
    from app.visa_snapshot import verified_overrides as vo
    from app.visa_snapshot.kimi_primary import serve_time_invariants
    route = entry['route']; nat, dest, purpose, doc = route_identity(route)
    if set(entry['fields']) & PROTECTED or set(entry['fields']) - (set(vo.OVERRIDABLE)-{'visa_products','confidence'}):
        raise PatchRejected('Route patch exceeds field-correction authority')
    if route_identity(current['route']) != route_identity(route): raise PatchRejected('Route identity changed')
    if entry.get('cache_key')!=current.get('cache_key'): raise PatchRejected('Current cache identity changed')
    hashes=entry.get('baseline_hashes')
    required={'raw_guidance','merged_guidance','seed_entries','operator_entries'}
    if (not isinstance(hashes,dict) or set(hashes)!=required or any(
            not isinstance(v,str) or re.fullmatch(r'[0-9a-f]{64}',v) is None for v in hashes.values())):
        raise PatchRejected('Exactly four valid baseline layer hashes are required')
    for field, expected in hashes.items():
        if digest(current[field]) != expected: raise PatchRejected('Current layer changed: ' + field)
    g = current['merged_guidance']; candidate = deepcopy(g)
    _validate_proofs(entry['fields'], entry['field_provenance'], sources, route,
                     entry['product_patches'][0]['program'] if nat != 'DEU' else None)
    candidate.update(deepcopy(entry['fields']))
    original = deepcopy(g.get('visa_products') or []); products = deepcopy(original); touched = set()
    archives = []
    for patch in entry['product_patches']:
        values = patch['fields']; proof = patch['field_provenance']; program = patch['program']
        if set(values) & PROTECTED: raise PatchRejected('A product correction cannot release or renew a route')
        _validate_proofs(values, proof, sources, route, program)
        matches = patch['matches']; positions = []
        for match in matches:
            found = [i for i,p in enumerate(original) if p.get('type') == match['type'] and digest(p) == match['sha256']]
            if len(found) != 1 or found[0] in touched: raise PatchRejected('Product identity/content is missing, changed or repeated')
            positions += found; touched.add(found[0])
        if patch['create_from_productless_primary']:
            if original or matches or len(entry['product_patches']) != 1: raise PatchRejected('Invalid productless-primary creation')
            if values['type'] != g.get('visa_category'): raise PatchRejected('Productless primary may not be renamed')
            old = {}; pos = 0
        elif len(positions) == 4:
            if nat != 'IDN' or program != 'tourist' or [original[i]['type'] for i in positions] != IDN_NAMES:
                raise PatchRejected('Only the four exact reviewed IDN grant variants may consolidate')
            if values['type'] != 'Visitor visa (subclass 600) — Tourist stream (apply outside Australia)':
                raise PatchRejected('IDN consolidation identity is not the reviewed Tourist600 stream')
            archives = [deepcopy(original[i]) for i in positions]; old = deepcopy(original[positions[0]]); pos = positions[0]
        elif len(positions) == 1:
            pos = positions[0]; old = deepcopy(original[pos])
            if values['type'] != old['type']: raise PatchRejected('Unreviewed product rename')
        else: raise PatchRejected('Unexpected replacement cardinality')
        old.update(deepcopy(values)); old['field_provenance'] = {**deepcopy(old.get('field_provenance') or {}),
            **{k:_provenance(p, route, old) for k,p in proof.items()}}
        decision = old['field_provenance']['disposition']
        old.update(source_url=decision['source_url'], source_quote=decision['quote'],
                   verified_at=decision['verified_at'], verifier='ai')
        old['corroborating_sources'] = [{'url': x['source_url'], 'quote': x['quote'],
            'checked_at': decision['verified_at'], 'authority': 'Australian official source; AI product review'}
            for x in proof['disposition']['evidence'][1:]]
        if not original: products = [old]
        else: products[pos] = old
        if len(positions) == 4:
            products = [p for i,p in enumerate(products) if i not in positions[1:]]
    candidate['visa_products'] = products
    key = vo._key(nat,dest,purpose,doc)
    prior = vo._parse_rows(current['seed_entries'], {}).get(key,{})
    fields = deepcopy(prior.get('fields') or {}); provenance = deepcopy(prior.get('field_provenance') or {})
    fields.update(deepcopy(entry['fields'])); fields['visa_products'] = products
    provenance.update({k:_provenance(p,route) for k,p in entry['field_provenance'].items()})
    proof = deepcopy(products[next(i for i,p in enumerate(products) if p.get('field_provenance',{}).get('disposition',{}).get('status')=='reviewed')]['field_provenance']['disposition'])
    proof.update(status='partial', verification_scope='product_fields_only', note='Only each explicitly reviewed product field has new evidence; untouched products and route claims retain their previous state.')
    proof.pop('subject',None); provenance['visa_products'] = proof
    verdict = provenance.get('disposition')
    if not verdict: raise PatchRejected('No scoped route anchor exists; do not copy a model decision as verified')
    result = {'route': {'nationality':nat,'destination':dest,'travel_purpose':purpose,'travel_document_type':doc},
        'verified_at':verdict['verified_at'],'verified_by':verdict.get('verified_by','Ellis AI Australian product source review'),
        'verifier':verdict.get('verifier','ai'),'source_url':verdict['source_url'],'note':verdict.get('note',''),
        'fields':fields,'field_provenance':provenance,'review_id':'australia21-2026-09-09','partial_review':True}
    errors = vo._field_errors(fields)
    if errors: raise PatchRejected('Overlay schema: '+'; '.join(errors))
    parsed = vo._parse_rows([result],{}).get(key)
    if parsed is None or set(parsed['fields']) != set(fields): raise PatchRejected('Serving loader would silently omit overlay fields')
    effective_fields=deepcopy(parsed['fields'])
    if current['operator_entries']:
        op=vo._parse_rows(current['operator_entries'],{},inherited={key:parsed}).get(key,{})
        for field,value in op.get('fields',{}).items():
            if field in fields and field in set(entry['fields'])|{'visa_products'} and value != fields[field]:
                raise PatchRejected('Current operator correction conflicts; no operator record may be overwritten')
        effective_fields.update(deepcopy(op.get('fields') or {}))
    effective,_=vo.merge_verified_fields(current['raw_guidance'],effective_fields,source_url=result['source_url'])
    for field in entry['fields']:
        if effective.get(field)!=entry['fields'][field]: raise PatchRejected('Reader changes a reviewed route field: '+field)
    if effective.get('visa_products') != products: raise PatchRejected('Reader changes reviewed products')
    before=set(serve_time_invariants(g)); after=set(serve_time_invariants(effective))
    if after-before: raise PatchRejected('New merged-answer conflict: '+'; '.join(sorted(after-before)))
    return {'entry':result,'effective_guidance':effective,'preflight':{'cache_key':entry['cache_key'],
        'baseline_hashes':deepcopy(entry['baseline_hashes']), 'retained_integrity_issues':sorted(after),
        'changed_route_fields': sorted(entry['fields']),
        'changed_product_fields': [{'product_type':p['fields']['type'],'program':p['program'],'fields':sorted(p['fields'])} for p in entry['product_patches']],
        'retained_seed_fields':sorted(set(prior.get('fields') or {})-set(entry['fields'])-{'visa_products'}),
        'original_product_count':len(original), 'final_product_count':len(products),
        'new_grounded_check':False,'new_release':False,'renew_fresh_until':False,
        'unresolved':deepcopy(entry['unresolved']), 'consolidated_original_products':archives}}


def convert_manifest(manifest, current_layers):
    sources={s['id']:s for s in manifest['sources']}; current={x['cache_key']:x for x in current_layers}
    if manifest.get('kind')!='reviewed_australia_product_patch': raise PatchRejected('Unexpected manifest kind')
    if len(sources)!=len(manifest['sources']) or len(current)!=len(current_layers):
        raise PatchRejected('Duplicate source or current route identity')
    keys=[e['cache_key'] for e in manifest['routes']]
    if len(keys)!=len(set(keys)) or len(keys)!=15:
        raise PatchRejected('Exactly 15 distinct reviewed routes are required')
    results=[convert_entry(e,sources,current[e['cache_key']]) for e in manifest['routes']]
    return {'schema_version':1,'kind':'reviewed_overlay_conversion','reviewed_at':manifest['reviewed_at'],
        'status':'not_installed','entries':[r['entry'] for r in results],
        'preflight':[r['preflight'] for r in results],'blocked':[],
        'contract':'Revalidate current raw, merged, seed and operator hashes before installing this separate overlay. Preserve every cache row, issue, hold, release and freshness timestamp. This partial AI review is not full-route certification.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--draft',type=Path,required=True); parser.add_argument('--layers',type=Path,required=True)
    parser.add_argument('--manifest-output',type=Path,required=True); parser.add_argument('--overlay-output',type=Path,required=True)
    args=parser.parse_args()
    manifest=build_manifest(json.loads(args.draft.read_text()),json.loads(args.layers.read_text()))
    overlay=convert_manifest(manifest,json.loads(args.layers.read_text()))
    args.manifest_output.write_text(json.dumps(manifest,ensure_ascii=False,indent=1)+'\n')
    args.overlay_output.write_text(json.dumps(overlay,ensure_ascii=False,indent=1)+'\n')
    print(json.dumps({'routes':len(overlay['entries']),'status':'not_installed','database_writes':0}))
