"""Exact current conditions and obsolete-uncertainty adjudication for KOR→CHN.

Only six condition fields change. Existing verdict, stays, products, general
review dates/grades, raw/operator layers and freshness deadlines are retained.
The three known stale warning objects are separately bound for safe serving;
uncertainty is never made a generally overridable policy field.
"""
from copy import deepcopy
from datetime import date, datetime, timezone

from scripts.convert_reviewed_product_validity import BASELINE_KEYS, _current_map, _sources
from scripts.convert_reviewed_product_patch import field_provenance, _subject
from scripts.prepare_reviewed_product_patch import PatchRejected, digest

KEY = 'KOR|KOR|CHN|tourism|default|unknown|v6'
URL = 'https://cs.mfa.gov.cn/gyls/lsgz/fwxx/202511/t20251110_11749824.shtml'
MANIFEST = 'reviewed_kor_chn_conditions_manifest_20260910.json'
OVERLAY = 'reviewed_kor_chn_conditions_overlay_20260910.json'
VALUES = {
    'passport_validity': 'Ordinary passport valid for at least the entire intended stay in China.',
    'passport_validity_requirement': {'kind': 'valid_through_departure', 'months': 0},
    'required_documents': ['Ordinary passport valid for the entire intended stay',
        'Invitation letters, air tickets and accommodation reservations are recommended as evidence of the purpose of entry'],
    'application_channel_detail': 'Eligible ordinary-passport visitors do not need to make advance visa-waiver declarations to Chinese embassies or consulates. Border examination and approval still apply.',
    'entry_requirements': 'Use an ordinary passport valid for the entire intended stay. Admission is subject to examination and approval by Chinese border inspection authorities. The visa waiver covers tourism; it does not cover work, study or news coverage. Supporting invitation letters, air tickets and accommodation reservations are recommended.',
    'insurance_required': None,
}
FIELDS = frozenset(VALUES)
METADATA_FIELDS = ('id', 'cache_key', 'status', 'model', 'missing_fields', 'contradictions',
                   'verification', 'generated_at', 'fresh_until', 'created_at', 'updated_at')
METADATA_DATES = frozenset(('generated_at', 'fresh_until', 'created_at', 'updated_at'))


def pending_metadata(row):
    """Lossless SQL/ORM date normalization for the exact reviewed cache revision."""
    result = {}
    for key in METADATA_FIELDS:
        value = row[key] if isinstance(row, dict) else getattr(row, key)
        if key in METADATA_DATES:
            if not isinstance(value, (str, datetime)):
                raise PatchRejected('The reviewed pending revision requires actual cache dates')
            stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
            stamp = stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)
            value = stamp.isoformat()
        result[key] = deepcopy(value)
    return result
OLD_UNCERTAINTY = [
    {'field': 'disposition/permitted_stay_days', 'reason': "Unilateral visa-free policy for ROK ordinary passport holders was announced through 2025 and may be extended, expanded to 30 days, or withdrawn; today's date is 2026-08-31, so confirm the current rule with Chinese authorities before travel."},
    {'field': 'passport_validity_requirement', 'reason': 'Exact minimum validity for visa-free entry was not verified from an official source.'},
    {'field': 'source_url', 'reason': 'Could not retrieve the single official Chinese government page for this route.'},
]
QUOTES = {
    'stay': 'They can stay in China for no more than 30 days without a visa.',
    'expiry': 'Other 48 Countries | remain in effect until December 31, 2026',
    'passport': 'For foreign nationals, an ordinary passport valid for at least the duration of intended stay in China is needed.',
    'document_types': 'Holders of travel documents or temporary or emergency documents other than ordinary passports are not allowed to enter into China without a visa.',
    'documents': 'It is recommended to take documents such as invitation letters, air tickets and reservations of accommodation as a proof corresponding to the purposes of entry into China.',
    'purpose': 'Visa waiver does not apply to those who come to China for work, study, news coverage or purposes alike.',
    'border': 'Foreign nationals traveling for the purpose of business, tourism, family or friends visit，exchange and transit that meet the visa waiver requirements can be allowed to enter China without a visa upon examination and approval in accordance with law by border inspection authorities.',
    'declaration': 'Foreign nationals eligible for the visa waiver do not need to declare in advance to Chinese embassies or consulates before entering China without a visa.',
    'clock': 'The duration of stay without a visa is calculated from the next day of entry and lasts for 30 calendar days.',
}
PROOF_QUOTES = {'passport_validity': ('passport', 'document_types'),
                'passport_validity_requirement': ('passport',),
                'required_documents': ('passport', 'documents'),
                'application_channel_detail': ('declaration', 'border'),
                'entry_requirements': ('passport', 'border', 'purpose', 'documents')}


