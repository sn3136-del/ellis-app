**Subject:** T-Station information base: both defects closed, current status, and formal quotation

Dear Hazel,

Thank you for the detailed review. Both defects your team raised are closed. Below is exactly what changed, what the database looks like right now, and the formal quotation with the arithmetic behind it.

---

## 1. The two demo defects

**Over-collection of information.** Your team was looking at the applicant processing flow, which was never part of this project. It has been removed entirely. The build at https://ellis-visa.com is the information base and nothing else. The query tool asks for exactly the six inputs your requirements document specifies: travel document type, nationality, travel purpose, departure location, destination, and an optional transit point. There is no passport number field and no address field anywhere in the product. Please re-test and tell me if you see otherwise.

**Localization.** This was a real defect and you were right to call it critical. The interface text switched languages but the record content did not, because a length limit in the translation layer was silently skipping longer values. The limit is removed. Record content now translates in full, including visa product names, notes, document checklists, entry conditions and transit rules. Switch to 简体中文 on any route and the entire answer is Chinese.

---

## 2. Current state of the site, measured

Every figure below is live now and re-computable from the Excel export.

| Acceptance metric (§6.1) | Target | Live today |
|---|---|---|
| Source coverage | 100% | **100.0%** (1,125 of 1,125 records) |
| Field completeness | ≥99% | **98.4%** (22,138 of 22,500 required cells) |
| Confidence at Medium or above | ≥90% | **94.9%** |
| Station coverage | ≥18 | **18 of 18** present |
| System availability | ≥99.99% | **100.0%** since monitoring began, 41 ms median |
| Error feedback closure | ≤48h | **61 open, none older than 48 hours** |

Field completeness is the one metric still short, by 0.6 points, and it is closing.

Two design decisions sit behind those numbers, and they speak directly to your point 2.

A record cannot carry a source unless that source is a government domain. This is enforced when data loads, not by review afterwards, so an unofficial citation cannot enter the database at all.

Any record graded Low confidence is withheld from customers until an operator releases it. The quality console shows which records are held and why. Nothing reaches a customer that the system cannot produce a government page for.

The correction loop is running on its own. The monitor re-reads official pages, files a discrepancy when a page and a record disagree, and holds it for a human ruling. That is what the 61 open items are. They are the loop working, not a backlog, and none has been open longer than 48 hours.

Your two designated test stations are at full depth now. Hong Kong covers 134 destinations and the United States covers 141. The other 16 stations are live and reach the same depth on the Stage 4 schedule below.

---

## 3. Quotation

I have used your in-house estimate as the benchmark, as you suggested. Here is the arithmetic on both sides.

**What the recurring obligation costs, by itself.** Your §4.3 requires a full data review every month, policy updates inside 48 hours, error closure inside 48 hours, and quarterly bidirectional sampling. At 1,125 records, a trained analyst re-verifying one record against its official page takes 5 to 8 minutes, so the monthly review alone is 95 to 150 hours. Add continuous policy monitoring across 187 destinations, the dispute desk that the 48 hour clock requires, and the quarterly sampling, and the standing load is 0.5 to 1.5 analysts, permanently. That is maintenance only. It produces no console, no API, no display pages, no assistant and no acceptance evidence.

**What the build costs.** Two engineers and a data lead, 16 to 20 person months. What is running today is 9,697 lines in the database core, 5,115 in the API layer, 2,419 in the quality console, 1,665 in the customer display page, a trilingual layer holding 39,609 cached translations, 121 backend test files, 11 live endpoints, 1,102 individually sourced field corrections and seven acceptance documents.

**Phase 1, one time**

| Item | Qty | Unit (USD) | Amount (USD) |
|---|---|---|---|
| Platform: all four deliverables P0 to P3, quality control backend, dataset pipeline, Excel and change-log export, acceptance document set | 1 | 34,000 | 34,000 |
| Station activation to acceptance depth | 18 | 3,000 | 54,000 |
| **Phase 1 total** | | | **88,000** |

**Operations, monthly from full go-live**

| Item | Monthly (USD) |
|---|---|
| Hosting with redundancy for the 99.99% commitment, external monitoring, daily backup with 1 hour RTO, 48 hour policy updates and 24 hour urgent updates, 48 hour error closure, monthly full data review, quarterly bidirectional sampling | **5,200** |

**Year one, Phase 1 plus operations: USD 150,400.**

Set that against the alternative. The in-house route is 16 to 20 person months of build before anything is usable, plus 0.5 to 1.5 analysts every month thereafter, and the first version reaches your operations team around month five. Ellis is in front of your team this week, already meeting four of the six acceptance metrics, with the fifth 0.6 points away.

---

## 4. Payment terms

Payment on acceptance and verification is acceptable. I would ask only that the tranches follow your own five stage process, so that each payment is tied to an acceptance you have already signed:

30% on Stage 2, functional acceptance.
30% on Stage 3, grey release. Hong Kong and the United States are at depth now, so this stage is ready when you are.
40% on Stage 4, full acceptance across all 18 stations.
Operations billed monthly from full go-live.

Prices exclude taxes. This quotation is valid for 60 days.

---

## 5. Procurement materials

Attached: company profile, certificate of incorporation, and the federal tax identification letter. Tell me what else your audit and legal teams need and I will turn it around the same day.

The environment is live at https://ellis-visa.com, and the quality control backend your standard treats as the gating deliverable is at https://ellis-visa.com/ops. I would rather your operations team spent an hour inside it than read another document, so please send me the accounts you want provisioned.

Best regards,

Sammy
Co-founder and CEO of Ellis

---

# 中文版

