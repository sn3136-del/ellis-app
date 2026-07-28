"""Phase 14 (provider error taxonomy, circuit breakers, redacted diagnostics)
and Phase 17 (structured logging redaction, honest Sentry/OTel status)."""
import json

from tests.conftest import AUTH
from app import provider_errors as pe
from app import observability as obs


# ---- Phase 14: classification ----
def test_error_classification_categories():
    cases = {
        "moonshot 429 rate limit exceeded": "kimi_rate_limited",
        "kimi: insufficient_quota for this billing period": "kimi_quota_exhausted",
        "max_tokens reached before completion": "kimi_token_limit",
        "moonshot API returned 401 unauthorized": "kimi_auth_failed",
        "DOCAI_UNAUTHENTICATED": "documentai_auth_failed",
        "docai: 429 RESOURCE_EXHAUSTED quota exceeded": "documentai_quota_exhausted",
        "browserbase: 429 too many concurrent sessions": "browserbase_quota_exhausted",
        "browserbase 401 unauthorized": "browserbase_auth_failed",
        "SMTP connection refused by relay": "email_delivery_failed",
        "temporal: failed to connect to host": "temporal_unavailable",
        "postgres connection refused": "database_unavailable",
        "docker container exited with code 137": "container_stopped",
        "OSError: No space left on device": "disk_exhausted",
        "MemoryError: cannot allocate 2GB": "memory_exhausted",
        "portal maintenance window active": "portal_maintenance",
        "portal request timed out after 30s": "portal_timeout",
        "???": "unknown",
    }
    for raw, want in cases.items():
        assert pe.classify_error(raw) == want, raw


def test_user_error_envelope_is_safe_and_complete():
    env = pe.user_error("moonshot 401 unauthorized: invalid api key sk-abcdef123456789")
    assert env["category"] == "kimi_auth_failed"
    assert env["data_preserved"] is True
    assert env["retry_available"] is False and env["manual_review_available"] is True
    assert env["admin_alert"] is True
    # The raw key never survives into the envelope.
    assert "sk-abcdef123456789" not in json.dumps(env)
    # Every catalog entry has all required fields and a non-empty safe message.
    for cat, entry in pe.CATALOG.items():
        for key in ("user_message", "data_preserved", "retry", "manual_review", "provider_status"):
            assert key in entry, f"{cat}.{key}"
        assert len(entry["user_message"]) > 20


def test_redact_diagnostic_scrubs_secrets_and_urls():
    raw = ("failed: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig at "
           "https://api.moonshot.ai/v1 with api_key=sk-supersecret12345678")
    out = pe.redact_diagnostic(raw)
    assert "sk-supersecret" not in out and "eyJ" not in out
    assert "moonshot.ai" not in out            # URLs collapsed


# ---- Phase 14: circuit breaker ----
def test_circuit_breaker_opens_half_opens_closes():
    clock = {"t": 0.0}
    b = pe.CircuitBreaker("kimi", threshold=3, cooldown_seconds=10,
                          retry_budget=100, now=lambda: clock["t"])
    assert b.state == "closed" and b.allow()
    for _ in range(3):
        b.record_failure()
    assert b.state == "open" and not b.allow()
    clock["t"] = 11.0
    assert b.state == "half_open" and b.allow()   # one probe allowed
    b.record_success()
    assert b.state == "closed"


def test_circuit_breaker_retry_budget():
    clock = {"t": 0.0}
    b = pe.CircuitBreaker("docai", threshold=99, retry_budget=3,
                          budget_window_seconds=100, now=lambda: clock["t"])
    assert b.allow() and b.allow() and b.allow()
    assert not b.allow()                       # budget exhausted
    clock["t"] = 200.0                          # window rolls over
    assert b.allow()


def test_backoff_is_exponential_and_capped():
    assert pe.backoff_seconds(1) == 1.0
    assert pe.backoff_seconds(3) == 4.0
    assert pe.backoff_seconds(10, cap=60.0) == 60.0


