"""Fresh reads may reuse extraction; stored results never supply fresh proof."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models import Base
from app.visa_snapshot import comparison_reuse as reuse
from app.visa_snapshot import freshness, kimi_primary, verified_overrides as vo
from app.visa_snapshot.fetching import FetchResult
from app.visa_snapshot.models import KimiRouteGuidanceCache, DatabaseIssueReport

URL = 'https://www.mofa.go.jp/visa'
TEXT = 'Canadian citizens must obtain a visa for tourism in Japan.'
ROUTE = {'passport_nationality': 'CAN', 'destination_country': 'JPN',
         'travel_document_type': 'ordinary_passport', 'travel_purpose': 'tourism'}
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def h(monkeypatch):
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    state = SimpleNamespace(db=db, calls=[], fetches=[], texts={URL: TEXT}, failed=set(),
        override=None, provenance={}, day='2026-09-09T09:00:00+00:00',
        answer={'page_relevant': True, 'page_is_nationality_specific': True,
                'consistent': True, 'corrected_fields': {}, 'evidence': {}},
        redirect=None, raw_hash='raw-same', challenge=False)
    monkeypatch.setattr(vo, 'find', lambda route: state.override)
    monkeypatch.setattr(vo, 'apply', lambda guidance, route: (deepcopy(guidance), deepcopy(state.provenance)))
    def fetch(url, **kwargs):
        state.fetches.append(url)
        if url in state.failed or url not in state.texts:
            return FetchResult(requested_url=url, ok=False, error='fixture unavailable')
        final = state.redirect or url
        return FetchResult(requested_url=url, ok=True, final_url=final,
            final_hostname=urlsplit(final).hostname, content_text=state.texts[url],
            content_hash=state.raw_hash, retrieved_at=state.day, challenge=state.challenge)
    def compare(system, user, **kwargs):
        state.calls.append(json.loads(user))
        return state.answer(json.loads(user)) if callable(state.answer) else deepcopy(state.answer)
    monkeypatch.setattr(freshness, 'fetch', fetch)
    monkeypatch.setattr(freshness, '_call', compare)
    monkeypatch.setattr(freshness, '_PROVIDER', None)
    yield state
    db.close(); engine.dispose()


def seed(h, guidance=None, route=None):
    route = route or dict(ROUTE)
    row = KimiRouteGuidanceCache(cache_key=kimi_primary.cache_key(route), route=route,
        guidance=guidance or {'disposition': 'VISA_REQUIRED', 'source_url': URL},
        status='KIMI_PRIMARY', verification={})
    h.db.add(row); h.db.commit()
    return row


def run(h, row):
    return freshness.recheck_row(h.db, row, today=h.day)


def test_identical_fresh_reads_reuse_one_comparison_and_preserve_partial_coverage(h):
    row = seed(h, {'disposition': 'VISA_REQUIRED', 'source_url': URL,
                  'government_fee': {'amount': 999, 'currency': 'CAD'}})
    first = run(h, row)
    before = deepcopy(row.verification['grounded_check'])
    assert first['model_comparisons'] == 1
    h.day = '2026-09-09T15:00:00+00:00'
    second = run(h, row)
    after = row.verification['grounded_check']
    assert len(h.fetches) == 2 and len(h.calls) == 1
    assert second['model_comparisons'] == 0 and second['model_comparisons_reused'] == 1
    assert after['verified_fields'] == before['verified_fields'] == ['disposition']
    assert after['unverified_fields'] == ['government_fee']
    assert after['renewed'] is False and row.fresh_until is None
    check = after['source_checks'][0]
    assert check['comparison_reused'] is True
    assert check['model_compared_at'] == before['source_checks'][0]['model_compared_at']
    assert check['source_read_at'] == check['revalidated_at'] == h.day


@pytest.mark.parametrize('change', ['text', 'guidance', 'nationality', 'destination', 'document',
    'purpose', 'prompt', 'reviewed_proof', 'catalog', 'provenance', 'day', 'version', 'contract',
    'provider_model', 'comparison_transport', 'redirect', 'validation_code'])
def test_each_comparison_dependency_invalidates_reuse(h, monkeypatch, change):
    row = seed(h); run(h, row)
    assert len(h.calls) == 1
    if change == 'text': h.texts[URL] += ' New official visa notice.'  # raw hash deliberately identical
    elif change == 'guidance': row.guidance = dict(row.guidance, permitted_stay='30 days')
    elif change in {'nationality', 'destination', 'document', 'purpose'}:
        key, value = {'nationality': ('passport_nationality', 'USA'),
            'destination': ('destination_country', 'KOR'),
            'document': ('travel_document_type', 'diplomatic_passport'),
            'purpose': ('travel_purpose', 'work')}[change]
        row.route = dict(row.route, **{key: value})
        row.cache_key = kimi_primary.cache_key(row.route)
    elif change == 'prompt': monkeypatch.setattr(freshness, '_SYSTEM', freshness._SYSTEM + '\nRevised contract.')
    elif change == 'reviewed_proof': h.override = {'field_provenance': {'disposition': {'note': 'review changed'}}}
    elif change == 'catalog': h.override = {'field_provenance': {'disposition': {'source_id': 'new-id', 'source_url': URL}}}
    elif change == 'provenance': h.provenance = {'policy_valid_through': '2026-09-30'}
    elif change == 'day': h.day = '2026-09-10T09:00:00+00:00'
    elif change == 'version': monkeypatch.setattr(reuse, 'VERSION', reuse.VERSION + 1)
    elif change == 'contract': monkeypatch.setattr(freshness, 'EVIDENCE_CONTRACT', 3)
    elif change == 'provider_model': monkeypatch.setenv('KIMI_GUIDANCE_MODEL', 'different-model')
    elif change == 'comparison_transport':
        contract = kimi_primary.comparison_provider_contract()
        monkeypatch.setattr(kimi_primary, 'comparison_provider_contract',
            lambda: dict(contract, final_json_only=False, reasoning_effort=None))
    elif change == 'redirect': h.redirect = 'https://www.mofa.go.jp/new-visa'
    elif change == 'validation_code': monkeypatch.setattr(reuse, 'validator_fingerprint', lambda: 'new-validator-code')
    h.db.commit()
    run(h, row)
    # An unrelated new destination rejects this authority before model use.
    assert len(h.calls) == (1 if change == 'destination' else 2)
    assert row.verification['grounded_check'].get('model_comparisons_reused', 0) == 0


def test_full_extracted_tail_changes_even_when_model_prefix_and_raw_hash_match(h):
    h.texts[URL] = TEXT + ' ' * freshness.MAX_PAGE_CHARS + ' old tail'
    row = seed(h); run(h, row)
    h.texts[URL] = h.texts[URL].replace('old tail', 'new tail')
    run(h, row)
    assert len(h.calls) == 2
    assert h.calls[0]['official_page_text'] == h.calls[1]['official_page_text']


def test_raw_html_hash_change_without_extracted_change_still_fetches_and_reuses(h):
    row = seed(h); run(h, row)
    h.raw_hash = 'different-raw-html-nonce'
    run(h, row)
    assert len(h.fetches) == 2 and len(h.calls) == 1
    assert row.verification['grounded_check']['content_hash'] == h.raw_hash


@pytest.mark.parametrize('failure', ['unreadable', 'challenge', 'untrusted_redirect'])
def test_unreadable_page_never_turns_cached_extraction_into_a_new_check(h, failure):
    row = seed(h); run(h, row)
    before = deepcopy(row.verification['grounded_check']); deadline = row.fresh_until
    h.day = '2026-09-09T15:00:00+00:00'
    if failure == 'unreadable': h.failed.add(URL)
    elif failure == 'challenge': h.challenge = True
    else: h.redirect = 'https://unrelated.example/visa'
    result = run(h, row)
    assert result['outcome'] == 'fetch_failed'
    assert result['model_comparisons_reused'] == 0 and len(h.calls) == 1
    assert row.fresh_until == deadline
    assert freshness.effective_check(row.verification)['at'] == before['at']


def test_new_dispute_blocks_renewal_after_identical_successful_reread(h):
    row = seed(h); run(h, row)
    row.fresh_until = datetime.now(timezone.utc) - timedelta(days=1)
    h.db.add(DatabaseIssueReport(org_id='tripcom', cache_key=row.cache_key, route=row.route,
        field='disposition', status='open', reported_by='freshness_monitor', note='Evidence under review'))
    h.db.commit(); h.db.refresh(row); deadline = row.fresh_until
    run(h, row)
    assert len(h.calls) == 1
    assert row.verification['grounded_check']['renewed'] is False
    assert freshness.active_disputed_fields(h.db, row.cache_key) == ['disposition']
    assert row.fresh_until == deadline


def test_cached_route_proof_cannot_be_copied_to_another_nationalitys_fee(h):
    quote = 'Canadian citizens pay CAD100 for a tourist visa.'
    h.texts[URL] += ' ' + quote
    h.answer['evidence'] = {'government_fee': quote}
    first = seed(h, {'disposition': 'VISA_REQUIRED', 'source_url': URL,
                    'government_fee': {'amount': 100, 'currency': 'CAD'}})
    run(h, first)
    second = seed(h, deepcopy(first.guidance), dict(ROUTE, passport_nationality='USA'))
    second.verification = deepcopy(first.verification); h.db.commit()
    result = run(h, second)
    assert len(h.calls) == 2 and result['outcome'] == 'page_not_relevant'
    assert result['model_comparisons_reused'] == 0
    assert second.fresh_until is None


@pytest.mark.parametrize('bad', ['proposal', 'unquoted', 'malformed', 'inconsistent', 'malformed_fields', 'provider_error'])
def test_unsuccessful_extraction_never_seeds_reuse(h, bad):
    row = seed(h)
    if bad in {'proposal', 'unquoted'}:
        h.answer['corrected_fields'] = {'permitted_stay': '30 days'}
        h.answer['evidence'] = {'permitted_stay': '30 days'} if bad == 'proposal' else {}
        if bad == 'proposal': h.texts[URL] += ' Tourist visas allow a stay of 30 days.'
    elif bad == 'malformed': h.answer = []
    elif bad == 'malformed_fields': h.answer['corrected_fields'] = []
    elif bad == 'provider_error': h.answer = lambda _: (_ for _ in ()).throw(RuntimeError('fixture provider error'))
    else: h.answer['consistent'] = False
    run(h, row); run(h, row)
    assert len(h.calls) == 2
    assert not (row.verification.get('comparison_cache') or {}).get('entries')


def test_actual_canadian_route_and_fee_both_reuse_only_after_current_reads(h):
    data = json.loads((ROOT / 'data/database_seed/reviewed_us_can12_coverage_2026_09_09.json').read_text())
    entry = next(e for e in data['routes'] if e['route']['nationality'] == 'GBR' and e['route']['destination'] == 'CAN')
    fields = entry['field_provenance']; fee = fields['government_fee']
    h.texts = {s['url']: s['text'] for s in data['sources']}
    h.override = {'source_url': entry['guidance']['source_url'], 'fields': {}, 'field_provenance': fields}
    h.answer = lambda payload: {'consistent': True, 'page_relevant': True, 'page_is_nationality_specific': False,
        'corrected_fields': {},
        'evidence': {'government_fee': fee['quote']} if payload['official_page_url'] == fee['source_url'] else {}}
    row = seed(h, entry['guidance'], dict(ROUTE, passport_nationality='GBR', destination_country='CAN'))
    run(h, row); calls = len(h.calls); reads = len(h.fetches)
    original_fields = row.verification['grounded_check']['verified_fields']
    result = run(h, row)
    assert {'disposition', 'government_fee'} <= set(row.verification['grounded_check']['verified_fields'])
    assert len(h.fetches) == 2 * reads and len(h.calls) < 2 * calls
    assert result['model_comparisons_reused'] >= 2
    assert row.verification['grounded_check']['verified_fields'] == original_fields
    assert row.verification['grounded_check']['renewed'] is False
    h.failed.add(fields['disposition']['source_url'])
    result = run(h, row)
    assert result['outcome'] != 'checked'
    assert result['model_comparisons_reused'] == 0


@pytest.mark.parametrize('manifest,program', [
    ('reviewed_phl_coverage_2026_09_09.json', 'source_table'),
    ('reviewed_rus_aus_idn15_coverage_2026_09_09.json', 'australia_evisitor_member')])
def test_actual_structured_proof_roundtrip_retains_fields_and_all_companion_scopes(h, manifest, program):
    data = json.loads((ROOT / 'data/database_seed' / manifest).read_text())
    entry = next(e for e in data['routes'] if (
        'source_table' in e['field_provenance']['disposition'] if program == 'source_table' else
        e['field_provenance']['disposition'].get('source_closed_list', {}).get('program') == program))
    h.texts = {s['url']: s['text'] for s in data['sources']}
    h.override = {'source_url': entry['guidance']['source_url'], 'fields': {}, 'field_provenance': entry['field_provenance']}
    h.answer['page_is_nationality_specific'] = False
    row = seed(h, entry['guidance'], dict(ROUTE, passport_nationality=entry['route']['nationality'],
        destination_country=entry['route']['destination']))
    assert run(h, row)['outcome'] == 'checked'
    before = deepcopy(row.verification['grounded_check']); count = len(h.calls)
    assert row.verification['comparison_cache']['entries']
    result = run(h, row); after = row.verification['grounded_check']
    assert result['model_comparisons_reused'] >= 1 and len(h.calls) < 2 * count
    assert after['verified_fields'] == before['verified_fields']
    assert after['unverified_fields'] == before['unverified_fields']
    assert after['field_sources']['disposition']['scope_quotes'] == before['field_sources']['disposition']['scope_quotes']


@pytest.mark.parametrize('change', ['future', 'missing'])
def test_unchanged_primary_cannot_reuse_after_companion_becomes_invalid(h, change):
    data = json.loads((ROOT / 'data/database_seed/reviewed_rus_aus_idn15_coverage_2026_09_09.json').read_text())
    entry = next(e for e in data['routes'] if e['route']['destination'] == 'AUS' and
        e['field_provenance']['disposition'].get('source_closed_list', {}).get('program') == 'australia_evisitor_member')
    proof = entry['field_provenance']['disposition']; rule = proof['source_closed_list']
    sources = {s['id']: s for s in data['sources']}
    companion = sources[rule['source_id']]['url']
    h.texts = {s['url']: s['text'] for s in data['sources']}
    h.override = {'source_url': entry['guidance']['source_url'], 'fields': {}, 'field_provenance': entry['field_provenance']}
    h.answer['page_is_nationality_specific'] = False
    row = seed(h, entry['guidance'], dict(ROUTE, passport_nationality=entry['route']['nationality'], destination_country='AUS'))
    assert run(h, row)['outcome'] == 'checked'
    assert row.verification['comparison_cache']['entries']
    if change == 'future':
        h.texts[companion] = h.texts[companion].replace(rule['heading_quote'],
            'Effective from 1 October 2026.\n' + rule['heading_quote'])
    else: h.failed.add(companion)
    result = run(h, row)
    assert result['outcome'] != 'checked'
    assert result['model_comparisons_reused'] == 0


def test_snapshot_is_bounded_and_contains_no_prompts_or_provider_extras(h):
    h.answer.update({'note': 'private-provider-note', 'secret': 'private-response-extra'})
    row = seed(h); run(h, row)
    cache = row.verification['comparison_cache']
    encoded = json.dumps(cache)
    assert freshness._SYSTEM not in encoded
    assert 'private-provider-note' not in encoded and 'private-response-extra' not in encoded
    additions = []
    for i in range(20):
        entry = deepcopy(cache['entries'][0]); entry['source_url'] = URL + str(i)
        entry['response']['evidence'] = {'entry_requirements': 'x' * 10_000}
        entry['response_digest'] = reuse.digest(entry['response'])
        additions.append(entry)
    bounded = reuse.merge({}, additions)
    assert len(bounded['entries']) <= reuse.MAX_ENTRIES
    assert len(reuse._json(bounded).encode()) <= reuse.MAX_CACHE_BYTES
    huge = deepcopy(additions[0]); huge['response']['evidence'] = {'x': 'x' * reuse.MAX_ENTRY_BYTES}
    huge['response_digest'] = reuse.digest(huge['response'])
    assert reuse.merge({}, [huge])['entries'] == []


def test_comparison_cache_is_committed_through_existing_cas_only(h, monkeypatch):
    row = seed(h)
    original = deepcopy(row.verification)
    def reject(db, row, entry, **kwargs):
        assert kwargs['comparison_cache']['entries']
        db.rollback()
        return False
    monkeypatch.setattr(freshness, '_commit_recheck', reject)
    assert run(h, row)['outcome'] == 'concurrent_change'
    h.db.refresh(row)
    assert row.verification == original and row.fresh_until is None


@pytest.mark.parametrize('corruption', ['signature', 'response', 'timestamp', 'version', 'oversized'])
def test_malformed_or_legacy_snapshot_cannot_skip_a_comparison(h, corruption):
    row = seed(h); run(h, row)
    metadata = deepcopy(row.verification); cache = metadata['comparison_cache']; entry = cache['entries'][0]
    if corruption == 'signature': entry['signature'] = 'bad-digest'
    elif corruption == 'response': entry['response']['evidence'] = []
    elif corruption == 'timestamp': entry['model_compared_at'] = '2099-09-09T00:00:00+00:00'
    elif corruption == 'version': cache['version'] = 0
    else: entry['response']['evidence']['disposition'] = 'x' * reuse.MAX_CACHE_BYTES
    row.verification = metadata; h.db.commit()
    run(h, row)
    assert len(h.calls) == 2
