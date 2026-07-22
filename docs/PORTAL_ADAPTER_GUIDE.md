# Writing a Portal Adapter

An adapter is the **only** place that knows how to talk to a specific
government/visa-provider portal. The orchestrator, state machine, vault, and
appointment engine are all portal-agnostic. Never put country logic in them.

## 1. Copy the demonstration adapter

Start from `src/main/portal/adapters/mockland.mjs`. It is a full, validated,
tested adapter — every field is populated and commented.

## 2. Fill in the contract

Provide every field in the `PortalAdapter` typedef (`adapterContract.mjs`).
The validator (`validateAdapter`) rejects an adapter that:

- omits any required string field or a positive integer `adapterVersion`;
- has any policy field outside `automated | applicant | prohibited`;
- does not list `solve_captcha` in `prohibitedActions`;
- marks an action both allowed and prohibited;
- sets `productionEnabled: true` without `productionApprovalStatus:
  'production_approved'`;
- fails to allowlist the hosts of its own URLs in `approvedDomains`;
- has no `driver` object.

**Conservative defaults** (`CONSERVATIVE_DEFAULTS`) are merged under your values.
If you don't know a portal's rule, leave it to the default: applicant-controlled,
no automated payment, no automated submission, personal declaration required.

## 3. Implement the `driver` hooks

The `driver` object supplies the imperative steps the orchestrator calls. In the
mock adapter these call `MockPortal`. In a **real** adapter they drive a
Playwright `page` bound to a Browserbase session:

```js
driver: {
  async register({ email, password, fullName }) {
    await page.goto(this.registrationUrl)            // approvedDomains-checked
    await page.fill('#reg-email', email)
    await page.fill('#reg-password', password)        // sensitive: never to an LLM
    await page.click('#reg-submit')
    if (await page.locator(this.captcha.detect).count()) return { ok: true, captchaToken: '…', needsEmailVerification: true }
    …
  },
  // login, createApplication, uploadDocument, discoverFee, pay, searchAppointments,
  // bookAppointment, rescheduleAppointment, declarePersonally, submit, getApplicationState
}
```

### Execution policy (deterministic-first)

1. Use the adapter **selector** (Playwright) for the action.
2. Verify the element semantically (label/role) before acting.
3. Perform the deterministic action.
4. Verify the postcondition (the portal's own success signal).
5. Only if selectors fail, fall back to **Stagehand `observe()`** with
   *non-sensitive* context, validate the proposed action against
   `allowedActions`, execute one `act()`, verify, and cache by adapter version.
6. Never expose secrets, card data, OTP, or passwords to Stagehand/any LLM.
7. Never let Stagehand navigate outside `approvedDomains`, approve a payment,
   change limits, invent answers, accept a declaration, or pick a slot outside
   preferences.

## 4. Human handoffs are states, not errors

When the portal shows a CAPTCHA / OTP / email verification / identity check /
payment / declaration, **return a result that routes the workflow to the
corresponding handoff state**. The orchestrator pauses and the UI renders a
Browserbase Live View (or the `local_handoff` panel) so the applicant completes
it on the real portal. Your driver then receives only the human's *result
marker*, never their secret.

## 5. Reconciliation

`getApplicationState` must return enough to answer "did my last payment /
booking / submission actually succeed?" (`paid`, `submitted`, `appointment`,
`confirmation`, `receipt`). The orchestrator calls it before any retry so a
timeout never causes a double charge or double submission.

## 6. Test your adapter

Add a test file under `tests/portal/` that runs your adapter against a mock (or
a portal-approved sandbox — **never the live production portal without written
authorization**). Assert the same safety invariants the demo adapter does.

## 7. Approval gate

An adapter ships `productionApprovalStatus: 'mock'` and `productionEnabled:
false`. Moving to production requires:

1. Legal review of the portal's terms of service for automation.
2. A signed operator agreement where the portal requires one.
3. Selectors verified against the live portal.
4. `productionApprovalStatus: 'production_approved'`.
5. Setting `productionEnabled: true` (the validator now permits it).

Until then the orchestrator will not run the adapter against production.
