"""Appointment PRE-STAGE: fill everything, so the human's remaining act is tiny.

Two routes, one shape. For a US B1/B2 applicant in China and for a Schengen
applicant going through an accredited intermediary, Ellis assembles the whole
application around the appointment — the form answer set, the fee and the
channel that fee is paid through, the supporting documents, the return-location
preference, the Art. 45 mandate — and then STOPS, at the exact point where the
law says a person must act.

What is deliberately NOT here, and can never be added:

  * No slot search and no booking. Automated scheduling gets the TRAVELLER's
    appointment and visa cancelled (roughly 2,000 cancellations, India 2025),
    so the cost of crossing this line is paid by the applicant, not by Ellis.
  * No calendar scraping. usvisascheduling.com is Cloudflare-walled and its
    inventory is personal to an account; availability may come only from
    published wait-time data, an attended session the applicant is sitting in,
    or an ESP agent channel where the operator is accredited.
  * No payment. Ellis computes the amount and names the channel. It never
    accepts, stores, forwards or types a card, bank or UnionPay credential.
  * No signature. The DS-160 is sworn under penalty of perjury and the Art. 45
    mandate is the applicant's own grant of authority; Ellis drafts, the
    applicant signs. There is no code path here that marks either one signed.

Honesty rules, enforced here rather than trusted to a caller:

  1. EVERY fee and rule is curated data carrying its own source URL and as_of
     date. A value Ellis does not have is reported as unknown, with the way to
     resolve it. There is no fallback that invents a number.
  2. NOTHING IS INVENTED INTO THE FORM. The answer set comes from
     consular_forms — the same machinery that prepares the DS-160 and the
     Schengen uniform form everywhere else in Ellis — so a fact Ellis does not
     hold appears in `missing`, worded for the applicant, never as a default.
  3. THE HUMAN ACTS ARE ALWAYS LISTED. Even a perfectly pre-staged case shows
     the irreducible acts, so nobody believes Ellis booked, paid or filed.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone

from . import consular_forms as cf, models
from .providers import pdfgen

# Curated facts below were verified against the official pages on this date.
# Anything older than a review cycle must be re-verified before it is shown
# next to a payment; the date travels with every value so a screen can say so.
AS_OF = "2026-08-11"

ROUTE_US_B1B2 = "us_b1b2"
ROUTE_SCHENGEN = "schengen"
SUPPORTED_ROUTES = (ROUTE_US_B1B2, ROUTE_SCHENGEN)

# The lines this module exists to hold. Published in the payload so the cockpit
# can state them to the applicant in the same words the code enforces.
NEVER_AUTOMATED = (
    "captcha",
    "login",
    "payment",
    "signature",
    "submission",
    "appointment_slot_search",
    "appointment_booking",
    "biometrics",
)

MANDATE_DOC_TYPE = "schengen_art45_mandate"

UNKNOWN = "unknown"


class UnsupportedRoute(Exception):
    """This route has no verified pre-stage. Saying so beats pre-staging the
    wrong country's form for somebody's appointment."""


# --------------------------------------------------------------- route keys

_SCHENGEN_ALIASES = {"schengen", "schengen_uniform", "eu", "eea", "vfs", "esp"}
_US_ALIASES = {"us", "usa", "us_b1b2", "us_b1_b2", "b1b2", "b1/b2", "b1_b2",
               "usa_b1b2", "ds160", "ds-160"}


def route_key(route=None, case=None) -> str:
    """Normalise whatever the cockpit holds — a key, a resolved-route dict, or
    nothing at all — into one of SUPPORTED_ROUTES.

    Falls back to the case's own destination so a caller that has only a case
    still gets the right pre-stage; raises rather than guessing when neither
    names a route Ellis has verified.
    """
    if isinstance(route, str):
        key = route.strip().lower().replace(" ", "_")
        if key in _US_ALIASES:
            return ROUTE_US_B1B2
        if key in _SCHENGEN_ALIASES:
            return ROUTE_SCHENGEN
        resolved = _route_from_destination(key)
        if resolved:
            return resolved
        raise UnsupportedRoute(f"no verified appointment pre-stage for {route!r}")
    if isinstance(route, dict):
        for field in ("route_key", "appointment_route", "key", "route"):
            named = route.get(field)
            if isinstance(named, str) and named.strip():
                try:
                    return route_key(named)
                except UnsupportedRoute:
                    break
        for field in ("destination", "destination_country", "member_state",
                      "destination_iso3"):
            resolved = _route_from_destination(route.get(field))
            if resolved:
                return resolved
    if case is not None:
        resolved = _route_from_destination(
            getattr(case, "destination_country", "") or "")
        if resolved:
            return resolved
    raise UnsupportedRoute(
        "Ellis has no verified appointment pre-stage for this route. It "
        "pre-stages a US B1/B2 appointment and a Schengen appointment through "
        "an accredited intermediary; anything else is not prepared here.")


def _route_from_destination(value) -> str:
    """The route a destination implies, or '' when it implies none."""
    name = str(value or "").strip()
    if not name:
        return ""
    from .visa_snapshot import registry
    iso3 = (registry.iso3(name) or name).upper()
    if iso3 in cf._SCHENGEN:
        return ROUTE_SCHENGEN
    if iso3 in ("USA", "US"):
        return ROUTE_US_B1B2
    return ""


def form_key_for(route: str, case=None) -> str:
    """The consular form this route's pre-stage fills. Always one of
    consular_forms' own keys: the form machinery is shared, never forked."""
    if route == ROUTE_US_B1B2:
        return "ds160_prep"
    iso3 = ""
    if case is not None:
        from .visa_snapshot import registry
        iso3 = registry.iso3(getattr(case, "destination_country", "") or "")
    return cf.form_for_destination(iso3) or "schengen_uniform"


