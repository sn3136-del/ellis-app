"""Source-checked facts that outrank the model's answer.

WHY THIS EXISTS. The Database's fast path answers from Kimi's own knowledge
(see kimi_primary's header: no official-source fetching runs there). That is
instant and usually right, but it is only as current as the model — and visa
policy moves. An audit against official government sources on 2026-08-22 found
ten routes where the model reported the wrong DISPOSITION because the policy
had changed: the Philippines and Russia going visa-free for Chinese nationals,
the China-Singapore mutual exemption, HKSAR passports being eTA-eligible for
Canada and not visa nationals for the UK, K-ETA waivers for Hong Kong and the
United States, Hong Kong's exclusion from India's e-Visa list and from Taiwan's
visa regime, and Indonesia being visa-on-arrival rather than visa-free for US
nationals.

Guessing harder does not fix that. A checked fact does. An override is a fact
checked against a named official page on a named date, and it wins
over the model for exactly the fields it names — nothing else is touched, and
an answer that carries one says so, with the source and the date, instead of
presenting a model recollection as established fact. Each field records
whether its verifier was human or AI. Historical entries default to AI.

RULES, all deliberate:
  * An override MUST carry a source_url on an official government domain and a
    verified_at date. Without both it is ignored — an unsourced correction is
    just a different guess.
  * It overrides ONLY the fields it lists. The rest of the answer is the
    model's, and is still labelled as such.
  * It never invents a route. If no answer exists for that route, there is
    nothing to override.
  * It is visible: the answer reports source_verified so a reader can see
    which facts were checked, by whom, and when.
"""
from __future__ import annotations

import json
import logging
import math
import os
import pathlib
import re
import threading
from copy import deepcopy
from contextvars import ContextVar
from functools import lru_cache

from .authority import hostname, is_government_host
from .policy_intervals import inherit_bounds

log = logging.getLogger(__name__)

OVERRIDES = pathlib.Path(__file__).resolve().parents[3] / "data" / \
    "database_seed" / "verified_overrides.json"

# Operator-written overrides live OUTSIDE the deployable tree, so a deploy
# rsync can never clobber what a Trip.com operator wrote through the console,
# and the daily /var/lib/ellis backup carries them. Entries here pass exactly
# the same gates as the seed file and are loaded AFTER it, so an operator
# correction wins over the shipped fact for the fields it names.
_OP_LOCK = threading.Lock()


def operator_overrides_path() -> pathlib.Path:
    env = os.environ.get("ELLIS_OPERATOR_OVERRIDES")
    if env:
        return pathlib.Path(env)
    var = pathlib.Path("/var/lib/ellis")
    if var.is_dir():
        return var / "operator_overrides.json"
    return OVERRIDES.parent / "operator_overrides.json"

# Fields an override is allowed to correct. Anything else in the file is
# ignored rather than trusted, so a malformed entry cannot reshape an answer.
OVERRIDABLE = frozenset({
    "disposition", "requirement_detail", "visa_category", "permitted_stay",
    "permitted_stay_days", "application_channel", "application_channel_detail",
    "government_fee", "official_portal_url", "visa_products", "processing_time",
    "exceptions", "required_documents", "entry_requirements", "confidence",
    # Route entry rules render separately on the traveller page. Correcting
    # only its synthesized entry_requirements text left these stale facts
    # visible beneath the corrected record.
    "passport_validity", "passport_validity_requirement",
    "insurance_required",
    "onward_travel_evidence", "accommodation_evidence", "financial_evidence",
    "biometrics_required", "appointment_required", "interview_required",
    # A mandatory pre-arrival filing (Malaysia's MDAC, the SG Arrival Card) is
    # the difference between boarding and not boarding, so a verified fact
    # must be able to correct it.
    "arrival_card", "health_requirements",
    # These existing normalized workflow fields also render on the traveller
    # page; a scoped correction must reach them as well as the channel label.
    "account_registration_steps", "payment_process", "submission_process",
    "photo_requirements",
    # Which mission handles this applicant. Verified jurisdiction rules were
    # being written and then silently dropped here, which is why that column
    # stayed empty on every record while the facts sat in the seed.
    "consular_jurisdiction",
    # Names the fields a destination was checked for and does not publish, so
    # a correct blank reads as "not publicly available" instead of a gap.
    "unpublished_fields",
    # The page the answer cites. A link that has gone dead, or that was never
    # about this nationality in the first place, could be corrected on the
    # record and not on the page the customer reads, because the customer page
    # shows the answer's own source_url and nothing could reach it. Any URL
    # written here passes the same government-host gate as the override's own
    # provenance, so this cannot be used to smuggle in an unofficial page.
    "source_url",
    # §4.2.1: where no single official source settles a route, several
    # high-credibility sources are cross-checked and "每条来源须逐条绑定URL",
    # each source bound to its own URL. A record carried exactly one URL, so
    # a route checked against three ministries could show only one of them and
    # the other two were unauditable. Each entry is gated individually.
    "corroborating_sources",
})

# Fields whose value is a link. Whatever an override puts in one of these has
# to satisfy the same rule as the override's provenance: an official page or
# nothing at all.
_URL_FIELDS = ("source_url", "official_portal_url")


# A verification writes prose, and some of that prose is the reviewer arguing
# with the claim in front of them rather than telling a traveller anything.
# Eleven overrides reached the display page carrying sentences like "CORRECTION
# TO THE SUBMITTED CLAIM" and "the claim's risk label is backwards, I am
# correcting it", and the customer page renders exceptions under "Good to know".
# A field a customer reads may not contain the workings.
_REVIEWER_VOICE = (
    "the claim", "correction to the submitted", "i am correcting",
    "the submitted row", "material detail", "should not be published",
    "do not publish", "the agent ", "as an ai", "my earlier",
    "purpose scope the claim", "is wrong and dangerous",
)
# The fields whose text a customer actually reads.
_CUSTOMER_TEXT = ("exceptions", "application_channel_detail", "requirement_detail",
                  "permitted_stay", "processing_time", "required_documents",
                  "entry_requirements", "consular_jurisdiction", "passport_validity",
                  "onward_travel_evidence", "accommodation_evidence", "financial_evidence",
                  "account_registration_steps", "payment_process", "submission_process",
                  "photo_requirements", "forms")

_BOOLEAN_FIELDS = ("biometrics_required", "appointment_required", "interview_required", "insurance_required")


