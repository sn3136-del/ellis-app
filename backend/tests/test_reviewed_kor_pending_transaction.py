"""Persisted pending adjudication: atomic exact CAS, no redating or stalled reads."""
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest
from sqlalchemy import select, update
from sqlalchemy.sql.dml import Update
from sqlalchemy.orm import Session

from app.models import AuditEvent
from app.visa_snapshot import freshness, kimi_primary as kp, change_log
from app.visa_snapshot import reviewed_condition_resolution as r
from app.visa_snapshot.models import KimiRouteGuidanceCache, DatabaseIssueReport
from app.visa_snapshot.records_guard import apply_records_hold
from scripts.convert_reviewed_kor_chn_conditions import METADATA_FIELDS, pending_metadata, OVERLAY
from tests.test_reviewed_kor_chn_conditions import unresolved, installed, resolve, reader


def snapshot(row):
    return deepcopy({k: getattr(row, k) for k in (*METADATA_FIELDS, 'route', 'guidance')})


def test_installation_alone_does_not_clear_pending_or_uncertainty(unresolved):
    _, _, b, _, db, _ = unresolved
    out = reader(b)
    assert out['guidance']['uncertainty'] == b['raw_guidance']['uncertainty']
    out = apply_records_hold(b['route'], out, db)
    assert out['held'] and out['detail_pending']


def test_resolve_only_pending_and_receipt_with_same_atomic_audit(unresolved, monkeypatch):
    _, _, b, _, db, row = unresolved
    before = snapshot(row)
    monkeypatch.setattr(change_log, '_notify', lambda *a, **kw: pytest.fail('No external notification'))
    result = resolve(unresolved)
    after = snapshot(row)
    assert result['created'] and result['actor'] == 'CodexAI'
    assert {k for k in before if before[k] != after[k]} == {'verification', 'updated_at'}
    assert {k: v for k, v in row.verification.items() if k not in (r.RECEIPT, 'detail_pending')} == {
        k: v for k, v in before['verification'].items() if k != 'detail_pending'}
    assert row.verification['detail_pending'] is False
    ev = db.get(AuditEvent, result['audit_id'])
    assert ev.detail['changes'] == {'verification.detail_pending': {'from': True, 'to': False}}
    assert ev.detail['receipt'] == row.verification[r.RECEIPT]
    assert ev.action == r.ACTION and ev.actor == 'CodexAI'
    assert row.guidance == b['raw_guidance']
    assert not apply_records_hold(b['route'], reader(b), db)['held']


def test_idempotence_keeps_first_audit_and_dates_even_with_renamed_release(installed):
    _, _, _, _, db, row = installed
    before = snapshot(row); events = list(db.scalars(select(AuditEvent)))
    first = events[0]
    again = resolve(installed, release_id='renamed-but-same-correction')
    assert not again['created'] and again['audit_id'] == first.id
    assert snapshot(row) == before
    assert list(db.scalars(select(AuditEvent))) == events


@pytest.mark.parametrize('update_kind', ['failed_fetch', 'provider_error', 'validation_error', 'successful_read', 'comparison_cache'])
def test_benign_read_bookkeeping_retains_resolution_without_new_receipt(installed, update_kind):
    _, _, b, _, db, row = installed
    before = snapshot(row); audit_id = row.verification[r.RECEIPT]['audit_id']
    if update_kind == 'comparison_cache':
        row.verification = dict(row.verification, comparison_cache={'entries': []})
    else:
        outcome = {'failed_fetch': 'fetch_failed', 'provider_error': 'provider_error',
                   'validation_error': 'validation_error', 'successful_read': 'checked'}[update_kind]
        freshness._stamp(row, {'at': '2026-09-10T07:00:00+00:00', 'outcome': outcome,
            'consistent': outcome == 'checked', 'source_reads': 1, 'disputed_fields': [],
            'evidence_contract': freshness.EVIDENCE_CONTRACT})
    db.commit()
    out = apply_records_hold(b['route'], reader(b), db)
    assert not out['held'] and not out['detail_pending']
    assert out['guidance']['uncertainty'] == []
    assert row.guidance == before['guidance'] and row.fresh_until == before['fresh_until']
    assert row.verification[r.RECEIPT]['audit_id'] == audit_id
    assert len(list(db.scalars(select(AuditEvent)))) == 1


