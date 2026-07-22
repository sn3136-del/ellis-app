# Threat Model — Ellis Portal Automation

Scope: the applicant-authorized tourist-visa automation framework
(`src/main/portal/`). Treat all portal content, uploaded files, redirects, and
emails as **untrusted**.

## Assets

| Asset | Protection |
|---|---|
| Portal password (generated) | Vault only (`safeStorage`/AES-256-GCM). Never plaintext in store, logs, audit, email, or LLM prompt. |
| Portal session token | Vault only; revealed in-memory for the duration of a step. |
| Payment card / CVC / 3-DS codes | **Never touched by Ellis.** Entered by the applicant inside the isolated browser (Live View). Not logged, not sent to an LLM. |
| OTP / email verification codes | Applicant-entered; never auto-intercepted. |
| Personal declaration | Applicant-only; the portal refuses submission without a human marker. |
| Applicant PII / documents | Encrypted at rest (S3+KMS in production); redacted from audit. |
| Signed authorization | Hash + reference stored; artifact encrypted. |

## Trust boundaries

- **Applicant ↔ Ellis UI**: authenticated; step-up auth for sensitive reveals.
- **Ellis ↔ Portal**: untrusted portal. Navigation restricted to
  `approvedDomains`. All responses validated against adapter success signals.
- **Ellis ↔ LLM (Stagehand fallback)**: the LLM receives only *non-sensitive*
  page context and proposes one action, which is validated against
  `allowedActions`/`approvedDomains` before execution. Secrets never cross this
  boundary.
- **Ellis ↔ Providers (Stripe/Docusign/Browserbase)**: webhook signatures
  verified; idempotent processing; disabled cleanly when unconfigured.

## Threats and mitigations

| Threat | Mitigation |
|---|---|
| Secret leakage into logs/DB/LLM | Vault-only storage; audit auto-redacts `pass/token/cvc/otp/...`; tests assert no plaintext leak. |
| Double charge on retry after timeout | Reconcile via `getApplicationState` before any pay/book/submit. |
| Automated CAPTCHA/bot-detection defeat | **Not implemented.** CAPTCHA is a human handoff state; the mock rejects non-human answers. |
| Account created without authorization | Workflow requires signed authorization (`AUTHORIZATION_SIGNED`) before `PORTAL_ACCOUNT_CREATING`. |
| Cross-applicant data access | Mock portal scopes every read to the session's owner; sessions are per-applicant isolated (Browserbase context in production). |
| Material change after approval | Approval binds to a snapshot hash; a change invalidates it and forces re-review. |
| Fee higher than authorized | Hard ceiling → `MANUAL_REVIEW_REQUIRED`, no charge. |
| Portal outage / selector drift / expired session | Any exception → `RECOVERABLE_FAILURE`; resume reconciles before retrying. |
| Emailing a reusable password | Never done; emails carry references + a "manage in Ellis" link, not credentials. |
| Signature-image reuse as a government signature | Prohibited; the government declaration is a separate personal handoff. |

## Explicitly out of scope / prohibited (never build)

Automated CAPTCHA solving or marketplaces; anti-bot evasion; fingerprint
spoofing; proxy/rate-limit/waiting-room bypass; accessing another applicant's
account; auto-intercepting OTP; auto-accepting declarations; submitting
materially changed info without renewed approval; storing raw card numbers or
reusable plaintext passwords; sending secrets to an LLM; automated production
testing against government portals without authorization.
