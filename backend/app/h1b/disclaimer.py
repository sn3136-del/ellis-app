"""The attorney disclaimer, in one place. Every H1B surface references these
keys; the text is never inlined elsewhere. Shown at case creation, before every
signature ceremony, and with any eligibility-adjacent output.

Ellis's posture (owner decision 2026-08-09): full form preparation for both
parties with automated flow up to each personal act, disclosed as a
non-attorney preparer (I-129 Part 8 / LCA Section K). Ellis gives no legal
advice; strategy and eligibility judgments belong to a licensed attorney.
"""

DISCLAIMER_VERSION = "h1b-attorney-disclaimer-2026-08-09"

ATTORNEY_DISCLAIMER = {
    "en": (
        "Ellis is not a law firm and does not give legal advice. Ellis prepares "
        "forms from information you provide and is disclosed as a preparer on "
        "them. H1B petitions carry legal obligations for the employer and the "
        "worker, signed under penalty of perjury. Review your case with a "
        "licensed US immigration attorney before signing or filing anything."
    ),
    "zh-CN": (
        "Ellis 不是律师事务所，也不提供法律意见。Ellis 根据您提供的信息填写表格，"
        "并在表格中如实署名为填表人。H1B 申请对雇主和员工都具有法律约束力，"
        "签署时须承担伪证处罚责任。在签署或提交任何文件之前，请先咨询美国持牌移民律师。"
    ),
    "zh-Hant": (
        "Ellis 不是律師事務所，也不提供法律意見。Ellis 根據您提供的資訊填寫表格，"
        "並在表格中如實署名為填表人。H1B 申請對雇主和員工都具有法律約束力，"
        "簽署時須承擔偽證處罰責任。在簽署或提交任何文件之前，請先諮詢美國持牌移民律師。"
    ),
}


def disclaimer(locale: str = "en") -> str:
    return ATTORNEY_DISCLAIMER.get(locale) or ATTORNEY_DISCLAIMER["en"]
