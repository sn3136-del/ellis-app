"""Appointment availability, monitoring, and the cockpit API.

The invariants here are traveler-safety invariants, not style preferences. An
automated appointment slot search does not get Ellis in trouble — it gets the
TRAVELER's appointment cancelled and their visa revoked (roughly 2,000
cancellations in India in 2025). So these tests pin, mechanically:

  * this module never sends a request anywhere, and above all never to a
    scheduling or booking host;
  * a wait time is accepted only from the Department of State, and only from a
    snapshot a human placed — with nothing placed, availability is an explicit
    "unavailable", never a fabricated date;
  * every deep link points at an official host, and a scheduling link is marked
    human-only and never-fetched;
  * monitoring means reminding a person or recording what a person saw; it can
    never claim a slot was booked or held, and an "observation" that is really
    an automated poll is refused at the door;
  * the cockpit endpoints are org-scoped, degrade to an honest 503 when a
    sibling module is missing, and carry the human acts and the attorney
    disclaimer in every payload.

Every test is hermetic. Nothing here touches the network, and the wait-time
directory is redirected to a temp path via $ELLIS_VISA_WAIT_TIMES_DIR.
"""
from __future__ import annotations

import ast
import datetime as dt
import inspect
import json
import sys
import types

import pytest

from app import appt_api, appt_availability as av

from .conftest import AUTH, AUTH2


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def wait_dir(tmp_path, monkeypatch):
    """An empty, isolated snapshot directory. Tests place files into it; the
    repo's real data/ is never read or written."""
    d = tmp_path / "visa_wait_times"
    d.mkdir()
    monkeypatch.setenv("ELLIS_VISA_WAIT_TIMES_DIR", str(d))
    return d


def _place(directory, name: str, payload) -> None:
    (directory / name).write_text(json.dumps(payload), encoding="utf-8")


def _snapshot(**over) -> dict:
    body = {
        "as_of": "2026-08-01",
        "source_url": av.GLOBAL_WAIT_TIMES_URL,
        "collected_by": "human transcription of the published page",
        "posts": [
            {"post": "Beijing", "post_code": "BEJ", "country": "China",
             "visitor": 21, "student_exchange": 4,
             "petition_based_temporary_workers_h_l_o_p_q": 6,
             "crew_and_transit_c_d": "N/A"},
            {"post": "Shanghai", "country": "China", "Visitor Visa (B1/B2)": "13 days"},
        ],
    }
    body.update(over)
    return body


def _case(db, org="orgAPPT", user="user1"):
    from app import models
    applicant = models.Applicant(org_id=org, user_id=user, full_name="Group Member",
                                 email="m@example.com")
    db.add(applicant)
    db.flush()
    row = models.VisaApplication(
        org_id=org, user_id=user, applicant_id=applicant.id,
        destination_country="United States", visa_type="tourist",
        adapter_id="", state="DRAFT", answers={})
    db.add(row)
    db.commit()
    return row


