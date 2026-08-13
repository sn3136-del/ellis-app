"""The Schengen 90/180 engine, EES guidance, and ETIAS readiness.

Every expected number below is computed BY HAND in the comment above it, from
the two statutory facts the engine rests on: the reference period is the day
being checked plus the 179 days before it, and both the day of entry and the
day of exit count as days of presence. If the engine and the comment ever
disagree, one of them is wrong and a traveller finds out at a border.

What these pin, beyond the arithmetic:

  * A stay Ellis cannot read never quietly disappears. The total becomes an
    explicit unknown with a floor ("at least N days used"), because a dropped
    stay understates days used, and understating days used is how somebody is
    refused entry.
  * Pre-EES stays ARE counted, and the divergence from the EU's official
    checker is stated in the payload. The law's clock did not start on
    10 April 2026; only the machine did.
  * ETIAS readiness never says a filing is possible. There is no portal, so
    every payload says so, warns about the sites that claim otherwise, and
    exposes no filing path at all.
  * The module touches no network. It counts dates; it does not read the EES.
"""
from __future__ import annotations

import inspect
from datetime import date, timedelta

import pytest

from app import models, schengen_stay as ss


# --------------------------------------------------------------------------
# A fixed "today" so no test's meaning changes when the calendar moves.
TODAY = date(2026, 8, 11)
# The window ending 2026-08-11 opens 179 days earlier:
#   Feb 13-28 (16) + Mar 31 + Apr 30 + May 31 + Jun 30 + Jul 31 + Aug 1-11 (11)
#   = 180 days, so 2026-02-13 is the first day inside it.
WINDOW_START = date(2026, 2, 13)


def _case(db, *, destination="Germany", answers=None, org="org-schengen"):
    applicant = models.Applicant(org_id=org, user_id="u-schengen",
                                 full_name="Ana Silva", email="ana@example.com")
    db.add(applicant)
    db.flush()
    row = models.VisaApplication(
        org_id=org, user_id="u-schengen", applicant_id=applicant.id,
        destination_country=destination, visa_type="tourist",
        answers=dict(answers or {}))
    db.add(row)
    db.commit()
    return row


# ==========================================================================
# Day counting
# ==========================================================================

def test_entry_and_exit_days_both_count():
    """Art. 6: the day of entry is the first day of stay and the day of exit
    the last. A same-day in-and-out is one day, not zero."""
    # 2026-06-01 -> 2026-06-10 inclusive of both ends = 10 days.
    r = ss.days_used([{"entry": "2026-06-01", "exit": "2026-06-10"}], TODAY)
    assert r["status"] == "computed"
    assert r["days_used"] == 10
    assert r["days_remaining"] == 80

    same_day = ss.days_used([{"entry": "2026-06-01", "exit": "2026-06-01"}], TODAY)
    assert same_day["days_used"] == 1


def test_rolling_window_boundary_is_179_days_back():
    """The reference period is the checked day plus the 179 before it. One day
    either side of that edge is the whole difference between a lawful trip and
    an overstay, so it is pinned explicitly."""
    r_in = ss.days_used([{"entry": WINDOW_START.isoformat(),
                          "exit": WINDOW_START.isoformat()}], TODAY)
    assert r_in["window_start"] == "2026-02-13"
    assert r_in["window_end"] == "2026-08-11"
    assert r_in["days_used"] == 1

    outside = (WINDOW_START - timedelta(days=1)).isoformat()
    r_out = ss.days_used([{"entry": outside, "exit": outside}], TODAY)
    assert r_out["days_used"] == 0
    assert r_out["days_remaining"] == 90

    # A stay straddling the edge contributes only its in-window half.
    straddle = ss.days_used(
        [{"entry": "2026-02-10", "exit": "2026-02-16"}], TODAY)
    # Feb 13, 14, 15, 16 are inside; Feb 10-12 are not.
    assert straddle["days_used"] == 4


