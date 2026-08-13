"""HRIS import — the employer's own payroll system, read once, confirmed by a human.

WHY THIS EXISTS
---------------
Trip.com HR already holds the facts an H-1B filing asks for: the title, the
wage and its period, the worksite, the hire date, whether the job is full time.
Retyping them into ETA-9035 and I-129 is how a transcription error becomes a
statement made under penalty of perjury. This seam pulls them once so the human
is CONFIRMING a value instead of composing one.

THE HONESTY RULE THAT SHAPES EVERY LINE BELOW
---------------------------------------------
An imported value is a SUGGESTION, never an answer. Nothing here writes
``CaseParty.answers``; this module holds no database session and takes no
``db`` argument, so "it can't auto-write" is a property of the code, not a
promise in a docstring. Every mapped value comes back wrapped as
``{value, confirmed: False, provider_field, note}`` — a shape a caller cannot
splat into an answers dict by accident, and one a review UI can render field by
field with its provenance attached.

Where a provider's value has no honest Ellis equivalent, the value is DROPPED
and a warning names it. A wage with an unmappable period ("QUARTER", "fixed")
yields a wage with no ``wage_offer_unit``: unfilled beats wrong, and a wage
number whose period Ellis guessed is the exact failure the LCA punishes.

PROVIDER CONTRACTS (researched live 2026-08-11; see CONTRACTS_AS_OF)
--------------------------------------------------------------------
Merge (unified HRIS, ``api.merge.dev``)
  * ``GET /api/hris/v1/employees`` with ``Authorization: Bearer <api key>``
    AND ``X-Account-Token: <linked account token>``. BOTH headers are required
    on every call: the API key identifies Ellis, the account token identifies
    WHICH employer's HRIS is being read. Ellis's config holds only the API key,
    so the account token is a per-employer argument — without it this provider
    reports unavailable rather than reading some other tenant's data.
  * ``expand=employments,work_location,company`` folds the position, the
    worksite and the employing entity into the one call.
  * The Employment object carries ``job_title, pay_rate, pay_period,
    pay_frequency, pay_currency, employment_type, effective_date``; Location
    carries ``street_1, street_2, city, state, zip_code, country``; Company
    carries ``legal_name, display_name, eins`` (an array of strings).
  * ``pay_rate`` is a plain decimal in major currency units.
  * The OpenAPI schema confirms ``remote_id`` and ``employee_number`` as
    lookup filters; ``work_email`` appears in the prose docs but NOT in the
    schema, so an email lookup is sent as a filter AND re-verified locally
    against every returned record. A server that silently ignored the filter
    gets a "no match" here, never someone else's salary.

Finch (``api.tryfinch.com``)
  * ``POST /employer/employment`` with ``{"requests": [{"individual_id": ...}]}``,
    headers ``Authorization: Bearer <access token>`` and
    ``Finch-API-Version: 2020-09-17``. Responses arrive as
    ``responses[].{individual_id, code, body}``.
  * The body carries ``title``, ``employment.{type,subtype}``, ``start_date``,
    ``location.{line1,line2,city,state,postal_code,country}`` and
    ``income.{unit,amount,currency}``. **``income.amount`` is in CENTS** — the
    single most dangerous difference between the two providers, converted here
    and asserted in the tests.
  * ``GET /employer/company`` carries ``legal_name`` and ``ein``.
  * Finch keys an employee by ``individual_id``. Its directory carries no email,
    so resolving one would mean pulling full individual records — which carry
    ``ssn`` and ``dob`` — for the whole company. Ellis declines and says so
    (see ``fetch_employee``); pulling a directory's SSNs to save a copy-paste
    is not a trade this product makes.

WHAT IS DELIBERATELY NOT READ
-----------------------------
The normalizers are allowlists, so nothing outside ``FIELD_KEYS`` can reach a
result even when the provider volunteers it. That excludes, on purpose:
``ssn``/``encrypted_ssn``, date of birth, gender, ethnicity, marital status,
personal email, home address — and Finch's company ``accounts``, which carries
bank account and routing numbers. ``include_remote_data`` is never requested:
the raw upstream payload is exactly the blob that would drag those fields in.
An H-1B petition does not need any of it.
"""
from __future__ import annotations