def _fake_module(name: str, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


# ===========================================================================
# 1. The prohibition: no network, and above all no scheduling host
# ===========================================================================
_NETWORK_MODULES = {
    "httpx", "requests", "aiohttp", "urllib.request", "urllib.error",
    "http.client", "socket", "subprocess", "playwright", "selenium",
    "websockets", "pycurl", "ftplib", "telnetlib", "smtplib",
}


def test_the_module_imports_nothing_that_can_make_a_request():
    """Structural proof that no function in appt_availability can reach a
    scheduling or booking host: the module imports no HTTP client, no browser
    driver, and no subprocess. Parsed from the AST, so a mention inside the
    docstring explaining the prohibition does not count as an import."""
    tree = ast.parse(inspect.getsource(av))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert not (imported & _NETWORK_MODULES), (
        f"appt_availability must send nothing: {sorted(imported & _NETWORK_MODULES)}")
    # urllib is present for URL PARSING only — never urllib.request.
    assert "urllib.parse" in imported


def test_the_module_exposes_no_way_to_search_hold_or_book_a_slot():
    src = inspect.getsource(av)
    for forbidden in ("def book", "def auto_book", "def reserve", "def hold_slot",
                      "def pick_slot", "def select_slot", "def search_slots",
                      "def poll", "def scrape", "def fetch_calendar",
                      "def solve_captcha"):
        assert forbidden not in src, f"{forbidden} must not exist in this module"


@pytest.mark.parametrize("host", sorted(av.NEVER_FETCH_HOSTS))
def test_every_scheduling_host_is_refused_as_a_source(host):
    """The guard rail: any future code about to REQUEST one of these must call
    assert_never_fetched first, and be stopped. Dropping the scheme does not
    walk past it."""
    for url in (f"https://{host}/some/calendar", f"{host}/some/calendar",
                f"//{host}/appointment"):
        with pytest.raises(av.ForbiddenAvailabilitySource):
            av.assert_never_fetched(url)


def test_a_snapshot_sourced_from_a_scheduling_system_is_refused(wait_dir):
    """A wait time attributed to the booking site means someone read a
    protected calendar. Ellis refuses the data rather than laundering it."""
    _place(wait_dir, "2026-08-01.json",
           _snapshot(source_url="https://www.usvisascheduling.com/wait-times"))
    with pytest.raises(av.ForbiddenAvailabilitySource):
        av.parse_snapshot(_snapshot(
            source_url="https://www.usvisascheduling.com/wait-times"))
    loaded = av.load_wait_times()
    assert loaded["available"] is False and loaded["refused"] is True
    assert loaded["records"] == []
    assert "scheduling" in loaded["reason"]


def test_a_wait_time_from_a_non_state_host_is_refused():
    with pytest.raises(av.UntrustedWaitTimeSource):
        av.parse_snapshot(_snapshot(source_url="https://visa-waits.example.com/data"))


# ===========================================================================
# 2. Honest unavailability
# ===========================================================================
def test_no_snapshot_means_an_explicit_unavailable_not_a_guess(wait_dir):
    out = av.load_wait_times()
    assert out["available"] is False
    assert out["records"] == [] and out["as_of"] is None
    assert "no public API or feed" in out["reason"]
    assert str(wait_dir) in out["reason"]
    # And it says how a human supplies the data, rather than implying a fetcher.
    assert "no documented json" in out["how_to_provide"]["why_manual"].lower()
    # The worked example is placeholders, so nothing in the plan can be read as
    # a wait time.
    assert out["how_to_provide"]["shape"]["posts"][0]["visitor"] == "<whole days>"


def test_availability_with_nothing_placed_fabricates_no_date(wait_dir):
    out = av.availability(post="Beijing", country="China", category="visitor")
    assert out["wait_time_data"]["available"] is False
    assert out["wait_time"]["known"] is False
    assert out["wait_time"]["wait_days"] is None
    assert out["live_slot_data_available"] is False
    assert out["wait_times"] == []
    # No date is invented anywhere a consumer would read one as data.
    assert out["wait_time"]["as_of"] is None
    assert out["wait_time_data"]["as_of"] is None
    assert out["posts_in_country"] == []
    # ...and the official links still tell the human where to go.
    assert any(l["is_scheduling_system"] for l in out["official_links"])


def test_a_requested_snapshot_date_that_is_not_present_says_so(wait_dir):
    _place(wait_dir, "2026-08-01.json", _snapshot())
    out = av.load_wait_times("2026-01-01")
    assert out["available"] is False
    assert "2026-01-01" in out["reason"]
    assert out["available_snapshots"] == ["2026-08-01"]


def test_unreadable_snapshot_degrades_instead_of_raising(wait_dir):
    (wait_dir / "2026-08-01.json").write_text("{not json", encoding="utf-8")
    out = av.load_wait_times()
    assert out["available"] is False and out["records"] == []
    assert "could not be read" in out["reason"]


# ===========================================================================
# 3. Wait-time parsing
# ===========================================================================
def test_wait_times_parse_from_a_placed_snapshot(wait_dir):
    _place(wait_dir, "2026-08-01.json", _snapshot())
    out = av.load_wait_times(today=dt.date(2026, 8, 5))
    assert out["available"] is True
    assert out["as_of"] == "2026-08-01" and out["stale"] is False
    by_key = {(r["post"], r["category"]): r["wait_days"] for r in out["records"]}
    assert by_key[("Beijing", "visitor")] == 21
    assert by_key[("Beijing", "student_exchange")] == 4
    # Published column names are normalized to canonical categories.
    assert by_key[("Beijing", "petition_worker")] == 6
    assert by_key[("Shanghai", "visitor")] == 13
    # "N/A" is omitted with a warning, never zero-filled.
    assert ("Beijing", "crew_transit") not in by_key
    assert any("crew_transit" in w for w in out["warnings"])
    # Every record carries its provenance.
    assert all(r["source_url"] == av.GLOBAL_WAIT_TIMES_URL for r in out["records"])
    assert all(r["as_of"] == "2026-08-01" for r in out["records"])


def test_long_form_rows_parse_too(wait_dir):
    _place(wait_dir, "2026-08-01.json", {
        "as_of": "2026-08-01", "source_url": av.WAIT_TIMES_TOOL_URL,
        "posts": [{"post": "Guangzhou", "category": "B1/B2", "wait_days": "45 days"},
                  {"post": "Wuhan", "category": "mystery", "wait_days": 3}]})
    out = av.load_wait_times(today=dt.date(2026, 8, 5))
    assert [(r["post"], r["category"], r["wait_days"]) for r in out["records"]] == [
        ("Guangzhou", "visitor", 45)]
    assert any("unrecognized category" in w for w in out["warnings"])


@pytest.mark.parametrize("raw,expect", [
    (21, 21), ("21", 21), ("21 days", 21), ("1,200", 1200), ("Same day", 0),
    (0, 0), ("", None), ("N/A", None), (None, None), (-3, None), (99999, None),
    (True, None), ("unknown", None),
])
def test_published_figures_coerce_or_become_unknown(raw, expect):
    assert av._coerce_wait_days(raw) == expect


def test_an_undated_snapshot_is_refused():
    payload = _snapshot()
    payload.pop("as_of")
    with pytest.raises(av.AvailabilityError):
        av.parse_snapshot(payload)


def test_a_stale_snapshot_is_flagged_rather_than_quietly_served(wait_dir):
    _place(wait_dir, "2026-01-01.json", _snapshot(as_of="2026-01-01"))
    out = av.load_wait_times(today=dt.date(2026, 8, 5))
    assert out["available"] is True and out["stale"] is True
    assert out["age_days"] > av.STALE_AFTER_DAYS
    assert "check the official page" in out["stale_note"]


def test_the_newest_snapshot_wins(wait_dir):
    _place(wait_dir, "2026-06-01.json", _snapshot(as_of="2026-06-01"))
    _place(wait_dir, "2026-08-01.json", _snapshot(as_of="2026-08-01"))
    assert av.available_snapshots() == ["2026-06-01", "2026-08-01"]
    assert av.load_wait_times(today=dt.date(2026, 8, 5))["as_of"] == "2026-08-01"


def test_an_unknown_post_or_category_is_unknown_never_interpolated(wait_dir):
    _place(wait_dir, "2026-08-01.json", _snapshot())
    missing_post = av.wait_time("Chengdu", "visitor")
    assert missing_post["known"] is False and missing_post["wait_days"] is None
    assert "publishes no" in missing_post["reason"]
    missing_cat = av.wait_time("Beijing", "crew_transit")
    assert missing_cat["known"] is False and missing_cat["wait_days"] is None


def test_a_wait_time_never_claims_to_be_a_slot(wait_dir):
    _place(wait_dir, "2026-08-01.json", _snapshot())
    entry = av.wait_time("Beijing", "visitor")
    assert entry["known"] is True and entry["wait_days"] == 21
    assert entry["is_live_slot_data"] is False
    snapshot = av.load_wait_times(today=dt.date(2026, 8, 5))
    assert snapshot["is_live_slot_data"] is False
    assert "not a slot" in snapshot["note"]


def test_posts_for_country_reports_only_what_was_placed(wait_dir):
    assert av.posts_for_country("China") == []
    _place(wait_dir, "2026-08-01.json", _snapshot())
    assert av.posts_for_country("China") == ["Beijing", "Shanghai"]
    assert av.posts_for_country("France") == []


# ===========================================================================
# 4. Deep links — official hosts only
# ===========================================================================
def test_every_deep_link_points_at_an_official_host():
    for route, kwargs in (("us", {"country": "China"}), ("us", {}),
                          ("schengen", {"member_state": "FR"}),
                          ("schengen", {})):
        for link in av.official_links(route=route, **kwargs):
            if not link["url"]:
                continue  # an honest "not determined" placeholder
            assert link["host"] in av.OFFICIAL_LINK_HOSTS, link
            assert link["url"].startswith("https://")
            # A booking system may be LINKED for a human and never fetched.
            if link["host"] in av.NEVER_FETCH_HOSTS:
                assert link["ellis_may_fetch"] is False and link["human_only"] is True


def test_a_link_to_a_non_official_host_is_refused():
    """Sending an applicant to a look-alike booking site is how people lose
    money to visa scams."""
    with pytest.raises(av.UnofficialLink):
        av.deep_link("scheduling_site", "https://us-visa-appointments.example.com/",
                     "Book now")


def test_scheduling_links_are_human_only_and_never_fetched():
    links = av.official_links(route="us", country="China")
    scheduling = [l for l in links if l["is_scheduling_system"]]
    assert scheduling, "the official scheduling site must be offered to the human"
    for link in scheduling:
        assert link["human_only"] is True
        assert link["ellis_may_fetch"] is False
        assert "cancelled" in link["note"]
        assert link["host"] in av.NEVER_FETCH_HOSTS


def test_wait_time_links_are_state_department_and_attributed():
    links = {l["kind"]: l for l in av.official_links(route="us")}
    assert links["wait_times_tool"]["host"] in av.WAIT_TIME_HOSTS
    assert links["quarterly_report"]["host"] in av.WAIT_TIME_HOSTS
    assert "Department of State" in links["wait_times_tool"]["attribution"]
    # Honest about why Ellis reads a placed snapshot instead of the page.
    assert "refuses automated fetches" in links["wait_times_tool"]["note"]


def test_an_undetermined_schengen_portal_is_reported_as_unknown():
    links = av.official_links(route="schengen", member_state="")
    portal = [l for l in links if l["kind"] == "official_application_portal"][0]
    assert portal["url"] == ""
    assert "does not guess" in portal["note"]


# ===========================================================================
# 5. Monitoring — a reminder, or a human's own observation
# ===========================================================================
def _future(hours=24):
    return dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=hours)


