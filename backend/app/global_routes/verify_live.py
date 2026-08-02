"""Safe live verification of portal families (brief Part 12).

Read-only, credential-free observation of a family's public entry page over
Browserbase+Playwright (the same observer the factory uses for recon). It:
  - never creates accounts, never logs in, never reserves appointments,
  - never pays, never submits, never touches CAPTCHA/OTP,
  - records the observation as verification evidence and upgrades the family
    to verified_live (or records the exact failure honestly).
"""
from __future__ import annotations

from sqlalchemy import select

from ..providers import browser as bb
from ..visa_snapshot.authority import is_government_host
from .families import mark_live_verified, mark_unreachable
from .models import PortalFamily


def _default_observer(hostnames):
    from ..portal.live_browser import build_observer_factory
    if not bb.is_configured():
        return None
    return build_observer_factory(hostnames)


def verify_family_live(db, family_id: str, *, observer=None) -> dict:
    """One read-only observation of the family's base URL. Stops before any
    account, appointment, payment or submission surface."""
    fam = db.execute(select(PortalFamily).where(
        PortalFamily.family_id == family_id)).scalars().first()
    if fam is None:
        raise ValueError(f"unknown portal family {family_id}")
    hosts = [h.lower() for h in (fam.hostnames or [])]
    if not hosts:
        return {"family_id": family_id, "ok": False,
                "reason": "family has no hostnames"}
    close = None
    obs = observer
    if obs is None:
        obs = _default_observer(hosts)
        close = getattr(obs, "close", None)
    if obs is None:
        return {"family_id": family_id, "ok": False,
                "reason": "no live browser provider configured (Browserbase key "
                          "absent) — verification honestly unavailable"}
    try:
        raw = obs(fam.base_url)
        if not raw or not raw.get("ok"):
            reason = (raw or {}).get("error") or "page could not be observed"
            mark_unreachable(db, family_id, reason)
            return {"family_id": family_id, "ok": False, "reason": str(reason)[:300]}
        seen_host = str(raw.get("hostname", "")).lower()
        if seen_host not in hosts:
            mark_unreachable(db, family_id,
                             f"redirected off the family hostnames to {seen_host}")
            return {"family_id": family_id, "ok": False,
                    "reason": f"redirected off allowlist to {seen_host}"}
        gov = is_government_host(seen_host)
        evidence = {
            "url": fam.base_url,
            "observed_hostname": seen_host,
            "status": int(raw.get("status", 0)),
            "title": str(raw.get("title", ""))[:120],
            "elements_observed": len(raw.get("elements") or []),
            "links_observed": len(raw.get("links") or []),
            "government_domain": gov,
            "read_only": True,
            "stopped_before": ["account_verification", "appointment_reservation",
                               "payment", "final_submission"],
        }
        # Reachability upgrades identity ONLY for government-domain families.
        # A reachable contractor site proves reachability, not authorization —
        # that still needs official-link evidence, so the status stays put.
        if gov or fam.verification_status in ("verified_official_domain",
                                              "verified_live"):
            mark_live_verified(db, family_id, evidence)
            return {"family_id": family_id, "ok": True, "evidence": evidence}
        fam.verification_evidence = dict(fam.verification_evidence or {},
                                         live_reachability=evidence)
        db.commit()
        return {"family_id": family_id, "ok": True, "evidence": evidence,
                "note": "reachable but identity NOT upgraded — non-government "
                        "domain requires official-link evidence"}
    finally:
        if close:
            try:
                close()
            except Exception:  # noqa: BLE001 - session cleanup is best-effort
                pass


def verify_family_official_link(db, family_id: str, page_url: str,
                                *, fetch=None) -> dict:
    """Prove a non-government portal's identity from the GOVERNMENT's own page.

    Fetches `page_url` (which must itself be on a government host — anything
    else is refused before a byte moves) and looks for a link whose host is one
    of the family's declared hostnames. Found -> the family is
    verified_via_official_link with the page recorded as evidence. Not found ->
    an honest failure; nothing is upgraded on hope."""
    import re as _re
    import urllib.request
    from datetime import datetime, timezone
    from urllib.parse import urlparse
    from ..visa_snapshot.authority import hostname as _hostname
    from .families import mark_official_link_verified

    fam = db.execute(select(PortalFamily).where(
        PortalFamily.family_id == family_id)).scalars().first()
    if fam is None:
        return {"family_id": family_id, "ok": False, "reason": "unknown family"}
    page_host = _hostname(page_url)
    if not is_government_host(page_host):
        return {"family_id": family_id, "ok": False,
                "reason": f"linking page {page_host} is not a government host — "
                          "only the government's own word upgrades identity"}
    if fetch is not None:
        try:
            html = fetch(page_url)
        except Exception as e:  # noqa: BLE001 — an unreachable page is a real answer
            return {"family_id": family_id, "ok": False,
                    "reason": f"could not fetch {page_host}: {str(e)[:120]}"}
        hrefs = _re.findall(r'''(?:href|src)\s*=\s*["']([^"']+)["']''', html)
        link_hosts = {urlparse(h).netloc.lower().split(":")[0]
                      for h in hrefs if "//" in h}
    else:  # pragma: no cover — live path: government sites bot-filter bare
        # HTTP clients (france-visas.gouv.fr 403s urllib), so the page is read
        # with the same real-browser observer recon uses. Read-only, one page.
        from ..portal.live_browser import LiveBrowserSession
        sess = LiveBrowserSession(allowed_hostnames=[page_host])
        try:
            obs = sess.observe(page_url)
        finally:
            sess.close()
        if not obs.get("ok"):
            return {"family_id": family_id, "ok": False,
                    "reason": f"could not fetch {page_host}: "
                              f"{obs.get('error') or obs.get('status')}"}
        link_hosts = {urlparse(l).netloc.lower().split(":")[0]
                      for l in obs.get("links", []) if "//" in l}
    matched = ""
    for want in (fam.hostnames or []):
        w = want.lower()
        if any(lh == w or lh.endswith("." + w) or w.endswith("." + lh.lstrip("www."))
               or lh.lstrip("www.") == w.lstrip("www.") for lh in link_hosts):
            matched = w
            break
    if not matched:
        return {"family_id": family_id, "ok": False,
                "reason": f"{page_host} does not link to any of "
                          f"{fam.hostnames} — identity not proven"}
    evidence = {"page": page_url[:500], "page_host": page_host,
                "matched_host": matched,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "method": "government page links to the portal hostname"}
    mark_official_link_verified(db, family_id, evidence)
    return {"family_id": family_id, "ok": True, "evidence": evidence}


def verify_families_live(db, family_ids: list[str], *, observer_factory=None,
                         log=None) -> dict:
    log = log or (lambda *_: None)
    results = []
    for fid in family_ids:
        log(f"live-verifying portal family {fid}")
        obs = observer_factory(fid) if observer_factory else None
        try:
            results.append(verify_family_live(db, fid, observer=obs))
        except Exception as exc:  # noqa: BLE001 — isolate per family
            results.append({"family_id": fid, "ok": False,
                            "reason": f"{type(exc).__name__}: {exc}"[:300]})
    ok = sum(1 for r in results if r.get("ok"))
    return {"verified": ok, "failed": len(results) - ok, "results": results}
