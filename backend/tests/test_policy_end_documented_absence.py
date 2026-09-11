"""Field 24 (policy end date) on a checked answer whose official page states
no end date is a documented absence, not a gap: labelled "Not publicly
available", excluded from the completeness denominator, and no bar to High.
An answer nobody checked keeps the gap, and a recheck deadline never fills it."""
from app.visa_snapshot import tstation

ROUTE = {"passport_nationality": "USA", "destination_country": "JPN", "travel_purpose": "tourism"}
PAGE = "https://www.mofa.go.jp/j_info/visit/visa/short/novisa.html"
GUIDANCE = {
    "disposition": "VISA_EXEMPT", "requirement_detail": "unconditional_visa_free",
    "visa_category": "No visa needed", "permitted_stay": "90 days",
    "required_documents": ["Valid passport"], "source_url": PAGE,
    "unpublished_fields": ["visa_fee_amount", "visa_fee_currency"],
}
PROVENANCE = {
    "fields": ["disposition"],
    "source_url": PAGE, "verified_at": "2026-09-11", "verifier": "ai",
    "note": "Reviewed the MOFA visa exemption list: US nationals may stay up to 90 days without a visa.",
    "quote": "Nationals of the United States may stay in Japan for up to 90 days without a visa.",
}
FRESHNESS = "2026-09-25T00:00:00+00:00"


def _row(guidance=GUIDANCE, provenance=PROVENANCE, **kw):
    return tstation.records_for_route(ROUTE, dict(guidance), provenance=provenance,
                                      valid_until=FRESHNESS, **kw)[0]


def test_checked_answer_without_a_published_end_date_is_a_documented_absence():
    row = _row()
    assert row["info_validity"] is None  # the recheck deadline never fills field 24
    assert row["freshness_valid_until"] == FRESHNESS
    st = tstation.field_status(row)
    assert st["info_validity"] == "not-published"
    assert "info_validity" in row["_unpublished"]
    values = dict(zip(tstation.FIELD_ORDER, tstation.export_values(row), strict=True))
    assert values["info_validity"] == tstation.NOT_PUBLICLY_AVAILABLE
    assert tstation.completeness(row) == 1.0
    assert row["confidence_level"] == "High"


def test_unchecked_answer_keeps_the_gap_and_reads_medium_but_stays_held():
    row = _row(provenance=None)
    assert row["info_validity"] is None
    assert tstation.field_status(row)["info_validity"] == "missing"
    assert "info_validity" not in (row.get("_unpublished") or [])
    assert row["confidence_level"] == "Medium"  # a cited official page, never read
    assert row["_evidence_low"] is True


def test_a_published_end_date_is_still_the_value_itself():
    row = _row(guidance=dict(GUIDANCE, policy_valid_until="2026-12-31"))
    assert row["info_validity"] == "2026-12-31"
    assert tstation.field_status(row)["info_validity"] == "filled"
    assert "info_validity" not in (row.get("_unpublished") or [])


def test_a_remaining_gap_elsewhere_still_grades_medium_not_high():
    guidance = dict(GUIDANCE, required_documents=None)
    row = _row(guidance=guidance)
    st = tstation.field_status(row)
    assert st["info_validity"] == "not-published"
    assert st["required_documents"] == "missing"
    assert row["confidence_level"] == "Medium"


def test_separate_permission_product_without_its_own_review_keeps_the_gap():
    guidance = {
        "disposition": "VISA_REQUIRED", "requirement_detail": "evisa",
        "visa_category": "Tourist e-Visa", "source_url": PAGE,
        "visa_products": [
            {"type": "Tourist e-Visa", "requirement_detail": "evisa"},
            {"type": "Transit visa-free 24 hours", "requirement_detail": "transit_visa_free"},
        ],
    }
    provenance = {"fields": ["disposition"], "source_url": PAGE, "verified_at": "2026-09-11",
                  "verifier": "ai", "note": "Reviewed the official page: a tourist e-Visa is required before travel.",
                  "quote": "A tourist e-Visa is required before travel."}
    rows = tstation.records_for_route(ROUTE, guidance, provenance=provenance, valid_until=FRESHNESS)
    by_type = {r["visa_type_name"]: r for r in rows}
    checked = by_type["Tourist e-Visa"]
    assert tstation.field_status(checked)["info_validity"] == "not-published"
    separate = [r for r in rows if r.get("_separate_permission")]
    for r in separate:
        assert tstation.field_status(r)["info_validity"] == "missing"


def test_productless_quoted_verdict_grades_high_without_per_field_proof():
    # The verdict quote is the single official source their ladder asks for.
    # Stay and documents came from the route answer, not from the quote: the
    # ladder grades completeness, and the publication hold is separate.
    row = _row()
    st = tstation.field_status(row)
    assert row.get("_product_index") is None and not row.get("_separate_permission")
    assert st["max_stay_duration"] == "filled" and st["required_documents"] == "filled"
    assert PROVENANCE["fields"] == ["disposition"]
    assert row["confidence_level"] == "High"


