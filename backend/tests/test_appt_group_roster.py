"""The US group-appointment roster: the one sanctioned batch path, and the
proof that it stays a paperwork path.

Two families of tests live here.

The first pins the OFFICIAL rules, because getting them wrong costs a whole
tour group its dates: ten or more travelling together, families and relatives
excluded in the government's own words, a cap of fifty, and per member their
OWN 10-character DS-160 confirmation plus an MRV receipt. Every rejection has
to name who is missing what — a roster that only says "not ready" gives the
coordinator nobody to chase.

The second is the hard line. This module must contain no slot search, no
booking, and no portal automation whatsoever: automated slot search on the US
scheduler gets the TRAVELLER's appointment and visa cancelled. That is checked
three ways — the module's imports are audited, its call graph is audited, and
the whole roster-plus-artifact path is exercised with the socket layer torn
out, so an HTTP client added later fails these tests rather than a traveller.
"""
from __future__ import annotations

import ast
import inspect
import json
import re
import socket
from datetime import date

import pytest

from app import appt_group_roster as roster_mod
from app import models
from app.appt_group_roster import (
    GROUP_SIZE_CAP, MIN_GROUP_SIZE, OFFICIAL_FAMILY_EXCLUSION,
    OFFICIAL_MIN_SIZE, OFFICIAL_NO_AGENT_SCHEDULING, OFFICIAL_OWN_DS160,
    build_roster, expiry_status, export_group_request, record_expiration,
)

TODAY = date(2026, 8, 11)


def member(i, *, relation="tour_member", **overrides):
    """One well-formed tour-group member. Overrides break exactly one thing."""
    base = {
        "case_id": f"case{i:03d}",
        "name": f"TRAVELLER {i:02d}",
        "passport_number": f"E{i:08d}",
        "ds160_confirmation": f"AA00ABCD{i:02d}",
        "mrv_receipt": f"CITIC{i:09d}",
        "relationship": relation,
    }
    base.update(overrides)
    return base


def tour_group(n=12, **overrides):
    return [member(i, **overrides) for i in range(n)]


# ---------------------------------------------------------------------------
# Who qualifies for a group appointment at all.
# ---------------------------------------------------------------------------

def test_a_group_of_ten_or_more_travelling_together_qualifies():
    r = build_roster(None, tour_group(10), group_kind="tour_group", today=TODAY)
    assert r["eligible"] is True
    assert r["verdict"] == "eligible"
    assert r["reasons"] == []
    assert r["size"] == 10 and r["cap_ok"] is True
    assert r["ready_count"] == 10 and r["submittable"] is True


def test_fewer_than_ten_is_ineligible_in_the_official_wording():
    """Nine people are not a group. The reason has to carry the rule and the
    remedy, since each of them must now request an individual appointment."""
    r = build_roster(None, tour_group(9), group_kind="tour_group", today=TODAY)
    assert r["eligible"] is False
    assert r["verdict"] == "ineligible"
    assert r["size"] == 9
    reason = " ".join(r["reasons"])
    assert OFFICIAL_MIN_SIZE in reason
    assert "individual appointment" in reason
    assert MIN_GROUP_SIZE == 10


def test_a_family_group_is_refused_in_the_governments_own_words():
    """Twelve relatives are twelve people, not a group: the official rule
    excludes them explicitly, so Ellis must not let the coordinator find that
    out at the consulate."""
    relations = ["self", "spouse", "child", "child", "mother", "father",
                 "brother", "sister", "cousin", "uncle", "niece", "grandmother"]
    members = [member(i, relation=rel) for i, rel in enumerate(relations)]
    r = build_roster(None, members, today=TODAY)
    assert r["eligible"] is False
    assert r["verdict"] == "ineligible"
    assert r["family_group"] is True
    assert any(OFFICIAL_FAMILY_EXCLUSION in reason for reason in r["reasons"])


def test_a_declared_family_group_is_refused_however_large():
    r = build_roster(None, tour_group(30, relation=""), group_kind="family",
                     today=TODAY)
    assert r["eligible"] is False
    assert any(OFFICIAL_FAMILY_EXCLUSION in reason for reason in r["reasons"])