def test_multi_trip_window_with_overlap_and_partial_stay():
    """Three stays, hand-counted: one half outside the window, two overlapping
    each other. Overlapping days are charged once, not twice."""
    trips = [
        # Jan 20 - Feb 20: only Feb 13-20 is in the window = 8 days.
        {"entry": "2026-01-20", "exit": "2026-02-20"},
        # May 1 - May 15 = 15 days, all inside.
        {"entry": "2026-05-01", "exit": "2026-05-15"},
        # May 10 - May 20 = 11 days, all inside, 6 of them shared with the one
        # above; the union May 1-20 is 20 days.
        {"entry": "2026-05-10", "exit": "2026-05-20"},
    ]
    r = ss.days_used(trips, TODAY)
    assert [t["days_in_window"] for t in r["trips_counted"]] == [8, 15, 11]
    # 8 + 20 = 28, not 8 + 15 + 11 = 34.
    assert r["days_used"] == 28
    assert r["overlapping_days_counted_once"] == 6
    assert r["days_remaining"] == 62


def test_days_remaining_is_the_same_computation():
    trips = [{"entry": "2026-07-01", "exit": "2026-07-20"}]           # 20 days
    assert (ss.days_remaining(trips, TODAY)["days_remaining"]
            == ss.days_used(trips, TODAY)["days_remaining"] == 70)


def test_overstay_is_reported_never_capped_or_softened():
    # Jan 1 - Jul 31 is long, but only Feb 13 - Jul 31 is in the window:
    # Feb 16 + Mar 31 + Apr 30 + May 31 + Jun 30 + Jul 31 = 169 days.
    r = ss.days_used([{"entry": "2026-01-01", "exit": "2026-07-31"}], TODAY)
    assert r["days_used"] == 169
    assert r["overstay"] is True
    assert r["days_over"] == 79
    # Remaining floors at zero — it never goes negative and never wraps.
    assert r["days_remaining"] == 0
    assert "automatically" in r["overstay_note"]


def test_a_stay_in_progress_counts_up_to_the_day_being_checked():
    r = ss.days_used([{"entry": "2026-08-01", "ongoing": True}], TODAY,
                     today=TODAY)
    # Aug 1 - Aug 11 inclusive = 11 days.
    assert r["status"] == "computed"
    assert r["days_used"] == 11
    assert r["trips_counted"][0]["ongoing"] is True


def test_no_recorded_stays_says_so_instead_of_implying_zero():
    """Zero days used is only true if the traveller really made no trips, and
    Ellis cannot see the EES record. The payload has to say whose assumption
    the zero is."""
    r = ss.days_used([], TODAY)
    assert r["days_used"] == 0
    assert r["no_stays_recorded"] is True
    assert "no Schengen stays" in r["basis"]
    assert r["computed_by"] == "ellis"


# ==========================================================================
# Planning mode
# ==========================================================================

def test_max_stay_from_with_a_clean_window_is_ninety_days():
    r = ss.max_stay_from("2026-09-01", [])
    assert r["status"] == "computed"
    assert r["max_days"] == 90
    # The exit day counts, so the 90th day is the last day inside.
    assert r["last_day_inside"] == (date(2026, 9, 1)
                                    + timedelta(days=89)).isoformat()


def test_max_stay_from_subtracts_only_the_days_still_in_the_window():
    """A 30-day June stay is still wholly inside the window for every day of a
    trip starting 2026-09-01 (a 90-day stay from there ends 2026-11-29, whose
    window opens 2026-06-03 — after which June 1-30 would start dropping out,
    but the stay cannot run that long anyway). So 90 - 30 = 60."""
    r = ss.max_stay_from("2026-09-01", [{"entry": "2026-06-01",
                                         "exit": "2026-06-30"}])
    assert r["max_days"] == 60
    assert r["last_day_inside"] == "2026-10-30"
    assert r["must_leave_on_or_before"] == "2026-10-30"


def test_max_stay_from_zero_days_names_the_earliest_date_instead():
    """Already at the limit: the honest answer is 'not that day', plus the day
    it does become possible — never a rounded-up 'a few days'."""
    # May 14 - Aug 11 inclusive = 18 + 30 + 31 + 11 = 90 days, the whole
    # allowance, all of it inside the window ending 2026-08-11.
    trips = [{"entry": "2026-05-14", "exit": "2026-08-11"}]
    used = ss.days_used(trips, TODAY)
    assert used["days_used"] == 90 and used["days_remaining"] == 0

    r = ss.max_stay_from("2026-08-12", trips)
    assert r["max_days"] == 0
    assert r["last_day_inside"] == ""
    # A day becomes free once May 14 drops out of the window: the window for
    # day d opens at d-179, so d = 2026-05-14 + 180 = 2026-11-10.
    assert r["earliest_entry_date"] == "2026-11-10"


