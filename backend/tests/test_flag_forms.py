"""Agent 1 - DOL FLAG preparation: the ETA-9141 PWD request + the LCA prefill
report.

The ETA-9141 is filed under penalty of perjury, so these tests hold the line
where it matters: every mapped name is a field that really exists in the
official blank, every written value reads back exactly (including the export
values that are NOT 'Yes'/'No' - '/0' means YES on one question and NO on the
next), the Department of Labor's own block stays untouched, dates go through
app/dates.py and fail closed, and a missing wage or SOC is REPORTED, never
invented. Nothing here touches the network: the wage seam reads the committed
OFLC fixture through $ELLIS_OFLC_WAGE_DIR.
"""
import json
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfReader
from sqlalchemy import select

from app import consular_forms
from app.h1b import flag_forms
from app.h1b import forms as h1b_forms
from app.h1b import models as h1b_models
from app.h1b import wage_data

from .conftest import AUTH

PETITIONER_AUTH = {"Authorization": "Bearer dev-token",
                   "X-Org-Id": "org1", "X-User-Id": "flaghr1"}

OFLC_FIXTURE = Path(__file__).parent / "fixtures" / "oflc"

# The repo copy of the blank + map (conftest points ELLIS_DATA_DIR at a copy of
# data/, so the loader reads that copy; the PDF itself is the same bytes).
PDF_PATH = consular_forms._template_path("eta-9141")
MAP_PATH = PDF_PATH.with_suffix(".map.json")


# ---------------------------------------------------------------- fixtures

@pytest.fixture
def oflc(monkeypatch):
    """The committed DOL fixture rows - never the live download."""
    monkeypatch.setenv("ELLIS_OFLC_WAGE_DIR", str(OFLC_FIXTURE))
    wage_data._CACHE.clear()
    yield
    wage_data._CACHE.clear()


@pytest.fixture
def no_oflc(monkeypatch, tmp_path):
    """No wage data on this server: every wage answer must degrade honestly."""
    monkeypatch.setenv("ELLIS_OFLC_WAGE_DIR", str(tmp_path))
    wage_data._CACHE.clear()
    yield
    wage_data._CACHE.clear()


