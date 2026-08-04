"""What a portal means by "you must answer this", read from a REAL page.

Ellis trusted one signal — the native `required` attribute — and modern SPA
forms are exactly the ones that never set it. Angular validates in TypeScript
and draws a red asterisk beside the caption, so Thailand's TDAC recorded all
23 of its fields as OPTIONAL. Downstream that is not a cosmetic loss: a fill
node built from `required: false` is `mandatory: false`, the runtime SKIPS it
when the case has no answer, and known_missing_questions never asks. Ellis
therefore submitted a government form with a mandatory Occupation left blank
and never told the applicant it needed one (observed live 2026-08-03).

These run the real extraction JS in real Chromium, because the claim under
test is "what does this page say about itself", and only a browser can answer.
"""
import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

from app.portal.live_browser import _EXTRACT_JS  # noqa: E402

# TDAC's own shape: a grid whose caption cell holds "*Occupation" as literal
# text, with no `for`, no aria-required, and no required attribute anywhere.
TDAC_PAGE = """
<div class="row"><div class="col">*Family Name</div>
     <div class="col"><input formcontrolname="familyName"></div></div>
<div class="row"><div class="col">Middle Name</div>
     <div class="col"><input formcontrolname="middleName"
                             placeholder="Only letters A-Z are allowed"></div></div>
<div class="row"><div class="col">*Occupation</div>
     <div class="col"><input formcontrolname="occupation"></div></div>
<div class="row"><div class="col">*Gender</div>
  <div class="col">
    <mat-radio-group id="mat-radio-group-0" class="mat-mdc-radio-group">
      <div class="mat-mdc-radio-button">
        <input type="radio" id="g-f" name="mat-radio-group-0"><label for="g-f">FEMALE</label>
      </div>
      <div class="mat-mdc-radio-button">
        <input type="radio" id="g-m" name="mat-radio-group-0"><label for="g-m">MALE</label>
      </div>
    </mat-radio-group>
  </div></div>
"""

# The other three dialects of the same statement.
MARKER_PAGE = """
<mat-form-field class="mat-mdc-form-field">
  <mat-label>Passport Number<span class="mat-mdc-form-field-required-marker">*</span></mat-label>
  <input id="passport">
</mat-form-field>
<mat-form-field class="mat-mdc-form-field">
  <mat-label>Middle Name</mat-label><input id="middle">
</mat-form-field>
<label for="email">Email <abbr title="required">*</abbr></label><input id="email">
<label for="fax">Fax</label><input id="fax">
<div class="form-group required"><label for="phone">Phone</label><input id="phone"></div>
<div class="form-group"><label for="alt">Alternate phone</label><input id="alt"></div>
<label for="nat">Nationality</label><input id="nat" aria-required="true">
<label for="ref">Referral code</label><input id="ref">
"""

# A page whose only asterisk is prose. Marking a field required because a
# paragraph elsewhere has a footnote star would make Ellis interrogate the
# applicant about fields the portal never asked for.
PROSE_PAGE = """
<p class="note">* Processing times are indicative and may vary by season,
   especially during national holidays and the peak travel period.</p>
<div class="row"><div class="col">Nickname</div>
     <div class="col"><input id="nick"></div></div>
"""


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001 — no browser binary installed
            pytest.skip(f"chromium unavailable: {str(exc)[:80]}")
        yield b
        b.close()


def _observe(browser, html) -> dict:
    """field name/id -> its observed element record."""
    page = browser.new_page()
    try:
        page.set_content(html)
        raw = page.evaluate(_EXTRACT_JS)
    finally:
        page.close()
    out = {}
    for el in raw.get("elements", []):
        # Every radio in a group shares one `name`; key those by selector so
        # the group's members stay distinct.
        key = el.get("selector") if el.get("type") == "radio" else \
            (el.get("name") or el.get("selector"))
        out.setdefault(key, el)
    return out


# ---- the portal's own asterisk ---------------------------------------------

def test_an_asterisk_in_the_caption_cell_marks_the_field_required(browser):
    """The exact TDAC shape: no required attribute anywhere on the page."""
    els = _observe(browser, TDAC_PAGE)
    assert els["occupation"]["required"] is True
    assert els["familyName"]["required"] is True


