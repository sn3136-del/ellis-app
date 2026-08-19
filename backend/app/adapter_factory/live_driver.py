"""Live Browserbase runtime driver (brief §19-§20, §28).

`BrowserbasePageDriver` adapts a live Playwright page (over a Browserbase CDP
session) to the deterministic `FlowRunner`'s duck-typed interface —
goto / fill / click / read_text / network_events / official_state. It is the
live counterpart of `SyntheticPortal` for the released-runtime path.

Safety, enforced here (not by policy):
- Sensitive selectors are NEVER typed; the driver refuses them so the runtime
  falls back to an applicant handoff.
- Navigation is confined to the adapter's hostname allowlist (fail closed).
- Network observation stores only §20-permitted, redacted evidence: method,
  normalized endpoint pattern, status, content-type, and TOP-LEVEL response
  KEY NAMES (values removed). Bodies, cookies, tokens, and query values are
  never captured or stored.
- No model is consulted; nothing here imports a provider or Kimi.
"""
from __future__ import annotations

import os
import re
from urllib.parse import urlparse

_SENSITIVE_SELECTOR = re.compile(
    r"(password|passcode|otp|one[-_]?time|cvv|cvc|card|pan|secret|token|captcha|pin|3ds|passkey)",
    re.IGNORECASE)

# Timeout budget, split by PURPOSE. Waiting for an element to exist deserves
# patience (pages and dialogs load); ACTING on an element that already exists
# does not — a control that refuses the action refuses it immediately, and a
# long action timeout only burns wall-clock before the fallback that works.
# Measured on a real run: ~15s wasted per readonly picker field, ~20 such
# fields per application.
_WAIT_MS = 8000      # element must appear
_ACT_MS = 2500       # element exists: fill / select / check it
_CLICK_MS = 6000     # click (navigation-bearing, slightly longer)

# ---------------------------------------------------------------------------
# One vocabulary for "an option in an open dropdown", shared by the picker
# (select_search) and the reader (list_options).
#
# ARIA comes first on purpose: every accessible combobox marks its rows
# role="option" and its panel role="listbox", whatever framework drew them.
# The class patterns after it are the widgets that get ARIA wrong or attach it
# late. Ellis previously carried only Ant Design's class names, so Angular
# Material's autocomplete — Thailand's TDAC — read as ZERO options while three
# were visibly on screen, and the run stalled on a dropdown a human could see.
# A framework-specific selector list is a per-portal outage waiting to happen.
_OPTION_JS = """
const OPTION_SEL = '[role="option"], mat-option, [class*="select-item-option"],'
  + '[class*="select-item"], [class*="option-content"], [class*="option-item"],'
  + '[class*="autocomplete"] li, [role="listbox"] li, [class*="dropdown"] li,'
  + '[class*="menu"] li, datalist option';
const PANEL_SEL = '[role="listbox"], [class*="autocomplete-panel"],'
  + '[class*="select-dropdown"], [class*="dropdown-menu"],'
  + '[class*="menu-surface"], [class*="dropdown"]';

function _visible(el) {
  if (!el || el.offsetParent === null) return false;
  if (el.getAttribute('aria-hidden') === 'true') return false;
  if (el.hidden) return false;
  return !/(^|[\\s-])hidden(\\s|$)/.test(String(el.className || ''));
}

// Portals built on web components keep their options inside a shadow root,
// where querySelectorAll cannot see them — the same silent "zero options on a
// list the applicant can see" that Angular Material produced. Walking every
// shadow root is expensive, so it is the FALLBACK when the light DOM shows
// nothing, never the path taken on a normal page.
function _deep(root, sel) {
  const out = Array.from(root.querySelectorAll(sel));
  if (out.length) return out;
  for (const host of root.querySelectorAll('*')) {
    if (host.shadowRoot) out.push(..._deep(host.shadowRoot, sel));
  }
  return out;
}

// Every select on a page keeps its own panel; only the OPEN one may be read.
// Reading the document's first match once declared an 83-entry list finished
// after 10 rows, because it had read a different, closed dropdown.
function _openPanel() {
  const open = _deep(document, PANEL_SEL).filter(_visible);
  return open.length ? open[open.length - 1] : null;
}

function _label(el) {
  // The row's own visible text — a framework that renders the label in a
  // child still yields it here. Fall back to the accessible name for rows
  // that draw their label some other way (icon + aria-label).
  const t = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
  return t || (el.getAttribute('aria-label') || el.getAttribute('title') || '').trim();
}

// A "no results"/"loading" row is a message, not a choice. Treating one as
// selectable would type a status message into a government form.
function _placeholder(el, label) {
  if (el.getAttribute('aria-disabled') === 'true' || el.hasAttribute('disabled'))
    return true;
  const cls = String(el.className || '') + ' ' +
              String(((el.parentElement || {}).className) || '');
  if (/empty|disabled|no-?data|no-?result|no-?option|loading|placeholder/i.test(cls))
    return true;
  return /^(loading|searching|no (results?|data|options?|matches)|nothing found)\\b/i
    .test(label);
}

// Rows, de-duplicated by ELEMENT: Ant nests an -option-content span inside
// its -option row, so both match OPTION_SEL and a text-keyed set would drop a
// genuine duplicate label instead of the nesting.
function _rows() {
  const panel = _openPanel();
  const scan = (root) => {
    const out = [], seen = new Set();
    for (const el of _deep(root, OPTION_SEL)) {
      if (!_visible(el)) continue;
      const row = el.closest('[role="option"], mat-option, [class*="select-item"],'
                             + '[class*="option-item"]') || el;
      if (seen.has(row)) continue;
      const label = _label(row);
      if (!label || _placeholder(row, label)) continue;
      seen.add(row);
      out.push([row, label]);
    }
    return out;
  };
  // Scoped read first; fall back to the document when the open panel cannot
  // be identified, so an unrecognised container is a slower read, not a
  // dropdown Ellis swears is empty.
  const scoped = panel ? scan(panel) : [];
  return scoped.length ? scoped : scan(document);
}

function _norm(s) {
  return String(s || '').toLowerCase()
    .replace(/[\\s\\u00a0]+/g, ' ').trim();
}

// "CHN : CHINESE" -> ["chn : chinese", "chn", "chinese"]. Split only on
// separators that genuinely divide a code from a name; a bare hyphen stays
// inside the word so "Guinea-Bissau" is never mistaken for "Guinea".
function _segments(label) {
  const n = _norm(label);
  const parts = n.split(/\\s*[:|\\/,()\\[\\]\\u2013]\\s*|\\s+-\\s+/)
                 .map(s => s.trim()).filter(Boolean);
  return parts.length > 1 ? [n].concat(parts) : [n];
}

// The match ladder, most precise first. Within a tier the portal's own order
// wins — it filtered and ranked these rows for the text we typed, so its
// first row is the answer a person reading the same list would pick.
// Below the ladder Ellis asks the applicant; it never settles for a
// near-miss ("China" must not select "China(Taiwan)").
function _pick(rows, want, allowSole) {
  const w = _norm(want);
  if (!w || !rows.length) return null;
  const exact = rows.find(([, t]) => _norm(t) === w);
  if (exact) return [exact, 'exact'];
  // Split date and count dropdowns disagree about padding: a month box may
  // list "5" where the canonical value is "05". Numbers are unambiguous, so
  // compare them as numbers rather than as text.
  if (/^\\d+$/.test(w)) {
    const n = Number(w);
    const num = rows.find(([, t]) => /^\\d+$/.test(_norm(t)) && Number(_norm(t)) === n);
    if (num) return [num, 'numeric'];
  }
  const seg = rows.find(([, t]) => _segments(t).indexOf(w) >= 0);
  if (seg) return [seg, 'segment'];
  const pre = rows.find(([, t]) => _norm(t).startsWith(w));
  if (pre) return [pre, 'prefix'];
  // A one or two character answer is a CODE, and a code that merely appears
  // somewhere inside a word is not evidence of anything: "M" is inside
  // "FEMALE". Judged option by option — an older adapter gives a choice group
  // one node each — that match ticked FEMALE for a man (2026-08-04). Short
  // answers must earn an exact, segment or prefix match or none at all.
  const sub = w.length > 2 ? rows.find(([, t]) => _norm(t).includes(w)) : null;
  if (sub) return [sub, 'contains'];
  // The portal narrowed its whole list to one row for THIS query — the
  // widget's own resolution of the text, not a guess by Ellis. Only for a
  // list the query filtered: a radio group of one is not an answer to
  // whatever the applicant said, so callers that pass a fixed set opt out.
  if (allowSole && rows.length === 1) return [rows[0], 'sole_option'];
  return null;
}
"""

# What a widget currently SHOWS, wherever it keeps it: an autocomplete writes
# the label into its own input; a styled select renders it in a selection
# element beside an input whose value stays ''. An empty input is therefore
# not an answer — it is a reason to look at what the widget renders.
_SHOWN_VALUE_JS = """(el) => {
  // A radio or checkbox CARRIES its option's value whether or not it is
  // chosen: #sex_F_0 reads "F" forever. Reporting that as "the field holds F"
  // let a resume shortcut skip the click that would have actually chosen
  // FEMALE, and the applicant watched the portal keep its own default
  // (2026-08-04, Singapore SGAC). For a choice, the held value is the value
  // only while the box is CHECKED.
  if (el.type === 'radio' || el.type === 'checkbox')
    return el.checked ? (el.value || '').trim() : '';
  // SGAC's chips are <button value="F">FEMALE</button> — probed live: no
  // radio, no aria, selection is a class. A button's value attribute is not a
  // held answer either; it is one only while the page marks the button chosen.
  if (el.tagName === 'BUTTON' || el.getAttribute('role') === 'button') {
    const marked = /select|active|checked|pressed/i.test(String(el.className || ''))
      || el.getAttribute('aria-pressed') === 'true'
      || el.getAttribute('aria-checked') === 'true';
    return marked ? ((el.value || '').trim() || (el.innerText || '').trim()) : '';
  }
  const own = (el.value || '').trim();
  if (own) return own;
  // Every layer from the widget ROOT down to the input can carry 'select' in
  // its class (ant-select > ant-select-selector > ant-select-selection-search
  // > input.ant-select-selection-search-input), so closest() lands on an
  // inner layer that holds no value node — and closest() can even return the
  // input itself. Climb to the OUTERMOST select-ish ancestor instead.
  let root = null, n = el.parentElement, hops = 0;
  while (n && hops < 6) {
    if (/select|combobox|dropdown|form-field/i.test(String(n.className || '')) ||
        n.getAttribute('role') === 'combobox') root = n;
    else if (root) break;
    n = n.parentElement; hops++;
  }
  const s = root && root.querySelector('[class*="selection-item"],'
    + '[class*="single-value"], [class*="selected-value"],'
    + '[class*="select-value"], [class*="value-text"]');
  if (s) return (s.innerText || '').trim();
  return root ? '' : (el.innerText || '').trim();
}"""


def _norm_prefix(a: str, b: str) -> int:
    """Length of the shared leading stem of two labels, case-insensitively."""
    a, b = str(a or "").lower().strip(), str(b or "").lower().strip()
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _parse_slot_datetime(raw: str) -> int | None:
    """Parse a portal slot's start time to a UTC epoch-ms integer, or None.
    Accepts ISO-8601 (with or without timezone/offset — a bare local time is
    read as UTC, which qualifying_slots then re-localises to the applicant's
    zone) and common 'YYYY-MM-DD HH:MM' forms. Deterministic; never guesses a
    date from a bare time, so an unparseable slot is dropped rather than
    booked at the wrong moment."""
    from datetime import datetime, timezone
    s = (raw or "").strip()
    if not s:
        return None
    # Normalise a trailing 'Z' and a space separator to ISO 'T'.
    iso = s.replace("Z", "+00:00")
    if " " in iso and "T" not in iso:
        iso = iso.replace(" ", "T", 1)
    for parse in (
        lambda: datetime.fromisoformat(iso),
        lambda: datetime.strptime(s, "%Y-%m-%dT%H:%M"),
        lambda: datetime.strptime(s, "%Y-%m-%d %H:%M"),
        lambda: datetime.strptime(s, "%d/%m/%Y %H:%M"),
    ):
        try:
            dt = parse()
        except (ValueError, TypeError):
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    return None


def sanitize_network_event(method: str, url: str, status: int, content_type: str,
                           body_text: str | None) -> dict:
    """Reduce a response to §20-permitted evidence. Top-level JSON key NAMES are
    kept; every value is discarded. Non-JSON bodies contribute no keys."""
    parsed = urlparse(url or "")
    keys: list[str] = []
    if body_text and "json" in (content_type or "").lower():
        try:
            import json
            obj = json.loads(body_text)
            if isinstance(obj, dict):
                keys = [str(k)[:60] for k in list(obj.keys())[:25]]
        except Exception:  # noqa: BLE001 — never let a body break redaction
            keys = []
    return {
        "method": (method or "").upper()[:8],
        "url": f"https://{parsed.netloc}{parsed.path}"[:300],  # query stripped
        "status": int(status or 0),
        "content_type": (content_type or "")[:100],
        "response_keys": keys,
        "category": "",   # the flow's evidence rules match on keys/status/host
    }


