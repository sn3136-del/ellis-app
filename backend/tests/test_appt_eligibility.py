"""Appointment eligibility triage — the module that decides whether an in-person
act is needed at all.

Every test here is an HONESTY test. The module is curated data plus arithmetic,
so the interesting failures are not crashes: they are a confident verdict built
out of an absent fact, a waiver applied to a category that lost it in 2025, or a
59-month boundary that is off by a day and sends an applicant to a VAC they did
not need (or, far worse, tells them to stay home when the law says appear).

Four invariants are asserted repeatedly:

  * each curated rule FIRES on the facts it covers and ABSTAINS on the rest;
  * an unknown or missing input yields ``"unknown"`` plus the thing that would
    settle it — never True, never False;
  * the 59-month window is exact at 58 / 59 / 60 months, in both directions,
    including month-end clamping;
  * every curated row carries a resolvable source and an as_of date.

No test touches the network: the module performs no I/O at all, which is itself
asserted. All fixture dates and visa facts are synthetic.
"""
import ast
import inspect
from datetime import date, timedelta

import pytest

from app import appt_eligibility as ae

TODAY = date(2026, 8, 11)


# --------------------------------------------------------------------------
# US: the category gate. H-1B is the case the whole H1B product depends on.

def test_h1b_is_never_waiver_eligible_at_any_post():
    for post in ("China", "Canada", "India", ""):
        got = ae.us_appointment_needed(
            {"visa_category": "H-1B", "post_country": post}, today=TODAY)
        assert got["needed"] is True, post
        assert got["waiver_rule"] is None or got["waiver_rule"] == "diplomatic_official"
        assert got["citations"], "a verdict must carry its source"
        assert got["as_of"] == ae.AS_OF


@pytest.mark.parametrize("spelling", ["H-1B", "H1B", "h1b", "h-1b", " H1B "])
def test_h1b_spellings_all_normalize_to_the_same_verdict(spelling):
    got = ae.us_appointment_needed({"visa_category": spelling,
                                    "post_country": "Canada"}, today=TODAY)
    assert got["needed"] is True
    assert got["visa_category"] == "H-1B"


def test_a_category_outside_the_waiver_table_needs_an_interview_without_the_post():
    # The 2025 narrowing is a closed list; F-1 is not on it, and that verdict
    # does not depend on knowing where the applicant applies.
    got = ae.us_appointment_needed({"visa_category": "F-1"}, today=TODAY)
    assert got["needed"] is True
    assert ae.WAIVER_EFFECTIVE_FROM in got["reason"]


def test_missing_category_is_unknown_and_names_the_missing_fact():
    got = ae.us_appointment_needed({"post_country": "Canada"}, today=TODAY)
    assert got["needed"] == ae.UNKNOWN
    assert "visa_category" in got["missing_facts"]
    assert got["citations"]


def test_unrecognized_category_abstains_rather_than_applying_the_closed_list():
    got = ae.us_appointment_needed({"visa_category": "Z-9",
                                    "post_country": "Canada"}, today=TODAY)
    assert got["needed"] == ae.UNKNOWN
    assert "Z-9" in got["reason"]


# --------------------------------------------------------------------------
# US: the B-1/B-2 renewal row — the one row an ordinary traveller can use.

def _b_renewal_facts(**over):
    facts = {
        "visa_category": "B1/B2",
        "post_country": "Canada",
        "nationality": "Canada",
        "prior_visa_category": "B1/B2",
        "prior_visa_full_validity": True,
        "prior_visa_expires_on": "2026-03-01",     # expired 5 months ago
        "prior_visa_issued_on": "2016-03-01",
        "date_of_birth": "1990-05-04",             # 25 at prior issuance
        "prior_refusal": False,
    }
    facts.update(over)
    return facts


def test_b_renewal_meeting_every_condition_is_waiver_eligible():
    got = ae.us_appointment_needed(_b_renewal_facts(), today=TODAY)
    assert got["needed"] is False
    assert got["waiver_rule"] == "b_visa_renewal"
    # A waiver is never a right: officer discretion rides along with every pass.
    assert any("consular officer" in c.lower() for c in got["caveats"])


def test_b_renewal_more_than_12_months_expired_needs_an_interview():
    got = ae.us_appointment_needed(
        _b_renewal_facts(prior_visa_expires_on="2025-06-01"), today=TODAY)
    assert got["needed"] is True
    assert "renewal_within_12_months" in got["unmet_conditions"]


