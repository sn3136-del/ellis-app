"""Visa-waiver travel authorizations: one vocabulary, per-authorization truth.

An ESTA, an eTA, an ETA and an NZeTA are the same *shape* of thing — a
pre-travel permission a visa-exempt traveller must hold before boarding — and
five different *facts*. Before this module each one would have been re-learned
by whichever surface needed it, which is how a fee goes stale in one place and
not another. So they live here once: what each authorization is, who it binds,
what it costs, how long it lasts, where its official page is, and — the part
that actually decides product behaviour — whether Ellis can deterministically
fill its form at all.

This is the tourist spine, and it is edition-neutral by construction. Nothing
here keys off a government family: a record is a destination, a scope and a set
of sourced facts, so the H1B beneficiary who needs a UK ETA for a layover reads
the same row as a Trip.com holidaymaker.

Four honesty rules hold every line of it:

  * **Curated data carries its receipts.** Every fee, validity and processing
    figure has `sources` and an `as_of`. A figure Ellis has not checked is
    absent with a reason, never a plausible number. `evidence` records HOW the
    fact was read: several government hosts refuse automated fetches, and their
    text was read through a search index rather than scraped — that difference
    is recorded rather than smoothed over.
  * **Requirement questions are answered from the sourced layer or not at
    all.** `is_required` never invents a nationality list. It answers from the
    authorization's own published scope (destination, purpose, the exemptions
    its government states in so many words) and otherwise defers to the route
    policy layer, carrying that layer's verification level through. A
    PROVISIONAL row never produces a verdict — it is surfaced, labelled, beside
    an explicit unknown and the government's own checker URL.
  * **fill_supported is a fact, not an ambition.** Australia's ETA is the case
    that proves the flag earns its keep: the subclass 601 exists only inside
    the AustralianETA app, behind an NFC read of the passport chip and a LIVE
    facial capture on the traveller's own device. No browser can do that, and
    no person may do it for another. So Ellis says so plainly, routes the
    traveller to the official app, and hands over a checklist — which is worth
    more than a fill that cannot exist.
  * **The personal acts stay personal.** Every record carries its irreducible
    human acts in the same shape the appointment cockpit uses. A record with no
    human act left would mean Ellis had done something it must not.
"""
from __future__ import annotations

from sqlalchemy import select

from .global_routes.models import RoutePairPolicy, pair_key
from .visa_snapshot.registry import (RegistryError, normalize_country,
                                     normalize_nationality)

# Every figure below was re-checked on this date against the cited official
# pages. Anything older than a fee change must be re-verified before it is
# shown next to a payment.
AS_OF = "2026-08-11"

ORDINARY = "ordinary_passport"

# --- Verdict vocabulary ------------------------------------------------------
# required / not_required are ASSERTIONS and are only ever reached from a
# sourced basis. not_applicable means this authorization does not govern the
# journey at all. unknown is the honest default and is never a soft "no".
REQUIRED = "required"
NOT_REQUIRED = "not_required"
NOT_APPLICABLE = "not_applicable"
UNKNOWN = "unknown"
VERDICTS = (REQUIRED, NOT_REQUIRED, NOT_APPLICABLE, UNKNOWN)

# Why a verdict was reached. The UI shows the reason; this is what tests pin.
BASES = (
    "unrecognized_destination",     # the destination is not a country Ellis knows
    "unrecognized_nationality",     # ditto for the passport nationality
    "unrecognized_purpose",         # a purpose outside the shared vocabulary
    "destination_mismatch",         # this authorization governs another country
    "purpose_out_of_scope",         # the authorization does not cover this purpose
    "published_exemption",          # the government names this nationality exempt
    "route_policy_verified",        # an officially-researched pair policy answered
    "route_policy_provisional",     # only provisional reference data exists
    "no_route_policy_row",          # the pair has no policy row at all
    "no_route_policy_layer",        # no database session was supplied
)

# The shared purpose vocabulary. A purpose outside it is unknown, not "other".
PURPOSES = ("tourism", "business", "transit", "short_study", "study", "work",
            "family_visit", "medical", "journalism", "diplomatic")

# How a curated fact was actually read. esta.cbp.dhs.gov, travel.state.gov,
# canada.ca and immi.homeaffairs.gov.au all refuse automated fetches; their
# text was read through a search index (never scraped, never bypassed), and
# that is a weaker provenance than a page Ellis fetched itself.
EVIDENCE_KINDS = ("primary_fetch", "official_text_indexed")


class UnknownAuthorization(KeyError):
    """No such travel authorization in the curated set."""


# --- The irreducible human acts, in the shape the cockpit already uses -------

def _pay_act(what: str, where: str) -> dict:
    return {"key": "pay_fee", "label": f"Pay the {what}", "actor": "applicant",
            "why_human": ("Ellis never enters card or bank details for anyone, "
                          "on any portal, for any edition."),
            "detail": where,
            "ellis_prepared": "the exact amount, its source and its date"}


