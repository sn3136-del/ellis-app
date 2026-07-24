"""Kimi-primary route guidance: an IMMEDIATE structured route decision.

For a new tourist route, ONE structured Kimi K3 call returns the full route
picture (disposition, category, stay, passport rules, documents, forms,
channel/portal, fees, processing time, biometrics/interview/appointment,
account/payment/submission steps, exceptions, uncertainty). The answer:

- populates the applicant UI immediately (status KIMI_PRIMARY, clearly labeled
  "AI-generated route guidance"),
- derives the next workflow steps deterministically from its fields,
- may auto-start the adapter build (same authorized bridge as research),
- is cached by nationality × residence × destination × purpose × jurisdiction ×
  policy month, reused instantly and refreshed asynchronously when stale.

It NEVER replaces the official-source pipeline: the existing on-demand research
+ ResearchEvidenceValidator continue as an asynchronous AUDIT (started in the
background as before), and their grounded result supersedes model guidance when
it lands. Guidance drives only reversible preparation (documents, OCR, forms,
adapter generation, navigation, appointment search, fee display); every real
account creation, booking, payment or submission still requires the applicant's
explicit confirmation and the runtime's fail-closed gates (runtime.
assert_execution_allowed) — none of that is relaxed here.

Security: the prompt contains ONLY route facts the applicant typed (nationality,
residence, destination, purpose, dates). No passwords, OTPs, cookies, payment
credentials or portal sessions exist anywhere in this module, and Kimi's answer
is data — it can name steps but cannot execute anything.

Failure handling: a missing mandatory field or a detected contradiction triggers
ONE retry that lists exactly what was missing; a still-incomplete answer is
returned honestly as KIMI_UNCERTAIN with the precise gaps — no silent broad
research is started by this path, and no administrator task is created.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ..config import REAL_ONLY_MODES, settings
from .models import KimiRouteGuidanceCache

STATUS_PRIMARY = "KIMI_PRIMARY"
STATUS_UNCERTAIN = "KIMI_UNCERTAIN"
STATUS_UNAVAILABLE = "KIMI_UNAVAILABLE"

DISPOSITIONS = ("VISA_REQUIRED", "VISA_EXEMPT",
                "ELECTRONIC_AUTHORIZATION_REQUIRED", "CONDITIONAL")

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
)

# Default freshness window; stale entries are reused instantly and refreshed in
# the background (never blocking the applicant).
TTL_DAYS = 14

_SYSTEM = """You are a visa-requirements engine. For the EXACT route in the user
message (passport nationality, lawful residence, destination, purpose, dates),
answer from your knowledge of official visa policy. Reply STRICT JSON with these
fields (omit nothing; use null when genuinely unknown and add an entry to
"uncertainty" naming the field and why):
disposition: one of VISA_REQUIRED | VISA_EXEMPT | ELECTRONIC_AUTHORIZATION_REQUIRED | CONDITIONAL
visa_category, permitted_stay, passport_validity, processing_time: short strings
required_documents, forms, account_registration_steps, payment_process,
submission_process, exceptions: arrays of short strings
application_channel: online_portal | embassy | visa_center | on_arrival | not_required
official_portal_url: the official government portal URL or null (NEVER invent one)
government_fee: {"amount": number|null, "currency": string|null}
photo_requirements, onward_travel_evidence, accommodation_evidence,
financial_evidence: short strings or null
biometrics_required, interview_required, appointment_required,
insurance_required: true|false|null
uncertainty: array of {"field":..., "reason":...} for anything not certain
confidence: high | medium | low
Rules: never guess a URL or a fee; unknown means null + uncertainty entry;
missing information is NEVER visa-exempt; answer for THIS nationality only."""


class GuidanceUnavailable(Exception):
    """No provider (no key / wrong mode) — honest, never fabricated."""


# ---- provider seam (tests inject; real modes use live Kimi) ------------------
_PROVIDER = None


def set_provider(fn) -> None:
    """Inject callable(system, user)->dict for tests. None resets to live Kimi."""
    global _PROVIDER
    _PROVIDER = fn


def _live_call(system: str, user: str) -> dict:
    s = settings()
    if not (s.moonshot_api_key and s.kimi_enabled):
        raise GuidanceUnavailable("Kimi K3 not configured — guidance unavailable")
    from ..providers.kimi import LiveKimiProvider
    provider = LiveKimiProvider()
    # The route decision is a deep-reasoning call: K3 regularly needs >120s for
    # visa-required routes, and an aborted call caches an unusable UNCERTAIN
    # answer. Still a HARD timeout — just a budget sized for this one call
    # (first-time routes only; identical routes hit the cache instantly).
    import os as _os
    guidance_timeout = int(_os.getenv("KIMI_GUIDANCE_TIMEOUT_SECONDS", "240") or 240)
    provider._timeout = max(provider._timeout, guidance_timeout)
    return provider._chat(system, user, json_mode=True)


def _call(system: str, user: str) -> dict:
    if _PROVIDER is not None:
        return _PROVIDER(system, user)
    if settings().runtime_mode not in REAL_ONLY_MODES + ("test", "local_mock_demo"):
        raise GuidanceUnavailable("guidance disabled in this runtime mode")
    return _live_call(system, user)


def is_available() -> bool:
    if _PROVIDER is not None:
        return True
    s = settings()
    return bool(s.moonshot_api_key and s.kimi_enabled)


# ---- prompt / validation -----------------------------------------------------
def build_prompt(route: dict) -> str:
    """User prompt from ROUTE FACTS ONLY (nothing sensitive exists here)."""
    return json.dumps({
        "passport_nationality": route.get("passport_nationality"),
        "lawful_country_of_residence": route.get("lawful_country_of_residence"),
        "destination_country": route.get("destination_country"),
        "visa_category": route.get("visa_category", "tourist_visa"),
        "travel_purpose": route.get("travel_purpose", "tourism"),
        "arrival_date": route.get("arrival_date") or route.get("policy_period"),
        "departure_date": route.get("departure_date"),
        "consular_jurisdiction": route.get("consular_jurisdiction") or "default",
    })


def validate_answer(raw: dict) -> tuple[dict, list, list]:
    """Whitelist + shape-check one answer. Returns (clean, missing, contradictions)."""
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
    return clean, missing, contradictions


def derive_workflow_plan(g: dict) -> list[dict]:
    """Deterministic next-step plan from the guidance FIELDS (never free text).
    Reversible preparation only; irreversible steps carry the confirmation flag."""
    steps: list[dict] = []
    disp = g.get("disposition")
    steps.append({"step": "collect_documents", "reversible": True,
                  "items": g.get("required_documents") or []})
    steps.append({"step": "ocr_and_validate_passport", "reversible": True})
    if disp == "VISA_EXEMPT":
        steps.append({"step": "prepare_entry_documents", "reversible": True,
                      "items": g.get("forms") or []})
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
        steps.append({"step": "display_exact_fees", "reversible": True,
                      "fee": g.get("government_fee")})
        steps.append({"step": "payment", "reversible": False,
                      "requires_applicant_confirmation": True})
        steps.append({"step": "final_review_and_signature", "reversible": False,
                      "requires_applicant_confirmation": True})
        steps.append({"step": "submission", "reversible": False,
                      "requires_applicant_confirmation": True})
    steps.append({"step": "track_status", "reversible": True})
    return steps


# ---- cache -------------------------------------------------------------------
def cache_key(route: dict) -> str:
    arrival = str(route.get("arrival_date") or route.get("policy_period") or "")
    policy_month = arrival[:7] or "unknown"          # YYYY-MM: the policy date bucket
    return "|".join((
        str(route.get("passport_nationality", "")).upper(),
        str(route.get("lawful_country_of_residence", "")).upper(),
        str(route.get("destination_country", "")).upper(),
        str(route.get("travel_purpose", "tourism")).lower(),
        str(route.get("consular_jurisdiction") or "default").lower(),
        policy_month,
    ))


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
            missing=None, contradictions=None, model: str = "") -> dict:
    return {
        "status": status,
        "ai_generated": True,
        "label": "AI-generated route guidance",
        "guidance": guidance,
        "workflow_plan": derive_workflow_plan(guidance) if guidance else [],
        "missing_fields": list(missing or []),
        "contradictions": list(contradictions or []),
        "cached": cached, "stale": stale, "model": model,
        # The safety boundary the UI must show with any guidance-driven flow:
        "irreversible_requires_confirmation": True,
        "audit": "official-source verification runs asynchronously and, once "
                 "grounded, supersedes this AI guidance",
    }


def get_route_guidance(db, route: dict, *, force_refresh: bool = False) -> dict:
    """The immediate route decision. Cached identical routes return instantly;
    a fresh route makes ONE structured Kimi call (with one targeted retry on
    missing fields). Never starts broad research; never creates review tasks."""
    key = cache_key(route)
    row = _cached(db, key)
    if row is not None and not force_refresh:
        return _result(row.status, row.guidance, cached=True, stale=_is_stale(row),
                       missing=row.missing_fields, contradictions=row.contradictions,
                       model=row.model)

    if not is_available():
        raise GuidanceUnavailable("Kimi K3 not configured — guidance unavailable")

    user = build_prompt(route)
    model = settings().kimi_model if _PROVIDER is None else "injected-test-provider"
    try:
        raw = _call(_SYSTEM, user)
    except GuidanceUnavailable:
        raise
    except Exception as e:  # noqa: BLE001 - one retry below, then honest failure
        raw, _err = None, e
    clean, missing, contradictions = validate_answer(raw or {})

    if missing or contradictions:
        # ONE targeted retry naming exactly what was missing/contradictory.
        retry_user = (user + "\n\nYour previous answer was incomplete. "
                      + (f"Missing or invalid fields: {', '.join(missing)}. " if missing else "")
                      + (f"Contradictions to resolve: {'; '.join(contradictions)}. " if contradictions else "")
                      + "Reply the FULL corrected JSON.")
        try:
            raw2 = _call(_SYSTEM, retry_user)
            clean2, missing2, contradictions2 = validate_answer(raw2 or {})
            if len(missing2) + len(contradictions2) < len(missing) + len(contradictions):
                clean, missing, contradictions = clean2, missing2, contradictions2
        except GuidanceUnavailable:
            raise
        except Exception:  # noqa: BLE001 - keep the first answer's honest gaps
            pass

    status = STATUS_PRIMARY if not missing and not contradictions else STATUS_UNCERTAIN
    now = _now()
    ttl = int(__import__("os").getenv("ELLIS_KIMI_GUIDANCE_TTL_DAYS", TTL_DAYS) or TTL_DAYS)
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
    row.generated_at = now
    row.fresh_until = now + timedelta(days=ttl)
    db.commit()
    return _result(status, clean, cached=False, stale=False,
                   missing=missing, contradictions=contradictions, model=model)


def refresh_stale_async(db_factory, route: dict) -> None:
    """Background refresh for a stale cache entry (never blocks the applicant).
    db_factory() must yield a NEW session (thread-safe)."""
    db = db_factory()
    try:
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
