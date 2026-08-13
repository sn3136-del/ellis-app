"""Agent 1 - the ETA-9141 remapped onto the newer (04/30/2021) blank.

The 2021 blank is a STRICT SUPERSET of the 2019 one: all 79 of that edition's
fields exist here under byte-identical names, plus 51 more. What those 51 are
matters more than the count, and these tests pin it:

  * NOT ONE of them is a new employer question. 22 are Section F (Prevailing
    Wage Determination, headed FOR OFFICIAL GOVERNMENT USE ONLY), 12 are the
    DOL-use running footer's tracking/status/validity slots, and 17 are printed
    page text the form generator turned into widgets - three of which sit ON TOP
    of the printed E.b question lines, where a write would print over the
    government's own page.
  * The expected new employer-side wage question does not exist. The piece-rate
    'wage offer requirements' box is Section F item 5a, under DOL's own 'Per:'
    question, inside the government block. Neither edition has an employer
    'wage offered' field or an employment begin/end date field - those live on
    the ETA-9035 (LCA).
  * The 2019 blank's page 4 carried no widget at all, so nothing could land in
    the government's block BY CONSTRUCTION. On this blank it can. Only the map's
    never-write list and build_fill's hard skip stop it - so both are tested
    directly, including the case where Ellis HAS computed a prevailing wage and
    still refuses to write it.

Every field name and every tick state below is read out of the real PDF, never
typed from the picture. Nothing here touches the network.
"""
import json
from io import BytesIO

import pytest
from pypdf import PdfReader

from app import consular_forms
from app.h1b import flag_forms
from app.h1b import forms as h1b_forms
from app.h1b import wage_data

from .test_flag_forms import (AUTH, OFLC_FIXTURE, PETITIONER_ANSWERS,
                              PETITIONER_AUTH, _filled_fields, _parent,
                              _prepared_case)

FORM_KEY = "eta-9141-2021"
PDF_PATH = consular_forms._template_path(FORM_KEY)
MAP_PATH = PDF_PATH.with_suffix(".map.json")

OLD_FORM_KEY = "eta-9141"
OLD_PDF_PATH = consular_forms._template_path(OLD_FORM_KEY)


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


def _raw_map():
    return json.loads(MAP_PATH.read_text())


def _mapped_names(form_key=FORM_KEY):
    m = h1b_forms.load_form_map(form_key)
    return (list(m["fields"])
            + [f for o in m["checkboxes"].values() for f in o.values()]
            + [g["field"] for g in m["radio_groups"].values()])


def _widget_states(path):
    """{field name: {every state name in that widget's own /AP /N}} - the PDF's
    truth, not the map's claim."""
    def _deref(obj):
        return obj.get_object() if hasattr(obj, "get_object") else obj

    states: dict[str, set] = {}
    for page in PdfReader(str(path)).pages:
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
    return states


# A full, realistic employer answer set - enough that every mapped question is
# answered, so "the government's block is still empty" means something.
FULL_ANSWERS = {
    **PETITIONER_ANSWERS,
    "h1b_visa_classification": "H-1B",
    "employer_legal_name": "Trip.com US Inc",
    "employer_dba": "Trip Travel",
    "employer_fein": "12-3456789",
    "employer_naics": "541511",
    "employer_address_line1": "285 Fulton St",
    "employer_city": "New York", "employer_state": "NY",
    "employer_postal_code": "10007", "h1b_employer_country": "United States",
    "soc_title": "Software Developers",
    "h1b_pwd_acwia_covered": "no", "h1b_pwd_cba_covered": "no",
    "h1b_pwd_dba_sca_requested": "no", "h1b_pwd_survey_requested": "no",
    "h1b_worksite_county": "New York County",
    "h1b_min_education_field": "Computer Science",
}


# ------------------------------------------------- the map is the PDF's truth

