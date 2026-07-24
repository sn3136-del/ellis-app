"""Automatic official-source discovery via Kimi K3 (brief §8 "AUTOMATIC
OFFICIAL-SOURCE RESEARCH").

This is the missing production wiring that let a real applicant route (e.g.
USA -> CHN) starve at DISCOVER_OFFICIAL_SOURCES: `fetching.search()` had no
provider outside tests, so a destination with no pre-seeded research file found
zero candidate sources and ended `research_incomplete` with an empty record.

The provider here is a *controlled* search: Kimi K3 PROPOSES candidate
official-source URLs for a query, but it is NEVER trusted. Every candidate is
then independently fetched (real HTTP, with an optional Browserbase render
fallback) and verified to be an official government domain by the deterministic
`authority` module before it can become evidence, and every extracted field
must cite fetched government-page text (`kimi_research.extract`'s grounding
validator). A proposed URL that does not resolve, is not government, or whose
text does not support a claim is dropped honestly. Kimi therefore cannot
fabricate a source, fee, portal, or requirement into the record.

Honesty / safety:
 - No Kimi key configured, or not a real runtime mode -> the default provider is
   absent and `fetching.search()` raises SearchUnavailable (never fabricates).
 - URLs are sanitized (http/https only, deduped, capped); nothing else.
 - Never bypasses anti-bot/CAPTCHA; a blocked fetch stays a recorded failure.
"""
from __future__ import annotations

import re
from functools import lru_cache

from ..config import REAL_ONLY_MODES, settings

# A candidate URL must look like a real absolute http(s) URL.
_URL_RE = re.compile(r"^https?://[^\s\"'<>]+$", re.I)
# Trailing punctuation the model sometimes appends inside prose.
_TRIM = ".,);]}'\">"

_MAX_CANDIDATES = 16

_DISCOVERY_SYSTEM = (
    "You locate the OFFICIAL government pages an applicant must use for a "
    "tourist visa. Given a search query, return the most useful OFFICIAL URLs. "
    "Prioritise SPECIFIC pages that state the actual requirements, not just site "
    "roots:\n"
    "- the exact tourist/visitor visa REQUIREMENTS page for the applicant's "
    "nationality (whether a visa is needed, exemptions, allowed stay),\n"
    "- the visa FEE page and the APPLICATION / how-to-apply page,\n"
    "- the official e-visa / ETA portal if one exists,\n"
    "- the responsible embassy/consulate's tourist-visa page for the applicant's "
    "residence, and the immigration authority's visa section.\n"
    "Rules:\n"
    "- Return ONLY real, official URLs you are confident exist. Prefer government "
    "domains (.gov, .gob, .go.*, .gouv.*, .europa.eu, national immigration/MFA "
    "sites). Deep links to the specific visa page are BETTER than a bare homepage "
    "(a wrong path is simply verified and dropped — but do not fabricate paths).\n"
    "- Do not return travel blogs, agencies, news, or unofficial aggregators.\n"
    "Reply strict JSON: {\"urls\": [\"https://...\", ...]} (max 16, no prose)."
)


def _sanitize_urls(raw) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    if not isinstance(raw, list):
        return out
    for item in raw:
        u = str(item or "").strip().strip(_TRIM).strip()
        if not u or not _URL_RE.match(u):
            continue
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
        if len(out) >= _MAX_CANDIDATES:
            break
    return out


# Injectable proposer for tests: callable(query:str) -> list[str]|dict. None uses
# live Kimi. Keeps the real Moonshot key out of the test path.
_PROPOSER = None


def set_proposer(fn) -> None:
    """Inject callable(query)->list[str] (or a raw {"urls":[...]}) for tests.
    None resets to live Kimi. Clears the memo so injection takes effect."""
    global _PROPOSER
    _PROPOSER = fn
    reset_cache()


def _propose_uncached(query: str) -> tuple[str, ...]:
    """One real Kimi K3 call proposing candidate official URLs for `query`.
    Returns a tuple (hashable/cacheable). Errors -> empty (fetch layer stays
    honest); the caller degrades to stored sources only."""
    if _PROPOSER is not None:
        try:
            raw = _PROPOSER(query)
        except Exception:  # noqa: BLE001
            return tuple()
        if isinstance(raw, dict):
            return tuple(_sanitize_urls(raw.get("urls")))
        return tuple(_sanitize_urls(list(raw or [])))
    from ..providers.kimi import LiveKimiProvider
    try:
        raw = LiveKimiProvider()._chat(_DISCOVERY_SYSTEM, query, json_mode=True)
    except Exception:  # noqa: BLE001 - network/model errors never crash research
        return tuple()
    return tuple(_sanitize_urls((raw or {}).get("urls")))


# Cache proposals for a worker's lifetime: official URLs for a given query are
# stable, and one new route issues several near-identical query variants.
@lru_cache(maxsize=512)
def _propose_cached(query: str) -> tuple[str, ...]:
    return _propose_uncached(query)


def kimi_search_provider(query: str) -> list[str]:
    """`fetching.set_search_provider`-compatible callable: query -> candidate
    URLs proposed by Kimi K3 (untrusted; verified downstream)."""
    return list(_propose_cached(query.strip()))


def discover_candidates_for_route(route: dict) -> list[str] | None:
    """Route-aware discovery: ONE Kimi call returning the comprehensive set of
    candidate official-source URLs for this exact route (destination + passport
    nationality + residence + category). Preferred over the per-query search
    loop because official sources are a property of the route, not the query
    phrasing — one call instead of ~10. Returns None when discovery is not
    available (no Kimi key / not a real mode), so callers fall back to the
    injected per-query `search()` seam (tests) or stored sources only."""
    if not is_available():
        return None
    dest = route.get("destination_country", "")
    nat = route.get("passport_nationality", "")
    res = route.get("lawful_country_of_residence", "")
    cat = route.get("visa_category", "tourist_visa")
    query = (f"destination={dest} passport_nationality={nat} residence={res} "
             f"visa_category={cat}. Find the official immigration authority, the "
             f"responsible embassy/consulate for a {res} resident, the official "
             f"e-visa/ETA portal, any officially appointed visa application "
             f"center, and the official fee and appointment pages.")
    return list(_propose_cached(query.strip()))


def reset_cache() -> None:
    _propose_cached.cache_clear()


def is_available() -> bool:
    """True when a discovery provider should be active: an injected test proposer,
    or a real runtime mode with a configured, enabled Kimi key. Otherwise
    discovery stays honestly unavailable."""
    if _PROPOSER is not None:
        return True
    s = settings()
    return bool(s.moonshot_api_key and s.kimi_enabled
                and s.runtime_mode in REAL_ONLY_MODES)


def default_search_provider():
    """The provider `fetching.search()` falls back to when none is injected.
    None => honest SearchUnavailable (no fabrication)."""
    return kimi_search_provider if is_available() else None