def test_planning_refuses_a_trip_that_would_overstay():
    # 60 days already used inside the window (Jun 1 - Jul 30).
    trips = [{"entry": "2026-06-01", "exit": "2026-07-30"}]
    # Sep 1 - Nov 5 is 66 days; those 60 stay in the window throughout, so the
    # traveller crosses 90 during it.
    r = ss.plan_trip(trips, "2026-09-01", "2026-11-05")
    assert r["status"] == "computed"
    assert r["allowed"] is False
    assert r["verdict"] == "would_overstay"
    assert r["requested_days"] == 66
    # 90 - 60 = 30 days available from that arrival date.
    assert r["max_days_available"] == 30
    assert r["suggested_exit_date"] == "2026-09-30"
    # Day 31 of the trip, 2026-10-01, is the first day over, by one day.
    assert r["first_day_over"] == "2026-10-01"
    assert r["days_over"] == 1
    assert "10/01/2026" in r["reason"]


def test_planning_accepts_a_trip_that_fits_exactly():
    trips = [{"entry": "2026-06-01", "exit": "2026-07-30"}]           # 60 days
    r = ss.plan_trip(trips, "2026-09-01", "2026-09-30")               # 30 days
    assert r["allowed"] is True
    assert r["verdict"] == "fits"
    assert r["requested_days"] == 30
    assert r["peak_days_used_during_trip"] == 90
    assert r["days_remaining_after"] == 0

    # One day longer is refused. The border counts by machine now; so does this.
    over = ss.plan_trip(trips, "2026-09-01", "2026-10-01")
    assert over["allowed"] is False
    assert over["days_over"] == 1


def test_planning_counts_only_the_part_of_an_old_stay_still_in_the_window():
    """The trap the engine exists for: an old stay is not 'used up' or 'gone',
    it decays out of the window day by day."""
    # A 40-day stay in February/March. For a trip starting 2026-09-01 the
    # window opens 2026-03-06, so only 2026-03-06 to 2026-03-11 (6 days) of it
    # still counts on the first day of the new trip, and it drops out entirely
    # six days later.
    trips = [{"entry": "2026-01-31", "exit": "2026-03-11"}]
    assert ss.days_used(trips, "2026-09-01")["days_used"] == 6
    r = ss.max_stay_from("2026-09-01", trips)
    assert r["max_days"] == 90        # the old stay is gone long before day 90


def test_can_take_trip_is_the_same_planning_answer():
    assert ss.can_take_trip is ss.plan_trip
    r = ss.can_take_trip([], trip={"entry": "2026-09-01", "exit": "2026-09-10"})
    assert r["allowed"] is True and r["requested_days"] == 10


# ==========================================================================
# The pre-EES divergence — the product value
# ==========================================================================

def test_pre_ees_stays_are_counted_and_the_divergence_is_stated():
    """The EU checker ignores any stay that began before 2026-04-10. Ellis does
    not, because the law does not — and it says out loud that the two answers
    will differ, and by how much."""
    trips = [
        {"entry": "2026-03-01", "exit": "2026-03-20"},   # 20 days, pre-EES
        {"entry": "2026-06-01", "exit": "2026-06-10"},   # 10 days, post-EES
    ]
    r = ss.days_used(trips, TODAY)
    assert r["days_used"] == 30                        # Ellis counts both
    assert r["days_before_ees_counted"] == 20
    assert r["trips_counted"][0]["began_before_ees"] is True
    assert r["trips_counted"][1]["began_before_ees"] is False

    tool = r["official_tool"]
    assert tool["differs_from_ellis"] is True
    assert tool["days_ellis_counted_that_the_tool_cannot_see"] == 20
    assert "2026-04-10" in tool["divergence"]
    assert "more days available" in tool["divergence"]
    # The official tool is linked for the traveller and never read by Ellis.
    assert tool["url"].startswith("https://travel-europe.europa.eu/")
    assert tool["ellis_reads_it"] is False


