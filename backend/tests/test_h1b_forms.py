"""Agent A - official H1B form fill and the paper mail packet.

Covers: exact /V values and per-checkbox recorded on-states on the I-129
(hybrid static-XFA stripped, NeedAppearances set), human-only signature lines
left empty, the applicant-worded missing list that never blocks the download,
per-party authorization (beneficiary 403), the signed short-lived download URL
(serves then expires), the ETA-9035 preparation watermark with Sections J/L
untouched, MM/DD/YYYY date formatting, and the mail packet's cover contract
(wet-ink checklist, Dallas lockbox + verify notice, dependents note, exhibit
list of accepted documents)."""
import time
from io import BytesIO

from pypdf import PdfReader

from app import models
from app.h1b import forms as h1b_forms
from app.h1b import models as h1b_models
from app.h1b.disclaimer import disclaimer
from app.main import _doc_sig
from sqlalchemy import select

from .conftest import AUTH

PETITIONER_AUTH = {"Authorization": "Bearer dev-token",
                   "X-Org-Id": "org1", "X-User-Id": "hr1"}
ADMIN_AUTH = {"Authorization": "Bearer admin-token",
              "X-Org-Id": "org1", "X-User-Id": "admin1"}


# ---------------------------------------------------------------- fixtures

def _create_case(client, **overrides):
    body = {"case_kind": "extension",
            "beneficiary_full_name": "WEI ZHANG",
            "beneficiary_email": "wei.zhang@example.com",
            "beneficiary_abroad": False, "beneficiary_in_us": True,
            "first_h1b": False}
    body.update(overrides)
    r = client.post("/h1b/cases", json=body, headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()


def _bind_petitioner(db, case_id, user_id="hr1"):
    pet = db.execute(select(h1b_models.CaseParty).where(
        h1b_models.CaseParty.application_id == case_id,
        h1b_models.CaseParty.role == "petitioner")).scalars().first()
    pet.user_id = user_id
    db.commit()


def _employer_profile(client):
    r = client.post("/h1b/employer-profiles", json={
        "legal_name": "Trip.com US Inc", "fein": "12-3456789",
        "naics_code": "561510", "address_line1": "285 Fulton St",
        "city": "New York", "state": "NY", "postal_code": "10007",
        "phone": "212-555-0100", "signatory_name": "JANE DOE",
        "signatory_title": "HR Director",
        "signatory_email": "jane.doe@trip.com",
        "signatory_phone": "212-555-0101",
        "parent_company_name": "Trip.com Group Ltd",
        "parent_company_country": "China"}, headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()["employer_profile_id"]


PETITIONER_ANSWERS = {
    "job_title": "Software Engineer",
    "wage_offer": "132000", "wage_offer_unit": "year",
    "employment_start_date": "2026-10-01",
    "employment_end_date": "2029-09-30",
    "full_time_position": True,
    "worksite_address_line1": "285 Fulton St",
    "worksite_address_city": "New York",
    "worksite_address_state": "NY",
    "worksite_address_zip": "10007",
    "soc_code": "15-1252", "soc_title": "Software Developers",
    "h1b_basis_for_classification": "extension",
    "h1b_requested_action": "extend",
    "h1b_total_workers": "1",
    "h1b_classification_symbol": "H-1B",
    "h1b_visa_classification": "H-1B",
}

BENEFICIARY_ANSWERS = {
    "surname": "ZHANG", "given_names": "WEI", "middle_name": "",
    "birth_date": "1993-04-15", "birth_country": "China",
    "citizenship_country": "China", "sex": "M",
    "passport_number": "EJ1234567", "passport_expiry": "2030-01-01",
    "passport_issue_date": "2020-01-02", "issuing_country": "China",
    "h1b_current_status": "H-1B", "current_status_expiry": "2026-12-31",
    "h1b_beneficiary_us_city": "Jersey City",
}


def _prepared_case(client, db):
    """A two-party extension case with a bound petitioner, employer profile,
    and both parties' answers on their own side of the wall."""
    pid = _employer_profile(client)
    out = _create_case(client, employer_profile_id=pid)
    case_id = out["case_id"]
    _bind_petitioner(db, case_id)
    r = client.post(f"/h1b/cases/{case_id}/party/petitioner/answers",
                    json={"answers": PETITIONER_ANSWERS},
                    headers=PETITIONER_AUTH)
    assert r.status_code == 200, r.text
    r = client.post(f"/h1b/cases/{case_id}/party/beneficiary/answers",
                    json={"answers": BENEFICIARY_ANSWERS}, headers=AUTH)
    assert r.status_code == 200, r.text
    return case_id


def _download_pdf(client, url):
    res = client.get(url)
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("application/pdf")
    return PdfReader(BytesIO(res.content))


# ---------------------------------------------------------------- build_fill

def test_build_fill_uses_exact_recorded_on_states_never_a_generic_on():
    plan = h1b_forms.build_fill("i-129", {
        "h1b_basis_for_classification": "extension",
        "h1b_requested_action": "extend", "sex": "F"})
    assert plan["tick_values"]["form1[0].#subform[1].continuation[0]"] == "/1"
    assert plan["tick_values"]["form1[0].#subform[1].P2Checkbox4[2]"] == "/C"
    assert plan["tick_values"]["form1[0].#subform[2].Line1_Gender_P3[1]"] == "/F"
    # The ETA-9035 prevailing-wage source ticks are TEXT fields whose recorded
    # on-state is the literal string 'X' - never the consular '/On'.
    plan = h1b_forms.build_fill("eta-9035", {"h1b_pw_source": "oes"})
    assert plan["tick_values"]["undefined_14"] == "X"


def test_build_fill_enforces_exactly_one_tick_per_group():
    # A multi-valued answer to a select-only-one group ticks NOTHING and the
    # key is reported missing rather than double-ticked.
    plan = h1b_forms.build_fill("i-129", {
        "h1b_requested_action": ["extend", "amend"]})
    assert not plan["tick_values"]
    assert "h1b_requested_action" in {m["key"] for m in plan["missing"]}
    # An option the map does not record stays untouched (unfilled beats wrong).
    plan = h1b_forms.build_fill("i-129", {"h1b_requested_action": "teleport"})
    assert not plan["tick_values"]
    # A real choice produces exactly one tick for the group.
    plan = h1b_forms.build_fill("i-129", {"h1b_requested_action": "extend"})
    ticked = [f for f in plan["tick_values"] if "P2Checkbox4" in f]
    assert ticked == ["form1[0].#subform[1].P2Checkbox4[2]"]


def test_build_fill_never_writes_human_only_fields():
    m = h1b_forms.load_form_map("i-129")
    # Even a hostile answers dict cannot reach a signature line: no mapped key
    # resolves to a human-only field, and the plan skips them structurally.
    plan = h1b_forms.build_fill("i-129", {k: "x" for k in
                                          list(m["fields"].values())})
    for field in m["human_only"]:
        assert field not in plan["text_values"]
        assert field not in plan["tick_values"]


def test_dates_format_mmddyyyy_and_fail_closed():
    plan = h1b_forms.build_fill("i-129", {"birth_date": "1993-04-15"})
    assert plan["text_values"][
        "form1[0].#subform[2].Line6_DateOfBirth[0]"] == "04/15/1993"
    # A non-ISO stored date is never half-translated onto a federal form.
    plan = h1b_forms.build_fill("i-129", {"birth_date": "15/04/1993"})
    assert "form1[0].#subform[2].Line6_DateOfBirth[0]" not in plan["text_values"]
    assert "birth_date" in {m["key"] for m in plan["missing"]}


# ---------------------------------------------------------------- I-129 fill

def test_prepare_i129_fills_values_ticks_and_strips_xfa(client, db):
    case_id = _prepared_case(client, db)
    r = client.post(f"/h1b/cases/{case_id}/forms/i-129/prepare",
                    headers=PETITIONER_AUTH)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["form_key"] == "i-129"
    assert out["filled_count"] > 10
    assert out["total_mapped"] >= out["filled_count"]
    assert out["attorney_disclaimer"] == disclaimer("en")
    assert 0 < out["expires_in"] <= 300

    pdf = _download_pdf(client, out["download_url"])
    acroform = pdf.trailer["/Root"]["/AcroForm"]
    # Hybrid static-XFA: the XFA layer must be gone or Acrobat shows a blank
    # form; appearances must be rebuilt so ticks are visible.
    assert "/XFA" not in acroform
    assert bool(acroform.get("/NeedAppearances")) is True  # BooleanObject

    fields = pdf.get_fields()
    assert fields["form1[0].#subform[0].Line3_CompanyorOrgName[0]"].get(
        "/V") == "Trip.com US Inc"
    assert fields["form1[0].#subform[0].TextField1[0]"].get("/V") == "123456789"
    # Extension slice checkbox states: basis continuation '/1', action '/C'.
    assert fields["form1[0].#subform[1].continuation[0]"].get("/V") == "/1"
    assert fields["form1[0].#subform[1].P2Checkbox4[2]"].get("/V") == "/C"
    assert fields["form1[0].#subform[2].Line1_Gender_P3[0]"].get("/V") == "/M"
    # Dates land in the form's own MM/DD/YYYY order.
    assert fields["form1[0].#subform[2].Line6_DateOfBirth[0]"].get(
        "/V") == "04/15/1993"
    assert fields["form1[0].#subform[4].Part5_Q10_DateFrom[0]"].get(
        "/V") == "10/01/2026"
    # Beneficiary identity crossed the wall through the whitelist only.
    assert fields["form1[0].#subform[1].Part3_Line2_FamilyName[0]"].get(
        "/V") == "ZHANG"

    # Human-only signature and date lines stay empty - personal acts.
    for sig in ("form1[0].#subform[6].P5_Line6a_SignatureofApplicant[0]",
                "form1[0].#subform[6].Line1b_DateofSignature[0]",
                "form1[0].#subform[6].Line_Signature[0]",
                "form1[0].#subform[6].Line_DateofSignature[0]",
                "form1[0].#subform[15].P5_Line6a_SignatureofApplicant[2]",
                "form1[0].#subform[15].Sect1_DateSignedByPetitioner[0]"):
        assert fields[sig].get("/V") in (None, ""), sig


def test_missing_list_is_applicant_worded_and_never_blocks_download(client, db):
    out = _create_case(client)
    case_id = out["case_id"]
    _bind_petitioner(db, case_id)
    r = client.post(f"/h1b/cases/{case_id}/forms/i-129/prepare",
                    headers=PETITIONER_AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    missing_keys = {m["key"] for m in body["missing"]}
    assert "job_title" in missing_keys
    assert "employer_fein" in missing_keys
    by_key = {m["key"]: m["label"] for m in body["missing"]}
    # Applicant-worded, never the raw key.
    assert by_key["employer_fein"] != "employer_fein"
    assert "FEIN" in by_key["employer_fein"]
    # The download still exists - a partly-filled official blank is the honest
    # artifact and missing answers are a question, not a lock.
    assert body["download_url"]
    _download_pdf(client, body["download_url"])


def test_prepared_form_stored_as_petitioner_document(client, db):
    case_id = _prepared_case(client, db)
    r = client.post(f"/h1b/cases/{case_id}/forms/i-129/prepare",
                    headers=PETITIONER_AUTH)
    assert r.status_code == 200
    doc = db.get(models.StoredDocument, r.json()["document_id"])
    assert doc.doc_type == "prepared_form"
    assert doc.application_id == case_id
    assert doc.extracted_fields.get("party") == "petitioner"
    blob = db.get(models.DocumentBlob, doc.id)
    assert blob is not None and blob.content.startswith(b"%PDF")


def test_unknown_form_key_404(client, db):
    case_id = _prepared_case(client, db)
    r = client.post(f"/h1b/cases/{case_id}/forms/ds-160/prepare",
                    headers=PETITIONER_AUTH)
    assert r.status_code == 404


# ---------------------------------------------------------------- party wall

def test_beneficiary_gets_403_on_both_endpoints(client, db):
    case_id = _prepared_case(client, db)
    # AUTH is the beneficiary's account; these are petitioner acts.
    r = client.post(f"/h1b/cases/{case_id}/forms/i-129/prepare", headers=AUTH)
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["acting_party"] == "petitioner"
    r = client.post(f"/h1b/cases/{case_id}/paper-packet", headers=AUTH)
    assert r.status_code == 403, r.text
    # An admin may act for either party.
    r = client.post(f"/h1b/cases/{case_id}/forms/eta-9035/prepare",
                    headers=ADMIN_AUTH)
    assert r.status_code == 200, r.text


def test_lca_form_carries_no_beneficiary_facts(client, db):
    """DOL never sees the worker: the ETA-9035 fill dict is petitioner-only."""
    case_id = _prepared_case(client, db)
    parent = db.get(models.VisaApplication, case_id)
    answers = h1b_forms.answers_for_form(db, parent, "eta-9035")
    for key in ("surname", "given_names", "passport_number", "birth_date",
                "full_name"):
        assert key not in answers, key


# ---------------------------------------------------------------- download URL

def test_download_url_serves_bytes_then_expires(client, db):
    case_id = _prepared_case(client, db)
    r = client.post(f"/h1b/cases/{case_id}/forms/i-129/prepare",
                    headers=PETITIONER_AUTH)
    assert r.status_code == 200
    out = r.json()
    doc_id = out["document_id"]
    # The minted URL serves the bytes (signature IS the authorization).
    res = client.get(out["download_url"])
    assert res.status_code == 200
    assert res.content.startswith(b"%PDF")
    # A tampered signature is refused.
    future = int(time.time()) + 60
    assert client.get(
        f"/documents/{doc_id}/content?exp={future}&sig=deadbeef").status_code == 401
    # An expired link is refused even with a genuine signature.
    past = int(time.time()) - 60
    assert client.get(
        f"/documents/{doc_id}/content?exp={past}&sig={_doc_sig(doc_id, past)}"
    ).status_code == 401


# ---------------------------------------------------------------- ETA-9035

def test_eta9035_watermark_and_j_l_untouched(client, db):
    case_id = _prepared_case(client, db)
    r = client.post(f"/h1b/cases/{case_id}/forms/eta-9035/prepare",
                    headers=PETITIONER_AUTH)
    assert r.status_code == 200, r.text
    out = r.json()
    assert "FLAG" in out["preparation_notice"]

    pdf = _download_pdf(client, out["download_url"])
    text = pdf.pages[0].extract_text()
    assert "PREPARATION COPY" in text
    assert "filed electronically in FLAG" in text

    fields = pdf.get_fields()
    assert fields["1  Job Title"].get("/V") == "Software Engineer"
    assert fields["5  Begin Date  mmddyyyy"].get("/V") == "10/01/2026"
    # Radio question set to its recorded state, not a guessed '/On'.
    assert fields["4  Is this a fulltime position"].get("/V") == "/Yes"
    # Wage-unit tick uses this form's own recorded on-state.
    assert fields["undefined_11"].get("/V") == "/On"
    # Section J signature/date are human-only; Section L (DOL certification)
    # and the DOL-use fields are never Ellis's to fill.
    for name in ("5  Signature", "6 Date signed",
                 "Department of Labor Office of Foreign Labor Certification",
                 "Certification Date date signed"):
        assert fields[name].get("/V") in (None, ""), name


def test_eta9035_zh_locale_payload(client, db):
    case_id = _prepared_case(client, db)
    r = client.post(f"/h1b/cases/{case_id}/forms/eta-9035/prepare?locale=zh-CN",
                    headers=PETITIONER_AUTH)
    assert r.status_code == 200
    out = r.json()
    assert out["attorney_disclaimer"] == disclaimer("zh-CN")
    assert "flag.dol.gov" in out["preparation_notice"]


# ---------------------------------------------------------------- paper packet

def test_paper_packet_cover_contract_and_merged_pdf(client, db):
    case_id = _prepared_case(client, db)
    # Accept one real document on the case so the exhibit list has an entry.
    up = client.post(f"/cases/{case_id}/documents", json={
        "name": "support-letter.pdf", "mime": "application/pdf",
        "size_bytes": 2048, "checklist_item_id": "support_letter",
        "text": "Letter of support for H-1B petition. The position is a "
                "specialty occupation. In support of the beneficiary."},
        headers=AUTH)
    assert up.status_code == 200, up.text
    s = client.post(f"/cases/{case_id}/checklist/support_letter/submit",
                    json={"document_id": up.json()["id"], "confirm": True},
                    headers=AUTH)
    assert s.status_code == 200, s.text

    r = client.post(f"/h1b/cases/{case_id}/paper-packet",
                    headers=PETITIONER_AUTH)
    assert r.status_code == 200, r.text
    out = r.json()

    # Wet-ink checklist names every human-only signature line.
    assert any("Part 7" in w for w in out["wet_ink_warnings"])
    assert any("Part 8" in w for w in out["wet_ink_warnings"])
    assert any("Supplement" in w for w in out["wet_ink_warnings"])
    # Address honesty: Dallas lockbox both ways, verify notice on the payload.
    assert "uscis.gov/i-129-addresses" in out["verify_address_notice"]
    assert any("Dallas" in ln for ln in out["usps_address"])
    assert any("Lewisville" in ln for ln in out["courier_address"])
    assert "PAPER" in out["dependents_paper_notice"]
    assert out["attorney_disclaimer"] == disclaimer("en")
    # The accepted document appears as an exhibit; Ellis output never does.
    assert any(e["name"] == "support-letter.pdf" for e in out["exhibits"])
    assert all(e["doc_type"] != "prepared_form" for e in out["exhibits"])

    pdf = _download_pdf(client, out["download_url"])
    i129_pages = len(PdfReader(
        str(h1b_forms._template_path("i-129"))).pages)
    assert len(pdf.pages) == i129_pages + 2      # cover + I-129 + exhibits

    cover_text = pdf.pages[0].extract_text()
    assert "WET-INK SIGNATURES REQUIRED" in cover_text
    assert "VERIFY at uscis.gov/i-129-addresses" in cover_text
    assert "Dallas" in cover_text
    exhibit_text = pdf.pages[-1].extract_text()
    assert "EXHIBIT LIST" in exhibit_text
    assert "support-letter.pdf" in exhibit_text

    # The merged packet still carries the filled I-129 values, XFA-free.
    fields = pdf.get_fields()
    assert fields["form1[0].#subform[0].Line3_CompanyorOrgName[0]"].get(
        "/V") == "Trip.com US Inc"
    assert "/XFA" not in pdf.trailer["/Root"]["/AcroForm"]
    # And no signature line was invented along the way.
    assert fields["form1[0].#subform[6].Line_Signature[0]"].get(
        "/V") in (None, "")


def test_paper_packet_zh_notices_localized(client, db):
    case_id = _prepared_case(client, db)
    r = client.post(f"/h1b/cases/{case_id}/paper-packet?locale=zh-Hant",
                    headers=PETITIONER_AUTH)
    assert r.status_code == 200, r.text
    out = r.json()
    assert "uscis.gov/i-129-addresses" in out["verify_address_notice"]
    assert "親筆簽名" in out["wet_ink_warnings"][0]
    assert out["attorney_disclaimer"] == disclaimer("zh-Hant")
