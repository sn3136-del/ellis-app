"""The appointment cockpit — one surface for eligibility triage, pre-stage,
the group-request roster, and legitimate availability.

What this router is allowed to be. Ellis prepares an appointment to the very
edge of the human's act and stops there. It never searches for a slot, never
books one, never enters a card, never signs, never submits, and never solves a
CAPTCHA. Automated slot search is not a grey area: the penalty falls on the
TRAVELER — appointments cancelled and visas revoked, at scale (India, 2025) —
so the boundary is enforced in code, not in policy prose. Every payload here
therefore carries ``human_acts``: the named acts that remain a person's, so a
caller can never mistake "Ellis prepared it" for "Ellis did it".

Authorization. Org-scoped through the existing helpers: ``get_principal``
authenticates, ``require_owner`` compares the case's ``org_id`` to the caller's
principal on every case-bound read. A cross-tenant case is a 403, never a
partial payload.

Composition. The four computation modules (``appt_eligibility``,
``appt_appointments_prestage``, ``appt_group_roster``, ``appt_availability``)
are imported LAZILY inside each endpoint and bound by signature, so this router
degrades to an honest 503 — naming the module and the reason — rather than
failing at import time or fabricating a result. An unavailable dependency is
reported as unavailable; it is never smoothed over with a default.

Disclaimer. Eligibility, wait times, and appointment sequencing are all
eligibility-adjacent output, so every payload carries the H1B attorney
disclaimer from ``app/h1b/disclaimer.py`` (Ellis is not a law firm).
"""
from __future__ import annotations

import csv
import importlib
import io
import inspect

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from . import audit, models
from .db import get_session
from .h1b.disclaimer import DISCLAIMER_VERSION, disclaimer
from .security import Principal, get_principal, require_owner

router = APIRouter(prefix="/appointments", tags=["appointments"])


# ---------------------------------------------------------------------------
# The compliance block every payload carries
# ---------------------------------------------------------------------------
NEVER_AUTOMATED = (
    "CAPTCHA solving",
    "logging in to a government or ESP account",
    "entering card, bank, or payment details",
    "signing anything",
    "submitting a filing or a request",
    "searching for or booking an appointment slot",
)

COMPLIANCE = {
    "never_automated": list(NEVER_AUTOMATED),
    "why": ("Automated appointment slot search is treated as abuse by the "
            "consular booking systems, and the penalty falls on the TRAVELER: "
            "appointments cancelled and visas revoked (roughly 2,000 "
            "cancellations in India in 2025). Ellis prepares everything around "
            "the appointment; an authorized human performs the act."),
    "availability_sources": ("published Department of State wait-time data, "
                            "what a human saw in their own attended session, or "
                            "an accredited-agent channel — never a scraped "
                            "booking calendar"),
}


# ---------------------------------------------------------------------------
# Human acts — named, per surface, so a payload can never read as "done"
# ---------------------------------------------------------------------------
def _act(act: str, who: str, why: str, ellis_does: str) -> dict:
    return {"act": act, "who": who, "why": why, "ellis_does": ellis_does}


_ATTORNEY_REVIEW = _act(
    "review the case with a licensed US immigration attorney",
    "the applicant or the employer",
    "eligibility and strategy are legal judgments; Ellis is not a law firm",
    "assembles the facts and shows how each conclusion was reached")
_SIGN_IN = _act(
    "sign in to the official account (and pass any CAPTCHA)",
    "the applicant, or an authorized coordinator on their own account",
    "credentials and bot checks are personal acts; automating them is a terms "
    "violation and risks the traveler's appointment",
    "opens the official page and stops at the sign-in screen")
_SIGN_DS160 = _act(
    "complete and electronically sign the DS-160",
    "the applicant, personally",
    "the Department of State requires each applicant to complete their own "
    "form; it is signed under penalty of perjury",
    "pre-fills every answer from case data and shows the sources")
_PAY_MRV = _act(
    "pay the MRV fee through the official channel",
    "the applicant or the employer",
    "Ellis never enters card or bank details",
    "computes the exact amount due and names the official payment channel")
_BOOK_SLOT = _act(
    "search for a slot and book the appointment on the official site",
    "the applicant, or an authorized human coordinator",
    "automated slot search gets the traveler's appointment cancelled and their "
    "visa revoked",
    "hands over the official link, the prepared details, and a reminder of when "
    "to check")
