"""Regional and transit policy notes: the store the 2026-08-31 Trip.com
evaluation showed was missing. A question naming Hainan or visa-free transit
must surface the verified note, a route the policy covers must carry it, and
an entry without a government source must never load."""
import json

import pytest

from app.visa_snapshot import special_policies


ENTRIES = [
    {"id": "test-hainan", "destination": "CHN", "region": "Hainan",
     "triggers": ["hainan", "海南", "sanya", "三亚"],
     "applies_to": ["IND", "USA"],
     "title_en": "Hainan regional visa-free entry",
     "title_zh": "海南入境免签",
     "summary_en": "Covered nationalities can enter Hainan without a visa.",
     "summary_zh": "适用国籍可免签入境海南。",
     "source_url": "https://www.nia.gov.cn/example",
     "verified_at": "2026-09-01", "verified_by": "test"},
    {"id": "test-transit", "destination": "CHN", "region": "",
     "triggers": ["transit visa-free", "过境免签", "144", "240"],
     "applies_to": ["USA"],
     "title_en": "China visa-free transit",
     "title_zh": "中国过境免签",
     "summary_en": "Eligible nationalities may transit without a visa.",
     "summary_zh": "符合条件的国籍可免签过境。",
     "source_url": "https://www.nia.gov.cn/example2",
     "verified_at": "2026-09-01", "verified_by": "test"},
    {"id": "test-ungated", "destination": "CHN",
     "triggers": ["hainan"], "applies_to": [],
     "title_en": "Never loads", "summary_en": "Commercial source.",
     "source_url": "https://www.ivisa.com/hainan",
     "verified_at": "2026-09-01", "verified_by": "test"},
]


@pytest.fixture()
def seed(tmp_path, monkeypatch):
    p = tmp_path / "special_policies.json"
    p.write_text(json.dumps(ENTRIES), encoding="utf-8")
    monkeypatch.setenv("ELLIS_SPECIAL_POLICIES", str(p))
    special_policies.reload()
    yield p
    special_policies.reload()


def test_non_government_sources_never_load(seed):
    ids = {e["id"] for e in special_policies.for_question("hainan rules?")}
    assert "test-hainan" in ids
    assert "test-ungated" not in ids


def test_questions_trigger_in_both_scripts(seed):
    assert special_policies.for_question("印度人去海南要签证吗")[0]["id"] == \
        "test-hainan"
    assert special_policies.for_question(
        "what is the 144-hour transit policy")[0]["id"] == "test-transit"
    assert special_policies.for_question("do I need a visa for japan") == []


def test_routes_carry_only_policies_that_cover_them(seed):
    ind = {"passport_nationality": "IND", "destination_country": "CHN"}
    usa = {"passport_nationality": "USA", "destination_country": "CHN"}
    jpn_dest = {"passport_nationality": "IND", "destination_country": "JPN"}
    assert [e["id"] for e in special_policies.for_route(ind)] == ["test-hainan"]
    assert {e["id"] for e in special_policies.for_route(usa)} == \
        {"test-hainan", "test-transit"}
    assert special_policies.for_route(jpn_dest) == []


def test_attach_says_whether_the_asker_is_covered(seed):
    """The decisive half of "does Hainan visa-free apply to me": a known
    nationality gets a yes or no against the eligible list, not just a
    description of the program."""
    ind = special_policies.attach(
        {}, question="hainan?", route={"passport_nationality": "VNM",
                                       "destination_country": "CHN"})
    note = ind["special_policies"][0]
    assert note["applies_to_you"] is False
    usa = special_policies.attach(
        {}, question="hainan?", route={"passport_nationality": "USA",
                                       "destination_country": "CHN"})
    assert usa["special_policies"][0]["applies_to_you"] is True
    anon = special_policies.attach({}, question="hainan?")
    assert "applies_to_you" not in anon["special_policies"][0]


def test_attach_merges_question_and_route_notes_once(seed):
    out = special_policies.attach(
        {"answer": 1},
        question="can I visit hainan?",
        route={"passport_nationality": "IND", "destination_country": "CHN"})
    ids = [e["id"] for e in out["special_policies"]]
    assert ids.count("test-hainan") == 1
    plain = special_policies.attach({"answer": 1}, question="visa for japan")
    assert "special_policies" not in plain


def test_a_trip_beyond_the_policy_window_is_flagged(seed, tmp_path,
                                                    monkeypatch):
    """A policy with a published end date, asked about for a later trip,
    says so. Deterministic date comparison, no guessing."""
    import json as _json
    p = tmp_path / "sp.json"
    rows = _json.loads((tmp_path / "special_policies.json").read_text()) \
        if (tmp_path / "special_policies.json").exists() else ENTRIES
    rows = [dict(r) for r in ENTRIES]
    rows[1]["valid_until"] = "2026-12-31"
    p.write_text(_json.dumps(rows), encoding="utf-8")
    monkeypatch.setenv("ELLIS_SPECIAL_POLICIES", str(p))
    special_policies.reload()
    out = special_policies.attach(
        {}, question="what is the 240 transit rule",
        route={"passport_nationality": "USA", "destination_country": "CHN",
               "arrival_date": "2027-03-01"})
    note = [n for n in out["special_policies"] if n["id"] == "test-transit"][0]
    assert note.get("beyond_verified_window") is True
    ok = special_policies.attach(
        {}, question="what is the 240 transit rule",
        route={"passport_nationality": "USA", "destination_country": "CHN",
               "arrival_date": "2026-10-01"})
    note2 = [n for n in ok["special_policies"] if n["id"] == "test-transit"][0]
    assert "beyond_verified_window" not in note2
    special_policies.reload()
