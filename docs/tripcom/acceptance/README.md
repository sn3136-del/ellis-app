# Acceptance archive 验收过程档案
Generated from the live system at 2026-08-30 11:13 UTC.

Section 5.3 of the Acceptance and Delivery Standard requires each of the five
stages to archive seven artefacts plus the signed acceptance report. This
directory holds one folder per stage, each containing all eight.

| Stage | Folder | Exit criteria |
|---|---|---|
| 1 | `stage-1/` | Self-test passed; online link accessible. |
| 2 | `stage-2/` | No outstanding issues, or waivers confirmed in writing by Party A. |
| 3 | `stage-3/` | Gray-site spot check passes, no blocking issues. |
| 4 | `stage-4/` | All quantitative metrics meet Chapter 6. |
| 5 | `stage-5/` | Sustained compliance; final report signed. |

## What is measured and what is not
Every metric in these documents is read from the running system at generation
time through its own API. Verdict columns, rosters, minutes and signature
blocks are deliberately empty: they record events between Party A and Party B
and cannot be produced by the party being audited.

## Current measured position
| Metric 指标 | Target 目标 | Measured 实测 | Meets 达标 |
|---|---|---|---|
| Field completeness 字段完整度 | ≥99% | **98.92%** | NO |
| Confidence, Medium or above 置信度中等及以上 | ≥90% | **95.53%** | yes |
| Source coverage 信源覆盖率 | 100% | **100.00%** | yes |
| Station coverage 站点覆盖率 | ≥18 | **18/18** | yes |
| Error feedback closure 错误反馈时效 | 0 past 48h | **78 past 48h** | NO |
| Records delivered 记录数 | n/a | **1118** | yes |
| Held from customers 暂不对客 | n/a | **50** | yes |

Regenerate with `python3 scripts/generate_acceptance_archive.py`.