def test_every_mapped_field_name_exists_in_the_2021_blank():
    reader = PdfReader(str(PDF_PATH))
    real = set((reader.get_fields() or {}).keys())
    assert len(reader.pages) == 4
    assert len(real) == 130, "the pre-staged 2021 ETA-9141 blank changed"
    assert "Expiration Date: 04/30/2021" in reader.pages[0].extract_text()

    mapped = _mapped_names()
    unknown = [name for name in mapped if name not in real]
    assert unknown == [], f"map names that are not in the PDF: {unknown}"
    assert len(mapped) == len(set(mapped)), "a PDF field is mapped twice"


def test_the_2021_blank_is_a_strict_superset_so_the_mapping_is_re_verified():
    """The employer-completed mapping is the 2019 mapping proved again against
    a second PDF - not a second guess at the same questions."""
    old = set((PdfReader(str(OLD_PDF_PATH)).get_fields() or {}).keys())
    new = set((PdfReader(str(PDF_PATH)).get_fields() or {}).keys())
    assert len(old) == 79 and len(new) == 130
    assert old < new, "the 2021 blank renamed or dropped a 2019 field"

    old_map = h1b_forms.load_form_map(OLD_FORM_KEY)
    new_map = h1b_forms.load_form_map(FORM_KEY)
    for part in ("fields", "checkboxes", "checkbox_states", "radio_groups"):
        assert new_map[part] == old_map[part], part
    assert set(_mapped_names()) == old


def test_every_one_of_the_130_fields_is_classified_exactly_once():
    """Mapped, government-use, or static page text: no field is left unaccounted
    for, and none is in two buckets at once."""
    real = set((PdfReader(str(PDF_PATH)).get_fields() or {}).keys())
    raw = _raw_map()
    mapped = set(_mapped_names())
    government = set(raw["_government_use_only"])
    static = set(raw["_static_page_text"])

    assert len(mapped) == 79
    assert len(government) == 34
    assert len(static) == 17
    assert mapped | government | static == real
    assert not (mapped & government) and not (mapped & static)
    assert not (government & static)
    # The two documented halves of the government bucket partition it.
    assert (set(raw["_section_f_fields"]) | set(raw["_dol_footer_fields"])
            == government)
    # Everything Ellis must never write is on ONE list the filler hard-skips.
    assert set(raw["_human_only"]) == government | static


def test_recorded_tick_states_match_the_2021_widgets_own_export_values():
    """A generic '/On' would silently answer the wrong thing: on this blank
    '/0' means YES on question E.b 2 and NO on E.b 3."""
    states = _widget_states(PDF_PATH)
    m = h1b_forms.load_form_map(FORM_KEY)
    for field, state in m["checkbox_states"].items():
        assert state in states.get(field, set()), (field, state)
    for key, group in m["radio_groups"].items():
        have = states.get(group["field"], set())
        for value, state in group["states"].items():
            assert state in have, (key, value, state, sorted(have))

    assert m["radio_groups"]["h1b_second_degree_required"]["states"] == {
        "yes": "/0", "no": "/1"}
    assert m["radio_groups"]["h1b_training_required"]["states"] == {
        "yes": "/Yes_3", "no": "/0"}
    assert m["radio_groups"]["h1b_experience_required"]["states"] == {
        "yes": "/Yes_3", "no": "/2"}
    # Every recorded on-state is a state that widget really has, and never /Off.
    for field, state in m["checkbox_states"].items():
        assert state != "/Off"


# --------------------------------------- what this edition really did add

