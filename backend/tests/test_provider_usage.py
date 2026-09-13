"""Real provider transport boundary with fake HTTP only; no billable calls."""
import json
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
from types import SimpleNamespace

import httpx
import pytest

from app.providers import kimi, kimi_vision, provider_usage as usage

SECRET = "sk-test-private-provider-credential-never-recorded"
PROMPT = "private document and applicant text never recorded"


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "usage.jsonl"
    monkeypatch.setenv("ELLIS_PROVIDER_USAGE_LOG", str(path))
    cfg = SimpleNamespace(moonshot_api_key=SECRET, kimi_base_url="https://api.moonshot.ai/v1",
        kimi_model="configured-model", kimi_timeout_seconds=2, kimi_enabled=True)
    monkeypatch.setattr(kimi, "settings", lambda: cfg)
    monkeypatch.setattr(kimi_vision, "settings", lambda: cfg)
    yield path
    assert usage.flush(2), "usage writer did not drain"


def response(status=200, **payload):
    body = {"id": "chatcmpl-test-response", "model": "resolved-provider-model",
        "choices": [{"message": {"content": '{"answer":"ok"}'}}], **payload}
    return httpx.Response(status, json=body, headers={"x-request-id": "request-test-123"},
        request=httpx.Request("POST", "https://api.moonshot.ai/v1/chat/completions"))


def events(path):
    assert usage.flush(2), "usage writer did not drain"
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_text_and_translation_share_one_redacted_event_per_http_call(ledger, monkeypatch):
    captured = []
    def fake_post(url, **kwargs):
        captured.append(kwargs)
        return response(usage={"prompt_tokens": 123, "completion_tokens": 45, "total_tokens": 168, "cached_tokens": 79,
            "prompt_tokens_details": {"cached_tokens": 80, "secret": SECRET},
            "completion_tokens_details": {"reasoning_tokens": 20}, "unexpected": PROMPT},
            choices=[{"message": {"content": json.dumps({"answer": PROMPT, "private": SECRET})}}])
    monkeypatch.setattr(httpx, "post", fake_post)
    provider = kimi.LiveKimiProvider()
    out = provider._chat(PROMPT, SECRET, model="code-highspeed", max_tokens=6000)
    assert out["answer"] == PROMPT
    provider.translate_batch({"item": PROMPT}, "zh-CN", "en", model="translation-model")
    rows = events(ledger)
    assert len(rows) == len(captured) == 2
    assert rows[0]["requested_model"] == "code-highspeed"
    assert rows[0]["response_model"] == "resolved-provider-model"
    assert rows[0]["provider_request_id"] == "request-test-123"
    assert rows[0]["usage"] == {"prompt_tokens": 123, "completion_tokens": 45,
        "total_tokens": 168, "cached_tokens": 79, "cached_prompt_tokens": 80, "reasoning_tokens": 20}
    assert rows[0]["credential_sha256_16"] == hashlib.sha256(SECRET.encode()).hexdigest()[:16]
    assert rows[1]["requested_model"] == "translation-model"
    assert "app.providers.kimi.translate_batch" in rows[1]["callers"]
    assert rows[0]["process_id"] == os.getpid()
    assert rows[0]["process_instance"] == rows[1]["process_instance"]
    assert rows[0]["attempt_id"] != rows[1]["attempt_id"]
    assert SECRET not in ledger.read_text() and PROMPT not in ledger.read_text()
    assert "messages" not in ledger.read_text() and "authorization" not in ledger.read_text()
    assert stat.S_IMODE(ledger.stat().st_mode) == 0o600


def test_http_error_and_timeout_are_audited_without_exception_or_error_body(ledger, monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: response(429, error={"message": SECRET + PROMPT}))
    with pytest.raises(kimi.KimiHttpError):
        kimi.LiveKimiProvider()._chat(PROMPT, SECRET)
    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout(SECRET + PROMPT)
    monkeypatch.setattr(httpx, "post", timeout)
    with pytest.raises(kimi.KimiTimeout):
        kimi.LiveKimiProvider()._chat(PROMPT, SECRET)
    first, second = events(ledger)
    assert first["outcome"] == "http_error" and first["http_status"] == 429
    assert first["usage_available"] is False and first["usage"] == {}
    assert second["outcome"] == "transport_error" and second["error_type"] == "ReadTimeout"
    assert second["http_status"] is None
    assert SECRET not in ledger.read_text() and PROMPT not in ledger.read_text()


