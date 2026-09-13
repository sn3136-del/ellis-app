"""Explicit Add/Refresh finds missing citations without inventing proof."""
from copy import deepcopy
import json
import threading
import time
from urllib.parse import urlsplit

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base
from app.visa_snapshot import fetching, freshness, kimi_primary, source_discovery, verified_overrides
from app.visa_snapshot.fetching import FetchResult
from app.visa_snapshot.models import KimiRouteGuidanceCache

ROUTE = {"passport_nationality": "GBR", "passport_issuing_country": "GBR",
         "lawful_country_of_residence": "GBR", "destination_country": "IRL",
         "travel_document_type": "ordinary_passport", "travel_purpose": "tourism",
         "visa_category": "tourist_visa"}
URL = "https://www.irishimmigration.ie/at-the-border/common-travel-area/"
QUOTE = "British citizens do not need a visa to visit Ireland for tourism."
GUIDANCE = {"disposition": "VISA_EXEMPT", "application_channel": "not_required",
            "confidence": "high", "source_url": None,
            "government_fee": {"amount": None, "currency": None}}


@pytest.fixture
def state(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    monkeypatch.setattr(verified_overrides, "find", lambda _: None)
    monkeypatch.setattr(verified_overrides, "apply", lambda g, _: (deepcopy(g), {}))
    monkeypatch.setattr(source_discovery, "official_source_seeds", lambda _: [])
    monkeypatch.setattr(kimi_primary, "provider_suspension", lambda: None)
    row = KimiRouteGuidanceCache(cache_key=kimi_primary.cache_key(ROUTE), route=deepcopy(ROUTE),
        guidance=deepcopy(GUIDANCE), status="KIMI_PRIMARY")
    db.add(row)
    db.commit()
    yield row, db
    source_discovery.set_proposer(None)
    freshness.set_provider(None)
    fetching.set_fetcher(None)
    db.close()
    engine.dispose()


def readings(*, text=QUOTE, response=None):
    fetched = []
    def fetch(url, **_):
        fetched.append(url)
        return FetchResult(requested_url=url, final_url=url, final_hostname=urlsplit(url).hostname,
            ok=True, http_status=200, content_text=text, content_hash="test-capture",
            retrieved_at="2026-09-13T00:00:00Z")
    fetching.set_fetcher(fetch)
    freshness.set_provider(lambda *_: deepcopy(response if response is not None else {
        "page_relevant": True, "page_is_nationality_specific": True, "consistent": True,
        "corrected_fields": {}, "evidence": {"disposition": QUOTE}}))
    return fetched


def test_periodic_or_reader_default_never_discovers(state):
    row, db = state
    source_discovery.set_proposer(lambda _: pytest.fail("implicit source discovery spends money"))
    result = freshness.recheck_route(db, ROUTE)
    assert result["outcome"] == "no_official_source"
    assert result["model_comparisons"] == 0
    assert row.guidance == GUIDANCE and row.fresh_until is None


def test_explicit_discovery_adopts_only_fetched_route_supported_citation(state):
    row, db = state
    queries = []
    source_discovery.set_proposer(lambda q: queries.append(q) or [URL, URL, "https://blog.example/ireland"])
    fetched = readings()
    result = freshness.recheck_route(db, ROUTE, discover_sources=True)
    assert len(queries) == 1 and fetched == [URL]
    assert "travel_document_type=ordinary_passport" in queries[0]
    assert "travel_purpose=tourism" in queries[0] and "passport_nationality=GBR" in queries[0]
    assert result["model_discovery_calls"] == 1 and result["model_comparisons"] == 1
    assert result["source_discovery"]["rejected_candidate_count"] == 1
    assert result["renewed"] is True and result["verified_fields"] == ["application_channel", "disposition"]
    assert result["changed"] == ["source_url"]
    assert row.guidance == dict(GUIDANCE, source_url=URL)
    assert row.verification["grounded_check"]["field_sources"]["disposition"]["source_url"] == URL
    assert row.fresh_until is not None


def test_existing_citation_avoids_a_discovery_call_even_on_explicit_request(state):
    row, db = state
    row.guidance = dict(row.guidance, source_url=URL)
    db.commit()
    source_discovery.set_proposer(lambda _: pytest.fail("existing evidence must be read first"))
    readings()
    result = freshness.recheck_route(db, ROUTE, discover_sources=True)
    assert result["outcome"] == "checked" and "model_discovery_calls" not in result


def test_curated_hint_is_untrusted_but_does_not_need_paid_discovery(state, monkeypatch):
    row, db = state
    monkeypatch.setattr(source_discovery, "official_source_seeds", lambda _: [URL])
    source_discovery.set_proposer(lambda _: pytest.fail("seed exists"))
    readings()
    result = freshness.recheck_route(db, ROUTE, discover_sources=True)
    assert result["model_discovery_calls"] == 0 and result["renewed"]
    assert result["source_discovery"]["outcome"] == "seed_candidates"


@pytest.mark.parametrize("case", ["irrelevant", "wrong_nationality", "wrong_destination", "invalid_json", "blocked"])
def test_proposal_or_irrelevant_fetch_never_fabricates_a_citation(state, case):
    row, db = state
    url = "https://www.mofa.go.jp/visa/" if case == "wrong_destination" else URL
    source_discovery.set_proposer(lambda _: [url])
    response = {"page_relevant": True, "page_is_nationality_specific": True,
        "consistent": True, "corrected_fields": {}, "evidence": {"disposition": QUOTE}}
    text = QUOTE
    if case == "irrelevant":
        response["page_relevant"] = False
    elif case == "invalid_json":
        response = {"note": "no typed verdict"}
    elif case == "wrong_nationality":
        text = "Canadian citizens do not need a visa to visit Ireland for tourism."
        response["evidence"]["disposition"] = text
    readings(text=text, response=response)
    if case == "blocked":
        fetching.set_fetcher(lambda u, **_: FetchResult(requested_url=u, final_url=u,
            ok=False, error="blocked"))
    result = freshness.recheck_route(db, ROUTE, discover_sources=True)
    assert not result.get("renewed")
    assert row.guidance == GUIDANCE and row.fresh_until is None
    assert "disposition" not in row.verification["grounded_check"].get("verified_fields", [])


def test_citation_adoption_does_not_turn_an_unexplained_disagreement_into_renewal(state):
    row, db = state
    source_discovery.set_proposer(lambda _: [URL])
    readings(response={"page_relevant": True, "page_is_nationality_specific": True,
        "consistent": False, "corrected_fields": {}, "evidence": {"disposition": QUOTE}})
    result = freshness.recheck_route(db, ROUTE, discover_sources=True)
    assert result["changed"] == ["source_url"] and not result["renewed"]
    assert row.fresh_until is None


@pytest.mark.parametrize("field,value", [("government_fee", {"amount": 0, "currency": "EUR"}),
    ("insurance_required", False), ("arrival_card", {"required": False}),
    ("required_documents", ["passport"])])
def test_missing_detail_proof_still_blocks_renewal_including_zero_and_false(state, field, value):
    row, db = state
    row.guidance = dict(row.guidance, **{field: value})
    db.commit()
    source_discovery.set_proposer(lambda _: [URL])
    readings()
    result = freshness.recheck_route(db, ROUTE, discover_sources=True)
    assert not result["renewed"] and field in result["unverified_fields"]
    assert row.guidance[field] == value and row.fresh_until is None


def test_actual_missing_fields_still_block_renewal(state):
    row, db = state
    row.missing_fields = ["permitted_stay"]
    db.commit()
    source_discovery.set_proposer(lambda _: [URL])
    readings()
    result = freshness.recheck_route(db, ROUTE, discover_sources=True)
    assert not result["renewed"] and row.missing_fields == ["permitted_stay"]


def test_explicit_context_survives_nested_pending_recovery_but_always_resets(state, monkeypatch):
    _, db = state
    seen = []
    def recheck(*_):
        seen.append(freshness._DISCOVER_SOURCES.get())
        raise RuntimeError("simulated recovery failure")
    monkeypatch.setattr(freshness, "recheck_row", recheck)
    with pytest.raises(RuntimeError):
        freshness.recheck_route(db, ROUTE, discover_sources=True)
    assert seen == [True] and freshness._DISCOVER_SOURCES.get() is False
    with pytest.raises(RuntimeError):
        freshness.recheck_route(db, ROUTE)
    assert seen == [True, False]


def test_explicit_permission_reaches_actual_pending_recovery_dispatch(state, monkeypatch):
    from app.visa_snapshot import detail_jobs
    row, db = state
    row.verification = {"detail_pending": True}
    db.commit()
    seen = []
    def recover(*_a, **_k):
        seen.append(freshness._DISCOVER_SOURCES.get())
        return {"outcome": "detail_pending", "renewed": False}
    monkeypatch.setattr(detail_jobs, "recover_row", recover)
    freshness.recheck_route(db, ROUTE, discover_sources=True)
    freshness.recheck_route(db, ROUTE)
    assert seen == [True, False] and not freshness._DISCOVER_SOURCES.get()


def test_request_deadline_includes_discovery_time(state, monkeypatch):
    row, db = state
    token = freshness._DISCOVER_SOURCES.set(True)
    try:
        def discover(*_, **__):
            time.sleep(.04)
            return {"urls": [URL], "outcome": "candidates", "model_discovery_calls": 1}
        monkeypatch.setattr(source_discovery, "bounded_route_candidates", discover)
        fetching.set_fetcher(lambda *_a, **_k: pytest.fail("route budget already consumed"))
        result = freshness.recheck_row(db, row, budget_seconds=.02)
        assert not result.get("renewed") and result["model_discovery_calls"] == 1
        assert row.guidance == GUIDANCE
    finally:
        freshness._DISCOVER_SOURCES.reset(token)


@pytest.mark.parametrize("value,expected", [(None, False), ({"amount": None, "currency": None}, False),
    ({"nested": [{"value": " "}, None]}, False), (False, True), (0, True),
    ({"amount": 0}, True), ({"required": False}, True), ({"currency": "USD"}, True)])
def test_asserted_value_keeps_negative_and_zero_claims(value, expected):
    assert freshness._has_asserted_value(value) is expected


def test_bounded_proposer_includes_document_purpose_and_caps_candidates(state):
    queries = []
    source_discovery.set_proposer(lambda q: queries.append(q) or {"urls": [
        "https://[", "https://user:secret@www.gov.ie/a", URL, URL,
        *[f"https://www.gov.ie/{i}" for i in range(7)]]})
    out = source_discovery.bounded_route_candidates(dict(ROUTE, travel_document_type="refugee_travel_document",
        travel_purpose="business"), timeout_seconds=1)
    assert out["model_discovery_calls"] == 1 and len(out["urls"]) == 4
    assert all("secret" not in u and u != "https://[" for u in out["urls"])
    assert "travel_document_type=refugee_travel_document" in queries[0]
    assert "travel_purpose=business" in queries[0]


def test_discovery_timeout_is_bounded_without_late_policy_writes(state, monkeypatch):
    release, entered = threading.Event(), threading.Event()
    monkeypatch.setattr(source_discovery, "_BOUNDED_DISCOVERY_SLOTS", threading.BoundedSemaphore(1))
    def propose(_):
        entered.set()
        release.wait(2)
        return [URL]
    source_discovery.set_proposer(propose)
    try:
        started = time.monotonic()
        result = source_discovery.bounded_route_candidates(ROUTE, timeout_seconds=.03)
        assert entered.is_set() and time.monotonic() - started < .5
        assert result == {"urls": [], "outcome": "timeout", "model_discovery_calls": 1}
        blocked = source_discovery.bounded_route_candidates(ROUTE, timeout_seconds=.02)
        assert blocked["outcome"] == "timeout" and blocked["model_discovery_calls"] == 0
    finally:
        release.set()


@pytest.mark.parametrize("raw", [None, {}, {"urls": "https://gov.ie"}, {"urls": [False]}])
def test_invalid_proposal_has_honest_non_candidate_outcome(state, raw):
    source_discovery.set_proposer(lambda _: raw)
    result = source_discovery.bounded_route_candidates(ROUTE, timeout_seconds=1)
    assert result["outcome"] == "invalid_response" and result["urls"] == []
    assert result["model_discovery_calls"] == 1


def test_unavailable_suspended_and_no_budget_do_not_call_provider(state, monkeypatch):
    source_discovery.set_proposer(lambda _: pytest.fail("provider not permitted"))
    assert source_discovery.bounded_route_candidates(ROUTE, timeout_seconds=0)["model_discovery_calls"] == 0
    monkeypatch.setattr(kimi_primary, "provider_suspension", lambda: {"reason": "balance"})
    assert source_discovery.bounded_route_candidates(ROUTE, timeout_seconds=1)["model_discovery_calls"] == 0
    source_discovery.set_proposer(None)
    monkeypatch.setattr(kimi_primary, "provider_suspension", lambda: None)
    assert source_discovery.bounded_route_candidates(ROUTE, timeout_seconds=1)["model_discovery_calls"] == 0


def test_proposal_uses_bounded_existing_k3_transport_and_safe_error(state, monkeypatch):
    monkeypatch.setattr(source_discovery, "is_available", lambda: True)
    calls = []
    def live(system, query, **kwargs):
        calls.append(kwargs)
        return {"urls": [URL]}
    monkeypatch.setattr(kimi_primary, "_live_call", live)
    result = source_discovery.bounded_route_candidates(ROUTE, timeout_seconds=100)
    assert result["urls"] == [URL]
    assert calls[0]["source_comparison"] is True and calls[0]["max_tokens"] == 1400
    assert 0 < calls[0]["timeout"] <= 20
    monkeypatch.setattr(kimi_primary, "_live_call", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("secret-key")))
    result = source_discovery.bounded_route_candidates(ROUTE, timeout_seconds=1)
    assert result["outcome"] == "provider_error" and "secret-key" not in json.dumps(result)
