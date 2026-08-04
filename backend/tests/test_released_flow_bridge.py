"""The case → released-adapter bridge (Part 1) + dynamic questions (Part 4).

Covers, hermetically (no network, fake page driver):
- fail-closed resolution: no pair / no released link / no binding → None, and
  the classic RealOnlyStop 409 still fires for unreleased routes;
- a released family binding resolves and yields a LIVE_PRODUCTION adapter;
- FlowRunner pauses with applicant-friendly questions on missing answers and
  resumes from the SAME node (same execution row — never a duplicate);
- real document upload reaches the declared file control;
- the provide_information signal validates and merges only asked answers;
- personal_gate readiness derives from the deterministic release (no admin);
- fee text parsing is exact-or-refuse.
"""
import os

import pytest
from sqlalchemy import select

from app import config, models
from app.adapter_factory import models as fm
from app.adapter_factory.runtime import FlowRunner, parse_fee_text
from app.portal import released_flow
from app.global_routes.models import (FamilyAdapterLink, PortalFamily,
                                      RoutePairPolicy, pair_key)

RK = ("rk1|nat=CHN|iss=CHN|doc=ordinary_passport|res=CHN|dest=VNM|"
      "cat=evisa_tourist|sub=any|pur=tourism|per=2026-07-23|jur=default")
HOST = "evisa.gov.vn"
FAMILY = "vietnam-evisa-test"


def _flow():
    def node(nid, action, **kw):
        return {"node_id": nid, "action": action, "allowed_hostname": HOST, **kw}
    return [
        node("open_home", "NAVIGATE",
             allowed_url_patterns=[f"https://{HOST}/"]),
        node("fill_surname", "FILL_NON_SENSITIVE", selector="#basic_ttcnHo",
             input_source="surname"),
        node("fill_religion", "FILL_NON_SENSITIVE", selector="#basic_ttcnTonGiao",
             input_source="religion",
             question={"question": "What is your religion?",
                       "why": "The official form has a mandatory religion field.",
                       "format": "free text", "kind": "text"}),
        node("pick_entry_gate", "SELECT_SEARCH", selector="#basic_ttcdNcCuaKhau",
             input_source="entry_checkpoint",
             question={"question": "Where will you enter Vietnam?",
                       "kind": "select"}),
        node("upload_passport", "UPLOAD_AUTHORIZED_DOCUMENT",
             selector="#basic_anhHoChieu", doc_type="passport"),
        node("captcha", "APPLICANT_HANDOFF", handoff_kind="captcha"),
        node("read_fee", "READ_FEE", selector="#fee", currency_hint="USD"),
        node("pay_handoff", "APPLICANT_HANDOFF", handoff_kind="payment_credentials"),
        node("declaration", "APPLICANT_HANDOFF",
             handoff_kind="legally_personal_declaration"),
        node("reconcile", "RECONCILE_OUTCOME"),
        node("submit", "CLICK", selector="#submit-btn", irreversibility="irreversible",
             retry_class="reconcile_first", max_retries=1,
             success_evidence=[{"kind": "network", "category": "submission_accepted",
                                "url_substring": "/api/applications"}]),
        node("done", "COMPLETE"),
    ]


class _FakeKeyboard:
    def __init__(self, page):
        self.page = page

    def type(self, text, **_kw):
        self.page.typed.append(text)

    def press(self, key, **_kw):
        self.page.pressed.append(key)


