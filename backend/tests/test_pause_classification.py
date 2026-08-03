"""Pause classification + applicant-provided payment details.

The bug this pins: a run that reached the fee boundary while the LIVE page
still showed unanswered form questions (or a CAPTCHA) used to pause as
"confirm the fee" — the applicant was sent to the wrong step. Now the driver
classifies the page and the workflow pauses as what the page actually needs:
in-Ellis questions (Ellis fills the answers back and clicks Next), a CAPTCHA
handoff, or — only when the page is genuinely quiet — the honest
fee-confirmation fallback.

Payment redesign: the applicant provides card details IN ELLIS
(provide_payment_details), Ellis fills the official portal's payment form,
and the applicant confirms the payment personally in the secure window. Card
data follows OTP hygiene end to end: vault-transported, revealed once,
destroyed, never persisted or echoed.
"""
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from tests.conftest import AUTH
from app import models, portal_queue, progress, service, vault
from tests.test_background_execution import (_confirm_contact, _fee_wf,
                                             _new_case, live_route)  # noqa: F401

FEE = {"ok": True, "amount": 2500, "currency": "USD", "display": "25.00 USD",
       "government_fee_cents": 2500, "service_fee_cents": 0, "payee": "Portal"}
CARD = {"holder": "TEST APPLICANT", "number": "4111111111111111",
        "cvv": "123", "expiry_month": "12", "expiry_year": "2033"}


# ---------- fee-boundary classification (why did we really pause?) -----------

def test_page_questions_at_fee_boundary_pause_as_questions_not_fee():
    class QuestionsOnPage:
        def discover_fee(self, **_kw):
            return {"ok": False, "code": "ADDITIONAL_INFORMATION_REQUIRED",
                    "questions": [{"key": "religion", "question": "What is your religion?",
                                   "why": "The official form still requires this on the current page.",
                                   "format": "", "mandatory": True, "kind": "select",
                                   "options": ["None", "Buddhism"]}],
                    "page_field_map": {"religion": {"selector": "#basic_religion",
                                                    "kind": "combobox"}}}

        def detach(self):
            pass

    wf = _fee_wf(QuestionsOnPage(), "FEE_DISCOVERY_PENDING")
    wf.start()
    assert wf.machine.state == "FEE_DISCOVERY_PENDING"      # never a fee pause
    assert wf.pending["handoff"] == "additional_information"
    assert [q["key"] for q in wf.pending["questions"]] == ["religion"]
    # The selector map is server-side state (snapshot), NEVER applicant-facing.
    assert "#basic_religion" not in json.dumps(wf.pending)
    assert wf._page_field_map == {"religion": {"selector": "#basic_religion",
                                               "kind": "combobox"}}
    assert wf.snapshot()["_page_field_map"] == wf._page_field_map


def test_page_answers_flow_back_and_fee_discovery_continues():
    class RecordsPageFill:
        def __init__(self):
            self.page_fills = []

        def discover_fee(self, page_fill=None, **_kw):
            self.page_fills.append(page_fill)
            if page_fill:                     # answers applied -> fee readable
                return dict(FEE)
            return {"ok": False, "code": "ADDITIONAL_INFORMATION_REQUIRED",
                    "questions": [{"key": "religion", "question": "What is your religion?",
                                   "mandatory": True, "kind": "text"}],
                    "page_field_map": {"religion": {"selector": "#basic_religion",
                                                    "kind": "combobox"}}}

        def detach(self):
            pass

    driver = RecordsPageFill()
    wf = _fee_wf(driver, "FEE_DISCOVERY_PENDING")
    wf.start()
    assert wf.pending["handoff"] == "additional_information"
    wf.provide_information({"religion": "None"})
    # Ellis filled the applicant's answer back onto the live page…
    assert driver.page_fills[-1] == [{"selector": "#basic_religion",
                                      "kind": "combobox", "value": "None"}]
    # …and fee discovery completed: exact-amount approval, map cleared.
    assert wf.machine.state == "PAYMENT_APPROVAL_REQUIRED"
    assert wf.pending["handoff"] == "payment_approval"
    assert wf.fee["amount"] == 2500
    assert wf._page_field_map is None


def test_captcha_at_fee_boundary_pauses_as_captcha_and_returns():
    class CaptchaThenFee:
        def __init__(self):
            self.solved = False

        def discover_fee(self, **_kw):
            return dict(FEE) if self.solved else \
                {"ok": False, "code": "CAPTCHA_REQUIRED"}

        def submit_captcha(self, **_kw):
            self.solved = True
            return {"ok": True}

        def detach(self):
            pass

    wf = _fee_wf(CaptchaThenFee(), "FEE_DISCOVERY_PENDING")
    wf.start()
    assert wf.machine.state == "CAPTCHA_ACTION_REQUIRED"
    assert wf.pending["handoff"] == "captcha"
    assert wf._captcha_return_state == "FEE_DISCOVERY_PENDING"
    wf.solve_captcha()
    assert wf.machine.state == "PAYMENT_APPROVAL_REQUIRED"
    assert wf.pending["handoff"] == "payment_approval"


def test_unsolved_observed_captcha_reasks_instead_of_failing():
    class StillThere:
        def discover_fee(self, **_kw):
            return {"ok": False, "code": "CAPTCHA_REQUIRED"}

        def submit_captcha(self, **_kw):
            return {"ok": False, "code": "CAPTCHA_STILL_PRESENT"}

        def detach(self):
            pass

    wf = _fee_wf(StillThere(), "FEE_DISCOVERY_PENDING")
    wf.start()
    wf.solve_captcha()
    assert wf.machine.state == "CAPTCHA_ACTION_REQUIRED"    # not a failure
    assert wf.pending["handoff"] == "captcha"


# ---------- reclassifying an already-recorded (mislabeled) fee pause ---------

def _stuck_fee_pause(driver):
    wf = _fee_wf(driver, "PAYMENT_APPROVAL_REQUIRED")
    wf.pending = {"state": "PAYMENT_APPROVAL_REQUIRED",
                  "reason": "Confirm the exact fee shown by the official portal.",
                  "handoff": "fee_confirmation", "live_view": None}
    return wf


def test_read_fee_downgrades_mislabeled_fee_pause_to_questions():
    class NoFeeButQuestions:
        def read_current_fee(self, **_kw):
            return {"ok": False, "code": "FEE_NOT_DISPLAYED",
                    "blockers": {"questions": [{"key": "religion",
                                                "question": "What is your religion?",
                                                "mandatory": True, "kind": "text"}],
                                 "page_field_map": {"religion": {
                                     "selector": "#basic_religion", "kind": "text"}}}}

        def detach(self):
            pass

    wf = _stuck_fee_pause(NoFeeButQuestions())
    wf.read_fee()
    assert wf.machine.state == "FEE_DISCOVERY_PENDING"
    assert wf.pending["handoff"] == "additional_information"
    assert [q["key"] for q in wf.pending["questions"]] == ["religion"]
    assert "#basic_religion" not in json.dumps(wf.pending)


def test_read_fee_downgrades_mislabeled_fee_pause_to_declaration():
    # The exact live bug: the run parked at "confirm the fee" while the portal
    # page still showed only its own declaration checkbox. A page re-read
    # reclassifies to the in-Ellis declaration step.
    class NoFeeButDeclaration:
        def read_current_fee(self, **_kw):
            return {"ok": False, "code": "FEE_NOT_DISPLAYED",
                    "blockers": {"declaration": ["I hereby declare the above is true"],
                                 "declaration_map": {"selectors": ['[id="agree"]'],
                                                     "label": "I hereby declare the above is true"}}}

        def detach(self):
            pass

    wf = _stuck_fee_pause(NoFeeButDeclaration())
    wf.read_fee()
    assert wf.machine.state == "FEE_DISCOVERY_PENDING"
    assert wf.pending["handoff"] == "personal_declaration"
    assert wf.pending["portal_messages"] == ["I hereby declare the above is true"]
    assert wf._page_declaration == {"selectors": ['[id="agree"]'],
                                    "label": "I hereby declare the above is true"}
    assert '[id="agree"]' not in json.dumps(wf.pending)   # selector server-side only


def test_read_fee_downgrades_mislabeled_fee_pause_to_captcha():
    class NoFeeButCaptcha:
        def read_current_fee(self, **_kw):
            return {"ok": False, "code": "FEE_NOT_DISPLAYED",
                    "blockers": {"captcha": True}}

        def detach(self):
            pass

    wf = _stuck_fee_pause(NoFeeButCaptcha())
    wf.read_fee()
    assert wf.machine.state == "CAPTCHA_ACTION_REQUIRED"
    assert wf.pending["handoff"] == "captcha"
    assert wf._captcha_return_state == "FEE_DISCOVERY_PENDING"


def test_read_fee_with_quiet_page_keeps_the_honest_fee_pause():
    class QuietPage:
        def read_current_fee(self, **_kw):
            return {"ok": False, "code": "FEE_NOT_DISPLAYED"}

        def detach(self):
            pass

    wf = _stuck_fee_pause(QuietPage())
    wf.read_fee()
    assert wf.machine.state == "PAYMENT_APPROVAL_REQUIRED"
    assert wf.pending["handoff"] == "fee_confirmation"


def test_restore_downgrades_mislabeled_fee_pause_to_questions():
    class RestoreShowsQuestions:
        def restore_view(self, **_kw):
            return {"ok": True,
                    "blockers": {"questions": [{"key": "entry_date",
                                                "question": "What is your entry date?",
                                                "mandatory": True, "kind": "date"}],
                                 "page_field_map": {"entry_date": {
                                     "selector": "#entry_date", "kind": "text"}}}}

        def detach(self):
            pass

    wf = _stuck_fee_pause(RestoreShowsQuestions())
    wf.restore_portal()
    assert wf.machine.state == "FEE_DISCOVERY_PENDING"
    assert wf.pending["handoff"] == "additional_information"


# ---------- applicant-provided payment details --------------------------------

class _CardFiller:
    def __init__(self, ok=True):
        self.ok = ok
        self.cards = []

    def fill_payment_details(self, card=None, **_kw):
        self.cards.append(dict(card or {}))
        if not self.ok:
            return {"ok": False, "code": "PAYMENT_FORM_NOT_FOUND"}
        return {"ok": True, "filled": ["cvv", "expiry", "holder", "number"]}

    def get_application_state(self, **_kw):
        return {"ok": True, "paid": False, "submitted": False}

    def pay(self, **_kw):
        return {"ok": True, "receipt": {"receiptNo": "R-1"}}

    def detach(self):
        pass


def _payment_wf(driver):
    wf = _fee_wf(driver, "PAYMENT_ACTION_REQUIRED", fee=dict(FEE),
                 payment_authorization={"amount_cents": 2500, "currency": "USD",
                                        "payee": "Portal", "status": "authorized"})
    wf.block_submit = True
    wf.inputs["payment_approved"] = True
    return wf


def test_payment_details_collected_in_ellis_filled_by_ellis_confirmed_by_applicant():
    driver = _CardFiller()
    wf = _payment_wf(driver)
    wf.start()
    # 1) Ellis asks for the details in its own UI, not the live window.
    assert wf.pending["handoff"] == "payment_credentials"
    assert wf.pending["fee"]["amount"] == 2500
    # 2) Ellis fills them on the portal — used once, never persisted.
    wf.provide_payment_details(card=dict(CARD))
    assert driver.cards == [CARD]
    assert wf.pending["handoff"] == "payment"
    assert wf.pending["payment_prefilled"] is True
    assert CARD["number"] not in json.dumps(wf.snapshot())
    assert CARD["cvv"] not in json.dumps(wf.inputs)
    # 3) The applicant's own confirmation click completes the payment.
    wf.complete_payment()
    assert wf.receipt == {"receiptNo": "R-1"}
    assert wf.machine.state in ("PAYMENT_COMPLETED", "FINAL_REVIEW_REQUIRED",
                                "READY_TO_SUBMIT")


