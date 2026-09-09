"""Audit history must preserve actual field values through search and CSV."""
import csv
import io
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import main
from app.db import get_session
from app.visa_snapshot.models import DatabaseChangeLog


ADMIN = {"authorization": "Bearer admin-token", "x-org-id": "history-test",
         "x-user-id": "operator-test"}


@pytest.fixture
def history(monkeypatch):
    engine = create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    DatabaseChangeLog.__table__.create(engine)
    db = Session(engine)
    prior = main.app.dependency_overrides.get(get_session)
    main.app.dependency_overrides[get_session] = lambda: db
    monkeypatch.setattr(main, "_change_source", lambda *_: {
        "source_url": "https://www.canada.ca/", "source_host": "www.canada.ca",
        "source_official": True})

    def add(changes, *, note="reviewed update", at=None):
        row = DatabaseChangeLog(
            id=uuid4().hex, cache_key="GBR|GBR|CAN|tourism|default|unknown|v6",
            route={"passport_nationality": "GBR", "destination_country": "CAN",
                   "travel_purpose": "tourism"},
            action="modify", origin="operator", note=note, changes=changes,
            created_at=at or datetime.now(timezone.utc))
        db.add(row)
        db.commit()
        return row.id

    try:
        yield TestClient(main.app), add
    finally:
        if prior is None:
            main.app.dependency_overrides.pop(get_session, None)
        else:
            main.app.dependency_overrides[get_session] = prior
        db.close()
        engine.dispose()


@pytest.mark.parametrize("query", ["calendar", "180 days", "supporting passport"])
def test_change_search_finds_old_new_and_nested_values(history, query):
    client, add = history
    wanted = add({
        "permitted_stay": {"from": "180 days", "to": "6 calendar months"},
        "required_documents": {"from": [], "to": ["supporting passport"]},
    })
    add({"permitted_stay": {"from": "14 days", "to": "30 days"}})
    response = client.get("/database/changes", params={"q": query}, headers=ADMIN)
    assert response.status_code == 200
    assert [row["id"] for row in response.json()["changes"]] == [wanted]


@pytest.mark.parametrize("literal,other", [
    ("100%", "100X"), ("zone_A", "zoneXA"), ("desk\\A", "deskXA"),
])
def test_change_search_treats_sql_wildcards_as_literal_text(history, literal, other):
    client, add = history
    wanted = add({}, note="Specific " + literal)
    add({}, note="Specific " + other)
    response = client.get("/database/changes", params={"q": literal}, headers=ADMIN)
    assert response.status_code == 200
    assert [row["id"] for row in response.json()["changes"]] == [wanted]


@pytest.mark.parametrize("text", ["零费用", 'bring "photo"', "desk\\A"])
def test_change_value_search_hides_json_storage_encoding(history, text):
    client, add = history
    wanted = add({"required_documents": {"from": [], "to": [text]}})
    add({"required_documents": {"from": [], "to": ["unrelated document"]}})
    response = client.get("/database/changes", params={"q": text}, headers=ADMIN)
    assert response.status_code == 200
    assert [row["id"] for row in response.json()["changes"]] == [wanted]


def test_change_search_filters_before_limiting_and_csv_uses_the_same_query(history):
    client, add = history
    then = datetime(2026, 9, 1, tzinfo=timezone.utc)
    wanted = add({"permitted_stay": {"from": "180 days", "to": "6 calendar months"}}, at=then)
    for offset in range(1, 8):
        add({"permitted_stay": {"from": "14 days", "to": "30 days"}},
            at=then + timedelta(days=offset))
    query = {"q": "calendar", "limit": 1}
    response = client.get("/database/changes", params=query, headers=ADMIN)
    assert response.status_code == 200
    assert [row["id"] for row in response.json()["changes"]] == [wanted]
    response = client.get("/database/changes.csv", params=query, headers=ADMIN)
    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert len(rows) == 1
    assert rows[0]["from"] == "180 days" and rows[0]["to"] == "6 calendar months"


def test_csv_preserves_zero_false_null_and_nested_values(history):
    client, add = history
    nested = {"amount": 0, "currency": "USD", "condition": "零费用"}
    add({
        "permitted_stay_days": {"from": 0, "to": 1},
        "processing_time": {"from": None, "to": 0},
        "insurance_required": {"from": True, "to": False},
        "government_fee": {"from": None, "to": nested},
        "required_documents": {"from": [], "to": ["passport", "photo"]},
        "passport_validity": {"from": "", "to": None},
    })
    response = client.get("/database/changes.csv", headers=ADMIN)
    assert response.status_code == 200
    rows = {row["field"]: row for row in csv.DictReader(
        io.StringIO(response.content.decode("utf-8-sig")))}
    assert (rows["permitted_stay_days"]["from"], rows["permitted_stay_days"]["to"]) == ("0", "1")
    assert rows["processing_time"]["to"] == "0"
    assert (rows["insurance_required"]["from"], rows["insurance_required"]["to"]) == ("True", "False")
    assert rows["government_fee"]["from"] == ""
    assert json.loads(rows["government_fee"]["to"]) == nested
    assert json.loads(rows["required_documents"]["from"]) == []
    assert json.loads(rows["required_documents"]["to"]) == ["passport", "photo"]
    assert rows["passport_validity"]["from"] == rows["passport_validity"]["to"] == ""
    assert all(row["source_url"] == "https://www.canada.ca/" and
               row["official_domain"] == "yes" for row in rows.values())


@pytest.mark.parametrize("old_key,new_key", [("from", "to"), ("before", "after")])
def test_csv_preserves_passport_unknown_migration_and_explicit_legacy_diff(history, old_key, new_key):
    client, add = history
    empty_rule = {"kind": None, "months": None}
    add({"passport_validity_requirement": {old_key: empty_rule, new_key: None}})
    response = client.get("/database/changes.csv", headers=ADMIN)
    assert response.status_code == 200
    row, = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert row["field"] == "passport_validity_requirement"
    assert json.loads(row["from"]) == empty_rule
    assert row["to"] == "" and row["raw_change"] == ""


@pytest.mark.parametrize("payload", [["retained-issue-id"], False, 0, None, {"unrecognized": "metadata"}])
def test_csv_retains_unrecognized_field_metadata_without_inventing_a_transition(history, payload):
    client, add = history
    add({"legacy_metadata": payload})
    response = client.get("/database/changes.csv", headers=ADMIN)
    assert response.status_code == 200
    row, = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert row["from"] == row["to"] == ""
    assert json.loads(row["raw_change"]) == payload


def test_csv_retains_non_dictionary_top_level_history(history):
    client, add = history
    payload = [{"old": "unstructured historical payload"}]
    add(payload)
    response = client.get("/database/changes.csv", headers=ADMIN)
    assert response.status_code == 200
    row, = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert row["field"] == row["from"] == row["to"] == ""
    assert json.loads(row["raw_change"]) == payload


def test_csv_canonical_keys_win_over_legacy_aliases_but_preserve_both(history):
    client, add = history
    payload = {"from": 5, "to": 0, "before": 9, "after": False}
    add({"processing_time": payload})
    response = client.get("/database/changes.csv", headers=ADMIN)
    assert response.status_code == 200
    row, = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert (row["from"], row["to"]) == ("5", "0")
    assert json.loads(row["raw_change"]) == payload