def test_this_edition_adds_no_employer_question_only_the_governments_block():
    """The premise that the newer blank carries new EMPLOYER-side wage
    questions does not survive the PDF. Every substantive field it adds is
    inside Section F, which is headed FOR OFFICIAL GOVERNMENT USE ONLY."""
    old = set((PdfReader(str(OLD_PDF_PATH)).get_fields() or {}).keys())
    reader = PdfReader(str(PDF_PATH))
    new_only = set((reader.get_fields() or {}).keys()) - old
    raw = _raw_map()
    assert len(new_only) == 51
    # Not one of them is mapped as an employer answer.
    assert not (new_only & set(_mapped_names()))
    assert new_only == set(raw["_government_use_only"]) | set(
        raw["_static_page_text"])

    # Section F really is on page 4, and page 4 really is the government's.
    page4 = "".join((reader.pages[3].extract_text() or "").split())
    assert "FOROFFICIALGOVERNMENTUSEONLY" in page4
    assert "F.PrevailingWageDeterminati" in page4
    page4_fields = {str(a.get_object().get("/T"))
                    for a in (reader.pages[3].get("/Annots") or [])
                    if a.get_object().get("/T") is not None}
    assert set(raw["_section_f_fields"]) <= page4_fields

    # The piece-rate box reads like an employer question and is not one: it is
    # Section F item 5a, under DOL's own 'Per:' question.
    piece_rate = ("5a  If Piece Rate is indicated in question 2 specify the "
                  "wage offer requirements")
    assert piece_rate in raw["_section_f_fields"]
    assert piece_rate in page4_fields


def test_no_employer_wage_or_employment_date_question_exists_on_either_edition():
    """Reported, not invented: the PWD request never asks the employer for the
    offered wage or the employment dates - the ETA-9035 does."""
    for form_key in (OLD_FORM_KEY, FORM_KEY):
        keys = set(h1b_forms.load_form_map(form_key)["fields"].values())
        assert not (keys & {"wage_offer", "wage_offer_unit",
                            "employment_start_date", "employment_end_date",
                            "h1b_wage_from_dollars", "h1b_prevailing_wage_dollars"})


# ------------------------------------- nothing of the government's is written

def test_no_signature_or_determination_field_is_ever_mapped():
    raw = _raw_map()
    names = _mapped_names()
    forbidden = ("signature", "signed", "pw tracking", "tracking number",
                 "case status", "validity period", "determination",
                 "expiration date", "prevailing wage", "additional notes")
    for name in names:
        low = name.lower()
        assert not any(word in low for word in forbidden), name
    for key in h1b_forms.load_form_map(FORM_KEY)["fields"].values():
        assert "signature" not in key and "pw_tracking" not in key, key
    # This blank has no signature widget at all - it is signed inside FLAG -
    # so the never-write list is the government's block and the printed page
    # furniture, and the map says which is which rather than leaving it vague.
    assert ("no signature or date-of-signature widget"
            in raw["_human_only_note"].lower())
    for name in ("1 PW tracking number", "8 Determination date",
                 "9 Expiration date", "2 Date PW request received",
                 "7 Additional Notes Regarding Wage Determination",
                 "Validity Period", "PW Tracking Number"):
        assert name in raw["_human_only"], name


def test_build_fill_hard_skips_a_never_write_field_even_if_the_map_pointed_at_one():
    """Defense in depth: the never-write list is a hard skip in the filler, not
    a comment on the map. A mapping error can never reach DOL's block."""
    m = h1b_forms.load_form_map(FORM_KEY)
    broken = {**m,
              "fields": {**m["fields"], "8 Determination date": "birth_date",
                         "1 PW tracking number": "job_title"},
              "checkboxes": {**m["checkboxes"],
                             "h1b_pw_wage_level": {"i": "I", "ii": "II"}},
              "checkbox_states": {**m["checkbox_states"], "I": "/On",
                                  "II": "/On"},
              "radio_groups": {**m["radio_groups"], "bogus": {
                  "field": "9 Expiration date", "states": {"yes": "/On"}}}}
    original = h1b_forms.load_form_map
    try:
        h1b_forms.load_form_map = (
            lambda key: broken if key == FORM_KEY else original(key))
        plan = h1b_forms.build_fill(FORM_KEY, {
            "birth_date": "1993-04-15", "job_title": "Software Engineer",
            "h1b_pw_wage_level": "ii", "bogus": "yes"})
    finally:
        h1b_forms.load_form_map = original
    for name in ("8 Determination date", "1 PW tracking number", "I", "II",
                 "9 Expiration date"):
        assert name not in plan["text_values"], name
        assert name not in plan["tick_values"], name