_SUBMIT_GROUP_REQUEST = _act(
    "submit the group appointment request to the consulate",
    "the human group coordinator",
    "the consulate's own guidance states agents cannot schedule group "
    "appointments; a coordinator submits and the consulate approves",
    "assembles and validates the roster so the submission is one action")
_BOOK_EACH_MEMBER = _act(
    "book each approved member's slot",
    "the human group coordinator",
    "booking remains a human act even inside the sanctioned group channel",
    "keeps the roster, the confirmations, and the outstanding members in order")
_ATTEND_BIOMETRICS = _act(
    "attend the biometrics appointment and the interview in person",
    "the applicant, personally",
    "fingerprints cannot be delegated (Schengen: Art. 45 bars the intermediary; "
    "Art. 13(3) reuse only helps a repeat applicant inside 59 months)",
    "prepares the packet and confirms the date, place, and documents")
_SIGN_MANDATE = _act(
    "sign the mandate authorizing the accredited intermediary",
    "the applicant, personally",
    "an intermediary may act only on a signed authorization",
    "drafts the mandate from case data")
_CHECK_OFFICIAL = _act(
    "check current availability on the official site",
    "the applicant or an authorized coordinator",
    "only a signed-in human sees real availability; Ellis holds published wait "
    "times, which are an estimate and not a slot",
    "shows the latest published wait times with their date and links to the "
    "official tool")

HUMAN_ACTS = {
    "triage": [_ATTORNEY_REVIEW, _SIGN_DS160, _PAY_MRV, _BOOK_SLOT,
               _ATTEND_BIOMETRICS],
    "prestage": [_SIGN_IN, _SIGN_DS160, _SIGN_MANDATE, _PAY_MRV, _BOOK_SLOT,
                 _ATTEND_BIOMETRICS],
    "group_roster": [_SUBMIT_GROUP_REQUEST, _BOOK_EACH_MEMBER, _PAY_MRV,
                     _ATTEND_BIOMETRICS],
    "group_roster_export": [_SUBMIT_GROUP_REQUEST, _BOOK_EACH_MEMBER],
    "availability": [_CHECK_OFFICIAL, _BOOK_SLOT],
}

_SURFACE_NOTE = {
    "triage": ("Whether an in-person act is required at all, and what it would "
               "be. A triage result is a preparation aid, not a legal "
               "determination."),
    "prestage": ("Everything Ellis has prepared for this appointment, and what "
                 "is still missing. Nothing here has been filed, paid, signed, "
                 "or booked."),
    "group_roster": ("The roster for the consulate's official group-appointment "
                     "channel. Ellis assembles and validates it; the human "
                     "coordinator submits it."),
    "group_roster_export": ("The roster as a file for the human coordinator to "
                            "carry into the official channel. Handle it as "
                            "personal data: it identifies real travellers."),
    "availability": ("Published wait times and the official places to look. "
                     "Ellis holds no live slot inventory and never will."),
}


def _envelope(payload: dict, *, surface: str, locale: str = "en") -> dict:
    """Wrap any cockpit payload in its honesty block: the named human acts, the
    compliance boundary, and the attorney disclaimer."""
    out = dict(payload or {})
    out["surface"] = surface
    out["human_acts"] = HUMAN_ACTS.get(surface, [])
    out["compliance"] = COMPLIANCE
    out["note"] = _SURFACE_NOTE.get(surface, "")
    out["disclaimer"] = disclaimer(locale)
    out["disclaimer_version"] = DISCLAIMER_VERSION
    return out


def _degraded(surface: str, reason: str, *, module: str, locale: str = "en") -> dict:
    """The 503 body. An unavailable dependency says so, names itself, and still
    carries the human acts — the human's work does not disappear because a
    module did."""
    return _envelope({"available": False, "reason": reason, "module": module},
                     surface=surface, locale=locale)


# ---------------------------------------------------------------------------
# Lazy composition of the sibling modules (they are built in parallel)
# ---------------------------------------------------------------------------
def _load(module_name: str, *, surface: str, locale: str):
    try:
        return importlib.import_module(f".{module_name}", __package__)
    except Exception as exc:  # noqa: BLE001 - any import failure degrades honestly
        raise HTTPException(503, _degraded(
            surface,
            f"app/{module_name}.py is not available "
            f"({exc.__class__.__name__}: {exc}), so this surface cannot answer",
            module=module_name, locale=locale))


