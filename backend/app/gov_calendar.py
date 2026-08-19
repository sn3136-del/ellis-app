"""Real appointment calendars, on the government systems that have one.

Almost every booking system hides its slots behind the applicant's own account
— Italy, the US, Portugal, VFS, TLScontact. Two do not: Germany's RK-Termin
(the Foreign Office's own system, serving 190+ missions worldwide) and Poland's
e-Konsulat. Neither has accounts at all. The only thing between an anonymous
visitor and the real month grid is a single image CAPTCHA.

That is a shape Ellis can serve honestly, because it already has the piece it
needs: the applicant solves the CAPTCHA themselves in the secure window. Ellis
never solves it, never registers anything, and never clicks a date.

Two rules this module will not bend:

  1. ELLIS NEVER CHOOSES A DATE. On e-Konsulat a click places a one-hour hold
     on a real slot; on RK-Termin it moves toward a booking. So Ellis READS the
     grid and never picks from it — not from stored preferences, not "the
     earliest", not ever. The applicant chooses.

     Opening the day the APPLICANT selected is a different act, and it is
     allowed: `open_day` below navigates their own window to the exact href
     that day carried in the grid Ellis read. That is their choice being
     carried out, not Ellis making one. It is the same line the flow schema
     draws around `appointment_selection` — "the slot is ALWAYS theirs to
     choose, never auto-picked from their preferences". The href must be one
     the grid actually produced; a fabricated or off-host URL is refused.
  2. ELLIS NEVER SOLVES THE CAPTCHA. It detects the gate and hands off. A
     system that automated the challenge would be evading the exact control
     these ministries put there deliberately.
"""
from __future__ import annotations

import re

RK_BASE = "https://service2.diplo.de/rktermin/extern/"

# Verified live 2026-08-02 on service2.diplo.de.
RK_CAPTCHA_INPUT = 'input[name="captchaText"]'
RK_CAPTCHA_WIDGET = "captcha"
RK_SUBMIT = 'input[name="action:appointment_showMonth"]'
RK_REFRESH = 'input[name="action:appointment_refreshCaptchamonth"]'


class CalendarUnavailable(Exception):
    """This system has no readable calendar for Ellis (blocked, or the walk
    could not reach the month view)."""


def rk_termin_missions(driver) -> list[dict]:
    """Every mission RK-Termin serves, by NAME.

    An applicant does not know their consulate is 'peki'; they know it is in
    Beijing. The list is read live rather than pinned because missions open,
    close and are renamed, and a stale code sends someone to a page that no
    longer exists.
    """
    driver.goto(f"{RK_BASE}choose_locationList.do")
    rows = driver.evaluate(
        "() => [...document.querySelectorAll('a')]"
        ".filter(a => /locationCode=/.test(a.href))"
        ".map(a => ({name:(a.innerText||'').replace(/\\s+/g,' ').trim().slice(0,60),"
        "            code:(a.href.match(/locationCode=([^&]+)/)||[])[1]}))") or []
    seen, out = set(), []
    for r in rows:
        code = (r.get("code") or "").strip()
        name = (r.get("name") or "").strip()
        if code and name and code not in seen:
            seen.add(code)
            out.append({"code": code, "name": name})
    out.sort(key=lambda m: m["name"])
    return out


