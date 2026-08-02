"""Booking a consular appointment WITH the applicant, in Ellis's secure window.

Every booking system probed — VFS, TLScontact, and the ministries' own bookers
(Italy's Prenot@Mi, Poland's e-Konsulat, the US AIS) — keeps its slot inventory
behind the applicant's own account. That is not bot defence: availability is
personal to an application, so there is no public calendar to read for anyone.

So Ellis does not scrape a calendar and does not hold anyone's portal password.
It opens the official booking site inside its own secure window (the same
Browserbase live view already used for CAPTCHAs, OTPs and payment), the
applicant signs in and picks their slot themselves, and Ellis records what they
booked so the date travels into their appointment packet and reminders.

The honesty rules this module enforces:

  1. ELLIS NEVER PICKS THE SLOT. There is no auto-book path here at all. The
     applicant chooses in the live portal; Ellis only records the result.
  2. NOTHING IS RECORDED THAT THE APPLICANT DID NOT CONFIRM. A booking is
     written only from their explicit confirmation, never inferred from a page
     Ellis happened to be looking at.
  3. THE DESTINATION IS AN OFFICIAL HOST. The window opens only on the booking
     URL that verified route data supplies — never a URL guessed from a name,
     because sending an applicant to a look-alike booking site is how people
     lose money to visa scams.
"""
from __future__ import annotations

from datetime import datetime, timezone

# Routes whose appointment is made in person and therefore needs a booking.
NEEDS_APPOINTMENT = {
    "EMBASSY_OR_CONSULATE_APPLICATION",
    "AUTHORIZED_VISA_CENTER",
    "APPOINTMENT_REQUIRED",
}


class BookingUnavailable(Exception):
    """No official booking URL is known for this route — Ellis says so rather
    than sending the applicant somewhere it guessed."""


def needs_appointment(route_outcome: str) -> bool:
    return (route_outcome or "") in NEEDS_APPOINTMENT


def booking_target(route: dict) -> dict:
    """Where the applicant must book, from VERIFIED route data only.

    Prefers the competent post's own booking URL; falls back to the official
    channel Ellis has on record for the route. Raises when neither exists —
    a guessed booking host is worse than no link.
    """
    j = (route or {}).get("jurisdiction") or {}
    # The resolver reports a settled jurisdiction as status 'verified' and
    # carries the post's own URL in competent_post_url.
    url = str(j.get("competent_post_url") or j.get("booking_url") or "").strip()
    post = str(j.get("competent_post_name") or "").strip()
    if not url:
        channel = (route or {}).get("official_channel") or {}
        url = str(channel.get("booking_url") or channel.get("url") or "").strip()
        post = post or str(channel.get("operator") or "").strip()
    if not url:
        raise BookingUnavailable(
            "Ellis has no verified booking address for this post. Confirm the "
            "competent consulate or visa centre first — Ellis will not guess a "
            "booking website.")
    if not url.lower().startswith("https://"):
        raise BookingUnavailable(f"refusing a non-HTTPS booking address: {url[:60]}")
    return {"url": url, "post_name": post,
            "hostname": url.split("/")[2] if "/" in url[8:] + "/" else ""}


def record(db, app_row, *, start_utc: int, location: str,
           confirmation_no: str = "", actor: str = "") -> dict:
    """Record the appointment the APPLICANT booked in the secure window.

    start_utc is epoch milliseconds. Nothing here contacts the portal: this is
    the applicant telling Ellis what they just did, so their packet, reminders
    and case timeline reflect the real date.
    """
    from . import audit, models
    if not isinstance(start_utc, int) or start_utc <= 0:
        raise ValueError("start_utc must be epoch milliseconds")
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    if start_utc < now_ms:
        raise ValueError("an appointment in the past cannot be recorded")
    from sqlalchemy import select
    existing = db.execute(select(models.Appointment).where(
        models.Appointment.application_id == app_row.id)).scalars().first()
    if existing is None:
        existing = models.Appointment(
            application_id=app_row.id, slot_id="", location_id=location[:80],
            start_utc=start_utc, confirmation_no=confirmation_no[:120])
        db.add(existing)
    else:
        # Rebooking: keep the count honest so the case history shows changes.
        if existing.start_utc != start_utc:
            existing.reschedule_count = (existing.reschedule_count or 0) + 1
        existing.start_utc = start_utc
        existing.location_id = location[:80]
        existing.confirmation_no = confirmation_no[:120]
    db.commit()
    audit.record(db, org_id=app_row.org_id, application_id=app_row.id,
                 action="appointment_recorded_by_applicant",
                 detail={"location": location[:80],
                         "reschedule_count": existing.reschedule_count or 0},
                 actor=actor or "applicant")
    return summary(db, app_row)


def summary(db, app_row) -> dict | None:
    """The recorded appointment, for the case screen and the packet."""
    from sqlalchemy import select
    from . import models
    row = db.execute(select(models.Appointment).where(
        models.Appointment.application_id == app_row.id)).scalars().first()
    if row is None:
        return None
    when = datetime.fromtimestamp(row.start_utc / 1000, timezone.utc)
    return {"start_utc": row.start_utc,
            "when_utc": when.strftime("%Y-%m-%d %H:%M UTC"),
            "location": row.location_id,
            "confirmation_no": row.confirmation_no,
            "rescheduled": int(row.reschedule_count or 0)}
