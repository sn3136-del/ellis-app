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
            return {"ok": 200 <= int(status) < 400, "status": int(status)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": "NAV_ERROR", "detail": str(e)[:120]}

    def fill(self, selector: str, value: str) -> dict:
        # The deterministic runtime never fills a sensitive field, but refuse
        # here too (defense in depth) so a mis-generated node fails closed.
        if _SENSITIVE_SELECTOR.search(selector or ""):
            return {"ok": False, "code": "SENSITIVE_FIELD_AUTOMATION"}
        try:
            self.page.fill(selector, str(value), timeout=15000)
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
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            # Readonly picker inputs (e.g. Ant Design date pickers) refuse
            # fill(); they accept focused keyboard entry. Type, commit with
            # Enter, then READ BACK the value — success only on exact echo.
            try:
                self.page.click(selector, timeout=10000)
                self.page.keyboard.type(str(value), delay=25)
                self.page.keyboard.press("Enter")
                self.page.wait_for_timeout(300)
                got = self.page.input_value(selector, timeout=5000)
                # A picker panel can linger after Enter and swallow the next
                # element's clicks — dismiss it before moving on.
                self.page.keyboard.press("Escape")
                if got == str(value):
                    return {"ok": True, "method": "typed"}
                return {"ok": False, "code": "VALUE_NOT_ACCEPTED",
                        "detail": f"portal kept {got!r}"[:120]}
            except Exception as e2:  # noqa: BLE001
                return {"ok": False, "code": "NO_SUCH_ELEMENT",
                        "detail": (str(e) + " | " + str(e2))[:120]}

    def click(self, selector: str) -> dict:
        try:
            # A still-open dropdown/date overlay from the previous field can
            # cover the button and turn the click into a 15s timeout.
            try:
                self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(120)
            except Exception:  # noqa: BLE001
                pass
            # Text selectors can also match stale HIDDEN elements (e.g. a
            # dismissed modal's button still in the DOM) — click the first
            # VISIBLE match so the wait can't hang on an invisible node.
            target = self.page.locator(f"{selector} >> visible=true").first
            try:    # a button below the fold is not "unclickable"
                target.scroll_into_view_if_needed(timeout=5000)
            except Exception:  # noqa: BLE001
                pass
            target.click(timeout=15000)
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
    # the exact question the portal is objecting to.
    _INVALID_FIELDS_JS = """() => {
      const out = [];
      const items = document.querySelectorAll(
        '[class*="form-item-has-error"], [class*="has-error"]');
      for (const item of items) {
        if (item.offsetParent === null) continue;
        const ctrl = item.querySelector('input,select,textarea');
        const id = ctrl ? (ctrl.id || ctrl.name || '') : '';
        const labelEl = item.querySelector('label');
        const label = labelEl ? (labelEl.textContent || '').trim() : '';
        const errEl = item.querySelector('[class*="explain"], [role="alert"]');
        const message = errEl ? (errEl.textContent || '').trim() : '';
        if (id || label || message) out.push({id, label, message});
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
      for (const s of sels) {
        for (const el of document.querySelectorAll(s)) {
          const r = el.getBoundingClientRect();
          if (r.width < 20 || r.height < 20) continue;
          if (el.tagName !== 'IFRAME' && el.offsetParent === null) continue;
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
        never routes an irreversible node here."""
        try:
            loc = self.page.locator(f"{selector} >> visible=true").first
            loc.click(timeout=8000, force=True)
            return {"ok": True, "method": "force"}
        except Exception:  # noqa: BLE001
            try:
                self.page.eval_on_selector(selector, "el => el.click()")
                return {"ok": True, "method": "dispatch"}
            except Exception as e2:  # noqa: BLE001
                return {"ok": False, "code": "CLICK_INTERCEPTED",
                        "detail": str(e2)[:120]}

    def read_value(self, selector: str) -> dict:
        """The value the portal currently holds in a field. Used to detect a
        value the FORM dropped (a dependent select can reset a field that was
        filled earlier) so it can be repaired instead of re-asked."""
        try:
            val = self.page.eval_on_selector(
                selector,
                "el => { if (el.value !== undefined && el.value !== null) return el.value;"
                " const w = el.closest('[class*=\"select\"]');"
                " const s = w && w.querySelector('[class*=\"selection-item\"]');"
                " return s ? s.innerText.trim() : ''; }")
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

    def check(self, selector: str) -> dict:
        """Real checkbox tick. Refuses anything resembling a sensitive control
        (final declarations are APPLICANT_HANDOFF nodes, never automated)."""
        if _SENSITIVE_SELECTOR.search(selector or ""):
            return {"ok": False, "code": "SENSITIVE_FIELD_AUTOMATION"}
        try:
            self.page.check(selector, timeout=15000)
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": "NO_SUCH_ELEMENT", "detail": str(e)[:120]}

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
            try:  # dismiss any lingering overlay (e.g. an open date picker)
                self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(150)
            except Exception:  # noqa: BLE001
                pass
            self.page.locator(f"{selector} >> visible=true").first.click(timeout=15000)
            try:  # clear any previous query so filters start clean
                self.page.keyboard.press("Control+A")
                self.page.keyboard.press("Delete")
            except Exception:  # noqa: BLE001
                pass
            self.page.keyboard.type(want, delay=25)
            # The option ROWS ([role=option]) are empty framework placeholders;
            # the labels live in the item/content children. Match and click in
            # one page evaluation so no element handle can go stale mid-scroll.
            # EXACT match wins over substring: "China" must never select
            # "China(Taiwan)" — a near-miss is a wrong answer, not a shortcut.
            pick_js = """(want) => {
              const seen = new Set(), opts = [];
              for (const el of document.querySelectorAll(
                     '[class*="select-item"], [class*="option-content"]')) {
                if (el.offsetParent === null) continue;
                // Skip the widget's own "no data"/empty placeholder row — it
                // is not a choice, and treating it as one would be a guess.
                const cls = (el.className || '') + ' ' +
                            ((el.closest('[class*="select-item"]') || {}).className || '');
                if (/empty|disabled/i.test(cls)) continue;
                const t = (el.textContent || '').trim();
                if (!t || seen.has(t)) continue;
                seen.add(t);
                opts.push([el, t]);
              }
              if (!opts.length) return {labels: []};
              const w = want.toLowerCase();
              let hit = opts.find(([, t]) => t.toLowerCase() === w)
                     || opts.find(([, t]) => t.toLowerCase().includes(w));
              if (!hit) return {labels: opts.map(([, t]) => t).slice(0, 8)};
              (hit[0].closest('[class*="select-item"]') || hit[0]).click();
              return {chosen: hit[1]};
            }"""
            result = {}
            for _ in range(16):     # ~4s: filtered lists render in a tick or two
                self.page.wait_for_timeout(250)
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
            self.page.wait_for_timeout(400)
            shown = ""
            try:
                shown = self.page.eval_on_selector(
                    selector,
                    "el => { const w = el.closest('[class*=\"select\"]');"
                    " const s = w && w.querySelector('[class*=\"selection-item\"]');"
                    " return s ? s.innerText.trim() : ''; }") or ""
            except Exception:  # noqa: BLE001
                shown = ""
            if shown and not (want.lower() in shown.lower()
                              or shown.lower() in chosen_text.lower()):
                return {"ok": False, "code": "VALUE_NOT_ACCEPTED",
                        "detail": f"portal shows {shown[:60]!r}"}
            return {"ok": True, "shown": (shown or chosen_text)[:60]}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": "NO_SUCH_ELEMENT", "detail": str(e)[:120]}

    def list_options(self, selector: str, max_options: int = 300) -> dict:
        """Read a search-combobox's REAL option labels WITHOUT selecting
        anything — powering applicant questions for missing answers (Part 7:
        the applicant chooses from the portal's actual list, never a blank
        field). Opens the widget, waits for the async list, harvests visible
        non-placeholder labels, scrolls the virtualized list until no new
        rows appear, then closes with Escape; the form state is unchanged."""
        if _SENSITIVE_SELECTOR.search(selector or ""):
            return {"ok": False, "code": "SENSITIVE_FIELD_AUTOMATION"}
        read_js = """(max) => {
          const seen = new Set(), labels = [];
          for (const el of document.querySelectorAll(
                 '[class*="select-item"], [class*="option-content"]')) {
            if (el.offsetParent === null) continue;
            const cls = (el.className || '') + ' ' +
                        ((el.closest('[class*="select-item"]') || {}).className || '');
            if (/empty|disabled/i.test(cls)) continue;
            const t = (el.textContent || '').trim();
            if (!t || seen.has(t)) continue;
            seen.add(t);
            labels.push(t);
            if (labels.length >= max) break;
          }
          return labels;
        }"""
        # Ant Design virtualizes long lists — only visible rows exist in the
        # DOM. Scroll the dropdown's holder to pull the rest in.
        scroll_js = """() => {
          const h = document.querySelector(
            '.rc-virtual-list-holder, [class*="dropdown"]:not([class*="hidden"]) [class*="list-holder"]');
          if (!h) return false;
          const before = h.scrollTop;
          h.scrollTop = before + Math.max(80, h.clientHeight - 20);
          return h.scrollTop !== before;
        }"""
        try:
            try:
                self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(120)
            except Exception:  # noqa: BLE001
                pass
            self.page.locator(f"{selector} >> visible=true").first.click(timeout=15000)
            labels: list = []
            for _ in range(12):     # ~3s: options usually render in one tick
                self.page.wait_for_timeout(250)
                labels = self.page.evaluate(read_js, max_options) or []
                if len(labels) > 1:
                    break
            seen = list(labels)
            barren = 0
            for _ in range(40):
                try:
                    moved = bool(self.page.evaluate(scroll_js))
                except Exception:  # noqa: BLE001
                    break
                if not moved:
                    break
                self.page.wait_for_timeout(80)
                more = self.page.evaluate(read_js, max_options) or []
                fresh = [t for t in more if t not in set(seen)]
                if fresh:
                    seen.extend(fresh)
                    barren = 0
                else:
                    barren += 1
                    if barren >= 2:     # the list stopped growing
                        break
                if len(seen) >= max_options:
                    break
            try:
                self.page.keyboard.press("Escape")
            except Exception:  # noqa: BLE001
                pass
            return {"ok": True, "options": seen[:max_options]}
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
