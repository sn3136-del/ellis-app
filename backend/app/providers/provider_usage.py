"""One secret-free usage event per actual provider HTTP attempt.

Only fixed metadata fields, provider identifiers and nonnegative token counts
are recorded. Prompts, responses, credentials, exception messages, URLs and
caller arguments never enter this ledger. Missing usage remains unknown.
"""
from __future__ import annotations

from datetime import datetime, timezone
import atexit
import hashlib
import inspect
import json
import logging
import os
import queue
from pathlib import Path
import re
import sys
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4

log = logging.getLogger("ellis.provider_usage")
_INSTANCE = uuid4().hex
_QUEUE = queue.Queue(maxsize=1024)
_WRITER = None
_START_LOCK = threading.Lock()
_COUNT_LOCK = threading.Lock()
_COUNTERS = {"written_events": 0, "failed_events": 0, "dropped_events": 0}
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}\Z")
_SECRET_SHAPE = re.compile(r"sk-[A-Za-z0-9_-]{12,}|Bearer|eyJ[A-Za-z0-9_-]{12,}", re.I)


def _after_fork():
    # A preloaded API worker inherits no running writer thread. It must not
    # inherit the parent's queued events or locked synchronization objects.
    global _INSTANCE, _QUEUE, _WRITER, _START_LOCK, _COUNT_LOCK, _COUNTERS
    _INSTANCE = uuid4().hex
    _QUEUE = queue.Queue(maxsize=1024)
    _WRITER = None
    _START_LOCK = threading.Lock()
    _COUNT_LOCK = threading.Lock()
    _COUNTERS = {"written_events": 0, "failed_events": 0, "dropped_events": 0}


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


def _identifier(value, secrets=()):
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        return None
    if _SECRET_SHAPE.search(value) or any(secret and secret in value for secret in secrets):
        return None
    return value


def _count(value):
    return value if type(value) is int and 0 <= value <= 10**15 else None


def _usage(payload):
    raw = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens", "cached_tokens"):
        value = _count(raw.get(key))
        if value is not None:
            out[key] = value
    for container, source, target in (
        ("prompt_tokens_details", "cached_tokens", "cached_prompt_tokens"),
        ("completion_tokens_details", "reasoning_tokens", "reasoning_tokens"),
    ):
        details = raw.get(container)
        value = _count(details.get(source)) if isinstance(details, dict) else None
        if value is not None:
            out[target] = value
    return out


def _callers():
    """Static code symbols only: no stack locals, arguments or filesystem paths."""
    callers = []
    frame = inspect.currentframe()
    try:
        frame = frame.f_back
        while frame is not None and len(callers) < 4:
            module = frame.f_globals.get("__name__", "")
            name = frame.f_code.co_name.strip("<>")
            if module.startswith("app.") and module != __name__ and name not in {"_chat", "_chat_vision"}:
                label = _identifier(f"{module}.{name}")
                if label and label not in callers:
                    callers.append(label)
            frame = frame.f_back
    finally:
        del frame
    return callers


def ledger_path():
    explicit = os.environ.get("ELLIS_PROVIDER_USAGE_LOG")
    if explicit:
        return Path(explicit)
    state = Path("/var/lib/ellis")
    if not state.is_dir():
        state = Path(os.environ.get("ELLIS_DATA_DIR") or "data")
    return state / "provider_usage.jsonl"