def rk_termin_walk(driver, *, location_code: str, realm_id: str = "",
                   category_id: str = "") -> dict:
    """Walk RK-Termin from a mission's realm list to its calendar gate.

    The category ids ROT — the ones a curated adapter pinned in July no longer
    existed in August — so the walk always re-reads the live lists instead of
    trusting stored ids.
    """
    def links_matching(pattern: str) -> list[dict]:
        # Each entry on these pages is a bold-14pt heading div, then a long
        # description (paragraphs, external links), then the entry's own
        # Continue link. The entry's NAME is that heading — the nearest text
        # above the link is usually the description's LAST sentence ("To
        # proceed, please click on Continue below"), which mislabelled every
        # queue. So the label is the last heading before the link in document
        # order; the nearest-text walk stays only as a fallback for pages
        # without such headings.
        return driver.evaluate(
            "(p) => {"
            "  const norm = s => String(s||'').replace(/\\s+/g,' ').trim();"
            "  const heads = [...document.querySelectorAll("
            "    'div[style*=\"14pt\"], h4, legend')].filter(h => norm(h.textContent));"
            "  const CHROME = /^(continue|return|back|weiter|zur.ck)$/i;"
            "  const all = [...document.body.querySelectorAll('*')];"
            "  return [...document.querySelectorAll('a')]"
            "    .filter(a => new RegExp(p).test(a.href))"
            "    .map(a => {"
            "      let label = '';"
            "      for (const h of heads) {"
            "        if (h.compareDocumentPosition(a) & Node.DOCUMENT_POSITION_FOLLOWING)"
            "          label = norm(h.textContent);"
            "        else break;"
            "      }"
            "      for (let i = all.indexOf(a) - 1; i >= 0 && !label; i--) {"
            "        const el = all[i];"
            "        if (el.querySelector && el.querySelector('a')) continue;"
            "        const t = norm(el.textContent);"
            "        if (t && t.length > 3 && t.length < 200 && !CHROME.test(t)) label = t;"
            "      }"
            "      return {text: label.slice(0,160) || (a.innerText||'').trim(),"
            "              query: (a.href.split('?')[1]||'')};"
            "    });"
            "}", pattern) or []

    driver.goto(f"{RK_BASE}choose_realmList.do?locationCode={location_code}")
    realms = links_matching("realmId=")
    if not realms:
        raise CalendarUnavailable(
            f"no appointment areas listed for location {location_code!r}")
    # A mission's AREAS are a real choice, not a detail to guess at. Shanghai
    # offers two — national visas (over 90 days) and consular matters without a
    # visa — and defaulting to the last one silently served passport/ID slots
    # to someone who came for a visa. When the caller names no realm, prefer
    # the area whose LABEL says visa (Visa/Visum — order across 196 missions
    # is not a contract), fall back to the first, and always return the full
    # list so the applicant can choose. Negated mentions do not count: the
    # consular area often says "without a visa"/"ohne Visum", and matching
    # that would recreate the exact bug this default exists to prevent.
    def _visa_area(label: str) -> bool:
        # Negations wear many coats: Shanghai says "without a visa", Peking
        # says "(au\u00dfer Visa)" — except visas — on its CONSULAR area. Any
        # excluding word directly before the visa word disqualifies the
        # mention; it does not disqualify the area if it also names visas
        # positively elsewhere in the label.
        # Up to two filler words ride between the negation and the visa
        # word: "except FOR visa" (Peking, English), "without A visa"
        # (Shanghai), "au\u00dfer Visa" (Peking, German).
        neg = (r"\b(no|not|without|ohne|kein\w*|au\u00dfer|ausser|except\w*|excluding)"
               r"\s+(\w+\s+){0,2}(visum|visa)\w*")
        stripped = re.sub(neg, " ", label, flags=re.I)
        return bool(re.search(r"visum|visa|\u7b7e\u8bc1", stripped, re.I))

    default_q = next((r["query"] for r in realms if _visa_area(r["text"])),
                     realms[0]["query"])
    realm_q = next((r["query"] for r in realms if realm_id
                    and f"realmId={realm_id}" in r["query"]), default_q)

    driver.goto(f"{RK_BASE}choose_categoryList.do?{realm_q}")
    cats = links_matching("categoryId=")
    if not cats:
        raise CalendarUnavailable("no appointment categories listed for this area")
    # The QUEUE default mirrors the area default: this lane is offered for
    # self-employed applicants (the owner's product scoping, told to
    # Trip.com), so when the caller names no category the queue whose label
    # covers self-employment is preferred — Shanghai lists specialty cooks
    # first, and position is not a contract. No match falls back to the
    # first, and the applicant's explicit choice always wins.
    # Two preference tiers: the queue NAMING self-employment first; failing
    # that, the general employment queue (Chengdu names no self-employment
    # queue — its work queue is the honest home for a self-employed
    # applicant, not the study queue that happens to list first).
    default_cat_q = next((c["query"] for c in cats
                          if re.search(r"selbstst|selbst.ndig|self.?employ",
                                       c["text"], re.I)),
                         next((c["query"] for c in cats
                               if re.search(r"erwerbst.tig|arbeitsaufnahme"
                                            r"|employment", c["text"], re.I)),
                              cats[0]["query"]))
    cat_q = next((c["query"] for c in cats if category_id
                  and f"categoryId={category_id}" in c["query"]), default_cat_q)

    driver.goto(f"{RK_BASE}appointment_showMonth.do?{cat_q}")
    return {"system": "rk_termin", "query": cat_q,
            "realm_id": _param(realm_q, "realmId"),
            "realms": [{"id": _param(r["query"], "realmId"),
                        "label": r["text"]} for r in realms],
            "category_id": _param(cat_q, "categoryId"),
            "categories": [{"id": _param(c["query"], "categoryId"),
                            "label": c["text"]} for c in cats],
            "captcha_required": captcha_present(driver)}


def _param(query: str, key: str) -> str:
    m = re.search(rf"{re.escape(key)}=([^&]+)", query or "")
    return m.group(1) if m else ""


