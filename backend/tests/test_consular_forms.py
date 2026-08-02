"""Consular forms: Ellis fills the applicant's Schengen / DS-160 form from what
they actually told it, and never invents a value or fabricates an official form.

A consular form is signed under penalty of perjury and handed to a government
officer. The invariants that make it safe for Ellis to prepare one:
  * every value comes from the applicant's own answer or a checksum-verified
    document reading — a gap is a visible blank, never a plausible guess;
  * missing REQUIRED fields are reported so the case asks before the applicant
    travels to an appointment with an incomplete form;
  * Ellis renders the government's own blank PDF when it has it, otherwise a
    clearly-labelled preparation sheet — it never draws a look-alike of an
    official form;
  * a machine reading never overwrites what the applicant explicitly typed.
"""
from __future__ import annotations

import pytest

from app import consular_forms as cf


ANSWERS = {
    "surname": "ELIAS", "given_names": "NOEMI", "birth_date": "1994-08-12",
    "nationality": "USA", "sex": "F", "place_of_birth": "CALIFORNIA",
    "passport_number": "X0000000", "passport_issue_date": "2021-05-05",
    "passport_expiry_date": "2031-05-04", "issuing_country": "USA",
    "address_line1": "1 Market St", "address_city": "San Francisco",
    "address_country": "USA", "email": "a@example.com", "phone": "+1 555 0100",
    "travel_purpose": "Tourism", "arrival_date": "2026-09-20",
    "departure_date": "2026-09-30", "accommodation": "Hotel Lutetia",
    "destination_country": "FRA", "travel_document_type": "ordinary_passport",
    "marital_status": "Single",
}


def test_every_schengen_state_maps_to_the_uniform_form():
    for iso3 in ("AUT", "BEL", "BGR", "HRV", "CZE", "DNK", "EST", "FIN", "FRA",
                 "DEU", "GRC", "HUN", "ISL", "ITA", "LVA", "LIE", "LTU", "LUX",
                 "MLT", "NLD", "NOR", "POL", "PRT", "ROU", "SVK", "SVN", "ESP",
                 "SWE", "CHE"):
        assert cf.form_for_destination(iso3) == "schengen_uniform", iso3
    assert cf.form_for_destination("USA") == "ds160_prep"


def test_a_route_with_no_verified_form_gets_none_not_a_wrong_form():
    """No form is better than another country's form."""
    assert cf.form_for_destination("VNM") is None
    assert cf.form_for_destination("") is None


def test_unanswered_fields_stay_blank_and_are_reported():
    sparse = {"surname": "ELIAS", "given_names": "NOEMI"}
    p = cf.prepare("schengen_uniform", sparse)
    joined = "\n".join(p["lines"])
    assert "ELIAS" in joined
    # Nothing invented: the unanswered required fields render as blanks...
    assert cf.BLANK in joined
    # ...and are named so the case can ask the applicant.
    for key in ("passport_number", "birth_date", "email"):
        assert key in p["missing_required"]
    assert p["filled"] == 2


def test_a_complete_application_reports_no_missing_required():
    p = cf.prepare("schengen_uniform", ANSWERS)
    assert p["missing_required"] == []
    assert p["filled"] >= 20


@pytest.mark.parametrize("form_key", ["schengen_uniform", "ds160_prep"])
def test_no_answer_value_is_ever_fabricated(form_key):
    """With NO answers at all, not one field may carry invented content."""
    p = cf.prepare(form_key, {})
    for line in p["lines"]:
        assert line.endswith(cf.BLANK), line
    assert p["filled"] == 0


def test_without_the_official_blank_ellis_never_claims_an_official_form():
    """No government template on file -> a clearly-labelled preparation sheet,
    never a fabricated official form. (ds160_prep has no blank by design: the
    DS-160 exists only online at CEAC, so there is no paper original.)"""
    out = cf.build("ds160_prep", ANSWERS, applicant_name="NOEMI ELIAS")
    assert out["kind"] == "preparation_sheet"
    assert out["pdf"][:4] == b"%PDF"
    # It never guesses which government field is which without a real map.
    assert cf.fill_official_template("ds160_prep", ANSWERS) is None


