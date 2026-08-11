"""OFLC wage-level computation: pinned to real DOL numbers, honest when the
government data is missing or un-annualisable.

The wage level is a legal representation Ellis SUGGESTS and a human confirms.
These tests hold the line on the honesty invariants: exact level boundaries,
below-Level-I refusal, the High/Annual label that voids the 2080 conversion,
the OEWS geographic-fallback caveats, unit conversions, and an empty cache dir
returning an honest 'unavailable' instead of a fabricated level.

Fixtures under tests/fixtures/oflc/ carry real DOL Abilene TX (area 10180) rows
so the arithmetic is pinned to authoritative numbers:
  SOC 11-1011  49.19 / 74.04 / 98.88 / 123.73  (hourly L1..L4)
  SOC 11-1021  23.02 / 38.75 / 54.47 /  70.20
"""
from pathlib import Path

import pytest

from app.h1b import wage_data

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "oflc"

# Real DOL hourly levels for the pinned Abilene TX rows.
_H_11_1011 = (49.19, 74.04, 98.88, 123.73)
_H_11_1021 = (23.02, 38.75, 54.47, 70.20)


@pytest.fixture(autouse=True)
def _point_at_fixture(monkeypatch):
    """Every test reads the committed fixture dir — never the live download."""
    monkeypatch.setenv("ELLIS_OFLC_WAGE_DIR", str(FIXTURE_DIR))
    wage_data._CACHE.clear()
    yield
    wage_data._CACHE.clear()


def _annual(hourly: float) -> int:
    return round(hourly * wage_data.HOURS_PER_YEAR)


# --- availability -----------------------------------------------------------
def test_is_available_true_with_fixture():
    assert wage_data.is_available() is True


def test_is_available_false_with_empty_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ELLIS_OFLC_WAGE_DIR", str(tmp_path))
    wage_data._CACHE.clear()
    assert wage_data.is_available() is False


def test_ensure_wage_data_reports_missing_honestly(tmp_path, monkeypatch):
    monkeypatch.setenv("ELLIS_OFLC_WAGE_DIR", str(tmp_path))
    wage_data._CACHE.clear()
    status = wage_data.ensure_wage_data()
    assert status["available"] is False
    assert wage_data._ALC_FILE in status["missing"]
    assert status["source"] == wage_data.SOURCE
    assert status["fetch"]["url"] == wage_data.WAGE_DATA_URL


def test_compute_unavailable_never_fabricates_a_level(tmp_path, monkeypatch):
    monkeypatch.setenv("ELLIS_OFLC_WAGE_DIR", str(tmp_path))
    wage_data._CACHE.clear()
    res = wage_data.compute_wage_level(
        area_code="10180", soc_code="11-1011", offered_wage=200000, wage_unit="year")
    assert res["available"] is False
    assert res["level"] is None
    assert res["level_wages"] is None
    assert res["invalid_2080_conversion"] is False
    assert res["source"] == wage_data.SOURCE
    assert res["as_of"] == wage_data.WAGE_DATA_AS_OF
    assert res["status"]


# --- pinned real DOL arithmetic --------------------------------------------
def test_level_wages_match_real_dol_numbers():
    res = wage_data.compute_wage_level(
        area_code="10180", soc_code="11-1011", offered_wage=None, wage_unit="year")
    assert res["level_wages"] == {
        1: _annual(49.19), 2: _annual(74.04), 3: _annual(98.88), 4: _annual(123.73)}
    # spot the exact expected integers so a refactor of the rounding is caught.
    assert res["level_wages"] == {1: 102315, 2: 154003, 3: 205670, 4: 257358}

    res2 = wage_data.compute_wage_level(
        area_code="10180", soc_code="11-1021", offered_wage=None, wage_unit="year")
    assert res2["level_wages"] == {1: 47882, 2: 80600, 3: 113298, 4: 146016}


def test_levels_are_evenly_spaced_in_the_source_hourly():
    res = wage_data.compute_wage_level(
        area_code="10180", soc_code="11-1011", offered_wage=None, wage_unit="year")
    raw = res["raw_levels"]
    step = (raw[4] - raw[1]) / 3
    assert raw[2] == pytest.approx(raw[1] + step, abs=0.01)
    assert raw[3] == pytest.approx(raw[1] + 2 * step, abs=0.01)


