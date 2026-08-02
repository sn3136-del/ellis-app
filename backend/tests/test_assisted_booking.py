"""Appointments are booked WITH the applicant, in Ellis's secure window.

Every booking system — VFS, TLScontact, and the ministries' own bookers —
keeps slot inventory behind the applicant's own account, so there is no
calendar for Ellis to read. These pin the rules that keep the assisted flow
honest: Ellis never picks a slot, never guesses a booking address, and never
records an appointment the applicant did not confirm.
"""
from __future__ import annotations

import datetime as dt

import pytest

from app import assisted_booking as ab


VERIFIED = {
    "status": "verified",
    "competent_post_name": "UK VAC Beijing",
    "competent_post_kind": "visa_centre",
    "competent_post_url": "https://visa.vfsglobal.com/chn/en/gbr",
}


def _ms(days_ahead: int) -> int:
    when = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days_ahead)
    return int(when.timestamp() * 1000)


# --- which routes need an appointment at all -------------------------------

@pytest.mark.parametrize("outcome", [
    "EMBASSY_OR_CONSULATE_APPLICATION", "AUTHORIZED_VISA_CENTER",
    "APPOINTMENT_REQUIRED"])
def test_in_person_routes_need_an_appointment(outcome):
    assert ab.needs_appointment(outcome) is True


@pytest.mark.parametrize("outcome", ["EVISA", "ELECTRONIC_AUTHORIZATION",
                                     "VISA_EXEMPT", "VISA_ON_ARRIVAL", ""])
def test_routes_ellis_files_itself_need_no_appointment(outcome):
    assert ab.needs_appointment(outcome) is False


# --- the booking address is never guessed ----------------------------------

def test_booking_target_uses_the_verified_post_url():
    t = ab.booking_target({"jurisdiction": VERIFIED})
    assert t["url"] == VERIFIED["competent_post_url"]
    assert t["post_name"] == "UK VAC Beijing"


def test_no_verified_url_refuses_rather_than_guessing():
    """Sending an applicant to a look-alike booking site is how people lose
    money to visa scams — with no verified address Ellis says so."""
    with pytest.raises(ab.BookingUnavailable):
        ab.booking_target({"jurisdiction": {"status": "verified",
                                            "competent_post_name": "Somewhere"}})


def test_non_https_booking_address_is_refused():
    with pytest.raises(ab.BookingUnavailable):
        ab.booking_target({"jurisdiction": {
            "status": "verified", "competent_post_url": "http://insecure.example"}})


def test_unresolved_jurisdiction_has_no_booking_target():
    with pytest.raises(ab.BookingUnavailable):
        ab.booking_target({"jurisdiction": {"status": "manual_selection_required"}})


# --- recording what the APPLICANT booked -----------------------------------

def _case(db, org="orgB"):
    from app import models
    applicant = models.Applicant(org_id=org, user_id="u1", full_name="T A",
                                 email="t@example.com")
    db.add(applicant); db.flush()
    row = models.VisaApplication(
        org_id=org, user_id="u1", applicant_id=applicant.id,
        destination_country="United Kingdom", visa_type="tourist",
        adapter_id="", state="DRAFT", answers={})
    db.add(row); db.commit()
    return row


def test_records_the_appointment_the_applicant_confirmed(db):
    row = _case(db)
    assert ab.summary(db, row) is None
    out = ab.record(db, row, start_utc=_ms(14), location="UK VAC Beijing",
                    confirmation_no="GWF123456")
    assert out["location"] == "UK VAC Beijing"
    assert out["confirmation_no"] == "GWF123456"
    assert out["rescheduled"] == 0
    assert ab.summary(db, row)["start_utc"] == out["start_utc"]


def test_rebooking_counts_as_a_reschedule(db):
    row = _case(db, org="orgB2")
    ab.record(db, row, start_utc=_ms(14), location="Beijing")
    out = ab.record(db, row, start_utc=_ms(21), location="Beijing")
    assert out["rescheduled"] == 1, "a changed date is a reschedule, recorded honestly"


def test_a_past_appointment_is_refused(db):
    """Recording a date already gone would put a false commitment on the case."""
    row = _case(db, org="orgB3")
    with pytest.raises(ValueError):
        ab.record(db, row, start_utc=_ms(-3), location="Beijing")


def test_non_epoch_start_is_refused(db):
    row = _case(db, org="orgB4")
    with pytest.raises(ValueError):
        ab.record(db, row, start_utc=0, location="Beijing")


def test_ellis_has_no_auto_book_path():
    """The module must expose no way to CHOOSE a slot — the applicant picks in
    the live portal and Ellis only records the result."""
    import inspect
    src = inspect.getsource(ab)
    for forbidden in ("def book(", "def auto_book", "def pick_slot",
                      "def reserve", "def select_slot"):
        assert forbidden not in src
