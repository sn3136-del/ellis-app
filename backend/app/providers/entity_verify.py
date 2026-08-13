"""Business-entity verification — a SECOND opinion on whether the petitioner
is a real, operating company, and how old it is.

WHY AN H-1B PETITION CARES
--------------------------
docs/H1B_RFE_TAXONOMY.md §4 (ability to pay / employer viability) and §8
(FDNS site-visit exposure) both list the same deterministic signals: entity age
under two years, an address that cannot be verified, a business that "cannot be
verified through publicly available information". For the Trip.com shape — a
young US subsidiary of a Chinese parent — those two sections compound. So this
seam exists to answer one narrow question with evidence: does a public record
of this company exist, and how old is it?

THE WORDING RULE, WHICH IS NOT NEGOTIABLE
------------------------------------------
A verification MISS is **unverified**, never **invalid**. A company can be real,
lawful and paying wages while a vendor's index has never heard of it: a new
Delaware entity, a recent name change, a state whose filings the vendor does not
carry. `VERDICTS` holds three values — `verified`, `unverified`, `pending` — and
there is deliberately no fourth. For the same reason `exists` is tri-state
`True | None` and is NEVER False: "not found" is an absence of evidence, and
this module does not have the standing to call a company fake.

Everything here is a second opinion. It flags for human review; it never
overwrites Ellis's own sourced answers, never gates a filing, and is never
quoted as the government's view of the petitioner.

PROVIDER CONTRACTS (researched live 2026-08-11; see CONTRACTS_AS_OF)
---------------------------------------------------------------------
Middesk (``api.middesk.com/v1``)
  * ``POST /businesses`` with HTTP Basic auth (the API key as the username, an
    empty password), body ``{name, tin: {tin}, addresses: [{address_line1,
    city, state, postal_code}]}``.
  * **Verification is asynchronous.** A 201 means the request was accepted, not
    that anything was verified. ``status`` is one of ``open, pending, in_audit,
    in_review, approved, rejected``; only a settled business is read, and an
    unsettled one comes back ``verdict: 'pending'`` with every fact unknown.
    ``recheck`` re-reads it via ``GET /businesses/{id}``.
  * ``review.tasks[]`` carries ``{key, status, sub_label, message}`` with
    statuses ``success | warning | failure | neutral``. The keys read here are
    ``address_verification``, ``tin``, ``watchlist``, and the SOS keys
    ``sos_active`` / ``sos_inactive``.
  * ``registrations[]`` carries ``{status, state, file_number,
    registration_date, entity_type}`` — the EARLIEST registration_date is the
    best available proxy for entity age.
  * Middesk publishes no employee count, so ``employee_count_band`` is None
    with a reason rather than a fabricated band.

Dun & Bradstreet Direct+ (``plus.dnb.com``)
  * ``POST /v3/token`` (HTTP Basic over ``client_id:client_secret``, body
    ``grant_type=client_credentials``) returns a bearer token; then
    ``GET /v1/match/cleanseMatch`` returns ranked ``matchCandidates[]`` with
    ``organization.{duns, primaryName, primaryAddress}``, a ``confidenceCode``
    (1-10) and ``matchQualityInformation``.
  * D&B's config is therefore a PAIR. ``ELLIS_ENTITY_VERIFY_KEY`` must hold
    ``client_id:client_secret``; anything else cannot mint a token, and this
    module says so instead of failing obscurely.
  * **Honest limit, stated rather than papered over:** entity age and employee
    count live in a Direct+ *data block* whose field schema sits behind a
    customer login that could not be read on 2026-08-11 (the published OpenAPI
    and the JSON samples both return 403 to an unauthenticated reader). Rather
    than invent ``organization.startDate``-shaped paths and risk reporting a
    confident wrong age, the D&B path answers existence and address match from
    cleanseMatch — which IS publicly documented — and reports age, size and
    watchlist screening as unknown with that reason attached. A deployment that
    needs them should extend `_dnb_*` against the real contract.
"""
from __future__ import annotations

import datetime as _dt

from .. import dates
from ..config import settings

SOURCE = "entity_verify"

# The date the provider contracts above were read from the vendors' own API
# references. A contract drifts; a stale one presented as current is what this
# date exists to expose.
CONTRACTS_AS_OF = "2026-08-11"

