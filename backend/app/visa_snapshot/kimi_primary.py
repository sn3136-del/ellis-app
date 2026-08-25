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
import time
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from ..config import REAL_ONLY_MODES, settings
from .models import KimiRouteGuidanceCache

STATUS_PRIMARY = "KIMI_PRIMARY"
STATUS_UNCERTAIN = "KIMI_UNCERTAIN"
STATUS_UNAVAILABLE = "KIMI_UNAVAILABLE"
STATUS_TIMEOUT = "KIMI_TIMEOUT"

DISPOSITIONS = ("VISA_REQUIRED", "VISA_EXEMPT",
                "ELECTRONIC_AUTHORIZATION_REQUIRED", "CONDITIONAL")

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
    confirms it. Trip.com's requirements doc asked for this gate; the owner
    decided Ellis must ALWAYS answer, so it is OFF unless switched on. The
    confidence flag is still computed and still feeds the operator queue and
    the grounded recheck — only the withholding is switched."""
    return os.getenv("ELLIS_DATABASE_HOLD_LOW_CONFIDENCE", "0").strip() == "1"


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
passport_validity_requirement: {"kind": "valid_on_arrival"|"valid_through_departure"|"months_after_arrival"|"months_after_departure", "months": integer|null}
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
route could predate a change — the rule is volatile, recently announced, or
politically driven — say so in uncertainty and rate confidence low rather
than presenting a possibly outdated rule as current.
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
    try:
        return provider._chat(system, user, json_mode=True, timeout=timeout,
                              max_tokens=max_tokens, model=model)
    except KimiTimeout as e:
        raise GuidanceTimeout() from e
    except KimiHttpError as e:
        from .. import provider_errors
        raise GuidanceProviderError(
            provider_errors.user_error(f"kimi moonshot HTTP {e.status}")) from e


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
    facts = {}
    for k in ROUTE_FACT_KEYS:
        v = (route or {}).get(k)
        if v not in (None, "", []):
            facts[k] = v
    facts.setdefault("travel_purpose", "tourism")
    facts.setdefault("visa_category",
                     category_for_purpose(facts.get("travel_purpose")))
    a, d = _iso(facts.get("arrival_date")), _iso(facts.get("departure_date"))
    if a and d and d > a:
        facts["trip_duration_days"] = (d - a).days
    facts["consular_jurisdiction"] = (route or {}).get("consular_jurisdiction") or "default"
    # The model must reason about TIME: policies move, and its knowledge has a
    # cutoff. Today's date lets it say "this may have changed" honestly.
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
        if exempt and k in ("processing_time", "government_fee") and v in (None, "", {}):
            # A visa-free route has no application to process or pay for;
            # demanding these marked 26 correct answers uncertain in one sweep.
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
    if not isinstance(clean.get("passport_validity_requirement"), dict):
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
    contradictions = []
    # Narrow, precise checks — a visa-exempt route must not carry a visa
    # application form or a positive government visa fee.
    if clean.get("disposition") == "VISA_EXEMPT":
        forms = [str(f).lower() for f in (clean.get("forms") or [])]
        if any("visa application" in f for f in forms):
            contradictions.append("disposition VISA_EXEMPT but forms include a visa application")
        fee = clean.get("government_fee") or {}
        if isinstance(fee, dict) and (fee.get("amount") or 0) and \
                "visa" in str(clean.get("visa_category", "")).lower():
            contradictions.append("disposition VISA_EXEMPT but a government visa fee is quoted")
        if clean.get("route_workflow_type") not in ("visa_exempt_preparation", "conditional"):
            contradictions.append("disposition VISA_EXEMPT but route_workflow_type "
                                  f"is {clean.get('route_workflow_type')}")
    ps_days = clean.get("permitted_stay_days")
    if ps_days is not None and (not isinstance(ps_days, (int, float)) or ps_days < 0
                                or ps_days > 3660):
        clean.pop("permitted_stay_days", None)

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
    arrival = str(route.get("arrival_date") or route.get("policy_period") or "")
    policy_month = arrival[:7] or "unknown"          # YYYY-MM: the policy date bucket
    parts = [
        str(route.get("passport_nationality", "")).upper(),
        str(route.get("lawful_country_of_residence", "")).upper(),
        str(route.get("destination_country", "")).upper(),
        str(route.get("travel_purpose", "tourism")).lower(),
        str(route.get("consular_jurisdiction") or "default").lower(),
        policy_month,
        CACHE_VERSION,
    ]
    # A stopover changes the answer (it can add a transit-visa requirement), so
    # it must change the key — otherwise a transit query is served the cached
    # non-transit answer and the transit question is never actually asked. The
    # suffix is APPENDED only when transit exists, so plain routes keep their
    # existing key and the shipped warm cache stays valid.
    transit = sorted({str(c).upper() for c in
                      (route.get("transit_countries") or []) if c})
    if transit:
        parts.append("via:" + ",".join(transit))
    # Document type likewise: a diplomatic passport is a different answer.
    doc = str(route.get("travel_document_type") or "ordinary_passport").lower()
    if doc and doc != "ordinary_passport":
        parts.append("doc:" + doc)
    return "|".join(parts)


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


def _result(status: str, guidance: dict, *, cached: bool, stale: bool,
            missing=None, contradictions=None, model: str = "",
            advisories=None, elapsed_seconds: float | None = None,
            released: bool = False) -> dict:
    # Exactly one Kimi pass produced this — the verification field says so
    # honestly for a complete decision and is empty otherwise.
    decided = status == STATUS_PRIMARY
    verification = {"passes": 1, "label": VERIFIED_LABEL} if decided else {}
    out = {
        "status": status,
        "ai_generated": True,
        "label": VERIFIED_LABEL if decided else "AI-generated route guidance",
        "guidance": guidance,
        "workflow_plan": derive_workflow_plan(guidance) if guidance else [],
        # The 3-5 key steps a traveller actually follows, deduplicated and
        # ordered (Trip.com's spec). The raw arrays stay in the guidance for
        # anything that needs them.
        "apply_steps": canonical_steps(guidance) if guidance else [],
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
        "review_required": bool(guidance) and not released
                           and str((guidance or {}).get("confidence", "")).lower() == "low",
        "operator_released": released,
    }
    # `held` is what actually withholds: the flag above AND the switch. With
    # the switch off (the default) a low-confidence answer is shown like any
    # other, still flagged for the operator queue and the grounded recheck.
    out["held"] = bool(out["review_required"]) and hold_enabled()
    if elapsed_seconds is not None:
        out["elapsed_seconds"] = round(elapsed_seconds, 2)
    return out


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
    if prov is None:
        return out
    out = dict(out)
    out["guidance"] = merged
    out["source_verified"] = prov
    if "disposition" in prov["fields"]:
        out["review_required"] = False
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
    head = _SYSTEM + f"\n\nTHIS CALL ({label}): fill ONLY these fields and set every other field to null: {', '.join(fields)}. Keep the JSON shape exactly as specified."
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
    merged = dict(core)
    if isinstance(detail, dict):
        merged.update(_detail_consistent(core, detail))
    clean, missing, contradictions = validate_answer(merged, detail_known=isinstance(detail, dict))
    status = STATUS_PRIMARY if clean.get("disposition") and not missing and not contradictions \
        else STATUS_UNCERTAIN
    ttl = int(os.getenv("ELLIS_KIMI_GUIDANCE_TTL_DAYS", TTL_DAYS) or TTL_DAYS) \
        if status == STATUS_PRIMARY else UNCERTAIN_TTL_DAYS
    ver = dict(row.verification or {})
    ver.pop("detail_pending", None)
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
    threading.Thread(target=_work, name="ellis-detail-stage", daemon=True).start()


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
    rows = [r for r in rows if r.guidance and f"|{CACHE_VERSION}" in (r.cache_key or "")]
    if not rows:
        return None
    def rank(r):
        parts = r.cache_key.split("|")
        same_purpose = len(parts) > 3 and parts[3] == want_purpose
        plain_doc = "doc:" not in r.cache_key
        no_transit = "via:" not in r.cache_key
        complete = r.status == STATUS_PRIMARY
        return (not complete, not same_purpose, not plain_doc, not no_transit)
    best = sorted(rows, key=rank)[0]
    out = _result(best.status, best.guidance, cached=True, stale=_is_stale(best),
                  missing=best.missing_fields, contradictions=best.contradictions,
                  model=best.model,
                  advisories=deterministic_advisories(route, best.guidance or {}))
    out["approximate_for"] = {
        "asked": {"travel_purpose": want_purpose,
                  "travel_document_type": route.get("travel_document_type"),
                  "arrival_date": route.get("arrival_date")},
        "served": best.route or {},
    }
    return apply_portal_fallback(apply_verified_overrides(out, route), route)


def get_route_guidance(db, route: dict, *, force_refresh: bool = False,
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
    if row is not None and not force_refresh:
        released = bool((row.verification or {}).get("operator_released"))
        gc = (row.verification or {}).get("grounded_check")
        out = apply_portal_fallback(apply_verified_overrides(_result(
            row.status, row.guidance, cached=True, stale=_is_stale(row),
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
            out["grounded_check"] = {k: gc.get(k) for k in
                                     ("at", "source_url", "consistent",
                                      "changed_fields", "disputed_fields")}
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
        if row is None:
            row = KimiRouteGuidanceCache(cache_key=key)
            db.add(row)
        row.route = {k: route.get(k) for k in (
            "passport_nationality", "lawful_country_of_residence", "destination_country",
            "visa_category", "travel_purpose", "arrival_date", "consular_jurisdiction")}
        row.status = status
        row.guidance = clean
        row.missing_fields = missing
        row.contradictions = contradictions
        row.model = model
        row.verification = {"passes": 1, "label": VERIFIED_LABEL,
                            **({"detail_pending": True} if staged else {})}
        row.generated_at = now
        row.fresh_until = now + timedelta(days=ttl)
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
    db = db_factory()
    try:
        from . import freshness
        row_key = cache_key(route)
        row = db.execute(select(KimiRouteGuidanceCache).where(
            KimiRouteGuidanceCache.cache_key == row_key)).scalars().first()
        outcome = None
        try:
            outcome = freshness.recheck_route(db, route)
        except Exception:  # noqa: BLE001 - grounded renewal is best-effort
            outcome = None
        ok = bool(outcome) and outcome.get("outcome") == "checked"
        grounded_before = row is not None and freshness.has_been_grounded(row)
        if not ok and not grounded_before:
            # Never grounded and the page path failed (usually: no source
            # URL): the old memory refresh is still better than serving a
            # schema-stale row forever.
            get_route_guidance(db, route, force_refresh=True)
    except Exception:  # noqa: BLE001 - background refresh is best-effort
        pass
    finally:
        db.close()


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
    "USA": ("usa", "us", "u.s.", "america", "united states", "the states", "美国", "美國", "american"),
    "GBR": ("uk", "u.k.", "britain", "great britain", "england", "united kingdom", "英国", "英國", "british", "english"),
    "CHN": ("china", "mainland china", "prc", "中国", "中國", "中国大陆", "chinese"),
    "HKG": ("hong kong", "hongkong", "hk", "香港", "hong konger"),
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
    "AUS": ("australia", "澳大利亚", "澳洲", "australian"),
    "NZL": ("new zealand", "新西兰", "紐西蘭", "new zealander"),
    "CAN": ("canada", "加拿大", "canadian"),
    "FRA": ("france", "法国", "法國", "french"),
    "DEU": ("germany", "德国", "德國", "german"),
    "ITA": ("italy", "意大利", "義大利", "italian"),
    "ESP": ("spain", "西班牙", "spanish"),
    "NLD": ("netherlands", "holland", "荷兰", "荷蘭", "dutch"),
    "CHE": ("switzerland", "瑞士", "swiss"),
    "ARE": ("uae", "united arab emirates", "dubai", "abu dhabi", "阿联酋", "阿聯酋", "emirati"),
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
    "QAT": ("qatar", "卡塔尔", "卡達", "qatari"),
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
    "CHN": ("beijing", "shanghai", "guangzhou", "shenzhen", "chengdu", "hangzhou", "北京", "上海", "广州", "深圳", "成都"),
    "HKG": (), "TWN": ("taipei", "台北"),
    "KOR": ("seoul", "busan", "首尔", "首爾", "釜山"),
    "THA": ("bangkok", "phuket", "chiang mai", "曼谷", "普吉"),
    "SGP": (), "MYS": ("kuala lumpur", "吉隆坡"),
    "VNM": ("hanoi", "ho chi minh", "saigon", "da nang", "河内", "胡志明", "岘港"),
    "IDN": ("jakarta", "雅加达", "巴厘岛", "峇里島"),
    "PHL": ("manila", "cebu", "马尼拉", "宿务"),
    "IND": ("delhi", "new delhi", "mumbai", "bangalore", "新德里", "孟买"),
    "ARE": (), "TUR": ("istanbul", "伊斯坦布尔"),
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
    ("business", ("business", "商务", "商務", "出差", "conference", "meeting")),
    ("study", ("study", "student", "留学", "留學", "university", "school")),
    ("work", ("work", "job", "工作", "employment")),
    ("family_visit", ("family", "relatives", "探亲", "探親", "visit my", "親友", "亲友")),
    ("transit", ("transit", "layover", "stopover", "过境", "過境", "转机", "轉機")),
)


def _country_mentions(text: str) -> list:
    """[(position, ISO3, is_demonym)] for every country named in the text,
    longest alias first so "south korea" beats "korea" and "new zealand"
    is not read as two words."""
    low = text.lower()
    found = []
    taken = [False] * len(low)
    try:
        from .registry import load_registry
        names = [(e["alpha_3"], n.lower(), False)
                 for e in load_registry("countries")["entries"]
                 for n in (e.get("name"), e.get("common_name")) if n and len(n) > 2]
    except Exception:  # noqa: BLE001 - aliases alone still work
        names = []
    aliases = [(iso, a, (i == len(al) - 1 and a.isascii()))
               for iso, al in _ALIASES.items() for i, a in enumerate(al)]
    cities = [(iso, c, False) for iso, cs in _CITIES.items() for c in cs]
    for iso, alias, demonym in sorted(names + aliases + cities, key=lambda x: -len(x[1])):
        start = 0
        while True:
            i = low.find(alias, start)
            if i < 0:
                break
            j = i + len(alias)
            ascii_word = alias.isascii()
            boundary_ok = (not ascii_word) or (
                (i == 0 or not low[i - 1].isalpha()) and (j >= len(low) or not low[j].isalpha()))
            if boundary_ok and not any(taken[i:j]):
                for k in range(i, j):
                    taken[k] = True
                found.append((i, iso, demonym))
            start = j
    return sorted(found)


# What single fact is the question really after? Detected from plain words so
# the answer page can lead with it.
_FOCUS_WORDS = (
    ("fee", ("how much", "cost", "price", "fee", "多少钱", "多少錢", "费用", "費用")),
    ("stay", ("how long can", "how many days", "length of stay", "stay",
              "住多久", "待多久", "停留", "几天能待", "幾天")),
    ("documents", ("documents", "what do i need to bring", "materials",
                   "paperwork", "材料", "资料", "資料", "文件")),
    ("processing", ("how long does it take", "processing", "how fast",
                    "几天出签", "幾天出簽", "办理时间", "辦理時間", "多久出")),
)

_TRANSIT_MARKERS = ("via ", "through ", "layover in ", "stopover in ",
                    "transit in ", "transiting ")

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
    zh = _re.search(r"(明年|今年)?\s*([一二三四五六七八九十]{1,2}|\d{1,2})月", question)
    if zh:
        raw = zh.group(2)
        nums = {"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,
                "十":10,"十一":11,"十二":12}
        month = nums.get(raw) or (int(raw) if raw.isdigit() else None)
        if month and 1 <= month <= 12:
            year = today.year + (1 if zh.group(1) == "明年"
                                 or month < today.month else 0)
            return f"{year:04d}-{month:02d}-01"
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
    mentions = _country_mentions(q)
    isos = []
    for _pos, iso, _d in mentions:
        if iso not in isos:
            isos.append(iso)
    if len(isos) < 2:
        return None
    nat = dest = None
    # Nationality: a demonym ("Chinese passport"), "with a X passport", or "from X".
    for pos, iso, demonym in mentions:
        if demonym:
            nat = iso
            break
    if nat is None:
        for pos, iso, _d in mentions:
            before = low[max(0, pos - 12):pos]
            if "from " in before or before.rstrip().endswith("from") or "持" in q[max(0, pos - 3):pos]:
                nat = iso
                break
    # Destination: "to X", "go to X", "visit X", "in X", 去X / 到X / 赴X.
    for pos, iso, _d in mentions:
        if iso == nat:
            continue
        before = low[max(0, pos - 10):pos]
        if any(w in before for w in (" to ", "to ", "visit", " in ", "going", "travel")) \
                or q[max(0, pos - 1):pos] in ("去", "到", "赴", "往"):
            dest = iso
            break
    nat_marked, dest_marked = nat is not None, dest is not None
    if nat is None:
        nat = next(i for i in isos if i != dest)
    if dest is None:
        dest = next(i for i in isos if i != nat)
    # Stopovers: "via singapore", "layover in dubai", 经/途经/转机.
    transit = []
    for pos, iso, _d in mentions:
        if iso in (nat, dest):
            continue
        before = low[max(0, pos - 14):pos]
        zh = q[max(0, pos - 3):pos]
        if any(m in before for m in _TRANSIT_MARKERS) or \
                any(z in zh for z in ("经", "經", "转机", "轉機", "途经", "途經")):
            if iso not in transit:
                transit.append(iso)
    purpose = "tourism"
    for name, words in _PURPOSE_WORDS:
        if any(w in low or w in q for w in words):
            purpose = name
            break
    doc = "diplomatic_passport" if ("diplomatic" in low or "外交护照" in q or "外交護照" in q) \
        else "service_passport" if ("official passport" in low or "公务护照" in q or "公務護照" in q) \
        else "ordinary_passport"
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
            "focus": _question_focus(q), "read_by": "deterministic"}


def parse_question_with_context(question: str, context: dict | None,
                                *, timeout: float = 20.0) -> dict:
    """A follow-up like "what about business?" or "and with a diplomatic
    passport?" or "to korea instead" modifies the route ON SCREEN rather than
    being refused for not naming two places. Deterministic only — a follow-up
    never guesses."""
    q = str(question or "").strip()
    ctx = context or {}
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
        doc = None
        if "diplomatic" in low or "外交护照" in q or "外交護照" in q:
            doc = "diplomatic_passport"
        elif "official passport" in low or "service passport" in low \
                or "公务护照" in q or "公務護照" in q:
            doc = "service_passport"
        elif "ordinary" in low or "普通护照" in q or "普通護照" in q:
            doc = "ordinary_passport"
        if len(isos) <= 1 and (isos or purpose or doc):
            nat = str(ctx.get("nationality")).upper()
            dest = str(ctx.get("destination")).upper()
            if isos:
                one = isos[0]
                before = low[:low.find(one.lower())] if False else low
                # "from X" changes the passport; anything else the destination
                pos = mentions[0][0]
                lead = low[max(0, pos - 12):pos]
                if "from " in lead or "持" in q[max(0, pos - 3):pos]:
                    nat = one
                elif one != nat:
                    dest = one
            return {"understood": True, "nationality": nat, "destination": dest,
                    "travel_purpose": purpose or ctx.get("travel_purpose", "tourism"),
                    "travel_document_type": doc or ctx.get("travel_document_type",
                                                           "ordinary_passport"),
                    "transit_countries": [], "focus": _question_focus(q),
                    "read_by": "context"}
    return parse_question(q, timeout=timeout)


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
    doc = str(raw.get("travel_document_type") or "ordinary_passport").strip()
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
                                       "processing") else _question_focus(q),
           "read_by": "model"}
    if len(_PARSE_CACHE) > 500:
        _PARSE_CACHE.clear()
    _PARSE_CACHE[ck] = dict(out)
    return out
