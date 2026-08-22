"""The AI agent that performs the operator's work: reads the official calendar
and drives the booking, inside a real authorized session.

This is the automated half of the booking desk. Instead of a person typing the
slots they saw, Ellis's browser agent reads them; instead of a person filling
the booking form, the agent drives it to the official confirmation. It does
this through the SAME live-portal machinery every other real portal action
uses (portal/live_driver.py + driver_factory.select_runtime_adapter), which
already enforces the boundary in code:

  * it binds only a production-approved, enabled adapter, in a real-only
    runtime, with a real browser session — never a mock, never speculatively;
  * the adapter contract PROHIBITS solve_captcha / bypass_bot_detection /
    spoof_fingerprint / rotate_proxy (portal/contract.py), so the agent
    structurally cannot defeat a human-verification challenge;
  * appointment/booking success is read from the OFFICIAL page, never assumed.

So the division of labour is honest and fixed:

  Ellis agent   reads the calendar, extracts the open slots, fills the booking
                form, drives it to the official confirmation, and captures that
                confirmation as evidence.
  the operator  is the accountable signed-in human: they (or their password
                manager) provide credentials — Ellis never handles a password —
                and they clear any human-verification check the site presents,
                because clearing THAT by machine is the abuse the official
                sites detect and punish (the traveller's appointment is
                cancelled). When the site throws a check, the agent stops and
                hands the live view to the operator, then resumes.

When no production-approved scheduling adapter or authorized session exists —
the state in every environment today — the agent fails CLOSED with an honest
reason and the desk falls back to the operator entering what they see. It
never fabricates a slot or a booking.
"""
from __future__ import annotations

from . import appt_booking as booking