# --- exact level boundaries -------------------------------------------------
def test_offer_equal_to_level_2_is_level_2():
    res = wage_data.compute_wage_level(
        area_code="10180", soc_code="11-1011", offered_wage=74.04, wage_unit="hour")
    assert res["offered_annual"] == _annual(74.04)
    assert res["level"] == 2
    assert res["meets_prevailing"] is True


def test_offer_just_below_level_2_is_level_1():
    res = wage_data.compute_wage_level(
        area_code="10180", soc_code="11-1011", offered_wage=74.03, wage_unit="hour")
    assert res["offered_annual"] < res["level_wages"][2]
    assert res["offered_annual"] >= res["level_wages"][1]
    assert res["level"] == 1


def test_offer_equal_to_level_1_is_level_1():
    res = wage_data.compute_wage_level(
        area_code="10180", soc_code="11-1011", offered_wage=49.19, wage_unit="hour")
    assert res["level"] == 1
    assert res["meets_prevailing"] is True


def test_offer_at_or_above_level_4_is_level_4():
    at_l4 = wage_data.compute_wage_level(
        area_code="10180", soc_code="11-1011", offered_wage=123.73, wage_unit="hour")
    assert at_l4["level"] == 4
    above = wage_data.compute_wage_level(
        area_code="10180", soc_code="11-1011", offered_wage=300.00, wage_unit="hour")
    assert above["level"] == 4
    assert above["meets_prevailing"] is True


def test_offer_below_level_1_has_no_level_and_fails_prevailing():
    res = wage_data.compute_wage_level(
        area_code="10180", soc_code="11-1011", offered_wage=20.00, wage_unit="hour")
    assert res["level"] is None
    assert res["meets_prevailing"] is False
    assert any("below level i" in c.lower() for c in res["caveats"])


# --- unit conversions -------------------------------------------------------
def test_unit_conversions_agree_for_equivalent_offers():
    hourly = wage_data.compute_wage_level(
        area_code="10180", soc_code="11-1011", offered_wage=74.04, wage_unit="hour")
    weekly = wage_data.compute_wage_level(
        area_code="10180", soc_code="11-1011", offered_wage=74.04 * 40, wage_unit="week")
    yearly = wage_data.compute_wage_level(
        area_code="10180", soc_code="11-1011", offered_wage=_annual(74.04), wage_unit="year")
    assert hourly["offered_annual"] == weekly["offered_annual"] == yearly["offered_annual"]
    assert hourly["level"] == weekly["level"] == yearly["level"] == 2


def test_monthly_unit_annualises_by_twelve():
    res = wage_data.compute_wage_level(
        area_code="10180", soc_code="11-1021", offered_wage=10000, wage_unit="month")
    assert res["offered_annual"] == 120000


def test_unknown_wage_unit_raises():
    with pytest.raises(ValueError):
        wage_data.compute_wage_level(
            area_code="10180", soc_code="11-1011", offered_wage=100, wage_unit="fortnight")


# --- High / Annual label voids the 2080 conversion --------------------------
def test_high_wage_label_sets_invalid_and_refuses_a_level():
    res = wage_data.compute_wage_level(
        area_code="10180", soc_code="29-1221", offered_wage=500000, wage_unit="year")
    assert res["invalid_2080_conversion"] is True
    assert res["level"] is None
    assert res["level_wages"] is None      # no fabricated annual basis
    assert res["meets_prevailing"] is None
    assert res["label"] == "High Wage"
    assert res["status"]


def test_annual_wage_label_sets_invalid_and_refuses_a_level():
    res = wage_data.compute_wage_level(
        area_code="10180", soc_code="25-2021", offered_wage=60000, wage_unit="year")
    assert res["invalid_2080_conversion"] is True
    assert res["level"] is None
    assert res["level_wages"] is None
    assert res["raw_levels_period"] == "year"
    assert res["label"] == "Annual Wage"


# --- OEWS geographic fallback caveats --------------------------------------
def test_geo_level_1_has_no_fallback_caveat():
    res = wage_data.compute_wage_level(
        area_code="10180", soc_code="11-1011", offered_wage=100000, wage_unit="year")
    assert res["geo_level"] == 1
    assert res["geo_caveat"] is None