**主题：** T站签证信息库：两项缺陷已修复、当前状态与正式报价

尊敬的Hazel：

感谢贵司的详细评审。您提出的两项缺陷均已修复。以下是具体的修改内容、数据库的当前状态，以及正式报价与其测算依据。

## 一、两项演示缺陷

**信息过度收集。** 贵司团队看到的是申请办理流程，该流程从未属于本项目范围，现已完全移除。https://ellis-visa.com 上的版本仅为信息库本身。查询器仅要求需求文档规定的六项输入：出行证件类型、国籍、出行目的、出发地、目的地，以及可选的中转点。产品中不存在任何护照号码栏位或地址栏位。烦请复测，如仍有发现请告知。

**本地化失败。** 这确是真实缺陷，贵司判定为严重问题是正确的。界面文案可切换，但记录内容未切换，原因是翻译层的长度上限静默跳过了较长的内容值。该上限已移除，记录内容现已完整翻译，包括签证产品名称、注意事项、材料清单、入境条件与中转规则。在任意线路切换至简体中文，整个答案均为中文。

## 二、网站当前状态（实测）

以下数据均为实时数据，可从Excel导出文件中复核。

| 验收指标（§6.1） | 目标 | 当前实测 |
|---|---|---|
| 来源覆盖率 | 100% | **100.0%**（1,125/1,125条） |
| 字段完整率 | ≥99% | **98.4%**（22,138/22,500个必填单元格） |
| 置信度中等及以上 | ≥90% | **94.9%** |
| 站点覆盖 | ≥18 | **18/18** 全部就位 |
| 系统可用性 | ≥99.99% | **100.0%**（自监控启用以来），中位延迟41毫秒 |
| 问题反馈闭环 | ≤48小时 | **61条待处理，无一超过48小时** |

字段完整率是唯一尚未达标的指标，差距0.6个百分点，正在收敛。

支撑这些数字的有两项设计决策，直接回应贵司第2点。

记录的来源必须是政府域名，否则无法写入。该校验在数据载入时强制执行，而非事后人工复核，因此非官方来源根本无法进入数据库。

任何被判定为低置信度的记录，在运营放行前不对客展示。质量管控后台可查看哪些记录被暂扣及其原因。凡是无法出示对应官方页面的信息，均不会送达客户。

修正闭环正在自主运行。监控程序重读官方页面，当页面与记录不一致时自动立案，交由人工裁定。这正是那61条待处理项的来源。它们代表闭环在运转，而非积压，且无一超过48小时。

贵司指定的两个测试站点已达完整深度：中国香港覆盖134个目的地，美国覆盖141个目的地。其余16个站点已上线，并将按下述第四阶段计划达到同等深度。

## 三、报价

按贵司建议，我们以贵司内部成本预估作为基准，双方测算如下。

**仅经常性运维义务的成本。** 贵司§4.3要求每月全量数据复核、48小时内政策更新、48小时内问题闭环、每季度双向抽样。以1,125条记录计，训练有素的分析师对照官方页面复核一条记录需5至8分钟，仅月度复核即为95至150小时。再加上覆盖187个目的地的持续政策监控、48小时时限所需的问题处理值守，以及季度抽样，常态工作量为0.5至1.5名分析师，且需长期投入。这仅是维护，不产出后台、API、展示页、助手或验收证据。

**建设成本。** 两名工程师加一名数据负责人，16至20人月。当前在线运行的包括：数据库核心9,697行、API层5,115行、质量管控后台2,419行、客户展示页1,665行、含39,609条缓存翻译的三语层、121个后端测试文件、11个线上接口、1,102条逐条溯源的字段修正，以及七份验收文档。

**一期，一次性**

| 项目 | 数量 | 单价（美元） | 金额（美元） |
|---|---|---|---|
| 平台：P0至P3全部四项交付物、信息质量管控后台、数据集流水线、Excel与变更记录导出、验收文档集 | 1 | 34,000 | 34,000 |
| 站点启用至验收深度 | 18 | 3,000 | 54,000 |
| **一期合计** | | | **88,000** |

**运维，全量上线后按月计**

| 项目 | 月费（美元） |
|---|---|
| 满足99.99%承诺的冗余托管、外部监控、每日备份及1小时RTO、48小时政策更新与24小时紧急更新、48小时问题闭环、月度全量数据复核、季度双向抽样 | **5,200** |

**第一年合计（一期加运维）：150,400美元。**

与替代方案对比：内部自建需16至20人月方可交付可用版本，此后每月还需0.5至1.5名分析师，且首个版本约在第五个月才能交到运营团队手中。Ellis本周即可交付验收，六项验收指标中已达成四项，第五项仅差0.6个百分点。

## 四、付款条款

验收核实后付款可以接受。仅希望付款节点与贵司自身的五阶段流程对应，使每笔付款都对应一次贵司已签署的验收：

第二阶段功能验收，支付30%。
第三阶段灰度验收，支付30%。中国香港与美国已达深度，贵司随时可启动该阶段。
第四阶段全量验收（18个站点），支付40%。
运维费自全量上线起按月结算。

报价未含税，有效期60天。

## 五、采购材料

随附：公司简介、公司注册证书、联邦税号证明文件。请告知贵司审计与法务团队还需哪些材料，我们当天回复。

测试环境已上线：https://ellis-visa.com ，贵司标准中作为前提判定的信息质量管控后台位于 https://ellis-visa.com/ops 。相比再读一份文档，我更希望贵司运营团队花一小时亲自使用它，烦请告知需要开通的账号。

此致

Sammy
Ellis 联合创始人兼首席执行官
