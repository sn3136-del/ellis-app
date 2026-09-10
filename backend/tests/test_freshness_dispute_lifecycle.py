"""Source disagreement and operator decisions survive subsequent sweeps."""
from copy import deepcopy
import json
from urllib.parse import urlsplit

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base
from app.visa_snapshot import fetching, freshness, kimi_primary, verified_overrides
from app.visa_snapshot.fetching import FetchResult
from app.visa_snapshot.models import DatabaseIssueReport, KimiRouteGuidanceCache


SOURCE = "https://www.mofa.go.jp/visa/fees"
OTHER_SOURCE = "https://www.mofa.go.jp/visa/tourism"
ROUTE = {"passport_nationality": "CHN", "destination_country": "JPN",
         "travel_document_type": "ordinary_passport", "travel_purpose": "tourism"}
GUIDANCE = {
    "disposition": "VISA_REQUIRED", "visa_category": "Temporary visitor",
    "permitted_stay": "90 days", "passport_validity": "valid for the stay",
    "required_documents": ["passport"], "application_channel": "authorised_agent",
    "application_channel_detail": "Apply through an accredited travel agency.",
    "government_fee": {"amount": 200, "currency": "CNY"},
    "processing_time": "5 working days", "confidence": "high",
    "source_url": SOURCE, "visa_products": [{"type": "Single-entry Temporary Visitor",
        "entry": "single", "validity": "3 months", "max_stay_days": 90,
        "fee": {"amount": 200, "currency": "CNY"}, "notes": None}],
}


@pytest.fixture
def row_and_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    monkeypatch.setattr(verified_overrides, "find", lambda _: None)
    monkeypatch.setattr(verified_overrides, "apply", lambda g, _: (deepcopy(g), {}))
    row = KimiRouteGuidanceCache(cache_key=kimi_primary.cache_key(ROUTE), route=ROUTE,
                                guidance=deepcopy(GUIDANCE), status="KIMI_PRIMARY")
    db.add(row)
    db.commit()
    yield row, db
    freshness.set_provider(None)
    fetching.set_fetcher(None)
    db.close()
    engine.dispose()


def file_fee(db, row, *, amount=715, source=SOURCE, quote=None, when="2026-09-09"):
    freshness._file_dispute(db, row, ROUTE, row.guidance,
        {"government_fee": {"amount": amount, "currency": "CNY"}},
        {"government_fee": quote or f"The visa fee is {amount} CNY."}, source, when)
    db.commit()


def dismiss(issue, db):
    issue.status = "dismissed"
    issue.resolution = "This official fee applies to a different consular service."
    issue.resolved_by = "operator-test"
    db.commit()


def test_dismissed_unchanged_source_proposal_does_not_reopen(row_and_db):
    row, db = row_and_db
    file_fee(db, row)
    issue = db.query(DatabaseIssueReport).one()
    original_proposal = deepcopy(issue.proposal)
    dismiss(issue, db)
    file_fee(db, row, when="2026-09-10", quote="The visa fee is 715\nCNY.")
    assert db.query(DatabaseIssueReport).count() == 1
    assert issue.status == "dismissed" and issue.resolved_by == "operator-test"
    assert issue.proposal == original_proposal
    assert freshness.active_disputed_fields(db, row.cache_key) == []


@pytest.mark.parametrize("change", ["amount", "source", "quote", "stored_value"])
def test_dismissal_never_hides_new_evidence_or_changed_record(row_and_db, change):
    row, db = row_and_db
    file_fee(db, row)
    dismiss(db.query(DatabaseIssueReport).one(), db)
    kwargs = {}
    if change == "amount":
        kwargs["amount"] = 800
    elif change == "source":
        kwargs["source"] = OTHER_SOURCE
    elif change == "quote":
        kwargs["quote"] = "From 1 October the visa fee is 715 CNY."
    else:
        row.guidance = {**row.guidance, "government_fee": {"amount": 300, "currency": "CNY"}}
        db.commit()
    file_fee(db, row, **kwargs)
    assert db.query(DatabaseIssueReport).count() == 2
    assert freshness.active_disputed_fields(db, row.cache_key) == ["government_fee"]


def test_open_proposals_preserve_each_official_source(row_and_db):
    row, db = row_and_db
    file_fee(db, row)
    file_fee(db, row, source=OTHER_SOURCE, amount=800)
    file_fee(db, row, when="2026-09-10")
    issues = db.query(DatabaseIssueReport).all()
    assert len(issues) == 2
    assert {i.proposal["source_url"]: i.proposal["fields"]["government_fee"]["page_says"]["amount"]
            for i in issues} == {SOURCE: 715, OTHER_SOURCE: 800}


def setup_readings(row, db, fees):
    row.guidance = {**row.guidance, "corroborating_sources": [{"url": OTHER_SOURCE}]}
    db.commit()
    texts = {url: "Chinese nationals must obtain a visa for tourism in Japan. "
                  f"The visa fee is {amount} CNY." for url, amount in fees.items()}

    def fetch(url, **_):
        return FetchResult(requested_url=url, final_url=url,
            final_hostname=urlsplit(url).hostname, ok=True, http_status=200,
            content_text=texts[url], content_hash=str(fees[url]), retrieved_at="2026-09-10")

    def compare(_, payload):
        url = json.loads(payload)["official_page_url"]
        amount = fees[url]
        changed = amount != GUIDANCE["government_fee"]["amount"]
        return {"page_relevant": True, "page_is_nationality_specific": True,
                "consistent": not changed,
                "corrected_fields": {"government_fee": {"amount": amount, "currency": "CNY"}} if changed else {},
                "evidence": {"government_fee": f"The visa fee is {amount} CNY."}}

    fetching.set_fetcher(fetch)
    freshness.set_provider(compare)