def _invoke(module, fn_name: str, offered: dict, *, surface: str,
            module_name: str, locale: str):
    """Call a sibling module's entry point, binding only the parameters it
    actually declares.

    The sibling modules are written in parallel, so this router does not assume
    their exact signatures. What it will never do is invent a result: a missing
    function, an unbindable parameter, or a raised exception all become an
    honest 503 naming the module and the reason."""
    fn = getattr(module, fn_name, None)
    if not callable(fn):
        raise HTTPException(503, _degraded(
            surface, f"app/{module_name}.py does not define {fn_name}() yet",
            module=module_name, locale=locale))
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):  # pragma: no cover - builtins only
        signature = None

    args: list = []
    kwargs: dict = {}
    missing: list[str] = []
    binding_positionals = True
    accepts_everything = False
    if signature is not None:
        for name, param in signature.parameters.items():
            if param.kind is param.VAR_KEYWORD:
                # A **kwargs sink declares that it takes whatever it is given.
                accepts_everything = True
                continue
            if param.kind is param.VAR_POSITIONAL:
                continue
            if name in offered:
                if param.kind is param.POSITIONAL_ONLY and binding_positionals:
                    args.append(offered[name])
                elif param.kind is param.POSITIONAL_ONLY:
                    continue
                else:
                    kwargs[name] = offered[name]
            elif param.default is param.empty:
                missing.append(name)
            elif param.kind is param.POSITIONAL_ONLY:
                binding_positionals = False
    if accepts_everything:
        for name, value in offered.items():
            kwargs.setdefault(name, value)
    if missing:
        raise HTTPException(503, _degraded(
            surface,
            f"app/{module_name}.py:{fn_name}() requires {missing}, which this "
            f"surface cannot supply; the modules are out of step",
            module=module_name, locale=locale))
    try:
        result = fn(*args, **kwargs)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - degrade honestly, never fabricate
        raise HTTPException(503, _degraded(
            surface,
            f"app/{module_name}.py:{fn_name}() could not answer "
            f"({exc.__class__.__name__}: {exc})",
            module=module_name, locale=locale))
    return result if isinstance(result, dict) else {"result": result}


def _load_case(db, case_id: str, principal: Principal) -> models.VisaApplication:
    """Org-scoped case load. A case in another tenant is a 403, and a missing
    case is a 404 — neither ever leaks the other tenant's data."""
    row = db.get(models.VisaApplication, case_id)
    if row is None:
        raise HTTPException(404, "case not found")
    require_owner(principal, row.org_id)
    return row


def _offered(db, row, principal: Principal, locale: str, **extra) -> dict:
    """Every value a sibling entry point could plausibly declare, under the
    names it would plausibly use. Binding is by name, so a module takes what it
    needs and ignores the rest.

    The aliases are deliberately specific. A one-letter or bare name like ``s``
    or ``row`` could collide with a parameter that means something else
    entirely, and handing a module a database session where it expected a
    string is exactly the kind of quiet wrong answer this surface must not
    produce."""
    base = {
        "db": db, "session": db,
        "case_id": row.id if row is not None else "",
        "application_id": row.id if row is not None else "",
        "case": row, "application": row, "app_row": row, "case_row": row,
        "principal": principal,
        "org_id": principal.org_id,
        "user_id": principal.user_id,
        "locale": locale,
    }
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# 1. Eligibility triage — is an in-person act even required?
# ---------------------------------------------------------------------------
@router.get("/triage/{case_id}")
def appointment_triage(case_id: str, locale: str = "en",
                       principal: Principal = Depends(get_principal),
                       db=Depends(get_session)):
    """Waiver/EVUS (US) and VIS 59-month status (Schengen): whether this
    applicant must appear in person at all, and what they must do if so."""
    row = _load_case(db, case_id, principal)
    module = _load("appt_eligibility", surface="triage", locale=locale)
    result = _invoke(module, "triage",
                     _offered(db, row, principal, locale),
                     surface="triage", module_name="appt_eligibility",
                     locale=locale)
    return _envelope({"case_id": row.id, "triage": result, "available": True},
                     surface="triage", locale=locale)


