"""International address validation — a SUGGESTION, never a silent rewrite.

WHY THIS EXISTS
---------------
Worksite and residential addresses are among the highest-friction fields Ellis
fills. They are typed by a human under time pressure, they are re-typed on
every form, and a wrong one is expensive in a specific way: a worksite address
on an LCA is a LEGAL STATEMENT about where the work happens (it sets the
prevailing-wage area of intended employment), and a residential address on a
consular form is a statement about where the applicant lives. So this module
raises fill accuracy on the fields that carry the most weight.

THE HONESTY RULE THAT SHAPES THE WHOLE MODULE
---------------------------------------------
A 'corrected' address is a SUGGESTION the human accepts or rejects. Ellis never
silently rewrites an address that becomes a statement on a government form. A
vendor that "improves" a street name has changed a legal statement, and the
person who signs the form is the only one entitled to do that.

That rule is enforced structurally, not by convention:

  * `verify_address` NEVER mutates the address it was given, and the address it
    returns under `normalized` is a separate dict labelled as a suggestion.
  * Every result carries `auto_apply: False`. It is a constant. There is no
    code path that sets it True.
  * The only way a suggestion becomes the address is `apply_correction(...,
    accepted=True)` — and `accepted` must be the literal True, so a truthy
    string from a checkbox that was never really checked cannot overwrite a
    legal statement.

UNVERIFIED IS NOT INVALID
-------------------------
An address the vendor could not confirm is an address the vendor could not
confirm. Reference data is thin in much of the world (Smarty and Loqate both
degrade to locality or thoroughfare precision in many countries), and a real
address in a village with no delivery-point file comes back unverified. Ellis
therefore never blocks, never flags as wrong, and never drops an unverified
address: it says so, with a reason, and the human proceeds.

EDITION-NEUTRAL
---------------
Nothing here knows what visa is being applied for. It takes an address and a
country and returns an assessment. The H1B worksite and the tourist
residential-address field call it identically.

PROVIDERS (contracts read from vendor docs on AS_OF)
----------------------------------------------------
smarty  GET https://international-street.api.smarty.com/verify
        auth: auth-id + auth-token (server-to-server) or key= (embedded key)
        input: country + address1..4 / locality / administrative_area /
               postal_code, or freeform
        output: a JSON ARRAY of candidates, each with `components`,
                `metadata` and `analysis`. `analysis.verification_status` is
                one of Verified | Partial | Ambiguous | None, and
                `analysis.address_precision` is None | AdministrativeArea |
                Locality | Thoroughfare | Premise | DeliveryPoint.
        source: https://www.smarty.com/docs/cloud/international-street-api

loqate  POST https://api.addressy.com/Cleansing/International/Batch/v1.00/json4.ws
        auth: Key
        output: {"Items": [{"Matches": [{...}]}]} (a flat Items list of matches
                is also tolerated). The verification verdict is the AVC's first
                character: V verified, P partially verified, A ambiguous,
                R reverted, U unverified.
        source: https://docs.loqate.com/report-codes/address-verification-code

An unrecognized provider name is NO provider — the deployment is unconfigured
and says so, rather than half-calling something Ellis does not understand.
"""
from __future__ import annotations

from ..config import settings

AS_OF = "2026-08-11"

SOURCE_SMARTY = "smarty"
SOURCE_LOQATE = "loqate"
SOURCE_NONE = "none"

# The three answers this module is allowed to give.
STATUS_VERIFIED = "verified"
STATUS_CORRECTED = "corrected"
STATUS_UNVERIFIED = "unverified"

_SMARTY_URL = "https://international-street.api.smarty.com/verify"
_LOQATE_URL = ("https://api.addressy.com/Cleansing/International/Batch/"
               "v1.00/json4.ws")
_TIMEOUT_SECONDS = 20

# Said in the payload of every unverified result so no UI has to remember it.
UNVERIFIED_NOTE = (
    "unverified is not invalid: the provider could not confirm this address "
    "against its reference data, which is not evidence that the address is "
    "wrong. Use it as typed.")

CORRECTED_NOTE = (
    "this is a SUGGESTION. Ellis does not rewrite an address that becomes a "
    "statement on a government form — a human accepts or rejects it.")

# Canonical field names. `normalized` is keyed by exactly these, so a caller
# never has to learn a vendor's field names.
CANONICAL_FIELDS = ("address1", "address2", "locality",
                    "administrative_area", "postal_code")

