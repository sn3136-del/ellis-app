"""Runtime-mode boundary: MockPortal only in test/local_mock_demo; real-only
modes fail closed with typed stops and NEVER fall back to MockPortal."""
import os

import pytest
from fastapi.testclient import TestClient

from app import config
from app.portal import driver_factory as df


@pytest.fixture()
def clean_settings():
    saved = {k: os.environ.get(k) for k in ("ELLIS_RUNTIME_MODE", "ELLIS_ENV")}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    config.settings.cache_clear()


def _set_mode(mode=None, env=None):
    os.environ.pop("ELLIS_RUNTIME_MODE", None)
    os.environ.pop("ELLIS_ENV", None)
    if mode is not None:
        os.environ["ELLIS_RUNTIME_MODE"] = mode
    if env is not None:
        os.environ["ELLIS_ENV"] = env
    config.settings.cache_clear()
    return config.settings()


def test_mode_derivation_and_failclosed(clean_settings):
    assert _set_mode(env="development").runtime_mode == "local_mock_demo"
    assert _set_mode(env="test").runtime_mode == "test"
    assert _set_mode(env="staging").runtime_mode == "staging"
    assert _set_mode(env="production").runtime_mode == "production"
    assert _set_mode(env="weird-env").runtime_mode == "production"      # fail closed
    s = _set_mode(mode="banana", env="development")
    assert s.runtime_mode == "production" and s.runtime_mode_invalid    # fail closed
    assert _set_mode(mode="tripcom_evaluation").runtime_mode == "tripcom_evaluation"


def test_mode_flags(clean_settings):
    for m in ("test", "local_mock_demo"):
        s = _set_mode(mode=m)
        assert s.mock_portal_allowed and not s.real_only_mode
    for m in ("tripcom_evaluation", "staging", "production"):
        s = _set_mode(mode=m)
        assert s.real_only_mode and not s.mock_portal_allowed
        assert s.production_mode  # real-only modes harden the display guard too


def test_real_only_portal_build_refuses_mockportal(clean_settings, monkeypatch):
    _set_mode(mode="production")
    # Prove MockPortal is not even CONSTRUCTED: poison the constructor.
    from app.portal import mock_portal
    monkeypatch.setattr(mock_portal.MockPortal, "__init__",
                        lambda self, *a, **k: (_ for _ in ()).throw(AssertionError("MockPortal constructed")))
    with pytest.raises(df.RealOnlyStop) as ei:
        df.build_runtime_portal(None)
    assert ei.value.status == "PORTAL_UNAVAILABLE"


def test_real_only_selection_stops_unsupported_no_fallback(clean_settings):
    for m in ("tripcom_evaluation", "staging", "production"):
        _set_mode(mode=m)
        df.register_adapters_for_mode()   # registers NOTHING in real-only modes
        from app.portal.contract import list_adapters
        assert list_adapters() == []
        with pytest.raises(df.RealOnlyStop) as ei:
            df.select_runtime_adapter("Japan", "tourist")
        assert ei.value.status == "UNSUPPORTED"
        assert df.select_metadata_adapter("Japan", "tourist") is None


def test_mock_modes_still_work(clean_settings):
    for m in ("test", "local_mock_demo"):
        _set_mode(mode=m)
        portal = df.build_runtime_portal(None)
        assert getattr(portal, "execution_class", "") == "MOCK"
        df.register_runtime_adapters(portal)
        adapter = df.select_runtime_adapter("Unknown Country", "tourist")
        assert adapter.adapter_id.startswith("mockland")  # demo fallback allowed here


