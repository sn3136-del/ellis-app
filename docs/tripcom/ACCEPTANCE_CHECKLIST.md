# Acceptance Checklist and Stage Test Record

Five stages per the Acceptance & Delivery Standard; each stage closes with
this checklist, the stage test report, and both parties' signatures.

## Metric checklist (section 6.1 of the standard)
| Metric | Target | Measured (date/value) | Pass |
|---|---|---|---|
| Functional completeness | 100% of required functions | 2026-08-28: all enumerated items live-verified | yes |
| Field completeness | >= 99% | 2026-08-28: 98.35% field-level (21,440/21,800 cells); 85.2% record-level | NO |
| Accuracy (sampled vs official) | >= 99.5% (policy fields 100%) | internal: 52 sampled, 10 corrected, re-samples clean; formal sampling is Party A's (2.2) | pending Party A |
| Confidence Medium-or-above | >= 90% | 2026-08-28: 87.4% (High 796, Medium 158, Low 138; Lows blocked) | NO |
| Source coverage (official URL) | 100% | 2026-08-28: 100.0% (1,092/1,092) | yes |
| Station coverage | >= 18 | 2026-08-28: 18/18 Phase-1 stations, 187 destinations | yes |
| Availability (monthly) | >= 99.99% | 100.0% since per-minute monitoring began 2026-08-28 (record younger than a month) | evidence young |
| Recovery time (RTO) | <= 1h | auto-restart (systemd + probe), daily backups, runbook | yes |
| Policy update timeliness | <= 48h (urgent 24h) | pipeline live, same-day corrections shipped; no external policy event yet in window | evidence young |
| Error feedback closure | <= 48h | 2026-08-28 full recheck: zero disputes older than 48h; 142 fresh engine-vs-official conflicts queued (must be worked within 2 days); historical backlog exceeded the window before it cleared | partial |
| Quality backend availability | pass | exercised live 2026-08-28 incl. phone widths | yes |

## Functional checklist
- [x] Query tool: six inputs, full outputs, subcategories, transit answers (2026-08-28)
- [x] Display data: per-combination page, document switcher, purpose filter (2026-08-28)
- [x] AI Q&A: natural language (zh + en), knowledge-base answers, sources (2026-08-28)
- [x] Quality backend: spot check, per-field checklist, issue loop,
      confidence gating, source traceability, change log, Excel + CSV export (2026-08-28)
- [x] Localization: full content switch in zh-CN and zh-Hant (2026-08-28)
- [x] Online access: browser only, no installation; phone-optimized to 320px (2026-08-28)

## Stage sign-off
| Stage | Date | Party A (Trip.com) | Party B |
|---|---|---|---|
| 1. Contractor self-test | | | |
| 2. Joint debugging | | | |
| 3. Canary (1-2 stations) | | | |
| 4. Full acceptance (>= 18) | | | |
| 5. Ongoing monitoring | | | |