def _write_event(path, event):
    """Writer thread only: append durably under a process-wide file lock."""
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with _COUNT_LOCK:
        delivery = dict(_COUNTERS)
    line = (json.dumps(dict(event, audit_delivery=delivery), separators=(",", ":"), ensure_ascii=True) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.fchmod(fd, 0o600)
        remaining = memoryview(line)
        while remaining:
            written = os.write(fd, remaining)
            if written <= 0:
                raise OSError("incomplete usage append")
            remaining = remaining[written:]
        os.fsync(fd)
    finally:
        try:
            # A forked child may retain this open-file description. Explicit
            # unlock releases the parent's completed append without waiting
            # for that child to close its inherited descriptor.
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _writer_loop():
    while True:
        path, event = _QUEUE.get()
        try:
            _write_event(path, event)
        except Exception as exc:
            with _COUNT_LOCK:
                _COUNTERS["failed_events"] += 1
            # Neither a path nor an exception message is safe to echo here.
            try:
                log.warning("provider_usage_write_failed error_type=%s", type(exc).__name__)
            except Exception:
                pass
        else:
            with _COUNT_LOCK:
                _COUNTERS["written_events"] += 1
        finally:
            _QUEUE.task_done()


def _emit(event):
    """Never wait for disk, a file lock or available queue capacity in a request."""
    global _WRITER
    if _WRITER is None:
        with _START_LOCK:
            if _WRITER is None:
                _WRITER = threading.Thread(target=_writer_loop, name="provider-usage-writer", daemon=True)
                _WRITER.start()
    try:
        # Resolve the path now; later configuration changes cannot redirect a
        # queued event. The event contains fixed, bounded metadata only.
        _QUEUE.put_nowait((ledger_path(), event))
    except queue.Full:
        with _COUNT_LOCK:
            _COUNTERS["dropped_events"] += 1


def delivery_status():
    """Process-local health counters; no provider data or credentials."""
    with _COUNT_LOCK:
        status = dict(_COUNTERS)
    return dict(status, pending_events=_QUEUE.unfinished_tasks)


def flush(timeout=1.0):
    """Bounded drain for graceful shutdown/tests, never called by HTTP code."""
    deadline = time.monotonic() + max(0, timeout)
    while _QUEUE.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(min(0.005, max(0, deadline - time.monotonic())))
    return not _QUEUE.unfinished_tasks


atexit.register(flush)


def _response_metadata(response, event, secrets):
    status = getattr(response, "status_code", None)
    event["http_status"] = status if type(status) is int else None
    event["outcome"] = ("http_error" if status >= 400 else "http_success") if type(status) is int else "http_response"
    response_headers = getattr(response, "headers", {}) or {}
    for key in ("x-request-id", "request-id", "x-moonshot-request-id"):
        request_id = _identifier(response_headers.get(key), secrets)
        if request_id:
            event["provider_request_id"] = request_id
            break
    try:
        payload = response.json()
    except Exception:
        payload = None
    event["response_json_available"] = isinstance(payload, dict)
    if isinstance(payload, dict):
        event["response_id"] = _identifier(payload.get("id"), secrets)
        event["response_model"] = _identifier(payload.get("model"), secrets)
        event["usage"] = _usage(payload)
        event["usage_available"] = bool(event["usage"])


def post(post_fn, url, *, headers, json: dict, timeout, operation="chat"):
    """HTTP transport wrapper shared by text, translation and vision clients.

    The caller keeps its original response/error handling. Retries each create
    an event because each is a separate provider attempt, possibly billable.
    """
    started = time.monotonic()
    secrets = ()
    event = {
        "event": "provider_usage", "schema_version": 1,
        "attempt_id": uuid4().hex, "started_at": datetime.now(timezone.utc).isoformat(),
        "provider": "moonshot",
        "process_id": os.getpid(), "process_instance": _INSTANCE,
        "http_status": None, "provider_request_id": None, "response_id": None,
        "response_model": None, "usage": {}, "usage_available": False,
    }
    try:
        auth = str(headers.get("authorization") or headers.get("Authorization") or "")
        credential = re.sub(r"^Bearer\s+", "", auth, flags=re.I).strip()
        secrets = (auth, credential)
        event.update({
            "operation": _identifier(operation, secrets) or "unknown",
            "requested_model": _identifier(json.get("model"), secrets),
            "max_output_tokens": _count(json.get("max_tokens")),
            "credential_sha256_16": hashlib.sha256(credential.encode()).hexdigest()[:16] if credential else None,
            "process_name": _identifier(Path(sys.argv[0]).name, secrets),
            "thread_name": _identifier(threading.current_thread().name, secrets),
            "callers": _callers(), "endpoint_host": _identifier(urlsplit(url).hostname, secrets),
        })
    except Exception as exc:
        event["metadata_error_type"] = _identifier(type(exc).__name__)
    try:
        response = post_fn(url, headers=headers, json=json, timeout=timeout)
    except Exception as exc:
        event["outcome"] = "transport_error"
        event["error_type"] = _identifier(type(exc).__name__)
        raise
    else:
        event["outcome"] = "http_response"
        try:
            _response_metadata(response, event, secrets)
        except Exception as exc:
            event["metadata_error_type"] = _identifier(type(exc).__name__)
        return response
    finally:
        try:
            event["duration_ms"] = round((time.monotonic() - started) * 1000, 2)
            _emit(event)
        except Exception:
            pass  # Audit infrastructure must never replace the HTTP result/error.
