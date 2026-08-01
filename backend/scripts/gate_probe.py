"""Replay a CANDIDATE entry-gate declaration through the same
Browserbase+Playwright browser the adapter factory drives, and report what it
reaches — used to author portal_families.json entry gates with evidence
instead of guesses.

Read-only in effect: the declared vocabulary (CLICK / SCROLL_TO_BOTTOM /
CHECK) is the recon replay's own, every action stays on the declared
hostnames, and nothing is ever typed or submitted. CAPTCHA/anti-bot walls are
reported, never solved.

  .venv/bin/python scripts/gate_probe.py '<json>'

where <json> is {"base_url": ..., "hostnames": [...], "actions": [...],
"expect_path": ..., "form_ready_selector": ...} (actions optional: an empty
list just observes the landing page).
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, ".")


def main() -> int:
    spec = json.loads(sys.argv[1])
    base_url = spec["base_url"]
    hostnames = [h.lower() for h in spec.get("hostnames") or []]
    actions = spec.get("actions") or []
    out: dict = {"base_url": base_url, "steps": [], "ok": False}

    from urllib.parse import urlparse
    from app.providers import browser as bb
    if not bb.is_configured():
        print(json.dumps({"error": "browserbase not configured"}))
        return 2

    session = bb.create_session()
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(session["connect_url"])
            page = browser.contexts[0].pages[0]
            page.goto(base_url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(4000)

            def snap(label):
                host = urlparse(page.url).hostname or ""
                fields = page.evaluate(
                    """() => Array.from(
                         document.querySelectorAll('input, select, textarea'))
                       .filter(e => e.type !== 'hidden' && e.offsetParent !== null)
                       .slice(0, 40)
                       .map(e => ({tag: e.tagName, type: e.type || '',
                                   id: e.id || '', name: e.name || ''}))""")
                buttons = page.evaluate(
                    """() => Array.from(document.querySelectorAll(
                         'button, a[role="button"], input[type="submit"]'))
                       .filter(e => e.offsetParent !== null).slice(0, 25)
                       .map(e => (e.innerText || e.value || '').trim().slice(0, 50))
                       .filter(Boolean)""")
                walls = page.evaluate(
                    """() => ({turnstile: !!document.querySelector(
                                 '[name="cf-turnstile-response"], .cf-turnstile'),
                               recaptcha: !!document.querySelector(
                                 '.g-recaptcha, [name="reCaptchaResponse"], '
                                 + 'iframe[src*="recaptcha"]'),
                               hcaptcha: !!document.querySelector('.h-captcha')})""")
                out["steps"].append({
                    "label": label, "url": page.url, "host": host,
                    "on_declared_host": any(host == h or host.endswith("." + h)
                                            for h in hostnames),
                    "visible_fields": len(fields), "fields": fields[:25],
                    "buttons": buttons, "walls": walls,
                    "title": page.title()[:80]})

            snap("landing")
            for i, a in enumerate(actions, 1):
                act, sel = a.get("action"), a.get("selector") or ""
                if act not in ("CLICK", "SCROLL_TO_BOTTOM", "CHECK"):
                    out["steps"].append({"label": f"step{i}",
                                         "error": f"action {act!r} outside vocabulary"})
                    break
                try:
                    loc = page.locator(sel).first
                    if act == "CLICK":
                        loc.click(timeout=10000)
                    elif act == "CHECK":
                        loc.check(timeout=10000)
                    else:
                        page.evaluate(
                            "(s) => { const el = document.querySelector(s);"
                            " if (el) el.scrollTop = el.scrollHeight; }", sel)
                    page.wait_for_timeout(2500)
                    snap(f"after step{i} {act} {sel[:40]}")
                except Exception as e:  # noqa: BLE001 — report, don't crash
                    out["steps"].append({"label": f"step{i} {act} {sel[:40]}",
                                         "error": str(e)[:160]})
                    break
            expect = spec.get("expect_path") or ""
            frs = spec.get("form_ready_selector") or ""
            last = next((s for s in reversed(out["steps"]) if "url" in s), {})
            out["reached_expect_path"] = bool(expect) and expect in (last.get("url") or "")
            if frs:
                try:
                    out["form_ready"] = page.locator(frs).first.is_visible()
                except Exception:  # noqa: BLE001
                    out["form_ready"] = False
            out["ok"] = all("error" not in s for s in out["steps"])
            browser.close()
    finally:
        try:
            bb.close_session(session["id"])
        except Exception:  # noqa: BLE001
            pass
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
