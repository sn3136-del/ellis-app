"""Agent 3: the official DOL/CDC/O*NET data wired into the RFE engine and two
wage/occupation endpoints.

Two disciplines under test:
 - counsel seam: the wage-level and below-prevailing signals prefer a COMPUTED
   DOL determination (Agent 1 wage_data, over the committed OFLC fixture) to the
   petitioner's self-report, degrade to self-report when the data is
   unavailable, carry the source + caveats, and keep the party wall (a
   beneficiary caller never sees the computed dollar figures). A new advisory
   SOC-mismatch signal is grounded ONLY on a real NIOCCS disagreement.
 - endpoints: POST /h1b/cases/{id}/wage-analysis and .../classify-occupation are
   petitioner-or-admin SUGGESTION surfaces, never a fabricated level/code, always
   carrying the attorney disclaimer.

No live network: wage runs against tests/fixtures/oflc (the same fixture Agent 1
pins), and the NIOCCS/O*NET classifier is monkeypatched at the provider seam.
"""
import json
from pathlib import Path

from sqlalchemy import select

from app.h1b import counsel
from app.h1b import models as h1b_models
from app.h1b import wage_data
from app.h1b.disclaimer import DISCLAIMER_VERSION, disclaimer
from app.providers import occupation

from .conftest import AUTH, AUTH2

PETITIONER_AUTH = {"Authorization": "Bearer dev-token",
                   "X-Org-Id": "org1", "X-User-Id": "hr1"}
ADMIN_AUTH = {"Authorization": "Bearer admin-token",
              "X-Org-Id": "org1", "X-User-Id": "admin1"}

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "oflc"

# Real DOL Abilene TX (area 10180) numbers, pinned from the fixture:
#   SOC 15-1252 (Software Developers), GeoLvl 3 -> a geographic-fallback caveat.
#   Level-I hourly 32.10 -> annual round(32.10 * 2080) = 66768.
_L1_15_1252 = round(32.10 * 2080)          # 66768 (Level-I annual, prevailing floor)


def _use_oflc(monkeypatch):
    """Point wage_data at the committed OFLC fixture (never the live download)."""
    monkeypatch.setenv("ELLIS_OFLC_WAGE_DIR", str(FIXTURE_DIR))
    wage_data._CACHE.clear()


# ---------------------------------------------------------------------------
# case helpers
# ---------------------------------------------------------------------------

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
    return pet


