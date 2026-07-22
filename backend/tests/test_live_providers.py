"""Live provider smoke tests — REAL network calls, self-contained so the
hermetic conftest can't affect them. Skipped unless the credential is in
backend/.env. They never print personal values.

Run:  (cd backend && . .venv/bin/activate && python -m pytest tests/test_live_providers.py -q)
"""
import base64
import os

import httpx
import pytest


def _dotenv(key: str) -> str:
    path = os.path.join(os.path.dirname(__file__), "..", ".env")
    try:
        for line in open(path):
            line = line.strip()
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return ""


KIMI = _dotenv("MOONSHOT_API_KEY")
KIMI_URL = _dotenv("KIMI_BASE_URL") or "https://api.moonshot.ai/v1"
KIMI_MODEL = _dotenv("KIMI_MODEL") or "kimi-k3"
BB = _dotenv("BROWSERBASE_API_KEY")
SPEC = ("/private/tmp/claude-501/-Users-sammynawaly-Documents-ellis-app/"
        "0cde54d6-d451-4ad9-a7a6-22630d1f6656/scratchpad/specimens/"
        "Dutch_passport_specimen_issued_9_March_2014.jpg")


def _kimi_content(payload) -> str:
    r = httpx.post(KIMI_URL.rstrip("/") + "/chat/completions",
                   headers={"authorization": f"Bearer {KIMI}"}, json=payload, timeout=90)
    r.raise_for_status()
    msg = r.json()["choices"][0]["message"]
    return msg.get("content") or msg.get("reasoning_content") or ""


@pytest.mark.skipif(not KIMI, reason="MOONSHOT_API_KEY not in .env")
def test_kimi_live_json():
    out = _kimi_content({"model": KIMI_MODEL, "max_tokens": 200,
                         "messages": [{"role": "user", "content": "Reply JSON {\"ok\":true}"}],
                         "response_format": {"type": "json_object"}})
    assert "ok" in out


@pytest.mark.skipif(not KIMI or not os.path.exists(SPEC), reason="KIMI or specimen missing")
def test_kimi_vision_reads_passport():
    img = base64.b64encode(open(SPEC, "rb").read()).decode()
    out = _kimi_content({"model": KIMI_MODEL, "max_tokens": 1500,
                         "messages": [{"role": "user", "content": [
                             {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}"}},
                             {"type": "text", "text": "What kind of document is this? One short sentence."}]}]})
    # Assert it recognized a passport — not any personal value.
    assert "passport" in out.lower()


@pytest.mark.skipif(not BB, reason="BROWSERBASE_API_KEY not in .env")
def test_browserbase_session_lifecycle():
    base = "https://api.browserbase.com/v1"
    projs = httpx.get(f"{base}/projects", headers={"X-BB-API-Key": BB}, timeout=30)
    assert projs.status_code == 200
    proj_id = projs.json()[0]["id"]
    sess = httpx.post(f"{base}/sessions", headers={"X-BB-API-Key": BB, "content-type": "application/json"},
                      json={"projectId": proj_id}, timeout=30)
    assert sess.status_code in (200, 201), sess.text[:150]
    sid = sess.json()["id"]
    assert sid
    # Release the session.
    httpx.post(f"{base}/sessions/{sid}", headers={"X-BB-API-Key": BB, "content-type": "application/json"},
               json={"projectId": proj_id, "status": "REQUEST_RELEASE"}, timeout=30)
