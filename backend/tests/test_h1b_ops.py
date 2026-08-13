"""H1B ops (Agent 6): USCIS org-account bulk registration, RFE response
assembly, and the 8 CFR 214.2(h)(19) cap-exemption decision tree.

The doctrine under test, not just the happy path:
  - a bulk row is never auto-filled and never silently dropped;
  - the 2,500 cap is USCIS's and is enforced, not truncated;
  - an RFE packet indexes real exhibits per ground, shows the gaps, and its
    narrative is DRAFT-labeled with every missing fact named;
  - cap exemption answers 'unknown' whenever a fact is absent, and answers
    False only when every path is conclusively negative;
  - all three surfaces are petitioner-or-admin (a beneficiary gets 403).
"""
import base64
import csv
import datetime as dt
import io

import pytest
from sqlalchemy import select

from app import models
from app.h1b import bulk_registration, cap_exemption, rfe_response
from app.h1b import models as h1b_models
from app.h1b.disclaimer import DISCLAIMER_VERSION
from app.providers import kimi

from .conftest import AUTH, AUTH2

PETITIONER_AUTH = {"Authorization": "Bearer dev-token",
                   "X-Org-Id": "org1", "X-User-Id": "hr-ops"}
ADMIN_AUTH = {"Authorization": "Bearer admin-token",
              "X-Org-Id": "org1", "X-User-Id": "admin-ops"}

