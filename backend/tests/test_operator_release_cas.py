"""An operator release cannot erase a concurrently committed page check."""
import copy

import pytest
from sqlalchemy.sql.dml import Update

from app.db import SessionLocal, get_session
from app.main import app
from app.visa_snapshot import kimi_primary as kp, freshness
from app.visa_snapshot.models import KimiRouteGuidanceCache

HEADERS = {"Authorization": "Bearer admin-token", "X-Org-Id": "release-cas",
           "X-User-Id": "operator"}
ROUTE = {"passport_nationality": "ISL", "destination_country": "NRU",
         "travel_purpose": "tourism", "travel_document_type": "ordinary_passport"}
BODY = {"nationality": "ISL", "destination": "NRU", "note": "Reviewed this route"}


@pytest.fixture
def release_session(db):
    key = kp.cache_key(ROUTE)
    db.query(KimiRouteGuidanceCache).filter_by(cache_key=key).delete()
    row = KimiRouteGuidanceCache(cache_key=key, route=ROUTE, guidance={"disposition": "VISA_EXEMPT"},
        verification={"detail_pending": False, "history": ["original review"],
                      "last_good_check": {"outcome": "checked", "at": "original"}},
        model="release-cas-fixture")
    db.add(row)
    db.commit()
    previous_override = app.dependency_overrides.get(get_session)
    app.dependency_overrides[get_session] = lambda: db
    yield db, row.id
    if previous_override is None:
        app.dependency_overrides.pop(get_session, None)
    else:
        app.dependency_overrides[get_session] = previous_override
    db.rollback()
    db.query(KimiRouteGuidanceCache).filter_by(model="release-cas-fixture").delete()
    db.commit()


def interleave_worker(monkeypatch, session, row_id, *, times, disputed=False):
    """A distinct DB session commits AFTER the release SELECT, before UPDATE."""
    execute = session.execute
    writes = []

    def intercepted(statement, *args, **kwargs):
        if isinstance(statement, Update) and statement.table.name == "kimi_route_guidance_cache" \
                and len(writes) < times:
            with SessionLocal() as worker:
                row = worker.get(KimiRouteGuidanceCache, row_id)
                metadata = copy.deepcopy(row.verification)
                metadata["grounded_check"] = {"outcome": "checked", "consistent": not disputed,
                    "evidence_contract": freshness.EVIDENCE_CONTRACT,
                    "at": f"new worker check {len(writes) + 1}",
                    "disputed_fields": ["government_fee"] if disputed else []}
                metadata["history"].append(f"worker {len(writes) + 1}")
                row.verification = metadata
                worker.commit()
                writes.append(copy.deepcopy(metadata))
        return execute(statement, *args, **kwargs)

    monkeypatch.setattr(session, "execute", intercepted)
    return writes


def test_release_retries_real_metadata_race_without_erasing_worker_history(
        client, release_session, monkeypatch):
    session, row_id = release_session
    writes = interleave_worker(monkeypatch, session, row_id, times=1)
    response = client.post("/database/approve", headers=HEADERS, json=BODY)
    assert response.status_code == 200, response.text
    assert len(writes) == 1
    with SessionLocal() as reader:
        actual = reader.get(KimiRouteGuidanceCache, row_id).verification
    assert actual == dict(writes[-1], operator_released={
        **actual["operator_released"], "by": "operator", "note": BODY["note"]})
    assert actual["detail_pending"] is False
    assert actual["last_good_check"]["at"] == "original"
    assert actual["grounded_check"]["disputed_fields"] == []
    assert actual["history"] == ["original review", "worker 1"]


def test_release_conflict_is_bounded_and_never_loses_concurrent_metadata(
        client, release_session, monkeypatch):
    session, row_id = release_session
    writes = interleave_worker(monkeypatch, session, row_id, times=3)
    response = client.post("/database/approve", headers=HEADERS, json=BODY)
    assert response.status_code == 409, response.text
    assert len(writes) == 3
    with SessionLocal() as reader:
        actual = reader.get(KimiRouteGuidanceCache, row_id).verification
    assert actual == writes[-1]
    assert "operator_released" not in actual


def test_release_accepts_legacy_json_null_metadata(client, release_session):
    session, row_id = release_session
    row = session.get(KimiRouteGuidanceCache, row_id)
    row.verification = None
    session.commit()
    response = client.post("/database/approve", headers=HEADERS, json=BODY)
    assert response.status_code == 200, response.text
    session.refresh(row)
    assert row.verification["operator_released"]["by"] == "operator"


def test_new_material_dispute_during_release_is_preserved_and_blocks_retry(
        client, release_session, monkeypatch):
    session, row_id = release_session
    writes = interleave_worker(monkeypatch, session, row_id, times=1, disputed=True)
    response = client.post("/database/approve", headers=HEADERS, json=BODY)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "publication_blocked"
    assert response.json()["detail"]["blocked_fields"] == ["government_fee"]
    with SessionLocal() as reader:
        actual = reader.get(KimiRouteGuidanceCache, row_id).verification
    assert actual == writes[-1]
    assert "operator_released" not in actual