class FakePage:
    """Just enough of a Playwright page for the FlowRunner drivers."""

    def __init__(self):
        self.filled = {}
        self.checked = []
        self.uploaded = {}
        self.clicked = []
        self.typed = []
        self.pressed = []
        self.url = f"https://{HOST}/"
        self.fee_text = "25 USD"
        self.keyboard = _FakeKeyboard(self)

    def goto(self, url, **_kw):
        self.url = url
        return type("R", (), {"status": 200})()

    def fill(self, selector, value, **_kw):
        self.filled[selector] = value

    def click(self, selector, **_kw):
        self.clicked.append(selector)

    def check(self, selector, **_kw):
        self.checked.append(selector)

    def press(self, selector, key, **_kw):
        pass

    def wait_for_selector(self, selector, **_kw):
        return None

    def set_input_files(self, selector, path, **_kw):
        with open(path, "rb") as fh:
            self.uploaded[selector] = fh.read()

    def query_selector(self, selector):
        if selector == "#fee":
            text = self.fee_text
            return type("E", (), {"inner_text": lambda self_: text})()
        return None

    def eval_on_selector_all(self, *_a, **_k):
        return None

    def eval_on_selector(self, selector, _js, **_k):
        # The driver reads a field back after writing it, because a portal
        # that silently drops a value must not read as filled. A double that
        # answers "" to every read says every field was dropped — so answer
        # what this page was actually given: the value filled into THIS
        # selector, else the last typed query (the combobox read-back).
        if selector in self.filled:
            return self.filled[selector]
        return self.typed[-1] if self.typed else ""

    def query_selector_all(self, selector):
        if selector == '[role="option"]' and self.typed:
            page = self
            text = self.typed[-1]

            class _Opt:
                def inner_text(self_o):
                    return text

                def click(self_o):
                    page.clicked.append(f"option:{text}")

                def evaluate(self_o, js):
                    if "textContent" in js:
                        return text
                    page.clicked.append(f"option:{text}")
                    return None
            return [_Opt()]
        return []

    def wait_for_timeout(self, _ms):
        pass

    def locator(self, selector):
        base = selector.split(" >> ")[0]
        page = self

        class _Loc:
            @property
            def first(self_l):
                return self_l

            def click(self_l, **_kw):
                page.clicked.append(base)
        return _Loc()

    def input_value(self, selector, **_k):
        return self.filled.get(selector, "")

    def evaluate(self, js, arg=None):
        # the combobox picker: echo the requested value as the chosen option
        if "select-item" in str(js):
            return {"chosen": arg} if arg else {"labels": []}
        # the CAPTCHA-presence probe: this fake portal DOES show a challenge
        # (the flow's captcha handoff is the behavior under test — a page
        # without one is now honestly skipped)
        if "recaptcha" in str(js):
            return {"present": True, "kind": ".g-recaptcha"}
        return None

    def on(self, *_a, **_k):
        pass


def _mk_released_route(db, *, suffix=""):
    fam_id = FAMILY + suffix
    rk = RK.replace("dest=VNM", f"dest=VNM{suffix}") if suffix else RK
    fam = PortalFamily(family_id=fam_id, name="Vietnam e-Visa (test)",
                       kind="evisa_portal", operator="Immigration Department",
                       base_url=f"https://{HOST}/", hostnames=[HOST],
                       destinations=["VNM"], account_required=False,
                       verification_status="verified_live")
    db.add(fam)
    cand = fm.AdapterCandidate(build_request_id="req" + suffix, route_key=rk,
                               adapter_id="vnm-test" + suffix, current_version=1,
                               status="released")
    db.add(cand)
    db.flush()
    ver = fm.AdapterCandidateVersion(
        candidate_id=cand.id, version=1,
        manifest={"route_key": rk, "allowed_hostnames": [HOST],
                  "portal_operator": "Immigration Department",
                  "confirmation_extraction": "#confirmation-no",
                  "receipt_extraction": "#receipt-no"},
        flow=_flow(), field_mappings=[], document_mappings=[],
        evidence_rules={"banner_text_sufficient": False},
        kill_switch_key="ks-vnm-test" + suffix)
    db.add(ver)
    rel = fm.AdapterRelease(candidate_id=cand.id, candidate_version=1, route_key=rk,
                            tier="sandbox", released_by="deterministic-release-engine",
                            release_kind="deterministic_auto", evidence_package={},
                            active=True)
    db.add(rel)
    db.flush()
    db.add(fm.AdapterRuntimeBinding(route_key=rk, tier="sandbox",
                                    candidate_id=cand.id, candidate_version=1,
                                    release_id=rel.id))
    gates = {name: True for name in (
        "official_portal_identity_confirmed", "destination_and_jurisdiction_correct",
        "no_mock_or_synthetic_driver", "safe_navigation_succeeded",
        "required_fields_mapped", "selectors_verified_repeated_sessions",
        "account_flow_mapped_where_applicable", "upload_flow_mapped_where_applicable",
        "applicant_confirmation_gates_preserved", "captcha_otp_handoffs_preserved",
        "payment_confirmation_preserved", "submission_confirmation_preserved",
        "no_irreversible_action_executed_in_testing", "structured_provider_errors",
        "security_scan_passed", "regression_tests_passed")}
    link = FamilyAdapterLink(family_id=fam_id, candidate_id=cand.id,
                             representative_route_key=rk, status="released",
                             released=True, release_tier="sandbox",
                             gate_report={"passed": True, "missing": [],
                                          "gates": gates})
    db.add(link)
    # pair_key is unique: another module's baseline import may already own
    # this row (the db fixture is session-shared), so bind the EXISTING row
    # to this family rather than inserting a duplicate.
    pk = pair_key("CHN", "ordinary_passport", "VNM")
    pol = db.execute(select(RoutePairPolicy).where(
        RoutePairPolicy.pair_key == pk)).scalars().first()
    if pol is None:
        pol = RoutePairPolicy(snapshot_date="2026-07-23", pair_key=pk,
                              passport_nationality="CHN",
                              travel_document_type="ordinary_passport",
                              destination_country="VNM")
        db.add(pol)
    pol.disposition = "EVISA_REQUIRED"
    pol.route_outcome = "EVISA"
    pol.primary_category = "evisa_tourist"
    pol.portal_family_id = fam_id
    pol.source = "official_research"
    pol.verification_status = "verified"
    pol.release_status = "released_adapter"
    db.commit()
    return cand, ver, link