def test_payment_details_manual_choice_goes_straight_to_secure_window():
    driver = _CardFiller()
    wf = _payment_wf(driver)
    wf.start()
    wf.provide_payment_details(manual=True)
    assert driver.cards == []                    # nothing ever sent to the page
    assert wf.pending["handoff"] == "payment"
    assert wf.pending["payment_prefilled"] is False


def test_payment_fill_failure_falls_back_to_secure_window_honestly():
    driver = _CardFiller(ok=False)
    wf = _payment_wf(driver)
    wf.start()
    wf.provide_payment_details(card=dict(CARD))
    assert wf.pending["handoff"] == "payment"
    assert wf.pending["payment_prefilled"] is False


def test_driver_without_payment_fill_keeps_the_old_window_flow():
    class WindowOnly:
        def detach(self):
            pass

    wf = _payment_wf(WindowOnly())
    wf.start()
    assert wf.pending["handoff"] == "payment"    # mock/legacy routes unchanged
    assert wf.pending["payment_prefilled"] is False


# ---------- transport hygiene: card follows the OTP vault pattern -------------

def test_card_is_vaulted_in_queue_revealed_once_and_destroyed(
        client, live_route, db, monkeypatch):  # noqa: F811
    case = _new_case(client)
    r = client.post(f"/cases/{case['id']}/signals/provide_payment_details",
                    headers=AUTH,
                    json={"card": {"holder": "TEST APPLICANT",
                                   "number": "4111 1111 1111 1111",
                                   "expiry": "12/33", "cvv": "123"}})
    assert r.status_code == 200 and r.json()["queued"] is True, r.text
    run = db.get(models.PortalRun, r.json()["run_id"])
    assert "card" not in (run.signal_kwargs or {})
    ref = run.signal_kwargs.get("card_ref")
    assert ref and ref.startswith("vault://")
    assert "4111" not in str(run.signal_kwargs)
    # The audit trail records THAT details were provided, never the values.
    events = client.get(f"/cases/{case['id']}/audit", headers=AUTH).json()["events"]
    assert any(e["action"] == "payment_details_provided" for e in events)
    assert "4111" not in json.dumps(events)
    captured = {}

    def fake_signal(db_, app_id, name, **kw):
        captured.update(kw, signal=name)
        exec_row = db_.execute(select(models.WorkflowExecution).where(
            models.WorkflowExecution.application_id == app_id)).scalar_one_or_none()
        return ({"case_id": app_id, "state": "DRAFT",
                 "pending": exec_row.pending if exec_row else None}, None)

    monkeypatch.setattr(service, "signal", fake_signal)
    portal_queue.run_pending_once("test-exec")
    assert captured["signal"] == "provide_payment_details"
    assert captured["card"]["number"] == "4111111111111111"   # revealed exactly once
    assert captured["card"]["expiry_month"] == "12"
    assert captured["card"]["expiry_year"] == "2033"
    with pytest.raises(KeyError):
        vault.reveal(ref)                        # destroyed immediately after use


@pytest.mark.parametrize("card,bad_key", [
    ({"holder": "T", "number": "4111111111111112", "expiry": "12/33", "cvv": "123"}, "number"),
    ({"holder": "T", "number": "4111111111111111", "expiry": "13/33", "cvv": "123"}, "expiry"),
    ({"holder": "T", "number": "4111111111111111", "expiry": "12/20", "cvv": "123"}, "expiry"),
    ({"holder": "T", "number": "4111111111111111", "expiry": "12/33", "cvv": "12"}, "cvv"),
    ({"holder": "", "number": "4111111111111111", "expiry": "12/33", "cvv": "123"}, "holder"),
])
def test_invalid_card_is_refused_and_never_echoed(client, live_route, card, bad_key):  # noqa: F811
    case = _new_case(client)
    r = client.post(f"/cases/{case['id']}/signals/provide_payment_details",
                    headers=AUTH, json={"card": card})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["reason"] == "invalid_payment_details"
    assert detail["key"] == bad_key
    assert card["number"] not in r.text          # values never echo back


# ---------- page-truth classification in the released-flow driver -------------

class _FakePage:
    def __init__(self, fields, captcha=False):
        self.fields = fields
        self.captcha = captcha
        self.validation = {"ok": True, "messages": [], "fields": []}

    def captcha_state(self, highlight=False):
        return {"ok": True, "present": self.captcha}

    def read_form_state(self):
        return {"ok": True, "fields": self.fields}

    def read_validation_errors(self):
        return self.validation

    def list_options(self, selector, max_options=300):
        return {"ok": True, "options": ["Buddhism", "None"]}


def _released_driver(page, nodes=None, answers=None):
    from app.portal.released_flow import ReleasedFlowDriver
    drv = object.__new__(ReleasedFlowDriver)
    drv._ensure_live = lambda: page
    drv._flow = lambda: SimpleNamespace(nodes=nodes or {}, order=list((nodes or {})))
    drv.app_row = SimpleNamespace(answers=answers or {})
    drv._progress_sink = None
    return drv


def test_passport_type_classifies_as_a_refill_from_the_intake_answer():
    # The REAL eVisa field (no flow node, no stored "type" answer, but intake
    # holds travel_document_type). It must classify as a silent REFILL Ellis
    # fills itself — never a question, and never a secure-window chore.
    page = _FakePage([
        {"id": "basic_hcLoai", "name": "hcLoai", "label": "Type",
         "type": "combobox", "tag": "input",
         "required": True, "empty": True, "sensitive": False},
    ])
    drv = _released_driver(page, answers={"travel_document_type": "ordinary_passport"})
    blockers = drv._page_blockers()
    assert not blockers.get("questions"), "a held answer is never asked"
    refills = blockers.get("refill") or []
    assert len(refills) == 1
    assert refills[0]["value"] == "Ordinary passport"   # the portal's own wording
    assert refills[0]["selector"] == '[id="basic_hcLoai"]'


def test_passport_type_without_an_intake_answer_is_asked_in_ellis():
    # No held answer -> a mandatory in-Ellis question (with the portal's own
    # option list), not a portal_form hand-off.
    page = _FakePage([
        {"id": "basic_hcLoai", "name": "hcLoai", "label": "Type",
         "type": "combobox", "tag": "input",
         "required": True, "empty": True, "sensitive": False},
    ])
    drv = _released_driver(page, answers={})
    blockers = drv._page_blockers()
    assert not blockers.get("refill")
    qs = blockers.get("questions") or []
    assert len(qs) == 1 and qs[0]["mandatory"] is True
    assert "selector" not in json.dumps(qs)            # never leaks selectors


def test_page_blockers_build_applicant_questions_without_selectors():
    fields = [
        # unmapped required empty combobox -> asked, options harvested
        {"id": "basic_religion", "name": "religion", "label": "Religion *",
         "type": "combobox", "tag": "input",
         "required": True, "empty": True, "sensitive": False},
        # flow-mapped required empty field with NO stored answer -> asked by flow key
        {"id": "basic_surname", "name": "surname", "label": "Surname",
         "type": "text", "tag": "input",
         "required": True, "empty": True, "sensitive": False},
        # already answered in the case -> silent refill entry, never a re-ask
        {"id": "basic_email", "name": "email", "label": "Email",
         "type": "text", "tag": "input",
         "required": True, "empty": True, "sensitive": False},
        # optional, filled, sensitive fields are never asked
        {"id": "opt", "name": "opt", "label": "Optional", "type": "text",
         "tag": "input", "required": False, "empty": True, "sensitive": False},
        {"id": "done", "name": "done", "label": "Done", "type": "text",
         "tag": "input", "required": True, "empty": False, "sensitive": False},
        {"id": "cardno", "name": "card_number", "label": "Card number",
         "type": "text", "tag": "input",
         "required": True, "empty": True, "sensitive": True},
        # declarations/checkboxes are applicant-personal: portal_form items
        {"id": "agree", "name": "agree", "label": "I declare *",
         "type": "checkbox", "tag": "input",
         "required": True, "empty": True, "sensitive": False},
        {"id": "secans", "name": "answer1", "label": "Security answer",
         "type": "text", "tag": "input",
         "required": True, "empty": True, "sensitive": False},
    ]
    nodes = {
        "n1": {"node_id": "n1", "action": "FILL_NON_SENSITIVE",
               "selector": "#basic_surname", "input_source": "surname",
               "question": {"question": "What is your surname?"}},
        "n2": {"node_id": "n2", "action": "FILL_NON_SENSITIVE",
               "selector": "#basic_email", "input_source": "email"},
    }
    drv = _released_driver(_FakePage(fields), nodes=nodes,
                           answers={"email": "t@example.com"})
    blockers = drv._page_blockers()
    keys = [q["key"] for q in blockers["questions"]]
    # Unmarked-required fields are ASKED too (portals often omit DOM required
    # markers) — but as optional, so the applicant can skip freely.
    assert keys == ["religion", "surname", "optional"]
    religion = blockers["questions"][0]
    assert religion["kind"] == "select"
    assert religion["options"] == ["Buddhism", "None"]
    assert religion["mandatory"] is True
    assert blockers["questions"][2]["mandatory"] is False
    # Attribute-form selectors survive JSF-style ids; tag comes from the page.
    assert blockers["page_field_map"]["religion"] == {"selector": '[id="basic_religion"]',
                                                      "kind": "combobox"}
    assert blockers["page_field_map"]["surname"]["selector"] == '[id="basic_surname"]'
    # The answered field becomes a silent repair, never a duplicate question.
    assert blockers["refill"] == [{"selector": '[id="basic_email"]', "kind": "text",
                                   "value": "t@example.com", "format": ""}]
    # The portal's own declaration checkbox becomes the in-Ellis declaration
    # step (applicant declares, Ellis transcribes); other personal items stay
    # secure-window messages.
    assert blockers["declaration"] == ["I declare"]
    assert blockers["declaration_map"] == {"selectors": ['[id="agree"]'],
                                           "label": "I declare"}
    assert blockers["portal_messages"] == ["Security answer"]
    # The applicant payload carries wording only — selectors stay server-side.
    assert "selector" not in json.dumps(blockers["questions"])
    assert "basic_" not in json.dumps(blockers["questions"])


def test_consent_box_is_not_auto_ticked_but_a_truthfulness_declaration_is():
    fields = [
        # A truthfulness declaration -> in-Ellis declaration (Ellis transcribes)
        {"id": "decl", "name": "decl", "label": "I declare the information is true and complete",
         "type": "checkbox", "tag": "input", "required": True, "empty": True, "sensitive": False},
        # A consent / T&C box worded with "hereby" -> NOT a declaration: it must
        # go to the secure window, never Ellis's auto-tick bundle.
        {"id": "tc", "name": "tc", "label": "I hereby agree to the terms and conditions",
         "type": "checkbox", "tag": "input", "required": True, "empty": True, "sensitive": False},
    ]
    drv = _released_driver(_FakePage(fields))
    blockers = drv._page_blockers()
    assert blockers["declaration"] == ["I declare the information is true and complete"]
    assert blockers["declaration_map"]["selectors"] == ['[id="decl"]']
    assert blockers["portal_messages"] == ["I hereby agree to the terms and conditions"]


def test_page_blockers_ask_radio_groups_as_choice_questions():
    fields = [
        {"id": "ins_yes", "name": "insurance", "label": "Did you buy insurance?",
         "type": "radio", "tag": "input", "options": ["Yes", "No"],
         "required": True, "empty": True, "sensitive": False},
    ]
    drv = _released_driver(_FakePage(fields))
    blockers = drv._page_blockers()
    q = blockers["questions"][0]
    assert q["key"] == "did_you_buy_insurance"
    assert q["kind"] == "select" and q["options"] == ["Yes", "No"]
    # The GROUP is addressed by name — an id would reach only one option.
    assert blockers["page_field_map"]["did_you_buy_insurance"] == {
        "selector": 'input[name="insurance"]', "kind": "radio"}


