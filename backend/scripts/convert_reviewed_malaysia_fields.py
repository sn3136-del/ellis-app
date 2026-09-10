"""Detached correction of six IDN→MYS tourist entry fields and their proofs.

The converter preserves the original verdict source/date, every other field,
operator and issue state. It never writes live stores or creates freshness.
The first existing purpose restriction is retained explicitly unreviewed;
only the replacement extension sentence gains a new element-level review.
"""
from copy import deepcopy
from datetime import date

from scripts.convert_reviewed_product_validity import BASELINE_KEYS, _current_map, _sources
from scripts.convert_reviewed_product_patch import field_provenance, _subject
from scripts.prepare_reviewed_product_patch import PatchRejected, digest

CASE_KEY = 'IDN|IDN|MYS|tourism|default|unknown|v6'
URLS = {
    'entry_tourists': 'https://www.motac.gov.my/en/pengumuman/entry-requirements-for-foreign-tourists-for-immigration-inspection-at-international-entry-points/',
    'entry_pdf': 'https://www.imi.gov.my/wp-content/uploads/2023/02/2023-CR-Portal-JIM-24022023-BI.pdf',
    'extension': 'https://www.imi.gov.my/index.php/en/main-services/pass/visitor-pass/social-visit-pass/short-term-social-visit-pass/',
    'insurance': 'https://www.tourism.gov.my/media/view/malaysia-relaxes-covid-19-testing-rules-travel-insurance-for-inbound-travellers',
}
PASSPORT = 'Documents must be valid for six months or more from the date of entry into Malaysia.'
FUNDS = 'Every visitor is also required to prove the financial ability to declare himself in Malaysia along with a fixed return ticket to return to his home country or to another country.'
TICKET = 'Possess a valid return ticket to their home country or an onward ticket to a next destination country.'
LODGING = 'Possess proof of accommodation booking or confirmed lodging arrangements for the duration of the stay in Malaysia.'
EXTENSION = 'An Extension may be given on Special consideration e.g. due to illness, accident, war in home country etc. The visitor must furnish evidence, and also present a confirmed return ticket to their home country or third country.'
INSURANCE = 'Travel insurance will also not be a prerequisite for foreigners entering the country.'
RETAINED = 'Visa-free entry is for tourism/social visits only; employment or paid work is prohibited.'
EXTENSION_VALUE = ('Extensions of the Short Term Social Visit Pass may be considered for special circumstances '
                   'such as illness, accident or war in the home country. Supporting evidence and a confirmed '
                   'ticket to the home country or a third country are required.')
VALUES = {
    'required_documents': ['Passport valid for six months or more from entry into Malaysia',
                           'Valid return ticket or onward ticket to the next destination country',
                           'Proof of sufficient funds for the stay',
                           'Proof of accommodation booking or confirmed lodging for the full stay'],
    'onward_travel_evidence': 'A valid return ticket to the home country or an onward ticket to the next destination country is required.',
    'accommodation_evidence': 'Proof of accommodation booking or confirmed lodging arrangements for the full stay is required.',
    'financial_evidence': 'Proof of sufficient funds for the stay is required.',
    'insurance_required': False,
    'exceptions': [RETAINED, EXTENSION_VALUE],
}
FIELDS = frozenset(VALUES)
EVIDENCE = {
    'required_documents': [('entry_pdf', PASSPORT), ('entry_pdf', FUNDS), ('entry_tourists', TICKET), ('entry_tourists', LODGING)],
    'onward_travel_evidence': [('entry_tourists', TICKET)],
    'accommodation_evidence': [('entry_tourists', LODGING)],
    'financial_evidence': [('entry_pdf', FUNDS)],
    'insurance_required': [('insurance', INSURANCE)],
    'exceptions': [('extension', EXTENSION)],
}


