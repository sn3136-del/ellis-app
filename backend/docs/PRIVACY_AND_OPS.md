# Privacy, Data Flow, Operations & Incident Response

## Data flow

```
Applicant → Electron client → API (auth) → durable service → workflow → portal
   │            │                │              │               │
   │            │                │              │               └─ portal password/session → VAULT (ref only in DB)
   │            │                │              └─ audit events (redacted) → Postgres
   │            │                └─ documents → OCR → extracted fields (applicant-approved) → answers
   │            └─ card/CVC/OTP/CAPTCHA → NEVER to the API/LLM; entered in the browser Live View
   └─ email/appointment notifications ← backend (no reusable passwords)
```

## What is stored where

| Data | Store | Notes |
|---|---|---|
| Applicant PII, answers | Postgres | tenant-scoped; encrypt at rest in prod (S3+KMS for docs) |
| Documents | S3 (prod) / local ref (dev) | KMS-encrypted; short-lived signed URLs |
| Extracted OCR fields | Postgres | only after applicant review + approval |
| Portal password / session | **Vault only** (`vault://…` ref in DB) | never plaintext in DB/logs/email |
| Card number / CVC / OTP / CAPTCHA answer | **Nowhere in Ellis** | entered by the applicant in the isolated browser |
| Audit events | Postgres (append-only) | auto-redacted; secrets never recorded |

## What is never sent to the LLM

Passwords, card data, OTP/CAPTCHA values, cookies, session tokens, or full raw
passports/bank statements. Kimi receives only non-sensitive OCR excerpts or
structured fields, and may only propose allowlisted tools.

## Operations runbook

- **Case stuck at a handoff**: `GET /cases/{id}` → `pending.handoff`. The
  applicant owes that action; re-send the Live View / instruction link.
- **`RECOVERABLE_FAILURE`**: check the audit `recoverable_failure` event's
  `code`. Resume re-runs `get_application_state` before any retry (no double
  charge/submit).
- **`MANUAL_REVIEW_REQUIRED`**: a human decision (fee over ceiling, contradictory
  preferences). No automated escape.
- **Worker/API restart**: safe at any time — state is durable in Postgres; both
  processes are stateless.
- **Scaling**: run N API replicas + M workers; the DB is the coordination point
  (or Temporal when `TEMPORAL_HOST` is set).

## Incident response

1. **Kill switch**: unset a provider env var (→ fallback) or set an adapter's
   `production_enabled=False` (→ live runs stop; validator still loads it in mock).
2. **Credential exposure**: `vault.rotate(ref, new_value)`; instruct the applicant
   to reset on the portal (rotating outside Ellis disconnects automation — warn).
3. **Audit pull**: `GET /cases/{id}/audit` (or `AuditEvent` by `org_id`).
4. **Leak check**: `audit.contains_plaintext(db, secret)` must return False.
5. **Portal policy change**: update the adapter's `portal_policy_review_date` and
   re-review before re-enabling.

## Compliance posture

- Applicant authorization (DocuSign or recorded in-app) precedes any account
  creation, filling, payment, or submission.
- Personal declarations under penalty of perjury are always applicant-completed.
- No CAPTCHA solving, anti-bot evasion, fingerprint spoofing, proxy rotation,
  rate-limit or waiting-room bypass, or unauthorized government submissions.
