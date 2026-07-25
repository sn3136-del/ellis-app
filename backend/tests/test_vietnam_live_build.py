"""Entry-gated live build (Vietnam eVisa shape) — hermetic, no network.

The real portal gates its application form (/e-visa/foreigners) behind a
declared in-session sequence (Apply now -> scroll both instruction containers
-> tick 2 acknowledgment checkboxes -> Next). These tests drive the REAL
pipeline code (recon -> specgen -> generator -> static/contract/live layers ->
16 release gates) with a fake observer that implements the exact live-observer
contract, including `observe_with_entry_gate` and `spawn_independent`.

Proves: the entry gate is replayed and recorded as the application-form
artifact; lookup/status/login/instruction pages never classify as the form;
the deterministic basic_* fallback map grounds real field mappings (including
missing-answer question metadata); the generated flow contains the entry-gate
nodes, 2 uploads, captcha/payment/declaration handoffs and a reconcile-first
irreversible submit; and all 16 deterministic release gates pass honestly on
the synthetic evidence — with the repeated-sessions gate really requiring a
second independent session.
"""
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.adapter_factory import models as fm, generator, recon, specgen, testing
from app.adapter_factory.build_workflow import (create_request, record_consent,
                                                run_build)
from app.adapter_factory.schema import validate_flow, validate_node
from app.global_routes import families, release_gates
from app.global_routes.models import PortalFamily

VN_HOST = "evisa.gov.vn"
VN_HOSTS = ["evisa.gov.vn", "evisa.xuatnhapcanh.gov.vn"]

ENTRY_GATE = next(e for e in families.load_seed()
                  if e["family_id"] == "vietnam-evisa")["entry_gate"]

PORTAL_EVIDENCE = {"hostnames": VN_HOSTS, "operator": "Vietnam Immigration Department",
                   "verification": "official_government_domain",
                   "portal_url": f"https://{VN_HOST}/",
                   "entry_gate": ENTRY_GATE}


def _el(name, etype="text", required=True, placeholder="", label=""):
    return {"selector": f"#{name}", "name": name, "label": label or name,
            "type": etype, "required": required, "sensitive": False,
            **({"placeholder": placeholder} if placeholder else {})}


def _form_elements():
    els = []
    text_ids = ["basic_ttcnHo", "basic_ttcnDemVaTen", "basic_ttcnEmail",
                "basic_ttcnConfirmEmail", "basic_ttcnTonGiao", "basic_ttcnNoiSinh",
                "basic_ttcnCccd", "basic_hcSo", "basic_hcNoiCap",
                "basic_ttllDcThuongTru", "basic_ttllDcLienHe", "basic_ttllSdt",
                "basic_ttllLlHoTen", "basic_ttllLlNoiOHienTai", "basic_ttllLlSdt",
                "basic_ttllLlQuanHe", "basic_nnNgheNghiepHienTai", "basic_nnTenCtyCq",
                "basic_nnChucVu", "basic_nnDiaChi", "basic_nnSdt",
                "basic_ttcdSoNgayTamTru", "basic_ttcdSdt", "basic_ttcdDcTamTru"]
    for t in text_ids:
        els.append(_el(t))
    for d in ["basic_ttcnNgayThangNamSinhStr", "basic_nddnTtdtTuNgayStr",
              "basic_nddnTtdtDenNgayStr", "basic_hcNgayCapStr",
              "basic_hcGiaTriDenStr", "basic_ttcdThoiGianNcStr"]:
        els.append(_el(d, placeholder="DD/MM/YYYY"))
    for c in ["basic_ttcnGioiTinh", "basic_ttcnMaQt", "basic_nnNgheNghiep",
              "basic_ttcdMucDich", "basic_ttcdTinhTp", "basic_ttcdPhuongXa",
              "basic_ttcdNcCuaKhau", "basic_ttcdXcCuaKhau"]:
        els.append(_el(c, etype="search-combobox"))
    els.append(_el("basic_anhMat", etype="file"))
    els.append(_el("basic_anhHoChieu", etype="file"))
    els.append(_el("basic_ttcdCqTcCamDoan", etype="checkbox", required=True))
    # Chrome/navigation buttons exactly as observed live on the real form
    # page (header Login FIRST in DOM order — the trap the selection must
    # never fall into), then the real primary action.
    for label in ("Login", "Instructions", "Cancel"):
        els.append({"selector": f'button:has-text("{label}")', "name": "",
                    "label": label, "type": "button", "required": False,
                    "sensitive": False, "submits": "submit"})
    els.append({"selector": 'button:has-text("Next")', "name": "",
                "label": "Next", "type": "button", "required": False,
                "sensitive": False, "submits": "submit"})
    return els