# --------------------------------------------------------------- curated fees
# Amounts are the government's own, from the government's own page. None of
# this is computed, inferred or remembered from a model: it is data, reviewed
# by a person when it changes, and it carries the page it came from.

MRV_FEE_USD = 185
MRV_SOURCE = ("https://travel.state.gov/content/travel/en/us-visas/"
              "visa-information-resources/fees/fees-visa-services.html")

# China CITIC Bank is the collection channel for the MRV fee. Ellis describes
# the channel and never touches it: no card number, no UnionPay credential, no
# bank login, ever, at any tier of authorization.
MRV_PAYMENT_CHANNELS = [
    {"key": "citic_smart_counter",
     "label": "China CITIC Bank branch (Smart Counter)",
     "instructions": ("Take your passport to a China CITIC Bank branch and pay "
                      "the MRV fee at the Smart Counter. Keep the receipt: the "
                      "receipt number is what the scheduling site asks for."),
     "accepts": ["UnionPay card", "cash"]},
    {"key": "citic_atm",
     "label": "China CITIC Bank ATM",
     "instructions": ("Pay at a China CITIC Bank ATM with a UnionPay card, then "
                      "keep the printed receipt."),
     "accepts": ["UnionPay card"]},
    {"key": "citic_online",
     "label": "China CITIC Bank online banking",
     "instructions": ("Pay online through China CITIC Bank with a UnionPay "
                      "debit card and save the electronic receipt."),
     "accepts": ["UnionPay debit card"]},
]

# Regulation (EC) No 810/2009 (Visa Code). Art. 16(1) sets the visa fee,
# Art. 16(4) the child bands, Art. 17(4a) the cap on an ESP service fee.
VISA_CODE_SOURCE = "https://eur-lex.europa.eu/eli/reg/2009/810/oj"
SCHENGEN_VISA_FEE_EUR = 90
SCHENGEN_CHILD_FEE_EUR = 45

# The scheduling platform for the China posts (CGI Federal Atlas360). Cited as
# the system of record for the posts and the return-location choice. Ellis
# never reads it: the account is login-walled and bot-protected, and reading it
# with software is the thing that cancels an applicant's visa.
US_SCHEDULING_SOURCE = "https://www.usvisascheduling.com/"
US_CHINA_POSTS = ("Beijing", "Shanghai", "Guangzhou", "Shenyang", "Wuhan")

# Where the uniform Schengen answer set is actually typed. Every member state
# runs its own national system and Ellis has verified one of them, so the rest
# resolve to an explicit unknown rather than to France's address.
NATIONAL_VISA_PORTALS = {
    "FRA": {"name": "France-Visas", "url": "https://france-visas.gouv.fr/",
            "source": "https://france-visas.gouv.fr/"},
}


def national_portal(case=None) -> dict:
    """The member state's own visa system for this case, or an honest unknown.

    The Schengen application is one uniform form, so the answer set Ellis
    assembles is the same everywhere; only the site it is typed into differs.
    Naming the wrong ministry's portal would send an applicant to a system that
    cannot receive their application.
    """
    from .visa_snapshot import registry
    iso3 = (registry.iso3(getattr(case, "destination_country", "") or "")
            if case is not None else "")
    entry = NATIONAL_VISA_PORTALS.get((iso3 or "").upper())
    if entry:
        return {"status": "curated", "as_of": AS_OF, **entry,
                "note": ("The uniform Schengen answer set Ellis assembled fills "
                         "this portal's own screens.")}
    return {"status": UNKNOWN, "name": "", "url": "", "source": "",
            "as_of": AS_OF,
            "how_to_resolve": ("Ellis has not verified the national visa system "
                               "for this member state. Confirm it with the "
                               "consulate or the accredited centre before you "
                               "type anything in.")}


