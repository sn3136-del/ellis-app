"""Audited resolution of one exact obsolete KOR→CHN extraction revision.

Installation preserves raw facts and TTL. A separate, explicit operation clears
only the persisted pending bit, atomically with a receipt. Readers require that
receipt and its original raw generation, while ordinary fetch bookkeeping may
continue. New extraction work and every independent serving hold remain active.
"""
from copy import deepcopy
from datetime import datetime, timezone, date
from functools import lru_cache
import hashlib
import json
import re
from uuid import uuid4

from sqlalchemy import select, update, func
from sqlalchemy.exc import SQLAlchemyError

ACTOR = 'CodexAI'
ACTION = 'database_reviewed_pending_resolution'
ROLLBACK_ACTION = 'database_reviewed_pending_resolution_rollback'
RECEIPT = 'reviewed_condition_resolution'
# Complete freshness._stamp/_commit_recheck bookkeeping keys. They cannot
# start extraction or own a pending bit. Unknown metadata remains significant.
READ_BOOKKEEPING = frozenset(('grounded_check', 'last_good_check', 'superseded_check',
                             'last_source_read_at', 'comparison_cache'))


class ResolutionRejected(ValueError):
    pass


def _today():
    return datetime.now(timezone.utc).date()


def _session():
    from app.db import SessionLocal
    return SessionLocal()


def _digest(value):
    from scripts.prepare_reviewed_product_patch import digest
    return digest(value)


def _applies(route):
    from . import kimi_primary
    from scripts.convert_reviewed_kor_chn_conditions import KEY
    return bool(kimi_primary.cache_key(route) == KEY
                and route.get('travel_document_type', 'ordinary_passport') == 'ordinary_passport'
                and not any(route.get(k) for k in ('arrival_date', 'consular_jurisdiction', 'transit_countries')))


@lru_cache(maxsize=2)
def _rebuilt(manifest_text, overlay_text):
    from scripts.convert_reviewed_kor_chn_conditions import KEY, convert
    manifest, prepared = json.loads(manifest_text), json.loads(overlay_text)
    baseline = dict(deepcopy(manifest['baseline']), cache_key=KEY)
    rebuilt, _, expected, provenance = convert(manifest, [baseline])
    if rebuilt != prepared:
        raise ResolutionRejected('Installed overlay differs from the full reviewed conversion')
    return manifest, expected, provenance


def _current(route):
    from . import verified_overrides as vo
    from scripts.convert_reviewed_kor_chn_conditions import MANIFEST, OVERLAY
    if not _applies(route):
        return None
    root = vo.OVERRIDES.parent
    try:
        if sum(p.resolve() == (root / OVERLAY).resolve() for p in vo._reviewed_overlay_paths()) != 1:
            return None
        mt, ot = (root / MANIFEST).read_text(), (root / OVERLAY).read_text()
        manifest, expected, provenance = _rebuilt(mt, ot)
        spec = manifest['specification']
        if not date.fromisoformat(spec['sources'][0]['checked_at']) <= _today() <= date.fromisoformat(spec['policy_effective_to']):
            return None
        return expected, provenance, manifest, {
            'manifest_sha256': hashlib.sha256(mt.encode()).hexdigest(),
            'overlay_sha256': hashlib.sha256(ot.encode()).hexdigest()}
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        return None


def _row(db):
    from .models import KimiRouteGuidanceCache as Model
    from scripts.convert_reviewed_kor_chn_conditions import KEY
    with db.no_autoflush:
        return db.execute(select(Model).where(Model.cache_key == KEY)
                          .execution_options(populate_existing=True)).scalar_one_or_none()


def _generation_matches(row, manifest):
    from scripts.convert_reviewed_kor_chn_conditions import pending_metadata
    if row is None or row.guidance != manifest['baseline']['raw_guidance'] or row.route != manifest['baseline']['route']:
        return False
    before = manifest['specification']['pending_baseline']
    current = pending_metadata(row)
    return all(current[k] == v for k, v in before.items()
               if k not in ('verification', 'updated_at', 'fresh_until'))


