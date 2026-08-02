"""Two portal shapes the skeleton refused to build, pinned as regressions:
the single-page form whose only control is Submit (Malaysia MDAC), and the
declared-account portal whose sign-in is external to its public pages (UAE
ICP via UAE PASS)."""
from app.adapter_factory.specgen import _page_roles, _skeleton_flow


class _Art:
    def __init__(self, page_key, elements, url="https://portal.gov.x/apply",
                 content_class="public_page"):
        self.page_key = page_key
        self.content_class = content_class
        self.structure = {"url_pattern": url, "elements": elements}


def _form_art(page_key="register", with_save=False):
    els = [
        {"selector": "#name", "name": "name", "label": "Name", "type": "text"},
        {"selector": "#passNo", "name": "passNo", "label": "Passport", "type": "text"},
        {"selector": "#dob", "name": "dob", "label": "DD/MM/YYYY", "type": "text"},
        {"selector": "#nat", "name": "nationality", "label": "Nationality", "type": "select"},
        {"selector": "#submitRegistration", "name": "submitRegistration",
         "label": "Submit", "type": "submit"},
    ]
    if with_save:
        els.append({"selector": "#saveBtn", "name": "save",
                    "label": "Save", "type": "button"})
    return _Art(page_key, els)


MAPPINGS = [
    {"page_key": "register", "portal_field": "name", "selector": "#name",
     "ellis_field": "full_name"},
    {"page_key": "register", "portal_field": "passNo", "selector": "#passNo",
     "ellis_field": "passport_number"},
    {"page_key": "register", "portal_field": "dob", "selector": "#dob",
     "ellis_field": "birth_date", "format": "DD/MM/YYYY"},
]


def test_single_page_form_with_only_submit_still_builds_fill_nodes():
    """MDAC's one page has no save control BY DESIGN — its only button
    submits. That page was treated as unbuildable and every fill dropped."""
    roles = {"application": _form_art()}
    nodes = _skeleton_flow("portal.gov.x", roles, MAPPINGS)
    actions = [n["action"] for n in nodes]
    assert actions.count("FILL_NON_SENSITIVE") == 3
    # And nothing in the fill segment clicks the submit control.
    ids = [n["node_id"] for n in nodes]
    assert "save_form" not in ids
    assert "form_filled" in ids


def test_single_page_submit_keeps_declaration_and_reconcile_guards():
    """The submit still happens — on the SAME page — behind the applicant's
    declaration and the reconcile-first guard, exactly like a dedicated
    submission page."""
    roles = _page_roles({"register": _form_art()})
    assert roles.get("submit") is roles.get("application")
    nodes = _skeleton_flow("portal.gov.x", roles, MAPPINGS)
    ids = [n["node_id"] for n in nodes]
    assert ids.index("declaration_handoff") < ids.index("submit")
    assert ids.index("reconcile_submission") < ids.index("submit")
    submit = next(n for n in nodes if n["node_id"] == "submit")
    assert submit["irreversibility"] == "irreversible"


def test_form_with_a_save_control_is_unchanged():
    roles = {"application": _form_art(with_save=True)}
    nodes = _skeleton_flow("portal.gov.x", roles, MAPPINGS)
    ids = [n["node_id"] for n in nodes]
    assert "save_form" in ids and "form_filled" not in ids


def test_declared_account_portal_gets_the_credentials_handoff():
    """UAE ICP signs in through UAE PASS — no password page exists on its
    public pages, yet the family declares account_required. The flow must
    declare the applicant's sign-in instead of silently omitting it (the
    exact gap that held uae-icp one gate from release)."""
    roles = {"application": _form_art(with_save=True)}
    nodes = _skeleton_flow("portal.gov.x", roles, MAPPINGS, account_required=True)
    handoffs = [n for n in nodes if n["action"] == "APPLICANT_HANDOFF"]
    assert any(n.get("handoff_kind") == "credentials" for n in handoffs)
    ids = [n["node_id"] for n in nodes]
    assert ids.index("login_handoff") < ids.index("goto_form")
    # Without the declaration, no phantom handoff appears.
    nodes2 = _skeleton_flow("portal.gov.x", roles, MAPPINGS, account_required=False)
    assert not any(n.get("handoff_kind") == "credentials" for n in nodes2
                   if n["action"] == "APPLICANT_HANDOFF")