def test_a_stay_straddling_the_ees_start_is_treated_as_pre_ees():
    """The documented rule is about when the stay BEGAN, whether or not it ran
    past 10 April 2026."""
    r = ss.days_used([{"entry": "2026-04-05", "exit": "2026-04-20"}], TODAY)
    assert r["days_used"] == 16
    assert r["trips_counted"][0]["began_before_ees"] is True
    assert r["days_before_ees_counted"] == 16
    assert r["official_tool"]["differs_from_ellis"] is True


def test_no_divergence_claimed_when_there_is_none():
    r = ss.days_used([{"entry": "2026-06-01", "exit": "2026-06-10"}], TODAY)
    assert r["days_before_ees_counted"] == 0
    assert r["official_tool"]["differs_from_ellis"] is False
    assert r["official_tool"]["divergence"] == ""


def test_the_second_blind_spot_expires_on_its_own_date():
    """The single/double-entry caveat is live until 6 October 2026 included and
    then it is simply no longer true. A caveat that never expires is noise."""
    live = ss.official_checker(on_date=date(2026, 8, 11))["blind_spots"]
    later = ss.official_checker(on_date=date(2026, 10, 7))["blind_spots"]
    by_key = {b["key"]: b for b in live}
    assert by_key["single_double_entry_visa"]["active"] is True
    assert by_key["single_double_entry_visa"]["until"] == "2026-10-06"
    assert by_key["pre_ees_stays"]["active"] is True
    assert {b["key"]: b["active"] for b in later}["single_double_entry_visa"] is False
    # The pre-EES blind spot has no expiry — those stays never enter the record.
    assert {b["key"]: b["active"] for b in later}["pre_ees_stays"] is True


# ==========================================================================
# Unknowns: never guessed, never silently dropped
# ==========================================================================

def test_an_unreadable_stay_makes_the_total_unknown_with_a_floor():
    trips = [
        {"entry": "2026-06-01", "exit": "2026-06-10"},        # 10 days, fine
        {"entry": "2026-07-01"},                              # no exit
    ]
    r = ss.days_used(trips, TODAY)
    assert r["status"] == "unknown"
    assert r["days_used"] is None and r["days_remaining"] is None
    # What is still true is published as a bound, not as the answer.
    assert r["at_least_days_used"] == 10
    assert r["at_most_days_remaining"] == 80
    assert len(r["trips_unresolved"]) == 1
    assert r["trips_unresolved"][0]["reason"] == "This stay has no exit date."
    assert "YYYY-MM-DD" in r["trips_unresolved"][0]["how_to_resolve"]
    # And the readable stay is still listed, so nothing vanished.
    assert len(r["trips_counted"]) == 1


@pytest.mark.parametrize("trip, fragment", [
    ({"entry": "05/04/2026", "exit": "2026-05-10"}, "entry date"),
    ({"entry": "2026-05-01", "exit": "not a date"}, "exit date"),
    ({"entry": "2026-05-10", "exit": "2026-05-01"}, "before the entry date"),
    ({"exit": "2026-05-10"}, "no entry date"),
])
def test_each_kind_of_unreadable_stay_is_named_not_guessed(trip, fragment):
    """05/04/2026 is the important one: day/month or month/day changes the
    answer, so app/dates.py refuses it and so does this."""
    r = ss.days_used([trip], TODAY)
    assert r["status"] == "unknown"
    assert fragment in r["trips_unresolved"][0]["reason"]


def test_an_unreadable_date_to_check_is_refused():
    r = ss.days_used([{"entry": "2026-06-01", "exit": "2026-06-10"}], "whenever")
    assert r["status"] == "unknown"
    assert r["days_used"] is None
    assert "cannot read the date" in r["reason"]
    # No window is invented for a date that was never read.
    assert "window_start" not in r


