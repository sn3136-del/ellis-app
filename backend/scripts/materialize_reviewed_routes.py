"""Insert absent canonical routes from an explicit reviewed evidence manifest.

Dry-run is the default. Applying requires --apply --backup NEW_FILE. This tool
does not call a model, fetch websites, edit overrides, release answers, or turn
an imported review into a grounded source check. The exact approved guidance
is retained; validation defaults are never silently inserted. New rows are due
for the first freshness sweep and may remain held while evidence is checked.

Manifest v1: {schema_version:1,id,reviewed_at,sources:[{id,url,checked_at,text,
reading_method}],routes:[{route:{nationality,destination,travel_purpose,
travel_document_type},guidance,field_provenance,policy_valid_from?,
policy_valid_through?}]}. Each nonempty factual field has AI provenance with
source_id,source_url,verified_at,quote,note. A disposition's reviewed country
table may additionally provide source_table:{heading_quote,table_quote,
nationality_quote}; all parts must be literal and bounded to that table.
Named-program evidence may use source_closed_list for the ICA Singapore
entry-visa list, or source_eu_citizen with an independently captured official
EU member-country page. These are reviewed inferences, not grounded checks.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import urlsplit
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.visa_snapshot import evidence_validator as evidence
from app.visa_snapshot.structured_evidence import validate_route_evidence, guidance_conditions_preserved
from app.visa_snapshot import kimi_primary as kp, registry, tstation
from app.visa_snapshot import verified_overrides as overrides
from app.visa_snapshot.change_log import diff

PURPOSES = {'tourism', 'business', 'family_visit', 'study', 'work', 'transit', 'other'}
METHODS = {'fetched_text', 'pdf_text', 'visual_official_document'}
UNKNOWN = (None, '', [], {})
CONSEQUENTIAL = {'permitted_stay', 'permitted_stay_days', 'visa_products',
                 'required_documents', 'entry_requirements'}



class MaterializationError(ValueError):
    def __init__(self, report):
        self.report = report
        super().__init__('Manifest rejected; no route, issue or history changes were applied')


def _date(value, label, *, today=None):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError(label + ' must use YYYY-MM-DD')
    parsed = date.fromisoformat(value)
    if today is not None and parsed > today:
        raise ValueError(label + ' cannot claim a future review')
    return parsed


def _official(url):
    parsed = urlsplit(str(url or ''))
    return (parsed.scheme == 'https' and not parsed.username and not parsed.password
            and evidence.source_is_official(str(url)))


def _date_in_quote(value, quote):
    when = _date(value, 'policy date')
    forms = {value, when.strftime('%d %B %Y'), when.strftime('%d %b %Y'),
             when.strftime('%B %d, %Y'), when.strftime('%B %d %Y')}
    forms |= {form.replace(' 0', ' ').lstrip('0') for form in forms}
    return any(evidence.quote_in_text(form, quote) for form in forms)


def _numeric_support(value, quote):
    """Reject unsupported numbers without pretending lexical matches prove
    every semantic claim. The explicit AI review remains recorded as a review,
    rather than becoming the engine's independently grounded contract."""
    if value in UNKNOWN or isinstance(value, bool):
        return True
    if isinstance(value, dict):
        return all(_numeric_support(v, quote) for k, v in value.items()
                   if k not in {'source_url', 'source_quote', 'verified_at', 'verifier',
                                'field_provenance', 'corroborating_sources'})
    if isinstance(value, list):
        return all(_numeric_support(v, quote) for v in value)
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            return False
        if value == 0 and re.search(r'\bfree\b|no (?:visa )?fee|without charge|免費', quote, re.I):
            return True
        value = format(value, 'g')
    # Currency-prefixed grouped amounts only: do not turn an ordinary decimal
    # such as 0.500 into five hundred. Rp500.000 is Indonesian rupiah notation.
    quote = re.sub(r'(?i)(?:Rp|IDR)\s*(\d{1,3}(?:\.\d{3})+)(?![\d.])',
                   lambda m: m[0] + ' ' + m[1].replace('.', ''), quote)
    quote = re.sub(r'(?<![\d.,])(\d{1,3}(?:,\d{3})+)(?![\d.,])',
                   lambda m: m[0] + ' ' + m[1].replace(',', ''), quote)
    numbers = re.findall(r'(?<![\w.])\d+(?:\.\d+)?(?![\w.])', str(value))
    return all(re.search(r'(?<![\d.])0*' + re.escape(n) + (r'(?:\.0+)?' if '.' not in n else '') + r'(?![\d.])', quote) for n in numbers)


