"""Environment-driven settings and capability flags.

A missing credential disables only its own integration; the rest of the service
runs. `capabilities()` reports what is live vs. running on a local fallback.
"""
from __future__ import annotations

import os
from functools import lru_cache


def _load_dotenv(path):
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    except FileNotFoundError:
        pass


_load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
_load_dotenv(".env")


def _bool(key: str, default: bool = False) -> bool:
    v = os.getenv(key)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


RUNTIME_MODES = ("test", "local_mock_demo", "tripcom_evaluation", "staging", "production")
# Modes in which MockPortal may be constructed at all.
MOCK_ALLOWED_MODES = ("test", "local_mock_demo")
# Modes with the absolute real-only execution boundary (never MockPortal,
# never invented fees/appointments/confirmations — fail closed instead).
REAL_ONLY_MODES = ("tripcom_evaluation", "staging", "production")


def _derive_runtime_mode(env: str) -> str:
    """ELLIS_RUNTIME_MODE wins; otherwise derive from ELLIS_ENV. Any invalid or
    unknown value fails CLOSED to 'production' (real-only, no mocks)."""
    raw = os.getenv("ELLIS_RUNTIME_MODE", "").strip().lower()
    if raw:
        return raw if raw in RUNTIME_MODES else "production"
    return {"test": "test", "development": "local_mock_demo",
            "staging": "staging", "production": "production"}.get(env, "production")


class Settings:
    # Read env in __init__ (NOT as class attributes) so a cache_clear() +
    # re-instantiation picks up any changed environment — config is reloadable.
    def __init__(self):
        self.app_name = "ellis-visa-backend"
        self.env = os.getenv("ELLIS_ENV", "development")
        # Explicit runtime mode (brief section 3). Fail-closed: unknown → production.
        self.runtime_mode = _derive_runtime_mode(self.env)
        raw_mode = os.getenv("ELLIS_RUNTIME_MODE", "").strip().lower()
        self.runtime_mode_invalid = bool(raw_mode) and raw_mode not in RUNTIME_MODES
        self.mock_portal_allowed = self.runtime_mode in MOCK_ALLOWED_MODES
        self.real_only_mode = self.runtime_mode in REAL_ONLY_MODES
        # Production mode hardens the "never present a mock/sandbox result as a
        # real government outcome" guard (execution.assert_not_mock_in_production).
        self.production_mode = self.env in ("production", "staging") or self.real_only_mode
        # SQLite for local/dev/test; set DATABASE_URL to a Postgres/Neon DSN in prod.
        self.database_url = os.getenv("DATABASE_URL", "sqlite:///./ellis.db")

        # --- Auth ---
        self.dev_api_token = os.getenv("ELLIS_DEV_TOKEN", "dev-token")
        self.admin_token = os.getenv("ELLIS_ADMIN_TOKEN", "admin-token")  # dev admin role
        self.clerk_secret_key = os.getenv("CLERK_SECRET_KEY", "")
        self.action_token_secret = os.getenv("ELLIS_ACTION_SECRET", "local-action-secret")

        # --- Secrets vault ---
        self.vault_passphrase = os.getenv("ELLIS_VAULT_PASSPHRASE", "local-dev-passphrase")
        self.aws_secrets_prefix = os.getenv("AWS_SECRETS_PREFIX", "")

        # --- Kimi K3 ---
        self.moonshot_api_key = os.getenv("MOONSHOT_API_KEY", "")
        self.kimi_base_url = os.getenv("KIMI_BASE_URL", "https://api.moonshot.ai/v1")
        self.kimi_model = os.getenv("KIMI_MODEL", "kimi-k3")
        self.kimi_enabled = _bool("KIMI_ENABLED", True)
        self.kimi_max_agent_steps = int(os.getenv("KIMI_MAX_AGENT_STEPS", "30"))
        self.kimi_timeout_seconds = int(os.getenv("KIMI_TIMEOUT_SECONDS", "120"))

        # --- OCR (Google Document AI) ---
        self.google_cloud_project = os.getenv("GOOGLE_CLOUD_PROJECT", "")
        self.document_ai_location = os.getenv("GOOGLE_DOCUMENT_AI_LOCATION", "us")
        self.document_ai_ocr_processor = os.getenv("GOOGLE_DOCUMENT_AI_OCR_PROCESSOR_ID", "")
        self.document_ai_form_processor = os.getenv("GOOGLE_DOCUMENT_AI_FORM_PROCESSOR_ID", "")
        self.document_ai_enabled = _bool("DOCUMENT_AI_ENABLED", True)
        self.gcp_quota_project = os.getenv("GOOGLE_CLOUD_QUOTA_PROJECT", "")
        # Kimi K3 vision is a feature-flagged fallback when Document AI has a
        # recoverable failure. Off by default in prod so a persistent Google
        # outage is visible rather than silently masked.
        self.kimi_ocr_fallback = _bool("ENABLE_KIMI_OCR_FALLBACK", False)

        # --- Browserbase ---
        self.browserbase_api_key = os.getenv("BROWSERBASE_API_KEY", "")
        self.browserbase_project_id = os.getenv("BROWSERBASE_PROJECT_ID", "")

        # --- Stripe Issuing ---
        self.stripe_secret_key = os.getenv("STRIPE_SECRET_KEY", "")
        self.stripe_webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
        self.issuing_approved = _bool("ELLIS_ISSUING_APPROVED", False)

        # --- DocuSign ---
        self.docusign_integration_key = os.getenv("DOCUSIGN_INTEGRATION_KEY", "")
        self.docusign_account_id = os.getenv("DOCUSIGN_ACCOUNT_ID", "")
        self.docusign_hmac_secret = os.getenv("DOCUSIGN_HMAC_SECRET", "")

        # --- Storage (S3 + KMS) ---
        self.s3_bucket = os.getenv("S3_BUCKET", "")
        self.kms_key_id = os.getenv("KMS_KEY_ID", "")

        # --- Temporal ---
        self.temporal_host = os.getenv("TEMPORAL_HOST", "")  # empty → DB workflow runner

        # --- App base URL (for signed email deep links) ---
        self.app_base_url = os.getenv("ELLIS_APP_BASE_URL", "")


