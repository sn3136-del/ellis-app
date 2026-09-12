"""Unknown issuance format does not mean a physical sticker visa."""
from copy import deepcopy

import pytest

from app.visa_snapshot import tstation


ROUTE = {"passport_nationality": "CHN", "destination_country": "JPN",
         "travel_purpose": "tourism", "travel_document_type": "ordinary_passport"}


def guidance(product, parent_detail="evisa"):
    return {"disposition": "VISA_REQUIRED", "requirement_detail": parent_detail,
            "application_channel": "authorised_agent", "visa_products": [product],
            "source_url": "https://www.cn.emb-japan.go.jp/itpr_zh/visa_kanko.html"}


def reviewed_product(name="Individual single-entry tourist visa"):
    product = {"type": name, "disposition": "VISA_REQUIRED",
               "requirement_detail": None, "entry": "single",
               "validity": "3 months", "max_stay_days": 30,
               "fee": {"amount": 715, "currency": "CNY"},
               "application_channel": "embassy_designated_agency"}
    product["field_provenance"] = {"disposition": {
        "status": "reviewed", "verifier": "ai", "verified_at": "2026-09-12",
        "source_url": "https://www.cn.emb-japan.go.jp/itpr_zh/visa_kanko.html",
        "verified_by": "Fixture source review", "note": "Chinese tourist visa product reviewed.",
        "subject": dict(ROUTE, product_type=name, disposition="VISA_REQUIRED",
                        requirement_detail=None)}}
    return product


@pytest.mark.parametrize("name", ["Group tourist visa", "Individual single-entry tourist visa",
                                   "3-year multiple-entry tourist visa", "Visitor permit"])
@pytest.mark.parametrize("unknown", [None, "", "unknown", "Not publicly available"])
def test_explicit_unknown_is_not_displayed_as_paper_visa(name, unknown):
    product = reviewed_product(name)
    product["requirement_detail"] = unknown
    # These unknown strings intentionally do not constitute a supported
    # verdict-proof subtype; the display fix must not change that boundary.
    g = guidance(product)
    before = deepcopy(g)
    row = tstation.records_for_route(ROUTE, g)[0]
    assert row["visa_requirement"] == "Visa Required in Advance"
    assert row["visa_requirement_detail"] is None
    assert row["application_method"] == "Agency Service"
    assert (row["max_stay_duration"], row["validity_duration"], row["visa_fee_amount"]) == (30, 3, 715)
    assert g == before


@pytest.mark.parametrize("status", ["unknown", "not_published", "not-published"])
def test_unknown_field_proof_stops_generic_physical_inference(status):
    product = {"type": "Tourist visa", "field_provenance": {
        "requirement_detail": {"status": status}}}
    row = tstation.records_for_route(ROUTE, guidance(product))[0]
    assert row["visa_requirement_detail"] is None
    assert row["visa_requirement"] == "Visa Required in Advance"


@pytest.mark.parametrize("name,detail", [
    ("Electronic visa", "eVisa"), ("Tourist eVisa", "eVisa"),
    ("ETA", "ETA Electronic Authorization"),
    ("Visa on arrival", "Paper Visa on Arrival"),
    ("eVisa on arrival", "eVisa on Arrival"),
    ("Sticker visa", "Paper Visa"), ("Physical visa", "Paper Visa"),
    ("Tourist visa vignette", "Paper Visa"), ("Paper visa", "Paper Visa"),
    ("Paper visa, single entry", "Paper Visa"),
])
def test_explicit_named_permission_format_is_preserved(name, detail):
    p = {"type": name, "requirement_detail": None}
    g = guidance(p)
    if "arrival" in name.lower():
        g.update(disposition="VISA_ON_ARRIVAL", requirement_detail="paper_visa_on_arrival")
    row = tstation.records_for_route(ROUTE, g)[0]
    assert row["visa_requirement_detail"] == detail


@pytest.mark.parametrize("detail,label", list(tstation.SUBCATEGORY.items()))
def test_known_structured_subtype_is_not_removed(detail, label):
    product = {"type": "Tourist visa", "requirement_detail": detail,
               "field_provenance": {"requirement_detail": {"status": "unknown"}}}
    g = guidance(product, detail)
    if detail.endswith("visa_free"):
        g["disposition"] = "VISA_EXEMPT" if detail == "unconditional_visa_free" else "CONDITIONAL"
    elif detail.endswith("on_arrival"):
        g["disposition"] = "VISA_ON_ARRIVAL"
    elif detail == "eta_electronic_authorization":
        g["disposition"] = "ELECTRONIC_AUTHORIZATION_REQUIRED"
    # The supported enum owns the rendered field even if old unknown metadata remains.
    assert tstation.records_for_route(ROUTE, g)[0]["visa_requirement_detail"] == label


@pytest.mark.parametrize("disputed", [[], ["permitted_stay_days"]])
@pytest.mark.parametrize("reviewed", [True, False])
def test_unknown_display_does_not_reuse_parent_evidence_or_change_hold(disputed, reviewed):
    p = reviewed_product()
    if not reviewed:
        p.pop("field_provenance")
    old_shape = deepcopy(p)
    old_shape.pop("requirement_detail")
    before = tstation.records_for_route(ROUTE, guidance(old_shape), disputed_fields=disputed)[0]
    after = tstation.records_for_route(ROUTE, guidance(p), disputed_fields=disputed)[0]
    assert before["visa_requirement_detail"] == "Paper Visa"
    assert after["visa_requirement_detail"] is None
    assert after["_evidence_low"] == before["_evidence_low"]
    assert after["_separate_permission"] == before["_separate_permission"]
    assert after["_product_source_verified"] == before["_product_source_verified"]
    assert {k: v for k, v in before.items() if k != "visa_requirement_detail"} == {
        k: v for k, v in after.items() if k != "visa_requirement_detail"}


@pytest.mark.parametrize("name", [
    "Tourist visa, not a paper visa", "Tourist visa without a sticker visa",
    "Tourist visa (paper visa is not required)", "Tourist visa (paper visa not issued)",
    "Tourist visa (a visa sticker is never provided)", "Tourist visa (visa sticker-free)",
])
def test_negated_physical_name_does_not_establish_paper(name):
    product = reviewed_product(name)
    assert tstation._named_physical_visa(product) is False
    row = tstation.records_for_route(ROUTE, guidance(product))[0]
    assert row["visa_requirement_detail"] is None
    assert row["visa_requirement"] == "Visa Required in Advance"


def test_absent_subtype_legacy_record_is_outside_this_bounded_change():
    assert tstation.records_for_route(ROUTE, guidance({"type": "Tourist visa"}))[0]["visa_requirement_detail"] == "Paper Visa"
