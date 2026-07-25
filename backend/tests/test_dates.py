"""Canonical date pipeline: contextual MRZ century resolution, deterministic
printed-date parsing (English month names, Chinese passport layouts), U.S.
display formatting, portal-specific formats, timezone-shift immunity, age
arithmetic, and end-to-end normalization through extraction, profile,
approval-merge, and validity.

All fixture dates here are synthetic — no real passport values appear."""
import ast
import inspect
from datetime import date

import pytest

from app import dates
from app.providers import ocr
from app.visa_snapshot import intake_flow

TODAY = date(2026, 7, 24)


# ---- MRZ YYMMDD century resolution (contextual — never a fixed pivot) --------
def test_birth_century_resolves_to_a_plausible_past_date():
    assert dates.parse_mrz_date("880613", kind="birth", today=TODAY) == date(1988, 6, 13)
    assert dates.parse_mrz_date("940812", kind="birth", today=TODAY) == date(1994, 8, 12)
    # A child born in the 2000s stays in the 2000s.
    assert dates.parse_mrz_date("190301", kind="birth", today=TODAY) == date(2019, 3, 1)
    # A birth date that would land in the FUTURE this century falls back a
    # century (the old fixed YY pivot mapped this to an impossible 2026-12-25).
    assert dates.parse_mrz_date("261225", kind="birth", today=TODAY) == date(1926, 12, 25)
    # Both candidates in the future -> impossible, rejected.
    assert dates.parse_mrz_date("270101", kind="birth", today=date(1926, 1, 1)) is None


def test_expiry_century_resolves_near_the_present():
    assert dates.parse_mrz_date("270517", kind="expiry", today=TODAY) == date(2027, 5, 17)
    assert dates.parse_mrz_date("310504", kind="expiry", today=TODAY) == date(2031, 5, 4)
    # A long-expired 1999 document is 1999, not 2099.
    assert dates.parse_mrz_date("990101", kind="expiry", today=TODAY) == date(1999, 1, 1)
    assert dates.parse_mrz_date("290423", kind="expiry", today=TODAY) == date(2029, 4, 23)


def test_issue_century_never_lands_in_the_future():
    assert dates.parse_mrz_date("040518", kind="issue", today=TODAY) == date(2004, 5, 18)
    assert dates.parse_mrz_date("170518", kind="issue", today=TODAY) == date(2017, 5, 18)
    # yy=30 as an issue year cannot be 2030 (future) — it is 1930.
    assert dates.parse_mrz_date("300101", kind="issue", today=TODAY) == date(1930, 1, 1)


def test_malformed_or_impossible_mrz_dates_rejected():
    for bad in ("", "abc123", "991350", "990230", "12345", "1234567"):
        assert dates.parse_mrz_date(bad, kind="birth", today=TODAY) is None


# ---- printed-zone parsing ----------------------------------------------------
def test_printed_dd_mon_yyyy_forms_normalize():
    assert dates.parse_printed_date("13 JUN 1988") == date(1988, 6, 13)
    assert dates.parse_printed_date("12 Aug 1994") == date(1994, 8, 12)
    assert dates.parse_printed_date("01 OCT 1988") == date(1988, 10, 1)
    assert dates.parse_printed_date("5 May 2021") == date(2021, 5, 5)
    assert dates.parse_printed_date("May 5, 2021") == date(2021, 5, 5)
    assert dates.parse_printed_date("13 June 1988") == date(1988, 6, 13)


def test_chinese_passport_date_layouts_normalize():
    # PRC passports print "18 5月/MAY 2017" (day, numeric month + 月, month name).
    assert dates.parse_printed_date("18 5月/MAY 2017") == date(2017, 5, 18)
    assert dates.parse_printed_date("24 4月/APR 2019") == date(2019, 4, 24)
    assert dates.parse_printed_date("2017年5月18日") == date(2017, 5, 18)
    # Numeric month and month name disagreeing is rejected, never guessed.
    assert dates.parse_printed_date("18 5月/JUN 2017") is None


def test_ambiguous_numeric_dates_are_rejected_never_guessed():
    assert dates.parse_printed_date("05/04/2031") is None       # DD/MM or MM/DD?
    assert dates.parse_printed_date("18/05/2017") == date(2017, 5, 18)   # day > 12
    assert dates.parse_printed_date("05/18/2017") == date(2017, 5, 18)   # month pos 1
    assert dates.parse_printed_date("2017-05-18") == date(2017, 5, 18)   # ISO
    assert dates.parse_printed_date("31 FEB 2020") is None       # impossible
    assert dates.parse_printed_date("garbage") is None


def test_leap_day_parses_and_non_leap_rejected():
    assert dates.parse_printed_date("29 FEB 2000") == date(2000, 2, 29)
    assert dates.parse_printed_date("29 FEB 1900") is None       # 1900 not a leap year
    assert dates.parse_mrz_date("000229", kind="birth", today=TODAY) == date(2000, 2, 29)


