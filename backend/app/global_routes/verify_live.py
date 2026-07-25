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