PROVIDERS = ("middesk", "dnb")

MIDDESK_BASE = "https://api.middesk.com/v1"
DNB_BASE = "https://plus.dnb.com"

_TIMEOUT_SECONDS = 30

# The complete verdict vocabulary. There is no 'invalid' and there will not be
# one: this module cannot see the difference between a company that does not
# exist and one a vendor has not indexed.
VERDICTS = ("verified", "unverified", "pending")

UNVERIFIED_WORDING = (
    "unverified — no public record matched. That is an absence of evidence, "
    "not evidence the employer is not real: a recently formed entity, a recent "
    "name change, or a state the provider does not index all read this way. "
    "Confirm against the employer's own formation documents and CP-575.")

# Under 2 years is the taxonomy's line for both the ability-to-pay and the
# FDNS-targeting signals (docs/H1B_RFE_TAXONOMY.md §4, §8).
YOUNG_ENTITY_YEARS = 2

TAXONOMY_DOC = "docs/H1B_RFE_TAXONOMY.md"


class UnknownEntityVerifyProvider(ValueError):
    """The configured provider is not one this module has a real client for.
    Raised rather than guessed: an invented endpoint sends an employer's FEIN
    to a host nobody vetted."""


class EntityVerifyInput(ValueError):
    """Raised BEFORE any network call when there is nothing to verify. A
    lookup with no legal name matches whatever the vendor feels like."""


# ---------------------------------------------------------------------------
# HTTP seam — every network byte this module sends passes through here, so a
# test that stubs it proves nothing escaped to the real network.
# ---------------------------------------------------------------------------
def _http_json(method: str, url: str, *, headers: dict | None = None,
               params: dict | None = None, json_body: dict | None = None,
               data: dict | None = None,
               auth: tuple | None = None) -> tuple[int, dict]:
    import httpx
    r = httpx.request(method, url, headers=headers or {}, params=params,
                      json=json_body, data=data, auth=auth,
                      timeout=_TIMEOUT_SECONDS)
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
    p = str(settings().entity_verify_provider or "").strip().lower()
    return p if p in PROVIDERS and settings().entity_verify_api_key else ""


def is_configured() -> bool:
    """True only when a KNOWN provider is named AND a key is present. Unset,
    the employer's own documents remain the only evidence — which is exactly
    what they are today."""
    return bool(configured_provider())


def capability() -> dict:
    """What this seam will and will not say, honestly, at runtime."""
    provider = configured_provider()
    return {
        "source": SOURCE,
        "configured": bool(provider),
        "provider": provider or "none",
        "contracts_as_of": CONTRACTS_AS_OF,
        "verdicts": list(VERDICTS),
        # Stated as False forever, not "not yet".
        "can_report_invalid": False,
        "is_second_opinion": True,
        "gates_filing": False,
        "reports_entity_age": provider == "middesk",
        "reports_employee_count": False,
        "reports_watchlist": provider == "middesk",
    }


def _now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat()


def _text(value) -> str:
    if value is None or isinstance(value, (dict, list, bool)):
        return ""
    return str(value).strip()


def _digits(value) -> str:
    return "".join(ch for ch in _text(value) if ch.isdigit())


def _blank_result(**over) -> dict:
    """The one skeleton every result shares, so a caller never has to ask
    whether a key is missing or unknown — unknown is always None."""
    result = {
        "available": False,
        # Tri-state on purpose: True or None. Never False. See the module
        # docstring.
        "exists": None,
        "verdict": "unverified",
        "entity_age_years": None,
        "entity_registration_date": None,
        "employee_count_band": None,
        "addresses_match": None,
        "tin_match": None,
        # int | None. 0 means "screened, nothing found"; None means "not
        # screened" — a difference a risk model must not blur.
        "watchlist_hits": None,
        "reference": "",
        "source": SOURCE,
        "provider": configured_provider() or "none",
        "fetched_at": None,
        "warnings": [],
        "reason": "",
        "note": ("a second opinion for human review; it never overrides the "
                 "employer's own formation documents and never gates a filing"),
    }
    result.update(over)
    return result


def _unavailable(reason: str, *, provider: str = "", **over) -> dict:
    return _blank_result(available=False, reason=reason,
                         provider=provider or configured_provider() or "none",
                         **over)


