"""Two field shapes Ellis could see but not answer, pinned as regressions.

Thailand's TDAC asks Date of Birth as THREE dropdowns (yyyy / mm / dd) under
one caption, and Gender as a radio group. Read as ordinary single controls,
the date bound only its year box — with a whole ISO date typed into it — and
the radio group matched no field at all, because a radio's own label is its
ANSWER ("FEMALE"), never the question the field asks ("Gender"). Both are
mandatory; the form does not continue without them.
"""
import pytest

from app.adapter_factory.specgen import (
    _date_part, _date_pattern, _deterministic_mapper, _entry_gated_flow,
    _page_roles, _radio_options, _skeleton_flow)
from app.adapter_factory.schema import validate_flow

HOST = "tdac.immigration.go.th"

# The shapes recon records for TDAC's Personal Information block.
DOB_PARTS = [
    {"selector": "#mat-input-18", "name": "mat-input-18",
     "label": "Date of Birth", "placeholder": "yyyy", "type": "select"},
    {"selector": "#mat-input-19", "name": "mat-input-19",
     "label": "Date of Birth", "placeholder": "mm", "type": "select"},
    {"selector": "#mat-input-20", "name": "mat-input-20",
     "label": "Date of Birth", "placeholder": "dd", "type": "select"},
]
GENDER_RADIOS = [
    {"selector": "#mat-radio-2-input", "name": "mat-radio-group-0",
     "label": "FEMALE", "option_label": "FEMALE", "group_label": "Gender",
     "group_key": "mat-radio-group-0", "type": "radio", "required": True},
    {"selector": "#mat-radio-3-input", "name": "mat-radio-group-0",
     "label": "MALE", "option_label": "MALE", "group_label": "Gender",
     "group_key": "mat-radio-group-0", "type": "radio", "required": True},
    {"selector": "#mat-radio-4-input", "name": "mat-radio-group-0",
     "label": "UNDEFINED", "option_label": "UNDEFINED", "group_label": "Gender",
     "group_key": "mat-radio-group-0", "type": "radio", "required": True},
]
SUBMIT = [{"selector": "#go", "name": "go", "label": "Continue", "type": "submit"}]


class _Art:
    def __init__(self, page_key, elements, art_id="art-1"):
        self.id = art_id
        self.page_key = page_key
        self.content_class = "public_page"
        self.structure = {"url_pattern": f"https://{HOST}/arrival-card",
                          "elements": elements}


def _art(*groups):
    els = []
    for g in groups:
        els.extend(g)
    return _Art("application_form", els)


# ---- the page's own statement of what each box wants -----------------------

@pytest.mark.parametrize("placeholder,expected", [
    ("yyyy", "YYYY"), ("mm", "MM"), ("dd", "DD"), ("YYYY", "YYYY"),
    ("Only letters A-Z are allowed", ""), ("", ""),
])
def test_a_box_that_asks_for_one_date_component_says_so(placeholder, expected):
    assert _date_part({"placeholder": placeholder}) == expected


def test_a_component_box_declares_a_format_the_runtime_can_render():
    """dates.to_portal already speaks these tokens, so the page's own word is
    the format — no guessing which part a box wants."""
    from app import dates
    for el, expected in zip(DOB_PARTS, ("1998", "05", "12")):
        assert dates.to_portal("1998-05-12", _date_pattern(el)) == expected


def test_a_full_mask_still_wins_over_a_component():
    assert _date_pattern({"placeholder": "DD/MM/YYYY"}) == "DD/MM/YYYY"


# ---- mapping ---------------------------------------------------------------

def test_every_part_of_a_split_date_is_mapped(db=None):
    """One field, three controls. Only the first ever bound, so the month and
    day boxes stayed empty on a mandatory question."""
    maps = _deterministic_mapper([_art(DOB_PARTS, SUBMIT)])
    dob = [m for m in maps if m["ellis_field"] == "birth_date"]
    assert [m["portal_field"] for m in dob] == [
        "mat-input-18", "mat-input-19", "mat-input-20"]


def test_a_radio_group_is_mapped_by_its_question_not_its_answers():
    """Tokenizing "FEMALE" as if it named a field matched nothing at all."""
    maps = _deterministic_mapper([_art(GENDER_RADIOS, SUBMIT)])
    assert [m["ellis_field"] for m in maps] == ["sex"]