def test_planning_refuses_to_answer_around_a_stay_it_cannot_read():
    """Half an answer is the dangerous one here: the unknown stay could be
    exactly the one that uses up the window."""
    trips = [{"entry": "2026-06-01", "exit": "2026-06-10"},
             {"entry": "2026-07-01", "ongoing": True}]
    plan = ss.plan_trip(trips, "2026-09-01", "2026-09-20")
    assert plan["status"] == "unknown"
    assert plan["allowed"] is None
    assert plan["verdict"] == "unknown"
    assert plan["how_to_resolve"]

    ceiling = ss.max_stay_from("2026-09-01", trips)
    assert ceiling["status"] == "unknown"
    assert ceiling["max_days"] is None


def test_planning_dates_that_cannot_be_read_are_refused():
    backwards = ss.plan_trip([], "2026-09-20", "2026-09-01")
    assert backwards["status"] == "unknown" and backwards["allowed"] is None
    unreadable = ss.plan_trip([], "sometime in autumn", "2026-09-20")
    assert unreadable["status"] == "unknown"
    assert "arrival" in unreadable["reason"]
    assert ss.max_stay_from("")["status"] == "unknown"


def test_a_future_check_date_with_an_open_stay_is_unknown():
    """Asking 'where will I stand in December' while a stay has no end date is
    not answerable, and a number would be a guess about the future."""
    r = ss.days_used([{"entry": "2026-08-01", "ongoing": True}],
                     "2026-12-01", today=TODAY)
    assert r["status"] == "unknown"
    assert r["trips_unresolved"][0]["ongoing"] is True


# ==========================================================================
# EES guidance
# ==========================================================================

def test_ees_guidance_is_curated_sourced_and_links_the_official_tool():
    g = ss.ees_guidance("schengen")
    assert g["status"] == "curated"
    assert g["applies"] is True
    assert g["operational_since"] == "2026-04-10"
    assert g["country_count"] == 29
    assert "DEU" in g["countries"] and "USA" not in g["countries"]
    assert g["as_of"] == ss.AS_OF

    topics = {c["key"]: c for c in g["what_changes"]}
    assert set(topics) == {"first_entry_biometrics", "no_more_stamps",
                           "automatic_overstay", "who_is_in_scope"}
    assert "fingerprints" in topics["first_entry_biometrics"]["detail"]
    assert "stamp" in topics["no_more_stamps"]["detail"]
    assert "automatic" in topics["automatic_overstay"]["title"].lower()
    # Every curated claim carries the page it came from.
    for item in g["what_changes"]:
        assert item["source"].startswith("https://")
    assert all(s.startswith("https://") for s in g["sources"])

    assert g["official_tool"]["url"] == ss.OFFICIAL_CHECKER_URL
    assert g["official_tool"]["ellis_reads_it"] is False
    assert "does not read the EES record" in g["ellis_role"]


def test_ees_guidance_resolves_a_country_or_a_case():
    for route in ("Germany", "DEU", "DE", {"destination": "France"}):
        assert ss.ees_guidance(route)["status"] == "curated"


def test_ees_guidance_on_a_case(db):
    case = _case(db, destination="Italy")
    assert ss.ees_guidance(None, case=case)["status"] == "curated"
    other = _case(db, destination="Japan")
    assert ss.ees_guidance(None, case=other)["status"] == "not_applicable"


def test_ees_guidance_is_explicit_when_it_does_not_apply_or_is_unknown():
    away = ss.ees_guidance("Japan")
    assert away["status"] == "not_applicable"
    assert away["applies"] is False
    assert "not to a country operating" in away["reason"]

    nowhere = ss.ees_guidance(None)
    assert nowhere["status"] == "unknown"
    assert nowhere["applies"] is None
    assert nowhere["how_to_resolve"]


# ==========================================================================
# ETIAS readiness
# ==========================================================================

def _etias(**answers):
    return ss.etias_readiness(answers=answers, route="Germany", today=TODAY)