# ---------------------------------------------------------------------------
# Middesk
# ---------------------------------------------------------------------------
# Middesk settles a business asynchronously; only these two statuses mean the
# review finished. Everything else is 'pending' and claims nothing.
_MIDDESK_SETTLED = ("approved", "rejected")

_MIDDESK_TASK_TRUTH = {"success": True, "failure": False}


def _middesk_auth() -> tuple:
    # Basic auth, API key as the username, empty password (Middesk's own
    # documented `-u <KEY>:` form).
    return (settings().entity_verify_api_key, "")


def _middesk_business(body: dict) -> dict:
    """The business object, whether the caller handed us an API response or a
    webhook envelope. Middesk's own quickstart documents the webhook shape
    (`data.object`) while the API returns the object directly; accepting both
    beats guessing which one a deployment wired up."""
    if not isinstance(body, dict):
        return {}
    if _text(body.get("object")) == "business" or "review" in body:
        return body
    data = body.get("data")
    if isinstance(data, dict) and isinstance(data.get("object"), dict):
        return data["object"]
    return body


def _middesk_tasks(business: dict) -> dict:
    review = business.get("review")
    tasks = review.get("tasks") if isinstance(review, dict) else None
    out: dict = {}
    for task in (tasks or []):
        if isinstance(task, dict) and _text(task.get("key")):
            out[_text(task.get("key"))] = task
    return out


def _task_truth(tasks: dict, key: str):
    """success -> True, failure -> False, anything else (warning, neutral,
    absent) -> None. A 'warning' is a partial match a human reads; collapsing
    it into a boolean is how a close-match address becomes a fake one."""
    task = tasks.get(key)
    if not isinstance(task, dict):
        return None
    return _MIDDESK_TASK_TRUTH.get(_text(task.get("status")).lower())


def _middesk_registration_date(business: dict) -> str:
    """The EARLIEST Secretary-of-State registration date on file, as ISO. The
    earliest filing is the closest public proxy for how long the entity has
    existed — and it is a proxy: a company that reincorporated or redomiciled
    reads younger here than it has traded."""
    best = ""
    for reg in (business.get("registrations") or []):
        if not isinstance(reg, dict):
            continue
        raw = _text(reg.get("registration_date")) or _text(reg.get("formation_date"))
        iso = dates.normalize_any(raw[:10], kind="issue") if raw else ""
        if iso and (not best or iso < best):
            best = iso
    return best


def _age_years(registration_iso: str, today: _dt.date) -> int | None:
    if not registration_iso or not dates.is_iso(registration_iso):
        return None
    year, month, day = (int(p) for p in registration_iso.split("-"))
    years = today.year - year - ((today.month, today.day) < (month, day))
    return years if years >= 0 else None


def _middesk_watchlist_hits(business: dict, tasks: dict):
    """A hit COUNT, or None when nothing was screened. `0` is a real finding
    (screened, clean); None is the absence of one."""
    watchlist = business.get("watchlist")
    if isinstance(watchlist, dict):
        hits = watchlist.get("hits")
        if isinstance(hits, list):
            return len(hits)
    truth = _task_truth(tasks, "watchlist")
    if truth is True:
        return 0
    # A failed watchlist task means hits exist, but Middesk did not hand us a
    # countable list — so the count stays unknown rather than invented, and the
    # caller learns about it through `warnings`.
    return None