# ---- recon addressing: the query is part of the address -------------------

def test_curated_form_url_keeps_its_query_string():
    """Malaysia's MDAC serves its 22-field form at /mdac/main?registerMain and
    an empty shell at /mdac/main. Dropping the query probed the shell."""
    from app.adapter_factory.build_workflow import _recon_paths

    class _Req:
        portal_evidence = {"portal_url": "https://imigresen-online.imi.gov.my/mdac/main",
                           "entry_urls": ["https://imigresen-online.imi.gov.my/mdac/main?registerMain"]}
    paths = _recon_paths(_Req())
    assert "/mdac/main?registerMain" in paths
    assert "/mdac/main" in paths          # the plain page is still probed


def test_two_pages_sharing_a_url_pattern_are_kept_when_their_forms_differ():
    """Sanitized patterns drop the query (values must never survive), so the
    form and the shell collapse to one pattern — and shape must decide."""
    from app.adapter_factory.recon import _shape_key
    shell = {"elements": [{"name": "lang", "type": "select"}]}
    form = {"elements": [{"name": "name", "type": "text"},
                         {"name": "passNo", "type": "text"},
                         {"name": "dob", "type": "text"}]}
    pattern = "https://imigresen-online.imi.gov.my/mdac/main"
    assert _shape_key(pattern, shell) != _shape_key(pattern, form)
    # The same page observed twice is still one page.
    assert _shape_key(pattern, form) == _shape_key(pattern, dict(form))


def test_shape_key_carries_no_values():
    from app.adapter_factory.recon import _shape_key
    art = {"elements": [{"name": "passport", "type": "text",
                         "value": "L898902C3", "label": "Passport"}]}
    assert "L898902C3" not in _shape_key("https://x.gov/apply", art)


def test_a_curated_form_path_gets_one_retry_when_it_renders_empty(db):
    """MDAC renders 22 fields on a good load and a bare shell on a bad one.
    Without a retry, one flaky load costs the whole portal."""
    from app.adapter_factory import recon
    from app.adapter_factory.build_workflow import create_request, record_consent
    HOST = "portal.gov.retry"
    FORM = {"ok": True, "status": 200, "url": f"https://{HOST}/apply?form",
            "hostname": HOST, "title": "Apply", "links": [], "iframes": [],
            "elements": [{"selector": "#a", "name": "a", "label": "A", "type": "text"},
                         {"selector": "#b", "name": "b", "label": "B", "type": "text"},
                         {"selector": "#c", "name": "c", "label": "C", "type": "select"}]}
    SHELL = dict(FORM, elements=[])
    calls = {"n": 0}

    def flaky(url):
        if url.endswith("?form"):
            calls["n"] += 1
            return SHELL if calls["n"] == 1 else FORM     # first load empty
        return {"ok": False, "status": 404, "url": url, "hostname": HOST}

    req = create_request(db, org_id="orgRT", user_id="u", application_id="",
                         route_key="rt|1", destination="Testland",
                         visa_type="tourist",
                         portal_evidence={"hostnames": [HOST],
                                          "entry_urls": [f"https://{HOST}/apply?form"]},
                         runtime_mode="local_mock_demo")
    record_consent(db, req, user_id="u")
    job = recon.run_recon(db, build_request=req, observer=flaky,
                          start_paths=("/apply?form",),
                          curated_paths=("/apply?form",))
    assert calls["n"] == 2, "the curated path must be retried once"
    arts = recon.artifacts(db, job.id)
    assert any(len((a.structure or {}).get("elements", [])) >= 3 for a in arts)


def test_a_standard_probe_path_is_never_retried(db):
    """An empty /application is an absent page, not a flaky one — retrying
    every probe would double every build's live traffic."""
    from app.adapter_factory import recon
    from app.adapter_factory.build_workflow import create_request, record_consent
    HOST = "portal.gov.noretry"
    calls = {"n": 0}

    def counting(url):
        calls["n"] += 1
        return {"ok": True, "status": 200, "url": url, "hostname": HOST,
                "title": "", "elements": [], "links": [], "iframes": []}

    req = create_request(db, org_id="orgNR", user_id="u", application_id="",
                         route_key="nr|1", destination="Testland",
                         visa_type="tourist", portal_evidence={"hostnames": [HOST]},
                         runtime_mode="local_mock_demo")
    record_consent(db, req, user_id="u")
    recon.run_recon(db, build_request=req, observer=counting,
                    start_paths=("/application",), curated_paths=())
    assert calls["n"] == 1