def _submit_act(where: str) -> dict:
    return {"key": "submit_application", "label": "Submit the application",
            "actor": "applicant",
            "why_human": ("The submission is a declaration made in the "
                          "traveller's own name. Ellis fills; the traveller "
                          "submits."),
            "detail": where,
            "ellis_prepared": "every field Ellis could fill, each traceable to "
                              "its source"}


def _review_act() -> dict:
    return {"key": "final_review", "label": "Review every answer before you send it",
            "actor": "applicant",
            "why_human": ("An answer on a government form is the traveller's "
                          "statement, not Ellis's. Unfilled beats wrong."),
            "detail": ("Read the prepared answers. Anything Ellis could not "
                       "establish is left blank and listed, never guessed."),
            "ellis_prepared": "the filled form plus the explicit list of gaps"}


# --- The curated set ---------------------------------------------------------
# Ordered by destination for readability only; nothing depends on the order.

_AUTHORIZATIONS: dict[str, dict] = {
    "usa-esta": {
        "key": "usa-esta",
        "name": "U.S. ESTA (Electronic System for Travel Authorization)",
        "programme": "Visa Waiver Program",
        "destination": "USA",
        "destination_name": "the United States",
        "authority": "U.S. Customs and Border Protection (Department of Homeland Security)",
        "official_url": "https://esta.cbp.dhs.gov/",
        "official_check_url": "https://esta.cbp.dhs.gov/",
        "portal_family_id": "usa-esta",
        "covered_purposes": ("tourism", "business", "transit"),
        "purpose_note": ("The Visa Waiver Program covers business or tourism "
                         "visits of 90 days or less, and transit. Study, "
                         "employment and journalism are outside it and are "
                         "different routes entirely — Ellis does not decide "
                         "which."),
        "exempt_nationalities": (),
        "exemption_note": ("CBP publishes who is eligible for the Visa Waiver "
                           "Program and who must instead hold a visa. Ellis "
                           "does not keep a second copy of that list, so an "
                           "eligibility question it cannot answer from the "
                           "route-policy layer is returned as unknown with the "
                           "official checker linked."),
        "fee": {
            "status": "curated", "amount": 40.27, "currency": "USD",
            "per": "travel authorization", "effective": "2026-01-01",
            "note": ("Raised from USD 21 to USD 40 on 2025-09-30 under Public "
                     "Law 119-21 (HR-1); CBP began assessing the FY2026 "
                     "inflation-adjusted USD 40.27 on 2026-01-01. HR-1 adjusts "
                     "this fee for inflation every fiscal year, so it is "
                     "expected to move again — treat a stale amount as unusable."),
            "source": "https://www.govinfo.gov/content/pkg/FR-2025-11-19/html/2025-20304.htm",
            "evidence": "primary_fetch", "as_of": AS_OF,
        },
        "additional_charges": (),
        "validity": {
            "status": "curated",
            "summary": ("2 years from approval, or until the passport expires, "
                        "whichever comes first"),
            "entries": "multiple",
            "max_stay_days": 90,
            "source": "https://www.cbp.gov/travel/international-visitors/esta",
            "evidence": "official_text_indexed", "as_of": AS_OF,
        },
        "processing": {
            "status": "curated",
            "summary": ("CBP recommends applying at least 72 hours before "
                        "departure; an answer can take that long, and an "
                        "airline will not board a traveller without an "
                        "approved ESTA."),
            "source": "https://www.cbp.gov/travel/international-visitors/esta",
            "evidence": "official_text_indexed", "as_of": AS_OF,
        },
        "fill_supported": True,
        "fill_unsupported_reason": "",
        "fill_note": ("The ESTA form is public — no account, no login wall — so "
                      "Ellis fills it deterministically and the traveller pays "
                      "and submits. CBP arms an invisible reCAPTCHA on the "
                      "disclaimer page: if it ever presents a visible "
                      "challenge that is a typed handoff to the traveller, "
                      "never something Ellis solves."),
        "human_acts": (
            _review_act(),
            _pay_act("ESTA fee", "Pay on esta.cbp.dhs.gov with the traveller's "
                                 "own card. Ellis shows the exact amount and "
                                 "never touches the card field."),
            _submit_act("Send the application from esta.cbp.dhs.gov."),
            {"key": "solve_captcha_if_shown",
             "label": "Answer the CAPTCHA, if one is shown",
             "actor": "applicant", "conditional": True,
             "why_human": ("Solving a CAPTCHA is a declaration that a human is "
                           "present. Ellis never solves one and never evades "
                           "one."),
             "detail": ("Usually invisible and never seen. If a challenge does "
                        "appear, the traveller answers it in their own session."),
             "ellis_prepared": "the session, paused at exactly that point"},
        ),
        "sources": (
            "https://esta.cbp.dhs.gov/",
            "https://www.cbp.gov/travel/international-visitors/esta",
            "https://www.govinfo.gov/content/pkg/FR-2025-11-19/html/2025-20304.htm",
        ),
    },

    "canada-eta": {
        "key": "canada-eta",
        "name": "Canada eTA (electronic travel authorization)",
        "programme": "Canadian visa-exempt air travel",
        "destination": "CAN",
        "destination_name": "Canada",
        "authority": "Immigration, Refugees and Citizenship Canada",
        "official_url": "https://www.canada.ca/en/immigration-refugees-citizenship/services/visit-canada/eta/apply.html",
        "official_check_url": "https://www.canada.ca/en/immigration-refugees-citizenship/services/visit-canada/check-visa-eta.html",
        "portal_family_id": "canada-ircc",
        "covered_purposes": ("tourism", "business", "transit"),
        "purpose_note": ("The eTA is the air-travel authorization for "
                         "visa-exempt travellers visiting or transiting "
                         "Canada. It is not a work or study permit."),
        "exempt_nationalities": (),
        "exemption_note": ("IRCC publishes who needs an eTA, who needs a "
                           "visitor visa and who needs neither. Ellis links "
                           "that checker rather than keeping a second copy of "
                           "the list."),
        "fee": {
            "status": "curated", "amount": 7, "currency": "CAD",
            "per": "person", "effective": "",
            "note": ("Non-refundable, paid by card on the official site. "
                     "IRPA s.91 FEE RULE: a travel professional may NOT charge "
                     "a fee specifically for submitting an eTA. That is a "
                     "release blocker for any paid-agent framing, not a UI "
                     "detail — see docs/SANCTIONED_AGENT_CHANNELS.md."),
            "source": "https://ircc.canada.ca/english/helpcentre/answer.asp?qnum=1056&top=16",
            "evidence": "primary_fetch", "as_of": AS_OF,
        },
        "additional_charges": (),
        "validity": {
            "status": "curated",
            "summary": ("up to 5 years, or until the passport expires, "
                        "whichever comes first"),
            "entries": "multiple",
            "max_stay_days": None,
            "max_stay_note": ("The length of each stay is decided by a border "
                              "services officer on arrival; Ellis does not "
                              "predict it."),
            "source": "https://ircc.canada.ca/english/helpcentre/answer.asp?qnum=1056&top=16",
            "evidence": "primary_fetch", "as_of": AS_OF,
        },
        "processing": {
            "status": "curated",
            "summary": ("most applicants are approved within minutes; some "
                        "requests take several days when supporting documents "
                        "are asked for"),
            "source": "https://www.canada.ca/en/immigration-refugees-citizenship/services/visit-canada/eta/apply.html",
            "evidence": "official_text_indexed", "as_of": AS_OF,
        },
        "fill_supported": True,
        "fill_unsupported_reason": "",
        "fill_note": ("The eTA form is public (no account, no CAPTCHA) but it "
                      "is APPLICANT-CONDITIONAL: the wizard will not render "
                      "its fields until the traveller's own citizenship is "
                      "answered, and a Virtual Waiting Room sits in front of "
                      "the whole thing. So credential-free recon reaches the "
                      "wizard but not the form, and Canada belongs on the "
                      "attended-observation path — see the canada-ircc family "
                      "notes. Fillable, not yet fill-ready."),
        "human_acts": (
            _review_act(),
            _pay_act("CAD 7 eTA fee", "Pay on the official IRCC form with the "
                                      "traveller's own card."),
            _submit_act("Send the application from the official IRCC eTA form."),
        ),
        "sources": (
            "https://www.canada.ca/en/immigration-refugees-citizenship/services/visit-canada/eta/apply.html",
            "https://ircc.canada.ca/english/helpcentre/answer.asp?qnum=1056&top=16",
        ),
    },

    "uk-eta": {
        "key": "uk-eta",
        "name": "UK ETA (Electronic Travel Authorisation)",
        "programme": "UK digital border",
        "destination": "GBR",
        "destination_name": "the United Kingdom (and Jersey, Guernsey and the Isle of Man)",
        "authority": "UK Visas and Immigration, Home Office",
        "official_url": "https://www.gov.uk/eta",
        "official_check_url": "https://www.gov.uk/eta",
        "portal_family_id": "uk-eta",
        "covered_purposes": ("tourism", "business", "transit", "short_study"),
        "purpose_note": ("An ETA permits multiple journeys for stays of up to "
                         "six months at a time. Anything needing longer, or "
                         "needing permission to work, is a visa route and not "
                         "an ETA."),
        "exempt_nationalities": ("GBR", "IRL"),
        "exemption_note": ("The Home Office states plainly: 'British and Irish "
                           "citizens do not need an ETA, including dual "
                           "citizens.' Separately, anyone who already holds UK "
                           "immigration permission to live, work or study does "
                           "not need one either — that is a status fact, not a "
                           "nationality fact, so it is stated here and never "
                           "inferred from a passport."),
        "fee": {
            "status": "curated", "amount": 20, "currency": "GBP",
            "per": "person", "effective": "2026-04-08",
            "note": ("GBP 10 at launch in October 2023, GBP 16 from April "
                     "2025, GBP 20 for applications lodged on or after "
                     "2026-04-08. The same GBP 20 applies online and in the "
                     "UK ETA app."),
            "source": "https://www.gov.uk/eta/apply",
            "evidence": "primary_fetch", "as_of": AS_OF,
        },
        "additional_charges": (),
        "validity": {
            "status": "curated",
            "summary": "2 years, or until the passport expires, whichever is sooner",
            "entries": "multiple",
            "max_stay_days": 180,
            "max_stay_note": "up to six months at a time",
            "source": "https://www.gov.uk/eta/apply",
            "evidence": "primary_fetch", "as_of": AS_OF,
        },
        "processing": {
            "status": "curated",
            "summary": ("a decision by email from UKVI, usually within a day; "
                        "allow up to 3 working days (Monday to Friday)"),
            "source": "https://www.gov.uk/eta/apply",
            "evidence": "primary_fetch", "as_of": AS_OF,
        },
        "fill_supported": True,
        "fill_unsupported_reason": "",
        "fill_note": ("The Home Office runs BOTH a UK ETA app and a web form "
                      "at apply-for-an-eta.homeoffice.gov.uk, at the same "
                      "price, and says to apply online if the app cannot be "
                      "used or if the traveller is not present. The web route "
                      "takes UPLOADED photos of the passport and face, which "
                      "are applicant-supplied files rather than a live device "
                      "capture — so unlike Australia's ETA it is genuinely "
                      "fillable. Ellis fills the web route; the traveller "
                      "supplies the photo, pays and submits."),
        "human_acts": (
            {"key": "supply_photo", "label": "Supply your own passport and face photos",
             "actor": "applicant",
             "why_human": ("A likeness submitted to a government is the "
                           "traveller's own. Ellis carries the file the "
                           "traveller gives it and never synthesises one."),
             "detail": ("Photograph the passport page and the traveller's own "
                        "face to the published rules. Ellis checks what it can "
                        "and states what it cannot."),
             "ellis_prepared": "the photo requirements, stated before you start"},
            _review_act(),
            _pay_act("GBP 20 ETA fee", "Pay on the official Home Office "
                                       "service with the traveller's own card."),
            _submit_act("Send the application from the official Home Office "
                        "ETA service."),
        ),
        "sources": (
            "https://www.gov.uk/eta",
            "https://www.gov.uk/eta/apply",
            "https://homeofficemedia.blog.gov.uk/electronic-travel-authorisation-eta-factsheet-april-2026/",
        ),
    },

    "australia-eta": {
        "key": "australia-eta",
        "name": "Australia ETA (Electronic Travel Authority, subclass 601)",
        "programme": "Australian visitor entry for eligible passport holders",
        "destination": "AUS",
        "destination_name": "Australia",
        "authority": "Department of Home Affairs (Australia)",
        "official_url": "https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601",
        "official_check_url": "https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601",
        # Deliberately unbound: no portal family carries the ETA, because no
        # portal does. australia-immi is ImmiAccount (eVisitor and online
        # lodgement) and its notes say so.
        "portal_family_id": "",
        "covered_purposes": ("tourism", "business"),
        "purpose_note": ("The subclass 601 is a visitor authority for tourism "
                         "or business visitor purposes. It does not permit "
                         "work or study."),
        "exempt_nationalities": (),
        "exemption_note": ("Only a short list of passports is eligible for the "
                           "subclass 601 at all; most other visa-exempt "
                           "travellers use eVisitor (subclass 651), which is a "
                           "different route lodged in ImmiAccount. Home "
                           "Affairs publishes the eligible list and Ellis "
                           "links it rather than copying it."),
        "fee": {
            "status": "curated", "amount": 20, "currency": "AUD",
            "per": "application", "effective": "",
            "note": ("A service charge for using the AustralianETA app. The "
                     "subclass 601 itself carries no visa application charge."),
            "source": "https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601",
            "evidence": "official_text_indexed", "as_of": AS_OF,
        },
        "additional_charges": (),
        "validity": {
            "status": "curated",
            "summary": ("12 months, or until the passport expires, whichever "
                        "is sooner"),
            "entries": "multiple",
            "max_stay_days": 90,
            "max_stay_note": "up to 3 months on each entry",
            "source": "https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601",
            "evidence": "official_text_indexed", "as_of": AS_OF,
        },
        "processing": {
            "status": "unknown",
            "summary": None,
            "reason": ("Home Affairs publishes no single processing figure Ellis "
                       "has verified for the subclass 601, and the app's own "
                       "outcome can be immediate or referred for further "
                       "processing. Ellis will not print a duration it has not "
                       "checked next to a departure date."),
            "source": "https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601",
            "evidence": "official_text_indexed", "as_of": AS_OF,
        },
        "fill_supported": False,
        "fill_unsupported_reason": (
            "APP-ONLY, AND UNAUTOMATABLE BY DESIGN. Home Affairs' own ETA help "
            "text states: 'You cannot apply for an ETA online, or through an "
            "agent or airline at this time, unless the application is lodged "
            "via the ETA app.' The AustralianETA app requires an NFC read of "
            "the passport's chip and a LIVE facial capture on the traveller's "
            "own device — an existing photo is refused. There is no web form "
            "to fill, and both of those acts are personal biometric acts that "
            "nobody may perform for another traveller. So Ellis does not "
            "pretend to a partial fill: it routes the traveller to the "
            "official app with a checklist and says exactly why."),
        "app_only": True,
        "app": {
            "name": "AustralianETA",
            "publisher": "Department of Home Affairs (Australia)",
            "find_it": ("Search for 'AustralianETA' in the Apple App Store or "
                        "Google Play. Only the Department of Home Affairs' own "
                        "app issues an ETA; look-alike apps and sites do not."),
            "device_requirement": ("A phone with NFC. Without NFC the app "
                                   "cannot read the passport chip and the ETA "
                                   "cannot be lodged at all — that is a real "
                                   "dead end, and Ellis says so before the "
                                   "traveller buys a ticket, not after."),
        },
        "traveler_checklist": (
            {"step": 1, "label": "Check the passport is eligible and biometric",
             "detail": ("The subclass 601 is limited to a short list of "
                        "passports, and the app must be able to read the "
                        "chip. Home Affairs' own page is the list.")},
            {"step": 2, "label": "Install the official AustralianETA app",
             "detail": ("From the Apple App Store or Google Play, published by "
                        "the Department of Home Affairs. Nothing else issues "
                        "an ETA.")},
            {"step": 3, "label": "Scan the passport data page, then its chip over NFC",
             "detail": ("Hold the passport against the back of the phone until "
                        "the chip is read. This is why no browser and no agent "
                        "can lodge this application.")},
            {"step": 4, "label": "Take the live facial image in the app",
             "detail": ("The app captures the face live and refuses a stored "
                        "photo. This is the traveller's own biometric act and "
                        "cannot be delegated to Ellis or to anyone else.")},
            {"step": 5, "label": "Answer the declaration questions yourself",
             "detail": ("Character and health declarations are made under the "
                        "traveller's own name. Ellis prepares the facts it "
                        "holds — passport details, travel dates — so the "
                        "answers are quick to give, and gives none of them.")},
            {"step": 6, "label": "Pay the AUD 20 service charge and submit",
             "detail": ("In the app, with the traveller's own card. Ellis "
                        "never enters card details.")},
            {"step": 7, "label": "Keep the outcome",
             "detail": ("The ETA is linked to the passport electronically — "
                        "there is no label to carry, but carry the passport "
                        "that was scanned.")},
        ),
        "human_acts": (
            {"key": "read_passport_chip",
             "label": "Scan your passport's chip with your own phone",
             "actor": "applicant",
             "why_human": ("An NFC chip read needs the physical passport "
                           "against the traveller's own device. No browser "
                           "session can do it and no third party may."),
             "detail": ("In the AustralianETA app, hold the passport against "
                        "the back of the phone."),
             "ellis_prepared": "the eligibility check and the device warning, "
                               "before the traveller starts"},
            {"key": "live_facial_capture",
             "label": "Take the live face scan in the app",
             "actor": "applicant",
             "why_human": ("Biometric capture is a personal act. Ellis never "
                           "performs, supplies or simulates one — and the app "
                           "refuses a stored photo anyway."),
             "detail": "Follow the app's on-screen prompts.",
             "ellis_prepared": "the plain warning that this step exists"},
            {"key": "answer_declarations",
             "label": "Answer the declaration questions yourself",
             "actor": "applicant",
             "why_human": ("Character and health declarations are statements "
                           "on a government form. Ellis never defaults one."),
             "detail": "Answer each question in the app in your own words.",
             "ellis_prepared": "the passport and travel facts you will be asked for"},
            _pay_act("AUD 20 service charge", "In the AustralianETA app, with "
                                              "the traveller's own card."),
            _submit_act("From inside the AustralianETA app."),
        ),
        "sources": (
            "https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601",
            "https://immi.homeaffairs.gov.au/help-text/eta/Pages/en/faqs.aspx",
        ),
    },

    "nz-nzeta": {
        "key": "nz-nzeta",
        "name": "New Zealand NZeTA (Electronic Travel Authority)",
        "programme": "New Zealand visa waiver and transit",
        "destination": "NZL",
        "destination_name": "New Zealand",
        "authority": "Immigration New Zealand",
        "official_url": "https://www.immigration.govt.nz/visas/new-zealand-electronic-travel-authority-nzeta/",
        "official_check_url": "https://www.immigration.govt.nz/visas/new-zealand-electronic-travel-authority-nzeta/",
        "portal_family_id": "nz-nzeta",
        "covered_purposes": ("tourism", "business", "transit"),
        "purpose_note": ("An NZeTA covers visitors from visa-waiver countries "
                         "and transit passengers. A traveller who needs a visa "
                         "is on a different route."),
        "exempt_nationalities": (),
        "exemption_note": ("Immigration New Zealand publishes the visa-waiver "
                           "list and the transit rules. Ellis links the "
                           "official checker rather than keeping a copy."),
        "fee": {
            "status": "curated", "amount": 23, "currency": "NZD",
            "per": "person", "effective": "",
            "channel_amounts": {"official_app": 17, "official_website": 23},
            "note": ("NZD 17 through Immigration New Zealand's own NZeTA app, "
                     "NZD 23 through the Immigration New Zealand website. The "
                     "curated amount is the WEBSITE price because that is the "
                     "channel Ellis can fill; a traveller who uses the app "
                     "pays less."),
            "source": "https://www.immigration.govt.nz/visas/new-zealand-electronic-travel-authority-nzeta/",
            "evidence": "official_text_indexed", "as_of": AS_OF,
        },
        "additional_charges": (
            {"key": "ivl", "amount": 100, "currency": "NZD",
             "label": "International Visitor Conservation and Tourism Levy (IVL)",
             "note": ("Paid at the same time as the NZeTA request and NOT "
                      "refunded even if the request is declined. Travellers on "
                      "Australian or New Zealand passports, many Pacific "
                      "Island passports, Auckland transit passengers, and "
                      "holders of a New Zealand resident visa, an Australian "
                      "Resident Visa or a Business Visitor Visa / APEC card "
                      "are exempt."),
             "source": "https://www.immigration.govt.nz/process-to-apply/applying-for-a-visa/fees-processing-times-and-refunds/paying-the-international-visitor-levy/",
             "evidence": "primary_fetch", "as_of": AS_OF},
        ),
        "validity": {
            "status": "curated",
            "summary": "2 years for travellers (5 years for crew)",
            "entries": "multiple",
            "max_stay_days": None,
            "max_stay_note": ("The permitted stay comes from the visa waiver "
                              "itself, not from the NZeTA; Ellis does not "
                              "restate it here."),
            "source": "https://www.immigration.govt.nz/visas/new-zealand-electronic-travel-authority-nzeta/",
            "evidence": "primary_fetch", "as_of": AS_OF,
        },
        "processing": {
            "status": "curated",
            "summary": "allow up to 72 hours",
            "source": "https://www.immigration.govt.nz/visas/new-zealand-electronic-travel-authority-nzeta/",
            "evidence": "primary_fetch", "as_of": AS_OF,
        },
        "fill_supported": True,
        "fill_unsupported_reason": "",
        "fill_note": ("Immigration New Zealand runs both an app and a web "
                      "request form. The web form is fillable, so Ellis fills "
                      "it and the traveller pays the NZeTA fee and the IVL "
                      "together and submits. Note the app is NZD 6 cheaper — "
                      "Ellis says so rather than quietly costing the traveller "
                      "more."),
        "human_acts": (
            _review_act(),
            _pay_act("NZeTA fee and the IVL", "Paid together on the official "
                                              "Immigration New Zealand form, "
                                              "with the traveller's own card."),
            _submit_act("Send the request from the official Immigration New "
                        "Zealand NZeTA form."),
        ),
        "sources": (
            "https://www.immigration.govt.nz/visas/new-zealand-electronic-travel-authority-nzeta/",
            "https://www.immigration.govt.nz/process-to-apply/applying-for-a-visa/fees-processing-times-and-refunds/paying-the-international-visitor-levy/",
        ),
    },
}