def _referenced_source_ids(provenance, sources):
    found = set()
    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key.endswith('source_id') and isinstance(child, str) and child in sources:
                    found.add(child)
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(provenance)
    return sorted(found)


def _proof(name, value, proof, sources, route, today):
    if not isinstance(proof, dict) or proof.get('verifier') != 'ai':
        raise ValueError(name + ' needs explicit AI field provenance')
    _date(proof.get('verified_at'), name + ' verified_at', today=today)
    if not isinstance(proof.get('note'), str) or not proof['note'].strip():
        raise ValueError(name + ' needs a review note')
    source = sources.get(proof.get('source_id'))
    if not source or proof.get('source_url') != source['url']:
        raise ValueError(name + ' needs its exact catalogued source URL')
    if name == 'source_url' and value != source['url']:
        raise ValueError('source_url must match its provenance source')
    if proof['verified_at'] != source['checked_at']:
        raise ValueError(name + ' review date must match its source read')
    if not evidence.jurisdiction_matches(source['url'], route['destination_country']):
        raise ValueError(name + ' source is not the destination authority')
    quote = proof.get('quote')
    if not isinstance(quote, str) or not quote.strip() or not evidence.quote_in_text(quote, source['text']):
        raise ValueError(name + ' quote is absent from the captured source text')
    additional = proof.get('additional_quotes', [])
    if not isinstance(additional, list) or any(not isinstance(part, str) or not part.strip()
            or not evidence.quote_in_text(part, source['text']) for part in additional):
        raise ValueError(name + ' additional quote is absent from the captured source text')
    supporting = proof.get('supporting_evidence', [])
    if not isinstance(supporting, list):
        raise ValueError(name + ' supporting evidence must be a list')
    supporting_quotes = []
    for item in supporting:
        extra = sources.get(item.get('source_id')) if isinstance(item, dict) else None
        if not extra or item.get('source_url') != extra['url']:
            raise ValueError(name + ' supporting evidence needs its exact catalogued source URL')
        if extra['checked_at'] != proof['verified_at'] or not evidence.jurisdiction_matches(extra['url'], route['destination_country']):
            raise ValueError(name + ' supporting evidence needs current destination-authority provenance')
        fragment = item.get('quote')
        if not isinstance(fragment, str) or not fragment.strip() or not evidence.quote_in_text(fragment, extra['text']):
            raise ValueError(name + ' supporting quote is absent from its captured source text')
        supporting_quotes.append(fragment)
    quoted_evidence = '\n'.join([quote] + additional + supporting_quotes)
    if name == 'disposition':
        result = validate_route_evidence(proof, source, sources, route, value, policy_date=today.isoformat())
        if not result['ok']:
            raise ValueError('disposition lacks exact route/document/purpose evidence')
    elif name in {'policy_valid_from', 'policy_valid_through'}:
        if not _date_in_quote(value, quoted_evidence):
            raise ValueError(name + ' date is not stated in its quote')
    elif name not in {'source_url', 'official_portal_url', 'corroborating_sources'}:
        if not _numeric_support(value, quoted_evidence):
            raise ValueError(name + ' contains numbers absent from its evidence')
        if name in {'biometrics_required', 'appointment_required', 'interview_required'} and not evidence.field_value_supported(name, value, quoted_evidence):
            raise ValueError(name + ' boolean is not supported by its quote')