def _clean_corroborating(value):
    """Keep only entries that name an official page and say what it said.

    An entry is {url, quote} at minimum. A URL that fails the government-host
    gate is dropped rather than the whole list, so one bad citation cannot
    take the good ones with it, and an entry with no quote is dropped because
    a bare link is not evidence of agreement."""
    if not isinstance(value, (list, tuple)):
        return None
    kept = []
    for item in value:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        quote = str(item.get("quote") or "").strip()
        if not url or not quote:
            continue
        if not is_government_host(hostname(url)):
            continue
        entry = {"url": url, "quote": quote[:600]}
        for k in ("checked_at", "authority", "agrees"):
            if item.get(k) not in (None, ""):
                entry[k] = item[k]
        kept.append(entry)
    return kept or None


def _reads_like_review(value) -> bool:
    """True when this text is the reviewer talking, not the answer."""
    if isinstance(value, (list, tuple)):
        return any(_reads_like_review(v) for v in value)
    low = str(value or "").lower()
    return any(marker in low for marker in _REVIEWER_VOICE)


def _key(nat: str, dest: str, purpose: str = "tourism",
         doc: str = "") -> str:
    """Ordinary-passport facts key on route alone; a fact verified for a
    SPECIFIC document (a diplomatic or service passport follows bilateral
    agreements, not tourist rules) carries the document in its key and only
    ever matches that document."""
    base = f"{str(nat).upper()}|{str(dest).upper()}|{str(purpose).lower()}"
    doc = str(doc or "").strip().lower()
    return f"{base}|{doc}" if doc and doc != "ordinary_passport" else base


_CACHE: dict = {"mtime": None, "table": {}}
# A lookup must retain the status of the table it actually selected, even
# when another request reloads repaired files before this response is built.
# ContextVar also preserves find(route)'s public return contract (entry/None).
_LOOKUP_STORE_ERRORS: ContextVar[tuple[str, ...]] = ContextVar(
    "verified_override_lookup_store_errors", default=())


class _VerificationTable(dict):
    def __init__(self, entries, errors=()):
        super().__init__(entries)
        self._store_errors = tuple(sorted(set(errors)))

    @property
    def store_errors(self) -> tuple[str, ...]:
        return self._store_errors


REVIEWED_OVERLAY_NAMES = (
    "reviewed_schengen_overlay_2026_09_09.json",
    "reviewed_australia_overlay_2026_09_09.json",
    "reviewed_japan_supported_overlay_2026_09_09.json",
)


REVIEWED_OVERLAY_LIST = "reviewed_overlays.json"


def _listed_reviewed_overlay_names():
    """Batches converted by the general reviewed-batch converter register
    through a committed list beside the seed, installed with the same pinned
    deployment as the overlay itself. The list names files only: each file
    still passes the reviewed-overlay schema and per-entry gates."""
    path = OVERRIDES.parent / REVIEWED_OVERLAY_LIST
    try:
        names = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(names, list):
        return []
    return [n for n in names if isinstance(n, str) and n.endswith(".json")
            and "/" not in n and n not in REVIEWED_OVERLAY_NAMES]


def _reviewed_overlay_paths():
    return [OVERRIDES.parent / name
            for name in (*REVIEWED_OVERLAY_NAMES, *_listed_reviewed_overlay_names())]


def _table() -> dict:
    """route key -> override entry, rebuilt whenever either file changes on
    disk so a newly verified fact reaches readers without a restart. Entries
    missing a source or a date, or citing a non-government domain, are
    dropped with no effect."""
    global _CACHE
    op = operator_overrides_path()
    try:
        mtime = tuple((path.stat().st_mtime_ns, path.stat().st_size)
                      if path.is_file() else None
                      for path in [OVERRIDES, OVERRIDES.parent / REVIEWED_OVERLAY_LIST,
                                   *_reviewed_overlay_paths(), op])
    except OSError:
        mtime = ("unreadable",)
    cached = _CACHE
    if cached["mtime"] == mtime and cached["table"] is not None:
        return cached["table"]
    table = _load_table()
    _CACHE = {"mtime": mtime, "table": table}
    return table


def _read_rows(path: pathlib.Path) -> list:
    if not path.is_file():
        return []
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a broken file must not break lookups
        return []
    return rows if isinstance(rows, list) else []


def _read_verification_store(path, kind, *, required=False, reviewed=False, errors=None):
    """Distinguish absent optional stores from corrupt/unreadable evidence."""
    errors = [] if errors is None else errors
    try:
        path.stat()
    except FileNotFoundError:
        if required:
            errors.append(kind)
        return []
    except OSError:
        errors.append(kind)
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if reviewed:
            from .evidence_validator import jurisdiction_matches
            from .reviewed_social_authority import entry_supported
            if (not isinstance(data, dict) or data.get('schema_version') != 1
                    or data.get('kind') != 'reviewed_overlay_conversion'
                    or not isinstance(data.get('entries'), list)):
                raise ValueError('invalid reviewed overlay schema')
            rows = data['entries']
            # Reviewed files are produced by a bounded converter. A malformed
            # entry must not quietly disappear and expose an older answer.
            for row in rows:
                from .reviewed_hkg_mainland_fields import entry_errors as delegated_entry_errors
                if delegated_entry_errors(row):
                    raise ValueError('invalid delegated permit application proof')
                if (not isinstance(row, dict) or not isinstance(row.get('route'), dict)
                        or not row['route'].get('nationality') or not row['route'].get('destination')
                        or not row.get('verified_at') or not row.get('fields')
                        or not ((is_government_host(hostname(str(row.get('source_url') or '')))
                            and jurisdiction_matches(str(row.get('source_url') or ''), row['route']['destination']))
                            or entry_supported(row))
                        or _field_errors(row['fields'])):
                    raise ValueError('invalid reviewed overlay entry')
        else:
            rows = data
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError('invalid verification store container')
        if any((row.get('fields') is not None and not isinstance(row.get('fields'), dict))
               or (row.get('route') is not None and not isinstance(row.get('route'), dict))
               for row in rows):
            raise ValueError('invalid verification record shape')
        return rows
    except (OSError, ValueError, TypeError, AttributeError):
        errors.append(kind)
        return []


def _annotate_store_status(guidance, errors):
    if not isinstance(guidance, dict):
        return guidance
    guidance = dict(guidance)
    prior = guidance.get('source_verification_store_unavailable')
    if isinstance(prior, dict) and prior.get('component') == 'verified_overrides':
        guidance.pop('source_verification_store_unavailable', None)
        prior = prior.get('prior_unavailable')
        if prior:
            guidance['source_verification_store_unavailable'] = deepcopy(prior)
    if errors:
        marker = {'component': 'verified_overrides', 'stores': list(errors)}
        if prior:
            marker['prior_unavailable'] = deepcopy(prior)
        guidance['source_verification_store_unavailable'] = marker
    return guidance


