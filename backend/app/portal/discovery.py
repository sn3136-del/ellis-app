"""Official visa-portal discovery.

`discover_official_visa_portal` is an allowlisted, backend-executed tool. Kimi
may REQUEST discovery for a country, but the backend runs the controlled search
and applies deterministic official-domain verification. The tool can only ever
produce a DISABLED adapter draft for administrator review — it can never
activate an adapter, create an account, pay, book, or submit.

Search runs through a controlled provider (Browserbase-backed when configured);
otherwise it returns an honest "search unavailable" result rather than guessing.
Kimi is never assumed to have inherent internet access.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from ..config import settings

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


def query_variants(country: str) -> list[str]:
    c = country.strip()
    return [
        f"official {c} tourist visa application",
        f"official {c} e-visa",
        f"{c} official immigration tourist visa",
        f"{c} ministry of foreign affairs visa",
        f"{c} embassy tourist visa",
        f"authorized {c} visa application center",
        f"{c} consulate tourist visa official",
    ]


def classify_domain(url: str) -> dict:
    host = (urlparse(url).netloc or url).lower()
    is_gov = any(host.endswith(t) or (t.strip(".") in host.split(".")) for t in _GOV_TLDS)
    looks_official = is_gov or any(h in host for h in _GOV_HINTS)
    contractor = any(host.endswith(c) for c in _KNOWN_CONTRACTORS)
    return {"host": host, "is_government_domain": is_gov,
            "looks_official": looks_official, "is_known_contractor": contractor}


def _search(query: str) -> list[str]:
    """Controlled search. Returns candidate URLs. Uses Browserbase when
    configured; otherwise raises SearchUnavailable (never fabricates results)."""
    s = settings()
    if not s.browserbase_api_key:
        raise SearchUnavailable("no controlled search provider configured")
    # A real implementation drives a Browserbase page to a search engine and
    # scrapes result links. That live scrape is an activation step; we do not
    # fabricate results here.
    raise SearchUnavailable("live Browserbase search not activated in this build")


class SearchUnavailable(Exception):
    pass


def discover_official_visa_portal(*, country: str, visa_type: str = "tourist") -> dict:
    """Returns a DISABLED adapter draft + verification evidence. Never activates."""
    import datetime
    queries = query_variants(country)
    candidates: list[dict] = []
    search_status = "ok"
    try:
        seen = set()
        for q in queries:
            for url in _search(q):
                host = classify_domain(url)["host"]
                if host in seen:
                    continue
                seen.add(host)
                candidates.append({"url": url, **classify_domain(url), "found_by": q})
    except SearchUnavailable as e:
        search_status = f"unavailable: {e}"

    # Verified candidates: government-domain, or known contractor WITH an official
    # linking source (the latter must be supplied by a human reviewer here).
    verified = [c for c in candidates if c["is_government_domain"]]
    draft = {
        "country": country,
        "visa_type": visa_type,
        "queries": queries,
        "candidates": candidates,
        "verified_candidates": verified,
        "verification_timestamp_note": "set by caller with a fixed clock",
        "adapter_status": "disabled_draft",          # NEVER auto-enabled
        "production_enabled": False,
        "requires_admin_review": True,
        "search_status": search_status,
        # Fields an admin fills during review before an adapter can be built.
        "review_checklist": [
            "confirm official domain / authorized-contractor link",
            "record registration + authentication rules",
            "record required documents",
            "record payment + appointment + declaration rules",
            "legal review of automation terms",
        ],
    }
    return draft
