"""Grounded renewal: an answer is re-checked against its own official page.

WHY. The Database's fast path answers from Kimi's model memory, and the
14-day "refresh" used to re-ask the same memory — which is how an answer
whose policy changed (Japan's fees, the Philippines going visa-free) could
be renewed, stale, forever. The 2026-08-22 source audit found ten routes
wrong on the verdict itself; every one was a change the model's knowledge
predates. Asking the model again louder does not fix that. Reading the
official page does.

WHAT THIS DOES. Every stored answer carries the URL of the official page it
rests on (source_url, or the human override's source, or the official
portal). A recheck:

  1. fetches that page — government domains only, with the fetching layer's
     honest challenge detection and its render fallback; a blocked page is a
     recorded failure, never guessed around;
  2. asks Kimi to compare the STORED answer against the PAGE TEXT — not
     against its memory — and name any field the page contradicts, quoting
     the page for each;
  3. validates the proposed corrections deterministically (same whitelist and
     contradiction checks as a fresh answer; a correction that introduces a
     contradiction is refused);
  4. applies what survives, stamps the row with when/where/what changed, and
     extends its freshness window;
  5. NEVER edits a field a human verified: if the page now contradicts an
     override, that is filed into the operator queue for a person — a machine
     does not silently outvote a person, in either direction.

Once a row has been grounded, memory-only regeneration is retired for it:
the page outranks memory, so a failed recheck keeps the existing answer
(still honestly marked stale) rather than reverting to what the model
happens to remember today.
"""
from __future__ import annotations

import hashlib
import json
import contextlib
import re
import os
import time
import threading
from pathlib import Path
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .authority import hostname, is_government_host
from .fetching import fetch
from .evidence_validator import (quote_in_text, supports_disposition, jurisdiction_matches,
                                 field_value_supported, route_supporting_excerpt)
from .models import DatabaseIssueReport, KimiRouteGuidanceCache
from . import freshness_evidence as proof_helpers
from . import comparison_reuse
from . import reviewed_stay_concepts

# Fields the page is allowed to correct — the same vocabulary a human
# override may correct, minus nothing: what a person may fix from a source,
# the machine may PROPOSE from the same source (and gets the extra
# deterministic gate below).
from .verified_overrides import OVERRIDABLE

# Fields whose correct value depends on WHO is travelling. A page that does
# not name this nationality cannot correct these, however confidently it
# describes the destination's general rules.
NATIONALITY_SPECIFIC = frozenset({
    "disposition", "requirement_detail", "visa_category", "permitted_stay",
    "permitted_stay_days", "application_channel", "application_channel_detail",
    "government_fee", "visa_products",
})

FETCH_TIMEOUT_SECONDS = 20.0
CALL_TIMEOUT_SECONDS = 45.0
MAX_PAGE_CHARS = 28_000  # model prompt only; deterministic proof uses the bounded full fetch
MAX_SOURCES = 8
ROUTE_BUDGET_SECONDS = 120.0
EVIDENCE_CONTRACT = 2

_SYSTEM = """You are checking a stored visa-requirements answer against the
OFFICIAL PAGE TEXT provided. Judge ONLY from the page text — never from your
own knowledge. Apply only the policy in force on policy_date. A future
announcement is not a correction to the current rule.

CRITICAL — NATIONALITY. Most government pages describe the destination's
rules for the WORLD, or for a different nationality than the one in the
route. Those pages must NOT be used to correct a fact that is specific to
this applicant's nationality. A generic page saying "you may apply at the
embassy, an accredited agency or a visa centre" does NOT contradict a stored
answer saying THIS nationality must use an accredited agency, and a generic
"stay up to 90 days" does NOT contradict a stored nationality-specific 15 or
30 days. Correct such a field ONLY when the page names this nationality (or
the applicant's country) and states the rule for them. When the page is
generic, treat those fields as unaddressed and leave them alone.

APPLICATION WORKFLOW. Route fields describe the default entry product, not
all optional products on the page. An embassy's office hours or appointment
notice for consular visa applicants does not contradict appointment_required
false for a visa-free, no-application visit. Quote an appointment rule that
explicitly applies to the default exempt journey; otherwise leave that field
unaddressed. A genuine changed entry condition or changed default visa verdict
must still be reported with its own scoped evidence. Empty workflow values
(null, empty text or empty lists) are missing extraction, not a claim that old
instructions were withdrawn. Report an actual changed rule with its value and
quote instead. The legacy online_application channel and online_portal describe
the same filing channel; a spelling change alone is not a policy conflict.

STAY CONCEPTS. Compare like with like: a published maximum for visa-exempt
visit eligibility is distinct from the individual admission period granted by
an immigration pass at entry. Read the stored stay text as well as its number.
An intentionally unknown individual grant is not a claim that an explicitly
retained exemption threshold is unknown. A changed threshold, changed admission
rule or missing explanation must still be reported with its own scoped quote.

MONEY, VALIDITY AND STAY ARE THE HIGHEST-VALUE CHECKS. Government fees and
validity periods change by law and the stored answer's figure may predate the
change: a 2026 audit found a stored GBP 10 where the page said GBP 20, a
stored USD 21 where the fee had become USD 40.27, and a stored 60-day/16-day
e-visa where the rule had been 120/30 for a year. When the page states a fee
amount, a validity period, or a stay length for a product the stored answer
also carries, compare digit for digit — a different number IS a contradiction
and must be corrected with the page's own figure and quote. A single price the
page states for all applicants (an ESTA, an ETA, an e-visa) is not
nationality-specific; a country-wise fee table is, so read THIS nationality's
row and no other.

GOVERNMENT AND AGENCY FEES. A published official fee remains available when
an agency's additional charge is unpublished: retain the official amount and
state "Agency fee not included" when the source explicitly separates them.
Keep nationality, application-location, product and effective-date conditions.
Uncertain travel-document acceptance is a separate field; it does not erase
an independently applicable published tariff or become verified by that tariff.
An empty fee extraction is not evidence that a fee was withdrawn. Propose an
actual sourced amount (including an explicit zero waiver); source silence must
leave the existing official fee alone.

Reply STRICT JSON:
{"page_relevant": true|false  (does this page actually cover this route/topic?),
 "page_is_nationality_specific": true|false  (does the page state rules FOR
    this applicant's nationality, rather than general/worldwide rules?),
 "consistent": true|false     (does the page contradict any stored field?),
 "corrected_fields": {field: new value, ...}  (ONLY fields the page text
    contradicts, with the value the page states; use the stored answer's own
    field names and shapes; {} when consistent),
 "evidence": {field: "short quote from the page", ...}  (a quote for EVERY
    corrected OR confirmed unchanged field; only explicitly supported fields
    earn renewed freshness; a correction without a matching quote is discarded),
 "route_evidence": {"quote": "literal rule heading", "source_id": "page",
    "source_table": {"heading_quote": "literal visa rule heading",
    "table_quote": "complete literal bounded heading and country list",
    "nationality_quote": "exact standalone country list item"}}
    (optional: only for an explicit finite country list; never infer visa-required
    from eVisa eligibility; preserve qualifications. A supplied reviewed_evidence
    contract can be reused only when every quoted passage still occurs.),
 "field_scope": {field: "literal bounded passage naming the exact visa program and field"},
 "note": "one short sentence"}
Rules: if the page does not mention a field, it is NOT a contradiction — leave
it alone. Classification proposals must use the supplied allowed_enum_values;
do not turn ordinary visit conditions into invented requirement_detail labels.
Report any supported changed condition under its actual field with its quote.
Never invent a fee, date or URL the page does not state. If the page
is irrelevant or unreadable, say page_relevant false and change nothing.
An explicit warning that official guidance conflicts is not a settled fact
contradicted by one page. Preserve that warning; a single source cannot
adjudicate a disagreement between official sources or verify the warning away.
When in doubt about whether the page speaks for THIS nationality, say
page_is_nationality_specific false and correct nothing nationality-specific.

For the Chinese ordinary-passport tourist page at
https://www.cn.emb-japan.go.jp/itpr_zh/visa_kanko.html only, the complete section
between "1．什么是中国人赴日旅游签证" and "2．签证类型" may support VISA_REQUIRED.
If it is present in full, extract route_evidence with source_id "page", quote
equal to that complete first section (including its heading, excluding the next
heading), and source_country_section {program: "japan_chinese_tourist_visa",
source_id: "page", heading_quote: the literal first heading, closing_quote: the
literal next heading, section_quote: the same complete first section}. Preserve
its designated-agency requirement. This supports no fee, stay duration,
electronic/paper issuance, other nationality, other purpose or other document.
Never assemble the proof from a different section or shorten its conditions."""

_PROVIDER = None
_MODEL_SLOTS = threading.BoundedSemaphore(4)


def _comparison_facts(guidance: dict) -> dict:
    """Send policy values, not their nested review archives, to extraction.

    Product proof can dwarf the source page and exhaust the comparison budget.
    Only the field_provenance metadata is omitted from this prompt copy; all
    policy values, conditions and notes are retained. Full original guidance
    and provenance still bind comparison reuse and every deterministic check.
    The selected route proof remains a separate reviewed_evidence input.
    """
    def without_archives(value):
        if isinstance(value, dict):
            return {key: without_archives(child) for key, child in value.items()
                    if key != 'field_provenance'}
        if isinstance(value, list):
            return [without_archives(child) for child in value]
        return value
    return {key: without_archives(guidance[key]) for key in OVERRIDABLE if key in guidance}


def _comparison_schema_errors(answer) -> list[str]:
    """Invalid extraction is not a model judgment that a page is irrelevant."""
    if not isinstance(answer, dict):
        return ["comparison response must be an object"]
    errors = [f"comparison {name} must be a boolean" for name in
              ("page_relevant", "page_is_nationality_specific", "consistent")
              if type(answer.get(name)) is not bool]
    errors += [f"comparison {name} must be an object" for name in
               ("corrected_fields", "evidence") if not isinstance(answer.get(name), dict)]
    errors += [f"comparison {name} must be an object" for name in
               ("route_evidence", "field_scope")
               if name in answer and not isinstance(answer[name], dict)]
    return errors