class FakeVietnamObserver:
    """Exact live-observer contract: callable(url)->obs, plus
    observe_with_entry_gate, spawn_independent and close."""

    def __init__(self, *, gate_fails=False, single_session=False):
        self.calls = []
        self.gate_calls = []
        self.closed = False
        self.gate_fails = gate_fails
        self.single_session = single_session
        self.spawned = []

    def __call__(self, url):
        self.calls.append(url)
        if url.rstrip("/") == f"https://{VN_HOST}":
            return {"ok": True, "status": 200, "url": url, "hostname": VN_HOST,
                    "title": "Vietnam e-Visa",
                    "elements": [{"selector": 'button:has-text("Apply now")',
                                  "name": "apply_now", "label": "Apply now",
                                  "type": "button", "required": False,
                                  "sensitive": False, "submits": "applynow"}],
                    "links": [], "iframes": [], "delayed": True}
        return {"ok": False, "status": 404, "url": url, "error": "not found"}

    def observe_with_entry_gate(self, base_url, entry_gate):
        self.gate_calls.append((base_url, entry_gate))
        if self.gate_fails:
            return {"ok": False, "status": 0, "url": base_url,
                    "error": "entry gate replay failed: Next stayed disabled"}
        performed = [{"action": a["action"], "selector": a.get("selector", ""),
                      "ok": True} for a in entry_gate.get("actions", [])]
        elements = _form_elements()
        for i, st in enumerate(performed):
            if st["action"] in ("CLICK", "CHECK"):
                elements.append({"selector": st["selector"],
                                 "name": f"entry_gate_step_{i + 1}",
                                 "label": "entry gate control",
                                 "type": "checkbox" if st["action"] == "CHECK" else "button",
                                 "required": False, "sensitive": False})
        return {"ok": True, "status": 200,
                "url": f"https://{VN_HOST}/e-visa/foreigners",
                "hostname": VN_HOST, "title": "e-Visa application",
                "elements": elements, "links": [], "iframes": [],
                "delayed": False, "entry_gate_replayed": performed}

    def spawn_independent(self):
        if self.single_session:
            return None
        child = FakeVietnamObserver()
        self.spawned.append(child)
        return child

    def close(self):
        self.closed = True


@pytest.fixture()
def real_mode(monkeypatch):
    from app.config import settings as settings_fn
    real = settings_fn()
    monkeypatch.setattr(real, "mock_portal_allowed", False, raising=False)
    monkeypatch.setattr(real, "real_only_mode", True, raising=False)
    monkeypatch.setattr(real, "runtime_mode", "local_real_services", raising=False)
    from app.portal import synthetic as syn

    def _forbidden(self, *a, **k):
        raise AssertionError("SyntheticPortal instantiated in a real runtime mode")

    monkeypatch.setattr(syn.SyntheticPortal, "__init__", _forbidden)
    yield


def _build(db, route_key, observer, destination="VNM"):
    req = create_request(db, org_id="orgVN", user_id="global-orchestrator",
                         application_id="", route_key=route_key,
                         destination=destination, visa_type="evisa_tourist",
                         portal_evidence=dict(PORTAL_EVIDENCE),
                         runtime_mode="local_real_services")
    req.jurisdiction_evidence = {"verified": True, "basis": "online national portal"}
    db.commit()
    record_consent(db, req, user_id="global-orchestrator")
    run_build(db, req.id, observer=observer)
    return req