def test_a_radio_group_carries_every_choice_the_page_showed():
    art = _art(GENDER_RADIOS, SUBMIT)
    opts = _radio_options(art, GENDER_RADIOS[0])
    assert [o["label"] for o in opts] == ["FEMALE", "MALE", "UNDEFINED"]
    assert [o["selector"] for o in opts] == [r["selector"] for r in GENDER_RADIOS]


def test_a_radio_group_without_citable_choices_maps_nothing():
    """No observed options means no answer Ellis could click — and it will
    not invent a selector for one."""
    orphan = [{"selector": "#lone", "name": "", "label": "MALE",
               "group_label": "Gender", "type": "radio"}]
    assert _radio_options(_art(orphan, SUBMIT), orphan[0]) == []


# ---- flow emission, in BOTH builders ---------------------------------------
# A portal reaches exactly one of these. Patching one and not the other is
# how Malaysia kept typing ISO dates after Vietnam was fixed.

def _flow(builder):
    art = _art(DOB_PARTS, GENDER_RADIOS, SUBMIT)
    maps = _deterministic_mapper([art])
    for m in maps:
        m["kind"] = "text"
        m["mandatory"] = True
    roles = _page_roles({"application_form": art})
    if builder is _skeleton_flow:
        return _skeleton_flow(HOST, roles, maps)
    return _entry_gated_flow(HOST, roles, maps, entry_gate={},
                             document_mappings=[])


@pytest.mark.parametrize("builder", [_skeleton_flow, _entry_gated_flow])
def test_each_date_component_gets_its_own_format(builder):
    nodes = [n for n in _flow(builder) if n.get("input_source") == "birth_date"]
    assert [n.get("format") for n in nodes] == ["YYYY", "MM", "DD"]
    assert [n["selector"] for n in nodes] == [
        "#mat-input-18", "#mat-input-19", "#mat-input-20"]


@pytest.mark.parametrize("builder", [_skeleton_flow, _entry_gated_flow])
def test_a_radio_group_becomes_one_node_carrying_its_options(builder):
    nodes = [n for n in _flow(builder) if n["action"] == "SELECT_RADIO"]
    assert len(nodes) == 1
    assert nodes[0]["input_source"] == "sex"
    assert [o["label"] for o in nodes[0]["options"]] == [
        "FEMALE", "MALE", "UNDEFINED"]


@pytest.mark.parametrize("builder", [_skeleton_flow, _entry_gated_flow])
def test_the_generated_flow_still_validates(builder):
    assert validate_flow(_flow(builder), allowed_hostnames=[HOST]) == []


def test_a_radio_node_without_options_is_refused_by_the_schema():
    """The schema is the last guard: a SELECT_RADIO Ellis cannot answer must
    never reach the runtime looking valid."""
    bad = [{"node_id": "fill_sex", "action": "SELECT_RADIO",
            "allowed_hostname": HOST, "selector": "#x", "input_source": "sex"}]
    errs = validate_flow(bad, allowed_hostnames=[HOST])
    assert any("requires observed options" in e for e in errs), errs


# ---- "Other, please specify" companions ------------------------------------

@pytest.mark.parametrize("el", [
    {"name": "traPurposeOTH", "label": ""},
    {"name": "tranModeOTH", "label": ""},
    {"name": "purpose_other", "label": ""},
    {"name": "occupation", "label": "Other (please specify)"},
    {"name": "x", "label": "If other, specify"},
])
def test_a_specify_other_box_is_recognised(el):
    from app.adapter_factory.specgen import is_specify_other_field
    assert is_specify_other_field(el)


@pytest.mark.parametrize("el", [
    {"name": "occupation", "label": "Occupation"},
    {"name": "motherName", "label": "Mother's Name"},
    {"name": "brother", "label": "Brother"},
    {"name": "otherNames", "label": "Other Names"},
])
def test_an_ordinary_field_is_not_mistaken_for_one(el):
    from app.adapter_factory.specgen import is_specify_other_field
    assert not is_specify_other_field(el)


# ---- one answer, several boxes, ONE round trip ------------------------------
# Thailand asks Date of Birth as three dropdowns. Walked one node at a time
# over a remote browser they cost ~17.5s each and killed the run three times
# on the applicant's own attempt (2026-08-03). They are the same answer
# rendered three ways, so they are committed together.