# destination alpha-3 -> the keys that govern it (a destination may one day
# have more than one; the lookup does not assume it has exactly one).
_BY_DESTINATION: dict[str, list[str]] = {}
for _k, _rec in _AUTHORIZATIONS.items():
    _BY_DESTINATION.setdefault(_rec["destination"], []).append(_k)


# --- Read access -------------------------------------------------------------

def keys() -> tuple[str, ...]:
    """Every curated authorization key, in a stable order."""
    return tuple(_AUTHORIZATIONS)


def _deep(value):
    """Freeze-free copy: tuples become lists so the payload is JSON-shaped and
    a caller can never mutate the curated table by editing what it was given."""
    if isinstance(value, dict):
        return {k: _deep(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_deep(v) for v in value]
    return value


def get(key: str) -> dict:
    """The curated record for one authorization. Raises UnknownAuthorization."""
    rec = _AUTHORIZATIONS.get((key or "").strip().lower())
    if rec is None:
        raise UnknownAuthorization(
            f"unknown travel authorization {key!r}; curated keys are "
            f"{', '.join(_AUTHORIZATIONS)}")
    return _deep(rec)


def for_destination(destination: str) -> tuple[str, ...]:
    """The authorization keys governing a destination ('' when Ellis curates
    none — which is NOT a statement that the destination requires nothing)."""
    try:
        dest = normalize_country(destination, field="destination")
    except RegistryError:
        return ()
    return tuple(_BY_DESTINATION.get(dest, ()))


def detail(key: str) -> dict:
    """The full record plus the derived summary the detail endpoint serves:
    official link, what Ellis can do, and what stays the traveller's own act."""
    rec = get(key)
    rec["as_of"] = AS_OF
    rec["how_to_apply"] = {
        "channel": "official_mobile_app" if rec.get("app_only") else "official_web_form",
        "url": rec["official_url"],
        "fill_supported": rec["fill_supported"],
        "reason": rec["fill_unsupported_reason"] or rec.get("fill_note", ""),
        "app": rec.get("app"),
    }
    rec["ellis_never"] = [
        "logs in as the traveller", "signs anything", "enters card details",
        "presses submit", "solves or evades a CAPTCHA",
        "performs or supplies a biometric",
    ]
    return rec


# --- The requirement question ------------------------------------------------

def _verdict(rec: dict, *, status: str, basis: str, reason: str,
             nationality: str = "", destination: str = "", purpose: str = "",
             sources: list | None = None, extra: dict | None = None) -> dict:
    out = {
        "key": rec["key"],
        "authorization": rec["name"],
        "authority": rec["authority"],
        "governs_destination": rec["destination"],
        "nationality": nationality,
        "destination": destination,
        "purpose": purpose,
        "status": status,
        "required": {REQUIRED: True, NOT_REQUIRED: False}.get(status),
        "basis": basis,
        "reason": reason,
        "official_check_url": rec["official_check_url"],
        "as_of": AS_OF,
        "sources": sources if sources is not None else list(rec["sources"]),
    }
    if extra:
        out.update(extra)
    return out


def _pair_policy(db, nationality: str, destination: str):
    if db is None:
        return None
    key = pair_key(nationality, ORDINARY, destination)
    return db.execute(select(RoutePairPolicy).where(
        RoutePairPolicy.pair_key == key)).scalars().first()


def is_required(key: str, nationality: str, destination: str,
                purpose: str = "tourism", *, db=None) -> dict:
    """Does this traveller need THIS authorization for THIS journey?

    The answer is assembled from the authorization's own published scope and
    then, for the nationality question, from the route-policy layer — which is
    the one place in Ellis where nationality x destination policy is sourced
    and version-controlled. This function deliberately keeps no second copy of
    any government's country list: a second copy is a copy that goes stale
    silently.

    A verdict of `required` or `not_required` is only ever reached from a
    VERIFIED basis. Provisional reference data produces `unknown` with the
    provisional reading attached and labelled, because a provisional row has
    never been good enough to count as coverage and is not good enough to tell
    a traveller they may board.
    """
    rec = get(key)

    try:
        dest = normalize_country(destination, field="destination")
    except RegistryError as exc:
        return _verdict(rec, status=UNKNOWN, basis="unrecognized_destination",
                        destination=str(destination or ""), purpose=purpose,
                        reason=f"Ellis does not recognise that destination: {exc}")

    if dest != rec["destination"]:
        return _verdict(
            rec, status=NOT_APPLICABLE, basis="destination_mismatch",
            destination=dest, purpose=purpose,
            reason=(f"The {rec['name']} authorizes travel to "
                    f"{rec['destination_name']} only. It says nothing about "
                    f"entry to {dest}."))

    try:
        nat = normalize_nationality(nationality)
    except RegistryError as exc:
        return _verdict(rec, status=UNKNOWN, basis="unrecognized_nationality",
                        nationality=str(nationality or ""), destination=dest,
                        purpose=purpose,
                        reason=f"Ellis does not recognise that nationality: {exc}")

    want = (purpose or "").strip().lower().replace("-", "_").replace(" ", "_")
    if want not in PURPOSES:
        return _verdict(rec, status=UNKNOWN, basis="unrecognized_purpose",
                        nationality=nat, destination=dest,
                        purpose=str(purpose or ""),
                        reason=(f"'{purpose}' is not a travel purpose Ellis "
                                f"knows, so it will not guess which route it "
                                f"belongs to. Known purposes: "
                                f"{', '.join(PURPOSES)}."))
    if want not in rec["covered_purposes"]:
        return _verdict(
            rec, status=NOT_APPLICABLE, basis="purpose_out_of_scope",
            nationality=nat, destination=dest, purpose=want,
            reason=(f"The {rec['name']} covers "
                    f"{', '.join(rec['covered_purposes'])}. It does not cover "
                    f"{want}. {rec['purpose_note']} Which route does apply is "
                    f"not something this module decides."))

    if nat in rec["exempt_nationalities"]:
        return _verdict(
            rec, status=NOT_REQUIRED, basis="published_exemption",
            nationality=nat, destination=dest, purpose=want,
            reason=(f"{rec['authority']} names this nationality as exempt. "
                    f"{rec['exemption_note']}"))

    policy = _pair_policy(db, nat, dest)
    if db is None:
        return _verdict(
            rec, status=UNKNOWN, basis="no_route_policy_layer",
            nationality=nat, destination=dest, purpose=want,
            reason=("Ellis answers the nationality question from its "
                    "route-policy layer, and no database session was supplied "
                    "to this call. Use the official checker linked here."))
    if policy is None:
        return _verdict(
            rec, status=UNKNOWN, basis="no_route_policy_row",
            nationality=nat, destination=dest, purpose=want,
            reason=(f"Ellis holds no route policy for a {nat} passport "
                    f"travelling to {dest}, so it does not know whether this "
                    f"authorization applies. Use the official checker linked "
                    f"here."))

    outcome = policy.route_outcome or "UNRESOLVED"
    reading = {"route_outcome": outcome,
               "verification_status": policy.verification_status,
               "source": policy.source,
               "official_source_urls": list(policy.official_source_urls or []),
               "dataset_date": policy.dataset_date}

    if policy.verification_status != "verified":
        return _verdict(
            rec, status=UNKNOWN, basis="route_policy_provisional",
            nationality=nat, destination=dest, purpose=want,
            reason=(f"The only data Ellis holds for a {nat} passport "
                    f"travelling to {dest} is {policy.verification_status} "
                    f"({policy.source}), reading {outcome}. Provisional data "
                    f"has never counted as coverage and it does not answer a "
                    f"boarding question either. Use the official checker "
                    f"linked here."),
            extra={"provisional_indication": reading})

    if outcome == "ELECTRONIC_AUTHORIZATION":
        return _verdict(
            rec, status=REQUIRED, basis="route_policy_verified",
            nationality=nat, destination=dest, purpose=want,
            reason=(f"Officially-researched route policy resolves a {nat} "
                    f"passport travelling to {dest} to "
                    f"ELECTRONIC_AUTHORIZATION, which is this authorization."),
            sources=(list(policy.official_source_urls or []) or list(rec["sources"])),
            extra={"route_policy": reading})

    return _verdict(
        rec, status=NOT_REQUIRED, basis="route_policy_verified",
        nationality=nat, destination=dest, purpose=want,
        reason=(f"Officially-researched route policy resolves a {nat} passport "
                f"travelling to {dest} to {outcome}, which is not an "
                f"electronic travel authorization. That is what this "
                f"authorization is not; it is not advice about what the "
                f"traveller does need."),
        sources=(list(policy.official_source_urls or []) or list(rec["sources"])),
        extra={"route_policy": reading})


def applicable(nationality: str, destination: str, purpose: str = "tourism",
               *, db=None) -> dict:
    """Every curated authorization that could govern this journey, each with
    its own verdict. An empty list is stated as a gap in Ellis's curation, NOT
    as 'nothing is required' — the distinction is the whole point."""
    try:
        dest = normalize_country(destination, field="destination")
    except RegistryError as exc:
        return {"status": UNKNOWN, "destination": str(destination or ""),
                "nationality": str(nationality or ""), "purpose": purpose,
                "authorizations": [], "as_of": AS_OF,
                "note": f"Ellis does not recognise that destination: {exc}"}

    found = _BY_DESTINATION.get(dest, [])
    if not found:
        return {"status": UNKNOWN, "destination": dest,
                "nationality": str(nationality or ""), "purpose": purpose,
                "authorizations": [], "as_of": AS_OF,
                "curated_destinations": sorted(_BY_DESTINATION),
                "note": ("Ellis curates visa-waiver travel authorizations for "
                         f"{', '.join(sorted(_BY_DESTINATION))} only. It holds "
                         f"none for {dest}, which is a gap in Ellis's data and "
                         f"NOT a statement that {dest} requires no pre-travel "
                         "authorization. The traveller's route for that "
                         "destination is resolved elsewhere.")}

    verdicts = [is_required(k, nationality, dest, purpose, db=db) for k in found]
    return {"status": "curated", "destination": dest,
            "nationality": str(nationality or ""), "purpose": purpose,
            "authorizations": verdicts, "as_of": AS_OF,
            "note": ("Each entry answers only for its own authorization. An "
                     "'unknown' is an honest gap with the government's own "
                     "checker linked, never a soft no.")}


def summary() -> list[dict]:
    """The compact list every caller wants first: what exists, what it costs,
    and whether Ellis can fill it."""
    out = []
    for key, rec in _AUTHORIZATIONS.items():
        fee = rec["fee"]
        out.append({
            "key": key, "name": rec["name"],
            "destination": rec["destination"],
            "destination_name": rec["destination_name"],
            "authority": rec["authority"],
            "official_url": rec["official_url"],
            "portal_family_id": rec["portal_family_id"],
            "fee": _deep(fee),
            "additional_charges": _deep(rec["additional_charges"]),
            "validity": _deep(rec["validity"]),
            "processing": _deep(rec["processing"]),
            "covered_purposes": list(rec["covered_purposes"]),
            "fill_supported": rec["fill_supported"],
            "fill_unsupported_reason": rec["fill_unsupported_reason"],
            "app_only": bool(rec.get("app_only")),
            "as_of": AS_OF,
        })
    return out
