"""Kimi-primary route guidance: the authoritative SINGLE-PASS route decision.

For a tourist route, ONE structured Kimi request sends the applicant's route
facts and requires one structured JSON answer covering the full route picture
(disposition, category, stay, passport-validity requirement, documents, forms,
channel/portal, fees, processing time, arrival card, health/vaccination
conditions, biometrics/interview/appointment, account/payment/submission
steps, exceptions, uncertainty). That Kimi result drives the Ellis workflow
directly — there is no second model pass; every check on top of it is the
deterministic validation in this module.

NO official-source fetching, Browserbase research, or evidence validation runs
on this path, and none is started asynchronously — the research pipeline
remains a separate developer/administrator tool only.

TIME LIMIT: the whole analysis runs under ONE hard wall-clock deadline
(default 60 seconds, ELLIS_GUIDANCE_DEADLINE_SECONDS). Every Kimi call gets a
bounded timeout sized to the remaining budget, one controlled retry happens
only for a malformed response and only when budget remains, and an exceeded
deadline surfaces the honest retry message — never an indefinite spinner and
never a broad-crawling fallback. Cached identical routes return immediately;
only complete (KIMI_PRIMARY) results are cached, so a failed attempt never
poisons the cache.

Deterministic validation stays deterministic: JSON/schema whitelisting,
mandatory-field checks, impossible-date and age arithmetic, passport-expiry
calculations, and internal-contradiction checks all happen in code, never in
the model.

Security: the prompt contains ONLY route facts the applicant typed or
confirmed (nationality, residence, destination, purpose, dates, transit, age,
prior refusals, passport issue/expiry dates). No passport image, no name, no
passport number, no document bytes, and no secret ever reaches the model from
this module, and Kimi's answer is data — it can name steps but cannot execute
anything. Guidance drives only reversible preparation; every real account
creation, booking, payment or submission still requires the applicant's
explicit confirmation and the runtime's fail-closed gates.
"""
from __future__ import annotations

import json
import os
import re as _re
import threading
import time
from weakref import WeakValueDictionary
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from ..config import REAL_ONLY_MODES, settings
from .models import KimiRouteGuidanceCache

STATUS_PRIMARY = "KIMI_PRIMARY"
STATUS_UNCERTAIN = "KIMI_UNCERTAIN"
STATUS_UNAVAILABLE = "KIMI_UNAVAILABLE"
STATUS_TIMEOUT = "KIMI_TIMEOUT"

# Pages that describe a pre-travel authorisation or a border formality, none
# of which is a visa. Matched against the URL and the category the answer
# cites, so a route graded VISA_REQUIRED on one of them is caught.
_AUTHORIZATION_NOT_VISA = ("etias", "esta", "/eta", "eta_", "k-eta", "keta",
                           "entry/exit system", "entry-exit-system", "ees_",
                           "electronic travel authoris", "electronic travel authoriz",
                           "electronic system for travel author")

# VISA_ON_ARRIVAL is a verdict the overrides, the records and the assistant
# already speak (52 shipped overrides carry it). Leaving it out of the
# engine's vocabulary made every invariant below skip those routes.
DISPOSITIONS = ("VISA_REQUIRED", "VISA_EXEMPT", "VISA_ON_ARRIVAL",
                "ELECTRONIC_AUTHORIZATION_REQUIRED", "CONDITIONAL")

# The requirement subcategories each verdict may carry. Shared with the
# override layer so both hands agree on what contradicts what.
DETAIL_FAMILY = {
    "VISA_EXEMPT": ("unconditional_visa_free", "conditional_visa_free",
                    "transit_visa_free"),
    "VISA_ON_ARRIVAL": ("evisa_on_arrival", "paper_visa_on_arrival"),
    "ELECTRONIC_AUTHORIZATION_REQUIRED": ("eta_electronic_authorization",),
    "VISA_REQUIRED": ("evisa", "paper_visa"),
}
_EXEMPT_DETAILS = DETAIL_FAMILY["VISA_EXEMPT"]
_FILING_CHANNELS = ("embassy_or_consulate", "embassy_designated_agency",
                    "visa_center", "authorised_agent", "on_arrival",
                    "embassy")


def serve_time_invariants(g: dict | None) -> list[str]:
    """Deterministic contradictions between an answer's verdict and its own
    parts, whoever wrote them. Run at generation (validate_answer) AND on the
    final merged answer at serve time, because the incident of 2026-09-09
    was a verdict from one hand (the model: visa-free) sitting over parts
    from another (a verified e-visa detail, a 25 USD fee). A served answer
    that fails these is uncertain, and uncertain answers are held."""
    if not isinstance(g, dict) or not g:
        return []
    problems: list[str] = []
    from ..passport_validity import passport_validity_rule_errors
    problems.extend(passport_validity_rule_errors(g.get("passport_validity_requirement")))
    eligibility = g.get("_permission_eligibility_issues")
    if isinstance(eligibility, list):
        problems.extend(str(value) for value in eligibility if isinstance(value, str))
    disp = str(g.get("disposition") or "").upper()
    detail = str(g.get("requirement_detail") or "").strip().lower()
    fee = g.get("government_fee") if isinstance(g.get("government_fee"), dict) else {}
    def positive(value):
        from math import isfinite
        if value in (None, ""):
            return False
        if isinstance(value, bool):
            problems.append("government_fee amount must be a nonnegative number")
            return False
        try:
            number = float(value)
        except (ValueError, TypeError):
            problems.append("government_fee amount must be a nonnegative number")
            return False
        if not isfinite(number) or number < 0:
            problems.append("government_fee amount must be a finite nonnegative number")
            return False
        return number > 0
    amount = positive(fee.get("amount"))
    raw_products = g.get("visa_products") or []
    if not isinstance(raw_products, list):
        problems.append("visa_products must be a list")
        raw_products = []
    if any(not isinstance(p, dict) for p in raw_products):
        problems.append("visa_products must contain only objects")
    products = [p for p in raw_products if isinstance(p, dict)]
    priced = [p for p in products if isinstance(p.get("fee"), dict)
              and positive(p["fee"].get("amount"))]
    if disp not in DISPOSITIONS:
        problems.append("disposition is missing or outside the supported vocabulary")
    if detail and detail not in REQUIREMENT_DETAILS:
        problems.append(f"unknown requirement_detail: {detail}")
    family = DETAIL_FAMILY.get(disp)
    if detail and family and detail not in family:
        problems.append(f"disposition {disp} but requirement_detail is {detail}")
    channel = str(g.get("application_channel") or "").strip().lower()
    array_fields = ("forms", "required_documents", "exceptions", "account_registration_steps",
                    "payment_process", "submission_process", "health_requirements")
    for field in array_fields:
        value = g.get(field)
        if value is not None and not isinstance(value, (list, tuple)):
            problems.append(f"{field} must be a list")
    forms = [str(f).lower() for f in (g.get("forms") or [])] if isinstance(g.get("forms"), (list, tuple)) else []
    if g.get("scheduled_policy_conflict"):
        problems.append("scheduled_policy conflicts with protected verified fields")
    if g.get("policy_interval_conflict"):
        problems.append("policy interval is invalid or does not cover the selected travel date")
    if disp == "VISA_EXEMPT":
        if amount:
            problems.append("disposition VISA_EXEMPT but a positive government fee is quoted")
        if priced:
            problems.append("disposition VISA_EXEMPT but priced visa products are listed")
        if detail and detail not in _EXEMPT_DETAILS:
            problems.append(f"disposition VISA_EXEMPT but requirement_detail is {detail}")
        if channel in _FILING_CHANNELS + ("online_portal", "authorized_agent", "visa_application_centre", "visa_application_center"):
            problems.append(f"disposition VISA_EXEMPT but application_channel is {channel}")
        if any("visa application" in f for f in forms):
            problems.append("disposition VISA_EXEMPT but forms include a visa application")
    elif disp in ("VISA_REQUIRED", "VISA_ON_ARRIVAL",
                  "ELECTRONIC_AUTHORIZATION_REQUIRED"):
        if detail in _EXEMPT_DETAILS:
            problems.append(f"disposition {disp} but requirement_detail is {detail}")
        if channel in ("not_required", "none", "no_application_required", "none_or_port_of_entry"):
            problems.append(f"disposition {disp} but application_channel is {channel}")
        if disp == "VISA_REQUIRED" and detail == "eta_electronic_authorization":
            problems.append("disposition VISA_REQUIRED but requirement_detail is an "
                            "electronic travel authorisation, which is not a visa")
        if disp == "ELECTRONIC_AUTHORIZATION_REQUIRED" and detail in ("evisa", "paper_visa"):
            problems.append("disposition ELECTRONIC_AUTHORIZATION_REQUIRED but "
                            f"requirement_detail is {detail}, which is a visa")
        if disp == "VISA_ON_ARRIVAL" and detail in ("evisa", "paper_visa"):
            problems.append(f"disposition VISA_ON_ARRIVAL but requirement_detail is {detail}")
    if disp == "VISA_REQUIRED":
        cited = " ".join(str(g.get(k) or "") for k in
                         ("source_url", "official_portal_url", "visa_category")).lower()
        if any(t in cited for t in _AUTHORIZATION_NOT_VISA):
            problems.append("disposition VISA_REQUIRED but the cited page is a travel "
                            "authorisation or border-formality page, which is not a visa")
    workflow = str(g.get("route_workflow_type") or "")
    if disp == "VISA_EXEMPT" and workflow and workflow not in ("visa_exempt_preparation", "conditional"):
        problems.append("disposition VISA_EXEMPT but route_workflow_type requires a visa application")
    if disp in ("VISA_REQUIRED", "VISA_ON_ARRIVAL", "ELECTRONIC_AUTHORIZATION_REQUIRED"):
        if str(g.get("processing_time") or "").strip().lower() == "not applicable (no visa)":
            problems.append(f"disposition {disp} but processing_time says no visa")
    return list(dict.fromkeys(problems))

# Route-specific workflow types the journey renders from. Derived
# deterministically from disposition/channel when Kimi omits it.
WORKFLOW_TYPES = ("visa_exempt_preparation", "evisa_portal", "embassy_submission",
                  "visa_center_submission", "electronic_authorization",
                  "visa_on_arrival", "conditional")

# Fields Kimi must answer for the guidance to count as complete.
MANDATORY_FIELDS = ("disposition", "visa_category", "permitted_stay",
                    "passport_validity", "required_documents",
                    "application_channel", "government_fee", "processing_time")

# All fields the structured answer may carry (whitelist — anything else is dropped).
ALL_FIELDS = MANDATORY_FIELDS + (
    "forms", "official_portal_url", "photo_requirements",
    "biometrics_required", "interview_required", "appointment_required",
    "account_registration_steps", "payment_process", "submission_process",
    "onward_travel_evidence", "accommodation_evidence", "financial_evidence",
    "insurance_required", "exceptions", "uncertainty", "confidence",
    # Structured additions:
    "permitted_stay_days",            # integer for deterministic duration checks
    "passport_validity_requirement",  # {kind, months} — deterministic comparison
    "arrival_card",                   # {required, name, submission_window}
    "health_requirements",            # [{name, applicability, trigger_countries, trigger, question}]
    "route_workflow_type",            # one of WORKFLOW_TYPES
    # Trip.com feedback (2026-08): a route offers MORE than one product, and
    # the honest channel matters. visa_products lists every option for the
    # purpose with its own entry/validity/stay/fee; application_channel_detail
    # says plainly whether individuals may apply directly or must use an
    # authorised agent; source_url backs the answer for their traceability.
    "visa_products",                  # [{type, entry, validity, max_stay_days, fee:{amount,currency}, notes}]
    "application_channel_detail",     # honest sentence: who may lodge, and how
    "source_url",                     # the official page the facts come from
    "requirement_detail",             # the field-spec subcategory (see REQUIREMENT_DETAILS)
    "transit_requirement",            # {required, note} for the stated transit points
)

# Trip.com's field spec asks the requirement to be reported at TWO levels: the
# primary classification (our disposition) and a subcategory. This is that
# subcategory vocabulary, verbatim from their spec.
# The channel vocabulary the UI knows how to label. Anything else is dropped
# rather than rendered raw.
APPLICATION_CHANNELS = ("online_portal", "embassy", "visa_center",
                        "authorised_agent", "authorized_agent",
                        "on_arrival", "not_required")

REQUIREMENT_DETAILS = (
    "unconditional_visa_free", "conditional_visa_free", "transit_visa_free",
    "evisa_on_arrival", "paper_visa_on_arrival",
    "evisa", "paper_visa", "eta_electronic_authorization",
)

# The user-facing decision label (replaces every "checked against official
# sources" or second-pass claim for guidance-driven flows). Exactly one Kimi
# pass produces the decision — the label must never claim more.
VERIFIED_LABEL = "Kimi route decision"

# The honest hard-deadline message (shown instead of an endless spinner).
TIMEOUT_MESSAGE = ("Ellis could not finish working out this route in time. "
                   "Please try again.")

# Total wall-clock budget for the analysis + its single malformed-retry.
DEFAULT_DEADLINE_SECONDS = 90  # richer schema (visa_products) needs more room
# Never start a Kimi call with less than this much budget left.
MIN_CALL_BUDGET_SECONDS = 5
# Output cap bounds Kimi latency inside the deadline. Kimi is a REASONING
# model: completion_tokens includes its hidden reasoning (observed ~10-13k
# characters before the ~1.5k-token JSON answer), so a small cap truncates the
# JSON mid-object (finish_reason "length") and the route comes back uncertain.
# 12000 leaves ample room for reasoning + the structured answer; the wall-clock
# deadline still governs overall latency.
PASS1_MAX_TOKENS = 12000

# Cache-schema version: bumping invalidates two-pass-era rows so a cached
# route always carries the honest single-pass label and verification shape.
CACHE_VERSION = "v6"  # + authorised_agent channel, purpose-driven category, stay notes

# Default freshness window; stale entries are reused instantly and refreshed in
# the background (never blocking the applicant).
TTL_DAYS = 14
# An incomplete (KIMI_UNCERTAIN) answer is ALSO cached, briefly: the reader
# gets an instant answer on repeat instead of a fresh 30-second model pass
# every time, and the background refresh keeps trying for a complete one.
UNCERTAIN_TTL_DAYS = 2


def hold_enabled() -> bool:
    """Whether a low-confidence answer is withheld from readers until a person
    confirms it. Trip.com's acceptance standard makes this mandatory ("block
    low-confidence until confirmed"), so it is ON by default — the reader
    still gets a response (an honest "being verified" card, never a blank
    refusal), operations sees the full answer in the quality backend, and
    approval releases it. Set ELLIS_DATABASE_HOLD_LOW_CONFIDENCE=0 to serve
    low-confidence answers directly."""
    return os.getenv("ELLIS_DATABASE_HOLD_LOW_CONFIDENCE", "1").strip() == "1"


# Two-STAGE answering (the fast path for a route nobody has asked before):
# stage 1 asks for the CORE verdict only (what the reader needs first) and is
# served the moment it lands; stage 2 fills the DETAIL (products, steps,
# health, evidence) in the background, told the verdict it must respect, and
# the reader's next poll picks it up. One consistent answer, first paint in a
# fraction of the time.
CORE_FIELDS = (
    "disposition", "requirement_detail", "visa_category", "permitted_stay",
    "permitted_stay_days", "passport_validity", "passport_validity_requirement",
    "application_channel", "application_channel_detail", "official_portal_url",
    "government_fee", "processing_time", "required_documents",
    "biometrics_required", "interview_required", "appointment_required",
    "insurance_required", "route_workflow_type", "source_url",
    "transit_requirement", "confidence", "uncertainty",
)
DETAIL_FIELDS = (
    "visa_products", "forms", "account_registration_steps", "payment_process",
    "submission_process", "exceptions", "photo_requirements",
    "onward_travel_evidence", "accommodation_evidence", "financial_evidence",
    "arrival_card", "health_requirements",
)

_SCHEMA_SPEC = """Reply STRICT JSON with these fields (omit nothing; use null
when genuinely unknown and add an entry to "uncertainty" naming the field and why):
disposition: one of VISA_REQUIRED | VISA_EXEMPT | ELECTRONIC_AUTHORIZATION_REQUIRED | CONDITIONAL
visa_category, permitted_stay, passport_validity, processing_time: short strings
permitted_stay_days: integer number of days of permitted stay, or null
passport_validity_requirement: null when the exact entry rule is unknown; otherwise {"kind": "valid_on_arrival"|"valid_through_departure"|"months_after_arrival"|"months_after_departure", "months": integer|null}. Never use a null kind or invent another kind. For month-based kinds, months must be a positive integer; for the two validity-only kinds use months: 0 or null. Do not turn visa-application passport requirements into border-entry rules.
required_documents, forms, account_registration_steps, payment_process,
submission_process, exceptions: arrays of short strings
application_channel: online_portal | embassy | visa_center | authorised_agent | on_arrival | not_required — use authorised_agent when individuals may NOT file directly and a designated agency must lodge for them (e.g. Chinese nationals applying for Japan); never call that a visa_center
official_portal_url: the official GOVERNMENT portal URL or null (NEVER invent one) — for THIS destination and visa type, on a government domain; contractor or commercial sites (VFS, BLS, "visa service" sites) are never accepted here
government_fee: {"amount": number|null, "currency": string|null} — the OFFICIAL consular fee only; if a service/agency fee also applies say so in application_channel_detail, never fold it in
visa_products: array of EVERY visa product available for this nationality + destination + purpose — each {"type": e.g. "Single-entry tourist"|"3-year multiple"|"5-year multiple"|"B1/B2", "entry": "single"|"multiple"|null, "validity": short string, "max_stay_days": integer|null, "fee": {"amount": number|null, "currency": string|null}, "notes": short string|null}; list them ALL; when only one product exists, still list that one, never an empty array for a route that needs a visa
application_channel_detail: one honest sentence naming WHO may lodge and HOW — e.g. "Individuals cannot apply directly; the application must go through a designated authorised agent" or "Apply yourself on the official portal" — never claim a walk-in visa centre where the destination refuses individual filings
source_url: the single official government page these facts come from, or null (NEVER invent one)
requirement_detail: the precise subcategory, one of unconditional_visa_free | conditional_visa_free | transit_visa_free | evisa_on_arrival | paper_visa_on_arrival | evisa | paper_visa | eta_electronic_authorization — pick the one matching disposition (visa-free splits into unconditional/conditional/transit; on-arrival into electronic/paper; advance into eVisa/paper/ETA), or null if genuinely none fits
transit_requirement: {"required": true|false|null, "note": short string|null} — answer ONLY for the transit points named in the route facts; if none were named use {"required": null, "note": null}, never invent a transit
photo_requirements, onward_travel_evidence, accommodation_evidence,
financial_evidence: short strings or null
biometrics_required, interview_required, appointment_required,
insurance_required: true|false|null
arrival_card: {"required": true|false|null, "name": string|null, "submission_window": string|null}
health_requirements: array of {"name": string, "applicability": "always_required"|"conditional"|"not_applicable", "trigger_countries": [ISO3...], "trigger": string|null, "question": string|null} — put conditional vaccination/health items HERE ONLY, never in required_documents; applicability is for THIS applicant's stated route (origin, residence, transit); use "conditional" only when a fact you were not given (e.g. recent travel history) decides it
route_workflow_type: visa_exempt_preparation | evisa_portal | embassy_submission | visa_center_submission | electronic_authorization | visa_on_arrival | conditional
uncertainty: array of {"field":..., "reason":...} for anything not certain
confidence: high | medium | low
Rules: never guess a URL or a fee; unknown means null + uncertainty entry;
missing information is NEVER visa-exempt; answer for THIS nationality only;
answer for THIS travel document type: a diplomatic or service/official
passport often has its own bilateral exemption agreement that ordinary
passports do not — when one is stated, answer from those agreements, never
from the ordinary-passport rule;
list ALL visa products for the purpose, each with its OWN stay and fee (never a
single generic "90 days" when products differ); name the real application
channel honestly; when the destination offers facilitation policies relevant to
tourists (e.g. simplified rules for accompanying family / secondary applicants,
asset-proof waivers, frequent-traveller lanes) note them in exceptions; if a
transit is implied, state transit-visa need in exceptions; keep every string
short — no prose."""