def _load_table() -> dict:
    # Seed first, then operator entries MERGED per field on top. An operator
    # who corrects one field must not wipe the seed's other verified facts:
    # a console edit of processing_time once shadowed a route's entire
    # verified entry and the flagship fee vanished from the served answer
    # (2026-09-01). Each field retains its own source and verifier identity.
    # The headline provenance is selected from the verified verdict at apply.
    errors = []
    table = _parse_rows(_read_verification_store(OVERRIDES, 'core_seed', required=True, errors=errors), {})
    for path in _reviewed_overlay_paths():
        table = _parse_rows(_read_verification_store(path, 'reviewed_overlay', reviewed=True, errors=errors), table)
    # Validate narrow operator edits against the already verified seed
    # verdict, never against the model's current guess.
    ops = _parse_rows(_read_verification_store(operator_overrides_path(), 'operator_overrides', errors=errors), {}, inherited=table)
    for k, op in ops.items():
        base = table.get(k)
        if base is None:
            table[k] = op
            continue
        merged_fields = dict(base["fields"])
        merged_fields.update(op["fields"])
        field_provenance = dict(base.get("field_provenance") or {})
        for field, incoming in (op.get("field_provenance") or {}).items():
            field_provenance[field] = inherit_bounds(field_provenance.get(field), incoming)
        table[k] = {"fields": merged_fields,
                    "source_url": op["source_url"],
                    "verified_at": op["verified_at"],
                    "verified_by": op["verified_by"],
                    "verifier": op["verifier"],
                    "field_provenance": field_provenance,
                    "note": (base.get("note") or "").strip()}
        if op.get("note"):
            table[k]["note"] = (table[k]["note"] + " | " + op["note"]).strip(" |")[:400]
    return _VerificationTable(table, errors)


UNANCHORED_ERROR = "fee/products need a verified disposition or requirement_detail"


def _field_errors(fields: dict) -> list[str]:
    """Pure structural lint. A named product is not proof of a route's rule."""
    from .kimi_primary import DISPOSITIONS
    errors = []
    verdict, detail = fields.get("disposition"), fields.get("requirement_detail")
    details = {d for family in _DETAIL_FAMILY.values() for d in family}
    if verdict is not None and not isinstance(verdict, str):
        return ["unknown disposition"]
    if detail is not None and not isinstance(detail, str):
        return ["unknown requirement_detail"]
    for key in _BOOLEAN_FIELDS:
        if fields.get(key) is not None and not isinstance(fields[key], bool):
            errors.append(f"{key} must be a boolean or null")
    for key in ("account_registration_steps", "payment_process", "submission_process", "forms"):
        value = fields.get(key)
        if value is not None and (not isinstance(value, list) or
                                  any(not isinstance(step, str) for step in value)):
            errors.append(f"{key} must be an array of strings or null")
    if fields.get("photo_requirements") is not None and not isinstance(fields["photo_requirements"], str):
        errors.append("photo_requirements must be a string or null")
    if fields.get("route_workflow_type") is not None:
        from .kimi_primary import WORKFLOW_TYPES
        if not isinstance(fields["route_workflow_type"], str) or fields["route_workflow_type"] not in WORKFLOW_TYPES:
            errors.append("route_workflow_type must be a known workflow enum or null")
    from .reviewed_japan_warning_resolution import health_shape_errors
    errors.extend(health_shape_errors(fields.get("health_requirements")))
    from ..passport_validity import passport_validity_rule_errors
    errors.extend(passport_validity_rule_errors(fields.get("passport_validity_requirement")))
    if verdict is not None and verdict not in DISPOSITIONS:
        errors.append("unknown disposition")
    if detail is not None and detail not in details:
        errors.append("unknown requirement_detail")
    if verdict in _DETAIL_FAMILY and detail is not None and detail not in _DETAIL_FAMILY[verdict]:
        errors.append("requirement_detail contradicts disposition")
    if any(k in fields for k in ("government_fee", "visa_products")) and not (verdict or detail):
        errors.append(UNANCHORED_ERROR)
    def valid_fee(fee):
        if fee is None:
            return True
        if not isinstance(fee, dict):
            return False
        amount = fee.get("amount")
        return amount is None or (not isinstance(amount, bool) and
                                  isinstance(amount, (int, float)) and
                                  math.isfinite(amount) and amount >= 0)
    if "government_fee" in fields and not valid_fee(fields["government_fee"]):
        errors.append("government_fee must contain a nonnegative finite numeric amount or null")
    if "visa_products" in fields and fields["visa_products"] is not None:
        products = fields["visa_products"]
        if (not isinstance(products, list) or any(
                not isinstance(p, dict) or not valid_fee(p.get("fee"))
                for p in products)):
            errors.append("visa_products must be an array of objects with valid numeric fees")
        elif any(p.get("requirement_detail") is not None and
                 (not isinstance(p["requirement_detail"], str) or
                  p["requirement_detail"] not in details) for p in products):
            errors.append("visa_products contain an unknown requirement_detail")
        elif any(p.get("source_url") and not is_government_host(
                hostname(str(p["source_url"]))) for p in products):
            errors.append("visa_products source_url must be an official government page")
    return errors


def lint_rows(rows: list) -> list[dict]:
    """Report unsafe raw entries without dropping their audit trail."""
    out = []
    for r in rows:
        errors = _field_errors(r.get("fields") or {})
        if r.get("verifier", "ai") not in ("human", "ai", "public"):
            errors.append("verifier must be human, ai or public")
        if errors:
            out.append({"route": r.get("route"), "errors": errors,
                        "source_url": r.get("source_url"), "fields": r.get("fields")})
    return out


def _provenance(entry: dict) -> dict:
    result = {"source_url": entry["source_url"], "verified_at": entry["verified_at"],
              "verified_by": str(entry.get("verified_by") or "").strip(),
              "verifier": entry.get("verifier", "ai"),
              "note": str(entry.get("note") or "").strip()[:400]}
    # A verification timestamp is not the interval in which the rule applies.
    # Preserve explicit policy bounds even when malformed: the common reader
    # must hold an invalid interval rather than silently remove its limit.
    for key in ("authority_binding_id", "authority_binding_sha256", "authority_linking_source_id", "authority_image_source_id", "quote_sha256",
                "effective_from", "effective_to", "policy_interval_evidence", "source_id",
                "source_table", "source_closed_list", "source_eu_citizen", "source_country_section",
                "supporting_sources", "supporting_evidence", "additional_quotes",
                "status", "verification_scope", "verified_elements", "retained_unverified_elements",
                "subject"):
        if key in entry:
            result[key] = deepcopy(entry[key])
    if isinstance(entry.get("quote"), str):
        result["quote"] = entry["quote"][:50000]
    return result


