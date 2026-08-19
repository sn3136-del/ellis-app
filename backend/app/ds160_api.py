"""The DS-160 question bank, served to the Ellis UI.

Ellis asks the government's questions in ITS OWN interface, in the portal's
own words and with the portal's own answer vocabulary, so an applicant answers
once — in Trip.com — and Ellis transcribes those answers into CEAC.

The bank is the artifact recorded from an attended session on ceac.state.gov
(2026-08-18): every question, every dropdown option with the value code CEAC
actually posts, every free-text field with its maxlength, every conditional
branch, and a flag on each question saying WHO may answer it.

Two rules the payload carries, because the UI must render them differently:

* ``applicant_only`` questions are the applicant's own — sworn history, the
  security and background screens, social-media identifiers, the retrieval
  security answer, and the signature itself. Ellis never pre-fills them and
  never answers them; it shows them in the secure window for the applicant.
* everything else Ellis fills from case data, and shows the applicant what it
  filled and where each value came from.

This module serves data. It performs no network I/O and touches no case.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/ds160", tags=["ds160"])

_BANK = (Path(__file__).resolve().parent / "portal_adapters" / "generated"
         / "usa-ceac-ds160" / "1" / "questions.json")


@lru_cache(maxsize=1)
def _bank() -> dict:
    if not _BANK.exists():          # fail honestly rather than invent a form
        raise HTTPException(503, {"reason": "the DS-160 question bank is not "
                                            "installed on this deployment"})
    return json.loads(_BANK.read_text())


def _split(q: dict) -> dict:
    """One question, normalised for the UI: who answers it, what control to
    render, and the exact choices the portal accepts."""
    opts = q.get("options")
    if opts == "COUNTRY_LIST":
        opts, note = None, "country_list"      # the UI reuses its own list
    else:
        note = ""
    return {
        "field": q.get("field", ""),
        "question": q.get("question", ""),
        "kind": q.get("kind", "text"),
        # True  -> the applicant answers it personally, in the secure window
        # False -> Ellis fills it from case data and shows its work
        "applicant_only": bool(q.get("applicant_only")),
        "options": opts,
        "options_source": note,
        "options_sample": q.get("options_sample"),
        "maxlength": q.get("maxlength"),
        "na_checkbox": q.get("na_checkbox"),
        "conditional": q.get("conditional"),
        "explanation_field": q.get("explanation_field"),
        "repeating": bool(q.get("repeating")),
        "notes": q.get("notes"),
    }


@router.get("/questions")
def questions(applicant_only: bool | None = None) -> dict:
    """The whole bank, or one side of it.

    ``applicant_only=false`` gives the questions Ellis fills (the wizard it
    can complete for the traveller); ``true`` gives the ones only the
    applicant may answer. Passing nothing gives both, page by page.
    """
    bank = _bank()
    pages = []
    for page in bank.get("pages", []):
        qs = [_split(q) for q in page.get("questions", [])]
        if applicant_only is not None:
            qs = [q for q in qs if q["applicant_only"] is applicant_only]
        if not qs:
            continue
        pages.append({
            "page": page.get("page", ""),
            "applicant_only_page": bool(page.get("applicant_only_page")),
            "note": page.get("note", ""),
            "questions": qs,
        })
    total = sum(len(p["questions"]) for p in pages)
    ellis_fills = sum(1 for p in pages for q in p["questions"]
                      if not q["applicant_only"])
    return {
        "form": "DS-160",
        "form_title": "Online Nonimmigrant Visa Application",
        "portal": "ceac.state.gov",
        "artifact": bank.get("artifact", ""),
        "source": bank.get("source", ""),
        "pages": pages,
        "counts": {"total": total, "ellis_fills": ellis_fills,
                   "applicant_answers": total - ellis_fills},
        # The line the surface exists to hold, in the portal's own words.
        "signature_rule": (
            "The DS-160 requires the APPLICANT to sign it electronically, "
            "even when someone else prepared it. Ellis fills the factual "
            "screens and declares itself in the form's own preparer block; "
            "the applicant answers the sworn questions and signs."),
        "preparer_block": (
            "The DS-160 asks 'Did anyone assist you in filling out this "
            "application?' and collects the preparer's organisation and "
            "address. Ellis answers yes and names the operating company — "
            "the form's own provision for exactly this."),
    }


@router.get("/summary")
def summary() -> dict:
    """A one-screen briefing: how much of the form Ellis does, and what is
    left for the traveller. This is what the journey card shows."""
    full = questions()
    c = full["counts"]
    return {
        "form": "DS-160",
        "portal": "ceac.state.gov",
        "counts": c,
        "pages": [{"page": p["page"], "questions": len(p["questions"]),
                   "applicant_only_page": p["applicant_only_page"]}
                  for p in full["pages"]],
        "what_ellis_does": [
            "fills every factual screen from the passport read and the "
            "applicant's answers, in CEAC's own field order",
            "renders each government question in Ellis, with the portal's "
            "exact dropdown choices, so nothing is retyped on the official site",
            "declares itself in the form's preparer block",
            "captures the confirmation page as the case's evidence",
        ],
        "what_stays_the_applicants": [
            "the portal's code check",
            "the Privacy Act agreement and the retrieval security answer",
            "sworn history questions and all five Security and Background parts",
            "the photograph",
            "Sign and Submit — signing and submitting are one button",
        ],
        "after_submission": [
            "the barcoded confirmation page must be taken to the interview",
            "the MRV fee is paid on the official channel by the applicant",
            "the interview appointment is booked through the booking desk",
            "fingerprints at the post are the final personal attestation",
        ],
    }


# The official form's OWN dropdowns, surfaced to the asking UI. Keyed by the
# Ellis answer key; each entry names the bank page and a fragment of the
# question as CEAC words it. Serving the portal's recorded options keeps the
# widget honest both ways: a dropdown on ceac.state.gov is a dropdown in
# Ellis (the applicant can only choose what the form offers), and free text
# stays free text.
_ANSWER_KEY_OPTIONS = {
    "marital_status": ("personal1", "Marital Status"),
    "trip_payer": ("travel", "Person/Entity Paying"),
    "has_specific_plans": ("travel", "specific travel plans"),
    "travelling_with": ("travel_companions", "traveling with you"),
    "been_to_us_before": ("previous_us_travel", "ever been in the U.S."),
    "occupation": ("work_education_present", "Primary Occupation"),
}


def options_for_answer_key(key: str) -> list[str]:
    """The recorded option LABELS for this answer key, or [] when the field
    is free text on the official form."""
    ref = _ANSWER_KEY_OPTIONS.get(key)
    if ref is None:
        return []
    page_key, fragment = ref
    try:
        bank = _bank()
    except Exception:  # noqa: BLE001 — no bank, no options; never invented
        return []
    for page in bank.get("pages", []):
        if page.get("page") != page_key:
            continue
        for q in page.get("questions", []):
            if fragment.lower() not in (q.get("question") or "").lower():
                continue
            out = []
            for o in q.get("options") or []:
                label = o if isinstance(o, str) else (o.get("label") or "")
                if label:
                    out.append(label)
            return out
    return []