@lru_cache
def settings() -> Settings:
    return Settings()


def killswitches() -> dict:
    """Operator kill switches (env-driven). When a provider or adapter switch is
    on, the API refuses to use it and falls back to the safe local path. These
    let an operator disable a provider/adapter instantly without a redeploy."""
    def _on(name: str) -> bool:
        return os.getenv(name, "0").lower() in ("1", "true", "yes", "on")
    return {
        "kimi": _on("ELLIS_KILL_KIMI"),
        "document_ai": _on("ELLIS_KILL_DOCUMENT_AI"),
        "browserbase": _on("ELLIS_KILL_BROWSERBASE"),
        "stripe": _on("ELLIS_KILL_STRIPE"),
        "adapters": [a.strip() for a in os.getenv("ELLIS_KILL_ADAPTERS", "").split(",") if a.strip()],
        "all_submissions": _on("ELLIS_KILL_SUBMISSIONS"),
    }


def capabilities() -> dict:
    s = settings()
    ks = killswitches()
    return {
        "auth": "clerk" if s.clerk_secret_key else "dev_token",
        "database": "postgres" if s.database_url.startswith("postgres") else "sqlite",
        "vault": "aws_secrets_manager" if s.aws_secrets_prefix else "local_encrypted",
        "kimi": bool(s.moonshot_api_key) and s.kimi_enabled and not ks["kimi"],
        "ocr": bool(s.google_cloud_project and s.document_ai_ocr_processor) and s.document_ai_enabled and not ks["document_ai"],
        "browserbase": bool(s.browserbase_api_key) and not ks["browserbase"],
        "stripe_issuing": bool(s.stripe_secret_key) and s.issuing_approved and not ks["stripe"],
        "docusign": bool(s.docusign_integration_key and s.docusign_account_id),
        "storage": "s3_kms" if s.s3_bucket else "local_encrypted",
        "workflow_engine": "temporal" if s.temporal_host else "db_runner",
        "fallbacks": {
            "kimi": "live" if (s.moonshot_api_key and s.kimi_enabled) else "local_test_provider",
            "ocr": "live" if (s.google_cloud_project and s.document_ai_ocr_processor) else "local_mrz_provider",
            "payment": "stripe_issuing" if (s.stripe_secret_key and s.issuing_approved) else "applicant_payment_window",
            "handoff": "browserbase_liveview" if s.browserbase_api_key else "local_handoff",
            "authorization": "docusign" if s.docusign_integration_key else "in_app_authorization",
        },
    }