def test_a_shared_household_id_reads_as_a_family_group():
    members = [member(i, relation="", family_id="HH-1") for i in range(11)]
    r = build_roster(None, members, today=TODAY)
    assert r["family_group"] is True and r["eligible"] is False


def test_a_tour_group_that_happens_to_contain_a_couple_still_qualifies():
    """The exclusion is for a group that IS a family, not for a tour that has
    relatives in it — over-reading it would deny a qualifying group."""
    members = tour_group(12)
    members[3]["relationship"] = "spouse"
    members[4]["relationship"] = "child"
    r = build_roster(None, members, today=TODAY)
    assert r["family_group"] is False
    assert r["eligible"] is True


def test_composition_ellis_cannot_see_is_unverified_never_assumed_eligible():
    """No declared group type and no relationships means Ellis does not know
    whether the family exclusion applies. It says so instead of guessing an
    eligibility verdict."""
    r = build_roster(None, tour_group(12, relation=""), today=TODAY)
    assert r["verdict"] == "unverified"
    assert r["eligible"] is False
    assert r["family_group"] is None
    assert r["submittable"] is False
    reason = " ".join(r["reasons"])
    assert "cannot confirm" in reason and OFFICIAL_FAMILY_EXCLUSION in reason


def test_over_the_cap_is_refused_and_split_into_submittable_batches():
    r = build_roster(None, tour_group(62), group_kind="tour_group", today=TODAY)
    assert r["cap_ok"] is False
    assert r["eligible"] is False
    assert [len(b) for b in r["batches"]] == [50, 12]
    reason = " ".join(r["reasons"])
    assert f"capped at {GROUP_SIZE_CAP}" in reason and "Split it into 2" in reason
    assert GROUP_SIZE_CAP == 50


def test_exactly_fifty_is_still_within_the_cap():
    r = build_roster(None, tour_group(50), group_kind="tour_group", today=TODAY)
    assert r["cap_ok"] is True and r["eligible"] is True
    assert len(r["batches"]) == 1


# ---------------------------------------------------------------------------
# Per-member readiness: exactly who is missing exactly what.
# ---------------------------------------------------------------------------

def test_a_nine_character_ds160_confirmation_is_rejected_never_padded():
    """A confirmation number that is not ten characters points at nobody's
    form. It is reported by LENGTH and dropped, so it cannot be transcribed
    into the scheduler and cannot leak through the roster either."""
    members = tour_group(12)
    members[5]["ds160_confirmation"] = "123456789"
    r = build_roster(None, members, group_kind="tour_group", today=TODAY)

    bad = r["members"][5]
    assert bad["ready"] is False
    assert bad["ds160_confirmation"] == ""          # never passed on
    assert any("not 10 characters" in m and "9" in m for m in bad["missing"])
    assert "123456789" not in json.dumps(r)
    assert r["submittable"] is False
    assert r["not_ready"] == ["TRAVELLER 05"]
    assert r["ready_count"] == 11
    # The group itself still qualifies — this is a chase list, not a refusal.
    assert r["eligible"] is True


@pytest.mark.parametrize("value", ["AA00ABCD1", "AA00ABCD123", "AA00-ABCD1",
                                   "AA00ABCD1!", ""])
def test_only_a_ten_character_confirmation_is_accepted(value):
    r = build_roster(None, [member(0, ds160_confirmation=value)], today=TODAY)
    assert r["members"][0]["ds160_confirmation"] == ""
    assert r["members"][0]["ready"] is False


def test_a_confirmation_split_by_copy_paste_whitespace_is_still_read():
    """Whitespace is a copy artifact, not a different number; nothing else
    about the value is touched."""
    r = build_roster(None, [member(0, ds160_confirmation=" aa00 abcd12 ")],
                     today=TODAY)
    assert r["members"][0]["ds160_confirmation"] == "AA00ABCD12"