class _RecordingDriver:
    """Counts what the runtime asks the browser to do."""

    def __init__(self, *, many_ok=True):
        self.batches, self.singles, self.reads, self.clicked = [], [], [], []
        self._many_ok = many_ok

    def click(self, selector):
        self.clicked.append(selector)
        return {"ok": True}

    def select_search_many(self, fields, budget_ms=1500):
        self.batches.append(list(fields))
        ok = self._many_ok
        return [{"ok": ok, "shown": v} if ok else
                {"ok": False, "code": "NO_OPTIONS"} for _, v in fields]

    def select_search(self, selector, value):
        self.singles.append((selector, value))
        return {"ok": True, "shown": value}

    def read_value(self, selector):
        self.reads.append(selector)
        return {"ok": True, "value": ""}

    def fill(self, selector, value):
        self.singles.append((selector, value))
        return {"ok": True}


class _NoFastPath(_RecordingDriver):
    """A driver from before select_search_many existed."""
    select_search_many = None

    def __getattr__(self, name):
        raise AttributeError(name)


def _runner(driver, nodes, answers):
    from app.adapter_factory.runtime import CompiledFlow, FlowRunner

    class _Exec:
        id = "x"; application_id = "a"; candidate_id = "c"; candidate_version = 1
        current_node = ""; status = "running"; org_id = "o"

    class _DB:
        def commit(self): pass
        def add(self, *_a, **_k): pass
        def execute(self, *_a, **_k): raise RuntimeError("no db in this test")

    r = FlowRunner.__new__(FlowRunner)
    r.db, r.execution, r.driver = _DB(), _Exec(), driver
    r.flow = CompiledFlow(nodes, [n["node_id"] for n in nodes],
                          {"allowed_hostnames": [HOST]})
    r.answers, r.documents = answers, []
    r.observed_options, r._rejected_fills = {}, set()
    r._portal_field_messages, r._grouped_done = {}, set()
    r._repair_attempted = False
    r._deferred_fills, r._deferred_asked = [], set()
    r._unfillable = set()
    r._declaration_ticked_nodes = set()
    r.on_progress = None
    r.fee_seen, r.slots_seen = None, []
    return r


def _dob_nodes():
    return [{"node_id": f"fill_{n}", "action": "SELECT_SEARCH",
             "allowed_hostname": HOST, "selector": f"#{n}",
             "input_source": "birth_date", "format": f}
            for n, f in (("y", "YYYY"), ("m", "MM"), ("d", "DD"))]


def test_a_split_date_is_committed_in_one_call():
    drv = _RecordingDriver()
    r = _runner(drv, _dob_nodes(), {"birth_date": "1988-06-13"})
    out = r._step(r.flow.nodes["fill_y"])
    assert out["status"] == "ok"
    assert drv.batches == [[("#y", "1988"), ("#m", "06"), ("#d", "13")]]
    assert drv.singles == [], "no box was typed into on its own"


def test_the_sibling_boxes_are_not_typed_again():
    drv = _RecordingDriver()
    r = _runner(drv, _dob_nodes(), {"birth_date": "1988-06-13"})
    r._step(r.flow.nodes["fill_y"])
    for nid in ("fill_m", "fill_d"):
        out = r._step(r.flow.nodes[nid])
        assert out["status"] == "ok"
        assert out["detail"]["grouped_with_previous"] is True
    assert len(drv.batches) == 1 and drv.singles == []


def test_a_batch_that_could_not_commit_falls_back_to_the_proven_path():
    """A partly-filled date is a wrong date. The fast path is never allowed
    to report what it managed."""
    drv = _RecordingDriver(many_ok=False)
    r = _runner(drv, _dob_nodes(), {"birth_date": "1988-06-13"})
    out = r._step(r.flow.nodes["fill_y"])
    assert drv.singles and drv.singles[0] == ("#y", "1988")
    assert r._grouped_done == set(), "siblings must still be filled one by one"


def test_a_driver_without_the_fast_path_still_fills_every_box():
    drv = _NoFastPath()
    r = _runner(drv, _dob_nodes(), {"birth_date": "1988-06-13"})
    for nid in ("fill_y", "fill_m", "fill_d"):
        assert r._step(r.flow.nodes[nid])["status"] == "ok"
    assert drv.singles == [("#y", "1988"), ("#m", "06"), ("#d", "13")]