def validate(spec, layer):
    from scripts.convert_reviewed_general_batch import _check_proof, quote_literal
    if (spec.get('schema_version') != 1 or spec.get('kind') != 'exact_kor_chn_condition_review'
            or spec.get('cache_key') != KEY or spec.get('route') != layer['route']):
        raise PatchRejected('Wrong condition-review contract or route')
    route = layer['route']
    expected = {'passport_nationality': 'KOR', 'lawful_country_of_residence': 'KOR',
        'destination_country': 'CHN', 'visa_category': 'tourist_visa', 'travel_purpose': 'tourism',
        'arrival_date': None, 'consular_jurisdiction': None, 'travel_document_type': 'ordinary_passport', 'transit_countries': None}
    if route != expected or spec.get('resolved_uncertainty') != OLD_UNCERTAINTY:
        raise PatchRejected('Only the exact ordinary-tourism route and three obsolete warnings are covered')
    pending = spec.get('pending_baseline') or {}
    if (set(pending) != set(METADATA_FIELDS) or pending_metadata(pending) != pending
            or digest(pending) != spec.get('pending_baseline_sha256')
            or pending.get('cache_key') != KEY or pending.get('status') != 'KIMI_PRIMARY'
            or pending.get('missing_fields') != [] or pending.get('contradictions') != []
            or pending.get('verification', {}).get('detail_pending') is not True):
        raise PatchRejected('The exact obsolete pending cache revision must be bound')
    g = layer['merged_guidance']
    if (g.get('uncertainty') != OLD_UNCERTAINTY or g.get('disposition') != 'VISA_EXEMPT'
            or g.get('requirement_detail') != 'conditional_visa_free' or g.get('permitted_stay_days') != 30
            or g.get('visa_products') != []):
        raise PatchRejected('Current policy, product inventory or uncertainty scope changed')
    sources = _sources(spec)
    if set(sources) != {'china-mfa-faq'} or sources['china-mfa-faq']['url'] != URL:
        raise PatchRejected('The current Chinese MFA FAQ is required')
    text = sources['china-mfa-faq']['text']
    nationality = spec.get('nationality_quote')
    if (not isinstance(nationality, str) or not quote_literal(nationality, text)
            or 'Nationals holding valid ordinary passports of 50' not in nationality
            or 'the Republic of Korea' not in nationality
            or 'are exempted from visa requirement' not in nationality
            or 'tourism' not in nationality
            or any(not quote_literal(q, text) for q in QUOTES.values())):
        raise PatchRejected('Nationality, period and complete controlling conditions must be literal current captures')
    if spec.get('policy_effective_to') != '2026-12-31':
        raise PatchRejected('The Korea policy expiry may not be widened')
    changes = spec.get('changes') or []
    if len(changes) != len(FIELDS) or {c.get('field') for c in changes} != FIELDS:
        raise PatchRejected('Only the exact six entry-condition fields may change')
    for c in changes:
        f = c['field']; p = c.get('proof') or {}
        if (set(c) != {'field', 'old_raw', 'old_merged', 'new', 'proof'} or
                c['old_raw'] != layer['raw_guidance'].get(f) or c['old_merged'] != g.get(f)
                or c['new'] != VALUES[f]):
            raise PatchRejected('Reviewed old or corrected field value changed: ' + f)
        if f == 'insurance_required':
            if p != {'status': 'unknown', 'verifier': 'ai', 'reason': 'The captured MFA FAQ does not establish an insurance requirement or exemption; the unsupported previous No is withdrawn.'}:
                raise PatchRejected('Insurance is unknown, never Not published or an exemption claim')
            continue
        if p.get('status') != 'reviewed' or p.get('verifier') != 'ai' or p.get('subject') != _subject(route):
            raise PatchRejected('Exact AI-attributed field subject is required')
        required = {QUOTES[k] for k in PROOF_QUOTES[f]}
        evidence = p.get('evidence') or []
        if (len(evidence) != len(required) or {e.get('quote') for e in evidence} != required
                or any(e.get('source_url') != URL or e.get('source_id') != 'china-mfa-faq' for e in evidence)):
            raise PatchRejected('Field proof does not retain its complete literal conditions')
        _check_proof(deepcopy(p), sources, route, f, c['new'])
        try:
            when = date.fromisoformat(p['verified_at'])
        except (ValueError, TypeError, KeyError) as exc:
            raise PatchRejected('Missing actual field review date') from exc
        if when > date.today() or when.isoformat() != sources['china-mfa-faq']['checked_at']:
            raise PatchRejected('Field evidence date does not match the current capture')
    return sources


