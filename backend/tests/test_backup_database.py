from pathlib import Path
import sqlite3

import pytest

from scripts.backup_database import backup_database


def test_backup_includes_committed_wal_and_is_readable_after_source_closes(tmp_path):
    source = tmp_path / 'live.db'
    dest = tmp_path / 'backup.db'
    with sqlite3.connect(source) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA wal_autocheckpoint=0')
        db.execute('CREATE TABLE records (id INTEGER PRIMARY KEY, value TEXT)')
        db.execute("INSERT INTO records(value) VALUES ('latest correction')")
        db.commit()
        assert Path(str(source) + '-wal').stat().st_size > 0
        result = backup_database(source, dest)
    with sqlite3.connect(dest) as restored:
        assert restored.execute('SELECT value FROM records').fetchall() == [('latest correction',)]
        assert restored.execute('PRAGMA integrity_check').fetchone() == ('ok',)
    assert result['integrity_check'] == 'ok'
    assert dest.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('bad_source', ['missing', 'corrupt', 'empty'])
def test_failed_backup_preserves_previous_snapshot(tmp_path, bad_source):
    source, dest = tmp_path / 'live.db', tmp_path / 'previous.db'
    if bad_source == 'corrupt':
        source.write_bytes(b'not a SQLite database')
    elif bad_source == 'empty':
        sqlite3.connect(source).close()
    dest.write_bytes(b'last good backup')
    with pytest.raises((ValueError, sqlite3.DatabaseError, RuntimeError)):
        backup_database(source, dest)
    assert dest.read_bytes() == b'last good backup'
    assert list(tmp_path.glob('.ellis-backup-*')) == []


def test_backup_refuses_live_destination(tmp_path):
    source = tmp_path / 'live.db'
    sqlite3.connect(source).close()
    with pytest.raises(ValueError):
        backup_database(source, source)
