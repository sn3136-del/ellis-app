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

  1. ELLIS NEVER CLICKS A DATE. On e-Konsulat a click places a one-hour hold on
     a real slot; on RK-Termin it moves toward a booking. Ellis READS the grid
     and shows it. The applicant chooses.
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
