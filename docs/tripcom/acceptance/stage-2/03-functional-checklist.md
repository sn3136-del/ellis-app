# Stage 2 functional checklist 功能清单
**联调验收 Joint Debug Acceptance** · measured 2026-08-30 11:46 UTC

Verdicts are left blank for Party A to complete. The evidence column is
measured from the live system.

| # | Requirement 需求 | Evidence 证据 | Party A verdict |
|---|---|---|---|
| 1 | P0 Information Quality Control Backend reachable | https://ellis-visa.com/ops | |
| 2 | P1 Visa Information Query Tool, six inputs | https://ellis-visa.com | |
| 3 | P2 Display page by passport x destination | https://ellis-visa.com | |
| 4 | P3 AI Q&A with source annotation | POST /api/database/ask | |
| 5 | Multi-dimensional spot check, combinable | console filter bar: passport, passport type, destination, purpose, requirement, confidence, visa type, missing field | |
| 6 | Per-field checklist over the 25 fields | field_status on every record | |
| 7 | Change management, add / modify / delete highlighted | /api/database/changes | |
| 8 | Issue loop: flag, notify, correct, review, publish | /api/database/issues | |
| 9 | Confidence labelling, low withheld from customers | 35 records held | |
| 10 | Source traceability, clickable official URL | 100.00% coverage | |
| 11 | Batch Excel export by filter | /api/database/export.xlsx | |
| 12 | AI Q&A answers logged for sampling | /api/database/asks | |