def test_vision_uses_same_ledger_without_image_or_prompt(ledger, monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: response(usage={"total_tokens": 31}))
    assert kimi_vision._chat_vision("private-image-base64", "image/png", PROMPT)
    row, = events(ledger)
    assert row["operation"] == "vision" and row["usage"] == {"total_tokens": 31}
    assert "private-image-base64" not in ledger.read_text() and PROMPT not in ledger.read_text()


def test_freshness_thread_and_actual_retry_attempts_remain_attributable(ledger, monkeypatch):
    from app.visa_snapshot import freshness, kimi_primary
    monkeypatch.setattr(kimi_primary, "settings", kimi.settings)
    monkeypatch.setattr(freshness, "_PROVIDER", None)
    monkeypatch.setenv("KIMI_GUIDANCE_MODEL", "code-highspeed")
    replies = iter([response(429), response(usage={"total_tokens": 10},
        choices=[{"finish_reason": "stop", "message": {"content": '{"answer":"ok"}'}}])])
    monkeypatch.setattr(httpx, "post", lambda *a, **k: next(replies))
    assert freshness._call(PROMPT, SECRET, timeout_seconds=3) == {"answer": "ok"}
    first, second = events(ledger)
    assert first["http_status"] == 429 and second["http_status"] == 200
    assert all(row["requested_model"] == "code-highspeed" for row in (first, second))
    assert all("app.visa_snapshot.freshness.lambda" in row["callers"] for row in (first, second))
    assert SECRET not in ledger.read_text() and PROMPT not in ledger.read_text()


def test_unknown_malformed_usage_is_not_zero_or_logged_verbatim(ledger, monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: response(model=SECRET, id=PROMPT,
        usage={"prompt_tokens": True, "completion_tokens": -1, "total_tokens": "900",
            "input_tokens": 12.5, "output_tokens": 0,
            "completion_tokens_details": {"reasoning_tokens": SECRET}}))
    kimi.LiveKimiProvider()._chat("test", "test")
    row, = events(ledger)
    assert row["usage"] == {"output_tokens": 0}
    assert row["response_model"] is None and row["response_id"] is None
    assert SECRET not in ledger.read_text() and PROMPT not in ledger.read_text()


def test_usage_write_failure_does_not_replace_valid_provider_result(ledger, monkeypatch, caplog):
    ledger.mkdir()
    monkeypatch.setattr(httpx, "post", lambda *a, **k: response())
    assert kimi.LiveKimiProvider()._chat(PROMPT, SECRET) == {"answer": "ok"}
    assert usage.flush(2)
    assert "provider_usage_write_failed" in caplog.text
    assert SECRET not in caplog.text and PROMPT not in caplog.text


@pytest.mark.parametrize("failure", ["headers", "json", "sink"])
def test_metadata_failures_preserve_response_and_original_transport_exception(ledger, monkeypatch, failure):
    class BadResponse:
        status_code = 200
        @property
        def headers(self):
            if failure == "headers":
                raise RuntimeError(SECRET)
            return {}
        def json(self):
            raise ValueError(PROMPT)
    result = BadResponse()
    if failure == "sink":
        monkeypatch.setattr(usage, "_emit", lambda event: (_ for _ in ()).throw(OSError(SECRET)))
    kwargs = dict(headers={"authorization": "Bearer " + SECRET}, json={"model": "test"}, timeout=1)
    assert usage.post(lambda *a, **k: result, "https://api.moonshot.ai/v1", **kwargs) is result
    original = httpx.ReadTimeout(SECRET)
    def fail(*a, **k):
        raise original
    with pytest.raises(httpx.ReadTimeout) as raised:
        usage.post(fail, "https://api.moonshot.ai/v1", **kwargs)
    assert raised.value is original
    if failure != "sink":
        first, second = events(ledger)
        assert first["outcome"] == "http_success"
        assert second["outcome"] == "transport_error"
        assert SECRET not in ledger.read_text() and PROMPT not in ledger.read_text()