def test_the_12_month_renewal_window_is_inclusive_at_its_edge():
    # Expiry exactly 12 months before today still renews inside the window;
    # one day earlier does not.
    inside = ae.us_appointment_needed(
        _b_renewal_facts(prior_visa_expires_on="2025-08-11"), today=TODAY)
    outside = ae.us_appointment_needed(
        _b_renewal_facts(prior_visa_expires_on="2025-08-10"), today=TODAY)
    assert inside["needed"] is False
    assert outside["needed"] is True


def test_applicant_under_18_at_prior_issuance_needs_an_interview():
    got = ae.us_appointment_needed(
        _b_renewal_facts(date_of_birth="2010-05-04"), today=TODAY)
    assert got["needed"] is True
    assert "age_18_or_over_at_prior_issuance" in got["unmet_conditions"]


def test_a_prior_refusal_defeats_the_waiver_row():
    got = ae.us_appointment_needed(_b_renewal_facts(prior_refusal=True), today=TODAY)
    assert got["needed"] is True
    assert "no_unwaived_prior_refusal" in got["unmet_conditions"]


def test_a_missing_condition_fact_is_unknown_not_a_pass():
    facts = _b_renewal_facts()
    facts.pop("prior_visa_full_validity")
    got = ae.us_appointment_needed(facts, today=TODAY)
    assert got["needed"] == ae.UNKNOWN
    assert "prior_visa_full_validity" in got["missing_facts"]
    # The conditions are published with the abstention so the UI can ask.
    assert "prior_visa_full_validity" in got["conditions"]


def test_an_empty_string_fact_is_missing_rather_than_false():
    got = ae.us_appointment_needed(_b_renewal_facts(prior_refusal=""), today=TODAY)
    assert got["needed"] == ae.UNKNOWN
    assert "no_unwaived_prior_refusal" in got["missing_facts"]


@pytest.mark.parametrize("no_word", ["no", "No", "false", "FALSE", "0", "否"])
def test_a_string_no_from_a_json_form_means_no(no_word):
    """Plain truthiness reads every non-empty string as True, which on
    `prior_visa_full_validity` would hand out a waiver the applicant does not
    have. These answers arrive from JSON forms, so the words have to work."""
    got = ae.us_appointment_needed(
        _b_renewal_facts(prior_visa_full_validity=no_word), today=TODAY)
    assert got["needed"] is True
    assert "prior_visa_full_validity" in got["unmet_conditions"]


@pytest.mark.parametrize("yes_word", ["yes", "TRUE", "1", "是"])
def test_a_string_yes_means_yes(yes_word):
    got = ae.us_appointment_needed(
        _b_renewal_facts(prior_visa_full_validity=yes_word), today=TODAY)
    assert got["needed"] is False


def test_an_unreadable_answer_is_not_an_answer():
    got = ae.us_appointment_needed(
        _b_renewal_facts(prior_visa_full_validity="probably?"), today=TODAY)
    assert got["needed"] == ae.UNKNOWN
    assert "prior_visa_full_validity" in got["missing_facts"]


def test_applying_outside_the_home_country_defeats_the_waiver():
    got = ae.us_appointment_needed(
        _b_renewal_facts(post_country="Mexico", nationality="Canada",
                         residence_country="Canada"), today=TODAY)
    assert got["needed"] is True
    assert "applying_in_country_of_nationality_or_residence" in got["unmet_conditions"]


def test_prior_visa_in_a_different_category_defeats_the_waiver():
    got = ae.us_appointment_needed(
        _b_renewal_facts(prior_visa_category="F-1"), today=TODAY)
    assert got["needed"] is True
    assert "prior_visa_same_category" in got["unmet_conditions"]


# --------------------------------------------------------------------------
# US: post-level policy outranks category eligibility.

def test_mission_china_interviews_every_b_applicant_even_a_perfect_renewal():
    got = ae.us_appointment_needed(
        _b_renewal_facts(post_country="China", nationality="China"), today=TODAY)
    assert got["needed"] is True
    assert "dropbox" in got["reason"] or "interview-waiver" in got["reason"]
    assert {c["id"] for c in got["citations"]} >= {"mission_china_visas"}


@pytest.mark.parametrize("spelling", ["China", "CN", "PRC", "中国",
                                      "People's Republic of China"])
def test_china_post_is_recognized_however_it_is_spelled(spelling):
    got = ae.us_appointment_needed(
        _b_renewal_facts(post_country=spelling), today=TODAY)
    assert got["needed"] is True