# Fields a historical seed entry may mention but that only an explicitly
# scoped field review may set. Opening them for every entry revived old
# ignored values (a legacy "forms: []" blanked a live ETA form list), so an
# entry may set them only through its own reviewed proof that names the
# field and the review kind it belongs to. Legacy entries stay ignored.
SCOPED_FIELDS = frozenset({"forms", "route_workflow_type"})


def _scoped_review(entry: dict, field: str) -> bool:
    proof = (entry.get("field_provenance") or {}).get(field) if isinstance(entry, dict) else None
    scope = proof.get("verification_scope") if isinstance(proof, dict) else None
    return (isinstance(proof, dict) and proof.get("status") == "reviewed"
            and isinstance(scope, dict) and bool(str(scope.get("kind") or "").strip())
            and scope.get("field") == field)


# An entry may declare that some of its fields describe the procedure for
# applicants lawfully resident in one country (a mission's own filing steps,
# fees and waivers). The declaration is stored per field and enforced by the
# lookup, so a route that carries a different residence never receives those
# fields or their proofs; the rest of the entry still applies.
_APPLICABILITY_KEYS = ("lawful_country_of_residence",)


def _field_applicability(entry: dict, clean: dict):
    """Return {field: constraint} or None when the declaration is malformed."""
    raw = entry.get("applicability") if isinstance(entry, dict) else None
    if raw is None:
        return {}
    if not isinstance(raw, dict) or set(raw) != {"lawful_country_of_residence", "fields"}:
        return None
    code = raw.get("lawful_country_of_residence")
    fields = raw.get("fields")
    declared = entry.get("fields") if isinstance(entry.get("fields"), dict) else {}
    if (not isinstance(code, str) or not re.fullmatch(r"[A-Z]{3}", code)
            or not isinstance(fields, list) or not fields
            or any(not isinstance(f, str) or f not in declared for f in fields)):
        return None
    # A field the shape guards quarantined is simply absent; the constraint
    # only ever covers what the entry still sets.
    return {f: {"lawful_country_of_residence": code} for f in fields if f in clean}


def _applicable(hit: dict | None, route: dict) -> dict | None:
    scoped = hit.get("field_applicability") if isinstance(hit, dict) else None
    if not scoped:
        return hit
    residence = str((route or {}).get("lawful_country_of_residence") or "").upper()
    dropped = {f for f, c in scoped.items()
               if residence and c.get("lawful_country_of_residence") != residence}
    if not dropped:
        return hit
    fields = {k: v for k, v in hit["fields"].items() if k not in dropped}
    if not fields:
        return None
    out = dict(hit)
    out["fields"] = fields
    out["field_provenance"] = {k: v for k, v in (hit.get("field_provenance") or {}).items() if k not in dropped}
    out["inapplicable_fields"] = sorted(dropped)
    return out


def _parse_rows(rows, table: dict, *, inherited: dict | None = None) -> dict:
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        from .reviewed_hkg_mainland_fields import entry_errors as delegated_entry_errors
        if delegated_entry_errors(r):
            continue
        route = r.get("route") or {}
        url = str(r.get("source_url") or "").strip()
        when = str(r.get("verified_at") or "").strip()
        fields = r.get("fields") or {}
        if not (route.get("nationality") and route.get("destination")):
            continue
        if not url or not when or not isinstance(fields, dict) or not fields:
            continue
        from .reviewed_social_authority import entry_supported, binding_for
        social_entry = entry_supported(r)
        if not is_government_host(hostname(url)) and not social_entry:
            continue          # an override must cite applicable official evidence
        if r.get("verifier", "ai") not in ("human", "ai", "public"):
            continue
        clean = _normalise_legacy_shapes({k: v for k, v in fields.items()
                                          if k in OVERRIDABLE or (k in SCOPED_FIELDS and _scoped_review(r, k))})
        route_key = _key(route["nationality"], route["destination"],
                         route.get("travel_purpose", "tourism"),
                         route.get("travel_document_type", ""))
        previous = (inherited or {}).get(route_key, {}).get("fields", {})
        errors = _field_errors(dict(previous, **clean))
        # A fee or product audit that does not restate the verdict is kept
        # and anchored at serve time to the answer it sits beside: applied
        # next to a visa, arrival or authorisation verdict, dropped next to
        # an exemption (see apply). Every other lint error quarantines.
        unanchored = any(UNANCHORED_ERROR in e for e in errors)
        errors = [e for e in errors if UNANCHORED_ERROR not in e]
        if errors:
            # Keep safe ancillary facts (arrival cards, processing times,
            # citations) while quarantining unanchored application claims.
            # Raw files remain intact for the correction queue.
            log.warning("Override %s quarantined: %s", route_key, "; ".join(errors))
            invalid_decision = bool(set(errors) & {
                "unknown disposition", "unknown requirement_detail",
                "requirement_detail contradicts disposition"})
            if invalid_decision:
                clean.pop("disposition", None)
                clean.pop("requirement_detail", None)
            for k in ("government_fee", "visa_products"):
                if invalid_decision or any(e.startswith(k) for e in errors):
                    clean.pop(k, None)
            for k in _BOOLEAN_FIELDS + ("passport_validity_requirement",
                                       "account_registration_steps", "payment_process",
                                       "submission_process", "photo_requirements", "health_requirements", "forms", "route_workflow_type"):
                if any(e.startswith(k) for e in errors):
                    clean.pop(k, None)
        for k in _URL_FIELDS:
            v = str(clean.get(k) or "").strip()
            if v and not is_government_host(hostname(v)) and not (
                    k == 'source_url' and social_entry and v == url):
                clean.pop(k, None)
        for k in _CUSTOMER_TEXT:
            if k in clean and _reads_like_review(clean[k]):
                clean.pop(k, None)
        if "corroborating_sources" in clean:
            cleaned = _clean_corroborating(clean["corroborating_sources"])
            if cleaned:
                clean["corroborating_sources"] = cleaned
            else:
                clean.pop("corroborating_sources", None)
        if not clean:
            continue
        applicability = _field_applicability(r, clean)
        if applicability is None:
            log.warning("Override %s skipped: malformed applicability declaration", route_key)
            continue
        provenance = _provenance(dict(r, source_url=url, verified_at=when))
        per_field = {}
        prior_proofs = ((table.get(route_key) or (inherited or {}).get(route_key) or {})
                        .get("field_provenance") or {})
        from .reviewed_hkg_mainland_fields import field_supported as delegated_field_supported
        for k in clean:
            p = (r.get("field_provenance") or {}).get(k)
            if isinstance(p, dict) and p.get("status") == "unknown" and clean[k] is None:
                # A deliberate unknown/null clears an unsupported old value;
                # it does not inherit the entry's source or verification date.
                per_field[k] = {"status": "unknown", "verifier": "ai",
                                "source_url": "", "verified_at": None,
                                "verified_by": "", "note": str(p.get("reason") or p.get("note") or "Unknown")}
                continue
            if (isinstance(p, dict) and p.get("verified_at") and
                    (is_government_host(hostname(str(p.get("source_url") or "")))
                     or delegated_field_supported(p, route, k, clean[k])
                     or social_entry and binding_for(p, route, field=k, value=clean[k])) and
                    p.get("verifier", "ai") in ("human", "ai", "public")):
                per_field[k] = _provenance(p)
            else:
                per_field[k] = dict(provenance)
            per_field[k] = inherit_bounds(prior_proofs.get(k), per_field[k])
        table[route_key] = {
            "fields": clean, "source_url": url, "verified_at": when,
            "verified_by": str(r.get("verified_by") or "").strip(),
            "verifier": r.get("verifier", "ai"),
            "field_provenance": per_field,
            "note": str(r.get("note") or "").strip()[:400],
        }
        if unanchored and any(k in clean for k in _PERMISSION_MACHINERY):
            table[route_key]["unanchored_products"] = True
        if applicability:
            table[route_key]["field_applicability"] = applicability
    return table