def test_diagnostics_endpoint_no_secrets(client):
    pe.reset_breakers()
    pe.breaker("kimi").record_failure()
    r = client.get("/diagnostics/providers", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["breakers"][0]["name"] == "kimi"
    assert "observability" in body and "kill_switches" in body
    assert "sk-" not in json.dumps(body)


# ---- Phase 17: redaction ----
def test_scrub_redacts_sensitive_keys_and_values():
    record = obs.scrub({
        "passport_number": "L898902C3",
        "authorization": "Bearer abc.def.ghi",
        "otp": "123456",
        "note": "visit https://liveview.browserbase.com/session/xyz now",
        "mrz": "P<UTOERIKSSON<<ANNA<<<<",
        "nested": {"card": "4111111111111111", "ok": "fine"},
        "identifier_in_text": "passport E12345678 attached",
        "harmless": "hello world",
    })
    blob = json.dumps(record)
    for leaked in ("L898902C3", "Bearer abc", "123456", "browserbase", "4111",
                   "E12345678", "ERIKSSON"):
        assert leaked not in blob, leaked
    assert record["harmless"] == "hello world"
    assert record["nested"]["ok"] == "fine"


def test_log_event_returns_scrubbed_record():
    rec = obs.log_event("test", path="/cases/1", token="sk-secret1234567890", status=200)
    assert rec["token"] == "[redacted]"
    assert rec["status"] == 200


def test_sentry_otel_honestly_disabled(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert obs.init_sentry().startswith("disabled")
    assert obs.init_otel().startswith("disabled")
    st = obs.status()
    assert st["sentry"].startswith("disabled") and st["otel"].startswith("disabled")


def test_scrub_redacts_all_numeric_passport_and_signed_params_regression():
    # REGRESSION (review-confirmed): all-numeric passport numbers + signed URL
    # params must be scrubbed from logs/telemetry.
    rec = obs.scrub({"msg": "passport 123456789 at /documents/x/content?exp=1&sig=deadbeefcafe"})
    blob = json.dumps(rec)
    assert "123456789" not in blob
    assert "sig=deadbeefcafe" not in blob


def test_otel_never_claims_enabled_without_exporter_regression(monkeypatch):
    # REGRESSION: init_otel must not report 'enabled' when no exporter is wired.
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4317")
    st = obs.init_otel()
    assert st != "enabled"
    assert st.startswith("disabled") or st.startswith("configured")


def test_request_log_carries_no_query_string(client):
    # The middleware logs method/path/status only — exercise a request and make
    # sure a signed query param never reaches the structured record.
    rec = obs.log_event("http_request", method="GET",
                        path="/documents/x/content", status=200, duration_ms=1.0)
    assert "sig" not in json.dumps(rec)


def test_a_portal_outage_never_reads_as_a_problem_with_the_application():
    """When the GOVERNMENT site is down (502/refused connection), the applicant
    is told the portal is unavailable — not that their application "needs a
    closer look". Regression 2026-07-28: evisa.gov.vn returned HTTP 502 and
    Ellis reported it as a case problem."""
    from app import progress

    # The driver classifies a server error as an outage, not a generic failure.
    class _Resp:
        status = 502

    class _Page:
        url = "https://evisa.gov.vn/"

        def goto(self, *_a, **_kw):
            return _Resp()

    from app.adapter_factory.live_driver import BrowserbasePageDriver
    d = BrowserbasePageDriver(_Page(), allowed_hostnames=["evisa.gov.vn"])
    res = d.goto("https://evisa.gov.vn/")
    assert res["ok"] is False
    assert res["code"] == "PORTAL_UNAVAILABLE"

    # And that cause survives into the applicant-facing message, both while
    # the failure is still recoverable and after it escalates.
    outage = "open_portal: PORTAL_UNAVAILABLE (the official portal returned HTTP 502)"
    for state in ("RECOVERABLE_FAILURE", "MANUAL_REVIEW_REQUIRED"):
        step = progress.step_for_state(state, None, outage)
        assert step == "portal_unavailable"
        msg = progress.STEP_MESSAGES[step]
        assert "temporarily unavailable" in msg
        assert "closer look" not in msg

    # A genuine application problem still says so.
    assert progress.step_for_state(
        "MANUAL_REVIEW_REQUIRED", None, "fee exceeds authorized maximum") == "manual_review"
