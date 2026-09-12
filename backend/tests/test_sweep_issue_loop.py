"""guard-20260912 T9: every sweep finding reaches the five-stage loop."""
import ast
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from app.visa_snapshot import consistency_sweep as cs, sweep_issues
from app.visa_snapshot import verified_overrides as vo
from app.visa_snapshot.models import DatabaseChangeLog, DatabaseIssueReport, KimiRouteGuidanceCache

from tests import _guard_fixtures as gf

NOW = datetime(2026, 9, 12, 3, 20, tzinfo=timezone.utc)
APP = Path(__file__).resolve().parents[1] / "app" / "visa_snapshot"
KEY = "HKG|HKG|VNM|tourism|default|unknown|v6"


@pytest.fixture
def operator(monkeypatch, tmp_path):
    monkeypatch.setenv("ELLIS_OPERATOR_OVERRIDES", str(tmp_path / "operator.json"))
    vo.reload()
    yield
    vo.reload()


def _cleanup(db, fingerprints):
    db.query(DatabaseIssueReport).filter(DatabaseIssueReport.fingerprint.in_(list(fingerprints))).delete(
        synchronize_session=False)
    db.commit()


def test_finding_files_one_issue_with_untruncated_fields(db):
    long_field = "passport_validity_requirement_and_entry_requirements_and_special_conditions"
    finding = cs.make_finding("surface_divergence", KEY, long_field, observed="a", expected="b",
                              surfaces=["reader", "qc"], evidence={"reason": "test"}, checked_at=NOW.isoformat())
    try:
        counts = sweep_issues.file_findings(db, [finding], run_id="run-1", now=NOW)
        assert counts["created"] == 1 and counts["updated"] == 0
        issue = db.execute(select(DatabaseIssueReport).where(
            DatabaseIssueReport.fingerprint == finding.fingerprint)).scalars().one()
        assert issue.reported_by == "sweep" and issue.status == "open"
        assert len(issue.field) == 64 and issue.field == long_field[:64]
        assert list(issue.proposal["fields"]) == [long_field]
        meta = issue.proposal["sweep"]
        assert meta["run_id"] == "run-1" and meta["code"] == "surface_divergence"
        assert meta["fingerprint"] == finding.fingerprint and meta["fields"] == [long_field]
        assert meta["severity"] == "blocking" and meta["occurrences"] == 1
        assert meta["first_seen"] == meta["last_seen"] == NOW.isoformat()
        assert issue.cache_key == KEY and issue.route["destination_country"] == "VNM"
        assert sweep_issues.sweep_fields(issue) == [long_field]
    finally:
        _cleanup(db, [finding.fingerprint])


def test_repeat_run_updates_instead_of_duplicating(db):
    finding = cs.make_finding("proof_missing_quote", KEY, "disposition", observed="asserted", expected="quoted")
    try:
        sweep_issues.file_findings(db, [finding], run_id="run-1", now=NOW)
        later = datetime(2026, 9, 13, 3, 20, tzinfo=timezone.utc)
        prediction = sweep_issues.file_findings(db, [finding], run_id="run-2", now=later, dry_run=True)
        counts = sweep_issues.file_findings(db, [finding], run_id="run-2", now=later)
        assert prediction["created"] == counts["created"] == 0
        assert prediction["updated"] == counts["updated"] == 1
        issues = db.execute(select(DatabaseIssueReport).where(
            DatabaseIssueReport.fingerprint == finding.fingerprint)).scalars().all()
        assert len(issues) == 1
        meta = issues[0].proposal["sweep"]
        assert meta["occurrences"] == 2 and meta["last_run_id"] == "run-2" and meta["first_run_id"] == "run-1"
        assert meta["last_seen"] == later.isoformat() and meta["first_seen"] == NOW.isoformat()
    finally:
        _cleanup(db, [finding.fingerprint])


@pytest.mark.parametrize("closed", ["dismissed", "published"])
def test_dismissed_finding_that_reproduces_files_a_superseding_issue_naming_the_prior(db, closed):
    finding = cs.make_finding("verdict_detail_missing", KEY, "requirement_detail", observed=None,
                              expected=["evisa", "paper_visa"])
    try:
        sweep_issues.file_findings(db, [finding], run_id="run-1", now=NOW)
        prior = db.execute(select(DatabaseIssueReport).where(
            DatabaseIssueReport.fingerprint == finding.fingerprint)).scalars().one()
        prior.status = closed
        db.commit()
        counts = sweep_issues.file_findings(db, [finding], run_id="run-2", now=NOW)
        assert counts["created"] == 1 and counts[f"{closed}_but_reproduced"] == 1
        issues = db.execute(select(DatabaseIssueReport).where(
            DatabaseIssueReport.fingerprint == finding.fingerprint)).scalars().all()
        fresh = next(i for i in issues if i.id != prior.id)
        assert fresh.status == "open"
        assert fresh.proposal["sweep"]["supersedes"] == prior.id
        assert fresh.proposal["sweep"]["supersedes_status"] == closed
        assert prior.id in fresh.note and closed in fresh.note
    finally:
        _cleanup(db, [finding.fingerprint])


def test_fingerprint_is_stable_across_runs_and_distinct_per_field(db, operator):
    with gf.seeded(db) as keys:
        first = cs.run(db, now=NOW, trigger="test", keys=keys, absence_checks=False, coverage=False)
        second = cs.run(db, now=datetime(2026, 9, 13, tzinfo=timezone.utc), trigger="test", keys=keys,
                        absence_checks=False, coverage=False)
    a = sorted(f["fingerprint"] for f in first["findings"])
    b = sorted(f["fingerprint"] for f in second["findings"])
    assert a and a == b
    assert len(set(a)) == len(a)
    one = cs.make_finding("proof_missing_quote", KEY, "disposition", observed="asserted")
    other = cs.make_finding("proof_missing_quote", KEY, "source_url", observed="asserted")
    assert one.fingerprint != other.fingerprint