def us_fees(case=None) -> list[dict]:
    """The US B1/B2 fee stack: one government fee, with its channel."""
    return [{
        "key": "mrv",
        "label": "MRV visa application fee (B1/B2)",
        "status": "curated",
        "amount": float(MRV_FEE_USD),
        "currency": "USD",
        "per": "applicant",
        "payer": "applicant",
        "refundable": False,
        "source": MRV_SOURCE,
        "as_of": AS_OF,
        "paid_before": "scheduling the interview",
        "payment_channels": [dict(c) for c in MRV_PAYMENT_CHANNELS],
        "ellis_role": "instructions_only",
        "ellis_never": ("Ellis never enters card, bank or UnionPay details and "
                        "never pays this fee for you. It tells you the exact "
                        "amount and where to pay it."),
        "note": ("The MRV fee is not refundable and not transferable. Pay it "
                 "before you schedule: the scheduling site asks for the "
                 "receipt number."),
    }]


def _age_on(birth_iso: str, when: date) -> int | None:
    from . import dates
    if not birth_iso or not dates.is_iso(birth_iso):
        return None
    try:
        born = date.fromisoformat(birth_iso)
    except ValueError:
        return None
    return when.year - born.year - ((when.month, when.day) < (born.month, born.day))


def schengen_fees(case=None, *, answers: dict | None = None,
                  today: date | None = None) -> list[dict]:
    """The Schengen fee stack: the visa fee (banded by the applicant's age) and
    the external service provider's fee.

    The ESP fee is deliberately UNKNOWN. It is set per member state and per
    centre by the provider, Ellis has not verified one for this case, and a
    plausible-looking number beside a payment instruction is exactly the kind
    of invention this codebase refuses. What Ellis does know is the legal cap,
    so it states the cap and where to read the real figure.
    """
    answers = answers if answers is not None else (getattr(case, "answers", None) or {})
    today = today or date.today()
    age = _age_on(str(answers.get("birth_date") or ""), today)

    fee = {
        "key": "schengen_visa_fee",
        "label": "Schengen short-stay visa fee",
        "currency": "EUR",
        "per": "applicant",
        "payer": "applicant (the accredited intermediary may pay it for you)",
        "refundable": False,
        "source": VISA_CODE_SOURCE,
        "source_detail": "Regulation (EC) No 810/2009 (Visa Code), Art. 16",
        "as_of": AS_OF,
        "ellis_role": "instructions_only",
        "ellis_never": ("Ellis never enters card or bank details and never pays "
                        "this fee for you."),
    }
    if age is None:
        fee.update({
            "status": UNKNOWN,
            "amount": None,
            "basis": "age not confirmed",
            "standard_adult_amount": float(SCHENGEN_VISA_FEE_EUR),
            "how_to_resolve": ("Give Ellis your date of birth, or upload your "
                               "passport biodata page, and Ellis will state "
                               "your exact fee. The standard adult fee is 90 "
                               "EUR; children are charged less or nothing."),
        })
    elif age < 6:
        fee.update({"status": "curated", "amount": 0.0,
                    "basis": "child under 6 at the date of application"})
    elif age < 12:
        fee.update({"status": "curated", "amount": float(SCHENGEN_CHILD_FEE_EUR),
                    "basis": "child aged 6 to 11 at the date of application"})
    else:
        fee.update({"status": "curated", "amount": float(SCHENGEN_VISA_FEE_EUR),
                    "basis": "adult"})

    service = {
        "key": "esp_service_fee",
        "label": "Visa application centre service fee",
        "status": UNKNOWN,
        "amount": None,
        "currency": "EUR",
        "per": "applicant",
        "payer": "applicant (the accredited intermediary may pay it for you)",
        "maximum_amount": float(SCHENGEN_VISA_FEE_EUR) / 2,
        "maximum_rule": ("A service fee may not exceed half the visa fee "
                         "(Visa Code, Art. 17(4a))."),
        "source": VISA_CODE_SOURCE,
        "source_detail": "Regulation (EC) No 810/2009 (Visa Code), Art. 17",
        "as_of": AS_OF,
        "ellis_role": "instructions_only",
        "how_to_resolve": ("The exact service fee is set by the external "
                           "service provider for your member state and centre. "
                           "Read it on the provider's own fee page before you "
                           "pay. Ellis will not guess it."),
    }
    return [fee, service]


