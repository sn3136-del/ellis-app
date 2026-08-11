"""Agent 5 - the LCA Public Access File (20 CFR 655.760) and the posting
notice (20 CFR 655.734).

Covers: the required-item list as data with a citation per item; the manifest's
honest present/partial/missing/unknown statuses (a conditional item nobody has
answered is UNKNOWN, never quietly dropped); a notice that carries every
element the regulation names and refuses to invent an absent wage; the two
clocks (10-day posting window, one-working-day availability after filing, the
30-days-before-filing notice window, the one-year retention deadline) all
computed through app/dates.py; per-party authorization (beneficiary 403); the
attorney disclaimer on every payload; and real PDF bytes served through the
short-lived signed URL.
"""
import datetime as _dt

from io import BytesIO

from pypdf import PdfReader
from sqlalchemy import select

from app import dates, models
from app.h1b import models as h1b_models
from app.h1b import public_access_file as paf
from app.h1b.disclaimer import disclaimer

from .conftest import AUTH

PETITIONER_AUTH = {"Authorization": "Bearer dev-token",
                   "X-Org-Id": "org1", "X-User-Id": "paf-hr"}
BENEFICIARY_AUTH = {"Authorization": "Bearer dev-token",
                    "X-Org-Id": "org1", "X-User-Id": "paf-worker"}
ADMIN_AUTH = {"Authorization": "Bearer admin-token",
              "X-Org-Id": "org1", "X-User-Id": "admin1"}


# ---------------------------------------------------------------- fixtures

PETITIONER_ANSWERS = {
    "job_title": "Software Engineer",
    "soc_code": "15-1252", "soc_title": "Software Developers",
    "wage_offer": "132000", "wage_offer_unit": "year",
    "prevailing_wage": "121300",
    "employment_start_date": "2026-10-01",
    "employment_end_date": "2029-09-30",
    "full_time_position": True,
    "worksite_address_line1": "285 Fulton St",
    "worksite_address_city": "New York",
    "worksite_address_state": "NY",
    "worksite_address_zip": "10007",
    "h1b_total_workers": "1",
}


