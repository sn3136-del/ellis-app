"""Regula Document Reader — reading PRIOR VISA STICKERS, and nothing else.

WHY THIS EXISTS
---------------
Ellis's own pipeline (Google Document AI + the ICAO 9303 MRZ parser) reads the
passport biodata page well. The one thing it cannot do is read the VISA STICKER
pasted on a later passport page: the visa number, the category/class, and the
"valid from"/"valid until" dates. Tourist forms ask for exactly those (DS-160's
previous-visa block, the Schengen application, the UK forms), and today an
applicant types them by hand from a small, dense sticker. Regula's document
reader recognizes visa stickers as a first-class document, so this provider
exists to fill THAT gap.

THIS IS NEVER AN IDENTITY SOURCE
--------------------------------
Identity in Ellis is established by exactly one path: a checksum-valid ICAO TD3
MRZ on the passport biodata page (`providers.passport_classifier.classify_page`
sets `accepted_as_passport_identity` only there). A visa sticker read is a
CONVENIENCE for form fields and can never establish who the applicant is —
Regula's own MRV (machine-readable visa) zone is explicitly rejected as an
identity source by that classifier.

That rule is enforced structurally, not by convention: `read_prior_visa`
returns ONLY the whitelisted visa-sticker fields in `_VISA_FIELDS`. Surname,
given names, date of birth, personal number and document number are dropped
before the result is built, so an identity field cannot travel out of this
module even if the vendor returns one. `can_establish_identity()` returns False
unconditionally and exists so a caller can assert the rule in one call.

Fields deliberately NOT returned even though Regula can produce them:
DATE_OF_ISSUE, DATE_OF_EXPIRY, AUTHORITY, PLACE_OF_ISSUE, ISSUING_STATE_CODE.
On a page carrying both a passport and a sticker those cannot be attributed
unambiguously to one or the other, and a misattributed date on a government
form is worse than a blank a human fills in.

PRIVACY
-------
The image bytes go to the document reader and nowhere else: never to an LLM,
never into a log, never into an error string. Failures degrade to a short
category, never the vendor's response body.

CONTRACT (verified against the Regula Web API OpenAPI 8.1.0, August 2026)
--------------------------------------------------------------------------
POST {base}/api/process
  body: {"processParam": {"scenario": "FullProcess", "resultTypeOutput": [36],
                          "dateFormat": "yyyy-MM-dd"},
         "List": [{"ImageData": {"image": "<base64>"}, "light": 6,
                   "page_idx": 0}],
         "systemInfo": {"license": "<base64 license>"}}
  response: ContainerList.List[] where the item with result_type 36 carries
            Text.fieldList[] of TextField {fieldType, fieldName, value,
            valueList:[{source, value, probability}], validityStatus}.
            CheckResult: 0 = negative, 1 = positive, 2 = not performed.
"""
from __future__ import annotations

import base64

from ..config import settings

SOURCE = "regula"

# Regula's documented cloud endpoint; a self-hosted/on-prem deployment sets
# REGULA_BASE_URL instead (the same API, inside the operator's own network).
_DEFAULT_BASE = "https://api.regulaforensics.com"
_PROCESS_PATH = "/api/process"
_TIMEOUT_SECONDS = 30

# Result.TEXT — "document textual fields from all sources with validity and
# cross-source compare checks".
_RESULT_TEXT = 36
# Scenario.FULL_PROCESS — the scenario that reads a sticker's printed zone as
# well as any machine-readable zone.
_SCENARIO = "FullProcess"
# Ellis is ISO-internal end to end (app/dates.py is the single authority), so
# the reader is asked for ISO dates rather than a locale format Ellis re-parses.
_DATE_FORMAT = "yyyy-MM-dd"

# A generous ceiling on one page image. Refused before any network call: a
# 50 MB scan is a client bug, not a document.
MAX_IMAGE_BYTES = 12 * 1024 * 1024