# -------------------------------------------------------------- human acts
# The acts that legally remain. These are constants, not a computed list: a
# route can never pre-stage its way out of one, so no state may remove them.

_US_HUMAN_ACTS = [
    {"key": "pay_mrv_fee",
     "label": "Pay the MRV fee at China CITIC Bank",
     "actor": "applicant",
     "why_human": ("Ellis never enters card, bank or UnionPay details and never "
                   "pays a government fee for anyone."),
     "detail": ("Pay 185 USD at a CITIC branch, ATM or online, and keep the "
                "receipt number. You need it to schedule."),
     "ellis_prepared": "the exact amount, the channel and what to keep"},
    {"key": "esign_ds160",
     "label": "Sign and submit your DS-160 at ceac.state.gov",
     "actor": "applicant",
     "why_human": ("The DS-160 is sworn under penalty of perjury and the "
                   "Security and Background questions are about you. Only you "
                   "may answer them, and only you may sign."),
     "detail": ("Sign in at ceac.state.gov/genniv, check every answer Ellis "
                "prepared, answer the Security and Background questions "
                "yourself, then e-sign and submit. Keep the 10-digit "
                "confirmation number."),
     "ellis_prepared": "every factual answer, in CEAC's own screen order"},
    {"key": "book_appointment",
     "label": "Book the interview yourself in your own account",
     "actor": "applicant or the group coordinator",
     "why_human": ("Automated slot searching and booking are forbidden, and it "
                   "is the traveller's appointment and visa that get cancelled "
                   "for it. Ellis will not search slots or book, at any tier "
                   "of authorization."),
     "detail": ("Sign in at usvisascheduling.com with your MRV receipt number "
                "and DS-160 confirmation number and choose your own slot. A "
                "group of 10 or more travelling together may instead go "
                "through the official group-appointment channel, which a human "
                "coordinator submits."),
     "ellis_prepared": ("everything the site asks for, so the visit is choose a "
                        "date and confirm")},
    {"key": "attend_appointment",
     "label": "Attend the biometrics appointment and the interview",
     "actor": "applicant",
     "why_human": ("Fingerprints and the interview can only be given by the "
                   "person applying. Nobody may attend for you."),
     "detail": ("Bring your passport, the DS-160 confirmation page and the MRV "
                "receipt. Ellis assembles the folder; you carry it."),
     "ellis_prepared": "the carry-in folder and the day's checklist"},
]

_SCHENGEN_HUMAN_ACTS = [
    {"key": "sign_mandate",
     "label": "Sign the Art. 45 mandate",
     "actor": "applicant",
     "why_human": ("The mandate is your own grant of authority to an accredited "
                   "intermediary. Ellis drafts it and can never sign it for "
                   "you."),
     "detail": ("Read the drafted mandate, then sign and date it by hand. "
                "Without your signature no intermediary may lodge anything."),
     "ellis_prepared": "the drafted mandate, unsigned"},
    {"key": "give_biometrics",
     "label": "Give your fingerprints in person",
     "actor": "applicant",
     "conditional": True,
     "why_human": ("Art. 45 bars an intermediary from collecting fingerprints "
                   "and Art. 43 bars it from the Visa Information System. This "
                   "act cannot be delegated to anyone, including Ellis."),
     "detail": ("Required in person if this is your first Schengen visa, or if "
                "your fingerprints were taken more than 59 months ago. If they "
                "are in the VIS from the last 59 months, and your member state "
                "permits lodging without appearance, this act can fall away. "
                "Ellis does not assume either way."),
     "ellis_prepared": "the appearance question, stated rather than assumed"},
    {"key": "book_appointment",
     "label": "Book the lodging appointment through the ESP agent channel",
     "actor": "accredited intermediary (a person, in their own account)",
     "why_human": ("Slot search and booking are never automated. The registered "
                   "agent channel is operated by an accredited human, and Ellis "
                   "prepares up to the confirm point only."),
     "detail": ("An accredited operator books through the ESP's registered "
                "agent portal. Ellis never scrapes a calendar and never "
                "reserves a slot."),
     "ellis_prepared": "the complete application, ready to lodge"},
    {"key": "pay_fees",
     "label": "Pay the visa fee and the centre's service fee",
     "actor": "applicant or the accredited intermediary",
     "why_human": "Ellis never enters card or bank details for anyone.",
     "detail": ("Pay at the centre or through the accredited intermediary's own "
                "account. Keep every receipt."),
     "ellis_prepared": "the fee, its legal basis and its cap"},
]


