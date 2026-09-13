"""Explicit absence of an entry requirement differs from missing information."""
import pytest
from app.visa_snapshot.evidence_validator import field_value_supported
from app.visa_snapshot.freshness import _quoted_proposals

CARD = {"required": False, "name": None, "submission_window": None}
ROUTE = {"passport_nationality": "GBR", "destination_country": "IRL", "travel_purpose": "tourism", "travel_document_type": "ordinary_passport"}


@pytest.mark.parametrize("field,value,quote", [
    ("insurance_required", False, "Travel insurance is not required."),
    ("insurance_required", False, "Insurance is not required."),
    ("insurance_required", False, "Medical insurance is not mandatory."),
    ("insurance_required", False, "No travel medical insurance is required."),
    ("insurance_required", False, "You do not need travel insurance."),
    ("arrival_card", CARD, "An arrival card is not required."),
    ("arrival_card", CARD, "No arrival card is required."),
    ("arrival_card", CARD, "Arrival cards are not required."),
    ("arrival_card", CARD, "No arrival cards are required."),
    ("arrival_card", CARD, "You do not need to complete an arrival card."),
    ("arrival_card", CARD, "There is no arrival card requirement."),
])
def test_explicit_unqualified_negative_can_be_extracted_and_verified(field, value, quote):
    assert field_value_supported(field, value, quote)
    answer = {"corrected_fields": {field: value}, "evidence": {field: quote}}
    quoted, evidence, unquoted = _quoted_proposals(answer, quote, ROUTE)
    assert quoted == {field: value} and evidence[field] == quote and unquoted == []


@pytest.mark.parametrize("field,value,quote", [
    ("insurance_required", False, "Travel insurance information is not available."),
    ("insurance_required", False, "Travel insurance is required."),
    ("insurance_required", False, "Travel insurance is not required for children."),
    ("insurance_required", False, "If you are covered by a reciprocal agreement, travel insurance is not required."),
    ("insurance_required", False, "Travel insurance is not required for the visa application."),
    ("insurance_required", False, "Travel insurance is not required online."),
    ("insurance_required", False, "Travel insurance is not required on arrival."),
    ("insurance_required", False, "Travel insurance is not required. Medical insurance is mandatory."),
    ("insurance_required", False, "Travel insurance is not required, except for long stays."),
    ("insurance_required", False, "It is not true that travel insurance is not required."),
    ("insurance_required", False, "British citizens do not need travel insurance."),
    ("insurance_required", False, "From 2027, travel insurance is not required."),
    ("insurance_required", False, "Life insurance is not required."),
    ("arrival_card", CARD, "Arrival card information is not published."),
    ("arrival_card", CARD, "An arrival card is required."),
    ("arrival_card", CARD, "An arrival card is not required for residents."),
    ("arrival_card", CARD, "An arrival card is not required when arriving from the UK."),
    ("arrival_card", CARD, "An arrival card is not required online."),
    ("arrival_card", CARD, "An arrival card is not required at the border; complete it before departure."),
    ("arrival_card", CARD, "A digital arrival card is not required; a paper arrival card is mandatory."),
    ("arrival_card", CARD, "A departure card is not required."),
    ("arrival_card", CARD, "No arrival card fee is required."),
    ("arrival_card", CARD, "No arrival card is required. Visitors must submit an arrival card."),
    ("arrival_card", CARD, "An arrival card was not required."),
    ("arrival_card", CARD, "An arrival card is not required after 1 January 2027."),
])
def test_missing_conditional_stage_or_contrary_statement_does_not_prove_false(field, value, quote):
    assert not field_value_supported(field, value, quote)
    quoted, _, unquoted = _quoted_proposals({"corrected_fields": {field: value}, "evidence": {field: quote}}, quote, ROUTE)
    assert quoted == {} and field in unquoted


@pytest.mark.parametrize("value", [
    {"required": False, "name": None, "submission_window": "within 3 days before arrival"},
    {"required": False, "name": "Electronic Travel Authorization", "submission_window": None},
    {"required": False, "name": None, "submission_window": None, "for_children_only": True},
])
def test_card_negative_cannot_verify_additional_unproven_fields(value):
    assert not field_value_supported("arrival_card", value, "An arrival card is not required.")


def test_negative_does_not_support_true_or_borrow_unrelated_boolean():
    assert not field_value_supported("insurance_required", True, "Travel insurance is not required.")
    assert not field_value_supported("arrival_card", {**CARD, "required": True}, "An arrival card is not required.")
    assert not field_value_supported("biometrics_required", False, "Travel insurance is not required.")
    assert not field_value_supported("insurance_required", False, "An arrival card is not required.")


