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
    # The spec ladder. An answer that asserts visa PRODUCTS but was never
    # checked against its official page is Low however good its URL looks:
    # an audit of every such record found 19 of 21 wrong (superseded fees,
    # products the destination does not issue, visas demanded of exempt
    # travellers). Once the official page has been read and agrees, the same
    # answer is Medium while required fields remain unchecked, and High once
    # its required fields are complete and checked.
    assert r["confidence_level"] == "Low"
    ok = tstation.records_for_route(route, ANSWER, None, "2026-08-27T00:00:00",
                                    grounded_ok=True)
    assert ok[0]["confidence_level"] == "Medium"  # Verdict-only grounding cannot certify all filled fields: checked, but not High.
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
         "permitted_stay_days": 30, "confidence": "high",
         "policy_valid_until": "2026-12-31",
         "required_documents": ["Valid passport"]}
    prov = {"source_url": "https://cs.mfa.gov.cn/x", "verified_at": "2026-08-22",
            "verified_by": "Ellis operator", "verifier": "human",
            "fields": ["disposition", "permitted_stay_days", "required_documents"],
            "note": "Official page confirms the exemption and valid-passport requirement."}
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


def test_discretionary_validity_remains_separate_from_the_stay_bound():
    """A stay ceiling cannot establish the independently granted visa validity."""
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
    assert (rows[0]["validity_duration"], rows[0]["validity_unit"]) == (None, None)
    assert rows[0]["max_stay_duration"] == 90
    assert rows[1]["validity_duration"] is None      # no bound, no guess


def test_parenthesized_duration_does_not_collapse_a_validity_range():
    """Six (6) months is a duration; one-to-three is an unresolved range.
    An on-arrival product's stay cannot establish its separate validity."""
    route = {"passport_nationality": "HKG", "destination_country": "GAB",
             "travel_purpose": "tourism"}
    g = {"disposition": "VISA_REQUIRED", "confidence": "high",
         "visa_products": [
             {"type": "e-Visa, short stay", "entry": "single",
              "validity": "One (1) to three (3) months", "max_stay_days": 90,
              "fee": {"amount": 70, "currency": "EUR"}},
             {"type": "e-Visa, long stay", "entry": "multiple",
              "validity": "Six (6) months", "max_stay_days": 90,
              "fee": {"amount": 185, "currency": "EUR"}},
             {"type": "Tourist visa on arrival (T)", "entry": "single",
              "validity": None, "max_stay_days": 30, "fee": None},
         ]}
    rows = tstation.records_for_route(route, g)
    assert (rows[0]["validity_duration"], rows[0]["validity_unit"]) == (None, None)
    assert rows[0]["validity_text"] == "One (1) to three (3) months"
    assert (rows[1]["validity_duration"], rows[1]["validity_unit"]) == (6, "Month")
    assert (rows[2]["validity_duration"], rows[2]["validity_unit"]) == (None, None)
    assert rows[2]["max_stay_duration"] == 30


def test_spelled_out_validities_read_definitionally():
    """"Three months from issue" and "not exceeding five years" state their
    figures in words. Reading a written-out number is reading, not guessing."""
    route = {"passport_nationality": "USA", "destination_country": "GIN",
             "travel_purpose": "tourism"}
    g = {"disposition": "VISA_REQUIRED", "confidence": "high",
         "visa_products": [
             {"type": "Tourist / Entry e-Visa", "entry": "multiple",
              "validity": "not exceeding five years", "max_stay_days": 90,
              "fee": None},
             {"type": "Tourist visa", "entry": "single",
              "validity": "Three months from issue", "max_stay_days": 30,
              "fee": None},
         ]}
    rows = tstation.records_for_route(route, g)
    assert (rows[0]["validity_duration"], rows[0]["validity_unit"]) == (5, "Year")
    assert (rows[1]["validity_duration"], rows[1]["validity_unit"]) == (3, "Month")