def test_the_governments_boxes_stay_empty_even_when_ellis_knows_the_wage(
        client, db, oflc):
    """The strongest version of the rule: this case HAS a computed prevailing
    wage and an OES level, the blank HAS boxes for both, and Ellis still leaves
    every one of them empty. The wage travels in the payload as a suggestion."""
    case_id = _prepared_case(client, db, petitioner_answers={
        **FULL_ANSWERS, "soc_code": "11-1021", "wage_offer": "85000",
        "wage_offer_unit": "year", "worksite_city": "Abilene",
        "worksite_state": "TX", "worksite_county": "Taylor County"})
    out = flag_forms.prepare_pwd_request(db, _parent(db, case_id))
    assert out["form_key"] == FORM_KEY
    assert out["wage"]["available"] is True
    assert out["wage"]["level"] == 2
    assert out["wage"]["written_on_form"] is False

    fields = _filled_fields(client.get(out["download_url"]).content)
    raw = _raw_map()
    for name in raw["_human_only"]:
        assert fields[name].get("/V") in (None, ""), name
    # ...and the computed numbers appear nowhere on the paper.
    text = "\n".join((page.extract_text() or "") for page in
                     PdfReader(BytesIO(
                         client.get(out["download_url"]).content)).pages)
    assert "85,000" not in text and "85000" not in text
    for level in ("Level II", "47,882", "47882"):
        assert level not in text

    block = out["government_block"]
    assert block["fillable_on_this_blank"] is True
    assert block["written_by_ellis"] == 0
    assert block["left_empty_count"] == 51
    labels = {row["label"] for row in block["determination_block"]}
    assert "F.4 - Prevailing wage (dollars)" in labels
    assert "F.4a - OES wage level II" in labels
    assert "F.8 - Determination date" in labels
    # Opaque widget names are reported in the government's own words.
    for row in block["determination_block"] + block["dol_use_footer"]:
        assert row["field"] and row["label"]
    assert "leaves every one of them empty" in block["notice"]


def test_the_2019_and_2021_blanks_refuse_the_governments_block_differently():
    """'Ellis never writes there' means two different things, and the payload
    says which: on the 2019 blank no such widget exists; on the 2021 blank it
    exists and is deliberately skipped."""
    old = flag_forms.government_block(OLD_FORM_KEY)
    new = flag_forms.government_block(FORM_KEY)
    assert old["fillable_on_this_blank"] is False
    assert old["left_empty_count"] == 0
    assert "carries no field at all" in old["reason"]
    assert new["fillable_on_this_blank"] is True
    assert "hard-skips them" in new["reason"]
    assert PdfReader(str(OLD_PDF_PATH)).pages[3].get("/Annots") in (None, [])


def test_static_page_text_widgets_are_never_written_over():
    """Three of the new widgets sit ON the printed E.b question lines. Writing
    there would print Ellis's text over the government's own question."""
    raw = _raw_map()
    for name in ("2 Does the employer require a second US diplomadegree",
                 "3 Is training for the job opportunity required",
                 "4 Is employment experience required",
                 "Page 1 of 4", "Form ETA 9141"):
        assert name in raw["_static_page_text"]
        assert name not in _mapped_names()

    out = h1b_forms.fill_form(FORM_KEY, {
        **FULL_ANSWERS, "h1b_second_degree_required": "no",
        "h1b_training_required": "no", "h1b_experience_required": "yes"})
    fields = _filled_fields(out["pdf"])
    for name in raw["_static_page_text"]:
        assert fields[name].get("/V") in (None, ""), name
    # The real questions were answered - on the widgets that really ask them.
    assert fields["undefined_13"]["/V"] == "/1"
    assert fields["undefined_14"]["/V"] == "/0"
    assert fields["undefined_15"]["/V"] == "/Yes_3"