# What callers actually type. The country is deliberately NOT a canonical
# field: it is the caller's input to the lookup, and a vendor's ISO-3 rendering
# of it is informational (it rides in `components`), never a "correction" to
# a country the human already chose.
_ALIASES: dict[str, tuple[str, ...]] = {
    "address1": ("address1", "address_line_1", "line1", "street",
                 "street_address", "address"),
    "address2": ("address2", "address_line_2", "line2", "unit", "apt",
                 "suite"),
    "locality": ("locality", "city", "town"),
    "administrative_area": ("administrative_area", "admin_area", "state",
                            "province", "region"),
    "postal_code": ("postal_code", "postalcode", "postcode", "zip",
                    "zip_code"),
    "organization": ("organization", "company", "employer"),
    "freeform": ("freeform",),
}

# Smarty precision levels, weakest first. Used only to explain WHY a result is
# unverified; never to upgrade one.
_PRECISION_ORDER = ("None", "AdministrativeArea", "Locality", "Thoroughfare",
                    "Premise", "DeliveryPoint")

# Loqate AVC verification-status letters (first character of the code).
_LOQATE_STATUS = {
    "V": ("verified", "a complete match against a single reference record"),
    "P": ("partial", "only part of the address matched a reference record"),
    "A": ("ambiguous", "several reference records matched this address"),
    "R": ("reverted", "the address did not meet the provider's minimum "
                      "verification standard; the input was returned unchanged"),
    "U": ("unverified", "the provider could not verify this address"),
}


class InvalidAddress(ValueError):
    """The input is not an address. Raised BEFORE any network call so a blank
    or malformed value is never sent to a vendor and never comes back wearing
    a verdict."""


# ---------------------------------------------------------------------------
# HTTP seam — every network byte this module sends passes through here, so a
# test that stubs it proves nothing escaped to the real network.
# ---------------------------------------------------------------------------
def _http(method: str, url: str, *, headers: dict | None = None,
          params: dict | None = None,
          json_body: dict | None = None) -> tuple[int, object]:
    import httpx
    r = httpx.request(method, url, headers=headers or {}, params=params,
                      json=json_body, timeout=_TIMEOUT_SECONDS)
    try:
        body = r.json()
    except Exception:  # noqa: BLE001 - a non-JSON body is an error body
        body = {}
    return r.status_code, body


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def active_provider() -> str:
    """The provider this deployment actually holds, or ''. An unrecognized name
    in the env is no provider at all — Ellis does not half-call a vendor whose
    contract it has not read."""
    name = (settings().address_verify_provider or "").strip().lower()
    return name if name in (SOURCE_SMARTY, SOURCE_LOQATE) else ""


def is_configured() -> bool:
    """True only when a known provider AND a key are present. Unconfigured, the
    human types the address exactly as today and nothing is invented."""
    return bool(active_provider()) and bool(settings().address_verify_key)