# ---------------------------------------------------------------------------
# 2. Pre-stage — everything prepared, everything still missing
# ---------------------------------------------------------------------------
@router.get("/prestage/{case_id}")
def appointment_prestage(case_id: str, locale: str = "en",
                         principal: Principal = Depends(get_principal),
                         db=Depends(get_session)):
    """Forms filled, fees computed, documents assembled, return location set —
    up to the human's act. Nothing in here has been filed, paid, or booked."""
    row = _load_case(db, case_id, principal)
    module = _load("appt_appointments_prestage", surface="prestage", locale=locale)
    result = _invoke(module, "prestage",
                     _offered(db, row, principal, locale),
                     surface="prestage",
                     module_name="appt_appointments_prestage", locale=locale)
    return _envelope({"case_id": row.id, "prestage": result, "available": True},
                     surface="prestage", locale=locale)


# ---------------------------------------------------------------------------
# 3. Group roster — the sanctioned batch channel (10+ travelling together)
# ---------------------------------------------------------------------------
class GroupRosterRequest(BaseModel):
    """A group-appointment roster request.

    ``case_ids`` are the member cases, each authorized independently against
    the caller's org. ``group_kind`` is the coordinator's own declaration
    ("tour_group", "company", "school", "family") — the consulate excludes
    families from the group channel, so an undeclared group cannot be confirmed
    eligible. ``include_passport_numbers`` defaults to FALSE: passport numbers
    are printed onto the coordinator's artifact only on an explicit request."""
    case_ids: list[str] = Field(default_factory=list)
    group_name: str = ""
    group_kind: str = ""
    expires_on: str = ""
    include_passport_numbers: bool = False
    post: str = ""
    country: str = ""
    coordinator_name: str = ""
    coordinator_email: str = ""
    travel_start_date: str = ""
    options: dict = Field(default_factory=dict)
    locale: str = "en"


def _build_roster(db, body: GroupRosterRequest, principal: Principal) -> tuple:
    """Authorize every member case, then hand the roster builder the rows.

    Authorization is per case: one member in another tenant fails the whole
    request rather than being silently dropped from a roster the coordinator
    would then submit as complete."""
    locale = body.locale or "en"
    if not body.case_ids:
        raise HTTPException(400, "case_ids is required: a roster is built from "
                                 "the member cases travelling together")
    seen: list[str] = []
    rows: list[models.VisaApplication] = []
    for case_id in body.case_ids:
        if case_id in seen:
            continue
        seen.append(case_id)
        rows.append(_load_case(db, case_id, principal))

    module = _load("appt_group_roster", surface="group_roster", locale=locale)
    offered = _offered(db, None, principal, locale,
                       case_ids=seen, member_cases=rows, cases=rows,
                       applications=rows, rows=rows, members=rows,
                       group_name=body.group_name, group_kind=body.group_kind,
                       expires_on=body.expires_on,
                       include_passport_numbers=bool(body.include_passport_numbers),
                       post=body.post, country=body.country,
                       coordinator_name=body.coordinator_name,
                       coordinator_email=body.coordinator_email,
                       travel_start_date=body.travel_start_date,
                       options=dict(body.options or {}))
    return module, _invoke(module, "build_roster", offered,
                           surface="group_roster",
                           module_name="appt_group_roster", locale=locale)


@router.post("/group-roster")
def group_roster(body: GroupRosterRequest,
                 principal: Principal = Depends(get_principal),
                 db=Depends(get_session)):
    """Assemble the consulate's group-appointment roster so the human
    coordinator's submission is a single action.

    Ellis validates completeness (each member's passport number, 10-digit
    DS-160 confirmation, and MRV receipt) and stops. It does not submit the
    request, and it does not book any member's slot."""
    locale = body.locale or "en"
    _module, roster = _build_roster(db, body, principal)
    return _envelope({"roster": roster, "available": True,
                      "case_ids": list(dict.fromkeys(body.case_ids))},
                     surface="group_roster", locale=locale)


# Columns a coordinator expects first; anything else the builder produced
# follows in a stable alphabetical order so the file never reshuffles.
_ROSTER_COLUMN_ORDER = (
    "member_index", "case_id", "surname", "given_names", "full_name",
    "date_of_birth", "nationality", "passport_number", "passport_expiry",
    "ds160_confirmation", "mrv_receipt_number", "post", "category",
    "status", "missing", "note",
)


def _roster_rows(roster) -> list[dict]:
    """The member rows inside whatever shape the builder returned."""
    if isinstance(roster, list):
        candidate = roster
    elif isinstance(roster, dict):
        candidate = None
        for key in ("members", "rows", "roster", "entries", "applicants"):
            value = roster.get(key)
            if isinstance(value, list):
                candidate = value
                break
        if candidate is None:
            candidate = []
    else:
        candidate = []
    return [dict(item) for item in candidate if isinstance(item, dict)]