def test_etias_readiness_never_claims_a_filing_is_possible():
    r = _etias(nationality="USA", birth_date="1990-01-01")
    assert r["required"] != True                                    # noqa: E712
    assert r["required"] == "not_yet"
    assert r["in_force"] is False
    assert ss.etias_in_force(TODAY) is False
    assert r["portal_status"] == "not_launched"
    assert r["filing_available"] is False
    assert r["ellis_can_file"]["today"] is False
    assert "cannot" in r["how_to_apply_today"]
    # The regulation permits an authorised intermediary once it opens, and that
    # is stated as a future capability, not an offer.
    assert r["ellis_can_file"]["when_it_opens"] is True
    assert "Art. 15" in r["ellis_can_file"]["basis"]
    # No start date is asserted anywhere.
    assert r["expected_start"]["status"] == "unknown"
    assert "2027" in r["expected_start"]["detail"]


def test_etias_readiness_warns_about_the_sites_taking_applications_today():
    r = _etias(nationality="USA", birth_date="1990-01-01")
    warning = r["scam_warning"]
    assert "not open" in warning
    assert "europa.eu" in warning
    assert r["official_domain"] == "europa.eu"
    # The only site named is the EU's own information page.
    assert r["official_site"].startswith("https://travel-europe.europa.eu/")
    assert all("europa.eu" in s or "eur-lex.europa.eu" in s
               for s in r["sources"])


def test_etias_fee_and_the_age_waiver_which_is_only_a_fee_waiver():
    adult = _etias(nationality="USA", birth_date="1990-01-01")
    assert adult["fee_eur"] == 20.0
    assert adult["exempt_by_age"] is False

    child = _etias(nationality="USA", birth_date="2015-05-01")      # 11
    assert child["exempt_by_age"] is True
    assert child["fee_eur"] == 0.0
    assert child["standard_fee_eur"] == 20.0
    # The application is still required — the waiver is the fee only.
    assert "still required" in child["fee_basis"]
    assert "FEE only" in child["exempt_by_age_note"]
    assert "Art. 18" in child["exempt_by_age_note"]

    elder = _etias(nationality="USA", birth_date="1950-01-01")      # 76
    assert elder["exempt_by_age"] is True and elder["fee_eur"] == 0.0

    # 70 exactly is exempt; 69 is not. The boundary is the whole rule.
    seventy = _etias(nationality="USA", birth_date=(TODAY.replace(
        year=TODAY.year - 70)).isoformat())
    sixty_nine = _etias(nationality="USA", birth_date=(TODAY.replace(
        year=TODAY.year - 69)).isoformat())
    assert seventy["exempt_by_age"] is True
    assert sixty_nine["exempt_by_age"] is False
    assert seventy["age_checked"] == 70 and sixty_nine["age_checked"] == 69


def test_etias_age_waiver_is_unknown_without_a_date_of_birth():
    r = _etias(nationality="USA")
    assert r["exempt_by_age"] == "unknown"
    assert r["age_checked"] is None
    assert r["fee_eur"] == 20.0                    # the standard fee, labelled
    assert "cannot say whether the age waiver applies" in r["fee_basis"]
    assert r["fee_how_to_resolve"]


def test_etias_validity_is_three_years_or_the_passport():
    r = _etias(nationality="USA", birth_date="1990-01-01")
    assert r["validity_years"] == 3
    assert "passport" in r["validity_note"]


def test_etias_does_not_apply_to_eu_nationals_or_non_etias_destinations():
    eu = ss.etias_readiness(answers={"nationality": "FRA",
                                     "birth_date": "1990-01-01"},
                            route="Germany", today=TODAY)
    assert eu["required"] is False
    assert eu["applies_when_launched"] is False
    assert "EU/EEA" in eu["reason"]

    away = ss.etias_readiness(answers={"nationality": "USA"}, route="Japan",
                              today=TODAY)
    assert away["required"] is False
    assert "not to a country in the ETIAS area" in away["reason"]


