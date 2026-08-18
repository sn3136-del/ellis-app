"""Production-TARGETED adapter: US DS-160 (ceac.state.gov/genniv) — the
nonimmigrant visa application itself, for a China→USA B-2 / B1-B2 tourist.

The route, verified against official sources 2026-08-18:
  1. DS-160 online at ceac.state.gov/genniv — this adapter.
  2. MRV fee $185, paid by the APPLICANT on China's own channel (Alipay,
     UnionPay via CITIC, cash at a CITIC branch). Ellis never pays.
  3. Interview appointment at the post — a SEPARATE system
     (ustraveldocs.com/cn + usvisascheduling.com), booked through the
     agent-channel booking desk, never from here.

What Ellis does on CEAC, and what it structurally cannot:
  * The instructions page itself states the agency basis: under 22 C.F.R.
    41.103 another person may assist in preparing the application, but the
    applicant must electronically SIGN it themselves. So Ellis fills the
    factual screens and stops, every time, at the applicant's own acts.
  * The BotDetect code on the location page is cleared by the APPLICANT in
    the secure window (declared captcha handoff on the family's entry gate).
    solve_captcha stays in prohibited_actions — Ellis cannot read it even by
    bug.
  * The Privacy Act "I AGREE" gate and the retrieval security question are
    the applicant's own: the agreement is a portal_terms_consent handoff
    (they sign the verbatim text in Ellis, which then transcribes the tick),
    and the security answer is a credentials handoff — it is the key to
    their application and never passes through Ellis.
  * CEAC's Security and Background screens (arrests, deportations, genocide)
    are sworn under penalty of perjury. They are NOT in the field map, not
    asked in Ellis, and are answered personally in the secure window.
  * Sign and Submit is the applicant's, always.

STATUS: `production_approval_status='tested'`, `production_enabled=False`.
Page 1 (location dropdown + START AN APPLICATION) and page 2 (Privacy Act
box, security question, Continue) were confirmed in an authorized attended
session on 2026-08-18 and their selectors below are REAL. The application
screens past page 2 have NOT been mapped yet: they sit behind the
applicant's own security answer, which is exactly where credential-free
recon must stop. Until an attended mapping pass records them, the flow
declares that honestly (see KNOWN_LIMITATIONS) and the runtime falls back to
page-truth form filling rather than a guessed selector.

ACTIVATION (one reviewed step at a time):
  1. Legal review of the 22 C.F.R. 41.103 preparer basis + CEAC terms.
  2. Attended mapping pass over the application screens (Personal 1/2,
     Address & Phone, Passport, Travel, US Contact, Family, Work/Education),
     recording real selectors — replaces the empty application_mappings.
  3. Verify the split date controls (day dropdown / month dropdown / year
     text) bind per component, not one ISO string into the year box.
  4. Set production_approval_status='production_approved'.
  5. Set production_enabled=True (the validator then permits it).
"""
from __future__ import annotations

from ..contract import DEFAULT_PROHIBITED, PortalAdapter, register_adapter
from .mockland import MockPortalDriver

BASE = "https://ceac.state.gov"
APPROVED = ["ceac.state.gov"]

# The interviewing post, as CEAC's own location dropdown codes it (read live
# from the page 2026-08-18). The pilot books Shanghai; every mainland post is
# listed so the value is chosen, never guessed.
POST_CODES = {
    "beijing": "BEJ",
    "guangzhou": "GUZ",
    "shanghai": "SHG",
    "shenyang": "SNY",
    "wuhan": "WUH",
}
DEFAULT_POST_CODE = POST_CODES["shanghai"]

# Verified live 2026-08-18, in an attended session.
LOCATION_SELECT = '[id="ctl00_SiteContentPlaceHolder_ucLocation_ddlLocation"]'
CAPTCHA_INPUT = ('[id="ctl00_SiteContentPlaceHolder_ucLocation_'
                 'IdentifyCaptcha1_txtCodeTextBox"]')
CAPTCHA_IMAGE = ('[id="c_default_ctl00_sitecontentplaceholder_uclocation_'
                 'identifycaptcha1_defaultcaptcha_CaptchaImage"]')
START_LINK = '[id="ctl00_SiteContentPlaceHolder_lnkNew"]'
PRIVACY_CHECKBOX = '[id="ctl00_SiteContentPlaceHolder_chkbxPrivacyAct"]'
SECURITY_QUESTION_SELECT = '[id="ctl00_SiteContentPlaceHolder_ddlQuestions"]'
SECURITY_ANSWER_INPUT = '[id="ctl00_SiteContentPlaceHolder_txtAnswer"]'
CONFIRM_CONTINUE = '[id="ctl00_SiteContentPlaceHolder_btnContinue"]'
# The application ID CEAC mints on page 2 (AA00…) — the applicant's own key to
# their application, and the number the appointment system later demands.
APPLICATION_ID_SELECTOR = '[id="ctl00_SiteContentPlaceHolder_lblBarcode"]'