def test_generated_pdf_is_deterministic():
    """Same answers -> identical bytes, so the artifact is hashable and
    comparable across case versions."""
    a = cf.build("ds160_prep", ANSWERS)["pdf"]
    b = cf.build("ds160_prep", ANSWERS)["pdf"]
    assert a == b


# --- document-driven filling ----------------------------------------------

def test_verified_passport_fills_gaps_but_never_overrides_the_applicant():
    profile = {
        "surname": {"value": "ELIAS", "needs_confirmation": False},
        "passport_number": {"value": "667490664", "needs_confirmation": False},
        "birth_date": {"value": "1994-08-12", "needs_confirmation": False},
    }
    typed = {"surname": "ELIAS-SMITH"}          # the applicant's own answer
    merged = cf.answers_from_documents(typed, profile)
    assert merged["surname"] == "ELIAS-SMITH"   # never overwritten by OCR
    assert merged["passport_number"] == "667490664"   # gap filled from passport
    assert merged["birth_date"] == "1994-08-12"


def test_low_confidence_reading_is_a_question_not_a_fact():
    profile = {"passport_number": {"value": "MAYBE123", "needs_confirmation": True}}
    merged = cf.answers_from_documents({}, profile)
    assert "passport_number" not in merged
    # ...so the form reports it as still required.
    p = cf.prepare("schengen_uniform", merged)
    assert "passport_number" in p["missing_required"]


def test_no_uploaded_passport_changes_nothing():
    assert cf.answers_from_documents(ANSWERS, None) == ANSWERS
    assert cf.answers_from_documents(ANSWERS, {}) == ANSWERS


# --- official template infrastructure --------------------------------------

def test_missing_official_blank_is_reported_not_silently_skipped():
    """An operator must be told the template is absent — a silent fallback to
    the preparation sheet would look like the official form succeeded."""
    report = cf.validate_template("ds160_prep")
    assert report["ready"] is False
    assert any("no official blank" in p for p in report["problems"])


def test_a_field_map_may_only_use_real_ellis_answer_keys(tmp_path, monkeypatch):
    """A map naming a field Ellis does not know is rejected, so a typo can
    never silently leave a government field blank on a filed form."""
    import json
    forms_dir = tmp_path / "reference" / "forms"
    forms_dir.mkdir(parents=True)
    (forms_dir / "schengen_uniform.pdf").write_bytes(b"%PDF-1.4\n")
    (forms_dir / "schengen_uniform.map.json").write_text(json.dumps(
        {"fields": {"Nachname": "surname", "Bogus": "not_an_ellis_field"}}))
    monkeypatch.setattr(cf, "_template_path",
                        lambda k: forms_dir / f"{k}.pdf")
    loaded = cf.load_field_map("schengen_uniform")
    assert loaded == {"Nachname": "surname"}   # the unknown key is dropped


def test_a_flattened_pdf_is_refused_as_a_template(tmp_path, monkeypatch):
    """A scanned/flattened PDF has no AcroForm fields and cannot be filled —
    Ellis must say so instead of returning an unfilled 'official form'."""
    forms_dir = tmp_path / "reference" / "forms"
    forms_dir.mkdir(parents=True)
    (forms_dir / "schengen_uniform.pdf").write_bytes(b"%PDF-1.4\n%not a form\n")
    monkeypatch.setattr(cf, "_template_path", lambda k: forms_dir / f"{k}.pdf")
    report = cf.validate_template("schengen_uniform")
    assert report["ready"] is False
    assert any("AcroForm" in p or "field map" in p for p in report["problems"])


# --- the official Schengen blank (added 2026-08-02) -------------------------