def test_a_missing_mrv_receipt_names_the_member_who_owes_it():
    members = tour_group(12)
    members[7]["mrv_receipt"] = ""
    r = build_roster(None, members, group_kind="tour_group", today=TODAY)

    short = r["members"][7]
    assert short["ready"] is False
    assert any("MRV fee receipt" in m for m in short["missing"])
    assert r["not_ready"] == ["TRAVELLER 07"]
    assert all(m["ready"] for i, m in enumerate(r["members"]) if i != 7)
    assert r["submittable"] is False


def test_a_garbage_mrv_receipt_is_reported_and_not_passed_on():
    r = build_roster(None, [member(0, mrv_receipt="???")], today=TODAY)
    assert r["members"][0]["mrv_receipt"] == ""
    assert any("MRV fee receipt number" in m for m in r["members"][0]["missing"])


def test_a_missing_passport_number_is_reported_as_absence_not_a_blank():
    r = build_roster(None, [member(0, passport_number="")], today=TODAY)
    assert r["members"][0]["passport_number_present"] is False
    assert "passport number" in r["members"][0]["missing"]


def test_two_members_sharing_one_ds160_breaks_both_of_them():
    """A shared confirmation number means somebody reused another traveller's
    form — the rule the scheduler states outright. Ellis cannot know which of
    the two is wrong, so it marks both."""
    members = tour_group(12)
    members[2]["ds160_confirmation"] = members[9]["ds160_confirmation"]
    r = build_roster(None, members, group_kind="tour_group", today=TODAY)

    for index, other in ((2, "TRAVELLER 09"), (9, "TRAVELLER 02")):
        entry = r["members"][index]
        assert entry["ready"] is False
        shared = " ".join(entry["missing"])
        assert OFFICIAL_OWN_DS160 in shared
        assert other in shared
    assert r["submittable"] is False


def test_a_duplicated_passport_number_is_caught_without_printing_it():
    members = tour_group(11)
    members[1]["passport_number"] = members[6]["passport_number"]
    r = build_roster(None, members, group_kind="tour_group", today=TODAY)
    assert r["members"][1]["ready"] is False and r["members"][6]["ready"] is False
    assert any("distinct passport number" in m for m in r["members"][1]["missing"])
    assert members[1]["passport_number"] not in json.dumps(r)


# ---------------------------------------------------------------------------
# Reading members from the database, and admitting when it cannot.
# ---------------------------------------------------------------------------

def test_roster_reads_case_rows_and_case_ids_through_the_db(db):
    applicant = models.Applicant(org_id="org1", user_id="user1",
                                 full_name="WANG XIAOMING",
                                 email="w@example.com")
    db.add(applicant)
    db.flush()
    case = models.VisaApplication(
        org_id="org1", user_id="user1", applicant_id=applicant.id,
        destination_country="USA", visa_type="tourist",
        answers={"passport_number": "E12345678",
                 "ds160_confirmation": "AA00ABCD99",
                 "mrv_receipt": "CITIC000123456"})
    db.add(case)
    db.commit()

    for entry in (case, case.id):            # ORM row and bare case id
        r = build_roster(db, [entry], today=TODAY)
        got = r["members"][0]
        assert got["name"] == "WANG XIAOMING"
        assert got["case_id"] == case.id
        assert got["passport_number_present"] is True
        assert got["ds160_confirmation"] == "AA00ABCD99"
        assert got["mrv_receipt"] == "CITIC000123456"
        assert got["ready"] is True


def test_an_unreadable_case_is_reported_not_silently_dropped(db):
    """A member Ellis cannot read is a traveller who would otherwise turn up
    without a slot, so the roster keeps the row and marks it."""
    r = build_roster(db, ["no-such-case"] + tour_group(11), today=TODAY)
    assert r["size"] == 12
    assert any("could not read case no-such-case" in m
               for m in r["members"][0]["missing"])
    assert r["members"][0]["ready"] is False


# ---------------------------------------------------------------------------
# Passport numbers stay out of the summary.
# ---------------------------------------------------------------------------