def test_url_pattern_keeps_query_keys_but_never_values():
    """The address must survive sanitization; the data must not."""
    from app.adapter_factory.recon import _pattern
    assert _pattern("https://x.gov.my/mdac/main?registerMain") == \
        "https://x.gov.my/mdac/main?registerMain"
    assert _pattern("https://x.gov/apply?email=real@example.com&ref=L898902C3") == \
        "https://x.gov/apply?email&ref"
    assert _pattern("https://x.gov/apply") == "https://x.gov/apply"


def test_sanitized_structure_still_carries_no_query_values():
    from app.adapter_factory.recon import sanitize_structure
    art = sanitize_structure({
        "url": "https://x.gov/apply?passport=L898902C3&token=abc123",
        "hostname": "x.gov", "status": 200, "title": "Apply", "elements": []})
    assert "L898902C3" not in art["url_pattern"]
    assert "abc123" not in art["url_pattern"]
    assert art["url_pattern"] == "https://x.gov/apply?passport&token"


# ---- live layer: transient vs real failures ------------------------------

def test_live_layer_retries_a_timeout_but_never_a_refusal():
    """A mapped page that times out is the network; a 404/403 is the portal's
    answer and must stand — retrying refusals is how a gate stops meaning
    anything."""
    from app.adapter_factory.testing import _transient_failure
    assert _transient_failure({"ok": False, "status": 0,
                               "error": "Page.goto: Timeout 30000ms exceeded."})
    assert _transient_failure({"ok": False, "status": 503})
    assert _transient_failure(None)
    assert not _transient_failure({"ok": False, "status": 404})
    assert not _transient_failure({"ok": False, "status": 403})
    assert not _transient_failure({"ok": False, "status": 0,
                                   "error": "off-allowlist host refused"})


def test_a_declared_path_written_with_its_hash_route_can_match():
    """Thailand's TDAC lives at /arrival-card/#/tac/arrival-card/add. A gate
    declaring exactly that could never be satisfied: path and fragment were
    compared separately, so a want containing '#' matched neither half."""
    from app.portal.live_browser import path_reaches_expected as reaches
    live = "https://tdac.immigration.go.th/arrival-card/#/tac/arrival-card/add"
    assert reaches(live, "/arrival-card/#/tac/arrival-card/add")
    assert reaches(live, "/tac/arrival-card/add")      # fragment alone still works
    assert reaches(live, "/arrival-card")              # base alone still works
    # And it stays strict: an unrelated route is still a miss.
    assert not reaches("https://tdac.immigration.go.th/arrival-card/#/tac/home",
                       "/tac/arrival-card/add")
    # A sanitized pattern (no fragment recorded) still satisfies the base half.
    assert reaches("https://tdac.immigration.go.th/arrival-card/",
                   "/arrival-card/#/tac/arrival-card/add")


# ---- consent doctrine: never take the DISAGREE option ---------------------

class _FakeLoc:
    """Minimal locator stand-in: evaluate() returns the control's own words."""
    def __init__(self, info): self._info = info
    def evaluate(self, _js): return self._info


def test_terms_choice_refuses_a_control_that_says_disagree():
    """Korea's K-ETA numbers its radios so #agree9 is DISAGREE and #dAgree9 is
    Agree — the shipped gate targeted all four NEGATIVE radios (verified live
    2026-08-02). A curation slip there records the opposite of the applicant's
    intent on a government form, so the runtime reads the control before it
    clicks."""
    import pytest
    from app.portal.live_browser import LiveBrowserSession
    sess = LiveBrowserSession(allowed_hostnames=["www.k-eta.go.kr"])
    for wording in ("Disagree", "I do not agree", "동의하지 않음", "不同意",
                    "No acepto", "Decline"):
        with pytest.raises(RuntimeError, match="DISAGREE"):
            sess._assert_affirmative_consent(_FakeLoc({"text": wording, "value": "0"}), "#agree9")


