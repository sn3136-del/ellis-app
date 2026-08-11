"""LCA Public Access File (20 CFR 655.760) and the posting notice (20 CFR
655.734) — the one H-1B compliance duty Ellis can carry end to end.

There is no portal here and no government account: the PAF is a file the
employer keeps, and the notice is a piece of paper (or an intranet page) the
employer puts in front of its own workers. Ellis can therefore do the whole
job — assemble the manifest, render the notice, and track the two clocks —
without ever touching a login, a payment, or a signature.

Doctrine, enforced here rather than trusted to callers:

- NOTHING IS INVENTED. Every element of the posted notice comes from a
  recorded case fact. An absent wage, worksite or period is reported MISSING
  and the notice is marked not-ready-to-post; it is never defaulted, rounded,
  or inferred. A notice with a guessed wage is a false statement posted in a
  workplace.
- ELLIS RECORDS, IT DOES NOT WITNESS. Ellis cannot see a sheet of paper on a
  breakroom wall. Every posting record is the employer's own attestation, is
  labelled as such in the manifest, and never becomes "verified".
- CITATION PER ITEM. Each required item carries the subparagraph it comes
  from, so a reviewer can check Ellis against the regulation rather than
  trusting it.
- PARTY WALL. The PAF is petitioner work product, assembled from petitioner
  facts only. The posted notice deliberately names NO beneficiary: 655.734
  does not ask for one, and a public workplace posting is the last place a
  worker's identity belongs.

Legal basis, verified 2026-08-11 against the sources in SOURCES:

  20 CFR 655.760(a)   the ten items that must be in the public access file
  20 CFR 655.760(a)   available for public examination at the employer's
                      principal place of business in the U.S. OR at the place
                      of employment, within ONE WORKING DAY after the date the
                      LCA is filed with DOL
  20 CFR 655.760(c)   retained one year beyond the last date any H-1B
                      nonimmigrant is employed under the LCA (or one year from
                      expiration/withdrawal if none was employed); payroll
                      records three years
  20 CFR 655.734(a)(1)(ii)(A)  hard-copy posting in at least two conspicuous
                      locations at each place of employment
  20 CFR 655.734(a)(1)(ii)(B)  or electronic notice to affected workers
  20 CFR 655.734(a)(1)(ii)     posted/provided on or within 30 days BEFORE the
                      date the LCA is filed, and kept up for a total of 10 days
"""
from __future__ import annotations

import datetime as _dt
from collections.abc import Mapping
from io import BytesIO

from sqlalchemy import select

from .. import audit, consular_forms, dates, models
from ..providers import pdfgen
from . import filing as h1b_filing
from . import forms as h1b_forms
from . import models as h1b_models

# The date the citations below were last read against the official sources.
AS_OF = "2026-08-11"

PAF_CITATION = "20 CFR 655.760"
NOTICE_CITATION = "20 CFR 655.734"

SOURCES = (
    "https://www.ecfr.gov/current/title-20/chapter-V/part-655/subpart-H/section-655.760",
    "https://www.ecfr.gov/current/title-20/chapter-V/part-655/subpart-H/section-655.734",
    "https://www.dol.gov/agencies/whd/fact-sheets/62m-h1b-notice",
    "https://flag.dol.gov/programs/lca",
)

# 655.734(a)(1)(ii): the notice stays up for "a total of 10 days" and must go
# up on, or within 30 days before, the LCA filing date.
POSTING_DAYS = 10
NOTICE_LEAD_DAYS = 30
# 655.760(a): the file is available within ONE WORKING DAY after filing.
AVAILABILITY_WORKING_DAYS = 1
# 655.760(c): one year past the end of employment under the LCA.
RETENTION_YEARS = 1
PAYROLL_RETENTION_YEARS = 3

POSTING_METHODS = ("hard_copy", "electronic")
# Per-item status. "partial" is the honest middle: Ellis holds the underlying
# facts but the file does not yet hold the document that item calls for.
ITEM_STATUSES = ("present", "partial", "missing", "not_applicable", "unknown")

# Documents Ellis itself produces are stored with the shared prepared-form
# doc_type; they are Ellis output, never a party's evidence.
_PREPARED = h1b_forms.PREPARED_DOC_TYPE
NOTICE_ARTIFACT_KIND = "paf_posting_notice"
PACKAGE_ARTIFACT_KIND = "paf_package"

# Where the PAF record lives: the LCA step's own detail dict. No new table —
# H1bCaseStep is already in privacy._CASE_CHILD_MODELS, so export and erasure
# stay complete for free.
_RECORD_KEY = "public_access_file"


# ---------------------------------------------------------------------------
# (a) The required-item list, as DATA with its citation per item.
#
# `satisfied_by` names the stored-document types that put an item genuinely IN
# the file. `facts` names the recorded answers that constitute the item's
# substance — enough for "partial" (Ellis can print it), never enough for
# "present" (the regulation asks for documentation, not a database row).
# `condition` is the answer key that decides whether a conditional item applies
# at all; an unanswered condition resolves to "unknown", never to "no".
# ---------------------------------------------------------------------------
PAF_CONTENTS = (
    {
        "item_id": "certified_lca",
        "citation": "20 CFR 655.760(a)(1)",
        "title": "Signed certified labor condition application",
        "description": ("A copy of the certified LCA (Form ETA-9035E or "
                        "ETA-9035) and the cover pages (Form ETA-9035CP), "
                        "signed by the employer."),
        "applies": "always",
        "satisfied_by": ("certified_lca",),
        "facts": (),
    },
    {
        "item_id": "wage_rate_documentation",
        "citation": "20 CFR 655.760(a)(2)",
        "title": "Documentation of the wage rate paid to the H-1B worker",
        "description": ("Documentation of the wage rate to be paid to each "
                        "H-1B nonimmigrant employed under the LCA."),
        "applies": "always",
        "satisfied_by": ("wage_rate_documentation", "employer_support_letter"),
        "facts": ("wage_offer", "wage_offer_unit"),
    },
    {
        "item_id": "actual_wage_memorandum",
        "citation": "20 CFR 655.760(a)(3)",
        "title": "Actual-wage memorandum",
        "description": ("A full, clear explanation of the system the employer "
                        "used to set the actual wage for other employees with "
                        "similar experience and qualifications in the "
                        "occupation, including any periodic increases."),
        "applies": "always",
        "satisfied_by": ("actual_wage_memorandum",),
        "facts": (),
    },
    {
        "item_id": "prevailing_wage_determination",
        "citation": "20 CFR 655.760(a)(4)",
        "title": "Prevailing-wage determination and its source",
        "description": ("Documentation of the prevailing wage rate and the "
                        "source used to establish it. The underlying "
                        "individual wage data stays confidential and is "
                        "disclosed only to DOL in an enforcement action."),
        "applies": "always",
        "satisfied_by": ("prevailing_wage_determination",),
        "facts": ("prevailing_wage",),
    },
    {
        "item_id": "notice_documentation",
        "citation": "20 CFR 655.760(a)(5)",
        "title": "Notice documentation (dates and locations)",
        "description": ("A copy of the notice given under 20 CFR 655.734, "
                        "with the dates and the locations where it was posted "
                        "— or the evidence of the electronic notice given to "
                        "affected workers."),
        "applies": "always",
        "satisfied_by": (),          # resolved from the case's PAF record
        "facts": (),
    },
    {
        "item_id": "benefits_summary",
        "citation": "20 CFR 655.760(a)(6)",
        "title": "Summary of the benefits offered to U.S. workers",
        "description": ("A summary of the benefits offered to U.S. workers in "
                        "the same occupational classification, a statement of "
                        "any differentiation, and any 'home country' benefits "
                        "given to H-1B nonimmigrants."),
        "applies": "always",
        "satisfied_by": ("benefits_summary",),
        "facts": (),
    },
    {
        "item_id": "corporate_change_statement",
        "citation": "20 CFR 655.760(a)(7)",
        "title": "Corporate-change successor statement",
        "description": ("Where a new entity has taken on the LCAs of a "
                        "predecessor, the successor's sworn statement "
                        "accepting all obligations, liabilities and "
                        "undertakings, with the affected LCAs, their "
                        "certification numbers and dates, and the new "
                        "entity's FEIN."),
        "applies": "conditional",
        "condition": "h1b_corporate_change",
        "condition_question": ("Has a corporate change (merger, acquisition, "
                               "reorganization) transferred this employer's "
                               "LCA obligations to a new entity?"),
        "satisfied_by": ("corporate_change_statement",
                         "corporate_relationship_evidence"),
        "facts": (),
    },
    {
        "item_id": "single_employer_entity_list",
        "citation": "20 CFR 655.760(a)(8)",
        "title": "Single-employer entity list",
        "description": ("Where the employer uses the Internal Revenue Code "
                        "'single employer' definition, a list of the entities "
                        "included in the H-1B-dependency determination."),
        "applies": "conditional",
        "condition": "h1b_single_employer_definition",
        "condition_question": ("Did this employer use the IRC 'single "
                               "employer' definition when determining H-1B "
                               "dependency?"),
        "satisfied_by": ("single_employer_entity_list",),
        "facts": (),
    },
    {
        "item_id": "exempt_nonimmigrant_list",
        "citation": "20 CFR 655.760(a)(9)",
        "title": "List of exempt H-1B nonimmigrants",
        "description": ("Where an H-1B-dependent or willful-violator employer "
                        "indicates it will employ only 'exempt' H-1B "
                        "nonimmigrants, a list of those nonimmigrants."),
        "applies": "conditional",
        "condition": "_dependent_or_willful",
        "condition_question": ("Is this employer H-1B-dependent or a willful "
                               "violator? (ETA-9035 Section F)"),
        "satisfied_by": ("exempt_nonimmigrant_list",),
        "facts": (),
    },
    {
        "item_id": "recruitment_summary",
        "citation": "20 CFR 655.760(a)(10)",
        "title": "Summary of U.S.-worker recruitment",
        "description": ("Where an H-1B-dependent or willful-violator employer "
                        "is subject to the recruitment attestation, a summary "
                        "of the recruitment methods used and the time frames "
                        "in which they were used."),
        "applies": "conditional",
        "condition": "_dependent_or_willful",
        "condition_question": ("Is this employer H-1B-dependent or a willful "
                               "violator? (ETA-9035 Section F)"),
        "satisfied_by": ("recruitment_summary",),
        "facts": (),
    },
)

