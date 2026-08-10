"""Mapping memory: what Ellis already knows about a portal FAMILY's fields.

Doctrine. Hand-building an adapter twice for the same family is the waste this
module removes. When a human corrects a field mapping, or an adapter carrying a
mapping is released, that pairing is remembered against the family and offered
back as a PROPOSAL the next time any build of that family runs. Every family in
data/reference/portal_families.json uses this identically: a tourist e-visa
form and a government work-visa form are the same problem to this table, and
nothing here reads a visa type, a destination, or a route key.

Three things this module is NOT:

1. Not authority. A remembered mapping is re-grounded against the NEW build's
   own observation and goes through the single validation chokepoint in
   specgen unchanged (unknown Ellis field, unobserved element, sensitive
   target, conditional Other-specify box, mismatched selector, and
   non-deterministic selector all still reject it). Memory chooses what to
   PROPOSE; the chokepoint alone chooses what is accepted. Nothing is filtered
   out before it — a remembered mapping onto a password box must reach the
   chokepoint and be refused there, on the record, rather than quietly dropped
   here where no rejection would be logged.
2. Not runtime. This is builder-side only. The runtime that fills a real
   applicant's form stays deterministic and reads released adapters, never
   this table.
3. Not vocabulary. A remembered mapping cannot invent an Ellis field; a row
   naming a field ELLIS_FIELDS does not contain is rejected on every build
   until a human extends the vocabulary deliberately.

THE SIGNATURE RECIPE (signature_for). A learned row must survive the portal
re-rendering itself, so it is keyed by what the field IS, never by where it sat
or what id the framework minted for it this morning:

  page_key + input type + distilled name + normalized caption

- PAGE KEY, NORMALIZED. The two observers name the same page differently: public
  recon slugs the path ("apply"), while an attended session prefixes the visit
  ORDER onto it ("attended_1_apply"). Signed raw, a mapping a human corrected
  during an attended walk would never be recalled by a later public build of the
  same portal — and two attended sessions that happened to visit the pages in a
  different order would sign the same field twice. The prefix is stripped here,
  in the module that owns the recipe, so both observers address one row; a
  caller rewriting the key on its way in would create the same split again.
- DISTILLED NAME: the control's name split on punctuation and camelCase, with
  framework and generated segments discarded — Angular Material (mat-input-7),
  React useId (:r3:), Vue, Ember, Radix, ASP.NET (ctl00_MainContent_txtSurname
  distils to "surname"), uuid runs, and the epoch-millisecond stamps Indonesia
  bakes into #spi_nationality_1785693541603. Pure-digit segments go too: they
  are indices and timestamps, never identity. A name that distils to nothing
  contributes nothing.
- NORMALIZED CAPTION: the field's own visible words — its label, or for a radio
  the question its GROUP asks (a radio is named after its answer, so "FEMALE"
  never names a field), falling back to placeholder/tooltip/title. Lowercased,
  punctuation collapsed, required/optional markers dropped, unicode kept so
  Vietnamese and Chinese labels sign as well as English ones.
- NOT INCLUDED: the selector, the element's index, its order on the page, its
  ancestors, the artifact id, the build, the org. Position is exactly what
  changes between two observations of one unchanged form.

A field with neither a distilled name nor a caption yields the empty signature
and is not learnable: two anonymous framework boxes on one page would sign
identically, and a memory that confuses two fields is worse than no memory.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

from sqlalchemy import select

from . import models as fm

# Each source's standing. A human who corrected a mapping saw the portal; a
# released adapter carried the mapping past the gates. Both are witnessed.
SOURCE_CONFIDENCE = {"human_correction": "confirmed",
                     "released_adapter": "released"}

# Framework noise in a control name. Same shapes the live extractor refuses to
# build selectors from (app/portal/live_browser.py): they are minted per render.
_FRAMEWORK_SEGMENTS = {
    "mat", "cdk", "ng", "react", "radix", "headlessui", "ember", "vue",
    "maincontent", "contentplaceholder", "aspnetform", "form1",
}
# ASP.NET ctl00, React useId :r3:, Vue v-123. The useId shape needs the digit
# right after the r, or "region" and "reason" read as framework noise.
_CTL_RE = re.compile(r"^ctl\d+$|^r\d[0-9a-z]{0,4}$|^v\d+$")
# Words that name a widget rather than a field: kept out so "mat-input-7" and
# "txtSurname" distil to what they actually ask for.
_GENERIC_SEGMENTS = {"input", "field", "control", "ctrl", "txt", "text", "tb",
                     "ddl", "lst", "sel", "cbo", "chk", "rdo", "lbl", "id"}
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}[0-9a-f\-]*", re.I)
_HEX_RE = re.compile(r"[0-9a-f]+", re.I)
# Presentation markers a form may or may not render next to the same label.
_MARKER_TOKENS = {"required", "mandatory", "optional", "compulsory"}
_CAPTION_CAP = 80
_READABLE_CAP = 150


def _distill_name(name: str) -> str:
    """The stable part of a control's name, or '' when nothing stable remains."""
    raw = _UUID_RE.sub(" ", str(name or ""))
    keep = []
    # Whole segments first (ctl00, MainContent, ContentPlaceHolder are one
    # authoring token each), then camelCase inside whatever survives.
    for segment in re.split(r"[^A-Za-z0-9]+", raw):
        if not segment or _is_noise(segment.lower()):
            continue
        for word in re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", segment).split():
            low = word.lower()
            if low and not _is_noise(low) and low not in _GENERIC_SEGMENTS:
                keep.append(low)
    return " ".join(keep)