def _provider_diagnostic(exc) -> dict:
    """Keep fixed diagnostic metadata; never a provider body or prompt."""
    from .. import provider_errors
    from ..providers.kimi import KimiHttpError
    from . import kimi_primary
    out = {"error_type": type(exc).__name__[:80]}
    if isinstance(exc, kimi_primary.GuidanceProviderError):
        envelope = exc.envelope
        category = envelope.get("category", "unknown")
        out["category"] = category if category in provider_errors.CATALOG else "unknown"
    elif isinstance(exc, (kimi_primary.GuidanceTimeout, TimeoutError)):
        out["category"] = "kimi_unavailable"
        out["technical"] = "comparison_timeout"
    else:
        out["category"] = "unknown"
    cause = exc.__cause__
    if isinstance(cause, KimiHttpError):
        out["http_status"] = cause.status
        if cause.error_type in {"content_filter", "invalid_request_error", "authentication_error",
                "permission_error", "not_found_error", "rate_limit_error", "rate_limit_reached_error",
                "exceeded_current_quota_error", "server_error", "api_error", "overloaded_error"}:
            out["provider_error_type"] = cause.error_type
    return out


def _fetch_diagnostic(result) -> dict:
    """Safe source failure categories without raw exception strings."""
    error = str(result.error or "").lower()
    pdf_errors = {"pdf_download_size_limit", "pdf_empty_document", "pdf_extraction_timeout",
        "pdf_extraction_capacity", "pdf_extraction_failed", "pdf_encrypted", "pdf_page_limit",
        "pdf_text_limit", "pdf_page_stream_limit", "pdf_image_only_or_no_extractable_text",
        "pdf_has_image_only_pages", "pdf_malformed", "pdf_parser_unavailable"}
    code = ("tls_certificate_error" if "certificate_verify_failed" in error else
            "source_timeout" if "timeout" in error or "deadline" in error else
            "source_challenge" if result.challenge else
            "http_error" if isinstance(result.http_status, int) and result.http_status >= 400 else
            error if error in pdf_errors else "source_fetch_error")
    return {"error_code": code, "http_status": result.http_status,
            "media_type": result.media_type if result.media_type in {"application/pdf", "text/html"} else None,
            "extraction_method": result.extraction_method if result.extraction_method in {"pypdf_text", "html_text"} else None}


def set_provider(fn) -> None:
    """Tests inject callable(system, user) -> dict. None resets to live Kimi."""
    global _PROVIDER
    _PROVIDER = fn


def _call(system: str, user: str, *, timeout_seconds: float = CALL_TIMEOUT_SECONDS) -> dict:
    from .bounded_io import call
    from . import kimi_primary
    budget = min(CALL_TIMEOUT_SECONDS, timeout_seconds)
    deadline = time.monotonic() + budget
    provider = _PROVIDER
    return call(lambda: provider(system, user) if provider is not None else
        kimi_primary._live_call(system, user, timeout=max(0.001, deadline - time.monotonic()),
                                max_tokens=6000), budget, _MODEL_SLOTS)


def _now():
    return datetime.now(timezone.utc)


def candidate_sources(guidance: dict, override: dict | None, *, limit: int | None = MAX_SOURCES) -> list[str]:
    """The official pages this answer rests on: the human override's source
    first (a person chose it), then the answer's own source, then the portal.
    Government domains only; order-preserving dedupe."""
    urls = []
    if override:
        urls.append(override.get("source_url") or "")
    g = guidance or {}
    urls.append(str(g.get("source_url") or ""))
    # A headline verdict page cannot attest the fees and conditions carried
    # by other cited pages. Visit their actual field/product provenance too.
    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"source_url", "url", "evidence_url"} and isinstance(item, str):
                    urls.append(item)
                elif key == "source_urls" and isinstance(item, list):
                    urls.extend(u for u in item if isinstance(u, str))
                elif isinstance(item, (dict, list)):
                    collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
    collect(override or {})
    collect(g)
    urls.append(str(g.get("official_portal_url") or ""))
    out, seen = [], set()
    for u in urls:
        if not isinstance(u, str):
            continue
        u = u.strip()
        if not u or u in seen or not u.lower().startswith(("http://", "https://")):
            continue
        if not is_government_host(hostname(u)):
            continue
        seen.add(u)
        out.append(u)
    return out if limit is None else out[:limit]


def _stamp(row, entry: dict) -> None:
    """Retain an earlier good read through outages, but retire a disproved read.
    Reading the same page and finding no route evidence is new evidence;
    failing to read it is not evidence against the previous check."""
    ver = dict(row.verification) if isinstance(row.verification, dict) else {}
    previous = ver.get("last_good_check") or ver.get("grounded_check") or {}
    previous = previous if isinstance(previous, dict) else {}
    irrelevant = set(entry.get("irrelevant_sources") or [])
    if entry.get("outcome") in ("page_not_relevant", "page_not_about_route"):
        irrelevant.update(entry.get("sources_tried") or [])
        if entry.get("source_url"):
            irrelevant.add(entry["source_url"])
    if previous.get("outcome") == "checked" and previous.get("source_url") in irrelevant:
        ver["superseded_check"] = dict(previous)
        ver.pop("last_good_check", None)
    ver["grounded_check"] = entry
    if isinstance(entry.get("source_reads"), int) and not isinstance(entry["source_reads"], bool) and entry["source_reads"] > 0:
        ver["last_source_read_at"] = entry.get("at")
    if entry.get("outcome") == "checked":
        ver["last_good_check"] = dict(entry)
    row.verification = ver


def _canonical_json_guard(raw):
    """Normalize representation, without accepting ambiguous or invalid JSON."""
    from decimal import Decimal

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON object key")
            result[key] = value
        return result

    def invalid_constant(_value):
        raise ValueError("Nonfinite JSON number")

    def render(value):
        if isinstance(value, dict):
            return "{" + ",".join(json.dumps(key, ensure_ascii=True) + ":" + render(value[key])
                                  for key in sorted(value)) + "}"
        if isinstance(value, list):
            return "[" + ",".join(render(item) for item in value) + "]"
        if isinstance(value, Decimal):
            if not value.is_finite():
                raise ValueError("Nonfinite JSON number")
            sign, digits, exponent = value.as_tuple()
            digits = list(digits)
            if not any(digits):
                digits, exponent = [0], 0
            else:
                while digits[-1] == 0:
                    digits.pop()
                    exponent += 1
            # Lossless decimal normalization must not round through a float
            # or Decimal context. Keep exponent syntax distinct from integers.
            return ("-" if sign else "") + "".join(map(str, digits)) + "e" + str(exponent)
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)

    if raw is None:
        return None
    try:
        value = json.loads(raw, object_pairs_hook=unique_object,
                           parse_float=Decimal, parse_constant=invalid_constant)
        # Sorting object keys is semantic; array order and bool/int/float
        # distinctions remain intact, including decimals beyond float precision.
        return render(value)
    except (ValueError, TypeError, RecursionError, ArithmeticError):
        # SQL NULL never equals another NULL: malformed stored JSON must not
        # pass a guarded write, even if the caller supplied the same bytes.
        return None


def json_unchanged(db, column, value):
    """Compare parsed JSON atomically, including legacy SQLite encodings.

    SQLite's JSON column is text. Direct equality rejects unchanged compact,
    pretty, Unicode-escaped or reordered objects. The connection-local pure
    function runs inside the UPDATE, so real edits after the final SELECT
    still fail the compare-and-swap guard. No legacy rows are rewritten.
    """
    from sqlalchemy import literal, cast, or_, func
    if db.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import JSONB
        comparison = cast(column, JSONB) == literal(value, type_=JSONB)
    elif db.get_bind().dialect.name == "sqlite":
        connection = db.connection().connection.driver_connection
        connection.create_function("ellis_json_guard", 1, _canonical_json_guard, deterministic=True)
        comparison = func.ellis_json_guard(column) == func.ellis_json_guard(literal(value, type_=column.type))
    else:
        comparison = column == literal(value, type_=column.type)
    return or_(column.is_(None), comparison) if value is None else comparison


def _commit_recheck(db, row, entry: dict, *, expected_guidance: dict,
                    expected_route: dict, fresh_until=None, comparison_cache=None, completion_projection=None) -> bool:
    """Merge metadata freshly and commit only if that exact row still exists.

    API and sweep processes have separate memory/locks. A final refresh alone
    still loses writes between SELECT and UPDATE, so the update compares the
    read metadata and the guidance this check actually evaluated. A conflict
    rolls back this attempt's pending issues/corrections/history, without
    retrying evidence against an answer it did not read.
    """
    from types import SimpleNamespace
    from sqlalchemy import update
    from sqlalchemy.orm.attributes import set_committed_value

    model = KimiRouteGuidanceCache
    key, row_id = row.cache_key, row.id
    desired_guidance = row.guidance
    # Column selection bypasses the ORM identity map and never autoflushes
    # the pending raw correction before its compare-and-swap guard.
    with db.no_autoflush:
        latest = db.execute(select(model.verification, model.guidance, model.route,
            model.fresh_until, model.generated_at).where(model.id == row_id)).first()
    from . import detail_jobs
    lease = detail_jobs.active_lease()
    lease_ok = latest is not None and detail_jobs.owns(lease, SimpleNamespace(
        id=row_id, cache_key=key, generated_at=latest.generated_at, verification=latest.verification))
    if (latest is None or latest.guidance != expected_guidance or latest.route != expected_route
            or lease is not None and not lease_ok
            or isinstance(latest.verification, dict) and latest.verification.get("detail_pending") and not lease_ok):
        db.rollback()
        if latest is not None:
            db.refresh(row)
        return False
    stamped = SimpleNamespace(verification=latest.verification)
    provisional = dict(entry, renewed=False, detail_completion_pending=True) if lease is not None else entry
    _stamp(stamped, provisional)
    if comparison_cache is not None:
        stamped.verification['comparison_cache'] = comparison_cache

    values = {"verification": stamped.verification}
    changed = desired_guidance != expected_guidance
    if changed:
        values["guidance"] = desired_guidance
    if fresh_until is not None and lease is None:
        values["fresh_until"] = fresh_until
    stmt = update(model).where(model.id == row_id,
        model.generated_at == latest.generated_at,
        json_unchanged(db, model.verification, latest.verification),
        json_unchanged(db, model.guidance, expected_guidance), json_unchanged(db, model.route, expected_route),
        model.fresh_until == latest.fresh_until).values(**values).execution_options(synchronize_session=False)
    if lease is not None:
        stmt = stmt.where(detail_jobs.live_lease_clause(db))
    with db.no_autoflush:
        result = db.execute(stmt)
    if result.rowcount != 1:
        db.rollback()
        db.refresh(row)
        return False
    # The guarded UPDATE already wrote these values. Mark them clean so the
    # subsequent ORM commit cannot issue an unconditional stale UPDATE.
    for name, value in values.items():
        set_committed_value(row, name, value)
    if changed:
        from . import change_log
        change_log.record(db, key, expected_route, expected_guidance, desired_guidance,
            origin="grounded_recheck", note=f"corrected against {entry.get('source_url') or ''}")
    # Save the exact state the source check committed, before any refresh
    # can rebase the old proof onto a concurrently edited answer.
    if lease is not None and completion_projection is not None:
        from copy import deepcopy
        lease["_checked_snapshot"] = deepcopy({"guidance": desired_guidance,
            "route": expected_route, "verification": stamped.verification,
            "fresh_until": latest.fresh_until, "renew_until": fresh_until,
            "projection": completion_projection})
    db.commit()
    return True


