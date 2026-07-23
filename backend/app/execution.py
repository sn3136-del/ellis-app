"""Execution classification — the single source of truth for HOW REAL any
external action or result is.

The brief's absolute requirement: every external action Ellis reports must carry
an exact execution classification, and a MOCK result must NEVER be presented to
an applicant or partner as a real submission / payment / appointment / government
confirmation.

Seven classifications (exactly the brief's set):

  MOCK                        an in-process test fixture (Mockland). Never real.
  LOCAL_PROVIDER              a deterministic local implementation (e.g. the local
                              MRZ parser, in-app authorization). A real function,
                              but no live external provider was contacted.
  LIVE_SANDBOX               a real external provider in sandbox / test mode
                              (Trip.com sandbox, Browserbase test session, a
                              portal's official staging environment).
  LIVE_PRODUCTION            a real external production system (a real government
                              portal, a real payment). The ONLY class that may
                              back a real government confirmation.
  APPLICANT_ACTION_REQUIRED  the step must be performed personally by the
                              applicant (CAPTCHA, OTP, payment, declaration).
  MANUAL_REVIEW_REQUIRED     blocked pending human / administrator review.
  UNSUPPORTED                the route / action is not supported.

The governing safety rule (encoded here, not left to callers):

  The execution class of a result is determined by the DRIVER THAT ACTUALLY RAN
  IT — never by configuration claims. A result can never be classified more-real
  than the driver that produced it. So even if an adapter's *config* claims
  `production_approved`, a result produced by a MockPortal driver is MOCK. This is
  what makes "never convert a mock result into a production result" structurally
  true rather than a promise.
"""
from __future__ import annotations

from enum import Enum


class ExecutionClass(str, Enum):
    MOCK = "MOCK"
    LOCAL_PROVIDER = "LOCAL_PROVIDER"
    LIVE_SANDBOX = "LIVE_SANDBOX"
    LIVE_PRODUCTION = "LIVE_PRODUCTION"
    APPLICANT_ACTION_REQUIRED = "APPLICANT_ACTION_REQUIRED"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
    UNSUPPORTED = "UNSUPPORTED"

    def __str__(self) -> str:  # so f-strings / json give the bare token
        return self.value


# Ordered by "realness" for provider-run steps. Used to clamp a claimed class
# down to what the driver can actually support (min of the two).
_REALNESS = {
    ExecutionClass.MOCK: 0,
    ExecutionClass.LOCAL_PROVIDER: 1,
    ExecutionClass.LIVE_SANDBOX: 2,
    ExecutionClass.LIVE_PRODUCTION: 3,
}

# Classes that describe a provider actually running a step (as opposed to a
# disposition like APPLICANT_ACTION_REQUIRED / MANUAL_REVIEW_REQUIRED).
PROVIDER_CLASSES = frozenset(_REALNESS)

# The ONLY class under which a terminal government outcome (submitted / paid /
# booked / confirmed) may be presented to a user or partner as a REAL result.
REAL_RESULT_CLASS = ExecutionClass.LIVE_PRODUCTION

# Terminal claims the production UI/API must not present as real unless the
# backing result is REAL_RESULT_CLASS and adapter-verified.
GUARDED_TERMINAL_CLAIMS = ("submitted", "paid", "booked", "confirmed", "issued")

# Human-readable, non-alarming descriptions (shown in UI legends / API docs).
DESCRIPTIONS = {
    ExecutionClass.MOCK: "Automated-test portal (Mockland). Not a real government portal; nothing was really submitted, paid, or booked.",
    ExecutionClass.LOCAL_PROVIDER: "Ran on a built-in local implementation. Real computation, but no live external provider was contacted.",
    ExecutionClass.LIVE_SANDBOX: "Ran against a real external provider in sandbox/test mode. Not a real government result.",
    ExecutionClass.LIVE_PRODUCTION: "Ran against the real external production system. This is a real result.",
    ExecutionClass.APPLICANT_ACTION_REQUIRED: "The applicant must complete this step personally.",
    ExecutionClass.MANUAL_REVIEW_REQUIRED: "Blocked pending human/administrator review.",
    ExecutionClass.UNSUPPORTED: "This route or action is not supported.",
}


def clamp(claimed: ExecutionClass, driver: ExecutionClass) -> ExecutionClass:
    """Return the least-real of a claimed class and the driver's actual class.
    Only meaningful for PROVIDER_CLASSES; dispositions pass through unchanged."""
    if claimed not in PROVIDER_CLASSES or driver not in PROVIDER_CLASSES:
        return claimed
    return claimed if _REALNESS[claimed] <= _REALNESS[driver] else driver


def is_real_government_result(ec) -> bool:
    """True only for a real production result. Sandbox, local, and mock are all
    explicitly NOT real government results."""
    return coerce(ec) == REAL_RESULT_CLASS


def coerce(ec) -> ExecutionClass:
    if isinstance(ec, ExecutionClass):
        return ec
    try:
        return ExecutionClass(str(ec))
    except ValueError:
        # Fail safe: anything unrecognised is treated as needing manual review,
        # never as a real result.
        return ExecutionClass.MANUAL_REVIEW_REQUIRED


# --- driver / adapter classification ---------------------------------------

