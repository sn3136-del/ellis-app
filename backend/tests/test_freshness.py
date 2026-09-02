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
    content_text=("Visa fees revised 1 July 2026: single entry 715 CNY. "
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
            "permitted_stay": "a stay of 15 days or 30 days as decided",
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
    assert row.fresh_until is not None       # checked -> fresh again


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
        "corrected_fields": {"permitted_stay": "60 days"},
        "evidence": {"permitted_stay": "some new wording"}, "note": ""})
    out = freshness.recheck_route(db, ROUTE)
    assert out["outcome"] == "checked"
    assert out["changed"] == [] and out["disputed"] == ["permitted_stay"]
    row = db.query(KimiRouteGuidanceCache).one()
    assert row.fresh_until is None            # disputed -> NOT refreshed
    issue = db.query(DatabaseIssueReport).one()
    assert issue.reported_by == "freshness_monitor"
    assert issue.status == "open"
    assert "60 days" in issue.note
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
        "evidence": {"visa_products": "some quote"}, "note": ""})
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
    row = db.query(KimiRouteGuidanceCache).one()
    assert row.guidance["permitted_stay"] == "90 days"
    assert row.fresh_until is None


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
        "evidence": {"permitted_stay": "15 days or 30 days as decided"},
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
    fetching.set_fetcher(lambda url, timeout_seconds=0: OFFICIAL_PAGE)
    freshness.set_provider(lambda system, user: {
        "page_relevant": True, "page_is_nationality_specific": False,
        "consistent": False,
        "corrected_fields": {
            "permitted_stay_days": 90,
            "application_channel": "embassy",
            "processing_time": "5 working days from application"},
        "evidence": {"permitted_stay_days": "stay of up to 90 days",
                     "application_channel": "apply at the diplomatic mission",
                     "processing_time": "five working days"},
        "note": "generic page"})
    out = freshness.recheck_route(db, ROUTE)
    assert out["outcome"] == "checked"
    # The nationality-specific corrections were skipped...
    assert "permitted_stay_days" not in out["changed"]
    assert "application_channel" not in out["changed"]
    assert set(out["generic_skipped"]) == {"application_channel",
                                           "permitted_stay_days"}
    # ...while a non-nationality field from the same page still applies.
    assert out["changed"] == ["processing_time"]
    row = db.query(KimiRouteGuidanceCache).one()
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
                     "Chinese nationals: 15 or 30 days as decided"},
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
    fetching.set_fetcher(lambda url, timeout_seconds=0: OFFICIAL_PAGE)
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
