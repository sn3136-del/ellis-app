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

A durable $9 per-cycle provider budget has been deployed as an additional
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

## Confirmed deployment and bounded closeout

The owner limited this work to 45 minutes, from 19:56:13 to 20:41:13 UTC on
September 15. New research stopped before the final ten-minute deployment and
verification window. Further official-source auditing is not scheduled.

- Runtime A was installed at 20:03:28 UTC, the frontend at 20:05:10 UTC, and
  the narrow Visa Code passport-authority fix at 20:22:16 UTC. All API health
  checks passed; all deployments preserved the disabled six-hour timer.
- Live cached customer reads at 20:25 confirmed USA–Pakistan, Canada–Vietnam,
  India–Japan and India–Spain all published, covering eight Medium products,
  without a manual publication bypass or an active hold. The associated five
  flags were resolved through revision-pinned source-review workflows.
- India–Japan now separates the INR500 official fee for Indian nationals
  applying in India from agency charges. India–Spain and Canada–Vietnam retain
  the distinct official passport-validity conditions with their source and
  jurisdiction qualifications. USA–Pakistan now has the source-backed mission
  document requirements and USD60 selected government fee, without inventing
  a 90-day duration from “less than three months”.
- A France–Russia flag proposed deleting insurance based on a FAQ answer about
  missing insurance data in an application. The current MFA conditions still
  require insurance subject to reciprocity exceptions, so that deletion
  proposal was dismissed. This did not certify every clause of the health field.
- Three previously corrected Canada–Taiwan flags were independently checked
  against current BOCA instructions and completed through review/publication:
  arrival-card timing, onward-document conditions, and the Canadian/UK
  extension note. Nine flags across six routes were closed in total.
- At 20:33:04 UTC, 11 routes between the 18 stations received a quote-only
  update: France, India, Philippines, Taiwan, Japan, Singapore, Indonesia,
  Vietnam and Spain to Russia; USA to Japan; and Canada to Taiwan. The batch
  adds 45 factual field cells with quotations, plus four display-unit aliases.
  Exact policy facts, grades and publication indicators were preserved, as
  were existing quote+URL entries. Hong Kong–Japan was excluded because adding
  provenance would invalidate a legacy warning-resolution pin; no old warning
  or corrected fact was silently reinstated.
- QC browser checks measured Records 1.59s, Issues 0.82s, Changes 0.72s, AI Q&A
  0.24s and Freshness 0.21s, with a 0.21s validated Records revisit. These are
  measured test timings, not a universal latency guarantee.

The quote batch and four-route data corrections are in the production operator
store, with transaction receipts and backups; they are not a wholesale database
replacement. Runtime source commits are eda39e8 and 651375c. The earlier quote
adapter recovered 95 saved factual quote cells, while stricter product/field
ownership removed misleading cross-product attributions; raw quote totals should
not be treated as a monotonically increasing quality score.

The most recent scheduled attempt began September 15 at 18:25:32 UTC and stopped
at 18:26:45 for insufficient provider balance. It attempted four routes, recorded
source reads for three, and verified or corrected none. The last completed run
was September 14, 18:26–20:49 UTC: 949 routes attempted, 836 with source reads,
40 verdicts verified and 27 disputes created, with no corrections or full-detail
renewals. Its known usage was 8,653,110 tokens, estimated at USD25.10; unknown
usage on failed requests is excluded. The owner-requested pause remains in force.

Remaining low/unpublished records and unsupported fields were not bulk-certified.
This release is not a declaration of 99% source support or universal accuracy.

## Final read-only inventory, 20:36 UTC

The deployed projection contains 1,600 factual field cells with an official
quote for routes where either endpoint is one of the 18 stations (814 route
keys, 1,220 product rows). Between the stations there are 1,307 such cells across
331 route keys and 547 product rows; route keys include different purposes and
document variants, so this is not the 306-pair tourism sample denominator.
The final 11-route quote batch accounts for 45 new factual quoted cells.

Nine route keys between the stations still have a Low or held product: eight
have Low confidence and nine have a held product (overlapping groups). The
broader either-endpoint scope has 132 affected route keys. These are remaining
work, not automatically approved records. Quote presence counts do not establish
that every clause of a composite field was checked, nor guarantee current policy.
