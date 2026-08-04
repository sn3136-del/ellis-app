"""Dropdown selection against REAL browser DOM, on more than one framework.

Ellis carried Ant Design's class names as if they were "how dropdowns work".
Thailand's TDAC serves an Angular Material autocomplete, so Ellis read ZERO
options from a list that was visibly showing three, and the run stalled on a
field a person could see perfectly well. These tests drive the driver's own
select_search/list_options over both widget families in a real Chromium — the
only place a selector claim can actually be proved.

No portal is touched: the pages are built here.
"""
import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

from app.adapter_factory.live_driver import BrowserbasePageDriver  # noqa: E402

# Angular Material 15+ autocomplete: mat-option rows carrying role="option",
# the label in an .mdc-list-item__primary-text child, panel in a cdk overlay.
# Thailand lists nationalities as "CODE : NAME", so the typed code is one
# segment of a longer label.
MATERIAL_PAGE = """
<div class="mat-mdc-form-field">
  <input id="nationality" role="combobox" aria-autocomplete="list" autocomplete="off">
</div>
<div class="cdk-overlay-container"><div class="cdk-overlay-pane">
  <div id="panel" class="mat-mdc-autocomplete-panel" role="listbox" hidden></div>
</div></div>
<style>
  .mat-mdc-autocomplete-panel { max-height: 90px; overflow-y: auto; width: 300px; }
  mat-option { display: block; padding: 6px; }
</style>
<script>
  const ROWS = [['CHN','CHINESE'], ['HKG','CHINESE - HONG KOKG'],
                ['MAC','CHINESE - MACAO'], ['CHL','CHILEAN'],
                ['KOR','KOREAN (SOUTH)'], ['JPN','JAPANESE']];
  const input = document.getElementById('nationality'),
        panel = document.getElementById('panel');
  function render() {
    const q = input.value.trim().toUpperCase();
    const hits = ROWS.filter(([c, n]) => !q || c.startsWith(q) || n.includes(q));
    panel.innerHTML = '';
    if (!hits.length) {
      const none = document.createElement('mat-option');
      none.setAttribute('role', 'option');
      none.setAttribute('aria-disabled', 'true');
      none.className = 'mat-mdc-option mdc-list-item--disabled';
      none.innerHTML = '<span class="mdc-list-item__primary-text">No results found</span>';
      panel.appendChild(none);
    }
    for (const [code, name] of hits) {
      const o = document.createElement('mat-option');
      o.setAttribute('role', 'option');
      o.className = 'mat-mdc-option mdc-list-item';
      const label = code + ' : ' + name;
      o.innerHTML = '<span class="mdc-list-item__primary-text">' +
                    '<span class="hl">' + code + '</span> : ' + name + '</span>';
      o.addEventListener('click', () => {
        input.value = label; panel.hidden = true; window.__committed = label;
      });
      panel.appendChild(o);
    }
    panel.hidden = false;
  }
  input.addEventListener('input', render);
  input.addEventListener('focus', render);
</script>
"""

# Ant Design select: the label sits in an -option-content child INSIDE the
# -option row, so both match the option vocabulary and the row must not be
# counted twice. The committed value renders beside an always-empty input.
ANT_PAGE = """
<div class="ant-select" id="wrap">
  <span class="ant-select-selection-item" id="shown"></span>
  <input id="gate" class="ant-select-selection-search-input" role="combobox"
         autocomplete="off">
</div>
<div class="ant-select-dropdown ant-select-dropdown-hidden" id="dd">
  <div class="rc-virtual-list"><div class="rc-virtual-list-holder" id="holder"></div></div>
</div>
<style>
  .ant-select-dropdown-hidden { display: none; }
  .rc-virtual-list-holder { max-height: 80px; overflow-y: auto; width: 300px; }
  .ant-select-item-option { padding: 6px; }
</style>
<script>
  const GATES = ['Noi Bai Int Airport', 'Lao Cai Landport', 'Moc Bai Landport',
                 'Hai Phong Seaport', 'China', 'China(Taiwan)'];
  const q = document.getElementById('gate'), dd = document.getElementById('dd'),
        holder = document.getElementById('holder'),
        shown = document.getElementById('shown');
  function render() {
    const query = q.value.trim().toLowerCase();
    const hits = GATES.filter(g => !query || g.toLowerCase().includes(query));
    holder.innerHTML = '';
    if (!hits.length) {
      const e = document.createElement('div');
      e.className = 'ant-select-item ant-select-item-empty';
      e.textContent = 'No data';
      holder.appendChild(e);
    }
    for (const g of hits) {
      const row = document.createElement('div');
      row.className = 'ant-select-item ant-select-item-option';
      row.setAttribute('role', 'option');
      row.innerHTML = '<div class="ant-select-item-option-content">' + g + '</div>';
      row.addEventListener('click', () => {
        shown.textContent = g; q.value = '';
        dd.classList.add('ant-select-dropdown-hidden');
        window.__committed = g;
      });
      holder.appendChild(row);
    }
    dd.classList.remove('ant-select-dropdown-hidden');
  }
  q.addEventListener('input', render);
  q.addEventListener('focus', render);
</script>
"""


