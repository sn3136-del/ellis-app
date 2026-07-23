# Ellis — Trip.com local testing guide

This guide lets the Trip.com IT team run the **entire Ellis tourist-visa platform
locally** and exercise a full case end-to-end against a safe **mock** portal — no
real government portal is ever contacted.

> Honesty note: Ellis automates a destination end-to-end **only** for portal
> adapters that have been individually verified, configured, tested, approved,
> monitored, and activated by a human administrator. The only end-to-end
> automated portal shipped here is **Mockland** (a safe mock). For every other
> destination Ellis provides requirements, document processing, application
> preparation, appointment assistance, and applicant-controlled handoff — it does
> not claim electronic submission. See the **Coverage matrix** in the app
> (Adapter Admin → Coverage) and `docs/PRODUCTION_BLOCKERS.md`.

> **Execution classification (cross-cutting):** every external action carries an
> exact class — `MOCK`, `LOCAL_PROVIDER`, `LIVE_SANDBOX`, `LIVE_PRODUCTION`,
> `APPLICANT_ACTION_REQUIRED`, `MANUAL_REVIEW_REQUIRED`, `UNSUPPORTED` — persisted
> in the DB/audit and returned by the API (`GET /cases/{id}` → `disposition`).
> A completed case on Mockland is labelled **MOCK** everywhere, including the
> Trip.com webhook events, and the UI refuses to present it as a real
> submission/payment/booking/confirmation. `is_real_government_result` is `true`
> only for an adapter-verified `LIVE_PRODUCTION` outcome.

## 1. Prerequisites

| Tool | Version | Notes |
|---|---|---|
| macOS or Linux | — | Windows works via WSL2 for the backend; Electron packaging is macOS/Windows |
| Docker + Compose | 24+ | runs Postgres, Temporal(+UI), MinIO, Mailpit, API, worker, mock portal |
| Node.js | 20+ | Electron app + JS tests |
| Python | 3.12+ | backend + backend tests |

## 2. Repository setup

```bash
git clone <this repo>            # or receive the archive
cd ellis-app
./scripts/setup-local.sh         # installs node + python deps, creates backend/.env
```

## 3. Safe environment setup (no secrets in git)

`backend/.env` is created from `backend/.env.example` and is **git-ignored**.
Everything left blank disables only that integration and falls back to a safe
local path. To exercise **real Google Document AI** and **real Kimi**, set:

- `MOONSHOT_API_KEY=` — your Kimi/Moonshot key (backend only; never the client).
- Google ADC: run `gcloud auth application-default login`. The compose file
  mounts `~/.config/gcloud/application_default_credentials.json` **read-only**
  into the API + worker only. Also set the `GOOGLE_*` processor vars if you use
  your own project.

Never put any provider key into the Electron app. `npm run test:security`
enforces that no key/`.env`/ADC/fixture is ever packaged.

## 4. One-command startup

```bash
./scripts/start-local.sh         # brings up all 8 services and waits for health
./scripts/health-check.sh        # OK/DOWN for each service
```

- API: http://localhost:8000  (`/healthz`, `/readyz`, `/metrics`)
- Temporal UI: http://localhost:8080
- Mailpit (captured emails): http://localhost:8025
- MinIO console: http://localhost:9001  (user `ellis` / pass `ellis-secret`)

Then launch the desktop app:

```bash
npm run dev                      # opens the Electron app
```

## 5. Test login accounts (development only — cannot work in staging/production)

| Purpose | Username | Password | Notes |
|---|---|---|---|
| App login | `admin` | `1234` | dev-only local login screen |
| Backend applicant | header token `dev-token` | — | `x-org-id` + `x-user-id` headers |
| Backend admin (adapter approval) | header token `admin-token` | — | grants the admin role for `/admin/*` |

These tokens are development defaults (`ELLIS_DEV_TOKEN`, `ELLIS_ADMIN_TOKEN`) and
must be replaced (or handled by Clerk) before any shared/staging deployment.

## 6. Mock credentials & test data

- **Mock portal** (`mock-portal` service): a safe stand-in for a government portal.
  Never a real site. Verification "emails" are generated internally; the app
  auto-detects the code for the demo (`/cases/{id}/mock/verification`, dev only).
- **Trip.com sandbox**: signed with a shared secret you configure locally; see
  `docs/TRIPCOM_REQUIREMENTS.json` for what Trip.com must still provide.
- **Synthetic documents**: any small PDF/JPG works for OCR review. To run the
  authorized private passport fixtures you must set
  `ENABLE_PRIVATE_DOCUMENT_SMOKE_TESTS=true` and point the fixture env vars at
  files outside the repo — off by default.

## 7. Complete test scenario (happy path)