def human_acts(route: str) -> list[dict]:
    """The irreducible acts for this route. Always populated: a route with no
    human act left would mean Ellis had done something it must not do."""
    acts = _US_HUMAN_ACTS if route == ROUTE_US_B1B2 else _SCHENGEN_HUMAN_ACTS
    return [dict(a) for a in acts]


# ------------------------------------------------------- the form answer set

_HOW_TO_RESOLVE = {
    "ask": "Answer this question in Ellis.",
    "passport": ("Upload your passport biodata page in Ellis. It reads the page "
                 "once and fills this for you."),
    "trip": "Confirm your travel dates and destination in Ellis.",
    "intake": "Add this to your case details in Ellis.",
}


def _merged_answers(db, case, form_key: str) -> tuple[dict, dict, dict]:
    """(raw answers, answers after the passport read, answers after derivation).

    Three layers so every filled value can name where it came from, and so a
    value Ellis derived is never presented as something the applicant said.
    """
    from . import checklist_intake
    raw = dict(getattr(case, "answers", None) or {})
    profile = checklist_intake.latest_passport_profile(db, case) if db is not None else {}
    from_docs = cf.answers_from_documents(raw, profile)
    derived = cf.derived_answers(form_key, from_docs)
    return raw, from_docs, derived


def _provenance(key: str, raw: dict, from_docs: dict, derived: dict) -> str:
    if raw.get(key) not in (None, "", []):
        return "applicant_answer"
    if from_docs.get(key) not in (None, "", []):
        return "passport_document"
    if derived.get(key) not in (None, "", []):
        return "derived_by_ellis"
    return ""


def form_answer_set(db, case, form_key: str) -> dict:
    """The route's form, filled by consular_forms, split into what is filled
    and what is missing.

    This calls consular_forms.prepare / missing_breakdown / field_questions
    rather than re-deriving any of it. There is exactly one place in Ellis that
    knows what a DS-160 asks and how a stored answer is written onto a
    government form, and this is not it.
    """
    spec = cf.FORMS.get(form_key)
    if spec is None:
        raise UnsupportedRoute(f"unknown consular form {form_key!r}")
    raw, from_docs, derived = _merged_answers(db, case, form_key)
    prepared = cf.prepare(form_key, derived)

    filled: list[dict] = []
    for label, key, required in spec["fields"]:
        value = cf.form_value(form_key, key, derived.get(key))
        if not value:
            continue
        filled.append({
            "key": key,
            "label": cf.label_for(key),
            "form_label": label.strip(),
            "value": value,
            "required": bool(required),
            "source": _provenance(key, raw, from_docs, derived),
        })

    missing: list[dict] = []
    seen: set[str] = set()
    for gap in cf.missing_breakdown(form_key, derived):
        seen.add(gap["key"])
        missing.append({
            "key": gap["key"],
            "label": gap["label"],
            "required": True,
            "kind": "form_answer",
            "source": gap["source"],
            "how_to_resolve": _HOW_TO_RESOLVE.get(
                gap["source"], _HOW_TO_RESOLVE["intake"]),
        })
    # The optional questions the form also asks, in the applicant's words. They
    # do not block anything, and saying so is the difference between a screen
    # that nags and one that ranks.
    for question in cf.field_questions(form_key, derived, only_missing=True):
        if question["key"] in seen or question["required"]:
            continue
        seen.add(question["key"])
        missing.append({
            "key": question["key"],
            "label": question["label"],
            "required": False,
            "kind": "form_answer",
            "source": "ask",
            "how_to_resolve": question.get("help") or _HOW_TO_RESOLVE["ask"],
        })

    return {
        "form_key": form_key,
        "title": spec["title"],
        "submission": spec["submission"],
        "note": spec["note"],
        "filled_count": prepared["filled"],
        "total_fields": prepared["total"],
        "official_form_available": cf.official_form_fillable(form_key, derived),
        "filled": filled,
        "missing": missing,
        "answers": derived,
    }


# ---------------------------------------------------------------- documents

