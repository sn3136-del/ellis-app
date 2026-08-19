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
    """Ellis may TRANSCRIBE the answer a human read; it may never PRODUCE one.

    Checked as capability rather than as prose (the module legitimately says
    "no OCR" in its own comments): no solving library or service is imported
    or called, and submit_captcha structurally cannot invent an answer — its
    text is caller-supplied, required, and has no default."""
    import inspect
    src = inspect.getsource(gc)
    lowered = src.lower()
    for forbidden in ("solve_captcha(", "2captcha", "anticaptcha", "capmonster",
                      "deathbycaptcha", "pytesseract", "easyocr", "tesseract",
                      "import cv2", "from pil", "image_to_string"):
        assert forbidden not in lowered, f"captcha-solving capability: {forbidden}"
    sig = inspect.signature(gc.submit_captcha)
    text = sig.parameters["text"]
    assert text.kind is inspect.Parameter.KEYWORD_ONLY
    assert text.default is inspect.Parameter.empty, "an answer must never default"

    class _D:
        def evaluate(self, _js):
            return True
        def fill(self, *_a):
            raise AssertionError("must not fill without an answer")
        def click(self, *_a):
            raise AssertionError("must not submit without an answer")
    for blank in ("", "   "):
        try:
            gc.submit_captcha(_D(), text=blank)
            assert False, "blank answers must be refused"
        except gc.CalendarUnavailable:
            pass


def test_the_module_never_chooses_a_day():
    """On e-Konsulat clicking a date places a one-hour hold on a real slot, so
    Ellis must never CHOOSE one — not the earliest, not from preferences.

    Opening the day the APPLICANT picked is a different act and is allowed
    (open_day), but it must be driven entirely by an href they were shown."""
    import inspect
    import io
    import tokenize
    src = inspect.getsource(gc)

    def _code_only(text):
        """Executable source with comments and docstrings stripped, so the
        module can NAME the rules it keeps ("never the earliest") without the
        prose reading as the capability itself."""
        out = []
        prev = tokenize.INDENT
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                continue
            if tok.type == tokenize.STRING and prev in (
                    tokenize.INDENT, tokenize.DEDENT, tokenize.NEWLINE,
                    tokenize.NL, tokenize.ENCODING):
                prev = tok.type          # a docstring, not a value
                continue
            if tok.type not in (tokenize.NL, tokenize.NEWLINE):
                prev = tok.type
            out.append(tok.string)
        return " ".join(out)

    code = _code_only(src)
    for forbidden in ("def book", "click_day", "select_slot", "def pick_day",
                      "earliest"):
        assert forbidden not in code, f"day-choosing capability: {forbidden}"
    # Nothing may rank or pick from the DAY list. (Sorting the MISSION list
    # alphabetically for display is fine and deliberately not caught here.)
    for fn in (gc.read_month, gc.month_summary, gc.open_day):
        body = _code_only(inspect.getsource(fn))
        for forbidden in ("sort (", "sorted (", "min (", "max (", "[ 0 ]"):
            assert forbidden not in body, (
                f"{fn.__name__} must not rank or pick a day: {forbidden}")
    for line in src.splitlines():
        if ".click(" in line:
            assert "showDay" not in line, "must never click a day link"
    sig = inspect.signature(gc.open_day)
    href = sig.parameters["href"]
    assert href.kind is inspect.Parameter.KEYWORD_ONLY
    assert href.default is inspect.Parameter.empty, "a day must never default"

    class _D:
        def __init__(self):
            self.went = None
        def goto(self, url):
            self.went = url
        def evaluate(self, _js):
            return self.went or ""
        def click(self, *_a):
            raise AssertionError("open_day must navigate, never click a day")
    real = ("https://service2.diplo.de/rktermin/extern/"
            "appointment_showDay.do?dateStr=20.09.2026")
    d = _D()
    gc.open_day(d, href=real, known_hrefs=[real])
    assert d.went == real


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