PAF_ITEM_IDS = tuple(item["item_id"] for item in PAF_CONTENTS)


# ---------------------------------------------------------------------------
# Localized user-facing strings (en + zh-CN + zh-Hant, the sibling contract in
# h1b/forms.py). The PDF ARTIFACTS stay English on purpose: the posting notice
# is read by U.S. workers at a U.S. worksite and is quoted from a U.S.
# regulation, and the dependency-free PDF writer is latin-1 only.
# ---------------------------------------------------------------------------
STRINGS = {
    "paf.ready_to_post": {
        "en": ("Every element 20 CFR 655.734 requires is present. Post this "
               "notice in at least two conspicuous locations at each place of "
               "employment, or give it electronically to affected workers, on "
               "or within 30 days before the LCA is filed, and keep it up for "
               "10 days."),
        "zh-CN": ("20 CFR 655.734 要求的各项内容均已齐备。请在提交 LCA 当日或此前 30 天内，"
                  "将本通知张贴于每个工作地点的至少两个显眼位置，或以电子方式送达受影响员工，"
                  "并保持 10 天。"),
        "zh-Hant": ("20 CFR 655.734 要求的各項內容均已齊備。請在提交 LCA 當日或此前 30 天內，"
                    "將本通知張貼於每個工作地點的至少兩個顯眼位置，或以電子方式送達受影響員工，"
                    "並保持 10 天。"),
    },
    "paf.not_ready_to_post": {
        "en": ("This notice is NOT ready to post: required facts are missing. "
               "Ellis leaves them blank rather than inventing them — a posted "
               "notice carrying a guessed wage or worksite is a false "
               "statement. Answer the missing items and generate it again."),
        "zh-CN": ("本通知尚不可张贴：必需信息缺失。Ellis 宁可留空也不会自行编造 - "
                  "张贴含有臆测工资或工作地点的通知即属虚假陈述。请补齐缺失项后重新生成。"),
        "zh-Hant": ("本通知尚不可張貼：必需資訊缺失。Ellis 寧可留空也不會自行編造 - "
                    "張貼含有臆測工資或工作地點的通知即屬虛假陳述。請補齊缺失項後重新產生。"),
    },
    "paf.attested_not_verified": {
        "en": ("Ellis records what the employer states about the posting; it "
               "cannot witness a notice on a wall or an intranet page. This "
               "record is the employer's own attestation, never a verified "
               "fact."),
        "zh-CN": ("Ellis 仅记录雇主就张贴情况所作的陈述，无法亲眼核实墙上的通知或内网页面。"
                  "此记录为雇主自行声明，并非经核实的事实。"),
        "zh-Hant": ("Ellis 僅記錄雇主就張貼情況所作的陳述，無法親眼核實牆上的通知或內網頁面。"
                    "此記錄為雇主自行聲明，並非經核實的事實。"),
    },
    "paf.availability": {
        "en": ("The public access file must be available for public "
               "examination at the employer's principal place of business in "
               "the U.S. or at the place of employment within ONE WORKING DAY "
               "after the LCA is filed (20 CFR 655.760(a))."),
        "zh-CN": ("公开查阅文档须自 LCA 提交之日起一个工作日内，在雇主美国主营业地或工作地点"
                  "供公众查阅（20 CFR 655.760(a)）。"),
        "zh-Hant": ("公開查閱文檔須自 LCA 提交之日起一個工作日內，在雇主美國主營業地或工作地點"
                    "供公眾查閱（20 CFR 655.760(a)）。"),
    },
    "paf.retention": {
        "en": ("Keep the file one year beyond the last date any H-1B worker "
               "is employed under this LCA — or, if none was employed, one "
               "year from the LCA's expiration or withdrawal. Payroll records "
               "are kept three years (20 CFR 655.760(c))."),
        "zh-CN": ("文档须保存至本 LCA 项下最后一名 H-1B 员工在职之日起满一年；"
                  "若无人受雇，则自 LCA 到期或撤回之日起满一年。工资单记录保存三年"
                  "（20 CFR 655.760(c)）。"),
        "zh-Hant": ("文檔須保存至本 LCA 項下最後一名 H-1B 員工在職之日起滿一年；"
                    "若無人受僱，則自 LCA 到期或撤回之日起滿一年。工資單記錄保存三年"
                    "（20 CFR 655.760(c)）。"),
    },
    "paf.english_artifact": {
        "en": ("The notice PDF is English: it is posted at a U.S. worksite "
               "and quotes a U.S. regulation verbatim."),
        "zh-CN": "通知 PDF 为英文：其张贴于美国工作地点，并逐字引用美国法规。",
        "zh-Hant": "通知 PDF 為英文：其張貼於美國工作地點，並逐字引用美國法規。",
    },
    "paf.nothing_filed": {
        "en": ("Nothing has been filed or posted by Ellis. Generating the "
               "notice and assembling the manifest are preparation; posting "
               "the notice and keeping the file are the employer's own acts."),
        "zh-CN": ("Ellis 未提交或张贴任何文件。生成通知与整理清单仅为准备工作；"
                  "张贴通知与保存文档均属雇主自身行为。"),
        "zh-Hant": ("Ellis 未提交或張貼任何文件。產生通知與整理清單僅為準備工作；"
                    "張貼通知與保存文檔均屬雇主自身行為。"),
    },
}


def tr(key: str, locale: str = "en") -> str:
    entry = STRINGS.get(key) or {}
    return entry.get(locale) or entry.get("en") or key