# The two fields that describe a permission rather than the route's rule.
_PERMISSION_MACHINERY = ("government_fee", "visa_products")


def anchored_to(hit: dict | None, guidance: dict | None) -> dict | None:
    """Bind a fee or product audit that did not restate the verdict to the
    answer it is served beside.

    Next to a visa, arrival or authorisation verdict the audited fee and
    products are that permission's own facts and apply. Next to an exemption,
    or an answer with no verdict, they would put a priced product beside
    "no visa needed" (the Hong Kong to Vietnam class), so they are dropped
    and the audit's other facts stay. The verdict itself is never verified by
    such an entry: its provenance fields never gain "disposition"."""
    if not hit or not hit.get("unanchored_products"):
        return hit
    verdict = str((guidance or {}).get("disposition") or "").upper()
    anchored = dict(hit)
    anchored.pop("unanchored_products", None)
    if verdict and verdict != "VISA_EXEMPT":
        return anchored
    fields = {k: v for k, v in (hit.get("fields") or {}).items() if k not in _PERMISSION_MACHINERY}
    if not fields:
        return None
    anchored["fields"] = fields
    anchored["field_provenance"] = {k: v for k, v in (hit.get("field_provenance") or {}).items()
                                    if k not in _PERMISSION_MACHINERY}
    return anchored


def reload() -> None:
    """Forget the cached table (tests swap the file path)."""
    global _CACHE
    _CACHE = {"mtime": None, "table": None}


def append_operator_entry(entry: dict, *, guidance: dict | None = None) -> dict:
    """Persist one operator-written override, applying the same gates the
    loader applies, but LOUDLY: a rejected entry raises ValueError naming the
    reason, so the console can tell the operator exactly what to fix instead
    of silently dropping their work. When a cached answer exists, its retained
    raw fields are part of the same pre-write contradiction check."""
    if guidance is not None and not isinstance(guidance, dict):
        raise ValueError("cached guidance must be an object before an edit can be validated")
    route = entry.get("route") or {}
    if not (route.get("nationality") and route.get("destination")):
        raise ValueError("the route needs a nationality and a destination")
    url = str(entry.get("source_url") or "").strip()
    if not url or not is_government_host(hostname(url)):
        raise ValueError("every edit must cite a page on an official "
                         "government domain")
    if not str(entry.get("note") or "").strip():
        raise ValueError("say what was checked and why, in the note")
    if not str(entry.get("verified_at") or "").strip():
        raise ValueError("every edit needs a verification date")
    if entry.get("verifier", "ai") not in ("human", "ai", "public"):
        raise ValueError("verifier must be human, ai or public")
    fields = entry.get("fields") or {}
    if not isinstance(fields, dict):
        raise ValueError("fields must be an object")
    unknown = [k for k in fields if k not in OVERRIDABLE]
    if unknown:
        raise ValueError(f"these fields cannot be edited: {sorted(unknown)}")
    clean = _normalise_legacy_shapes({k: v for k, v in fields.items() if k in OVERRIDABLE})
    for k in _URL_FIELDS:
        v = str(clean.get(k) or "").strip()
        if v and not is_government_host(hostname(v)):
            raise ValueError(f"{k} must be a page on an official government "
                             "domain")
    for k in _CUSTOMER_TEXT:
        if k in clean and _reads_like_review(clean[k]):
            raise ValueError(f"{k} reads like a reviewer's argument, not a "
                             "customer-facing fact. State the fact plainly")
    if not clean:
        raise ValueError("no editable fields were given")
    route_key = _key(route["nationality"], route["destination"],
                     route.get("travel_purpose", "tourism"),
                     route.get("travel_document_type", ""))
    entry = dict(entry, fields=clean, verifier=entry.get("verifier", "ai"))
    path = operator_overrides_path()
    with _OP_LOCK:
        # Re-read under the write lock. A concurrent edit may have changed
        # the verdict since the request began, invalidating a fee-only edit.
        current_table = _load_table()
        if getattr(current_table, 'store_errors', ()):
            raise ValueError("source verification store is unavailable; restore it before editing")
        existing = current_table.get(route_key) or {}
        checked = dict(existing.get("fields") or {}, **clean)
        errors = _field_errors(checked)
        if guidance is not None and str(guidance.get("disposition") or "").upper() not in ("", "VISA_EXEMPT"):
            # A fee or product edit beside a cached visa, arrival or
            # authorisation verdict is that permission's own fact (anchored_to).
            errors = [e for e in errors if UNANCHORED_ERROR not in e]
        if errors:
            raise ValueError("; ".join(errors))
        from .kimi_primary import serve_time_invariants
        from .permission_eligibility import issues as eligibility_issues, annotate as annotate_eligibility
        implied = _verdict_implied_by_detail(checked, {})
        if implied:
            checked = dict(checked, disposition=implied)
        if checked.get("disposition"):
            errors.extend(serve_time_invariants(checked))
        errors.extend(eligibility_issues(checked, route))
        if errors:
            raise ValueError("; ".join(errors))
        if guidance is not None:
            merged, _ = merge_verified_fields(guidance, checked, source_url=url)
            # A cached/imported marker described the old product. Recompute
            # only this derived marker after the correction; keep all facts
            # so unrelated contradictions still reject the atomic write.
            merged = annotate_eligibility(merged, route)
            errors = serve_time_invariants(merged)
            if errors:
                raise ValueError("edit conflicts with the complete cached answer: " + "; ".join(errors))
        incoming = _parse_rows([entry], {}, inherited=current_table).get(route_key) or {}
        incoming_proofs = incoming.get("field_provenance") or {}
        entry = dict(entry, field_provenance=incoming_proofs)
        rows = _read_rows(path)
        # One entry per route: consolidate edits with per-field provenance
        # instead of stacking shadowed duplicates or losing earlier facts.
        def _rk(r):
            rr = r.get("route") or {}
            return _key(rr.get("nationality", ""), rr.get("destination", ""),
                        rr.get("travel_purpose", "tourism"),
                        rr.get("travel_document_type", ""))
        prior = next((r for r in reversed(rows) if _rk(r) == _rk(entry)), None)
        if prior:
            # Retain prior verified fields AND their original authors. A
            # human correcting one fee cannot turn an AI verdict human.
            parsed = _parse_rows([prior], {}, inherited=current_table).get(route_key) or {}
            retained = dict(parsed.get("fields") or {})
            retained.update(clean)
            per_field = dict(parsed.get("field_provenance") or {})
            per_field.update(incoming_proofs)
            entry = dict(entry, fields=retained, field_provenance=per_field)
        rows = [r for r in rows if _rk(r) != _rk(entry)]
        rows.append(entry)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(path)
    reload()
    return entry