def _case(db, *, org="org1"):
    applicant = models.Applicant(org_id=org, user_id="user1", full_name="T A",
                                 email="t@example.com")
    db.add(applicant)
    db.flush()
    app_row = models.VisaApplication(
        org_id=org, user_id="user1", applicant_id=applicant.id,
        destination_country="Vietnam", visa_type="tourist", adapter_id="",
        state="DRAFT",
        answers={"passport_nationality": "CHN", "nationality": "CHN",
                 "travel_document_type": "ordinary_passport",
                 "surname": "TESTER"})
    db.add(app_row)
    db.commit()
    return app_row


@pytest.fixture()
def real_only(db):
    saved = os.environ.get("ELLIS_RUNTIME_MODE")
    os.environ["ELLIS_RUNTIME_MODE"] = "local_real_services"
    config.settings.cache_clear()
    yield
    if saved is None:
        os.environ.pop("ELLIS_RUNTIME_MODE", None)
    else:
        os.environ["ELLIS_RUNTIME_MODE"] = saved
    config.settings.cache_clear()


@pytest.fixture(autouse=True)
def _clean(db):
    yield
    for table in (fm.AdapterRuntimeBinding, fm.AdapterRelease, fm.AdapterExecution,
                  fm.AdapterCheckpoint, fm.AdapterOutcomeEvidence,
                  fm.AdapterCandidateVersion, fm.AdapterCandidate,
                  FamilyAdapterLink, PortalFamily, RoutePairPolicy):
        for row in db.execute(select(table)).scalars().all():
            db.delete(row)
    db.commit()


# ---- resolution ------------------------------------------------------------

def test_unreleased_route_resolves_to_none(db):
    from app.portal.released_flow import resolve_released_route
    app_row = _case(db)
    assert resolve_released_route(db, app_row) is None


def test_link_without_release_is_none(db):
    from app.portal.released_flow import resolve_released_route
    cand, ver, link = _mk_released_route(db)
    link.released = False
    db.commit()
    assert resolve_released_route(db, _case(db)) is None


def test_released_route_resolves(db):
    from app.portal.released_flow import resolve_released_route
    _mk_released_route(db)
    released = resolve_released_route(db, _case(db))
    assert released is not None
    assert released.route_key == RK
    assert released.family.account_required is False


def test_quarantined_version_fails_closed(db):
    from app.portal.released_flow import resolve_released_route
    cand, ver, link = _mk_released_route(db)
    ver.quarantined = True
    db.commit()
    assert resolve_released_route(db, _case(db)) is None


def test_real_only_start_without_release_still_409s(db, client, real_only):
    """The screenshot error stays for genuinely unreleased routes."""
    from tests.conftest import AUTH
    app_row = _case(db)
    r = client.post(f"/cases/{app_row.id}/start", headers=AUTH)
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "real_only_stop"


def test_released_adapter_classifies_live_production(db, real_only):
    from app.portal.released_flow import build_for_case
    from app.execution import classify_adapter, ExecutionClass
    _mk_released_route(db)
    built = build_for_case(db, _case(db))
    assert built is not None
    portal, adapter = built
    assert adapter.channel == "released_flow"
    assert adapter.account_required is False
    assert classify_adapter(adapter) == ExecutionClass.LIVE_PRODUCTION