def test_a_reminder_asks_a_human_to_look_and_claims_nothing_else():
    rec = av.create_reminder(case_id="c1", post="Beijing", category="B1/B2",
                             due_at=_future())
    assert rec["record_kind"] == "reminder"
    assert rec["booked"] is False and rec["slot_held"] is False
    assert rec["slot_reserved"] is False and rec["automated_check"] is False
    assert "check availability yourself" in rec["human_act"]
    assert rec["category"] == "visitor"


def test_a_reminder_already_in_the_past_is_refused():
    with pytest.raises(av.MonitoringError):
        av.create_reminder(post="Beijing", due_at=dt.datetime.now(dt.timezone.utc)
                           - dt.timedelta(hours=1))


@pytest.mark.parametrize("post,due_at", [
    ("", "future"),            # nowhere to send the human
    ("Beijing", "not-a-date"),  # an unparseable due time
    ("Beijing", ""),            # no due time at all
])
def test_an_unusable_reminder_is_refused(post, due_at):
    when = _future() if due_at == "future" else due_at
    with pytest.raises(av.MonitoringError):
        av.create_reminder(post=post, due_at=when)


def test_an_unknown_reminder_kind_is_refused():
    with pytest.raises(av.MonitoringError):
        av.create_reminder(post="Beijing", due_at=_future(), kind="snipe_slot")


