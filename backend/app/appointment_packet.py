"""The file an applicant carries to their consular appointment.

Most of the world's tourist visas cannot be filed online: a Schengen visa, a UK
visitor visa, a Chinese L visa are decided in person, and the applicant walks
into a consulate or visa centre carrying a folder. Ellis cannot submit those —
but the folder is exactly what it can assemble, and assembling it correctly is
most of the work a visa agency actually does.

This module builds that folder as ONE deliverable: a cover sheet stating where
to go and what to bring, the filled official form when Ellis has the
government's own blank, every document the applicant uploaded, and the exact
next steps for the day.

Three rules keep it honest, and each is enforced here rather than trusted to a
caller:

  1. NOTHING IS INVENTED. The competent post, the fee, and the form values come
     from verified route data or the applicant's own answers. Where Ellis does
     not know, the packet SAYS it does not know and tells the applicant how to
     confirm — a confidently wrong consulate address wastes a trip that may
     have taken months to schedule.
  2. IT NEVER CLAIMS TO BE A SUBMISSION. The packet is preparation. Its cover
     page says so plainly, so nobody arrives believing Ellis already filed.
  3. GAPS ARE LISTED, not hidden. A missing required document or unanswered
     form field appears in "before you go", because finding out at the counter
     is the expensive way to find out.
"""
from __future__ import annotations

from . import consular_forms
from .providers import pdfgen

# What the packet is FOR: routes decided in person. An electronic route that
# Ellis drives itself never produces a packet (it would imply the applicant
# must do something they do not have to do).
IN_PERSON_OUTCOMES = {
    "EMBASSY_OR_CONSULATE_APPLICATION",
    "AUTHORIZED_VISA_CENTER",
    "APPOINTMENT_REQUIRED",
    "MAIL_APPLICATION",
    "REQUIRES_MANUAL_JURISDICTION_SELECTION",
}

UNKNOWN = "not confirmed by Ellis — verify with the post before you travel"


def applies_to(route_outcome: str) -> bool:
    """Is a carry-in packet the right artifact for this route?"""
    return (route_outcome or "") in IN_PERSON_OUTCOMES


# A requirement counts as met only when the applicant EXPLICITLY submitted a
# document against it — a merely-bound or auto-detected file is not their
# confirmation, and the post will ask for anything they did not stand behind.
FULFILLED_STATUSES = {"submitted", "fulfilled", "satisfied", "accepted", "complete"}


def _is_fulfilled(item: dict) -> bool:
    if str(item.get("status") or "").lower() in FULFILLED_STATUSES:
        return True
    binding = item.get("binding") or {}
    return bool(binding.get("submitted"))


def _post_lines(jurisdiction: dict | None) -> list[str]:
    """Where the applicant must go. Never guessed: an unresolved jurisdiction
    is stated as unresolved, with what Ellis needs to resolve it."""
    j = jurisdiction or {}
    status = j.get("status") or ""
    if status in ("verified", "resolved"):
        out = [f"  Post:      {j.get('competent_post_name') or UNKNOWN}"]
        for label, key in (("Kind", "competent_post_kind"), ("Address", "address"),
                           ("City", "city"), ("Country", "country"),
                           ("Booking", "competent_post_url"), ("Phone", "phone")):
            val = str(j.get(key) or "").strip()
            if val:
                out.append(f"  {label + ':':10} {val}")
        return out
    if status == "residence_required":
        return ["  Post:      cannot be determined yet — Ellis needs your lawful",
                "             country of residence to name the competent post."]
    # Unverified, but researched: name it and give the address, and say in the
    # same breath that the applicant must confirm it. "NOT CONFIRMED" with
    # nothing beside it is not caution, it is withholding the one thing they
    # asked for — where to go.
    best = j.get("best_known") or {}
    if best.get("competent_post_name"):
        out = ["  Post:      " + str(best["competent_post_name"]),
               "             NOT YET CONFIRMED as the post for your residence —",
               "             check with them before you travel."]
        if best.get("address"):
            out.append(f"  {'Address:':10} {best['address']}")
        if best.get("evidence"):
            out.append(f"  {'Source:':10} {str(best['evidence'])[:100]}")
        return out
    return ["  Post:      NOT CONFIRMED. Several posts may serve you, or the",
            "             rule is not verified. Confirm the competent post for",
            "             your residence before booking or travelling.",
            f"             ({str(j.get('detail') or '')[:120]})" if j.get("detail") else ""]


def _fee_line(verified_fee: dict | None) -> str:
    if not verified_fee:
        return f"  Fee:       {UNKNOWN}"
    cents = verified_fee.get("amount_cents")
    cur = verified_fee.get("currency") or ""
    if cents is None:
        return f"  Fee:       {UNKNOWN}"
    return (f"  Fee:       {cents / 100:.2f} {cur}"
            f"   (source: {str(verified_fee.get('source_url') or 'official')[:60]})")