# A web-component combobox: the panel and its rows live in a shadow root,
# invisible to document.querySelectorAll. The applicant sees a list; an
# unguarded read sees nothing — the same silent stall, a different cause.
SHADOW_PAGE = """
<combo-box id="host"></combo-box>
<script>
  const CITIES = ['Bangkok', 'Chiang Mai', 'Phuket', 'Krabi'];
  class ComboBox extends HTMLElement {
    connectedCallback() {
      const r = this.attachShadow({mode: 'open'});
      r.innerHTML = '<input id="q" role="combobox" autocomplete="off">' +
                    '<div id="panel" role="listbox"></div>';
      const q = r.getElementById('q'), panel = r.getElementById('panel');
      const render = () => {
        const query = q.value.trim().toLowerCase();
        const hits = CITIES.filter(c => !query || c.toLowerCase().includes(query));
        panel.innerHTML = '';
        for (const c of hits) {
          const o = document.createElement('div');
          o.setAttribute('role', 'option');
          o.textContent = c;
          o.addEventListener('click', () => {
            q.value = c; window.__committed = c;
          });
          panel.appendChild(o);
        }
      };
      q.addEventListener('input', render);
      q.addEventListener('focus', render);
    }
  }
  customElements.define('combo-box', ComboBox);
</script>
"""


# Angular Material radio group: the native input is visually hidden under a
# styled wrapper, and the wrapper's aria-checked — not the input's DOM
# property — is what the applicant can see.
RADIO_PAGE = """
<div class="mat-mdc-radio-group" role="radiogroup" aria-label="Gender">
  <div class="mat-mdc-radio-button" id="w-f" aria-checked="false">
    <input id="r-f" type="radio" name="gender" value="F"><label for="r-f">FEMALE</label>
  </div>
  <div class="mat-mdc-radio-button" id="w-m" aria-checked="false">
    <input id="r-m" type="radio" name="gender" value="M"><label for="r-m">MALE</label>
  </div>
  <div class="mat-mdc-radio-button" id="w-u" aria-checked="false">
    <input id="r-u" type="radio" name="gender" value="U"><label for="r-u">UNDEFINED</label>
  </div>
</div>
<style> input[type=radio] { opacity: 0; position: absolute; } </style>
<script>
  for (const el of document.querySelectorAll('input[type=radio]')) {
    el.addEventListener('change', () => {
      for (const w of document.querySelectorAll('.mat-mdc-radio-button'))
        w.setAttribute('aria-checked', String(w.contains(document.activeElement)
          || (w.querySelector('input') || {}).checked === true));
      window.__committed = el.value;
    });
  }
</script>
"""

RADIO_OPTIONS = [{"label": "FEMALE", "selector": "#r-f"},
                 {"label": "MALE", "selector": "#r-m"},
                 {"label": "UNDEFINED", "selector": "#r-u"}]


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001 — no browser binary installed
            pytest.skip(f"chromium unavailable: {str(exc)[:80]}")
        yield b
        b.close()


def _driver(browser, html):
    page = browser.new_page()
    page.set_content(html)
    drv = BrowserbasePageDriver.__new__(BrowserbasePageDriver)
    drv.page = page
    return drv, page


def test_a_material_autocomplete_is_selectable(browser):
    """The Thailand stall: type the code, take the row the portal filtered to."""
    drv, page = _driver(browser, MATERIAL_PAGE)
    res = drv.select_search("#nationality", "CHN")
    assert res["ok"], res
    assert page.evaluate("() => window.__committed") == "CHN : CHINESE"


def test_a_code_matches_its_own_segment_not_a_neighbour(browser):
    """"CHN" must take "CHN : CHINESE", never the HKG/MAC rows that also
    carry the word CHINESE."""
    drv, page = _driver(browser, MATERIAL_PAGE)
    assert drv.select_search("#nationality", "JPN")["ok"]
    assert page.evaluate("() => window.__committed") == "JPN : JAPANESE"