def test_sweep_writes_no_facts_and_the_import_graph_excludes_fact_writers(db, operator):
    source = (APP / "sweep_issues.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = set()
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            calls.add(fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", ""))
        elif isinstance(node, ast.ImportFrom):
            imported.update(alias.name for alias in node.names)
            imported.add(str(node.module or ""))
    assert not calls & {"append_operator_entry", "recheck_route", "recheck_row", "write_text",
                        "write_bytes", "unlink", "rename", "record"}, calls
    assert not imported & {"change_log", "fetching", "verified_overrides", "freshness"}, imported
    finding = cs.make_finding("surface_divergence", KEY, "visa_fee_amount", observed=1, expected=25)
    with gf.seeded(db) as keys:
        guidance = json.dumps(sorted((r.cache_key, json.dumps(r.guidance, sort_keys=True)) for r in
                                     db.execute(select(KimiRouteGuidanceCache)).scalars()))
        override_hash = hashlib.sha256(vo.OVERRIDES.read_bytes()).hexdigest()
        changes = db.query(DatabaseChangeLog).count()
        try:
            sweep_issues.file_findings(db, [finding], run_id="run-1", now=NOW)
            db.expire_all()
            assert json.dumps(sorted((r.cache_key, json.dumps(r.guidance, sort_keys=True)) for r in
                                     db.execute(select(KimiRouteGuidanceCache)).scalars())) == guidance
            assert hashlib.sha256(vo.OVERRIDES.read_bytes()).hexdigest() == override_hash
            assert db.query(DatabaseChangeLog).count() == changes
        finally:
            _cleanup(db, [finding.fingerprint])


def test_recheck_reruns_the_single_check_for_the_route(db, operator):
    with gf.seeded(db) as keys:
        korea = next(k for k in keys if k.startswith("HKG|HKG|KOR|tourism"))
        evidence = cs.run(db, now=NOW, trigger="test", keys=[korea], absence_checks=False, coverage=False)
        conflict = next(f for f in evidence["findings"] if f["code"] == "verdict_page_scheme_conflict")
        try:
            sweep_issues.file_findings(db, [conflict], run_id=evidence["run_id"], now=NOW)
            issue = db.execute(select(DatabaseIssueReport).where(
                DatabaseIssueReport.fingerprint == conflict["fingerprint"])).scalars().one()
            passes, why = sweep_issues.recheck(db, issue)
            assert passes is False and "verdict_page_scheme_conflict still fires" in why
            reader_flag = DatabaseIssueReport(cache_key=korea, route={}, field="x", reported_by="reader")
            assert sweep_issues.recheck(db, reader_flag)[0] is False
        finally:
            _cleanup(db, [conflict["fingerprint"]])


def test_adjudication_signature_binds_full_evidence_beyond_display_truncation():
    one = cs.make_finding("proof_missing_quote", KEY, "disposition", observed="asserted",
                          evidence={"long_page": "x" * 5000, "tail": "old"}).as_dict()
    two = dict(one, evidence={"long_page": "x" * 5000, "tail": "changed"})
    assert sweep_issues._bounded(one["evidence"]) == sweep_issues._bounded(two["evidence"])
    assert sweep_issues.finding_signature(one) != sweep_issues.finding_signature(two)
    stored = dict(one, evidence=sweep_issues._bounded(one["evidence"]),
                  evidence_sha256=sweep_issues.evidence_digest(one["evidence"]))
    assert sweep_issues.finding_signature(stored) == sweep_issues.finding_signature(one)


def test_live_finding_refreshes_all_reviewed_context_before_adjudication(db):
    old = cs.make_finding("proof_missing_quote", KEY, "disposition", observed="asserted",
                          expected="old quote", surfaces=["qc"], evidence={"page": "old"})
    current = cs.make_finding("proof_missing_quote", KEY, "disposition", observed="asserted",
                              expected="current quote", surfaces=["qc", "reader"],
                              evidence={"page": "new"})
    assert old.fingerprint == current.fingerprint
    try:
        sweep_issues.file_findings(db, [old], run_id="old-context", now=NOW)
        sweep_issues.file_findings(db, [current], run_id="current-context", now=NOW)
        issue = db.query(DatabaseIssueReport).filter_by(fingerprint=current.fingerprint).one()
        proposal = dict(issue.proposal)
        meta = dict(proposal["sweep"])
        assert meta["expected"] == current.expected and meta["surfaces"] == current.surfaces
        assert meta["severity"] == current.severity and meta["code"] == current.code
        assert proposal["fields"]["disposition"] == {"observed": current.observed, "expected": current.expected}
        assert sweep_issues.finding_signature(meta) == sweep_issues.finding_signature(current.as_dict())
        meta["false_positive_adjudication"] = {"finding_signature": sweep_issues.finding_signature(meta)}
        proposal["sweep"] = meta
        issue.proposal = proposal
        issue.status = "dismissed"
        db.commit()
        repeated = sweep_issues.file_findings(db, [current], run_id="same-context", now=NOW)
        assert repeated["created"] == 0 and repeated["adjudicated_false_positive"] == 1
    finally:
        _cleanup(db, [current.fingerprint])