# ---- FlowRunner: dynamic questions + uploads + fee -------------------------

def _runner(db, app_row, cand, ver, answers, documents=None, page=None):
    from app.adapter_factory.compiler import compile_flow
    from app.adapter_factory.live_driver import BrowserbasePageDriver
    execution = fm.AdapterExecution(org_id="org1", application_id=app_row.id,
                                    candidate_id=cand.id, candidate_version=1,
                                    tier="sandbox", status="running")
    db.add(execution)
    db.commit()
    driver = BrowserbasePageDriver(page or FakePage(), allowed_hostnames=[HOST])
    return execution, FlowRunner(db, execution=execution, compiled=compile_flow(ver),
                                 driver=driver, case_answers=answers,
                                 documents=documents or [])


def test_missing_answer_pauses_with_all_questions(db):
    cand, ver, _ = _mk_released_route(db)
    app_row = _case(db)
    execution, runner = _runner(db, app_row, cand, ver,
                                {"surname": "TESTER"})
    res = runner.run()
    assert res["status"] == "paused_applicant_action"
    assert res["handoff_kind"] == "additional_information"
    keys = [q["key"] for q in res["questions"]]
    # religion (missing), entry checkpoint (missing), passport doc (missing) —
    # asked at once, stopping AT the first missing node.
    assert keys[0] == "religion"
    assert "entry_checkpoint" in keys
    assert any(k.startswith("document:") for k in keys)
    q = res["questions"][0]
    assert "selector" not in q and "#" not in q["question"]
    assert q["mandatory"] is True
    assert execution.current_node == "fill_religion"


def test_answering_resumes_same_execution_and_node(db, tmp_path):
    cand, ver, _ = _mk_released_route(db)
    app_row = _case(db)
    page = FakePage()
    execution, runner = _runner(db, app_row, cand, ver, {"surname": "TESTER"},
                                page=page)
    assert runner.run()["status"] == "paused_applicant_action"
    doc = tmp_path / "passport.jpg"
    doc.write_bytes(b"fake-jpeg-bytes")
    execution2, runner2 = None, None
    # resume: same execution row, answers now complete, document present
    from app.adapter_factory.compiler import compile_flow
    from app.adapter_factory.live_driver import BrowserbasePageDriver
    runner2 = FlowRunner(db, execution=execution, compiled=compile_flow(ver),
                         driver=BrowserbasePageDriver(page, allowed_hostnames=[HOST]),
                         case_answers={"surname": "TESTER", "religion": "None",
                                       "entry_checkpoint": "Noi Bai Int Airport"},
                         documents=[{"doc_type": "passport", "name": "p.jpg",
                                     "mime": "image/jpeg", "path": str(doc)}])
    res = runner2.run()
    # advanced through religion + select + upload, now paused at the captcha
    assert res["status"] == "paused_applicant_action"
    assert res["handoff_kind"] == "captcha"
    assert page.filled["#basic_ttcnTonGiao"] == "None"
    assert page.uploaded["#basic_anhHoChieu"] == b"fake-jpeg-bytes"
    only = db.execute(select(fm.AdapterExecution).where(
        fm.AdapterExecution.application_id == app_row.id)).scalars().all()
    assert len(only) == 1     # never a duplicate application/execution


def test_optional_missing_answer_is_skipped(db):
    cand, ver, _ = _mk_released_route(db)
    flow = _flow()
    for n in flow:
        if n["node_id"] == "fill_religion":
            n["mandatory"] = False
    ver.flow = flow
    db.commit()
    app_row = _case(db)
    _, runner = _runner(db, app_row, cand, ver, {"surname": "TESTER"})
    res = runner.run()
    # religion skipped; next missing mandatory is the entry checkpoint select
    assert res["status"] == "paused_applicant_action"
    assert res["questions"][0]["key"] == "entry_checkpoint"


def test_fee_parse_exact_or_refuse():
    assert parse_fee_text("25 USD") == {"text": "25 USD", "amount_cents": 2500,
                                        "currency": "USD"}
    assert parse_fee_text("USD 25.00")["amount_cents"] == 2500
    assert parse_fee_text("$50")["amount_cents"] == 5000
    assert parse_fee_text("800,000 VND")["amount_cents"] == 80000000
    assert parse_fee_text("") is None
    assert parse_fee_text("Fee: 25 or 50 USD") is None      # ambiguous
    assert parse_fee_text("no amount here") is None