# ---------------------------------------------------------------------------
# (d) Posting-window and deadline helpers. Every calendar value in and out of
# this module travels through app/dates.py: ISO internally, MM/DD/YYYY for
# anything a human reads. Unparseable input FAILS CLOSED — an empty result and
# a stated reason, never a guessed date.
# ---------------------------------------------------------------------------

def _iso(value) -> str:
    """Canonical ISO for any recorded date value. us_numeric is on because
    these dates are typed by U.S. employers in the U.S. display format."""
    return dates.normalize_any(value, kind="expiry", us_numeric=True)


def _date(value) -> _dt.date | None:
    iso = _iso(value)
    if not iso:
        return None
    return _dt.date(int(iso[0:4]), int(iso[5:7]), int(iso[8:10]))


_TEN_DAY_NOTE = ("20 CFR 655.734(a)(1)(ii) requires the notice to remain "
                 "posted for a total of 10 days; Ellis tracks the attested "
                 "window as consecutive calendar days, which satisfies the "
                 "total either way.")


def posting_window(start, *, days: int = POSTING_DAYS) -> dict:
    """The 10-day posting window that begins on `start`, counted INCLUSIVELY:
    a notice up on day one is up for one of its ten days. Returns the last day
    it must remain posted."""
    d = _date(start)
    if d is None:
        return {"valid": False, "start": "", "end": "", "days": days,
                "reason": "posting start date is missing or not a real date"}
    end = d + _dt.timedelta(days=days - 1)
    return {"valid": True, "start": dates.to_iso(d), "end": dates.to_iso(end),
            "days": days, "start_display": dates.to_display(dates.to_iso(d)),
            "end_display": dates.to_display(dates.to_iso(end)),
            "citation": f"{NOTICE_CITATION}(a)(1)(ii)"}


def posting_progress(start, end="", *, today: _dt.date | None = None,
                     days: int = POSTING_DAYS) -> dict:
    """How far through the 10 days a recorded posting is.

    `consecutive_days` is the span the employer attests to (end - start + 1).
    `days_elapsed` is how much of it has actually passed. `meets_ten_days` is
    the compliance question and is answered only by the attested span; Ellis
    never infers that a notice stayed up because time passed.

    With NO attested end date the answer is `meets_ten_days: None` — unknown.
    The employer said when the notice went up, not when it came down, and a
    notice can be taken down on day three. Ellis reports the date the window
    WOULD close (`planned_end`) as a target, never as an attestation.
    """
    started = _date(start)
    if started is None:
        return {"valid": False, "reason": "posting start date is missing or "
                                          "not a real date",
                "meets_ten_days": None}
    planned_end_iso = posting_window(start, days=days)["end"]
    attested = _date(end)
    today = today or _dt.date.today()
    if attested is None:
        planned = _date(planned_end_iso)
        elapsed = (0 if today < started
                   else (min(today, planned) - started).days + 1)
        return {
            "valid": True,
            "start": dates.to_iso(started),
            "start_display": dates.to_display(dates.to_iso(started)),
            "end": "", "end_attested": False,
            "planned_end": planned_end_iso,
            "planned_end_display": dates.to_display(planned_end_iso),
            "required_days": days,
            # Not zero and not ten: the employer has attested to no span at all.
            "consecutive_days": None,
            "days_elapsed": elapsed,
            "days_remaining": max(0, days - elapsed),
            # UNKNOWN, never True. Elapsed time is not evidence a notice stayed
            # up, and a future start date is not evidence it ever went up.
            "meets_ten_days": None,
            "window_closed": today > planned,
            "reason": ("the employer has not recorded the date the notice came "
                       "down, so the 10-day total is not attested; Ellis shows "
                       f"{dates.to_display(planned_end_iso)} as the earliest "
                       "date it may be removed"),
            "citation": f"{NOTICE_CITATION}(a)(1)(ii)",
            "note": _TEN_DAY_NOTE,
        }
    finished = attested
    if finished < started:
        return {"valid": False,
                "reason": "posting end date is before the start date",
                "meets_ten_days": False}
    span = (finished - started).days + 1
    if today < started:
        elapsed = 0
    else:
        elapsed = (min(today, finished) - started).days + 1
    return {
        "valid": True,
        "start": dates.to_iso(started), "end": dates.to_iso(finished),
        "end_attested": True,
        "start_display": dates.to_display(dates.to_iso(started)),
        "end_display": dates.to_display(dates.to_iso(finished)),
        "required_days": days,
        "consecutive_days": span,
        "days_elapsed": elapsed,
        "days_remaining": max(0, span - elapsed),
        "meets_ten_days": span >= days,
        "window_closed": today > finished,
        "citation": f"{NOTICE_CITATION}(a)(1)(ii)",
        "note": _TEN_DAY_NOTE,
    }


def notice_timing(notice_start, lca_filed, *,
                  lead_days: int = NOTICE_LEAD_DAYS) -> dict:
    """20 CFR 655.734(a)(1)(ii): the notice goes up ON the LCA filing date or
    within the 30 days BEFORE it. Anything earlier is stale; anything after
    the filing date is late. An unknown filing date yields `compliant: None` —
    unknown is stated, never assumed compliant."""
    started = _date(notice_start)
    filed = _date(lca_filed)
    if started is None:
        return {"compliant": None, "reason": "posting start date is unknown",
                "citation": f"{NOTICE_CITATION}(a)(1)(ii)"}
    if filed is None:
        return {"compliant": None,
                "reason": ("the LCA filing date is unknown, so the 30-day "
                           "notice window cannot be checked"),
                "notice_start": dates.to_iso(started),
                "citation": f"{NOTICE_CITATION}(a)(1)(ii)"}
    days_before = (filed - started).days
    if days_before < 0:
        reason = ("the notice went up AFTER the LCA was filed; it must go up "
                  "on the filing date or in the 30 days before it")
    elif days_before > lead_days:
        reason = (f"the notice went up {days_before} days before filing, more "
                  f"than the {lead_days} days the rule allows")
    else:
        reason = (f"the notice went up {days_before} days before the LCA was "
                  f"filed, inside the {lead_days}-day window")
    return {"compliant": 0 <= days_before <= lead_days,
            "days_before_filing": days_before,
            "notice_start": dates.to_iso(started),
            "lca_filed": dates.to_iso(filed),
            "lca_filed_display": dates.to_display(dates.to_iso(filed)),
            "lead_days": lead_days, "reason": reason,
            "citation": f"{NOTICE_CITATION}(a)(1)(ii)"}


def next_working_day(value, *, working_days: int = AVAILABILITY_WORKING_DAYS) -> str:
    """The ISO date `working_days` working days after `value`, counting
    Monday-Friday. Federal holidays are NOT modelled — and cannot make this
    answer wrong in the dangerous direction: a holiday can only push the real
    deadline LATER, so the date returned here is always safe to act on."""
    d = _date(value)
    if d is None:
        return ""
    remaining = max(0, int(working_days))
    while remaining > 0:
        d += _dt.timedelta(days=1)
        if d.weekday() < 5:
            remaining -= 1
    return dates.to_iso(d)


def availability_deadline(lca_filed) -> dict:
    """20 CFR 655.760(a): the file is open to public examination within ONE
    WORKING DAY after the LCA is filed."""
    filed = _date(lca_filed)
    if filed is None:
        return {"known": False,
                "reason": ("the LCA filing date is unknown, so the "
                           "one-working-day availability deadline cannot be "
                           "computed"),
                "working_days": AVAILABILITY_WORKING_DAYS,
                "citation": f"{PAF_CITATION}(a)"}
    deadline = next_working_day(filed)
    return {"known": True, "lca_filed": dates.to_iso(filed),
            "lca_filed_display": dates.to_display(dates.to_iso(filed)),
            "deadline": deadline, "deadline_display": dates.to_display(deadline),
            "working_days": AVAILABILITY_WORKING_DAYS,
            "holiday_caveat": ("federal holidays are not modelled; a holiday "
                               "can only move this deadline later, so this "
                               "date is the safe one to meet"),
            "citation": f"{PAF_CITATION}(a)"}