def test_question_labels_that_are_already_questions_stay_verbatim():
    fields = [
        {"id": "cover", "name": "cover", "label": "Who will cover the trip’s expenses of the applicant",
         "type": "radio", "tag": "input", "options": ["Personal", "Company"],
         "required": False, "empty": True, "sensitive": False},
        {"id": "exp", "name": "exp", "label": "Intended expenses (in USD)",
         "type": "text", "tag": "input",
         "required": False, "empty": True, "sensitive": False},
    ]
    drv = _released_driver(_FakePage(fields))
    qs = {q["key"]: q["question"] for q in drv._page_blockers()["questions"]}
    # Interrogative labels are never wrapped in "What is your …?"
    assert qs["who_will_cover_the_trip_s_expenses_of_th"] == \
        "Who will cover the trip’s expenses of the applicant?"
    assert qs["intended_expenses_in_usd"] == "What is your Intended expenses (in USD)?"


def test_handoff_node_entry_never_reads_as_waiting_for_the_applicant():
    from app.adapter_factory.runtime import FlowRunner

    seen = []
    runner = object.__new__(FlowRunner)
    runner.on_progress = lambda step, status: seen.append((step, status))
    captcha_node = {"node_id": "n1", "action": "APPLICANT_HANDOFF",
                    "handoff_kind": "captcha"}
    # Entering the node = Ellis CHECKING whether a challenge exists — the
    # applicant is not needed yet, so no "waiting_…" message may show.
    runner._progress(captcha_node, "active")
    # Stepping PAST it (no challenge present) must not leave "Waiting for you
    # to complete the CAPTCHA" as the last-completed message either.
    runner._progress(captcha_node, "done")
    # An actual pause keeps the honest waiting message.
    runner._progress(captcha_node, "handoff")
    assert seen == [("checking_form", "active"), ("checking_form", "done"),
                    ("waiting_captcha", "handoff")]


def test_page_blockers_adopt_the_flow_key_when_the_selector_drifted():
    # The page field's id no longer matches the node selector, but the
    # label-derived key equals the node's input_source: the flow key is
    # adopted, so an already-given answer is refilled — never re-asked.
    fields = [
        {"id": "new_religion_id", "name": "", "label": "Religion",
         "type": "text", "tag": "input",
         "required": True, "empty": True, "sensitive": False},
    ]
    nodes = {"n1": {"node_id": "n1", "action": "FILL_NON_SENSITIVE",
                    "selector": "#old_religion_id", "input_source": "religion"}}
    drv = _released_driver(_FakePage(fields), nodes=nodes,
                           answers={"religion": "None"})
    blockers = drv._page_blockers()
    assert "questions" not in blockers
    assert blockers["refill"] == [{"selector": '[id="new_religion_id"]',
                                   "kind": "text", "value": "None", "format": ""}]


def test_page_blockers_derive_date_format_from_placeholder_and_selector_tag():
    fields = [
        {"id": "", "name": "visaType", "label": "Visa type", "type": "select",
         "tag": "select", "required": True, "empty": True, "sensitive": False,
         "options": ["Tourist", "Business"]},
        {"id": "entry", "name": "entry", "label": "Entry date", "type": "text",
         "tag": "input", "placeholder": "DD/MM/YYYY",
         "required": True, "empty": True, "sensitive": False},
    ]
    drv = _released_driver(_FakePage(fields))
    blockers = drv._page_blockers()
    # id-less <select> gets a tag-correct selector — not input[name=...].
    assert blockers["page_field_map"]["visa_type"]["selector"] == 'select[name="visaType"]'
    assert blockers["questions"][0]["options"] == ["Tourist", "Business"]
    # A placeholder that IS a date format is persisted for the fill.
    assert blockers["page_field_map"]["entry_date"]["format"] == "DD/MM/YYYY"


def test_page_blockers_refuse_unbuildable_selectors_and_jargon_names():
    fields = [
        # no id, no name -> unreadable; label-less name-only jargon -> skipped
        {"id": "", "name": "", "label": "Mystery", "type": "text", "tag": "input",
         "required": True, "empty": True, "sensitive": False},
        {"id": "x7", "name": "txtFld7", "label": "", "type": "text", "tag": "input",
         "required": True, "empty": True, "sensitive": False},
    ]
    drv = _released_driver(_FakePage(fields))
    blockers = drv._page_blockers()
    # Nothing askable -> honest portal_form note instead of jargon questions.
    assert "questions" not in blockers
    assert blockers["portal_messages"]


def test_page_blockers_use_one_combined_page_probe():
    class _ProbePage(_FakePage):
        def __init__(self, fields):
            super().__init__(fields)
            self.probes = 0
            self.separate_reads = 0

        def page_probe(self):
            self.probes += 1
            return {"ok": True, "captcha": {"present": False},
                    "fields": self.fields, "validation": self.validation}

        def captcha_state(self, highlight=False):
            self.separate_reads += 1
            return {"ok": True, "present": False}

        def read_form_state(self):
            self.separate_reads += 1
            return {"ok": True, "fields": self.fields}

        def read_validation_errors(self):
            self.separate_reads += 1
            return self.validation

    page = _ProbePage([{"id": "a", "name": "a", "label": "Field A", "type": "text",
                        "tag": "input", "required": True, "empty": True,
                        "sensitive": False}])
    drv = _released_driver(page)
    drv._page_blockers()
    # ONE round-trip, not three (three cost three page waits per pass).
    assert page.probes == 1
    assert page.separate_reads == 0


def test_option_harvesting_is_bounded_per_classification_pass():
    fields = [{"id": f"c{i}", "name": f"c{i}", "label": f"Choice {i}",
               "type": "combobox", "tag": "input", "required": True,
               "empty": True, "sensitive": False} for i in range(6)]
    page = _FakePage(fields)
    calls = []
    page.list_options = lambda selector, max_options=300: (
        calls.append(selector) or {"ok": True, "options": ["X", "Y"]})
    drv = _released_driver(page)
    qs = drv._page_blockers()["questions"]
    assert len(qs) == 6                  # every field still asked…
    assert len(calls) == 3               # …but only 3 lists are harvested
    assert qs[0].get("options") == ["X", "Y"]
    assert "options" not in qs[5]        # the rest stay free text


def test_page_blockers_report_captcha_first():
    drv = _released_driver(_FakePage([], captcha=True))
    assert drv._page_blockers() == {"captcha": True}


def test_page_blockers_quiet_page_reports_nothing():
    drv = _released_driver(_FakePage([{"id": "done", "name": "done", "label": "Done",
                                       "type": "text", "required": True,
                                       "empty": False, "sensitive": False}]))
    assert drv._page_blockers() == {}


# ---------- optional questions are never prompted -------------------------------

def _discover_harness(blockers_seq, fee_after_rewind=False):
    """discover_fee with everything below the classification stubbed out."""
    from app.portal.released_flow import ReleasedFlowDriver

    drv = object.__new__(ReleasedFlowDriver)
    state = {"rewinds": 0}
    seq = list(blockers_seq)

    class _Runner:
        fee_seen = None

        def run(self, stop_before=None):
            return {"status": "boundary", "node": "b", "action": "APPLICANT_HANDOFF",
                    "handoff_kind": "payment_credentials"}

    drv._runner = lambda: _Runner()
    drv._cleanup_tmp = lambda: None
    drv._page_blockers = lambda: (seq.pop(0) if seq else {})
    drv._fee_from_page_text = lambda: (
        {"amount_cents": 2500, "currency": "USD"}
        if state["rewinds"] and fee_after_rewind else None)
    drv._rewind_to_advance_click = lambda: state.__setitem__("rewinds", state["rewinds"] + 1)
    drv._apply_page_fill = lambda fills: None
    drv.released = SimpleNamespace(family=SimpleNamespace(operator="Op", name="Fam"))
    return drv, state


_OPTIONAL_Q = {"key": "position", "question": "What is your position?",
               "mandatory": False, "kind": "text"}
_MANDATORY_Q = {"key": "religion", "question": "What is your religion?",
                "mandatory": True, "kind": "text"}


def test_optional_only_pages_auto_advance_instead_of_prompting():
    drv, state = _discover_harness(
        [{"questions": [_OPTIONAL_Q], "page_field_map": {"position": {"selector": "[id=\"p\"]", "kind": "text"}}}],
        fee_after_rewind=True)
    res = drv.discover_fee()
    # Ellis clicked Next itself; the applicant was never prompted.
    assert res["ok"] is True and res["amount"] == 2500
    assert state["rewinds"] == 1


def test_only_mandatory_questions_are_ever_asked():
    drv, _ = _discover_harness(
        [{"questions": [_MANDATORY_Q, _OPTIONAL_Q], "page_field_map": {}}])
    res = drv.discover_fee()
    assert res["code"] == "ADDITIONAL_INFORMATION_REQUIRED"
    assert [q["key"] for q in res["questions"]] == ["religion"]   # optional dropped


def test_optional_only_page_that_refuses_to_advance_hands_off_honestly():
    # Same skippable set before and after the advance attempt: re-asking is a
    # circle, not progress — the applicant gets the secure window instead.
    opt = {"questions": [_OPTIONAL_Q], "page_field_map": {}}
    drv, state = _discover_harness([opt, opt])          # page never moves
    res = drv.discover_fee()
    assert res["code"] == "APPLICANT_ACTION_REQUIRED"
    assert res["handoff"] == "portal_form"
    assert state["rewinds"] == 1                        # exactly one auto-attempt


def test_portal_complaints_after_the_advance_become_required_asks():
    # The portal refused the advance and flagged a field: THAT is the proof of
    # requiredness — the promoted question is asked, with the portal's wording.
    opt = {"questions": [_OPTIONAL_Q], "page_field_map": {}}
    promoted = {"questions": [dict(_OPTIONAL_Q, mandatory=True,
                                   why="The official portal says: required")],
                "page_field_map": {"position": {"selector": '[id="p"]', "kind": "text"}}}
    drv, state = _discover_harness([opt, promoted])
    res = drv.discover_fee()
    assert res["code"] == "ADDITIONAL_INFORMATION_REQUIRED"
    assert res["questions"][0]["mandatory"] is True
    assert "official portal says" in res["questions"][0]["why"]


def test_portal_validation_promotes_unmarked_fields_to_required():
    fields = [
        {"id": "basic_chiphi", "name": "chiphi", "label": "Intended expenses (in USD)",
         "type": "text", "tag": "input", "required": False, "empty": True, "sensitive": False},
        {"id": "opt2", "name": "opt2", "label": "Other note", "type": "text",
         "tag": "input", "required": False, "empty": True, "sensitive": False},
    ]
    page = _FakePage(fields)
    page.validation = {"ok": True, "messages": [], "fields": [
        {"id": "basic_chiphi", "label": "Intended expenses (in USD)",
         "message": "Please enter the intended expenses"}]}
    drv = _released_driver(page)
    by_key = {q["key"]: q for q in drv._page_blockers()["questions"]}
    # The portal flagged it -> required, with the portal's own reason.
    assert by_key["intended_expenses_in_usd"]["mandatory"] is True
    assert by_key["intended_expenses_in_usd"]["why"] == \
        "The official portal says: Please enter the intended expenses"
    # Unflagged unmarked fields stay skippable.
    assert by_key["other_note"]["mandatory"] is False


def test_portal_form_pause_upgrades_to_in_ellis_questions():
    drv = _released_driver(_FakePage([]))
    drv._page_blockers = lambda: {
        "questions": [dict(_OPTIONAL_Q, mandatory=True,
                           why="The official portal says: required")],
        "page_field_map": {"position": {"selector": '[id="p"]', "kind": "text"}}}
    res = drv._result_from({"status": "paused_applicant_action",
                            "handoff_kind": "portal_form",
                            "portal_messages": ["Position — required"]})
    assert res["code"] == "ADDITIONAL_INFORMATION_REQUIRED"
    assert res["questions"][0]["key"] == "position"
    # No askable mandatory fields -> the portal_form pause stands.
    drv._page_blockers = lambda: {}
    res = drv._result_from({"status": "paused_applicant_action",
                            "handoff_kind": "portal_form",
                            "portal_messages": ["Sign here"]})
    assert res["code"] == "APPLICANT_ACTION_REQUIRED" and res["handoff"] == "portal_form"


