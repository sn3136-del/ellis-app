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


def test_the_visa_area_is_preferred_by_label_not_position():
    """Area order is not a contract across 196 missions. With no realm named,
    the walk prefers the area whose label says visa wherever it sits, refuses
    to be fooled by negated labels ("without a visa"), falls back to the first
    when no area mentions visa, and an explicit choice always wins."""
    class _W:
        def __init__(self, realms, cats):
            self._realms, self._cats = realms, cats
        def goto(self, url):
            pass
        def evaluate(self, js, *a):
            if "captchaText" in js:
                return False
            if a and a[0] == "realmId=":
                return self._realms
            if a and a[0] == "categoryId=":
                return self._cats
            return []

    cats = [{"text": "Some queue", "query": "categoryId=7&realmId=4"}]

    # The consular area lists FIRST and its label mentions "visa" negated —
    # exactly the Shanghai trap. The walk must still land on the visa area.
    realms = [
        {"text": "Consular matters, without a visa", "query": "realmId=9&locationCode=shan"},
        {"text": "Visa Application (over 90 days)", "query": "realmId=4&locationCode=shan"},
    ]
    out = gc.rk_termin_walk(_W(realms, cats), location_code="shan")
    assert out["realm_id"] == "4"

    # Peking in ENGLISH: "(except for visa)" — a filler word rides between
    # the negation and the visa word.
    peki_en = [
        {"text": "Consular matters (except for visa)", "query": "realmId=1224&locationCode=peki"},
        {"text": "Visa", "query": "realmId=12&locationCode=peki"},
    ]
    out = gc.rk_termin_walk(_W(peki_en, cats), location_code="peki")
    assert out["realm_id"] == "12"

    # Peking's real trap: the consular area says "(außer Visa)" — except
    # visas — and lists FIRST. An excluding word is a negation too.
    peking = [
        {"text": "Rechts- und Konsularsachen (außer Visa)", "query": "realmId=1224&locationCode=peki"},
        {"text": "Visa / 签证", "query": "realmId=12&locationCode=peki"},
    ]
    out = gc.rk_termin_walk(_W(peking, cats), location_code="peki")
    assert out["realm_id"] == "12"

    # No area mentions visa at all: fall back to the first, never invent.
    plain = [
        {"text": "Passport matters", "query": "realmId=1&locationCode=x"},
        {"text": "Certifications", "query": "realmId=2&locationCode=x"},
    ]
    out = gc.rk_termin_walk(_W(plain, cats), location_code="x")
    assert out["realm_id"] == "1"

    # The applicant's explicit choice beats the label default.
    out = gc.rk_termin_walk(_W(realms, cats), location_code="shan", realm_id="9")
    assert out["realm_id"] == "9"


def test_the_booking_form_fill_is_transcription_and_clicks_nothing():
    """fill_book_form refuses the picture box outright, an empty answer set
    is an error rather than a silent no-op, and filling never clicks."""
    class _D:
        def __init__(self): self.clicked = []
        def evaluate(self, js, *a):
            return {"filled": list(a[0].keys()) if a else [], "refused": []}
        def click(self, sel): self.clicked.append(sel)
    d = _D()
    out = gc.fill_book_form(d, answers={"lastname": "CAO", "captchaText": "ABC"})
    assert out["filled"] == ["lastname"]
    assert any(r["name"] == "captchaText" for r in out["refused"])
    assert d.clicked == [], "filling must click nothing"
    with pytest.raises(gc.CalendarUnavailable):
        gc.fill_book_form(d, answers={})


def test_submit_is_only_the_applicants_relayed_instruction():
    """No instruction, an unticked confirmation, or an empty picture answer
    each refuse BEFORE the click; only the full set presses Submit."""
    class _D:
        def __init__(self, unticked=0, cap_empty=False):
            self.state = {"unticked": unticked, "captcha_empty": cap_empty,
                          "has_submit": True}
            self.clicked = []
        def evaluate(self, js, *a):
            return self.state if "checkbox" in js else ""
        def click(self, sel): self.clicked.append(sel)
    with pytest.raises(gc.CalendarUnavailable):
        gc.submit_book_form(_D(), applicant_instructed=False)
    for d in (_D(unticked=1), _D(cap_empty=True)):
        with pytest.raises(gc.CalendarUnavailable):
            gc.submit_book_form(d, applicant_instructed=True)
        assert d.clicked == [], "a refused submit must not have clicked"
    d = _D()
    out = gc.submit_book_form(d, applicant_instructed=True)
    assert out["submitted"] is True and len(d.clicked) == 1