# ------------------------------------------------- values, dates and the fill

def test_fill_reads_back_from_the_real_2021_pdf(no_oflc):
    out = h1b_forms.fill_form(FORM_KEY, {
        **FULL_ANSWERS, "h1b_pwd_survey_requested": "yes",
        "h1b_pwd_survey_name": "Radford Global Technology Survey",
        "h1b_pwd_survey_publication_date": "2026-07-01"})
    fields = _filled_fields(out["pdf"])
    assert fields["1  Legal business name"]["/V"] == "Trip.com US Inc"
    assert fields["12  Federal Employer Identification Number FEIN from IRS"][
        "/V"] == "12-3456789"
    assert fields["13  NAICS code must be at least 4digits"]["/V"] == "541511"
    assert fields["1  Job Title"]["/V"] == "Software Engineer"
    assert fields["2  Suggested SOC ONETOES code"]["/V"] == "15-1252"
    assert fields["2a Suggested SOC ONETOES occupation title"]["/V"] \
        == "Software Developers"
    assert fields["1 Worksite address 1"]["/V"] == "285 Fulton St"
    assert fields["4  County"]["/V"] == "New York County"
    assert fields["5  StateDistrictTerritory"]["/V"] == "NY"
    assert fields["Bachelors"]["/V"] == "/On"
    assert fields["undefined_9"]["/V"] == "/On"          # survey requested: yes
    assert fields["perform the job duties"]["/V"] == "/Yes_2"
    assert fields["undefined_17"]["/V"] == "/On"         # one worksite
    # Section E.c 7a (geographic places of employment) is mapped and fillable.
    assert "h1b_additional_worksites" in set(
        h1b_forms.load_form_map(FORM_KEY)["fields"].values())
    # Untouched questions keep no value at all - unfilled beats wrong. (The
    # answered side of each pair is ticked, the other side is not even /Off-set.)
    for name in ("None", "Master", "undefined_5", "undefined_16", "Eb1a",
                 "2  Address 2"):
        assert fields[name].get("/V") in (None, ""), name


def test_the_wage_survey_date_fills_and_fails_closed():
    """The only date this blank asks the employer for is the wage survey's
    publication date; it goes through app/dates.py like every other."""
    field = "4b Survey date of publication"
    plan = h1b_forms.build_fill(
        FORM_KEY, {"h1b_pwd_survey_publication_date": "2026-07-01"})
    assert plan["text_values"][field] == "07/01/2026"

    plan = h1b_forms.build_fill(
        FORM_KEY, {"h1b_pwd_survey_publication_date": "01/07/2026"})
    assert field not in plan["text_values"]
    assert "h1b_pwd_survey_publication_date" in {m["key"]
                                                 for m in plan["missing"]}


def test_a_missing_wage_is_reported_not_invented(client, db, oflc):
    case_id = _prepared_case(client, db, petitioner_answers={
        "job_title": "Software Engineer",
        "worksite_address_line1": "285 Fulton St"})
    out = flag_forms.prepare_pwd_request(db, _parent(db, case_id))
    wage = out["wage"]
    assert wage["available"] is False
    assert wage.get("level") is None
    assert {"soc_code", "wage_offer"} <= {m["key"] for m in
                                          wage["missing_inputs"]}
    assert "does not yet record" in wage["reason"]
    for row in wage["missing_inputs"]:
        assert row["label"] and row["label"] != row["key"]

    missing = {m["key"] for m in out["missing"]}
    assert {"soc_code", "soc_title"} <= missing
    fields = _filled_fields(client.get(out["download_url"]).content)
    for name in ("2  Suggested SOC ONETOES code",
                 "2a Suggested SOC ONETOES occupation title",
                 "undefined_18", "undefined_19"):
        assert fields[name].get("/V") in (None, ""), name