def test_advance_refusal_detected_after_async_validation_render():
    from app.adapter_factory.runtime import FlowRunner

    class LateErrors:
        def __init__(self):
            self.settled = 0
            self.reads = 0

        def read_validation_errors(self):
            self.reads += 1
            if self.settled:
                return {"ok": True, "messages": [], "fields": [
                    {"id": "basic_chiphi", "label": "Intended expenses",
                     "message": "Please enter intended expenses"}]}
            return {"ok": True, "messages": [], "fields": []}

        def settle(self, ms=700):
            self.settled += 1

    runner = object.__new__(FlowRunner)
    driver = LateErrors()
    runner.driver = driver
    runner._repair_attempted = True          # skip the silent-repair pass
    sentinel = {"status": "handoff", "handoff_kind": "portal_form"}
    runner._portal_validation_questions = lambda node: sentinel
    out = runner._advance_refused({"node_id": "n", "selector": "#next"})
    # The refusal only became visible AFTER the settle re-read — previously
    # this read as a successful advance and the flow sailed to a wrong pause.
    assert out is sentinel
    assert driver.settled == 1 and driver.reads == 2


def test_read_fee_auto_advances_past_optional_only_fields():
    class OptionalOnlyPage:
        def read_current_fee(self, **_kw):
            return {"ok": False, "code": "FEE_NOT_DISPLAYED",
                    "blockers": {"questions": [dict(_OPTIONAL_Q)],
                                 "page_field_map": {"position": {
                                     "selector": '[id="pos"]', "kind": "text"}}}}

        def discover_fee(self, **_kw):
            return dict(FEE)

        def detach(self):
            pass

    wf = _stuck_fee_pause(OptionalOnlyPage())
    wf.read_fee()
    # No prompt for skippable fields — the drive continued to the real fee.
    assert wf.machine.state == "PAYMENT_APPROVAL_REQUIRED"
    assert wf.pending["handoff"] == "payment_approval"
    assert wf.fee["amount"] == 2500


# ---------- checkbox commit (entry gate + declaration) -------------------------

class _FakeLocator:
    """Stand-in for a Playwright locator: only the ENGINE resolves selectors."""

    def __init__(self, page, selector, kind="base"):
        self.page = page
        self.selector = selector
        self.kind = kind

    @property
    def first(self):
        return self

    def locator(self, expr):
        # xpath=ancestor::label[1] / ancestor::*[contains(@class,'checkbox')][1]
        # / "a" (the driver's embedded-link probe inside the label)
        if expr == "a":
            return _FakeLocator(self.page, self.selector, "anchor")
        kind = "label" if "label" in expr else "wrapper"
        return _FakeLocator(self.page, self.selector, kind)

    def count(self):
        if self.kind == "anchor":
            return 1 if getattr(self.page, "label_anchors", 0) else 0
        if self.selector not in self.page.known_selectors:
            return 0
        if self.kind == "base":
            return 1                      # the element itself always resolves
        return 1 if self.kind in self.page.click_targets else 0

    def wait_for(self, state=None, timeout=None):
        if self.selector not in self.page.known_selectors:
            raise RuntimeError("not found")

    def click(self, timeout=None):
        # Clicking the base locator IS clicking the input element.
        kind = "input" if self.kind == "base" else self.kind
        if kind not in self.page.click_targets:
            raise RuntimeError("not clickable")
        self.page.clicked.append(kind)
        # Only a click the FRAMEWORK honors flips the wrapper's own state.
        if kind in self.page.commits:
            self.page.state["wrapChecked"] = True
        self.page.state["input"] = True

    def evaluate(self, js):
        if self.selector not in self.page.known_selectors:
            raise RuntimeError("not found")
        if "scrollIntoView" in js or "el.click()" in js:
            return None
        return dict(self.page.state)

    def is_checked(self, timeout=None):
        return bool(self.page.state.get("input"))


class _FakeCheckboxPage:
    """A page whose selectors are PLAYWRIGHT syntax — CSS parsing must never
    be attempted on them (that regression read as 'element missing').

    click_targets: which ancestors exist to click.
    commits:       which of them the FRAMEWORK actually honors (an Ant
                   checkbox commits on its label, not on a raw input poke).
    """

    def __init__(self, state, selector, click_targets=("label", "wrapper", "input"),
                 commits=("label",), check_commits=False):
        self.known_selectors = {selector}
        self.state = state
        self.click_targets = set(click_targets)
        self.commits = set(commits)
        self.check_commits = check_commits
        self.clicked = []
        self.checks = []

    def locator(self, selector):
        return _FakeLocator(self, selector)

    def evaluate(self, js, arg=None):
        # Any attempt to parse a selector as CSS in the page is the bug.
        raise AssertionError("selector must not reach document.querySelector")

    def eval_on_selector(self, selector, js):
        raise AssertionError("selector must not reach eval_on_selector here")

    def check(self, selector, timeout=None):
        self.checks.append(selector)
        self.state["input"] = True              # native property only…
        if self.check_commits:                  # …unless the widget honors it
            self.state["wrapChecked"] = True

    def wait_for_timeout(self, ms):
        pass


PW_SELECTOR = 'input[type="checkbox"]:visible >> nth=0'


def _driver_for(page):
    from app.adapter_factory.live_driver import BrowserbasePageDriver
    drv = object.__new__(BrowserbasePageDriver)
    drv.page = page
    drv.allowed = []
    return drv


def test_checkbox_commits_by_clicking_the_label_like_a_human():
    page = _FakeCheckboxPage(
        {"input": False, "wrapChecked": False, "aria": None}, PW_SELECTOR)
    drv = _driver_for(page)
    res = drv.check(PW_SELECTOR)
    # Resolved through the locator engine (never parsed as CSS — the fake
    # asserts on that) and ticked on the LABEL, which is what fires the
    # framework's own handler.
    assert res["ok"] is True and res["method"] == "click_label"
    assert page.clicked == ["label"]
    assert page.state["wrapChecked"] is True


def test_label_with_an_embedded_link_is_ticked_via_the_wrapper_instead():
    # 'I have read the <a>terms</a>…' — a center-point click on that label
    # can land on the link and navigate the live session off the form. The
    # driver must skip the label target and commit through the wrapper.
    page = _FakeCheckboxPage(
        {"input": False, "wrapChecked": False, "aria": None}, PW_SELECTOR,
        commits=("label", "wrapper"))
    page.label_anchors = 1
    drv = _driver_for(page)
    res = drv.check(PW_SELECTOR)
    assert res["ok"] is True and res["method"] == "click_wrapper"
    assert "label" not in page.clicked
    assert page.state["wrapChecked"] is True


def test_raw_input_click_the_framework_ignores_is_not_success():
    # THE production bug: poking the hidden input sets .checked while Ant's
    # model never registers — the box stays visually empty and Next disabled.
    # With a framework wrapper present, only ITS state may count.
    page = _FakeCheckboxPage(
        {"input": False, "wrapChecked": False, "aria": None}, PW_SELECTOR,
        click_targets=("input",), commits=())
    drv = _driver_for(page)
    res = drv.check(PW_SELECTOR)
    assert res == {"ok": False, "code": "CHECK_NOT_COMMITTED"}
    assert page.state["input"] is True             # the property DID flip…
    assert page.state["wrapChecked"] is False      # …but the widget did not


def test_checkbox_falls_back_through_targets_to_playwright_check():
    page = _FakeCheckboxPage(
        {"input": False, "wrapChecked": False, "aria": None}, PW_SELECTOR,
        click_targets=("input",), commits=(), check_commits=True)
    drv = _driver_for(page)
    res = drv.check(PW_SELECTOR)
    assert res["ok"] is True and res["method"] == "check"
    assert page.checks == [PW_SELECTOR]


def test_plain_native_checkbox_without_a_wrapper_trusts_its_own_state():
    from app.adapter_factory.live_driver import BrowserbasePageDriver as B
    s = B._checkbox_committed_strict
    # No framework wrapper (wrapChecked None) -> the input IS the truth.
    assert s({"input": True, "wrapChecked": None, "aria": None})
    assert not s({"input": False, "wrapChecked": None, "aria": None})
    # Framework wrapper present -> ONLY its state counts.
    assert not s({"input": True, "wrapChecked": False, "aria": None})
    assert s({"input": False, "wrapChecked": True, "aria": None})
    # aria wins over everything when present.
    assert s({"input": False, "wrapChecked": False, "aria": True})
    assert not s({"input": True, "wrapChecked": True, "aria": False})
    assert not s(None)


def test_declaration_mark_uses_the_same_verified_commit():
    page = _FakeCheckboxPage(
        {"input": False, "wrapChecked": False, "aria": None}, '[id="agree"]')
    drv = _driver_for(page)
    res = drv.apply_declaration_mark('[id="agree"]')
    assert res["ok"] is True and res["method"] == "click_label"


def test_disabled_advance_button_is_never_reported_as_clicked():
    # THE production bug: the portal disables Next until the form satisfies
    # it; a "successful" click there sent the run onward believing the page
    # had advanced. force_click made it worse — force=True skips the
    # 'enabled' actionability check entirely.
    class _DisabledBtnPage:
        def __init__(self):
            self.clicked = False

        def keyboard_press(self, *a):
            pass

        @property
        def keyboard(self):
            return SimpleNamespace(press=lambda *a: None)

        def wait_for_timeout(self, ms):
            pass

        def locator(self, sel):
            page = self

            class L:
                first = None

                def scroll_into_view_if_needed(self, timeout=None):
                    pass

                def is_disabled(self, timeout=None):
                    return True

                def click(self, timeout=None, force=False):
                    page.clicked = True      # must never happen

            loc = L()
            loc.first = loc
            return loc

        def eval_on_selector(self, sel, js):
            return True if "disabled" in js else None

    from app.adapter_factory.live_driver import BrowserbasePageDriver
    page = _DisabledBtnPage()
    drv = object.__new__(BrowserbasePageDriver)
    drv.page = page
    drv.allowed = []
    assert drv.click('button:has-text("Next")') == {"ok": False, "code": "DISABLED"}
    assert drv.force_click('button:has-text("Next")') == {"ok": False, "code": "DISABLED"}
    assert page.clicked is False


def test_runtime_treats_a_disabled_next_as_a_real_blocker():
    from app.adapter_factory.runtime import FlowRunner
    runner = object.__new__(FlowRunner)
    runner.driver = SimpleNamespace(click=lambda sel: {"ok": False, "code": "DISABLED"})
    runner._repair_attempted = True
    runner._portal_validation_questions = lambda node: None
    out = runner._step({"node_id": "next", "action": "CLICK", "selector": "#next"})
    # Never "ok", never forced — an honest failure the classifier explains.
    assert out["status"] == "failed"
    assert "disabled" in out["reason"].lower()


def test_declaration_fast_path_ticks_every_box_then_advances():
    from app.portal.released_flow import ReleasedFlowDriver

    class _Page:
        def __init__(self):
            self.remaining = [
                {"selector": '[id="a"]', "text": "Committed to declare temporary residence"},
                {"selector": '[id="b"]', "text": "I hereby declare that the above statements are true"},
            ]
            self.ticked = []
            self.next_clicks = 0

        def find_declaration_checkbox(self):
            return {"found": bool(self.remaining), "boxes": list(self.remaining)}

        def check(self, sel):
            self.ticked.append(sel)
            self.remaining = [b for b in self.remaining if b["selector"] != sel]
            return {"ok": True, "method": "click_label"}

        def next_button_state(self):
            return {"ok": True, "present": True, "disabled": bool(self.remaining)}

        def click_next_button(self):
            self.next_clicks += 1
            return {"ok": True}

        def settle(self, ms=0):
            pass

    page = _Page()
    drv = object.__new__(ReleasedFlowDriver)
    drv._ensure_live = lambda: page
    drv._progress_sink = None
    drv._emit_declaration_audit = lambda text: None
    assert drv._tick_declaration_and_advance() is True
    # EVERY declaration box ticked (the form gates Next on all of them),
    # then Next pressed exactly once.
    assert page.ticked == ['[id="a"]', '[id="b"]']
    assert page.next_clicks == 1


