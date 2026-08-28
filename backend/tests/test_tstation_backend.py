"""The quality-control backend, pinned to Trip.com's acceptance standard:
25-field records with per-field status, combined filtering, the change log,
and the two-sheet Excel export."""
import io

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.main import app
from app.visa_snapshot import kimi_primary, tstation

READER = {"authorization": "Bearer dev-token", "x-org-id": "org-a",
          "x-user-id": "reader-1"}
ADMIN = {"authorization": "Bearer admin-token", "x-org-id": "org-b",
         "x-user-id": "operator-1"}

ANSWER = {
    "disposition": "VISA_REQUIRED", "visa_category": "Tourist visa",
    "permitted_stay": "30 days", "passport_validity": "6 months",
    "required_documents": ["passport", "photo"],
    "application_channel": "EMBASSY_OR_CONSULATE",
    "government_fee": {"amount": 100, "currency": "USD"},
    "processing_time": "5 working days", "confidence": "high",
    "source_url": "https://www.mofa.go.jp/j_info/visit/visa/index.html",
    "visa_products": [
        {"type": "Single-entry tourist", "entry": "single",
         "validity": "3 months", "max_stay_days": 30,
         "fee": {"amount": 100, "currency": "USD"}, "notes": None},
        {"type": "Multiple-entry tourist", "entry": "multiple",
         "validity": "5 years", "max_stay_days": 90,
         "fee": {"amount": 250, "currency": "USD"}, "notes": "High income"},
    ],
}


@pytest.fixture()
def client():
    c = TestClient(app)
    yield c
    kimi_primary.set_provider(None)


def test_the_25_field_record_speaks_their_dictionary_exactly():
    route = {"passport_nationality": "CHN", "destination_country": "JPN",
             "travel_purpose": "tourism"}
    rows = tstation.records_for_route(route, ANSWER, None, "2026-08-27T00:00:00")
    assert len(rows) == 2                      # one record per visa product
    r = rows[0]
    assert set(tstation.FIELD_ORDER) <= set(r.keys())
    assert r["visa_type_name"] == "Single-entry tourist"
    assert (r["validity_duration"], r["validity_unit"]) == (3, "Month")
    assert (r["max_stay_duration"], r["max_stay_unit"]) == (30, "Day")
    assert r["entries"] == "Single"
    assert (r["visa_fee_amount"], r["visa_fee_currency"]) == (100, "USD")
    assert r["application_method"] == "Embassy Submission"
    assert (r["processing_min_days"], r["processing_unit"]) == (5, "Working Day")
    # The spec ladder: an engine answer WITH an official source is Medium;
    # strip the source and the same answer is Low ("non-official only").
    assert r["confidence_level"] == "Medium"
    bare = {k: v for k, v in ANSWER.items()
            if k not in ("source_url", "official_portal_url")}
    low = tstation.records_for_route(route, bare, None, "2026-08-27T00:00:00")
    assert low[0]["confidence_level"] == "Low"
    r2 = rows[1]
    assert (r2["validity_duration"], r2["validity_unit"]) == (5, "Year")
    assert r2["entries"] == "Multiple"
    assert r2["special_conditions"] == "High income"


def test_visa_free_yields_one_clean_record_and_human_check_is_high():
    route = {"passport_nationality": "SGP", "destination_country": "CHN",
             "travel_purpose": "tourism"}
    g = {"disposition": "VISA_EXEMPT", "permitted_stay": "30 days",
         "permitted_stay_days": 30, "confidence": "high"}
    prov = {"source_url": "https://cs.mfa.gov.cn/x", "verified_at": "2026-08-22",
            "verified_by": "Ellis source audit"}
    rows = tstation.records_for_route(route, g, prov,
                                      valid_until="2026-11-20T00:00:00")
    assert len(rows) == 1
    r = rows[0]
    assert r["visa_requirement"] == "Visa-free"
    assert r["visa_type_name"] == "No visa needed"
    assert r["visa_fee_amount"] == 0
    assert r["confidence_level"] == "High"     # a person verified it
    assert r["source_url"] == "https://cs.mfa.gov.cn/x"
    assert r["collected_at"] == "2026-08-22"
    assert tstation.completeness(r) == 1.0