def test_missing_quote_is_still_unverified():
    quoted, _, unquoted = _quoted_proposals({"corrected_fields": {"insurance_required": False}, "evidence": {"insurance_required": "Travel insurance is not required."}}, "Visitors need a valid passport.", ROUTE)
    assert quoted == {} and unquoted == ["insurance_required"]


@pytest.mark.parametrize("field,value,quote", [
    ("insurance_required", True, "Travel insurance is required."),
    ("insurance_required", True, "Medical insurance is mandatory."),
    ("insurance_required", True, "You need travel insurance."),
    ("arrival_card", {**CARD, "required": True}, "An arrival card is required."),
    ("arrival_card", {**CARD, "required": True}, "You must complete an arrival card."),
    ("arrival_card", {**CARD, "required": True}, "Arrival cards are mandatory."),
])
def test_matching_positive_is_recognized_for_confirmation_and_conflict(field, value, quote):
    assert field_value_supported(field, value, quote)


@pytest.mark.parametrize("field,value,quote", [
    ("insurance_required", True, "Travel insurance is required only for students."),
    ("insurance_required", True, "Travel insurance is required for the visa application."),
    ("insurance_required", True, "Travel insurance is required. Travel insurance is not required."),
    ("insurance_required", True, "From 2027, travel insurance is required."),
    ("arrival_card", {**CARD, "required": True}, "An arrival card is required unless arriving from the UK."),
    ("arrival_card", {**CARD, "required": True}, "An arrival card is required for children."),
    ("arrival_card", {**CARD, "required": True}, "It is not true that an arrival card is required."),
    ("arrival_card", {**CARD, "required": True}, "A digital arrival card is required only online."),
])
def test_positive_uses_the_same_condition_and_contradiction_boundaries(field, value, quote):
    assert not field_value_supported(field, value, quote)


@pytest.mark.parametrize("field", ["insurance_required", "arrival_card"])
@pytest.mark.parametrize("initial,readings", [(True, (False, True)), (False, (True, False)), (False, (True, True))])
def test_real_recheck_preserves_opposing_official_rules_and_corrects_only_agreement(monkeypatch, field, initial, readings):
    import json
    from copy import deepcopy
    from urllib.parse import urlsplit
    from tests.test_freshness_dispute_lifecycle import row_and_db, SOURCE, OTHER_SOURCE
    from app.visa_snapshot import freshness, fetching
    from app.visa_snapshot.fetching import FetchResult
    from app.visa_snapshot.models import DatabaseIssueReport
    fixture = row_and_db.__wrapped__(monkeypatch)
    row, db = next(fixture)
    def value(required):
        return required if field == "insurance_required" else {**CARD, "required": required, "name": "Arrival card"}
    try:
        original = value(initial)
        row.guidance = {**row.guidance, field: deepcopy(original), "corroborating_sources": [{"url": OTHER_SOURCE}]}
        db.commit()
        terms = dict(zip((SOURCE, OTHER_SOURCE), readings))
        topic = "Travel insurance" if field == "insurance_required" else "An arrival card"
        quotes = {url: topic + (" is required." if flag else " is not required.") for url, flag in terms.items()}
        fetching.set_fetcher(lambda url, **kwargs: FetchResult(requested_url=url, final_url=url,
            final_hostname=urlsplit(url).hostname, ok=True, http_status=200,
            content_text="Chinese citizens need a visa to visit Japan. " + quotes[url], content_hash=quotes[url], retrieved_at="2026-09-13"))
        def compare(_, payload):
            url = json.loads(payload)["official_page_url"]
            same = terms[url] == initial
            return {"page_relevant": True, "page_is_nationality_specific": True, "consistent": same,
                    "corrected_fields": {} if same else {field: value(terms[url])}, "evidence": {field: quotes[url]}}
        freshness.set_provider(compare)
        result = freshness.recheck_row(db, row)
        if len(set(readings)) > 1:
            assert row.guidance[field] == original
            assert result["changed"] == [] and result["disputed"] == [field]
            assert freshness.active_disputed_fields(db, row.cache_key) == [field]
            assert db.query(DatabaseIssueReport).count() >= 1
            assert not result["renewed"]
        else:
            assert row.guidance[field] == value(True)
            assert result["changed"] == [field] and result["disputed"] == []
    finally:
        try:
            next(fixture)
        except StopIteration:
            pass
