"""RFE response assembly: the issues, the exhibits that cure them, and a DRAFT
narrative an attorney reviews.

An RFE ("Request for Evidence") names one or more grounds; the response is a
packet that answers each ground with evidence. Ellis assembles the packet and
indexes it; a human reviews, signs and files it.

Doctrine, enforced here rather than trusted to callers:

 - THE ISSUES ARE CLASSIFIED, NEVER INVENTED. `parse_rfe_text` matches the
   RFE's own wording against docs/H1B_RFE_TAXONOMY.md phrases, deterministically
   — no model reads the notice. Text that matches nothing becomes an explicit
   `unclassified` finding for a human to key, never a guessed ground.
 - THE EXHIBIT INDEX IS HONEST. Each issue lists the curing evidence the
   taxonomy names, the exhibits ACTUALLY on file that answer it (via
   counsel.evidence_index), and the gaps. A gap is always shown; the packet
   never implies a document that does not exist.
 - THE NARRATIVE IS A DRAFT. Kimi K3 drafts it when configured, under the same
   grounded-or-dropped discipline as counsel.draft_narrative (facts-only
   prompt + deterministic number grounding, falling back to a local template).
   It is labeled DRAFT for attorney review and lists every missing fact.
 - NO DATE IS EVER COMPUTED. The response deadline is the one PRINTED ON THE
   NOTICE. If the operator has not entered it, Ellis says it does not know it.
   A guessed RFE deadline is a missed filing.
 - THE PREMIUM-PROCESSING CLOCK IS EXPLAINED, NEVER ACTED ON. Ellis does not
   file the response, does not restart a clock, and does not pay a fee.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import textwrap
from functools import lru_cache

from ..dates import normalize_any, to_display
from ..providers import kimi as _kimi
from ..providers import pdfgen
from . import counsel
from .disclaimer import disclaimer

TAXONOMY_AS_OF = counsel.TAXONOMY_AS_OF
TAXONOMY_DOC = counsel.TAXONOMY_DOC
DRAFT_LABEL = counsel.DRAFT_LABEL

RFE_STRINGS = {
    "packet.title": {
        "en": "H-1B RFE RESPONSE PACKET (DRAFT)",
        "zh-CN": "H-1B 补件回复文件包（草稿）",
        "zh-Hant": "H-1B 補件回覆文件包（草稿）"},
    "issue.on_file": {"en": "On file", "zh-CN": "已在案", "zh-Hant": "已在案"},
    "issue.gap": {"en": "Not on file", "zh-CN": "尚未提供", "zh-Hant": "尚未提供"},
    "deadline.unknown": {
        "en": "Unknown — read the response deadline printed on your RFE "
              "notice. Ellis never calculates it.",
        "zh-CN": "未知——请以补件通知书上印制的回复期限为准。Ellis 绝不自行推算。",
        "zh-Hant": "未知——請以補件通知書上印製的回覆期限為準。Ellis 絕不自行推算。"},
}


def rfe_string(key: str, locale: str = "en") -> str:
    entry = RFE_STRINGS.get(key) or {}
    return entry.get(locale) or entry.get("en") or key


def _today() -> _dt.date:  # seam so tests are deterministic
    return _dt.date.today()


# ---------------------------------------------------------------------------
# Curated premium-processing facts. Explained; never acted on.
# ---------------------------------------------------------------------------

PREMIUM_PROCESSING = {
    "as_of": "2026-08-09",
    "source": ("https://www.uscis.gov/forms/all-forms/"
               "how-do-i-request-premium-processing"),
    "explanation": {
        "en": ("Premium processing does not run through an RFE. USCIS's "
               "business-day clock STOPS on the day the RFE is issued, and a "
               "NEW full period begins on the day USCIS receives the complete "
               "response — it does not resume where it left off. Filing a "
               "partial response to 'start the clock' restarts it against an "
               "incomplete record. Ellis does not file this response, does not "
               "restart any clock, and does not pay the premium-processing "
               "fee; the petitioner or their attorney does all three."),
        "zh-CN": ("加急处理在补件期间不计时。USCIS 的工作日计时在补件通知签发当日"
                  "停止，并在收到完整回复之日重新开始一个完整周期，而不是接续之前"
                  "的天数。为了“重新计时”而提交不完整的回复，只会让计时基于不完整"
                  "的材料重新开始。Ellis 不提交本回复、不重启任何计时、也不支付加急"
                  "费用；这三件事都由雇主或其律师完成。"),
        "zh-Hant": ("加急處理在補件期間不計時。USCIS 的工作日計時在補件通知簽發當日"
                    "停止，並在收到完整回覆之日重新開始一個完整週期，而不是接續之前"
                    "的天數。為了「重新計時」而提交不完整的回覆，只會讓計時基於不完"
                    "整的材料重新開始。Ellis 不提交本回覆、不重啟任何計時、也不支付"
                    "加急費用；這三件事都由雇主或其律師完成。"),
    },
}

DEADLINE_WARNING = {
    "en": ("USCIS sets the response deadline on the notice itself and it is "
           "not extendable. A response received after it is decided on the "
           "record as it stands, which usually means denial. Ellis reads the "
           "date only if you enter it from the notice."),
    "zh-CN": ("回复期限由 USCIS 在补件通知上载明，且不可延长。逾期送达的回复不会被"
              "考虑，案件将按现有材料裁决，通常意味着拒批。只有当您从通知上录入该"
              "日期时，Ellis 才会引用它。"),
    "zh-Hant": ("回覆期限由 USCIS 在補件通知上載明，且不可延長。逾期送達的回覆不會被"
                "考慮，案件將按現有材料裁決，通常意味著拒批。只有當您從通知上錄入該"
                "日期時，Ellis 才會引用它。"),
}


# ---------------------------------------------------------------------------
# Issue catalog — docs/H1B_RFE_TAXONOMY.md as data.
#
# `match_phrases` are USCIS's own recurring wording (English by design: the
# notice is English). `curing_evidence` items name the doc_types / checklist
# item ids that would satisfy them, so the assembler can say honestly which
# exhibits already answer the ground.
# ---------------------------------------------------------------------------

def _ev(label: str, *, doc_types: tuple[str, ...] = (),
        item_ids: tuple[str, ...] = ()) -> dict:
    return {"label": label, "doc_types": list(doc_types),
            "item_ids": list(item_ids)}


ISSUES: tuple[dict, ...] = (
    {
        "key": "specialty_occupation",
        "title": {"en": "Specialty occupation not established",
                  "zh-CN": "未能证明属于专业职位",
                  "zh-Hant": "未能證明屬於專業職位"},
        "taxonomy_section": "§1",
        "citations": ("8 CFR 214.2(h)(4)(iii)(A)",),
        "uscis_wording": ("The evidence does not establish that the proffered "
                          "position qualifies as a specialty occupation."),
        # Deliberately NOT the bare phrase "specialty occupation": it appears
        # in almost every H-1B RFE sentence, including the beneficiary-
        # qualifications ground, and would classify every notice as this one.
        "match_phrases": (
            "qualifies as a specialty occupation",
            "proffered position qualifies",
            "214.2(h)(4)(iii)(a)",
            "directly related specific specialty",
            "bachelor's or higher degree in a specific specialty",
        ),
        "required_facts": ("job_title", "job_duties", "soc_code",
                           "wage_level", "degree_field"),
        "curing_evidence": (
            _ev("Duty-by-duty breakdown with percentage time allocations "
                "mapped to specific degree coursework",
                doc_types=("job_description",), item_ids=("job_description",)),
            _ev("Employer support letter tying each duty to the degree field",
                doc_types=("employer_support_letter",),
                item_ids=("support_letter",)),
            _ev("Occupational Outlook Handbook excerpt for the SOC code",
                doc_types=("ooh_excerpt",)),
            _ev("Internal job postings and degree requirements for the same "
                "role", doc_types=("job_posting",)),
            _ev("Comparable postings from similar-size, same-industry "
                "employers requiring identical degrees",
                doc_types=("comparable_job_postings",)),
            _ev("Organizational chart showing where the role sits",
                doc_types=("org_chart",)),
            _ev("Expert opinion letter (professor or industry) tying each duty "
                "to the degree field", doc_types=("expert_opinion_letter",)),
        ),
    },
    {
        "key": "beneficiary_qualifications",
        "title": {"en": "Beneficiary's qualifications not established",
                  "zh-CN": "未能证明受益人具备资格",
                  "zh-Hant": "未能證明受益人具備資格"},
        "taxonomy_section": "§2",
        "citations": ("8 CFR 214.2(h)(4)(iii)(C)", "8 CFR 214.2(h)(4)(iii)(D)"),
        "uscis_wording": ("The evidence does not establish that the beneficiary "
                          "is qualified to perform services in a specialty "
                          "occupation."),
        "match_phrases": (
            "qualified to perform services in a specialty occupation",
            "beneficiary is qualified",
            "214.2(h)(4)(iii)(c)",
            "equivalent to a united states bachelor",
            "credential evaluation",
            "foreign degree equivalency",
        ),
        "required_facts": ("beneficiary_full_name", "degree_field"),
        "curing_evidence": (
            _ev("NACES-member credential evaluation stating US bachelor's "
                "equivalency in the specific field",
                doc_types=("credential_evaluation",),
                item_ids=("credential_evaluation",)),
            _ev("Degree certificate 学位证 with certified translation",
                doc_types=("degree_certificate",),
                item_ids=("degree_certificate",)),
            _ev("Graduation certificate 毕业证 with certified translation",
                doc_types=("graduation_certificate",),
                item_ids=("graduation_certificate",)),
            _ev("Academic transcripts showing coursework in the required field",
                doc_types=("transcript",), item_ids=("transcript",)),
            _ev("CHESICC (学信网) / CDGDC verification reports for Chinese "
                "credentials", doc_types=("credential_verification",)),
            _ev("Experience letters showing progressive responsibility "
                "(three-for-one equivalency)",
                doc_types=("experience_letter", "resume_cv"),
                item_ids=("resume",)),
        ),
    },
    {
        "key": "bona_fide_job_offer",
        "title": {"en": "Bona fide position / third-party placement",
                  "zh-CN": "真实职位与第三方派驻",
                  "zh-Hant": "真實職位與第三方派駐"},
        "taxonomy_section": "§3",
        "citations": ("8 CFR 214.2(h)(4)(ii) (United States employer)",),
        "uscis_wording": ("The evidence does not establish that a bona fide "
                          "position in a specialty occupation is available as "
                          "of the requested start date."),
        "match_phrases": (
            "bona fide position",
            "bona fide job offer",
            "as of the requested start date",
            "contracts, work orders, or similar evidence",
            "end-client",
            "end client",
            "third-party",
            "united states employer",
        ),
        "required_facts": ("worksite_location", "employment_start_date",
                           "job_duties"),
        "curing_evidence": (
            _ev("Master services agreement plus statement of work or work "
                "order covering the full validity period",
                doc_types=("client_contract", "statement_of_work")),
            _ev("End-client letter describing duties, required degree, "
                "duration and worksite", doc_types=("end_client_letter",)),
            _ev("Certified LCA listing every worksite",
                doc_types=("certified_lca",), item_ids=("certified_lca",)),
            _ev("Evidence of non-speculative work at filing (project plans, "
                "funded contracts)", doc_types=("project_plan",)),
            _ev("Organizational chart and in-house product documentation",
                doc_types=("org_chart",)),
        ),
    },
    {
        "key": "ability_to_pay",
        "title": {"en": "Employer viability / ability to pay the proffered wage",
                  "zh-CN": "雇主经营实体与支付能力",
                  "zh-Hant": "雇主經營實體與支付能力"},
        "taxonomy_section": "§4",
        "citations": ("20 CFR 655.731 (required wage obligation)",),
        "uscis_wording": ("The evidence does not establish that the petitioner "
                          "has the ability to pay the proffered wage, or that "
                          "the petitioner is a bona fide, operating business."),
        "match_phrases": (
            "ability to pay",
            "proffered wage",
            "bona fide, operating business",
            "bona fide operating business",
            "financial ability",
        ),
        "required_facts": ("employer_legal_name", "wage_offer"),
        "curing_evidence": (
            _ev("Federal tax returns and audited financial statements",
                doc_types=("employer_financials",),
                item_ids=("employer_financials",)),
            _ev("Payroll records (W-3, Forms 941) showing cash wages",
                doc_types=("payroll_records",)),
            _ev("Bank statements", doc_types=("bank_statement",)),
            _ev("FEIN evidence (IRS CP-575 or equivalent)",
                doc_types=("fein_evidence",), item_ids=("fein_evidence",)),
            _ev("Business license, premises lease and client contracts or "
                "funding evidence", doc_types=("business_license", "lease")),
            _ev("Organizational chart with headcount",
                doc_types=("org_chart",)),
        ),
    },
    {
        "key": "maintenance_of_status",
        "title": {"en": "Maintenance of status",
                  "zh-CN": "身份维持",
                  "zh-Hant": "身分維持"},
        "taxonomy_section": "§5",
        "citations": ("8 CFR 214.1(c)(4)",),
        "uscis_wording": ("Submit evidence that the beneficiary has maintained "
                          "valid H-1B status."),
        "match_phrases": (
            "maintained valid h-1b status",
            "maintenance of status",
            "period of stay",
            "pay stubs",
            "extraordinary circumstances beyond the control",
            "214.1(c)(4)",
        ),
        "required_facts": ("beneficiary_full_name", "case_kind"),
        "curing_evidence": (
            _ev("All pay stubs for the current validity period",
                doc_types=("pay_stub",)),
            _ev("W-2 forms for the current validity period",
                doc_types=("w2",)),
            _ev("Most recent I-94 record", doc_types=("i94_record",),
                item_ids=("i94_record",)),
            _ev("Prior I-797 approval notices", doc_types=("prior_i797",),
                item_ids=("prior_i797",)),
            _ev("Leave documentation explaining any pay gap",
                doc_types=("leave_documentation",)),
        ),
    },
    {
        "key": "export_control",
        "title": {"en": "Export control / deemed export (I-129 Part 6)",
                  "zh-CN": "出口管制与视同出口（I-129 第 6 部分）",
                  "zh-Hant": "出口管制與視同出口（I-129 第 6 部分）"},
        "taxonomy_section": "§6",
        "citations": ("15 CFR 770-774 (EAR)", "22 CFR 120-130 (ITAR)"),
        "uscis_wording": ("Part 6 of Form I-129 (export control certification) "
                          "is unanswered or inconsistent with the petition."),
        # Bare "ear"/"itar" are excluded: as three- and four-letter runs they
        # collide with ordinary prose ("Dear petitioner") and would classify a
        # cover letter as an export-control ground.
        "match_phrases": (
            "part 6",
            "export control",
            "deemed export",
            "technology or technical data",
            "export administration regulations",
            "international traffic in arms",
            "15 cfr",
            "22 cfr",
        ),
        "required_facts": (),
        "curing_evidence": (
            _ev("Documented export-control classification (ECCN) review "
                "predating the filing",
                doc_types=("export_control_review",)),
            _ev("Technology control plan, if a license is required",
                doc_types=("technology_control_plan",)),
            _ev("Fundamental-research exclusion memo, for universities",
                doc_types=("fundamental_research_memo",)),
        ),
    },
    {
        "key": "registration_consistency",
        "title": {"en": "Registration consistency (weighted selection era)",
                  "zh-CN": "注册信息一致性（加权抽签时期）",
                  "zh-Hant": "註冊資訊一致性（加權抽籤時期）"},
        "taxonomy_section": "§7",
        "citations": ("8 CFR 214.2(h)(8)(iii) (registration)",),
        "uscis_wording": ("The wage level, SOC code or worksite in the petition "
                          "is inconsistent with the registration, raising a "
                          "process-integrity concern."),
        # Bare "registration" / "wage level" / "soc code" are excluded: they
        # appear in most H-1B notices. Missing a ground costs a human keying
        # it; over-matching would build a packet for a ground USCIS never
        # raised.
        "match_phrases": (
            "process integrity",
            "inconsistent with the registration",
            "differs from the registration",
            "registered wage level",
            "registered soc code",
            "wage level indicated on the registration",
        ),
        "required_facts": ("soc_code", "wage_offer", "worksite_location"),
        "curing_evidence": (
            _ev("Registration confirmation showing the registered wage level, "
                "SOC code and worksite",
                doc_types=("registration_confirmation",)),
            _ev("Certified LCA matching the registered SOC, wage level and "
                "worksite", doc_types=("certified_lca",),
                item_ids=("certified_lca",)),
            _ev("Prevailing wage determination or the OFLC wage-level "
                "computation supporting the level",
                doc_types=("prevailing_wage_determination",)),
        ),
    },
    {
        "key": "fdns_site_visit",
        "title": {"en": "Site visit / employment verification findings",
                  "zh-CN": "实地核查与雇佣核实",
                  "zh-Hant": "實地核查與僱傭核實"},
        "taxonomy_section": "§8",
        "citations": ("8 CFR 214.2(h)(4)(i)(B) (inspections)",),
        "uscis_wording": ("Information obtained during a site visit indicates "
                          "the beneficiary is not employed in the capacity "
                          "specified in the petition."),
        "match_phrases": (
            "site visit",
            "not employed in the capacity specified",
            "inspection or verification",
            "fdns",
        ),
        "required_facts": ("worksite_location", "employer_legal_name"),
        "curing_evidence": (
            _ev("Public access file with the certified LCA and wage records",
                doc_types=("public_access_file", "certified_lca")),
            _ev("Payroll evidence that the wage paid equals the LCA required "
                "wage", doc_types=("payroll_records", "pay_stub")),
            _ev("Consistent duty descriptions across the I-129, LCA, offer "
                "letter and client statement of work",
                doc_types=("employer_support_letter", "job_description"),
                item_ids=("support_letter", "job_description")),
            _ev("Evidence of verifiable premises and web presence",
                doc_types=("premises_evidence",)),
        ),
    },
)

ISSUES_BY_KEY = {i["key"]: i for i in ISSUES}
ISSUE_KEYS = tuple(i["key"] for i in ISSUES)


# ---------------------------------------------------------------------------
# Deterministic classification of the RFE's own text.
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).replace("’", "'").lower()


@lru_cache(maxsize=512)
def _phrase_re(phrase: str) -> re.Pattern:
    """Whole-token match. A plain substring test classified 'Dear petitioner'
    as an export-control ground (it contains 'ear'), so a phrase must be
    bounded by non-alphanumerics on both sides. Written this way rather than
    with \\b so phrases ending in punctuation — '214.2(h)(4)(iii)(a)' — still
    match."""
    return re.compile(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])")


def parse_rfe_text(text: str) -> dict:
    """Classify an RFE notice's text against the taxonomy's phrases.

    Deterministic and conservative: an issue is proposed only when the notice
    actually contains one of USCIS's recurring phrases for it, and the matched
    phrases are returned so a human can check the call. No model reads the
    notice, and nothing is inferred from a ground's absence.

    Returns {issues: [...], unmatched: bool, note}. `unmatched` True means a
    human must key the grounds — never a default ground.
    """
    haystack = _norm(text)
    found: list[dict] = []
    if haystack:
        for issue in ISSUES:
            hits = [p for p in issue["match_phrases"]
                    if _phrase_re(p).search(haystack)]
            if hits:
                found.append({"issue_key": issue["key"],
                              "matched_phrases": hits,
                              "source": "parsed_from_notice"})
    note = ""
    if not haystack:
        note = ("No text was supplied. Enter the RFE's grounds, or upload the "
                "notice so its text can be read.")
    elif not found:
        note = ("Ellis could not match this notice to any ground in "
                f"{TAXONOMY_DOC}. Key the grounds yourself — a guessed ground "
                "would build the wrong packet.")
    return {"issues": found, "unmatched": not found, "note": note,
            "taxonomy_as_of": TAXONOMY_AS_OF, "taxonomy_doc": TAXONOMY_DOC}


def normalize_issues(issues) -> tuple[list[dict], list[str]]:
    """(known issues, unknown keys). Callers may pass plain keys or dicts with
    an optional `officer_wording` quote. An unknown key is REPORTED, never
    silently dropped and never mapped to a nearby ground."""
    known: list[dict] = []
    unknown: list[str] = []
    seen: set[str] = set()
    for raw in issues or []:
        if isinstance(raw, str):
            key, wording = raw, ""
        else:
            key = str((raw or {}).get("issue_key") or (raw or {}).get("key") or "")
            wording = str((raw or {}).get("officer_wording") or "")
        if key not in ISSUES_BY_KEY:
            unknown.append(key)
            continue
        if key in seen:
            continue
        seen.add(key)
        known.append({"issue_key": key, "officer_wording": wording})
    return known, unknown


# ---------------------------------------------------------------------------
# Exhibit mapping.
# ---------------------------------------------------------------------------

def _exhibit_matches(exhibit: dict, evidence: dict) -> bool:
    return (exhibit.get("doc_type") in evidence["doc_types"]
            or exhibit.get("item_id") in evidence["item_ids"])


def map_issue_evidence(issue: dict, exhibits: list[dict]) -> dict:
    """For one issue: which curing-evidence items the case already answers, and
    which are gaps. A missing-status exhibit never counts as on file."""
    on_file = [e for e in exhibits if e.get("status") != "missing"]
    items: list[dict] = []
    for evidence in issue["curing_evidence"]:
        matched = [e for e in on_file if _exhibit_matches(e, evidence)]
        items.append({
            "label": evidence["label"],
            "doc_types": evidence["doc_types"],
            "item_ids": evidence["item_ids"],
            "status": "on_file" if matched else "gap",
            "exhibits": [{"exhibit_no": e["exhibit_no"], "title": e["title"],
                          "document_id": e.get("document_id"),
                          "status": e["status"]} for e in matched],
        })
    return {
        "evidence": items,
        "exhibit_numbers": sorted({x["exhibit_no"] for i in items
                                   for x in i["exhibits"]}),
        "gaps": [i["label"] for i in items if i["status"] == "gap"],
    }


# ---------------------------------------------------------------------------
# The DRAFT narrative.
# ---------------------------------------------------------------------------

NARRATIVE_SYSTEM = """You draft the narrative section of a response to a U.S. \
Citizenship and Immigration Services Request for Evidence on an H-1B petition, \
for review by a licensed immigration attorney.

