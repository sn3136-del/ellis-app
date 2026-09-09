"""The bounded repair never certifies a policy or discards an unrelated hold."""
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import AuditEvent
from app.visa_snapshot.models import KimiRouteGuidanceCache, DatabaseChangeLog, DatabaseIssueReport

spec = importlib.util.spec_from_file_location("legacy_unknown_migration",
    Path(__file__).parents[1] / "scripts/normalize_legacy_passport_unknowns.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
RUS = "RUS|RUS|CHN|tourism|default|unknown|v6"


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(mod.overrides, "find", lambda route: None)
    path = tmp_path / "cache.db"
    engine = create_engine("sqlite:///" + str(path))
    for model in (KimiRouteGuidanceCache, DatabaseIssueReport, DatabaseChangeLog, AuditEvent):
        model.__table__.create(engine)
    with Session(engine) as db:
        for key in (*mod.TARGETS, RUS):
            nat, _, dest, *_ = key.split("|")
            route = {"passport_nationality": nat, "destination_country": dest, "travel_purpose": "tourism"}
            rule = {"kind": "duration_of_intended_stay", "months": None} if key == RUS else mod.EMPTY
            db.add(KimiRouteGuidanceCache(cache_key=key, route=route,
                guidance={"disposition": "VISA_REQUIRED", "requirement_detail": "paper_visa",
                          mod.FIELD: rule, "passport_validity": "Original prose retained"},
                contradictions=[mod.FAILURE], verification={"operator_released": False, "prior_check": "unchanged"},
                model="legacy-test", status="KIMI_UNCERTAIN"))
            db.add(DatabaseIssueReport(cache_key=key, field="integrity", reported_by="freshness_monitor",
                status="open", note=mod.NOTE, proposal={"outcome": "integrity_failed",
                    "contradictions": [mod.FAILURE], "historical_read": "keep"}))
        db.add(DatabaseIssueReport(cache_key=mod.TARGETS[0], field="source_dispute",
            reported_by="freshness_monitor", status="open", note="Separate source hold", proposal={"keep": True}))
        db.add(DatabaseIssueReport(cache_key=mod.TARGETS[0], field="integrity",
            reported_by="reader", status="open", note=mod.NOTE, proposal={"keep": True}))
        db.add(DatabaseChangeLog(cache_key=mod.TARGETS[0], action="historic", origin="operator", changes={"keep": True}))
        db.commit()
    engine.dispose()
    return path


def rows(path, table):
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute("SELECT * FROM " + table + " ORDER BY id")]


def test_dry_run_is_read_only_and_targets_only_two_exact_shapes(database):
    before = database.read_bytes()
    report = mod.normalize(database)
    assert not report["applied"] and report["normalized"] == 0
    assert {p["cache_key"] for p in report["plan"]} == set(mod.TARGETS)
    assert all(p["full_guidance_passes"] for p in report["plan"])
    assert database.read_bytes() == before
    with pytest.raises(ValueError, match="backup"):
        mod.normalize(database, apply=True)


def test_repair_backs_up_logs_and_preserves_source_holds_provenance_and_history(database, tmp_path):
    before = {r["cache_key"]: r for r in rows(database, "kimi_route_guidance_cache")}
    backup = tmp_path / "backup.db"
    report = mod.normalize(database, apply=True, backup=backup)
    assert report["normalized"] == report["issues_corrected"] == 2
    assert (backup.stat().st_mode & 0o777) == 0o600
    assert {r["cache_key"]: r for r in rows(backup, "kimi_route_guidance_cache")} == before
    current = {r["cache_key"]: r for r in rows(database, "kimi_route_guidance_cache")}
    assert current[RUS] == before[RUS]
    for key in mod.TARGETS:
        expected = dict(json.loads(before[key]["guidance"]), **{mod.FIELD: None})
        assert json.loads(current[key]["guidance"]) == expected
        assert json.loads(current[key]["contradictions"]) == []
        for field in ("verification", "route", "status", "fresh_until", "generated_at"):
            assert current[key][field] == before[key][field]
    issues = rows(database, "database_issue_reports")
    corrected = [r for r in issues if r["status"] == "corrected"]
    assert len(corrected) == 2
    assert all(json.loads(r["proposal"])["historical_read"] == "keep" for r in corrected)
    assert all(r["status"] == "open" for r in issues if r not in corrected)
    assert len(rows(database, "database_change_log")) == 3
    assert len(rows(database, "audit_events")) == 2
    again = mod.normalize(database, apply=True, backup=tmp_path / "unused-backup.db")
    assert not again["applied"] and again["normalized"] == 0
    assert len(rows(database, "audit_events")) == 2


def test_another_invariant_keeps_the_integrity_issue_open(database, tmp_path):
    with sqlite3.connect(database) as db:
        row = db.execute("SELECT guidance FROM kimi_route_guidance_cache WHERE cache_key=?", (mod.TARGETS[0],)).fetchone()
        raw = dict(json.loads(row[0]), disposition="VISA_EXEMPT", requirement_detail="evisa")
        db.execute("UPDATE kimi_route_guidance_cache SET guidance=? WHERE cache_key=?", (json.dumps(raw), mod.TARGETS[0]))
    report = mod.normalize(database, apply=True, backup=tmp_path / "backup.db")
    assert report["normalized"] == 2 and report["issues_corrected"] == 1
    issue = next(r for r in rows(database, "database_issue_reports")
        if r["cache_key"] == mod.TARGETS[0] and r["reported_by"] == "freshness_monitor" and r["field"] == "integrity")
    assert issue["status"] == "open"


def test_audit_failure_rolls_back_both_repairs_and_resolutions(database, tmp_path):
    before_cache = rows(database, "kimi_route_guidance_cache")
    before_issues = rows(database, "database_issue_reports")
    with sqlite3.connect(database) as db:
        db.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON audit_events BEGIN SELECT RAISE(ABORT,'audit unavailable'); END")
    with pytest.raises(sqlite3.IntegrityError):
        mod.normalize(database, apply=True, backup=tmp_path / "backup.db")
    assert rows(database, "kimi_route_guidance_cache") == before_cache
    assert rows(database, "database_issue_reports") == before_issues
    assert len(rows(database, "database_change_log")) == 1


def test_cas_rejects_another_sessions_new_guidance_and_review_stamp(database):
    with sqlite3.connect(database) as first:
        first.row_factory = sqlite3.Row
        row = first.execute("SELECT * FROM kimi_route_guidance_cache WHERE cache_key=?", (mod.TARGETS[0],)).fetchone()
        with sqlite3.connect(database) as second:
            second.execute("UPDATE kimi_route_guidance_cache SET guidance=?, verification=? WHERE id=?",
                (json.dumps({mod.FIELD: {"kind": "months_after_arrival", "months": 3}}),
                 json.dumps({"new_review": "must survive"}), row["id"]))
        with pytest.raises(RuntimeError, match="changed"):
            mod._replace_cache(first, row, {mod.FIELD: None}, [], "2026-09-09 17:30:00")
    current = next(r for r in rows(database, "kimi_route_guidance_cache") if r["id"] == row["id"])
    assert json.loads(current["verification"]) == {"new_review": "must survive"}
    assert json.loads(current["guidance"])[mod.FIELD]["months"] == 3