def test_terms_choice_allows_the_agree_control_and_unreadable_ones():
    from app.portal.live_browser import LiveBrowserSession
    sess = LiveBrowserSession(allowed_hostnames=["x.gov"])
    for wording in ("Agree", "I have read and agreed to the above.", "동의", "同意"):
        sess._assert_affirmative_consent(_FakeLoc({"text": wording, "value": "1"}), "#dAgree9")
    # A control with no readable wording must not block an otherwise honest
    # gate — the negative wording is what we are looking for.
    sess._assert_affirmative_consent(_FakeLoc({"text": "", "value": ""}), "#terms")


def test_verification_code_fields_are_sensitive_in_both_halves_of_the_rule():
    """Georgia names its CAPTCHA input 'SecurityCode' — no 'captcha', no 'otp'.
    It slipped the sensitive net entirely, so a curated gate could have typed
    into a challenge field."""
    import re as _re
    from app.portal.live_browser import LiveBrowserSession, _EXTRACT_JS
    for name in ("SecurityCode", "security_code", "verificationCode",
                 "confirm_code", "authcode", "g-recaptcha-response",
                 "cf-turnstile-response"):
        assert LiveBrowserSession._SENSITIVE_TARGET_RE.search(name), name
    # Ordinary applicant fields stay fillable.
    for name in ("surname", "given_names", "email", "passport_number", "code_of_country"):
        assert not LiveBrowserSession._SENSITIVE_TARGET_RE.search(name), name
    # The in-page extractor must carry the SAME rule, or recon records a
    # challenge field as an ordinary fillable input.
    for token in ("securit", "verif", "turnstile", "recaptcha"):
        assert token in _EXTRACT_JS


# ---- gate vocabulary: SELECT_OPTION and WAIT_FOR_SELECTOR ------------------

def test_select_option_must_declare_its_own_value_and_never_the_applicants():
    """A gate choice is curated data ('not a representative', 'ordinary
    passport') — an applicant-derived one would carry a person into a
    throwaway observation session."""
    import pytest
    from app.global_routes.families import _validate_entry_gate
    ok = {"actions": [{"action": "SELECT_OPTION", "selector": "select#x",
                       "option_value": "no"}],
          "expect_path": "/form"}
    _validate_entry_gate("fam", ok)          # declared literal: fine

    with pytest.raises(ValueError, match="option_value or option_label"):
        _validate_entry_gate("fam", {"actions": [
            {"action": "SELECT_OPTION", "selector": "select#x"}],
            "expect_path": "/form"})

    with pytest.raises(ValueError, match="never the applicant"):
        _validate_entry_gate("fam", {"actions": [
            {"action": "SELECT_OPTION", "selector": "select#x",
             "option_value": "x", "value_from": "route.nationality"}],
            "expect_path": "/form"})


def test_all_three_vocabularies_agree_on_the_gate_actions():
    """A gate valid at seed load must not be refused by recon's sanitizer or
    the live replay — the three lists drifting is how a correct curation dies
    silently downstream."""
    from app.global_routes.families import _ENTRY_GATE_VOCAB
    from app.adapter_factory.recon import _ENTRY_GATE_ACTIONS
    from app.portal.live_browser import LiveBrowserSession
    live = set(LiveBrowserSession.ENTRY_GATE_ACTIONS) | {"TERMS_CHOICE"}
    assert set(_ENTRY_GATE_VOCAB) == set(_ENTRY_GATE_ACTIONS) == live