def build(*, applicant_name: str, destination: str, route: dict,
          checklist: list[dict], documents: list[dict],
          answers: dict, form_key: str | None = None,
          form_prepared: dict | None = None,
          appointment: dict | None = None) -> dict:
    """Assemble the packet's CONTENT (no bytes yet), so the caller can render
    it, inspect it, or test it. Returns the cover text plus the manifest of
    what should be in the folder and what is still missing."""
    route = route or {}
    outcome = route.get("route_outcome") or ""
    # A checklist item carries its own live status ('submitted' once the
    # applicant has explicitly fulfilled it); anything else is still owed.
    # Reading a 'satisfied' flag that does not exist told applicants every
    # document was missing while the folder contained them.
    missing_docs = [i.get("label") or i.get("id") for i in (checklist or [])
                    if i.get("required", True) and not _is_fulfilled(i)]
    have_docs = [d for d in (documents or []) if not d.get("rejected")]
    missing_fields = list((form_prepared or {}).get("missing_required") or [])

    lines: list[str] = []
    lines.append(f"APPOINTMENT PACKET — {destination}")
    lines.append("")
    lines.append("This is a PREPARATION file. Ellis has NOT submitted anything:")
    lines.append("this visa is decided in person, and only you can attend.")
    lines.append("Bring this folder, and check every item before you go.")
    lines.append("")
    lines.append(f"  Applicant: {applicant_name or UNKNOWN}")
    lines.append(f"  Route:     {outcome.replace('_', ' ').title() or UNKNOWN}")
    lines.extend(_post_lines(route.get("jurisdiction")))
    lines.append(_fee_line(route.get("verified_fee")))
    if appointment:
        lines.append(f"  APPOINTMENT: {appointment.get('when_utc') or UNKNOWN}")
        if appointment.get("location"):
            lines.append(f"             {appointment['location']}")
        if appointment.get("confirmation_no"):
            lines.append(f"             confirmation {appointment['confirmation_no']}")
    else:
        lines.append("  APPOINTMENT: not booked yet — book it in Ellis's secure "
                     "window before")
        lines.append("             you print this folder.")
    lines.append("")

    lines.append("BEFORE YOU GO")
    if missing_docs or missing_fields:
        if missing_docs:
            lines.append("  Documents still missing — the post will ask for these:")
            lines.extend(f"    [ ] {m}" for m in missing_docs)
        if missing_fields:
            lines.append("  Form answers Ellis does not have (left blank to complete):")
            lines.extend(f"    [ ] {m}" for m in missing_fields)
    else:
        lines.append("  Nothing outstanding: every required document is on file")
        lines.append("  and every required form field is filled.")
    lines.append("")

    lines.append("WHAT IS IN THIS FOLDER")
    n = 0
    if form_key and consular_forms.official_template_available(form_key):
        n += 1
        lines.append(f"  {n}. {consular_forms.FORMS[form_key]['title']} — filled, "
                     f"UNSIGNED (sign it by hand at the post)")
    elif form_key:
        n += 1
        lines.append(f"  {n}. {consular_forms.FORMS[form_key]['title']} — a "
                     f"preparation sheet (Ellis does not hold the official blank; "
                     f"copy these values onto the official form)")
    for d in have_docs:
        n += 1
        lines.append(f"  {n}. {d.get('name') or d.get('doc_type') or 'document'}")
    if n == 0:
        lines.append("  (nothing yet — upload your documents in Ellis first)")
    lines.append("")

    lines.append("ON THE DAY")
    steps = [
        "Bring your ORIGINAL passport, not a copy.",
        "Bring this folder and the printed form; sign the form only where and "
        "when the officer tells you to.",
        "Arrive early — many posts refuse late arrivals and rebooking can take "
        "weeks.",
        "Expect to give fingerprints and a photograph.",
        "Pay the fee by the method the post accepts; confirm this in advance.",
        "Keep every receipt and the tracking number you are given.",
    ]
    if form_key and consular_forms.FORMS.get(form_key, {}).get("note"):
        steps.append(consular_forms.FORMS[form_key]["note"])
    for i, s in enumerate(steps, start=1):
        for j, chunk in enumerate(consular_forms._wrap(s, 68)):
            lines.append(f"  {str(i) + '.' if j == 0 else '   '} {chunk}")
    lines.append("")
    lines.append("Ellis prepared this file from the answers and documents you")
    lines.append("gave it. It is not legal advice and not a government decision.")

    return {
        "destination": destination,
        "applicant_name": applicant_name,
        "route_outcome": outcome,
        "cover_lines": lines,
        "form_key": form_key or "",
        "official_form_included": bool(
            form_key and consular_forms.official_template_available(form_key)),
        "documents": [{"id": d.get("id"), "name": d.get("name"),
                       "doc_type": d.get("doc_type")} for d in have_docs],
        "missing_documents": missing_docs,
        "missing_form_fields": missing_fields,
        "ready": not missing_docs and not missing_fields,
    }