def test_etias_scope_is_unknown_when_ellis_has_not_confirmed_it():
    unknown_dest = ss.etias_readiness(answers={"nationality": "USA"},
                                      today=TODAY)
    assert unknown_dest["required"] == "unknown"
    assert unknown_dest["applies_when_launched"] == "unknown"
    assert unknown_dest["how_to_resolve"]

    # Destination known, visa-exempt status not: ETIAS still is not required
    # today (nothing is), but whether it ever applies stays unknown.
    unconfirmed = _etias(nationality="IND", birth_date="1990-01-01")
    assert unconfirmed["required"] == "not_yet"
    assert unconfirmed["applies_when_launched"] == "unknown"
    assert "has not confirmed" in unconfirmed["reason"]

    # A caller that HAS confirmed the route can say so, and gets a straight
    # answer without this module inventing a visa policy.
    visa_national = ss.etias_readiness(
        answers={"nationality": "IND", "birth_date": "1990-01-01"},
        route="Germany", today=TODAY, visa_exempt=False)
    assert visa_national["required"] is False
    assert visa_national["applies_when_launched"] is False
    exempt = ss.etias_readiness(
        answers={"nationality": "USA", "birth_date": "1990-01-01"},
        route="Germany", today=TODAY, visa_exempt=True)
    assert exempt["required"] == "not_yet"
    assert exempt["applies_when_launched"] is True


def test_etias_passport_validity_is_asymmetric_about_the_unknown():
    """A passport that already fails cannot pass for a later departure, so
    False is safe. A passport that passes today might still fail for a
    departure Ellis does not know about, so that is 'unknown' — never a
    reassuring True."""
    ok = _etias(nationality="USA", birth_date="1990-01-01",
                passport_issue_date="2022-01-01",
                passport_expiry_date="2032-01-01",
                departure_date="2026-09-20")
    assert ok["passport_validity_ok"] is True

    expired_soon = _etias(nationality="USA", passport_expiry_date="2026-10-01",
                          departure_date="2026-09-20")
    # Three months after 2026-09-20 is 2026-12-20; the passport dies first.
    assert expired_soon["passport_validity_ok"] is False
    assert "Renew" in expired_soon["passport_validity"]["how_to_resolve"]

    no_departure = _etias(nationality="USA", passport_expiry_date="2032-01-01")
    assert no_departure["passport_validity_ok"] == "unknown"
    assert "day you leave" in no_departure["passport_validity"]["reason"]

    no_passport = _etias(nationality="USA")
    assert no_passport["passport_validity_ok"] == "unknown"
    assert "expiry date" in no_passport["passport_validity"]["reason"]

    too_old = _etias(nationality="USA", passport_issue_date="2015-01-01",
                     passport_expiry_date="2032-01-01",
                     departure_date="2026-09-20")
    assert too_old["passport_validity_ok"] is False
    assert "ten years" in too_old["passport_validity"]["reason"]


def test_etias_readiness_on_a_case(db):
    case = _case(db, destination="Spain",
                 answers={"nationality": "USA", "birth_date": "1990-01-01",
                          "passport_expiry_date": "2032-01-01"})
    r = ss.etias_readiness(case, today=TODAY)
    assert r["required"] == "not_yet"
    assert r["nationality"] == "USA"
    assert r["filing_available"] is False


# ==========================================================================
# The composed screen, and the boundary the module never crosses
# ==========================================================================

def test_stay_report_carries_the_count_with_its_caveats(db):
    case = _case(db, destination="Germany")
    report = ss.stay_report([{"entry": "2026-03-01", "exit": "2026-03-20"}],
                            on_date=TODAY, case=case,
                            planned={"entry": "2026-09-01",
                                     "exit": "2026-09-20"})
    assert report["stay"]["days_used"] == 20
    assert report["ees"]["status"] == "curated"
    assert report["plan"]["allowed"] is True
    # The divergence travels with the number, so a screen cannot show one
    # without the other.
    assert report["stay"]["official_tool"]["differs_from_ellis"] is True


def test_the_module_performs_no_network_io():
    """Ellis computes; it does not query the EU checker, the EES or any border
    system. Enforced by test as well as by review."""
    source = inspect.getsource(ss)
    for forbidden in ("import httpx", "import requests", "urlopen",
                      "aiohttp", "socket.", "webdriver", "playwright"):
        assert forbidden not in source, forbidden
    for payload in (ss.days_used([], TODAY), ss.ees_guidance("schengen"),
                    ss.max_stay_from("2026-09-01", [])):
        assert "reading the EES record" in payload["ellis_never"]


def test_the_statutory_numbers_are_not_tunable():
    assert ss.ALLOWANCE_DAYS == 90
    assert ss.WINDOW_DAYS == 180
    assert ss.EES_START == date(2026, 4, 10)
