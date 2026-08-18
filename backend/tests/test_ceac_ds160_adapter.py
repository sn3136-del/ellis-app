"""The CEAC DS-160 adapter + its typed-flow artifacts.

Everything here is pinned against what an ATTENDED session on the live portal
actually observed (2026-08-18), and against the lines the adapter exists to
hold:

* the applicant's own acts are handoffs, never automated: the BotDetect code,
  the Privacy Act agreement, the retrieval security question, the sworn
  history questions, and Sign and Submit;
* the Security and Background screens are never mapped or pre-filled;
* CEAC's split date of birth binds per COMPONENT (day / month / year), never
  one ISO string into the year box;
* the adapter cannot run live until it is individually production-approved;
* booking is NOT done here — China schedules on a different system whose
  terms prohibit automated access.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ARTIFACTS = (Path(__file__).resolve().parents[1] / "app" / "portal_adapters"
             / "generated" / "usa-ceac-ds160" / "1")


@pytest.fixture(scope="module")
def flow():
    return json.loads((ARTIFACTS / "flow.json").read_text())


@pytest.fixture(scope="module")
def manifest():
    return json.loads((ARTIFACTS / "manifest.json").read_text())


@pytest.fixture(scope="module")
def mappings():
    return json.loads((ARTIFACTS / "mappings.json").read_text())


def _nodes(flow, action):
    return [n for n in flow if n.get("action") == action]


# ----------------------------------------------------------- artifact health

def test_flow_and_mappings_pass_the_real_validators(flow, manifest, mappings):
    from app.adapter_factory.schema import validate_field_mapping, validate_flow
    assert validate_flow(flow, allowed_hostnames=manifest["allowed_hostnames"]) == []
    for m in mappings["fields"]:
        assert validate_field_mapping(m) == []


def test_static_validator_passes_the_candidate(flow, manifest, mappings):
    import types

    from app.adapter_factory.static_validator import validate_candidate
    row = types.SimpleNamespace(
        manifest=manifest, flow=flow, field_mappings=mappings["fields"],
        document_mappings=mappings["documents"],
        evidence_rules={"banner_text_sufficient": False,
                        "required": ["submission_accepted", "submitted"]},
        recovery={"resume": "reconcile the saved application before any re-fill",
                  "on_unexpected_state": "fail_closed",
                  "rate_limits": {"searchMinIntervalMs": 60000,
                                  "maxChecksPerDay": 12}},
        kill_switch_key="usa-ceac-ds160", rollback_to_version=0, version=1)
    report = validate_candidate(row)
    assert report["passed"], report["checks"]


def test_every_hostname_is_the_official_portal(flow, manifest):
    assert manifest["allowed_hostnames"] == ["ceac.state.gov"]
    for n in flow:
        assert n["allowed_hostname"] == "ceac.state.gov"


# ------------------------------------------------- the applicant's own acts

def test_the_captcha_is_always_the_applicants(flow):
    kinds = [n["handoff_kind"] for n in _nodes(flow, "APPLICANT_HANDOFF")]
    assert "captcha" in kinds
    # Nothing in the flow reads, types, or otherwise touches the code box.
    blob = json.dumps(flow).lower()
    assert "txtcodetextbox" not in blob, "the flow must never target the code input"
    assert "captcha" not in json.dumps(
        [n for n in flow if n["action"] != "APPLICANT_HANDOFF"]).lower()


def test_privacy_agreement_and_security_answer_are_handoffs(flow):
    kinds = [n["handoff_kind"] for n in _nodes(flow, "APPLICANT_HANDOFF")]
    # The Privacy Act / CFAA notice: signed verbatim by the applicant.
    assert "portal_terms_consent" in kinds
    # The retrieval security answer is the key to their application.
    assert "credentials" in kinds
    # And no node ever types into those controls.
    blob = json.dumps(flow)
    assert "txtAnswer" not in blob
    assert "chkbxPrivacyAct" not in blob


def test_security_and_background_is_never_mapped(flow, mappings):
    """Sworn answers are never a mapped field. The words may appear in a
    handoff's PURPOSE prose (that is where the flow says who answers them);
    they may never appear as something Ellis fills."""
    for m in mappings["fields"]:
        for banned in ("secur", "background", "arrest", "deport", "genocide"):
            assert banned not in m["portal_field"].lower()
            assert banned not in m["selector"].lower()
            assert banned not in m["ellis_field"].lower()
    # No node targets a Security-and-Background control either.
    for n in flow:
        assert "securityandbackground" not in str(n.get("selector", "")).lower()
        if n["action"] != "APPLICANT_HANDOFF":
            assert "genocide" not in json.dumps(n).lower()


def test_no_node_signs_or_submits_the_application(flow):
    blob = json.dumps(flow).lower()
    for banned in ("btnsign", "signandsubmit", "sign_and_submit", "btnsubmit"):
        assert banned not in blob


# ---------------------------------------------------- what Ellis DOES fill

def test_personal1_fields_carry_the_real_observed_selectors(mappings):
    by_field = {m["ellis_field"]: m for m in mappings["fields"]}
    # Every selector below was read off the live form in an attended session.
    assert by_field["surname"]["selector"] == \
        '[id="ctl00_SiteContentPlaceHolder_FormView1_tbxAPP_SURNAME"]'
    assert by_field["given_names"]["selector"] == \
        '[id="ctl00_SiteContentPlaceHolder_FormView1_tbxAPP_GIVEN_NAME"]'
    assert by_field["sex"]["selector"] == \
        '[id="ctl00_SiteContentPlaceHolder_FormView1_ddlAPP_GENDER"]'
    assert by_field["place_of_birth"]["selector"] == \
        '[id="ctl00_SiteContentPlaceHolder_FormView1_tbxAPP_POB_CITY"]'
    for m in mappings["fields"]:
        assert m["selector"].startswith('[id="ctl00_SiteContentPlaceHolder')
    # The three DS-160 screens verified live are all represented.
    assert {m["page_key"] for m in mappings["fields"]} >= {
        "personal1", "personal2", "travel"}
    # Travel carries the tourist class: B (purpose) + B2-TM (sub-class).
    travel = {m["ellis_field"]: m for m in mappings["fields"]
              if m["page_key"] == "travel"}
    assert "travel_purpose_class" in travel
    assert travel["travel_purpose_subclass"]["portal_field"] == \
        "dlPrincipalAppTravel_ctl00_ddlOtherPurpose"


def test_the_split_date_of_birth_binds_per_component(mappings, flow):
    """CEAC asks for the date of birth in THREE controls. Binding one ISO
    string into the year box leaves month and day empty on a form that will
    not continue — the TDAC bug, pinned here so it cannot come back."""
    dob = [m for m in mappings["fields"] if m["ellis_field"] == "birth_date"]
    assert len(dob) == 3, "day, month and year each need their own mapping"
    assert {m["format"] for m in dob} == {"DD", "MON", "YYYY"}
    portal_fields = {m["portal_field"] for m in dob}
    assert portal_fields == {"ddlDOBDay", "ddlDOBMonth", "tbxDOBYear"}
    # The flow nodes carry the component too, so the runtime renders the right
    # part into each control.
    parts = {n.get("date_part") for n in flow if n.get("input_source") == "birth_date"}
    assert parts == {"DD", "MON", "YYYY"}


def test_the_post_is_chosen_from_ceacs_own_codes():
    from app.portal.adapters.us_ceac_ds160 import DEFAULT_POST_CODE, POST_CODES
    # Read live off the location dropdown 2026-08-18.
    assert POST_CODES == {"beijing": "BEJ", "guangzhou": "GUZ",
                          "shanghai": "SHG", "shenyang": "SNY", "wuhan": "WUH"}
    assert DEFAULT_POST_CODE == "SHG"


def test_the_entry_gate_matches_the_live_page(manifest):
    gate = manifest["entry_gate"]
    assert gate["declared_handoffs"] == ["captcha"]
    assert gate["expect_path"] == "/GenNIV/Common/ConfirmApplicationID.aspx"
    actions = {a["action"]: a for a in gate["actions"]}
    assert actions["SELECT_OPTION"]["option_value"] == "SHG"
    assert "ddlLocation" in actions["SELECT_OPTION"]["selector"]
    assert "lnkNew" in actions["CLICK"]["selector"]
    # The postback quirk that cost a live session: recorded so the next
    # engineer does not rediscover it.
    assert "postback" in gate["description"].lower()


# ------------------------------------------------------- the adapter itself

def test_adapter_is_fail_closed_until_individually_approved():
    from app.portal.adapters.us_ceac_ds160 import build_us_ceac_ds160_adapter
    from app.portal.mock_portal import MockPortal
    a = build_us_ceac_ds160_adapter(MockPortal())
    assert a.production_enabled is False
    assert a.production_approval_status == "tested"
    assert a.approved_domains == ["ceac.state.gov"]
    # The applicant signs and submits; Ellis prepares (22 C.F.R. 41.103).
    assert a.representative_submission == "applicant"
    assert a.personal_declaration_required is True
    # Nothing about appointments happens on CEAC.
    assert a.appointment_search == "none"
    assert a.appointment_booking == "prohibited"
    # Ellis never pays the MRV fee.
    assert a.payment_policy == "applicant"
    assert a.third_party_payment_policy == "applicant"
    for banned in ("solve_captcha", "bypass_bot_detection"):
        assert banned in a.prohibited_actions


def test_application_mappings_stay_empty_until_the_attended_pass():
    """A guessed CEAC selector would type a passport number into the wrong
    screen of a sworn federal form. Empty is the honest state."""
    from app.portal.adapters.us_ceac_ds160 import (KNOWN_LIMITATIONS,
                                                   build_us_ceac_ds160_adapter)
    from app.portal.mock_portal import MockPortal
    a = build_us_ceac_ds160_adapter(MockPortal())
    assert a.application_mappings == []
    assert any("not field-mapped yet" in lim for lim in KNOWN_LIMITATIONS)
    assert any("Security and Background" in lim for lim in KNOWN_LIMITATIONS)


def test_the_scheduling_adapter_is_not_for_china():
    """ais.usvisa-info.com does not serve mainland China (verified
    2026-08-18): China runs on ustraveldocs.com / usvisascheduling.com, whose
    terms forbid automated access. The adapter records that, so nobody points
    it at a Chinese post."""
    from app.portal.adapters import us_visa_scheduling as mod
    doc = " ".join((mod.__doc__ or "").lower().split())   # collapse line wraps
    assert "not mainland china" in doc
    assert "ustraveldocs" in doc
    assert "operator_manual" in doc


def test_registry_registers_ceac_only_in_mock_allowed_modes():
    from app.portal import driver_factory
    src = (Path(driver_factory.__file__)).read_text()
    assert "build_us_ceac_ds160_adapter" in src
    # It sits inside the mock_portal_allowed branch, never the real-only path.
    real_only = src.split("if not settings().mock_portal_allowed:")[1].split("return None")[0]
    assert "ceac" not in real_only.lower()
