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


# local_real_services: a single-user LOCAL desktop deployment that talks to
# REAL providers (Document AI, Kimi, Browserbase) against a local SQLite DB and
# dev-token auth — but with the absolute real-only execution boundary. It is the
# default for the self-contained packaged app: never a mock/synthetic portal,
# never invented fees/appointments/confirmations; fail closed instead.
RUNTIME_MODES = ("test", "local_mock_demo", "local_real_services",
                 "tripcom_evaluation", "staging", "production")
# Modes in which MockPortal may be constructed at all.
MOCK_ALLOWED_MODES = ("test", "local_mock_demo")
# Modes with the absolute real-only execution boundary (never MockPortal,
# never invented fees/appointments/confirmations — fail closed instead).
REAL_ONLY_MODES = ("local_real_services", "tripcom_evaluation", "staging", "production")


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
        # Residential proxying for portals that refuse datacenter egress
        # (Germany's RK-Termin among them). On by default: a plan without it
        # falls back to a plain session rather than failing.
        self.browserbase_proxies = os.getenv(
            "BROWSERBASE_PROXIES", "true").strip().lower() not in ("0", "false", "no")
        # Minimum quiet period between recon passes against the SAME
        # government host. A portal's willingness to serve Ellis is a shared,
        # exhaustible resource: six rebuild passes against one Thai host in an
        # afternoon put it behind a challenge that then met real applicants.
        # Set to 0 only where the "portal" is a fixture (the test suite does).
        self.recon_host_quiet_seconds = int(
            os.getenv("ELLIS_RECON_HOST_QUIET_SECONDS", str(15 * 60)))

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

        # --- USCIS Torch (case status, read-only) ---
        # developer.uscis.gov publishes a Case Status API and a FOIA API and
        # NOTHING that files. Credentials here buy tracking of receipts Ellis
        # already filed elsewhere; there is no submit path to configure.
        self.uscis_torch_base_url = os.getenv("USCIS_TORCH_BASE_URL",
                                              "https://api.uscis.gov")
        self.uscis_torch_client_id = os.getenv("USCIS_TORCH_CLIENT_ID", "")
        self.uscis_torch_client_secret = os.getenv("USCIS_TORCH_CLIENT_SECRET", "")
        self.uscis_torch_access_token = os.getenv("USCIS_TORCH_ACCESS_TOKEN", "")
        self.uscis_torch_enabled = _bool("USCIS_TORCH_ENABLED", True)

        # --- O*NET Web Services v2 (occupation search) ---
        # Free registration at onetcenter.org/developer; sent as X-API-Key.
        # Unset → the occupation provider degrades honestly (no classification
        # is ever fabricated). NIOCCS and NAICS validation need no key.
        self.onet_api_key = os.getenv("ONET_API_KEY", "")

        # --- Browser session provider (Browserbase | Steel) ---
        # Steel is an Apache-2.0 second source with the same CDP shape, self
        # hostable. CAPTCHA solving and stealth stay OFF on every Ellis session
        # regardless of provider: a government filing needs a clean,
        # attributable session, and solving a CAPTCHA for an applicant is a
        # personal act Ellis never performs.
        self.browser_provider = os.getenv("ELLIS_BROWSER_PROVIDER", "browserbase")
        self.steel_api_key = os.getenv("STEEL_API_KEY", "")
        self.steel_base_url = os.getenv("STEEL_BASE_URL", "")  # self-host origin

        # --- Official US government reference feeds (no key, no vendor) ---
        # Federal Register API v1: rule changes that move fees/windows/editions.
        # USCIS H-1B Employer Data Hub: an employer's own filing history.
        self.federal_register_enabled = os.getenv(
            "ELLIS_FEDERAL_REGISTER", "on").strip().lower() not in ("0", "off", "false")
        self.uscis_employer_hub_enabled = os.getenv(
            "ELLIS_USCIS_EMPLOYER_HUB", "on").strip().lower() not in ("0", "off", "false")

        # --- Third-party corroboration / verification (all optional) ---
        # Each is a SECOND opinion only. Unset → the provider reports
        # unavailable and Ellis's own curated answer stands; nothing is ever
        # fabricated and no third party is ever quoted as the government.
        self.sherpa_api_key = os.getenv("SHERPA_API_KEY", "")          # travel requirements
        self.regula_api_key = os.getenv("REGULA_API_KEY", "")          # prior-visa sticker read
        self.regula_base_url = os.getenv("REGULA_BASE_URL", "")        # on-prem option
        self.everify_client_id = os.getenv("EVERIFY_CLIENT_ID", "")    # DHS E-Verify (employer)
        self.everify_client_secret = os.getenv("EVERIFY_CLIENT_SECRET", "")
        self.credential_eval_partner = os.getenv("ELLIS_CREDENTIAL_EVAL", "")  # wes|ece|''

        # --- HRIS / entity / field-quality (all optional, honest-degrade) ---
        # HRIS pull so an employer never retypes title/salary/worksite; a
        # unified aggregator (Merge/Finch) is one key for many systems. Unset →
        # the employer types the facts as today; nothing is invented.
        self.hris_provider = os.getenv("ELLIS_HRIS_PROVIDER", "")       # merge|finch|''
        self.hris_api_key = os.getenv("ELLIS_HRIS_API_KEY", "")
        # Business-entity verification (existence/age/size) feeding ability-to-pay
        # and FDNS-risk signals. A SECOND opinion, never the government's word.
        self.entity_verify_provider = os.getenv("ELLIS_ENTITY_VERIFY", "")  # middesk|dnb|''
        self.entity_verify_api_key = os.getenv("ELLIS_ENTITY_VERIFY_KEY", "")
        # Address validation + visa-photo compliance. Raise fill accuracy /
        # catch a photo the consulate will reject BEFORE submission — never to
        # evade anyone. Unset → Ellis flags the field/photo for human review.
        self.address_verify_provider = os.getenv("ELLIS_ADDRESS_VERIFY", "")  # smarty|loqate|''
        self.address_verify_key = os.getenv("ELLIS_ADDRESS_VERIFY_KEY", "")
        self.photo_check_provider = os.getenv("ELLIS_PHOTO_CHECK", "")   # regula|''

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
    # Imported lazily: the provider imports this module, and its is_configured()
    # is the single definition of "Torch can actually be reached".
    from .providers.torch_status import is_configured as _torch_configured
    # Same rule for every provider added since: the PROVIDER's own
    # is_configured() is the single definition of "this can actually answer".
    # A config flag is a claim, and a claim is not a capability.
    from .providers import browser as _browser
    from .providers import (credential_eval as _credential_eval,
                            document_read as _document_read,
                            everify as _everify,
                            federal_register as _federal_register,
                            requirements_oracle as _requirements_oracle,
                            uscis_employer_hub as _employer_hub)
    from .providers import (address_verify as _address_verify,
                            entity_verify as _entity_verify,
                            hris as _hris,
                            photo_check as _photo_check)
    return {
        "auth": "clerk" if s.clerk_secret_key else "dev_token",
        "database": "postgres" if s.database_url.startswith("postgres") else "sqlite",
        "vault": "aws_secrets_manager" if s.aws_secrets_prefix else "local_encrypted",
        "kimi": bool(s.moonshot_api_key) and s.kimi_enabled and not ks["kimi"],
        "ocr": bool(s.google_cloud_project and s.document_ai_ocr_processor) and s.document_ai_enabled and not ks["document_ai"],
        "browserbase": bool(s.browserbase_api_key) and not ks["browserbase"],
        "stripe_issuing": bool(s.stripe_secret_key) and s.issuing_approved and not ks["stripe"],
        "docusign": bool(s.docusign_integration_key and s.docusign_account_id),
        # O*NET occupation search. A live key only helps SUGGEST an O*NET-SOC
        # code a human confirms; NIOCCS and NAICS validation stay key-free.
        "onet_occupation_search": bool(s.onet_api_key),
        # Read-only tracking of filed government receipts. Never a filing path.
        "uscis_case_status": _torch_configured(),
        # Which cloud browser serves applicant sessions. Both speak CDP; the
        # provider choice never changes what Ellis is willing to do in a
        # session (no CAPTCHA solving, no stealth, personal acts stay personal).
        # Asked of the dispatch that actually opens sessions, so this can never
        # drift from what a session would really run on; the Browserbase kill
        # switch still forces the local path.
        "browser_provider": (
            "local_handoff"
            if (_browser.active_provider() == "browserbase" and ks["browserbase"])
            else _browser.active_provider()),
        # Key-free official US reference feeds. The Federal Register needs only
        # the operator switch; the Employer Data Hub ALSO needs a cached
        # government file on disk, because USCIS publishes no API for it — the
        # switch alone would advertise lookups this deployment cannot answer.
        "federal_register": _federal_register.is_configured(),
        "uscis_employer_hub": _employer_hub.is_configured(),
        "uscis_employer_hub_enabled": s.uscis_employer_hub_enabled,
        # Optional second opinions; each is honestly unavailable when unset and
        # never overrides Ellis's own curated, sourced answer.
        "sherpa_requirements": _requirements_oracle.is_configured(),
        "regula_document_read": _document_read.is_configured(),
        "everify_employer": _everify.is_configured(),
        # The partner Ellis actually holds a referral for, not the raw env
        # value: an unrecognized name is no partner at all.
        "credential_evaluation_partner": (_credential_eval.configured_partner()
                                          or "none"),
        # Employer-data and field-quality helpers. Unset means the human
        # supplies the fact exactly as today — never a fabricated one.
        # Asked of each PROVIDER, not of the env string, per the rule above: a
        # named provider with no key (or, for the photo service, no operator
        # host) cannot answer, and advertising it would promise a capability
        # this deployment does not have.
        "hris_import": _hris.configured_provider() or "none",
        "entity_verification": _entity_verify.configured_provider() or "none",
        "address_verification": (_address_verify.active_provider()
                                 if _address_verify.is_configured() else "none"),
        "photo_compliance_check": (_photo_check.active_provider()
                                   if _photo_check.is_configured() else "none"),
        "storage": "s3_kms" if s.s3_bucket else "local_encrypted",
        "workflow_engine": "temporal" if s.temporal_host else "db_runner",
        "fallbacks": {
            "kimi": "live" if (s.moonshot_api_key and s.kimi_enabled) else "local_test_provider",
            "ocr": "live" if (s.google_cloud_project and s.document_ai_ocr_processor) else "local_mrz_provider",
            "payment": "stripe_issuing" if (s.stripe_secret_key and s.issuing_approved) else "applicant_payment_window",
            # Client vocabulary for "an embedded live view", whichever provider
            # serves it — the same string create_handoff() actually emits, so
            # this must ask the same question it does (browser.is_configured()),
            # not a single vendor's key. The real provider is `browser_provider`.
            "handoff": ("browserbase_liveview" if _browser.is_configured()
                        else "local_handoff"),
            "authorization": "docusign" if s.docusign_integration_key else "in_app_authorization",
            "case_status": "uscis_torch" if _torch_configured() else "tracking_unavailable",
        },
    }
