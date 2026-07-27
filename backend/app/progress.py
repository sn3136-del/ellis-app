"""Applicant-safe progress vocabulary for live portal execution.

Every message is derived from a REAL checkpoint: a typed-flow node the runner
is actually executing, a workflow state, or a pending applicant handoff.
Nothing here is fabricated, nothing exposes selectors/internal terms, and no
message credits Kimi with portal work — Browserbase/Playwright drives the
portal and Kimi is structurally absent from that path (adapter_factory/runtime
imports no model provider).
"""
from __future__ import annotations

# step_key -> applicant-facing English message. The renderer translates via
# i18n using the same keys; this map is the backend fallback text.
STEP_MESSAGES = {
    "queued": "Starting secure portal work",
    "connecting": "Connecting securely to the official visa portal",
    "opening_form": "Opening the official application form",
    "portal_instructions": "Completing the portal instructions",
    "filling_name_passport": "Filling your name and passport details",
    "entering_birth_date": "Entering your date of birth",
    "selecting_nationality": "Selecting your nationality",
    "filling_contact": "Filling your contact information",
    "filling_emergency_contact": "Filling your emergency-contact information",
    "filling_travel_details": "Filling your travel details",
    "waiting_occupation_options": "Waiting for the portal's occupation options",
    "waiting_travel_purpose_options": "Waiting for the portal's travel-purpose options",
    "selecting_entry_checkpoint": "Selecting your entry checkpoint",
    "selecting_exit_checkpoint": "Selecting your exit checkpoint",
    "uploading_photo": "Uploading your passport photograph",
    "uploading_passport_biodata": "Uploading your passport biodata page",
    "uploading_document": "Uploading a required document",
    "checking_form": "Checking the government form for missing fields",
    "confirming_declaration": "Confirming the declaration on the official form",
    "preparing_review": "Preparing the application review",
    "reading_fee": "Reading the official fee from the portal",
    "waiting_information": "Waiting for your answers to the portal's questions",
    "waiting_captcha": "Waiting for you to complete the CAPTCHA",
    "waiting_email_code": "Waiting for your email verification code",
    "waiting_sms_code": "Waiting for your SMS verification code",
    "waiting_fee_confirmation": "Waiting for you to confirm the official fee",
    "waiting_payment_details": "Waiting for your payment details",
    "filling_payment_details": "Entering your payment details on the official portal",
    "waiting_payment": "Waiting for your payment confirmation",
    "waiting_payment_result": "Waiting for the portal's payment result",
    "waiting_declaration": "Waiting for your government declaration",
    "waiting_portal_form": "Waiting for you to finish items on the official form",
    "waiting_final_review": "Waiting for your final application review",
    "waiting_submit_confirmation": "Waiting for your final submission confirmation",
    "verifying_payment": "Verifying the payment with the official portal",
    "submitting": "Submitting your application on the official portal",
    "reading_confirmation": "Reading the official confirmation from the portal",
    "submitted": "Application submitted — pending government decision",
    "stalled": ("Ellis has not received a response from the official portal. "
                "Your application data is saved. Retry the connection."),
    "recoverable_failure": ("The official portal did not respond as expected. "
                            "Your application data is saved."),
    "manual_review": "This application needs a closer look before continuing.",
}

# input_source -> step key for FILL/SELECT nodes (grouped, applicant-friendly).
_FIELD_STEPS = {
    "surname": "filling_name_passport",
    "given_names": "filling_name_passport",
    "full_name": "filling_name_passport",
    "sex": "filling_name_passport",
    "passport_number": "filling_name_passport",
    "issuing_country": "filling_name_passport",
    "passport_issue_date": "filling_name_passport",
    "passport_expiry_date": "filling_name_passport",
    "birth_date": "entering_birth_date",
    "nationality": "selecting_nationality",
    "email": "filling_contact",
    "phone": "filling_contact",
    "religion": "filling_contact",
    "place_of_birth": "filling_contact",
    "permanent_address": "filling_contact",
    "contact_address": "filling_contact",
    "home_address": "filling_contact",
    "entry_checkpoint": "selecting_entry_checkpoint",
    "exit_checkpoint": "selecting_exit_checkpoint",
}
_FIELD_PREFIX_STEPS = (
    ("emergency_contact", "filling_emergency_contact"),
    ("occupation", "waiting_occupation_options"),
    ("travel_purpose", "waiting_travel_purpose_options"),
)

_DOC_STEPS = {
    "photo": "uploading_photo",
    "passport": "uploading_passport_biodata",
    "passport_biodata": "uploading_passport_biodata",
}