# ---- normalize_any / display / portal ---------------------------------------
def test_normalize_any_accepts_every_canonical_input():
    assert dates.normalize_any("1988-06-13", kind="birth", today=TODAY) == "1988-06-13"
    assert dates.normalize_any("880613", kind="birth", today=TODAY) == "1988-06-13"
    assert dates.normalize_any("13 JUN 1988", kind="birth", today=TODAY) == "1988-06-13"
    assert dates.normalize_any("garbage", kind="birth", today=TODAY) == ""
    # MM/DD/YYYY only in the explicit U.S.-entry context.
    assert dates.normalize_any("06/13/1988", kind="birth", us_numeric=True) == "1988-06-13"
    assert dates.normalize_any("05/04/2031", kind="expiry") == ""            # ambiguous
    assert dates.normalize_any("05/04/2031", kind="expiry", us_numeric=True) == "2031-05-04"


def test_display_is_us_format_and_a_pure_string_transform():
    assert dates.to_display("1988-06-13") == "06/13/1988"
    assert dates.to_display("2031-05-04") == "05/04/2031"
    assert dates.to_display("") == ""
    assert dates.to_display("not a date") == "not a date"   # unchanged, never guessed


def test_portal_formats_are_route_specific_and_fail_closed():
    assert dates.to_portal("1988-06-13", "DD/MM/YYYY") == "13/06/1988"
    assert dates.to_portal("1988-06-13", "MM/DD/YYYY") == "06/13/1988"
    assert dates.to_portal("1988-06-13", "YYYYMMDD") == "19880613"
    assert dates.to_portal("1988-06-13", "DD-MON-YYYY") == "13-JUN-1988"
    assert dates.to_portal("1988-06-13", "DD MONTH YYYY") == "13 June 1988"
    assert dates.to_portal("880613", "DD/MM/YYYY") == ""     # non-canonical: refused
    assert dates.to_portal("1988-06-13", "") == ""


def test_no_timezone_machinery_exists_in_the_date_module():
    """Passport dates are calendar dates: the module must never import
    tz-aware datetime machinery, so a date cannot shift by a day."""
    src = inspect.getsource(dates)
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
    # Only datetime.date is imported — never datetime/timezone/tzinfo, so no
    # tz-aware conversion can exist in this module.
    assert imported == {"annotations", "re", "date"}
    assert "timezone" not in imported and "tzinfo" not in src


# ---- age ---------------------------------------------------------------------
def test_age_is_calculated_before_and_after_the_birthday():
    assert dates.derive_age("1988-06-13", today=date(2026, 6, 12)) == 37
    assert dates.derive_age("1988-06-13", today=date(2026, 6, 13)) == 38
    assert dates.derive_age("1988-06-13", today=date(2026, 6, 14)) == 38
    # Leap-day birthday: age ticks on Mar 1 in non-leap years.
    assert dates.derive_age("2000-02-29", today=date(2026, 2, 28)) == 25
    assert dates.derive_age("2000-02-29", today=date(2026, 3, 1)) == 26
    assert dates.derive_age("not-a-date") is None
    assert dates.derive_age("2050-01-01", today=TODAY) is None   # future -> invalid


# ---- extraction produces canonical ISO --------------------------------------
def _mrz_text(birth="880613", expiry="270517"):
    from app.providers.ocr import mrz_check_digit
    pn = "X1234567<"
    l1 = "P<CHNCAO<<XIANGWEI".ljust(44, "<")[:44]
    l2 = (pn + mrz_check_digit(pn) + "CHN" + birth + mrz_check_digit(birth)
          + "F" + expiry + mrz_check_digit(expiry) + "<" * 14
          + mrz_check_digit("<" * 14) + "0")
    return f"PASSPORT\n{l1}\n{l2}\n"


def test_extracted_fields_carry_iso_dates_not_raw_mrz():
    res = ocr.LocalOcrProvider().process(text=_mrz_text())
    fields = {f.key: f.value for f in res.fields}
    assert fields["birth_date"] == "1988-06-13"
    assert fields["expiry_date"] == "2027-05-17"
    assert res.mrz_valid is True


def test_profile_dates_are_iso_and_age_derived():
    mrz = ocr.parse_mrz(_mrz_text())
    profile = intake_flow.build_passport_profile(
        ocr_fields={}, mrz=mrz, recognized_text=_mrz_text(),
        mrz_valid=True, today=TODAY)
    f = profile["fields"]
    assert f["birth_date"]["value"] == "1988-06-13"
    assert f["expiry_date"]["value"] == "2027-05-17"
    assert profile["prefill"]["birth_date"] == "1988-06-13"
    assert profile["prefill"]["passport_expiry_date"] == "2027-05-17"
    assert profile["prefill"]["age"] == 38
    assert f["document_type"]["value"] == "P"


