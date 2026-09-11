"""Grounded renewal: an answer is re-checked against its own official page.

Includes the JAPAN REGRESSION: the exact failure Trip.com found in their demo
— a stored answer whose stay/channel/fee no longer match the official page —
must be caught by a recheck and corrected from the page, and can never again
be "renewed" by re-asking the model's memory once the row is grounded.
"""
import pytest

from app.visa_snapshot import fetching, freshness, kimi_primary
from app.visa_snapshot.fetching import FetchResult
from app.visa_snapshot.models import DatabaseIssueReport, KimiRouteGuidanceCache


ROUTE = {"passport_nationality": "CHN", "passport_issuing_country": "CHN",
         "lawful_country_of_residence": "CHN",
         "travel_document_type": "ordinary_passport",
         "destination_country": "JPN", "visa_category": "tourist_visa",
         "travel_purpose": "tourism"}

# The Japan-shaped stored answer: superficially complete, quietly outdated —
# a blanket 90-day stay and a "visa centre" channel, the two headline errors
# from Trip.com's demo test.
STALE_JPN = {
    "disposition": "VISA_REQUIRED", "visa_category": "Temporary visitor",
    "permitted_stay": "90 days", "passport_validity": "valid for the stay",
    "required_documents": ["passport"], "application_channel": "authorised_agent",
    "application_channel_detail": "Applications must be lodged through an "
                                  "accredited travel agency.",
    "government_fee": {"amount": 200, "currency": "CNY"},
    "processing_time": "5 working days", "confidence": "high",
    "source_url": "https://www.mofa.go.jp/j_info/visit/visa/index.html",
    "visa_products": [{"type": "Single-entry Temporary Visitor",
                       "entry": "single", "validity": "3 months",
                       "max_stay_days": 90,
                       "fee": {"amount": 200, "currency": "CNY"},
                       "notes": None}],
}

OFFICIAL_PAGE = FetchResult(
    requested_url="https://www.mofa.go.jp/j_info/visit/visa/index.html",
    ok=True, final_url="https://www.mofa.go.jp/j_info/visit/visa/index.html",
    final_hostname="www.mofa.go.jp", http_status=200,
    content_text=("Chinese nationals must obtain a visa for tourism in Japan. Visa fees revised 1 July 2026: single entry 715 CNY. "
                  "Single-entry temporary visitor visas for tourism permit a "
                  "stay of 15 days or 30 days as decided by the mission."),
    content_hash="abc123", retrieved_at="2026-08-22T00:00:00Z")


@pytest.fixture()
def db():
    from app.db import SessionLocal, engine
    from app.models import Base
    Base.metadata.create_all(engine)
    s = SessionLocal()
    yield s
    s.rollback()
    s.close()


@pytest.fixture(autouse=True)
def _clean(db, tmp_path, monkeypatch):
    # Isolate from the SHIPPED overrides: several of these routes are
    # human-verified in production, and this file is about what the machine
    # does on its own. The override-collision case installs its own file.
    from app.visa_snapshot import verified_overrides as vo
    empty = tmp_path / "no_overrides.json"
    empty.write_text("[]")
    monkeypatch.setattr(vo, "OVERRIDES", empty)
    vo.reload()
    for r in db.query(KimiRouteGuidanceCache).all():
        db.delete(r)
    for r in db.query(DatabaseIssueReport).all():
        db.delete(r)
    db.commit()
    yield
    fetching.set_fetcher(None)
    freshness.set_provider(None)
    kimi_primary.set_provider(None)
    vo.reload()


def _seed(db, guidance=STALE_JPN, route=ROUTE):
    row = KimiRouteGuidanceCache(cache_key=kimi_primary.cache_key(route),
                                 route=dict(route), status="KIMI_PRIMARY",
                                 guidance=dict(guidance))
    db.add(row)
    db.commit()
    return row


def test_japan_regression_the_page_corrects_the_stored_answer(db):
    """The demo failure, replayed: the official page contradicts the stored
    stay and fee; the recheck corrects BOTH from the page, quotes required."""
    _seed(db)
    fetching.set_fetcher(lambda url, timeout_seconds=0: OFFICIAL_PAGE)
    freshness.set_provider(lambda system, user: {
        "page_relevant": True, "page_is_nationality_specific": True, "consistent": False,
        "corrected_fields": {
            "permitted_stay": "15 or 30 days, decided by the mission",
            "government_fee": {"amount": 715, "currency": "CNY"}},
        "evidence": {
            "permitted_stay": "a stay of 15 days or 30 days as decided by the mission",
            "government_fee": "single entry 715 CNY"},
        "note": "fee and stay revised"})
    out = freshness.recheck_route(db, ROUTE)
    assert out["outcome"] == "checked"
    assert out["changed"] == ["government_fee", "permitted_stay"]
    row = db.query(KimiRouteGuidanceCache).one()
    assert row.guidance["permitted_stay"].startswith("15 or 30 days")
    assert row.guidance["government_fee"] == {"amount": 715, "currency": "CNY"}
    gc = row.verification["grounded_check"]
    assert gc["outcome"] == "checked" and gc["changed_fields"]
    assert gc["source_url"] == OFFICIAL_PAGE.final_url
    assert row.fresh_until is None  # the page does not verify the other detail fields
    assert gc["verified_fields"] == ["disposition", "government_fee", "permitted_stay"]
    assert gc["unverified_fields"] and not gc["renewed"]


def test_a_correction_without_a_page_quote_is_discarded(db):
    _seed(db)
    fetching.set_fetcher(lambda url, timeout_seconds=0: OFFICIAL_PAGE)
    freshness.set_provider(lambda system, user: {
        "page_relevant": True, "page_is_nationality_specific": True, "consistent": False,
        "corrected_fields": {"permitted_stay": "7 days"},
        "evidence": {}, "note": "no quote offered"})
    out = freshness.recheck_route(db, ROUTE)
    assert out["outcome"] == "checked" and out["changed"] == []
    assert db.query(KimiRouteGuidanceCache).one() \
             .guidance["permitted_stay"] == "90 days"


def test_the_machine_never_outvotes_a_human_override(db, tmp_path, monkeypatch):  # noqa: F811
    """A page contradiction on a HUMAN-verified field files an operator issue
    and leaves both the override and the freshness window untouched."""
    import json as _json
    from app.visa_snapshot import verified_overrides as vo
    f = tmp_path / "verified_overrides.json"
    f.write_text(_json.dumps([{
        "route": {"nationality": "CHN", "destination": "JPN"},
        "verified_at": "2026-08-22", "source_url": "https://www.mofa.go.jp/x",
        "fields": {"permitted_stay": "15 or 30 days, mission decides"}}]))
    monkeypatch.setattr(vo, "OVERRIDES", f)
    vo.reload()
    _seed(db)
    fetching.set_fetcher(lambda url, timeout_seconds=0: OFFICIAL_PAGE)
    freshness.set_provider(lambda system, user: {
        "page_relevant": True, "page_is_nationality_specific": True, "consistent": False,
        "corrected_fields": {"permitted_stay": "30 days"},
        "evidence": {"permitted_stay": "stay of 15 days or 30 days as decided by the mission"}, "note": ""})
    out = freshness.recheck_route(db, ROUTE)
    assert out["outcome"] == "checked"
    assert out["changed"] == [] and out["disputed"] == ["permitted_stay"]
    row = db.query(KimiRouteGuidanceCache).one()
    assert row.fresh_until is None            # disputed -> NOT refreshed
    issue = db.query(DatabaseIssueReport).one()
    assert issue.reported_by == "freshness_monitor"
    assert issue.status == "open"
    assert "30 days" in issue.note
    vo.reload()