def _validate(spec, layer):
    from scripts.convert_reviewed_general_batch import _check_proof, quote_literal
    if (set(spec) != {'schema_version', 'kind', 'id', 'cache_key', 'route', 'baseline_sha256', 'sources', 'changes'}
            or spec['schema_version'] != 1 or spec['kind'] != 'reviewed_malaysia_entry_fields'
            or not isinstance(spec['id'], str) or not spec['id'].strip()):
        raise PatchRejected('Invalid Malaysia entry-field specification')
    route = spec['route']
    expected = {'passport_nationality': 'IDN', 'lawful_country_of_residence': 'IDN',
                'destination_country': 'MYS', 'visa_category': 'tourist_visa', 'travel_purpose': 'tourism',
                'arrival_date': None, 'consular_jurisdiction': None,
                'travel_document_type': 'ordinary_passport', 'transit_countries': None}
    if spec['cache_key'] != CASE_KEY or route != expected or route != layer['route']:
        raise PatchRejected('Review covers only the exact current ordinary Indonesian tourist route')
    if layer['operator_entries']:
        raise PatchRejected('Operator-owned routes need a separate integration contract')
    if (layer['merged_guidance'].get('permitted_stay') != 'Less than 1 month'
            or layer['merged_guidance'].get('permitted_stay_days') is not None
            or layer['merged_guidance'].get('disposition') != 'VISA_EXEMPT'
            or layer['merged_guidance'].get('requirement_detail') != 'unconditional_visa_free'
            or layer['raw_guidance'].get('visa_products') != []
            or layer['merged_guidance'].get('visa_products') not in (None, [])):
        raise PatchRejected('Existing visa verdict, stay qualifier or product scope changed')
    if layer['merged_guidance'].get('exceptions', [])[:1] != [RETAINED]:
        raise PatchRejected('Retained purpose condition is no longer the reviewed baseline')
    sources = _sources(spec)
    if set(sources) != set(URLS) or any(sources[s]['url'] != u for s, u in URLS.items()):
        raise PatchRejected('Exact Malaysian government source catalog is required')
    context = {'entry_tourists': 'ENTRY REQUIREMENTS FOR FOREIGN TOURISTS',
               'entry_pdf': 'Visitors must meet the following requirements before entering Malaysia',
               'extension': 'Extension of Social Visit Pass',
               'insurance': 'Starting 1 May 2022'}
    for sid, anchor in context.items():
        if not quote_literal(anchor, sources[sid]['text']):
            raise PatchRejected('Missing governing scope in source: ' + sid)
    changes = spec['changes']
    if (not isinstance(changes, list) or len(changes) != len(FIELDS)
            or {c.get('field') for c in changes} != FIELDS):
        raise PatchRejected('Exactly six reviewed fields are required')
    for change in changes:
        if set(change) != {'field', 'old_raw', 'old_merged', 'new', 'proof'}:
            raise PatchRejected('Unexpected field mutation')
        field = change['field']; proof = change['proof']
        if (change['old_raw'] != layer['raw_guidance'].get(field)
                or change['old_merged'] != layer['merged_guidance'].get(field)
                or change['new'] != VALUES[field]
                or field == 'insurance_required' and type(change['new']) is not bool):
            raise PatchRejected('Field differs from the reviewed correction: ' + field)
        keys = {'status', 'verifier', 'verified_at', 'scope_note', 'evidence', 'subject'}
        if field == 'exceptions': keys |= {'verified_elements', 'retained_unverified_elements'}
        if (not isinstance(proof, dict) or set(proof) != keys or proof['status'] != 'reviewed'
                or proof['verifier'] != 'ai' or proof['subject'] != _subject(route)):
            raise PatchRejected('Proof must retain exact AI field scope')
        expected_evidence = [{'source_id': sid, 'source_url': URLS[sid], 'quote': quote} for sid, quote in EVIDENCE[field]]
        if proof['evidence'] != expected_evidence:
            raise PatchRejected('Evidence omits or alters the approved field-specific rule')
        if field == 'exceptions' and (proof['verified_elements'] != [EXTENSION_VALUE]
                or proof['retained_unverified_elements'] != [RETAINED]):
            raise PatchRejected('The untouched condition cannot receive a new review')
        try:
            day = date.fromisoformat(proof['verified_at'])
        except (ValueError, TypeError) as exc:
            raise PatchRejected('Invalid field review date') from exc
        if day > date.today() or any(sources[e['source_id']].get('checked_at') != day.isoformat() for e in expected_evidence):
            raise PatchRejected('Field date must match its actual source capture')
        _check_proof(deepcopy(proof), sources, route, field, change['new'])
    return sources


def build_manifest(spec, layers):
    layer = _current_map(layers, {spec.get('cache_key')})[spec['cache_key']]
    if any(k not in layer for k in BASELINE_KEYS): raise PatchRejected('Incomplete current layer capture')
    baseline = {k: deepcopy(layer[k]) for k in BASELINE_KEYS}
    hashes = {k: digest(v) for k, v in baseline.items()}
    if spec['baseline_sha256'] != hashes: raise PatchRejected('Current baseline differs from reviewed layers')
    _validate(spec, layer)
    return {'schema_version': 1, 'kind': 'malaysia_entry_fields_exact_layers', 'cache_key': CASE_KEY,
            'specification': deepcopy(spec), 'baseline': baseline, 'baseline_sha256': hashes}


