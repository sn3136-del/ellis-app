"""A date box a portal drives with its own picker, filled in a REAL browser.

Malaysia's MDAC records its Date of Birth as an ordinary text input with a
DD/MM/YYYY placeholder — recon cannot see that it is `readonly`, because
readonly is not something recon records. The live run typed at it five times,
about nine seconds each, and ended the applicant's application on a field
they could have filled themselves in two seconds (2026-08-04).

A readonly input is not a refusal to hold the answer. It is a refusal to be
TYPED into: the portal means for its own picker to write the value. So Ellis
writes it the way a picker does — assignment plus the events the form listens
for — and then believes the page rather than itself.
"""
import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

from app.adapter_factory.live_driver import BrowserbasePageDriver  # noqa: E402

PLAIN = '<input id="dob" type="text" placeholder="DD/MM/YYYY">'

READONLY = '<input id="dob" type="text" placeholder="DD/MM/YYYY" readonly>'

# jQuery-UI style: readonly, and the calendar it opens covers the input, so
# every later actionability check has to reach through it.
PICKER_OVERLAY = """
<input id="dob" type="text" placeholder="DD/MM/YYYY" readonly>
<div id="cal" style="display:none;position:absolute;top:0;left:0;
     width:400px;height:300px;background:#eee;z-index:99"></div>
<script>
  const el = document.getElementById('dob'), cal = document.getElementById('cal');
  for (const ev of ['focus', 'click'])
    el.addEventListener(ev, () => cal.style.display = 'block');
</script>
"""

# A React-style controlled input: a plain `el.value = x` is ignored, only the
# prototype's own setter is observed.
CONTROLLED = """
<input id="dob" type="text" placeholder="DD/MM/YYYY" readonly>
<script>
  const el = document.getElementById('dob');
  let committed = '';
  Object.defineProperty(el, 'value', {
    get() { return committed; },
    set(v) { committed = v; },
    configurable: true,
  });
</script>
"""

# The portal normalises what it was given. That is agreement, not refusal.
REFORMATS = """
<input id="dob" type="text" placeholder="DD/MM/YYYY">
<script>
  const el = document.getElementById('dob');
  el.addEventListener('change', () => { el.value = el.value.replace(/\\//g, '-'); });
</script>
"""

# The portal REJECTS what it was given and empties its own box. Ellis used to
# report ok here and walk on into a submit that could never pass.
CLEARS = """
<input id="dob" type="text" placeholder="DD/MM/YYYY">
<script>
  const el = document.getElementById('dob');
  el.addEventListener('change', () => { el.value = ''; });
</script>
"""

# Not readonly, and genuinely unfillable: disabled. Ellis must not invent a
# way past a control the portal switched off.
DISABLED = '<input id="dob" type="text" placeholder="DD/MM/YYYY" disabled>'


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001 — no browser binary installed
            pytest.skip(f"chromium unavailable: {str(exc)[:80]}")
        yield b
        b.close()


def _fill(browser, html, value="13/06/1988"):
    page = browser.new_page()
    try:
        page.set_content(html)
        drv = BrowserbasePageDriver.__new__(BrowserbasePageDriver)
        drv.page = page
        res = drv.fill("#dob", value)
        return res, page.eval_on_selector("#dob", "el => el.value")
    finally:
        page.close()


@pytest.mark.parametrize("name,html", [
    ("plain", PLAIN), ("readonly", READONLY),
    ("readonly under a calendar", PICKER_OVERLAY),
    ("controlled + readonly", CONTROLLED),
])
def test_a_date_the_applicant_gave_reaches_the_field(browser, name, html):
    res, shown = _fill(browser, html)
    assert res["ok"], (name, res)
    assert shown == "13/06/1988", (name, shown)


def test_a_portal_that_normalises_the_date_is_agreeing_not_refusing(browser):
    res, shown = _fill(browser, REFORMATS)
    assert res["ok"] and shown == "13-06-1988"
    assert res.get("reformatted") == "13-06-1988", \
        "the portal's own wording of the answer should be reported"


def test_a_portal_that_clears_the_field_is_never_reported_as_filled(browser):
    """The dangerous one: 'ok' on an empty box sends the run into a submit
    that cannot pass, and nothing downstream can tell."""
    res, shown = _fill(browser, CLEARS)
    assert res["ok"] is False
    assert res["code"] == "VALUE_NOT_ACCEPTED"
    assert shown == ""


def test_a_disabled_control_is_refused_not_forced(browser):
    """Readonly means 'my picker writes this'. Disabled means 'not now' —
    and writing to it anyway would be Ellis overruling the portal."""
    res, shown = _fill(browser, DISABLED)
    assert res["ok"] is False
    assert shown == ""


