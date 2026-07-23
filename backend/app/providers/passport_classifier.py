"""Passport biodata-page classifier + visa/stamp-page rejection (Phase 4).

Accept ONLY the passport biodata information page as the source of passport
identity. Reject (or separately classify) visa sticker pages, entry/exit stamp
pages, passport covers, observation pages, residence permits, national IDs,
blank pages, blurry/cropped pages, and any page missing the photo/MRZ or whose
MRZ cannot be validated. A visa sticker is NEVER used as the passport identity
source.

The primary signal is deterministic: the ICAO machine-readable-zone *kind*
(TD3 passport vs. MRV visa vs. TD1/TD2 ID) plus keyword evidence in the OCR
text. Google Document AI OCR text and (flag-gated) Kimi vision classification
are INPUTS to this function, never the sole authority — the model may not
silently override a deterministic MRZ signal.
"""
from __future__ import annotations

import re

# The exact wording the brief requires when a visa or stamp page is submitted
# instead of the passport biodata page.
VISA_STAMP_MESSAGE = (
    "This appears to be a visa or stamp page, not the passport biodata page. "
    "Upload the page containing your photograph, passport number, personal "
    "information and machine-readable zone."
)

_UPLOAD_BIODATA_HINT = (
    "Upload the passport biodata page — the page with your photograph, passport "
    "number, personal information and machine-readable zone."
)

# Page types. Only `passport_biodata` may be used as the passport identity source.
BIODATA = "passport_biodata"

# Keyword evidence (deterministic, case-insensitive).
_VISA_WORDS = ("multiple entry", "single entry", "duration of stay", "visa type",
               "visa category", "visa number", "number of entries", "valid for travel to")
_STAMP_WORDS = ("admitted", "departure", "port of entry", "immigration officer",
                "arrival", "entry stamp", "exit stamp", "admission number")
_OBS_WORDS = ("observations", "endorsements", "amendments", "official observations")
_RESIDENCE_WORDS = ("residence permit", "permis de séjour", "aufenthaltstitel",
                    "carte de séjour", "titre de séjour", "residence card")
_ID_WORDS = ("identity card", "national id", "national identity", "id card",
             "carte nationale", "identity number")
_COVER_WORDS = ("passport", "passeport", "reisepass", "pasaporte")


# The ICAO document code is the FIRST character of MRZ line 1: 'P' passport,
# 'V' machine-readable visa, 'I'/'A'/'C' ID card / residence permit (TD1/TD2).
_KIND_BY_CODE = {"P": "TD3_PASSPORT", "V": "MRV_VISA", "I": "TD1_TD2", "A": "TD1_TD2", "C": "TD1_TD2"}


def _mrz_lines(text: str) -> list[str]:
    """Whitespace-stripped lines that look like an MRZ line (>=28 chars, all in
    the MRZ charset, containing the '<' filler)."""
    out = []
    for raw in text.upper().splitlines():
        ln = raw.replace(" ", "")
        if len(ln) >= 28 and "<" in ln and re.fullmatch(r"[A-Z0-9<]+", ln):
            out.append(ln)
    return out


def _detect_mrz_kind(text: str) -> str | None:
    """Return the ICAO MRZ document kind. TD3_PASSPORT | MRV_VISA | TD1_TD2 | None.

    PRIMARY: read the document code from the START of MRZ line 1. This is the
    critical fix — searching a concatenation of both lines let a 'P' inside a
    visa/ID name zone (e.g. PETROV, PHAM) masquerade as a passport document code,
    so a machine-readable VISA was accepted as a passport identity source.
    """
    lines = _mrz_lines(text)
    if lines and lines[0][0] in _KIND_BY_CODE:
        return _KIND_BY_CODE[lines[0][0]]
    # FALLBACK for merged/wrapped OCR with no clear line breaks: choose the MRZ
    # whose document-code pattern starts EARLIEST. The true doc code precedes the
    # name zone, so a visa ('V…') is chosen over a later name-zone 'P'.
    u = "".join(text.upper().split())
    hits = []
    for code, kind in (("P", "TD3_PASSPORT"), ("V", "MRV_VISA"), ("[IAC]", "TD1_TD2")):
        m = re.search(code + r"[A-Z<][A-Z<]{3}[A-Z0-9<]*?<<", u)
        if m:
            hits.append((m.start(), kind))
    if hits:
        hits.sort()
        return hits[0][1]
    return None


def _has(text: str, words) -> bool:
    t = text.lower()
    return any(w in t for w in words)