def _documents(db, case) -> tuple[list[dict], list[dict]]:
    """(documents, missing) from the case's own checklist.

    Only a document the applicant EXPLICITLY submitted counts as present. A
    file that merely sits in the case is not their confirmation, and the post
    will ask for anything they did not stand behind — so the same rule the
    appointment packet uses is the rule here, imported rather than re-typed.
    """
    from . import appointment_packet, checklist_intake
    if db is None:
        return [], []
    state = checklist_intake.checklist_state(db, case)
    docs: list[dict] = []
    missing: list[dict] = []
    for item in state.get("items") or []:
        if str(item.get("kind") or "document") != "document":
            continue          # 'check' is Ellis's to verify, 'form' is this build
        present = appointment_packet._is_fulfilled(item)
        binding = item.get("binding") or {}
        label = item.get("label") or item.get("id") or "document"
        docs.append({
            "item_id": item.get("id"),
            "label": label,
            "required": bool(item.get("required", True)),
            "present": present,
            "status": item.get("status") or "",
            "document_id": binding.get("document_id") or "",
            "source": "applicant_upload",
        })
        if not present and item.get("required", True) and not item.get("waived"):
            missing.append({
                "key": f"document:{item.get('id')}",
                "label": label,
                "required": True,
                "kind": "document",
                "source": "upload",
                "how_to_resolve": ("Upload this document in Ellis and submit it "
                                   "against this checklist item."),
            })
    return docs, missing


# --------------------------------------------------- document-return choice

def document_return(db, case) -> dict:
    """The US document-return preference, PROPOSED rather than decided.

    Ellis pre-selects from what the applicant already told it and marks the
    choice unconfirmed. It cannot read the real list of return locations: that
    list lives inside the applicant's own scheduling account, behind a login
    and bot protection, and reading it with software is precisely the act that
    gets an applicant's visa cancelled. So the proposal is a head start, and
    the human confirms it against the list their own account shows.
    """
    saved = ""
    if db is not None:
        from sqlalchemy import select
        row = db.execute(select(models.AppointmentPreference).where(
            models.AppointmentPreference.application_id == case.id)).scalars().first()
        prefs = (row.prefs or {}) if row is not None else {}
        # Only the document-return preference. `preferredLocation` answers a
        # different question (where to be interviewed), and reading it here
        # would present one choice as if it were another.
        saved = str(prefs.get("documentReturnLocation") or "").strip()
    answers = getattr(case, "answers", None) or {}
    stated = str(answers.get("document_return_location") or "").strip()

    base = {
        "options": list(US_CHINA_POSTS),
        "source": US_SCHEDULING_SOURCE,
        "as_of": AS_OF,
        "confirmed": False,
        "ellis_never": ("Ellis does not read your scheduling account. The exact "
                        "pickup or courier options are the ones your own "
                        "account shows you."),
    }
    chosen = stated or saved
    if chosen:
        return {**base, "status": "proposed", "location": chosen,
                "basis": ("your saved preference" if not stated
                          else "the location you told Ellis"),
                "how_to_resolve": ("Confirm this against the return locations "
                                   "your scheduling account lists.")}
    city = str(answers.get("address_city") or "").strip()
    match = next((p for p in US_CHINA_POSTS if city and p.lower() in city.lower()), "")
    if match:
        return {**base, "status": "proposed", "location": match,
                "basis": "the city in your address",
                "how_to_resolve": ("Confirm this against the return locations "
                                   "your scheduling account lists.")}
    return {**base, "status": UNKNOWN, "location": "",
            "basis": "",
            "how_to_resolve": ("Tell Ellis which city you want your passport "
                               "returned in, and it will pre-select it for "
                               "you.")}


# ------------------------------------------------------- the Art. 45 mandate

