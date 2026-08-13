"""H-1B cap-exemption determination as a structured decision tree.

The statute is INA 214(g)(5) (8 U.S.C. 1184(g)(5)); the qualifying-institution
definitions Ellis walks are the ones at 8 CFR 214.2(h)(19)(iii) (the ACWIA-fee
exemption list, which carries the same institution definitions) as amended by
the H-1B Modernization Final Rule effective 2025-01-17, plus the beneficiary
paths at INA 214(g)(5)(C) and 214(g)(7).

Doctrine, enforced here rather than trusted to callers:

- 'unknown' IS A REAL ANSWER, and the common one. A path whose facts are not on
  file is INDETERMINATE, and an indeterminate path means the case-level answer
  is 'unknown' with the missing facts named. Ellis never guesses that an
  employer is a university, and never infers nonprofit status from a name.
- exempt=False ONLY WHEN EVERY PATH IS CONCLUSIVELY NEGATIVE. One unanswered
  path is enough to make the whole determination 'unknown'.
- ADVISORY, NOT LEGAL ADVICE. Cap exemption decides whether a petition may be
  filed year-round outside the lottery — an attorney's call. Every payload
  carries the attorney disclaimer and every path carries its citation and the
  evidence USCIS expects to see.
"""
from __future__ import annotations

from sqlalchemy import select

from . import models as h1b_models

AS_OF = "2026-08-11"
MODERNIZATION_RULE_EFFECTIVE = "2025-01-17"

SOURCES = {
    "ina_214g": ("https://uscode.house.gov/view.xhtml?req=granuleid:"
                 "USC-prelim-title8-section1184"),
    "cfr_214_2": ("https://www.ecfr.gov/current/title-8/chapter-I/"
                  "subchapter-B/part-214/section-214.2"),
    "hea_101a": ("https://uscode.house.gov/view.xhtml?req=granuleid:"
                 "USC-prelim-title20-section1001"),
    "uscis_h1b": ("https://www.uscis.gov/working-in-the-united-states/"
                  "temporary-workers/h-1b-specialty-occupations"),
}

DETERMINATIONS = ("exempt", "not_exempt", "unknown")
PATH_STATUSES = ("met", "not_met", "indeterminate")


# ---------------------------------------------------------------------------
# Fact vocabulary. Entity facts are the PETITIONER's (they live on the
# petitioner party's answers); beneficiary facts are the beneficiary's own and
# live on the parent case. Every one is tri-state: True / False / unknown.
# ---------------------------------------------------------------------------

ENTITY_FACT_KEYS = (
    # Higher Education Act §101(a) elements (20 U.S.C. 1001(a)).
    "ihe_admits_only_hs_graduates",
    "ihe_authorized_to_provide_post_secondary_education",
    "ihe_awards_bachelors_or_two_year_credit",
    "ihe_public_or_nonprofit",
    "ihe_accredited_or_preaccredited",
    # Explicit short-circuit when the petitioner already knows the answer.
    "institution_of_higher_education",
    # Nonprofit status and the affiliation paths.
    "nonprofit_organization",
    "affiliation_owned_or_controlled_by_ihe",
    "affiliation_operated_by_ihe",
    "affiliation_member_branch_cooperative_or_subsidiary_of_ihe",
    "affiliation_written_agreement_with_ihe",
    "fundamental_activity_furthers_ihe_mission",
    # Research organizations.
    "research_is_fundamental_activity",
    "government_entity",
)

BENEFICIARY_FACT_KEYS = (
    "previously_counted_against_cap",
    "cap_number_still_available_to_beneficiary",
    "j1_physician_national_interest_waiver",
    "works_at_qualifying_entity_at_least_half_time",
    "duties_further_essential_purpose_of_qualifying_entity",
)

FACT_KEYS = ENTITY_FACT_KEYS + BENEFICIARY_FACT_KEYS

_TRUE_WORDS = frozenset({"true", "yes", "y", "1"})
_FALSE_WORDS = frozenset({"false", "no", "n", "0"})

