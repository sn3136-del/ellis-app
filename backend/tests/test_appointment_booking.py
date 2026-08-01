"""Real appointment booking: read the portal's live calendar, let the APPLICANT
choose, book that exact slot reconcile-first, rank centres by distance.

The rules that make it safe to reserve a government appointment for a named
person:
  * Ellis never auto-books — a flow without an applicant-selection handoff
    cannot release the appointment_booking capability.
  * Ellis books ONLY a slot the applicant explicitly chose AND that it really
    observed as bookable.
  * A slot taken by someone else is an honest SLOT_GONE, never a wrong booking.
  * A portal with a Book button but no readable calendar produces no booking
    segment at all (fail closed) rather than guessing.
"""
from __future__ import annotations

import pytest

from app.adapter_factory import specgen, schema, auto_release
from app.adapter_factory.live_driver import _parse_slot_datetime
from app import appointments as appt


class _Art:
    def __init__(self, page_key, elements, url_pattern=""):
        self.page_key = page_key
        self.structure = {"elements": elements, "url_pattern": url_pattern}


def _calendar_page(n=3):
    els = [{"selector": "#book-btn", "name": "book", "label": "Confirm booking",
            "type": "button", "submits": "book"}]
    for i in range(n):
        dt = f"2026-09-{15 + i}T09:00"
        els.append({"selector": f'[data-slot="S{i}"]', "name": f"slot_{i}",
                    "label": dt, "type": "button",
                    "attrs": {"data-slot": f"S{i}", "data-datetime": dt}})
    return _Art("appointments", els)


# --- flow generation -------------------------------------------------------

def test_calendar_page_yields_a_complete_bookable_segment():
    flow = specgen._skeleton_flow("p.gov.example",
                                  {"appointments": _calendar_page()}, [])
    ids = {n["node_id"]: n for n in flow}
    assert "read_slots" in ids and "book" in ids
    assert ids["read_slots"]["action"] == "READ_APPOINTMENT_INVENTORY"
    assert ids["read_slots"]["slot_selector"] == "[data-slot]"
    assert ids["book"]["action"] == "BOOK_APPOINTMENT"
    assert ids["book"]["irreversibility"] == "irreversible"
    assert ids["book"]["retry_class"] == "reconcile_first"
    # The applicant's own choice is a required step of the flow.
    assert any(n.get("handoff_kind") == "appointment_selection" for n in flow)
    assert schema.validate_flow(flow, allowed_hostnames=["p.gov.example"]) == []


def test_book_button_without_a_readable_calendar_emits_no_booking():
    """Fail closed: no observed slot rows means Ellis cannot show real
    availability, so it must not emit a booking step that guesses."""
    art = _Art("appointments", [{"selector": "#book-btn", "name": "book",
                                 "label": "Book appointment", "type": "button",
                                 "submits": "book"}])
    flow = specgen._skeleton_flow("p.gov.example", {"appointments": art}, [])
    ids = [n["node_id"] for n in flow]
    assert "book" not in ids and "read_slots" not in ids


def test_a_flow_that_auto_books_can_never_release_the_capability():
    """Without an applicant-selection handoff the capability gate refuses —
    reserving a government appointment is always the applicant's decision."""
    class V:
        flow = [
            {"node_id": "read", "action": "READ_APPOINTMENT_INVENTORY",
             "slot_selector": "[data-slot]"},
            {"node_id": "rec", "action": "RECONCILE_OUTCOME",
             "retry_class": "reconcile_first"},
            {"node_id": "book", "action": "BOOK_APPOINTMENT", "handle_attr": "data-slot",
             "irreversibility": "irreversible", "retry_class": "reconcile_first",
             "max_retries": 1,
             "success_evidence": [{"kind": "network", "category": "appointment_booked"}]},
        ]
    ok, problems, _ = auto_release.capability_gate(V, "appointment_booking")
    assert ok is False
    assert any("appointment-selection" in p for p in problems)


# --- slot time parsing -----------------------------------------------------

@pytest.mark.parametrize("raw,expect_parsed", [
    ("2026-09-15T09:30:00Z", True),
    ("2026-09-15T09:30:00+02:00", True),
    ("2026-09-15 09:30", True),
    ("15/09/2026 09:30", True),
    ("next Tuesday", False),      # never guess a date
    ("09:30", False),             # a bare time has no date — refuse
    ("", False),
])
def test_slot_times_parse_or_are_refused(raw, expect_parsed):
    assert (_parse_slot_datetime(raw) is not None) is expect_parsed


# --- nearest-centre ranking ------------------------------------------------

CENTERS = {"PAR": {"lat": 48.8566, "lon": 2.3522, "label": "Paris"},
           "LYO": {"lat": 45.7500, "lon": 4.8500, "label": "Lyon"}}


def _slots():
    return [{"slotId": "a", "startUtc": 1000, "locationId": "PAR"},
            {"slotId": "b", "startUtc": 5000, "locationId": "LYO"},
            {"slotId": "c", "startUtc": 200, "locationId": "UNKNOWN"}]


def test_nearest_centre_wins_over_earliest_time():
    """The applicant near Lyon gets Lyon first even though Paris has an
    earlier slot — distance is the primary key, time breaks ties."""
    near_lyon = {"lat": 45.76, "lon": 4.84}
    ranked = appt.rank_by_nearest(
        appt.annotate_distance(_slots(), CENTERS, near_lyon), {})
    assert ranked[0]["slotId"] == "b"
    assert ranked[0]["distanceKm"] < 5
    assert ranked[0]["locationLabel"] == "Lyon"
    # A centre with unknown coordinates never claims to be nearest.
    assert ranked[-1]["slotId"] == "c" and ranked[-1]["distanceKm"] is None


def test_unknown_location_is_never_hidden_by_a_travel_radius():
    """maxTravelKm drops centres KNOWN to be too far; it must not silently
    hide a slot whose distance could not be measured."""
    ranked = appt.rank_by_nearest(
        appt.annotate_distance(_slots(), CENTERS, {"lat": 45.76, "lon": 4.84}),
        {"maxTravelKm": 100})
    ids = [s["slotId"] for s in ranked]
    assert "a" not in ids          # Paris ~390km away: correctly dropped
    assert "b" in ids and "c" in ids


def test_no_applicant_location_means_no_distance_claims():
    ranked = appt.annotate_distance(_slots(), CENTERS, None)
    assert all(s["distanceKm"] is None for s in ranked)