def _receipt_valid(db, row, current):
    from app.models import AuditEvent
    if current is None or not _generation_matches(row, current[2]):
        return False
    _, _, manifest, hashes = current
    before = manifest['specification']['pending_baseline']
    ver = row.verification if isinstance(row.verification, dict) else {}
    receipt = ver.get(RECEIPT)
    if not isinstance(receipt, dict) or ver.get('detail_pending') is not False:
        return False
    # Preserve all non-fetch metadata, including new pending flags and labels.
    # Same-valued regeneration remains new work via generated_at above.
    old_other = {k: v for k, v in before['verification'].items()
                 if k not in READ_BOOKKEEPING | {'detail_pending'}}
    new_other = {k: v for k, v in ver.items()
                 if k not in READ_BOOKKEEPING | {'detail_pending', RECEIPT}}
    if old_other != new_other:
        return False
    expected = dict(hashes, contract=manifest['kind'], cache_key=row.cache_key,
                    row_id=row.id, generation=before['generated_at'],
                    raw_sha256=_digest(row.guidance), route_sha256=_digest(row.route),
                    prior_metadata_sha256=_digest(before))
    if set(receipt) != set(expected) | {'audit_id'} or any(receipt[k] != v for k, v in expected.items()):
        return False
    event = db.get(AuditEvent, receipt['audit_id'])
    detail = event.detail if event is not None else {}
    if event is None or event.at is None:
        return False
    event_at = event.at.replace(tzinfo=timezone.utc) if event.at.tzinfo is None else event.at.astimezone(timezone.utc)
    captured = datetime.fromisoformat(manifest['specification']['pending_baseline_captured_at'])
    if not captured <= event_at <= datetime.now(timezone.utc):
        return False
    return bool(event is not None and event.actor == ACTOR and event.action == ACTION
                and event.application_id == 'database' and event.org_id == 'platform'
                and isinstance(detail, dict) and detail.get('receipt') == receipt
                and detail.get('changes') == {'verification.detail_pending': {'from': True, 'to': False}})


def reconcile(route, guidance, provenance):
    """All readers remove the old warnings only with the committed receipt."""
    current = _current(route)
    if current is None or guidance != current[0] or provenance != current[1]:
        return guidance
    try:
        with _session() as db:
            if _receipt_valid(db, _row(db), current):
                return dict(deepcopy(guidance), uncertainty=[])
    except (SQLAlchemyError, ValueError, TypeError, KeyError, AttributeError):
        pass
    return guidance


def preserve_unresolved_pending(route, out, db=None):
    """Missing/invalid receipts cannot silently erase the original work hold."""
    from scripts.convert_reviewed_kor_chn_conditions import OLD_UNCERTAINTY
    if not _applies(route) or not isinstance(out.get('guidance'), dict):
        return out
    warnings = out['guidance'].get('uncertainty')
    current = _current(route)
    try:
        if db is None:
            with _session() as read_db:
                row = _row(read_db)
                protected = bool(row is not None and (row.verification or {}).get(RECEIPT))
                valid = _receipt_valid(read_db, row, current)
        else:
            row = _row(db)
            protected = bool(row is not None and (row.verification or {}).get(RECEIPT))
            valid = _receipt_valid(db, row, current)
        if protected:
            from . import kimi_primary
            if current is not None:
                clean = dict(current[0], uncertainty=[])
                public_clean = dict(clean)
                if public_clean.get('transit_requirement') == {'required': None, 'note': None}:
                    public_clean.pop('transit_requirement')
            if (not valid or current is None or out['guidance'] not in (clean, public_clean)
                    or out.get('source_verified') != current[1] or kimi_primary._is_stale(row)
                    or out.get('stale') or out.get('held') or out.get('review_required')
                    or out.get('missing_fields') or out.get('contradictions')):
                return dict(out, detail_pending=True)
        elif warnings == OLD_UNCERTAINTY and current is not None:
            return dict(out, detail_pending=True)
    except (SQLAlchemyError, ValueError, TypeError, KeyError, AttributeError):
        if current is not None or warnings == OLD_UNCERTAINTY:
            return dict(out, detail_pending=True)
    return out


def _matching_entries(rows):
    from . import verified_overrides as vo
    def matches(entry):
        r = entry.get('route') or {}
        return vo._key(r.get('nationality'), r.get('destination'), r.get('travel_purpose', 'tourism'),
                       r.get('travel_document_type', '')) == vo._key('KOR', 'CHN', 'tourism', 'ordinary_passport')
    return [e for e in rows if isinstance(e, dict) and matches(e)]


