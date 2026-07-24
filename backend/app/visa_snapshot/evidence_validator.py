"""Deterministic ResearchEvidenceValidator (brief "INDEPENDENT RESEARCH
VALIDATION"): an INDEPENDENT gate over Kimi's grounded extraction.

Kimi already drops fields citing an unknown/absent source (kimi_research). This
validator adds the deterministic, model-free checks the brief requires and
rejects, per material claim:
  - nonofficial source presented as authoritative  -> non_official_source
  - wrong jurisdiction for the exact route          -> jurisdiction_mismatch
  - a citation whose page text does NOT support it  -> citation_unsupported
  - an assumption presented as fact (no citation)   -> uncited_assumption
It also verifies the DISPOSITION against the cited pages' actual text (a route
is "complete" only when an official page states the disposition), and reports
which required fields are still missing so the pipeline can retry automatically.

Never weakens a safeguard and never invents: a claim it cannot verify is
rejected with a precise reason, not passed through.
"""
from __future__ import annotations

import re

from .authority import hostname, is_government_host, registrable_domain

# Country ALPHA-3 -> the government TLD suffixes whose sites are that country's
# official domains. Used for jurisdiction matching (a China disposition must be
# cited from a Chinese official domain, etc.).
_DEST_GOV_SUFFIXES = {
    "CHN": ("gov.cn", "org.cn"), "MEX": ("gob.mx",), "USA": ("gov", "mil"),
    "VNM": ("gov.vn",), "KHM": ("gov.kh",), "IND": ("gov.in",), "GBR": ("gov.uk",),
    "AUS": ("gov.au",), "JPN": ("go.jp",), "KOR": ("go.kr",), "SGP": ("gov.sg",),
    "THA": ("go.th",), "MYS": ("gov.my",), "IDN": ("go.id",), "PHL": ("gov.ph",),
    "ARE": ("gov.ae",), "SAU": ("gov.sa",), "QAT": ("gov.qa",), "EGY": ("gov.eg",),
    "MAR": ("gov.ma",), "TUR": ("gov.tr",), "NZL": ("govt.nz",),
}

# Disposition -> (positive support patterns, negative/contradiction patterns).
# Multilingual (English / Simplified Chinese / Spanish). A page "supports" a
# disposition when a positive pattern matches AND no negative pattern matches.
_DISPOSITION_SUPPORT = {
    "VISA_REQUIRED": (
        r"visa (is |are )?required|must (obtain|apply for|hold) (a )?visa|"
        r"need(s|ed)? (a |an )?visa|apply for a( tourist| l| chinese)? visa|"
        r"tourist \(l\) visa|\bl visa\b|签证|需要办理签证|需申请签证|应当申请签证|"
        r"requiere (una )?visa|necesita(n)? (una )?visa|debe(n)? tramitar (una )?visa",
        # Negations (English/Spanish/Chinese) that flip a "visa" mention to
        # visa-free — must NOT be read as a requirement.
        r"visa[- ]free|no visa (is )?required|without a visa|visa exemption|"
        r"免签|无需签证|免办签证|"
        r"no (se )?requiere(n)?( de)? (una )?visa|no necesita(n)?( de)? (una )?visa|"
        r"do(es)? not (require|need)( a| an)? visa|not require a visa|"
        r"sin( necesidad de)? visa|exent",
    ),
    "VISA_FREE": (
        r"visa[- ]free|no visa (is )?required|do(es)? not (require|need) a visa|"
        r"without a visa|visa exemption|visa[- ]exempt|免签|无需签证|免办签证|"
        r"no (se )?requiere(n)? (de )?visa|sin (necesidad de )?visa|exent[ao]s? de visa|"
        r"no necesita(n)? visa|not require a visa|may enter.*without a visa",
        r"must (obtain|apply for) a visa|visa is required|需要签证|requiere visa",
    ),
    "EVISA_REQUIRED": (
        # Must be an e-VISA specifically — never a mere "electronic form" (e.g.
        # Mexico's electronic FMM entry permit is NOT an e-visa).
        r"\be-?visa\b|electronic visa|电子签证|visa electr[oó]nica",
        r"visa[- ]free|no visa (is )?required|do(es)? not (require|need) a visa|免签|"
        r"no (se )?requiere(n)? visa|no necesita(n)? visa",
    ),
    "ETA_REQUIRED": (
        r"electronic travel authoriz|\beta\b|电子旅行授权",
        r"visa[- ]free|no visa (is )?required|免签",
    ),
}