def _roster_csv(rows: list[dict]) -> str:
    """Fallback renderer, used only when the roster module publishes no
    artifact of its own. A leading '=', '+', '-' or '@' is neutralized: a
    coordinator opens this in a spreadsheet, where such a cell would run as a
    formula."""
    def _cell(value):
        if value is None:
            return ""
        if isinstance(value, (list, tuple)):
            value = "; ".join(str(v) for v in value)
        text = str(value)
        return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text

    columns = [c for c in _ROSTER_COLUMN_ORDER if any(c in r for r in rows)]
    extras = sorted({k for r in rows for k in r} - set(columns))
    columns += extras
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns or ["member"],
                            extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: _cell(row.get(k)) for k in columns})
    return buffer.getvalue()


_EXPORT_HEADERS = {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
    # On the file itself, so the boundary survives being emailed onward.
    "X-Ellis-Human-Act": ("a human coordinator submits this roster; Ellis never "
                          "submits or books"),
}


def _roster_artifact(module, roster: dict, *, locale: str = "en") -> dict:
    """The roster module's own submittable artifact, when it publishes one.

    Preferred over this router's fallback renderer: the specialist module owns
    the column order the consulate expects, the spreadsheet-formula hardening,
    and the printable page that states on its face whether the roster may be
    submitted at all — including the line saying a roster must NOT be submitted.

    A module that publishes no exporter at all falls back to this router's plain
    renderer. A module that publishes one and then FAILS does not: silently
    handing the coordinator a stripped-down file, missing the very page that
    says whether the roster may be submitted, is the wrong kind of quiet. That
    is an honest 503 naming the module."""
    export = getattr(module, "export_group_request", None)
    if not callable(export):
        return {}
    try:
        artifact = export(roster)
    except Exception as exc:  # noqa: BLE001 - degrade honestly, never downgrade
        raise HTTPException(503, _degraded(
            "group_roster_export",
            f"app/appt_group_roster.py:export_group_request() could not build "
            f"the coordinator's artifact ({exc.__class__.__name__}: {exc}); "
            f"Ellis will not hand over a file that cannot say whether the "
            f"roster may be submitted",
            module="appt_group_roster", locale=locale))
    return artifact if isinstance(artifact, dict) else {}


@router.get("/group-roster/export")
def group_roster_export(
    case_id: list[str] = Query(default_factory=list),
    group_name: str = "", group_kind: str = "", expires_on: str = "",
    include_passport_numbers: bool = False,
    post: str = "", country: str = "",
    coordinator_name: str = "", coordinator_email: str = "",
    travel_start_date: str = "", format: str = "csv", locale: str = "en",
    principal: Principal = Depends(get_principal),
    db=Depends(get_session),
):
    """The roster as a file the human coordinator carries into the official
    channel.

    Stateless by construction: the roster is rebuilt from the same authorized
    member cases, so an export can never be a stale copy of someone else's
    group. The file identifies real travellers — it goes to the coordinator,
    and only counts are written to the audit trail. Passport numbers are
    printed only when ``include_passport_numbers`` is explicitly requested."""
    if format not in ("csv", "pdf", "json"):
        raise HTTPException(400, "format must be 'csv', 'pdf', or 'json'")
    body = GroupRosterRequest(
        case_ids=list(case_id), group_name=group_name, group_kind=group_kind,
        expires_on=expires_on,
        include_passport_numbers=include_passport_numbers, post=post,
        country=country, coordinator_name=coordinator_name,
        coordinator_email=coordinator_email,
        travel_start_date=travel_start_date, locale=locale)
    module, roster = _build_roster(db, body, principal)
    artifact = _roster_artifact(module, roster, locale=locale)
    rows = _roster_rows(artifact) or _roster_rows(roster)

    # Counts and flags only — never a passport number, a DS-160 confirmation,
    # or an MRV receipt in the audit trail.
    audit.record(db, org_id=principal.org_id,
                 application_id=(case_id[0] if case_id else ""),
                 action="appointment_group_roster_exported",
                 detail={"members": len(rows), "cases": len(set(case_id)),
                         "format": format,
                         "identifiers_included": bool(include_passport_numbers),
                         "submittable": bool(roster.get("submittable"))
                         if isinstance(roster, dict) else None},
                 actor=principal.user_id)

    if format == "json":
        payload = {"roster": roster, "members": rows, "available": True}
        if artifact:
            # The printable bytes are not JSON; the rest of the artifact is.
            payload["artifact"] = {k: v for k, v in artifact.items()
                                   if k != "pdf" and not isinstance(v, bytes)}
        return _envelope(payload, surface="group_roster_export", locale=locale)

    if format == "pdf":
        pdf = artifact.get("pdf")
        if not isinstance(pdf, (bytes, bytearray)):
            raise HTTPException(503, _degraded(
                "group_roster_export",
                "app/appt_group_roster.py publishes no printable roster, so "
                "there is no PDF to hand the coordinator; export csv instead",
                module="appt_group_roster", locale=locale))
        name = artifact.get("filename") or "us-group-scheduling-request.pdf"
        return Response(content=bytes(pdf), media_type="application/pdf",
                        headers={**_EXPORT_HEADERS,
                                 "Content-Disposition": f'attachment; filename="{name}"'})

    text = artifact.get("csv")
    if not isinstance(text, str):
        text = _roster_csv(rows)
    name = artifact.get("csv_filename") or "ellis-group-appointment-roster.csv"
    return Response(content=text, media_type="text/csv; charset=utf-8",
                    headers={**_EXPORT_HEADERS,
                             "Content-Disposition": f'attachment; filename="{name}"'})


