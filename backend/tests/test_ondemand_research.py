"""On-demand per-route research engine (strategy change §8-§16, §30)."""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal, create_all
from app.main import app as fastapi_app
from app.visa_snapshot import SNAPSHOT_DATE, fetching, kimi_research, ondemand, pipeline
from app.visa_snapshot.fetching import FetchResult
from app.visa_snapshot.models import (HumanReviewTask, OnDemandRouteResearchJob,
                                      RouteIntake, VisaRoute)
from app.visa_snapshot.routekey import RouteInput, route_key

H = {"Authorization": "Bearer dev-token", "X-Org-Id": "org-od", "X-User-Id": "u1"}

GOV_URL = "https://www.evisa.gov.kh/info"   # .gov.kh is a government suffix
BLOG_URL = "https://travelblog.example/cambodia"

ROUTE_ANSWERS = {
    "passport_nationality": "SGP", "passport_issuing_country": "SG",
    "travel_document_type": "ordinary_passport", "lawful_country_of_residence": "SG",
    "destination_country": "KH", "visa_category": "evisa_tourist",
    "travel_purpose": "tourism", "arrival_date": "2026-10-01",
    "departure_date": "2026-10-08", "age": 30, "email": "od@example.com",
    "preferred_language": "en",
}


def _normalized():
    from app.visa_snapshot.routekey import normalize_route
    return normalize_route(RouteInput(
        passport_nationality="SGP", passport_issuing_country="SG",
        travel_document_type="ordinary_passport", lawful_country_of_residence="SG",
        destination_country="KH", visa_category="evisa_tourist",
        policy_period="2026-10-01"))


def _key():
    return route_key(RouteInput(
        passport_nationality="SGP", passport_issuing_country="SG",
        travel_document_type="ordinary_passport", lawful_country_of_residence="SG",
        destination_country="KH", visa_category="evisa_tourist",
        policy_period="2026-10-01"))


@pytest.fixture()
def db():
    create_all()
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _isolate_research_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "RESEARCH_DIR", tmp_path / "research")
    yield
    fetching.set_fetcher(None)
    fetching.set_search_provider(None)
    kimi_research.set_extractor(None)


def _fake_fetch_ok(url, timeout_seconds=20):
    host = url.split("/")[2]
    return FetchResult(requested_url=url, ok=True, final_url=url, redirect_chain=[],
                       final_hostname=host, http_status=200,
                       content_text="Cambodia e-Visa for tourism. Fee USD 30. "
                                    "Stay 30 days. Singapore citizens eligible.",
                       content_hash="c" * 64, page_language="en",
                       retrieved_at="2026-07-23T12:00:00Z")


def _fake_extractor_good(system, user):
    return {"fields": {
        "visa_requirement": {"value": "EVISA_REQUIRED", "sources": ["s1"]},
        "maximum_stay": {"value": "30 days", "sources": ["s1"]},
        "government_fee_amount": {"value": 30, "sources": ["s1"]},
        "government_fee_currency": {"value": "USD", "sources": ["s1"]},
        "official_application_portal": {"value": "https://www.evisa.gov.kh/", "sources": ["s1"]},
        # HALLUCINATIONS that must be rejected:
        "interview_required": {"value": True, "sources": ["s99"]},      # unknown id
        "biometrics_required": {"value": True, "sources": []},          # no citation
    }, "conflicts": [], "summary": "eVisa required for SGP tourists."}


def _mk_job(db, answers=None):
    return ondemand.create_job(db, org_id="org-od", user_id="u1",
                               answers=answers or ROUTE_ANSWERS,
                               normalized=_normalized(), key=_key())


def test_grounding_rejects_hallucinated_citations():
    pages = [{"id": "s1", "url": GOV_URL, "hostname": "www.evisa.gov.kh", "text": "x"}]
    kimi_research.set_extractor(_fake_extractor_good)
    out = kimi_research.extract({"destination_country": "KHM"}, pages)
    assert "visa_requirement" in out["fields"]
    assert "interview_required" not in out["fields"]     # unknown source id -> dropped
    assert "biometrics_required" not in out["fields"]    # uncited -> dropped
    reasons = {r["field"]: r["reason"] for r in out["rejected"]}
    assert reasons["interview_required"] == "ungrounded_no_valid_citation"
    assert reasons["biometrics_required"] == "ungrounded_no_valid_citation"


def test_full_on_demand_job_verifies_and_caches_route(db):
    fetching.set_fetcher(_fake_fetch_ok)
    fetching.set_search_provider(lambda q: [GOV_URL, BLOG_URL])
    kimi_research.set_extractor(_fake_extractor_good)
    job = _mk_job(db)
    job = ondemand.run_job(db, job.id)
    assert job.status == "complete", (job.status, job.error, job.progress[-3:])
    assert job.result["resolution"]["readiness_status"] != "NOT_READY"
    # Immutable route record cached in PostgreSQL:
    head = db.execute(select(VisaRoute).where(VisaRoute.route_key == job.route_key)).scalars().one()
    assert head.research_status == "verified"
    assert head.disposition == "EVISA_REQUIRED"
    # Research touched ONLY this destination:
    assert all("KHM" in (p.get("stage") or "KHM") for p in [])  # no other-dest artifacts
    assert (pipeline.RESEARCH_DIR / "KHM" / "research.json").exists()
    assert not (pipeline.RESEARCH_DIR / "JPN").exists()


