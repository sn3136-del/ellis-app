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

_SCHEMA_SPEC = """Reply STRICT JSON with these fields (omit nothing; use null
when genuinely unknown and add an entry to "uncertainty" naming the field and why):
disposition: one of VISA_REQUIRED | VISA_EXEMPT | ELECTRONIC_AUTHORIZATION_REQUIRED | CONDITIONAL
visa_category, permitted_stay, passport_validity, processing_time: short strings
permitted_stay_days: integer number of days of permitted stay, or null
passport_validity_requirement: {"kind": "valid_on_arrival"|"valid_through_departure"|"months_after_arrival"|"months_after_departure", "months": integer|null}
required_documents, forms, account_registration_steps, payment_process,
submission_process, exceptions: arrays of short strings
application_channel: online_portal | embassy | visa_center | authorised_agent | on_arrival | not_required — use authorised_agent when individuals may NOT file directly and a designated agency must lodge for them (e.g. Chinese nationals applying for Japan); never call that a visa_center
official_portal_url: the official government portal URL or null (NEVER invent one) — for THIS destination and visa type; do not point at an unrelated page
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


def validate_answer(raw: dict) -> tuple[dict, list, list]:
    """Whitelist + shape-check one answer. Returns (clean, missing, contradictions).
    Purely deterministic — schema validity, mandatory fields, and internal
    contradictions; never a model judgement."""
    clean: dict = {}
    for k in ALL_FIELDS:
        if k in (raw or {}):
            clean[k] = raw[k]
    missing = []
    for k in MANDATORY_FIELDS:
        v = clean.get(k)
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
    # Confidence drives the display hold, so an unexpected word must not slip
    # through as an unknown label (or as "not low").
    conf = str(clean.get("confidence") or "").strip().lower()
    clean["confidence"] = conf if conf in ("high", "medium", "low") else "low"
    for k in ("application_channel_detail", "source_url"):
        if k in clean and not isinstance(clean[k], str):
            clean.pop(k, None)
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
    cannot_self_file = any(t in detail for t in (
        "cannot apply directly", "not accept individual", "does not accept direct",
        "must apply through", "must be lodged by", "must go through",
        "through a designated", "through an authorised", "through an authorized",
        "accredited travel agency", "designated agency", "authorised agent",
        "authorized agent"))
    if cannot_self_file and channel in ("visa_center", "embassy", "online_portal"):
        contradictions.append(
            f"application_channel '{channel}' but the channel detail says "
            "individuals may not file directly (use authorised_agent)")

    # (i)/(iv) A visa-required route should list its products, and a product's
    # own note must not contradict the stay printed beside it.
    products = clean.get("visa_products") or []
    if clean.get("disposition") == "VISA_REQUIRED" and not products:
        contradictions.append("disposition VISA_REQUIRED but no visa_products "
                              "were listed for this purpose")
    for vp in products:
        if not isinstance(vp, dict):
            continue
        note = str(vp.get("notes") or "")
        d = vp.get("max_stay_days")
        if d is None or not note:
            continue
        named = [int(n) for n in _re.findall(r"\b(\d{1,3})\s*days?\b", note.lower())]
        if named and max(named) < d:
            contradictions.append(
                f"visa product '{vp.get('type')}' says {d} days but its note "
                f"names a shorter granted stay ({max(named)} days)")
    return clean, missing, contradictions


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
    if elapsed_seconds is not None:
        out["elapsed_seconds"] = round(elapsed_seconds, 2)
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


def get_route_guidance(db, route: dict, *, force_refresh: bool = False) -> dict:
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
        out = apply_verified_overrides(_result(
            row.status, row.guidance, cached=True, stale=_is_stale(row),
            released=released,
            missing=row.missing_fields, contradictions=row.contradictions,
            model=row.model,
            advisories=deterministic_advisories(route, row.guidance or {})), route)
        if isinstance(gc, dict) and gc.get("outcome") == "checked":
            # Machine provenance, deliberately WEAKER than the human badge:
            # "the official page was read on this date and agreed / was
            # applied", never "a person verified this".
            out["grounded_check"] = {k: gc.get(k) for k in
                                     ("at", "source_url", "consistent",
                                      "changed_fields")}
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

    # ---- the ONE structured Kimi analysis ----------------------------------
    try:
        raw = _call(_SYSTEM, user, timeout=budget(), max_tokens=PASS1_MAX_TOKENS)
    except (GuidanceUnavailable, GuidanceTimeout, GuidanceProviderError):
        raise
    except Exception:  # noqa: BLE001 - malformed transport/JSON -> one retry below
        raw = None
    clean, missing, contradictions = validate_answer(raw or {})

    if missing or contradictions:
        # ONE controlled retry, only for a malformed/incomplete answer and only
        # inside the remaining budget.
        retry_user = (user + "\n\nYour previous answer was incomplete. "
                      + (f"Missing or invalid fields: {', '.join(missing)}. " if missing else "")
                      + (f"Contradictions to resolve: {'; '.join(contradictions)}. " if contradictions else "")
                      + "Reply the FULL corrected JSON.")
        try:
            raw2 = _call(_SYSTEM, retry_user, timeout=budget(), max_tokens=PASS1_MAX_TOKENS)
            clean2, missing2, contradictions2 = validate_answer(raw2 or {})
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

    # Cache ONLY complete, verified results — a failed or uncertain attempt is
    # returned honestly but never poisons the cache (the applicant can simply
    # retry).
    if status == STATUS_PRIMARY:
        now = _now()
        ttl = int(os.getenv("ELLIS_KIMI_GUIDANCE_TTL_DAYS", TTL_DAYS) or TTL_DAYS)
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
        row.verification = {"passes": 1, "label": VERIFIED_LABEL}
        row.generated_at = now
        row.fresh_until = now + timedelta(days=ttl)
        db.commit()
    return apply_verified_overrides(_result(
        status, clean, cached=False, stale=False,
        missing=missing, contradictions=contradictions, model=model,
        advisories=advisories, elapsed_seconds=elapsed), route)


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
_ASK_SYSTEM = ("""Extract the travel route from the user's question. Reply STRICT
JSON: {"nationality": ISO3 or null, "destination": ISO3 or null,
"travel_purpose": one of tourism|business|family_visit|study|work|transit|other
or null, "travel_document_type": "ordinary_passport" unless another is named}.
Use ISO3 country codes (CHN, JPN, USA, GBR...). Null anything not stated. No
prose, JSON only.""")


def parse_question(question: str, *, timeout: float = 20.0) -> dict:
    """Turn 'What visa for tourism in Japan with a Chinese passport?' into a
    route dict the Database lookup understands. Deterministic shape-check on
    the model's answer; never invents a country the user did not name."""
    q = str(question or "").strip()
    if not q:
        raise GuidanceUnavailable("no question to read")
    raw = _call(_ASK_SYSTEM, json.dumps({"question": q[:500]}),
                timeout=timeout, max_tokens=2000)
    if not isinstance(raw, dict):
        raise GuidanceUnavailable("could not read the question")
    nat = str(raw.get("nationality") or "").strip().upper()
    dest = str(raw.get("destination") or "").strip().upper()
    purpose = str(raw.get("travel_purpose") or "tourism").strip().lower()
    if len(nat) != 3 or len(dest) != 3:
        return {"understood": False, "nationality": nat, "destination": dest}
    doc = str(raw.get("travel_document_type") or "ordinary_passport").strip()
    return {"understood": True, "nationality": nat, "destination": dest,
            "travel_purpose": purpose if purpose in
            ("tourism", "business", "family_visit", "study", "work",
             "transit", "other")
            else "tourism", "travel_document_type": doc or "ordinary_passport"}