def effective_check(verification: dict | None) -> dict:
    """Latest route-supported reading, or the last one through a failed read."""
    ver = verification if isinstance(verification, dict) else {}
    gc = ver.get("grounded_check") if isinstance(ver.get("grounded_check"), dict) else {}
    if gc.get("outcome") == "checked" and gc.get("evidence_contract") == EVIDENCE_CONTRACT:
        return gc
    good = ver.get("last_good_check") if isinstance(ver.get("last_good_check"), dict) else {}
    return good if good.get("outcome") == "checked" and good.get("evidence_contract") == EVIDENCE_CONTRACT else {}


def last_source_read_at(verification: dict | None):
    """Transport evidence only; it never supplies a policy verification stamp."""
    ver = verification if isinstance(verification, dict) else {}
    check = ver.get("grounded_check") if isinstance(ver.get("grounded_check"), dict) else {}
    if isinstance(check.get("source_reads"), int) and not isinstance(check["source_reads"], bool) and check["source_reads"] > 0:
        return check.get("at")
    return ver.get("last_source_read_at") or effective_check(ver).get("at")


def _page_has_visa_topic(text: str) -> bool:
    return bool(re.search(r"visa|travel authori[sz]|\beta\b|\besta\b|免签|免簽|签证|簽證|thị thực", text or "", re.I))


def _enum_proposal_errors(raw: dict) -> list[str]:
    """Malformed model classifications are failed extraction, not policy facts.

    Keep nullable optional fields' existing deletion/adjudication path. A new
    phrase that restates entry conditions cannot become a novel enum merely
    because its words occur in an official quote.
    """
    from . import kimi_primary
    fields = raw.get('corrected_fields')
    if not isinstance(fields, dict):
        return []
    vocabularies = {'disposition': kimi_primary.DISPOSITIONS,
                    'requirement_detail': kimi_primary.REQUIREMENT_DETAILS,
                    'application_channel': kimi_primary.APPLICATION_CHANNELS}
    return sorted(k for k, allowed in vocabularies.items() if k in fields
                  and not (fields[k] is None and k != 'disposition')
                  and not (isinstance(fields[k], str) and fields[k] in allowed))



def _empty_workflow_proposal_errors(raw):
    # Missing extraction is not an official claim that a published workflow
    # ceased to exist. Report explicit changes under their actual value;
    # empty readings remain failed checks, never route-policy disputes.
    fields = raw.get('corrected_fields')
    workflow = {'application_channel', 'application_channel_detail',
                'account_registration_steps', 'payment_process',
                'submission_process', 'appointment_required'}
    if not isinstance(fields, dict):
        return []
    return sorted(k for k, v in fields.items() if k in workflow and
                  (v is None or isinstance(v, (str, list, dict)) and not v))


def _empty_official_fee_proposal_errors(raw):
    """Missing extraction cannot withdraw a separately published tariff.

    Explicit not-published findings use the reviewed absence pathway. A page
    about agency charges or document acceptance cannot prove an empty
    government-fee correction merely because its quote was fetched.
    """
    fields = raw.get("corrected_fields")
    if not isinstance(fields, dict) or "government_fee" not in fields:
        return []
    fee = fields["government_fee"]
    return ["government_fee"] if (fee in (None, "", [], {}) or
        isinstance(fee, dict) and fee.get("amount") is None) else []


def _equivalent_workflow_aliases(guidance, quoted, evidence):
    # One observed legacy spelling, not a policy change. Do not normalize raw
    # storage or grant verification/TTL credit as a side effect of comparison.
    if (guidance.get('application_channel') == 'online_application'
            and quoted.get('application_channel') == 'online_portal'):
        return {'application_channel': {'record_holds': 'online_application',
            'value': 'online_portal', 'quote': evidence.get('application_channel')}}
    return {}

def _quoted_proposals(raw: dict, text: str, route: dict | None = None) -> tuple[dict, dict, list[str]]:
    fields = raw.get("corrected_fields") or {}
    evidence = raw.get("evidence") or {}
    if not isinstance(fields, dict):
        fields = {}
    if not isinstance(evidence, dict):
        evidence = {}
    quoted, unquoted = {}, []
    invalid_enums = set(_enum_proposal_errors(raw))
    empty_workflow = set(_empty_workflow_proposal_errors(raw))
    empty_fees = set(_empty_official_fee_proposal_errors(raw))
    for k, v in fields.items():
        if k not in OVERRIDABLE:
            continue
        if k in invalid_enums or k in empty_workflow or k in empty_fees:
            unquoted.append(k)
            continue
        if k == 'passport_validity_requirement':
            from ..passport_validity import normalize_passport_validity_rule
            v = normalize_passport_validity_rule(v)
        if k == 'passport_validity_requirement' and isinstance(v, dict) and v.get('kind') == 'valid_for_duration_of_stay':
            # A provider synonym is accepted only after the literal quote
            # proves this exact border-entry constraint; application validity
            # is a separate obligation and cannot be normalized into it.
            normalized = {'kind': 'valid_through_departure', 'months': 0}
            if (set(v) <= {'kind','months'} and not isinstance(v.get('months'),bool) and v.get('months') in (None,0)
                    and isinstance(evidence.get(k),str) and quote_in_text(evidence[k],text)
                    and field_value_supported(k,normalized,evidence[k])):
                v = normalized
        if (not isinstance(evidence.get(k), str) or not quote_in_text(evidence[k], text)
                or route is not None and not proof_helpers.field_scope_matches_route(k,evidence[k],route)
                or (v not in (None, "", [], {}) and not field_value_supported(k, v, evidence[k]))):
            unquoted.append(k)
        else:
            quoted[k] = v
    return quoted, evidence, sorted(unquoted)


def _supports_route(page_text: str, raw: dict, route: dict, guidance: dict,
                    proposed: dict, source_url: str = "", policy_date: str = "") -> bool:
    """Silence, generic pages and the model's relevance flag cannot renew a row.
    A sourced NEW verdict is eligible too, so a wrong stored verdict can be
    corrected instead of making its own correction impossible."""
    from . import kimi_primary
    nationality = str(route.get("passport_nationality") or "")
    if not nationality or raw.get("page_relevant") is not True \
            or raw.get("page_is_nationality_specific") is not True:
        return False
    verdict = proposed.get("disposition", guidance.get("disposition"))
    detail = proposed.get("requirement_detail")
    if detail and "disposition" not in proposed:
        verdict = next((d for d, details in kimi_primary.DETAIL_FAMILY.items()
                        if detail in details), verdict)
    statement = route_supporting_excerpt(page_text, verdict, route, policy_date=policy_date)
    # A narrowly structured outbound table may scope its rows with a heading,
    # e.g. 'Visa requirements for HKSAR passport holders. Vietnam: visa required'.
    # Never join an unrelated nationality-bearing sentence to that heading.
    if not statement and source_url and jurisdiction_matches(source_url, nationality):
        from .evidence_validator import _NATIONALITY_NAMES
        nat_names = _NATIONALITY_NAMES.get(nationality, ())
        dest_names = _NATIONALITY_NAMES.get(route.get("destination_country", ""), ())
        for nat_name in nat_names:
            heading = re.search(r"Visa requirements for\s+" + re.escape(nat_name.strip()) +
                                r"(?: SAR)? passport holders[.:\n]", page_text, re.I)
            if not heading:
                continue
            for dest_name in dest_names:
                row_match = re.search(r"(?:^|[.\n])\s*" + re.escape(dest_name.strip()) +
                                     r"\s*:\s*(?:no visa required|visa required|visa[- ]free|visa[- ]exempt)\s*[.\n]",
                                     page_text[heading.end():], re.I)
                if row_match:
                    combined = nationality + " " + nat_name.strip() + " passport holders: " + row_match.group().strip(".\n ")
                    statement = route_supporting_excerpt(combined, verdict, route, policy_date=policy_date)
    if not statement:
        return False
    if not source_url or jurisdiction_matches(source_url, route.get("destination_country", "")):
        return True
    # Home-government outbound guidance can settle its citizens' route too.
    # It must explicitly name the traveller group and the DESTINATION in the
    # supporting statement, so an inbound rule for visitors to that government
    # cannot be recycled as a rule for travelling elsewhere. The test lives
    # in source_authority (guard-20260912 T3) so the grading hand and this
    # hand cannot disagree.
    from .source_authority import is_corroborating
    if not jurisdiction_matches(source_url, nationality):
        return False
    return is_corroborating(source_url, route, statement=statement)


def _source_authority_matches(url: str, route: dict) -> bool:
    return (jurisdiction_matches(url, route.get("destination_country", "")) or
            jurisdiction_matches(url, route.get("passport_nationality", "")))


def _source_conflict_fields(guidance):
    """Explicit unresolved warnings await adjudication, never auto-selection.

    Recognize the stored warning itself, not a provider's unsupported claim
    that a field is disputed. Ordinary requirements and historical statements
    saying a conflict was resolved do not match this narrow warning form.
    """
    warning = re.compile(
        r"^\s*(?:unresolved\s*[:–—-]?\s*)?(?:"
        r"(?:official|consular)\b[^.!?;\n]{0,80}\b(?:guidance|sources?|instructions?)\s+"
        r"(?:(?:currently|still)\s+)?conflicts?\b|"
        r"conflicting\s+(?:official|consular)\b[^.!?;\n]{0,60}\b(?:guidance|sources?|instructions?)\b)", re.I)
    return {key for key, value in guidance.items() if key in OVERRIDABLE
            and isinstance(value, str) and warning.search(value)
            and not re.search(r"\b(?:resolved|superseded)\b", value, re.I)}