def _version(db, req):
    cand = db.get(fm.AdapterCandidate, req.current_candidate_id)
    return cand, generator.get_version(db, cand.id, cand.current_version)


# ---------------- recon: entry-gate replay produces the form artifact -------

def test_recon_records_entry_gated_form_artifact(db, real_mode):
    req = create_request(db, org_id="orgVN", user_id="u", application_id="",
                         route_key="rkvn|recon", destination="VNM",
                         visa_type="evisa_tourist",
                         portal_evidence=dict(PORTAL_EVIDENCE),
                         runtime_mode="local_real_services")
    obs = FakeVietnamObserver()
    job = recon.run_recon(db, build_request=req, observer=obs,
                          start_paths=("/",), follow_links=True)
    assert job.status == "complete"
    arts = recon.artifacts(db, job.id)
    form = [a for a in arts if a.content_class == "application_form"]
    assert len(form) == 1
    assert form[0].url_pattern.endswith("/e-visa/foreigners")
    # the replay echo survives sanitization (actions + selectors only)
    echo = form[0].structure.get("entry_gate_replayed")
    assert echo and all(s["action"] in ("CLICK", "SCROLL_TO_BOTTOM", "CHECK")
                        for s in echo)
    # the recon wave itself never navigated to the gated path directly
    assert all("/e-visa/foreigners" not in u for u in obs.calls)
    # sanitized: no free text, placeholders capped
    for el in form[0].structure["elements"]:
        assert "value" not in el
        assert len(el.get("placeholder", "")) <= 60


def test_recon_is_honest_when_gate_replay_fails(db, real_mode):
    req = create_request(db, org_id="orgVN", user_id="u", application_id="",
                         route_key="rkvn|recon-fail", destination="VNM",
                         visa_type="evisa_tourist",
                         portal_evidence=dict(PORTAL_EVIDENCE),
                         runtime_mode="local_real_services")
    job = recon.run_recon(db, build_request=req,
                          observer=FakeVietnamObserver(gate_fails=True),
                          start_paths=("/",), follow_links=False)
    arts = recon.artifacts(db, job.id)
    assert not [a for a in arts if a.content_class == "application_form"]
    assert "entry gate replay failed" in (job.error or "")


# ---------------- page-role classification -------------------------------

def _fake_art(page_key, elements, url, content_class="public_page", art_id="a1"):
    return SimpleNamespace(id=art_id, page_key=page_key, content_class=content_class,
                           structure={"url_pattern": url, "elements": elements})


def test_gated_form_artifact_wins_application_role():
    form = _fake_art("application_form", _form_elements(),
                     f"https://{VN_HOST}/e-visa/foreigners",
                     content_class="application_form")
    lookup = _fake_art("web_guest_tra_cuu_ho_so",
                       [_el(f"search_{i}") for i in range(8)],
                       "https://evisa.xuatnhapcanh.gov.vn/web/guest/tra-cuu-ho-so")
    roles = specgen._page_roles({"application_form": form,
                                 "web_guest_tra_cuu_ho_so": lookup},
                                entry_gated=True)
    assert roles["application"] is form


def test_lookup_status_login_instruction_pages_never_classify_as_form():
    lookup = _fake_art("web_guest_tra_cuu_ho_so",
                       [_el(f"code_{i}") for i in range(12)],
                       "https://evisa.xuatnhapcanh.gov.vn/web/guest/tra-cuu-ho-so")
    declare = _fake_art("web_guest_khai_bao_tam_tru",
                        [_el(f"kb_{i}") for i in range(20)],
                        "https://evisa.xuatnhapcanh.gov.vn/web/guest/khai-bao-tam-tru")
    login = _fake_art("signin", [_el("user"), _el("pass", etype="password"),
                                 _el("user2"), _el("user3")],
                      f"https://{VN_HOST}/login")
    guide = _fake_art("huong_dan", [_el(f"g_{i}") for i in range(5)],
                      f"https://{VN_HOST}/huong-dan")
    by_page = {"web_guest_tra_cuu_ho_so": lookup,
               "web_guest_khai_bao_tam_tru": declare,
               "signin": login, "huong_dan": guide}
    # entry-gated portal: NOTHING may claim the form when the gated artifact
    # is missing (that absence must surface as an honest gate failure).
    assert "application" not in specgen._page_roles(dict(by_page), entry_gated=True)
    # even for a non-gated portal these page shapes never classify as the form
    assert "application" not in specgen._page_roles(dict(by_page), entry_gated=False)


