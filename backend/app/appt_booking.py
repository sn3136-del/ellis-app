"""Agent-channel appointment booking — the compliant version of "book it for
me on Trip.com".

The legal ground (both researched, both cited on every payload):

* US: the official scheduling FAQ permits a company to schedule for its users
  PROVIDED it uses the official website — an agency permission, not a bot
  permission. The same platform treats automated slot search as abuse, and the
  penalty falls on the TRAVELER (appointments cancelled, visas revoked;
  roughly 2,000 cancellations in India in 2025).
* Schengen: Visa Code Art. 45 — "a visa application may be submitted by the
  applicant, an accredited commercial intermediary or a professional".

So the pipeline is built around a NAMED HUMAN in their own session on the
official site, with Ellis doing everything around that person:

  requested        the applicant asked, inside Trip.com (posts + date windows)
  slots_offered    an operator READ the official calendar in their own session
                   and recorded what they saw — stamped who and when
  slot_picked      the applicant chose one of those, inside Trip.com
  booked           the operator executed the booking and recorded EVIDENCE:
                   a confirmation number + the confirmation document on file
  failed           honest terminal state, reason required
  cancelled        the applicant withdrew

Hard rules, enforced here and pinned by tests:
* This module performs NO network I/O. It cannot poll, scrape, hold or book a
  slot — there is nothing to switch on. The booking site is worked by a
  person, in their own browser, in their own account.
* An offered slot is a person's own reading of the official calendar and says
  so (recorded_by / recorded_at on every slot). Never presented as live
  inventory.
* `booked` exists only behind evidence. No confirmation number + stored
  confirmation document on the same case -> no booked, ever.
* Operator acts (offer / booked / failed) require the admin role; applicant
  acts (create / pick / cancel) require case ownership. Neither can do the
  other's part.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import models

ROUTES = ("us_b1b2", "schengen")

ACTIVE_STATUSES = ("requested", "slots_offered", "slot_picked")
TERMINAL_STATUSES = ("booked", "failed", "cancelled")

# The one doc_type `booked` accepts as evidence — written only by the booking
# desk's own /evidence endpoint (magic-byte checked) or the agent's captured
# confirmation. An applicant's passport scan is never a booking confirmation.
EVIDENCE_DOC_TYPE = "booking_confirmation"

# Applicant-controlled inputs are bounded: these rows fan out into every
# operator's queue, so an unbounded post list or window set is a stored-payload
# amplifier. Caps are generous for real use, hostile to abuse.
MAX_POSTS = 10
MAX_POST_LEN = 80
MAX_WINDOWS = 6

# Cited on every payload, beside the acts. Anchors: the two texts the user of
# this feature will be asked about, stated with their limits.
LEGAL_BASIS = {
    "us_b1b2": {
        "basis": ("Official US visa scheduling FAQ: a company may schedule "
                  "for its users, provided it uses the services on the "
                  "official website."),
        "limit": ("Agency is permitted; automation is not. Automated slot "
                  "search is treated as abuse and the penalty falls on the "
                  "traveler, so every slot here was read, and every booking "
                  "made, by a named person in their own session."),
    },
    "schengen": {
        "basis": ("EU Visa Code Art. 45: an application may be submitted by "
                  "the applicant, an accredited commercial intermediary or a "
                  "professional."),
        "limit": ("Accreditation is per member state and per ESP contract; "
                  "fingerprints and the VIS remain the applicant's own acts "
                  "(Arts. 43/45)."),
    },
}

# The acts that stay a person's, named per seat. `act` sentences render
# verbatim in the cockpit (AppointmentCockpit renders `act` when the key is
# not in the UI vocabulary).
def human_acts(route: str) -> list[dict]:
    operator = ("a Trip.com operator" if route == "us_b1b2"
                else "an accredited operator")
    return [
        {"key": "login",
         "act": "Sign in to the official scheduling site",
         "who": f"{operator}, in their own account",
         "why": ("the official FAQ permits agency through the official "
                 "website; the account and session are the operator's own"),
         "non_delegable": True,
         "ellis_does": "prepares the complete work order beside the window"},
        {"act": "Read the calendar and record what it offers",
         "who": operator,
         "why": ("availability is visible only to a signed-in human; "
                 "automated reading is treated as abuse and endangers the "
                 "traveler's own appointment"),
         "non_delegable": True,
         "ellis_does": ("relays the recorded slots to the applicant inside "
                        "Trip.com, stamped with who saw them and when")},
        {"act": "Pick the appointment date",
         "who": "the applicant, inside Trip.com",
         "why": "the appointment is the applicant's own",
         "ellis_does": "shows the recorded slots and stores the choice"},
        {"key": "captcha",
         "act": "Clear any human check the site presents",
         "who": operator, "non_delegable": True,
         "why": "a human check exists to be cleared by a human",
         "ellis_does": "waits"},
        {"act": "Execute the booking and keep the confirmation",
         "who": operator, "non_delegable": True,
         "why": "the booking click is the agency act itself",
         "ellis_does": ("records the confirmation number and the "
                        "confirmation document as evidence — 'booked' never "
                        "appears without them")},
    ]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt) -> str:
    return dt.isoformat() if dt else ""


def _iso_date_or_empty(v) -> str:
    """A YYYY-MM-DD string as itself, or empty. Never stores a free-text value
    that would travel into the operator's queue as a fake date."""
    s = str(v or "").strip()
    if not s:
        return ""
    try:
        from datetime import date
        date.fromisoformat(s[:10])
        return s[:10]
    except ValueError:
        return ""