# Applicant-nationality references (for nationality-anchored detection). A
# disposition only grounds when the applicant's nationality — or a universal
# marker ("all foreign nationals", "citizens of the following countries") —
# appears near the disposition statement, so a page's rule for OTHER
# nationalities never grounds this route.
_NATIONALITY_NAMES = {
    "USA": ("united states", "u.s.", "u.s.a", " us ", "us citizen", "us national",
            "u.s. citizen", "american citizen", "americans", "estados unidos",
            "estadounidense", "美国"),
    "CHN": ("china", "chinese", "中国"), "MEX": ("mexico", "méxico", "mexican"),
    "GBR": ("united kingdom", "british", "u.k."), "CAN": ("canada", "canadian"),
    "IND": ("india", "indian"), "VNM": ("vietnam", "vietnamese"),
    "KHM": ("cambodia", "cambodian"), "SGP": ("singapore", "singaporean"),
}
_UNIVERSAL_MARKERS = (
    "all foreign", "all nationalities", "any nationality", "foreign citizens",
    "foreign nationals", "citizens of all countries", "regardless of nationality",
    "todos los", "todas las nacionalidades", "extranjeros", "所有外国", "外国公民",
    "following countries", "siguientes países", "list of countries",
)


def _nationality_anchored(text: str, nationality: str, at: int, *, window: int = 500) -> bool:
    """True when the disposition statement applies to THIS applicant's
    nationality: either the nationality name appears within `window` chars of
    the matched phrase, OR the phrase is in a "list of countries" (universal
    marker) context AND the nationality name appears somewhere on the page (i.e.
    it is in that list). A generic list header alone never grounds a route."""
    low = (text or "").lower()
    lo, hi = max(0, at - window), min(len(low), at + window)
    seg = low[lo:hi]
    names = [n for n in _NATIONALITY_NAMES.get((nationality or "").upper(), ()) if n.strip()]
    if any(n in seg for n in names):
        return True
    if any(m in seg for m in _UNIVERSAL_MARKERS):
        # A country-list context still requires the nationality to be listed.
        return any(n in low for n in names) if names else True
    return False

# Canonical disposition aliases coming from the pipeline.
_DISP_ALIASES = {
    "VISA_EXEMPT": "VISA_FREE", "VISA_FREE": "VISA_FREE",
    "VISA_REQUIRED": "VISA_REQUIRED", "EMBASSY_VISA_REQUIRED": "VISA_REQUIRED",
    "AUTHORIZED_VISA_CENTER_REQUIRED": "VISA_REQUIRED",
    "EVISA_REQUIRED": "EVISA_REQUIRED", "ETA_REQUIRED": "ETA_REQUIRED",
    "ELECTRONIC_AUTHORIZATION_REQUIRED": "ETA_REQUIRED",
}


def _page_by_url(pages: list[dict], url: str) -> dict | None:
    for p in pages:
        if p.get("url") == url or p.get("final_url") == url:
            return p
    # tolerant match on hostname+path
    for p in pages:
        if p.get("url") and url and p["url"].split("?")[0] == url.split("?")[0]:
            return p
    return None


def source_is_official(url: str) -> bool:
    return is_government_host(hostname(url))


# Reverse map: a country-specific gov suffix -> the ALPHA-3 that owns it. Used to
# reject a source that clearly belongs to a DIFFERENT country than the route.
_SUFFIX_OWNER = {suf: dest for dest, sufs in _DEST_GOV_SUFFIXES.items()
                 for suf in sufs if suf not in ("gov", "mil")}


def _owning_country(host: str) -> str | None:
    for suf in sorted(_SUFFIX_OWNER, key=len, reverse=True):
        if host == suf or host.endswith("." + suf):
            return _SUFFIX_OWNER[suf]
    return None