def test_passport_numbers_are_absent_from_the_roster_summary():
    """The roster is what gets serialized into API responses and logs. It
    records that a passport number exists, never the number."""
    members = tour_group(12)
    r = build_roster(None, members, group_kind="tour_group", today=TODAY)
    blob = json.dumps(r)
    for entry in members:
        assert entry["passport_number"] not in blob
    assert all(m["passport_number_present"] for m in r["members"])
    assert all("passport_number" not in m for m in r["members"])
    assert r["identifiers_included"] is False
    # And the artifact says the numbers are withheld rather than pretending
    # there are none.
    artifact = export_group_request(r)
    assert "withheld" in "\n".join(artifact["lines"])
    assert members[0]["passport_number"] not in artifact["csv"]


def test_passport_numbers_are_printed_only_when_explicitly_asked_for():
    members = tour_group(12)
    r = build_roster(None, members, group_kind="tour_group", today=TODAY,
                     include_passport_numbers=True)
    assert r["identifiers_included"] is True
    assert r["members"][0]["passport_number"] == members[0]["passport_number"]
    artifact = export_group_request(r)
    assert members[0]["passport_number"] in artifact["csv"]


# ---------------------------------------------------------------------------
# The artifact the human submits.
# ---------------------------------------------------------------------------

def test_the_artifact_renders_a_pdf_and_a_structured_table():
    r = build_roster(None, tour_group(12), group_kind="tour_group", today=TODAY,
                     expires_on="2026-09-30")
    artifact = export_group_request(r)

    assert artifact["kind"] == "us_group_scheduling_request"
    assert artifact["pdf"].startswith(b"%PDF-") and artifact["pdf"].endswith(b"%%EOF")
    assert len(artifact["pdf"]) > 1000
    assert artifact["submittable"] is True
    assert "READY FOR THE COORDINATOR TO SUBMIT" in artifact["status"]

    rows = artifact["rows"]
    assert len(rows) == 12
    assert rows[0]["ds160_confirmation"] == "AA00ABCD00"
    assert rows[0]["ready"] == "yes"

    lines = artifact["csv"].splitlines()
    assert lines[0] == ",".join(artifact["columns"])
    assert len(lines) == 13
    body = "\n".join(artifact["lines"])
    assert OFFICIAL_NO_AGENT_SCHEDULING in body
    assert OFFICIAL_OWN_DS160.split("must ")[1][:20] in body
    assert "09/30/2026" in body                      # the expiry, US display
    assert artifact["submitted_by"].startswith("the group coordinator")


def test_a_fifty_member_roster_renders_every_member_across_pages():
    """A full-cap roster is longer than one page; pdfgen writes one page at a
    time, so the last twenty travellers must not fall off the bottom."""
    from io import BytesIO

    from pypdf import PdfReader

    r = build_roster(None, tour_group(50), group_kind="tour_group", today=TODAY)
    artifact = export_group_request(r)
    assert len(artifact["rows"]) == 50
    assert artifact["csv"].count("\n") == 51
    assert artifact["pdf"].startswith(b"%PDF-")

    pages = PdfReader(BytesIO(artifact["pdf"])).pages
    assert len(pages) > 1
    text = "".join(page.extract_text() for page in pages)
    assert "TRAVELLER 00" in text and "TRAVELLER 49" in text


def test_an_ineligible_group_gets_an_artifact_that_says_do_not_submit():
    """The chase list is still worth printing for a group that does not
    qualify — but it must never be mistaken for a filing."""
    r = build_roster(None, tour_group(6), group_kind="tour_group", today=TODAY)
    artifact = export_group_request(r)
    assert artifact["submittable"] is False
    assert artifact["status"].startswith("DO NOT SUBMIT")
    body = "\n".join(artifact["lines"])
    assert "DO NOT SUBMIT" in body
    assert OFFICIAL_MIN_SIZE in body
    assert len(artifact["rows"]) == 6


def test_the_chase_list_names_the_member_and_the_missing_item():
    members = tour_group(12)
    members[4]["mrv_receipt"] = ""
    artifact = export_group_request(
        build_roster(None, members, group_kind="tour_group", today=TODAY))
    body = "\n".join(artifact["lines"])
    assert "CHASE THESE BEFORE SUBMITTING" in body
    chase = body.split("CHASE THESE BEFORE SUBMITTING")[1]
    assert "TRAVELLER 04" in chase and "MRV fee receipt" in chase


