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

# The one wording of the disclaimer. Served to the screen (behind a "Read
# disclaimer" button on the final step) rather than printed on the cover.
DISCLAIMER = ("Ellis prepared this file from the answers and documents you "
              "gave it. It is not legal advice and not a government decision. "
              "This visa is decided in person by the consular officer; Ellis "
              "has not submitted anything on your behalf.")


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
          form_prepared: dict | None = None, form_answers: dict | None = None,
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
    # ONLY documents. A checklist carries three kinds of row and just one of
    # them is a file the applicant owes: 'check' is something Ellis verifies
    # (passport validity, status 'auto'), and 'form' is the consular form Ellis
    # is producing INTO this very folder. Counting all three told an applicant
    # whose six documents were every one submitted that three were still
    # missing — and one of the three was the form they were about to download
    # (2026-08-04). It also held `ready` false forever, so nothing downstream
    # could ever consider the packet complete.
    missing_docs = [i.get("label") or i.get("id") for i in (checklist or [])
                    if str(i.get("kind") or "document") == "document"
                    and i.get("required", True) and not _is_fulfilled(i)]
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
            # In the applicant's words. This printed the raw storage keys —
            # "place_of_birth", "accommodation" — on the cover of a document
            # somebody carries into a consulate (2026-08-04).
            lines.extend(f"    [ ] {consular_forms.label_for(m)}"
                         for m in missing_fields)
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
    # The not-legal-advice disclaimer is NOT printed on the cover any more —
    # the applicant reads it behind "Read disclaimer" on the final screen
    # instead (product decision 2026-08-04). It still exists, in one place,
    # as DISCLAIMER below, so screen and any future print can only ever say
    # the same words.

    form_answers = form_answers or answers or {}
    return {
        "destination": destination,
        "applicant_name": applicant_name,
        "route_outcome": outcome,
        "cover_lines": lines,
        "form_key": form_key or "",
        # Whether the applicant will actually receive the GOVERNMENT'S form or
        # a preparation sheet. This asked only whether the blank PDF exists on
        # disk, so the screen could promise a filled official form while the
        # zip held a preparation sheet.
        "official_form_included": bool(
            form_key and consular_forms.official_form_fillable(
                form_key, form_answers)),
        "documents": [{"id": d.get("id"), "name": d.get("name"),
                       "doc_type": d.get("doc_type")} for d in have_docs
                      if str(d.get("doc_type") or "") != "applicant_signature"],
        "missing_documents": missing_docs,
        "missing_form_fields": missing_fields,
        # The same gaps in the applicant's own words, each with the place its
        # answer comes from, so a screen can offer the right way to close it
        # instead of printing a storage key.
        "missing_form_fields_detail": (
            consular_forms.missing_breakdown(form_key, form_answers)
            if form_key else []),
        # Where they go and what they do there, as DATA. Both were reachable
        # only as prose inside cover_lines, so the screen could not say "go to
        # <address>" without a second full packet build.
        "post": post_block(route),
        "next_steps": list(steps),
        "disclaimer": DISCLAIMER,
        "ready": not missing_docs and not missing_fields,
    }


