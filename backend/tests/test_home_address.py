"""Structured home address at intake: mandatory (line 1 / city / country
only — country-aware, no U.S. format assumed), enforced server-side at
resolve AND continuation, carried into the case answers for form
preparation, portal account creation and adapters, and never logged."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal, create_all
from app.main import app as fastapi_app
from app import models as core_models
from app.visa_snapshot import kimi_primary
from app.visa_snapshot.api import INTAKE_FIELDS
from app.visa_snapshot.models import KimiRouteGuidanceCache

from .test_intake_flow import (H, ANSWERS_SGP, EXEMPT_ANSWER, _passport_text,
                               _single_pass)


@pytest.fixture()
def db():
    create_all()
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture()
def client():
    return TestClient(fastapi_app)


@pytest.fixture(autouse=True)
def _reset(db):
    for row in db.execute(select(KimiRouteGuidanceCache)).scalars().all():
        db.delete(row)
    db.commit()
    yield
    kimi_primary.set_provider(None)


def _strip_address(answers: dict) -> dict:
    return {k: v for k, v in answers.items() if not k.startswith("address_")}


def test_intake_contract_declares_structured_address_fields():
    by_key = {f["key"]: f for f in INTAKE_FIELDS}
    assert by_key["address_line1"]["required"] is True
    assert by_key["address_city"]["required"] is True
    assert by_key["address_country"]["required"] is True
    # Country-aware: region and postal code are NEVER universally required.
    assert by_key["address_region"]["required"] is False
    assert by_key["address_postal_code"]["required"] is False
    assert by_key["address_line2"]["required"] is False


def test_resolve_refuses_intake_without_address(client):
    answers = _strip_address(dict(ANSWERS_SGP, destination_country="LVA"))
    iid = client.post("/intake", json={"answers": answers}, headers=H).json()["id"]
    r = client.post(f"/intake/{iid}/resolve", headers=H)
    assert r.status_code == 422
    missing = r.json()["detail"]["missing_fields"]
    assert {"address_line1", "address_city", "address_country"} <= set(missing)


def test_continue_refuses_intake_without_address(client):
    kimi_primary.set_provider(_single_pass(EXEMPT_ANSWER))
    answers = _strip_address(dict(ANSWERS_SGP, destination_country="EST"))
    iid = client.post("/intake", json={"answers": answers}, headers=H).json()["id"]
    client.post(f"/intake/{iid}/passport",
                json={"name": "p.pdf", "text": _passport_text()}, headers=H)
    client.post(f"/intake/{iid}/guidance", headers=H)
    r = client.post(f"/intake/{iid}/continue", headers=H)
    assert r.status_code == 422
    assert r.json()["detail"]["reason"] == "address_required"


def test_international_address_without_region_or_postal_is_valid(client):
    kimi_primary.set_provider(_single_pass(EXEMPT_ANSWER))
    answers = dict(_strip_address(dict(ANSWERS_SGP, destination_country="ISL")),
                   address_line1="Plot 5, Airport Road",
                   address_city="Kigali", address_country="RWA")
    iid = client.post("/intake", json={"answers": answers}, headers=H).json()["id"]
    client.post(f"/intake/{iid}/passport",
                json={"name": "p.pdf", "text": _passport_text()}, headers=H)
    client.post(f"/intake/{iid}/guidance", headers=H)
    r = client.post(f"/intake/{iid}/continue", headers=H)
    assert r.status_code == 200


def test_address_persists_and_transfers_into_the_case(client, db):
    kimi_primary.set_provider(_single_pass(EXEMPT_ANSWER))
    answers = dict(ANSWERS_SGP, destination_country="FIN")
    iid = client.post("/intake", json={"answers": answers}, headers=H).json()["id"]
    # Persists across refresh (a fresh GET returns the saved structured fields).
    got = client.get(f"/intake/{iid}", headers=H).json()["answers"]
    assert got["address_line1"] == "12 Harbor Lane"
    assert got["address_city"] == "Springfield"
    client.post(f"/intake/{iid}/passport",
                json={"name": "p.pdf", "text": _passport_text()}, headers=H)
    client.post(f"/intake/{iid}/guidance", headers=H)
    case_id = client.post(f"/intake/{iid}/continue", headers=H).json()["case_id"]
    case = client.get(f"/cases/{case_id}", headers=H).json()
    for key, val in (("address_line1", "12 Harbor Lane"),
                     ("address_city", "Springfield"),
                     ("address_region", "IL"),
                     ("address_postal_code", "62704"),
                     ("address_country", "USA")):
        assert case["answers"][key] == val
    # The same structured keys are what the workflow hands adapters verbatim
    # (workflow passes VisaApplication.answers to create_application).
    row = db.get(core_models.VisaApplication, case_id)
    assert row.answers["address_line1"] == "12 Harbor Lane"


def test_adapter_vocabulary_and_synthetic_answers_cover_address():
    from app.adapter_factory.specgen import ELLIS_FIELDS, _NAME_HINTS
    from app.adapter_factory.testing import _ANSWERS
    for key in ("address_line1", "address_line2", "address_city",
                "address_region", "address_postal_code", "address_country"):
        assert key in ELLIS_FIELDS
    assert "street_address" in _NAME_HINTS["address_line1"]
    assert "zip" in _NAME_HINTS["address_postal_code"]
    # Synthetic adapter runs have address answers available.
    assert _ANSWERS["address_line1"]


def test_address_never_in_audit_logs(client):
    kimi_primary.set_provider(_single_pass(EXEMPT_ANSWER))
    answers = dict(ANSWERS_SGP, destination_country="NOR")
    iid = client.post("/intake", json={"answers": answers}, headers=H).json()["id"]
    client.post(f"/intake/{iid}/passport",
                json={"name": "p.pdf", "text": _passport_text()}, headers=H)
    client.post(f"/intake/{iid}/guidance", headers=H)
    case_id = client.post(f"/intake/{iid}/continue", headers=H).json()["case_id"]
    import json as _json
    events = client.get(f"/cases/{case_id}/audit", headers=H).json()["events"]
    dump = _json.dumps(events)
    assert "12 Harbor Lane" not in dump
    assert "Springfield" not in dump
    assert "62704" not in dump