# ---------------------------------------------------------------------------
# The whitelist. TextFieldType numbers from Regula's e-text-field-type enum.
# Nothing outside this map can appear in a result — see the module docstring.
# ---------------------------------------------------------------------------
_VISA_FIELDS: dict[int, str] = {
    29: "visa_id",             # VISA_ID — the number printed on the sticker
    196: "visa_number",        # VISA_NUMBER — the separate control number
    30: "visa_class",          # VISA_CLASS
    31: "visa_subclass",       # VISA_SUBCLASS
    100: "visa_type",          # VISA_TYPE
    101: "visa_valid_from",    # VISA_VALID_FROM
    102: "visa_valid_until",   # VISA_VALID_UNTIL
    103: "duration_of_stay",   # DURATION_OF_STAY
    104: "number_of_entries",  # NUMBER_OF_ENTRIES
}

# Named only to document the rule: these are the identity fields a document
# reader would happily return, and this module never does.
IDENTITY_FIELDS_NEVER_RETURNED = (
    "surname", "given_names", "date_of_birth", "personal_number",
    "document_number", "nationality", "sex", "place_of_birth",
)

# Date-shaped visa fields, normalized through Ellis's canonical date pipeline.
_DATE_FIELDS = {"visa_valid_from": "issue", "visa_valid_until": "expiry"}

# CheckResult.ERROR — the reader performed a validity check and it FAILED.
_CHECK_ERROR = 0


class InvalidDocumentImage(ValueError):
    """The bytes are not a document image. Raised BEFORE any network call so an
    empty upload is never sent to the vendor and never comes back as fields."""


# ---------------------------------------------------------------------------
# HTTP seam — every network byte this module sends passes through here.
# ---------------------------------------------------------------------------
def _http_json(method: str, url: str, *, headers: dict,
               json_body: dict | None = None) -> tuple[int, dict]:
    import httpx
    r = httpx.request(method, url, headers=headers, json=json_body,
                      timeout=_TIMEOUT_SECONDS)
    try:
        body = r.json()
    except Exception:  # noqa: BLE001 - a non-JSON body is an error body
        body = {}
    return r.status_code, (body if isinstance(body, dict) else {})


def _base_url() -> str:
    return (settings().regula_base_url or _DEFAULT_BASE).rstrip("/")


def is_configured() -> bool:
    """True when a license key (cloud) or a self-hosted base URL is present.
    Unconfigured, `read_prior_visa` reports unavailable and the applicant types
    the sticker values themselves, exactly as today."""
    s = settings()
    return bool(s.regula_api_key or s.regula_base_url)


def can_establish_identity(result=None) -> bool:
    """False. Always. A visa-sticker read is never an identity source; identity
    comes only from a checksum-valid TD3 MRZ on the biodata page. The argument
    is accepted so a caller can write the assertion inline against any result."""
    return False


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------
def _text_fields(body: dict) -> list[dict]:
    """The TextField list from the result_type 36 container. Defensive about
    shape: a container list that does not contain a text result yields []."""
    container = body.get("ContainerList")
    items = container.get("List") if isinstance(container, dict) else None
    if not isinstance(items, list):
        return []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            result_type = int(item.get("result_type") or 0)
        except (TypeError, ValueError):
            continue
        if result_type != _RESULT_TEXT:
            continue
        text = item.get("Text")
        fields = text.get("fieldList") if isinstance(text, dict) else None
        if isinstance(fields, list):
            return [f for f in fields if isinstance(f, dict)]
    return []


def _best_value(field: dict) -> tuple[str, int | None]:
    """(value, probability). Regula puts the most confident value on the field
    itself and the per-source candidates in valueList; probability rides on the
    candidates. A missing probability stays None — never an invented score."""
    value = str(field.get("value") or "").strip()
    probability = None
    values = field.get("valueList")
    if isinstance(values, list):
        for candidate in values:
            if not isinstance(candidate, dict):
                continue
            cand_value = str(candidate.get("value") or "").strip()
            if value and cand_value != value:
                continue
            try:
                probability = int(candidate.get("probability"))
            except (TypeError, ValueError):
                probability = None
            if not value and cand_value:
                value = cand_value
            break
    return value, probability


def _normalize_date(value: str, kind: str) -> str:
    """Canonical ISO for a sticker date, or '' when it does not parse. Ellis is
    ISO-internal (app/dates.py is the single authority); a value this cannot
    read is left alone by the caller and flagged, never rewritten into a date
    Ellis merely finds plausible."""
    from .. import dates
    return dates.normalize_any(value, kind=kind)