def _is_noise(low: str) -> bool:
    if low in _FRAMEWORK_SEGMENTS or low.isdigit() or _CTL_RE.match(low):
        return True
    return bool(len(low) >= 8 and _HEX_RE.fullmatch(low)
                and any(c.isdigit() for c in low))


def _caption(field: dict) -> str:
    """The field's own words. A radio's identity is the question its GROUP
    asks, never the answer the button stands for."""
    etype = str(field.get("type") or "").lower()
    order = (("group_label", "label") if etype == "radio" else ("label", "group_label"))
    for key in order + ("placeholder", "tooltip", "title", "aria_label"):
        # A label that normalizes away (an asterisk, a stripped directive) is
        # no caption at all — keep looking rather than sign on nothing.
        caption = _normalize_caption(str(field.get(key) or ""))
        if caption:
            return caption
    return ""


def _normalize_caption(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    words = [w for w in re.split(r"[^\w]+", text, flags=re.UNICODE)
             if w and w not in _MARKER_TOKENS]
    return " ".join(words)[:_CAPTION_CAP].strip()


# An attended session names a page by the ORDER the applicant reached it
# ("attended_2_apply"); public recon names the same page "apply". The visit
# index is not identity, and neither is which observer was watching.
_ATTENDED_PAGE_RE = re.compile(r"^attended_\d+_")


def _normalize_page_key(page_key) -> str:
    """One name for one page, whoever observed it (see the recipe above)."""
    return _ATTENDED_PAGE_RE.sub("", str(page_key or ""))[:60]


def signature_for(observed_field: dict) -> str:
    """A stable identity for one observed portal control (recipe in the module
    docstring). '' when the field carries nothing stable to key on."""
    field = observed_field or {}
    name = _distill_name(field.get("name", ""))
    caption = _caption(field)
    if not name and not caption:
        return ""
    page_key = _normalize_page_key(field.get("page_key"))
    itype = re.sub(r"[^a-z\-]", "", str(field.get("type") or "").lower())[:24]
    canonical = f"v1|page={page_key}|type={itype}|name={name}|label={caption}"
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]
    readable = re.sub(r"\s+", "-", f"{page_key}:{itype}:{name}:{caption}")
    return f"v1:{readable[:_READABLE_CAP]}:{digest}"


def observed_rows(artifacts) -> list[dict]:
    """Every observed element of a recon job, each carrying the page_key and
    artifact_id a proposal must cite."""
    rows = []
    for art in artifacts or []:
        for el in (art.structure or {}).get("elements", []) or []:
            rows.append({**el, "page_key": art.page_key, "artifact_id": art.id})
    return rows


