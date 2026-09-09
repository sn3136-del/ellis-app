"""Deterministic effective-date policies applied to a canonical answer at read.

These reviewed schedules never create a cache row or ask a model to decide for
an arrival date. The evidence, route/document/purpose and validity interval are
explicit. Conflicting intervals fail closed. A human's conflicting verified
field is retained and exposed for review, never silently overruled by this AI
reading of an announced policy.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import json
import logging
from pathlib import Path
import re

from .authority import hostname, is_government_host
from .evidence_validator import quote_in_text, jurisdiction_matches, _NATIONALITY_NAMES

log = logging.getLogger(__name__)
POLICIES = Path(__file__).resolve().parents[3] / "data" / "database_seed" / "scheduled_policies.json"
_CACHE = {"version": None, "rows": []}
_FIELDS = frozenset({"disposition", "requirement_detail", "permitted_stay", "permitted_stay_days",
                     "government_fee", "visa_products", "visa_category", "application_channel",
                     "application_channel_detail", "route_workflow_type", "processing_time",
                     "source_url", "exceptions"})


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _today():
    return date.today()


def _key(route):
    return tuple(route.get(k) for k in ("nationality", "destination", "travel_purpose", "travel_document_type"))


def _official(url):
    return isinstance(url, str) and url.startswith("https://") and is_government_host(hostname(url))


def _parse_rows(raw):
    """Strict whole-file validation, including independently checkable quoted
    nationality, ordinary-passport scope, exemption duration and effective date.
    This first implementation intentionally accepts only this verified policy
    shape, rather than treating arbitrary future immigration prose as code."""
    if not isinstance(raw, list):
        raise ValueError("scheduled policies must be an array")
    rows, ids = [], set()
    for row in raw:
        if not isinstance(row, dict):
            raise ValueError("scheduled policy must be an object")
        route, fields, evidence = row.get("route"), row.get("fields"), row.get("evidence")
        if not isinstance(route, dict) or not isinstance(fields, dict) or not isinstance(evidence, dict):
            raise ValueError("scheduled policy requires route, fields and evidence")
        key = _key(route)
        if (not all(isinstance(x, str) for x in key) or
                not re.fullmatch(r"[A-Z]{3}", key[0]) or not re.fullmatch(r"[A-Z]{3}", key[1]) or
                key[2:] != ("tourism", "ordinary_passport")):
            raise ValueError("scheduled policy must name an exact supported route, purpose and document")
        start, end, verified = _date(row.get("effective_from")), _date(row.get("effective_to")), _date(row.get("verified_at"))
        if not start or not verified or (row.get("effective_to") is not None and not end) or (end and end < start):
            raise ValueError("invalid scheduled policy dates")
        if row.get("verifier") != "ai" or not _official(row.get("source_url")) or not _official(evidence.get("source_url")):
            raise ValueError("scheduled policy needs official sources and explicit AI provenance")
        if not all(jurisdiction_matches(url, key[1]) for url in (row["source_url"], evidence["source_url"])):
            raise ValueError("scheduled policy source belongs to another destination")
        ident = row.get("id")
        if not isinstance(ident, str) or not ident or ident in ids:
            raise ValueError("scheduled policy id must be unique")
        ids.add(ident)
        text, quotes = evidence.get("text"), evidence.get("quotes")
        required_quotes = {"effective_from", "nationality", "document", "exemption", "stay"}
        if end:
            required_quotes.add("effective_to")
        if not isinstance(text, str) or not isinstance(quotes, dict) or not required_quotes <= quotes.keys():
            raise ValueError("scheduled policy needs the deciding verbatim quotes")
        if not all(isinstance(q, str) and quote_in_text(q, text) for q in quotes.values()):
            raise ValueError("scheduled policy quote is absent from evidence text")
        start_forms = (start.isoformat(), f"{start.day} {start.strftime('%B')} {start.year}")
        if not any(quote_in_text(form, quotes["effective_from"]) for form in start_forms):
            raise ValueError("effective date is not supported by its quote")
        if end and not any(quote_in_text(form, quotes["effective_to"]) for form in
                           (end.isoformat(), f"{end.day} {end.strftime('%B')} {end.year}")):
            raise ValueError("expiry date is not supported by its quote")
        names = _NATIONALITY_NAMES.get(key[0], ()) + {"KOR": ("Korea (ROK)",), "GBR": ("UK",), "USA": ("USA",)}.get(key[0], ())
        if not any(name.strip().casefold() == quotes["nationality"].strip().casefold() for name in names):
            raise ValueError("nationality quote does not match the policy route")
        if "ordinary passport holders" not in quotes["document"].casefold() or "exemption" not in quotes["exemption"].casefold():
            raise ValueError("document or exemption scope is not supported")
        days = fields.get("permitted_stay_days")
        if isinstance(days, bool) or not isinstance(days, int) or days <= 0 or not re.search(rf"\b{days}[- ]days?\b", quotes["stay"], re.I):
            raise ValueError("stay duration does not match its quote")
        if set(fields) - _FIELDS or fields.get("disposition") != "VISA_EXEMPT" or fields.get("requirement_detail") != "unconditional_visa_free":
            raise ValueError("unsupported scheduled policy fields or disposition")
        expected = {"permitted_stay": f"{days} days", "government_fee": {"amount": 0, "currency": None},
                    "visa_products": [], "application_channel": "not_required", "route_workflow_type": "visa_exempt_preparation",
                    "visa_category": "Visa exemption", "processing_time": "Not applicable (no visa)"}
        if any(fields.get(k) != v for k, v in expected.items()) or fields.get("source_url") != row["source_url"]:
            raise ValueError("scheduled exemption bundle is incomplete or contradictory")
        exceptions = fields.get("exceptions")
        if not isinstance(exceptions, list) or not all(isinstance(x, str) for x in exceptions):
            raise ValueError("scheduled exceptions must be explicit strings")
        # This is declarative replacement of a known obsolete duration. Do
        # not accept arbitrary regex patterns or delete independent requirements.
        superseded = row.get("superseded_stay_days", [])
        if not isinstance(superseded, list) or any(isinstance(x, bool) or not isinstance(x, int) or x <= 0 for x in superseded):
            raise ValueError("superseded stays must be positive whole days")
        for old in rows:
            old_end = _date(old.get("effective_to")) or date.max
            if _key(old["route"]) == key and start <= old_end and _date(old["effective_from"]) <= (end or date.max):
                raise ValueError("overlapping scheduled policies for the same route")
        rows.append(deepcopy(row))
    return rows


def _load():
    try:
        stat = POLICIES.stat()
        version = (str(POLICIES), stat.st_mtime_ns, stat.st_size)
        if _CACHE["version"] == version:
            return _CACHE["rows"]
        rows = _parse_rows(json.loads(POLICIES.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError) as exc:
        log.error("scheduled policy store rejected: %s", exc)
        return []
    _CACHE.update(version=version, rows=rows)
    return rows


def _metadata(row, on):
    return {"id": row["id"], "effective_from": row["effective_from"],
            "effective_to": row.get("effective_to"), "date_used": on.isoformat(),
            "source_url": row["source_url"], "evidence_url": row["evidence"]["source_url"],
            "permitted_stay_days": row["fields"]["permitted_stay_days"], "verifier": "ai"}


def _exceptions(existing, additions, superseded):
    kept = []
    # Preserve unrelated admission conditions even when their original entry
    # shared a sentence/line with the old visa-exemption duration.
    for item in existing if isinstance(existing, list) else []:
        if not isinstance(item, str):
            continue
        for part in re.split(r"(?<=[.!?;])\s+|,\s+", item):
            if any(re.search(rf"\b{days}\s*[- ]?\s*days?\b", part, re.I) for days in superseded):
                continue
            if part.strip():
                kept.append(part.strip())
    return list(dict.fromkeys(kept + list(additions)))


def apply(guidance, provenance, route):
    """Return copies with an applicable source-quoted date policy, never a
    second cached decision. Invalid/missing arrival dates use today's rule.
    Conflicting human or later independently verified fields preserve the
    existing answer and expose scheduled_policy_conflict for the hold gate."""
    if not isinstance(guidance, dict) or not guidance:
        return guidance, provenance
    selected = _date((route or {}).get("arrival_date")) or _today()
    rt = {"nationality": (route or {}).get("passport_nationality"),
          "destination": (route or {}).get("destination_country"),
          "travel_purpose": (route or {}).get("travel_purpose", "tourism"),
          "travel_document_type": (route or {}).get("travel_document_type", "ordinary_passport")}
    policies = [p for p in _load() if _key(p["route"]) == _key(rt)]
    applicable = [p for p in policies if _date(p["effective_from"]) <= selected and
                  (not p.get("effective_to") or selected <= _date(p["effective_to"]))]
    if not applicable:
        future = sorted((p for p in policies if _date(p["effective_from"]) > selected), key=lambda p: p["effective_from"])
        if not future:
            return guidance, provenance
        g = deepcopy(guidance)
        g["upcoming_policy"] = _metadata(future[0], selected)
        return g, provenance
    row = applicable[0]
    g, prov = deepcopy(guidance), deepcopy(provenance or {})
    prior = dict(prov.get("field_provenance") or {})
    candidate = deepcopy(row["fields"])
    candidate["exceptions"] = _exceptions(g.get("exceptions"), candidate["exceptions"], row.get("superseded_stay_days", []))
    conflicts = []
    for field, value in candidate.items():
        source = prior.get(field) or (prov if field in (prov.get("fields") or []) else {})
        when = _date(source.get("verified_at"))
        protected = source.get("verifier") == "human" or (when and when > _date(row["verified_at"]))
        if protected and g.get(field) != value:
            conflicts.append(field)
    if conflicts:
        g["scheduled_policy_conflict"] = {**_metadata(row, selected), "fields": sorted(conflicts)}
        return g, provenance
    g.update(candidate)
    g.pop("upcoming_policy", None)
    g["scheduled_policy"] = _metadata(row, selected)
    source = {"source_url": row["source_url"], "verified_at": row["verified_at"],
              "verified_by": "Ellis scheduled policy evidence review", "verifier": "ai",
              "note": "Official schedule, effective " + row["effective_from"],
              "effective_from": row["effective_from"], "effective_to": row.get("effective_to"),
              "evidence_url": row["evidence"]["source_url"], "quotes": deepcopy(row["evidence"]["quotes"])}
    for field in candidate:
        prior[field] = dict(source)
    prov.update(source)
    prov["fields"] = sorted(set(prov.get("fields") or []) | candidate.keys())
    prov["field_provenance"] = prior
    prov["scheduled_policy"] = _metadata(row, selected)
    return g, prov