def _employer_profile(client, **overrides):
    body = {"legal_name": "Trip.com US Inc", "fein": "12-3456789",
            "naics_code": "561510", "address_line1": "285 Fulton St",
            "city": "New York", "state": "NY", "postal_code": "10007",
            "phone": "212-555-0100", "signatory_name": "JANE DOE",
            "signatory_title": "HR Director",
            "signatory_email": "jane.doe@trip.com",
            "signatory_phone": "212-555-0101"}
    body.update(overrides)
    r = client.post("/h1b/employer-profiles", json=body, headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()["employer_profile_id"]


def _bind_parties(db, case_id):
    rows = db.execute(select(h1b_models.CaseParty).where(
        h1b_models.CaseParty.application_id == case_id)).scalars().all()
    for row in rows:
        row.user_id = "paf-hr" if row.role == "petitioner" else "paf-worker"
    db.commit()


def _attest(db, profile_id, *, dependent=None, willful=None):
    """Set the two penalty-of-perjury attestations directly on the employer
    profile. They are deliberately not free-form answers (h1b/api.py refuses
    them on the answers endpoint), so a fixture writes the columns."""
    row = db.get(h1b_models.EmployerProfile, profile_id)
    row.h1b_dependent = dependent
    row.willful_violator = willful
    db.commit()


def _case(client, db, *, answers=None, attestations=None):
    """A two-party extension case with a bound petitioner and beneficiary and
    the petitioner's own LCA facts on their side of the wall."""
    pid = _employer_profile(client)
    if attestations is not None:
        _attest(db, pid, **attestations)
    r = client.post("/h1b/cases", json={
        "case_kind": "extension", "beneficiary_full_name": "WEI ZHANG",
        "beneficiary_email": "wei.zhang@example.com",
        "beneficiary_abroad": False, "beneficiary_in_us": True,
        "first_h1b": False, "employer_profile_id": pid}, headers=AUTH)
    assert r.status_code == 200, r.text
    case_id = r.json()["case_id"]
    _bind_parties(db, case_id)
    payload = dict(PETITIONER_ANSWERS)
    if answers is not None:
        payload = dict(answers)
    r = client.post(f"/h1b/cases/{case_id}/party/petitioner/answers",
                    json={"answers": payload}, headers=PETITIONER_AUTH)
    assert r.status_code == 200, r.text
    return case_id


def _parent(db, case_id):
    return db.get(models.VisaApplication, case_id)


def _download(client, url):
    res = client.get(url)
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("application/pdf")
    return res.content


def _pdf_text(content: bytes) -> str:
    reader = PdfReader(BytesIO(content))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


# ------------------------------------------------- (a) the contents, as data

def test_paf_contents_is_data_with_a_citation_per_item():
    assert len(paf.PAF_CONTENTS) == 10          # 20 CFR 655.760(a)(1)-(10)
    seen = []
    for i, item in enumerate(paf.PAF_CONTENTS, start=1):
        assert item["citation"] == f"20 CFR 655.760(a)({i})"
        assert item["title"] and item["description"]
        assert item["applies"] in ("always", "conditional")
        if item["applies"] == "conditional":
            # A conditional item must say what decides it AND how to ask.
            assert item["condition"] and item["condition_question"]
        seen.append(item["item_id"])
    assert len(set(seen)) == len(seen)
    # The items the statute names, present by id.
    for item_id in ("certified_lca", "wage_rate_documentation",
                    "actual_wage_memorandum", "prevailing_wage_determination",
                    "notice_documentation", "benefits_summary",
                    "corporate_change_statement", "exempt_nonimmigrant_list",
                    "recruitment_summary"):
        assert item_id in seen


# ------------------------------------------------------- (b) posting notice

def _facts(**overrides):
    facts = {"employer_legal_name": "Trip.com US Inc",
             "employer_address_line1": "285 Fulton St",
             "employer_city": "New York", "employer_state": "NY",
             "employer_postal_code": "10007", **PETITIONER_ANSWERS}
    facts.update(overrides)
    return {k: v for k, v in facts.items() if v is not None}


def test_notice_carries_every_element_the_regulation_names():
    notice = paf.build_posting_notice(_facts())
    assert notice["ready_to_post"] is True
    assert notice["missing"] == []
    elements = {e["element"]: e for e in notice["elements"]}
    for key, _label in paf.NOTICE_ELEMENTS:
        assert elements[key]["status"] == "present", key
        assert elements[key]["citation"] == "20 CFR 655.734(a)(1)(ii)"

    text = "\n".join(notice["lines"])
    assert "Number of H-1B nonimmigrants sought: 1" in text
    assert "Software Developers (SOC/O*NET code 15-1252)" in text
    assert "$132,000 per year" in text
    # Period of employment renders through app/dates.py display format.
    assert "10/01/2026 through 09/30/2029" in text
    assert "285 Fulton St, New York, NY 10007" in text
    assert "available for public inspection at" in text
    assert paf.COMPLAINT_STATEMENT.split(".")[0] in " ".join(notice["lines"])
    # A public workplace posting never names the worker.
    assert "ZHANG" not in text.upper()


def test_notice_refuses_to_invent_an_absent_wage():
    facts = _facts()
    facts.pop("wage_offer")
    notice = paf.build_posting_notice(facts)
    assert notice["ready_to_post"] is False
    assert "wage_offer" in {m["key"] for m in notice["missing"]}
    text = "\n".join(notice["lines"])
    assert "DRAFT - DO NOT POST" in text
    assert "Wages offered: [MISSING" in text
    # No number, no zero, no "per year" alone - nothing that reads as a wage.
    assert "$" not in text.split("INSTRUCTIONS TO THE EMPLOYER")[0]
    assert "132,000" not in text

    # A wage with no rate period is not a wage either.
    facts = _facts()
    facts.pop("wage_offer_unit")
    notice = paf.build_posting_notice(facts)
    assert notice["ready_to_post"] is False
    assert "wage_offer_unit" in {m["key"] for m in notice["missing"]}
    assert "$132,000" not in "\n".join(notice["lines"])

    # An unparseable amount is missing, never echoed onto a posted notice.
    notice = paf.build_posting_notice(_facts(wage_offer="competitive"))
    assert notice["ready_to_post"] is False
    assert "competitive" not in "\n".join(notice["lines"])


def test_notice_adds_the_dependent_statement_only_when_attested():
    plain = paf.build_posting_notice(_facts())
    assert paf.DEPENDENT_COMPLAINT_STATEMENT[:40] not in "\n".join(plain["lines"])
    # Unanswered dependency is stated as unanswered, never assumed "no".
    assert "UNANSWERED" in "\n".join(plain["lines"])

    dependent = paf.build_posting_notice(
        _facts(h1b_dependent_employer=True, willful_violator=False))
    text = "\n".join(dependent["lines"])
    assert "Immigrant and Employee Rights" in text
    assert "UNANSWERED" not in text
    assert any(e["element"] == "dependent_complaint_statement"
               for e in dependent["elements"])


def test_build_posting_notice_refuses_an_orm_row(db):
    row = models.VisaApplication(org_id="org1", user_id="u", applicant_id="a",
                                 destination_country="US", visa_type="h1b")
    try:
        paf.build_posting_notice(row)
    except TypeError as e:
        assert "notice_facts" in str(e)
    else:                                    # pragma: no cover - guard
        raise AssertionError("an ORM row must be refused, not half-rendered")


# --------------------------------------------------- (d) the window helpers

def test_posting_window_counts_ten_days_inclusively():
    window = paf.posting_window("2026-09-01")
    assert window["valid"] is True
    assert window["start"] == "2026-09-01"
    # Day one counts: 09-01 .. 09-10 is ten days, not eleven.
    assert window["end"] == "2026-09-10"
    assert window["end_display"] == "09/10/2026"
    assert dates.is_iso(window["end"])
    assert paf.posting_window("not a date")["valid"] is False


def test_posting_progress_tracks_the_ten_consecutive_days():
    mid = paf.posting_progress("2026-09-01", "2026-09-10",
                               today=_dt.date(2026, 9, 4))
    assert mid["consecutive_days"] == 10
    assert mid["days_elapsed"] == 4
    assert mid["days_remaining"] == 6
    assert mid["meets_ten_days"] is True
    assert mid["window_closed"] is False

    short = paf.posting_progress("2026-09-01", "2026-09-09",
                                 today=_dt.date(2026, 9, 30))
    assert short["consecutive_days"] == 9
    assert short["meets_ten_days"] is False
    assert short["window_closed"] is True

    # Time passing never fills the window on its own: the attested span rules.
    assert paf.posting_progress("2026-09-01", "2026-09-09",
                                today=_dt.date(2027, 1, 1))["meets_ten_days"] is False
    assert paf.posting_progress("", "")["valid"] is False
    assert paf.posting_progress("2026-09-10", "2026-09-01")["valid"] is False


def test_an_unfinished_posting_is_never_reported_as_ten_days_met():
    """The employer said when the notice went UP. It has not said when it came
    down, and a notice can be taken down on day three. The 10-day answer is
    UNKNOWN - never True from a start date and never True from elapsed time."""
    open_window = paf.posting_progress("2026-09-01", "",
                                       today=_dt.date(2026, 9, 4))
    assert open_window["valid"] is True
    assert open_window["meets_ten_days"] is None      # not True
    assert open_window["end_attested"] is False
    assert open_window["end"] == ""                   # no date is invented
    assert open_window["consecutive_days"] is None    # no span is attested
    # The window's last day is offered as a TARGET, under its own key.
    assert open_window["planned_end"] == "2026-09-10"
    assert "not attested" in open_window["reason"]

    # Even long after the window would have closed, silence is still silence.
    later = paf.posting_progress("2026-09-01", "", today=_dt.date(2027, 1, 1))
    assert later["meets_ten_days"] is None
    assert later["window_closed"] is True


def test_notice_timing_window_is_thirty_days_before_filing():
    assert paf.notice_timing("2026-09-01", "2026-09-15")["compliant"] is True
    assert paf.notice_timing("2026-09-15", "2026-09-15")["compliant"] is True
    late = paf.notice_timing("2026-09-16", "2026-09-15")
    assert late["compliant"] is False and late["days_before_filing"] == -1
    stale = paf.notice_timing("2026-08-01", "2026-09-15")
    assert stale["compliant"] is False and stale["days_before_filing"] == 45
    # Unknown is stated, never assumed compliant.
    assert paf.notice_timing("2026-09-01", "")["compliant"] is None


def test_one_working_day_availability_skips_the_weekend():
    # 2026-09-15 is a Tuesday -> Wednesday.
    assert paf.next_working_day("2026-09-15") == "2026-09-16"
    # 2026-09-18 is a Friday -> Monday, not Saturday.
    assert paf.next_working_day("2026-09-18") == "2026-09-21"
    # 2026-09-19 is a Saturday -> Monday.
    assert paf.next_working_day("2026-09-19") == "2026-09-21"
    assert paf.next_working_day("nonsense") == ""

    deadline = paf.availability_deadline("2026-09-18")
    assert deadline["known"] is True
    assert deadline["deadline"] == "2026-09-21"
    assert deadline["deadline_display"] == "09/21/2026"
    assert deadline["citation"] == "20 CFR 655.760(a)"
    assert paf.availability_deadline("")["known"] is False


def test_retention_deadline_is_one_year_past_the_employment_end():
    out = paf.retention_deadline(employment_end="2029-09-30")
    assert out["known"] is True and out["basis"] == "employment_end"
    assert out["keep_until"] == "2030-09-30"
    assert out["payroll_years"] == 3
    # No employment under the LCA: one year from its expiry instead.
    out = paf.retention_deadline(lca_expiry="2027-03-31")
    assert out["basis"] == "lca_expiry" and out["keep_until"] == "2028-03-31"
    assert paf.retention_deadline()["known"] is False


# --------------------------------------------------------- (c) the manifest

def test_manifest_lists_every_item_with_its_citation_and_honest_status(client, db):
    case_id = _case(client, db)
    r = client.get(f"/h1b/cases/{case_id}/paf/manifest", headers=PETITIONER_AUTH)
    assert r.status_code == 200, r.text
    out = r.json()

    assert [i["item_id"] for i in out["items"]] == list(paf.PAF_ITEM_IDS)
    for item, spec in zip(out["items"], paf.PAF_CONTENTS):
        assert item["citation"] == spec["citation"]
        assert item["status"] in paf.ITEM_STATUSES
    assert out["complete"] is False             # nothing has been filed yet
    assert out["citation"] == "20 CFR 655.760"
    assert out["as_of"] == paf.AS_OF

    by_id = {i["item_id"]: i for i in out["items"]}
    # Ellis holds the wage and prevailing-wage facts but not the documents.
    assert by_id["wage_rate_documentation"]["status"] == "partial"
    assert by_id["prevailing_wage_determination"]["status"] == "partial"
    # No fact substitute exists for these: honestly missing.
    assert by_id["actual_wage_memorandum"]["status"] == "missing"
    assert by_id["certified_lca"]["status"] == "missing"
    assert by_id["benefits_summary"]["status"] == "missing"
    assert by_id["notice_documentation"]["status"] == "missing"
    for item_id in ("actual_wage_memorandum", "certified_lca"):
        assert by_id[item_id]["next_action"]


def test_unanswered_conditional_items_are_unknown_never_quietly_dropped(client, db):
    case_id = _case(client, db)
    out = client.get(f"/h1b/cases/{case_id}/paf/manifest",
                     headers=PETITIONER_AUTH).json()
    by_id = {i["item_id"]: i for i in out["items"]}
    for item_id in ("corporate_change_statement", "single_employer_entity_list",
                    "exempt_nonimmigrant_list", "recruitment_summary"):
        assert by_id[item_id]["status"] == "unknown", item_id
        assert by_id[item_id]["reason"]
        assert by_id[item_id]["condition_question"]

    # Answered "no" -> not applicable, with the reason recorded.
    r = client.post(f"/h1b/cases/{case_id}/party/petitioner/answers",
                    json={"answers": {"h1b_corporate_change": False}},
                    headers=PETITIONER_AUTH)
    assert r.status_code == 200, r.text
    out = client.get(f"/h1b/cases/{case_id}/paf/manifest",
                     headers=PETITIONER_AUTH).json()
    by_id = {i["item_id"]: i for i in out["items"]}
    assert by_id["corporate_change_statement"]["status"] == "not_applicable"


def test_dependent_employer_items_follow_the_attested_flags(client, db):
    case_id = _case(client, db,
                    attestations={"dependent": True, "willful": False})
    out = client.get(f"/h1b/cases/{case_id}/paf/manifest",
                     headers=PETITIONER_AUTH).json()
    by_id = {i["item_id"]: i for i in out["items"]}
    assert by_id["exempt_nonimmigrant_list"]["status"] == "missing"
    assert by_id["recruitment_summary"]["status"] == "missing"

    case_id = _case(client, db,
                    attestations={"dependent": False, "willful": False})
    out = client.get(f"/h1b/cases/{case_id}/paf/manifest",
                     headers=PETITIONER_AUTH).json()
    by_id = {i["item_id"]: i for i in out["items"]}
    assert by_id["exempt_nonimmigrant_list"]["status"] == "not_applicable"
    assert by_id["recruitment_summary"]["status"] == "not_applicable"


def test_manifest_turns_a_submitted_document_into_present(client, db):
    case_id = _case(client, db)
    parent = _parent(db, case_id)
    db.add(models.StoredDocument(
        org_id=parent.org_id, application_id=parent.id,
        name="certified-lca.pdf", mime="application/pdf", size_bytes=10,
        doc_type="certified_lca", ocr_status="done"))
    db.commit()
    out = client.get(f"/h1b/cases/{case_id}/paf/manifest",
                     headers=PETITIONER_AUTH).json()
    by_id = {i["item_id"]: i for i in out["items"]}
    assert by_id["certified_lca"]["status"] == "present"
    assert by_id["certified_lca"]["evidence"][0]["name"] == "certified-lca.pdf"


# ------------------------------------------------------------ the endpoints

def test_notice_endpoint_produces_downloadable_pdf_bytes(client, db):
    case_id = _case(client, db)
    r = client.post(f"/h1b/cases/{case_id}/paf/notice", headers=PETITIONER_AUTH)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ready_to_post"] is True
    assert out["missing"] == []
    assert out["citation"] == "20 CFR 655.734(a)(1)(ii)"
    assert out["attorney_disclaimer"] == disclaimer("en")
    assert 0 < out["expires_in"] <= 300

    text = _pdf_text(_download(client, out["download_url"]))
    assert "NOTICE OF FILING OF LABOR CONDITION APPLICATION" in text
    assert "Wage and Hour Division" in text
    assert "$132,000 per year" in text

    # The artifact is registered on the case for 655.760(a)(5).
    doc = db.get(models.StoredDocument, out["document_id"])
    assert doc is not None and doc.application_id == case_id
    manifest = client.get(f"/h1b/cases/{case_id}/paf/manifest",
                          headers=PETITIONER_AUTH).json()
    by_id = {i["item_id"]: i for i in manifest["items"]}
    # Notice generated but not yet posted: partial, with the gap named.
    assert by_id["notice_documentation"]["status"] == "partial"
    assert "no posting" in by_id["notice_documentation"]["next_action"]


def test_notice_endpoint_downloads_a_draft_when_facts_are_missing(client, db):
    answers = dict(PETITIONER_ANSWERS)
    answers.pop("wage_offer")
    case_id = _case(client, db, answers=answers)
    r = client.post(f"/h1b/cases/{case_id}/paf/notice?locale=zh-CN",
                    headers=PETITIONER_AUTH)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ready_to_post"] is False
    assert "wage_offer" in {m["key"] for m in out["missing"]}
    assert out["posting_guidance"] == paf.tr("paf.not_ready_to_post", "zh-CN")
    assert out["attorney_disclaimer"] == disclaimer("zh-CN")
    # A missing fact never blocks the download - the employer sees the gap.
    assert "DRAFT - DO NOT POST" in _pdf_text(_download(client,
                                                        out["download_url"]))


def test_posting_record_hard_copy_and_its_compliance_read(client, db):
    case_id = _case(client, db)
    r = client.post(f"/h1b/cases/{case_id}/paf/posting", headers=PETITIONER_AUTH,
                    json={"method": "hard_copy", "start_date": "2026-09-01",
                          "end_date": "2026-09-10",
                          "locations": ["Main lobby notice board",
                                        "4th floor breakroom"],
                          "lca_filed_date": "2026-09-15"})
    assert r.status_code == 200, r.text
    out = r.json()
    # The end date is the employer's own attestation, and is labelled as one.
    assert out["posting"]["end_date"] == "2026-09-10"
    assert out["posting"]["end_date_attested"] is True
    assert out["posting"]["attested_by_employer"] is True
    assert out["compliance"]["compliant"] is True
    checks = {c["check"]: c for c in out["compliance"]["checks"]}
    assert checks["two_conspicuous_locations"]["passed"] is True
    assert checks["two_conspicuous_locations"]["citation"] == \
        "20 CFR 655.734(a)(1)(ii)(A)"
    assert checks["ten_days"]["passed"] is True
    assert checks["within_30_days_before_filing"]["passed"] is True
    assert out["availability"]["deadline"] == "2026-09-16"
    assert out["attested_not_verified_notice"]
    assert out["attorney_disclaimer"] == disclaimer("en")

    # It survives as the case's record and drives the manifest.
    manifest = client.get(f"/h1b/cases/{case_id}/paf/manifest",
                          headers=PETITIONER_AUTH).json()
    assert manifest["posting_record"]["locations"] == [
        "Main lobby notice board", "4th floor breakroom"]
    assert manifest["availability"]["deadline"] == "2026-09-16"


def test_a_posting_with_no_removal_date_is_recorded_but_not_called_compliant(
        client, db):
    """An employer records the posting the day it goes up - the ordinary case.
    Ellis must store exactly that, and must not invent the removal date, stamp
    it into a record labelled `attested_by_employer`, and then answer its own
    invention with a passing 10-day check."""
    case_id = _case(client, db)
    r = client.post(f"/h1b/cases/{case_id}/paf/posting", headers=PETITIONER_AUTH,
                    json={"method": "hard_copy", "start_date": "2026-09-01",
                          "locations": ["Main lobby", "Breakroom"],
                          "lca_filed_date": "2026-09-15"})
    assert r.status_code == 200, r.text
    out = r.json()
    posting = out["posting"]
    # No date the employer did not give.
    assert posting["end_date"] == ""
    assert posting["end_date_attested"] is False
    # Ellis's own arithmetic lives under its own key, clearly not an attestation.
    assert posting["planned_end_date"] == "2026-09-10"

    checks = {c["check"]: c for c in out["compliance"]["checks"]}
    assert checks["ten_days"]["passed"] is None          # unknown, not passed
    assert checks["two_conspicuous_locations"]["passed"] is True
    assert checks["within_30_days_before_filing"]["passed"] is True
    # Tri-state: not a claim of compliance, and not an accusation either.
    assert out["compliance"]["compliant"] is None
    assert out["compliance"]["failed"] == []
    assert out["compliance"]["unknown"] == ["ten_days"]

    # The manifest says which fact is missing rather than alleging a breach.
    manifest = client.get(f"/h1b/cases/{case_id}/paf/manifest",
                          headers=PETITIONER_AUTH).json()
    item = {i["item_id"]: i for i in manifest["items"]}["notice_documentation"]
    assert item["status"] == "partial"
    assert "cannot be confirmed" in item["next_action"]
    assert "ten_days" in item["next_action"]

    # And the stored row carries no invented end date either.
    step = db.execute(select(h1b_models.H1bCaseStep).where(
        h1b_models.H1bCaseStep.application_id == case_id,
        h1b_models.H1bCaseStep.step_key == "lca")).scalars().first()
    assert step.detail["public_access_file"]["posting"]["end_date"] == ""


def test_posting_record_flags_a_shortfall_instead_of_hiding_it(client, db):
    case_id = _case(client, db)
    r = client.post(f"/h1b/cases/{case_id}/paf/posting", headers=PETITIONER_AUTH,
                    json={"method": "hard_copy", "start_date": "2026-09-01",
                          "end_date": "2026-09-05",
                          "locations": ["Main lobby notice board"],
                          "lca_filed_date": "2026-10-30"})
    assert r.status_code == 200, r.text
    compliance = r.json()["compliance"]
    assert compliance["compliant"] is False
    assert set(compliance["failed"]) == {"ten_days",
                                         "two_conspicuous_locations",
                                         "within_30_days_before_filing"}


def test_electronic_notice_is_recorded_with_its_evidence(client, db):
    case_id = _case(client, db)
    r = client.post(f"/h1b/cases/{case_id}/paf/posting", headers=PETITIONER_AUTH,
                    json={"method": "electronic", "start_date": "2026-09-01",
                          "electronic_method": "intranet",
                          "electronic_evidence":
                              "Posted on the HR intranet home page for all "
                              "workers at 285 Fulton St",
                          "lca_filed_date": "2026-09-15"})
    assert r.status_code == 200, r.text
    checks = {c["check"]: c for c in r.json()["compliance"]["checks"]}
    assert checks["electronic_notice_evidence"]["passed"] is True
    assert checks["electronic_notice_evidence"]["citation"] == \
        "20 CFR 655.734(a)(1)(ii)(B)"
    assert "two_conspicuous_locations" not in checks

    # Individual direct e-mail need only be given once (ETA-9035CP).
    r = client.post(f"/h1b/cases/{case_id}/paf/posting", headers=PETITIONER_AUTH,
                    json={"method": "electronic", "start_date": "2026-09-01",
                          "end_date": "2026-09-01",
                          "electronic_method": "email",
                          "electronic_evidence": "Direct e-mail to all 40 "
                                                 "workers in the occupation",
                          "individual_direct_email": True,
                          "lca_filed_date": "2026-09-15"})
    checks = {c["check"]: c for c in r.json()["compliance"]["checks"]}
    assert checks["ten_days"]["passed"] is True


def test_posting_record_refuses_what_cannot_be_true(client, db):
    case_id = _case(client, db)
    r = client.post(f"/h1b/cases/{case_id}/paf/posting", headers=PETITIONER_AUTH,
                    json={"method": "carrier_pigeon", "start_date": "2026-09-01"})
    assert r.status_code == 400
    assert "carrier_pigeon" in r.json()["detail"]["reason"]

    r = client.post(f"/h1b/cases/{case_id}/paf/posting", headers=PETITIONER_AUTH,
                    json={"method": "hard_copy", "start_date": "sometime in May"})
    # A date app/dates.py cannot read is refused, never guessed into the record.
    assert r.status_code == 400
    assert "real date" in r.json()["detail"]["reason"]

    r = client.post(f"/h1b/cases/{case_id}/paf/posting", headers=PETITIONER_AUTH,
                    json={"method": "hard_copy", "start_date": "2026-09-10",
                          "end_date": "2026-09-01"})
    assert r.status_code == 400
    assert "before" in r.json()["detail"]["reason"]


def test_recorded_dates_are_stored_canonically_through_app_dates(client, db):
    """A U.S. employer types MM/DD/YYYY; the record keeps canonical ISO and the
    payload displays MM/DD/YYYY. One authority (app/dates.py) does both."""
    case_id = _case(client, db)
    r = client.post(f"/h1b/cases/{case_id}/paf/posting", headers=PETITIONER_AUTH,
                    json={"method": "hard_copy", "start_date": "09/01/2026",
                          "end_date": "09/10/2026",
                          "locations": ["Lobby", "Breakroom"],
                          "lca_filed_date": "09/15/2026"})
    assert r.status_code == 200, r.text
    posting = r.json()["posting"]
    assert posting["start_date"] == "2026-09-01"
    assert posting["end_date"] == "2026-09-10"
    assert posting["lca_filed_date"] == "2026-09-15"
    assert dates.is_iso(posting["start_date"])
    assert r.json()["compliance"]["progress"]["end_display"] == "09/10/2026"
    assert r.json()["availability"]["deadline_display"] == "09/16/2026"

    # The stored record is ISO on the row too, not only in the response.
    step = db.execute(select(h1b_models.H1bCaseStep).where(
        h1b_models.H1bCaseStep.application_id == case_id,
        h1b_models.H1bCaseStep.step_key == "lca")).scalars().first()
    stored = step.detail["public_access_file"]["posting"]
    assert stored["start_date"] == "2026-09-01"


def test_package_assembles_manifest_and_notice_into_one_pdf(client, db):
    case_id = _case(client, db)
    client.post(f"/h1b/cases/{case_id}/paf/notice", headers=PETITIONER_AUTH)
    client.post(f"/h1b/cases/{case_id}/paf/posting", headers=PETITIONER_AUTH,
                json={"method": "hard_copy", "start_date": "2026-09-01",
                      "locations": ["Main lobby", "Breakroom"],
                      "lca_filed_date": "2026-09-15"})
    r = client.get(f"/h1b/cases/{case_id}/paf/package", headers=PETITIONER_AUTH)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["included_notice"] is True
    assert out["manifest"]["items"]

    content = _download(client, out["download_url"])
    reader = PdfReader(BytesIO(content))
    assert len(reader.pages) >= 2
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "H-1B PUBLIC ACCESS FILE - MANIFEST" in text
    assert "20 CFR 655.760(a)(1)" in text
    assert "NOTICE OF FILING OF LABOR CONDITION APPLICATION" in text
    # The manifest states the two deadlines and the attestation caveat.
    assert "09/16/2026" in text                  # 1 working day after filing
    assert "09/30/2030" in text                  # 1 year past employment end
    # The attorney disclaimer is printed on the cover, not only in the payload.
    assert "Ellis is not a law firm" in text


def test_notice_item_turns_present_only_when_generated_and_posted(client, db):
    case_id = _case(client, db)
    client.post(f"/h1b/cases/{case_id}/paf/notice", headers=PETITIONER_AUTH)
    # The employer records the full attested window - when it went up AND when
    # it came down. Only then can the item be "present": see
    # test_an_unfinished_posting_is_never_reported_as_ten_days_met.
    client.post(f"/h1b/cases/{case_id}/paf/posting", headers=PETITIONER_AUTH,
                json={"method": "hard_copy", "start_date": "2026-09-01",
                      "end_date": "2026-09-10",
                      "locations": ["Main lobby", "Breakroom"],
                      "lca_filed_date": "2026-09-15"})
    out = client.get(f"/h1b/cases/{case_id}/paf/manifest",
                     headers=PETITIONER_AUTH).json()
    item = {i["item_id"]: i for i in out["items"]}["notice_documentation"]
    assert item["status"] == "present"
    assert item["citation"] == "20 CFR 655.760(a)(5)"
    # Both halves the regulation asks for: the notice, and where/when it ran.
    assert any(e.get("doc_type") == paf.NOTICE_ARTIFACT_KIND
               for e in item["evidence"])
    assert any(e.get("locations") for e in item["evidence"])

    # A posting that falls short drags the item back to partial, with the
    # failing check named - it never stays "present" on a stale snapshot.
    client.post(f"/h1b/cases/{case_id}/paf/posting", headers=PETITIONER_AUTH,
                json={"method": "hard_copy", "start_date": "2026-09-01",
                      "end_date": "2026-09-04", "locations": ["Main lobby"],
                      "lca_filed_date": "2026-09-15"})
    out = client.get(f"/h1b/cases/{case_id}/paf/manifest",
                     headers=PETITIONER_AUTH).json()
    item = {i["item_id"]: i for i in out["items"]}["notice_documentation"]
    assert item["status"] == "partial"
    assert "ten_days" in item["next_action"]
    assert out["posting"]["attested_not_verified"] is True


def test_a_case_without_an_lca_step_degrades_to_409(client, db):
    case_id = _case(client, db)
    step = db.execute(select(h1b_models.H1bCaseStep).where(
        h1b_models.H1bCaseStep.application_id == case_id,
        h1b_models.H1bCaseStep.step_key == "lca")).scalars().first()
    db.delete(step)
    db.commit()
    for verb, url in (("get", f"/h1b/cases/{case_id}/paf/manifest"),
                      ("post", f"/h1b/cases/{case_id}/paf/notice"),
                      ("get", f"/h1b/cases/{case_id}/paf/package")):
        r = getattr(client, verb)(url, headers=PETITIONER_AUTH)
        assert r.status_code == 409, f"{verb} {url} -> {r.status_code}"
        assert "lca" in r.json()["detail"]["reason"]


def test_every_payload_carries_the_disclaimer_and_localizes(client, db):
    case_id = _case(client, db)
    for locale in ("en", "zh-CN", "zh-Hant"):
        r = client.get(f"/h1b/cases/{case_id}/paf/manifest?locale={locale}",
                       headers=PETITIONER_AUTH)
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["attorney_disclaimer"] == disclaimer(locale)
        assert out["availability_notice"] == paf.tr("paf.availability", locale)
        assert out["retention_notice"] == paf.tr("paf.retention", locale)
        assert out["nothing_filed_notice"] == paf.tr("paf.nothing_filed", locale)
    # An unknown locale falls back to English rather than leaking a key.
    out = client.get(f"/h1b/cases/{case_id}/paf/manifest?locale=xx",
                     headers=PETITIONER_AUTH).json()
    assert out["availability_notice"] == paf.tr("paf.availability", "en")


# ------------------------------------------------------------ the party wall

def test_beneficiary_is_refused_on_every_paf_endpoint(client, db):
    case_id = _case(client, db)
    calls = [
        ("get", f"/h1b/cases/{case_id}/paf/manifest", None),
        ("post", f"/h1b/cases/{case_id}/paf/notice", None),
        ("post", f"/h1b/cases/{case_id}/paf/posting",
         {"method": "hard_copy", "start_date": "2026-09-01",
          "locations": ["a", "b"]}),
        ("get", f"/h1b/cases/{case_id}/paf/package", None),
    ]
    for verb, url, body in calls:
        kwargs = {"headers": BENEFICIARY_AUTH}
        if body is not None:
            kwargs["json"] = body
        r = getattr(client, verb)(url, **kwargs)
        assert r.status_code == 403, f"{verb} {url} -> {r.status_code}"
        assert r.json()["detail"]["acting_party"] == "petitioner"

    # An admin may act on the same endpoints.
    assert client.get(f"/h1b/cases/{case_id}/paf/manifest",
                      headers=ADMIN_AUTH).status_code == 200


def test_another_org_cannot_reach_the_file(client, db):
    case_id = _case(client, db)
    other = {"Authorization": "Bearer dev-token", "X-Org-Id": "org2",
             "X-User-Id": "paf-hr"}
    r = client.get(f"/h1b/cases/{case_id}/paf/manifest", headers=other)
    assert r.status_code in (403, 404)


def test_unknown_case_is_a_404(client, db):
    r = client.get("/h1b/cases/does-not-exist/paf/manifest",
                   headers=PETITIONER_AUTH)
    assert r.status_code == 404