# ---- provide_information signal (API validation) ---------------------------

def test_provide_information_accepts_only_asked_keys(db, client):
    from tests.conftest import AUTH
    app_row = _case(db)
    exec_row = models.WorkflowExecution(
        application_id=app_row.id, state="APPLICATION_FILLING",
        pending={"state": "APPLICATION_FILLING", "handoff": "additional_information",
                 "questions": [{"key": "religion", "question": "What is your religion?",
                                "kind": "text", "mandatory": True},
                               {"key": "arrival", "question": "Arrival date?",
                                "kind": "date", "mandatory": True}]})
    db.add(exec_row)
    db.commit()
    r = client.post(f"/cases/{app_row.id}/signals/provide_information",
                    headers=AUTH,
                    json={"answers": {"religion": " None ",
                                      "arrival": "08/01/2026",
                                      "unasked_key": "sneaky",
                                      "document:passport": "x"}})
    # The workflow itself can't drive (no released binding in mock mode is fine
    # for THIS assertion) — what matters is what got persisted.
    db.refresh(app_row)
    assert app_row.answers.get("religion") == "None"
    assert app_row.answers.get("arrival") == "2026-08-01"    # canonical ISO
    assert "unasked_key" not in app_row.answers
    assert "document:passport" not in app_row.answers


def test_provide_information_rejects_bad_date(db, client):
    from tests.conftest import AUTH
    app_row = _case(db)
    db.add(models.WorkflowExecution(
        application_id=app_row.id, state="APPLICATION_FILLING",
        pending={"handoff": "additional_information",
                 "questions": [{"key": "arrival", "kind": "date",
                                "question": "Arrival date?"}]}))
    db.commit()
    r = client.post(f"/cases/{app_row.id}/signals/provide_information",
                    headers=AUTH, json={"answers": {"arrival": "not a date"}})
    assert r.status_code == 422
    assert r.json()["detail"]["reason"] == "invalid_answer"


# ---- personal gate: deterministic no-admin readiness -----------------------

def test_deterministic_release_completes_gates(db):
    from app.personal_gate import readiness
    _mk_released_route(db)
    rep = readiness(db, destination="Vietnam", visa_type="tourist",
                    nationality="CHN", residence="CHN", include_evidence=True)
    assert rep["missing_gates"] == []
    assert rep["route_approved_for_live"] is True
    ev = rep["gates"]["admin_approval_recorded"]["evidence"]
    assert "deterministic release" in ev
    assert rep["gates"]["admin_approval_recorded"]["by"] == "deterministic-release-engine"


def test_unreleased_route_keeps_gates_missing(db):
    from app.personal_gate import readiness
    rep = readiness(db, destination="Vietnam", visa_type="tourist",
                    nationality="CHN", residence="CHN")
    assert len(rep["missing_gates"]) == 15


def test_failed_gate_report_derives_nothing(db):
    from app.personal_gate import readiness
    cand, ver, link = _mk_released_route(db)
    report = dict(link.gate_report)
    report["passed"] = False
    link.gate_report = report
    db.commit()
    rep = readiness(db, destination="Vietnam", visa_type="tourist",
                    nationality="CHN", residence="CHN")
    assert len(rep["missing_gates"]) == 15


# ---------- known_missing_questions: pre-run prompt, no browser --------------

def test_known_missing_questions_come_from_stored_flow_without_a_browser(db):
    """The applicant is asked for known form fields when the CASE PAGE opens —
    computed purely from the released flow's stored nodes (measured 2026-07-28:
    discovering them via the live portal took ~65s from the start click)."""
    _mk_released_route(db)
    app_row = _case(db)
    qs = released_flow.known_missing_questions(db, app_row)
    keys = [q["key"] for q in qs]
    # surname is already answered; religion + entry_checkpoint are not.
    assert keys == ["religion", "entry_checkpoint"]
    religion = qs[0]
    assert religion["question"] == "What is your religion?"
    assert religion["mandatory"] is True and religion["kind"] == "text"
    assert qs[1]["kind"] == "select"
    # Applicant-facing payload: never selectors or element ids.
    import json as _json
    dumped = _json.dumps(qs)
    assert "basic_" not in dumped and "#" not in dumped
    # Once answered, the question disappears.
    app_row.answers = dict(app_row.answers, religion="None",
                           entry_checkpoint="Noi Bai")
    db.commit()
    assert released_flow.known_missing_questions(db, app_row) == []


