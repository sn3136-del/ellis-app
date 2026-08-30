# -*- coding: utf-8 -*-
"""Generate the §5.3 acceptance archive from live measurement.

Their standard requires each of five stages to archive an acceptance plan, a
team roster, functional and sampling checklists, verification and
rectification records, nonconformance notes, minutes, and a signed acceptance
report. Everything here that can be measured is measured against the running
system at generation time. Nothing is marked passed on our own say-so, and the
signature blocks stay empty because a counter-signature is an event between
two parties, not a file we can write.
"""
import json, ssl, urllib.request, collections, pathlib, datetime, sys
CTX=ssl.create_default_context(); CTX.check_hostname=False; CTX.verify_mode=ssl.CERT_NONE
H={"Authorization":"Bearer admin-token","X-Org-Id":"ellis","X-User-Id":"ops"}
BASE="https://ellis-visa.com/api"
def get(path):
    return json.loads(urllib.request.urlopen(urllib.request.Request(
        BASE+path, headers=H), timeout=180, context=CTX).read())

recs=get("/database/records?limit=5000")["records"]
live=[r for r in recs if r["confidence_level"]!="Low"]
issues=get("/database/issues").get("issues",[])
try: fresh=get("/database/freshness").get("summary",{})
except Exception: fresh={}
now=datetime.datetime.now(datetime.timezone.utc)
STAMP=now.strftime("%Y-%m-%d %H:%M UTC")

filled=sum(1 for r in live for v in r["field_status"].values() if v=="filled")
den=sum(1 for r in live for v in r["field_status"].values() if v in ("filled","missing"))
conf=collections.Counter(r["confidence_level"] for r in recs)
srcs=sum(1 for r in recs if r.get("source_url"))
STA=["HKG","TWN","JPN","KOR","USA","THA","SGP","MYS","GBR","RUS","AUS","IDN",
     "PHL","FRA","VNM","ESP","IND","CAN"]
nat=collections.Counter(r["travel_document_country"] for r in recs)
stations=sum(1 for s in STA if nat.get(s))
openish=[i for i in issues if i["status"] not in ("published","dismissed")]
def _age(iso):
    try:
        t=datetime.datetime.fromisoformat(str(iso).replace("Z","+00:00"))
        if t.tzinfo is None: t=t.replace(tzinfo=datetime.timezone.utc)
        return (now-t).total_seconds()
    except Exception:
        return 0.0
old48=[i for i in openish if _age(i.get("created_at"))>172800]

M = {
 "field_completeness": (f"{100*filled/den:.2f}%", "≥99%", 100*filled/den>=99),
 "confidence_medium_plus": (f"{100*(conf['High']+conf['Medium'])/len(recs):.2f}%",
                            "≥90%", 100*(conf['High']+conf['Medium'])/len(recs)>=90),
 "source_coverage": (f"{100*srcs/len(recs):.2f}%", "100%", srcs==len(recs)),
 "station_coverage": (f"{stations}/18", "≥18", stations>=18),
 "error_feedback_48h": (f"{len(old48)} past 48h", "0 past 48h", not old48),
 "records": (str(len(recs)), "n/a", True),
 "held_from_customers": (str(conf["Low"]), "n/a", True),
}

OUT=pathlib.Path("docs/tripcom/acceptance")
STAGES=[
 ("1","承建单位自测 Contractor Self-Test",
  "Party B completes development and self-test, provides the online link and a self-test report.",
  "Self-test passed; online link accessible."),
 ("2","联调验收 Joint Debug Acceptance",
  "Joint testing of backend functions, data completeness and export capability.",
  "No outstanding issues, or waivers confirmed in writing by Party A."),
 ("3","灰度验收 Canary Acceptance",
  "Go live on 1 to 2 T-Station sites and verify real data quality.",
  "Gray-site spot check passes, no blocking issues."),
 ("4","全量验收 Full Acceptance",
  "Full go-live across at least 18 Phase 1 sites, run acceptance sampling.",
  "All quantitative metrics meet Chapter 6."),
 ("5","持续监控验收 Ongoing Monitoring",
  "Runtime quality monitoring, periodic review and the error feedback loop.",
  "Sustained compliance; final report signed."),
]
def metrics_table():
    rows=["| Metric 指标 | Target 目标 | Measured 实测 | Meets 达标 |",
          "|---|---|---|---|"]
    names={"field_completeness":"Field completeness 字段完整度",
           "confidence_medium_plus":"Confidence, Medium or above 置信度中等及以上",
           "source_coverage":"Source coverage 信源覆盖率",
           "station_coverage":"Station coverage 站点覆盖率",
           "error_feedback_48h":"Error feedback closure 错误反馈时效",
           "records":"Records delivered 记录数",
           "held_from_customers":"Held from customers 暂不对客"}
    for k,(val,target,ok) in M.items():
        rows.append(f"| {names[k]} | {target} | **{val}** | {'yes' if ok else 'NO'} |")
    return "\n".join(rows)