def _fast_path_page(sig_before="page-A", sig_after="page-A",
                    button_state=None):
    class _Page:
        def __init__(self):
            self.remaining = [
                {"selector": '[id="b"]',
                 "text": "I hereby declare that the above statements are true"},
            ]
            self.next_clicks = 0
            self.sigs = [sig_before, sig_after]

        def find_declaration_checkbox(self):
            return {"found": bool(self.remaining), "boxes": list(self.remaining)}

        def check(self, sel):
            self.remaining = [b for b in self.remaining if b["selector"] != sel]
            return {"ok": True}

        def next_button_state(self):
            return button_state if button_state is not None else \
                {"ok": True, "present": True, "disabled": False}

        def page_signature(self):
            return self.sigs.pop(0) if self.sigs else ""

        def click_next_button(self):
            self.next_clicks += 1
            return {"ok": True}

        def settle(self, ms=0):
            pass

    return _Page()


def _fast_path_driver(page):
    from app.portal.released_flow import ReleasedFlowDriver
    drv = object.__new__(ReleasedFlowDriver)
    drv._ensure_live = lambda: page
    drv._progress_sink = None
    drv.audited = []
    drv._emit_declaration_audit = lambda text: drv.audited.append(text)
    drv._page_blockers = lambda: {}
    return drv


def test_fast_path_fills_known_answer_fields_before_ticking_and_next():
    # eVisa's passport "Type" has NO flow node; its answer (intake's
    # travel_document_type) is held from intake. The fast path must land it
    # BEFORE the declaration tick and the Next press — pressing Next against
    # an incomplete form dead-ended the run into manual review.
    page = _fast_path_page(sig_before="page-A", sig_after="page-B")
    drv = _fast_path_driver(page)
    events = []
    drv._page_blockers = lambda: {"refill": [
        {"selector": '[id="basic_hcLoai"]', "kind": "combobox",
         "value": "Ordinary passport"}]}
    orig_check = page.check
    page.check = lambda sel: (events.append("tick"), orig_check(sel))[1]
    drv._fill_observed_values = lambda fills: (events.append("fill"), len(fills))[1]
    assert drv._tick_declaration_and_advance() is True
    assert events and events[0] == "fill"      # filled before any tick
    assert "tick" in events
    assert page.next_clicks == 1


def test_fast_path_steps_aside_when_unanswered_mandatory_fields_remain():
    # A genuinely unanswered mandatory field is not the fast path's page:
    # classification must ask it in Ellis — no tick, no click.
    page = _fast_path_page()
    drv = _fast_path_driver(page)
    drv._page_blockers = lambda: {"questions": [{"key": "religion",
                                                 "mandatory": True}]}
    assert drv._tick_declaration_and_advance() is False
    assert page.next_clicks == 0


def test_portal_form_pause_with_a_held_answer_refills_and_retries():
    # THE screenshot bug: the runner paused portal_form over the passport
    # "Type" field ("complete it in the secure window yourself") while the
    # classifier knew the answer was HELD (intake's travel_document_type) —
    # the upgrade only acted on questions and threw the refill away. It must
    # fill the page itself, rewind to the flow's advance click, and signal a
    # retry — never send a fillable field to the applicant's window.
    from app.portal.released_flow import ReleasedFlowDriver

    class _Live:
        def __init__(self):
            self.sig = "page-3"

        def page_signature(self):
            return self.sig

    live = _Live()
    drv = object.__new__(ReleasedFlowDriver)
    drv._allow_refill_retry = True
    drv._portal_form_refilled_sigs = set()
    drv._ensure_live = lambda: live
    drv._page_blockers = lambda: {"refill": [
        {"selector": '[id="basic_hcLoai"]', "kind": "combobox",
         "value": "Ordinary passport"}]}
    calls = []
    drv._fill_observed_values = lambda fills: (calls.append("fill"), len(fills))[1]
    drv._rewind_to_advance_click = lambda: calls.append("rewind")
    out = drv._portal_form_to_questions({"portal_messages": ["Please enter Passport type"]})
    assert out == {"ok": False, "code": "PORTAL_STEP_REFILLED"}
    # Fill only — NO rewind: the cursor already sits AT the refused CLICK
    # node; rewinding would land on the previous page's advance and
    # double-click it.
    assert calls == ["fill"]
    # SAME page still incomplete after Ellis filled it: no silent loop — the
    # pause stands (None) so a human sees it.
    assert drv._portal_form_to_questions({}) is None
    # A LATER page's first refill must not be blocked by this page's.
    live.sig = "page-5"
    out3 = drv._portal_form_to_questions({})
    assert out3 == {"ok": False, "code": "PORTAL_STEP_REFILLED"}
    assert calls == ["fill", "fill"]


def test_fill_segment_refills_held_answers_and_resumes_in_place():
    # THE user's third repro: the portal_form pause hit during the CREATE/FILL
    # segment (DOCUMENT_UPLOAD_PENDING, 5s after start) — not fee discovery —
    # and the discover-only refill gate skipped it: no refill, no question
    # (the answer is held!), applicant parked in the secure window. The fill
    # segments must refill and re-run the SAME segment immediately.
    from app.portal.released_flow import ReleasedFlowDriver

    class _Live:
        def page_signature(self):
            return "form-page"

    walks = []
    drv = object.__new__(ReleasedFlowDriver)
    drv._allow_refill_retry = False
    drv._portal_form_refilled_sigs = set()
    drv._ensure_live = lambda: _Live()
    drv._page_blockers = lambda: {"refill": [
        {"selector": '[id="basic_hcLoai"]', "kind": "combobox",
         "value": "Ordinary passport"}]}
    filled = []
    drv._fill_observed_values = lambda fills: (filled.append(list(fills)), len(fills))[1]

    def advance():
        walks.append("advance")
        return {"status": "boundary"}       # the re-run reaches the boundary

    out = drv._result_retrying_refills(
        {"status": "paused_applicant_action", "handoff_kind": "portal_form"},
        advance)
    assert filled, "the held answer must be filled by Ellis"
    assert walks == ["advance"]             # segment re-ran once, in place
    assert out["ok"] is True
    assert drv._allow_refill_retry is False  # permission never leaks out


def test_upload_document_segment_refills_and_advances_end_to_end():
    # Wiring lock for the user's ACTUAL repro state: DOCUMENT_UPLOAD_PENDING
    # runs upload_document(), the runtime raises portal_form for a field with
    # no flow node ("Please enter Passport type"), and the whole chain —
    # _result_from -> _portal_form_to_questions -> refill -> retry — must
    # complete inside that call. Patching only the fee-discovery loop (as an
    # earlier attempt did) leaves this path parked in the secure window.
    from app.portal.released_flow import ReleasedFlowDriver

    class _Live:
        def page_signature(self):
            return "form-page"

    advances = []
    drv = object.__new__(ReleasedFlowDriver)
    drv._allow_refill_retry = False
    drv._portal_form_refilled_sigs = set()
    drv._ensure_live = lambda: _Live()
    drv._page_blockers = lambda: {"refill": [
        {"selector": '[id="basic_hcLoai"]', "kind": "combobox",
         "value": "Ordinary passport"}]}
    filled = []
    drv._fill_observed_values = lambda fills: (filled.append(list(fills)), len(fills))[1]

    def _advance(stop_before=None):
        advances.append(1)
        if len(advances) == 1:
            return {"status": "paused_applicant_action",
                    "handoff_kind": "portal_form",
                    "portal_messages": ["Please enter Passport type"]}
        return {"status": "boundary"}

    drv._advance = _advance
    out = drv.upload_document()
    assert out["ok"] is True, "the segment must advance, not pause the applicant"
    assert filled and filled[0][0]["value"] == "Ordinary passport"
    assert len(advances) == 2               # refused walk, then the retry


def test_refill_retry_is_confined_to_the_discover_loop():
    # Callers whose workflow arm cannot re-enter (e.g. the captcha branch)
    # must never receive PORTAL_STEP_REFILLED — outside the discover loop the
    # upgrade keeps its pause/question behavior unchanged.
    from app.portal.released_flow import ReleasedFlowDriver

    drv = object.__new__(ReleasedFlowDriver)
    drv._allow_refill_retry = False          # not inside _discover_fee_loop
    drv._portal_form_refilled_sigs = set()
    drv._page_blockers = lambda: {"refill": [
        {"selector": '[id="basic_hcLoai"]', "kind": "combobox",
         "value": "Ordinary passport"}]}
    drv._fill_observed_values = lambda fills: (_ for _ in ()).throw(
        AssertionError("no fill outside the discover loop"))
    assert drv._portal_form_to_questions({}) is None


def test_portal_form_pause_without_the_answer_asks_in_ellis_ui():
    # No held answer -> the field becomes a mandatory in-Ellis question
    # (ADDITIONAL_INFORMATION), exactly like every other missing field —
    # never a secure-window chore.
    from app.portal.released_flow import ReleasedFlowDriver

    drv = object.__new__(ReleasedFlowDriver)
    drv._allow_refill_retry = True
    drv._portal_form_refilled_sigs = set()
    drv._page_blockers = lambda: {
        "questions": [{"key": "passport_type", "label": "Type",
                       "mandatory": True,
                       "options": ["Ordinary passport", "Diplomatic passport"]}],
        "page_field_map": {"passport_type": {"selector": '[id="basic_hcLoai"]',
                                             "kind": "combobox"}}}
    out = drv._portal_form_to_questions({})
    assert out["code"] == "ADDITIONAL_INFORMATION_REQUIRED"
    assert out["questions"][0]["key"] == "passport_type"


def test_discover_loop_retries_immediately_after_a_portal_step_refill():
    # A PORTAL_STEP_REFILLED result must re-walk the flow in the same
    # discover call (the fill landed, the cursor is rewound) — not pause.
    from app.portal.released_flow import ReleasedFlowDriver

    class _Runner:
        fee_seen = None

        def __init__(self, script):
            self.script = script

        def run(self, stop_before=None):
            return self.script.pop(0)

    script = [{"status": "paused_applicant_action", "handoff_kind": "portal_form"},
              {"status": "failed", "reason": "portal closed"}]
    runner = _Runner(script)
    drv = object.__new__(ReleasedFlowDriver)
    drv._allow_refill_retry = True
    drv._portal_form_refilled_sigs = set()
    drv._runner = lambda: runner
    drv._fee_from_page_text = lambda: None
    drv._page_blockers = lambda: {"refill": [
        {"selector": '[id="basic_hcLoai"]', "kind": "combobox",
         "value": "Ordinary passport"}]}
    filled = []
    drv._fill_observed_values = lambda fills: (filled.append(list(fills)), len(fills))[1]
    drv._rewind_to_advance_click = lambda: None
    out = drv._discover_fee_loop(None, False, False, set())
    assert filled, "the held answer must be filled by Ellis"
    assert not script, "the walk must resume immediately after the refill"
    assert out["code"] == "portal closed"       # second result surfaces honestly


def test_incomplete_form_failure_routes_to_classification_not_a_dead_end():
    # THE stall: passport "Type" empty (no flow node), Next disabled, the
    # runner fails honestly with FORM_INCOMPLETE_REASON. That failure must
    # trigger the page classifier (which refills the held answer) once —
    # never surface directly as a dead failure -> manual review.
    from app.portal.released_flow import ReleasedFlowDriver
    from app.adapter_factory.runtime import FORM_INCOMPLETE_REASON

    class _Runner:
        fee_seen = None

        def run(self, stop_before=None):
            return {"status": "failed", "reason": FORM_INCOMPLETE_REASON}

    drv = object.__new__(ReleasedFlowDriver)
    drv._runner = lambda: _Runner()
    drv._fee_from_page_text = lambda: None
    drv._tick_declaration_and_advance = lambda: False
    filled = []
    drv._page_blockers = lambda: {"refill": [
        {"selector": '[id="basic_hcLoai"]', "kind": "combobox",
         "value": "Ordinary passport"}]}
    drv._apply_page_fill = lambda fills: filled.append(list(fills))
    out = drv._discover_fee_loop(None, False, False, set())
    assert filled, "classifier refill must run on the incomplete-form failure"
    assert out["ok"] is False
    assert FORM_INCOMPLETE_REASON in str(out.get("code"))