def _validate_install(db, current):
    from . import verified_overrides as vo, kimi_primary as kp, freshness, tstation
    from scripts.convert_reviewed_kor_chn_conditions import OVERLAY
    expected, provenance, manifest, _ = current
    row = _row(db)
    if not _generation_matches(row, manifest):
        raise ResolutionRejected('The installed review does not own this current raw generation')
    errors, seed = [], []
    for path in [vo.OVERRIDES, *vo._reviewed_overlay_paths()]:
        entries = vo._read_verification_store(path, 'pending_resolution_seed', required=path == vo.OVERRIDES,
                                             reviewed=path != vo.OVERRIDES, errors=errors)
        if path.resolve() != (vo.OVERRIDES.parent / OVERLAY).resolve():
            seed.extend(_matching_entries(entries))
    ops = vo._read_verification_store(vo.operator_overrides_path(), 'pending_resolution_operator', errors=errors)
    if (errors or seed != manifest['baseline']['seed_entries']
            or _matching_entries(ops) != manifest['baseline']['operator_entries']):
        raise ResolutionRejected('The ordered seed/operator layers changed')
    vo.reload()
    g, prov = vo.apply(row.guidance, row.route)
    if g not in (expected, dict(expected, uncertainty=[])) or prov != provenance:
        raise ResolutionRejected('Installed merged facts or field ownership changed')
    _, missing, diagnostics = kp.validate_answer(g)
    issues = freshness.active_disputed_fields(db, row.cache_key)
    for check in ((row.verification or {}).get('grounded_check'), (row.verification or {}).get('last_good_check')):
        if isinstance(check, dict): issues.extend(check.get('disputed_fields') or [])
    rows = tstation.records_for_route(row.route, g, prov, disputed_fields=issues)
    if (kp._is_stale(row) or missing or diagnostics or kp.serve_time_invariants(g)
            or issues or not rows or any(r.get('_evidence_low', r.get('confidence_level') == 'Low') for r in rows)):
        raise ResolutionRejected('An independent evidence, freshness or policy hold remains')
    return row


def _append_audit(db, audit_id, detail, action=ACTION):
    from app import audit
    from app.models import AuditEvent
    next_seq = (db.query(func.max(AuditEvent.seq)).scalar() or 0) + 1
    db.add(AuditEvent(id=audit_id, seq=next_seq, org_id='platform', application_id='database',
                      action=action, actor=ACTOR, detail=audit.redact(detail)))


def _cas_clauses(db, row):
    from . import freshness
    from .models import KimiRouteGuidanceCache as Model
    from scripts.convert_reviewed_kor_chn_conditions import METADATA_FIELDS
    clauses = [Model.id == row.id, freshness.json_unchanged(db, Model.guidance, row.guidance),
               freshness.json_unchanged(db, Model.route, row.route)]
    for field in METADATA_FIELDS:
        column, value = getattr(Model, field), getattr(row, field)
        clauses.append(freshness.json_unchanged(db, column, value) if field in
                       ('verification', 'missing_fields', 'contradictions') else column == value)
    return clauses


