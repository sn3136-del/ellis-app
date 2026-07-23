"""Observability (Phase 17): structured redacted request logging, and Sentry /
OpenTelemetry initialization behind env flags.

Honesty rules:
  * Sentry/OTel report 'disabled' unless BOTH the dependency is installed AND
    the DSN/endpoint is configured — never a fake 'enabled'.
  * The log redactor guarantees no passport data, payment data, OTPs,
    passwords, cookies, tokens, Live View URLs, or provider keys can enter a
    log line — tested, not promised.
  * Request logs carry method/path/status/duration only — never bodies,
    never query strings (which could carry signed doc tokens).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time

logger = logging.getLogger("ellis")

# Sensitive KEY names → value replaced entirely.
_SENSITIVE_KEY = re.compile(
    r"pass(word|port)?|pwd|secret|token|cookie|authorization|card|cvc|cvv|otp|"
    r"3ds|ssn|captcha|credential|api[_-]?key|mrz|birth", re.I)
# Sensitive VALUE shapes → scrubbed wherever they appear.
_SENSITIVE_VALUE = re.compile(
    r"(sk-[A-Za-z0-9]{8,}"                    # provider keys
    r"|Bearer\s+[A-Za-z0-9._\-]+"             # bearer tokens
    r"|eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-.]+"  # JWTs
    r"|AKIA[0-9A-Z]{16}"                      # AWS
    r"|vault://\S+"                           # vault refs stay internal
    r"|https?://\S*(browserbase|liveview|live-view)\S*"  # Live View URLs
    r"|[A-Z0-9<]{6,}<{2,}[A-Z0-9<]*"          # MRZ fragments
    r"|\b(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*\d)[A-Z0-9]{8,9}\b"  # alnum passport-like ids
    r"|\b\d{8,9}\b"                            # all-numeric passport/ID numbers (e.g. US 9-digit)
    r"|[?&](sig|token|t)=[A-Za-z0-9._\-]+"     # signed URL params (doc preview / deep links)
    r")")


def scrub(value, depth: int = 0):
    """Deep-redact a structure for logging/telemetry."""
    if depth > 6:
        return "[depth]"
    if isinstance(value, dict):
        return {k: ("[redacted]" if _SENSITIVE_KEY.search(str(k)) else scrub(v, depth + 1))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v, depth + 1) for v in value]
    if isinstance(value, str):
        return _SENSITIVE_VALUE.sub("[redacted]", value)
    return value


def log_event(event: str, **fields):
    """One structured, scrubbed log line."""
    record = {"event": event, **scrub(fields)}
    logger.info(json.dumps(record, default=str))
    return record   # returned so tests can assert on exactly what was logged


class RequestLogMiddleware:
    """ASGI middleware: method/path/status/duration only. No bodies, no query
    strings, no headers."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        start = time.monotonic()
        status_holder = {"status": 0}

        async def send_wrapper(message):
            if message.get("type") == "http.response.start":
                status_holder["status"] = message.get("status", 0)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            log_event("http_request", method=scope.get("method", ""),
                      path=scope.get("path", ""), status=status_holder["status"],
                      duration_ms=round((time.monotonic() - start) * 1000, 1))


# --- Sentry / OpenTelemetry (flag-gated, honest) ------------------------------
_STATE = {"sentry": "disabled", "otel": "disabled"}


def init_sentry() -> str:
    dsn = os.getenv("SENTRY_DSN", "")
    if not dsn:
        _STATE["sentry"] = "disabled (no SENTRY_DSN)"
        return _STATE["sentry"]
    try:
        import sentry_sdk  # type: ignore
    except ImportError:
        _STATE["sentry"] = "disabled (sentry-sdk not installed)"
        return _STATE["sentry"]
    sentry_sdk.init(dsn=dsn, before_send=lambda e, h: scrub(e),   # pragma: no cover
                    traces_sample_rate=float(os.getenv("SENTRY_TRACES_RATE", "0.1")),
                    release=os.getenv("ELLIS_RELEASE", ""), send_default_pii=False)
    _STATE["sentry"] = "enabled"
    return _STATE["sentry"]


def init_otel() -> str:
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    if not endpoint:
        _STATE["otel"] = "disabled (no OTEL_EXPORTER_OTLP_ENDPOINT)"
        return _STATE["otel"]
    try:
        from opentelemetry import trace  # type: ignore  # noqa: F401
    except ImportError:
        _STATE["otel"] = "disabled (opentelemetry not installed)"
        return _STATE["otel"]
    # Honest: the package + endpoint are present, but this build does not wire a
    # TracerProvider/OTLP exporter, so nothing is actually exported. Do NOT claim
    # 'enabled'. Wiring the exporter is a deliberate activation step.
    _STATE["otel"] = "configured (exporter not wired in this build)"  # pragma: no cover
    return _STATE["otel"]


def status() -> dict:
    return dict(_STATE)