def captcha_image(driver) -> dict:
    """The CAPTCHA challenge image, as a PNG data URI, so Ellis can show it
    ENLARGED and legible.

    RK-Termin does NOT use an <img>: the challenge is a <captcha> custom
    element holding a <div> whose CSS `background` is an inline
    `data:image/jpg;base64,...` (verified live 2026-08-19). So the data URI is
    lifted straight out of that style when present — no fetch, no canvas (a
    canvas read taints on this page and yields nothing) — and an element
    screenshot is the fallback for any other shape.

    A reading aid only: Ellis does not interpret the picture. The applicant
    reads it and types the answer themselves.
    """
    # 1. The inline data URI on the captcha element's background.
    try:
        found = driver.evaluate(
            "() => {"
            "  const els = [...document.querySelectorAll('captcha *, captcha,"
            "    [id*=captcha i] *, [class*=captcha i] *')];"
            "  for (const e of els) {"
            "    const b = (e.style && e.style.background) || '';"
            "    const c = getComputedStyle(e).backgroundImage || '';"
            "    const m = (b + ' ' + c).match(/url\\(['\"]?(data:image\\/[^'\")]+)/);"
            "    if (m) return m[1];"
            "  }"
            "  return '';"
            "}")
    except Exception:  # noqa: BLE001
        found = ""
    if found:
        return {"image": str(found)}

    # 2. Anything else: screenshot the element the browser is showing.
    shot = getattr(driver, "shot", None)
    if shot is not None:
        for sel in ("captcha", '[id*="captcha" i] div[style*="data:image"]',
                    'img[src*="captcha" i]', 'img[id*="captcha" i]'):
            try:
                data = shot(sel)
            except Exception:  # noqa: BLE001 — try the next candidate
                continue
            if data:
                return {"image": "data:image/png;base64," + data}
    return {"image": ""}


def focus_captcha(driver, *, zoom: float = 1.7) -> dict:
    """Zoom the live page around the challenge and CENTRE it in the viewport.

    Purely presentational: the applicant reads the characters from this window
    while they type. Ellis changes how the page is displayed, never what it
    says or does.

    Zooming from the top-left corner (the obvious first attempt) pushes the
    form off the left edge — the labels and the Continue button get clipped.
    So after scaling, the captcha's own box is measured and the page is
    scrolled so that box sits in the MIDDLE of the viewport, horizontally and
    vertically. Safe to fail: an unzoomable page is still readable, just
    smaller.
    """
    try:
        ok = driver.evaluate(
            "(z) => {"
            "  document.body.style.zoom = z;"
            "  const sels = ['captcha', 'input[name=\"captchaText\"]',"
            "                '[id*=\"captcha\" i]'];"
            "  let el = null;"
            "  for (const s of sels) { el = document.querySelector(s); if (el) break; }"
            "  if (!el) return false;"
            "  const r = el.getBoundingClientRect();"
            "  const cx = r.left + window.scrollX + r.width / 2;"
            "  const cy = r.top + window.scrollY + r.height / 2;"
            "  window.scrollTo({"
            "    left: Math.max(0, cx - window.innerWidth / 2),"
            "    top:  Math.max(0, cy - window.innerHeight / 2)"
            "  });"
            "  return true;"
            "}", zoom)
        return {"zoomed": bool(ok), "zoom": zoom}
    except Exception:  # noqa: BLE001 — presentation only
        return {"zoomed": False, "zoom": 1.0}


def submit_captcha(driver, *, text: str) -> dict:
    """Type the answer THE APPLICANT read, and continue.

    The line this keeps: Ellis never READS the challenge image. No OCR, no
    model, no solving service — `text` is what a human typed into Ellis after
    looking at it. Ellis only transcribes their answer into the portal's own
    field, exactly as it transcribes a signed declaration. If a machine ever
    produced this string, the control these ministries put there would have
    been defeated, so nothing in Ellis may generate it.

    Refuses when no challenge is showing (nothing to answer) and when the
    answer is empty or implausibly long.
    """
    answer = str(text or "").strip()
    if not answer:
        raise CalendarUnavailable("no answer given — the applicant reads the "
                                  "image and types it themselves")
    if len(answer) > 24:
        raise CalendarUnavailable("that does not look like a challenge answer")
    if not captcha_present(driver):
        raise CalendarUnavailable("no image check is showing right now")
    driver.fill(RK_CAPTCHA_INPUT, answer)
    # Continue, never "Load another picture": the day-page gate lists its
    # refresh button FIRST, so a bare input[type=submit] click would refresh
    # the challenge forever instead of continuing.
    for sel in (RK_SUBMIT,
                'input[type="submit"][name^="action:appointment_"]'
                ':not([name*="efreshCaptcha"])',
                'input[type="submit"]:not([name*="efreshCaptcha"])',
                'button[type="submit"]'):
        try:
            if driver.evaluate("() => !!document.querySelector(%r)" % sel):
                driver.click(sel)
                break
        except Exception:  # noqa: BLE001 — try the next candidate
            continue
    still = captcha_present(driver)
    return {"submitted": True, "still_challenged": still,
            "note": ("That answer was wrong or the image refreshed — read the "
                     "new one and try again." if still else
                     "Accepted. Reading the month.")}


def captcha_present(driver) -> bool:
    """Is the image CAPTCHA standing in front of the month view?"""
    # Either the answer field OR the challenge element itself: RK-Termin
    # renders the picture in a <captcha> custom element, and a page can show
    # one without the other while it is mid-refresh.
    try:
        return bool(driver.evaluate(
            "() => !!(document.querySelector('input[name=\"captchaText\"]')"
            " || document.querySelector('captcha'))"))
    except Exception:  # noqa: BLE001 — an unreadable page is treated as gated
        return True


