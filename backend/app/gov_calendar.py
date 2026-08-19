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
        # The link's own text is the button word ("Continue"), and RK-Termin
        # gives each category no container of its own — the name the applicant
        # needs ("Appointment waiting list A for students with APS procedure")
        # simply PRECEDES the link in document order. So walk backwards from
        # the link and take the nearest real text that is not another button.
        return driver.evaluate(
            "(p) => {"
            "  const CHROME = /^(continue|return|back|weiter|zur.ck)$/i;"
            "  const all = [...document.body.querySelectorAll('*')];"
            "  return [...document.querySelectorAll('a')]"
            "    .filter(a => new RegExp(p).test(a.href))"
            "    .map(a => {"
            "      let label = '';"
            "      for (let i = all.indexOf(a) - 1; i >= 0 && !label; i--) {"
            "        const el = all[i];"
            "        if (el.querySelector && el.querySelector('a')) continue;"
            "        const t = (el.textContent||'').replace(/\\s+/g,' ').trim();"
            "        if (t && t.length > 3 && t.length < 200 && !CHROME.test(t)) label = t;"
            "      }"
            "      return {text: label.slice(0,90) || (a.innerText||'').trim(),"
            "              query: (a.href.split('?')[1]||'')};"
            "    });"
            "}", pattern) or []

    driver.goto(f"{RK_BASE}choose_realmList.do?locationCode={location_code}")
    realms = links_matching("realmId=")
    if not realms:
        raise CalendarUnavailable(
            f"no appointment areas listed for location {location_code!r}")
    realm_q = next((r["query"] for r in realms if realm_id
                    and f"realmId={realm_id}" in r["query"]), realms[-1]["query"])

    driver.goto(f"{RK_BASE}choose_categoryList.do?{realm_q}")
    cats = links_matching("categoryId=")
    if not cats:
        raise CalendarUnavailable("no appointment categories listed for this area")
    cat_q = next((c["query"] for c in cats if category_id
                  and f"categoryId={category_id}" in c["query"]), cats[0]["query"])

    driver.goto(f"{RK_BASE}appointment_showMonth.do?{cat_q}")
    return {"system": "rk_termin", "query": cat_q,
            "categories": [{"id": _param(c["query"], "categoryId"),
                            "label": c["text"]} for c in cats],
            "captcha_required": captcha_present(driver)}


def _param(query: str, key: str) -> str:
    m = re.search(rf"{re.escape(key)}=([^&]+)", query or "")
    return m.group(1) if m else ""


def captcha_image(driver) -> dict:
    """The CAPTCHA challenge image, read from the page and returned as a data
    URI so Ellis can show it ENLARGED for legibility. This is a reading aid
    only: the applicant still types the answer into their own window, and Ellis
    never solves it. Returns an empty image when none is present."""
    try:
        data = driver.evaluate(
            "() => {"
            "  const img = [...document.querySelectorAll('img')]"
            "    .find(i => /captcha/i.test(i.src) || /captcha/i.test(i.id)"
            "            || /captcha/i.test(i.className));"
            "  if (!img) return '';"
            "  try {"
            "    const c = document.createElement('canvas');"
            "    c.width = img.naturalWidth || img.width;"
            "    c.height = img.naturalHeight || img.height;"
            "    c.getContext('2d').drawImage(img, 0, 0);"
            "    return c.toDataURL('image/png');"
            "  } catch (e) { return img.src || ''; }"
            "}")
    except Exception:  # noqa: BLE001
        data = ""
    return {"image": str(data or "")}


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
    for sel in (RK_SUBMIT, 'input[type="submit"]', 'button[type="submit"]'):
        try:
            if driver.evaluate(
                    "() => !!document.querySelector(%r)" % sel):
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
    try:
        return bool(driver.evaluate(
            "() => !!document.querySelector('input[name=\"captchaText\"]')"))
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
    days = driver.evaluate(
        "() => [...document.querySelectorAll('a')]"
        ".filter(a => /appointment_showDay\\.do/.test(a.href))"
        ".map(a => ({label:(a.innerText||'').trim().slice(0,40),"
        "            href:a.href,"
        "            title:(a.getAttribute('title')||'').slice(0,80)}))") or []
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
