"""A current source can resolve only its exact old conditions and pending revision."""
from copy import deepcopy
from datetime import date, datetime
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from app.models import AuditEvent

from app.visa_snapshot import kimi_primary as kp, tstation, verified_overrides as vo
from app.visa_snapshot import reviewed_condition_resolution as resolution
from app.visa_snapshot.models import KimiRouteGuidanceCache, DatabaseIssueReport
from app.visa_snapshot.records_guard import apply_records_hold
from scripts.convert_reviewed_kor_chn_conditions import (
    KEY, MANIFEST, OVERLAY, VALUES, FIELDS, OLD_UNCERTAINTY, METADATA_DATES,
    build_manifest, convert, pending_metadata)
from scripts.convert_reviewed_product_validity import BASELINE_KEYS
from scripts.prepare_reviewed_product_patch import PatchRejected, digest

ROOT = Path(__file__).resolve().parents[2] / 'data/database_seed'


def artifacts():
    manifest = json.loads((ROOT / MANIFEST).read_text())
    overlay = json.loads((ROOT / OVERLAY).read_text())
    baseline = dict(deepcopy(manifest['baseline']), cache_key=KEY)
    return manifest, overlay, baseline


@pytest.fixture
def unresolved(tmp_path, monkeypatch):
    manifest, overlay, baseline = artifacts()
    root = tmp_path / 'seed'; root.mkdir()
    for name, content in [(MANIFEST, manifest), (OVERLAY, overlay),
                          ('verified_overrides.json', baseline['seed_entries'])]:
        (root / name).write_text(json.dumps(content))
    monkeypatch.setattr(vo, 'OVERRIDES', root / 'verified_overrides.json')
    monkeypatch.setattr(vo, '_reviewed_overlay_paths', lambda: [root / OVERLAY])
    monkeypatch.setattr(vo, 'operator_overrides_path', lambda: root / 'operators.json')
    monkeypatch.setattr(vo, '_CACHE', {'mtime': None, 'table': None})
    monkeypatch.setattr(resolution, '_today', lambda: date(2026, 9, 10))
    engine = create_engine('sqlite:///' + str(tmp_path / 'cache.db'))
    KimiRouteGuidanceCache.__table__.create(engine)
    DatabaseIssueReport.__table__.create(engine)
    AuditEvent.__table__.create(engine)
    monkeypatch.setattr(resolution, '_session', lambda: Session(engine))
    with Session(engine) as db:
        metadata = deepcopy(manifest['specification']['pending_baseline'])
        for key in METADATA_DATES:
            metadata[key] = datetime.fromisoformat(metadata[key])
        row = KimiRouteGuidanceCache(**metadata, route=deepcopy(baseline['route']),
                                     guidance=deepcopy(baseline['raw_guidance']))
        db.add(row); db.commit()
        yield manifest, overlay, baseline, root, db, row
    engine.dispose()


def resolve(fixture, **extra):
    _, _, baseline, _, db, _ = fixture
    hashes = resolution._current(baseline['route'])[3]
    return resolution.resolve_pending(db, **hashes, release_id=extra.pop('release_id', 'publication20260910h'), **extra)


@pytest.fixture
def installed(unresolved):
    resolve(unresolved)
    return unresolved


def reader(baseline, raw=None):
    out = kp.apply_verified_overrides(kp._result(
        'KIMI_PRIMARY', deepcopy(raw if raw is not None else baseline['raw_guidance']),
        cached=True, stale=False), baseline['route'])
    out.update(detail_pending=False, grounded_check={})
    return out


def test_six_conditions_only_preserve_all_review_ownership():
    m, overlay, baseline = artifacts()
    before = deepcopy(baseline)
    rebuilt, report, g, provenance = convert(m, [baseline])
    assert rebuilt == overlay and baseline == before
    assert {k for k in set(g) | set(before['merged_guidance'])
            if g.get(k) != before['merged_guidance'].get(k)} == FIELDS
    assert all(g[k] == value for k, value in VALUES.items())
    assert g['uncertainty'] == OLD_UNCERTAINTY
    assert g['insurance_required'] is None
    assert provenance['field_provenance']['insurance_required']['status'] == 'unknown'
    assert not provenance['field_provenance']['insurance_required']['verified_at']
    assert not g.get('unpublished_fields')
    for key, old in before['source_provenance'].items():
        if key not in {'fields', 'field_provenance'}:
            assert provenance[key] == old
    for key, old in before['source_provenance']['field_provenance'].items():
        if key not in FIELDS:
            assert provenance['field_provenance'][key] == old
    assert report['renew_fresh_until'] is False
    assert report['new_verdict_grade'] is False