def read_month(driver) -> list[dict]:
    """Read the month grid WITHOUT clicking anything.

    Returns one entry per bookable day: its label and the link that would open
    it. Ellis hands these to the applicant; the applicant is the one who
    clicks, because on these systems a click reserves.
    """
    if captcha_present(driver):
        raise CalendarUnavailable(
            "the portal's image check has not been completed yet — the "
            "applicant completes it in the secure window, never Ellis")
    # RK-Termin's available-day cells say "Appointments are available" as their
    # visible text; the actual DATE lives in the link's href (dateStr=DD.MM.YYYY)
    # or, failing that, the calendar cell. Pull the date out so the applicant
    # sees a date, never the cell's phrasing.
    days = driver.evaluate(
        "() => {"
        "  const dateOf = (a) => {"
        "    const h = a.href || '';"
        "    let m = h.match(/(?:dateStr|datum|date)=([0-9]{1,2}[.\\/-][0-9]{1,2}[.\\/-][0-9]{4})/i);"
        "    if (m) return m[1];"
        "    m = h.match(/([0-9]{1,2}[.\\/-][0-9]{1,2}[.\\/-][0-9]{4})/);"
        "    if (m) return m[1];"
        "    let n = a;"
        "    for (let i=0;i<4 && n;i++,n=n.parentElement) {"
        "      const t = (n.getAttribute && (n.getAttribute('title')||n.getAttribute('id'))) || '';"
        "      const mm = String(t).match(/([0-9]{1,2}[.\\/-][0-9]{1,2}[.\\/-][0-9]{4})/);"
        "      if (mm) return mm[1];"
        "    }"
        "    return '';"
        "  };"
        "  return [...document.querySelectorAll('a')]"
        "    .filter(a => /appointment_showDay\\.do/.test(a.href))"
        "    .map(a => {"
        "      const d = dateOf(a);"
        "      return {label: d || (a.innerText||'').trim().slice(0,40),"
        "              date: d, href: a.href,"
        "              title:(a.getAttribute('title')||'').slice(0,80)};"
        "    });"
        "}") or []
    return [d for d in days if d.get("href")]


def month_summary(driver) -> dict:
    """What the applicant should be told about this month: whether any day is
    bookable, and how many. An empty month is a real answer — 'no appointments
    available' is what these systems show most of the time, and pretending
    otherwise would send someone to refresh a page forever."""
    try:
        days = read_month(driver)
    except CalendarUnavailable as e:
        return {"readable": False, "reason": str(e), "days": []}
    text = ""
    try:
        text = (driver.evaluate("() => document.body ? document.body.innerText : ''")
                or "")[:400]
    except Exception:  # noqa: BLE001
        pass
    none_free = bool(re.search(
        r"keine|no appointments|nicht verf|brak termin|unavailable", text, re.I))
    return {"readable": True, "days": days, "bookable_count": len(days),
            "none_available": (not days) or none_free,
            "notice": text.strip()[:200]}


# Hosts whose day links open_day will follow. A day URL that is not on the
# system Ellis just read is refused outright — the applicant's window is never
# steered somewhere the calendar did not name.
DAY_LINK_HOSTS = ("service2.diplo.de", "secure.e-konsulat.gov.pl",
                  "secure2.e-konsulat.gov.pl")

_DAY_HREF_RE = re.compile(r"appointment_showDay\.do", re.IGNORECASE)


def read_day_times(driver, *, href: str, known_hrefs: list[str] | None = None) -> dict:
    """The TIMES offered on one open day, read without booking anything.

    The month grid says WHICH DAYS are open; the times live on the day's own
    page. This opens the day the applicant picked (same three guards as
    open_day) and reads the slot labels it offers. Reading reserves nothing —
    on these systems it is the CONFIRM at the end of the booking form that
    takes a slot, not looking at the list.
    """
    # An EMPTY href reads the page the applicant's window is already on —
    # used right after they solve a day-page challenge, where re-navigating
    # would only summon the gate again. Navigation still requires a real,
    # known day link.
    if str(href or "").strip():
        opened = open_day(driver, href=href, known_hrefs=known_hrefs)
    else:
        landed = ""
        try:
            landed = driver.evaluate("() => location.href") or ""
        except Exception:  # noqa: BLE001
            pass
        opened = {"opened": False, "url": landed}
    # RK-Termin re-challenges on some navigations: the day arrives gated, not
    # empty. Say which, so the applicant is asked for the picture again
    # instead of being told there are no times.
    if captcha_present(driver):
        return {"url": opened.get("url", ""), "times": [], "count": 0,
                "none_available": False, "captcha_required": True}
    times = []
    try:
        times = driver.evaluate(
            "() => {"
            "  const seen = new Set(); const out = [];"
            "  const push = (label, value) => {"
            "    const t = String(label || '').replace(/\\s+/g, ' ').trim();"
            "    const m = t.match(/([0-9]{1,2}[:.][0-9]{2})/);"
            "    if (!m) return;"
            "    const time = m[1].replace('.', ':');"
            "    if (seen.has(time)) return;"
            "    seen.add(time);"
            "    out.push({time, label: t.slice(0, 60), value: String(value || '')});"
            "  };"
            "  const h4s = [...document.querySelectorAll('h4')];"
            "  document.querySelectorAll('a[href*=\"appointment_showForm.do\"]')"
            "    .forEach(a => {"
            "      let head = null;"
            "      for (const h of h4s) {"
            "        if (h.compareDocumentPosition(a) & Node.DOCUMENT_POSITION_FOLLOWING)"
            "          head = h;"
            "        else break;"
            "      }"
            "      if (head) push(head.textContent, a.href);"
            "    });"
            "  document.querySelectorAll('input[type=radio]').forEach(r => {"
            "    const lab = (r.id && document.querySelector('label[for=\"' + r.id + '\"]'))"
            "             || r.closest('label');"
            "    push(lab ? lab.textContent : (r.value || ''), r.value);"
            "  });"
            "  document.querySelectorAll('a').forEach(a => {"
            "    if (/appointment_/.test(a.href || '')) push(a.textContent, a.href);"
            "  });"
            "  document.querySelectorAll('option').forEach(o => push(o.textContent, o.value));"
            "  return out;"
            "}") or []
    except Exception:  # noqa: BLE001 — an unreadable day is an honest empty
        times = []
    return {"url": opened.get("url", ""), "times": times,
            "count": len(times),
            "none_available": not times}