# The wizard's question for each fact (en + zh-CN + zh-Hant parity contract).
# Every one is answerable yes / no / "I don't know" — and "I don't know" is a
# first-class answer that keeps the determination honest.
FACT_PROMPTS = {
    "ihe_admits_only_hs_graduates": {
        "en": "Does the petitioner admit only students who hold a high school "
              "diploma or its recognized equivalent?",
        "zh-CN": "雇主是否仅招收持有高中毕业证书或同等学历的学生？",
        "zh-Hant": "雇主是否僅招收持有高中畢業證書或同等學歷的學生？"},
    "ihe_authorized_to_provide_post_secondary_education": {
        "en": "Is the petitioner legally authorized in its state to provide "
              "education beyond secondary school?",
        "zh-CN": "雇主是否依所在州法律获准提供高中以上教育？",
        "zh-Hant": "雇主是否依所在州法律獲准提供高中以上教育？"},
    "ihe_awards_bachelors_or_two_year_credit": {
        "en": "Does the petitioner award a bachelor's degree, or provide a "
              "program of at least two years acceptable toward one?",
        "zh-CN": "雇主是否授予学士学位，或提供至少两年、可计入学士学位的课程？",
        "zh-Hant": "雇主是否授予學士學位，或提供至少兩年、可計入學士學位的課程？"},
    "ihe_public_or_nonprofit": {
        "en": "Is the petitioner a public or other nonprofit institution?",
        "zh-CN": "雇主是否为公立或其他非营利机构？",
        "zh-Hant": "雇主是否為公立或其他非營利機構？"},
    "ihe_accredited_or_preaccredited": {
        "en": "Is the petitioner accredited by a nationally recognized "
              "accrediting agency, or granted preaccreditation status?",
        "zh-CN": "雇主是否获全国认可的认证机构认证，或已取得预认证资格？",
        "zh-Hant": "雇主是否獲全國認可的認證機構認證，或已取得預認證資格？"},
    "institution_of_higher_education": {
        "en": "Is the petitioner an institution of higher education as defined "
              "in section 101(a) of the Higher Education Act (20 U.S.C. "
              "1001(a))?",
        "zh-CN": "雇主是否属于《高等教育法》第101(a)条（20 U.S.C. 1001(a)）定义的"
                 "高等教育机构？",
        "zh-Hant": "雇主是否屬於《高等教育法》第101(a)條（20 U.S.C. 1001(a)）定義的"
                   "高等教育機構？"},
    "nonprofit_organization": {
        "en": "Is the petitioner a nonprofit organization holding IRS "
              "tax-exempt status?",
        "zh-CN": "雇主是否为持有美国国税局免税资格的非营利组织？",
        "zh-Hant": "雇主是否為持有美國國稅局免稅資格的非營利組織？"},
    "affiliation_owned_or_controlled_by_ihe": {
        "en": "Is the petitioner connected to an institution of higher "
              "education through shared ownership or control by the same board "
              "or federation?",
        "zh-CN": "雇主是否与某高等教育机构由同一董事会或联合体共同拥有或控制？",
        "zh-Hant": "雇主是否與某高等教育機構由同一董事會或聯合體共同擁有或控制？"},
    "affiliation_operated_by_ihe": {
        "en": "Is the petitioner operated by an institution of higher "
              "education?",
        "zh-CN": "雇主是否由某高等教育机构运营？",
        "zh-Hant": "雇主是否由某高等教育機構營運？"},
    "affiliation_member_branch_cooperative_or_subsidiary_of_ihe": {
        "en": "Is the petitioner attached to an institution of higher "
              "education as a member, branch, cooperative or subsidiary?",
        "zh-CN": "雇主是否作为成员、分支、合作机构或子机构附属于某高等教育机构？",
        "zh-Hant": "雇主是否作為成員、分支、合作機構或子機構附屬於某高等教育機構？"},
    "affiliation_written_agreement_with_ihe": {
        "en": "Has the petitioner entered a written affiliation agreement with "
              "an institution of higher education establishing an ACTIVE "
              "working relationship for research or education?",
        "zh-CN": "雇主是否与某高等教育机构签订书面附属协议，并就研究或教育建立了"
                 "实际运作的合作关系？",
        "zh-Hant": "雇主是否與某高等教育機構簽訂書面附屬協議，並就研究或教育建立了"
                   "實際運作的合作關係？"},
    "fundamental_activity_furthers_ihe_mission": {
        "en": "Is a fundamental activity of the petitioner to directly "
              "contribute to that institution's research or education mission?",
        "zh-CN": "直接支持该高等教育机构的研究或教育使命，是否属于雇主的基本活动？",
        "zh-Hant": "直接支持該高等教育機構的研究或教育使命，是否屬於雇主的基本活動？"},
    "research_is_fundamental_activity": {
        "en": "Is basic research, applied research, or both a fundamental "
              "activity of the petitioner?",
        "zh-CN": "基础研究、应用研究或两者，是否属于雇主的基本活动？",
        "zh-Hant": "基礎研究、應用研究或兩者，是否屬於雇主的基本活動？"},
    "government_entity": {
        "en": "Is the petitioner a federal, state or local government entity?",
        "zh-CN": "雇主是否为联邦、州或地方政府机构？",
        "zh-Hant": "雇主是否為聯邦、州或地方政府機構？"},
    "previously_counted_against_cap": {
        "en": "Has the beneficiary already been counted against the H-1B "
              "numerical limit?",
        "zh-CN": "受益人此前是否已计入 H-1B 名额限制？",
        "zh-Hant": "受益人此前是否已計入 H-1B 名額限制？"},
    "cap_number_still_available_to_beneficiary": {
        "en": "Does the beneficiary still hold that cap number — six-year "
              "limit not exhausted, and no full year abroad that reset it?",
        "zh-CN": "受益人是否仍保有该名额：六年上限未用尽，且未因在境外连续满一年"
                 "而重置？",
        "zh-Hant": "受益人是否仍保有該名額：六年上限未用盡，且未因在境外連續滿一年"
                   "而重置？"},
    "j1_physician_national_interest_waiver": {
        "en": "Is the beneficiary a physician who received a waiver of the J-1 "
              "two-year foreign-residence requirement?",
        "zh-CN": "受益人是否为已获 J-1 两年回国居住要求豁免的医师？",
        "zh-Hant": "受益人是否為已獲 J-1 兩年回國居住要求豁免的醫師？"},
    "works_at_qualifying_entity_at_least_half_time": {
        "en": "Will the beneficiary spend at least half of their working time "
              "performing duties at a qualifying cap-exempt institution?",
        "zh-CN": "受益人是否将至少一半工作时间在符合豁免条件的机构履行职务？",
        "zh-Hant": "受益人是否將至少一半工作時間在符合豁免條件的機構履行職務？"},
    "duties_further_essential_purpose_of_qualifying_entity": {
        "en": "Do those duties directly and predominately further the "
              "essential purpose or mission of that qualifying institution?",
        "zh-CN": "该等职务是否直接且主要地促进该机构的核心宗旨或使命？",
        "zh-Hant": "該等職務是否直接且主要地促進該機構的核心宗旨或使命？"},
}