class AgentUnavailable(Exception):
    """The agent could not act, honestly. `kind` routes the UI:
      * unsupported / not_ready  -> fall back to manual operator entry;
      * human_check              -> the operator clears the check in the live
                                    view, then re-runs the agent;
      * capture_missing          -> the booking may have gone through; the
                                    operator attaches the official confirmation.
    Never a silent failure and never a fabricated success."""

    def __init__(self, reason: str, *, kind: str = "not_ready",
                 needs: list[str] | None = None, live_view_url: str = "",
                 confirmation_hint: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.kind = kind
        self.needs = needs or []
        self.live_view_url = live_view_url
        self.confirmation_hint = confirmation_hint

    def as_payload(self) -> dict:
        return {"ran": False, "kind": self.kind, "reason": self.reason,
                "needs": self.needs, "live_view_url": self.live_view_url,
                "confirmation_hint": self.confirmation_hint}


# The human presence the agent depends on — named so the UI can state it and so
# no reader mistakes "the agent books it" for "unattended".
_REQUIRES = [
    "an authorized operator signed in to the official site, in their own "
    "session (Ellis never handles the password)",
    "a production-approved scheduling adapter for this route",
    "the operator present to clear any human-verification check",
]


def _route_target(row) -> tuple[str, str]:
    """(country, visa_type) for adapter selection — matches the scheduling
    adapters (app/portal/adapters/{us_visa,schengen}_scheduling.py). US is the
    single B1/B2 scheduling surface; Schengen is the accredited-ESP flow,
    modelled as the Schengen Area (the ESP portal, not the member state, is
    what the agent navigates)."""
    if row.route == "schengen":
        return ("Schengen Area", "schengen")
    return ("United States", "b1b2")


def _resolve_driver(row):
    """Bind the live driver for this route, or fail closed with the honest
    reason. Any missing precondition (no adapter, not real-only, no session)
    surfaces as AgentUnavailable('not_ready') — never a mock fallback."""
    from .portal.driver_factory import RealOnlyStop, select_runtime_adapter
    country, visa_type = _route_target(row)
    try:
        adapter = select_runtime_adapter(country, visa_type)
    except RealOnlyStop as e:
        raise AgentUnavailable(
            f"Ellis has no live path to the official scheduling site for this "
            f"route yet ({e.args[0] if e.args else 'unsupported'}).",
            kind="unsupported", needs=list(_REQUIRES))
    driver = getattr(adapter, "driver", None)
    if driver is None:
        raise AgentUnavailable(
            "the scheduling adapter has no bound live session; an authorized "
            "operator must open one first.",
            kind="not_ready", needs=list(_REQUIRES))
    return adapter, driver


def _normalize_slots(raw_slots) -> list[dict]:
    out = []
    for s in (raw_slots or []):
        if not isinstance(s, dict):
            continue
        post = str(s.get("post") or s.get("location") or s.get("center") or "").strip()
        when = str(s.get("when") or s.get("start") or s.get("datetime") or "").strip()
        if not post or not when:
            continue
        out.append({"post": post, "when": when,
                    "label": str(s.get("label") or s.get("type") or "").strip()})
    return out


def read_slots(db, row, *, operator: str, driver=None) -> dict:
    """The agent reads the official calendar and records what it found as the
    offered slots — stamped as agent-read from the operator's session. Returns
    {ran: True, count} or raises AgentUnavailable (honest fallback to manual).

    `driver` is injected in tests; production resolves it through the gated
    live-driver path and never accepts one over the wire."""
    if driver is None:
        _adapter, driver = _resolve_driver(row)
    # The applicant's first preferred post labels slots on calendars that do
    # not name the post themselves (a single-post scheduling page).
    preferred = (row.posts or [""])[0]
    try:
        result = driver.search_appointments(route=row.route, post=preferred)
    except Exception as e:  # noqa: BLE001 - a live human-check surfaces here
        raise _as_agent_error(e, driver)
    if not (isinstance(result, dict) and result.get("ok")):
        raise AgentUnavailable(
            "the agent could not read the official calendar in this session.",
            kind="not_ready", needs=list(_REQUIRES),
            live_view_url=_live_view(driver))
    slots = _normalize_slots(result.get("slots"))
    if not slots:
        # An honest empty read is a real answer, not a failure: no open slots
        # in the requested window right now.
        return {"ran": True, "count": 0, "empty": True,
                "note": ("Ellis read the official calendar and found no open "
                         "slots in this window right now. It will not hold or "
                         "poll — try again, or widen the dates.")}
    booking.offer_slots(db, row, slots=slots,
                        actor=f"Ellis agent (operator {operator})",
                        source="ellis_agent")
    return {"ran": True, "count": len(slots)}


# The codebase's own vocabulary for "someone took it first, and NOTHING was
# booked" (portal/synthetic.py, adapter_factory/runtime.py §24). It is the only
# answer that lets the agent move down the applicant's ranked list: it means the
# attempt was reversible and left no appointment behind.
_SLOT_GONE_CODES = ("SLOT_GONE", "NO_SLOTS", "SLOT_TAKEN", "SLOT_UNAVAILABLE")


def _slot_is_gone(result: dict) -> bool:
    """True ONLY when the driver said the slot is no longer available. An
    ambiguous "the page did not confirm" is deliberately not this: that answer
    could also mean the booking went through unseen, and moving to the next
    preference on a guess is how someone ends up with two appointments (and
    both cancelled). If the driver cannot tell, the agent stops."""
    code = str(result.get("code") or result.get("status") or "").strip().upper()
    return code in _SLOT_GONE_CODES


def _candidates(row) -> list[dict]:
    """What the agent may attempt, in the applicant's own order.

    Every candidate is a slot the APPLICANT NAMED: their ranked shortlist if
    they gave one (rank 1 first), else the single slot they picked. Ellis adds
    nothing to this list — there is no branch here that reaches for an unranked
    slot, "the earliest", or anything else off the calendar. If all of these
    fail, the agent fails closed and the desk offers fresh slots."""
    out = []
    for entry in booking.ranked_in_order(row):
        if entry.get("post") and entry.get("when"):
            out.append({"rank": entry.get("rank") or (len(out) + 1),
                        "post": entry.get("post"), "when": entry.get("when")})
    if out:
        return out
    picked = dict(row.picked_slot or {})
    if picked.get("post") and picked.get("when"):
        return [{"rank": picked.get("rank") or 0, "post": picked["post"],
                 "when": picked["when"]}]
    return []


def book(db, row, *, operator: str, driver=None,
         store_evidence=None) -> dict:
    """The agent drives the applicant's chosen slot to the official
    confirmation and records `booked` WITH the confirmation it captured.
    Evidence discipline is unchanged: no captured confirmation document -> no
    booked (the operator attaches it), so a real booking is never lost and a
    fake one never shown.

    When the applicant ranked several preferred times, the agent works DOWN
    THEIR LIST — first choice first, stopping at the first that books — and
    only steps to the next one when the official site said that slot is gone.
    It never substitutes a slot they did not rank; if every one of their
    choices is taken, it fails closed and the desk offers fresh slots.

    `store_evidence(name, mime, content) -> document_id` persists the captured
    confirmation page; injected by the API layer (and tests)."""
    if row.status != "slot_picked":
        raise AgentUnavailable(
            "there is no picked slot to book yet.", kind="not_ready")
    if driver is None:
        _adapter, driver = _resolve_driver(row)
    queue = _candidates(row)
    if not queue:
        raise AgentUnavailable(
            "there is no picked slot to book yet.", kind="not_ready")
    gone = []
    result, chosen = None, None
    while queue:
        candidate = queue.pop(0)
        try:
            attempt = driver.book_appointment(
                slot={"post": candidate["post"], "when": candidate["when"]},
                route=row.route)
        except Exception as e:  # noqa: BLE001
            # A human check or a broken session says nothing about whether the
            # slot is free. Surface it; never walk the list on a guess.
            raise _as_agent_error(e, driver)
        if isinstance(attempt, dict) and attempt.get("ok"):
            result, chosen = attempt, candidate
            break
        if isinstance(attempt, dict) and _slot_is_gone(attempt):
            gone.append(candidate)
            fresh = _normalize_slots(attempt.get("slots"))
            if fresh:
                # The site handed back what it still shows. Skip straight to the
                # applicant's next choice that is actually on it — still only
                # ever one of their own — instead of trying dead slots in turn.
                nxt = booking.next_available_rank(row, fresh)
                if nxt is None:
                    queue = []
                    break
                floor = nxt.get("rank") or 0
                queue = [c for c in queue if (c.get("rank") or 0) >= floor]
            continue
        raise AgentUnavailable(
            "the agent reached the booking step but the official page did not "
            "confirm; nothing was booked.",
            kind="not_ready", live_view_url=_live_view(driver))
    if result is None:
        # Every slot the applicant named is taken. The honest next step is a
        # fresh reading of the calendar and a fresh choice by them — Ellis does
        # not pick a replacement, so `booked` simply does not happen here.
        which = ("the time this applicant chose is" if len(gone) <= 1 else
                 f"none of the {len(gone)} preferred times this applicant "
                 f"chose are")
        raise AgentUnavailable(
            f"{which} still open on the official site; nothing was booked. "
            "Read the calendar again so they can choose from what is open now "
            "— Ellis will not book a time they did not choose.",
            kind="not_ready", needs=list(_REQUIRES),
            live_view_url=_live_view(driver))
    number = str(result.get("confirmation") or "").strip()
    capture = result.get("confirmation_capture")
    capture_mime = str(result.get("capture_mime") or "image/png")
    if not number:
        raise AgentUnavailable(
            "the official page did not return a confirmation number.",
            kind="not_ready", live_view_url=_live_view(driver))
    if not capture or store_evidence is None:
        # The booking may have officially succeeded — preserve the number so
        # nothing is lost — but `booked` needs the document, so route the
        # operator to attach the official confirmation.
        raise AgentUnavailable(
            "Ellis completed the booking steps but could not capture the "
            "official confirmation document; attach it to record the booking.",
            kind="capture_missing", confirmation_hint=number)
    rank = int(chosen.get("rank") or 0)
    if gone:
        # The record must name what was ACTUALLY booked, not the first choice
        # that was already taken. This is not Ellis choosing: `chosen` came out
        # of the applicant's own ranked list.
        row.picked_slot = dict(row.picked_slot or {}, post=chosen["post"],
                               when=chosen["when"], rank=rank)
    doc_id = store_evidence("agent-confirmation", capture_mime, capture)
    ranked_note = (f" (their choice ranked {rank}; ranks "
                   f"{', '.join(str(g.get('rank')) for g in gone)} were "
                   f"already taken)") if gone else ""
    booking.record_booked(db, row, confirmation_number=number,
                          evidence_document_id=doc_id,
                          note=f"booked by Ellis agent in operator {operator}'s session{ranked_note}",
                          actor=f"Ellis agent (operator {operator})")
    out = {"ran": True, "confirmation_number": number}
    if rank:
        out["booked_rank"] = rank
        out["ranks_gone"] = [g.get("rank") for g in gone]
    return out


def _live_view(driver) -> str:
    try:
        return str(driver.live_view_url() or "")
    except Exception:  # noqa: BLE001
        return ""


def _as_agent_error(exc: Exception, driver) -> AgentUnavailable:
    """Translate a live-driver stop into the honest agent state. Only a real
    human-verification challenge becomes `human_check` (the operator clears
    it) — recognised by its message, NOT by exception type. A generic
    RealOnlyStop (off-allowlist URL, no session, unsupported route) is a
    config/infra fault with nothing to clear, so it falls through to
    `not_ready` with its own honest reason and the desk falls back to manual —
    never an unbreakable 'clear the check and retry' loop."""
    lv = _live_view(driver)
    text = str(exc).lower()
    if "captcha" in text or "verification" in text or "challenge" in text \
            or "human check" in text or "bot" in text:
        return AgentUnavailable(
            "the official site presented a human-verification check. By design "
            "Ellis never clears one — the operator clears it in the live view, "
            "then re-runs the agent.",
            kind="human_check", needs=list(_REQUIRES), live_view_url=lv)
    return AgentUnavailable(f"the agent could not continue: {exc}",
                            kind="not_ready", live_view_url=lv)