def open_day(driver, *, href: str, known_hrefs: list[str] | None = None) -> dict:
    """Open the day the APPLICANT picked, in their own window.

    This carries out a choice the applicant already made on a grid Ellis read
    from the live site; it never makes the choice. Three guards, all
    fail-closed:

      * the href must look like this system's own day link;
      * its host must be one of DAY_LINK_HOSTS;
      * when the caller passes the hrefs it showed the applicant, the target
        must be one of THOSE — so a stale or edited link cannot send someone
        to a day they never saw.

    Returns where the window landed. It does not fill the booking form or
    submit anything: the applicant's name, passport and email are theirs to
    enter, and the confirmation email is theirs to receive.
    """
    url = str(href or "").strip()
    if not url or not _DAY_HREF_RE.search(url):
        raise CalendarUnavailable("that is not a day link from this calendar")
    host = ""
    m = re.match(r"https?://([^/]+)/", url)
    if m:
        host = m.group(1).lower()
    if not any(host == h or host.endswith("." + h) for h in DAY_LINK_HOSTS):
        raise CalendarUnavailable(f"refusing to open a day link on {host!r}")
    if known_hrefs:
        if url not in set(known_hrefs):
            raise CalendarUnavailable(
                "that day is not one of the ones Ellis showed — read the "
                "month again and pick from the current grid")
    driver.goto(url)
    landed = ""
    try:
        landed = driver.evaluate("() => location.href") or ""
    except Exception:  # noqa: BLE001
        pass
    return {"opened": True, "url": landed or url,
            "note": ("The day is open in your own window. Enter your details "
                     "there and confirm — the booking and its confirmation "
                     "email are yours to complete.")}


_FORM_QUERY_RE = re.compile(r"^[A-Za-z0-9=&_.\-]+$")


_TIME_HREF_RE = re.compile(r"appointment_showForm\.do", re.IGNORECASE)


def open_time(driver, *, href: str, known_hrefs: list[str]) -> dict:
    """Open the booking form behind the TIME the applicant picked.

    Same contract as open_day: this carries out a choice the applicant made
    from a list Ellis read off the day page — it never makes one. The href
    must be a real "Book this appointment" link, on a known host, and one of
    the exact links Ellis showed; anything else is refused.
    """
    url = str(href or "").strip()
    if not url or not _TIME_HREF_RE.search(url):
        raise CalendarUnavailable("that is not a booking link from this day")
    host = ""
    m = re.match(r"https?://([^/]+)/", url)
    if m:
        host = m.group(1).lower()
    if not any(host == h or host.endswith("." + h) for h in DAY_LINK_HOSTS):
        raise CalendarUnavailable(f"refusing to open a booking link on {host!r}")
    if not known_hrefs or url not in set(known_hrefs):
        raise CalendarUnavailable(
            "that time is not one of the ones Ellis showed — read the day "
            "again and pick from the current list")
    driver.goto(url)
    landed = ""
    try:
        landed = driver.evaluate("() => location.href") or ""
    except Exception:  # noqa: BLE001
        pass
    return {"opened": True, "url": landed or url}


def open_book_form(driver, *, query: str) -> dict:
    """Open the new-appointment form for the category the APPLICANT chose.

    `query` must be the site's own query string, exactly as rk_termin_walk
    returned it — never hand-built. Waiting-list categories carry no date at
    all (registering joins a queue, not a slot); dated categories reach the
    same form from their day page.
    """
    q = str(query or "").strip()
    if (not q or not _FORM_QUERY_RE.match(q)
            or "categoryId=" not in q or "locationCode=" not in q):
        raise CalendarUnavailable(
            "that is not a category from this mission's own list")
    driver.goto(f"{RK_BASE}appointment_showForm.do?{q}")
    return read_book_form(driver)