def test_refused_advance_is_not_reported_as_moved():
    # The old proof of movement was circular: the finder only reports
    # UNCHECKED boxes, so after ticking, "boxes gone" was true even when the
    # portal refused the Next click and the page never moved. The page
    # IDENTITY (signature) is the proof now — unchanged means unmoved, and
    # no declaration_confirmed_on_portal audit may be emitted.
    page = _fast_path_page(sig_before="page-A", sig_after="page-A")
    drv = _fast_path_driver(page)
    assert drv._tick_declaration_and_advance() is False
    assert drv.audited == []
    assert page.next_clicks == 1         # the click happened, it just failed


def test_signature_change_is_real_movement_and_audits_the_declaration():
    page = _fast_path_page(sig_before="page-A", sig_after="page-B")
    drv = _fast_path_driver(page)
    assert drv._tick_declaration_and_advance() is True
    assert len(drv.audited) == 1


def test_fast_path_fails_closed_when_the_advance_button_is_not_recognized():
    # {present: False} (a 'Continue'-labelled portal) and {ok: False} (state
    # read failed) must BOTH refuse the fast path — never fall through to a
    # blind click that could press an arbitrary submit control. Those pages
    # advance through the flow's own declared CLICK node under full guards.
    for state in ({"ok": True, "present": False}, {"ok": False}):
        page = _fast_path_page(button_state=state)
        drv = _fast_path_driver(page)
        assert drv._tick_declaration_and_advance() is False
        assert page.next_clicks == 0     # no click of any kind


def test_declaration_finder_never_matches_consent_or_terms_language():
    # Repo policy (released_flow._DECLARATION_RE comment): a 'hereby
    # authorize/consent/agree to the terms' box is the APPLICANT's to give.
    # The driver-level finder feeds both auto-tick paths, so bare 'hereby'
    # must not qualify and consent/T&C language must disqualify.
    from app.adapter_factory.live_driver import BrowserbasePageDriver
    js = BrowserbasePageDriver._FIND_DECLARATION_JS
    assert "|hereby|" not in js
    assert "consent.test(text)" in js


def test_click_next_button_never_presses_a_text_blind_submit():
    # A bare input[type=submit] candidate clicks the FIRST visible submit on
    # the page — on a drifted page that can be a Pay control. Only controls
    # whose FULL trimmed text is one of the advance words may qualify.
    import inspect
    from app.adapter_factory.live_driver import BrowserbasePageDriver
    src = inspect.getsource(BrowserbasePageDriver.click_next_button)
    assert "'input[type=\"submit\"]'" not in src   # no locator-based blind click
    assert "words.includes(t)" in src              # exact membership, not substring
    assert "_ADVANCE_WORDS" in src
    words = BrowserbasePageDriver._ADVANCE_WORDS
    assert "next" in words and "tiếp tục" in words
    # Words that appear on dangerous controls must never count as "advance".
    for bad in ("pay", "submit", "confirm", "pagar", "payer", "ok"):
        assert bad not in words


def test_disabled_next_ticks_declarations_then_retries_the_same_click():
    # THE eVisa stall: 'I hereby declare…' has NO id and NO name; nothing
    # ticked it, Next stayed disabled, the run died into manual review. On a
    # DISABLED click the runtime now ticks every declaration and retries its
    # OWN declared click — under all guards, exactly once.
    from app.adapter_factory.runtime import FlowRunner

    class _Drv:
        def __init__(self):
            self.declared_ticked = False
            self.clicks = 0

        def click(self, sel):
            self.clicks += 1
            if self.declared_ticked:
                return {"ok": True}
            return {"ok": False, "code": "DISABLED"}

        def tick_declarations(self):
            self.declared_ticked = True
            return 2

        def read_validation_errors(self):
            return {"ok": True, "messages": [], "fields": []}

    runner = object.__new__(FlowRunner)
    drv = _Drv()
    runner.driver = drv
    runner._repair_attempted = True
    runner._declaration_ticked_nodes = set()
    runner._portal_validation_questions = lambda node: None
    runner._advance_refused = lambda node: None
    out = runner._step({"node_id": "next", "action": "CLICK", "selector": "#next",
                        "max_retries": 0})
    assert out["status"] == "ok"
    assert drv.clicks == 2               # disabled once, then the real click
    assert runner._declaration_ticked_nodes == {"next"}


def test_second_declaration_page_gets_its_own_fast_path():
    # The tick guard is per NODE, burned only by a pass that ticked something:
    # a family with declaration boxes on two different pages (consent page
    # early, 'I declare…' before submit) must fast-path BOTH — a per-run
    # burned-before-tick flag dead-ended page two.
    from app.adapter_factory.runtime import FlowRunner

    class _Drv:
        def __init__(self):
            self.ticked_calls = 0
            self.enabled = False

        def click(self, sel):
            if self.enabled:
                self.enabled = False     # next page starts disabled again
                return {"ok": True}
            return {"ok": False, "code": "DISABLED"}

        def tick_declarations(self):
            self.ticked_calls += 1
            self.enabled = True
            return 1

    runner = object.__new__(FlowRunner)
    drv = _Drv()
    runner.driver = drv
    runner._repair_attempted = True
    runner._declaration_ticked_nodes = set()
    runner._portal_validation_questions = lambda node: None
    runner._advance_refused = lambda node: None
    for node_id in ("next_page_3", "next_page_6"):
        out = runner._step({"node_id": node_id, "action": "CLICK",
                            "selector": "#next", "max_retries": 0})
        assert out["status"] == "ok"
    assert drv.ticked_calls == 2
    assert runner._declaration_ticked_nodes == {"next_page_3", "next_page_6"}


def test_disabled_repair_reclicks_inline_instead_of_dying_with_the_loop():
    # With the default single attempt, a `continue` after a successful repair
    # exhausts the loop without re-clicking; the post-loop verification then
    # sees the freshly repaired (clean) form and reports a phantom ok on a
    # page whose Next was never clicked. The repair must re-click INLINE.
    from app.adapter_factory.runtime import FlowRunner

    class _Drv:
        def __init__(self):
            self.repaired = False
            self.clicks = 0

        def click(self, sel):
            self.clicks += 1
            if self.repaired:
                return {"ok": True}
            return {"ok": False, "code": "DISABLED"}

        def tick_declarations(self):
            return 0

    runner = object.__new__(FlowRunner)
    drv = _Drv()
    runner.driver = drv
    runner._repair_attempted = False
    runner._declaration_ticked_nodes = set()

    def _repair():
        drv.repaired = True
        return True

    runner._repair_flagged_fields = _repair
    runner._portal_validation_questions = lambda node: None
    runner._advance_refused = lambda node: None
    out = runner._step({"node_id": "next", "action": "CLICK", "selector": "#next",
                        "max_retries": 0})
    assert out["status"] == "ok"
    assert drv.clicks == 2               # disabled once, then the repaired click


def test_post_tick_reclick_timeout_reaches_timeout_recovery_not_disabled_verdict():
    # After the declarations tick, the re-click's OWN failure code is the
    # truth. A TIMEOUT re-click (overlay, or the page actually advanced and
    # detached the button) must reach the TIMEOUT recovery — _advanced_past /
    # force_click — never the false "Next button is still disabled" failure.
    from app.adapter_factory.runtime import FlowRunner

    class _Drv:
        def __init__(self):
            self.clicks = 0

        def click(self, sel):
            self.clicks += 1
            if self.clicks == 1:
                return {"ok": False, "code": "DISABLED"}
            return {"ok": False, "code": "TIMEOUT"}

        def tick_declarations(self):
            return 2

    runner = object.__new__(FlowRunner)
    runner.driver = _Drv()
    runner._repair_attempted = True
    runner._declaration_ticked_nodes = set()
    runner._portal_validation_questions = lambda node: None
    runner._advanced_past = lambda node: True
    out = runner._step({"node_id": "next", "action": "CLICK", "selector": "#next",
                        "max_retries": 0})
    assert out["status"] == "ok"
    assert out["detail"]["already_advanced"] is True


def test_declaration_fast_path_refuses_when_next_stays_disabled():
    from app.portal.released_flow import ReleasedFlowDriver

    class _StuckPage:
        def find_declaration_checkbox(self):
            return {"found": True, "boxes": [{"selector": '[id="a"]', "text": "I hereby declare"}]}

        def check(self, sel):
            return {"ok": True}

        def next_button_state(self):
            return {"ok": True, "present": True, "disabled": True}   # form still invalid

        def click_next_button(self):
            raise AssertionError("must not press a disabled Next")

        def settle(self, ms=0):
            pass

    drv = object.__new__(ReleasedFlowDriver)
    drv._ensure_live = lambda: _StuckPage()
    drv._progress_sink = None
    # Falls through to real classification instead of faking progress.
    assert drv._tick_declaration_and_advance() is False


def test_missing_checkbox_is_reported_honestly():
    page = _FakeCheckboxPage({"input": False}, PW_SELECTOR)
    drv = _driver_for(page)
    assert drv.check("#nope") == {"ok": False, "code": "NO_SUCH_ELEMENT"}


def test_flagged_field_matches_its_node_in_every_selector_form():
    from app.adapter_factory.runtime import FlowRunner as F
    ok = F._selector_targets_field
    # A portal-flagged field must find its node whatever selector form the
    # released flow used — a literal '#'+id lookup silently lost these.
    assert ok('#basic_chiphi', 'basic_chiphi')
    assert ok('[id="basic_chiphi"]', 'basic_chiphi')
    assert ok('input[name="basic_chiphi"]', 'basic_chiphi')
    assert ok('#basic_chiphi:visible', 'basic_chiphi')
    assert ok('input[name="chiphi"] >> nth=0', 'chiphi')
    assert ok('form#f > #basic_chiphi', 'basic_chiphi')
    # …without matching a DIFFERENT field that merely shares a prefix.
    assert not ok('#basic_chiphi_extra', 'basic_chiphi')
    assert not ok('#other', 'basic_chiphi')
    assert not ok('', 'basic_chiphi')
    assert not ok('#basic_chiphi', '')


def test_flagged_field_lookup_scans_fill_nodes_only():
    from types import SimpleNamespace as NS
    from app.adapter_factory.runtime import FlowRunner
    runner = object.__new__(FlowRunner)
    runner.flow = NS(nodes={
        "click": {"node_id": "click", "action": "CLICK", "selector": "#basic_x"},
        "fill": {"node_id": "fill", "action": "FILL_NON_SENSITIVE",
                 "selector": '[id="basic_x"]', "input_source": "x"},
    })
    # The FILL node wins — a CLICK sharing the id must not be "repaired".
    assert runner._node_for_flagged_field("basic_x")["node_id"] == "fill"
    assert runner._node_for_flagged_field("nope") is None




# ---------- in-Ellis captcha (human solves, Ellis transcribes) -----------------

def test_captcha_pause_carries_page_and_challenge_captures():
    class CapDriver:
        def captcha_context(self):
            return {"captcha_document_id": "doc-cap", "review_document_id": "doc-rev"}

        def detach(self):
            pass

    wf = _fee_wf(CapDriver(), "CAPTCHA_ACTION_REQUIRED")
    wf._captcha_return_state = "FEE_DISCOVERY_PENDING"
    wf.start()
    # The applicant solves INSIDE Ellis: the pause carries captures of the
    # challenge and of the portal's own review page (opaque doc ids only).
    assert wf.pending["handoff"] == "captcha"
    assert wf.pending["captcha_document_id"] == "doc-cap"
    assert wf.pending["review_document_id"] == "doc-rev"


