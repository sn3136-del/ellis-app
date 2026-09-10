"""Exact, reviewed government-account publications; never a social host grant.

The versioned local registry binds one government backlink, one account, one
post, its captured text and one route's approved facts. An authenticated
origin mission remains attributed to its own government; it does not become
the destination owner. Stored capture dates never become fresh HTTP checks.
"""
from datetime import date
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from .authority import hostname, is_government_host
from .authority_ownership import government_owner

REGISTRY_PATH = Path(__file__).resolve().parents[3] / 'data/database_seed/reviewed_social_authorities.json'
_SCOPE = ('passport_nationality', 'destination_country', 'travel_document_type', 'travel_purpose')
_MISSING = object()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def _text_hash(value):
    return hashlib.sha256(value.encode()).hexdigest() if isinstance(value, str) else None


def _safe_https(url):
    try:
        u = urlsplit(url)
        return u.scheme == 'https' and bool(u.hostname) and not u.username and not u.password and not u.port and not u.fragment
    except (ValueError, TypeError):
        return False


def _scope(route):
    return {'passport_nationality': route.get('passport_nationality', route.get('nationality')),
            'destination_country': route.get('destination_country', route.get('destination')),
            'travel_document_type': route.get('travel_document_type'),
            'travel_purpose': route.get('travel_purpose')}


def _record_valid(record):
    """The registry is reviewed deployment data; malformed entries fail closed."""
    b = record['binding']; captures = {c['id']: c for c in record['captures']}
    account = b['account_url']; post = b['post_url']; image = b['image_url']
    a, p, i = map(urlsplit, (account, post, image))
    if (b['schema_version'] != 1 or b['authority_kind'] != 'origin_government_mission'
            or any(not _safe_https(u) for u in (account, post, image))
            or a.hostname != 'www.instagram.com' or p.hostname != a.hostname or i.hostname != a.hostname
            or a.query or a.path.count('/') != 2 or not a.path.strip('/')
            or not p.path.startswith('/p/') or p.path.count('/') != 3 or p.query
            or i.path != p.path or i.query != 'img_index=2'
            or set(b['scope']) != set(_SCOPE) or b['scope']['passport_nationality'] != b['owner_country']
            or b['scope']['destination_country'] != b['mission_country']):
        return False
    date.fromisoformat(b['effective_from'])
    auth, policy, visual = (captures[b[k]] for k in ('official_linking_source_id', 'post_source_id', 'image_source_id'))
    if (not _safe_https(auth['url']) or auth['url'] != b['official_linking_source_url']
            or not is_government_host(hostname(auth['url']))
            or government_owner(hostname(auth['url'])) != b['owner_country']
            or not any(link.get('href') == account for link in auth.get('links', []))
            or not auth.get('owner_title') or auth['owner_title'] not in auth['text']
            or urlsplit(account).netloc.removeprefix('www.') + urlsplit(account).path not in auth['text']):
        return False
    for c, expected_url, expected_hash in ((auth, auth['url'], b['official_linking_capture_sha256']),
            (policy, post, b['post_capture_sha256']), (visual, image, b['image_capture_sha256'])):
        if (c['url'] != expected_url or c['sha256'] != expected_hash or _text_hash(c['text']) != expected_hash
                or date.fromisoformat(c['checked_at']) > date.today()):
            return False
    if (policy.get('author_account_url') != account or visual.get('author_account_url') != account
            or _text_hash(policy.get('author_capture_text')) != b['author_capture_sha256']
            or policy.get('author_capture_sha256') != b['author_capture_sha256']
            or visual.get('author_capture_sha256') != b['author_capture_sha256']
            or urlsplit(account).netloc.removeprefix('www.') + urlsplit(account).path not in policy['author_capture_text']
            or post not in policy['author_capture_text'] or visual.get('parent_post_url') != post):
        return False
    return (isinstance(record.get('approved_guidance'), dict) and record['approved_guidance'].get('source_url') == post
            and digest(record['approved_guidance']) == b.get('approved_guidance_sha256'))


@lru_cache(maxsize=8)
def _load(path, mtime, size):
    try:
        data = json.loads(Path(path).read_text())
        rows = data['bindings']
        if data['schema_version'] != 1 or not isinstance(rows, list): return ()
        if len({r['binding']['id'] for r in rows}) != len(rows): return ()
        if not all(_record_valid(r) for r in rows): return ()
        return tuple(rows)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return ()


