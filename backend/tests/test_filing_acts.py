"""filing_acts — the single authority for human acts and the tap count.

Pins: (1) the tap count is COUNTED from the acts, never a second number that
could drift; (2) every act key stays inside the UI's localized vocabulary;
(3) no-portal / paper filings report an honest unknown with a reason instead
of an invented count; (4) the payloads the filing cockpits render actually
carry human_acts + taps_to_done from this module — the frontend keeps no
fallback copy, so an endpoint that stopped sending them would show an honest
"unknown", never a fabricated list.
"""
from sqlalchemy import select

from app import filing_acts
from app.h1b import models as h1b_models

from .conftest import AUTH

PETITIONER_AUTH = {"Authorization": "Bearer dev-token",
                   "X-Org-Id": "org1", "X-User-Id": "hr1"}


# ------------------------------------------------------------- pure contract

def test_every_act_key_is_in_the_ui_vocabulary():
    for filing, acts in filing_acts._REGISTRY.items():
        assert acts, f"{filing} has no acts — a filing with no human act left "
        for act in acts:
            assert act["key"] in filing_acts.ACT_VOCABULARY, (filing, act)
            # who/why/ellis_does are the server's own words: never empty.
            assert act["who"] and act["why"] and act["ellis_does"], (filing,
                                                                     act)


def test_taps_are_counted_from_the_acts_never_a_second_number():
    for filing in filing_acts._REGISTRY:
        taps = filing_acts.taps_to_done(filing)
        acts = filing_acts.acts_for(filing)
        if not taps["known"]:
            assert taps["reason"]
            continue
        assert taps["min"] == sum(1 for a in acts if not a["conditional"])
        assert taps["max"] == len(acts)
        assert taps["min"] <= taps["max"]


def test_known_tap_counts_match_the_filing_reality():
    # FLAG filings: login, review, submit — three, no conditionals.
    assert filing_acts.taps_to_done("eta-9035") == {
        "known": True, "min": 3, "max": 3,
        "basis": "counted from the named human acts of this filing"}
    # I-129 online: login, sign, pay, submit — four.
    i129 = filing_acts.taps_to_done("i-129")
    assert (i129["min"], i129["max"]) == (4, 4)


def test_no_portal_and_paper_filings_report_honest_unknown():
    # Both consular routes are here on purpose: Ellis drives neither the
    # counter nor the official site, so a tap count would be an invention.
    for filing in ("paf", "i-129-paper", "consular-in-person",
                   "consular-online"):
        taps = filing_acts.taps_to_done(filing)
        assert taps["known"] is False and taps["reason"]
        # ... but the ACTS are still named: unknown taps never erases the
        # human acts themselves.
        assert filing_acts.acts_for(filing)


def test_unknown_filing_gets_nothing_invented():
    assert filing_acts.acts_for("made-up-form") == []
    taps = filing_acts.taps_to_done("made-up-form")
    assert taps["known"] is False and taps["reason"]


def test_consular_key_maps_the_spec_submission_modes():
    assert filing_acts.consular_key("in_person") == "consular-in-person"
    assert filing_acts.consular_key("applicant_online") == "consular-online"


def test_signature_and_login_acts_are_non_delegable():
    for filing, acts in filing_acts._REGISTRY.items():
        for act in acts:
            if act["key"] in ("sign", "biometrics"):
                assert act["non_delegable"] is True, (filing, act)


# --------------------------------------------------- the payloads carry them

def _h1b_case(client, db):
    r = client.post("/h1b/cases", json={
        "case_kind": "extension", "beneficiary_full_name": "WEI ZHANG",
        "beneficiary_email": "wei.zhang@example.com",
        "beneficiary_abroad": False, "beneficiary_in_us": True,
        "first_h1b": False}, headers=AUTH)
    assert r.status_code == 200, r.text
    case_id = r.json()["case_id"]
    pet = db.execute(select(h1b_models.CaseParty).where(
        h1b_models.CaseParty.application_id == case_id,
        h1b_models.CaseParty.role == "petitioner")).scalars().first()
    pet.user_id = "hr1"
    db.commit()
    return case_id


def test_prepare_payload_carries_acts_and_counted_taps(client, db):
    case_id = _h1b_case(client, db)
    r = client.post(f"/h1b/cases/{case_id}/forms/i-129/prepare",
                    headers=PETITIONER_AUTH)
    assert r.status_code == 200, r.text
    out = r.json()
    assert [a["key"] for a in out["human_acts"]] == ["login", "sign", "pay",
                                                     "submit"]
    assert out["taps_to_done"] == filing_acts.taps_to_done("i-129")


def test_consular_form_carries_acts_keyed_on_its_own_submission_mode(client):
    # Schengen (FRA): the uniform form is lodged in person -> in-person acts,
    # and NO tap count (ink and an appointment are acts, not taps).
    cid = client.post("/cases", headers=AUTH, json={
        "full_name": "Wei Zhang", "email": "wei@e.com",
        "destination_country": "FRA"}).json()["id"]
    out = client.get(f"/cases/{cid}/consular-form", headers=AUTH).json()
    assert out["available"] is True and out["submission"] == "in_person"
    assert [a["key"] for a in out["human_acts"]] == ["review", "sign",
                                                     "submit"]
    assert out["taps_to_done"]["known"] is False
    assert out["taps_to_done"]["reason"]

    # US (DS-160 prep sheet): the applicant works the official site
    # themselves -> online acts, and still no invented tap count.
    cid2 = client.post("/cases", headers=AUTH, json={
        "full_name": "Wei Zhang", "email": "wei@e.com",
        "destination_country": "USA"}).json()["id"]
    out2 = client.get(f"/cases/{cid2}/consular-form", headers=AUTH).json()
    assert out2["available"] is True and out2["submission"] == "applicant_online"
    assert "sign" in [a["key"] for a in out2["human_acts"]]
    assert out2["taps_to_done"]["known"] is False


def test_paf_manifest_carries_the_review_act_and_no_tap_count(client, db):
    case_id = _h1b_case(client, db)
    r = client.get(f"/h1b/cases/{case_id}/paf/manifest",
                   headers=PETITIONER_AUTH)
    assert r.status_code == 200, r.text
    out = r.json()
    assert [a["key"] for a in out["human_acts"]] == ["review"]
    assert out["taps_to_done"]["known"] is False
    assert out["taps_to_done"]["reason"]
