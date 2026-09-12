"""Who may state a fact about a route: governing item 5 as one module.

The rule. A visa fact is backed by the destination government's own page.
Two documented exemptions and one demotion:

  Exemption 1, European Union common visa law. The Schengen visa lists are
  Union law (Regulation 2018/1806 and its two annexes, the Visa Code
  810/2009, ETIAS 2018/1240, the Entry/Exit System 2017/2226), published by
  the Union and not by any one member state. A citation of one of those
  instruments on an official Union host is competent for every served
  Schengen destination, and ONLY for the fields Union law governs: the
  verdict, its subcategory, the permitted stay and the visa fee. National
  fields (documents, channel, processing time, portal) stay national.

  Exemption 2, an authorised official visa-service provider. A provider
  (a VFS or TLS style centre) may state application logistics when the
  destination's own government page appoints it. The register ships EMPTY:
  a guessed provider is worse than none, and no appointment page is
  established in this repository today. An entry loads only when its
  appointing page is destination-owned, its scope is a subset of the
  logistics fields and never the verdict, and it is not stale.

  Demotion, the traveller's own government. A page owned by the passport's
  own state is a third state for the destination's rules: never competent,
  and corroborating only when the supporting statement names both the
  traveller group and the destination. That test is freshness._supports_route
  lifted verbatim, so the two hands cannot disagree.

The base computation, government_owner(hostname(url)) equal to the
destination, is the one evidence_validator.jurisdiction_matches already
made; that function is now a thin wrapper over is_competent so every
existing call site moves together.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass
from datetime import date, datetime, timezone

from .authority import hostname, is_government_host
from .authority_ownership import government_owner

KIND_DESTINATION = "destination"
KIND_EU_VISA_LAW = "eu_visa_law"
KIND_PROVIDER = "authorised_provider"
KIND_TRAVELLER = "traveller_government"
KIND_THIRD_PARTY = "third_party_government"
KIND_NON_GOVERNMENT = "non_government"

# Exemption 1. The hosts are the Union's own: the legal text (eur-lex and
# the Publications Office that serves the same CELEX documents), the
# Commission's home-affairs site and the ETIAS site.
EU_LAW_HOSTS = frozenset({"eur-lex.europa.eu", "publications.europa.eu",
                          "home-affairs.ec.europa.eu", "travel-europe.europa.eu"})
# The closed instrument list, matched in the citation (the URL, a quote or
# a note) in either the Official Journal form or the CELEX form.
# CELEX numbers come in the act form (32018R1806) and the consolidated
# form (02018R1806-20251230); both name the same instrument.
EU_INSTRUMENTS = {
    "2018/1806": r"2018/1806|\b[03]2018R1806\b|\b02018R1806-\d{8}\b",
    "810/2009": r"810/2009|\b[03]2009R0810\b|\b02009R0810-\d{8}\b",
    "2018/1240": r"2018/1240|\b[03]2018R1240\b|\b02018R1240-\d{8}\b",
    "2017/2226": r"2017/2226|\b[03]2017R2226\b|\b02017R2226-\d{8}\b",
    # Directive 2004/38/EC, the free movement directive: the Union law that
    # states a Union citizen's right to enter another Member State. The
    # pre-T3 carve-out accepted it on eur-lex for FRA and ESP, and the
    # reviewed ESP to FRA routes cite it; leaving it out narrowed that
    # carve-out (a defect fix after T6). Scoped to Member State destinations
    # only, never to a Schengen state outside the Union.
    "2004/38": r"2004/38|\b[03]2004L0038\b|\b02004L0038-\d{8}\b",
}
# What Union law decides. Everything else on a Schengen route is national.
EU_LAW_FIELDS = frozenset({"disposition", "requirement_detail", "permitted_stay",
                           "permitted_stay_days", "government_fee"})

# Exemption 2. The logistics a provider may be appointed to state.
PROVIDER_FIELDS = frozenset({"application_channel", "application_channel_detail",
                             "official_portal_url", "processing_time",
                             "appointment_required", "consulate_district"})
_PROVIDERS_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data",
                               "database_seed", "authorised_providers.json")
_PROVIDER_LOCK = threading.Lock()
_PROVIDER_STATE: dict = {"mtime": None, "entries": [], "errors": []}


def schengen_destinations() -> frozenset[str]:
    from ..consular_forms import _SCHENGEN
    return frozenset(_SCHENGEN)


def instrument_destinations(instrument: str) -> frozenset[str]:
    """Where a Union instrument decides entry: the free movement directive in
    the Member States, every other listed instrument in the Schengen area."""
    if instrument == "2004/38":
        from .structured_evidence import EU_MEMBERS
        return frozenset(EU_MEMBERS)
    return schengen_destinations()


@dataclass(frozen=True)
class Authority:
    kind: str
    owner: str = ""
    exemption: str | None = None
    instrument: str | None = None
    appointed_by: str | None = None
    scope: tuple = ()
    note: str = ""

    def competent_for(self, field: str) -> bool:
        if self.kind == KIND_DESTINATION:
            return True
        if self.kind in (KIND_EU_VISA_LAW, KIND_PROVIDER):
            return field in self.scope
        return False


def _instrument_named(citation: str) -> str | None:
    text = str(citation or "")
    for name, pattern in EU_INSTRUMENTS.items():
        if re.search(pattern, text, re.I):
            return name
    return None


def _route_parts(route) -> tuple[str, str]:
    if isinstance(route, str):
        return route.upper(), ""
    route = route or {}
    dest = str(route.get("destination_country") or route.get("destination") or "").upper()
    nat = str(route.get("passport_nationality") or route.get("nationality") or "").upper()
    return dest, nat


# ---------------------------------------------------------------------------
# Exemption 2: the provider register
# ---------------------------------------------------------------------------

def _providers_path() -> str:
    return os.environ.get("ELLIS_AUTHORISED_PROVIDERS", os.path.abspath(_PROVIDERS_PATH))


def _provider_entry_errors(e: dict, today: date) -> list[str]:
    errors = []
    dest = str(e.get("destination") or "").upper()
    host = str(e.get("host") or "").lower()
    if not (e.get("id") and dest and host):
        errors.append("id, destination and host are required")
    appointed = str(e.get("appointed_by_url") or "")
    if not appointed or government_owner(hostname(appointed)) != dest:
        errors.append("appointed_by_url must be a page the destination government owns")
    fields = e.get("authorised_fields")
    fields = [str(f) for f in fields] if isinstance(fields, list) else []
    if not fields or not set(fields) <= PROVIDER_FIELDS:
        errors.append("authorised_fields must be a non-empty subset of the logistics fields")
    if {"disposition", "requirement_detail"} & set(fields):
        errors.append("a provider is never competent for the verdict")
    quote = str(e.get("appointment_quote") or "")
    if len(quote.strip()) < 20:
        errors.append("appointment_quote must be at least 20 characters")
    if hashlib.sha256(quote.encode("utf-8")).hexdigest() != str(e.get("quote_sha256") or ""):
        errors.append("quote_sha256 does not match appointment_quote")
    try:
        checked = date.fromisoformat(str(e.get("checked_at") or "")[:10])
        if checked > today:
            errors.append("checked_at is in the future")
    except ValueError:
        errors.append("checked_at must be an ISO date")
    try:
        until = date.fromisoformat(str(e.get("valid_until") or "")[:10])
        if until < today:
            errors.append("entry is stale: valid_until has passed")
    except ValueError:
        errors.append("valid_until must be an ISO date")
    return errors


def _load_providers(today: date | None = None) -> list[dict]:
    path = _providers_path()
    today = today or datetime.now(timezone.utc).date()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return []
    with _PROVIDER_LOCK:
        if _PROVIDER_STATE["mtime"] == (mtime, today):
            return _PROVIDER_STATE["entries"]
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            _PROVIDER_STATE["errors"] = ["authorised_providers.json is unreadable"]
            return _PROVIDER_STATE["entries"]
        entries, errors = [], []
        for e in raw if isinstance(raw, list) else []:
            if not isinstance(e, dict):
                continue
            problems = _provider_entry_errors(e, today)
            if problems:
                errors.append({"id": e.get("id"), "errors": problems})
                continue
            entries.append(dict(e, destination=str(e["destination"]).upper(),
                                host=str(e["host"]).lower(),
                                authorised_fields=sorted(str(f) for f in e["authorised_fields"])))
        _PROVIDER_STATE.update(mtime=(mtime, today), entries=entries, errors=errors)
        return entries


def reload_providers() -> None:
    with _PROVIDER_LOCK:
        _PROVIDER_STATE["mtime"] = None


def provider_store_status() -> dict:
    _load_providers()
    return {"entries": len(_PROVIDER_STATE["entries"]), "errors": list(_PROVIDER_STATE["errors"])}


def _provider_for(host: str, dest: str) -> dict | None:
    for e in _load_providers():
        if e["destination"] == dest and (host == e["host"] or host.endswith("." + e["host"])):
            return e
    return None


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------

def authority_for(url: str, route, *, citation: str = "") -> Authority:
    """Classify a page for a route. ``citation`` is any text that names what
    the page is (a quote, a note); the URL itself always counts."""
    dest, nat = _route_parts(route)
    host = hostname(str(url or ""))
    if not host or not is_government_host(host):
        provider = _provider_for(host, dest) if host and dest else None
        if provider is not None:
            return Authority(KIND_PROVIDER, owner=dest, exemption="authorised_provider",
                             appointed_by=provider["appointed_by_url"],
                             scope=tuple(provider["authorised_fields"]), note=provider["id"])
        return Authority(KIND_NON_GOVERNMENT, note="not an official host")
    owner = government_owner(host) or ""
    if dest and owner == dest:
        return Authority(KIND_DESTINATION, owner=owner)
    if host in EU_LAW_HOSTS:
        instrument = _instrument_named(f"{url} {citation or ''}")
        if instrument and dest in instrument_destinations(instrument):
            return Authority(KIND_EU_VISA_LAW, owner="EU", exemption="eu_visa_law",
                             instrument=instrument, scope=tuple(sorted(EU_LAW_FIELDS)))
        if dest in schengen_destinations():
            return Authority(KIND_THIRD_PARTY, owner="EU",
                             note="Union host but no instrument from the closed list is named "
                                  "for this destination")
    provider = _provider_for(host, dest) if dest else None
    if provider is not None:
        return Authority(KIND_PROVIDER, owner=dest, exemption="authorised_provider",
                         appointed_by=provider["appointed_by_url"],
                         scope=tuple(provider["authorised_fields"]), note=provider["id"])
    if nat and owner == nat:
        return Authority(KIND_TRAVELLER, owner=owner,
                         note="the traveller's own government is a third state for this destination")
    return Authority(KIND_THIRD_PARTY, owner=owner)


def is_competent(url: str, route, field: str = "disposition", *, citation: str = "") -> bool:
    """May this page state ``field`` for this route?"""
    return authority_for(url, route, citation=citation).competent_for(field)


def classify(url: str, route, *, citation: str = "") -> str:
    return authority_for(url, route, citation=citation).kind


def statement_names_group_and_destination(statement: str, route) -> bool:
    """freshness._supports_route's home-government test, lifted verbatim:
    the supporting statement must name the traveller group (a nationality
    word beside passport, citizen or national) AND the destination, so an
    inbound rule for visitors to that government is never recycled as a
    rule for travelling elsewhere."""
    from .evidence_validator import _NATIONALITY_NAMES
    dest, nat = _route_parts(route)
    lower = str(statement or "").casefold()
    names = _NATIONALITY_NAMES.get(nat, ())
    if not any(re.search(re.escape(n.strip()) + r".{0,25}(?:passport|citizen|national)|(?:passport|citizen|national).{0,25}" + re.escape(n.strip()), lower)
               for n in names):
        return False
    destination_names = _NATIONALITY_NAMES.get(dest, ())
    return any(re.search(r"(?<![a-z])" + re.escape(n.strip()) + r"(?![a-z])", statement, re.I)
               for n in destination_names)


def is_corroborating(url: str, route, *, statement: str = "") -> bool:
    """May this page CORROBORATE a route fact (never establish it)? A
    competent page always may. The traveller's own government may only when
    the statement names both the traveller group and the destination."""
    authority = authority_for(url, route)
    if authority.kind in (KIND_DESTINATION, KIND_EU_VISA_LAW):
        return True
    if authority.kind == KIND_TRAVELLER:
        return statement_names_group_and_destination(statement, route)
    return False