def test_geo_level_2_flags_contiguous_area_fallback():
    res = wage_data.compute_wage_level(
        area_code="10180", soc_code="13-2011", offered_wage=80000, wage_unit="year")
    assert res["geo_level"] == 2
    assert res["geo_caveat"] and "contiguous" in res["geo_caveat"].lower()
    assert res["geo_caveat"] in res["caveats"]


def test_geo_level_3_flags_statewide_fallback():
    res = wage_data.compute_wage_level(
        area_code="10180", soc_code="15-1252", offered_wage=100000, wage_unit="year")
    assert res["geo_level"] == 3
    assert res["geo_caveat"] and "statewide" in res["geo_caveat"].lower()
    # A statewide fallback still computes a level; it is the AREA that is caveated.
    assert res["level"] is not None


# --- not-found is honest, not fabricated -----------------------------------
def test_unknown_area_soc_returns_no_level_with_a_note():
    res = wage_data.compute_wage_level(
        area_code="99999", soc_code="11-1011", offered_wage=100000, wage_unit="year")
    assert res["available"] is True
    assert res["level"] is None
    assert res["level_wages"] is None
    assert res["status"]


def test_every_result_carries_source_and_as_of():
    res = wage_data.compute_wage_level(
        area_code="10180", soc_code="11-1011", offered_wage=160000, wage_unit="year")
    assert res["source"] == wage_data.SOURCE
    assert res["as_of"] == wage_data.WAGE_DATA_AS_OF


def test_soc_code_accepts_unhyphenated_and_dotted_forms():
    a = wage_data.compute_wage_level(
        area_code="10180", soc_code="111011", offered_wage=160000, wage_unit="year")
    b = wage_data.compute_wage_level(
        area_code="10180", soc_code="11-1011.00", offered_wage=160000, wage_unit="year")
    assert a["level"] == b["level"] == 2
    assert a["soc_code"] == b["soc_code"] == "11-1011"


# --- area + occupation lookups ---------------------------------------------
def test_lookup_area_by_unambiguous_county():
    res = wage_data.lookup_area("Callahan County")
    assert res["available"] is True
    assert res["area_code"] == "10180"
    assert res["ambiguous"] is False


def test_lookup_area_by_code():
    res = wage_data.lookup_area("10180")
    assert res["area_code"] == "10180"


def test_lookup_area_ambiguous_county_lists_candidates():
    res = wage_data.lookup_area("Jones County")
    assert res["area_code"] is None
    assert res["ambiguous"] is True
    codes = {c["area_code"] for c in res["candidates"]}
    assert {"10180", "28860"} <= codes
    assert res["note"]


def test_lookup_area_state_hint_disambiguates():
    res = wage_data.lookup_area("Jones County, TX")
    assert res["area_code"] == "10180"
    assert res["ambiguous"] is False


def test_lookup_area_zip_degrades_honestly():
    res = wage_data.lookup_area("73301")   # a TX ZIP, not an OFLC area code
    assert res["area_code"] is None
    assert res["note"] and "zip" in res["note"].lower()


def test_lookup_area_unavailable_when_no_geography(tmp_path, monkeypatch):
    monkeypatch.setenv("ELLIS_OFLC_WAGE_DIR", str(tmp_path))
    wage_data._CACHE.clear()
    res = wage_data.lookup_area("Callahan County")
    assert res["available"] is False
    assert res["area_code"] is None
    assert res["note"]


def test_soc_title_lookup():
    assert wage_data.soc_title("11-1011") == "Chief Executives"
    assert wage_data.soc_title("111021") == "General and Operations Managers"


def test_soc_title_unknown_returns_none():
    assert wage_data.soc_title("99-9999") is None


def test_compute_attaches_soc_title():
    res = wage_data.compute_wage_level(
        area_code="10180", soc_code="15-1252", offered_wage=100000, wage_unit="year")
    assert res["soc_title"] == "Software Developers"


# --- fetch helper never downloads in a test --------------------------------
def test_download_helper_returns_a_plan_and_does_not_execute():
    plan = wage_data.download_wage_data()   # execute defaults to False
    assert plan["url"] == wage_data.WAGE_DATA_URL
    assert any("curl" in c for c in plan["commands"])
    assert "--continue-at" in " ".join(plan["commands"])   # resumable Range pull
    assert "ok" not in plan                                # nothing was run