def mandate_lines(*, applicant_name: str, member_state: str, intermediary: str,
                  answers: dict | None = None, today: date | None = None) -> list[str]:
    """The text of the mandate, as lines.

    Deterministic for a given day and a given answer set, so the artifact is
    hashable and two builds of the same case cannot disagree. Every value Ellis
    does not hold renders as an explicit blank the applicant completes; nothing
    here is guessed, because this is a document somebody signs.
    """
    answers = answers or {}
    today = today or date.today()
    blank = cf.BLANK

    def val(key: str) -> str:
        return cf.form_value("schengen_uniform", key, answers.get(key)) or blank

    lines = [
        "MANDATE TO AN ACCREDITED COMMERCIAL INTERMEDIARY",
        "Regulation (EC) No 810/2009 (Visa Code), Article 45",
        "",
        f"Applicant:        {applicant_name or blank}",
        f"Date of birth:    {val('birth_date')}",
        f"Nationality:      {val('nationality')}",
        f"Passport number:  {val('passport_number')}",
        f"Member state:     {member_state or blank}",
        f"Intermediary:     {intermediary or blank}",
        f"Prepared by Ellis on {today.isoformat()}",
        "",
    ]
    body = [
        ("I authorise the intermediary named above to act for me in connection "
         "with my Schengen short-stay visa application, and only for the acts "
         "listed below:"),
    ]
    for line in body:
        lines.extend("  " + chunk for chunk in cf._wrap(line, 68))
    lines.append("")
    for act in (
        "lodge my application and my supporting documents at the visa "
        "application centre on my behalf;",
        "book the appointment for me through the registered agent channel;",
        "pay the visa fee and the service fee on my behalf;",
        "receive my passport and the decision on my behalf and return them "
        "to me.",
    ):
        chunks = cf._wrap(act, 64)
        lines.append(f"  - {chunks[0]}")
        lines.extend(f"    {chunk}" for chunk in chunks[1:])
    lines.append("")
    lines.append("I understand and confirm that:")
    for note in (
        "my fingerprints and my photograph can be taken only from me, in "
        "person, and no intermediary may give them for me;",
        "the intermediary has no access to the Visa Information System and "
        "cannot decide my application;",
        "I remain personally responsible for the truth of every statement in "
        "my application;",
        "I may withdraw this mandate in writing at any time.",
    ):
        chunks = cf._wrap(note, 64)
        lines.append(f"  - {chunks[0]}")
        lines.extend(f"    {chunk}" for chunk in chunks[1:])
    lines += [
        "",
        "SIGNATURE",
        "",
        "  Signature: ______________________________________",
        "",
        "  Place: ______________________  Date: ____________",
        "",
    ]
    for line in (
        "STATUS: UNSIGNED DRAFT. Ellis prepared this document and has not "
        "signed it. It cannot sign it for you. This becomes a mandate only "
        "when you sign it yourself.",
    ):
        lines.extend(cf._wrap(line, 70))
    return lines


def render_mandate(**kwargs) -> bytes:
    """The mandate as a PDF. Unsigned, and it says so on its face."""
    return pdfgen.text_pdf(mandate_lines(**kwargs),
                           title="Schengen mandate (Art. 45): UNSIGNED DRAFT")


def _mandate_inputs(db, case, route) -> dict:
    answers = getattr(case, "answers", None) or {}
    if db is not None:
        _, _, answers = _merged_answers(db, case, "schengen_uniform")
    name = str(answers.get("full_name") or
               f"{answers.get('given_names', '')} {answers.get('surname', '')}".strip())
    if not name and db is not None:
        applicant = db.get(models.Applicant, case.applicant_id)
        name = getattr(applicant, "full_name", "") or ""
    intermediary = ""
    if isinstance(route, dict):
        for field in ("intermediary", "intermediary_name", "agent_name", "esp"):
            candidate = route.get(field)
            if isinstance(candidate, str) and candidate.strip():
                intermediary = candidate.strip()
                break
    return {"applicant_name": name,
            "member_state": str(getattr(case, "destination_country", "") or ""),
            "intermediary": intermediary,
            "answers": answers}


def draft_mandate(db, case, route=None, *, inputs: dict | None = None,
                  actor: str = "ellis") -> models.StoredDocument:
    """Render the mandate and store it as a case document.

    Idempotent by content: an identical draft is returned rather than stored
    twice, so a cockpit that renders ten times leaves one document behind.
    The stored row is marked UNSIGNED, and there is no function in this module
    that can flip that — signing is an act performed by a person on paper or in
    a signature flow Ellis does not own.
    """
    from sqlalchemy import select
    inputs = inputs if inputs is not None else _mandate_inputs(db, case, route)
    pdf = render_mandate(**inputs)
    sha = hashlib.sha256(pdf).hexdigest()

    existing = db.execute(select(models.StoredDocument).where(
        models.StoredDocument.application_id == case.id,
        models.StoredDocument.doc_type == MANDATE_DOC_TYPE).order_by(
        models.StoredDocument.created_at.desc())).scalars().all()
    for row in existing:
        if row.sha256 == sha:
            return row

    doc = models.StoredDocument(
        org_id=case.org_id, application_id=case.id,
        name="Schengen mandate (Art. 45), UNSIGNED DRAFT.pdf",
        mime="application/pdf", size_bytes=len(pdf), sha256=sha,
        storage_ref=f"derived://{MANDATE_DOC_TYPE}", doc_type=MANDATE_DOC_TYPE,
        ocr_status="done", execution_class="LOCAL_PROVIDER",
        page_classification={"page_type": "authorization_mandate",
                             "classifier": "appt_prestage",
                             "reject": False,
                             "accepted_as_passport_identity": False,
                             "reasons": ["Ellis-drafted Art. 45 mandate"]},
        # The signature state lives WITH the artifact, and it is false. Nothing
        # in Ellis may set it true: only the applicant signs, off this path.
        extracted_fields={"signed": False, "signature_required": True,
                          "signed_by": "", "drafted_by": "ellis",
                          "legal_basis": "Visa Code Art. 45"})
    db.add(doc)
    db.flush()
    db.add(models.DocumentBlob(document_id=doc.id, org_id=case.org_id,
                               mime="application/pdf", content=pdf))
    db.commit()
    from . import audit
    audit.record(db, org_id=case.org_id, application_id=case.id,
                 action="schengen_mandate_drafted",
                 detail={"document_id": doc.id, "signed": False,
                         "size_bytes": len(pdf)},
                 actor=actor)
    return doc


