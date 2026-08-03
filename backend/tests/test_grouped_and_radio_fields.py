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