@pytest.mark.parametrize("fees", [{SOURCE: 200, OTHER_SOURCE: 715}, {SOURCE: 715, OTHER_SOURCE: 200}])
def test_confirmed_unchanged_value_blocks_conflicting_automatic_correction(row_and_db, fees):
    row, db = row_and_db
    setup_readings(row, db, fees)
    result = freshness.recheck_row(db, row)
    assert row.guidance["government_fee"] == GUIDANCE["government_fee"]
    assert result["changed"] == [] and result["disputed"] == ["government_fee"]
    assert row.verification["grounded_check"]["renewed"] is False
    assert freshness.active_disputed_fields(db, row.cache_key) == ["government_fee"]
    issue = db.query(DatabaseIssueReport).one()
    assert {item["source_url"]: item["value"]["amount"]
            for item in issue.proposal["conflicting_evidence"]} == fees


def test_conflicting_proposed_values_preserve_both_sources(row_and_db):
    row, db = row_and_db
    fees = {SOURCE: 715, OTHER_SOURCE: 800}
    setup_readings(row, db, fees)
    result = freshness.recheck_row(db, row)
    assert row.guidance["government_fee"] == GUIDANCE["government_fee"]
    assert result["changed"] == [] and result["disputed"] == ["government_fee"]
    issues = db.query(DatabaseIssueReport).all()
    assert {i.proposal["source_url"]: i.proposal["fields"]["government_fee"]["page_says"]["amount"]
            for i in issues} == fees


def test_agreeing_current_sources_still_allow_a_grounded_correction(row_and_db):
    row, db = row_and_db
    setup_readings(row, db, {SOURCE: 715, OTHER_SOURCE: 715})
    result = freshness.recheck_row(db, row)
    assert row.guidance["government_fee"] == {"amount": 715, "currency": "CNY"}
    assert result["changed"] == ["government_fee"] and result["disputed"] == []
    assert db.query(DatabaseIssueReport).count() == 0


WARNING = ("Official Korean consular guidance conflicts on remaining passport validity at entry. "
           "Confirm the applicable requirement before travel; visa-application requirements are separate.")
VALIDITY_QUOTE = "Your passport must be valid for the entire duration of your stay."


def setup_warning_reading(row, db):
    row.guidance = {**row.guidance, "passport_validity": WARNING}
    db.commit()
    text = "Chinese nationals must obtain a visa for tourism in Japan. " + VALIDITY_QUOTE
    fetching.set_fetcher(lambda url, **_: FetchResult(requested_url=url, final_url=url,
        final_hostname=urlsplit(url).hostname, ok=True, http_status=200,
        content_text=text, content_hash="unchanged-passport-page", retrieved_at="2026-09-10"))
    freshness.set_provider(lambda *_: {"page_relevant": True, "page_is_nationality_specific": True,
        "consistent": False, "corrected_fields": {"passport_validity": "Valid for the entire duration of your stay"},
        "evidence": {"passport_validity": VALIDITY_QUOTE}})


def test_one_source_cannot_replace_or_verify_away_a_conflict_warning(row_and_db):
    row, db = row_and_db
    setup_warning_reading(row, db)
    for _ in range(2):
        result = freshness.recheck_row(db, row)
        assert row.guidance["passport_validity"] == WARNING
        assert result["changed"] == [] and result["disputed"] == ["passport_validity"]
        check = row.verification["grounded_check"]
        assert not check["renewed"] and "passport_validity" not in check["verified_fields"]
        assert check["awaiting_adjudication_fields"] == ["passport_validity"]
        assert check["source_checks"][0]["awaiting_adjudication"]["passport_validity"]["quote"] == VALIDITY_QUOTE
    # Keep the actual conflict visible without asserting that one official
    # page refutes the warning that other official pages disagree with it.
    assert db.query(DatabaseIssueReport).count() == 0
    from app.visa_snapshot.records_guard import apply_records_hold
    guarded = apply_records_hold(ROUTE, {"guidance": row.guidance,
        "grounded_check": row.verification["grounded_check"], "operator_released": True}, db)
    assert guarded["held"] and guarded["review_required"]


def test_human_issue_proposal_also_preserves_a_conflict_warning(row_and_db):
    row, db = row_and_db
    setup_warning_reading(row, db)
    issue = DatabaseIssueReport(cache_key=row.cache_key, route=ROUTE,
        reported_by="reader", field="passport_validity", status="open")
    db.add(issue)
    db.commit()
    proposal = freshness.propose_for_issue(db, issue.id)
    assert proposal["fields"] == {} and proposal["verified_fields"] == []
    assert proposal["awaiting_adjudication"]["passport_validity"]["quote"] == VALIDITY_QUOTE
    assert proposal["consistent"] is False
    assert row.guidance["passport_validity"] == WARNING


@pytest.mark.parametrize("value", [
    "Passport must be valid for six months.",
    "No conflicting official guidance was found; validity through departure is required.",
    "Official guidance does not conflict on passport validity.",
    "Official guidance conflicts were resolved by the ministry's current guidance.",
])
def test_settled_requirements_do_not_become_uncertainty_warnings(value):
    assert freshness._source_conflict_fields({"passport_validity": value}) == set()