def test_form_signature_heuristic_classifies_ungated_form():
    form = _fake_art("deep_form", _form_elements(), f"https://{VN_HOST}/apply/form")
    roles = specgen._page_roles({"deep_form": form}, entry_gated=False)
    assert roles.get("application") is form
    assert specgen._looks_like_application_form(form)


# ---------------- deterministic basic_* mapping --------------------------

def test_known_field_map_covers_ellis_keys_and_questions():
    form = _fake_art("application_form", _form_elements(),
                     f"https://{VN_HOST}/e-visa/foreigners",
                     content_class="application_form")
    props = specgen._known_field_proposals([form])
    by_field = {p["portal_field"]: p for p in props}
    assert by_field["basic_ttcnHo"]["ellis_field"] == "surname"
    assert by_field["basic_ttcnDemVaTen"]["ellis_field"] == "given_names"
    assert by_field["basic_ttcnNgayThangNamSinhStr"]["format"] == "DD/MM/YYYY"
    assert by_field["basic_ttcnGioiTinh"]["kind"] == "select"
    assert by_field["basic_hcSo"]["ellis_field"] == "passport_number"
    assert by_field["basic_hcNoiCap"]["ellis_field"] == "issuing_country"
    # missing-answer keys carry the full applicant-question contract
    for pf, key in [("basic_ttcnTonGiao", "religion"),
                    ("basic_ttcnNoiSinh", "place_of_birth"),
                    ("basic_ttllLlHoTen", "emergency_contact_name"),
                    ("basic_ttcdTinhTp", "vietnam_province"),
                    ("basic_ttcdNcCuaKhau", "entry_checkpoint"),
                    ("basic_ttcdSoNgayTamTru", "days_of_stay")]:
        q = by_field[pf].get("question")
        assert q, pf
        assert set(q) >= {"key", "question", "why", "format", "mandatory", "kind"}
        assert q["key"] == key
        assert "official" in q["why"].lower()
        # applicant-friendly: never selectors/dev terms
        assert "basic_" not in q["question"] and "#" not in q["question"]
    # files/commitment checkbox are NOT field mappings
    assert "basic_anhMat" not in by_field
    assert "basic_ttcdCqTcCamDoan" not in by_field
    # every mapped ellis field is canonical vocabulary
    assert all(p["ellis_field"] in specgen.ELLIS_FIELDS for p in props)


# ---------------- full build: flow, layers, 16 gates ----------------------

