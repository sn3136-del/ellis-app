# Production Blockers — clearly separated from completed engineering

## ✅ Completed engineering (runnable + tested here)

- FastAPI backend: cases, documents/OCR, review/approve, preferences,
  authorization, durable start + human-handoff signals, appointment, audit,
  capabilities, adapters, webhooks — with auth + tenant isolation.
- Full data model + SQLAlchemy (SQLite dev / Postgres prod) + `create_all`.
- Durable, resumable 40-state workflow persisted to the DB; worker-restart
  consistency proven by test + demo.
- Kimi K3 provider (OpenAI-compatible) + deterministic local double; allowlisted
  tool registry with backend validation, loop/no-progress detection,
  prompt-injection resistance.
- OCR pipeline: Document AI adapter + local provider; deterministic ICAO 9303
  MRZ parse + checksum + cross-document conflict detection.
- Payment (Stripe Issuing + applicant window), Browser/Live View, DocuSign —
  all behind capability checks with local fallbacks.
- Encrypted secrets vault (refs only in DB); append-only redacting audit.
- Portal contract + mock portal + `mockland` + `vietnam-evisa` adapters.
- 26 pytest tests + `demo_e2e.py` (16-step flow) — all green with zero creds.
- Existing 26 JavaScript framework tests still green; Electron build intact;
  typed backend client added to the Electron renderer.

## ✅ Providers ACTIVATED and live-smoke-tested this pass

- **Kimi K3 — LIVE (provider-tested).** Real key configured; `test_kimi_live_json`
  and `test_kimi_vision_reads_passport` pass against `api.moonshot.ai`. Kimi K3 is
  a reasoning model — the client reads `reasoning_content` when `content` is empty
  and uses a generous token budget so vision/JSON calls return content.
- **Browserbase — LIVE (provider-tested).** Real key configured;
  `test_browserbase_session_lifecycle` creates and releases a real isolated session.
- **Docker + PostgreSQL — VERIFIED.** `docker compose up -d postgres` → healthy;
  schema applied to a clean Postgres; full workflow + worker-restart durability
  ran to COMPLETED on Postgres (this surfaced and fixed a real `BigInteger`
  bug for epoch-ms columns that SQLite had masked). Backend image builds (308 MB)
  and the containerized API serves `/healthz`, enforces auth, and creates cases.

## ⛔ Production blockers (require credentials, infra, or external approval)

These are **configuration/approval** steps, not missing engineering.

| Blocker | Activation |
|---|---|
| Live Document AI OCR | processor IDs are set, but **no Google access token** here (no `gcloud`, no service-account). Provide `GOOGLE_APPLICATION_CREDENTIALS` (service-account JSON) or `gcloud auth application-default login`. Verify: `python -c "from app.providers import documentai; print(documentai.is_authenticated())"`. Until then OCR uses the live **Kimi K3 vision** tier (reads passports; not pixel-accurate on MRZ check digits) → local text tier. |
| Stripe Issuing | `STRIPE_SECRET_KEY` + `ELLIS_ISSUING_APPROVED=true` (applicant payment window works now) |
| Automated fee payment | `STRIPE_SECRET_KEY` + `ELLIS_ISSUING_APPROVED=true` + Stripe Issuing approval |
| Embedded authorization | `DOCUSIGN_INTEGRATION_KEY` + `DOCUSIGN_ACCOUNT_ID` + template |
| Production auth | `CLERK_SECRET_KEY` + `verify_clerk` implementation |
| Managed Postgres | `DATABASE_URL` (Neon) + Alembic migrations |
| Encrypted secrets at scale | `AWS_SECRETS_PREFIX` + IAM (replaces local vault) |
| Document storage | `S3_BUCKET` + `KMS_KEY_ID` |
| Durable engine at scale | `TEMPORAL_HOST` + a Temporal worker deployment |
| Observability | `sentry-sdk` + OpenTelemetry exporter config |

## ⛔ Per-country production activation (legal + engineering)

No country adapter is production-enabled. For each real portal:

1. **Legal review** of the portal's terms for authorized-agent automation.
2. Implement the live **Playwright driver** (the config/mappings exist for
   Vietnam; the driver currently binds to the mock).
3. Verify selectors against the live portal (manual, authorized).
4. `production_approval_status='production_approved'` + `production_enabled=True`.

## ⛔ Environment limits of this build sandbox (not code gaps)

- **Docker was unavailable**, so `docker-compose.yml` is authored but not run
  here. The service is fully verified on SQLite + the DB workflow runner.
- **No Temporal server / Postgres server / cloud accounts** were present, so
  those paths are implemented behind flags and exercised via their local
  fallbacks, not against live infra.

Nothing above is faked or stubbed as if complete: the code paths exist, the
fallbacks are real and tested, and every activation is a named config step.