Rules you must follow, without exception:
- Use ONLY the facts in the provided JSON. Every claim must be traceable to one
  of them.
- Address each issue in `issues` in order, in its own paragraph, referring to
  the exhibits listed for that issue by exhibit number.
- NEVER invent, estimate, or embellish a value, a date, a wage, or a document
  that is not listed. If a fact is missing, do not mention it; the provided
  missing_facts and gaps lists already name the holes.
- Do not state legal conclusions about eligibility, and do not characterize the
  officer's request. State facts and point at exhibits.
- Reply JSON: {"draft_text": "..."} and nothing else."""


def narrative_inputs(db, case, assembled_issues: list[dict]) -> tuple[dict, dict, list[str]]:
    """(facts, sources, missing) for the RFE narrative. The base facts are
    counsel's case-record facts; `missing` adds the per-issue required facts
    each addressed ground needs and the case does not have."""
    facts, sources, missing = counsel.narrative_facts(db, case, "support_letter")
    extra = []
    for entry in assembled_issues:
        for key in ISSUES_BY_KEY[entry["issue_key"]]["required_facts"]:
            if key not in facts and key not in missing and key not in extra:
                extra.append(key)
    return facts, sources, missing + extra


def _local_narrative(facts: dict, assembled: list[dict], missing: list[str],
                     locale: str) -> str:
    """Deterministic template from the SAME inputs the live path gets. A fact
    that is not on file produces no sentence — never a placeholder."""
    lines: list[str] = ["To: U.S. Citizenship and Immigration Services", ""]
    subject = "Re: Response to Request for Evidence — H-1B petition"
    if facts.get("beneficiary_full_name"):
        subject += f" for {facts['beneficiary_full_name']}"
    lines += [subject, ""]

    opener = (f"{facts['employer_legal_name']} submits this response"
              if facts.get("employer_legal_name")
              else "The petitioner submits this response")
    opener += " to the Request for Evidence, addressing each ground below."
    lines += [opener, ""]

    for n, entry in enumerate(assembled, start=1):
        issue = ISSUES_BY_KEY[entry["issue_key"]]
        title = issue["title"].get(locale) or issue["title"]["en"]
        lines += [f"{n}. {title}"]
        if entry.get("officer_wording"):
            lines += _wrap(f"The notice states: \"{entry['officer_wording']}\"",
                           indent="   ")
        exhibits = entry["exhibit_numbers"]
        if exhibits:
            lines += _wrap(
                "The following exhibits respond to this ground: "
                + ", ".join(f"Exhibit {x}" for x in exhibits) + ".",
                indent="   ")
        else:
            lines += _wrap("No exhibit on file responds to this ground yet.",
                           indent="   ")
        for gap in entry["gaps"]:
            lines += _wrap(f"Still to be provided: {gap}", indent="   - ")
        lines += [""]

    lines += ["Every statement above is drawn from the case record; nothing "
              "has been assumed or estimated."]
    if missing:
        lines += ["", "The following facts are not on file and must be "
                      "provided and confirmed before this response is used:"]
        lines += [f"  - {key}" for key in missing]
    return "\n".join(lines)


def draft_response_narrative(db, case, assembled: list[dict], *,
                             locale: str = "en") -> dict:
    """A DRAFT narrative for attorney review. Live Kimi K3 when configured
    (facts-only prompt plus counsel's deterministic number-grounding check);
    the local template otherwise, or whenever the live draft fails the check."""
    facts, sources, missing = narrative_inputs(db, case, assembled)
    payload_issues = [
        {"issue": entry["issue_key"],
         "uscis_wording": ISSUES_BY_KEY[entry["issue_key"]]["uscis_wording"],
         "officer_wording": entry.get("officer_wording") or "",
         "exhibit_numbers": entry["exhibit_numbers"],
         "gaps": entry["gaps"]}
        for entry in assembled]

    body = ""
    engine = "local_template"
    provider = _kimi.get_provider()
    if getattr(provider, "name", "") != "local_test_provider" and hasattr(provider, "_chat"):
        try:
            out = provider._chat(
                NARRATIVE_SYSTEM,
                json.dumps({"facts": facts, "issues": payload_issues,
                            "missing_facts": missing}, ensure_ascii=False),
                max_tokens=3000, temperature=0.2)
            draft = str((out or {}).get("draft_text") or "").strip()
            # Same guard as counsel.draft_narrative: any 3+ digit run in the
            # draft must exist in the facts, so an invented wage or date drops
            # the whole live draft for the deterministic template.
            grounding = dict(facts)
            grounding["_exhibit_numbers"] = [n for e in payload_issues
                                             for n in e["exhibit_numbers"]]
            if draft and counsel._numbers_grounded(draft, grounding):
                body = draft
                engine = getattr(provider, "name", "kimi")
        except Exception:  # noqa: BLE001 — any live failure degrades honestly
            body = ""
    if not body:
        body = _local_narrative(facts, assembled, missing, locale)
        engine = "local_template"

    label = DRAFT_LABEL.get(locale) or DRAFT_LABEL["en"]
    return {"draft_text": f"{label}\n\n{body}\n\n{disclaimer(locale)}",
            "label": label, "label_i18n": dict(DRAFT_LABEL),
            "grounded_facts": facts, "fact_sources": sources,
            "missing_facts": missing, "engine": engine}


# ---------------------------------------------------------------------------
# Assembly.
# ---------------------------------------------------------------------------

def _response_deadline(value) -> dict:
    """The deadline is READ, never computed. An unparseable or absent value is
    honestly unknown."""
    if value in (None, ""):
        return {"known": False, "date": None, "display": None,
                "source": "not entered"}
    iso = normalize_any(value, kind="expiry", us_numeric=True)
    if not iso:
        return {"known": False, "date": None, "display": None,
                "source": f"could not read {str(value)!r} as a date"}
    return {"known": True, "date": iso, "display": to_display(iso),
            "source": "entered from the RFE notice"}


def assemble(db, principal, case, issues, *, locale: str = "en",
             notice_text: str = "", response_due_date=None,
             receipt_number: str = "") -> dict:
    """Assemble the RFE response packet for `case`.

    `issues` are issue keys (or dicts with `officer_wording`); when none are
    given and `notice_text` is supplied, the text is classified deterministically
    and the result reported — an unclassifiable notice yields no issues and says
    so, rather than a default ground.
    """
    parsed = parse_rfe_text(notice_text) if notice_text else None
    if not issues and parsed and parsed["issues"]:
        issues = [{"issue_key": i["issue_key"]} for i in parsed["issues"]]
    known, unknown = normalize_issues(issues)

    exhibits = counsel.evidence_index(db, principal, case)
    assembled: list[dict] = []
    for entry in known:
        issue = ISSUES_BY_KEY[entry["issue_key"]]
        mapping = map_issue_evidence(issue, exhibits)
        assembled.append({
            "issue_key": issue["key"],
            "title": issue["title"].get(locale) or issue["title"]["en"],
            "title_i18n": dict(issue["title"]),
            "taxonomy_section": issue["taxonomy_section"],
            "citations": list(issue["citations"]),
            "uscis_wording": issue["uscis_wording"],
            "officer_wording": entry["officer_wording"],
            **mapping,
        })

    narrative = draft_response_narrative(db, case, assembled, locale=locale)

    warnings: list[str] = []
    if unknown:
        warnings.append(
            "These issue keys are not in Ellis's taxonomy and were not "
            "assembled: " + ", ".join(sorted(set(unknown)))
            + f". Valid keys: {', '.join(ISSUE_KEYS)}.")
    if not assembled:
        warnings.append(
            "No RFE ground was assembled. Ellis builds a packet only for "
            "grounds it was given or could match; it never assumes one.")
    total_gaps = sum(len(a["gaps"]) for a in assembled)
    if total_gaps:
        warnings.append(
            f"{total_gaps} curing-evidence item(s) across the addressed "
            f"grounds are not on file. They are listed per issue and in the "
            f"packet; the response is incomplete until they are provided or "
            f"an attorney decides they are unnecessary.")

    return {
        "case_id": case.id,
        "receipt_number": receipt_number or "",
        "issues": assembled,
        "unknown_issue_keys": sorted(set(unknown)),
        "parsed_from_notice": parsed,
        "exhibits": exhibits,
        "exhibit_counts": {
            "total": len(exhibits),
            "on_file": sum(1 for e in exhibits if e["status"] != "missing"),
            "missing": sum(1 for e in exhibits if e["status"] == "missing")},
        "narrative": narrative,
        "response_deadline": _response_deadline(response_due_date),
        "deadline_warning": DEADLINE_WARNING.get(locale) or DEADLINE_WARNING["en"],
        "deadline_unknown_text": rfe_string("deadline.unknown", locale),
        "premium_processing": {
            "explanation": (PREMIUM_PROCESSING["explanation"].get(locale)
                            or PREMIUM_PROCESSING["explanation"]["en"]),
            "as_of": PREMIUM_PROCESSING["as_of"],
            "source": PREMIUM_PROCESSING["source"],
            "ellis_action": "none — explained only"},
        "warnings": warnings,
        "taxonomy_as_of": TAXONOMY_AS_OF,
        "taxonomy_doc": TAXONOMY_DOC,
        "filed_by": "the petitioner or their attorney — Ellis never files, "
                    "signs, or submits a response",
    }


# ---------------------------------------------------------------------------
# Printable packet (deterministic, locally produced, and it says so).
# ---------------------------------------------------------------------------

def _wrap(text: str, indent: str = "") -> list[str]:
    return textwrap.wrap(text, width=92, initial_indent=indent,
                         subsequent_indent=(" " * len(indent))) or [""]


def _pdf_safe(line: str) -> str:
    return line.encode("latin-1", "ignore").decode("latin-1")


def build_packet_pdf(packet: dict, *, case_kind: str = "") -> bytes:
    """The printable response packet: caption, per-issue exhibit index with
    gaps shown, the DRAFT narrative, the premium-processing explanation, and
    the attorney disclaimer."""
    lines: list[str] = [rfe_string("packet.title", "en"), ""]
    lines += [f"Case: {packet['case_id']}"]
    if case_kind:
        lines += [f"Case kind: {case_kind}"]
    if packet.get("receipt_number"):
        lines += [f"USCIS receipt number: {packet['receipt_number']}"]
    deadline = packet.get("response_deadline") or {}
    lines += [f"Response deadline: "
              f"{deadline.get('display') or 'UNKNOWN — read it from the RFE notice'}"]
    lines += [f"Prepared by Ellis, a non-attorney preparer, on "
              f"{_today().isoformat()}. Nothing here has been filed.", ""]

    lines += ["GROUNDS ADDRESSED"]
    if not packet["issues"]:
        lines += ["  (none — no ground was given or could be matched)"]
    for n, issue in enumerate(packet["issues"], start=1):
        lines += [""]
        lines += _wrap(f"{n}. {issue['title_i18n']['en']} "
                       f"[{issue['taxonomy_section']}]")
        lines += _wrap(f"USCIS wording: {issue['uscis_wording']}", indent="   ")
        if issue.get("officer_wording"):
            lines += _wrap(f"As written on this notice: "
                           f"{issue['officer_wording']}", indent="   ")
        lines += _wrap("Citations: " + "; ".join(issue["citations"]), indent="   ")
        for item in issue["evidence"]:
            mark = "ON FILE" if item["status"] == "on_file" else "NOT ON FILE"
            refs = ", ".join(f"Exhibit {e['exhibit_no']}"
                             for e in item["exhibits"])
            lines += _wrap(f"[{mark}] {item['label']}"
                           + (f" — {refs}" if refs else ""), indent="   - ")

    lines += ["", "EXHIBIT INDEX"]
    on_file = [e for e in packet["exhibits"] if e["status"] != "missing"]
    missing = [e for e in packet["exhibits"] if e["status"] == "missing"]
    if not on_file:
        lines += ["  (no documents on file yet)"]
    for e in on_file:
        lines += _wrap(f"Exhibit {e['exhibit_no']}: {e['title']} "
                       f"[{e['party']}] — {e['status']}", indent="  ")
    lines += ["", "REQUIRED ITEMS NOT YET ON FILE"]
    if missing:
        for e in missing:
            lines += _wrap(f"Exhibit {e['exhibit_no']}: {e['title']} "
                           f"[{e['party']}] — MISSING", indent="  ")
    else:
        lines += ["  None — every required checklist item is on file."]

    lines += ["", "DRAFT NARRATIVE"]
    for line in (packet["narrative"]["draft_text"] or "").split("\n"):
        lines += _wrap(line) if line.strip() else [""]

    missing_facts = packet["narrative"].get("missing_facts") or []
    lines += ["", "FACTS STILL MISSING FROM THE RECORD"]
    if missing_facts:
        for key in missing_facts:
            lines += [f"  - {key}"]
    else:
        lines += ["  None."]

    lines += ["", "PREMIUM PROCESSING"]
    lines += _wrap(PREMIUM_PROCESSING["explanation"]["en"], indent="  ")
    lines += _wrap(f"Source: {PREMIUM_PROCESSING['source']} "
                   f"(as of {PREMIUM_PROCESSING['as_of']})", indent="  ")

    lines += ["", "RESPONSE DEADLINE"]
    lines += _wrap(DEADLINE_WARNING["en"], indent="  ")

    lines += [""]
    lines += _wrap(disclaimer("en"))
    return pdfgen.text_pdf([_pdf_safe(line) for line in lines],
                           title="H-1B RFE Response Packet")