def fact_prompt(key: str, locale: str = "en") -> str:
    entry = FACT_PROMPTS.get(key) or {}
    return entry.get(locale) or entry.get("en") or key


def _tri(value) -> tuple[bool | None, bool]:
    """(tri-state, readable). An unrecognized word is UNKNOWN, never False —
    'maybe' is not a No on a determination that decides a filing route."""
    if value is None or value == "":
        return None, True
    if isinstance(value, bool):
        return value, True
    word = str(value).strip().lower()
    if word in _TRUE_WORDS:
        return True, True
    if word in _FALSE_WORDS:
        return False, True
    return None, False


# ---------------------------------------------------------------------------
# Derived facts. A derivation is a transparent AND over recorded elements —
# never an inference from something Ellis merely noticed.
# ---------------------------------------------------------------------------

_HEA_ELEMENTS = (
    "ihe_admits_only_hs_graduates",
    "ihe_authorized_to_provide_post_secondary_education",
    "ihe_awards_bachelors_or_two_year_credit",
    "ihe_public_or_nonprofit",
    "ihe_accredited_or_preaccredited",
)


# A derived group is never itself a wizard question — asking "do you satisfy
# the affiliation group?" would invite a guess. The underlying facts are asked.
_DERIVED_QUESTION_FACTS = {
    "_any_affiliation_relationship": (
        "affiliation_owned_or_controlled_by_ihe",
        "affiliation_operated_by_ihe",
        "affiliation_member_branch_cooperative_or_subsidiary_of_ihe",
        "affiliation_written_agreement_with_ihe"),
}


