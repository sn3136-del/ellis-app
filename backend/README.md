# Ellis Visa Backend

A FastAPI service that turns the tested Ellis portal framework into a real,
multi-user, durable production backend. It owns persistence, authentication,
AI (Kimi K3) tool-calling, OCR, the durable workflow, and every live provider
integration — each behind a capability check with a local fallback, so the
whole service and its **26 passing tests** run with **zero cloud credentials**.

## Run it

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate     # optional
pip install -r requirements.txt
pytest -q                       # 26 tests, all green
python demo_e2e.py              # the full 16-step flow, end to end
uvicorn app.main:app --reload   # the API at http://localhost:8000
```

`docker-compose up` brings up the full production-shaped stack (Postgres,
Temporal + UI, API, worker, MinIO). *Docker was not runnable in the build
sandbox, so compose is authored but unverified here; the service itself is
fully verified on SQLite + the DB workflow runner.*

## Architecture

```
Electron client ──HTTP (Clerk/dev auth)──▶ FastAPI (app/main.py)
                                             │
                        ┌────────────────────┼─────────────────────┐
                        ▼                    ▼                     ▼
                 durable service      Kimi K3 agent           OCR pipeline
                 (app/service.py)   (app/providers/kimi)   (app/providers/ocr)
                        │            allowlisted tools       Document AI + MRZ
                        ▼            backend-validated
              VisaWorkflow (app/workflow.py)
              40-state machine, reconcile-before-retry
                        │
        ┌───────────────┼────────────────┬──────────────┬─────────────┐
        ▼               ▼                ▼              ▼             ▼
   Postgres/SQLite  SecretsVault   payment/browser/   portal adapters   audit
   (models.py)      (vault.py)     docusign providers (contract+mock+   (append-only,
   + WorkflowExec   refs only      + local fallbacks   mockland+vietnam)  redacting)
```

**Durability without a Temporal server here:** the entire workflow state is
persisted to `workflow_executions` after every step (including the mock portal
state, so the restart demo is faithful). A signal loads the row, advances to the
next handoff/terminal state, and saves. A worker restart resumes with no
in-memory state. Activation: set `TEMPORAL_HOST` and the same step functions
become Temporal activities.

## Safety properties (enforced + tested)

- **Kimi never touches secrets/payments/bookings/submissions/declarations.** It
  may only propose tools from an allowlist; the backend validates and executes.
  Prompt-injection cannot escalate — the dangerous tools do not exist in the
  registry. (`test_kimi_*`)
- **No double payment / booking / submission** — reconcile before acting.
- **CAPTCHA/OTP/verification/payment/declaration are human handoffs** — the
  workflow pauses; the mock rejects any non-human marker. (`test_captcha_*`)
- **Generated portal passwords live only in the vault** — never in the DB, audit,
  or email. (`test_generated_password_never_leaks`)
- **Tenant isolation** — object-level org checks on every route. (`test_api_flow_and_tenant_isolation`)
- **Worker restart preserves the completed workflow.** (`test_worker_restart_*`)

## Deployment

- **DB**: set `DATABASE_URL` to Neon/Postgres. Run Alembic migrations (add
  `alembic` — `create_all` covers dev).
- **Workers**: run `python -m app.worker` (or a Temporal worker) separately from
  the API. Both are stateless; scale horizontally.
- **Providers**: set the env vars in `.env.example`; `GET /capabilities` shows
  what is live vs. fallback.
- **Observability**: add `sentry-sdk` + OpenTelemetry (hooks noted in config).

See `docs/` for the coverage matrix, privacy/data-flow, ops, incident response,
and the honest production-blocker list. The tested JavaScript framework
(`src/main/portal/`) remains in place as the reference until the Python
replacement is verified in production.
