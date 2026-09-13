"""A sourced ruling survives identical rechecks without becoming fresh evidence."""
from copy import deepcopy
from datetime import datetime, timezone
import pytest

from app.models import AuditEvent
from app.visa_snapshot import freshness, records_guard, verified_overrides
from app.visa_snapshot.models import DatabaseIssueReport
from tests.test_freshness_dispute_lifecycle import (
    row_and_db, setup_readings, SOURCE, OTHER_SOURCE, ROUTE, GUIDANCE,
)


def source_dismiss(db, issue, *, audit=True, source=SOURCE, excerpt="The tariff applies to another service.", actor="reviewer"):
    now = datetime.now(timezone.utc)
    issue.status, issue.resolution = "dismissed", "The current published tariff applies; this comparison names a separate consular service."
    issue.resolved_by, issue.resolved_at = actor, now
    if audit:
        db.add(AuditEvent(org_id="platform", application_id="database", actor=actor,
            action="database_issue_dismissed", at=now,
            detail={"issue_id": issue.id, "source_url": source, "source_excerpt": excerpt}))
    db.commit()


def protect_fee(monkeypatch):
    monkeypatch.setattr(verified_overrides, "find", lambda route: {
        "source_url": SOURCE, "fields": {"government_fee": deepcopy(GUIDANCE["government_fee"])}})


def held(db, row):
    return records_guard.apply_records_hold(ROUTE, {
        "guidance": row.guidance, "grounded_check": freshness.effective_check(row.verification),
        "operator_released": True}, db).get("held")


def test_identical_sourced_dismissal_no_hold_no_new_verification_or_renewal(row_and_db, monkeypatch):
    row, db = row_and_db
    protect_fee(monkeypatch)
    setup_readings(row, db, {SOURCE: 715, OTHER_SOURCE: 715})
    first = freshness.recheck_row(db, row)
    assert first["disputed"] == ["government_fee"] and held(db, row)
    original_guidance, original_expiry = deepcopy(row.guidance), row.fresh_until
    issues = db.query(DatabaseIssueReport).all()
    original_proposals = {issue.id: deepcopy(issue.proposal) for issue in issues}
    for issue in issues:
        source_dismiss(db, issue)
    second = freshness.recheck_row(db, row)
    gc = freshness.effective_check(row.verification)
    assert second["disputed"] == [] and second["raw_disputed"] == ["government_fee"]
    assert gc["disputed_fields"] == [] and gc["raw_disputed_fields"] == ["government_fee"]
    assert gc["adjudicated_fields"] == ["government_fee"] and len(gc["adjudicated_findings"]) == 2
    assert not held(db, row) and freshness.active_disputed_fields(db, row.cache_key) == []
    assert not second["consistent"] and not second["renewed"]
    assert "government_fee" not in gc["verified_fields"] and "government_fee" not in gc["field_sources"]
    assert "government_fee" in gc["unverified_fields"]
    assert all("government_fee" not in check["verified_fields"] for check in gc["source_checks"])
    assert row.guidance == original_guidance and row.fresh_until == original_expiry
    assert db.query(DatabaseIssueReport).count() == 2
    assert {issue.id: issue.proposal for issue in db.query(DatabaseIssueReport)} == original_proposals


@pytest.mark.parametrize("invalid", ["missing_audit", "missing_excerpt", "unofficial_source", "wrong_actor", "old_audit"])
def test_unaudited_or_unsourced_decision_never_exempts_publication(row_and_db, monkeypatch, invalid):
    row, db = row_and_db
    protect_fee(monkeypatch)
    setup_readings(row, db, {SOURCE: 715, OTHER_SOURCE: 715})
    freshness.recheck_row(db, row)
    for issue in db.query(DatabaseIssueReport).all():
        source_dismiss(db, issue, audit=invalid != "missing_audit",
            excerpt="" if invalid == "missing_excerpt" else "The tariff applies to another service.",
            source="https://example.com/" if invalid == "unofficial_source" else SOURCE)
    if invalid in {"wrong_actor", "old_audit"}:
        for event in db.query(AuditEvent):
            if invalid == "wrong_actor":
                event.actor = "somebody_else"
            else:
                event.at = datetime(2020, 1, 1)
        db.commit()
    result = freshness.recheck_row(db, row)
    assert result["disputed"] == ["government_fee"]
    assert result["adjudicated_fields"] == [] and held(db, row)
    assert not result["renewed"]


