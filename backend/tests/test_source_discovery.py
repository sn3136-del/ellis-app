"""Automatic official-source discovery + Browserbase render fallback.

Covers the root-cause fix: on-demand research used to starve at
DISCOVER_OFFICIAL_SOURCES because no search provider was wired outside tests.
These tests exercise the Kimi-backed discovery provider (candidates proposed,
sanitized, honestly unavailable without a key), the render-fetch fallback, and a
full on-demand job that completes via the route-aware discovery path — all with
injected providers, never touching a real network or the Moonshot key.
"""
import pytest
from sqlalchemy import select

from app.db import SessionLocal, create_all
from app.visa_snapshot import fetching, kimi_research, ondemand, pipeline, source_discovery
from app.visa_snapshot.fetching import FetchResult
from app.visa_snapshot.models import VisaRoute
from app.visa_snapshot.routekey import RouteInput, normalize_route, route_key

# Un-researched destinations no other test module caches (the shared session DB
# is populated by snapshot-importing tests) — so research genuinely runs instead
# of short-circuiting at CHECK_STORED_POLICY on a stored/verified route.
DEST_OK = "BR"     # Brazil — route completes via discovery in this test
DEST_NONE = "PE"   # Peru — "finds nothing" honesty test

GOV_URL = "https://www.evisa.gov.kh/info"      # .gov.kh — government suffix
GOV_ROOT = "https://www.evisa.gov.kh/"
BLOG_URL = "https://travelblog.example/cambodia"

ROUTE_ANSWERS = {
    "passport_nationality": "SGP", "passport_issuing_country": "SG",
    "travel_document_type": "ordinary_passport", "lawful_country_of_residence": "SG",
    "destination_country": "KH", "visa_category": "evisa_tourist",
    "travel_purpose": "tourism", "arrival_date": "2026-10-01",
    "departure_date": "2026-10-08", "age": 30, "email": "od@example.com",
    "preferred_language": "en",
}


def _ri():
    return RouteInput(passport_nationality="SGP", passport_issuing_country="SG",
                      travel_document_type="ordinary_passport",
                      lawful_country_of_residence="SG", destination_country="KH",
                      visa_category="evisa_tourist", policy_period="2026-10-01")


@pytest.fixture()
def db():
    create_all()
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "RESEARCH_DIR", tmp_path / "research")
    yield
    fetching.set_fetcher(None)
    fetching.set_search_provider(None)
    fetching.set_render_fetcher(None)
    kimi_research.set_extractor(None)
    source_discovery.set_proposer(None)


# ---------------------------------------------------------------- sanitizing --
def test_sanitize_urls_drops_junk_dedupes_and_caps():
    raw = ["https://a.gov.kh/", "http://b.gov.kh", "not-a-url", "",
           "ftp://x.gov", "https://a.gov.kh/", "javascript:alert(1)",
           "https://c.gov.kh/page.", None]
    out = source_discovery._sanitize_urls(raw)
    assert out == ["https://a.gov.kh/", "http://b.gov.kh", "https://c.gov.kh/page"]
    big = [f"https://d{i}.gov.kh/" for i in range(50)]
    assert len(source_discovery._sanitize_urls(big)) == source_discovery._MAX_CANDIDATES


# ---------------------------------------------------------------- availability --
def test_unavailable_without_key_or_proposer():
    # conftest wipes MOONSHOT_API_KEY -> discovery honestly unavailable.
    assert source_discovery.is_available() is False
    assert source_discovery.default_search_provider() is None
    assert source_discovery.discover_candidates_for_route(
        {"destination_country": "KHM"}) is None


def test_search_raises_when_unavailable():
    with pytest.raises(fetching.SearchUnavailable):
        fetching.search("KHM official tourist visa SGP")