def _middesk_result(business: dict, today: _dt.date) -> dict:
    warnings: list[str] = []
    status = _text(business.get("status")).lower()
    reference = _text(business.get("id"))

    if status and status not in _MIDDESK_SETTLED:
        return _blank_result(
            available=True, verdict="pending", provider="middesk",
            reference=reference, fetched_at=_now_iso(),
            reason=("the verification is still running at the provider "
                    f"(status '{status}'); nothing is claimed until it settles"))

    tasks = _middesk_tasks(business)
    sos_active = _task_truth(tasks, "sos_active")
    name_ok = _task_truth(tasks, "name")
    # Existence is affirmative only: an active SOS filing, a matched name, or a
    # registration row. Their absence proves nothing and leaves `exists` None.
    registration_iso = _middesk_registration_date(business)
    found = bool(sos_active or name_ok or registration_iso
                 or status == "approved")

    if "sos_inactive" in tasks and not sos_active:
        warnings.append(
            "the provider found the Secretary-of-State registration INACTIVE; "
            "an inactive filing is an ability-to-pay and FDNS question, and "
            "must be confirmed against the employer's own records")

    watchlist_truth = _task_truth(tasks, "watchlist")
    if watchlist_truth is False:
        warnings.append(
            "the provider reported a watchlist finding for this business; it "
            "is a lead for human review, not a determination")

    age = _age_years(registration_iso, today)
    if age is None and found:
        warnings.append(
            "no registration date was returned, so entity age is unknown — not "
            "young and not established")

    result = _blank_result(
        available=True,
        exists=True if found else None,
        verdict="verified" if found else "unverified",
        entity_age_years=age,
        entity_registration_date=registration_iso or None,
        # Middesk publishes no headcount. Said, not guessed.
        employee_count_band=None,
        addresses_match=_task_truth(tasks, "address_verification"),
        tin_match=_task_truth(tasks, "tin"),
        watchlist_hits=_middesk_watchlist_hits(business, tasks),
        reference=reference,
        provider="middesk",
        fetched_at=_now_iso(),
        warnings=warnings,
        reason="" if found else UNVERIFIED_WORDING,
    )
    if result["employee_count_band"] is None:
        result["warnings"].append(
            "this provider publishes no employee count; employee_count_band is "
            "unknown, not small")
    return result


def _middesk_payload(legal_name: str, fein: str, address: dict) -> dict:
    payload: dict = {"name": legal_name}
    tin = _digits(fein)
    if tin:
        # The FEIN is sent to the verifier and never echoed back into the
        # result, never logged, and never placed in a URL.
        payload["tin"] = {"tin": tin}
    line1 = _text(address.get("line1") or address.get("address_line1"))
    if line1 or _text(address.get("city")):
        payload["addresses"] = [{
            "address_line1": line1,
            "address_line2": _text(address.get("line2")
                                   or address.get("address_line2")),
            "city": _text(address.get("city")),
            "state": _text(address.get("state")),
            "postal_code": _text(address.get("postal_code")
                                 or address.get("zip")),
        }]
    return payload


def _verify_middesk(*, legal_name: str, fein: str, address: dict,
                    today: _dt.date) -> dict:
    try:
        status, body = _http_json(
            "POST", f"{MIDDESK_BASE}/businesses",
            headers={"Accept": "application/json"},
            json_body=_middesk_payload(legal_name, fein, address),
            auth=_middesk_auth())
    except Exception:  # noqa: BLE001 - never surface transport internals
        return _unavailable("the entity verification provider is unreachable",
                            provider="middesk")
    if status >= 400:
        return _unavailable(
            f"the entity verification provider returned HTTP {status}",
            provider="middesk")
    return _middesk_result(_middesk_business(body), today)


def recheck(reference: str) -> dict:
    """Re-read a verification that was still running.

    Middesk settles asynchronously, so `verify_employer` can legitimately
    return `verdict: 'pending'`. This re-reads that business by the `reference`
    the pending result carried. Unconfigured or on a provider with no async
    verdict, it degrades honestly rather than inventing a settled answer."""
    ref = _text(reference)
    if not ref:
        raise EntityVerifyInput("a verification reference is required")
    provider = configured_provider()
    if provider != "middesk":
        return _unavailable(
            "re-reading a pending verification is only supported for Middesk",
            provider=provider or "none")
    try:
        status, body = _http_json("GET", f"{MIDDESK_BASE}/businesses/{ref}",
                                  headers={"Accept": "application/json"},
                                  auth=_middesk_auth())
    except Exception:  # noqa: BLE001
        return _unavailable("the entity verification provider is unreachable",
                            provider="middesk")
    if status >= 400:
        return _unavailable(
            f"the entity verification provider returned HTTP {status}",
            provider="middesk")
    return _middesk_result(_middesk_business(body),
                           _dt.datetime.now(tz=_dt.timezone.utc).date())


# ---------------------------------------------------------------------------
# Dun & Bradstreet Direct+
# ---------------------------------------------------------------------------
_DNB_BLOCK_LIMIT_NOTE = (
    "D&B reports entity age, employee count and address grading in Direct+ "
    "data blocks whose field schema is behind a customer login Ellis could not "
    "read; rather than guess a field path and risk a confident wrong age, they "
    "are reported unknown here. This provider answers existence only. Middesk "
    "supplies entity age from Secretary-of-State registrations.")