def mandate_status(db, case) -> dict:
    """What Ellis can honestly say about the mandate: drafted, never signed."""
    from sqlalchemy import select
    row = db.execute(select(models.StoredDocument).where(
        models.StoredDocument.application_id == case.id,
        models.StoredDocument.doc_type == MANDATE_DOC_TYPE).order_by(
        models.StoredDocument.created_at.desc())).scalars().first()
    if row is None:
        return {"drafted": False, "document_id": "", "signed": False,
                "signed_by": "",
                "note": "Ellis has not drafted the mandate for this case yet."}
    return {"drafted": True, "document_id": row.id, "signed": False,
            "signed_by": "",
            "note": ("Drafted by Ellis and unsigned. Ellis cannot sign it; the "
                     "applicant signs it personally.")}


# -------------------------------------------------------------- the payload

def prestage(db, case, route=None) -> dict:
    """Everything Ellis can prepare for this appointment, in one payload.

    Returns {route, filled, missing, fees, human_acts, documents,
    mandate_document_id?, ...}. `filled` is what Ellis has, each value naming
    where it came from; `missing` is what it does not have, worded for the
    applicant with the one action that closes it; `human_acts` is what remains
    legally personal and never shrinks to nothing.
    """
    key = route_key(route, case)
    form_key = form_key_for(key, case)
    form = form_answer_set(db, case, form_key)
    documents, missing_docs = _documents(db, case)
    missing = list(form["missing"]) + missing_docs

    payload = {
        "route": key,
        "as_of": AS_OF,
        "case_id": getattr(case, "id", ""),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "form": {k: v for k, v in form.items()
                 if k not in ("filled", "missing", "answers")},
        "filled": form["filled"],
        "missing": missing,
        "documents": documents,
        "human_acts": human_acts(key),
        "never_automated": list(NEVER_AUTOMATED),
        "submitted": False,
        "disclaimer": ("Ellis prepared this from your own answers and documents. "
                       "It has not booked, paid, signed or submitted anything, "
                       "and it will not."),
    }

    if key == ROUTE_US_B1B2:
        payload["fees"] = us_fees(case)
        payload["document_return"] = document_return(db, case)
    else:
        payload["fees"] = schengen_fees(case, answers=form["answers"])
        payload["national_portal"] = national_portal(case)
        inputs = _mandate_inputs(db, case, route)
        doc = (draft_mandate(db, case, route, inputs=inputs)
               if db is not None else None)
        if doc is not None:
            payload["mandate_document_id"] = doc.id
            payload["mandate"] = mandate_status(db, case)
            documents.append({
                "item_id": "", "label": "Schengen mandate (Art. 45), unsigned",
                "required": True, "present": True, "status": "drafted",
                "document_id": doc.id, "source": "prepared_by_ellis",
                "signed": False,
                "note": ("You sign this yourself. Ellis drafted it and cannot "
                         "sign it for you.")})
            if not any(m["key"] == "signature:mandate" for m in missing):
                missing.append({
                    "key": "signature:mandate",
                    "label": "Your signature on the Art. 45 mandate",
                    "required": True, "kind": "signature", "source": "personal",
                    "how_to_resolve": ("Print the drafted mandate, sign and date "
                                       "it, and upload the signed copy.")})
        if not inputs["intermediary"]:
            missing.append({
                "key": "intermediary_name",
                "label": "The accredited intermediary who will act for you",
                "required": True, "kind": "case_fact", "source": "intake",
                "how_to_resolve": ("Name the accredited intermediary for your "
                                   "member state. Ellis leaves it blank on the "
                                   "mandate rather than guessing.")})

    required_missing = [m for m in missing if m.get("required")]
    payload["readiness"] = {
        "filled": len(payload["filled"]),
        "form_fields": form["total_fields"],
        "missing_required": len(required_missing),
        "missing_optional": len(missing) - len(required_missing),
        "documents_present": sum(1 for d in documents if d.get("present")),
        "documents_required": sum(1 for d in documents if d.get("required")),
        "ready_for_the_human": not required_missing,
        "human_acts_remaining": len(payload["human_acts"]),
    }
    return payload
