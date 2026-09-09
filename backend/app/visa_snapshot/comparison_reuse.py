"""Bounded extraction memoization, never a cached verification decision.

Every caller must fetch current sources and rerun the ordinary evidence gates.
Only digests and public source quotations are retained; prompts are not stored.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

VERSION = 1  # Bump when extraction, authority or evidence semantics change.
MAX_ENTRIES = 8
MAX_ENTRY_BYTES = 16 * 1024
MAX_CACHE_BYTES = 64 * 1024


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    try:
        return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()
    except (TypeError, ValueError, RecursionError):
        return None


@lru_cache(maxsize=1)
def validator_fingerprint():
    """A deployed validation change invalidates old extraction automatically.

    Read source bytes once per process. Missing code disables reuse rather than
    silently treating an unknown validation implementation as the same contract.
    """
    directory = Path(__file__).parent
    names = ('comparison_reuse.py', 'freshness.py', 'fetching.py', 'evidence_validator.py',
        'freshness_evidence.py', 'structured_evidence.py', 'authority.py', 'authority_ownership.py',
        'kimi_primary.py', 'verified_overrides.py', 'policy_intervals.py', 'scheduled_policies.py')
    try:
        hashes = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in names}
        hashes['passport_validity.py'] = hashlib.sha256((directory.parent / 'passport_validity.py').read_bytes()).hexdigest()
        return digest(hashes)
    except OSError:
        return None


def identity(*, payload, system, guidance, provenance, reviewed_fields, catalog,
             sources, full_text, requested_url, evidence_contract, provider):
    code = validator_fingerprint()
    if not code: return None
    return digest({'version': VERSION, 'validator_fingerprint': code, 'evidence_contract': evidence_contract,
        'payload': payload, 'system': system, 'guidance': guidance,
        'provenance': provenance, 'reviewed_fields': reviewed_fields,
        'catalog': catalog, 'sources': sources, 'full_text': full_text,
        'requested_url': requested_url, 'provider': provider})


def dependencies(captures):
    """Timestamps are not evidence content; missing and redirected pages are."""
    return digest({url: {'url': c['url'], 'text': c['text']}
                   for url, c in captures.items()})


def _entries(cache):
    if not isinstance(cache, dict) or cache.get('version') != VERSION:
        return []
    try:
        if len(_json(cache).encode('utf-8')) > MAX_CACHE_BYTES:
            return []
    except (TypeError, ValueError, RecursionError):
        return []
    entries = cache.get('entries')
    return entries if isinstance(entries, list) and len(entries) <= MAX_ENTRIES else []


def _valid_entry(entry):
    if not isinstance(entry, dict): return False
    try:
        if len(_json(entry).encode('utf-8')) > MAX_ENTRY_BYTES: return False
        timestamp = datetime.fromisoformat(entry['model_compared_at'].replace('Z', '+00:00'))
        if timestamp.tzinfo is None or timestamp > datetime.now(timezone.utc) + timedelta(minutes=1): return False
        datetime.strptime(entry['policy_date'], '%Y-%m-%d')
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError):
        return False
    if any(not isinstance(entry.get(k), str) or not re.fullmatch('[0-9a-f]{64}', entry[k])
           for k in ('signature', 'dependencies', 'response_digest')): return False
    response = entry.get('response')
    return (isinstance(response, dict) and response.get('consistent') is True
        and response.get('corrected_fields') == {} and isinstance(response.get('evidence'), dict)
        and digest(response) == entry['response_digest'])


def lookup(cache, *, signature, source_url, policy_date):
    """Tentative hit only: compare current companion digests before using it."""
    if not signature: return None
    for entry in _entries(cache):
        if (_valid_entry(entry) and entry.get('signature') == signature
                and entry.get('source_url') == source_url and entry.get('policy_date') == policy_date):
            return deepcopy(entry)
    return None


_PROOF_KEYS = {'quote', 'source_id', 'source_table', 'source_closed_list',
               'source_eu_citizen', 'source_country_section'}
_RULE_KEYS = {'program', 'heading_quote', 'table_quote', 'nationality_quote',
    'closing_quote', 'general_rule_quote', 'exception_quote', 'eligibility_quote',
    'age_quote', 'rule_quote', 'section_quote', 'membership_quote', 'excluded_nationality',
    'source_id', 'general_rule_source_id', 'exception_source_id', 'eligibility_source_id', 'list_source_id',
    'nationality_source_id', 'membership_source_id'}


def snapshot(*, answer, check, signature, dependency_digest, source_url, policy_date, page_text):
    """Only an accepted, unchanged extraction may seed a future candidate.

    Retain only field quotations that survived this run's deterministic checks.
    No arbitrary provider note, unknown field, prompt or response metadata is saved.
    """
    from .evidence_validator import quote_in_text
    fields = set(check.get('verified_fields') or [])
    if any(k in answer and not isinstance(answer[k], dict)
           for k in ('corrected_fields', 'evidence', 'field_scope', 'route_evidence')):
        return None
    if any(k in answer and not isinstance(answer[k], bool)
           for k in ('page_relevant', 'page_is_nationality_specific')):
        return None
    if (not fields or check.get('outcome') not in ('checked', 'field_checked')
            or answer.get('consistent') is not True or answer.get('corrected_fields')
            or check.get('unquoted_fields') or not signature or not dependency_digest):
        return None
    response = {'consistent': True, 'corrected_fields': {}, 'evidence': {}}
    for key in ('page_relevant', 'page_is_nationality_specific'):
        response[key] = answer.get(key) is True
    for key in ('evidence', 'field_scope'):
        values = answer.get(key)
        if isinstance(values, dict):
            response[key] = {k: v for k, v in values.items() if k in fields and isinstance(v, str)
                             and quote_in_text(v, page_text)}
    accepted = (check.get('route_evidence') or {}).get('proof')
    if isinstance(accepted, dict):
        proof = {}
        for key in _PROOF_KEYS & accepted.keys():
            value = accepted[key]
            if key in ('quote', 'source_id') and isinstance(value, str):
                proof[key] = value
            elif isinstance(value, dict):
                proof[key] = {k: v for k, v in value.items() if k in _RULE_KEYS and isinstance(v, str)}
        response['route_evidence'] = proof
    entry = {'signature': signature, 'dependencies': dependency_digest,
        'source_url': source_url, 'policy_date': policy_date,
        'model_compared_at': check.get('model_compared_at'), 'response': response,
        'response_digest': digest(response)}
    return entry if _valid_entry(entry) else None


def merge(cache, additions):
    """Newest bounded entries win per source; malformed old entries disappear."""
    entries = []
    seen = set()
    for entry in list(reversed(additions)) + _entries(cache):
        if not _valid_entry(entry) or entry.get('source_url') in seen: continue
        seen.add(entry['source_url'])
        candidate = {'version': VERSION, 'entries': entries + [deepcopy(entry)]}
        if len(_json(candidate).encode('utf-8')) <= MAX_CACHE_BYTES:
            entries = candidate['entries']
        if len(entries) == MAX_ENTRIES: break
    return {'version': VERSION, 'entries': entries}
