# Acceptance Checklist and Stage Test Record

Five stages per the Acceptance & Delivery Standard; each stage closes with
this checklist, the stage test report, and both parties' signatures.

## Metric checklist (section 6.1 of the standard)
| Metric | Target | Measured (date/value) | Pass |
|---|---|---|---|
| Functional completeness | 100% of required functions | | |
| Field completeness | >= 99% | | |
| Accuracy (sampled vs official) | >= 99.5% (policy fields 100%) | | |
| Confidence Medium-or-above | >= 90% | | |
| Source coverage (official URL) | 100% | | |
| Station coverage | >= 18 | | |
| Availability (monthly) | >= 99.99% | | |
| Recovery time (RTO) | <= 1h | | |
| Policy update timeliness | <= 48h (urgent 24h) | | |
| Error feedback closure | <= 48h | | |
| Quality backend availability | pass | | |

## Functional checklist
- [ ] Query tool: six inputs, full outputs, subcategories, transit answers
- [ ] Display data: per-combination page, document switcher, purpose filter
- [ ] AI Q&A: natural language (zh + en), knowledge-base answers, sources
- [ ] Quality backend: spot check, per-field checklist, issue loop,
      confidence gating, source traceability, change log, Excel export
- [ ] Localization: full content switch in zh-CN and zh-Hant
- [ ] Online access: browser only, no installation

## Stage sign-off
| Stage | Date | Party A (Trip.com) | Party B |
|---|---|---|---|
| 1. Contractor self-test | | | |
| 2. Joint debugging | | | |
| 3. Canary (1-2 stations) | | | |
| 4. Full acceptance (>= 18) | | | |
| 5. Ongoing monitoring | | | |