def test_an_ant_select_still_selects(browser):
    """Vietnam's widget: the label lives in a child of the option row."""
    drv, page = _driver(browser, ANT_PAGE)
    res = drv.select_search("#gate", "Lao Cai")
    assert res["ok"], res
    assert page.evaluate("() => window.__committed") == "Lao Cai Landport"


def test_an_exact_match_beats_a_longer_one_containing_it(browser):
    """"China" must never commit "China(Taiwan)" — a near-miss on a
    government form is a wrong answer, not a shortcut."""
    drv, page = _driver(browser, ANT_PAGE)
    assert drv.select_search("#gate", "China")["ok"]
    assert page.evaluate("() => window.__committed") == "China"


@pytest.mark.parametrize("html,selector", [(MATERIAL_PAGE, "#nationality"),
                                           (ANT_PAGE, "#gate")])
def test_an_unmatched_value_commits_nothing(browser, html, selector):
    """No option matches: Ellis refuses rather than picking something."""
    drv, page = _driver(browser, html)
    res = drv.select_search(selector, "NOWHERELAND")
    assert not res["ok"] and res["code"] == "NO_OPTIONS"
    assert page.evaluate("() => window.__committed") in (None, "")


@pytest.mark.parametrize("html,selector,expected", [
    (MATERIAL_PAGE, "#nationality", "CHN : CHINESE"),
    (ANT_PAGE, "#gate", "Noi Bai Int Airport"),
])
def test_the_full_list_is_readable_after_a_query_matched_nothing(
        browser, html, selector, expected):
    """The runtime's REAL sequence: a value that matches nothing, then a read
    of the applicant's choices. The failed query is still filtering the
    widget, so an unguarded read offers the applicant an empty list — which
    is a pause with nothing to answer."""
    drv, page = _driver(browser, html)
    assert not drv.select_search(selector, "NOWHERELAND")["ok"]
    listed = drv.list_options(selector)
    assert listed["ok"], listed
    assert expected in (listed.get("options") or []), listed


@pytest.mark.parametrize("html,selector,placeholder", [
    (MATERIAL_PAGE, "#nationality", "No results found"),
    (ANT_PAGE, "#gate", "No data"),
])
def test_an_empty_state_row_is_never_a_choice(browser, html, selector, placeholder):
    """"No results found" is a message. Offering it as an option would put a
    status message into a government form."""
    drv, _ = _driver(browser, html)
    assert not drv.select_search(selector, "NOWHERELAND")["ok"]
    listed = drv.list_options(selector)
    assert placeholder not in (listed.get("options") or [])


def test_options_inside_a_web_component_are_still_found(browser):
    """A shadow root hides its rows from querySelectorAll. Ellis looks harder
    before claiming a dropdown the applicant can see is empty."""
    drv, page = _driver(browser, SHADOW_PAGE)
    # Playwright's own selectors pierce open shadow roots; the page scripts
    # Ellis evaluates do not, which is where the rows went missing.
    res = drv.select_search("#q", "Chiang")
    assert res["ok"], res
    assert page.evaluate("() => window.__committed") == "Chiang Mai"


@pytest.mark.parametrize("answer,expected", [
    ("FEMALE", "F"), ("MALE", "M"), ("F", "F"), ("male", "M"),
])
def test_a_radio_group_is_answered_by_the_portals_own_words(
        browser, answer, expected):
    """"F" answers FEMALE because the portal's word starts with it. The native
    input is hidden under a styled wrapper, so the click has to reach the
    control a person would actually press."""
    drv, page = _driver(browser, RADIO_PAGE)
    res = drv.select_radio(RADIO_OPTIONS, answer)
    assert res["ok"], res
    assert page.evaluate("() => window.__committed") == expected


def test_an_answer_no_radio_offers_is_never_clicked(browser):
    """A fixed set the answer is not on becomes an applicant question, with
    the portal's real choices — never the nearest-looking button."""
    drv, page = _driver(browser, RADIO_PAGE)
    res = drv.select_radio(RADIO_OPTIONS, "prefer not to say")
    assert not res["ok"] and res["code"] == "NO_OPTIONS"
    assert res["options"] == ["FEMALE", "MALE", "UNDEFINED"]
    assert page.evaluate("() => window.__committed") in (None, "")


