# Ellis — Trip.com acceptance test plan

Each test has: **ID · Preconditions · Steps · Expected · Evidence · Result · Notes**.
`Result` values: PASS / FAIL / N-A. Automated tests below were green at commit
time; UI-driven tests are marked where they require the desktop app.

Run the automated suite: `./scripts/run-acceptance-tests.sh`.

| ID | Area | Preconditions | Steps | Expected result | Evidence | Result | Notes |
|---|---|---|---|---|---|---|---|
| AT-01 | Installation | Docker/Node/Python present | `./scripts/setup-local.sh` | deps installed, `backend/.env` created from template | script exit 0 | PASS | idempotent |
| AT-02 | Startup | AT-01 | `./scripts/start-local.sh` | 8 services up; 6 report healthy | `health-check.sh` all OK | PASS | Temporal warms on first boot |
| AT-03 | Health/readiness | AT-02 | `curl /healthz` `/readyz` | `{"ok":true}` / `{"ready":true}` | curl output | PASS | readyz checks DB |
| AT-04 | Authentication | app running | login `admin`/`1234`; backend `dev-token` + `admin-token` | applicant vs admin role enforced | 403 on non-admin activation | PASS | test_adapters_admin |
| AT-05 | Case creation | AT-02 | POST `/cases` (Trip.com case) | case id, state DRAFT | frontend_backend test | PASS | |
| AT-06 | Document + OCR | AT-05 | upload passport → Document AI | doc classified, fields + MRZ status | document_ocr audit; OCR review UI | PASS (local); provider-tested w/ ADC | Google DocAI when configured |
| AT-07 | Review & correct | AT-06 | edit/accept/reject fields; approve | applicant-approved values become canonical | approve returns answers | PASS | |
| AT-08 | Missing info | AT-06 | fill required fields | `missing_fields` empties | review endpoint | PASS | |
| AT-09 | Native signature | AT-07 | prepare + sign (consent/intent/step-up) | tamper-evident artifact hash; material change invalidates | signature_id, artifact_hash | PASS | test_signature |
| AT-10 | Portal selection | AT-02 | Adapter Admin → coverage; select Mockland | honest service level shown | coverage matrix | PASS | only Mockland automated |
| AT-11 | Portal account + CAPTCHA/OTP | AT-09 | start → captcha/email handoffs | Ellis never solves CAPTCHA; collects no OTP/password | Live View modal; signals | PASS | |
| AT-12 | Applicant payment | AT-11 | payment_approval + payment handoffs | card never seen by Ellis; complete_payment resumes | PaymentModal; charge==1 | PASS | |
| AT-13 | Appointments | AT-12 | select slot / auto-book | booking once, ICS/notification | Appointment row; book==1 | PASS | |
| AT-14 | Declaration | AT-13 | personal declaration | only applicant signs | complete_declaration | PASS | |
| AT-15 | Submission | AT-14 | submit through Mockland | confirmation + receipt | SubmissionConfirmation | PASS | submit==1 |
| AT-16 | Notifications | AT-15 | check Mailpit + in-app | email + notification recorded | EmailNotification; Mailpit UI | PASS | |
| AT-17 | Temporal restart | AT-11+ | stop worker → restart | workflow durable (RUNNING); resumes | test_live_temporal_restart | PASS | live cluster |
| AT-18 | Idempotency | AT-17 | complete after restart | payment/booking/submission each == 1 | ledger counts | PASS | reconcile short-circuit |
| AT-19 | Export | any case | GET `/cases/{id}/export` | portable, secret-free bundle | test_privacy | PASS | |
| AT-20 | Deletion | any case | DELETE `/cases/{id}` | cascade + non-PII tombstone | test_privacy | PASS | tenant-isolated |
| AT-21 | Metrics | AT-02 | GET `/metrics` (admin) | org-scoped counts + kill switches, no PII | test_privacy | PASS | |
| AT-22 | Error recovery | provider off | run with providers absent | safe local fallbacks, no crash | capabilities fallbacks | PASS | |
| AT-23 | Tenant isolation | 2 orgs | cross-tenant access | 403 | test_e2e, test_privacy | PASS | |
| AT-24 | Adapter approval | admin | drive lifecycle to production_active | human-admin only; no AI activation; immutable audit | test_adapters_admin | PASS | |
| AT-25 | Secret scanning | build present | `./scripts/scan-release-secrets.sh` | CLEAN, exit 0 | scan output | PASS | |
| AT-26 | Clean Electron packaging | — | `./scripts/build-electron-clean.sh` | no key/.env/ADC/fixture in app.asar | build_security test | PASS | unsigned locally |
| AT-27 | Trip.com sandbox | — | signed request + webhook verify | HMAC + replay + idempotency | test_tripcom | PASS | prod spec pending |

## Not yet covered (honest gaps)

- Real government portal automation (only Mockland is end-to-end automated).
- Sentry / OpenTelemetry live pipelines, Stripe Issuing live, cloud staging deploy.
- Trip.com production API/webhook schemas — see `docs/TRIPCOM_REQUIREMENTS.json`.
- Configurable load tests (Phase 9) — not yet executed; do not treat as run.