def test_the_official_schengen_blank_is_installed_and_ready():
    """The harmonised form from the Italian MFA (esteri.it) — the same form
    every Schengen state uses. validate_template proves the PDF is fillable
    and that every mapped field really exists in it."""
    from app import consular_forms as cf
    report = cf.validate_template("schengen_uniform")
    assert report["ready"], report["problems"]
    assert report["mapped"] >= 15


def test_filling_the_official_blank_writes_real_values():
    """Regression: prepare() returns a print-ready sheet, not a value map, so
    reading a 'values' key it never had filled the government's blank with
    NOTHING — an empty official form, which is worse than no form at all."""
    from pypdf import PdfReader
    from io import BytesIO
    from app import consular_forms as cf
    pdf = cf.fill_official_template("schengen_uniform", {
        "surname": "CHEN", "given_names": "NINGYAN",
        "passport_number": "EG1085037", "nationality": "CHINESE"})
    assert pdf, "the official blank should fill"
    fields = {k.strip(): v.get("/V")
              for k, v in (PdfReader(BytesIO(pdf)).get_fields() or {}).items()
              if v.get("/V")}
    assert fields.get("1 Surname Family name") == "CHEN"
    assert fields.get("3 First names Given names") == "NINGYAN"
    assert fields.get("13 Number of travel document") == "EG1085037"


def test_no_answers_never_produces_a_blank_official_form():
    """An official blank with nothing on it looks like Ellis produced the
    applicant's application. It must fall back to the preparation sheet."""
    from app import consular_forms as cf
    assert cf.fill_official_template("schengen_uniform", {}) is None


def test_unanswered_fields_are_left_blank_not_invented():
    from pypdf import PdfReader
    from io import BytesIO
    from app import consular_forms as cf
    pdf = cf.fill_official_template("schengen_uniform", {"surname": "CHEN"})
    fields = {k.strip(): v.get("/V")
              for k, v in (PdfReader(BytesIO(pdf)).get_fields() or {}).items()}
    assert fields.get("1 Surname Family name") == "CHEN"
    assert not fields.get("13 Number of travel document"), \
        "a passport number nobody gave must never appear on a sworn form"


# --- tick-boxes the applicant answers, Ellis ticks --------------------------

def test_checkbox_questions_are_asked_in_plain_words():
    """The form says 'Cost of travelling and living during the applicant's stay
    is covered by'; the applicant is asked who is paying."""
    from app import consular_forms as cf
    qs = {q["key"]: q for q in cf.checkbox_questions("schengen_uniform")}
    assert set(qs) >= {"marital_status", "travel_purpose", "costs_covered_by",
                       "means_of_support"}
    assert qs["costs_covered_by"]["label"] == "Who is paying for your trip?"
    assert qs["means_of_support"]["multi"] is True
    assert any(o["label"] == "Married" for o in qs["marital_status"]["options"])


def test_the_applicants_answers_tick_the_right_boxes():
    from io import BytesIO
    from pypdf import PdfReader
    from app import consular_forms as cf
    pdf = cf.fill_official_template("schengen_uniform", {
        "surname": "CHEN", "marital_status": "married",
        "travel_purpose": "tourism", "costs_covered_by": "self",
        "means_of_support": ["cash", "credit_card"]})
    fields = PdfReader(BytesIO(pdf)).get_fields() or {}
    on = {k.strip() for k, v in fields.items() if str(v.get("/V")) in ("/On", "On")}
    assert {"Married", "Tourism", "by the applicant himselfherself",
            "Cash", "Credit card"} <= on
    assert "Single" not in on and "Business" not in on


def test_an_unanswered_group_is_left_untouched():
    """This form is sworn: a box nobody ticked must stay empty, never guessed."""
    from io import BytesIO
    from pypdf import PdfReader
    from app import consular_forms as cf
    pdf = cf.fill_official_template("schengen_uniform", {"surname": "CHEN"})
    fields = PdfReader(BytesIO(pdf)).get_fields() or {}
    on = {k.strip() for k, v in fields.items() if str(v.get("/V")) in ("/On", "On")}
    assert on == set(), f"nothing should be ticked, got {on}"


