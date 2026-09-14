# Reviewer labels and application instructions

Requested by the owner: use AI/Ellis attribution instead of provider or coding-agent names in Ellis, and show How to apply only when instructions are available.

## Changes

- QC data-source labels and the customer workbook now use AI review or Ellis review for branded reviewer identities. QC notes, change-log display, admin actor labels and displayed API errors use neutral wording. English and both Chinese modes are covered.
- Official quotations and URLs retain their exact text. Internal reviewer IDs and raw audit records retain their original identities; this is a presentation change, not a change to who performed a review.
- How to apply now displays the published answer's stored application instructions when a separate ordered procedure is unavailable. Only explicit source-ordered steps receive numbering; other saved instructions use bullets. Instructions belonging to a different visa product remain under that product's name.
- Empty, placeholder-only, link-only and no-application cards are hidden. Supported visa-free/no-filing aliases cannot revive stale payment/submission steps. Held answers remain held.

## Verification

Focused tests exercise actual QC fields, the real Excel export, change-log formatting, the real customer screen, applicable visa-free aliases, placeholder filtering and preservation of exact evidence. The full frontend suite passed 472 tests; the focused backend suite passed 90 tests, and the final installer passed 37 tests. Live API comparison covered all 1,453 records: only 93 data-source labels and their corresponding evidence revision hashes changed. Live Excel labels also read AI review.

This work does not perform a new historical source audit or change route facts, confidence, publication decisions, or the freshness schedule. A live browser smoke was prepared but could not run: the sandbox rejected Chrome launch and CUA reported User unavailable. Actual React component tests cover both present and absent application cards; deployed asset hashes match the tested build. Live API/Excel checks passed, with no paid lookup or refresh requests.

## Deployed

Frontend publication20260913ab (source a7c50c2) went live at 2026-09-14T04:05:14Z. Backend runtime20260914a (tested backend source ed9302b, identical selected runtime files at a7c50c2) went live at 2026-09-14T04:09:11Z. Public API health returned ok. Git audit commits do not change runtime code.

## Existing freshness failure

The 00:26 UTC scheduled run had already stopped at 00:46 UTC after persistent Kimi 429 rate limits: 109 attempts, 98 source reads, 10 verified, and 840 remaining cycle routes. Its timer remains active and enabled. The initial backend release preflight stopped before replacing code because this failed state differed from its expected successful idle state. The final narrow installer explicitly pinned and preserved that observed prior failure; it restarted only the API and sent no scheduler signals, reset no freshness status, and changed no database or cron data. This release does not claim that the prior failed sweep is fixed.