def test_records_listing_serves_one_answer_per_route(client, db):
    """A route cached again per transit itinerary or arrival month changes
    the advice, not the product table. A stale variant with its own product
    names must not stand beside the fresh answer as phantom twins."""
    from datetime import datetime, timedelta, timezone
    from app.visa_snapshot.models import KimiRouteGuidanceCache
    for row in db.query(KimiRouteGuidanceCache).all():
        db.delete(row)
    db.commit()
    _warm(client, "NZL", "BLZ")
    fresh = db.query(KimiRouteGuidanceCache).one()
    stale_guidance = dict(fresh.guidance)
    stale_guidance["visa_products"] = [
        {"type": "Old-name visitor visa", "entry": "single",
         "validity": None, "max_stay_days": None, "fee": None}]
    db.add(KimiRouteGuidanceCache(
        cache_key=fresh.cache_key + "|via:JPN", route=dict(fresh.route),
        status="KIMI_PRIMARY", guidance=stale_guidance,
        generated_at=datetime.now(timezone.utc) - timedelta(days=3)))
    db.commit()
    out = client.get("/database/records?nationality=NZL&destination=BLZ",
                     headers=ADMIN).json()
    names = {r["visa_type_name"] for r in out["records"]}
    assert "Old-name visitor visa" not in names
    assert out["summary"]["total"] == 2


def test_proven_free_visas_keep_their_zero_and_get_a_currency():
    """"Exempt", "Nil" and "gratis" are how official pages say free. A zero
    backed by any of those words survives, and a surviving zero carries USD
    like the visa-exempt branch, so the currency cell never reads missing."""
    route = {"passport_nationality": "CHN", "destination_country": "NPL",
             "travel_purpose": "tourism"}
    g = {"disposition": "VISA_ON_ARRIVAL", "confidence": "high",
         "visa_products": [
             {"type": "30-day tourist visa", "entry": "multiple",
              "validity": "30 days", "max_stay_days": 30,
              "fee": {"amount": 0},
              "notes": "Gratis for Chinese nationals"},
             {"type": "Child visa", "entry": "single", "validity": "30 days",
              "max_stay_days": 30, "fee": {"amount": 0, "currency": "EUR"},
              "notes": "Children are exempt from the visa fee"},
             {"type": "Suspicious free visa", "entry": "single",
              "validity": "30 days", "max_stay_days": 30,
              "fee": {"amount": 0}, "notes": "No explanation given"},
         ]}
    rows = tstation.records_for_route(route, g)
    assert (rows[0]["visa_fee_amount"], rows[0]["visa_fee_currency"]) == (0, "USD")
    assert (rows[1]["visa_fee_amount"], rows[1]["visa_fee_currency"]) == (0, "EUR")
    assert rows[2]["visa_fee_amount"] is None     # an unexplained zero stays out


def test_productless_visa_does_not_invent_validity_from_rolling_stay():
    """A permitted stay is not a visa's entry window, even without products."""
    route = {"passport_nationality": "SEN", "destination_country": "FRA",
             "travel_purpose": "tourism"}
    g = {"disposition": "VISA_REQUIRED", "confidence": "high",
         "visa_category": "Short-stay Schengen C",
         "permitted_stay": "90 days in any 180-day period"}
    rows = tstation.records_for_route(route, g)
    assert (rows[0]["validity_duration"], rows[0]["validity_unit"]) == (None, None)


def test_records_listing_prefers_the_canonical_row_over_a_newer_dated_copy(client, db):
    """A row keyed to an arrival month was a fresh, unverified model answer
    (the travel date used to fork the decision). Being newest must not let
    it displace the route's canonical row: Trip.com's console showed Hong
    Kong to Vietnam as "Visa-free" from a dated copy generated that morning,
    beside the verified visa-required answer readers got without a date."""
    from datetime import datetime, timezone
    from app.visa_snapshot.models import KimiRouteGuidanceCache
    for row in db.query(KimiRouteGuidanceCache).all():
        db.delete(row)
    db.commit()
    _warm(client, "NZL", "BLZ")
    canonical = db.query(KimiRouteGuidanceCache).one()
    assert canonical.cache_key.split("|")[5] == "unknown"
    wrong = dict(canonical.guidance)
    wrong["disposition"] = "VISA_EXEMPT"
    wrong["visa_products"] = [
        {"type": "Phantom visa-free entry", "entry": "single",
         "validity": None, "max_stay_days": 30, "fee": None}]
    parts = canonical.cache_key.split("|")
    parts[5] = "2026-09"
    db.add(KimiRouteGuidanceCache(
        cache_key="|".join(parts), route=dict(canonical.route),
        status="KIMI_PRIMARY", guidance=wrong,
        generated_at=datetime.now(timezone.utc)))
    db.commit()
    out = client.get("/database/records?nationality=NZL&destination=BLZ",
                     headers=ADMIN).json()
    names = {r["visa_type_name"] for r in out["records"]}
    assert "Phantom visa-free entry" not in names
    assert all(r["visa_requirement"] != "Visa-free" for r in out["records"]) \
        or canonical.guidance.get("disposition") == "VISA_EXEMPT"


