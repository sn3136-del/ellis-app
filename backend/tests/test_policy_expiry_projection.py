"""A source policy's end date and our next review deadline are different facts."""
from copy import deepcopy
import pytest

from app.visa_snapshot import tstation


ROUTE = {
    "passport_nationality": "HKG",
    "destination_country": "VNM",
    "travel_purpose": "tourism",
}
GUIDANCE = {
    "disposition": "VISA_REQUIRED",
    "visa_category": "Tourist e-Visa",
    "requirement_detail": "evisa",
}
FRESHNESS = "2026-09-23T12:34:56+00:00"


def test_cache_deadline_does_not_invent_a_policy_expiry_or_fill_contract_field():
    row = tstation.records_for_route(ROUTE, GUIDANCE, valid_until=FRESHNESS)[0]

    assert row["info_validity"] is None
    assert row["freshness_valid_until"] == FRESHNESS
    assert tstation.field_status(row)["info_validity"] == "missing"
    # Metadata must not increase the fixed 25-field acceptance denominator.
    complete_except_expiry = {f: "value" for f in tstation.CONTRACT_FIELDS}
    complete_except_expiry.update(
        info_validity=row["info_validity"],
        freshness_valid_until=row["freshness_valid_until"],
    )
    summary = tstation.acceptance_summary([complete_except_expiry])
    assert summary["field_count"] == 25
    assert summary["field_completeness_rate"] == 24 / 25
    assert summary["record_completeness_rate"] == 0


def test_changing_internal_refresh_window_never_changes_published_policy_end():
    guidance = dict(GUIDANCE, policy_valid_until="2026-12-31")
    rows = [tstation.records_for_route(ROUTE, guidance, valid_until=deadline)[0]
            for deadline in (FRESHNESS, "2026-10-23T12:34:56+00:00", None)]

    assert {row["info_validity"] for row in rows} == {"2026-12-31"}
    assert [row["freshness_valid_until"] for row in rows] == [
        FRESHNESS, "2026-10-23T12:34:56+00:00", None]


def test_explicit_unpublished_policy_date_stays_blank_and_separate_from_freshness():
    guidance = dict(GUIDANCE, unpublished_fields=["info_validity"])
    row = tstation.records_for_route(ROUTE, guidance, valid_until=FRESHNESS)[0]

    assert row["info_validity"] is None
    assert tstation.field_status(row)["info_validity"] == "not-published"
    assert row["freshness_valid_until"] == FRESHNESS


def test_product_policy_dates_do_not_all_inherit_the_route_default():
    guidance = dict(GUIDANCE, policy_valid_until="2026-12-31", visa_products=[
        {"type": "Single-entry tourist e-Visa", "policy_valid_until": "2026-11-30"},
        {"type": "Multiple-entry tourist e-Visa", "policy_valid_until": None},
    ])
    original = deepcopy(guidance)
    rows = tstation.records_for_route(ROUTE, guidance, valid_until=FRESHNESS)

    assert [row["info_validity"] for row in rows] == ["2026-11-30", None]
    assert all(row["freshness_valid_until"] == FRESHNESS for row in rows)
    assert guidance == original


def test_separate_permission_product_does_not_inherit_parent_policy_expiry():
    guidance = dict(GUIDANCE, policy_valid_until="2026-12-31", visa_products=[
        {"type": "Tourist sticker visa", "requirement_detail": "paper_visa"},
    ])
    row = tstation.records_for_route(ROUTE, guidance, valid_until=FRESHNESS)[0]

    assert row["_separate_permission"] is True
    assert row["info_validity"] is None
    assert row["freshness_valid_until"] == FRESHNESS


def test_export_dictionary_does_not_redefine_policy_expiry_as_a_warranty_date():
    description = tstation.FIELD_DESCRIPTIONS["info_validity"]
    assert "Published policy validity end date" in description
    assert "not the policy's expiry date" in description
    assert "freshness_valid_until" not in tstation.FIELD_ORDER


def interval_provenance():
    return {"fields": ["disposition"], "field_provenance": {"disposition": {
        "source_url": "https://www.meco.org.tw/services/visa-services/",
        "verified_at": "2026-09-09", "verifier": "ai",
        "note": "Reviewed Taiwan ordinary passport exemption for the Philippines.",
        "effective_from": "2026-07-01", "effective_to": "2027-06-30",
        "policy_interval_evidence": {"effective_to": {
            "source_url": "https://www.meco.org.tw/news/detail/1178",
            "verified_at": "2026-09-09", "verifier": "ai",
            "quote": "These regulations will remain in effect from 01 July 2026 to 30 June 2027.",
        }},
    }}}


def test_known_reviewed_policy_interval_survives_a_separate_refresh_deadline():
    route = dict(ROUTE, passport_nationality="TWN", destination_country="PHL")
    guidance = {"disposition": "VISA_EXEMPT", "permitted_stay_days": 14}
    row = tstation.records_for_route(route, guidance, interval_provenance(), valid_until=FRESHNESS)[0]
    assert row["info_validity"] == "2027-06-30"
    assert row["freshness_valid_until"] == FRESHNESS


@pytest.mark.parametrize("fault", ["unquoted", "wrong_date", "wrong_country", "public", "future",
                                  "invalid_interval", "ancillary", "bare_date", "bound_disagrees"])
def test_unverified_or_unrelated_interval_dates_do_not_fill_policy_expiry(fault):
    provenance = interval_provenance()
    proof = provenance["field_provenance"]["disposition"]
    bound = proof["policy_interval_evidence"]["effective_to"]
    if fault == "unquoted":
        bound.pop("quote")
    elif fault == "wrong_date":
        bound["quote"] = "The policy ends on 30 June 2028."
    elif fault == "wrong_country":
        bound["source_url"] = "https://www.mofa.go.jp/page.html"
    elif fault == "public":
        bound["verifier"] = "public"
    elif fault == "future":
        bound["verified_at"] = "2099-01-01"
    elif fault == "invalid_interval":
        proof["effective_to"] = "2027-02-30"
    elif fault == "ancillary":
        provenance["field_provenance"] = {"passport_validity": proof}
        provenance["fields"] = ["passport_validity"]
    elif fault == "bare_date":
        proof.pop("policy_interval_evidence")
    elif fault == "bound_disagrees":
        bound["effective_to"] = "2028-06-30"
    route = dict(ROUTE, passport_nationality="TWN", destination_country="PHL")
    row = tstation.records_for_route(route, GUIDANCE, provenance, valid_until=FRESHNESS)[0]
    assert row["info_validity"] is None


def test_direct_verdict_interval_quote_and_separate_permission_scope():
    provenance = interval_provenance()
    proof = provenance["field_provenance"]["disposition"]
    proof["quote"] = proof.pop("policy_interval_evidence")["effective_to"]["quote"]
    route = dict(ROUTE, passport_nationality="TWN", destination_country="PHL")
    guidance = dict(GUIDANCE, visa_products=[
        {"type": "Tourist sticker visa", "requirement_detail": "paper_visa"},
    ])
    assert tstation._reviewed_policy_end(provenance, route) == "2027-06-30"
    row = tstation.records_for_route(route, guidance, provenance, valid_until=FRESHNESS)[0]
    assert row["_separate_permission"] is True
    assert row["info_validity"] is None