def read_book_form(driver) -> dict:
    """The booking form as the applicant will see it: every question, its live
    options, and which parts are theirs alone.

    Field ids and definitionIds ROT monthly, so nothing here is pinned — the
    questions, options and required marks are read from the live page every
    time, and the fill addresses fields by NAME in the same live DOM so the
    current ids post themselves.
    """
    out = None
    try:
        out = driver.evaluate(
            "() => {"
            "  const form = document.querySelector('form');"
            "  if (!form || !form.querySelector("
            "      'input[name^=\"fields\"], input[name=\"lastname\"]')) return null;"
            "  const all = [...document.body.querySelectorAll('*')];"
            "  const labelFor = (el) => {"
            "    if (el.id) {"
            "      const lab = document.querySelector('label[for=\"' + el.id + '\"]');"
            "      const lt = lab ? (lab.textContent||'').replace(/\\s+/g,' ').trim() : '';"
            "      if (lt) return lt;"
            "    }"
            "    let label = '';"
            "    for (let i = all.indexOf(el) - 1; i >= 0 && !label; i--) {"
            "      const e2 = all[i];"
            "      if (e2.querySelector && e2.querySelector('input,select,textarea,captcha')) continue;"
            "      const t = (e2.textContent||'').replace(/\\s+/g,' ').trim();"
            "      if (t && t.length > 1 && t.length < 160) label = t;"
            "    }"
            "    return label;"
            "  };"
            "  const strip = s => String(s||'').replace(/[^a-zA-Z0-9]/g,'');"
            "  const fields = [];"
            "  for (const el of form.querySelectorAll('input, select')) {"
            "    const t = (el.type||'').toLowerCase();"
            "    if (t === 'hidden' || t === 'submit' || t === 'button') continue;"
            "    let label = labelFor(el).replace(/:\\s*$/,'');"
            "    const required = /^\\*/.test(label);"
            "    label = label.replace(/^\\*\\s*/,'');"
            "    if (el.name === 'captchaText') {"
            "      fields.push({name: el.name, label, kind: 'captcha',"
            "                   applicant_only: true, required: true});"
            "      continue;"
            "    }"
            "    if (t === 'checkbox') {"
            "      fields.push({name: el.name, label, kind: 'checkbox',"
            "                   applicant_only: true, required});"
            "      continue;"
            "    }"
            "    if (el.tagName === 'SELECT') {"
            "      fields.push({name: el.name, label, kind: 'select', required,"
            "                   options: [...el.options].map(o => o.value).filter(Boolean)});"
            "      continue;"
            "    }"
            "    if (el.readOnly) {"
            "      const hid = [...form.querySelectorAll('input[type=hidden]')]"
            "        .find(h => strip(h.name) === strip(el.id||el.name));"
            "      fields.push({name: hid ? hid.name : el.name, label,"
            "                   kind: 'date', required, format: 'DD.MM.YYYY'});"
            "      continue;"
            "    }"
            "    fields.push({name: el.name, label, kind: 'text', required});"
            "  }"
            "  const head = form.querySelector('h1,h2,legend')"
            "            || document.querySelector('h1,h2');"
            "  return {title: (head ? head.innerText : '').trim().slice(0,160),"
            "          fields};"
            "}")
    except Exception:  # noqa: BLE001 — an unreadable page is an honest absence
        out = None
    if not out:
        raise CalendarUnavailable("no booking form on this page")
    out["url"] = ""
    try:
        out["url"] = driver.evaluate("() => location.href") or ""
    except Exception:  # noqa: BLE001
        pass
    out["captcha_required"] = captcha_present(driver)
    return out