def find(route: dict) -> dict | None:
    # A diplomatic or service passport answer is a different policy: it
    # matches ONLY an override verified for that document. Ordinary-passport
    # routes match only document-less overrides.
    doc = str(route.get("travel_document_type") or "ordinary_passport")
    table = _table()
    _LOOKUP_STORE_ERRORS.set(getattr(table, 'store_errors', ()))
    hit = table.get(_key(route.get("passport_nationality", ""),
                         route.get("destination_country", ""),
                         route.get("travel_purpose", "tourism"),
                         doc))
    return _applicable(hit, route)


# Fields that only describe APPLYING for a visa. When a verified override
# says no visa is needed, whatever the model wrote in these is about an
# application that does not happen, and would contradict the verdict on the
# same page ("No visa needed ... processing time about 3 working days").
# NOTE: arrival_card is deliberately NOT here. A visa-free route can still
# require a pre-arrival filing, and dropping it would strand a traveller.
_APPLICATION_ONLY = ("processing_time", "forms", "account_registration_steps",
                     "payment_process", "submission_process",
                     "official_portal_url", "government_fee",
                     # An independent re-verification found visa-free answers
                     # still carrying the machinery of an application: a
                     # "Tourist eVisa" category, an eVisa workflow type, a
                     # channel sentence sending the traveller to a portal, and
                     # a product table with fees. A verified visa-free verdict
                     # clears those too.
                     "visa_products", "visa_category", "application_channel",
                     "application_channel_detail", "route_workflow_type")


def _drop_application_leftovers(merged: dict, fields: dict) -> None:
    """A verified visa-free verdict clears application-only leftovers, unless
    the override itself supplied them. Never invents a value: it removes
    claims that the verified verdict has made false."""
    if str(fields.get("disposition") or "").upper() != "VISA_EXEMPT":
        return
    for k in _APPLICATION_ONLY:
        if k in fields:
            continue          # the verified fact wins, whatever it says
        merged.pop(k, None)
    # The model's subcategory is a leftover too when it names a visa: a
    # verified visa-free verdict cannot sit over "paper_visa" (Hong Kong to
    # Algeria) or "eta_electronic_authorization" (Australia to Spain, for an
    # ETIAS not yet in operation). Dropped, never rewritten: the route's own
    # verified detail, if any, was already merged in.
    if "requirement_detail" not in fields:
        detail = str(merged.get("requirement_detail") or "")
        if detail and detail not in _DETAIL_FAMILY["VISA_EXEMPT"]:
            merged.pop("requirement_detail", None)
    # Both are about applying for a visa. With no visa to apply for they are
    # false, whatever the model said. (This previously only wrote False when
    # the value was ALREADY falsy, so a visa-free answer could still show
    # "Appointment: Required".)
    merged["appointment_required"] = False
    merged["interview_required"] = False
    docs = merged.get("required_documents")
    if docs is not None and "required_documents" not in fields:
        merged["required_documents"] = _documents_without_an_application(docs)
    # A verdict of "no authorisation is required" cannot sit beside a sentence
    # saying one is. Singapore to Spain read "No visa and no travel
    # authorisation" in one field and "require only an approved ETIAS to enter
    # Spain" in the next. exceptions was on no clean-up list, so the model's
    # stale claim outlived the verdict that falsified it.
    if str(merged.get("application_channel") or "").lower() in (
            "not_required", "none", "no_application_required",
            "none_or_port_of_entry") and "exceptions" not in fields:
        merged["exceptions"] = _exceptions_without_a_required_authorisation(
            merged.get("exceptions"))


# Items that exist only to feed an application form. On a route where nothing
# is applied for they are not documents a traveller needs, they are the
# residue of an application that does not happen. Japan to Italy listed "email
# address, payment card" on a verified visa-free verdict, left over from an
# ETIAS the European Commission has not yet brought into operation.
_APPLICATION_ONLY_DOCUMENTS = (
    "email address", "e-mail address", "email", "payment card", "credit card",
    "debit card", "bank card", "application form", "visa application form",
    "online application form", "application fee", "payment method",
)


def _documents_without_an_application(docs):
    """Drop the items that only make sense when there is a form to submit.

    What survives is what a border officer can actually ask to see: the
    passport, the onward ticket, the accommodation booking, the funds."""
    def keep(item: str) -> bool:
        low = str(item).strip().lower()
        return bool(low) and not any(m in low for m in _APPLICATION_ONLY_DOCUMENTS)

    if isinstance(docs, (list, tuple)):
        kept = [d for d in docs if keep(d)]
        return kept if kept else docs
    text = str(docs or "")
    if not text:
        return docs
    kept = [part.strip() for part in text.split(",") if keep(part)]
    return ", ".join(kept) if kept else docs


# A sentence that says the traveller must hold an authorisation, on a route
# whose verified verdict is that none is required. The explanatory sentences
# stay: "ETIAS is an entry authorisation, not a visa" and "ETIAS is not in
# operation" are both true and both useful. Only the assertion goes.
_ASSERTS_AN_AUTHORISATION = re.compile(
    r"(requires?\s+(only\s+)?an?\s+(approved\s+)?(ETIAS|ESTA|K-ETA|ETA|"
    r"electronic travel authoris\w*)"
    r"|must\s+(hold|obtain|apply\s+for|have)\s+an?\s+(approved\s+)?"
    r"(ETIAS|ESTA|K-ETA|ETA|electronic travel authoris\w*)"
    r"|need\s+an?\s+(approved\s+)?(ETIAS|ESTA|K-ETA|ETA)"
    r"|(ETIAS|ESTA|K-ETA)\s+is\s+required)", re.I)