def convert(manifest, current_layers):
    from app.visa_snapshot import verified_overrides as vo, tstation
    from app.visa_snapshot.kimi_primary import serve_time_invariants
    baseline = dict(deepcopy(manifest['baseline']), cache_key=manifest['cache_key'])
    if build_manifest(manifest['specification'], [baseline]) != manifest:
        raise PatchRejected('Baseline contract was altered')
    layer = _current_map(current_layers, {CASE_KEY})[CASE_KEY]
    for field in BASELINE_KEYS:
        if field not in layer or digest(layer[field]) != manifest['baseline_sha256'][field]:
            raise PatchRejected('Current layer changed: ' + field)
    spec = manifest['specification']; _validate(spec, layer); route = layer['route']
    key = vo._key('IDN', 'MYS', 'tourism', 'ordinary_passport')
    prior = vo._parse_rows(layer['seed_entries'], {}).get(key)
    if not prior: raise PatchRejected('Missing original verdict owner')
    original, checked = vo.merge_verified_fields(layer['raw_guidance'], prior['fields'], source_url=prior['source_url'])
    original_prov = dict(prior['field_provenance'].get('disposition') or vo._provenance(prior),
                         fields=sorted(checked), field_provenance=prior['field_provenance'])
    if original != layer['merged_guidance'] or original_prov != layer['source_provenance']:
        raise PatchRejected('Current layer reconstruction differs')
    output = deepcopy(prior)
    output.update(route={'nationality': 'IDN', 'destination': 'MYS', 'travel_purpose': 'tourism',
                         'travel_document_type': 'ordinary_passport'}, review_id=spec['id'], partial_review=True)
    for change in spec['changes']:
        field = change['field']; proof = field_provenance(change['proof'], route, field)
        if field != 'exceptions': proof['verification_scope'] = 'malaysia_entry_fields_only'
        output['fields'][field] = deepcopy(change['new']); output['field_provenance'][field] = vo._provenance(proof)
    parsed = vo._parse_rows([output], {}).get(key)
    expected = deepcopy(prior); expected['fields'].update(deepcopy(VALUES))
    expected['field_provenance'].update({f: output['field_provenance'][f] for f in FIELDS})
    if parsed != expected: raise PatchRejected('Loader changed reviewed facts or prior authorship')
    effective, checked_after = vo.merge_verified_fields(layer['raw_guidance'], parsed['fields'], source_url=parsed['source_url'])
    provenance = dict(original_prov, fields=sorted(checked_after), field_provenance=parsed['field_provenance'])
    if ({k: v for k, v in original.items() if k not in FIELDS} != {k: v for k, v in effective.items() if k not in FIELDS}
            or set(checked_after) != set(checked) | FIELDS):
        raise PatchRejected('Correction changed an unrelated value or proof scope')
    if serve_time_invariants(effective) != serve_time_invariants(original):
        raise PatchRejected('Correction altered integrity holds')
    before = tstation.records_for_route(route, original, original_prov)
    after = tstation.records_for_route(route, effective, provenance)
    if len(before) != len(after) or len(after) != 1:
        raise PatchRejected('Synthetic default product scope changed')
    allowed_cells = {'required_documents', 'entry_requirements', 'special_conditions'}
    if {k:v for k,v in before[0].items() if k not in allowed_cells} != {k:v for k,v in after[0].items() if k not in allowed_cells}:
        raise PatchRejected('Correction changed grade, dates or unrelated record cells')
    report = {'cache_key': CASE_KEY, 'fields_reviewed': sorted(FIELDS),
              'value_changes': sorted(f for f in FIELDS if original.get(f) != effective.get(f)),
              'new_verdict_reviews': 0, 'new_product_reviews': 0, 'new_grounded_check': False,
              'new_release': False, 'renew_fresh_until': False, 'before_records': before, 'after_records': after}
    return {'schema_version': 1, 'kind': 'reviewed_overlay_conversion', 'review_id': spec['id'],
            'entries': [output], 'status': 'detached; exact-layer preflight required'}, report, effective, provenance
