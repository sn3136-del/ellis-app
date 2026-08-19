"""Real calendars on the two systems that have one, read but never touched.

Germany's RK-Termin and Poland's e-Konsulat have no accounts at all — one image
CAPTCHA stands in front of the month grid. Ellis can serve that honestly: the
applicant solves the challenge in the secure window. What Ellis must never do
is solve it, or click a date — on e-Konsulat a click places a one-hour hold on
a real slot.
"""
from __future__ import annotations

import pytest

from app import gov_calendar as gc


class _Driver:
    """Minimal stand-in: canned evaluate results, records navigation."""
    def __init__(self, captcha=True, days=None, text=""):
        self.captcha, self._days, self._text = captcha, days or [], text
        self.visited = []
    def goto(self, url):
        self.visited.append(url)
    def evaluate(self, js, *a):
        if "captchaText" in js:
            return not self.captcha if False else self.captcha
        if "appointment_showDay" in js:
            return self._days
        if "innerText" in js and "body" in js:
            return self._text
        return []


def test_a_gated_month_is_never_read():
    """The CAPTCHA is the applicant's to solve; until they have, Ellis has no
    calendar and says so rather than reporting an empty one."""
    with pytest.raises(gc.CalendarUnavailable):
        gc.read_month(_Driver(captcha=True))


def test_summary_reports_the_gate_instead_of_pretending_no_slots():
    out = gc.month_summary(_Driver(captcha=True))
    assert out["readable"] is False
    assert "image check" in out["reason"]
    assert out["days"] == []


def test_an_open_month_is_read_without_clicking():
    days = [{"label": "14", "href": "https://service2.diplo.de/rktermin/extern/appointment_showDay.do?d=14",
             "title": "3 free"}]
    d = _Driver(captcha=False, days=days, text="Please choose a day")
    out = gc.month_summary(d)
    assert out["readable"] is True
    assert out["bookable_count"] == 1
    assert out["none_available"] is False
    assert d.visited == [], "reading a month must navigate nowhere — a click reserves"


def test_an_empty_month_says_so():
    """'No appointments available' is the honest, common answer; reporting it
    as unreadable would send the applicant to refresh forever."""
    d = _Driver(captcha=False, days=[], text="Unfortunately there are no appointments available")
    out = gc.month_summary(d)
    assert out["readable"] is True and out["none_available"] is True


def test_the_module_never_solves_a_captcha():
    import inspect
    src = inspect.getsource(gc)
    for forbidden in ("solve_captcha", "captcha_answer", "ocr", "2captcha", "anticaptcha"):
        assert forbidden not in src.lower()


def test_the_module_never_clicks_a_day():
    """On e-Konsulat clicking a date places a one-hour hold on a real slot, so
    there must be no code path that clicks one."""
    import inspect
    src = inspect.getsource(gc)
    for forbidden in ("def book", ".click(", "click_day", "select_slot"):
        assert forbidden not in src


def test_category_ids_are_read_live_not_pinned():
    """The July-curated ids no longer existed in August: the walk must re-read
    the live lists rather than trust stored ids."""
    import inspect
    src = inspect.getsource(gc.rk_termin_walk)
    assert "choose_categoryList" in src and "links_matching" in src


def test_open_day_carries_the_applicants_choice_and_fails_closed():
    """open_day carries out the day the APPLICANT picked from a grid Ellis
    read — it never chooses one, and it refuses anything but a real day link
    from this system, on-host, and present in the grid actually shown."""
    from app import gov_calendar as gc

    class Drv:
        def goto(self, url):
            self.url = url
        def evaluate(self, _):
            return getattr(self, "url", "")

    real = ("https://service2.diplo.de/rktermin/extern/"
            "appointment_showDay.do?dateStr=20.09.2026&locationCode=peki")
    out = gc.open_day(Drv(), href=real, known_hrefs=[real])
    assert out["opened"] is True and "showDay" in out["url"]

    # Off-host, not-a-day-link, and not-in-the-shown-grid all fail closed.
    other = ("https://service2.diplo.de/rktermin/extern/"
             "appointment_showDay.do?dateStr=01.01.2027")
    for bad in ("https://evil.example/appointment_showDay.do?x=1",
                "https://service2.diplo.de/rktermin/extern/appointment_showMonth.do",
                real):  # real link, but grid only showed `other`
        try:
            gc.open_day(Drv(), href=bad, known_hrefs=[other])
            assert False, f"should have refused {bad}"
        except gc.CalendarUnavailable:
            pass