def render_cover_pdf(packet: dict) -> bytes:
    """The cover sheet as a PDF — the first page of the folder."""
    return pdfgen.text_pdf(
        packet.get("cover_lines") or [],
        title=f"Appointment packet — {packet.get('destination') or ''}")


def build_for_case(db, app_row) -> dict:
    """Assemble the packet for a real case from its verified route, checklist,
    documents and answers. Raises PacketNotApplicable when the route is one
    Ellis files itself — offering a carry-in folder there would tell the
    applicant to do work they do not have to do."""
    from . import checklist_intake, consular_forms as cf, models
    from .global_routes import resolver
    from .visa_snapshot import registry
    from sqlalchemy import select

    answers = app_row.answers or {}
    dest_name = app_row.destination_country or ""
    # One shared lookup (registry.iso3). The copy that lived here returned the
    # index key — 'DE', not 'DEU' — like the three others did.
    iso3 = registry.iso3(dest_name)
    try:
        route = resolver.resolve_route(
            db, nationality=answers.get("passport_nationality") or "",
            destination=iso3 or dest_name,
            issuing_country=answers.get("passport_issuing_country") or None,
            travel_document_type=answers.get("travel_document_type") or "ordinary_passport",
            residence=answers.get("lawful_country_of_residence") or None,
            residence_subdivision=answers.get("residence_subdivision") or None)
    except Exception as exc:  # noqa: BLE001 — an unresolvable route is honest
        raise PacketNotApplicable(f"route could not be resolved: {exc}") from exc
    if not applies_to(route.get("route_outcome") or ""):
        raise PacketNotApplicable(
            f"route {route.get('route_outcome')!r} is not an in-person route; "
            f"Ellis files this one itself")

    state = checklist_intake.checklist_state(db, app_row)
    checklist = state.get("items") or state.get("checklist") or []
    documents = checklist_intake.document_rows(db, app_row.id)

    form_key = cf.form_for_destination(iso3) if iso3 else None
    prepared = None
    if form_key:
        passport = (getattr(app_row, "passport_profile", None) or
                    answers.get("passport_profile"))
        merged = cf.answers_from_documents(answers, passport)
        prepared = cf.prepare(form_key, merged)
        form_answers = merged

    from . import assisted_booking
    packet = build(
        applicant_name=str(answers.get("full_name") or
                           f"{answers.get('given_names','')} {answers.get('surname','')}".strip()),
        destination=dest_name, route=route, checklist=checklist,
        documents=documents, answers=answers, form_key=form_key,
        form_prepared=prepared,
        appointment=assisted_booking.summary(db, app_row))
    packet["_form_prepared"] = prepared
    packet["_form_answers"] = locals().get("form_answers") or {}
    packet["_application_id"] = app_row.id
    packet["_route"] = route
    return packet


class PacketNotApplicable(Exception):
    """This case's route is not decided in person — no carry-in folder."""


def render_zip(db, app_row, packet: dict) -> bytes:
    """The whole folder as one download: cover sheet, the filled official form
    (or its preparation sheet), and every accepted document the applicant
    uploaded, under names that say what each file is."""
    import io
    import zipfile
    from . import consular_forms as cf, models

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("00-START-HERE.pdf", render_cover_pdf(packet))
        form_key = packet.get("form_key") or ""
        prepared = packet.get("_form_prepared")
        if form_key and prepared:
            # The applicant's OWN answers (passport OCR already merged in).
            # prepare() returns a print-ready sheet, not a value map — reading
            # a 'values' key it never had meant the official blank was filled
            # with nothing at all.
            filled = cf.fill_official_template(
                form_key, packet.get("_form_answers") or {})
            if filled:
                z.writestr(f"01-{form_key}-OFFICIAL-FORM.pdf", filled)
            else:
                z.writestr(f"01-{form_key}-preparation-sheet.pdf",
                           cf.render_pdf(prepared,
                                         applicant_name=packet.get("applicant_name") or ""))
        for i, d in enumerate(packet.get("documents") or [], start=2):
            blob = db.get(models.DocumentBlob, d.get("id")) if d.get("id") else None
            content = getattr(blob, "content", None)
            if not content:
                continue
            safe = str(d.get("name") or d.get("doc_type") or f"document-{i}")
            safe = "".join(c for c in safe if c.isalnum() or c in "._- ")[:60]
            z.writestr(f"{i:02d}-{safe}", content)
    return buf.getvalue()
