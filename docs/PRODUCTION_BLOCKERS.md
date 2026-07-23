# Production blockers — Ellis for Trip.com

Honest list of what blocks each runtime mode. Updated 2026-07-23.
Runtime modes: `test | local_mock_demo | tripcom_evaluation | staging | production`
(`ELLIS_RUNTIME_MODE`; MockPortal only ever in the first two).

## Blocks `production` (LIVE_PRODUCTION_READY for any route)

1. **No production-approved live portal adapter exists.** Every route resolves at
   most APPLICANT_HANDOFF_READY. An adapter must pass the adapters_admin
   lifecycle (discovered → … → production_active) with an authenticated human
   administrator approving each activation step; an AI actor cannot approve.
2. **No live Playwright/Browserbase portal driver.** The runtime refuses to bind
   any portal in real-only modes (RealOnlyStop) until a route-specific driver is
   individually built, contract-tested, and approved.
3. **Payment/appointment/submission evidence flows unproven against any real
   portal.** The evidence/reconciliation code paths exist but are mock-tested
   only. Live verification requires an authorized portal account and applicant.
4. **Cloud staging environment does not exist** (no IaC, no Secret Manager
   deployment). All real-provider use so far runs from this workstation.
5. **Clerk production auth not activated** (dev tokens only). `production_preflight`
   blocks default credentials in production but Clerk JWT verification is stubbed.
6. **Trip.com production connector credentials/spec not supplied by Trip.com**
   (`tripcom_admin.health` reports `is_tripcom_production: false`).

## Blocks `staging` / `tripcom_evaluation`

- Same adapter/driver gaps as production (sandbox portals may be used once an
  authorized sandbox and an approved adapter exist).
- A Trip.com-authorized sandbox environment has not been provided.

## Snapshot-data caveats (all modes)

- STRATEGY (2026-07-23): global pre-research was discontinued in favor of
  cached ON-DEMAND route research. 29 destinations carry ingested official
  evidence; any other exact route is researched on demand when an applicant
  enters it (OnDemandRouteResearchJob, Kimi-grounded, honest limits), and the
  verified result is cached for reuse. Routes not yet researched resolve
  NOT_READY (with an automatic focused research job) — never guessed.
  Coverage: see `data/snapshots/2026-07-23/coverage_report.json`
  (matrix completeness is structural, NOT requirements coverage).
- JS-rendered official portals (SPAs) can defeat plain-HTTP fetching; the
  research job then finishes research_incomplete with a review task instead of
  guessing. Browser-rendered fetching is a future activation step.
- Fees/portals/jurisdictions marked anything other than `verified` must never be
  presented as verified; the resolution engine already enforces this.
- Before any irreversible action (payment, booking, submission) a MANUAL live
  reverification of the exact route, portal, and fee is required
  (`POST /admin/snapshot/reverify`) — the snapshot alone is never sufficient.

## Explicitly NOT planned (by design)

- Automatic/recurring rule refresh (`AUTOMATIC_RULE_REFRESH_ENABLED=false`,
  manual reverification only).
- Any CAPTCHA/OTP/anti-bot bypass; applicant-personal steps stay personal.
