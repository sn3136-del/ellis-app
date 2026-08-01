"""Going global means the factory now builds portals in languages and shapes
Vietnam never had. These pin the fail-closed rules that keep that honest:
consent boxes are never auto-ticked in ANY language, a flow may not be
released with a segment the runtime would refuse, and a portal whose controls
were not observed produces no flow at all rather than a partial one that
reports success it did not earn.

Every case here comes from the 2026-07-31 adversarial review of the
globalization change set; each was a real, verified defect.
"""
from __future__ import annotations

import re

import pytest

from app.adapter_factory import specgen
from app.adapter_factory.live_driver import BrowserbasePageDriver
from app.portal.released_flow import ReleasedFlowDriver


# --- consent is the applicant's, in every language -------------------------

CONSENT_TEXTS = [
    "Ich habe die Datenschutzerklärung gelesen und akzeptiere sie",
    "Einverständniserklärung zur Datenverarbeitung",
    "J'accepte la déclaration de confidentialité",
    "Kişisel verilerimin işlenmesine ilişkin aydınlatma metnini okudum, "
    "beyan ederim ki KVKK kapsamında rıza gösteriyorum",
    "Dengan ini saya menyatakan menyetujui kebijakan privasi",
    "Заявляю о согласии на обработку персональных данных",
    "أقر بموافقتي على سياسة الخصوصية",
    "I agree to the terms and conditions",
]

DECLARATION_TEXTS = [
    "I hereby declare that the information given above is true and complete",
    "Ich erkläre hiermit, dass meine Angaben wahr und vollständig sind",
    "Je déclare que les renseignements fournis sont exacts",
    "Tôi cam đoan những nội dung khai trên là đúng sự thật",
]


def _js_regexes():
    """The two regexes the browser-side declaration finder actually uses."""
    js = BrowserbasePageDriver._FIND_DECLARATION_JS
    decl = re.search(r"const re = /(.*)/i;", js).group(1)
    consent = re.search(r"const consent = /(.*)/i;", js).group(1)
    return decl, consent


def _py(pattern: str) -> re.Pattern:
    """The JS source patterns are plain regex text — usable from Python once
    the JS-only escape for a literal backslash is undone."""
    return re.compile(pattern.replace("\\\\u0307", "̇"), re.IGNORECASE)


@pytest.mark.parametrize("text", CONSENT_TEXTS)
def test_consent_checkboxes_are_never_swept_into_the_declaration_bundle(text):
    """A privacy/T&C acceptance box is the APPLICANT's to give: it must never
    be auto-ticked by the driver, nor bundled into the declarations the
    applicant affirms in Ellis. Regression: bare 'erklär'/'déclar' stems
    matched Datenschutzerklärung and déclaration de confidentialité."""
    decl_src, consent_src = _js_regexes()
    decl, consent = _py(decl_src), _py(consent_src)
    swept = bool(decl.search(text)) and not bool(consent.search(text))
    assert not swept, f"driver would auto-tick a consent box: {text!r}"

    # released_flow's routing must agree — it has its own consent guard.
    rf_swept = bool(ReleasedFlowDriver._DECLARATION_RE.search(text)) and \
        not bool(ReleasedFlowDriver._CONSENT_RE.search(text))
    assert not rf_swept, f"released_flow would bundle a consent box: {text!r}"


@pytest.mark.parametrize("text", DECLARATION_TEXTS)
def test_genuine_truthfulness_declarations_are_still_recognized(text):
    """The consent guard must not swallow real declarations — those are what
    the applicant declares verbatim in Ellis and Ellis transcribes."""
    decl_src, consent_src = _js_regexes()
    assert _py(decl_src).search(text), f"declaration not recognized: {text!r}"
    assert not _py(consent_src).search(text), f"declaration read as consent: {text!r}"
    assert ReleasedFlowDriver._DECLARATION_RE.search(text)
    assert not ReleasedFlowDriver._CONSENT_RE.search(text)


# --- the advance/confirm vocabulary may not press dangerous controls -------

def test_advance_words_exclude_payment_and_submit_vocabulary():
    words = BrowserbasePageDriver._ADVANCE_WORDS
    for dangerous in ("pay", "pagar", "payer", "zahlen", "submit", "enviar",
                      "confirm", "confirmar", "ok", "yes"):
        assert dangerous not in words


def test_turkish_dotted_capital_advance_word_normalizes():
    """JS lowercases 'İleri' to 'i' + U+0307 + 'leri', which never equals the
    ascii word — the matcher strips the combining dot before comparing."""
    assert "\\u0307" in BrowserbasePageDriver._NORM_JS
    assert "İleri".lower().replace("̇", "") in BrowserbasePageDriver._ADVANCE_WORDS


def test_confirm_notice_only_looks_inside_a_dialog_container():
    """A page-wide scan would press a payment screen's standalone 'Confirmar'.
    The notice matcher is scoped to dialog/modal/notice containers."""
    js = BrowserbasePageDriver._NOTICE_JS
    assert "closest(DIALOG)" in js
    assert 'role="dialog"' in js


def test_click_next_hit_tests_before_pressing():
    """A coordinate click on an occluded button would 'succeed' having hit an
    overlay: the control is scrolled into view and hit-tested first."""
    import inspect
    src = inspect.getsource(BrowserbasePageDriver.click_next_button)
    assert "scrollIntoView" in src
    assert "elementFromPoint" in src