# What the attended session on 2026-08-18 confirmed live, page by page:
#   Personal 1  — names, native-alphabet N/A, sex, marital status, the SPLIT
#                 date of birth (ddlDOBDay/ddlDOBMonth/tbxDOBYear), city and
#                 country of birth. Filled and saved to Personal 2.
#   Personal 2  — nationality (ddlAPP_NATL); the other-nationality / US SSN /
#                 tax-ID questions handed off. Saved to Travel.
#   Travel      — Purpose = B, Specify = B2-TM (TOURISM/MEDICAL TREATMENT),
#                 who-is-paying; specific-plans handed off.
# Address & Phone, Passport, U.S. Contact, Family and Work/Education were seen
# in the left-nav but not yet field-mapped.
KNOWN_LIMITATIONS = [
    "Address & Phone, Passport, U.S. Contact, Family and Work/Education are "
    "not field-mapped yet: the applicant completes them in the secure window "
    "while Ellis fills what page truth allows, until an attended mapping pass "
    "records their selectors.",
    "Security and Background screens are deliberately never mapped or "
    "pre-filled — sworn answers belong to the applicant alone.",
    "CEAC splits the date of birth into day/month/year controls; each binds "
    "separately (verified live, pinned by test).",
    "Several controls postback on change (Purpose->Specify, the location "
    "'I AGREE' box): the runtime must wait for each postback before the next "
    "action, exactly as the flow's WAIT_FOR_STATE nodes declare.",
    "The appointment is NOT booked here. China schedules on ustraveldocs.com "
    "/ usvisascheduling.com, whose terms prohibit automated access — that leg "
    "runs through the agent-channel booking desk with a named operator.",
]


def build_us_ceac_ds160_adapter(portal) -> PortalAdapter:
    return register_adapter(PortalAdapter(
        adapter_id="us-ceac-ds160-v1", adapter_version=1,
        destination_country="United States", visa_type="b1b2",
        portal_operator="U.S. Department of State — Consular Electronic "
                        "Application Center (CEAC)",
        approved_domains=APPROVED,
        # CEAC mints an application ID instead of an account: "registration"
        # IS the instructions page where the applicant clears the code and
        # starts an application. Ellis never creates an account here.
        registration_url=f"{BASE}/genniv/",
        login_url=f"{BASE}/genniv/",
        application_url=f"{BASE}/GenNIV/General/complete/complete_personal.aspx",
        # No appointment exists on CEAC — the interview is booked in a wholly
        # separate system (see appointment_booking='prohibited' below). The
        # contract requires a URL, so this names where THIS portal's road ends.
        appointment_url=f"{BASE}/genniv/",
        required_applicant_fields=[
            "surname", "given_names", "sex", "marital_status", "birth_date",
            "place_of_birth", "nationality", "passport_number",
            "issuing_country", "passport_issue_date", "passport_expiry_date",
            "address_line1", "address_city", "address_country", "phone",
            "email", "travel_purpose", "arrival_date", "accommodation",
        ],
        required_documents=["passport_data_page", "portrait_photo"],
        registration_mappings=[],
        # Empty BY DESIGN until the attended mapping pass (ACTIVATION step 2).
        # A guessed CEAC selector would type an applicant's passport number
        # into the wrong screen of a sworn federal form.
        application_mappings=[],
        captcha_detect=f"{CAPTCHA_IMAGE}, .LBD_CaptchaImage",
        password_requirements={},
        # The MRV fee is paid by the applicant on China's own channel.
        payment_policy="applicant", third_party_payment_policy="applicant",
        appointment_search="none",
        appointment_booking="prohibited",
        reschedule_policy="prohibited",
        # 22 C.F.R. 41.103: another person may PREPARE the application; the
        # applicant signs and submits it personally.
        representative_submission="applicant",
        personal_declaration_required=True,
        fee_discovery="none",
        confirmation_extraction="applicationId",
        # CEAC issues no receipt: the MRV fee is paid in another system, by
        # the applicant. The application ID is the only number it gives back.
        receipt_extraction="applicationId",
        resume_behavior=("re-open with the applicant's own application ID + "
                         "security answer in the secure window; reconcile the "
                         "saved application before any re-fill"),
        rate_limits={"searchMinIntervalMs": 60000, "maxChecksPerDay": 12},
        portal_policy_review_date="2026-08-18",
        production_approval_status="tested",
        production_enabled=False,
        allowed_actions=["navigate", "read", "fill", "click", "upload"],
        prohibited_actions=list(DEFAULT_PROHIBITED),
        driver=MockPortalDriver(portal),
    ))