def test_a_name_the_pdf_cannot_carry_is_flagged_and_kept_in_the_csv():
    """A Chinese tour group is the normal case here. pdfgen writes latin-1, so
    the PDF says the name is elsewhere instead of printing blanks."""
    members = tour_group(12)
    members[0]["name"] = "王小明"
    artifact = export_group_request(
        build_roster(None, members, group_kind="tour_group", today=TODAY))
    assert "王小明" in artifact["csv"]
    assert "王小明" in artifact["rows"][0]["name"]
    body = "\n".join(artifact["lines"])
    assert "[see CSV for name]" in body
    assert "?" not in body                     # never a row of question marks


def test_a_name_starting_with_an_equals_sign_is_not_a_spreadsheet_formula():
    members = tour_group(12)
    members[0]["name"] = "=1+1"
    artifact = export_group_request(
        build_roster(None, members, group_kind="tour_group", today=TODAY))
    assert artifact["csv"].splitlines()[1].split(",")[1] == "'=1+1"


# ---------------------------------------------------------------------------
# The request expires, and a lapsed one has to be re-requested.
# ---------------------------------------------------------------------------

def test_an_unrecorded_expiry_is_unknown_never_invented():
    status = expiry_status("", today=TODAY)
    assert status["known"] is False
    assert status["status"] == "unknown"
    assert status["expires_on"] == "" and status["days_remaining"] is None
    assert status["expired"] is False and status["reminder_due"] is False
    assert "does not guess" in status["note"]
    assert not re.search(r"\d{2}/\d{2}/\d{4}", status["note"])


def test_an_unparseable_expiry_is_unknown_rather_than_a_guessed_date():
    assert expiry_status("sometime in September", today=TODAY)["known"] is False


def test_an_expiry_inside_the_reminder_window_asks_for_a_re_request():
    status = expiry_status("2026-08-15", today=TODAY)
    assert status["status"] == "expiring"
    assert status["days_remaining"] == 4
    assert status["reminder_due"] is True
    assert status["expired"] is False
    assert "08/15/2026" in status["note"] and "re-request" in status["note"]


def test_a_comfortable_expiry_raises_no_reminder():
    status = expiry_status(date(2026, 12, 1), today=TODAY)
    assert status["status"] == "active" and status["reminder_due"] is False
    assert status["display"] == "12/01/2026"


def test_a_lapsed_request_blocks_submission_and_says_to_re_request():
    r = build_roster(None, tour_group(12), group_kind="tour_group",
                     today=TODAY, expires_on="2026-08-01")
    assert r["expiry"]["expired"] is True
    assert r["expiry"]["reminder_due"] is True
    assert r["submittable"] is False           # eligible, ready, but lapsed
    assert r["eligible"] is True
    assert any("expired on 08/01/2026" in reason for reason in r["reasons"])
    assert any("new Group Scheduling Request" in reason for reason in r["reasons"])
    assert export_group_request(r)["status"].startswith("NOT READY")


def test_recording_an_expiry_later_updates_the_roster_in_place_of_guessing():
    r = build_roster(None, tour_group(12), group_kind="tour_group", today=TODAY)
    assert r["expiry"]["known"] is False and r["submittable"] is True

    updated = record_expiration(r, "2026-08-20", today=TODAY)
    assert updated["expiry"]["display"] == "08/20/2026"
    assert updated["submittable"] is True
    assert r["expiry"]["known"] is False       # the original is not mutated

    lapsed = record_expiration(updated, "2026-08-10", today=TODAY)
    assert lapsed["submittable"] is False
    # Re-recording does not stack duplicate expiry reasons.
    assert sum("expired on" in reason for reason in lapsed["reasons"]) == 1
    assert sum("expired on" in reason
               for reason in record_expiration(lapsed, "2026-08-09",
                                               today=TODAY)["reasons"]) == 1