def _proposal_identity(proposal):
    """Only an unchanged finding inherits an operator's dismissal.

    Read timestamps and HTML whitespace change on ordinary rechecks. The
    source, quoted claim, current value and any conflicting readings do not
    get ignored: a change to any of them is a new finding.
    """
    if not isinstance(proposal, dict) or not isinstance(proposal.get("fields"), dict):
        return None
    fields = proposal["fields"]
    if not fields or not proposal.get("source_url") or any(
            not isinstance(v, dict) or not {"page_says", "record_holds", "quote"} <= set(v)
            for v in fields.values()):
        return None

    def normalize(value):
        if isinstance(value, str):
            return " ".join(value.split())
        if isinstance(value, dict):
            return {k: normalize(v) for k, v in value.items()}
        if isinstance(value, list):
            return [normalize(v) for v in value]
        return value

    try:
        return json.dumps(normalize({k: proposal.get(k) for k in
            ("source_url", "fields", "conflicting_evidence")}),
            sort_keys=True, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return None


def _file_dispute(db, row, route, guidance, fields, evidence, source_url, when,
                  *, conflicting_evidence=None):
    if not fields:
        return
    field_key = ",".join(sorted(fields))[:64]
    proposal = {"source_url": source_url, "checked_at": when,
                "fields": {k: {"page_says": v, "record_holds": guidance.get(k),
                               "quote": str(evidence.get(k) or "")}
                           for k, v in sorted(fields.items())}}
    if conflicting_evidence:
        proposal["conflicting_evidence"] = conflicting_evidence
    note = (f"Automatic source check against {source_url}: " + "; ".join(
        f"{k}: page says {json.dumps(v, ensure_ascii=False)[:120]} "
        f"(quote: {str(evidence.get(k) or '')[:160]})" for k, v in sorted(fields.items())))[:1000]
    candidates = db.execute(select(DatabaseIssueReport).where(
        DatabaseIssueReport.cache_key == row.cache_key,
        DatabaseIssueReport.reported_by == "freshness_monitor",
        DatabaseIssueReport.field == field_key,
        DatabaseIssueReport.status.in_(("open", "acknowledged", "dismissed")))).scalars().all()
    identity = _proposal_identity(proposal)
    if identity is not None and any(issue.status == "dismissed" and
            _proposal_identity(issue.proposal) == identity for issue in candidates):
        return
    # A second official page must not replace the evidence from the first.
    # Match the full field set too: the display key is truncated to 64 chars.
    existing = next((issue for issue in candidates if issue.status != "dismissed"
        and isinstance(issue.proposal, dict)
        and issue.proposal.get("source_url") == source_url
        and isinstance(issue.proposal.get("fields"), dict)
        and set(issue.proposal["fields"]) == set(fields)), None)
    if existing is not None:
        existing.note, existing.proposal = note, proposal
    else:
        db.add(DatabaseIssueReport(org_id="platform", cache_key=row.cache_key,
                                   route=route, field=field_key, note=note,
                                   reported_by="freshness_monitor", status="open",
                                   proposal=proposal))


def _future_scheduled_source(url: str, route: dict, policy_date: str) -> bool:
    """Known future schedules cannot rewrite today's raw canonical answer."""
    from . import scheduled_policies as scheduled
    on = scheduled._date(policy_date) or scheduled._today()
    route_key = (route.get("passport_nationality"), route.get("destination_country"),
                 route.get("travel_purpose", "tourism"), route.get("travel_document_type", "ordinary_passport"))
    normalized = str(url).split("?")[0].rstrip("/")
    matches = [p for p in scheduled._load() if scheduled._key(p["route"]) == route_key and
               normalized in {str(p["source_url"]).split("?")[0].rstrip("/"),
                              str(p["evidence"]["source_url"]).split("?")[0].rstrip("/")}]
    return bool(matches) and all(scheduled._date(p["effective_from"]) > on for p in matches)


def recheck_row(db, row, *, today: str | None = None, budget_seconds: float | None = None,
                should_stop=None) -> dict:
    """Read a canonical answer's actual source. Only route-supported, fully
    quoted and consistent corrections can renew it. All other outcomes retain
    the answer without claiming verification or inventing replacement facts."""
    from . import kimi_primary, verified_overrides
    if not kimi_primary.is_canonical_key(row.cache_key):
        return {"outcome": "noncanonical", "route_key": row.cache_key}
    if (row.verification or {}).get("detail_pending"):
        from . import detail_jobs
        lease = detail_jobs.active_lease()
        if lease is None:
            return detail_jobs.recover_row(db, row, today=today, budget_seconds=budget_seconds,
                                          should_stop=should_stop)
        if not detail_jobs.owns(lease, row):
            return {"outcome": "concurrent_change", "route_key": row.cache_key, "detail_pending": True}
    route, original = dict(row.route or {}), dict(row.guidance or {})
    override = verified_overrides.find(route)
    guidance, provenance = verified_overrides.apply(dict(original), route)
    unresolved_fields = _source_conflict_fields(guidance)
    old_comparisons = (row.verification or {}).get('comparison_cache')
    reviewed_fields, catalog = proof_helpers.reviewed_evidence(override, row.verification)
    source_override = dict(override or {}, supporting_sources=[{'id':k,'url':v} for k,v in catalog.items()])
    all_sources = candidate_sources(guidance, source_override, limit=None)
    previous = (row.verification or {}).get("grounded_check") or {}
    cursor = previous.get("source_cursor", 0)
    cursor = cursor if isinstance(cursor, int) and cursor >= 0 else 0
    offset = cursor % len(all_sources) if all_sources else 0
    ordered = all_sources[offset:] + all_sources[:offset]
    sources = ordered[:MAX_SOURCES]
    route_budget = ROUTE_BUDGET_SECONDS if budget_seconds is None else max(0.0, min(ROUTE_BUDGET_SECONDS, budget_seconds))
    deadline = time.monotonic() + route_budget
    when = today or _now().isoformat()
    if not sources:
        if not _commit_recheck(db, row, {"at": when, "outcome": "no_official_source",
                "disputed_fields": sorted(unresolved_fields),
                "note": "the answer names no government page to check"},
                expected_guidance=original, expected_route=route):
            return {"outcome": "concurrent_change", "route_key": row.cache_key, "changed": []}
        return {"outcome": "no_official_source", "route_key": row.cache_key}

    tried, irrelevant, unquoted_all = [], [], set()
    readings, source_checks, candidates, captures = [], [], [], {}
    full_captures, deferred_comparisons, comparison_context = {}, [], {}
    model_counts = {'model_comparisons': 0, 'model_comparisons_reused': 0}
    visited, completed = set(), set()
    page = raw = None
    fallback = None

    def compare(fr, payload, signature, previous=None):
        remaining = deadline - time.monotonic()
        if remaining <= 0 or (should_stop is not None and should_stop()):
            visited.discard(fr.requested_url)
            return
        reused = previous is not None
        compared_at = previous['model_compared_at'] if reused else _now().isoformat()
        try:
            if reused:
                answer = previous['response']
                model_counts['model_comparisons_reused'] += 1
            else:
                model_counts['model_comparisons'] += 1
                answer = _call(_SYSTEM, json.dumps(payload, ensure_ascii=False, sort_keys=True), timeout_seconds=remaining)
        except Exception as e:
            source_checks.append({'source_url': fr.final_url, 'outcome': 'provider_error', 'at': when,
                'source_read_at': fr.retrieved_at or when, 'model_compared_at': compared_at,
                'comparison_reused': False, 'provider_diagnostic': _provider_diagnostic(e)})
            return
        schema_errors = _comparison_schema_errors(answer)
        if schema_errors:
            source_checks.append({'source_url': fr.final_url, 'outcome': 'validation_error', 'at': when,
                'source_read_at': fr.retrieved_at or when, 'model_compared_at': compared_at,
                'comparison_reused': reused, 'validation_errors': schema_errors,
                'relevance_reason': 'invalid_comparison_response'})
            return
        quoted, evidence, unquoted = _quoted_proposals(answer, fr.content_text, route)
        invalid_enums = _enum_proposal_errors(answer)
        empty_workflow = _empty_workflow_proposal_errors(answer)
        empty_fees = _empty_official_fee_proposal_errors(answer)
        equivalent = _equivalent_workflow_aliases(guidance, quoted, evidence)
        quoted = {k:v for k,v in quoted.items() if k not in equivalent}
        workflow_candidate = dict(guidance, **({"disposition": quoted["disposition"]}
            if "disposition" in quoted else {}))
        workflow_rejected = {k: {'value': quoted.get(k, guidance.get(k)), 'quote': q}
            for k, q in evidence.items() if not proof_helpers.field_workflow_matches(
                k, q, workflow_candidate, route)}
        quoted = {k: v for k, v in quoted.items() if k not in workflow_rejected}
        awaiting_adjudication = {k: {"value": v, "quote": evidence[k]}
                                for k, v in quoted.items() if k in unresolved_fields}
        quoted = {k: v for k, v in quoted.items() if k not in unresolved_fields}
        unquoted_all.update(unquoted)
        check = {'source_url': fr.final_url, 'outcome': 'validation_error' if invalid_enums or empty_workflow or empty_fees or equivalent or workflow_rejected else 'page_not_relevant', 'at': when,
            'source_read_at': fr.retrieved_at or when, 'model_compared_at': compared_at,
            'comparison_reused': reused, 'content_hash': fr.content_hash, 'verified_fields': [],
            'unquoted_fields': unquoted,
            'validation_errors': ([f'invalid enum proposal: {k}' for k in invalid_enums]
                + [f'unscoped default workflow evidence: {k}' for k in sorted(workflow_rejected)]
                + [f'empty workflow proposal: {k}' for k in empty_workflow]
                + [f'empty official fee proposal: {k}' for k in empty_fees]
                + [f'legacy workflow alias requires normalization: {k}' for k in sorted(equivalent)]),
            'empty_workflow_fields': {k: {'value': (answer.get('corrected_fields') or {}).get(k), 'quote': evidence.get(k)} for k in empty_workflow},
            'empty_official_fee_fields': {k: {'value': (answer.get('corrected_fields') or {}).get(k), 'quote': evidence.get(k)} for k in empty_fees},
            'equivalent_legacy_fields': equivalent,
            'rejected_workflow_fields': workflow_rejected,
            'awaiting_adjudication': awaiting_adjudication,
            'proposed_fields': {k: {'value': v, 'quote': evidence[k]} for k, v in quoted.items()}}
        source_checks.append(check)
        candidates.append((fr, answer, quoted, evidence, check))
        comparison_context[fr.final_url] = signature

    for url in sources:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or (should_stop is not None and should_stop()):
            break
        visited.add(url)
        fr = fetch(url, timeout_seconds=min(FETCH_TIMEOUT_SECONDS, remaining),
                   total_timeout_seconds=min(FETCH_TIMEOUT_SECONDS, remaining))
        if not (fr.ok and fr.content_text and not fr.challenge
                and is_government_host(fr.final_hostname)):
            source_checks.append({"source_url": url, "outcome": "fetch_failed", "at": when,
                                  "fetch_diagnostic": _fetch_diagnostic(fr)})
            continue
        if fr.final_url in completed:
            continue
        completed.add(fr.final_url)
        tried.append(fr.final_url)
        full_captures[url] = full_captures[fr.final_url] = {'url': fr.final_url, 'text': fr.content_text}
        captures[url] = captures[fr.final_url] = {'url': fr.final_url,
            'text': fr.content_text, 'checked_at': when[:10]}
        companion = url in catalog.values()
        if ((not _source_authority_matches(fr.final_url, route) and not companion) or
                _future_scheduled_source(fr.final_url, route, when[:10])):
            irrelevant.extend((url, fr.final_url))
            source_checks.append({"source_url": fr.final_url, "outcome": "scope_or_effective_date_mismatch", "at": when})
            continue
        if not _page_has_visa_topic(fr.content_text):
            if companion:
                source_checks.append({'source_url': fr.final_url, 'outcome': 'supporting_source_read', 'at': when})
                continue
            irrelevant.extend((url, fr.final_url))
            source_checks.append({"source_url": fr.final_url, "outcome": "page_not_relevant", "at": when})
            continue
        payload = {"route": {k: route.get(k) for k in
                              ("passport_nationality", "destination_country",
                               "travel_purpose", "travel_document_type")},
                   "policy_date": when[:10],
                   "stored_answer": _comparison_facts(guidance),
                   "allowed_enum_values": {'disposition': kimi_primary.DISPOSITIONS,
                       'requirement_detail': kimi_primary.REQUIREMENT_DETAILS,
                       'application_channel': kimi_primary.APPLICATION_CHANNELS},
                   "reviewed_evidence": reviewed_fields.get('disposition') if
                       (reviewed_fields.get('disposition') or {}).get('source_url') == fr.final_url else None,
                   "official_page_url": fr.final_url,
                   "official_page_text": fr.content_text[:MAX_PAGE_CHARS]}
        signature = comparison_reuse.identity(payload=payload, system=_SYSTEM,
            guidance=guidance, provenance=provenance, reviewed_fields=reviewed_fields,
            catalog=catalog, sources=all_sources, full_text=fr.content_text,
            requested_url=url, evidence_contract=EVIDENCE_CONTRACT,
            provider={'model': os.getenv('KIMI_GUIDANCE_MODEL') or os.getenv('KIMI_MODEL', 'kimi-k3'),
                      'base_url': os.getenv('KIMI_BASE_URL', 'https://api.moonshot.ai/v1'),
                      'test_provider': id(_PROVIDER) if _PROVIDER is not None else None})
        previous = comparison_reuse.lookup(old_comparisons, signature=signature,
            source_url=fr.final_url, policy_date=when[:10])
        if previous:
            # Wait for current companion reads before reusing extraction. A
            # miss retains the existing read/compare order and bounded budget.
            deferred_comparisons.append((fr, payload, signature, previous))
        else:
            compare(fr, payload, signature)

    dependency_digest = comparison_reuse.dependencies(full_captures)
    for fr, payload, signature, previous in deferred_comparisons:
        compare(fr, payload, signature,
            previous if previous['dependencies'] == dependency_digest else None)

    # Classify after all available pages are read: a named contract may need
    # a second official page, and a generic fee page cannot establish eligibility.
    fresh_sources = proof_helpers.source_map(captures, catalog)
    # Both fresh captures must exist before distinguishing an eligibility
    # threshold from a reviewed, individually assigned admission grant.
    scoped_candidates = []
    for fr, answer, quoted, evidence, check in candidates:
        rejected = reviewed_stay_concepts.rejected_stay_fields(quoted, evidence,
            fr.final_url, captures, guidance, reviewed_fields, route, when[:10])
        if rejected:
            check['rejected_stay_concept_fields'] = rejected
            check['validation_errors'].extend('stay concept mismatch: ' + k for k in rejected)
            check['outcome'] = 'validation_error'
            quoted = {k: v for k, v in quoted.items() if k not in rejected}
            check['proposed_fields'] = {k: v for k, v in check['proposed_fields'].items() if k not in rejected}
        scoped_candidates.append((fr, answer, quoted, evidence, check))
    candidates = scoped_candidates
    route_results = []
    deferred = []
    for fr, answer, quoted, evidence, check in candidates:
        source = captures[fr.final_url]
        verdict = quoted.get('disposition', guidance.get('disposition'))
        result = proof_helpers.structured_route_result(answer, reviewed_fields.get('disposition'),
            source, fresh_sources, route, verdict, dict(guidance, **quoted), when[:10])
        has_contract = proof_helpers.has_structured_contract(answer, reviewed_fields.get('disposition'), fr.final_url)
        supported = bool(result) or (not has_contract and _supports_route(
            source['text'], answer, route, guidance, quoted, fr.final_url, when[:10]))
        if supported:
            if result is None:
                result = {'ok': True, 'quote': route_supporting_excerpt(source['text'], verdict, route,
                    policy_date=when[:10]), 'scope_quotes': [], 'program': None}
            check['route_evidence'] = result
            check['route_supported'] = True
            check['outcome'] = 'validation_error' if check.get('validation_errors') else 'checked'
            route_results.append(result)
            if page is None: page, raw = fr, answer
            readings.append((fr, answer, quoted, evidence, check))
        else:
            if answer.get('page_relevant') is True:
                check['relevance_reason'] = 'route_evidence_not_supported'
            else:
                check['relevance_reason'] = 'model_marked_page_irrelevant'
            deferred.append((fr, answer, quoted, evidence, check))
    for fr, answer, quoted, evidence, check in deferred:
        allowed = proof_helpers.ancillary_fields(captures[fr.final_url], answer, guidance, route, route_results)
        if allowed:
            check['outcome'] = 'validation_error' if check.get('validation_errors') else 'field_checked'
            check['allowed_fields'] = sorted(allowed)
            readings.append((fr, answer, {k:v for k,v in quoted.items() if k in allowed}, evidence, check))
        rejected = {k:v for k,v in quoted.items() if k not in allowed}
        if not allowed:
            irrelevant.append(fr.final_url)
        if rejected:
            _file_dispute(db, row, route, guidance, rejected, evidence, fr.final_url, when)
            fallback = (fr, rejected)

    # Advance by actual attempts. Advancing by the selected eight URLs could
    # return to the same offset forever when a slow first page used the budget.
    source_cursor = (offset + max(1, len(visited))) % len(all_sources) if all_sources else 0
    if page is None:
        if should_stop is not None and should_stop():
            outcome = "cancelled"
        elif time.monotonic() >= deadline:
            outcome = "budget_exhausted"
        else:
            outcome = ("provider_error" if any(s["outcome"] == "provider_error" for s in source_checks)
                       else "validation_error" if any(s.get('validation_errors') for s in source_checks)
                       else "page_not_relevant" if tried else "fetch_failed")
        entry = {"at": when, "outcome": outcome, "sources": sources,
                 "disputed_fields": sorted(unresolved_fields),
                 "sources_tried": tried, "irrelevant_sources": irrelevant,
                 "source_reads": len(tried),
                 "source_fetch_failures": sum(s["outcome"] == "fetch_failed" for s in source_checks),
                 "unquoted_fields": sorted(unquoted_all), "source_checks": source_checks,
                 "validation_errors": sorted({error for c in source_checks for error in c.get('validation_errors', [])}),
                 "source_cursor": source_cursor, "unchecked_sources": [u for u in all_sources if u not in visited],
                 **model_counts}
        if not _commit_recheck(db, row, entry, expected_guidance=original, expected_route=route):
            return {"outcome": "concurrent_change", "route_key": row.cache_key, "changed": [],
                    "source_reads": entry["source_reads"], "source_fetch_failures": entry["source_fetch_failures"],
                    **model_counts}
        return {**entry, "route_key": row.cache_key, "changed": [],
                "disputed": sorted(fallback[1]) if fallback else [],
                "generic_skipped": sorted(set(fallback[1]) & NATIONALITY_SPECIFIC) if fallback else []}

    effective_candidate = dict(guidance)
    for _, _, changes, _, _ in readings:
        effective_candidate.update(changes)
    scoped_readings = []
    for fr, answer, quoted, evidence, check in readings:
        rejected = {k:v for k,v in quoted.items() if not proof_helpers.field_program_matches(k,evidence.get(k),effective_candidate)}
        if proof_helpers.needs_program_scope(effective_candidate):
            allowed = proof_helpers.ancillary_fields(captures[fr.final_url],answer,effective_candidate,route,route_results)
            check['unscoped_program_fields'] = sorted({'government_fee','processing_time'} - allowed)
            rejected.update({k:v for k,v in quoted.items() if k in {'government_fee','processing_time'} and k not in allowed})
        if rejected:
            _file_dispute(db,row,route,guidance,rejected,evidence,fr.final_url,when)
            fallback=(fr,rejected)
        scoped_readings.append((fr,answer,{k:v for k,v in quoted.items() if k not in rejected},evidence,check))
    readings = scoped_readings

    proposed, conflicts, observations = {}, set(), []
    unquoted = sorted(unquoted_all)
    for fr, answer, quoted, quotes, check in readings:
        for key, value in quoted.items():
            if key in proposed and proposed[key] != value:
                conflicts.add(key)
            proposed[key] = value
            observations.append({"field": key, "source_url": fr.final_url,
                                 "value": value, "quote": quotes[key]})
    # A page confirming the current value is evidence too. Comparing only
    # corrected_fields silently chose another page's correction over that
    # reading, even when both quoted contradictory official figures.
    for fr, answer, quoted, quotes, check in readings:
        allowed = set(check.get("allowed_fields", proposed)) - set(check.get("unscoped_program_fields", []))
        for key in (set(proposed) & allowed) - set(quoted):
            value, quote = guidance.get(key), quotes.get(key)
            if (value not in (None, "", [], {}) and isinstance(quote, str)
                    and quote_in_text(quote, captures[fr.final_url]["text"])
                    and field_value_supported(key, value, quote)
                    and proof_helpers.field_scope_matches_route(key, quote, route)
                    and proof_helpers.field_program_matches(key, quote, guidance)
                    and proof_helpers.field_workflow_matches(key, quote, guidance, route, confirmation=True)):
                observations.append({"field": key, "source_url": fr.final_url,
                                     "value": value, "quote": quote})
                if proposed[key] != value:
                    conflicts.add(key)
    # The source read can take a minute. Refresh both the row and the override,
    # and refuse to overwrite any field changed during that time.
    db.refresh(row)
    from . import detail_jobs
    lease = detail_jobs.active_lease()
    if ((lease is not None and not detail_jobs.owns(lease, row))
            or (row.verification or {}).get("detail_pending") and lease is None):
        db.rollback()
        return {"outcome": "concurrent_change" if lease is not None else "detail_pending", "route_key": row.cache_key,
                "source_reads": len(tried),
                "source_fetch_failures": sum(s["outcome"] == "fetch_failed" for s in source_checks),
                **model_counts}
    override = verified_overrides.find(route)
    protected = set((override or {}).get("fields") or {})
    current = dict(row.guidance or {})
    seen, final_provenance = verified_overrides.apply(dict(current), route)
    unresolved_fields = _source_conflict_fields(seen)
    disputed, applied = {}, {}
    for k, v in proposed.items():
        if k in conflicts or conflicts & {"disposition", "requirement_detail"}:
            disputed[k] = v
            continue
        if v == seen.get(k):
            continue
        workflow_change = (k == 'appointment_required' and seen.get('disposition') == 'VISA_EXEMPT'
                           and seen.get('application_channel') in {'none', 'not_required'})
        if workflow_change or k in protected or k in conflicts or v in (None, "", [], {}) or current.get(k) != original.get(k):
            # Changing a no-application visit into an appointment workflow
            # needs review even when the old Boolean lacked an override.
            # A conditional entry quote must never become an unqualified fact.
            disputed[k] = v
        else:
            applied[k] = v
    if applied:
        candidate = {**current, **applied}
        merged, candidate_provenance = verified_overrides.apply(dict(candidate), route)
        clean, missing, contradictions = kimi_primary.validate_answer(merged)
        contradictions = list(contradictions) + kimi_primary.serve_time_invariants(merged)
        # Validation may discard malformed fields. Never write the rejected
        # original value into raw guidance while stamping it as corrected.
        rejected = any(k not in clean or clean[k] != v for k, v in applied.items())
        if contradictions or missing or rejected:
            disputed.update(applied)
            applied = {}
        else:
            row.guidance = candidate  # only page corrections, never override copies
            seen = merged
            final_provenance = candidate_provenance
    conflicting_evidence = sorted((item for item in observations if item["field"] in conflicts),
                                 key=lambda item: (item["field"], item["source_url"]))
    for fr, _, quoted, quotes, _ in readings:
        own_disputes = {k: v for k, v in quoted.items() if k in disputed}
        _file_dispute(db, row, route, seen, own_disputes, quotes, fr.final_url, when,
                      conflicting_evidence=conflicting_evidence)
    remaining_contradictions = kimi_primary.serve_time_invariants(seen)
    consistent = (all(answer.get("consistent") is True for _, answer, _, _, _ in readings) and not proposed and not unquoted
                  and not disputed and not unresolved_fields and fallback is None and not remaining_contradictions)
    # Verifying the verdict does not verify every accompanying fee, duration,
    # document and condition. Renew the whole row only with explicit evidence
    # for every substantive populated field; existing old provenance does not
    # make an unmentioned detail newly checked today.
    metadata_fields = {"confidence", "source_url", "official_portal_url", "corroborating_sources", "unpublished_fields"}
    substantive = {k for k in OVERRIDABLE - metadata_fields if seen.get(k) not in (None, "", [], {})}
    verified_fields, verified_field_sources = set(), {}
    for fr, answer, quoted, quotes, check in readings:
        source = captures[fr.final_url]
        result, confirmed = None, False
        if check.get('route_supported'):
            result = proof_helpers.structured_route_result(answer, reviewed_fields.get('disposition'),
                source, fresh_sources, route, seen.get('disposition'), seen, when[:10])
            has_contract = proof_helpers.has_structured_contract(answer, reviewed_fields.get('disposition'), fr.final_url)
            confirmed = bool(result) or (not has_contract and _supports_route(source['text'],answer,route,seen,{},fr.final_url,when[:10]))
            if not confirmed:
                check['verified_fields'] = []
                continue
        allowed = set(check.get('allowed_fields', substantive)) - set(check.get('unscoped_program_fields', []))
        if proof_helpers.needs_program_scope(seen) or not check.get('route_supported'):
            program_fields = proof_helpers.ancillary_fields(source,answer,seen,route,route_results)
            allowed -= {'government_fee','processing_time'} - program_fields
        supported_fields = {k for k in substantive & allowed if isinstance(quotes.get(k), str)
                            and quote_in_text(quotes[k], source['text'])
                            and field_value_supported(k, seen[k], quotes[k])
                            and proof_helpers.field_scope_matches_route(k,quotes[k],route)
                            and proof_helpers.field_program_matches(k,quotes[k],seen)
                            and proof_helpers.field_workflow_matches(k,quotes[k],seen,route,confirmation=True)}
        supported_fields.discard('disposition')
        supported_fields.difference_update(unresolved_fields)
        for key in (substantive & allowed) - {'disposition'} - unresolved_fields:
            reviewed = proof_helpers.reviewed_field_quote(key, seen[key], reviewed_fields,
                                                         source, fresh_sources, route)
            if (reviewed and proof_helpers.field_program_matches(key,reviewed[0],seen)
                    and proof_helpers.field_workflow_matches(key,reviewed[0],seen,route,confirmation=True)):
                supported_fields.add(key)
                verified_field_sources[key] = {'source_url':fr.final_url, 'checked_at':when,
                    'quote':reviewed[0], 'supporting_evidence':reviewed[1]}
        if check.get('route_supported'):
            verdict = seen.get('disposition')
            if confirmed:
                result = result or check['route_evidence']
                supported_fields.add('disposition')
                verified_field_sources['disposition'] = {'source_url':fr.final_url, 'checked_at':when,
                    'quote':result['quote'], 'scope_quotes':result.get('scope_quotes', [])}
                if verdict == 'VISA_EXEMPT' and seen.get('application_channel') in {'not_required','none'}:
                    supported_fields.add('application_channel')
                    verified_field_sources['application_channel'] = dict(verified_field_sources['disposition'],
                        derived_from='disposition')
        # A price on a generic page cannot verify a verdict, even if the model
        # supplies a second unrelated visa quote in the same response.
        if not check.get('route_supported'):
            supported_fields.discard('disposition')
        verified_fields.update(supported_fields)
        check['verified_fields'] = sorted(supported_fields)
        if supported_fields:
            check['revalidated_at'] = when
        for key in supported_fields:
            if key not in verified_field_sources:
                verified_field_sources[key] = {'source_url':fr.final_url, 'checked_at':when, 'quote':quotes[key]}
    unverified_fields = sorted(substantive - verified_fields)
    unchecked_sources = [u for u in all_sources if u not in visited]
    failed_sources = any(c["outcome"] in {"fetch_failed", "provider_error", "validation_error"} for c in source_checks)
    missing_for_renewal = row.missing_fields
    from . import detail_jobs
    if detail_jobs.active_lease() is not None:
        # Historical stage-one missing markers may outlive already repaired
        # fields. Recompute them from the exact source-checked projection;
        # metadata is cleared only by successful owning-job completion.
        _, actual_missing, actual_contradictions = kimi_primary.validate_answer(dict(row.guidance or {}, **seen), detail_known=True)
        missing_for_renewal = actual_missing or actual_contradictions
    renewed = (not (disputed or unresolved_fields or unquoted or fallback or remaining_contradictions or missing_for_renewal
                    or unverified_fields or unchecked_sources or failed_sources
                    or active_disputed_fields(db, row.cache_key)) and (consistent or bool(applied)))
    validation_errors = sorted({error for c in source_checks for error in c.get('validation_errors', [])})
    entry = {"at": when, "outcome": "validation_error" if validation_errors else "checked", "evidence_contract": EVIDENCE_CONTRACT,
                 "source_reads": len(tried),
                 "source_fetch_failures": sum(s["outcome"] == "fetch_failed" for s in source_checks),
                 "source_url": page.final_url,
                 "content_hash": page.content_hash, "consistent": consistent,
                 "changed_fields": sorted(applied), "disputed_fields": sorted(set(disputed) | unresolved_fields),
                 "awaiting_adjudication_fields": sorted(unresolved_fields),
                 "unquoted_fields": unquoted, "irrelevant_sources": irrelevant,
                 "validation_errors": validation_errors,
                 "verified_fields": sorted(verified_fields), "unverified_fields": unverified_fields,
                 "field_sources": verified_field_sources, "source_checks": source_checks,
                 "source_cursor": source_cursor, "unchecked_sources": unchecked_sources,
                 "renewed": renewed,
                 "generic_page_skipped": sorted(fallback[1]) if fallback else [],
                 "note": str(raw.get("note") or "")[:200], **model_counts}
    # No unresolved proposal, fabricated quote, missing cell or unexplained
    # disagreement can silently extend the freshness promise.
    fresh_until = None
    if renewed:
        ttl = (kimi_primary.TTL_DAYS if row.status == kimi_primary.STATUS_PRIMARY
               else kimi_primary.UNCERTAIN_TTL_DAYS)
        fresh_until = _now() + timedelta(days=ttl)
    snapshots = []
    if not (proposed or disputed or unresolved_fields or unquoted or fallback or remaining_contradictions):
        for fr, answer, _, _, check in readings:
            snapshot = comparison_reuse.snapshot(answer=answer, check=check,
                signature=comparison_context.get(fr.final_url), dependency_digest=dependency_digest,
                source_url=fr.final_url, policy_date=when[:10], page_text=captures[fr.final_url]['text'])
            if snapshot and (check.get('route_evidence') or {}).get('proof'):
                # A bounded serialization must not drop a required proof
                # component and then poison every same-day reuse. Unknown new
                # contract shapes simply do not enter the extraction cache.
                roundtrip = proof_helpers.structured_route_result(snapshot['response'],
                    reviewed_fields.get('disposition'), captures[fr.final_url], fresh_sources,
                    route, seen.get('disposition'), seen, when[:10])
                if (not roundtrip or roundtrip.get('scope_quotes') != check['route_evidence'].get('scope_quotes')
                        or roundtrip.get('conditions') != check['route_evidence'].get('conditions')):
                    snapshot = None
            if snapshot: snapshots.append(snapshot)
    updated_comparisons = comparison_reuse.merge(old_comparisons, snapshots)
    if not _commit_recheck(db, row, entry, expected_guidance=current, expected_route=route,
            fresh_until=fresh_until, comparison_cache=updated_comparisons,
            completion_projection={"guidance": seen, "provenance": final_provenance}):
        return {"outcome": "concurrent_change", "route_key": row.cache_key, "changed": [], "disputed": [],
                "source_reads": entry["source_reads"], "source_fetch_failures": entry["source_fetch_failures"],
                **model_counts}
    return {"outcome": entry['outcome'], "route_key": row.cache_key, "consistent": consistent,
            "renewed": entry["renewed"], "verified_fields": entry["verified_fields"],
            "unverified_fields": entry["unverified_fields"], "unchecked_source_count": len(unchecked_sources),
            "source_reads": entry["source_reads"], "source_fetch_failures": entry["source_fetch_failures"],
            **model_counts,
            "changed": sorted(applied), "disputed": sorted(set(disputed) | unresolved_fields),
            "generic_skipped": sorted(fallback[1]) if fallback else [],
            "unquoted_fields": unquoted, "validation_errors": validation_errors, "source_url": page.final_url}


def note_unreadable(db, row, outcome: dict | None) -> None:
    """A stale answer whose official page could not be read is never
    regenerated from the model's memory. The answer stays, marked stale, and
    ONE open issue tells a person which page failed and why. Refreshed in
    place on every attempt, never stacked."""
    from sqlalchemy import select as _sel
    o = dict(outcome or {})
    # A fetched page that lacks this route's proof is an evidence gap, not
    # a transport outage. Never create a misleading source_unreadable issue.
    if o.get("outcome") != "fetch_failed" or o.get("source_reads", 0):
        return
    # An unreadable page is a sweep statistic, not a correction for a person:
    # filing one ticket per stale row put 803 machine notes in front of the
    # operators on 2026-09-09 and buried the real disputes. The sweep summary
    # already counts unreadable pages; the queue stays for facts to rule on.
    if not os.getenv("ELLIS_FILE_UNREADABLE_ISSUES", "").strip():
        return
    reason = str(o.get("outcome") or "unreadable")
    pages = o.get("sources") or o.get("sources_tried") or []
    note = (f"The official page could not be read ({reason}), so the answer "
            f"was not renewed. Pages tried: {', '.join(str(p) for p in pages)[:400]}")
    existing = db.execute(_sel(DatabaseIssueReport).where(
        DatabaseIssueReport.cache_key == row.cache_key,
        DatabaseIssueReport.reported_by == "freshness_monitor",
        DatabaseIssueReport.field == "source_unreadable",
        DatabaseIssueReport.status.in_(("open", "acknowledged")))).scalars().first()
    if existing is not None:
        existing.note = note[:1000]
    else:
        db.add(DatabaseIssueReport(
            org_id="platform", cache_key=row.cache_key, route=dict(row.route or {}),
            field="source_unreadable", note=note[:1000],
            reported_by="freshness_monitor", status="open"))
    db.commit()


def recheck_route(db, route: dict) -> dict | None:
    """Recheck by route (the stale-serving path). None when nothing is cached."""
    from . import kimi_primary
    key = kimi_primary.canonical_key(kimi_primary.cache_key(route))
    row = db.execute(select(KimiRouteGuidanceCache).where(
        KimiRouteGuidanceCache.cache_key == key)).scalars().first()
    if row is None:
        return None
    return recheck_row(db, row)


def has_been_grounded(row) -> bool:
    return bool(effective_check(row.verification))


def due_rows(db, *, older_than_hours: int = 48, limit: int = 400) -> list:
    """The cached answers whose last grounded check is older than the cycle,
    oldest first — the worklist for the automatic 48-hour sweep. A row never
    checked sorts first of all. Transit (via:) variants are skipped: the
    canonical row carries the route's facts and the variants inherit its
    corrections at read time through the same guidance fields."""
    from datetime import datetime, timedelta, timezone
    from . import kimi_primary
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=older_than_hours)
    out = []
    for row in db.execute(select(KimiRouteGuidanceCache)).scalars():
        key = row.cache_key or ""
        verification = row.verification if isinstance(row.verification, dict) else {}
        if not kimi_primary.is_canonical_key(key):
            continue
        if verification.get("detail_pending"):
            from . import detail_jobs
            if detail_jobs.retry_eligible(row):
                out.append((None, row))
            continue
        gc = verification.get("grounded_check") if isinstance(verification.get("grounded_check"), dict) else {}
        try:
            at = datetime.fromisoformat(str(gc.get("at") or "").replace("Z", "+00:00"))
            at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
            if at > now:
                at = None
        except (TypeError, ValueError, OverflowError):
            at = None
        if at is None or at < cutoff:
            out.append((at, row))
    out.sort(key=lambda pair: pair[0] or datetime.min.replace(tzinfo=timezone.utc))
    return [row for _at, row in out[:limit]]