def _validate_entry(entry, sources, today):
    if not isinstance(entry, dict):
        raise ValueError('route entry must be an object')
    raw_route = entry.get('route')
    required = {'nationality', 'destination', 'travel_purpose', 'travel_document_type'}
    if not isinstance(raw_route, dict) or set(raw_route) != required:
        raise ValueError('route must explicitly contain nationality, destination, purpose and document only')
    nat, dest = raw_route['nationality'], raw_route['destination']
    if not all(isinstance(code, str) and re.fullmatch(r'[A-Z]{3}', code) and registry.iso3(code, default=None) == code for code in (nat, dest)) or nat == dest:
        raise ValueError('route needs distinct registered ISO alpha-3 countries')
    documents = {r['code'] for r in registry.load_registry('travel_document_types')['entries']}
    if raw_route['travel_document_type'] not in documents or raw_route['travel_purpose'] not in PURPOSES:
        raise ValueError('unknown route purpose or document')
    route = {'passport_nationality': nat, 'passport_issuing_country': nat,
             'destination_country': dest, 'travel_purpose': raw_route['travel_purpose'],
             'travel_document_type': raw_route['travel_document_type']}
    guidance, provenance = entry.get('guidance'), entry.get('field_provenance')
    if not isinstance(guidance, dict) or not isinstance(provenance, dict):
        raise ValueError('guidance and field_provenance must be objects')
    json.dumps(guidance, allow_nan=False)
    if set(guidance) - overrides.OVERRIDABLE:
        raise ValueError('guidance contains unsupported fields')
    if guidance.get('disposition') not in kp.DISPOSITIONS or not _official(guidance.get('source_url')):
        raise ValueError('guidance needs an explicit disposition and official source')
    if str(guidance.get('confidence') or '').lower() not in {'', 'low', 'medium'}:
        raise ValueError('imported AI review cannot claim High confidence')
    if not any(guidance.get(field) not in UNKNOWN for field in CONSEQUENTIAL):
        raise ValueError('a verdict-only or empty placeholder is not a reviewed route')
    errors = overrides._field_errors(guidance) + kp.serve_time_invariants(guidance)
    for field in overrides._CUSTOMER_TEXT:
        if field in guidance and overrides._reads_like_review(guidance[field]):
            errors.append(field + ' contains reviewer instructions')
    if errors:
        raise ValueError('; '.join(errors))
    for name, value in guidance.items():
        if value in UNKNOWN or name == 'confidence':
            continue
        _proof(name, value, provenance.get(name), sources, route, today)
    disposition_proof = provenance['disposition']
    condition_scope = validate_route_evidence(disposition_proof, sources[disposition_proof['source_id']],
        sources, route, guidance['disposition'], policy_date=today.isoformat())
    condition_check = guidance_conditions_preserved(condition_scope, guidance)
    if not condition_check['ok']:
        raise ValueError('guidance omits source condition: ' + condition_check['reason'])
    for name in ('policy_valid_from', 'policy_valid_through'):
        if entry.get(name):
            bound = _date(entry[name], name)
            if name == 'policy_valid_from' and bound > today or name == 'policy_valid_through' and bound < today:
                raise ValueError('reviewed policy is not effective today')
            _proof(name, entry[name], provenance.get(name), sources, route, today)
    # Validate a copy for gaps/status, but never persist default facts added by
    # validate_answer. The supplied guidance remains the exact approved data.
    _, missing, contradictions = kp.validate_answer(deepcopy(guidance))
    if contradictions:
        raise ValueError('validation contradiction: ' + '; '.join(contradictions))
    merged, existing_provenance = overrides.apply(deepcopy(guidance), route)
    final_problems = kp.serve_time_invariants(merged)
    if final_problems:
        raise ValueError('existing overlay creates contradiction: ' + '; '.join(final_problems))
    checked_fields = set((existing_provenance or {}).get('fields') or [])
    # The common merger canonicalises empty collections to null and the
    # exemption's not_required channel to null, and clears visa-application
    # appointment/interview flags to False for exempt entry. These are
    # classification normalizations, not border-inspection promises.
    # Every other explicit value, including null clearing a stay/fee, is exact.
    def equivalent(name, value):
        actual = merged.get(name)
        return (actual == value or value in UNKNOWN and actual in UNKNOWN
                or name == 'application_channel' and value == 'not_required'
                and actual is None and guidance.get('disposition') == 'VISA_EXEMPT'
                or name in {'appointment_required', 'interview_required'} and value is None
                and actual is False and guidance.get('disposition') == 'VISA_EXEMPT')
    if any(not equivalent(name, value) for name, value in guidance.items()):
        raise ValueError('existing overlay changes a reviewed field; correct the overlay explicitly first')
    records = tstation.records_for_route(route, merged, existing_provenance, grounded_ok=False)
    if not records or any(r.get('confidence_level') == 'High' for r in records):
        raise ValueError('import cannot claim a High-grade or empty served record')
    return {'cache_key': kp.cache_key(route), 'route': route, 'guidance': deepcopy(guidance),
            'missing_fields': missing, 'status': kp.STATUS_UNCERTAIN if missing else kp.STATUS_PRIMARY,
            'field_provenance': deepcopy(provenance),
            'policy_valid_from': entry.get('policy_valid_from'), 'policy_valid_through': entry.get('policy_valid_through'),
            'preview_grades': sorted({r.get('confidence_level') for r in records}),
            'source_ids': _referenced_source_ids(provenance, sources)}