def _employer_profile(client, **overrides):
    body = {"legal_name": "Trip.com US Inc", "fein": "12-3456789"}
    body.update(overrides)
    r = client.post("/h1b/employer-profiles", json=body, headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()["employer_profile_id"]


def _write_pet_answers(client, case_id, answers):
    r = client.post(f"/h1b/cases/{case_id}/party/petitioner/answers",
                    json={"answers": answers}, headers=PETITIONER_AUTH)
    assert r.status_code == 200, r.text


def _prepared_case(client, db, pet_answers, **case_overrides):
    profile_id = _employer_profile(client)
    out = _create_case(client, employer_profile_id=profile_id, **case_overrides)
    case_id = out["case_id"]
    _bind_petitioner(db, case_id)
    if pet_answers:
        _write_pet_answers(client, case_id, pet_answers)
    return case_id


# A worksite that resolves cleanly in the fixture: Taylor County, TX -> area 10180.
_WORKSITE = {"worksite_county": "Taylor County", "worksite_state": "TX"}


# Provider-seam fakes (real return shapes; no network).
def _fake_classify_live(**kw):
    return {"soc": [{"code": "15-1252", "title": "Software Developers",
                     "probability": 0.97}],
            "naics": [{"code": "5415",
                       "title": "Computer Systems Design and Related Services",
                       "probability": 0.98}],
            "source": "nioccs", "live": True, "caveat": occupation.NIOCCS_CAVEAT}


def _fake_classify_degraded(**kw):
    return {"soc": [], "naics": [], "source": "nioccs", "live": False,
            "caveat": occupation.NIOCCS_CAVEAT, "note": "CDC NIOCCS is unreachable"}


def _fake_onet_live(keyword):
    return {"matches": [{"code": "15-1252.00", "title": "Software Developers"}],
            "source": "onet", "live": True}


# ===========================================================================
# counsel seam: the two upgraded signals prefer COMPUTED over self-report
# ===========================================================================

def test_wage_level_signal_prefers_computed_over_self_report():
    sig = counsel.SIGNALS["wage_level_is_one"]
    # Computed level 1 fires even when the self-report says II.
    assert sig({"computed_wage": {"level": 1},
                "petitioner_answers": {"wage_level": "II"}})
    # Computed level 2 does NOT fire even when the self-report says I.
    assert not sig({"computed_wage": {"level": 2},
                    "petitioner_answers": {"wage_level": "I"}})
    # Computed None level (offer below prevailing) never fires this signal —
    # the more severe below-prevailing signal owns that case.
    assert not sig({"computed_wage": {"level": None},
                    "petitioner_answers": {"wage_level": "I"}})
    # Data unavailable -> the self-report governs, unchanged.
    assert sig({"computed_wage": None,
                "petitioner_answers": {"wage_level": "I"}})
    assert not sig({"petitioner_answers": {"wage_level": "II"}})


def test_below_prevailing_signal_prefers_computed_over_self_report():
    sig = counsel.SIGNALS["wage_offer_below_prevailing_wage"]
    # Computed meets_prevailing False fires even when the self-report looks fine.
    assert sig({"computed_wage": {"meets_prevailing": False},
                "petitioner_answers": {"wage_offer": 200000,
                                       "prevailing_wage": 50000}})
    # Computed meets_prevailing True does NOT fire even if self-report would.
    assert not sig({"computed_wage": {"meets_prevailing": True},
                    "petitioner_answers": {"wage_offer": 40000,
                                           "prevailing_wage": 50000}})
    # Computed indeterminate (None) -> the self-report governs.
    assert sig({"computed_wage": {"meets_prevailing": None},
                "petitioner_answers": {"wage_offer": 40000,
                                       "prevailing_wage": 50000}})
    # No computed data -> the self-report governs, unchanged.
    assert sig({"petitioner_answers": {"wage_offer": 40000,
                                       "prevailing_wage": 50000}})
    assert not sig({"petitioner_answers": {"wage_offer": 60000,
                                           "prevailing_wage": 50000}})


def test_compute_case_wage_resolves_area_calls_provider_and_degrades(monkeypatch):
    calls = {}

    def fake_compute(**kw):
        calls.update(kw)
        return {"level": 1, "meets_prevailing": True, "source": "DOL OFLC OEWS"}

    monkeypatch.setattr(wage_data, "is_available", lambda: True)
    monkeypatch.setattr(wage_data, "lookup_area",
                        lambda q: {"area_code": "10180", "ambiguous": False})
    monkeypatch.setattr(wage_data, "compute_wage_level", fake_compute)
    ctx = {"petitioner_answers": {"soc_code": "15-1252",
                                  "worksite_county": "Taylor County",
                                  "worksite_state": "TX", "wage_offer": 70000,
                                  "wage_offer_unit": "year"}}
    out = counsel._compute_case_wage(ctx)
    assert out["level"] == 1
    # The seam resolves the worksite to an area code and calls Agent 1's real
    # signature (area_code + soc_code + offered_wage + wage_unit).
    assert calls["area_code"] == "10180" and calls["soc_code"] == "15-1252"
    assert calls["offered_wage"] == 70000.0 and calls["wage_unit"] == "year"

    # Missing SOC/offer -> None, provider never called (never a fabricated level).
    assert counsel._compute_case_wage(
        {"petitioner_answers": {"soc_code": "15-1252"}}) is None
    # Ambiguous worksite -> None (a wrong OEWS area is a real filing error).
    monkeypatch.setattr(wage_data, "lookup_area",
                        lambda q: {"area_code": None, "ambiguous": True})
    assert counsel._compute_case_wage(ctx) is None
    # Data unavailable -> None (self-report governs).
    monkeypatch.setattr(wage_data, "is_available", lambda: False)
    assert counsel._compute_case_wage(ctx) is None


def test_compute_case_wage_none_when_data_not_downloaded():
    # Real wage_data module, but no OFLC data on disk (no fixture env) ->
    # is_available() False -> the seam degrades to None (self-report governs).
    assert counsel._compute_case_wage(
        {"petitioner_answers": {"soc_code": "15-1252",
                                "worksite_county": "Taylor County",
                                "worksite_state": "TX", "wage_offer": 70000}}) is None


def test_compute_case_wage_over_real_fixture(monkeypatch):
    _use_oflc(monkeypatch)
    out = counsel._compute_case_wage(
        {"petitioner_answers": {"soc_code": "15-1252",
                                "worksite_county": "Taylor County",
                                "worksite_state": "TX", "wage_offer": 70000,
                                "wage_offer_unit": "year"}})
    assert out is not None
    assert out["level"] == 1
    assert out["level_wages"][1] == _L1_15_1252
    assert out["meets_prevailing"] is True
    assert out["geo_level"] == 3 and out["geo_caveat"]      # statewide fallback


def test_soc_mismatch_signal_grounds_only_on_real_disagreement(monkeypatch):
    sig = counsel.SIGNALS["soc_title_duties_mismatch"]
    ctx = {"petitioner_answers": {"soc_code": "15-1252",
                                  "job_title": "Software Engineer"},
           "employer": None}
    # No stated SOC -> silent.
    assert not sig({"petitioner_answers": {}, "employer": None})
    # Classifier disagrees -> fires.
    monkeypatch.setattr(occupation, "classify_nioccs",
                        lambda **kw: {"soc": [{"code": "13-1111",
                                               "probability": 0.9}], "live": True})
    assert sig(dict(ctx))
    # Classifier agrees -> silent.
    monkeypatch.setattr(occupation, "classify_nioccs",
                        lambda **kw: {"soc": [{"code": "15-1252",
                                               "probability": 0.99}], "live": True})
    assert not sig(dict(ctx))
    # Degraded classifier (empty soc) -> silent (never a presumed mismatch).
    monkeypatch.setattr(occupation, "classify_nioccs",
                        lambda **kw: {"soc": [], "live": False})
    assert not sig(dict(ctx))


# ---- end-to-end through the counsel rfe-risks endpoint ---------------------

def test_rfe_risks_uses_computed_wage_level_and_carries_caveats(client, db,
                                                                monkeypatch):
    _use_oflc(monkeypatch)
    case_id = _prepared_case(client, db, {
        "soc_code": "15-1252", "wage_offer": 70000, "wage_offer_unit": "year",
        "wage_level": "II", **_WORKSITE})   # self-report says II; DOL computes I
    admin = client.get(f"/h1b/cases/{case_id}/counsel/rfe-risks",
                       headers=ADMIN_AUTH).json()
    by = {r["ground"]: r for r in admin["risks"]}
    assert "wage_level_1" in by, admin
    facts = by["wage_level_1"]["facts"]
    # Fired from the COMPUTED level, not the self-report, and it says so.
    assert facts["computed_wage_level"] == 1
    assert "OFLC" in facts["wage_determination_source"]
    assert facts["wage_geo_caveat"]           # GeoLvl 3 caveat surfaced
    # Offered >= computed Level-I -> below-prevailing does NOT fire.
    assert "lca_wage_below_prevailing" not in by

    # Party wall: the beneficiary sees the ground but not the computed level.
    ben = client.get(f"/h1b/cases/{case_id}/counsel/rfe-risks",
                     headers=AUTH).json()
    byb = {r["ground"]: r for r in ben["risks"]}
    assert byb["wage_level_1"]["facts"]["computed_wage_level"] == counsel.REDACTED
    # The methodology caveat (no dollar figure) still rides along for either party.
    assert byb["wage_level_1"]["facts"]["wage_geo_caveat"]


def test_rfe_risks_computed_below_prevailing_fires_and_redacts(client, db,
                                                               monkeypatch):
    _use_oflc(monkeypatch)
    # Self-report alone would NOT fire (offer 50000 >= self-reported prevailing
    # 40000); only the computed Level-I determination (66768) trips it.
    case_id = _prepared_case(client, db, {
        "soc_code": "15-1252", "wage_offer": 50000, "wage_offer_unit": "year",
        "prevailing_wage": 40000, **_WORKSITE})
    admin = client.get(f"/h1b/cases/{case_id}/counsel/rfe-risks",
                       headers=ADMIN_AUTH).json()
    by = {r["ground"]: r for r in admin["risks"]}
    assert "lca_wage_below_prevailing" in by, admin
    assert by["lca_wage_below_prevailing"]["facts"]["computed_prevailing_wage"] \
        == _L1_15_1252
    assert "wage_level_1" not in by           # computed level None -> not level I

    ben = client.get(f"/h1b/cases/{case_id}/counsel/rfe-risks",
                     headers=AUTH).json()
    byb = {r["ground"]: r for r in ben["risks"]}
    assert byb["lca_wage_below_prevailing"]["facts"]["computed_prevailing_wage"] \
        == counsel.REDACTED
    assert str(_L1_15_1252) not in json.dumps(ben["risks"])


def test_rfe_risks_soc_mismatch_is_grounded_advisory(client, db, monkeypatch):
    monkeypatch.setattr(occupation, "classify_nioccs",
                        lambda **kw: {"soc": [{"code": "13-1111",
                                               "title": "Analysts",
                                               "probability": 0.9}], "live": True})
    case_id = _prepared_case(client, db, {
        "soc_code": "15-1252", "job_title": "Software Engineer"})
    admin = client.get(f"/h1b/cases/{case_id}/counsel/rfe-risks",
                       headers=ADMIN_AUTH).json()
    by = {r["ground"]: r for r in admin["risks"]}
    assert "soc_title_duties_mismatch" in by, admin
    risk = by["soc_title_duties_mismatch"]
    assert risk["severity"] == "advisory"      # pinned; a signal to confirm
    assert risk["facts"]["nioccs_suggested_soc"] == "13-1111"
    assert risk["facts"]["stated_soc_code"] == "15-1252"
    assert risk["facts"]["nioccs_probability"] == 0.9


def test_rfe_risks_no_soc_mismatch_when_classifier_agrees(client, db, monkeypatch):
    monkeypatch.setattr(occupation, "classify_nioccs",
                        lambda **kw: {"soc": [{"code": "15-1252",
                                               "probability": 0.99}], "live": True})
    case_id = _prepared_case(client, db, {
        "soc_code": "15-1252", "job_title": "Software Engineer"})
    admin = client.get(f"/h1b/cases/{case_id}/counsel/rfe-risks",
                       headers=ADMIN_AUTH).json()
    assert "soc_title_duties_mismatch" not in {r["ground"] for r in admin["risks"]}


# ===========================================================================
# POST /h1b/cases/{id}/wage-analysis
# ===========================================================================

def test_wage_analysis_computed_level_and_caveats(client, db, monkeypatch):
    _use_oflc(monkeypatch)
    case_id = _prepared_case(client, db, {
        "soc_code": "15-1252", "wage_offer": 70000, "wage_offer_unit": "year",
        **_WORKSITE})
    r = client.post(f"/h1b/cases/{case_id}/wage-analysis", headers=PETITIONER_AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is True
    assert body["level"] == 1
    assert body["prevailing_wage"] == _L1_15_1252
    assert len(body["level_wages"]) == 4
    assert body["meets_prevailing"] is True
    assert body["geo_level"] == 3 and body["geo_caveat"]     # DOL fell off the MSA
    assert body["invalid_2080_conversion"] is False
    assert body["label_caveat"] == ""
    assert "OFLC" in body["source"] and body["as_of"] == "2026-07-01"
    assert body["soc_title"] == "Software Developers"
    # A suggestion, framed as one, with the disclaimer.
    assert body["is_suggestion"] is True and "confirm" in body["note"].lower()
    assert body["attorney_disclaimer"] == disclaimer("en")
    assert body["disclaimer_version"] == DISCLAIMER_VERSION


def test_wage_analysis_high_wage_label_surfaces_never_converts(client, db,
                                                               monkeypatch):
    _use_oflc(monkeypatch)
    # SOC 29-1221 is a "High Wage" row in the fixture: OEWS cannot estimate it,
    # so the 2080 conversion is invalid and NO level is invented.
    case_id = _prepared_case(client, db, {
        "soc_code": "29-1221", "wage_offer": 300000, "wage_offer_unit": "year",
        **_WORKSITE})
    body = client.post(f"/h1b/cases/{case_id}/wage-analysis",
                       headers=ADMIN_AUTH).json()
    assert body["available"] is True
    assert body["level"] is None
    assert body["label"] == "High Wage"
    assert body["invalid_2080_conversion"] is True
    assert "2080" in body["label_caveat"]


def test_wage_analysis_data_absent_is_honest_never_fabricated(client, db):
    # No fixture env: the OFLC data is not on disk. Never a fabricated level.
    case_id = _prepared_case(client, db, {
        "soc_code": "15-1252", "wage_offer": 70000, "wage_offer_unit": "year",
        **_WORKSITE})
    body = client.post(f"/h1b/cases/{case_id}/wage-analysis",
                       headers=PETITIONER_AUTH).json()
    assert body["available"] is False
    assert body["level"] is None
    assert body["data_status"] == "unavailable"
    assert "not downloaded" in body["reason"]
    # Even when it cannot compute, it is still a disclaimer-bearing suggestion.
    assert body["attorney_disclaimer"] == disclaimer("en")
    assert body["is_suggestion"] is True


def test_wage_analysis_unresolved_worksite_is_honest(client, db, monkeypatch):
    _use_oflc(monkeypatch)
    # A worksite that matches no OFLC area is surfaced honestly, never guessed.
    case_id = _prepared_case(client, db, {
        "soc_code": "15-1252", "wage_offer": 70000, "wage_offer_unit": "year",
        "worksite_city": "Nowheresville", "worksite_state": "ZZ"})
    body = client.post(f"/h1b/cases/{case_id}/wage-analysis",
                       headers=PETITIONER_AUTH).json()
    assert body["available"] is False and body["level"] is None
    assert body["data_status"] == "worksite_unresolved"


def test_wage_analysis_missing_inputs_is_honest(client, db, monkeypatch):
    # Provider available, but the case lacks the facts: never a fabricated level.
    _use_oflc(monkeypatch)
    case_id = _prepared_case(client, db, {})     # petitioner wrote nothing
    body = client.post(f"/h1b/cases/{case_id}/wage-analysis",
                       headers=PETITIONER_AUTH).json()
    assert body["available"] is False and body["level"] is None
    assert set(body["missing_inputs"]) == {"soc_code", "wage_offer", "worksite"}


def test_wage_analysis_beneficiary_forbidden(client, db, monkeypatch):
    _use_oflc(monkeypatch)
    case_id = _prepared_case(client, db, {
        "soc_code": "15-1252", "wage_offer": 70000, **_WORKSITE})
    # AUTH is the beneficiary on this case — the wage analysis is petitioner-private.
    r = client.post(f"/h1b/cases/{case_id}/wage-analysis", headers=AUTH)
    assert r.status_code == 403, r.text
    # Another org sees nothing.
    r2 = client.post(f"/h1b/cases/{case_id}/wage-analysis", headers=AUTH2)
    assert r2.status_code in (403, 404)
    # The admin may run it.
    assert client.post(f"/h1b/cases/{case_id}/wage-analysis",
                       headers=ADMIN_AUTH).status_code == 200


def test_wage_analysis_404_on_non_h1b_case(client):
    cid = client.post("/cases", headers=AUTH, json={
        "full_name": "Anna", "email": "a@e.com",
        "destination_country": "Mockland"}).json()["id"]
    assert client.post(f"/h1b/cases/{cid}/wage-analysis",
                       headers=ADMIN_AUTH).status_code == 404


# ===========================================================================
# POST /h1b/cases/{id}/classify-occupation
# ===========================================================================

def test_classify_occupation_returns_suggestions_with_caveat(client, db,
                                                             monkeypatch):
    monkeypatch.setattr(occupation, "classify_nioccs", _fake_classify_live)
    monkeypatch.setattr(occupation, "search_onet", _fake_onet_live)
    case_id = _prepared_case(client, db, {})
    r = client.post(f"/h1b/cases/{case_id}/classify-occupation",
                    json={"industry_text": "online travel booking software",
                          "occupation_text": "software engineer"},
                    headers=PETITIONER_AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    nioccs = body["nioccs"]
    assert nioccs["available"] is True and nioccs["live"] is True
    assert nioccs["soc"][0]["code"] == "15-1252"
    assert nioccs["soc"][0]["probability"] == 0.97
    assert nioccs["naics"][0]["code"] == "5415"
    # CDC's own status rides along; every suggestion is confirm-before-use.
    assert "no longer supported" in nioccs["caveat"]
    assert nioccs["confirm_before_use"] is True
    # O*NET matches too.
    assert body["onet"]["available"] is True
    assert body["onet"]["matches"][0]["code"] == "15-1252.00"
    # A suggestion, framed as one, with the disclaimer.
    assert body["is_suggestion"] is True
    assert body["attorney_disclaimer"] == disclaimer("en")
    assert body["disclaimer_version"] == DISCLAIMER_VERSION


def test_classify_occupation_degrades_honestly_when_unreachable(client, db,
                                                                monkeypatch):
    # NIOCCS degraded (never a fabricated code); O*NET has no key configured.
    monkeypatch.setattr(occupation, "classify_nioccs", _fake_classify_degraded)
    case_id = _prepared_case(client, db, {})
    body = client.post(f"/h1b/cases/{case_id}/classify-occupation",
                       json={"occupation_text": "software engineer"},
                       headers=ADMIN_AUTH).json()
    nioccs = body["nioccs"]
    assert nioccs["available"] is False and nioccs["live"] is False
    assert nioccs["soc"] == [] and nioccs["naics"] == []
    assert "no longer supported" in nioccs["caveat"]
    assert nioccs["note"]                       # honest degrade note
    assert body["onet"]["available"] is False   # no O*NET key configured
    # Still a disclaimer-bearing suggestion surface, never a fabricated code.
    assert body["attorney_disclaimer"] == disclaimer("en")


def test_classify_occupation_requires_text(client, db):
    case_id = _prepared_case(client, db, {})
    r = client.post(f"/h1b/cases/{case_id}/classify-occupation",
                    json={"industry_text": "", "occupation_text": ""},
                    headers=PETITIONER_AUTH)
    assert r.status_code == 422, r.text


def test_classify_occupation_beneficiary_forbidden(client, db):
    case_id = _prepared_case(client, db, {})
    r = client.post(f"/h1b/cases/{case_id}/classify-occupation",
                    json={"occupation_text": "software engineer"}, headers=AUTH)
    assert r.status_code == 403, r.text