def _exceptions_without_a_required_authorisation(items):
    """Drop only the sentences that contradict a no-authorisation verdict."""
    if not isinstance(items, (list, tuple)):
        if items and _ASSERTS_AN_AUTHORISATION.search(str(items)):
            return None
        return items
    kept = [x for x in items
            if not _ASSERTS_AN_AUTHORISATION.search(str(x or ""))]
    return kept if kept != list(items) else items


# Which requirement_detail subcategories can truthfully sit under each
# verified disposition. A detail outside its verdict's family is a leftover
# from the un-overridden model answer and reads as a contradiction
# ("Visa required" badge next to "unconditional visa-free").
_DETAIL_FAMILY = {
    "VISA_EXEMPT": ("unconditional_visa_free", "conditional_visa_free",
                    "transit_visa_free"),
    "VISA_ON_ARRIVAL": ("evisa_on_arrival", "paper_visa_on_arrival"),
    "ELECTRONIC_AUTHORIZATION_REQUIRED": ("eta_electronic_authorization",),
    "VISA_REQUIRED": ("evisa", "paper_visa"),
}


def _drop_exemption_leftovers(merged: dict, fields: dict,
                              original: dict) -> None:
    """The mirror of _drop_application_leftovers: a verified visa-REQUIRED
    verdict clears the model's exemption claims. Never invents a value."""
    verdict = str(fields.get("disposition") or "").upper()
    if not verdict or verdict == "VISA_EXEMPT":
        return
    if "requirement_detail" not in fields:
        detail = str(merged.get("requirement_detail") or "")
        family = _DETAIL_FAMILY.get(verdict)
        if detail and family is not None and detail not in family:
            merged.pop("requirement_detail", None)
    # When the verdict actually FLIPPED (model said exempt, source says a
    # visa is needed) the model's exemption narrative is falsified with it.
    flipped = str(original.get("disposition") or "").upper() == "VISA_EXEMPT"
    if flipped:
        pat = re.compile(r"免签|免簽|visa[- ]?free|visa[- ]?exempt|exemption",
                         re.I)
        # These values only described the superseded exemption. A verified
        # required verdict establishes neither a zero fee nor absence of an
        # appointment. Remove those claims; never invent their replacements.
        # Nonzero fees and alternate products remain available for review.
        channel = str(merged.get("application_channel") or "").lower()
        if "application_channel" not in fields and channel in (
                "not_required", "none", "no_application_required", "none_or_port_of_entry"):
            merged.pop("application_channel", None)
        fee = merged.get("government_fee")
        if "government_fee" not in fields and isinstance(fee, dict) \
                and fee.get("amount") in (0, 0.0) and not isinstance(fee.get("amount"), bool):
            merged.pop("government_fee", None)
        for key in ("appointment_required", "interview_required", "biometrics_required"):
            if key not in fields and merged.get(key) is False:
                merged.pop(key, None)
        for key in ("processing_time", "route_workflow_type", "visa_category",
                    "application_channel_detail"):
            value = merged.get(key)
            if key not in fields and isinstance(value, str) and re.search(
                    r"no[_ -]?visa|visa[_ -]?exempt|visa[_ -]?free|not applicable|免签|免簽",
                    value, re.I):
                merged.pop(key, None)
        if "exceptions" not in fields:
            v = merged.get("exceptions")
            if isinstance(v, list):
                kept = [x for x in v if not pat.search(str(x))]
                if len(kept) != len(v):
                    merged["exceptions"] = kept
            elif isinstance(v, str) and pat.search(v):
                merged.pop("exceptions", None)
        # The prose fields the model wrote for its exempt answer contradict
        # the verified visa-required verdict just as loudly as the enum did.
        for k in ("permitted_stay", "application_channel_detail",
                  "visa_category"):
            if k not in fields and isinstance(merged.get(k), str) \
                    and pat.search(merged[k]):
                merged.pop(k, None)


def _drop_changed_permission_leftovers(merged: dict, fields: dict,
                                       original: dict) -> bool:
    """A new permission family invalidates unverified application details.

    A visa-to-ETA correction left UK visitor-visa biometrics, documents,
    processing times, and visa-required prose beneath the verified ETA.
    Retain explicitly checked fields and alternate products; the record
    layer assigns each product only its own procedure and evidence.
    """
    def family(g):
        verdict = g.get("disposition")
        known = {"VISA_REQUIRED": "visa", "VISA_ON_ARRIVAL": "arrival",
                 "ELECTRONIC_AUTHORIZATION_REQUIRED": "authorisation",
                 "VISA_EXEMPT": "exemption"}.get(verdict)
        if known:
            return known
        detail = g.get("requirement_detail")
        for disposition, details in _DETAIL_FAMILY.items():
            if detail in details:
                return family({"disposition": disposition})
        return None
    before, after = family(original), family(fields)
    if not before or not after or before == after or "exemption" in (before, after):
        # The dedicated exemption cleanup above already distinguishes
        # application-only documents from ordinary border documents.
        return False
    for k in ("processing_time", "forms", "account_registration_steps",
              "payment_process", "submission_process", "official_portal_url",
              "government_fee", "visa_category", "application_channel",
              "application_channel_detail", "route_workflow_type",
              "required_documents", "entry_requirements", "exceptions",
              "photo_requirements", "biometrics_required", "appointment_required", "interview_required",
              "consular_jurisdiction", "source_url"):
        if k not in fields:
            merged.pop(k, None)
    return True


def _verdict_implied_by_detail(fields: dict, guidance: dict) -> str | None:
    """A verified requirement subcategory IS a verdict. An override that was
    checked for "evisa" (with its fee and products) but never wrote
    `disposition` let a model answer of VISA_EXEMPT stand underneath it, and
    Hong Kong to Vietnam showed "No visa needed" over an eVisa badge and a
    25 USD fee. Returns the disposition the detail belongs to when the model's
    verdict is outside that family, else None (nothing to correct)."""
    if "disposition" in fields:
        return None
    detail = str(fields.get("requirement_detail") or "").strip()
    if not detail:
        return None
    for verdict, family in _DETAIL_FAMILY.items():
        if detail in family:
            # Returned even when the model already agrees: the verdict is
            # then a VERIFIED fact (recorded in the provenance) and the
            # leftover-droppers run. Without this, 272 live routes whose
            # override carried a detail or products but no disposition kept
            # a model-only verdict labelled as verified, and a verified
            # visa-free detail sat beside a model fee (China to Colombia,
            # "No visa needed" with a 138 USD fee).
            return verdict
    return None