def retention_deadline(*, employment_end="", lca_expiry="") -> dict:
    """20 CFR 655.760(c): one year beyond the last date an H-1B nonimmigrant
    is employed under the LCA, or — if none was employed — one year from the
    LCA's expiration or withdrawal."""
    basis_key, basis = ("employment_end", _date(employment_end))
    if basis is None:
        basis_key, basis = ("lca_expiry", _date(lca_expiry))
    if basis is None:
        return {"known": False,
                "reason": ("neither the last date of employment under this "
                           "LCA nor the LCA's expiry date is recorded"),
                "years": RETENTION_YEARS,
                "payroll_years": PAYROLL_RETENTION_YEARS,
                "citation": f"{PAF_CITATION}(c)"}
    keep_until = dates._shift_years(basis, RETENTION_YEARS)
    return {"known": True, "basis": basis_key, "basis_date": dates.to_iso(basis),
            "keep_until": dates.to_iso(keep_until),
            "keep_until_display": dates.to_display(dates.to_iso(keep_until)),
            "years": RETENTION_YEARS,
            "payroll_years": PAYROLL_RETENTION_YEARS,
            "payroll_note": ("payroll records for the H-1B worker and for "
                             "other employees in the occupation are kept "
                             "three years from their creation"),
            "citation": f"{PAF_CITATION}(c)"}


# ---------------------------------------------------------------------------
# Case facts. The PAF is petitioner work product: the fact dict is the LCA
# step's assembled petitioner answers plus the petitioner's own party answers.
# No beneficiary fact is read, and none appears on the notice.
# ---------------------------------------------------------------------------

def _lca_step(db, parent: models.VisaApplication):
    return db.execute(select(h1b_models.H1bCaseStep).where(
        h1b_models.H1bCaseStep.application_id == parent.id,
        h1b_models.H1bCaseStep.step_key == "lca")).scalars().first()


def notice_facts(db, parent: models.VisaApplication) -> dict:
    """The flat fact dict `build_posting_notice` consumes. Petitioner facts
    only — filing.answers_for_step for the LCA step (the canonical
    cross-party-safe builder, which also maps the EmployerProfile columns) plus
    the acting petitioner's own party answers, which are their own to state."""
    step = _lca_step(db, parent)
    if step is None:
        raise LookupError("this case's plan has no 'lca' step")
    out = h1b_filing.answers_for_step(db, parent, step)
    petitioner = h1b_filing._petitioner_party(db, parent.id)
    for key, value in ((petitioner.answers or {}) if petitioner else {}).items():
        if value in (None, ""):
            continue
        out.setdefault(key, value)
    record = paf_record(db, parent)
    filed = (record.get("posting") or {}).get("lca_filed_date") or ""
    if filed:
        out.setdefault("lca_filed_date", filed)
    if step.lca_number:
        out.setdefault("lca_number", step.lca_number)
    return out


def _first(facts: Mapping, *keys) -> str:
    for key in keys:
        value = facts.get(key)
        if value not in (None, "", [], {}):
            return str(value).strip()
    return ""


def _address(facts: Mapping, line1_keys, city_keys, state_keys, zip_keys) -> str:
    line1 = _first(facts, *line1_keys)
    city = _first(facts, *city_keys)
    state = _first(facts, *state_keys)
    postal = _first(facts, *zip_keys)
    if not (line1 or city or state):
        return ""
    tail = " ".join(p for p in (state, postal) if p)
    return ", ".join(p for p in (line1, city, tail) if p)


def worksite_address(facts: Mapping) -> str:
    return _address(
        facts,
        ("worksite_address_line1", "worksite_address_line", "worksite_location"),
        ("worksite_address_city", "worksite_city"),
        ("worksite_address_state", "worksite_state"),
        ("worksite_address_zip", "worksite_postal_code"))


def employer_address(facts: Mapping) -> str:
    return _address(facts, ("employer_address_line1",), ("employer_city",),
                    ("employer_state",), ("employer_postal_code",))


_WAGE_UNITS = {
    "year": "per year", "yr": "per year", "annual": "per year",
    "annually": "per year", "hour": "per hour", "hr": "per hour",
    "hourly": "per hour", "week": "per week", "weekly": "per week",
    "bi-weekly": "every two weeks", "biweekly": "every two weeks",
    "month": "per month", "monthly": "per month",
}


def _money(value) -> str:
    """A recorded amount formatted for a posted notice, or '' when it is not a
    real number. '' is the whole point: an unparseable wage becomes MISSING,
    never a zero and never the raw string."""
    if isinstance(value, bool) or value in (None, "", [], {}):
        return ""
    raw = str(value).strip().replace(",", "").replace("$", "")
    try:
        amount = float(raw)
    except ValueError:
        return ""
    if amount != amount or amount in (float("inf"), float("-inf")):
        return ""
    if amount == int(amount):
        return f"${int(amount):,}"
    return f"${amount:,.2f}"


def _wage_phrase(facts: Mapping) -> tuple[str, list[str]]:
    """(rendered wage, missing keys). A wage with no rate period is not a
    wage: both halves are required before anything is rendered."""
    low = _money(_first(facts, "wage_offer", "h1b_wage_from_dollars"))
    high = _money(_first(facts, "wage_offer_to", "h1b_wage_to_dollars"))
    unit_raw = _first(facts, "wage_offer_unit").lower()
    unit = _WAGE_UNITS.get(unit_raw, "")
    missing = []
    if not low:
        missing.append("wage_offer")
    if not unit:
        missing.append("wage_offer_unit")
    if missing:
        return "", missing
    if high and high != low:
        return f"{low} to {high} {unit}", []
    return f"{low} {unit}", []


def _period_phrase(facts: Mapping) -> tuple[str, list[str]]:
    start = _iso(_first(facts, "employment_start_date"))
    end = _iso(_first(facts, "employment_end_date"))
    missing = []
    if not start:
        missing.append("employment_start_date")
    if not end:
        missing.append("employment_end_date")
    if missing:
        return "", missing
    return f"{dates.to_display(start)} through {dates.to_display(end)}", []


def _occupation_phrase(facts: Mapping) -> tuple[str, list[str]]:
    soc_title = _first(facts, "soc_title")
    soc_code = _first(facts, "soc_code")
    job_title = _first(facts, "job_title")
    if soc_title and soc_code:
        return f"{soc_title} (SOC/O*NET code {soc_code})", []
    if soc_title or soc_code:
        return (soc_title or f"SOC/O*NET code {soc_code}"), []
    if job_title:
        return job_title, []
    return "", ["soc_title"]


def _worker_count(facts: Mapping) -> tuple[str, list[str]]:
    raw = _first(facts, "h1b_worksite_worker_count", "h1b_total_workers",
                 "h1b_number_of_workers")
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits or int(digits) <= 0:
        return "", ["h1b_total_workers"]
    return digits, []


# 20 CFR 655.734(a)(1)(ii) — the statement the notice must carry, verbatim.
COMPLAINT_STATEMENT = (
    "Complaints alleging misrepresentation of material facts in the labor "
    "condition application and/or failure to comply with the terms of the "
    "labor condition application may be filed with any office of the Wage and "
    "Hour Division of the United States Department of Labor.")

# The additional statement 20 CFR 655.734(a)(1)(ii) requires of an
# H-1B-dependent or willful-violator employer seeking non-exempt workers. The
# office named in the regulation is now the Immigrant and Employee Rights
# Section (IER); the regulation's own wording is kept and the rename noted.
DEPENDENT_COMPLAINT_STATEMENT = (
    "Complaints alleging failure to offer employment to an equally or better "
    "qualified U.S. applicant, or an employer's misrepresentation regarding "
    "such offer(s) of employment, may be filed with the U.S. Department of "
    "Justice, Civil Rights Division, Office of Special Counsel for "
    "Immigration-Related Unfair Employment Practices (now the Immigrant and "
    "Employee Rights Section), 950 Pennsylvania Avenue NW, Washington, DC "
    "20530.")