def test_typed_captcha_answer_is_transcribed_once_and_never_persisted():
    class CapDriver:
        def __init__(self):
            self.answers = []

        def submit_captcha(self, captcha_token=None, human_answer=None,
                           answer=None, **_kw):
            self.answers.append(answer)
            return {"ok": True}

        def discover_fee(self, **_kw):
            return dict(FEE)

        def detach(self):
            pass

    driver = CapDriver()
    wf = _fee_wf(driver, "CAPTCHA_ACTION_REQUIRED")
    wf._captcha_return_state = "FEE_DISCOVERY_PENDING"
    wf.solve_captcha(token="48152")
    # Ellis transcribed the HUMAN's solution exactly once…
    assert driver.answers == ["48152"]
    # …and the value survives nowhere.
    assert wf.inputs.get("captcha_answer") is None
    assert "48152" not in json.dumps(wf.snapshot())
    assert wf.machine.state == "PAYMENT_APPROVAL_REQUIRED"   # flow continued


class _CaptchaPage:
    """The review page: captcha input + Next + (on success) the NOTICE."""

    def __init__(self, accept=True):
        self.accept = accept
        self.log = []

    def apply_captcha_answer(self, answer):
        self.log.append(("answer", answer))
        return {"ok": True}

    def click_next_button(self):
        self.log.append(("next",))
        return {"ok": True}

    def settle(self, ms=0):
        pass

    def notice_state(self):
        return {"ok": True, "present": self.accept}

    # The PAGE carries eVisa's real entry conditions, whose "Not FALLing under
    # the cases of suspension" once matched the Spanish error stem and turned
    # every acceptance into a rejection. The NOTICE is read on its own.
    _PAGE = ("Foreigners with valid international travel document. Not falling "
             "under the cases of suspension from entry.")

    def read_text(self, selector):
        return {"ok": True, "text": self._PAGE}

    def notice_text(self):
        # The success notice carries the registration code, no error language;
        # a rejection would instead say "invalid security code".
        if not self.accept:
            return {"ok": False, "code": "NO_NOTICE"}
        return {"ok": True,
                "text": ("DECLARATION COMPLETED. Registration code: "
                         "E260727CHNEG108503764. Please note this document code.")}

    def confirm_notice(self):
        self.log.append(("confirm",))
        return {"ok": True}

    def captcha_state(self, highlight=False):
        return {"ok": True, "present": not self.accept}

    def screenshot_png(self):
        return b"png-bytes"


def _captcha_driver(page):
    from app.portal.released_flow import ReleasedFlowDriver
    drv = object.__new__(ReleasedFlowDriver)
    drv._ensure_live = lambda: page
    drv._consume_handoff = lambda kind: drv.__dict__.setdefault("consumed", []).append(kind) or True
    drv._advance = lambda stop_before=None: {"status": "boundary", "node": "b",
                                             "action": "APPLICANT_HANDOFF",
                                             "handoff_kind": "payment_credentials"}
    drv.stored = []
    drv._store_page_document = lambda png, doc_type, name: drv.stored.append(doc_type) or "doc-1"
    drv._labeled_reference_from_page = lambda d, kind: "E260727CHNEG108503764"
    drv.app_row = SimpleNamespace(portal_reference="", org_id="o1", id="c1", answers={})
    drv.db = SimpleNamespace(commit=lambda: None)
    return drv


def test_accepted_code_presses_next_confirms_the_notice_and_notes_the_reference():
    page = _CaptchaPage(accept=True)
    drv = _captcha_driver(page)
    res = drv.submit_captcha(answer="424983")
    assert res["ok"] is True
    # Ellis typed the code, pressed Next, preserved the notice, confirmed it.
    assert ("answer", "424983") in page.log
    assert ("next",) in page.log and ("confirm",) in page.log
    assert "registration_notice" in drv.stored
    # The e-Visa document code the portal says to note: noted FOR the applicant.
    assert drv.app_row.portal_reference == "E260727CHNEG108503764"
    assert drv.consumed == ["captcha"]


def test_rejected_code_reports_wrong_and_leaves_the_flow_at_the_challenge():
    page = _CaptchaPage(accept=False)
    drv = _captcha_driver(page)
    res = drv.submit_captcha(answer="000000")
    assert res == {"ok": False, "code": "CAPTCHA_WRONG"}
    assert ("next",) in page.log                 # the attempt was really made
    assert "consumed" not in drv.__dict__        # cursor stays AT the captcha


def test_wrong_code_reasks_with_a_fresh_challenge_capture():
    class WrongCode:
        def submit_captcha(self, **_kw):
            return {"ok": False, "code": "CAPTCHA_WRONG"}

        def captcha_context(self):
            return {"captcha_document_id": "d2", "review_document_id": "r2"}

        def detach(self):
            pass

    wf = _fee_wf(WrongCode(), "CAPTCHA_ACTION_REQUIRED")
    wf._captcha_return_state = "FEE_DISCOVERY_PENDING"
    wf.solve_captcha(token="999999")
    assert wf.machine.state == "CAPTCHA_ACTION_REQUIRED"
    assert wf.pending["handoff"] == "captcha"
    assert wf.pending["captcha_retry"] is True   # "wasn't accepted — try again"
    assert wf.pending["captcha_document_id"] == "d2"   # the FRESH digits image


def test_captcha_code_is_vaulted_in_queue_and_destroyed(
        client, live_route, db, monkeypatch):  # noqa: F811
    case = _new_case(client)
    r = client.post(f"/cases/{case['id']}/signals/solve_captcha", headers=AUTH,
                    json={"token": "48152"})
    assert r.status_code == 200 and r.json()["queued"] is True
    run = db.get(models.PortalRun, r.json()["run_id"])
    ref = run.signal_kwargs.get("token_ref")
    assert ref and ref.startswith("vault://")
    assert "48152" not in str(run.signal_kwargs)
    captured = {}

    def fake_signal(db_, app_id, name, **kw):
        captured.update(kw, signal=name)
        return ({"case_id": app_id, "state": "DRAFT", "pending": None}, None)

    monkeypatch.setattr(service, "signal", fake_signal)
    portal_queue.run_pending_once("test-exec")
    assert captured["signal"] == "solve_captcha"
    assert captured["token"] == "48152"          # revealed exactly once
    with pytest.raises(KeyError):
        vault.reveal(ref)                        # destroyed immediately after


# ---------- portal banners, photo re-upload, and known-answer aliases ----------

def test_photo_banner_becomes_a_document_upload_ask():
    page = _FakePage([])
    page.validation = {"ok": True, "fields": [], "messages": [
        "Portrait photo was captured from another source. Your application "
        "may be rejected. Please upload another photo"]}
    drv = _released_driver(page)
    blockers = drv._page_blockers()
    q = blockers["questions"][0]
    # The portal's banner complaint, verbatim, becomes an upload ask IN ELLIS.
    assert q["key"] == "document:photo" and q["kind"] == "document"
    assert q["mandatory"] is True
    assert "Portrait photo" in q["question"]


def test_non_photo_banners_ride_along_verbatim():
    page = _FakePage([])
    page.validation = {"ok": True, "fields": [], "messages": [
        "Your session will expire in 5 minutes"]}
    drv = _released_driver(page)
    assert drv._page_blockers()["portal_messages"] == [
        "Your session will expire in 5 minutes"]


def test_rejected_photo_reupload_reruns_the_flow_uploads():
    class PhotoRejected:
        def __init__(self):
            self.kwargs_seen = []

        def discover_fee(self, **kw):
            self.kwargs_seen.append(kw)
            if kw.get("refresh_uploads"):
                return dict(FEE)          # new photo accepted -> fee page
            return {"ok": False, "code": "ADDITIONAL_INFORMATION_REQUIRED",
                    "questions": [{"key": "document:photo", "kind": "document",
                                   "mandatory": True,
                                   "question": "Please upload another photo"}],
                    "page_field_map": {}}

        def detach(self):
            pass

    driver = PhotoRejected()
    wf = _fee_wf(driver, "FEE_DISCOVERY_PENDING")
    wf.start()
    assert wf.pending["handoff"] == "additional_information"
    assert wf.pending["questions"][0]["kind"] == "document"
    assert wf._page_refresh_uploads is True
    # The applicant uploaded the new photo in Ellis; the re-drive re-runs the
    # flow's upload nodes exactly once so the NEW file reaches the portal.
    wf.start()
    assert driver.kwargs_seen[-1].get("refresh_uploads") is True
    assert wf.machine.state == "PAYMENT_APPROVAL_REQUIRED"
    assert wf._page_refresh_uploads is None


def test_passport_type_field_fills_from_the_intake_answer():
    fields = [{"id": "hcLoai", "name": "type", "label": "Type", "type": "select",
               "tag": "select", "options": ["Ordinary passport", "Diplomatic passport"],
               "required": True, "empty": True, "sensitive": False}]
    drv = _released_driver(_FakePage(fields),
                           answers={"travel_document_type": "ordinary_passport"})
    blockers = drv._page_blockers()
    # Never re-asked: intake already knows the passport type.
    assert "questions" not in blockers
    assert blockers["refill"] == [{"selector": '[id="hcLoai"]', "kind": "select",
                                   "value": "Ordinary passport", "format": ""}]


def test_rewind_to_uploads_reruns_the_latest_reversible_upload_window():
    from app.portal.released_flow import ReleasedFlowDriver

    execution = SimpleNamespace(current_node="n4", status="paused_applicant_action")
    committed = []
    drv = object.__new__(ReleasedFlowDriver)
    drv._execution = lambda: execution
    drv._flow = lambda: SimpleNamespace(
        order=["n1", "n2", "n3", "n4"],
        nodes={"n1": {"node_id": "n1", "action": "UPLOAD_AUTHORIZED_DOCUMENT",
                      "selector": "#photo", "doc_type": "photo"},
               "n2": {"node_id": "n2", "action": "UPLOAD_AUTHORIZED_DOCUMENT",
                      "selector": "#passport", "doc_type": "passport"},
               "n3": {"node_id": "n3", "action": "CLICK", "selector": "#next",
                      "irreversibility": "reversible"},
               "n4": {"node_id": "n4", "action": "APPLICANT_HANDOFF",
                      "handoff_kind": "payment_credentials"}})
    drv.db = SimpleNamespace(commit=lambda: committed.append(True))
    drv._rewind_to_uploads()
    assert execution.current_node == "n1"     # first upload of the window
    assert execution.status == "running" and committed


# ---------- in-Ellis declaration + review-before-payment -----------------------

def test_portal_declaration_is_made_in_ellis_and_transcribed_by_ellis():
    class DeclarationPage:
        def __init__(self):
            self.declaration_fills = []

        def discover_fee(self, page_fill=None, declaration_fill=None, **_kw):
            if declaration_fill:
                self.declaration_fills.append(dict(declaration_fill))
                return dict(FEE)         # ticked + advanced -> fee page
            return {"ok": False, "code": "APPLICANT_ACTION_REQUIRED",
                    "handoff": "personal_declaration",
                    "portal_messages": ["I hereby declare that the above statements are true"],
                    "declaration_map": {"selector": '[id="agree"]',
                                        "label": "I hereby declare that the above statements are true"}}

        def detach(self):
            pass

    driver = DeclarationPage()
    wf = _fee_wf(driver, "FEE_DISCOVERY_PENDING")
    wf.start()
    # The applicant declares IN ELLIS against the portal's verbatim statement.
    assert wf.pending["handoff"] == "personal_declaration"
    assert wf.pending["portal_messages"] == [
        "I hereby declare that the above statements are true"]
    assert wf._page_declaration["selector"] == '[id="agree"]'
    assert '[id="agree"]' not in json.dumps(
        {k: v for k, v in wf.pending.items()})   # selector never applicant-facing
    wf.complete_declaration()
    # Ellis transcribed the recorded declaration once and moved on to the fee.
    assert driver.declaration_fills == [{"selector": '[id="agree"]',
                                         "label": "I hereby declare that the above statements are true"}]
    assert wf.machine.state == "PAYMENT_APPROVAL_REQUIRED"
    assert wf.inputs["declaration_completed"] is False    # consumed one-shot
    assert wf._page_declaration is None