def test_a_lone_radio_is_not_an_answer_to_whatever_was_asked(browser):
    """A dropdown filtered to one row IS the portal's resolution of the query;
    a one-button radio group was never filtered by anything."""
    drv, _ = _driver(browser, RADIO_PAGE)
    res = drv.select_radio([RADIO_OPTIONS[1]], "FEMALE")
    assert not res["ok"] and res["code"] == "NO_OPTIONS"


def test_a_committed_combobox_does_not_read_as_empty(browser):
    """Ant keeps the committed label BESIDE an always-empty input. Reading
    the input alone made every filled combobox look blank on resume, so Ellis
    re-typed fields the applicant had already answered."""
    drv, _ = _driver(browser, ANT_PAGE)
    assert drv.select_search("#gate", "Moc Bai Landport")["ok"]
    assert drv.read_value("#gate") == {"ok": True, "value": "Moc Bai Landport"}


# Angular Material 15+/MDC, as the framework really renders it: the native
# input is visually hidden at opacity 0 under a ripple/touch-target overlay
# that intercepts pointer events, and the checked state shows up as a class
# on the mat-radio-button, never as aria-checked on the input.
MDC_RADIO_PAGE = """
<mat-radio-group id="mat-radio-group-0" class="mat-mdc-radio-group">
  <mat-radio-button id="mat-radio-2" class="mat-mdc-radio-button">
    <div class="mdc-form-field"><div class="mdc-radio">
      <input id="mat-radio-2-input" class="mdc-radio__native-control" type="radio"
             name="mat-radio-group-0" value="FEMALE">
      <div class="mdc-radio__background"></div>
      <div class="mat-mdc-radio-touch-target"></div>
    </div><label for="mat-radio-2-input">FEMALE</label></div>
  </mat-radio-button>
  <mat-radio-button id="mat-radio-3" class="mat-mdc-radio-button">
    <div class="mdc-form-field"><div class="mdc-radio">
      <input id="mat-radio-3-input" class="mdc-radio__native-control" type="radio"
             name="mat-radio-group-0" value="MALE">
      <div class="mdc-radio__background"></div>
      <div class="mat-mdc-radio-touch-target"></div>
    </div><label for="mat-radio-3-input">MALE</label></div>
  </mat-radio-button>
  <mat-radio-button id="mat-radio-4" class="mat-mdc-radio-button">
    <div class="mdc-form-field"><div class="mdc-radio">
      <input id="mat-radio-4-input" class="mdc-radio__native-control" type="radio"
             name="mat-radio-group-0" value="UNDEFINED">
      <div class="mdc-radio__background"></div>
      <div class="mat-mdc-radio-touch-target"></div>
    </div><label for="mat-radio-4-input">UNDEFINED</label></div>
  </mat-radio-button>
</mat-radio-group>
<style>
  .mdc-radio { position: relative; display: inline-block; width: 40px; height: 40px; }
  .mdc-radio__native-control { position: absolute; inset: 0; opacity: 0; }
  .mdc-radio__background { position: absolute; top: 10px; left: 10px;
                           width: 20px; height: 20px; border: 1px solid #666; }
  /* The overlay that intercepts a real pointer click. */
  .mat-mdc-radio-touch-target { position: absolute; inset: -8px; z-index: 5; }
  mat-radio-button { display: inline-block; }
</style>
<script>
  for (const el of document.querySelectorAll('input[type=radio]')) {
    el.addEventListener('change', () => {
      for (const b of document.querySelectorAll('mat-radio-button'))
        b.classList.toggle('mat-mdc-radio-checked',
          (b.querySelector('input') || {}).checked === true);
      window.__committed = el.value;
    });
  }
</script>
"""

# What recon produced BEFORE cssPath learned that a radio group shares one
# name: every choice carrying the identical selector. The already-released
# Thailand adapter carries exactly this, so the driver must survive it.
COLLAPSED_OPTIONS = [
    {"label": "FEMALE", "selector": 'input[name="mat-radio-group-0"]'},
    {"label": "MALE", "selector": 'input[name="mat-radio-group-0"]'},
    {"label": "UNDEFINED", "selector": 'input[name="mat-radio-group-0"]'},
]
MDC_OPTIONS = [{"label": "FEMALE", "selector": "#mat-radio-2-input"},
               {"label": "MALE", "selector": "#mat-radio-3-input"},
               {"label": "UNDEFINED", "selector": "#mat-radio-4-input"}]