def _all_of(values: list[bool | None]) -> bool | None:
    if any(v is False for v in values):
        return False
    if all(v is True for v in values):
        return True
    return None


def _any_of(values: list[bool | None]) -> bool | None:
    if any(v is True for v in values):
        return True
    if all(v is False for v in values):
        return False
    return None


def derive_facts(facts: dict) -> tuple[dict, dict]:
    """Resolve derived facts, returning (resolved, provenance). The explicit
    answer always wins; the HEA §101(a) elements derive the IHE fact only when
    it was not answered directly."""
    resolved = dict(facts)
    provenance: dict[str, str] = {k: "recorded" for k, v in facts.items()
                                  if v is not None}
    if resolved.get("institution_of_higher_education") is None:
        derived = _all_of([resolved.get(k) for k in _HEA_ELEMENTS])
        resolved["institution_of_higher_education"] = derived
        if derived is not None:
            provenance["institution_of_higher_education"] = (
                "derived from the Higher Education Act §101(a) elements")
    resolved["_any_affiliation_relationship"] = _any_of([
        resolved.get("affiliation_owned_or_controlled_by_ihe"),
        resolved.get("affiliation_operated_by_ihe"),
        resolved.get("affiliation_member_branch_cooperative_or_subsidiary_of_ihe"),
        resolved.get("affiliation_written_agreement_with_ihe"),
    ])
    return resolved, provenance


# ---------------------------------------------------------------------------
# The decision tree. Each path is a conjunction of tri-state facts.
# ---------------------------------------------------------------------------