_SYSTEM = ("""You are a visa-requirements engine. For the EXACT route in the user
message (passport nationality, issuing country, travel-document type, lawful
residence, destination, the stated travel purpose, dates, trip duration, transit
countries, age, prior refusals, passport issue/expiration dates), answer from
your knowledge of official visa policy.
The facts include today's date. Visa policy CHANGES: exemptions are introduced
and withdrawn, fees are revised, channels move. If your knowledge of this
route could predate a change - the rule is volatile, recently announced, or
politically driven - say so in uncertainty and rate confidence low rather
than presenting a possibly outdated rule as current.

A TRAVEL AUTHORISATION IS NOT A VISA, and a border formality is not a visa
either. ETIAS (Europe), ESTA (United States), eTA (Canada), K-ETA (Korea),
ETA (United Kingdom, Australia, New Zealand) and the EU Entry/Exit System
(EES) are none of them visas. A traveller who is visa-exempt for a
destination stays VISA_EXEMPT when one of these applies; use
ELECTRONIC_AUTHORIZATION_REQUIRED only when the authorisation is actually
in force and actually required of this nationality today, and never
VISA_REQUIRED. EES is a biometric registration performed at the border and
changes no disposition at all. Specifically for the Schengen area: a
national of a country listed in Annex II of Regulation (EU) 2018/1806 is
visa-exempt for up to 90 days in any 180 for tourism, business, family
visit and transit. Citing an ETIAS or EES page is not a reason to answer
VISA_REQUIRED for such a national.
""" + _SCHEMA_SPEC)

class GuidanceUnavailable(Exception):
    """No provider (no key / wrong mode) — honest, never fabricated."""


class GuidanceTimeout(Exception):
    """The 60-second route-analysis deadline was exceeded."""

    def __init__(self, message: str = TIMEOUT_MESSAGE):
        super().__init__(message)


class GuidanceProviderError(Exception):
    """A precise, applicant-safe provider failure (401/402/429/5xx/timeout).
    Carries the provider_errors envelope — never a raw response or a secret."""

    def __init__(self, envelope: dict):
        self.envelope = envelope
        super().__init__(envelope.get("user_message", "provider error"))


# ---- provider seam (tests inject; real modes use live Kimi) ------------------
_PROVIDER = None


def set_provider(fn) -> None:
    """Inject callable(system, user)->dict for tests. None resets to live Kimi."""
    global _PROVIDER
    _PROVIDER = fn


def _deadline_seconds() -> float:
    return float(os.getenv("ELLIS_GUIDANCE_DEADLINE_SECONDS",
                           DEFAULT_DEADLINE_SECONDS) or DEFAULT_DEADLINE_SECONDS)


# Outbound model calls are BOUNDED and retried. A cold-miss route decision
# holds its connection for about nine seconds, so eight readers arriving
# together fired eight simultaneous requests, the provider rate-limited us,
# and the reader saw 503 on a route that was merely busy. Measured: 34 of 139
# concurrent lookups failed that way. Queueing behind a few slots turns a
# refusal into a wait, which is what the caller's own timeout is already for.
_LIVE_SLOTS = threading.Semaphore(
    max(1, int(os.getenv("ELLIS_KIMI_CONCURRENCY", "3") or 3)))

# Statuses worth trying again: a rate limit or a transient upstream fault.
# A 401/402/404 is a real answer and retrying it only wastes the budget.
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


# The provider rejects some place names outright (HTTP 400, a content
# filter rather than a rate limit). Taiwan is the one that matters to a
# Trip.com station: every composer and recheck call for a TWN route failed
# on 2026-09-03. A rejected call is retried once with those words replaced
# by the ISO code, which the provider accepts, and the answer is the same
# rule for the same passport.
_NEUTRAL_WORDS = (
    (_re.compile(r"\bTaiwan(?:ese)?\b", _re.I), "TWN"),
    (_re.compile(r"台灣|台湾|臺灣|中華民國|中华民国"), "TWN"),
)


def _neutralized(text: str) -> str:
    out = str(text or "")
    for rx, rep in _NEUTRAL_WORDS:
        out = rx.sub(rep, out)
    return out


_CODE_KEYS = ("nationality", "destination", "passport", "country", "iso",
              "transit_countries", "applies_to", "route")