def test_an_unmarked_field_stays_optional(browser):
    """Over-marking would interrogate the applicant about fields the portal
    never demanded — the caption is read, not assumed."""
    els = _observe(browser, TDAC_PAGE)
    assert els["middleName"]["required"] is False


def test_the_caption_is_still_reported_without_its_asterisk(browser):
    """The marker is a fact about the field, not part of its name."""
    els = _observe(browser, TDAC_PAGE)
    assert els["occupation"]["label"] == "Occupation"


def test_a_radio_group_is_required_by_its_question_not_its_answers(browser):
    """"FEMALE" carries no marker — "*Gender", asked once above the group,
    does. Read per-radio, the group looked entirely optional."""
    els = _observe(browser, TDAC_PAGE)
    radios = [e for e in els.values() if e.get("type") == "radio"]
    assert len(radios) == 2
    assert all(r["required"] for r in radios)
    assert {r["group_label"] for r in radios} == {"Gender"}


# ---- the other dialects ----------------------------------------------------

@pytest.mark.parametrize("field,expected", [
    ("passport", True),    # Material's own required marker span
    ("middle", False),
    ("email", True),       # <abbr title="required">
    ("fax", False),
    ("phone", True),       # a wrapper classed "required"
    ("alt", False),
    ("nat", True),         # aria-required, the signal that always worked
    ("ref", False),
])
def test_each_way_a_portal_marks_a_field(browser, field, expected):
    els = _observe(browser, MARKER_PAGE)
    assert els[field]["required"] is expected


def test_a_footnote_in_prose_never_marks_a_field(browser):
    """A long string containing a star is a sentence, not a marker."""
    els = _observe(browser, PROSE_PAGE)
    assert els["nick"]["required"] is False


# ---- the consequence the bug actually had ----------------------------------

def test_a_required_observation_survives_into_a_mandatory_flow_node(browser):
    """The whole point: recon -> mapping -> flow node. A field the portal
    marked required must reach the runtime as one it will ASK about rather
    than silently skip."""
    from app.adapter_factory.specgen import _deterministic_mapper, _page_roles, _skeleton_flow

    els = _observe(browser, TDAC_PAGE)

    class _Art:
        id = "art-1"
        page_key = "application_form"
        content_class = "public_page"
        structure = {"url_pattern": "https://tdac.immigration.go.th/arrival-card",
                     "elements": list(els.values()) + [
                         {"selector": "#go", "name": "go", "label": "Continue",
                          "type": "submit"}]}

    art = _Art()
    maps = _deterministic_mapper([art])
    occupation = [m for m in maps if m["ellis_field"] == "occupation"]
    assert occupation, "the portal's Occupation field was not mapped at all"
    assert occupation[0]["required"] is True

    for m in maps:
        m["kind"] = "text"
    nodes = _skeleton_flow("tdac.immigration.go.th", _page_roles(
        {"application_form": art}), maps)
    occ = [n for n in nodes if n.get("input_source") == "occupation"]
    assert occ and occ[0]["mandatory"] is True


# ---- a selector must name ONE control -------------------------------------
# Every member of a radio group shares its `name` by definition, so a
# [name="..."] selector names the QUESTION and can never name a choice. The
# shipped Thailand adapter carries input[name="mat-radio-group-0"] for FEMALE,
# MALE and UNDEFINED alike; the driver clicked the first match, set FEMALE,
# and reported that it had set MALE — on a government form.

RADIO_VALUES_PAGE = """
<div class="col">*Gender</div>
<mat-radio-group id="mat-radio-group-0">
  <input type="radio" id="mat-radio-2-input" name="mat-radio-group-0" value="FEMALE">
  <label for="mat-radio-2-input">FEMALE</label>
  <input type="radio" id="mat-radio-3-input" name="mat-radio-group-0" value="MALE">
  <label for="mat-radio-3-input">MALE</label>
  <input type="radio" id="mat-radio-4-input" name="mat-radio-group-0" value="UNDEFINED">
  <label for="mat-radio-4-input">UNDEFINED</label>
</mat-radio-group>
<input name="familyName">
"""