PUBLIC_INSPECTION_TEMPLATE = (
    "The labor condition application is available for public inspection at "
    "{location}.")

# Notice elements, in posting order. Each names its citation, the fact keys it
# is built from, and whether the regulation requires it unconditionally.
NOTICE_ELEMENTS = (
    ("worker_count", "Number of H-1B nonimmigrants sought"),
    ("occupational_classification", "Occupational classification"),
    ("wages_offered", "Wages offered"),
    ("period_of_employment", "Period of employment"),
    ("place_of_employment", "Place(s) of employment"),
    ("public_inspection", "Where the LCA is available for public inspection"),
    ("complaint_statement", "Complaint statement (Wage and Hour Division)"),
)


# ---------------------------------------------------------------------------
# (b) The posting notice.
# ---------------------------------------------------------------------------

def build_posting_notice(case: Mapping) -> dict:
    """The 20 CFR 655.734 notice, built ONLY from recorded case facts.

    `case` is the flat fact mapping produced by `notice_facts(db, parent)` (or
    any mapping carrying the same keys) — deliberately not an ORM row, so the
    render is a pure, unit-testable function over facts.

    Every element the regulation names is emitted or reported missing. A
    missing element is never filled with a placeholder value that could read
    as a fact: the line says the fact is MISSING, `ready_to_post` is False,
    and the PDF carries a DO-NOT-POST banner.
    """
    if not isinstance(case, Mapping):
        raise TypeError("build_posting_notice takes the fact mapping from "
                        "notice_facts(db, parent), not an ORM row")

    employer = _first(case, "employer_legal_name")
    count, count_missing = _worker_count(case)
    occupation, occupation_missing = _occupation_phrase(case)
    wage, wage_missing = _wage_phrase(case)
    period, period_missing = _period_phrase(case)
    worksite = worksite_address(case)
    inspection = employer_address(case) or worksite
    job_title = _first(case, "job_title")

    dependent = case.get("h1b_dependent_employer")
    willful = case.get("willful_violator")
    dependent_or_willful = bool(dependent) or bool(willful)

    values = {
        "worker_count": (count, count_missing),
        "occupational_classification": (occupation, occupation_missing),
        "wages_offered": (wage, wage_missing),
        "period_of_employment": (period, period_missing),
        "place_of_employment": (worksite,
                                [] if worksite else ["worksite_address_line1"]),
        "public_inspection": (inspection,
                              [] if inspection else ["employer_address_line1"]),
        "complaint_statement": (COMPLAINT_STATEMENT, []),
    }

    elements, missing = [], []
    for key, label in NOTICE_ELEMENTS:
        value, gaps = values[key]
        elements.append({"element": key, "label": label,
                         "value": value, "status": "present" if value else "missing",
                         "citation": f"{NOTICE_CITATION}(a)(1)(ii)"})
        for gap in gaps:
            missing.append({"key": gap, "label": h1b_forms.label_for_key(gap),
                            "element": key})
    if not employer:
        missing.append({"key": "employer_legal_name",
                        "label": h1b_forms.label_for_key("employer_legal_name"),
                        "element": "employer"})
    ready = not missing

    def _fact(label: str, value: str, gap_label: str) -> str:
        return f"{label}: {value}" if value else f"{label}: [MISSING - {gap_label}]"

    lines: list[str] = []
    if not ready:
        lines += ["*** DRAFT - DO NOT POST ***",
                  f"{len(missing)} required item(s) are missing. Ellis leaves "
                  f"them blank rather than inventing them.", ""]
    lines.append("NOTICE OF FILING OF LABOR CONDITION APPLICATION")
    lines.append("H-1B NONIMMIGRANT WORKERS - 20 CFR 655.734")
    lines.append("")
    lines += consular_forms._wrap(
        f"Notice is hereby given that {employer or '[MISSING - employer legal name]'} "
        f"has filed, or will file within the next 30 days, a Labor Condition "
        f"Application (Form ETA-9035/9035E) with the U.S. Department of Labor, "
        f"Employment and Training Administration, for the position(s) "
        f"described below.", 92)
    lines.append("")
    lines.append(_fact("Number of H-1B nonimmigrants sought", count,
                       "number of workers sought"))
    lines.append(_fact("Occupational classification", occupation,
                       "SOC occupation title/code"))
    if job_title:
        lines.append(f"Job title: {job_title}")
    lines.append(_fact("Wages offered", wage, "offered wage and its rate period"))
    lines.append(_fact("Period of employment", period,
                       "employment start and end dates"))
    lines.append(_fact("Place(s) of employment", worksite, "worksite address"))
    lines.append("")
    if inspection:
        lines += consular_forms._wrap(
            PUBLIC_INSPECTION_TEMPLATE.format(location=inspection), 92)
    else:
        lines += consular_forms._wrap(PUBLIC_INSPECTION_TEMPLATE.format(
            location="[MISSING - the employer's principal place of business "
                     "in the U.S. or the worksite]"), 92)
    lines.append("")
    lines += consular_forms._wrap(COMPLAINT_STATEMENT, 92)
    if dependent_or_willful:
        lines.append("")
        lines += consular_forms._wrap(DEPENDENT_COMPLAINT_STATEMENT, 92)
        elements.append({"element": "dependent_complaint_statement",
                         "label": "Additional complaint statement "
                                  "(H-1B-dependent / willful violator)",
                         "value": DEPENDENT_COMPLAINT_STATEMENT,
                         "status": "present",
                         "citation": f"{NOTICE_CITATION}(a)(1)(ii)"})
    lines.append("")
    lines.append(f"Date of this notice: {dates.to_display(dates.to_iso(_dt.date.today()))}")

    lines += ["", "", "-" * 78,
              "INSTRUCTIONS TO THE EMPLOYER - NOT PART OF THE POSTED NOTICE",
              "-" * 78, ""]
    lines += consular_forms._wrap(
        "20 CFR 655.734(a)(1)(ii)(A): post this notice in at least TWO "
        "conspicuous locations at EACH place of employment where an H-1B "
        "nonimmigrant will be employed, in a size and position where workers "
        "in the occupation can easily see and read it.", 92)
    lines.append("")
    lines += consular_forms._wrap(
        "20 CFR 655.734(a)(1)(ii)(B): instead of posting, the notice may be "
        "given electronically to affected workers - e-mail, an intranet page, "
        "an electronic bulletin board or a newsletter. Hard-copy posting is "
        "required where affected workers have no practical computer access.", 92)
    lines.append("")
    lines += consular_forms._wrap(
        f"Timing: the notice goes up ON the day the LCA is filed or within the "
        f"{NOTICE_LEAD_DAYS} days before it, and stays up for a total of "
        f"{POSTING_DAYS} days.", 92)
    lines.append("")
    lines += consular_forms._wrap(
        "Keep a copy of this notice, with the dates and the locations where "
        "it was posted, in the public access file "
        "(20 CFR 655.760(a)(5)).", 92)
    if dependent is None or willful is None:
        lines.append("")
        lines += consular_forms._wrap(
            "UNANSWERED: this employer has not stated whether it is "
            "H-1B-dependent or a willful violator (ETA-9035 Section F). If "
            "either is true and non-exempt workers are sought, the notice must "
            "also carry the Department of Justice statement in "
            "20 CFR 655.734(a)(1)(ii). Ellis does not assume the answer.", 92)

    pdf = _text_pdf_pages(lines, title="H-1B Notice of Filing (20 CFR 655.734)")
    return {"lines": lines, "elements": elements, "missing": missing,
            "ready_to_post": ready, "pdf": pdf,
            "citation": f"{NOTICE_CITATION}(a)(1)(ii)",
            "posting": posting_requirements()}