def _product_is_free(p: dict) -> bool:
    fee = (p.get("fee") or {}) if isinstance(p.get("fee"), dict) else {}
    amount = fee.get("amount")
    words = f"{p.get('type') or ''} {p.get('notes') or ''}".lower()
    exempt = any(k in words for k in ("visa-free", "visa free", "no visa",
                                      "exempt", "waiver", "without a visa",
                                      "free of charge", "entry is free"))
    return (amount in (None, 0, 0.0)) and (exempt or amount == 0)


def enforce_verdict_invariants(merged: dict) -> None:
    """Normalize only definitional vocabulary; preserve disputed evidence.

    Contradictions are reported by kimi_primary.serve_time_invariants and
    held from readers. Deleting a verified price or rewriting it to zero
    would erase the evidence an operator needs to settle the contradiction.
    """
    if not isinstance(merged, dict):
        return
    if merged.get("disposition") == "VISA_REQUIRED" and merged.get(
            "requirement_detail") in _DETAIL_FAMILY["VISA_ON_ARRIVAL"]:
        merged["disposition"] = "VISA_ON_ARRIVAL"


_TEXT_LIST_FIELDS = frozenset({
    "forms", "required_documents", "exceptions", "account_registration_steps",
    "payment_process", "submission_process",
})


def _normalise_text_lists(guidance):
    """Preserve a historical prose value as one item, without inventing a split.

    Only these string-list fields have a known lossless legacy conversion.
    Unknown shapes and object-list fields remain untouched for invariant review.
    """
    if not isinstance(guidance, dict):
        return guidance
    out = guidance
    for key in _TEXT_LIST_FIELDS:
        value = out.get(key)
        if isinstance(value, str):
            if out is guidance:
                out = dict(guidance)
            out[key] = [value] if value.strip() else []
    return out


def _normalise_legacy_shapes(guidance):
    """Share lossless unknown/list normalization across writers and readers."""
    from ..passport_validity import normalize_passport_validity_rule
    guidance = _normalise_text_lists(guidance)
    if isinstance(guidance, dict) and "passport_validity_requirement" in guidance:
        value = normalize_passport_validity_rule(guidance["passport_validity_requirement"])
        if value is not guidance["passport_validity_requirement"]:
            guidance = dict(guidance, passport_validity_requirement=value)
    return guidance


def _finalize_guidance(guidance, provenance=None):
    """One cleanup boundary for records, transit and traveler responses.

    Only a consistent final exempt answer may lose application leftovers.
    Priced products, contradictory channels and malformed shapes survive
    unchanged so the shared invariant gate can hold them for review.
    """
    from . import kimi_primary
    guidance = _normalise_legacy_shapes(guidance)
    if not isinstance(guidance, dict) or kimi_primary.serve_time_invariants(guidance):
        return guidance
    return kimi_primary._strip_visa_free_leftovers(
        guidance, verified_fields=(provenance or {}).get("fields") or ())


def merge_verified_fields(guidance: dict, fields: dict, *, source_url: str = "") -> tuple[dict, dict]:
    """Pure merge used by both serving and atomic operator-write validation.

    Return the merged guidance and effective checked fields, including a
    verdict implied by a checked detail. This performs no file lookup and
    does not choose a dated policy. Unsupported or contradictory values
    remain visible to the invariant validator unless the sourced correction
    explicitly replaces them or makes an old exemption claim inapplicable.
    """
    guidance = _normalise_legacy_shapes(guidance)
    fields = dict(_normalise_legacy_shapes(fields))
    implied = _verdict_implied_by_detail(fields, guidance)
    if implied:
        fields["disposition"] = implied
    merged = dict(guidance)
    merged.update(fields)
    _drop_application_leftovers(merged, fields)
    _drop_exemption_leftovers(merged, fields, guidance)
    if _drop_changed_permission_leftovers(merged, fields, guidance) and "source_url" not in fields:
        merged["source_url"] = source_url
    enforce_verdict_invariants(merged)
    return _finalize_guidance(merged, {"fields": list(fields)}), fields


def apply(guidance: dict, route: dict) -> tuple[dict, dict | None]:
    """Return (guidance, provenance). The guidance is a COPY with the verified
    fields replaced; provenance names the source, the date and the fields so
    the answer can show what was checked rather than implying all of it was."""
    from . import scheduled_policies, policy_intervals
    from .permission_eligibility import annotate
    token = _LOOKUP_STORE_ERRORS.set(())
    try:
        hit = find(route or {})
        guidance = _annotate_store_status(guidance, _LOOKUP_STORE_ERRORS.get())
    finally:
        _LOOKUP_STORE_ERRORS.reset(token)
    # Missing evidence must hold the original claims, not run exemption or
    # product-family cleanup against an incomplete set of verified layers.
    # Another component's existing hold likewise remains intact on recovery.
    if isinstance(guidance, dict) and guidance.get('source_verification_store_unavailable'):
        return guidance, None
    guidance = _normalise_legacy_shapes(guidance)
    hit = anchored_to(hit, guidance if isinstance(guidance, dict) else None)
    if not hit or not isinstance(guidance, dict):
        result, provenance = scheduled_policies.apply(guidance, None, route)
        result = policy_intervals.annotate(annotate(result, route), provenance, route)
        return _finalize_guidance(result, provenance), provenance
    implied = _verdict_implied_by_detail(hit["fields"], guidance)
    merged, fields = merge_verified_fields(guidance, hit["fields"], source_url=hit["source_url"])
    field_provenance = dict(hit.get("field_provenance") or {})
    if implied:
        field_provenance["disposition"] = dict(
            field_provenance.get("requirement_detail") or _provenance(hit))
    # A newer edit of a side field never takes authorship or the source link
    # of a previously checked verdict. Keep each field's trail available.
    verdict_provenance = field_provenance.get("disposition") or _provenance(hit)
    provenance = dict(verdict_provenance, fields=sorted(fields),
                      field_provenance=field_provenance)
    from .reviewed_condition_resolution import reconcile
    merged = reconcile(route, merged, provenance)
    from .reviewed_japan_warning_resolution import reconcile as reconcile_japan
    merged = reconcile_japan(route, merged, provenance)
    from .reviewed_japan_station_warnings import reconcile as reconcile_japan_station
    merged = reconcile_japan_station(route, merged, provenance)
    from .reviewed_hkg_mainland_warning_resolution import reconcile as reconcile_permit
    merged = reconcile_permit(route, merged, provenance)
    result, provenance = scheduled_policies.apply(merged, provenance, route)
    result = policy_intervals.annotate(annotate(result, route), provenance, route)
    return _finalize_guidance(result, provenance), provenance