@pytest.mark.parametrize("change", ["proposed_value", "quote", "stored_value", "source_url", "conflicting_evidence", "field_set"])
def test_changed_comparison_never_matches_previous_dismissal(row_and_db, change):
    row, db = row_and_db
    fields = {"government_fee": {"amount": 715, "currency": "CNY"}}
    evidence = {"government_fee": "The visa fee is 715 CNY."}
    guidance, url, conflicts = deepcopy(row.guidance), SOURCE, []
    freshness._file_dispute(db, row, ROUTE, guidance, fields, evidence, url, "2026-09-13")
    db.commit()
    source_dismiss(db, db.query(DatabaseIssueReport).one())
    if change == "proposed_value": fields["government_fee"]["amount"] = 800
    elif change == "quote": evidence["government_fee"] = "From October the visa fee is 715 CNY."
    elif change == "stored_value": guidance["government_fee"]["amount"] = 300
    elif change == "source_url": url = OTHER_SOURCE
    elif change == "conflicting_evidence": conflicts = [{"field": "government_fee", "source_url": OTHER_SOURCE, "value": 900}]
    else:
        fields["processing_time"] = "7 days"; evidence["processing_time"] = "7 days"
    assert freshness._file_dispute(db, row, ROUTE, guidance, fields, evidence, url, "2026-09-14", conflicting_evidence=conflicts) is None
    db.commit()
    assert db.query(DatabaseIssueReport).count() == 2
    assert freshness.active_disputed_fields(db, row.cache_key)


def test_every_current_comparison_requires_its_own_matching_ruling(row_and_db, monkeypatch):
    row, db = row_and_db
    protect_fee(monkeypatch)
    setup_readings(row, db, {SOURCE: 715, OTHER_SOURCE: 715})
    freshness.recheck_row(db, row)
    source_dismiss(db, db.query(DatabaseIssueReport).first())
    result = freshness.recheck_row(db, row)
    assert result["disputed"] == ["government_fee"] and held(db, row)
    assert result["adjudicated_fields"] == []
    assert len(freshness.effective_check(row.verification)["adjudicated_findings"]) == 1
    assert not result["renewed"]


def test_changed_evidence_after_successful_dismissal_holds_again(row_and_db, monkeypatch):
    row, db = row_and_db
    protect_fee(monkeypatch)
    setup_readings(row, db, {SOURCE: 715, OTHER_SOURCE: 715})
    freshness.recheck_row(db, row)
    for issue in db.query(DatabaseIssueReport).all(): source_dismiss(db, issue)
    assert freshness.recheck_row(db, row)["disputed"] == []
    setup_readings(row, db, {SOURCE: 800, OTHER_SOURCE: 715})
    result = freshness.recheck_row(db, row)
    assert result["disputed"] == ["government_fee"] and held(db, row)
    assert not result["renewed"] and result["adjudicated_fields"] == []


def test_explicit_unresolved_source_warning_stays_held_beside_adjudicated_field(row_and_db, monkeypatch):
    from tests.test_freshness_dispute_lifecycle import WARNING
    row, db = row_and_db
    protect_fee(monkeypatch)
    row.guidance = {**row.guidance, "passport_validity": WARNING}
    db.commit()
    setup_readings(row, db, {SOURCE: 715, OTHER_SOURCE: 715})
    freshness.recheck_row(db, row)
    for issue in db.query(DatabaseIssueReport).all(): source_dismiss(db, issue)
    result = freshness.recheck_row(db, row)
    assert result["adjudicated_fields"] == ["government_fee"]
    assert result["disputed"] == ["passport_validity"] and held(db, row)
    assert row.guidance["passport_validity"] == WARNING
    assert not result["renewed"]