def test_missing_live_adapter_stops_workflow_without_mockportal(clean_settings, monkeypatch):
    """THE brief-required regression: in a real-only mode a case with no live
    adapter stops (typed status), and MockPortal is never invoked."""
    _set_mode(mode="production")
    from app.portal import mock_portal
    constructed = []
    orig = mock_portal.MockPortal.__init__
    monkeypatch.setattr(mock_portal.MockPortal, "__init__",
                        lambda self, *a, **k: constructed.append(1) or orig(self, *a, **k))
    from app import service
    from app.db import SessionLocal, create_all
    from app import models
    create_all()
    db = SessionLocal()
    try:
        applicant = models.Applicant(org_id="org-rt", user_id="u", full_name="T", email="t@example.com")
        db.add(applicant); db.commit()
        app_row = models.VisaApplication(org_id="org-rt", user_id="u", applicant_id=applicant.id,
                                         destination_country="Japan", visa_type="tourist")
        db.add(app_row); db.commit()
        with pytest.raises(df.RealOnlyStop) as ei:
            service.signal(db, app_row.id, "start")
        assert ei.value.status in ("UNSUPPORTED", "PORTAL_UNAVAILABLE")
        assert constructed == []                       # MockPortal never touched
        assert db.get(models.VisaApplication, app_row.id) is not None  # case preserved
    finally:
        db.close()


def test_http_start_returns_typed_stop_in_real_only(clean_settings):
    _set_mode(mode="tripcom_evaluation")
    from app.main import app as fastapi_app
    client = TestClient(fastapi_app)
    h = {"Authorization": "Bearer dev-token", "X-Org-Id": "org-rt2", "X-User-Id": "u"}
    r = client.post("/cases", json={"full_name": "T", "email": "t@example.com",
                                    "destination_country": "Japan", "visa_type": "tourist"},
                    headers=h)
    assert r.status_code == 200, r.text
    case_id = r.json()["id"]
    r2 = client.post(f"/cases/{case_id}/start", headers=h)
    assert r2.status_code == 409
    detail = r2.json()["detail"]
    assert detail["reason"] == "real_only_stop"
    assert detail["status"] in ("UNSUPPORTED", "PORTAL_UNAVAILABLE")


def test_capabilities_reports_runtime_mode(clean_settings):
    _set_mode(mode="local_mock_demo")
    from app.main import app as fastapi_app
    client = TestClient(fastapi_app)
    h = {"Authorization": "Bearer dev-token", "X-Org-Id": "o", "X-User-Id": "u"}
    caps = client.get("/capabilities", headers=h).json()
    assert caps["runtime_mode"] == "local_mock_demo"
    assert caps["execution_classification"]["runtime_portal_execution_class"] == "MOCK"
    _set_mode(mode="production")
    caps = client.get("/capabilities", headers=h).json()
    assert caps["runtime_mode"] == "production"
    assert caps["execution_classification"]["runtime_portal_execution_class"] == "UNSUPPORTED"


def test_adapters_endpoint_empty_in_real_only(clean_settings):
    _set_mode(mode="staging")
    from app.main import app as fastapi_app
    client = TestClient(fastapi_app)
    h = {"Authorization": "Bearer dev-token", "X-Org-Id": "o", "X-User-Id": "u"}
    r = client.get("/adapters", headers=h).json()
    assert r["adapters"] == [] and r["runtime_mode"] == "staging"


def test_mock_verification_endpoint_hidden_in_real_only(clean_settings):
    _set_mode(mode="production", env="development")
    from app.main import app as fastapi_app
    client = TestClient(fastapi_app)
    h = {"Authorization": "Bearer dev-token", "X-Org-Id": "o", "X-User-Id": "u"}
    r = client.get("/cases/nonexistent/mock/verification", headers=h)
    assert r.status_code == 404


def test_db_portal_store_refuses_real_only(clean_settings):
    _set_mode(mode="production")
    from app.portal_store import DbPortalStore
    with pytest.raises(RuntimeError):
        DbPortalStore(ensure_schema=False)


def test_temporal_worker_wiring_refuses_real_only(clean_settings):
    _set_mode(mode="staging")
    from app import temporal_worker
    with pytest.raises(SystemExit):
        temporal_worker._wire_portal_store()
