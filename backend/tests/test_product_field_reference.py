"""A product-owned source link is distinct from verified visa eligibility."""
from copy import deepcopy

import pytest

from app.visa_snapshot import tstation


ROUTE = {"passport_nationality": "AUS", "destination_country": "IDN",
         "travel_purpose": "tourism", "travel_document_type": "ordinary_passport"}
URL = "https://www.imigrasi.go.id/wna/daftar-visa-indonesia/C1"


def guidance():
    product = {"type": "Single-entry visit visa (C1)", "disposition": "VISA_REQUIRED",
               "requirement_detail": "evisa", "entry": "single", "validity": "90 days",
               "max_stay_days": 60, "fee": {"amount": 1000000, "currency": "IDR"}}
    product["field_provenance"] = {"fee": {
        "status": "reviewed", "verifier": "ai", "verified_at": "2026-09-09",
        "source_url": URL, "quote": "Masa tinggal 60 hari (dapat diperpanjang) : Rp. 1.000.000",
        "subject": dict(ROUTE, product_type=product["type"], disposition=product["disposition"],
                        requirement_detail=product["requirement_detail"])}}
    return {"disposition": "VISA_ON_ARRIVAL", "requirement_detail": "evisa_on_arrival",
            "official_portal_url": "https://evisa.imigrasi.go.id/front/info/evoa",
            "visa_products": [product]}


def test_product_field_reference_retains_link_without_verification_or_dates():
    g = guidance()
    before = deepcopy(g)
    row = tstation.records_for_route(ROUTE, g, collected_at="2026-09-09", valid_until="2026-10-09")[0]
    assert row["source_url"] == URL
    assert row["data_source"] == "Ellis product field source (reference only)"
    assert row["_product_source_verified"] is None and row.get("_prov") is None
    assert not row.get("_grounded") and row["confidence_level"] == "Low"
    assert row["collected_at"] is None and row["info_validity"] is None
    assert row["required_documents"] is None
    assert not tstation.verdict_provenance_supported(row.get("_prov"))
    assert g == before


@pytest.mark.parametrize("change", ["nationality", "destination", "purpose", "document", "product", "disposition",
                                    "detail", "unscoped", "unreviewed", "public", "no_quote", "unofficial", "foreign"])
def test_unowned_or_unreviewed_field_links_do_not_fill_a_product_source(change):
    g = guidance()
    proof = g["visa_products"][0]["field_provenance"]["fee"]
    keys = {"nationality": "passport_nationality", "destination": "destination_country",
            "purpose": "travel_purpose", "document": "travel_document_type", "product": "product_type",
            "disposition": "disposition", "detail": "requirement_detail"}
    if change in keys:
        proof["subject"][keys[change]] = "different subject"
    elif change == "unscoped":
        proof.pop("subject")
    elif change == "unreviewed":
        proof["status"] = "unknown"
    elif change == "public":
        proof["verifier"] = "public"
    elif change == "no_quote":
        proof["quote"] = ""
    elif change == "unofficial":
        proof["source_url"] = "https://example.com/indonesia/C1"
    else:
        proof["source_url"] = "https://www.immd.gov.hk/eng/services/visas/visit-transit.html"
    row = tstation.records_for_route(ROUTE, g)[0]
    assert row["source_url"] is None
    assert row["confidence_level"] == "Low" and row.get("_prov") is None


def test_an_explicit_product_source_still_wins_over_a_field_reference():
    g = guidance()
    own = "https://evisa.imigrasi.go.id/front/info/visa"
    g["visa_products"][0]["source_url"] = own
    assert tstation.records_for_route(ROUTE, g)[0]["source_url"] == own
