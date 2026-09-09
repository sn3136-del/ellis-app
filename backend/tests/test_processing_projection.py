"""The day-only processing contract must not turn hours or scopes into days."""
from copy import deepcopy

import pytest

from app.visa_snapshot import tstation

ROUTE = {"passport_nationality": "CHN", "destination_country": "FRA", "travel_purpose": "study"}
SHORT = {"type": "Short-stay study visa (Schengen C)", "fee": {"amount": 90, "currency": "EUR"}}
LONG = {"type": "Long-stay student visa (VLS-TS)", "fee": {"amount": 99, "currency": "EUR"}}
MIXED = ("Short-stay: 24 or 48 working hours for complete applications not requiring "
         "additional verification; long-stay: up to 2 weeks")


def records(text, products=None, **patch):
    guidance = {"disposition": "VISA_REQUIRED", "requirement_detail": "paper_visa",
                "processing_time": text, "visa_products": products if products is not None else [SHORT, LONG],
                "application_channel": "visa_center", "required_documents": ["Passport", "Application form"],
                "source_url": "https://france-visas.gouv.fr/", **patch}
    before = deepcopy(guidance)
    rows = tstation.records_for_route(ROUTE, guidance)
    assert guidance == before
    return rows


def test_actual_mixed_french_study_timing_never_becomes_48_working_days_for_both_products():
    rows = records(MIXED)
    assert len(rows) == 2
    for row in rows:
        assert (row["processing_min_days"], row["processing_unit"]) == (None, None)
        assert MIXED in row["special_conditions"]  # literal hours, conditions, punctuation and scope survive
        assert "product scope must be checked" in row["special_conditions"]
        assert row["visa_requirement"] == "Visa Required in Advance"
        assert row["application_method"] == "Agency Service"
        assert row["source_url"] == "https://france-visas.gouv.fr/"
        assert row["confidence_level"] == "Low"  # projection is not new proof
        assert tstation.field_status(row)["processing_min_days"] == "optional-empty"
        assert "_processing_note" not in row


@pytest.mark.parametrize("text", [
    "48 working hours", "24 or 48 working hours", "48 business hours", "48 hours", "12 hours",
    "2 working weeks", "2 business weeks", "2 calendar months", "one year", "within 15 working days",
    "up to 2 weeks", "at most 10 calendar days", "maximum of 15 days", "less than 5 days", "≤ 15 days",
    "5 working days or 10 calendar days", "5 days for standard and 2 days for priority",
    "No processing time is published", "5–3 working days", "-5 working days", "−5 calendar days",
    "This is not 5 working days", "Do not assume 5 calendar days",
])
def test_unrepresentable_or_ambiguous_timing_keeps_literal_text_without_a_minimum(text):
    row, = records(text, [SHORT])
    assert (row["processing_min_days"], row["processing_unit"]) == (None, None)
    assert text in row["special_conditions"]


@pytest.mark.parametrize("text,minimum,unit", [
    ("5 working days", 5, "Working Day"), ("5 business days", 5, "Working Day"),
    ("15 calendar days", 15, "Calendar Day"), ("0 calendar days", 0, "Calendar Day"),
    ("Usually 3 weeks", 21, "Calendar Day"), ("three (3) calendar weeks", 21, "Calendar Day"),
    ("5–15 working days", 5, "Working Day"), ("3 to 5 calendar days", 3, "Calendar Day"),
    ("2–4 weeks", 14, "Calendar Day"),
    ("15 calendar days. The office works during business hours.", 15, "Calendar Day"),
])
def test_only_local_day_or_calendar_week_units_map_to_the_day_contract(text, minimum, unit):
    row, = records(text, [SHORT])
    assert (row["processing_min_days"], row["processing_unit"]) == (minimum, unit)
    if text not in ("5 working days", "5 business days", "15 calendar days", "0 calendar days"):
        assert text in row["special_conditions"]


@pytest.mark.parametrize("text", ["Short-stay: 15 calendar days", "Children: 5 working days",
                                      "Priority applications: 3 working days"])
def test_one_category_specific_route_timing_is_not_copied_to_every_product(text):
    for row in records(text):
        assert row["processing_min_days"] is None
        assert text in row["special_conditions"]
        assert "product scope must be checked" in row["special_conditions"]


def test_product_timings_override_mixed_route_timing_without_borrowing_sibling_conditions():
    short, long = records(MIXED, [
        {**SHORT, "processing_time": "15 calendar days; may take longer if additional checks are needed"},
        {**LONG, "processing_time": "Case-specific; no numeric period published"}])
    assert (short["processing_min_days"], short["processing_unit"]) == (15, "Calendar Day")
    assert "additional checks" in short["special_conditions"]
    assert "24 or 48" not in short["special_conditions"]
    assert long["processing_min_days"] is None
    assert "Case-specific; no numeric period published" in long["special_conditions"]
    assert "24 or 48" not in long["special_conditions"]


def test_explicit_unknown_product_timing_clears_inherited_numeric_and_text():
    short, long = records(MIXED, [SHORT, {**LONG, "processing_time": None}])
    assert MIXED in short["special_conditions"]
    assert long["processing_min_days"] is None
    assert not long["special_conditions"]


def test_generic_timing_can_still_apply_to_multiple_fee_variants():
    adult, child = records("15 calendar days", [SHORT, {**SHORT, "type": "Short-stay study visa (child)"}])
    assert [row["processing_min_days"] for row in [adult, child]] == [15, 15]


def test_eta_timing_text_does_not_leak_to_separate_visitor_visa():
    eta, visitor = records("Usually 3 working days", [
        {"type": "Electronic Travel Authorisation (ETA)"}, {"type": "Standard Visitor visa"}],
        disposition="ELECTRONIC_AUTHORIZATION_REQUIRED", requirement_detail="eta_electronic_authorization")
    assert eta["processing_min_days"] == 3
    assert "Usually 3 working days" in eta["special_conditions"]
    assert visitor["processing_min_days"] is None
    assert not visitor["special_conditions"]


def test_visa_free_products_do_not_gain_a_visa_processing_instruction():
    row, = records("Usually 3 working days", [], disposition="VISA_EXEMPT",
                   requirement_detail="unconditional_visa_free", application_channel="not_required",
                   required_documents=["Passport"])
    assert row["processing_min_days"] is None
    assert not row["special_conditions"]