import datetime as _dt

from .. import dates
from ..config import settings

SOURCE = "hris"

# The date the provider contracts documented above were read from the vendors'
# own API references. A contract drifts; a stale one presented as current is
# what this date exists to expose.
CONTRACTS_AS_OF = "2026-08-11"

PROVIDERS = ("merge", "finch")

MERGE_BASE = "https://api.merge.dev/api/hris/v1"
FINCH_BASE = "https://api.tryfinch.com"
FINCH_API_VERSION = "2020-09-17"

_TIMEOUT_SECONDS = 20

# The provider-neutral vocabulary a normalizer may emit. An allowlist, not a
# convention: `_keep` drops everything else, so a provider that starts
# returning SSNs in a new field cannot leak one through this module.
FIELD_KEYS = (
    "employee_display_name", "employee_work_email",
    "job_title",
    "pay_rate", "pay_period", "pay_currency",
    "employment_type",
    "employment_start_date",
    "worksite_line1", "worksite_line2", "worksite_city", "worksite_state",
    "worksite_postal_code", "worksite_country",
    "employer_legal_name", "employer_trade_name", "employer_fein",
)

# Fields a provider may hand over that Ellis refuses to carry. Documented as
# data so the refusal is greppable and testable, not just prose.
NEVER_IMPORTED = (
    "ssn", "encrypted_ssn", "date_of_birth", "dob", "gender", "ethnicity",
    "marital_status", "personal_email", "home_location", "residence",
    "accounts",
)

# Wage-period vocabulary Ellis actually has (app/h1b/wage_data._UNIT_FACTORS).
# A provider period outside this map produces NO unit and a loud warning.
_MERGE_PAY_PERIOD = {
    "HOUR": "hour", "WEEK": "week", "EVERY_TWO_WEEKS": "biweek",
    "MONTH": "month", "YEAR": "year",
}
_FINCH_INCOME_UNIT = {
    "hourly": "hour", "weekly": "week", "bi_weekly": "biweek",
    "monthly": "month", "yearly": "year",
}

# Employment type -> the I-129/LCA full-time attestation. Only the two
# unambiguous cases map. INTERN, TEMPORARY, SEASONAL, CONTRACTOR and Finch's
# `contractor` type are left UNMAPPED on purpose: each is a different answer to
# a different question, and this one is signed under penalty of perjury.
_MERGE_FULL_TIME = {"FULL_TIME": True, "PART_TIME": False}
_FINCH_FULL_TIME = {"full_time": True, "part_time": False}

# The hire date an HRIS holds and the start date a petition asks for are not the
# same fact, and the difference is a real RFE. Said on the field itself so it
# travels with the value into whatever UI renders it.
START_DATE_NOTE = (
    "This is the employee's HRIS hire date. ETA-9035 and I-129 ask for the "
    "requested H-1B employment START date — the same value only for a new hire "
    "beginning on their H-1B start. On an extension or amendment it is the new "
    "period's start; confirm it before it goes on a form.")


class UnknownHrisProvider(ValueError):
    """The configured provider is not one this module has a real client for.
    Raised rather than guessed: an invented endpoint sends an employer's payroll
    data to a host nobody vetted."""


class HrisLookupInput(ValueError):
    """Raised BEFORE any network call when no usable identifier was supplied.
    A lookup with no identifier returns "an employee" — and the wrong salary on
    an LCA is a wage violation, not a typo."""


# ---------------------------------------------------------------------------
# HTTP seam — every network byte this module sends passes through here, so a
# test that stubs it proves nothing escaped to the real network.
# ---------------------------------------------------------------------------
def _http_json(method: str, url: str, *, headers: dict | None = None,
               params: dict | None = None,
               json_body: dict | None = None) -> tuple[int, dict]:
    import httpx
    r = httpx.request(method, url, headers=headers or {}, params=params,
                      json=json_body, timeout=_TIMEOUT_SECONDS)
    try:
        body = r.json()
    except Exception:  # noqa: BLE001 - a non-JSON body is an error body
        body = {}
    return r.status_code, (body if isinstance(body, dict) else {})


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def configured_provider() -> str:
    """The provider this deployment can actually call ('' when none)."""
    p = str(settings().hris_provider or "").strip().lower()
    return p if p in PROVIDERS and settings().hris_api_key else ""