def test_each_radio_in_a_group_gets_its_own_selector(browser):
    els = _observe(browser, RADIO_VALUES_PAGE)
    radios = [e for e in els.values() if e.get("type") == "radio"]
    selectors = [r["selector"] for r in radios]
    assert len(selectors) == 3
    assert len(set(selectors)) == 3, f"choices share a selector: {selectors}"


def test_a_shared_name_selector_is_never_emitted_for_a_choice(browser):
    els = _observe(browser, RADIO_VALUES_PAGE)
    for e in els.values():
        if e.get("type") != "radio":
            continue
        assert e["selector"] != 'input[name="mat-radio-group-0"]'


def test_the_observed_selectors_really_address_one_element_each(browser):
    """The claim under test is 'this selector picks out this control', so it
    is checked against the page, not against a naming convention."""
    page = browser.new_page()
    try:
        page.set_content(RADIO_VALUES_PAGE)
        from app.portal.live_browser import _EXTRACT_JS
        raw = page.evaluate(_EXTRACT_JS)
        for el in raw.get("elements", []):
            sel = el["selector"]
            n = page.evaluate("s => document.querySelectorAll(s).length", sel)
            assert n == 1, f"{sel!r} matches {n} elements, not one"
    finally:
        page.close()


def test_a_unique_name_is_still_the_selector(browser):
    """The name rule is right for ordinary fields and must not be lost —
    it survives a re-render where a framework-generated id does not."""
    els = _observe(browser, RADIO_VALUES_PAGE)
    assert els["familyName"]["selector"] == 'input[name="familyName"]'


# ---- asking the page what a widget actually does ---------------------------
# TDAC's Date of Birth carries combobox ARIA but opens no list; Nationality on
# the same page opens a real one. Read from markup alone they are identical,
# and building both as SELECT_SEARCH killed three runs over one date.

PROBE_PAGE = """
<input id="nationality" role="combobox" aria-autocomplete="list" placeholder="Select or enter">
<input id="year" role="combobox" aria-autocomplete="list" placeholder="yyyy">
<div class="cdk-overlay-container">
  <div id="panel" class="mat-mdc-autocomplete-panel" role="listbox" hidden></div>
</div>
<script>
  const panel = document.getElementById('panel');
  // Nationality opens a real list. The year box carries the same ARIA and
  // opens nothing at all — exactly what the live run met.
  document.getElementById('nationality').addEventListener('focus', () => {
    panel.innerHTML = '';
    for (const c of ['CHN : CHINESE', 'CHL : CHILEAN']) {
      const o = document.createElement('mat-option');
      o.setAttribute('role', 'option');
      o.textContent = c;
      panel.appendChild(o);
    }
    panel.hidden = false;
  });
  // A real Material autocomplete closes when it loses focus. Without this the
  // first field's list stays on screen and every later probe reads it.
  document.getElementById('nationality').addEventListener('blur', () => {
    panel.hidden = true; panel.innerHTML = '';
  });
</script>
"""

# A panel that ignores Escape and blur. Ellis must say it does not know, not
# report the neighbour's list as this field's.
STUCK_PANEL_PAGE = """
<input id="first" role="combobox" aria-autocomplete="list">
<input id="second" role="combobox" aria-autocomplete="list" placeholder="yyyy">
<div role="listbox" id="stuck">
  <div role="option">Bangkok</div><div role="option">Phuket</div>
</div>
"""


def _probe(browser, html, selector):
    from app.portal.live_browser import LiveBrowserSession
    page = browser.new_page()
    try:
        page.set_content(html)
        sess = LiveBrowserSession.__new__(LiveBrowserSession)
        return sess._probe_one(page, selector)
    finally:
        page.close()


def test_a_field_that_opens_a_list_is_a_dropdown(browser):
    assert _probe(browser, PROBE_PAGE, "#nationality") == "options"


def test_a_field_that_opens_nothing_is_not(browser):
    """Identical ARIA, identical widget class, opposite truth."""
    assert _probe(browser, PROBE_PAGE, "#year") == "empty"