def test_a_blocked_page_is_an_honest_failure_never_a_guess(db):
    _seed(db)
    fetching.set_fetcher(lambda url, timeout_seconds=0: FetchResult(
        requested_url=url, ok=True, final_url=url,
        final_hostname="www.mofa.go.jp", content_text="checking your browser",
        challenge=True))
    freshness.set_provider(lambda system, user: (_ for _ in ()).throw(
        AssertionError("the model must never be called for a blocked page")))
    out = freshness.recheck_route(db, ROUTE)
    assert out["outcome"] == "fetch_failed"
    row = db.query(KimiRouteGuidanceCache).one()
    assert row.guidance["permitted_stay"] == "90 days"   # untouched
    assert row.fresh_until is None                        # NOT renewed


def test_a_non_government_source_is_never_fetched(db):
    bad = dict(STALE_JPN, source_url="https://travel-blog.example.com/japan",
               official_portal_url=None)
    _seed(db, guidance=bad)
    fetching.set_fetcher(lambda url, timeout_seconds=0: (_ for _ in ()).throw(
        AssertionError("a non-government URL must never be fetched")))
    out = freshness.recheck_route(db, ROUTE)
    assert out["outcome"] == "no_official_source"


def test_a_correction_that_contradicts_itself_is_refused_and_filed(db):
    """The deterministic gate: a page 'correction' that makes the answer
    contradict itself (visa products dropped from a visa-required route) is
    refused wholesale and routed to the operator queue."""
    _seed(db)
    fetching.set_fetcher(lambda url, timeout_seconds=0: OFFICIAL_PAGE)
    freshness.set_provider(lambda system, user: {
        "page_relevant": True, "page_is_nationality_specific": True, "consistent": False,
        "corrected_fields": {"visa_products": []},
        "evidence": {"visa_products": "Single-entry temporary visitor visas"}, "note": ""})
    out = freshness.recheck_route(db, ROUTE)
    assert out["outcome"] == "checked"
    assert out["changed"] == [] and out["disputed"] == ["visa_products"]
    assert db.query(KimiRouteGuidanceCache).one() \
             .guidance["visa_products"], "products must survive"


def test_once_grounded_memory_regen_never_reverts_the_answer(db, monkeypatch):
    """The renewal doctrine: after a row has been checked against its page, a
    failed later recheck keeps the corrected answer — it must never fall back
    to regenerating from model memory, which is how a grounded correction
    would silently revert to the stale value."""
    row = _seed(db)
    fetching.set_fetcher(lambda url, timeout_seconds=0: OFFICIAL_PAGE)
    freshness.set_provider(lambda system, user: {
        "page_relevant": True, "page_is_nationality_specific": True, "consistent": False,
        "corrected_fields": {"government_fee": {"amount": 715, "currency": "CNY"}},
        "evidence": {"government_fee": "single entry 715 CNY"}, "note": ""})
    assert freshness.recheck_route(db, ROUTE)["changed"] == ["government_fee"]

    # Later, the page is unreachable AND the model's memory still says 200.
    fetching.set_fetcher(lambda url, timeout_seconds=0: FetchResult(
        requested_url=url, ok=False, error="timeout"))
    kimi_primary.set_provider(lambda system, user: (_ for _ in ()).throw(
        AssertionError("memory regeneration must not run for a grounded row")))
    from app.db import SessionLocal
    kimi_primary.refresh_stale_async(SessionLocal, ROUTE)
    db.expire_all()
    assert db.query(KimiRouteGuidanceCache).one() \
             .guidance["government_fee"] == {"amount": 715, "currency": "CNY"}


def test_an_irrelevant_landing_page_falls_through_to_the_real_one(db):
    """The Japan miss, fixed: the first source was the embassy homepage, which
    does not state the rule. An irrelevant page is a reason to try the NEXT
    source, not to abandon the route unchecked."""
    landing = FetchResult(
        requested_url="https://www.cn.emb-japan.go.jp/", ok=True,
        final_url="https://www.cn.emb-japan.go.jp/",
        final_hostname="www.cn.emb-japan.go.jp", http_status=200,
        content_text="Embassy of Japan in China. News, events, about us.",
        content_hash="home1")
    pages = {landing.final_url: landing,
             OFFICIAL_PAGE.final_url: OFFICIAL_PAGE}
    seen = []

    def _fetch(url, timeout_seconds=0):
        seen.append(url)
        return pages.get(url, FetchResult(requested_url=url, ok=False))
    fetching.set_fetcher(_fetch)

    def _judge(system, user):
        # Irrelevant for the homepage, a real verdict for the visa page.
        if "News, events" in user:
            return {"page_relevant": False, "consistent": True,
                    "corrected_fields": {}, "evidence": {}, "note": "landing"}
        return {"page_relevant": True, "page_is_nationality_specific": True, "consistent": False,
                "corrected_fields": {"government_fee": {"amount": 715,
                                                        "currency": "CNY"}},
                "evidence": {"government_fee": "single entry 715 CNY"},
                "note": "fee revised"}
    freshness.set_provider(_judge)

    _seed(db, guidance=dict(STALE_JPN,
                            official_portal_url=OFFICIAL_PAGE.final_url,
                            source_url=landing.final_url))
    out = freshness.recheck_route(db, ROUTE)
    assert seen[0] == landing.final_url, "the stored source is tried first"
    assert out["outcome"] == "checked"
    assert out["changed"] == ["government_fee"]
    assert out["source_url"] == OFFICIAL_PAGE.final_url


def test_when_no_source_states_the_rule_nothing_is_changed_or_refreshed(db):
    """Every page read, none of them about this route: an honest non-answer.
    The row keeps its answer and stays due — it is not marked fresh on the
    strength of pages that said nothing."""
    landing = FetchResult(
        requested_url="https://www.mofa.go.jp/j_info/visit/visa/index.html",
        ok=True, final_url="https://www.mofa.go.jp/j_info/visit/visa/index.html",
        final_hostname="www.mofa.go.jp", content_text="Ministry news index.",
        content_hash="h")
    fetching.set_fetcher(lambda url, timeout_seconds=0: landing)
    freshness.set_provider(lambda system, user: {
        "page_relevant": False, "consistent": True,
        "corrected_fields": {}, "evidence": {}, "note": ""})
    _seed(db)
    out = freshness.recheck_route(db, ROUTE)
    assert out["outcome"] == "page_not_relevant"
    assert out["source_reads"] == 1 and out["source_fetch_failures"] == 0
    row = db.query(KimiRouteGuidanceCache).one()
    assert row.guidance["permitted_stay"] == "90 days"
    assert row.fresh_until is None
    assert freshness.last_source_read_at(row.verification) == out['at']
    assert not freshness.effective_check(row.verification)