def is_configured() -> bool:
    """True only when a KNOWN provider is named AND a key is present. A named
    provider with no key cannot answer, and a key with no provider has no
    endpoint — either way the employer types the facts exactly as today."""
    return bool(configured_provider())


def capability() -> dict:
    """What this seam will and will not do, honestly, at runtime."""
    provider = configured_provider()
    return {
        "source": SOURCE,
        "configured": bool(provider),
        "provider": provider or "none",
        "contracts_as_of": CONTRACTS_AS_OF,
        # Stated as False forever. See the module docstring.
        "writes_answers": False,
        "values_are_confirmed": False,
        "never_imported": list(NEVER_IMPORTED),
        "lookup_by": (("remote_id", "employee_number", "work_email")
                      if provider == "merge"
                      else ("individual_id",) if provider == "finch" else ()),
    }


def _unavailable(reason: str, *, provider: str = "",
                 warnings: list | None = None) -> dict:
    """The one shape an unavailable import takes: no fields, a reason, and
    nothing a caller could mistake for a fact."""
    return {
        "available": False,
        "fields": {},
        "source": SOURCE,
        "provider": provider or configured_provider() or "none",
        "fetched_at": None,
        "warnings": list(warnings or []),
        "reason": reason,
    }


def _now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------
def _text(value) -> str:
    if value is None or isinstance(value, (dict, list, bool)):
        return ""
    return str(value).strip()


def _keep(out: dict, key: str, value) -> None:
    """Write one normalized field, or nothing. The allowlist check is here, at
    the single write point, so no normalizer can bypass it."""
    if key not in FIELD_KEYS:
        raise AssertionError(f"{key} is not in the HRIS field allowlist")
    text = _text(value)
    if text:
        out[key] = text


