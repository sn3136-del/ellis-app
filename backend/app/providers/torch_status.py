"""USCIS Torch case status — read-only tracking of an already-filed receipt.

Edition-neutral by construction. This provider knows a receipt number and
nothing else: no visa type, no portal family, no edition. Any route whose
government filing ends in a receipt can adopt it unchanged; the tourist
editions track their outcomes through the existing consular/portal machinery
today and would call this the same way the day a tourist route starts issuing
receipts.

USCIS's Torch developer platform (developer.uscis.gov) publishes exactly two
sanctioned programmatic surfaces: Case Status and FOIA. There is NO filing API.
Ellis therefore only ever REPORTS what the government already decided. This
module has no submit path, and myUSCIS is never scraped: the sanctioned API or
nothing.

Honest degradation is the whole point. Unconfigured, unauthenticated,
unreachable, or unrecognized, the answer is `live: False` with a marker status —
never an invented "Case Was Received". A status is a real government outcome
only when `is_real_government_status` says so: live, from the production host,
carrying the government's own words. A sandbox host answers honestly as
LIVE_SANDBOX and can never read as the real thing.

A malformed receipt number RAISES before any network call. A tracker that
guesses at garbage input is worse than one that refuses.
"""
from __future__ import annotations

import datetime as _dt
import re
import time
from urllib.parse import urlparse

from ..config import settings
from ..execution import ExecutionClass

SOURCE = "uscis_torch"

# Status markers Ellis emits itself. They are deliberately snake_case so no
# consumer can confuse one with a government status text (USCIS answers in
# prose: "Case Was Received", "Case Was Approved").
UNAVAILABLE_STATUS = "tracking_unavailable"
NOT_FOUND_STATUS = "receipt_not_found"
# Vocabulary for a caller tracking a filing step that has produced no receipt
# yet. It lives here so every edition says it the same way.
NO_RECEIPT_STATUS = "no_receipt_yet"
NO_RECEIPT_MESSAGE = "no receipt yet"

MARKER_STATUSES = (UNAVAILABLE_STATUS, NOT_FOUND_STATUS, NO_RECEIPT_STATUS)

# A USCIS receipt number is three letters and ten digits. The three letters are
# the filing office: five service centers plus IOE for electronic filings
# (USCIS ELIS). An unlisted prefix is not tracked — an office Ellis cannot name
# is an assumption, and assumptions do not reach a government API.
RECEIPT_PREFIXES = ("IOE", "WAC", "EAC", "LIN", "SRC", "MSC")
_RECEIPT_RE = re.compile(r"^([A-Z]{3})(\d{10})$")

# Hosts that serve the Torch SANDBOX. A sandbox answer is a real HTTP round trip
# to USCIS, so it is LIVE_SANDBOX — real provider, test data, never a real case.
_SANDBOX_HOST_PREFIXES = ("api-int.", "api-test.", "sandbox.")

_TIMEOUT_SECONDS = 20
_TOKEN_CACHE: dict = {}


class InvalidReceiptNumber(ValueError):
    """The receipt number is not a USCIS receipt number. Raised BEFORE any call
    so a typo is never handed to the government API and never comes back
    wearing a status."""


def normalize_receipt_number(raw: str) -> str:
    """Uppercase, stripped of the spaces and dashes humans type. Normalization
    never repairs a receipt: it only removes formatting."""
    return re.sub(r"[\s\-]", "", (raw or "")).upper()


def is_valid_receipt_number(raw: str) -> bool:
    m = _RECEIPT_RE.match(normalize_receipt_number(raw))
    return bool(m) and m.group(1) in RECEIPT_PREFIXES


def _base_url() -> str:
    return (settings().uscis_torch_base_url or "").rstrip("/")


def is_configured() -> bool:
    """True only when this deployment can actually reach Torch. Credentials are
    either a pre-issued access token or a client_credentials pair."""
    s = settings()
    if not s.uscis_torch_enabled or not _base_url():
        return False
    return bool(s.uscis_torch_access_token
                or (s.uscis_torch_client_id and s.uscis_torch_client_secret))


def execution_class() -> ExecutionClass:
    """How real a successful read from the CONFIGURED host would be. Determined
    by the host that would actually answer, never by a config claim."""
    host = (urlparse(_base_url()).hostname or "").lower()
    if any(host.startswith(p) for p in _SANDBOX_HOST_PREFIXES):
        return ExecutionClass.LIVE_SANDBOX
    return ExecutionClass.LIVE_PRODUCTION


def reset_token_cache() -> None:
    _TOKEN_CACHE.clear()


def _http_json(method: str, url: str, *, headers: dict,
               data: dict | None = None) -> tuple[int, dict]:
    """The single HTTP seam. Every network byte this provider sends passes
    through here, so a test that stubs it proves nothing escaped."""
    import httpx
    r = httpx.request(method, url, headers=headers, data=data,
                      timeout=_TIMEOUT_SECONDS)
    try:
        body = r.json()
    except Exception:  # noqa: BLE001 - a non-JSON body is an error body
        body = {}
    return r.status_code, (body if isinstance(body, dict) else {})