def test_entry_gated_build_reaches_release_recommendation(db, real_mode):
    obs = FakeVietnamObserver()
    req = _build(db, "rkvn|build", obs)
    assert req.state == "AWAITING_INTERNAL_RELEASE", (req.state, req.error,
                                                      req.progress)
    cand, row = _version(db, req)
    flow = row.flow
    ids = [n["node_id"] for n in flow]
    actions = {n["node_id"]: n for n in flow}

    # entry-gate nodes, in declared order, straight after NAVIGATE
    gate_ids = [i for i in ids if i.startswith("entry_gate_")]
    assert gate_ids == ["entry_gate_1_click", "entry_gate_2_scroll_to_bottom",
                        "entry_gate_3_scroll_to_bottom", "entry_gate_4_check",
                        "entry_gate_5_check", "entry_gate_6_click"]
    assert ids.index("entry_gate_1_click") == ids.index("open_portal") + 1
    assert actions["entry_gate_6_click"]["expected_transition"] == "/e-visa/foreigners"

    # grounded fill/select segment from the real form ids
    assert actions["fill_basic_ttcnho"]["input_source"] == "surname"
    assert actions["fill_basic_ttcnho"]["action"] == "FILL_NON_SENSITIVE"
    assert actions["fill_basic_ttcnmaqt"]["action"] == "SELECT_SEARCH"
    assert actions["fill_basic_ttcnngaythangnamsinhstr"]["format"] == "DD/MM/YYYY"
    q = actions["fill_basic_ttcntongiao"].get("question")
    assert q and q["key"] == "religion" and q["mandatory"] is True
    # optional national id stays optional (skipped when unanswered)
    assert actions["fill_basic_ttcncccd"]["mandatory"] is False

    # 2 uploads with canonical stored-document types
    uploads = [n for n in flow if n["action"] == "UPLOAD_AUTHORIZED_DOCUMENT"]
    assert {(n["node_id"], n["doc_type"]) for n in uploads} == \
        {("upload_basic_anhmat", "photo"), ("upload_basic_anhhochieu", "passport")}

    # commitment checkbox is CHECKed; handoffs preserved before submit
    assert actions["check_basic_ttcdcqtccamdoan"]["action"] == "CHECK"
    handoffs = {n["handoff_kind"] for n in flow if n["action"] == "APPLICANT_HANDOFF"}
    assert {"captcha", "payment_credentials",
            "legally_personal_declaration"} <= handoffs
    assert ids.index("captcha_handoff") > ids.index("check_basic_ttcdcqtccamdoan")
    assert ids.index("captcha_handoff") < ids.index("submit")

    # continue/submit target the real primary action — NEVER a chrome/
    # navigation control (the live form's header Login button is first in
    # DOM order and must lose to the priority list).
    assert actions["continue_to_review"]["selector"] == 'button:has-text("Next")'
    for nid in ("continue_to_review", "submit"):
        low = actions[nid]["selector"].lower()
        for chrome in ("login", "cancel", "instruction", "back", "home"):
            assert chrome not in low, (nid, actions[nid]["selector"])

    # the unobservable post-review submit is an honest known limitation
    assert any("reversible boundary" in l for l in (row.known_limitations or []))

    # irreversible submit: evidence + reconcile-first + bounded retry
    submit = actions["submit"]
    assert submit["irreversibility"] == "irreversible"
    assert submit["retry_class"] == "reconcile_first"
    assert submit["max_retries"] == 1
    assert any(e.get("category") == "submission_accepted"
               for e in submit["success_evidence"])
    assert ids.index("reconcile_submission") < ids.index("submit")

    assert not validate_flow(flow, allowed_hostnames=VN_HOSTS)

    # layers: static + contract + live structural with TWO independent sessions
    passed = testing.layers_passed(db, row)
    assert {"STATIC_VALIDATED", "CONTRACT_TESTED", "LIVE_STRUCTURAL_TESTED"} <= passed
    run = db.execute(select(fm.AdapterTestRun).where(
        fm.AdapterTestRun.candidate_version_id == row.id,
        fm.AdapterTestRun.layer == "live_structural")).scalars().first()
    assert run.summary["independent_sessions"] == 2
    assert obs.spawned and obs.spawned[0].closed  # second session was real + closed
    assert obs.spawned[0].gate_calls              # and replayed the gate itself


def test_all_16_gates_pass_on_entry_gated_evidence(db, real_mode):
    families.sync_families(db)
    fam = db.execute(select(PortalFamily).where(
        PortalFamily.family_id == "vietnam-evisa")).scalars().one()
    assert fam.verification_status == "verified_official_domain"
    assert fam.entry_gate.get("expect_path") == "/e-visa/foreigners"

    req = _build(db, "rkvn|gates", FakeVietnamObserver())
    cand, row = _version(db, req)
    report = release_gates.evaluate_gates(db, build_request=req, candidate=cand,
                                          version=row, family=fam)
    assert report["passed"], report["missing"]
    assert set(report["gates"]) == set(release_gates.GATE_NAMES)
    g = report["gates"]
    assert "entry gate replayed" in g["safe_navigation_succeeded"]["reason"]
    assert "2 independent sessions" in g["selectors_verified_repeated_sessions"]["reason"]
    assert "DECLARED" in g["captcha_otp_handoffs_preserved"]["reason"]
    assert "photo" in g["upload_flow_mapped_where_applicable"]["reason"]