def test_an_exemption_lane_on_a_conditional_route_is_not_a_visa():
    """Chinese passport transiting Korea: the free transit lanes were
    labelled "eVisa" with "Embassy Submission" because the route-level
    channel sentence (which mentions the embassy for those who need a visa)
    was copied onto every product. A lane that says no visa is needed and
    costs nothing is the route's own exemption with nothing to apply for."""
    route = {"passport_nationality": "CHN", "destination_country": "KOR",
             "travel_purpose": "transit"}
    g = {"disposition": "CONDITIONAL", "requirement_detail": "transit_visa_free",
         "application_channel": "not_required",
         "application_channel_detail": "No Korean visa is needed on the lanes below. "
                                       "Otherwise apply through the Embassy's designated agencies.",
         "visa_products": [
             {"type": "Airside transit at Incheon", "entry": "single", "validity": None,
              "max_stay_days": None, "fee": {"amount": 0, "currency": "KRW"},
              "notes": "Free, no visa needed while you stay in the transit area."},
             {"type": "Jeju direct entry (B-2)", "entry": "single", "validity": None,
              "max_stay_days": 30, "fee": {"amount": 0, "currency": "KRW"},
              "notes": "Free, no visa needed on a direct arrival at Jeju."}]}
    rows = tstation.records_for_route(route, g)
    assert [r["visa_requirement_detail"] for r in rows] == ["Transit Visa-free"] * 2
    assert [r["application_method"] for r in rows] == [None, None]
    # Indonesia to Japan: the registration lane is free and online, the paper
    # visa beside it is lodged at the visa application centre named in the
    # channel sentence, not "Online Application" copied from the route.
    g2 = {"disposition": "CONDITIONAL", "requirement_detail": "conditional_visa_free",
          "application_channel": "online_portal",
          "application_channel_detail": "Register the e-passport online on JAVES. Travellers "
                                        "who do not register apply for a paper visa through the "
                                        "Japan Visa Application Center (JVAC).",
          "visa_products": [
              {"type": "E-passport visa exemption registration", "entry": "multiple",
               "validity": "3 years", "max_stay_days": 15, "fee": {"amount": 0, "currency": "JPY"},
               "notes": "Registration and entry are free of charge."},
              {"type": "Multiple-entry short-term stay visa (tourism)", "entry": "multiple",
               "validity": "5 years", "max_stay_days": 30,
               "fee": {"amount": 3330000, "currency": "IDR"}, "notes": None}]}
    rows2 = tstation.records_for_route({"passport_nationality": "IDN",
                                        "destination_country": "JPN",
                                        "travel_purpose": "tourism"}, g2)
    assert rows2[0]["visa_requirement_detail"] == "Conditional Visa-free"
    assert rows2[0]["application_method"] == "Online Application"
    assert rows2[1]["application_method"] == "Agency Service"
    assert rows2[1]["visa_requirement_detail"] == "Paper Visa"
@pytest.mark.parametrize("explicit_entry", ["Return ticket needed.", ["Return ticket needed."]])
def test_conditional_arrival_card_scope_survives_record_rendering(explicit_entry):
    from app.visa_snapshot import tstation
    card = {"required": None, "name": "TWAC", "submission_window": "Within7 days",
            "notes": "Only multiple-entry permit holders file; resident holders are excluded."}
    guidance = {"disposition": "VISA_EXEMPT", "arrival_card": card}
    text = tstation._entry_requirements(guidance)
    assert card["notes"] in text
    assert "Within7 days" in text
    assert "must still file" not in text
    assert "TWAC required" not in text
    guidance["entry_requirements"] = explicit_entry
    rows = tstation.records_for_route({"passport_nationality": "HKG", "destination_country": "TWN",
                                      "travel_purpose": "tourism"}, guidance)
    assert rows
    assert all("Only multiple-entry permit holders file" in row["entry_requirements"]
               and "resident holders are excluded" in row["entry_requirements"] for row in rows)
    assert all("Return ticket needed" in row["entry_requirements"] for row in rows)


def test_required_arrival_card_retains_resident_exemptions_and_legacy_notes():
    from app.visa_snapshot import tstation
    text = tstation._entry_requirements({"disposition": "VISA_REQUIRED", "arrival_card": {
        "required": True, "name": "TWAC", "submission_window": "Within7 days",
        "notes": ["Resident holders are excluded."], "note": "Filing is free."}})
    assert "TWAC required" in text
    assert "Resident holders are excluded" in text
    assert "Filing is free" in text
