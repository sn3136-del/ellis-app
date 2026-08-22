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
  slot_picked      the applicant chose one of those, inside Trip.com — either
                   one slot, or up to MAX_RANKED of them in their own order of
                   preference (rank_slots), which is still ONE person's own
                   choice and never Ellis's: see the note on rank_slots
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
  acts (create / pick / rank / cancel) require case ownership. Neither can do
  the other's part.
* ELLIS NEVER PICKS THE SLOT — the same line drawn in gov_calendar.py ("Ellis
  READS the grid and never picks from it... The applicant chooses") and
  assisted_booking.py. Ranking does not bend it: the candidates are the
  applicant's own named choices and nothing else, so every slot Ellis can
  reach is one they already consented to. Read rank_slots before changing it.
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

# How many preferred times the applicant may name in one session (Trip.com's
# ask). A cap, not a target: one is a complete answer. See rank_slots for why a
# ranked list is still the applicant's own choice and not Ellis picking.
MAX_RANKED = 5

# One sentence, used by both applicant acts (pick and rank): consent must land
# on the slot the applicant actually SAW, never on a position in a list that
# moved underneath them.
SLOTS_CHANGED = ("the offered dates changed before your pick — please look "
                 "again and pick from the current list")

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
    clears any pick OR ranking made against it — a preference order over slots
    that no longer exist is not a choice, so it is dropped rather than
    re-pointed at whatever now sits at those positions."""
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
    row.ranked_slots = []
    row.ranked_at = None
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
            raise BookingError(409, SLOTS_CHANGED)
    row.picked_slot = dict(chosen, picked_by=actor)
    row.picked_at = _now()
    row.status = "slot_picked"


def rank_slots(db, row, *, indices: list, actor: str,
               expect: list | None = None):
    """The applicant names up to MAX_RANKED of the offered slots, in their own
    order of preference — Trip.com's "five preferred times in one session".

    THIS IS NOT ELLIS PICKING THE SLOT, and the design is what makes that true
    rather than a promise:

    * Every ranked entry is a slot the applicant PERSONALLY SELECTED from the
      slots they were offered and saw. Ellis contributes no candidate, scores
      nothing, and never adds "the earliest one" to the list.
    * The ORDER is theirs. It is preference, not a computed ranking.
    * At booking time the agent takes the highest-ranked entry that is STILL
      AVAILABLE (`next_available_rank`). Every outcome it can produce is a slot
      this person already named and consented to.
    * If all of their choices are gone, the honest answer is NEW offered slots
      — the request goes back to the desk. Ellis does not widen the window, does
      not fall back to whatever is earliest, and does not book a slot the
      applicant never named. There is no code path here that could.

    So the only thing ranking automates is the RETRY, across choices the
    applicant made. That is the same line gov_calendar.py draws when it lets
    Ellis open the day the applicant selected: carrying out their choice is not
    making one. A reader looking for "Ellis picks the slot" will not find it
    here, because it is not here.

    Ranking is one act, from the same precondition as pick_slot: rank 1 becomes
    the picked slot, so everything downstream (evidence, `booked`) is unchanged.
    `expect` optionally echoes the {post, when} of each ranked slot IN THE SAME
    ORDER — the same staleness protection pick_slot has, so a re-offer between
    render and rank can never bind consent to slots the applicant never saw."""
    if row.status != "slots_offered":
        raise BookingError(409, f"cannot rank on a {row.status} request")
    slots = list(row.offered_slots or [])
    if not isinstance(indices, (list, tuple)) or not indices:
        raise BookingError(422, "choose at least one of the offered slots")
    if len(indices) > MAX_RANKED:
        raise BookingError(422, f"at most {MAX_RANKED} preferred times can be "
                                f"ranked in one session; {len(indices)} given")
    chosen, seen = [], set()
    for i in indices:
        # bool is an int in Python — a True in the list is malformed input, not
        # index 1, so it is refused rather than silently ranked.
        if isinstance(i, bool) or not isinstance(i, int) \
                or i < 0 or i >= len(slots):
            raise BookingError(422, f"slot index {i} is not one of the "
                                    f"{len(slots)} offered")
        if i in seen:
            raise BookingError(422, f"slot index {i} is ranked twice — each "
                                    f"preferred time can be named once")
        seen.add(i)
        chosen.append(slots[i])
    echo = [e for e in (expect or []) if isinstance(e, dict)]
    if echo:
        if len(echo) != len(chosen):
            raise BookingError(409, SLOTS_CHANGED)
        for want, got in zip(echo, chosen):
            want_post = str(want.get("post") or "").strip()
            want_when = str(want.get("when") or "").strip()
            if (want_post and got.get("post") != want_post) or \
                    (want_when and got.get("when") != want_when):
                raise BookingError(409, SLOTS_CHANGED)
    at = _now()
    row.ranked_slots = [dict(s, rank=n, ranked_by=actor)
                        for n, s in enumerate(chosen, start=1)]
    row.ranked_at = at
    # Rank 1 IS the pick: the applicant's first preference, recorded through the
    # same field every downstream step already reads. The lower ranks are the
    # fallbacks they authorised in advance, used only if their first choice is
    # gone by the time the operator reaches the official site.
    row.picked_slot = dict(chosen[0], picked_by=actor, rank=1)
    row.picked_at = at
    row.status = "slot_picked"


def next_available_rank(row, still_available):
    """The applicant's highest-ranked choice that is STILL available, or None.

    Pure: no I/O, no clock, no ordering by time. It can only ever return an
    entry from the applicant's OWN ranked list, matched on (post, when) against
    what is actually on offer right now.

    None means exactly one thing — every slot this applicant named is gone. The
    honest response to that is a fresh set of offered slots, never a substitute
    Ellis chose for them."""
    have = set()
    for s in (still_available or []):
        if isinstance(s, dict):
            key = (str(s.get("post") or "").strip(),
                   str(s.get("when") or "").strip())
        elif isinstance(s, (list, tuple)) and len(s) == 2:
            key = (str(s[0]).strip(), str(s[1]).strip())
        else:
            continue
        if key != ("", ""):
            have.add(key)
    for entry in ranked_in_order(row):
        key = (str(entry.get("post") or "").strip(),
               str(entry.get("when") or "").strip())
        if key in have:
            return dict(entry)
    return None


def ranked_in_order(row) -> list[dict]:
    """The ranked entries, first preference first. Ordered by the stored `rank`
    (falling back to stored position) so a hand-edited or legacy row can never
    silently reorder the applicant's preferences."""
    entries = [e for e in (getattr(row, "ranked_slots", None) or [])
               if isinstance(e, dict)]

    def _key(pair):
        i, entry = pair
        try:
            return (int(entry.get("rank") or (i + 1)), i)
        except (TypeError, ValueError):
            return (i + 1, i)

    return [e for _i, e in sorted(enumerate(entries), key=_key)]


def record_booked(db, row, *, confirmation_number: str,
                  evidence_document_id: str, note: str, actor: str,
                  booked_slot: dict | None = None):
    """`booked` only behind evidence: number + confirmation document stored on
    THIS case. Anything less is refused, not softened.

    booked_slot: when the operator worked the applicant's RANKED list by hand
    and the first choice was gone, this names the entry actually booked. It
    must be one of the slots the applicant ranked (or the picked slot itself)
    — an operator can no more book an unranked time than the agent can — and
    picked_slot is updated so the record names what really happened instead of
    a slot that was already taken."""
    if row.status != "slot_picked":
        raise BookingError(409, f"cannot mark a {row.status} request booked — "
                                "the applicant has not picked a slot")
    if booked_slot:
        want = (str(booked_slot.get("post") or "").strip(),
                str(booked_slot.get("when") or "").strip())
        allowed = {(str(e.get("post") or "").strip(),
                    str(e.get("when") or "").strip())
                   for e in ranked_in_order(row)}
        picked = row.picked_slot or {}
        allowed.add((str(picked.get("post") or "").strip(),
                     str(picked.get("when") or "").strip()))
        allowed.discard(("", ""))
        if want not in allowed:
            raise BookingError(409, "that slot is not one the applicant chose "
                                    "— only a ranked choice can be recorded "
                                    "as booked")
        match = next((dict(e) for e in ranked_in_order(row)
                      if (str(e.get("post") or "").strip(),
                          str(e.get("when") or "").strip()) == want),
                     dict(picked))
        row.picked_slot = match
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
        # The applicant's own shortlist, first preference first. Rank 1 is also
        # `picked_slot`; the rest are the fallbacks they authorised in advance.
        "ranked_slots": ranked_in_order(row),
        "ranked_at": _iso(getattr(row, "ranked_at", None)),
        "max_ranked": MAX_RANKED,
        "ranking_notice": ("You choose these, in your own order. Ellis books "
                           "the highest one still available when it reaches "
                           "the official site, and nothing outside this list "
                           "— if they are all taken, you are asked again with "
                           "fresh dates."),
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
