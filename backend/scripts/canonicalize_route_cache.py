"""Retire obsolete dated/residence/district/transit copies only after a SQLite backup.

Dry-run by default. A copy is retained when its canonical row is absent.
Open issues and change history are repointed without closing any issue.
"""
import argparse
import json
import sqlite3
from pathlib import Path


def canonical(key):
    parts = str(key or '').split('|')
    if len(parts) < 7 or parts[6] != 'v6':
        return None
    if (parts[1] == parts[0] and parts[4] == 'default' and parts[5] == 'unknown'
            and not any(s.startswith('via:') for s in parts[7:])):
        return None
    parts[1], parts[4], parts[5] = parts[0], 'default', 'unknown'
    return '|'.join(parts[:7] + [s for s in parts[7:] if not s.startswith('via:')])


def migrate(database, *, apply=False, backup=None):
    uri = Path(database).resolve().as_uri() + ('?mode=rw' if apply else '?mode=ro')
    db = sqlite3.connect(uri, uri=True)
    try:
        if apply:
            if not backup:
                raise ValueError('An explicit backup path is required')
            target = Path(backup)
            if target.exists() or target.resolve() == Path(database).resolve():
                raise ValueError('Backup must be a new, separate file')
            db.execute('BEGIN IMMEDIATE')
            # Hold the writer lock while a separate reader takes the snapshot.
            # Backing up this connection inside its write transaction can block.
            with sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True) as snapshot_db:
                with sqlite3.connect(target) as backup_db:
                    snapshot_db.backup(backup_db)
        keys = {r[0] for r in db.execute('select cache_key from kimi_route_guidance_cache')}
        plan = [(key, canonical(key)) for key in sorted(keys) if canonical(key)]
        safe = [(key, target) for key, target in plan if target in keys]
        tables = {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
        moved = {}
        if apply:
            # Names are fixed literals, never supplied by callers.
            for table in ('database_issue_reports', 'database_change_log'):
                if table not in tables:
                    continue
                columns = {r[1] for r in db.execute('pragma table_info('+table+')')}
                if 'cache_key' not in columns:
                    continue
                moved[table] = sum(db.execute('update '+table+' set cache_key=? where cache_key=?',
                                              (target, key)).rowcount for key, target in safe)
            for key, target in safe:
                db.execute('delete from kimi_route_guidance_cache where cache_key=?', (key,))
            db.commit()
        return {'applied': apply, 'backup': str(backup) if apply else None,
                'retired': [key for key, _ in safe],
                'retained_without_canonical': [key for key, target in plan if target not in keys],
                'repointed': moved}
    finally:
        db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup')
    args = parser.parse_args()
    print(json.dumps(migrate(args.database, apply=args.apply, backup=args.backup), indent=2))