def posting_requirements() -> dict:
    """The two lawful ways to give the notice, as data."""
    return {
        "hard_copy": {
            "citation": f"{NOTICE_CITATION}(a)(1)(ii)(A)",
            "locations_required": 2,
            "requirement": ("at least two conspicuous locations at each place "
                            "of employment where an H-1B nonimmigrant will be "
                            "employed"),
            "days": POSTING_DAYS,
        },
        "electronic": {
            "citation": f"{NOTICE_CITATION}(a)(1)(ii)(B)",
            "requirement": ("direct electronic notice to affected workers - "
                            "e-mail, intranet/home page, electronic bulletin "
                            "board or newsletter; hard copy is required where "
                            "affected workers have no practical computer "
                            "access"),
            "days": POSTING_DAYS,
            "direct_email_note": ("where each affected worker is given "
                                  "individual direct notice by e-mail, the "
                                  "notice need only be given once"),
        },
        "lead_days": NOTICE_LEAD_DAYS,
    }


# ---------------------------------------------------------------------------
# Multi-page rendering. pdfgen.text_pdf writes ONE page; a PAF manifest is
# longer than one page, so pages are cut here and merged with pypdf (already a
# dependency, same as the paper packet).
# ---------------------------------------------------------------------------
_LINES_PER_PAGE = 58


def _paginate(lines: list[str], per_page: int = _LINES_PER_PAGE) -> list[list[str]]:
    if not lines:
        return [[""]]
    return [lines[i:i + per_page] for i in range(0, len(lines), per_page)]


def _text_pdf_pages(lines: list[str], *, title: str) -> bytes:
    pages = _paginate(lines)
    if len(pages) == 1:
        return pdfgen.text_pdf(pages[0], title=title)
    from pypdf import PdfReader, PdfWriter
    writer = PdfWriter()
    for page in pages:
        writer.append(PdfReader(BytesIO(pdfgen.text_pdf(page, title=title))))
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# The PAF record: the notice artifact and the employer's posting attestation,
# kept on the LCA step's detail dict.
# ---------------------------------------------------------------------------

def paf_record(db, parent: models.VisaApplication) -> dict:
    step = _lca_step(db, parent)
    if step is None:
        return {}
    return dict((step.detail or {}).get(_RECORD_KEY) or {})


def _write_record(db, parent: models.VisaApplication, section: str,
                  payload: dict) -> dict:
    step = _lca_step(db, parent)
    if step is None:
        raise LookupError("this case's plan has no 'lca' step")
    detail = dict(step.detail or {})
    record = dict(detail.get(_RECORD_KEY) or {})
    record[section] = payload
    detail[_RECORD_KEY] = record
    step.detail = detail                     # reassign: JSON columns need it
    db.add(step)
    db.commit()
    return record


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def prepare_posting_notice(db, parent: models.VisaApplication, *,
                           actor: str = "") -> dict:
    """Render, store and register the posting notice. The stored PDF is the
    artifact the employer actually posts, so its id and hash are what the
    manifest points at for 20 CFR 655.760(a)(5)."""
    facts = notice_facts(db, parent)
    notice = build_posting_notice(facts)
    doc = h1b_forms.store_prepared_pdf(
        db, parent, name="h1b-lca-posting-notice.pdf", pdf=notice["pdf"],
        detail={"kind": NOTICE_ARTIFACT_KIND,
                "citation": notice["citation"],
                "ready_to_post": notice["ready_to_post"],
                "missing_count": len(notice["missing"])})
    _write_record(db, parent, "notice", {
        "document_id": doc.id, "sha256": doc.sha256,
        "generated_at": _now(), "generated_by": actor,
        "ready_to_post": notice["ready_to_post"],
        "missing": [m["key"] for m in notice["missing"]]})
    audit.record(db, org_id=parent.org_id, application_id=parent.id,
                 action="h1b_paf_notice_prepared",
                 detail={"document_id": doc.id,
                         "ready_to_post": notice["ready_to_post"],
                         "missing_count": len(notice["missing"]),
                         "citation": notice["citation"]},
                 actor=actor)
    return {"document_id": doc.id, "sha256": doc.sha256,
            "ready_to_post": notice["ready_to_post"],
            "missing": notice["missing"], "elements": notice["elements"],
            "notice_text": notice["lines"], "citation": notice["citation"],
            "posting_requirements": notice["posting"],
            **h1b_forms.mint_download_url(doc.id)}


class PostingEvidenceError(ValueError):
    """A posting record Ellis refuses to store because it cannot be true."""


def record_posting(db, parent: models.VisaApplication, *, method: str,
                   start_date="", end_date="", locations=(),
                   electronic_method="", electronic_evidence="",
                   individual_direct_email: bool = False,
                   lca_filed_date="", actor: str = "") -> dict:
    """Record how the 655.734 notice was given.

    Ellis stores the employer's ATTESTATION and computes the two clocks over
    it. It refuses only what cannot be true (an unknown method, an unparseable
    date, an end before a start). A shortfall the employer can still fix — one
    posting location instead of two, a nine-day window, a notice given after
    filing — is RECORDED and flagged, because hiding it would be worse than
    showing it.
    """
    if method not in POSTING_METHODS:
        raise PostingEvidenceError(
            f"unknown notice method {method!r}; 20 CFR 655.734(a)(1)(ii) "
            f"allows {' or '.join(POSTING_METHODS)}")
    start_iso = _iso(start_date)
    if start_date and not start_iso:
        raise PostingEvidenceError(
            "the posting start date is not a real date; Ellis stores a date it "
            "can read or none at all")
    end_iso = _iso(end_date)
    if end_date and not end_iso:
        raise PostingEvidenceError(
            "the posting end date is not a real date; Ellis stores a date it "
            "can read or none at all")
    filed_iso = _iso(lca_filed_date)
    if lca_filed_date and not filed_iso:
        raise PostingEvidenceError(
            "the LCA filing date is not a real date; Ellis stores a date it "
            "can read or none at all")
    if start_iso and end_iso and end_iso < start_iso:
        raise PostingEvidenceError(
            "the posting end date is before the posting start date")
    # The date the window WOULD close, if the notice stays up. It is Ellis's
    # arithmetic, kept under its own key and never merged into `end_date`: an
    # end date the employer did not give must never sit inside a record stamped
    # `attested_by_employer`, and it must never answer the 10-day question.
    window = posting_window(start_iso) if start_iso else {"valid": False}
    planned_end = window["end"] if window.get("valid") else ""

    places = [str(loc).strip() for loc in (locations or []) if str(loc).strip()]
    payload = {
        "method": method,
        "start_date": start_iso, "end_date": end_iso,
        "end_date_attested": bool(end_iso),
        "planned_end_date": planned_end,
        "locations": places,
        "electronic_method": str(electronic_method or "").strip(),
        "electronic_evidence": str(electronic_evidence or "").strip(),
        "individual_direct_email": bool(individual_direct_email),
        "lca_filed_date": filed_iso,
        "recorded_at": _now(), "recorded_by": actor,
        "attested_by_employer": True,
    }
    payload["compliance"] = posting_compliance(payload)
    record = _write_record(db, parent, "posting", payload)
    audit.record(db, org_id=parent.org_id, application_id=parent.id,
                 action="h1b_paf_posting_recorded",
                 detail={"method": method, "start_date": start_iso,
                         "end_date": end_iso,
                         "end_date_attested": bool(end_iso),
                         "location_count": len(places),
                         "compliant": payload["compliance"]["compliant"]},
                 actor=actor)
    return record["posting"]