@pytest.mark.parametrize('field', BASELINE_KEYS)
def test_every_live_layer_must_match_exactly(field):
    m, _, baseline = artifacts()
    baseline[field] = [] if isinstance(baseline[field], list) else dict(baseline[field], unexpected='new revision')
    # The initially empty operator list must also fail once anything appears.
    if field == 'operator_entries':
        baseline[field] = [{'unexpected': 'new operator revision'}]
    with pytest.raises(PatchRejected):
        convert(m, [baseline])


@pytest.mark.parametrize('case', ['source_url', 'nationality', 'expiry', 'subject',
    'missing_condition', 'insurance_np', 'insurance_false', 'extra_field',
    'new_warning', 'metadata_hash', 'no_pending_revision'])
def test_changed_source_scope_or_adjudication_is_rejected(case):
    m, _, baseline = artifacts(); spec = deepcopy(m['specification'])
    if case == 'source_url': spec['sources'][0]['url'] = 'https://www.homeaffairs.gov.au/'
    elif case == 'nationality': spec['nationality_quote'] = 'Korea ordinary tourists'
    elif case == 'expiry': spec['policy_effective_to'] = '2027-12-31'
    elif case == 'subject': spec['changes'][0]['proof']['subject']['nationality'] = 'USA'
    elif case == 'missing_condition': spec['changes'][0]['proof']['evidence'].pop()
    elif case.startswith('insurance'):
        c = next(c for c in spec['changes'] if c['field'] == 'insurance_required')
        if case == 'insurance_np': c['proof']['status'] = 'not_published'
        else: c['new'] = False
    elif case == 'extra_field': spec['changes'].append({'field': 'government_fee', 'new': {'amount': 999}})
    elif case == 'new_warning': spec['resolved_uncertainty'].append({'field': 'entry_requirements', 'reason': 'New doubt'})
    elif case == 'metadata_hash': spec['pending_baseline_sha256'] = '0' * 64
    elif case == 'no_pending_revision':
        spec['pending_baseline']['verification'].pop('detail_pending')
        spec['pending_baseline_sha256'] = digest(spec['pending_baseline'])
    with pytest.raises((PatchRejected, KeyError)):
        build_manifest(spec, [baseline])


def test_current_canonical_reader_qc_and_guard_agree_without_data_writes(installed):
    m, overlay, baseline, root, db, row = installed
    before = pending_metadata(row); raw = deepcopy(row.guidance)
    out = reader(baseline)
    assert out['guidance']['uncertainty'] == []
    result = apply_records_hold(baseline['route'], out, db)
    assert not result['held'] and not result['review_required'] and not result['detail_pending']
    assert result['guidance']['disposition'] == 'VISA_EXEMPT'
    assert result['guidance']['permitted_stay_days'] == 30
    assert result['guidance']['passport_validity_requirement'] == {'kind': 'valid_through_departure', 'months': 0}
    assert 'Chinese embassies or consulates' in result['guidance']['application_channel_detail']
    assert 'examination and approval' in result['guidance']['entry_requirements']
    qc = tstation.records_for_route(baseline['route'], result['guidance'], result['source_verified'])
    assert len(qc) == 1 and not qc[0]['_evidence_low']
    assert 'entire intended stay' in qc[0]['entry_requirements']
    assert pending_metadata(row) == before and row.guidance == raw
    assert not db.new and not db.dirty and not db.deleted


def test_real_cached_lookup_uses_same_correction_without_refresh(installed, monkeypatch):
    _, _, baseline, _, db, row = installed
    before = pending_metadata(row)
    def no_model(*args, **kwargs):
        raise AssertionError('A reviewed cache correction must not invoke a model')
    monkeypatch.setattr(kp, '_call', no_model)
    monkeypatch.setattr(kp, 'join_detail_stage', lambda **kwargs: None)
    result = apply_records_hold(baseline['route'], kp.get_route_guidance(db, baseline['route']), db)
    assert result['cached'] and not result['stale']
    assert not result['held'] and not result['detail_pending'], result
    assert result['guidance']['uncertainty'] == []
    assert pending_metadata(row) == before