def classify_driver(driver) -> ExecutionClass:
    """Classify a bound portal driver by WHAT IT ACTUALLY IS.

    A driver may declare its own class via an `execution_class` attribute
    (the MockPortal declares MOCK; a live Browserbase driver declares
    LIVE_SANDBOX or LIVE_PRODUCTION based on its target environment). An
    unknown/None driver is treated as MANUAL_REVIEW_REQUIRED — never as real."""
    if driver is None:
        return ExecutionClass.MANUAL_REVIEW_REQUIRED
    declared = getattr(driver, "execution_class", None)
    if declared is not None:
        return coerce(declared)
    # No self-declaration: infer conservatively from the class name.
    name = type(driver).__name__.lower()
    if "mock" in name:
        return ExecutionClass.MOCK
    return ExecutionClass.MANUAL_REVIEW_REQUIRED


def classify_adapter(adapter) -> ExecutionClass:
    """Classify what running through this adapter would actually produce.

    The result is clamped to the driver's real class, so a config that claims
    production but binds a mock driver still classifies MOCK. If the adapter is
    not production-enabled/approved, it can be at most LIVE_SANDBOX even with a
    real driver."""
    if adapter is None:
        return ExecutionClass.UNSUPPORTED
    driver_ec = classify_driver(getattr(adapter, "driver", None))
    approved = getattr(adapter, "production_approval_status", "") == "production_approved"
    enabled = bool(getattr(adapter, "production_enabled", False))
    if approved and enabled:
        claimed = ExecutionClass.LIVE_PRODUCTION
    elif getattr(adapter, "production_approval_status", "") in ("tested", "implemented"):
        claimed = ExecutionClass.LIVE_SANDBOX
    else:
        claimed = driver_ec if driver_ec in PROVIDER_CLASSES else ExecutionClass.MANUAL_REVIEW_REQUIRED
    return clamp(claimed, driver_ec)


# --- OCR classification -----------------------------------------------------

# OCR engines that are real live production providers.
_LIVE_OCR_ENGINES = {"google_document_ai", "kimi_k3_vision"}


def classify_ocr(ocr_meta: dict) -> ExecutionClass:
    """Classify how an OCR result was produced from the failover metadata that
    `ocr.process_with_failover` returns (never any personal values)."""
    meta = ocr_meta or {}
    engine = meta.get("fallback_engine") if meta.get("fallback_used") else meta.get("primary")
    if engine in _LIVE_OCR_ENGINES:
        return ExecutionClass.LIVE_PRODUCTION
    # local_text / local MRZ parser, or degraded-to-local.
    return ExecutionClass.LOCAL_PROVIDER


# --- result disposition (the display guard) ---------------------------------

def result_disposition(state: str, ec, *, adapter_verified: bool = True) -> dict:
    """Compute the guarded, display-safe disposition of a case result.

    This is the enforcement point for: "the production UI must refuse to display
    submitted/paid/booked/confirmed unless the approved live adapter has
    retrieved and verified the corresponding result from the official portal."

    Returns a dict the UI/API renders instead of trusting raw state text:
      - execution_class:            the classification token
      - is_real_government_result:  True ONLY for adapter-verified LIVE_PRODUCTION
      - display_status:             a status string SAFE to show to a human
      - real_terminal_claims_allowed: whether the caller may present the guarded
                                    terminal claims as real
      - disclaimer:                 plain-language explanation when not real
    """
    ec = coerce(ec)
    real = is_real_government_result(ec) and adapter_verified
    terminal = state == "COMPLETED"
    if real:
        display = "COMPLETED" if terminal else state
        disclaimer = ""
    else:
        # Never let a non-production result read as a real government outcome.
        suffix = {
            ExecutionClass.MOCK: "MOCK — automated-test portal",
            ExecutionClass.LOCAL_PROVIDER: "LOCAL — built-in provider, not a live portal",
            ExecutionClass.LIVE_SANDBOX: "SANDBOX — real provider in test mode, not a real visa",
        }.get(ec, str(ec))
        display = f"{state} ({suffix})" if terminal else f"{state} · {suffix}"
        disclaimer = DESCRIPTIONS.get(ec, "This is not a real government result.")
    return {
        "execution_class": str(ec),
        "execution_class_description": DESCRIPTIONS.get(ec, ""),
        "is_real_government_result": real,
        "adapter_verified": bool(adapter_verified),
        "display_status": display,
        "real_terminal_claims_allowed": real,
        "disclaimer": disclaimer,
    }


def assert_not_mock_in_production(ec, *, production_mode: bool) -> None:
    """Guard used before any code path that would present a result as real. In
    production mode a MOCK / LOCAL / SANDBOX class may not back a real terminal
    claim — raises so a bug can never silently pass a mock off as production."""
    if production_mode and not is_real_government_result(ec):
        raise MockAsProductionError(
            f"refusing to present a {coerce(ec)} result as a real production outcome")


class MockAsProductionError(RuntimeError):
    pass


def legend() -> dict:
    """The full classification legend for API /capabilities and UI tooltips."""
    return {c.value: DESCRIPTIONS[c] for c in ExecutionClass}


def record_execution(db, *, org_id: str, application_id: str, action: str, ec,
                     detail: dict | None = None):
    """Persist the execution classification for one action to the database AND
    the audit trail (both are required by the brief). Detail must be
    non-sensitive metadata only — it is redacted on the audit path regardless."""
    from . import models, audit
    ec = coerce(ec)
    real = is_real_government_result(ec)
    row = models.ExecutionRecord(org_id=org_id, application_id=application_id,
                                 action=action, execution_class=str(ec),
                                 is_real_government_result=real, detail=detail or {})
    db.add(row)
    db.commit()
    audit.record(db, org_id=org_id, application_id=application_id,
                 action=f"execution_classified:{action}",
                 detail={"execution_class": str(ec),
                         "is_real_government_result": real, **(detail or {})})
    return row
