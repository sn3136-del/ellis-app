"""Explicit manual publication serves saved facts without rewriting their evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.sql.dml import Update

from app.db import SessionLocal
from app.models import AuditEvent
from app.visa_snapshot import kimi_primary as kp, freshness, row_projection
from app.visa_snapshot.models import KimiRouteGuidanceCache, DatabaseIssueReport
from .test_operator_release_cas import release_session, HEADERS, BODY, ROUTE

LOOKUP_BODY = {"nationality": BODY["nationality"], "destination": BODY["destination"]}


@pytest.fixture
def publication_session(release_session, monkeypatch):
    session, row_id = release_session
    monkeypatch.setenv("ELLIS_DATABASE_HOLD_LOW_CONFIDENCE", "1")
    monkeypatch.setenv("ELLIS_HOLD_UNCERTAIN", "1")
    monkeypatch.setattr(kp, "is_available", lambda: True)
    monkeypatch.setattr(kp, "_call", lambda *a, **k: pytest.fail("Publication must not call a model"))
    row = session.get(KimiRouteGuidanceCache, row_id)
    row.fresh_until = datetime.now(timezone.utc) + timedelta(days=1)
    session.commit()
    yield session, row_id
    session.rollback()
    session.query(DatabaseIssueReport).filter_by(org_id="publication-regression").delete()
    session.commit()


def releases(session):
    return session.query(AuditEvent).filter_by(action="database_answer_released",
                                              org_id=HEADERS["X-Org-Id"]).count()


def add_dispute(session, key, *, status="open"):
    issue = DatabaseIssueReport(cache_key=key, route=ROUTE,
        org_id="publication-regression", reported_by="freshness_monitor", status=status,
        field="government_fee", note="Official fee conflicts with stored answer",
        proposal={"fields": {"government_fee": {"value": {"amount": 25, "currency": "USD"}}}})
    session.add(issue)
    session.commit()
    return issue.id


def current_metadata(row_id):
    with SessionLocal() as reader:
        return deepcopy(reader.get(KimiRouteGuidanceCache, row_id).verification)


def assert_blocked_without_release(response, session, row_id, before, audit_count):
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "publication_blocked"
    assert detail["published"] is False and detail["held"] is True
    assert detail["cache_key"] == kp.cache_key(ROUTE)
    assert detail["message"] and detail["reasons"]
    assert current_metadata(row_id) == before
    assert releases(session) == audit_count
    return detail


def assert_manual_publication(response, session, row_id, before, audit_count):
    assert response.status_code == 200, response.text
    assert response.json()["published"] is True and response.json()["held"] is False
    assert response.json()["publication_state"] == "published"
    actual = current_metadata(row_id)
    release = actual.pop("operator_released")
    assert release["mode"] == "manual_publication" and release["by"] == "operator"
    assert actual == {k: v for k, v in before.items() if k != "operator_released"}
    assert releases(session) == audit_count + 1


def assert_reader_and_records_published(client):
    lookup = client.post("/database/lookup", headers=HEADERS, json=LOOKUP_BODY).json()
    assert lookup.get("held") is False and lookup["guidance"], lookup
    records = client.get("/database/records", headers=HEADERS,
        params={"nationality": "ISL", "destination": "NRU"}).json()["records"]
    assert records and all(r["publication_state"] == "published" and not r["held"] for r in records)
    return lookup, records


@pytest.mark.parametrize("already_released", [False, True])
@pytest.mark.parametrize("status", ["open", "acknowledged"])
def test_manual_publication_preserves_open_findings_and_publishes_saved_answer(
        client, publication_session, already_released, status):
    session, row_id = publication_session
    row = session.get(KimiRouteGuidanceCache, row_id)
    if already_released:
        row.verification = dict(row.verification, operator_released={"by": "earlier-reviewer", "at": "earlier"})
        session.commit()
    issue_id = add_dispute(session, row.cache_key, status=status)
    before, count = current_metadata(row_id), releases(session)
    saved = deepcopy(row.guidance)
    prior_records = row_projection.records_projection(session, row, ROUTE)
    response = client.post("/database/approve", headers=HEADERS, json=BODY)
    assert_manual_publication(response, session, row_id, before, count)
    lookup, records = assert_reader_and_records_published(client)
    assert lookup["guidance"]["disposition"] == saved["disposition"]
    session.refresh(row)
    assert row.guidance == saved and session.get(DatabaseIssueReport, issue_id).status == status
    after_records = row_projection.records_projection(session, row, ROUTE)
    assert [(r["confidence_level"], r["_disputed"], r["_source_check"]) for r in after_records] == [
        (r["confidence_level"], r["_disputed"], r["_source_check"]) for r in prior_records]


@pytest.mark.parametrize("blocker", ["pending", "grounded_dispute", "contradiction"])
def test_explicit_manual_publication_preserves_quality_state_without_blocking_saved_answer(
        client, publication_session, blocker):
    session, row_id = publication_session
    row = session.get(KimiRouteGuidanceCache, row_id)
    if blocker == "pending":
        row.verification = dict(row.verification, detail_pending=True)
    elif blocker == "grounded_dispute":
        row.verification = dict(row.verification, grounded_check={
            "outcome": "checked", "evidence_contract": freshness.EVIDENCE_CONTRACT,
            "consistent": False, "disputed_fields": ["government_fee"]})
    else:
        row.guidance = {"disposition": "VISA_EXEMPT", "requirement_detail": "evisa",
            "government_fee": {"amount": 25, "currency": "USD"},
            "visa_products": [{"type": "Tourist e-visa", "fee": {"amount": 25, "currency": "USD"}}]}
    session.commit()
    saved = deepcopy(row.guidance)
    before, count = current_metadata(row_id), releases(session)
    prior_records = row_projection.records_projection(session, row, ROUTE)
    response = client.post("/database/approve", headers=HEADERS, json=BODY)
    assert_manual_publication(response, session, row_id, before, count)
    lookup, _ = assert_reader_and_records_published(client)
    assert lookup["guidance"]["disposition"] == saved["disposition"]
    session.refresh(row)
    assert row.guidance == saved
    after_records = row_projection.records_projection(session, row, ROUTE)
    assert [(r["confidence_level"], r["_disputed"], r["_contradictions"], r["_source_check"]) for r in after_records] == [
        (r["confidence_level"], r["_disputed"], r["_contradictions"], r["_source_check"]) for r in prior_records]
    if blocker == "pending":
        assert lookup.get("detail_pending") is False and current_metadata(row_id)["detail_pending"] is True
    elif blocker == "grounded_dispute":
        assert lookup["grounded_check"]["disputed_fields"] == ["government_fee"]
        assert lookup["grounded_check"]["consistent"] is False
    else:
        assert lookup["contradictions"] and any(r["_contradictions"] for r in after_records)


def test_missing_answer_still_cannot_be_published(client, publication_session):
    session, row_id = publication_session
    row = session.get(KimiRouteGuidanceCache, row_id)
    row.guidance = {}
    session.commit()
    before, count = current_metadata(row_id), releases(session)
    response = client.post("/database/approve", headers=HEADERS, json=BODY)
    detail = assert_blocked_without_release(response, session, row_id, before, count)
    assert "no visa-requirement answer" in detail["message"]


def test_soft_low_release_is_confirmed_by_lookup_and_quality_records(
        client, publication_session):
    session, row_id = publication_session
    key = kp.cache_key(ROUTE)
    before = client.post("/database/lookup", headers=HEADERS, json=LOOKUP_BODY).json()
    assert before.get("held") is True, before
    count = releases(session)
    response = client.post("/database/approve", headers=HEADERS, json=BODY)
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True, "cache_key": key, "published": True,
        "held": False, "publication_state": "published", "reasons": [],
        "blocked_fields": [], "released_by": "operator"}
    after = client.post("/database/lookup", headers=HEADERS, json=LOOKUP_BODY).json()
    assert after["held"] is False and after["guidance"]["disposition"] == "VISA_EXEMPT"
    records = client.get("/database/records", headers=HEADERS,
        params={"nationality": "ISL", "destination": "NRU"}).json()["records"]
    assert records and all(r["publication_state"] == "published" and not r["held"] for r in records)
    assert releases(session) == count + 1


def test_new_issue_between_preflight_and_write_is_preserved_while_manual_publication_succeeds(
        client, publication_session, monkeypatch):
    session, row_id = publication_session
    before, count = current_metadata(row_id), releases(session)
    original_execute, inserted = session.execute, []

    def interleave(statement, *args, **kwargs):
        if isinstance(statement, Update) and statement.table.name == "kimi_route_guidance_cache" and not inserted:
            with SessionLocal() as worker:
                inserted.append(add_dispute(worker, kp.cache_key(ROUTE)))
        return original_execute(statement, *args, **kwargs)

    monkeypatch.setattr(session, "execute", interleave)
    response = client.post("/database/approve", headers=HEADERS, json=BODY)
    assert_manual_publication(response, session, row_id, before, count)
    assert len(inserted) == 1 and session.get(DatabaseIssueReport, inserted[0]).status == "open"
    assert_reader_and_records_published(client)


@pytest.mark.parametrize("new_guidance", [
    {"disposition": "VISA_EXEMPT", "permitted_stay": "60 days"},
    {"disposition": "VISA_EXEMPT", "requirement_detail": "evisa",
     "visa_products": [{"type": "e-Visa", "fee": {"amount": 25, "currency": "USD"}}]},
])
def test_concurrent_fact_change_cannot_be_approved_implicitly(
        client, publication_session, monkeypatch, new_guidance):
    session, row_id = publication_session
    before, count = current_metadata(row_id), releases(session)
    original_execute, writes = session.execute, []

    def interleave(statement, *args, **kwargs):
        if isinstance(statement, Update) and statement.table.name == "kimi_route_guidance_cache" and not writes:
            with SessionLocal() as worker:
                row = worker.get(KimiRouteGuidanceCache, row_id)
                row.guidance = deepcopy(new_guidance)
                worker.commit()
                writes.append(True)
        return original_execute(statement, *args, **kwargs)

    monkeypatch.setattr(session, "execute", interleave)
    response = client.post("/database/approve", headers=HEADERS, json=BODY)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "publication_changed"
    assert current_metadata(row_id) == before and releases(session) == count
    with SessionLocal() as reader:
        assert reader.get(KimiRouteGuidanceCache, row_id).guidance == new_guidance


def test_source_override_change_after_preflight_cannot_publish_unreviewed_values(
        client, publication_session, monkeypatch):
    session, row_id = publication_session
    before, count = current_metadata(row_id), releases(session)
    current_stay = [30]
    original_execute = session.execute
    monkeypatch.setattr(kp, "apply_verified_overrides", lambda out, route: dict(out,
        guidance=dict(out["guidance"], permitted_stay=f"{current_stay[0]} days")))

    def interleave(statement, *args, **kwargs):
        if isinstance(statement, Update) and statement.table.name == "kimi_route_guidance_cache":
            # External source overlays have no cache-row revision. Both
            # versions pass publication checks, but the second was not reviewed.
            current_stay[0] = 60
        return original_execute(statement, *args, **kwargs)

    monkeypatch.setattr(session, "execute", interleave)
    response = client.post("/database/approve", headers=HEADERS, json=BODY)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "publication_changed"
    assert current_metadata(row_id) == before and releases(session) == count


@pytest.mark.parametrize("wrong_route", [
    dict(ROUTE, destination_country="VNM"),
    dict(ROUTE, travel_document_type="diplomatic_passport"), {}, []])
def test_cached_route_and_document_must_match_the_answer_being_published(
        client, publication_session, wrong_route):
    session, row_id = publication_session
    row = session.get(KimiRouteGuidanceCache, row_id)
    row.route = deepcopy(wrong_route)
    session.commit()
    before, count = current_metadata(row_id), releases(session)
    response = client.post("/database/approve", headers=HEADERS, json=BODY)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "publication_route_mismatch"
    assert current_metadata(row_id) == before and releases(session) == count


def test_manual_release_of_soft_low_siblings_really_publishes_every_product(
        client, publication_session, monkeypatch):
    from .test_grade_publication import two_default_evisa_case
    session, row_id = publication_session
    route, raw = two_default_evisa_case()
    row = session.get(KimiRouteGuidanceCache, row_id)
    row.route, row.cache_key, row.guidance = route, kp.cache_key(route), deepcopy(raw["guidance"])
    session.commit()
    # Supply this fixture's already reviewed override provenance; leave the
    # actual product grading and publication guard untouched.
    monkeypatch.setattr(kp, "apply_verified_overrides", lambda out, route: dict(
        out, source_verified=deepcopy(raw["source_verified"]), held=False, review_required=False))
    assert [r["_held"] for r in row_projection.records_projection(session, row, route)] == [False, False, True, True]
    before, count = current_metadata(row_id), releases(session)
    response = client.post("/database/approve", headers=HEADERS, json={
        "nationality": route["passport_nationality"], "destination": route["destination_country"]})
    assert response.status_code == 200, response.text
    assert response.json()["published"] is True and response.json()["publication_state"] == "published"
    actual = current_metadata(row_id)
    assert actual.pop("operator_released")["by"] == "operator"
    assert actual == before and releases(session) == count + 1
    session.refresh(row)
    assert row.guidance == raw["guidance"]
    assert [r["_held"] for r in row_projection.records_projection(session, row, route)] == [False] * 4


def test_manual_pending_full_lookup_is_immediate_without_model_source_or_detail_work(
        client, publication_session, monkeypatch):
    from app.visa_snapshot import fetching, detail_jobs, records_guard
    session, row_id = publication_session
    row = session.get(KimiRouteGuidanceCache, row_id)
    row.verification = dict(row.verification, detail_pending=True,
        grounded_check={"outcome": "provider_error", "renewed": False, "note": "source check not completed"})
    row.missing_fields = ["required_documents"]
    row.fresh_until = datetime.now(timezone.utc) - timedelta(days=1)
    session.commit()
    before, count = current_metadata(row_id), releases(session)
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("Manual publication must serve saved content without provider/source/detail work")
    for module, name in [
        (kp, "_call"), (kp, "_live_call"), (kp, "join_detail_stage"),
        (kp, "_recover_detail_async"), (kp, "refresh_stale_async"),
        (detail_jobs, "recover_row"), (freshness, "recheck_row"),
        (freshness, "recheck_route"), (fetching, "fetch"),
    ]:
        monkeypatch.setattr(module, name, forbidden)
    response = client.post("/database/approve", headers=HEADERS, json=BODY)
    assert_manual_publication(response, session, row_id, before, count)
    direct = kp.get_route_guidance(session, ROUTE, stage="full")
    assert direct["guidance"]["disposition"] == "VISA_EXEMPT"
    direct = records_guard.apply_records_hold(ROUTE, direct, session)
    assert direct.get("detail_pending") is False and direct.get("held") is False
    lookup, _ = assert_reader_and_records_published(client)
    assert lookup["detail_pending"] is False and lookup["missing_fields"] == ["required_documents"]
    assert current_metadata(row_id)["detail_pending"] is True
    assert current_metadata(row_id)["grounded_check"] == before["grounded_check"]
    assert calls == []


@pytest.mark.parametrize("legacy_release", [
    {"by": "old-reviewer", "at": "earlier"},
    {"by": "old-reviewer", "at": "earlier", "mode": "different_mode"},
])
def test_legacy_or_nonmanual_release_does_not_bypass_pending_guard(
        client, publication_session, monkeypatch, legacy_release):
    from app.visa_snapshot import detail_jobs, records_guard
    session, row_id = publication_session
    row = session.get(KimiRouteGuidanceCache, row_id)
    row.verification = dict(row.verification, detail_pending=True, operator_released=legacy_release)
    session.commit()
    monkeypatch.setattr(detail_jobs, "retry_eligible", lambda _: False)
    joins = []
    monkeypatch.setattr(kp, "join_detail_stage", lambda **kwargs: joins.append(kwargs))
    direct = kp.get_route_guidance(session, ROUTE, stage="full")
    guarded = records_guard.apply_records_hold(ROUTE, direct, session)
    assert guarded["detail_pending"] is True and guarded["held"] is True
    assert len(joins) == 1
    lookup = client.post("/database/lookup", headers=HEADERS, json=LOOKUP_BODY).json()
    assert lookup["held"] is True and lookup["guidance"] is None
    assert current_metadata(row_id)["operator_released"] == legacy_release