def fill_book_form(driver, *, answers: dict) -> dict:
    """Transcribe the APPLICANT'S answers into the booking form — nothing else.

    Fail-closed by construction: it refuses the CAPTCHA box, every checkbox
    (the confirmations are attestations, the applicant's alone), every button,
    hidden plumbing, any field the live form does not carry, and any select
    value that is not one of the site's own options. It clicks nothing —
    Submit is the applicant's. A date answer lands in both halves of the
    datepicker pair IN EACH HALF'S OWN FORMAT: the readonly face the human
    sees gets DD.MM.YYYY, and the hidden twin that actually POSTs gets ISO
    yyyy-mm-dd — the site's own onSelect handler does exactly this
    conversion, and the server parses only the ISO form.
    """
    if not isinstance(answers, dict) or not answers:
        raise CalendarUnavailable("no answers to transcribe")
    clean, refused = {}, []
    for name, value in answers.items():
        if name == "captchaText":
            refused.append({"name": name,
                            "why": "the image check is the applicant's"})
            continue
        clean[str(name)] = str(value)
    result = {"filled": [], "refused": refused}
    if clean:
        out = driver.evaluate(
            "(ans) => {"
            "  const out = {filled: [], refused: []};"
            "  const strip = s => String(s||'').replace(/[^a-zA-Z0-9]/g,'');"
            "  const form = document.querySelector('form');"
            "  if (!form) return null;"
            "  for (const [name, value] of Object.entries(ans)) {"
            "    const el = form.querySelector(`[name=\"${name}\"]`);"
            "    if (!el) { out.refused.push({name, why: 'not on this form'}); continue; }"
            "    const t = (el.type||'').toLowerCase();"
            "    if (t === 'checkbox' || t === 'submit' || t === 'button'"
            "        || el.tagName === 'BUTTON') {"
            "      out.refused.push({name, why: \"attestations and buttons are the applicant's\"});"
            "      continue;"
            "    }"
            "    if (t === 'hidden') {"
            "      const vis = [...form.querySelectorAll('input[readonly]')]"
            "        .find(v => strip(v.id||v.name) === strip(name));"
            "      if (!vis) { out.refused.push({name, why: 'hidden plumbing is not fillable'}); continue; }"
            "      const m = value.match(/^(\\d{2})\\.(\\d{2})\\.(\\d{4})$/);"
            "      el.value = m ? `${m[3]}-${m[2]}-${m[1]}` : value;"
            "      vis.value = value;"
            "      out.filled.push(name); continue;"
            "    }"
            "    if (el.tagName === 'SELECT') {"
            "      if (![...el.options].some(o => o.value === value)) {"
            "        out.refused.push({name, why: \"not one of the site's own options\"});"
            "        continue;"
            "      }"
            "      el.value = value;"
            "      el.dispatchEvent(new Event('change', {bubbles: true}));"
            "      out.filled.push(name); continue;"
            "    }"
            "    if (el.readOnly) {"
            "      const hid = [...form.querySelectorAll('input[type=hidden]')]"
            "        .find(h => strip(h.name) === strip(el.id||el.name));"
            "      const m = value.match(/^(\\d{2})\\.(\\d{2})\\.(\\d{4})$/);"
            "      el.value = value;"
            "      if (hid) hid.value = m ? `${m[3]}-${m[2]}-${m[1]}` : value;"
            "      out.filled.push(name); continue;"
            "    }"
            "    el.value = value;"
            "    el.dispatchEvent(new Event('change', {bubbles: true}));"
            "    out.filled.push(name);"
            "  }"
            "  return out;"
            "}", clean)
        if out is None:
            raise CalendarUnavailable("no booking form on this page")
        result["filled"] = out.get("filled", [])
        result["refused"].extend(out.get("refused", []))
    return result


def relay_confirmations(driver, *, labels: list[str]) -> dict:
    """Tick the confirmation checkboxes the APPLICANT confirmed in Ellis.

    The applicant is shown each statement VERBATIM in the Ellis UI and ticks
    it there; this carries those ticks to the page. A live checkbox is ticked
    only when its own label text matches one the applicant confirmed —
    whitespace aside, no paraphrase counts. Boxes they did not confirm stay
    untouched, and nothing is submitted here.
    """
    wanted = {re.sub(r"\s+", " ", str(l)).strip() for l in (labels or []) if str(l).strip()}
    if not wanted:
        raise CalendarUnavailable("no confirmed statements to relay")
    out = driver.evaluate(
        "(wanted) => {"
        "  const norm = s => String(s||'').replace(/\\s+/g,' ').trim();"
        "  const all = [...document.body.querySelectorAll('*')];"
        "  const labelFor = (el) => {"
        "    if (el.id) {"
        "      const lab = document.querySelector('label[for=\"' + el.id + '\"]');"
        "      const lt = lab ? norm(lab.textContent) : '';"
        "      if (lt) return lt;"
        "    }"
        "    for (let i = all.indexOf(el) - 1; i >= 0; i--) {"
        "      const e2 = all[i];"
        "      if (e2.querySelector && e2.querySelector('input,select')) continue;"
        "      const t = norm(e2.textContent).replace(/^\\*\\s*/,'');"
        "      if (t && t.length > 1 && t.length < 200) return t;"
        "    }"
        "    return '';"
        "  };"
        "  const res = {ticked: [], skipped: []};"
        "  for (const cb of document.querySelectorAll('form input[type=checkbox]')) {"
        "    const label = labelFor(cb);"
        "    if (wanted.includes(label)) {"
        "      cb.checked = true;"
        "      cb.dispatchEvent(new Event('change', {bubbles: true}));"
        "      res.ticked.push(label);"
        "    } else {"
        "      res.skipped.push(label);"
        "    }"
        "  }"
        "  return res;"
        "}", sorted(wanted))
    if out is None:
        raise CalendarUnavailable("no booking form on this page")
    unmatched = sorted(wanted - set(out.get("ticked", [])))
    return {"ticked": out.get("ticked", []),
            "left_unticked": out.get("skipped", []),
            "unmatched": unmatched}


def enter_captcha_answer(driver, *, text: str) -> dict:
    """Type the applicant's CAPTCHA answer into the form's answer box.

    Same contract as submit_captcha: the answer comes from the applicant, who
    read the picture in Ellis — this transcribes it and nothing more. No
    button is pressed here; on the booking form the answer travels with the
    single Submit, which needs the applicant's instruction.
    """
    answer = str(text or "").strip()
    if not answer:
        raise CalendarUnavailable("the applicant has not given an answer to enter")
    if len(answer) > 24:
        raise CalendarUnavailable("that does not look like a picture answer")
    driver.fill('input[name="captchaText"]', answer)
    return {"entered": True}