def posting_compliance(posting: Mapping, *,
                       today: _dt.date | None = None) -> dict:
    """Every 655.734 test Ellis can actually apply to a recorded posting, each
    answered True / False / None (None = the facts needed are not recorded)."""
    method = posting.get("method") or ""
    checks: list[dict] = []

    progress = posting_progress(posting.get("start_date") or "",
                                posting.get("end_date") or "", today=today)
    checks.append({
        "check": "ten_days",
        "citation": f"{NOTICE_CITATION}(a)(1)(ii)",
        # None when the employer has attested to no end date: unknown is the
        # honest answer, and it is neither a pass nor a violation.
        "passed": progress.get("meets_ten_days"),
        "detail": (progress.get("reason")
                   or f"attested window covers "
                      f"{progress.get('consecutive_days')} consecutive days "
                      f"(10 required)")})

    if method == "hard_copy":
        places = list(posting.get("locations") or [])
        checks.append({
            "check": "two_conspicuous_locations",
            "citation": f"{NOTICE_CITATION}(a)(1)(ii)(A)",
            "passed": len(places) >= 2,
            "detail": (f"{len(places)} posting location(s) recorded; the rule "
                       f"requires at least two conspicuous locations at each "
                       f"place of employment")})
    elif method == "electronic":
        evidence = str(posting.get("electronic_evidence") or "").strip()
        checks.append({
            "check": "electronic_notice_evidence",
            "citation": f"{NOTICE_CITATION}(a)(1)(ii)(B)",
            "passed": bool(evidence),
            "detail": ("evidence of the electronic notice to affected workers "
                       "is recorded" if evidence else
                       "no evidence of the electronic notice is recorded")})
        if posting.get("individual_direct_email"):
            checks.append({
                "check": "direct_email_once",
                "citation": f"{NOTICE_CITATION}(a)(1)(ii)(B)",
                "passed": True,
                "detail": ("individual direct notice by e-mail need only be "
                           "given once; the 10-day availability check does not "
                           "apply")})

    timing = notice_timing(posting.get("start_date") or "",
                           posting.get("lca_filed_date") or "")
    checks.append({"check": "within_30_days_before_filing",
                   "citation": f"{NOTICE_CITATION}(a)(1)(ii)",
                   "passed": timing.get("compliant"),
                   "detail": timing.get("reason")})

    # A direct-e-mail notice is excused from the 10-day check by the ETA-9035CP
    # instructions; nothing else is.
    if method == "electronic" and posting.get("individual_direct_email"):
        for check in checks:
            if check["check"] == "ten_days":
                check["passed"] = True
                check["detail"] = ("individual direct notice by e-mail need "
                                   "only be given once")
    failed = [c["check"] for c in checks if c["passed"] is False]
    unknown = [c["check"] for c in checks if c["passed"] is None]
    # Tri-state, on purpose. False means a test Ellis could apply FAILED.
    # None means a test could not be applied for want of a recorded fact —
    # saying True there would claim compliance nobody attested to, and saying
    # False would accuse an employer of a breach Ellis cannot see.
    compliant = False if failed else (None if unknown else True)
    return {"compliant": compliant,
            "failed": failed, "unknown": unknown, "checks": checks,
            "progress": progress, "timing": timing,
            "attested_not_verified": True}


# ---------------------------------------------------------------------------
# (c) The manifest.
# ---------------------------------------------------------------------------

def _case_documents(db, parent: models.VisaApplication) -> dict:
    """doc_type -> [{document_id, name}] for the party evidence on this case.
    Ellis's own prepared artifacts are excluded: they are output, not evidence.
    """
    rows = db.execute(select(models.StoredDocument).where(
        models.StoredDocument.application_id == parent.id)).scalars().all()
    out: dict[str, list[dict]] = {}
    for doc in rows:
        if doc.doc_type == _PREPARED or not doc.doc_type:
            continue
        out.setdefault(doc.doc_type, []).append(
            {"document_id": doc.id, "name": doc.name, "doc_type": doc.doc_type})
    return out


def _condition_state(item: Mapping, facts: Mapping) -> tuple[str, str]:
    """('applicable' | 'not_applicable' | 'unknown', reason). An unanswered
    condition is UNKNOWN — never silently 'no'. A file that is quietly missing
    an item it needed is the failure mode this whole module exists to avoid."""
    if item.get("applies") != "conditional":
        return "applicable", ""
    key = item.get("condition") or ""
    if key == "_dependent_or_willful":
        dependent = facts.get("h1b_dependent_employer")
        willful = facts.get("willful_violator")
        if dependent is None and willful is None:
            return "unknown", ("this employer has not stated whether it is "
                               "H-1B-dependent or a willful violator "
                               "(ETA-9035 Section F)")
        if bool(dependent) or bool(willful):
            return "applicable", ("this employer is H-1B-dependent or a "
                                  "willful violator")
        return "not_applicable", ("this employer is neither H-1B-dependent nor "
                                  "a willful violator")
    value = facts.get(key)
    if value is None or value == "":
        return "unknown", (item.get("condition_question")
                           or f"'{key}' has not been answered")
    if isinstance(value, str):
        truthy = value.strip().lower() in ("yes", "true", "y", "1")
    else:
        truthy = bool(value)
    return ("applicable" if truthy else "not_applicable"), ""


def _notice_item_status(record: Mapping) -> tuple[str, list[dict], str]:
    """(status, evidence, next_action) for 655.760(a)(5). Present needs BOTH
    the notice artifact and the employer's record of when and where it was
    given — the regulation asks for the notice AND its dates/locations."""
    notice = dict(record.get("notice") or {})
    posting = dict(record.get("posting") or {})
    evidence, gaps = [], []
    if notice.get("document_id"):
        evidence.append({"document_id": notice["document_id"],
                         "name": "h1b-lca-posting-notice.pdf",
                         "doc_type": NOTICE_ARTIFACT_KIND,
                         "generated_at": notice.get("generated_at", "")})
        if not notice.get("ready_to_post", False):
            gaps.append("the generated notice is still missing required facts")
    else:
        gaps.append("no posting notice has been generated")
    if posting.get("method"):
        detail = {"method": posting["method"],
                  "start_date": posting.get("start_date", ""),
                  "end_date": posting.get("end_date", ""),
                  "locations": list(posting.get("locations") or []),
                  "attested_by_employer": True}
        if posting.get("electronic_evidence"):
            detail["electronic_evidence"] = posting["electronic_evidence"]
        evidence.append(detail)
        # Recomputed, never read back from the snapshot stored at record time:
        # the manifest must answer for the case as it stands today.
        compliance = posting_compliance(posting)
        if compliance.get("failed"):
            gaps.append("the recorded posting does not yet meet "
                        "20 CFR 655.734: "
                        + ", ".join(compliance["failed"]))
        if compliance.get("unknown"):
            # Not an accusation: these are the tests Ellis could not apply
            # because a fact is not recorded yet.
            gaps.append("20 CFR 655.734 cannot be confirmed until the employer "
                        "records: " + ", ".join(compliance["unknown"]))
    else:
        gaps.append("no posting or electronic-notice record exists")
    if not evidence:
        return "missing", [], "; ".join(gaps)
    if gaps:
        return "partial", evidence, "; ".join(gaps)
    return "present", evidence, ""


