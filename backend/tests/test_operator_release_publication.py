"""Publishing reports the reader's actual state and never records a false release."""
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


@pytest.mark.parametrize("already_released", [False, True])
@pytest.mark.parametrize("status", ["open", "acknowledged"])
def test_open_material_dispute_cannot_be_published_or_falsely_audited(
        client, publication_session, already_released, status):
    session, row_id = publication_session
    row = session.get(KimiRouteGuidanceCache, row_id)
    if already_released:
        row.verification = dict(row.verification, operator_released={"by": "earlier-reviewer", "at": "earlier"})
        session.commit()
    add_dispute(session, row.cache_key, status=status)
    before, count = current_metadata(row_id), releases(session)
    response = client.post("/database/approve", headers=HEADERS, json=BODY)
    detail = assert_blocked_without_release(response, session, row_id, before, count)
    assert detail["publication_state"] == "withheld"
    assert detail["blocked_fields"] == ["government_fee"]
    assert "government_fee" in detail["message"]
    lookup = client.post("/database/lookup", headers=HEADERS, json=LOOKUP_BODY).json()
    assert lookup.get("held") is True and lookup["guidance"] is None, lookup


@pytest.mark.parametrize("blocker", ["pending", "grounded_dispute", "contradiction", "missing_answer"])
def test_release_respects_live_shared_publication_checks(
        client, publication_session, blocker):
    session, row_id = publication_session
    row = session.get(KimiRouteGuidanceCache, row_id)
    if blocker == "pending":
        row.verification = dict(row.verification, detail_pending=True)
    elif blocker == "grounded_dispute":
        row.verification = dict(row.verification, grounded_check={
            "outcome": "checked", "evidence_contract": freshness.EVIDENCE_CONTRACT,
            "consistent": False, "disputed_fields": ["government_fee"]})
    elif blocker == "contradiction":
        row.guidance = {"disposition": "VISA_EXEMPT", "requirement_detail": "evisa",
            "government_fee": {"amount": 25, "currency": "USD"},
            "visa_products": [{"type": "Tourist e-visa", "fee": {"amount": 25, "currency": "USD"}}]}
    else:
        row.guidance = {}
    session.commit()
    before, count = current_metadata(row_id), releases(session)
    response = client.post("/database/approve", headers=HEADERS, json=BODY)
    detail = assert_blocked_without_release(response, session, row_id, before, count)
    if blocker == "pending":
        assert "pending" in detail["message"]
    if blocker == "missing_answer":
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


def test_new_issue_between_preflight_and_write_rolls_back_release_metadata_and_audit(
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
    detail = assert_blocked_without_release(response, session, row_id, before, count)
    assert detail["blocked_fields"] == ["government_fee"] and len(inserted) == 1
    assert session.get(DatabaseIssueReport, inserted[0]).status == "open"


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