def _access_token() -> str | None:
    s = settings()
    if s.uscis_torch_access_token:
        return s.uscis_torch_access_token
    cid, secret = s.uscis_torch_client_id, s.uscis_torch_client_secret
    if not (cid and secret):
        return None
    now = time.time()
    if _TOKEN_CACHE.get("key") == cid and _TOKEN_CACHE.get("expires_at", 0) > now:
        return _TOKEN_CACHE.get("token")
    try:
        code, body = _http_json(
            "POST", f"{_base_url()}/oauth/accesstoken",
            headers={"content-type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials",
                  "client_id": cid, "client_secret": secret})
    except Exception:  # noqa: BLE001 - an unreachable IdP degrades honestly
        return None
    token = str(body.get("access_token") or "") if code < 400 else ""
    if not token:
        return None
    try:
        ttl = int(body.get("expires_in") or 0)
    except (TypeError, ValueError):
        ttl = 0
    # Expire the cached token early: a token that dies mid-sweep would turn a
    # tracked case into an unavailable one for no reason.
    _TOKEN_CACHE.update({"key": cid, "token": token,
                         "expires_at": now + max(ttl - 60, 60)})
    return token


def _degraded(receipt: str, status: str, note: str) -> dict:
    return {"receipt_number": receipt, "status": status, "status_date": None,
            "description": "", "form_type": "", "source": SOURCE, "live": False,
            "execution_class": str(ExecutionClass.UNSUPPORTED), "note": note}


def _iso_date(value) -> str | None:
    """USCIS dates arrive as epoch milliseconds or as an ISO timestamp. Anything
    else returns None — a date Ellis cannot read is not a date it invents."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    if text.isdigit():
        try:
            secs = int(text) / (1000 if len(text) > 10 else 1)
            return _dt.datetime.fromtimestamp(
                secs, tz=_dt.timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    try:
        return _dt.datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def get_case_status(receipt_number: str) -> dict:
    """Track one receipt at USCIS. Read-only: this is the whole provider.

    Returns {receipt_number, status, status_date, description, form_type,
    source, live, execution_class}. `status` is the government's own status text
    when live is True, and one of MARKER_STATUSES otherwise.

    Raises InvalidReceiptNumber for anything that is not a USCIS receipt
    number — checked before the provider is even asked whether it is
    configured, so a malformed value can never reach the network."""
    receipt = normalize_receipt_number(receipt_number)
    if not is_valid_receipt_number(receipt):
        raise InvalidReceiptNumber(
            "not a USCIS receipt number: expected three letters "
            f"({'/'.join(RECEIPT_PREFIXES)}) followed by ten digits")

    if not is_configured():
        return _degraded(receipt, UNAVAILABLE_STATUS,
                         "USCIS Torch API not configured")
    token = _access_token()
    if not token:
        return _degraded(receipt, UNAVAILABLE_STATUS,
                         "USCIS Torch API authentication failed")
    try:
        code, body = _http_json(
            "GET", f"{_base_url()}/case-status/{receipt}",
            headers={"authorization": f"Bearer {token}",
                     "accept": "application/json"})
    except Exception:  # noqa: BLE001 - never surface transport internals
        return _degraded(receipt, UNAVAILABLE_STATUS,
                         "USCIS Torch API unreachable")
    if code == 404:
        # A real answer, but not a case status: USCIS does not know this
        # number. Reporting it as a status would be the fabrication.
        return _degraded(receipt, NOT_FOUND_STATUS,
                         "USCIS does not recognize this receipt number")
    if code >= 400:
        return _degraded(receipt, UNAVAILABLE_STATUS,
                         f"USCIS Torch API error (HTTP {code})")

    payload = body.get("case_status") if isinstance(
        body.get("case_status"), dict) else body
    status_text = str(payload.get("current_case_status_text_en") or "").strip()
    if not status_text:
        return _degraded(receipt, UNAVAILABLE_STATUS,
                         "USCIS Torch API returned no status text")
    return {
        "receipt_number": receipt,
        # Verbatim government wording. Ellis never re-phrases an adjudication.
        "status": status_text,
        "status_date": _iso_date(payload.get("modifiedDate")
                                 or payload.get("submittedDate")),
        "description": str(payload.get("current_case_status_desc_en") or "").strip(),
        "form_type": str(payload.get("formType") or "").strip(),
        "source": SOURCE,
        "live": True,
        "execution_class": str(execution_class()),
    }


def is_real_government_status(result: dict) -> bool:
    """The display guard. True only for a live read from the PRODUCTION USCIS
    host carrying the government's own status text — so a degraded, sandbox, or
    marker result can never be presented as a real government outcome."""
    if not isinstance(result, dict) or not result.get("live"):
        return False
    if result.get("source") != SOURCE:
        return False
    if result.get("execution_class") != str(ExecutionClass.LIVE_PRODUCTION):
        return False
    status = str(result.get("status") or "")
    return bool(status) and status not in MARKER_STATUSES