def test_unrelated_neighbouring_selects_are_never_grouped():
    """Only boxes filling the SAME answer are one question. Grouping two
    different fields would type one answer into both."""
    nodes = [{"node_id": "fill_nat", "action": "SELECT_SEARCH", "selector": "#nat",
              "allowed_hostname": HOST, "input_source": "nationality"},
             {"node_id": "fill_city", "action": "SELECT_SEARCH", "selector": "#city",
              "allowed_hostname": HOST, "input_source": "address_city"}]
    drv = _RecordingDriver()
    r = _runner(drv, nodes, {"nationality": "CHN", "address_city": "Guangzhou"})
    r._step(r.flow.nodes["fill_nat"])
    assert drv.batches == [] and drv.singles == [("#nat", "CHN")]


def test_a_combobox_is_never_reported_set_from_its_own_typed_text():
    """read_value on a search combobox returns whatever was last TYPED into
    it, committed or not. Trusting it laundered three failed date selections
    into successes and left the form's date of birth unset."""
    class _Leftover(_NoFastPath):
        def read_value(self, selector):
            self.reads.append(selector)
            return {"ok": True, "value": "1988"}     # the query, not a choice

    drv = _Leftover()
    r = _runner(drv, _dob_nodes(), {"birth_date": "1988-06-13"})
    out = r._step(r.flow.nodes["fill_y"])
    assert out["status"] == "ok"
    assert out.get("detail", {}).get("already_set") is not True
    assert drv.singles == [("#y", "1988")], "the box must be genuinely committed"


# ---- a control that opens no list is not a dropdown -------------------------
# TDAC's Date of Birth boxes are placeheld yyyy/mm/dd and carry combobox ARIA,
# so recon typed them "select" and specgen built SELECT_SEARCH. At run time
# all three read ZERO options and ended the application three times over one
# date — while Nationality, on the same page and the same widget class,
# committed in four seconds. Recon now asks the page which it is.

@pytest.mark.parametrize("probe,expected", [
    ("options", "SELECT_SEARCH"),   # the page opened a list: a real dropdown
    ("empty", "FILL_NON_SENSITIVE"),  # it opened nothing: a box you type in
    ("unknown", "SELECT_SEARCH"),   # unreachable: keep the ARIA reading
    (None, "SELECT_SEARCH"),        # never probed: keep the ARIA reading
])
def test_the_page_decides_whether_a_field_is_a_dropdown(probe, expected):
    from app.adapter_factory.specgen import fill_action_for
    el = {"type": "select"}
    if probe is not None:
        el["opens_list"] = probe
    assert fill_action_for(el, "select") == expected


def test_a_plain_text_field_is_never_upgraded_to_a_dropdown():
    from app.adapter_factory.specgen import fill_action_for
    assert fill_action_for({"opens_list": "options"}, "text") == "FILL_NON_SENSITIVE"


@pytest.mark.parametrize("builder", [_skeleton_flow, _entry_gated_flow])
def test_both_builders_honour_the_probe(builder):
    """Patching one builder and not the other is how Malaysia kept typing ISO
    dates after Vietnam was fixed."""
    parts = [dict(p, opens_list="empty") for p in DOB_PARTS]
    art = _Art("application_form", parts + SUBMIT)
    maps = _deterministic_mapper([art])
    for m in maps:
        m["kind"] = "text"
    roles = _page_roles({"application_form": art})
    nodes = _skeleton_flow(HOST, roles, maps) if builder is _skeleton_flow \
        else _entry_gated_flow(HOST, roles, maps, entry_gate={},
                               document_mappings=[])
    dob = [n for n in nodes if n.get("input_source") == "birth_date"]
    assert len(dob) == 3
    assert {n["action"] for n in dob} == {"FILL_NON_SENSITIVE"}
    # The per-part date format must survive the downgrade, or the year box
    # gets a whole ISO date typed into it.
    assert [n.get("format") for n in dob] == ["YYYY", "MM", "DD"]