def classify_page(*, text: str = "", mrz: dict | None = None, has_image: bool = False,
                  vision_hint: str = "") -> dict:
    """Classify an uploaded document page.

    Args:
      text:        OCR-recognized text (Document AI / Kimi vision / local layer).
      mrz:         result of ocr.parse_mrz(text) — a valid TD3 passport MRZ, or None.
      has_image:   whether raw image/PDF bytes were supplied.
      vision_hint: an optional non-authoritative doc-type hint (e.g. Kimi vision).

    Returns a dict:
      page_type                     the classified page type
      accepted_as_passport_identity True only for a validated biodata page
      reject                        True when the applicant should re-upload
      message                       user-facing guidance ("" when accepted)
      reasons                       deterministic evidence (non-sensitive)
    """
    text = text or ""
    reasons: list[str] = []
    kind = _detect_mrz_kind(text)
    stripped = "".join(text.split())

    def result(page_type, *, accepted=False, reject=False, message=""):
        return {"page_type": page_type, "accepted_as_passport_identity": accepted,
                "reject": reject, "message": message, "reasons": reasons,
                "mrz_kind": kind}

    # 1. A validated TD3 passport MRZ is the strongest possible signal → accept.
    if kind == "TD3_PASSPORT" and mrz and mrz.get("mrz_valid"):
        reasons.append("valid ICAO TD3 passport MRZ with correct check digits")
        return result(BIODATA, accepted=True)

    # 2. A machine-readable VISA (MRV) → visa sticker page. Never an identity source.
    if kind == "MRV_VISA":
        reasons.append("ICAO MRV (machine-readable visa) zone detected")
        return result("visa_page", reject=True, message=VISA_STAMP_MESSAGE)

    # 3. A TD1/TD2 zone → national ID or residence permit, not a passport.
    if kind == "TD1_TD2":
        reasons.append("ICAO TD1/TD2 (ID card / residence permit) zone detected")
        page = "residence_permit" if _has(text, _RESIDENCE_WORDS) else "national_id"
        return result(page, reject=True, message=(
            "This looks like a national ID or residence permit, not a passport "
            "biodata page. " + _UPLOAD_BIODATA_HINT))

    # 4. A TD3 zone was present but could NOT be validated (checksum fail / partial).
    if kind == "TD3_PASSPORT":
        reasons.append("passport MRZ found but check digits could not be validated")
        return result("passport_biodata_unverified", reject=True, message=(
            "We found a passport machine-readable zone but couldn't verify it. "
            "Upload a clearer, flat, well-lit photo of the biodata page so the "
            "machine-readable zone is fully readable."))

    # --- No MRZ found. Fall back to keyword evidence. ---
    # 5. Visa keywords (without a passport MRZ) → visa page. Guard against a real
    #    biodata page that merely mentions the word "visa" by requiring the page
    #    NOT to look like a biodata page (several biodata field labels present).
    mentions_visa = bool(re.search(r"\bvisa\b", text, re.I))
    if (_has(text, _VISA_WORDS) or mentions_visa) and not stripped_looks_like_biodata(text):
        reasons.append("visa-page keywords present, no passport MRZ")
        return result("visa_page", reject=True, message=VISA_STAMP_MESSAGE)

    # 6. Entry/exit stamp keywords → stamp page.
    if _has(text, _STAMP_WORDS):
        reasons.append("entry/exit stamp keywords present, no passport MRZ")
        return result("stamp_page", reject=True, message=VISA_STAMP_MESSAGE)

    # 7. Observation / endorsement page.
    if _has(text, _OBS_WORDS):
        reasons.append("observation/endorsement keywords present")
        return result("observation_page", reject=True, message=(
            "This looks like an observations/amendments page. " + _UPLOAD_BIODATA_HINT))

    # 8. Residence permit / national ID by keyword.
    if _has(text, _RESIDENCE_WORDS):
        reasons.append("residence-permit keywords present")
        return result("residence_permit", reject=True, message=(
            "This looks like a residence permit, not a passport biodata page. " + _UPLOAD_BIODATA_HINT))
    if _has(text, _ID_WORDS):
        reasons.append("national-ID keywords present")
        return result("national_id", reject=True, message=(
            "This looks like a national ID card, not a passport biodata page. " + _UPLOAD_BIODATA_HINT))

    # 9. Blank page (no readable text at all).
    if len(stripped) == 0:
        reasons.append("no readable text")
        page = "blank_page" if not has_image else "unreadable"
        msg = ("This page appears blank. " + _UPLOAD_BIODATA_HINT) if page == "blank_page" else (
            "We couldn't read this image. Upload a clearer, well-lit, in-focus "
            "photo of the passport biodata page.")
        return result(page, reject=True, message=msg)

    # 10. Passport cover: essentially just the word PASSPORT + country, very little else.
    if _has(text, _COVER_WORDS) and len(stripped) < 60 and not re.search(r"\d", stripped):
        reasons.append("cover keywords with almost no biodata text")
        return result("passport_cover", reject=True, message=(
            "This looks like the passport cover, not the biodata page. " + _UPLOAD_BIODATA_HINT))

    # 11. Blurry / cropped: has an image but too little recognized text and no MRZ.
    if has_image and len(stripped) < 20:
        reasons.append("image supplied but too little recognized text and no MRZ")
        return result("unreadable", reject=True, message=(
            "We couldn't read enough of this image. Make sure the whole biodata "
            "page — including the machine-readable zone — is visible, flat, and in focus."))

    # 12. Anything else is a legitimate supporting document (bank statement, photo,
    #     itinerary…). It is simply NOT a passport identity source — not an error.
    reasons.append("no passport MRZ and no passport-page markers; treated as a supporting document")
    return result(vision_hint or "other_document", accepted=False, reject=False)


def stripped_looks_like_biodata(text: str) -> bool:
    """Heuristic: does the page carry several passport-biodata field labels? Used
    to avoid misreading a biodata page that merely mentions the word 'visa'."""
    t = text.lower()
    labels = ("surname", "given name", "given names", "nationality", "date of birth",
              "place of birth", "date of expiry", "passport no", "type", "authority")
    return sum(1 for w in labels if w in t) >= 3
