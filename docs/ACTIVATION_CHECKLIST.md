# Production Activation Checklist & Operations

## Provider activation (each independent; absent → safe fallback)

| Provider | Env vars | Fallback when absent |
|---|---|---|
| Stripe Issuing | `STRIPE_SECRET_KEY`, `ELLIS_ISSUING_APPROVED=true` | Applicant-controlled payment window |
| Browserbase | `BROWSERBASE_API_KEY` (+ project id) | `local_handoff` instruction panel |
| Docusign | `DOCUSIGN_INTEGRATION_KEY`, `DOCUSIGN_ACCOUNT_ID`, `DOCUSIGN_PRIVATE_KEY` | In-app typed/drawn authorization (hashed) |
| Secrets vault (AWS) | `AWS_REGION`, `AWS_SECRETS_PREFIX` | Electron `safeStorage` / AES-256-GCM |
| Email | existing Ellis SMTP settings | Local Mail app (macOS) |

`capabilityReport()` prints the live/fallback status for all of them.

## Per-country adapter activation

For each real portal, in order:

1. **Legal review** of the portal's terms for automated agents. If automation is
   prohibited, keep every automatable step applicant-driven via Live View.
2. Implement the adapter `driver` with deterministic Playwright selectors.
3. Verify selectors against the live portal (manually, authorized).
4. Add adapter tests (mock or portal-approved sandbox only).
5. Set `productionApprovalStatus: 'production_approved'`.
6. Set `productionEnabled: true`.
7. Configure rate limits conservatively (`searchMinIntervalMs`,
   `maxChecksPerDay`) — respect the portal's limits and waiting rooms.

The `validateAdapter` gate refuses `productionEnabled` without approval, so an
un-reviewed adapter cannot reach production by mistake.

## Kill switches

- Per-adapter: `productionEnabled: false` immediately disables live runs for
  that portal (validator still allows the adapter to load in mock).
- Per-provider: unset the env var → fallback engages, no code change.
- Global: disable the portal-automation feature flag in Ellis settings.

## Operations runbook

- **Stuck case**: inspect `workflow.snapshot()` → `state` + `history`. A handoff
  state means the applicant owes an action; re-send the Live View link.
- **`RECOVERABLE_FAILURE`**: read the audit `recoverable_failure` entry's
  `code`. Resume re-runs `getApplicationState` before any retry.
- **`MANUAL_REVIEW_REQUIRED`**: a human must decide (e.g. fee over ceiling,
  contradictory preferences). No automated escape.
- **Suspected double action**: check the portal via `getApplicationState`; the
  reconcile logic prevents duplicates, but verify the confirmation/reference.

## Incident response

1. Flip the relevant kill switch (adapter flag, provider env, or global flag).
2. Rotate any potentially exposed portal credential via `SecretsVault.rotate`
   and notify the applicant to reset on the portal.
3. Pull the append-only audit for the affected `caseId` (`AuditLog.forCase`).
4. Confirm no secret leaked: `AuditLog.containsPlaintext(secret)` must be false.
5. File the portal-policy review date update if a portal changed its terms.