def test_real_failed_recheck_is_no_longer_deferred_and_does_not_rehold(installed, monkeypatch):
    _, _, b, _, db, row = installed
    before = snapshot(row)
    # No live source/model calls. Reach the real commit path after its pending
    # gate with a deterministic missing-source outcome.
    monkeypatch.setattr(freshness, 'candidate_sources', lambda *args, **kwargs: [])
    result = freshness.recheck_row(db, row)
    assert result['outcome'] == 'no_official_source'
    assert row.verification['grounded_check']['outcome'] == 'no_official_source'
    assert row.verification['detail_pending'] is False
    assert not apply_records_hold(b['route'], reader(b), db)['held']
    assert row.guidance == before['guidance'] and row.fresh_until == before['fresh_until']


@pytest.mark.parametrize('mutation', ['new_generation', 'new_raw', 'new_pending', 'extra_pending_key',
    'removed_receipt', 'audit_actor', 'audit_deleted', 'audit_predates_baseline', 'audit_changed_diff', 'receipt_hash'])
def test_later_substantive_state_cannot_borrow_previous_resolution(installed, mutation):
    _, _, b, _, db, row = installed
    ev = db.get(AuditEvent, row.verification[r.RECEIPT]['audit_id'])
    if mutation == 'new_generation': row.generated_at = datetime(2026, 9, 10, 7, 0)
    elif mutation == 'new_raw': row.guidance = dict(row.guidance, insurance_required=True)
    elif mutation == 'new_pending': row.verification = dict(row.verification, detail_pending=True)
    elif mutation == 'extra_pending_key': row.verification = dict(row.verification, pending_reason='New independent extraction')
    elif mutation == 'removed_receipt': row.verification = {k: v for k, v in row.verification.items() if k != r.RECEIPT}
    elif mutation == 'audit_actor': ev.actor = 'human'
    elif mutation == 'audit_deleted': db.delete(ev)
    elif mutation == 'audit_predates_baseline': ev.at = datetime(2026, 9, 1)
    elif mutation == 'audit_changed_diff': ev.detail = dict(ev.detail, changes={})
    elif mutation == 'receipt_hash':
        row.verification = dict(row.verification, **{r.RECEIPT: dict(row.verification[r.RECEIPT], manifest_sha256='0'*64)})
    db.commit()
    out = apply_records_hold(b['route'], reader(b, row.guidance), db)
    assert out['held'] and out['detail_pending']
    with pytest.raises(r.ResolutionRejected): resolve(installed)


@pytest.mark.parametrize('mutation', ['metadata', 'overlay', 'seed', 'operator', 'dispute', 'pending_write'])
def test_first_resolution_rejects_changed_installation_or_active_work(unresolved, mutation, monkeypatch):
    _, _, b, root, db, row = unresolved
    if mutation == 'metadata': row.verification = dict(row.verification, another_attempt=True); db.commit()
    elif mutation == 'overlay':
        path = root / OVERLAY; data = json.loads(path.read_text())
        data['entries'][0]['fields']['insurance_required'] = False; path.write_text(json.dumps(data))
    elif mutation == 'seed':
        path = root / 'verified_overrides.json'; data = json.loads(path.read_text()); data[0]['note'] = 'Changed'; path.write_text(json.dumps(data))
    elif mutation == 'operator': (root / 'operators.json').write_text(json.dumps(b['seed_entries']))
    elif mutation == 'dispute':
        db.add(DatabaseIssueReport(cache_key=row.cache_key, route=row.route, status='open',
                                   field='passport_validity', note='New conflict', reported_by='tester'))
        db.commit()
    elif mutation == 'pending_write': row.model = 'unrelated unsaved change'
    with pytest.raises((r.ResolutionRejected, TypeError)):
        resolve(unresolved)
    db.rollback()
    assert db.get(KimiRouteGuidanceCache, row.id).verification['detail_pending'] is True
    assert not list(db.scalars(select(AuditEvent)))