PATHS: tuple[dict, ...] = (
    {
        "key": "institution_of_higher_education",
        "kind": "entity",
        "title": {
            "en": "Institution of higher education",
            "zh-CN": "高等教育机构",
            "zh-Hant": "高等教育機構"},
        "requires": ("institution_of_higher_education",),
        "basis": ("The petitioner is an institution of higher education as "
                  "defined in section 101(a) of the Higher Education Act of "
                  "1965 (20 U.S.C. 1001(a))."),
        "citations": ("INA 214(g)(5)(A), 8 U.S.C. 1184(g)(5)(A)",
                      "8 CFR 214.2(h)(19)(iii)(A)",
                      "20 U.S.C. 1001(a) (HEA §101(a))"),
        "evidence_needed": (
            "Accreditation letter from a nationally recognized accrediting "
            "agency (or documented preaccreditation)",
            "State authorization to provide education beyond secondary school",
            "Catalog or charter showing degrees awarded and admission "
            "requirements",
            "IRS determination letter or state charter showing public or "
            "nonprofit status"),
    },
    {
        "key": "affiliated_or_related_nonprofit",
        "kind": "entity",
        "title": {
            "en": "Nonprofit related to or affiliated with an institution of "
                  "higher education",
            "zh-CN": "与高等教育机构关联或附属的非营利机构",
            "zh-Hant": "與高等教育機構關聯或附屬的非營利機構"},
        "requires": ("nonprofit_organization", "_any_affiliation_relationship",
                     "fundamental_activity_furthers_ihe_mission"),
        "basis": ("The petitioner is a nonprofit entity related to or "
                  "affiliated with an institution of higher education, and a "
                  "fundamental activity of the petitioner directly contributes "
                  "to that institution's research or education mission (the "
                  f"standard as amended effective "
                  f"{MODERNIZATION_RULE_EFFECTIVE})."),
        "citations": ("INA 214(g)(5)(A), 8 U.S.C. 1184(g)(5)(A)",
                      "8 CFR 214.2(h)(19)(iii)(B)",
                      "H-1B Modernization Final Rule, effective "
                      f"{MODERNIZATION_RULE_EFFECTIVE}"),
        "evidence_needed": (
            "IRS 501(c)(3) (or (c)(4)/(c)(6)) determination letter",
            "The affiliation instrument: shared-control documents, operating "
            "agreement, or the written affiliation agreement with the "
            "institution",
            "Evidence the affiliation is an ACTIVE working relationship, not "
            "merely a signed agreement",
            "Documentation that a fundamental activity of the nonprofit "
            "directly contributes to the institution's research or education "
            "mission"),
    },
    {
        "key": "nonprofit_research_organization",
        "kind": "entity",
        "title": {
            "en": "Nonprofit research organization",
            "zh-CN": "非营利研究机构",
            "zh-Hant": "非營利研究機構"},
        "requires": ("nonprofit_organization", "research_is_fundamental_activity"),
        "conflicts": ("government_entity",),
        "basis": ("The petitioner is a nonprofit research organization whose "
                  "fundamental activity is basic research, applied research, "
                  "or both (the 'fundamental activity' standard replaced "
                  "'primarily engaged' effective "
                  f"{MODERNIZATION_RULE_EFFECTIVE})."),
        "citations": ("INA 214(g)(5)(B), 8 U.S.C. 1184(g)(5)(B)",
                      "8 CFR 214.2(h)(19)(iii)(C)",
                      "H-1B Modernization Final Rule, effective "
                      f"{MODERNIZATION_RULE_EFFECTIVE}"),
        "evidence_needed": (
            "IRS tax-exempt determination letter",
            "Charter, bylaws or mission statement identifying research as a "
            "fundamental activity",
            "Grant awards, published research, or research budget lines "
            "evidencing the activity in fact"),
    },
    {
        "key": "governmental_research_organization",
        "kind": "entity",
        "title": {
            "en": "Governmental research organization",
            "zh-CN": "政府研究机构",
            "zh-Hant": "政府研究機構"},
        "requires": ("government_entity", "research_is_fundamental_activity"),
        "basis": ("The petitioner is a federal, state or local government "
                  "entity whose fundamental activity is basic research, "
                  "applied research, or both (state and local entities were "
                  f"added effective {MODERNIZATION_RULE_EFFECTIVE})."),
        "citations": ("INA 214(g)(5)(B), 8 U.S.C. 1184(g)(5)(B)",
                      "8 CFR 214.2(h)(19)(iii)(C)",
                      "H-1B Modernization Final Rule, effective "
                      f"{MODERNIZATION_RULE_EFFECTIVE}"),
        "evidence_needed": (
            "Enabling statute, charter or executive order establishing the "
            "entity as a government body",
            "Mission or program documents identifying research as a "
            "fundamental activity"),
    },
    {
        "key": "beneficiary_employed_at_qualifying_entity",
        "kind": "beneficiary",
        "title": {
            "en": "Beneficiary works at a qualifying institution for a "
                  "non-qualifying employer",
            "zh-CN": "受益人受雇于非豁免雇主但在豁免机构工作",
            "zh-Hant": "受益人受僱於非豁免雇主但在豁免機構工作"},
        "requires": ("works_at_qualifying_entity_at_least_half_time",
                     "duties_further_essential_purpose_of_qualifying_entity"),
        "basis": ("The beneficiary is not directly employed by a qualifying "
                  "institution, but spends at least half of their time "
                  "performing job duties at one, and those duties directly and "
                  "predominately further that institution's essential purpose "
                  f"(codified effective {MODERNIZATION_RULE_EFFECTIVE})."),
        "citations": ("INA 214(g)(5)(A)-(B), 8 U.S.C. 1184(g)(5)",
                      "8 CFR 214.2(h)(8)(iii)(F)(4)",
                      "H-1B Modernization Final Rule, effective "
                      f"{MODERNIZATION_RULE_EFFECTIVE}"),
        "evidence_needed": (
            "Written agreement between the petitioner and the qualifying "
            "institution",
            "Letter from the qualifying institution describing the duties "
            "performed on its behalf and the time allocation",
            "The petitioner's own evidence that the qualifying institution "
            "itself meets one of the entity paths above"),
    },
    {
        "key": "beneficiary_previously_counted",
        "kind": "beneficiary",
        "title": {
            "en": "Beneficiary was already counted against the cap",
            "zh-CN": "受益人此前已计入名额",
            "zh-Hant": "受益人此前已計入名額"},
        "requires": ("previously_counted_against_cap",
                     "cap_number_still_available_to_beneficiary"),
        "basis": ("The beneficiary was already counted against the numerical "
                  "limit and has not exhausted their entitlement to that cap "
                  "number, so this petition is not counted again."),
        "citations": ("INA 214(g)(7), 8 U.S.C. 1184(g)(7)",
                      "8 CFR 214.2(h)(8)(iii)(F)"),
        "evidence_needed": (
            "Prior I-797 approval notice(s) showing cap-subject H-1B status",
            "I-94 records and passport stamps covering the prior H-1B period",
            "Evidence the six-year limit has not been exhausted, or that time "
            "spent outside the United States is recapturable"),
    },
    {
        "key": "beneficiary_j1_waiver_physician",
        "kind": "beneficiary",
        "title": {
            "en": "Physician with a J-1 national-interest waiver",
            "zh-CN": "获得 J-1 国家利益豁免的医师",
            "zh-Hant": "獲得 J-1 國家利益豁免的醫師"},
        "requires": ("j1_physician_national_interest_waiver",),
        "basis": ("The beneficiary has received a waiver of the J-1 two-year "
                  "foreign-residence requirement under INA 214(l), which "
                  "exempts the petition from the numerical limit."),
        "citations": ("INA 214(g)(5)(C), 8 U.S.C. 1184(g)(5)(C)",
                      "INA 214(l), 8 U.S.C. 1184(l)"),
        "evidence_needed": (
            "The waiver approval (I-612 approval or Department of State "
            "favorable recommendation with USCIS approval)",
            "The underlying employment agreement in the designated shortage "
            "area"),
    },
)