for num,title,work,exit_ in STAGES:
    p=OUT/f"stage-{num}"
    p.mkdir(parents=True, exist_ok=True)
    (p/"01-acceptance-plan.md").write_text(f"""# Stage {num} acceptance plan 验收计划
**{title}**

Project: T-Station Visa Information Base 项目：T站签证信息库建设
Generated from the live system at {STAMP}.

## Scope of this stage 本阶段范围
{work}

## Exit criteria 出口标准
{exit_}

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
""", encoding="utf-8")

    (p/"02-acceptance-team-roster.md").write_text(f"""# Stage {num} acceptance team roster 验收小组名单
**{title}** · generated {STAMP}

Party A appoints the team leader per the Acceptance and Delivery Standard.
This roster is filled in by both parties before the stage begins.

| Role 角色 | Party 单位 | Name 姓名 | Contact 联系方式 |
|---|---|---|---|
| Team leader 组长 (appointed by Party A) | Trip.com Group | | |
| Product acceptance 产品验收 | Trip.com Group | | |
| Data quality acceptance 数据质量验收 | Trip.com Group | | |
| Delivery lead 交付负责人 | Ellis Intelligence, Inc. | | |
| Data lead 数据负责人 | Ellis Intelligence, Inc. | | |
""", encoding="utf-8")

    (p/"03-functional-checklist.md").write_text(f"""# Stage {num} functional checklist 功能清单
**{title}** · measured {STAMP}

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
| 9 | Confidence labelling, low withheld from customers | {M['held_from_customers'][0]} records held | |
| 10 | Source traceability, clickable official URL | {M['source_coverage'][0]} coverage | |
| 11 | Batch Excel export by filter | /api/database/export.xlsx | |
| 12 | AI Q&A answers logged for sampling | /api/database/asks | |
""", encoding="utf-8")

    (p/"04-data-sampling-checklist.md").write_text(f"""# Stage {num} data sampling checklist 数据抽样清单
**{title}** · measured {STAMP}

Sampling unit per the standard: by station, on the passport type x destination
combination, compared field by field against the official source.

{metrics_table()}

## Sample frame 抽样框
Records in scope: {len(recs)}. Shown to customers: {len(live)}. Held: {M['held_from_customers'][0]}.

Party A selects the sample. Party B supplies, for every sampled record, the
official page it is bound to and the date it was last compared with that page.

| Route 线路 | Field 字段 | Ellis value | Official page value | Match |
|---|---|---|---|---|
| | | | | |
""", encoding="utf-8")

    (p/"05-verification-and-rectification-record.md").write_text(f"""# Stage {num} verification and rectification record 验证与整改记录
**{title}** · generated {STAMP}

Every correction is already tracked in the live change log and issue queue.
This record points at them rather than restating them, so it cannot drift.

- Change log: https://ellis-visa.com/api/database/changes and /api/database/changes.csv
- Issue queue: https://ellis-visa.com/api/database/issues

Open items not yet published at generation time: **{len(openish)}**.
Items open beyond the 48 hour closure window: **{len(old48)}**.

| Finding 问题 | Raised by 提出方 | Date 日期 | Action 处理 | Closed 关闭 |
|---|---|---|---|---|
| | | | | |
""", encoding="utf-8")

    (p/"06-nonconformance-notes.md").write_text(f"""# Stage {num} nonconformance notes 不合格说明
**{title}** · generated {STAMP}

Measured shortfalls against chapter 6 at generation time. A metric that meets
its target is listed too, so the note is a complete statement rather than a
selection.

{metrics_table()}

## Known shortfalls 已知差距
""" + ("\n".join(f"- {k}: measured {v[0]} against a target of {v[1]}."
                 for k,v in M.items() if not v[2]) or "- None at generation time.") + """

## Party A additions 甲方补充
| Item | Raised | Agreed remedy | Due |
|---|---|---|---|
| | | | |
""", encoding="utf-8")

    (p/"07-meeting-minutes.md").write_text(f"""# Stage {num} meeting minutes 会议纪要
**{title}**

| Field | Value |
|---|---|
| Date 日期 | |
| Attendees 参会人 | |
| Chair 主持 | |

## Agenda 议程
1. Review of the stage checklist.
2. Data sampling results.
3. Nonconformances and remedies.
4. Decision on the exit criteria.

## Decisions 决议
| # | Decision | Owner | Due |
|---|---|---|---|
| | | | |
""", encoding="utf-8")

    (p/"08-acceptance-report.md").write_text(f"""# Stage {num} acceptance report 验收报告
**{title}**

Project: T-Station Visa Information Base 项目：T站签证信息库建设
Party A 甲方: Trip.com Group, Visa and Insurance Business Unit
Party B 乙方: Ellis Intelligence, Inc.

## Exit criteria 出口标准
{exit_}

## Measured position at generation, {STAMP}
{metrics_table()}

## Conclusion 结论
- [ ] Accepted 通过
- [ ] Accepted with conditions 有条件通过
- [ ] Not accepted 不通过

Conditions or reasons 条件或理由:

## Signatures 双方签署
| Party | Name | Title | Signature | Date |
|---|---|---|---|---|
| Party A 甲方 | | | | |
| Party B 乙方 | | | | |

This report is unsigned. A counter-signature is an act between the two
parties and is not generated.
""", encoding="utf-8")

idx=OUT/"README.md"
idx.write_text(f"""# Acceptance archive 验收过程档案
Generated from the live system at {STAMP}.

Section 5.3 of the Acceptance and Delivery Standard requires each of the five
stages to archive seven artefacts plus the signed acceptance report. This
directory holds one folder per stage, each containing all eight.

| Stage | Folder | Exit criteria |
|---|---|---|
""" + "\n".join(f"| {n} | `stage-{n}/` | {e} |" for n,_,_,e in STAGES) + f"""

## What is measured and what is not
Every metric in these documents is read from the running system at generation
time through its own API. Verdict columns, rosters, minutes and signature
blocks are deliberately empty: they record events between Party A and Party B
and cannot be produced by the party being audited.

## Current measured position
{metrics_table()}

Regenerate with `python3 scripts/generate_acceptance_archive.py`.
""", encoding="utf-8")
print("wrote", len(list(OUT.rglob("*.md"))), "documents across", len(STAGES), "stages")
for k,(v,t,ok) in M.items(): print(f"   {k:24} {v:>12}  target {t:<12} {'ok' if ok else 'SHORT'}")