def build_manifest(spec, layers):
    layer = _current_map(layers, {KEY})[KEY]
    if any(k not in layer for k in BASELINE_KEYS):
        raise PatchRejected('All six baseline layers are required')
    baseline = {k: deepcopy(layer[k]) for k in BASELINE_KEYS}
    hashes = {k: digest(v) for k, v in baseline.items()}
    if hashes != spec.get('baseline_sha256'):
        raise PatchRejected('The reviewed baseline changed')
    validate(spec, layer)
    return {'schema_version': 1, 'kind': 'kor_chn_conditions_exact_layers', 'cache_key': KEY,
            'specification': deepcopy(spec), 'baseline': baseline, 'baseline_sha256': hashes}


def convert(manifest, layers):
    from app.visa_snapshot import verified_overrides as vo, tstation
    from app.visa_snapshot.kimi_primary import serve_time_invariants
    if manifest.get('kind') != 'kor_chn_conditions_exact_layers' or manifest.get('schema_version') != 1:
        raise PatchRejected('Wrong manifest contract')
    baseline = dict(deepcopy(manifest['baseline']), cache_key=KEY)
    if build_manifest(manifest['specification'], [baseline]) != manifest:
        raise PatchRejected('The bound review contract changed')
    layer = _current_map(layers, {KEY})[KEY]
    if any(k not in layer or digest(layer[k]) != manifest['baseline_sha256'][k] for k in BASELINE_KEYS):
        raise PatchRejected('Current six-layer baseline changed')
    if layer['operator_entries']:
        raise PatchRejected('Operator-owned routes need their own integration contract')
    spec = manifest['specification']; validate(spec, layer)
    key = vo._key('KOR', 'CHN', 'tourism', 'ordinary_passport')
    prior = vo._parse_rows(layer['seed_entries'], {}).get(key)
    if not prior:
        raise PatchRejected('Prior source ownership is required')
    original, checked = vo.merge_verified_fields(layer['raw_guidance'], prior['fields'], source_url=prior['source_url'])
    original_prov = dict(prior['field_provenance'].get('disposition') or vo._provenance(prior),
                         fields=sorted(checked), field_provenance=prior['field_provenance'])
    if original != layer['merged_guidance'] or original_prov != layer['source_provenance']:
        raise PatchRejected('Current facts and authorship cannot be reconstructed')
    output = deepcopy(prior)
    output.update(route={'nationality': 'KOR', 'destination': 'CHN', 'travel_purpose': 'tourism',
                         'travel_document_type': 'ordinary_passport'}, review_id=spec['id'],
                  partial_review=True, field_review_scope='kor_chn_entry_conditions_only')
    for c in spec['changes']:
        f = c['field']; p = field_provenance(c['proof'], layer['route'], f)
        p['verification_scope'] = 'kor_chn_entry_conditions_only'
        output['fields'][f] = deepcopy(c['new'])
        output['field_provenance'][f] = vo._provenance(p)
    parsed = vo._parse_rows([output], {}).get(key)
    expected = deepcopy(prior)
    expected['fields'].update(deepcopy(VALUES))
    expected['field_provenance'].update({f: output['field_provenance'][f] for f in FIELDS})
    expected['field_provenance']['insurance_required'] = {
        'status': 'unknown', 'verifier': 'ai', 'source_url': '', 'verified_at': None,
        'verified_by': '', 'note': output['field_provenance']['insurance_required']['note']}
    if parsed != expected:
        raise PatchRejected('Loader altered facts or review ownership outside this correction')
    effective, checked = vo.merge_verified_fields(layer['raw_guidance'], parsed['fields'], source_url=parsed['source_url'])
    if {k: v for k, v in effective.items() if k not in FIELDS} != {k: v for k, v in original.items() if k not in FIELDS}:
        raise PatchRejected('Unrelated facts changed')
    if serve_time_invariants(effective) != serve_time_invariants(original):
        raise PatchRejected('Independent integrity holds changed')
    provenance = dict(original_prov, fields=sorted(checked), field_provenance=parsed['field_provenance'])
    before = tstation.records_for_route(layer['route'], original, original_prov)
    after = tstation.records_for_route(layer['route'], effective, provenance)
    if len(before) != len(after) or any(a['confidence_level'] != b['confidence_level'] for a, b in zip(before, after, strict=True)):
        raise PatchRejected('Product inventory or review grades changed')
    overlay = {'schema_version': 1, 'kind': 'reviewed_overlay_conversion', 'review_id': spec['id'],
               'review_scope': 'kor_chn_entry_conditions_only', 'entries': [output]}
    report = {'cache_key': KEY, 'changed_fields': sorted(FIELDS), 'new_verdict_grade': False,
              'new_product_grade': False, 'renew_fresh_until': False, 'raw_or_operator_writes': False,
              'uncertainty_adjudication': 'Only the three bound stale warning objects, once this exact output is registered and current.'}
    return overlay, report, effective, provenance