def _manifest(path, today):
    raw = Path(path).read_bytes()
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get('schema_version') != 1 or not isinstance(data.get('id'), str) or not data['id'].strip():
        raise ValueError('a named schema_version 1 manifest is required')
    _date(data.get('reviewed_at'), 'manifest reviewed_at', today=today)
    if not isinstance(data.get('routes'), list) or not data['routes'] or not isinstance(data.get('sources'), list):
        raise ValueError('manifest needs nonempty routes and source evidence')
    sources = {}
    for source in data['sources']:
        if (not isinstance(source, dict) or not isinstance(source.get('id'), str) or not source['id']
                or source['id'] in sources or not _official(source.get('url'))
                or not isinstance(source.get('text'), str) or not source['text'].strip()
                or source.get('reading_method') not in METHODS):
            raise ValueError('invalid, duplicate or unofficial captured source')
        _date(source.get('checked_at'), 'source checked_at', today=today)
        sources[source['id']] = source
    entries, invalid, seen = [], [], set()
    overrides.reload()
    for index, entry in enumerate(data['routes']):
        try:
            valid = _validate_entry(entry, sources, today)
            if valid['cache_key'] in seen:
                raise ValueError('duplicate canonical route in manifest')
            seen.add(valid['cache_key'])
            entries.append(valid)
        except (ValueError, TypeError, KeyError) as error:
            invalid.append({'index': index, 'reason': str(error)})
    return data, sources, entries, invalid, hashlib.sha256(raw).hexdigest()