def test_single_session_live_evidence_fails_repeated_sessions_gate(db, real_mode):
    families.sync_families(db)
    fam = db.execute(select(PortalFamily).where(
        PortalFamily.family_id == "vietnam-evisa")).scalars().one()
    obs = FakeVietnamObserver(single_session=True)
    req = _build(db, "rkvn|onesession", obs)
    cand, row = _version(db, req)
    run = db.execute(select(fm.AdapterTestRun).where(
        fm.AdapterTestRun.candidate_version_id == row.id,
        fm.AdapterTestRun.layer == "live_structural")).scalars().first()
    assert run.summary["independent_sessions"] == 1
    report = release_gates.evaluate_gates(db, build_request=req, candidate=cand,
                                          version=row, family=fam)
    assert report["passed"] is False
    assert any("second independent session" in m for m in report["missing"])


def test_primary_action_selector_prefers_priority_and_excludes_chrome():
    form = _fake_art("application_form", _form_elements(),
                     f"https://{VN_HOST}/e-visa/foreigners",
                     content_class="application_form")
    assert specgen._primary_action_selector(form) == 'button:has-text("Next")'
    # a page with ONLY chrome buttons yields no candidate (honest empty)
    chrome_only = _fake_art("shell", [
        {"selector": 'button:has-text("Login")', "name": "", "label": "Login",
         "type": "button", "required": False, "sensitive": False,
         "submits": "submit"},
        {"selector": 'button:has-text("Cancel")', "name": "", "label": "Cancel",
         "type": "button", "required": False, "sensitive": False,
         "submits": "submit"},
    ], f"https://{VN_HOST}/")
    assert specgen._primary_action_selector(chrome_only) == ""
    # priority order: Submit beats Review when both present
    prio = _fake_art("p", [
        {"selector": 'button:has-text("Review")', "name": "", "label": "Review",
         "type": "button", "required": False, "sensitive": False,
         "submits": "submit"},
        {"selector": 'button:has-text("Submit")', "name": "", "label": "Submit",
         "type": "button", "required": False, "sensitive": False,
         "submits": "submit"},
    ], f"https://{VN_HOST}/x")
    assert specgen._primary_action_selector(prio) == 'button:has-text("Submit")'


# ---------------- schema rules for the new vocabulary ---------------------

def test_schema_rules_for_new_actions():
    host = [VN_HOST]
    ok = {"node_id": "s1", "action": "SCROLL_TO_BOTTOM",
          "allowed_hostname": VN_HOST, "selector": ""}
    assert not validate_node(ok, allowed_hostnames=host)
    bad_irrev = dict(ok, irreversibility="irreversible")
    assert any("reversible" in e for e in
               validate_node(bad_irrev, allowed_hostnames=host))
    sel_search = {"node_id": "s2", "action": "SELECT_SEARCH",
                  "allowed_hostname": VN_HOST, "selector": "#basic_ttcnMaQt",
                  "input_source": "nationality"}
    assert not validate_node(sel_search, allowed_hostnames=host)
    assert any("input_source" in e for e in validate_node(
        {**sel_search, "input_source": ""}, allowed_hostnames=host))
    assert any("sensitive" in e for e in validate_node(
        {**sel_search, "input_source": "card_number"}, allowed_hostnames=host))
    upload = {"node_id": "u1", "action": "UPLOAD_AUTHORIZED_DOCUMENT",
              "allowed_hostname": VN_HOST, "selector": "#basic_anhMat"}
    assert any("doc_type" in e for e in validate_node(upload, allowed_hostnames=host))
    assert not validate_node(dict(upload, doc_type="photo"), allowed_hostnames=host)
    # acknowledgment CHECK fine; declaration-signing CHECK refused
    ack = {"node_id": "c1", "action": "CHECK", "allowed_hostname": VN_HOST,
           "selector": 'input[type="checkbox"]:visible >> nth=0'}
    assert not validate_node(ack, allowed_hostnames=host)
    assert any("sensitive" in e for e in validate_node(
        dict(ack, selector="#declaration_sign"), allowed_hostnames=host))
