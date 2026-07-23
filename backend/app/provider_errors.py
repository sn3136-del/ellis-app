"""Provider-quota and infrastructure error handling (Phase 14).

Maps every provider / infrastructure failure to a SAFE, user-facing explanation
plus honest flags: whether data was preserved, whether retry is available,
whether manual review is available, and a provider status category. Raw
provider responses, keys, tokens, or request data are NEVER exposed — only a
redacted technical category reaches diagnostics.

Also provides deterministic circuit breakers with backoff and retry budgets,
and env-driven kill switches (config.killswitches). Docker/host resource issues
(CPU, memory, disk, container health) are handled as infrastructure categories —
Docker has no AI "tokens".
"""
from __future__ import annotations

import re
import time

# category -> (user_message, data_preserved, retry, manual_review, provider_status)
CATALOG: dict[str, dict] = {
    # --- Kimi K3 ---
    "kimi_token_limit": {
        "user_message": "The AI assistant hit its response-size limit for this step. Your data is saved — try again, or continue without AI assistance.",
        "data_preserved": True, "retry": True, "manual_review": True, "provider_status": "degraded"},
    "kimi_quota_exhausted": {
        "user_message": "The AI assistant's usage quota is exhausted. Your case is saved and continues without AI assistance until quota is restored.",
        "data_preserved": True, "retry": False, "manual_review": True, "provider_status": "quota_exhausted"},
    "kimi_rate_limited": {
        "user_message": "The AI assistant is receiving too many requests. Your data is saved — please retry in a moment.",
        "data_preserved": True, "retry": True, "manual_review": True, "provider_status": "rate_limited"},
    "kimi_auth_failed": {
        "user_message": "The AI assistant is temporarily unavailable (configuration issue). Your case is saved; an administrator has been notified.",
        "data_preserved": True, "retry": False, "manual_review": True, "provider_status": "auth_failed"},
    "kimi_unavailable": {
        "user_message": "The AI assistant is temporarily unavailable. Your case is saved and everything else keeps working.",
        "data_preserved": True, "retry": True, "manual_review": True, "provider_status": "unavailable"},
    # --- Google Document AI ---
    "documentai_quota_exhausted": {
        "user_message": "Document reading is temporarily paused (service quota). Your upload is saved and will be processed as soon as capacity returns.",
        "data_preserved": True, "retry": True, "manual_review": True, "provider_status": "quota_exhausted"},
    "documentai_auth_failed": {
        "user_message": "Document reading is temporarily unavailable (configuration issue). Your upload is saved; an administrator has been notified.",
        "data_preserved": True, "retry": False, "manual_review": True, "provider_status": "auth_failed"},
    # --- Browserbase ---
    "browserbase_quota_exhausted": {
        "user_message": "Secure browser sessions are temporarily at capacity. Your case is saved — the portal step will resume when a session is available.",
        "data_preserved": True, "retry": True, "manual_review": True, "provider_status": "quota_exhausted"},
    "browserbase_auth_failed": {
        "user_message": "Secure browser sessions are temporarily unavailable (configuration issue). An administrator has been notified.",
        "data_preserved": True, "retry": False, "manual_review": True, "provider_status": "auth_failed"},
    "browserbase_unavailable": {
        "user_message": "The secure browser service is temporarily unavailable. Your case is saved.",
        "data_preserved": True, "retry": True, "manual_review": True, "provider_status": "unavailable"},
    # --- Email / storage / infra ---
    "email_delivery_failed": {
        "user_message": "We couldn't deliver an email notification. Your case is unaffected; delivery will be retried automatically.",
        "data_preserved": True, "retry": True, "manual_review": False, "provider_status": "degraded"},
    "storage_failure": {
        "user_message": "Document storage had a temporary problem. Your case data is safe; please retry the upload.",
        "data_preserved": True, "retry": True, "manual_review": True, "provider_status": "unavailable"},
    "temporal_unavailable": {
        "user_message": "Case processing is briefly paused (workflow engine restarting). Nothing is lost — your case resumes automatically.",
        "data_preserved": True, "retry": True, "manual_review": False, "provider_status": "unavailable"},
    "database_unavailable": {
        "user_message": "Ellis is briefly unavailable (database connection). Please retry shortly; no submitted data is lost.",
        "data_preserved": True, "retry": True, "manual_review": False, "provider_status": "unavailable"},
    "container_stopped": {
        "user_message": "A background service is restarting. Your case is saved and resumes automatically.",
        "data_preserved": True, "retry": True, "manual_review": False, "provider_status": "unavailable"},
    "disk_exhausted": {
        "user_message": "Ellis is temporarily unable to store new documents (server storage full). An administrator has been alerted; your case data is safe.",
        "data_preserved": True, "retry": False, "manual_review": True, "provider_status": "resource_exhausted"},
    "memory_exhausted": {
        "user_message": "Ellis is under heavy load. Please retry in a moment; your case data is safe.",
        "data_preserved": True, "retry": True, "manual_review": False, "provider_status": "resource_exhausted"},
    "worker_crash": {
        "user_message": "A processing step was interrupted. Nothing was lost — the durable workflow resumes from its last verified state.",
        "data_preserved": True, "retry": True, "manual_review": True, "provider_status": "degraded"},
    # --- Portal ---
    "portal_timeout": {
        "user_message": "The official portal is responding slowly. Ellis will re-check the portal's actual state before retrying anything — no step is ever duplicated.",
        "data_preserved": True, "retry": True, "manual_review": True, "provider_status": "degraded"},
    "portal_maintenance": {
        "user_message": "The official portal is under maintenance. Your case is saved and resumes when the portal returns.",
        "data_preserved": True, "retry": True, "manual_review": False, "provider_status": "maintenance"},
    "portal_rate_limited": {
        "user_message": "The official portal is limiting requests. Ellis is waiting respectfully and will continue automatically.",
        "data_preserved": True, "retry": True, "manual_review": False, "provider_status": "rate_limited"},
    "unknown": {
        "user_message": "Something unexpected happened. Your case data is saved; you can retry or ask for a manual review.",
        "data_preserved": True, "retry": True, "manual_review": True, "provider_status": "unknown"},
}