def test_canadas_gate_is_expressible_now():
    """Canada's 34-field eTA form is public — no account, no CAPTCHA — and was
    unreachable purely because the vocabulary had no way to answer a <select>
    or wait out a virtual waiting room."""
    from app.global_routes.families import load_seed
    ca = next(f for f in load_seed() if f["family_id"] == "canada-ircc")
    acts = [a["action"] for a in ca["entry_gate"]["actions"]]
    # The wizard is a CHAIN: each answer reveals the next question, and two of
    # them are applicant-specific (citizenship, US permanent residence). The
    # gate answers those with declared literals to reveal the form's structure
    # in a session that submits nothing; at runtime the flow fills the
    # applicant's real values from the passport they uploaded.
    assert "WAIT_FOR_SELECTOR" in acts and acts.count("SELECT_OPTION") >= 3
    sels = [a["selector"] for a in ca["entry_gate"]["actions"]]
    assert any("countryOfCitizenship" in x for x in sels)
    # A question only some nationalities see must be declared optional.
    for a in ca["entry_gate"]["actions"]:
        if "PermanentResident" in a["selector"]:
            assert a.get("optional") is True
    # The form host must be the one probe URLs get built from.
    assert ca["hostnames"][0].startswith("eta.onlineservices")
    assert "#/application" in ca["entry_gate"]["expect_path"]


def test_ids_carrying_a_generated_number_are_never_used_as_selectors():
    """Indonesia's arrival card ships #spi_nationality_1785693541603 — that
    suffix is an epoch millisecond stamp minted at page load, so the selector
    could never re-verify in a second session and the portal failed the
    repeated-sessions gate every time (verified live 2026-08-02)."""
    import re
    from app.portal.live_browser import _EXTRACT_JS
    m = re.search(r"const generatedId = /\((.+?)\)/i;", _EXTRACT_JS)
    assert m, "generatedId rule missing from the extractor"
    gen = re.compile(m.group(1), re.I)
    for bad in ("spi_nationality_1785693541603", "spi_full_name_1785693541603",
                "field_1699999999999", "x_a3f8b2c1-4d5e-9f01"):
        assert gen.search(bad), bad
    # Real authored ids — including short numbers — stay usable.
    for good in ("surname", "passportNumber", "field_2024", "step3_email",
                 "addressLine1"):
        assert not gen.search(good), good


def test_a_name_is_preferred_over_a_generated_id():
    """A name survives a re-render where a generated id does not."""
    from app.portal.live_browser import _EXTRACT_JS
    chain = _EXTRACT_JS[_EXTRACT_JS.index("const cssPath"):_EXTRACT_JS.index("const labelFor")]
    id_first = chain.index("!generatedId.test(el.id)) return '#'")
    name_line = chain.index("if (el.name) return tagl + '[name=")
    assert id_first < name_line, "a clean id may still win"
    # …but the fallback id line must come AFTER the name line.
    assert chain.rindex("'#' + CSS.escape(el.id)") > name_line


def test_a_stable_id_prefix_beats_a_deep_css_path():
    """Indonesia ships spi_passport_no_1785696463523: the field name is
    'spi_passport_no_' and only the render stamp moves. Anchoring on the
    prefix keeps both precision and meaning, where a deep ancestor path keeps
    neither."""
    from app.portal.live_browser import _EXTRACT_JS
    assert 'id^=' in _EXTRACT_JS, "no prefix-anchored selector emitted"
    # The prefix rule must sit AFTER the name rule (a name is still better)
    # and BEFORE the ancestor-path fallback (a path is still worse).
    chain = _EXTRACT_JS[_EXTRACT_JS.index("const cssPath"):_EXTRACT_JS.index("const labelFor")]
    assert chain.index("[name=") < chain.index("id^=") < chain.index("parts.join")
    # And it only fires when the prefix is a real field name, not one letter.
    assert "m[1].length >= 4" in chain
    # It must confirm the selector is unique before trusting it.
    assert "querySelectorAll(pref).length === 1" in chain


def test_a_file_input_takes_its_caption_from_the_upload_widget():
    """Custom upload widgets hide the native input and put the caption
    ('Passport bio page') in a wrapper. That caption is what tells Ellis which
    document is wanted — without it the upload cannot be identified and the
    portal fails upload_flow_mapped forever (Thailand's TDAC)."""
    from app.portal.live_browser import _EXTRACT_JS
    block = _EXTRACT_JS[_EXTRACT_JS.index("const labelFor"):_EXTRACT_JS.index("const els")]
    assert "el.type === 'file'" in block, "no file-input caption rule"
    # Bounded: a few ancestors, a short string — never a whole page of prose.
    assert "i < 4" in block and "t.length <= 120" in block
    # And ONLY for file inputs: a text field must keep its own label rules.
    assert block.index("el.type === 'file'") > block.index("el.placeholder")