def test_a_sensitive_field_is_still_refused_outright(browser):
    page = browser.new_page()
    try:
        page.set_content('<input id="cardNumber" type="text">')
        drv = BrowserbasePageDriver.__new__(BrowserbasePageDriver)
        drv.page = page
        res = drv.fill("#cardNumber", "4111111111111111")
        assert res["ok"] is False
        assert res["code"] == "SENSITIVE_FIELD_AUTOMATION"
        assert page.eval_on_selector("#cardNumber", "el => el.value") == ""
    finally:
        page.close()


# ---- a choice is not text --------------------------------------------------
# Singapore's ICA arrival card gives its Sex group one node PER option
# (fill_sex_f_0 / _m_0 / _o_0), each fed the applicant's answer. A radio
# cannot be filled with text at all: Ellis spent 26 seconds failing at three
# of them and ended the run (2026-08-04).

SEX_GROUP = """
<div>
  <input type="radio" id="sex_F_0" name="sex" value="F"><label for="sex_F_0">FEMALE</label>
  <input type="radio" id="sex_M_0" name="sex" value="M"><label for="sex_M_0">MALE</label>
  <input type="radio" id="sex_O_0" name="sex" value="O"><label for="sex_O_0">OTHERS</label>
</div>
"""


def _fill_sel(browser, html, selector, value):
    page = browser.new_page()
    try:
        page.set_content(html)
        drv = BrowserbasePageDriver.__new__(BrowserbasePageDriver)
        drv.page = page
        res = drv.fill(selector, value)
        state = page.evaluate(
            "() => Object.fromEntries([...document.querySelectorAll('input')]"
            ".map(e => [e.id, e.checked]))")
        return res, state
    finally:
        page.close()


def test_the_option_that_is_the_answer_is_ticked(browser):
    res, state = _fill_sel(browser, SEX_GROUP, "#sex_M_0", "M")
    assert res["ok"], res
    assert state == {"sex_F_0": False, "sex_M_0": True, "sex_O_0": False}


def test_an_option_that_is_not_the_answer_is_left_alone(browser):
    """Not an error — and emphatically not something to tick anyway."""
    res, state = _fill_sel(browser, SEX_GROUP, "#sex_F_0", "M")
    assert res["ok"] is False
    assert res["code"] == "NOT_THIS_OPTION"
    assert not any(state.values()), "nothing may be ticked"


def test_the_applicant_s_own_word_answers_the_option(browser):
    """MRZ gives M/F; the portal's labels say MALE/FEMALE. Both must land."""
    for want in ("MALE", "male", "M"):
        res, state = _fill_sel(browser, SEX_GROUP, "#sex_M_0", want)
        assert res["ok"], (want, res)
        assert state["sex_M_0"] is True


def test_a_checkbox_answered_by_its_label_is_ticked(browser):
    html = ('<input type="checkbox" id="agree" value="YES">'
            '<label for="agree">I agree</label>')
    res, state = _fill_sel(browser, html, "#agree", "YES")
    assert res["ok"] and state["agree"] is True


# ---- the mask the field itself declares -------------------------------------

@pytest.mark.parametrize("html,expected", [
    ('<input id="d" placeholder="DD/MM/YYYY">', "DD/MM/YYYY"),
    ('<input id="d" placeholder="dd-mm-yyyy">', "DD-MM-YYYY"),
    ('<input id="d" title="YYYY/MM/DD">', "YYYY/MM/DD"),
    ('<div class="form-group"><input id="d"><small class="hint">DD MON YYYY</small></div>',
     "DD MON YYYY"),
    ('<input id="d" placeholder="Enter your date of birth">', ""),
    ('<input id="d" placeholder="Only letters A-Z are allowed">', ""),
    ('<input id="d">', ""),
])
def test_a_field_states_the_date_shape_it_wants(browser, html, expected):
    page = browser.new_page()
    try:
        page.set_content(html)
        drv = BrowserbasePageDriver.__new__(BrowserbasePageDriver)
        drv.page = page
        assert drv.declared_date_format("#d") == expected
    finally:
        page.close()


def test_a_declared_mask_turns_a_canonical_date_into_what_the_portal_asked_for(browser):
    """Singapore printed 'Date must be in DD/MM/YYYY format' at the applicant
    over an ISO passport expiry the adapter never declared a mask for."""
    from app import dates
    page = browser.new_page()
    try:
        page.set_content('<input id="d" placeholder="DD/MM/YYYY">')
        drv = BrowserbasePageDriver.__new__(BrowserbasePageDriver)
        drv.page = page
        fmt = drv.declared_date_format("#d")
        assert dates.to_portal("2027-05-17", fmt) == "17/05/2027"
    finally:
        page.close()