def test_checkbox_appearances_are_regenerated():
    """A tick present in the data but not rendered is, on a printed consular
    form, a box that was never ticked."""
    import inspect
    from app import consular_forms as cf
    assert "set_need_appearances_writer" in inspect.getsource(cf.fill_official_template)


# --- DS-160: prepared data, never a substitute for the applicant ------------

def test_ds160_covers_the_sections_ceac_actually_asks():
    from app import consular_forms as cf
    sections = {l.split(" - ")[0] for l, _, _ in cf.DS160_PREP}
    assert {"Personal Information 1", "Passport", "Travel", "Address and Phone",
            "U.S. Point of Contact", "Family", "Previous U.S. Travel",
            "Work/Education"} <= sections
    assert len(cf.DS160_PREP) >= 45


def test_ds160_never_prepares_the_security_questions():
    """CEAC's Security and Background screen asks about arrests, deportation
    and persecution. Those are sworn by the person they are about — even a
    blank prompt shaped like one invites clicking through the most
    consequential screen of the form."""
    from app import consular_forms as cf
    blob = " ".join(f"{l} {k}" for l, k, _ in cf.DS160_PREP).lower()
    for forbidden in ("arrest", "deport", "convict", "terror", "genocide",
                      "security and background", "narcotic", "prostitut"):
        assert forbidden not in blob


def test_ds160_says_it_is_online_only_and_self_signed():
    from app import consular_forms as cf
    note = cf.FORMS["ds160_prep"]["note"].lower()
    assert "only online" in note
    assert "penalty of perjury" in note
    assert "yourself" in note or "only you" in note


# --- destinations that abolished the paper form ----------------------------

def test_the_big_online_destinations_all_have_a_preparation_sheet():
    """China moved to COVA, the UK to gov.uk, India to indianvisaonline — none
    of them has a blank PDF left to fill. What Ellis can give is every answer
    in the portal's own screen order."""
    from app import consular_forms as cf
    assert cf.form_for_destination("CHN") == "china_cova_prep"
    assert cf.form_for_destination("GBR") == "uk_online_prep"
    assert cf.form_for_destination("IND") == "india_online_prep"
    for key in ("china_cova_prep", "uk_online_prep", "india_online_prep"):
        assert len(cf.FORMS[key]["fields"]) >= 25
        assert cf.FORMS[key]["submission"] == "applicant_online"


def test_each_online_sheet_says_it_is_online_and_self_declared():
    """The applicant must not think Ellis submitted it, and must know the
    declaration is theirs."""
    from app import consular_forms as cf
    for key, host in (("china_cova_prep", "cova"),
                      ("uk_online_prep", "gov.uk"),
                      ("india_online_prep", "indianvisaonline")):
        note = cf.FORMS[key]["note"].lower()
        assert host in note, f"{key} should name where to apply"
        assert "you" in note


def test_online_sheets_follow_the_portals_own_screen_order():
    """Sections are grouped the way the site asks for them, so each screen
    fills in one pass rather than sending the applicant hunting."""
    from app import consular_forms as cf
    for key, expected in (
            ("china_cova_prep", {"Section 1", "Section 2", "Section 3"}),
            ("uk_online_prep", {"About you", "Passport", "Contact", "Travel"}),
            ("india_online_prep", {"Applicant Details", "Passport Details",
                                   "Visa Details"})):
        sections = {l.split(" - ")[0] for l, _, _ in cf.FORMS[key]["fields"]}
        assert expected <= sections, f"{key} missing {expected - sections}"


def test_no_online_sheet_pretends_an_official_blank_exists():
    from app import consular_forms as cf
    for key in ("china_cova_prep", "uk_online_prep", "india_online_prep"):
        assert cf.official_template_available(key) is False
        assert cf.fill_official_template(key, {"surname": "X"}) is None