def propose_for_issue(db, issue_id: str) -> dict | None:
    """A human flagged a record, so Ellis checks the official page and
    attaches what it found as a PROPOSAL on the issue: page value, stored
    value and verbatim quote per field, for an operator to accept or
    decline in the correction queue. Nothing is changed here — a flag plus
    a page reading is still a proposal until a person accepts it."""
    from . import kimi_primary, verified_overrides
    from .models import DatabaseIssueReport
    issue = db.get(DatabaseIssueReport, issue_id)
    if issue is None or issue.reported_by == "freshness_monitor":
        return None
    route = dict(issue.route or {})
    expected_key = kimi_primary.canonical_key(kimi_primary.cache_key(route))
    if issue.cache_key and kimi_primary.canonical_key(issue.cache_key) != expected_key:
        issue.proposal = {"outcome": "route_identity_mismatch", "checked_at": _now().isoformat()}
        db.commit()
        return issue.proposal
    row = db.execute(select(KimiRouteGuidanceCache).where(
        KimiRouteGuidanceCache.cache_key ==
        kimi_primary.canonical_key(issue.cache_key or kimi_primary.cache_key(route)))).scalars().first()
    if row is None or (row.verification or {}).get("detail_pending"):
        return None
    guidance = dict(row.guidance or {})
    override = verified_overrides.find(route)
    if override:
        guidance, _ = verified_overrides.apply(guidance, route)
    unresolved_fields = _source_conflict_fields(guidance)
    when = _now().isoformat()
    outcome = "page_unreachable"
    proposal = None
    reviewed_fields, catalog = proof_helpers.reviewed_evidence(override, row.verification)
    source_override = dict(override or {}, supporting_sources=[{'id':k,'url':v} for k,v in catalog.items()])
    captures, candidates = {}, []
    workflow_rejections = []
    comparison_rejections = []
    deadline = time.monotonic() + ROUTE_BUDGET_SECONDS
    requested = {"visa_requirement": "disposition", "visa_fee_amount": "government_fee",
                 "visa_fee_currency": "government_fee", "visa_type": "visa_category",
                 "application_method": "application_channel", "stay_duration": "permitted_stay"}.get(issue.field, issue.field)
    for url in candidate_sources(guidance, source_override):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        fr = fetch(url, timeout_seconds=min(FETCH_TIMEOUT_SECONDS, remaining),
                   total_timeout_seconds=min(FETCH_TIMEOUT_SECONDS, remaining))
        if not (fr.ok and fr.content_text and not fr.challenge
                and is_government_host(fr.final_hostname)):
            continue
        captures[url] = captures[fr.final_url] = {'url':fr.final_url,
            'text':fr.content_text, 'checked_at':when[:10]}
        companion = url in catalog.values()
        if ((not _source_authority_matches(fr.final_url, route) and not companion) or
                _future_scheduled_source(fr.final_url, route, when[:10])):
            outcome = "page_not_relevant"
            continue
        if companion and not _page_has_visa_topic(fr.content_text):
            continue
        payload = {
            "reviewed_evidence": reviewed_fields.get('disposition') if
                (reviewed_fields.get('disposition') or {}).get('source_url') == fr.final_url else None,
            "policy_date": when[:10],
            "route": {k: route.get(k) for k in
                      ("passport_nationality", "destination_country",
                       "travel_purpose", "travel_document_type")},
            "flag_from_reader": {"field": issue.field, "note": issue.note},
            "stored_answer": _comparison_facts(guidance),
            "official_page_url": fr.final_url,
            "official_page_text": fr.content_text[:MAX_PAGE_CHARS],
        }
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            raw = _call(_SYSTEM, json.dumps(payload, ensure_ascii=False), timeout_seconds=remaining)
        except Exception as e:  # noqa: BLE001 - an outcome, not a crash
            outcome = "provider_error"
            issue.proposal = {"outcome": outcome, "checked_at": when,
                              "provider_diagnostic": _provider_diagnostic(e)}
            db.commit()
            return issue.proposal
        schema_errors = _comparison_schema_errors(raw)
        if schema_errors:
            comparison_rejections.append({'source_url': fr.final_url,
                'validation_errors': schema_errors})
            continue
        quoted, evidence, unquoted = _quoted_proposals(raw, fr.content_text, route)
        empty_workflow = _empty_workflow_proposal_errors(raw)
        empty_fees = _empty_official_fee_proposal_errors(raw)
        equivalent = _equivalent_workflow_aliases(guidance, quoted, evidence)
        if empty_workflow or empty_fees or equivalent:
            workflow_rejections.append({'source_url': fr.final_url,
                'empty_fields': {k: {'value': (raw.get('corrected_fields') or {}).get(k), 'quote': evidence.get(k)} for k in empty_workflow},
                'empty_official_fee_fields': {k: {'value': (raw.get('corrected_fields') or {}).get(k), 'quote': evidence.get(k)} for k in empty_fees},
                'equivalent_legacy_fields': equivalent})
        quoted = {k:v for k,v in quoted.items() if k not in equivalent}
        workflow_candidate = dict(guidance, **({"disposition": quoted["disposition"]}
            if "disposition" in quoted else {}))
        rejected = {k: {'value': quoted.get(k, guidance.get(k)), 'quote': q}
            for k, q in evidence.items() if not proof_helpers.field_workflow_matches(
                k, q, workflow_candidate, route)}
        if rejected:
            workflow_rejections.append({'source_url': fr.final_url, 'fields': rejected})
        quoted = {k:v for k,v in quoted.items() if k not in rejected}
        candidates.append((fr,raw,quoted,evidence,unquoted))
    fresh_sources = proof_helpers.source_map(captures,catalog)
    stay_rejections, scoped_candidates = [], []
    for fr, raw, quoted, evidence, unquoted in candidates:
        rejected = reviewed_stay_concepts.rejected_stay_fields(quoted, evidence,
            fr.final_url, captures, guidance, reviewed_fields, route, when[:10])
        if rejected:
            stay_rejections.append({'source_url': fr.final_url, 'fields': rejected})
            quoted = {k: v for k, v in quoted.items() if k not in rejected}
            evidence = {k: v for k, v in evidence.items() if k not in rejected}
        scoped_candidates.append((fr, raw, quoted, evidence, unquoted))
    candidates = scoped_candidates
    applicable, route_results, deferred = [], [], []
    for fr,raw,quoted,evidence,unquoted in candidates:
        source=captures[fr.final_url]
        verdict=quoted.get('disposition',guidance.get('disposition'))
        result=proof_helpers.structured_route_result(raw,reviewed_fields.get('disposition'),source,
            fresh_sources,route,verdict,dict(guidance,**quoted),when[:10])
        has_contract=proof_helpers.has_structured_contract(raw,reviewed_fields.get('disposition'),fr.final_url)
        if result or (not has_contract and _supports_route(source['text'],raw,route,guidance,quoted,fr.final_url,when[:10])):
            result=result or {'ok':True,'quote':route_supporting_excerpt(source['text'],verdict,route,policy_date=when[:10])}
            route_results.append(result)
            applicable.append((fr,raw,quoted,evidence,unquoted,None))
        else:
            deferred.append((fr,raw,quoted,evidence,unquoted))
    for fr,raw,quoted,evidence,unquoted in deferred:
        allowed=proof_helpers.ancillary_fields(captures[fr.final_url],raw,guidance,route,route_results)
        if allowed:
            applicable.append((fr,raw,{k:v for k,v in quoted.items() if k in allowed},evidence,unquoted,allowed))
    if candidates and not applicable:
        outcome='page_not_relevant'
    effective_candidate=dict(guidance)
    for _,_,changes,_,_,_ in applicable:
        effective_candidate.update(changes)
    for fr,raw,quoted,evidence,unquoted,allowed in applicable:
        quoted={k:v for k,v in quoted.items() if proof_helpers.field_program_matches(k,evidence.get(k),effective_candidate)}
        if not proof_helpers.field_program_matches(requested,evidence.get(requested),effective_candidate):
            continue
        if proof_helpers.needs_program_scope(effective_candidate):
            program_fields=proof_helpers.ancillary_fields(captures[fr.final_url],raw,effective_candidate,route,route_results)
            quoted={k:v for k,v in quoted.items() if k not in {'government_fee','processing_time'} or k in program_fields}
            if requested in {'government_fee','processing_time'} and requested not in program_fields:
                continue
        awaiting_adjudication = {k: {"value": v, "quote": evidence[k]}
                                for k, v in quoted.items() if k in unresolved_fields}
        fields = {k: {"page_says": v, "record_holds": guidance.get(k),
                      "quote": evidence[k]} for k, v in quoted.items() if k not in unresolved_fields}
        confirmed = (requested not in unresolved_fields and (allowed is None or requested in allowed) and (requested == "disposition" or
                     isinstance(evidence.get(requested), str)
                     and quote_in_text(evidence[requested], fr.content_text)
                     and field_value_supported(requested, guidance.get(requested), evidence[requested])
                     and proof_helpers.field_scope_matches_route(requested,evidence[requested],route)
                     and proof_helpers.field_workflow_matches(requested,evidence[requested],effective_candidate,route,confirmation=True)))
        captured_text = str(fr.content_text or "")[:200000]
        proposal = {"outcome": "checked", "source_url": fr.final_url,
                    "checked_at": when,
                    # guard-20260912 T9: the page text the quotes were read
                    # from, so accepting the proposal re-validates every quote
                    # against the same capture instead of trusting it.
                    "captured_page": {"source_url": fr.final_url, "chars": len(captured_text),
                                      "sha256": hashlib.sha256(captured_text.encode("utf-8")).hexdigest(),
                                      "text": captured_text},
                    "consistent": confirmed and raw.get("consistent") is True and not fields and not unquoted,
                    "verified_fields": [requested] if confirmed else [],
                    "awaiting_adjudication": awaiting_adjudication,
                    "unquoted_fields": unquoted, "fields": fields,
                    "note": str(raw.get("note") or "")[:200],
                    "proposed_by": "ellis-ai"}
        if fields or confirmed or requested in awaiting_adjudication:
            break  # a proposal cites this exact source; no mixed-source claims
    if comparison_rejections:
        if not proposal or not (proposal.get('fields') or proposal.get('verified_fields')
                                or proposal.get('awaiting_adjudication')):
            # An invalid extraction must not overwrite a reader's existing
            # source evidence or masquerade as a checked/irrelevant page.
            return {'outcome': 'validation_error', 'checked_at': when, 'consistent': False,
                    'verified_fields': [], 'fields': {}, 'issue_unchanged': True,
                    'rejected_comparisons': comparison_rejections}
        proposal['rejected_comparisons'] = comparison_rejections
    if workflow_rejections:
        if not proposal or not (proposal.get('fields') or proposal.get('verified_fields')
                                or proposal.get('awaiting_adjudication')):
            proposal = {'outcome': 'validation_error', 'checked_at': when, 'consistent': False,
                        'verified_fields': [], 'fields': {}}
        proposal['rejected_workflow_fields'] = workflow_rejections
    if stay_rejections:
        if not proposal or not (proposal.get('fields') or proposal.get('verified_fields')
                                or proposal.get('awaiting_adjudication')):
            # An invalid comparison is not an adjudication. Preserve the
            # original issue and proposal rather than overwriting its history.
            return {'outcome': 'validation_error', 'checked_at': when, 'consistent': False,
                    'verified_fields': [], 'fields': {}, 'issue_unchanged': True,
                    'rejected_stay_concept_fields': stay_rejections}
        proposal['rejected_stay_concept_fields'] = stay_rejections
    issue.proposal = proposal or {"outcome": outcome, "checked_at": when}
    db.commit()
    return issue.proposal