def _dnb_credentials() -> tuple[str, str]:
    """D&B authenticates a client_id/client_secret PAIR, so the single config
    value must carry both, colon-separated. A value with no colon returns
    ('', '') and the caller degrades with that stated reason."""
    raw = _text(settings().entity_verify_api_key)
    if ":" not in raw:
        return ("", "")
    client_id, _, client_secret = raw.partition(":")
    return (client_id.strip(), client_secret.strip())


def _dnb_token() -> str:
    client_id, client_secret = _dnb_credentials()
    if not (client_id and client_secret):
        return ""
    status, body = _http_json(
        "POST", f"{DNB_BASE}/v3/token",
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json"},
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret))
    if status >= 400:
        return ""
    return _text(body.get("access_token"))


def _dnb_best_candidate(body: dict) -> dict:
    candidates = [c for c in (body.get("matchCandidates") or [])
                  if isinstance(c, dict)]
    if not candidates:
        return {}
    # D&B's confidenceCode runs 1 (weak) to 10 (strong); the strongest wins.
    def confidence(candidate):
        try:
            return int(candidate.get("confidenceCode"))
        except (TypeError, ValueError):
            return 0
    return max(candidates, key=confidence)


def _verify_dnb(*, legal_name: str, address: dict) -> dict:
    client_id, client_secret = _dnb_credentials()
    if not (client_id and client_secret):
        return _unavailable(
            "D&B Direct+ authenticates a client id and secret; set "
            "ELLIS_ENTITY_VERIFY_KEY to 'client_id:client_secret'",
            provider="dnb")
    try:
        token = _dnb_token()
    except Exception:  # noqa: BLE001
        return _unavailable("the entity verification provider is unreachable",
                            provider="dnb")
    if not token:
        return _unavailable(
            "the entity verification provider refused the configured "
            "credentials", provider="dnb")

    params = {"name": legal_name, "countryISOAlpha2Code": "US"}
    line1 = _text(address.get("line1") or address.get("address_line1"))
    if line1:
        params["streetAddressLine1"] = line1
    for key, source in (("addressLocality", "city"),
                        ("addressRegion", "state"),
                        ("postalCode", "postal_code")):
        value = _text(address.get(source))
        if value:
            params[key] = value

    try:
        status, body = _http_json(
            "GET", f"{DNB_BASE}/v1/match/cleanseMatch",
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/json"},
            params=params)
    except Exception:  # noqa: BLE001
        return _unavailable("the entity verification provider is unreachable",
                            provider="dnb")
    if status >= 400:
        return _unavailable(
            f"the entity verification provider returned HTTP {status}",
            provider="dnb")

    candidate = _dnb_best_candidate(body)
    if not candidate:
        return _blank_result(
            available=True, exists=None, verdict="unverified", provider="dnb",
            fetched_at=_now_iso(), reason=UNVERIFIED_WORDING,
            warnings=[_DNB_BLOCK_LIMIT_NOTE])

    org = candidate.get("organization")
    org = org if isinstance(org, dict) else {}

    return _blank_result(
        available=True,
        exists=True,
        verdict="verified",
        entity_age_years=None,
        employee_count_band=None,
        # D&B grades a match component by component, in a schema Ellis has not
        # verified. An address match read out of an unverified grade string
        # would be a guess dressed as a check, so it stays unknown.
        addresses_match=None,
        watchlist_hits=None,
        reference=_text(org.get("duns")),
        provider="dnb",
        fetched_at=_now_iso(),
        warnings=[_DNB_BLOCK_LIMIT_NOTE],
    )


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------
def verify_employer(*, legal_name: str, fein: str = "",
                    address: dict | None = None,
                    today: _dt.date | None = None) -> dict:
    """Check the petitioning employer against a business-records provider.

    Args:
      legal_name: the employer's legal name. Required.
      fein:       the employer's FEIN. Sent to the verifier; never echoed into
                  the result, never logged, never placed in a URL.
      address:    ``{line1, line2, city, state, postal_code}`` (``address_line1``
                  / ``zip`` also accepted).

    Returns ``{available, exists, verdict, entity_age_years,
    entity_registration_date, employee_count_band, addresses_match, tin_match,
    watchlist_hits, reference, source, provider, fetched_at, warnings, reason}``.

    `verdict` is one of `VERDICTS`. A miss is **unverified**, never invalid, and
    `exists` is True or None — never False. Unknown is always None: an unknown
    entity age is not a young one, and an unscreened watchlist is not a clean
    one. Unconfigured, `available` is False with a reason and every fact stays
    None.

    Raises `EntityVerifyInput` before any network call when `legal_name` is
    empty, and `UnknownEntityVerifyProvider` when the configured provider has
    no client here.
    """
    name = _text(legal_name)
    if not name:
        raise EntityVerifyInput("the employer legal name is required")
    addr = dict(address or {})
    day = today or _dt.datetime.now(tz=_dt.timezone.utc).date()

    if not is_configured():
        named = str(settings().entity_verify_provider or "").strip().lower()
        if named and named not in PROVIDERS:
            raise UnknownEntityVerifyProvider(
                f"unknown entity verification provider: expected one of "
                f"{'|'.join(PROVIDERS)}")
        return _unavailable("no entity verification provider is configured",
                            provider="none")

    provider = configured_provider()
    if provider == "middesk":
        return _verify_middesk(legal_name=name, fein=fein, address=addr,
                               today=day)
    if provider == "dnb":
        return _verify_dnb(legal_name=name, address=addr)
    raise UnknownEntityVerifyProvider(  # pragma: no cover - gated above
        f"unknown entity verification provider: expected one of "
        f"{'|'.join(PROVIDERS)}")