class BrowserbasePageDriver:
    def __init__(self, page, *, allowed_hostnames: list[str], state_probe=None):
        self.page = page
        self.allowed = [h.lower() for h in (allowed_hostnames or [])]
        self._events: list[dict] = []
        self._state_probe = state_probe
        self._wire_network()

    # ---- FlowRunner interface ----------------------------------------------
    def goto(self, url: str) -> dict:
        if not self._host_ok(url):
            return {"ok": False, "code": "OFF_ALLOWLIST"}
        try:
            resp = self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            status = resp.status if resp else 200
            if not self._host_ok(self.page.url):
                return {"ok": False, "code": "REDIRECTED_OFF_ALLOWLIST"}
            status = int(status)
            if status >= 500:
                # The government site itself is failing (502/503 from its own
                # front door). Nothing about this application is wrong and no
                # retry of OUR work can fix it — say which it is, so the
                # applicant is never told their case "needs a closer look"
                # when the portal is simply down.
                return {"ok": False, "code": "PORTAL_UNAVAILABLE", "status": status,
                        "detail": f"the official portal returned HTTP {status}"}
            return {"ok": 200 <= status < 400, "status": status}
        except Exception as e:  # noqa: BLE001
            msg = str(e)[:120]
            if "ERR_CONNECTION" in msg or "ERR_NAME_NOT_RESOLVED" in msg or \
                    "Timeout" in msg or "ERR_EMPTY_RESPONSE" in msg:
                return {"ok": False, "code": "PORTAL_UNAVAILABLE", "detail": msg}
            return {"ok": False, "code": "NAV_ERROR", "detail": msg}

    # ------------------------------------------------------------ overlays
    # A date picker does not close because the date is written. Malaysia's MDAC
    # calendar stayed open over State and City after the arrival date went in,
    # so every field beneath it was answering to a panel instead of the form,
    # and the applicant watched Ellis stall against its own calendar
    # (2026-08-04). Escape was pressed before a CLICK and nowhere else, which
    # is precisely backwards: the click is the one action that already scrolls
    # and retries. It is the quiet fills underneath that get swallowed.
    #
    # Cost matters here — this runs before every field — so nothing is
    # dismissed on suspicion. One JS call asks the page the only question that
    # matters: at this field's own coordinates, is the topmost element the
    # field? Anything else is a real cover, and only then is it cleared.
    _COVERED_JS = """(el) => {
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) return '';        // not laid out: nothing to cover
      const x = r.left + r.width / 2;
      const y = r.top + Math.min(r.height / 2, 12);
      if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) return '';
      const top = document.elementFromPoint(x, y);
      if (!top || top === el || el.contains(top) || top.contains(el)) return '';
      // A <label> sitting over its own control is not a cover — clicking it
      // focuses the field, which is what we wanted anyway.
      if (top.tagName === 'LABEL' && el.id && top.getAttribute('for') === el.id)
        return '';
      return (top.tagName + '.' + String(top.className || '')).slice(0, 60);
    }"""

    def _uncover(self, selector: str) -> None:
        """Clear whatever is sitting on top of this field, cheapest first.

        Deterministic and click-free: Ellis never clicks a blind coordinate on
        a government form to dismiss a panel — a stray click on a real control
        is how an application gets a wrong answer nobody typed. Escape, then
        the document-level mousedown every calendar and dropdown widget listens
        for, then a scroll that moves the field out from under. Silent when it
        cannot: the fill below will report the real failure."""
        try:
            covered = self.page.eval_on_selector(selector, self._COVERED_JS)
        except Exception:  # noqa: BLE001 — unreadable: let the action judge
            return
        if not covered:
            return
        for step in ("escape", "mousedown", "scroll"):
            try:
                if step == "escape":
                    self.page.keyboard.press("Escape")
                elif step == "mousedown":
                    # What a bootstrap/jQuery datepicker and most custom
                    # dropdowns actually close on. Dispatched on <body>, so it
                    # bubbles to document without landing on any control.
                    self.page.evaluate(
                        "() => { if (document.activeElement &&"
                        " document.activeElement.blur) document.activeElement.blur();"
                        " document.body.dispatchEvent(new MouseEvent('mousedown',"
                        " {bubbles: true})); }")
                else:
                    self.page.locator(selector).first.scroll_into_view_if_needed(
                        timeout=_ACT_MS)
                self.page.wait_for_timeout(80)
                if not self.page.eval_on_selector(selector, self._COVERED_JS):
                    return
            except Exception:  # noqa: BLE001 — try the next escalation
                continue

    def fill(self, selector: str, value: str) -> dict:
        # The deterministic runtime never fills a sensitive field, but refuse
        # here too (defense in depth) so a mis-generated node fails closed.
        if _SENSITIVE_SELECTOR.search(selector or ""):
            return {"ok": False, "code": "SENSITIVE_FIELD_AUTOMATION"}
        # Timeout budget: WAIT for the element (pages load), then act fast.
        # A readonly picker input rejects fill() immediately — a long fill
        # timeout only burns wall-clock before the keyboard fallback that
        # actually works (measured: 15s wasted on ~20 fields per run).
        try:
            self.page.locator(selector).first.wait_for(state="attached",
                                                       timeout=_WAIT_MS)
        except Exception as e:  # noqa: BLE001
            # A TIMEOUT here is proof: it waited the full budget and the field
            # never appeared. Every rung below needs that same element and will
            # each spend its own timeout re-discovering the same absence —
            # ~11s of an applicant's life. Say so now.
            #
            # Anything ELSE is the wait itself failing, which proves nothing
            # about the page: a driver whose locator has no wait_for, a page
            # closing, a protocol error. Treating those as "no such element"
            # made a fill that would have worked report failure and sent the
            # run to the applicant instead of onward.
            if "timeout" not in str(e).lower():
                pass                       # unproven: take the normal ladder
            else:
                return {"ok": False, "code": "NO_SUCH_ELEMENT",
                        "detail": "the field never appeared on the page"}
        self._uncover(selector)
        # A radio or checkbox holds a CHOICE, not text, and cannot be filled at
        # all. Older adapters mapped one node per option (Singapore's ICA card
        # has fill_sex_f_0 / _m_0 / _o_0, all fed the same answer), so each
        # option was "filled" with the applicant's sex in turn: the matching
        # one is the answer, the others are not, and none of them is text.
        # Ellis spent 26 seconds failing at three of them and ended the run.
        choice = self._choice_kind(selector)
        if choice:
            return self._choose_maybe_date(selector, str(value), choice)
        # A READONLY input is not a field that might accept typing — it is a
        # field the portal reserved for its own picker. Playwright refuses to
        # fill it, and the keyboard fallback types into something that cannot
        # receive characters, so both rungs are guaranteed to fail: ~11s per
        # date field (fill 2.5s + click 6s + read-back 2.5s) spent proving what
        # the DOM says outright. Malaysia's MDAC asks for three such dates.
        # Ask once, cheaply, and go straight to the write its widget uses.
        try:
            if self.page.eval_on_selector(
                    selector,
                    "el => el.readOnly === true || el.hasAttribute('readonly')"):
                return self._set_like_a_picker(
                    selector, str(value), "the field is readonly")
        except Exception:  # noqa: BLE001 — unreadable: take the normal ladder
            pass
        try:
            # Was a list already open before this fill? Then an open list
            # AFTER it proves nothing about this box — only a panel that
            # appears in RESPONSE to the typing marks a search-select.
            try:
                open_before = bool(
                    (self.page.evaluate(self._OPEN_LIST_JS) or {}).get("open"))
            except Exception:  # noqa: BLE001
                open_before = True          # unknowable: never hijack the fill
            self.page.fill(selector, str(value), timeout=_ACT_MS)
            # The page is the authority on what this box IS. If typing into it
            # opened an options list, this is a search-select wearing a text
            # input's clothes, and the text alone commits nothing — an option
            # must be clicked (or honestly reported unmatchable).
            if not open_before:
                self.page.wait_for_timeout(200)
                committed = self._commit_open_list(selector, str(value))
                if committed is not None:
                    return committed
            # Framework forms (rc-field-form/Ant Design) commit a controlled
            # input's value to their own store on blur. Without it the DOM
            # shows the text while the form still considers the field empty —
            # and the portal rejects the page with "please enter …".
            try:
                self.page.eval_on_selector(
                    selector,
                    "el => { el.dispatchEvent(new Event('change', {bubbles: true}));"
                    " el.blur(); }")
            except Exception:  # noqa: BLE001
                pass
            # A fill that "succeeded" is not a field that HOLDS the answer. A
            # portal that clears its own input on change (and plenty do, when
            # the text does not match the mask they wanted) left Ellis
            # reporting ok on an empty box and walking into a submit that
            # could never pass. Only emptiness is refused here — a portal
            # normalising 13/06/1988 to 13-06-1988 is agreeing with us, and
            # is reported as such rather than fought.
            kept = self._value_now(selector)
            if kept == "":
                return {"ok": False, "code": "VALUE_NOT_ACCEPTED",
                        "detail": "the portal cleared the field after filling it"}
            if kept is not None and kept != str(value):
                return {"ok": True, "reformatted": kept[:60]}
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            # Readonly picker inputs (e.g. Ant Design date pickers) refuse
            # fill(); they accept focused keyboard entry. Type, commit with
            # Enter, then READ BACK the value — success only on exact echo.
            try:
                self.page.click(selector, timeout=_CLICK_MS)
                self.page.keyboard.type(str(value), delay=25)
                self.page.keyboard.press("Enter")
                self.page.wait_for_timeout(300)
                got = self.page.input_value(selector, timeout=_ACT_MS)
                # A picker panel can linger after Enter and swallow the next
                # element's clicks — dismiss it before moving on.
                self.page.keyboard.press("Escape")
                if got == str(value):
                    return {"ok": True, "method": "typed"}
            except Exception as e2:  # noqa: BLE001
                e = Exception(f"{e} | {e2}")
            # Still nothing. A READONLY input is not a refusal to hold the
            # answer, it is a refusal to be TYPED into: the portal means for
            # its own picker to write the value, and a picker writes it by
            # assignment plus the events the form listens for. Malaysia's
            # MDAC date of birth is exactly this — recon can only see a text
            # input with a DD/MM/YYYY placeholder, and five attempts to type
            # into it ended the applicant's run (2026-08-04).
            #
            # This writes the applicant's OWN answer, in the portal's own
            # declared format, the way the portal's own widget would — and
            # then believes the page rather than itself: no read-back, no
            # success.
            return self._set_like_a_picker(selector, str(value), e)

    # A date box states the shape it wants, right on itself. Singapore's
    # ICA arrival card takes the passport expiry as DD/MM/YYYY and answered an
    # ISO date with "Date must be in DD/MM/YYYY format" — visible on the
    # applicant's own screen — because the adapter node declared no format and
    # the canonical value went in untranslated (2026-08-04). An adapter built
    # before its portal's masks were read is not a reason to write a date the
    # portal has just said it will not take.
    _DATE_MASK_JS = """(el) => {
      const own = [el.getAttribute('placeholder'), el.getAttribute('aria-placeholder'),
                   el.getAttribute('title'), el.getAttribute('data-format'),
                   el.getAttribute('data-date-format')];
      const ff = el.closest('mat-form-field, [class*="form-field"], .form-group, td, div');
      // A mask rendered beside the box (a hint span) counts too, but only a
      // SHORT neighbouring string — a paragraph mentioning a date does not.
      if (ff) {
        for (const h of ff.querySelectorAll(
            '[class*="hint"], [class*="help"], small, .form-text')) {
          const t = (h.textContent || '').trim();
          if (t && t.length <= 40) own.push(t);
        }
      }
      for (const raw of own) {
        if (!raw) continue;
        const m = String(raw).toUpperCase().match(
          /\\b(?:YYYY|YY|MM|DD|MON|MONTH)(?:[^A-Z0-9]{1,3}(?:YYYY|YY|MM|DD|MON|MONTH)){1,2}\\b/);
        if (m) return m[0];
      }
      return '';
    }"""

    def field_is_required(self, selector: str) -> bool:
        """Does the PAGE say this field must be answered?

        An adapter built before its portal's required markers could be read
        calls everything optional, and the runtime then skips a blank field
        silently. Thailand's released adapter says every one of its 23 fields
        is optional; the form itself draws a red asterisk beside Occupation,
        Date of Birth and Gender, and refuses to continue without them. The
        applicant watched Ellis step over a mandatory Occupation and say
        nothing (2026-08-04).

        Read from the page, the same way live_browser's recon reads it, so a
        stale adapter cannot make Ellis silent about a field the portal is
        about to reject. False when it cannot be read: this only ever ADDS a
        question the page asked for, never invents one.
        """
        js = """(el) => {
          const REQ_MARK = /(^|\\s)[*\\uff0a]|[*\\uff0a]\\s*$/;
          const REQ_CLASS = /required|mandatory|asterisk/i;
          const marked = (n) => {
            if (!n) return false;
            if (REQ_CLASS.test(String(n.className || ''))) return true;
            if (n.querySelector && n.querySelector(
                '[class*="required"], [class*="asterisk"], [class*="mandatory"],'
                + ' abbr[title*="equired"]')) return true;
            const t = (n.textContent || '').replace(/\\s+/g, ' ').trim();
            return t.length > 0 && t.length <= 80 && REQ_MARK.test(t);
          };
          if (el.required || el.getAttribute('aria-required') === 'true') return true;
          if (el.id) {
            const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
            if (marked(l)) return true;
          }
          const ff = el.closest('mat-form-field, [class*="form-field"]');
          if (ff && marked(ff.querySelector('mat-label, label'))) return true;
          if (marked(el.closest('label'))) return true;
          let w = el.parentElement;
          for (let i = 0; i < 3 && w; i++, w = w.parentElement) {
            if (w.querySelectorAll('input, select, textarea').length > 1) break;
            if (REQ_CLASS.test(String(w.className || ''))) return true;
          }
          // The caption cell beside the field, which is where a grid-layout
          // portal like TDAC puts its "*Occupation".
          let cell = el;
          for (let i = 0; i < 9 && cell && cell.parentElement; i++) {
            cell = cell.parentElement;
            const prev = cell.previousElementSibling;
            if (!prev) continue;
            if (prev.querySelector && prev.querySelector('input, select, textarea')) continue;
            const t = (prev.innerText || '').trim();
            if (t && t.length <= 60) return marked(prev);
          }
          return false;
        }"""
        try:
            return bool(self.page.eval_on_selector(selector, js))
        except Exception:  # noqa: BLE001 — unreadable is not "required"
            return False

    def declared_date_format(self, selector: str) -> str:
        """The date pattern this field says it wants ('DD/MM/YYYY'), or ''.

        Read from the page, never inferred from the value: a portal that has
        not said what it wants gets the canonical date and its own validation
        gets to speak.
        """
        try:
            got = self.page.eval_on_selector(selector, self._DATE_MASK_JS) or ""
        except Exception:  # noqa: BLE001
            return ""
        got = str(got).strip().upper()
        # Only a mask made ENTIRELY of date tokens and separators is one.
        import re as _re
        if not got or len(got) > 20:
            return ""
        if _re.fullmatch(r"(?:YYYY|YY|MONTH|MON|MM|DD|[^A-Z0-9]){3,}", got) is None:
            return ""
        return got if _re.search(r"(?:YYYY|YY|MONTH|MON|MM|DD)", got) else ""

    def _choice_kind(self, selector: str) -> str:
        """'radio' / 'checkbox' / 'button' when the target holds a choice,
        else ''.

        A BUTTON is a choice here by construction: this is only consulted by
        fill(), and an adapter that feeds an answer to a button means "press
        this if it is the answer" — SGAC's sex and arrival-date chips are
        literally <button value=F>FEMALE</button> (probed live 2026-08-04).
        Text was unfillable into them, so every chip died as
        VALUE_NOT_ACCEPTED and the applicant was asked what their own passport
        says."""
        try:
            return self.page.eval_on_selector(
                selector,
                "el => (el.type === 'radio' || el.type === 'checkbox') ? el.type"
                " : (el.tagName === 'BUTTON' || el.getAttribute('role') === 'button'"
                "    || el.getAttribute('role') === 'radio') ? 'button' : ''") or ""
        except Exception:  # noqa: BLE001
            return ""

    # The renderings a portal actually prints a date option in. DD/MM first:
    # every portal Ellis has met that offers date CHIPS (SGAC's three arrival
    # days) is day-first, and the same page declared DD/MM/YYYY on its typed
    # date boxes. MM/DD is tried last, so it can only ever match when no
    # day-first rendering did.
    _DATE_LABEL_FORMATS = ("DD/MM/YYYY", "D/M/YYYY", "DD-MM-YYYY",
                           "YYYY-MM-DD", "DD MON YYYY", "MM/DD/YYYY")

    def _choose_maybe_date(self, selector: str, want: str, kind: str) -> dict:
        """_choose, but a canonical ISO answer also recognises its own date in
        the portal's rendering.

        Singapore's arrival card offers the date as three radio chips labelled
        '04/08/2026'; the case's answer is '2026-08-04'. Textually those never
        match, so all three chips read as NOT_THIS_OPTION, the date was never
        chosen, and the applicant was asked to answer a question whose answer
        Ellis was holding (2026-08-04). Same date, spelled the portal's ways.
        """
        res = self._choose(selector, want, kind)
        from .. import dates
        if not dates.is_iso(want):
            return res
        if res.get("ok") or res.get("code") != "NOT_THIS_OPTION":
            return res
        for fmt in self._DATE_LABEL_FORMATS:
            spelled = dates.to_portal(want, fmt)
            if not spelled or spelled == want:
                continue
            res = self._choose(selector, spelled, kind)
            if res.get("ok") or res.get("code") != "NOT_THIS_OPTION":
                return res
        return res

    def _choose(self, selector: str, want: str, kind: str) -> dict:
        """Answer ONE option of a choice group with the applicant's answer.

        The option's own words decide: its value attribute, its label, the text
        beside it. A match is ticked and verified against what the page
        renders; a non-match is reported as NOT_THIS_OPTION so the caller can
        move on. Ellis never ticks an option it cannot read as the answer, and
        never reports a tick the page does not show.
        """
        js = "([sel, want]) => {" + _OPTION_JS + """
          const el = document.querySelector(sel);
          if (!el) return {code: 'NO_SUCH_ELEMENT'};
          const words = [];
          const v = el.getAttribute('value'); if (v) words.push(v);
          // A button chip wears its label on itself (<button>FEMALE</button>).
          if (el.tagName === 'BUTTON' || el.getAttribute('role') === 'button')
            words.push(_label(el));
          if (el.id) {
            const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
            if (l) words.push(_label(l));
          }
          // The wrapper's text speaks for THIS option only when the wrapper
          // holds no sibling. 'radio' in a class name is also how GROUP
          // containers are styled, and closest() walked up to one: its text
          // was "FEMALE MALE OTHERS", which starts with "F", so the F node
          // "matched" every group and the verifying click landed on the
          // container — which is how OTHERS got selected on an applicant
          // whose passport says F (2026-08-04, Singapore SGAC).
          const w = el.closest('label, [class*="radio"], [class*="checkbox"]');
          if (w && w !== el &&
              w.querySelectorAll('input[type=radio], input[type=checkbox]').length <= 1)
            words.push(_label(w));
          const rows = words.filter(Boolean).map((t, i) => [i, t]);
          return {code: 'OK', match: !!_pick(rows, want, false),
                  words: rows.map(([, t]) => t).slice(0, 4)};
        }"""
        try:
            got = self.page.evaluate(js, [selector, want]) or {}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": "NO_SUCH_ELEMENT", "detail": str(e)[:120]}
        if got.get("code") != "OK":
            return {"ok": False, "code": got.get("code") or "NO_SUCH_ELEMENT"}
        if not got.get("match"):
            # This option is simply not what the applicant said. Not an error,
            # and emphatically not something to tick anyway.
            return {"ok": False, "code": "NOT_THIS_OPTION",
                    "detail": f"{want[:20]!r} is not " + str(got.get("words") or [])[:60]}
        try:
            if kind == "button":
                # A chip button: click IT (there is no hidden input to check),
                # then believe only what the page renders — a selected-state
                # class or aria flag that appears on it. SGAC marks the chosen
                # chip `button_Selected` (probed live 2026-08-04).
                self.page.eval_on_selector(selector, "el => el.click()")
                self.page.wait_for_timeout(150)
                checked = self.page.eval_on_selector(
                    selector,
                    "el => /select|active|checked|pressed/i.test("
                    "String(el.className || ''))"
                    " || el.getAttribute('aria-pressed') === 'true'"
                    " || el.getAttribute('aria-checked') === 'true'")
            else:
                self.page.eval_on_selector(
                    selector, "el => { const l = el.id && document.querySelector("
                              "'label[for=\"' + CSS.escape(el.id) + '\"]');"
                              " (l || el).click(); if (!el.checked) el.click(); }")
                self.page.wait_for_timeout(120)
                checked = self.page.eval_on_selector(selector, "el => !!el.checked")
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": "NO_SUCH_ELEMENT", "detail": str(e)[:120]}
        if not checked:
            return {"ok": False, "code": "VALUE_NOT_ACCEPTED",
                    "detail": f"{want[:30]!r} did not stay selected"}
        return {"ok": True, "method": f"{kind}_choice"}

    # After typing into a field, did the page open a list of options for it?
    # Returns {open: bool, rows: [labels]} — the page's own answer to "is this
    # a search-select pretending to be a text box".
    _OPEN_LIST_JS = "() => {" + _OPTION_JS + """
      const panel = _openPanel();
      if (!panel) return {open: false, rows: []};
      return {open: true, rows: _rows().map(([, t]) => t).slice(0, 400)};
    }"""

    _PICK_OPEN_JS = "(want) => {" + _OPTION_JS + """
      const rows = _rows();
      if (!rows.length) return null;
      const hit = _pick(rows, want, true);
      if (!hit) return {labels: rows.map(([, t]) => t).slice(0, 12)};
      hit[0][0].click();
      return {chosen: hit[0][1], match: hit[1]};
    }"""

    def _commit_open_list(self, selector: str, value: str) -> dict | None:
        """A text fill that opened an options list has NOT answered anything
        yet — the widget commits on an option CLICK, and the typed text is
        only its filter. Singapore's nationality box took 'CHN', reported ok,
        and the page sat on 'No items found' with nothing committed; the save
        then failed on a field every checkpoint called filled (2026-08-04).

        Returns None when no list opened (a plain text box — the fill already
        stands). Otherwise commits the best option and reports it, retrying an
        ISO country code as the registry's display name and then as its
        leading stem — 'CHN' → 'China' → 'CHIN', which is how a list that says
        'CHINESE' gets found without Ellis ever guessing a row it cannot read
        as the answer.
        """
        try:
            state = self.page.evaluate(self._OPEN_LIST_JS) or {}
        except Exception:  # noqa: BLE001 — unreadable: let the fill stand
            return None
        if not state.get("open"):
            return None

        queries = [value]
        v = str(value).strip()
        if len(v) == 3 and v.isalpha():
            try:
                from ..visa_snapshot.registry import _country_index
                entry = (_country_index() or {}).get(v.upper()) or {}
                name = str(entry.get("name") or "").strip()
            except Exception:  # noqa: BLE001
                name = ""
            if name:
                # The name itself, then its shrinking stem: 'China' misses a
                # list that says 'CHINESE', 'CHIN' finds it.
                queries.append(name)
                queries.extend(name[:n] for n in (5, 4)
                               if len(name) > n)
        wanted = queries[1] if len(queries) > 1 else v

        for i, q in enumerate(queries):
            if i > 0:
                # REAL keystrokes, not fill(): the widget's filter listens to
                # key events, and a one-shot programmatic value change left
                # SGAC's list unfiltered while the checkpoint said the query
                # was typed (proved against the live page, 2026-08-04).
                #
                # Cleared with fill(""), never select-all+Delete: a widget
                # that eats the key combo keeps the old text and each retry
                # APPENDS — an applicant watched "ChinaChin…" pile up in their
                # nationality box (2026-08-04). And VERIFIED before matching:
                # if the box does not hold exactly the query, no option it
                # filtered can be trusted to belong to it.
                try:
                    self.page.fill(selector, "", timeout=_ACT_MS)
                    self.page.click(selector, timeout=_CLICK_MS)
                    self.page.keyboard.type(q, delay=40)
                    holds = self.page.eval_on_selector(
                        selector, "el => (el.value || '').trim()")
                    if (holds or "") != q:
                        continue
                except Exception:  # noqa: BLE001
                    break
            # The list loads asynchronously (debounce + a network round trip).
            # 250ms was inside SGAC's debounce; poll instead of guessing one
            # magic delay.
            got = None
            for _ in range(10):                      # up to ~1.5s per query
                self.page.wait_for_timeout(150)
                try:
                    got = self.page.evaluate(self._PICK_OPEN_JS,
                                             wanted if i else q)
                except Exception:  # noqa: BLE001
                    got = None
                if got:
                    break
            if got and got.get("chosen"):
                self.page.wait_for_timeout(150)
                return {"ok": True, "shown": got["chosen"][:60],
                        "match": got.get("match") or "exact"}
            labels = [str(x) for x in (got or {}).get("labels") or []]
            if labels:
                # No ladder match, but the filter came from the answer itself,
                # so a row SHARING ITS STEM is the list's own spelling of this
                # value — CHINESE for China. Among stem-sharers the shortest
                # row wins: 'CHINESE / HONG KONG SAR' is a different, more
                # specific answer the applicant did not give. A tie is real
                # ambiguity and fails to a question, never a coin flip.
                cand = [(l, _norm_prefix(l, wanted)) for l in labels]
                cand = [(l, n) for l, n in cand if n >= 4]
                if cand:
                    best = max(n for _, n in cand)
                    finalists = sorted((l for l, n in cand if n == best),
                                       key=len)
                    if len(finalists) == 1 or \
                            len(finalists[0]) < len(finalists[1]):
                        chosen = finalists[0]
                        try:
                            self.page.evaluate(
                                "(w) => {" + _OPTION_JS + """
                                  for (const [el, t] of _rows())
                                    if (t === w) { el.click(); return true; }
                                  return false; }""", chosen)
                            self.page.wait_for_timeout(150)
                            return {"ok": True, "shown": chosen[:60],
                                    "match": "stem"}
                        except Exception:  # noqa: BLE001
                            pass
        # Nothing committable. Clear the leftover filter text so the form is
        # not left holding a fragment nobody chose, and fail honestly.
        try:
            self.page.fill(selector, "", timeout=_ACT_MS)
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "code": "NO_OPTIONS",
                "detail": f"no option matches {v[:30]!r}"}

    def _value_now(self, selector: str):
        """What the field holds right now, or None when it cannot be read."""
        try:
            return self.page.eval_on_selector(
                selector, "el => (el.value === undefined || el.value === null)"
                          " ? null : String(el.value)")
        except Exception:  # noqa: BLE001
            return None

    def _set_like_a_picker(self, selector: str, value: str, prior) -> dict:
        """Write a value the way the field's own picker writes it, then verify
        against the page. Never used before a normal fill and a real keyboard
        entry have both been tried."""
        try:
            editable = self.page.eval_on_selector(
                selector, "el => el.readOnly === true || el.hasAttribute('readonly')")
        except Exception:  # noqa: BLE001
            return {"ok": False, "code": "NO_SUCH_ELEMENT",
                    "detail": str(prior)[:120]}
        if not editable:
            # Not readonly: something else refused this field, and inventing a
            # way past it would be guessing at the portal's intent.
            return {"ok": False, "code": "VALUE_NOT_ACCEPTED",
                    "detail": str(prior)[:120]}
        try:
            self.page.eval_on_selector(selector, """(el, v) => {
                el.focus();
                const fire = () => {
                    for (const t of ['input', 'change'])
                        el.dispatchEvent(new Event(t, {bubbles: true}));
                };
                // React (and anything else tracking the value on the node)
                // ignores a plain assignment — the PROTOTYPE's setter is what
                // its onChange observes. A widget that instead defines its own
                // value accessor is the mirror case and needs the plain one.
                // Try the framework path, then the plain path, and let the
                // field itself say which one it accepted.
                const d = Object.getOwnPropertyDescriptor(
                    Object.getPrototypeOf(el), 'value');
                if (d && d.set) { d.set.call(el, v); fire(); }
                if (String(el.value || '') !== v) { el.value = v; fire(); }
                el.blur();
            }""", value)
            self.page.wait_for_timeout(150)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": "NO_SUCH_ELEMENT", "detail": str(e)[:120]}
        kept = self._value_now(selector)
        if kept is None or kept == "":
            return {"ok": False, "code": "VALUE_NOT_ACCEPTED",
                    "detail": f"the portal kept {kept!r} for a readonly field"}
        if kept == value:
            return {"ok": True, "method": "picker_value"}
        return {"ok": True, "method": "picker_value", "reformatted": kept[:60]}

    def click(self, selector: str) -> dict:
        try:
            # A still-open dropdown/date overlay from the previous field can
            # cover the button and turn the click into a 15s timeout. Only
            # dismissed when it is genuinely in the way — an unconditional
            # Escape before every click also closes panels Ellis opened on
            # purpose.
            self._uncover(selector)
            # Text selectors can also match stale HIDDEN elements (e.g. a
            # dismissed modal's button still in the DOM) — click the first
            # VISIBLE match so the wait can't hang on an invisible node.
            target = self.page.locator(f"{selector} >> visible=true").first
            try:    # a button below the fold is not "unclickable"
                target.scroll_into_view_if_needed(timeout=_ACT_MS)
            except Exception:  # noqa: BLE001
                pass
            # A DISABLED control cannot be clicked — and must never be
            # reported as clicked. The portal disables its Next until the form
            # is valid; a "successful" click there sent the run onward
            # believing the page had advanced when it had not moved at all.
            try:
                if target.is_disabled(timeout=_ACT_MS):
                    return {"ok": False, "code": "DISABLED"}
            except Exception:  # noqa: BLE001 — unreadable: let the click judge
                pass
            target.click(timeout=_CLICK_MS)
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            msg = str(e).lower()
            if "timeout" in msg:
                return {"ok": False, "code": "TIMEOUT"}
            return {"ok": False, "code": "NO_SUCH_ELEMENT", "detail": str(e)[:120]}

    # Deterministic read of the PORTAL's own visible validation messages. Used
    # only to explain an advance the portal refused — Ellis reports what the
    # government form says instead of dead-ending the applicant.
    _VALIDATION_JS = """() => {
      const out = [];
      const sel = '[class*="form-item-explain-error"], [class*="explain-error"],'
                + '[class*="error-message"], [class*="invalid-feedback"],'
                + '[role="alert"], [class*="ant-form-item-explain"]';
      for (const el of document.querySelectorAll(sel)) {
        if (el.offsetParent === null) continue;
        const t = (el.textContent || '').trim();
        if (t && t.length < 200 && !out.includes(t)) out.push(t);
        if (out.length >= 12) break;
      }
      return out;
    }"""

    # A field carrying an error, paired with its own label — lets Ellis name
    # the exact question the portal is objecting to. Two passes: framework
    # error containers (Ant & friends), then portals that render a bare error
    # line under the widget with no error-classed form item at all (SGAC's
    # "Please fill in the field above" beneath a YES/NO chip group revealed
    # mid-fill, 2026-08-04). A flagged CHIP GROUP reports its own options
    # ({id,value,label} per chip) so the runtime can ask the applicant with
    # the portal's real choices and click the chosen chip on the resume.
    _INVALID_FIELDS_JS = """() => {
      const out = [];
      const seenIds = new Set();
      const textOf = (el) => (el.textContent || '').trim();
      const chipOptions = (root) => {
        const opts = [];
        for (const b of root.querySelectorAll(
            'button[value], [role="radio"], [role="button"][value]')) {
          if (b.offsetParent === null || !b.id) continue;
          const label = textOf(b);
          const value = (b.getAttribute('value') || label).trim();
          if (label && opts.length < 12) opts.push({id: b.id, value, label});
        }
        return opts.length >= 2 ? opts : null;
      };
      const items = document.querySelectorAll(
        '[class*="form-item-has-error"], [class*="has-error"]');
      for (const item of items) {
        if (item.offsetParent === null) continue;
        const ctrl = item.querySelector('input,select,textarea');
        const id = ctrl ? (ctrl.id || ctrl.name || '') : '';
        const labelEl = item.querySelector('label');
        const label = labelEl ? textOf(labelEl) : '';
        const errEl = item.querySelector('[class*="explain"], [role="alert"]');
        const message = errEl ? textOf(errEl) : '';
        if (id || label || message) { out.push({id, label, message}); if (id) seenIds.add(id); }
        if (out.length >= 12) return out;
      }
      // Pass 2: walk up from each visible error line to the box that holds
      // exactly one control or one chip group. A box with several visible
      // controls is a form-level banner ("There are missing or erroneous
      // field(s)") that names no single field — never flag through it.
      const errSel = '[class*="error"], [role="alert"], [class*="invalid"], [class*="danger"]';
      for (const err of document.querySelectorAll(errSel)) {
        if (err.offsetParent === null) continue;
        const message = textOf(err);
        if (!message || message.length > 200) continue;
        let box = err.parentElement, hit = null;
        for (let i = 0; i < 5 && box && box !== document.body; i++, box = box.parentElement) {
          const chips = chipOptions(box);
          if (chips) {
            if (chips.length > 6) break;   // a whole-form sweep, not one group
            hit = {chips, box}; break;
          }
          const vis = Array.from(box.querySelectorAll('input,select,textarea'))
            .filter((c) => c.offsetParent !== null);
          if (vis.length === 1) { hit = {ctrl: vis[0], box}; break; }
          if (vis.length > 1) break;
        }
        if (!hit) continue;
        // The question the widget answers usually lives on the enclosing
        // form group's own label, not inside the chip box (SGAC's "Sex as
        // indicated in passport" sits above the F/M/O chips).
        const groupLabel = (box2) => {
          const grp = box2.closest('[class*="form-group"], [class*="form-row"], fieldset');
          if (!grp) return '';
          const l = grp.querySelector('label, legend, [class*="form_title"], [class*="form-title"]');
          return l ? textOf(l) : '';
        };
        if (hit.chips) {
          const ids = hit.chips.map((o) => o.id);
          let p = ids[0];
          for (const s of ids.slice(1)) {
            let j = 0; while (j < p.length && p[j] === s[j]) j++;
            p = p.slice(0, j);
          }
          const id = p.replace(/[_-]+$/, '');
          if (!id || seenIds.has(id)) continue;
          // Prefer the group's label; fall back to the box's text minus the
          // error line and the chips' own words.
          let label = groupLabel(hit.box);
          if (!label) {
            label = textOf(hit.box);
            for (const t of [message].concat(hit.chips.map((o) => o.label))) {
              label = label.split(t).join(' ');
            }
            label = label.replace(/\\s+/g, ' ').trim();
          }
          seenIds.add(id);
          out.push({id, label: label.slice(0, 240), message, options: hit.chips});
        } else {
          // A single control is only "flagged" when the page itself marks it
          // invalid — a stray error-classed element near a healthy input must
          // not invent a complaint.
          const c = hit.ctrl;
          const invalid = c.matches(
            '.ng-invalid, [aria-invalid="true"], .is-invalid, [class*="error"]');
          if (!invalid) continue;
          const id = c.id || c.name || '';
          if (!id || seenIds.has(id)) continue;
          seenIds.add(id);
          const lab = document.querySelector('label[for="' + CSS.escape(id) + '"]');
          out.push({id, label: lab ? textOf(lab) : groupLabel(hit.box), message});
        }
        if (out.length >= 12) break;
      }
      return out;
    }"""

    def read_validation_errors(self) -> dict:
        """{messages: [...], fields: [{id,label,message}]} — the portal's own
        complaints, verbatim. Empty when the page shows none."""
        try:
            messages = self.page.evaluate(self._VALIDATION_JS) or []
        except Exception:  # noqa: BLE001
            messages = []
        try:
            fields = self.page.evaluate(self._INVALID_FIELDS_JS) or []
        except Exception:  # noqa: BLE001
            fields = []
        return {"ok": True, "messages": list(messages), "fields": list(fields)}

    # Markers real portals use for a challenge widget. Static strings — the
    # presence of a CAPTCHA is OBSERVED, never assumed from the flow.
    _CAPTCHA_JS = """(highlight) => {
      const sels = ['iframe[src*="recaptcha"]', 'iframe[src*="hcaptcha"]',
                    'iframe[title*="captcha" i]', '.g-recaptcha', '.h-captcha',
                    '[id*="captcha" i]', '[class*="captcha" i]',
                    'img[src*="captcha" i]', 'input[name*="captcha" i]'];
      // A widget the applicant cannot reach is not a challenge they can
      // solve. A modal notice leaves the whole form (CAPTCHA included) laid
      // out and visible underneath it, so size and offsetParent both still
      // pass — only a hit test tells the truth about what is on top.
      const reachable = (el, r) => {
        const x = r.left + r.width / 2, y = r.top + r.height / 2;
        if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) return true;
        const top = document.elementFromPoint(x, y);
        return !top || el.contains(top) || top.contains(el);
      };
      for (const s of sels) {
        for (const el of document.querySelectorAll(s)) {
          const r = el.getBoundingClientRect();
          if (r.width < 20 || r.height < 20) continue;
          if (el.tagName !== 'IFRAME' && el.offsetParent === null) continue;
          if (!reachable(el, r)) continue;
          if (highlight) {
            el.scrollIntoView({block: 'center', inline: 'center'});
            const box = (el.closest('div') || el);
            box.style.outline = '3px solid #e11d48';
            box.style.outlineOffset = '4px';
            box.setAttribute('data-ellis-focus', '1');
          }
          return {present: true, kind: s};
        }
      }
      return {present: false};
    }"""

    def captcha_state(self, highlight: bool = False) -> dict:
        """Is a challenge widget actually on the page right now? With
        highlight, the applicant's own view is scrolled to it and it is
        outlined — a visual aid only: no form value is touched."""
        try:
            res = self.page.evaluate(self._CAPTCHA_JS, bool(highlight)) or {}
            return {"ok": True, "present": bool(res.get("present")),
                    "kind": str(res.get("kind") or "")}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": "READ_ERROR", "detail": str(e)[:120]}

    # Page-truth form probe: every visible form field with its label, type,
    # required flag, and whether the PORTAL currently holds a value for it —
    # emptiness only, never the value itself. Powers pause classification
    # ("why did we stop here?"): unanswered questions on the live page become
    # in-Ellis applicant questions instead of a misleading fee pause.
    _FORM_STATE_JS = """() => {
      const sensitive = /(password|passcode|otp|one[-_]?time|cvv|cvc|card|\\bpan\\b|secret|token|captcha|\\bpin\\b|3ds|passkey)/i;
      const labelFor = (el) => {
        if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
        if (el.id) {
          const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
          if (l) return l.innerText;
        }
        const item = el.closest('[class*="form-item"], .form-group, .field');
        if (item) { const l = item.querySelector('label'); if (l) return l.innerText; }
        const p = el.closest('label'); if (p) return p.innerText;
        return el.placeholder || '';
      };
      const requiredFor = (el) => {
        if (el.required || el.getAttribute('aria-required') === 'true') return true;
        const item = el.closest('[class*="form-item"], .form-group, .field');
        if (!item) return false;
        if (item.querySelector('[class*="required"]')) return true;
        const l = item.querySelector('label');
        return !!(l && /[*＊]/.test(l.innerText || ''));
      };
      const optionLabelFor = (el) => {
        const wrap = el.closest('label');
        if (wrap) return (wrap.innerText || '').trim();
        if (el.id) {
          const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
          if (l) return (l.innerText || '').trim();
        }
        return (el.value || '').trim();
      };
      const out = [];
      const radioGroups = {};
      for (const el of document.querySelectorAll('input, select, textarea')) {
        const tag = el.tagName.toLowerCase();
        let type = (el.type || tag).toLowerCase();
        if (['hidden', 'button', 'submit', 'reset', 'file', 'image'].includes(type)) continue;
        if (el.disabled) continue;
        if (el.offsetParent === null) {
          // Custom-styled checkboxes/radios hide the native input and render
          // a styled twin: the control is REAL if its label/wrapper is
          // visible. Everything else hidden stays skipped.
          const widgetVisible = (type === 'checkbox' || type === 'radio') &&
            (() => { const w = el.closest('label, [class*="checkbox"], [class*="radio"], [class*="form-check"]');
                     return !!(w && w.offsetParent !== null); })();
          if (!widgetVisible) continue;
        }
        if (tag === 'input' && (type === 'text' || type === 'search' || type === '')) {
          if (el.getAttribute('role') === 'combobox' ||
              el.getAttribute('aria-autocomplete') === 'list' ||
              el.closest('[role="combobox"]')) type = 'combobox';
        }
        if (tag === 'select') type = 'select';
        const name = (el.name || '').slice(0, 80);
        const id = (el.id || '').slice(0, 80);
        if (type === 'radio') {
          // One entry per GROUP: the group's question label, every option's
          // own label, and whether any member is selected yet.
          if (!name) continue;
          const g = radioGroups[name] || (radioGroups[name] = {
            id, name, options: [], checked: false, required: false, label: '' });
          g.options.push(optionLabelFor(el).replace(/\\s+/g, ' ').slice(0, 80));
          g.checked = g.checked || el.checked;
          g.required = g.required || requiredFor(el);
          if (!g.label) {
            // The group's QUESTION label — a legend, or a label/heading that
            // is NOT one of the options' own wrapping labels (those would
            // give us "Yes" instead of "Did you buy insurance?").
            const item = el.closest('[class*="form-item"], .form-group, .field, fieldset');
            let l = item && item.querySelector('legend');
            if (!l && item) {
              for (const cand of item.querySelectorAll('label, [class*="label"]')) {
                if (!cand.querySelector('input') && (cand.innerText || '').trim()) { l = cand; break; }
              }
            }
            g.label = l ? (l.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 120) : '';
          }
          continue;
        }
        let label = (labelFor(el) || '').trim().replace(/\\s+/g, ' ').slice(0, 120);
        if (!label && type === 'checkbox') {
          // Declaration/consent statements usually sit NEXT to the box, not in
          // a <label>. Read the text bound to THIS box only — never a shared
          // container's full text (that would cross-attribute one box's
          // statement to a neighbour and could auto-tick the wrong box).
          const own = (el.closest('label') || null);
          let box = own;
          if (!box) {
            const cand = el.closest('[class*="form-check"], [class*="checkbox-item"], li');
            // Only trust a container that holds exactly ONE checkbox.
            if (cand && cand.querySelectorAll('input[type="checkbox"]').length === 1) box = cand;
          }
          if (box) {
            label = (box.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 220);
          } else {
            // Fall back to the immediate sibling text next to the box.
            const sib = el.nextElementSibling || el.parentElement;
            if (sib && (sib.innerText || '').length < 260)
              label = (sib.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 220);
          }
        }
        const placeholder = (el.placeholder || '').slice(0, 40);
        let empty = true;
        let options = [];
        if (type === 'select') {
          const sel = el.selectedIndex >= 0 ? el.options[el.selectedIndex] : null;
          empty = !sel || !(sel.value || '').trim() || sel.disabled;
          options = Array.from(el.options)
            .filter((o) => (o.value || '').trim() && !o.disabled)
            .map((o) => (o.label || o.text || '').trim()).filter(Boolean).slice(0, 60);
        } else if (type === 'combobox') {
          // The chosen value lives in the widget ROOT, and EVERY layer down
          // to the input carries 'select' in its class
          // (ant-select > ant-select-selector > ant-select-selection-search >
          //  input.ant-select-selection-search-input). A closest() match
          //  therefore lands on an inner layer that holds no value node, and
          //  the search input's own value is always '' — so every filled
          //  combobox read as permanently EMPTY (Ellis refilled and re-asked
          //  fields the applicant had already answered). Climb to the
          //  OUTERMOST select-ish ancestor and read the value there.
          let root = null, n = el.parentElement, hops = 0;
          while (n && hops < 6) {
            if (/select|combobox|dropdown/i.test((n.className || '').toString())) root = n;
            else if (root) break;
            n = n.parentElement; hops++;
          }
          const s = root && root.querySelector(
            '[class*="selection-item"], [class*="single-value"], [class*="selected-value"]');
          empty = !(s && (s.innerText || '').trim()) && !(el.value || '').trim();
        } else if (type === 'checkbox') {
          // ANCESTOR wrapper only (an Ant input's own class contains
          // 'checkbox'), and when a framework wrapper exists ITS state is
          // the truth — a raw-set input.checked with an unregistered
          // framework model must still classify as unticked.
          const wrap = el.parentElement ? el.parentElement.closest(
            '[class*="checkbox"], [class*="form-check"], [role="checkbox"]') : null;
          const ariaEl = (wrap && wrap.getAttribute('aria-checked') !== null) ? wrap
                         : (el.getAttribute('aria-checked') !== null ? el : null);
          if (ariaEl) empty = ariaEl.getAttribute('aria-checked') !== 'true';
          else if (wrap) empty = !/checked/i.test(wrap.className || '');
          else empty = !el.checked;
        } else {
          empty = !(el.value || '').trim();
        }
        // A checkbox's LABEL is a statement sentence, not a value: matching it
        // against the sensitive-value regex would drop a real declaration that
        // merely mentions "card"/"secret". Sensitivity for a checkbox rests on
        // its name/id only (a checkbox never carries a card/OTP value).
        const sens = type === 'password' || sensitive.test(name) || sensitive.test(id) ||
                     (type !== 'checkbox' && sensitive.test(label));
        out.push({ id, name, label, type, options, placeholder,
                   tag: tag,
                   required: requiredFor(el), empty,
                   sensitive: sens });
        if (out.length >= 80) break;
      }
      for (const name of Object.keys(radioGroups)) {
        const g = radioGroups[name];
        if (out.length >= 80) break;
        out.push({ id: g.id, name: g.name, label: g.label || g.options[0] || '',
                   type: 'radio', options: g.options.filter(Boolean).slice(0, 20),
                   placeholder: '', tag: 'input',
                   required: g.required, empty: !g.checked,
                   sensitive: sensitive.test(g.name) || sensitive.test(g.label || '') });
      }
      return out;
    }"""

    def page_probe(self) -> dict:
        """Everything pause-classification needs, in ONE round-trip: the
        challenge state, every visible field's shape/emptiness, and the
        portal's own validation complaints. Three separate evaluates cost
        three page round-trips on every classification pass."""
        combined = (
            "() => ({"
            f"  captcha: ({self._CAPTCHA_JS})(false),"
            f"  fields: ({self._FORM_STATE_JS})(),"
            f"  validation: {{messages: ({self._VALIDATION_JS})(),"
            f"                fields: ({self._INVALID_FIELDS_JS})()}}"
            "})")
        try:
            res = self.page.evaluate(combined) or {}
        except Exception:  # noqa: BLE001 — a bad page yields no classification
            return {"ok": False, "captcha": {"present": False}, "fields": [],
                    "validation": {"messages": [], "fields": []}}
        return {"ok": True,
                "captcha": {"present": bool((res.get("captcha") or {}).get("present"))},
                "fields": list(res.get("fields") or []),
                "validation": {
                    "messages": list((res.get("validation") or {}).get("messages") or []),
                    "fields": list((res.get("validation") or {}).get("fields") or [])}}

    def read_form_state(self) -> dict:
        """{fields: [{id,name,label,type,required,empty,sensitive,options}]}
        for every visible form control — presence and emptiness only, values
        are never read back."""
        try:
            fields = self.page.evaluate(self._FORM_STATE_JS) or []
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": "READ_ERROR", "detail": str(e)[:120]}
        return {"ok": True, "fields": list(fields)}

    def fill_observed_field(self, selector: str, value: str, kind: str = "text") -> dict:
        """Fill one field the PAGE (not the flow) told us about, with an
        answer the applicant just provided. Same sensitivity refusal as every
        other automated fill — payment fields go through the dedicated
        applicant-authorized path, never this one."""
        if _SENSITIVE_SELECTOR.search(selector or ""):
            return {"ok": False, "code": "SENSITIVE_FIELD_AUTOMATION"}
        if kind == "select":
            try:
                self.page.select_option(selector, label=str(value), timeout=_ACT_MS)
                return {"ok": True}
            except Exception:  # noqa: BLE001 — fall back to value match
                try:
                    self.page.select_option(selector, value=str(value), timeout=_ACT_MS)
                    return {"ok": True}
                except Exception as e2:  # noqa: BLE001
                    return {"ok": False, "code": "NO_SUCH_OPTION", "detail": str(e2)[:120]}
        if kind == "combobox":
            return self.select_search(selector, str(value))
        if kind == "radio":
            # Pick the group option whose OWN label matches the applicant's
            # choice (exact first, then containment) — never a positional guess.
            pick_js = """(args) => {
              const {sel, want} = args;
              const w = want.trim().toLowerCase();
              let fallback = null;
              for (const r of document.querySelectorAll(sel)) {
                if (r.type !== 'radio' || r.offsetParent === null) continue;
                const wrap = r.closest('label');
                const forLbl = r.id ? document.querySelector('label[for="' + CSS.escape(r.id) + '"]') : null;
                const t = ((wrap && wrap.innerText) || (forLbl && forLbl.innerText) ||
                           r.value || '').trim().toLowerCase();
                if (!t) continue;
                if (t === w) { r.click(); return {ok: true}; }
                if (!fallback && (t.includes(w) || w.includes(t))) fallback = r;
              }
              if (fallback) { fallback.click(); return {ok: true}; }
              return {ok: false};
            }"""
            try:
                res = self.page.evaluate(pick_js, {"sel": selector, "want": str(value)}) or {}
                if res.get("ok"):
                    return {"ok": True}
                return {"ok": False, "code": "NO_SUCH_OPTION"}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "code": "READ_ERROR", "detail": str(e)[:120]}
        return self.fill(selector, str(value))

    def apply_declaration_mark(self, selector: str) -> dict:
        """Tick the portal's declaration checkbox AFTER the applicant made the
        declaration themselves in Ellis (the signed in-app statement). This is
        transcription of the applicant's own recorded act — Ellis never
        declares on its own initiative. Uses the same human-mimicking,
        render-verified commit as every checkbox tick."""
        return self._commit_checkbox(selector)

    # ---- applicant-authorized payment fill ----------------------------------
    # Detection first tries the standards-track autocomplete tokens (cc-*),
    # then conservative name/id/label patterns. Field KINDS and selectors only
    # — never a value — cross this boundary.
    _PAYMENT_FORM_JS = """() => {
      const pats = {
        number: /(card.?(number|no)\\b|\\bpan\\b|cc.?num)/i,
        cvv: /(cvv|cvc|csc|security.?code)/i,
        expiry: /(exp|valid.?(thru|until))/i,
        holder: /(card.?holder|holder.?name|name.{0,12}on.{0,6}card)/i,
      };
      const auto = { 'cc-number': 'number', 'cc-csc': 'cvv', 'cc-exp': 'expiry',
                     'cc-exp-month': 'expiry_month', 'cc-exp-year': 'expiry_year',
                     'cc-name': 'holder' };
      const labelFor = (el) => {
        if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
        if (el.id) {
          const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
          if (l) return l.innerText;
        }
        const p = el.closest('label'); if (p) return p.innerText;
        return el.placeholder || '';
      };
      const fields = {};
      for (const el of document.querySelectorAll('input, select')) {
        if (el.offsetParent === null || el.disabled) continue;
        const type = (el.type || '').toLowerCase();
        if (['hidden', 'button', 'submit', 'checkbox', 'radio', 'file'].includes(type)) continue;
        const sel = el.id ? '#' + CSS.escape(el.id)
          : (el.name ? el.tagName.toLowerCase() + '[name="' + el.name + '"]' : '');
        if (!sel) continue;
        const hay = (el.name || '') + ' ' + (el.id || '') + ' ' + (labelFor(el) || '')
                    + ' ' + (el.placeholder || '');
        let kind = auto[(el.getAttribute('autocomplete') || '').toLowerCase()] || '';
        if (!kind) {
          if (pats.number.test(hay)) kind = 'number';
          else if (pats.cvv.test(hay)) kind = 'cvv';
          else if (pats.holder.test(hay)) kind = 'holder';
          else if (pats.expiry.test(hay)) {
            // A combined 'MM/YY' text input mentions BOTH month and year —
            // it must stay 'expiry' or it would be filled with the month only.
            const hasM = /month|\\bmm\\b/i.test(hay);
            const hasY = /year|\\byy(yy)?\\b/i.test(hay);
            if (el.tagName === 'SELECT') kind = (hasY && !hasM) ? 'expiry_year' : 'expiry_month';
            else if (hasM && hasY) kind = 'expiry';
            else if (hasY) kind = 'expiry_year';
            else if (hasM) kind = 'expiry_month';
            else kind = 'expiry';
          }
        }
        if (!kind || fields[kind]) continue;
        fields[kind] = { selector: sel, tag: el.tagName.toLowerCase(),
                         placeholder: (el.placeholder || '').slice(0, 20) };
      }
      return { present: !!fields.number, fields };
    }"""

    def _payment_frames(self) -> list:
        """Frames card details may EVER be typed into: the page itself plus
        child frames whose URL passes the adapter hostname allowlist. A
        cross-origin/injected/blank frame is refused outright — the same
        fail-closed boundary goto() enforces for navigation."""
        frames = [self.page]
        try:
            for f in self.page.frames:
                if f == self.page.main_frame:
                    continue
                try:
                    if self._host_ok(str(f.url or "")):
                        frames.append(f)
                except Exception:  # noqa: BLE001 — unreadable URL: refuse
                    continue
        except Exception:  # noqa: BLE001 — frame enumeration is best-effort
            pass
        return frames

    def read_payment_form(self) -> dict:
        """Locate the portal's own payment-card fields on the current page
        (top document first, then allowlisted child frames only). Returns
        field KINDS and selectors only. present=false when no card-number
        field exists — the caller then falls back to the applicant typing in
        the live view."""
        for idx, frame in enumerate(self._payment_frames()):
            try:
                res = frame.evaluate(self._PAYMENT_FORM_JS) or {}
            except Exception:  # noqa: BLE001
                continue
            if res.get("present"):
                return {"ok": True, "present": True, "frame": idx,
                        "fields": res.get("fields") or {}}
        return {"ok": True, "present": False, "fields": {}}

    def fill_payment_fields(self, form: dict, card: dict) -> dict:
        """Fill the applicant's OWN card details (provided explicitly for this
        purpose, transported via a one-time vault reference) into the portal's
        payment form. Deliberately bypasses the sensitive-selector refusal —
        this is the single applicant-authorized entry path, and it only ever
        types into the page or an ALLOWLISTED child frame (_payment_frames).
        Ellis never clicks Pay, never stores the values, and no value ever
        reaches an error message, checkpoint, or log. ok requires every
        critical detected field (number, cvv, an expiry) actually filled —
        a partial fill must never be reported as 'your details are entered'.
        Returns filled/missing field KINDS only."""
        fields = (form or {}).get("fields") or {}
        frame_idx = int((form or {}).get("frame") or 0)
        frames = self._payment_frames()
        if frame_idx >= len(frames):
            return {"ok": False, "filled": [], "missing": sorted(fields),
                    "code": "PAYMENT_FRAME_GONE"}
        target = frames[frame_idx]
        number = re.sub(r"\D", "", str(card.get("number") or ""))
        month = str(card.get("expiry_month") or "").zfill(2)
        year4 = str(card.get("expiry_year") or "")
        values: dict[str, str] = {}
        if number:
            values["number"] = number
        if card.get("cvv"):
            values["cvv"] = str(card["cvv"])
        if card.get("holder"):
            values["holder"] = str(card["holder"])
        if month and year4:
            values["expiry_month"] = month
            values["expiry_year"] = year4
            # Combined expiry inputs: MM/YY unless the placeholder shows YYYY.
            ph = str((fields.get("expiry") or {}).get("placeholder") or "")
            yy = year4 if "yyyy" in ph.lower() else year4[-2:]
            values["expiry"] = f"{month}/{yy}"
        # Select widgets label options unpredictably ('05', '5', '2033', '33'):
        # try each equivalent representation; the readback stays the judge.
        candidates = {
            "expiry_month": [month, month.lstrip("0") or month],
            "expiry_year": [year4, year4[-2:]],
        }
        filled, missing = [], []
        for kind, spec in fields.items():
            value = values.get(kind)
            if value is None:
                continue
            sel = spec.get("selector") or ""
            try:
                if spec.get("tag") == "select":
                    done = False
                    for cand in candidates.get(kind, [value]):
                        for by in ("value", "label"):
                            try:
                                target.select_option(sel, **{by: cand}, timeout=4000)
                                done = True
                                break
                            except Exception:  # noqa: BLE001 — next candidate
                                continue
                        if done:
                            break
                    if not done:
                        raise RuntimeError("no matching option")
                else:
                    target.fill(sel, value, timeout=_ACT_MS)
                    try:
                        target.eval_on_selector(
                            sel,
                            "el => { el.dispatchEvent(new Event('change', {bubbles: true}));"
                            " el.blur(); }")
                    except Exception:  # noqa: BLE001
                        pass
                filled.append(kind)
            except Exception:  # noqa: BLE001 — kind only, never the value
                missing.append(kind)
        # Every critical field the PAGE declares must be filled: number always;
        # cvv when detected; and when any expiry field is detected, the whole
        # expiry must have landed (combined, or month+year together).
        expiry_kinds = [k for k in ("expiry", "expiry_month", "expiry_year")
                        if k in fields]
        ok = "number" in filled \
            and not ("cvv" in fields and "cvv" not in filled) \
            and all(k in filled for k in expiry_kinds)
        return {"ok": ok, "filled": sorted(filled), "missing": sorted(missing)}

    # An UNCHECKED declaration/consent checkbox and a stable selector for it.
    # Text-matched on the box's own label (ancestor label / adjacent text),
    # so the "I hereby declare …" statement is found wherever it renders.
    _FIND_DECLARATION_JS = r"""() => {
      // TRUTHFULNESS declarations only — mirrors released_flow._DECLARATION_RE.
      // Bare 'hereby' is excluded on purpose: a 'hereby authorize/consent/
      // agree to the terms' box is the APPLICANT's to give, never auto-ticked,
      // and any consent/T&C language disqualifies a box outright (fail closed:
      // a refused box routes to the applicant's secure window, not to a tick).
      // Non-English declaration stems are FIRST-PERSON anchored ("je déclare",
      // "ich erkläre") — a bare stem like "erklär" matches every German
      // "-erklärung" compound (Datenschutzerklärung = privacy policy), which
      // names a document to accept, not a truthfulness declaration.
      const re = /(declar|je\s+déclare|ich\s+erkläre|erkläre\s+ich|perjury|\boath\b|cam\s*đoan|beyan\s*ederim|dengan\s+ini\s+menyatakan|заявляю|أقر|(information|statements?|details|answers?|above|foregoing)[^.]{0,60}(true|complete|correct|accurate)|(true|complete|correct|accurate)[^.]{0,40}(and|&)[^.]{0,20}(complete|correct|accurate|true))/i;
      const consent = /(agree|consent|authoriz|autoris|autoriz|acept|accept|terms|términos|condiciones|conditions|termos|bedingungen|zustimm|einverstanden|einverständnis|einwillig|datenschutz|akzept|confidentialit|privacidad|privacidade|gizlilik|kvkk|rıza|şart|koşul|syarat|persetujuan|menyetuju|setuju|privasi|согласен|согласи|условия|конфиденциальн|أوافق|موافق|الشروط|خصوصية|privacy|marketing|newsletter|subscribe|third[-\s]*part)/i;
      const out = [];
      const all = document.querySelectorAll('input[type="checkbox"]');
      all.forEach((el, idx) => {
        if (out.length >= 6) return;
        const wrap = el.parentElement ? el.parentElement.closest(
          '[class*="checkbox"], [class*="form-check"], [role="checkbox"]') : null;
        const ariaEl = (wrap && wrap.getAttribute('aria-checked') !== null) ? wrap
                       : (el.getAttribute('aria-checked') !== null ? el : null);
        let checked;
        if (ariaEl) checked = ariaEl.getAttribute('aria-checked') === 'true';
        else if (wrap) checked = /checked/i.test(wrap.className || '');
        else checked = !!el.checked;
        if (checked) return;
        // The statement can sit several ancestors up (label > span > text):
        // climb until text appears — exactly how a human's eye finds it.
        let text = '', n = el, hops = 0;
        while (n && hops < 4 && text.length < 30) {
          text = (n.innerText || '').trim(); n = n.parentElement; hops++;
        }
        if (!re.test(text) || consent.test(text)) return;
        const lab = el.closest('label');
        const vis = (lab && lab.offsetParent !== null) || el.offsetParent !== null;
        if (!vis) return;
        // The eVisa 'I hereby declare' box has NO id and NO name — requiring
        // one silently skipped it (the exact box the applicant kept pointing
        // at). The engine-syntax nth selector addresses ANY checkbox.
        let sel = '';
        if (el.id) sel = '[id="' + el.id + '"]';
        else if (el.name) sel = 'input[name="' + el.name + '"]';
        else sel = 'input[type="checkbox"] >> nth=' + idx;
        out.push({selector: sel, text: text.slice(0, 200)});
      });
      return {found: out.length > 0, boxes: out};
    }"""

    def find_declaration_checkbox(self) -> dict:
        """EVERY unchecked declaration box on the page. The eVisa form carries
        more than one ("Committed to declare temporary residence…" and the
        final "I hereby declare that the above statements are true…"): ticking
        only the first leaves Next disabled forever."""
        try:
            return self.page.evaluate(self._FIND_DECLARATION_JS) or {"found": False}
        except Exception:  # noqa: BLE001
            return {"found": False}

    def tick_declarations(self) -> int:
        """Tick every unchecked declaration box on the page, verified. No
        Next click here — the caller's own declared CLICK node retries the
        advance under all runtime guards. Returns how many committed."""
        found = self.find_declaration_checkbox()
        ticked = 0
        for box in (found.get("boxes") or []):
            sel = box.get("selector") or ""
            if not sel:
                continue
            try:
                if (self._commit_checkbox(sel) or {}).get("ok"):
                    ticked += 1
            except Exception:  # noqa: BLE001
                continue
        return ticked

    def page_signature(self) -> str:
        """A cheap identity fingerprint of the CURRENT page/step: URL plus the
        fields it renders. Two reads of an unmoved page (even one that just
        re-rendered validation errors) match; a real step change does not.
        Checked state is deliberately excluded — ticking a box must not read
        as movement. Empty string when unreadable (callers fall back)."""
        try:
            return str(self.page.evaluate("""() => {
              const ids = [];
              for (const el of document.querySelectorAll('input, select, textarea')) {
                if (ids.length >= 40) break;
                ids.push(el.id || el.name || el.type || el.tagName);
              }
              const h = document.querySelector('h1, h2, h3, [class*="title"]');
              return location.href + '|' +
                     (h ? (h.innerText || '').trim().slice(0, 80) : '') + '|' +
                     ids.join(',');
            }""") or "")
        except Exception:  # noqa: BLE001
            return ""

    # Words a portal's own advance control uses, exact-match on the control's
    # full trimmed text (never substring: "Next of kin" must not qualify).
    # Covers the portal languages of the seeded families.
    _ADVANCE_WORDS = (
        "next", "continue", "tiếp tục", "tiep tuc",
        "siguiente", "continuar", "suivant", "continuer",
        "próximo", "proximo", "seguinte", "weiter",
        "devam", "devam et", "ileri", "lanjut", "selanjutnya",
        "далее", "продолжить", "التالي", "次へ", "다음", "下一步", "ถัดไป")

    # Text normalization shared by the advance/notice matchers: lowercase,
    # then strip U+0307 — JS lowercases Turkish dotted İ to 'i' + combining
    # dot, which never equals the plain-ascii word ('İleri' vs 'ileri').
    _NORM_JS = "(s) => s.trim().toLowerCase().replace(/\\u0307/g, '')"

    def next_button_state(self) -> dict:
        """Is the page's advance button present, and can it act?"""
        try:
            res = self.page.evaluate("""(words) => {
              const norm = %s;
              for (const b of document.querySelectorAll('button, input[type="submit"]')) {
                if (b.offsetParent === null) continue;
                const t = norm((b.innerText || b.value || ''));
                if (words.includes(t))
                  return {present: true, disabled: !!b.disabled};
              }
              return {present: false};
            }""" % self._NORM_JS, list(self._ADVANCE_WORDS)) or {}
            return {"ok": True, **res}
        except Exception:  # noqa: BLE001
            return {"ok": False}

    def click_next_button(self) -> dict:
        """Click the page's own advance button (Next / Tiếp tục / Weiter /
        Suivant …) — used only on the applicant's explicit instruction paths
        (their typed captcha is the go-ahead for THIS page's advance)."""
        # NEVER a bare input[type=submit]: a text-blind submit click can fire
        # an arbitrary control (a Pay button, a search widget). Only controls
        # whose FULL trimmed text is an advance word qualify. The control is
        # scrolled into view and the click point is hit-tested — a click that
        # would land on an overlay is an honest NEXT_NOT_FOUND, never a blind
        # coordinate press.
        try:
            res = self.page.evaluate("""(words) => {
              const norm = %s;
              for (const b of document.querySelectorAll('button, input[type="submit"]')) {
                if (b.offsetParent === null) continue;
                const t = norm((b.innerText || b.value || ''));
                if (!words.includes(t)) continue;
                b.scrollIntoView({block: 'center'});
                const r = b.getBoundingClientRect();
                const x = r.left + r.width / 2, y = r.top + r.height / 2;
                const hit = document.elementFromPoint(x, y);
                if (!hit || !(b === hit || b.contains(hit)))
                  return {present: false, blocked: true};
                return {present: true, x, y};
              }
              return {present: false};
            }""" % self._NORM_JS, list(self._ADVANCE_WORDS)) or {}
            if not res.get("present"):
                return {"ok": False, "code": "NEXT_NOT_FOUND"}
            self.page.mouse.click(res["x"], res["y"])
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": "CLICK_FAILED", "detail": str(e)[:120]}

    # A blocking notice/confirmation dialog with its own Confirm control
    # (e.g. the eVisa "DECLARATION COMPLETED" notice carrying the document
    # code). Exact full-text match across the seeded families' portal
    # languages — never a substring — and ONLY inside a dialog-like
    # container: a page-wide scan would happily press a payment screen's
    # standalone "Confirmar" button, which is no notice at all.
    _NOTICE_JS = """() => {
      const words = ['confirm', 'xác nhận', 'xac nhan', 'confirmar',
                     'confirmer', 'bestätigen', 'onayla', 'konfirmasi',
                     'подтвердить', 'تأكيد', '確認', '확인', '确认'];
      const norm = (s) => s.trim().toLowerCase().replace(/\\u0307/g, '');
      const DIALOG = '[role="dialog"], [role="alertdialog"], .modal, ' +
                     '[class*="modal"], [class*="dialog"], [class*="notice"], ' +
                     '[class*="popup"], [class*="swal"], [class*="overlay"]';
      for (const b of document.querySelectorAll('button, a[role="button"]')) {
        if (b.offsetParent === null) continue;
        const dialog = b.closest(DIALOG);
        if (!dialog) continue;
        const t = norm(b.innerText || '');
        if (words.includes(t)) {
          const r = b.getBoundingClientRect();
          // The dialog's OWN text, so a caller judging success/failure reads
          // the notice and not the whole page behind it.
          return {present: true, x: r.left + r.width / 2, y: r.top + r.height / 2,
                  text: (dialog.innerText || '').slice(0, 4000)};
        }
      }
      return {present: false, text: ''};
    }"""

    def notice_state(self) -> dict:
        """Is a Confirm-style notice on screen right now?"""
        try:
            res = self.page.evaluate(self._NOTICE_JS) or {}
            return {"ok": True, "present": bool(res.get("present"))}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": "READ_ERROR", "detail": str(e)[:120]}

    def notice_text(self) -> dict:
        """The text of the blocking notice ALONE — never the page behind it.

        Without this the caller's only reader was read_text('body'), so a
        notice was judged by the whole page: eVisa's entry conditions say
        "Not FALLing under the cases of suspension from entry", the Spanish
        error stem `fall` matched inside "falling", and a perfectly good
        "DECLARATION COMPLETED" notice was read as a rejection — the
        applicant's correct CAPTCHA code came back "wasn't accepted"
        (2026-08-03).
        """
        try:
            res = self.page.evaluate(self._NOTICE_JS) or {}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": "READ_ERROR", "detail": str(e)[:120]}
        if not res.get("present"):
            return {"ok": False, "code": "NO_NOTICE"}
        return {"ok": True, "text": str(res.get("text") or "")}

    def confirm_notice(self) -> dict:
        """Click the notice's Confirm — a routine acknowledgement the
        applicant's flow explicitly continues through."""
        try:
            res = self.page.evaluate(self._NOTICE_JS) or {}
            if not res.get("present"):
                return {"ok": False, "code": "NO_NOTICE"}
            self.page.mouse.click(res["x"], res["y"])
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": "CLICK_FAILED", "detail": str(e)[:120]}

    def screenshot_png(self):
        """Full-page PNG of the CURRENT page — used once, at submission, to
        preserve the official confirmation exactly as the portal showed it."""
        try:
            return self.page.screenshot(full_page=True)
        except Exception:  # noqa: BLE001
            return None

    # The same static challenge markers _CAPTCHA_JS observes, as locators.
    # 'captcha' (a CUSTOM ELEMENT) comes from RK-Termin, which renders its
    # challenge as <captcha><div style="background:url(data:image/jpg...)">
    # — no <img> anywhere. Ordered most specific first.
    _CAPTCHA_WIDGETS = ('img[src*="captcha" i]', 'captcha', '.g-recaptcha',
                        '.h-captcha', '[id*="captcha" i]', '[class*="captcha" i]',
                        'iframe[src*="recaptcha"]', 'iframe[src*="hcaptcha"]')

    # How far to blow the page up before capturing the challenge. These are
    # small bitmaps; screenshotting at 1x gives the applicant a postage stamp.
    _CAPTCHA_ZOOM = 2.5

    def zoom_to_captcha(self, zoom: float = 0.0) -> bool:
        """Blow the page up around the challenge and scroll it into view.

        Purely presentational — it changes how the page is DISPLAYED, never
        what it says or does. Two things depend on it: the capture below comes
        back at usable resolution, and the applicant watching the live window
        can actually read the characters. Safe to fail."""
        z = zoom or self._CAPTCHA_ZOOM
        try:
            self.page.evaluate(
                "(z) => {"
                "  document.body.style.transformOrigin = 'top left';"
                "  document.body.style.zoom = z;"
                "  const sels = ['captcha', 'img[src*=\"captcha\" i]',"
                "                '[id*=\"captcha\" i]', '[class*=\"captcha\" i]'];"
                "  for (const s of sels) {"
                "    const el = document.querySelector(s);"
                "    if (el && el.scrollIntoView) {"
                "      el.scrollIntoView({block: 'center', inline: 'center'});"
                "      break;"
                "    }"
                "  }"
                "  return true;"
                "}", z)
            self.page.wait_for_timeout(250)
            return True
        except Exception:  # noqa: BLE001 — presentation only
            return False

    def captcha_screenshot_png(self):
        """PNG of the challenge widget itself (e.g. the digits image), so the
        applicant can read and solve it INSIDE Ellis. The image is shown, the
        HUMAN solves — Ellis never interprets it.

        The page is zoomed first so the capture is legible rather than a
        postage stamp, and left zoomed so the live window matches what Ellis
        is showing."""
        self.zoom_to_captcha()
        for sel in self._CAPTCHA_WIDGETS:
            try:
                loc = self.page.locator(f"{sel} >> visible=true").first
                if loc.count() == 0:
                    continue
                shot = loc.screenshot(timeout=8000)
                if shot:
                    return shot
            except Exception:  # noqa: BLE001
                continue
        return None

    def apply_captcha_answer(self, answer: str) -> dict:
        """Type the HUMAN's solved captcha answer into the portal's own
        captcha input — pure transcription of the applicant's solution
        (identical in kind to the declaration tick and the OTP code path).
        Ellis never reads, interprets, or solves the challenge itself. The
        value never reaches an error message or checkpoint."""
        for sel in ('input[name*="captcha" i]', 'input[id*="captcha" i]',
                    'input[placeholder*="captcha" i]'):
            try:
                loc = self.page.locator(f"{sel} >> visible=true").first
                if loc.count() == 0:
                    continue
                loc.fill(str(answer), timeout=8000)
                try:
                    self.page.eval_on_selector(
                        sel,
                        "el => { el.dispatchEvent(new Event('change', {bubbles: true}));"
                        " el.blur(); }")
                except Exception:  # noqa: BLE001
                    pass
                return {"ok": True}
            except Exception:  # noqa: BLE001 — code only, never the value
                continue
        return {"ok": False, "code": "CAPTCHA_INPUT_NOT_FOUND"}

    def settle(self, ms: int = 700) -> None:
        """Give the page a beat — framework validation (Ant explain-error)
        renders asynchronously after a refused click; reading too early sees
        a clean page and mistakes a silent refusal for success."""
        try:
            self.page.wait_for_timeout(int(ms))
        except Exception:  # noqa: BLE001
            pass

    def current_url(self) -> dict:
        try:
            return {"ok": True, "url": str(self.page.url or "")}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": "READ_ERROR", "detail": str(e)[:120]}

    def is_visible(self, selector: str) -> dict:
        """Is any element matching this selector currently visible? Used to
        tell 'the click never landed' from 'the page already moved on'."""
        try:
            n = self.page.locator(f"{selector} >> visible=true").count()
            return {"ok": True, "visible": bool(n)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": "READ_ERROR", "detail": str(e)[:120]}

    def force_click(self, selector: str) -> dict:
        """Last-resort click for a button an overlay intercepts: dispatch the
        event on the element itself. Reversible navigation only — the runtime
        never routes an irreversible node here.

        force=True bypasses Playwright's actionability checks — INCLUDING
        'enabled' — and eval click() ignores them entirely. Neither may be
        aimed at a DISABLED control: that reports a successful click on a
        button which cannot act, and the run proceeds believing the page
        advanced when it never moved."""
        try:
            loc = self.page.locator(f"{selector} >> visible=true").first
            if loc.is_disabled(timeout=_ACT_MS):
                return {"ok": False, "code": "DISABLED"}
        except Exception:  # noqa: BLE001 — unreadable: let the click judge
            loc = self.page.locator(f"{selector} >> visible=true").first
        try:
            loc.click(timeout=_CLICK_MS, force=True)
            return {"ok": True, "method": "force"}
        except Exception:  # noqa: BLE001
            try:
                if self.page.eval_on_selector(selector, "el => !!el.disabled"):
                    return {"ok": False, "code": "DISABLED"}
                self.page.eval_on_selector(selector, "el => el.click()")
                return {"ok": True, "method": "dispatch"}
            except Exception as e2:  # noqa: BLE001
                return {"ok": False, "code": "CLICK_INTERCEPTED",
                        "detail": str(e2)[:120]}

    def read_value(self, selector: str) -> dict:
        """The value the portal currently holds in a field. Used to detect a
        value the FORM dropped (a dependent select can reset a field that was
        filled earlier) so it can be repaired instead of re-asked.

        A styled select keeps its committed value BESIDE the input, and the
        input's own value stays '' — so an empty string is not an answer here,
        it is a reason to look at what the widget renders. Returning it made
        every filled combobox read as blank on resume."""
        try:
            val = self.page.eval_on_selector(selector, _SHOWN_VALUE_JS)
            return {"ok": True, "value": str(val or "")}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": "NO_SUCH_ELEMENT", "detail": str(e)[:120]}

    def read_text(self, selector: str) -> dict:
        try:
            el = self.page.query_selector(selector)
            if el is None:
                return {"ok": False, "code": "NO_SUCH_ELEMENT"}
            return {"ok": True, "text": (el.inner_text() or "")[:200]}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": "READ_ERROR", "detail": str(e)[:120]}

    # ---- appointment inventory (read-only) + booking -----------------------
    # Reads bookable slots from the portal's OWN calendar/list DOM using the
    # adapter's DECLARED selectors — never guessed. Read-only: observing
    # available slots reserves nothing. Each slot carries the portal's own
    # slot handle (a data-attr / value the book step later clicks), the
    # location id/label, and a parsed start time — enough for qualifying_slots
    # to rank and for the applicant to choose.
    _SLOT_READ_JS = r"""(cfg) => {
      const rows = Array.from(document.querySelectorAll(cfg.slotSelector || ''));
      const out = [];
      for (const el of rows) {
        if (el.offsetParent === null) continue;              // not visible
        const disabled = el.matches('[disabled], [aria-disabled="true"], '
          + '.disabled, [class*="disabled"], [class*="unavailable"], '
          + '[class*="booked"], [class*="full"]');
        if (disabled) continue;                              // not bookable
        const handle = (cfg.handleAttr && el.getAttribute(cfg.handleAttr))
          || el.getAttribute('data-slot') || el.getAttribute('data-datetime')
          || el.getAttribute('data-time') || el.getAttribute('value') || '';
        const dt = el.getAttribute('data-datetime') || el.getAttribute('data-date')
          || el.getAttribute('datetime')
          || (el.querySelector('time') && el.querySelector('time').getAttribute('datetime'))
          || '';
        const loc = cfg.locationSelector
          ? ((document.querySelector(cfg.locationSelector) || {}).value
             || (document.querySelector(cfg.locationSelector) || {}).getAttribute?.('data-location')
             || '')
          : (el.getAttribute('data-location') || el.getAttribute('data-center') || '');
        out.push({handle: String(handle).slice(0, 200),
                  datetime: String(dt).slice(0, 40),
                  text: (el.innerText || '').trim().slice(0, 80),
                  location: String(loc).slice(0, 120)});
        if (out.length >= 400) break;                        // bounded read
      }
      return out;
    }"""

    def read_appointment_slots(self, node: dict) -> dict:
        """Read bookable slots from the portal's live calendar. `node` carries
        the adapter's declared slot_selector (+ optional handle_attr,
        location_selector). No selector -> nothing observed (fail closed);
        a slot is emitted only if its start time parses to a real timestamp."""
        sel = str(node.get("slot_selector") or "").strip()
        if not sel:
            return {"ok": True, "slots": []}
        cfg = {"slotSelector": sel,
               "handleAttr": node.get("handle_attr") or "",
               "locationSelector": node.get("location_selector") or ""}
        try:
            raw = self.page.evaluate(self._SLOT_READ_JS, cfg) or []
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": "SLOT_READ_ERROR", "detail": str(e)[:120]}
        slots = []
        for r in raw:
            start = _parse_slot_datetime(r.get("datetime") or r.get("text") or "")
            if start is None or not r.get("handle"):
                continue        # only offer a slot Ellis can actually book
            slots.append({"slotId": r["handle"], "startUtc": start,
                          "locationId": (r.get("location") or "").strip() or "default",
                          "label": r.get("text") or ""})
        return {"ok": True, "slots": slots}

    def book_appointment_slot(self, node: dict, slot_id: str) -> dict:
        """Click the applicant's CHOSEN slot by its portal handle and confirm.
        Reconcile-first is enforced by the caller (the flow's RECONCILE node);
        here we click ONLY the slot whose handle the applicant selected, then
        the declared confirm control, and report success solely from evidence
        the caller verifies — never from a banner."""
        handle_attr = node.get("handle_attr") or "data-slot"
        # Escape the handle for an attribute selector; refuse quotes/backslashes
        # rather than build an unsafe selector.
        if any(c in slot_id for c in '"\\'):
            return {"ok": False, "code": "UNSAFE_SLOT_HANDLE"}
        slot_sel = f'[{handle_attr}="{slot_id}"]'
        picked = self.click(slot_sel)
        if not picked.get("ok"):
            # The chosen slot is gone (someone booked it first) — honest signal.
            return {"ok": False, "code": "SLOT_GONE", "detail": picked.get("code")}
        confirm_sel = str(node.get("confirm_selector") or "").strip()
        if confirm_sel:
            conf = self.click(confirm_sel)
            if not conf.get("ok"):
                return {"ok": False, "code": "BOOK_CONFIRM_FAILED",
                        "detail": conf.get("code")}
        return {"ok": True}

    # The VISUAL/framework truth of a checkbox — never just the input's DOM
    # property. page.check can set input.checked=true while the framework
    # model (which paints the box and validates the form) stays unchecked:
    # reading input.checked back is circular and reported false success.
    # Wrapper class ('…-checked' / 'is-checked') or aria-checked is the state
    # the PAGE actually renders; input.checked counts only for plain native
    # checkboxes with no framework wrapper.
    # Evaluated against a RESOLVED element (locator.evaluate), never against a
    # selector string: flow selectors use Playwright syntax (':visible',
    # '>> nth=0') that document.querySelector cannot parse — passing one here
    # threw and read as "element missing" on a page that plainly had it.
    _CHECKBOX_STATE_JS = """(el) => {
      if (!el) return null;
      // ANCESTORS ONLY. An Ant input's own class is 'ant-checkbox-input',
      // so closest() from the element matches the INPUT — we would then read
      // the input's class as if it were the framework wrapper's and never
      // see 'ant-checkbox-checked'. Start the search at the parent.
      const wrap = el.parentElement ? el.parentElement.closest(
        '[class*="checkbox"], [class*="form-check"], [role="checkbox"]') : null;
      const ariaEl = (wrap && wrap.getAttribute('aria-checked') !== null) ? wrap
                     : (el.getAttribute('aria-checked') !== null ? el : null);
      // RAW signals — the caller decides. A framework widget shows truth in
      // the wrapper class/aria; a plain native checkbox shows it in .checked.
      const signals = {
        input: !!el.checked,
        wrapChecked: wrap ? /checked/i.test(wrap.className || '') : null,
        aria: ariaEl ? ariaEl.getAttribute('aria-checked') === 'true' : null,
      };
      // Where a HUMAN would click: the visible wrapper/label, else the input.
      const clickEl = (wrap && wrap.offsetParent !== null) ? wrap
        : (el.closest('label') && el.closest('label').offsetParent !== null)
          ? el.closest('label')
          : (el.offsetParent !== null ? el : null);
      const r = clickEl ? clickEl.getBoundingClientRect() : null;
      return { ...signals,
               clickX: r ? r.left + Math.min(14, r.width / 2) : null,
               clickY: r ? r.top + r.height / 2 : null };
    }"""

    def _checkbox_state(self, selector: str):
        """Resolve the selector through Playwright's OWN engine (it understands
        ':visible' / '>> nth=' flow selectors), then read the raw signals off
        the resolved element."""
        try:
            loc = self.page.locator(selector).first
            if loc.count() == 0:
                return None
            return loc.evaluate(self._CHECKBOX_STATE_JS)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _checkbox_committed_strict(state) -> bool:
        """Did the FRAMEWORK register the tick? When a framework wrapper is
        present, only its own state counts — a raw click can set the hidden
        input's .checked while React never runs its handler, leaving the box
        visually empty and the page's Next disabled (observed on evisa).
        A plain native checkbox has no wrapper: its .checked is the truth."""
        if not state:
            return False
        if state.get("aria") is not None:
            return bool(state.get("aria"))
        if state.get("wrapChecked") is not None:
            return bool(state.get("wrapChecked"))
        return bool(state.get("input"))

    @staticmethod
    def _checkbox_committed(state) -> bool:
        """ANY rendered-or-native checked signal counts. The union is safe
        because commits happen via HUMAN-equivalent clicks first: a real click
        that flips any signal is exactly what the applicant's own click does.
        (The old failure modes: trusting input.checked after a PROGRAMMATIC
        page.check — false positive on framework widgets; trusting only the
        wrapper class — false negative on native boxes inside a
        'checkbox'-classed wrapper.)"""
        if not state:
            return False
        return bool(state.get("aria") or state.get("wrapChecked") or state.get("input"))

    def _commit_checkbox(self, selector: str) -> dict:
        """Tick a checkbox the way a HUMAN does. WAITS for the element first
        (entry-gate dialogs render late — an instant querySelector miss must
        not fail the node), then: real mouse click on the visible widget →
        verify → page.check fallback → verify. Only a verified CHECKED state
        is success."""
        try:
            # Locator wait (not page.wait_for_selector): the same engine that
            # will resolve the selector for the click and the state read.
            self.page.locator(selector).first.wait_for(state="attached", timeout=_WAIT_MS)
        except Exception:  # noqa: BLE001
            return {"ok": False, "code": "NO_SUCH_ELEMENT"}
        state = self._checkbox_state(selector)
        if state is None:
            return {"ok": False, "code": "NO_SUCH_ELEMENT"}
        if self._checkbox_committed_strict(state):
            return {"ok": True, "already": True}
        base = self.page.locator(selector).first
        # Click targets a HUMAN would use, in order. A framework checkbox is
        # driven by its label/wrapper: a real Playwright click there fires the
        # React handler that actually commits the value. Raw coordinate clicks
        # can flip the hidden input without the framework ever noticing.
        targets = [
            ("label", base.locator("xpath=ancestor::label[1]")),
            ("wrapper", base.locator("xpath=ancestor::*[contains(@class,'checkbox')][1]")),
            ("input", base),
        ]
        for name, loc in targets:
            try:
                if loc.count() == 0:
                    continue
                # A label embedding a link ('read the <a>terms</a>…') can
                # swallow the center-point click and navigate the live session
                # off the form — drive the wrapper/input targets instead.
                if name == "label" and loc.first.locator("a").count() > 0:
                    continue
                loc.first.click(timeout=_ACT_MS)
            except Exception:  # noqa: BLE001 — try the next target
                continue
            self.settle(250)
            if self._checkbox_committed_strict(self._checkbox_state(selector)):
                return {"ok": True, "method": f"click_{name}"}
        # Playwright's own check() — actionability-aware, handles plain inputs.
        try:
            self.page.check(selector, timeout=_ACT_MS)
        except Exception:  # noqa: BLE001
            pass
        self.settle(250)
        if self._checkbox_committed_strict(self._checkbox_state(selector)):
            return {"ok": True, "method": "check"}
        # Last resort: dispatch a native click on the element itself.
        try:
            base.evaluate("el => el.click()")
        except Exception:  # noqa: BLE001
            pass
        self.settle(250)
        if self._checkbox_committed_strict(self._checkbox_state(selector)):
            return {"ok": True, "method": "dispatch"}
        return {"ok": False, "code": "CHECK_NOT_COMMITTED"}

    def check(self, selector: str) -> dict:
        """Real checkbox tick, verified against the state the page RENDERS
        (wrapper class / aria-checked), not the input property page.check
        mutates — a framework widget that ignores the hidden input's DOM flag
        must never read as ticked when its box is visually empty."""
        if _SENSITIVE_SELECTOR.search(selector or ""):
            return {"ok": False, "code": "SENSITIVE_FIELD_AUTOMATION"}
        self._uncover(selector)
        return self._commit_checkbox(selector)

    # One page evaluation that answers SEVERAL comboboxes. Everything happens
    # inside the browser — the query is typed as real key events (a framework
    # that listens for keydown gets one), the async option list is awaited on
    # a short beat, and the pick is clicked — so the whole group costs ONE
    # round trip instead of ~15 per field.
    _MANY_JS = "(async ([fields, budgetMs]) => {" + _OPTION_JS + """
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      const fire = (el, type, init) => el.dispatchEvent(
        new (type.startsWith('key') ? KeyboardEvent : Event)(
          type, Object.assign({bubbles: true, cancelable: true}, init || {})));
      const out = [];
      let prev = null;
      for (const [sel, want] of fields) {
        const el = document.querySelector(sel);
        if (!el) { out.push({sel, code: 'NO_SUCH_ELEMENT'}); continue; }
        // Close the previous box's panel before opening this one. Two open
        // panels and _rows() reads whichever the document happens to list
        // last — the month's list answering the day's question.
        if (prev && prev !== el) { prev.blur(); await sleep(30); }
        prev = el;
        el.focus();
        // Clear, then enter the query character by character so a widget that
        // filters on keystrokes sees each one.
        el.value = '';
        fire(el, 'input');
        for (const ch of String(want)) {
          fire(el, 'keydown', {key: ch});
          el.value += ch;
          fire(el, 'input');
          fire(el, 'keyup', {key: ch});
        }
        let hit = null;
        for (let waited = 0; waited <= budgetMs; waited += 60) {
          const rows = _rows();
          if (rows.length) {
            hit = _pick(rows, want, true);
            if (hit) break;
            // Rows are showing and none is the answer: that is a real
            // no-match, not a list still loading. Stop paying for it.
            if (waited >= 240) break;
          }
          await sleep(60);
        }
        if (!hit) { out.push({sel, code: 'NO_OPTIONS'}); continue; }
        hit[0][0].click();
        await sleep(30);
        out.push({sel, code: 'OK', chosen: hit[0][1], match: hit[1]});
      }
      if (prev) prev.blur();
      return out;
    })"""

    def select_search_many(self, fields: list, budget_ms: int = 1500) -> list:
        """Answer several search-comboboxes in ONE round trip, then verify each
        one the same way select_search does.

        Thailand asks Date of Birth as three dropdowns. Answered one node at a
        time over a remote browser they cost ~3s each on a hit and ~17s on a
        miss — measured on the applicant's own run, 2026-08-03 — because the
        expense is the ~15 sequential Playwright round trips per field, not the
        work. Grouped here the whole date is one trip.

        Returns one result dict per field, in order, shaped like
        select_search's. A field this cannot commit is reported honestly so the
        caller can retry it on the proven single-field path — this is a fast
        path, never a replacement for the page being the authority.
        """
        pairs = [(str(s), str(v).strip()) for s, v in fields]
        for sel, _ in pairs:
            if _SENSITIVE_SELECTOR.search(sel or ""):
                return [{"ok": False, "code": "SENSITIVE_FIELD_AUTOMATION"}
                        for _ in pairs]
        if pairs:
            self._uncover(pairs[0][0])
        try:
            picked = self.page.evaluate(self._MANY_JS,
                                        [pairs, int(budget_ms)]) or []
        except Exception as e:  # noqa: BLE001
            return [{"ok": False, "code": "NO_SUCH_ELEMENT",
                     "detail": str(e)[:120]} for _ in pairs]
        out = []
        for (sel, want), got in zip(pairs, picked + [{}] * len(pairs)):
            code = (got or {}).get("code")
            if code != "OK":
                out.append({"ok": False, "code": code or "NO_OPTIONS",
                            "detail": f"no option matches {want[:40]!r}"})
                continue
            # Read back what the WIDGET now shows — a pick is only committed
            # if the page can be seen holding it.
            try:
                shown = self.page.eval_on_selector(sel, _SHOWN_VALUE_JS) or ""
            except Exception:  # noqa: BLE001
                shown = ""
            chosen = str(got.get("chosen") or "")
            if shown and not (want.lower() in shown.lower()
                              or shown.strip().lower() in chosen.lower()
                              or chosen.strip().lower() in shown.lower()):
                out.append({"ok": False, "code": "VALUE_NOT_ACCEPTED",
                            "detail": f"portal shows {shown[:60]!r}"})
                continue
            out.append({"ok": True, "shown": (shown or chosen)[:60],
                        "match": got.get("match") or "exact"})
        return out

    def select_search(self, selector: str, value: str) -> dict:
        """Search-combobox selection (verified live against Ant Design selects):
        focus, TYPE the query (combobox inputs refuse fill()), WAIT for the
        filtered option list (they load asynchronously), pick the exact match,
        else the first option containing the query. The committed selection is
        read back from the widget's selection element — an unreadable or
        mismatched selection fails honestly (never a guess)."""
        if _SENSITIVE_SELECTOR.search(selector or ""):
            return {"ok": False, "code": "SENSITIVE_FIELD_AUTOMATION"}
        want = str(value).strip()
        try:
            # A lingering overlay (the date picker two fields up) sits ON this
            # combobox and eats the click that opens it. Escape alone was tried
            # here and was not enough for MDAC's calendar — _uncover escalates
            # and then CHECKS, instead of pressing a key and hoping.
            self._uncover(selector)
            self.page.locator(f"{selector} >> visible=true").first.click(timeout=_CLICK_MS)
            try:  # clear any previous query so filters start clean
                self.page.keyboard.press("ControlOrMeta+A")
                self.page.keyboard.press("Delete")
            except Exception:  # noqa: BLE001
                pass
            self.page.keyboard.type(want, delay=25)
            # Match and click in ONE page evaluation so no element handle can
            # go stale between the read and the click (a virtualized list
            # re-renders its rows as it settles).
            pick_js = "(want) => {" + _OPTION_JS + """
              const rows = _rows();
              if (!rows.length) return {labels: []};
              const hit = _pick(rows, want, true);
              if (!hit) return {labels: rows.map(([, t]) => t).slice(0, 8)};
              hit[0][0].click();
              return {chosen: hit[0][1], match: hit[1]};
            }"""
            result = {}
            # Read FIRST — fast lists are already rendered — then poll on a
            # short beat. Same ~2.5s settle budget, reached only when the
            # portal is genuinely slow to render its options.
            for i in range(26):
                if i:
                    self.page.wait_for_timeout(100)
                result = self.page.evaluate(pick_js, want) or {}
                if result.get("chosen") or len(result.get("labels") or []) > 1:
                    break
            chosen_text = result.get("chosen")
            if not chosen_text:
                self.page.keyboard.press("Escape")
                labels = result.get("labels") or []
                # Carry the portal's REAL choices back so the runtime can ask
                # the applicant to pick one instead of failing on a near-miss.
                return {"ok": False, "code": "NO_OPTIONS", "options": labels,
                        "detail": (f"{len(labels)} options, none match {want[:30]!r}"
                                   if labels else f"no option matches {want[:40]!r}")}
            self.page.wait_for_timeout(250)
            # Read back what the WIDGET now shows, wherever it keeps it — the
            # selection is only committed if the page can be seen holding it.
            shown = ""
            try:
                shown = self.page.eval_on_selector(selector, _SHOWN_VALUE_JS) or ""
            except Exception:  # noqa: BLE001
                shown = ""
            norm_shown, norm_want = shown.strip().lower(), want.strip().lower()
            norm_chosen = str(chosen_text).strip().lower()
            if shown and not (norm_want in norm_shown or norm_shown in norm_chosen
                              or norm_chosen in norm_shown):
                return {"ok": False, "code": "VALUE_NOT_ACCEPTED",
                        "detail": f"portal shows {shown[:60]!r}"}
            return {"ok": True, "shown": (shown or chosen_text)[:60],
                    "match": result.get("match") or "exact"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": "NO_SUCH_ELEMENT", "detail": str(e)[:120]}

    def select_radio(self, options: list, value: str) -> dict:
        """Answer a radio GROUP by its option labels.

        The group's choices were observed at build time, each with its own
        selector, so nothing is searched for here — the answer is matched
        against the portal's own words with the SAME ladder select_search
        commits with (exact, then a delimiter-separated segment, then prefix,
        then substring). "F" answers "FEMALE" because the portal's word starts
        with it; nothing answers a group whose words it does not match, which
        becomes an applicant question rather than a coin flip.

        The click is verified against what the page RENDERS (aria-checked /
        the wrapper's own class), not the hidden input's DOM property — a
        framework widget that ignored the click must not read as answered.
        """
        want = str(value or "").strip()
        rows = [(str((o or {}).get("label", "")).strip(),
                 str((o or {}).get("selector", "")).strip())
                for o in (options or [])]
        rows = [r for r in rows if r[0] and r[1]]
        if not want or not rows:
            return {"ok": False, "code": "NO_OPTIONS",
                    "options": [r[0] for r in rows],
                    "detail": "no observed choices" if not rows else "no answer"}
        hit = self.page.evaluate(
            "([labels, want]) => {" + _OPTION_JS + """
              const rows = labels.map((t, i) => [i, t]);
              const pick = _pick(rows, want, false);
              return pick ? {index: pick[0][0], match: pick[1]} : null;
            }""", [[r[0] for r in rows], want])
        if not hit:
            return {"ok": False, "code": "NO_OPTIONS",
                    "options": [r[0] for r in rows],
                    "detail": f"{len(rows)} choices, none match {want[:30]!r}"}
        label, sel = rows[int(hit["index"])]
        if _SENSITIVE_SELECTOR.search(sel):
            return {"ok": False, "code": "SENSITIVE_FIELD_AUTOMATION"}
        self._uncover(sel)
        # A radio group's members share one `name`, so a spec built before
        # that was understood can carry the SAME selector for every choice.
        # Taking `.first` there answers whichever button the page happens to
        # draw first — Ellis asked for MALE, clicked FEMALE, and reported
        # success on a government form (Thailand's TDAC, 2026-08-03). Click
        # the element whose OWN rendered words are the answer Ellis chose, or
        # refuse: a group Ellis cannot address is an applicant question, never
        # a coin flip.
        click_js = "([sel, want, doClick]) => {" + _OPTION_JS + """
          const all = Array.from(document.querySelectorAll(sel));
          if (!all.length) return {code: 'NO_SUCH_ELEMENT'};
          const named = (el) => {
            // The words a person reads next to THIS button: its own label,
            // its wrapper's text, then the value it carries.
            const id = el.id && document.querySelector(
              'label[for="' + CSS.escape(el.id) + '"]');
            if (id) return _norm(_label(id));
            const w = el.closest('label, [class*="radio-button"], [class*="radio"]');
            if (w && w !== el) {
              const t = _norm(_label(w));
              if (t) return t;
            }
            return _norm(el.getAttribute('value') || '');
          };
          let target = all[0];
          if (all.length > 1) {
            const w = _norm(want);
            const hits = all.filter((el) => named(el) === w);
            // Ambiguity is refused, not resolved by position.
            if (hits.length !== 1) return {code: 'AMBIGUOUS_OPTION',
                                           seen: all.map(named).slice(0, 8)};
            target = hits[0];
          }
          // A styled widget hides its native input under an overlay, so a
          // real pointer click lands on the ripple, not the control. Click
          // what the framework listens to.
          const lbl = target.id && document.querySelector(
            'label[for="' + CSS.escape(target.id) + '"]');
          if (doClick) {
            (lbl || target).click();
            if (!target.checked && lbl) target.click();
          }
          // Report by identity: whichever control is NOW checked, and what it
          // is called. A click that landed on a sibling must not read as ok.
          const grp = target.getAttribute('name');
          const members = grp ? Array.from(document.querySelectorAll(
            '[name="' + grp.replace(/"/g, '') + '"]')) : all;
          const on = members.filter((el) => el.checked ||
            el.getAttribute('aria-checked') === 'true');
          return {code: 'OK', checked: on.length === 1 ? named(on[0]) : null,
                  intended: named(target)};
        }"""
        try:
            res = self.page.evaluate(click_js, [sel, label, True]) or {}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": "NO_SUCH_ELEMENT", "detail": str(e)[:120]}
        code = res.get("code")
        if code == "AMBIGUOUS_OPTION":
            return {"ok": False, "code": "AMBIGUOUS_OPTION",
                    "options": [r[0] for r in rows],
                    "detail": "this group's choices share one selector — "
                              f"cannot tell {label[:30]!r} from its siblings"}
        if code != "OK":
            return {"ok": False, "code": code or "NO_SUCH_ELEMENT"}
        self.page.wait_for_timeout(120)
        # The page must be seen holding the answer Ellis meant — not merely
        # holding AN answer.
        try:
            state = self.page.evaluate(click_js, [sel, label, False]) or {}
        except Exception:  # noqa: BLE001
            state = res
        got = state.get("checked")
        if got is None:
            return {"ok": False, "code": "VALUE_NOT_ACCEPTED",
                    "detail": f"{label[:40]!r} did not stay selected"}
        if got != state.get("intended"):
            return {"ok": False, "code": "VALUE_NOT_ACCEPTED",
                    "detail": f"the portal selected {str(got)[:30]!r}, not "
                              f"{label[:30]!r}"}
        return {"ok": True, "shown": label[:60], "match": hit.get("match")}

    def list_options(self, selector: str, max_options: int = 300,
                     max_scrolls: int = 12) -> dict:
        """Read a search-combobox's REAL option labels WITHOUT selecting
        anything — powering applicant questions for missing answers (Part 7:
        the applicant chooses from the portal's actual list, never a blank
        field). Opens the widget, waits for the async list, harvests visible
        non-placeholder labels, scrolls the virtualized list until no new
        rows appear, then closes with Escape; the form state is unchanged.

        Returns `complete`: True only when the widget itself ran out of
        options. `max_scrolls` is the read budget — mid-run harvests keep it
        small (an applicant is waiting); a dedicated warm-up pass raises it to
        capture a long list (e.g. Vietnam's 83 border gates) in full."""
        if _SENSITIVE_SELECTOR.search(selector or ""):
            return {"ok": False, "code": "SENSITIVE_FIELD_AUTOMATION"}
        # Same option vocabulary the picker commits with: a list Ellis offers
        # an applicant must be the list Ellis can then select from.
        read_js = "(max) => {" + _OPTION_JS + """
          const seen = new Set(), labels = [];
          for (const [, t] of _rows()) {
            if (seen.has(t)) continue;
            seen.add(t);
            labels.push(t);
            if (labels.length >= max) break;
          }
          return labels;
        }"""
        # Long lists are virtualized — only visible rows exist in the DOM.
        # Scroll the open panel's holder to pull the rest in.
        # Reports WHY it stopped, not just that it did: "did not move" means
        # end-of-list only when a scroller was found and is already at its
        # bottom. No scroller + content that fits = a genuinely short list.
        # Anything else is an incomplete read, and an incomplete list must
        # never be offered to an applicant as the portal's whole list.
        scroll_js = "() => {" + _OPTION_JS + """
          const dd = _openPanel();
          // The scroller is the panel itself on some widgets and an inner
          // holder on others; an overflowing panel counts either way.
          const h = (dd || document).querySelector(
            '.rc-virtual-list-holder, [class*="list-holder"], [class*="panel"]')
            || (dd && dd.scrollHeight > dd.clientHeight + 2 ? dd : null);
          // No holder inside the OPEN dropdown: completeness is unknown, so
          // the read is reported partial rather than guessed complete.
          if (!h) return {found: false, moved: false, atEnd: false, fits: false};
          const before = h.scrollTop;
          h.scrollTop = before + Math.max(80, h.clientHeight - 20);
          const fits = h.scrollHeight <= h.clientHeight + 2;
          const atEnd = h.scrollTop + h.clientHeight >= h.scrollHeight - 2;
          return {found: true, moved: h.scrollTop !== before, atEnd, fits};
        }"""
        try:
            try:
                self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(60)
            except Exception:  # noqa: BLE001
                pass
            self.page.locator(f"{selector} >> visible=true").first.click(timeout=_CLICK_MS)
            labels: list = []
            # Read first, then poll on a short beat — same ~1.8s settle
            # budget, paid only when the options genuinely render late.
            for i in range(19):
                if i:
                    self.page.wait_for_timeout(100)
                labels = self.page.evaluate(read_js, max_options) or []
                if len(labels) > 1:
                    break
            # This read almost always follows a query that matched NOTHING —
            # and the widget is still filtered by that query, so the "full
            # list" is empty and the applicant is offered no choices at all.
            # Clear the leftover query and read again. Only when the filter
            # is showing nothing: a widget already listing options keeps its
            # state untouched, so a committed value is never wiped.
            if not labels:
                try:
                    residue = self.page.eval_on_selector(
                        selector, "el => (el.value || '').trim()") or ""
                except Exception:  # noqa: BLE001
                    residue = ""
                if residue:
                    self.page.keyboard.press("ControlOrMeta+A")
                    self.page.keyboard.press("Delete")
                    for i in range(19):
                        if i:
                            self.page.wait_for_timeout(100)
                        labels = self.page.evaluate(read_js, max_options) or []
                        if len(labels) > 1:
                            break
            seen = list(labels)
            # `complete` = the WIDGET ran out of options, not this read running
            # out of budget. A partial list must never be offered as if it were
            # the portal's whole list (a virtualized dropdown of 83 border
            # gates yields ~10 rows on its first screen).
            complete = False
            for _ in range(max_scrolls):
                try:
                    st = self.page.evaluate(scroll_js) or {}
                except Exception:  # noqa: BLE001
                    break
                if not st.get("found"):
                    # No scroller at all: complete only if everything already
                    # rendered fits (a short list like Yes/No or occupations).
                    complete = bool(st.get("fits"))
                    break
                if not st.get("moved"):
                    complete = bool(st.get("atEnd") or st.get("fits"))
                    break
                self.page.wait_for_timeout(60)
                more = self.page.evaluate(read_js, max_options) or []
                fresh = [t for t in more if t not in set(seen)]
                seen.extend(fresh)
                # Keep scrolling even when a pass yields nothing new. A list
                # that is NOT virtualized has every row in the DOM from the
                # first read, so "no fresh rows" is what a complete list looks
                # like — stopping there reported a whole list as partial.
                # The scroller reaching its end is the only completeness proof.
                if len(seen) >= max_options:
                    break               # hit the read budget: partial
            try:
                self.page.keyboard.press("Escape")
            except Exception:  # noqa: BLE001
                pass
            # A list whose rows are driven by TYPING into the field can never
            # be proven complete from one render: it shows what matches the
            # current filter (or a suggestion subset when empty), not its
            # vocabulary. SGAC's place-of-residence read a fitting handful,
            # called itself complete, and the applicant got a dropdown with no
            # China and no USA in it (2026-08-04). Partial keeps the question
            # a type-anything with suggestions.
            try:
                filter_driven = bool(self.page.eval_on_selector(
                    selector,
                    "el => (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA')"
                    " && !el.readOnly"))
            except Exception:  # noqa: BLE001
                filter_driven = False
            return {"ok": True, "options": seen[:max_options],
                    "complete": bool(complete) and len(seen) < max_options
                    and not filter_driven}
        except Exception as e:  # noqa: BLE001
            try:
                self.page.keyboard.press("Escape")
            except Exception:  # noqa: BLE001
                pass
            return {"ok": False, "code": "NO_SUCH_ELEMENT", "detail": str(e)[:120]}

    def scroll_bottom(self, selector: str = "") -> dict:
        """Scroll a container (or the window) to its bottom and emit a scroll
        event — some portals gate their Continue buttons on a full read."""
        try:
            if selector:
                self.page.eval_on_selector_all(
                    selector,
                    "els => els.forEach(el => { el.scrollTop = el.scrollHeight;"
                    " el.dispatchEvent(new Event('scroll', {bubbles: true})); })")
            else:
                self.page.evaluate(
                    "() => window.scrollTo(0, document.body.scrollHeight)")
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": "SCROLL_ERROR", "detail": str(e)[:120]}

    def upload(self, selector: str, path: str) -> dict:
        """Attach an authorized document to a real file input. The path is a
        backend-local temp copy of a case document the applicant approved.

        Format adaptation: e-visa upload fields (photo, passport biodata)
        require images. When the applicant's file is a PDF and the portal's
        own input does not declare PDF support in its accept attribute, the
        first page is converted to JPEG and THAT is uploaded — same document,
        the format the portal demands. A portal that genuinely takes PDFs
        declares it and receives the original. Conversion failure falls back
        to the original file so the portal's own validation stays the judge."""
        converted = None
        try:
            use_path = path
            if str(path).lower().endswith(".pdf"):
                accept = ""
                try:
                    accept = self.page.eval_on_selector(
                        selector, "el => el.getAttribute('accept') || ''") or ""
                except Exception:  # noqa: BLE001
                    accept = ""
                if "pdf" not in accept.lower():
                    from ..providers.pdf_image import pdf_first_page_jpeg
                    converted = pdf_first_page_jpeg(path)
                    if converted:
                        use_path = converted
            self.page.set_input_files(selector, use_path, timeout=30000)
            return {"ok": True, "converted_to_image": bool(converted)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": "UPLOAD_FAILED", "detail": str(e)[:120]}
        finally:
            if converted:
                try:    # Playwright reads the file during set_input_files;
                    os.unlink(converted)   # the temp JPEG must not linger
                except OSError:
                    pass

    def network_events(self) -> list[dict]:
        return list(self._events)

    def official_state(self) -> dict:
        """Reconciliation reads authoritative state via an injected probe (an
        adapter-declared READ against the official account/status surface).
        With no probe, returns known=False — NEVER an assumed success (§20)."""
        if self._state_probe is None:
            return {"known": False}
        try:
            return self._state_probe(self.page) or {"known": False}
        except Exception:  # noqa: BLE001
            return {"known": False}

    def close(self):
        pass  # the owning LiveBrowserSession releases the Browserbase session

    # ---- internals ----------------------------------------------------------
    def _host_ok(self, url: str) -> bool:
        h = urlparse(url).netloc.lower()
        return any(h == a or h.endswith("." + a) for a in self.allowed)

    def _wire_network(self):
        on = getattr(self.page, "on", None)
        if not callable(on):
            return

        def _handler(response):  # pragma: no cover - needs a live page
            try:
                req = response.request
                ctype = response.headers.get("content-type", "")
                body = None
                if "json" in ctype.lower():
                    try:
                        body = response.text()
                    except Exception:  # noqa: BLE001
                        body = None
                self._events.append(sanitize_network_event(
                    req.method, response.url, response.status, ctype, body))
            except Exception:  # noqa: BLE001
                pass

        try:
            on("response", _handler)
        except Exception:  # noqa: BLE001
            pass

    # Test seam: feed a sanitized event directly (hermetic tests).
    def _record_event(self, method, url, status, content_type, body_text):
        self._events.append(sanitize_network_event(method, url, status, content_type, body_text))
