"""Agent-channel booking endpoints (app/appt_booking.py holds the rules).

Two seats, enforced with the existing auth helpers:
* the APPLICANT (case owner): create, pick a slot (or rank up to five of them
  in their own order of preference), cancel, read their view;
* the OPERATOR (admin role — Trip.com's own staff): read the queue, record
  the slots they saw, record the booked evidence, or fail honestly.

No endpoint here touches the booking site. The operator works the official
site in their own browser; these endpoints store what the humans did.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from . import appt_booking as booking
from . import appt_booking_agent as agent
from . import audit, models
from .db import get_session
from .security import Principal, get_principal, require_admin, require_owner

router = APIRouter(prefix="/appointments/booking", tags=["appointments"])


def _owned_case(db, case_id: str, principal: Principal):
    """Org-scoped read access to the case (applicant view)."""
    case = db.get(models.VisaApplication, case_id)
    if case is None:
        raise HTTPException(404, "case not found")
    require_owner(principal, case.org_id)
    return case


def _applicant_case(db, case_id: str, principal: Principal):
    """The applicant seat for MUTATIONS: owns the case, is not the operator,
    and — on a two-party H1B case — is the BENEFICIARY, never the petitioner.
    The appointment is the traveller's own personal act; the employer prepares
    around it but never requests, picks, or cancels it."""
    case = _owned_case(db, case_id, principal)
    if principal.role == "admin":
        raise HTTPException(403, "operators cannot perform the applicant's "
                                 "booking acts; sign in as the applicant")
    _require_beneficiary_on_h1b(db, case, principal)
    return case


def _require_beneficiary_on_h1b(db, case, principal: Principal) -> None:
    """If this case is part of a two-party H1B matter (a consular child case,
    or a parent that carries CaseParty rows), booking is the beneficiary's act:
    a principal bound to the petitioner party (or to no party) is refused. A
    plain single-party case has no CaseParty rows, so ownership alone defines
    the applicant and this is a no-op."""
    try:
        from .h1b import models as h1b_models
        from .h1b.api import _party_roles
    except Exception:  # pragma: no cover - h1b always ships in this edition
        return
    answers = dict(getattr(case, "answers", None) or {})
    parent_id = answers.get("h1b_parent_case_id") or case.id
    has_parties = db.execute(select(h1b_models.CaseParty.id).where(
        h1b_models.CaseParty.application_id == parent_id)).scalars().first()
    if not has_parties:
        return
    roles = _party_roles(db, parent_id, principal)
    if "beneficiary" in roles or "admin" in roles:
        return
    raise HTTPException(403, {
        "reason": "booking the appointment is the beneficiary's own act; your "
                  "account does not act for the beneficiary on this case",
        "your_roles": sorted(roles)})


def _request_row(db, request_id: str):
    row = db.get(models.AppointmentBookingRequest, request_id)
    if row is None:
        raise HTTPException(404, "booking request not found")
    return row


def _record(db, row, action: str, principal: Principal, detail: dict):
    audit.record(db, org_id=row.org_id, application_id=row.application_id,
                 action=action, detail=detail, actor=principal.user_id)


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except booking.BookingError as e:
        raise HTTPException(e.status_code, {"reason": e.reason})


# ------------------------------------------------------------- centre finding

# Kimi's centre pick is a convenience for the applicant's own choice of WHERE
# to apply. It never touches a government site and never sees slot data. A
# live UI must not hang on a model call, so the budget is tight and every
# failure degrades to available:false — the caller's own great-circle sort is
# always the fallback answer.
_NEAREST_TIMEOUT_SECONDS = 8.0

_NEAREST_SYSTEM = (
    "You help a traveller find the OFFICIAL visa appointment centre closest to "
    "their home address. You are given the applicant's address and a list of "
    "real centres, each with an id, name, city and street address. Choose the "
    "single nearest centre by real-world geography. Reply ONLY as JSON: "
    '{"centre_id": "<one id from the list>", "reason": "<one short sentence, '
    'max 18 words, naming the applicant\'s district/city and the centre\'s>"}. '
    "The centre_id MUST be one of the given ids. Never invent a centre."
)


class NearestCentreIn(BaseModel):
    address: str
    centres: list[dict] = []      # [{id, name, city, address}]


@router.post("/nearest-centre")
def nearest_centre(body: NearestCentreIn,
                   principal: Principal = Depends(get_principal)):
    """Kimi K3's pick of the nearest centre, or available:false so the caller
    uses its own distance calculation. Never raises."""
    ids = [str(c.get("id") or "") for c in (body.centres or []) if c.get("id")]
    if not body.address.strip() or not ids:
        return {"available": False, "reason": "address or centre list missing"}
    try:
        from .providers import kimi as kimi_mod
        provider = kimi_mod.get_provider()
        chat = getattr(provider, "_chat", None)
        if chat is None:
            return {"available": False,
                    "reason": "no reasoning provider configured"}
        payload = {
            "applicant_address": body.address.strip(),
            "centres": [{"id": c.get("id"), "name": c.get("name"),
                         "city": c.get("city"), "address": c.get("address")}
                        for c in body.centres],
        }
        import json as _json
        # K3 quirks, learned the hard way: temperature=0.0 is REJECTED (HTTP
        # 400) by the reasoning model, and a small max_tokens starves its
        # reasoning budget — so no temperature, and headroom for the answer.
        out = chat(_NEAREST_SYSTEM, _json.dumps(payload, ensure_ascii=False),
                   json_mode=True, timeout=_NEAREST_TIMEOUT_SECONDS,
                   max_tokens=600) or {}
    except Exception:  # noqa: BLE001 - ANY failure degrades to the caller's own math
        return {"available": False,
                "reason": "the reasoning engine did not answer"}
    picked = str(out.get("centre_id") or "").strip()
    if picked not in ids:
        # A hallucinated or empty id is refused outright — the caller's
        # deterministic sort is always the safer answer.
        return {"available": False, "reason": "no valid centre returned"}
    return {"available": True, "centre_id": picked,
            "note": str(out.get("reason") or "")[:200],
            "engine": getattr(provider, "name", "kimi")}


class CreateBookingIn(BaseModel):
    route: str                     # us_b1b2 | schengen
    posts: list[str] = []
    date_windows: list[dict] = []  # [{from, to}]
    note: str = ""


@router.post("/cases/{case_id}")
def create_booking(case_id: str, body: CreateBookingIn,
                   principal: Principal = Depends(get_principal),
                   db=Depends(get_session)):
    case = _applicant_case(db, case_id, principal)
    row = _run(booking.create_request, db, case, route=body.route,
               posts=body.posts, date_windows=body.date_windows,
               note=body.note, actor=principal.user_id)
    _record(db, row, "appt_booking_requested", principal,
            {"route": row.route, "posts": row.posts})
    db.commit()
    return booking.view(row)


@router.get("/cases/{case_id}")
def case_booking(case_id: str, principal: Principal = Depends(get_principal),
                 db=Depends(get_session)):
    """The applicant's view: the active request, else the newest one, else an
    honest {exists: False} — never an invented state."""
    _owned_case(db, case_id, principal)
    row = booking.active_request(db, case_id)
    if row is None:
        row = db.execute(
            select(models.AppointmentBookingRequest)
            .where(models.AppointmentBookingRequest.application_id == case_id)
            .order_by(models.AppointmentBookingRequest.created_at.desc())
        ).scalars().first()
    return booking.view(row)


class PickIn(BaseModel):
    index: int
    # The slot the applicant SAW, echoed back so a concurrent re-offer can
    # never bind their consent to a different slot at the same position.
    post: str = ""
    when: str = ""


@router.post("/{request_id}/pick")
def pick_slot(request_id: str, body: PickIn,
              principal: Principal = Depends(get_principal),
              db=Depends(get_session)):
    row = _request_row(db, request_id)
    _applicant_case(db, row.application_id, principal)
    _run(booking.pick_slot, db, row, index=body.index,
         expect_post=body.post, expect_when=body.when,
         actor=principal.user_id)
    _record(db, row, "appt_booking_slot_picked", principal,
            {"slot": row.picked_slot})
    db.commit()
    return booking.view(row)


class RankIn(BaseModel):
    # The offered-slot positions the applicant chose, IN THEIR OWN ORDER of
    # preference (first = rank 1). At most booking.MAX_RANKED of them.
    indices: list[int] = []
    # The slots they SAW at those positions, echoed back in the same order, so
    # a concurrent re-offer can never bind their ranking to different slots.
    slots: list[dict] = []          # [{post, when}]


@router.post("/{request_id}/rank")
def rank_slots(request_id: str, body: RankIn,
               principal: Principal = Depends(get_principal),
               db=Depends(get_session)):
    """The applicant names up to five preferred times in one session, in their
    own order. Same seat as /pick (it IS a pick — of a shortlist), and the same
    honesty: Ellis books the highest-ranked one still available and nothing
    else. See booking.rank_slots for why that is still the applicant's choice
    and not Ellis choosing."""
    row = _request_row(db, request_id)
    _applicant_case(db, row.application_id, principal)
    _run(booking.rank_slots, db, row, indices=body.indices,
         expect=body.slots, actor=principal.user_id)
    _record(db, row, "appt_booking_slots_ranked", principal,
            {"ranked": [{"rank": s.get("rank"), "post": s.get("post"),
                         "when": s.get("when")}
                        for s in (row.ranked_slots or [])]})
    db.commit()
    return booking.view(row)


@router.post("/{request_id}/cancel")
def cancel_booking(request_id: str,
                   principal: Principal = Depends(get_principal),
                   db=Depends(get_session)):
    row = _request_row(db, request_id)
    _applicant_case(db, row.application_id, principal)
    _run(booking.cancel, db, row, actor=principal.user_id)
    _record(db, row, "appt_booking_cancelled", principal, {})
    db.commit()
    return booking.view(row)


# ---------------------------------------------------------- operator (admin)

@router.get("/queue")
def booking_queue(principal: Principal = Depends(get_principal),
                  db=Depends(get_session)):
    """Active requests, oldest first — the operator's worklist. Admin-only:
    the queue spans cases and names travellers."""
    require_admin(principal)
    rows = db.execute(
        select(models.AppointmentBookingRequest)
        .where(models.AppointmentBookingRequest.status.in_(
            booking.ACTIVE_STATUSES))
        .order_by(models.AppointmentBookingRequest.created_at.asc())
    ).scalars().all()
    return {"requests": [booking.view(r) for r in rows],
            "never_automated_notice": (
                "Work each request in your OWN session on the official site. "
                "Record what you see and what you did — Ellis stores it and "
                "shows the applicant; it never opens the site itself.")}


class OfferSlotsIn(BaseModel):
    slots: list[dict]              # [{post, when, label?}]


@router.post("/{request_id}/offer-slots")
def offer_slots(request_id: str, body: OfferSlotsIn,
                principal: Principal = Depends(get_principal),
                db=Depends(get_session)):
    require_admin(principal)
    row = _request_row(db, request_id)
    _run(booking.offer_slots, db, row, slots=body.slots,
         actor=principal.user_id)
    _record(db, row, "appt_booking_slots_offered", principal,
            {"count": len(row.offered_slots)})
    db.commit()
    return booking.view(row)


class EvidenceIn(BaseModel):
    name: str
    mime: str
    content_b64: str


# Same allowlist + magic-byte contract as the case documents endpoint: a file
# whose bytes do not match its declared type is refused, never stored.
_EVIDENCE_MAGIC = {
    "application/pdf": (b"%PDF-",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
}


@router.post("/{request_id}/evidence")
def upload_evidence(request_id: str, body: EvidenceIn,
                    principal: Principal = Depends(get_principal),
                    db=Depends(get_session)):
    """The operator's confirmation document (screenshot/PDF of the official
    confirmation), stored on the request's case as doc_type
    'booking_confirmation'. This is the evidence half of `booked` — without
    it, booked cannot be recorded."""
    import base64
    import hashlib
    require_admin(principal)
    row = _request_row(db, request_id)
    # Evidence has exactly one purpose: proving the booking of the picked
    # slot. Any other state has nothing to prove — refuse, don't accumulate.
    if row.status != "slot_picked":
        raise HTTPException(409, {"reason": f"a {row.status} request takes no "
                                            "booking evidence — evidence "
                                            "belongs to a picked slot"})
    if body.mime not in _EVIDENCE_MAGIC:
        raise HTTPException(415, "unsupported evidence type — PDF, JPEG or PNG")
    try:
        content = base64.b64decode(body.content_b64 or "")
    except Exception:
        raise HTTPException(400, "invalid content_b64")
    if not content:
        raise HTTPException(422, "the confirmation document is empty")
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, "document too large")
    if not any(content.startswith(m) for m in _EVIDENCE_MAGIC[body.mime]):
        raise HTTPException(415, "file content does not match its declared type")
    # Duplicate upload (double-click, retry): return the existing record —
    # same sha256 contract as the case documents endpoint, so a retried
    # "Record booked" never mints a second confirmation artifact.
    sha = hashlib.sha256(content).hexdigest()
    from sqlalchemy import select as _select
    dup = db.execute(_select(models.StoredDocument).where(
        models.StoredDocument.application_id == row.application_id,
        models.StoredDocument.sha256 == sha,
        models.StoredDocument.doc_type == "booking_confirmation",
    )).scalars().first()
    if dup is not None:
        return {"document_id": dup.id, "name": dup.name, "duplicate": True}
    doc = models.StoredDocument(
        org_id=row.org_id, application_id=row.application_id,
        name=(body.name or "booking-confirmation")[:255], mime=body.mime,
        size_bytes=len(content), sha256=sha,
        doc_type="booking_confirmation", ocr_status="skipped")
    db.add(doc)
    db.flush()
    db.add(models.DocumentBlob(document_id=doc.id, org_id=row.org_id,
                               mime=body.mime, content=content))
    _record(db, row, "appt_booking_evidence_uploaded", principal,
            {"document_id": doc.id, "name": doc.name})
    db.commit()
    return {"document_id": doc.id, "name": doc.name}


class BookedIn(BaseModel):
    confirmation_number: str
    evidence_document_id: str
    note: str = ""
    # When the desk worked the applicant's RANKED list by hand and the first
    # choice was gone, these name the slot actually booked. The rules layer
    # refuses anything the applicant did not rank, so an operator can no more
    # book an unchosen time than the agent can.
    booked_post: str = ""
    booked_when: str = ""


@router.post("/{request_id}/booked")
def record_booked(request_id: str, body: BookedIn,
                  principal: Principal = Depends(get_principal),
                  db=Depends(get_session)):
    require_admin(principal)
    row = _request_row(db, request_id)
    _run(booking.record_booked, db, row,
         confirmation_number=body.confirmation_number,
         evidence_document_id=body.evidence_document_id,
         note=body.note, actor=principal.user_id,
         # When the desk worked the ranked list by hand and the first choice
         # was gone, name the one actually booked. The rules layer refuses
         # anything the applicant did not rank.
         booked_slot=({"post": body.booked_post, "when": body.booked_when}
                      if body.booked_when.strip() else None))
    _record(db, row, "appt_booking_booked", principal,
            {"confirmation_number": row.confirmation.get("number", ""),
             "evidence_document_id":
                 row.confirmation.get("evidence_document_id", "")})
    db.commit()
    return booking.view(row)


def _store_evidence(db, row, principal):
    """The callback the agent uses to persist a captured confirmation page as
    the booking's evidence document (doc_type booking_confirmation) — the same
    evidence `booked` requires when an operator uploads it by hand."""
    import hashlib

    def _save(name: str, mime: str, content: bytes) -> str:
        doc = models.StoredDocument(
            org_id=row.org_id, application_id=row.application_id,
            name=(name or "agent-confirmation")[:255], mime=mime,
            size_bytes=len(content or b""),
            sha256=hashlib.sha256(content or b"").hexdigest(),
            doc_type="booking_confirmation", ocr_status="skipped")
        db.add(doc)
        db.flush()
        db.add(models.DocumentBlob(document_id=doc.id, org_id=row.org_id,
                                   mime=mime, content=content or b""))
        return doc.id
    return _save


@router.post("/{request_id}/agent/read-slots")
def agent_read_slots(request_id: str,
                     principal: Principal = Depends(get_principal),
                     db=Depends(get_session)):
    """Ellis's browser agent reads the official calendar in the operator's
    authorized session and records the open slots. Operator-invoked (the agent
    acts inside the operator's own session). Fails closed honestly — the
    payload's `agent` block says why — and never fabricates a slot."""
    require_admin(principal)
    row = _request_row(db, request_id)
    try:
        outcome = agent.read_slots(db, row, operator=principal.user_id)
    except agent.AgentUnavailable as e:
        db.rollback()
        return {**booking.view(row), "agent": e.as_payload()}
    _record(db, row, "appt_booking_agent_read", principal, outcome)
    db.commit()
    return {**booking.view(row), "agent": {**outcome, "ran": True}}


@router.post("/{request_id}/agent/book")
def agent_book(request_id: str,
               principal: Principal = Depends(get_principal),
               db=Depends(get_session)):
    """Ellis's browser agent drives the picked slot to the official
    confirmation and records `booked` WITH the confirmation it captured. The
    evidence rule is unchanged: no captured document -> honest fallback to the
    operator attaching it. Never a fabricated booking."""
    require_admin(principal)
    row = _request_row(db, request_id)
    try:
        outcome = agent.book(db, row, operator=principal.user_id,
                             store_evidence=_store_evidence(db, row, principal))
    except agent.AgentUnavailable as e:
        db.rollback()
        return {**booking.view(row), "agent": e.as_payload()}
    _record(db, row, "appt_booking_agent_booked", principal,
            {"confirmation_number": row.confirmation.get("number", "")})
    db.commit()
    return {**booking.view(row), "agent": {**outcome, "ran": True}}


class FailedIn(BaseModel):
    reason: str


@router.post("/{request_id}/failed")
def record_failed(request_id: str, body: FailedIn,
                  principal: Principal = Depends(get_principal),
                  db=Depends(get_session)):
    require_admin(principal)
    row = _request_row(db, request_id)
    _run(booking.record_failed, db, row, reason=body.reason,
         actor=principal.user_id)
    _record(db, row, "appt_booking_failed", principal,
            {"reason": row.failure_reason})
    db.commit()
    return booking.view(row)