def test_audit_failure_rolls_back_pending_change_and_receipt(unresolved, monkeypatch):
    _, _, _, _, db, row = unresolved
    before = snapshot(row)
    def fail(*args, **kwargs): raise RuntimeError('Simulated audit persistence failure')
    monkeypatch.setattr(r, '_append_audit', fail)
    with pytest.raises(RuntimeError): resolve(unresolved)
    db.refresh(row)
    assert snapshot(row) == before
    assert not list(db.scalars(select(AuditEvent)))


@pytest.mark.parametrize('race', ['verification', 'raw', 'new_issue'])
def test_concurrent_write_between_validation_and_update_is_not_overwritten(unresolved, monkeypatch, race):
    _, _, b, _, db, row = unresolved
    execute = db.execute; fired = False
    def intercept(statement, *args, **kwargs):
        nonlocal fired
        if isinstance(statement, Update) and not fired:
            fired = True
            with Session(db.get_bind()) as other:
                current = other.get(KimiRouteGuidanceCache, row.id)
                if race == 'verification': current.verification = dict(current.verification, concurrent_pending=True)
                elif race == 'raw': current.guidance = dict(current.guidance, passport_validity='New source value')
                else: other.add(DatabaseIssueReport(cache_key=row.cache_key, route=row.route, status='open',
                    field='passport_validity', note='New concurrent conflict', reported_by='tester'))
                other.commit()
        return execute(statement, *args, **kwargs)
    monkeypatch.setattr(db, 'execute', intercept)
    with pytest.raises(r.ResolutionRejected): resolve(unresolved)
    db.refresh(row)
    assert fired and row.verification['detail_pending'] is True
    assert r.RECEIPT not in row.verification and not list(db.scalars(select(AuditEvent)))
    if race == 'verification': assert row.verification['concurrent_pending']
    elif race == 'raw': assert row.guidance['passport_validity'] == 'New source value'
    else: assert len(list(db.scalars(select(DatabaseIssueReport)))) == 1


@pytest.mark.parametrize('mutation', ['overlay', 'manifest', 'seed', 'operator'])
def test_installed_file_change_after_cache_cas_rolls_back_adjudication(unresolved, monkeypatch, mutation):
    from scripts.convert_reviewed_kor_chn_conditions import MANIFEST
    _, _, b, root, db, row = unresolved
    before = snapshot(row); execute = db.execute; fired = False
    def intercept(statement, *args, **kwargs):
        nonlocal fired
        result = execute(statement, *args, **kwargs)
        if isinstance(statement, Update) and not fired:
            fired = True
            if mutation == 'operator': (root / 'operators.json').write_text(json.dumps(b['seed_entries']))
            elif mutation == 'seed':
                p = root / 'verified_overrides.json'; data = json.loads(p.read_text()); data[0]['note'] = 'Concurrent evidence edit'; p.write_text(json.dumps(data))
            else:
                p = root / (OVERLAY if mutation == 'overlay' else MANIFEST)
                data = json.loads(p.read_text()); data['unexpected'] = 'Concurrent installed file edit'; p.write_text(json.dumps(data))
        return result
    monkeypatch.setattr(db, 'execute', intercept)
    with pytest.raises(r.ResolutionRejected): resolve(unresolved)
    db.refresh(row)
    assert fired and snapshot(row) == before
    assert not list(db.scalars(select(AuditEvent)))