# ---- printed-zone vs MRZ cross-check ----------------------------------------
def _cn_style_text(printed_birth="13 JUN 1988", printed_issue="18 5月/MAY 2017"):
    return (
        "PEOPLE'S REPUBLIC OF CHINA\n护照\nPASSPORT\n"
        "姓名/Name\nCAO, XIANGWEI\n"
        f"出生日期/Date of birth\n{printed_birth}\n"
        f"签发日期/Date of issue\n{printed_issue}\n"
        "出生地点/Place of birth\n河北/HEBEI\n"
        "签发地点/Place of issue\n香港/HONG KONG\n"
        + _mrz_text())


def test_printed_zone_agreement_passes_without_conflicts():
    mrz = ocr.parse_mrz(_mrz_text())
    profile = intake_flow.build_passport_profile(
        ocr_fields={}, mrz=mrz, recognized_text=_cn_style_text(),
        mrz_valid=True, today=TODAY)
    assert profile["fields"]["birth_date"]["value"] == "1988-06-13"
    assert not any(c["field"] == "birth_date" for c in profile["conflicts"])
    # The printed issue date (which the MRZ cannot supply) is normalized to ISO.
    assert profile["fields"]["issue_date"]["value"] == "2017-05-18"
    assert profile["prefill"]["passport_issue_date"] == "2017-05-18"
    # Chinese-layout places extract from the Latin line.
    assert profile["fields"]["place_of_birth"]["value"].startswith("HEBEI")
    assert profile["fields"]["place_of_issue"]["value"].startswith("HONG KONG")


def test_printed_zone_disagreement_requires_applicant_confirmation():
    mrz = ocr.parse_mrz(_mrz_text())          # MRZ birth 1988-06-13
    profile = intake_flow.build_passport_profile(
        ocr_fields={}, mrz=mrz,
        recognized_text=_cn_style_text(printed_birth="14 JUN 1988"),
        mrz_valid=True, today=TODAY)
    bd = profile["fields"]["birth_date"]
    assert bd["value"] == "1988-06-13"        # MRZ never silently overwritten
    assert bd["needs_confirmation"] is True
    assert any(c["field"] == "birth_date" and c["visual"] == "1988-06-14"
               for c in profile["conflicts"])


def test_multilingual_label_runs_never_fake_a_name_conflict():
    """'Surname/Nom/Apellidos' must never capture 'Nom' as the printed surname
    (the source of a false MRZ-vs-printed conflict on U.S. passports)."""
    text = ("PASSPORT\nSurname/Nom/Apellidos\nCAO\n"
            "Given Names/Prénoms/Nombres\nXIANGWEI\n" + _mrz_text())
    mrz = ocr.parse_mrz(_mrz_text())
    profile = intake_flow.build_passport_profile(
        ocr_fields={}, mrz=mrz, recognized_text=text, mrz_valid=True, today=TODAY)
    assert profile["conflicts"] == []
    assert profile["fields"]["surname"]["needs_confirmation"] is False


# ---- unparseable printed dates never prefill ---------------------------------
def test_unparseable_issue_date_is_flagged_and_never_prefills():
    text = "PASSPORT\nDate of issue\n05/04/2021\n" + _mrz_text()   # ambiguous
    mrz = ocr.parse_mrz(_mrz_text())
    profile = intake_flow.build_passport_profile(
        ocr_fields={}, mrz=mrz, recognized_text=text, mrz_valid=True, today=TODAY)
    issue = profile["fields"].get("issue_date")
    assert issue is not None and issue["needs_confirmation"] is True
    assert "passport_issue_date" not in profile["prefill"]


# ---- portal adapter formatting ----------------------------------------------
def test_vietnam_adapter_declares_portal_date_formats():
    from app.portal.adapters.vietnam_evisa import build_vietnam_evisa_adapter
    adapter = build_vietnam_evisa_adapter(object())
    fmt_by_field = {m["field"]: m.get("format") for m in adapter.application_mappings}
    assert fmt_by_field["birth_date"] == "DD/MM/YYYY"
    assert fmt_by_field["intended_arrival"] == "DD/MM/YYYY"
    assert fmt_by_field.get("full_name") is None      # only dates carry formats


def test_generated_flow_fill_applies_declared_portal_format():
    from app.adapter_factory.runtime import FlowRunner

    class Driver:
        def __init__(self):
            self.filled = {}

        def fill(self, selector, value):
            self.filled[selector] = value
            return {"ok": True}

    node = {"node_id": "fill_dob", "action": "FILL_NON_SENSITIVE",
            "selector": "#dob", "input_source": "birth_date",
            "format": "DD/MM/YYYY"}
    runner = FlowRunner.__new__(FlowRunner)
    runner.answers = {"birth_date": "1988-06-13"}
    runner.driver = Driver()
    runner._from_driver = lambda n, r: {"status": "ok"}
    out = FlowRunner._step(runner, node)
    assert out["status"] == "ok"
    assert runner.driver.filled == {"#dob": "13/06/1988"}