def post_block(route: dict | None) -> dict:
    """The competent post as structured data, carrying its own status.

    `status` is the whole point: 'verified' is a post an official page proved
    serves this residence, 'unconfirmed' is one Ellis found and could not
    prove, 'unknown' is neither. A caller that renders an unconfirmed post as
    fact is sending somebody to a building on a guess.
    """
    jur = (route or {}).get("jurisdiction") or {}
    best = jur.get("best_known") or {}
    name = jur.get("competent_post_name") or best.get("competent_post_name") or ""
    return {
        "name": name,
        "kind": jur.get("competent_post_kind") or best.get("competent_post_kind") or "",
        "address": jur.get("address") or best.get("address") or "",
        "booking_url": jur.get("competent_post_url") or "",
        "source_url": best.get("evidence") or "",
        "status": ("verified" if jur.get("status") in ("verified", "resolved")
                   else "unconfirmed" if name else "unknown"),
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
    prepared, form_answers = None, {}
    if form_key:
        # The applicant's own verified passport, read once at intake. This read
        # `app_row.passport_profile`, which is not a column, so it was always
        # None and the packet judged the form without it.
        merged = cf.derived_answers(form_key, cf.answers_from_documents(
            answers, checklist_intake.latest_passport_profile(db, app_row)))
        prepared = cf.prepare(form_key, merged)
        form_answers = merged

    from . import assisted_booking
    signature_present = bool(
        checklist_intake.applicant_signature_bytes(db, app_row.id))
    packet = build(
        applicant_name=str(answers.get("full_name") or
                           f"{answers.get('given_names','')} {answers.get('surname','')}".strip()),
        destination=dest_name, route=route, checklist=checklist,
        documents=documents, answers=answers, form_key=form_key,
        form_prepared=prepared, form_answers=form_answers,
        appointment=assisted_booking.summary(db, app_row))
    packet["signature_present"] = signature_present
    packet["_form_prepared"] = prepared
    packet["_form_answers"] = form_answers
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
    from . import checklist_intake, consular_forms as cf, models

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i, (name, content, _mime) in enumerate(_folder_parts(db, app_row, packet)):
            safe = "".join(c for c in name if c.isalnum() or c in "._- ")[:60]
            z.writestr(f"{i:02d}-{safe}", content)
    return buf.getvalue()


def _folder_parts(db, app_row, packet: dict):
    """The folder's contents in carry-in order: cover, the filled official
    form (photo placed), then every accepted document. One source, so the zip
    and the combined PDF can never disagree about what the applicant brings.
    Yields (name, bytes, mime)."""
    from . import checklist_intake, consular_forms as cf, models

    yield ("START-HERE.pdf", render_cover_pdf(packet), "application/pdf")
    form_key = packet.get("form_key") or ""
    prepared = packet.get("_form_prepared")
    if form_key and prepared:
        # The applicant's OWN answers (passport OCR already merged in).
        filled = cf.fill_official_template(
            form_key, packet.get("_form_answers") or {})
        if filled:
            # The photograph the form asks for, from the photo the applicant
            # already uploaded, and the signature they drew in Ellis's pad.
            # Silently unchanged when either is absent — an empty frame is a
            # form they can still use.
            photo = checklist_intake.applicant_photo_bytes(db, app_row.id)
            if photo:
                filled = cf.place_photo(filled, photo, form_key)
            sig = checklist_intake.applicant_signature_bytes(db, app_row.id)
            sig_box = cf.signature_box(form_key)
            if sig and sig_box:
                filled = cf.place_photo(filled, sig, form_key, box=sig_box)
            yield (f"{form_key}-OFFICIAL-FORM.pdf", filled, "application/pdf")
        else:
            yield (f"{form_key}-preparation-sheet.pdf",
                   cf.render_pdf(prepared,
                                 applicant_name=packet.get("applicant_name") or ""),
                   "application/pdf")
    for d in packet.get("documents") or []:
        # The signature is INSIDE the form, not a document anyone carries
        # separately — listing it would print somebody's bare signature as a
        # loose page in a folder that changes hands.
        if str(d.get("doc_type") or "") == "applicant_signature":
            continue
        blob = db.get(models.DocumentBlob, d.get("id")) if d.get("id") else None
        content = getattr(blob, "content", None)
        if not content:
            continue
        yield (str(d.get("name") or d.get("doc_type") or "document"),
               content, str(d.get("mime") or ""))


# A4, matching the official blanks the form pages come from — mixing letter
# and A4 pages makes a printed folder with edges that do not line up.
_A4 = (595.32, 841.92)
_MARGIN = 36.0


def render_combined_pdf(db, app_row, packet: dict) -> bytes:
    """The whole application as ONE PDF the applicant scrolls front to back:
    cover, the filled official form, then every document, each behind a page
    that names it.

    A folder of separate files asked the applicant to be their own file
    manager — open eight things, print eight things, lose one (2026-08-04).
    One document is one print job and one attachment.

    Honesty rule: a file that cannot be rendered into the combined PDF is not
    silently dropped — its name page says plainly that the original must be
    brought separately, so the folder never claims to contain what it does
    not."""
    import io
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()

    def append_pdf(data: bytes) -> bool:
        try:
            writer.append(PdfReader(io.BytesIO(data)))
            return True
        except Exception:  # noqa: BLE001 — an unreadable file gets a notice page
            return False

    def append_image(data: bytes) -> bool:
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(data))
            img.load()
            if img.mode != "RGB":
                img = img.convert("RGB")
            jbuf = io.BytesIO()
            img.save(jbuf, format="JPEG", quality=92)
            page_w, page_h = _A4
            return append_pdf(pdfgen.image_pdf(
                jbuf.getvalue(), pixels=img.size, page=_A4,
                box=(_MARGIN, _MARGIN, page_w - 2 * _MARGIN,
                     page_h - 2 * _MARGIN)))
        except Exception:  # noqa: BLE001
            return False

    parts = list(_folder_parts(db, app_row, packet))
    total_docs = max(0, len(parts) - 1)  # everything after the cover
    for idx, (name, content, mime) in enumerate(parts):
        if idx > 0:
            # A one-line divider page before each item, so a person flicking
            # through the printed stack can see where one document ends and
            # the next begins — the job filenames did in the zip.
            # Plain ASCII only: pdfgen writes latin-1 and renders an em dash
            # as '?', which on a divider page reads like something failed.
            writer.append(PdfReader(io.BytesIO(pdfgen.text_pdf(
                ["", "", f"{idx} of {total_docs}", "", name],
                title=name))))
        low_mime, low_name = mime.lower(), name.lower()
        if low_mime.startswith("image/") or low_name.endswith(
                (".jpg", ".jpeg", ".png")):
            ok = append_image(content)
        else:
            ok = append_pdf(content) or append_image(content)
        if not ok:
            writer.append(PdfReader(io.BytesIO(pdfgen.text_pdf([
                "", f"{name} could not be included in this combined file.",
                "Bring the original from Ellis's document list — it IS on",
                "file; only this preview of it failed.",
            ], title=name))))

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
