"""Browserbase session infrastructure: isolation, short-lived URLs, honesty."""
import pytest
from fastapi.testclient import TestClient

from app.main import app as fastapi_app
from app.providers import browser as bb

H = {"Authorization": "Bearer dev-token", "X-Org-Id": "org-bb", "X-User-Id": "u1"}
OTHER = {"Authorization": "Bearer dev-token", "X-Org-Id": "org-other", "X-User-Id": "u2"}


@pytest.fixture()
def client():
    return TestClient(fastapi_app)


@pytest.fixture()
def case_id(client):
    r = client.post("/cases", json={"full_name": "T", "email": "t@example.com",
                                    "destination_country": "Mockland",
                                    "visa_type": "tourist"}, headers=H)
    assert r.status_code == 200
    return r.json()["id"]


@pytest.fixture()
def fake_bb(monkeypatch):
    calls = {"created": 0, "closed": [], "lv": 0}

    def create_session():
        calls["created"] += 1
        return {"id": f"bb-sess-{calls['created']}", "mode": "browserbase",
                "connect_url": "wss://connect.example/x"}

    def live_view_url(session_id):
        calls["lv"] += 1
        return f"https://www.browserbase.com/devtools-fullscreen/{session_id}?t={calls['lv']}"

    def close_session(session_id):
        calls["closed"].append(session_id)
    monkeypatch.setattr(bb, "create_session", create_session)
    monkeypatch.setattr(bb, "live_view_url", live_view_url)
    monkeypatch.setattr(bb, "close_session", close_session)
    return calls


def test_session_lifecycle_and_reuse(client, case_id, fake_bb):
    r = client.post(f"/cases/{case_id}/browser-session", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "browserbase" and body["live_view_available"] is True
    assert "provider_session_id" not in body            # provider id never exposed
    assert r.headers["cache-control"] == "no-store"
    # Second create reuses the open session (isolated one-per-case):
    client.post(f"/cases/{case_id}/browser-session", headers=H)
    assert fake_bb["created"] == 1

    lv = client.get(f"/cases/{case_id}/browser-session/live-view", headers=H)
    assert lv.status_code == 200
    assert lv.json()["url"].startswith("https://www.browserbase.com/")
    assert lv.headers["cache-control"] == "no-store"
    # Minted FRESH each request (short-lived), not cached/persisted:
    lv2 = client.get(f"/cases/{case_id}/browser-session/live-view", headers=H)
    assert lv2.json()["url"] != lv.json()["url"]

    closed = client.delete(f"/cases/{case_id}/browser-session", headers=H).json()
    assert closed["closed"] == 1 and fake_bb["closed"] == ["bb-sess-1"]
    # After close: honest 404, and a new POST opens a fresh isolated session.
    assert client.get(f"/cases/{case_id}/browser-session/live-view", headers=H).status_code == 404
    client.post(f"/cases/{case_id}/browser-session", headers=H)
    assert fake_bb["created"] == 2


def test_tenant_isolation(client, case_id, fake_bb):
    client.post(f"/cases/{case_id}/browser-session", headers=H)
    assert client.post(f"/cases/{case_id}/browser-session", headers=OTHER).status_code == 403
    assert client.get(f"/cases/{case_id}/browser-session/live-view", headers=OTHER).status_code == 403
    assert client.delete(f"/cases/{case_id}/browser-session", headers=OTHER).status_code == 403


def test_local_mode_is_honest_no_liveview(client, case_id):
    # No Browserbase key in hermetic tests -> local mode, live view 404s honestly.
    r = client.post(f"/cases/{case_id}/browser-session", headers=H)
    assert r.json()["mode"] == "local" and r.json()["live_view_available"] is False
    lv = client.get(f"/cases/{case_id}/browser-session/live-view", headers=H)
    assert lv.status_code == 404
    # Structured detail: the renderer distinguishes "not configured" from a
    # session that simply ENDED (they need different applicant wording).
    assert lv.json()["detail"]["reason"] == "not_configured"
    assert "local mode" in lv.json()["detail"]["message"]


def test_live_view_url_never_in_audit_or_logs(client, case_id, fake_bb, db):
    client.post(f"/cases/{case_id}/browser-session", headers=H)
    client.get(f"/cases/{case_id}/browser-session/live-view", headers=H)
    client.delete(f"/cases/{case_id}/browser-session", headers=H)
    from app.models import AuditEvent
    from sqlalchemy import select
    events = db.execute(select(AuditEvent).where(
        AuditEvent.application_id == case_id)).scalars().all()
    blob = " ".join(str(e.detail) + e.action for e in events)
    assert "browserbase.com" not in blob
    assert "bb-sess" not in blob                     # provider ids not audited either
    assert any(e.action == "browser_session_opened" for e in events)
    assert any(e.action == "browser_session_closed" for e in events)


def test_observability_scrub_redacts_live_view_urls():
    from app.observability import scrub
    out = scrub({"msg": "handoff at https://www.browserbase.com/devtools-fullscreen/abc?t=1"})
    assert "browserbase.com/devtools-fullscreen/abc" not in str(out)