def test_an_automated_poll_can_never_be_recorded_as_an_observation():
    """The one laundering route a monitoring model must close: calling a
    scraper's output 'what a human saw'."""
    for source in ("automated_poll", "scraper", "bot", "slot_watcher", "sniper"):
        with pytest.raises(av.ForbiddenAvailabilitySource):
            av.record_human_observation(post="Beijing", source_kind=source)


def test_a_human_observation_records_provenance_and_holds_nothing():
    rec = av.record_human_observation(
        case_id="c1", post="Beijing", category="visitor",
        earliest_offered_date="2026-11-04", slots_seen=3, observer="coordinator",
        source_kind="attended_human_session")
    assert rec["record_kind"] == "observation"
    assert rec["provenance"] == "a human looked, in their own authenticated session"
    assert rec["booked"] is False and rec["slot_held"] is False
    assert rec["is_live_slot_data"] is False
    assert "nothing is held" in rec["expires_note"]


def test_an_observation_cannot_be_dated_in_the_future_or_carry_junk():
    with pytest.raises(av.MonitoringError):
        av.record_human_observation(post="Beijing", observed_at=_future())
    with pytest.raises(av.MonitoringError):
        av.record_human_observation(post="Beijing", observed_at="last Tuesday")
    with pytest.raises(av.MonitoringError):
        av.record_human_observation(post="Beijing", earliest_offered_date="soon")
    with pytest.raises(av.MonitoringError):
        av.record_human_observation(post="Beijing", slots_seen=-1)


def test_due_reminders_are_the_ones_whose_time_has_come():
    now = dt.datetime(2026, 8, 11, 12, 0, tzinfo=dt.timezone.utc)
    soon = av.create_reminder(post="Beijing", due_at=now + dt.timedelta(hours=1),
                              now=now - dt.timedelta(days=1))
    later = av.create_reminder(post="Shanghai", due_at=now + dt.timedelta(days=5),
                               now=now)
    due = av.due_reminders([soon, later], now=now + dt.timedelta(hours=2))
    assert [r["post"] for r in due] == ["Beijing"]
    assert av.due_reminders([soon, later], now=now - dt.timedelta(days=1)) == []


