"""The conversational surfaces must obey the same gate as structured readers."""
import pytest

from app import main
from app.visa_snapshot import assistant, kimi_primary as kp, verified_overrides as vo

HEADERS = {"Authorization": "Bearer dev-token", "X-Org-Id": "held-prose", "X-User-Id": "reader"}


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(vo, "OVERRIDES", tmp_path / "no-seed.json")
    monkeypatch.setenv("ELLIS_OPERATOR_OVERRIDES", str(tmp_path / "operators.json"))
    monkeypatch.setattr(kp, "is_available", lambda: True)
    vo.reload()
    yield
    vo.reload()


def _withdrawn_answer():
    return {"status": kp.STATUS_PRIMARY, "held": True,
            "guidance": {"disposition": "VISA_EXEMPT", "requirement_detail": "visa_free",
                         "permitted_stay": "999 days", "source_url": "https://example.com"},
            "source_verified": {"note": "Withdrawn visa-free for 999 days"}}


def _forbid_composer(*args, **kwargs):
    raise AssertionError("Held/comparison replies must not invoke a composer")


def test_held_ask_uses_deterministic_reply_despite_old_conversation(client, monkeypatch):
    monkeypatch.setattr(kp, "parse_question_with_context", lambda *a: {
        "understood": True, "nationality": "ISL", "destination": "NRU",
        "travel_purpose": "tourism", "travel_document_type": "ordinary_passport"})
    monkeypatch.setattr(kp, "get_route_guidance", lambda *a, **k: _withdrawn_answer())
    monkeypatch.setattr(assistant, "compose_reply_ex", _forbid_composer)
    response = client.post("/database/ask", headers=HEADERS, json={
        "question": "Do I need a visa for Nauru with an Icelandic passport?",
        "history": [{"role": "assistant", "text": "You are visa-free for 999 days."}]})
    assert response.status_code == 200
    answer = response.json()
    assert answer["held"] and answer["guidance"] is None
    assert answer["reply_source"] == "facts"
    assert answer["reply"].startswith("We are checking this route")
    assert "999" not in response.text and "source_verified" not in answer


def test_comparison_gates_each_cached_side_and_never_composes_unknown_facts(client, monkeypatch):
    monkeypatch.setattr(assistant, "comparison_destinations", lambda *a: ["NRU", "FSM"])
    monkeypatch.setattr(assistant, "compose_reply", _forbid_composer)
    monkeypatch.setattr(assistant, "compose_reply_ex", _forbid_composer)
    keys = []
    def cached(db, key):
        keys.append(key)
        return object() if "|NRU|" in key else None
    monkeypatch.setattr(kp, "_cached", cached)
    # The comparison must call the shared reader, not inspect raw cache
    # guidance and mistake its official-looking URL for verified evidence.
    answer = _withdrawn_answer()
    answer["held"] = False
    monkeypatch.setattr(kp, "get_route_guidance", lambda *a, **k: answer)
    response = client.post("/database/ask", headers=HEADERS, json={
        "question": "Compare visa requirements for Nauru and Micronesia",
        "context": {"nationality": "ISL"},
        "history": [{"role": "assistant", "text": "Both are visa-free for 999 days."}]})
    assert response.status_code == 200
    out = response.json()
    assert out["comparison"] and out["reply_source"] == "facts"
    assert "NRU: being checked against official sources" in out["reply"]
    assert "FSM: not answered yet" in out["reply"]
    assert "999" not in out["reply"] and "visa-free" not in out["reply"]
    assert out["routes"][0]["held"] and out["routes"][1]["unverified"]
    assert len(keys) == 2 and all(kp.is_canonical_key(key) for key in keys)


def test_comparison_preserves_document_purpose_and_policy_date(client, monkeypatch):
    monkeypatch.setattr(assistant, "comparison_destinations", lambda *a: ["NRU", "FSM"])
    routes = []
    monkeypatch.setattr(kp, "_cached", lambda *a: object())
    def answer(db, route, **kwargs):
        routes.append(route)
        return _withdrawn_answer()
    monkeypatch.setattr(kp, "get_route_guidance", answer)
    response = client.post("/database/ask", headers=HEADERS, json={
        "question": "Compare visa requirements for work with my diplomatic passport",
        "context": {"nationality": "ISL", "travel_purpose": "tourism",
                    "travel_document_type": "ordinary_passport", "arrival_date": "2027-04-03"}})
    assert response.status_code == 200
    assert len(routes) == 2
    for route in routes:
        assert route["travel_purpose"] == "work"
        assert route["travel_document_type"] == "diplomatic_passport"
        assert route["arrival_date"] == "2027-04-03"
