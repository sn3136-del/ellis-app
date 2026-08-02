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


# --- portal-terms consent: signed in Ellis, transcribed by Ellis -----------

def test_terms_transcription_requires_signed_consent_at_runtime(db):
    """A requires_signed_terms CLICK must fail closed to the consent handoff
    when no matching signed consent exists, and proceed once the applicant
    has signed the EXACT terms text (hash-bound)."""
    from types import SimpleNamespace
    from app import models, portal_terms
    from app.adapter_factory.runtime import FlowRunner
    from app.adapter_factory.compiler import CompiledFlow
    from app.adapter_factory import models as fm

    applicant = models.Applicant(org_id="o", user_id="u", full_name="T",
                                 email="t@example.com")
    db.add(applicant); db.flush()
    app_row = models.VisaApplication(
        org_id="o", user_id="u", applicant_id=applicant.id,
        destination_country="United States", visa_type="tourist", answers={})
    db.add(app_row); db.flush()
    execution = fm.AdapterExecution(org_id="o", application_id=app_row.id,
                                    candidate_id="c1", candidate_version=1)
    db.add(execution); db.commit()

    terms_text = "I have read and understand the information and agree to these terms."
    h = portal_terms.terms_hash(terms_text)
    node = {"node_id": "agree", "action": "CLICK", "selector": "#yes",
            "allowed_hostname": "esta.cbp.dhs.gov",
            "requires_signed_terms": True, "consent_terms_hash": h,
            "consent_family_id": "usa-esta"}
    compiled = CompiledFlow([node,
                             {"node_id": "done", "action": "COMPLETE",
                              "allowed_hostname": "esta.cbp.dhs.gov"}],
                            ["agree", "done"],
                            {"allowed_hostnames": ["esta.cbp.dhs.gov"]})
    clicks = []
    driver = SimpleNamespace(click=lambda sel: (clicks.append(sel) or {"ok": True}))
    runner = FlowRunner(db, execution=execution, compiled=compiled, driver=driver)

    # 1. No consent -> handoff, no click.
    out = runner._step(compiled.nodes["agree"])
    assert out["status"] == "handoff"
    assert out["handoff_kind"] == "portal_terms_consent"
    assert clicks == []

    # 2. Consent staged but UNSIGNED -> still refused.
    consent = portal_terms.create_consent_request(
        db, app_row, portal_family_id="usa-esta",
        terms_title="ESTA disclaimer", terms_text=terms_text)
    out = runner._step(compiled.nodes["agree"])
    assert out["status"] == "handoff" and clicks == []

    # 3. Signed for DIFFERENT text -> refused (hash mismatch).
    other = portal_terms.create_consent_request(
        db, app_row, portal_family_id="usa-esta",
        terms_title="Old terms", terms_text="Some other terms text entirely.")
    portal_terms.record_signature(db, other, signature_id="sig-old", actor="applicant")
    out = runner._step(compiled.nodes["agree"])
    assert out["status"] == "handoff" and clicks == []

    # 4. Signed for the exact text -> transcribed.
    portal_terms.record_signature(db, consent, signature_id="sig-1", actor="applicant")
    out = runner._step(compiled.nodes["agree"])
    assert out["status"] == "ok", out
    assert clicks == ["#yes"]

    # 5. Revoked -> refused again.
    portal_terms.revoke(db, consent, reason="applicant withdrew", actor="applicant")
    clicks.clear()
    out = runner._step(compiled.nodes["agree"])
    assert out["status"] == "handoff" and clicks == []


# --- Ellis-driven account creation: email + vaulted password + applicant OTP -

