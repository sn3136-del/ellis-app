"""Official visa-portal discovery (Phase 1).

`discover_official_visa_portal` is an allowlisted, backend-executed tool. Kimi
may REQUEST discovery for a country, but the backend runs the controlled search
and applies deterministic official-domain verification. The tool can only ever
produce a DISABLED adapter draft for administrator review — it can never
activate an adapter, create an account, pay, book, or submit. Kimi never
activates an adapter based on search results.

Search runs through a controlled, pluggable provider (Browserbase-backed / an
approved web-search API when configured via set_search_provider). Absent one, it
returns an honest "search unavailable" result rather than guessing — the first
result is never trusted, and results are never fabricated. Search-engine CAPTCHAs
/ anti-bot controls are never bypassed.

This module also provides scheduled change-detection: a deterministic
fingerprint + diff over an official portal URL/content snapshot. A detected
material change yields a directive to PAUSE the affected adapter pending review
(applied by the ops scheduler through adapters_admin). Fetching a live portal is
an approved-component step, so the monitor requires an injected snapshot fetcher
and never fabricates a fetch.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from urllib.parse import urlparse

# Deterministic official-domain heuristics. A candidate is only "verified" when
# it is an official government TLD/domain, OR an official source explicitly links
# to it as an authorized contractor (that second path requires human evidence).
_GOV_TLDS = (".gov", ".gov.uk", ".gouv.fr", ".go.jp", ".go.kr", ".gov.vn", ".gov.cn",
             ".gov.au", ".gov.sg", ".gov.in", ".govt.nz", ".gob.mx", ".gov.br",
             ".gc.ca", ".europa.eu")
_GOV_HINTS = ("mofa", "immigration", "evisa", "e-visa", "consul", "embassy",
              "visa.go", "mfa", "gov", "migration")
_KNOWN_CONTRACTORS = {  # authorized VAC operators, still require an official link as evidence
    "vfsglobal.com", "tlscontact.com", "blsinternational.com"}


def query_variants(country: str, nationality: str = "", residence: str = "") -> list[str]:
    """Every required search variation for a destination — never trust one query.
    Adds nationality/residence-specific queries when those are known, because
    eligibility and the correct portal often depend on them."""
    c = country.strip()
    qs = [
        f"{c} embassy",
        f"{c} embassy tourist visa",
        f"official {c} tourist visa application",
        f"official {c} e-visa",
        f"{c} immigration tourist visa",
        f"{c} ministry of foreign affairs visa",
        f"{c} consulate tourist visa",
        f"authorized {c} visa application center",
    ]
    if nationality.strip():
        qs.append(f"{c} visa {nationality.strip()}")
    if residence.strip():
        qs.append(f"{c} visa {residence.strip()} residents")
    return qs


def classify_domain(url: str) -> dict:
    host = (urlparse(url).netloc or url).lower()
    is_gov = any(host.endswith(t) or (t.strip(".") in host.split(".")) for t in _GOV_TLDS)
    looks_official = is_gov or any(h in host for h in _GOV_HINTS)
    contractor = any(host.endswith(c) for c in _KNOWN_CONTRACTORS)
    return {"host": host, "is_government_domain": is_gov,
            "looks_official": looks_official, "is_known_contractor": contractor}


@dataclass
class SearchResult:
    url: str
    title: str = ""
    excerpt: str = ""


class SearchUnavailable(Exception):
    pass


# Pluggable search provider. A production deployment registers an approved
# provider (Browserbase-driven search or an approved web-search API) that returns
# a list[SearchResult]. Until then, discovery is honestly "unavailable".
_SEARCH_PROVIDER = None


def set_search_provider(fn):
    """Register a callable(query:str) -> list[SearchResult]. Pass None to clear."""
    global _SEARCH_PROVIDER
    _SEARCH_PROVIDER = fn


def _search(query: str) -> list[SearchResult]:
    """Controlled search. Returns candidate results, or raises SearchUnavailable
    (never fabricates results, never bypasses anti-bot controls)."""
    if _SEARCH_PROVIDER is not None:
        return list(_SEARCH_PROVIDER(query))
    # No provider registered → unavailable (a Browserbase key alone is not a
    # search provider; wiring the live scrape is a deliberate activation step).
    raise SearchUnavailable("no controlled search provider configured")


def _source_hash(r: SearchResult) -> str:
    return hashlib.sha256(f"{r.url}\n{r.title}\n{r.excerpt}".encode()).hexdigest()


def discover_official_visa_portal(*, country: str, visa_type: str = "tourist",
                                  nationality: str = "", residence: str = "",
                                  reviewer: str = "", verification_timestamp: str = "") -> dict:
    """Return a DISABLED adapter draft + full verification evidence. Never activates."""
    queries = query_variants(country, nationality, residence)
    candidates: list[dict] = []
    search_status = "ok"
    try:
        seen = set()
        for q in queries:
            for r in _search(q):
                cls = classify_domain(r.url)
                host = cls["host"]
                if host in seen:
                    continue
                seen.add(host)
                candidates.append({
                    "url": r.url, "title": r.title, "excerpt": (r.excerpt or "")[:500],
                    "source_hash": _source_hash(r), "found_by": q, **cls})
    except SearchUnavailable as e:
        search_status = f"unavailable: {e}"

    # Verified candidates: government-domain only here. A known contractor requires
    # a human-recorded official linking source before it counts as verified.
    verified = [c for c in candidates if c["is_government_domain"]]
    official_verification_urls = [c["url"] for c in verified]
    portal_operator = verified[0]["host"] if verified else ""

    draft = {
        "country": country,
        "visa_type": visa_type,
        "applicant_nationality": nationality,
        "applicant_residence": residence,
        # Nationality/residence eligibility rules are filled by an admin/rules
        # engine during review — never inferred from a search snippet.
        "nationality_residence_rules": None,
        "queries": queries,
        "candidates": candidates,
        "verified_candidates": verified,
        "official_verification_urls": official_verification_urls,
        "portal_operator": portal_operator,
        "domain_evidence": [{"url": c["url"], "host": c["host"],
                             "is_government_domain": c["is_government_domain"],
                             "is_known_contractor": c["is_known_contractor"],
                             "source_hash": c["source_hash"], "found_by": c["found_by"]}
                            for c in candidates],
        "verification_timestamp": verification_timestamp or None,
        "effective_date": None,       # admin records from the official source
        "expiration_date": None,      # admin records where applicable
        "reviewer": reviewer,
        "review_status": "pending_admin_review",
        "portal_limitations": [
            "requirements not yet verified against the official source",
            "fees not yet verified",
            "registration/login/OTP/CAPTCHA/payment/appointment/declaration checkpoints not yet mapped",
            "representative-submission policy not yet reviewed",
        ],
        "adapter_status": "disabled_draft",          # NEVER auto-enabled
        "production_enabled": False,
        "requires_admin_review": True,
        "search_status": search_status,
        # Fields an admin fills during review before an adapter can be built.
        "review_checklist": [
            "confirm official domain / authorized-contractor link",
            "store official-source evidence + hashes",
            "verify current eligibility + requirements",
            "verify current fee rules",
            "record registration + authentication rules",
            "record required documents",
            "map CAPTCHA/OTP/payment/appointment/declaration checkpoints",
            "review representative-submission policy",
            "legal review of automation terms",
        ],
    }
    return draft


# --- Scheduled change detection --------------------------------------------
@dataclass
class PortalSnapshot:
    url: str
    content: str = ""          # fetched HTML/text (by an approved fetcher only)
    fetched_at: str = ""

    def fingerprint(self) -> dict:
        return {"url": self.url,
                "url_hash": hashlib.sha256(self.url.encode()).hexdigest(),
                "content_hash": hashlib.sha256(self.content.encode()).hexdigest(),
                "fetched_at": self.fetched_at}


def detect_portal_change(previous: dict | None, current: dict) -> dict:
    """Deterministic diff over two portal fingerprints. Returns whether a
    material change occurred (URL moved or content hash changed) and the reasons.
    A change means the affected adapter must be PAUSED pending review."""
    if not previous:
        return {"changed": False, "first_seen": True, "reasons": []}
    reasons = []
    if previous.get("url_hash") != current.get("url_hash"):
        reasons.append("official portal URL changed")
    if previous.get("content_hash") != current.get("content_hash"):
        reasons.append("portal content changed")
    return {"changed": bool(reasons), "first_seen": False, "reasons": reasons}


def scan_for_changes(monitored: list[dict], *, fetcher=None) -> dict:
    """Scheduled monitor. `monitored` is a list of
    {adapter_id, url, previous_fingerprint}. `fetcher(url)->PortalSnapshot` is an
    approved live component. Without a fetcher this is honestly unavailable (no
    fabricated fetches). Returns adapters that must be paused for review."""
    if fetcher is None:
        return {"status": "monitoring_unavailable",
                "reason": "no approved portal fetcher configured", "pause": []}
    pause = []
    for m in monitored:
        snap = fetcher(m["url"])
        change = detect_portal_change(m.get("previous_fingerprint"), snap.fingerprint())
        if change["changed"]:
            pause.append({"adapter_id": m["adapter_id"], "url": m["url"],
                          "reasons": change["reasons"], "new_fingerprint": snap.fingerprint()})
    return {"status": "ok", "pause": pause}
