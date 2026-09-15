# September 15 maintenance and scheduled-refresh pause

## Operator instruction: keep the six-hour refresh disabled

On September 15, 2026, the owner explicitly requested pausing the automatic
six-hour refresh because of its token cost, until they request reactivation.
They clarified that this instruction refers only to the six-hour scheduled run.
Manual Refresh and Add a route remain available; other on-access behavior is
unchanged. Do not enable or start `ellis-freshness.timer` during deployment.

At 19:47:45 UTC the production timer was `inactive`, `disabled`, with no next
firing. The service had `MainPID=0`; its previous provider-balance failure remains
recorded rather than being erased. The pause survives server restart.

The previous completed six-hour run recorded 8,653,110 tokens: 5,639,738 uncached
input, 2,518,016 cached input and 495,356 output. At Kimi K3's published rates of
$3 / $0.30 / $15 per million respectively, recorded usage costs $25.1049588 USD.
This excludes unknown usage from unsuccessful requests and is not an invoice.
Pricing source: https://platform.kimi.ai/docs/pricing/chat (checked September 15).

A durable $9 per-cycle provider budget has been prepared as an additional
protection for a future reactivation. It does not enable the scheduler and does
not limit manually initiated API requests. Unknown request usage keeps its full
reservation; unsupported request types fail closed. No incomplete check receives
verification credit.

## Work in this release

- Faster QC tab loading, server-validated conditional Records reads, and deferred
  rendering of history and resolved issues.
- Saved official-policy quote recovery; no new policy fact is inferred by the
  quote adapter.
- Product-container quotes must support the particular field and product before
  appearing in its Quotes drawer.
- Explicit English and Chinese paused-state display for the disabled schedule.
- Narrow official-source corrections for USA–Pakistan, Canada–Vietnam,
  India–Japan and India–Spain, with issue dispositions audited separately from
  data writes. Deployment and final row verification are recorded in the release
  receipts; this document alone is not proof they have completed.

Local working evidence and deployment receipts are under
`work/resume20260915` in the parent workspace. No blanket assertion that all
historical route fields are verified or current is made by this maintenance.