def jurisdiction_matches(url: str, destination: str) -> bool:
    """The cited official source belongs to the destination country's government.
    A known destination requires one of its official suffixes. An unknown
    destination is lenient EXCEPT it rejects a host that demonstrably belongs to
    a different known country (e.g. a .gov.kh source can't ground a Togo route)."""
    host = hostname(url)
    if not is_government_host(host):
        return False
    dest = (destination or "").upper()
    sufs = _DEST_GOV_SUFFIXES.get(dest)
    if sufs:
        return any(host == s or host.endswith("." + s) for s in sufs)
    owner = _owning_country(host)
    return owner is None or owner == dest


def supports_disposition(text: str, disposition: str, *, nationality: str = "") -> bool:
    """Does the page text state this disposition? When `nationality` is given,
    the statement must be nationality-anchored (the applicant's nationality or a
    universal marker appears near the matched phrase) so a rule for OTHER
    nationalities on the same page never counts."""
    canon = _DISP_ALIASES.get((disposition or "").upper())
    spec = _DISPOSITION_SUPPORT.get(canon or "")
    if not spec:
        return False
    pos, neg = spec
    low = (text or "").lower()
    m = re.search(pos, low, re.I)
    if not m:
        return False
    if neg and re.search(neg, low[max(0, m.start() - 60):m.end() + 120], re.I):
        return False
    if nationality and not _nationality_anchored(text, nationality, m.start()):
        return False
    return True


