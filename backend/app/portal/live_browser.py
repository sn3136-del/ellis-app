"""Live Browserbase browser session + credential-free structural observer
(brief §14, §15, §28).

`LiveBrowserSession` owns one isolated Browserbase session and a Playwright
page connected over CDP. `observe(url)` navigates a PUBLIC page and returns a
STRUCTURAL observation (roles, labels, input types, required flags, a stable
selector, navigation relationships) — never values, cookies, tokens, or the
Live View URL. The observation is then passed through `recon.sanitize_structure`
before anything downstream sees it, so this module is the live counterpart of
`SyntheticPortal.observe`.

Credential-free by construction: the observer only navigates and reads DOM
structure; it never authenticates, never fills a field, never reads storage.
The heavy Playwright import is lazy so this module imports without Playwright
installed (tests exercise the pure `normalize_observation`).
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from ..providers import browser as bb


def path_reaches_expected(url: str, expect_path: str) -> bool:
    """Whether a URL (or bare path) has REACHED a declared expect_path. An SPA
    is free to append its own step segment (…/individual-form/draft), prefix a
    per-session id, or route in the fragment (…/#/form), so the declared path
    is matched at a path-segment boundary — never a bare substring, so /visa
    never matches /visa-information. The single source of truth for this rule;
    both the live entry-gate replay and the recon artifact check use it."""
    want = (expect_path or "").rstrip("/")
    if not want:
        return True
    # A DECLARED path may itself be written with the fragment a hash-routed SPA
    # shows in the address bar ("/arrival-card/#/tac/arrival-card/add"). The
    # candidates below are path and fragment SEPARATELY, so a want containing
    # '#' could never equal either — Thailand's TDAC gate could not match its
    # own declared destination. Split the want the same way the URL is split
    # and satisfy it when EITHER half is reached.
    if "#" in want:
        base, _, frag = want.partition("#")
        parts = [p for p in (base.rstrip("/"), "/" + frag.lstrip("/#").rstrip("/"))
                 if p and p != "/"]
        return any(path_reaches_expected(url, p) for p in parts)
    parsed = urlparse(url)
    candidates = [parsed.path.rstrip("/")]
    if parsed.fragment:
        candidates.append("/" + parsed.fragment.lstrip("/#").rstrip("/"))
    if not parsed.scheme and not parsed.netloc:
        # A bare path/pattern was passed (recon's sanitized url_pattern).
        candidates.append((url or "").split("#")[0].rstrip("/"))
    return any(p == want or p.startswith(want + "/")
               or p.endswith(want) or (want + "/") in p
               for p in candidates)

# Extraction runs IN the page and returns JSON-serializable structure only.
# It deliberately never reads element .value, cookies, or storage.
_EXTRACT_JS = r"""
() => {
  const sensitive = /(password|passcode|otp|one[-_]?time|cvv|cvc|card|pan|secret|token|captcha|pin|3ds|passkey|securit(y)?[-_ ]?code|verif(y|ication)[-_ ]?code|confirm(ation)?[-_ ]?code|authcode|imgcaptcha|g-recaptcha|h-captcha|turnstile)/i;
  // Framework-generated ids change on every render (Angular Material
  // mat-input-7, React useId :r3:, Vue v-123, ASP.NET ctl00_...), so a
  // selector built on one cannot re-verify in a second session — the exact
  // failure that blocks Angular portals at the repeated-sessions gate.
  // Stable authoring attributes are preferred over any volatile id.
  // Volatile ids change on every render, so a selector built on one can never
  // re-verify in a second session — the exact failure that blocks Angular and
  // React portals at the repeated-sessions gate. Two shapes: a known framework
  // PREFIX, and an id carrying a generated number ANYWHERE in it. Indonesia's
  // arrival card ships #spi_nationality_1785693541603 — that suffix is an
  // epoch millisecond stamp minted at page load (verified live 2026-08-02), so
  // a long digit run or a uuid segment makes the whole id untrustworthy.
  const volatileId = /^(mat-|cdk-|ng-|:r[0-9a-z]+:|v-|react-|radix-|headlessui-|ember|ctl[0-9]|MainContent_|[0-9a-f]{8}-[0-9a-f]{4})/i;
  const generatedId = /(\d{10,}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4})/i;
  const STABLE_ATTRS = ['formcontrolname', 'data-testid', 'data-test',
                        'data-cy', 'data-qa', 'ng-reflect-name'];
  const cssPath = (el) => {
    const tagl = el.tagName.toLowerCase();
    for (const a of STABLE_ATTRS) {
      const v = el.getAttribute && el.getAttribute(a);
      if (v) return tagl + '[' + a + '="' + v.replace(/"/g, '') + '"]';
    }
    if (el.id && !volatileId.test(el.id) && !generatedId.test(el.id)) return '#' + CSS.escape(el.id);
    // A NAME survives a re-render where a generated id does not, so it is
    // preferred over any volatile/generated id, not just over a path.
    //
    // But every member of a radio group shares its name BY DEFINITION, so on
    // a group this selector names the QUESTION and can never name a choice.
    // Ellis asked for MALE, clicked the first element the selector matched,
    // set FEMALE on a government form, and reported success (Thailand's TDAC,
    // 2026-08-03). A selector that matches more than one control is not a
    // selector — qualify it by the value the option carries, and otherwise
    // fall through to the id/path rules below, which do tell them apart.
    if (el.name) {
      const base = tagl + '[name="' + el.name.replace(/"/g, '') + '"]';
      let shared = false;
      try { shared = document.querySelectorAll(base).length > 1; } catch (e) {}
      if (!shared) return base;
      const v = el.getAttribute && el.getAttribute('value');
      if (v) {
        const q = base + '[value="' + v.replace(/"/g, '') + '"]';
        try { if (document.querySelectorAll(q).length === 1) return q; }
        catch (e) {}
      }
    }
    // An id that is a STABLE prefix plus a per-render stamp is still precise
    // if we anchor on the prefix. Indonesia's arrival card ships
    // spi_passport_no_1785696463523 — the field is 'spi_passport_no_', only
    // the stamp moves. A prefix selector beats a deep ancestor path in both
    // stability and meaning; it is only emitted when the prefix is long
    // enough to be a real field name and it matches exactly one element.
    if (el.id) {
      const m = el.id.match(/^(.*?[a-z])[_-]?\d{10,}$/i);
      if (m && m[1].length >= 4) {
        const pref = tagl + '[id^="' + m[1].replace(/"/g, '') + '"]';
        try { if (document.querySelectorAll(pref).length === 1) return pref; }
        catch (e) {}
      }
    }
    if (el.id && !generatedId.test(el.id)) return '#' + CSS.escape(el.id);   // volatile prefix, still addressable
    if (el.tagName === 'BUTTON') {
      // A button with stable visible text gets a bounded, deterministic
      // text selector instead of a brittle deep ancestor path.
      const t = (el.innerText || '').trim().replace(/\s+/g, ' ').replace(/["<>]/g, '').slice(0, 40);
      if (t) return 'button:has-text("' + t + '")';
    }
    const parts = [];
    let n = el;
    while (n && n.nodeType === 1 && parts.length < 4) {
      let s = n.tagName.toLowerCase();
      if (n.className && typeof n.className === 'string') {
        const c = n.className.trim().split(/\s+/)[0];
        if (c) s += '.' + CSS.escape(c);
      }
      parts.unshift(s);
      n = n.parentElement;
    }
    return parts.join(' > ');
  };
  // A grid layout puts the caption in its OWN cell beside the input's cell,
  // with no `for` tying them together — TDAC's whole passport section is
  // "<div class=col>*Family Name</div><div class=col><input></div>". The row's
  // combined text is every label at once and attributes none of them, so read
  // the PRECEDING sibling cell: short, label-shaped, and specific to this one
  // field. Bounded to a few ancestors and a short string so a stray container
  // can never become a label.
  const cellLabel = (el) => {
    let cell = el;
    // Depth 9, not 5: a Material control buries its input under four wrapper
    // divs before the layout grid even starts, so TDAC's "Date of Birth" sits
    // at ancestor 8 and its nationality autocomplete at 7. The two guards
    // below (no inputs of its own, 60 characters or fewer) are what keep a
    // distant container from being mistaken for a caption.
    for (let i = 0; i < 9 && cell && cell.parentElement; i++) {
      cell = cell.parentElement;
      const prev = cell.previousElementSibling;
      if (!prev) continue;
      // A sibling holding its own inputs is a PEER field, not this one's
      // caption — but the caption may still sit one level up, before the whole
      // group. Bailing out here cost TDAC its grouped fields: Date of Birth is
      // three inputs (yyyy/mm/dd) in sibling cells, and its nationality is a
      // Material autocomplete beside one, so every one of them fell through to
      // its placeholder and arrived named "yyyy" or "Select or enter"
      // (2026-08-03). Keep climbing instead of giving up.
      if (prev.querySelector && prev.querySelector('input, select, textarea')) continue;
      const t = (prev.innerText || '').trim().replace(/\s+/g, ' ');
      if (t && t.length <= 60) return t.replace(/^[*＊]\s*/, '');
    }
    return '';
  };
  // The SAME caption cell cellLabel reads, returned unstripped — the asterisk
  // it removes is the only place some portals say "you must answer this".
  const cellCaptionEl = (el) => {
    let cell = el;
    for (let i = 0; i < 9 && cell && cell.parentElement; i++) {
      cell = cell.parentElement;
      const prev = cell.previousElementSibling;
      if (!prev) continue;
      if (prev.querySelector && prev.querySelector('input, select, textarea')) continue;
      const t = (prev.innerText || '').trim().replace(/\s+/g, ' ');
      if (t && t.length <= 60) return prev;
    }
    return null;
  };
  // A portal states "this answer is required" in more ways than the required
  // attribute, and the attribute is the one modern SPA forms skip. Angular
  // Material validates in TypeScript and draws a red asterisk in the caption
  // — so TDAC's 23-field form recorded as entirely OPTIONAL, and Ellis
  // silently skipped a mandatory Occupation on a form that refuses to submit
  // without it, instead of asking the applicant for it (2026-08-03).
  // Read the marker the applicant themselves reads.
  const REQ_MARK = /(^|\s)[*＊]|[*＊]\s*$/;
  const REQ_CLASS = /required|mandatory|asterisk/i;
  const markedRequired = (node) => {
    if (!node) return false;
    if (REQ_CLASS.test(String(node.className || ''))) return true;
    if (node.querySelector && node.querySelector(
        '[class*="required"], [class*="asterisk"], [class*="mandatory"],'
        + ' abbr[title*="equired"]')) return true;
    // textContent, not innerText: MDC renders its marker in a span the
    // layout may collapse, and a caption's asterisk is authored text either
    // way. Bounded to caption-length strings so a whole section's prose
    // containing a footnote star can never mark a field required.
    const t = (node.textContent || '').replace(/\s+/g, ' ').trim();
    return t.length > 0 && t.length <= 80 && REQ_MARK.test(t);
  };
  const isRequired = (el) => {
    if (el.required || el.getAttribute('aria-required') === 'true') return true;
    if (el.id) {
      const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (markedRequired(l)) return true;
    }
    const ff = el.closest && el.closest('mat-form-field, [class*="form-field"]');
    if (ff && markedRequired(ff.querySelector('mat-label, label'))) return true;
    if (ff && REQ_CLASS.test(String(ff.className || ''))) return true;
    if (markedRequired(el.closest('label'))) return true;
    // Bootstrap-era forms class the field's WRAPPER "required". Bounded to a
    // few ancestors, and only while the wrapper still holds this one control
    // — a whole <form class="required-fields"> must not mark every field on
    // the page.
    let w = el.parentElement;
    for (let i = 0; i < 3 && w; i++, w = w.parentElement) {
      if (w.querySelectorAll('input, select, textarea').length > 1) break;
      if (REQ_CLASS.test(String(w.className || ''))) return true;
    }
    return markedRequired(cellCaptionEl(el));
  };
  const labelFor = (el) => {
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
    if (el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) return l.innerText; }
    const ff = el.closest && el.closest('mat-form-field, [class*="form-field"]');
    const ml = ff && ff.querySelector('mat-label');
    if (ml && (ml.innerText || '').trim()) return ml.innerText;
    const cell = cellLabel(el);
    // Prefer a real caption over a placeholder: TDAC's placeholders are format
    // rules ("Only letters A-Z are allowed"), which name no field at all.
    if (cell) return cell;
    if (el.placeholder) return el.placeholder;
    const p = el.closest('label'); if (p) return p.innerText;
    if (el.tagName === 'BUTTON' || el.type === 'submit') return el.innerText || el.value || '';
    // A FILE input routinely has no label of its own: custom upload widgets
    // hide the native control and put the caption ("Passport bio page",
    // "Photo") in a wrapper. That caption is what a human reads to know which
    // document is wanted, so read it the same way — bounded to a few
    // ancestors and to a short string, and only for file inputs, where the
    // alternative is refusing to identify the upload at all.
    if (el.type === 'file') {
      let n = el.parentElement;
      for (let i = 0; i < 6 && n; i++, n = n.parentElement) {
        // innerText returns only VISIBLE text, and an upload widget is
        // routinely collapsed until its section opens — Thailand's TDAC keeps
        // its Browse control hidden, so every ancestor read back empty and the
        // document could not be identified at all. textContent sees the
        // caption either way; it is the same authored words a human reads
        // once the section expands.
        const t = ((n.innerText || n.textContent || '')
                   .replace(/\s+/g, ' ').trim());
        if (t && t.length >= 3 && t.length <= 120) return t;
      }
    }
    return el.name || '';
  };
  const els = [];
  // Form controls PLUS appointment-calendar slot elements, which are usually
  // plain div/td/li carrying a slot handle attribute rather than form inputs.
  // Hidden inputs are machine plumbing, not applicant-facing structure —
  // ASP.NET WebForms alone plants dozens (__VIEWSTATE, __EVENTVALIDATION)
  // that typed as 'text' flood the page with phantom mappable fields
  // (verified live on evisa.gov.az).
  // Custom widgets are real form controls. New Zealand's NZeTA has NO native
  // <select> anywhere: its Nationality field is a <mat-select>, so a query
  // limited to native elements recorded a 47-field application as an empty
  // page (verified live 2026-08-02). Same for ARIA comboboxes and switches.
  document.querySelectorAll('input:not([type=hidden]), select, textarea, button, a[href], '
    + 'mat-select, [role="combobox"], [role="switch"], mat-slide-toggle, '
    + '[data-slot], [data-slot-id], [data-datetime], [data-appointment]'
  ).forEach((el) => {
    const tag = el.tagName.toLowerCase();
    let type = (el.type || (tag === 'a' ? 'link' : tag)).toLowerCase();
    // A <select>'s DOM type is 'select-one'/'select-multiple' — downstream
    // vocabulary knows only 'select', and the coercion fallback was turning
    // real dropdowns (Nationality on evisa.gov.az) into text inputs.
    if (tag === 'select') type = 'select';
    // A custom widget IS its native equivalent for mapping purposes: a
    // mat-select is a dropdown, a switch is a checkbox. Without this they
    // fall through the unknown-type path and become buttons.
    if (tag === 'mat-select' || el.getAttribute('role') === 'combobox') type = 'select';
    if (tag === 'mat-slide-toggle' || el.getAttribute('role') === 'switch') type = 'checkbox';
    // javascript:/# hrefs make an anchor an ACTION control, not a navigation.
    if (tag === 'a') {
      const h = el.getAttribute('href') || '';
      if (/^(javascript:|#|$)/i.test(h.trim())) type = 'button';
    }
    // Search-combobox detection (ARIA-based): SPA select widgets whose entry
    // control is a text input driving a filtered option list.
    if (tag === 'input' && (type === 'text' || type === 'search' || type === '')) {
      if (el.getAttribute('role') === 'combobox' ||
          el.getAttribute('aria-autocomplete') === 'list' ||
          el.closest('[role="combobox"]')) type = 'search-combobox';
    }
    // A framework-generated id is not a field name. Angular Material numbers
    // every control "mat-input-0", "mat-input-1"... so TDAC's whole passport
    // section arrived as meaningless ids and the mapper could bind exactly ONE
    // field of twenty (2026-08-03). The SAME element carries
    // formcontrolname="familyName", which cssPath already trusts enough to
    // build a selector from — so trust it for the name too.
    let name = (el.name || el.id || '');
    if (!name || volatileId.test(name) || generatedId.test(name)) {
      for (const a of STABLE_ATTRS) {
        const v = el.getAttribute && el.getAttribute(a);
        if (v) { name = v; break; }
      }
    }
    name = name.slice(0, 80);
    const label = (labelFor(el) || '').trim().slice(0, 120);
    const rec = { selector: cssPath(el).slice(0, 200), name, label, type,
                  placeholder: (el.placeholder || '').slice(0, 60),
                  required: isRequired(el),
                  sensitive: type === 'password' || sensitive.test(name) || sensitive.test(label) };
    // A radio's own label is its ANSWER ("FEMALE"), never the question the
    // field asks ("Gender") — that sits once on the group. Recorded without
    // it, TDAC's mandatory Gender arrived as three unrelated controls named
    // after their own answers, matched no Ellis field, and was left blank on
    // a form that refuses to continue without it (2026-08-03).
    if (type === 'radio') {
      const grp = el.closest('mat-radio-group, [role="radiogroup"], fieldset,'
                             + ' [class*="radio-group"]');
      let gl = '';
      if (grp) {
        gl = grp.getAttribute('aria-label') || '';
        if (!gl) {
          const by = grp.getAttribute('aria-labelledby');
          const t = by && document.getElementById(by);
          if (t) gl = t.innerText || '';
        }
        if (!gl) { const lg = grp.querySelector('legend'); if (lg) gl = lg.innerText || ''; }
        if (!gl) gl = cellLabel(grp) || '';
        rec.group_key = String(grp.id || el.name || '').slice(0, 80);
        // A radio group is marked required ONCE, on the question — never on
        // the individual answers. Read it where the portal writes it.
        if (!rec.required && (markedRequired(grp.querySelector('legend'))
                              || markedRequired(cellCaptionEl(grp))
                              || grp.getAttribute('aria-required') === 'true'))
          rec.required = true;
      }
      if (gl) rec.group_label = gl.replace(/\s+/g, ' ').trim().replace(/^[*＊]\s*/, '').slice(0, 120);
      rec.option_label = label;
    }
    if (tag === 'button' || type === 'submit') rec.submits = (name || 'submit').replace(/[^a-z_]/gi, '').toLowerCase().slice(0, 40);
    if (tag === 'a' && el.getAttribute('href')) {
      try { rec.navigates_to = new URL(el.href, location.href).pathname.slice(0, 200); } catch (e) {}
    }
    // STRUCTURAL slot handles only: the attributes an appointment calendar
    // uses to identify each bookable slot. Names/values here are portal
    // structure (a slot id or its datetime), never applicant data — and only
    // this fixed allowlist is ever read.
    const SLOT_ATTRS = ['data-slot', 'data-slot-id', 'data-datetime',
                        'data-date', 'data-time', 'data-appointment'];
    for (const a of SLOT_ATTRS) {
      const v = el.getAttribute && el.getAttribute(a);
      if (v) { (rec.attrs = rec.attrs || {})[a] = String(v).slice(0, 60); }
    }
    els.push(rec);
  });
  const links = Array.from(document.querySelectorAll('a[href]')).slice(0, 40).map((a) => {
    try { return new URL(a.href, location.href).href; } catch (e) { return ''; }
  }).filter(Boolean);
  const iframes = Array.from(document.querySelectorAll('iframe')).slice(0, 10).map((f) => {
    try { return new URL(f.src || '', location.href).pathname; } catch (e) { return ''; }
  });
  return { title: (document.title || '').slice(0, 120), elements: els.slice(0, 200),
           links, iframes, hasShadow: !!document.querySelector('*') && [...document.querySelectorAll('*')].some((e) => e.shadowRoot) };
}
"""

# Hard deadline for attaching to a remote browser session. Without it a
# stalled Browserbase session hangs a build indefinitely.
CONNECT_TIMEOUT_MS = 60_000

_ALLOWED_TYPES = {"text", "email", "password", "date", "select", "checkbox",
                  "radio", "file", "button", "submit", "tel", "number", "link",
                  "search-combobox"}


def normalize_observation(url: str, status: int, hostname: str, raw: dict,
                          delayed: bool = False) -> dict:
    """Pure normalizer: shape a raw page-extraction dict into the observation
    contract `recon.sanitize_structure` consumes. No live browser needed, so
    this is what the hermetic tests drive. Still emits NO values — the JS never
    captured any."""
    elements = []
    for el in (raw or {}).get("elements", []) or []:
        etype = el.get("type")
        etype = etype if etype in _ALLOWED_TYPES else "text"
        rec = {
            "selector": str(el.get("selector", ""))[:200],
            "name": re.sub(r"[^a-zA-Z0-9_\-]", "", str(el.get("name", "")))[:80],
            "label": str(el.get("label", ""))[:120],
            # An <a> with no resolvable destination (href="#", javascript:)
            # is an SPA ACTION control — a button, never a text input. Typing
            # them "text" flooded pages with phantom unnamed fields, drowning
            # the real form and failing "no form page was mappable" (verified
            # live on evisa.gov.az: 34 navbar anchors became text inputs).
            "type": "button" if etype in ("link",) and el.get("navigates_to") is None else etype,
            "required": bool(el.get("required", False)),
            "sensitive": bool(el.get("sensitive", False)) or etype == "password",
        }
        if el.get("placeholder"):
            rec["placeholder"] = str(el.get("placeholder"))[:60]
        if el.get("submits"):
            rec["submits"] = re.sub(r"[^a-z_]", "", str(el.get("submits")))[:40]
        if el.get("navigates_to"):
            rec["navigates_to"] = str(el.get("navigates_to"))[:200]
        # Radio-group context: the question the group asks, and the answer
        # this particular button stands for. Both are portal STRUCTURE — no
        # applicant value is captured here, same as every other field.
        if el.get("group_label"):
            rec["group_label"] = str(el.get("group_label"))[:120]
        if el.get("group_key"):
            rec["group_key"] = re.sub(r"[^a-zA-Z0-9_\-]", "",
                                      str(el.get("group_key")))[:80]
        if el.get("option_label"):
            rec["option_label"] = str(el.get("option_label"))[:120]
        elements.append(rec)
    return {
        "ok": 200 <= int(status) < 400,
        "status": int(status),
        "url": url,
        "hostname": (hostname or urlparse(url).netloc).lower(),
        "title": str((raw or {}).get("title", ""))[:120],
        "elements": elements,
        "links": [str(l)[:300] for l in ((raw or {}).get("links") or [])][:40],
        "iframes": [str(f)[:200] for f in ((raw or {}).get("iframes") or [])][:10],
        "delayed": bool(delayed or (raw or {}).get("hasShadow")),
    }


class LiveBrowserSession:
    """One isolated Browserbase session + Playwright page over CDP. Used for
    credential-free reconnaissance (observe) and, reusing the same page, as the
    backing page for the live runtime driver."""

    def __init__(self, *, allowed_hostnames: list[str], session: dict | None = None,
                 page=None):
        self.allowed = [h.lower() for h in (allowed_hostnames or [])]
        self.session = session
        self.page = page          # injected in tests; else lazily connected
        self._pw = None
        self._owns_session = session is None

    # ---- lifecycle ----------------------------------------------------------
    def _ensure_page(self):
        if self.page is not None:
            return self.page
        if self.session is None:
            self.session = bb.create_session()
        connect = self.session.get("connect_url")
        if not connect:
            raise RuntimeError("no Browserbase connect URL — is BROWSERBASE_API_KEY set?")
        from playwright.sync_api import sync_playwright  # pragma: no cover
        self._pw = sync_playwright().start()             # pragma: no cover
        chromium = self._pw.chromium                     # pragma: no cover
        # A stalled Browserbase session would otherwise block the whole build
        # forever — connect_over_cdp has no default deadline.
        browser = chromium.connect_over_cdp(             # pragma: no cover
            connect, timeout=CONNECT_TIMEOUT_MS)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()  # pragma: no cover
        # Reattach to a LIVE page — pages[0] can be a closed leftover, and a
        # dead page turns every later action into NO_SUCH_ELEMENT noise.
        live = [p for p in ctx.pages if not p.is_closed()]  # pragma: no cover
        self.page = live[0] if live else ctx.new_page()     # pragma: no cover
        return self.page

    def close(self):
        try:
            if self._pw is not None:
                self._pw.stop()  # pragma: no cover
        finally:
            if self._owns_session and self.session and not str(
                    self.session.get("id", "")).startswith("local-"):
                bb.close_session(self.session.get("id", ""))

    # ---- observation --------------------------------------------------------
    def _host_ok(self, url: str) -> bool:
        h = urlparse(url).netloc.lower()
        return any(h == a or h.endswith("." + a) for a in self.allowed)

    def observe(self, url: str) -> dict:
        """Navigate a PUBLIC page and return its sanitized-shape structure. The
        observer never authenticates and refuses to leave the allowlist."""
        if self.allowed and not self._host_ok(url):
            return {"ok": False, "status": 0, "url": url,
                    "error": "off-allowlist host refused"}
        page = self._ensure_page()
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)  # pragma: no cover
            status = resp.status if resp else 200                                # pragma: no cover
            # Cookie-challenge CDNs (verified live on evisa.gov.az) answer the
            # FIRST navigation 403 while setting the cookie that makes the
            # second one succeed — a real browser reloads through this without
            # anyone noticing. One same-URL retry inside the same session is
            # that reload; it solves no CAPTCHA and spoofs nothing.
            if resp is not None and status in (403, 503):                        # pragma: no cover
                resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)  # pragma: no cover
                status = resp.status if resp else status                         # pragma: no cover
            final = page.url                                                     # pragma: no cover
            # A redirect that leaves the allowlist is not observed further.
            if self.allowed and not self._host_ok(final):                        # pragma: no cover
                return {"ok": False, "status": status, "url": final,
                        "error": "redirected off allowlist"}
            self._settle_for_render(page)                                       # pragma: no cover
            raw = page.evaluate(_EXTRACT_JS)                                     # pragma: no cover
            self._probe_widgets(page, raw)                                       # pragma: no cover
        except Exception as e:  # noqa: BLE001                                    # pragma: no cover
            return {"ok": False, "status": 0, "url": url, "error": str(e)[:200]}
        return normalize_observation(final, status, urlparse(final).netloc, raw)  # pragma: no cover

    # A field recorded as a dropdown may not BE one. Thailand's Date of Birth
    # is three boxes placeheld "yyyy"/"mm"/"dd"; recon typed them "select" from
    # their ARIA alone, specgen emitted SELECT_SEARCH, and at run time all
    # three read ZERO options and killed the application three times over one
    # date — while Nationality, on the same page and the same widget class,
    # committed in four seconds (2026-08-03). Nothing stored could say why,
    # because nobody had ever asked the page.
    #
    # So ask it HERE, once, at build time, where being wrong costs a rebuild
    # instead of an applicant's run. Read-only: focus a field, see whether a
    # list appears, count the rows, press Escape. Nothing is typed, nothing is
    # chosen, no value is recorded — only the SHAPE of the widget, which is
    # the whole of what recon exists to learn.
    WIDGET_PROBE_MAX = 6

    def _probe_widgets(self, page, raw: dict) -> None:  # pragma: no cover
        selects = [e for e in (raw.get("elements") or [])
                   if e.get("type") in ("select", "search-combobox")
                   and e.get("selector") and not e.get("sensitive")]
        for el in selects[:self.WIDGET_PROBE_MAX]:
            el["opens_list"] = self._probe_one(page, el["selector"])

    def _probe_one(self, page, selector: str) -> str:
        """'options' (a list appeared), 'empty' (it opened nothing), or
        'unknown' (the field could not be reached, or the page was not in a
        state where the answer would mean anything). Never raises: a probe
        that fails leaves the observation exactly as it was."""
        from ..adapter_factory.live_driver import _OPTION_JS
        count_js = "() => {" + _OPTION_JS + " return _rows().length; }"
        try:
            # The PREVIOUS field's panel closes first. Left open, its rows are
            # still on the page and this field reads as a dropdown because its
            # neighbour is one — the same mistake as reading the month's list
            # to answer the day's question. Only a page showing NO options can
            # say what opening this one did.
            if not self._close_open_lists(page, count_js):
                return "unknown"
            page.locator(f"{selector} >> visible=true").first.click(timeout=3000)
            rows = 0
            for i in range(8):
                if i:
                    page.wait_for_timeout(120)
                rows = int(page.evaluate(count_js) or 0)
                if rows:
                    break
            verdict = "options" if rows else "empty"
        except Exception:  # noqa: BLE001 — a diagnosis must never fail a build
            verdict = "unknown"
        try:
            self._close_open_lists(page, count_js)
        except Exception:  # noqa: BLE001
            pass
        return verdict

    def _close_open_lists(self, page, count_js: str) -> bool:  # pragma: no cover
        """Get the page back to showing no option rows. True when it is clean.
        Escape, then a blur — a panel that ignores both leaves the next
        reading meaningless, and saying so is better than guessing."""
        for attempt in range(3):
            if int(page.evaluate(count_js) or 0) == 0:
                return True
            page.keyboard.press("Escape")
            if attempt:
                page.evaluate("() => document.activeElement "
                              "&& document.activeElement.blur()")
            page.wait_for_timeout(120)
        return int(page.evaluate(count_js) or 0) == 0

    def _settle_for_render(self, page) -> None:  # pragma: no cover
        """domcontentloaded fires before a client-rendered (React/Angular/Vue)
        portal has drawn its form, so extracting immediately captures an empty
        shell — the root cause of 'no form page was mappable' on SPA e-visa
        portals. Wait, bounded, for the network to go idle and the framework
        to paint real inputs, then stop the moment they appear. Purely a
        capture-more wait: it changes nothing about how the structure is used,
        and every step degrades gracefully so a slow/never-idle page still
        yields whatever is on screen."""
        try:
            page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:  # noqa: BLE001 — some portals poll forever; that is fine
            pass
        try:
            # Real form fields appearing is the signal to proceed; a content
            # page that never grows one just burns the short budget once.
            page.wait_for_function(
                "() => document.querySelectorAll("
                "'input:not([type=hidden]), select, textarea').length >= 3",
                timeout=6000)
        except Exception:  # noqa: BLE001 — not every page is a form; extract as-is
            pass
        try:
            page.wait_for_timeout(600)   # a final paint tick for late hydration
        except Exception:  # noqa: BLE001
            pass

    # ---- declarative entry-gate replay (credential-free, reversible) --------
    # Some SPA portals gate their application form behind an in-session
    # instruction sequence (open modal, scroll acknowledgment text, tick the
    # acknowledgment checkboxes, continue). The sequence is DECLARED, curated
    # data — never inferred from page content — and only these reversible
    # navigation/acknowledgment actions are permitted.
    ENTRY_GATE_ACTIONS = ("CLICK", "SCROLL_TO_BOTTOM", "CHECK",
                          "SELECT_OPTION", "WAIT_FOR_SELECTOR")
    ENTRY_GATE_MAX_ACTIONS = 12

    # Georgia names its CAPTCHA input "SecurityCode" — no 'captcha', no 'otp',
    # nothing the old pattern caught, so a curated gate could legally have
    # typed into a challenge field. Verification-code fields of every naming
    # convention are refused here (verified live 2026-08-02).
    _SENSITIVE_TARGET_RE = re.compile(
        r"(password|passcode|otp|one[-_]?time|cvv|cvc|card|pan|secret|token|"
        r"captcha|pin|3ds|passkey|pay|securit(y)?[-_ ]?code|"
        r"verif(y|ication)[-_ ]?code|confirm(ation)?[-_ ]?code|authcode|"
        r"imgcaptcha|g-recaptcha|h-captcha|turnstile)", re.IGNORECASE)

    def _assert_gate_target_safe(self, locator, action: str, selector: str):
        """No entry-gate action may ever touch a password/payment/sensitive
        field: CHECK only real checkboxes, CLICK never a value-bearing input."""
        info = locator.evaluate(
            "el => ({tag: el.tagName.toLowerCase(),"
            " type: (el.getAttribute('type') || '').toLowerCase(),"
            " ident: ((el.name || '') + ' ' + (el.id || ''))})")
        if info.get("type") == "password" or \
                self._SENSITIVE_TARGET_RE.search(info.get("ident", "")):
            raise RuntimeError(f"entry gate refused: {selector!r} is a sensitive field")
        if action == "CHECK" and not (info.get("tag") == "input"
                                      and info.get("type") == "checkbox"):
            raise RuntimeError(f"entry gate refused: CHECK target {selector!r} "
                               f"is not a checkbox")
        if action == "CLICK" and info.get("tag") in ("input", "textarea", "select") \
                and info.get("type") not in ("button", "submit", "checkbox", "radio"):
            raise RuntimeError(f"entry gate refused: CLICK target {selector!r} "
                               f"is a form input")

    # Applied to a RESOLVED element, never to a selector string: entry-gate
    # selectors are Playwright syntax (':visible', '>> nth=N', ':has-text()')
    # which document.querySelectorAll cannot parse.
    _SCROLL_ONE_JS = """(t) => {
         if (!t) return;
         t.scrollTop = t.scrollHeight;
         t.dispatchEvent(new Event('scroll', {bubbles: true}));
       }"""

    # A consent control's own words, in the languages the curated portals use.
    # Ellis reads what it is about to click and refuses the negative option.
    # Unanchored: a real refusal control reads "I do not agree", not just
    # "Disagree". Only unambiguous refusals — never a bare "no", which appears
    # in ordinary sentences ("there is no fee").
    _CONSENT_NO_RE = re.compile(
        r"(\bdisagree\b|\bdo(es)? not agree\b|\bdon'?t agree\b|\bnot agree\b|"
        r"\bdecline\b|\breject\b|\brefuse\b|\bdisagreement\b|"
        r"동의하지|비동의|거부|不同意|拒否|同意しない|拒绝|"
        r"não concordo|no acepto|je n'accepte pas|nicht einverstanden|"
        r"katılmıyorum|не согласен|không đồng ý)", re.IGNORECASE)

    def _assert_affirmative_consent(self, loc, selector: str):
        """A TERMS_CHOICE may only ever take the AGREE option.

        Korea's K-ETA numbers its consent radios so that #agree9 is the
        DISAGREE option and #dAgree9 is Agree (verified live 2026-08-02) — the
        shipped gate targeted all four negative radios. A curation mistake
        there is not a broken build, it is Ellis recording the opposite of the
        applicant's intent on a government form, so the runtime refuses rather
        than trusting the curated selector's name.

        Judged from what the control SAYS (its label, or its own text), plus
        the radio value where the page pairs agree=1 / disagree=0. A control
        whose wording cannot be read at all is allowed through: silent absence
        of a label must not block honest gates, and the negative wording is
        what we are actually looking for.
        """
        try:  # pragma: no cover — live DOM
            info = loc.evaluate(
                """(el) => {
                  const lab = el.id ? document.querySelector('label[for="' + CSS.escape(el.id) + '"]') : null;
                  const wrap = el.closest ? el.closest('label') : null;
                  const text = ((lab && lab.innerText) || (wrap && wrap.innerText)
                                || el.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim();
                  const sib = el.parentElement ? [...el.parentElement.querySelectorAll(
                      'input[type=radio][name="' + (el.name || '') + '"]')].length : 0;
                  return {text, value: el.value || '', type: el.type || '', siblings: sib};
                }""")
        except Exception:  # noqa: BLE001 — unreadable control: allow, see docstring
            return
        text = str((info or {}).get("text") or "")
        if text and self._CONSENT_NO_RE.search(text):
            raise RuntimeError(
                f"TERMS_CHOICE {selector!r} targets the control labelled "
                f"{text[:40]!r} — Ellis refuses to take the DISAGREE option on "
                f"an applicant's behalf. Curate the affirmative control.")

    @staticmethod
    def _click_possibly_styled(page, loc, selector: str):
        """Click a control that may be a STYLED custom widget whose native
        input is 0x0 (Material/MDL radios and checkboxes paint a wrapper and
        hide the real input). Click the visible label the human clicks; fall
        back to the element itself. Never a blind coordinate press: every
        path targets this exact element or its own label."""
        # A control below the fold is not unclickable — bring it into view
        # first, exactly as a person scrolling the page would.
        try:
            loc.scroll_into_view_if_needed(timeout=8000)
        except Exception:  # noqa: BLE001 — a 0x0 input cannot be scrolled to;
            try:            # scroll its painted label instead
                loc.evaluate("el => el.scrollIntoView({block: 'center'})")
            except Exception:  # noqa: BLE001
                pass
        try:
            box = loc.bounding_box()
        except Exception:  # noqa: BLE001
            box = None
        if box and box.get("width", 0) > 0 and box.get("height", 0) > 0:
            try:
                loc.click(timeout=15000)
                return
            except Exception:  # noqa: BLE001 — a styled control can HAVE a box
                pass          # and still be covered by the label that paints
                              # it (pointer events land on the wrapper), so
                              # fall through to the label paths below rather
                              # than failing a gate that a human can click.
        # Click its <label for=…>, which is what paints the control.
        el_id = ""
        try:
            el_id = loc.evaluate("el => el.id || ''") or ""
        except Exception:  # noqa: BLE001
            pass
        if el_id:
            label = page.locator(f'label[for="{el_id}"]').first
            try:
                if label.count():
                    label.scroll_into_view_if_needed(timeout=5000)
                    label.click(timeout=15000)
                    return
            except Exception:  # noqa: BLE001
                pass
        # A styled control may also WRAP its input in an unlabelled <label>.
        try:
            wrapper = loc.locator("xpath=ancestor::label[1]").first
            if wrapper.count():
                wrapper.scroll_into_view_if_needed(timeout=5000)
                wrapper.click(timeout=15000)
                return
        except Exception:  # noqa: BLE001
            pass
        # Last resort: a real DOM click on the element ITSELF — still exactly
        # the declared target, never a different control, and independent of
        # viewport geometry that a 0x0 styled input does not have.
        loc.evaluate("el => el.click()")

    @staticmethod
    def _scroll_container_to_bottom(page, selector: str):
        """Set scrollTop to max on every matching container (window when the
        selector is empty) and dispatch a scroll event — SPAs enable their
        'Next' button on the scroll event, not on scrollTop alone.

        Resolution goes through Playwright's selector engine so a declared
        entry-gate selector behaves identically here and at runtime (the
        runtime path uses eval_on_selector_all)."""
        if not selector:
            page.evaluate(
                """() => {
                     const t = document.scrollingElement;
                     if (!t) return;
                     t.scrollTop = t.scrollHeight;
                     t.dispatchEvent(new Event('scroll', {bubbles: true}));
                   }""")
            return
        loc = page.locator(selector)
        for i in range(min(loc.count(), 20)):
            try:
                loc.nth(i).evaluate(LiveBrowserSession._SCROLL_ONE_JS)
            except Exception:  # noqa: BLE001 — one container never blocks the rest
                continue

    def observe_with_entry_gate(self, base_url: str, entry_gate: dict) -> dict:
        """Replay the gate, retrying ONCE on a first-pass failure.

        A gated SPA's first visit in a fresh browser is routinely the slow one:
        Thailand's TDAC swallows the tile click until its invisible Turnstile
        self-populates (~10-13s), Cambodia's guest dialog mounts behind a
        Cloudflare interstitial, and Angular routes are still hydrating. The
        SECOND independent session the repeated-sessions gate requires is
        always a fresh browser, so it always paid that cost and always failed
        first — which read as "these selectors are unstable" when the truth was
        "this page was not ready yet".

        One retry in the SAME session, after a settle. Nothing is clicked twice
        that had an effect: a gate that got partway simply replays from the
        portal's own start page, which is where a failed replay leaves it.
        """
        out = self._observe_with_entry_gate_once(base_url, entry_gate)
        if out and out.get("ok"):
            return out
        try:  # pragma: no cover — live path
            self._ensure_page().wait_for_timeout(6000)
        except Exception:  # noqa: BLE001
            pass
        retry = self._observe_with_entry_gate_once(base_url, entry_gate)
        if retry and retry.get("ok"):
            retry["replay_attempt"] = 2
            return retry
        return retry or out

    def _observe_with_entry_gate_once(self, base_url: str, entry_gate: dict) -> dict:
        """Replay a DECLARED entry gate from base_url and observe the
        destination page's structure. Actions are restricted to the reversible
        navigation/acknowledgment vocabulary; the observer still never
        authenticates, never fills a value, and never leaves the allowlist."""
        gate = entry_gate or {}
        actions = list(gate.get("actions") or [])[: self.ENTRY_GATE_MAX_ACTIONS]
        for a in actions:
            if (a or {}).get("action") not in self.ENTRY_GATE_ACTIONS + ("TERMS_CHOICE",):
                return {"ok": False, "status": 0, "url": base_url,
                        "error": f"entry gate action {(a or {}).get('action')!r} "
                                 f"not in the declared vocabulary"}
        terms_captured: list[dict] = []
        if self.allowed and not self._host_ok(base_url):
            return {"ok": False, "status": 0, "url": base_url,
                    "error": "off-allowlist host refused"}
        page = self._ensure_page()
        performed: list[dict] = []
        try:                                                                     # pragma: no cover
            # A single-page portal often redirects its own landing route
            # while the first navigation is still settling, which aborts
            # goto() with "interrupted by another navigation". That is the
            # app routing, not a failure: settle and navigate once more.
            # The allowlist still governs where we end up.
            try:
                resp = page.goto(base_url, wait_until="domcontentloaded", timeout=45000)
            except Exception as nav:  # noqa: BLE001
                if "interrupted by another navigation" not in str(nav):
                    raise
                page.wait_for_timeout(2000)
                resp = page.goto(base_url, wait_until="domcontentloaded", timeout=45000)
            status = resp.status if resp else 200
            if self.allowed and not self._host_ok(page.url):
                return {"ok": False, "status": status, "url": page.url,
                        "error": "entry gate landing left the allowlist"}
            # domcontentloaded fires long before a client-rendered portal has
            # drawn its first gate control, so acting immediately clicks at a
            # page that is not there yet (ESTA's splash modal and its DHS
            # security notice both mount after hydration). Settle exactly as
            # the read-only observer does before touching anything.
            self._settle_for_render(page)
            for a in actions:
                act = a["action"]
                sel = str(a.get("selector") or "")
                if act == "SCROLL_TO_BOTTOM":
                    self._scroll_container_to_bottom(page, sel)
                    performed.append({"action": act, "selector": sel, "ok": True})
                    continue
                if act == "WAIT_FOR_SELECTOR":
                    # Purely observational: a bounded wait for something the
                    # page will render on its own. Canada's eTA puts a virtual
                    # waiting room ("You're in line to apply") in front of the
                    # form, and the queue clears without any interaction.
                    try:
                        # VISIBLE, not merely attached: a queued or
                        # not-yet-hydrated page can carry the control in its
                        # DOM while it is still unusable, and "attached" would
                        # release the wait into a select_option that then times
                        # out. Canada's queue took 141s to clear on a live
                        # measurement, and the control became visible exactly
                        # when it did.
                        page.locator(sel).first.wait_for(
                            state="visible",
                            timeout=int(a.get("timeout_ms") or 120000))
                        ok = True
                    except Exception as e:  # noqa: BLE001
                        if not a.get("optional"):
                            return {"ok": False, "status": status, "url": page.url,
                                    "error": f"entry gate WAIT_FOR_SELECTOR {sel[:60]!r} "
                                             f"never appeared: {str(e)[:120]}"}
                        ok = False
                    performed.append({"action": act, "selector": sel, "ok": ok})
                    continue
                if act == "SELECT_OPTION":
                    # A DECLARED choice on a declared control. The value is
                    # curated data (an eligibility answer the whole route
                    # depends on: "not a representative", "ordinary passport"),
                    # never the applicant's — families.py refuses value_from at
                    # seed load, so nothing personal can reach this session.
                    loc = page.locator(sel).first
                    try:
                        loc.wait_for(state="visible",
                                     timeout=int(a.get("timeout_ms") or 30000))
                    except Exception as e:  # noqa: BLE001
                        # A question the portal only asks SOME nationalities is
                        # declared optional; its absence is the portal being
                        # itself, not a broken gate.
                        if a.get("optional"):
                            performed.append({"action": act, "selector": sel,
                                              "ok": True, "skipped": "not_shown"})
                            continue
                        return {"ok": False, "status": status, "url": page.url,
                                "error": f"entry gate SELECT_OPTION target "
                                         f"{sel[:60]!r} never appeared: {str(e)[:100]}"}
                    self._assert_gate_target_safe(loc, "SELECT_OPTION", sel)
                    try:
                        if a.get("option_value"):
                            loc.select_option(value=str(a["option_value"]))
                        else:
                            loc.select_option(label=str(a["option_label"]))
                    except Exception as e:  # noqa: BLE001
                        return {"ok": False, "status": status, "url": page.url,
                                "error": f"entry gate SELECT_OPTION on {sel[:60]!r} "
                                         f"failed: {str(e)[:140]}"}
                    performed.append({"action": act, "selector": sel, "ok": True})
                    page.wait_for_timeout(int(a.get("settle_ms") or 900))
                    continue
                if act == "TERMS_CHOICE":
                    # The portal's own terms gate. This OBSERVATION session
                    # carries no applicant and submits nothing: capture the
                    # VERBATIM terms text as evidence (the applicant signs
                    # exactly this text in Ellis before any live run may
                    # transcribe the choice), then take the declared control
                    # so the form behind the gate can be structurally
                    # observed.
                    tsel = str(a.get("terms_text_selector") or "")
                    # The terms container may still be rendering (an SPA modal
                    # fades in). Wait for it to ATTACH before snapshotting —
                    # locator.all() on an unattached selector is always empty,
                    # which reads as "no terms" on a page that plainly has them.
                    try:
                        page.locator(tsel).first.wait_for(
                            state="attached",
                            timeout=int(a.get("timeout_ms") or 15000))
                    except Exception:  # noqa: BLE001 — absence handled below
                        pass
                    text = ""
                    for one in page.locator(tsel).all()[:6]:
                        try:
                            # inner_text returns only VISIBLE text, and portals
                            # routinely keep their terms in a collapsed
                            # accordion until the applicant expands it. The
                            # terms are the same either way, so fall back to
                            # text_content — Ellis shows the applicant the full
                            # verbatim text regardless of how the page folds it.
                            got = (one.inner_text(timeout=8000) or "").strip()
                            if not got:
                                got = (one.text_content(timeout=8000) or "").strip()
                            if got:
                                text += got + "\n\n"
                        except Exception:  # noqa: BLE001
                            continue
                    if not text.strip():
                        # A step DECLARED optional may legitimately be absent:
                        # portals gate first-visit notices on session storage
                        # (ESTA's DHS security notice appears once per browser
                        # session). Skip it honestly and record that it was not
                        # shown — never treat an absent notice as agreed.
                        if a.get("optional"):
                            performed.append({"action": act, "selector": sel,
                                              "ok": True, "skipped": "not_shown"})
                            continue
                        return {"ok": False, "status": status, "url": page.url,
                                "error": f"TERMS_CHOICE captured no terms text "
                                         f"via {tsel[:60]!r} — refusing to "
                                         f"proceed past unread terms"}
                    terms_captured.append({
                        "title": str(a.get("purpose") or "Portal terms")[:300],
                        "text": text.strip()[:20000],
                        "selector": sel, "source_url": page.url[:500]})
                    loc = page.locator(sel).first
                    # Material/MDL radios and checkboxes render the NATIVE
                    # input at 0x0 and paint a styled wrapper, so waiting for
                    # it to be "visible" never succeeds. Wait for it to exist,
                    # then click what the human actually clicks: its label.
                    loc.wait_for(state="attached",
                                 timeout=int(a.get("timeout_ms") or 30000))
                    self._assert_gate_target_safe(loc, "CLICK", sel)
                    self._assert_affirmative_consent(loc, sel)
                    self._click_possibly_styled(page, loc, sel)
                    # An SPA gate routes client-side: give the next step's DOM
                    # time to exist before we look for it, or a correct gate
                    # reads as "element missing" purely on timing. A modal that
                    # mounts on click needs the same grace a fresh page does.
                    page.wait_for_timeout(2500)
                    performed.append({"action": act, "selector": sel, "ok": True})
                    continue
                loc = page.locator(sel).first
                loc.wait_for(state="visible",
                             timeout=int(a.get("timeout_ms") or 30000))
                self._assert_gate_target_safe(loc, act, sel)
                if act == "CLICK":
                    loc.click(timeout=15000)
                else:  # CHECK — a real checkbox check, acknowledgment only
                    loc.check(timeout=15000)
                performed.append({"action": act, "selector": sel, "ok": True})
            expect_path = str(gate.get("expect_path") or "")
            if expect_path:
                page.wait_for_url(
                    lambda u: path_reaches_expected(u, expect_path), timeout=30000)
            # SPA render readiness (declared, portal-agnostic):
            #  1. the destination path is already confirmed (wait_for_url);
            #  2. wait for the declared concrete control ATTACHED — not
            #     visible: styled upload inputs are permanently display:none
            #     behind "Choose file" buttons, and a visibility wait on an
            #     [id^=...] locator can pin itself to exactly those;
            #  3. wait until the declared minimum number of form controls
            #     exists — SPA forms hydrate field-by-field while nomenclature
            #     lists load, which can be slow from a datacenter egress.
            ready = str(gate.get("form_ready_selector") or "")
            count_spec = gate.get("form_ready_all") or {}
            count_sel = str(count_spec.get("selector") or "")
            count_min = int(count_spec.get("min") or 0)
            try:
                if ready:
                    page.wait_for_selector(ready, state="attached", timeout=90000)
                if count_sel and count_min:
                    # Poll through Playwright's engine — a declared selector
                    # with ':visible'/'>> nth=' must count here exactly as it
                    # does everywhere else, not SyntaxError in a CSS parser.
                    deadline = 60.0
                    while True:
                        if page.locator(count_sel).count() >= count_min:
                            break
                        if deadline <= 0:
                            raise TimeoutError(
                                f"form_ready_all: fewer than {count_min} matches")
                        page.wait_for_timeout(500)
                        deadline -= 0.5
                elif not ready:
                    page.wait_for_load_state("networkidle", timeout=30000)
                # A form is "ready" the moment its FIRST field exists, but an
                # Angular/React form finishes mounting its action bar after
                # that — Thailand's TDAC still had Continue, Preview and Add
                # Other Travelers unrendered when familyName appeared. The
                # extraction that follows would then miss the advance control,
                # and the repeated-sessions gate would call it "unstable"
                # because one session raced ahead of the other. Settle until
                # the button count stops growing, briefly and bounded.
                seen, stable = -1, 0
                for _ in range(16):          # <= 8s
                    try:
                        now = page.locator("button, input[type=submit]").count()
                    except Exception:  # noqa: BLE001
                        break
                    stable = stable + 1 if now == seen else 0
                    seen = now
                    if stable >= 2:          # unchanged across two polls
                        break
                    page.wait_for_timeout(500)
            except Exception as e:  # noqa: BLE001 — honest, diagnosable failure
                probe = count_sel or ready or "*"
                try:
                    have = page.locator(probe).count()
                except Exception:  # noqa: BLE001
                    have = -1
                return {"ok": False, "status": status, "url": page.url,
                        "error": f"form not ready at "
                                 f"{urlparse(page.url).path[:80]!r} "
                                 f"({probe[:40]!r} matches={have}, "
                                 f"need>={count_min or 1}): {str(e)[:100]}"}
            final = page.url
            if self.allowed and not self._host_ok(final):
                return {"ok": False, "status": status, "url": final,
                        "error": "entry gate left the allowlist"}
            # Same tolerance as the wait above: a final URL that carries the
            # declared path at a segment boundary IS the declared destination.
            if expect_path and not path_reaches_expected(final, expect_path):
                return {"ok": False, "status": status, "url": final,
                        "error": f"entry gate ended at {urlparse(final).path!r}, "
                                 f"expected {expect_path!r}"}
            raw = page.evaluate(_EXTRACT_JS)
        except Exception as e:  # noqa: BLE001                                    # pragma: no cover
            return {"ok": False, "status": 0, "url": base_url,
                    "error": f"entry gate replay failed: {str(e)[:160]}"}
        # The replayed gate controls WERE observed (interacted with): echo them
        # as structural elements so downstream contract checks can ground the
        # flow's entry-gate node selectors in recorded observation.
        for i, st in enumerate(performed):                                       # pragma: no cover
            if st["action"] in ("CLICK", "CHECK", "TERMS_CHOICE"):
                raw.setdefault("elements", []).append({
                    "selector": st["selector"],
                    "name": f"entry_gate_step_{i + 1}",
                    "label": "entry gate control",
                    "type": "checkbox" if st["action"] == "CHECK" else "button",
                    "required": False, "sensitive": False})
        obs = normalize_observation(final, status, urlparse(final).netloc, raw)   # pragma: no cover
        obs["entry_gate_replayed"] = performed                                    # pragma: no cover
        if terms_captured:                                                        # pragma: no cover
            # The portal's own public terms text, verbatim — the exact words
            # the applicant must sign in Ellis before any live transcription.
            obs["terms_captured"] = terms_captured
        return obs                                                                # pragma: no cover


def build_observer_factory(hostnames: list[str]):
    """Return `observer(url) -> observation` backed by ONE live session, for
    `recon.run_recon` / `build_workflow.run_build`. Real Browserbase only.
    The returned callable carries `.close()` so the caller (run_build's finally
    block, or the API layer) can release the Browserbase session when done."""
    if not bb.is_configured():
        return None
    session = LiveBrowserSession(allowed_hostnames=hostnames)

    def observe(url: str) -> dict:
        return session.observe(url)

    observe.close = session.close
    observe.session = session          # runtime reuse: same page drives execution
    # Entry-gate replay (declared, reversible) shares the same session/page.
    observe.observe_with_entry_gate = session.observe_with_entry_gate
    # A brand-new isolated session for repeated-session selector verification
    # (the selectors_verified_repeated_sessions gate needs a SECOND session).
    observe.spawn_independent = lambda: _threaded_observer_factory(hostnames)
    return observe


def _threaded_observer_factory(hostnames: list[str]):
    """Independent live observer that runs ALL its Playwright work on one
    dedicated OS thread. Playwright's sync API refuses a SECOND driver on a
    thread that already hosts one ("Sync API inside the asyncio loop" — the
    first driver's loop stays attached to the thread), so the second
    independent session gets its own thread with no loop at all. Same
    credential-free LiveBrowserSession underneath; calls are marshalled with
    submit(...).result() so callers stay synchronous."""
    if not bb.is_configured():
        return None
    from concurrent.futures import ThreadPoolExecutor
    ex = ThreadPoolExecutor(max_workers=1,
                            thread_name_prefix="live-observer-2")
    holder: dict = {}

    def _session() -> LiveBrowserSession:
        if "s" not in holder:
            holder["s"] = LiveBrowserSession(allowed_hostnames=hostnames)
        return holder["s"]

    def observe(url: str) -> dict:
        return ex.submit(lambda: _session().observe(url)).result()

    def observe_with_entry_gate(base_url: str, entry_gate: dict) -> dict:
        return ex.submit(
            lambda: _session().observe_with_entry_gate(base_url, entry_gate)).result()

    def close():
        try:
            if "s" in holder:
                ex.submit(holder["s"].close).result()
        finally:
            ex.shutdown(wait=False)

    observe.observe_with_entry_gate = observe_with_entry_gate
    observe.close = close
    return observe