def _number(value):
    """A pay rate as a float, or None. A rate that will not parse is dropped —
    a wage is not a field to be lenient about."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "").replace("$", "")
    try:
        return float(s)
    except ValueError:
        return None


def _money_str(amount: float) -> str:
    """A money amount as a plain decimal string, cents kept, never exponent
    notation. `%g` would render a 1,234,567 salary as '1.23457e+06' — a number
    no human confirms and no portal accepts."""
    return f"{amount:.2f}".rstrip("0").rstrip(".") or "0"


def _iso_date(value) -> str:
    """A provider date (ISO date or ISO datetime) as a canonical ISO calendar
    date. Anything else returns '' and the caller warns — never a guessed date."""
    s = _text(value)
    if not s:
        return ""
    head = s[:10]
    if dates.is_iso(head):
        return dates.normalize_any(head, kind="issue")
    return ""


def _digits(value) -> str:
    return "".join(ch for ch in _text(value) if ch.isdigit())


def _location_fields(out: dict, loc, *, line1_key: str, line2_key: str,
                     city_key: str, state_key: str, postal_key: str,
                     country_key: str) -> None:
    if not isinstance(loc, dict):
        return
    _keep(out, "worksite_line1", loc.get(line1_key))
    _keep(out, "worksite_line2", loc.get(line2_key))
    _keep(out, "worksite_city", loc.get(city_key))
    _keep(out, "worksite_state", loc.get(state_key))
    _keep(out, "worksite_postal_code", loc.get(postal_key))
    _keep(out, "worksite_country", loc.get(country_key))


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------
def _merge_headers(account_token: str) -> dict:
    return {
        "Authorization": f"Bearer {settings().hris_api_key}",
        "X-Account-Token": account_token,
        "Accept": "application/json",
    }


def _merge_identity_matches(employee: dict, *, external_id: str,
                            email: str) -> bool:
    """Does this record actually belong to the person who was asked for?

    Merge's OpenAPI schema does not list `work_email` as a filter even though
    the prose docs do. If the server ignored the filter it would return the
    whole roster, and the first row is a stranger. So the match is re-proved
    locally on every record before a single field is read."""
    if external_id:
        wanted = external_id.strip().lower()
        for key in ("remote_id", "employee_number", "id"):
            if _text(employee.get(key)).lower() == wanted:
                return True
        return False
    if email:
        return _text(employee.get("work_email")).lower() == email.strip().lower()
    return False


def _merge_current_employment(employments, warnings: list) -> dict:
    """The position in force. Merge returns a history; the newest
    `effective_date` wins. When no entry carries one, the first is used and the
    ambiguity is reported — a stale title and a current title look identical in
    an unordered list."""
    rows = [e for e in (employments or []) if isinstance(e, dict)]
    if not rows:
        return {}
    dated = [(_iso_date(e.get("effective_date")), e) for e in rows]
    dated = [(d, e) for d, e in dated if d]
    if dated:
        return max(dated, key=lambda pair: pair[0])[1]
    if len(rows) > 1:
        warnings.append(
            "the HRIS returned several employment records with no effective "
            "date; the first was used and the position must be confirmed")
    return rows[0]


def _normalize_merge(employee: dict, warnings: list) -> dict:
    out: dict = {}
    _keep(out, "employee_display_name", employee.get("display_full_name"))
    _keep(out, "employee_work_email", employee.get("work_email"))

    # Not a petition fact, so not a field — but importing a departed employee's
    # title and wage into a live petition is worth saying out loud.
    status = _text(employee.get("employment_status")).upper()
    if status and status != "ACTIVE":
        warnings.append(
            f"the HRIS marks this employee {status.lower()}, not active; "
            "confirm the employment before it is described on a petition")

    start = _iso_date(employee.get("start_date")) or _iso_date(employee.get("hire_date"))
    if start:
        _keep(out, "employment_start_date", start)
    elif employee.get("start_date") or employee.get("hire_date"):
        warnings.append("the HRIS start date could not be read as a date and "
                        "was dropped")

    employment = _merge_current_employment(employee.get("employments"), warnings)
    _keep(out, "job_title", employment.get("job_title"))
    # Merge states pay_rate in MAJOR currency units (unlike Finch).
    rate = _number(employment.get("pay_rate"))
    if rate is not None:
        _keep(out, "pay_rate", _money_str(rate))
    elif employment.get("pay_rate") is not None:
        warnings.append("the HRIS pay rate could not be read as a number and "
                        "was dropped")
    _keep(out, "pay_period", employment.get("pay_period"))
    _keep(out, "pay_currency", employment.get("pay_currency"))
    _keep(out, "employment_type", employment.get("employment_type"))

    _location_fields(out, employee.get("work_location"),
                     line1_key="street_1", line2_key="street_2",
                     city_key="city", state_key="state",
                     postal_key="zip_code", country_key="country")

    company = employee.get("company")
    if isinstance(company, dict):
        _keep(out, "employer_legal_name", company.get("legal_name"))
        _keep(out, "employer_trade_name", company.get("display_name"))
        eins = company.get("eins")
        # `eins` is an array of strings and an employer may hold several. One
        # is a suggestion; several is a choice a human makes, so Ellis carries
        # none and says why.
        if isinstance(eins, list):
            usable = [_digits(e) for e in eins if _digits(e)]
            if len(usable) == 1:
                _keep(out, "employer_fein", usable[0])
            elif len(usable) > 1:
                warnings.append(
                    "the HRIS lists more than one employer identification "
                    "number; the petitioning entity's FEIN must be chosen by a "
                    "human")
    return out


def _fetch_merge(*, external_id: str, email: str, account_token: str) -> dict:
    if not account_token:
        return _unavailable(
            "Merge reads one employer's HRIS per linked-account token; supply "
            "the employer's Merge account token to import their data",
            provider="merge")

    params: dict = {"expand": "employments,work_location,company",
                    "page_size": 10}
    if external_id:
        params["remote_id"] = external_id
    if email:
        # Sent as a filter and re-verified locally; see _merge_identity_matches.
        params["work_email"] = email

    try:
        status, body = _http_json("GET", f"{MERGE_BASE}/employees",
                                  headers=_merge_headers(account_token),
                                  params=params)
    except Exception:  # noqa: BLE001 - never surface transport internals
        return _unavailable("the HRIS is unreachable", provider="merge")
    if status >= 400:
        return _unavailable(f"the HRIS returned HTTP {status}", provider="merge")

    results = body.get("results")
    rows = [r for r in (results or []) if isinstance(r, dict)]
    matched = [r for r in rows
               if _merge_identity_matches(r, external_id=external_id, email=email)]
    if not matched:
        return _unavailable(
            "no employee in the HRIS matched that identifier", provider="merge")
    if len(matched) > 1:
        return _unavailable(
            "more than one employee in the HRIS matched that identifier; "
            "Ellis will not choose between them", provider="merge")

    warnings: list[str] = []
    fields = _normalize_merge(matched[0], warnings)
    return {
        "available": bool(fields),
        "fields": fields,
        "source": SOURCE,
        "provider": "merge",
        "fetched_at": _now_iso(),
        "warnings": warnings,
        "reason": ("" if fields else
                   "the HRIS record held none of the facts a petition needs"),
    }


# ---------------------------------------------------------------------------
# Finch
# ---------------------------------------------------------------------------
def _finch_headers() -> dict:
    return {
        "Authorization": f"Bearer {settings().hris_api_key}",
        "Finch-API-Version": FINCH_API_VERSION,
        "Accept": "application/json",
    }


def _normalize_finch(employment: dict, company: dict, warnings: list) -> dict:
    out: dict = {}
    first = _text(employment.get("first_name"))
    last = _text(employment.get("last_name"))
    _keep(out, "employee_display_name", " ".join(p for p in (first, last) if p))
    _keep(out, "job_title", employment.get("title"))

    if employment.get("is_active") is False:
        warnings.append(
            "the HRIS marks this employment inactive; confirm the employment "
            "before it is described on a petition")

    emp = employment.get("employment")
    if isinstance(emp, dict):
        # subtype carries full_time/part_time; type carries employee/contractor.
        _keep(out, "employment_type",
              _text(emp.get("subtype")) or _text(emp.get("type")))

    start = _iso_date(employment.get("start_date"))
    if start:
        _keep(out, "employment_start_date", start)
    elif employment.get("start_date"):
        warnings.append("the HRIS start date could not be read as a date and "
                        "was dropped")

    income = employment.get("income")
    if isinstance(income, dict):
        # Finch states income.amount in CENTS. Converted here, once.
        cents = _number(income.get("amount"))
        if cents is not None:
            _keep(out, "pay_rate", _money_str(cents / 100.0))
        elif income.get("amount") is not None:
            warnings.append("the HRIS pay amount could not be read as a number "
                            "and was dropped")
        _keep(out, "pay_period", income.get("unit"))
        _keep(out, "pay_currency", income.get("currency"))

    _location_fields(out, employment.get("location"),
                     line1_key="line1", line2_key="line2", city_key="city",
                     state_key="state", postal_key="postal_code",
                     country_key="country")

    if isinstance(company, dict):
        _keep(out, "employer_legal_name", company.get("legal_name"))
        _keep(out, "employer_fein", _digits(company.get("ein")))
    return out


def _fetch_finch(*, external_id: str, email: str) -> dict:
    if email and not external_id:
        return _unavailable(
            "Finch identifies an employee by individual_id; its directory "
            "carries no email, and resolving one would mean pulling every "
            "individual record — which carries SSN and date of birth — for the "
            "whole company. Supply the Finch individual_id.",
            provider="finch")

    try:
        status, body = _http_json(
            "POST", f"{FINCH_BASE}/employer/employment",
            headers=_finch_headers(),
            json_body={"requests": [{"individual_id": external_id}]})
    except Exception:  # noqa: BLE001 - never surface transport internals
        return _unavailable("the HRIS is unreachable", provider="finch")
    if status >= 400:
        return _unavailable(f"the HRIS returned HTTP {status}", provider="finch")

    rows = [r for r in (body.get("responses") or []) if isinstance(r, dict)]
    # Finch echoes the requested id per row; only the row that echoes OUR id is
    # read. A batch API that reorders or pads its responses must not be able to
    # hand this employee somebody else's wage.
    matched = [r for r in rows
               if _text(r.get("individual_id")) == external_id]
    if not matched:
        return _unavailable(
            "no employee in the HRIS matched that identifier", provider="finch")
    row = matched[0]
    try:
        code = int(row.get("code"))
    except (TypeError, ValueError):
        code = 0
    employment = row.get("body")
    if code >= 400 or not isinstance(employment, dict):
        return _unavailable(
            "the HRIS could not return employment data for that employee",
            provider="finch")

    warnings: list[str] = []
    # The company call is a bonus, not a requirement: a failure here costs the
    # employer name and FEIN, not the import.
    company: dict = {}
    try:
        c_status, c_body = _http_json("GET", f"{FINCH_BASE}/employer/company",
                                      headers=_finch_headers())
        if c_status < 400:
            company = c_body
        else:
            warnings.append("the employer record could not be read from the "
                            "HRIS; the employer facts were left blank")
    except Exception:  # noqa: BLE001
        warnings.append("the employer record could not be read from the HRIS; "
                        "the employer facts were left blank")

    fields = _normalize_finch(employment, company, warnings)
    return {
        "available": bool(fields),
        "fields": fields,
        "source": SOURCE,
        "provider": "finch",
        "fetched_at": _now_iso(),
        "warnings": warnings,
        "reason": ("" if fields else
                   "the HRIS record held none of the facts a petition needs"),
    }


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------
def fetch_employee(external_id: str = "", *, email: str = "",
                   account_token: str = "") -> dict:
    """Pull one employee's employment facts from the configured HRIS.

    Args:
      external_id:   the employer's own id for this person — Merge `remote_id`
                     / `employee_number`, or a Finch `individual_id`.
      email:         work email, an alternative identifier on Merge only.
      account_token: Merge's per-employer linked-account token. Required for
                     Merge (it selects WHOSE HRIS is read) and unused by Finch.

    Returns ``{available, fields, source, provider, fetched_at, warnings}``
    and, when unavailable, ``reason``. `fields` uses the provider-neutral
    ``FIELD_KEYS`` vocabulary; run it through `to_party_answers` to reach
    Ellis's petition vocabulary.

    Unconfigured, unreachable, or unmatched, `available` is False with a reason
    and `fields` is empty — never a partially invented employee. Raises
    `HrisLookupInput` before any network call when no identifier is given, and
    `UnknownHrisProvider` when the configured provider has no client here.
    """
    ext = _text(external_id)
    mail = _text(email)
    if not ext and not mail:
        raise HrisLookupInput(
            "an employee identifier (external_id or email) is required")

    if not is_configured():
        named = str(settings().hris_provider or "").strip().lower()
        if named and named not in PROVIDERS:
            raise UnknownHrisProvider(
                f"unknown HRIS provider: expected one of {'|'.join(PROVIDERS)}")
        return _unavailable("no HRIS provider is configured", provider="none")

    provider = configured_provider()
    if provider == "merge":
        return _fetch_merge(external_id=ext, email=mail,
                            account_token=_text(account_token))
    if provider == "finch":
        return _fetch_finch(external_id=ext, email=mail)
    raise UnknownHrisProvider(  # pragma: no cover - configured_provider gates it
        f"unknown HRIS provider: expected one of {'|'.join(PROVIDERS)}")


def _suggestion(value, provider_field: str, note: str = "") -> dict:
    """One mapped value, in the only shape this module emits.

    `confirmed: False` is not decoration. It is the reason a caller cannot take
    this dict for an answer: `CaseParty.answers` holds scalars, so a suggestion
    that reached it unread would be visibly, loudly wrong rather than quietly
    filed on a government form."""
    return {"value": value, "confirmed": False, "source": SOURCE,
            "provider_field": provider_field, "note": note}


def to_party_answers(fields: dict) -> dict:
    """Map normalized HRIS fields onto Ellis's petitioner vocabulary.

    Returns::

        {"suggestions": {answer_key: {value, confirmed: False, source,
                                      provider_field, note}, ...},
         "unconfirmed": True, "writes_answers": False,
         "warnings": [...], "dropped": [...], "source": "hris"}

    Every entry is `confirmed: False`. NOTHING here writes `CaseParty.answers`
    — this function takes no case, no party and no session, and its output is
    deliberately not answer-shaped. A value with no honest Ellis equivalent is
    DROPPED into `dropped` with the reason, never coerced: an unmappable wage
    period yields a `wage_offer` with no `wage_offer_unit`, because an LCA wage
    whose period Ellis guessed is a wage violation waiting to be found.
    """
    src = dict(fields or {})
    out: dict = {}
    warnings: list[str] = []
    dropped: list[dict] = []

    def put(key: str, value, provider_field: str, note: str = "") -> None:
        if _text(value):
            out[key] = _suggestion(_text(value), provider_field, note)

    put("job_title", src.get("job_title"), "job_title")
    put("employer_legal_name", src.get("employer_legal_name"), "employer_legal_name")
    put("employer_dba", src.get("employer_trade_name"), "employer_trade_name")
    put("employer_fein", src.get("employer_fein"), "employer_fein",
        "Confirm against the CP-575 / FEIN evidence in the case: the HRIS "
        "entity and the PETITIONING entity are not always the same company.")

    put("worksite_address_line1", src.get("worksite_line1"), "worksite_line1")
    put("worksite_address_city", src.get("worksite_city"), "worksite_city")
    put("worksite_address_state", src.get("worksite_state"), "worksite_state")
    put("worksite_address_zip", src.get("worksite_postal_code"),
        "worksite_postal_code")

    put("employment_start_date", src.get("employment_start_date"),
        "employment_start_date", START_DATE_NOTE)

    # --- wage: the amount and its period travel together or not at all ------
    raw_period = _text(src.get("pay_period")).strip()
    period = (_MERGE_PAY_PERIOD.get(raw_period.upper())
              or _FINCH_INCOME_UNIT.get(raw_period.lower()))
    rate = _text(src.get("pay_rate"))
    if rate:
        note = ("Gross wage as recorded in payroll. The LCA wage is the CASH "
                "wage offered for the H-1B position; bonuses and equity do not "
                "count toward it.")
        if not period:
            note += (" The payroll period could not be mapped, so no wage "
                     "period was imported — set it by hand.")
        put("wage_offer", rate, "pay_rate", note)
    if period:
        put("wage_offer_unit", period, "pay_period")
    elif raw_period:
        dropped.append({"answer_key": "wage_offer_unit", "value": raw_period,
                        "reason": ("the HRIS pay period has no Ellis wage "
                                   "period; a guessed period is a wage "
                                   "violation, so none was imported")})
        warnings.append(
            f"the HRIS pay period '{raw_period}' has no Ellis equivalent; the "
            "offered wage period was left unset")

    currency = _text(src.get("pay_currency")).upper()
    if currency and currency != "USD":
        warnings.append(
            f"the HRIS states this wage in {currency}; the LCA wage must be "
            "stated in USD")

    # --- full-time attestation: only the unambiguous cases ------------------
    raw_type = _text(src.get("employment_type"))
    full_time = (_MERGE_FULL_TIME.get(raw_type.upper())
                 if raw_type.upper() in _MERGE_FULL_TIME
                 else _FINCH_FULL_TIME.get(raw_type.lower()))
    if full_time is not None:
        out["full_time_position"] = _suggestion(
            "Yes" if full_time else "No", "employment_type",
            "This is an attestation signed under penalty of perjury. Confirm "
            "it against the offer letter, not the payroll record.")
    elif raw_type:
        dropped.append({"answer_key": "full_time_position", "value": raw_type,
                        "reason": ("only FULL_TIME and PART_TIME map to the "
                                   "full-time attestation; everything else is "
                                   "a different answer to a different question")})
        warnings.append(
            f"the HRIS employment type '{raw_type}' does not answer the "
            "full-time attestation; it was left unanswered")

    return {
        "suggestions": out,
        # Said in the payload, not only in the docstring, so a caller reading
        # the dict at runtime learns the rule without reading this file.
        "unconfirmed": True,
        "writes_answers": False,
        "warnings": warnings,
        "dropped": dropped,
        "source": SOURCE,
        "note": ("imported employment facts are suggestions for a human to "
                 "confirm; none of them is an answer until a person says so"),
    }