def _records():
    try:
        stat = REGISTRY_PATH.stat()
        return _load(str(REGISTRY_PATH), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return ()


def registered_reference(url):
    """Retain this exact citation as metadata only; this never verifies a fact."""
    return any(url in (r['binding']['post_url'], r['binding']['image_url']) for r in _records())


def capture_matches(source):
    if not isinstance(source, dict): return False
    for r in _records():
        for approved in r['captures']:
            if source.get('url') not in (r['binding']['post_url'], r['binding']['image_url']): continue
            if all(source.get(k) == approved.get(k) for k in ('id','url','text','sha256','checked_at',
                        'author_account_url','author_capture_text','author_capture_sha256','parent_post_url')):
                return True
    return False


def binding_for(proof, route=None, *, field=None, value=_MISSING):
    from .evidence_validator import quote_in_text
    if not isinstance(proof, dict) or proof.get('verifier') != 'ai': return None
    for r in _records():
        b = r['binding']
        if proof.get('authority_binding_id') != b['id'] or proof.get('authority_binding_sha256') != digest(b): continue
        if proof.get('effective_to') is not None: continue
        if proof.get('subject') != b['scope'] or (route is not None and _scope(route) != b['scope']): continue
        captures = [c for c in r['captures'] if c['url'] == proof.get('source_url') and c['id'] == proof.get('source_id')]
        if len(captures) != 1 or captures[0]['url'] not in (b['post_url'], b['image_url']): continue
        source = captures[0]
        if (proof.get('verified_at') != source['checked_at']
                or _text_hash(proof.get('quote')) != proof.get('quote_sha256')
                or not quote_in_text(proof.get('quote'), source['text'])): continue
        if field is not None:
            if field == 'policy_valid_from': expected = b['effective_from']
            elif field in b['allowed_fact_fields'] or field == 'source_url': expected = r['approved_guidance'].get(field, _MISSING)
            else: continue
            if value is not _MISSING and value != expected: continue
        return r
    return None


def source_applicable(proof, source, sources, route, *, field=None, value=_MISSING):
    """Independent applicability; jurisdiction_matches keeps owner semantics."""
    from .evidence_validator import jurisdiction_matches
    if jurisdiction_matches(source.get('url', ''), route.get('destination_country', '')): return True
    r = binding_for(proof, route, field=field, value=value)
    if not r or not capture_matches(source): return False
    # Ingestion must supply its currently reviewed government backlink too.
    auth = next(c for c in r['captures'] if c['id'] == r['binding']['official_linking_source_id'])
    supplied = sources.get(auth['id'])
    return bool(supplied and all(supplied.get(k) == auth.get(k) for k in ('url','text','sha256','checked_at','links','owner_title')))


def entry_supported(entry):
    if not isinstance(entry, dict) or not isinstance(entry.get('route'), dict): return False
    r = binding_for(entry, entry['route'])
    if not r: return False
    fields = entry.get('fields'); proofs = entry.get('field_provenance') or {}
    if not isinstance(fields, dict) or not fields or not isinstance(proofs, dict): return False
    for field, value in fields.items():
        if field == 'confidence' and value == 'low' or field == 'visa_products' and value in (None, []): continue
        if not binding_for(proofs.get(field), entry['route'], field=field, value=value): return False
    return True


def guidance_supported(guidance, provenance, route):
    """A copied proof cannot bless another route or altered approved values."""
    r = binding_for(provenance, route)
    if not r: return False
    proofs = provenance.get('field_provenance') or {}
    if not isinstance(proofs, dict): return False
    # A missing/edited field-list must not erase the binding's mandatory facts.
    for field, expected in r['approved_guidance'].items():
        if field == 'confidence':
            if guidance.get(field) != expected: return False
            continue
        if field == 'visa_products':
            if guidance.get(field) not in (None, []): return False
            continue
        if guidance.get(field, _MISSING) != expected or not binding_for(
                proofs.get(field), route, field=field, value=expected):
            return False
    try:
        selected = date.fromisoformat(route.get('arrival_date') or date.today().isoformat())
    except (ValueError, TypeError):
        return False
    if selected < date.fromisoformat(r['binding']['effective_from']):
        return False
    structural = {'appointment_required': False, 'interview_required': False,
                  'application_channel': 'not_required', 'processing_time': 'Not applicable (no visa)',
                  'government_fee': {'amount': 0, 'currency': None}, 'route_workflow_type': 'visa_exempt_preparation'}
    for field, value in guidance.items():
        if field in r['approved_guidance'] or value in (None, '', [], {}):
            continue
        if field in structural and value == structural[field]:
            continue
        # This source registry approves a minimal rule, not arbitrary facts
        # added by a later raw regeneration. Additional substantive facts or
        # absence dispositions need a separate captured review before serving.
        return False
    for field in provenance.get('fields') or []:
        value = guidance.get(field)
        if field == 'confidence' and value == 'low' or field == 'visa_products' and value in (None, []): continue
        own = proofs.get(field, provenance)
        # Separately attributed legacy or other-government field reviews keep
        # their own status; this binding supplies no credit for those facts.
        if isinstance(own, dict) and not own.get('authority_binding_id'):
            continue
        if not binding_for(own, route, field=field, value=value): return False
    return (provenance.get('effective_from') == r['binding']['effective_from']
            or (proofs.get('disposition') or {}).get('effective_from') == r['binding']['effective_from'])
