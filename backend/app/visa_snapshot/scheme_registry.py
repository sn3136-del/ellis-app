"""The scheme registry: which passports a list-based programme admits.

An ETA, an e-visa and a visa waiver are LIST-BASED: the destination publishes
the passports the programme is open to, and a passport that is absent needs
something else. Trip.com's first and third findings (Indonesia is not K-ETA
eligible, Thailand is not ETA 601 eligible) are both a product served to a
nationality the list excludes. Until guard-20260912 the only list Ellis held
was the Australian ETA (permission_eligibility.PROGRAMS) plus one hard-coded
Indonesia to Korea branch; the other list-gated products were served with no
list at all.

This module loads data/database_seed/scheme_lists.json with the discipline
special_policies uses: a module-level path, an ELLIS_SCHEME_LISTS override,
an mtime-keyed cache under a lock, reload(), and entries that fail a gate
dropped at load with a recorded store error. The gates, each a silent drop
plus a store error:

  * source_url must be competent for the destination (source_authority);
    Union law and a provider page are NOT admissible for a nationality list
  * quote_sha256 must match list_quote
  * every nationality in eligible_nationalities must appear in list_quote as
    its own list item under the closed-list validator (a country name on a
    line of its own, resolved through the country registry and the page
    alias table the capture script used)
  * checked_at must parse and not be in the future
  * patterns must compile
  * list_state established requires a non-empty list of real ISO3 codes

A malformed file leaves the previous entries in place and sets
store_unavailable. A not_established or not_publicly_available entry is a
placeholder: it names the programme and, where the repo already proved it,
its list URL, and it refuses nothing (permission_eligibility reads
list_state before it appends an issue).

No module enforces the registry until T6.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import date, datetime, timezone

_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data",
                     "database_seed", "scheme_lists.json")
_LOCK = threading.Lock()
_STATE: dict = {"mtime": None, "entries": [], "errors": [], "unavailable": False}

SCHEME_KINDS = ("eta", "evisa", "visa_waiver", "visa_free", "voa")
LIST_STATES = ("established", "not_established", "not_publicly_available", "not_applicable")
# The page spellings the capture script resolved that the country registry
# does not carry verbatim. Every alias maps to exactly one ISO3.
PAGE_ALIASES = {
    "hong kong (sar of china)": "HKG", "china p. r.(hong kong)": "HKG", "hong kong sar": "HKG",
    "china p. r.(macao)": "MAC", "macao": "MAC", "macau": "MAC",
    "united kingdom—british citizen": "GBR", "united kingdom-british citizen": "GBR",
    "uk-british citizen(gbr)": "GBR", "united kingdom": "GBR", "british citizen": "GBR",
    "united states of america": "USA", "united states": "USA",
    "south korea": "KOR", "korea, republic of": "KOR", "republic of korea": "KOR",
    "taiwan": "TWN", "taiwan (excluding official or diplomatic passports)": "TWN",
    "the netherlands": "NLD", "netherlands": "NLD",
    "republic of san marino": "SMR", "san marino": "SMR",
    "vatican city": "VAT", "holy see": "VAT",
    "brunei": "BRN", "brunei darussalam": "BRN",
    "czechia": "CZE", "czech republic": "CZE", "slovak": "SVK", "slovakia": "SVK",
    "turkiye": "TUR", "türkiye": "TUR", "turkey": "TUR",
    "u.a.e": "ARE", "united arab emirates": "ARE",
    "russia": "RUS", "russian federation": "RUS",
    "dominican rep.": "DOM", "dominican republic": "DOM",
    "commonwealth of dominica": "DMA", "dominica": "DMA",
    "solomon is.": "SLB", "solomon islands": "SLB",
    "st. kitts-nevis": "KNA", "saint kitts and nevis": "KNA",
    "st. lucia": "LCA", "saint lucia": "LCA",
    "st. vincent": "VCT", "saint vincent and the grenadines": "VCT",
    "trinidad-tobago": "TTO", "trinidad and tobago": "TTO",
    "antigua-barbuda": "ATG", "antigua and barbuda": "ATG",
    "bosnia-hercegovina": "BIH", "bosnia and herzegovina": "BIH",
    "republic of serbia": "SRB", "serbia": "SRB",
    "surinam": "SUR", "suriname": "SUR",
    "micronesia": "FSM", "micronesia, federated states of": "FSM",
    "el salvador": "SLV", "costa rica": "CRI", "saudi arabia": "SAU",
    "south africa": "ZAF", "new zealand": "NZL", "marshall islands": "MHL",
    "eswatini": "SWZ", "guatemala": "GTM", "kazakhstan": "KAZ", "kiribati": "KIR",
    "nauru": "NRU", "palau": "PLW", "samoa": "WSM", "tonga": "TON", "tuvalu": "TUV",
    "venezuela": "VEN", "bolivia": "BOL", "iran": "IRN", "laos": "LAO", "vietnam": "VNM",
    "moldova": "MDA", "north macedonia": "MKD", "tanzania": "TZA", "syria": "SYR",
}


def _seed_path() -> str:
    return os.environ.get("ELLIS_SCHEME_LISTS", os.path.abspath(_PATH))


def _country_names() -> dict:
    """lower-cased name -> ISO3 from the registry plus the page aliases."""
    from .registry import load_registry
    out: dict = {}
    for e in load_registry("countries")["entries"]:
        code = e.get("alpha_3")
        if not code:
            continue
        for key in ("name", "common_name", "official_name"):
            if e.get(key):
                out.setdefault(str(e[key]).strip().lower(), code)
    for alias, code in PAGE_ALIASES.items():
        out[alias] = code
    return out


_NAMES_CACHE: dict = {}


def country_code(item: str) -> str | None:
    """The ISO3 a list item names, or None when the item is not one country."""
    if not _NAMES_CACHE:
        _NAMES_CACHE.update(_country_names())
    text = re.sub(r"^\s*(?:[-*•|]|\d+[.)])\s*", "", str(item or "")).strip().lower()
    text = re.sub(r"\s+", " ", text)
    if not text:
        return None
    if text in _NAMES_CACHE:
        return _NAMES_CACHE[text]
    bare = re.sub(r"\s*\(.*\)\s*$", "", text).strip()
    if bare in _NAMES_CACHE:
        return _NAMES_CACHE[bare]
    # "Vatican City - passport must indicate ..." and "United Kingdom – British
    # Citizen": the country is the part before the dash clause.
    head = re.split(r"\s+[-–—]\s+", text, maxsplit=1)[0].strip()
    if head != text and head in _NAMES_CACHE:
        return _NAMES_CACHE[head]
    return None


def list_items(quote: str) -> list[str]:
    """The list items of a captured quote: one country per line, ignoring
    the stay-period lines the K-ETA page interleaves and bare separators."""
    items = []
    for line in str(quote or "").splitlines():
        line = line.strip().strip("|").strip()
        if not line or re.match(r"^(?:allowed period of stay|stay)\b", line, re.I):
            continue
        items.append(line)
    return items


def quote_hash(quote: str) -> str:
    return hashlib.sha256(str(quote or "").encode("utf-8")).hexdigest()


def _entry_errors(e: dict, today: date) -> list[str]:
    from .source_authority import is_competent, authority_for, KIND_DESTINATION
    errors = []
    dest = str(e.get("destination") or "").upper()
    if not e.get("id") or not dest or not e.get("name"):
        errors.append("id, destination and name are required")
    if e.get("scheme_kind") not in SCHEME_KINDS:
        errors.append("scheme_kind must be one of " + ", ".join(SCHEME_KINDS))
    state = e.get("list_state")
    if state not in LIST_STATES:
        errors.append("list_state must be one of " + ", ".join(LIST_STATES))
    for key in ("patterns",):
        for pattern in (e.get(key) or []):
            try:
                re.compile(str(pattern))
            except re.error:
                errors.append(f"pattern does not compile: {pattern!r}")
    checked = str(e.get("checked_at") or "")
    if checked:
        try:
            if date.fromisoformat(checked[:10]) > today:
                errors.append("checked_at is in the future")
        except ValueError:
            errors.append("checked_at must be an ISO date")
    elif state == "established":
        errors.append("an established entry needs checked_at")
    url = str(e.get("source_url") or "")
    if url and authority_for(url, {"destination_country": dest}).kind != KIND_DESTINATION:
        errors.append("source_url must be a page the destination government owns")
    if state != "established":
        return errors
    if not url or not is_competent(url, {"destination_country": dest}):
        errors.append("an established list needs a competent destination-owned source_url")
    quote = str(e.get("list_quote") or "")
    if not quote.strip():
        errors.append("an established list needs list_quote")
    if quote_hash(quote) != str(e.get("quote_sha256") or ""):
        errors.append("quote_sha256 does not match list_quote")
    nats = e.get("eligible_nationalities")
    if not isinstance(nats, list) or not nats:
        errors.append("an established list needs a non-empty eligible_nationalities list")
        return errors
    codes = {country_code(item) for item in list_items(quote)}
    codes.discard(None)
    for nat in nats:
        nat = str(nat).upper()
        if not re.fullmatch(r"[A-Z]{3}", nat):
            errors.append(f"not an ISO3 code: {nat}")
        elif nat not in codes:
            errors.append(f"{nat} is not its own list item in list_quote")
    return errors


def _load() -> list[dict]:
    path = _seed_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        with _LOCK:
            _STATE.update(unavailable=True)
        return _STATE["entries"]
    with _LOCK:
        if _STATE["mtime"] == mtime:
            return _STATE["entries"]
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, list):
                raise ValueError("scheme_lists.json must be a list")
        except (OSError, ValueError) as exc:
            _STATE.update(unavailable=True, errors=[{"id": None, "errors": [f"unreadable: {exc}"]}])
            return _STATE["entries"]
        today = datetime.now(timezone.utc).date()
        entries, errors = [], []
        for e in raw:
            if not isinstance(e, dict):
                continue
            problems = _entry_errors(e, today)
            if problems:
                errors.append({"id": e.get("id"), "errors": problems})
                continue
            entries.append(dict(
                e, destination=str(e["destination"]).upper(),
                eligible_nationalities=sorted({str(n).upper() for n in (e.get("eligible_nationalities") or [])}),
                excluded_documents=sorted({str(d) for d in (e.get("excluded_documents") or [])}),
                patterns=[str(p) for p in (e.get("patterns") or [])],
                requirement_details=[str(d) for d in (e.get("requirement_details") or [])]))
        _STATE.update(mtime=mtime, entries=entries, errors=errors, unavailable=False)
        return entries


def reload() -> None:
    with _LOCK:
        _STATE["mtime"] = None
        _STATE["unavailable"] = False


def entries() -> list[dict]:
    return list(_load())


def for_destination(destination: str) -> list[dict]:
    dest = str(destination or "").upper()
    return [e for e in _load() if e["destination"] == dest]


def store_status() -> dict:
    _load()
    return {"entries": len(_STATE["entries"]), "errors": list(_STATE["errors"]),
            "store_unavailable": bool(_STATE["unavailable"]),
            "established": sorted(e["id"] for e in _STATE["entries"] if e["list_state"] == "established"),
            "not_established": sorted(e["id"] for e in _STATE["entries"] if e["list_state"] != "established")}


def covers(entry: dict, nationality: str) -> bool:
    """True only for an ESTABLISHED list that names the nationality."""
    return (entry.get("list_state") == "established"
            and str(nationality or "").upper() in set(entry.get("eligible_nationalities") or []))


def select(item: dict, destination: str, *, headline: bool = False) -> dict | None:
    """The programme a product (or a headline answer) belongs to, resolved in
    this order: an explicit program_id, the portal marker, a name pattern,
    and finally the requirement detail when the destination has exactly one
    programme of that family. Never by product NAME alone across
    programmes: a generic label picks a programme only through its pattern."""
    if not isinstance(item, dict):
        return None
    candidates = for_destination(destination)
    if not candidates:
        return None
    explicit = str(item.get("program_id") or "").strip()
    if explicit:
        return next((e for e in candidates if e["id"] == explicit), None)
    # The application portal names the programme; a source_url citing the
    # scheme's page does not (a citation explaining an exclusion is not a
    # recommendation to apply).
    portal = str(item.get("official_portal_url") or "").lower()
    for e in candidates:
        marker = str(e.get("portal_marker") or "").lower()
        if marker and marker in portal:
            return e
    text = " ".join(str(item.get(k) or "") for k in ("type", "visa_category", "visa_label", "name"))
    for e in candidates:
        for pattern in e.get("patterns") or []:
            if re.search(pattern, text, re.I):
                return e
    detail = str(item.get("requirement_detail") or "")
    if headline and not detail and item.get("disposition") == "ELECTRONIC_AUTHORIZATION_REQUIRED":
        detail = "eta_electronic_authorization"
    if detail:
        family = [e for e in candidates if detail in (e.get("requirement_details") or [])]
        if len(family) == 1:
            return family[0]
    return None