@pytest.mark.parametrize("want,expected", [
    ("MALE", "MALE"), ("FEMALE", "FEMALE"), ("UNDEFINED", "UNDEFINED"),
    ("M", "MALE"), ("F", "FEMALE"), ("male", "MALE"),
])
def test_a_real_mdc_radio_commits_the_answer_it_was_given(browser, want, expected):
    """The native input is invisible and covered by a ripple, so a pointer
    click cannot reach it — and MRZ hands Ellis 'M'/'F', never 'MALE'."""
    drv, page = _driver(browser, MDC_RADIO_PAGE)
    res = drv.select_radio(MDC_OPTIONS, want)
    assert res["ok"], res
    assert page.evaluate("window.__committed") == expected
    assert page.eval_on_selector(
        f"input[value='{expected}']",
        "el => el.closest('mat-radio-button')"
        ".classList.contains('mat-mdc-radio-checked')") is True


@pytest.mark.parametrize("want,expected", [("MALE", "MALE"), ("F", "FEMALE")])
def test_a_group_whose_choices_share_one_selector_is_resolved_by_its_words(
        browser, want, expected):
    """The shipped Thailand adapter's Gender group carries ONE selector for
    all three choices. Taking .first answers FEMALE while reporting MALE — a
    false statement on a government form. The page still says which button is
    which, so read that and click the right one; the already-released adapter
    is repaired without a rebuild."""
    drv, page = _driver(browser, MDC_RADIO_PAGE)
    res = drv.select_radio(COLLAPSED_OPTIONS, want)
    assert res["ok"], res
    assert page.evaluate("window.__committed") == expected


def test_a_group_ellis_cannot_tell_apart_is_refused_not_guessed(browser):
    """No labels, no values, one selector: nothing on the page distinguishes
    the choices. That is an applicant question, never a coin flip."""
    bare = """
    <div role="radiogroup">
      <input type="radio" name="mat-radio-group-0">
      <input type="radio" name="mat-radio-group-0">
      <input type="radio" name="mat-radio-group-0">
    </div>
    <script>document.querySelectorAll('input').forEach(
      el => el.addEventListener('change', () => window.__committed = 'something'));
    </script>
    """
    drv, page = _driver(browser, bare)
    res = drv.select_radio(COLLAPSED_OPTIONS, "MALE")
    assert res["ok"] is False
    assert res["code"] == "AMBIGUOUS_OPTION"
    assert page.evaluate("window.__committed || ''") == "", \
        "a refusal must not leave an answer on the form"


def test_no_answer_is_ever_reported_for_a_button_that_did_not_take(browser):
    """A framework that ignores the click must read as unanswered."""
    drv, page = _driver(browser, MDC_RADIO_PAGE)
    page.evaluate("document.querySelectorAll('input[type=radio]')"
                  ".forEach(el => el.addEventListener('click',"
                  " e => e.preventDefault(), true))")
    res = drv.select_radio(MDC_OPTIONS, "MALE")
    assert res["ok"] is False


@pytest.mark.parametrize("want", ["X", "", "OTHER"])
def test_an_answer_the_group_does_not_offer_becomes_a_question(browser, want):
    drv, page = _driver(browser, MDC_RADIO_PAGE)
    res = drv.select_radio(MDC_OPTIONS, want)
    assert res["ok"] is False and res["code"] == "NO_OPTIONS"
    assert res["options"] == ["FEMALE", "MALE", "UNDEFINED"]


# Thailand's Date of Birth: three Material autocompletes under one caption,
# each filtering asynchronously. One answer, three boxes.
DOB_PAGE = """
<div class="row">
  <input id="mat-input-18" role="combobox" aria-autocomplete="list" placeholder="yyyy" autocomplete="off">
  <input id="mat-input-19" role="combobox" aria-autocomplete="list" placeholder="mm" autocomplete="off">
  <input id="mat-input-20" role="combobox" aria-autocomplete="list" placeholder="dd" autocomplete="off">
</div>
<div class="cdk-overlay-container">
  <div class="mat-mdc-autocomplete-panel" role="listbox" id="panel" hidden></div>
</div>
<style> .mat-mdc-autocomplete-panel { max-height: 120px; overflow-y: auto; }
        mat-option { display: block; } </style>
<script>
  const YEARS = Array.from({length: 100}, (_, i) => String(1930 + i));
  const MONTHS = Array.from({length: 12}, (_, i) => String(i + 1).padStart(2, '0'));
  const DAYS = Array.from({length: 31}, (_, i) => String(i + 1).padStart(2, '0'));
  const SETS = {'mat-input-18': YEARS, 'mat-input-19': MONTHS, 'mat-input-20': DAYS};
  const panel = document.getElementById('panel');
  window.__committed = {};
  for (const id of Object.keys(SETS)) {
    const input = document.getElementById(id);
    const render = () => {
      const q = input.value.trim();
      panel.innerHTML = '';
      // The list arrives a beat late, exactly like a real Material panel.
      setTimeout(() => {
        if (document.activeElement !== input) return;
        panel.innerHTML = '';
        for (const v of SETS[id].filter(x => !q || x.startsWith(q))) {
          const o = document.createElement('mat-option');
          o.setAttribute('role', 'option');
          o.textContent = v;
          o.addEventListener('click', () => {
            input.value = v; panel.hidden = true; window.__committed[id] = v;
          });
          panel.appendChild(o);
        }
        panel.hidden = false;
      }, 120);
    };
    input.addEventListener('input', render);
    input.addEventListener('focus', render);
  }
</script>
"""

