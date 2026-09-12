"""guard-20260912 T2: the read-only consistency sweep."""
import ast
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from app.visa_snapshot import consistency_sweep as cs, kimi_primary
from app.visa_snapshot import verified_overrides as vo
from app.visa_snapshot.models import DatabaseChangeLog, DatabaseIssueReport, KimiRouteGuidanceCache

from tests import _guard_fixtures as gf

NOW = datetime(2026, 9, 12, 3, 20, tzinfo=timezone.utc)
APP = Path(__file__).resolve().parents[1] / "app" / "visa_snapshot"


@pytest.fixture
def operator(monkeypatch, tmp_path):
    monkeypatch.setenv("ELLIS_OPERATOR_OVERRIDES", str(tmp_path / "operator.json"))
    vo.reload()
    yield
    vo.reload()


def _guidance_digest(db, keys):
    rows = db.execute(select(KimiRouteGuidanceCache).where(
        KimiRouteGuidanceCache.cache_key.in_(keys))).scalars().all()
    payload = json.dumps(sorted((r.cache_key, json.dumps(r.guidance, sort_keys=True),
                                 json.dumps(r.verification, sort_keys=True), r.status) for r in rows))
    return hashlib.sha256(payload.encode()).hexdigest()


def test_sweep_is_read_only(db, operator):
    with gf.seeded(db) as keys:
        before = _guidance_digest(db, keys)
        override_hash = hashlib.sha256(vo.OVERRIDES.read_bytes()).hexdigest()
        changes = db.execute(select(DatabaseChangeLog)).scalars().all()
        issues = db.execute(select(DatabaseIssueReport)).scalars().all()
        evidence = cs.run(db, now=NOW, trigger="test")
        db.expire_all()
        assert _guidance_digest(db, keys) == before
        assert hashlib.sha256(vo.OVERRIDES.read_bytes()).hexdigest() == override_hash
        assert len(db.execute(select(DatabaseChangeLog)).scalars().all()) == len(changes)
        assert len(db.execute(select(DatabaseIssueReport)).scalars().all()) == len(issues)
    assert evidence["inventory"]["canonical_rows"] == 12
    assert evidence["inventory"]["served_rows"] == 18
    assert evidence["inventory"]["published"] + evidence["inventory"]["withheld"] == 18


# Writers by name (any receiver) and session writers (a db or session receiver).
FACT_WRITERS = {"append_operator_entry", "recheck_route", "recheck_row", "write_text",
                "write_bytes", "unlink", "rename"}
SESSION_WRITERS = {"add", "delete", "commit", "flush", "merge", "add_all", "execute_update", "record"}
SESSION_NAMES = {"db", "session", "s", "db_", "sess", "change_log"}


def _writer_references(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Name):
            if fn.id in FACT_WRITERS:
                names.add(fn.id)
        elif isinstance(fn, ast.Attribute):
            if fn.attr in FACT_WRITERS:
                names.add(fn.attr)
            receiver = fn.value.id if isinstance(fn.value, ast.Name) else (
                fn.value.attr if isinstance(fn.value, ast.Attribute) else "")
            if fn.attr in SESSION_WRITERS and receiver in SESSION_NAMES:
                names.add(f"{receiver}.{fn.attr}")
    return names