def test_the_recon_sanitizer_keeps_the_verdict():
    from app.adapter_factory.recon import sanitize_structure
    out = sanitize_structure({"elements": [
        {"selector": "#a", "name": "a", "label": "Year", "type": "select",
         "opens_list": "empty"},
        {"selector": "#b", "name": "b", "label": "Nationality", "type": "select",
         "opens_list": "options"},
        {"selector": "#c", "name": "c", "label": "X", "type": "select",
         "opens_list": "<script>"},          # not a verdict: dropped
    ]})
    els = {e["name"]: e for e in out["elements"]}
    assert els["a"]["opens_list"] == "empty"
    assert els["b"]["opens_list"] == "options"
    assert "opens_list" not in els["c"]


# ---- the model is a proposer, not the only one ------------------------------
# Thailand v21 mapped Date of Birth; v22, from a BETTER observation of the
# same page, did not — the field just went missing from the model's reply, and
# a mandatory question on a government form stopped being filled because a
# model varied. The deterministic name-hint mapper finds those three boxes
# every time, so it is the floor under the reply.

def test_a_field_the_model_omitted_is_still_mapped():
    from app.adapter_factory.specgen import _with_deterministic_floor
    art = _art(DOB_PARTS, SUBMIT)
    model_said = [{"ellis_field": "surname", "portal_field": "familyName",
                   "selector": "#familyName", "page_key": "application_form",
                   "artifact_id": art.id, "required": True}]
    out = _with_deterministic_floor(model_said, [art])
    assert [m["portal_field"] for m in out if m["ellis_field"] == "birth_date"] == [
        "mat-input-18", "mat-input-19", "mat-input-20"]


def test_the_model_still_wins_where_it_has_an_opinion():
    """The floor is additive. A portal field the model mapped is the model's,
    even where the name hints would have read it differently."""
    from app.adapter_factory.specgen import _with_deterministic_floor
    art = _art(DOB_PARTS, SUBMIT)
    model_said = [{"ellis_field": "expiry_date", "portal_field": "mat-input-18",
                   "selector": "#mat-input-18", "page_key": "application_form",
                   "artifact_id": art.id, "required": True}]
    out = _with_deterministic_floor(model_said, [art])
    for m in out:
        if m["portal_field"] == "mat-input-18":
            assert m["ellis_field"] == "expiry_date"
    assert sum(1 for m in out if m["portal_field"] == "mat-input-18") == 1


def test_the_floor_invents_nothing():
    """It can only add fields the PAGE has. A floor that could smuggle in an
    unobserved field would be worse than the gap it fills."""
    from app.adapter_factory.specgen import _with_deterministic_floor
    art = _art(SUBMIT)          # a page with no fillable fields at all
    assert _with_deterministic_floor([], [art]) == []


# ---- fill what you can, then come back and ask ------------------------------
# Thailand met a blank Occupation five fields in and stopped dead, leaving
# Gender, residence and the whole trip section empty. The applicant saw a
# half-filled form and one question at a time — and when the pause carried no
# question at all, its Continue re-drove straight back into the same pause.

def _page(*names, click=True):
    nodes = [{"node_id": f"fill_{n}", "action": "FILL_NON_SENSITIVE",
              "allowed_hostname": HOST, "selector": f"#{n}",
              "input_source": n, "mandatory": True} for n in names]
    if click:
        nodes.append({"node_id": "next", "action": "CLICK",
                      "allowed_hostname": HOST, "selector": "#next"})
    return nodes


def test_a_gap_does_not_stop_the_rest_of_the_page():
    drv = _RecordingDriver()
    nodes = _page("surname", "occupation", "sex")
    r = _runner(drv, nodes, {"surname": "CAO", "sex": "M"})
    for nid in ("fill_surname", "fill_occupation", "fill_sex"):
        assert r._step(r.flow.nodes[nid])["status"] == "ok"
    assert [s for s, _ in drv.singles] == ["#surname", "#sex"], \
        "the fields Ellis could fill must all be filled"
    assert r._deferred_fills == ["fill_occupation"]


def test_the_gaps_are_asked_together_before_the_form_advances():
    drv = _RecordingDriver()
    nodes = _page("surname", "occupation", "employer")
    r = _runner(drv, nodes, {"surname": "CAO"})
    for nid in ("fill_surname", "fill_occupation", "fill_employer"):
        r._step(r.flow.nodes[nid])
    out = r._step(r.flow.nodes["next"])
    assert out["status"] == "handoff"
    assert out["handoff_kind"] == "additional_information"
    assert [q["key"] for q in out["questions"]] == ["occupation", "employer"]
    assert out["resume_node"] == "fill_occupation", \
        "the resume must rewind to the first gap so the answers get typed in"
    assert "#next" not in [s for s, _ in drv.singles] and not drv.clicked, \
        "the form must not be advanced with holes in it"


