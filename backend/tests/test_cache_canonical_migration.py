"""Live cleanup must be reversible and preserve disputes and document scope."""
import importlib.util
import sqlite3
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('cache_migration', Path(__file__).parents[1] / 'scripts/canonicalize_route_cache.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_dry_run_backup_and_issue_repointing(tmp_path):
    path = tmp_path / 'cache.db'
    canonical = 'HKG|HKG|VNM|tourism|default|unknown|v6'
    dated = canonical.replace('|unknown|', '|2026-10|')
    via = canonical + '|via:JPN'
    special = dated + '|doc:diplomatic_passport'
    with sqlite3.connect(path) as db:
        db.execute('create table kimi_route_guidance_cache(cache_key text primary key)')
        db.execute('create table database_issue_reports(cache_key text, status text)')
        db.execute('create table database_change_log(cache_key text)')
        db.executemany('insert into kimi_route_guidance_cache values (?)', [(x,) for x in (canonical, dated, via, special)])
        db.execute('insert into database_issue_reports values (?,?)', (via, 'open'))
        db.execute('insert into database_change_log values (?)', (dated,))
    dry = mod.migrate(path)
    assert set(dry['retired']) == {dated, via}
    assert dry['retained_without_canonical'] == [special]
    with sqlite3.connect(path) as db:
        assert db.execute('select count(*) from kimi_route_guidance_cache').fetchone()[0] == 4
    with pytest.raises(ValueError):
        mod.migrate(path, apply=True)
    backup = tmp_path / 'before.db'
    applied = mod.migrate(path, apply=True, backup=backup)
    with sqlite3.connect(backup) as db:
        assert db.execute('select count(*) from kimi_route_guidance_cache').fetchone()[0] == 4
    with sqlite3.connect(path) as db:
        assert set(x[0] for x in db.execute('select cache_key from kimi_route_guidance_cache')) == {canonical, special}
        assert db.execute('select * from database_issue_reports').fetchone() == (canonical, 'open')
        assert db.execute('select * from database_change_log').fetchone() == (canonical,)
    assert applied['repointed'] == {'database_issue_reports': 1, 'database_change_log': 1}