def test_injected_proposer_makes_discovery_available():
    source_discovery.set_proposer(lambda q: [GOV_URL, BLOG_URL, "junk"])
    assert source_discovery.is_available() is True
    prov = source_discovery.default_search_provider()
    assert prov is not None
    # Candidates are proposed as-is (verification/grounding happens downstream);
    # only non-URL junk is dropped here.
    assert prov("anything") == [GOV_URL, BLOG_URL]
    route = normalize_route(_ri())
    assert source_discovery.discover_candidates_for_route(route) == [GOV_URL, BLOG_URL]


def test_search_uses_default_provider_when_available():
    source_discovery.set_proposer(lambda q: [GOV_URL])
    assert fetching.search("q") == [GOV_URL]


# ------------------------------------------------------------ render fallback --
def test_fetch_uses_render_fallback_when_plain_fetch_fails(monkeypatch):
    def failing_default(url, timeout_seconds=20.0):
        return FetchResult(requested_url=url, ok=False, error="http 403")
    monkeypatch.setattr(fetching, "_default_fetch", failing_default)
    rendered = FetchResult(requested_url=GOV_ROOT, ok=True, final_url=GOV_ROOT,
                           final_hostname="www.evisa.gov.kh", http_status=200,
                           content_text="Rendered eVisa content", content_hash="r" * 64,
                           page_language="en", retrieved_at="2026-07-23T12:00:00Z")
    fetching.set_render_fetcher(lambda url, timeout_seconds=20.0: rendered)
    out = fetching.fetch(GOV_ROOT)
    assert out.ok and out.content_text == "Rendered eVisa content"


def test_no_render_fallback_keeps_honest_failure(monkeypatch):
    def failing_default(url, timeout_seconds=20.0):
        return FetchResult(requested_url=url, ok=False, error="http 500")
    monkeypatch.setattr(fetching, "_default_fetch", failing_default)
    monkeypatch.setattr(fetching, "_render_fetcher", lambda: None)
    out = fetching.fetch(GOV_ROOT)
    assert out.ok is False and out.error == "http 500"


# ------------------------------------------------- full job via discovery path --
def _fake_fetch_ok(url, timeout_seconds=20):
    host = url.split("/")[2]
    return FetchResult(requested_url=url, ok=True, final_url=url, redirect_chain=[],
                       final_hostname=host, http_status=200,
                       content_text="Cambodia e-Visa for tourism. Fee USD 30. "
                                    "Stay 30 days. Singapore citizens eligible.",
                       content_hash="c" * 64, page_language="en",
                       retrieved_at="2026-07-23T12:00:00Z")


def _fake_extractor(system, user):
    return {"fields": {
        "visa_requirement": {"value": "EVISA_REQUIRED", "sources": ["s1"]},
        "maximum_stay": {"value": "30 days", "sources": ["s1"]},
        "government_fee_amount": {"value": 30, "sources": ["s1"]},
        "government_fee_currency": {"value": "USD", "sources": ["s1"]},
        "official_application_portal": {"value": GOV_ROOT, "sources": ["s1"]},
    }, "conflicts": [], "summary": "eVisa required."}


def _ri_dest(dest):
    return RouteInput(passport_nationality="SGP", passport_issuing_country="SG",
                      travel_document_type="ordinary_passport",
                      lawful_country_of_residence="SG", destination_country=dest,
                      visa_category="evisa_tourist", policy_period="2026-10-01")


def test_on_demand_job_completes_via_route_aware_discovery(db):
    """The failing case shape: NO set_search_provider, only a Kimi proposer.
    Discovery must find the official source via the route-aware call and the job
    must complete with a verified, cached route."""
    ri = _ri_dest(DEST_OK)
    answers = dict(ROUTE_ANSWERS, destination_country=DEST_OK)
    source_discovery.set_proposer(lambda q: [GOV_URL, BLOG_URL])
    fetching.set_fetcher(_fake_fetch_ok)
    kimi_research.set_extractor(_fake_extractor)
    job = ondemand.create_job(db, org_id="org-sd", user_id="u1",
                              answers=answers, normalized=normalize_route(ri),
                              key=route_key(ri))
    job = ondemand.run_job(db, job.id)
    assert job.status == "complete", (job.status, job.error, job.progress[-4:])
    assert job.result["resolution"]["readiness_status"] != "NOT_READY"
    head = db.execute(select(VisaRoute).where(
        VisaRoute.route_key == job.route_key)).scalars().one()
    assert head.research_status == "verified"
    assert head.disposition == "EVISA_REQUIRED"
    # Route-aware discovery ran exactly one query, not the 10-query loop.
    assert job.counters.get("queries_used") == 1
    notes = " ".join(n.get("note", "") for n in job.progress)
    assert "route-aware discovery proposed" in notes