LOCALES = ("en", "zh-CN", "zh-Hant")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _create_case(client, **overrides):
    body = {"case_kind": "cap_initial",
            "beneficiary_full_name": "WEI ZHANG",
            "beneficiary_email": "wei.zhang@example.com",
            "beneficiary_abroad": True, "beneficiary_in_us": False,
            "first_h1b": True}
    body.update(overrides)
    r = client.post("/h1b/cases", json=body, headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()["case_id"]


def _bind_petitioner(db, case_id, user_id="hr-ops"):
    pet = db.execute(select(h1b_models.CaseParty).where(
        h1b_models.CaseParty.application_id == case_id,
        h1b_models.CaseParty.role == "petitioner")).scalars().first()
    pet.user_id = user_id
    db.commit()
    return pet


def _employer_profile(client, **overrides):
    body = {"legal_name": "Trip.com US Inc", "fein": "12-3456789",
            "address_line1": "1 Biscayne Blvd", "city": "Miami", "state": "FL"}
    body.update(overrides)
    r = client.post("/h1b/employer-profiles", json=body, headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()["employer_profile_id"]


BENEFICIARY_FACTS = {
    "surname": "ZHANG", "given_names": "WEI", "sex": "M",
    "birth_date": "1994-05-12", "birth_country": "China",
    "citizenship_country": "China", "passport_number": "E12345678",
    "passport_expiry": "2031-03-04", "passport_issuing_country": "China",
}

PETITIONER_FACTS = {
    "job_title": "Software Engineer", "soc_code": "15-1252",
    "wage_offer": 150000, "wage_offer_unit": "Year", "wage_level": "III",
    "worksite_city": "Miami", "worksite_state": "FL",
    "worksite_postal_code": "33131", "job_duties": "Design and build systems.",
    "employment_start_date": "2027-10-01",
}


def _write_answers(client, case_id, role, answers, headers):
    r = client.post(f"/h1b/cases/{case_id}/party/{role}/answers",
                    json={"answers": answers}, headers=headers)
    assert r.status_code == 200, r.text


def _ready_case(client, db, *, beneficiary=None, petitioner=None, **overrides):
    case_id = _create_case(client, **overrides)
    _bind_petitioner(db, case_id)
    _write_answers(client, case_id, "beneficiary",
                   {**BENEFICIARY_FACTS, **(beneficiary or {})}, AUTH)
    _write_answers(client, case_id, "petitioner",
                   {**PETITIONER_FACTS, **(petitioner or {})}, PETITIONER_AUTH)
    return case_id


def _read_csv(blob: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(blob.decode("utf-8-sig"))))


def _submit_item(client, case_id, item_id, text="content"):
    up = client.post(f"/cases/{case_id}/documents", json={
        "name": f"{item_id}.pdf", "mime": "application/pdf",
        "size_bytes": 2048, "checklist_item_id": item_id, "text": text},
        headers=AUTH)
    assert up.status_code == 200, (item_id, up.text)
    s = client.post(f"/cases/{case_id}/checklist/{item_id}/submit",
                    json={"document_id": up.json()["id"], "confirm": True},
                    headers=AUTH)
    assert s.status_code == 200, (item_id, s.text)
    return up.json()["id"]


# ===========================================================================
# 1. Bulk registration — validation, no auto-fill, no silent drop
# ===========================================================================

def test_bulk_row_is_complete_and_written_verbatim(client, db):
    case_id = _ready_case(client, db)
    out = bulk_registration.build_workbook(db, "org1", [case_id],
                                           fiscal_year=2028)
    assert out["ready_count"] == 1, out["errors_by_row"]
    assert out["errors_by_row"] == {}
    rows = _read_csv(out["csv_bytes"])
    assert len(rows) == 2                       # header + one beneficiary
    values = dict(zip(rows[0], rows[1]))
    assert values["Beneficiary Last/Family Name"] == "ZHANG"
    assert values["Beneficiary First/Given Name"] == "WEI"
    assert values["Beneficiary Gender"] == "Male"
    assert values["Beneficiary Date of Birth (MM/DD/YYYY)"] == "05/12/1994"
    assert values["Beneficiary Country of Birth"] == "China"
    assert values["Passport or Travel Document Number"] == "E12345678"
    assert values["Passport or Travel Document Expiration Date (MM/DD/YYYY)"] == "03/04/2031"
    assert values["SOC Code"] == "15-1252"
    assert values["OEWS Wage Level"] == "III"
    assert values["Worksite State"] == "FL"


def test_missing_required_value_errors_and_never_autofills(client, db):
    """The single most important bulk rule: a fact Ellis does not have becomes
    an EMPTY cell plus a named error — never a plausible guess."""
    case_id = _ready_case(client, db, beneficiary={"passport_number": ""})
    # Blank the recorded value outright (a written "" is not stored as absent).
    case = db.get(models.VisaApplication, case_id)
    answers = dict(case.answers or {})
    answers.pop("passport_number", None)
    case.answers = answers
    db.commit()

    out = bulk_registration.build_workbook(db, "org1", [case_id],
                                           fiscal_year=2028)
    row = out["rows"][0]
    assert row["ready"] is False
    assert row["values"]["passport_number"] == ""
    codes = {e["code"] for e in row["errors"]}
    assert "missing_required_value" in codes
    assert out["ready_count"] == 0
    # Reported, not dropped.
    assert out["excluded"] == [{"row": 1, "case_id": case_id,
                                "codes": sorted(codes)}]
    assert out["errors_by_row"][1] == row["errors"]
    # And it is not in the uploadable file.
    assert len(_read_csv(out["csv_bytes"])) == 1


def test_full_name_is_never_split_into_surname_and_given_names(client, db):
    """Which token is the family name is a guess. On a federal filing Ellis
    asks instead — full_name alone leaves BOTH name cells empty and errored."""
    case_id = _ready_case(client, db)
    case = db.get(models.VisaApplication, case_id)
    answers = dict(case.answers or {})
    answers.pop("surname", None)
    answers.pop("given_names", None)
    answers["full_name"] = "WEI ZHANG"
    case.answers = answers
    db.commit()

    out = bulk_registration.build_workbook(db, "org1", [case_id],
                                           fiscal_year=2028)
    row = out["rows"][0]
    assert row["values"]["beneficiary_last_name"] == ""
    assert row["values"]["beneficiary_first_name"] == ""
    columns = {e["column"] for e in row["errors"]}
    assert {"beneficiary_last_name", "beneficiary_first_name"} <= columns
    assert row["ready"] is False


@pytest.mark.parametrize("answers,column,code", [
    ({"birth_date": "not a date"}, "beneficiary_date_of_birth",
     "unparseable_date"),
    ({"birth_date": "2099-01-01"}, "beneficiary_date_of_birth",
     "date_not_in_past"),
    ({"passport_expiry": "2001-01-01"}, "passport_expiration_date",
     "passport_expired"),
    ({"birth_country": "Wakanda"}, "beneficiary_country_of_birth",
     "unknown_country"),
    ({"sex": "unspecified"}, "beneficiary_gender", "unmappable_value"),
    ({"passport_number": "!!"}, "passport_number", "unusable_passport_number"),
    ({"surname": "ZH4NG"}, "beneficiary_last_name", "unusable_characters"),
])
def test_bad_beneficiary_cells_are_caught(client, db, answers, column, code):
    case_id = _ready_case(client, db, beneficiary=answers)
    out = bulk_registration.build_workbook(db, "org1", [case_id],
                                           fiscal_year=2028)
    row = out["rows"][0]
    assert row["ready"] is False
    assert (column, code) in {(e["column"], e["code"]) for e in row["errors"]}
    assert row["values"][column] == ""       # blanked, never repaired


@pytest.mark.parametrize("answers,column,code", [
    ({"soc_code": "151252"}, "soc_code", "malformed_soc_code"),
    ({"wage_level": "V"}, "oews_wage_level", "unmappable_value"),
    ({"wage_offer": "lots"}, "offered_wage", "unparseable_wage"),
    ({"wage_offer": -5}, "offered_wage", "wage_not_positive"),
    ({"worksite_state": "Florida"}, "worksite_state", "unknown_state"),
    ({"worksite_postal_code": "331"}, "worksite_postal_code", "malformed_zip"),
    ({"wage_offer_unit": "fortnight"}, "offered_wage_unit", "unmappable_value"),
])
def test_bad_weighted_cells_are_caught(client, db, answers, column, code):
    case_id = _ready_case(client, db, petitioner=answers)
    out = bulk_registration.build_workbook(db, "org1", [case_id],
                                           fiscal_year=2028)
    row = out["rows"][0]
    assert (column, code) in {(e["column"], e["code"]) for e in row["errors"]}
    assert row["values"][column] == ""


def test_wage_level_and_gender_normalization_is_a_transform_not_a_guess(client, db):
    case_id = _ready_case(client, db, beneficiary={"sex": "female"},
                          petitioner={"wage_level": "2",
                                      "wage_offer_unit": "annual"})
    out = bulk_registration.build_workbook(db, "org1", [case_id],
                                           fiscal_year=2028)
    row = out["rows"][0]
    assert row["ready"] is True, row["errors"]
    assert row["values"]["beneficiary_gender"] == "Female"
    assert row["values"]["oews_wage_level"] == "II"
    assert row["values"]["offered_wage_unit"] == "Year"


def test_duplicate_beneficiary_in_one_batch_is_an_error(client, db):
    first = _ready_case(client, db)
    second = _ready_case(client, db)          # same passport + DOB
    out = bulk_registration.build_workbook(db, "org1", [first, second],
                                           fiscal_year=2028)
    assert out["ready_count"] == 1
    codes = {e["code"] for e in out["rows"][1]["errors"]}
    assert "duplicate_beneficiary_in_batch" in codes


def test_non_cap_case_and_foreign_org_case_are_reported_not_dropped(client, db):
    extension = _ready_case(client, db, case_kind="extension")
    out = bulk_registration.build_workbook(
        db, "org1", [extension, "does-not-exist"], fiscal_year=2028)
    assert [r["case_id"] for r in out["rows"]] == [extension, "does-not-exist"]
    assert out["rows"][0]["errors"][0]["code"] == "not_a_cap_case"
    assert out["rows"][1]["errors"][0]["code"] == "case_not_available"
    assert out["ready_count"] == 0


def test_other_orgs_case_is_never_readable(client, db):
    case_id = _ready_case(client, db)
    out = bulk_registration.build_workbook(db, "org2", [case_id],
                                           fiscal_year=2028)
    assert out["rows"][0]["errors"][0]["code"] == "case_not_available"


def test_2500_cap_is_enforced_not_truncated(db):
    ids = [f"case{i}" for i in range(bulk_registration.MAX_BENEFICIARIES + 1)]
    with pytest.raises(bulk_registration.BulkLimitExceeded) as e:
        bulk_registration.build_workbook(db, "org1", ids)
    assert e.value.limit == 2500
    assert e.value.requested == 2501
    # Exactly at the cap is fine (they are all unknown cases, but no raise).
    out = bulk_registration.build_workbook(db, "org1", ids[:-1])
    assert out["requested_count"] == 2500
    assert len(out["rows"]) == 2500


def test_identity_may_come_from_an_accepted_passport_document(client, db):
    case_id = _create_case(client)
    _bind_petitioner(db, case_id)
    _write_answers(client, case_id, "petitioner", PETITIONER_FACTS,
                   PETITIONER_AUTH)
    up = client.post(f"/cases/{case_id}/documents", json={
        "name": "passport.pdf", "mime": "application/pdf", "size_bytes": 2048,
        "text": "passport page"}, headers=AUTH)
    assert up.status_code == 200, up.text
    doc = db.get(models.StoredDocument, up.json()["id"])
    doc.doc_type = "passport"
    doc.approved = True                       # admin-accepted, not a bare upload
    doc.page_classification = {"reject": False}
    doc.extracted_fields = {"surname": "LI", "given_names": "MEI",
                            "sex": "F", "birth_date": "1990-01-02",
                            "nationality": "China",
                            "passport_number": "G99887766",
                            "passport_expiry": "2030-06-30",
                            "issuing_country": "China"}
    db.commit()

    out = bulk_registration.build_workbook(db, "org1", [case_id],
                                           fiscal_year=2028)
    row = out["rows"][0]
    assert row["values"]["beneficiary_last_name"] == "LI"
    assert row["values"]["passport_number"] == "G99887766"
    assert row["sources"]["beneficiary_last_name"] == "accepted_passport.surname"
    # Country of birth is NOT on the passport; it stays an honest gap.
    assert row["values"]["beneficiary_country_of_birth"] == ""
    assert "missing_required_value" in {e["code"] for e in row["errors"]}


def test_unaccepted_passport_upload_never_grounds_a_row(client, db):
    """A bare upload does not fulfil anything (intake doctrine), so it must not
    silently supply values to a federal file either."""
    case_id = _create_case(client)
    _bind_petitioner(db, case_id)
    up = client.post(f"/cases/{case_id}/documents", json={
        "name": "p.pdf", "mime": "application/pdf", "size_bytes": 10,
        "text": "x"}, headers=AUTH)
    assert up.status_code == 200, up.text
    doc = db.get(models.StoredDocument, up.json()["id"])
    doc.doc_type = "passport"
    doc.approved = False
    doc.extracted_fields = {"surname": "GHOST", "passport_number": "X1234567"}
    db.commit()
    out = bulk_registration.build_workbook(db, "org1", [case_id],
                                           fiscal_year=2028)
    assert out["rows"][0]["values"]["beneficiary_last_name"] == ""
    assert "GHOST" not in out["csv_bytes"].decode("utf-8-sig")


def test_a_rejected_passport_page_never_grounds_a_row(client, db):
    """The classifier's reject verdict is load-bearing: a page Ellis refused
    must not quietly supply a passport number to a federal file."""
    case_id = _create_case(client)
    _bind_petitioner(db, case_id)
    up = client.post(f"/cases/{case_id}/documents", json={
        "name": "blurry.pdf", "mime": "application/pdf", "size_bytes": 2048,
        "text": "blurry"}, headers=AUTH)
    doc = db.get(models.StoredDocument, up.json()["id"])
    doc.doc_type = "passport"
    doc.approved = True
    doc.page_classification = {"reject": True, "reasons": ["not a biodata page"]}
    doc.extracted_fields = {"surname": "REJECTED", "passport_number": "Z1111111"}
    db.commit()
    out = bulk_registration.build_workbook(db, "org1", [case_id],
                                           fiscal_year=2028)
    assert out["rows"][0]["values"]["beneficiary_last_name"] == ""
    assert "Z1111111" not in out["csv_bytes"].decode("utf-8-sig")


def test_weighted_columns_only_from_fy2027(client, db):
    case_id = _ready_case(client, db)
    pre = bulk_registration.build_workbook(db, "org1", [case_id],
                                           fiscal_year=2026)
    assert pre["weighted_selection"] is False
    assert all(c["group"] == "identity" for c in pre["columns"])
    assert "SOC Code" not in _read_csv(pre["csv_bytes"])[0]
    assert pre["selection_entries_total"] is None

    post = bulk_registration.build_workbook(db, "org1", [case_id],
                                            fiscal_year=2027)
    assert post["weighted_selection"] is True
    assert "SOC Code" in _read_csv(post["csv_bytes"])[0]
    # Level III => 3 entries under the weighted rule.
    assert post["selection_entries_total"] == 3


def test_include_invalid_writes_blank_cells_and_says_so(client, db):
    good = _ready_case(client, db)
    bad = _ready_case(client, db, beneficiary={"passport_number": "!!",
                                               "birth_date": "1988-08-08"})
    out = bulk_registration.build_workbook(
        db, "org1", [good, bad], fiscal_year=2028, include_invalid=True)
    rows = _read_csv(out["csv_bytes"])
    assert len(rows) == 3                      # header + BOTH rows
    passport_col = rows[0].index("Passport or Travel Document Number")
    assert rows[2][passport_col] == ""         # blank, never repaired
    assert any("INCLUDES rows Ellis could not complete" in c
               for c in out["caveats"])


def test_template_is_curated_data_with_a_source_and_a_verify_caveat(db):
    out = bulk_registration.build_workbook(db, "org1", [], fiscal_year=2028)
    assert out["template_as_of"] == bulk_registration.TEMPLATE_AS_OF
    assert out["template_source"].startswith("https://www.uscis.gov/")
    assert any("diff these column headers" in c for c in out["caveats"])
    assert "Ellis never signs in" in out["upload_instructions"]


def test_next_cap_fiscal_year_follows_the_march_season():
    assert bulk_registration.next_cap_fiscal_year(dt.date(2026, 3, 10)) == 2027
    assert bulk_registration.next_cap_fiscal_year(dt.date(2026, 8, 11)) == 2028
    assert bulk_registration.next_cap_fiscal_year(dt.date(2027, 1, 5)) == 2028


# --- endpoint --------------------------------------------------------------

def test_bulk_endpoint_is_petitioner_or_admin(client, db):
    case_id = _ready_case(client, db)
    denied = client.post("/h1b/orgs/org1/bulk-registration",
                         json={"case_ids": [case_id]}, headers=AUTH)
    assert denied.status_code == 403, denied.text
    assert denied.json()["detail"]["acting_party"] == "petitioner"

    ok = client.post("/h1b/orgs/org1/bulk-registration?locale=zh-CN",
                     json={"case_ids": [case_id], "fiscal_year": 2028},
                     headers=PETITIONER_AUTH)
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["ready_count"] == 1
    assert body["submitted"] is False
    assert body["disclaimer_version"] == DISCLAIMER_VERSION
    assert body["attorney_disclaimer"].startswith("Ellis 不是律师事务所")

    admin = client.post("/h1b/orgs/org1/bulk-registration",
                        json={"case_ids": [case_id]}, headers=ADMIN_AUTH)
    assert admin.status_code == 200, admin.text


def test_bulk_endpoint_returns_the_file_and_not_a_second_copy_of_the_data(client, db):
    case_id = _ready_case(client, db)
    r = client.post("/h1b/orgs/org1/bulk-registration",
                    json={"case_ids": [case_id], "fiscal_year": 2028},
                    headers=PETITIONER_AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    blob = base64.b64decode(body["download"]["content_base64"])
    assert body["download"]["mime"] == "text/csv"
    assert "E12345678" in blob.decode("utf-8-sig")
    # The passport number rides in the file only, never a second time in JSON.
    assert "E12345678" not in r.text
    assert "beneficiary_last_name" in body["rows"][0]["filled_columns"]


def test_bulk_endpoint_rejects_over_the_cap_and_empty_lists(client, db):
    over = client.post("/h1b/orgs/org1/bulk-registration",
                       json={"case_ids": [f"c{i}" for i in range(2501)]},
                       headers=PETITIONER_AUTH)
    assert over.status_code == 422
    assert over.json()["detail"]["limit"] == 2500
    empty = client.post("/h1b/orgs/org1/bulk-registration",
                        json={"case_ids": []}, headers=PETITIONER_AUTH)
    assert empty.status_code == 422


def test_bulk_endpoint_is_org_scoped(client, db):
    case_id = _ready_case(client, db)
    r = client.post("/h1b/orgs/org1/bulk-registration",
                    json={"case_ids": [case_id]}, headers=AUTH2)
    assert r.status_code == 403


def test_bulk_audit_never_records_a_passport_number(client, db):
    from app import audit
    case_id = _ready_case(client, db)
    r = client.post("/h1b/orgs/org1/bulk-registration",
                    json={"case_ids": [case_id], "fiscal_year": 2028},
                    headers=PETITIONER_AUTH)
    assert r.status_code == 200
    assert not audit.contains_plaintext(db, "E12345678")


# ===========================================================================
# 2. RFE response assembly
# ===========================================================================

def test_parse_rfe_text_matches_taxonomy_wording():
    out = rfe_response.parse_rfe_text(
        "The evidence does not establish that the proffered position "
        "qualifies as a specialty occupation under 8 CFR 214.2(h)(4)(iii)(A).")
    keys = [i["issue_key"] for i in out["issues"]]
    assert "specialty_occupation" in keys
    assert out["unmatched"] is False
    assert out["issues"][0]["matched_phrases"]


def test_unclassifiable_notice_yields_no_ground_at_all():
    out = rfe_response.parse_rfe_text("Dear petitioner, please see enclosed.")
    assert out["issues"] == []
    assert out["unmatched"] is True
    assert "guessed ground" in out["note"]


@pytest.mark.parametrize("issue", rfe_response.ISSUES,
                         ids=[i["key"] for i in rfe_response.ISSUES])
def test_every_issue_recognizes_its_own_uscis_wording(issue):
    out = rfe_response.parse_rfe_text(issue["uscis_wording"])
    assert issue["key"] in [i["issue_key"] for i in out["issues"]], issue["key"]


def test_ordinary_prose_matches_no_ground():
    """Precision guard. 'Dear petitioner' once matched the export-control
    ground on the substring 'ear'; every phrase is token-bounded now."""
    neutral = ("Dear petitioner, please find enclosed our earlier "
               "correspondence regarding your company and its research and "
               "development team. We look forward to hearing from you soon.")
    out = rfe_response.parse_rfe_text(neutral)
    assert out["issues"] == [], out["issues"]
    assert out["unmatched"] is True


def test_empty_notice_is_honest_about_having_nothing():
    out = rfe_response.parse_rfe_text("")
    assert out["issues"] == [] and out["unmatched"] is True
    assert "No text was supplied" in out["note"]


def _rfe_case(client, db):
    case_id = _ready_case(client, db, case_kind="extension",
                          beneficiary={"degree_field": "Computer Science"})
    profile_id = _employer_profile(client)
    pet = _bind_petitioner(db, case_id)
    pet.employer_profile_id = profile_id
    db.commit()
    _submit_item(client, case_id, "support_letter", "support letter")
    _submit_item(client, case_id, "job_description", "duties")
    return case_id


def test_packet_indexes_exhibits_per_issue_and_shows_gaps(client, db):
    case_id = _rfe_case(client, db)
    case = db.get(models.VisaApplication, case_id)
    packet = rfe_response.assemble(
        db, None, case, ["specialty_occupation", "ability_to_pay"])

    by_key = {i["issue_key"]: i for i in packet["issues"]}
    specialty = by_key["specialty_occupation"]
    assert specialty["exhibit_numbers"], "submitted exhibits must be indexed"
    on_file = [e for e in specialty["evidence"] if e["status"] == "on_file"]
    assert {e["label"] for e in on_file} >= {
        "Employer support letter tying each duty to the degree field"}
    # Everything not on file is a named gap, never omitted.
    assert "Expert opinion letter (professor or industry) tying each duty "\
           "to the degree field" in specialty["gaps"]
    assert by_key["ability_to_pay"]["gaps"]
    assert specialty["citations"] == ["8 CFR 214.2(h)(4)(iii)(A)"]
    assert specialty["taxonomy_section"] == "§1"


def test_missing_exhibits_never_read_as_on_file(client, db):
    case_id = _rfe_case(client, db)
    case = db.get(models.VisaApplication, case_id)
    packet = rfe_response.assemble(db, None, case, ["beneficiary_qualifications"])
    issue = packet["issues"][0]
    assert issue["exhibit_numbers"] == []
    assert len(issue["gaps"]) == len(issue["evidence"])
    assert packet["exhibit_counts"]["missing"] > 0


def test_narrative_is_draft_labeled_with_missing_facts_listed(client, db):
    case_id = _rfe_case(client, db)
    case = db.get(models.VisaApplication, case_id)
    packet = rfe_response.assemble(db, None, case, ["specialty_occupation"])
    narrative = packet["narrative"]
    assert narrative["draft_text"].startswith(rfe_response.DRAFT_LABEL["en"])
    assert "not a law firm" in narrative["draft_text"]
    assert narrative["engine"] == "local_template"
    # A required fact this case does not carry is NAMED, not invented.
    assert "job_duties" not in narrative["missing_facts"]
    assert "wage_level" in narrative["grounded_facts"]
    for key in narrative["missing_facts"]:
        assert key not in narrative["grounded_facts"]


def test_narrative_missing_facts_include_the_issue_specific_ones(client, db):
    case_id = _ready_case(client, db, case_kind="extension",
                          petitioner={"soc_code": "", "job_duties": ""})
    case = db.get(models.VisaApplication, case_id)
    packet = rfe_response.assemble(db, None, case, ["specialty_occupation"])
    missing = packet["narrative"]["missing_facts"]
    assert "degree_field" in missing
    assert "employer_legal_name" in missing
    assert len(set(missing)) == len(missing), "missing facts must not repeat"


class _FakeKimi:
    name = "kimi-k3-fake"

    def __init__(self, draft):
        self._draft = draft

    def _chat(self, system, user, **kwargs):
        return {"draft_text": self._draft}


def test_live_draft_with_an_invented_number_is_dropped(client, db, monkeypatch):
    case_id = _rfe_case(client, db)
    case = db.get(models.VisaApplication, case_id)
    monkeypatch.setattr(
        kimi, "get_provider",
        lambda: _FakeKimi("The beneficiary earns 987654 per year."))
    packet = rfe_response.assemble(db, None, case, ["specialty_occupation"])
    assert packet["narrative"]["engine"] == "local_template"
    assert "987654" not in packet["narrative"]["draft_text"]


def test_live_grounded_draft_is_used_and_still_labeled_draft(client, db, monkeypatch):
    case_id = _rfe_case(client, db)
    case = db.get(models.VisaApplication, case_id)
    monkeypatch.setattr(
        kimi, "get_provider",
        lambda: _FakeKimi("Exhibit 1 responds to the specialty occupation "
                          "ground under 8 CFR 214.2(h)(4)(iii)(A)."))
    packet = rfe_response.assemble(db, None, case, ["specialty_occupation"])
    assert packet["narrative"]["engine"] == "kimi-k3-fake"
    assert packet["narrative"]["draft_text"].startswith(
        rfe_response.DRAFT_LABEL["en"])


def test_unknown_issue_key_is_reported_never_mapped_to_a_neighbour(client, db):
    case_id = _rfe_case(client, db)
    case = db.get(models.VisaApplication, case_id)
    packet = rfe_response.assemble(db, None, case,
                                   ["specialty_occupatoin", "ability_to_pay"])
    assert packet["unknown_issue_keys"] == ["specialty_occupatoin"]
    assert [i["issue_key"] for i in packet["issues"]] == ["ability_to_pay"]
    assert any("not in Ellis's taxonomy" in w for w in packet["warnings"])


def test_no_issue_means_no_packet_content_and_a_loud_warning(client, db):
    case_id = _rfe_case(client, db)
    case = db.get(models.VisaApplication, case_id)
    packet = rfe_response.assemble(db, None, case, [])
    assert packet["issues"] == []
    assert any("never assumes one" in w for w in packet["warnings"])


def test_deadline_is_read_never_computed(client, db):
    case_id = _rfe_case(client, db)
    case = db.get(models.VisaApplication, case_id)

    absent = rfe_response.assemble(db, None, case, ["ability_to_pay"])
    assert absent["response_deadline"] == {
        "known": False, "date": None, "display": None, "source": "not entered"}
    assert "Ellis never calculates it" in absent["deadline_unknown_text"]

    junk = rfe_response.assemble(db, None, case, ["ability_to_pay"],
                                 response_due_date="soon")
    assert junk["response_deadline"]["known"] is False

    entered = rfe_response.assemble(db, None, case, ["ability_to_pay"],
                                    response_due_date="11/03/2026")
    assert entered["response_deadline"] == {
        "known": True, "date": "2026-11-03", "display": "11/03/2026",
        "source": "entered from the RFE notice"}


def test_premium_processing_is_explained_and_acted_on_by_nobody(client, db):
    case_id = _rfe_case(client, db)
    case = db.get(models.VisaApplication, case_id)
    packet = rfe_response.assemble(db, None, case, ["ability_to_pay"])
    pp = packet["premium_processing"]
    assert pp["ellis_action"] == "none — explained only"
    assert "new full period begins" in pp["explanation"].lower()
    assert pp["source"].startswith("https://www.uscis.gov/")
    assert packet["filed_by"].startswith("the petitioner or their attorney")


def test_packet_pdf_is_a_pdf_and_says_nothing_was_filed(client, db):
    case_id = _rfe_case(client, db)
    case = db.get(models.VisaApplication, case_id)
    packet = rfe_response.assemble(db, None, case, ["specialty_occupation"])
    pdf = rfe_response.build_packet_pdf(packet, case_kind="extension")
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 800


# --- endpoint --------------------------------------------------------------

def test_rfe_endpoint_is_petitioner_or_admin(client, db):
    case_id = _rfe_case(client, db)
    denied = client.post(f"/h1b/cases/{case_id}/rfe/assemble",
                         json={"issues": ["specialty_occupation"]}, headers=AUTH)
    assert denied.status_code == 403
    ok = client.post(f"/h1b/cases/{case_id}/rfe/assemble",
                     json={"issues": ["specialty_occupation"]},
                     headers=PETITIONER_AUTH)
    assert ok.status_code == 200, ok.text
    assert ok.json()["filed"] is False
    assert ok.json()["disclaimer_version"] == DISCLAIMER_VERSION


def test_rfe_endpoint_classifies_an_uploaded_notice(client, db):
    case_id = _rfe_case(client, db)
    up = client.post(f"/cases/{case_id}/documents", json={
        "name": "rfe.pdf", "mime": "application/pdf", "size_bytes": 100,
        "text": "The evidence does not establish that the beneficiary is "
                "qualified to perform services in a specialty occupation."},
        headers=AUTH)
    assert up.status_code == 200, up.text
    doc = db.get(models.StoredDocument, up.json()["id"])
    doc.ocr_text = ("The evidence does not establish that the beneficiary is "
                    "qualified to perform services in a specialty occupation.")
    db.commit()

    r = client.post(f"/h1b/cases/{case_id}/rfe/assemble",
                    json={"notice_document_id": doc.id, "generate_pdf": True},
                    headers=PETITIONER_AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["notice_source"] == "uploaded_document"
    assert [i["issue_key"] for i in body["issues"]] == ["beneficiary_qualifications"]
    assert body["document"]["mime"] == "application/pdf"
    assert body["document"]["url"].startswith(f"/documents/{body['document']['document_id']}/content?")

    dl = client.get(body["document"]["url"], headers=PETITIONER_AUTH)
    assert dl.status_code == 200
    assert dl.content.startswith(b"%PDF")


def test_rfe_endpoint_refuses_a_notice_with_no_readable_text(client, db):
    case_id = _rfe_case(client, db)
    up = client.post(f"/cases/{case_id}/documents", json={
        "name": "scan.pdf", "mime": "application/pdf", "size_bytes": 10},
        headers=AUTH)
    assert up.status_code == 200, up.text
    doc = db.get(models.StoredDocument, up.json()["id"])
    doc.ocr_text = ""
    db.commit()
    r = client.post(f"/h1b/cases/{case_id}/rfe/assemble",
                    json={"notice_document_id": doc.id}, headers=PETITIONER_AUTH)
    assert r.status_code == 409
    assert "cannot classify" in r.json()["detail"]["reason"]


def test_rfe_endpoint_refuses_another_cases_notice(client, db):
    case_id = _rfe_case(client, db)
    other = _rfe_case(client, db)
    up = client.post(f"/cases/{other}/documents", json={
        "name": "rfe.pdf", "mime": "application/pdf", "size_bytes": 10,
        "text": "site visit"}, headers=AUTH)
    r = client.post(f"/h1b/cases/{case_id}/rfe/assemble",
                    json={"notice_document_id": up.json()["id"]},
                    headers=PETITIONER_AUTH)
    assert r.status_code == 404


def test_rfe_issue_catalog_endpoint(client):
    r = client.get("/h1b/rfe/issues?locale=zh-Hant", headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["issues"]) == len(rfe_response.ISSUES)
    assert body["taxonomy_doc"] == "docs/H1B_RFE_TAXONOMY.md"
    assert body["issues"][0]["title"] == rfe_response.ISSUES[0]["title"]["zh-Hant"]


# ===========================================================================
# 3. Cap exemption
# ===========================================================================

def test_no_entity_facts_means_unknown_never_a_guess():
    out = cap_exemption.determine({})
    assert out["exempt"] == "unknown"
    assert out["matched_paths"] == []
    assert out["open_questions"], "an unknown must name what it needs"
    assert out["advisory"] is True
    assert all(p["status"] == "indeterminate" for p in out["paths"])


def test_higher_education_act_elements_derive_the_institution_fact():
    facts = {k: True for k in cap_exemption._HEA_ELEMENTS}
    out = cap_exemption.determine(facts)
    assert out["exempt"] is True
    assert out["matched_paths"] == ["institution_of_higher_education"]
    assert out["fact_provenance"]["institution_of_higher_education"].startswith(
        "derived from the Higher Education Act")
    assert "20 U.S.C. 1001(a) (HEA §101(a))" in out["citations"]
    assert out["evidence_needed"]


def test_an_explicit_answer_outranks_the_derived_elements():
    facts = {k: True for k in cap_exemption._HEA_ELEMENTS}
    facts["institution_of_higher_education"] = False
    out = cap_exemption.determine(facts)
    assert "institution_of_higher_education" not in out["matched_paths"]
    assert out["fact_provenance"]["institution_of_higher_education"] == "recorded"


def test_affiliated_nonprofit_needs_all_three_limbs():
    partial = {"nonprofit_organization": True,
               "affiliation_written_agreement_with_ihe": True}
    out = cap_exemption.determine(partial)
    assert out["exempt"] == "unknown"
    path = next(p for p in out["paths"]
                if p["key"] == "affiliated_or_related_nonprofit")
    assert path["status"] == "indeterminate"
    assert path["missing_facts"] == ["fundamental_activity_furthers_ihe_mission"]

    complete = {**partial, "fundamental_activity_furthers_ihe_mission": True}
    done = cap_exemption.determine(complete)
    assert done["exempt"] is True
    assert done["matched_paths"] == ["affiliated_or_related_nonprofit"]


def test_nonprofit_research_and_government_research_paths():
    nonprofit = cap_exemption.determine({"nonprofit_organization": True,
                                         "research_is_fundamental_activity": True,
                                         "government_entity": False})
    assert nonprofit["exempt"] is True
    assert "nonprofit_research_organization" in nonprofit["matched_paths"]

    government = cap_exemption.determine({"government_entity": True,
                                          "research_is_fundamental_activity": True})
    assert government["exempt"] is True
    assert "governmental_research_organization" in government["matched_paths"]


def test_beneficiary_paths_exempt_without_any_entity_fact():
    counted = cap_exemption.determine({
        "previously_counted_against_cap": True,
        "cap_number_still_available_to_beneficiary": True})
    assert counted["exempt"] is True
    assert counted["matched_paths"] == ["beneficiary_previously_counted"]
    assert "INA 214(g)(7), 8 U.S.C. 1184(g)(7)" in counted["citations"]

    physician = cap_exemption.determine(
        {"j1_physician_national_interest_waiver": True})
    assert physician["exempt"] is True
    assert physician["matched_paths"] == ["beneficiary_j1_waiver_physician"]


def test_one_open_path_keeps_the_whole_answer_unknown():
    """The rule that protects an applicant: a NO on six paths plus silence on
    the seventh is 'unknown', never 'not exempt'."""
    facts = {k: False for k in cap_exemption.FACT_KEYS}
    facts.pop("j1_physician_national_interest_waiver")
    out = cap_exemption.determine(facts)
    assert out["exempt"] == "unknown"
    open_paths = [p["key"] for p in out["paths"] if p["status"] == "indeterminate"]
    assert open_paths == ["beneficiary_j1_waiver_physician"]


def test_every_path_conclusively_negative_is_not_exempt():
    facts = {k: False for k in cap_exemption.FACT_KEYS}
    out = cap_exemption.determine(facts)
    assert out["exempt"] is False
    assert out["open_questions"] == []
    assert "cap-subject" in out["basis"]
    assert all(p["status"] == "not_met" for p in out["paths"])


def test_an_unreadable_answer_is_unknown_not_no():
    """'Maybe' must never be read as a No. Every other fact here is a hard No,
    so the government-research path turns entirely on the fuzzy answer: it
    stays open, and the determination stays 'unknown'."""
    facts = {k: False for k in cap_exemption.FACT_KEYS}
    facts["government_entity"] = True
    facts["research_is_fundamental_activity"] = "maybe"
    out = cap_exemption.determine(facts)
    assert out["exempt"] == "unknown"
    assert "research_is_fundamental_activity" in out["unreadable_facts"]
    assert any("treated as unknown rather than as a No" in n
               for n in out["notes"])
    open_paths = [p["key"] for p in out["paths"] if p["status"] == "indeterminate"]
    assert open_paths == ["governmental_research_organization"]


def test_yes_no_words_are_accepted_as_recorded_answers():
    out = cap_exemption.determine({"government_entity": "yes",
                                   "research_is_fundamental_activity": "Y"})
    assert out["exempt"] is True
    assert out["unreadable_facts"] == []


def test_a_government_entity_never_matches_the_nonprofit_research_path():
    out = cap_exemption.determine({"nonprofit_organization": True,
                                   "research_is_fundamental_activity": True,
                                   "government_entity": True})
    assert out["exempt"] is True
    assert "nonprofit_research_organization" not in out["matched_paths"]
    assert "governmental_research_organization" in out["matched_paths"]


def test_open_questions_ask_the_underlying_facts_not_the_derived_group():
    out = cap_exemption.determine({"nonprofit_organization": True,
                                   "fundamental_activity_furthers_ihe_mission": True})
    asked = {q["fact"] for q in out["open_questions"]}
    assert not any(f.startswith("_") for f in asked)
    assert "affiliation_written_agreement_with_ihe" in asked
    for question in out["open_questions"]:
        assert question["prompt"] and question["prompt"] != question["fact"]
        assert question["asked_of"] in ("petitioner", "beneficiary")


def test_exempt_result_warns_the_exemption_must_be_evidenced():
    out = cap_exemption.determine({"government_entity": True,
                                   "research_is_fundamental_activity": True})
    assert any("must be EVIDENCED" in n for n in out["notes"])


# --- case binding + endpoint ----------------------------------------------

def test_case_facts_are_read_party_correctly(client, db):
    case_id = _ready_case(client, db)
    _write_answers(client, case_id, "petitioner",
                   {"nonprofit_organization": True,
                    "research_is_fundamental_activity": True},
                   PETITIONER_AUTH)
    _write_answers(client, case_id, "beneficiary",
                   {"previously_counted_against_cap": False}, AUTH)
    case = db.get(models.VisaApplication, case_id)
    facts = cap_exemption.case_facts(db, case)
    assert facts["nonprofit_organization"] is True
    assert facts["previously_counted_against_cap"] is False
    out = cap_exemption.evaluate(db, case)
    assert out["exempt"] is True
    assert out["case_id"] == case_id


def test_cap_exemption_endpoint_is_unknown_before_any_fact(client, db):
    case_id = _ready_case(client, db)
    r = client.get(f"/h1b/cases/{case_id}/cap-exemption", headers=PETITIONER_AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["exempt"] == "unknown"
    assert body["advisory"] is True
    assert body["open_questions"]
    assert body["disclaimer_version"] == DISCLAIMER_VERSION
    assert body["answer_facts_via"]["petitioner"].endswith(
        f"/h1b/cases/{case_id}/party/petitioner/answers")


def test_cap_exemption_endpoint_is_petitioner_or_admin(client, db):
    case_id = _ready_case(client, db)
    denied = client.get(f"/h1b/cases/{case_id}/cap-exemption", headers=AUTH)
    assert denied.status_code == 403
    admin = client.get(f"/h1b/cases/{case_id}/cap-exemption", headers=ADMIN_AUTH)
    assert admin.status_code == 200


def test_cap_exemption_endpoint_after_the_wizard_writes_its_answers(client, db):
    case_id = _ready_case(client, db)
    _write_answers(client, case_id, "petitioner",
                   {k: True for k in cap_exemption._HEA_ELEMENTS},
                   PETITIONER_AUTH)
    r = client.get(f"/h1b/cases/{case_id}/cap-exemption?locale=zh-CN",
                   headers=PETITIONER_AUTH)
    body = r.json()
    assert body["exempt"] is True
    assert body["matched_paths"] == ["institution_of_higher_education"]
    assert body["paths"][0]["title"] == cap_exemption.PATHS[0]["title"]["zh-CN"]


def test_cap_exemption_question_catalog_endpoint(client):
    r = client.get("/h1b/cap-exemption/questions?locale=zh-Hant", headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["paths"]) == len(cap_exemption.PATHS)
    assert {q["fact"] for q in body["questions"]} == set(
        cap_exemption.FACT_PROMPTS)
    assert "unknown (leave unanswered)" in body["answers_accepted"]
    assert body["sources"]["hea_101a"].startswith("https://uscode.house.gov/")


# ===========================================================================
# 4. Cross-cutting contracts
# ===========================================================================

def test_locale_parity_across_every_new_string_table():
    tables = [cap_exemption.FACT_PROMPTS, rfe_response.RFE_STRINGS,
              {"deadline": rfe_response.DEADLINE_WARNING},
              {"premium": rfe_response.PREMIUM_PROCESSING["explanation"]}]
    for table in tables:
        for key, entry in table.items():
            assert set(entry) == set(LOCALES), key
            assert all(entry[loc].strip() for loc in LOCALES), key
    for path in cap_exemption.PATHS:
        assert set(path["title"]) == set(LOCALES), path["key"]
    for issue in rfe_response.ISSUES:
        assert set(issue["title"]) == set(LOCALES), issue["key"]


def test_catalogs_are_internally_consistent():
    assert len({i["key"] for i in rfe_response.ISSUES}) == len(rfe_response.ISSUES)
    for issue in rfe_response.ISSUES:
        assert issue["curing_evidence"], issue["key"]
        assert issue["citations"] and issue["uscis_wording"]
        assert issue["match_phrases"]
        for phrase in issue["match_phrases"]:
            assert phrase == phrase.lower(), (issue["key"], phrase)
    assert len({p["key"] for p in cap_exemption.PATHS}) == len(cap_exemption.PATHS)
    for path in cap_exemption.PATHS:
        for fact in path["requires"]:
            assert fact in cap_exemption.FACT_KEYS or fact.startswith("_")
        assert path["citations"] and path["evidence_needed"] and path["basis"]
    assert len({c.key for c in bulk_registration.COLUMNS}) == len(
        bulk_registration.COLUMNS)
    for column in bulk_registration.COLUMNS:
        assert column.key in bulk_registration.VALIDATORS
        assert column.asked_of in ("beneficiary", "petitioner")


def test_unknown_locale_falls_back_to_english(client, db):
    case_id = _ready_case(client, db)
    r = client.get(f"/h1b/cases/{case_id}/cap-exemption?locale=klingon",
                   headers=PETITIONER_AUTH)
    assert r.status_code == 200
    assert r.json()["attorney_disclaimer"].startswith("Ellis is not a law firm")