def test_answering_lets_the_deferred_field_fill_normally():
    drv = _RecordingDriver()
    nodes = _page("surname", "occupation")
    r = _runner(drv, nodes, {"surname": "CAO"})
    r._step(r.flow.nodes["fill_occupation"])
    r._step(r.flow.nodes["next"])
    r.answers["occupation"] = "Engineer"
    assert r._step(r.flow.nodes["fill_occupation"])["status"] == "ok"
    assert ("#occupation", "Engineer") in drv.singles


def test_a_question_the_applicant_declines_never_loops():
    """Asked once. Still blank on the resume, it pauses on the spot rather
    than deferring again forever."""
    drv = _RecordingDriver()
    nodes = _page("surname", "occupation")
    r = _runner(drv, nodes, {"surname": "CAO"})
    r._step(r.flow.nodes["fill_occupation"])
    r._step(r.flow.nodes["next"])          # asked
    out = r._step(r.flow.nodes["fill_occupation"])   # still unanswered
    assert out["status"] == "handoff"
    assert out["handoff_kind"] == "additional_information"


def test_an_optional_gap_is_never_asked_about():
    drv = _RecordingDriver()
    nodes = _page("surname")
    nodes.insert(1, {"node_id": "fill_nickname", "action": "FILL_NON_SENSITIVE",
                     "allowed_hostname": HOST, "selector": "#nickname",
                     "input_source": "nickname", "mandatory": False})
    r = _runner(drv, nodes, {"surname": "CAO"})
    r._step(r.flow.nodes["fill_nickname"])
    assert r._deferred_fills == []
    assert r._step(r.flow.nodes["next"])["status"] == "ok"


def test_a_page_with_no_gaps_advances_untouched():
    drv = _RecordingDriver()
    nodes = _page("surname", "occupation")
    r = _runner(drv, nodes, {"surname": "CAO", "occupation": "Engineer"})
    for nid in ("fill_surname", "fill_occupation"):
        r._step(r.flow.nodes[nid])
    assert r._step(r.flow.nodes["next"])["status"] == "ok"


def test_a_field_ellis_cannot_operate_does_not_end_the_run():
    """Malaysia's region select refused five times at eight seconds each and
    took the whole application down, on a form where every other field had
    already been filled. Filling a form is not all-or-nothing."""
    class _Stubborn(_RecordingDriver):
        def fill(self, selector, value):
            if selector == "#region":
                return {"ok": False, "code": "VALUE_NOT_ACCEPTED",
                        "detail": "the portal kept ''"}
            self.singles.append((selector, value))
            return {"ok": True}

    nodes = _page("surname", "region", "email")
    drv = _Stubborn()
    r = _runner(drv, nodes, {"surname": "CAO", "region": "Selangor",
                             "email": "a@b.c"})
    for nid in ("fill_surname", "fill_region", "fill_email"):
        assert r._step(r.flow.nodes[nid])["status"] == "ok"
    assert [s for s, _ in drv.singles] == ["#surname", "#email"]
    out = r._step(r.flow.nodes["next"])
    assert out["status"] == "handoff"
    # Ellis HOLDS the region answer; the field refused it. That is a request to
    # finish the field yourself, not a request for information Ellis already
    # has — and it names the field in the applicant's own words.
    assert out["handoff_kind"] == "portal_form"
    assert not out.get("questions")
    assert "region" in " ".join(out["portal_messages"])


def test_a_field_that_fails_again_goes_to_the_secure_window_not_a_loop():
    class _Stubborn(_RecordingDriver):
        def fill(self, selector, value):
            return {"ok": False, "code": "VALUE_NOT_ACCEPTED", "detail": "no"}

    nodes = _page("region")
    drv = _Stubborn()
    r = _runner(drv, nodes, {"region": "Selangor"})
    assert r._step(r.flow.nodes["fill_region"])["status"] == "ok"   # deferred
    r._step(r.flow.nodes["next"])                                   # asked
    out = r._step(r.flow.nodes["fill_region"])                      # still refuses
    assert out["status"] == "handoff" and out["handoff_kind"] == "portal_form"