def test_declaration_gives_up_to_secure_window_after_repeated_tick_failures():
    # A custom declaration widget Ellis cannot tick must NOT loop the applicant
    # through the same ask forever — after two tries it hands off to the
    # secure window honestly.
    class UntickableDeclaration:
        def __init__(self):
            self.attempts = 0

        def discover_fee(self, page_fill=None, declaration_fill=None, **_kw):
            if declaration_fill:
                self.attempts += 1     # "ticked" but the box never advances
            return {"ok": False, "code": "APPLICANT_ACTION_REQUIRED",
                    "handoff": "personal_declaration",
                    "portal_messages": ["I hereby declare the above is true"],
                    "declaration_map": {"selectors": ['[id="agree"]'],
                                        "label": "I hereby declare the above is true"}}

        def detach(self):
            pass

    driver = UntickableDeclaration()
    wf = _fee_wf(driver, "FEE_DISCOVERY_PENDING")
    wf.start()
    assert wf.pending["handoff"] == "personal_declaration"
    wf.complete_declaration()      # attempt 1 -> still declaration
    assert wf.pending["handoff"] == "personal_declaration"
    wf.complete_declaration()      # attempt 2 -> give up, hand to secure window
    assert wf.pending["handoff"] == "portal_form"
    assert wf.pending["portal_messages"] == ["I hereby declare the above is true"]
    assert driver.attempts == 2


def test_stale_fee_approval_after_reclassify_never_wipes_the_flow():
    # A "confirm the fee" modal that was reclassified out from under the
    # applicant must not, on a late click, invent a fee or re-walk the portal.
    class Quiet:
        def detach(self):
            pass

    wf = _fee_wf(Quiet(), "FEE_DISCOVERY_PENDING")
    wf.pending = {"state": "FEE_DISCOVERY_PENDING", "reason": "questions",
                  "handoff": "additional_information", "live_view": None,
                  "questions": [{"key": "religion", "question": "?", "mandatory": True,
                                 "kind": "text"}]}
    # A stale approve_payment arrives while the case is really at
    # FEE_DISCOVERY_PENDING (reclassified). It is ignored — no fee invented,
    # no state change, the real pause stands.
    wf.approve_payment(amount_cents=2500, currency="USD")
    assert wf.fee is None
    assert wf.machine.state == "FEE_DISCOVERY_PENDING"
    assert wf.pending["handoff"] == "additional_information"


def test_full_application_review_comes_before_any_payment():
    class FeeReady:
        def detach(self):
            pass

    wf = _fee_wf(FeeReady(), "PAYMENT_APPROVAL_REQUIRED", fee=dict(FEE))
    gate = {"ok": False}
    wf.submission_gate = lambda: (gate["ok"], "final review and signature required")
    wf.start()
    # Unsigned review: the applicant reviews + signs the COMPLETE application
    # BEFORE any fee approval or payment step.
    assert wf.pending["handoff"] == "final_review"
    assert wf.machine.state == "PAYMENT_APPROVAL_REQUIRED"
    gate["ok"] = True
    wf.start()
    assert wf.pending["handoff"] == "payment_approval"    # now the exact fee


# ---------- adversarial-review regressions ------------------------------------

def test_gated_route_never_offers_the_card_ask():
    class GatedFiller(_CardFiller):
        def payment_fill_allowed(self):
            return False       # payment_preparation not released for the route

    wf = _payment_wf(GatedFiller())
    wf.start()
    # The fail-closed capability gate wins over method existence: the
    # applicant types their card personally, exactly the pre-redesign flow.
    assert wf.pending["handoff"] == "payment"
    assert wf.pending["payment_fillable"] is False


def test_reported_payment_failure_reoffers_the_card_ask():
    class DecliningPay(_CardFiller):
        def pay(self, **_kw):
            return {"ok": False, "code": "OUTCOME_UNCERTAIN"}

    driver = DecliningPay()
    wf = _payment_wf(driver)
    wf.start()
    wf.provide_payment_details(card=dict(CARD))
    assert wf.pending["payment_prefilled"] is True
    wf.complete_payment()
    assert wf.pending["purpose"] == "payment_verification"
    wf.provide_information({"payment_result": "Payment failed or was cancelled"})
    # A declined attempt clears the stale "details are filled in" claim and
    # offers the card ask again (possibly a different card).
    assert wf.machine.state == "PAYMENT_ACTION_REQUIRED"
    assert wf.pending["handoff"] == "payment_credentials"
    assert wf.inputs["payment_details_provided"] is False


def test_reclassify_refuses_a_fee_pause_recorded_at_a_foreign_state():
    class Whatever:
        def detach(self):
            pass

    wf = _fee_wf(Whatever(), "DOCUMENT_UPLOAD_PENDING")
    wf.pending = {"state": "DOCUMENT_UPLOAD_PENDING", "reason": "r",
                  "handoff": "fee_confirmation", "live_view": None}
    # A mis-sequenced pause is left untouched — never forced through an
    # illegal state-machine edge.
    assert wf._reclassify_fee_pause({"captcha": True}) is False
    assert wf.machine.state == "DOCUMENT_UPLOAD_PENDING"


def test_reclassify_downgrades_to_portal_form_for_personal_items():
    class Whatever:
        def detach(self):
            pass

    wf = _fee_wf(Whatever(), "PAYMENT_APPROVAL_REQUIRED")
    wf.pending = {"state": "PAYMENT_APPROVAL_REQUIRED", "reason": "r",
                  "handoff": "fee_confirmation", "live_view": None}
    assert wf._reclassify_fee_pause(
        {"portal_messages": ["I declare the information is true"]}) is True
    assert wf.machine.state == "FEE_DISCOVERY_PENDING"
    assert wf.pending["handoff"] == "portal_form"
    assert wf.pending["portal_messages"] == ["I declare the information is true"]


def test_apply_page_fill_formats_dates_and_rewinds_to_the_flows_own_click():
    from app.portal.released_flow import ReleasedFlowDriver

    class RecordingPage:
        def __init__(self):
            self.filled = []

        def fill_observed_field(self, selector, value, kind="text"):
            self.filled.append((selector, value, kind))
            return {"ok": True}

    page = RecordingPage()
    execution = SimpleNamespace(current_node="n3", status="paused_applicant_action")
    committed = []
    drv = object.__new__(ReleasedFlowDriver)
    drv._ensure_live = lambda: page
    drv._execution = lambda: execution
    drv._flow = lambda: SimpleNamespace(
        order=["n1", "n2", "n3"],
        nodes={"n1": {"node_id": "n1", "action": "FILL_NON_SENSITIVE",
                      "selector": "#a", "irreversibility": "reversible"},
               "n2": {"node_id": "n2", "action": "CLICK", "selector": "#next",
                      "irreversibility": "reversible"},
               "n3": {"node_id": "n3", "action": "APPLICANT_HANDOFF",
                      "handoff_kind": "payment_credentials"}})
    drv.db = SimpleNamespace(commit=lambda: committed.append(True))
    drv._apply_page_fill([{"selector": '[id="entry"]', "kind": "text",
                           "value": "2030-01-05", "format": "DD/MM/YYYY"}])
    # The ISO answer rendered in the portal's own declared format…
    assert page.filled == [('[id="entry"]', "05/01/2030", "text")]
    # …and the cursor rewound to the flow's OWN advance click so the re-click
    # runs under every FlowRunner guard — never a page-blind direct click.
    assert execution.current_node == "n2"
    assert execution.status == "running"
    assert committed


def test_rewind_never_crosses_a_non_reversible_node():
    from app.portal.released_flow import ReleasedFlowDriver

    execution = SimpleNamespace(current_node="n4", status="paused_applicant_action")
    drv = object.__new__(ReleasedFlowDriver)
    drv._execution = lambda: execution
    drv._flow = lambda: SimpleNamespace(
        order=["n1", "n2", "n3", "n4"],
        nodes={"n1": {"node_id": "n1", "action": "CLICK", "selector": "#next",
                      "irreversibility": "reversible"},
               "n2": {"node_id": "n2", "action": "CLICK", "selector": "#pay",
                      "irreversibility": "conditionally_reversible"},
               "n3": {"node_id": "n3", "action": "READ_FEE", "selector": "#fee"},
               "n4": {"node_id": "n4", "action": "APPLICANT_HANDOFF",
                      "handoff_kind": "payment_credentials"}})
    drv.db = SimpleNamespace(commit=lambda: None)
    drv._rewind_to_advance_click()
    # n2 is not plainly reversible: it resets the candidate and n1 sits
    # BEFORE it, so no rewind may cross it — the cursor must not move.
    assert execution.current_node == "n4"


def test_corrected_card_replaces_a_queued_one_and_busy_while_running(
        client, live_route, db):  # noqa: F811
    case = _new_case(client)
    r1 = client.post(f"/cases/{case['id']}/signals/provide_payment_details",
                     headers=AUTH,
                     json={"card": {"holder": "TEST A", "number": "4111 1111 1111 1111",
                                    "expiry": "12/33", "cvv": "123"}})
    run = db.get(models.PortalRun, r1.json()["run_id"])
    old_ref = run.signal_kwargs["card_ref"]
    # A corrected card while the run is still QUEUED replaces the payload —
    # never a silent dedupe that fills the typo'd card.
    r2 = client.post(f"/cases/{case['id']}/signals/provide_payment_details",
                     headers=AUTH,
                     json={"card": {"holder": "TEST B", "number": "4242 4242 4242 4242",
                                    "expiry": "11/32", "cvv": "999"}})
    assert r2.json()["run_id"] == r1.json()["run_id"]
    db.expire_all()
    run = db.get(models.PortalRun, r1.json()["run_id"])
    new_ref = run.signal_kwargs["card_ref"]
    assert new_ref != old_ref
    with pytest.raises(KeyError):
        vault.reveal(old_ref)                     # the typo'd card is gone
    assert json.loads(vault.reveal(new_ref))["number"] == "4242424242424242"
    # While the details are being TYPED (run running), a third submit gets an
    # honest busy instead of a false "sent".
    run.status = "running"
    db.commit()
    r3 = client.post(f"/cases/{case['id']}/signals/provide_payment_details",
                     headers=AUTH,
                     json={"card": {"holder": "TEST C", "number": "4111 1111 1111 1111",
                                    "expiry": "10/31", "cvv": "111"}})
    assert r3.status_code == 409
    assert r3.json()["detail"]["reason"] == "case_busy"


def test_erasure_destroys_a_vaulted_card(client, live_route, db):  # noqa: F811
    case = _new_case(client)
    r = client.post(f"/cases/{case['id']}/signals/provide_payment_details",
                    headers=AUTH,
                    json={"card": {"holder": "TEST APPLICANT",
                                   "number": "4111 1111 1111 1111",
                                   "expiry": "12/33", "cvv": "123"}})
    run = db.get(models.PortalRun, r.json()["run_id"])
    ref = run.signal_kwargs["card_ref"]
    assert client.delete(f"/cases/{case['id']}", headers=AUTH).status_code == 200
    with pytest.raises(KeyError):
        vault.reveal(ref)          # ciphertext must not survive erasure


def test_non_dict_card_is_refused_without_echo(client, live_route):  # noqa: F811
    case = _new_case(client)
    r = client.post(f"/cases/{case['id']}/signals/provide_payment_details",
                    headers=AUTH, json={"card": "4111111111111111"})
    assert r.status_code == 422
    assert r.json()["detail"]["reason"] == "invalid_payment_details"
    assert "4111111111111111" not in r.text


# ---------- progress vocabulary ------------------------------------------------

def test_payment_details_progress_steps_are_applicant_safe():
    assert progress.HANDOFF_STEPS["payment_credentials"] == "waiting_payment_details"
    for key in ("waiting_payment_details", "filling_payment_details"):
        msg = progress.STEP_MESSAGES[key]
        assert msg and "kimi" not in msg.lower()
        assert "#" not in msg and "css" not in msg.lower()