def test_research_auto_triggers_adapter_build_when_authorized(db):
    """After research verifies a portal-requiring route, the backend AUTOMATICALLY
    starts the adapter build when the applicant's standing authorization covers
    portal selection — removing the routine 'click Build connector' break."""
    from tests.test_e2e import _new_case
    from app.adapter_factory import models as afm
    app_id = _new_case(db)  # creates a standing authorization covering portal selection
    ri = _ri_dest("BN")     # Brunei — un-researched, distinct destination
    answers = dict(ROUTE_ANSWERS, destination_country="BN")
    source_discovery.set_proposer(lambda q: [GOV_URL])
    fetching.set_fetcher(_fake_fetch_ok)
    kimi_research.set_extractor(_fake_extractor)   # EVISA_REQUIRED + verified gov portal
    job = ondemand.create_job(db, org_id="org1", user_id="user1", case_id=app_id,
                              answers=answers, normalized=normalize_route(ri),
                              key=route_key(ri))
    job = ondemand.run_job(db, job.id)
    assert job.status == "complete", (job.status, job.error)
    ar = job.result["adapter_readiness"]
    assert ar["adapter_ready"] is True, ar
    assert ar["auto_build_started"] is True, ar
    # A consented build request exists for this route, tied to the authorization.
    req = db.execute(select(afm.AdapterBuildRequest).where(
        afm.AdapterBuildRequest.route_key == job.route_key)).scalars().first()
    assert req is not None and req.consent_given is True
    assert req.standing_authorization_id


def test_research_no_autobuild_without_standing_authorization(db):
    """Adapter-ready but no standing authorization -> NO build is auto-started
    (honest; the applicant must authorize first)."""
    ri = _ri_dest("BH")     # Bahrain — un-researched, distinct destination
    answers = dict(ROUTE_ANSWERS, destination_country="BH")
    source_discovery.set_proposer(lambda q: [GOV_URL])
    fetching.set_fetcher(_fake_fetch_ok)
    kimi_research.set_extractor(_fake_extractor)
    job = ondemand.create_job(db, org_id="org1", user_id="user1", case_id="no-such-app",
                              answers=answers, normalized=normalize_route(ri),
                              key=route_key(ri))
    job = ondemand.run_job(db, job.id)
    ar = job.result["adapter_readiness"]
    assert ar["adapter_ready"] is True
    assert ar["auto_build_started"] is False


def test_discovery_finds_nothing_stays_honest(db):
    """A proposer that returns only a non-government blog: no verified evidence,
    job ends research_incomplete honestly — never fabricated. Uses a distinct,
    un-cached destination so no stored policy short-circuits the research."""
    ri = _ri_dest(DEST_NONE)
    answers = dict(ROUTE_ANSWERS, destination_country=DEST_NONE)
    source_discovery.set_proposer(lambda q: [BLOG_URL])
    fetching.set_fetcher(_fake_fetch_ok)
    kimi_research.set_extractor(_fake_extractor)
    job = ondemand.create_job(db, org_id="org-sd2", user_id="u1",
                              answers=answers, normalized=normalize_route(ri),
                              key=route_key(ri))
    job = ondemand.run_job(db, job.id)
    # Blog is fetched but is not a government host, so extraction is skipped and
    # no verified disposition is produced.
    assert job.status == "research_incomplete"
    assert job.counters.get("disposition") in (None, "")
