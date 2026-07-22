# Ellis Portal-Automation Framework

A versioned, adapter-based framework for performing as much of the tourist-visa
process as is technically and **legally** permitted, keeping the applicant in
control of every step that must remain personal (CAPTCHA, OTP, identity checks,
payment secrets, and declarations under penalty of perjury).

> **Status: core framework + mock portal + one demonstration adapter, fully
> tested (`npm test` → 26 passing).** No real government portal adapter is
> implemented or activated. See the Coverage Matrix below.

---

## 1. What this is (and what the brief assumed)

The original brief specified a cloud stack — Next.js, FastAPI, Neon Postgres,
Temporal, Browserbase, Stripe Issuing, Docusign, AWS KMS/Secrets Manager. **Ellis
is not that app.** Ellis is an **Electron desktop application** (electron-vite),
plain JavaScript, React renderer + Node main process, persisting to a JSON store.
There is no Postgres, no Temporal, no cloud infra, and no third-party credentials.

Rather than fake those integrations (which the brief itself forbids), this
framework:

- **Builds the parts that are real and testable today** in the actual stack:
  the adapter contract, a complete mock portal, the workflow state machine, the
  appointment engine, an encrypted secrets vault, an append-only audit log, and
  a resumable orchestrator — all runnable under `node --test`.
- **Represents every external provider behind a capability interface** with a
  safe fallback. Missing credentials disable *only* that provider; the workflow
  still runs end-to-end through the applicant-controlled path.

| Brief's target | This build | Activation path |
|---|---|---|
| Temporal durable workflow | Explicit resumable state machine (`snapshot()`/signals) | Swap `VisaWorkflow` driver loop for Temporal activities; state set is already defined |
| Browserbase Live View | `providers.BrowserbaseLiveView` → `local_handoff` fallback | Set `BROWSERBASE_API_KEY`; implement `createLiveView` |
| Playwright/Stagehand | Adapter `driver` hooks (mock calls today) | Real adapter's `driver` uses Playwright against the page |
| Stripe Issuing | `providers.StripeIssuing` → applicant payment window | Set `STRIPE_SECRET_KEY` + `ELLIS_ISSUING_APPROVED=true` |
| Docusign | `providers.Docusign` → in-app authorization | Set `DOCUSIGN_*`; implement envelope + webhook verify |
| AWS Secrets Manager / KMS | `SecretsVault` via Electron `safeStorage` (OS keychain) or AES-256-GCM | Point vault backend at AWS in production |
| Neon Postgres | JSON store today; models are plain objects | Persist `snapshot()` + audit entries to Postgres |

## 2. Module map

```
src/main/portal/
  adapterContract.mjs   Typed adapter contract, conservative defaults, validator, registry
  mockPortal.mjs        Complete in-process mock government portal
  adapters/mockland.mjs Demonstration adapter (MOCK ONLY, productionEnabled:false)
  stateMachine.mjs      40-state machine with guarded transitions + handoff states
  vault.mjs             Encrypted secrets vault (safeStorage/AES-GCM); password generator
  providers.mjs         Stripe/Browserbase/Docusign/email interfaces + capability report
  appointments.mjs      Preferences, validation, earliest-qualifying ranking (tz-aware)
  audit.mjs             Append-only, auto-redacting audit log
  ics.mjs               RFC 5545 appointment calendar events
  workflow.mjs          Resumable orchestrator tying it all together
tests/portal/
  framework.test.mjs    Contract, state machine, vault, audit, appointments, providers
  e2e.test.mjs          Full mock-portal run + every safety property
```

## 3. Running

```bash
npm test          # node --test, 26 tests, ~0.2s
```

The end-to-end test drives a case from DRAFT to COMPLETED against the mock
portal, answering each human handoff, and asserts the safety invariants below.

## 4. Safety invariants (enforced + tested)

- **CAPTCHA is never auto-solved.** The workflow pauses at
  `CAPTCHA_ACTION_REQUIRED`; only a human marker advances it. Test: *"CAPTCHA
  cannot be auto-solved"* and the mock rejects any non-human answer.
- **No double payment / booking / submission.** Every one reconciles against
  the portal's current state before acting. Tests: *"no double payment"*,
  *"submission is idempotent"*.
- **Personal declaration stays personal.** The portal refuses submission until a
  human declaration marker is set. Test: *"submission blocked until the personal
  declaration is completed"*.
- **Fee ceiling.** A fee above the authorized maximum halts to
  `MANUAL_REVIEW_REQUIRED` with no charge. Test present.
- **Secrets never leak.** Generated portal passwords live only in the vault
  (never plaintext in the store, audit, or email). Tests: *"vault never
  persists plaintext"*, *"generated portal password never appears in audit log
  or emails"*.
- **Missing credentials degrade safely.** Test: *"payment fallback engages when
  Stripe Issuing is unconfigured"*.
- **Portal failures are recoverable, not crashes.** Any exception →
  `RECOVERABLE_FAILURE`. Test: *"maintenance mode routes to recoverable failure"*.

## 5. Coverage matrix (honest)

| Adapter | Country | Visa | Status | Production |
|---|---|---|---|---|
| `mockland-tourist-v1` | Mockland | tourist | **mock** | disabled |

No production government-portal adapter exists. Building one requires: the
adapter definition, deterministic Playwright selectors against the real portal,
a **legal review of that portal's terms** (many prohibit automated agents),
explicit `production_approved` status, and the `productionEnabled` flag — which
the validator refuses to accept without approval.

### Next highest-priority adapters (by legitimacy of automation)

Prefer official online **e-visa** portals over embassy portals — they permit
applicant/agent online filing and have no in-person biometrics:

1. Vietnam e-Visa (`evisa.gov.vn`)
2. India e-Visa (`indianvisaonline.gov.in`) — where restored for the nationality
3. Turkey e-Visa, Egypt e-Visa, Kenya eTA, Sri Lanka ETA

Embassy/consular portals (US CEAC, Schengen VACs) require in-person biometrics
and often forbid automation; those stay applicant-driven at the counter.

See `../../docs/` for the [adapter development guide](../../docs/PORTAL_ADAPTER_GUIDE.md),
[threat model](../../docs/THREAT_MODEL.md), and
[production activation checklist](../../docs/ACTIVATION_CHECKLIST.md).
