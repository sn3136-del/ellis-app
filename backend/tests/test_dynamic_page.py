"""The dynamic read->ask->transcribe cycle: opt-in, ceremony-safe, and it
never guesses — a page's unanswered question pauses as an ask, a control
that refuses an answer re-asks it, and review/sign/captcha are never walked."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.portal import dynamic_page as dyn


class _Driver:
    def __init__(self, fields=None, has_file=False, captcha=False,
                 advance_ok=True, fill_ok=True):
        self._fields = fields or []
        self._has_file = has_file
        self._captcha = captcha
        self._advance_ok = advance_ok
        self._fill_ok = fill_ok
        self.filled, self.advanced = [], 0
    def captcha_state(self, highlight=False):
        return {"ok": True, "present": self._captcha}
    def read_dynamic_questions(self):
        return {"ok": True, "fields": self._fields,
                "has_file_input": self._has_file}
    def select_radio(self, options, value):
        self.filled.append(("radio", value)); return {"ok": self._fill_ok}
    def select_search(self, selector, value):
        self.filled.append(("select", value)); return {"ok": self._fill_ok}
    def fill_observed_field(self, selector, value, kind="text"):
        self.filled.append(("text", value)); return {"ok": self._fill_ok}
    def click_next_button(self):
        self.advanced += 1
        # after advancing, the next page has nothing dynamic
        self._fields = []
        return {"ok": self._advance_ok}


RADIO = {"kind": "radio", "name": "ctl00_Foo_rblDisease",
         "question": "Do you have a communicable disease?",
         "options": [{"label": "Yes", "selector": "[id=y]"},
                     {"label": "No", "selector": "[id=n]"}]}


def test_only_opted_in_adapters_are_dynamic():
    assert dyn.enabled({"adapter_id": "usa-ceac-ds160"})
    assert not dyn.enabled({"adapter_id": "vnm-c104cae553"})
    assert not dyn.enabled({})


def test_review_sign_and_ceremony_kinds_are_never_walked():
    d = _Driver(fields=[RADIO])
    for node in ({"node_id": "review_handoff", "handoff_kind": "legally_personal_declaration"},
                 {"node_id": "sign_handoff", "handoff_kind": "legally_personal_declaration"},
                 {"node_id": "x", "handoff_kind": "captcha"},
                 {"node_id": "x", "handoff_kind": "credentials"},
                 {"node_id": "x", "handoff_kind": "payment"}):
        out = dyn.cycle(d, node=node, answers={"ds160_rblDisease": "No"})
        assert out["status"] == "pass"
    assert d.filled == [] and d.advanced == 0


def test_a_missing_answer_is_asked_never_guessed():
    d = _Driver(fields=[RADIO])
    out = dyn.cycle(d, node={"node_id": "h", "handoff_kind": "legally_personal_declaration"},
                    answers={})
    assert out["status"] == "ask"
    assert out["questions"][0]["key"] == "ds160_rblDisease"
    assert out["questions"][0]["options"] == ["Yes", "No"]
    assert d.filled == [] and d.advanced == 0


def test_a_stored_answer_is_transcribed_and_the_page_advanced():
    d = _Driver(fields=[RADIO])
    out = dyn.cycle(d, node={"node_id": "h", "handoff_kind": "legally_personal_declaration"},
                    answers={"ds160_rblDisease": "No"})
    assert out["status"] == "ok" and out["pages_walked"] == 1
    assert ("radio", "No") in d.filled and d.advanced == 1


def test_a_refused_answer_is_reasked_not_forced():
    d = _Driver(fields=[RADIO], fill_ok=False)
    out = dyn.cycle(d, node={"node_id": "h", "handoff_kind": "legally_personal_declaration"},
                    answers={"ds160_rblDisease": "Maybe"})
    assert out["status"] == "ask"
    assert d.advanced == 0


def test_a_captcha_page_passes_to_the_normal_handoff():
    d = _Driver(fields=[RADIO], captcha=True)
    out = dyn.cycle(d, node={"node_id": "h", "handoff_kind": "legally_personal_declaration"},
                    answers={"ds160_rblDisease": "No"})
    assert out["status"] == "pass"


def test_an_upload_handoff_on_its_own_page_passes():
    d = _Driver(fields=[], has_file=True)
    out = dyn.cycle(d, node={"node_id": "photo_upload_handoff",
                             "handoff_kind": "document_upload"}, answers={})
    assert out["status"] == "pass"
