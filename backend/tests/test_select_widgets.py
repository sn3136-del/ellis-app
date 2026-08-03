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