def test_the_monitoring_summary_never_claims_a_booking(wait_dir):
    now = dt.datetime(2026, 8, 11, 12, 0, tzinfo=dt.timezone.utc)
    records = [
        av.create_reminder(post="Beijing", due_at=now + dt.timedelta(hours=1),
                           now=now - dt.timedelta(days=1)),
        av.record_human_observation(post="Beijing", observed_at=now, now=now),
    ]
    summary = av.monitoring_summary(records, now=now + dt.timedelta(hours=2))
    assert summary["booked"] is False and summary["slot_held"] is False
    assert summary["counts"] == {"reminders": 1, "observations": 1, "due_now": 1}
    assert "never polls" in summary["policy"]

    payload = json.dumps(av.availability(post="Beijing", records=records, now=now))
    for claim in ('"booked": true', '"slot_held": true', '"slot_reserved": true',
                  '"automated_check": true', '"live_slot_data_available": true'):
        assert claim not in payload.lower()


# ===========================================================================
# 6. The cockpit API — org scoping, honest degradation, envelope
# ===========================================================================
def _install(monkeypatch, module_name: str, **attrs):
    monkeypatch.setitem(sys.modules, f"app.{module_name}",
                        _fake_module(f"app.{module_name}", **attrs))


def test_availability_endpoint_requires_authentication(client):
    assert client.get("/appointments/availability").status_code == 401