def test_the_2021_fill_is_watermarked_as_a_preparation_copy(no_oflc):
    out = h1b_forms.fill_form(FORM_KEY, {"job_title": "Software Engineer"})
    text = PdfReader(BytesIO(out["pdf"])).pages[0].extract_text()
    assert "PREPARATION COPY" in text
    assert "flag.dol.gov" in text


# ------------------------------------------------------- the edition selector

def test_the_newer_edition_is_the_default_and_the_older_stays_reachable():
    assert flag_forms.DEFAULT_EDITION == "2021"
    assert flag_forms.FORM_KEY == FORM_KEY
    assert flag_forms.form_key_for_edition() == FORM_KEY
    assert flag_forms.form_key_for_edition("2021") == FORM_KEY
    assert flag_forms.form_key_for_edition("2019") == OLD_FORM_KEY
    assert flag_forms.LEGACY_FORM_KEY == OLD_FORM_KEY
    assert flag_forms.edition_of(FORM_KEY) == "2021"
    assert flag_forms.edition_of(OLD_FORM_KEY) == "2019"
    assert set(h1b_forms.PWD_FORM_KEYS) == {FORM_KEY, OLD_FORM_KEY}
    for key in h1b_forms.PWD_FORM_KEYS:
        assert key in h1b_forms.FORM_KEYS
        assert h1b_forms.STEP_BY_FORM[key] == "lca"
        assert h1b_forms.PREPARATION_WATERMARKS[key] == h1b_forms.PWD_WATERMARK


def test_an_unknown_edition_raises_instead_of_substituting_a_blank():
    for bad in ("2018", "", None, "latest"):
        with pytest.raises(ValueError):
            flag_forms.form_key_for_edition(bad)
    with pytest.raises(ValueError):
        flag_forms.edition_of("i-129")


def test_the_edition_block_reports_the_blank_without_claiming_it_is_current():
    info = flag_forms.edition_info()
    assert info["edition"] == "2021" and info["is_default"] is True
    assert info["form_key"] == FORM_KEY
    assert info["blank_expiration"] == "04/30/2021"
    assert info["omb_control_number"] == "1205-0508"
    assert sorted(info["available_editions"]) == ["2019", "2021"]
    assert "dol.gov" in info["source"]
    # Honest degradation about the form itself: a printed expiry is not a
    # claim that this is DOL's current edition.
    assert "not a promise that it is" in info["currency_notice"]
    assert "does NOT claim" in info["currency_note"]

    # The default carries no "you picked the old one" warning...
    assert info["older_edition_notice"] == ""
    older = flag_forms.edition_info("2019")
    assert older["blank_expiration"] == "05/31/2019"
    assert older["is_default"] is False
    # ...and the older edition never describes itself as the newer blank.
    assert "OLDER of the two" in older["older_edition_notice"]
    assert "newer" not in older["currency_notice"]
    assert older["currency_notice"] == info["currency_notice"]

    # Both blanks say about themselves exactly what the map records.
    for edition, spec in flag_forms.EDITIONS.items():
        reader = PdfReader(str(consular_forms._template_path(spec["form_key"])))
        assert len(reader.get_fields() or {}) == spec["field_count"], edition
        assert (f"Expiration Date: {spec['blank_expiration']}"
                in reader.pages[0].extract_text()), edition


def test_both_editions_fill_the_same_answers_identically(no_oflc):
    """Choosing an edition changes the paper, never the answers."""
    answers = {**FULL_ANSWERS, "h1b_second_degree_required": "yes",
               "h1b_second_degree_detail": "M.S., Computer Science"}
    old = h1b_forms.build_fill(OLD_FORM_KEY, answers)
    new = h1b_forms.build_fill(FORM_KEY, answers)
    assert new["text_values"] == old["text_values"]
    assert new["tick_values"] == old["tick_values"]
    assert new["filled_keys"] == old["filled_keys"]
    assert new["total_mapped"] == old["total_mapped"]
    assert [m["key"] for m in new["missing"]] == [m["key"]
                                                  for m in old["missing"]]
    # The never-write lists are what differ, and only that.
    assert old["human_only"] == []
    assert len(new["human_only"]) == 51

    for form_key in (OLD_FORM_KEY, FORM_KEY):
        fields = _filled_fields(h1b_forms.fill_form(form_key, answers)["pdf"])
        assert fields["1  Job Title"]["/V"] == "Software Engineer"
        assert fields["undefined_13"]["/V"] == "/0"     # second degree: YES
        assert fields["Bachelors"]["/V"] == "/On"


