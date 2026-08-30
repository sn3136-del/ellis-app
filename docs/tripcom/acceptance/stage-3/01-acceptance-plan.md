# Stage 3 acceptance plan 验收计划
**灰度验收 Canary Acceptance**

Project: T-Station Visa Information Base 项目：T站签证信息库建设
Generated from the live system at 2026-08-30 13:57 UTC.

## Scope of this stage 本阶段范围
Go live on 1 to 2 T-Station sites and verify real data quality.

## Exit criteria 出口标准
Gray-site spot check passes, no blocking issues.

## What will be examined 检查内容
1. The four deliverables P0 to P3, reachable in a browser with no installation.
2. The 25-field dataset, its enumerations and its required flags.
3. The Excel export: two worksheets, field descriptions and data.
4. The change log and the issue feedback loop.
5. The quantified metrics in chapter 6.

## Method 方法
Every figure in the checklist is read from the running system through its own
API at the time of generation, not from documentation. Any figure that cannot
be measured is recorded as unmeasured rather than assumed.

## Signatures 签署
| Party | Name | Date |
|---|---|---|
| Party A 甲方 (Trip.com Group) | | |
| Party B 乙方 (Ellis Intelligence, Inc.) | | |