def audit_integrity(db) -> dict:
    """Check every canonical served answer, independent of model/page access.
    Refresh one open issue per row so every sweep exposes contradictions
    without growing duplicate queue entries or modifying verified policy."""
    from . import kimi_primary, verified_overrides
    checked = violated = created = resolved = 0
    for row in db.execute(select(KimiRouteGuidanceCache)).scalars():
        if not kimi_primary.is_canonical_key(row.cache_key):
            continue
        checked += 1
        route = row.route if isinstance(row.route, dict) else {}
        if not isinstance(row.guidance, dict) or not isinstance(row.route, dict):
            failures = ["Cached route and guidance must be objects"]
        else:
            guidance, _ = verified_overrides.apply(dict(row.guidance), dict(route))
            failures = kimi_primary.serve_time_invariants(guidance)
        issues = db.execute(select(DatabaseIssueReport).where(
            DatabaseIssueReport.cache_key == row.cache_key,
            DatabaseIssueReport.reported_by == "freshness_monitor",
            DatabaseIssueReport.field == "integrity",
            DatabaseIssueReport.status.in_(("open", "acknowledged")))).scalars().all()
        if not failures:
            for issue in issues:
                prior = issue.proposal if isinstance(issue.proposal, dict) else {}
                if prior.get("outcome") != "integrity_failed":
                    continue  # close only findings this deterministic audit owns
                now = _now()
                issue.status, issue.resolved_by, issue.resolved_at = "corrected", "freshness_monitor", now
                issue.resolution = "The current merged route passes the deterministic integrity checks. Source accuracy and other disputes retain their separate status."
                issue.proposal = {**prior, "resolution_check": {"outcome": "integrity_passed",
                    "checked_at": now.isoformat(), "contradictions": []}}
                resolved += 1
            continue
        violated += 1
        issue = issues[0] if issues else None
        note = ("Deterministic integrity check: " + "; ".join(failures))[:1000]
        proposal = {"outcome": "integrity_failed", "checked_at": _now().isoformat(),
                    "contradictions": failures, "fields": {}}
        if issue is None:
            created += 1
            db.add(DatabaseIssueReport(org_id="platform", cache_key=row.cache_key,
                                       route=route, field="integrity",
                                       note=note, reported_by="freshness_monitor",
                                       status="open", proposal=proposal))
        else:
            issue.note, issue.proposal = note, proposal
    db.commit()
    return {"checked": checked, "violated": violated, "created": created, "resolved": resolved}