# ---------------------------------------------------------------------------
# THE HARD LINE: no slot search, no booking, no portal automation, no network.
# ---------------------------------------------------------------------------

def _module_ast() -> ast.Module:
    return ast.parse(inspect.getsource(roster_mod))


# Anything that could reach a network, drive a browser, or touch a portal.
FORBIDDEN_IMPORTS = {
    "http", "httpx", "requests", "urllib", "urllib3", "socket", "ssl",
    "aiohttp", "websockets", "playwright", "selenium", "browserbase",
    "webbrowser", "ftplib", "smtplib", "telnetlib", "asyncio", "subprocess",
}
ALLOWED_IMPORTS = {"__future__", "csv", "io", "re", "textwrap", "datetime",
                   "pypdf"}


def test_the_module_imports_nothing_that_could_reach_a_network():
    """An HTTP client cannot appear in this file by accident: automated slot
    search is what cancels a traveller's visa, so the import list is pinned."""
    absolute, relative = set(), set()
    for node in ast.walk(_module_ast()):
        if isinstance(node, ast.Import):
            absolute.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:                       # from . / from .providers
                relative.add(node.module or "")
            else:
                absolute.add((node.module or "").split(".")[0])

    assert absolute & FORBIDDEN_IMPORTS == set()
    assert absolute <= ALLOWED_IMPORTS, f"unexpected import: {absolute - ALLOWED_IMPORTS}"
    assert relative <= {"", "providers"}


# Verbs that would mean this module had grown hands. Deliberately about what
# it CALLS, not what it says: the docstring is allowed to name the things this
# module refuses to do.
BOOKING_CALL_RE = re.compile(
    r"^(book|reserve|hold_slot|schedule|search|find_slot|get_slot|read_slot|"
    r"fetch|download|open_url|navigate|click|goto|urlopen|urlretrieve|"
    r"connect|create_connection|run_flow|drive|submit|http)", re.IGNORECASE)

SAFE_CALLS: set[str] = set()


def test_the_module_calls_nothing_that_searches_books_or_drives_a_portal():
    called = set()
    for node in ast.walk(_module_ast()):
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name:
                called.add(name)
    offenders = {name for name in called
                 if BOOKING_CALL_RE.match(name) and name not in SAFE_CALLS}
    assert offenders == set(), f"booking-shaped call in the roster: {offenders}"


def test_the_module_defines_no_scheduling_function():
    names = {node.name for node in ast.walk(_module_ast())
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert not {n for n in names
                if re.search(r"slot|book|schedul|availab|calendar", n, re.I)}


def test_the_refusal_to_automate_is_stated_in_the_module_and_in_the_roster():
    assert roster_mod.NO_AUTOMATION["slot_search"].startswith("never")
    assert roster_mod.NO_AUTOMATION["agent_scheduling"] == OFFICIAL_NO_AGENT_SCHEDULING
    r = build_roster(None, tour_group(12), group_kind="tour_group", today=TODAY)
    assert r["automation"]["booking"].startswith("never")
    assert "never searches for or books" in r["human_action"]["ellis_role"]
    assert r["human_action"]["actor"].startswith("group coordinator")


def test_the_whole_path_runs_with_the_socket_layer_torn_out(monkeypatch):
    """The strongest form of the assertion: build the roster, validate every
    member, render the PDF and the CSV — all with sockets unusable. Any HTTP
    call added to this path later raises here."""
    def _no_network(*args, **kwargs):
        raise AssertionError("the group roster must never open a connection")

    for name in ("socket", "create_connection", "getaddrinfo", "socketpair"):
        monkeypatch.setattr(socket, name, _no_network, raising=False)

    members = tour_group(12)
    members[3]["mrv_receipt"] = ""
    members[8]["ds160_confirmation"] = "12345"
    r = build_roster(None, members, group_kind="tour_group", today=TODAY,
                     expires_on="2026-09-01", include_passport_numbers=True)
    artifact = export_group_request(r)

    assert r["size"] == 12 and r["ready_count"] == 10
    assert artifact["pdf"].startswith(b"%PDF-")
    assert len(artifact["rows"]) == 12