def _unavailable(note: str) -> dict:
    return {"available": False, "fields": {}, "confidence": None,
            "warnings": [], "source": SOURCE, "identity_source": False,
            "note": note}


def read_prior_visa(image_bytes: bytes) -> dict:
    """Read a PRIOR VISA STICKER from one passport page image.

    Returns {available, fields, confidence, warnings, source, identity_source}:
      available       False whenever the reader could not answer (unconfigured,
                      unreachable, HTTP error, no text result). Never fabricated.
      fields          only the whitelisted visa-sticker values in `_VISA_FIELDS`
                      that were actually recognized: visa_id, visa_number,
                      visa_class, visa_subclass, visa_type, visa_valid_from,
                      visa_valid_until, duration_of_stay, number_of_entries.
                      Dates are canonical ISO. NEVER an identity field.
      confidence      lowest per-field recognition probability, 0.0-1.0, or
                      None when the reader reported none.
      warnings        human-readable, non-sensitive notes (nothing recognized,
                      a failed validity check, an unparseable date).
      identity_source always False — see `can_establish_identity`.

    Raises InvalidDocumentImage for empty/oversized/non-bytes input, before the
    provider is even asked whether it is configured."""
    if not isinstance(image_bytes, (bytes, bytearray)):
        raise InvalidDocumentImage("image_bytes must be bytes")
    if not image_bytes:
        raise InvalidDocumentImage("image_bytes is empty")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise InvalidDocumentImage(
            f"image exceeds {MAX_IMAGE_BYTES} bytes")

    if not is_configured():
        return _unavailable("Regula document reader not configured")

    s = settings()
    payload: dict = {
        "processParam": {"scenario": _SCENARIO,
                         "resultTypeOutput": [_RESULT_TEXT],
                         "dateFormat": _DATE_FORMAT},
        "List": [{"ImageData": {"image": base64.b64encode(bytes(image_bytes)).decode()},
                  "light": 6, "page_idx": 0}],
    }
    if s.regula_api_key:
        # The documented credential is a base64 license carried in the request
        # (HTTP 403 means "server or request does not contain valid license").
        # A self-hosted install may instead sit behind its own proxy auth; that
        # is deployment configuration, not something invented here.
        payload["systemInfo"] = {"license": s.regula_api_key}

    try:
        code, body = _http_json("POST", f"{_base_url()}{_PROCESS_PATH}",
                                headers={"content-type": "application/json",
                                         "accept": "application/json"},
                                json_body=payload)
    except Exception:  # noqa: BLE001
        # Never surface transport internals — and never any part of the
        # document, which a transport exception string can easily echo back.
        return _unavailable("Regula document reader unreachable")
    if code == 403:
        return _unavailable("Regula document reader rejected the license")
    if code >= 400:
        return _unavailable(f"Regula document reader returned HTTP {code}")

    fields_out: dict = {}
    warnings: list[str] = []
    probabilities: list[int] = []

    for field in _text_fields(body):
        try:
            ftype = int(field.get("fieldType"))
        except (TypeError, ValueError):
            continue
        name = _VISA_FIELDS.get(ftype)
        if not name:
            # Everything else — including every identity field — is dropped
            # here, before it can reach the result.
            continue
        value, probability = _best_value(field)
        if not value:
            continue
        if name in _DATE_FIELDS:
            normalized = _normalize_date(value, _DATE_FIELDS[name])
            if normalized:
                value = normalized
            else:
                warnings.append(
                    f"{name} could not be read as a date and was left as printed")
        fields_out[name] = value
        if probability is not None:
            probabilities.append(probability)
        try:
            if int(field.get("validityStatus")) == _CHECK_ERROR:
                warnings.append(f"{name} failed the reader's validity check")
        except (TypeError, ValueError):
            pass

    if not fields_out:
        warnings.append("no visa-sticker fields were recognized on this page")

    confidence = (min(probabilities) / 100.0) if probabilities else None
    return {
        "available": True,
        "fields": fields_out,
        "confidence": confidence,
        "warnings": warnings,
        "source": SOURCE,
        # Stated in the payload so no caller has to remember the rule.
        "identity_source": False,
        "note": "prior visa sticker only; identity still comes from the MRZ",
    }