def test_field_status_reports_missing_required_fields():
    route = {"passport_nationality": "CHN", "destination_country": "JPN",
             "travel_purpose": "tourism"}
    rows = tstation.records_for_route(
        route, {"disposition": "VISA_REQUIRED", "confidence": "high"}, None)
    st = tstation.field_status(rows[0])
    assert st["visa_fee_amount"] == "missing"
    assert st["consulate_district"] == "optional-empty"
    assert st["visa_requirement"] == "filled"
    assert tstation.completeness(rows[0]) < 1.0


def _warm(client, nat, dest):
    kimi_primary.set_provider(lambda system, user: dict(ANSWER))
    r = client.post("/database/lookup", headers=READER,
                    json={"nationality": nat, "destination": dest})
    assert r.status_code == 200


def test_records_endpoint_filters_and_checklists(client):
    _warm(client, "NZL", "BLZ")
    _warm(client, "NZL", "BLZ")
    out = client.get("/database/records?nationality=NZL&destination=BLZ",
                     headers=ADMIN).json()
    assert out["summary"]["total"] == 2        # two products, one route
    rec = out["records"][0]
    assert rec["travel_document_country"] == "NZL"
    assert rec["field_status"]["visa_requirement"] == "filled"
    assert out["summary"]["source_coverage"] is not None
    # Filters combine: a requirement filter that matches nothing.
    none = client.get("/database/records?nationality=NZL&destination=BLZ"
                      "&requirement=Visa-free", headers=ADMIN).json()
    assert none["summary"]["total"] == 0
    # Readers cannot see the ops surface.
    assert client.get("/database/records", headers=READER).status_code == 403


def test_the_change_log_records_the_engine_answer(client):
    _warm(client, "NZL", "VUT")
    out = client.get("/database/changes?q=VUT", headers=ADMIN).json()
    assert any(c["action"] == "add" and c["origin"] == "engine"
               and (c["route"] or {}).get("destination_country") == "VUT"
               for c in out["changes"])
    assert client.get("/database/changes", headers=READER).status_code == 403


def test_excel_export_has_two_sheets_and_the_data(client):
    _warm(client, "NZL", "FSM")
    r = client.get("/database/export.xlsx?nationality=NZL&destination=FSM",
                   headers=ADMIN)
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]
    wb = load_workbook(io.BytesIO(r.content))
    # Data first: the acceptance standard reads the 25-field header row off
    # sheet 1; the descriptions ride second.
    assert wb.sheetnames == ["Data", "Field descriptions"]
    data = wb["Data"]
    header = [c.value for c in data[1]]
    assert header == list(tstation.FIELD_ORDER)
    assert data.max_row >= 3                    # header + two products
    fields = wb["Field descriptions"]
    # snapshot row (5.2) + header row + 25 field rows
    assert fields.max_row == 2 + len(tstation.FIELD_ORDER)
    assert fields["A1"].value == "Snapshot (UTC)"
    assert client.get("/database/export.xlsx", headers=READER).status_code == 403


def test_discretionary_validity_maps_to_the_stay_bound():
    """"Set by the consulate" is not a parser failure — it is the truth that
    no fixed validity exists. Their own display standard writes these as
    "Up to N days (determined at issuance)", so the record carries the stay
    length as the upper bound; with no stay known it stays honestly empty."""
    route = {"passport_nationality": "CHN", "destination_country": "FRA",
             "travel_purpose": "tourism"}
    g = {"disposition": "VISA_REQUIRED", "confidence": "high",
         "visa_products": [
             {"type": "Short-stay Schengen C", "entry": "single",
              "validity": "Up to trip duration / consulate discretion",
              "max_stay_days": 90,
              "fee": {"amount": 90, "currency": "EUR"}},
             {"type": "Mystery visa", "entry": "single",
              "validity": "as granted", "max_stay_days": None, "fee": None},
         ]}
    rows = tstation.records_for_route(route, g)
    assert (rows[0]["validity_duration"], rows[0]["validity_unit"]) == (90, "Day")
    assert rows[1]["validity_duration"] is None      # no bound, no guess