# ---- what Ellis already knows is not a question -----------------------------
# The applicant was shown a dialog asking for their arrival date, travel
# purpose, departure date and address — every box PRE-FILLED with the answer
# Ellis already held — and the reason given was
#   'Page.fill: Timeout 2500ms exceeded. Call log: - waiting for
#    locator("input[formcontrolname=\"arrDate\"]")'
# Those fields did not lack an answer; Ellis could not reach them.

def test_a_field_ellis_cannot_reach_is_not_asked_about():
    class _Unreachable(_RecordingDriver):
        def fill(self, selector, value):
            if selector == "#arrdate":
                return {"ok": False, "code": "VALUE_NOT_ACCEPTED",
                        "detail": 'Page.fill: Timeout 2500ms exceeded. Call log: '
                                  '- waiting for locator("input[formcontrolname=\\"arrDate\\"]")'}
            self.singles.append((selector, value))
            return {"ok": True}

    nodes = _page("surname", "arrdate")
    drv = _Unreachable()
    r = _runner(drv, nodes, {"surname": "CAO", "arrdate": "2026-08-04"})
    for nid in ("fill_surname", "fill_arrdate"):
        r._step(r.flow.nodes[nid])
    out = r._step(r.flow.nodes["next"])
    assert out["status"] == "handoff"
    assert out["handoff_kind"] == "portal_form", \
        "Ellis has the answer — this is not an information request"
    assert not out.get("questions")


def test_no_applicant_message_ever_carries_automation_internals():
    from app.adapter_factory.runtime import _applicant_safe_message
    leaks = [
        'Page.fill: Timeout 2500ms exceeded. Call log: - waiting for locator("x")',
        'waiting for locator("input[formcontrolname=\\"arrDate\\"]")',
        "Timeout 2500ms exceeded",
        'selector "#mat-input-18" not found',
        "Traceback (most recent call last)",
        "#dob >> visible=true",
    ]
    for t in leaks:
        assert _applicant_safe_message(t) == "", t
    # The portal's OWN words still come through.
    for t in ("Date must be in DD/MM/YYYY format",
              "This field is required",
              "Please enter your occupation"):
        assert _applicant_safe_message(t) == t


def test_the_unreachable_message_names_fields_in_plain_words():
    class _Unreachable(_RecordingDriver):
        def fill(self, selector, value):
            return {"ok": False, "code": "VALUE_NOT_ACCEPTED", "detail": "locator(...)"}

    nodes = _page("arrdate")
    nodes[0]["question"] = {"question": "What is your arrival date?"}
    r = _runner(_Unreachable(), nodes, {"arrdate": "2026-08-04"})
    r._step(r.flow.nodes["fill_arrdate"])
    out = r._step(r.flow.nodes["next"])
    msg = " ".join(out["portal_messages"])
    assert "arrival date" in msg
    for banned in ("locator", "Timeout", "formcontrolname", "#", "selector"):
        assert banned not in msg, msg


def test_the_ask_is_remembered_across_a_new_runner():
    """A new FlowRunner is built for every segment. Runner-local memory alone
    re-asked the same four fields three times in ninety seconds and the
    applicant never got to answer any of them."""
    from app.adapter_factory.runtime import FlowRunner
    seen = {"rows": []}

    class _DB:
        def commit(self): pass
        def add(self, row): seen["rows"].append((row.node_id, row.status))
        def execute(self, *a, **k):
            class R:
                def scalars(self_inner):
                    class S:
                        def all(s2): return [n for n, st in seen["rows"]
                                             if st == FlowRunner.ASKED_STATUS]
                    return S()
            return R()

    drv = _RecordingDriver()
    nodes = _page("occupation")
    r = _runner(drv, nodes, {})
    r.db = _DB()
    r._step(r.flow.nodes["fill_occupation"])
    out = r._step(r.flow.nodes["next"])
    assert [q["key"] for q in out["questions"]] == ["occupation"]
    # A FRESH runner over the same execution must know it was already asked.
    r2 = _runner(drv, nodes, {})
    r2.db = _DB()
    r2._deferred_asked = r2._already_asked()
    assert "fill_occupation" in r2._deferred_asked
