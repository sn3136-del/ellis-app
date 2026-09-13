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
import threading
import time
from functools import lru_cache

from ..config import REAL_ONLY_MODES, settings

# A candidate URL must look like a real absolute http(s) URL.
_URL_RE = re.compile(r"^https?://[^\s\"'<>]+$", re.I)
# Trailing punctuation the model sometimes appends inside prose.
_TRIM = ".,);]}'\">"

_MAX_CANDIDATES = 16

_DISCOVERY_SYSTEM = (
    "You locate OFFICIAL government entry and visa policy pages for the exact "
    "nationality, residence, travel document and purpose in the query. Return the most useful OFFICIAL URLs. "
    "Prioritise SPECIFIC pages that state the actual requirements, not just site "
    "roots:\n"
    "- the exact entry/visa REQUIREMENTS page for the applicant's "
    "nationality (whether a visa is needed, exemptions, allowed stay),\n"
    "- the visa FEE page and the APPLICATION / how-to-apply page,\n"
    "- the official e-visa / ETA portal if one exists,\n"
    "- the responsible embassy/consulate's applicable visa page for the applicant's "
    "residence, and the immigration authority's visa section.\n"
    "Rules:\n"
    "- A visa-exempt route still needs an official POLICY reference. Do not "
    "substitute a visa application portal for the exemption rule.\n"
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
    return list(_propose_cached(_route_query(route)))


def _route_query(route: dict) -> str:
    dest = route.get("destination_country", "")
    nat = route.get("passport_nationality", "")
    res = route.get("lawful_country_of_residence", "")
    cat = route.get("visa_category", "tourist_visa")
    doc = route.get("travel_document_type", "ordinary_passport")
    purpose = route.get("travel_purpose", "tourism")
    query = (f"destination={dest} passport_nationality={nat} residence={res} "
             f"travel_document_type={doc} travel_purpose={purpose} visa_category={cat}. Find the official immigration authority, the "
             f"responsible embassy/consulate for a {res} resident, the official "
             f"e-visa/ETA portal, any officially appointed visa application "
             f"center, and the official fee and appointment pages.")
    return query.strip()


_BOUNDED_DISCOVERY_SLOTS = threading.BoundedSemaphore(2)
DISCOVERY_TIMEOUT_SECONDS = 20.0
DISCOVERY_MAX_SOURCES = 4


def bounded_route_candidates(route: dict, *, timeout_seconds: float,
                             target_fields: list[str] | None = None,
                             existing_urls: list[str] | None = None) -> dict:
    """One explicit-request proposal operation; returned URLs are not evidence.

    No proposal memo is used here: a failed earlier suggestion must not block
    a later explicit retry. Existing provider rate/suspension controls apply;
    transport retries, if any, share this one operation's hard deadline.
    """
    from . import kimi_primary
    from .bounded_io import call
    from urllib.parse import urlsplit
    budget = max(0.0, min(DISCOVERY_TIMEOUT_SECONDS, timeout_seconds))
    report = {"urls": [], "outcome": "unavailable", "model_discovery_calls": 0}
    if budget <= 0:
        return dict(report, outcome="budget_exhausted")
    if not is_available() or kimi_primary.provider_suspension():
        return report
    deadline = time.monotonic() + budget
    proposer = _PROPOSER
    query = (_route_query(route) + f" Return at most {DISCOVERY_MAX_SOURCES} useful pages, "
             "prioritising the exact entry/exemption rule. Omit filing, fee and appointment pages "
             "when no visa application applies. These are untrusted search hints, not a policy answer.")
    if target_fields:
        query += (" The stored answer has unresolved fields: " + ", ".join(sorted(set(target_fields))) +
                  ". Locate the competent official pages that actually publish these details for this "
                  "route and product; search absence on one page is not proof of non-publication.")
    if existing_urls:
        query += " Find additional pages, not these already cited URLs: " + ", ".join(existing_urls[:8]) + "."

    def propose():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("source discovery budget exhausted")
        report["model_discovery_calls"] = 1
        if proposer is not None:
            return proposer(query)
        return kimi_primary._live_call(_DISCOVERY_SYSTEM, query,
            timeout=remaining, max_tokens=1400,
            source_comparison=True)
    try:
        raw = call(propose, budget, _BOUNDED_DISCOVERY_SLOTS)
    except TimeoutError:
        return dict(report, outcome="timeout")
    except Exception:
        return dict(report, outcome="provider_error")
    values = raw.get("urls") if isinstance(raw, dict) else raw if proposer is not None else None
    if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
        return dict(report, outcome="invalid_response")
    urls = []
    for url in _sanitize_urls(values):
        try:
            parsed = urlsplit(url)
            if not parsed.hostname or parsed.username or parsed.password:
                continue
        except ValueError:
            continue
        urls.append(url)
        if len(urls) >= DISCOVERY_MAX_SOURCES:
            break
    return dict(report, urls=urls, outcome="candidates" if urls else "no_candidates")


def reset_cache() -> None:
    _propose_cached.cache_clear()


import json as _json
from functools import lru_cache as _lru
from pathlib import Path as _Path


@_lru(maxsize=1)
def _load_seeds() -> dict:
    """Curated per-destination OFFICIAL source URL hints (deep requirement
    pages). Read-only reference data; the pipeline independently re-fetches and
    grounds each URL, so these only steer WHERE to look — never WHAT to claim."""
    from .registry import SNAPSHOT_DIR
    # data/reference/official_source_seeds.json (repo-relative, next to snapshots)
    candidates = [
        _Path(SNAPSHOT_DIR).parent / "reference" / "official_source_seeds.json",
        _Path(__file__).resolve().parents[3] / "data" / "reference" / "official_source_seeds.json",
    ]
    for p in candidates:
        try:
            if p.exists():
                data = _json.loads(p.read_text(encoding="utf-8"))
                return {k.upper(): v for k, v in data.items()
                        if isinstance(v, list) and not k.startswith("_")}
        except Exception:  # noqa: BLE001 - seeds are optional, never crash research
            continue
    return {}


def official_source_seeds(destination: str) -> list[str]:
    """Curated official-source URL hints for a destination (government domains
    only). Empty when none are curated — discovery then relies on Kimi proposals
    + internal-link following alone."""
    from .authority import hostname, is_government_host
    seeds = _load_seeds().get((destination or "").upper(), [])
    out = []
    for u in seeds:
        if isinstance(u, str) and _URL_RE.match(u.strip()) and is_government_host(hostname(u)):
            out.append(u.strip())
    return out


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
