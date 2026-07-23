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
| AT-28 | Execution classification | any case | GET `/cases/{id}` + complete a Mockland case | disposition present; completed mock case is NEVER presented as real; ExecutionRecord persisted | test_execution (17+) | PASS | absolute requirement |
| AT-29 | Wrong-page rejection | AT-05 | upload a visa/stamp page | exact guidance message; no identity fields stored | test_passport_classifier (20) | PASS | incl. P-name regression |
| AT-30 | Language toggle + translation | app open | switch EN/简/繁; POST `/i18n/translate` | static UI localized; identifiers never translated; honest `unavailable` without Kimi | test_i18n (15) + i18n.test.mjs (8) | PASS | |
| AT-31 | Assistant identity | any | GET `/assistant/identity?lang=zh-CN` | answers as Ellis, never Kimi/Moonshot/official | test_i18n | PASS | |
| AT-32 | First-run setup wizard | admin | POST `/setup` + tests + send-test-email | secrets vaulted, never echoed (fingerprint only); sender-alone rejected; rotation/revocation | test_setup (11) | PASS | |
| AT-33 | Route readiness gate | admin | set gates via `/admin/routes/readiness` | all 15 required; evidence mandatory; live start blocked otherwise | test_personal_gate (9) | PASS | personal-test gate |
| AT-34 | Route rules + coverage | admin | create/verify rules; GET `/routes/coverage` | pending never served as verified; honest status ladder | test_rules_fees (10) | PASS | |
| AT-35 | Fee engine | admin | create/verify fee; GET `/routes/fees` | full breakdown; automated payment blocked without verified current fee; staleness alert | test_rules_fees | PASS | |
| AT-36 | Passport validity | AT-07 | approve expired passport / start | blocked + official renewal authority + queued email + retry-after-renewal | test_emails_validity (14) | PASS | destination rule, not generic |
| AT-37 | Email pipeline | AT-16 | queue events; `/admin/email/process-queue` | retry then dead-letter; content guard blocks passport numbers + Live View URLs | test_emails_validity | PASS | |
| AT-38 | Document preview | AT-06 | Preview button / signed URL | expiring signed URLs; auth + tenant checks; no local paths | test_doc_preview (5) | PASS | |
| AT-39 | Provider errors + breakers | — | GET `/diagnostics/providers` | taxonomy w/ safe messages; breakers open/half-open/close; secrets scrubbed | test_errors_observability (11) | PASS | |
| AT-40 | Trip.com connector admin | admin | health/deliveries/replay/process | honest sandbox label; signed deliveries; replay; case.status carries execution class | test_tripcom_admin (6) | PASS | NOT production |
| AT-41 | Runtime-mode boundary | — | set each ELLIS_RUNTIME_MODE; start a case | MockPortal only in test/local_mock_demo (+banner); real-only modes stop typed (UNSUPPORTED/PORTAL_UNAVAILABLE), MockPortal never constructed | test_runtime_modes (14) | PASS | poisoned-constructor proof |
| AT-42 | Trip.com-only bundle | — | `npm test` (tripcom_only) | legacy products absent from source AND built renderer bundle; root bundle gone | tripcom_only.test.mjs (6) | PASS | |
| AT-43 | Route intake + resolution | applicant | Start-your-visa wizard; Resolve route | save/resume; one readiness status; honest checks; snapshot-date labels; NOT_READY never invents requirements | test_route_resolution (8) + intake_logic | PASS | |
| AT-44 | Snapshot data layer | admin | importer validate/dry-run/apply/rollback; matrix; export | immutable versions; material-change review tasks; 318,222-entry matrix; 7 separate coverage metrics | test_snapshot_core (12) + test_snapshot_importer (12) | PASS | matrix completeness ≠ requirements coverage |
| AT-45 | On-demand route research | applicant | Resolve a missing route | focused job auto-starts (this route only); 9-step progress UI; grounded Kimi extraction rejects uncited fields; blocked sources → honest incomplete + review task; cache reuse = zero fetches; date-honest labels | test_ondemand_research (11) | PASS | live-proven vs KHM (honest incomplete on SPA portal) |
| AT-46 | Live driver safety | — | (unit) drive pay/book/submit via BrowserbaseLiveViewDriver | fail-closed binding; evidence-only success; reconcile prevents duplicate pay/submit; secrets refused; allowlist enforced | test_live_driver (13) | PASS | no live portal contacted |
| AT-47 | Browser sessions + Live View | applicant | open/refresh/close case browser session | tenant-isolated; URLs minted fresh, no-store, never logged/audited; honest local-mode 404 | test_browser_sessions (5) | PASS | |
| AT-48 | Adapter dry-validation harness | admin | GET `/admin/adapters/harness` | structural/safety/domains/selectors/extraction/recovery checks + live-binding fail-closed proof; evidence only, cannot approve | test_adapter_harness (7) | PASS | |

## Not yet covered (honest gaps)

- Real government portal automation (only Mockland is end-to-end automated); no
  route has passed the 15-point readiness gate — live starts are hard-blocked.
- Sentry / OpenTelemetry live pipelines (init is flag-gated and honestly
  `disabled` without DSN/deps), Stripe Issuing live, cloud staging deploy
  (Phase 16) — not deployed.
- Trip.com production API/webhook schemas — see `docs/TRIPCOM_REQUIREMENTS.json`;
  the connector reports `is_tripcom_production: false` until then.
- Configurable load tests (Phase 9/18) — not yet executed; do not treat as run.
- Full graphical demonstration recording (Phase 18) — pending; the flows are
  covered by the automated acceptance tests above.