_DISPUTES = threading.local()


@contextlib.contextmanager
def disputed_fields_snapshot(db):
    """Answer active_disputed_fields for every route from one query.

    A whole-inventory read (the QC record build, the freshness listing) asked
    the issue table once per cached answer, twice for the record build. Inside
    this block the open monitor findings are read once at entry and grouped by
    canonical key; the answer for each route is the same as the per-route
    query would give at that moment.

    Findings are grouped by the key they were STORED under, because the
    per-route query compares the stored key to the route's canonical key. A
    finding filed against a residence, dated or via: variant never answered
    for the canonical route, and it must not start to inside this block."""
    if getattr(_DISPUTES, "by_key", None) is not None:
        yield
        return
    by_key: dict[str, list] = {}
    for issue in db.execute(select(DatabaseIssueReport).where(
            DatabaseIssueReport.reported_by == "freshness_monitor",
            DatabaseIssueReport.status.in_(("open", "acknowledged")),
            DatabaseIssueReport.field != "source_unreadable")).scalars():
        by_key.setdefault(issue.cache_key or "", []).append(issue)
    _DISPUTES.by_key = by_key
    try:
        yield
    finally:
        _DISPUTES.by_key = None


def active_disputed_fields(db, cache_key: str) -> list[str]:
    """Material, unresolved monitor findings survive a newer grounding stamp.
    A transient source outage is not a dispute of previously checked facts.
    All other open/acknowledged monitor issues require explicit resolution."""
    from . import kimi_primary
    key = kimi_primary.canonical_key(cache_key)
    snapshot = getattr(_DISPUTES, "by_key", None)
    if snapshot is not None:
        issues = snapshot.get(key, ())
    else:
        issues = db.execute(select(DatabaseIssueReport).where(
            DatabaseIssueReport.cache_key == key,
            DatabaseIssueReport.reported_by == "freshness_monitor",
            DatabaseIssueReport.status.in_(("open", "acknowledged")),
            DatabaseIssueReport.field != "source_unreadable")).scalars()
    fields = set()
    for issue in issues:
        proposed = (issue.proposal or {}).get("fields") or {}
        if isinstance(proposed, dict) and proposed:
            fields.update(proposed)
        else:
            fields.update(f.strip() for f in str(issue.field or "source_dispute").split(",") if f.strip())
    return sorted(fields)



def sweep_status_path() -> Path:
    return Path(os.environ.get("ELLIS_FRESHNESS_STATUS_FILE", "/var/lib/ellis/freshness-sweep-status.json"))


def read_sweep_status() -> dict:
    """Last durable sweep progress, never an invented scheduled/verified time."""
    try:
        result = json.loads(sweep_status_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return result if isinstance(result, dict) else {}