PATHS_BY_KEY = {p["key"]: p for p in PATHS}


def _evaluate_path(path: dict, resolved: dict) -> dict:
    """One path's status plus exactly which facts are still open."""
    missing: list[str] = []
    failed: list[str] = []
    for key in path["requires"]:
        value = resolved.get(key)
        if value is True:
            continue
        if value is False:
            failed.append(key)
        else:
            missing.append(key)
    for key in path.get("conflicts", ()):
        if resolved.get(key) is True:
            failed.append(key)
    if failed:
        status = "not_met"
    elif missing:
        status = "indeterminate"
    else:
        status = "met"
    return {"key": path["key"], "kind": path["kind"], "status": status,
            "requires": list(path["requires"]),
            "missing_facts": missing, "failed_facts": failed,
            "citations": list(path["citations"]),
            "evidence_needed": list(path["evidence_needed"])}


def determine(facts: dict, *, locale: str = "en") -> dict:
    """Pure decision-tree evaluation.

    Returns {exempt, basis, evidence_needed, citations, ...} where `exempt` is
    True, False, or the string 'unknown'. 'unknown' whenever ANY path is still
    indeterminate and none is met — an absent entity fact is never read as a
    negative.
    """
    tri_facts: dict = {}
    unreadable: list[str] = []
    for key in FACT_KEYS:
        value, readable = _tri(facts.get(key))
        tri_facts[key] = value
        if not readable:
            unreadable.append(key)

    resolved, provenance = derive_facts(tri_facts)
    evaluated = [_evaluate_path(p, resolved) for p in PATHS]

    met = [e for e in evaluated if e["status"] == "met"]
    indeterminate = [e for e in evaluated if e["status"] == "indeterminate"]

    if met:
        exempt: bool | str = True
        primary = PATHS_BY_KEY[met[0]["key"]]
        basis = primary["basis"]
        citations = list(primary["citations"])
        evidence_needed = list(primary["evidence_needed"])
    elif indeterminate:
        exempt = "unknown"
        basis = ("No cap-exemption path can be decided on the facts recorded "
                 "for this case. The facts named in open_questions have not "
                 "been provided, and Ellis does not guess them.")
        citations = ["INA 214(g)(5), 8 U.S.C. 1184(g)(5)",
                     "8 CFR 214.2(h)(19)(iii)"]
        evidence_needed = sorted({
            item for e in indeterminate
            for item in PATHS_BY_KEY[e["key"]]["evidence_needed"]})
    else:
        exempt = False
        basis = ("Every cap-exemption path at INA 214(g)(5) and 8 CFR "
                 "214.2(h)(19)(iii) is conclusively negative on the recorded "
                 "facts, so this petition appears to be cap-subject and would "
                 "run through the annual electronic registration.")
        citations = ["INA 214(g)(5), 8 U.S.C. 1184(g)(5)",
                     "8 CFR 214.2(h)(19)(iii)"]
        evidence_needed = []

    # The wizard's remaining questions: one per still-open fact, deduplicated,
    # each naming the party who can answer it and the paths it would unlock.
    open_questions: list[dict] = []
    seen_facts: dict[str, dict] = {}
    for e in indeterminate:
        asked: list[str] = []
        for key in e["missing_facts"]:
            # A derived group is not a question: ask the facts underneath it.
            asked.extend(_DERIVED_QUESTION_FACTS.get(key, (key,)))
        for key in asked:
            question = seen_facts.get(key)
            if question is None:
                question = {
                    "fact": key,
                    "prompt": fact_prompt(key, locale),
                    "prompt_i18n": dict(FACT_PROMPTS.get(key) or {}),
                    "asked_of": ("petitioner" if key in ENTITY_FACT_KEYS
                                 else "beneficiary"),
                    "unlocks_paths": []}
                seen_facts[key] = question
                open_questions.append(question)
            if e["key"] not in question["unlocks_paths"]:
                question["unlocks_paths"].append(e["key"])

    for e in evaluated:
        path = PATHS_BY_KEY[e["key"]]
        e["title"] = path["title"].get(locale) or path["title"]["en"]
        e["title_i18n"] = dict(path["title"])

    notes: list[str] = []
    if unreadable:
        notes.append(
            "These answers were not a readable yes or no and were treated as "
            "unknown rather than as a No: " + ", ".join(sorted(unreadable)))
    if exempt is True:
        notes.append(
            "A cap-exempt petition may be filed year-round and skips the "
            "electronic registration, but the exemption must be EVIDENCED in "
            "the petition; a claimed exemption without the documents above is "
            "a denial ground.")

    return {
        "exempt": exempt,
        "basis": basis,
        "evidence_needed": evidence_needed,
        "citations": citations,
        "matched_paths": [e["key"] for e in met],
        "paths": evaluated,
        "open_questions": open_questions,
        "facts_considered": {k: resolved.get(k) for k in FACT_KEYS},
        "fact_provenance": provenance,
        "unreadable_facts": sorted(unreadable),
        "notes": notes,
        "as_of": AS_OF,
        "sources": dict(SOURCES),
        "advisory": True,
        "locale": locale,
    }