def submit_book_form(driver, *, applicant_instructed: bool) -> dict:
    """Press Submit — only as the applicant's explicitly relayed instruction.

    The applicant has, in the Ellis UI: answered every question that was
    filled, ticked each confirmation statement shown verbatim, read the
    picture and typed its answer, and pressed the final button. This carries
    that press to the page. Fail-closed: it refuses without that instruction,
    refuses while any confirmation checkbox on the page is unticked, and
    refuses while the picture answer box is empty — a submit the site would
    bounce is not one worth relaying.
    """
    if applicant_instructed is not True:
        raise CalendarUnavailable("the applicant has not instructed this submit")
    state = driver.evaluate(
        "() => {"
        "  const form = document.querySelector('form');"
        "  if (!form) return null;"
        "  const boxes = [...form.querySelectorAll('input[type=checkbox]')];"
        "  const cap = form.querySelector('input[name=\"captchaText\"]');"
        "  return {unticked: boxes.filter(b => !b.checked).length,"
        "          captcha_empty: !!cap && !cap.value.trim(),"
        "          has_submit: !!form.querySelector("
        "            'input[name=\"action:appointment_addAppointment\"]')};"
        "}")
    if not state:
        raise CalendarUnavailable("no booking form on this page")
    if not state.get("has_submit"):
        raise CalendarUnavailable("this page has no submit button")
    if state.get("unticked"):
        raise CalendarUnavailable(
            "a confirmation statement is not ticked yet — it needs the "
            "applicant's confirmation in Ellis first")
    if state.get("captcha_empty"):
        raise CalendarUnavailable(
            "the picture answer box is empty — the applicant types that first")
    # Dated queues: the Book link carries only dateStr, so the form's
    # redundant hidden `date` starts EMPTY and the server bounces the first
    # submit with a date-format complaint (its own re-render then fills the
    # field). Transcribe the site's own slot value across before pressing —
    # nothing is chosen here, dateStr IS the slot the applicant picked.
    try:
        driver.evaluate(
            "() => {"
            "  const d = document.querySelector('input[name=\"date\"]');"
            "  const ds = document.querySelector('input[name=\"dateStr\"]');"
            "  if (d && ds && !d.value.trim() && ds.value.trim())"
            "    d.value = ds.value;"
            "}")
    except Exception:  # noqa: BLE001 — absent fields mean nothing to copy
        pass
    driver.click('input[name="action:appointment_addAppointment"]')
    landed, text = "", ""
    after = None
    try:
        landed = driver.evaluate("() => location.href") or ""
        text = driver.evaluate(
            "() => (document.body.innerText || '').replace(/\\s+/g,' ').trim().slice(0, 600)") or ""
        # An accepted registration leaves the form BEHIND; a rejected one
        # re-renders it with the site's own field messages ("Please enter a
        # date in the Format dd.mm.yyyy") — polite text no error-word scan
        # catches. So the judgment is structural: form still present, or any
        # field message showing, means NOT accepted.
        after = driver.evaluate(
            "() => {"
            "  const errs = [];"
            "  document.querySelectorAll("
            "      '[class*=error], [id*=error], .errorMessage').forEach(e => {"
            "    const t = (e.textContent||'').replace(/\\s+/g,' ').trim();"
            "    if (t && t.length < 160 && !errs.includes(t)) errs.push(t);"
            "  });"
            "  [...document.querySelectorAll('span,div,li,label')].forEach(e => {"
            "    if (e.children.length) return;"
            "    const t = (e.textContent||'').replace(/\\s+/g,' ').trim();"
            "    if (/^(please enter|bitte geben)/i.test(t)"
            "        && t.length < 160 && !errs.includes(t)) errs.push(t);"
            "  });"
            "  return {form_still: !!document.querySelector("
            "            'form input[name^=\"fields\"]'),"
            "          errors: errs.slice(0, 6)};"
            "}")
    except Exception:  # noqa: BLE001
        pass
    after = after or {}
    errors = list(after.get("errors") or [])
    # RK-Termin's stale-token page ("An error occurred while processing your
    # appointment... browser open for a very long time") means the form
    # session died between opening and submitting. Nothing was booked; the
    # only recovery is opening the form afresh.
    expired = bool(re.search(
        r"error occurred while processing your appointment|ref-id",
        text, re.I))
    succeeded = (not expired and not after.get("form_still", True)
                 and not errors)
    # The applicant keeps a picture of the official confirmation, exactly as
    # their own window shows it — the page text can be forwarded, but the
    # page itself is the thing they saw.
    shot = ""
    if succeeded:
        try:
            shot = "data:image/png;base64," + driver.shot("body")
        except Exception:  # noqa: BLE001 — a missing picture is not a failure
            shot = ""
    return {"submitted": True, "url": landed, "page_text": text,
            "errors": errors, "looks_successful": succeeded,
            "session_expired": expired, "screenshot": shot}