def test_generated_flow_fill_refuses_non_canonical_date_for_formatted_field():
    from app.adapter_factory.runtime import FlowRunner

    class Driver:
        def __init__(self):
            self.filled = {}

        def fill(self, selector, value):
            self.filled[selector] = value
            return {"ok": True}

    node = {"node_id": "fill_dob", "action": "FILL_NON_SENSITIVE",
            "selector": "#dob", "input_source": "birth_date",
            "format": "DD/MM/YYYY"}
    runner = FlowRunner.__new__(FlowRunner)
    runner.answers = {"birth_date": "06/13/1988"}    # not canonical ISO
    runner.driver = Driver()
    runner._from_driver = lambda n, r: {"status": "ok"}
    out = FlowRunner._step(runner, node)
    assert runner.driver.filled == {}                 # nothing written
    assert out["status"] == "failed"


# ---- approval merge normalizes every date-shaped answer ----------------------
def test_approve_merge_normalizes_us_and_mrz_dates(client=None):
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    from app.db import create_all
    create_all()
    c = TestClient(fastapi_app)
    H = {"Authorization": "Bearer dev-token", "X-Org-Id": "org-dates", "X-User-Id": "ud"}
    case = c.post("/cases", headers=H, json={
        "full_name": "Date Test", "email": "d@example.com",
        "destination_country": "Vietnam", "visa_type": "tourist",
        "answers": {}}).json()
    up = c.post(f"/cases/{case['id']}/documents", headers=H, json={
        "name": "passport.pdf", "mime": "application/pdf", "size_bytes": 1024,
        "text": _mrz_text()})
    doc_id = up.json()["id"]
    # The applicant edits the expiry in U.S. display format.
    ap = c.post(f"/cases/{case['id']}/documents/{doc_id}/approve", headers=H,
                json=[{"key": "expiry_date", "value": "05/17/2027"},
                      {"key": "issue_date", "value": "18 5月/MAY 2017"}])
    ans = ap.json()["answers"]
    assert ans["expiry_date"] == "2027-05-17"        # canonical ISO in answers
    assert ans["issue_date"] == "2017-05-18"
    assert ans["birth_date"] == "1988-06-13"          # from extraction, ISO


# ---- validity uses display format in prose, ISO in structure ----------------
def test_validity_explanations_use_us_display_and_structured_iso():
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    from app.db import create_all
    create_all()
    c = TestClient(fastapi_app)
    H = {"Authorization": "Bearer dev-token", "X-Org-Id": "org-dates2", "X-User-Id": "ud2"}
    case = c.post("/cases", headers=H, json={
        "full_name": "Exp Test", "email": "e@example.com",
        "destination_country": "Vietnam", "visa_type": "tourist",
        "answers": {"expiry_date": "2020-01-02"}}).json()
    v = c.get(f"/cases/{case['id']}/passport-validity", headers=H).json()
    assert v["status"] == "expired"
    assert v["expiry_date"] == "2020-01-02"           # structured: canonical ISO
    assert "01/02/2020" in v["explanation"]           # prose: U.S. display format
    assert "2020-01-02" not in v["explanation"]


def test_corrupted_mrz_name_is_flagged_by_the_printed_comma_name_line():
    """MRZ name characters carry NO check digit — a noisy scan can corrupt
    them while every checksum still validates. The printed 'SURNAME, GIVEN'
    line must flag the disagreement for applicant confirmation."""
    corrupted = _mrz_text().replace("CAO<<XIANGWEI", "CAO<<XIANGWEIKQZV")
    text = "PASSPORT\n姓名/Name\nCAO, XIANGWEI\n" + corrupted
    mrz = ocr.parse_mrz(corrupted)
    assert mrz["mrz_valid"] is True                    # checksums still pass
    profile = intake_flow.build_passport_profile(
        ocr_fields={}, mrz=mrz, recognized_text=text, mrz_valid=True, today=TODAY)
    assert profile["fields"]["given_names"]["needs_confirmation"] is True
    assert any(c["field"] == "full_name" for c in profile["conflicts"])


def test_agreeing_printed_comma_name_line_creates_no_conflict():
    text = "PASSPORT\n姓名/Name\nCAO, XIANGWEI\n" + _mrz_text()
    mrz = ocr.parse_mrz(_mrz_text())
    profile = intake_flow.build_passport_profile(
        ocr_fields={}, mrz=mrz, recognized_text=text, mrz_valid=True, today=TODAY)
    assert profile["conflicts"] == []
    assert profile["fields"]["given_names"]["needs_confirmation"] is False