def test_import_graph_excludes_fact_writers():
    """The sweep and every module it introduces reference no writer: no
    override append, no grounded recheck, no change-log record, no session
    add, delete or commit, no file write."""
    modules = ["consistency_sweep.py", "row_projection.py"]
    for extra in ("sweep_proof_checks.py", "sweep_absence.py", "inventory_coverage.py",
                  "source_authority.py", "scheme_registry.py", "sweep_issues.py"):
        if (APP / extra).exists():
            modules.append(extra)
    for name in modules:
        refs = _writer_references(APP / name)
        if name == "sweep_issues.py":
            # The issue writer may add and commit issue reports, never facts.
            assert not (refs & FACT_WRITERS) and "change_log.record" not in refs, (name, refs)
            continue
        assert not refs, (name, sorted(refs))
    # And by import: the sweep never imports the change log or the fetcher.
    tree = ast.parse((APP / "consistency_sweep.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.update(alias.name for alias in node.names)
            imported.add(str(node.module or ""))
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert not (imported & {"change_log", "fetching", "append_operator_entry", "recheck_route"}), imported


def test_sweep_detects_injected_surface_divergence(db, operator, monkeypatch):
    original = cs._qc_projection

    def skewed(db_, r, route):
        rows = original(db_, r, route)
        if r.cache_key.startswith("HKG|HKG|VNM"):
            for rec in rows:
                rec["visa_fee_amount"] = 999
        return rows
    monkeypatch.setattr(cs, "_qc_projection", skewed)
    with gf.seeded(db):
        evidence = cs.run(db, now=NOW, trigger="test", proof_checks=False, absence_checks=False,
                          coverage=False)
    hits = [f for f in evidence["findings"] if f["code"] == "surface_divergence"
            and f["cache_key"].startswith("HKG|HKG|VNM") and f["field"] == "visa_fee_amount"]
    assert len(hits) == 2, [f for f in evidence["findings"] if f["code"] == "surface_divergence"]
    assert hits[0]["expected"] == 999 and hits[0]["observed"] == 25
    assert hits[0]["surfaces"] == ["reader", "qc"] and hits[0]["severity"] == "blocking"


def test_sweep_detects_a_key_fork(db, operator):
    fork = "HKG|HKG|VNM|tourism|default|2026-12|v6"
    with gf.seeded(db):
        db.add(KimiRouteGuidanceCache(cache_key=fork, route={"passport_nationality": "HKG",
                                                             "destination_country": "VNM",
                                                             "travel_purpose": "tourism"},
                                      guidance={"disposition": "VISA_EXEMPT"}, status="KIMI_PRIMARY",
                                      model="fixture", verification={}))
        db.commit()
        try:
            evidence = cs.run(db, now=NOW, trigger="test", proof_checks=False, absence_checks=False,
                              coverage=False)
        finally:
            db.query(KimiRouteGuidanceCache).filter_by(cache_key=fork).delete()
            db.commit()
    forks = [f for f in evidence["findings"] if f["code"] == "key_fork"]
    assert len(forks) == 1
    assert forks[0]["observed"] == fork
    assert forks[0]["cache_key"] == "HKG|HKG|VNM|tourism|default|unknown|v6"
    assert forks[0]["evidence"]["canonical_exists"] is True


def test_declared_differences_do_not_file(db, operator):
    with gf.seeded(db):
        evidence = cs.run(db, now=NOW, trigger="test", proof_checks=False, absence_checks=False,
                          coverage=False)
    declared = evidence["declared_differences"]
    # Held routes carry no claims on the reader surface (three of the twelve).
    assert declared["held_envelope"] >= 3
    # The workbook prints labels for documented blanks on every row.
    assert declared["export_label"] > 0
    # The composer sees a subset on every published route.
    assert declared["ai_subset"] >= 1
    assert set(declared) == set(cs.DECLARED_DIFFERENCES)
    # None of those five ever becomes a finding.
    assert not [f for f in evidence["findings"] if f["code"] in cs.DECLARED_DIFFERENCES]
    # And the twelve fixture rows raise no surface divergence of their own.
    assert not [f for f in evidence["findings"] if f["code"] in cs.BLOCKING_CODES], \
        [f for f in evidence["findings"] if f["code"] in cs.BLOCKING_CODES]


def test_fingerprint_is_stable_across_runs(db, operator, monkeypatch):
    original = cs._qc_projection

    def skewed(db_, r, route):
        rows = original(db_, r, route)
        if r.cache_key.startswith("MYS|MYS|RUS"):
            for rec in rows:
                rec["entries"] = "Multiple"
        return rows
    monkeypatch.setattr(cs, "_qc_projection", skewed)
    with gf.seeded(db):
        first = cs.run(db, now=NOW, trigger="test", proof_checks=False, absence_checks=False, coverage=False)
        second = cs.run(db, now=datetime(2026, 9, 13, tzinfo=timezone.utc), trigger="test",
                        proof_checks=False, absence_checks=False, coverage=False)
    a = [f for f in first["findings"] if f["code"] == "surface_divergence"]
    b = [f for f in second["findings"] if f["code"] == "surface_divergence"]
    assert a and [f["fingerprint"] for f in a] == [f["fingerprint"] for f in b]
    assert a[0]["checked_at"] != b[0]["checked_at"]
    # Distinct per field and per route.
    assert cs.fingerprint("surface_divergence", "K", "entries", "x") != cs.fingerprint("surface_divergence", "K", "fee", "x")
    assert cs.fingerprint("surface_divergence", "K", "entries", "x") != cs.fingerprint("surface_divergence", "J", "entries", "x")
    assert cs.fingerprint("surface_divergence", "K", "entries", {"b": 1, "a": 2}) == \
        cs.fingerprint("surface_divergence", "K", "entries", {"a": 2, "b": 1})


def test_stale_in_process_records_copy_is_a_finding(db, operator, monkeypatch):
    from app import main as served
    with gf.seeded(db):
        fresh = served._build_tstation_rows(db)
        stale = [dict(r) for r in fresh]
        for rec in stale:
            if rec["_cache_key"].startswith("HKG|HKG|VNM"):
                rec["visa_fee_amount"] = 1
        monkeypatch.setitem(served._RECORDS_CACHE, "rows", stale)
        try:
            evidence = cs.run(db, now=NOW, trigger="test", proof_checks=False, absence_checks=False,
                              coverage=False)
        finally:
            monkeypatch.setitem(served._RECORDS_CACHE, "rows", None)
    hits = [f for f in evidence["findings"] if f["code"] == "stale_projection"]
    assert len(hits) == 1 and hits[0]["cache_key"].startswith("HKG|HKG|VNM")