# ---------------------------------------------------------------------------
# Case binding.
# ---------------------------------------------------------------------------

def case_facts(db, case) -> dict:
    """Harvest the recorded cap-exemption facts, party-correctly: entity facts
    from the PETITIONER's own answers, beneficiary facts from the parent case.
    A key that was never answered is simply absent — which `determine` reads as
    unknown."""
    parent_answers = dict(case.answers or {})
    petitioner = db.execute(select(h1b_models.CaseParty).where(
        h1b_models.CaseParty.application_id == case.id,
        h1b_models.CaseParty.role == "petitioner")).scalars().first()
    petitioner_answers = dict((petitioner.answers or {}) if petitioner else {})

    facts: dict = {}
    for key in ENTITY_FACT_KEYS:
        if key in petitioner_answers:
            facts[key] = petitioner_answers[key]
    for key in BENEFICIARY_FACT_KEYS:
        if key in parent_answers:
            facts[key] = parent_answers[key]
        elif key in petitioner_answers:
            # The petitioner may know the beneficiary's prior-cap history from
            # the prior I-797 they hold; that is a petitioner-recorded fact.
            facts[key] = petitioner_answers[key]
    return facts


def evaluate(db, case, *, extra_facts: dict | None = None,
             locale: str = "en") -> dict:
    """Determination for a case. `extra_facts` are the wizard's answers in this
    request; they override the recorded ones without being persisted here (a
    determination is advisory and never writes a filing fact)."""
    facts = case_facts(db, case)
    if extra_facts:
        facts.update({k: v for k, v in extra_facts.items() if k in FACT_KEYS})
    out = determine(facts, locale=locale)
    out["case_id"] = case.id
    out["case_kind"] = str((case.answers or {}).get("h1b_case_kind") or "")
    return out