# --- specgen emits whole segments or none of them --------------------------

class _Art:
    def __init__(self, page_key, elements, url_pattern=""):
        self.page_key = page_key
        self.structure = {"elements": elements, "url_pattern": url_pattern}


def _fill_mapping(page_key):
    return {"page_key": page_key, "portal_field": "fullName",
            "selector": "#fullName", "ellis_field": "full_name", "format": ""}


def test_fillable_form_without_an_observed_save_control_emits_no_fill_nodes():
    """Regression: the flow filled the form then NAVIGATED away, abandoning it
    unsaved while reporting success. No observed save/advance control means
    the form is unbuildable, so required_fields_mapped fails honestly."""
    app_art = _Art("application", [
        {"selector": "#fullName", "name": "fullName", "label": "Full name",
         "type": "text"},
        # The only advance control has a non-deterministic ancestor-path
        # selector, so it is never a usable target.
        {"selector": "div.wrap > form > button:nth-child(3)", "name": "next",
         "label": "Next", "type": "button", "submits": "next"},
    ])
    flow = specgen._skeleton_flow("portal.gov.example", {"application": app_art},
                                  [_fill_mapping("application")])
    ids = [n["node_id"] for n in flow]
    assert not any(i.startswith("fill_") for i in ids), ids
    assert "save_form" not in ids


def test_fees_segment_is_all_or_nothing():
    """A payment handoff with no official fee read is incoherent — and the
    runtime's payment_preparation gate refuses it — so the whole fees segment
    drops when no fee element was observed."""
    fees_art = _Art("fees", [{"selector": "#blurb", "name": "blurb",
                              "label": "Information", "type": "text"}])
    flow = specgen._skeleton_flow("portal.gov.example", {"fees": fees_art}, [])
    ids = [n["node_id"] for n in flow]
    assert "payment_handoff" not in ids
    assert "read_fee" not in ids
    assert "goto_fees" not in ids


def test_upload_of_an_unidentified_document_is_never_mapped():
    """Regression: every unknown file input defaulted to 'passport', so a
    photo field would receive the applicant's passport scan. A file input
    whose type cannot be identified stays unmapped (the applicant uploads it
    personally); a photo OF another document is not a portrait."""
    art = _Art("application", [
        {"selector": "#a", "name": "doc1", "label": "Attachment", "type": "file"},
        {"selector": "#b", "name": "ticket", "label": "Upload a photo of your "
         "return ticket", "type": "file"},
        {"selector": "#c", "name": "portrait", "label": "Passport-style photo",
         "type": "file"},
    ])
    mapped = {d["portal_field"]: d["doc_type"]
              for d in specgen._document_mappings({"application": art})}
    assert "doc1" not in mapped            # unidentifiable -> unmapped
    assert "ticket" not in mapped          # names another document -> unmapped
    assert mapped == {}                    # 'Passport-style photo' is ambiguous


def test_expect_path_reached_at_segment_boundary_not_substring():
    """Regression: an SPA appends its own step suffix (…/individual-form/draft)
    or a per-session id, and both the live replay and the recon artifact check
    must accept that as REACHING the declared path — via ONE shared predicate,
    so the two can never disagree (they did: replay passed, recon rejected)."""
    from app.portal.live_browser import path_reaches_expected as reached
    assert reached("/e-arrival/foreigner/individual-form/draft",
                   "/e-arrival/foreigner/individual-form")
    assert reached("https://h.gov.uk/electronic-travel-authorisation/2021-2608/"
                   "electronic-travel-authorisation/enter-email-address",
                   "/enter-email-address")
    assert reached("https://h/main/#/vjwform/step1", "/vjwform")   # fragment route
    assert reached("/anything", "")                                 # no expectation
    assert not reached("/visa-information", "/visa")                # never a substring


def test_payment_without_fee_does_not_block_release_like_vietnam():
    """The release-time capability cross-check must not be STRICTER than the
    proven Vietnam route: Vietnam ships a payment_credentials handoff, no
    READ_FEE, and releases with only submission_execution (payment is the
    applicant's own window). So a failing payment_preparation gate must NOT
    block release; only no-fallback capabilities (submission, appointments) do."""
    from app.global_routes.release_gates import _NO_FALLBACK_CAPABILITIES
    assert "payment_preparation" not in _NO_FALLBACK_CAPABILITIES
    assert "account_registration" not in _NO_FALLBACK_CAPABILITIES
    assert "submission_execution" in _NO_FALLBACK_CAPABILITIES
    assert "appointment_booking" in _NO_FALLBACK_CAPABILITIES


def test_node_ids_from_camelcase_and_punctuated_fields_are_schema_valid():
    """Regression: raw portal field names produced 'fill_fechaNacimiento',
    which the lowercase-only node-id grammar refuses — every non-entry-gated
    build died at schema validation."""
    from app.adapter_factory.schema import _NODE_ID_RE
    used: set[str] = set()
    for raw in ("fechaNacimiento", "applicant[0].name", "Datos--Generales",
                "fechaNacimiento"):     # repeat: must not collide
        slug = specgen._unique_node_slug(raw, used)
        assert _NODE_ID_RE.match(f"fill_{slug}"), slug
    assert len(used) == 4, "distinct fields must get distinct node ids"