# ---------------------------------------------------------------------------
# 4. Availability — published wait times + the official places to look
# ---------------------------------------------------------------------------
# appt_availability is honest with an OPERATOR about where a wait-time snapshot
# must be placed, and names the absolute directory to place it in. That is right
# for the person running Ellis and wrong for an HTTP response: the server's
# filesystem layout is not the caller's business, and it is a free map for
# anyone probing the deployment. The instructions survive; the paths do not.
# Kept narrow on purpose: these are the keys that really carry a server path.
# Any path that appears anywhere else in the payload is caught by the string
# substitution below, so breadth here would only risk blanking real data.
_SERVER_PATH_KEYS = ("directory", "snapshot_directory")


_PATH_PLACEHOLDER = "<the wait-times directory on the Ellis server>"


def _redact_server_paths(value, directory: str | None = None):
    """Recursively replace this server's filesystem locations with the
    placeholder an operator's own instructions use. Applied at the HTTP edge
    only, so the library keeps telling a local operator the truth."""
    if directory is None:
        directory = _wait_times_dir_text()
    if isinstance(value, dict):
        return {key: (_PATH_PLACEHOLDER
                      if key in _SERVER_PATH_KEYS and isinstance(item, str)
                      else _redact_server_paths(item, directory))
                for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_server_paths(item, directory) for item in value]
    if isinstance(value, str) and directory and directory in value:
        return value.replace(directory, _PATH_PLACEHOLDER)
    return value


def _wait_times_dir_text() -> str:
    """The configured snapshot directory as text, or '' if it cannot be read.
    Imported lazily and defensively: redaction must never be the thing that
    breaks the response it is protecting."""
    try:
        from .appt_availability import wait_times_dir
        return str(wait_times_dir())
    except Exception:  # noqa: BLE001 - redaction is best-effort, never fatal
        return ""


@router.get("/availability")
def appointment_availability(
    post: str = "", country: str = "", category: str = "visitor",
    route: str = "us", member_state: str = "", as_of: str = "",
    locale: str = "en",
    principal: Principal = Depends(get_principal),
):
    """What Ellis legitimately knows about availability, and where the human
    goes to find out for real.

    The only sources are the Department of State's published wait times and
    what a human saw in their own session. There is no live slot inventory
    here: reading a booking calendar by machine gets the traveler's appointment
    cancelled, so Ellis does not do it. Unconfigured means an explicit
    'unavailable', never a fabricated date."""
    module = _load("appt_availability", surface="availability", locale=locale)
    result = _invoke(module, "availability",
                     {"post": post, "country": country, "category": category,
                      "route": route, "member_state": member_state,
                      "as_of": as_of or None, "org_id": principal.org_id},
                     surface="availability", module_name="appt_availability",
                     locale=locale)
    payload = _redact_server_paths(dict(result))
    payload["available"] = bool(
        (payload.get("wait_time_data") or {}).get("available"))
    return _envelope(payload, surface="availability", locale=locale)
