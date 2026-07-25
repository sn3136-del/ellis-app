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
        # the driver's read-back: echo the typed value as the shown selection
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

    def evaluate(self, *_a, **_k):
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
    pol = RoutePairPolicy(snapshot_date="2026-07-23",
                          pair_key=pair_key("CHN", "ordinary_passport", "VNM"),
                          passport_nationality="CHN",
                          travel_document_type="ordinary_passport",
                          destination_country="VNM",
                          disposition="EVISA_REQUIRED", route_outcome="EVISA",
                          primary_category="evisa_tourist",
                          portal_family_id=fam_id, source="official_research",
                          verification_status="verified",
                          release_status="released_adapter")
    db.add(pol)
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
