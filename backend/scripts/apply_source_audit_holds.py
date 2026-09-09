"""Apply reviewed source holds without changing any cached answer or history.

Dry-run by default. --apply requires --backup to a new SQLite file. Every
manifest route must have its canonical cache row unless its reviewed hold
explicitly sets protect_uncached_route=true. That exception inserts only an
issue, so a later cold lookup is held without inventing cached route coverage.
Open or acknowledged freshness-monitor source_audit issues are reused. Existing
resolved issues, reader reports, cached guidance and change history are retained.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.visa_snapshot.kimi_primary import cache_key, canonical_key, normalize_document_type

DEFAULT_MANIFEST = Path(__file__).resolve().parents[2] / 'data/database_seed/source_audit_holds_2026_09_09.json'


def _read_manifest(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data, dict) or not data.get('id') or not isinstance(data.get('holds'), list) or not data['holds']:
        raise ValueError('Manifest must identify a non-empty list of reviewed holds')
    entries, seen = [], set()
    for hold in data['holds']:
        if not isinstance(hold, dict):
            raise ValueError('Each hold must be an object')
        if 'protect_uncached_route' in hold and type(hold['protect_uncached_route']) is not bool:
            raise ValueError('protect_uncached_route must be a boolean')
        parts = str(hold.get('route_key', '')).split('|')
        if len(parts) != 4:
            raise ValueError('Each hold needs nationality|destination|purpose|document')
        nationality, destination, purpose, document = parts
        if not re.fullmatch(r'[A-Z]{3}', nationality) or not re.fullmatch(r'[A-Z]{3}', destination):
            raise ValueError('Manifest nationalities and destinations must be ISO alpha-3 codes')
        if not re.fullmatch(r'[a-z_]+', purpose) or normalize_document_type(document) != document:
            raise ValueError('Manifest purpose and document must use canonical names')
        if not str(hold.get('decision', '')).startswith('hold_') or not hold.get('finding') or not hold.get('sources'):
            raise ValueError('Each hold must include its reviewed decision, finding and sources')
        route = {'passport_nationality': nationality, 'destination_country': destination,
                 'travel_purpose': purpose, 'travel_document_type': document}
        key = cache_key(route)
        if key in seen:
            raise ValueError('Manifest contains duplicate canonical routes')
        seen.add(key)
        entries.append((key, route, hold))
    return data, entries


def migrate(database, *, manifest=DEFAULT_MANIFEST, apply=False, backup=None):
    data, entries = _read_manifest(manifest)
    database = Path(database).resolve()
    if apply:
        if not backup:
            raise ValueError('An explicit backup path is required with --apply')
        backup = Path(backup).resolve()
        if backup.exists() or backup == database:
            raise ValueError('Backup must be a new, separate file')
        if not backup.parent.is_dir():
            raise ValueError('Backup parent directory must already exist')
    db = sqlite3.connect(database.as_uri() + ('?mode=rw' if apply else '?mode=ro'), uri=True)
    db.row_factory = sqlite3.Row
    try:
        if apply:
            db.execute('BEGIN IMMEDIATE')
        keys = {row[0] for row in db.execute('select cache_key from kimi_route_guidance_cache')}
        existing = {}
        for row in db.execute("select id, cache_key, status from database_issue_reports "
                              "where reported_by='freshness_monitor' and field='source_audit' "
                              "and status in ('open','acknowledged') order by id"):
            existing.setdefault(canonical_key(row['cache_key']), []).append(dict(row))
        missing = [key for key, _, hold in entries
                   if key not in keys and not hold.get('protect_uncached_route', False)]
        uncached = [key for key, _, hold in entries
                    if key not in keys and hold.get('protect_uncached_route', False)]
        plan = [{'cache_key': key, 'route_key': hold['route_key'],
                 'canonical_present': key in keys,
                 'protect_uncached_route': hold.get('protect_uncached_route', False),
                 'action': 'missing_canonical' if key in missing else
                           ('reuse_existing' if existing.get(key) else
                            'insert_uncached_hold' if key in uncached else 'insert_hold'),
                 'existing_issue_ids': [row['id'] for row in existing.get(key, [])],
                 'finding': hold['finding']} for key, _, hold in entries]
        report = {'manifest_id': data['id'], 'manifest_holds': len(entries),
                  'matched_routes': len(entries) - len(missing) - len(uncached),
                  'missing_canonical': missing, 'uncached_protected_routes': uncached,
                  'applied': False, 'backup': None, 'plan': plan,
                  'inserted': 0, 'reused': 0, 'repointed_existing': 0,
                  'cache_and_change_history_unchanged': True}
        if not apply:
            return report
        if missing:
            raise ValueError('All manifest routes without explicit uncached protection must match canonical cache rows: ' + ', '.join(missing))
        # Keep the writer lock while a separate read connection copies the
        # committed database, including WAL content. No changes precede backup.
        # Exclusive creation prevents accidentally replacing another backup.
        with backup.open('xb'):
            pass
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as snapshot_db:
            with sqlite3.connect(backup) as backup_db:
                snapshot_db.backup(backup_db)
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(' ', timespec='microseconds')
        for key, route, hold in entries:
            matches = existing.get(key, [])
            if matches:
                report['reused'] += 1
                # A legacy dated/residence issue must address the canonical
                # row to hold current serving; preserve its note and evidence.
                for issue in matches:
                    if issue['cache_key'] != key:
                        db.execute('update database_issue_reports set cache_key=? where id=?', (key, issue['id']))
                        report['repointed_existing'] += 1
                continue
            proposal = {'audit_id': data['id'], 'checked_at': data.get('reviewed_at'),
                        'verifier': data.get('verifier'), 'decision': hold['decision'],
                        'finding': hold['finding'], 'sources': hold['sources'],
                        'protect_uncached_route': hold.get('protect_uncached_route', False),
                        'unresolved': hold.get('unresolved', ''),
                        'fields': {'source_audit': {'finding': hold['finding'],
                                                   'decision': hold['decision']}}}
            db.execute('''insert into database_issue_reports
                (id,org_id,cache_key,route,field,note,reported_by,status,
                 resolution,resolved_by,resolved_at,notified_at,notified_to,
                 reviewed_by,reviewed_at,published_at,proposal,created_at,updated_at)
                values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (uuid.uuid4().hex, 'platform', key, json.dumps(route), 'source_audit',
                 ('Reviewed source audit: ' + hold['finding'])[:1000], 'freshness_monitor', 'open',
                 '', '', None, None, '', '', None, None,
                 json.dumps(proposal, ensure_ascii=False), now, now))
            report['inserted'] += 1
        db.commit()
        report['applied'], report['backup'] = True, str(backup)
        return report
    finally:
        db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database')
    parser.add_argument('--manifest', default=str(DEFAULT_MANIFEST))
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup')
    args = parser.parse_args()
    try:
        result = migrate(args.database, manifest=args.manifest, apply=args.apply, backup=args.backup)
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(2, f'No holds applied: {exc}\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