# Deterministic classification patterns applied to a provider/infra error string.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("kimi_token_limit", re.compile(r"max[_ ]?tokens|token limit|context[_ ]length", re.I)),
    ("kimi_quota_exhausted", re.compile(r"(kimi|moonshot).*(quota|billing|insufficient)|insufficient_quota", re.I)),
    ("kimi_rate_limited", re.compile(r"(kimi|moonshot).*(429|rate)|rate[_ ]?limit.*(kimi|moonshot)", re.I)),
    ("kimi_auth_failed", re.compile(r"(kimi|moonshot).*(401|403|unauthorized|invalid api key)", re.I)),
    ("kimi_unavailable", re.compile(r"(kimi|moonshot).*(unavailable|timeout|connection|5\d\d)", re.I)),
    ("documentai_quota_exhausted", re.compile(r"docai.*(429|quota|resource_exhausted)|documentai.*quota", re.I)),
    ("documentai_auth_failed", re.compile(r"docai.*(401|403|unauthenticated)|DOCAI_UNAUTHENTICATED", re.I)),
    ("browserbase_quota_exhausted", re.compile(r"browserbase.*(quota|429|concurrent)", re.I)),
    ("browserbase_auth_failed", re.compile(r"browserbase.*(401|403|unauthorized)", re.I)),
    ("browserbase_unavailable", re.compile(r"browserbase.*(unavailable|timeout|5\d\d|connection)", re.I)),
    ("email_delivery_failed", re.compile(r"smtp|mail.*(bounce|fail|reject)", re.I)),
    ("storage_failure", re.compile(r"(s3|gcs|storage).*(fail|error|denied)", re.I)),
    ("temporal_unavailable", re.compile(r"temporal.*(unavailable|connect|timeout)", re.I)),
    ("database_unavailable", re.compile(r"(postgres|database|sqlalchemy).*(connect|unavailable|refused|operational)", re.I)),
    ("container_stopped", re.compile(r"container.*(stop|exit|dead)|docker.*(stop|exit)", re.I)),
    ("disk_exhausted", re.compile(r"no space left|disk (full|quota)|ENOSPC", re.I)),
    ("memory_exhausted", re.compile(r"out of memory|OOM|MemoryError|cannot allocate", re.I)),
    ("worker_crash", re.compile(r"worker.*(crash|kill|died)", re.I)),
    ("portal_maintenance", re.compile(r"MAINTENANCE|portal.*maintenance", re.I)),
    ("portal_rate_limited", re.compile(r"portal.*(429|rate)", re.I)),
    ("portal_timeout", re.compile(r"portal.*(timeout|timed out)", re.I)),
]