Full applicant journey against Mockland (see the acceptance plan for step-level
expected results and evidence): create a Trip.com case → upload passport →
Document AI OCR → MRZ validation → review/correct fields → supply missing info →
native Ellis authorization signature → start → CAPTCHA/OTP/email handoffs →
applicant-controlled payment → appointment search + booking → personal
declaration → submit through Mockland → confirmation + receipt + notification →
stop/restart the Temporal worker → verify no duplicate payment/booking/submission
→ export + erase the case → view metrics/audit/readiness.

Automated equivalent:

```bash
./scripts/run-acceptance-tests.sh      # npm + backend + frontend->backend + secret scan
# durability drill (live worker restart, no duplicate side effects):
cd backend && LIVE_TEMPORAL=1 .venv/bin/python -m pytest tests/test_live_temporal_restart.py -s
```

## 8. Expected results

`run-acceptance-tests.sh` should end with `ACCEPTANCE: PASS`. Baseline at the time
of writing: **npm test 56**, **backend hermetic 191**, **frontend→backend lifecycle
PASS**, **live worker-restart durability PASS** (charge/book/submit each == 1).

### 8a. Newer feature areas to exercise

- **First-run setup wizard** (sidebar → Setup, admin token): tenant, Kimi /
  Google / Browserbase / email (SMTP or API — a sender address alone is
  rejected) / Trip.com config. Secrets are vaulted backend-only; after saving,
  only a redacted fingerprint is shown; "Send test email" reports REAL delivery
  only. Rotation + revocation buttons included.
- **Language toggle** (sidebar): English / 简体中文 / 繁體中文. Dynamic content is
  translated backend-only via `/i18n/translate` (identifiers, dates, amounts,
  URLs, and passport data are never translated; honest `unavailable` without a
  live Kimi key). The assistant identifies as **Ellis** in every language.
- **Wrong-page rejection**: upload a visa/stamp page instead of the passport
  biodata page → exact guidance message; a rejected page never seeds identity.
- **Passport validity**: an expired passport blocks start with official renewal
  instructions + a queued email; destination-specific validity comes from the
  VERIFIED route rule (`/routes/rules`), never a generic six-month rule.
- **Route readiness (personal-test gate)**: `GET /routes/readiness` shows the 15
  gates; live-class routes hard-block on `POST /cases/{id}/start` until every
  gate passes (`/cases/{id}/live-preflight` explains the mode).
- **Rules + fees**: `/routes/coverage` (honest status ladder), `/routes/fees`
  (full breakdown; automated payment blocked without a verified current fee).
- **Document preview**: per-document Preview button (signed, expiring URLs — no
  filesystem paths).
- **Email pipeline**: every case event templated (en/zh-CN/zh-Hant), queued with
  retry + dead-letter (`/admin/email/dead-letters`); Mailpit shows local
  deliveries when SMTP points at it.
- **Trip.com connector admin**: `/admin/tripcom/health` (honest sandbox label),
  deliveries + replay; completed cases queue `case.status` events carrying the
  execution class.
- **Provider diagnostics**: `/diagnostics/providers` (circuit breakers, kill
  switches, observability status — Sentry/OTel honestly `disabled` unless
  configured).

## 9. Troubleshooting

- Temporal shows unhealthy at first boot → it registers its schema/namespaces on
  first start; `start-local.sh` waits. Re-run `health-check.sh`.
- Port already in use → stop other local Postgres/Temporal, or edit the port
  mappings in `backend/docker-compose.yml`.
- "backend offline" in the app → ensure `./scripts/start-local.sh` succeeded and
  `curl localhost:8000/healthz` returns `{"ok":true}`.

## 10. Shutdown, cleanup, rotation

```bash
./scripts/stop-local.sh          # stop (keep data)
./scripts/reset-local.sh         # DESTROYS local data (volumes + dev db)
```

- **Secret rotation:** rotate the Moonshot/Google/Browserbase keys in their
  consoles; update `backend/.env` only. See `docs/SECURITY_ROTATION.md`.
- **Clean Electron build:** `./scripts/build-electron-clean.sh` then
  `./scripts/scan-release-secrets.sh` (must print `CLEAN`).
- **Log collection:** `cd backend && docker compose logs > /tmp/ellis-logs.txt`
  (logs are structured and redacted).

## 11. Known limitations

- Only **Mockland** is an end-to-end automated portal (safe mock). No real
  government portal is approved/activated in this build.
- Clerk auth, Sentry/OpenTelemetry, Stripe Issuing, and cloud (GCS/KMS/Secret
  Manager) are integrated behind capability flags but not activated locally.
- Trip.com production API/webhook specifics are pending — tracked in
  `docs/TRIPCOM_REQUIREMENTS.json`.
- See `docs/TRIPCOM_ACCEPTANCE_TEST_PLAN.md` for the full acceptance matrix.