def _employer_profile(client):
    r = client.post("/h1b/employer-profiles", json={
        "legal_name": "Trip.com US Inc", "fein": "12-3456789",
        "naics_code": "541511", "address_line1": "285 Fulton St",
        "address_line2": "Floor 80", "city": "New York", "state": "NY",
        "postal_code": "10007", "phone": "212-555-0100",
        "signatory_name": "JANE DOE", "signatory_title": "HR Director",
        "signatory_email": "jane.doe@trip.com",
        "signatory_phone": "212-555-0101"}, headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()["employer_profile_id"]


PETITIONER_ANSWERS = {
    "job_title": "Software Engineer",
    "job_duties": "Design, build and operate distributed booking services.",
    "soc_code": "15-1252",
    "wage_offer": "132000", "wage_offer_unit": "year",
    "worksite_address_line1": "285 Fulton St",
    "worksite_address_city": "New York",
    "worksite_address_state": "NY",
    "worksite_address_zip": "10007",
    "worksite_county": "New York County",
    "h1b_min_education": "bachelor",
    "h1b_min_education_field": "Computer Science",
    "h1b_experience_required": True,
    "h1b_experience_months": "24",
    "h1b_experience_occupation": "Software Engineer",
    "h1b_second_degree_required": False,
    "h1b_training_required": "no",
    "h1b_special_requirements": "Proficiency in Go and Kubernetes.",
    "h1b_supervises_others": "no",
    "h1b_travel_required": "yes",
    "h1b_travel_details": "Occasional travel to Shanghai, twice a year.",
    "h1b_multiple_worksites": "no",
    "h1b_contact_last_name": "DOE", "h1b_contact_first_name": "JANE",
    "h1b_contact_job_title": "HR Director",
}

BENEFICIARY_ANSWERS = {
    "surname": "ZHANG", "given_names": "WEI", "full_name": "WEI ZHANG",
    "birth_date": "1993-04-15", "birth_country": "China",
    "passport_number": "EJ1234567", "passport_expiry": "2030-01-01",
}


def _prepared_case(client, db, petitioner_answers=None):
    pid = _employer_profile(client)
    r = client.post("/h1b/cases", json={
        "case_kind": "extension", "beneficiary_full_name": "WEI ZHANG",
        "beneficiary_email": "wei.zhang@example.com",
        "beneficiary_abroad": False, "beneficiary_in_us": True,
        "first_h1b": False, "employer_profile_id": pid}, headers=AUTH)
    assert r.status_code == 200, r.text
    case_id = r.json()["case_id"]

    pet = db.execute(select(h1b_models.CaseParty).where(
        h1b_models.CaseParty.application_id == case_id,
        h1b_models.CaseParty.role == "petitioner")).scalars().first()
    pet.user_id = "flaghr1"
    db.commit()

    answers = (PETITIONER_ANSWERS if petitioner_answers is None
               else petitioner_answers)
    r = client.post(f"/h1b/cases/{case_id}/party/petitioner/answers",
                    json={"answers": answers}, headers=PETITIONER_AUTH)
    assert r.status_code == 200, r.text
    r = client.post(f"/h1b/cases/{case_id}/party/beneficiary/answers",
                    json={"answers": BENEFICIARY_ANSWERS}, headers=AUTH)
    assert r.status_code == 200, r.text
    return case_id


def _parent(db, case_id):
    from app import models
    return db.get(models.VisaApplication, case_id)


def _filled_fields(pdf_bytes):
    return PdfReader(BytesIO(pdf_bytes)).get_fields()


# ------------------------------------------------------- the map is real

def test_every_mapped_field_name_exists_in_the_official_blank():
    real = set((PdfReader(str(PDF_PATH)).get_fields() or {}).keys())
    assert len(real) == 79, "the pre-staged ETA-9141 blank changed"
    m = h1b_forms.load_form_map("eta-9141")

    mapped = list(m["fields"])
    for options in m["checkboxes"].values():
        mapped += list(options.values())
    mapped += [g["field"] for g in m["radio_groups"].values()]

    unknown = [name for name in mapped if name not in real]
    assert unknown == [], f"map names that are not in the PDF: {unknown}"
    assert len(mapped) == len(set(mapped)), "a PDF field is mapped twice"


def test_recorded_tick_states_match_the_pdfs_own_export_values():
    """The on-state in the map must be a state the widget really has - a
    generic '/On' on these questions would silently answer the wrong thing."""
    def _deref(obj):
        return obj.get_object() if hasattr(obj, "get_object") else obj

    reader = PdfReader(str(PDF_PATH))
    states: dict[str, set] = {}
    for page in reader.pages:
        for annot in (page.get("/Annots") or []):
            annot = _deref(annot)
            name = annot.get("/T")
            if name is None and annot.get("/Parent") is not None:
                name = _deref(annot.get("/Parent")).get("/T")
            ap = annot.get("/AP")
            if not name or not ap or "/N" not in ap:
                continue
            states.setdefault(str(name), set()).update(
                str(k) for k in _deref(ap["/N"]))

    m = h1b_forms.load_form_map("eta-9141")
    for field, state in m["checkbox_states"].items():
        assert state in states.get(field, set()), (field, state)
    for key, group in m["radio_groups"].items():
        have = states.get(group["field"], set())
        for value, state in group["states"].items():
            assert state in have, (key, value, state, sorted(have))
    # Question E.b 2 answers YES with '/0' while E.b 3 answers NO with '/0':
    # the same literal state means opposite things on adjacent questions.
    assert m["radio_groups"]["h1b_second_degree_required"]["states"]["yes"] == "/0"
    assert m["radio_groups"]["h1b_training_required"]["states"]["no"] == "/0"


def test_map_never_touches_a_signature_or_the_dol_only_block():
    raw = json.loads(MAP_PATH.read_text())
    m = h1b_forms.load_form_map("eta-9141")
    names = (list(m["fields"])
             + [f for o in m["checkboxes"].values() for f in o.values()]
             + [g["field"] for g in m["radio_groups"].values()])
    forbidden = ("signature", "signed", "pw tracking", "tracking number",
                 "case status", "validity period", "determination",
                 "expiration date", "prevailing wage")
    for name in names:
        low = name.lower()
        assert not any(word in low for word in forbidden), name
    for key in m["fields"].values():
        assert "signature" not in key and "pw_tracking" not in key, key
    # The blank carries no signature widget at all: the list is empty BY
    # CONSTRUCTION, and the map says so rather than leaving it unexplained.
    assert m["human_only"] == []
    assert "no signature or date-of-signature widget" in raw["_human_only_note"]


def test_page_four_is_the_governments_block_and_carries_no_field():
    """Section F (FOR OFFICIAL GOVERNMENT USE ONLY) plus the DOL-use header are
    static page text, so nothing Ellis writes can ever land there."""
    reader = PdfReader(str(PDF_PATH))
    assert reader.pages[3].get("/Annots") in (None, [])
    text = reader.pages[3].extract_text()
    assert "FOR OFFICIAL GOVERNMENT USE ONLY" in text
    assert "PW Tracking Number" in text


# ------------------------------------------------------- the fill itself

def test_build_fill_writes_values_and_exact_states():
    plan = h1b_forms.build_fill("eta-9141", {
        "h1b_visa_classification": "H-1B",
        "job_title": "Software Engineer",
        "soc_code": "15-1252",
        "h1b_min_education": "bachelor",
        "h1b_second_degree_required": False,   # -> '/1' (NO on this question)
        "h1b_training_required": "no",         # -> '/0' (NO on this question)
        "h1b_experience_required": True,       # -> '/Yes_3'
    })
    assert plan["text_values"]["1  Job Title"] == "Software Engineer"
    assert plan["tick_values"]["Bachelors"] == "/On"
    assert plan["tick_values"]["undefined_13"] == "/1"
    assert plan["tick_values"]["undefined_14"] == "/0"
    assert plan["tick_values"]["undefined_15"] == "/Yes_3"
    # Exactly one education box: the other six are untouched, not '/Off'-set.
    assert [f for f in plan["tick_values"]
            if f in ("None", "High SchoolGED", "Associates", "Bachelors",
                     "Master", "Doctorate PhD", "Other degree JD MD etc")
            ] == ["Bachelors"]


def test_build_fill_refuses_a_multi_valued_answer_to_a_one_of_question():
    plan = h1b_forms.build_fill("eta-9141",
                                {"h1b_min_education": ["bachelor", "master"]})
    assert plan["tick_values"] == {}
    assert "h1b_min_education" in {m["key"] for m in plan["missing"]}


def test_dates_render_mmddyyyy_and_fail_closed():
    field = "4b Survey date of publication"
    plan = h1b_forms.build_fill(
        "eta-9141", {"h1b_pwd_survey_publication_date": "2026-07-01"})
    assert plan["text_values"][field] == "07/01/2026"
    # A non-ISO stored date is never half-translated onto a federal form.
    plan = h1b_forms.build_fill(
        "eta-9141", {"h1b_pwd_survey_publication_date": "01/07/2026"})
    assert field not in plan["text_values"]
    assert "h1b_pwd_survey_publication_date" in {m["key"] for m in plan["missing"]}


def test_fill_form_reads_back_from_the_real_pdf(no_oflc):
    out = h1b_forms.fill_form("eta-9141", {
        "h1b_visa_classification": "H-1B",
        "employer_legal_name": "Trip.com US Inc",
        "employer_fein": "12-3456789",
        "employer_naics": "541511",
        "employer_address_line1": "285 Fulton St",
        "employer_city": "New York", "employer_state": "NY",
        "employer_postal_code": "10007",
        "job_title": "Software Engineer", "soc_code": "15-1252",
        "soc_title": "Software Developers",
        "job_duties": "Design, build and operate distributed booking services.",
        "h1b_special_requirements": "Proficiency in Go and Kubernetes.",
        "worksite_address_line1": "285 Fulton St",
        "worksite_address_city": "New York",
        "worksite_address_state": "NY", "worksite_address_zip": "10007",
        "h1b_min_education": "bachelor",
        "h1b_experience_required": True, "h1b_experience_months": "24",
    })
    fields = _filled_fields(out["pdf"])
    assert fields["1  Legal business name"]["/V"] == "Trip.com US Inc"
    assert fields["12  Federal Employer Identification Number FEIN from IRS"]["/V"] \
        == "12-3456789"
    assert fields["13  NAICS code must be at least 4digits"]["/V"] == "541511"
    assert fields["2  Suggested SOC ONETOES code"]["/V"] == "15-1252"
    assert fields["2a Suggested SOC ONETOES occupation title"]["/V"] \
        == "Software Developers"
    assert fields["1 Worksite address 1"]["/V"] == "285 Fulton St"
    assert fields["5  StateDistrictTerritory"]["/V"] == "NY"
    assert fields["Bachelors"]["/V"] == "/On"
    assert fields["undefined_15"]["/V"] == "/Yes_3"
    # Untouched questions keep no value at all - unfilled beats wrong.
    for name in ("None", "Master", "undefined_13", "undefined_16",
                 "4a  Survey Name", "Eb1a"):
        assert fields[name].get("/V") in (None, ""), name
    # The government's page stays a blank government page.
    assert PdfReader(BytesIO(out["pdf"])).pages[3].get("/Annots") in (None, [])
    assert out["human_only"] == []


def test_fill_is_watermarked_as_a_preparation_copy(no_oflc):
    out = h1b_forms.fill_form("eta-9141", {"job_title": "Software Engineer"})
    text = PdfReader(BytesIO(out["pdf"])).pages[0].extract_text()
    assert "PREPARATION COPY" in text
    assert "flag.dol.gov" in text


# ------------------------------------------------------- derivations

def test_derivations_are_grounded_idempotent_and_named(oflc):
    class _Case:
        visa_type = "h1b"

    first = flag_forms.derive_pwd_answers(_Case(), {
        "soc_code": "11-1021", "worksite_city": "Abilene",
        "worksite_state": "TX", "worksite_county": "Taylor County"})
    answers, derived = first["answers"], first["derived"]
    # The classification symbol comes off the case's own type.
    assert answers["h1b_visa_classification"] == "H-1B"
    # The SOC title is DOL's own title for the code already on file.
    assert answers["soc_title"] == "General and Operations Managers"
    # The worksite alias is the same recorded fact under its sibling spelling.
    assert answers["worksite_address_city"] == "Abilene"
    assert answers["worksite_address_state"] == "TX"
    assert answers["h1b_worksite_county"] == "Taylor County"
    by_key = {d["key"]: d for d in derived}
    assert set(by_key) == {"h1b_visa_classification", "soc_title",
                           "worksite_address_city", "worksite_address_state",
                           "h1b_worksite_county"}
    for entry in derived:
        assert entry["source"] and entry["label"] != entry["key"]

    # Idempotent: a second pass derives nothing and overwrites nothing.
    second = flag_forms.derive_pwd_answers(_Case(), answers)
    assert second["derived"] == []
    assert second["answers"] == answers


def test_a_party_answer_is_never_overwritten_by_a_derivation(oflc):
    class _Case:
        visa_type = "h1b"

    out = flag_forms.derive_pwd_answers(_Case(), {
        "soc_code": "11-1021", "soc_title": "Operations Manager (as stated)",
        "h1b_visa_classification": "H-1B1"})["answers"]
    assert out["soc_title"] == "Operations Manager (as stated)"
    assert out["h1b_visa_classification"] == "H-1B1"


def test_no_wage_data_derives_no_soc_title(no_oflc):
    class _Case:
        visa_type = "h1b"

    out = flag_forms.derive_pwd_answers(_Case(), {"soc_code": "11-1021"})
    assert "soc_title" not in out["answers"]
    assert [d["key"] for d in out["derived"]] == ["h1b_visa_classification"]


# ------------------------------------------------------- prepare_pwd_request

def test_prepare_pwd_request_fills_stores_and_reports(client, db, oflc):
    case_id = _prepared_case(client, db)
    out = flag_forms.prepare_pwd_request(db, _parent(db, case_id),
                                         actor="flaghr1")
    assert out["form_key"] == "eta-9141"
    assert out["filled_count"] > 0
    assert out["human_only"] == []
    assert "flag.dol.gov" in out["preparation_notice"]
    assert "never logs in" in out["human_acts_notice"]

    res = client.get(out["download_url"])
    assert res.status_code == 200
    fields = _filled_fields(res.content)
    assert fields["1  Legal business name"]["/V"] == "Trip.com US Inc"
    assert fields["1  Job Title"]["/V"] == "Software Engineer"
    assert fields["3  Address 1"]["/V"] == "285 Fulton St"     # employer block
    assert fields["1 Worksite address 1"]["/V"] == "285 Fulton St"
    assert fields["Bachelors"]["/V"] == "/On"
    assert fields["undefined_15"]["/V"] == "/Yes_3"            # experience: yes
    assert fields["perform the job duties"]["/V"] == "/Yes_2"  # travel: yes
    assert fields["undefined_17"]["/V"] == "/On"               # one worksite
    assert fields[
        "1  Indicate the type of visa classification supported by this "
        "application Write classification symbol"]["/V"] == "H-1B"

    derived = {d["key"] for d in out["derived"]}
    assert "h1b_visa_classification" in derived
    assert "soc_title" in derived      # 15-1252 -> the DOL OES title
    assert fields["2a Suggested SOC ONETOES occupation title"]["/V"] \
        == "Software Developers"


def test_generic_prepare_endpoint_fills_the_same_9141(client, db, oflc):
    case_id = _prepared_case(client, db)
    r = client.post(f"/h1b/cases/{case_id}/forms/eta-9141/prepare",
                    headers=PETITIONER_AUTH)
    assert r.status_code == 200, r.text
    out = r.json()
    fields = _filled_fields(client.get(out["download_url"]).content)
    # The endpoint path runs the same derivations - the two entry points cannot
    # produce differently-filled copies of one perjury form.
    assert fields[
        "1  Indicate the type of visa classification supported by this "
        "application Write classification symbol"]["/V"] == "H-1B"
    assert fields["2a Suggested SOC ONETOES occupation title"]["/V"] \
        == "Software Developers"


def test_pwd_request_is_petitioner_scoped(client, db, oflc):
    case_id = _prepared_case(client, db)
    # The beneficiary's own account cannot prepare the petitioner's PWD request.
    r = client.post(f"/h1b/cases/{case_id}/forms/eta-9141/prepare", headers=AUTH)
    assert r.status_code == 403, r.text
    r = client.post(f"/h1b/cases/{case_id}/forms/eta-9141/prepare",
                    headers={"Authorization": "Bearer admin-token",
                             "X-Org-Id": "org1", "X-User-Id": "admin1"})
    assert r.status_code == 200, r.text


def test_pwd_request_carries_no_beneficiary_facts(client, db, oflc):
    case_id = _prepared_case(client, db)
    out = flag_forms.prepare_pwd_request(db, _parent(db, case_id))
    text = "\n".join((page.extract_text() or "")
                     for page in PdfReader(BytesIO(
                         client.get(out["download_url"]).content)).pages)
    for secret in ("ZHANG", "WEI", "EJ1234567", "1993"):
        assert secret not in text, secret


def test_missing_wage_and_soc_are_reported_not_invented(client, db, oflc):
    case_id = _prepared_case(client, db, petitioner_answers={
        "job_title": "Software Engineer",
        "worksite_address_line1": "285 Fulton St"})
    out = flag_forms.prepare_pwd_request(db, _parent(db, case_id))
    missing = {m["key"] for m in out["missing"]}
    assert {"soc_code", "soc_title"} <= missing
    labels = {m["key"]: m["label"] for m in out["missing"]}
    assert labels["soc_code"] == "SOC (O*NET/OES) occupation code"
    assert all(m["label"] and m["label"] != m["key"] for m in out["missing"])

    fields = _filled_fields(client.get(out["download_url"]).content)
    for name in ("2  Suggested SOC ONETOES code",
                 "2a Suggested SOC ONETOES occupation title"):
        assert fields[name].get("/V") in (None, ""), name

    wage = out["wage"]
    assert wage["available"] is False
    assert wage.get("level") is None
    assert {"soc_code", "wage_offer"} <= {m["key"] for m in wage["missing_inputs"]}
    assert "does not yet record" in wage["reason"]


def test_wage_context_is_computed_but_never_written_on_the_form(client, db, oflc):
    case_id = _prepared_case(client, db, petitioner_answers={
        **PETITIONER_ANSWERS, "soc_code": "11-1021",
        "wage_offer": "85000", "wage_offer_unit": "year",
        "worksite_city": "Abilene", "worksite_state": "TX",
        "worksite_county": "Taylor County"})
    out = flag_forms.prepare_pwd_request(db, _parent(db, case_id))
    wage = out["wage"]
    assert wage["available"] is True
    assert wage["written_on_form"] is False
    assert wage["area_code"] == "10180"
    # L2 annual is 38.75 * 2080 = 80,600 and L3 is 113,298: 85,000 is Level II.
    assert wage["level"] == 2
    assert wage["prevailing_wage"] == round(23.02 * wage_data.HOURS_PER_YEAR)
    assert wage["source"] == wage_data.SOURCE
    assert wage["as_of"] == wage_data.WAGE_DATA_AS_OF

    # ...and none of it reaches the PDF: the wage is DOL's answer, not ours.
    text = "\n".join((page.extract_text() or "")
                     for page in PdfReader(BytesIO(
                         client.get(out["download_url"]).content)).pages)
    assert "47,882" not in text and "47882" not in text
    assert "85000" not in text and "85,000" not in text


def test_wage_context_degrades_honestly_without_the_dol_data(client, db, no_oflc):
    case_id = _prepared_case(client, db)
    out = flag_forms.prepare_pwd_request(db, _parent(db, case_id))
    wage = out["wage"]
    assert wage["available"] is False
    assert wage["data_status"] == "unavailable"
    assert "not downloaded" in wage["reason"]
    assert "level" not in wage or wage["level"] is None


def test_naics_check_never_rewrites_the_employers_code(client, db, no_oflc):
    case_id = _prepared_case(client, db)
    out = flag_forms.prepare_pwd_request(db, _parent(db, case_id))
    assert out["naics"] == {"code": "541511", "checked": True, "verified": True,
                            "source": "census",
                            "title": "Custom Computer Programming Services"}

    unverified = flag_forms.naics_check({"employer_naics": "999999"})
    assert unverified["verified"] is False
    assert unverified["code"] == "999999"       # reported, never replaced
    assert "not proof it is wrong" in unverified["note"].lower()
    assert flag_forms.naics_check({})["checked"] is False


def test_choice_vocabulary_comes_from_the_verified_map():
    vocab = flag_forms.choice_vocabulary()
    assert vocab["h1b_min_education"] == ["associate", "bachelor", "doctorate",
                                          "high_school", "master", "none",
                                          "other"]
    assert vocab["h1b_experience_required"] == ["no", "yes"]
    assert vocab["h1b_pwd_dba_or_sca"] == ["dba", "sca"]


# ------------------------------------------------------- LCA prefill report

def test_lca_prefill_report_matches_the_real_lca_fill(client, db, no_oflc):
    case_id = _prepared_case(client, db)
    parent = _parent(db, case_id)
    report = flag_forms.lca_prefill_report(db, parent)

    plan = h1b_forms.build_fill(
        "eta-9035", h1b_forms.answers_for_form(db, parent, "eta-9035"))
    assert {r["key"] for r in report["ellis_can_fill"]} == set(plan["filled_keys"])
    assert {r["key"] for r in report["still_needed"]} == {
        m["key"] for m in plan["missing"]}
    assert report["counts"] == {"mapped": plan["total_mapped"],
                                "ellis_can_fill": len(plan["filled_keys"]),
                                "still_needed": len(plan["missing"])}
    assert report["counts"]["ellis_can_fill"] > 0
    assert report["counts"]["still_needed"] > 0

    keys = {r["key"] for r in report["ellis_can_fill"]}
    assert {"job_title", "soc_code", "employer_legal_name", "employer_fein",
            "worksite_address_line1"} <= keys


def test_lca_prefill_report_is_applicant_worded_and_names_the_human_acts(
        client, db, no_oflc):
    case_id = _prepared_case(client, db)
    report = flag_forms.lca_prefill_report(db, _parent(db, case_id))
    for row in report["ellis_can_fill"] + report["still_needed"]:
        assert row["label"] and row["label"] != row["key"]
        assert "_" not in row["label"]
    outstanding = {r["key"]: r["label"] for r in report["still_needed"]}
    assert outstanding["h1b_wage_from_dollars"].startswith("Offered wage")
    # The signature/date lines and the DOL certification block are named as the
    # human's, never counted as something Ellis can fill.
    assert "5  Signature" in report["human_only"]
    assert not ({r["key"] for r in report["ellis_can_fill"]}
                & set(report["human_only"]))
    assert "never logs in" in report["human_acts_notice"]
    assert report["filed_at"] == "flag.dol.gov"


def test_lca_prefill_report_localizes_its_own_wording(client, db, no_oflc):
    case_id = _prepared_case(client, db)
    parent = _parent(db, case_id)
    for locale in ("en", "zh-CN", "zh-Hant"):
        report = flag_forms.lca_prefill_report(db, parent, locale=locale)
        assert report["intro"] == flag_forms.tr("flag.lca_report", locale)
        assert report["human_acts_notice"] == flag_forms.tr("flag.human_acts",
                                                            locale)
    assert flag_forms.tr("flag.human_acts", "zh-CN") \
        != flag_forms.tr("flag.human_acts", "en")


def test_a_case_without_an_lca_step_raises_rather_than_guessing(db, client):
    from app import models
    orphan = models.VisaApplication(
        org_id="org1", user_id="user1", applicant_id="none",
        destination_country="United States", visa_type="h1b", answers={})
    db.add(orphan)
    db.commit()
    with pytest.raises(LookupError):
        flag_forms.prepare_pwd_request(db, orphan)
    with pytest.raises(LookupError):
        flag_forms.lca_prefill_report(db, orphan)