def test_known_missing_questions_fail_closed_without_a_released_route(db):
    app_row = _case(db)          # no released chain rows exist for this pair
    app_row.answers = dict(app_row.answers, passport_nationality="SWE",
                           nationality="SWE")
    db.commit()
    assert released_flow.known_missing_questions(db, app_row) == []


def test_option_less_select_is_honest_not_a_fake_choose_from_list(db):
    """A select whose real list Ellis has not read yet must NOT be presented
    as "choose from list" over a free-text box. It says so honestly and the
    fill step verifies the typed answer against the live page."""
    _mk_released_route(db)
    app_row = _case(db)
    qs = {q["key"]: q for q in released_flow.known_missing_questions(db, app_row)}
    entry = qs["entry_checkpoint"]
    assert entry["kind"] == "select"
    assert "options" not in entry
    assert entry["options_pending"] is True
    assert entry["format"] == ""          # never a misleading placeholder


def test_harvested_options_become_a_real_dropdown_for_the_next_applicant(db):
    """What the portal actually offered on one run is asked as a REAL dropdown
    before the next applicant's browser session opens — the whole point of the
    pre-run questions. Corroboration first: a list seen only ONCE may be
    specific to that applicant, so it is not re-served until it repeats."""
    cand, ver, link = _mk_released_route(db)
    app_row = _case(db)
    gates = ["Noi Bai Intl Airport", "Tan Son Nhat Intl Airport"]

    def ask():
        return {q["key"]: q
                for q in released_flow.known_missing_questions(db, app_row)}

    released_flow.remember_field_options(
        db, candidate_id=cand.id, candidate_version=1,
        field_key="entry_checkpoint", node_id="pick_entry_gate",
        options=gates, complete=True)
    assert "options" not in ask()["entry_checkpoint"]      # seen once: not yet
    released_flow.remember_field_options(
        db, candidate_id=cand.id, candidate_version=1,
        field_key="entry_checkpoint", node_id="pick_entry_gate",
        options=gates, complete=True)
    entry = ask()["entry_checkpoint"]                       # same list twice
    assert entry["options"] == gates
    assert "options_pending" not in entry and "options_partial" not in entry

    # A DIFFERENT adapter version never inherits the list — it re-reads the
    # portal rather than trusting a form that may have changed.
    from app.adapter_factory import models as fm
    binding = db.execute(select(fm.AdapterRuntimeBinding).where(
        fm.AdapterRuntimeBinding.candidate_id == cand.id)).scalars().first()
    binding.candidate_version = 2
    db.add(fm.AdapterCandidateVersion(
        candidate_id=cand.id, version=2, manifest=ver.manifest, flow=ver.flow,
        field_mappings=[], document_mappings=[],
        evidence_rules={"banner_text_sufficient": False}, kill_switch_key="ks2"))
    db.commit()
    assert "options" not in ask()["entry_checkpoint"]
    assert ask()["entry_checkpoint"]["options_pending"] is True


def test_a_truncated_list_is_offered_as_suggestions_never_as_the_whole_list(db):
    """Vietnam's portal offers 83 border gates behind a virtualized dropdown;
    a mid-run read that reaches only the first rows must NEVER be presented as
    the portal's whole list — a traveller whose real gate sits past those rows
    would have no way to enter it. It is offered as suggestions on a field they
    can still type into."""
    cand, _v, _l = _mk_released_route(db)
    app_row = _case(db)
    partial = ["An Thoi Port Border Gate", "Ben Luc Port Border Gate"]
    for _ in range(2):        # corroborated, but still an INCOMPLETE read
        released_flow.remember_field_options(
            db, candidate_id=cand.id, candidate_version=1,
            field_key="entry_checkpoint", node_id="pick_entry_gate",
            options=partial, complete=False)
    entry = {q["key"]: q
             for q in released_flow.known_missing_questions(db, app_row)}["entry_checkpoint"]
    assert entry["options"] == partial
    assert entry["options_partial"] is True
    assert entry["format"] == ""      # never "choose from list"


