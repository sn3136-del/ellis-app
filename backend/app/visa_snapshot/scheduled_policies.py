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
        names = _NATIONALITY_NAMES.get(key[0], ()) + {"KOR": ("Korea (ROK)",), "GBR": ("UK",), "USA": ("USA",), "MAC": ("Macao", "Macau")}.get(key[0], ())
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


class PolicyStoreUnavailable(ValueError):
    """An unreadable policy store is not an empty, successfully read schedule."""


def _load():
    try:
        stat = POLICIES.stat()
        version = (str(POLICIES), stat.st_mtime_ns, stat.st_size)
        if _CACHE["version"] == version:
            return _CACHE["rows"]
        rows = _parse_rows(json.loads(POLICIES.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError) as exc:
        log.error("scheduled policy store rejected: %s", exc)
        raise PolicyStoreUnavailable("scheduled policy store could not be validated") from exc
    _CACHE.update(version=version, rows=rows)
    return rows


def _metadata(row, on):
    return {"id": row["id"], "effective_from": row["effective_from"],
            "effective_to": row.get("effective_to"), "date_used": on.isoformat(),
            "source_url": row["source_url"], "evidence_url": row["evidence"]["source_url"],
            "permitted_stay_days": row["fields"]["permitted_stay_days"], "verifier": "ai"}


_DEPENDENT_STAY_FIELDS = (
    "entry_requirements", "required_documents", "onward_travel_evidence",
    "accommodation_evidence", "financial_evidence", "exceptions",
)
_ADMISSION_DETAIL = re.compile(
    r"\b(?:return|onward|tickets?|transport|depart(?:ure|ing)?|leave|leaving|exit|"
    r"extensions?|extend(?:ed|ing)?|funds?|money|insurance|accommodation|hotel|"
    r"passport(?!\s+holders?\b)|valid|validity|proof|photos?|applications?|fees?|health|vaccin\w*|registration|cards?)\b", re.I)


def _day_mentions(text, days):
    word = {15: "fifteen", 30: "thirty", 45: "forty[- ]five", 60: "sixty", 90: "ninety"}.get(days)
    amount = rf"(?:{word}(?:\s*\({days}\))?|{days})" if word else str(days)
    return list(re.finditer(rf"\b{amount}\s*[-–—]?\s*(?:calendar\s+)?days?\b", text, re.I))


def _stay_dependency(text, match, field):
    """Bind the old day count to a stay or departure deadline, not any number.

    This only identifies an unresolved dependency. It never concludes that
    the schedule's source establishes ticket, funds or extension requirements.
    """
    before, after = text[:match.start()], text[match.end():]
    historical = list(re.finditer(r"\b(?:formerly|previously|superseded)\b", before, re.I))
    if historical:
        history_scope = before[historical[-1].end():]
        if not re.search(r"\b(?:and|but|however|now|currently|still)\b", history_scope, re.I):
            return False
    if re.match(r"\s+(?:(?:visa[- ](?:free|exempt)|permitted)\s+)?(?:period|stay|limit)?\s*"
                r"(?:(?:is|was|has been)\s+)?superseded\b", after, re.I):
        return False
    if re.search(r"\bno longer\s+(?:applies|applicable|available|valid|in force)\b", after, re.I):
        return False
    negated = re.search(r"\b(?:not|never)\b(?:[\s-]+\w+){0,4}\s*$", before, re.I)
    upper_bound = re.search(r"\b(?:not exceeding|no more than|not longer than|no longer than)\s*$", before, re.I)
    if negated and not upper_bound:
        return False
    if re.match(
        r"\s*(?:visa[- ](?:free|exempt(?:ion)?)\s+(?:period|stay|limit|entry|admission)|"
        r"(?:permitted|allowed|authori[sz]ed|exempt)\s+(?:period|stay)|"
        r"(?:(?:tourist|tourism|initial|maximum)\s+)?stays?|visa[- ]exemption)\b", after, re.I):
        return True
    if re.search(
        r"\b(?:stay(?:s)?|admission(?:\s+period)?|permitted\s+period)\s*"
        r"(?:(?:of|for|is|are|up to|not exceeding|no more than|not longer than|no longer than|maximum(?: of)?)\s*)*$", before, re.I):
        return True
    if re.search(r"\b(?:visa[- ](?:free|exempt(?:ion)?)|exemption)\s*"
                 r"(?:(?:stay|period|is|are|of|for|up to|allows?|permits?)\s*)*$", before, re.I):
        return True
    # A ticket validity period or a passport's remaining validity is not a
    # departure deadline. Require the deadline language and travel action.
    deadline = re.search(r"\b(?:within|before|by|no later than)\s+(?:the\s+)?$", before, re.I)
    action = re.search(r"\b(?:return|onward|tickets?|depart\w*|leav\w*|exit)\b", before, re.I)
    if deadline and (field == "onward_travel_evidence" or action):
        return True
    if (deadline and re.search(r"\bextend\w*|\bextension\b", before, re.I)
            and re.match(r"\s+(?:stay\s+)?limit\b", after, re.I)):
        return True
    return False


def _historical_clause(text, policy):
    start = _date(policy.get("effective_from"))
    if not start:
        return False
    from datetime import timedelta
    previous = start - timedelta(days=1)
    dates = [("before", start), ("until", previous), ("through", previous)]
    for word, when in dates:
        for form in (when.isoformat(), f"{when.day} {when.strftime('%B')} {when.year}"):
            if re.search(rf"\b{word}\s+{re.escape(form)}\b", text, re.I):
                return True
    return False


def _text_leaves(value, path):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _text_leaves(item, f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _text_leaves(item, f"{path}.{key}")


def _dependent_stay_conflicts(guidance, policy):
    issues = []
    current = policy["fields"]["permitted_stay_days"]
    old_days = [day for day in policy.get("superseded_stay_days", []) if day != current]
    for field in _DEPENDENT_STAY_FIELDS:
        for path, text in _text_leaves(guidance.get(field), field):
            for part in re.split(r"(?<=[.!?;])\s+|\n+|,\s+(?=(?:but|however|now|currently)\b)", text, flags=re.I):
                if _historical_clause(part, policy):
                    continue
                for day in old_days:
                    if any(_stay_dependency(part, match, field) for match in _day_mentions(part, day)):
                        issues.append({"field": field, "path": path,
                                       "superseded_stay_days": day, "text": part.strip()})
    return issues


def _exceptions(existing, additions, superseded):
    kept = []
    # Only a pure obsolete stay statement can be superseded by this source.
    # Independent requirements and conditions keep their original wording;
    # unresolved dependencies are held below rather than rewritten or erased.
    for item in existing if isinstance(existing, list) else []:
        if not isinstance(item, str):
            continue
        if item in additions:
            kept.append(item)
            continue
        for part in re.split(r"(?<=[.!?;])\s+|,\s+", item):
            old_stay = any(_stay_dependency(part, match, "exceptions")
                           for days in superseded for match in _day_mentions(part, days))
            if old_stay and not _ADMISSION_DETAIL.search(part):
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
    try:
        loaded = _load()
    except PolicyStoreUnavailable:
        # Without a valid store its route scope is unknown, including at cold
        # start. Do not silently publish a cached rule that may be superseded.
        # Preserve claims and evidence for QC; the shared reader withholds the
        # unresolved answer until the store is readable again.
        g = deepcopy(guidance)
        previous = g.get("scheduled_policy_conflict")
        if isinstance(previous, dict) and previous.get("reason") == "scheduled_policy_store_unavailable":
            previous = previous.get("prior_conflict")
        g["scheduled_policy_conflict"] = {
            "reason": "scheduled_policy_store_unavailable",
            "fields": ["scheduled_policy"], "date_used": selected.isoformat(),
        }
        if previous:
            g["scheduled_policy_conflict"]["prior_conflict"] = previous
        return g, provenance
    marker = guidance.get("scheduled_policy_conflict")
    if isinstance(marker, dict) and marker.get("reason") == "scheduled_policy_store_unavailable":
        guidance = deepcopy(guidance)
        if marker.get("prior_conflict"):
            guidance["scheduled_policy_conflict"] = deepcopy(marker["prior_conflict"])
        else:
            guidance.pop("scheduled_policy_conflict")
    policies = [p for p in loaded if _key(p["route"]) == _key(rt)]
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
    dependencies = _dependent_stay_conflicts(g, row)
    if dependencies:
        g["scheduled_policy_conflict"] = {
            **_metadata(row, selected),
            "fields": sorted({issue["field"] for issue in dependencies}),
            "reason": "Dependent entry guidance still refers to a superseded stay duration",
            "dependent_stay_claims": dependencies,
        }
    source = {"source_url": row["source_url"], "verified_at": row["verified_at"],
              "verified_by": "Ellis scheduled policy evidence review", "verifier": "ai",
              "note": "Official schedule, effective " + row["effective_from"],
              "effective_from": row["effective_from"], "effective_to": row.get("effective_to"),
              "evidence_url": row["evidence"]["source_url"], "quotes": deepcopy(row["evidence"]["quotes"])}
    for field in candidate:
        old_proof = prior.get(field) or (provenance if field in ((provenance or {}).get("fields") or []) else {})
        prior[field] = dict(source)
        if field == "exceptions":
            preserved = [part for part in candidate[field] if part not in row["fields"][field]]
            if preserved:
                # The scheduled source only establishes its new exemption
                # sentences, not any retained admission/extension conditions.
                if isinstance(old_proof, dict) and old_proof.get("scope") == "scheduled_exemption_additions_only":
                    old_proof = old_proof.get("prior_evidence") or {}
                prior[field].update(
                    scope="scheduled_exemption_additions_only",
                    note="Official schedule supports only the added exemption statements; "
                         "retained independent conditions keep their earlier evidence and are not reverified here.",
                    prior_evidence=deepcopy(old_proof or {}), preserved_claims=deepcopy(preserved))
    prov.update(source)
    prov["fields"] = sorted(set(prov.get("fields") or []) | candidate.keys())
    prov["field_provenance"] = prior
    prov["scheduled_policy"] = _metadata(row, selected)
    return g, prov