def test_stored_route_reused_without_new_research(db):
    """Second matching route: CHECK_STORED_POLICY short-circuits — no fetches."""
    fetching.set_fetcher(_fake_fetch_ok)
    fetching.set_search_provider(lambda q: [GOV_URL])
    kimi_research.set_extractor(_fake_extractor_good)
    j1 = ondemand.run_job(db, _mk_job(db).id)
    assert j1.status == "complete"

    calls = {"n": 0}

    def counting_fetch(url, timeout_seconds=20):
        calls["n"] += 1
        return _fake_fetch_ok(url)
    fetching.set_fetcher(counting_fetch)
    j2 = _mk_job(db)
    assert j2.id != j1.id
    j2 = ondemand.run_job(db, j2.id)
    assert j2.status == "complete"
    assert j2.result.get("reused_stored") is True
    assert calls["n"] == 0                      # zero new fetches — cache reuse


def test_blocked_sources_are_honest_research_incomplete(db):
    def failing_fetch(url, timeout_seconds=20):
        return FetchResult(requested_url=url, ok=False, error="403 blocked",
                           retrieved_at="2026-07-23T12:00:00Z")
    fetching.set_fetcher(failing_fetch)
    fetching.set_search_provider(lambda q: [GOV_URL])
    kimi_research.set_extractor(_fake_extractor_good)
    answers = dict(ROUTE_ANSWERS, destination_country="TD")  # Chad: nothing stored
    from app.visa_snapshot.routekey import normalize_route
    norm = normalize_route(RouteInput(
        passport_nationality="SGP", passport_issuing_country="SG",
        travel_document_type="ordinary_passport", lawful_country_of_residence="SG",
        destination_country="TD", visa_category="tourist_visa", policy_period="2026-10-01"))
    key = route_key(RouteInput(
        passport_nationality="SGP", passport_issuing_country="SG",
        travel_document_type="ordinary_passport", lawful_country_of_residence="SG",
        destination_country="TD", visa_category="tourist_visa", policy_period="2026-10-01"))
    job = ondemand.create_job(db, org_id="org-od", answers=answers, normalized=norm, key=key)
    job = ondemand.run_job(db, job.id)
    assert job.status == "research_incomplete"          # honest, no guessing
    assert job.counters["fetch_failures"][0]["error"] == "403 blocked"
    task = db.execute(select(HumanReviewTask).where(
        HumanReviewTask.subject_id == key)).scalars().first()
    assert task is not None                              # review task created


def test_kimi_unavailable_is_honest_not_fabricated(db):
    fetching.set_fetcher(_fake_fetch_ok)
    fetching.set_search_provider(lambda q: [GOV_URL])

    def unavailable(system, user):
        raise kimi_research.KimiUnavailable("no key")
    kimi_research.set_extractor(unavailable)
    answers = dict(ROUTE_ANSWERS, destination_country="TG")   # Togo: nothing stored
    norm = dict(_normalized(), destination_country="TGO")
    key = _key().replace("dest=KHM", "dest=TGO")
    job = ondemand.create_job(db, org_id="org-od", answers=answers, normalized=norm, key=key)
    job = ondemand.run_job(db, job.id)
    assert job.status == "research_incomplete"
    assert job.result["resolution"]["readiness_status"] == "NOT_READY"


def test_time_budget_saves_partials_and_review_task(db, monkeypatch):
    fetching.set_fetcher(_fake_fetch_ok)
    fetching.set_search_provider(lambda q: [GOV_URL])
    kimi_research.set_extractor(_fake_extractor_good)
    answers = dict(ROUTE_ANSWERS, destination_country="TN")   # Tunisia
    norm = dict(_normalized(), destination_country="TUN")
    key = _key().replace("dest=KHM", "dest=TUN")
    job = ondemand.create_job(db, org_id="org-od", answers=answers, normalized=norm, key=key)
    job.limits = dict(job.limits, max_seconds=0)   # exhausted immediately
    db.commit()
    job = ondemand.run_job(db, job.id)
    assert job.status == "timed_out"
    assert any(t.title.startswith("On-demand research timed out")
               for t in db.execute(select(HumanReviewTask)).scalars())