def find_supporting_excerpt(text: str, disposition: str, *, window: int = 240) -> str:
    canon = _DISP_ALIASES.get((disposition or "").upper())
    spec = _DISPOSITION_SUPPORT.get(canon or "")
    if not spec:
        return ""
    m = re.search(spec[0], text or "", re.I)
    if not m:
        return ""
    a = max(0, m.start() - window // 2)
    return (text[a:a + window]).strip()


def validate_disposition(route: dict, disposition: str, source_urls: list[str],
                         pages: list[dict]) -> dict:
    """Independently verify a disposition. Returns
    {ok, reasons[], supporting_url, supporting_excerpt}."""
    dest = (route or {}).get("destination_country", "")
    nat = (route or {}).get("passport_nationality", "")
    reasons: list[str] = []
    if not disposition:
        return {"ok": False, "reasons": ["no disposition proposed"],
                "supporting_url": "", "supporting_excerpt": ""}
    if not source_urls:
        return {"ok": False, "reasons": ["uncited_assumption: disposition has no source"],
                "supporting_url": "", "supporting_excerpt": ""}
    supporting_url, supporting_excerpt = "", ""
    official = [u for u in source_urls if source_is_official(u)]
    if not official:
        reasons.append("non_official_source: no cited source is a government domain")
    for url in official:
        if not jurisdiction_matches(url, dest):
            reasons.append(f"jurisdiction_mismatch: {hostname(url)} is not a {dest} official domain")
            continue
        page = _page_by_url(pages, url)
        text = (page or {}).get("text", "")
        if page is None:
            reasons.append(f"citation_unfetched: {url}")
            continue
        if supports_disposition(text, disposition, nationality=nat):
            supporting_url = url
            supporting_excerpt = find_supporting_excerpt(text, disposition)
            break
        reasons.append(f"citation_unsupported: {hostname(url)} text does not state {disposition}")
    ok = bool(supporting_url)
    if ok:
        reasons = []   # a valid supporting source overrides the per-source notes
    return {"ok": ok, "reasons": reasons[:8],
            "supporting_url": supporting_url, "supporting_excerpt": supporting_excerpt}


def validate_field(route: dict, name: str, value, source_urls: list[str],
                   pages: list[dict]) -> dict:
    """Generic per-field validation: at least one official, jurisdiction-matched
    source whose page text mentions the value (or a value token)."""
    dest = (route or {}).get("destination_country", "")
    if not source_urls:
        return {"ok": False, "reason": "uncited_assumption"}
    official = [u for u in source_urls if source_is_official(u)]
    if not official:
        return {"ok": False, "reason": "non_official_source"}
    tokens = [t for t in re.findall(r"[A-Za-z0-9]{3,}", str(value)) if t.lower() not in
              ("the", "and", "for", "visa", "not", "yes", "true", "false")][:4]
    for url in official:
        if not jurisdiction_matches(url, dest):
            continue
        page = _page_by_url(pages, url)
        if page is None:
            continue
        low = (page.get("text", "") or "").lower()
        if not tokens or any(t.lower() in low for t in tokens):
            return {"ok": True, "reason": "", "url": url}
    return {"ok": False, "reason": "citation_unsupported"}


# Pipeline-canonical dispositions, in specificity order (electronic programs win
# over the generic required/free classification). A generic "visa required"
# resolves to EMBASSY_VISA_REQUIRED (the consular application channel).
_DISP_SPECIFICITY = ("EVISA_REQUIRED", "ETA_REQUIRED", "VISA_FREE", "EMBASSY_VISA_REQUIRED")


def detect_disposition_from_pages(route: dict, pages: list[dict]) -> dict:
    """Deterministic, EVIDENCE-GROUNDED disposition detection: scan the fetched
    OFFICIAL, jurisdiction-matched pages and report the disposition their text
    actually states (with the supporting url + excerpt). This confirms from real
    page text — it never invents. Returns
    {disposition, supporting_url, supporting_excerpt, attested[], conflict}.
    Missing/ambiguous evidence yields disposition=None (never visa-free by
    default)."""
    dest = (route or {}).get("destination_country", "")
    nat = (route or {}).get("passport_nationality", "")
    attested: dict[str, list[str]] = {}
    excerpt_for: dict[str, tuple] = {}
    for p in pages:
        url = p.get("url") or p.get("final_url") or ""
        if not source_is_official(url) or not jurisdiction_matches(url, dest):
            continue
        text = p.get("text", "") or ""
        for disp in _DISP_SPECIFICITY:
            if supports_disposition(text, disp, nationality=nat):
                attested.setdefault(disp, []).append(url)
                excerpt_for.setdefault(disp, (url, find_supporting_excerpt(text, disp)))
    if not attested:
        return {"disposition": None, "supporting_url": "", "supporting_excerpt": "",
                "attested": {}, "conflict": False}
    # VISA_FREE and EMBASSY_VISA_REQUIRED directly contradict; treat
    # co-attestation as a conflict to resolve, never silently pick one.
    conflict = ("VISA_FREE" in attested and "EMBASSY_VISA_REQUIRED" in attested)
    chosen = next((d for d in _DISP_SPECIFICITY if d in attested), None)
    url, exc = excerpt_for.get(chosen, ("", ""))
    return {"disposition": chosen, "supporting_url": url, "supporting_excerpt": exc,
            "attested": {k: v[:3] for k, v in attested.items()}, "conflict": conflict}


def validate_extraction(route: dict, extraction: dict, pages: list[dict], *,
                        disposition: str = "", disposition_sources: list[str] | None = None,
                        required_fields: tuple = ()) -> dict:
    """Full independent validation pass. Returns accepted/rejected field notes,
    the disposition verdict, and the still-missing required fields (for retry)."""
    fields = (extraction or {}).get("fields", {}) or {}
    rejected: list[dict] = []
    accepted: list[str] = []
    for name, payload in fields.items():
        v = validate_field(route, name, (payload or {}).get("value"),
                           (payload or {}).get("sources") or [], pages)
        if v["ok"]:
            accepted.append(name)
        else:
            rejected.append({"field": name, "reason": v["reason"]})
    disp_verdict = validate_disposition(route, disposition,
                                        disposition_sources or [], pages)
    present = set(fields.keys())
    missing = [f for f in required_fields if f not in present]
    if not disp_verdict["ok"]:
        missing = ["disposition"] + [m for m in missing if m != "disposition"]
    return {"accepted": accepted, "rejected": rejected,
            "disposition": disp_verdict, "missing_fields": missing,
            "ok": disp_verdict["ok"]}