def test_api_and_worker_processes_append_complete_distinct_events(ledger):
    script = '''from app.providers import provider_usage as usage
class Response:
    status_code=200
    headers={"x-request-id":"concurrent-test"}
    def json(self): return {"usage":{"total_tokens":2}}
for _ in range(20):
    usage.post(lambda *a,**k:Response(),"https://api.moonshot.ai/v1",headers={},json={"model":"test"},timeout=1)
'''
    backend = str(Path(__file__).resolve().parents[1])
    env = dict(os.environ, PYTHONPATH=backend)
    children = [subprocess.Popen([sys.executable, "-c", script], cwd=backend, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(3)]
    for child in children:
        _, errors = child.communicate(timeout=15)
        assert child.returncode == 0, errors.decode()
    rows = events(ledger)
    assert len(rows) == len({r["attempt_id"] for r in rows}) == 60
    assert len({r["process_id"] for r in rows}) == len({r["process_instance"] for r in rows}) == 3
    assert all(row["usage"] == {"total_tokens": 2} for row in rows)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="fork-only descriptor ownership regression")
def test_child_fork_cannot_retain_a_completed_parent_append_lock(ledger):
    script = '''import os, threading
from app.providers import provider_usage as usage
entered, release = threading.Event(), threading.Event()
original = os.fsync
def pause(fd):
    entered.set()
    assert release.wait(5)
    original(fd)
os.fsync = pause
def emit():
    usage.post(lambda *a,**k:object(), "https://api.moonshot.ai/v1", headers={}, json={"model":"test"},timeout=.05)
emit()
assert entered.wait(2)
reader, writer = os.pipe()
child = os.fork()
if child == 0:
    os.close(writer)
    os.read(reader,1)
    os._exit(0)
os.close(reader)
try:
    release.set()
    assert usage.flush(2)
    emit()
    completed_while_child_alive = usage.flush(.5)
finally:
    os.write(writer,b"x")
    os.close(writer)
    os.waitpid(child,0)
    os.fsync = original
assert completed_while_child_alive
'''
    backend = str(Path(__file__).resolve().parents[1])
    subprocess.run([sys.executable, "-c", script], cwd=backend,
        env=dict(os.environ, PYTHONPATH=backend), capture_output=True, text=True, check=True, timeout=15)
    assert len(events(ledger)) == 2


def test_busy_file_lock_cannot_delay_a_provider_response(ledger):
    import fcntl
    with ledger.open("w") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX)
        started = time.monotonic()
        result = object()
        got = usage.post(lambda *a, **k: result, "https://api.moonshot.ai/v1",
            headers={}, json={"model": "test"}, timeout=0.05)
        elapsed = time.monotonic() - started
        assert got is result and elapsed < 0.2
        assert usage.flush(0.05) is False
        fcntl.flock(owner, fcntl.LOCK_UN)
    row, = events(ledger)
    assert row["outcome"] == "http_response"


def test_full_queue_records_drops_without_waiting_for_slow_sink(ledger, monkeypatch):
    import threading
    release = threading.Event()
    entered = threading.Event()
    original = usage._write_event
    def slow(path, event):
        entered.set()
        assert release.wait(5)
        original(path, event)
    monkeypatch.setattr(usage, "_write_event", slow)
    before = usage.delivery_status()["dropped_events"]
    kwargs = dict(headers={}, json={"model": "test"}, timeout=0.05)
    usage.post(lambda *a, **k: object(), "https://api.moonshot.ai/v1", **kwargs)
    assert entered.wait(1)
    try:
        for _ in range(usage._QUEUE.maxsize + 2):
            usage.post(lambda *a, **k: object(), "https://api.moonshot.ai/v1", **kwargs)
        assert usage.delivery_status()["dropped_events"] == before + 2
    finally:
        release.set()
    assert usage.flush(5)
    assert events(ledger)[-1]["audit_delivery"]["dropped_events"] == before + 2