def test_a_waiver_eligible_category_with_no_post_abstains():
    # B renewals can be waiver-eligible, but posts apply their own policy, so
    # without the post there is no answer.
    facts = _b_renewal_facts()
    facts.pop("post_country")
    got = ae.us_appointment_needed(facts, today=TODAY)
    assert got["needed"] == ae.UNKNOWN
    assert "post_country" in got["missing_facts"]


def test_diplomatic_category_keeps_the_waiver_outside_china():
    got = ae.us_appointment_needed(
        {"visa_category": "A-1", "post_country": "Canada"}, today=TODAY)
    assert got["needed"] is False
    assert got["waiver_rule"] == "diplomatic_official"


def test_diplomatic_category_at_a_china_post_abstains_instead_of_guessing():
    got = ae.us_appointment_needed(
        {"visa_category": "A-1", "post_country": "China"}, today=TODAY)
    assert got["needed"] == ae.UNKNOWN
    assert {c["id"] for c in got["citations"]} >= {"mission_china_visas",
                                                   "dos_interview_waiver_2025"}


def test_h2a_renewal_row_needs_the_renewal_fact_before_it_passes():
    unknown = ae.us_appointment_needed(
        {"visa_category": "H-2A", "post_country": "Mexico",
         "nationality": "Mexico"}, today=TODAY)
    assert unknown["needed"] == ae.UNKNOWN
    assert "is_renewal" in unknown["missing_facts"]

    got = ae.us_appointment_needed(
        {"visa_category": "H-2A", "post_country": "Mexico",
         "nationality": "Mexico", "is_renewal": True, "prior_refusal": False},
        today=TODAY)
    assert got["needed"] is False
    assert got["waiver_rule"] == "h2a_renewal"


# --------------------------------------------------------------------------
# EVUS.

def test_evus_required_for_a_prc_national_on_a_ten_year_b1b2():
    got = ae.evus_status({"nationality": "China", "visa_category": "B1/B2",
                          "us_visa_validity_years": 10})
    assert got["required"] is True
    assert got["url"] == "https://www.evus.gov/"
    assert "not an appointment" in got["note"]


def test_evus_not_required_for_a_non_prc_national():
    got = ae.evus_status({"nationality": "Japan", "visa_category": "B1/B2",
                          "us_visa_validity_years": 10})
    assert got["required"] is False


def test_evus_not_required_for_an_h1b_holder():
    got = ae.evus_status({"nationality": "China", "visa_category": "H-1B"})
    assert got["required"] is False
    assert "10-year B-1/B-2" in got["note"]


def test_evus_unknown_when_the_visa_length_is_not_on_record():
    got = ae.evus_status({"nationality": "China", "visa_category": "B1/B2"})
    assert got["required"] == ae.UNKNOWN
    assert got["missing_facts"]


def test_evus_unknown_without_a_nationality():
    got = ae.evus_status({"visa_category": "B1/B2", "us_visa_validity_years": 10})
    assert got["required"] == ae.UNKNOWN


def test_evus_five_year_visa_is_out_of_scope():
    got = ae.evus_status({"nationality": "China", "visa_category": "B1/B2",
                          "us_visa_validity_years": 5})
    assert got["required"] is False


# --------------------------------------------------------------------------
# Schengen: the 59-month VIS reuse window. The crux of the whole feature.

def _enrolled_months_ago(months: int) -> str:
    """A VIS enrolment date exactly ``months`` calendar months before TODAY."""
    return ae._add_months(TODAY, -months).isoformat()


@pytest.mark.parametrize("months,within,required", [
    (0, True, False),
    (12, True, False),
    (58, True, False),     # inside — fingerprints are copied forward
    (59, False, True),     # AT the boundary: Art. 13(3) says LESS THAN 59
    (60, False, True),     # past it
    (72, False, True),
])
def test_the_59_month_boundary_is_exact(months, within, required):
    got = ae.schengen_biometrics_required(
        {"fingerprints_enrolled_on": _enrolled_months_ago(months),
         "prior_schengen_visa": True}, today=TODAY)
    assert got["within_59_months"] is within, months
    assert got["required"] is required, months
    assert got["months_since_enrolment"] == months


def test_one_day_decides_the_boundary():
    """Art. 13(3) says LESS THAN 59 months, so the boundary day itself is out
    and the day after it is in. One day either way changes whether a person has
    to fly to a visa centre."""
    on_the_line = ae._add_months(TODAY, -59)
    lapsed = ae.schengen_biometrics_required(
        {"fingerprints_enrolled_on": on_the_line.isoformat(),
         "prior_schengen_visa": True}, today=TODAY)
    assert lapsed["required"] is True
    assert lapsed["within_59_months"] is False

    one_day_newer = ae.schengen_biometrics_required(
        {"fingerprints_enrolled_on": (on_the_line + timedelta(days=1)).isoformat(),
         "prior_schengen_visa": True}, today=TODAY)
    assert one_day_newer["required"] is False
    assert one_day_newer["within_59_months"] is True


