"""Arrival-card filing on visa-exempt routes.

The gap this covers: a CHN->THA case had every document in, a released
thailand-tdac adapter, and a resolver that reached it — and pressing Continue
recorded a stage row and stopped. Nothing was ever enqueued, so the screen
said "Entry preparation complete" while the arrival card was never filed
(2026-08-03).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app import entry_preparation as ep


# --- the destination's window, parsed from researched prose ----------------

@pytest.mark.parametrize("text,days", [
    ("within 3 days before arrival", 3),
    ("3 days before your arrival in Thailand", 3),
    ("72 hours prior to arrival", 3),
    ("within 36 hours of arrival", 2),      # rounds up: still the day before
    ("within 14 days prior to arrival", 14),
    ("trong vòng 3 ngày trước khi đến", 3),  # vi
    ("抵达前 3 天内", 3),                     # zh
])
def test_window_is_parsed_from_the_researched_text(text, days):
    assert ep.window_days(text) == days


@pytest.mark.parametrize("text", [
    "", None, "any time before arrival", "no arrival card required",
    "within 900 days",                      # implausible: not a window
])
def test_an_unusable_window_is_none_not_a_guess(text):
    """None means 'file now and let the portal be the authority on its own
    timing' — better than inventing a number and holding a ready case back."""
    assert ep.window_days(text) is None


# --- scheduling and release ------------------------------------------------

class _Detail(dict):
    pass


class _Stage:
    def __init__(self, application_id, opens_on):
        self.application_id = application_id
        self.stage = ep.STAGE_ENTRY_FILING
        self.detail = {"opens_on": opens_on}
        self.completed_at = None


class _App:
    def __init__(self, id_, state="DRAFT"):
        self.id = id_
        self.state = state


class _FakeDB:
    """Just enough of the session for due_cases: one scalars() list of stage
    rows, a case lookup, and no runs in flight unless told otherwise."""
    def __init__(self, stages, apps, busy=()):
        self._stages = stages
        self._apps = {a.id: a for a in apps}
        self._busy = set(busy)
        self._q = 0

    def execute(self, *_a, **_k):
        self._q += 1
        db = self
        class _R:
            def scalars(inner):
                # 1st query: the stage rows. Later: the in-flight run probe.
                class _S:
                    def all(s2):
                        return db._stages
                    def first(s2):
                        return "run" if db._pending_busy else None
                return _S()
        return _R()

    def get(self, _model, app_id):
        self._pending_busy = app_id in self._busy
        return self._apps.get(app_id)

    _pending_busy = False


def _dates():
    today = date.today()
    return (today + timedelta(days=5)).isoformat(), (today - timedelta(days=1)).isoformat()


def test_a_case_whose_window_has_not_opened_is_left_alone():
    future, _ = _dates()
    db = _FakeDB([_Stage("a", future)], [_App("a")])
    assert ep.due_cases(db) == []


def test_a_case_whose_window_has_opened_is_released():
    _, past = _dates()
    app = _App("a")
    db = _FakeDB([_Stage("a", past)], [app])
    assert ep.due_cases(db) == [app]


def test_a_case_the_applicant_already_started_is_never_re_released():
    """Only an untouched DRAFT is the worker's to release — anything the
    applicant drove by hand belongs to them."""
    _, past = _dates()
    db = _FakeDB([_Stage("a", past)], [_App("a", state="SUBMITTING")])
    assert ep.due_cases(db) == []


def test_a_case_with_a_run_in_flight_is_not_queued_twice():
    _, past = _dates()
    db = _FakeDB([_Stage("a", past)], [_App("a")], busy=["a"])
    assert ep.due_cases(db) == []


def test_a_schedule_with_no_recorded_window_releases_immediately():
    """No parsed window means the portal decides — so it goes now."""
    app = _App("a")
    db = _FakeDB([_Stage("a", None)], [app])
    assert ep.due_cases(db) == [app]