def test_register_account_generates_vaults_and_never_double_registers(db):
    """REGISTER_ACCOUNT fills the applicant's OWN email plus a FRESH password
    Ellis generates and vaults (never the applicant's secret), reconciles an
    existing session instead of making a second account, and never reads the
    emailed code (that stays an applicant OTP handoff)."""
    from types import SimpleNamespace
    from app import models, vault
    from app.adapter_factory.runtime import FlowRunner
    from app.adapter_factory.compiler import CompiledFlow
    from app.adapter_factory import models as fm

    applicant = models.Applicant(org_id="o", user_id="u", full_name="T",
                                 email="applicant@example.com")
    db.add(applicant); db.flush()
    app_row = models.VisaApplication(org_id="o", user_id="u",
                                     applicant_id=applicant.id,
                                     destination_country="Japan", visa_type="tourist",
                                     answers={"email": "applicant@example.com"})
    db.add(app_row); db.flush()
    execution = fm.AdapterExecution(org_id="o", application_id=app_row.id,
                                    candidate_id="c1", candidate_version=1)
    db.add(execution); db.commit()

    node = {"node_id": "register_account", "action": "REGISTER_ACCOUNT",
            "allowed_hostname": "evisa.example", "retry_class": "reconcile_first",
            "email_selector": "#email", "password_selector": "#password",
            "confirm_password_selector": "#confirm", "submit_selector": "#register",
            "success_evidence": [{"kind": "network",
                                  "category": "account_registration_submitted"}]}
    compiled = CompiledFlow([node, {"node_id": "done", "action": "COMPLETE",
                                    "allowed_hostname": "evisa.example"}],
                            ["register_account", "done"],
                            {"allowed_hostnames": ["evisa.example"]})

    calls = {"register": []}
    authed = {"v": False}
    driver = SimpleNamespace(
        session_authenticated=lambda: authed["v"],
        register_account=lambda **kw: (calls["register"].append(kw) or
                                       {"ok": True, "evidence": {"status": 200}}))
    runner = FlowRunner(db, execution=execution, compiled=compiled, driver=driver,
                        case_answers={"email": "applicant@example.com"})

    # 1. Fresh: registers with the applicant's email + a generated password.
    out = runner._step(compiled.nodes["register_account"])
    assert out["status"] == "ok", out
    assert len(calls["register"]) == 1
    kw = calls["register"][0]
    assert kw["email"] == "applicant@example.com"
    assert kw["password"] and len(kw["password"]) >= 16
    generated_pw = kw["password"]
    ref = out["detail"]["password_vault_ref"]
    assert vault.reveal(ref) == generated_pw       # vaulted, revealable by ref
    # The plaintext password is NEVER in the evidence/detail surface.
    assert generated_pw not in str(out["detail"].get("account_email", ""))

    # 2. Reconcile: an already-authenticated session makes NO second account.
    authed["v"] = True
    out2 = runner._step(compiled.nodes["register_account"])
    assert out2["status"] == "ok"
    assert out2["detail"].get("reconciled_existing") is True
    assert len(calls["register"]) == 1             # still exactly one registration


def test_register_account_flow_requires_otp_handoff_and_reconcile():
    """A REGISTER_ACCOUNT flow must carry the OTP handoff and a reconcile, or
    the account_registration capability gate refuses it."""
    from app.adapter_factory import auto_release, schema
    base = [
        {"node_id": "reg", "action": "REGISTER_ACCOUNT",
         "allowed_hostname": "h", "retry_class": "reconcile_first",
         "email_selector": "#e", "password_selector": "#p", "submit_selector": "#s",
         "success_evidence": [{"kind": "network",
                               "category": "account_registration_submitted"}]},
        {"node_id": "verify", "action": "VERIFY_EVIDENCE", "allowed_hostname": "h",
         "success_evidence": [{"kind": "session_state",
                               "category": "session_authenticated"}]},
    ]

    class V:
        flow = [schema.normalize_node(n) for n in base]      # no reconcile, no otp
    ok, problems, _ = auto_release.capability_gate(V, "account_registration")
    assert not ok
    assert any("reconcile" in p for p in problems)
    assert any("OTP" in p for p in problems)


# --- learning a login-walled portal from a consented session ---------------

def test_a_signed_in_observation_is_never_reported_as_credential_free():
    """Nineteen portals show no form until an account signs in. Their fields
    may come from a consented applicant session — but the gate report must say
    so, or a release launders how the evidence was obtained."""
    import inspect
    from app.global_routes import release_gates as rg
    src = inspect.getsource(rg.evaluate_gates)
    assert "CONSENTED" in src and "signed-in applicant session" in src
    assert "credential-free public observation" in src


def test_signed_in_evidence_without_consent_cannot_release():
    """Consent is the whole basis for this exception; without it recorded, the
    gate fails even though the fields were mapped."""
    import inspect
    from app.global_routes import release_gates as rg
    src = inspect.getsource(rg.evaluate_gates)
    assert "has_consent(build_request)" in src
    assert "consent to learn this portal" in src


def test_public_evidence_is_preferred_over_signed_in():
    """If the form WAS visible credential-free, that is what the report says —
    the exception is only for portals that genuinely offer no other way."""
    import inspect
    from app.global_routes import release_gates as rg
    src = inspect.getsource(rg._form_evidence_provenance)
    assert 'if saw_public:' in src and 'return "public"' in src