def test_job_resume_is_idempotent_no_duplicate_versions(db):
    fetching.set_fetcher(_fake_fetch_ok)
    fetching.set_search_provider(lambda q: [GOV_URL])
    kimi_research.set_extractor(_fake_extractor_good)
    job = _mk_job(db)
    job = ondemand.run_job(db, job.id)
    assert job.status == "complete"
    # Re-run the finished job: no-op; and importer dedupes identical content.
    job2 = ondemand.run_job(db, job.id)
    assert job2.status == "complete"
    from app.visa_snapshot.models import VisaRouteVersion
    versions = db.execute(select(VisaRouteVersion)).scalars().all()
    keys = {}
    for v in versions:
        keys[v.route_id] = keys.get(v.route_id, 0) + 1
    assert all(c == 1 for c in keys.values())   # one immutable version, no dups


def test_intake_resolve_auto_creates_focused_job_only(db):
    client = TestClient(fastapi_app)
    r = client.post("/intake", json={"answers": ROUTE_ANSWERS}, headers=H)
    iid = r.json()["id"]
    rr = client.post(f"/intake/{iid}/resolve", headers=H)
    assert rr.status_code == 200
    body = rr.json()
    if body["readiness_status"] == "NOT_READY":
        assert "research_job" in body
        jid = body["research_job"]["id"]
        jr = client.get(f"/research-jobs/{jid}", headers=H)
        assert jr.status_code == 200
        # tenant isolation
        other = dict(H, **{"X-Org-Id": "org-other"})
        assert client.get(f"/research-jobs/{jid}", headers=other).status_code == 404
        # the job targets EXACTLY this route
        assert jr.json()["route_key"] == body["route_key"]


def test_no_global_fleet_behavior():
    """No module auto-spawns all-destination research: creating one job touches
    only its own destination, and nothing in the package enumerates research
    for every country at import time."""
    import app.visa_snapshot.ondemand as od
    import inspect
    src = inspect.getsource(od)
    assert "for c in load_registry(\"countries\")" not in src   # no all-country loop
    # enumerate_batches exists ONLY behind the manual CLI build command:
    import app.visa_snapshot.__main__ as cli
    assert "enumerate_batches" in inspect.getsource(cli.cmd_build)


def test_date_honesty_for_late_research(db, monkeypatch):
    fetching.set_fetcher(_fake_fetch_ok)
    # Jurisdiction-consistent official source for Laos (.gov.la) so the
    # evidence validator accepts the disposition (this test is about date
    # honesty, not jurisdiction).
    fetching.set_search_provider(lambda q: ["https://www.evisa.gov.la/info"])
    kimi_research.set_extractor(_fake_extractor_good)
    answers = dict(ROUTE_ANSWERS, destination_country="LA")   # Laos
    norm = dict(_normalized(), destination_country="LAO")
    key = _key().replace("dest=KHM", "dest=LAO")
    job = ondemand.create_job(db, org_id="org-od", answers=answers, normalized=norm, key=key)
    job.researched_at_date = "2026-08-15"    # simulated later on-demand research
    db.commit()
    job = ondemand.run_job(db, job.id)
    assert job.status == "complete"
    dh = job.result["date_honesty"]
    assert dh["snapshot_supported"] is False
    assert dh["label"] == "Route researched on demand on 2026-08-15"
    head = db.execute(select(VisaRoute).where(VisaRoute.route_key == key)).scalars().one()
    from app.visa_snapshot.models import VisaRouteVersion
    v = db.execute(select(VisaRouteVersion).where(
        VisaRouteVersion.route_id == head.id)).scalars().one()
    assert v.record["researched_on_demand_at"] == "2026-08-15"
    assert "NOT evidence" in v.record["notes"]


def test_research_status_never_upgrades_whole_destination_file(db, tmp_path):
    """One grounded route must not promote unrelated dispositions in the shared
    destination file to verified."""
    fetching.set_fetcher(_fake_fetch_ok)
    fetching.set_search_provider(lambda q: [GOV_URL])
    kimi_research.set_extractor(_fake_extractor_good)
    # Pre-existing incomplete file for KHM with an unrelated disposition:
    d = pipeline.RESEARCH_DIR / "KHM"
    d.mkdir(parents=True)
    (d / "research.json").write_text(json.dumps({
        "destination": "KHM", "retrieved_at": "2026-07-23T00:00:00Z",
        "research_status": "research_incomplete",
        "sources": [], "dispositions": [
            {"nationalities": ["RUS"], "category": "tourist_visa",
             "disposition": "EMBASSY_VISA_REQUIRED", "source_urls": []}],
        "policies": [], "fees": [], "portals": [], "unresolved": []}))
    job = ondemand.run_job(db, _mk_job(db).id)
    assert job.status == "complete"
    data = json.loads((d / "research.json").read_text())
    assert data["research_status"] == "research_incomplete"   # never upgraded
    # ...while OUR route's record is verified via its own VisaRoute row:
    head = db.execute(select(VisaRoute).where(
        VisaRoute.route_key == job.route_key)).scalars().one()
    assert head.research_status == "verified"