def test_both_entry_points_fill_the_2021_blank_identically(client, db, oflc):
    """The generic prepare endpoint and prepare_pwd_request run the same
    derivations on the newer key too - one case can never hold two
    differently-filled copies of one perjury form."""
    case_id = _prepared_case(client, db)
    r = client.post(f"/h1b/cases/{case_id}/forms/{FORM_KEY}/prepare",
                    headers=PETITIONER_AUTH)
    assert r.status_code == 200, r.text
    endpoint = _filled_fields(client.get(r.json()["download_url"]).content)

    out = flag_forms.prepare_pwd_request(db, _parent(db, case_id))
    module = _filled_fields(client.get(out["download_url"]).content)

    classification = ("1  Indicate the type of visa classification supported "
                      "by this application Write classification symbol")
    for name in (classification, "2a Suggested SOC ONETOES occupation title",
                 "1  Job Title", "1 Worksite address 1"):
        assert endpoint[name].get("/V") == module[name].get("/V"), name
    assert endpoint[classification]["/V"] == "H-1B"          # derived
    assert endpoint["2a Suggested SOC ONETOES occupation title"]["/V"] \
        == "Software Developers"
    assert r.json()["human_only"] == out["human_only"]


def test_the_2021_request_is_petitioner_scoped_and_carries_no_beneficiary_facts(
        client, db, oflc):
    case_id = _prepared_case(client, db)
    # The beneficiary's own account cannot prepare the petitioner's request.
    r = client.post(f"/h1b/cases/{case_id}/forms/{FORM_KEY}/prepare",
                    headers=AUTH)
    assert r.status_code == 403, r.text

    out = flag_forms.prepare_pwd_request(db, _parent(db, case_id))
    text = "\n".join((page.extract_text() or "") for page in
                     PdfReader(BytesIO(
                         client.get(out["download_url"]).content)).pages)
    for secret in ("ZHANG", "WEI", "EJ1234567", "1993"):
        assert secret not in text, secret


def test_the_new_notices_are_localized_like_their_siblings():
    for key in ("flag.edition_currency", "flag.older_edition",
                "flag.government_fields_left_empty"):
        en = flag_forms.tr(key, "en")
        assert en and en != key
        for locale in ("zh-CN", "zh-Hant"):
            assert flag_forms.tr(key, locale) != en
        # An unknown locale falls back to English, never to a raw key.
        assert flag_forms.tr(key, "xx-YY") == en

    for locale in ("en", "zh-CN", "zh-Hant"):
        info = flag_forms.edition_info(locale=locale)
        assert info["currency_notice"] == flag_forms.tr("flag.edition_currency",
                                                        locale)
        block = flag_forms.government_block(FORM_KEY, locale)
        assert block["notice"] == flag_forms.tr(
            "flag.government_fields_left_empty", locale)


def test_choice_vocabulary_is_the_same_on_both_editions():
    assert (flag_forms.choice_vocabulary(FORM_KEY)
            == flag_forms.choice_vocabulary(OLD_FORM_KEY))
    vocab = flag_forms.choice_vocabulary()
    assert vocab["h1b_min_education"] == ["associate", "bachelor", "doctorate",
                                          "high_school", "master", "none",
                                          "other"]
    assert vocab["h1b_experience_required"] == ["no", "yes"]