# Deterministic risk signals in the taxonomy's own vocabulary. Each is
# True / False / None, and None means UNKNOWN — a risk engine that treats an
# unknown age as "not young" has invented a reassurance.
SIGNAL_CITES = {
    "entity_age_under_2_years": f"{TAXONOMY_DOC} §4 (ability to pay), §8 (FDNS)",
    "business_address_unverified": f"{TAXONOMY_DOC} §4, §8",
    "entity_not_publicly_verifiable": f"{TAXONOMY_DOC} §8 (FDNS targeting)",
    "watchlist_finding_present": f"{TAXONOMY_DOC} §8",
}


def counsel_signals(result: dict) -> dict:
    """Translate a `verify_employer` result into the RFE taxonomy's signals.

    Returns ``{signals: {name: True|False|None}, cites, advisory: True,
    source, provider, as_of}``. The counsel engine consumes these as a SECOND
    OPINION: a signal flags a risk for human review and adds curing evidence to
    a checklist. It never overwrites a sourced Ellis answer, never becomes an
    eligibility verdict, and — because `verify_employer` cannot say `invalid` —
    can never assert that an employer is not real.

    Every signal is tri-state. `None` is not `False`: an unverifiable address
    and an address nobody checked are different facts, and the second one is
    not a clean bill of health."""
    res = dict(result or {})
    available = bool(res.get("available"))
    verdict = _text(res.get("verdict")) or "unverified"
    age = res.get("entity_age_years")
    addresses_match = res.get("addresses_match")
    hits = res.get("watchlist_hits")

    if not available or verdict == "pending":
        signals = {name: None for name in SIGNAL_CITES}
    else:
        signals = {
            "entity_age_under_2_years": (None if age is None
                                         else age < YOUNG_ENTITY_YEARS),
            "business_address_unverified": (None if addresses_match is None
                                            else not addresses_match),
            # Fires only on a settled miss. It says "no public record matched",
            # which is exactly the FDNS targeting criterion — and is not, and
            # must never be rendered as, a claim that the employer is fake.
            "entity_not_publicly_verifiable": verdict == "unverified",
            "watchlist_finding_present": (None if hits is None else hits > 0),
        }

    return {
        "signals": signals,
        "cites": dict(SIGNAL_CITES),
        # Never a verdict, never a gate.
        "advisory": True,
        "overrides_ellis_answer": False,
        "source": SOURCE,
        "provider": _text(res.get("provider")) or "none",
        "as_of": _text(res.get("fetched_at")),
        "note": ("second-opinion risk signals for human review; an unverified "
                 "employer is one no public record matched, never one proven "
                 "not to exist"),
    }