class BookingError(Exception):
    """A refused transition, with the honest reason and an HTTP status."""

    def __init__(self, status_code: int, reason: str):
        super().__init__(reason)
        self.status_code = status_code
        self.reason = reason


def active_request(db, application_id: str):
    """The case's one active request, or None. One at a time, by design: two
    parallel booking orders for the same traveller is how double-booking (and
    cancelled appointments) happen."""
    rows = db.execute(
        select(models.AppointmentBookingRequest)
        .where(models.AppointmentBookingRequest.application_id == application_id)
        .order_by(models.AppointmentBookingRequest.created_at.desc())
    ).scalars().all()
    for row in rows:
        if row.status in ACTIVE_STATUSES:
            return row
    return None


def create_request(db, case, *, route: str, posts: list, date_windows: list,
                   note: str, actor: str) -> models.AppointmentBookingRequest:
    if route not in ROUTES:
        raise BookingError(422, f"unknown route '{route}' — one of {ROUTES}")
    if active_request(db, case.id) is not None:
        raise BookingError(409, "this case already has an active booking "
                                "request — cancel it before opening another")
    clean_posts = [str(p).strip()[:MAX_POST_LEN]
                   for p in (posts or []) if str(p).strip()][:MAX_POSTS]
    if not clean_posts:
        raise BookingError(422, "at least one preferred post/city is required")
    windows = []
    for w in (date_windows or [])[:MAX_WINDOWS]:
        if not isinstance(w, dict):
            continue
        # Each bound is an ISO date or empty — a malformed value is dropped, not
        # stored, so the operator's queue never carries junk from the wire.
        frm = _iso_date_or_empty(w.get("from"))
        to = _iso_date_or_empty(w.get("to"))
        if frm or to:
            windows.append({"from": frm, "to": to})
    row = models.AppointmentBookingRequest(
        org_id=case.org_id, application_id=case.id, route=route,
        posts=clean_posts, date_windows=windows,
        note=str(note or "")[:500], requested_by=actor)
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        # The partial unique index caught a concurrent create that raced past
        # the check above — same honest answer, now impossible to bypass.
        db.rollback()
        raise BookingError(409, "this case already has an active booking "
                                "request — cancel it before opening another")
    return row


def offer_slots(db, row, *, slots: list, actor: str,
                source: str = "operator_manual"):
    """Records what was SEEN in the official calendar, in an authorized
    session. `source` says HOW it was read: 'ellis_agent' (Ellis's browser
    agent read the calendar) or 'operator_manual' (a person typed what they
    saw). Re-offering supersedes the old list (the calendar moved on) and
    clears any pick made against it."""
    if row.status not in ("requested", "slots_offered", "slot_picked"):
        raise BookingError(409, f"cannot offer slots on a {row.status} request")
    recorded, at = [], _iso(_now())
    for s in (slots or []):
        if not isinstance(s, dict):
            continue
        post = str(s.get("post") or "").strip()
        when = str(s.get("when") or "").strip()
        if not post or not when:
            raise BookingError(422, "every slot needs a post and a when")
        recorded.append({"post": post, "when": when,
                         "label": str(s.get("label") or "").strip(),
                         "recorded_by": actor, "recorded_at": at,
                         "source": source})
    if not recorded:
        raise BookingError(422, "no slots given — to report none available, "
                                "mark the request failed with the reason")
    row.offered_slots = recorded
    row.picked_slot = {}
    row.picked_at = None
    row.status = "slots_offered"