# ---------------------------------------------------------------------------
# Input handling
# ---------------------------------------------------------------------------
def _scalar(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        raise InvalidAddress("address values must be text")
    if isinstance(value, (int, float)):
        return str(value)
    if not isinstance(value, str):
        raise InvalidAddress("address values must be text")
    return value.strip()


def canonical_address(address: dict) -> dict:
    """Map a caller's address dict onto the canonical field names. Unknown keys
    are ignored; nothing is invented and no value is reformatted."""
    if not isinstance(address, dict):
        raise InvalidAddress("address must be a dict")
    lowered = {str(k).strip().lower(): v for k, v in address.items()}
    out: dict[str, str] = {}
    for canonical, aliases in _ALIASES.items():
        for alias in aliases:
            if alias in lowered:
                value = _scalar(lowered[alias])
                if value:
                    out[canonical] = value
                break
    return out


def _compare_key(value: str) -> str:
    """Comparison form: case-folded, punctuation-light, whitespace-collapsed.
    Used only to decide whether a vendor CHANGED something; the values a caller
    sees are always the untouched originals and the vendor's own output."""
    text = str(value or "")
    for ch in ",.":
        text = text.replace(ch, " ")
    return " ".join(text.split()).casefold()


def _diff(original: dict, normalized: dict) -> dict:
    """{field: {'from': original, 'to': suggested}} for every canonical field
    the vendor would change. A field the vendor did not return is never
    reported as a change to blank."""
    changes: dict[str, dict] = {}
    for field in CANONICAL_FIELDS:
        suggested = str(normalized.get(field) or "").strip()
        if not suggested:
            continue
        current = str(original.get(field) or "").strip()
        if _compare_key(current) != _compare_key(suggested):
            changes[field] = {"from": current, "to": suggested}
    return changes


# ---------------------------------------------------------------------------
# Result construction
# ---------------------------------------------------------------------------
def _result(*, available: bool, status: str, normalized: dict | None,
            components: dict, changes: dict, warnings: list,
            source: str, note: str) -> dict:
    return {
        "available": available,
        "status": status,
        # A SUGGESTION, keyed by CANONICAL_FIELDS, or None when there is none.
        "normalized": normalized,
        "components": components,
        "changes": changes,
        "warnings": warnings,
        "source": source,
        # A constant. No code path sets this True — see the module docstring.
        "auto_apply": False,
        "requires_human_acceptance": status == STATUS_CORRECTED,
        "note": note,
    }


def _unavailable(note: str, *, source: str = SOURCE_NONE) -> dict:
    return _result(available=False, status=STATUS_UNVERIFIED, normalized=None,
                   components={}, changes={}, warnings=[], source=source,
                   note=f"{note}; {UNVERIFIED_NOTE}")


# ---------------------------------------------------------------------------
# Smarty
# ---------------------------------------------------------------------------
def _smarty_auth(key: str) -> dict:
    """Smarty's server-to-server credential is an auth-id/auth-token PAIR, held
    here as 'id:token'. A single value is sent as an embedded `key`, which is
    Smarty's other documented form. The credential goes into the request and
    nowhere else — never into a warning, a note, or a log."""
    if ":" in key:
        auth_id, _, auth_token = key.partition(":")
        return {"auth-id": auth_id.strip(), "auth-token": auth_token.strip()}
    return {"key": key}


def _str_map(value) -> dict:
    """A vendor object reduced to its scalar entries. Nested structures are
    dropped rather than guessed at."""
    if not isinstance(value, dict):
        return {}
    out = {}
    for k, v in value.items():
        if isinstance(v, (str, int, float)) and not isinstance(v, bool):
            text = str(v).strip()
            if text:
                out[str(k)] = text
    return out


def _smarty_normalized(candidate: dict) -> dict:
    """The suggestion, in canonical fields.

    `address1` is the vendor's first postal-format line — the street line. The
    remaining address2..address12 lines are the vendor's rendering of city,
    region and postal code for an envelope; they are surfaced under
    `components.formatted_lines` rather than proposed as a second street line,
    because re-splitting a locale-specific postal format is exactly the kind of
    guess that puts a wrong string on a form.
    """
    components = _str_map(candidate.get("components"))
    normalized: dict[str, str] = {}
    line1 = str(candidate.get("address1") or "").strip()
    if line1:
        normalized["address1"] = line1
    for canonical, key in (("locality", "locality"),
                           ("administrative_area", "administrative_area"),
                           ("postal_code", "postal_code")):
        value = components.get(key, "")
        if value:
            normalized[canonical] = value
    return normalized


def _verify_smarty(canonical: dict, country: str, key: str) -> dict:
    params: dict = {"country": country}
    params.update(_smarty_auth(key))
    for field in ("address1", "address2", "locality", "administrative_area",
                  "postal_code", "organization", "freeform"):
        value = canonical.get(field)
        if value:
            params[field] = value

    try:
        code, body = _http("GET", _SMARTY_URL, params=params,
                           headers={"accept": "application/json"})
    except Exception:  # noqa: BLE001 - never surface transport internals,
        # which can echo the request (and therefore the credential) back.
        return _unavailable("address verification is unreachable",
                            source=SOURCE_SMARTY)
    if code in (401, 402):
        return _unavailable(
            "address verification rejected this deployment's credentials",
            source=SOURCE_SMARTY)
    if code >= 400:
        return _unavailable(f"address verification returned HTTP {code}",
                            source=SOURCE_SMARTY)

    candidates = [c for c in body if isinstance(c, dict)] if isinstance(
        body, list) else []
    if not candidates:
        return _result(
            available=True, status=STATUS_UNVERIFIED, normalized=None,
            components={}, changes={},
            warnings=["the provider returned no candidate for this address"],
            source=SOURCE_SMARTY, note=UNVERIFIED_NOTE)

    warnings: list[str] = []
    if len(candidates) > 1:
        warnings.append(
            f"the provider returned {len(candidates)} candidate addresses; "
            "a human must choose")

    candidate = candidates[0]
    analysis = candidate.get("analysis") if isinstance(
        candidate.get("analysis"), dict) else {}
    verification = str(analysis.get("verification_status") or "").strip()
    precision = str(analysis.get("address_precision") or "").strip()

    normalized = _smarty_normalized(candidate)
    components = _str_map(candidate.get("components"))
    lines = [str(candidate.get(f"address{i}") or "").strip() for i in range(1, 13)]
    lines = [line for line in lines if line]
    if lines:
        components["formatted_lines"] = " | ".join(lines)
    changes = _diff(canonical, normalized)

    if verification == "Verified" and len(candidates) == 1:
        status = STATUS_CORRECTED if changes else STATUS_VERIFIED
    else:
        status = STATUS_UNVERIFIED
        if verification == "Partial":
            warnings.append(
                "the provider matched this address only to "
                f"{precision or 'a lower'} precision")
        elif verification == "Ambiguous":
            warnings.append(
                "the provider found several possible matches for this address")
        elif verification in ("None", ""):
            warnings.append(
                "the provider could not match this address to its reference "
                "data")
        if precision and precision in _PRECISION_ORDER:
            max_precision = str(analysis.get("max_address_precision") or "").strip()
            if max_precision and max_precision != precision:
                warnings.append(
                    f"this country supports {max_precision} precision; this "
                    f"address reached {precision}")

    note = CORRECTED_NOTE if status == STATUS_CORRECTED else (
        UNVERIFIED_NOTE if status == STATUS_UNVERIFIED
        else "the provider confirmed this address as typed")
    return _result(available=True, status=status,
                   normalized=normalized or None, components=components,
                   changes=changes, warnings=warnings, source=SOURCE_SMARTY,
                   note=note)


# ---------------------------------------------------------------------------
# Loqate
# ---------------------------------------------------------------------------
def _loqate_match(body) -> dict:
    """The first match, tolerating both documented envelopes: an Items list of
    {"Matches": [...]} and a flat Items list of matches."""
    items = body.get("Items") if isinstance(body, dict) else None
    if not isinstance(items, list):
        return {}
    for item in items:
        if not isinstance(item, dict):
            continue
        matches = item.get("Matches")
        if isinstance(matches, list):
            for match in matches:
                if isinstance(match, dict):
                    return match
            continue
        if item:
            return item
    return {}


def _loqate_normalized(match: dict) -> dict:
    """Canonical fields from Loqate's structured output. As with Smarty, only
    Address1 is proposed as the street line; Address2..8 are envelope lines and
    are surfaced as components, never re-split into form fields."""
    normalized: dict[str, str] = {}
    line1 = str(match.get("Address1") or "").strip()
    if line1:
        normalized["address1"] = line1
    for canonical, key in (("locality", "Locality"),
                           ("administrative_area", "AdministrativeArea"),
                           ("postal_code", "PostalCode")):
        value = str(match.get(key) or "").strip()
        if value:
            normalized[canonical] = value
    return normalized


def _verify_loqate(canonical: dict, country: str, key: str) -> dict:
    single_line = ", ".join(
        canonical[f] for f in ("address1", "address2", "locality",
                               "administrative_area", "postal_code")
        if canonical.get(f)) or canonical.get("freeform", "")
    payload = {"Key": key, "Geocode": False,
               "Addresses": [{"Address": single_line, "Country": country}]}
    try:
        code, body = _http("POST", _LOQATE_URL, json_body=payload,
                           headers={"content-type": "application/json",
                                    "accept": "application/json"})
    except Exception:  # noqa: BLE001 - never surface transport internals
        return _unavailable("address verification is unreachable",
                            source=SOURCE_LOQATE)
    if code >= 400:
        return _unavailable(f"address verification returned HTTP {code}",
                            source=SOURCE_LOQATE)

    match = _loqate_match(body)
    if not match:
        return _result(
            available=True, status=STATUS_UNVERIFIED, normalized=None,
            components={}, changes={},
            warnings=["the provider returned no candidate for this address"],
            source=SOURCE_LOQATE, note=UNVERIFIED_NOTE)
    # Loqate reports an error as an Items entry carrying an Error code.
    if match.get("Error"):
        return _unavailable("address verification returned an error",
                            source=SOURCE_LOQATE)

    avc = str(match.get("AVC") or "").strip().upper()
    letter = avc[:1]
    known = _LOQATE_STATUS.get(letter)

    normalized = _loqate_normalized(match)
    components = {k: v for k, v in _str_map(match).items()
                  if k in ("AQI", "AVC", "CountryName", "ISO3166-2",
                           "ISO3166-3", "Address", "Thoroughfare", "Premise",
                           "SubBuilding", "Building", "DependentLocality")}
    changes = _diff(canonical, normalized)

    warnings: list[str] = []
    if known is None:
        # An unrecognized verification code is not a verification. Ellis does
        # not promote a code it cannot read into a verdict.
        status = STATUS_UNVERIFIED
        warnings.append(
            "the provider returned a verification code Ellis does not "
            "recognize, so this address is treated as unverified")
    elif letter == "V":
        status = STATUS_CORRECTED if changes else STATUS_VERIFIED
    else:
        status = STATUS_UNVERIFIED
        warnings.append(f"the provider reported: {known[1]}")

    note = CORRECTED_NOTE if status == STATUS_CORRECTED else (
        UNVERIFIED_NOTE if status == STATUS_UNVERIFIED
        else "the provider confirmed this address as typed")
    return _result(available=True, status=status,
                   normalized=normalized or None, components=components,
                   changes=changes, warnings=warnings, source=SOURCE_LOQATE,
                   note=note)


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------
def verify_address(address: dict, country: str = "") -> dict:
    """Validate and normalize one international address.

    Returns {available, status, normalized, components, changes, warnings,
    source, auto_apply, requires_human_acceptance, note}:

      available   False whenever no provider could answer (unconfigured,
                  unreachable, credentials rejected, HTTP error). Never faked.
      status      'verified'   the provider confirmed the address AS TYPED.
                  'corrected'  the provider confirmed a DIFFERENT address. This
                               is a SUGGESTION — see `changes`, and see
                               `apply_correction`. Ellis does not apply it.
                  'unverified' the provider could not confirm it. NOT invalid:
                               reference data is thin in much of the world.
      normalized  the suggestion keyed by CANONICAL_FIELDS, or None. It is a
                  new dict; the address passed in is never mutated.
      changes     {field: {'from','to'}} — exactly what accepting would change.
      warnings    human-readable, non-sensitive reasons. Never a credential,
                  never a vendor response body.
      auto_apply  always False.

    Raises InvalidAddress for a non-dict address, a missing country, or an
    address with no street line and no freeform value — before the provider is
    even asked whether it is configured, so a blank never reaches a vendor.
    """
    canonical = canonical_address(address)
    country_value = _scalar(country) or _scalar(
        (address or {}).get("country") if isinstance(address, dict) else "")
    if not country_value:
        raise InvalidAddress("country is required to verify an address")
    if not (canonical.get("address1") or canonical.get("freeform")):
        raise InvalidAddress(
            "a street line (address1) or a freeform address is required")

    provider = active_provider()
    if not provider:
        return _unavailable("address verification is not configured")
    key = settings().address_verify_key
    if not key:
        return _unavailable("address verification has no credentials",
                            source=provider)

    if provider == SOURCE_SMARTY:
        return _verify_smarty(canonical, country_value, key)
    return _verify_loqate(canonical, country_value, key)


def apply_correction(address: dict, result: dict, *, accepted: bool) -> dict:
    """The ONLY way a suggestion becomes the address.

    Returns a NEW dict. Unless `accepted` is the literal True, that dict is the
    address exactly as it was given — a truthy string from a checkbox that was
    never really ticked cannot rewrite a statement on a government form.

    Accepting merges the canonical suggestion over the caller's fields; every
    other key the caller carries (country, notes, ids) is preserved.
    """
    if not isinstance(address, dict):
        raise InvalidAddress("address must be a dict")
    merged = dict(address)
    if accepted is not True:
        return merged
    normalized = (result or {}).get("normalized")
    if not isinstance(normalized, dict) or not normalized:
        return merged
    # Write onto the caller's own key names where they exist, so an accepted
    # correction lands in the field the caller already fills from.
    lowered = {str(k).strip().lower(): k for k in address}
    for field, value in normalized.items():
        if field not in CANONICAL_FIELDS:
            continue
        target = field
        for alias in _ALIASES.get(field, ()):
            if alias in lowered:
                target = lowered[alias]
                break
        merged[target] = value
    return merged


def review_prompt(result: dict) -> str:
    """One line for a human deciding on a correction, or '' when there is
    nothing to decide. The wording never asserts the suggestion is right."""
    if not isinstance(result, dict) or result.get("status") != STATUS_CORRECTED:
        return ""
    changes = result.get("changes") or {}
    parts = [f"{field}: '{c.get('from', '')}' -> '{c.get('to', '')}'"
             for field, c in changes.items()]
    if not parts:
        return ""
    return ("The address verifier suggests a different address. Accept it only "
            "if it is correct — this address goes on a government form. "
            + "; ".join(parts))