def test_the_form_is_reached_only_by_the_sites_own_query():
    """open_book_form takes the walk's own query string and nothing else —
    no hand-built addresses, no foreign hosts, no markup."""
    class _D:
        def goto(self, url): raise AssertionError("must refuse before navigating")
        def evaluate(self, js, *a): return None
    for bad in ("", "categoryId=1", "locationCode=shan",
                "locationCode=shan&categoryId=<script>",
                "https://evil.example/?locationCode=shan&categoryId=1"):
        with pytest.raises(gc.CalendarUnavailable):
            gc.open_book_form(_D(), query=bad)


def test_a_time_is_opened_only_from_the_list_ellis_showed():
    """open_time carries the applicant's slot choice — a real Book link, on a
    known host, from the exact list Ellis displayed. Everything else refuses
    before navigating."""
    class _D:
        def __init__(self): self.went = None
        def goto(self, url): self.went = url
        def evaluate(self, js, *a): return ""
    real = ("https://service2.diplo.de/rktermin/extern/appointment_showForm.do"
            "?locationCode=peki&realmId=12&categoryId=2686&dateStr=24.08.2026"
            "&openingPeriodId=67531")
    for bad_href, known in (
            ("", [real]),
            ("https://evil.example/appointment_showForm.do?x=1", [real]),
            ("https://service2.diplo.de/rktermin/extern/appointment_showMonth.do?x=1", [real]),
            (real, []),                       # nothing was shown
            (real, [real + "&other=1"])):     # not the shown link
        d = _D()
        with pytest.raises(gc.CalendarUnavailable):
            gc.open_time(d, href=bad_href, known_hrefs=known)
        assert d.went is None, "a refused time must not navigate"
    d = _D()
    out = gc.open_time(d, href=real, known_hrefs=[real])
    assert d.went == real and out["opened"] is True


def test_confirmations_need_statements_to_relay():
    with pytest.raises(gc.CalendarUnavailable):
        gc.relay_confirmations(object(), labels=[])


def test_the_queue_default_finds_self_employment_not_the_first_seat():
    """Shanghai lists specialty cooks first; the lane is offered for
    self-employed applicants, so the default queue is the one whose label
    covers self-employment (German or English), wherever it sits. No match
    falls back to the first, and an explicit choice still wins."""
    class _W:
        def __init__(self, realms, cats):
            self._realms, self._cats = realms, cats
        def goto(self, url):
            pass
        def evaluate(self, js, *a):
            if "captchaText" in js:
                return False
            if a and a[0] == "realmId=":
                return self._realms
            if a and a[0] == "categoryId=":
                return self._cats
            return []

    realms = [{"text": "Wartelisten Visum (über 90 Tage)",
               "query": "realmId=1315&locationCode=shan"}]
    cats = [
        {"text": "Nationale Visa für Spezialitätenköche und -köchinnen",
         "query": "categoryId=3180&realmId=1315&locationCode=shan"},
        {"text": "Warteliste für sonstige Erwerbstätigkeit (u.a. Selbständige "
                 "Tätigkeiten, Praktikum, Au-pair)",
         "query": "categoryId=3181&realmId=1315&locationCode=shan"},
        {"text": "Warteliste für die Wiedereinreise",
         "query": "categoryId=3175&realmId=1315&locationCode=shan"},
    ]
    out = gc.rk_termin_walk(_W(realms, cats), location_code="shan")
    assert out["category_id"] == "3181"

    # English session labels match too.
    cats_en = [
        {"text": "National Visa for specialty cooks", "query": "categoryId=1&realmId=9"},
        {"text": "Waiting list for other employment (among others: "
                 "self-employment, internship, au pair)", "query": "categoryId=2&realmId=9"},
    ]
    out = gc.rk_termin_walk(_W(realms, cats_en), location_code="shan")
    assert out["category_id"] == "2"

    # No self-employment queue at this mission: first, never invented.
    out = gc.rk_termin_walk(_W(realms, cats[:1]), location_code="shan")
    assert out["category_id"] == "3180"

    # The applicant's explicit pick still beats the default.
    out = gc.rk_termin_walk(_W(realms, cats), location_code="shan", category_id="3175")
    assert out["category_id"] == "3175"