_SECRET_PAT = re.compile(
    r"(sk-[A-Za-z0-9]{8,}|Bearer\s+\S+|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_\-]{10,}|"
    r"api[_-]?key\s*[:=]\s*\S+|password\s*[:=]\s*\S+)", re.I)


def redact_diagnostic(raw: str, limit: int = 120) -> str:
    """A short technical category safe for diagnostics: secrets scrubbed, then
    truncated. Never the raw provider response."""
    scrubbed = _SECRET_PAT.sub("[redacted]", raw or "")
    scrubbed = re.sub(r"https?://\S+", "[url]", scrubbed)
    return scrubbed[:limit]


def classify_error(raw: str) -> str:
    for category, pat in _PATTERNS:
        if pat.search(raw or ""):
            return category
    return "unknown"


def user_error(raw: str, *, category: str | None = None) -> dict:
    """The complete Phase 14 error envelope for one failure."""
    cat = category or classify_error(raw)
    entry = CATALOG.get(cat, CATALOG["unknown"])
    return {
        "category": cat,
        "user_message": entry["user_message"],
        "data_preserved": entry["data_preserved"],
        "retry_available": entry["retry"],
        "manual_review_available": entry["manual_review"],
        "provider_status": entry["provider_status"],
        "technical": redact_diagnostic(raw),
        "admin_alert": entry["provider_status"] in
                       ("auth_failed", "quota_exhausted", "resource_exhausted"),
    }


class CircuitBreaker:
    """Deterministic circuit breaker: opens after `threshold` consecutive
    failures, half-opens after `cooldown_seconds`, closes on a success. A
    retry budget caps attempts within a window so a flapping provider can't
    consume unbounded retries. The clock is injectable for tests."""

    def __init__(self, name: str, *, threshold: int = 5, cooldown_seconds: float = 60.0,
                 retry_budget: int = 20, budget_window_seconds: float = 300.0, now=None):
        self.name = name
        self.threshold = threshold
        self.cooldown = cooldown_seconds
        self.retry_budget = retry_budget
        self.budget_window = budget_window_seconds
        self._now = now or time.monotonic
        self._failures = 0
        self._opened_at: float | None = None
        self._attempts: list[float] = []

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        if self._now() - self._opened_at >= self.cooldown:
            return "half_open"
        return "open"

    def allow(self) -> bool:
        # Budget check first: prune attempts outside the window.
        t = self._now()
        self._attempts = [a for a in self._attempts if t - a < self.budget_window]
        if len(self._attempts) >= self.retry_budget:
            return False
        if self.state == "open":
            return False
        self._attempts.append(t)
        return True

    def record_success(self):
        self._failures = 0
        self._opened_at = None

    def record_failure(self):
        self._failures += 1
        if self._failures >= self.threshold:
            self._opened_at = self._now()

    def snapshot(self) -> dict:
        return {"name": self.name, "state": self.state, "failures": self._failures,
                "budget_remaining": max(0, self.retry_budget - len(self._attempts))}


def backoff_seconds(attempt: int, *, base: float = 1.0, cap: float = 60.0) -> float:
    """Deterministic exponential backoff (no jitter here so it's testable; the
    caller may add jitter)."""
    return min(cap, base * (2 ** max(0, attempt - 1)))


# Process-wide breakers per provider (the API/worker share one registry).
_BREAKERS: dict[str, CircuitBreaker] = {}


def breaker(name: str) -> CircuitBreaker:
    if name not in _BREAKERS:
        _BREAKERS[name] = CircuitBreaker(name)
    return _BREAKERS[name]


def breakers_snapshot() -> list[dict]:
    return [b.snapshot() for b in _BREAKERS.values()]


def reset_breakers():
    _BREAKERS.clear()
