"""The corrected gate reads what a finding reported from its field column.

The column is a 64 character display string, so a finding naming several
fields can have its last name cut mid word. The finding's proposal carries
the names in full, but it can also name more fields than the column reported
(the cut dropped whole names after a comma, or a reader flagged one field and
the research proposal named others). The gate therefore keeps the column's
tokens and reads the proposal only to complete a trailing token the cut
truncated, when exactly one proposal field name extends it. It never demands
a field only the proposal names and never closes a finding on one.

Every case runs through the real endpoint with real change-log rows.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.visa_snapshot import kimi_primary
from app.visa_snapshot.models import DatabaseChangeLog, DatabaseIssueReport

ADMIN = {"authorization": "Bearer admin-token", "x-org-id": "gate-review",
         "x-user-id": "reviewer"}
KEY = "HKG|HKG|TUV|tourism|default|unknown|v6"
ROUTE = {"passport_nationality": "HKG", "destination_country": "TUV",
         "travel_purpose": "tourism", "travel_document_type": "ordinary_passport"}
WIDTH = DatabaseIssueReport.__table__.c.field.type.length


def _finding(db, column, proposal_fields, *, status="acknowledged", reported_by="freshness_monitor",
             resolved_by=""):
    proposal = None
    if proposal_fields is not None:
        proposal = {"fields": {name: {"page_says": "x", "record_holds": "y"}
                               for name in proposal_fields}}
    row = DatabaseIssueReport(
        cache_key=KEY, route=ROUTE, field=column, note="check this",
        reported_by=reported_by, status=status, resolved_by=resolved_by,
        proposal=proposal, created_at=datetime.now(timezone.utc) - timedelta(minutes=5))
    db.add(row)
    db.commit()
    return row.id


def _logged(db, *names, origin="operator-edit"):
    db.add(DatabaseChangeLog(
        cache_key=kimi_primary.canonical_key(KEY), route=ROUTE, action="modify",
        origin=origin, changes={n: {"from": "old", "to": "new"} for n in names},
        note="sourced correction", created_at=datetime.now(timezone.utc)))
    db.commit()


def _advance(client, issue_id, status="corrected"):
    return client.post(f"/database/issues/{issue_id}", headers=ADMIN,
                       json={"status": status, "resolution": "sourced correction applied"})


def _clear(db):
    db.query(DatabaseChangeLog).filter_by(cache_key=kimi_primary.canonical_key(KEY)).delete()
    db.query(DatabaseIssueReport).filter_by(cache_key=KEY).delete()
    db.commit()


@pytest.fixture(autouse=True)
def _own_route(db):
    _clear(db)
    yield
    _clear(db)


def test_a_reader_flag_cannot_close_on_a_field_only_the_proposal_names(client, db):
    """F1. A reader flagged the fee. The research proposal named
    processing_time instead. Fixing processing_time does not fix the fee the
    reader saw, so the finding stays open."""
    issue = _finding(db, "visa_fee_amount", ["processing_time"], reported_by="reader-1")
    _logged(db, "processing_time")
    r = _advance(client, issue)
    assert r.status_code == 422, r.text
    assert "apply a sourced correction" in r.json()["detail"]
    db.expire_all()
    assert db.get(DatabaseIssueReport, issue).status == "acknowledged"


def test_a_wider_proposal_never_adds_a_requirement_the_finding_did_not_report(client, db):
    """F2. The reader flagged the fee, the proposal named the fee and
    processing_time, the operator corrected the fee. That is the correction
    the finding asked for."""
    issue = _finding(db, "government_fee", ["government_fee", "processing_time"],
                     reported_by="reader-1")
    _logged(db, "government_fee")
    r = _advance(client, issue)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "corrected"


def test_a_reported_field_the_proposal_renamed_is_still_the_one_that_must_be_fixed(client, db):
    """F2 mirror. The reader flagged the fee under its display alias and the
    proposal named only processing_time. A sourced fee edit closes it."""
    issue = _finding(db, "visa_fee_amount", ["processing_time"], reported_by="reader-1")
    _logged(db, "government_fee")
    assert _advance(client, issue).status_code == 200


def test_a_live_finding_whose_cut_fell_on_a_comma_can_still_be_reviewed(client, db):
    """F3, the live row 026fa4b199464857809a8b7dd871c369. The column holds
    three complete names and is exactly 64 characters because the cut fell
    on the comma before permitted_stay and permitted_stay_days. The page and
    the record agree on permitted_stay_days, so no honest edit can ever log
    it. The gate runs again on the way to reviewed and must not demand it."""
    column = "accommodation_evidence,financial_evidence,onward_travel_evidence"
    assert len(column) == WIDTH
    issue = _finding(db, column, ["accommodation_evidence", "financial_evidence",
                                  "onward_travel_evidence", "permitted_stay",
                                  "permitted_stay_days"],
                     status="corrected", resolved_by="author")
    _logged(db, "accommodation_evidence", "financial_evidence", "onward_travel_evidence",
            "permitted_stay")
    r = _advance(client, issue, "reviewed")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "reviewed"


def test_a_trailing_token_cut_mid_word_is_completed_from_the_proposal(client, db):
    """The release's target case. The column ends in passport_validity_requirem,
    a name no change-log row can carry. The proposal names it in full and
    that full name is what the gate asks for."""
    column = "official_portal_url,passport_validity,passport_validity_requirem"
    assert len(column) == WIDTH
    proposal = ["official_portal_url", "passport_validity", "passport_validity_requirement"]
    issue = _finding(db, column, proposal)
    _logged(db, "official_portal_url", "passport_validity")
    assert _advance(client, issue).status_code == 422, "the completed name is still required"
    _logged(db, "passport_validity_requirement", origin="grounded_recheck")
    r = _advance(client, issue)
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("column, proposal, logged", [
    # A short column: the proposal adds two names the column never carried.
    ("government_fee",
     ["government_fee", "processing_time", "permitted_stay_days"],
     ["government_fee"]),
    # A full-width column ending in a complete name: the cut dropped whole
    # names after a comma and the proposal still carries them.
    ("accommodation_evidence,financial_evidence,onward_travel_evidence",
     ["accommodation_evidence", "financial_evidence", "onward_travel_evidence",
      "permitted_stay", "permitted_stay_days"],
     ["accommodation_evidence", "financial_evidence", "onward_travel_evidence"]),
    # A full-width column ending in a truncated name: the completed name is
    # required, the names the cut dropped after it are not.
    ("official_portal_url,passport_validity,passport_validity_requirem",
     ["official_portal_url", "passport_validity", "passport_validity_requirement",
      "processing_time"],
     ["official_portal_url", "passport_validity", "passport_validity_requirement"]),
])
def test_the_gate_never_requires_a_field_only_the_proposal_names(client, db, column, proposal, logged):
    issue = _finding(db, column, proposal)
    _logged(db, *logged)
    r = _advance(client, issue)
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("column, proposal", [
    # The cut left a token that prefixes two proposal names: the finding
    # cannot say which one it reported.
    ("application_channel,application_channel_detail,permitted_stay,pe",
     ["application_channel", "application_channel_detail", "permitted_stay",
      "permitted_stay_days"]),
    # The cut left a token no proposal name extends.
    ("official_portal_url,passport_validity,passport_validity_requirem",
     ["official_portal_url", "passport_validity", "processing_time"]),
    # No proposal at all: nothing can complete the token.
    ("official_portal_url,passport_validity,passport_validity_requirem", None),
])
def test_an_ambiguous_or_unmatched_cut_keeps_the_column_token_and_refuses(client, db, column, proposal):
    """Even when every field the proposal names has been corrected, a token
    the proposal cannot identify is kept as it is and the gate refuses."""
    assert len(column) == WIDTH
    issue = _finding(db, column, proposal)
    _logged(db, "application_channel", "application_channel_detail", "permitted_stay",
            "permitted_stay_days", "official_portal_url", "passport_validity",
            "passport_validity_requirement", "processing_time")
    r = _advance(client, issue)
    assert r.status_code == 422, r.text


def test_a_short_column_is_never_completed_even_when_a_proposal_name_extends_it(client, db):
    """Only a full-width column can have been cut. A shorter column that
    happens to prefix a proposal name is a complete report of its own."""
    issue = _finding(db, "permitted_stay", ["permitted_stay_days"])
    _logged(db, "permitted_stay_days")
    assert _advance(client, issue).status_code == 422
    _logged(db, "permitted_stay")
    assert _advance(client, issue).status_code == 200


def test_a_whole_answer_finding_accepts_any_sourced_correction(client, db):
    """A finding on ai_answer names no field. Any sourced correction after it
    counts, and the proposal's field names do not narrow that."""
    issue = _finding(db, "ai_answer", ["processing_time"], reported_by="reader-1")
    assert _advance(client, issue).status_code == 422, "no correction at all"
    _logged(db, "government_fee")
    assert _advance(client, issue).status_code == 200
