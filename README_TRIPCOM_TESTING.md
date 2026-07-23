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
of writing: **npm test 45**, **backend hermetic 61**, **frontend→backend lifecycle
PASS**, **live worker-restart durability PASS** (charge/book/submit each == 1).

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