def test_a_disagreeing_check_is_not_served_as_a_clean_bill_of_health(db):
    """The page was read but disagreed. The served payload must carry
    consistent=False so the UI cannot print "read and matched" over a route
    whose own source contradicted it."""
    from app.db import SessionLocal
    _seed(db)
    fetching.set_fetcher(lambda url, timeout_seconds=0: OFFICIAL_PAGE)
    freshness.set_provider(lambda system, user: {
        "page_relevant": True, "page_is_nationality_specific": True, "consistent": False,
        "corrected_fields": {"permitted_stay": "15 or 30 days"},
        "evidence": {"permitted_stay": "stay of 15 days or 30 days as decided"},
        "note": ""})
    freshness.recheck_route(db, ROUTE)
    db.commit()
    kimi_primary.set_provider(lambda system, user: STALE_JPN)
    served = kimi_primary.get_route_guidance(SessionLocal(), ROUTE)
    assert served["cached"] is True
    assert served["grounded_check"]["consistent"] is False
    assert served["grounded_check"]["changed_fields"] == ["permitted_stay"]


def test_a_generic_page_cannot_touch_nationality_specific_fields(db):
    """THE ROOT CAUSE OF THE JAPAN DEMO FAILURE, pinned. A ministry page that
    describes the destination's rules for the WORLD (every channel listed, a
    90-day ceiling) is true in general and wrong for this applicant. Unless
    the page speaks for THIS nationality, it may not correct a
    nationality-specific field — enforced in code, not requested in the
    prompt."""
    _seed(db)
    from dataclasses import replace
    generic = replace(OFFICIAL_PAGE, content_text=(
        "Visa information. A stay of up to 90 days. Apply at the diplomatic mission. "
        "Processing takes 5 working days."))
    fetching.set_fetcher(lambda url, timeout_seconds=0: generic)
    freshness.set_provider(lambda system, user: {
        "page_relevant": True, "page_is_nationality_specific": False,
        "consistent": False,
        "corrected_fields": {
            "permitted_stay_days": 90,
            "application_channel": "embassy",
            "processing_time": "5 working days"},
        "evidence": {"permitted_stay_days": "stay of up to 90 days",
                     "application_channel": "apply at the diplomatic mission",
                     "processing_time": "Processing takes 5 working days"},
        "note": "generic page"})
    out = freshness.recheck_route(db, ROUTE)
    assert out["outcome"] == "page_not_relevant"
    # The nationality-specific corrections were skipped...
    assert "permitted_stay_days" not in out["changed"]
    assert "application_channel" not in out["changed"]
    assert set(out["generic_skipped"]) == {"application_channel",
                                           "permitted_stay_days"}
    # No generic discrepancy renews the route or silently disappears.
    assert out["changed"] == []
    assert set(out["disputed"]) == {"permitted_stay_days", "application_channel", "processing_time"}
    assert db.query(DatabaseIssueReport).count() == 1
    row = db.query(KimiRouteGuidanceCache).one()
    assert row.fresh_until is None
    assert row.guidance["permitted_stay"] == "90 days"  # untouched original
    assert row.guidance["application_channel"] == "authorised_agent"


def test_a_nationality_specific_page_may_correct_those_fields(db):
    _seed(db)
    fetching.set_fetcher(lambda url, timeout_seconds=0: OFFICIAL_PAGE)
    freshness.set_provider(lambda system, user: {
        "page_relevant": True, "page_is_nationality_specific": True,
        "consistent": False,
        "corrected_fields": {"permitted_stay_days": 30},
        "evidence": {"permitted_stay_days":
                     "stay of 15 days or 30 days as decided"},
        "note": "China-specific page"})
    out = freshness.recheck_route(db, ROUTE)
    assert out["changed"] == ["permitted_stay_days"]
    assert db.query(KimiRouteGuidanceCache).one() \
             .guidance["permitted_stay_days"] == 30


def test_a_prose_channel_can_never_survive_validation(db):
    """The recheck once wrote "diplomatic mission, accredited agency, Japan
    Visa Application Centre, or online" INTO the channel enum, which the UI
    renders through a fixed vocabulary. validate_answer now drops any value
    outside that vocabulary, on every path that produces an answer."""
    from app.visa_snapshot.kimi_primary import validate_answer
    base = {"disposition": "VISA_REQUIRED", "visa_category": "x",
            "permitted_stay": "x", "passport_validity": "x",
            "required_documents": ["p"], "processing_time": "x",
            "government_fee": {"amount": 1, "currency": "CNY"},
            "visa_products": [{"type": "t"}]}
    prose = "diplomatic mission, accredited agency, or online"
    clean, _m, _c = validate_answer({**base, "application_channel": prose})
    assert "application_channel" not in clean
    for ok in ("authorised_agent", "embassy", "visa_center",
               "online_portal", "on_arrival", "not_required"):
        clean, _m, _c = validate_answer({**base, "application_channel": ok})
        assert clean["application_channel"] == ok


def test_dead_links_are_stripped_and_bot_walls_are_kept():
    """Trip.com's demo complaint: the official-site link pointed nowhere.
    A dead link (hard 404 or a soft-404 page saying 'not found' under a 200)
    is removed from the answer; a bot-walled or slow page is NOT dead."""
    from app.visa_snapshot import url_health
    url_health.set_checker(lambda u: "dead" in u)
    try:
        g = {"official_portal_url": "https://example.com/dead/path",
             "source_url": "https://example.com/alive",
             "application_channel": "visa_center"}
        removed = url_health.strip_dead_links(g)
        assert removed == ["https://example.com/dead/path"]
        assert g["official_portal_url"] is None
        assert g["source_url"] == "https://example.com/alive"
    finally:
        url_health.set_checker(None)


