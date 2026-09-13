"""Qualified processing wording reaches QC without becoming an invented minimum."""
from copy import deepcopy

import pytest

from app import main
from app.visa_snapshot import tstation
from tests.test_tstation_backend import ANSWER

ROUTE = {"passport_nationality": "CHN", "destination_country": "JPN", "travel_purpose": "tourism"}


def project(text, products=None, **extras):
    guidance = dict(deepcopy(ANSWER), processing_time=text, **extras)
    if products is not None:
        guidance["visa_products"] = products
    rows = tstation.records_for_route(ROUTE, guidance, None)
    for row in rows:
        row["_cache_key"] = "test"
    return rows


@pytest.mark.parametrize("text,minimum,unit", [
    ("within 24 hours", None, None),
    ("5–10 working days; additional checks may take longer", 5, "Working Day"),
    ("3 working days", 3, "Working Day"),
    ("Normally three working days", 3, "Working Day"),
    ("Up to 6 weeks", None, None),
    ("Usually 2 weeks, but complex applications can take 3 months", None, None)])
def test_exact_processing_wording_is_visible_without_new_numeric_inference(text, minimum, unit):
    row = project(text)[0]
    payload = main._record_payload(row)
    assert row["processing_text"] == payload["processing_text"] == text
    assert (row["processing_min_days"], row["processing_unit"]) == (minimum, unit)
    assert payload["visa_fee_amount"] == 100 and payload["visa_fee_currency"] == "USD"
    assert "processing_text" not in tstation.FIELD_ORDER


@pytest.mark.parametrize("text", [None, "", "Unknown", "Not publicly available", "Not applicable",
    "Not published by the consulate", "Not verified, usually 5 working days"])
def test_absence_or_unverified_guess_is_not_promoted_to_processing_value(text):
    row = project(text)[0]
    assert main._record_payload(row)["processing_text"] is None


def test_product_scoped_timing_does_not_leak_to_siblings():
    products = deepcopy(ANSWER["visa_products"])
    products[0]["processing_time"] = "Within 24 hours"
    rows = project("Priority: 2 working days; standard: 10 working days", products)
    assert rows[0]["processing_text"] == "Within 24 hours"
    assert rows[1]["processing_text"] is None
    assert "product scope must be checked" in rows[1]["special_conditions"]


def test_separate_permission_and_exempt_lane_do_not_inherit_visa_timing():
    products = [{"type": "Optional eVisa", "requirement_detail": "evisa", "entry": "single",
                 "validity": "90 days", "max_stay_days": 30, "fee": {"amount": 25, "currency": "USD"}}]
    row = project("within 24 hours", products, requirement_detail="paper_visa")[0]
    assert row["_separate_permission"] and row["processing_text"] is None
    exempt = project("within 24 hours", [], disposition="VISA_EXEMPT", application_channel="not_required")[0]
    assert exempt["processing_text"] is None


def test_explicit_unpublished_tag_keeps_processing_label():
    row = project("within 24 hours", unpublished_fields=["processing_time"])[0]
    assert main._record_payload(row)["processing_text"] is None