def pick_slot(db, row, *, index: int, actor: str,
              expect_post: str = "", expect_when: str = ""):
    """Bind the applicant's consent to the slot they actually SAW, not to a
    position. When the caller echoes the slot's post/when (the UI does), a
    mismatch means the operator re-offered between render and pick — the pick
    is refused with an honest 'the dates changed' so consent never lands on a
    slot the applicant never saw."""
    if row.status != "slots_offered":
        raise BookingError(409, f"cannot pick on a {row.status} request")
    slots = list(row.offered_slots or [])
    if not isinstance(index, int) or index < 0 or index >= len(slots):
        raise BookingError(422, f"slot index {index} is not one of the "
                                f"{len(slots)} offered")
    chosen = slots[index]
    if expect_post or expect_when:
        if (expect_post and chosen.get("post") != expect_post) or \
                (expect_when and chosen.get("when") != expect_when):
            raise BookingError(409, "the offered dates changed before your "
                                    "pick — please look again and pick from "
                                    "the current list")
    row.picked_slot = dict(chosen, picked_by=actor)
    row.picked_at = _now()
    row.status = "slot_picked"


def record_booked(db, row, *, confirmation_number: str,
                  evidence_document_id: str, note: str, actor: str):
    """`booked` only behind evidence: number + confirmation document stored on
    THIS case. Anything less is refused, not softened."""
    if row.status != "slot_picked":
        raise BookingError(409, f"cannot mark a {row.status} request booked — "
                                "the applicant has not picked a slot")
    number = str(confirmation_number or "").strip()
    if not number:
        raise BookingError(422, "a confirmation number is required")
    doc_id = str(evidence_document_id or "").strip()
    if not doc_id:
        raise BookingError(422, "the confirmation document is required — "
                                "upload it to the case first")
    doc = db.get(models.StoredDocument, doc_id)
    if doc is None or doc.application_id != row.application_id:
        raise BookingError(409, "evidence document not found on this case")
    # It must be a booking CONFIRMATION — the doc_type only the /evidence
    # endpoint or the agent's capture writes. An applicant's own upload (a
    # passport, a photo) is never accepted as proof of a booking.
    if doc.doc_type != EVIDENCE_DOC_TYPE:
        raise BookingError(409, "that document is not a booking confirmation; "
                                "attach the official confirmation page")
    row.confirmation = {"number": number, "evidence_document_id": doc_id,
                        "note": str(note or "")[:500],
                        "recorded_by": actor, "recorded_at": _iso(_now())}
    row.status = "booked"


def record_failed(db, row, *, reason: str, actor: str):
    if row.status in TERMINAL_STATUSES:
        raise BookingError(409, f"request is already {row.status}")
    clean = str(reason or "").strip()
    if not clean:
        raise BookingError(422, "an honest reason is required")
    row.failure_reason = f"{clean} (recorded by {actor})"[:500]
    row.status = "failed"


def cancel(db, row, *, actor: str):
    if row.status in TERMINAL_STATUSES:
        raise BookingError(409, f"request is already {row.status}")
    row.failure_reason = f"cancelled by the applicant ({actor})"[:500]
    row.status = "cancelled"


def view(row) -> dict:
    """The payload both seats render. Honesty carried in the data: slots are
    stamped with who read them and when; `is_real_government_result` is True
    ONLY on booked, which structurally requires the evidence pair."""
    if row is None:
        return {"exists": False}
    return {
        "exists": True,
        "id": row.id,
        "case_id": row.application_id,
        "route": row.route,
        "status": row.status,
        "posts": list(row.posts or []),
        "date_windows": list(row.date_windows or []),
        "note": row.note,
        "requested_by": row.requested_by,
        "requested_at": _iso(row.created_at),
        "offered_slots": list(row.offered_slots or []),
        # True when Ellis's browser agent read at least one of the slots (vs a
        # person typing them). Honest provenance, per slot, in `source`.
        "agent_read": any(isinstance(s, dict) and s.get("source") == "ellis_agent"
                          for s in (row.offered_slots or [])),
        "slots_notice": ("Each slot below was read from the official calendar "
                         "at the stated time — by Ellis's agent or a named "
                         "operator, shown per slot. Not live inventory, and "
                         "not held: it can be gone by the time the booking is "
                         "made."),
        "picked_slot": dict(row.picked_slot or {}),
        "picked_at": _iso(row.picked_at),
        "confirmation": dict(row.confirmation or {}),
        "failure_reason": row.failure_reason,
        "is_real_government_result": row.status == "booked",
        "legal_basis": LEGAL_BASIS.get(row.route, {}),
        "human_acts": human_acts(row.route),
        "never_automated_notice": (
            "Ellis never polls, scrapes, holds or books a slot on the "
            "official site. Every reading and every booking here was made "
            "by a named person in their own session."),
    }