def test_an_unreachable_field_is_reported_unknown_never_guessed(browser):
    assert _probe(browser, PROBE_PAGE, "#does-not-exist") == "unknown"


def test_the_probe_records_no_answer_on_the_form(browser):
    """Read-only: nothing typed, nothing chosen. A build pass must not leave
    state on a government form."""
    page = browser.new_page()
    try:
        page.set_content(PROBE_PAGE)
        from app.portal.live_browser import LiveBrowserSession
        sess = LiveBrowserSession.__new__(LiveBrowserSession)
        sess._probe_one(page, "#nationality")
        assert page.eval_on_selector("#nationality", "el => el.value") == ""
        assert page.eval_on_selector("#year", "el => el.value") == ""
    finally:
        page.close()


def test_the_verdict_reaches_the_observation(browser):
    """observe() runs the probe over the page's selects and hangs the verdict
    on the element records the build reads."""
    from app.portal.live_browser import LiveBrowserSession
    page = browser.new_page()
    try:
        page.set_content(PROBE_PAGE)
        raw = page.evaluate(_EXTRACT_JS)
        sess = LiveBrowserSession.__new__(LiveBrowserSession)
        sess._probe_widgets(page, raw)
        by_id = {e["selector"]: e for e in raw["elements"]}
        assert by_id["#nationality"]["opens_list"] == "options"
        assert by_id["#year"]["opens_list"] == "empty"
    finally:
        page.close()


def test_a_list_that_will_not_close_is_reported_unknown(browser):
    """A page already showing options cannot say what focusing THIS field
    did. Reporting the neighbour's list as this field's is exactly how a
    date box came to be built as a dropdown."""
    assert _probe(browser, STUCK_PANEL_PAGE, "#second") == "unknown"


def test_one_field_s_open_list_never_becomes_its_neighbour_s_verdict(browser):
    """Probed in order, Nationality opens a real list and the year box opens
    nothing — the probe must not carry the first answer into the second."""
    from app.portal.live_browser import LiveBrowserSession
    page = browser.new_page()
    try:
        page.set_content(PROBE_PAGE)
        raw = page.evaluate(_EXTRACT_JS)
        sess = LiveBrowserSession.__new__(LiveBrowserSession)
        sess._probe_widgets(page, raw)
        by_id = {e["selector"]: e for e in raw["elements"]}
        assert by_id["#nationality"]["opens_list"] == "options"
        assert by_id["#year"]["opens_list"] == "empty"
    finally:
        page.close()


# A per-session token is not an identity. TDAC's radios carry encrypted
# values ("cXhw5LLgYCIM0sCqCyP+2A=="); a selector built on one is unique
# today and matches nothing tomorrow.
OPAQUE_VALUE_PAGE = """
<mat-radio-group id="g">
  <input type="radio" name="g" value="cXhw5LLgYCIM0sCqCyP+2A=="><label>FEMALE</label>
  <input type="radio" name="g" value="ar2hFYafLjtx0JUbCesHKg=="><label>MALE</label>
</mat-radio-group>
"""

READABLE_VALUE_PAGE = """
<mat-radio-group id="g">
  <input type="radio" name="g" value="FEMALE"><label>FEMALE</label>
  <input type="radio" name="g" value="MALE"><label>MALE</label>
</mat-radio-group>
"""


def test_a_readable_value_makes_a_good_selector(browser):
    els = _observe(browser, READABLE_VALUE_PAGE)
    sels = sorted(e["selector"] for e in els.values() if e.get("type") == "radio")
    assert sels == ['input[name="g"][value="FEMALE"]', 'input[name="g"][value="MALE"]']


def test_an_encrypted_value_is_never_baked_into_a_selector(browser):
    """Better a shared selector the driver resolves by label than a unique
    one that expires with the session."""
    els = _observe(browser, OPAQUE_VALUE_PAGE)
    for e in els.values():
        if e.get("type") == "radio":
            assert "cXhw" not in e["selector"] and "ar2hFY" not in e["selector"], \
                e["selector"]