def test_month_end_clamping_does_not_shift_the_window():
    # 2021-08-31 + 59 months lands in a 30-day month; the clamp must not let a
    # lapsed applicant read as reusable.
    on = date(2026, 7, 30)
    got = ae.schengen_biometrics_required(
        {"fingerprints_enrolled_on": "2021-08-31", "prior_schengen_visa": True,
         "application_date": on.isoformat()}, today=TODAY)
    assert got["boundary_date"] == "2026-07-31"
    assert got["required"] is False
    lapsed = ae.schengen_biometrics_required(
        {"fingerprints_enrolled_on": "2021-08-31", "prior_schengen_visa": True,
         "application_date": "2026-07-31"}, today=TODAY)
    assert lapsed["required"] is True


def test_first_time_applicant_must_appear_and_it_is_non_delegable():
    got = ae.schengen_biometrics_required({"first_time_applicant": True},
                                          today=TODAY)
    assert got["required"] is True
    assert got["non_delegable"] is True
    assert {c["id"] for c in got["citations"]} >= {"visa_code_art43_45"}


def test_no_prior_schengen_visa_means_appear_in_person():
    got = ae.schengen_biometrics_required({"prior_schengen_visa": False},
                                          today=TODAY)
    assert got["required"] is True
    assert got["within_59_months"] is False


def test_unknown_prior_date_names_the_document_that_would_settle_it():
    got = ae.schengen_biometrics_required({"prior_schengen_visa": True},
                                          today=TODAY)
    assert got["required"] == ae.UNKNOWN
    assert got["within_59_months"] is None
    evidence = " ".join(e["evidence"] + e["how"] for e in got["evidence_needed"])
    assert "sticker" in evidence
    # Ellis can read that sticker itself — the provider is named, so the UI can
    # offer the upload instead of a typing task.
    assert "read_prior_visa" in evidence


def test_a_future_enrolment_date_is_bad_data_not_a_fresh_enrolment():
    got = ae.schengen_biometrics_required(
        {"fingerprints_enrolled_on": "2027-01-01", "prior_schengen_visa": True},
        today=TODAY)
    assert got["required"] == ae.UNKNOWN
    assert got["evidence_needed"]


def test_the_application_date_not_today_anchors_the_window():
    # A case being prepared now for an application three months out is measured
    # from the application date.
    facts = {"fingerprints_enrolled_on": _enrolled_months_ago(58),
             "prior_schengen_visa": True, "application_date": "2026-11-11"}
    got = ae.schengen_biometrics_required(facts, today=TODAY)
    assert got["required"] is True
    assert got["within_59_months"] is False


# --------------------------------------------------------------------------
# Schengen: the sticker is a PROXY, and a proxy near the line proves nothing.

def test_the_visa_sticker_issue_date_is_accepted_as_a_proxy_with_a_caveat():
    got = ae.schengen_biometrics_required(
        {"prior_visa_issued_on": _enrolled_months_ago(24),
         "prior_schengen_visa": True}, today=TODAY)
    assert got["required"] is False
    assert got["anchor_kind"] == "prior_visa_issue_date_proxy"
    assert any("proxy" in c for c in got["caveats"])


def test_a_proxy_that_only_just_clears_the_boundary_abstains():
    # The real enrolment date is the prior APPLICATION, always earlier than the
    # sticker — so a 20-day margin cannot prove reuse.
    anchor = ae._add_months(TODAY, -59) + timedelta(days=20)
    got = ae.schengen_biometrics_required(
        {"prior_visa_issued_on": anchor.isoformat(), "prior_schengen_visa": True},
        today=TODAY)
    assert got["required"] == ae.UNKNOWN
    assert got["within_59_months"] is True     # the proxy says so; it is not enough
    assert got["evidence_needed"]


def test_an_authoritative_enrolment_date_at_the_same_margin_does_decide():
    anchor = ae._add_months(TODAY, -59) + timedelta(days=20)
    got = ae.schengen_biometrics_required(
        {"fingerprints_enrolled_on": anchor.isoformat(),
         "prior_schengen_visa": True}, today=TODAY)
    assert got["required"] is False
    assert got["anchor_kind"] == "vis_enrolment"