@pytest.mark.parametrize('mutation', ['added', 'edited', 'reordered', 'removed'])
def test_new_or_different_uncertainty_cannot_borrow_old_resolution(installed, mutation):
    _, _, baseline, _, db, row = installed
    raw = deepcopy(baseline['raw_guidance'])
    if mutation == 'added': raw['uncertainty'].append({'field': 'insurance_required', 'reason': 'New concern'})
    elif mutation == 'edited': raw['uncertainty'][0]['reason'] += ' New information'
    elif mutation == 'reordered': raw['uncertainty'].reverse()
    else: raw['uncertainty'] = []
    row.guidance = deepcopy(raw)
    db.commit()
    out = reader(baseline, raw)
    result = apply_records_hold(baseline['route'], out, db)
    assert result['held'] and result['detail_pending']


@pytest.mark.parametrize('field', ['verification', 'generated_at',
    'model', 'status', 'missing_fields', 'contradictions', 'guidance', 'route'])
def test_new_cache_revision_cannot_clear_its_pending_marker(installed, field):
    _, _, baseline, _, db, row = installed
    if field in METADATA_DATES: setattr(row, field, datetime(2026, 9, 11))
    elif field == 'verification': row.verification = dict(row.verification, new_pending_reason='Fresh extraction requested')
    elif field == 'guidance': row.guidance = dict(row.guidance, insurance_required=True)
    elif field == 'route': row.route = dict(row.route, travel_document_type='diplomatic_passport')
    elif field in ('missing_fields', 'contradictions'): setattr(row, field, ['New condition problem'])
    else: setattr(row, field, 'Changed')
    db.commit()
    out = reader(baseline)
    result = apply_records_hold(baseline['route'], out, db)
    assert result['held'] and result['detail_pending']


@pytest.mark.parametrize('hold', ['stale', 'held', 'review_required', 'missing', 'contradiction',
                                'grounded_dispute', 'active_dispute', 'no_database'])
def test_independent_gates_are_never_bypassed(installed, monkeypatch, hold):
    _, _, baseline, _, db, _ = installed
    out = reader(baseline)
    if hold in ('stale', 'held', 'review_required'): out[hold] = True
    elif hold == 'missing': out['missing_fields'] = ['some_required_condition']
    elif hold == 'contradiction': out['contradictions'] = ['Conflicting stay']
    elif hold == 'grounded_dispute': out['grounded_check'] = {'disputed_fields': ['passport_validity']}
    elif hold == 'active_dispute':
        from app.visa_snapshot import freshness
        monkeypatch.setattr(freshness, 'active_disputed_fields', lambda *args: ['passport_validity'])
    elif hold == 'no_database':
        monkeypatch.setattr(resolution, '_session', lambda: (_ for _ in ()).throw(SQLAlchemyError('Unavailable')))
        out = reader(baseline)
        db = None
    result = apply_records_hold(baseline['route'], out, db)
    assert result['held']


@pytest.mark.parametrize('change', ['overlay', 'manifest', 'unregistered', 'expired', 'future'])
def test_missing_changed_or_outdated_review_does_not_clear_warnings(installed, monkeypatch, change):
    m, overlay, baseline, root, db, _ = installed
    if change == 'overlay':
        overlay['entries'][0]['fields']['insurance_required'] = False
        (root / OVERLAY).write_text(json.dumps(overlay))
    elif change == 'manifest':
        m['specification']['policy_effective_to'] = '2028-01-01'
        (root / MANIFEST).write_text(json.dumps(m))
    elif change == 'unregistered': monkeypatch.setattr(vo, '_reviewed_overlay_paths', lambda: [])
    elif change == 'expired': monkeypatch.setattr(resolution, '_today', lambda: date(2027, 1, 1))
    elif change == 'future': monkeypatch.setattr(resolution, '_today', lambda: date(2026, 9, 9))
    result = apply_records_hold(baseline['route'], reader(baseline), db)
    assert result['held'] and result['detail_pending']
    assert result['guidance']['uncertainty'] == OLD_UNCERTAINTY


@pytest.mark.parametrize('field,value', [('travel_document_type', 'diplomatic_passport'),
    ('passport_nationality', 'USA'), ('travel_purpose', 'business'),
    ('arrival_date', '2026-10-01'), ('transit_countries', ['HKG'])])
def test_other_subject_or_context_does_not_receive_warning_adjudication(installed, field, value):
    _, _, baseline, _, db, _ = installed
    route = dict(baseline['route'], **{field: value})
    assert resolution._current(route) is None