def test_availability_endpoint_is_honest_when_nothing_is_placed(client, wait_dir):
    r = client.get("/appointments/availability",
                   params={"post": "Beijing", "country": "China"}, headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["live_slot_data_available"] is False
    assert body["wait_time"]["wait_days"] is None
    assert body["human_acts"] and body["disclaimer"]
    assert "not a law firm" in body["disclaimer"]
    assert "booking" in " ".join(body["compliance"]["never_automated"]).lower()


def test_the_availability_response_never_maps_the_servers_filesystem(
        client, wait_dir):
    """appt_availability tells a local OPERATOR the absolute directory to place
    a snapshot in, which is right for them and wrong for an HTTP caller: the
    server's filesystem layout is a free map for anyone probing the deployment.
    The instructions survive the edge; the paths do not."""
    empty = client.get("/appointments/availability",
                       params={"post": "Beijing"}, headers=AUTH)
    _place(wait_dir, "2026-08-01.json", _snapshot())
    served = client.get("/appointments/availability",
                        params={"post": "Beijing"}, headers=AUTH)
    for r in (empty, served):
        assert str(wait_dir) not in r.text, "the response leaks a server path"
    # ...and the operator instructions are still there, just without the path.
    body = empty.json()
    assert body["wait_time_data"]["available"] is False
    assert "no documented json" in \
        body["wait_time_data"]["how_to_provide"]["why_manual"].lower()
    assert "<the wait-times directory" in body["wait_time_data"]["directory"]


def test_availability_endpoint_serves_a_placed_snapshot(client, wait_dir):
    _place(wait_dir, "2026-08-01.json", _snapshot())
    r = client.get("/appointments/availability",
                   params={"post": "Beijing", "category": "visitor"}, headers=AUTH)
    body = r.json()
    assert body["available"] is True
    assert body["wait_time"]["wait_days"] == 21
    assert body["wait_time"]["is_live_slot_data"] is False
    assert body["live_slot_data_available"] is False


@pytest.mark.parametrize("surface", ["triage", "prestage", "availability",
                                     "group_roster", "group_roster_export"])
def test_every_surface_names_the_human_acts_that_remain(surface):
    acts = appt_api.HUMAN_ACTS[surface]
    assert acts, f"{surface} must name the human's remaining acts"
    for act in acts:
        assert act["act"] and act["who"] and act["why"] and act["ellis_does"]


def test_the_named_human_acts_cover_the_never_automated_boundary():
    every = " ".join(a["act"] for acts in appt_api.HUMAN_ACTS.values() for a in acts)
    for act in ("sign in", "sign", "pay", "book", "submit", "in person"):
        assert act in every.lower()


# --- triage ----------------------------------------------------------------
def test_triage_is_org_scoped(client, db, monkeypatch):
    row = _case(db, org="org1")
    _install(monkeypatch, "appt_eligibility",
             triage=lambda **kw: {"in_person_required": True})
    assert client.get(f"/appointments/triage/{row.id}", headers=AUTH).status_code == 200
    # Another tenant is refused outright, never a partial payload.
    cross = client.get(f"/appointments/triage/{row.id}", headers=AUTH2)
    assert cross.status_code == 403
    assert client.get("/appointments/triage/nope", headers=AUTH).status_code == 404


def test_triage_payload_carries_the_acts_and_the_disclaimer(client, db, monkeypatch):
    row = _case(db, org="org1")
    _install(monkeypatch, "appt_eligibility",
             triage=lambda db, case_id: {"in_person_required": False,
                                         "reason": "VIS reuse inside 59 months"})
    body = client.get(f"/appointments/triage/{row.id}", headers=AUTH).json()
    assert body["triage"]["in_person_required"] is False
    assert body["surface"] == "triage"
    assert body["human_acts"] and body["disclaimer_version"]
    assert "not a law firm" in body["disclaimer"]


def test_a_missing_sibling_module_degrades_to_an_honest_503(client, db, monkeypatch):
    row = _case(db, org="org1")
    _install(monkeypatch, "appt_eligibility")   # module present, triage() absent
    r = client.get(f"/appointments/triage/{row.id}", headers=AUTH)
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert detail["available"] is False
    assert "does not define triage()" in detail["reason"]
    assert detail["module"] == "appt_eligibility"
    # The human's work does not vanish because a module did.
    assert detail["human_acts"] and detail["disclaimer"]


def test_a_sibling_that_raises_never_becomes_a_fabricated_answer(client, db, monkeypatch):
    row = _case(db, org="org1")

    def _boom(**kw):
        raise RuntimeError("wage data missing")

    _install(monkeypatch, "appt_appointments_prestage", prestage=_boom)
    r = client.get(f"/appointments/prestage/{row.id}", headers=AUTH)
    assert r.status_code == 503
    assert "could not answer" in r.json()["detail"]["reason"]


def test_a_sibling_needing_something_this_router_cannot_supply_says_so(
        client, db, monkeypatch):
    row = _case(db, org="org1")
    _install(monkeypatch, "appt_appointments_prestage",
             prestage=lambda db, case_id, mystery_input: {"ok": True})
    r = client.get(f"/appointments/prestage/{row.id}", headers=AUTH)
    assert r.status_code == 503
    assert "mystery_input" in r.json()["detail"]["reason"]


def test_prestage_is_org_scoped(client, db, monkeypatch):
    row = _case(db, org="org1")
    _install(monkeypatch, "appt_appointments_prestage",
             prestage=lambda **kw: {"ready": False, "missing": ["ds160"]})
    ok = client.get(f"/appointments/prestage/{row.id}", headers=AUTH)
    assert ok.status_code == 200 and ok.json()["prestage"]["missing"] == ["ds160"]
    assert client.get(f"/appointments/prestage/{row.id}",
                      headers=AUTH2).status_code == 403


# --- group roster ----------------------------------------------------------
_ROSTER_MEMBER = {
    "case_id": "", "full_name": "Group Member", "passport_number": "E12345678",
    "ds160_confirmation": "AA00ABCDEF", "mrv_receipt_number": "MRV-9911",
    "status": "ready", "missing": [],
}


def _roster_module(monkeypatch, captured=None, **extra):
    def build_roster(**kw):
        if captured is not None:
            captured.update(kw)
        members = [dict(_ROSTER_MEMBER, case_id=cid)
                   for cid in kw.get("case_ids", [])]
        return {"group_name": kw.get("group_name", ""), "members": members,
                "member_count": len(members), "submitted": False,
                "submittable": False}

    _install(monkeypatch, "appt_group_roster", build_roster=build_roster, **extra)


def test_group_roster_requires_member_cases(client, monkeypatch):
    _roster_module(monkeypatch)
    r = client.post("/appointments/group-roster", json={"case_ids": []}, headers=AUTH)
    assert r.status_code == 400


def test_group_roster_authorizes_every_member_case(client, db, monkeypatch):
    mine = _case(db, org="org1")
    theirs = _case(db, org="org2")
    _roster_module(monkeypatch)
    ok = client.post("/appointments/group-roster",
                     json={"case_ids": [mine.id], "group_name": "Tour A"},
                     headers=AUTH)
    assert ok.status_code == 200
    body = ok.json()
    assert body["roster"]["member_count"] == 1
    assert body["roster"]["submitted"] is False
    assert body["human_acts"] and body["disclaimer"]
    # One member in another tenant fails the WHOLE request — a roster silently
    # missing a traveller would be submitted as if it were complete.
    cross = client.post("/appointments/group-roster",
                        json={"case_ids": [mine.id, theirs.id]}, headers=AUTH)
    assert cross.status_code == 403


def test_group_roster_export_is_a_csv_for_the_human_coordinator(client, db, monkeypatch):
    row = _case(db, org="org1")
    _roster_module(monkeypatch)
    r = client.get("/appointments/group-roster/export",
                   params={"case_id": [row.id], "group_name": "Tour A"},
                   headers=AUTH)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert "never submits or books" in r.headers["x-ellis-human-act"]
    header, first = r.text.strip().splitlines()[:2]
    assert header.split(",")[:3] == ["case_id", "full_name", "passport_number"]
    assert "E12345678" in first


def test_group_roster_export_never_writes_traveller_details_to_the_audit_log(
        client, db, monkeypatch):
    from app import audit
    row = _case(db, org="org1")
    _roster_module(monkeypatch)
    client.get("/appointments/group-roster/export",
               params={"case_id": [row.id]}, headers=AUTH)
    for secret in ("E12345678", "AA00ABCDEF", "MRV-9911"):
        assert audit.contains_plaintext(db, secret) is False


def test_group_roster_export_is_org_scoped(client, db, monkeypatch):
    row = _case(db, org="org1")
    _roster_module(monkeypatch)
    assert client.get("/appointments/group-roster/export",
                      params={"case_id": [row.id]},
                      headers=AUTH2).status_code == 403


def test_group_roster_json_export_carries_the_envelope(client, db, monkeypatch):
    row = _case(db, org="org1")
    _roster_module(monkeypatch)
    body = client.get("/appointments/group-roster/export",
                      params={"case_id": [row.id], "format": "json"},
                      headers=AUTH).json()
    assert body["members"][0]["case_id"] == row.id
    assert body["surface"] == "group_roster_export"
    assert body["human_acts"] and body["disclaimer"]


def test_group_roster_export_refuses_an_unknown_format(client, db, monkeypatch):
    row = _case(db, org="org1")
    _roster_module(monkeypatch)
    assert client.get("/appointments/group-roster/export",
                      params={"case_id": [row.id], "format": "xlsx"},
                      headers=AUTH).status_code == 400


def test_the_roster_modules_own_artifact_is_preferred_over_the_fallback(
        client, db, monkeypatch):
    """The specialist module owns the consulate's column order and the
    printable page; this router must not quietly render its own instead."""
    row = _case(db, org="org1")
    _roster_module(monkeypatch, export_group_request=lambda roster: {
        "columns": ["no", "name"], "rows": [{"no": 1, "name": "Group Member"}],
        "csv": "no,name\n1,Group Member\n",
        "csv_filename": "us-group-scheduling-request.csv",
        "filename": "us-group-scheduling-request.pdf",
        "pdf": b"%PDF-1.4 roster",
        "submittable": False})
    csv_out = client.get("/appointments/group-roster/export",
                         params={"case_id": [row.id]}, headers=AUTH)
    assert csv_out.text == "no,name\n1,Group Member\n"
    assert "us-group-scheduling-request.csv" in csv_out.headers["content-disposition"]

    pdf_out = client.get("/appointments/group-roster/export",
                         params={"case_id": [row.id], "format": "pdf"},
                         headers=AUTH)
    assert pdf_out.status_code == 200
    assert pdf_out.headers["content-type"] == "application/pdf"
    assert pdf_out.content.startswith(b"%PDF")


def test_a_pdf_export_with_no_printable_roster_says_so(client, db, monkeypatch):
    row = _case(db, org="org1")
    _roster_module(monkeypatch)          # build_roster only, no artifact
    r = client.get("/appointments/group-roster/export",
                   params={"case_id": [row.id], "format": "pdf"}, headers=AUTH)
    assert r.status_code == 503
    assert "export csv instead" in r.json()["detail"]["reason"]


def test_a_failing_exporter_is_a_503_never_a_quietly_downgraded_file(
        client, db, monkeypatch):
    """A module that publishes NO exporter falls back to this router's plain
    renderer, which is fine — there was never a status page to lose.

    A module that publishes one and then FAILS is different: falling back would
    hand the coordinator a file stripped of the very page that says whether the
    roster may be submitted at all, and they would submit it. That degrades
    honestly, out loud, naming the module."""
    row = _case(db, org="org1")

    def _broken(roster):
        raise RuntimeError("pdf backend missing")

    _roster_module(monkeypatch, export_group_request=_broken)
    for fmt in ("csv", "pdf", "json"):
        r = client.get("/appointments/group-roster/export",
                       params={"case_id": [row.id], "format": fmt}, headers=AUTH)
        assert r.status_code == 503, fmt
        detail = r.json()["detail"]
        assert detail["module"] == "appt_group_roster"
        assert "pdf backend missing" in detail["reason"]
        # Even the refusal still names the human's work.
        assert detail["human_acts"] and detail["disclaimer"]


def test_passport_numbers_are_printed_only_on_an_explicit_request(
        client, db, monkeypatch):
    """Passport numbers on a roster are a privacy decision, so the default is
    off and the audit trail records which way it went."""
    from app import audit
    row = _case(db, org="org1")
    captured: dict = {}
    _roster_module(monkeypatch, captured)
    client.get("/appointments/group-roster/export",
               params={"case_id": [row.id]}, headers=AUTH)
    assert captured["include_passport_numbers"] is False
    client.get("/appointments/group-roster/export",
               params={"case_id": [row.id], "include_passport_numbers": "true"},
               headers=AUTH)
    assert captured["include_passport_numbers"] is True
    events = [e for e in audit.for_application(db, row.id)
              if e.action == "appointment_group_roster_exported"]
    assert [e.detail["identifiers_included"] for e in events] == [False, True]


def test_the_fallback_csv_neutralizes_spreadsheet_formulas():
    """A coordinator opens the roster in a spreadsheet; a name starting with
    '=' would be executed there."""
    out = appt_api._roster_csv([{"full_name": "=cmd|'/c calc'!A1",
                                 "missing": ["passport number", "MRV receipt"]}])
    cell = out.strip().splitlines()[1].split(",")[0]
    assert cell.startswith("'="), "the leading '=' must not survive as a formula"
    assert "passport number; MRV receipt" in out


def test_the_roster_builder_receives_only_authorized_cases(client, db, monkeypatch):
    row = _case(db, org="org1")
    captured: dict = {}
    _roster_module(monkeypatch, captured)
    client.post("/appointments/group-roster",
                json={"case_ids": [row.id, row.id], "post": "Beijing"},
                headers=AUTH)
    assert captured["case_ids"] == [row.id], "duplicates collapse, no extras appear"
    assert captured["post"] == "Beijing"
    assert captured["org_id"] == "org1"


# --- the real modules, wired together --------------------------------------
def test_the_router_binds_the_real_sibling_modules(client, db):
    """Contract test against appt_eligibility / appt_appointments_prestage /
    appt_group_roster as they actually are. The router binds their entry points
    by signature, so a drift there would be a silent 503 in production — this
    makes it fail loudly instead."""
    row = _case(db, org="org1")
    for path in (f"/appointments/triage/{row.id}",
                 f"/appointments/prestage/{row.id}"):
        r = client.get(path, headers=AUTH)
        assert r.status_code == 200, (path, r.json())
        body = r.json()
        assert body["available"] is True
        assert body["human_acts"] and body["disclaimer"]

    r = client.post("/appointments/group-roster",
                    json={"case_ids": [row.id], "group_kind": "tour_group"},
                    headers=AUTH)
    assert r.status_code == 200, r.json()
    roster = r.json()["roster"]
    # A one-person "group" is not a group; the real module says so rather than
    # producing something the coordinator would submit.
    assert roster["submittable"] is False


def test_the_real_roster_export_is_the_coordinators_artifact(client, db):
    row = _case(db, org="org1")
    csv_out = client.get("/appointments/group-roster/export",
                         params={"case_id": [row.id], "group_kind": "tour_group"},
                         headers=AUTH)
    assert csv_out.status_code == 200
    assert csv_out.headers["content-type"].startswith("text/csv")
    assert csv_out.text.splitlines()[0].startswith("no,name")

    pdf_out = client.get("/appointments/group-roster/export",
                         params={"case_id": [row.id], "format": "pdf"},
                         headers=AUTH)
    assert pdf_out.status_code == 200
    assert pdf_out.content.startswith(b"%PDF")
    assert "never submits or books" in pdf_out.headers["x-ellis-human-act"]


# --- the compliance block is on every payload ------------------------------
def test_no_cockpit_payload_ever_claims_ellis_booked_anything(client, db, monkeypatch,
                                                             wait_dir):
    row = _case(db, org="org1")
    _install(monkeypatch, "appt_eligibility", triage=lambda **kw: {"ok": True})
    _install(monkeypatch, "appt_appointments_prestage", prestage=lambda **kw: {"ok": True})
    _roster_module(monkeypatch)
    payloads = [
        client.get("/appointments/availability", headers=AUTH).json(),
        client.get(f"/appointments/triage/{row.id}", headers=AUTH).json(),
        client.get(f"/appointments/prestage/{row.id}", headers=AUTH).json(),
        client.post("/appointments/group-roster", json={"case_ids": [row.id]},
                    headers=AUTH).json(),
    ]
    for body in payloads:
        assert body["human_acts"], body["surface"]
        assert body["disclaimer_version"] == appt_api.DISCLAIMER_VERSION
        never = " ".join(body["compliance"]["never_automated"]).lower()
        assert "booking an appointment slot" in never or "booking an appointment" in never
        assert "captcha" in never and "signing" in never
        assert "traveler" in body["compliance"]["why"].lower()