def materialize(database, *, manifest, apply=False, backup=None, now=None):
    now = now or datetime.now(timezone.utc)
    data, sources, entries, invalid, manifest_hash = _manifest(manifest, now.date())
    database = Path(database).resolve()
    if apply:
        if not backup:
            raise ValueError('--apply requires an explicit backup')
        backup = Path(backup).resolve()
        if backup.exists() or backup == database or not backup.parent.is_dir():
            raise ValueError('backup must be a new, separate file in an existing directory')
    db = sqlite3.connect(database.as_uri() + ('?mode=rw' if apply else '?mode=ro'), uri=True, timeout=30)
    try:
        if apply:
            db.execute('BEGIN IMMEDIATE')
        keys = {r[0] for r in db.execute('select cache_key from kimi_route_guidance_cache')}
        plan = []
        for entry in entries:
            key = entry['cache_key']
            action = 'existing' if key in keys else 'insert'
            legacy = [old for old in keys if old != key and kp.canonical_key(old) == key]
            if action == 'insert' and legacy:
                action = 'invalid_legacy_orphan'
                invalid.append({'cache_key': key, 'reason': 'legacy orphan requires explicit migration review'})
            plan.append({'cache_key': key, 'action': action, 'preview_grades': entry['preview_grades'],
                         'missing_fields': entry['missing_fields']})
        report = {'manifest_id': data['id'], 'manifest_sha256': manifest_hash,
                  'manifest_routes': len(data['routes']), 'applied': False, 'backup': None,
                  'inserted': 0, 'existing': sum(p['action'] == 'existing' for p in plan),
                  'would_insert': sum(p['action'] == 'insert' for p in plan),
                  'skipped_invalid': len(invalid), 'invalid': invalid, 'plan': plan,
                  'existing_cache_issues_and_history_preserved': True,
                  'review_is_not_grounded_verification': True}
        if invalid:
            if apply:
                raise MaterializationError(report)
            return report
        if not apply:
            return report
        with backup.open('xb'):
            pass
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as snapshot:
            with sqlite3.connect(backup) as destination:
                snapshot.backup(destination)
        stamp = now.astimezone(timezone.utc).replace(tzinfo=None).isoformat(' ', timespec='microseconds')
        for entry, action in zip(entries, plan):
            if action['action'] != 'insert':
                continue
            review = {'kind': 'reviewed_manifest_materialization', 'verifier': 'ai',
                      'manifest_id': data['id'], 'manifest_sha256': manifest_hash,
                      'reviewed_at': data['reviewed_at'], 'imported_at': now.isoformat(),
                      'field_provenance': entry['field_provenance'],
                      'sources': [sources[sid] for sid in entry['source_ids']],
                      'policy_valid_from': entry['policy_valid_from'], 'policy_valid_through': entry['policy_valid_through'],
                      'requires_initial_source_check': True}
            db.execute('''insert into kimi_route_guidance_cache
                (id,cache_key,route,status,guidance,missing_fields,contradictions,model,verification,
                 generated_at,fresh_until,created_at,updated_at) values (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (uuid.uuid4().hex, entry['cache_key'], json.dumps(entry['route']), entry['status'],
                 json.dumps(entry['guidance'], ensure_ascii=False, allow_nan=False), json.dumps(entry['missing_fields']),
                 '[]', 'reviewed-source-import', json.dumps({'source_review': review}, ensure_ascii=False),
                 stamp, stamp, stamp, stamp))
            # Direct insertion keeps history atomic and does not invoke the
            # change-log helper's optional external messaging webhook.
            db.execute('''insert into database_change_log
                (id,cache_key,route,action,origin,changes,note,created_at,updated_at)
                values (?,?,?,?,?,?,?,?,?)''',
                (uuid.uuid4().hex, entry['cache_key'], json.dumps(entry['route']), 'add', 'reviewed-source-import',
                 json.dumps(diff(None, entry['guidance']), ensure_ascii=False),
                 'Added absent canonical route from AI-reviewed manifest ' + data['id'][:200]
                 + '; initial official-source check is due. No operator release or grounded-check stamp was created.', stamp, stamp))
            report['inserted'] += 1
        db.commit()
        report['applied'], report['backup'] = True, str(backup)
        return report
    finally:
        db.close()  # An exception rolls back the entire pending transaction.


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database')
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup')
    args = parser.parse_args()
    try:
        result = materialize(args.database, manifest=args.manifest, apply=args.apply, backup=args.backup)
    except MaterializationError as error:
        print(json.dumps(error.report, indent=2, ensure_ascii=False))
        raise SystemExit(2)
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.exit(2, f'No reviewed routes applied: {error}\n')
    print(json.dumps(result, indent=2, ensure_ascii=False))
    raise SystemExit(2 if result['skipped_invalid'] else 0)