def test_a_proxy_past_the_boundary_is_decisive_because_enrolment_is_older_still():
    got = ae.schengen_biometrics_required(
        {"prior_visa_issued_on": _enrolled_months_ago(70),
         "prior_schengen_visa": True}, today=TODAY)
    assert got["required"] is True


# --------------------------------------------------------------------------
# Schengen: the age exemptions, including the contested 6-11 band.

def test_a_child_under_six_is_exempt_from_fingerprinting():
    got = ae.schengen_biometrics_required(
        {"date_of_birth": "2022-01-01", "first_time_applicant": True},
        today=TODAY)
    assert got["required"] is False
    assert got["basis"] == "Art. 13(7)(a)"


def test_a_child_between_six_and_eleven_is_unknown_not_exempt():
    got = ae.schengen_biometrics_required(
        {"age": 8, "first_time_applicant": True}, today=TODAY)
    assert got["required"] == ae.UNKNOWN
    assert {c["id"] for c in got["citations"]} >= {"vis_recast_2021_1134"}


def test_a_twelve_year_old_falls_through_to_the_ordinary_rules():
    got = ae.schengen_biometrics_required(
        {"age": 12, "first_time_applicant": True}, today=TODAY)
    assert got["required"] is True


def test_a_claimed_physical_exemption_is_the_consulates_call():
    got = ae.schengen_biometrics_required(
        {"fingerprint_exemption": "amputation", "prior_schengen_visa": True,
         "fingerprints_enrolled_on": _enrolled_months_ago(6)}, today=TODAY)
    assert got["required"] == ae.UNKNOWN
    assert "TEMPORARY" in got["reason"]


# --------------------------------------------------------------------------
# triage(): the combined verdict the cockpit renders.

def test_us_triage_lists_the_human_acts_and_never_claims_end_to_end_delegation():
    got = ae.triage({"destination_country": "United States",
                     **_b_renewal_facts(post_country="China",
                                        nationality="China")}, today=TODAY)
    assert got["route"] == "us"
    assert got["verdict"]["in_person_required"] is True
    assert got["verdict"]["agent_deliverable_end_to_end"] is False
    acts = {a["act"]: a for a in got["human_acts"]}
    assert {"ds160_sign_submit", "pay_mrv", "book_interview_slot",
            "attend_interview"} <= set(acts)
    assert acts["book_interview_slot"]["non_delegable"] is True
    assert "cancel" in acts["book_interview_slot"]["why"].lower()


def test_a_group_case_hands_the_booking_action_to_the_coordinator():
    got = ae.triage({"destination_country": "US",
                     "visa_category": "B1/B2", "post_country": "China",
                     "group": {"member_count": 24, "kind": "tour_group"}},
                    today=TODAY)
    acts = {a["act"]: a for a in got["human_acts"]}
    assert "group_request_submit" in acts
    assert acts["group_request_submit"]["who"] == "group_coordinator"
    assert "book_interview_slot" not in acts


def test_a_family_of_ten_is_not_a_group_request():
    got = ae.triage({"destination_country": "US", "visa_category": "B1/B2",
                     "post_country": "China",
                     "group": {"member_count": 10, "kind": "family"}},
                    today=TODAY)
    acts = {a["act"] for a in got["human_acts"]}
    assert "book_interview_slot" in acts
    assert "group_request_submit" not in acts


def test_a_waived_us_case_swaps_the_interview_for_a_document_drop_off():
    got = ae.triage({"destination_country": "United States",
                     **_b_renewal_facts()}, today=TODAY)
    assert got["verdict"]["in_person_required"] is False
    acts = {a["act"]: a for a in got["human_acts"]}
    assert "submit_documents" in acts
    assert acts["submit_documents"]["non_delegable"] is False
    # The conditional interview stays on the list, flagged as discretionary.
    assert "Conditional" in acts["attend_interview"]["why"]


def test_evus_becomes_a_human_act_when_it_is_required():
    got = ae.triage({"destination_country": "United States",
                     "visa_category": "B1/B2", "post_country": "China",
                     "nationality": "China", "us_visa_validity_years": 10},
                    today=TODAY)
    assert got["evus"]["required"] is True
    assert "evus_enrol" in {a["act"] for a in got["human_acts"]}