def test_product_row_keeps_the_per_field_proof_gate():
    # A product whose fee is not the parent page's fee and carries no proof
    # of its own stays Medium: the parent quote never described that product.
    guidance = {
        "disposition": "VISA_REQUIRED", "requirement_detail": "evisa",
        "visa_category": "Tourist e-Visa", "source_url": PAGE,
        "government_fee": {"amount": 25, "currency": "USD"},
        "required_documents": ["Valid passport"], "permitted_stay": "30 days",
        "visa_products": [
            {"type": "Tourist e-Visa", "requirement_detail": "evisa", "validity": "90 days",
             "entry": "single", "fee": {"amount": 80, "currency": "USD"},
             "application_channel": "online", "required_documents": ["Valid passport", "Photo"]},
        ],
    }
    provenance = {"fields": ["disposition", "government_fee", "permitted_stay"], "source_url": PAGE,
                  "verified_at": "2026-09-11", "verifier": "ai",
                  "note": "Reviewed the official page: a 25 USD tourist e-Visa is required before travel.",
                  "quote": "A tourist e-Visa (USD 25) is required before travel."}
    rows = tstation.records_for_route(ROUTE, guidance, provenance=provenance, valid_until=FRESHNESS)
    product = next(r for r in rows if r.get("_product_index") == 0)
    st = tstation.field_status(product)
    assert not any(v == "missing" for v in st.values()), st
    assert product["confidence_level"] == "Medium"


def test_reviewed_table_never_lends_the_parent_documents_to_a_separate_permission():
    # USA to GBR class: the ETA review checked the product table, so the
    # Standard Visitor visa row keeps the review's verdict credit, but the
    # ETA document list is not that visa's document list and the row grades
    # on the gap instead of serving another permission's papers.
    guidance = {
        "disposition": "ELECTRONIC_AUTHORIZATION_REQUIRED",
        "requirement_detail": "eta_electronic_authorization",
        "visa_category": "Electronic Travel Authorisation", "source_url": PAGE,
        "required_documents": ["Valid passport", "Digital photo taken in the ETA app"],
        "permitted_stay": "6 months",
        "visa_products": [
            {"type": "Electronic Travel Authorisation", "requirement_detail": "eta_electronic_authorization"},
            {"type": "Standard Visitor visa", "requirement_detail": "paper_visa"},
        ],
    }
    provenance = {"fields": ["disposition", "visa_products"], "source_url": PAGE, "verified_at": "2026-09-11",
                  "verifier": "ai", "note": "Reviewed the official page: US citizens need an ETA; a Standard Visitor visa is the alternative.",
                  "quote": "US citizens need an ETA to visit the UK."}
    rows = tstation.records_for_route(ROUTE, guidance, provenance=provenance, valid_until=FRESHNESS)
    separate = [r for r in rows if r.get("_separate_permission")]
    assert len(separate) == 1
    visitor = separate[0]
    assert visitor.get("_table_reviewed") is True
    assert not visitor.get("required_documents")
    assert tstation.field_status(visitor)["required_documents"] == "missing"
    assert visitor["confidence_level"] == "Medium"


def test_side_field_finding_still_strips_visa_only_cells_from_a_visa_free_record():
    guidance = {
        "disposition": "VISA_EXEMPT", "requirement_detail": "conditional_visa_free",
        "visa_category": "No visa needed", "source_url": PAGE, "permitted_stay_days": 90,
        "processing_time": "5 business days", "validity": "90 days", "entries": "multiple",
    }
    provenance = {"fields": ["disposition"], "source_url": PAGE, "verified_at": "2026-09-11",
                  "verifier": "ai", "note": "Reviewed the official page: visa-free for 90 days.",
                  "quote": "Visa-free for 90 days."}
    clean = tstation.records_for_route(ROUTE, guidance, provenance=provenance, valid_until=FRESHNESS)[0]
    side = tstation.records_for_route(ROUTE, guidance, provenance=provenance, valid_until=FRESHNESS,
                                      disputed_fields=["processing_time"])[0]
    for f in ("processing_min_days", "processing_max_days", "validity_duration", "entries"):
        assert clean.get(f) in (None, ""), f
        assert side.get(f) in (None, ""), f
    assert side["confidence_level"] == "Medium" and side["_evidence_low"] is False


def test_funds_and_accommodation_findings_are_material():
    assert tstation.material_disputes(["financial_evidence"]) == ["financial_evidence"]
    assert tstation.material_disputes(["accommodation_evidence"]) == ["accommodation_evidence"]
    assert tstation.material_disputes(["processing_time", "notes"]) == []