def test_due_rows_selects_the_48_hour_backlog_oldest_first(db):
    """The automatic sweep's worklist: never-checked rows first, then rows
    whose last grounded check is older than the cycle; freshly checked rows
    and transit variants stay out; the cap holds."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    def seed(key_suffix, checked_at):
        row = KimiRouteGuidanceCache(
            cache_key=f"AAA|AAA|BB{key_suffix}|tourism|default|unknown|v6"
                      + ("" if "via" not in key_suffix else ""),
            route={"passport_nationality": "AAA"}, status="KIMI_PRIMARY",
            guidance=dict(STALE_JPN))
        if checked_at is not None:
            row.verification = {"grounded_check": {"at": checked_at,
                                                   "outcome": "checked"}}
        db.add(row)
        return row
    seed("1", None)                                             # never checked
    seed("2", (now - timedelta(hours=72)).isoformat())          # overdue
    seed("3", (now - timedelta(hours=3)).isoformat())           # fresh
    via = KimiRouteGuidanceCache(
        cache_key="AAA|AAA|BB4|tourism|default|unknown|v6|via:JPN",
        route={}, status="KIMI_PRIMARY", guidance=dict(STALE_JPN))
    db.add(via)
    db.commit()
    got = [r.cache_key for r in freshness.due_rows(db, older_than_hours=48)]
    assert "AAA|AAA|BB1|tourism|default|unknown|v6" == got[0]   # never first
    assert "AAA|AAA|BB2|tourism|default|unknown|v6" in got
    assert all("BB3" not in k and "via:" not in k for k in got)
    capped = freshness.due_rows(db, older_than_hours=48, limit=1)
    assert len(capped) == 1



def test_next_sweep_time_is_absent_when_timer_is_unavailable(monkeypatch):
    """A missing timer cannot earn a fabricated six-hour countdown."""
    import subprocess
    from app.main import _next_sweep_at
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: (_ for _ in ()).throw(FileNotFoundError()))
    assert _next_sweep_at() is None


def test_human_flags_get_an_ai_proposal_operators_accept_or_decline(db):
    """A human flag sends Ellis to the official page; what it finds waits in
    the queue as a proposal. Accepting writes a sourced override the next
    reader sees and moves the issue to corrected; the monitor's own disputes
    are refused by the accept path."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.visa_snapshot import verified_overrides as vo
    client = TestClient(app)
    ADMIN = {"authorization": "Bearer admin-token", "x-org-id": "org-b",
             "x-user-id": "op-1"}
    _seed(db)
    flagged = client.post("/database/report-issue", headers=ADMIN,
                          json={"nationality": "CHN", "destination": "JPN",
                                "travel_purpose": "tourism",
                                "field": "visa_fee_amount",
                                "note": "fee looks outdated"}).json()
    assert flagged["ok"]
    fetching.set_fetcher(lambda url, timeout_seconds=0: OFFICIAL_PAGE)
    freshness.set_provider(lambda system, user: {
        "page_relevant": True, "page_is_nationality_specific": True,
        "consistent": False,
        "corrected_fields": {"disposition": "VISA_REQUIRED", "government_fee": {"amount": 715,
                                                "currency": "CNY"}},
        "evidence": {"disposition": "Chinese nationals must obtain a visa for tourism in Japan", "government_fee": "single entry 715 CNY"},
        "note": "fee revised on the page"})
    prop = freshness.propose_for_issue(db, flagged["id"])
    assert prop["outcome"] == "checked"
    assert prop["fields"]["government_fee"]["page_says"]["amount"] == 715
    assert prop["fields"]["government_fee"]["quote"]
    ok = client.post(f"/database/issues/{flagged['id']}/accept-proposal",
                     headers=ADMIN).json()
    assert ok["ok"] and ok["status"] == "corrected"
    row = db.query(KimiRouteGuidanceCache).one()
    served, prov = vo.apply(dict(row.guidance), dict(row.route))
    assert served["government_fee"]["amount"] == 715
    assert "accepted by op-1" in (prov or {}).get("verified_by", "")
    vo.reload()


def test_a_failed_attempt_never_erases_a_good_check(db):
    """The eight-record regression of 2026-09-02: a grounded answer whose
    page later blocks robots (or whose provider errors) must STAY grounded.
    The failed attempt is recorded beside the last good read, never over it,
    so the 48-hour sweep can never demote a verified route to Low and hold
    it from readers."""
    _seed(db)
    fetching.set_fetcher(lambda url, timeout_seconds=0: OFFICIAL_PAGE)
    freshness.set_provider(lambda system, user: {
        "page_relevant": True, "page_is_nationality_specific": True,
        "consistent": True, "corrected_fields": {}, "evidence": {}, "note": ""})
    assert freshness.recheck_route(db, ROUTE)["outcome"] == "checked"
    good_at = db.query(KimiRouteGuidanceCache).one() \
                .verification["grounded_check"]["at"]

    for failure in ("blocked", "provider"):
        if failure == "blocked":
            fetching.set_fetcher(lambda url, timeout_seconds=0: FetchResult(
                requested_url=url, ok=True, final_url=url,
                final_hostname="www.mofa.go.jp",
                content_text="checking your browser", challenge=True))
            expected = "fetch_failed"
        else:
            fetching.set_fetcher(lambda url, timeout_seconds=0: OFFICIAL_PAGE)
            freshness.set_provider(lambda system, user: (_ for _ in ()).throw(
                RuntimeError("content_filter")))
            expected = "provider_error"
        assert freshness.recheck_route(db, ROUTE)["outcome"] == expected
        db.expire_all()
        row = db.query(KimiRouteGuidanceCache).one()
        # The attempt is on the record, honestly.
        assert row.verification["grounded_check"]["outcome"] == expected
        # And the good read still counts.
        eff = freshness.effective_check(row.verification)
        assert eff["outcome"] == "checked" and eff["consistent"] is True
        assert eff["at"] == good_at
        assert freshness.has_been_grounded(row)


def test_never_read_rows_have_no_effective_check(db):
    row = _seed(db)
    assert freshness.effective_check(row.verification) == {}
    fetching.set_fetcher(lambda url, timeout_seconds=0: FetchResult(
        requested_url=url, ok=False, error="timeout"))
    assert freshness.recheck_route(db, ROUTE)["outcome"] == "fetch_failed"
    assert not freshness.has_been_grounded(db.query(KimiRouteGuidanceCache).one())

def test_the_48_hour_drill_plants_catches_and_never_leaves_a_trace(db):
    """Their §VI.4 simulation as a product feature: plant a fake policy
    change, let the automatic recheck read the official page and put the
    record right, and read the elapsed time off the response. When the
    recheck misses, the drill restores the original itself."""
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    ADMIN = {"authorization": "Bearer admin-token", "x-org-id": "org-b",
             "x-user-id": "op-1"}
    _seed(db)
    from dataclasses import replace
    page = replace(OFFICIAL_PAGE, content_text=OFFICIAL_PAGE.content_text.replace("715 CNY", "200 CNY"))
    fetching.set_fetcher(lambda url, timeout_seconds=0: page)
    freshness.set_provider(lambda system, user: {
        "page_relevant": True, "page_is_nationality_specific": True,
        "consistent": False,
        "corrected_fields": {"government_fee": {"amount": 200,
                                                "currency": "CNY"}},
        "evidence": {"government_fee": "single entry 200 CNY"},
        "note": "drill corrected"})
    out = client.post("/database/freshness/drill", headers=ADMIN,
                      json={"nationality": "CHN", "destination": "JPN"}).json()
    assert out["ok"] and out["field"] == "government_fee"
    assert out["caught"] is True and out["seconds"] >= 0
    row = db.query(KimiRouteGuidanceCache).one()
    assert row.guidance["government_fee"]["amount"] == 200
    # Miss path: a recheck that calls the planted value consistent.
    freshness.set_provider(lambda system, user: {
        "page_relevant": True, "page_is_nationality_specific": True,
        "consistent": True, "corrected_fields": {}, "evidence": {}})
    out2 = client.post("/database/freshness/drill", headers=ADMIN,
                       json={"nationality": "CHN", "destination": "JPN"}).json()
    assert out2["caught"] is False and out2["restored"] is True
    db.expire_all()
    row = db.query(KimiRouteGuidanceCache).one()
    assert row.guidance["government_fee"]["amount"] == 200       # no trace