def test_schengen_reuse_plus_a_verified_no_appearance_state_is_agent_end_to_end():
    got = ae.triage({"destination_country": "France",
                     "prior_visa": {"prior_schengen_visa": True,
                                    "fingerprints_enrolled_on": _enrolled_months_ago(24)},
                     "route_facts": {"submission_without_appearance_permitted": True}},
                    today=TODAY)
    assert got["route"] == "schengen"
    assert got["member_state"] == "France"
    assert got["verdict"]["in_person_required"] is False
    assert got["verdict"]["agent_deliverable_end_to_end"] is True
    acts = {a["act"]: a for a in got["human_acts"]}
    assert "appear_biometrics" not in acts
    # The mandate signature survives everything: Art. 45 needs the applicant's
    # own authorization.
    assert acts["sign_mandate"]["non_delegable"] is True


def test_schengen_reuse_without_a_verified_state_policy_is_unknown_not_yes():
    """Two gates, reported separately. EU law settles the fingerprints; whether
    the applicant must show up to LODGE is the member state's own rule, and an
    unverified state rule leaves the in-person question open."""
    got = ae.triage({"destination_country": "Germany",
                     "prior_visa": {"prior_schengen_visa": True,
                                    "fingerprints_enrolled_on": _enrolled_months_ago(24)}},
                    today=TODAY)
    assert got["verdict"]["biometrics_required"] is False
    assert got["verdict"]["in_person_required"] == ae.UNKNOWN
    assert got["verdict"]["agent_deliverable_end_to_end"] == ae.UNKNOWN
    assert got["submission_without_appearance_permitted"] == ae.UNKNOWN
    assert any("permit" in q["question"] for q in got["open_questions"])


def test_a_state_that_demands_appearance_makes_it_in_person_even_with_reuse():
    got = ae.triage({"destination_country": "Belgium",
                     "prior_visa": {"prior_schengen_visa": True,
                                    "fingerprints_enrolled_on": _enrolled_months_ago(6)},
                     "route_facts": {"personal_appearance_required": True}},
                    today=TODAY)
    assert got["verdict"]["biometrics_required"] is False
    assert got["verdict"]["in_person_required"] is True
    assert got["verdict"]["summary"] == ae.STRINGS["verdict.appearance_for_lodging"]
    acts = {a["act"]: a for a in got["human_acts"]}
    # No biometric act is invented for a case that does not need one — but the
    # trip to the counter is still on the list, and still the applicant's.
    assert "appear_biometrics" not in acts
    assert acts["appear_to_lodge"]["non_delegable"] is True


def test_a_lapsed_schengen_applicant_gets_a_non_delegable_appearance():
    got = ae.triage({"destination_country": "Italy",
                     "prior_visa": {"prior_schengen_visa": True,
                                    "fingerprints_enrolled_on": _enrolled_months_ago(70)},
                     "route_facts": {"submission_without_appearance_permitted": True}},
                    today=TODAY)
    assert got["verdict"]["in_person_required"] is True
    assert got["verdict"]["agent_deliverable_end_to_end"] is False
    appear = [a for a in got["human_acts"] if a["act"] == "appear_biometrics"][0]
    assert appear["non_delegable"] is True


def test_an_unknown_biometric_status_never_reads_as_agent_deliverable():
    got = ae.triage({"destination_country": "Spain",
                     "prior_visa": {"prior_schengen_visa": True},
                     "route_facts": {"submission_without_appearance_permitted": True}},
                    today=TODAY)
    assert got["verdict"]["in_person_required"] == ae.UNKNOWN
    assert got["verdict"]["agent_deliverable_end_to_end"] == ae.UNKNOWN
    assert "appear_biometrics" in {a["act"] for a in got["human_acts"]}
    assert got["open_questions"]


def test_personal_appearance_required_route_fact_blocks_delegation():
    got = ae.triage({"destination_country": "Belgium",
                     "prior_visa": {"prior_schengen_visa": True,
                                    "fingerprints_enrolled_on": _enrolled_months_ago(6)},
                     "route_facts": {"personal_appearance_required": True}},
                    today=TODAY)
    assert got["verdict"]["agent_deliverable_end_to_end"] is False


def test_an_unsupported_destination_makes_no_claim_at_all():
    got = ae.triage({"destination_country": "Japan", "visa_category": "B1/B2"},
                    today=TODAY)
    assert got["route"] == "unsupported"
    assert got["verdict"]["in_person_required"] == ae.UNKNOWN
    assert got["verdict"]["agent_deliverable_end_to_end"] == ae.UNKNOWN
    assert got["open_questions"]


def test_ireland_and_cyprus_are_not_schengen():
    for country in ("Ireland", "Cyprus"):
        assert ae.triage({"destination_country": country},
                         today=TODAY)["route"] == "unsupported"