def _restored(value, key: str = ""):
    """Put the place name back into text the model wrote after a
    neutralised retry: "TWN passport holders" reads as "Taiwan passport
    holders" again. ISO-coded fields keep their codes."""
    if isinstance(value, dict):
        return {k: _restored(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_restored(v, key) for v in value]
    if isinstance(value, str) and not any(c in key.lower() for c in _CODE_KEYS):
        return _re.sub(r"\bTWN\b(?=\s+(?:passport|nationals?|citizens?|residents?|travell?ers?|region|authorit))",
                       "Taiwan", value)
    return value


def _live_call(system: str, user: str, *, timeout: float, max_tokens: int) -> dict:
    s = settings()
    if not (s.moonshot_api_key and s.kimi_enabled):
        raise GuidanceUnavailable("Kimi K3 not configured — guidance unavailable")
    from ..providers.kimi import KimiHttpError, KimiTimeout, LiveKimiProvider
    provider = LiveKimiProvider()
    # Optional operator override for the guidance model only (falls back to
    # KIMI_MODEL). Lets a deployment pick a faster Kimi tier for the bounded
    # route decision without touching the rest of the system.
    model = os.getenv("KIMI_GUIDANCE_MODEL", "").strip() or None
    deadline = time.monotonic() + max(0.0, timeout)
    neutralised = False
    with _LIVE_SLOTS:
        for attempt in range(3):
            left = deadline - time.monotonic()
            if left <= 0:
                raise GuidanceTimeout()
            try:
                out = provider._chat(system, user, json_mode=True, timeout=left,
                                     max_tokens=max_tokens, model=model)
                return _restored(out) if neutralised else out
            except KimiTimeout as e:
                raise GuidanceTimeout() from e
            except KimiHttpError as e:
                if e.status == 400 and attempt == 0 and (
                        _neutralized(user) != user or _neutralized(system) != system):
                    # A content filter, not a budget problem: one retry with
                    # the place words replaced by the ISO code, and the name
                    # put back into whatever text comes out.
                    system, user = _neutralized(system), _neutralized(user)
                    neutralised = True
                    continue
                # Back off only while the caller's own budget can still pay
                # for it. Past that the honest answer is the failure, not a
                # longer spinner.
                pause = 0.75 * (2 ** attempt)
                if (e.status not in _RETRY_STATUS or attempt == 2
                        or deadline - time.monotonic() <= pause + 2.0):
                    from .. import provider_errors
                    raise GuidanceProviderError(provider_errors.user_error(
                        f"kimi moonshot HTTP {e.status}")) from e
                time.sleep(pause)
    raise GuidanceTimeout()


def _call(system: str, user: str, *, timeout: float, max_tokens: int) -> dict:
    if _PROVIDER is not None:
        return _PROVIDER(system, user)
    if settings().runtime_mode not in REAL_ONLY_MODES + ("test", "local_mock_demo"):
        raise GuidanceUnavailable("guidance disabled in this runtime mode")
    return _live_call(system, user, timeout=timeout, max_tokens=max_tokens)


def is_available() -> bool:
    if _PROVIDER is not None:
        return True
    s = settings()
    return bool(s.moonshot_api_key and s.kimi_enabled)


# ---- prompt / validation -----------------------------------------------------
ROUTE_FACT_KEYS = (
    # Sanitized route facts ONLY — no name, no passport number, no images.
    "passport_nationality", "passport_issuing_country", "travel_document_type",
    "lawful_country_of_residence", "destination_country", "visa_category",
    "travel_purpose", "arrival_date", "departure_date", "transit_countries",
    "departure_city",
    "age", "prior_refusals", "existing_destination_visas",
    "existing_residence_permits", "recent_travel_countries",
    "passport_issue_date", "passport_expiry_date",
)


# The purpose the reader picked decides which visa product family the answer
# is about. Sending "tourist_visa" for a Study lookup asked the wrong question.
CATEGORY_FOR_PURPOSE = {
    "tourism": "tourist_visa", "business": "business_visa",
    "family_visit": "visitor_visa", "study": "student_visa",
    "work": "work_visa", "transit": "transit_visa", "other": "visitor_visa",
}


def category_for_purpose(purpose: str) -> str:
    return CATEGORY_FOR_PURPOSE.get(str(purpose or "").strip().lower(),
                                    "tourist_visa")


def route_facts(route: dict) -> dict:
    """The sanitized fact set sent to Kimi (whitelist — nothing else leaves)."""
    keys = ("passport_nationality", "travel_document_type", "destination_country",
            "travel_purpose")
    facts = {k: route[k] for k in keys if route.get(k) not in (None, "", [])}
    facts.setdefault("travel_purpose", "tourism")
    facts["travel_document_type"] = normalize_document_type(
        facts.get("travel_document_type")) or "ordinary_passport"
    facts["visa_category"] = category_for_purpose(facts["travel_purpose"])
    facts["passport_issuing_country"] = facts.get("passport_nationality")
    facts["lawful_country_of_residence"] = facts.get("passport_nationality")
    facts["consular_jurisdiction"] = "default"
    facts["today"] = date.today().isoformat()
    return facts


def build_prompt(route: dict) -> str:
    """User prompt from ROUTE FACTS ONLY (nothing sensitive exists here)."""
    return json.dumps(route_facts(route))


def _iso(v) -> date | None:
    try:
        return date.fromisoformat(str(v)) if v else None
    except ValueError:
        return None


def validate_answer(raw: dict, *, detail_known: bool = True) -> tuple[dict, list, list]:
    """Whitelist + shape-check one answer. Returns (clean, missing, contradictions).
    Purely deterministic — schema validity, mandatory fields, and internal
    contradictions; never a model judgement."""
    clean: dict = {}
    for k in ALL_FIELDS:
        if k in (raw or {}):
            clean[k] = raw[k]
    missing = []
    exempt = str(clean.get("disposition") or "").upper() == "VISA_EXEMPT"
    for k in MANDATORY_FIELDS:
        v = clean.get(k)
        if exempt and k in ("processing_time", "government_fee", "application_channel") and v in (None, "", {}):
            # A visa-free route has no application to process or pay for;
            # demanding these marked 26 correct answers uncertain in one sweep.
            clean.setdefault("application_channel", "not_required")
            clean.setdefault("processing_time", "Not applicable (no visa)")
            clean.setdefault("government_fee", {"amount": 0, "currency": None})
            continue
        if v in (None, "", [], {}):
            missing.append(k)
        elif k == "disposition" and str(v).upper() not in DISPOSITIONS:
            missing.append(k)
        elif k == "government_fee" and not isinstance(v, dict):
            missing.append(k)
    if "disposition" in clean and isinstance(clean["disposition"], str):
        clean["disposition"] = clean["disposition"].upper()
    # Normalize the structured additions defensively (wrong shapes are dropped,
    # never trusted).
    from ..passport_validity import normalize_passport_validity_rule
    if "passport_validity_requirement" in clean:
        clean["passport_validity_requirement"] = normalize_passport_validity_rule(
            clean["passport_validity_requirement"])
    if clean.get("passport_validity_requirement") is not None and not isinstance(clean["passport_validity_requirement"], dict):
        clean.pop("passport_validity_requirement", None)
    if not isinstance(clean.get("arrival_card"), dict):
        clean.pop("arrival_card", None)
    if not isinstance(clean.get("health_requirements"), list):
        clean.pop("health_requirements", None)
    else:
        clean["health_requirements"] = [h for h in clean["health_requirements"]
                                        if isinstance(h, dict) and h.get("name")]
    # The Trip.com additions get the same treatment: a wrong shape is dropped,
    # never rendered. transit_requirement in particular must carry an explicit
    # `required` key — without one the UI would read a missing answer as a
    # confident "no transit visa needed".
    tr = clean.get("transit_requirement")
    if not isinstance(tr, dict) or "required" not in tr \
            or tr.get("required") not in (True, False, None):
        clean.pop("transit_requirement", None)
    if not isinstance(clean.get("visa_products"), list):
        clean.pop("visa_products", None)
    else:
        products = []
        for vp in clean["visa_products"]:
            if not isinstance(vp, dict) or not str(vp.get("type") or "").strip():
                continue
            if not isinstance(vp.get("fee"), dict):
                vp.pop("fee", None)
            d = vp.get("max_stay_days")
            if d is not None and (not isinstance(d, (int, float))
                                  or d < 0 or d > 3660):
                vp.pop("max_stay_days", None)
            products.append(vp)
        clean["visa_products"] = products
    rd = str(clean.get("requirement_detail") or "").strip().lower()
    clean["requirement_detail"] = rd if rd in REQUIREMENT_DETAILS else None
    # application_channel is an ENUM the UI renders through a fixed
    # vocabulary. It was never checked, so a grounded recheck was able to
    # write a prose sentence into it ("diplomatic mission, accredited agency,
    # Japan Visa Application Centre, or online"), which renders as raw text
    # where a label belongs. An unrecognised value is dropped, and the honest
    # sentence still lives in application_channel_detail.
    ch = str(clean.get("application_channel") or "").strip().lower().replace(" ", "_")
    if ch in APPLICATION_CHANNELS:
        clean["application_channel"] = ch
    elif "application_channel" in clean:
        clean.pop("application_channel")
    # Confidence drives the display hold, so an unexpected word must not slip
    # through as an unknown label (or as "not low").
    conf = str(clean.get("confidence") or "").strip().lower()
    clean["confidence"] = conf if conf in ("high", "medium", "low") else "low"
    for k in ("application_channel_detail", "source_url"):
        if k in clean and not isinstance(clean[k], str):
            clean.pop(k, None)
    # Links must be OFFICIAL: every URL an answer carries must sit on a
    # government domain, or it is dropped here — the model has offered
    # commercial lookalikes (korea-evisa.com) and contractor sites, and a
    # prompt rule alone does not stop it. The channel label still renders;
    # only the link goes.
    from .authority import is_government_host
    from urllib.parse import urlparse
    for k in ("official_portal_url", "source_url"):
        u = clean.get(k)
        if isinstance(u, str) and u.startswith("http"):
            if not is_government_host(urlparse(u).hostname or ""):
                clean[k] = None
    wt = str(clean.get("route_workflow_type") or "").strip().lower()
    if wt not in WORKFLOW_TYPES:
        clean["route_workflow_type"] = derive_workflow_type(clean)
    # One deterministic invariant set, the same one the serve path re-runs
    # on the final merged answer: a verdict may not contradict its own parts.
    contradictions = serve_time_invariants(clean)
    if clean.get("disposition") == "VISA_EXEMPT" and \
            clean.get("route_workflow_type") not in ("visa_exempt_preparation", "conditional"):
        contradictions.append("disposition VISA_EXEMPT but route_workflow_type "
                              f"is {clean.get('route_workflow_type')}")
    ps_days = clean.get("permitted_stay_days")
    if ps_days is not None and (not isinstance(ps_days, (int, float)) or ps_days < 0
                                or ps_days > 3660):
        clean.pop("permitted_stay_days", None)

    # A travel authorisation is not a visa. Answering VISA_REQUIRED while
    # citing an ETIAS/ESTA/eTA page is the exact error that told a Japanese
    # tourist they needed a visa for Italy: the engine read the ETIAS page,
    # which is a pre-travel authorisation for the visa-EXEMPT, and graded the
    # route as visa-required. EES is a border biometric and decides nothing.
    if clean.get("disposition") == "VISA_REQUIRED":
        cited = " ".join(str(clean.get(k) or "") for k in
                         ("source_url", "official_portal_url", "visa_category")).lower()
        if any(t in cited for t in _AUTHORIZATION_NOT_VISA):
            contradictions.append(
                "disposition VISA_REQUIRED but the cited page is a travel "
                "authorisation or border-formality page (ETIAS/ESTA/eTA/EES), "
                "which applies to visa-exempt travellers and is not a visa")

    # --- the Trip.com appendix defects, caught deterministically -------------
    # (iii) The headline channel must not contradict the honest sentence. If
    # the detail says individuals cannot file directly, calling it a visa
    # centre or an embassy counter is the exact mislabel they rejected.
    detail = str(clean.get("application_channel_detail") or "").lower()
    channel = str(clean.get("application_channel") or "").lower()
    # Only an AGENCY requirement contradicts a direct channel. "Must apply
    # through the official portal" is a direct channel and was being read as
    # a contradiction (18 false positives in one warm set).
    cannot_self_file = any(t in detail for t in (
        "cannot apply directly", "not accept individual", "does not accept direct",
        "through a designated", "through an authorised", "through an authorized",
        "accredited travel agency", "designated agency", "designated travel",
        "authorised agent", "authorized agent", "accredited agency"))
    if cannot_self_file and channel in ("visa_center", "embassy", "online_portal"):
        contradictions.append(
            f"application_channel '{channel}' but the channel detail says "
            "individuals may not file directly (use authorised_agent)")

    # (i)/(iv) A visa-required route should list its products, and a product's
    # own note must not contradict the stay printed beside it.
    products = clean.get("visa_products") or []
    if detail_known and clean.get("disposition") == "VISA_REQUIRED" and not products:
        contradictions.append("disposition VISA_REQUIRED but no visa_products "
                              "were listed for this purpose")
    for vp in products:
        if not isinstance(vp, dict):
            continue
        note = str(vp.get("notes") or "")
        d = vp.get("max_stay_days")
        if d is None or not note:
            continue
        # Only a number that describes the STAY counts ("stay of 15 days",
        # "granted 15 days", 停留15天). "Apply 45 days before travel" does not.
        nl = note.lower()
        named = [int(n) for n in _re.findall(
            r"(?:stay|granted|allowed|permit)[^.;]{0,30}?(\d{1,3})\s*days?", nl)]
        named += [int(n) for n in _re.findall(
            r"(\d{1,3})\s*days?[^.;]{0,20}?(?:stay|granted|per visit|per entry)", nl)]
        named += [int(n) for n in _re.findall(r"停留(\d{1,3})天", note)]
        if named and max(named) < d:
            contradictions.append(
                f"visa product '{vp.get('type')}' says {d} days but its note "
                f"names a shorter granted stay ({max(named)} days)")
    return clean, missing, contradictions


# ---- "Steps to apply": 3-5 key steps, in the order they happen ------------
# The engine returns three step arrays that overlap and repeat ("Pay the visa
# fee online" and "Credit/debit card payment through ImmiAccount at time of
# lodgement" are one step), sometimes dozens deep. Trip.com's spec asks for
# 3-5 KEY steps. Each step is therefore classified into the stage it belongs
# to, one line is kept per stage — the shortest clear one — and the stages are
# emitted in the order a traveller meets them.
# Biometrics come BEFORE the decisive submission: the US biometrics (OFC)
# visit precedes the interview, and a visa centre takes them as you hand the
# file in.
_STAGE_ORDER = ("account", "form", "documents", "appointment", "pay",
                "biometrics", "submit", "collect")
_STAGE_WORDS = {
    "account": ("create an account", "register an account", "create account",
                "sign up", "registration", "immiaccount", "create a profile"),
    "form": ("fill", "complete the application", "complete the form",
             "application form", "ds-160", "online form", "questionnaire"),
    # "photo" alone is too broad — it caught "Attend OFC appointment for
    # fingerprints/photo", which is a biometrics visit, not paperwork.
    "documents": ("upload", "gather", "prepare the document", "attach",
                  "supporting document", "passport photo", "photo requirement"),
    "appointment": ("appointment", "book a slot", "schedule", "interview slot"),
    "pay": ("pay", "payment", "fee online", "charge", "card"),
    "submit": ("submit", "lodge", "attend", "hand in", "deliver", "drop off",
               "in person", "apply at"),
    "biometrics": ("biometric", "fingerprint", "photograph at", "vfs biometric",
                   "ofc appointment", "ofc visit", "applicant service centre",
                   "applicant service center"),
    "collect": ("collect", "pick up", "courier", "receive the passport",
                "receive your visa", "track"),
}
_STAGE_MAX = 5


# Lines that describe a CIRCUMSTANCE rather than an action ("Payment methods
# vary by centre", "Accepted methods vary by center"). They belong in the
# notes, not in a numbered list of things to do.
_NOT_A_STEP = ("vary by", "varies by", "may differ", "differs by",
               "depends on the", "accepted methods", "methods vary",
               "where offered", "if applicable")


def _is_actionable(step: str) -> bool:
    low = step.lower().strip()
    if any(w in low for w in _NOT_A_STEP):
        return False
    return len(low.split()) >= 2


def _stage_of(step: str) -> str | None:
    low = step.lower()
    for stage in _STAGE_ORDER:
        if any(w in low for w in _STAGE_WORDS[stage]):
            return stage
    return None


def _pay_goes_early(step: str) -> bool:
    """A fee paid ONLINE or in advance happens before the appointment (the US
    MRV fee is paid before an interview can be booked). A fee paid AT the
    centre happens with the submission, so it stays after."""
    low = step.lower()
    if any(w in low for w in ("at the visa application centre", "at the centre",
                              "at the center", "on submission", "when submitting",
                              "at time of submission", "on collection")):
        return False
    return any(w in low for w in ("online", "in advance", "before", "bank",
                                 "portal", "website"))


def _tidy_step(step: str) -> str:
    """One short sentence, sentence-cased, no trailing full stop."""
    t = " ".join(str(step or "").split()).strip(" .;·-")
    if not t:
        return t
    # Cut a trailing qualifier that turns a step into a paragraph.
    for sep in (" according to ", " as required by ", " depending on ",
                " at time of ", "; ", " — ", " - "):
        if len(t) > 90 and sep in t:
            t = t.split(sep)[0].strip(" .;,-")
    if len(t) > 110:
        cut = t[:110].rsplit(" ", 1)[0]
        t = cut.rstrip(" ,;:") 
    return t[0].upper() + t[1:] if t else t


def canonical_steps(g: dict) -> list:
    """The 3-5 key steps for this answer, deduplicated and ordered.

    Deterministic: no model call. An unclassifiable step is kept only if
    there is room, so a route whose steps are all unusual still shows
    something rather than nothing."""
    raw = []
    for key in ("account_registration_steps", "payment_process",
                "submission_process"):
        for s in (g or {}).get(key) or []:
            if isinstance(s, str) and s.strip():
                raw.append(s.strip())
    if not raw:
        return []
    best: dict = {}
    extra: list = []
    for s in raw:
        t = _tidy_step(s)
        if not t or not _is_actionable(t):
            continue
        stage = _stage_of(t)
        if stage is None:
            if t not in extra:
                extra.append(t)
            continue
        # The clearest line for a stage is the shortest one that still says
        # something (very short fragments lose to a fuller sentence).
        cur = best.get(stage)
        if cur is None or (len(t) >= 18 and len(t) < len(cur)) or len(cur) < 18:
            best[stage] = t
    # A fee paid online/in advance sorts before the appointment; a fee paid at
    # the centre stays with the submission.
    order = list(_STAGE_ORDER)
    if "pay" in best and _pay_goes_early(best["pay"]):
        order.remove("pay")
        order.insert(order.index("appointment"), "pay")
    steps = [best[st] for st in order if st in best]
    for t in extra:
        if len(steps) >= _STAGE_MAX:
            break
        if t not in steps:
            steps.append(t)
    return steps[:_STAGE_MAX]


def derive_workflow_type(g: dict) -> str:
    """Deterministic fallback mapping disposition/channel -> workflow type."""
    disp = str((g or {}).get("disposition") or "").upper()
    chan = str((g or {}).get("application_channel") or "").lower()
    if disp == "VISA_EXEMPT":
        return "visa_exempt_preparation"
    if disp == "ELECTRONIC_AUTHORIZATION_REQUIRED":
        return "electronic_authorization"
    if disp == "VISA_ON_ARRIVAL":
        return "visa_on_arrival"
    if disp == "VISA_REQUIRED":
        if chan == "online_portal":
            return "evisa_portal"
        if chan in ("visa_center", "authorised_agent"):
            # An authorised agent lodges on the applicant's behalf: the
            # workflow is the same drop-off shape as a visa centre, even
            # though the label the reader sees must stay honest.
            return "visa_center_submission"
        if chan == "on_arrival":
            return "visa_on_arrival"
        return "embassy_submission"
    return "conditional"


def deterministic_advisories(route: dict, clean: dict, *, today: date | None = None) -> list[str]:
    """Applicant-facing arithmetic the model is never trusted with: impossible
    dates, trip duration vs permitted stay, passport-expiry vs the structured
    validity requirement, age sanity. Advisories, not guidance mutations."""
    from .. import dates as dates_mod
    from .. import passport_validity as pv
    today = today or date.today()
    out: list[str] = []
    arrival, departure = _iso(route.get("arrival_date")), _iso(route.get("departure_date"))
    if arrival and departure and departure <= arrival:
        out.append("departure date is not after arrival date")
    if arrival and arrival < today:
        out.append(f"arrival date {dates_mod.to_display(arrival.isoformat())} is in the past")
    days = clean.get("permitted_stay_days")
    if arrival and departure and isinstance(days, (int, float)) and days:
        trip = (departure - arrival).days
        if trip > int(days):
            out.append(f"trip duration {trip} days exceeds the permitted stay of {int(days)} days")
    age = route.get("age")
    if age is not None:
        try:
            if not (0 <= int(age) <= 130):
                out.append("age is outside a plausible range")
        except (TypeError, ValueError):
            out.append("age is not a number")
    expiry = pv.parse_expiry(str(route.get("passport_expiry_date") or ""))
    if expiry:
        if expiry < today:
            out.append(f"passport expired on {dates_mod.to_display(expiry.isoformat())}")
        else:
            req = clean.get("passport_validity_requirement") or {}
            if isinstance(req, dict) and req.get("kind"):
                need, need_text = pv.required_valid_until(req, arrival, departure)
                if need and expiry < need:
                    out.append(f"passport expires {dates_mod.to_display(expiry.isoformat())} "
                               f"but must be {need_text} — until "
                               f"{dates_mod.to_display(need.isoformat())}")
    return out


def derive_workflow_plan(g: dict) -> list[dict]:
    """Deterministic next-step plan from the guidance FIELDS (never free text).
    Route-specific: only stages that apply to this route type appear.
    Reversible preparation only; irreversible steps carry the confirmation flag."""
    steps: list[dict] = []
    disp = g.get("disposition")
    wtype = g.get("route_workflow_type") or derive_workflow_type(g)
    steps.append({"step": "collect_documents", "reversible": True,
                  "items": g.get("required_documents") or []})
    steps.append({"step": "ocr_and_validate_passport", "reversible": True})
    if disp == "VISA_EXEMPT":
        steps.append({"step": "prepare_entry_documents", "reversible": True,
                      "items": g.get("forms") or []})
        card = g.get("arrival_card") or {}
        if isinstance(card, dict) and card.get("required"):
            steps.append({"step": "arrival_card_preparation", "reversible": True,
                          "name": card.get("name"),
                          "submission_window": card.get("submission_window")})
    else:
        steps.append({"step": "prepare_forms", "reversible": True,
                      "items": g.get("forms") or []})
        if g.get("official_portal_url"):
            steps.append({"step": "generate_route_adapter", "reversible": True,
                          "portal": g.get("official_portal_url")})
            steps.append({"step": "account_registration", "reversible": False,
                          "requires_applicant_confirmation": True})
        if g.get("appointment_required"):
            steps.append({"step": "appointment_search", "reversible": True})
            steps.append({"step": "appointment_booking", "reversible": False,
                          "requires_applicant_confirmation": True})
        fee = g.get("government_fee") or {}
        if isinstance(fee, dict) and fee.get("amount"):
            steps.append({"step": "display_exact_fees", "reversible": True, "fee": fee})
            steps.append({"step": "payment", "reversible": False,
                          "requires_applicant_confirmation": True})
        steps.append({"step": "final_review_and_signature", "reversible": False,
                      "requires_applicant_confirmation": True})
        steps.append({"step": "submission", "reversible": False,
                      "requires_applicant_confirmation": True})
    steps.append({"step": "track_status", "reversible": True})
    for s in steps:
        s["workflow_type"] = wtype
    return steps


# ---- cache -------------------------------------------------------------------
def cache_key(route: dict) -> str:
    # A travel date is NOT a different policy, so it never forks the decision.
    # Bucketing by arrival month gave the same route two answers: the verified
    # row, and a fresh model answer under a "2026-09" key that nobody had
    # checked. Hong Kong to Vietnam read "visa required, e-visa" without a
    # date and "visa-free, 30 days" with one, on the same day, to Trip.com's
    # testers. The freshness sweep keeps the ONE row current, which is how a
    # policy that changes on a future date reaches readers: through the
    # re-check on the day, never through a speculative dated copy. The slot
    # stays in the key so every shipped row keeps its address.
    policy_month = "unknown"
    nationality = str(route.get("passport_nationality", "")).upper()
    parts = [
        nationality,
        # Residence never forks the decision either. No verified fact and no
        # policy in this database depends on where the passport holder lives,
        # yet "based in Dubai" used to mint a second, unverified model answer
        # under a key nobody else could see. The slot stays so every shipped
        # row keeps its address.
        nationality,
        str(route.get("destination_country", "")).upper(),
        str(route.get("travel_purpose", "tourism")).lower(),
        "default",
        policy_month,
        CACHE_VERSION,
    ]
    # Stopovers never fork the destination decision. Their independently
    # checked transit-purpose answers are attached per request below.
    # Document type likewise: a diplomatic passport is a different answer.
    doc = normalize_document_type(route.get("travel_document_type"))
    if doc and doc != "ordinary_passport":
        parts.append("doc:" + doc)
    return "|".join(parts)


def is_canonical_key(key: str) -> bool:
    """The one row a route's verified facts live on: residence equal to the
    nationality, no travel month, no stopover. Document variants stay
    canonical (a diplomatic passport is its own verified answer), and so do
    consular districts. Everything else is a variant that must inherit,
    never a second decision."""
    parts = str(key or "").split("|")
    if len(parts) < 7 or parts[6] != CACHE_VERSION:
        return False
    if parts[1] != parts[0] or parts[4] != "default" or parts[5] != "unknown":
        return False
    return not any(p.startswith("via:") for p in parts[7:])


def canonical_key(key: str) -> str:
    """The canonical key a variant inherits from (see is_canonical_key)."""
    parts = str(key or "").split("|")
    if len(parts) < 7:
        return str(key or "")
    parts[1] = parts[0]
    parts[4] = "default"
    parts[5] = "unknown"
    return "|".join(parts[:7] + [p for p in parts[7:] if not p.startswith("via:")])


# Shorthand document names collapse to the registry codes: "ordinary" must
# hit the same cache row and records as "ordinary_passport", or the same
# route splits into two entries whose 25-field records then drift apart.
_DOC_SHORTHAND = {
    "ordinary": "ordinary_passport", "passport": "ordinary_passport",
    "diplomatic": "diplomatic_passport", "service": "service_passport",
    "official": "service_passport", "emergency": "emergency_passport",
    "temporary": "temporary_passport", "child": "child_passport",
}


def normalize_document_type(raw) -> str:
    doc = str(raw or "ordinary_passport").strip().lower().replace(" ", "_")
    return _DOC_SHORTHAND.get(doc, doc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _cached(db, key: str) -> KimiRouteGuidanceCache | None:
    return db.execute(select(KimiRouteGuidanceCache).where(
        KimiRouteGuidanceCache.cache_key == key)).scalars().first()


def _is_stale(row) -> bool:
    fu = row.fresh_until
    if fu is None:
        return False
    if fu.tzinfo is None:                      # SQLite returns naive datetimes
        fu = fu.replace(tzinfo=timezone.utc)
    return fu < _now()


def _strip_visa_free_leftovers(guidance: dict, *, verified_fields=()) -> dict:
    """A visa-free verdict must not carry application machinery.

    The model sometimes leaves a fee, a portal or a product table in a
    VISA_EXEMPT answer (seen live: a diplomatic-exemption answer showing
    "Government fee 260 CNY" and an application portal). The same clearing
    the override path applies is applied to EVERY served answer here, so the
    page, the quality backend and the Excel export all get the clean truth.
    arrival_card deliberately survives — a visa-free route can still require
    a pre-arrival filing."""
    if not isinstance(guidance, dict)             or str(guidance.get("disposition") or "").upper() != "VISA_EXEMPT":
        return guidance
    from .verified_overrides import _APPLICATION_ONLY
    out = dict(guidance)
    verified_fields = set(verified_fields or ())
    for k in _APPLICATION_ONLY:
        # "forms" survives: a visa-free route can still carry an ARRIVAL form
        # (the SG Arrival Card, Malaysia's MDAC) and the entry-preparation
        # checklist is built from it.
        if k == "forms" or k in verified_fields:
            continue
        out.pop(k, None)
    if "appointment_required" not in verified_fields:
        out["appointment_required"] = False
    if "interview_required" not in verified_fields:
        out["interview_required"] = False
    return out


def _result(status: str, guidance: dict, *, cached: bool, stale: bool,
            missing=None, contradictions=None, model: str = "",
            advisories=None, elapsed_seconds: float | None = None,
            released: bool = False) -> dict:
    # Exactly one Kimi pass produced this — the verification field says so
    # honestly for a complete decision and is empty otherwise.
    # The invariants run on the answer AS STORED: a model verdict of visa-free over priced e-visa
    # products is the shape of the Hong Kong to Vietnam incident, and tidying
    # it away is how it was served with a straight face.
    from .verified_overrides import _normalise_legacy_shapes
    raw_problems = set(serve_time_invariants(guidance))
    # Legacy prose lists and empty passport contracts have lossless shared
    # normalizations. These are not new policy facts or source verification.
    guidance = _normalise_legacy_shapes(guidance)
    problems = serve_time_invariants(guidance)
    resolved_shapes = raw_problems - set(problems)
    contradictions = list(dict.fromkeys(
        [issue for issue in (contradictions or []) if issue not in resolved_shapes] + problems))
    # Preserve the stored fields until the route's sourced overrides have
    # been merged. Cleaning an exempt answer here erased its channel/fee
    # before a required-visa correction, while the records reader merged
    # the unmodified cache. verified_overrides.apply owns final cleanup.
    guidance = dict(guidance or {})
    decided = status == STATUS_PRIMARY
    verification = {"passes": 1, "label": VERIFIED_LABEL} if decided else {}
    out = {
        "status": status,
        "ai_generated": True,
        "label": VERIFIED_LABEL if decided else "AI-generated route guidance",
        "guidance": guidance,
        "workflow_plan": derive_workflow_plan(guidance) if guidance and not problems else [],
        # The 3-5 key steps a traveller actually follows, deduplicated and
        # ordered (Trip.com's spec). The raw arrays stay in the guidance for
        # anything that needs them.
        "apply_steps": canonical_steps(guidance) if guidance and not problems else [],
        "missing_fields": list(missing or []),
        "contradictions": list(contradictions or []),
        "advisories": list(advisories or []),
        "cached": cached, "stale": stale, "model": model,
        "verification": verification,
        # The safety boundary the UI must show with any guidance-driven flow:
        "irreversible_requires_confirmation": True,
        # Information-quality gate (Trip.com requirement 4): an answer the
        # engine itself rates LOW confidence is held back from the reader
        # until a person confirms it, rather than shown as if it were solid.
        # The engine's own doubt is the trigger — this is not a second
        # opinion about whether it is right.
        # The gate reads the SAME grading the 25-field records publish
        # (section 4.2.3): the engine's own doubt, and equally a record the
        # standard grades Low because no official source backs it. Keying
        # only on the engine's self-rating let records shown as Low in the
        # console still reach customers unheld.
        # An answer that contradicts itself is uncertain, and uncertain
        # answers are held: the engine's own doubt, a missing official
        # source, or a verdict at odds with its own parts.
        "review_required": bool(guidance) and (bool(problems)
                           or (not released and _spec_confidence_is_low(guidance))),
        "operator_released": released,
    }
    # `held` is what actually withholds: the flag above AND the switch. With
    # the switch off (the default) a low-confidence answer is shown like any
    # other, still flagged for the operator queue and the grounded recheck.
    out["held"] = bool(problems) or (bool(out["review_required"]) and hold_enabled())
    if elapsed_seconds is not None:
        out["elapsed_seconds"] = round(elapsed_seconds, 2)
    return out


def _spec_confidence_is_low(guidance: dict) -> bool:
    """True when the acceptance standard's own confidence ladder grades this
    answer Low: conflicting or non-official-only evidence. An answer naming
    no official page at all is Low however sure the engine sounds; a
    human-verified answer always carries its source, so it never trips this."""
    g = guidance or {}
    if not (g.get("source_url") or g.get("official_portal_url")):
        return True
    return str(g.get("confidence") or "").lower() == "low"


_PORTAL_TABLE: dict = {"mtime": None, "portals": {}}


def _official_portals() -> dict:
    """Destination -> verified official portal URL, reloaded when the file
    changes. Every entry was loaded in a real browser and quoted before it
    was written down; the file ships with the seed."""
    import pathlib
    path = pathlib.Path(os.getenv("ELLIS_DATA_DIR") or str(
        pathlib.Path(__file__).resolve().parents[3] / "data")) / \
        "database_seed" / "official_portals.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    if _PORTAL_TABLE["mtime"] != mtime:
        try:
            _PORTAL_TABLE["portals"] = json.loads(path.read_text()).get("portals", {})
            _PORTAL_TABLE["mtime"] = mtime
        except Exception:  # noqa: BLE001 — a broken file must not break serving
            return _PORTAL_TABLE["portals"] or {}
    return _PORTAL_TABLE["portals"]


def apply_portal_fallback(out: dict, route: dict) -> dict:
    """The owner's rule: when a link is possible, it has to be there. A route
    that requires an application but carries no portal link is served the
    destination's VERIFIED official portal from the reference table. Answers
    that already carry a link (their own, or a human override's) keep it, and
    a visa-exempt route stays linkless because there is nothing to apply
    for."""
    g = out.get("guidance")
    if not isinstance(g, dict) or g.get("official_portal_url"):
        return out
    if g.get("disposition") not in ("VISA_REQUIRED",
                                    "ELECTRONIC_AUTHORIZATION_REQUIRED",
                                    "CONDITIONAL"):
        # A conditional verdict still helps more with the official page in
        # hand; only a plain visa-exempt route has nothing to link to.
        return out
    url = _official_portals().get(
        str(route.get("destination_country") or "").upper())
    if url:
        out = dict(out)
        out["guidance"] = dict(g, official_portal_url=url)
    return out


def apply_verified_overrides(out: dict, route: dict) -> dict:
    """Let human-verified, officially-sourced facts win over the model.

    The model answers from its own knowledge, which goes stale when a policy
    changes. Where a person has checked a route against an official government
    page, that fact replaces the model's for exactly the fields it covers, and
    the answer says so with the source and the date. A held (low-confidence)
    answer whose disposition has been verified is no longer held: the doubt was
    about a fact that is now checked."""
    from . import verified_overrides
    guidance = out.get("guidance")
    if not isinstance(guidance, dict) or not guidance:
        return out
    merged, prov = verified_overrides.apply(guidance, route or {})
    out = dict(out)
    out["guidance"] = merged
    if prov is not None:
        out["source_verified"] = prov
    # Anything DERIVED from the guidance has to be derived again. These two
    # are computed in _result from the raw model answer, before this function
    # runs, so a verified verdict fixed the guidance and left the steps built
    # from the answer it replaced. Japan to Italy said "No visa needed" and
    # "Government fee: None" above a five step How to apply that opened with
    # "Pay the EUR 7 fee online", for an ETIAS the European Commission has not
    # brought into operation.
    problems = serve_time_invariants(merged)
    out["apply_steps"] = canonical_steps(merged) if merged and not problems else []
    out["workflow_plan"] = derive_workflow_plan(merged) if merged and not problems else []
    if prov is None:
        # Shared display normalization is not new evidence. Preserve the
        # stored completeness/status and every substantive disagreement.
        if problems:
            out["held"] = True
            out["review_required"] = True
            out["status"] = STATUS_UNCERTAIN
            out["contradictions"] = list(dict.fromkeys(list(out.get("contradictions") or []) + problems))
        return out
    # The contradictions are re-derived from the FINAL answer: the model's
    # were about parts the verified facts have now replaced, and a verified
    # part sitting at odds with the verdict is exactly what must never be
    # served quietly.
    problems = serve_time_invariants(merged)
    _, merged_missing, merged_diagnostics = validate_answer(merged)
    out["contradictions"] = merged_diagnostics
    out["missing_fields"] = merged_missing
    out["status"] = STATUS_UNCERTAIN if (merged_missing or merged_diagnostics) else STATUS_PRIMARY
    verified_verdict = "disposition" in set((prov or {}).get("fields") or [])
    if problems:
        out["review_required"] = True
        out["held"] = True
    elif verified_verdict:
        # A verified verdict IS the confirmation the hold waits for: holding
        # a verified answer served an empty card for China to the UK while
        # its checked fee and products sat in the override. A verified detail
        # implies its verdict (verified_overrides), so this covers every
        # override that verified what kind of permission the route needs.
        out["review_required"] = False
        out["held"] = False
    # An override that verified only a fee or a stay confirms nothing about
    # the verdict, so the hold the answer earned on its own stands.
    return out


def normalize_guidance_label(guidance: dict | None):
    """Serve-time normalization for STORED guidance JSON (case_route_guidance
    rows written in the two-pass era). Their label and verification claim an
    independent second-pass check that no longer exists — the claim must never
    reach the UI. Pure and defensive: non-dict input is returned unchanged,
    the input is never mutated, and no DB migration is needed."""
    if not isinstance(guidance, dict):
        return guidance
    out = dict(guidance)
    label = str(out.get("label") or "")
    low = label.lower()
    if "second" in low or "kimi k3" in low or "official source" in low:
        out["label"] = VERIFIED_LABEL
    ver = out.get("verification")
    if isinstance(ver, dict) and ver:
        stale = ver.get("verdict") in ("ACCEPT", "REVISE") \
            or ver.get("passes") not in (None, 1) \
            or "second" in str(ver.get("label") or "").lower()
        if stale:
            out["verification"] = {"passes": 1, "label": VERIFIED_LABEL}
    return out


def reconcile_guidance_with_route(db, guidance: dict, *, nationality: str,
                                  destination: str) -> dict:
    """The REGISTRY outranks the model on what kind of journey this is.

    Ellis's verified route record said China -> Singapore is VISA_FREE with an
    online arrival card; the model's guidance card said "Short-Term Visit Pass
    (tourist visa), 30 SGD, 3-5 working days" — a visa that does not exist on
    this route, priced and scheduled. The applicant read it and asked whether
    they were filing a visa (2026-08-04). When the pair policy says the route
    is visa-free entry preparation, the guidance headline must say so: no visa
    wording, no visa fee, no processing wait. Only the contradicting fields
    are touched; everything else the model wrote stands.
    """
    g = dict(guidance or {})
    try:
        from sqlalchemy import text as _sql
        from .registry import iso3
        nat = iso3(nationality, default=str(nationality or "").upper())
        dest = iso3(destination, default=str(destination or "").upper())
        row = db.execute(_sql(
            "SELECT disposition, route_outcome, max_stay_days, portal_family_id "
            "FROM global_route_pair_policies WHERE passport_nationality = :n "
            "AND destination_country = :d AND travel_document_type = "
            "'ordinary_passport'"), {"n": nat, "d": dest}).fetchone()
    except Exception:  # noqa: BLE001 — no record, nothing to reconcile against
        return g
    if row is None:
        return g
    disposition = str(row[0] or "")
    outcome = str(row[1] or "")
    if disposition not in ("VISA_FREE", "VISA_EXEMPT") \
            and outcome != "ENTRY_PREPARATION":
        return g
    claims_visa = "visa" in str(g.get("visa_category") or "").lower() \
        or bool((g.get("government_fee") or {}).get("amount"))
    if not claims_visa and g.get("disposition") == "VISA_EXEMPT":
        return g
    g["disposition"] = "VISA_EXEMPT"
    g["route_workflow_type"] = "visa_exempt_preparation"
    g["visa_category"] = "Visa-free entry"
    if row[2]:
        g["permitted_stay"] = f"Up to {int(row[2])} days"
        g["permitted_stay_days"] = int(row[2])
    # No visa exists, so the government visa fee is zero — SHOWN as zero, not
    # hidden: an absent fee tile reads as "unknown", a free one is a fact the
    # applicant wants (ICA: "SGAC submission is free of charge").
    g["government_fee"] = {"amount": 0, "currency": ""}
    family = str(row[3] or "")
    if family == "singapore-sgac":
        # ICA-verified specifics for the SG Arrival Card: not a visa, free,
        # submitted within 3 days (including the day) of arrival.
        g["processing_time"] = ("No visa application — free SG Arrival Card, "
                                "submitted online within 3 days before arrival")
    else:
        g["processing_time"] = ("No visa application — entry papers are "
                                "completed online before you travel")
    g["reconciled_with_route_record"] = True
    return g


def _stage_system(fields: tuple, label: str, verdict: dict | None = None) -> str:
    head = _SYSTEM + (
        f"\n\nTHIS CALL ({label}): output ONLY these fields: {', '.join(fields)}. "
        "OMIT every other field from the JSON entirely: do not write them as "
        "null and do not name them in \"uncertainty\".")
    if label == "CORE":
        # The core answer is read while the reader waits; token count is
        # latency. Facts stay complete, prose stays short.
        head += (" Keep text fields tight: required_documents is a short "
                 "comma-separated list (at most 8 items, no explanations); "
                 "application_channel_detail at most 2 sentences; "
                 "permitted_stay and processing_time one short sentence each.")
    if verdict:
        head += ("\nThe verdict is ALREADY DECIDED and must be respected exactly: "
                 + json.dumps(verdict, ensure_ascii=False)
                 + ". Fill the detail consistently with it: a visa-exempt route "
                 "lists NO visa products and NO visa application form.")
    return head


def _detail_consistent(core: dict, detail: dict) -> dict:
    """Deterministic consistency between the served verdict and the detail:
    the verdict wins. A visa-exempt route keeps no visa products and no visa
    application form, whatever the detail call said."""
    out = {k: v for k, v in (detail or {}).items() if k in DETAIL_FIELDS and v is not None}
    if str(core.get("disposition") or "").upper() == "VISA_EXEMPT":
        out["visa_products"] = []
        out["forms"] = [f for f in (out.get("forms") or [])
                        if "visa application" not in str(f).lower()]
    return out


def fill_detail(db, key: str, route: dict, user: str, *, after=None) -> None:
    """Stage 2: the DETAIL for a row that was served core-first. Told the
    verdict it must respect; merged deterministically; the row's pending flag
    cleared whatever happens so readers stop polling; `after` (recheck +
    pre-translation) runs once the full answer exists."""
    row = _cached(db, key)
    if row is None:
        return
    core = dict(row.guidance or {})
    verdict = {k: core.get(k) for k in ("disposition", "requirement_detail",
                                          "visa_category", "application_channel",
                                          "permitted_stay", "government_fee")}
    try:
        detail = _call(_stage_system(DETAIL_FIELDS, "DETAIL", verdict), user,
                       timeout=60.0, max_tokens=PASS1_MAX_TOKENS)
    except Exception:  # noqa: BLE001 — the core answer stands on its own
        detail = None
    # Merge onto the row AS IT IS NOW, not the snapshot taken before a call
    # that can run a minute: a grounded re-check or an operator edit may
    # have written in the meantime, and the detail stage must never undo it.
    try:
        db.refresh(row)
    except Exception:  # noqa: BLE001 - never overwrite from an obsolete snapshot
        db.rollback()
        return
    current = dict(row.guidance or {})
    changed_since = {k for k in set(current) | set(core)
                     if current.get(k) != core.get(k)}
    merged = dict(current)
    if isinstance(detail, dict):
        for k, v in _detail_consistent(current, detail).items():
            if k in changed_since:
                continue
            merged[k] = v
    clean, missing, contradictions = validate_answer(merged, detail_known=isinstance(detail, dict))
    status = STATUS_PRIMARY if clean.get("disposition") and not missing and not contradictions \
        else STATUS_UNCERTAIN
    ttl = int(os.getenv("ELLIS_KIMI_GUIDANCE_TTL_DAYS", TTL_DAYS) or TTL_DAYS) \
        if status == STATUS_PRIMARY else UNCERTAIN_TTL_DAYS
    ver = dict(row.verification or {})
    ver.pop("detail_pending", None)
    from . import change_log
    change_log.record(db, key, route, current, clean,
                      origin="engine", note="detail stage completed")
    row.guidance, row.status = clean, status
    row.missing_fields, row.contradictions = missing, contradictions
    row.verification = ver
    row.fresh_until = (row.generated_at or _now()) + timedelta(days=ttl)
    db.commit()
    if after is not None:
        try:
            after(route, apply_portal_fallback(apply_verified_overrides(_result(
                status, clean, cached=True, stale=False, missing=missing,
                contradictions=contradictions, model=row.model), route), route))
        except Exception:  # noqa: BLE001
            pass


# Live detail-stage threads, joinable so a test (or a shutdown) can drain
# them instead of letting a stray thread consume the next test's stubbed
# provider. Pruned on each spawn; entries are daemon threads.
_DETAIL_THREADS: list = []
_DETAIL_GUARD = threading.Lock()


def join_detail_stage(timeout: float = 5.0, *, key: str | None = None) -> bool:
    """Wait within ONE deadline, optionally only for this route's detail.
    A request for route A must never wait for unrelated route B research."""
    deadline = time.monotonic() + max(0.0, timeout)
    with _DETAIL_GUARD:
        targets = [t for t in _DETAIL_THREADS
                   if key is None or getattr(t, "ellis_route_key", None) == key]
    for t in targets:
        if t is threading.current_thread():
            continue
        t.join(max(0.0, deadline - time.monotonic()))
    with _DETAIL_GUARD:
        _DETAIL_THREADS[:] = [t for t in _DETAIL_THREADS if t.is_alive()]
    return not any(t.is_alive() for t in targets)


def _fill_detail_async(key: str, route: dict, user: str, *, after=None) -> None:
    import threading
    from ..db import SessionLocal

    def _work():
        s = SessionLocal()
        try:
            fill_detail(s, key, route, user, after=after)
        except Exception:  # noqa: BLE001
            try:
                row = _cached(s, key)
                if row is not None:
                    ver = dict(row.verification or {}); ver.pop("detail_pending", None)
                    row.verification = ver; s.commit()
            except Exception:  # noqa: BLE001
                pass
        finally:
            s.close()
    t = threading.Thread(target=_work, name="ellis-detail-stage", daemon=True)
    t.ellis_route_key = key
    with _DETAIL_GUARD:
        _DETAIL_THREADS[:] = [t for t in _DETAIL_THREADS if t.is_alive()]
        _DETAIL_THREADS.append(t)
        t.start()


def nearest_cached_answer(db, route: dict) -> dict | None:
    """The closest real answer we already hold for this nationality and
    destination, when the exact variant cannot be decided right now.

    The owner's rule: the Database always answers. A timeout or a provider
    outage must not leave a reader with nothing, so an answer for the SAME
    nationality and destination — differing only in purpose, document type,
    travel month or stopover — is served instead, marked as the approximation
    it is. It never crosses to another route, and it never invents anything:
    if we hold nothing for this pair, this returns None."""
    nat = str(route.get("passport_nationality") or "").upper()
    dest = str(route.get("destination_country") or "").upper()
    if not nat or not dest:
        return None
    want_purpose = str(route.get("travel_purpose") or "tourism").lower()
    rows = db.execute(select(KimiRouteGuidanceCache).where(
        KimiRouteGuidanceCache.cache_key.like(f"{nat}|%|{dest}|%"))).scalars().all()
    # Only canonical rows, and only the same travel document: a diplomatic
    # passport's answer is never "close" to an ordinary one, and a variant
    # row is never a substitute for the route itself.
    want_doc = normalize_document_type(route.get("travel_document_type")) or "ordinary_passport"
    def _doc_of(key: str) -> str:
        for p in key.split("|")[7:]:
            if p.startswith("doc:"):
                return p[4:]
        return "ordinary_passport"
    rows = [r for r in rows if r.guidance and is_canonical_key(r.cache_key or "")
            and _doc_of(r.cache_key) == want_doc
            and r.cache_key.split("|")[3] == want_purpose]
    if not rows:
        return None
    def rank(r):
        parts = r.cache_key.split("|")
        same_purpose = len(parts) > 3 and parts[3] == want_purpose
        complete = r.status == STATUS_PRIMARY
        return (not complete, not same_purpose)
    best = sorted(rows, key=rank)[0]
    out = _result(best.status, best.guidance, cached=True, stale=_is_stale(best),
                  missing=best.missing_fields, contradictions=best.contradictions,
                  model=best.model,
                  advisories=deterministic_advisories(route, best.guidance or {}))
    out["approximate_for"] = {
        "asked": {"travel_purpose": want_purpose,
                  "travel_document_type": route.get("travel_document_type")},
        "served": best.route or {},
        "served_purpose": (best.cache_key.split("|") + [""] * 4)[3],
    }
    return apply_portal_fallback(apply_verified_overrides(out, route), route)


import threading as _threading

# One generation per route at a time: concurrent identical lookups (a reader
# double-submitting, the form prewarm racing the submit, two readers on the
# same cold route) share a single Kimi call instead of each paying for one.
# Each holder AND waiter owns a strong reference. The registry expires only
# after the last user leaves, so a waking waiter can never be bypassed by a
# newly-created lock for the same route.
_INFLIGHT: WeakValueDictionary = WeakValueDictionary()
_INFLIGHT_GUARD = _threading.Lock()


def _transit_checks(db, route: dict, stopovers: list[str]) -> list[dict]:
    """Read independently verified TRANSIT-purpose facts, never infer them
    from a tourist exemption or inherit the destination's operator release."""
    from . import freshness, tstation, verified_overrides
    from .authority import hostname, is_government_host
    checks = []
    portals = _official_portals()
    for stop in stopovers:
        transit_route = {**route, "destination_country": stop, "travel_purpose": "transit",
                         "visa_category": category_for_purpose("transit")}
        key = cache_key(transit_route)
        row = _cached(db, key)
        reference = portals.get(stop)
        if not isinstance(reference, str) or not is_government_host(hostname(reference)):
            reference = None
        check = {"country": stop, "required": None, "status": "unknown",
                 "source_url": reference,
                 "note": f"Transit requirements in {country_name(stop)} still need checking for this passport and itinerary."}
        if row is None or (row.verification or {}).get("detail_pending"):
            checks.append(check)
            continue
        try:
            g, prov = verified_overrides.apply(dict(served_guidance(row) or {}), transit_route)
            gc = freshness.effective_check(row.verification)
            disputed = freshness.active_disputed_fields(db, key) + list(gc.get("disputed_fields") or [])
            problems = serve_time_invariants(g)
            verified_verdict = bool(prov and "disposition" in (prov.get("fields") or []))
            from .records_guard import grounded_verdict_supported
            grounded = grounded_verdict_supported(gc)
            records = tstation.records_for_route(transit_route, g, prov, grounded_ok=grounded,
                                                 disputed_fields=disputed + problems)
        except (TypeError, ValueError, AttributeError):
            checks.append(check)
            continue
        if (not (verified_verdict or grounded) or disputed or problems or not records or
                any(r.get("confidence_level") == "Low" for r in records)):
            checks.append(check)
            continue
        source = (prov or {}).get("source_url") or gc.get("source_url")
        if not source or not is_government_host(hostname(source)):
            checks.append(check)
            continue
        disposition, detail = g.get("disposition"), g.get("requirement_detail")
        required = (True if disposition in ("VISA_REQUIRED", "VISA_ON_ARRIVAL") else
                    False if disposition == "VISA_EXEMPT" and detail == "unconditional_visa_free" else None)
        raw_notes = g.get("exceptions")
        notes = [x for x in raw_notes if isinstance(x, str)] if isinstance(raw_notes, list) else []
        if required is True:
            headline = f"A transit visa is required in {country_name(stop)}."
        elif required is False:
            headline = f"No transit visa is required in {country_name(stop)} under the checked transit rule."
        else:
            headline = f"The checked transit rule for {country_name(stop)} has conditions or separate authorisation requirements; confirm how they apply to this itinerary."
        check.update(required=required, status="verified" if required is not None else "conditional",
                     disposition=disposition, requirement_detail=detail, source_url=source,
                     note=" ".join([headline] + notes), source_verified=prov,
                     grounded_check=gc if grounded else None)
        checks.append(check)
    return checks


def get_route_guidance(db, route: dict, *, force_refresh: bool = False,
                       stage: str = "full", after=None) -> dict:
    """One destination answer, with independently checked stopover information.
    Itinerary details are response context and never a second cache decision."""
    destination_route = dict(route or {})
    raw_stops = destination_route.pop("transit_countries", [])
    stopovers = sorted({str(c).strip().upper() for c in raw_stops
                       if isinstance(c, str) and _re.fullmatch(r"[A-Za-z]{3}", c.strip())
                       and c.strip().upper() != str(route.get("destination_country") or "").upper()}) \
        if isinstance(raw_stops, list) else []
    out = _destination_guidance(db, destination_route, force_refresh=force_refresh, stage=stage, after=after)
    out = dict(out)
    out["transit_countries"] = stopovers
    if not isinstance(out.get("guidance"), dict):
        return out
    from . import health_context
    guidance = health_context.apply(dict(out["guidance"]), route)
    guidance.pop("transit_requirement", None)  # never trust a destination model's transit guess
    out["guidance"] = guidance
    if not stopovers:
        return out
    checks = _transit_checks(db, destination_route, stopovers)
    values = [c["required"] for c in checks]
    required = True if any(v is True for v in values) else False if all(v is False for v in values) else None
    note = " ".join(c["note"] for c in checks)
    guidance["transit_requirement"] = {"required": required, "note": note, "checks": checks,
                                       "incomplete": any(v is None for v in values)}
    out["advisories"] = list(out.get("advisories") or []) + [{"code": "TRANSIT_CHECK_SEPARATE",
        "severity": "info", "note": note, "message": note,
        "sources": [c["source_url"] for c in checks if c.get("source_url")]}]
    return out


def _destination_guidance(db, route: dict, *, force_refresh: bool = False,
                          stage: str = "full", after=None) -> dict:
    key = cache_key(route)
    if not force_refresh and _cached(db, key) is not None:
        return _get_route_guidance_locked(db, route, force_refresh=force_refresh,
                                          stage=stage, after=after)
    with _INFLIGHT_GUARD:
        lk = _INFLIGHT.get(key)
        if lk is None:
            lk = _threading.Lock()
            _INFLIGHT[key] = lk
    acquired = lk.acquire(timeout=max(_deadline_seconds() * 2, 60))
    try:
        if acquired:
            # Another request may have generated and committed while this one
            # waited; drop stale session state so the fresh row is visible.
            try:
                db.expire_all()
            except Exception:  # noqa: BLE001 - session hygiene must not fail a lookup
                pass
        else:
            # The lock timed out: another decision for this exact route is
            # still running. Serve what exists rather than start a second,
            # interleaving decision; with nothing cached the honest answer
            # is the timeout the reader page already handles.
            try:
                db.expire_all()
            except Exception:  # noqa: BLE001
                pass
            if _cached(db, key) is None:
                raise GuidanceTimeout()
            return _get_route_guidance_locked(db, route, force_refresh=False,
                                              stage=stage, after=after)
        return _get_route_guidance_locked(db, route, force_refresh=force_refresh,
                                          stage=stage, after=after)
    finally:
        if acquired:
            lk.release()


def _get_route_guidance_locked(db, route: dict, *, force_refresh: bool = False,
                               stage: str = "full", after=None) -> dict:
    """The authoritative single-pass route decision under one hard deadline.

    Cached identical routes return instantly. A fresh route runs exactly ONE
    structured Kimi request -> deterministic validation -> at most ONE
    targeted retry when malformed/incomplete (and only while budget remains).
    The Kimi result is used directly. Never starts research; never creates
    review tasks; never leaves the caller without a bounded outcome.
    """
    key = cache_key(route)
    row = _cached(db, key)
    from . import freshness as _freshness
    if row is not None and force_refresh:
        # A released answer, or one already checked against its official
        # page, is never regenerated from the model's memory: the page and
        # the person outrank the model. The cached answer is served.
        ver = row.verification or {}
        if ver.get("operator_released") or ver.get("drill_shadow") or _freshness.effective_check(ver):
            force_refresh = False
    if row is not None and not force_refresh:
        if stage == "full" and (row.verification or {}).get("detail_pending"):
            join_detail_stage(timeout=min(60.0, _deadline_seconds()), key=key)
            db.expire_all()
            row = _cached(db, key)
            if row is None:
                raise GuidanceTimeout()
        released = bool((row.verification or {}).get("operator_released"))
        if not released and not is_canonical_key(key):
            # A stopover or residence variant inherits the release an
            # operator gave the route itself; the console only ever sees the
            # canonical row.
            base = _cached(db, canonical_key(key))
            released = bool(base is not None and
                            (base.verification or {}).get("operator_released"))
        # The grounding that counts: a failed later attempt never erases
        # the last successful read of the official page.
        gc = _freshness.effective_check(row.verification)
        out = apply_portal_fallback(apply_verified_overrides(_result(
            row.status, served_guidance(row), cached=True, stale=_is_stale(row),
            released=released,
            missing=row.missing_fields, contradictions=row.contradictions,
            model=row.model,
            advisories=deterministic_advisories(route, row.guidance or {})), route), route)
        out["detail_pending"] = bool((row.verification or {}).get("detail_pending"))
        if isinstance(gc, dict) and gc.get("outcome") == "checked":
            # Machine provenance, deliberately WEAKER than the human badge:
            # "the official page was read on this date and matched", never
            # "a person verified this". `consistent` is passed through so the
            # UI can only claim a match when there actually was one — a check
            # that found a disagreement is not a clean bill of health.
            # "outcome" rides along: the reader-page hold gate keys on it,
            # and without it every machine-grounded answer read as never
            # grounded and was held from readers while the console graded
            # it Medium (found 2026-09-03).
            out["grounded_check"] = {k: gc.get(k) for k in
                                     ("at", "outcome", "source_url",
                                      "consistent", "changed_fields",
                                      "disputed_fields", "evidence_contract",
                                      "verified_fields", "unverified_fields",
                                      "source_checks", "field_sources",
                                      "unchecked_sources", "renewed")}
        return out

    if not is_available():
        raise GuidanceUnavailable("Kimi K3 not configured — guidance unavailable")

    started = time.monotonic()
    deadline = started + _deadline_seconds()

    def remaining() -> float:
        return deadline - time.monotonic()

    def budget() -> float:
        r = remaining()
        if r < MIN_CALL_BUDGET_SECONDS:
            raise GuidanceTimeout()
        return r

    user = build_prompt(route)
    model = (os.getenv("KIMI_GUIDANCE_MODEL", "").strip() or settings().kimi_model) \
        if _PROVIDER is None else "injected-test-provider"

    # ---- the ONE structured analysis -----------------------------------------
    # (A concurrent core/detail split was tried and reverted: the halves could
    # contradict each other — the verdict half said visa-exempt while the
    # detail half, not knowing that, listed visa products — and it was not
    # faster. Speed comes from the two-STAGE path below instead.)
    staged = stage == "core"
    system = _stage_system(CORE_FIELDS, "CORE") if staged else _SYSTEM
    try:
        raw = _call(system, user, timeout=budget(), max_tokens=PASS1_MAX_TOKENS)
    except (GuidanceUnavailable, GuidanceTimeout, GuidanceProviderError):
        raise
    except Exception:  # noqa: BLE001 - malformed transport/JSON -> one retry below
        raw = None
    clean, missing, contradictions = validate_answer(raw or {}, detail_known=not staged)
    detail_ok = not staged

    if not clean.get("disposition"):
        # ONE controlled retry, ONLY when there is no verdict at all (nothing
        # to show the reader) and only inside the remaining budget. An answer
        # that merely has gaps is served immediately as KIMI_UNCERTAIN and
        # cached briefly; the background refresh keeps trying for a complete
        # one. A slow retry for a gap was the difference between a 30-second
        # answer and a timeout.
        retry_user = (user + "\n\nYour previous answer was incomplete. "
                      + (f"Missing or invalid fields: {', '.join(missing)}. " if missing else "")
                      + (f"Contradictions to resolve: {'; '.join(contradictions)}. " if contradictions else "")
                      + "Reply the FULL corrected JSON.")
        try:
            raw2 = _call(system, retry_user, timeout=budget(), max_tokens=PASS1_MAX_TOKENS)
            clean2, missing2, contradictions2 = validate_answer(raw2 or {}, detail_known=detail_ok)
            if len(missing2) + len(contradictions2) < len(missing) + len(contradictions):
                clean, missing, contradictions = clean2, missing2, contradictions2
        except (GuidanceUnavailable, GuidanceTimeout, GuidanceProviderError):
            raise
        except Exception:  # noqa: BLE001 - keep the first answer's honest gaps
            pass

    # Transit answers belong to their own verified transit-purpose route;
    # an unsolicited model transit field never becomes canonical policy.
    clean.pop("transit_requirement", None)
    status = STATUS_PRIMARY if clean and not missing and not contradictions \
        else STATUS_UNCERTAIN
    elapsed = time.monotonic() - started
    advisories = deterministic_advisories(route, clean)

    # Cache every answer that HAS content: a complete one for the full window,
    # an incomplete one briefly (UNCERTAIN_TTL_DAYS) so a repeat reader gets it
    # instantly while the background refresh keeps trying for a complete one.
    # A failed attempt (no content) is never cached.
    has_content = bool(clean.get("disposition"))   # defaults alone are not an answer
    if has_content and status in (STATUS_PRIMARY, STATUS_UNCERTAIN):
        now = _now()
        ttl = int(os.getenv("ELLIS_KIMI_GUIDANCE_TTL_DAYS", TTL_DAYS) or TTL_DAYS) \
            if status == STATUS_PRIMARY else UNCERTAIN_TTL_DAYS
        if row is None:
            row = _cached(db, key)
        _prior_guidance = dict(row.guidance) if row is not None and row.guidance else None
        if row is None:
            row = KimiRouteGuidanceCache(cache_key=key)
            db.add(row)
        row.route = {k: route.get(k) for k in (
            "passport_nationality", "lawful_country_of_residence", "destination_country",
            "visa_category", "travel_purpose", "consular_jurisdiction",
            # Without these two, a diplomatic-passport row later reads as an
            # ordinary-passport answer in the quality backend and the export,
            # and ordinary-passport overrides wrongly apply to it.
            "travel_document_type")}
        row.status = status
        row.guidance = clean
        row.missing_fields = missing
        row.contradictions = contradictions
        row.model = model
        # Updated in place: a regeneration must never wipe the row's history
        # (an operator's release, the last good page check, a drill shadow).
        ver = dict(row.verification or {})
        ver.pop("detail_pending", None)
        ver.update({"passes": 1, "label": VERIFIED_LABEL})
        if staged:
            ver["detail_pending"] = True
        row.verification = ver
        row.generated_at = now
        row.fresh_until = now + timedelta(days=ttl)
        from . import change_log
        change_log.record(db, key, route, _prior_guidance, clean,
                          origin="engine",
                          note="route answered by the engine")
        db.commit()
    out = apply_portal_fallback(apply_verified_overrides(_result(
        status, clean, cached=False, stale=False,
        missing=missing, contradictions=contradictions, model=model,
        advisories=advisories, elapsed_seconds=elapsed), route), route)
    if staged and has_content:
        if _PROVIDER is not None:
            # Injected provider (tests): stage 2 runs inline, deterministically.
            fill_detail(db, key, route, user, after=after)
            row = _cached(db, key)
            out = apply_portal_fallback(apply_verified_overrides(_result(
                row.status, row.guidance, cached=False, stale=False,
                missing=row.missing_fields, contradictions=row.contradictions,
                model=row.model, advisories=advisories,
                elapsed_seconds=elapsed), route), route)
            out["detail_pending"] = False
        else:
            _fill_detail_async(key, route, user, after=after)
            out["detail_pending"] = True
    elif after is not None and not out.get("cached"):
        try:
            after(route, out)
        except Exception:  # noqa: BLE001
            pass
    return out


_REFRESH_IN_FLIGHT: set = set()
_REFRESH_GUARD = _threading.Lock()


def refresh_stale_async(db_factory, route: dict) -> None:
    """Background renewal for a stale cache entry (never blocks the reader).

    Renewal is GROUNDED first: the stored answer is re-checked against its own
    official government page (freshness.recheck_route), which is the only way
    a policy change actually reaches the answer — re-asking the model's memory
    just renews the same staleness with a new timestamp. Memory regeneration
    remains only as the fallback for a row that has never been grounded and
    names no official page; once a row has been checked against its page, a
    failed recheck keeps the existing answer (still honestly marked stale)
    rather than reverting to whatever the model remembers today."""
    row_key = cache_key(route)
    with _REFRESH_GUARD:
        if row_key in _REFRESH_IN_FLIGHT:
            return
        _REFRESH_IN_FLIGHT.add(row_key)
    db = db_factory()
    try:
        from . import freshness
        row = db.execute(select(KimiRouteGuidanceCache).where(
            KimiRouteGuidanceCache.cache_key == row_key)).scalars().first()
        outcome = None
        try:
            outcome = freshness.recheck_route(db, route)
        except Exception:  # noqa: BLE001 - grounded renewal is best-effort
            outcome = None
        ok = bool(outcome) and outcome.get("outcome") == "checked"
        if not ok and row is not None:
            # The page could not be read, so nothing is renewed: the answer
            # stays, honestly marked stale, and one open issue tells a person
            # which page failed. Re-asking the model's memory was how a
            # stale row became a fresh guess with a new timestamp.
            freshness.note_unreadable(db, row, outcome)
    except Exception:  # noqa: BLE001 - background refresh is best-effort
        pass
    finally:
        db.close()
        with _REFRESH_GUARD:
            _REFRESH_IN_FLIGHT.discard(row_key)


def maybe_start_adapter_build(db, *, org_id: str, user_id: str, case_id: str,
                              route: dict, route_key: str, guidance: dict) -> dict:
    """Guidance-driven adapter generation: reuse the SAME authorized bridge as
    research (standing authorization + government-domain portal required; build
    runs the normal validation/testing/auto-release pipeline). Defensive: any
    problem simply means no build starts."""
    portal = (guidance or {}).get("official_portal_url") or ""
    if not portal or not case_id:
        return {"started": False, "reason": "no portal or no case"}
    from types import SimpleNamespace
    from . import ondemand
    job = SimpleNamespace(org_id=org_id, user_id=user_id, case_id=case_id,
                          route=route, route_key=route_key, requested_language="en")
    state = {"disposition": {"VISA_REQUIRED": "EMBASSY_VISA_REQUIRED",
                             "ELECTRONIC_AUTHORIZATION_REQUIRED": "ETA_REQUIRED",
                             "CONDITIONAL": None,
                             "VISA_EXEMPT": None}.get(guidance.get("disposition")),
             "portal": portal, "material_conflict": False,
             "disposition_sources": [], "jurisdiction": None}
    if guidance.get("application_channel") == "online_portal" and \
            guidance.get("disposition") == "VISA_REQUIRED":
        state["disposition"] = "EVISA_REQUIRED"
    out = ondemand._evaluate_adapter_readiness(db, job, state)
    return {"started": bool(out.get("auto_build_started")), "detail": out}


# --- AI Q&A: natural language -> route (Trip.com feature 3) -------------------
_ASK_SYSTEM = ("""You read a traveller's message and extract their route. The
message can be casual, indirect, misspelled, in English or Chinese or mixed,
and may mention places that are NOT part of the route (a friend's origin, an
aside). Work out what the person actually holds and where they are actually
going.

Reply STRICT JSON:
{"nationality": ISO3 of the PASSPORT they hold ("I'm Chinese", "my UK
passport", "from France" when it means them, 持中国护照...) or null,
"destination": ISO3 of where THEY are going or null,
"transit_countries": [ISO3...] for stopovers/layovers ("via Dubai",
经新加坡转机), else [],
"travel_purpose": tourism|business|family_visit|study|work|transit or null,
"travel_document_type": "diplomatic_passport"|"service_passport"|
"emergency_passport"|"ordinary_passport" (default ordinary_passport),
"arrival_date": "YYYY-MM-DD" if they say WHEN they are going ("in january
2027", 明年三月 — use the first of that month) else null,
"focus": "fee"|"stay"|"documents"|"processing"|null — the ONE fact asked for,
if any}

Examples:
"im meeting my friend from paris in new york, i have a chinese passport"
 -> nationality CHN, destination USA (the friend being from France is not the route)
"wife and i wanna do bali then bangkok, we're german" -> nationality DEU,
 destination IDN (first destination; Bangkok is a later leg, not transit)
"how long can singaporeans stay in the uk" -> nationality SGP, destination GBR, focus "stay"
"日本人去美国出差要办什么" -> nationality JPN, destination USA, purpose business
"i wanna go somewhere warm" -> destination null (never invent)

Null anything not stated. NEVER invent a country. No prose, JSON only.""")


# Deterministic question reading: country names matched straight from the
# registry (plus the everyday names and Chinese names people actually type),
# instantly and without a model call. "from France" is read as a French
# passport — the sensible reading for a visa tool — and the form below keeps
# the nationality visible so the reader can change it.
_ALIASES = {
    "USA": ("usa", "us", "u.s.", "america", "united states", "the states", "美国", "美國", "american", "yank", "yankee"),
    "GBR": ("uk", "u.k.", "britain", "great britain", "england", "united kingdom", "英国", "英國", "british", "english", "brit", "briton"),
    "CHN": ("china", "mainland china", "prc", "mainland", "中国", "中國", "中国大陆",
            "中华人民共和国", "中華人民共和國", "内地", "內地", "大陆", "大陸",
            "chinese"),
    "HKG": ("hong kong", "hongkong", "hk", "hksar", "香港", "特区护照", "特區護照",
            "港人", "hong konger", "hongkonger"),
    "TWN": ("taiwan", "台湾", "台灣", "taiwanese"),
    "JPN": ("japan", "日本", "japanese"),
    "KOR": ("korea", "south korea", "韩国", "韓國", "korean"),
    "SGP": ("singapore", "新加坡", "singaporean"),
    "MYS": ("malaysia", "马来西亚", "馬來西亞", "malaysian"),
    "THA": ("thailand", "泰国", "泰國", "thai"),
    "VNM": ("vietnam", "viet nam", "越南", "vietnamese"),
    "IDN": ("indonesia", "bali", "印尼", "印度尼西亚", "indonesian"),
    "PHL": ("philippines", "菲律宾", "菲律賓", "filipino"),
    "IND": ("india", "印度", "indian"),
    "RUS": ("russia", "俄罗斯", "俄羅斯", "russian"),
    "AUS": ("australia", "澳大利亚", "澳洲", "australian", "aussie"),
    "NZL": ("new zealand", "新西兰", "紐西蘭", "new zealander"),
    "CAN": ("canada", "加拿大", "canadian", "canuck"),
    "FRA": ("france", "法国", "法國", "french"),
    "DEU": ("germany", "德国", "德國", "german"),
    "ITA": ("italy", "意大利", "義大利", "italian"),
    "ESP": ("spain", "西班牙", "spanish"),
    "NLD": ("netherlands", "holland", "荷兰", "荷蘭", "dutch"),
    "CHE": ("switzerland", "瑞士", "swiss"),
    "ARE": ("uae", "united arab emirates", "dubai", "abu dhabi", "阿联酋", "阿聯酋",
            "迪拜", "杜拜", "阿布扎比", "emirati"),
    "TUR": ("turkey", "türkiye", "土耳其", "turkish"),
    "EGY": ("egypt", "埃及", "egyptian"),
    "BRA": ("brazil", "巴西", "brazilian"),
    "MEX": ("mexico", "墨西哥", "mexican"),
    "MAC": ("macau", "macao", "澳门", "澳門"),
    "KHM": ("cambodia", "柬埔寨", "cambodian"),
    "PRT": ("portugal", "葡萄牙", "portuguese"),
    "GRC": ("greece", "希腊", "希臘", "greek"),
    "AUT": ("austria", "奥地利", "奧地利", "austrian"),
    "BEL": ("belgium", "比利时", "比利時", "belgian"),
    "SWE": ("sweden", "瑞典", "swedish"),
    "NOR": ("norway", "挪威", "norwegian"),
    "DNK": ("denmark", "丹麦", "丹麥", "danish"),
    "FIN": ("finland", "芬兰", "芬蘭", "finnish"),
    "IRL": ("ireland", "爱尔兰", "愛爾蘭", "irish"),
    "POL": ("poland", "波兰", "波蘭", "polish"),
    "CZE": ("czech republic", "czechia", "捷克", "czech"),
    "HUN": ("hungary", "匈牙利", "hungarian"),
    "ISR": ("israel", "以色列", "israeli"),
    "SAU": ("saudi arabia", "saudi", "沙特", "沙烏地", "saudi"),
    "QAT": ("qatar", "卡塔尔", "卡達", "doha", "多哈", "qatari"),
    "ZAF": ("south africa", "南非", "south african"),
    "ARG": ("argentina", "阿根廷", "argentine", "argentinian"),
    "CHL": ("chile", "智利", "chilean"),
    "PER": ("peru", "秘鲁", "秘魯", "peruvian"),
    "MAR": ("morocco", "摩洛哥", "moroccan"),
    "KEN": ("kenya", "肯尼亚", "肯亞", "kenyan"),
    "LKA": ("sri lanka", "斯里兰卡", "斯里蘭卡", "sri lankan"),
    "NPL": ("nepal", "尼泊尔", "尼泊爾", "nepali", "nepalese"),
    "MDV": ("maldives", "马尔代夫", "馬爾地夫", "maldivian"),
    "PAK": ("pakistan", "巴基斯坦", "pakistani"),
    "BRN": ("brunei", "文莱", "汶萊", "bruneian"),
    "BGD": ("bangladesh", "孟加拉", "bangladeshi"),
    "MNG": ("mongolia", "蒙古", "mongolian"),
    "KAZ": ("kazakhstan", "哈萨克斯坦", "哈薩克", "kazakh"),
    "UZB": ("uzbekistan", "乌兹别克斯坦", "烏茲別克", "uzbek"),
    "GEO": ("georgia", "格鲁吉亚", "喬治亞", "georgian"),
    "ARM": ("armenia", "亚美尼亚", "亞美尼亞", "armenian"),
    "JOR": ("jordan", "约旦", "約旦", "jordanian"),
    "IRN": ("iran", "伊朗", "iranian"),
    "CUB": ("cuba", "古巴", "cuban"),
    "ISL": ("iceland", "冰岛", "冰島", "icelandic"),
}
# Cities people type instead of countries. A city is a destination hint only
# (nobody says "from Tokyo" meaning a Japanese passport — but if they do, the
# country rule still reads it).
_CITIES = {
    "JPN": ("tokyo", "osaka", "kyoto", "东京", "東京", "大阪", "京都"),
    "CHN": ("beijing", "shanghai", "guangzhou", "shenzhen", "chengdu", "hangzhou", "hainan", "sanya", "haikou", "北京", "上海", "广州", "深圳", "成都", "海南", "三亚", "三亞", "海口", "杭州", "厦门", "西安", "重庆", "南京", "武汉"),
    "KOR_extra": ("仁川", "济州岛", "濟州島"),
    "ARE": ("dubai", "abu dhabi", "迪拜", "阿布扎比"), "THA": ("bangkok", "phuket", "chiang mai", "曼谷", "普吉", "清迈", "清邁", "芭提雅"),
    "MYS": ("kuala lumpur", "penang", "吉隆坡", "槟城", "檳城"), "IDN": ("jakarta", "巴厘岛", "巴厘島", "雅加达", "雅加達"),
    "GBR": ("london", "伦敦", "倫敦"), "FRA": ("paris", "巴黎"), "USA": ("new york", "los angeles", "san francisco", "纽约", "紐約", "洛杉矶", "洛杉磯", "旧金山", "舊金山", "夏威夷"),
    "AUS": ("sydney", "melbourne", "悉尼", "雪梨", "墨尔本", "墨爾本"), "CAN": ("toronto", "vancouver", "多伦多", "多倫多", "温哥华", "溫哥華"),
    "DEU": ("frankfurt", "munich", "法兰克福", "法蘭克福", "慕尼黑", "柏林"), "ITA": ("rome", "milan", "罗马", "羅馬", "米兰", "米蘭"),
    "ESP": ("madrid", "barcelona", "马德里", "馬德里", "巴塞罗那", "巴塞隆納"), "NLD": ("amsterdam", "阿姆斯特丹"), "CHE": ("zurich", "苏黎世", "蘇黎世"),
    "TUR": ("istanbul", "伊斯坦布尔", "伊斯坦堡"), "EGY": ("cairo", "开罗", "開羅"), "VNM": ("hanoi", "ho chi minh", "da nang", "nha trang", "河内", "河內", "胡志明", "岘港", "峴港", "芽庄", "芽莊"),
    "PHL": ("manila", "cebu", "马尼拉", "馬尼拉", "宿务", "宿霧", "长滩岛", "長灘島"), "KHM": ("phnom penh", "siem reap", "金边", "金邊", "暹粒"), "NPL": ("kathmandu", "加德满都", "加德滿都"),
    "QAT": ("doha", "多哈"), "RUS": ("moscow", "莫斯科", "圣彼得堡", "聖彼得堡"), "MAC": ("澳门", "澳門"), "TWN": ("台北", "高雄"),
    "JPN_extra": ("冲绳", "沖繩", "北海道", "名古屋", "福冈", "福岡", "札幌"),
    "HKG": (), "TWN": ("taipei", "台北"),
    "KOR": ("seoul", "busan", "jeju", "首尔", "首爾", "釜山", "济州", "濟州"),
    "THA": ("bangkok", "phuket", "chiang mai", "曼谷", "普吉"),
    "SGP": (), "MYS": ("kuala lumpur", "吉隆坡"),
    "VNM": ("hanoi", "ho chi minh", "saigon", "da nang", "河内", "胡志明", "岘港"),
    "IDN": ("jakarta", "雅加达", "巴厘岛", "峇里島"),
    "PHL": ("manila", "cebu", "马尼拉", "宿务"),
    "IND": ("delhi", "new delhi", "mumbai", "bangalore", "新德里", "孟买"),
    "ARE": (), "TUR": ("istanbul", "伊斯坦布尔", "伊斯坦堡"),
    "GBR": ("london", "manchester", "edinburgh", "伦敦", "倫敦"),
    "FRA": ("paris", "nice", "lyon", "巴黎"),
    "DEU": ("berlin", "munich", "frankfurt", "柏林", "慕尼黑", "法兰克福"),
    "ITA": ("rome", "milan", "venice", "florence", "罗马", "羅馬", "米兰", "米蘭"),
    "ESP": ("madrid", "barcelona", "马德里", "巴塞罗那"),
    "NLD": ("amsterdam", "阿姆斯特丹"), "CHE": ("zurich", "geneva", "苏黎世", "日内瓦"),
    "USA": ("new york", "los angeles", "san francisco", "las vegas", "chicago", "纽约", "紐約", "洛杉矶", "洛杉磯", "旧金山", "舊金山"),
    "CAN": ("toronto", "vancouver", "montreal", "多伦多", "多倫多", "温哥华", "溫哥華"),
    "AUS": ("sydney", "melbourne", "悉尼", "雪梨", "墨尔本", "墨爾本"),
    "NZL": ("auckland", "奥克兰"), "RUS": ("moscow", "莫斯科", "圣彼得堡"),
    "EGY": ("cairo", "开罗"), "BRA": ("rio", "sao paulo", "里约"),
    "MEX": ("cancun", "mexico city", "坎昆"), "PRT": ("lisbon", "里斯本"),
    "GRC": ("athens", "santorini", "雅典", "圣托里尼"), "AUT": ("vienna", "维也纳"),
    "CZE": ("prague", "布拉格"), "HUN": ("budapest", "布达佩斯"),
    "ISR": ("tel aviv", "jerusalem", "特拉维夫", "耶路撒冷"),
    "MDV": ("male", "马累"), "KHM": ("siem reap", "phnom penh", "暹粒", "金边"),
    "NPL": ("kathmandu", "加德满都"), "LKA": ("colombo", "科伦坡"),
}

_PURPOSE_WORDS = (
    ("business", ("business", "商务", "商務", "出差", "conference", "meeting",
                  "exhibition", "trade fair", "展会", "展會", "参展", "參展",
                  "开会", "開會", "会议", "會議", "公务", "公務")),
    ("study", ("study", "student", "留学", "留學", "读书", "讀書", "上学",
               "上學", "念书", "唸書", "university", "school")),
    ("work", ("work visa", "work permit", "for work", "to work", "working", "job",
              "employment", "employed", "work assignment", "assignment", "subclass 482",
              "工作", "打工", "就业", "就業", "上班", "做嘢", "返工")),
    ("family_visit", ("visit my family", "visiting my family", "visit family",
                      "visiting family", "see my family", "family visit",
                      "relatives", "探亲", "探親", "visit my", "親友", "亲友",
                      "visit friends", "visiting friends", "亲戚", "親戚", "看望")),
    ("transit", ("transit", "layover", "stopover", "过境", "過境", "转机", "轉機",
                 "中转", "中轉")),
)


# Uppercase codes a tester or an agent types: "CN to JP", "USA→VNM". Only
# uppercase tokens count, so "in", "my" and "5th" stay ordinary words.
_ISO2 = {
    "CN": "CHN", "JP": "JPN", "KR": "KOR", "SG": "SGP", "MY": "MYS", "TH": "THA",
    "VN": "VNM", "ID": "IDN", "PH": "PHL", "TW": "TWN", "HK": "HKG", "MO": "MAC",
    "IN": "IND", "AU": "AUS", "CA": "CAN", "GB": "GBR", "UK": "GBR", "US": "USA",
    "FR": "FRA", "DE": "DEU", "ES": "ESP", "IT": "ITA", "RU": "RUS", "AE": "ARE",
    "TR": "TUR", "EG": "EGY", "NZ": "NZL", "BR": "BRA", "MX": "MEX", "KH": "KHM",
    "MM": "MMR", "NP": "NPL", "LK": "LKA", "PK": "PAK", "BD": "BGD",
    "SA": "SAU", "QA": "QAT", "CH": "CHE", "AT": "AUT", "NL": "NLD", "BE": "BEL",
    "PT": "PRT", "GR": "GRC", "SE": "SWE", "NO": "NOR", "DK": "DNK", "FI": "FIN",
    "IE": "IRL", "PL": "POL", "CZ": "CZE", "HU": "HUN", "MA": "MAR", "ZA": "ZAF",
    "KE": "KEN", "AR": "ARG", "CL": "CHL", "PE": "PER", "CO": "COL", "IL": "ISR",
    "JO": "JOR", "OM": "OMN", "KZ": "KAZ", "UZ": "UZB", "MN": "MNG", "BN": "BRN",
}
# Airport codes people use for the city ("LAX", "layover in DXB").
_AIRPORTS = {
    "LAX": "USA", "JFK": "USA", "SFO": "USA", "ORD": "USA", "EWR": "USA", "SEA": "USA",
    "LHR": "GBR", "LGW": "GBR", "CDG": "FRA", "AMS": "NLD", "FRA": "DEU", "MUC": "DEU",
    "FCO": "ITA", "MAD": "ESP", "ZRH": "CHE", "VIE": "AUT", "IST": "TUR", "DXB": "ARE",
    "AUH": "ARE", "DOH": "QAT", "NRT": "JPN", "HND": "JPN", "KIX": "JPN", "ICN": "KOR",
    "GMP": "KOR", "SIN": "SGP", "KUL": "MYS", "BKK": "THA", "DMK": "THA", "HKT": "THA",
    "CGK": "IDN", "DPS": "IDN", "MNL": "PHL", "CEB": "PHL", "SGN": "VNM", "HAN": "VNM",
    "PVG": "CHN", "SHA": "CHN", "PEK": "CHN", "PKX": "CHN", "CAN": "CHN", "SZX": "CHN",
    "CTU": "CHN", "TPE": "TWN", "SYD": "AUS", "MEL": "AUS", "BNE": "AUS", "PER": "AUS",
    "AKL": "NZL", "YVR": "CAN", "YYZ": "CAN", "YUL": "CAN", "DEL": "IND", "BOM": "IND",
    "MAA": "IND", "BLR": "IND", "CMB": "LKA", "KTM": "NPL", "PNH": "KHM", "REP": "KHM",
    "VTE": "LAO", "RGN": "MMR", "CAI": "EGY", "SVO": "RUS", "DME": "RUS",
    "GRU": "BRA", "MCT": "OMN", "RUH": "SAU", "JED": "SAU", "TLV": "ISR",
}
_AIRLINE_RE = _re.compile(
    r"\b(?:jeju|hainan|china|korean|japan|singapore|thai|vietnam|malaysia|air asia"
    r"|airasia|cathay|eva|philippine|garuda|qatar|emirates|etihad|turkish|egypt"
    r"|air india|air china|air canada|air france|british|american|united|delta"
    r"|scoot|jetstar|peach|hk express|hong kong)\s+(?:air(?:lines?|ways)?|express)\b"
    r"|\bair\s+(?:china|india|canada|france|asia|macau|japan|busan|seoul|new zealand)\b"
    r"|海南航空|海航|济州航空|濟州航空|中国国航|国航|東航|东航|南航|国泰|國泰|港龙|港龍"
    r"|长荣|長榮|华航|華航|新航|泰航|越航|亚航|亞航|大韩航空|大韓航空|日航|全日空|春秋航空|吉祥航空")
_LOWER_STOPWORDS = frozenset({
    "to", "in", "on", "at", "by", "for", "my", "me", "is", "it", "do", "we", "an",
    "or", "of", "no", "so", "if", "up", "as", "be", "am", "are", "can", "the",
    "and", "via", "any", "get", "not", "now", "new", "old", "one", "two", "how",
    "who", "why", "all", "out", "off", "per", "pre", "id", "pm", "ok", "yes",
    "vs", "eta", "sar", "day", "fee", "usd", "cny", "eur", "hkd", "sgd", "aud",
    "cad", "gbp", "jpy", "krw", "thb", "vnd", "idr", "php", "myr", "inr", "sat",
    "sun", "mon", "tue", "wed", "thu", "fri", "jan", "feb", "mar", "apr", "may",
    "jun", "jul", "aug", "sep", "oct", "nov", "dec", "min", "max", "hrs", "hr",
    "im", "ive", "ill", "its", "let", "see", "say", "use", "got", "did", "has",
    "had", "was", "hi", "hey", "yo", "ur", "pls", "plz", "thx", "tho", "per",
})
# Uppercase three-letter tokens that are not countries but look like codes.
_NOT_A_CODE = frozenset({
    "SAR", "ETA", "VFS", "MRV", "CBP", "ICA", "NIA", "BNO", "OCI", "EEP", "ADS",
    "AUD", "CNY", "USD", "EUR", "JPY", "SGD", "THB", "VND", "IDR", "PHP", "MYR",
    "INR", "GBP", "CAD", "CHF", "HKD", "TWD", "KRW", "RUB", "AED", "TRY", "EGP",
    "NZD", "BRL", "MXN", "RMB", "NTD", "MOP", "IDs", "PDF", "URL", "FAQ", "VIP",
    "GPS", "SIM", "ATM", "TDAC", "MDAC", "ESTA", "ETIAS", "EVOA",
})
# Misspellings people really type, mapped straight to the country.
_TYPOS = {
    "japn": "JPN", "japam": "JPN", "jappan": "JPN", "thialand": "THA",
    "tailand": "THA", "thailnd": "THA", "veitnam": "VNM", "vietnamn": "VNM",
    "veitnamn": "VNM", "singapor": "SGP", "singapure": "SGP", "singpore": "SGP",
    "phillipines": "PHL", "philipines": "PHL", "phillippines": "PHL",
    "malasia": "MYS", "malaysa": "MYS", "indonisia": "IDN", "indonezia": "IDN",
    "koria": "KOR", "corea": "KOR", "austrailia": "AUS", "austrlia": "AUS",
    "candada": "CAN", "cananda": "CAN", "amercia": "USA", "amerika": "USA",
    "brittain": "GBR", "britian": "GBR", "germeny": "DEU", "germny": "DEU",
    "swizerland": "CHE", "switzerland": "CHE", "swtizerland": "CHE",
    "turkiye": "TUR", "türkiye": "TUR", "cambodja": "KHM", "cambodgia": "KHM",
    "dubai": "ARE", "abu dhabi": "ARE", "bangkok": "THA", "phuket": "THA",
    "hanoi": "VNM", "saigon": "VNM", "ho chi minh": "VNM", "kuala lumpur": "MYS",
    "manila": "PHL", "cebu": "PHL", "jakarta": "IDN", "london": "GBR",
    "paris": "FRA", "berlin": "DEU", "munich": "DEU", "rome": "ITA",
    "milan": "ITA", "madrid": "ESP", "barcelona": "ESP", "sydney": "AUS",
    "melbourne": "AUS", "toronto": "CAN", "vancouver": "CAN", "new york": "USA",
    "los angeles": "USA", "san francisco": "USA", "moscow": "RUS",
    "istanbul": "TUR", "cairo": "EGY", "delhi": "IND", "new delhi": "IND",
    "mumbai": "IND", "bangalore": "IND", "chennai": "IND", "kathmandu": "NPL",
    "colombo": "LKA", "phnom penh": "KHM", "siem reap": "KHM",
    "vientiane": "LAO", "luang prabang": "LAO", "yangon": "MMR",
    "zurich": "CHE", "geneva": "CHE", "vienna": "AUT", "amsterdam": "NLD",
    "brussels": "BEL", "lisbon": "PRT", "athens": "GRC", "prague": "CZE",
    "doha": "QAT", "riyadh": "SAU", "jeddah": "SAU", "muscat": "OMN",
    "tel aviv": "ISR", "auckland": "NZL", "queenstown": "NZL",
}


def _country_spans(text: str) -> list:
    """[(start, end, ISO3, is_demonym)] for every country named in the text:
    names and aliases longest first (so "south korea" beats "korea"), city
    and region words, common misspellings, and uppercase codes ("CN", "JPN").
    A word within an edit or two of a country name counts when it is long
    enough that nothing ordinary looks like it ("thialand", not "woman").
    """
    low = text.lower()
    # "Korean Air", "Hainan Airlines", 海南航空: a carrier, not a place.
    # Blanked in place so every position stays aligned with the text.
    for m in _AIRLINE_RE.finditer(low):
        low = low[:m.start()] + " " * (m.end() - m.start()) + low[m.end():]
    found = []
    taken = [False] * len(low)
    try:
        from .registry import load_registry
        names = [(e["alpha_3"], n.lower(), False)
                 for e in load_registry("countries")["entries"]
                 for n in (e.get("name"), e.get("common_name")) if n and len(n) > 2]
    except Exception:  # noqa: BLE001 - aliases alone still work
        names = []
    # Demonyms are the ascii aliases listed after the Chinese names
    # ("british", "english"; "chinese"). Marking only the last one left
    # "British passport" with no nationality, and the route inverted.
    aliases = []
    for iso, al in _ALIASES.items():
        last_cjk = max((i for i, a in enumerate(al) if not a.isascii()), default=-1)
        for i, a in enumerate(al):
            aliases.append((iso, a, a.isascii() and i > last_cjk))
    cities = [(iso.split("_")[0], c, False) for iso, cs in _CITIES.items() for c in cs]
    for iso, alias, demonym in sorted(names + aliases + cities, key=lambda x: -len(x[1])):
        start = 0
        while True:
            i = low.find(alias, start)
            if i < 0:
                break
            j = i + len(alias)
            ascii_word = alias.isascii()
            if demonym and j < len(low) and low[j] == "s" and \
                    (j + 1 >= len(low) or not low[j + 1].isalpha()):
                j += 1          # "Indians", "Australians": the plural demonym
            boundary_ok = (not ascii_word) or (
                (i == 0 or not low[i - 1].isalpha()) and (j >= len(low) or not low[j].isalpha()))
            if boundary_ok and not any(taken[i:j]):
                for k in range(i, j):
                    taken[k] = True
                found.append((i, j, iso, demonym))
            start = j
    # Cities and misspellings, same boundary rule.
    for word, iso in sorted(_TYPOS.items(), key=lambda x: -len(x[0])):
        start = 0
        while True:
            i = low.find(word, start)
            if i < 0:
                break
            j = i + len(word)
            ok = (i == 0 or not low[i - 1].isalpha()) and (j >= len(low) or not low[j].isalpha())
            if ok and not any(taken[i:j]):
                for k in range(i, j):
                    taken[k] = True
                found.append((i, j, iso, False))
            start = j
    # Uppercase codes in the original text.
    try:
        from .registry import load_registry
        alpha3 = {e["alpha_3"] for e in load_registry("countries")["entries"]}
    except Exception:  # noqa: BLE001
        alpha3 = set()
    for m in _re.finditer(r"(?<![A-Za-z])([A-Z]{2,3})(?![A-Za-z])", text):
        tok = m.group(1)
        i, j = m.start(1), m.end(1)
        if any(taken[i:j]) or tok in _NOT_A_CODE:
            continue
        iso = _ISO2.get(tok) if len(tok) == 2 else (
            tok if tok in alpha3 else _AIRPORTS.get(tok))
        if iso:
            for k in range(i, j):
                taken[k] = True
            found.append((i, j, iso, False))
    # A terse query ("cn->us via tokyo", "sg→cn business", "kr visa for cn")
    # writes codes in lowercase. Only short queries qualify, and only tokens
    # that are not ordinary words.
    if len(low.split()) <= 8:
        for m in _re.finditer(r"(?<![a-z0-9])([a-z]{2,3})(?![a-z])", low):
            tok = m.group(1)
            i, j = m.start(1), m.end(1)
            if any(taken[i:j]) or tok in _LOWER_STOPWORDS:
                continue
            up = tok.upper()
            iso = _ISO2.get(up) if len(tok) == 2 else (
                up if up in alpha3 else _AIRPORTS.get(up))
            if iso and up not in _NOT_A_CODE:
                for k in range(i, j):
                    taken[k] = True
                found.append((i, j, iso, False))
    # A long word one edit away from a country name.
    if len(found) < 2:
        import difflib
        names = {}
        for iso, al in _ALIASES.items():
            for a in al:
                if a.isascii() and len(a) >= 6 and " " not in a:
                    names[a] = iso
        for m in _re.finditer(r"[a-z]{6,}", low):
            i, j = m.start(), m.end()
            if any(taken[i:j]):
                continue
            close = difflib.get_close_matches(m.group(0), list(names), n=1, cutoff=0.88)
            if close:
                for k in range(i, j):
                    taken[k] = True
                found.append((i, j, names[close[0]], False))
    return sorted(found)


def _country_mentions(text: str) -> list:
    """[(position, ISO3, is_demonym)], the span list without its end."""
    return [(i, iso, dem) for i, _j, iso, dem in _country_spans(text)]


def served_guidance(row) -> dict:
    """What readers get from a cached row: the answer itself, or, while a
    48-hour drill has a planted value in it, the pre-plant answer stashed
    beside it. A drill must never show a reader a fake fee."""
    shadow = (row.verification or {}).get("drill_shadow") if row.verification else None
    return shadow if isinstance(shadow, dict) and shadow else (row.guidance or {})


def country_name(iso: str, script: str = "en") -> str:
    """The display name for an ISO3 code in the reply's script, or the code
    itself when the registry does not know it."""
    try:
        from .registry import load_registry
        for e in load_registry("countries")["entries"]:
            if e.get("alpha_3") == str(iso or "").upper():
                if script == "zh-TW":
                    return e.get("name_hant") or e.get("name_zh") or e.get("name") or iso
                if script == "zh":
                    return e.get("name_zh") or e.get("name") or iso
                return e.get("common_name") or e.get("name") or iso
    except Exception:  # noqa: BLE001
        pass
    return str(iso or "")


_RESIDENCE_RE = _re.compile(
    r"(?:live|living|based|resident|residing|residents|settled|working|work)"
    r"\s+(?:in|of|at)\s+(?:the\s+)?$")
_RESIDENCE_ZH = ("住在", "居住在", "定居", "常住", "在")


def _residence_at(q: str, low: str, pos: int) -> bool:
    """"I live in Dubai", "based in Singapore", 住在新加坡: where the
    traveller lives, which is neither the passport nor the trip."""
    before = low[max(0, pos - 24):pos]
    if _RESIDENCE_RE.search(before):
        return True
    zb = q[max(0, pos - 3):pos]
    return any(zb.endswith(m) for m in ("住在", "居住在", "定居", "常住"))


# What single fact is the question really after? Detected from plain words so
# the answer page can lead with it.
_FOCUS_WORDS = (
    ("fee", ("how much", "cost", "price", "fee", "cheapest", "cheaper",
             "多少钱", "多少錢", "费用", "費用", "最便宜", "便宜")),
    ("stay", ("how long can", "how many days", "length of stay", "stay",
              "住多久", "待多久", "停留", "几天能待", "幾天")),
    ("documents", ("documents", "what do i need to bring", "materials",
                   "paperwork", "材料", "资料", "資料", "文件")),
    ("processing", ("how long does it take", "processing", "how fast",
                    "fastest", "quickest", "几天出签", "幾天出簽",
                    "办理时间", "辦理時間", "多久出", "最快")),
    # The three questions T-station's real query log asks that had no focus
    # of their own (2026-09-01): how long the visa stays valid, how many
    # entries it allows, and where the application actually happens.
    ("validity", ("valid for", "validity", "how long is it valid",
                  "visa valid", "expire", "有效期", "有效多久",
                  "几年有效", "幾年有效")),
    ("entries", ("single entry", "multiple entry", "how many entries",
                 "re-enter", "reenter", "单次", "多次", "几次入境",
                 "幾次入境", "能进几次", "能進幾次")),
    ("channel", ("where do i apply", "where to apply", "how do i apply",
                 "how to apply", "which website", "在哪申请", "在哪申請",
                 "怎么申请", "怎麼申請", "去哪办", "去哪辦", "哪里办", "哪裡辦")),
)

_TRANSIT_MARKERS = ("via ", "through ", "layover in ", "stopover in ",
                    "transit in ", "transiting ", "transit through ",
                    "connect in ", "connecting in ")
# The marker has to sit right before the place: "via Dubai" is a stopover,
# "via VFS for Spain" is a visa centre, and reading the second as transit
# turned a tourist's Schengen question into an airport-transit answer.
_TRANSIT_RE = _re.compile(
    r"(?:\bvia|\bthrough|layover in|stopover in|transit(?:ing)?(?: in| through| at)?"
    r"|connect(?:ing)? in)\s+(?:the\s+)?$")
# A demonym right before one of these describes the destination's own
# paperwork ("Australian visa", "Korean group tour"), not the traveller.
_DESTINATION_HINT_RE = _re.compile(
    r"^[a-z]+\s+(?:sar\s+)?(?:(?:standard|visitor|tourist|business|work|student"
    r"|residence|resident|transit|entry|visit|e|short[- ]stay|long[- ]stay|multiple[- ]entry)\s+){0,2}"
    r"(?:visas?|e-?visas?|eta\b|esta\b|embassy|consulate|immigration|border|entry"
    r"|group tour|policy|scheme|exemption|waiver|arrival card|transit|government"
    r"|authorities|side|permit|pass\b)")
# 韩国签证, 中国领事馆, 香港入境处: the country's own paperwork or office.
_ZH_HINT_SUFFIX = ("签证", "簽證", "领事馆", "領事館", "使馆", "使館", "大使馆", "大使館",
                   "签证中心", "簽證中心", "移民局", "海关", "海關", "入境处", "入境處",
                   "过境签", "過境簽", "落地签", "落地簽", "对", "對")
# 在意大利办, 喺香港領事館申請: where the paperwork is lodged, not a leg of the trip.
_ZH_PLACE_PREFIX = ("在", "喺", "係")
_HOLDER_BEFORE = ("have a ", "have an ", "has a ", "hold a ", "holds a ",
                  "holding a ", "holding an ", "using a ", "using an ",
                  "i am ", "i'm ", "we are ")
# "with a X passport", "on a X passport": these prepositions mark the
# passport only when a document noun follows; "on a Thailand holiday" is a
# trip.
_HOLDER_BEFORE_NOUN = ("with a ", "with an ", "on a ", "on an ")
_PASSPORT_AFTER_RE = _re.compile(
    r"(?:(?!(?:for|to|as|and|or|via|in|into|at|from|with|on|by|of|vs|versus)\s)[a-z.'-]+\s+){1,3}(?:sar\s+)?"
    r"(?:passports?|citizens?|citizenship|nationals?|nationality|applicants?"
    r"|travell?ers?|tourists?|residents?|visitors?|holders?)\b")
_PASSPORT_NOUN_RE = _re.compile(
    r"(?:[a-z.'-]+\s+){1,2}(?:sar\s+)?(?:passports?|travel documents?|documents? of identity|permits?)\b")
_TRIP_HINT_RE = _re.compile(
    r"^[a-z]+\s+(?:holidays?|trips?|tours?|vacations?|visits?|cruises?|flights?"
    r"|itinerary|stopovers?|layovers?|getaway|honeymoon|break|package|adventure|run)\b")


_ZH_NATIONAL_SUFFIX = ("人", "公民", "护照", "護照", "居民", "国籍", "國籍", "籍",
                       "同胞", "游客", "遊客", "旅客")


def _marks_nationality(q: str, low: str, pos: int, demonym: bool,
                       end: int | None = None, strict: bool = False) -> bool:
    """Whether the country mentioned at pos is the traveller's own.

    English says it several ways: a demonym ("Chinese", "Indians"), "from
    X", "issued by X", "with a / hold a / for X passport", "X citizens",
    "X applicants". Chinese says X护照, 持X, X人, X公民, 内地居民. A demonym
    that names the destination's paperwork ("Australian visa") is not the
    traveller."""
    before = low[max(0, pos - 12):pos]
    after = low[pos:pos + 48]
    if _DESTINATION_HINT_RE.match(after):
        return False
    if end is not None:
        tail = q[end:end + 4]
        if any(tail.startswith(sfx) for sfx in _ZH_HINT_SUFFIX):
            # 韩国签证, 马来西亚对中国免签: the country whose paperwork or
            # policy this is, not the traveller.
            return False
        if any(tail.startswith(sfx) for sfx in _ZH_NATIONAL_SUFFIX):
            # 日本人去中国: the suffix makes the country the traveller,
            # unless a destination marker sits right before it (去日本人多吗
            # is not a nationality, but nobody writes that).
            if q[max(0, pos - 1):pos] not in ("去", "到", "赴", "往", "飞", "飛"):
                return True
        if q[max(0, pos - 1):pos] in ("对", "對"):
            return True          # 马来西亚对中国免签: 中国 is the traveller
    if strict:
        return demonym or "from " in before or before.rstrip().endswith("from")
    if demonym:
        return True
    seg = q[pos:pos + 8]
    k = seg.find("护照")
    if k < 0:
        k = seg.find("護照")
    passport_after = k > 0 and not any(ch in seg[:k] for ch in "，,。.、;； ")
    if _TRIP_HINT_RE.match(after):
        return False          # "on a Thailand holiday": the trip, not the passport
    return ("from " in before or before.rstrip().endswith("from")
            or "issued by" in before
            or "持" in q[max(0, pos - 3):pos]
            or passport_after
            or bool(_PASSPORT_AFTER_RE.match(after))
            or any(before.endswith(m) for m in _HOLDER_BEFORE)
            or (any(before.endswith(m) for m in _HOLDER_BEFORE_NOUN)
                and bool(_PASSPORT_NOUN_RE.match(after))))


def _doc_from_text(q: str, low: str) -> str | None:
    """The travel document named in the words, or None. Shared by the fresh
    parse and the follow-up path so the vocabulary can never drift. Order
    matters: 公务普通护照 must win before 公务护照, and 旅行证 must not fire on
    the generic word 旅行证件."""
    if "公务普通护照" in q or "公務普通護照" in q or "official ordinary passport" in low \
            or "因公普通护照" in q or "因公普通護照" in q:
        return "official_ordinary_passport"
    if "diplomatic" in low or "外交护照" in q or "外交護照" in q:
        return "diplomatic_passport"
    if "official passport" in low or "service passport" in low \
            or "公务护照" in q or "公務護照" in q:
        return "service_passport"
    if "儿童护照" in q or "兒童護照" in q or "child passport" in low:
        return "child_passport"
    if "temporary passport" in low or "临时护照" in q or "臨時護照" in q:
        return "temporary_passport"
    if "emergency passport" in low or "紧急护照" in q or "緊急護照" in q:
        return "emergency_passport"
    if "签证身份书" in q or "簽證身份書" in q or "document of identity" in low \
            or _re.search(r"\bd\.?i\.? holders?\b", low):
        return "identity_certificate"
    if "身份证明书" in q or "身份證明書" in q:
        return "identity_certificate"
    if ("旅行证" in q and "旅行证件" not in q) or ("旅行證" in q and "旅行證件" not in q) \
            or "prc travel document" in low:
        return "prc_travel_document"
    if "refugee" in low or "难民" in q or "難民" in q:
        return "refugee_travel_document"
    if "stateless" in low or "无国籍" in q or "無國籍" in q:
        return "stateless_travel_document"
    if "laissez-passer" in low or "laissez passer" in low \
            or "联合国通行证" in q or "聯合國通行證" in q:
        return "laissez_passer"
    return None

_MONTHS = {m: i + 1 for i, m in enumerate((
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december"))}
_MONTHS.update({m[:3]: v for m, v in list(_MONTHS.items())})


def _extract_arrival(question: str) -> str | None:
    """When the words say WHEN, the answer must be for THEN: policies carry
    end dates (China's visa-free window runs to 31 Dec 2026), and the cache
    is bucketed by policy month. Deterministic patterns only; absent means
    now, never a guess."""
    from datetime import date
    q = str(question or "").lower()
    today = date.today()
    m = _re.search(r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
                   r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|"
                   r"nov(?:ember)?|dec(?:ember)?)\.?\s*(?:of\s*)?(20\d\d)?\b", q)
    if m and (m.group(2) or "next year" in q or "明年" in question):
        month = _MONTHS[m.group(1)[:3]]
        year = int(m.group(2)) if m.group(2) else today.year + 1
        return f"{year:04d}-{month:02d}-01"
    if m and m.group(1) not in ("may",):      # bare month: the next occurrence
        month = _MONTHS[m.group(1)[:3]]
        year = today.year + (1 if month < today.month else 0)
        return f"{year:04d}-{month:02d}-01"
    zh = _re.search(r"(明年|今年)?\s*([一二三四五六七八九十]{1,2}|\d{1,2})月"
                    r"\s*(\d{1,2})?\s*[日号號]?", question)
    if zh:
        raw = zh.group(2)
        nums = {"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,
                "十":10,"十一":11,"十二":12}
        month = nums.get(raw) or (int(raw) if raw.isdigit() else None)
        if month and 1 <= month <= 12:
            year = today.year + (1 if zh.group(1) == "明年"
                                 or month < today.month else 0)
            # "12月15日" answers for THE DAY: policies carry end dates and a
            # 12月31日 question is not a 1月1日 question.
            day = int(zh.group(3)) if zh.group(3) and 1 <= int(zh.group(3)) <= 31 else 1
            return f"{year:04d}-{month:02d}-{day:02d}"
    if "next month" in q or "下个月" in question or "下個月" in question:
        y, mo = (today.year, today.month + 1) if today.month < 12 else (today.year + 1, 1)
        return f"{y:04d}-{mo:02d}-01"
    return None


def _question_focus(question: str) -> str | None:
    low = str(question or "").lower()
    for name, words in _FOCUS_WORDS:
        if any(w in low or w in question for w in words):
            return name
    return None


def _deterministic_route(question: str) -> dict | None:
    """A route read straight from the words, or None when the text does not
    name two places. Instant; no model call."""
    q = str(question or "").strip()
    low = q.lower()
    spans = _country_spans(q)
    # Where the traveller lives is neither the passport nor the trip.
    residence = None
    kept = []
    for i, j, iso, dem in spans:
        if _residence_at(q, low, i) and not dem:
            residence = residence or iso
        else:
            kept.append((i, j, iso, dem))
    spans = kept
    mentions = [(i, iso, dem) for i, _j, iso, dem in spans]
    isos = []
    for _pos, iso, _d in mentions:
        if iso not in isos:
            isos.append(iso)
    implied_dest = None
    if len(isos) == 1:
        # "esta singapore", "k-eta for chinese", "evisitor": the authorisation
        # names the destination that issues it.
        implied = None
        if _re.search(r"\besta\b", low):
            implied = "USA"
        elif _re.search(r"\bk-?eta\b", low):
            implied = "KOR"
        elif "evisitor" in low:
            implied = "AUS"
        elif "visit japan web" in low:
            implied = "JPN"
        if implied and implied != isos[0]:
            spans = spans + [(len(q), len(q), implied, False)]
            mentions = mentions + [(len(q), implied, False)]
            isos = isos + [implied]
            implied_dest = implied
    if len(isos) == 1 and len(spans) >= 2:
        # "Chinese passport going to China", 台灣護照回台灣: the one country
        # is named twice, once as the passport and once as the trip. That
        # is a whole route, not a missing fact.
        one = isos[0]
        as_nat = any(_marks_nationality(q, low, i, dem, j) for i, j, _iso, dem in spans)
        as_dest = any(
            not _marks_nationality(q, low, i, dem, j) and (
                any(w in low[max(0, i - 10):i] for w in (" to ", "to ", "visit", "going", "travel", "back to"))
                or q[max(0, i - 1):i] in ("去", "到", "赴", "往", "回", "飞", "飛")
                or q[max(0, i - 2):i] in ("前往", "飞往", "飛往", "回到"))
            for i, j, _iso, dem in spans)
        if as_nat and as_dest:
            return {"understood": True, "nationality": one, "destination": one,
                    "travel_purpose": "tourism",
                    "travel_document_type": _doc_from_text(q, low) or "ordinary_passport",
                    "transit_countries": [], "confident": True,
                    "arrival_date": _extract_arrival(q), "residence": None,
                    "focus": _question_focus(q), "read_by": "deterministic"}
    doc_named = _doc_from_text(q, low)
    nat = None
    dest = implied_dest
    implied_nat = None
    if doc_named == "identity_certificate":
        implied_nat = "HKG"      # a Hong Kong Document of Identity
    elif doc_named in ("official_ordinary_passport", "prc_travel_document"):
        implied_nat = "CHN"      # 因公普通护照, 旅行证: PRC documents
    if implied_nat and implied_nat not in isos:
        # The document names its issuer even when the country is not written.
        isos = [implied_nat] + isos
        spans = [(0, 0, implied_nat, False)] + spans
        mentions = [(0, implied_nat, False)] + mentions
        nat = implied_nat
    if len(isos) < 2:
        return None
    # Nationality: a demonym ("Chinese passport", "Indians"), "with a X
    # passport", "from X", "for X citizens", X护照 ("中国护照"), X人, or the
    # country standing right before a named non-passport document
    # (香港签证身份书). A demonym on the destination's paperwork
    # ("Australian visa for Chinese applicants") is not the traveller.
    # A demonym or "from X" outranks a bare country followed by a holder
    # word ("Vietnam visitors from India": India is the traveller; "UK
    # visitor visa for an Indian citizen": India again). A country named
    # only as paperwork ("Korean visa") or as the place a form is lodged
    # (在意大利办) is never the passport.
    hinted = set()
    placed = set()
    for pos, end, iso, demonym in spans:
        after = low[pos:pos + 48]
        tail = q[end:end + 4]
        if _DESTINATION_HINT_RE.match(after) or any(tail.startswith(s) for s in _ZH_HINT_SUFFIX):
            hinted.add((pos, iso))
        if q[max(0, pos - 1):pos] in _ZH_PLACE_PREFIX and any(
                tail.startswith(s) for s in ("办", "辦", "申请", "申請", "领事", "領事",
                                             "使馆", "使館", "签证中心", "簽證中心", "递交", "遞交")):
            placed.add((pos, iso))
    for strict in (True, False):
        for pos, end, iso, demonym in spans:
            if (pos, iso) in placed:
                continue
            if _marks_nationality(q, low, pos, demonym, end, strict):
                nat = iso
                break
        if nat is not None:
            break
    if nat is None and doc_named and doc_named != "ordinary_passport":
        # 香港签证身份书 / "refugee travel document issued by Germany":
        # the issuing place right before the document phrase is who holds it.
        for kw in ("签证身份书", "簽證身份書", "身份证明书", "旅行证", "旅行證",
                   "refugee", "stateless", "document of identity"):
            k = q.find(kw) if not kw.isascii() else low.find(kw)
            if k > 0:
                prior = [m for m in mentions if m[0] < k]
                if prior:
                    nat = prior[-1][1]
                break
    # Stopovers BEFORE the destination falls back: "via singapore",
    # "layover in dubai", 经/途经, and 在X转机 where the marker FOLLOWS the
    # place. Otherwise the fallback claims the stopover as the destination
    # and the whole route inverts.
    transit = []
    for pos, iso, _d in mentions:
        if iso == nat:
            continue
        before = low[max(0, pos - 24):pos]
        zh_before = q[max(0, pos - 3):pos]
        zh_after = q[pos:pos + 8]
        if _TRANSIT_RE.search(before) or \
                any(z in zh_before for z in ("经", "經", "途经", "途經")) or \
                any(z in zh_after for z in ("转机", "轉機", "中转", "中轉")):
            if iso not in transit:
                transit.append(iso)
    # Destination: "to X", "go to X", "visit X", "in X", 去X / 到X / 赴X.
    for pos, iso, _d in mentions:
        if iso == nat or iso in transit:
            continue
        if (pos, iso) in placed:
            continue
        before = low[max(0, pos - 10):pos]
        if any(w in before for w in (" to ", "to ", "visit", " in ", "going", "travel",
                                     "fly", "→", "->", "trip", " for ")) \
                or q[max(0, pos - 1):pos] in ("去", "到", "赴", "往", "飞", "飛", "回") \
                or q[max(0, pos - 2):pos] in ("前往", "飞往", "飛往", "到达", "抵达"):
            dest = iso
            break
    nat_marked, dest_marked = nat is not None, dest is not None
    if nat is None:
        # Only a country named as a plain place can be the passport by
        # default. If every remaining mention is paperwork or an office, the
        # passport is genuinely unstated.
        plain = [i for i in isos if i != dest and i not in transit and any(
            (p, ii) not in hinted and (p, ii) not in placed
            for p, _e, ii, _d in spans if ii == i)]
        nat = next(iter(plain), None)
        if nat is None:
            return None
    if dest is None:
        dest = next((i for i in isos if i != nat and i not in transit and any(
            (p, ii) not in placed for p, _e, ii, _d in spans if ii == i)), None)
    if dest is None and transit:
        # A pure airside question ("只在新加坡转机不出机场"): the stopover IS
        # the place being asked about.
        dest = transit[0]
    if dest is None:
        return None          # every other place is an office or paperwork
    purpose = "tourism"
    for name, words in _PURPOSE_WORDS:
        if any(w in low or w in q for w in words):
            purpose = name
            break
    if purpose == "tourism" and len(low.split()) <= 4 and _re.search(r"\bwork\b", low):
        purpose = "work"          # "CN PH work": a terse query says the purpose in one word
    if purpose == "transit" and transit and dest not in transit:
        # "经迪拜转机去法国" asks about the whole trip: the stopover rides in
        # transit_countries and is answered by the transit rule; the
        # destination leg keeps its own purpose. Without this the answer
        # inverted to "airport transit visa for FRANCE" and said nothing
        # about the stopover.
        purpose = "tourism"
    doc = doc_named or "ordinary_passport"
    if dest in transit:
        # The stopover is the whole question: keep the transit purpose and
        # do not also list the place as its own stopover.
        transit = [t for t in transit if t != dest]
        purpose = "transit"
    # SURE only when the words themselves say which is which: both sides
    # marked ("from X ... to Y", a demonym, 持X护照去Y), or exactly two
    # countries with at least one marker. "Meeting my friend from Paris in
    # New York" mentions two countries and marks neither as a route — that
    # is the model's question, not this function's.
    extra = len(isos) - len(transit)          # countries that are not stopovers
    confident = (extra == 2 and (nat_marked or dest_marked)) or \
        (extra <= 2 and nat_marked and dest_marked)
    return {"understood": True, "nationality": nat, "destination": dest,
            "travel_purpose": purpose, "travel_document_type": doc,
            "transit_countries": transit[:5], "confident": confident,
            "arrival_date": _extract_arrival(q),
            "residence": residence if residence not in (nat, dest) else None,
            "focus": _question_focus(q), "read_by": "deterministic"}


def parse_question_with_context(question: str, context: dict | None,
                                *, timeout: float = 20.0) -> dict:
    """A follow-up like "what about business?" or "那费用多少" or "and if we
    transit through Hong Kong?" modifies the route ON SCREEN rather than
    being refused for not naming two places, and a fresh question missing
    one slot borrows it from the context instead of dead-ending.
    Deterministic only — a follow-up never guesses."""
    q = str(question or "").strip()
    ctx = context or {}
    focus = _question_focus(q)
    ctx_nat = str(ctx.get("nationality") or "").strip().upper()
    ctx_dest = str(ctx.get("destination") or "").strip().upper()
    if (ctx_nat or ctx_dest) and not (ctx_nat and ctx_dest):
        # A clarify was answered. Ellis asked for exactly one missing fact
        # ("Which country issued your passport?"), so a reply naming ONE
        # country fills that slot and the pending slot survives. Without
        # this, "Australia ETA?" -> "which passport?" -> "China" forgot
        # Australia entirely and refused the asker a second time
        # (Trip.com evaluation 2026-08-31, finding 4).
        mentions = _country_mentions(q)
        isos = []
        for _pos, iso, _dem in mentions:
            if iso not in isos:
                isos.append(iso)
        if len(isos) == 1:
            one = isos[0]
            known = ctx_nat or ctx_dest
            if one != known:
                nat = one if not ctx_nat else ctx_nat
                dest = one if not ctx_dest else ctx_dest
                low = q.lower()
                purpose = None
                for name, words in _PURPOSE_WORDS:
                    if any(w in low or w in q for w in words):
                        purpose = name
                        break
                doc = _doc_from_text(q, low)
                return {"understood": True, "nationality": nat,
                        "destination": dest,
                        "travel_purpose": purpose
                            or ctx.get("travel_purpose") or "tourism",
                        "travel_document_type": doc
                            or ctx.get("travel_document_type")
                            or "ordinary_passport",
                        "transit_countries": [str(t).upper() for t in
                                              (ctx.get("transit_countries")
                                               or []) if t][:5],
                        "focus": focus, "read_by": "context-slot"}
            # Restating the country already known keeps the same fact
            # missing: fall through so the asker is asked for it again.
    if ctx.get("nationality") and ctx.get("destination"):
        low = q.lower()
        mentions = _country_mentions(q)
        isos = []
        for _pos, iso, _dem in mentions:
            if iso not in isos:
                isos.append(iso)
        purpose = None
        for name, words in _PURPOSE_WORDS:
            if any(w in low or w in q for w in words):
                purpose = name
                break
        doc = _doc_from_text(q, low)
        if doc is None and ("ordinary" in low or "普通护照" in q or "普通護照" in q):
            doc = "ordinary_passport"
        if len(isos) <= 1 and (isos or purpose or doc or focus):
            nat = str(ctx.get("nationality")).upper()
            dest = str(ctx.get("destination")).upper()
            transit = [str(t).upper() for t in
                       (ctx.get("transit_countries") or []) if t]
            if isos:
                one = isos[0]
                pos = mentions[0][0]
                lead = low[max(0, pos - 24):pos]
                zh_after = q[pos:pos + 8]
                if _TRANSIT_RE.search(lead) or \
                        any(z in q[max(0, pos - 3):pos] for z in ("经", "經", "途经", "途經")) or \
                        any(z in zh_after for z in ("转机", "轉機", "中转", "中轉")):
                    # "and if we transit through Hong Kong?" adds a stopover;
                    # the trip on screen keeps its destination.
                    if one not in transit and one != dest:
                        transit = transit + [one]
                    if purpose == "transit":
                        purpose = None
                elif _marks_nationality(q, low, pos, mentions[0][2]) and (
                        mentions[0][2] or not _re.search(
                            r"\b(?:for|to)\s+(?:tourists?|visitors?|travell?ers?)\b", low)):
                    # "what about my wife, she has an Indian passport": the
                    # passport changes, the destination on screen stays.
                    # "what about Japan for tourists?" changes the trip.
                    nat = one
                elif one != nat:
                    dest = one
            return {"understood": True, "nationality": nat, "destination": dest,
                    "travel_purpose": purpose or ctx.get("travel_purpose", "tourism"),
                    "travel_document_type": doc or ctx.get("travel_document_type",
                                                           "ordinary_passport"),
                    "transit_countries": transit[:5], "focus": focus,
                    "read_by": "context"}
    full = parse_question(q, timeout=timeout)
    if full.get("understood"):
        return full
    # A fresh question missing exactly one slot borrows it from whatever
    # context exists ("去法国签证要准备什么材料" + a known CHN nationality).
    nat = full.get("nationality") or str(ctx.get("nationality") or "").upper()
    dest = full.get("destination") or str(ctx.get("destination") or "").upper()
    if nat and dest:
        low = q.lower()
        purpose = None
        for name, words in _PURPOSE_WORDS:
            if any(w in low or w in q for w in words):
                purpose = name
                break
        return {"understood": True, "nationality": nat, "destination": dest,
                "travel_purpose": purpose or ctx.get("travel_purpose", "tourism"),
                "travel_document_type": _doc_from_text(q, low)
                    or ctx.get("travel_document_type", "ordinary_passport"),
                "transit_countries": [], "focus": focus,
                "read_by": "context-merge"}
    return full


# Questions repeat — the same tester types the same sentence, demos rerun the
# same lines. A parse (even one the model produced) is remembered by its
# normalized wording, so the repeat costs nothing.
_PARSE_CACHE: dict = {}


def _parse_cache_key(q: str) -> str:
    return _re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", q.lower()).strip()


def parse_question(question: str, *, timeout: float = 20.0) -> dict:
    """Turn 'What visa for tourism in Japan with a Chinese passport?' into a
    route dict the Database lookup understands. Deterministic shape-check on
    the model's answer; never invents a country the user did not name."""
    q = str(question or "").strip()
    if not q:
        raise GuidanceUnavailable("no question to read")
    ck = _parse_cache_key(q)
    hit = _PARSE_CACHE.get(ck)
    if hit is not None:
        return dict(hit)
    direct = _deterministic_route(q)
    if direct is not None and direct.get("confident"):
        return direct
    # Anything the fast matcher is not SURE about goes to the model, whose
    # whole job is messy wording — asides, typos, mixed languages. Held to a
    # short budget so a miss answers in seconds; if the model cannot read it
    # either, the matcher's best two-country guess beats a refusal.
    raw = _call(_ASK_SYSTEM, json.dumps({"question": q[:500]}),
                timeout=min(timeout, 10.0), max_tokens=800)
    if not isinstance(raw, dict):
        raise GuidanceUnavailable("could not read the question")
    nat = str(raw.get("nationality") or "").strip().upper()
    dest = str(raw.get("destination") or "").strip().upper()
    purpose = str(raw.get("travel_purpose") or "tourism").strip().lower()
    if len(nat) != 3 or len(dest) != 3:
        if direct is not None:
            return direct          # the matcher's guess beats a refusal
        return {"understood": False, "nationality": nat, "destination": dest}
    # The words outrank the model on the document: a named 旅行证 or refugee
    # travel document must never collapse to ordinary_passport.
    doc = _doc_from_text(q, q.lower()) \
        or str(raw.get("travel_document_type") or "ordinary_passport").strip()
    transit = [str(c).strip().upper() for c in
               (raw.get("transit_countries") or [])
               if isinstance(c, str) and len(str(c).strip()) == 3][:5]
    focus = raw.get("focus")
    out = {"understood": True, "nationality": nat, "destination": dest,
           "travel_purpose": purpose if purpose in
           ("tourism", "business", "family_visit", "study", "work",
            "transit", "other")
           else "tourism", "travel_document_type": doc or "ordinary_passport",
           "transit_countries": transit,
           "arrival_date": raw.get("arrival_date")
           if _re.fullmatch(r"20\d\d-\d\d-\d\d", str(raw.get("arrival_date") or ""))
           else _extract_arrival(q),
           "focus": focus if focus in ("fee", "stay", "documents",
                                       "processing", "validity", "entries",
                                       "channel") else _question_focus(q),
           "read_by": "model"}
    if len(_PARSE_CACHE) > 500:
        _PARSE_CACHE.clear()
    _PARSE_CACHE[ck] = dict(out)
    return out