DOB_FIELDS = [("#mat-input-18", "1988"), ("#mat-input-19", "06"),
              ("#mat-input-20", "13")]


def test_a_split_date_commits_every_part(browser):
    """1988 / 06 / 13 — the day box is the one the live run died on."""
    drv, page = _driver(browser, DOB_PAGE)
    results = drv.select_search_many(DOB_FIELDS)
    assert [r["ok"] for r in results] == [True, True, True], results
    assert page.evaluate("window.__committed") == {
        "mat-input-18": "1988", "mat-input-19": "06", "mat-input-20": "13"}


def test_a_grouped_date_costs_one_round_trip_not_fifteen_per_box(browser):
    """The expense was never the work, it was ~15 sequential Playwright calls
    per field over a remote browser. Counted, not timed: wall-clock on a
    local fixture proves nothing about a link to Browserbase."""
    class _Counting:
        """Forwards to the real Playwright object, counting every call that
        crosses into the browser. Nested handles (page.keyboard) count into
        the same tally."""

        def __init__(self, target, tally):
            object.__setattr__(self, "_target", target)
            object.__setattr__(self, "_tally", tally)

        def __getattr__(self, name):
            attr = getattr(self._target, name)
            if not callable(attr):
                return _Counting(attr, self._tally)

            def counted(*a, **kw):
                self._tally.append(name)
                return attr(*a, **kw)
            return counted

    def _trips(html, run):
        drv, page = _driver(browser, html)
        tally: list = []
        drv.page = _Counting(page, tally)
        run(drv)
        return len(tally)

    grouped = _trips(DOB_PAGE, lambda d: d.select_search_many(DOB_FIELDS))
    one_by_one = _trips(DOB_PAGE, lambda d: [d.select_search(s, v)
                                             for s, v in DOB_FIELDS])
    assert grouped * 2 < one_by_one, (
        f"grouped {grouped} browser calls vs one-at-a-time {one_by_one}")


def test_a_part_that_cannot_commit_is_reported_never_assumed(browser):
    """A partly-filled date is a WRONG date. Each part reports for itself so
    the caller can fall back to the proven single-field path."""
    drv, page = _driver(browser, DOB_PAGE)
    results = drv.select_search_many(
        [("#mat-input-18", "1988"), ("#mat-input-19", "99")])
    assert results[0]["ok"] is True
    assert results[1]["ok"] is False and results[1]["code"] == "NO_OPTIONS"


def test_a_missing_box_never_reads_as_answered(browser):
    drv, page = _driver(browser, DOB_PAGE)
    results = drv.select_search_many([("#nope", "1988")])
    assert results[0]["ok"] is False
    assert results[0]["code"] == "NO_SUCH_ELEMENT"


def test_a_one_letter_answer_never_matches_by_substring(browser):
    """"M" is inside "FEMALE". Judged option by option — which is how older
    adapters lay out a choice group — that substring ticked FEMALE for a man.
    A code must earn an exact, segment or prefix match or none at all."""
    drv, page = _driver(browser, MDC_RADIO_PAGE)
    res = drv.select_radio([{"label": "FEMALE", "selector": "#mat-radio-2-input"}], "M")
    assert res["ok"] is False, res
    assert page.evaluate("window.__committed || ''") == ""


def test_a_longer_answer_still_matches_inside_a_label(browser):
    """The substring tier is what finds 'Bai' in 'Noi Bai Intl Airport'; only
    the one and two character cases are refused."""
    drv, page = _driver(browser, ANT_PAGE)
    res = drv.select_search("#gate", "Bai")
    assert res["ok"], res
    assert page.evaluate("window.__committed") == "Noi Bai Int Airport"