def resolve_pending(db, *, manifest_sha256, overlay_sha256, release_id):
    """Explicit post-install operation. Atomic, idempotent and notification-free."""
    from . import freshness
    from .models import KimiRouteGuidanceCache as Model
    from .models import DatabaseIssueReport
    from scripts.convert_reviewed_kor_chn_conditions import pending_metadata, METADATA_FIELDS
    if db.new or db.dirty or db.deleted:
        raise ResolutionRejected('Use a clean session; unrelated writes cannot be committed')
    if not isinstance(release_id, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', release_id):
        raise ResolutionRejected('A bounded release identifier is required')
    from scripts.convert_reviewed_kor_chn_conditions import MANIFEST
    from . import verified_overrides as vo
    try:
        route = json.loads((vo.OVERRIDES.parent / MANIFEST).read_text())['baseline']['route']
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ResolutionRejected('Installed manifest is unavailable') from exc
    current = _current(route)
    if current is None or current[3] != {'manifest_sha256': manifest_sha256, 'overlay_sha256': overlay_sha256}:
        raise ResolutionRejected('Pinned installed review does not validate')
    row = _validate_install(db, current)
    if _receipt_valid(db, row, current):
        return {'created': False, 'audit_id': row.verification[RECEIPT]['audit_id'],
                'actor': ACTOR, 'cache_key': row.cache_key, 'detail_pending': False}
    manifest = current[2]; before = manifest['specification']['pending_baseline']
    if pending_metadata(row) != before:
        raise ResolutionRejected('The exact pending revision changed before adjudication')
    audit_id = uuid4().hex
    receipt = dict(current[3], contract=manifest['kind'], cache_key=row.cache_key, row_id=row.id,
                   generation=before['generated_at'], raw_sha256=_digest(row.guidance),
                   route_sha256=_digest(row.route), prior_metadata_sha256=_digest(before), audit_id=audit_id)
    verification = dict(deepcopy(row.verification), detail_pending=False, **{RECEIPT: receipt})
    clauses = _cas_clauses(db, row) + [~select(DatabaseIssueReport.id).where(
        DatabaseIssueReport.cache_key == row.cache_key,
        DatabaseIssueReport.status.in_(('open', 'acknowledged'))).exists()]
    try:
        with db.no_autoflush:
            changed = db.execute(update(Model).where(*clauses).values(verification=verification)
                                 .execution_options(synchronize_session=False))
        if changed.rowcount != 1:
            raise ResolutionRejected('A concurrent cache revision prevented pending adjudication')
        # The cache write lock closes the issue-insertion boundary. Re-read all
        # installed artifacts and ordered stores immediately before the audit,
        # so a deployment mutation during the earlier checks rolls back too.
        final = _current(route)
        if final is None or final[3] != current[3]:
            raise ResolutionRejected('Installed artifacts changed during adjudication')
        _validate_install(db, final)
        _append_audit(db, audit_id, {'receipt': receipt, 'release_id': release_id,
            'changes': {'verification.detail_pending': {'from': True, 'to': False}},
            'note': 'Exact obsolete extraction warnings resolved by installed official-source review; raw facts, grade and TTL unchanged.'})
        db.commit()
        db.refresh(row)
    except Exception:
        db.rollback()
        raise
    return {'created': True, 'audit_id': audit_id, 'actor': ACTOR,
            'cache_key': row.cache_key, 'detail_pending': False}


def rollback_pending(db, *, audit_id, release_id):
    """Restore only this event's pending bit before a failed deployment rollback.

No policy facts, freshness timestamps, source checks or original audit event
are rewound. Changed raw generations and concurrent metadata reject the write.
It can run even when the deployed evidence file itself failed its integrity
check: restoring an owned pending hold does not require serving its claims.
"""
    from app.models import AuditEvent
    from .models import KimiRouteGuidanceCache as Model
    from scripts.convert_reviewed_kor_chn_conditions import pending_metadata
    if db.new or db.dirty or db.deleted:
        raise ResolutionRejected('Use a clean rollback session')
    if not isinstance(release_id, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', release_id):
        raise ResolutionRejected('A bounded rollback release identifier is required')
    original = db.get(AuditEvent, audit_id)
    if (original is None or original.actor != ACTOR or original.action != ACTION
            or original.application_id != 'database' or original.org_id != 'platform'
            or (original.detail or {}).get('changes') != {'verification.detail_pending': {'from': True, 'to': False}}):
        raise ResolutionRejected('The exact original pending-resolution event is required')
    receipt = (original.detail or {}).get('receipt')
    row = _row(db)
    if (not isinstance(receipt, dict) or receipt.get('audit_id') != audit_id or row is None
            or receipt.get('row_id') != row.id or receipt.get('cache_key') != row.cache_key
            or receipt.get('generation') != pending_metadata(row)['generated_at']
            or receipt.get('raw_sha256') != _digest(row.guidance)
            or receipt.get('route_sha256') != _digest(row.route)):
        raise ResolutionRejected('Rollback does not own this current raw generation')
    ver = row.verification if isinstance(row.verification, dict) else {}
    prior = db.scalars(select(AuditEvent).where(AuditEvent.action == ROLLBACK_ACTION,
                       AuditEvent.actor == ACTOR, AuditEvent.application_id == 'database'))
    for event in prior:
        detail = event.detail or {}
        if detail.get('resolution_audit_id') != audit_id:
            continue
        if (detail.get('receipt') != receipt or detail.get('changes') != {
                'verification.detail_pending': {'from': False, 'to': True}}
                or ver.get('detail_pending') is not True or RECEIPT in ver):
            raise ResolutionRejected('Existing rollback receipt no longer matches current state')
        return {'created': False, 'audit_id': event.id, 'resolution_audit_id': audit_id,
                'actor': ACTOR, 'detail_pending': True, 'cache_key': row.cache_key}
    if ver.get('detail_pending') is not False or ver.get(RECEIPT) != receipt:
        raise ResolutionRejected('A later pending state or receipt cannot be overwritten')
    restored = {k: deepcopy(v) for k, v in ver.items() if k != RECEIPT}
    restored['detail_pending'] = True
    rollback_id = uuid4().hex
    try:
        with db.no_autoflush:
            result = db.execute(update(Model).where(*_cas_clauses(db, row)).values(verification=restored)
                                .execution_options(synchronize_session=False))
        if result.rowcount != 1:
            raise ResolutionRejected('A concurrent change prevented pending rollback')
        _append_audit(db, rollback_id, {'resolution_audit_id': audit_id, 'receipt': receipt,
            'release_id': release_id, 'changes': {'verification.detail_pending': {'from': False, 'to': True}},
            'note': 'Failed deployment recovery restored only its owned pending hold; original resolution audit retained.'},
            action=ROLLBACK_ACTION)
        db.commit(); db.refresh(row)
    except Exception:
        db.rollback()
        raise
    return {'created': True, 'audit_id': rollback_id, 'resolution_audit_id': audit_id,
            'actor': ACTOR, 'detail_pending': True, 'cache_key': row.cache_key}