def test_every_triage_payload_states_the_lines_it_will_not_cross():
    for case in ({"destination_country": "United States", "visa_category": "H-1B",
                  "post_country": "China"},
                 {"destination_country": "France"},
                 {"destination_country": "Japan"}):
        got = ae.triage(case, today=TODAY)
        joined = " ".join(got["never_automated"]).lower()
        assert "slot search" in joined and "captcha" in joined
        assert "biometric" in joined


def test_triage_of_an_empty_case_does_not_crash_or_conclude():
    got = ae.triage({}, today=TODAY)
    assert got["verdict"]["in_person_required"] == ae.UNKNOWN


# --------------------------------------------------------------------------
# The contract the cockpit router binds to. appt_api resolves triage's
# parameters by NAME out of what the request already holds, so the signature is
# an interface: one required argument called `case`, which may be the
# VisaApplication row the router just loaded.

class _Row:
    """A VisaApplication-shaped stand-in: columns plus the answers JSON."""

    def __init__(self, destination, answers):
        self.id = "case-1"
        self.org_id = "org-1"
        self.destination_country = destination
        self.visa_type = "tourist"
        self.answers = answers


def test_triage_takes_the_case_row_the_router_already_loaded():
    row = _Row("France", {"prior_visa": {"prior_schengen_visa": True,
                                         "fingerprints_enrolled_on":
                                             _enrolled_months_ago(24)},
                          "route_facts": {"submission_without_appearance_permitted": True}})
    got = ae.triage(row, today=TODAY)
    assert got["route"] == "schengen"
    assert got["verdict"]["agent_deliverable_end_to_end"] is True


def test_a_us_tourist_route_supplies_its_own_visa_class_and_says_so():
    """`visa_type` is a route word, not a visa class. Triage may resolve the US
    tourist route to B-1/B-2 — and labels where the category came from — while
    the rule engine on its own still refuses to infer one."""
    row = _Row("United States", {"post_country": "China"})
    row.visa_type = "tourist"
    got = ae.triage(row, today=TODAY)
    assert got["visa_category_source"] == "us_route_default"
    assert got["us"]["visa_category"] == "B-1/B-2"
    assert got["verdict"]["in_person_required"] is True

    strict = ae.us_appointment_needed({"visa_type": "tourist",
                                       "post_country": "China"}, today=TODAY)
    assert strict["needed"] == ae.UNKNOWN


def test_an_explicit_category_is_never_overwritten_by_the_route_word():
    row = _Row("United States", {"visa_category": "H-1B", "post_country": "Canada"})
    row.visa_type = "tourist"
    got = ae.triage(row, today=TODAY)
    assert got["visa_category_source"] == "case"
    assert got["us"]["visa_category"] == "H-1B"


def test_the_case_row_columns_win_over_a_stale_answer():
    row = _Row("United States", {"destination_country": "France",
                                 "visa_category": "H-1B",
                                 "post_country": "China"})
    got = ae.triage(row, today=TODAY)
    assert got["route"] == "us"
    assert got["verdict"]["in_person_required"] is True


def test_only_the_case_argument_is_required():
    import inspect
    params = inspect.signature(ae.triage).parameters
    required = [n for n, p in params.items() if p.default is p.empty]
    assert required == ["case"]
    assert "locale" in params and "today" in params


def test_the_verdict_and_acts_render_in_the_callers_language():
    case = {"destination_country": "United States", "visa_category": "H-1B",
            "post_country": "China"}
    zh = ae.triage(case, locale="zh-CN", today=TODAY)
    assert zh["verdict"]["summary_text"] == ae.STRINGS["verdict.interview_required"]["zh-CN"]
    # The full trilingual dict survives beside it — a client can render any of
    # the three without a second call.
    assert set(zh["verdict"]["summary"]) == {"en", "zh-CN", "zh-Hant"}
    assert all("label_text" in a for a in zh["human_acts"])
    hant = ae.triage(case, locale="zh-TW", today=TODAY)
    assert hant["locale"] == "zh-Hant"
    unknown_lang = ae.triage(case, locale="pt-BR", today=TODAY)
    assert unknown_lang["verdict"]["summary_text"] == \
        ae.STRINGS["verdict.interview_required"]["en"]


