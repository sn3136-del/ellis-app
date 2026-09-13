# Deployed UI readability fixes

Frontend release `publication20260913aa`, source commit `8df05c5`, deployed at 2026-09-13T22:36:00.557711+00:00.

- Change-log objects render as readable field/value summaries; source metadata has a source-record and page count. Changes remain visible when their summary counts happen to match. Full audit values and exports are preserved.
- AI-reviewed updates use their own new evidence for the authorship label; an operator update alone no longer implies human verification. The source-link tooltip is neutral.
- The traveler “Checks and appointments” card is hidden whenever it would contain an unconfirmed requirement or applicability. Stored guidance and QC data were not changed.

All 459 frontend tests passed; the web build and release-secret scan passed. The deployed change log was verified in English and Simplified Chinese, including the actual Thailand → Australia source update. The deployed customer renderer was checked using a locally replayed, previously captured live response with unconfirmed checks: the card was absent and the rest of the answer rendered. That replay made no live lookup/research call and does not change or certify the current Canada → Vietnam data.

The frontend-only deployment preserved the backend, database and freshness schedule. The prior four-route source corrections remain in place.