def remember(db, *, family_id: str, mapping: dict, observed_field: dict,
             source: str, actor: str = "") -> fm.LearnedFieldMapping | None:
    """Record that this portal field means this Ellis field for this family.

    Upsert on (family_id, field_signature, ellis_field): a repeat is
    corroboration, so it bumps observations rather than duplicating the row.
    Confidence only ever rises — a released adapter corroborating a mapping a
    human confirmed does not demote it back to 'released'.

    Returns None when there is nothing learnable (no family, no Ellis field, or
    a field with no stable signature). The Ellis field is NOT validated here:
    validation lives at specgen's one chokepoint, which re-checks this mapping
    on every build that consults it.
    """
    if source not in SOURCE_CONFIDENCE:
        raise ValueError(f"unknown mapping-memory source {source!r}")
    family_id = str(family_id or "").strip()
    ellis_field = str((mapping or {}).get("ellis_field") or "").strip()
    if not family_id or not ellis_field:
        return None
    field = dict(observed_field or {})
    if not field.get("page_key"):
        field["page_key"] = (mapping or {}).get("page_key", "")
    signature = signature_for(field)
    if not signature:
        return None
    confidence = SOURCE_CONFIDENCE[source]
    row = db.execute(select(fm.LearnedFieldMapping).where(
        fm.LearnedFieldMapping.family_id == family_id,
        fm.LearnedFieldMapping.field_signature == signature,
        fm.LearnedFieldMapping.ellis_field == ellis_field)).scalars().first()
    portal_field = str(field.get("name") or (mapping or {}).get("portal_field") or "")[:120]
    page_key = str(field.get("page_key") or "")[:80]
    if row is None:
        row = fm.LearnedFieldMapping(
            family_id=family_id, field_signature=signature, ellis_field=ellis_field,
            portal_field=portal_field, page_key=page_key, confidence=confidence,
            source=source, observations=1, created_by=str(actor or "")[:64])
        db.add(row)
    else:
        row.observations = int(row.observations or 0) + 1
        row.portal_field = portal_field or row.portal_field
        row.page_key = page_key or row.page_key
        if confidence == "confirmed":
            row.confidence, row.source = confidence, source
        row.created_by = row.created_by or str(actor or "")[:64]
    db.commit()
    return row


def lookup(db, family_id: str, observed_fields) -> list[dict]:
    """Proposals in specgen's proposal shape for whatever this family's memory
    recognizes among the fields THIS build observed.

    The address always comes from the current observation — memory supplies the
    meaning, the page supplies the selector — so a remembered mapping follows a
    portal that re-renders its ids. Nothing is filtered by sensitivity or type:
    every proposal is answered by the one chokepoint, which records its reason.

    At most ONE proposal per observed field, ever. Memory keeps disagreeing
    rows rather than overwriting one with the other, so a box remembered as two
    different Ellis fields is a question for a human: a confirmed row outranks a
    released one, and a tie between different fields proposes NOTHING. Two
    accepted mappings onto one box would put two different answers in one
    government form field, which is worse than leaving it to the proposers.
    """
    family_id = str(family_id or "").strip()
    fields = list(observed_fields or [])
    if not (db is not None and family_id and fields):
        return []
    by_signature: dict[str, list] = {}
    for row in db.execute(select(fm.LearnedFieldMapping).where(
            fm.LearnedFieldMapping.family_id == family_id)).scalars():
        by_signature.setdefault(row.field_signature, []).append(row)
    if not by_signature:
        return []
    out: list[dict] = []
    for field in fields:
        row = _settled(by_signature.get(signature_for(field)))
        if row is None:
            continue
        out.append({
            "ellis_field": row.ellis_field,
            "portal_field": str(field.get("name") or ""),
            "selector": field.get("selector", ""),
            "page_key": field.get("page_key", ""),
            "artifact_id": field.get("artifact_id", ""),
            "required": bool(field.get("required")),
            "label": str(field.get("label") or "")[:160],
            "learned": True,
            "learned_source": row.source,
            "learned_confidence": row.confidence,
            "field_signature": row.field_signature,
        })
    return out


def _settled(rows) -> fm.LearnedFieldMapping | None:
    """The one thing memory says about a field, or nothing when it disagrees
    with itself at the same standing."""
    rows = list(rows or [])
    if not rows:
        return None
    top = [r for r in rows if r.confidence == "confirmed"] or rows
    if len({r.ellis_field for r in top}) > 1:
        return None
    return max(top, key=lambda r: int(r.observations or 0))


def forget(db, *, family_id: str, field_signature: str = "",
           ellis_field: str = "") -> int:
    """Drop remembered mappings for one family; returns how many went.

    A family is always required: a wrong mapping is forgotten one family at a
    time, never by wiping the table.
    """
    family_id = str(family_id or "").strip()
    if not family_id:
        raise ValueError("forget() requires a family_id")
    q = select(fm.LearnedFieldMapping).where(
        fm.LearnedFieldMapping.family_id == family_id)
    if field_signature:
        q = q.where(fm.LearnedFieldMapping.field_signature == field_signature)
    if ellis_field:
        q = q.where(fm.LearnedFieldMapping.ellis_field == ellis_field)
    rows = list(db.execute(q).scalars())
    for row in rows:
        db.delete(row)
    db.commit()
    return len(rows)