def test_every_verdict_keeps_its_shape_across_a_matrix_of_inputs():
    """The interface the cockpit binds to: a tri-state verdict, a reason, a
    citation, and the as_of date — on every path, including the ugly ones."""
    us_cases = [{}, {"visa_category": "H-1B"}, {"visa_category": None},
                {"visa_category": "B1/B2", "post_country": "China"},
                {"visa_category": "Z-9", "post_country": "Canada"},
                {"visa_category": "A-1", "post_country": "China"},
                _b_renewal_facts(), _b_renewal_facts(prior_refusal=True),
                {"visa_category": "B1/B2", "post_country": "Atlantis"}]
    for facts in us_cases:
        got = ae.us_appointment_needed(facts, today=TODAY)
        assert got["needed"] in (True, False, ae.UNKNOWN), facts
        assert got["reason"] and got["citations"] and got["as_of"] == ae.AS_OF

    schengen_cases = [{}, {"prior_schengen_visa": True}, {"first_time_applicant": True},
                      {"fingerprints_enrolled_on": "not a date"},
                      {"fingerprints_enrolled_on": "2021-02-30"},
                      {"prior_visa_issued_on": _enrolled_months_ago(59)},
                      {"age": "not a number"}, {"date_of_birth": ""},
                      {"fingerprints_enrolled_on": _enrolled_months_ago(1)}]
    for facts in schengen_cases:
        got = ae.schengen_biometrics_required(facts, today=TODAY)
        assert got["required"] in (True, False, ae.UNKNOWN), facts
        assert got["within_59_months"] in (True, False, None), facts
        assert got["reason"] and got["citations"] and got["as_of"] == ae.AS_OF
        assert isinstance(got["evidence_needed"], list)

    for facts in ({}, {"nationality": "China"}, {"nationality": ""},
                  {"nationality": "China", "us_visa_validity_years": "ten"}):
        got = ae.evus_status(facts)
        assert got["required"] in (True, False, ae.UNKNOWN), facts
        assert got["url"] and got["note"]


def test_an_unparseable_date_never_becomes_a_verdict():
    for bad in ("not a date", "05/04/2031", "2021-02-30", "??", 0):
        got = ae.schengen_biometrics_required(
            {"fingerprints_enrolled_on": bad, "prior_schengen_visa": True},
            today=TODAY)
        assert got["required"] == ae.UNKNOWN, bad
        assert got["evidence_needed"], bad


# --------------------------------------------------------------------------
# Curated-data discipline: sources, as_of, and the parity contract.

def test_every_curated_rule_names_a_resolvable_source():
    rows = list(ae.WAIVER_RULES) + list(ae.POST_POLICY.values()) \
        + list(ae.VIS_RULES.values()) + [ae.EVUS]
    for row in rows:
        assert row["sources"], row
        for sid in row["sources"]:
            assert sid in ae.SOURCES, sid
            assert ae.SOURCES[sid]["url"].startswith("https://")
            assert ae.SOURCES[sid]["authority"]


def test_citations_carry_the_as_of_date():
    for citation in ae._cite(*ae.SOURCES):
        assert citation["as_of"] == ae.AS_OF
        assert citation["url"] and citation["title"]


def test_curated_data_is_iso_dated_and_reports_its_own_staleness():
    assert ae.freshness(TODAY)["stale"] is False
    much_later = date(TODAY.year + 2, TODAY.month, TODAY.day)
    assert ae.freshness(much_later)["stale"] is True
    data = ae.curated_data(TODAY)
    assert data["as_of"] == ae.AS_OF
    assert data["us"]["waiver_effective_from"] == "2025-10-01"
    assert data["schengen"]["reuse_window_months"] == 59


def test_the_waiver_table_stays_a_closed_short_list():
    covered = {c for rule in ae.WAIVER_RULES for c in rule["categories"]}
    # The 2025 narrowing: diplomatic/official, B renewals, H-2A renewals. If a
    # category is added here it is a policy change and needs a source with it.
    assert "H-1B" not in covered
    assert {"B-1/B-2", "BCC", "H-2A"} <= covered
    assert covered - set(ae.DIPLOMATIC_CATEGORIES) == {"B-1/B-2", "BCC", "H-2A"}


def test_every_user_visible_string_has_all_three_languages():
    for key, entry in ae.STRINGS.items():
        assert set(entry) == {"en", "zh-CN", "zh-Hant"}, key
        assert all(v.strip() for v in entry.values()), key


def test_labels_fall_back_to_english_for_an_unsupported_language():
    assert ae.label("verdict.unknown", "fr") == ae.label("verdict.unknown", "en")
    assert ae.label("no.such.key", "en") == ""


def test_the_module_performs_no_network_io():
    """Eligibility is data plus arithmetic. Nothing here may reach a network —
    least of all a bot-protected scheduling calendar."""
    tree = ast.parse(inspect.getsource(ae))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & {"requests", "httpx", "urllib", "socket", "http",
                            "aiohttp"}), imported