def test_due_rows_takes_never_checked_first_and_respects_the_threshold(db):
    """The sweep's worklist: never-checked rows first, then the oldest, and
    nothing younger than the threshold. The threshold the sweep script uses
    excludes only the last 15 minutes so late rows in a five-hour run are
    eligible again at the next six-hour start."""
    from datetime import datetime, timedelta, timezone
    import importlib.util, pathlib
    now = datetime.now(timezone.utc)
    for i, hours in enumerate([None, 50, 30, 45]):
        row = KimiRouteGuidanceCache(
            cache_key=f"CHN|CHN|X{i}|tourism|default|unknown|{kimi_primary.CACHE_VERSION}",
            route={"passport_nationality": "CHN", "destination_country": f"X{i}", "travel_purpose": "tourism"},
            guidance={"disposition": "VISA_REQUIRED"}, status=kimi_primary.STATUS_PRIMARY,
            verification={} if hours is None else {"grounded_check": {"at": (now - timedelta(hours=hours)).isoformat(), "outcome": "checked"}})
        db.add(row)
    db.commit()
    due = freshness.due_rows(db, older_than_hours=40, limit=10)
    keys = [r.cache_key.split("|")[2] for r in due]
    assert keys == ["X0", "X1", "X3"]          # never, 50h, 45h; the 30h row waits
    spec = importlib.util.spec_from_file_location(
        "sweep", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "freshness_sweep.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    assert mod.DUE_AFTER_HOURS == 0.25 and mod.MAX_SECONDS <= 5 * 3600


def test_a_page_cannot_correct_a_field_to_nothing(db):
    """The monitor applies values a page states and never an absence: an
    empty correction is dropped, the sourced value survives."""
    _seed(db)
    from dataclasses import replace
    page = replace(OFFICIAL_PAGE, content_text=OFFICIAL_PAGE.content_text +
                   " Applications are processed within 8 working days.")
    fetching.set_fetcher(lambda url, timeout_seconds=0: page)
    freshness.set_provider(lambda system, user: {
        "page_relevant": True, "page_is_nationality_specific": True,
        "consistent": False,
        "corrected_fields": {"application_channel": None, "exceptions": [],
                             "processing_time": "8 working days"},
        "evidence": {"application_channel": "no channel named",
                     "exceptions": "none listed",
                     "processing_time": "processed within 8 working days"}})
    row = db.query(KimiRouteGuidanceCache).one()
    before_channel = row.guidance.get("application_channel")
    freshness.recheck_row(db, row)
    db.expire_all()
    row = db.query(KimiRouteGuidanceCache).one()
    assert row.guidance.get("application_channel") == before_channel
    assert row.guidance.get("processing_time") == "8 working days"


@pytest.mark.parametrize("claims_specific", [False, True])
def test_menu_text_cannot_confirm_any_route_even_when_model_claims_it_does(db, claims_specific):
    from dataclasses import replace
    row = _seed(db)
    fetching.set_fetcher(lambda url, timeout_seconds=0: replace(
        OFFICIAL_PAGE, content_text="Visa services home page. Search contact help login " * 8))
    freshness.set_provider(lambda *_: {"page_relevant": True,
        "page_is_nationality_specific": claims_specific, "consistent": True})
    out = freshness.recheck_row(db, row)
    assert out["outcome"] == "page_not_relevant"
    assert row.fresh_until is None and freshness.effective_check(row.verification) == {}


def test_entire_quote_must_appear_and_unquoted_correction_blocks_renewal(db):
    row = _seed(db)
    fetching.set_fetcher(lambda url, timeout_seconds=0: OFFICIAL_PAGE)
    freshness.set_provider(lambda *_: {"page_relevant": True, "page_is_nationality_specific": True,
        "consistent": False, "corrected_fields": {
            "government_fee": {"amount": 715, "currency": "CNY"}, "permitted_stay": "60 days"},
        "evidence": {"government_fee": " SINGLE  ENTRY  715 CNY ",
                     "permitted_stay": "Single-entry temporary visitor visas for tourism permit a stay of 60 days"}})
    out = freshness.recheck_row(db, row)
    assert out["changed"] == ["government_fee"]
    assert out["unquoted_fields"] == ["permitted_stay"]
    assert row.guidance["permitted_stay"] == "90 days" and row.fresh_until is None


def test_irrelevant_same_page_retires_prior_check_but_other_page_does_not(db):
    row = _seed(db)
    good = {"outcome": "checked", "evidence_contract": freshness.EVIDENCE_CONTRACT, "consistent": True, "source_url": OFFICIAL_PAGE.final_url}
    freshness._stamp(row, good)
    freshness._stamp(row, {"outcome": "page_not_relevant", "sources_tried": ["https://www.mofa.go.jp/other"]})
    assert freshness.effective_check(row.verification) == good
    freshness._stamp(row, {"outcome": "page_not_relevant", "sources_tried": [OFFICIAL_PAGE.final_url]})
    assert freshness.effective_check(row.verification) == {}
    assert row.verification["superseded_check"] == good


@pytest.mark.parametrize("missing", [[], ["government_fee"]])
def test_uncertain_rows_never_get_primary_ttl(db, missing):
    from datetime import datetime, timedelta, timezone
    row = _seed(db, guidance={"disposition": "VISA_REQUIRED", "source_url": OFFICIAL_PAGE.final_url})
    row.status, row.missing_fields = kimi_primary.STATUS_UNCERTAIN, missing
    db.commit()
    fetching.set_fetcher(lambda url, timeout_seconds=0: OFFICIAL_PAGE)
    freshness.set_provider(lambda *_: {"page_relevant": True, "page_is_nationality_specific": True,
                                      "consistent": True, "corrected_fields": {}})
    freshness.recheck_row(db, row)
    if missing:
        assert row.fresh_until is None
    else:
        assert row.fresh_until <= datetime.now(timezone.utc) + timedelta(days=kimi_primary.UNCERTAIN_TTL_DAYS)


def test_only_canonical_complete_detail_rows_are_scheduled(db):
    canonical = _seed(db)
    for key in (canonical.cache_key.replace("CHN|CHN|", "CHN|SGP|"),
                canonical.cache_key.replace("unknown", "2026-09"), canonical.cache_key + "|via:SGP"):
        db.add(KimiRouteGuidanceCache(cache_key=key, route=ROUTE, guidance=STALE_JPN, status="KIMI_PRIMARY"))
    pending = _seed(db, route={**ROUTE, "destination_country": "AAA"})
    pending.verification = {"detail_pending": True}
    db.commit()
    assert freshness.due_rows(db) == [canonical]
    fetching.set_fetcher(lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must await detail")))
    assert freshness.recheck_row(db, pending)["outcome"] == "detail_pending"


def test_recheck_writes_only_corrected_fields_without_baking_override(db, monkeypatch):
    from dataclasses import replace
    from app.visa_snapshot import verified_overrides as vo
    row = _seed(db)
    override = {"fields": {"disposition": "VISA_REQUIRED", "government_fee": {"amount": 715, "currency": "CNY"}},
                "source_url": OFFICIAL_PAGE.final_url}
    monkeypatch.setattr(vo, "find", lambda *_: override)
    monkeypatch.setattr(vo, "apply", lambda g, _: ({**g, **override["fields"]}, None))
    page = replace(OFFICIAL_PAGE, content_text=OFFICIAL_PAGE.content_text + " Processing takes 8 working days.")
    fetching.set_fetcher(lambda *_a, **_k: page)
    freshness.set_provider(lambda *_: {"page_relevant": True, "page_is_nationality_specific": True,
        "consistent": False, "corrected_fields": {"processing_time": "8 working days"},
        "evidence": {"processing_time": "Processing takes 8 working days"}})
    out = freshness.recheck_row(db, row)
    assert out["changed"] == ["processing_time"]
    assert row.guidance == {**STALE_JPN, "processing_time": "8 working days"}


def test_integrity_sweep_files_one_issue_per_canonical_conflict(db):
    row = _seed(db, guidance={**STALE_JPN, "disposition": "VISA_EXEMPT"})
    db.add(KimiRouteGuidanceCache(cache_key=row.cache_key + "|via:SGP", route=ROUTE,
                                  guidance=dict(row.guidance), status="KIMI_PRIMARY"))
    db.commit()
    assert freshness.audit_integrity(db) == {"checked": 1, "violated": 1, "created": 1, "resolved": 0}
    assert freshness.audit_integrity(db) == {"checked": 1, "violated": 1, "created": 0, "resolved": 0}
    issue = db.query(DatabaseIssueReport).one()
    assert issue.field == "integrity" and issue.proposal["contradictions"]


def test_human_proposal_discards_fabricated_quote(db):
    row = _seed(db)
    issue = DatabaseIssueReport(org_id="platform", cache_key=row.cache_key, route=ROUTE,
        field="permitted_stay", note="check", reported_by="person", status="open")
    db.add(issue); db.commit()
    fetching.set_fetcher(lambda *_a, **_k: OFFICIAL_PAGE)
    freshness.set_provider(lambda *_: {"page_relevant": True, "page_is_nationality_specific": True,
        "consistent": False, "corrected_fields": {"permitted_stay": "60 days"},
        "evidence": {"permitted_stay": "Chinese nationals may stay 60 days"}})
    proposal = freshness.propose_for_issue(db, issue.id)
    assert proposal["fields"] == {} and proposal["unquoted_fields"] == ["permitted_stay"]
    assert proposal["consistent"] is False



def test_active_material_disputes_survive_new_check_but_outages_do_not(db):
    row = _seed(db)
    for field, status in (("source_unreadable", "open"), ("integrity", "open"),
                          ("government_fee", "acknowledged"), ("permitted_stay", "corrected")):
        db.add(DatabaseIssueReport(org_id="platform", cache_key=row.cache_key,
            route=ROUTE, field=field, note="check", reported_by="freshness_monitor", status=status))
    freshness._stamp(row, {"outcome": "checked", "consistent": True})
    db.commit()
    assert freshness.active_disputed_fields(db, row.cache_key + "|via:SGP") == ["government_fee", "integrity"]


def test_mismatched_issue_identity_never_reads_another_routes_page(db):
    row = _seed(db)
    issue = DatabaseIssueReport(org_id="platform", cache_key=row.cache_key,
        route={**ROUTE, "destination_country": "VNM"}, field="disposition",
        note="mismatched route", reported_by="person", status="open")
    db.add(issue); db.commit()
    fetching.set_fetcher(lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("wrong route must not be read")))
    assert freshness.propose_for_issue(db, issue.id)["outcome"] == "route_identity_mismatch"


def test_home_government_outbound_rule_requires_destination_context():
    raw = {"page_relevant": True, "page_is_nationality_specific": True}
    route = {"passport_nationality": "HKG", "destination_country": "VNM"}
    good = "Visa requirements for Hong Kong SAR passport holders. Vietnam: visa required."
    assert freshness._supports_route(good, raw, route, {"disposition": "VISA_REQUIRED"}, {}, "https://www.immd.gov.hk/visa")
    wrong = "Visa requirements for Hong Kong SAR passport holders. Japan: visa required."
    assert not freshness._supports_route(wrong, raw, route, {"disposition": "VISA_REQUIRED"}, {}, "https://www.immd.gov.hk/visa")
    assert not freshness._source_authority_matches("https://www.exteriores.gob.es/visa", route)


def test_curated_uae_embassy_supports_uae_route_only():
    url = "https://www.uae-embassy.org/visas-services/visas-for-non-us-citizens"
    route = {"passport_nationality": "CAN", "destination_country": "ARE"}
    raw = {"page_relevant": True, "page_is_nationality_specific": True}
    text = "Canadian passport holders may obtain a visa on arrival for tourism."
    assert freshness._supports_route(text, raw, route,
        {"disposition": "VISA_ON_ARRIVAL"}, {}, url)
    for destination in ("USA", "HKG", "TGO"):
        assert not freshness._source_authority_matches(url, {**route,
            "destination_country": destination})


def test_future_schedule_source_cannot_rewrite_current_canonical_answer(db):
    from dataclasses import replace
    from app.visa_snapshot import scheduled_policies as sp
    policy = next(p for p in sp._load() if p["route"]["nationality"] == "HKG")
    route = {**ROUTE, "passport_nationality": "HKG", "destination_country": "THA"}
    row = _seed(db, guidance={**STALE_JPN, "source_url": policy["source_url"]}, route=route)
    page = replace(OFFICIAL_PAGE, final_url=policy["source_url"], final_hostname="consular.mfa.go.th",
                   content_text="Hong Kong passport holders are visa-exempt for 30 days, effective from 15 September 2026.")
    fetching.set_fetcher(lambda *_a, **_k: page)
    freshness.set_provider(lambda *_: (_ for _ in ()).throw(AssertionError("future page cannot correct today's raw row")))
    before = dict(row.guidance)
    out = freshness.recheck_row(db, row, today="2026-09-09T12:00:00Z")
    assert out["outcome"] == "page_not_relevant"
    assert row.guidance == before and row.fresh_until is None



def test_legacy_weak_page_checks_have_no_verification_credit(db):
    row = _seed(db)
    legacy = {"outcome": "checked", "consistent": True, "source_url": OFFICIAL_PAGE.final_url}
    row.verification = {"grounded_check": legacy, "last_good_check": legacy}
    assert freshness.effective_check(row.verification) == {}
    assert not freshness.has_been_grounded(row)
    assert row.verification["last_good_check"] == legacy  # retained as audit history


@pytest.mark.parametrize('text,route,disposition,url', [
    ('Canadian diplomatic passport holders may enter Japan visa-free.',
     {'passport_nationality': 'CAN', 'destination_country': 'JPN'}, 'VISA_EXEMPT', 'https://www.mofa.go.jp/visa'),
    ('Canadian citizens may visit Japan for tourism visa-free.',
     {'passport_nationality': 'CAN', 'destination_country': 'JPN', 'travel_purpose': 'work'}, 'VISA_EXEMPT', 'https://www.mofa.go.jp/visa'),
    ('From 1 December 2030 Canadian citizens must obtain a visa for Japan.',
     {'passport_nationality': 'CAN', 'destination_country': 'JPN'}, 'VISA_REQUIRED', 'https://www.mofa.go.jp/visa'),
    ('United States passport holders should check travel advice. India allows Nepal nationals to enter visa-free.',
     {'passport_nationality': 'USA', 'destination_country': 'IND'}, 'VISA_EXEMPT', 'https://travel.state.gov/visa'),
])
def test_different_document_purpose_future_date_or_nationality_never_supports_route(text, route, disposition, url):
    raw = {'page_relevant': True, 'page_is_nationality_specific': True}
    assert not freshness._supports_route(text, raw, route, {'disposition': disposition}, {}, url, '2026-09-09')


def test_literal_but_unrelated_quote_cannot_apply_invented_fee(db):
    row = _seed(db)
    fetching.set_fetcher(lambda *_a, **_k: OFFICIAL_PAGE)
    freshness.set_provider(lambda *_: {'page_relevant': True, 'page_is_nationality_specific': True,
        'consistent': False, 'corrected_fields': {'government_fee': {'amount': 999, 'currency': 'CNY'}},
        'evidence': {'government_fee': 'Chinese nationals must obtain a visa for tourism in Japan'}})
    result = freshness.recheck_row(db, row)
    assert result['changed'] == [] and result['unquoted_fields'] == ['government_fee']
    assert row.guidance['government_fee']['amount'] == 200 and row.fresh_until is None


def test_verdict_only_page_does_not_renew_unchecked_detail_fields(db):
    from dataclasses import replace
    row = _seed(db)
    fetching.set_fetcher(lambda *_a, **_k: replace(OFFICIAL_PAGE,
        content_text='Chinese nationals must obtain a visa for tourism in Japan.'))
    freshness.set_provider(lambda *_: {'page_relevant': True, 'page_is_nationality_specific': True,
        'consistent': True, 'corrected_fields': {}, 'evidence': {}})
    freshness.recheck_row(db, row)
    check = freshness.effective_check(row.verification)
    assert check['consistent'] and check['verified_fields'] == ['disposition']
    assert 'government_fee' in check['unverified_fields'] and not check['renewed']
    assert row.fresh_until is None


def test_corrupt_or_future_attempt_timestamps_never_hide_due_rows(db):
    from datetime import datetime, timedelta, timezone
    for index, stamp in enumerate(['zzzz', '9999-12-31T00:00:00+00:00',
                                   (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()]):
        route = {**ROUTE, 'destination_country': ['JPN', 'VNM', 'CAN'][index]}
        row = _seed(db, route=route)
        row.verification = {'grounded_check': {'at': stamp}}
    db.commit()
    assert len(freshness.due_rows(db)) == 3


def test_malformed_cached_payload_gets_integrity_issue_without_aborting_audit(db):
    row = _seed(db)
    row.guidance = 'corrupt JSON shape'
    db.commit()
    assert freshness.audit_integrity(db) == {'checked': 1, 'violated': 1, 'created': 1, 'resolved': 0}
    assert db.query(DatabaseIssueReport).one().proposal['contradictions'] == ['Cached route and guidance must be objects']


def test_second_field_source_is_checked_after_matching_headline_and_corrects_fee(db):
    import json
    from dataclasses import replace
    fee_url = 'https://www.mofa.go.jp/visa/fees'
    row = _seed(db, guidance={**STALE_JPN, 'corroborating_sources': [{'url': fee_url}]})
    fetched = []
    def fetch(url, **_):
        fetched.append(url)
        return replace(OFFICIAL_PAGE, final_url=url, content_text=(
            'Chinese nationals must obtain a visa for tourism in Japan. '
            + ('The visa fee is 715 CNY.' if url == fee_url else 'See the separate fee schedule.')))
    def answer(_system, user):
        fee = json.loads(user)['official_page_url'] == fee_url
        return {'page_relevant': True, 'page_is_nationality_specific': True, 'consistent': not fee,
            'corrected_fields': {'government_fee': {'amount': 715, 'currency': 'CNY'}} if fee else {},
            'evidence': {'government_fee': 'The visa fee is 715 CNY'} if fee else {}}
    fetching.set_fetcher(fetch); freshness.set_provider(answer)
    result = freshness.recheck_row(db, row)
    assert fetched == [OFFICIAL_PAGE.final_url, fee_url]
    assert result['changed'] == ['government_fee'] and row.guidance['government_fee']['amount'] == 715
    check = freshness.effective_check(row.verification)
    assert len(check['source_checks']) == 2
    assert check['field_sources']['government_fee']['source_url'] == fee_url
    assert check['field_sources']['government_fee']['quote'] == 'The visa fee is 715 CNY'


def test_freshness_renews_only_when_all_fields_are_supported_across_sources(db):
    import json
    from dataclasses import replace
    fee_url = 'https://www.mofa.go.jp/visa/fees'
    row = _seed(db, guidance={'disposition': 'VISA_REQUIRED', 'permitted_stay': '30 days',
        'government_fee': {'amount': 7, 'currency': 'USD'}, 'source_url': OFFICIAL_PAGE.final_url,
        'visa_products': [], 'corroborating_sources': [{'url': fee_url}]})
    def fetch(url, **_):
        return replace(OFFICIAL_PAGE, final_url=url, content_text=(
            'Chinese nationals must obtain a visa for tourism in Japan. '
            + ('The visa fee is 7 USD.' if url == fee_url else 'The permitted stay is 30 days.')))
    def answer(_system, user):
        fee = json.loads(user)['official_page_url'] == fee_url
        return {'page_relevant': True, 'page_is_nationality_specific': True, 'consistent': True,
            'corrected_fields': {}, 'evidence': {'government_fee': 'The visa fee is 7 USD'} if fee
            else {'permitted_stay': 'The permitted stay is 30 days'}}
    fetching.set_fetcher(fetch); freshness.set_provider(answer)
    freshness.recheck_row(db, row)
    check = freshness.effective_check(row.verification)
    assert check['verified_fields'] == ['disposition', 'government_fee', 'permitted_stay']
    assert check['unverified_fields'] == [] and check['renewed'] and row.fresh_until is not None


def test_generic_fee_page_disagreement_is_not_hidden_by_first_matching_page(db):
    import json
    from dataclasses import replace
    fee_url = 'https://www.mofa.go.jp/visa/fees'
    row = _seed(db, guidance={**STALE_JPN, 'visa_products': [
        {**STALE_JPN['visa_products'][0], 'source_url': fee_url}]})
    fetching.set_fetcher(lambda url, **_: replace(OFFICIAL_PAGE, final_url=url, content_text=(
        'The visa fee is 715 CNY.' if url == fee_url else OFFICIAL_PAGE.content_text)))
    def answer(_system, user):
        fee = json.loads(user)['official_page_url'] == fee_url
        return {'page_relevant': True, 'page_is_nationality_specific': not fee, 'consistent': not fee,
            'corrected_fields': {'government_fee': {'amount': 715, 'currency': 'CNY'}} if fee else {},
            'evidence': {'government_fee': 'The visa fee is 715 CNY'} if fee else {}}
    freshness.set_provider(answer)
    freshness.recheck_row(db, row)
    issue = db.query(DatabaseIssueReport).one()
    assert issue.field == 'government_fee' and issue.proposal['source_url'] == fee_url
    assert row.guidance['government_fee']['amount'] == 200 and row.fresh_until is None
    assert freshness.effective_check(row.verification)['consistent'] is False


def test_source_budget_rotates_overflow_instead_of_ignoring_later_citations(db):
    from dataclasses import replace
    links = [f'https://www.mofa.go.jp/visa/field{i}' for i in range(12)]
    row = _seed(db, guidance={'disposition': 'VISA_REQUIRED', 'source_url': links[0],
        'corroborating_sources': [{'url': u} for u in links[1:]]})
    fetched = []
    def fetch(url, **_):
        fetched.append(url)
        return replace(OFFICIAL_PAGE, final_url=url)
    fetching.set_fetcher(fetch)
    freshness.set_provider(lambda *_: {'page_relevant': True, 'page_is_nationality_specific': True,
        'consistent': True, 'corrected_fields': {}})
    freshness.recheck_row(db, row)
    first = set(fetched)
    assert len(first) == freshness.MAX_SOURCES and freshness.effective_check(row.verification)['unchecked_sources']
    assert row.fresh_until is None
    freshness.recheck_row(db, row)
    assert set(fetched) == set(links)


def test_fee_issue_does_not_stop_at_verdict_only_headline(db):
    import json
    from dataclasses import replace
    fee_url = 'https://www.mofa.go.jp/visa/fee-flag'
    row = _seed(db, guidance={**STALE_JPN, 'corroborating_sources': [{'url': fee_url}]})
    issue = DatabaseIssueReport(org_id='platform', cache_key=row.cache_key, route=ROUTE,
        field='visa_fee_amount', note='fee changed', reported_by='reader', status='open')
    db.add(issue); db.commit()
    fetching.set_fetcher(lambda url, **_: replace(OFFICIAL_PAGE, final_url=url))
    def answer(_system, user):
        fee = json.loads(user)['official_page_url'] == fee_url
        return {'page_relevant': True, 'page_is_nationality_specific': True, 'consistent': not fee,
            'corrected_fields': {'government_fee': {'amount': 715, 'currency': 'CNY'}} if fee else {},
            'evidence': {'government_fee': 'single entry 715 CNY'} if fee else {}}
    freshness.set_provider(answer)
    proposal = freshness.propose_for_issue(db, issue.id)
    assert proposal['source_url'] == fee_url and not proposal['consistent']
    assert proposal['fields']['government_fee']['page_says']['amount'] == 715
    assert row.guidance['government_fee']['amount'] == 200


def test_repaired_integrity_issue_is_corrected_without_closing_source_or_user_disputes(db):
    row = _seed(db, guidance={**STALE_JPN, 'disposition': 'VISA_EXEMPT'})
    freshness.audit_integrity(db); freshness.audit_integrity(db)
    own = db.query(DatabaseIssueReport).one()
    old_proposal = dict(own.proposal)
    source = DatabaseIssueReport(org_id='platform', cache_key=row.cache_key, route=ROUTE,
        field='government_fee', note='source disagreement', reported_by='freshness_monitor', status='open')
    human = DatabaseIssueReport(org_id='platform', cache_key=row.cache_key, route=ROUTE,
        field='integrity', note='reader complaint', reported_by='reader', status='open')
    db.add_all([source, human])
    row.guidance = dict(STALE_JPN)
    db.commit()
    result = freshness.audit_integrity(db)
    assert result == {'checked': 1, 'violated': 0, 'created': 0, 'resolved': 1}
    assert own.status == 'corrected' and own.resolved_by == 'freshness_monitor' and own.resolved_at
    assert own.proposal['contradictions'] == old_proposal['contradictions']
    assert own.proposal['resolution_check']['outcome'] == 'integrity_passed'
    assert source.status == human.status == 'open'
    assert freshness.active_disputed_fields(db, row.cache_key) == ['government_fee']
    assert freshness.audit_integrity(db)['resolved'] == 0


def test_source_cursor_resumes_actual_attempts_after_partial_route_budget(db):
    import threading
    from dataclasses import replace
    links = [f'https://www.mofa.go.jp/visa/rotate{i}' for i in range(4)]
    row = _seed(db, guidance={'disposition': 'VISA_REQUIRED', 'source_url': links[0],
        'corroborating_sources': [{'url': u} for u in links[1:]]})
    fetched = []
    stop = threading.Event()
    fetching.set_fetcher(lambda url, **_: (fetched.append(url) or replace(OFFICIAL_PAGE, final_url=url)))
    def answer(*_):
        stop.set()
        return {'page_relevant': True, 'page_is_nationality_specific': True,
            'consistent': True, 'corrected_fields': {}}
    freshness.set_provider(answer)
    freshness.recheck_row(db, row, should_stop=stop.is_set)
    assert fetched == links[:1] and row.verification['grounded_check']['source_cursor'] == 1
    stop.clear()
    freshness.recheck_row(db, row, should_stop=stop.is_set)
    assert fetched == links[:2]
    assert row.verification['grounded_check']['unchecked_sources']


def test_previous_cycles_late_rows_are_due_at_next_six_hour_start(db):
    from datetime import datetime, timedelta, timezone
    row = _seed(db)
    row.verification = {'grounded_check': {'at': (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}}
    db.commit()
    assert freshness.due_rows(db, older_than_hours=0.25) == [row]


def test_dispute_snapshot_answers_exactly_like_the_per_route_query(db):
    # A finding stored under a transit variant never answered for the
    # canonical route (the per-route query compares the stored key to the
    # canonical key). The whole-inventory snapshot must not fold it in.
    row = _seed(db)
    db.add(DatabaseIssueReport(org_id="platform", cache_key=row.cache_key + "|via:SGP",
        route=ROUTE, field="government_fee", note="check", reported_by="freshness_monitor", status="open"))
    db.add(DatabaseIssueReport(org_id="platform", cache_key=row.cache_key,
        route=ROUTE, field="permitted_stay", note="check", reported_by="freshness_monitor", status="open"))
    db.commit()
    for key in (row.cache_key, row.cache_key + "|via:SGP"):
        outside = freshness.active_disputed_fields(db, key)
        with freshness.disputed_fields_snapshot(db):
            inside = freshness.active_disputed_fields(db, key)
        assert outside == inside == ["permitted_stay"], (key, outside, inside)