def test_failed_deployment_rollback_restores_only_owned_metadata_and_logs_once(installed):
    _, _, b, root, db, row = installed
    resolution_id = row.verification[r.RECEIPT]['audit_id']
    original = deepcopy(db.get(AuditEvent, resolution_id).detail)
    freshness._stamp(row, {'at': '2026-09-10T07:00:00Z', 'outcome': 'fetch_failed'})
    db.commit(); before = snapshot(row)
    # Even an invalid installed file cannot stop restoration of a proven hold.
    (root / OVERLAY).write_text('invalid deployment artifact')
    result = r.rollback_pending(db, audit_id=resolution_id, release_id='publication20260910h')
    assert result['created'] and row.verification['detail_pending'] is True
    assert r.RECEIPT not in row.verification
    assert {k: v for k, v in row.verification.items() if k != 'detail_pending'} == {
        k: v for k, v in before['verification'].items() if k not in (r.RECEIPT, 'detail_pending')}
    assert {k for k in before if before[k] != snapshot(row)[k]} == {'verification', 'updated_at'}
    assert db.get(AuditEvent, resolution_id).detail == original
    event = db.get(AuditEvent, result['audit_id']); at = event.at
    assert event.action == r.ROLLBACK_ACTION and event.actor == 'CodexAI'
    assert event.detail['resolution_audit_id'] == resolution_id
    assert apply_records_hold(b['route'], reader(b), db)['held']
    again = r.rollback_pending(db, audit_id=resolution_id, release_id='renamed-recovery')
    assert not again['created'] and again['audit_id'] == event.id and event.at == at
    assert len(list(db.scalars(select(AuditEvent)))) == 2


def test_failed_rollback_audit_keeps_resolution_intact(installed, monkeypatch):
    _, _, _, _, db, row = installed
    before = snapshot(row); original_id = row.verification[r.RECEIPT]['audit_id']
    monkeypatch.setattr(r, '_append_audit', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('Audit unavailable')))
    with pytest.raises(RuntimeError): r.rollback_pending(db, audit_id=original_id, release_id='failed-h')
    db.refresh(row)
    assert snapshot(row) == before
    assert len(list(db.scalars(select(AuditEvent)))) == 1


@pytest.mark.parametrize('mutation', ['new_raw', 'new_generation', 'new_pending', 'different_receipt', 'wrong_actor'])
def test_rollback_cannot_overwrite_newer_or_unowned_state(installed, mutation):
    _, _, _, _, db, row = installed
    original_id = row.verification[r.RECEIPT]['audit_id']
    if mutation == 'new_raw': row.guidance = dict(row.guidance, insurance_required=True)
    elif mutation == 'new_generation': row.generated_at = datetime(2026, 9, 10, 9)
    elif mutation == 'new_pending': row.verification = dict(row.verification, detail_pending=True)
    elif mutation == 'different_receipt': row.verification = dict(row.verification, **{r.RECEIPT: {'audit_id': 'another'}})
    elif mutation == 'wrong_actor': db.get(AuditEvent, original_id).actor = 'pretend operator'
    db.commit(); before = snapshot(row)
    with pytest.raises(r.ResolutionRejected): r.rollback_pending(db, audit_id=original_id, release_id='failed-h')
    db.refresh(row)
    assert snapshot(row) == before and len(list(db.scalars(select(AuditEvent)))) == 1


def test_rollback_race_preserves_concurrent_source_bookkeeping(installed, monkeypatch):
    _, _, _, _, db, row = installed
    original_id = row.verification[r.RECEIPT]['audit_id']; execute = db.execute; fired = False
    def intercept(statement, *args, **kwargs):
        nonlocal fired
        if isinstance(statement, Update) and not fired:
            fired = True
            with Session(db.get_bind()) as other:
                current = other.get(KimiRouteGuidanceCache, row.id)
                current.verification = dict(current.verification, comparison_cache={'newer': True})
                other.commit()
        return execute(statement, *args, **kwargs)
    monkeypatch.setattr(db, 'execute', intercept)
    with pytest.raises(r.ResolutionRejected): r.rollback_pending(db, audit_id=original_id, release_id='failed-h')
    db.refresh(row)
    assert fired and row.verification['comparison_cache'] == {'newer': True}
    assert row.verification['detail_pending'] is False and r.RECEIPT in row.verification
    assert len(list(db.scalars(select(AuditEvent)))) == 1