# workflow pending handoff kind -> waiting step key.
HANDOFF_STEPS = {
    "captcha": "waiting_captcha",
    "otp": "waiting_email_code",
    "email_verification": "waiting_email_code",
    "sms_verification": "waiting_sms_code",
    "additional_information": "waiting_information",
    "fee_confirmation": "waiting_fee_confirmation",
    "payment_approval": "waiting_fee_confirmation",
    "payment": "waiting_payment",
    "payment_credentials": "waiting_payment_details",
    "three_ds": "waiting_payment",
    "payment_verification": "waiting_payment_result",
    "personal_declaration": "waiting_declaration",
    "legally_personal_declaration": "waiting_declaration",
    "portal_form": "waiting_portal_form",
    "final_review": "waiting_final_review",
    "review": "waiting_final_review",
}

# workflow state -> coarse step key (used when no finer node checkpoint exists).
STATE_STEPS = {
    "PORTAL_ACCOUNT_CREATING": "connecting",
    "PORTAL_LOGIN_REQUIRED": "connecting",
    "BROWSER_SESSION_PENDING": "connecting",
    "APPLICATION_FILLING": "filling_travel_details",
    "DOCUMENT_UPLOAD_PENDING": "uploading_document",
    "FEE_DISCOVERY_PENDING": "reading_fee",
    "PAYMENT_APPROVAL_REQUIRED": "waiting_fee_confirmation",
    "PAYMENT_ACTION_REQUIRED": "waiting_payment",
    "PAYMENT_PROCESSING": "verifying_payment",
    "CAPTCHA_ACTION_REQUIRED": "waiting_captcha",
    "OTP_ACTION_REQUIRED": "waiting_email_code",
    "PORTAL_VERIFICATION_REQUIRED": "waiting_email_code",
    "PERSONAL_DECLARATION_REQUIRED": "waiting_declaration",
    "FINAL_REVIEW_REQUIRED": "waiting_final_review",
    "READY_TO_SUBMIT": "waiting_submit_confirmation",
    "SUBMITTING": "submitting",
    "SUBMITTED": "reading_confirmation",
    "CONFIRMATION_PENDING": "reading_confirmation",
    "COMPLETED": "submitted",
    "RECOVERABLE_FAILURE": "recoverable_failure",
    "MANUAL_REVIEW_REQUIRED": "manual_review",
}


def step_for_node(node: dict) -> str:
    """Progress key for one typed-flow node. Derived from the node's declared
    semantic fields only (action / input_source / doc_type / handoff kind) —
    never the selector."""
    action = str((node or {}).get("action") or "")
    node_id = str((node or {}).get("node_id") or "")
    if action == "NAVIGATE":
        return "connecting"
    if action == "WAIT_FOR_STATE":
        return "opening_form"
    if action in ("SCROLL_TO_BOTTOM",):
        return "portal_instructions"
    if action in ("CLICK", "CHECK") and node_id.startswith("entry_gate"):
        return "portal_instructions"
    if action in ("FILL_NON_SENSITIVE", "SELECT_SEARCH", "SELECT"):
        src = str(node.get("input_source") or "")
        for prefix, key in _FIELD_PREFIX_STEPS:
            if src.startswith(prefix):
                return key
        return _FIELD_STEPS.get(src, "filling_travel_details")
    if action == "UPLOAD_AUTHORIZED_DOCUMENT":
        return _DOC_STEPS.get(str(node.get("doc_type") or ""), "uploading_document")
    if action == "READ_FEE":
        return "reading_fee"
    if action in ("APPLICANT_HANDOFF", "PAUSE"):
        return HANDOFF_STEPS.get(str(node.get("handoff_kind") or ""), "waiting_information")
    if action in ("RECONCILE_OUTCOME",):
        return "checking_form"
    if action == "VERIFY_EVIDENCE":
        return "reading_confirmation"
    if action == "COMPLETE":
        return "submitted"
    if node.get("irreversibility") == "irreversible":
        return "submitting"
    if action == "CLICK":
        return "preparing_review"
    return "checking_form"


def step_for_state(state: str, pending: dict | None = None) -> str:
    """Coarse fallback: the workflow state (and any pending handoff kind)."""
    if pending and pending.get("handoff"):
        key = HANDOFF_STEPS.get(str(pending.get("handoff")))
        if key:
            return key
    return STATE_STEPS.get(str(state or ""), "checking_form")


def message_for(step_key: str) -> str:
    return STEP_MESSAGES.get(step_key, STEP_MESSAGES["checking_form"])