def test_a_longer_read_supersedes_a_truncated_one(db):
    cand, _v, _l = _mk_released_route(db)
    app_row = _case(db)
    released_flow.remember_field_options(
        db, candidate_id=cand.id, candidate_version=1,
        field_key="entry_checkpoint", node_id="pick_entry_gate",
        options=["A", "B"], complete=False)
    full = [f"Gate {i}" for i in range(30)]     # 25+ is trusted on sight
    released_flow.remember_field_options(
        db, candidate_id=cand.id, candidate_version=1,
        field_key="entry_checkpoint", node_id="pick_entry_gate",
        options=full, complete=True)
    entry = {q["key"]: q
             for q in released_flow.known_missing_questions(db, app_row)}["entry_checkpoint"]
    assert entry["options"] == full
    assert "options_partial" not in entry


def test_cached_options_are_trimmed_and_carry_no_selectors(db):
    cand, _v, _l = _mk_released_route(db)
    app_row = _case(db)
    raw = ["  Noi Bai Intl Airport  ", "", "   ", "Da Nang Intl Airport"]
    clean = ["Noi Bai Intl Airport", "Da Nang Intl Airport"]
    for _ in range(2):
        released_flow.remember_field_options(
            db, candidate_id=cand.id, candidate_version=1,
            field_key="entry_checkpoint", node_id="pick_entry_gate",
            options=raw, complete=True)
    entry = {q["key"]: q
             for q in released_flow.known_missing_questions(db, app_row)}["entry_checkpoint"]
    assert entry["options"] == clean
    import json as _json
    assert "#" not in _json.dumps(entry)

    # A blank-only harvest never wipes a good list.
    released_flow.remember_field_options(
        db, candidate_id=cand.id, candidate_version=1,
        field_key="entry_checkpoint", node_id="pick_entry_gate",
        options=["", "  "], complete=True)
    entry2 = {q["key"]: q
              for q in released_flow.known_missing_questions(db, app_row)}["entry_checkpoint"]
    assert entry2["options"] == clean

    # An empty/blank-only harvest never overwrites a good list with nothing.
    released_flow.remember_field_options(
        db, candidate_id=cand.id, candidate_version=1,
        field_key="entry_checkpoint", options=["", "  "])
    qs2 = {q["key"]: q for q in released_flow.known_missing_questions(db, app_row)}
    assert qs2["entry_checkpoint"]["options"] == [
        "Noi Bai Intl Airport", "Da Nang Intl Airport"]


# ---------- the pre-flight gate: ask FIRST, fill second ----------------------
# Thailand, 2026-08-03: a run reached the government form, typed five fields,
# and stalled on a mandatory Occupation the applicant had never been asked
# for. Every question the released flow already knows about belongs in ONE
# prompt before a keystroke reaches the portal.

def test_the_driver_lists_its_known_questions_without_opening_a_browser(db):
    _mk_released_route(db)
    app_row = _case(db)
    released = released_flow.resolve_released_route(db, app_row)
    drv = released_flow.ReleasedFlowDriver(db, app_row=app_row, released=released)
    qs = drv.preflight_questions()
    assert [q["key"] for q in qs] == ["religion", "entry_checkpoint"]
    # No session was ever created — this is a read of the stored flow.
    assert drv._session is None and drv._page_driver is None


def test_preflight_reads_the_answers_the_workflow_holds_now(db):
    """Answers provided in THIS drive have not been written to the case row
    yet. Reading only the row would re-ask a question just answered and loop
    the applicant through the same prompt."""
    _mk_released_route(db)
    app_row = _case(db)
    released = released_flow.resolve_released_route(db, app_row)
    drv = released_flow.ReleasedFlowDriver(db, app_row=app_row, released=released)
    qs = drv.preflight_questions({"religion": "None",
                                  "entry_checkpoint": "Noi Bai Intl Airport"})
    assert qs == []
    # The case row itself is untouched by a read.
    assert "religion" not in (app_row.answers or {})


def test_preflight_asks_only_what_the_portal_demands(db):
    """An optional field is not worth interrupting anyone for — the portal
    accepts the form without it."""
    _mk_released_route(db)
    app_row = _case(db)
    released = released_flow.resolve_released_route(db, app_row)
    ver = released.version_row
    ver.flow = [dict(n, mandatory=False) if n.get("input_source") == "religion"
                else n for n in ver.flow]
    db.commit()
    drv = released_flow.ReleasedFlowDriver(db, app_row=app_row, released=released)
    assert [q["key"] for q in drv.preflight_questions()] == ["entry_checkpoint"]