def assemble_paf(db, parent: models.VisaApplication) -> dict:
    """The ordered PAF manifest: every 655.760(a) item, its citation, and an
    honest status. Nothing is assumed present; nothing conditional is assumed
    inapplicable."""
    facts = notice_facts(db, parent)
    record = paf_record(db, parent)
    by_type = _case_documents(db, parent)

    items: list[dict] = []
    for spec in PAF_CONTENTS:
        state, reason = _condition_state(spec, facts)
        entry = {
            "item_id": spec["item_id"],
            "citation": spec["citation"],
            "title": spec["title"],
            "description": spec["description"],
            "applies": spec["applies"],
            "satisfied_by": list(spec["satisfied_by"]),
        }
        if spec["applies"] == "conditional":
            entry["condition_question"] = spec.get("condition_question", "")
        if state == "not_applicable":
            entry.update({"status": "not_applicable", "evidence": [],
                          "next_action": "", "reason": reason})
            items.append(entry)
            continue
        if spec["item_id"] == "notice_documentation":
            status, evidence, next_action = _notice_item_status(record)
        else:
            evidence = [e for t in spec["satisfied_by"] for e in by_type.get(t, [])]
            held = [k for k in spec["facts"] if _first(facts, k)]
            if evidence:
                status, next_action = "present", ""
            elif spec["facts"] and len(held) == len(spec["facts"]):
                status = "partial"
                next_action = ("Ellis holds the facts for this item; the file "
                               "still needs the document itself (" +
                               ", ".join(spec["satisfied_by"]) + ")")
            else:
                status = "missing"
                next_action = ("add a document of type " +
                               " or ".join(spec["satisfied_by"])
                               if spec["satisfied_by"]
                               else "record this item on the case")
        if state == "unknown":
            # The item may or may not be required; say so instead of choosing.
            status = "unknown" if status == "missing" else status
            next_action = (reason + (f"; {next_action}" if next_action else ""))
        entry.update({"status": status, "evidence": evidence,
                      "next_action": next_action})
        if state == "unknown":
            entry["reason"] = reason
        items.append(entry)

    counts = {status: 0 for status in ITEM_STATUSES}
    for entry in items:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    required = [e for e in items if e["status"] != "not_applicable"]

    posting = dict(record.get("posting") or {})
    filed = posting.get("lca_filed_date") or _first(facts, "lca_filed_date")

    return {
        "case_id": parent.id,
        "as_of": AS_OF,
        "citation": PAF_CITATION,
        "items": items,
        "counts": counts,
        "required_count": len(required),
        "complete": all(e["status"] == "present" for e in required),
        "availability": availability_deadline(filed),
        "retention": retention_deadline(
            employment_end=_first(facts, "employment_end_date"),
            lca_expiry=_first(facts, "lca_expiry_date")),
        "posting": (posting_compliance(posting) if posting.get("method")
                    else {"compliant": None,
                          "reason": "no posting or electronic-notice record "
                                    "exists for this case",
                          "attested_not_verified": True}),
        "posting_record": posting,
        "notice": dict(record.get("notice") or {}),
        "posting_requirements": posting_requirements(),
        "location_rule": ("the file is kept at the employer's principal place "
                          "of business in the U.S. or at the place of "
                          "employment (20 CFR 655.760(a))"),
        "sources": list(SOURCES),
    }


def _manifest_lines(parent: models.VisaApplication, manifest: Mapping,
                    facts: Mapping) -> list[str]:
    lines = ["H-1B PUBLIC ACCESS FILE - MANIFEST", ""]
    lines.append(f"Case: {parent.id}   Generated: "
                 f"{dates.to_display(dates.to_iso(_dt.date.today()))}")
    employer = _first(facts, "employer_legal_name")
    lines.append(f"Employer: {employer or '[not recorded]'}")
    worksite = worksite_address(facts)
    lines.append(f"Worksite: {worksite or '[not recorded]'}")
    lines.append(f"Authority: {PAF_CITATION} (contents, availability, "
                 f"retention); {NOTICE_CITATION} (notice)")
    lines.append(f"Citations verified: {manifest['as_of']}")
    lines.append("")
    lines += consular_forms._wrap(tr("paf.nothing_filed", "en"), 92)
    lines.append("")
    lines += consular_forms._wrap(manifest["location_rule"].upper(), 92)
    lines.append("")

    lines.append("REQUIRED CONTENTS")
    lines.append("")
    for i, item in enumerate(manifest["items"], start=1):
        lines.append(f"{i:>2}. [{item['status'].upper()}] {item['title']}")
        lines.append(f"    {item['citation']}")
        for ev in item["evidence"]:
            if ev.get("name"):
                lines.append(f"    - {ev['name']} ({ev.get('doc_type', '')})")
            else:
                where = ", ".join(ev.get("locations") or []) or ev.get(
                    "electronic_evidence", "")
                # "to <end>" only when the employer actually attested to an end
                # date; otherwise the line says the notice is still up.
                span = (f"{ev.get('start_date', '')} to {ev['end_date']}"
                        if ev.get("end_date") else
                        f"{ev.get('start_date', '')} (no removal date recorded)")
                lines += consular_forms._wrap(
                    f"    - {ev.get('method', 'notice')}: {span}"
                    + (f"; {where}" if where else ""), 92)
        if item.get("next_action"):
            lines += consular_forms._wrap(f"    NEEDED: {item['next_action']}", 92)
        if item.get("reason") and item["status"] == "not_applicable":
            lines += consular_forms._wrap(f"    N/A: {item['reason']}", 92)
        lines.append("")

    lines.append("DEADLINES")
    availability = manifest["availability"]
    if availability.get("known"):
        lines.append(f"  Public availability (1 working day after filing): "
                     f"{availability['deadline_display']}")
    else:
        lines += consular_forms._wrap(
            f"  Public availability: {availability.get('reason', '')}", 92)
    lines += consular_forms._wrap("  " + tr("paf.availability", "en"), 92)
    retention = manifest["retention"]
    if retention.get("known"):
        lines.append(f"  Keep the file until: {retention['keep_until_display']}")
    else:
        lines += consular_forms._wrap(
            f"  Retention: {retention.get('reason', '')}", 92)
    lines += consular_forms._wrap("  " + tr("paf.retention", "en"), 92)
    lines.append("")

    lines.append("NOTICE (20 CFR 655.734)")
    posting = manifest["posting"]
    if posting.get("compliant") is None and posting.get("reason"):
        lines += consular_forms._wrap(f"  {posting['reason']}", 92)
    for check in posting.get("checks", []):
        mark = {True: "PASS", False: "FAIL"}.get(check["passed"], "UNKNOWN")
        lines += consular_forms._wrap(
            f"  [{mark}] {check['check']} ({check['citation']}): "
            f"{check.get('detail', '')}", 92)
    lines += consular_forms._wrap("  " + tr("paf.attested_not_verified", "en"), 92)
    lines.append("")
    lines += consular_forms._wrap("DISCLAIMER: " + _disclaimer_en(), 92)
    lines.append("")
    lines.append("SOURCES")
    for src in manifest["sources"]:
        lines.append(f"  {src}")
    return lines


def _disclaimer_en() -> str:
    from .disclaimer import ATTORNEY_DISCLAIMER
    return ATTORNEY_DISCLAIMER["en"]


def build_paf_package(db, parent: models.VisaApplication, *,
                      actor: str = "") -> dict:
    """The assembled PAF PDF: the manifest cover (every item, its citation and
    its honest status, the two deadlines, the notice compliance read) followed
    by the posting notice Ellis generated, when one exists.

    Party evidence is INDEXED, not merged: those documents are the employer's
    own files in the employer's own cabinet, and several of them are images.
    The cover says which ones are still needed and where they belong.
    """
    facts = notice_facts(db, parent)
    manifest = assemble_paf(db, parent)
    cover = _text_pdf_pages(_manifest_lines(parent, manifest, facts),
                            title="H-1B Public Access File Manifest")

    parts = [cover]
    notice_doc_id = (manifest.get("notice") or {}).get("document_id") or ""
    if notice_doc_id:
        blob = db.execute(select(models.DocumentBlob).where(
            models.DocumentBlob.document_id == notice_doc_id)).scalars().first()
        if blob is not None and blob.content:
            parts.append(bytes(blob.content))

    if len(parts) == 1:
        package = parts[0]
    else:
        from pypdf import PdfReader, PdfWriter
        writer = PdfWriter()
        for part in parts:
            writer.append(PdfReader(BytesIO(part)))
        buf = BytesIO()
        writer.write(buf)
        package = buf.getvalue()

    doc = h1b_forms.store_prepared_pdf(
        db, parent, name="h1b-public-access-file.pdf", pdf=package,
        detail={"kind": PACKAGE_ARTIFACT_KIND, "citation": PAF_CITATION,
                "complete": manifest["complete"],
                "included_notice": bool(notice_doc_id)})
    _write_record(db, parent, "package", {
        "document_id": doc.id, "sha256": doc.sha256,
        "generated_at": _now(), "generated_by": actor,
        "complete": manifest["complete"]})
    audit.record(db, org_id=parent.org_id, application_id=parent.id,
                 action="h1b_paf_package_built",
                 detail={"document_id": doc.id,
                         "complete": manifest["complete"],
                         "counts": manifest["counts"]},
                 actor=actor)
    return {"document_id": doc.id, "sha256": doc.sha256,
            "manifest": manifest, "included_notice": bool(notice_doc_id),
            **h1b_forms.mint_download_url(doc.id)}
